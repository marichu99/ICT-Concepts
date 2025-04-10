from datetime import datetime, time as dtime
import pandas as pd
from tradenotifier import send_email_notification
import MetaTrader5 as mt5
import os

from time import time

# === CONFIGURATION ===
SYMBOLS = ["EURUSD", "GBPUSD"]
HIGHER_TF = mt5.TIMEFRAME_H1
MID_TF = mt5.TIMEFRAME_M30
LOWER_TF = mt5.TIMEFRAME_M15
APP_PASSWORD = os.getenv("APP_PASSWORD")


HIGHER_BARS = 100
MID_BARS = 150
LOWER_BARS = 200

VOLUME = 0.1
BACKTEST_DIR = "./backend/backtest"
SIGNAL_LOG = "./signals.csv"

# === TIME SESSION SETUP ===
TRADING_SESSION_START = dtime(7, 0)
TRADING_SESSION_END = dtime(17, 0)

# === SETUP ===
if not mt5.initialize():
    raise RuntimeError("MT5 initialization failed")
os.makedirs(BACKTEST_DIR, exist_ok=True)

# === UTILITY FUNCTIONS ===
def fetch_data(symbol, timeframe, bars):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    return df

def is_in_session(current_time):
    now_time = current_time.time()
    return TRADING_SESSION_START <= now_time <= TRADING_SESSION_END

def get_bias(df):
    last_candle = df.iloc[-1]
    return "bullish" if last_candle['close'] > last_candle['open'] else "bearish"

def identify_consolidation(df, threshold=0.002):
    df['range'] = df['high'] - df['low']
    return df[df['range'] < threshold]

def detect_fakeout(consolidation_df, full_df):
    if consolidation_df.empty:
        return False
    high = consolidation_df['high'].max()
    low = consolidation_df['low'].min()
    post_consolidation = full_df[full_df['time'] > consolidation_df['time'].max()]
    spike = post_consolidation[(post_consolidation['high'] > high) | (post_consolidation['low'] < low)]
    return not spike.empty

def is_smt_divergence(df1, df2):
    highs1 = df1['high'].tail(3).values
    highs2 = df2['high'].tail(3).values
    return (highs1[-1] > highs1[-2] > highs1[-3]) and not (highs2[-1] > highs2[-2])

def log_signal(pair, direction, reason):
    with open(SIGNAL_LOG, "a") as f:
        f.write(f"{datetime.now()},{pair},{direction},{reason}\n")

def place_order(symbol, order_type="buy", volume=0.1):
    tick = mt5.symbol_info_tick(symbol)
    price = tick.ask if order_type == "buy" else tick.bid

    signal_message = f"trade execution for order type {order_type} at price {price}"
    
    send_email_notification(f"{symbol} ",signal_message)
    order = mt5.order_send({
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": mt5.ORDER_TYPE_BUY if order_type == "buy" else mt5.ORDER_TYPE_SELL,
        "price": price,
        "deviation": 10,
        "magic": 234000,
        "comment": "PO3+SMT MTF Bias",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    })
    print(f"Trade result for {symbol}: {order}")

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
        execute_mtf_po3_smt_strategy()


# === STRATEGY LOGIC ===
def execute_mtf_po3_smt_strategy():
    while True:
        try:
        
            now = datetime.now()
            if not is_in_session(now):
                print("Outside trading session")
                continue

            for symbol in SYMBOLS:
                # --- Fetch all TF data ---
                df_higher = fetch_data(symbol, HIGHER_TF, HIGHER_BARS)
                df_mid = fetch_data(symbol, MID_TF, MID_BARS)
                df_lower = fetch_data(symbol, LOWER_TF, LOWER_BARS)

                # --- Save for backtesting ---
                df_lower.to_csv(f"{BACKTEST_DIR}/{symbol}_{LOWER_TF}.csv", index=False)
                df_mid.to_csv(f"{BACKTEST_DIR}/{symbol}_{MID_TF}.csv", index=False)
                df_higher.to_csv(f"{BACKTEST_DIR}/{symbol}_{HIGHER_TF}.csv", index=False)

                # --- Multi-TF Bias Check ---
                bias_high = get_bias(df_higher)
                bias_mid = get_bias(df_mid)

                if bias_high != bias_mid:
                    print(f"Bias mismatch on {symbol} - skipping")
                    continue

                # --- Consolidation + Fakeout ---
                accumulation = identify_consolidation(df_lower)
                if not detect_fakeout(accumulation, df_lower):
                    continue

                # --- SMT Divergence Check ---
                other_symbol = "GBPUSD" if symbol == "EURUSD" else "EURUSD"
                df_other = fetch_data(other_symbol, LOWER_TF, LOWER_BARS)
                if not is_smt_divergence(df_lower, df_other):
                    continue

                # --- Signal Trigger ---
                direction = "buy" if bias_high == "bullish" else "sell"
                log_signal(symbol, direction, f"MTF Bias + PO3 + SMT Divergence")
                place_order(symbol, direction)
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(60 * 15)  # Run every 15 minutes

def main():
    conn()


if __name__ == "__main__":
    main()