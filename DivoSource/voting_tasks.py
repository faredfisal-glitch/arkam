import asyncio
import random
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from telethon.tl.functions.channels import JoinChannelRequest, GetFullChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.types import PeerChannel
from telethon.errors import FloodWaitError, ChannelPrivateError

from DivoSource.database import (
    get_vote, update_vote_status, increment_vote_progress,
    has_account_voted, add_vote_account, get_accounts
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

def generate_vote_dashboard(vote_id, vote_type, success, failed, target, status):
    type_name = 'يستحق' if vote_type == 'yastahaqq' else 'عادي'
    text = f"<blockquote>◉╮ 📊 حالة التصويت ({type_name})\n"
    text += f"◉᚜┃ 🎯 الهدف: {target}\n"
    text += f"◉᚜┃ ✅ نجاح: {success}\n"
    text += f"◉᚜┃ ❌ فشل: {failed}\n"
    
    if status == "running":
        text += f"◉╯ وضع التشغيل: 🟢 جاري العمل\n"
    elif status == "cancelled":
        text += f"◉╯ وضع التشغيل: 🔴 متوقف (إلغاء يدوي)\n"
    elif status == "completed":
        text += f"◉╯ وضع التشغيل: ✅ اكتمل\n"
    else:
        text += f"◉╯ وضع التشغيل: {status}\n"
        
    text += "</blockquote>"
    
    btns = []
    if status == "running":
        btns.append([InlineKeyboardButton(text="⏹ إيقاف العملية", callback_data=f"stop_vote_{vote_id}")])
    
    return text, InlineKeyboardMarkup(inline_keyboard=btns) if btns else None

async def run_vote_task(vote_id: int, chat_id: int, msg_id: int, bot: Bot):
    vote_info = await get_vote(vote_id)
    if not vote_info: return
    
    vid, vtype, target_link, target_count, success_count, failed_count, status = vote_info
    
    if status != "running": return
    
    entity_str, message_id = parse_message_link(target_link)
    if not entity_str or not message_id:
        await bot.edit_message_text("❌ رابط الرسالة غير صحيح.", chat_id=chat_id, message_id=msg_id)
        await update_vote_status(vote_id, "error_invalid_link")
        return

    accounts = await get_accounts()
    active_accounts = [acc for acc in accounts if acc[2] == "active"]
    if not active_accounts:
        await bot.edit_message_text("❌ لا يوجد حسابات نشطة للتصويت.", chat_id=chat_id, message_id=msg_id)
        await update_vote_status(vote_id, "stopped_no_accounts")
        return

    current_success = success_count
    current_failed = failed_count
    
    for acc in active_accounts:
        # Check DB to see if paused/cancelled
        current_vote_info = await get_vote(vote_id)
        if current_vote_info[6] != "running":
            break
            
        if current_success >= target_count:
            break
            
        phone, session_name, stat, proxy = acc[0], acc[1], acc[2], acc[3]
        
        # Check if already voted
        if await has_account_voted(vote_id, phone):
            continue
            
        from DivoSource.accounts import get_phone_lock
        async with get_phone_lock(phone):
            client = await get_client(phone, session_name, proxy)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                current_failed += 1
                await increment_vote_progress(vote_id, False)
                continue
                
            entity = await client.get_entity(entity_str)
            
            try:
                await client(JoinChannelRequest(entity))
            except Exception as e:
                logger.warning(f"Joining failed for {phone} on {entity_str}: {e}")

            discussion_group = None
            try:
                full_channel = await client(GetFullChannelRequest(entity))
                if full_channel.full_chat.linked_chat_id:
                    logger.info(f"Found linked discussion group ID: {full_channel.full_chat.linked_chat_id}")
                    discussion_group = await client.get_entity(PeerChannel(full_channel.full_chat.linked_chat_id))
                    await client(JoinChannelRequest(discussion_group))
                    logger.info(f"Successfully joined discussion group for {entity_str}")
            except Exception as e:
                logger.warning(f"Could not join discussion group for {entity_str}: {e}")

            if vtype == 'yastahaqq':
                try:
                    await client.send_message(entity, "يستحق", comment_to=message_id)
                    logger.info(f"Successfully commented using comment_to for {entity_str}")
                except Exception as e:
                    logger.info(f"comment_to failed: {e}. Falling back to reply_to.")
                    if discussion_group:
                        await client.send_message(discussion_group, "يستحق")
                    else:
                        await client.send_message(entity, "يستحق", reply_to=message_id)
            else:
                msg = await client.get_messages(entity, ids=message_id)
                if msg and msg.buttons:
                    await msg.click(0)
                else:
                    raise Exception("لا يوجد زر شفاف في الرسالة")
                    
            await add_vote_account(vote_id, phone)
            current_success += 1
            await increment_vote_progress(vote_id, True)
            
            # Dashboard update
            txt, mark = generate_vote_dashboard(vote_id, vtype, current_success, current_failed, target_count, "running")
            try:
                await bot.edit_message_text(text=txt, chat_id=chat_id, message_id=msg_id, reply_markup=mark, parse_mode="HTML")
            except Exception:
                pass
                
            await asyncio.sleep(random.uniform(DELAY_RANGE_MIN, DELAY_RANGE_MAX))
            
        except FloodWaitError as e:
            logger.warning(f"FloodWait in voting: {e}")
            current_failed += 1
            await increment_vote_progress(vote_id, False)
        except Exception as e:
            logger.warning(f"Error voting with {phone}: {e}")
            current_failed += 1
            await increment_vote_progress(vote_id, False)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    final_info = await get_vote(vote_id)
    final_status = final_info[6]
    
    if final_status == 'running':
        await update_vote_status(vote_id, "completed")
        final_status = "completed"
        
    txt, mark = generate_vote_dashboard(vote_id, vtype, current_success, current_failed, target_count, final_status)
    try:
        await bot.edit_message_text(text=txt, chat_id=chat_id, message_id=msg_id, reply_markup=mark, parse_mode="HTML")
    except Exception:
        pass
