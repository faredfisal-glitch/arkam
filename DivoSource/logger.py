import logging
import os
import sys

LOGS_DIR = "bot/logs"
if not os.path.exists(LOGS_DIR):
    os.makedirs(LOGS_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f"{LOGS_DIR}/logs.txt", encoding='utf-8'),
    ]
)

logger = logging.getLogger("HybridBot")
