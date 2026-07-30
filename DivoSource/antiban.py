import asyncio
import random
from typing import Callable, Any
from telethon.errors import FloodWaitError
from config import DELAY_RANGE_MIN, DELAY_RANGE_MAX
from DivoSource.logger import logger

async def delay_random():
    """انتظار وقت عشوائي لمحاكاة سلوك بشري"""
    delay = random.randint(DELAY_RANGE_MIN, DELAY_RANGE_MAX)
    logger.info(f"انتظار {delay} ثانية (Anti-Ban)")
    await asyncio.sleep(delay)

async def human_simulation():
    """محاكاة أفعال بشرية لتقليل احتمالية الحظر قبل تنفيذ العمليات الحقيقية"""
    # مثلا: تأخير إضافي، أو التوقف لفترات متقطعة
    logger.info("تنفيذ محاكاة السلوك البشري...")
    await delay_random()

async def handle_flood(e: FloodWaitError):
    """التعامل مع FloodWait بحذر"""
    wait_time = e.seconds
    logger.warning(f"تم اكتشاف FloodWait! إيقاف مؤقت لمدة {wait_time} ثانية لتجنب الحظر.")
    await asyncio.sleep(wait_time)

async def safe_execute(func: Callable, *args, **kwargs) -> Any:
    """تنفيذ آمِن للدوال مع التعامل التلقائي مع أخطاء FloodWait"""
    while True:
        try:
            await human_simulation()
            return await func(*args, **kwargs)
        except FloodWaitError as e:
            await handle_flood(e)
        except Exception as e:
            logger.error(f"خطأ غير متوقع أثناء التنفيذ الآمن: {e}")
            raise e
