import time
import logging
import pandas as pd
import talib
from binance.spot import Spot
from config import API_KEY, SECRET_KEY
import math

# Configuración del logging
logging.basicConfig(
    filename='trading_log.txt', 
    level=logging.INFO, 
    format='%(asctime)s - %(message)s'
)

# Configuración del cliente de Binance
client = Spot(api_key=API_KEY, api_secret=SECRET_KEY)

# Obtener y mostrar la marca de tiempo del servidor
print(client.time())

# Obtener y mostrar la información de la cuenta y balance
account = client.account()
balances = account['balances']
df_balances = pd.DataFrame(balances)
df_balances['amount'] = df_balances['free'].astype(float) + df_balances['locked'].astype(float)
df_balances = df_balances[df_balances['amount'] != 0]
#print(df_balances)



# Columnas para el DataFrame de velas
columns_velas = [
    "Open Time", "Open", "High", "Low", "Close", "Volume", "Close Time",
    "Quote Asset Volume", "Number of Trades", "Taker Buy Base Asset Volume",
    "Taker Buy Quote Asset Volume", "Ignore"
]

# Pares e intervalo de tiempo
pares = ["WIFUSDT", "SOLUSDT", "LISTAUSDT", "XRPUSDT", "BTCUSDT", "DOGEUSDT"]  # Añadir los pares que desees
temporalidad = "5m"
# Variables globales
comprado = {par: False for par in pares}
flag_perdida = False
capital = {par: 10 for par in pares}
list_ordenes = []
dict_ordenes = {par: {} for par in pares}

def obtener_precision(par):
    exchange_info = client.exchange_info()
    for symbol_info in exchange_info['symbols']:
        if symbol_info['symbol'] == par:
            for filter in symbol_info['filters']:
                if filter['filterType'] == 'LOT_SIZE':
                    step_size = float(filter['stepSize'])
                    precision = int(round(-math.log(step_size, 10), 0))
                    return precision
    return 8  # Valor predeterminado si no se encuentra el par

def crear_orden(tipo, par, cantidad, precio=None):
    precision = obtener_precision(par)
    cantidad = round(cantidad, precision)
    orden = client.new_order(
        symbol=par,
        side='BUY' if tipo == 'compra' else 'SELL',
        type='MARKET',
        quantity=cantidad
    )
    return orden

def obtener_datos_velas(par, temporalidad):
    velas_par = client.klines(par, temporalidad, limit=1000)
    df = pd.DataFrame(velas_par, columns=columns_velas)
    df['par'] = par

    # Convertir columnas de tiempo a formato legible
    df["Open Time"] = pd.to_datetime(df["Open Time"], unit='ms')
    df["Close Time"] = pd.to_datetime(df["Close Time"], unit='ms').dt.tz_localize('UTC').dt.tz_convert('America/Santiago')

    # Convertir columnas a tipo float
    df[["Open", "High", "Low", "Close", "Volume"]] = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    
    return df

def calcular_indicadores(df):
    df['rsi'] = talib.RSI(df['Close'], 8)
    df['macd'], df['macdSignal'], df['macdHist'] = talib.MACD(df['Close'], fastperiod=12, slowperiod=26, signalperiod=9)
    df['slowk'], df['slowd'] = talib.STOCH(df['High'], df['Low'], df['Close'], fastk_period=14, slowk_period=3, slowd_period=3)
    df['upperband'], df['middleband'], df['lowerband'] = talib.BBANDS(df['Close'], timeperiod=20, nbdevup=2, nbdevdn=2)
    
    return df

def evaluar_compra_venta(par, row, comprado, capital, dict_ordenes):
    macd = row['macd']
    macdHist = row['macdHist']
    rsi = row['rsi']
    slowk = row['slowk']
    slowd = row['slowd']
    upperband = row['upperband']
    lowerband = row['lowerband']
    close = row['Close']
    close_time = row['Close Time']
    par_orden = row['par']
    macd_compra = macd < 0 and macdHist < 0 and macd < macdHist

    if rsi < 30 and not comprado and macd_compra and slowk < 20 and slowd < 20 and close < lowerband:
        cantidad_compra = capital / close
        dict_ordenes.update({
            'fecha_compra': close_time,
            'precio_compra': close,
            'par_orden': par_orden,
            'cantidad': cantidad_compra
        })
        orden = crear_orden('compra', par, cantidad_compra)
        logging.info(f'Comprado {par}: {dict_ordenes}')
        print(f"compre {par} a", close)
        logging.info(f'orden: {orden}')
        return True, dict_ordenes, capital

    venta_condicion_1 = rsi > 70 and slowk > 80 and slowd > 80 and macd > 0 and macdHist > 0 and macd > macdHist
    venta_condicion_2 = rsi > 70 and slowk > 80 and slowd > 80 and close > upperband

    if comprado and (venta_condicion_1 or venta_condicion_2):
        cantidad_venta = dict_ordenes['cantidad']
        dict_ordenes.update({
            'fecha_venta': close_time,
            'precio_venta': close,
            'g/p': ((close / dict_ordenes['precio_compra']) - 1),
            'capital_nuevo': capital + (((close / dict_ordenes['precio_compra']) - 1) * capital)
        })
        orden = crear_orden('venta', par, cantidad_venta)
        logging.info(f'Vendido {par}: {dict_ordenes}')
        logging.info(f'orden: {orden}')
        list_ordenes.append(dict_ordenes)
        print(f"vendi {par} a", close)
        return False, {}, dict_ordenes['capital_nuevo']

    if comprado and (((close / dict_ordenes['precio_compra']) - 1) * 100) < 1 and flag_perdida:
        cantidad_venta = dict_ordenes['cantidad']
        dict_ordenes.update({
            'fecha_venta': close_time,
            'precio_venta': close,
            'g/p': ((close / dict_ordenes['precio_compra']) - 1),
            'capital_nuevo': capital + (((close / dict_ordenes['precio_compra']) - 1) * capital)
        })
        orden = crear_orden('venta', par, cantidad_venta)
        logging.info(f'Vendido {par}: {dict_ordenes}')
        logging.info(f'orden: {orden}')
        list_ordenes.append(dict_ordenes)
        print(f"vendi {par} a", close)
        return False, {}, dict_ordenes['capital_nuevo']

    return comprado, dict_ordenes, capital

while True:
    for par in pares:
        df = obtener_datos_velas(par, temporalidad)
        df = calcular_indicadores(df)
        df = df[['Close Time', 'Close', 'rsi', 'macdHist', 'macd', 'macdSignal', 'slowk', 'slowd', 'upperband', 'middleband', 'lowerband', 'par']].tail(1)
        print(df)

        for _, row in df.iterrows():
            comprado[par], dict_ordenes[par], capital[par] = evaluar_compra_venta(par, row, comprado[par], capital[par], dict_ordenes[par])
        
    time.sleep(5)
