import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from tradenotifier import send_email_notification
import os
from datetime import datetime, timedelta

# Define trading sessions in NY Time, convert to UTC (+5)
SILVER_BULLET_SESSIONS = {
    "London_Open": {"start": (3, 0), "end": (4, 0)},    # 3AM–4AM NY
    "New_York_AM": {"start": (10, 0), "end": (11, 0)},  # 10AM–11AM NY
    "New_York_PM": {"start": (14, 0), "end": (15, 0)}   # 2PM–3PM NY
}

def convert_to_utc(session):
    return {
        "start": (session["start"][0] + 5, session["start"][1]),
        "end": (session["end"][0] + 5, session["end"][1])
    }

SILVER_BULLET_SESSIONS_UTC = {key: convert_to_utc(val) for key, val in SILVER_BULLET_SESSIONS.items()}
NUM_BARS = 100
APP_PASSWORD = os.getenv("APP_PASSWORD")
LOT_SIZE = 0.1
DEVIATION = 10

# === CONNECTION ===
def conn():
    if not mt5.initialize():
        print("Initialization failed:", mt5.last_error())
        return

    login = mt5.login(36610, APP_PASSWORD, "EGMSecurities-Demo")
    if not login:
        print("Login failed:", mt5.last_error())
        return

    print("Login successful.")
    gatherDataController()

# === DATA GATHERING CONTROLLER ===
def gatherDataController():
    print("Gathering data for Silver Bullet ICT strategy")
    SYMBOLS = ["EURUSD", "GBPUSD", "XAUUSD"]
    TIMEFRAME = mt5.TIMEFRAME_M5

    while True:
        now = datetime.utcnow()
        for symbol in SYMBOLS:
            for session_name, times in SILVER_BULLET_SESSIONS_UTC.items():
                start_hour, start_minute = times["start"]
                end_hour, end_minute = times["end"]
                session_start = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
                session_end = now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)

                if session_start <= now <= session_end:
                    print(f"Within {session_name} session for {symbol}")

                    data = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 1, NUM_BARS)
                    if data is None or len(data) == 0:
                        print(f"No data for {symbol}")
                        continue

                    df = pd.DataFrame(data)
                    df["time"] = pd.to_datetime(df["time"], unit="s")
                    os.makedirs("backend/backtest", exist_ok=True)
                    df.to_csv(f"backend/backtest/{symbol}_{session_name}.csv", index=False)

                    generate_trade_signals(df, symbol, session_name)

# === TECHNICAL DETECTORS ===
def detect_fvg(df):
    df["FVG_Up"] = (df["low"].shift(2) > df["high"].shift(1))
    df["FVG_Down"] = (df["high"].shift(2) < df["low"].shift(1))
    return df

def detect_order_blocks(df):
    df["Bullish_OB"] = (df["close"].shift(1) < df["open"].shift(1)) & (df["close"] > df["open"])
    df["Bearish_OB"] = (df["close"].shift(1) > df["open"].shift(1)) & (df["close"] < df["open"])
    return df

# === TRADE ENTRY FUNCTION ===
def place_order(symbol, direction, volume=LOT_SIZE):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        print(f"No tick data for {symbol}")
        return
    
    signal_message = f"trade execution for order type {order_type} at price {price}"
    
    send_email_notification(f"{symbol} ",signal_message)


    order_type = mt5.ORDER_TYPE_BUY if direction == "buy" else mt5.ORDER_TYPE_SELL
    price = tick.ask if direction == "buy" else tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": price,
        "deviation": DEVIATION,
        "magic": 777777,
        "comment": f"SilverBullet_{direction.upper()}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC
    }

    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"Trade failed for {symbol}: {result.comment}")
    else:
        print(f"Trade executed: {direction.upper()} {symbol} at {price}")

# === SIGNAL PROCESSOR ===
def generate_trade_signals(df, symbol, session):
    df = detect_fvg(df)
    df = detect_order_blocks(df)

    df["Buy_Signal"] = df["Bullish_OB"] & df["FVG_Up"]
    df["Sell_Signal"] = df["Bearish_OB"] & df["FVG_Down"]

    signal = df.iloc[-1]
    os.makedirs("backend/signals", exist_ok=True)
    df.to_csv(f"backend/signals/{symbol}_{session}_signals.csv", index=False)

    if signal["Buy_Signal"]:
        print(f"✅ BUY Signal on {symbol} ({session})")
        place_order(symbol, "buy")
    elif signal["Sell_Signal"]:
        print(f"✅ SELL Signal on {symbol} ({session})")
        place_order(symbol, "sell")

# === MAIN ===
def main():
    conn()

if __name__ == "__main__":
    main()
