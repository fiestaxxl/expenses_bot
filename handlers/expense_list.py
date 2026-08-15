"""Просмотр трат за период с правкой на месте: /list.

Период — кнопками (этот месяц / прошлый / 7 дней) или текстом:
«01.08 15.08», «01.08.2026 15.08.2026», «01.08» (по сегодня).

Список постраничный, под ним кнопки-номера: тап по номеру открывает карточку
траты, в карточке кнопками правятся категория/сумма/дата/комментарий,
там же удаление. Изменения применяются к базе сразу.
Текстовые команды («3 Такси», «3 удалить») тоже работают.
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

PFX = "lst"


class ListFlow(StatesGroup):
    waiting_period = State()
    browsing = State()
    waiting_field = State()   # ждём текст для суммы/даты/комментария


_PERIOD_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Этот месяц", callback_data="lstp:this"),
     InlineKeyboardButton(text="Прошлый месяц", callback_data="lstp:prev")],
    [InlineKeyboardButton(text="Последние 7 дней", callback_data="lstp:7d")],
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


@router.callback_query(ListFlow.waiting_period, F.data.startswith("lstp:"))
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
    await _load_period(callback.message, state, db, d1, d2)


_DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?$")


def _parse_period(text: str, today: date) -> tuple[date, date] | None:
    parts = [p for p in re.split(r"[\s\-–—]+", text.strip()) if p]
    if not 1 <= len(parts) <= 2:
        return None
    dates = []
    for p in parts:
        m = _DATE.match(p)
        if not m:
            return None
        year = int(m.group(3)) if m.group(3) else today.year
        try:
            dates.append(date(year, int(m.group(2)), int(m.group(1))))
        except ValueError:
            return None
    d1, d2 = dates[0], dates[1] if len(dates) == 2 else today
    return (d1, d2) if d1 <= d2 else (d2, d1)


@router.message(ListFlow.waiting_period, F.text)
async def period_text(message: Message, state: FSMContext, db: Database):
    period = _parse_period(message.text, config.today())
    if not period:
        await message.answer("Не понял период. Пример: <code>01.08 15.08</code>",
                             parse_mode="HTML")
        return
    await _load_period(message, state, db, *period)


async def _load_period(message: Message, state: FSMContext, db: Database,
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
    await state.update_data(entries=entries, page=0, header=header)
    await _send_page(message, state, new_message=True)


def _header(data) -> str:
    entries = data["entries"]
    total = f"{sum(e['amount'] for e in entries):,.2f}".replace(",", " ")
    return f"<b>{data['header']}</b> — {len(entries)} шт., {total} ₽"


async def _send_page(message: Message, state: FSMContext, new_message: bool = False):
    data = await state.get_data()
    text, kb = editing.page_view(data["entries"], data.get("page", 0), PFX,
                                 header=_header(data))
    if new_message:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)
    else:
        await message.edit_text(text, parse_mode="HTML", reply_markup=kb)


# ── Кнопки списка/карточки ───────────────────────────────────────────────────

@router.callback_query(ListFlow.browsing, F.data.startswith(f"{PFX}:"))
async def browse_callback(callback: CallbackQuery, state: FSMContext, db: Database):
    parts = callback.data.split(":")
    action = parts[1]
    data = await state.get_data()
    entries = data["entries"]

    async def refresh_page(page: int):
        await state.update_data(page=page)
        text, kb = editing.page_view(entries, page, PFX, header=_header(await state.get_data()))
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

    if action == "page":
        await refresh_page(int(parts[2]))

    elif action == "open":
        idx = int(parts[2])
        if idx >= len(entries):
            await refresh_page(0)
        else:
            text, kb = editing.card_view(entries, idx, PFX)
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

    elif action == "f":
        field, idx = parts[2], int(parts[3])
        if field == "category":
            categories = await db.get_categories()
            await state.update_data(categories=categories)
            await callback.message.edit_text(
                f"Трата {idx + 1}: выбери категорию",
                reply_markup=editing.category_kb(idx, categories, PFX),
            )
        else:
            await state.set_state(ListFlow.waiting_field)
            await state.update_data(edit_idx=idx, edit_field=field)
            await callback.message.answer(editing.FIELD_PROMPTS[field], parse_mode="HTML")

    elif action == "sc":
        idx, ci = int(parts[2]), int(parts[3])
        category = data["categories"][ci]
        entries[idx]["category"] = category
        await db.update_expense(entries[idx]["id"], category=category)
        await state.update_data(entries=entries)
        text, kb = editing.card_view(entries, idx, PFX)
        await callback.message.edit_text("✅ Категория обновлена\n\n" + text,
                                         parse_mode="HTML", reply_markup=kb)

    elif action == "del":
        idx = int(parts[2])
        await callback.message.edit_reply_markup(
            reply_markup=editing.confirm_del_kb(idx, PFX))

    elif action == "del2":
        idx = int(parts[2])
        await db.delete_expense(entries[idx]["id"])
        entries.pop(idx)
        await state.update_data(entries=entries)
        if entries:
            await refresh_page(min(idx, len(entries) - 1) // editing.PAGE_SIZE)
        else:
            await callback.message.edit_text("Всё удалено, список пуст.")
            await state.clear()

    await callback.answer()


@router.message(ListFlow.waiting_field, F.text)
async def field_input(message: Message, state: FSMContext, db: Database):
    data = await state.get_data()
    idx, field = data["edit_idx"], data["edit_field"]
    entries = data["entries"]
    e = entries[idx]

    value = editing.parse_field_value(field, message.text, e["year"])
    if isinstance(value, str) and field != "comment":
        await message.answer(value)
        return

    if field == "amount":
        e["amount"] = value
        await db.update_expense(e["id"], amount=value)
    elif field == "date":
        e["day"], e["month"], e["year"] = value
        await db.update_expense(e["id"], day=value[0], month=value[1], year=value[2])
    else:
        e["comment"] = value or None
        await db.update_expense(e["id"], comment=value)

    await state.set_state(ListFlow.browsing)
    await state.update_data(entries=entries)
    text, kb = editing.card_view(entries, idx, PFX)
    await message.answer("✅ Обновил\n\n" + text, parse_mode="HTML", reply_markup=kb)


# ── Текстовые команды и выход ────────────────────────────────────────────────

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
        note = f"🗑 Строка {cmd.index} удалена. Нумерация сдвинулась!"
    elif cmd.action == "category":
        e["category"] = cmd.value
        await db.update_expense(e["id"], category=cmd.value)
        note = "✅ " + editing.format_row(cmd.index, e)
    elif cmd.action == "amount":
        e["amount"] = cmd.value
        await db.update_expense(e["id"], amount=cmd.value)
        note = "✅ " + editing.format_row(cmd.index, e)
    else:
        e["comment"] = cmd.value or None
        await db.update_expense(e["id"], comment=cmd.value)
        note = "✅ " + editing.format_row(cmd.index, e)

    await state.update_data(entries=entries)
    await message.answer(note, parse_mode="HTML")
