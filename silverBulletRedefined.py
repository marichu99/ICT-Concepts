import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timedelta,timezone
from typing import List, Dict, Optional, Tuple
import pytz

class TradingSimulator:
    def __init__(self, symbol: str, timeframe: int, days_back: int = 150, account_balance: float = 10000, risk_per_trade: float = 0.01):
        self.symbol = symbol
        self.timeframe = timeframe
        self.days_back = days_back
        self.initial_account_balance = account_balance # Store initial balance
        self.account_balance = account_balance
        self.risk_per_trade = risk_per_trade
        self.daily_bias = self._get_daily_bias(symbol=symbol)
        
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
            print(f"Could not retrieve symbol info for {self.symbol}. Error: {mt5.last_error()}")
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
        
    def calculate_take_profit(
        self,
        entry_price: float,
        stop_loss_price: float,
        rr_ratio: float,
        trade_type: str,
        digits: Optional[int] = None
    ) -> float:
        """
        Calculates the Take Profit (TP) level based on the entry price, Stop Loss (SL) price,
        and a Risk-to-Reward (RR) ratio.

        The 'loss' or risk is defined as the absolute difference between the entry price
        and the stop_loss_price. The TP is then set at a distance from the entry price
        that is 'rr_ratio' times this risk, in the direction of profit.

        Args:
            entry_price (float): The entry price of the trade.
            stop_loss_price (float): The Stop Loss price for the trade.
            rr_ratio (float): The desired Risk-to-Reward ratio (e.g., 1.5 for 1:1.5 R:R).
                            This value must be greater than 0.
            trade_type (str): The type of trade, either 'buy' or 'sell'.
            digits (Optional[int]): The number of decimal places to round the TP to.
                                    If None, no rounding is performed. If provided,
                                    must be a non-negative integer.

        Returns:
            float: The calculated Take Profit price.

        Raises:
            ValueError: If rr_ratio is not positive, trade_type is invalid,
                        stop_loss_price implies no risk, or if stop_loss_price
                        is not logically placed relative to the entry_price
                        for the given trade_type.
        """
        if not isinstance(rr_ratio, (int, float)) or rr_ratio <= 0:
            raise ValueError("Risk-to-Reward ratio (rr_ratio) must be a positive number.")

        trade_type_lower = trade_type.lower()
        if trade_type_lower not in ['buy', 'sell']:
            raise ValueError("Invalid trade_type. Must be 'buy' or 'sell'.")

        # Calculate risk per unit and validate SL placement
        if trade_type_lower == 'buy':
            if stop_loss_price >= entry_price:
                raise ValueError(
                    f"For a 'buy' trade, Stop Loss price ({stop_loss_price}) "
                    f"must be below the entry price ({entry_price})."
                )
            risk_per_unit = entry_price - stop_loss_price
        else:  # trade_type_lower == 'sell'
            if stop_loss_price <= entry_price:
                raise ValueError(
                    f"For a 'sell' trade, Stop Loss price ({stop_loss_price}) "
                    f"must be above the entry price ({entry_price})."
                )
            risk_per_unit = stop_loss_price - entry_price

        # This check ensures risk is positive, which should already be true if SL is correctly placed.
        if risk_per_unit <= 0: # Should ideally not be hit if above checks are sound
            raise ValueError(
                "Calculated risk per unit must be positive. "
                "Ensure stop_loss_price is correctly placed relative to entry_price."
            )

        reward_per_unit = risk_per_unit * rr_ratio

        # Calculate TP price
        if trade_type_lower == 'buy':
            tp_price = entry_price + reward_per_unit
        else:  # trade_type_lower == 'sell'
            tp_price = entry_price - reward_per_unit

        # Optional rounding
        if digits is not None:
            if not isinstance(digits, int) or digits < 0:
                raise ValueError("Digits for rounding must be a non-negative integer.")
            return round(tp_price, digits)
        else:
            return tp_price
    
    def _get_filtered_data(self) -> pd.DataFrame:
        """
        Fetches historical data from MetaTrader 5 and filters it to New York time intervals.
        Time intervals: 2–3 AM, 10–11 AM, 2–3 PM (New York time, DST-aware).

        """
        start_date = datetime.now(timezone.utc) - timedelta(days=self.days_back)
        end_date = datetime.now(timezone.utc)

        print(f"The start date {start_date} the end date  {end_date}")
        # Fetch recent bars
        rates = mt5.copy_rates_range(self.symbol, self.timeframe, start_date, end_date)
        if rates is None or len(rates) == 0:
            raise Exception("No data returned for symbol: " + self.symbol)

        # Convert to DataFrame
        df = pd.DataFrame(rates)
        
        # Convert timestamp to datetime and localize to UTC
        df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)

        # Convert to New York time (handles DST)
        ny_tz = pytz.timezone("America/New_York")
        df['time_ny'] = df['time'].dt.tz_convert(ny_tz)
        
        # Extract hour and minute
        df['hour'] = df['time_ny'].dt.hour
       
        # Filter to allowed hours
        df = df[df['hour'].isin([2, 10, 14])]

        # Drop helper columns
        df = df.drop(columns=['hour'])

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
    def detect_fvg_w_sweeps_atr(df: pd.DataFrame, lookback: int = 20, atr_period: int = 14, rr_ratio: float = 2.0) -> List[Tuple[pd.Timestamp, str, float, float]]:
        """
        Detect Fair Value Gaps with liquidity sweeps using ATR and include TP and SL.
        """
        fvg = []
        if len(df) < max(lookback, atr_period) + 2:
            return fvg

        high = df['high']
        low = df['low']
        close = df['close']
        open_ = df['open']
        prev_close = close.shift(1)

        # True Range and ATR
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=atr_period).mean()

        for i in range(max(lookback, atr_period)):
            lookback_high_max = df.iloc[i-lookback:i-2]['high'].max()
            lookback_low_min = df.iloc[i-lookback:i]['low'].min()

            candle1_high = df.iloc[i-2]['high']
            candle1_low = df.iloc[i-2]['low']

            candle3 = df.iloc[i]
            entry = candle3['close']
            atr_margin = atr.iloc[i]

            # Bearish FVG (go short)
            if candle3['high'] > lookback_high_max and candle3['close'] < candle3['open']:
                sweep_margin = candle3['high'] - lookback_high_max
                if sweep_margin <= atr_margin and candle3['low'] > candle1_high:
                    sl = candle3['high'] + atr_margin
                    tp = entry - (sl - entry) * rr_ratio
                    fvg.append((df.index[i], "bearish", round(tp, 5), round(sl, 5)))

            # Bullish FVG (go long)
            elif candle3['low'] < lookback_low_min and candle3['close'] > candle3['open']:
                sweep_margin = lookback_low_min - candle3['low']
                if sweep_margin <= atr_margin and candle3['high'] < candle1_low:
                    sl = candle3['low'] - atr_margin
                    tp = entry + (entry - sl) * rr_ratio
                    fvg.append((df.index[i], "bullish", round(tp, 5), round(sl, 5)))

        return fvg

    @staticmethod
    def  detect_fvg_liquidity_shifts(
        df: pd.DataFrame,
        group_size: int = 12,
        atr_period: int = 14,
        rr_ratio: float = 2.0
    ) -> List[Tuple[pd.Timestamp, str, float, float]]:
        """
        Detect Fair Value Gaps with liquidity sweeps and MSS using ATR and RR logic.
        Groups candles, detects liquidity grabs from previous group, checks MSS + 3-candle FVG,
        and returns trade signal with TP and SL.
        """
        signals = []
        if len(df) < max(group_size * 2, atr_period + 3):
            return signals

        # Calculate ATR
        high = df['high']
        low = df['low']
        close = df['close']
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=atr_period).mean()

        num_groups = len(df) // group_size

        for g in range(1, num_groups):
            prev_group = df.iloc[(g - 1) * group_size : g * group_size]
            curr_group = df.iloc[g * group_size : (g + 1) * group_size]
            if len(prev_group) < group_size or len(curr_group) < 3:
                continue

            prev_high = prev_group['high'].max()
            prev_low = prev_group['low'].min()

            for i in range(2, len(curr_group)):
                candle1 = curr_group.iloc[i - 2]
                candle2 = curr_group.iloc[i - 1]
                candle3 = curr_group.iloc[i]
                idx = curr_group.index[i]

                atr_margin = atr.loc[idx]
                entry = candle3['close']

                # === 1. Bearish setup (Buy-side liquidity taken) ===
                if candle3['high'] > prev_high and candle3['close'] < candle3['open']:
                    # MSS: lower high and lower close
                    if candle3['high'] < candle2['high'] and candle3['close'] < candle2['close']:
                        # FVG: candle3.low > candle1.high
                        if candle3['low'] > candle1['high']:
                            sl = candle3['high'] + atr_margin
                            tp = entry - (sl - entry) * rr_ratio
                            signals.append((idx, "bearish", round(tp, 5), round(sl, 5),entry))

                # === 2. Bullish setup (Sell-side liquidity taken) ===
                elif candle3['low'] < prev_low and candle3['close'] > candle3['open']:
                    # MSS: higher low and higher close
                    if candle3['low'] > candle2['low'] and candle3['close'] > candle2['close']:
                        # FVG: candle3.high < candle1.low
                        if candle3['high'] < candle1['low']:
                            sl = candle3['low'] - atr_margin
                            tp = entry + (entry - sl) * rr_ratio
                            signals.append((idx, "bullish", round(tp, 5), round(sl, 5),entry))

        return signals
    
    @staticmethod
    def detect_fvg_liquidity_shifts_v2(
        df_5m: pd.DataFrame,
        df_15m: pd.DataFrame,
        group_size: int = 12,
        atr_period: int = 14,
        rr_ratio: float = 2.0,
        fvg_buffer: float = 0.00015
    ) -> List[Tuple[pd.Timestamp, str, float, float, float]]:
        """
        Detects signals based on:
        1. Liquidity sweeps on 15m
        2. FVG zones on 5m directly after sweep (without breaker logic)

        Returns: list of tuples (signal_time, direction, tp, sl, entry)
        """
        signals = []

        if len(df_5m) < group_size * 3 or len(df_15m) < group_size:
            return signals

        # Calculate ATR on 5m
        tr = pd.concat([
            df_5m['high'] - df_5m['low'],
            (df_5m['high'] - df_5m['close'].shift()).abs(),
            (df_5m['low'] - df_5m['close'].shift()).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=atr_period).mean()

        # Detect sweeps on 15m
        num_groups = len(df_15m) // group_size
        prev_highs, prev_lows = [], []

        sweep_signals = []
        for g in range(1, num_groups):
            prev_group = df_15m.iloc[(g - 1) * group_size: g * group_size]
            curr_group = df_15m.iloc[g * group_size: (g + 1) * group_size]

            if len(prev_group) < group_size or len(curr_group) < 3:
                continue

            prev_high = prev_group['high'].max()
            prev_low = prev_group['low'].min()

            prev_highs.append(prev_high)
            prev_lows.append(prev_low)

            for i in range(2, len(curr_group)):
                candle = curr_group.iloc[i]
                idx = curr_group.index[i]

                if (candle['high'] > prev_high and candle['close'] < candle['open']):
                #or \
                #(any(candle['high'] > x for x in prev_highs) and candle['close'] < candle['open']):
                    sweep_signals.append((idx, "bearish_sweep", prev_high))

                elif (candle['low'] < prev_low and candle['close'] > candle['open']):
                #or \
                #    (any(candle['low'] < x for x in prev_lows) and candle['close'] > candle['open']):
                    sweep_signals.append((idx, "bullish_sweep", prev_low))

        # Look for FVG after sweep
        for sweep_time, sweep_type, sweep_level in sweep_signals:
            end_time = sweep_time + pd.Timedelta(minutes=45)
            post_sweep_df = df_5m[(df_5m.index >= sweep_time) & (df_5m.index < end_time)]

            if len(post_sweep_df) < 5:
                continue

            for i in range(len(post_sweep_df) - 3):
                fvg_candles = post_sweep_df.iloc[i + 1:i + 4]
                if len(fvg_candles) < 3:
                    continue

                f1, f2, f3 = fvg_candles.iloc[0], fvg_candles.iloc[1], fvg_candles.iloc[2]
                idx = fvg_candles.index[2]
                atr_margin = atr.loc[idx] if idx in atr.index else atr.iloc[-1]

                if sweep_type == "bearish_sweep":
                    if f3['low'] > (f1['high'] + fvg_buffer) and f3['close'] < f3['open']:
                        fvg_top = f1['high']
                        fvg_bottom = f3['low']
                        entry = (fvg_top + fvg_bottom) / 2
                        sl = entry + atr_margin
                        tp = entry - (sl - entry) * rr_ratio
                        signals.append((idx, "bearish", round(tp, 5), round(sl, 5), round(entry, 5)))

                elif sweep_type == "bullish_sweep":
                    if f3['high'] < (f1['low'] - fvg_buffer) and f3['close'] > f3['open']:
                        fvg_bottom = f1['low']
                        fvg_top = f3['high']
                        entry = (fvg_top + fvg_bottom) / 2
                        sl = entry - atr_margin
                        tp = entry + (entry - sl) * rr_ratio
                        signals.append((idx, "bullish", round(tp, 5), round(sl, 5), round(entry, 5)))

        return signals

    @staticmethod
    def detect_fvg_liquidity_shifts_w_flipped_signals(
        df_5m: pd.DataFrame,
        df_15m: pd.DataFrame,
        group_size: int = 12,
        atr_period: int = 14,
        rr_ratio: float = 2.0,
        fvg_buffer: float = 0.00015
    ) -> List[Tuple[pd.Timestamp, pd.Timestamp, str, float, float, float]]:
        """
        Detects flipped signals: assumes FVG setup is a continuation not reversal.
        Returns: [(fvg_time, breaker_time, direction, TP, SL, Entry)]
        """
        signals = []

        if len(df_5m) < group_size * 3 or len(df_15m) < group_size:
            return signals

        # Calculate ATR on 5m
        tr = pd.concat([
            df_5m['high'] - df_5m['low'],
            (df_5m['high'] - df_5m['close'].shift()).abs(),
            (df_5m['low'] - df_5m['close'].shift()).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=atr_period).mean()

        # Liquidity sweep detection
        sweep_signals, prev_highs, prev_lows = [], [], []
        num_groups = len(df_15m) // group_size

        for g in range(1, num_groups):
            prev_group = df_15m.iloc[(g - 1) * group_size: g * group_size]
            curr_group = df_15m.iloc[g * group_size: (g + 1) * group_size]

            if len(prev_group) < group_size or len(curr_group) < 3:
                continue

            prev_high = prev_group['high'].max()
            prev_low = prev_group['low'].min()

            prev_highs.append(prev_high)
            prev_lows.append(prev_low)

            for i in range(2, len(curr_group)): 
                candle = curr_group.iloc[i]
                idx = curr_group.index[i]

                if (candle['high'] > prev_high and candle['close'] < candle['open']) or \
                (any(candle['high'] > x for x in prev_highs) and candle['close'] < candle['open']):
                    sweep_signals.append((idx, "bearish_sweep", prev_high))
                elif (candle['low'] < prev_low and candle['close'] > candle['open']) or \
                    (any(candle['low'] < x for x in prev_lows) and candle['close'] > candle['open']):
                    sweep_signals.append((idx, "bullish_sweep", prev_low))

        # Post-sweep breaker + FVG detection (flipped direction)
        for sweep_time, sweep_type, sweep_level in sweep_signals:
            end_time = sweep_time + pd.Timedelta(minutes=45)
            post_sweep_df = df_5m[(df_5m.index >= sweep_time) & (df_5m.index < end_time)]

            if len(post_sweep_df) < 6:
                continue

            for i in range(2, len(post_sweep_df) - 3):
                c1 = post_sweep_df.iloc[i - 2]
                c2 = post_sweep_df.iloc[i - 1]
                c3 = post_sweep_df.iloc[i]
                idx = post_sweep_df.index[i]
                atr_margin = atr.get(idx, atr.iloc[-1])

                breaker_body = abs(c3['close'] - c3['open'])
                breaker_range = c3['high'] - c3['low']
                body_ratio = breaker_body / breaker_range if breaker_range != 0 else 0

                if body_ratio < 0.4:
                    continue

                # --- Flipping the logic ---

                # Flipping bearish_sweep → long trade
                if sweep_type == "bearish_sweep":
                    if (
                        c3['close'] > c3['open'] and
                        c3['close'] > c1['high'] and
                        c3['low'] > c2['low'] and
                        c3['low'] <= sweep_level
                    ):
                        fvg_candles = post_sweep_df.iloc[i + 1:i + 4]
                        if len(fvg_candles) < 3:
                            continue

                        f1, f2, f3 = fvg_candles.iloc[0], fvg_candles.iloc[1], fvg_candles.iloc[2]
                        if (
                            f1['high'] + fvg_buffer < f3['low'] and
                            f3['close'] < f3['open']
                        ):
                            fvg_time = f3.name
                            breaker_time = idx
                            entry = (f1['high'] + f3['low']) / 2
                            sl = entry - atr_margin  # ← Inverted: SL below
                            tp = entry + (entry - sl) * rr_ratio  # ← TP above
                            signals.append((fvg_time, breaker_time, "bullish", round(tp, 5), round(sl, 5), round(entry, 5)))

                # Flipping bullish_sweep → short trade
                elif sweep_type == "bullish_sweep":
                    if (
                        c3['close'] < c3['open'] and
                        c3['close'] < c1['low'] and
                        c3['high'] < c2['high'] and
                        c3['high'] >= sweep_level
                    ):
                        fvg_candles = post_sweep_df.iloc[i + 1:i + 4]
                        if len(fvg_candles) < 3:
                            continue

                        f1, f2, f3 = fvg_candles.iloc[0], fvg_candles.iloc[1], fvg_candles.iloc[2]
                        if (
                            f1['low'] - fvg_buffer > f3['high'] and
                            f3['close'] > f3['open']
                        ):
                            fvg_time = f3.name
                            breaker_time = idx
                            entry = (f1['low'] + f3['high']) / 2
                            sl = entry + atr_margin  # ← Inverted: SL above
                            tp = entry - (sl - entry) * rr_ratio  # ← TP below
                            signals.append((fvg_time, breaker_time, "bearish", round(tp, 5), round(sl, 5), round(entry, 5)))

        return signals

    @staticmethod
    def detect_fvg_liquidity_shifts_w_breaker_dep(
        df_5m: pd.DataFrame,
        df_15m: pd.DataFrame,
        group_size: int = 12,
        atr_period: int = 14,
        rr_ratio: float = 2.0,
        fvg_buffer: float = 0.00015
    ) -> List[Tuple[pd.Timestamp, pd.Timestamp, str, float, float, float]]:
        """
        Detects trading signals using:
        1. Liquidity sweeps on 15m
        2. Breaker block on 5m with body filter & alignment to sweep
        3. Robust FVG pattern following breaker
        Returns: [(fvg_time, breaker_time, direction, TP, SL, Entry)]
        """
        signals = []

        if len(df_5m) < group_size * 3 or len(df_15m) < group_size:
            return signals

        # ATR on 5m
        tr = pd.concat([
            df_5m['high'] - df_5m['low'],
            (df_5m['high'] - df_5m['close'].shift()).abs(),
            (df_5m['low'] - df_5m['close'].shift()).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=atr_period).mean()

        # Detect liquidity sweeps
        sweep_signals, prev_highs, prev_lows = [], [], []
        num_groups = len(df_15m) // group_size

        for g in range(1, num_groups):
            prev_group = df_15m.iloc[(g - 1) * group_size: g * group_size]
            curr_group = df_15m.iloc[g * group_size: (g + 1) * group_size]

            if len(prev_group) < group_size or len(curr_group) < 3:
                continue

            prev_high = prev_group['high'].max()
            prev_low = prev_group['low'].min()

            prev_highs.append(prev_high)
            prev_lows.append(prev_low)

            for i in range(2, len(curr_group)):
                candle = curr_group.iloc[i]
                idx = curr_group.index[i]

                if (candle['high'] > prev_high and candle['close'] < candle['open']) or \
                (any(candle['high'] > x for x in prev_highs) and candle['close'] < candle['open']):
                    sweep_signals.append((idx, "bearish_sweep", prev_high))
                elif (candle['low'] < prev_low and candle['close'] > candle['open']) or \
                    (any(candle['low'] < x for x in prev_lows) and candle['close'] > candle['open']):
                    sweep_signals.append((idx, "bullish_sweep", prev_low))

        # Evaluate breakers and FVGs
        for sweep_time, sweep_type, sweep_level in sweep_signals:
            end_time = sweep_time + pd.Timedelta(minutes=45)
            post_sweep_df = df_5m[(df_5m.index >= sweep_time) & (df_5m.index < end_time)]

            if len(post_sweep_df) < 6:
                continue

            for i in range(1, len(post_sweep_df) - 3):
                c1 = post_sweep_df.iloc[i - 2]
                c2 = post_sweep_df.iloc[i - 1]
                c3 = post_sweep_df.iloc[i]
                idx = post_sweep_df.index[i]
                atr_margin = atr.get(idx, atr.iloc[-1])

                breaker_body = abs(c3['close'] - c3['open'])
                breaker_range = c3['high'] - c3['low']
                body_ratio = breaker_body / breaker_range if breaker_range != 0 else 0

                if body_ratio < 0.4:
                    continue  # Reject weak-bodied breakers

                if sweep_type == "bearish_sweep":
                    if (
                        c3['close'] > c3['open'] and
                        c3['close'] > c1['high'] and
                        c3['low'] > c2['low'] and
                        c3['low'] <= sweep_level  # touches or breaks into sweep zone
                    ):
                        fvg_candles = post_sweep_df.iloc[i + 1:i + 4]
                        if len(fvg_candles) < 3:
                            continue

                        f1, f2, f3 = fvg_candles.iloc[0], fvg_candles.iloc[1], fvg_candles.iloc[2]
                        if (
                            f1['high'] + fvg_buffer < f3['low'] and
                            f3['close'] < f3['open'] 
                            #f2['high'] < f3['low']
                        ):
                            fvg_time = f3.name
                            breaker_time = idx
                            entry = (f1['high'] + f3['low']) / 2
                            sl = entry + atr_margin
                            tp = entry - (sl - entry) * rr_ratio
                            signals.append((fvg_time, breaker_time, "bearish", round(tp, 5), round(sl, 5), round(entry, 5)))

                elif sweep_type == "bullish_sweep":
                    if (
                        c3['close'] < c3['open'] and
                        c3['close'] < c1['low'] and
                        c3['high'] < c2['high'] and
                        c3['high'] >= sweep_level
                    ):
                        fvg_candles = post_sweep_df.iloc[i + 1:i + 4]
                        if len(fvg_candles) < 3:
                            continue

                        f1, f2, f3 = fvg_candles.iloc[0], fvg_candles.iloc[1], fvg_candles.iloc[2]
                        if (
                            f1['low'] - fvg_buffer > f3['high'] and
                            f3['close'] > f3['open']
                            #f2['low'] > f3['high']
                        ):
                            fvg_time = f3.name
                            breaker_time = idx
                            entry = (f1['low'] + f3['high']) / 2
                            sl = entry - atr_margin
                            tp = entry + (entry - sl) * rr_ratio
                            signals.append((fvg_time, breaker_time, "bullish", round(tp, 5), round(sl, 5), round(entry, 5)))

        return signals

    @staticmethod
    def detect_fvg_liquidity_shifts_inverted(
        df_5m: pd.DataFrame,
        df_15m: pd.DataFrame,
        group_size: int = 12,
        atr_period: int = 14,
        rr_ratio: float = 2.0,
        fvg_buffer: float = 0.00015
    ) -> List[Tuple[pd.Timestamp, str, float, float, float]]:
        """
        Detects signals by inverting trade direction from sweep logic:
        - Bearish sweep → Bullish trade
        - Bullish sweep → Bearish trade
        """
        signals = []

        if len(df_5m) < group_size * 3 or len(df_15m) < group_size:
            return signals

        tr = pd.concat([
            df_5m['high'] - df_5m['low'],
            (df_5m['high'] - df_5m['close'].shift()).abs(),
            (df_5m['low'] - df_5m['close'].shift()).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=atr_period).mean()

        num_groups = len(df_15m) // group_size
        prev_highs, prev_lows = [], []

        sweep_signals = []
        for g in range(1, num_groups):
            prev_group = df_15m.iloc[(g - 1) * group_size: g * group_size]
            curr_group = df_15m.iloc[g * group_size: (g + 1) * group_size]

            if len(prev_group) < group_size or len(curr_group) < 3:
                continue

            prev_high = prev_group['high'].max()
            prev_low = prev_group['low'].min()

            prev_highs.append(prev_high)
            prev_lows.append(prev_low)

            for i in range(2, len(curr_group)):
                candle = curr_group.iloc[i]
                idx = curr_group.index[i]

                if (candle['high'] > prev_high and candle['close'] < candle['open']) or \
                (any(candle['high'] > x for x in prev_highs) and candle['close'] < candle['open']):
                    sweep_signals.append((idx, "bearish_sweep", prev_high))

                elif (candle['low'] < prev_low and candle['close'] > candle['open']) or \
                    (any(candle['low'] < x for x in prev_lows) and candle['close'] > candle['open']):
                    sweep_signals.append((idx, "bullish_sweep", prev_low))

        for sweep_time, sweep_type, sweep_level in sweep_signals:
            end_time = sweep_time + pd.Timedelta(minutes=45)
            post_sweep_df = df_5m[(df_5m.index >= sweep_time) & (df_5m.index < end_time)]

            if len(post_sweep_df) < 5:
                continue

            for i in range(len(post_sweep_df) - 3):
                fvg_candles = post_sweep_df.iloc[i + 1:i + 4]
                if len(fvg_candles) < 3:
                    continue

                f1, f2, f3 = fvg_candles.iloc[0], fvg_candles.iloc[1], fvg_candles.iloc[2]
                idx = fvg_candles.index[2]
                atr_margin = atr.loc[idx] if idx in atr.index else atr.iloc[-1]

                # Inverted logic
                if sweep_type == "bearish_sweep":
                    # Look for BULLISH FVG after bearish sweep
                    if f3['high'] < (f1['low'] - fvg_buffer) and f3['close'] > f3['open']:
                        fvg_bottom = f1['low']
                        fvg_top = f3['high']
                        entry = (fvg_top + fvg_bottom) / 2
                        sl = entry - atr_margin
                        tp = entry + (entry - sl) * rr_ratio
                        signals.append((idx, "bullish", round(tp, 5), round(sl, 5), round(entry, 5)))

                elif sweep_type == "bullish_sweep":
                    # Look for BEARISH FVG after bullish sweep
                    if f3['low'] > (f1['high'] + fvg_buffer) and f3['close'] < f3['open']:
                        fvg_top = f1['high']
                        fvg_bottom = f3['low']
                        entry = (fvg_top + fvg_bottom) / 2
                        sl = entry + atr_margin
                        tp = entry - (sl - entry) * rr_ratio
                        signals.append((idx, "bearish", round(tp, 5), round(sl, 5), round(entry, 5)))

        return signals
    
    @staticmethod
    def detect_fvg_liquidity_shifts_w_breaker(
        df_5m: pd.DataFrame,
        df_15m: pd.DataFrame,
        group_size: int = 12,
        atr_period: int = 14,
        rr_ratio: float = 2.0,
        fvg_buffer: float = 0.00015  # ~1.5 pips buffer for forex
    ) -> List[Tuple[pd.Timestamp, str, float, float, float]]:
        """
        Detect trading opportunities based on:
        1. Liquidity sweeps on 15m timeframe
        2. Breaker blocks on 5m timeframe opposite to sweep direction
        3. FVGs on 5m timeframe after breaker with buffer
        
        Returns signals with entry at 50% of FVG, TP, and SL levels.
        """
        signals = []
        
        # Validate data length
        if len(df_5m) < group_size * 3 or len(df_15m) < group_size:
            return signals

        # Calculate ATR on 5m
        high_5m = df_5m['high']
        low_5m = df_5m['low']
        close_5m = df_5m['close']
        prev_close_5m = close_5m.shift(1)
        tr = pd.concat([
            high_5m - low_5m,
            (high_5m - prev_close_5m).abs(),
            (low_5m - prev_close_5m).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=atr_period).mean()

        # Detect liquidity sweeps on 15m
        num_groups_15m = len(df_15m) // group_size
        sweep_signals = []
        prev_highs = []
        prev_lows = []

        for g in range(1, num_groups_15m):
            prev_group = df_15m.iloc[(g - 1) * group_size : g * group_size]
            curr_group = df_15m.iloc[g * group_size : (g + 1) * group_size]
            
            if len(prev_group) < group_size or len(curr_group) < 3:
                continue

            prev_high = prev_group['high'].max()
            prev_low = prev_group['low'].min()

            prev_highs.append(prev_high)
            prev_lows.append(prev_low)

            for i in range(3, len(curr_group)):
                c0 = curr_group.iloc[i - 3]
                c1 = curr_group.iloc[i - 2]
                c2 = curr_group.iloc[i - 1]
                c3 = curr_group.iloc[i]         # current candle
                idx = curr_group.index[i]

                # Confirm bearish sweep: new high and close bearish
                if (
                    c3['high'] > prev_high and
                    c3['close'] < c3['open'] and
                    all(prev_candle['high'] < c3['high'] for prev_candle in [ c1, c2])
                ):
                    sweep_signals.append((idx, "bearish_sweep", prev_high))

                # Confirm bullish sweep: new low and close bullish
                elif (
                    c3['low'] < prev_low and
                    c3['close'] > c3['open'] and
                    all(prev_candle['low'] > c3['low'] for prev_candle in [ c1, c2])
                ):
                    sweep_signals.append((idx, "bullish_sweep", prev_low))


        # Now check 5m for breaker and FVG after sweep
        for sweep in sweep_signals:
            sweep_time, sweep_type, sweep_level = sweep
            
            end_time = sweep_time + pd.Timedelta(minutes=45)
            post_sweep_df = df_5m[(df_5m.index >= sweep_time) & (df_5m.index < end_time)]
            if len(post_sweep_df) < 5:  # Need at least 5 candles for pattern
                continue
        # Look for breaker block (opposite to sweep direction)
            for i in range(2, len(post_sweep_df)):
                candle1 = post_sweep_df.iloc[i-2]
                candle2 = post_sweep_df.iloc[i-1]
                candle3 = post_sweep_df.iloc[i]
                idx = post_sweep_df.index[i]
                
                atr_margin = atr.loc[idx] if idx in atr.index else atr.iloc[-1]
                
                # For bearish sweep (look for bullish breaker)
                if sweep_type == "bearish_sweep":
                    # Breaker: strong bullish candle breaking structure
                    if (candle3['close'] > candle3['open'] and 
                        candle3['close'] > candle1['high'] and 
                        candle3['low'] > candle2['low']):
                        
                        # Now look for FVG after breaker with buffer
                        if i+3 < len(post_sweep_df):
                            fvg_candle1 = post_sweep_df.iloc[i+1]
                            fvg_candle2 = post_sweep_df.iloc[i+2]
                            fvg_candle3 = post_sweep_df.iloc[i+3]
                            
                            # FVG condition with buffer (candle3.low > candle1.high + buffer)
                            if (fvg_candle3['low'] > (fvg_candle1['high'] + fvg_buffer) and
                                #fvg_candle2['close'] < fvg_candle2['open'] and
                                fvg_candle3['close'] < fvg_candle3['open']):  # Confirming bearish
                                
                                # Calculate 50% entry of FVG range
                                fvg_top = fvg_candle1['high']
                                fvg_bottom = fvg_candle3['low']
                                entry = (fvg_top + fvg_bottom) / 2
                                
                                # SL above FVG top with ATR buffer
                                #sl = fvg_top + atr_margin
                                sl = entry + atr_margin
                                tp = entry - (sl - entry) * rr_ratio
                                
                                signals.append((
                                    fvg_candle3.name, 
                                    "bullish",          
                                    round(sl, 5), 
                                    round(sl, 5),
                                    round(entry, 5)
                                ))
                                break
                                
                # For bullish sweep (look for bearish breaker)
                elif sweep_type == "bullish_sweep":
                    # Breaker: strong bearish candle breaking structure
                    if (candle3['close'] < candle3['open'] and 
                        candle3['close'] < candle1['low'] and 
                        candle3['high'] < candle2['high']):
                        
                        # Now look for FVG after breaker with buffer
                        if i+3 < len(post_sweep_df):
                            fvg_candle1 = post_sweep_df.iloc[i+1]
                            fvg_candle2 = post_sweep_df.iloc[i+2]
                            fvg_candle3 = post_sweep_df.iloc[i+3]
                            
                            # FVG condition with buffer (candle3.high < candle1.low - buffer)
                            if (fvg_candle3['high'] < (fvg_candle1['low'] - fvg_buffer) and
                                #fvg_candle2['close'] > fvg_candle2['open'] and 
                                fvg_candle3['close'] > fvg_candle3['open']):  # Confirming bullish
                                
                                # Calculate 50% entry of FVG range
                                fvg_bottom = fvg_candle1['low']
                                fvg_top = fvg_candle3['high']
                                entry = (fvg_top + fvg_bottom) / 2
                                
                                # SL below FVG bottom with ATR buffer
                                #sl = fvg_bottom - atr_margin
                                sl = entry - atr_margin
                                tp = entry + (entry - sl) * rr_ratio
                                
                                signals.append((
                                    fvg_candle3.name, 
                                    "bearish", 
                                    round(sl, 5), 
                                    round(sl, 5),
                                    round(entry, 5)
                                ))
                                break
        return signals
    
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
    def detect_liquidity_sweeps(df: pd.DataFrame, lookback: int = 30, buffer: float = 0.0002, rr_ratio: float = 2.0) -> List[Tuple[pd.Timestamp, str, float, float]]:
        """Detect liquidity sweeps (false breakouts) and include SL and TP."""
        sweeps = []
        if len(df) < lookback + 1:
            return sweeps

        for i in range(lookback, len(df)):
            candle = df.iloc[i]
            high, low, open_, close = candle['high'], candle['low'], candle['open'], candle['close']
            timestamp = df.index[i]

            lookback_high_max = df.iloc[i - lookback:i]['high'].max()
            lookback_low_min = df.iloc[i - lookback:i]['low'].min()

            entry_price = close  # use candle close as entry

            # Bullish sweep → short setup
            if high > lookback_high_max and close < open_:
                sl = high + buffer
                tp = entry_price - (sl - entry_price) * rr_ratio
                sweeps.append((timestamp, "bullish_sweep_reversal_short", round(tp, 5), round(sl, 5)))

            # Bearish sweep → long setup
            elif low < lookback_low_min and close > open_:
                sl = low - buffer
                tp = entry_price + (entry_price - sl) * rr_ratio
                sweeps.append((timestamp, "bearish_sweep_reversal_long", round(tp, 5), round(sl, 5)))

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
            df_5M =  self._get_historical_data_w_tf(mt5.TIMEFRAME_M5)
            df_15M =  self._get_historical_data_w_tf(mt5.TIMEFRAME_M15)
            fvg_signals = self.detect_fvg_liquidity_shifts_v2(df_5m=df_5M,df_15m=df_15M,group_size=12,rr_ratio=rr_ratio)
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

    def _get_historical_data(self) -> pd.DataFrame:
        """Fetch historical data from MT5."""
        start_date = datetime.now(timezone.utc) - timedelta(days=self.days_back)
        end_date = datetime.now(timezone.utc)

        print("----------------------------")
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
    
    def _get_historical_data_w_tf(self,timeframe) -> pd.DataFrame:
        """Fetch historical data from MT5."""
        days_back = 230
        start_date = datetime.now(timezone.utc) - timedelta(days=days_back)
        end_date = datetime.now(timezone.utc)

        print(f"Fetching data for: {self.symbol}")
        print(f"Timeframe: {self.timeframe_to_string(timeframe)}")
        print(f"Start date: {start_date.strftime('%Y-%m-%d %H:%M')}")
        print(f"End date: {end_date.strftime('%Y-%m-%d %H:%M')}")

        #rates = mt5.copy_rates_from_pos(self.symbol, self.timeframe, 1, 1000)
        rates = mt5.copy_rates_range(self.symbol, timeframe, start_date, end_date)
        if rates is None or len(rates) == 0:
            error_message = f"Failed to fetch historical data for {self.symbol}. Error: {mt5.last_error()}, Rates count: {len(rates) if rates is not None else 'None'}"
            # mt5.shutdown() # Keep MT5 running for other simulations
            raise ValueError(error_message)
            
        df = pd.DataFrame(rates)
        print(f"Fetched {len(df)} rates for {self.symbol}.")
        df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
        df.set_index('time', inplace=True)
        return df
    
    def  _get_sub_timeframe_data(self, start_time: pd.Timestamp, end_time: pd.Timestamp, sub_timeframe: int) -> pd.DataFrame:
        """
        Fetches historical data for a smaller timeframe within a given range.
        """
        rates = mt5.copy_rates_range(self.symbol, sub_timeframe, start_time, end_time)
        #rates = mt5.copy_rates_from_pos(self.symbol, self.timeframe, 1, 1000)
        if rates is None or len(rates) == 0:
            return pd.DataFrame()

        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df = df.set_index('time')
        return df[['open', 'high', 'low', 'close', 'tick_volume']]
    
    def _get_daily_bias(self,symbol: str) -> Dict[str, List]:
        """
        Determine daily bias using SMC principles:
        - Draw on Liquidity (above highs or below lows)
        - Imbalance (price seeking unfilled FVGs)
        
        Returns:
            Dict[bias: List of [levels and final reason]]
        """
        if not mt5.initialize():
            raise RuntimeError("Failed to initialize MetaTrader5")

        now = datetime.utcnow()
        start = now - timedelta(days=120)  # Buffer to ensure full 100 D1 candles

        # Get D1 data
        df = self._get_historical_data_w_tf(mt5.TIMEFRAME_D1)
        df = df[-100:]  # Use last 100 candles

        current_close = df['close'].iloc[-1]
        current_high = df['high'].iloc[-1]
        current_low = df['low'].iloc[-1]

        # --- DRAW ON LIQUIDITY LOGIC ---
        max_high = df['high'].rolling(window=30).max().iloc[-1]
        min_low = df['low'].rolling(window=30).min().iloc[-1]

        seeking_buy_stops = current_close < max_high and (max_high - current_close) / current_close > 0.01
        seeking_sell_stops = current_close > min_low and (current_close - min_low) / current_close > 0.01

        liquidity_bias = None
        if seeking_buy_stops:
            liquidity_bias = "bullish"
            liquidity_reason = f"Draw on liquidity above 30-day high at {round(max_high, 2)}"
        elif seeking_sell_stops:
            liquidity_bias = "bearish"
            liquidity_reason = f"Draw on liquidity below 30-day low at {round(min_low, 2)}"
        else:
            liquidity_bias = "neutral"
            liquidity_reason = "No clear liquidity draw observed"

        # --- FAIR VALUE GAP (FVG) LOGIC ---
        fvg_bias = None
        fvg_reason = "No daily FVG detected"
        for i in range(len(df) - 2):
            c1, c2, c3 = df.iloc[i], df.iloc[i + 1], df.iloc[i + 2]
            if c3['low'] > c1['high']:  # Bearish imbalance (gap down)
                if current_close < c1['high']:
                    fvg_bias = "bullish"
                    fvg_reason = f"Price seeking up to fill FVG from {c1.name.date()}"
            elif c3['high'] < c1['low']:  # Bullish imbalance (gap up)
                if current_close > c1['low']:
                    fvg_bias = "bearish"
                    fvg_reason = f"Price seeking down to fill FVG from {c1.name.date()}"

        # --- RESOLUTION ---
        final_bias = "neutral"
        reasons = []

        if liquidity_bias == fvg_bias and liquidity_bias != "neutral":
            final_bias = liquidity_bias
            reasons = [max_high if final_bias == "bullish" else min_low, fvg_reason, liquidity_reason]
        #elif liquidity_bias != "neutral":
        #    final_bias = liquidity_bias
        #    reasons = [max_high if final_bias == "bullish" else min_low, liquidity_reason]
        #elif fvg_bias != "neutral":
        #    final_bias = fvg_bias
        #    reasons = [fvg_reason]

        mt5.shutdown()

        return (final_bias, reasons)
    
    def _process_signals(self, signals: List[Tuple[pd.Timestamp, str]], signal_type_prefix: str, rr_ratio: float, sl_points_fixed: int, enable_compounding: bool) -> List[Dict]:
        """Process trading signals and simulate trades with lot size calculation."""
        processed_trades = []
        
        if self.symbol_info is None:
            print("Cannot process signals: symbol_info is not available.")
            return processed_trades
        
        bias_direction,bias_reason = self.daily_bias
            
        value_per_point_per_lot = (self.symbol_info.trade_tick_value / self.symbol_info.trade_tick_size) * self.symbol_info.point if self.symbol_info.trade_tick_size > 0 else 0

        for signal_time, signal_details,tp,sl,entry in signals: # signal_details contains direction, e.g., "bullish" or "bearish_sweep_reversal_long"
            #if not self.in_london_newyork_window(signal_time):
            #    continue
            
            # only take trades offered the direction of our daily bias
            
            if bias_direction==signal_details:
                print(f"The daily bias was {bias_direction}")
                print(f"The signal direction was {signal_details}")
                exit(0)
                continue
                
            entry_idx = self.df.index.get_loc(signal_time)
            if entry_idx + 3 >= len(self.df): # Need at least 3 candles for entry fill check 
                continue
            
            entry_candle = self.df.iloc[entry_idx]
            #entry_price = entry_candle['close'] # Entry at close of signal candle
            entry_price = entry

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
                
            lot_size = self._calculate_lot_size(entry_price, sl)
            if lot_size == 0 or lot_size < self.symbol_info.volume_min : # Check if lot size is valid
                print(f"Skipping trade at {signal_time} due to invalid lot size: {lot_size}")
                continue
            
            # Check if price fills the entry zone (next 3 candles)
            # For a buy, we need low of future candles to touch entry_price.
            # For a sell, we need high of future candles to touch entry_price.
            filled = False
            actual_entry_price = entry_price # Assume entry at signal candle close initially
            #entry_fill_time = signal_time - timedelta(hours=4)
            entry_fill_time = signal_time
            #entry_fill_time = breaker_time


            # Check for fill within next 3 candles (or immediate fill on signal candle itself)
            if (signal_direction == "bullish" and entry_candle['low'] <= entry_price and entry_candle['high'] >= entry_price) or \
            (signal_direction == "bearish" and entry_candle['low'] <= entry_price and entry_candle['high'] >= entry_price):
                filled = True # Filled on the signal candle itself

            if not filled:
                future_candles_for_fill = self.df.iloc[entry_idx + 1 : entry_idx + 1 + 30] # Look 3 candles ahead for fill
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
                
                # --- SIMPLIFIED: Only win/loss (TP or SL), no timeouts or early exits ---
                start_tracking_idx = self.df.index.get_loc(entry_fill_time) + 1
                monitoring_window_end_idx = min(start_tracking_idx + 12, len(self.df))

                timeout_start_time = self.df.index[start_tracking_idx]
                #timeout_end_time = self.df.index[len(self.df) - 1] + pd.Timedelta(self.timeframe, unit='s')
                timeout_end_time = self.df.index[len(self.df) - 1]

                minute_data = self._get_sub_timeframe_data(timeout_start_time, timeout_end_time, mt5.TIMEFRAME_M1)
                high = int(0)
                low = int(0)

                for _, m1_candle in minute_data.iterrows():
                    m1_high, m1_low = m1_candle['high'], m1_candle['low']

                    if signal_direction == "bullish":  # Buy trade
                        if m1_high >= tp:
                            result = "win"
                            high = m1_high
                            low = m1_low
                            exit_price = tp
                            pnl_points = (tp - actual_entry_price) / self.symbol_info.point
                            exit_time = m1_candle.name
                            break
                        elif m1_low <= sl:
                            result = "loss"
                            high = m1_high
                            low = m1_low
                            exit_price = sl
                            pnl_points = (sl - actual_entry_price) / self.symbol_info.point
                            exit_time = m1_candle.name
                            break

                    elif signal_direction == "bearish":  # Sell trade
                        if m1_low <= tp:
                            result = "win"
                            high = m1_high
                            low = m1_low
                            exit_price = tp
                            pnl_points = (actual_entry_price - tp) / self.symbol_info.point
                            exit_time = m1_candle.name
                            break
                        elif m1_high >= sl:
                            result = "loss"
                            high = m1_high
                            low = m1_low
                            exit_price = sl
                            pnl_points = (actual_entry_price - sl) / self.symbol_info.point
                            exit_time = m1_candle.name
                            break


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

    def  calculate_sl(self,trade_type:str, lookback:int=20, buffer_pips:int=2):
        """
        Calculates SL based on recent highs/lows.
        For 'sell' trades: SL = highest high over last `lookback` candles + buffer.
        For 'buy' trades:  SL = lowest low over last `lookback` candles - buffer.
        """
        recent_candles = self.df.iloc[-lookback:]  # last N candles

        if recent_candles.empty:
            raise ValueError("No data in recent_candles to calculate SL.")

        if trade_type.lower() == 'sell':
            if 'high' not in recent_candles.columns:
                raise KeyError("DataFrame 'recent_candles' must contain a 'high' column.")
            highest_high = recent_candles['high'].max()
            stop_loss = highest_high + (buffer_pips * self.symbol_info.point)
            return round(stop_loss, getattr(self.symbol_info, 'digits', 5))


        elif trade_type.lower() == 'buy':
            if 'low' not in recent_candles.columns:
                raise KeyError("DataFrame 'recent_candles' must contain a 'low' column.")
            lowest_low = recent_candles['low'].min()
            stop_loss = lowest_low - (buffer_pips * self.symbol_info.point)
            return round(stop_loss, getattr(self.symbol_info, 'digits', 5))

        else:
            raise ValueError("Invalid trade type. Use 'buy' or 'sell'.")

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
    #timeframes_to_test = [mt5.TIMEFRAME_M5, mt5.TIMEFRAME_M15]
    timeframes_to_test = [mt5.TIMEFRAME_M5]
    
    all_results_summary = []

    for sym in symbols_to_test:
        for tf_val in timeframes_to_test:
            print(f"\n{'='*30} SIMULATING: {sym} - {TradingSimulator.timeframe_to_string(tf_val)} {'='*30}")
            #try:
            sim = TradingSimulator(
                symbol=sym, 
                timeframe=tf_val, 
                days_back=230 ,             # How many days of data
                account_balance=10000,    # Starting balance
                risk_per_trade=0.01        # Risk 1% per trade
            )
            
            trades_df = sim.simulate_trades(
                use_fvg=True, 
                use_sweeps=False, 
                rr_ratio=3.0,             # Risk:Reward Ratio
                sl_points_fixed=400,      # Stop loss in points (e.g., for EURUSD 150 points = 15 pips if point=0.0001)
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

            #except (RuntimeError, ValueError, Exception) as e:
            #    print(f"!!! ERROR during simulation for {sym} - {TradingSimulator.timeframe_to_string(tf_val)}: {e}")
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
        