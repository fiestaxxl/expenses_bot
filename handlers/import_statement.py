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

    if await state.get_state() in (ImportFlow.waiting_category, ImportFlow.preview):
        data = await state.get_data()
        pending = data.get("pending", []) + [(path, doc.file_name or "выписка.pdf")]
        await state.update_data(pending=pending)
        await message.answer(f"📥 Файл в очереди ({len(pending)}). Сначала закончим с текущей выпиской.")
        return

    await _process_file(message, state, db, path, doc.file_name or "выписка.pdf")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    data = await state.get_data()
    for p, _ in data.get("pending", []):
        _rm(p)
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

def _preview_keyboard(n: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Импортировать ({n})", callback_data="imp:commit")],
        [InlineKeyboardButton(text="❌ Отменить импорт", callback_data="imp:cancel")],
    ])


async def _show_preview(message: Message, state: FSMContext, db: Database):
    data = await state.get_data()
    staged = data["staged"]
    staged.sort(key=lambda e: (e["year"], e["month"], e["day"]))
    await state.update_data(staged=staged)
    await state.set_state(ImportFlow.preview)

    if not staged:
        await message.answer(
            "Новых трат в выписке нет — только пропуски. Подтверди, чтобы я их запомнил.",
            reply_markup=_preview_keyboard(0),
        )
        return

    chunks = editing.render_list(staged)
    total = f"{sum(e['amount'] for e in staged):,.2f}".replace(",", " ")
    for chunk in chunks[:-1]:
        await message.answer(chunk, parse_mode="HTML")
    await message.answer(
        chunks[-1] + f"\n\nИтого: <b>{total} ₽</b>\n{editing.HELP}",
        parse_mode="HTML",
        reply_markup=_preview_keyboard(len(staged)),
    )


@router.message(ImportFlow.preview, F.text)
async def preview_edit(message: Message, state: FSMContext, db: Database):
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
        note = f"Строка {cmd.index} удалена. Нумерация сдвинулась!"
    elif cmd.action == "category":
        e["category"] = cmd.value
        note = editing.format_row(cmd.index, e)
    elif cmd.action == "amount":
        e["amount"] = cmd.value
        note = editing.format_row(cmd.index, e)
    else:
        e["comment"] = cmd.value or None
        note = editing.format_row(cmd.index, e)

    await state.update_data(staged=staged, mark=data["mark"])
    await message.answer(note, parse_mode="HTML",
                         reply_markup=_preview_keyboard(len(staged)))


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
