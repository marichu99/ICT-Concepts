import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timedelta,timezone
from typing import List, Dict, Optional, Tuple

class TradingSimulator:
    def __init__(self, symbol: str, timeframe: int, days_back: int = 150, account_balance: float = 10000, risk_per_trade: float = 0.01):
        self.symbol = symbol
        self.timeframe = timeframe
        self.days_back = days_back
        self.initial_account_balance = account_balance # Store initial balance
        self.account_balance = account_balance
        self.risk_per_trade = risk_per_trade
        
        if not mt5.initialize():
            # Attempt to login if initialization failed (common in scripts)
            # Replace with your actual account details or ensure MT5 terminal is logged in
            # account_info = mt5.account_info()
            # if account_info is None or account_info.login == 0:
            #     print("MT5 not logged into an account.")
            #     # Example login (use environment variables or secure config for credentials)
            #     # mt5.login(account=YOUR_ACCOUNT, password="YOUR_PASSWORD", server="YOUR_SERVER")
            if not mt5.initialize(): # Check again
                error_message = f"MetaTrader5 initialize() failed with error: {mt5.last_error()}"
                mt5.shutdown()
                raise RuntimeError(error_message)

        self.symbol_info = self._get_symbol_info()
        if self.symbol_info is None:
            error_message = f"Could not retrieve symbol info for {self.symbol}. Error: {mt5.last_error()}"
            mt5.shutdown()
            raise ValueError(error_message)
            
        self.df = self._get_historical_data()
        self.current_trade_id = 0


    def _get_symbol_info(self) -> Optional[mt5.SymbolInfo]:
        """Get symbol information from MT5."""
        info = mt5.symbol_info(self.symbol)
        if info is None:
            print(f"Failed to get symbol info for {self.symbol}, error: {mt5.last_error()}")
            return None
        return info

    def _calculate_lot_size(self, entry_price: float, stop_loss_price: float) -> float:
        """Returns a fixed lot size of 0.5, ensuring it's within symbol limits."""
        fixed_lot = 1.0
        
        # Ensure the fixed lot is within the symbol's min/max limits
        if self.symbol_info is None:
            print("Cannot verify lot size: symbol_info not available. Using 0.5.")
            return fixed_lot
        
        # Check if 0.5 is within allowed range
        min_lot = self.symbol_info.volume_min
        max_lot = self.symbol_info.volume_max
        
        if fixed_lot < min_lot:
            print(f"Warning: Fixed lot (0.5) is below minimum allowed ({min_lot}). Using minimum.")
            return min_lot
        elif fixed_lot > max_lot:
            print(f"Warning: Fixed lot (0.5) exceeds maximum allowed ({max_lot}). Using maximum.")
            return max_lot
        else:
            return fixed_lot
        

    def _get_historical_data(self) -> pd.DataFrame:
        """Fetch historical data from MT5."""
        start_date = datetime.now(timezone.utc) - timedelta(days=self.days_back)
        end_date = datetime.now(timezone.utc)

        print(f"Fetching data for: {self.symbol}")
        print(f"Timeframe: {self.timeframe_to_string(self.timeframe)}")
        print(f"Start date: {start_date.strftime('%Y-%m-%d %H:%M')}")
        print(f"End date: {end_date.strftime('%Y-%m-%d %H:%M')}")

        rates = mt5.copy_rates_range(self.symbol, self.timeframe, start_date, end_date)
        if rates is None or len(rates) == 0:
            error_message = f"Failed to fetch historical data for {self.symbol}. Error: {mt5.last_error()}, Rates count: {len(rates) if rates is not None else 'None'}"
            # mt5.shutdown() # Keep MT5 running for other simulations
            raise ValueError(error_message)
            
        df = pd.DataFrame(rates)
        print(f"Fetched {len(df)} rates for {self.symbol}.")
        df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
        df.set_index('time', inplace=True)
        return df
    
    @staticmethod
    def timeframe_to_string(tf_int: int) -> str:
        """Converts MT5 timeframe integer to a readable string."""
        tf_map = {
            mt5.TIMEFRAME_M1: "M1", mt5.TIMEFRAME_M2: "M2", mt5.TIMEFRAME_M3: "M3",
            mt5.TIMEFRAME_M4: "M4", mt5.TIMEFRAME_M5: "M5", mt5.TIMEFRAME_M6: "M6",
            mt5.TIMEFRAME_M10: "M10", mt5.TIMEFRAME_M12: "M12", mt5.TIMEFRAME_M15: "M15",
            mt5.TIMEFRAME_M20: "M20", mt5.TIMEFRAME_M30: "M30", mt5.TIMEFRAME_H1: "H1",
            mt5.TIMEFRAME_H2: "H2", mt5.TIMEFRAME_H3: "H3", mt5.TIMEFRAME_H4: "H4",
            mt5.TIMEFRAME_H6: "H6", mt5.TIMEFRAME_H8: "H8", mt5.TIMEFRAME_H12: "H12",
            mt5.TIMEFRAME_D1: "D1", mt5.TIMEFRAME_W1: "W1", mt5.TIMEFRAME_MN1: "MN1"
        }
        return tf_map.get(tf_int, f"UnknownTF({tf_int})")

    @staticmethod
    def detect_fvg(df: pd.DataFrame) -> List[Tuple[pd.Timestamp, str]]:
        """Detect Fair Value Gaps in price data (standard 3-candle pattern)."""
        fvg = []
        if len(df) < 3:
            return fvg
            
        for i in range(2, len(df)):
            # Candle i-2 is Candle 1, Candle i-1 is Candle 2, Candle i is Candle 3
            candle1_high = df.iloc[i-2]['high']
            candle1_low = df.iloc[i-2]['low']
            
            # candle2_high = df.iloc[i-1]['high'] # Not directly used in this FVG definition
            # candle2_low = df.iloc[i-1]['low']   # Not directly used in this FVG definition
            
            candle3_high = df.iloc[i]['high']
            candle3_low = df.iloc[i]['low']
            
            # Bullish FVG: Candle 3's Low is above Candle 1's High
            if candle3_low > candle1_high:
                fvg.append((df.index[i], "bullish")) # Signal on close of Candle 3
                
            # Bearish FVG: Candle 3's High is below Candle 1's Low
            elif candle3_high < candle1_low:
                fvg.append((df.index[i], "bearish")) # Signal on close of Candle 3
        return fvg
    @staticmethod
    def detect_fvg_w_sweeps(df: pd.DataFrame,lookback: int=20) -> List[Tuple[pd.Timestamp, str]]:
        """Detect Fair Value Gaps in price data (standard 3-candle pattern)."""
        fvg = []
        if len(df) < lookback+1:
            return fvg
        
        MAX_SWEEP_MARGIN = df['high'].std() * 0.5  # or use ATR if available

            
        for i in range(2, len(df)):

            lookback_high_max = df.iloc[i-lookback:i-2]['high'].max()
            lookback_low_min = df.iloc[i-lookback:i]['low'].min()

            # Candle i-2 is Candle 1, Candle i-1 is Candle 2, Candle i is Candle 3
            candle1_high = df.iloc[i-2]['high']
            candle1_low = df.iloc[i-2]['low']
            
            # candle2_high = df.iloc[i-1]['high'] # Not directly used in this FVG definition
            # candle2_low = df.iloc[i-1]['low']   # Not directly used in this FVG definition
            
            candle3_high = df.iloc[i]['high']
            candle3_close = df.iloc[i]['close']
            candle3_open = df.iloc[i]['open']
            candle3_low = df.iloc[i]['low']

            if candle3_high > lookback_high_max and candle3_close < candle3_open:
                sweep_margin = candle3_high - lookback_high_max
                if sweep_margin <= MAX_SWEEP_MARGIN and candle3_low > candle1_high:
                    fvg.append((df.index[i], "bullish"))

            elif candle3_low < lookback_low_min and candle3_close > candle3_open:
                sweep_margin = lookback_low_min - candle3_low
                if sweep_margin <= MAX_SWEEP_MARGIN and candle3_high < candle1_low:
                    fvg.append((df.index[i], "bearish"))

            
        return fvg
    
    @staticmethod
    def detect_liquidity_sweeps(df: pd.DataFrame, lookback: int = 20) -> List[Tuple[pd.Timestamp, str]]:
        """Detect liquidity sweeps (false breakouts)."""
        sweeps = []
        if len(df) < lookback + 1 : # Ensure enough data for lookback
            return sweeps

        for i in range(lookback, len(df)):
            current_candle = df.iloc[i]
            current_high = current_candle['high']
            current_low = current_candle['low']
            
            lookback_high_max = df.iloc[i-lookback:i]['high'].max()
            lookback_low_min = df.iloc[i-lookback:i]['low'].min()
            
            # Bullish sweep (false breakout above resistance, then closes bearish)
            if current_high > lookback_high_max and current_candle['close'] < current_candle['open']:
                sweeps.append((df.index[i], "bullish_sweep_reversal_short")) # Signal to go short
            
            # Bearish sweep (false breakout below support, then closes bullish)
            elif current_low < lookback_low_min and current_candle['close'] > current_candle['open']:
                sweeps.append((df.index[i], "bearish_sweep_reversal_long")) # Signal to go long
                
        return sweeps
    
    @staticmethod
    def in_london_newyork_window(ts: pd.Timestamp) -> bool:
        """Check if time is in London/NY overlap or NY session (UTC based)."""
        hour = ts.hour
        # London: 07:00-16:00 UTC (approx, DST dependent)
        # New York: 12:00-21:00 UTC (approx, DST dependent)
        # Overlap: 12:00-16:00 UTC
        # Common active hours: 08:00 - 17:00 UTC for broader coverage
        return (8 <= hour < 12) or (13 <= hour < 17) 

    def simulate_trades(self, use_fvg: bool = True, use_sweeps: bool = True, rr_ratio: float = 2.0, sl_points_fixed: int = 200, enable_compounding: bool = False) -> pd.DataFrame:
        """Simulate trades based on selected strategies."""
       
        trades = []
        self.account_balance = self.initial_account_balance # Reset balance for each simulation run
        self.current_trade_id = 0


        if self.symbol_info is None:
            print("Cannot simulate trades: symbol_info is not available.")
            return pd.DataFrame()

        if use_fvg:
            fvg_signals = self.detect_fvg(self.df)
            trades += self._process_signals(fvg_signals, "FVG", rr_ratio, sl_points_fixed, enable_compounding)
            
        if use_sweeps:
            sweep_signals = self.detect_liquidity_sweeps(self.df)
            trades += self._process_signals(sweep_signals, "SWEEP", rr_ratio, sl_points_fixed, enable_compounding)
        
        if not trades:
            print("No trades were generated.")
            return pd.DataFrame()

        trades_df = pd.DataFrame(trades)

        # trades_df.to_csv(f"trades_{self.symbol}_{self.timeframe_to_string(self.timeframe)}.csv")
        return trades_df
    def _get_sub_timeframe_data(self, start_time: pd.Timestamp, end_time: pd.Timestamp, sub_timeframe: int) -> pd.DataFrame:
        """
        Fetches historical data for a smaller timeframe within a given range.
        """
        rates = mt5.copy_rates_range(self.symbol, sub_timeframe, start_time, end_time)
        if rates is None or len(rates) == 0:
            return pd.DataFrame()

        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df = df.set_index('time')
        return df[['open', 'high', 'low', 'close', 'tick_volume']]
    
    def _process_signals(self, signals: List[Tuple[pd.Timestamp, str]], signal_type_prefix: str, rr_ratio: float, sl_points_fixed: int, enable_compounding: bool) -> List[Dict]:
        """Process trading signals and simulate trades with lot size calculation."""
        processed_trades = []
        
        if self.symbol_info is None:
            print("Cannot process signals: symbol_info is not available.")
            return processed_trades
            
        value_per_point_per_lot = (self.symbol_info.trade_tick_value / self.symbol_info.trade_tick_size) * self.symbol_info.point if self.symbol_info.trade_tick_size > 0 else 0

        for signal_time, signal_details in signals: # signal_details contains direction, e.g., "bullish" or "bearish_sweep_reversal_long"
            if not self.in_london_newyork_window(signal_time):
                continue
                
            entry_idx = self.df.index.get_loc(signal_time)
            if entry_idx + 3 >= len(self.df): # Need at least 3 candles for entry fill check
                continue
            
            entry_candle = self.df.iloc[entry_idx]
            entry_price = entry_candle['close'] # Entry at close of signal candle

            # Get the current tick (bid/ask)
            tick = mt5.symbol_info_tick(self.symbol)

            bid=0.0
            ask=0.0
            spread=0.0
            if tick:
                bid = tick.bid
                ask = tick.ask
                spread = (ask - bid) 
            
            sl_distance_price = sl_points_fixed * self.symbol_info.point
            tp_distance_price = sl_distance_price * rr_ratio
            
            position_type = ""
            signal_direction = "" # To determine SL/TP placement

            if signal_type_prefix == "FVG":
                if signal_details == "bullish": # FVG Bullish implies price expected to go up
                    position_type = "buy"
                    signal_direction = "bullish"
                elif signal_details == "bearish": # FVG Bearish implies price expected to go down
                    position_type = "sell"
                    signal_direction = "bearish"
            elif signal_type_prefix == "SWEEP":
                if signal_details == "bearish_sweep_reversal_long": # Swept lows, expect price to go up
                    position_type = "buy"
                    signal_direction = "bullish"
                elif signal_details == "bullish_sweep_reversal_short": # Swept highs, expect price to go down
                    position_type = "sell"
                    signal_direction = "bearish"
            
            if not position_type: # If no valid position type determined
                continue

            if signal_direction == "bullish": # Buy trade
                sl = entry_price - sl_distance_price - spread
                tp = entry_price + tp_distance_price + spread
            elif signal_direction == "bearish": # Sell trade
                sl = entry_price + sl_distance_price + spread
                tp = entry_price - tp_distance_price -spread
            else: # Should not happen if position_type is set
                continue 
                
            lot_size = self._calculate_lot_size(entry_price, sl)
            if lot_size == 0 or lot_size < self.symbol_info.volume_min : # Check if lot size is valid
                 print(f"Skipping trade at {signal_time} due to invalid lot size: {lot_size}")
                 continue
            
            # Check if price fills the entry zone (next 3 candles)
            # For a buy, we need low of future candles to touch entry_price.
            # For a sell, we need high of future candles to touch entry_price.
            filled = False
            actual_entry_price = entry_price # Assume entry at signal candle close initially
            entry_fill_time = signal_time

            # Check for fill within next 3 candles (or immediate fill on signal candle itself)
            if (signal_direction == "bullish" and entry_candle['low'] <= entry_price and entry_candle['high'] >= entry_price) or \
               (signal_direction == "bearish" and entry_candle['low'] <= entry_price and entry_candle['high'] >= entry_price):
                filled = True # Filled on the signal candle itself

            if not filled:
                future_candles_for_fill = self.df.iloc[entry_idx + 1 : entry_idx + 1 + 3] # Look 3 candles ahead for fill
                for k in range(len(future_candles_for_fill)):
                    future_candle = future_candles_for_fill.iloc[k]
                    if signal_direction == "bullish":
                        if future_candle['low'] <= entry_price: # Price came down to entry
                            filled = True
                            entry_fill_time = future_candles_for_fill.index[k]
                            # actual_entry_price = entry_price # Entry price is fixed
                            break
                    elif signal_direction == "bearish":
                        if future_candle['high'] >= entry_price: # Price came up to entry
                            filled = True
                            entry_fill_time = future_candles_for_fill.index[k]
                            # actual_entry_price = entry_price # Entry price is fixed
                            break
            
            if filled:
                self.current_trade_id += 1
                result = None
                exit_time = None
                exit_price = None
                pnl = 0.0
                pnl_points = 0.0
                
                # Start tracking from the candle *after* the fill candle, or after signal candle if filled immediately
                # Max 12 candles *after fill* for trade duration
                start_tracking_idx = self.df.index.get_loc(entry_fill_time) + 1
                monitoring_window_end_idx = min(start_tracking_idx + 12, len(self.df))

                for i in range(start_tracking_idx, monitoring_window_end_idx):
                    current_candle_trade = self.df.iloc[i]
                    high, low = current_candle_trade['high'], current_candle_trade['low']
                    
                    if signal_direction == "bullish": # Buy trade
                        if high >= tp: # Check TP first
                            result = "win"
                            exit_price = tp
                            pnl_points = (tp - actual_entry_price) / self.symbol_info.point
                            exit_time = self.df.index[i]
                            break
                    #    elif low <= sl:
                    #        result = "loss"
                    #        exit_price = sl
                    #        pnl_points = (sl - actual_entry_price) / self.symbol_info.point
                    #        exit_time = self.df.index[i]
                    #        break
                    elif signal_direction == "bearish": # Sell trade
                        if low <= tp: # Check TP first
                            result = "win"
                            exit_price = tp
                            pnl_points = (actual_entry_price - tp) / self.symbol_info.point
                            exit_time = self.df.index[i]
                            break
                        #elif high >= sl:
                        #    result = "loss"
                        #    exit_price = sl
                        #    pnl_points = (actual_entry_price - sl) / self.symbol_info.point
                        #    exit_time = self.df.index[i]
                        #    break
                
                # --- NEW LOGIC: Granular Check for "Loss Exceeds Potential Profit" within Timeout ---
                if result is None: # Trade still active after main timeframe check (i.e., it would be a "timeout")
                    
                    # Determine the actual time range for the potential timeout candles
                    # This is from the start_tracking_idx up to the end of the 12-candle window
                    timeout_start_time = self.df.index[start_tracking_idx]
                    timeout_end_time = self.df.index[monitoring_window_end_idx - 1] + pd.Timedelta(self.timeframe, unit='s') # End of the last candle

                    #print(f"The timeout start time is {timeout_start_time} and the entry time is {entry_fill_time}")

                    # Fetch minute data for this entire potential timeout window
                    # This is the "going granular" part
                    minute_data = self._get_sub_timeframe_data(timeout_start_time, timeout_end_time, mt5.TIMEFRAME_M1)

                    early_exit_loss_triggered = False
                    exit_candle_m1 = None

                    potential_profit_points = tp_distance_price / self.symbol_info.point

                    for _, m1_candle in minute_data.iterrows():
                        m1_high, m1_low = m1_candle['high'], m1_candle['low']
                        m1_close = m1_candle['close']

                        current_unrealized_pnl_points_m1 = 0.0

                        if signal_direction == "bullish":
                            
                            # For buy, check if M1 high hits or crosses above TP
                            if m1_high >= tp:
                                result = "win"
                                exit_price = tp
                                pnl_points = (tp - actual_entry_price) / self.symbol_info.point
                                exit_time = m1_candle.name
                                early_exit_loss_triggered = False # TP hit, not early exit loss
                                break
                            
                            # If not SL/TP, check for "loss exceeds potential profit" using M1 low
                            current_unrealized_pnl_points_m1 = (m1_low - actual_entry_price) / self.symbol_info.point # Worst point in candle
                            if current_unrealized_pnl_points_m1 < 0 and abs(current_unrealized_pnl_points_m1) > potential_profit_points:
                                result = "early_exit_loss"
                                exit_price = m1_low # Exit at the point the condition was met
                                pnl_points = current_unrealized_pnl_points_m1
                                exit_time = m1_candle.name
                                early_exit_loss_triggered = True
                                break # Exit the M1 loop
                        
                        elif signal_direction == "bearish":
                           
                            # For sell, check if M1 low hits or crosses below TP
                            if m1_low <= tp:
                                result = "win"
                                exit_price = tp
                                pnl_points = (actual_entry_price - tp) / self.symbol_info.point
                                exit_time = m1_candle.name
                                early_exit_loss_triggered = False # TP hit, not early exit loss
                                break

                            # If not SL/TP, check for "loss exceeds potential profit" using M1 high
                            current_unrealized_pnl_points_m1 = (actual_entry_price - m1_high) / self.symbol_info.point # Worst point in candle
                            if current_unrealized_pnl_points_m1 < 0 and abs(current_unrealized_pnl_points_m1) > potential_profit_points:
                                result = "early_exit_loss"
                                exit_price = m1_high # Exit at the point the condition was met
                                pnl_points = current_unrealized_pnl_points_m1
                                exit_time = m1_candle.name
                                
                                early_exit_loss_triggered = True
                                break # Exit the M1 loop
                    
                    # If after checking all M1 candles, no SL/TP or early_exit_loss was triggered
                    if result is None:
                        result = "timeout"
                        # Exit at close of the last candle in the monitoring window (main timeframe)
                        # This covers the case where the M1 data for the very last part of the timeout period might be incomplete,
                        # or the condition wasn't met on any M1 candle.
                        exit_price = self.df.iloc[monitoring_window_end_idx - 1]['close']
                        exit_time = self.df.index[monitoring_window_end_idx - 1]
                        if signal_direction == "bullish":
                            pnl_points = (exit_price - actual_entry_price) / self.symbol_info.point
                        else: # Bearish
                            pnl_points = (actual_entry_price - exit_price) / self.symbol_info.point

                # Continue with PnL calculation and trade data logging as before
                value_per_point_per_lot = (self.symbol_info.trade_tick_value / self.symbol_info.trade_tick_size) \
                                        * self.symbol_info.point if self.symbol_info.trade_tick_size > 0 else 0.0

                if value_per_point_per_lot > 0 :
                    pnl = pnl_points * value_per_point_per_lot * lot_size
                else: # Should not happen with checks
                    pnl = 0 

                trade_data = {
                    "trade_id": self.current_trade_id,
                    "signal_time": signal_time,
                    "entry_fill_time": entry_fill_time,
                    "exit_time": exit_time,
                    "type": f"{signal_type_prefix}_{signal_details}",
                    "position": position_type,
                    "entry": actual_entry_price,
                    "sl": sl,
                    "tp": tp,
                    "exit_price": exit_price,
                    "result": result,
                    "highest_point": high,
                    "lowest_point": low,
                    "rr_ratio": rr_ratio,
                    "pnl": pnl,
                    "pnl_points": pnl_points,
                    "lot_size": lot_size,
                    "initial_balance_for_trade": self.account_balance, # Balance before this trade's P&L
                }
                
                if enable_compounding:
                    self.account_balance += pnl
                
                trade_data["balance_after_trade"] = self.account_balance if not enable_compounding else self.account_balance # if not compounding, it shows balance without this PnL; if compounding, it is updated
                if not enable_compounding: # If not compounding, show what balance would be with this pnl
                     trade_data["balance_after_trade"] = trade_data["initial_balance_for_trade"] + pnl


                processed_trades.append(trade_data)
                    
        return processed_trades
    
    def _calculate_atr(self, df_subset: pd.DataFrame, period: int = 14) -> float: # Now takes df_subset
        """Calculate Average True Range on a DataFrame slice."""
        if len(df_subset) < 2: return 0.0 # Not enough data for prev_close
        
        # Ensure we don't modify the original DataFrame slice if it's passed from self.df
        temp_df = df_subset.copy()
        
        temp_df['prev_close'] = temp_df['close'].shift(1)
        temp_df['tr1'] = temp_df['high'] - temp_df['low']
        temp_df['tr2'] = abs(temp_df['high'] - temp_df['prev_close'])
        temp_df['tr3'] = abs(temp_df['low'] - temp_df['prev_close'])
        temp_df['tr'] = temp_df[['tr1', 'tr2', 'tr3']].max(axis=1)
        
        # Use .rolling().mean() for ATR if enough data, otherwise simple mean of TR
        if len(temp_df) >= period:
            atr_series = temp_df['tr'].rolling(window=period, min_periods=1).mean()
            return atr_series.iloc[-1] if not atr_series.empty else 0.0
        elif not temp_df['tr'].empty:
            return temp_df['tr'].mean()
        return 0.0

    def generate_report(self, trades_df: pd.DataFrame) -> Dict:
        """Generate performance report from trades."""
        if trades_df.empty:
            print("Trade DataFrame is empty. Cannot generate report.")
            return {
                "message": "No trades to report.",
                "total_trades": 0
            }
            
        wins = trades_df[trades_df['result'] == 'win']
        losses = trades_df[trades_df['result'] == 'loss']
        timeouts = trades_df[trades_df['result'] == 'timeout'] # Trades that hit max duration
        
        total_trades = len(trades_df)
        win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0
        
        total_profit_wins = wins['pnl'].sum()
        total_loss_losses = losses['pnl'].sum() # This will be negative or zero
        
        # For profit factor, consider timeouts that were profitable/unprofitable
        gross_profit = trades_df[trades_df['pnl'] > 0]['pnl'].sum()
        gross_loss = trades_df[trades_df['pnl'] < 0]['pnl'].sum() # Negative

        avg_win_pnl = wins['pnl'].mean() if not wins.empty else 0
        avg_loss_pnl = losses['pnl'].mean() if not losses.empty else 0 # Negative
        
        profit_factor = abs(gross_profit / gross_loss) if gross_loss != 0 else float('inf') if gross_profit > 0 else 0
        
        # Calculate cumulative P&L based on initial balance for accurate drawdown
        # The 'balance_after_trade' if compounding is off is not sequential actual balance.
        # If compounding is on, 'balance_after_trade' can be used.
        # For drawdown, we need the equity curve.
        
        # If compounding was enabled, self.account_balance in __init__ was used and updated.
        # If not, use initial_balance + cumulative P&L
        
        current_balance_col = 'balance_after_trade' if 'balance_after_trade' in trades_df.columns else None

        if current_balance_col and not trades_df[trades_df[current_balance_col].isnull()].empty:
            # Handle cases where balance might be null if a trade has no PnL (e.g. lot size 0)
            print(f"Warning: Null values found in '{current_balance_col}'. Drawdown might be inaccurate.")
            # Fallback to cumulative PnL from initial for drawdown calculation
            equity_curve = self.initial_account_balance + trades_df['pnl'].cumsum()
        elif current_balance_col and any(col in trades_df.columns for col in ['initial_balance_for_trade']):
             # If compounding is off, balance_after_trade is initial_balance_for_trade + pnl
             # If compounding is on, balance_after_trade is the running balance
             # The logic in _process_signals for 'balance_after_trade' aims to reflect this.
            equity_curve = trades_df[current_balance_col]
        else: # Fallback if balance columns are not as expected
            print("Warning: Suitable balance column not found for drawdown. Using PnL cumsum from initial balance.")
            equity_curve = self.initial_account_balance + trades_df['pnl'].cumsum()


        if not equity_curve.empty:
            max_equity = equity_curve.cummax()
            drawdown = max_equity - equity_curve
            max_drawdown_value = drawdown.max()
            # Max drawdown percentage (relative to peak equity at the time of drawdown)
            # Or relative to initial balance if preferred
            # Max DD % relative to peak: (drawdown / max_equity).max() * 100
            # Max DD % relative to initial: max_drawdown_value / self.initial_account_balance * 100
            max_drawdown_percentage = (max_drawdown_value / max_equity[drawdown.idxmax()]) * 100 if max_equity[drawdown.idxmax()] > 0 else 0
            if max_drawdown_value == 0 : max_drawdown_percentage = 0.0 # Avoid NaN if no drawdown
        else:
            max_drawdown_value = 0.0
            max_drawdown_percentage = 0.0

        return {
            "total_trades": total_trades,
            "wins": len(wins),
            "losses": len(losses),
            "timeouts": len(timeouts),
            "win_rate_percentage": win_rate,
            "average_win_pnl": avg_win_pnl,
            "average_loss_pnl": avg_loss_pnl,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss, # This is negative
            "profit_factor": profit_factor,
            "net_pnl": trades_df['pnl'].sum(),
            "max_drawdown_value": max_drawdown_value,
            "max_drawdown_percentage": max_drawdown_percentage,
            "average_rr_ratio_config": trades_df['rr_ratio'].mean() if not trades_df.empty else 0, # Configured RR
            "best_trade_pnl": trades_df['pnl'].max() if not trades_df.empty else 0,
            "worst_trade_pnl": trades_df['pnl'].min() if not trades_df.empty else 0,
            "final_balance": equity_curve.iloc[-1] if not equity_curve.empty else self.initial_account_balance,
            "initial_balance": self.initial_account_balance
        }


if __name__ == "__main__":
    # Test on different instruments and timeframes
    symbols_to_test = ["UT100Roll", "US500Roll", "XAUUSD"] 
    timeframes_to_test = [mt5.TIMEFRAME_M5, mt5.TIMEFRAME_M15]
    
    all_results_summary = []

    for sym in symbols_to_test:
        for tf_val in timeframes_to_test:
            print(f"\n{'='*30} SIMULATING: {sym} - {TradingSimulator.timeframe_to_string(tf_val)} {'='*30}")
            try:
                sim = TradingSimulator(
                    symbol=sym, 
                    timeframe=tf_val, 
                    days_back=90,             # How many days of data
                    account_balance=10000,    # Starting balance
                    risk_per_trade=0.01       # Risk 1% per trade
                )
                
                trades_df = sim.simulate_trades(
                    use_fvg=True, 
                    use_sweeps=True, 
                    rr_ratio=3.0,             # Risk:Reward Ratio
                    sl_points_fixed=150,      # Stop loss in points (e.g., for EURUSD 150 points = 15 pips if point=0.0001)
                                              # For XAUUSD 150 points = $1.50 if point=0.01
                    enable_compounding=True
                )

                if not trades_df.empty:
                    trades_df.to_csv(f"trades_{sym}_{sim.timeframe_to_string(tf_val)}.csv")
                    print(f"\n--- Trades for {sym} - {sim.timeframe_to_string(tf_val)} ---")
                    print(trades_df[['signal_time', 'type', 'position', 'entry','sl','tp', 'exit_price', 'result', 'pnl', 'lot_size']].head())
                
                report = sim.generate_report(trades_df)
                
                print(f"\n--- Performance Report for {sym} - {sim.timeframe_to_string(tf_val)} ---")
                for k, v in report.items():
                    if isinstance(v, float):
                        print(f"{k.replace('_', ' ').title()}: {v:.2f}")
                    else:
                        print(f"{k.replace('_', ' ').title()}: {v}")
                all_results_summary.append({
                    "symbol": sym, 
                    "timeframe": sim.timeframe_to_string(tf_val),
                    **report # Add all report items
                })

            except (RuntimeError, ValueError, Exception) as e:
                print(f"!!! ERROR during simulation for {sym} - {TradingSimulator.timeframe_to_string(tf_val)}: {e}")
            print(f"{'='*80}\n")

    # Shutdown MT5 connection once all simulations are done
    mt5.shutdown()
    print("MetaTrader5 connection shut down.")

    # Optional: Print a summary table of all results
    if all_results_summary:
        summary_df = pd.DataFrame(all_results_summary)
        print("\n\n--- Overall Simulation Summary ---")
        print(summary_df)
        #print(summary_df[['symbol', 'timeframe', 'Total Trades', 'Net Pnl', 'Win Rate Percentage', 'Profit Factor', 'Max Drawdown Percentage', 'Final Balance']].round(2))
        summary_df.to_csv("simulation_summary_report.csv")