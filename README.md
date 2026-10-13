# bot_intradia_orb
Opening Range Breakout sobre "stocks in play" (Zarattini, Barbon y Aziz, 2023): compra la ruptura del máximo de los primeros 5 minutos en acciones con volumen relativo muy alto y una noticia o gap, con stop en el otro extremo del rango y cierre al final del día.
Opening Range Breakout sobre "stocks in play" (Zarattini, Barbon y Aziz, 2023): compra la ruptura del máximo de los primeros 5 minutos en acciones con volumen relativo muy alto y una noticia o gap, con stop en el otro extremo del rango y cierre al final del día. Es probablemente la más citada en day trading y se parece bastante a tu señal de ruptura, pero con un filtro de volumen relativo mucho más estricto.
VWAP como sistema de tendencia (Zarattini y Aziz, 2023): entra a favor del precio respecto al VWAP, en la línea de tu técnica de tendencia.
Momentum intradía del índice (Gao et al., 2018): la rentabilidad de la primera media hora de SPY predice la de la última media hora. Es simple y fácil de testear con una sola acción.

Las dos primeras tienen un problema: sus resultados son muy altos y los autores tienen intereses comerciales. Las críticas habituales son que subestiman el deslizamiento y las comisiones en acciones pequeñas y poco líquidas, y que los resultados no se han replicado tanto como cabría esperar. Con un coste del 0,10 %, tu backtest ya es más conservador que muchos de esos estudios.

En tu bot, el cambio más prometedor sería sustituir el ranking por volatilidad por un filtro de volumen relativo (volumen de la ventana frente a su media de los últimos 14 días). Es un cambio pequeño, y tu backtest compara con entradas al azar, así que ya sabrías si mejora.

Si quieres, te preparo un modo backtest_orb que pruebe esa variante sobre el mismo periodo y universo para compararla con la actual.

El modo sombra está listo en `bot_orb.py` y `bot-orb.yml`, y no he tocado la operativa de entrada y cierre. Los he probado con datos sintéticos de 60 sesiones, y vuelvo a comprobar que el bot sigue entrando con su stop y cerrando como antes. Contra Alpaca de verdad no he podido probarlos.

**Qué hace la sombra**
Tras el cierre del mercado (a las 16:25 NY, con un respaldo a las 16:55), el bot descarga las velas de 1 minuto del día y reproduce, para tus 4 símbolos, cinco variantes **sin enviar órdenes**:

- **BASE**: lo que hace el bot real.
- **CONF5**: espera a que una vela de 5 minutos cierre por encima del máximo del rango.
- **STOP_MEDIO**: el stop en el punto medio del rango, en vez de en el mínimo.
- **BE_1R**: mueve el stop a break-even cuando el precio avanza 1R.
- **TRAIL_1R**: trailing stop a 1R del máximo alcanzado.

Cada día añade sus filas a `datos/sombra_orb.csv`, sin duplicar si un cron se repite. También anota si QQQ estaba por encima de su propio rango cuando entraste, para probar el filtro de contexto. Al final de cada ejecución imprime un resumen acumulado: operaciones, acierto, R medio bruto y neto de coste (0,05 % y 0,10 %) y profit factor, y la diferencia de cada variante frente a BASE en los mismos símbolo-día, con su error estándar.

**Cambios en el workflow**
- Guarda la carpeta `datos/` en el repositorio, incluido el registro real de entradas y cierres, así que el histórico se acumula día a día.
- Tiene dos modos más para lanzar a mano: `sombra` (con una fecha opcional para un día que se haya perdido) y `resumen`.
- Muestra el log de cada ejecución en su página.

# Sigue la técnica de orb.txt? Sí en lo esencial: rango 9:30-9:45, compra al romper el máximo, stop en el mínimo, sin take profit y cierre de fin de día. Hay desviaciones que puse a propósito:

Solo entra hasta las 11:00.
No persigue si el precio ya está más de un 0,5 % por encima del máximo.
Descarta stops a menos del 0,2 % o a más del 3 %.
Compra a mercado al precio actual, no exactamente en el máximo.
Arriesga un 0,5 % por operación en vez del 100 % del capital.
Cierra a las 15:35 o 15:50, no con una orden al cierre.

Minutos de Actions. Entre entrada, cierre y sombra, el bot consume unos 90 minutos al día, es decir unos 1.900 al mes, justo en el límite de 2.000 si el repositorio es privado. Si el repositorio es público, no cuenta.

Hay un archivo bot-orb-corregido.yml en mis salidas que no recuerdo haber generado. Ignóralo y usa el bot-orb.yml de ahora.

Si vuelve a pasar, pégame el log de una ejecución concreta, con su hora y el cron que la lanzó, y lo reviso.

**Para que funcione**
- Si el commit automático falla (aparecerá "No se pudo guardar en el repositorio"), activa *Settings → Actions → General → Workflow permissions → Read and write permissions*.
- Mantén la variable `DRY_RUN` en `false`, o bórrala, para que el bot real siga operando.

**Cómo leerlo**: con 1 o 3 operaciones al día, necesitas meses para llegar a 100 operaciones BASE, y el resumen te avisa mientras sean pocas. No cambies la estrategia por una diferencia que quede dentro de dos errores estándar, y ten en cuenta que la sombra usa velas de 1 minuto con datos de IEX, así que será parecida a la operativa real pero no idéntica.

Para empezar, sube los dos archivos, haz commit, y mañana, después de las 22:25 en Madrid, mira la ejecución de sombra. Pásame el log del primer día y vemos si todo cuadra con lo que hizo el bot real.

¿Sigue la técnica de orb.txt? Sí en lo esencial: rango 9:30-9:45, compra al romper el máximo, stop en el mínimo, sin take profit y cierre de fin de día. Hay desviaciones que puse a propósito:

Solo entra hasta las 11:00.
No persigue si el precio ya está más de un 0,5 % por encima del máximo.
Descarta stops a menos del 0,2 % o a más del 3 %.
Compra a mercado al precio actual, no exactamente en el máximo.
Arriesga un 0,5 % por operación en vez del 100 % del capital.
Cierra a las 15:35 o 15:50, no con una orden al cierre.

Minutos de Actions. Entre entrada, cierre y sombra, el bot consume unos 90 minutos al día, es decir unos 1.900 al mes, justo en el límite de 2.000 si el repositorio es privado. Si el repositorio es público, no cuenta.

Hay un archivo bot-orb-corregido.yml en mis salidas que no recuerdo haber generado. Ignóralo y usa el bot-orb.yml de ahora.

Si vuelve a pasar, pégame el log de una ejecución concreta, con su hora y el cron que la lanzó, y lo reviso.
