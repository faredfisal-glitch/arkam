import asyncio
import json
import random
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from telethon import errors
from telethon.tl.functions.channels import InviteToChannelRequest, JoinChannelRequest
from telethon.tl.functions.contacts import ResolveUsernameRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.errors import (
    FloodWaitError, UserPrivacyRestrictedError, UserNotMutualContactError, 
    UserChannelsTooMuchError, PeerFloodError, ChatAdminRequiredError,
    UserAlreadyParticipantError, InviteHashExpiredError
)
from telethon.tl.types import UserStatusOnline, UserStatusRecently, UserStatusOffline, ChannelParticipantsRecent

from DivoSource.database import (
    get_transfer, update_transfer_status, increment_transfer_progress,
    update_transfer_members_data, get_accounts, update_account_status
)
from DivoSource.accounts import get_client
from config import DELAY_RANGE_MIN, DELAY_RANGE_MAX
from DivoSource.logger import logger

async def get_target_entity(client, target_link):
    target = target_link.strip().replace('https://t.me/', '').replace('@', '')
    try:
        # If it's a private link, resolve it directly using the link (client must have joined already)
        if '+' in target or 'joinchat/' in target or 'chat/' in target:
            return await client.get_entity(target_link)
        return await client.get_entity(target)
    except Exception as e:
        logger.error(f"Error resolving target entity {target_link}: {e}")
        return None

async def safe_join(client, link):
    if not link or link == 'file': 
        return
    try:
        entity = None
        if 'chat/' in link or '+' in link:
            hash_str = link.split('+')[-1] if '+' in link else link.split('chat/')[-1]
            try:
                res = await client(ImportChatInviteRequest(hash_str))
                entity = getattr(res, 'chats', [None])[0]
            except (UserAlreadyParticipantError, InviteHashExpiredError):
                # If already in, try to get entity from link/hash
                try: entity = await client.get_entity(link)
                except: pass
        else:
            target = link.strip().replace('https://t.me/', '').replace('@', '')
            entity = await client.get_entity(target)
            await client(JoinChannelRequest(entity))
        
        # --- Priming Step ---
        # Fetching participants helps the account 'see' users and avoids 'Peer Not Found' errors
        if entity:
            try:
                await client.get_participants(entity, limit=100)
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Could not join/prime {link}: {e}")

async def scrape_public_members_gen(client, source_link):
    source = source_link.strip().replace('https://t.me/', '')
    async for user in client.iter_participants(source):
        if not user.bot and not user.deleted:
            yield user.username if user.username else user.id

async def scrape_hidden_members_gen(client, source_link):
    source = source_link.strip().replace('https://t.me/', '')
    users = set()
    queue = asyncio.Queue(maxsize=1000)

    async def producer_recent():
        try:
            async for user in client.iter_participants(source, filter=ChannelParticipantsRecent(), limit=200):
                if not user.bot and not user.deleted:
                    await queue.put(f"{user.id}:{user.username}" if user.username else str(user.id))

        except Exception: pass

    async def producer_search():
        try:
            search_queries = "abcdefghijklmnopqrstuvwxyz0123456789ابتثجحخدذرزسشصضطظعغفقكلمنهوي"
            batch_size = 3
            for i in range(0, len(search_queries), batch_size):
                batch = search_queries[i:i+batch_size]
                tasks = []
                for char in batch:
                    async def one_search(c):
                        try:
                            from telethon.tl.functions.channels import GetParticipantsRequest
                            from telethon.tl.types import ChannelParticipantsSearch
                            p = await client(GetParticipantsRequest(channel=source, filter=ChannelParticipantsSearch(c), offset=0, limit=200, hash=0))
                            for u in p.users:
                                if not getattr(u, 'bot', False) and not getattr(u, 'deleted', False):
                                    await queue.put(f"{u.id}:{u.username}" if getattr(u, 'username', None) else str(u.id))
                        except Exception: pass
                    tasks.append(one_search(char))
                await asyncio.gather(*tasks)
                await asyncio.sleep(0.1)
                if len(users) > 40000: break

        except Exception: pass

    async def producer_messages():
        try:
            async for msg in client.iter_messages(source, limit=None): 
                if msg.sender_id:
                    sender = getattr(msg, 'sender', None)
                    if sender:
                        if not getattr(sender, 'bot', False) and not getattr(sender, 'deleted', False):
                            await queue.put(f"{sender.id}:{sender.username}" if sender.username else str(sender.id))
                    else:
                        await queue.put(str(msg.sender_id))
                if len(users) > 70000: break
                if len(users) % 500 == 0: await asyncio.sleep(0.01)

        except Exception: pass

    # Run all producers
    producers = [
        asyncio.create_task(producer_recent()),
        asyncio.create_task(producer_search()),
        asyncio.create_task(producer_messages())
    ]

    try:
        while not all(p.done() for p in producers) or not queue.empty():
            try:
                # Wait for an item with timeout to check producers status
                item = await asyncio.wait_for(queue.get(), timeout=0.5)
                uid_str = item.split(":")[0]
                if uid_str.isdigit():
                    uid = int(uid_str)
                    if uid not in users:
                        users.add(uid)
                        yield item
            except asyncio.TimeoutError:
                continue
            except Exception: break
    finally:
        for p in producers:
            if not p.done():
                p.cancel()
        # Wait a bit for tasks to cancel
        if producers:
            await asyncio.gather(*producers, return_exceptions=True)

async def scrape_online_members_gen(client, source_link):
    source = source_link.strip().replace('https://t.me/', '')
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    async for user in client.iter_participants(source):
        if not user.bot and not user.deleted:
            is_active = False
            if isinstance(user.status, (UserStatusOnline, UserStatusRecently)):
                is_active = True
            elif isinstance(user.status, UserStatusOffline):
                if user.status.was_online > now - timedelta(hours=24):
                    is_active = True
            if is_active:
                yield user.username if user.username else user.id

async def incremental_scraper(client, source, ttype, users_queue, shared_state, transfer_id):
    shared_state['scraper_done'] = False
    try:
        if ttype == "public":
            gen = scrape_public_members_gen(client, source)
        elif ttype == "hidden":
            gen = scrape_hidden_members_gen(client, source)
        elif ttype == "online":
            gen = scrape_online_members_gen(client, source)
        else:
            shared_state['scraper_done'] = True
            return

        async for user_id in gen:
            users_queue.append(user_id)
            if len(users_queue) % 50 == 0:
                # Yield control
                await asyncio.sleep(0.01)
            
            # Periodically save to DB to prevent data loss if crash occurs
            if len(users_queue) % 200 == 0:
                await update_transfer_members_data(transfer_id, json.dumps(users_queue))
                
    except Exception as e:
        logger.error(f"Incremental scraper error: {e}")
    finally:
        shared_state['scraper_done'] = True
        # Final save of whatever we found
        await update_transfer_members_data(transfer_id, json.dumps(users_queue))

def generate_dashboard(tid, success, failed, remaining, status, total_target=0, scraper_done=True, extra_info=None, privacy=0, already_in=0):
    text = f"<blockquote>◉╮ 🔄 حالة النقل\n"
    text += f"◉᚜┃ ✅ نجاح: {success} / {total_target}\n"
    text += f"◉᚜┃ 👥 موجودين بالفعل: {already_in}\n"
    text += f"◉᚜┃ 🔒 خصوصية: {privacy}\n"
    text += f"◉᚜┃ ❌ فشل تقني: {failed}\n"
    if extra_info:
        text += f"◉᚜┃ ℹ️ تفاصيل: {extra_info}\n"
    rem_text = f"{remaining}" if scraper_done else f"{remaining} (جاري السحب... ⏳)"
    text += f"◉᚜┃ ⏳ باقي من القائمة: {rem_text}\n"
    text += f"◉╯ الحالة: {status}</blockquote>"
    
    btns = []
    if status == "running":
        btns.append([InlineKeyboardButton(text="⏸ إيقاف مؤقت", callback_data=f"pause_transfer_{tid}")])
    elif status == "paused":
        btns.append([InlineKeyboardButton(text="▶️ استئناف", callback_data=f"resume_transfer_{tid}")])
    
    if status in ["running", "paused"]:
        btns.append([InlineKeyboardButton(text="⏹ إلغاء وإيقاف", callback_data=f"stop_transfer_{tid}")])
    
    return text, InlineKeyboardMarkup(inline_keyboard=btns) if btns else None

async def worker_task(client, target_entity, users_queue, transfer_id, bot: Bot, chat_id, msg_id, shared_state):
    """
    Worker for a specific account. Takes users from the queue and invites them.
    shared_state is a dict: {'running': True, 'success': int, 'failed': int, 'privacy': int, 'target': int, 'last_update_count': int, 'scraper_done': bool}
    """
    client_adds_count = 0
    MAX_ADDS_PER_ACCOUNT = shared_state.get('adds_per_account', 40)
    
    # Ensure client is in target
    try:
        if not target_entity:
            # Fallback: try to resolve target_entity if it wasn't passed correctly
            target_link = shared_state.get('target_link')
            if target_link:
                target_entity = await get_target_entity(client, target_link)
        
        if target_entity:
            target_entity = await client.get_entity(getattr(target_entity, 'id', target_entity))
        else:
            logger.error(f"Worker for {getattr(client, 'phone', 'unknown')} has no valid target entity.")
            return # Cannot proceed without target
    except Exception as e:
        logger.debug(f"Worker could not verify target entity: {e}")
    
    while shared_state['running']:
        if client_adds_count >= MAX_ADDS_PER_ACCOUNT:
            logger.info(f"Account reached {MAX_ADDS_PER_ACCOUNT} adds. Resting and detaching from task.")
            try: await bot.send_message(chat_id, f"<blockquote>◉╮ ✅ حصة مكتملة\n◉᚜┃ الحساب: {getattr(client, 'phone', 'مجهول').replace('+', '')}\n◉╯ أكمل حصته ({MAX_ADDS_PER_ACCOUNT} إضافة) وتوقف.</blockquote>", parse_mode="HTML")
            except Exception: pass
            break
            
        transfer_info = await get_transfer(transfer_id)
        if not transfer_info: break
        db_status = transfer_info[4]
        if db_status != 'running':
            shared_state['running'] = False
            break
            
        if shared_state['success'] >= shared_state['target']:
            shared_state['running'] = False
            await update_transfer_status(transfer_id, "completed")
            break

        if not users_queue:
            if not shared_state.get('scraper_done', True):
                await asyncio.sleep(2)
                continue
            shared_state['running'] = False
            await update_transfer_status(transfer_id, "completed")
            break

        user = users_queue.pop(0)

        # Update UI every 15 actions collectively
        current_total = shared_state['success'] + shared_state['failed'] + shared_state.get('already_in', 0)
        if current_total - shared_state['last_update_count'] >= 15:
            shared_state['last_update_count'] = current_total
            txt, mark = generate_dashboard(
                transfer_id, 
                shared_state['success'], 
                shared_state['failed'], 
                len(users_queue), 
                "running", 
                shared_state['target'], 
                scraper_done=shared_state.get('scraper_done', True),
                privacy=shared_state.get('privacy', 0),
                already_in=shared_state.get('already_in', 0)
            )
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=txt, reply_markup=mark, parse_mode="HTML")
            except Exception: pass

        try:
            # Resolve if needed
            user_to_invite = user
            if isinstance(user, str):
                if ":" in user:
                    uid, uname = user.split(":", 1)
                    # Prioritize username for better success rate
                    if uname and uname != "None":
                        user_to_invite = uname
                    else:
                        user_to_invite = int(uid)
                elif user.isdigit():
                    user_to_invite = int(user)
                else:
                    try:
                        user_to_invite = await client.get_entity(user.replace('@', ''))
                    except Exception:
                        shared_state['failed'] += 1
                        await increment_transfer_progress(transfer_id, False)
                        continue

            # Before inviting, we must ensure the account knows this user
            # This is critical to avoid Privacy/Peer errors when using IDs
            try:
                if isinstance(user_to_invite, int):
                    user_to_invite = await client.get_input_entity(user_to_invite)
            except Exception as e:
                logger.debug(f"Could not prime user {user_to_invite}: {e}")

            # Double check if processed by another worker while we were priming
            uid_key = str(user)
            if uid_key in shared_state['processed_ids']:
                continue
            
            await client(InviteToChannelRequest(target_entity, [user_to_invite]))
            shared_state['success'] += 1
            shared_state['processed_ids'].add(uid_key)
            client_adds_count += 1
            await increment_transfer_progress(transfer_id, True)
            
            # Write to success TXT
            succ_file = f"bot/transfer_{transfer_id}_success.txt"
            with open(succ_file, "a", encoding="utf-8") as f:
                f.write(f"{user_to_invite}\n")
            
            # Smart delay
            await asyncio.sleep(random.uniform(DELAY_RANGE_MIN, DELAY_RANGE_MAX))
            
        except FloodWaitError as e:
            logger.warning(f"FloodWait on an account, sleeping for {e.seconds}s")
            users_queue.insert(0, user) # Put back user
            if e.seconds > 600:
                break
            await asyncio.sleep(e.seconds)
        except PeerFloodError:
            logger.error(f"Account {getattr(client, 'phone', 'unknown')} got PeerFloodError. Stopping worker.")
            try: await bot.send_message(chat_id, f"<blockquote>◉╮ ⚠️ توقف حساب\n◉᚜┃ الحساب: {getattr(client, 'phone', 'مجهول').replace('+', '')}\n◉╯ توقف بسبب قيود الإضافة (Flood).</blockquote>", parse_mode="HTML")
            except Exception: pass
            users_queue.insert(0, user)
            break
        except (UserPrivacyRestrictedError, UserNotMutualContactError):
            shared_state['privacy'] = shared_state.get('privacy', 0) + 1
            await increment_transfer_progress(transfer_id, False)
        except UserAlreadyParticipantError:
            shared_state['already_in'] = shared_state.get('already_in', 0) + 1
        except UserChannelsTooMuchError:
            shared_state['failed'] += 1
            await increment_transfer_progress(transfer_id, False)
        except ChatAdminRequiredError:
            logger.error("Admin required to add members to target.")
            users_queue.insert(0, user)
            shared_state['running'] = False
            await update_transfer_status(transfer_id, "stopped_admin_required")
            break
        except Exception as e:
            err_msg = str(e).lower()
            if "already" in err_msg or "participant" in err_msg:
                shared_state['already_in'] = shared_state.get('already_in', 0) + 1
                continue
            if "peer" in err_msg or "invalid" in err_msg:
                shared_state['failed'] += 1
                await increment_transfer_progress(transfer_id, False)
                continue
            logger.error(f"Worker error for {getattr(client, 'phone', 'unknown')}: {e}")
            shared_state['failed'] += 1
            await increment_transfer_progress(transfer_id, False)

async def run_transfer_job(transfer_id: int, chat_id: int, msg_id: int, bot: Bot):
    transfer_info = await get_transfer(transfer_id)
    if not transfer_info: return
    
    tid, ttype, source, target, status, success_count, failed_count, target_count, members_data_raw, adds_per_account = transfer_info
    
    logger.info(f"Job {tid} started. Type: {ttype}, Target: {target_count}, Adds/Acc: {adds_per_account}")
    
    if status not in ["running", "paused"]: return
    
    # 1. Prepare Active Clients
    accounts = await get_accounts()
    active_accounts = [acc for acc in accounts if acc[2] == "active"]
    if not active_accounts:
        await bot.send_message(chat_id, "❌ لا يوجد حسابات نشطة للقيام بالنقل.")
        await update_transfer_status(transfer_id, "stopped_no_accounts")
        return
        
    clients = []
    
    async def connect_and_verify(acc):
        phone, session_name, stat, proxy = acc[0], acc[1], acc[2], acc[3]
        from DivoSource.accounts import get_phone_lock
        async with get_phone_lock(phone):
            client = await get_client(phone, session_name, proxy)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                await update_account_status(phone, "inactive")
                await bot.send_message(chat_id, f"<blockquote>◉╮ ❌ جلسة منتهية\n◉᚜┃ الحساب: {phone.replace('+', '')}\n◉╯ غير مسجل دخول (الجلسة منتهية).</blockquote>", parse_mode="HTML")
                await client.disconnect()
                return None
            
            # Check if account is actually alive (GetMe)
            try:
                await client.get_me()
            except Exception:
                await update_account_status(phone, "inactive")
                await bot.send_message(chat_id, f"<blockquote>◉╮ ❌ حساب محظور\n◉᚜┃ الحساب: {phone.replace('+', '')}\n◉╯ هذا الحساب محظور نهائياً (Banned).</blockquote>", parse_mode="HTML")
                await client.disconnect()
                return None

            # Join groups
            try:
                await safe_join(client, source)
                await safe_join(client, target)
            except Exception as e:
                logger.warning(f"Account {phone} failed to join: {e}")
                if "CHANNELS_ADMIN_PUBLIC_RELOAD_QUOTA_EXCEEDED" in str(e):
                    await bot.send_message(chat_id, f"<blockquote>◉╮ ⚠️ قيود مؤقتة\n◉᚜┃ الحساب: {phone.replace('+', '')}\n◉╯ لديه قيود مؤقتة على الانضمام.</blockquote>", parse_mode="HTML")
            
            return client
        except Exception as e:
            logger.warning(f"Error initializing client {phone}: {e}")
            await bot.send_message(chat_id, f"<blockquote>◉╮ ⚠️ خطأ في التشغيل\n◉᚜┃ الحساب: {phone.replace('+', '')}\n◉╯ {str(e)[:50]}</blockquote>", parse_mode="HTML")
            try:
                await client.disconnect()
            except Exception:
                pass
            return None

    # Connect to all accounts in parallel
    conn_tasks = [asyncio.create_task(connect_and_verify(acc)) for acc in active_accounts]
    if conn_tasks:
        results = await asyncio.gather(*conn_tasks)
        clients = [c for c in results if c is not None]
         
    if not clients:
        await bot.send_message(chat_id, "❌ لا توجد حسابات صالحة أو نشطة للقيام بالنقل.")
        await update_transfer_status(transfer_id, "stopped_no_sessions")
        return

    await bot.send_message(chat_id, f"✅ تم تجهيز {len(clients)} حسابات للمشاركة في هذه العملية.")

    # 2. Get Members Queue
    users_queue = []
    scraper_task = None
    
    if members_data_raw:
        users_queue = json.loads(members_data_raw)
        scraper_done_init = True
    else:
        # Start incremental scraping
        scraper_done_init = False
        shared_state_scraper = {'scraper_done': False}
        scraper_task = asyncio.create_task(incremental_scraper(clients[0], source, ttype, users_queue, shared_state_scraper, transfer_id))
        
        # Wait for first 15 members or scraper done (Reduced from 100 to 15 for faster start)
        while len(users_queue) < 15 and not shared_state_scraper['scraper_done']:
            await asyncio.sleep(1)
            
        if not users_queue and shared_state_scraper['scraper_done']:
            await bot.send_message(chat_id, "❌ لم يتم العثور على أعضاء في المجموعة المصدر.")
            await update_transfer_status(transfer_id, "completed")
            for c in clients:
                try: await c.disconnect()
                except: pass
            return
        scraper_done_init = shared_state_scraper['scraper_done']

    # Resolve target entity
    try:
        target_entity = await get_target_entity(clients[0], target)
    except Exception as e:
        await bot.send_message(chat_id, f"❌ خطأ في مجموعة الهدف: {e}")
        await update_transfer_status(transfer_id, "stopped_target_error")
        for c in clients:
            try: await c.disconnect()
            except: pass
        return

    # 3. Start Workers
    shared_state = {
        'running': True,
        'success': success_count,
        'failed': failed_count,
        'privacy': 0,
        'already_in': 0,
        'processed_ids': set(), # Track members processed in this run
        'target': target_count,
        'adds_per_account': adds_per_account,
        'target_link': target, # Store link for worker fallback
        'last_update_count': success_count + failed_count,
        'scraper_done': scraper_done_init
    }
    
    # If scraper is still running, we need to keep shared_state['scraper_done'] updated
    async def scraper_monitor():
        if scraper_task:
            await scraper_task
            shared_state['scraper_done'] = True

    monitor_task = asyncio.create_task(scraper_monitor())
    
    txt, mark = generate_dashboard(tid, shared_state['success'], shared_state['failed'], len(users_queue), "running", shared_state['target'], scraper_done=shared_state['scraper_done'])
    try: await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=txt, reply_markup=mark, parse_mode="HTML")
    except Exception: pass

    tasks = []
    for client in clients:
        tasks.append(asyncio.create_task(worker_task(client, target_entity, users_queue, transfer_id, bot, chat_id, msg_id, shared_state)))

    # Wait for all workers to finish (either target reached, queue empty, or paused)
    await asyncio.gather(*tasks)

    # 4. Final Cleanup & State Save
    for c in clients: 
        try: await c.disconnect()
        except Exception: pass
        
    final_info = await get_transfer(transfer_id)
    final_status = final_info[4]
    
    # Save the remaining queue if we pause
    if final_status == 'paused':
        await update_transfer_members_data(transfer_id, json.dumps(users_queue))
        txt, mark = generate_dashboard(tid, shared_state['success'], shared_state['failed'], len(users_queue), "paused", shared_state['target'], privacy=shared_state.get('privacy', 0), already_in=shared_state.get('already_in', 0))
        try: await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=txt, reply_markup=mark, parse_mode="HTML")
        except Exception: pass
    elif final_status in ['completed', 'running']: # running here means it finished naturally
        await update_transfer_status(transfer_id, "completed")
        txt, mark = generate_dashboard(tid, shared_state['success'], shared_state['failed'], 0, "completed 🟢", shared_state['target'], privacy=shared_state.get('privacy', 0), already_in=shared_state.get('already_in', 0))
        try: await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=txt, reply_markup=None, parse_mode="HTML")
        except Exception: pass
    else: # stopped
        await update_transfer_members_data(transfer_id, json.dumps([]))
        txt, mark = generate_dashboard(tid, shared_state['success'], shared_state['failed'], len(users_queue), "stopped 🔴", shared_state['target'], privacy=shared_state.get('privacy', 0), already_in=shared_state.get('already_in', 0))
        try: await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=txt, reply_markup=None, parse_mode="HTML")
        except Exception: pass

    # Send Success File if finished or stopped
    if final_status in ['completed', 'running', 'stopped']:
        import os
        from aiogram.types import FSInputFile
        succ_file = f"bot/transfer_{transfer_id}_success.txt"
        if os.path.exists(succ_file):
            try:
                doc = FSInputFile(succ_file, filename=f"Successful_Transfers_Task_{transfer_id}.txt")
                await bot.send_document(chat_id, doc, caption="✅ ملف نصي يحتوي على جميع المستخدمين الذين نجحت إضافتهم في هذه العملية.")
                os.remove(succ_file)
            except Exception as e:
                logger.error(f"Failed to send success file: {e}")

async def run_fetch_to_file_job(ftype: str, source_link: str, target_count: int, chat_id: int, msg_id: int, bot: Bot):
    accounts = await get_accounts()
    active_accounts = [acc for acc in accounts if acc[2] == "active"]
    if not active_accounts:
        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="❌ لا يوجد حسابات نشطة للقيام بالسحب.")
        return

    # Just take the first active account to scrape
    acc = active_accounts[0]
    phone, session_name, stat, proxy = acc[0], acc[1], acc[2], acc[3]
    from DivoSource.accounts import get_phone_lock
    async with get_phone_lock(phone):
        client = await get_client(phone, session_name, proxy)
    
    try:
        await client.connect()
        if not await client.is_user_authorized():
            from DivoSource.database import update_account_status
            await update_account_status(phone, "inactive")
            await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=f"<blockquote>◉╮ ❌ جلسة منتهية\n◉᚜┃ الحساب: {phone.replace('+', '')}\n◉╯ غير مسجل دخول (الجلسة منتهية).</blockquote>", parse_mode="HTML")
            await client.disconnect()
            return

        try:
            await client.get_me()
        except Exception:
            from DivoSource.database import update_account_status
            await update_account_status(phone, "inactive")
            await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=f"<blockquote>◉╮ ❌ حساب محظور\n◉᚜┃ الحساب: {phone.replace('+', '')}\n◉╯ هذا الحساب محظور نهائياً.</blockquote>", parse_mode="HTML")
            await client.disconnect()
            return

        # Attempt to join the source link if necessary
        try:
            await safe_join(client, source_link)
        except Exception as e:
            logger.warning(f"Account {phone} failed to join source: {e}")
            if "CHANNELS_ADMIN_PUBLIC_RELOAD_QUOTA_EXCEEDED" in str(e):
                await bot.send_message(chat_id, f"<blockquote>◉╮ ⚠️ قيود مؤقتة\n◉᚜┃ الحساب: {phone.replace('+', '')}\n◉╯ لديه قيود مؤقتة على الانضمام.</blockquote>", parse_mode="HTML")

        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=f"<blockquote>◉╮ ✅ تم التوصيل\n◉᚜┃ الحساب: {phone.replace('+', '')}\n◉╯ جاري البدء في استخراج الأعضاء... ⏳</blockquote>", parse_mode="HTML")

        # Choose the right generator
        if ftype == "public":
            gen = scrape_public_members_gen(client, source_link)
        elif ftype == "hidden":
            gen = scrape_hidden_members_gen(client, source_link)
        elif ftype == "online":
            gen = scrape_online_members_gen(client, source_link)
        else:
            await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="❌ نوع السحب غير معروف.")
            return

        import time
        users = []
        last_update_time = time.time()
        
        async for user_id in gen:
            users.append(user_id)
            
            # Yield control to prevent freezing the bot
            if len(users) % 50 == 0:
                await asyncio.sleep(0.01)
                
            # Update UI message only every 3 seconds to avoid FloodWait
            if time.time() - last_update_time >= 3.0:
                last_update_time = time.time()
                try:
                    await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=f"<blockquote>◉╮ ⏳ جاري الاستخراج\n◉╯ تم العثور على: {len(users)} عضو حتى الآن.</blockquote>", parse_mode="HTML")
                except Exception:
                    pass
                
            if target_count > 0 and len(users) >= target_count:
                break

        if not users:
            await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="❌ لم يتم العثور على أي أعضاء في هذه المجموعة.")
        else:
            # Save to a file
            import os
            import time
            from aiogram.types import FSInputFile
            
            file_name = f"bot/scraped_{chat_id}_{int(time.time())}.txt"
            with open(file_name, "w", encoding="utf-8") as f:
                for u in users:
                    f.write(f"{u}\n")
            
            await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=f"<blockquote>◉╮ ✅ اكتمل السحب بنجاح!\n◉᚜┃ إجمالي الأعضاء: {len(users)}\n◉╯ جاري إرسال الملف لك...</blockquote>", parse_mode="HTML")
            
            doc = FSInputFile(file_name, filename=f"Scraped_{ftype}_{len(users)}_members.txt")
            await bot.send_document(chat_id, doc, caption=f"<blockquote>◉╮ 📥 نتائج الجلب ({ftype})\n◉᚜┃ عدد الأعضاء: {len(users)}\n◉᚜┃ الرابط المستهدف: <code>{source_link}</code>\n◉╯ يمكنك حفظ هذا الملف واستخدامه لاحقاً في خيار 'النقل عبر ملف'.</blockquote>", parse_mode="HTML")
            
            # Clean up
            os.remove(file_name)

    except Exception as e:
        logger.error(f"Error during fetch to file: {e}")
        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=f"❌ حدث خطأ أثناء عملية الجلب:\n{e}")
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


