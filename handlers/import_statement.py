"""Импорт PDF-выписок: кидаешь файл в чат — бот парсит, категоризирует,
показывает редактируемое превью и по подтверждению заливает в БД.

Поддерживаются выписки Сбера («Выписка по платёжному счёту»),
Т-Банка («Справка о движении средств») и Яндекс Банка («Выписка по договору»).

Флоу: вопросы по незнакомым мерчантам (бот запоминает ответы) → превью
со всеми будущими тратами, которое можно править текстовыми командами →
кнопка «Импортировать». Каждый импорт можно целиком отменить: /imports.

Самопереводы и поступления отбрасываются (свои реквизиты — в /settings).
Повторный импорт того же файла или пересекающейся выписки дублей не создаёт.
"""

import html
import logging
import os
import tempfile
from datetime import date, datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import statement_parser as sp
from config import config
from database import Database
from handlers import editing

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(F.from_user.id == config.OWNER_ID)


class ImportFlow(StatesGroup):
    waiting_category = State()   # вопросы по незнакомым операциям
    preview = State()            # редактируемое превью перед вставкой
    preview_field = State()      # ждём текст для суммы/даты/комментария в превью


class SetupFlow(StatesGroup):
    """Онбординг перед первым импортом: свои реквизиты для фильтра самопереводов."""
    step = State()


PFX = "impv"  # callback-префикс интерактивного превью

# (ключ настройки, заголовок, зачем это нужно / что будет без него, подсказка формата)
SETUP_STEPS = [
    ("own_phones", "📱 Твой номер телефона",
     "Переводы между своими банками в выписках выглядят как обычные переводы "
     "по номеру телефона. Если я знаю твой номер — молча отброшу их, это не траты.\n"
     "<i>Пропустишь — буду спрашивать про каждый такой перевод отдельно.</i>",
     "Пришли номер (или несколько через запятую): +7 950 011-88-91"),
    ("own_names", "👤 Твоё имя, как его пишут банки",
     "Банки подписывают переводы сокращённым именем, например "
     "«Перевод для Г. Иван Сергеевич». Зная его, я отличу перевод самому себе "
     "от перевода другому человеку.\n"
     "<i>Пропустишь — такие переводы превратятся в вопросы.</i>",
     "Пришли как в выписке (можно несколько через запятую): Г. Иван Сергеевич"),
    ("own_banks", "🏦 Банки, где у тебя есть свои счета",
     "Если переводишь сам себе, например, в Яндекс Банк — назови такие банки, "
     "и я не буду считать эти переводы тратами.\n"
     "<i>Пропустишь — каждый перевод в другой банк станет вопросом.</i>",
     "Через запятую, как пишут в выписках: Yandex, Яндекс, Альфа"),
    ("own_accounts", "📄 Номера своих счетов/договоров",
     "Они видны в шапке каждой выписки. Внутрибанковские переводы между своими "
     "счетами я тогда отброшу автоматически.\n"
     "<i>Пропустишь — про них буду спрашивать.</i>",
     "Номера через запятую: 5312639601, 40817810800028174889"),
]


async def _own_settings(db: Database) -> dict:
    return {key: await db.get_setting_list(key) for key in sp.SELF_KEYS}


async def _import_after(db: Database) -> date:
    raw = await db.get_setting("import_after")
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return date.min


# ── Приём документа ──────────────────────────────────────────────────────────

@router.message(F.document)
async def handle_document(message: Message, state: FSMContext, db: Database, bot: Bot):
    doc = message.document
    name = (doc.file_name or "").lower()
    if not name.endswith(".pdf") and doc.mime_type != "application/pdf":
        await message.answer("Это не PDF. Пришли выписку из Сбера, Т-Банка или Яндекс Банка.")
        return

    fd, path = tempfile.mkstemp(suffix=".pdf", prefix="statement_")
    os.close(fd)
    await bot.download(doc, destination=path)

    if await state.get_state() in (ImportFlow.waiting_category, ImportFlow.preview,
                                   ImportFlow.preview_field, SetupFlow.step):
        data = await state.get_data()
        pending = data.get("pending", []) + [(path, doc.file_name or "выписка.pdf")]
        await state.update_data(pending=pending)
        await message.answer(f"📥 Файл в очереди ({len(pending)}). Сначала закончим с текущей выпиской.")
        return

    filename = doc.file_name or "выписка.pdf"

    # первый импорт — сначала короткая настройка
    if not await _setup_complete(db):
        await state.set_state(SetupFlow.step)
        await state.update_data(setup_idx=0, setup_file=path, setup_filename=filename)
        await message.answer(
            "👋 Это твоя первая выписка! Прежде чем разобрать её, задам "
            "4 коротких вопроса.\n\n"
            "<b>Зачем:</b> в выписках полно переводов самому себе между своими "
            "банками — это не траты, и я хочу отбрасывать их автоматически. "
            "Для этого мне нужно знать твои реквизиты.\n\n"
            "Каждый шаг можно пропустить — импорт всё равно сработает, просто "
            "я буду чаще переспрашивать. Всё сохраняется только в твоей базе "
            "и правится потом в /settings.",
            parse_mode="HTML",
        )
        await _setup_ask(message, 0)
        return

    await _process_file(message, state, db, path, filename)


async def _setup_complete(db: Database) -> bool:
    if await db.get_setting("setup_done") == "1":
        return True
    for key, *_ in SETUP_STEPS:
        if await db.get_setting(key):
            return True  # реквизиты уже заведены (сидом или руками)
    return False


async def _setup_ask(message: Message, idx: int):
    key, title, why, hint = SETUP_STEPS[idx]
    await message.answer(
        f"<b>Шаг {idx + 1}/{len(SETUP_STEPS)}. {title}</b>\n\n{why}\n\n{hint}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⏭ Пропустить этот шаг", callback_data="setup:skip")
        ]]),
    )


def _normalize_setup(key: str, text: str) -> str:
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if key == "own_phones":
        digits = ["".join(ch for ch in p if ch.isdigit())[-10:] for p in parts]
        parts = [d for d in digits if len(d) == 10]
    return ", ".join(parts)


async def _setup_advance(message: Message, state: FSMContext, db: Database,
                         value: str | None):
    """Сохраняет ответ (или пропуск) и двигает визард дальше."""
    data = await state.get_data()
    idx = data["setup_idx"]
    key, title, _, _ = SETUP_STEPS[idx]

    if value:
        await db.set_setting(key, value)
        await message.answer(f"✅ Сохранил: <code>{html.escape(value)}</code>",
                             parse_mode="HTML")
    else:
        await message.answer("Ок, пропускаем — про эти переводы я буду спрашивать. "
                             "Передумаешь — /settings.")

    idx += 1
    if idx < len(SETUP_STEPS):
        await state.update_data(setup_idx=idx)
        await _setup_ask(message, idx)
        return

    await db.set_setting("setup_done", "1")
    await message.answer("🎉 Настройка готова! Теперь разберу выписку…")
    path, filename = data["setup_file"], data["setup_filename"]
    await state.set_state(None)
    await _process_file(message, state, db, path, filename)


@router.message(SetupFlow.step, F.text, ~F.text.in_(editing.MENU_TEXTS),
                ~F.text.startswith("/"))
async def setup_answer(message: Message, state: FSMContext, db: Database):
    data = await state.get_data()
    key = SETUP_STEPS[data["setup_idx"]][0]
    value = _normalize_setup(key, message.text)
    if not value:
        await message.answer("Не понял значение — попробуй ещё раз или нажми "
                             "«Пропустить этот шаг».")
        return
    await _setup_advance(message, state, db, value)


@router.callback_query(SetupFlow.step, F.data == "setup:skip")
async def setup_skip(callback: CallbackQuery, state: FSMContext, db: Database):
    await callback.answer()
    await _setup_advance(callback.message, state, db, None)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    data = await state.get_data()
    for p, _ in data.get("pending", []):
        _rm(p)
    if data.get("setup_file"):
        _rm(data["setup_file"])
    await state.clear()
    await message.answer("Ок, отменил.")


def _rm(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


async def _process_file(message: Message, state: FSMContext, db: Database,
                        path: str, filename: str):
    try:
        text = await sp.pdf_to_text(path)
        ops = sp.parse_statement(text)
    except sp.ParseError as e:
        await message.answer(f"⚠️ {html.escape(str(e))}", parse_mode="HTML")
        _rm(path)
        await _next_pending(message, state, db)
        return
    finally:
        _rm(path)

    rules = await db.get_merchant_rules()
    own = await _own_settings(db)
    sp.classify(ops, rules, own)
    known = await db.known_hashes([o.op_hash for o in ops])
    import_after = await _import_after(db)
    staged, queue, mark, counts = sp.stage_ops(ops, known, import_after)

    bank = ops[0].bank
    d_min, d_max = min(o.op_date for o in ops), max(o.op_date for o in ops)
    period = f"{d_min.strftime('%d.%m')}–{d_max.strftime('%d.%m.%Y')}"
    old_note = (f"{counts['old']} до {import_after.strftime('%d.%m.%Y')}, "
                if counts["old"] else "")
    summary = (
        f"📄 <b>{bank}</b>, выписка {period}, операций: {len(ops)}\n"
        f"• распознано трат: <b>{len(staged)}</b>\n"
        f"• пропущено: {counts['self']} самопереводов/поступлений, "
        f"{old_note}{counts['dup']} уже импортировано\n"
        f"• вопросов: <b>{len(queue)}</b>"
    )
    await message.answer(summary, parse_mode="HTML")

    data = await state.get_data()
    await state.update_data(
        staged=staged, queue=queue, mark=mark,
        pending=data.get("pending", []),
        meta={"bank": bank, "period": period, "filename": filename},
    )
    if queue:
        await state.set_state(ImportFlow.waiting_category)
        await _ask_next(message, state, db)
    elif staged or mark:
        await _show_preview(message, state, db)
    else:
        await message.answer("Импортировать нечего.")
        await state.set_state(None)
        await _next_pending(message, state, db)


# ── Вопросы по неизвестным операциям ─────────────────────────────────────────

async def _ask_next(message: Message, state: FSMContext, db: Database):
    data = await state.get_data()
    item = data["queue"][0]
    ops = item["ops"]
    lines = [f"  {e['date_str']} — {e['amount']:,.2f} ₽".replace(",", " ") for e in ops[:5]]
    if len(ops) > 5:
        lines.append(f"  … и ещё {len(ops) - 5}")
    hint = f"\nСбер считает: <i>{html.escape(ops[0]['sber_hint'])}</i>" if ops[0]["sber_hint"] else ""

    if item["kind"] == "transfer":
        head = f"🔀 <b>{html.escape(ops[0]['desc'])}</b>"
        question = "Это перевод себе или трата?"
    else:
        head = f"🏪 <b>{html.escape(item['merchant'])}</b>"
        question = "Какая категория?"

    text = f"{head}\n" + "\n".join(lines) + hint + f"\n\n{question}"

    categories = await db.get_categories()
    rows = [
        [InlineKeyboardButton(text=c, callback_data=f"imp:c:{c}") for c in categories[i:i + 2]]
        for i in range(0, len(categories), 2)
    ]
    if item["kind"] == "transfer":
        rows.insert(0, [InlineKeyboardButton(text="🔁 Себе — пропустить", callback_data="imp:self")])
    rows.append([InlineKeyboardButton(text="⏭ Не трата, пропустить", callback_data="imp:skip")])

    await message.answer(text, parse_mode="HTML",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(ImportFlow.waiting_category, F.data.startswith("imp:"))
async def process_answer(callback: CallbackQuery, state: FSMContext, db: Database):
    data = await state.get_data()
    queue = data["queue"]
    if not queue:
        await callback.answer()
        return
    item = queue.pop(0)
    staged, mark = data["staged"], data["mark"]

    action = callback.data.split(":", 2)
    if action[1] == "c":
        category = action[2]
        for e in item["ops"]:
            e["category"] = category
            e["comment"] = e["comment"] or item["merchant"]
            staged.append(e)
        if item["learn"]:
            await db.add_merchant_rule(item["merchant"], category)
        note = f"→ {category}"
    else:  # self / skip
        mark.extend(e["hash"] for e in item["ops"])
        note = "пропущено (себе)" if action[1] == "self" else "пропущено"

    label = item["merchant"] if item["kind"] == "unknown" else item["ops"][0]["desc"]
    await callback.message.edit_text(f"{html.escape(label[:60])} {note}", parse_mode="HTML")
    await callback.answer()

    await state.update_data(staged=staged, queue=queue, mark=mark)
    if queue:
        await _ask_next(callback.message, state, db)
    else:
        await _show_preview(callback.message, state, db)


# ── Превью с правкой ─────────────────────────────────────────────────────────

def _preview_actions(n: int) -> list[list[InlineKeyboardButton]]:
    return [
        [InlineKeyboardButton(text=f"✅ Импортировать ({n})", callback_data="imp:commit")],
        [InlineKeyboardButton(text="❌ Отменить импорт", callback_data="imp:cancel")],
    ]


def _preview_header(data) -> str:
    staged = data["staged"]
    total = f"{sum(e['amount'] for e in staged):,.2f}".replace(",", " ")
    meta = data["meta"]
    return (f"<b>Превью: {meta['bank']} {meta['period']}</b> — "
            f"{len(staged)} трат на {total} ₽\n"
            f"Проверь список, поправь что нужно и жми «Импортировать».")


async def _show_preview(message: Message, state: FSMContext, db: Database,
                        edit_message: bool = False):
    data = await state.get_data()
    staged = data["staged"]
    staged.sort(key=lambda e: (e["year"], e["month"], e["day"]))
    await state.update_data(staged=staged)
    await state.set_state(ImportFlow.preview)

    if not staged:
        await message.answer(
            "Новых трат в выписке нет — только пропуски. Подтверди, чтобы я их запомнил.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=_preview_actions(0)),
        )
        return

    text, kb = editing.page_view(staged, data.get("page", 0), PFX,
                                 header=_preview_header(data),
                                 action_rows=_preview_actions(len(staged)))
    if edit_message:
        await message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(ImportFlow.preview, F.data.startswith(f"{PFX}:"))
async def preview_callback(callback: CallbackQuery, state: FSMContext, db: Database):
    parts = callback.data.split(":")
    action = parts[1]
    data = await state.get_data()
    staged = data["staged"]

    async def refresh_page(page: int):
        await state.update_data(page=page)
        d = await state.get_data()
        text, kb = editing.page_view(staged, page, PFX, header=_preview_header(d),
                                     action_rows=_preview_actions(len(staged)))
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

    if action == "page":
        if parts[2] == "ask":
            _, n_pages = editing.clamp_page(len(staged), 0)
            await callback.answer(f"Пришли номер страницы текстом (1–{n_pages})",
                                  show_alert=True)
            return
        await refresh_page(int(parts[2]))

    elif action == "open":
        idx = int(parts[2])
        if idx >= len(staged):
            await refresh_page(0)
        else:
            text, kb = editing.card_view(staged, idx, PFX)
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
            await state.set_state(ImportFlow.preview_field)
            await state.update_data(edit_idx=idx, edit_field=field)
            await callback.message.answer(
                editing.FIELD_PROMPTS[field], parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✖️ Отмена", callback_data=f"{PFX}:fcancel:{idx}")
                ]]),
            )

    elif action == "sc":
        idx, ci = int(parts[2]), int(parts[3])
        staged[idx]["category"] = data["categories"][ci]
        await state.update_data(staged=staged)
        text, kb = editing.card_view(staged, idx, PFX)
        await callback.message.edit_text("✅ Категория обновлена\n\n" + text,
                                         parse_mode="HTML", reply_markup=kb)

    elif action == "del":
        idx = int(parts[2])
        await callback.message.edit_reply_markup(
            reply_markup=editing.confirm_del_kb(idx, PFX))

    elif action == "del2":
        idx = int(parts[2])
        data["mark"].append(staged[idx]["hash"])  # больше не предлагать эту операцию
        staged.pop(idx)
        await state.update_data(staged=staged, mark=data["mark"])
        await refresh_page(min(idx, max(len(staged) - 1, 0)) // editing.PAGE_SIZE)

    await callback.answer()


@router.callback_query(ImportFlow.preview_field, F.data.startswith(f"{PFX}:fcancel:"))
async def preview_field_cancel(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":")[2])
    data = await state.get_data()
    await state.set_state(ImportFlow.preview)
    text, kb = editing.card_view(data["staged"], idx, PFX)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.message(ImportFlow.preview_field, F.text, ~F.text.in_(editing.MENU_TEXTS),
                ~F.text.startswith("/"))
async def preview_field_input(message: Message, state: FSMContext, db: Database):
    data = await state.get_data()
    idx, field = data["edit_idx"], data["edit_field"]
    staged = data["staged"]
    e = staged[idx]

    value = editing.parse_field_value(field, message.text, e["year"])
    if isinstance(value, str) and field != "comment":
        await message.answer(value)
        return

    if field == "amount":
        e["amount"] = value
    elif field == "date":
        e["day"], e["month"], e["year"] = value
    else:
        e["comment"] = value or None

    await state.set_state(ImportFlow.preview)
    await state.update_data(staged=staged)
    text, kb = editing.card_view(staged, idx, PFX)
    await message.answer("✅ Обновил\n\n" + text, parse_mode="HTML", reply_markup=kb)


@router.message(ImportFlow.preview, F.text, ~F.text.in_(editing.MENU_TEXTS),
                ~F.text.startswith("/"))
async def preview_edit(message: Message, state: FSMContext, db: Database):
    # просто число — переход на страницу превью
    if message.text.strip().isdigit():
        data = await state.get_data()
        page, _ = editing.clamp_page(len(data["staged"]), int(message.text) - 1)
        await state.update_data(page=page)
        await _show_preview(message, state, db)
        return

    categories = await db.get_categories()
    cmd = editing.parse_edit(message.text, categories)
    if cmd is None:
        await message.answer(editing.HELP, parse_mode="HTML")
        return
    if isinstance(cmd, str):
        await message.answer(cmd)
        return

    data = await state.get_data()
    staged = data["staged"]
    if not 1 <= cmd.index <= len(staged):
        await message.answer(f"Нет строки {cmd.index} (всего {len(staged)}).")
        return
    e = staged[cmd.index - 1]
    if cmd.action == "delete":
        data["mark"].append(e["hash"])  # больше не предлагать эту операцию
        staged.pop(cmd.index - 1)
        note = f"🗑 Строка {cmd.index} удалена. Нумерация сдвинулась!"
    elif cmd.action == "category":
        e["category"] = cmd.value
        note = "✅ " + editing.format_row(cmd.index, e)
    elif cmd.action == "amount":
        e["amount"] = cmd.value
        note = "✅ " + editing.format_row(cmd.index, e)
    else:
        e["comment"] = cmd.value or None
        note = "✅ " + editing.format_row(cmd.index, e)

    await state.update_data(staged=staged, mark=data["mark"])
    await message.answer(note, parse_mode="HTML")
    await _show_preview(message, state, db)


@router.callback_query(ImportFlow.preview, F.data == "imp:cancel")
async def preview_cancel(callback: CallbackQuery, state: FSMContext, db: Database):
    await callback.message.edit_text("Импорт отменён, в базу ничего не попало.")
    await callback.answer()
    await state.set_state(None)
    await _next_pending(callback.message, state, db)


@router.callback_query(ImportFlow.preview, F.data == "imp:commit")
async def preview_commit(callback: CallbackQuery, state: FSMContext, db: Database):
    data = await state.get_data()
    staged, mark, meta = data["staged"], data["mark"], data["meta"]

    import_id = await db.create_import(meta["bank"], meta["period"], meta["filename"])
    items = [
        {"amount": e["amount"], "category": e["category"],
         "day": e["day"], "month": e["month"], "year": e["year"],
         "comment": e["comment"], "hash": e["hash"]}
        for e in staged
    ]
    added = await db.import_expenses(items, import_id)
    await db.mark_imported(mark, import_id)

    if added:
        totals: dict[str, float] = {}
        for e in staged:
            totals[e["category"]] = totals.get(e["category"], 0) + e["amount"]
        lines = [
            f"  {cat}: {total:,.2f} ₽".replace(",", " ")
            for cat, total in sorted(totals.items(), key=lambda x: -x[1])
        ]
        total_sum = f"{sum(totals.values()):,.2f}".replace(",", " ")
        text = (f"✅ Импорт #{import_id}: добавлено <b>{added}</b> трат "
                f"на <b>{total_sum} ₽</b>:\n" + "\n".join(lines)
                + "\n\nОтменить можно в /imports")
        from handlers.budgets import budget_summary_for
        today = config.today()
        summary = await budget_summary_for(db, set(totals), today.month, today.year)
        if summary:
            text += "\n\n<b>Лимиты месяца:</b>\n" + summary
    else:
        text = "Готово, новых трат не добавлено."
    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer()

    await state.set_state(None)
    await _next_pending(callback.message, state, db)


async def _next_pending(message: Message, state: FSMContext, db: Database):
    data = await state.get_data()
    pending = data.get("pending", [])
    await state.clear()
    if pending:
        path, filename = pending[0]
        await state.update_data(pending=pending[1:])
        await message.answer(f"📥 Беру следующий файл из очереди ({len(pending)})…")
        await _process_file(message, state, db, path, filename)


# ── История импортов и отмена ────────────────────────────────────────────────

@router.message(Command("imports"))
async def cmd_imports(message: Message, db: Database):
    rows = await db.list_imports()
    if not rows:
        await message.answer("Импортов ещё не было.")
        return
    lines, buttons = [], []
    for imp_id, bank, period, created, n, total in rows:
        total_s = f"{total:,.0f}".replace(",", " ")
        lines.append(f"#{imp_id} · {bank} {period} — {n} трат на {total_s} ₽ ({created[:10]})")
        if n:
            buttons.append([InlineKeyboardButton(
                text=f"↩️ Отменить #{imp_id} ({n} трат)",
                callback_data=f"impundo:{imp_id}")])
    await message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None,
    )


@router.callback_query(F.data.startswith("impundo:"))
async def undo_ask(callback: CallbackQuery):
    imp_id = callback.data.split(":")[1]
    await callback.message.answer(
        f"Точно удалить все траты импорта #{imp_id}? Это уберёт их из базы, "
        f"выписку можно будет импортировать заново.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"impundo2:{imp_id}"),
            InlineKeyboardButton(text="Нет", callback_data="impundo2:no"),
        ]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("impundo2:"))
async def undo_confirm(callback: CallbackQuery, db: Database):
    arg = callback.data.split(":")[1]
    if arg == "no":
        await callback.message.edit_text("Ок, не трогаю.")
        await callback.answer()
        return
    deleted = await db.undo_import(int(arg))
    await callback.message.edit_text(f"↩️ Импорт #{arg} отменён, удалено трат: {deleted}.")
    await callback.answer()
