"""Текстовые команды правки списка трат и рендер списков.

Синтаксис команд (номер строки + действие):
  3 Такси          — сменить категорию (можно кусок названия: «3 еда общ»)
  3 450            — сменить сумму
  3 # новый текст  — сменить комментарий (пустой «3 #» удаляет комментарий)
  3 удалить        — убрать строку
"""

import html
import re

MAX_MSG = 3500  # запас до телеграмного лимита 4096

HELP = (
    "Правка: <code>№ категория</code> · <code>№ сумма</code> · "
    "<code>№ # комментарий</code> · <code>№ удалить</code>"
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
