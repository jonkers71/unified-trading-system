import MetaTrader5 as mt5
import asyncio
import logging
import time
from pybit.unified_trading import HTTP
from telethon import TelegramClient, events
from .signal_parser import SignalParser
from .risk_manager import RiskManager

class TradingEngine:
    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger("TradingEngine")
        self.parser = SignalParser()
        self.risk_manager = RiskManager(config)
        self.client = None
        self.bybit_session = None
        
        # Latency tracking
        self.mt5_latency = 0
        self.bybit_latency = 0
        
        # Trade Monitoring
        self.trade_history = []
        self.daily_profit = 0.0
        
        # Advanced State Tracking
        self.active_signals = {} # map signal_id -> status
        self.monitored_channels = config.get('channels', [])

    async def start(self):
        """Initialize all connections"""
        self.logger.info("🚀 Starting Trading Engine...")
        
        # 1. MT5 Initialize
        if self.config['mt5']['enabled']:
            if not mt5.initialize():
                self.logger.error(f"Failed to initialize MT5: {mt5.last_error()}")
            else:
                self.logger.info("✅ MT5 Connected")
        
        # 2. Bybit Initialize
        if self.config['bybit']['enabled']:
            self.bybit_session = HTTP(
                testnet=self.config['bybit']['testnet'],
                api_key=self.config['bybit']['api_key'],
                api_secret=self.config['bybit']['api_secret']
            )
            self.logger.info("✅ Bybit API Connected")
            
        # 3. Telegram Initialize
        self.client = TelegramClient(
            self.config['telegram']['session_name'],
            self.config['telegram']['api_id'],
            self.config['telegram']['api_hash']
        )
        
        @self.client.on(events.NewMessage)
        async def handle_new_message(event):
            await self.on_message_received(event)
            
        await self.client.start(phone=self.config['telegram']['phone_number'])
        self.logger.info("✅ Telegram Client Started")
        
        # Start Background Monitors
        asyncio.create_task(self._latency_monitor_loop())
        asyncio.create_task(self._protection_monitor_loop())
        
        # Keep engine running
        await self.client.run_until_disconnected()

    async def _latency_monitor_loop(self):
        """Background task to update connection latency metrics"""
        while True:
            try:
                if self.config['mt5']['enabled']:
                    start = time.perf_counter()
                    mt5.terminal_info()
                    self.mt5_latency = int((time.perf_counter() - start) * 1000)
                
                if self.config['bybit']['enabled']:
                    start = time.perf_counter()
                    try:
                        # Try multiple common method names for Bybit server time
                        if hasattr(self.bybit_session, 'get_server_time'):
                            self.bybit_session.get_server_time()
                        elif hasattr(self.bybit_session, 'get_time'):
                            self.bybit_session.get_time()
                        else:
                            # Fallback: simple public request to check connectivity/latency
                            self.bybit_session.get_instruments_info(category="linear", limit=1)
                    except Exception:
                        # If a specific call fails, we still want to record the attempt for latency if possible, 
                        # but we catch it here to avoid the generic loop error spamming.
                        pass
                    self.bybit_latency = int((time.perf_counter() - start) * 1000)
            except Exception as e:
                self.logger.warning(f"Latency check error: {e}")
            await asyncio.sleep(10)

    async def _protection_monitor_loop(self):
        """Background task for Breakeven and Trailing Stop management"""
        while True:
            try:
                if self.config['mt5']['enabled']:
                    await self._manage_mt5_protection()
                # Bybit protection logic would go here
            except Exception as e:
                self.logger.error(f"Protection monitor error: {e}")
            await asyncio.sleep(1) # Check every second for low latency execution

    async def _manage_mt5_protection(self):
        """Check all open MT5 positions for BE and trailing stop trigger"""
        positions = mt5.positions_get()
        if not positions: return

        for pos in positions:
            if pos.magic != self.config['mt5']['magic_number']: continue
            
            symbol = pos.symbol
            # Get current settings
            be_enabled = self.config['trading'].get('be_enabled', True)
            be_buffer = self.config['trading'].get('be_buffer', 5.0)
            trailing_enabled = self.config['trading'].get('trailing_enabled', True)
            trailing_dist = self.config['trading'].get('trailing_distance', 15.0)
            
            tick = mt5.symbol_info_tick(symbol)
            if not tick: continue
            
            current_price = tick.bid if pos.type == mt5.POSITION_TYPE_BUY else tick.ask
            profit_points = (current_price - pos.price_open) if pos.type == mt5.POSITION_TYPE_BUY else (pos.price_open - current_price)
            
            # 1. Breakeven Logic (Move SL to Entry + Buffer after TP1)
            # We assume TP1 is reached if profit is > distance to TP1
            # For simplicity, we can also check if the position is "Hybrid" and profit is significant
            if be_enabled and pos.sl < pos.price_open and "Hybrid" in pos.comment:
                # If we are in profit by at least 20 pips (example threshold for TP1)
                # Ideally, we should fetch the original signal's TP1 value
                if profit_points > 0.0020: # Example logic for major pairs
                    new_sl = pos.price_open + (be_buffer * 0.0001) if pos.type == mt5.POSITION_TYPE_BUY else pos.price_open - (be_buffer * 0.0001)
                    self._modify_sl(pos.ticket, new_sl)
            
            # 2. Trailing Stop Logic
            if trailing_enabled:
                threshold = trailing_dist * 0.0001
                if pos.type == mt5.POSITION_TYPE_BUY:
                    if current_price - pos.sl > (threshold * 2): # If price moved significantly away from SL
                        new_sl = current_price - threshold
                        if new_sl > pos.sl:
                            self._modify_sl(pos.ticket, new_sl)
                else: # SELL
                    if pos.sl - current_price > (threshold * 2):
                        new_sl = current_price + threshold
                        if pos.sl == 0 or new_sl < pos.sl:
                           self._modify_sl(pos.ticket, new_sl)

    def _modify_sl(self, ticket, new_sl):
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl": new_sl
        }
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            self.logger.error(f"Failed to modify SL for {ticket}: {result.comment}")
        else:
            self.logger.info(f"✅ SL Modified for {ticket} to {new_sl}")

    async def on_message_received(self, event):
        """Handle incoming signals from Telegram"""
        sender_id = event.chat_id
        channel_info = next((c for c in self.config['channels'] if c['id'] == sender_id), None)
        if not channel_info: return

        text = event.message.message
        signal = self.parser.parse_message(text, channel_info)
        
        if signal:
            if not self._check_spread(signal):
                self.logger.warning(f"Trade Aborted: Spread exceeds limit for {signal['symbol']}")
                return
            await self.execute_trade(signal)

    def _check_spread(self, signal):
        """Verify if current spread is within allowed limits"""
        symbol = signal['symbol']
        info = mt5.symbol_info(symbol)
        if not info: return False
        
        current_spread = info.spread # in points
        
        # Determine limit based on asset type
        if "XAU" in symbol or "GOLD" in symbol.upper():
            limit = self.config['trading'].get('max_spread_gold', 800)
        else:
            limit = self.config['trading'].get('max_spread_forex', 5)
            
        return current_spread <= limit

    async def execute_trade(self, signal):
        """Direct execution logic based on UI settings"""
        tp_mode = self.config['trading'].get('tp_mode', 'hybrid')
        
        if signal['type'] == 'forex':
            if tp_mode == 'split':
                await self._execute_mt5_split(signal)
            elif tp_mode == 'hybrid':
                await self._execute_mt5_hybrid(signal)
            elif tp_mode == 'scalper':
                await self._execute_mt5_scalper(signal)
            else: # Sniper
                await self._execute_mt5_sniper(signal)
        elif signal['type'] == 'crypto':
            await self._execute_bybit(signal)

    async def _execute_mt5_hybrid(self, signal):
        """Execute 1 large position with partial close monitoring"""
        symbol = signal['symbol']
        side = mt5.ORDER_TYPE_BUY if signal['side'] == 'BUY' else mt5.ORDER_TYPE_SELL
        
        info = mt5.symbol_info(symbol)
        balance = mt5.account_info().balance
        lot = self.risk_manager.calculate_mt5_lot(info, signal['entry'], signal['sl'], balance)
        
        # Use simple target for initial order
        final_tp_idx = 1 if self.config['trading'].get('final_target') == 'tp2' else 2
        if len(signal['tps']) <= final_tp_idx: final_tp_idx = len(signal['tps']) - 1
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": side,
            "price": mt5.symbol_info_tick(symbol).ask if side == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick(symbol).bid,
            "sl": signal['sl'],
            "tp": signal['tps'][final_tp_idx],
            "magic": self.config['mt5']['magic_number'],
            "comment": f"Hybrid: {signal['channel_name']}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(request)
        self._log_trade(result, symbol, lot, signal)

    async def _execute_mt5_split(self, signal):
        """Execute 3 separate positions for each TP"""
        symbol = signal['symbol']
        side = mt5.ORDER_TYPE_BUY if signal['side'] == 'BUY' else mt5.ORDER_TYPE_SELL
        
        info = mt5.symbol_info(symbol)
        balance = mt5.account_info().balance
        total_lot = self.risk_manager.calculate_mt5_lot(info, signal['entry'], signal['sl'], balance)
        
        # Split lot according to config
        splits = self.config['trading'].get('tp_split', [33, 33, 34])
        min_v = info.volume_min
        
        lot1 = max(min_v, round(total_lot * (splits[0]/100), 2))
        lot2 = max(min_v, round(total_lot * (splits[1]/100), 2))
        lot3 = round(total_lot - (lot1 + lot2), 2)
        
        # If total is too small for 3 positions, just use 1
        if lot3 <= 0 or (lot1 + lot2 + lot3) > (total_lot * 1.1): # Over-risking guard
             lots = [total_lot, 0, 0]
        else:
             lots = [lot1, lot2, lot3]
        for i, tp_price in enumerate(signal['tps']):
            if i >= 3: break
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": lots[i],
                "type": side,
                "price": mt5.symbol_info_tick(symbol).ask if side == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick(symbol).bid,
                "sl": signal['sl'],
                "tp": tp_price,
                "magic": self.config['mt5']['magic_number'],
                "comment": f"Split TP{i+1}: {signal['channel_name']}",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            self._log_trade(result, symbol, lots[i], signal)

    async def _execute_mt5_sniper(self, signal):
        """Execute 1 position targeting a specific TP level"""
        # Logic same as hybrid but no partial close monitoring
        await self._execute_mt5_hybrid(signal)

    async def _execute_mt5_scalper(self, signal):
        """Execute 1 position targeting ONLY TP1"""
        symbol = signal['symbol']
        side = mt5.ORDER_TYPE_BUY if signal['side'] == 'BUY' else mt5.ORDER_TYPE_SELL
        
        info = mt5.symbol_info(symbol)
        balance = mt5.account_info().balance
        lot = self.risk_manager.calculate_mt5_lot(info, signal['entry'], signal['sl'], balance)
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": side,
            "price": mt5.symbol_info_tick(symbol).ask if side == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick(symbol).bid,
            "sl": signal['sl'],
            "tp": signal['tps'][0], # Only TP1
            "magic": self.config['mt5']['magic_number'],
            "comment": f"Scalper: {signal['channel_name']}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(request)
        self._log_trade(result, symbol, lot, signal)

    def _log_trade(self, result, symbol, lot, signal):
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            self.logger.error(f"Order Failed: {result.comment}")
        else:
            self.logger.info(f"✅ Success: {symbol} {lot} lots")
            self.trade_history.append({
                "time": time.strftime("%H:%M:%S"),
                "symbol": symbol,
                "type": signal['side'],
                "target": "Active",
                "status": f"Executed | {lot} lots",
                "success": True
            })

    async def _execute_bybit(self, signal):
        """Bybit Logic (to be refined for Multi-TP/Partial)"""
        # For now keeping basic, will expand if user specifically asks for Bybit TP split
        symbol = signal['symbol']
        side = "Buy" if signal['side'] == "BUY" else "Sell"
        try:
            balance_resp = self.bybit_session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
            balance = float(balance_resp['result']['list'][0]['totalEquity'])
            instrument_resp = self.bybit_session.get_instruments_info(category="linear", symbol=symbol)
            rules = instrument_resp['result']['list'][0]
            qty = self.risk_manager.calculate_bybit_qty(rules, signal['entry'], signal['sl'], balance)
            
            order_resp = self.bybit_session.place_order(
                category="linear", symbol=symbol, side=side, orderType="Market",
                qty=str(qty), takeProfit=str(signal['tps'][0]), stopLoss=str(signal['sl']),
                tpOrderType="Market", slOrderType="Market", positionIdx=0
            )
            
            if order_resp['retCode'] == 0:
                self.logger.info(f"✅ Bybit Success: {symbol} {qty}")
                self.trade_history.append({
                    "time": time.strftime("%H:%M:%S"), "symbol": symbol, "type": signal['side'],
                    "target": "TP1", "status": f"Bybit: {qty}", "success": True
                })
        except Exception as e:
            self.logger.error(f"Bybit Error: {e}")
