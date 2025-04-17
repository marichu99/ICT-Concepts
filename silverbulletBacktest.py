import os
import pandas as pd

SIGNALS_DIR = "backend/signals"
BACKTEST_DIR = "backend/backtest"

# Define pips per instrument for win/loss calculation
PIP_THRESHOLDS = {
    "EURUSD": 0.0010,
    "GBPUSD": 0.0010,
    "XAUUSD": 1.0
}

def calculate_win_rate(symbol, session):
    signal_file = f"{SIGNALS_DIR}/{symbol}_{session}_signals.csv"
    backtest_file = f"{BACKTEST_DIR}/{symbol}_{session}.csv"

    if not os.path.exists(signal_file) or not os.path.exists(backtest_file):
        print(f"Missing data for {symbol} ({session})")
        return None

    df_signal = pd.read_csv(signal_file)
    df_bt = pd.read_csv(backtest_file)

    pip_threshold = PIP_THRESHOLDS.get(symbol, 0.0010)
    wins = 0
    losses = 0

    for i in range(len(df_signal) - 1):
        current = df_signal.iloc[i]
        next_candle = df_signal.iloc[i + 1]

        if current["Buy_Signal"]:
            entry_price = current["close"]
            high = next_candle["high"]
            result = "win" if high - entry_price >= pip_threshold else "loss"
        elif current["Sell_Signal"]:
            entry_price = current["close"]
            low = next_candle["low"]
            result = "win" if entry_price - low >= pip_threshold else "loss"
        else:
            continue

        if result == "win":
            wins += 1
        else:
            losses += 1

    total = wins + losses
    win_rate = (wins / total) * 100 if total > 0 else 0

    print(f"{symbol} ({session}) — Win Rate: {win_rate:.2f}% ({wins} wins / {total} trades)")
    return symbol, session, win_rate, wins, losses

def main():
    results = []
    for filename in os.listdir(SIGNALS_DIR):
        if filename.endswith("_signals.csv"):
            parts = filename.replace("_signals.csv", "").split("_")
            symbol = parts[0]
            session = "_".join(parts[1:])
            result = calculate_win_rate(symbol, session)
            if result:
                results.append(result)

    if results:
        df = pd.DataFrame(results, columns=["Symbol", "Session", "Win Rate (%)", "Wins", "Losses"])
        df.to_csv("backend/win_rate_summary.csv", index=False)
        print("\n✅ Win rate summary saved to backend/win_rate_summary.csv")

if __name__ == "__main__":
    main()
