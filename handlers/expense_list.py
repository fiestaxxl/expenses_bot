"""Просмотр трат за период с правкой на месте: /list.

Период — кнопками (этот месяц / прошлый / 7 дней) или текстом:
«01.08 15.08», «01.08.2026 15.08.2026», «01.08» (по сегодня).
Правка — теми же командами, что и в превью импорта (handlers/editing.py),
изменения применяются к базе сразу.
"""

import re
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import config
from database import Database
from handlers import editing

router = Router()
router.message.filter(F.from_user.id == config.OWNER_ID)


class ListFlow(StatesGroup):
    waiting_period = State()
    browsing = State()


_PERIOD_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Этот месяц", callback_data="lst:this"),
     InlineKeyboardButton(text="Прошлый месяц", callback_data="lst:prev")],
    [InlineKeyboardButton(text="Последние 7 дней", callback_data="lst:7d")],
])


@router.message(Command("list"))
@router.message(F.text == "📋 Траты")
async def cmd_list(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(ListFlow.waiting_period)
    await message.answer(
        "За какой период показать траты?\n"
        "Кнопкой или текстом: <code>01.08 15.08</code>",
        parse_mode="HTML",
        reply_markup=_PERIOD_KB,
    )


@router.callback_query(ListFlow.waiting_period, F.data.startswith("lst:"))
async def period_button(callback: CallbackQuery, state: FSMContext, db: Database):
    today = config.today()
    kind = callback.data.split(":")[1]
    if kind == "this":
        d1, d2 = today.replace(day=1), today
    elif kind == "prev":
        last_prev = today.replace(day=1) - timedelta(days=1)
        d1, d2 = last_prev.replace(day=1), last_prev
    else:
        d1, d2 = today - timedelta(days=6), today
    await callback.answer()
    await _show_period(callback.message, state, db, d1, d2)


_DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?$")


def _parse_period(text: str, today: date) -> tuple[date, date] | None:
    parts = re.split(r"[\s\-–—]+", text.strip())
    parts = [p for p in parts if p]
    if not 1 <= len(parts) <= 2:
        return None
    dates = []
    for p in parts:
        m = _DATE.match(p)
        if not m:
            return None
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        try:
            dates.append(date(year, month, day))
        except ValueError:
            return None
    d1 = dates[0]
    d2 = dates[1] if len(dates) == 2 else today
    return (d1, d2) if d1 <= d2 else (d2, d1)


@router.message(ListFlow.waiting_period, F.text)
async def period_text(message: Message, state: FSMContext, db: Database):
    period = _parse_period(message.text, config.today())
    if not period:
        await message.answer("Не понял период. Пример: <code>01.08 15.08</code>",
                             parse_mode="HTML")
        return
    await _show_period(message, state, db, *period)


async def _show_period(message: Message, state: FSMContext, db: Database,
                       d1: date, d2: date):
    rows = await db.get_expenses_between(d1, d2)
    header = f"Траты {d1.strftime('%d.%m.%Y')} — {d2.strftime('%d.%m.%Y')}"
    if not rows:
        await state.clear()
        await message.answer(f"{header}: пусто.")
        return

    entries = [
        {"id": r[0], "amount": r[1], "category": r[2],
         "day": r[3], "month": r[4], "year": r[5], "comment": r[6]}
        for r in rows
    ]
    await state.set_state(ListFlow.browsing)
    await state.update_data(entries=entries, d1=d1.isoformat(), d2=d2.isoformat())

    total = f"{sum(e['amount'] for e in entries):,.2f}".replace(",", " ")
    chunks = editing.render_list(entries)
    await message.answer(f"<b>{header}</b> — {len(entries)} шт., {total} ₽",
                         parse_mode="HTML")
    for chunk in chunks:
        await message.answer(chunk, parse_mode="HTML")
    await message.answer(editing.HELP + "\nЗакончить: /done", parse_mode="HTML")


@router.message(ListFlow.browsing, Command("done"))
async def browsing_done(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Ок, закончили с правками.")


@router.message(ListFlow.browsing, F.text)
async def browsing_edit(message: Message, state: FSMContext, db: Database):
    categories = await db.get_categories()
    cmd = editing.parse_edit(message.text, categories)
    if cmd is None:
        await message.answer(editing.HELP + "\nЗакончить: /done", parse_mode="HTML")
        return
    if isinstance(cmd, str):
        await message.answer(cmd)
        return

    data = await state.get_data()
    entries = data["entries"]
    if not 1 <= cmd.index <= len(entries):
        await message.answer(f"Нет строки {cmd.index} (всего {len(entries)}).")
        return
    e = entries[cmd.index - 1]

    if cmd.action == "delete":
        await db.delete_expense(e["id"])
        entries.pop(cmd.index - 1)
        note = f"Строка {cmd.index} удалена из базы. Нумерация сдвинулась!"
    elif cmd.action == "category":
        e["category"] = cmd.value
        await db.update_expense(e["id"], category=cmd.value)
        note = "✏️ " + editing.format_row(cmd.index, e)
    elif cmd.action == "amount":
        e["amount"] = cmd.value
        await db.update_expense(e["id"], amount=cmd.value)
        note = "✏️ " + editing.format_row(cmd.index, e)
    else:
        e["comment"] = cmd.value or None
        await db.update_expense(e["id"], comment=cmd.value)
        note = "✏️ " + editing.format_row(cmd.index, e)

    await state.update_data(entries=entries)
    await message.answer(note, parse_mode="HTML")
