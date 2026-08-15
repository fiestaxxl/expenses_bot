"""Интерактивный просмотр/правка списков трат: страницы с кнопками-номерами,
карточка траты с кнопками по каждому полю. Плюс текстовые команды для скорости.

Текстовые команды (номер строки + действие):
  3 Такси          — сменить категорию (можно кусок названия: «3 еда общ»)
  3 450            — сменить сумму
  3 # новый текст  — сменить комментарий (пустой «3 #» удаляет комментарий)
  3 удалить        — убрать строку
"""

import html
import math
import re

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

MAX_MSG = 3500  # запас до телеграмного лимита 4096
PAGE_SIZE = 10

HELP = (
    "Нажми на номер траты под списком, чтобы её поправить.\n"
    "<i>Быстрая правка текстом: «3 Такси», «3 450», «3 # коммент», «3 удалить»</i>"
)

_CMD = re.compile(r"^(\d+)\s+(.+)$", re.S)


class EditCommand:
    def __init__(self, index: int, action: str, value=None):
        self.index = index      # 1-based номер строки
        self.action = action    # 'category' | 'amount' | 'comment' | 'delete'
        self.value = value


def match_category(text: str, categories: list[str]) -> str | list[str] | None:
    """Точное или частичное совпадение. Возвращает категорию,
    список кандидатов (неоднозначно) или None."""
    norm = lambda s: re.sub(r"[^\wа-яё]+", " ", s.lower()).strip()
    t = norm(text)
    if not t:
        return None
    exact = [c for c in categories if norm(c) == t]
    if exact:
        return exact[0]
    words = t.split()
    partial = [c for c in categories if all(w in norm(c) for w in words)]
    if len(partial) == 1:
        return partial[0]
    return partial or None


def parse_edit(text: str, categories: list[str]) -> EditCommand | str | None:
    """Разбирает команду правки. Возвращает EditCommand,
    строку с ошибкой или None (текст не похож на команду)."""
    m = _CMD.match(text.strip())
    if not m:
        return None
    index = int(m.group(1))
    rest = m.group(2).strip()

    if rest.lower() in ("удалить", "удали", "x", "х", "-"):
        return EditCommand(index, "delete")
    if rest.startswith("#"):
        return EditCommand(index, "comment", rest[1:].strip())
    try:
        amount = float(rest.replace(",", ".").replace(" ", ""))
        if amount <= 0:
            return "Сумма должна быть больше нуля."
        return EditCommand(index, "amount", amount)
    except ValueError:
        pass
    cat = match_category(rest, categories)
    if isinstance(cat, str):
        return EditCommand(index, "category", cat)
    if isinstance(cat, list) and cat:
        return "Уточни категорию, подходит несколько: " + ", ".join(cat)
    return f"Не понял «{rest}». {strip_tags(HELP)}"


def strip_tags(s: str) -> str:
    return re.sub(r"</?[a-z]+>", "", s)


def format_row(n: int, e: dict) -> str:
    """e: {day, month, amount, category, comment}"""
    amount = f"{e['amount']:,.2f}".replace(",", " ").rstrip("0").rstrip(".")
    comment = f" — {html.escape(str(e['comment']))}" if e.get("comment") else ""
    return (f"{n}. {e['day']:02d}.{e['month']:02d}  <b>{amount} ₽</b>  "
            f"{html.escape(e['category'])}{comment}")


def render_list(entries: list[dict]) -> list[str]:
    """Нумерованный список кусками под лимит телеграма."""
    chunks, cur = [], []
    size = 0
    for i, e in enumerate(entries, 1):
        line = format_row(i, e)
        if size + len(line) > MAX_MSG and cur:
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks or ["(пусто)"]


# ── Интерактивные страницы и карточки ────────────────────────────────────────
# Схема callback_data (prefix — 'lst' для /list, 'impv' для превью импорта):
#   {prefix}:page:N        — показать страницу N
#   {prefix}:open:I        — карточка траты I (индекс с нуля)
#   {prefix}:f:FIELD:I     — править поле (category|amount|date|comment)
#   {prefix}:sc:I:CI       — установить категорию номер CI из списка категорий
#   {prefix}:del:I / del2:I — удалить (с подтверждением)

def clamp_page(n_entries: int, page: int) -> tuple[int, int]:
    n_pages = max(1, math.ceil(n_entries / PAGE_SIZE))
    return max(0, min(page, n_pages - 1)), n_pages


def page_view(entries: list[dict], page: int, prefix: str,
              header: str = "", action_rows: list | None = None):
    """(text, keyboard) — страница списка с кнопками-номерами и навигацией."""
    page, n_pages = clamp_page(len(entries), page)
    lo = page * PAGE_SIZE
    hi = min(len(entries), lo + PAGE_SIZE)

    lines = [format_row(i + 1, entries[i]) for i in range(lo, hi)]
    text = (header + "\n\n" if header else "") + ("\n".join(lines) or "(пусто)")
    if entries:
        text += "\n\n👇 Нажми номер, чтобы поправить трату"

    nums = [InlineKeyboardButton(text=str(i + 1), callback_data=f"{prefix}:open:{i}")
            for i in range(lo, hi)]
    rows = [nums[j:j + 5] for j in range(0, len(nums), 5)]
    if n_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}:page:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"стр. {page + 1}/{n_pages}",
                                        callback_data=f"{prefix}:page:{page}"))
        if page < n_pages - 1:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{prefix}:page:{page + 1}"))
        rows.append(nav)
    rows.extend(action_rows or [])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def card_view(entries: list[dict], idx: int, prefix: str):
    """(text, keyboard) — карточка одной траты с кнопками по полям."""
    e = entries[idx]
    amount = f"{e['amount']:,.2f}".replace(",", " ").rstrip("0").rstrip(".")
    comment = html.escape(str(e["comment"])) if e.get("comment") else "—"
    text = (f"✏️ <b>Трата {idx + 1}</b>\n"
            f"📅 Дата: {e['day']:02d}.{e['month']:02d}.{e.get('year', '')}\n"
            f"💰 Сумма: {amount} ₽\n"
            f"🏷 Категория: {html.escape(e['category'])}\n"
            f"💬 Комментарий: {comment}\n\n"
            f"Что поменять?")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏷 Категория", callback_data=f"{prefix}:f:category:{idx}"),
         InlineKeyboardButton(text="💰 Сумма", callback_data=f"{prefix}:f:amount:{idx}")],
        [InlineKeyboardButton(text="📅 Дата", callback_data=f"{prefix}:f:date:{idx}"),
         InlineKeyboardButton(text="💬 Комментарий", callback_data=f"{prefix}:f:comment:{idx}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"{prefix}:del:{idx}")],
        [InlineKeyboardButton(text="⬅️ К списку", callback_data=f"{prefix}:page:{idx // PAGE_SIZE}")],
    ])
    return text, kb


def category_kb(idx: int, categories: list[str], prefix: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=c, callback_data=f"{prefix}:sc:{idx}:{ci}")
         for ci, c in list(enumerate(categories))[i:i + 2]]
        for i in range(0, len(categories), 2)
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{prefix}:open:{idx}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_del_kb(idx: int, prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"{prefix}:del2:{idx}"),
        InlineKeyboardButton(text="⬅️ Нет", callback_data=f"{prefix}:open:{idx}"),
    ]])


FIELD_PROMPTS = {
    "amount": "Пришли новую сумму (например 450 или 199.90):",
    "date": "Пришли новую дату: <code>дд.мм</code> или <code>дд.мм.гггг</code>",
    "comment": "Пришли новый комментарий (или <code>-</code>, чтобы убрать):",
}

_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?$")


def parse_field_value(field: str, text: str, default_year: int):
    """Значение поля из текста пользователя или строка-ошибка."""
    text = text.strip()
    if field == "amount":
        try:
            amount = float(text.replace(",", ".").replace(" ", ""))
            if amount <= 0:
                raise ValueError
            return amount
        except ValueError:
            return "Нужно число больше нуля, например 450 или 199.90"
    if field == "date":
        m = _DATE_RE.match(text)
        if not m:
            return "Формат: дд.мм или дд.мм.гггг, например 05.08"
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else default_year
        import datetime
        try:
            datetime.date(year, month, day)
        except ValueError:
            return "Такой даты не бывает, проверь число и месяц"
        return (day, month, year)
    # comment
    return "" if text == "-" else text
