import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import time
import os
from datetime import datetime, timedelta

# === Config ===
SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD"]
TIMEFRAME = mt5.TIMEFRAME_M5
BARS = 200
LOT_SIZE = 0.1
DEVIATION = 10
MAGIC = 777777
APP_PASSWORD = os.getenv("APP_PASSWORD")


# === Initialize MT5 ===
def init_mt5():
    if not mt5.initialize():
        print("MT5 Initialization failed:", mt5.last_error())
        return False
    # Replace with your actual credentials
    login_success = mt5.login(36610, password="Marichu12", server="EGMSecurities-Demo")
    if not login_success:
        print("MT5 Login failed:", mt5.last_error())
        return False
    print("MT5 login successful.")
    return True

# === Detect liquidity zones ===
def find_liquidity_zones(df):
    high_swings = df[(df['high'] > df['high'].shift(1)) & (df['high'] > df['high'].shift(-1))]
    low_swings = df[(df['low'] < df['low'].shift(1)) & (df['low'] < df['low'].shift(-1))]

    return high_swings['high'].max(), low_swings['low'].min()

# === Detect liquidity sweep ===
def is_liquidity_sweep(df, high_zone, low_zone):
    latest = df.iloc[-1]
    previous = df.iloc[-2]

    # Sweep above high and close below
    if latest['high'] > high_zone and latest['close'] < previous['close']:
        return "sell"

    # Sweep below low and close above
    if latest['low'] < low_zone and latest['close'] > previous['close']:
        return "buy"

    return None

# === Detect order blocks ===
def detect_order_block(df, direction):
    if direction == "buy":
        return df[(df["close"] > df["open"]) & (df["close"].shift(1) < df["open"].shift(1))].iloc[-1]
    else:
        return df[(df["close"] < df["open"]) & (df["close"].shift(1) > df["open"].shift(1))].iloc[-1]

# === Place order ===
def place_order(symbol, direction):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        print(f"No tick for {symbol}")
        return

    order_type = mt5.ORDER_TYPE_BUY if direction == "buy" else mt5.ORDER_TYPE_SELL
    price = tick.ask if direction == "buy" else tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": LOT_SIZE,
        "type": order_type,
        "price": price,
        "deviation": DEVIATION,
        "magic": MAGIC,
        "comment": f"MMXN_{direction.upper()}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC
    }

    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"Trade failed: {result.comment}")
    else:
        print(f"✅ MMXN Trade executed: {direction.upper()} on {symbol} at {price}")

# === Core MMXN logic ===
def process_symbol(symbol):
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, BARS)
    if rates is None or len(rates) == 0:
        print(f"No data for {symbol}")
        return

    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')

    high_zone, low_zone = find_liquidity_zones(df)
    direction = is_liquidity_sweep(df, high_zone, low_zone)

    if direction:
        ob = detect_order_block(df, direction)
        print(f"{symbol} | MMXN Signal: {direction.upper()} | OB: {ob['open']:.2f}")
        place_order(symbol, direction)

# === Main loop ===
def main():
    if not init_mt5():
        return

    while True:
        print(f"\n🔄 Running MMXN model at {datetime.utcnow()}")
        for sym in SYMBOLS:
            process_symbol(sym)
        time.sleep(60)  # run every 1 min

if __name__ == "__main__":
    main()
