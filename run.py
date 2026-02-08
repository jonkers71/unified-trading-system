import logging
import yaml
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from backend.trading_engine import TradingEngine
from pydantic import BaseModel

# Setup Logging with UTF-8 support for Emojis
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/unified_trader.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    try:
        with open("config/settings.yaml", "r") as f:
            config = yaml.safe_load(f)
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
        "monitored_channels": engine.monitored_channels if engine else []
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
        
        # Save to file
        try:
            with open("config/settings.yaml", "w") as f:
                yaml.dump(engine.config, f)
            return {"status": "success", "message": "Settings saved & updated"}
        except Exception as e:
            return {"status": "error", "message": f"Failed to save: {e}"}
            
    return {"status": "error", "message": "Engine not initialized"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
