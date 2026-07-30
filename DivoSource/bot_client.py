from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from config import BOT_TOKEN

if not BOT_TOKEN or BOT_TOKEN == 'your_bot_token':
    raise ValueError("الرجاء وضع توكن البوت في ملف .env")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
