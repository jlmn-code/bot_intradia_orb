# -*- coding: utf-8 -*-
"""
Backtest de Opening Range Breakout (ORB) sobre "stocks in play" (Alpaca, solo datos)

Basado en la idea de Zarattini, Barbon y Aziz (2023), simplificada. NO envía órdenes:
solo descarga datos históricos y simula la estrategia.

Reglas (por sesión)
  1. Universo del día (con datos hasta AYER): precio >= 5 $, volumen medio 14 días >= 1M acciones, ATR(14) >= 0,50 $.
  2. Vela de apertura = primera vela de 5 min (9:30-9:35 NY).
  3. Volumen relativo (RV) = volumen de esa vela / media del volumen de la misma vela en los 14 días anteriores.
  4. "Stocks in play" = las N_TOP (20) con mayor RV y RV >= 1.
  5. Dirección: vela alcista -> solo LARGO; vela bajista -> solo CORTO; doji -> no se opera.
  6. Entrada: orden stop en el máximo (largo) o mínimo (corto) de la vela de apertura.
  7. Stop loss = 10 % del ATR(14) diario desde la entrada. Sin take profit: salida al cierre de la sesión.
  8. Riesgo 1 % del capital por operación, apalancamiento total máximo 4x (aprox.).

Comparación: el mismo universo líquido eligiendo al azar el mismo número de símbolos cada día,
con las mismas reglas de dirección, entrada y stop. Si el filtro de volumen relativo no aporta,
la estrategia no debería superar al azar.

Uso:   python bot_orb.py backtest
Variables de entorno (opcionales):
  BACKTEST_DIAS (250), COSTE_PCT (0.0010 = 0,10 % ida y vuelta, spread + deslizamiento),
  DIRECCION (ambos | largos)
Credenciales: APCA_API_KEY_ID / APCA_API_SECRET_KEY (sirven las de paper).

AVISO: herramienta de aprendizaje. Con stops tan cortos el coste por operación pesa muchísimo:
mira la tabla de sensibilidad al coste antes de sacar conclusiones.
"""

import os
import sys
import csv
import math
import time
import random
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed, Adjustment

# ==========================================================
# CONFIGURACIÓN
# ==========================================================
NY = ZoneInfo("America/New_York")

# Universo candidato (editable). Para ORB conviene un universo amplio con acciones "en movimiento".
POOL = [
    "AAPL",# "MSFT", 
"NVDA", #"AMZN", "GOOGL", "META", 
"TSLA", #"AMD", "AVGO", "NFLX",
    "QQQ",#"MU", "PLTR", "COIN", "MSTR", #"UBER", "SHOP", "CRWD", "PANW", "ARM", #"INTC",
   # "QCOM", "BA", "JPM", "BAC", "C", #"GS", "XOM", "CVX", "OXY", "LLY",
   # "PFE", "MRNA", "SOFI", "HOOD", #"RIVN", #"SNAP", "ROKU", "DKNG", "ABNB", #"PYPL",
  #  "DIS", "NKE", "SBUX", "WMT", "COST", #"HD", "F", "GM", "T", "CSCO",
   # "ORCL", "CRM", "ADBE", "NOW", "SNOW", #"SMCI", "MARA", "RIOT", "AFRM", "UPST",
 #   "RBLX", "TSM", "BABA", "NIO", "SPY", #"QQQ", "IWM", "XLE", "XLF", "ARKK",
]

N_TOP = 3                 # nº de "stocks in play" por día
RV_MIN = 1.0                # volumen relativo mínimo (1.0 = igual que la media)
RV_VENTANA = 14             # días para la media de volumen de la vela de apertura
RV_MIN_HIST = 10            # mínimo de días de historia para calcular RV
PRECIO_MIN = 5.0
VOL_MEDIO_MIN = 1_000_000   # volumen medio diario (14 días) en acciones; solo se aplica con datos SIP
ATR_MIN = 0.50
ATR_P = 14

SL_FRAC_ATR = 0.10          # stop = 10 % del ATR diario
RIESGO_OP = 0.01            # 1 % del capital arriesgado por operación
APALANC_MAX = 4.0           # exposición bruta máxima / capital

BACKTEST_DIAS = int(os.environ.get("BACKTEST_DIAS", "250"))
COSTE_PCT = float(os.environ.get("COSTE_PCT", "0.0010"))   # coste ida y vuelta en % del precio
DIRECCION = os.environ.get("DIRECCION", "ambos").strip().lower()   # ambos | largos
REPS_ALEATORIO = 50
COSTES_SENSIBILIDAD = [0.0, 0.0002, 0.0005, 0.0010]

RETRASO_MIN = 16            # el plan gratis exige >15 min de retraso con SIP
APERTURA_MIN = 9 * 60 + 30  # minutos desde medianoche (NY)
CIERRE_MIN = 16 * 60
CSV_DETALLE = "backtest_orb.csv"

data_client = None
_feed = None                # feed elegido en la primera descarga (SIP o IEX); no se mezclan


# ==========================================================
# UTILIDADES
# ==========================================================
def ahora_ny():
    return datetime.now(NY)


def log(msg):
    print(f"[{ahora_ny():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def secreto(*nombres):
    for n in nombres:
        v = os.environ.get(n)
        if v:
            return v
    try:
        from google.colab import userdata
        for n in nombres:
            try:
                v = userdata.get(n)
                if v:
                    return v
            except Exception:
                pass
    except Exception:
        pass
    return None


def iniciar_clientes():
    global data_client
    key = secreto("API_KEY", "APCA_API_KEY_ID")
    sec = secreto("SECRET_KEY", "APCA_API_SECRET_KEY")
    if not key or not sec:
        log("❌ Faltan credenciales (APCA_API_KEY_ID / APCA_API_SECRET_KEY).")
        return False
    data_client = StockHistoricalDataClient(api_key=key, secret_key=sec)
    return True


# ==========================================================
# DATOS
# ==========================================================
def descargar_barras(simbolos, timeframe, inicio, fin, lote=20):
    """{simbolo: DataFrame} con índice en hora NY. Usa SIP; si falla en la primera descarga, cae a IEX
    y se queda con IEX para no mezclar volúmenes de fuentes distintas."""
    global _feed
    salida = {}
    simbolos = list(simbolos)
    for i in range(0, len(simbolos), lote):
        trozo = simbolos[i:i + lote]
        df = None
        for intento in range(3):
            for feed in ([_feed] if _feed else [DataFeed.SIP, DataFeed.IEX]):
                try:
                    req = StockBarsRequest(symbol_or_symbols=trozo, timeframe=timeframe, start=inicio, end=fin,
                                           feed=feed, adjustment=Adjustment.SPLIT)
                    df = data_client.get_stock_bars(req).df
                    if _feed is None:
                        _feed = feed
                        log(f"ℹ️ Feed de datos: {feed}")
                    break
                except Exception as e:
                    log(f"⚠️ Descarga ({feed}) falló: {str(e)[:120]}")
            if df is not None:
                break
            time.sleep(5 * (intento + 1))
        if df is None or df.empty:
            continue
        for sym, g in df.groupby(level="symbol"):
            g = g.droplevel("symbol").sort_index()
            g.index = g.index.tz_convert(NY)
            salida[sym] = g
        time.sleep(0.5)
    return salida


# ==========================================================
# PREPARACIÓN
# ==========================================================
def true_range(df):
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()],
                     axis=1).max(axis=1)


def preparar_diaria(df):
    """Valores del día ANTERIOR (shift 1) indexados por fecha: así no se usa información del propio día."""
    d = df.copy()
    atr = true_range(d).ewm(alpha=1 / ATR_P, adjust=False).mean()
    vol_med = d["volume"].rolling(14).mean()
    out = pd.DataFrame({"atr_prev": atr.shift(1), "vol_prev": vol_med.shift(1), "close_prev": d["close"].shift(1)})
    out.index = d.index.date
    return out[~out.index.duplicated(keep="last")]


def preparar_5m(df):
    d = df.copy()
    d["minuto"] = d.index.hour * 60 + d.index.minute
    d["fecha"] = d.index.date
    return d[(d["minuto"] >= APERTURA_MIN) & (d["minuto"] < CIERRE_MIN)]


# ==========================================================
# SIMULACIÓN DE UNA OPERACIÓN
# ==========================================================
def simular_trade(op, hi, lo, cl, nivel_hi, nivel_lo, direccion, riesgo):
    """Barras DESPUÉS de la vela de apertura. Devuelve (entrada, salida, tipo) o None si no hay ruptura.
    Si la vela de entrada también toca el stop, se asume stop (criterio conservador).
    Si una vela abre más allá del stop, se sale a la apertura (hueco)."""
    entrada, stop = None, None
    for i in range(len(op)):
        if entrada is None:
            if direccion == 1 and hi[i] >= nivel_hi:
                entrada = max(nivel_hi, op[i])
                stop = entrada - riesgo
            elif direccion == -1 and lo[i] <= nivel_lo:
                entrada = min(nivel_lo, op[i])
                stop = entrada + riesgo
            else:
                continue
            if direccion == 1 and lo[i] <= stop:
                return entrada, stop, "SL"
            if direccion == -1 and hi[i] >= stop:
                return entrada, stop, "SL"
            continue
        if direccion == 1 and lo[i] <= stop:
            return entrada, min(stop, op[i]), "SL"
        if direccion == -1 and hi[i] >= stop:
            return entrada, max(stop, op[i]), "SL"
    if entrada is None:
        return None
    return entrada, float(cl[-1]), "EOD"


def registros_simbolo(sym, d5, dd, vol_min):
    """Una fila por (símbolo, día) que pasa el filtro de universo, con su RV, dirección y operación simulada."""
    regs = []
    grupos = {f: g for f, g in d5.groupby("fecha")}
    primeras = {f: g.iloc[0] for f, g in grupos.items() if g["minuto"].iloc[0] == APERTURA_MIN}
    if len(primeras) < RV_MIN_HIST + 2:
        return regs
    fechas = sorted(primeras)
    vols = pd.Series([float(primeras[f]["volume"]) for f in fechas], index=fechas)
    base = vols.shift(1).rolling(RV_VENTANA, min_periods=RV_MIN_HIST).mean()
    rv = vols / base
    for f in fechas:
        if f not in dd.index or pd.isna(rv[f]) or base[f] <= 0:
            continue
        info = dd.loc[f]
        if (pd.isna(info["atr_prev"]) or pd.isna(info["vol_prev"]) or pd.isna(info["close_prev"])
                or info["close_prev"] < PRECIO_MIN or info["vol_prev"] < vol_min or info["atr_prev"] < ATR_MIN):
            continue
        v = primeras[f]
        o, h, l, c = float(v["open"]), float(v["high"]), float(v["low"]), float(v["close"])
        direccion = 1 if c > o else (-1 if c < o else 0)
        trade = None
        riesgo = SL_FRAC_ATR * float(info["atr_prev"])
        if direccion != 0 and h > l and riesgo > 0:
            g = grupos[f]
            g = g[g["minuto"] >= APERTURA_MIN + 5]
            if len(g) >= 3:
                r = simular_trade(g["open"].values, g["high"].values, g["low"].values, g["close"].values,
                                  h, l, direccion, riesgo)
                if r:
                    entrada, salida, tipo = r
                    trade = {"rb": direccion * (salida - entrada) / riesgo, "k": entrada / riesgo, "tipo": tipo}
        regs.append({"fecha": f, "sym": sym, "rv": float(rv[f]), "dir": direccion, "trade": trade})
    return regs


# ==========================================================
# MÉTRICAS
# ==========================================================
def neto(t, c):
    """R de la operación tras el coste c (ida y vuelta, en % del precio). k = precio / riesgo por acción."""
    return t["rb"] - c * t["k"]


def retorno_dia(trades, c):
    """Retorno diario aproximado: riesgo 1 % por operación, exposición total limitada a APALANC_MAX."""
    if not trades:
        return 0.0
    exposicion = RIESGO_OP * sum(t["k"] for t in trades)
    escala = min(1.0, APALANC_MAX / exposicion) if exposicion > 0 else 1.0
    return RIESGO_OP * escala * sum(neto(t, c) for t in trades)


def curva(retornos):
    eq = np.cumprod(1 + np.array(retornos, float))
    dd = eq / np.maximum.accumulate(eq) - 1
    return float(eq[-1] - 1), float(dd.min())


def stats(rs):
    rs = np.array(rs, float)
    if len(rs) == 0:
        return None
    gan, per = rs[rs > 0].sum(), -rs[rs < 0].sum()
    se = rs.std(ddof=1) / math.sqrt(len(rs)) if len(rs) > 1 else float("nan")
    return {"n": len(rs), "acierto": float(np.mean(rs > 0)), "r": float(rs.mean()), "se": float(se),
            "pf": float(gan / per) if per > 0 else float("inf")}


def linea(nombre, st, ret=None, dd=None, extra=""):
    if st is None:
        return f"{nombre}: sin operaciones"
    s = (f"{nombre}: n={st['n']} | acierto {st['acierto']:.0%} | R medio {st['r']:+.3f} (±{st['se']:.3f}) "
         f"| profit factor {st['pf']:.2f}")
    if ret is not None:
        s += f" | retorno compuesto {ret:+.1%} | caída máx. {dd:.1%}"
    return s + extra


# ==========================================================
# BACKTEST
# ==========================================================
def permitido(r):
    return r["trade"] is not None and (DIRECCION != "largos" or r["dir"] == 1)


def backtest():
    rng = random.Random(42)
    ahora = ahora_ny()
    fin = ahora - timedelta(minutes=RETRASO_MIN)
    log(f"⬇️ Descargando velas diarias y de 5 min de {len(POOL)} símbolos (puede tardar varios minutos)...")
    diarias_raw = descargar_barras(POOL, TimeFrame(1, TimeFrameUnit.Day),
                                   ahora - timedelta(days=int(BACKTEST_DIAS * 1.5) + 60), fin)
    raw5 = descargar_barras(list(diarias_raw), TimeFrame(5, TimeFrameUnit.Minute),
                            ahora - timedelta(days=int(BACKTEST_DIAS * 1.5) + 30), fin)
    if not raw5:
        log("❌ Sin datos suficientes.")
        return
    vol_min = VOL_MEDIO_MIN if _feed == DataFeed.SIP else 0
    if vol_min == 0:
        log("⚠️ Datos IEX: los volúmenes son solo una fracción del total, se desactiva el filtro de volumen medio.")

    regs = []
    for s, df in raw5.items():
        if s in diarias_raw and len(diarias_raw[s]) > 30:
            regs += registros_simbolo(s, preparar_5m(df), preparar_diaria(diarias_raw[s]), vol_min)
    if not regs:
        log("❌ Ningún símbolo/día pasó los filtros.")
        return

    ref = max(raw5, key=lambda s: len(raw5[s]))
    dref = preparar_5m(raw5[ref])
    hoy = ahora.date()
    dias = sorted({f for f in dref[dref["minuto"] == APERTURA_MIN]["fecha"] if f < hoy})[-BACKTEST_DIAS:]
    por_fecha = defaultdict(list)
    for r in regs:
        por_fecha[r["fecha"]].append(r)
    log(f"▶️ Backtest sobre {len(dias)} sesiones ({dias[0]} → {dias[-1]}), {len(raw5)} símbolos, "
        f"direcciones: {DIRECCION}, coste {COSTE_PCT:.2%}")

    sel_trades, filas_csv, dias_con_sel = [], [], 0
    est_diario = []                       # lista de listas de operaciones por día (estrategia)
    az_trades = [[] for _ in range(REPS_ALEATORIO)]
    az_diario = [[] for _ in range(REPS_ALEATORIO)]
    for D in dias:
        univ = por_fecha.get(D, [])
        sel = sorted([r for r in univ if r["rv"] >= RV_MIN], key=lambda r: r["rv"], reverse=True)[:N_TOP]
        if not sel:
            est_diario.append([])
            for k in range(REPS_ALEATORIO):
                az_diario[k].append([])
            continue
        dias_con_sel += 1
        tr = [r for r in sel if permitido(r)]
        est_diario.append([r["trade"] for r in tr])
        for r in tr:
            sel_trades.append(r)
            filas_csv.append({"fecha": D, "simbolo": r["sym"], "rv": round(r["rv"], 2),
                              "direccion": "largo" if r["dir"] == 1 else "corto",
                              "R_bruto": round(r["trade"]["rb"], 3),
                              "R_neto": round(neto(r["trade"], COSTE_PCT), 3), "salida": r["trade"]["tipo"]})
        # Línea base: mismo nº de símbolos, elegidos al azar en el universo líquido del día
        for k in range(REPS_ALEATORIO):
            muestra = rng.sample(univ, min(len(sel), len(univ)))
            tr_az = [r["trade"] for r in muestra if permitido(r)]
            az_diario[k].append(tr_az)
            az_trades[k] += tr_az

    if not sel_trades:
        log("❌ La estrategia no generó operaciones en este periodo.")
        return

    c = COSTE_PCT
    r_est = [neto(r["trade"], c) for r in sel_trades]
    r_az = [neto(t, c) for lst in az_trades for t in lst]
    st_e, st_a = stats(r_est), stats(r_az)
    ret_e, dd_e = curva([retorno_dia(d, c) for d in est_diario])
    rets_az = [curva([retorno_dia(d, c) for d in az_diario[k]]) for k in range(REPS_ALEATORIO)]
    ret_a = float(np.mean([x[0] for x in rets_az]))
    dd_a = float(np.mean([x[1] for x in rets_az]))

    print("\n" + "=" * 86)
    print(linea("ESTRATEGIA (top RV) ", st_e, ret_e, dd_e))
    print(linea("AL AZAR (mismo n)   ", st_a, ret_a, dd_a, f" (media de {REPS_ALEATORIO} repeticiones)"))
    print("=" * 86)
    print(f"Días con al menos un 'stock in play': {dias_con_sel} de {len(dias)} | "
          f"operaciones por día (media): {len(sel_trades) / max(dias_con_sel, 1):.1f}")

    print("\nSensibilidad al coste ida y vuelta (R medio por operación):")
    print("  coste     estrategia      al azar")
    for cc in COSTES_SENSIBILIDAD:
        e = np.mean([neto(r["trade"], cc) for r in sel_trades])
        a = np.mean([neto(t, cc) for lst in az_trades for t in lst])
        print(f"  {cc:.2%}   {e:+9.3f}   {a:+9.3f}")

    if DIRECCION != "largos":
        print("\nDesglose de la estrategia (con el coste indicado):")
        for nombre, d in (("largos", 1), ("cortos", -1)):
            sub = [neto(r["trade"], c) for r in sel_trades if r["dir"] == d]
            print("  " + linea(nombre, stats(sub)))

    se_dif = math.sqrt(st_e["se"] ** 2 + (st_a["se"] if st_a else 0.0) ** 2)
    dif = st_e["r"] - (st_a["r"] if st_a else 0.0)
    print(f"\nDiferencia de R medio frente al azar: {dif:+.3f} (±{se_dif:.3f})")
    if st_e["r"] <= 0:
        print("⚠️ El R medio es <= 0: tras costes NO hay beneficio en este periodo.")
    if dif <= 0:
        print("⚠️ No supera a elegir al azar en el mismo universo: el filtro de volumen relativo no aporta aquí.")
    elif abs(dif) < 2 * se_dif:
        print("ℹ️ Supera al azar, pero la diferencia está dentro del ruido estadístico (menos de 2 errores estándar).")
    else:
        print("ℹ️ Supera al azar de forma estadísticamente apreciable en este periodo. Valida con otro periodo.")
    if st_e["n"] < 100:
        print("ℹ️ Menos de 100 operaciones: resultado poco concluyente.")
    print("Notas: entrada con orden stop en el extremo de la vela de 9:30 (si hay hueco, a la apertura de la vela); "
          "stop = 10 % del ATR diario; salida al cierre. Si la vela de entrada toca también el stop, se asume stop. "
          "Las operaciones de un mismo día están correlacionadas, así que los errores estándar son optimistas. "
          "El retorno compuesto es aproximado (exposición máxima 4x, sin comisiones por acción, sin costes de préstamo en cortos).")

    pd.DataFrame(filas_csv).to_csv(CSV_DETALLE, index=False)
    print(f"Detalle guardado en {CSV_DETALLE}")


# ==========================================================
# MAIN
# ==========================================================
if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "backtest"
    if modo != "backtest":
        print("Por ahora solo existe el modo 'backtest':  python bot_orb.py backtest")
        sys.exit(1)
    if iniciar_clientes():
        backtest()
