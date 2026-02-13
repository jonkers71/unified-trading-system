import yaml
import os

config_path = "config/settings.yaml"

if os.path.exists(config_path):
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    print("Current Bybit Setting:", config.get('bybit', {}).get('testnet'))
    
    # Switch to Mainnet
    if 'bybit' in config:
        config['bybit']['testnet'] = False
        print("Switching Bybit to Mainnet (testnet: False)")
    
    with open(config_path, 'w') as f:
        yaml.dump(config, f)
    print("Settings updated successfully.")
else:
    print(f"Error: {config_path} not found.")
