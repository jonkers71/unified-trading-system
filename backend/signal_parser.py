import re
from datetime import datetime
import logging

class SignalParser:
    """
    Parses Telegram messages into standardized trade signals.
    Supports both Forex and Crypto formats.

    Supported channel formats (verified against real WolfX Forex+Gold VIP messages):

    Format A - WolfX Standard (WOLFXSIGNALS.COM footer):
        XAUUSD 📈 BUY 2050.00
        💰TP1 2055.00
        💰TP2 2060.00
        💰TP3 2070.00
        🚫SL 2045.00
        WOLFXSIGNALS.COM content

    Format B - FortunePrime footer (portal.fortuneprime.com):
        EURJPY 📉 SELL 183.847
        💰TP1 183.647
        💰TP2 183.347
        💰TP3 182.847
        🚫SL 184.647
        👉 Follow it here to obtain the best results: portal.fortuneprime.com

    Format C - Free Platinum Signal (keyword-based entry):
        🔔 FREE PLATINUM SIGNAL 🔔
        🚨 NEW ORDER – GOLD BUY 📈
        • Entry: 5167.00
        • SL: 5159.00
        • TP: 5182.00

    Format D - WolfX Crypto (Enter below keyword):
        BTC/USDT
        🔹Enter below: 69355.2
        📉SELL
        💰TP1 68854.4
        🚫SL 70381.2
        〽️Leverage x10
    """
    
    def __init__(self):
        self.logger = logging.getLogger("SignalParser")
        
        # Common regex patterns
        self.patterns = {
            # Symbol: specific knowns first, then generic letter pairs (no digits in the pair group)
            # Supports: XAUUSD, BTCUSDT, EURUSD, EURJPY, AUDJPY, USDJPY, GBPUSD, CHFJPY,
            #           NZDJPY, SILVER->XAGUSD, GOLD->XAUUSD, 1000PEPE, #BTC etc.
            'symbol': r'(?:#)?(\bXAUUSD\b|\bXAGUSD\b|\bGOLD\b|\bSILVER\b|\bBTCUSDT\b|\bETHUSDT\b|\bBCHUSDT\b|\bXRPUSDT\b|\bSOLUSDT\b|\bDOGEUSDT\b|\d*[A-Z]{3,}/?[A-Z]{3,})',
            'type': r'\b(BUY|SELL|LONG|SHORT)\b',
            # Entry: supports "ENTRY:", "PRICE:", "AT", "@", "Enter below:", "Enter at:"
            # Also handles bullet-point format: "• Entry: 5167.00"
            'price': r'(?:ENTRY\s*(?:ZONE|PRICE)?|PRICE|ENTER\s*(?:BELOW|AT|AROUND)?|AT|@)\s*:?\s*(\d+\.?\d*)',
            # SL: handles optional emojis like 🔴, 🚫, ❌, 🛑 and bullet "• SL: 5159.00"
            'sl': r'(?:🔴|🚫|❌|🛑|STOPLOSS|STOP\s*LOSS|SL)\s*:?\s*(\d+\.?\d*)',
            # TP: handles "TP1: 1234.5", "💰TP1 1234.5", "TARGET 1: 1234.5", "• TP: 1234.5"
            'tp': r'(?:💰|🤑|🎯|🏹|✅)?\s*(?:\s*)?(?:TP|TARGET|TAKEPROFIT|TARGET)\s*(\d+)?\s*:?\s*(\d+\.?\d*)',
            'action_move_sl': r'(?:MOVE SL TO|SL TO|BE)\s*:?\s*(\d+\.?\d*)',
            'action_close': r'(?:CLOSE|EXIT)\s+(?:HALF|PARTIAL|ALL|NOW)',
            # Leverage for crypto
            'leverage': r'LEVERAGE\s*[Xx]?(\d+)'
        }

        # Negative keywords that indicate analysis/recap posts, NOT actionable signals.
        # IMPORTANT: Use whole-word matching where possible to avoid false matches.
        # "RESULTS" is intentionally excluded here because "obtain the best results"
        # appears in the FortunePrime channel footer on every real signal.
        self._negative_keywords = [
            "ANALYSIS", "RECAP", "VIP PERFORMANCE", "SUMMARY",
            "FREE REPORT", "REVIEW", "OUTLOOK", "FORECAST",
            "TOTAL WEEK", "WEEK PROFIT", "ORDER CLOSED",
            "PROFIT WON", "PIPS 🟢", "FUNDED ACCOUNT"
        ]

        # Symbol aliases: map channel-specific names to MT5/Bybit symbols
        self._symbol_aliases = {
            "GOLD": "XAUUSD",
            "SILVER": "XAGUSD",
        }

    def parse_message(self, text, channel_info):
        """
        Main entry point for parsing. Returns a dict or None.
        """
        text_upper = text.upper()
        
        # 0. Noise Check: Must contain a trade side or a known update action
        has_side = re.search(self.patterns['type'], text_upper)
        has_update = (re.search(self.patterns['action_move_sl'], text_upper) or
                      re.search(self.patterns['action_close'], text_upper))
        
        if not has_side and not has_update:
            self.logger.debug("Message ignored as noise (no Side or Action found)")
            return None

        # Guard: Filter out purely informational or analytical messages.
        # Only filter if the message doesn't also contain an update action.
        if not has_update:
            for nk in self._negative_keywords:
                if nk in text_upper:
                    self.logger.debug(f"Message ignored as noise (Negative Keyword: '{nk}')")
                    return None

        # 1. Extract Symbol
        symbol_match = re.search(self.patterns['symbol'], text_upper)
        if not symbol_match:
            self.logger.debug(f"Parse fail: No symbol found in suspected signal: {text_upper[:80]}")
            return None
        symbol = symbol_match.group(1).replace('/', '')

        # Guard: Filter out common words that might be mistaken for symbols.
        # When a noise word is detected as the symbol, retry on the remaining text.
        noise_words = {
            "SIGNAL", "ALERT", "TRADE", "VIDEO", "WOLFX", "FOLLOW",
            "FREE", "JOIN", "CHANNEL", "ORDER", "CLOSED", "PLATINUM",
            "CONTACT", "SUPPORT", "NEW", "PIPS", "PROFIT"
        }
        # Retry up to 5 times in case multiple noise words appear before the real symbol
        for _ in range(5):
            if symbol not in noise_words:
                break
            # Remove the first occurrence of this noise word and search again
            remaining_text = re.sub(r'\b' + re.escape(symbol) + r'\b', '', text_upper, count=1)
            text_upper = remaining_text
            symbol_match = re.search(self.patterns['symbol'], text_upper)
            if symbol_match:
                symbol = symbol_match.group(1).replace('/', '')
            else:
                self.logger.debug(f"Symbol found was noise and no valid symbol remains.")
                return None
        if symbol in noise_words:
            self.logger.debug(f"All symbol candidates were noise words.")
            return None

        # Resolve aliases (GOLD -> XAUUSD, SILVER -> XAGUSD)
        symbol = self._symbol_aliases.get(symbol, symbol)

        # 2. Extract Side
        type_match = re.search(self.patterns['type'], text_upper)
        side = None
        if type_match:
            side = "BUY" if type_match.group(1) in ["BUY", "LONG"] else "SELL"
        
        # 3. Extract Entry Price (multiple strategies, most specific first)
        entry = self._extract_entry(text_upper, symbol)
        
        # 4. Extract SL
        sl = self._extract_value(text_upper, self.patterns['sl'])
        
        # 5. Extract TPs (Multiple)
        tps = self._extract_all_tps(text_upper)
        
        # 6. Check for Update Actions
        action = None
        action_val = None
        
        move_sl_match = re.search(self.patterns['action_move_sl'], text_upper)
        if move_sl_match:
            action = "MOVE_SL"
            action_val = move_sl_match.group(1)
            if action_val:
                try: action_val = float(action_val)
                except: pass
            else:
                action_val = "BE"
        elif re.search(self.patterns['action_close'], text_upper):
            action = "CLOSE"
            
        if not entry and not sl and not tps and not action:
            self.logger.debug(f"Parse fail: Missing all key fields for {symbol} (Entry:{entry}, SL:{sl}, TPs:{tps}, Action:{action})")
            return None
        
        # Warn if we have incomplete data for a new trade signal
        if not action and (not entry or not sl or not tps):
            self.logger.warning(f"⚠️ Incomplete signal for {symbol}: Entry={entry}, SL={sl}, TPs={tps}")

        signal = {
            'timestamp': datetime.now().isoformat(),
            'symbol': symbol,
            'side': side if side else "UPDATE",
            'entry': float(entry) if entry else None,
            'sl': float(sl) if sl else None,
            'tps': [float(tp) for tp in tps] if tps else [],
            'action': action,
            'action_val': action_val,
            'channel_name': channel_info.get('name', 'Unknown'),
            'channel_id': channel_info.get('id'),
            'type': channel_info.get('type', 'forex')
        }
        
        self.logger.info(f"Successfully parsed signal: {signal['side']} {symbol} Action: {action}")
        return signal

    def _extract_entry(self, text_upper, symbol):
        """
        Extract entry price using multiple strategies in order of reliability.

        Strategy 1: Keyword-based (ENTRY:, PRICE:, @, Enter below:) — most reliable.
                    Also handles bullet-point format "• Entry: 5167.00".
        Strategy 2: SYMBOL directly followed by a decimal price.
                    e.g. "XAUUSD 2050.00" or "EURUSD 1.08500"
        Strategy 3: SYMBOL [emoji] DIRECTION [emoji] PRICE (WolfX inline format).
                    FIX: The symbol is matched by its literal name, so digits in
                    the price are never consumed by the symbol-matching group.
                    e.g. "EURJPY 📉 SELL 183.847" -> correctly extracts 183.847
        Strategy 4: DIRECTION [emoji] PRICE (no symbol in between).
                    e.g. "📉SELL 183.847" or "BUY 2050.00"
                    Only matches if price contains a decimal point.
        """
        # Strategy 1: Keyword-based (most reliable)
        entry = self._extract_value(text_upper, self.patterns['price'])
        if entry:
            return entry

        # Strategy 2: SYMBOL directly followed by a decimal price
        sym_price_pattern = r'\b' + re.escape(symbol) + r'\b\s*(?:[^\w\s\d])?\s*(\d+\.\d+)'
        sym_match = re.search(sym_price_pattern, text_upper)
        if sym_match:
            return sym_match.group(1)

        # Strategy 3: SYMBOL [emoji] DIRECTION [emoji] PRICE
        # Use the already-extracted symbol name literally so the char class
        # cannot consume digits that are part of the price.
        # Pattern: symbol name, skip emojis/spaces, direction keyword,
        #          skip emojis/spaces (non-greedy), capture full decimal price.
        sym_dir_price_pattern = (
            r'\b' + re.escape(symbol) + r'\b'
            r'[^0-9]*?'                  # skip emojis / spaces (non-greedy)
            r'(?:BUY|SELL|LONG|SHORT)'   # direction keyword
            r'[^0-9]*?'                  # skip emojis / spaces (non-greedy)
            r'(\d+\.\d+)'               # capture full decimal price (requires dot)
        )
        m = re.search(sym_dir_price_pattern, text_upper)
        if m:
            return m.group(1)

        # Also handle: DIRECTION SYMBOL EMOJI PRICE
        inline_sym_pattern = (
            r'(?:BUY|SELL|LONG|SHORT)'
            r'\s+' + re.escape(symbol) + r'\b'
            r'[^0-9]*?'
            r'(\d+\.\d+)'
        )
        m2 = re.search(inline_sym_pattern, text_upper)
        if m2:
            return m2.group(1)

        # Strategy 4: DIRECTION [emoji] PRICE (no symbol in between)
        # Only match if price has a decimal point to avoid grabbing TP/SL numbers
        inline_pattern = r'(?:BUY|SELL|LONG|SHORT)[^0-9]*?(\d+\.\d+)'
        inline_match = re.search(inline_pattern, text_upper)
        if inline_match:
            return inline_match.group(1)

        return None

    def _extract_value(self, text, pattern):
        match = re.search(pattern, text)
        return match.group(1) if match else None

    def _extract_all_tps(self, text):
        """
        Extract all TP prices from text.
        Handles formats like:
        - TP1: 1.234, TP2: 1.250
        - 💰TP1 68854.4
        - TARGET 1: 1.234
        - • TP: 83.80  (single TP bullet format)
        """
        # Primary pattern: emoji + TP/TARGET label + optional number + price
        tp_pattern = r'(?:💰|🤑|🎯|🏹|✅)?\s*(?:TP|TARGET)\s*\d*\s*:?\s*(\d+\.\d+|\d{4,})'
        tps = re.findall(tp_pattern, text)

        # Fallback: bullet-point single TP "• TP: 83.80"
        if not tps:
            bullet_tp = re.search(r'[•\-]\s*TP\s*:?\s*(\d+\.\d+)', text)
            if bullet_tp:
                tps = [bullet_tp.group(1)]
        
        # Remove duplicates while preserving order, and filter out small numbers (like TP indices)
        seen = set()
        result = []
        for tp in tps:
            if tp not in seen and (float(tp) > 10 or '.' in tp):
                seen.add(tp)
                result.append(tp)
        
        return result


if __name__ == "__main__":
    # -----------------------------------------------------------------------
    # Test suite — run with: python3 backend/signal_parser.py
    # -----------------------------------------------------------------------
    parser = SignalParser()
    channel_forex = {'name': 'WolfX Forex+Gold VIP', 'type': 'forex', 'id': -1001}
    channel_crypto = {'name': 'Wolfx Crypto VIP', 'type': 'crypto', 'id': -1002}
    
    tests = []

    # --- Format A: WolfX Standard (WOLFXSIGNALS.COM footer) ---
    tests.append(("WolfX XAUUSD BUY", channel_forex, """
XAUUSD 📈 BUY 2050.00
💰TP1 2055.00
💰TP2 2060.00
💰TP3 2070.00
🚫SL 2045.00
WOLFXSIGNALS.COM content
""", {"symbol": "XAUUSD", "side": "BUY", "entry": 2050.0, "sl": 2045.0, "tps_len": 3}))

    # --- Format B: FortunePrime footer (the "RESULTS" false-block bug) ---
    tests.append(("FortunePrime XAUUSD BUY", channel_forex, """
XAUUSD 📈 BUY 5006.00
💰TP1 5008.00
💰TP2 5011.00
💰TP3 5016.00
🚫SL 4998.00
👉 Follow it here to obtain the best results: portal.fortuneprime.com
""", {"symbol": "XAUUSD", "side": "BUY", "entry": 5006.0, "sl": 4998.0, "tps_len": 3}))

    tests.append(("FortunePrime USDJPY SELL (3dp)", channel_forex, """
USDJPY 📉 SELL 159.582
💰TP1 159.382
💰TP2 159.082
💰TP3 158.582
🚫SL 160.382
👉 Follow it here to obtain the best results: portal.fortuneprime.com
""", {"symbol": "USDJPY", "side": "SELL", "entry": 159.582, "sl": 160.382, "tps_len": 3}))

    tests.append(("FortunePrime GBPUSD BUY", channel_forex, """
GBPUSD 📈 BUY 1.3288
💰TP1 1.3308
💰TP2 1.3338
💰TP3 1.3388
🚫SL 1.3208
👉 Follow it here to obtain the best results: portal.fortuneprime.com
""", {"symbol": "GBPUSD", "side": "BUY", "entry": 1.3288, "sl": 1.3208, "tps_len": 3}))

    tests.append(("FortunePrime CHFJPY SELL", channel_forex, """
CHFJPY 📉 SELL 202.25
💰TP1 202.05
💰TP2 201.75
💰TP3 201.25
🚫SL 203.05
👉 Follow it here to obtain the best results: portal.fortuneprime.com
""", {"symbol": "CHFJPY", "side": "SELL", "entry": 202.25, "sl": 203.05, "tps_len": 3}))

    # --- Bug fix tests: JPY pairs with decimal prices ---
    tests.append(("EURJPY SELL 183.847 (BUG FIX)", channel_forex, """
EURJPY 📉 SELL 183.847
💰TP1 183.647
💰TP2 183.347
💰TP3 182.847
🚫SL 184.647
👉 Follow it here to obtain the best results: portal.fortuneprime.com
""", {"symbol": "EURJPY", "side": "SELL", "entry": 183.847, "sl": 184.647, "tps_len": 3}))

    tests.append(("AUDJPY BUY 110.476 (BUG FIX)", channel_forex, """
AUDJPY 📈 BUY 110.476
💰TP1 110.676
💰TP2 110.976
💰TP3 111.476
🚫SL 109.676
👉 Follow it here to obtain the best results: portal.fortuneprime.com
""", {"symbol": "AUDJPY", "side": "BUY", "entry": 110.476, "sl": 109.676, "tps_len": 3}))

    tests.append(("NZDJPY SELL 92.81", channel_forex, """
NZDJPY 📉 SELL 92.81
💰TP1 92.61
💰TP2 92.31
💰TP3 91.81
🚫SL 93.61
WOLFXSIGNALS.COM content
""", {"symbol": "NZDJPY", "side": "SELL", "entry": 92.81, "sl": 93.61, "tps_len": 3}))

    # --- Format C: Free Platinum Signal (keyword-based entry) ---
    tests.append(("Free Platinum GOLD BUY", channel_forex, """
🔔 FREE PLATINUM SIGNAL 🔔
🚨 NEW ORDER – GOLD BUY 📈
• Entry: 5167.00
• SL: 5159.00
• TP: 5182.00
""", {"symbol": "XAUUSD", "side": "BUY", "entry": 5167.0, "sl": 5159.0, "tps_len": 1}))

    # --- Format D: WolfX Crypto ---
    tests.append(("WolfX Crypto BTCUSDT SELL", channel_crypto, """
BTC/USDT
🔹Enter below: 69355.2
📉SELL
💰TP1 68854.4
💰TP2 68431.9
💰TP3 66265.8
🚫SL 70381.2
〽️Leverage x10
""", {"symbol": "BTCUSDT", "side": "SELL", "entry": 69355.2, "sl": 70381.2, "tps_len": 3}))

    # --- Noise: Analysis message (should return None) ---
    tests.append(("ANALYSIS message (should be None)", channel_forex, """
📊 #XAUUSD Analysis – 24.03.2026
Gold is currently under strong downside pressure, with the market showing
a clear lack of bullish reaction so far. The first key level to watch is
around 4082. This is the nearest potential reaction zone.
""", None))

    # --- Noise: ORDER CLOSED message (should return None) ---
    tests.append(("ORDER CLOSED (should be None)", channel_forex, """
✅ ORDER CLOSED - GOLD ✅
• Entry: 5312.00
• Take Profit: 5300.00
📊 Profit Won : +120 PIPS 🟢
""", None))

    # --- Noise: Weekly recap (should return None) ---
    tests.append(("Weekly recap (should be None)", channel_forex, """
📆 16 MARCH - 22 MARCH
✅️ Monday 💰+200 PIPS 🟢
📊 Total Week Profit: 860 PIPS🔥
⭐️ 11/12 SIGNALS IN PROFITS ⭐️
""", None))

    # --- Run tests ---
    print("=" * 60)
    print("SIGNAL PARSER TEST SUITE")
    print("=" * 60)
    all_passed = True
    for name, ch, msg, expected in tests:
        result = parser.parse_message(msg, ch)
        if expected is None:
            ok = result is None
            status = "✅ PASS" if ok else f"❌ FAIL (got: {result})"
        else:
            if result is None:
                ok = False
                status = "❌ FAIL (got None)"
            else:
                checks = []
                for k, v in expected.items():
                    if k == "tps_len":
                        checks.append(len(result['tps']) == v)
                    else:
                        checks.append(result.get(k) == v)
                ok = all(checks)
                if ok:
                    status = "✅ PASS"
                else:
                    details = {k: result.get(k) for k in expected if k != 'tps_len'}
                    details['tps'] = result.get('tps')
                    status = f"❌ FAIL (got: {details})"
        if not ok:
            all_passed = False
        print(f"  {status} | {name}")

    print("=" * 60)
    if all_passed:
        print("✅ ALL TESTS PASSED")
    else:
        print("❌ SOME TESTS FAILED — review output above")
        exit(1)
