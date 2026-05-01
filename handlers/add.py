from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command, StateFilter
from database import Database
from config import config
from keyboards import MAIN_MENU

router = Router()
router.message.filter(F.from_user.id == config.OWNER_ID)


class AddExpense(StatesGroup):
    waiting_amount = State()
    waiting_category = State()
    waiting_day = State()
    waiting_comment = State()


def owner_only(func):
    return func


@router.message(Command("add"))
@router.message(F.text == "➕ Добавить трату")
@router.message(StateFilter(None), F.text.regexp(r"^\d+([.,]\d+)?$"))
async def cmd_add(message: Message, state: FSMContext, db: Database):
    text = message.text.strip()

    # if it's just a number, prefill amount and skip to category
    try:
        amount = float(text.replace(",", "."))
        if amount > 0:
            await state.update_data(amount=amount)
            await _ask_category(message, state, db)
            return
    except ValueError:
        pass

    await message.answer(
        "Введи сумму расхода:\n"
        "<i>Например: 500 или 1990.50</i>",
        parse_mode="HTML",
    )
    await state.set_state(AddExpense.waiting_amount)


@router.message(Command("undo"))
async def cmd_undo(message: Message, db: Database):
    deleted = await db.delete_last_expense()
    if deleted:
        await message.answer("✅ Последняя трата удалена.")
    else:
        await message.answer("Нет трат для удаления.")


@router.message(AddExpense.waiting_amount)
async def process_amount(message: Message, state: FSMContext, db: Database):
    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Введи корректную сумму (например: 500 или 199.90)")
        return

    await state.update_data(amount=amount)
    await _ask_category(message, state, db)


async def _ask_category(message: Message, state: FSMContext, db: Database):
    categories = await db.get_categories()
    buttons = [
        [InlineKeyboardButton(text=cat, callback_data=f"cat:{cat}")]
        for cat in categories
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Выбери категорию:", reply_markup=keyboard)
    await state.set_state(AddExpense.waiting_category)


@router.callback_query(AddExpense.waiting_category, F.data.startswith("cat:"))
async def process_category(callback: CallbackQuery, state: FSMContext):
    category = callback.data.split(":", 1)[1]
    await state.update_data(category=category)
    await callback.message.edit_text(f"Категория: <b>{category}</b>", parse_mode="HTML")

    today = config.today().day
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Сегодня ({today})", callback_data=f"day:{today}")],
        [InlineKeyboardButton(text="Другой день", callback_data="day:custom")],
    ])
    await callback.message.answer("День расхода:", reply_markup=keyboard)
    await state.set_state(AddExpense.waiting_day)
    await callback.answer()


@router.callback_query(AddExpense.waiting_day, F.data.startswith("day:"))
async def process_day_callback(callback: CallbackQuery, state: FSMContext):
    value = callback.data.split(":", 1)[1]
    if value == "custom":
        await callback.message.edit_text("Введи день (число от 1 до 31):")
        await callback.answer()
        return
    day = int(value)
    await state.update_data(day=day)
    await callback.message.edit_text(f"День: <b>{day}</b>", parse_mode="HTML")
    await _ask_comment(callback.message, state)
    await callback.answer()


@router.message(AddExpense.waiting_day)
async def process_day_text(message: Message, state: FSMContext):
    try:
        day = int(message.text.strip())
        if not 1 <= day <= 31:
            raise ValueError
    except ValueError:
        await message.answer("Введи число от 1 до 31:")
        return
    await state.update_data(day=day)
    await _ask_comment(message, state)


async def _ask_comment(message: Message, state: FSMContext):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Без комментария", callback_data="comment:skip")]
    ])
    await message.answer("Комментарий (необязательно):", reply_markup=keyboard)
    await state.set_state(AddExpense.waiting_comment)


@router.callback_query(AddExpense.waiting_comment, F.data == "comment:skip")
async def process_comment_skip(callback: CallbackQuery, state: FSMContext, db: Database):
    await _save_expense(callback.message, state, db, comment=None)
    await callback.answer()


@router.message(AddExpense.waiting_comment)
async def process_comment(message: Message, state: FSMContext, db: Database):
    comment = message.text.strip()
    await _save_expense(message, state, db, comment=comment)


async def _save_expense(message: Message, state: FSMContext, db: Database, comment: str | None):
    data = await state.get_data()
    today = config.today()
    month = today.month
    year = today.year

    await db.add_expense(
        amount=data["amount"],
        category=data["category"],
        day=data["day"],
        month=month,
        year=year,
        comment=comment,
    )
    await state.clear()

    comment_str = f" · {comment}" if comment else ""
    await message.answer(
        f"✅ <b>{data['amount']:,.0f} ₽</b> — {data['category']}"
        f", {data['day']}.{month:02d}.{year}{comment_str}\n\n"
        f"Отправь следующую сумму или используй меню.",
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )
