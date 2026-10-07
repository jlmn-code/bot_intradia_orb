# -*- coding: utf-8 -*-
"""
Bot ORB 15 minutos (Opening Range Breakout) - Alpaca, SOLO PAPER TRADING
Implementa las pautas de orb.txt. No hay modo backtest: opera de verdad en la cuenta paper.

Reglas (por símbolo, solo largos)
  1. Rango de apertura = 9:30-9:45 NY (máximo y mínimo de esos 15 minutos).
  2. Entrada: si el precio rompe el MÁXIMO del rango, compra a mercado. Si no rompe, no se opera.
  3. Stop loss en el MÍNIMO del rango (orden stop enviada junto a la compra, tipo OTO).
  4. Sin take profit: se aprovecha la tendencia del día.
  5. Salida: el modo "cierre" cancela el stop y cierra la posición al final de la sesión (15:35-15:50 NY).

Cómo funciona (importante)
  Alpaca NO permite una orden de entrada "stop" (comprar al romper) con el stop loss adjunto: la entrada
  en una orden OTO solo puede ser a mercado o límite. Por eso el modo "entrada" es un proceso que dura
  hasta ENTRADA_HASTA (11:00 NY): espera a las 9:45, calcula el rango y vigila el precio cada POLL_SEG
  segundos; en cuanto un símbolo supera su máximo envía la compra a mercado con el stop adjunto.

Modos:   python bot_orb.py entrada | cierre
Credenciales (claves de PAPER): API_KEY_ORB / SECRET_KEY_ORB (o APCA_API_KEY_ID / APCA_API_SECRET_KEY).
DRY_RUN: solo es False si la variable de entorno DRY_RUN vale false/0/no.
FEED_DATOS: "iex" (por defecto, gratis y en tiempo real, aunque solo recoge operaciones de IEX) o "sip" (plan de pago).

AVISO: herramienta de aprendizaje. Las cifras de orb.txt son estimaciones sin verificar, no una ventaja demostrada.
"""

import os
import sys
import csv
import math
import time
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest, StopLossRequest, GetOrdersRequest, GetCalendarRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed

# ==========================================================
# CONFIGURACIÓN
# ==========================================================
DRY_RUN = os.environ.get("DRY_RUN", "true").strip().lower() not in ("false", "0", "no")
FEED = DataFeed.SIP if os.environ.get("FEED_DATOS", "iex").strip().lower() == "sip" else DataFeed.IEX
NY = ZoneInfo("America/New_York")
PREFIJO = "orb-"                   # prefijo de client_order_id: el cierre solo toca lo de este bot
LOG_CSV = "registro_orb.csv"

# Universo (orb.txt recomienda NVDA, TSLA, AAPL/MSFT y QQQ)
POOL = ["AAPL", "NVDA", "TSLA", "QQQ"]

APERTURA = dtime(9, 30)
FIN_RANGO = dtime(9, 45)           # el rango son los primeros 15 minutos
ENTRADA_HASTA = dtime(11, 0)       # no se abren entradas nuevas a partir de esta hora (NY)
CIERRE_PERMITIDO = (dtime(15, 25), dtime(15, 59))
POLL_SEG = 15                      # cada cuántos segundos se mira el precio
MIN_VELAS_RANGO = 12               # de 15 velas de 1 min, mínimo para dar el rango por válido

# Filtros de seguridad (no vienen de orb.txt)
MAX_PERSECUCION_PCT = 0.005        # si al detectar la ruptura el precio ya está >0,5 % sobre el máximo, no se persigue
RIESGO_MIN_PCT = 0.002             # el stop no puede quedar a menos del 0,2 % del precio
RIESGO_MAX_PCT = 0.03              # si el stop queda a más del 3 %, no se entra

# Tamaño (orb.txt usa el 100 % del capital en NVDA; aquí se arriesga una fracción para repartir entre símbolos)
RIESGO_POR_OPERACION = 0.005       # 0,5 % del equity arriesgado por operación
MAX_POSICION_PCT = 0.25            # máximo 25 % del equity en un símbolo
MAX_EXPOSICION = 0.80              # máximo 80 % del equity entre todas las posiciones
MAX_POSICIONES = 4

trading_client = None
data_client = None
CAMPOS_CSV = ["timestamp", "evento", "simbolo", "qty", "orb_alto", "orb_bajo", "precio", "stop", "pnl", "modo",
              "orden_id"]


# ==========================================================
# UTILIDADES
# ==========================================================
def ahora_ny():
    return datetime.now(NY)


def log(msg):
    print(f"[{ahora_ny():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def registrar_csv(fila):
    nuevo = not os.path.exists(LOG_CSV)
    fila = {"timestamp": ahora_ny().isoformat(timespec="seconds"), "modo": "DRY_RUN" if DRY_RUN else "PAPER", **fila}
    with open(LOG_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS_CSV, restval="", extrasaction="ignore")
        if nuevo:
            w.writeheader()
        w.writerow(fila)


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
    global trading_client, data_client
    key = secreto("API_KEY_ORB", "APCA_API_KEY_ID", "API_KEY")
    sec = secreto("SECRET_KEY_ORB", "APCA_API_SECRET_KEY", "SECRET_KEY")
    if not key or not sec:
        log("❌ Faltan credenciales (API_KEY_ORB / SECRET_KEY_ORB). Usa claves de PAPER TRADING.")
        return False
    data_client = StockHistoricalDataClient(api_key=key, secret_key=sec)
    trading_client = TradingClient(api_key=key, secret_key=sec, paper=True)
    try:
        trading_client.get_account()
    except Exception as e:
        if "401" in str(e):
            log("❌ Alpaca rechaza las credenciales (401): usa las claves de PAPER (empiezan por 'PK').")
            return False
        raise
    return True


def esperar_hasta(hora, extra_seg=0):
    objetivo = datetime.combine(ahora_ny().date(), hora, tzinfo=NY) + timedelta(seconds=extra_seg)
    while True:
        falta = (objetivo - ahora_ny()).total_seconds()
        if falta <= 0:
            return
        time.sleep(min(15, falta))


def fuera_de_franja(rango, nombre):
    if DRY_RUN:
        return False
    t = ahora_ny().time()
    if ahora_ny().weekday() >= 5 or not (rango[0] <= t <= rango[1]):
        log(f"⏸️ Modo {nombre}: fuera de la franja permitida {rango[0]:%H:%M}-{rango[1]:%H:%M} NY. No se hace nada.")
        return True
    return False


# ==========================================================
# DATOS
# ==========================================================
def rango_apertura(hoy):
    """{simbolo: {'alto','bajo','n'}} con el máximo y mínimo de las velas de 1 min entre 9:30 y 9:45 NY."""
    ini = datetime.combine(hoy, APERTURA, tzinfo=NY)
    fin = datetime.combine(hoy, FIN_RANGO, tzinfo=NY)
    salida = {}
    try:
        df = data_client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=POOL, timeframe=TimeFrame(1, TimeFrameUnit.Minute),
            start=ini, end=fin - timedelta(seconds=1), feed=FEED)).df
    except Exception as e:
        log(f"❌ No se pudieron descargar las velas del rango: {str(e)[:150]}")
        return salida
    if df is None or df.empty:
        return salida
    for sym, g in df.groupby(level="symbol"):
        g = g.droplevel("symbol").sort_index()
        g = g[(g.index >= ini) & (g.index < fin)]
        if len(g) < MIN_VELAS_RANGO:
            log(f"   ⚠️ {sym}: rango incompleto ({len(g)} de 15 velas), se descarta hoy")
            continue
        salida[sym] = {"alto": float(g["high"].max()), "bajo": float(g["low"].min()), "n": len(g)}
    return salida


def precios_actuales(simbolos):
    """Último precio negociado de varios símbolos en una sola llamada. {} si falla."""
    try:
        t = data_client.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=list(simbolos), feed=FEED))
        return {s: float(v.price) for s, v in t.items()}
    except Exception as e:
        log(f"   ⚠️ sin precios en tiempo real ({str(e)[:80]})")
        return {}


# ==========================================================
# ÓRDENES Y TAMAÑO
# ==========================================================
def calcular_cantidad(equity, exposicion, precio, riesgo):
    q_riesgo = math.floor(equity * RIESGO_POR_OPERACION / riesgo)
    q_pos = math.floor(equity * MAX_POSICION_PCT / precio)
    q_exp = math.floor(max(0.0, equity * MAX_EXPOSICION - exposicion) / precio)
    return min(q_riesgo, q_pos, q_exp), (q_riesgo, q_pos, q_exp)


def ordenes_del_bot(dias=5):
    inicio = ahora_ny() - timedelta(days=dias)
    ords = trading_client.get_orders(GetOrdersRequest(status=QueryOrderStatus.ALL, after=inicio, limit=500))
    return [o for o in ords if (o.client_order_id or "").startswith(PREFIJO)]


def dia_de_cierre_anticipado(hoy):
    """True si hoy el mercado cierra antes de las 16:00 (el cierre del bot llegaría tarde)."""
    try:
        cal = trading_client.get_calendar(GetCalendarRequest(start=hoy, end=hoy))
        return bool(cal) and cal[0].close.time() < dtime(16, 0)
    except Exception as e:
        log(f"⚠️ No se pudo consultar el calendario ({str(e)[:80]}); se continúa.")
        return False


# ==========================================================
# MODO ENTRADA
# ==========================================================
def ejecutar_entrada():
    hoy = ahora_ny().date()
    fin_ventana = datetime.combine(hoy, ENTRADA_HASTA, tzinfo=NY)
    if not DRY_RUN:
        t = ahora_ny().time()
        if ahora_ny().weekday() >= 5 or not (APERTURA <= t < ENTRADA_HASTA):
            log(f"⏸️ Modo entrada: fuera de la franja {APERTURA:%H:%M}-{ENTRADA_HASTA:%H:%M} NY. No se hace nada.")
            return
        if not trading_client.get_clock().is_open:
            log("⏸️ Mercado cerrado. No se hace nada.")
            return
        if dia_de_cierre_anticipado(hoy):
            log("⏸️ Hoy el mercado cierra antes de las 16:00: no se entra.")
            return

    # 1) Esperar a que termine el rango (9:45) y calcularlo
    if APERTURA <= ahora_ny().time() < FIN_RANGO:
        log(f"⏳ Esperando a las {FIN_RANGO:%H:%M} NY para cerrar el rango de apertura...")
        esperar_hasta(FIN_RANGO, 10)
    rangos = rango_apertura(hoy)
    if not rangos:
        log("❌ No hay rango de apertura de hoy (¿festivo, fuera de horario o sin datos?).")
        return

    equity = float(trading_client.get_account().equity)
    posiciones = {p.symbol: p for p in trading_client.get_all_positions()}
    exposicion = sum(abs(float(p.market_value)) for p in posiciones.values())
    historial = ordenes_del_bot()
    de_hoy = {o.symbol for o in historial if o.created_at.astimezone(NY).date() == hoy}
    previas = {o.symbol for o in historial if o.created_at.astimezone(NY).date() < hoy}
    for s in sorted((previas & set(posiciones)) - de_hoy):
        log(f"⚠️ {s}: sigue abierta una posición de un día anterior de este bot. Ciérrala a mano si no la quieres.")
    log(f"💼 Equity ${equity:,.2f} | posiciones {len(posiciones)} | exposición {exposicion / equity:.0%} "
        f"| feed {FEED.value}")

    activos = {}
    for s in POOL:
        r = rangos.get(s)
        if r is None:
            continue
        log(f"   📐 {s}: rango {r['bajo']:.2f} - {r['alto']:.2f} ({(r['alto'] - r['bajo']) / r['alto']:.2%})")
        if s in posiciones:
            log(f"   ⏭️ {s}: ya hay una posición abierta en la cuenta")
        elif s in de_hoy:
            log(f"   ⏭️ {s}: este bot ya operó hoy en este símbolo")
        else:
            activos[s] = r
    n_pos = len(posiciones)

    # 2) Vigilar la ruptura del máximo hasta ENTRADA_HASTA (en DRY_RUN fuera de horario: una sola pasada)
    una_pasada = DRY_RUN and ahora_ny() >= fin_ventana
    vueltas = 0
    precios = {}
    while activos and n_pos < MAX_POSICIONES and (una_pasada or ahora_ny() < fin_ventana):
        precios = precios_actuales(activos)
        for s in list(activos):
            p, r = precios.get(s), activos[s]
            if p is None or p <= r["alto"]:
                continue
            if p > r["alto"] * (1 + MAX_PERSECUCION_PCT):
                log(f"   ⏭️ {s}: ruptura ya muy extendida ({p:.2f} vs máximo {r['alto']:.2f}); no se persigue")
                del activos[s]
                continue
            stop = round(r["bajo"], 2)
            riesgo = p - stop
            if riesgo / p < RIESGO_MIN_PCT or riesgo / p > RIESGO_MAX_PCT:
                log(f"   ⏭️ {s}: stop a {riesgo / p:.2%} del precio (fuera de {RIESGO_MIN_PCT:.1%}-{RIESGO_MAX_PCT:.0%})")
                del activos[s]
                continue
            qty, det = calcular_cantidad(equity, exposicion, p, riesgo)
            if qty < 1:
                log(f"   ⚠️ {s}: tamaño < 1 acción (riesgo/posición/exposición: {det})")
                del activos[s]
                continue
            log(f"🟢 RUPTURA {s}: precio {p:.2f} > máximo {r['alto']:.2f} | COMPRA {qty} acc a mercado "
                f"| STOP {stop:.2f} ({riesgo / p:.2%}) | riesgo ${qty * riesgo:,.0f}")
            oid = ""
            if not DRY_RUN:
                try:
                    o = trading_client.submit_order(MarketOrderRequest(
                        symbol=s, qty=qty, side=OrderSide.BUY, time_in_force=TimeInForce.GTC,
                        order_class=OrderClass.OTO, stop_loss=StopLossRequest(stop_price=stop),
                        client_order_id=f"{PREFIJO}{s}-{hoy:%Y%m%d}"))
                    oid = str(o.id)
                    time.sleep(3)
                    o2 = trading_client.get_order_by_id(o.id)
                    log(f"   ✅ Orden {oid[:8]}: {str(getattr(o2.status, 'value', o2.status))} "
                        f"(precio medio {o2.filled_avg_price})")
                except Exception as e:
                    log(f"❌ Error enviando {s}: {e}")
                    del activos[s]
                    continue
            registrar_csv({"evento": "entrada", "simbolo": s, "qty": qty, "orb_alto": r["alto"], "orb_bajo": r["bajo"],
                           "precio": p, "stop": stop, "orden_id": oid})
            exposicion += qty * p
            n_pos += 1
            del activos[s]
            if n_pos >= MAX_POSICIONES:
                break
        if una_pasada:
            break
        vueltas += 1
        if vueltas % 20 == 0 and activos:
            log("👀 Vigilando: " + " | ".join(
                f"{s} {precios.get(s, float('nan')):.2f}/{r['alto']:.2f}" for s, r in activos.items()))
        time.sleep(POLL_SEG)

    if activos:
        log("ℹ️ Sin ruptura (o sin entrada) en: " + ", ".join(activos) + ". Es normal: no se fuerza ninguna entrada.")


# ==========================================================
# MODO CIERRE (solo lo de este bot, solo las órdenes de hoy)
# ==========================================================
def ejecutar_cierre():
    if fuera_de_franja(CIERRE_PERMITIDO, "cierre"):
        return
    hoy = ahora_ny().date()
    syms = sorted({o.symbol for o in ordenes_del_bot() if o.created_at.astimezone(NY).date() == hoy})
    if not syms:
        log("ℹ️ El bot no abrió operaciones hoy. Nada que cerrar.")
        return
    posiciones = {p.symbol: p for p in trading_client.get_all_positions()}
    objetivo = [s for s in syms if s in posiciones]
    if not objetivo:
        log(f"ℹ️ Las operaciones de hoy ({', '.join(syms)}) ya no tienen posición abierta (stop ejecutado). Nada que cerrar.")
        return
    log(f"🔔 Cierre de fin de día para: {', '.join(objetivo)}")
    abiertas = list(trading_client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=objetivo, limit=500)))
    for o in abiertas:
        log(f"   cancelando orden abierta {o.symbol} ({str(getattr(o.order_type, 'value', o.order_type))})")
        if not DRY_RUN:
            try:
                trading_client.cancel_order_by_id(o.id)
            except Exception as e:
                log(f"   ⚠️ {e}")
    if not DRY_RUN and abiertas:
        time.sleep(3)
    for s in objetivo:
        p = posiciones[s]
        pnl = float(p.unrealized_pl)
        log(f"🔴 Cerrando {s}: {p.qty} acc | entrada {float(p.avg_entry_price):.2f} | precio {float(p.current_price):.2f} "
            f"| P&L {pnl:+,.2f}")
        registrar_csv({"evento": "cierre", "simbolo": s, "qty": p.qty, "precio": p.current_price, "pnl": pnl})
        if DRY_RUN:
            continue
        for intento in range(2):
            try:
                trading_client.close_position(s)
                break
            except Exception as e:
                log(f"❌ Error cerrando {s} (intento {intento + 1}): {e}")
                time.sleep(3)


# ==========================================================
# MAIN
# ==========================================================
def main(modo):
    if modo not in ("entrada", "cierre"):
        print("Uso: python bot_orb.py entrada | cierre")
        return 1
    if not iniciar_clientes():
        return 1
    log("🧪 DRY_RUN: se simula, NO se envían órdenes" if DRY_RUN else "🚀 PAPER: se envían órdenes a la cuenta paper")
    if modo == "entrada":
        ejecutar_entrada()
    else:
        ejecutar_cierre()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
