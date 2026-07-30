import asyncio
from aiogram import types, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import OWNER_ID
from DivoSource.fsm import ReactionState
from DivoSource.database import (
    create_reaction, update_reaction_status, get_reaction, get_all_reactions
)
from DivoSource.reaction_tasks import run_reaction_task, generate_reaction_dashboard
from DivoSource.bot_client import bot

router = Router()

def get_reaction_menu_keyboard():
    btns = [
        [InlineKeyboardButton(text="➕ بدء عملية تفاعل جديدة", callback_data="reaction_new")],
        [InlineKeyboardButton(text="📊 عرض العمليات الحالية", callback_data="reaction_list")],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=btns)

def get_emoji_keyboard():
    emojis = ["👍", "❤️", "🔥", "💘", "💕", "🥰", "😢", "😡", "👎", "🎉", "🤯"]
    btns = []
    row = []
    for i, emoji in enumerate(emojis):
        row.append(InlineKeyboardButton(text=emoji, callback_data=f"emoji_select_{emoji}"))
        if len(row) == 4:
            btns.append(row)
            row = []
    if row:
        btns.append(row)
    
    btns.append([InlineKeyboardButton(text="➕ إيموجي مخصص", callback_data="emoji_custom")])
    btns.append([InlineKeyboardButton(text="🔙 إلغاء", callback_data="sec_reaction")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

@router.callback_query(F.data == "sec_reaction")
async def sec_reaction_handler(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return await call.answer("مرفوض", show_alert=True)
    await state.clear()
    await call.message.edit_text("<blockquote>◉╮ 🎭 قسم التفاعل\n◉╯ تحكم في عمليات التفاعل على الرسائل:</blockquote>", reply_markup=get_reaction_menu_keyboard(), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "reaction_new")
async def reaction_new_handler(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return await call.answer("مرفوض", show_alert=True)
    await call.message.answer("<blockquote>◉╮ 🔗 الرابط المستهدف\n◉╯ أرسل رابط الرسالة المستهدفة (قناة أو جروب):</blockquote>", parse_mode="HTML")
    await state.set_state(ReactionState.waiting_for_link)
    await call.answer()

@router.message(ReactionState.waiting_for_link)
async def process_reaction_link(message: types.Message, state: FSMContext):
    link = message.text.strip()
    await state.update_data(link=link)
    await message.answer("<blockquote>◉╮ ✨ نوع التفاعل\n◉╯ اختر إيموجي من القائمة أو أدخل إيموجي مخصص:</blockquote>", reply_markup=get_emoji_keyboard(), parse_mode="HTML")
    await state.set_state(ReactionState.waiting_for_emoji)

@router.callback_query(ReactionState.waiting_for_emoji, F.data.startswith("emoji_select_"))
async def process_emoji_select(call: types.CallbackQuery, state: FSMContext):
    emoji = call.data[len("emoji_select_"):]
    await state.update_data(emoji=emoji)
    await call.message.edit_text(f"<blockquote>◉╮ ✅ تم الاختيار\n◉╯ الإيموجي: {emoji}\n◉╯ أرسل عدد التفاعلات المطلوبة:</blockquote>", parse_mode="HTML")
    await state.set_state(ReactionState.waiting_for_count)
    await call.answer()

@router.callback_query(ReactionState.waiting_for_emoji, F.data == "emoji_custom")
async def process_emoji_custom_req(call: types.CallbackQuery, state: FSMContext):
    await call.message.edit_text("<blockquote>◉╮ ⌨️ إيموجي مخصص\n◉╯ أرسل الإيموجي المخصص الآن:</blockquote>", parse_mode="HTML")
    await call.answer()

@router.message(ReactionState.waiting_for_emoji)
async def process_emoji_custom(message: types.Message, state: FSMContext):
    emoji = message.text.strip()
    # التحقق البسيط من كونه إيموجي (اختياري)
    await state.update_data(emoji=emoji)
    await message.answer(f"<blockquote>◉╮ ✅ تم الاختيار\n◉╯ الإيموجي: {emoji}\n◉╯ أرسل عدد التفاعلات المطلوبة:</blockquote>", parse_mode="HTML")
    await state.set_state(ReactionState.waiting_for_count)

@router.message(ReactionState.waiting_for_count)
async def process_reaction_count(message: types.Message, state: FSMContext):
    try:
        count = int(message.text.strip())
        if count <= 0: raise ValueError
    except ValueError:
        return await message.answer("<blockquote>◉╮ ❌ خطأ\n◉╯ الرجاء إدخال عدد صحيح أكبر من صفر.</blockquote>", parse_mode="HTML")
    
    data = await state.get_data()
    link = data['link']
    emoji = data['emoji']
    
    reaction_id = await create_reaction(link, emoji, count)
    
    txt, mark = generate_reaction_dashboard(reaction_id, emoji, 0, 0, count, "running")
    msg = await message.answer(txt, reply_markup=mark, parse_mode="HTML")
    
    asyncio.create_task(run_reaction_task(reaction_id, message.chat.id, msg.message_id, bot))
    await state.clear()

@router.callback_query(F.data == "reaction_list")
async def reaction_list_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return await call.answer("مرفوض", show_alert=True)
        
    reactions = await get_all_reactions()
    if not reactions:
        return await call.answer("لا توجد عمليات حالية.", show_alert=True)
    
    msg = "<blockquote>◉╮ 📊 آخر عمليات التفاعل:\n"
    btns = []
    for r in reactions:
        rid, link, emoji, target, success, failed, status = r
        stat_icon = "🟢" if status == "running" else "✅" if status == "completed" else "🔴"
        msg += f"◉᚜┃ {stat_icon} عملية #{rid} | {emoji} | {success}/{target}\n"
        if status == "running":
            btns.append([InlineKeyboardButton(text=f"⏹ إيقاف #{rid}", callback_data=f"stop_reaction_{rid}")])
    
    msg += "◉╯ انتهى عرض العمليات</blockquote>"
    btns.append([InlineKeyboardButton(text="🔙 رجوع", callback_data="sec_reaction")])
    await call.message.edit_text(msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("stop_reaction_"))
async def stop_reaction_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return await call.answer("مرفوض", show_alert=True)
        
    reaction_id = int(call.data[len("stop_reaction_"):])
    await update_reaction_status(reaction_id, "cancelled")
    
    await call.answer("🛑 جاري إيقاف عملية التفاعل...", show_alert=True)
    # Task will handle final dashboard update
