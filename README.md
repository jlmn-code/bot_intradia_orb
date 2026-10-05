# bot_intradia_orb
Opening Range Breakout sobre "stocks in play" (Zarattini, Barbon y Aziz, 2023): compra la ruptura del máximo de los primeros 5 minutos en acciones con volumen relativo muy alto y una noticia o gap, con stop en el otro extremo del rango y cierre al final del día.
Opening Range Breakout sobre "stocks in play" (Zarattini, Barbon y Aziz, 2023): compra la ruptura del máximo de los primeros 5 minutos en acciones con volumen relativo muy alto y una noticia o gap, con stop en el otro extremo del rango y cierre al final del día. Es probablemente la más citada en day trading y se parece bastante a tu señal de ruptura, pero con un filtro de volumen relativo mucho más estricto.
VWAP como sistema de tendencia (Zarattini y Aziz, 2023): entra a favor del precio respecto al VWAP, en la línea de tu técnica de tendencia.
Momentum intradía del índice (Gao et al., 2018): la rentabilidad de la primera media hora de SPY predice la de la última media hora. Es simple y fácil de testear con una sola acción.

Las dos primeras tienen un problema: sus resultados son muy altos y los autores tienen intereses comerciales. Las críticas habituales son que subestiman el deslizamiento y las comisiones en acciones pequeñas y poco líquidas, y que los resultados no se han replicado tanto como cabría esperar. Con un coste del 0,10 %, tu backtest ya es más conservador que muchos de esos estudios.

En tu bot, el cambio más prometedor sería sustituir el ranking por volatilidad por un filtro de volumen relativo (volumen de la ventana frente a su media de los últimos 14 días). Es un cambio pequeño, y tu backtest compara con entradas al azar, así que ya sabrías si mejora.

Si quieres, te preparo un modo backtest_orb que pruebe esa variante sobre el mismo periodo y universo para compararla con la actual.
