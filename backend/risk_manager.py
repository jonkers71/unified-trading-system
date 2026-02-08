import math
import logging

class RiskManager:
    """
    Handles position sizing and risk validation for both Forex and Crypto.
    """
    
    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger("RiskManager")
        self.default_risk = config.get('trading', {}).get('default_risk_percent', 1.0) / 100.0

    def calculate_mt5_lot(self, symbol_info, entry, sl, balance):
        """
        Calculates lot size for MT5 symbols based on account balance and risk.
        Requires symbol_info from mt5.symbol_info(symbol).
        """
        if not symbol_info:
            return 0.0
            
        risk_amount = balance * self.default_risk
        
        # Calculate tick-based risk
        points_at_risk = abs(entry - sl)
        if points_at_risk == 0:
            return 0.0
            
        tick_size = symbol_info.trade_tick_size
        tick_value = symbol_info.trade_tick_value
        
        if tick_size == 0:
              return 0.0
              
        # Lot = Risk / (Points / TickSize * TickValue)
        lot = risk_amount / (points_at_risk / tick_size * tick_value)
        
        # Round to lot_step
        lot_step = symbol_info.volume_step
        lot = math.floor(lot / lot_step) * lot_step
        
        # Clamp to min/max
        lot = max(symbol_info.volume_min, min(symbol_info.volume_max, lot))
        
        return round(lot, 2)

    def calculate_bybit_qty(self, symbol_rules, entry, sl, balance):
        """
        Calculates quantity for Bybit crypto trades.
        Requires rules from market/instruments-info.
        """
        risk_amount = balance * self.default_risk
        price_diff_percent = abs(entry - sl) / entry
        
        if price_diff_percent == 0:
            return 0.0
            
        # Standard risk-based qty: Qty = RiskAmount / DistanceToSL
        qty = risk_amount / abs(entry - sl)
        
        qty_step = float(symbol_rules.get('qty_step', 0.001))
        # Round down to qty_step precision
        qty = math.floor(qty / qty_step) * qty_step
        
        return qty

    def validate_trade(self, signal, current_positions):
        """
        Check for max positions, overlapping trades, etc.
        """
        symbol = signal['symbol']
        max_pos = self.config.get('trading', {}).get('max_positions_per_symbol', 3)
        
        # Count existing positions for this symbol
        count = sum(1 for p in current_positions if p['symbol'] == symbol)
        
        if count >= max_pos:
            self.logger.warning(f"Trade rejected: Max positions reached for {symbol} ({count})")
            return False
            
        return True
