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
            'symbol': r'([A-Z]{3,}/?[A-Z]{3,}|GOLD|XAUUSD|BTCUSDT|ETHUSDT|BCHUSDT)',
            'type': r'(BUY|SELL|LONG|SHORT)',
            'price': r'(?:ENTRY|PRICE|AT|@)\s*:?\s*(\d+\.?\d*)',
            'sl': r'(?:SL|STOPLOSS|STOP LOSS)\s*:?\s*(\d+\.?\d*)',
            'tp': r'(?:TP|TAKEPROFIT|TARGET)\s*(\d+)?\s*:?\s*(\d+\.?\d*)'
        }

    def parse_message(self, text, channel_info):
        """
        Main entry point for parsing. Returns a dict or None.
        """
        text = text.upper()
        
        # 1. Extract Symbol
        symbol_match = re.search(self.patterns['symbol'], text)
        if not symbol_match:
            return None
        symbol = symbol_match.group(1).replace('/', '')
        
        # 2. Extract Side
        type_match = re.search(self.patterns['type'], text)
        if not type_match:
             return None
        side = "BUY" if type_match.group(1) in ["BUY", "LONG"] else "SELL"
        
        # 3. Extract Entry (Handle ranges like 1.0500 - 1.0510)
        entry_prices = re.findall(r'(\d+\.\d+)', text)
        # Simple heuristic: first number usually entry, unless it's SL/TP
        # Professional logic would use position in text
        entry = self._extract_value(text, self.patterns['price'])
        
        # 4. Extract SL
        sl = self._extract_value(text, self.patterns['sl'])
        
        # 5. Extract TPs (Multiple)
        tps = self._extract_all_tps(text)
        
        if not entry or not sl or not tps:
            self.logger.warning(f"Failed to parse required fields for {symbol}: Entry={entry}, SL={sl}, TPs={tps}")
            return None

        signal = {
            'timestamp': datetime.now().isoformat(),
            'symbol': symbol,
            'side': side,
            'entry': float(entry),
            'sl': float(sl),
            'tps': [float(tp) for tp in tps],
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
        # Finds all TP sequences: TP1: 1.234, TP2: 1.250, etc.
        tps = re.findall(r'(?:TP|TARGET|TP\d+)\s*:?\s*(\d+\.?\d*)', text)
        # Remove duplicates while preserving order
        seen = set()
        return [x for x in tps if not (x in seen or seen.add(x))]

if __name__ == "__main__":
    # Test cases
    parser = SignalParser()
    test_msg = """
    🔥 GOLD BUY NOW 🔥
    Entry: 2020.50
    SL: 2015.00
    TP1: 2025.00
    TP2: 2030.00
    TP3: 2040.00
    """
    print(parser.parse_message(test_msg, {'name': 'GoldVIP', 'type': 'forex'}))
