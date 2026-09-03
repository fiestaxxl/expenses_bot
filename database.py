import aiosqlite
from datetime import date
from config import config


class Database:
    def __init__(self):
        self.db_path = config.DB_PATH

    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    amount REAL NOT NULL,
                    category TEXT NOT NULL,
                    day INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    year INTEGER NOT NULL,
                    comment TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS merchant_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern TEXT UNIQUE NOT NULL,
                    category TEXT NOT NULL,
                    comment TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS imports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bank TEXT,
                    period TEXT,
                    filename TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS imported_ops (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hash TEXT UNIQUE NOT NULL,
                    expense_id INTEGER,
                    import_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # миграция для баз, созданных до появления import_id
            cursor = await db.execute("PRAGMA table_info(imported_ops)")
            cols = [r[1] for r in await cursor.fetchall()]
            if "import_id" not in cols:
                await db.execute("ALTER TABLE imported_ops ADD COLUMN import_id INTEGER")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS budgets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    month INTEGER NOT NULL,
                    year INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    UNIQUE(category, month, year)
                )
            """)
            # миграция: own_contracts (только Т-Банк) -> own_accounts (все банки)
            await db.execute("""
                INSERT OR IGNORE INTO settings (key, value)
                SELECT 'own_accounts', value FROM settings WHERE key = 'own_contracts'
            """)
            await db.execute("DELETE FROM settings WHERE key = 'own_contracts'")
            # seed default categories if empty
            cursor = await db.execute("SELECT COUNT(*) FROM categories")
            count = (await cursor.fetchone())[0]
            if count == 0:
                defaults = ["Еда", "Транспорт", "Жильё", "Здоровье", "Развлечения", "Одежда", "Прочее"]
                await db.executemany(
                    "INSERT OR IGNORE INTO categories (name) VALUES (?)",
                    [(c,) for c in defaults]
                )
            await db.commit()

    # ── Categories ──────────────────────────────────────────────────────────

    async def get_categories(self) -> list[str]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT name FROM categories ORDER BY name")
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

    async def add_category(self, name: str) -> bool:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("INSERT INTO categories (name) VALUES (?)", (name,))
                await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def rename_category(self, old_name: str, new_name: str) -> bool:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("UPDATE categories SET name = ? WHERE name = ?", (new_name, old_name))
                await db.execute("UPDATE expenses SET category = ? WHERE category = ?", (new_name, old_name))
                await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def delete_category(self, name: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM categories WHERE name = ?", (name,))
            await db.commit()
            return cursor.rowcount > 0

    # ── Expenses ─────────────────────────────────────────────────────────────

    async def add_expense(self, amount: float, category: str, day: int,
                          month: int, year: int, comment: str = None):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO expenses (amount, category, day, month, year, comment) VALUES (?,?,?,?,?,?)",
                (amount, category, day, month, year, comment)
            )
            await db.commit()

    async def get_monthly_by_category(self, month: int, year: int) -> list[tuple]:
        """Returns [(category, total), ...] sorted by total desc."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT category, SUM(amount) as total
                FROM expenses
                WHERE month = ? AND year = ?
                GROUP BY category
                ORDER BY total DESC
            """, (month, year))
            return await cursor.fetchall()

    async def get_monthly_total(self, month: int, year: int) -> float:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE month = ? AND year = ?",
                (month, year)
            )
            return (await cursor.fetchone())[0]

    async def get_daily_expenses(self, month: int, year: int) -> list[tuple]:
        """Returns [(day, category, amount), ...] for a given month."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT day, category, SUM(amount)
                FROM expenses
                WHERE month = ? AND year = ?
                GROUP BY day, category
                ORDER BY day, category
            """, (month, year))
            return await cursor.fetchall()

    async def get_monthly_totals_by_year(self, year: int) -> list[tuple]:
        """Returns [(month, total), ...] for trend chart."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT month, SUM(amount)
                FROM expenses
                WHERE year = ?
                GROUP BY month
                ORDER BY month
            """, (year,))
            return await cursor.fetchall()

    async def get_all_time_by_category(self) -> list[tuple]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT category, SUM(amount)
                FROM expenses
                GROUP BY category
                ORDER BY SUM(amount) DESC
            """)
            return await cursor.fetchall()

    async def get_last_expenses(self, limit: int = 10) -> list[tuple]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT amount, category, day, month, year, comment
                FROM expenses
                ORDER BY year DESC, month DESC, day DESC, created_at DESC
                LIMIT ?
            """, (limit,))
            return await cursor.fetchall()

    # ── Настройки ────────────────────────────────────────────────────────────

    async def get_setting(self, key: str, default: str = "") -> str:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = await cursor.fetchone()
            return row[0] if row else default

    async def set_setting(self, key: str, value: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            await db.commit()

    async def get_setting_list(self, key: str) -> list[str]:
        """Настройка-список: значения через запятую."""
        raw = await self.get_setting(key)
        return [x.strip() for x in raw.split(",") if x.strip()]

    # ── Редактирование трат ──────────────────────────────────────────────────

    async def get_expenses_between(self, d1, d2) -> list[tuple]:
        """[(id, amount, category, day, month, year, comment), ...] за период."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT id, amount, category, day, month, year, comment
                FROM expenses
                WHERE year*10000 + month*100 + day BETWEEN ? AND ?
                ORDER BY year, month, day, id
            """, (d1.year * 10000 + d1.month * 100 + d1.day,
                  d2.year * 10000 + d2.month * 100 + d2.day))
            return await cursor.fetchall()

    async def update_expense(self, expense_id: int, *, amount: float = None,
                             category: str = None, comment: str = None,
                             day: int = None, month: int = None, year: int = None) -> bool:
        sets, params = [], []
        if amount is not None:
            sets.append("amount = ?"); params.append(amount)
        if category is not None:
            sets.append("category = ?"); params.append(category)
        if comment is not None:
            sets.append("comment = ?"); params.append(comment or None)
        for name, value in (("day", day), ("month", month), ("year", year)):
            if value is not None:
                sets.append(f"{name} = ?"); params.append(value)
        if not sets:
            return False
        params.append(expense_id)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                f"UPDATE expenses SET {', '.join(sets)} WHERE id = ?", params
            )
            await db.commit()
            return cursor.rowcount > 0

    async def delete_expense(self, expense_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
            await db.commit()
            return cursor.rowcount > 0

    # ── Лимиты (бюджеты по категориям на месяц) ─────────────────────────────
    # category = имя категории или спец-строка "Резерв"

    async def get_budgets(self, month: int, year: int) -> dict[str, float]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT category, amount FROM budgets WHERE month = ? AND year = ?",
                (month, year),
            )
            return {r[0]: r[1] for r in await cursor.fetchall()}

    async def set_budget(self, category: str, month: int, year: int, amount: float):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO budgets (category, month, year, amount) VALUES (?,?,?,?) "
                "ON CONFLICT(category, month, year) DO UPDATE SET amount=excluded.amount",
                (category, month, year, amount),
            )
            await db.commit()

    async def delete_budget(self, category: str, month: int, year: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM budgets WHERE category = ? AND month = ? AND year = ?",
                (category, month, year),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def copy_budgets(self, from_month: int, from_year: int,
                           to_month: int, to_year: int) -> int:
        """Переносит лимиты месяца (существующие в целевом месяце не трогает)."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                INSERT OR IGNORE INTO budgets (category, month, year, amount)
                SELECT category, ?, ?, amount FROM budgets
                WHERE month = ? AND year = ?
            """, (to_month, to_year, from_month, from_year))
            await db.commit()
            return cursor.rowcount

    async def get_spent(self, category: str, month: int, year: int) -> float:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM expenses "
                "WHERE category = ? AND month = ? AND year = ?",
                (category, month, year),
            )
            return (await cursor.fetchone())[0]

    # ── Импорт выписок ───────────────────────────────────────────────────────

    async def get_merchant_rules(self) -> list[tuple]:
        """Returns [(pattern, category, comment), ...] выученных правил."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT pattern, category, comment FROM merchant_rules"
            )
            return await cursor.fetchall()

    async def add_merchant_rule(self, pattern: str, category: str, comment: str = None):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO merchant_rules (pattern, category, comment) VALUES (?,?,?) "
                "ON CONFLICT(pattern) DO UPDATE SET category=excluded.category, "
                "comment=excluded.comment",
                (pattern, category, comment),
            )
            await db.commit()

    async def delete_merchant_rule(self, pattern: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM merchant_rules WHERE pattern = ?", (pattern,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def known_hashes(self, hashes: list[str]) -> set[str]:
        """Какие из хэшей уже импортированы."""
        if not hashes:
            return set()
        async with aiosqlite.connect(self.db_path) as db:
            placeholders = ",".join("?" * len(hashes))
            cursor = await db.execute(
                f"SELECT hash FROM imported_ops WHERE hash IN ({placeholders})", hashes
            )
            return {r[0] for r in await cursor.fetchall()}

    async def create_import(self, bank: str, period: str, filename: str) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "INSERT INTO imports (bank, period, filename) VALUES (?,?,?)",
                (bank, period, filename),
            )
            await db.commit()
            return cursor.lastrowid

    async def import_expenses(self, items: list[dict], import_id: int) -> int:
        """Вставляет траты и помечает хэши операций одной транзакцией.

        items: [{amount, category, day, month, year, comment, hash}, ...]
        Возвращает число добавленных (уже известные хэши пропускаются).
        """
        added = 0
        async with aiosqlite.connect(self.db_path) as db:
            for it in items:
                cursor = await db.execute(
                    "SELECT 1 FROM imported_ops WHERE hash = ?", (it["hash"],)
                )
                if await cursor.fetchone():
                    continue
                cursor = await db.execute(
                    "INSERT INTO expenses (amount, category, day, month, year, comment) "
                    "VALUES (?,?,?,?,?,?)",
                    (it["amount"], it["category"], it["day"], it["month"],
                     it["year"], it["comment"]),
                )
                await db.execute(
                    "INSERT INTO imported_ops (hash, expense_id, import_id) VALUES (?,?,?)",
                    (it["hash"], cursor.lastrowid, import_id),
                )
                added += 1
            await db.commit()
        return added

    async def mark_imported(self, hashes: list[str], import_id: int = None):
        """Пометить операции как обработанные без создания трат (пропуски)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany(
                "INSERT OR IGNORE INTO imported_ops (hash, import_id) VALUES (?,?)",
                [(h, import_id) for h in hashes],
            )
            await db.commit()

    async def list_imports(self, limit: int = 10) -> list[tuple]:
        """[(id, bank, period, created_at, n_expenses, total), ...] новые сверху."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT i.id, i.bank, i.period, i.created_at,
                       COUNT(e.id), COALESCE(SUM(e.amount), 0)
                FROM imports i
                LEFT JOIN imported_ops io ON io.import_id = i.id AND io.expense_id IS NOT NULL
                LEFT JOIN expenses e ON e.id = io.expense_id
                GROUP BY i.id
                ORDER BY i.id DESC
                LIMIT ?
            """, (limit,))
            return await cursor.fetchall()

    async def undo_import(self, import_id: int) -> int:
        """Удаляет все траты импорта и его следы. Возвращает число удалённых трат."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                DELETE FROM expenses WHERE id IN (
                    SELECT expense_id FROM imported_ops
                    WHERE import_id = ? AND expense_id IS NOT NULL
                )
            """, (import_id,))
            deleted = cursor.rowcount
            await db.execute("DELETE FROM imported_ops WHERE import_id = ?", (import_id,))
            await db.execute("DELETE FROM imports WHERE id = ?", (import_id,))
            await db.commit()
            return deleted

    async def delete_last_expense(self) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT id FROM expenses ORDER BY created_at DESC LIMIT 1
            """)
            row = await cursor.fetchone()
            if not row:
                return False
            await db.execute("DELETE FROM expenses WHERE id = ?", (row[0],))
            await db.commit()
            return True
