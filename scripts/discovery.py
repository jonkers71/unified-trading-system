import asyncio
import yaml
from telethon import TelegramClient

async def list_channels():
    # Load config to get credits
    try:
        # Now running from root, so path is simple
        with open("config/settings.yaml", "r") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"Error: Could not load config/settings.yaml. Make sure it exists. {e}")
        return

    client = TelegramClient(
        config['telegram']['session_name'],
        config['telegram']['api_id'],
        config['telegram']['api_hash']
    )

    await client.start(phone=config['telegram']['phone_number'])
    print("\n--- 📢 YOUR TELEGRAM CHANNELS & GROUPS ---")
    print(f"{'ID':<15} | {'NAME'}")
    print("-" * 40)

    async for dialog in client.iter_dialogs():
        if dialog.is_channel or dialog.is_group:
            print(f"{dialog.id:<15} | {dialog.name}")
    
    print("\n--- Copy the ID (including the minus sign) into your settings.yaml ---")
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(list_channels())
