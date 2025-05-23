import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import os
from tradenotifier import send_email_notification
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import time

# Define the trading sessions for Silver Bullet in UTC
SILVER_BULLET_SESSIONS = {
    "London_NewYork_Overlap_Morning": {"start": (4, 0), "end": (8, 0)},  # NY Time: 4AM - 8AM
    "New_York_Session_Early": {"start": (9, 0), "end": (13, 0)}          # NY Time: 9AM - 1PM
}


# Convert to EAT (assuming NY is UTC-5)
def convert_to_eat(session):
    # From NY (UTC-5) to EAT (UTC+3) = +8 hours difference
    start_eat = ((session["start"][0] + 7) % 24, session["start"][1])
    end_eat = ((session["end"][0] + 7) % 24, session["end"][1])
    return {"start": start_eat, "end": end_eat}


SILVER_BULLET_SESSIONS_UTC = {key: convert_to_eat(val) for key, val in SILVER_BULLET_SESSIONS.items()}

# Define the number of bars to fetch
NUM_BARS = 100

# get the passwords
APP_PASSWORD = os.getenv("APP_PASSWORD")
APP_PASSWORD = os.getenv("APP_PASSWORD")

# Trading parameters
LOT_SIZE = 0.1
RR_RATIO = 2.0  # Risk:Reward ratio
MAX_SLIPPAGE = 3  # Max allowed slippage in points

def conn():
    """Initialize MT5 connection and login"""
    resu = {
        "Response": 200,
        "Message": "Data from python"
    }
    valid_conn = mt5.initialize()
    if not valid_conn:
        resu["init_err"] = mt5.last_error()
        raise ConnectionError(f"MT5 initialization failed: {mt5.last_error()}")
    
    login = mt5.login(36610, APP_PASSWORD, "EGMSecurities-Demo")
    if not login:
        resu["login_err"] = mt5.last_error()
        raise ConnectionError(f"MT5 login failed: {mt5.last_error()}")
    
    resu["Message"] = "Login is Successful"
    print("MT5 connection established successfully")
    gatherDataController()

def execute_trade(symbol: str, signal_type: str, price: float, sl: float, tp: float):
    """Execute a trade in MT5"""
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        print(f"{symbol} not found")
        return None
    
    if not symbol_info.visible:
        print(f"{symbol} is not visible, trying to switch on")
        if not mt5.symbol_select(symbol, True):
            print(f"symbol_select({symbol}) failed")
            return None
    
    point = symbol_info.point
    deviation = MAX_SLIPPAGE
    
    if signal_type == "buy":
        order_type = mt5.ORDER_BUY
        sl_price = price - sl * point
        tp_price = price + tp * point
    elif signal_type == "sell":
        order_type = mt5.ORDER_SELL
        sl_price = price + sl * point
        tp_price = price - tp * point
    else:
        return None
    
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": LOT_SIZE,
        "type": order_type,
        "price": price,
        "sl": sl_price,
        "tp": tp_price,
        "deviation": deviation,
        "magic": 123456,
        "comment": "SilverBulletICT",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    
    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"Order failed, retcode={result.retcode}")
        return None
    else:
        signal_message=f" We have made a {signal_type} signal for the {symbol} at {price} with a TP of {tp} and a SL of {sl}"
        # send the email with signal notice
        send_email_notification(f"{symbol} TRADE ACTION",signal_message)
    
    print(f"Trade executed: {symbol} {signal_type} at {price}")
    return result

def calculate_position_size(symbol: str, risk_pct: float = 1.0, sl_points: float = 100) -> float:
    """Calculate position size based on account balance and risk percentage"""
    account_info = mt5.account_info()
    if account_info is None:
        print("Failed to get account info")
        return LOT_SIZE  # default
    
    balance = account_info.balance
    risk_amount = balance * (risk_pct / 100)
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        return LOT_SIZE
    
    point_value = symbol_info.trade_tick_value * symbol_info.trade_tick_size
    position_size = risk_amount / (sl_points * point_value)
    
    # Round to nearest 0.01 lot
    return round(position_size * 100) / 100

def detect_fvg(df):
    """
    Detects Fair Value Gaps (FVG) in price action.
    Returns list of tuples with (timestamp, direction) for each FVG.
    """
    fvg_signals = []
    for i in range(2, len(df)):
        prev_low = df.iloc[i - 2]['low']
        prev_high = df.iloc[i - 2]['high']
        
        if prev_low > df.iloc[i - 1]['high']:
            print("The fvg bullish signal has been found")
            exit(0)
            fvg_signals.append((df.index[i], "bullish"))
        elif prev_high < df.iloc[i - 1]['low']:
            print("The fvg bearish signal has been found")
            exit(0)
            fvg_signals.append((df.index[i], "bearish"))
    
    # Also add the column-based detection for the original signal generation
    df["FVG_Up"] = (df["low"].shift(2) > df["high"].shift(1))  # Bullish FVG
    df["FVG_Down"] = (df["high"].shift(2) < df["low"].shift(1))  # Bearish FVG
    
    return fvg_signals

def detect_order_blocks(df):
    print("we are detecting order blocks ......")
    """
    Detects bullish and bearish Order Blocks (OB).
    A bullish order block is a bearish candle before an uptrend.
    A bearish order block is a bullish candle before a downtrend.
    """
    df["Bullish_OB"] = (df["close"].shift(1) < df["open"].shift(1)) & (df["close"] > df["open"])
    df["Bearish_OB"] = (df["close"].shift(1) > df["open"].shift(1)) & (df["close"] < df["open"])
    print("we are done detecting order blocks ")
    return df

def  process_signals_for_execution(df, symbol, session_name):
    """Process signals and execute trades"""
    df = detect_order_blocks(df)
    fvg_signals = detect_fvg(df)
    
    # Get current price for execution
    current_price = mt5.symbol_info_tick(symbol).ask
    
    # Process FVG signals
    for signal_time, signal_dir in fvg_signals:
        if signal_time == df.index[-1]:  # Only act on most recent signal
            if signal_dir == "bullish":
                sl_points = (current_price - df.iloc[-1]['low']) / mt5.symbol_info(symbol).point
                tp_points = sl_points * RR_RATIO
                execute_trade(symbol, "buy", current_price, sl_points, tp_points)
            else:
                sl_points = (df.iloc[-1]['high'] - current_price) / mt5.symbol_info(symbol).point
                tp_points = sl_points * RR_RATIO
                execute_trade(symbol, "sell", current_price, sl_points, tp_points)
    
    # Process Order Block signals
    latest = df.iloc[-1]
    if latest["Buy_Signal"]:
        sl_points = (current_price - df.iloc[-1]['low']) / mt5.symbol_info(symbol).point
        tp_points = sl_points * RR_RATIO
        execute_trade(symbol, "buy", current_price, sl_points, tp_points)
    elif latest["Sell_Signal"]:
        sl_points = (df.iloc[-1]['high'] - current_price) / mt5.symbol_info(symbol).point
        tp_points = sl_points * RR_RATIO
        execute_trade(symbol, "sell", current_price, sl_points, tp_points)

def generate_trade_signals(df, symbol, session):
    """
    Generates buy/sell signals based on FVGs and Order Blocks.
    Returns the enhanced DataFrame with signals.
    """
    df = detect_order_blocks(df)
    detect_fvg(df)  # This populates both the list and columns

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
    # Ensure directory exists
    output_dir = "/backend/signals/silver-bullet"
    os.makedirs(output_dir, exist_ok=True)
    df.to_csv(f"{output_dir}/{symbol}_{session}_signals.csv", index=False)
    return df

def gatherDataController():
    print("Gathering data for Silver Bullet ICT strategy")
    
    # Define symbols and timeframes
    SYMBOLS = ["UT100Roll", "US500Roll", "XAUUSD"]  # Example assets
    TIMEFRAME = mt5.TIMEFRAME_M5  # 5-minute timeframe for ICT      

    while True:
        for pair in SYMBOLS:
            # Get the current UTC time
            now = datetime.now()

            # Check if the current time is within any of the Silver Bullet sessions
            for session_name, session_times in SILVER_BULLET_SESSIONS_UTC.items():
                start_hour, start_minute = session_times["start"]
                end_hour, end_minute = session_times["end"]
                
                session_start = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
                session_end = now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
                print(f"The session start {session_start}")
                print(f"The session stop {session_end}")
                print(f"The session name {session_name}")
                print(f"The session now {now}")
                print(f"Session within {session_start <= now <= session_end}")
                

                if session_start <= now <= session_end:
                    print(f"Currently within {session_name} session for {pair}")
                    
                    # Fetch historical data
                    backtest_data = mt5.copy_rates_from_pos(pair, TIMEFRAME, 1, NUM_BARS)
                    if backtest_data is None or len(backtest_data) == 0:
                        print(f"No data retrieved for {pair}")
                        continue
                    
                    bars = pd.DataFrame(backtest_data)
                    bars["time"] = pd.to_datetime(bars["time"], unit="s")
                    bars.set_index('time', inplace=True)

                    # Ensure directory exists
                    output_dir = "/backend/backtest/silver-bullet/"
                    os.makedirs(output_dir, exist_ok=True)

                    # Then save the CSV
                    filename = f"{output_dir}/{pair}_{session_name}.csv"
                    bars.to_csv(filename, index=False)
                    # Generate and process signals
                    signals_df = generate_trade_signals(bars, pair, session_name)
                    
                    # Execute trades based on signals
                    process_signals_for_execution(bars, pair, session_name)
                    
                    # Small delay to avoid spamming
                    time.sleep(1000)

def main():
    conn()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nScript terminated by user")
    finally:
        mt5.shutdown()
        print("MT5 connection closed")