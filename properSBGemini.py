import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
import time
from typing import List, Dict, Optional, Tuple

# --- Configuration --- (ADJUST THESE CAREFULLY) ---
SYMBOL = "UT100Roll" 
SYMBOLS = ["UT100Roll", "US500Roll", "XAUUSD"] 

TIMEFRAME_MT5 = mt5.TIMEFRAME_M5  # MT5 Timeframe Constant
TIMEFRAME_SECONDS = 5 * 60       # Timeframe in seconds (for M5; M1=60, M15=15*60, H1=3600*1)
DAYS_BACK_FOR_INITIAL_DATA = 5 # Days of data to fetch initially for indicators (adjust based on ATR period, lookbacks)
CANDLES_FOR_SIGNAL_DATA = 150 # Number of recent candles to use for signal detection (should be enough for lookbacks)

# Trading Parameters
ACCOUNT_BALANCE_FOR_RISK_CALC = 10000 # Specify balance to use for risk calculation (or fetch live: mt5.account_info().balance)
RISK_PER_TRADE = 0.01             # Risk 1% of account per trade
RR_RATIO = 3.0                    # Risk:Reward Ratio
SL_POINTS_FIXED = 150             # Stop loss in points (e.g., for EURUSD 150 points = 15 pips if point=0.0001)
                                  # For XAUUSD 150 points = $1.50 if point=0.01. ADJUST BASED ON SYMBOL!
MAX_TRADE_DURATION_CANDLES = 1   # Max number of candles a trade can stay open before timeout

# Strategy Toggles
USE_FVG = True
USE_SWEEPS = True
LOOKBACK_SWEEPS = 20 # Lookback period for liquidity sweeps

# Session Filtering (UTC hours)
SESSION_FILTER_ENABLED = True
LONDON_OPEN_HOUR_UTC = 7
NY_CLOSE_HOUR_UTC = 21 # Example: trade from London open to NY close
# More granular: (8 <= hour < 12) or (13 <= hour < 17)

# MT5 Connection Details (Fill if needed, or ensure MT5 terminal is running and logged in)
MT5_ACCOUNT = 00000000      # Replace with your MT5 account number
MT5_PASSWORD = "YOUR_PASSWORD" # Replace with your MT5 password
MT5_SERVER = "YOUR_SERVER"    # Replace with your MT5 server name
MAGIC_NUMBER = 123456         # Magic number for orders placed by this EA

RUN_INTERVAL_SECONDS = 10 # How often to check for new signals/manage trades (should be less than timeframe)

# --- LiveTrader Class ---
class LiveTrader:
    def __init__(self, symbol: str, timeframe_mt5: int, timeframe_seconds: int,
                 account_balance_for_risk: float, risk_per_trade: float,
                 rr_ratio: float, sl_points_fixed: int, max_trade_duration_candles: int,
                 use_fvg: bool, use_sweeps: bool, lookback_sweeps: int, magic_number: int):

        self.symbol = symbol
        self.timeframe_mt5 = timeframe_mt5
        self.timeframe_seconds = timeframe_seconds
        self.account_balance_for_risk = account_balance_for_risk # Can be updated live if needed
        self.risk_per_trade = risk_per_trade
        self.rr_ratio = rr_ratio
        self.sl_points_fixed = sl_points_fixed
        self.max_trade_duration_seconds = max_trade_duration_candles * timeframe_seconds
        self.use_fvg = use_fvg
        self.use_sweeps = use_sweeps
        self.lookback_sweeps = lookback_sweeps
        self.magic_number = magic_number

        self.symbol_info = None
        self.last_candle_timestamp_processed = None # To avoid acting on the same candle multiple times
        self.active_trade_ticket = None
        self.active_trade_entry_time = None # Unix timestamp (seconds)

        if not self._initialize_mt5():
            raise ConnectionError("Failed to initialize MetaTrader 5. Ensure terminal is running and credentials are correct.")

        print("LiveTrader initialized successfully.")

    def _initialize_mt5(self) -> bool:
        if not mt5.initialize():
            print(f"initialize() failed, error code = {mt5.last_error()}")
            # Attempt login if initialization failed (often needed if script runs standalone)
            # Ensure your MT5 terminal is running and logged in, or provide credentials
            # print(f"Attempting login to account {MT5_ACCOUNT} on server {MT5_SERVER}...")
            # if mt5.login(MT5_ACCOUNT, password=MT5_PASSWORD, server=MT5_SERVER):
            #     print("Login successful.")
            #     if not mt5.initialize(): # Try initializing again after login
            #         print(f"Second initialize() failed after login, error code = {mt5.last_error()}")
            #         return False
            # else:
            #     print(f"Login failed, error code = {mt5.last_error()}")
            #     return False
            return False # If basic initialize fails and no login provided/successful

        print(f"MetaTrader 5 initialized: Version {mt5.version()}")
        acc_info = mt5.account_info()
        if acc_info:
            print(f"Account Info: Login: {acc_info.login}, Balance: {acc_info.balance} {acc_info.currency}, Server: {acc_info.server}")
            # self.account_balance_for_risk = acc_info.balance # Optionally use live balance
        else:
            print(f"Failed to get account info, error code = {mt5.last_error()}")
            # return False # Decide if this is critical

        self.symbol_info = mt5.symbol_info(self.symbol)
        if self.symbol_info is None:
            print(f"Failed to get symbol info for {self.symbol}, error: {mt5.last_error()}")
            return False
        if not self.symbol_info.visible:
            print(f"Symbol {self.symbol} is not visible in MarketWatch, trying to enable.")
            if not mt5.symbol_select(self.symbol, True):
                print(f"Failed to enable symbol {self.symbol}, error: {mt5.last_error()}")
                return False
            self.symbol_info = mt5.symbol_info(self.symbol) # Refresh info
            if not self.symbol_info or not self.symbol_info.visible:
                 print(f"Still cannot see symbol {self.symbol} after trying to enable.")
                 return False
        return True

    def _estimate_target_profit(self, position):
        """Estimate the profit in USD if TP is hit."""
        # Get symbol info for contract size
        symbol_info = mt5.symbol_info(position.symbol)
        if not symbol_info:
            print(f"Failed to get symbol info for {position.symbol}")
            return None

        contract_size = symbol_info.trade_contract_size  # Usually 100,000 for Forex
        entry_price = position.price_open
        tp_price = position.tp
        volume = position.volume
        point = symbol_info.point

        if tp_price == 0:
            print("No Take Profit set.")
            return None

        # Calculate price difference
        price_diff = tp_price - entry_price
        if position.type == mt5.ORDER_TYPE_SELL:
            price_diff = entry_price - tp_price  # Invert for SELL

        estimated_profit = price_diff * volume * contract_size
        return estimated_profit

    def _get_historical_data(self, num_candles: int) -> Optional[pd.DataFrame]:
        """Fetches the last N candles."""
        try:
            rates = mt5.copy_rates_from_pos(self.symbol, self.timeframe_mt5, 0, num_candles)
            if rates is None or len(rates) == 0:
                print(f"Failed to fetch historical data for {self.symbol}. Error: {mt5.last_error()}")
                return None
            
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s', utc=True) # Ensure UTC
            df.set_index('time', inplace=True)
            return df
        except Exception as e:
            print(f"Error fetching historical data: {e}")
            return None

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
        

    # --- Signal Detection Methods (Copied from backtester) ---
    @staticmethod
    def detect_fvg(df: pd.DataFrame) -> List[Tuple[pd.Timestamp, str]]:
        fvg = []
        if len(df) < 3: return fvg
        for i in range(2, len(df)):
            candle1_high = df.iloc[i-2]['high']
            candle1_low = df.iloc[i-2]['low']
            candle3_high = df.iloc[i]['high']
            candle3_low = df.iloc[i]['low']
            if candle3_low > candle1_high:
                fvg.append((df.index[i], "bullish"))
            elif candle3_high < candle1_low:
                fvg.append((df.index[i], "bearish"))
        return fvg

    @staticmethod
    def detect_liquidity_sweeps(df: pd.DataFrame, lookback: int) -> List[Tuple[pd.Timestamp, str]]:
        sweeps = []
        if len(df) < lookback + 1: return sweeps
        for i in range(lookback, len(df)):
            current_candle = df.iloc[i]
            lookback_high_max = df.iloc[i-lookback:i]['high'].max()
            lookback_low_min = df.iloc[i-lookback:i]['low'].min()
            if current_candle['high'] > lookback_high_max and current_candle['close'] < current_candle['open']:
                sweeps.append((df.index[i], "bullish_sweep_reversal_short"))
            elif current_candle['low'] < lookback_low_min and current_candle['close'] > current_candle['open']:
                sweeps.append((df.index[i], "bearish_sweep_reversal_long"))
        return sweeps

    @staticmethod
    def in_trading_session(ts: pd.Timestamp) -> bool:
        if not SESSION_FILTER_ENABLED:
            return True
        # Ensure timestamp is UTC before extracting hour
        ts_utc = ts if ts.tzinfo == timezone.utc else ts.tz_convert(timezone.utc)
        hour = ts_utc.hour
        # Example: Trade from London open (approx 7-8 UTC) to near NY close (approx 21 UTC)
        # Refined: (8 <= hour < 12) or (13 <= hour < 17)
        return (LONDON_OPEN_HOUR_UTC <= hour < NY_CLOSE_HOUR_UTC)


    def _place_trade(self, signal_type: str, signal_direction: str, entry_price: float, sl_price: float, tp_price: float) -> Optional[int]:
        """Places a trade via MT5."""
        lot_size = self._calculate_lot_size(entry_price, sl_price)
        if lot_size <= 0 or lot_size < self.symbol_info.volume_min: # Ensure lot size is valid
            print(f"Invalid lot size: {lot_size}. Minimum: {self.symbol_info.volume_min}. Skipping trade.")
            return None

        order_type = None
        if signal_direction == "bullish": # Buy
            order_type = mt5.ORDER_TYPE_BUY
            price = mt5.symbol_info_tick(self.symbol).ask # Use current ask for buy
        elif signal_direction == "bearish": # Sell
            order_type = mt5.ORDER_TYPE_SELL
            price = mt5.symbol_info_tick(self.symbol).bid # Use current bid for sell
        else:
            print(f"Unknown signal direction for trade placement: {signal_direction}")
            return None

        deviation = 20 # Slippage deviation in points

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": lot_size,
            "type": order_type,
            "price": price, # For market orders, this is current market price
            #"sl": sl_price,
            "tp": tp_price,
            "deviation": deviation,
            "magic": self.magic_number,
            "comment": "Chopped by mabera",
            "type_time": mt5.ORDER_TIME_GTC, # Good Till Cancelled
            "type_filling": mt5.ORDER_FILLING_IOC # Immediate Or Cancel (or try FOK)
                           # Check your broker's allowed filling types mt5.symbol_info(self.symbol).filling_mode
        }

        print(f"Attempting to place {signal_direction} trade for {self.symbol} at ~{price}, Lots: {lot_size}, SL: {sl_price}, TP: {tp_price}")
        
        # Send order
        result = mt5.order_send(request)

        if result is None:
            print(f"order_send failed, error code = {mt5.last_error()}")
            return None
        
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"Order placed successfully. Ticket: {result.order}, Position: {result.deal}")
            return int(result.order) # Return order ticket
        else:
            print(f"Order failed. Retcode: {result.retcode} - {result.comment}")
            print(f"Request details: {result.request}")
            return None

    def _close_trade(self, position_ticket: int, position_type, volume: float, comment: str):
        """Closes an open position by its ticket."""
        print(f"Attempting to close position ticket {position_ticket} ({comment})...")
        tick = mt5.symbol_info_tick(self.symbol)
        if tick is None:
            print(f"Failed to get tick for {self.symbol} to close trade.")
            return False

        price = tick.bid if position_type == mt5.ORDER_TYPE_BUY else tick.ask # Closing buy at bid, sell at ask
        opposite_order_type = mt5.ORDER_TYPE_SELL if position_type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        deviation = 20

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": volume,
            "type": opposite_order_type,
            "position": position_ticket, # Specify the position ticket to close
            "price": price,
            "deviation": deviation,
            "magic": self.magic_number,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC # Or FOK, check allowed types
        }
        # Ensure a compatible filling type (same logic as _place_trade)
        #allowed_filling_types = self.symbol_info.filling_modes
        #if mt5.ORDER_FILLING_IOC in allowed_filling_types:
        #    request["type_filling"] = mt5.ORDER_FILLING_IOC
        #elif mt5.ORDER_FILLING_FOK in allowed_filling_types:
        #     request["type_filling"] = mt5.ORDER_FILLING_FOK
        #elif len(allowed_filling_types) > 0:
        #     request["type_filling"] = allowed_filling_types[0]
        #else:
        #    request["type_filling"] = mt5.ORDER_FILLING_FOK


        result = mt5.order_send(request)

        if result is None:
            print(f"Close order_send failed, error code = {mt5.last_error()}")
            return False
        
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"Position {position_ticket} closed successfully. Result comment: {result.comment}")
            return True
        else:
            print(f"Failed to close position {position_ticket}. Retcode: {result.retcode} - {result.comment}")
            print(f"Request details: {result.request}")
            return False

    def _get_open_position(self):
        """Checks for an open position on the symbol managed by this EA."""
        positions = mt5.positions_get(symbol=self.symbol)
        if positions is None:
            if mt5.last_error() != mt5.RES_S_OK: # RES_S_OK means "no error", so positions is empty
                 print(f"Error getting positions: {mt5.last_error()}")
            return None
        
        for pos in positions:
            if pos.magic == self.magic_number:
                return pos # Return the first position found with our magic number
        return None

    def calculate_stops(self, signal): # signal is your signal dictionary
        # Get the current tick (bid/ask)
        tick = mt5.symbol_info_tick(self.symbol)
        if not tick or tick.bid == 0.0 or tick.ask == 0.0:
            print(f"Error: Could not retrieve valid tick data for {self.symbol}")
            return None, None, None, None # entry_price, sl_price, tp_price, trade_direction

        current_bid = tick.bid
        current_ask = tick.ask

        # --- Determine trade direction based on your signal ---
        trade_direction = None
        if signal["type"] == "FVG":
            if signal["direction_detail"] == "bullish": trade_direction = "bullish"
            elif signal["direction_detail"] == "bearish": trade_direction = "bearish"
        elif signal["type"] == "SWEEP":
            if signal["direction_detail"] == "bearish_sweep_reversal_long": trade_direction = "bullish"
            elif signal["direction_detail"] == "bullish_sweep_reversal_short": trade_direction = "bearish"
        
        if not trade_direction:
            print("Error: Trade direction could not be determined from signal.")
            return None, None, None, None

        # --- Calculate desired distances in price units ---
        # Your desired SL distance from your entry point
        user_desired_sl_distance_price = self.sl_points_fixed * self.symbol_info.point
        user_desired_tp_distance_price = user_desired_sl_distance_price * self.rr_ratio


        # Broker's minimum stop distance from relevant market price, in price units
        broker_min_stop_offset_price = self.symbol_info.trade_stops_level  * self.symbol_info.point
        # It's often good to add a small buffer (e.g., 1-2 points) to be safe,
        # as sometimes `trade_stops_level` is the absolute minimum.
        # safety_buffer_price = 1 * self.point 
        # broker_min_stop_offset_price += safety_buffer_price

        sl_price = 0.0
        tp_price = 0.0
        entry_price = 0.0 # This will be the price for the TradeRequest

        if trade_direction == "bullish": # BUY order
            entry_price = current_ask # Market buy order fills at Ask

            # 1. Calculate SL based on your desired distance from entry
            ideal_sl = entry_price - user_desired_sl_distance_price
            
            # 2. Ensure SL respects broker's minimum distance: SL must be <= current_bid - broker_min_offset
            #    (SL for BUY is a SELL STOP, triggered by BID price)
            min_allowable_sl_from_broker = current_bid - broker_min_stop_offset_price
            sl_price = min(ideal_sl, min_allowable_sl_from_broker)
            
            # 3. Ensure SL is actually below the entry price (vital if spread + min_stop_offset is large)
            sl_price = min(sl_price, entry_price - self.symbol_info.point) # At least one point away


            # 4. Calculate TP based on your desired distance from entry
            ideal_tp = entry_price + user_desired_tp_distance_price

            # 5. Ensure TP respects broker's minimum distance: TP must be >= current_ask + broker_min_offset
            #    (TP for BUY is a SELL LIMIT, set relative to ASK, triggered by BID)
            min_allowable_tp_from_broker = current_ask + broker_min_stop_offset_price
            tp_price = max(ideal_tp, min_allowable_tp_from_broker)

            # 6. Ensure TP is actually above the entry price
            tp_price = max(tp_price, entry_price + self.symbol_info.point)


        elif trade_direction == "bearish": # SELL order
            entry_price = current_bid # Market sell order fills at Bid

            # 1. Calculate SL based on your desired distance from entry
            ideal_sl = entry_price + user_desired_sl_distance_price

            # 2. Ensure SL respects broker's minimum distance: SL must be >= current_ask + broker_min_offset
            #    (SL for SELL is a BUY STOP, triggered by ASK price)
            min_allowable_sl_from_broker = current_ask + broker_min_stop_offset_price
            sl_price = max(ideal_sl, min_allowable_sl_from_broker)

            # 3. Ensure SL is actually above the entry price
            sl_price = max(sl_price, entry_price + self.symbol_info.point)


            # 4. Calculate TP based on your desired distance from entry
            ideal_tp = entry_price - user_desired_tp_distance_price
            
            # 5. Ensure TP respects broker's minimum distance: TP must be <= current_bid - broker_min_offset
            #    (TP for SELL is a BUY LIMIT, set relative to BID, triggered by ASK)
            min_allowable_tp_from_broker = current_bid - broker_min_stop_offset_price
            tp_price = min(ideal_tp, min_allowable_tp_from_broker)

            # 6. Ensure TP is actually below the entry price
            tp_price = min(tp_price, entry_price - self.symbol_info.point)

        # Round to the symbol's digit precision
        sl_price = round(sl_price, self.digits)
        tp_price = round(tp_price, self.digits)
        entry_price = round(entry_price, self.digits) # Good practice for the order request

        # --- Final Sanity Checks ---
        if trade_direction == "bullish":
            if sl_price >= entry_price:
                print(f"CRITICAL WARNING (Bullish): SL {sl_price} is not below entry {entry_price}. Spread or min_stop_level too large. No trade.")
                return None, None, None, None
            if tp_price <= entry_price:
                    print(f"CRITICAL WARNING (Bullish): TP {tp_price} is not above entry {entry_price}. No trade.")
                    return None, None, None, None
        elif trade_direction == "bearish":
            if sl_price <= entry_price:
                print(f"CRITICAL WARNING (Bearish): SL {sl_price} is not above entry {entry_price}. Spread or min_stop_level too large. No trade.")
                return None, None, None, None
            if tp_price >= entry_price:
                print(f"CRITICAL WARNING (Bearish): TP {tp_price} is not below entry {entry_price}. No trade.")
                return None, None, None, None
        
        return entry_price, sl_price, tp_price, trade_direction

    def run_trade_logic(self):
        """Main trading logic loop."""
        print(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')}] Running trade logic for {self.symbol}...")

        # --- 1. Get Open Position ---
        open_position = self._get_open_position()

        if open_position:
            self.active_trade_ticket = open_position.ticket
            # If self.active_trade_entry_time was not set when position was opened, try to get it now
            if self.active_trade_entry_time is None:
                self.active_trade_entry_time = open_position.time # This is Unix timestamp
            
            print(f"Active position found: Ticket {open_position.ticket}, Type: {'BUY' if open_position.type == mt5.ORDER_TYPE_BUY else 'SELL'}, Volume: {open_position.volume}, Open Time: {open_position.time}, Price: {open_position.price_open}")
            
            # --- 2. Manage Open Position (Timeout) ---
            if self.active_trade_entry_time:
                current_time_seconds = int(datetime.now(timezone.utc).timestamp())
                adjusted_entry_time = self.active_trade_entry_time - 3 * 60 * 60 

                duration_seconds = current_time_seconds - adjusted_entry_time
                print(f"The entry time {adjusted_entry_time}")
                print(f"The current time in seconds {current_time_seconds}")
                print(f"The duration {duration_seconds}")
                print(f"The max trade duration {self.max_trade_duration_seconds}")
                if duration_seconds > self.max_trade_duration_seconds:
                    print(f"Trade Timeout! Position {open_position.ticket} open for {duration_seconds}s (max: {self.max_trade_duration_seconds}s). Closing.")
                    estimated_profit = self._estimate_target_profit(open_position)
                    if estimated_profit is not None:
                        print(f"Estimated target profit if TP hit: ${estimated_profit:.2f}")
                        if open_position.profit < 0 and abs(open_position.profit) > estimated_profit:
                            print(f"Loss exceeds potential profit. Consider closing trade.")
                            if self._close_trade(open_position.ticket, open_position.type, open_position.volume, "Timeout closure"):
                                self.active_trade_ticket = None
                                self.active_trade_entry_time = None
                            return 
            else:
                print(f"Warning: Active trade {open_position.ticket} found, but entry time unknown for timeout check. Storing current time as approx entry time.")
                self.active_trade_entry_time = open_position.time # Store it now

        else: # No active position for our magic number
            self.active_trade_ticket = None
            self.active_trade_entry_time = None
            
            # --- 3. Fetch Data for New Signals ---
            df = self._get_historical_data(CANDLES_FOR_SIGNAL_DATA)
            if df is None or df.empty:
                print("Could not get data for signal detection.")
                return

            latest_candle_time = df.index[-1]
            if self.last_candle_timestamp_processed == latest_candle_time:
                print(f"Latest candle {latest_candle_time} already processed. Waiting for new candle.")
                return # No new candle yet

            print(f"New candle detected: {latest_candle_time}. Processing signals...")
            self.last_candle_timestamp_processed = latest_candle_time # Mark as processed for next cycle

            # --- 4. Generate Signals ---
            all_signals = []
            if self.use_fvg:
                fvg_signals = self.detect_fvg(df)
                for ts, direction in fvg_signals:
                    all_signals.append({"time": ts, "type": "FVG", "direction_detail": direction})
            
            if self.use_sweeps:
                sweep_signals = self.detect_liquidity_sweeps(df, self.lookback_sweeps)
                for ts, direction_detail in sweep_signals:
                     all_signals.append({"time": ts, "type": "SWEEP", "direction_detail": direction_detail})
            
            if not all_signals:
                print("No new signals generated.")
                return

            # Sort signals by time (though they should be mostly sorted if from same df pass)
            all_signals.sort(key=lambda x: x["time"])
            
            # Consider only the latest signal on the most recent candle(s)
            # We are interested in signals on the just-closed candle, or one before if processing is delayed
            # The `detect_fvg` and `detect_liquidity_sweeps` give timestamp of the candle where pattern completes.
            # Let's take the most recent valid signal
            
            latest_signal_to_consider = None
            for signal in reversed(all_signals): # Check newest first
                # Only consider signals from the latest candle or one before
                if signal["time"] >= df.index[-2]: # Signal on last or second-to-last candle
                    if self.in_trading_session(signal["time"]):
                        latest_signal_to_consider = all_signals[len(all_signals)-2] # we will use the second last signal
                        break # Take the newest valid signal
                    else:
                        print(f"Signal at {signal['time']} is outside trading session. Ignoring.")
            
            if not latest_signal_to_consider:
                print("No valid signals in trading session on recent candles.")
                return

            signal = latest_signal_to_consider
            print(f"Processing signal: Time: {signal['time']}, Type: {signal['type']}, Detail: {signal['direction_detail']}")

            # --- 5. Determine Trade Parameters from Signal ---
            signal_candle_data = df.loc[signal["time"]]
            entry_price = signal_candle_data['close'] # Entry at close of signal candle

            entry_price, sl_price, tp_price, trade_direction = self.calculate_stops(signal)

            if( entry_price !=None and sl_price!=None and tp_price != None and trade_direction!=None):
                if len(mt5.positions_get()) < 2:
                    signal_time = pd.to_datetime(signal["time"])
                    now_utc = datetime.now(timezone.utc)

                    # Avoid placing trade if signal is too recent (same minute or very fresh)
                    delta = abs(now_utc - signal_time)
                    if delta.total_seconds() > 60 and now_utc.minute != 5:
                        opened_order_ticket = self._place_trade(
                            signal_type=f"{signal['type']}_{signal['direction_detail']}",
                            signal_direction=trade_direction,
                            entry_price=entry_price,
                            sl_price=sl_price,
                            tp_price=tp_price
                        )
                        if opened_order_ticket:
                            self.active_trade_ticket = opened_order_ticket
                            self.active_trade_entry_time = int(now_utc.timestamp())
                            print(f"Trade initiated. Ticket: {self.active_trade_ticket}, Entry Time: {now_utc}")
                    else:
                        print("Signal too recent or current time is restricted (e.g. 5th minute).")
                else:
                    print("We have maxed out our limit of concurrent trades.")


    def stop(self):
        print("Shutting down LiveTrader and MetaTrader 5 connection.")
        mt5.shutdown()

# --- Main Execution ---
if __name__ == "__main__":
    print("Starting Live Trading Script - EXTREME CAUTION ADVISED - USE DEMO ACCOUNT")
    print("Ensure MetaTrader 5 terminal is running and logged into the correct account.")
    print(f"Trading Symbol: {SYMBOL}, Timeframe: M{TIMEFRAME_SECONDS//60}")
    print(f"Risk per trade: {RISK_PER_TRADE*100}%, SL Points: {SL_POINTS_FIXED}, RR Ratio: {RR_RATIO}")
    print("Press Ctrl+C to stop the script.")

    trader = None
    while True:
        try:
            trader = LiveTrader(
                symbol=SYMBOL,
                timeframe_mt5=TIMEFRAME_MT5,
                timeframe_seconds=TIMEFRAME_SECONDS,
                account_balance_for_risk=ACCOUNT_BALANCE_FOR_RISK_CALC,
                risk_per_trade=RISK_PER_TRADE,
                rr_ratio=RR_RATIO,
                sl_points_fixed=SL_POINTS_FIXED,
                max_trade_duration_candles=MAX_TRADE_DURATION_CANDLES,
                use_fvg=USE_FVG,
                use_sweeps=USE_SWEEPS,
                lookback_sweeps=LOOKBACK_SWEEPS,
                magic_number=MAGIC_NUMBER
            )
            
            
            trader.run_trade_logic()
            print(f"Waiting for {RUN_INTERVAL_SECONDS} seconds before next check...")
            time.sleep(RUN_INTERVAL_SECONDS)

        except ConnectionError as e:
            print(f"Connection Error: {e}")
        except ValueError as e:
            print(f"Value Error: {e}")
        except KeyboardInterrupt:
            print("Script interrupted by user (Ctrl+C).")
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if trader:
                trader.stop()
            else: # If trader object was not created, ensure MT5 is shutdown if it was initialized partially
                mt5.shutdown()
            print("Script terminated.")