import asyncio
import os
import json
from aiogram import types, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from DivoSource.fsm import TransferState, FetchToFileState
from config import OWNER_ID
from DivoSource.database import create_transfer, update_transfer_status

router = Router()

def get_transfer_keyboard():
    btns = [
        [InlineKeyboardButton(text="👥 النقل الظاهر", callback_data="transfer_type_public")],
        [InlineKeyboardButton(text="🟢 النقل الأونلاين", callback_data="transfer_type_online")],
        [InlineKeyboardButton(text="🕵️‍♂️ النقل الخفي", callback_data="transfer_type_hidden")],
        [InlineKeyboardButton(text="📄 النقل عبر ملف", callback_data="transfer_type_file")],
        [InlineKeyboardButton(text="📥 جلب إلى ملف", callback_data="fetch_menu")],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=btns)

@router.callback_query(F.data == "sec_transfer")
async def sec_transfer_handler(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض", show_alert=True)
    await state.clear()
    await call.message.edit_text("<blockquote>◉╮ 🚀 قسم النقل الذكي\n◉╯ اختر نوع النقل المطلوب:</blockquote>", reply_markup=get_transfer_keyboard(), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("transfer_type_"))
async def start_transfer_flow(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض")
    ttype = call.data.split("_")[2]
    await state.update_data(transfer_type=ttype)
    
    if ttype == "file":
        await call.message.edit_text("<blockquote>◉╮ 📄 قسم النقل عبر ملف\n◉╯ أرسل الآن ملف الأعضاء بصيغة TXT:</blockquote>", parse_mode="HTML")
        await state.set_state(TransferState.waiting_for_file)
    else:
        await call.message.edit_text("<blockquote>◉╮ 🔗 قسم النقل\n◉╯ أرسل رابط المجموعة المصدر (التي سيتم سحب الأعضاء منها):</blockquote>", parse_mode="HTML")
        await state.set_state(TransferState.waiting_for_source)
    await call.answer()

@router.message(TransferState.waiting_for_file, F.document)
async def process_transfer_file(message: types.Message, state: FSMContext):
    document = message.document
    if not document.file_name.lower().endswith(".txt"):
         return await message.answer("<blockquote>◉╮ ❌ خطأ في الملف\n◉╯ الرجاء إرسال ملف بصيغة TXT فقط.</blockquote>", parse_mode="HTML")
    
    file_path = f"bot/temp_transfer_{message.from_user.id}.txt"
    await message.bot.download(document, destination=file_path)
    
    try:
        content = ""
        # Try multiple encodings for better compatibility (Windows/Linux/BOM)
        for enc in ["utf-8-sig", "utf-8", "latin-1", "cp1256"]:
            try:
                with open(file_path, "r", encoding=enc) as f:
                    content = f.read()
                break
            except UnicodeDecodeError:
                continue
        
        if not content:
            if os.path.exists(file_path): os.remove(file_path)
            return await message.answer("<blockquote>◉╮ ❌ خطأ في الملف\n◉╯ تعذر قراءة محتوى الملف أو التشفير غير مدعوم.</blockquote>", parse_mode="HTML")

        raw_lines = content.splitlines()
        lines = []
        for l in raw_lines:
            l = l.strip()
            if not l: continue
            # Clean member data: remove @, t.me links, etc.
            l = l.replace('https://t.me/', '').replace('http://t.me/', '').replace('t.me/', '').replace('@', '')
            # If line has spaces (e.g. username #comment), take the first part
            l = l.split()[0].strip()
            if l:
                lines.append(l)
        
        if not lines:
            if os.path.exists(file_path): os.remove(file_path)
            return await message.answer("<blockquote>◉╮ ❌ خطأ في الملف\n◉╯ الملف فارغ أو لا يحتوي على يوزرات صالحة.</blockquote>", parse_mode="HTML")
            
        members_data = json.dumps(lines)
        os.remove(file_path)
    except Exception as e:
        if os.path.exists(file_path): os.remove(file_path)
        from DivoSource.logger import logger
        logger.error(f"Error reading transfer file: {e}")
        return await message.answer(f"<blockquote>◉╮ ❌ خطأ في القراءة\n◉╯ {e}</blockquote>", parse_mode="HTML")
        
    await state.update_data(members_data=members_data)
    await message.answer(f"<blockquote>◉╮ ✅ تم قراءة الملف\n◉╯ تم قراءة {len(lines)} يوزر.\n\nالآن أرسل رابط المجموعة الهدف:</blockquote>", parse_mode="HTML")
    await state.set_state(TransferState.waiting_for_target)

@router.message(TransferState.waiting_for_source)
async def process_transfer_source(message: types.Message, state: FSMContext):
    await state.update_data(source_link=message.text.strip())
    await message.answer("<blockquote>◉╮ 🎯 المجموعة الهدف\n◉╯ الآن أرسل رابط المجموعة الهدف (التي سننقلهم إليها):</blockquote>", parse_mode="HTML")
    await state.set_state(TransferState.waiting_for_target)

@router.message(TransferState.waiting_for_target)
async def process_transfer_target(message: types.Message, state: FSMContext):
    await state.update_data(target_link=message.text.strip())
    await message.answer("<blockquote>◉╮ 🔢 العدد المستهدف\n◉╯ كم عدد المستخدمين المطلوب نقلهم بنجاح؟ (أرسل رقماً):</blockquote>", parse_mode="HTML")
    await state.set_state(TransferState.waiting_for_count)

@router.message(TransferState.waiting_for_count)
async def process_transfer_count(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("<blockquote>◉╮ ❌ خطأ\n◉╯ الرجاء إرسال رقم صحيح.</blockquote>", parse_mode="HTML")
        
    await state.update_data(target_count=int(message.text))
    await message.answer("<blockquote>◉╮ 📊 حصة الحساب\n◉╯ كم عدد الإضافات المطلوبة من كل حساب؟ (مثلاً: 15):</blockquote>", parse_mode="HTML")
    await state.set_state(TransferState.waiting_for_adds_per_acc)

@router.message(TransferState.waiting_for_adds_per_acc)
async def process_transfer_adds_per_acc(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("<blockquote>◉╮ ❌ خطأ\n◉╯ الرجاء إرسال رقم صحيح.</blockquote>", parse_mode="HTML")
        
    adds_per_acc = int(message.text)
    data = await state.get_data()
    
    ttype = data.get('transfer_type')
    source = data.get('source_link', 'file')
    target = data.get('target_link')
    m_data = data.get('members_data')
    target_count = data.get('target_count')
    
    # التحقق من وجود البيانات لو كان النوع "ملف"
    if ttype == 'file' and not m_data:
         return await message.answer("<blockquote>◉╮ ❌ خطأ\n◉╯ لم يتم العثور على بيانات الملف في الذاكرة. يرجى إعادة الرفع.</blockquote>", parse_mode="HTML")
         
    from DivoSource.logger import logger
    logger.info(f"Starting transfer: type={ttype}, source={source}, target={target}, target_count={target_count}, adds_per_acc={adds_per_acc}")
    
    transfer_id = await create_transfer(ttype, source, target, target_count, members_data=m_data, adds_per_account=adds_per_acc)
    
    msg = f"<blockquote>◉╮ 🚀 بدأت عملية النقل!\n"
    msg += f"◉᚜┃ المعرف: {transfer_id}\n"
    msg += f"◉᚜┃ النوع: {ttype}\n"
    msg += f"◉᚜┃ المستهدف الكلي: {target_count}\n"
    msg += f"◉᚜┃ حصة الحساب: {adds_per_acc}\n"
    msg += f"◉╯ ⏳ جاري تجهيز الحسابات...</blockquote>"
    
    sent_msg = await message.answer(msg, parse_mode="HTML")
    await state.clear()
    
    from DivoSource.transfer_tasks import run_transfer_job
    import asyncio
    asyncio.create_task(run_transfer_job(transfer_id, message.chat.id, sent_msg.message_id, message.bot))

@router.callback_query(F.data.startswith("pause_transfer_"))
async def pause_transfer_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض")
    tid = int(call.data.split("_")[2])
    await update_transfer_status(tid, "paused")
    await call.answer("تم إرسال أمر الإيقاف المؤقت، سيتم الحفظ فوراً...", show_alert=True)

@router.callback_query(F.data.startswith("resume_transfer_"))
async def resume_transfer_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض")
    tid = int(call.data.split("_")[2])
    await update_transfer_status(tid, "running")
    await call.answer("تم الاستئناف، جاري إكمال النقل...", show_alert=True)
    from DivoSource.transfer_tasks import run_transfer_job
    import asyncio
    asyncio.create_task(run_transfer_job(tid, call.message.chat.id, call.message.message_id, call.bot))

@router.callback_query(F.data.startswith("stop_transfer_"))
async def stop_transfer_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض")
    tid = int(call.data.split("_")[2])
    await update_transfer_status(tid, "stopped")
    await call.answer("تم إيقاف وإلغاء النقل نهائياً.", show_alert=True)

# --- Fetch To File Feature Handlers ---

@router.callback_query(F.data == "fetch_menu")
async def fetch_menu_handler(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض")
    btns = [
        [InlineKeyboardButton(text="👥 جلب من جروب ظاهر", callback_data="fetch_type_public")],
        [InlineKeyboardButton(text="🟢 جلب أونلاين", callback_data="fetch_type_online")],
        [InlineKeyboardButton(text="🕵️‍♂️ جلب من جروب مخفي", callback_data="fetch_type_hidden")],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="sec_transfer")]
    ]
    markup = InlineKeyboardMarkup(inline_keyboard=btns)
    await call.message.edit_text("<blockquote>◉╮ 📥 جلب الأعضاء لملف\n◉╯ اختر نوع الجلب الذي تريده:</blockquote>", reply_markup=markup, parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("fetch_type_"))
async def start_fetch_flow(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID: return await call.answer("مرفوض")
    ftype = call.data.split("_")[2]
    await state.update_data(fetch_type=ftype)
    
    await call.message.edit_text("<blockquote>◉╮ 🔗 رابط المصدر\n◉╯ أرسل الآن رابط المجموعة المصدر (التي سيتم سحب الأعضاء منها):</blockquote>", parse_mode="HTML")
    await state.set_state(FetchToFileState.waiting_for_source)
    await call.answer()

@router.message(FetchToFileState.waiting_for_source)
async def process_fetch_source(message: types.Message, state: FSMContext):
    source_link = message.text.strip()
    await state.update_data(source_link=source_link)
    await message.answer("<blockquote>◉╮ 🔢 العدد المطلوب\n◉╯ كم عدد المستخدمين المطلوب جلبهم إلى الملف؟ (أرسل رقماً أو 'الكل'):</blockquote>", parse_mode="HTML")
    await state.set_state(FetchToFileState.waiting_for_count)

@router.message(FetchToFileState.waiting_for_count)
async def process_fetch_count(message: types.Message, state: FSMContext):
    count_str = message.text.strip()
    target_count = 0
    if count_str.isdigit():
        target_count = int(count_str)
    elif count_str != 'الكل':
        return await message.answer("<blockquote>◉╮ ❌ خطأ\n◉╯ الرجاء إرسال رقم صحيح أو 'الكل'.</blockquote>", parse_mode="HTML")
        
    data = await state.get_data()
    ftype = data.get('fetch_type')
    source_link = data.get('source_link')
    
    msg = f"<blockquote>◉╮ 🚀 بدأت عملية الجلب!\n"
    msg += f"◉᚜┃ النوع: {ftype}\n"
    msg += f"◉᚜┃ المصدر: {source_link}\n"
    msg += f"◉╯ العدد: {'الكل' if target_count == 0 else target_count}</blockquote>"
    
    sent_msg = await message.answer(msg, parse_mode="HTML")
    await state.clear()
    
    from DivoSource.transfer_tasks import run_fetch_to_file_job
    import asyncio
    asyncio.create_task(run_fetch_to_file_job(ftype, source_link, target_count, message.chat.id, sent_msg.message_id, message.bot))

