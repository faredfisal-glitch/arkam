import asyncio
from aiogram import types, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import OWNER_ID
from DivoSource.fsm import YastahaqqVoteState, NormalVoteState
from DivoSource.database import create_vote, update_vote_status, get_vote
from DivoSource.voting_tasks import run_vote_task, generate_vote_dashboard
from DivoSource.bot_client import bot

router = Router()

def get_voting_keyboard():
    btns = [
        [InlineKeyboardButton(text="💬 تصويت 'يستحق'", callback_data="vote_yastahaqq")],
        [InlineKeyboardButton(text="🔘 تصويت عادي (أزرار شفافة)", callback_data="vote_normal")],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=btns)

@router.callback_query(F.data == "sec_voting")
async def sec_voting_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return await call.answer("مرفوض", show_alert=True)
    await call.message.edit_text("<blockquote>◉╮ 🗳 قسم التصويت\n◉╯ اختر نوع التصويت المطلوب:</blockquote>", reply_markup=get_voting_keyboard(), parse_mode="HTML")
    await call.answer()

# --- Yastahaqq Vote ---
@router.callback_query(F.data == "vote_yastahaqq")
async def vote_yastahaqq_handler(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return await call.answer("مرفوض", show_alert=True)
    await call.message.answer("<blockquote>◉╮ 🔗 الرابط المستهدف\n◉╯ أرسل رابط الرسالة المستهدفة:</blockquote>", parse_mode="HTML")
    await state.set_state(YastahaqqVoteState.waiting_for_link)
    await call.answer()

@router.message(YastahaqqVoteState.waiting_for_link)
async def yastahaqq_process_link(message: types.Message, state: FSMContext):
    link = message.text.strip()
    await state.update_data(link=link)
    await message.answer("<blockquote>◉╮ 🔢 العدد المطلوب\n◉╯ أرسل عدد التصويتات المطلوبة (مثال: 50):</blockquote>", parse_mode="HTML")
    await state.set_state(YastahaqqVoteState.waiting_for_count)

@router.message(YastahaqqVoteState.waiting_for_count)
async def yastahaqq_process_count(message: types.Message, state: FSMContext):
    try:
        count = int(message.text.strip())
        if count <= 0: raise ValueError
    except ValueError:
        return await message.answer("<blockquote>◉╮ ❌ خطأ\n◉╯ الرجاء إدخال عدد صحيح أكبر من صفر.</blockquote>", parse_mode="HTML")
    
    data = await state.get_data()
    link = data['link']
    
    vote_id = await create_vote("yastahaqq", link, count)
    
    txt, mark = generate_vote_dashboard(vote_id, "yastahaqq", 0, 0, count, "running")
    msg = await message.answer(txt, reply_markup=mark, parse_mode="HTML")
    
    asyncio.create_task(run_vote_task(vote_id, message.chat.id, msg.message_id, bot))
    await state.clear()

# --- Normal Vote ---
@router.callback_query(F.data == "vote_normal")
async def vote_normal_handler(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return await call.answer("مرفوض", show_alert=True)
    await call.message.answer("<blockquote>◉╮ 🔗 الرابط المستهدف\n◉╯ أرسل رابط الرسالة المستهدفة:</blockquote>", parse_mode="HTML")
    await state.set_state(NormalVoteState.waiting_for_link)
    await call.answer()

@router.message(NormalVoteState.waiting_for_link)
async def normal_process_link(message: types.Message, state: FSMContext):
    link = message.text.strip()
    await state.update_data(link=link)
    await message.answer("<blockquote>◉╮ 🔢 العدد المطلوب\n◉╯ أرسل عدد التصويتات المطلوبة (مثال: 50):</blockquote>", parse_mode="HTML")
    await state.set_state(NormalVoteState.waiting_for_count)

@router.message(NormalVoteState.waiting_for_count)
async def normal_process_count(message: types.Message, state: FSMContext):
    try:
        count = int(message.text.strip())
        if count <= 0: raise ValueError
    except ValueError:
        return await message.answer("<blockquote>◉╮ ❌ خطأ\n◉╯ الرجاء إدخال عدد صحيح أكبر من صفر.</blockquote>", parse_mode="HTML")
    
    data = await state.get_data()
    link = data['link']
    
    vote_id = await create_vote("normal", link, count)
    
    txt, mark = generate_vote_dashboard(vote_id, "normal", 0, 0, count, "running")
    msg = await message.answer(txt, reply_markup=mark, parse_mode="HTML")
    
    asyncio.create_task(run_vote_task(vote_id, message.chat.id, msg.message_id, bot))
    await state.clear()

# --- Stop Vote ---
@router.callback_query(F.data.startswith("stop_vote_"))
async def stop_vote_handler(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return await call.answer("مرفوض", show_alert=True)
        
    vote_id = int(call.data[len("stop_vote_"):])
    await update_vote_status(vote_id, "cancelled")
    
    # Task will pick it up on the next iteration and finalize the message
    await call.answer("🛑 جاري إيقاف عملية التصويت...", show_alert=True)
