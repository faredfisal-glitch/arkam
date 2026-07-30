import asyncio
import datetime
from telethon import TelegramClient
from DivoSource.database import get_accounts, update_account_status, update_account_email
from DivoSource.accounts import get_client, advanced_clear_sessions, get_account_email, clean_service_messages
from DivoSource.logger import logger
from config import OWNER_ID
from DivoSource.bot_client import bot

async def check_account(phone, session_name, status, proxy, twofa, email):
    if status != 'active':
        return
    
    from DivoSource.accounts import get_phone_lock
    async with get_phone_lock(phone):
        client = await get_client(phone, session_name, proxy)
        try:
            await client.connect()
            
            if not await client.is_user_authorized():
                await update_account_status(phone, 'revoked')
                
                time_now = datetime.datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')
                msg = (
                    f"🚨 **تنبيه أمني: تم تسجيل الخروج أو طرد الجلسة!**\n\n"
                    f"📞 الرقم: `{phone}`\n"
                    f"⏰ الوقت: `{time_now}`"
                )
                await bot.send_message(OWNER_ID, msg, parse_mode="Markdown")
                logger.warning(f"تم طرد جلسة الحساب {phone}")
                return

            # تتبع تغييرات بريد تسجيل الدخول
            current_email = await get_account_email(client, twofa, login_email=True)
            if email and email != "N/A" and current_email != email:
                time_now = datetime.datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')
                msg = (
                    f"📧 **تنبيه: تم تغيير بريد تسجيل الدخول (Login Email)!**\n\n"
                    f"📞 الرقم: `{phone}`\n"
                    f"🔴 البريد القديم: `{email}`\n"
                    f"🟢 البريد الجديد: `{current_email}`\n"
                    f"⏰ الوقت: `{time_now}`"
                )
                await bot.send_message(OWNER_ID, msg, parse_mode="Markdown")
                await update_account_email(phone, current_email)
            elif not email or email == "N/A":
                await update_account_email(phone, current_email)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            if "auth key" in str(e).lower() or "session" in str(e).lower():
                 await update_account_status(phone, 'revoked')
                 time_now = datetime.datetime.now().strftime('%I:%M:%S %p')
                 await bot.send_message(OWNER_ID, f"🚨 **تنبيه: جلسة الحساب {phone} تعطلت!**\n⏰ الوقت: `{time_now}`\nالسبب: `{e}`")
            logger.error(f"خطأ في مراقبة الحساب {phone}: {e}")
        finally:
            if client:
                try:
                    await asyncio.shield(client.disconnect())
                except Exception:
                    pass


async def security_monitor_loop():
    """نظام مراقبة أمان الجلسات وتتبع التغييرات"""
    logger.info("تم بدء نظام مراقبة أمان الجلسات (كل 15 دقيقة).")
    while True:
        try:
            accounts = await get_accounts()
            # تنفيذ الفحص لجميع الحسابات بشكل متوازي
            batch_size = 5
            for i in range(0, len(accounts), batch_size):
                batch = accounts[i:i + batch_size]
                tasks = [check_account(*acc) for acc in batch]
                await asyncio.gather(*tasks, return_exceptions=True)
                await asyncio.sleep(1) # تأخير طفيف
                
        except Exception as e:
            logger.error(f"خطأ في حلقة مراقبة الأمان الرئيسية: {e}")
            
        try:
            await asyncio.sleep(900)
        except asyncio.CancelledError:
            logger.info("تم إيقاف حلقة مراقبة الأمان.")
            break

