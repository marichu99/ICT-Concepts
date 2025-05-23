import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from tradenotifier import send_email_notification
from typing import List, Dict, Optional, Tuple
import logging
import time
import uuid
import os

class ICTSilverBulletTrader:
    def __init__(self, symbols: List[str], timeframe: int, lot_size: float = 0.1, rr_ratio: float = 2.0,open_trades:List=[]):
        # Configure logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('ict_silver_bullet.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger('ICT Trader')
        
        self.symbols = symbols
        self.timeframe = timeframe
        self.lot_size = lot_size
        self.rr_ratio = rr_ratio
        self.max_slippage = 3  # points
        self.open_trades=open_trades
        
        self.logger.info(f"Initializing ICTSilverBulletTrader for symbols: {', '.join(symbols)}")
        self.logger.info(f"Configuration: Timeframe={timeframe}, Lot Size={lot_size}, RR Ratio={rr_ratio}")
        
        APP_PASSWORD = os.getenv("APP_PASSWORD")
        ACCOUNT_NUMBER = os.getenv("ACCOUNT_NUMBER")
        if not mt5.initialize():
            error = mt5.last_error()
            self.logger.error(f"MT5 Initialize() failed: {error}")
            exit(1)
        else:

            login = mt5.login(918791, APP_PASSWORD, "EGMSecurities-Demo")
            if not login:
                error = mt5.last_error()
                self.logger.error(f"MT5 login() failed: {error}")
                exit(1)
        
            
        self.logger.info("MT5 initialized successfully")
            
        # Verify all symbols are available
        valid_symbols = []
        for symbol in self.symbols:
            if not mt5.symbol_select(symbol, True):
                self.logger.info(f"Symbol {symbol} not found, skipping")
            else:
                valid_symbols.append(symbol)
                self.logger.info(f"Symbol {symbol} is available for trading")
        
        self.symbols = valid_symbols
        if not self.symbols:
            self.logger.error("No valid symbols available for trading")
            mt5.shutdown()
            exit(1)
    
    def get_recent_data(self, symbol: str, bars: int = 100) -> Optional[pd.DataFrame]:
        """Fetch recent price data for a symbol"""
        self.logger.info(f"Fetching {bars} bars of data for {symbol}")
        
        try:
            rates = mt5.copy_rates_from_pos(symbol, self.timeframe, 0, bars)
            if rates is None:
                self.logger.info(f"No data returned for {symbol}")
                return None
                
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s')
            df.set_index('time', inplace=True)
            
            self.logger.info(f"Retrieved {len(df)} bars for {symbol}, latest: {df.index[-1]}")
            return df
            
        except Exception as e:
            self.logger.error(f"Error fetching data for {symbol}: {str(e)}")
            return None
    
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
    def  in_london_newyork_window(ts: pd.Timestamp) -> bool:
        """Check if time is in London/NY overlap or NY session."""
        hour = ts.hour
        return (8 <= hour < 12) or (13 <= hour < 17)
    
    def execute_trade(self, symbol: str, signal_type: str, entry_price: float, sl_price: float, tp_price: float):
        """Execute a trade in MT5"""
        self.logger.info(f"Preparing {signal_type} trade for {symbol} at {entry_price}")
        
        try:
            point = mt5.symbol_info(symbol).point
            deviation = self.max_slippage
            
            
            if signal_type == "bullish":
                order_type = mt5.ORDER_TYPE_BUY
                price = mt5.symbol_info_tick(symbol).ask
                self.logger.info(f"Buy signal - Ask price: {price}")
            else:
                order_type = mt5.ORDER_TYPE_SELL
                price = mt5.symbol_info_tick(symbol).bid
                self.logger.info(f"Sell signal - Bid price: {price}")

            lot_size = self.lot_size
            if(symbol == "XAUUSD"):
                lot_size = 0.01
           
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume":lot_size,
                "type": order_type,
                "price": price,
                "tp": tp_price,
                "deviation": deviation,
                "magic": 202404,
                "comment": "ICT_SilverBullet",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            self.logger.info(f"Sending trade request: {request}")
            result = mt5.order_send(request)
            
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                self.logger.error(f"Order failed for {symbol}, retcode={result.retcode}, comment={result.comment}")
                return False
            else:
                signal_message=f" Trade executed successfully: {symbol} {signal_type} at {price} \n\n Trade details - SL: {sl_price}, TP: {tp_price}, Lot: {self.lot_size}"
                # append to the open trades list
                order_made = {
                    "symbol":symbol,
                    "price":price,
                    "position":result.order,
                    "time":datetime.now(),
                    "take_p":tp_price,
                    "order_type":order_type   
                }
                self.open_trades.append(order_made)
                # send the email with signal notice
                send_email_notification(f"{symbol} TRADE ACTION",signal_message)
            
            
            self.logger.info(f"Trade executed successfully: {symbol} {signal_type} at {price}")
            self.logger.info(f"Trade details - SL: {sl_price}, TP: {tp_price}, Lot: {self.lot_size}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error executing trade for {symbol}: {str(e)}")
            return False
    
    def process_sl_open_trades(self):
        """Check open trades and update SL every 5 minutes within a short second range."""
        self.logger.info("Checking open trades for SL updates.")
        self.logger.info(f"Currently tracking {len(self.open_trades)} open trades.")

        now = datetime.now()
        print(f"We are now at the {now.minute} minute")
        if now.minute % 5 != 0:
            self.logger.info("Outside of SL update window.")
            return

        for trade in self.open_trades:
            if trade.get("sl_updated") or trade.get("time").minute == datetime.now().minute:
                continue  

            try:
                self.update_sl(trade)
                trade["sl_updated"] = True
                self.logger.info(f"SL updated for trade: {trade['symbol']}")
            except Exception as e:
                self.logger.error(f"Failed to update SL for {trade['symbol']}: {e}")
 

    def update_sl(self,trades:Dict):
        tp = trades["take_p"]
        price = trades["price"]
        rr_ratio = self.rr_ratio

        stop_loss=0
        # calculate the stop loss
        if(trades["order_type"] == mt5.ORDER_TYPE_BUY):  
            amount_to_win = tp-price
            amount_to_lose = amount_to_win/rr_ratio
            stop_loss = price-amount_to_lose
        if(trades["order_type"] == mt5 .ORDER_TYPE_SELL):  
            amount_to_win = price-tp
            amount_to_lose = amount_to_win/rr_ratio
            stop_loss = price+amount_to_lose
        # Prepare the modification request
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": trades["position"],
            "sl": stop_loss,
            "tp":tp
        }

        # Send the request
        result = mt5.order_send(request)

        # Check the result
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            print("Stop Loss updated successfully!")
        else:
            print(f"Failed to update SL. Retcode={result.retcode}")

    
    def  process_signals(self):
        """Main trading logic that processes signals and executes trades"""
        self.logger.info("Starting signal processing cycle")
        orders=mt5.positions_get()

        
        for symbol in self.symbols:
            if(len(orders)>4):
                print(f"We have reached the open trades limit positions of {4} trades at a time")
                print(f"Kindly wait for the trades to close")
                continue
            self.logger.info(f"Processing symbol: {symbol}")
            
            df = self.get_recent_data(symbol)
            if df is None:
                continue
                
            current_time = df.index[-1]
            self.logger.info(f"Current time for {symbol}: {current_time}")
            
            if not self.in_london_newyork_window(current_time):
                self.logger.info(f"Outside trading window for {symbol}")
                continue
                
            self.logger.info(f"Inside trading window for {symbol}")
            
            # Get FVG signals
            fvg_signals = self.detect_fvg(df)
            self.logger.info(f"Found {len(fvg_signals)} FVG signals for {symbol}")
            
            for signal_time, signal_dir in fvg_signals:
                if signal_time == current_time:
                    self.logger.info(f"New FVG signal detected for {symbol}: {signal_dir} at {signal_time}")
                    
                    entry_price = df.iloc[-1]['close']
                    atr = self.calculate_atr(df, len(df)-1)
                    self.logger.info(f"ATR for {symbol}: {atr}")
                    
                    if signal_dir == "bullish":
                        sl_price = entry_price - 0.2 * atr
                        tp_price = entry_price + 0.4 * atr
                    else:
                        sl_price = entry_price + 0.2 * atr
                        tp_price = entry_price - 0.4 * atr
                    
                    self.logger.info(f"Preparing {signal_dir} trade for {symbol}")
                    self.logger.info(f"Entry: {entry_price}, SL: {sl_price}, TP: {tp_price}")
                    
                    self.execute_trade(symbol, signal_dir,   entry_price, sl_price, tp_price)
            
            # Get liquidity sweep signals
            sweep_signals = self.detect_liquidity_sweeps(df)
            self.logger.info(f"Found {len(sweep_signals)} sweep signals for {symbol}")
            
            for signal_time, signal_dir in sweep_signals:
                if signal_time == current_time:
                    self.logger.info(f"New liquidity sweep detected for {symbol}: {signal_dir} at {signal_time}")
                    
                    entry_price = df.iloc[-1]['close']
                    atr = self.calculate_atr(df, len(df)-1)
                    self.logger.info(f"ATR for {symbol}: {atr}")
                    
                    if signal_dir == "bullish":
                        sl_price = entry_price - 0.2 * atr
                        tp_price = entry_price + 0.4 * atr
                    else:
                        sl_price = entry_price + 0.2 * atr
                        tp_price = entry_price - 0.4 * atr
                    
                    self.logger.info(f"Preparing {signal_dir} trade for {symbol}")
                    self.logger.info(f"Entry: {entry_price}, SL: {sl_price}, TP: {tp_price}")
                    
                    self.execute_trade(symbol, signal_dir, entry_price, sl_price, tp_price)
    
    @staticmethod
    def calculate_atr(df: pd.DataFrame, idx: int, period: int = 14) -> float:
        """Calculate Average True Range at given index."""
        try:
            start_idx = max(0, idx - period)
            df_slice = df.iloc[start_idx:idx+1].copy()
            
            df_slice['prev_close'] = df_slice['close'].shift(1)
            df_slice['tr1'] = df_slice['high'] - df_slice['low']
            df_slice['tr2'] = abs(df_slice['high'] - df_slice['prev_close'])
            df_slice['tr3'] = abs(df_slice['low'] - df_slice['prev_close'])
            df_slice['tr'] = df_slice[['tr1', 'tr2', 'tr3']].max(axis=1)
            
            return df_slice['tr'].mean()
        except Exception as e:
            logging.error(f"Error calculating ATR: {str(e)}")
            return 0.0
    
    def run(self):
        """Main trading loop"""
        self.logger.info(f"Starting ICT Silver Bullet trader for symbols: {', '.join(self.symbols)}")
        self.logger.info("Press Ctrl+C to stop the trader")
        
        try:
            while True:
                self.logger.info("Starting new iteration")
                self.process_signals()                
                self.process_sl_open_trades()
                sleep_time = 3000  # seconds
                self.logger.info(f"Sleeping for {sleep_time} seconds")
                #time.sleep(sleep_time)
                
        except KeyboardInterrupt:
            self.logger.info("\nReceived shutdown signal, stopping trader...")
        except Exception as e:
            self.logger.error(f"Unexpected error: {str(e)}")
        finally:
            mt5.shutdown()
            self.logger.info("MT5 connection closed")
            self.logger.info("Trader shutdown complete")


if __name__ == "__main__":
    # Configure your symbols here
    symbols = ["UT100Roll", "US500Roll", "XAUUSD"] 
    
    # Create and run the trader
    trader = ICTSilverBulletTrader(
        symbols=symbols,
        timeframe=mt5.TIMEFRAME_M5,
        lot_size=0.5,
        rr_ratio=2.0
    )
    trader.run()