import logging
import yaml
import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import PlainTextResponse
from backend.trading_engine import TradingEngine
from pydantic import BaseModel

# Ensure logs directory exists
os.makedirs('logs', exist_ok=True)

# Setup Logging with UTF-8 support for Emojis
logging.basicConfig(
    level=logging.DEBUG, # Default to DEBUG, will be adjusted after config load
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/unified_trader.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("API")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    try:
        with open("config/settings.yaml", "r") as f:
            config = yaml.safe_load(f)
        
        # Apply log level from config
        log_level = config.get('system', {}).get('log_level', 'INFO').upper()
        logging.getLogger().setLevel(getattr(logging, log_level, logging.INFO))
        logger.info(f"Log level set to: {log_level}")
        
        engine = TradingEngine(config)
        asyncio.create_task(engine.start())
        yield
    except Exception as e:
        print(f"Failed to start engine: {e}")
        yield

app = FastAPI(title="Unified Trading System API", lifespan=lifespan)
engine = None

# Mount Frontend
app.mount("/frontend", StaticFiles(directory="frontend"), name="frontend")

class ConfigUpdate(BaseModel):
    risk_percent: float
    tp_mode: str
    final_target: str = "tp3"
    be_enabled: bool = True
    be_buffer: float = 5.0
    trailing_enabled: bool = False
    trailing_distance: float = 15.0
    symbol_suffix: str = ""
    channel_ids: str = ""
    max_spread_forex: float = 5.0
    max_spread_gold: float = 800.0
    tp_split_1: int = 33
    tp_split_2: int = 33

@app.get("/status")
async def get_status():
    return {
        "engine_active": engine is not None,
        "mt5_connected": True if engine and engine.config['mt5']['enabled'] else False,
        "mt5_latency": engine.mt5_latency if engine else 0,
        "bybit_connected": True if engine and engine.config['bybit']['enabled'] else False,
        "bybit_latency": engine.bybit_latency if engine else 0,
        "daily_profit": engine.daily_profit if engine else 0.0,
        "trade_history": engine.trade_history if engine else [],
        "monitored_channels": engine.monitored_channels if engine else [],
        "settings": engine.config.get('trading', {}) if engine else {},
        "new_trades_enabled": engine.new_trades_enabled if engine else True
    }

@app.post("/settings/update")
async def update_settings(data: ConfigUpdate):
    if engine:
        engine.config['trading']['default_risk_percent'] = data.risk_percent
        engine.config['trading']['tp_mode'] = data.tp_mode
        engine.config['trading']['final_target'] = data.final_target
        engine.config['trading']['be_enabled'] = data.be_enabled
        engine.config['trading']['be_buffer'] = data.be_buffer
        engine.config['trading']['trailing_enabled'] = data.trailing_enabled
        engine.config['trading']['trailing_distance'] = data.trailing_distance
        engine.config['trading']['symbol_suffix'] = data.symbol_suffix
        engine.config['trading']['max_spread_forex'] = data.max_spread_forex
        engine.config['trading']['max_spread_gold'] = data.max_spread_gold
        engine.config['trading']['tp_split'] = [data.tp_split_1, data.tp_split_2, 100 - (data.tp_split_1 + data.tp_split_2)]
        
        # Update channel list if provided
        if data.channel_ids:
            # Assuming comma separated IDs
            ids = [id.strip() for id in data.channel_ids.split(",") if id.strip()]
            # This is a bit simplified, but we update the internal monitored list
            # Usually we'd want names too, but we can stick to IDs for now or preserve existing names
            new_channels = []
            for cid in ids:
                existing = next((c for c in engine.config.get('channels', []) if str(c['id']) == cid), None)
                if existing:
                    new_channels.append(existing)
                else:
                    new_channels.append({"id": int(cid) if cid.startswith("-") or cid.isdigit() else cid, "name": f"Node {cid}"})
            engine.config['channels'] = new_channels
            engine.monitored_channels = new_channels
        
        # Save to file
        try:
            with open("config/settings.yaml", "w") as f:
                yaml.dump(engine.config, f)
            return {"status": "success", "message": "Settings saved & updated"}
        except Exception as e:
            return {"status": "error", "message": f"Failed to save: {e}"}
            
    return {"status": "error", "message": "Engine not initialized"}

@app.post("/engine/toggle-trades")
async def toggle_trades():
    if engine:
        engine.new_trades_enabled = not engine.new_trades_enabled
        status = "ENABLED" if engine.new_trades_enabled else "STANDBY"
        return {"status": "success", "new_state": engine.new_trades_enabled, "message": f"Global trade execution is now {status}"}
    return {"status": "error", "message": "Engine not initialized"}

@app.get("/logs", response_class=PlainTextResponse)
async def view_logs(lines: int = 100):
    """View the last N lines of the log file"""
    try:
        with open('logs/unified_trader.log', 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
            return ''.join(all_lines[-lines:])
    except FileNotFoundError:
        return "Log file not found. No activity yet."
    except Exception as e:
        return f"Error reading logs: {e}"

@app.post("/logs/level/{level}")
async def set_log_level(level: str):
    """Set the logging level dynamically: DEBUG, INFO, WARNING, ERROR"""
    level = level.upper()
    if level not in ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']:
        return {"status": "error", "message": f"Invalid level. Use: DEBUG, INFO, WARNING, ERROR, CRITICAL"}
    
    logging.getLogger().setLevel(getattr(logging, level))
    
    # Also update config if engine is running
    if engine:
        if 'system' not in engine.config:
            engine.config['system'] = {}
        engine.config['system']['log_level'] = level
        try:
            with open("config/settings.yaml", "w") as f:
                yaml.dump(engine.config, f)
        except:
            pass # Non-critical if save fails
    
    return {"status": "success", "message": f"Log level set to {level}"}

@app.get("/logs/level")
async def get_log_level():
    """Get current logging level"""
    return {"level": logging.getLevelName(logging.getLogger().level)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
