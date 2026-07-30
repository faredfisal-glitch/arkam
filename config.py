import os
from dotenv import load_dotenv

load_dotenv()

API_ID = os.getenv("API_ID", "8186557")
API_HASH = os.getenv("API_HASH", "efd77b34c69c164ce158037ff5a0d117")
BOT_TOKEN = os.getenv("BOT_TOKEN", "8923461434:AAFEVIE6OLeEMBgNjJDNz2ssBoRTWvf8rk4")
OWNER_ID = int(os.getenv("OWNER_ID", "6740309897"))

# Anti-Ban Configurations
MAX_CONCURRENT_ACCOUNTS = int(os.getenv("MAX_CONCURRENT_ACCOUNTS", 3))
DELAY_RANGE_MIN = int(os.getenv("DELAY_RANGE_MIN", 0))
DELAY_RANGE_MAX = int(os.getenv("DELAY_RANGE_MAX", 0))
PROXY_ENABLED = os.getenv("PROXY_ENABLED", "True").lower() in ('true', '1', 't')

DB_PATH = "bot/database.sqlite"
SESSIONS_DIR = "bot/sessions"

if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR, exist_ok=True)

# Gmail IMAP Configuration for Auto-OTP
GMAIL_USER = os.getenv("GMAIL_USER", "xixsyrfrx@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "ptvi jfbb caod hdwl")
DEFAULT_2FA = os.getenv("DEFAULT_2FA", "MR_Divo@2004a")
