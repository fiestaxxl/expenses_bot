from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
from database import Database
from config import config

router = Router()
router.message.filter(F.from_user.id == config.OWNER_ID)


class CategoryStates(StatesGroup):
    waiting_new_name = State()
    waiting_rename = State()


@router.message(Command("categories"))
@router.message(F.text == "⚙️ Категории")
async def cmd_categories(message: Message, state: FSMContext, db: Database):
    await state.clear()
    await show_categories(message, db)


async def show_categories(message: Message, db: Database):
    cats = await db.get_categories()
    if not cats:
        await message.answer("Категорий нет. Добавь через кнопку ниже.")
        return

    buttons = [
        [
            InlineKeyboardButton(text=f"✏️ {cat}", callback_data=f"rencat:{cat}"),
            InlineKeyboardButton(text="🗑", callback_data=f"delcat:{cat}"),
        ]
        for cat in cats
    ]
    buttons.append([InlineKeyboardButton(text="➕ Добавить категорию", callback_data="addcat")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(
        f"<b>Категории ({len(cats)}):</b>\n" + "\n".join(f"• {c}" for c in cats) +
        "\n\n✏️ — переименовать,  🗑 — удалить",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


@router.callback_query(F.data == "addcat")
async def cb_add_category(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Введи название новой категории:")
    await state.set_state(CategoryStates.waiting_new_name)
    await callback.answer()


@router.message(CategoryStates.waiting_new_name)
async def process_new_category(message: Message, state: FSMContext, db: Database):
    name = message.text.strip()
    if not name:
        await message.answer("Название не может быть пустым.")
        return
    added = await db.add_category(name)
    await state.clear()
    if added:
        await message.answer(f"✅ Категория <b>{name}</b> добавлена.", parse_mode="HTML")
    else:
        await message.answer(f"Категория <b>{name}</b> уже существует.", parse_mode="HTML")
    await show_categories(message, db)


@router.callback_query(F.data.startswith("rencat:"))
async def cb_rename_category(callback: CallbackQuery, state: FSMContext):
    name = callback.data.split(":", 1)[1]
    await state.update_data(rename_from=name)
    await state.set_state(CategoryStates.waiting_rename)
    await callback.message.answer(f"Введи новое название для категории <b>{name}</b>:", parse_mode="HTML")
    await callback.answer()


@router.message(CategoryStates.waiting_rename)
async def process_rename(message: Message, state: FSMContext, db: Database):
    new_name = message.text.strip()
    if not new_name:
        await message.answer("Название не может быть пустым.")
        return
    data = await state.get_data()
    old_name = data["rename_from"]
    ok = await db.rename_category(old_name, new_name)
    await state.clear()
    if ok:
        await message.answer(f"✅ Категория переименована: <b>{old_name}</b> → <b>{new_name}</b>", parse_mode="HTML")
    else:
        await message.answer(f"Категория <b>{new_name}</b> уже существует.", parse_mode="HTML")
    await show_categories(message, db)


@router.callback_query(F.data.startswith("delcat:"))
async def cb_delete_category(callback: CallbackQuery, db: Database):
    name = callback.data.split(":", 1)[1]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirmdelcat:{name}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="canceldelcat"),
        ]
    ])
    await callback.message.answer(
        f"Удалить категорию <b>{name}</b>?\n"
        f"(Существующие траты этой категории останутся)",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirmdelcat:"))
async def cb_confirm_delete(callback: CallbackQuery, db: Database):
    name = callback.data.split(":", 1)[1]
    deleted = await db.delete_category(name)
    if deleted:
        await callback.message.edit_text(f"✅ Категория <b>{name}</b> удалена.", parse_mode="HTML")
    else:
        await callback.message.edit_text("Категория не найдена.")
    await callback.answer()


@router.callback_query(F.data == "canceldelcat")
async def cb_cancel_delete(callback: CallbackQuery):
    await callback.message.edit_text("Отменено.")
    await callback.answer()
