import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta, time as dtime
import pytz
import os

# === CONFIGURATION ===
SYMBOL = "XAUUSD"
TIMEZONE = pytz.timezone("UTC")
H4 = mt5.TIMEFRAME_H4
M5 = mt5.TIMEFRAME_M5
D1 = mt5.TIMEFRAME_D1
H1 = mt5.TIMEFRAME_H1
M15 = mt5.TIMEFRAME_M15
START_DATE = datetime.now() - timedelta(days=150)
END_DATE = datetime.now()
LOG_FILE = "mmxn_backtest_log.csv"

# === Initialize MT5 ===
def initialize_mt5():
    if not mt5.initialize():
        raise RuntimeError(f"MT5 Initialization failed: {mt5.last_error()}")

# === Fetch historical data ===
def fetch_data(symbol, timeframe, start, end):
    rates = mt5.copy_rates_range(symbol, timeframe, start, end)
    if rates is None:
        raise RuntimeError(f"Failed to fetch data for {symbol} at timeframe {timeframe}")
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s').dt.tz_localize('UTC').dt.tz_convert('US/Eastern')
    return df

import pandas as pd

# === Detect Equal Highs/Lows (Liquidity Pools) ===
def detect_equal_highs(df, window=7, threshold=0.8):
    equal_highs = []
    for i in range(len(df)):
        if i < window:
            equal_highs.append(False)
            continue
        window_highs = df['high'].iloc[i-window:i]
        equal = window_highs.max() - window_highs.min() < threshold
        equal_highs.append(equal)
    return pd.Series(equal_highs)

def detect_equal_lows(df, window=7, threshold=0.8):
    equal_lows = []
    for i in range(len(df)):
        if i < window:
            equal_lows.append(False)
            continue
        window_lows = df['low'].iloc[i-window:i]
        equal = window_lows.max() - window_lows.min() < threshold
        equal_lows.append(equal)
    return pd.Series(equal_lows)

# === Detect Liquidity Sweeps ===
def detect_liquidity_sweep(df, i, lookback=5, buffer=0.2):
    if i < lookback:
        return None

    highs = df['high'].iloc[i-lookback:i]
    lows = df['low'].iloc[i-lookback:i]
    curr = df.iloc[i]

    max_high = highs.max()
    min_low = lows.min()

    # Bullish sweep: breaks below recent lows, closes back above
    if curr['low'] < min_low - buffer and curr['close'] > min_low:
        return 'bullish_sweep'
    # Bearish sweep: breaks above recent highs, closes back below
    elif curr['high'] > max_high + buffer and curr['close'] < max_high:
        return 'bearish_sweep'
    else:
        return None

# === Detect FVGs ===
def detect_fvgs(df):
    fvgs = []
    for i in range(2, len(df)):
        prev = df.iloc[i - 2]
        curr = df.iloc[i]
        if curr['low'] > prev['high']:  # Bullish FVG
            fvgs.append({'time': df.iloc[i]['time'], 'direction': 'bullish', 'low': prev['high'], 'high': curr['low']})
        elif curr['high'] < prev['low']:  # Bearish FVG
            fvgs.append({'time': df.iloc[i]['time'], 'direction': 'bearish', 'low': curr['high'], 'high': prev['low']})
    return pd.DataFrame(fvgs)

# === Final Bias Function with Liquidity Sweeps ===
def determine_bias(h4_df):
    fvgs = detect_fvgs(h4_df)
    h4_df = h4_df.copy()

    # Add liquidity pool detection
    h4_df['equal_highs'] = detect_equal_highs(h4_df)
    h4_df['equal_lows'] = detect_equal_lows(h4_df)

    bias_rows = []

    for i in range(len(h4_df)):
        row = h4_df.iloc[i]
        time = row['time']
        close = row['close']
        high = row['high']
        low = row['low']

        recent_fvg = fvgs[fvgs['time'] < time].tail(5)
        bullish_fvg = recent_fvg[recent_fvg['direction'] == 'bullish']
        bearish_fvg = recent_fvg[recent_fvg['direction'] == 'bearish']

        # Liquidity Pools
        liquidity_above = h4_df['equal_highs'].iloc[max(0, i-5):i].any()
        liquidity_below = h4_df['equal_lows'].iloc[max(0, i-5):i].any()

        # Liquidity Sweeps
        sweep = detect_liquidity_sweep(h4_df, i)

        # Bias Logic
        bias = None
        reason = ""

        if sweep == 'bullish_sweep':
            bias = 'bullish'
            reason = 'bullish_sweep'
        elif sweep == 'bearish_sweep':
            bias = 'bearish'
            reason = 'bearish_sweep'
        elif liquidity_above and not liquidity_below:
            bias = 'bullish'
            reason = 'liquidity_above'
        elif liquidity_below and not liquidity_above:
            bias = 'bearish'
            reason = 'liquidity_below'
        elif not bullish_fvg.empty and close > bullish_fvg.iloc[-1]['high']:
            bias = 'bullish'
            reason = 'fvg_breach_up'
        elif not bearish_fvg.empty and close < bearish_fvg.iloc[-1]['low']:
            bias = 'bearish'
            reason = 'fvg_breach_down'
        else:
            bias = 'bullish' if close > row['open'] else 'bearish'
            reason = 'candle_body'

        bias_rows.append({'time': time, 'bias': bias, 'reason': reason})

    return pd.DataFrame(bias_rows)


# === Check if time is within desired trading sessions ===
def is_in_session(dt):
    t = dt.time()
    ny_start = dtime(8, 0)
    ny_end = dtime(17, 0)
    london_start = dtime(3, 0)
    london_end = dtime(12, 0)
    return (london_start <= t <= london_end) or (ny_start <= t <= ny_end)

# === Detect Fair Value Gaps (FVGs) ===
def detect_fvgs(df):
    signals = []
    for i in range(2, len(df)):
        c1 = df.iloc[i - 2]
        c3 = df.iloc[i]

        # Bullish FVG
        if c3['low'] > c1['high']:
            mid = (c3['low'] + c1['high']) / 2
            signals.append({
                'time': c3['time'],
                'direction': 'bullish',
                'high': c3['low'],
                'low': c1['high'],
                'mid': mid
            })

        # Bearish FVG
        if c3['high'] < c1['low']:
            mid = (c3['high'] + c1['low']) / 2
            signals.append({
                'time': c3['time'],
                'direction': 'bearish',
                'high': c1['low'],
                'low': c3['high'],
                'mid': mid
            })
    return pd.DataFrame(signals)

# === Generate valid trade entries ===
def generate_signals(m15_df, bias_df):
    fvg_df = detect_fvgs(m15_df)
    signals = []

    for _, fvg in fvg_df.iterrows():
        entry_time = fvg['time']
        if not is_in_session(entry_time):
            continue

        matching_bias = bias_df[bias_df['time'] <= entry_time]
        if matching_bias.empty:
            continue
        bias = matching_bias.iloc[-1]['bias']

        if fvg['direction'] != bias:
            continue

        # Simulate return to FVG zone (entry condition)
        future_bars = m15_df[m15_df['time'] > entry_time].head(12)
        for _, bar in future_bars.iterrows():
            if fvg['low'] <= bar['low'] <= fvg['high']:  # return to FVG zone
                signals.append({
                    'time': bar['time'],
                    'bias': bias,
                    'action': 'buy' if bias == 'bullish' else 'sell',
                    'entry': round(fvg['mid'], 2)
                })
                break
    return pd.DataFrame(signals)

# === Simulate trades ===
def simulate_trades_with_dynamic_sl_tp(signals, m15_df, symbol):
    trades = []

    for idx, entry in signals.iterrows():
        entry_time = entry['time']
        entry_price = entry['entry']
        action = entry['action']
        bias = entry['bias']

        # Get M15 candles before entry time
        past_candles = m15_df[m15_df['time'] < entry_time].tail(10)

        if past_candles.empty:
            continue

        # Determine SL from swing high/low
        if action == 'buy':
            sl = past_candles['low'].min() - 0.30  # 3 pips below lowest low
            tp = entry_price + 2 * (entry_price - sl)
        else:  # sell
            sl = past_candles['high'].max() + 0.30  # 3 pips above highest high
            tp = entry_price - 2 * (sl - entry_price)

        # Monitor price after entry
        future_candles = m15_df[m15_df['time'] > entry_time]

        for _, row in future_candles.iterrows():
            high = row['high']
            low = row['low']

            if action == 'buy':
                if low <= sl:
                    outcome = 'Loss'
                    exit_price = sl
                    break
                elif high >= tp:
                    outcome = 'Win'
                    exit_price = tp
                    break
            else:  # sell
                if high >= sl:
                    outcome = 'Loss'
                    exit_price = sl
                    break
                elif low <= tp:
                    outcome = 'Win'
                    exit_price = tp
                    break
        else:
            continue  # No outcome met

        pnl = (exit_price - entry_price) if action == 'buy' else (entry_price - exit_price)
        rr = abs(pnl / abs(entry_price - sl))

        trades.append({
            'Time': entry_time,
            'Symbol': symbol,
            'Timeframe': '15min',
            'Bias': bias,
            'Action': action,
            'Entry Price': round(entry_price, 2),
            'SL': round(sl, 2),
            'TP': round(tp, 2),
            'Exit Price': round(exit_price, 2),
            'PnL': round(pnl, 2),
            'R:R': round(rr, 2),
            'Result': outcome
        })

    return pd.DataFrame(trades)

# === Calculate win rate ===
def calculate_win_rate(trades):
    wins = trades[trades['PnL'] > 0]
    return len(wins) / len(trades) * 100 if len(trades) else 0

# === Calculate win rate ===
def calculate_pnl(trades):
    wins = trades[trades['PnL'] > 0]
    losses = trades[trades['PnL'] < 0]

    profit= sum(wins["PnL"])
    losses = sum(losses["PnL"])
    return profit,losses

# === Main function ===
def main():
    initialize_mt5()
    h4_data = fetch_data(SYMBOL, H1, START_DATE, END_DATE)
    m15_data = fetch_data(SYMBOL, M5, START_DATE, END_DATE)

    bias_data = determine_bias(h4_data)
    signals = generate_signals(m15_data, bias_data)
    trade_log = simulate_trades_with_dynamic_sl_tp(signals, m15_data, SYMBOL)
    win_rate = calculate_win_rate(trade_log)

    profit,loss = calculate_pnl(trade_log)

    print(f"The Profit was: {profit} and the loss was {loss} over the last 5 months")

    print(f"\nWin Rate (last 5 months): {win_rate:.2f}%")
    print("\nTrade Breakdown:\n")
    #print(trade_log.to_string(index=False))

    trade_log.to_csv(LOG_FILE, index=False)
    mt5.shutdown()

if __name__ == "__main__":
    main()
