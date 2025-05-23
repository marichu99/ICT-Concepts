import pandas as pd
from datetime import datetime

import os

# Gets files in current directory that start with "trades_"
files = [f for f in os.listdir() if f.startswith("trades_") and os.path.isfile(f)]

for f in files:
    # Load the data
    df = pd.read_csv(f, parse_dates=['signal_time'])

    # Extract date from 'signal_time'
    df['date'] = df['signal_time'].dt.date

    # Group by date and calculate max drawdown per day
    def calculate_max_drawdown(group):
        balances = group['balance_after_trade'].tolist()
        peak = balances[0]
        max_drawdown = 0
        for balance in balances:
            if balance > peak:
                peak = balance
            drawdown = (peak - balance) / peak
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        return max_drawdown * 100  # as percentage

    max_drawdown_per_day = df.groupby('date').apply(calculate_max_drawdown)
    print(f"The max drawown per day for {f} is {max_drawdown_per_day}")