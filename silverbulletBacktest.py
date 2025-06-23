import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

class TradingSimulator:
    def __init__(self, symbol: str, timeframe: int, days_back: int = 150, account_balance: float = 10000, risk_per_trade: float = 0.01):
        self.symbol = symbol
        self.timeframe = timeframe
        self.days_back = days_back
        self.account_balance = account_balance
        self.risk_per_trade = risk_per_trade  # Risk 1% of account per trade
        self.point_value = self.get_point_value(symbol)  # Value per pip/point for the symbol
        self.df = self._get_historical_data()

    def get_point_value(self,symbol: str) -> float:
        info = mt5.symbol_info(symbol)
        if info and info.trade_tick_size > 0:
            return (info.trade_tick_value / info.trade_tick_size) * info.point
        else:
            # fallback to hardcoded map
            return self._get_point_value()

        
    def  _get_point_value(self) -> float:
        """Get the value per point for the symbol, including indices/futures."""
        point_values = {
            # Forex pairs
            'EURUSD': 0.0001,
            'GBPUSD': 0.0001,
            'USDJPY': 0.01,
            'AUDUSD': 0.0001,
            'USDCAD': 0.0001,
            # Metals
            'XAUUSD': 0.01,  # Gold (1 pip = $0.01 per ounce)
            'XAGUSD': 0.001, # Silver
            # Indices (futures/CFDs) - typical values
            'US500Roll': 0.1,    # SP500 (1 point = $0.1 per contract)
            'US30': 0.1,     # Dow Jones
            'UT100Roll': 0.1,   # NASDAQ100
            'UT100': 0.1,    # Alternative NASDAQ100 symbol
            'GER40': 0.1,    # DAX
            'UK100': 0.1,    # FTSE
        }
        
        # Check for known symbols
        for sym in point_values:
            if sym in self.symbol:
                return point_values[sym]
        
        # Default for unknown symbols
        if "JPY" in self.symbol:
            return 0.01  # JPY pairs
        return 0.0001    # Default for most forex
        
    def _calculate_lot_size(self, entry_price: float, stop_loss: float) -> float:
        """
        Calculate lot size based on account balance and risk percentage.
        
        Formula: Lots = (Account Balance * Risk %) / (Stop Loss in pips * Pip Value)
        """
        # Calculate stop loss distance in pips/points
        if stop_loss < entry_price:  # Long trade
            sl_pips = (entry_price - stop_loss) / self.point_value
        else:  # Short trade
            sl_pips = (stop_loss - entry_price) / self.point_value
            
        # Calculate dollar amount to risk
        risk_amount = self.account_balance * self.risk_per_trade
        
        # Calculate lot size (standard lot = 100,000 units)
        # For a standard lot, each pip is worth $10 for most pairs
        lot_size = round(risk_amount / (sl_pips * self.point_value * 100000), 2)
     
        # Ensure lot size is within reasonable bounds
        return max(0.01, min(lot_size, 50))  # Min 0.01 lot, max 50 lots
        
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
        

        pd.DataFrame(trades).sort_values('time').to_csv("SB.csv")
        return pd.DataFrame(trades).sort_values('time')
    
    def _process_signals(self, signals: List[Tuple[pd.Timestamp, str]], signal_type: str, rr_ratio: float) -> List[Dict]:
        """Process trading signals and simulate trades with lot size calculation."""
        trades = []
        
        for signal_time, signal_dir in signals:
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
            
            if signal_dir == "bullish":
                sl = entry_price - sl_distance
                tp = entry_price + tp_distance
                position_type = "buy"
            else:
                sl = entry_price + sl_distance
                tp = entry_price - tp_distance
                position_type = "sell"
                
            # Calculate lot size based on risk
            lot_size = self._calculate_lot_size(entry_price, sl)
            
            # Check if price fills the entry zone (next 3 candles)
            future = self.df.iloc[entry_idx + 1:entry_idx + 4]
            prices = future['low'] if signal_dir == "bullish" else future['high']
            
            if ((signal_dir == "bullish" and prices.min() <= entry_price) or 
                (signal_dir == "bearish" and prices.max() >= entry_price)):
                
                # Track trade outcome (12 candle duration max)
                result = None
                exit_time = None
                exit_price = None
                pnl = 0
                pnl_pips = 0
                
                for i in range(entry_idx + 1, min(entry_idx + 12, len(self.df))):
                    current_candle = self.df.iloc[i]
                    high, low = current_candle['high'], current_candle['low']
                    
                    if signal_dir == "bullish":
                        if low <= sl:
                            result = "loss"
                            exit_price = sl
                            pnl_pips = (sl - entry_price) / self.point_value
                            break
                        elif high >= tp:
                            result = "win"
                            exit_price = tp
                            pnl_pips = (tp - entry_price) / self.point_value
                            break
                        
                    else:
                        if high >= sl:
                            result = "loss"
                            exit_price = sl
                            pnl_pips = (entry_price - sl) / self.point_value
                            break
                        elif low <= tp:
                            result = "win"
                            exit_price = tp
                            pnl_pips = (entry_price - tp) / self.point_value
                            break
                        
                
                if result:
                    # Calculate monetary P&L
                    pnl = pnl_pips * self.point_value * lot_size * 100000
                    
                    # Update account balance (compound or not, depending on your preference)
                    # self.account_balance += pnl  # Uncomment for compounding
                    
                    trades.append({
                        "time": signal_time,
                        "type": f"{signal_type}_{signal_dir}",
                        "position": position_type,
                        "entry": entry_price,
                        "sl": sl,
                        "tp": tp,
                        "exit_price": exit_price,
                        "exit_time": self.df.index[i] if result else None,
                        "result": result,
                        "rr_ratio": rr_ratio,
                        "pnl": pnl,
                        "pnl_pips": pnl_pips,
                        "lot_size": lot_size,
                        "atr": atr,
                        "balance": self.account_balance + pnl  # Show what balance would be
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
        total_profit = sum(wins['pnl'])
        total_loss = sum(losses['pnl'])
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
            "total_profit": total_profit,
            "total_loss": total_loss,
            "profit_factor": profit_factor,
            "net_pnl": trades_df['pnl'].sum(),
            "max_drawdown": max_drawdown,
            "avg_rr": trades_df['rr_ratio'].mean(),
            "best_trade": trades_df['pnl'].max(),
            "worst_trade": trades_df['pnl'].min()
        }


if __name__ == "__main__":
    # Initialize simulator with M1 data for precision
    #simulator = TradingSimulator(symbol="XAUUSD", timeframe=mt5.TIMEFRAME_M5, days_back=30)
    
    # Simulate trades with both FVG and liquidity sweep strategies
    #trades_df = simulator.simulate_trades(use_fvg=True, use_sweeps=True, rr_ratio=2.0)
    
    # Generate performance report
    #report = simulator.generate_report(trades_df)
    
       
    # Test on different instruments and timeframes
    symbols = ["UT100Roll", "US500Roll", "XAUUSD"]
    timeframes = [mt5.TIMEFRAME_M5, mt5.TIMEFRAME_M15]
    results = {}
    for sym in symbols:
        for tf in timeframes:
            sim = TradingSimulator(
                symbol=sym, 
                timeframe=mt5.TIMEFRAME_M5, 
                days_back=300,
                account_balance=10000,  # $10,000 starting balance
                risk_per_trade=0.01      # Risk 1% per trade
            )
            trades = sim.simulate_trades()
            report = sim.generate_report(trades)

            # Print results
            print("\n=== Performance Report ===")
            for k, v in report.items():
                print(f"{k.replace('_', ' ').title()}: {v:.2f}" if isinstance(v, float) else f"{k.replace('_', ' ').title()}: {v}")
            print("=====================================================================================")
 
    
    # Shutdown MT5 connection
    mt5.shutdown()