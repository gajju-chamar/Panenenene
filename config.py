import os

# Telegram Settings
TOKEN = os.getenv("TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", 0))
BOT_USERNAME = os.getenv("BOT_USERNAME", "YourBotUsername")

# Channel IDs (Optional)
STICKER_LOG_CHANNEL_ID = os.getenv("STICKER_LOG_CHANNEL_ID")
INBOX_CHANNEL_ID = os.getenv("INBOX_CHANNEL_ID")

# Database
MONGO_URL = os.getenv("MONGO_URL")
