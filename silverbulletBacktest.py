import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

class TradingSimulator:
    def __init__(self, symbol: str, timeframe: int, days_back: int = 150):
        self.symbol = symbol
        self.timeframe = timeframe
        self.days_back = days_back
        self.df = self._get_historical_data()
        
    def _get_historical_data(self) -> pd.DataFrame:
        """Fetch historical data from MT5."""
        if not mt5.initialize():
            print("Initialize() failed", mt5.last_error())
            exit(0)
            
        start_date = datetime.now() - timedelta(days=self.days_back)
        end_date = datetime.now()

        print(f"The symbol {self.symbol}")

        print(f"The timeframe {self.timeframe}")
        print(f"The start_date {start_date}")
        print(f"The end_date {end_date}")

        rates = mt5.copy_rates_range(self.symbol, self.timeframe, start_date, end_date)
        df = pd.DataFrame(rates)
        print(f"The df {df}")
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df.set_index('time', inplace=True)
        return df
    
    @staticmethod
    def detect_fvg(df: pd.DataFrame) -> List[Tuple[pd.Timestamp, str]]:
        """Detect Fair Value Gaps in price data."""
        fvg = []
        for i in range(2, len(df)):
            prev_low = df.iloc[i - 2]['low']
            prev_high = df.iloc[i - 2]['high']
            
            if prev_low > df.iloc[i - 1]['high']:
                fvg.append((df.index[i], "bullish"))
            elif prev_high < df.iloc[i - 1]['low']:
                fvg.append((df.index[i], "bearish"))
        return fvg
    
    @staticmethod
    def detect_liquidity_sweeps(df: pd.DataFrame, lookback: int = 20) -> List[Tuple[pd.Timestamp, str]]:
        """Detect liquidity sweeps (false breakouts)."""
        sweeps = []
        for i in range(lookback, len(df)):
            current_high = df.iloc[i]['high']
            current_low = df.iloc[i]['low']
            
            # Check for bullish sweep (false breakout above resistance)
            if current_high > df.iloc[i-lookback:i]['high'].max() and df.iloc[i]['close'] < df.iloc[i]['open']:
                sweeps.append((df.index[i], "bullish"))
            
            # Check for bearish sweep (false breakout below support)
            elif current_low < df.iloc[i-lookback:i]['low'].min() and df.iloc[i]['close'] > df.iloc[i]['open']:
                sweeps.append((df.index[i], "bearish"))
                
        return sweeps
    
    @staticmethod
    def in_london_newyork_window(ts: pd.Timestamp) -> bool:
        """Check if time is in London/NY overlap or NY session."""
        hour = ts.hour
        return (8 <= hour < 12) or (13 <= hour < 17)  # London/NY overlap + NY session
    
    def simulate_trades(self, use_fvg: bool = True, use_sweeps: bool = True, rr_ratio: float = 2.0) -> pd.DataFrame:
        """Simulate trades based on selected strategies."""
        trades = []
        
        if use_fvg:
            fvg_signals = self.detect_fvg(self.df)
            trades += self._process_signals(fvg_signals, "FVG", rr_ratio)
            
        if use_sweeps:
            sweep_signals = self.detect_liquidity_sweeps(self.df)
            trades += self._process_signals(sweep_signals, "SWEEP", rr_ratio)
            
        return pd.DataFrame(trades).sort_values('time')
    
    def _process_signals(self, signals: List[Tuple[pd.Timestamp, str]], signal_type: str, rr_ratio: float) -> List[Dict]:
        """Process trading signals and simulate trades."""
        trades = []
        
        for signal_time, signal_type in signals:
            if not self.in_london_newyork_window(signal_time):
                continue
                
            entry_idx = self.df.index.get_loc(signal_time)
            if entry_idx + 3 >= len(self.df):
                continue
                
            # Entry logic
            entry_candle = self.df.iloc[entry_idx]
            entry_price = entry_candle['close']
            
            # Risk management
            atr = self._calculate_atr(entry_idx)
            sl_distance = 0.2
            tp_distance = sl_distance * rr_ratio
            
            if signal_type == "bullish":
                sl = entry_price - sl_distance
                tp = entry_price + tp_distance
            else:
                sl = entry_price + sl_distance
                tp = entry_price - tp_distance
                
            # Check if price fills the entry zone (next 3 candles)
            future = self.df.iloc[entry_idx + 1:entry_idx + 4]
            prices = future['low'] if signal_type == "bullish" else future['high']
            
            if ((signal_type == "bullish" and prices.min() <= entry_price) or 
                (signal_type == "bearish" and prices.max() >= entry_price)):
                
                # Track trade outcome (12 candle duration max)
                result = None
                exit_time = None
                exit_price = None
                pnl = 0
                
                for i in range(entry_idx + 1, min(entry_idx + 12, len(self.df))):
                    current_candle = self.df.iloc[i]
                    high, low = current_candle['high'], current_candle['low']
                    
                    if signal_type == "bullish":
                        if high >= tp:
                            result = "win"
                            exit_price = tp
                            break
                        elif low <= sl:
                            result = "loss"
                            exit_price = sl
                            break
                    else:
                        if low <= tp:
                            result = "win"
                            exit_price = tp
                            break
                        elif high >= sl:
                            result = "loss"
                            exit_price = sl
                            break
                
                if result:
                    pnl = (exit_price - entry_price) * (1 if signal_type == "bullish" else -1)
                    trades.append({
                        "time": signal_time,
                        "type": f"{signal_type}_{signal_type}",
                        "entry": entry_price,
                        "sl": sl,
                        "tp": tp,
                        "exit_price": exit_price,
                        "exit_time": self.df.index[i] if result else None,
                        "result": result,
                        "rr_ratio": rr_ratio,
                        "pnl": pnl,
                        "atr": atr
                    })
                    
        return trades
    
    def _calculate_atr(self, idx: int, period: int = 14) -> float:
        """Calculate Average True Range at given index."""
        start_idx = max(0, idx - period)
        df_slice = self.df.iloc[start_idx:idx+1].copy()
        
        df_slice['prev_close'] = df_slice['close'].shift(1)
        df_slice['tr1'] = df_slice['high'] - df_slice['low']
        df_slice['tr2'] = abs(df_slice['high'] - df_slice['prev_close'])
        df_slice['tr3'] = abs(df_slice['low'] - df_slice['prev_close'])
        df_slice['tr'] = df_slice[['tr1', 'tr2', 'tr3']].max(axis=1)
        
        return df_slice['tr'].mean()
    
    def generate_report(self, trades_df: pd.DataFrame) -> Dict:
        """Generate performance report from trades."""
        if trades_df.empty:
            return {}
            
        wins = trades_df[trades_df['result'] == 'win']
        losses = trades_df[trades_df['result'] == 'loss']
        
        total_trades = len(trades_df)
        win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0
        avg_win = wins['pnl'].mean() if not wins.empty else 0
        avg_loss = losses['pnl'].mean() if not losses.empty else 0
        profit_factor = abs(wins['pnl'].sum() / losses['pnl'].sum()) if not losses.empty else float('inf')
        
        cumulative_pnl = trades_df['pnl'].cumsum()
        max_drawdown = (cumulative_pnl.cummax() - cumulative_pnl).max()
        
        return {
            "total_trades": total_trades,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "net_pnl": trades_df['pnl'].sum(),
            "max_drawdown": max_drawdown,
            "avg_rr": trades_df['rr_ratio'].mean(),
            "best_trade": trades_df['pnl'].max(),
            "worst_trade": trades_df['pnl'].min()
        }


if __name__ == "__main__":
    # Initialize simulator with M1 data for precision
    simulator = TradingSimulator(symbol="XAUUSD", timeframe=mt5.TIMEFRAME_M1, days_back=30)
    
    # Simulate trades with both FVG and liquidity sweep strategies
    trades_df = simulator.simulate_trades(use_fvg=True, use_sweeps=True, rr_ratio=2.0)
    
    # Generate performance report
    report = simulator.generate_report(trades_df)
    
    # Print results
    print("\n=== Performance Report ===")
    for k, v in report.items():
        print(f"{k.replace('_', ' ').title()}: {v:.2f}" if isinstance(v, float) else f"{k.replace('_', ' ').title()}: {v}")
    
    print("\n=== Last 5 Trades ===")
    print(trades_df.tail())
    
    # Shutdown MT5 connection
    mt5.shutdown()