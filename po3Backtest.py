from datetime import datetime, timedelta
import pandas as pd
import MetaTrader5 as mt5
from po3 import get_bias,identify_consolidation,detect_fakeout,is_smt_divergence
import os
from collections import defaultdict

# Constants
SYMBOLS = ["EURUSD", "GBPUSD"]
HIGHER_TF = mt5.TIMEFRAME_H1
MID_TF = mt5.TIMEFRAME_M30
LOWER_TF = mt5.TIMEFRAME_M15

TAKE_PROFIT_PIPS = 30
STOP_LOSS_PIPS = 20

# Initialize MetaTrader
if not mt5.initialize():
    raise RuntimeError("MT5 initialization failed")

def simulate_trade(entry_price, direction, candles):
    if direction == "buy":
        tp_price = entry_price + TAKE_PROFIT_PIPS * 0.0001
        sl_price = entry_price - STOP_LOSS_PIPS * 0.0001
    else:
        tp_price = entry_price - TAKE_PROFIT_PIPS * 0.0001
        sl_price = entry_price + STOP_LOSS_PIPS * 0.0001

    for _, row in candles.iterrows():
        if direction == "buy":
            if row['low'] <= sl_price:
                return False
            if row['high'] >= tp_price:
                return True
        else:
            if row['high'] >= sl_price:
                return False
            if row['low'] <= tp_price:
                return True
    return False  # neither TP nor SL hit

def run_backtest():
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30 * 5)  # 5 months back
    current_time = start_date

    results = defaultdict(lambda: {'wins': 0, 'losses': 0})

    while current_time < end_date:
        for symbol in SYMBOLS:
            # Fetch historical data ending at current_time
            df_higher = pd.DataFrame(mt5.copy_rates_from(symbol, HIGHER_TF, current_time, 100))
            df_mid = pd.DataFrame(mt5.copy_rates_from(symbol, MID_TF, current_time, 150))
            df_lower = pd.DataFrame(mt5.copy_rates_from(symbol, LOWER_TF, current_time, 200))

            if df_higher.empty or df_mid.empty or df_lower.empty:
                continue

            df_higher['time'] = pd.to_datetime(df_higher['time'], unit='s')
            df_mid['time'] = pd.to_datetime(df_mid['time'], unit='s')
            df_lower['time'] = pd.to_datetime(df_lower['time'], unit='s')

            # Check biases
            bias_high = get_bias(df_higher)
            bias_mid = get_bias(df_mid)
            if bias_high != bias_mid:
                continue

            # Check PO3 + fakeout
            consolidation = identify_consolidation(df_lower)
            if not detect_fakeout(consolidation, df_lower):
                continue

            # SMT Divergence
            other_symbol = "GBPUSD" if symbol == "EURUSD" else "EURUSD"
            df_other = pd.DataFrame(mt5.copy_rates_from(other_symbol, LOWER_TF, current_time, 200))
            df_other['time'] = pd.to_datetime(df_other['time'], unit='s')
            if not is_smt_divergence(df_lower, df_other):
                continue

            # Simulate trade
            direction = "buy" if bias_high == "bullish" else "sell"
            entry_price = df_lower.iloc[-1]['close']
            future_candles = pd.DataFrame(mt5.copy_rates_from(symbol, LOWER_TF, current_time + timedelta(minutes=15), 10))
            future_candles['time'] = pd.to_datetime(future_candles['time'], unit='s')

            if future_candles.empty:
                continue

            trade_result = simulate_trade(entry_price, direction, future_candles)
            month_key = current_time.strftime("%Y-%m")
            if trade_result:
                results[month_key]['wins'] += 1
            else:
                results[month_key]['losses'] += 1

        current_time += timedelta(minutes=15)

    return results

def calculate_winrate(results):
    for month, data in results.items():
        total = data['wins'] + data['losses']
        if total == 0:
            winrate = 0
        else:
            winrate = (data['wins'] / total) * 100
        print(f"{month}: {data['wins']} wins / {total} trades => Win rate: {winrate:.2f}%")

# === Run it ===
if __name__ == "__main__":
    result_data = run_backtest()
    calculate_winrate(result_data)
