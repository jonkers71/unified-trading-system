import MetaTrader5 as mt5
import asyncio
import logging
import time
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pybit.unified_trading import HTTP
from pybit.exceptions import InvalidRequestError, FailedRequestError
from telethon import TelegramClient, events
from .signal_parser import SignalParser
from .risk_manager import RiskManager

class TradingEngine:
    def __init__(self, config, on_state_change=None):
        self.config = config
        self.on_state_change = on_state_change
        self.logger = logging.getLogger("TradingEngine")
        self.parser = SignalParser()
        self.risk_manager = RiskManager(config)
        self.client = None
        self.bybit_session = None
        
        # Latency tracking
        self.mt5_latency = 0
        self.bybit_latency = 0
        self.bybit_status = "INITIALIZING"
        
        # Trade Monitoring
        self.trade_history = []
        self.daily_profit = 0.0
        
        # Advanced State Tracking
        self.active_signals = {} # map signal_id -> status
        self._recent_signals = {} # map: "SYMBOL_SIDE" -> timestamp
        self.monitored_channels = config.get('channels', [])
        
        # Performance Stats
        self.performance_stats = {
            "rolling_7d": {"labels": [], "data": []},
            "historical": {"labels": [], "data": []}
        }
        
        # Operational Control
        self.new_trades_enabled = True
        
        # Persistence
        os.makedirs("config", exist_ok=True)
        self.db_path = "config/trading_data.db"

    def _init_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS active_signals (
                        signal_id TEXT PRIMARY KEY,
                        data TEXT
                    )
                ''')
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS app_state (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                ''')
                conn.commit()
        except Exception as e:
            self.logger.error(f"Failed to init DB: {e}")

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
                api_secret=self.config['bybit']['api_secret'],
                recv_window=10000 # Increased for Proxmox drift protection
            )
            # Perform hard validation at startup
            await self._validate_bybit_auth()
            
        # 3. Load Persistent State & Reconcile
        self._load_state()
        await self._reconcile_positions()
        
        # 4. Telegram Initialize
        os.makedirs("config", exist_ok=True)
        self.session_path = os.path.join("config", self.config['telegram']['session_name'])
        self.client = TelegramClient(
            self.session_path,
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
        asyncio.create_task(self._performance_update_loop())
        
        # Keep engine running
        await self.client.run_until_disconnected()

    def _save_state(self):
        """Save active signals and state to SQLite"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Sync active signals safely via DELETE/INSERT
                cursor.execute("DELETE FROM active_signals")
                for sig_id, sig_data in self.active_signals.items():
                    cursor.execute(
                        "INSERT INTO active_signals (signal_id, data) VALUES (?, ?)",
                        (sig_id, json.dumps(sig_data))
                    )
                
                # Sync app state
                history_json = json.dumps(self.trade_history[-50:])
                cursor.execute(
                    "INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)",
                    ("trade_history", history_json)
                )
                cursor.execute(
                    "INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)",
                    ("daily_profit", json.dumps(self.daily_profit))
                )
                conn.commit()
            
            self._notify_state_change()
        except Exception as e:
            self.logger.error(f"Failed to save state to DB: {e}")

    def _notify_state_change(self):
        if self.on_state_change:
            try:
                self.on_state_change()
            except Exception as e:
                self.logger.error(f"State broadcast failed: {e}")

    def _load_state(self):
        """Load state from SQLite, migrating from JSON if necessary."""
        
        # 1. Check if the new database already exists.
        db_exists = os.path.exists(self.db_path)
        
        # 2. If DB does NOT exist, check for the old state.json to migrate.
        if not db_exists:
            old_state_file = "logs/state.json"
            if os.path.exists(old_state_file):
                try:
                    self.logger.info("Migrating old state.json to SQLite...")
                    with open(old_state_file, "r") as f:
                        state = json.load(f)
                        self.active_signals = state.get("active_signals", {})
                        self.daily_profit = state.get("daily_profit", 0.0)
                        self.trade_history = state.get("trade_history", [])
                    
                    # Now that data is in memory, create the DB and save it.
                    self._init_db() # Creates the empty DB file and tables.
                    self._save_state() # Saves the migrated data into the new DB.
                    self.logger.info("✅ Migration successful.")
                    return # End the function here, as state is now loaded.
                except Exception as e:
                    self.logger.error(f"Failed to migrate old state: {e}")

        # 3. If we reach this point, either the DB existed or migration was not needed.
        # We must ensure the DB and tables are created before trying to read.
        self._init_db()
        
        # 4. Now, proceed to load from SQLite as normal.
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Load active signals
                cursor.execute("SELECT signal_id, data FROM active_signals")
                for row in cursor.fetchall():
                    self.active_signals[row[0]] = json.loads(row[1])
                    
                # Load app state
                cursor.execute("SELECT key, value FROM app_state")
                state_rows = cursor.fetchall()
                state_dict = {row[0]: json.loads(row[1]) for row in state_rows}
                
                self.daily_profit = state_dict.get("daily_profit", 0.0)
                self.trade_history = state_dict.get("trade_history", [])
                
                self.logger.info(f"📂 State Loaded: {len(self.active_signals)} active signals restored from SQLite.")
        except Exception as e:
            self.logger.error(f"Failed to load state from DB: {e}")

    async def _reconcile_positions(self):
        """Verify internal state against actual broker positions"""
        self.logger.info("🔍 Reconciling positions with brokers...")
        
        # 1. MT5 Reconciliation
        if self.config['mt5']['enabled']:
            positions = mt5.positions_get(magic=self.config['mt5']['magic_number'])
            if positions is None:
                self.logger.error(f"Failed to get MT5 positions: {mt5.last_error()}")
            else:
                active_tickets = {str(p.ticket) for p in positions}
                
                # Check for orphans (Positions in MT5 but not in our state)
                for p in positions:
                    found = False
                    for sig_id, sig in self.active_signals.items():
                        if str(sig.get('ticket')) == str(p.ticket) or str(sig.get('id')) == str(p.ticket):
                            found = True
                            break
                    
                    if not found:
                        self.logger.info(f"🔗 Linking orphan MT5 position: {p.symbol} (Ticket: {p.ticket})")
                        # Create a basic signal object to track it
                        new_sig_id = f"RETORED_{p.ticket}"
                        self.active_signals[new_sig_id] = {
                            "symbol": p.symbol,
                            "side": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
                            "entry": p.price_open,
                            "sl": p.sl,
                            "tps": [p.tp] if p.tp > 0 else [],
                            "ticket": p.ticket,
                            "restored": True,
                            "channel_name": "Restored"
                        }
                        
                        # Add to history for dashboard visibility
                        self.trade_history.append({
                            "time": time.strftime("%H:%M:%S"),
                            "symbol": p.symbol,
                            "type": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
                            "target": "RESTORED",
                            "status": f"Ticket #{p.ticket}",
                            "success": True
                        })
                
                # Cleanup (Signals in our state but no longer in MT5)
                # We skip signals that don't have a ticket yet (just opened)
                to_remove = []
                for sig_id, sig in self.active_signals.items():
                    ticket = sig.get('ticket')
                    if ticket and str(ticket) not in active_tickets:
                        self.logger.info(f"🧹 Removing stale signal from state: {sig['symbol']} (Ticket: {ticket})")
                        to_remove.append(sig_id)
                
                for rid in to_remove:
                    del self.active_signals[rid]
        
        # 2. Bybit Reconciliation (Advanced)
        if self.config['bybit']['enabled'] and self.bybit_session:
            try:
                pos_resp = self.bybit_session.get_positions(category="linear", settleCoin="USDT")
                bybit_positions = [p for p in pos_resp.get('result', {}).get('list', []) if float(p.get('size', 0)) > 0]
                self.logger.info(f"📡 Bybit: Found {len(bybit_positions)} active positions.")
                
                active_bybit_symbols = {p['symbol'] for p in bybit_positions}
                
                # Check for orphans
                for p in bybit_positions:
                    sym = p['symbol']
                    found = False
                    for sig_id, sig in self.active_signals.items():
                        if sig.get('symbol') == sym and sig.get('ticket') != 'closed': 
                            found = True
                            break
                    
                    if not found:
                        self.logger.info(f"🔗 Linking orphan Bybit position: {sym}")
                        new_sig_id = f"RESTORED_BYBIT_{sym}_{int(time.time())}"
                        sl_val = float(p.get('stopLoss', 0))
                        tp_val = float(p.get('takeProfit', 0))
                        
                        self.active_signals[new_sig_id] = {
                            "symbol": sym,
                            "side": str(p.get('side', 'BUY')).upper(),
                            "entry": float(p.get('avgPrice', 0)),
                            "sl": sl_val if sl_val > 0 else 0.0,
                            "tps": [tp_val] if tp_val > 0 else [],
                            "ticket": f"bybit_{sym}",
                            "restored": True,
                            "channel_name": "Restored (Bybit)",
                            "type": "crypto"
                        }
                        
                        self.trade_history.append({
                            "time": time.strftime("%H:%M:%S"),
                            "symbol": sym,
                            "type": str(p.get('side', 'BUY')).upper(),
                            "target": "RESTORED",
                            "status": f"Bybit: {p.get('size')}",
                            "success": True
                        })
                        
                # Cleanup (Signals in our state for Bybit but no longer active)
                to_remove_bybit = []
                for sig_id, sig in self.active_signals.items():
                    # Check if crypto or implicitly Bybit via ticket
                    if sig.get('type') == 'crypto' or str(sig.get('ticket', '')).startswith('bybit_') or str(sig.get('ticket', '')).startswith('crypto_'):
                        if sig.get('symbol') not in active_bybit_symbols:
                            self.logger.info(f"🧹 Removing stale Bybit signal from state: {sig['symbol']}")
                            to_remove_bybit.append(sig_id)
                            
                for rid in to_remove_bybit:
                    del self.active_signals[rid]
                    
            except Exception as e:
                self.logger.warning(f"Bybit reconciliation failed: {e}")

        self._save_state()
        
        # Initial stats update
        await self._update_performance_stats()

    async def _performance_update_loop(self):
        """Periodically refresh performance statistics"""
        while True:
            await asyncio.sleep(300) # Every 5 minutes
            try:
                await self._update_performance_stats()
            except Exception as e:
                self.logger.error(f"Performance loop error: {e}")

    async def _update_performance_stats(self):
        """Aggregate history from brokers for dashboard charts"""
        self.logger.info("📊 Refreshing performance statistics...")
        
        all_trades = []
        
        # 1. Fetch MT5 History
        if self.config['mt5']['enabled']:
            # Fetch last 30 days
            from_date = datetime.now() - timedelta(days=30)
            deals = mt5.history_deals_get(from_date, datetime.now())
            if deals is not None:
                magic = self.config['mt5']['magic_number']
                for d in deals:
                    # Filter by magic number and outgoing deals (closed positions)
                    if d.magic == magic and d.entry == mt5.DEAL_ENTRY_OUT:
                        all_trades.append({
                            "time": datetime.fromtimestamp(d.time),
                            "profit": d.profit + d.commission + d.swap
                        })

        # 2. Fetch Bybit History (if possible)
        if self.config['bybit']['enabled'] and self.bybit_session:
            try:
                # Bybit v5 get_closed_pnl
                resp = self.bybit_session.get_closed_pnl(category="linear", limit=50)
                for p in resp.get('result', {}).get('list', []):
                    all_trades.append({
                        "time": datetime.fromtimestamp(int(p['updatedTime'])/1000),
                        "profit": float(p['closedPnl'])
                    })
            except Exception as e:
                self.logger.debug(f"Bybit history fetch failed: {e}")

        if not all_trades:
            today = datetime.now().date()
            self.performance_stats['rolling_7d'] = {
                "labels": [(today - timedelta(days=i)).strftime("%m/%d") for i in range(6, -1, -1)],
                "data": [0.0] * 7
            }
            self.performance_stats['historical'] = {"labels": [], "data": []}
            return

        # Sort by time
        all_trades.sort(key=lambda x: x['time'])

        # 3. Calculate Rolling 7D (Daily Buckets)
        today = datetime.now().date()
        rolling_data = {}
        for i in range(7):
            d = today - timedelta(days=i)
            rolling_data[d] = 0.0
            
        for t in all_trades:
            t_date = t['time'].date()
            if t_date in rolling_data:
                rolling_data[t_date] += t['profit']
        
        sorted_rolling = sorted(rolling_data.items())
        self.performance_stats['rolling_7d'] = {
            "labels": [d.strftime("%m/%d") for d, _ in sorted_rolling],
            "data": [round(v, 2) for _, v in sorted_rolling]
        }

        # 4. Calculate Historical Equity Curve (Cumulative)
        cumulative = 0
        hist_labels = []
        hist_data = []
        
        daily_hist = {}
        for t in all_trades:
            t_date = t['time'].date()
            daily_hist[t_date] = daily_hist.get(t_date, 0.0) + t['profit']
            
        sorted_hist = sorted(daily_hist.items())
        for d, p in sorted_hist:
            cumulative += p
            hist_labels.append(d.strftime("%m/%d"))
            hist_data.append(round(cumulative, 2))
            
        self.performance_stats['historical'] = {
            "labels": hist_labels,
            "data": hist_data
        }
        self.logger.info(f"📈 Analytics Updated: {len(all_trades)} trades compiled.")

    async def _validate_bybit_auth(self):
        """Hard validation of Bybit credentials and permissions at startup"""
        try:
            # 1. Key & Permission Check
            key_info = self.bybit_session.get_api_key_information()
            permissions = key_info.get('result', {}).get('permissions', {})
            
            # Check for 'SpotTrade' or 'ContractTrade' depending on category
            # For Unified account, we usually check 'SpotTrade' and 'ContractTrade'
            has_trade = any('Trade' in p for p in permissions.get('Spot', []) + permissions.get('Contract', []))
            
            if not has_trade:
                self.logger.error("❌ Bybit Auth Error: API Key lacks 'Trade' permissions.")
                self.bybit_status = "LACKS TRADE PERM"
            else:
                self.logger.info("✅ Bybit API Connected & Authenticated")
                self.bybit_status = "AUTHENTICATED"
                
        except InvalidRequestError as e:
            mode = "Testnet" if self.config['bybit']['testnet'] else "Mainnet"
            if e.ret_code == 10003:
                self.logger.error(f"❌ Bybit Auth Error: Invalid API Key for {mode}. Check config (testnet={self.config['bybit']['testnet']}).")
                self.bybit_status = "INVALID KEYS"
            elif e.ret_code == 10004:
                 self.logger.error("❌ Bybit Auth Error: Invalid Signature. Check API Secret.")
                 self.bybit_status = "SIGNATURE ERROR"
            elif e.ret_code == 10002:
                self.logger.error("❌ Bybit Auth Error: Clock sync issue (10002). Increase recv_window or sync time.")
                self.bybit_status = "CLOCK ERROR"
            else:
                self.logger.error(f"❌ Bybit API Error [{e.ret_code}]: {e.message}")
                self.bybit_status = f"API ERR {e.ret_code}"
        except Exception as e:
            self.logger.error(f"❌ Bybit Connection Failed: {e}")
            self.bybit_status = "CONN FAILED"

    async def _latency_monitor_loop(self):
        """Background task to update connection latency metrics"""
        while True:
            try:
                if self.config['mt5']['enabled']:
                    start = time.perf_counter()
                    mt5.terminal_info()
                    self.mt5_latency = int((time.perf_counter() - start) * 1000)
                
                if self.config['bybit']['enabled'] and self.bybit_session:
                    start = time.perf_counter()
                    try:
                        # Use a lightweight authenticated call to verify session health
                        self.bybit_session.get_api_key_information()
                        self.bybit_latency = int((time.perf_counter() - start) * 1000)
                    except Exception as e:
                        # LOG the error so it's not silent
                        self.logger.debug(f"Bybit background health check failed: {e}")
                        self.bybit_latency = -1 # Indicate error
                        
                        # Only update status if it was previously authenticated (don't overwrite deep startup errors)
                        if self.bybit_status == "AUTHENTICATED":
                            self.bybit_status = "CONN LOST"
                        elif "API key is invalid" in str(e):
                             self.bybit_status = "INVALID KEYS"
            except Exception as e:
                self.logger.warning(f"Latency check error: {e}")
            await asyncio.sleep(300)

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
            info = mt5.symbol_info(symbol)
            if not info: continue

            # Get current settings
            be_enabled = self.config['trading'].get('be_enabled', True)
            be_buffer_pips = self.config['trading'].get('be_buffer', 5.0)
            trailing_enabled = self.config['trading'].get('trailing_enabled', True)
            trailing_dist_pips = self.config['trading'].get('trailing_distance', 15.0)
            
            tick = mt5.symbol_info_tick(symbol)
            if not tick: continue
            
            point = info.point
            # In MT5, 1 pip = 10 points for most pairs, but for Gold it can vary.
            # We'll treat the user's "pips" as 10 * point for consistency with common usage.
            pip_unit = point * 10 
            
            current_price = tick.bid if pos.type == mt5.POSITION_TYPE_BUY else tick.ask
            profit_points = (current_price - pos.price_open) if pos.type == mt5.POSITION_TYPE_BUY else (pos.price_open - current_price)
            
            # CRITICAL: Find the original signal to check TP1
            # We use the ticket or symbol to find associated signal data
            signal_data = next((s for s in self.active_signals.values() if s['symbol'] == symbol or s.get('ticket') == pos.ticket), None)
            
            # If no signal data (e.g. engine restarted), we can't safely verify TP1, so we skip movement 
            # to avoid moving SL too early.
            if not signal_data: continue

            tp1 = signal_data['tps'][0]
            
            # Check if TP1 has been reached (or current price is beyond it)
            tp1_reached = False
            if pos.type == mt5.POSITION_TYPE_BUY:
                if tick.bid >= tp1: tp1_reached = True
            else: # SELL
                if tick.ask <= tp1: tp1_reached = True

            if not tp1_reached:
                continue # Do not move Stop Loss until TP1 is hit

            # === PROGRESSIVE MODE: Partial Close Logic ===
            if signal_data.get('progressive'):
                splits = self.config['trading'].get('tp_split', [33, 33, 34])
                original_vol = signal_data.get('original_volume', pos.volume)
                min_vol = info.volume_min
                
                # TP1 reached: Close first partial if not already done
                if not signal_data.get('tp1_closed', False):
                    close_vol = max(min_vol, round(original_vol * (splits[0] / 100), 2))
                    close_vol = min(close_vol, pos.volume)  # Don't close more than remaining
                    
                    close_side = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
                    close_price = tick.bid if pos.type == mt5.POSITION_TYPE_BUY else tick.ask
                    
                    close_req = {
                        "action": mt5.TRADE_ACTION_DEAL,
                        "symbol": symbol,
                        "volume": close_vol,
                        "type": close_side,
                        "price": close_price,
                        "position": pos.ticket,
                        "magic": self.config['mt5']['magic_number'],
                        "comment": "Progressive: TP1 partial",
                        "type_time": mt5.ORDER_TIME_GTC,
                        "type_filling": mt5.ORDER_FILLING_IOC,
                    }
                    result = mt5.order_send(close_req)
                    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                        self.logger.info(f"✅ Progressive TP1: Closed {close_vol} of {symbol} ({splits[0]}%)")
                        signal_data['tp1_closed'] = True
                        self._save_state()
                    else:
                        self.logger.error(f"❌ Progressive TP1 partial close failed for {symbol}: {result.comment if result else 'No result'}")
                
                # TP2 reached: Close second partial if not already done
                if len(signal_data.get('tps', [])) >= 2 and not signal_data.get('tp2_closed', False):
                    tp2 = signal_data['tps'][1]
                    tp2_reached = False
                    if pos.type == mt5.POSITION_TYPE_BUY:
                        if tick.bid >= tp2: tp2_reached = True
                    else:
                        if tick.ask <= tp2: tp2_reached = True
                    
                    if tp2_reached:
                        # Refresh position to get updated volume after TP1 close
                        refreshed = mt5.positions_get(ticket=pos.ticket)
                        if refreshed:
                            current_vol = refreshed[0].volume
                            close_vol = max(min_vol, round(original_vol * (splits[1] / 100), 2))
                            close_vol = min(close_vol, current_vol)
                            
                            close_side = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
                            close_price = tick.bid if pos.type == mt5.POSITION_TYPE_BUY else tick.ask
                            
                            close_req = {
                                "action": mt5.TRADE_ACTION_DEAL,
                                "symbol": symbol,
                                "volume": close_vol,
                                "type": close_side,
                                "price": close_price,
                                "position": pos.ticket,
                                "magic": self.config['mt5']['magic_number'],
                                "comment": "Progressive: TP2 partial",
                                "type_time": mt5.ORDER_TIME_GTC,
                                "type_filling": mt5.ORDER_FILLING_IOC,
                            }
                            result = mt5.order_send(close_req)
                            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                                self.logger.info(f"✅ Progressive TP2: Closed {close_vol} of {symbol} ({splits[1]}%)")
                                signal_data['tp2_closed'] = True
                                self._save_state()
                            else:
                                self.logger.error(f"❌ Progressive TP2 partial close failed for {symbol}: {result.comment if result else 'No result'}")

            # 1. Breakeven Logic (Move SL to Entry + Buffer)
            is_buy = pos.type == mt5.POSITION_TYPE_BUY
            sl_needs_move = (is_buy and pos.sl < pos.price_open) or (not is_buy and pos.sl > pos.price_open)
            if be_enabled and sl_needs_move:
                new_sl = pos.price_open + (be_buffer_pips * point) if pos.type == mt5.POSITION_TYPE_BUY else pos.price_open - (be_buffer_pips * point)
                self._modify_sl(pos, new_sl, info)
            
            # 2. Trailing Stop Logic
            if trailing_enabled:
                threshold = trailing_dist_pips * point
                if pos.type == mt5.POSITION_TYPE_BUY:
                    # Move UP if price is far enough from current SL
                    if current_price - pos.sl > (threshold * 1.5):
                        new_sl = current_price - threshold
                        if new_sl > pos.sl:
                            self._modify_sl(pos, new_sl, info)
                else: # SELL
                    # Move DOWN
                    if (pos.sl == 0 or pos.sl - current_price > (threshold * 1.5)):
                        new_sl = current_price + threshold
                        if pos.sl == 0 or new_sl < pos.sl:
                            self._modify_sl(pos, new_sl, info)

    def _modify_sl(self, pos, new_sl, info):
        """Internal helper to modify SL with Stop Level guards"""
        # Ensure we respect the broker's minimum stop distance
        tick = mt5.symbol_info_tick(pos.symbol)
        if not tick: return

        # Stop Level is in points
        stop_level_price = info.trade_stops_level * info.point
        
        # Check distance from current price
        if pos.type == mt5.POSITION_TYPE_BUY:
            if tick.bid - new_sl < stop_level_price:
                # Adjust to minimum allowed distance
                new_sl = tick.bid - stop_level_price - (info.point * 2) 
        else:
            if new_sl - tick.ask < stop_level_price:
                new_sl = tick.ask + stop_level_price + (info.point * 2)

        # Final sanity check: don't move SL backwards
        if pos.type == mt5.POSITION_TYPE_BUY:
            if new_sl <= pos.sl: return
        else:
            if pos.sl != 0 and new_sl >= pos.sl: return

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": pos.ticket,
            "sl": round(new_sl, info.digits),
            "tp": pos.tp
        }
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            self.logger.warning(f"⚠️ SL Modify Rejection [{pos.symbol}]: {result.comment} (Code: {result.retcode})")
        else:
            self.logger.info(f"✅ SL Optimized for {pos.symbol} #{pos.ticket} -> {new_sl}")

    async def on_message_received(self, event):
        """Handle incoming signals from Telegram"""
        sender_id = event.chat_id
        text = event.message.message
        
        # Log ALL incoming messages for debugging
        self.logger.debug(f"📨 Message from {sender_id}: {text[:100]}...")
        
        channel_info = next((c for c in self.config['channels'] if c['id'] == sender_id), None)
        if not channel_info:
            self.logger.debug(f"⏭️ Ignoring message from unmonitored channel: {sender_id}")
            return
        
        self.logger.info(f"📡 Signal received from [{channel_info.get('name', sender_id)}]")
        
        signal = self.parser.parse_message(text, channel_info)
        
        if not signal:
            # Check if this message looked like it should have been a signal before logging failure
            looks_like_signal = any(kw in text.upper() for kw in ['BUY', 'SELL', 'LONG', 'SHORT', 'MOVE SL', 'CLOSE'])
            if looks_like_signal:
                self.logger.warning(f"❌ Failed to parse potential signal: {text[:150]}...")
                self.trade_history.append({
                    "time": time.strftime("%H:%M:%S"),
                    "symbol": "PARSE_FAIL",
                    "type": "--",
                    "target": "--",
                    "status": f"Parser failed",
                    "success": False
                })
            else:
                self.logger.debug(f"⏭️ Silently ignoring non-signal message")
            return
            
        self.logger.info(f"✅ Parsed: {signal['side']} {signal['symbol']} Entry:{signal.get('entry')} SL:{signal.get('sl')} TPs:{signal.get('tps')}")
        
        # --- Deduplication Check ---
        dedup_key = f"{signal['symbol']}_{signal['side']}"
        now = time.time()
        last_seen = self._recent_signals.get(dedup_key, 0)
        
        if now - last_seen < 10:  # 10-second dedup window
            self.logger.warning(f"⚠️ Duplicate signal ignored: {dedup_key} (seen {now - last_seen:.1f}s ago)")
            return
            
        self._recent_signals[dedup_key] = now
        
        # Cleanup old entries (>60s) to prevent memory leak over uptime
        cutoff = now - 60
        self._recent_signals = {k: v for k, v in self._recent_signals.items() if v > cutoff}
        
        if signal.get('action'):
            self.logger.info(f"🔄 Update signal detected: {signal['action']}")
            await self.handle_signal_update(signal)
        elif not self._check_spread(signal):
            self.logger.warning(f"🚫 Trade Aborted: Spread exceeds limit for {signal['symbol']}")
            self.trade_history.append({
                "time": time.strftime("%H:%M:%S"),
                "symbol": signal['symbol'],
                "type": signal['side'],
                "target": "--",
                "status": f"Spread limit exceeded",
                "success": False
            })
            return
        else:
            await self.execute_trade(signal)

    async def handle_signal_update(self, signal):
        """Process updates like MOVE SL or CLOSE for existing trades"""
        raw_symbol = signal['symbol']
        action = signal['action']
        val = signal['action_val']
        
        self.logger.info(f"🔄 Processing Update Action: {action} for {raw_symbol}")
        
        # 1. MT5 Updates
        if self.config['mt5']['enabled']:
            symbol = self._resolve_mt5_symbol(raw_symbol)
            if symbol:
                positions = mt5.positions_get(symbol=symbol)
                if positions:
                    for pos in positions:
                        if pos.magic != self.config['mt5']['magic_number']: continue
                        
                        if action == "MOVE_SL":
                            new_sl = pos.price_open if val == "BE" else float(val)
                            info = mt5.symbol_info(symbol)
                            if info:
                                self._modify_sl(pos, new_sl, info)
                            else:
                                self.logger.error(f"Failed to get info for {symbol} during SL update")
                        elif action == "CLOSE":
                            self._close_mt5_position(pos)
                else:
                    self.logger.debug(f"🔍 No open MT5 positions for {symbol} found to update")
                        
        # 2. Bybit Updates
        if self.config['bybit']['enabled'] and self.bybit_session:
            # Resolve Bybit Symbol (remove suffix if needed)
            bybit_symbol = signal['symbol'].replace(self.config['trading'].get('symbol_suffix', ''), '')
            
            if action == "MOVE_SL":
                try:
                    self.bybit_session.set_trading_stop(
                        category="linear", symbol=bybit_symbol, stopLoss=str(val), 
                        tpslMode="Full", positionIdx=0
                    )
                    self.logger.info(f"✅ Bybit SL Updated: {bybit_symbol} -> {val}")
                    # Update local state if found
                    rid = next((k for k, v in self.active_signals.items() if v.get('symbol') == bybit_symbol), None)
                    if rid:
                        self.active_signals[rid]['sl'] = val
                        self._save_state()
                except InvalidRequestError as e:
                    self.logger.error(f"❌ Bybit SL Update Failed [{e.ret_code}]: {e.message}")
                except Exception as e:
                    self.logger.error(f"⚠️ Bybit SL Update Error: {e}")
            
            elif action == "CLOSE":
                try:
                    # Closing by placing an opposite market order
                    # 1. Get current position to find size
                    pos_resp = self.bybit_session.get_positions(category="linear", symbol=bybit_symbol)
                    positions = pos_resp.get('result', {}).get('list', [])
                    
                    for p in positions:
                        size = float(p.get('size', 0))
                        if size > 0:
                            side = "Sell" if p['side'] == "Buy" else "Buy"
                            self.bybit_session.place_order(
                                category="linear",
                                symbol=bybit_symbol,
                                side=side,
                                orderType="Market",
                                qty=str(size),
                                reduceOnly=True
                            )
                            self.logger.info(f"🛑 Bybit Position Closed: {bybit_symbol} ({size})")
                            # Remove from active signals
                            rid = next((k for k, v in self.active_signals.items() if v.get('symbol') == bybit_symbol), None)
                            if rid:
                                del self.active_signals[rid]
                                self._save_state()
                except Exception as e:
                    self.logger.error(f"❌ Bybit Market Close Failed: {e}")

    async def process_manual_signal(self, text, asset_type='forex'):
        """Manually process a signal text (for UI testing)"""
        self.logger.info(f"🧪 Manual Signal Injection [Type: {asset_type}]: {text[:50]}...")
        
        # Mock channel info for parsing
        channel_info = {
            'name': 'MANUAL_TEST',
            'id': 'UI',
            'type': asset_type
        }
        
        signal = self.parser.parse_message(text, channel_info)
        if not signal:
            self.logger.error("❌ Manual Parse Failed")
            return {"status": "error", "message": "Failed to parse signal text"}
            
        self.logger.info(f"✅ Manual Signal Parsed: {signal['side']} {signal['symbol']}")
        
        # Execute (standard flow)
        if signal.get('action'):
            await self.handle_signal_update(signal)
            return {"status": "success", "message": f"Update {signal['action']} processed"}
        else:
            # Spread check
            if not self._check_spread(signal):
                self.logger.warning(f"🚫 Range Aborted: Spread limit for {signal['symbol']}")
                return {"status": "error", "message": "Spread exceeds limit"}
            
            await self.execute_trade(signal)
            return {"status": "success", "message": f"Trade {signal['side']} {signal['symbol']} dispatched"}

    def _close_mt5_position(self, pos):
        """Close an MT5 position completely"""
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY,
            "position": pos.ticket,
            "price": mt5.symbol_info_tick(pos.symbol).bid if pos.type == mt5.POSITION_TYPE_BUY else mt5.symbol_info_tick(pos.symbol).ask,
            "magic": pos.magic,
            "comment": "CLOSE SIGNAL",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            self.logger.error(f"Failed to close {pos.ticket}: {result.comment}")
        else:
            self.logger.info(f"✅ Closed Position {pos.ticket}")
            # Remove from active signals
            rid = next((k for k, v in self.active_signals.items() if v.get('ticket') == pos.ticket), None)
            if rid:
                del self.active_signals[rid]
                self._save_state()

    def _check_spread(self, signal):
        """Verify if current spread is within allowed limits"""
        symbol = signal['symbol']
        asset_type = signal.get('type', 'forex')
        
        # For crypto, we don't check spread via MT5 - it goes through Bybit
        crypto_keywords = ['USDT', 'USDC', 'BUSD', 'BTC', 'ETH', 'SOL', 'XRP', 'DOGE']
        is_crypto = any(kw in symbol.upper() for kw in crypto_keywords) or asset_type == 'crypto'
        
        if is_crypto:
            self.logger.debug(f"Spread check skipped for crypto: {symbol}")
            return True
        
        # MT5 spread check for forex/metals
        info = mt5.symbol_info(symbol)
        if not info: 
            self.logger.warning(f"Could not get symbol info for {symbol} - trying with suffix")
            # Try with broker suffix
            suffix = self.config['trading'].get('symbol_suffix', '')
            if suffix:
                info = mt5.symbol_info(symbol + suffix)
            if not info:
                self.logger.error(f"Symbol {symbol} not found in MT5")
                return False
        
        current_spread = info.spread # in points
        
        # Determine limit based on asset type (Metals vs Forex)
        metals_keywords = ['XAU', 'GOLD', 'XAG', 'SILVER', 'XPT', 'PLATINUM', 'XPD', 'PALLADIUM']
        is_metal = any(kw in symbol.upper() for kw in metals_keywords)
        
        if is_metal:
            limit = self.config['trading'].get('max_spread_gold', 800)
            asset_label = "METAL"
        else:
            limit = self.config['trading'].get('max_spread_forex', 5)
            asset_label = "FOREX"
        
        self.logger.debug(f"Spread check for {symbol} ({asset_label}): {current_spread} vs limit {limit}")
        return current_spread <= limit

    def _resolve_mt5_symbol(self, raw_symbol):
        """Resolve the actual MT5 symbol, ensuring it is TRADEABLE (not disabled/readonly)"""
        
        def is_tradeable(sym):
            info = mt5.symbol_info(sym)
            if not info: return False
            # Ensure FULL trading is enabled (not disabled, close-only, or long/short only)
            if info.trade_mode != mt5.SYMBOL_TRADE_MODE_FULL:
                self.logger.warning(f"Symbol {sym} trade mode is restricted: {info.trade_mode}")
                return False
            return True

        # 1. Try raw symbol
        if is_tradeable(raw_symbol):
            mt5.symbol_select(raw_symbol, True)
            return raw_symbol
        
        # 2. Try with broker suffix
        suffix = self.config['trading'].get('symbol_suffix', '')
        if suffix:
            suffixed = raw_symbol + suffix
            if is_tradeable(suffixed):
                mt5.symbol_select(suffixed, True)
                self.logger.debug(f"Symbol resolved to tradeable variant: {raw_symbol} -> {suffixed}")
                return suffixed
        
        # 3. Last ditch: If we found the raw symbol but it was disabled, and no suffix worked, 
        # maybe we should just return it and let the order fail? 
        # No, better to return None so we don't spam errors about "Tick data".
        
        self.logger.error(f"❌ Symbol {raw_symbol} not found or TRADING DISABLED (checked suffix: '{suffix}')")
        return None

    async def execute_trade(self, signal):
        """Direct execution logic based on UI settings"""
        if not self.new_trades_enabled:
            self.logger.info(f"⏸️ Skipping new trade for {signal['symbol']} (Standby Mode Active)")
            self.trade_history.append({
                "time": time.strftime("%H:%M:%S"),
                "symbol": signal['symbol'],
                "type": signal['side'],
                "target": "--",
                "status": "Skipped (Standby)",
                "success": False
            })
            return
            
        tp_mode = self.config['trading'].get('tp_mode', 'hybrid')
        self.logger.info(f"⚡ Executing {signal['symbol']} in {tp_mode.upper()} mode")
        
        if signal['type'] == 'forex':
            await self._execute_mt5_trade(signal, tp_mode)
        elif signal['type'] == 'crypto':
            await self._execute_bybit(signal)

    async def _execute_mt5_trade(self, signal, mode):
        """Unified execution logic for MT5 positions"""
        raw_symbol = signal['symbol']
        symbol = self._resolve_mt5_symbol(raw_symbol)
        if not symbol:
            self._log_failed_trade(raw_symbol, signal, "Symbol not found in MT5")
            return
            
        side = mt5.ORDER_TYPE_BUY if signal['side'] == 'BUY' else mt5.ORDER_TYPE_SELL
        info = mt5.symbol_info(symbol)
        
        # Try to get tick with small retries
        tick = None
        for _ in range(5):
            tick = mt5.symbol_info_tick(symbol)
            if tick: break
            await asyncio.sleep(0.1)
            
        if not tick:
            self._log_failed_trade(symbol, signal, "Could not get tick data (timeout)")
            return
            
        balance = mt5.account_info().balance
        total_lot = self.risk_manager.calculate_mt5_lot(info, signal['entry'], signal['sl'], balance)
        
        # Prepare execution tasks based on mode
        orders_to_place = [] # list of (lot, tp_price, comment_suffix)
        
        if mode == 'split':
            splits = self.config['trading'].get('tp_split', [33, 33, 34])
            min_v = info.volume_min
            lot1 = max(min_v, round(total_lot * (splits[0]/100), 2))
            lot2 = max(min_v, round(total_lot * (splits[1]/100), 2))
            lot3 = round(total_lot - (lot1 + lot2), 2)
            
            # If total is too small for 3 positions, just use 1
            if lot3 <= 0 or (lot1 + lot2 + lot3) > (total_lot * 1.1):
                lots = [total_lot, 0, 0]
            else:
                lots = [lot1, lot2, lot3]
                
            for i, tp_price in enumerate(signal['tps']):
                if i >= 3: break
                if lots[i] > 0:
                    orders_to_place.append((lots[i], tp_price, f"Split TP{i+1}"))
                    
        elif mode == 'scalper' and signal['tps']:
            orders_to_place.append((total_lot, signal['tps'][0], "Scalper"))
            
        elif mode == 'progressive' and signal['tps']:
            final_tp_idx = min(2, len(signal['tps']) - 1)
            orders_to_place.append((total_lot, signal['tps'][final_tp_idx], "Progressive"))
            
        else: # sniper or hybrid
            final_tp_idx = 1 if self.config['trading'].get('final_target') == 'tp2' else 2
            if len(signal['tps']) <= final_tp_idx: final_tp_idx = len(signal['tps']) - 1
            if final_tp_idx >= 0 and final_tp_idx < len(signal['tps']):
                orders_to_place.append((total_lot, signal['tps'][final_tp_idx], mode.capitalize()))

        # Execute all determined orders
        for lot, tp_price, comment_suffix in orders_to_place:
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": lot,
                "type": side,
                "price": tick.ask if side == mt5.ORDER_TYPE_BUY else tick.bid,
                "sl": signal['sl'],
                "tp": tp_price,
                "magic": self.config['mt5']['magic_number'],
                "comment": f"{comment_suffix}: {signal['channel_name']}"[:31], # MT5 max comment is 31 chars
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            result = mt5.order_send(request)
            
            if mode == 'progressive' and result and result.retcode == mt5.TRADE_RETCODE_DONE:
                signal['progressive'] = True
                signal['tp1_closed'] = False
                signal['tp2_closed'] = False
                signal['original_volume'] = lot
                
            self._log_trade(result, symbol, lot, signal)

    def _log_trade(self, result, symbol, lot, signal):
        """Log trade result and append to history"""
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            self.logger.error(f"❌ Trade Failed for {symbol}: {result.comment}")
            status = f"Failed: {result.comment}"
            success = False
        else:
            self.logger.info(f"✅ Trade Placed: {symbol} {lot} lots")
            status = "Filled"
            success = True
            
            # Store the ticket in active_signals for protection tracking
            signal_id = f"{symbol}_{int(time.time())}"
            signal['ticket'] = result.order
            self.active_signals[signal_id] = signal
            
        self.trade_history.append({
            "time": time.strftime("%H:%M:%S"),
            "symbol": symbol,
            "type": signal['side'],
            "target": str(signal['tps'][0]) if signal['tps'] else "--",
            "status": status,
            "success": success
        })
        self._save_state()

    def _log_failed_trade(self, symbol, signal, reason):
        """Log a failure before MT5 order sending"""
        self.logger.error(f"❌ Execution Aborted for {symbol}: {reason}")
        self.trade_history.append({
            "time": time.strftime("%H:%M:%S"),
            "symbol": symbol,
            "type": signal['side'],
            "target": "--",
            "status": f"Error: {reason}",
            "success": False
        })
        self._save_state()

    async def _execute_bybit(self, signal):
        """Bybit Logic (to be refined for Multi-TP/Partial)"""
        # For now keeping basic, will expand if user specifically asks for Bybit TP split
        symbol = signal['symbol']
        
        # Guard: Don't attempt trades if Bybit never authenticated
        if self.bybit_status != "AUTHENTICATED":
            self.logger.error(f"❌ Bybit Skipped: Not authenticated (Status: {self.bybit_status})")
            self.trade_history.append({
                "time": time.strftime("%H:%M:%S"), "symbol": symbol, "type": signal['side'],
                "target": "--", "status": f"Bybit: {self.bybit_status}", "success": False
            })
            return
        
        side = "Buy" if signal['side'] == "BUY" else "Sell"
        try:
            balance_resp = self.bybit_session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
            balance = float(balance_resp['result']['list'][0]['totalEquity'])
            instrument_resp = self.bybit_session.get_instruments_info(category="linear", symbol=symbol)
            rules = instrument_resp['result']['list'][0]
            qty = self.risk_manager.calculate_bybit_qty(rules, signal['entry'], signal['sl'], balance)
            
            tp_mode = self.config['trading'].get('tp_mode', 'hybrid')
            initial_tpsl_mode = "Partial" if tp_mode == 'progressive' else "Full"
            
            order_kwargs = {
                "category": "linear",
                "symbol": symbol,
                "side": side,
                "orderType": "Market",
                "qty": str(qty),
                "stopLoss": str(signal['sl']),
                "slOrderType": "Market",
                "positionIdx": 0,
                "tpslMode": initial_tpsl_mode
            }
            
            if tp_mode != 'progressive' and signal.get('tps'):
                order_kwargs["takeProfit"] = str(signal['tps'][0])
                order_kwargs["tpOrderType"] = "Market"

            order_resp = self.bybit_session.place_order(**order_kwargs)
            
            if order_resp.get('retCode', 1) != 0:
                self.logger.error(f"❌ Bybit Order Failed [{order_resp.get('retCode')}]: {order_resp.get('retMsg')}")
                self.trade_history.append({
                    "time": time.strftime("%H:%M:%S"), "symbol": symbol, "type": signal['side'],
                    "target": "--", "status": f"Bybit: {order_resp.get('retCode')}", "success": False
                })
                self._save_state()
                return

            self.logger.info(f"✅ Bybit Main Order Placed: {symbol} {qty}")
            
            if tp_mode == 'progressive' and signal.get('tps'):
                import math
                splits = self.config['trading'].get('tp_split', [33, 33, 34])
                qty_step = float(rules['lotSizeFilter']['qtyStep'])
                min_qty = float(rules['lotSizeFilter']['minOrderQty'])
                
                accumulated_qty = 0.0
                num_tps_to_set = min(len(signal['tps']), len(splits))
                
                for i, tp_price in enumerate(signal['tps']):
                    if i >= len(splits): break
                    
                    if i == num_tps_to_set - 1:
                        # Last chunk: use the remainder of the total quantity 
                        # to ensure the sum of TP sizes equals the total position size perfectly
                        chunk_size = qty - accumulated_qty
                        # Final floor per precision step to avoid floating point overshoot
                        chunk_size = math.floor(chunk_size / qty_step) * qty_step
                    else:
                        raw_chunk = qty * (splits[i] / 100.0)
                        chunk_size = math.floor(raw_chunk / qty_step) * qty_step
                        
                    chunk_size = max(min_qty, chunk_size)
                    
                    if chunk_size > 0:
                        accumulated_qty += chunk_size
                        try:
                            self.bybit_session.set_trading_stop(
                                category="linear",
                                symbol=symbol,
                                tpslMode="Partial",
                                takeProfit=str(tp_price),
                                tpOrderType="Market",
                                tpSize=str(round(chunk_size, 8)),
                                stopLoss=str(signal['sl']),
                                slOrderType="Market",
                                slSize=str(round(chunk_size, 8)),
                                positionIdx=0
                            )
                            self.logger.info(f"✅ Bybit Partial TP{i+1} Set: {symbol} {chunk_size} @ {tp_price}")
                        except Exception as e:
                            self.logger.error(f"❌ Failed to set Bybit Partial TP{i+1}: {e}")

            self.trade_history.append({
                "time": time.strftime("%H:%M:%S"), "symbol": symbol, "type": signal['side'],
                "target": "TP1" if not signal.get('tps') else str(signal['tps'][0]), 
                "status": f"Bybit: {qty}", "success": True
            })
            self._save_state()
        except InvalidRequestError as e:
            # pybit v5 exceptions might have ret_code or retCode depending on version/context
            ret_code = getattr(e, 'ret_code', getattr(e, 'retCode', 'UNKNOWN'))
            msg = f"Bybit API Error [{ret_code}]: {e.message}"
            if ret_code == 10002:
                msg = "Bybit Auth Error: Clock sync issue (Error 10002). Please sync Windows clock."
            elif ret_code in (10003, 10004):
                msg = "Bybit Auth Error: Invalid API Keys or Environment (Testnet/Mainnet) mismatch."
            elif ret_code == 10005:
                msg = "Bybit Auth Error: API Key lacks 'Trade' permissions."
                
            self.logger.error(f"❌ {msg}")
            self.trade_history.append({
                "time": time.strftime("%H:%M:%S"), "symbol": symbol, "type": signal['side'],
                "target": "--", "status": f"Bybit: {ret_code}", "success": False
            })
            self._save_state()
        except FailedRequestError as e:
            self.logger.error(f"❌ Bybit HTTP Error: {e.message} (Status: {e.status_code})")
        except Exception as e:
            self.logger.error(f"❌ Bybit Execution Error: {e}")
            self.trade_history.append({
                "time": time.strftime("%H:%M:%S"), "symbol": symbol, "type": signal['side'],
                "target": "--", "status": "Bybit: Error", "success": False
            })
