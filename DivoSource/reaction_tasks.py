import asyncio
import random
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from telethon import functions, types
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.errors import FloodWaitError

from DivoSource.database import (
    get_reaction, update_reaction_status, increment_reaction_progress,
    has_account_reacted, add_reaction_account, get_accounts
)
from DivoSource.accounts import get_client
from config import DELAY_RANGE_MIN, DELAY_RANGE_MAX
from DivoSource.logger import logger
from DivoSource.utils import format_header, format_separator

def parse_message_link(link: str):
    link = link.strip().replace('https://t.me/', '').replace('t.me/', '')
    parts = link.split('/')
    if len(parts) >= 2:
        if parts[0] == 'c':
            # Private channel: t.me/c/123456789/123
            try:
                chat_id = int('-100' + parts[1])
                msg_id = int(parts[2])
                return chat_id, msg_id
            except Exception:
                pass
        else:
            # Public channel: t.me/username/123
            try:
                username = parts[0]
                msg_id = int(parts[1])
                return username, msg_id
            except Exception:
                pass
    return None, None

def generate_reaction_dashboard(reaction_id, emoji, success, failed, target, status, current_phone=None):
    text = f"<blockquote>◉╮ 📊 حالة عملية التفاعل\n"
    text += f"◉᚜┃ 🎯 الهدف: {target}\n"
    text += f"◉᚜┃ ✨ الإيموجي: {emoji}\n"
    text += f"◉᚜┃ ✅ نجاح: {success}\n"
    text += f"◉᚜┃ ❌ فشل: {failed}\n"
    
    if current_phone:
        text += f"◉᚜┃ 📱 الحساب الحالي: <code>{current_phone.replace('+', '')}</code>\n"
        
    if status == "running":
        text += f"◉╯ وضع التشغيل: 🟢 جاري العمل\n"
    elif status == "cancelled":
        text += f"◉╯ وضع التشغيل: 🔴 متوقف (إلغاء يدوي)\n"
    elif status == "completed":
        text += f"◉╯ وضع التشغيل: ✅ اكتمل\n"
    else:
        text += f"◉╯ وضع التشغيل: {status}\n"
        
    text += "</blockquote>"
        
    text += f"{format_separator()}"
    
    btns = []
    if status == "running":
        btns.append([InlineKeyboardButton(text="⏹ إيقاف العملية", callback_data=f"stop_reaction_{reaction_id}")])
    
    return text, InlineKeyboardMarkup(inline_keyboard=btns) if btns else None

async def run_reaction_task(reaction_id: int, chat_id: int, msg_id: int, bot: Bot):
    reaction_info = await get_reaction(reaction_id)
    if not reaction_info: return
    
    rid, target_link, emoji, target_count, success_count, failed_count, status = reaction_info
    
    if status != "running": return
    
    entity_str, message_id = parse_message_link(target_link)
    if not entity_str or not message_id:
        await bot.edit_message_text("❌ رابط الرسالة غير صحيح.", chat_id=chat_id, message_id=msg_id)
        await update_reaction_status(reaction_id, "error_invalid_link")
        return

    accounts = await get_accounts()
    active_accounts = [acc for acc in accounts if acc[2] == "active"]
    if not active_accounts:
        await bot.edit_message_text("❌ لا يوجد حسابات نشطة للتفاعل.", chat_id=chat_id, message_id=msg_id)
        await update_reaction_status(reaction_id, "stopped_no_accounts")
        return

    current_success = success_count
    current_failed = failed_count
    
    for acc in active_accounts:
        # Check DB to see if paused/cancelled
        current_info = await get_reaction(reaction_id)
        if current_info[6] != "running":
            break
            
        if current_success >= target_count:
            break
            
        phone, session_name, stat, proxy = acc[0], acc[1], acc[2], acc[3]
        
        # Check if already reacted
        if await has_account_reacted(reaction_id, phone):
            continue
            
        from DivoSource.accounts import get_phone_lock
        async with get_phone_lock(phone):
            client = await get_client(phone, session_name, proxy)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                current_failed += 1
                await increment_reaction_progress(reaction_id, False)
                continue
                
            # Dashboard update with current phone
            txt, mark = generate_reaction_dashboard(reaction_id, emoji, current_success, current_failed, target_count, "running", phone)
            try:
                await bot.edit_message_text(text=txt, chat_id=chat_id, message_id=msg_id, reply_markup=mark, parse_mode="HTML")
            except Exception:
                pass

            entity = await client.get_entity(entity_str)
            
            # محاولة الانضمام (اختياري لكن يفضل)
            try:
                await client(JoinChannelRequest(entity))
            except Exception:
                pass

            # تنفيذ التفاعل
            await client(functions.messages.SendReactionRequest(
                peer=entity,
                msg_id=message_id,
                reaction=[types.ReactionEmoji(emoticon=emoji)]
            ))
                    
            await add_reaction_account(reaction_id, phone)
            current_success += 1
            await increment_reaction_progress(reaction_id, True)
            
            # Dashboard update
            txt, mark = generate_reaction_dashboard(reaction_id, emoji, current_success, current_failed, target_count, "running")
            try:
                await bot.edit_message_text(text=txt, chat_id=chat_id, message_id=msg_id, reply_markup=mark, parse_mode="HTML")
            except Exception:
                pass
                
            await asyncio.sleep(random.uniform(DELAY_RANGE_MIN, DELAY_RANGE_MAX))
            
        except FloodWaitError as e:
            logger.warning(f"FloodWait in reaction: {e}")
            current_failed += 1
            await increment_reaction_progress(reaction_id, False)
        except Exception as e:
            logger.warning(f"Error reacting with {phone}: {e}")
            current_failed += 1
            await increment_reaction_progress(reaction_id, False)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    final_info = await get_reaction(reaction_id)
    final_status = final_info[6]
    
    if final_status == 'running':
        await update_reaction_status(reaction_id, "completed")
        final_status = "completed"
        
    txt, mark = generate_reaction_dashboard(reaction_id, emoji, current_success, current_failed, target_count, final_status)
    try:
        await bot.edit_message_text(text=txt, chat_id=chat_id, message_id=msg_id, reply_markup=mark, parse_mode="HTML")
    except Exception:
        pass
