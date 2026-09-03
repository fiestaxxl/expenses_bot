"""Лимиты трат по категориям на месяц: /limits и кнопка «💰 Лимиты».

Механика конвертов: на месяц задаются лимиты по категориям и «Резерв».
Каждая трата уменьшает остаток своей категории; перерасход категорий
съедает резерв. После добавления траты бот сразу пишет, сколько осталось.

Спец-строка «Резерв» — не категория: из неё «добираются» пробитые лимиты.
"""

import html
from datetime import date

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import config
from database import Database

router = Router()
router.message.filter(F.from_user.id == config.OWNER_ID)

RESERVE = "Резерв"

MONTH_NAMES = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}


class BudgetFlow(StatesGroup):
    editing = State()        # экран настройки лимитов
    waiting_amount = State() # ждём сумму лимита


# ── Чистые хелперы (используются и из add.py / import_statement.py) ──────────

def _rub(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ")


def bar(spent: float, limit: float, width: int = 8) -> str:
    if limit <= 0:
        return "▱" * width
    filled = min(width, round(width * min(spent, limit) / limit))
    return "▰" * filled + "▱" * (width - filled)


def status_line(category: str, spent: float, limit: float) -> str:
    """Одна строка статуса лимита категории."""
    left = limit - spent
    if left < 0:
        return (f"🔴 {html.escape(category)}: лимит {_rub(limit)} ₽ "
                f"превышен на <b>{_rub(-left)} ₽</b>")
    icon = "🟡" if limit > 0 and left <= limit * 0.25 else "🟢"
    return (f"{icon} {html.escape(category)}: осталось <b>{_rub(left)} ₽</b> "
            f"из {_rub(limit)} ₽")


def reserve_left(budgets: dict[str, float], spent_map: dict[str, float]) -> float | None:
    """Остаток резерва: резерв минус суммарный перерасход категорий."""
    if RESERVE not in budgets:
        return None
    overrun = sum(
        max(0.0, spent_map.get(cat, 0) - limit)
        for cat, limit in budgets.items() if cat != RESERVE
    )
    return budgets[RESERVE] - overrun


async def budget_note(db: Database, category: str, month: int, year: int) -> str:
    """Строка «сколько осталось» после добавления траты. Пустая, если лимита нет."""
    budgets = await db.get_budgets(month, year)
    if category not in budgets:
        return ""
    spent = await db.get_spent(category, month, year)
    line = status_line(category, spent, budgets[category])
    if spent > budgets[category]:
        spent_map = dict(await db.get_monthly_by_category(month, year))
        reserve = reserve_left(budgets, spent_map)
        if reserve is not None:
            line += (f"\n🛟 Резерв: осталось {_rub(reserve)} ₽"
                     if reserve >= 0 else
                     f"\n🆘 Резерв исчерпан (минус {_rub(-reserve)} ₽)")
    return line


async def budget_summary_for(db: Database, categories: set[str],
                             month: int, year: int) -> str:
    """Компактная сводка лимитов по затронутым категориям (после импорта)."""
    budgets = await db.get_budgets(month, year)
    touched = [c for c in budgets if c in categories]
    if not touched:
        return ""
    spent_map = dict(await db.get_monthly_by_category(month, year))
    lines = [status_line(c, spent_map.get(c, 0), budgets[c]) for c in touched]
    reserve = reserve_left(budgets, spent_map)
    if reserve is not None:
        lines.append(f"🛟 Резерв: {_rub(reserve)} ₽")
    return "\n".join(lines)


# ── Экран /limits ────────────────────────────────────────────────────────────

async def _limits_view(db: Database, month: int, year: int):
    budgets = await db.get_budgets(month, year)
    spent_map = dict(await db.get_monthly_by_category(month, year))

    header = f"<b>💰 Лимиты — {MONTH_NAMES[month]} {year}</b>"
    buttons = []
    if not budgets:
        prev_m, prev_y = (month - 1, year) if month > 1 else (12, year - 1)
        text = header + "\n\nЛимиты на этот месяц не заданы."
        if await db.get_budgets(prev_m, prev_y):
            buttons.append([InlineKeyboardButton(
                text=f"📥 Перенести с {MONTH_NAMES[prev_m].lower()}",
                callback_data="bud:copy")])
    else:
        cats = {c: v for c, v in budgets.items() if c != RESERVE}
        lines = []
        total_limit = total_spent = 0.0
        for cat, limit in sorted(cats.items(), key=lambda x: -x[1]):
            spent = spent_map.get(cat, 0)
            total_limit += limit
            total_spent += spent
            left = limit - spent
            mark = "🔴" if left < 0 else ("🟡" if limit > 0 and left <= limit * 0.25 else "🟢")
            left_str = f"−{_rub(-left)}" if left < 0 else _rub(left)
            lines.append(
                f"{mark} <b>{html.escape(cat)}</b>\n"
                f"    {bar(spent, limit)}  {_rub(spent)} / {_rub(limit)} · "
                f"осталось {left_str} ₽"
            )
        text = header + "\n\n" + "\n".join(lines)
        text += (f"\n\n<b>Итого:</b> {_rub(total_spent)} / {_rub(total_limit)} ₽ · "
                 f"осталось {_rub(total_limit - total_spent)} ₽")
        reserve = reserve_left(budgets, spent_map)
        if reserve is not None:
            icon = "🛟" if reserve >= 0 else "🆘"
            text += (f"\n{icon} Резерв: {_rub(reserve)} из {_rub(budgets[RESERVE])} ₽")

    buttons.append([InlineKeyboardButton(text="✏️ Настроить лимиты", callback_data="bud:edit")])
    buttons.append([InlineKeyboardButton(text="✖️ Закрыть", callback_data="bud:close")])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("limits"))
@router.message(F.text == "💰 Лимиты")
async def cmd_limits(message: Message, state: FSMContext, db: Database):
    await state.clear()
    today = config.today()
    text, kb = await _limits_view(db, today.month, today.year)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


# ── Настройка лимитов ────────────────────────────────────────────────────────

async def _edit_view(db: Database, state: FSMContext, month: int, year: int):
    budgets = await db.get_budgets(month, year)
    categories = await db.get_categories()
    items = categories + [RESERVE]
    await state.set_state(BudgetFlow.editing)
    await state.update_data(bud_items=items)

    rows = []
    for i, cat in enumerate(items):
        current = f"{_rub(budgets[cat])} ₽" if cat in budgets else "—"
        icon = "🛟 " if cat == RESERVE else ""
        rows.append([InlineKeyboardButton(text=f"{icon}{cat}: {current}",
                                          callback_data=f"bud:set:{i}")])
    rows.append([InlineKeyboardButton(text="⬅️ К лимитам", callback_data="bud:back")])
    text = (f"<b>Настройка лимитов — {MONTH_NAMES[month]} {year}</b>\n\n"
            f"Нажми на категорию и пришли сумму.\n"
            f"«Резерв» покрывает перерасход остальных категорий.")
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("bud:"))
async def budget_callback(callback: CallbackQuery, state: FSMContext, db: Database):
    today = config.today()
    action = callback.data.split(":")[1]

    if action == "close":
        await state.clear()
        await callback.message.edit_text("Ок. Лимиты всегда в /limits 👌")

    elif action == "copy":
        prev_m, prev_y = (today.month - 1, today.year) if today.month > 1 else (12, today.year - 1)
        n = await db.copy_budgets(prev_m, prev_y, today.month, today.year)
        text, kb = await _limits_view(db, today.month, today.year)
        await callback.message.edit_text(f"📥 Перенесено лимитов: {n}\n\n" + text,
                                         parse_mode="HTML", reply_markup=kb)

    elif action == "edit":
        text, kb = await _edit_view(db, state, today.month, today.year)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

    elif action == "back":
        await state.clear()
        text, kb = await _limits_view(db, today.month, today.year)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

    elif action == "set":
        data = await state.get_data()
        items = data.get("bud_items")
        if not items:  # состояние потерялось — вернёмся к списку
            text, kb = await _edit_view(db, state, today.month, today.year)
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            await callback.answer()
            return
        cat = items[int(callback.data.split(":")[2])]
        budgets = await db.get_budgets(today.month, today.year)
        current = f"{_rub(budgets[cat])} ₽" if cat in budgets else "не задан"
        await state.set_state(BudgetFlow.waiting_amount)
        await state.update_data(bud_category=cat)
        await callback.message.answer(
            f"<b>{html.escape(cat)}</b> — лимит на {MONTH_NAMES[today.month].lower()}: "
            f"{current}\n\nПришли новую сумму (например <code>8000</code>).\n"
            f"<code>0</code> — жёсткий ноль («не тратим»), "
            f"<code>-</code> — убрать лимит.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✖️ Отмена", callback_data="bud:edit")
            ]]),
        )
    await callback.answer()


@router.message(BudgetFlow.waiting_amount, F.text, ~F.text.startswith("/"),
                ~F.text.in_({"➕ Добавить трату", "📋 Траты", "📊 Отчёты",
                             "⚙️ Категории", "💰 Лимиты"}))
async def budget_amount(message: Message, state: FSMContext, db: Database):
    data = await state.get_data()
    cat = data["bud_category"]
    today = config.today()
    text = message.text.strip()

    if text == "-":
        await db.delete_budget(cat, today.month, today.year)
        note = f"Лимит «{html.escape(cat)}» убран."
    else:
        try:
            amount = float(text.replace(",", ".").replace(" ", ""))
            if amount < 0:
                raise ValueError
        except ValueError:
            await message.answer("Нужно число (0 или больше), либо «-», чтобы убрать лимит.")
            return
        await db.set_budget(cat, today.month, today.year, amount)
        note = f"✅ {html.escape(cat)}: лимит {_rub(amount)} ₽"

    view_text, kb = await _edit_view(db, state, today.month, today.year)
    await message.answer(note + "\n\n" + view_text, parse_mode="HTML", reply_markup=kb)
