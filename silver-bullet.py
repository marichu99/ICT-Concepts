import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta

# Define the trading sessions for Silver Bullet in UTC
SILVER_BULLET_SESSIONS = {
    "London_Open": {"start": (3, 0), "end": (4, 0)},  # NY Time: 3AM - 4AM
    "New_York_AM": {"start": (10, 0), "end": (11, 0)}, # NY Time: 10AM - 11AM
    "New_York_PM": {"start": (14, 0), "end": (15, 0)}  # NY Time: 2PM - 3PM
}

# Convert to UTC (assuming NY is UTC-5)
def convert_to_utc(session):
    start_utc = (session["start"][0] + 5, session["start"][1])
    end_utc = (session["end"][0] + 5, session["end"][1])
    return {"start": start_utc, "end": end_utc}

SILVER_BULLET_SESSIONS_UTC = {key: convert_to_utc(val) for key, val in SILVER_BULLET_SESSIONS.items()}

# Define the number of bars to fetch
NUM_BARS = 100

# get the passwords
APP_PASSWORD = os.getenv("APP_PASSWORD")

def conn():
    # start the connection to MT5
    resu = {
        "Response": 200,
        "Message": "Data from python"
    }
    # resu["server"]=server
    valid_conn = mt5.initialize()
    # check if the connection went through
    if not (valid_conn):
        resu["init_err"] = mt5.last_error()
    # login into your account
    login = mt5.login(36610, APP_PASSWORD, "EGMSecurities-Demo")
    if not login:
        resu["login_err"] = mt5.last_error()
        print("the login was successful")

    else:
        resu["Message"] = "Login is Successful"
        print("the login was successful")
        gatherDataController()

def gatherDataController():
    print("Gathering data for Silver Bullet ICT strategy")
    
    # Define symbols and timeframes
    SYMBOLS = ["EURUSD", "GBPUSD", "XAUUSD"]  # Example assets
    TIMEFRAME = mt5.TIMEFRAME_M5  # 5-minute timeframe for ICT      

    while True:
        for pair in SYMBOLS:
        # Get the current UTC time
            now = datetime.utcnow()

                # Check if the current time is within any of the Silver Bullet sessions
            for session_name, session_times in SILVER_BULLET_SESSIONS_UTC.items():
                start_hour, start_minute = session_times["start"]
                end_hour, end_minute = session_times["end"]
                
                session_start = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
                session_end = now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)

                if session_start <= now <= session_end:
                    print(f"Currently within {session_name} session")
                    
                    # Fetch historical data
                    backtest_data = mt5.copy_rates_from_pos(pair, TIMEFRAME, 1, NUM_BARS)
                    if backtest_data is None or len(backtest_data) == 0:
                        print(f"No data retrieved for {pair}")
                        continue
                    
                    bars = pd.DataFrame(backtest_data)
                    bars["time"] = pd.to_datetime(bars["time"], unit="s")

                    # Save data to CSV
                    filename = f"backend/backtest/{pair}_{session_name}.csv"
                    bars.to_csv(filename, index=False)

                    # Process for FVGs and Order Blocks
                    generate_trade_signals(bars, pair, session_name)

    # Shutdown MT5 connection
    mt5.shutdown()

def detect_fvg(df):
    """
    Detects Fair Value Gaps (FVG) in price action.
    FVG occurs when there is an imbalance in price with a large gap between candle wicks.
    """
    df["FVG_Up"] = (df["low"].shift(2) > df["high"].shift(1))  # Bullish FVG
    df["FVG_Down"] = (df["high"].shift(2) < df["low"].shift(1))  # Bearish FVG
    return df

def detect_order_blocks(df):
    """
    Detects bullish and bearish Order Blocks (OB).
    A bullish order block is a bearish candle before an uptrend.
    A bearish order block is a bullish candle before a downtrend.
    """
    df["Bullish_OB"] = (df["close"].shift(1) < df["open"].shift(1)) & (df["close"] > df["open"])
    df["Bearish_OB"] = (df["close"].shift(1) > df["open"].shift(1)) & (df["close"] < df["open"])
    return df

def generate_trade_signals(df, symbol, session):
    """
    Generates buy/sell signals based on FVGs and Order Blocks.
    """
    df = detect_fvg(df)
    df = detect_order_blocks(df)

    # Buy Signal: When price taps into a bullish OB & Bullish FVG exists
    df["Buy_Signal"] = (df["Bullish_OB"]) & (df["FVG_Up"])

    # Sell Signal: When price taps into a bearish OB & Bearish FVG exists
    df["Sell_Signal"] = (df["Bearish_OB"]) & (df["FVG_Down"])

    # Filter for latest signal
    latest_signal = df.iloc[-1]

    if latest_signal["Buy_Signal"]:
        print(f"BUY Signal detected on {symbol} in {session} session")
    elif latest_signal["Sell_Signal"]:
        print(f"SELL Signal detected on {symbol} in {session} session")

    # Save signals to CSV
    df.to_csv(f"backend/signals/{symbol}_{session}_signals.csv", index=False)


def main():
    conn()


if __name__ == "__main__":
    main()
