import sys
import os
from unittest.mock import MagicMock

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mock MetaTrader5 before importing TradingEngine
mock_mt5 = MagicMock()
sys.modules['MetaTrader5'] = mock_mt5

from backend.trading_engine import TradingEngine

class MockSymbolInfo:
    def __init__(self, name, trade_mode):
        self.name = name
        self.trade_mode = trade_mode

def test_symbol_resolution():
    config = {
        'mt5': {'enabled': True, 'magic_number': 123},
        'bybit': {'enabled': False},
        'channels': [],
        'trading': {'symbol_suffix': '+', 'tp_mode': 'hybrid'}
    }
    
    engine = TradingEngine(config)
    
    # Test Case 1: Raw symbol is tradeable
    # SYMBOL_TRADE_MODE_FULL = 4
    mock_mt5.SYMBOL_TRADE_MODE_FULL = 4
    mock_mt5.SYMBOL_TRADE_MODE_DISABLED = 0
    
    def side_effect_info(sym):
        if sym == "EURUSD":
            return MockSymbolInfo("EURUSD", 4) # FULL
        return None
        
    mock_mt5.symbol_info.side_effect = side_effect_info
    
    resolved = engine._resolve_mt5_symbol("EURUSD")
    print(f"Test 1 (Raw Tradeable): {resolved} -> Expected: EURUSD")
    assert resolved == "EURUSD"

    # Test Case 2: Raw symbol is disabled, suffix is tradeable
    def side_effect_info_2(sym):
        if sym == "EURUSD":
            return MockSymbolInfo("EURUSD", 0) # DISABLED
        if sym == "EURUSD+":
            return MockSymbolInfo("EURUSD+", 4) # FULL
        return None
        
    mock_mt5.symbol_info.side_effect = side_effect_info_2
    
    resolved = engine._resolve_mt5_symbol("EURUSD")
    print(f"Test 2 (Suffix Required): {resolved} -> Expected: EURUSD+")
    assert resolved == "EURUSD+"

    # Test Case 3: Raw symbol is restricted (Close Only = 1), suffix is tradeable
    def side_effect_info_3(sym):
        if sym == "EURUSD":
            return MockSymbolInfo("EURUSD", 1) # Restricted
        if sym == "EURUSD+":
            return MockSymbolInfo("EURUSD+", 4) # FULL
        return None
        
    mock_mt5.symbol_info.side_effect = side_effect_info_3
    
    resolved = engine._resolve_mt5_symbol("EURUSD")
    print(f"Test 3 (Restricted Fallback): {resolved} -> Expected: EURUSD+")
    assert resolved == "EURUSD+"

    print("\n✅ All Symbol Resolution Tests Passed!")

if __name__ == "__main__":
    test_symbol_resolution()
