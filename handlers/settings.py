"""Настройки импорта выписок: /settings.

- свои реквизиты (телефоны, договоры Т-Банка, имя, «свои» банки) —
  всё, что уходит на них, считается самопереводом и не попадает в траты;
- граница импорта (операции по эту дату включительно игнорируются);
- выученные правила мерчантов (посмотреть/удалить).
"""

import html
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import config
from database import Database
from handlers.editing import MENU_TEXTS

router = Router()
router.message.filter(F.from_user.id == config.OWNER_ID)


class SettingsFlow(StatesGroup):
    waiting_value = State()
    manage_rules = State()


SETTING_LABELS = {
    "own_phones": ("📱 Свои телефоны", "последние 10 цифр, через запятую: 9504567890"),
    "own_names": ("👤 Своё имя в переводах", "как пишут банки, через запятую: И. Иван Иванович"),
    "own_banks": ("🏦 «Свои» банки", "подстроки, через запятую: Yandex, Яндекс, Альфа"),
    "own_accounts": ("📄 Свои счета/договоры", "номера счетов или договоров из выписок любых банков, через запятую"),
    "import_after": ("📅 Граница импорта", "дата ГГГГ-ММ-ДД; операции по неё включительно не импортируются"),
}


async def _settings_text(db: Database) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["<b>Настройки импорта выписок</b>\n"]
    buttons = []
    for key, (label, _) in SETTING_LABELS.items():
        value = await db.get_setting(key) or "—"
        lines.append(f"{label}: <code>{html.escape(value)}</code>")
        buttons.append([InlineKeyboardButton(text=f"✏️ {label}", callback_data=f"set:{key}")])
    rules = await db.get_merchant_rules()
    lines.append(f"\n🧠 Выученных правил мерчантов: {len(rules)}")
    buttons.append([InlineKeyboardButton(text="🧠 Правила мерчантов", callback_data="set:rules")])
    buttons.append([InlineKeyboardButton(text="✖️ Закрыть", callback_data="set:close")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("settings"))
async def cmd_settings(message: Message, state: FSMContext, db: Database):
    await state.clear()
    text, kb = await _settings_text(db)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("set:"))
async def setting_click(callback: CallbackQuery, state: FSMContext, db: Database):
    key = callback.data.split(":", 1)[1]
    if key == "close":
        await state.clear()
        await callback.message.edit_text("Настройки закрыты. Открыть снова: /settings")
        await callback.answer()
        return
    if key == "cancelinput":
        await state.clear()
        text, kb = await _settings_text(db)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        await callback.answer()
        return
    if key == "rules":
        await _show_rules(callback.message, state, db)
        await callback.answer()
        return
    label, hint = SETTING_LABELS[key]
    current = await db.get_setting(key) or "—"
    await state.set_state(SettingsFlow.waiting_value)
    await state.update_data(setting_key=key)
    await callback.message.answer(
        f"{label}\nСейчас: <code>{html.escape(current)}</code>\n\n"
        f"Пришли новое значение ({hint}).\nОчистить: <code>-</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✖️ Отмена", callback_data="set:cancelinput")
        ]]),
    )
    await callback.answer()


@router.message(SettingsFlow.waiting_value, Command("cancel"))
async def value_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Ок, не меняю.")


@router.message(SettingsFlow.waiting_value, F.text, ~F.text.in_(MENU_TEXTS),
                ~F.text.startswith("/"))
async def value_set(message: Message, state: FSMContext, db: Database):
    data = await state.get_data()
    key = data["setting_key"]
    value = message.text.strip()
    if value == "-":
        value = ""
    if key == "import_after" and value:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            await message.answer("Формат даты: ГГГГ-ММ-ДД, например 2026-08-14")
            return
    await db.set_setting(key, value)
    await state.clear()
    text, kb = await _settings_text(db)
    await message.answer("Сохранил.\n\n" + text, parse_mode="HTML", reply_markup=kb)


# ── Правила мерчантов ────────────────────────────────────────────────────────

async def _show_rules(message: Message, state: FSMContext, db: Database):
    rules = await db.get_merchant_rules()
    if not rules:
        await message.answer("Выученных правил пока нет — они появляются, "
                             "когда отвечаешь на вопросы при импорте.")
        return
    lines = [
        f"{i}. {html.escape(pattern)} → {html.escape(cat)}"
        + (f" ({html.escape(comment)})" if comment else "")
        for i, (pattern, cat, comment) in enumerate(rules, 1)
    ]
    await state.set_state(SettingsFlow.manage_rules)
    await state.update_data(rules=[r[0] for r in rules])
    await message.answer(
        "<b>Правила мерчантов</b>\n" + "\n".join(lines)
        + "\n\nУдалить правило: пришли его номер.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✖️ Закрыть", callback_data="set:cancelinput")
        ]]),
    )


@router.message(SettingsFlow.manage_rules, Command("done"))
async def rules_done(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Ок.")


@router.message(SettingsFlow.manage_rules, F.text, ~F.text.in_(MENU_TEXTS),
                ~F.text.startswith("/"))
async def rules_delete(message: Message, state: FSMContext, db: Database):
    data = await state.get_data()
    patterns = data["rules"]
    try:
        idx = int(message.text.strip())
        if not 1 <= idx <= len(patterns):
            raise ValueError
    except ValueError:
        await message.answer(f"Пришли номер правила (1–{len(patterns)}) или /done")
        return
    await db.delete_merchant_rule(patterns[idx - 1])
    await message.answer(f"Правило «{html.escape(patterns[idx - 1])}» удалено.",
                         parse_mode="HTML")
    await _show_rules(message, state, db)
