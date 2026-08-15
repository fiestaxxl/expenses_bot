"""Парсер банковских PDF-выписок: Сбер, Т-Банк, Яндекс Банк.

Извлечение текста — через `pdftotext -layout` (пакет poppler-utils,
на сервере: apt install poppler-utils, на маке: brew install poppler).

Результат разбора — список Operation. Классификация:
  - skip_self: перевод между своими счетами / поступление — не трата
  - категория+комментарий по словарю мерчантов (MERCHANT_RULES + правила из БД)
  - unknown: мерчант не распознан, бот спросит категорию
  - transfer: перевод в другой банк / незнакомый договор / человеку —
    бот спросит, себе это или трата
"""

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date

# ── Свои реквизиты (настройки, хранятся в таблице settings) ──────────────────
# own_phones   — последние 10 цифр своих номеров: "9500118891"
# own_accounts — номера своих счетов/договоров в любых банках
# own_names    — как банки пишут ваше имя в переводах: "Г. Иван Сергеевич"
# own_banks    — подстроки банков, переводы в которые считать своими: "Yandex,Яндекс"
# Всё, что уходит на эти реквизиты, считается самопереводом и не попадает в траты.

SELF_KEYS = ("own_phones", "own_accounts", "own_names", "own_banks")

# ── Словарь мерчантов: (regex по описанию, категория, комментарий) ───────────
# Порядок важен: первое совпадение выигрывает.

MERCHANT_RULES: list[tuple[str, str, str | None]] = [
    # такси
    (r"YANDEX[*.]?\s?(GO|4121|TAXI|FASTEN)|YANDEX\.TAXI", "Такси", None),
    # транспорт
    (r"YANDEX\*RASPEL", "Транспорт", "Электричка (Яндекс Расписания)"),
    (r"СЗППК", "Транспорт", "Электричка"),
    (r"METRO TPP|GORELECTROTRANS|GORELEKTROTR|AVTOBUSNYJ|VEST-SERVIS|WHOOSH|RNAZK", "Транспорт", None),
    # еда: доставка/кафе/пекарни/чаевые
    (r"YANDEX\*EDA", "Еда (общепит)", "Яндекс Еда"),
    (r"VKUSVILL|VV_\d", "Еда (общепит)", "ВкусВилл"),
    (r"BUSHE", "Еда (общепит)", "Буше"),
    (r"NETMONET|СберЧаевые|TP\*TIPS", "Еда (общепит)", "Чаевые"),
    (r"COFFEEBON|SURF COFFEE|SUBWAY|FASTFUD|PEKARNYA|ПЕКАРНЯ|BAKERY|CEKH", "Еда (общепит)", None),
    # еда: продукты домой
    (r"DIXY|ДИКСИ|PYATEROCHKA|ПЯТЕРОЧКА|MAGNIT|МАГНИТ|LENTA|ЛЕНТА-|Лента-|PEREKRESTOK|"
     r"ПЕРЕКРЕСТОК|Перекресток|KRASNOE&BELOE|SEMISHAGOFF|AZBUKA VKUSA|MINIMARKET|"
     r"PRODUKTY|WINELAB|Winelab|AM SANKT|АМ_P_QR|\bAM\b", "Еда (дома)", None),
    # здоровье
    (r"STOMATOLOG", "Здоровье", "Стоматология"),
    (r"APTEKA|АПТЕКА|36,6", "Здоровье", "Аптека"),
    (r"Dolyame", "Здоровье", "Долями (косметика)"),
    # подарки
    (r"GOLD APPLE", "Подарки", "Золотое Яблоко"),
    (r"CVETY|TSVETY|ЦВЕТЫ", "Подарки", "Цветы"),
    # подписки
    (r"YM\*kvmka", "Подписки", "YM*kvmka"),
    (r"FOLLOWPULSE", "Подписки", "FOLLOWPULSE"),
    (r"accountbroker", "Подписки", "Claude"),
    # связь
    (r"mBank\.t2|T2 MOBILE", "Связь и интернет", "t2"),
    (r"BEELINE|mBank\.beeline", "Связь и интернет", "Билайн"),
    (r"mBank\.MTS|МТС", "Связь и интернет", "МТС"),
    # развлечения
    (r"KINOTEATR|KARO|Kinokassa|KASSIR", "Развлечения", "Кино/билеты"),
    (r"Club Matchball", "Развлечения", "Club Matchball"),
    # инвестиции
    (r"Пополнение брокерского", "Инвестиции", "Пополнение брокерского счёта"),
    # путешествия
    (r"TRIP\.COM|AVIASALES|AEROFLOT|POBEDA|S7 AIRLINES", "Путешествия", None),
    # прочее
    (r"ZOOOPTTORG|ЗООМАГАЗИН", "Прочее", "Зоомагазин"),
    (r"LAMODA|Lamodа|Lamoda", "Одежда", "Lamoda"),
]

# Категории Сбера, которым верим, если мерчант не распознан словарём
SBER_HINTS = {
    "Супермаркеты": "Еда (дома)",
    "Рестораны и кафе": "Еда (общепит)",
    "Транспорт": "Транспорт",
    "Отдых и развлечения": "Развлечения",
    "Здоровье и красота": "Здоровье",
}


@dataclass
class Operation:
    bank: str                 # 'Сбер' | 'Т-Банк' | 'Яндекс'
    op_date: date
    time: str                 # 'HH:MM' или ''
    amount: float             # всегда > 0
    income: bool
    description: str          # очищенное описание/мерчант
    card: str = ""
    sber_category: str = ""   # категория из выписки Сбера (подсказка)
    # результат классификации:
    kind: str = ""            # 'expense' | 'self' | 'income' | 'transfer' | 'unknown'
    category: str = ""
    comment: str | None = None
    merchant_key: str = ""    # ключ для обучаемых правил

    @property
    def op_hash(self) -> str:
        raw = f"{self.bank}|{self.op_date.isoformat()}|{self.time}|" \
              f"{'+' if self.income else '-'}{self.amount:.2f}|{self.description}"
        return hashlib.sha1(raw.encode()).hexdigest()


class ParseError(Exception):
    pass


# ── Извлечение текста ────────────────────────────────────────────────────────

async def pdf_to_text(path: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "pdftotext", "-layout", path, "-",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise ParseError(
            "Не найден pdftotext. Установи poppler-utils:\n"
            "  apt install poppler-utils  (или brew install poppler)"
        )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise ParseError(f"pdftotext не смог прочитать файл: {err.decode(errors='ignore')[:200]}")
    return out.decode("utf-8", errors="ignore")


# ── Утилиты ──────────────────────────────────────────────────────────────────

def _amount(s: str) -> tuple[float, bool]:
    """'-5 000.00' / '+2 000,00' / '–64 376,00' -> (5000.0, income?)"""
    s = s.replace("\xa0", " ").strip()
    income = s.startswith("+")
    s = s.lstrip("+-–−").replace(" ", "").replace(",", ".")
    return float(s), income


def _date(s: str) -> date:
    d, m, y = s.split(".")
    return date(int(y), int(m), int(d))


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" ,")


def detect_bank(text: str) -> str:
    if "ТБАНК" in text or "TBANK.RU" in text:
        return "Т-Банк"
    if "Выписка по платёжному счёту" in text or "sberbank.ru" in text:
        return "Сбер"
    if "Яндекс Банк" in text:
        return "Яндекс"
    raise ParseError("Не удалось определить банк по содержимому PDF")


def parse_statement(text: str) -> list[Operation]:
    bank = detect_bank(text)
    parser = {"Т-Банк": _parse_tbank, "Сбер": _parse_sber, "Яндекс": _parse_yandex}[bank]
    ops = parser(text)
    if not ops:
        raise ParseError(f"Формат {bank} распознан, но не нашёл ни одной операции")
    return ops


# ── Т-Банк («Справка о движении средств») ────────────────────────────────────

_TB_ROW = re.compile(
    r"^\s*(\d{2}\.\d{2}\.\d{4})\s{2,}(\d{2}\.\d{2}\.\d{4})\s{2,}"
    r"([+\-][\d\s]+[.,]\d{2})\s*₽\s{2,}[+\-][\d\s]+[.,]\d{2}\s*₽\s{2,}"
    r"(\S.*?)(?:\s{2,}(\d{4}|—))?\s*$"
)
_TB_NOISE = re.compile(
    r"АКЦИОНЕРНОЕ|РОССИЯ, 1|ТЕЛ\.:|Справка о движении|Исх\. №|Гурьев Иван|"
    r"Адрес места|О продукте|Дата заключения|Номер договора|Номер лицевого|"
    r"Сумма доступного|Движение средств|Дата и время|^\s*операции\s|"
    r"АО «ТБанк»|БИК \d|Пополнения:|Расходы:|С уважением|Руководитель|^\s*\d{1,3}\s*$|^\s*$"
)
_TIME = re.compile(r"\b(\d{2}:\d{2})\b")


def _parse_tbank(text: str) -> list[Operation]:
    ops: list[Operation] = []
    cur: Operation | None = None
    for line in text.splitlines():
        m = _TB_ROW.match(line)
        if m:
            amount, income = _amount(m.group(3))
            cur = Operation(
                bank="Т-Банк", op_date=_date(m.group(1)), time="",
                amount=amount, income=income,
                description=_clean(m.group(4)), card=m.group(5) or "",
            )
            ops.append(cur)
            continue
        if _TB_NOISE.search(line):
            continue
        if cur is None:
            continue
        # строка-продолжение: времена + возможный хвост описания
        rest = line
        if not cur.time:
            times = _TIME.findall(line)
            if times:
                cur.time = times[0]
                rest = _TIME.sub("", line)
        chunks = [c for c in re.split(r"\s{2,}", rest.strip()) if c.strip()]
        if chunks:
            cur.description = _clean(cur.description + " " + " ".join(chunks))
    return ops


# ── Сбер («Выписка по платёжному счёту») ─────────────────────────────────────

_SB_ROW = re.compile(
    r"^\s*(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})\s{2,}(\S.*?)\s{2,}"
    r"(\+?[\d\s]+,\d{2})(?:\s{2,}[\d\s]+,\d{2})?\s*$"
)
_SB_CODE = re.compile(r"^\s*(\d{2}\.\d{2}\.\d{4})\s+(\d{5,7})\s+(\S.*)$")
_SB_NOISE = re.compile(
    r"www\.sberbank|ул\. Вавилова|Заказано в СберБанк|Выписка по платёжному|"
    r"За период|ИТОГО ПО ОПЕРАЦИЯМ|Владелец счёта|Остаток на|Номер счёта|"
    r"Карты, привязанные|МИР |Валюта|Дата открытия|Дата закрытия|Пополнение\s+[\d\s]+,|"
    r"Списание\s+[\d\s]+,|Расшифровка операций|ДАТА ОПЕРАЦИИ|Дата обработки|"
    r"и код авторизации|Продолжение на следующей|Для проверки подлинности|"
    r"Зайдите в приложение|Нажмите кнопку|Получите документ|Действителен|"
    r"до \d{2}\.\d{2}\.\d{4}|Предоставляя QR|Страница \d|Дата формирования|"
    r"^\s*[0-9A-F]{20,}\s*$|^\s*с \d{2}\.\d{2}\.\d{4} по|ПАО Сбербанк|"
    r"Денежные средства|В выписке отображаются|Срок обработки|"
    r"Согласно статье|электронной подписью|правоотношениях|Скачать электронный|"
    r"Проверить подпись|списания/зачисления|По курсу банка|Гурьев Иван|^\s*\*?\s*$|^\s*\d\s*$"
)


def _parse_sber(text: str) -> list[Operation]:
    ops: list[Operation] = []
    seen_codes: set[tuple] = set()
    cur: Operation | None = None
    cur_code: str | None = None
    for line in text.splitlines():
        m = _SB_ROW.match(line)
        if m and not _SB_NOISE.search(line):
            amount, income = _amount(m.group(4))
            cur = Operation(
                bank="Сбер", op_date=_date(m.group(1)), time=m.group(2),
                amount=amount, income=income, description="",
                sber_category=_clean(m.group(3)),
            )
            cur_code = None
            continue
        if cur is None:
            continue
        mc = _SB_CODE.match(line)
        if mc and not cur.description:
            cur_code = mc.group(2)
            cur.description = _clean(mc.group(3))
            key = (cur_code, cur.amount, cur.op_date, cur.income)
            if key in seen_codes:  # дубль строки на разрыве страницы
                cur = None
                continue
            seen_codes.add(key)
            _finish_sber(cur)
            ops.append(cur)
            continue
        if _SB_NOISE.search(line):
            continue
        # продолжение описания
        if cur in ops and line.strip():
            cur.description = _clean(cur.description + " " + line.strip())
            _finish_sber(cur)
    for op in ops:
        op.description = re.sub(r"\.?\s*Операция по (карте|счету).*$", "", op.description).strip()
    return ops


def _finish_sber(op: Operation) -> None:
    m = re.search(r"карте \*{4}(\d{4})", op.description)
    if m:
        op.card = m.group(1)


# ── Яндекс Банк («Выписка по договору») ──────────────────────────────────────

_YA_ROW = re.compile(
    r"^\s{0,4}(\S.*?)\s{2,}(\d{2}\.\d{2}\.\d{4})\s{2,}(\d{2}\.\d{2}\.\d{4})"
    r"(?:\s{2,}\*(\d{4}))?\s{2,}([+\-–−][\d\s]+,\d{2})\s*₽\s{2,}[+\-–−][\d\s]+,\d{2}\s*₽\s*$"
)
_YA_NOISE = re.compile(
    r"Исх\. №|Выписка по договору|АО «Яндекс Банк»|сообщает, что|№ К\d|"
    r"«Банковский счёт»|В рамках Договора|Номер счёта|Выписка по Договору|"
    r"Входящий остаток|Исходящий остаток|Всего расходных|Всего приходных|"
    r"Описание операции|Дата и время|^\s*операции\b|^\s*МСК\s*$|обработки|"
    r"Продолжение на|Страница \d|Гурьев Иван|Дата рождения|Паспорт|^\s*$"
)
_YA_TIME = re.compile(r"\bв (\d{2}:\d{2})\b")


def _parse_yandex(text: str) -> list[Operation]:
    ops: list[Operation] = []
    cur: Operation | None = None
    for line in text.splitlines():
        m = _YA_ROW.match(line)
        if m:
            amount, income = _amount(m.group(5))
            cur = Operation(
                bank="Яндекс", op_date=_date(m.group(2)), time="",
                amount=amount, income=income,
                description=_clean(m.group(1)), card=m.group(4) or "",
            )
            ops.append(cur)
            continue
        if _YA_NOISE.search(line):
            continue
        if cur is None:
            continue
        rest = line
        if not cur.time:
            mt = _YA_TIME.search(line)
            if mt:
                cur.time = mt.group(1)
                rest = _YA_TIME.sub("", line)
        chunks = [c for c in re.split(r"\s{2,}", rest.strip()) if c.strip()]
        if chunks:
            cur.description = _clean(cur.description + " " + " ".join(chunks))
    return ops


# ── Классификация ────────────────────────────────────────────────────────────

def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _is_self(op: Operation, own: dict[str, list[str]]) -> bool:
    d = op.description
    digits = _digits(d)
    if any(p and p in digits for p in own.get("own_phones", [])):
        return True
    if re.search(r"Перевод себе|Перевод между счетами одного клиента|"
                 r"Внутрибанковский перевод|Перевод с договора|Внесение наличных|"
                 r"Снятие наличных", d):
        return True
    # номер своего счёта/договора в описании (любой банк)
    accounts = own.get("own_accounts", [])
    m = re.search(r"(?:договор|счет|счёт)[уа]?\s*№?\s*(\d{6,})", d, re.I)
    if m:
        return m.group(1) in accounts
    if any(a and len(a) >= 6 and a in d for a in accounts):
        return True
    # «свои» банки — только в контексте перевода, иначе «Yandex» зацепит
    # оплату YANDEX*GO; голое имя банка («Яндекс») — это карточный перевод Сбера
    transfer_ctx = "перевод" in d.lower() or "перевод" in op.sber_category.lower()
    for b in own.get("own_banks", []):
        if not b:
            continue
        if d.strip().lower() == b.lower():
            return True
        if transfer_ctx and b.lower() in d.lower():
            return True
    if "Перевод для" in d and any(
        n and n.lower() in d.lower() for n in own.get("own_names", [])
    ):
        return True
    return False


_TRANSFER_RE = re.compile(
    r"Внешний перевод по номеру телефона|Внутренний перевод на договор|"
    r"Перевод в (T-Bank|Ozon Bank|ВТБ|Райффайзен)|Перевод для |Перевод СБП"
)


def merchant_key(op: Operation) -> str:
    """Ключ мерчанта для обучаемых правил."""
    d = op.description
    m = re.search(r"Оплата (?:в|услуг) (.+)$", d)
    if m:
        d = m.group(1)
    m = re.search(r"по номеру телефона (\+?[\d\s\-()]+)", op.description)
    if m:
        d = "+" + _digits(m.group(1))[-10:]
    d = re.sub(r"\b(SANKT-PETERBU|ST PETERSBURG|SANKT PETERBURG|MOSKVA|MOSCOW|"
               r"Moskva|Sankt-Peterbu|g\.|RUS)\b", "", d)
    return _clean(d)[:60]


def classify(ops: list[Operation], db_rules: list[tuple[str, str, str | None]],
             own: dict[str, list[str]] | None = None) -> None:
    """Проставляет kind/category/comment.

    db_rules — выученные правила (substring, category, comment),
    own — свои реквизиты из настроек (ключи SELF_KEYS).
    """
    own = own or {}
    for op in ops:
        if op.income:
            op.kind = "income"
            continue
        if _is_self(op, own):
            op.kind = "self"
            continue
        op.merchant_key = merchant_key(op)
        matched = False
        for pattern, cat, comment in db_rules:
            if pattern.upper() in op.description.upper() or pattern.upper() in op.merchant_key.upper():
                op.kind, op.category, op.comment = "expense", cat, comment
                matched = True
                break
        if matched:
            continue
        for pattern, cat, comment in MERCHANT_RULES:
            if re.search(pattern, op.description, re.I):
                op.kind, op.category, op.comment = "expense", cat, comment
                matched = True
                break
        if matched:
            continue
        # категория Сбера как надёжный fallback (рестораны, супермаркеты, транспорт...)
        if op.sber_category in SBER_HINTS:
            op.kind = "expense"
            op.category = SBER_HINTS[op.sber_category]
            op.comment = op.merchant_key or None
            continue
        # переводы в другие банки / незнакомые договоры / людям — спросить, себе или трата
        if _TRANSFER_RE.search(op.description):
            op.kind = "transfer"
        else:
            op.kind = "unknown"


# ── Подготовка к импорту (чистая логика, используется хендлером) ─────────────

def op_entry(op: Operation) -> dict:
    return {
        "hash": op.op_hash,
        "day": op.op_date.day,
        "month": op.op_date.month,
        "year": op.op_date.year,
        "date_str": op.op_date.strftime("%d.%m"),
        "amount": op.amount,
        "category": op.category,
        "comment": op.comment,
        "merchant": op.merchant_key or op.description[:60],
        "desc": op.description[:100],
        "kind": op.kind,
        "sber_hint": op.sber_category,
    }


def stage_ops(ops: list[Operation], known_hashes: set[str], import_after: date):
    """Раскладывает классифицированные операции:

    Returns: (staged, queue, mark, counts)
      staged — готовые траты (list[dict])
      queue  — вопросы пользователю: [{merchant, kind, learn, ops:[dict]}]
      mark   — хэши, помечаемые обработанными без вставки
      counts — {'self': n, 'old': n, 'dup': n}
    """
    staged: list[dict] = []
    ask: list[dict] = []
    mark: list[str] = []
    counts = {"self": 0, "old": 0, "dup": 0}

    for op in ops:
        if op.op_hash in known_hashes:
            counts["dup"] += 1
            continue
        if op.income or op.kind == "self":
            counts["self"] += 1
            mark.append(op.op_hash)
            continue
        if op.op_date <= import_after:
            counts["old"] += 1
            mark.append(op.op_hash)
            continue
        entry = op_entry(op)
        (staged if op.kind == "expense" else ask).append(entry)

    # незнакомые мерчанты группируем: один вопрос на мерчанта;
    # переводы — по одному, правило учим только для конкретного телефона
    queue: list[dict] = []
    groups: dict[str, dict] = {}
    for e in ask:
        if e["kind"] == "unknown":
            g = groups.setdefault(e["merchant"], {"merchant": e["merchant"], "kind": "unknown",
                                                  "learn": True, "ops": []})
            g["ops"].append(e)
        else:
            queue.append({"merchant": e["merchant"], "kind": "transfer",
                          "learn": e["merchant"].startswith("+"), "ops": [e]})
    return staged, list(groups.values()) + queue, mark, counts
