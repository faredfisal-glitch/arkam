import asyncio
import os
import re
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, StartBotRequest, ReportRequest
from telethon.tl.functions.account import GetPasswordRequest, ReportPeerRequest, GetAuthorizationsRequest, UpdatePasswordSettingsRequest, ConfirmPasswordEmailRequest, SendVerifyEmailCodeRequest, VerifyEmailRequest
from telethon.tl.functions.auth import LogOutRequest, ResetAuthorizationsRequest
from telethon import password as telethon_password
from telethon.tl import types
from telethon.tl.functions.contacts import BlockRequest
from telethon.tl.types import InputReportReasonSpam, InputReportReasonViolence, InputReportReasonPornography, InputReportReasonChildAbuse, InputReportReasonCopyright, InputReportReasonOther
from telethon import errors

from DivoSource.accounts import get_client, advanced_clear_sessions
from DivoSource.database import (
    get_accounts,
    get_account_by_phone,
    update_account_status,
    update_account_2fa_password,
    delete_account_from_db,
)
from DivoSource.antiban import human_simulation
from DivoSource.logger import logger
from config import MAX_CONCURRENT_ACCOUNTS, SESSIONS_DIR

async def execute_parallel(action_func, *args, count=None, check_all=False, skip_simulation=False, **kwargs):
    """ينفذ وظيفة على عدد محدد من الحسابات النشطة بالتوازي ويعيد النتائج"""
    accounts = await get_accounts()
    if check_all:
        target_accounts = accounts
    else:
        target_accounts = [acc for acc in accounts if acc[2] == "active"]
    
    if not target_accounts:
        return []

    if count is not None:
        target_accounts = target_accounts[:count]

    results = []
    
    # تقسيم الحسابات إلى دفعات بناءً على MAX_CONCURRENT_ACCOUNTS
    for i in range(0, len(target_accounts), MAX_CONCURRENT_ACCOUNTS):
        batch = target_accounts[i:i + MAX_CONCURRENT_ACCOUNTS]
        tasks = []
        
        async def worker(acc):
            phone, session_name, status, proxy = acc[0], acc[1], acc[2], acc[3]
            from DivoSource.accounts import get_phone_lock
            async with get_phone_lock(phone):
                client = await get_client(phone, session_name, proxy)
                try:
                    await client.connect()
                    if not await client.is_user_authorized():
                        await update_account_status(phone, "inactive")
                        return (phone, False, "❌ حساب غير مسجل")
                    
                    if not skip_simulation:
                        await human_simulation()
                    # تمرير client و phone وأي متغيرات أخرى
                    res = await action_func(client, phone, *args, **kwargs)
                    return (phone, True, res)
                except Exception as e:
                    # إشعار فوري إذا تعطلت الجلسة أثناء التنفيذ
                    if "auth key" in str(e).lower() or "session" in str(e).lower():
                        from DivoSource.bot_client import bot
                        from config import OWNER_ID
                        import datetime
                        time_now = datetime.datetime.now().strftime('%I:%M:%S %p')
                        await bot.send_message(OWNER_ID, f"🚨 **تنبيه فوري: تعطلت جلسة الحساب {phone}!**\n⏰ الوقت: `{time_now}`\nالسبب: `{e}`")
                        await update_account_status(phone, 'revoked')
                    return (phone, False, f"⚠️ {str(e)[:50]}")
                finally:
                    await asyncio.shield(client.disconnect())

        for acc in batch:
            tasks.append(asyncio.create_task(worker(acc)))
        
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for br in batch_results:
            if isinstance(br, Exception):
                pass
            else:
                results.append(br)
                
        if not skip_simulation:
            await asyncio.sleep(0.5)
        
    return results

async def execute_for_phone(phone, action_func, *args, **kwargs):
    account = await get_account_by_phone(phone)
    if not account:
        return [(phone, False, "❌ لم يتم العثور على الحساب المطلوب")]

    phone, session_name, status, proxy = account[0], account[1], account[2], account[3]
    if status != "active":
        return [(phone, False, "❌ الحساب غير نشط")]

    from DivoSource.accounts import get_phone_lock
    async with get_phone_lock(phone):
        client = await get_client(phone, session_name, proxy)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                await update_account_status(phone, "inactive")
                return [(phone, False, "❌ حساب غير مسجل")]

            await human_simulation()
            res = await action_func(client, phone, *args, **kwargs)
            return [(phone, True, res)]
        except Exception as e:
            return [(phone, False, f"⚠️ {str(e)[:50]}")]
        finally:
            await asyncio.shield(client.disconnect())

# ================= الوظائف =================

async def _action_get_code(client, phone):
    try:
        async for msg in client.iter_messages(777000, limit=2):
            if msg.text:
                codes = re.findall(r'\b(\d(?:\s*\d){4,5})\b', msg.text)
                if codes:
                    clean_code = codes[0].replace(" ", "").replace("\t", "")
                    return f"🔑 الكود: {clean_code}"
                return f"📝 {msg.text[:50]}..."
        return "لا توجد رسائل"
    except Exception as e:
        return f"خطأ: {str(e)[:30]}"

async def get_codes_task():
    return await execute_parallel(_action_get_code, skip_simulation=True)

async def get_code_for_phone_task(phone: str):
    account = await get_account_by_phone(phone)
    if not account:
        return False, "❌ لم يتم العثور على الحساب المطلوب."

    _, session_name, status, proxy, twofa_password = account
    if status != "active":
        return False, "❌ الحساب غير نشط."

    client = await get_client(phone, session_name, proxy)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            await update_account_status(phone, "inactive")
            return False, "❌ الحساب غير مسجل دخول."

        # قراءة الأكواد لا تحتاج لمحاكاة بشرية لأنها لا ترسل شيء
        code_text = "لا يوجد كود داخل آخر رسالة."
        async for msg in client.iter_messages(777000, limit=5):
            if not msg.text:
                continue

            codes = re.findall(r'\b(\d(?:\s*\d){4,5})\b', msg.text)
            if codes:
                clean_code = codes[0].replace(" ", "").replace("\t", "")
                code_text = f"🔑 الكود: {clean_code}"
                break

        result = code_text
        if twofa_password:
            result += f"\n\n🔐 كلمة تحقق الحساب:\n<code>{twofa_password}</code>"
        return True, result
    except Exception as e:
        return False, f"❌ {str(e)[:80]}"
    finally:
        try: await asyncio.shield(client.disconnect())
        except Exception: pass


async def _detect_freeze_notice(client, phone):
    freeze_notice = (
        "your account was blocked for violations of the telegram terms of service "
        "based on user reports confirmed by our moderators."
    )
    official_targets = ("777000", "@SpamBot")

    def normalize_text(text: str) -> str:
        text = (text or "").lower()
        return " ".join(text.split())

    async def has_freeze_text(messages):
        for message in messages:
            text = normalize_text((message.raw_text or "") + " " + (getattr(message, "message", "") or ""))
            if freeze_notice in text:
                logger.info(f"Freeze notice found for {phone}: {text[:200]}")
                return True
        return False

    for target in official_targets:
        try:
            messages = [msg async for msg in client.iter_messages(target, limit=5)]
            if await has_freeze_text(messages):
                return True
        except Exception as target_error:
            logger.warning(
                f"Freeze notice scan failed for {phone} via {target}: "
                f"{target_error.__class__.__name__}: {target_error}"
            )

    return False


async def _action_check_status(client, phone):
    try:
        me = await client.get_me()
        name = me.first_name or ""
        username = f"@{me.username}" if me.username else "لا يوجد معرف"
        is_frozen = await _detect_freeze_notice(client, phone)

        if is_frozen:
            await update_account_status(phone, "frozen")
        else:
            await update_account_status(phone, "active")

        return {"text": f"{name} | {username}", "is_frozen": is_frozen}
    except Exception as e:
        await update_account_status(phone, "inactive")
        return "غير مفعل"

async def check_status_task():
    return await execute_parallel(_action_check_status, check_all=True, skip_simulation=True)

async def _action_check_email(client, phone):
    try:
        pw = await client(GetPasswordRequest())
        if pw.email_unconfirmed_pattern:
            return f"بريد غير مؤكد: {pw.email_unconfirmed_pattern}"
        elif pw.has_password:
            return "🔐 توجد كلمة مرور"
        else:
            return "لا يوجد بريد"
    except Exception:
        return "خطأ في الفحص"

async def check_email_task():
    return await execute_parallel(_action_check_email)

async def _action_count_sessions(client, phone):
    try:
        authorizations = await client(GetAuthorizationsRequest())
        count = len(authorizations.authorizations)
        return f"📊 {count} جلسة نشطة"
    except Exception as e:
        return f"⚠️ خطأ: {str(e)[:25]}"

async def count_sessions_task():
    return await execute_parallel(_action_count_sessions, check_all=True, skip_simulation=True)

async def get_channel_entity(client, identifier):
    identifier = identifier.strip()
    if identifier.startswith('https://t.me/+'):
        hash_part = identifier.split('+')[1]
        return await client(ImportChatInviteRequest(hash_part))
    elif identifier.startswith('https://t.me/'):
        username = identifier.replace('https://t.me/', '').split('/')[0]
        return await client.get_entity(username)
    else:
        return await client.get_entity(identifier)

async def _action_join(client, phone, link):
    try:
        entity = await get_channel_entity(client, link)
        if isinstance(entity, ImportChatInviteRequest):
            return "✅ تم الانضمام عبر رابط دعوة" # Already imported
        await client(JoinChannelRequest(entity))
        return "✅ تم الانضمام"
    except errors.UserAlreadyParticipantError:
        return "⚠️ عضو بالفعل"
    except errors.InviteHashExpiredError:
        return "⏰ رابط منتهي"
    except Exception as e:
         return f"❌ {str(e)[:25]}"

async def join_channel_task(link: str, count: int = None):
    return await execute_parallel(_action_join, link, count=count)

async def _action_leave(client, phone, link):
    try:
        entity = await get_channel_entity(client, link)
        await client(LeaveChannelRequest(entity))
        return "✅ تمت المغادرة"
    except errors.UserNotParticipantError:
        return "⚠️ ليس عضواً"
    except Exception as e:
        return f"❌ {str(e)[:25]}"

async def leave_channel_task(link: str, count: int = None):
    return await execute_parallel(_action_leave, link, count=count)

async def _action_disable_2fa(client, phone, password):
    try:
        await client.edit_2fa(current_password=password, new_password=None)
        await update_account_2fa_password(phone, None)
        return "✅ تم التعطيل"
    except errors.PasswordHashInvalidError:
        return "❌ كلمة المرور غير صحيحة"
    except Exception as e:
        return f"❌ {str(e)[:25]}"

async def disable_2fa_task(password: str, phone: str = None):
    if phone:
        return await execute_for_phone(phone, _action_disable_2fa, password)
    return await execute_parallel(_action_disable_2fa, password)

async def _action_enable_2fa(client, phone, new_password):
    try:
        await client.edit_2fa(new_password=new_password)
        await update_account_2fa_password(phone, new_password)
        return "✅ تم التفعيل"
    except Exception as e:
        return f"❌ {str(e)[:25]}"

async def enable_2fa_task(new_password: str, phone: str = None):
    if phone:
        return await execute_for_phone(phone, _action_enable_2fa, new_password)
    return await execute_parallel(_action_enable_2fa, new_password)

async def _action_change_2fa(client, phone, new_password):
    account = await get_account_by_phone(phone)
    if not account:
        return "❌ الحساب غير موجود في قاعدة البيانات"
    _, _, _, _, old_password = account
    
    try:
        await client.edit_2fa(current_password=old_password, new_password=new_password)
        await update_account_2fa_password(phone, new_password)
        return "✅ تم التغيير"
    except errors.PasswordHashInvalidError:
        return "❌ كلمة المرور المخزنة غير صحيحة"
    except Exception as e:
        return f"❌ {str(e)[:25]}"

async def change_2fa_task(new_password: str, phone: str = None):
    if phone:
        return await execute_for_phone(phone, _action_change_2fa, new_password)
    return await execute_parallel(_action_change_2fa, new_password)

async def _action_start_bot(client, phone, link):
    try:
        # استخراج اسم البوت وكود البدء
        bot_username = link.strip()
        payload = ""
        
        if 't.me/' in bot_username or 'telegram.me/' in bot_username:
            parts = bot_username.split('/')[-1].split('?start=')
            bot_username = parts[0]
            if len(parts) > 1:
                payload = parts[1]
        elif ' ' in bot_username:
            parts = bot_username.split(' ', 1)
            bot_username = parts[0]
            payload = parts[1]
            
        bot_username = bot_username.replace('@', '')
        
        entity = await client.get_entity(bot_username)
        if payload:
            await client(StartBotRequest(
                bot=entity,
                peer=entity,
                start_param=payload
            ))
            return f"✅ تم التشغيل (الكود: {payload})"

        await client.send_message(entity, "/start")
        return "✅ تم الضغط على Start"
    except Exception as e:
        return f"❌ {str(e)[:25]}"

async def start_bot_task(link: str, count: int = None):
    return await execute_parallel(_action_start_bot, link, count=count)

async def _action_report_peer(client, phone, link, reason_type, message):
    try:
        # استخراج المعرف والمعلومات من الرابط
        identifier = link.strip()
        msg_id = None
        
        if 't.me/' in identifier:
            parts = identifier.split('/')
            if len(parts) > 4: # t.me/channel/123
                identifier = parts[-2]
                msg_id = int(parts[-1])
            else:
                identifier = parts[-1]
        
        entity = await client.get_entity(identifier)
        
        reasons = {
            "spam": InputReportReasonSpam(),
            "violence": InputReportReasonViolence(),
            "porn": InputReportReasonPornography(),
            "child": InputReportReasonChildAbuse(),
            "copyright": InputReportReasonCopyright(),
            "other": InputReportReasonOther()
        }
        reason = reasons.get(reason_type, InputReportReasonSpam())
        
        if msg_id:
            await client(ReportRequest(entity, [msg_id], reason, message))
            return "✅ تم الإبلاغ عن الرسالة"
        else:
            await client(ReportPeerRequest(peer=entity, reason=reason, message=message))
            return "✅ تم الإبلاغ عن القناة/الجروب"
    except Exception as e:
        return f"❌ {str(e)[:25]}"

async def report_peer_task(link, reason_type, message):
    return await execute_parallel(_action_report_peer, link, reason_type, message)

async def _action_report_user(client, phone, identifier):
    try:
        entity = await client.get_entity(identifier)
        # حظر المستخدم
        await client(BlockRequest(id=entity))
        # الإبلاغ عن الحساب (كـ سبام افتراضياً)
        await client(ReportPeerRequest(peer=entity, reason=InputReportReasonSpam(), message="Spam account"))
        return "✅ تم الحظر والإبلاغ"
    except Exception as e:
        return f"❌ {str(e)[:25]}"

async def clear_other_sessions_task(phone: str, session_name: str, proxy: str = None):
    client = await get_client(phone, session_name, proxy)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            await update_account_status(phone, "inactive")
            return False, "❌ الحساب غير مسجل دخول."

        # استخدام نظام الطرد المتقدم
        success, result = await advanced_clear_sessions(client, phone, force_kick=True)
        return success, result
    except Exception as e:
        logger.error(f"خطأ أثناء تفريغ الجلسات للحساب {phone}: {e}")
        return False, f"❌ {str(e)[:80]}"
    finally:
        try: await asyncio.shield(client.disconnect())
        except Exception: pass

async def terminate_account_session_task(phone: str, session_name: str, proxy: str = None):
    client = await get_client(phone, session_name, proxy)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            await update_account_status(phone, "inactive")
            return False, "⚠️ الجلسة غير صالحة بالفعل، وسيتم حذف الحساب محلياً."

        # تسجيل خروج صريح للجلسة الحالية الخاصة بالبوت من حساب تيليجرام.
        await client(LogOutRequest())
        await client.log_out()
        return True, "✅ تم إنهاء جلسة الحساب من تيليجرام."
    except Exception as e:
        logger.error(f"خطأ أثناء إنهاء جلسة الحساب {phone}: {e}")
        return False, f"⚠️ تعذر إنهاء الجلسة من تيليجرام: {str(e)[:80]}"
    finally:
        try: await asyncio.shield(client.disconnect())
        except Exception: pass

async def report_user_task(identifier):
    return await execute_parallel(_action_report_user, identifier)

async def _action_clear_sessions(client, phone):
    success, result = await advanced_clear_sessions(client, phone, force_kick=True)
    return result

async def clear_all_sessions_task():
    """تفريغ الجلسات الأخرى من كل الحسابات النشطة دفعة واحدة"""
    return await execute_parallel(_action_clear_sessions)


async def delete_inactive_accounts_task():
    """حذف كل الحسابات غير النشطة أو المحظورة من قاعدة البيانات ومن الملفات"""
    accounts = await get_accounts()
    inactive_accounts = [acc for acc in accounts if acc[2] in ["inactive", "frozen"]]
    
    if not inactive_accounts:
        return []

    results = []
    
    async def worker(acc):
        phone, session_name, status, proxy = acc[0], acc[1], acc[2], acc[3]
        try:
            try:
                client = await get_client(phone, session_name, proxy)
                await client.connect()
                if await client.is_user_authorized():
                    await client(LogOutRequest())
                await client.disconnect()
            except Exception:
                pass
            
            await delete_account_from_db(phone)
            
            session_file = os.path.join(SESSIONS_DIR, f"{session_name}.session")
            if os.path.exists(session_file):
                os.remove(session_file)
            
            return (phone, True, f"✅ تم حذف الحساب ({status})")
        except Exception as e:
            return (phone, False, f"❌ فشل الحذف: {str(e)[:40]}")
            
    tasks = [asyncio.create_task(worker(acc)) for acc in inactive_accounts]
    batch_results = await asyncio.gather(*tasks, return_exceptions=True)
    
    for br in batch_results:
        if not isinstance(br, Exception):
            results.append(br)

    return results

async def init_change_email_task(phone: str, new_email: str):
    account = await get_account_by_phone(phone)
    if not account: return False, "❌ الحساب غير موجود"
    phone, session_name, status, proxy, password = account
    
    client = await get_client(phone, session_name, proxy)
    try:
        await client.connect()
        
        # فحص إذا كان هناك بريد معلق بالفعل
        pwd = await client(GetPasswordRequest())
        if pwd.email_unconfirmed_pattern:
            return True, f"⚠️ يوجد طلب معلق بالفعل لبريد ينتهي بـ ({pwd.email_unconfirmed_pattern}).\nيرجى إدخال الكود المرسل لهذا البريد، أو الانتظار حتى تنتهي صلاحية الطلب القديم لتتمكن من تغيير البريد."

        # محاولة طلب تغيير البريد
        await client.edit_2fa(
            current_password=password,
            email=new_email
        )
        
        return True, f"✅ تم إرسال كود التحقق إلى: {new_email}\nيرجى تزويدي بالكود المكون من 6 أرقام."
    except Exception as e:
        if "Email unconfirmed" in str(e):
             # هذه الحالة تعني أن تليجرام ينتظر الكود فعلياً
             return True, "⚠️ تليجرام ينتظر كود التأكيد للبريد الذي أدخلته للتو أو لطلب سابق.\nيرجى إرسال الكود المكون من 6 أرقام."
        return False, f"❌ خطأ: {str(e)}"
    finally:
        try: await asyncio.shield(client.disconnect())
        except Exception: pass

async def confirm_change_email_task(phone: str, code: str):
    account = await get_account_by_phone(phone)
    if not account: return False, "❌ الحساب غير موجود"
    phone, session_name, status, proxy, password = account
    
    client = await get_client(phone, session_name, proxy)
    try:
        # استخدام مهلة زمنية للاتصال
        await asyncio.wait_for(client.connect(), timeout=15)
        
        if not await client.is_user_authorized():
             return False, "⚠️ الجلسة منتهية، يرجى إعادة تسجيل الدخول."

        await client(ConfirmPasswordEmailRequest(code=code))
        return True, "✅ تم تغيير البريد الإلكتروني بنجاح."
    except asyncio.TimeoutError:
        return False, "⏰ انتهت مهلة الاتصال، قد تكون المشكلة في البروكسي."
    except Exception as e:
        logger.error(f"Error in confirm_change_email_task for {phone}: {e}")
        return False, f"❌ خطأ: {str(e)}"
    finally:
        try: await asyncio.shield(client.disconnect())
        except Exception: pass

async def init_change_login_email_task(phone: str, new_email: str, phone_code_hash: str = None):
    account = await get_account_by_phone(phone)
    if not account: return False, "❌ الحساب غير موجود"
    phone, session_name, status, proxy, password = account
    
    client = await get_client(phone, session_name, proxy)
    try:
        await client.connect()
        # طلب إرسال كود التحقق لبريد تسجيل الدخول
        try:
            await client(SendVerifyEmailCodeRequest(
                purpose=types.EmailVerifyPurposeLoginChange(),
                email=new_email
            ))
            return True, f"✅ تم إرسال كود التحقق إلى: {new_email}"
        except Exception as e:
            # محاولة استخدام Setup مع الهاش إذا توفر
            try:
                purpose = None
                if phone_code_hash:
                    purpose = types.EmailVerifyPurposeLoginSetup(phone_number=phone, phone_code_hash=phone_code_hash)
                else:
                    try:
                        purpose = types.EmailVerifyPurposeLoginSetup(phone_number=phone, phone_code_hash="")
                    except Exception:
                        purpose = types.EmailVerifyPurposeLoginSetup()

                await client(SendVerifyEmailCodeRequest(purpose=purpose, email=new_email))
                return True, f"✅ تم إرسال كود التحقق (Setup) إلى: {new_email}"
            except Exception as setup_e:
                return False, f"❌ فشل الطلب: {str(e)} | {str(setup_e)}"
    finally:
        try: await asyncio.shield(client.disconnect())
        except Exception: pass

async def confirm_change_login_email_task(phone: str, code: str, phone_code_hash: str = None):
    account = await get_account_by_phone(phone)
    if not account: return False, "❌ الحساب غير موجود"
    phone, session_name, status, proxy, password = account
    
    # استخدام الباسوورد الافتراضي إذا لم يكن مسجلاً
    import config
    actual_pwd = password or getattr(config, 'DEFAULT_2FA', "MR_Divo@2004a")
    
    client = await get_client(phone, session_name, proxy)
    try:
        await client.connect()
        logger.info(f"Attempting to confirm email for {phone} using official method...")
        
        from telethon.tl.functions.account import GetPasswordRequest, UpdatePasswordSettingsRequest
        from telethon import password as telethon_password
        from telethon.tl import types as tl_types
        
        # جلب بيانات الباسوورد وحساب الهاش
        pwd_info = await client(GetPasswordRequest())
        password_hash = telethon_password.compute_check(pwd_info, actual_pwd)
        
        # محاولة التأكيد كبريد "تسجيل دخول" (لا يشترط باسوورد)
        try:
            # نحاول أولاً باستخدام LoginChange
            from telethon.tl.functions.account import VerifyEmailRequest
            await client(VerifyEmailRequest(
                purpose=tl_types.EmailVerifyPurposeLoginChange(),
                verification=tl_types.EmailVerificationCode(code=code)
            ))
            logger.info(f"Login email confirmed via LoginChange for {phone}")
            return True, "✅ تم تغيير بريد تسجيل الدخول بنجاح."
        except Exception as e_change:
            logger.warning(f"LoginChange failed for {phone}: {e_change}. Trying LoginSetup...")
            
            # محاولة باستخدام LoginSetup (البديل المضمون)
            try:
                await client(VerifyEmailRequest(
                    purpose=tl_types.EmailVerifyPurposeLoginSetup(phone_number=phone, phone_code_hash=phone_code_hash or ""),
                    verification=tl_types.EmailVerificationCode(code=code)
                ))
                return True, "✅ تم تأكيد بريد تسجيل الدخول (LoginSetup) بنجاح."
            except Exception as e_setup:
                return False, f"❌ فشل التأكيد: {str(e_change)} | {str(e_setup)}"
        
        logger.info(f"Email confirmed officially for {phone}")
        return True, "✅ تم ربط بريد تسجيل الدخول (الاسترداد) بنجاح."
        
    except Exception as e:
        logger.error(f"Official confirmation failed for {phone}: {e}")
        return False, f"❌ خطأ في الكود أو الباسوورد: {str(e)}"
    finally:
        try: await asyncio.shield(client.disconnect())
        except Exception: pass
