import re
from datetime import datetime
import logging

class SignalParser:
    """
    Parses Telegram messages into standardized trade signals.
    Supports both Forex and Crypto formats.
    """
    
    def __init__(self):
        self.logger = logging.getLogger("SignalParser")
        
        # Common regex patterns
        self.patterns = {
            'symbol': r'([A-Z]{3,}/?[A-Z]{3,}|GOLD|XAUUSD|BTCUSDT|ETHUSDT|BCHUSDT|XRPUSDT|SOLUSDT|DOGEUSDT)',
            'type': r'(BUY|SELL|LONG|SHORT)',
            # Entry: supports "ENTRY:", "PRICE:", "AT", "@", "Enter below:", "Enter at:"
            'price': r'(?:ENTRY|PRICE|ENTER\s*(?:BELOW|AT|AROUND)?|AT|@)\s*:?\s*(\d+\.?\d*)',
            'sl': r'(?:SL|STOPLOSS|STOP\s*LOSS)\s*:?\s*(\d+\.?\d*)',
            # TP: handles "TP1: 1234.5" and "💰TP1 1234.5" formats
            'tp': r'(?:💰)?\s*(?:TP|TAKEPROFIT|TARGET)\s*(\d+)?\s*:?\s*(\d+\.?\d*)',
            'action_move_sl': r'(?:MOVE SL TO|SL TO|BE)\s*:?\s*(\d+\.?\d*)',
            'action_close': r'(?:CLOSE|EXIT)\s+(?:HALF|PARTIAL|ALL|NOW)',
            # Leverage for crypto
            'leverage': r'LEVERAGE\s*[Xx]?(\d+)'
        }

    def parse_message(self, text, channel_info):
        """
        Main entry point for parsing. Returns a dict or None.
        """
        text = text.upper()
        
        # 1. Extract Symbol
        symbol_match = re.search(self.patterns['symbol'], text)
        if not symbol_match:
            self.logger.debug(f"Parse fail: No symbol found in: {text[:80]}")
            return None
        symbol = symbol_match.group(1).replace('/', '')
        
        # 2. Extract Side
        type_match = re.search(self.patterns['type'], text)
        if not type_match:
            self.logger.debug(f"Parse fail: No BUY/SELL found for {symbol}")
            return None
        side = "BUY" if type_match.group(1) in ["BUY", "LONG"] else "SELL"
        
        # 3. Extract Entry
        # First try keyword-based extraction (ENTRY:, PRICE:, Enter below:, etc.)
        entry = self._extract_value(text, self.patterns['price'])
        
        # If no keyword entry found, try extracting price directly after BUY/SELL
        # Format: "XAUUSD 📈 BUY 5009.00" or "USDJPY SELL 156.98"
        if not entry:
            inline_pattern = r'(?:BUY|SELL|LONG|SHORT)\s+(\d+\.?\d*)'
            inline_match = re.search(inline_pattern, text)
            if inline_match:
                entry = inline_match.group(1)

        
        # 4. Extract SL
        sl = self._extract_value(text, self.patterns['sl'])
        
        # 5. Extract TPs (Multiple)
        tps = self._extract_all_tps(text)
        
        # 6. Check for Update Actions
        action = None
        action_val = None
        
        move_sl_match = re.search(self.patterns['action_move_sl'], text)
        if move_sl_match:
            action = "MOVE_SL"
            action_val = float(move_sl_match.group(1)) if move_sl_match.group(1) else "BE"
        elif re.search(self.patterns['action_close'], text):
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
            'side': side,
            'entry': float(entry) if entry else None,
            'sl': float(sl) if sl else None,
            'tps': [float(tp) for tp in tps] if tps else [],
            'action': action,
            'action_val': action_val,
            'channel_name': channel_info.get('name', 'Unknown'),
            'channel_id': channel_info.get('id'),
            'type': channel_info.get('type', 'forex')
        }
        
        self.logger.info(f"Successfully parsed signal: {side} {symbol} TP1: {tps[0]}")
        return signal

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
        """
        # This regex captures: optional emoji, TP/TARGET, optional number, then THE PRICE
        # The price is a decimal number that follows after the TP label
        tp_pattern = r'(?:💰)?\s*(?:TP|TARGET)\s*\d*\s*:?\s*(\d+\.\d+|\d{4,})'
        tps = re.findall(tp_pattern, text)
        
        # Remove duplicates while preserving order, and filter out small numbers (like TP indices)
        seen = set()
        result = []
        for tp in tps:
            # Filter: TP prices should be reasonably sized (not just "1", "2", "3")
            if tp not in seen and (float(tp) > 10 or '.' in tp):
                seen.add(tp)
                result.append(tp)
        
        return result


if __name__ == "__main__":
    # Test cases
    parser = SignalParser()
    
    # Forex test
    forex_msg = """
    🔥 GOLD BUY NOW 🔥
    Entry: 2020.50
    SL: 2015.00
    TP1: 2025.00
    TP2: 2030.00
    TP3: 2040.00
    """
    print("=== FOREX TEST ===")
    print(parser.parse_message(forex_msg, {'name': 'GoldVIP', 'type': 'forex'}))
    
    # Crypto test (WolfX format)
    crypto_msg = """
    BTC/USDT 

    🔹Enter below: 69355.2(with a minimum value of 69352.0)

    📉SELL 

    💰TP1 68854.4
    💰TP2 68431.9
    💰TP3 66265.8
    🚫SL 70381.2

    〽️Leverage x10
    """
    print("\n=== CRYPTO TEST ===")
    print(parser.parse_message(crypto_msg, {'name': 'Wolfx Crypto VIP', 'type': 'crypto'}))
    
    # WolfX Forex format (inline price after BUY/SELL)
    wolfx_gold = """
    XAUUSD 📈 BUY 5009.00

    💰TP1 5011.00
    💰TP2 5014.00
    💰TP3 5019.00
    🚫SL 5001.00

    WOLFXSIGNALS.COM content
    """
    print("\n=== WOLFX GOLD TEST ===")
    result = parser.parse_message(wolfx_gold, {'name': 'WolfX Forex VIP', 'type': 'forex'})
    print(f"Symbol: {result['symbol']}, Side: {result['side']}, Entry: {result['entry']}, SL: {result['sl']}, TPs: {result['tps']}")
    
    wolfx_jpy = """
    USDJPY 📉 SELL 156.98

    💰TP1 156.78
    💰TP2 156.48
    💰TP3 155.98
    🚫SL 157.78
    """
    print("\n=== WOLFX USDJPY TEST ===")
    result = parser.parse_message(wolfx_jpy, {'name': 'WolfX Forex VIP', 'type': 'forex'})
    print(f"Symbol: {result['symbol']}, Side: {result['side']}, Entry: {result['entry']}, SL: {result['sl']}, TPs: {result['tps']}")
