import re
from datetime import datetime

class MockParser:
    def __init__(self):
        self.patterns = {
            'symbol': r'([A-Z]{3,}/?[A-Z]{3,}|GOLD|XAUUSD|BTCUSDT|ETHUSDT|BCHUSDT|XRPUSDT|SOLUSDT|DOGEUSDT)',
            'type': r'(BUY|SELL|LONG|SHORT)',
            'price': r'(?:ENTRY|PRICE|ENTER\s*(?:BELOW|AT|AROUND)?|AT|@)\s*:?\s*(\d+\.?\d*)',
            'sl': r'(?:SL|STOPLOSS|STOP\s*LOSS)\s*:?\s*(\d+\.?\d*)',
            'action_move_sl': r'(?:MOVE SL TO|SL TO|BE)\s*:?\s*(\d+\.?\d*)',
            'action_close': r'(?:CLOSE|EXIT)\s+(?:HALF|PARTIAL|ALL|NOW)',
        }

    def parse_message(self, text):
        text = text.upper()
        
        has_side = re.search(self.patterns['type'], text)
        has_update = re.search(self.patterns['action_move_sl'], text) or re.search(self.patterns['action_close'], text)
        
        if not has_side and not has_update:
            print("Noise Filter: Ignored")
            return None

        symbol_match = re.search(self.patterns['symbol'], text)
        if not symbol_match:
            print("Fail: No symbol")
            return None
        symbol = symbol_match.group(1).replace('/', '')
        
        type_match = re.search(self.patterns['type'], text)
        side = None
        if type_match:
            side = "BUY" if type_match.group(1) in ["BUY", "LONG"] else "SELL"
        
        entry = self._extract_value(text, self.patterns['price'])
        
        if not entry:
            inline_pattern = r'(?:BUY|SELL|LONG|SHORT)\s+(\d+\.?\d*)'
            inline_match = re.search(inline_pattern, text)
            if inline_match:
                entry = inline_match.group(1)
            else:
                # NEW TEST: What if price is after symbol?
                inline_sym_pattern = r'(?:BUY|SELL|LONG|SHORT)\s+[A-Z0-9/]+\s+(\d+\.?\d*)'
                inline_sym_match = re.search(inline_sym_pattern, text)
                if inline_sym_match:
                    entry = inline_sym_match.group(1)

        sl = self._extract_value(text, self.patterns['sl'])
        tps = self._extract_all_tps(text)
        
        return {
            'symbol': symbol,
            'side': side,
            'entry': entry,
            'sl': sl,
            'tps': tps
        }

    def _extract_value(self, text, pattern):
        match = re.search(pattern, text)
        return match.group(1) if match else None

    def _extract_all_tps(self, text):
        # Current pattern
        tp_pattern = r'(?:💰)?\s*(?:TP|TARGET)\s*\d*\s*:?\s*(\d+\.\d+|\d{4,})'
        return re.findall(tp_pattern, text)

parser = MockParser()
msg = """
SIGNAL ALERT

BUY XAUUSD 5055.8

🤑TP1: 5057.8
🤑TP2: 5060.8
🤑TP3: 5069.8
🔴SL: 5041.8
"""
print(parser.parse_message(msg))
