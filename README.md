# 🚀 Unified Trading System (Zero-Latency)

This is a modern, consolidated trading engine designed for local deployment on **Proxmox (Windows VM)**. It eliminates the need for MT5 Expert Advisors and JSON file polling, achieving execution speeds of **<50ms**.

## ✨ Features
- **Consolidated Core**: One Python service handles Telegram, MT5, and Bybit.
- **Direct Library Control**: No MT5 EA (`.mq5`) required.
- **Advanced TP Management**: Supports Hybrid (Partial Close), Multi-Position, and Target Selection.
- **Modern Dashboard**: Web-based monitoring and remote control.
- **Persistent Settings**: Configurable via UI without touching code.

## 📁 Structure
- `/backend`: FastAPI server + Trading Engine core.
- `/frontend`: Vanilla SPA Dashboard (Zero build step).
- `/config`: YAML-based settings logic.
- `/logs`: Centralized diagnostic logging.

## 🛠️ Installation (On Proxmox Windows VM)

1. **Install Python 3.11+**: Ensure "Add to PATH" is checked.
2. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
3. **Configure API Keys**: Edit `config/settings.yaml` with your Telegram, MT5, and Bybit credentials.
4. **Run the System**:
   ```bash
   python run.py
   ```

## 🖥️ Using the Dashboard
1. Open your browser to `http://localhost:8000/frontend/index.html` (or serve via Python `http.server`).
2. Monitor live signals and positions in real-time.
3. Use the **Settings** tab to adjust Risk % and TP modes on the fly.

## 🔍 Discovery Tool
To find your Telegram channel IDs:
```bash
python discovery.py
```

## 🛡️ Trade Management
- **Risk Manager**: Automatically calculates lot sizes based on your account balance and stop loss distance.
- **Signal Parser**: Normalizes inconsistent Telegram message formats into professional trade objects.
- **Safety**: Includes emergency stop logic and spread protection.
