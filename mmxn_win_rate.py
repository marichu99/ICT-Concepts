import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta, time as dtime
import pytz
import os

# === CONFIGURATION ===
SYMBOL = "XAUUSD"
TIMEZONE = pytz.timezone("UTC")
H4 = mt5.TIMEFRAME_H4
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

# === Determine market bias using H4 timeframe ===
def determine_bias(h4_df):
    h4_df['bias'] = h4_df['close'].diff().apply(lambda x: 'bullish' if x > 0 else 'bearish')
    return h4_df[['time', 'bias']]

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
def simulate_trades(signals, m15_df, symbol):
    trades = []

    for i in range(len(signals) - 1):
        entry = signals.iloc[i]
        exit = signals.iloc[i + 1]
        session_data = m15_df[(m15_df['time'] > entry['time']) & (m15_df['time'] <= exit['time'])]

        if session_data.empty:
            continue

        prices = session_data['low'] if entry['action'] == 'buy' else session_data['high']
        drawdown = (prices.min() - entry['entry']) if entry['action'] == 'buy' else (entry['entry'] - prices.max())
        pnl = (exit['entry'] - entry['entry']) if entry['action'] == 'buy' else (entry['entry'] - exit['entry'])

        trades.append({
            'Time': entry['time'],
            'Symbol': symbol,
            'Timeframe': '15min',
            'Action': entry['action'],
            'Entry Price': round(entry['entry'], 2),
            'Exit Price': round(exit['entry'], 2),
            'PnL': round(pnl, 2),
            'Drawdown': round(drawdown, 2),
            'Bias': entry['bias']
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
    h4_data = fetch_data(SYMBOL, H4, START_DATE, END_DATE)
    m15_data = fetch_data(SYMBOL, M15, START_DATE, END_DATE)

    bias_data = determine_bias(h4_data)
    signals = generate_signals(m15_data, bias_data)
    trade_log = simulate_trades(signals, m15_data, SYMBOL)
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
