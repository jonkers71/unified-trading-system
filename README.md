# 🚀 Unified Trading System (Zero-Latency)

This is a modern, consolidated trading engine designed for local deployment on **Proxmox (Windows VM)**. It eliminates the need for MT5 Expert Advisors and JSON file polling, achieving execution speeds of **<50ms**.

## ✨ Features
- **Consolidated Core**: One Python service handles Telegram, MT5, and Bybit environments (Mainnet/Testnet).
- **Direct Library Control**: No MT5 EA (`.mq5`) required—achieving sub-50ms execution.
- **Advanced TP Management**: Supports Hybrid (Partial Close), Multi-Position, and Target Selection.
- **Modern Dashboard**: Professional Mission Control for real-time monitoring and remote control.
- **Dynamic Configuration**: Change Risk %, TP modes, and Communication Nodes instantly via UI.
- **Smart Symbol Resolution**: Automatically detects and switches to tradable broker symbols (e.g., EURUSD -> EURUSD+).

## 📁 Structure
- `/backend`: FastAPI server + Zero-Latency Trading Engine.
- `/frontend`: Vanilla SPA Dashboard (High-performance, zero build step).
- `/config`: Persistent YAML settings management.
- `/logs`: Centralized diagnostic logging with Emoji support.

## 🛠️ Installation (On Proxmox Windows VM)

1. **Install Python 3.11+**: Ensure "Add to PATH" is checked.
2. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
3. **Configure API Keys**: Edit `config/settings.yaml` (Base credentials).
4. **Run the System**:
   ```bash
   python run.py
   ```

## 🖥️ Using the Dashboard
1. Open your browser to `http://localhost:8000/frontend/index.html`.
2. **Terminal**: Monitor live executions and engine health.
3. **System Parameters**: Adjust Risk %, Execution Modes, and Broker Suffixes.
4. **Signal Nodes**: Manage Authorized Channel IDs for instant hot-reloading.

## 🔍 Discovery Tool
To find your Telegram channel IDs:
```bash
python scripts/discovery.py
```

## 🛡️ Trade Management
- **Risk Manager**: Instant lot size calculation based on live equity and SL distance.
- **Signal Parser**: Broad format support including Emojis (`🤑`, `🔴`, `💰`), inline prices, and noise filtering.
- **Safety**: Built-in spread protection, trade-mode validation, and emergency abort mission protocols.
