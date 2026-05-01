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
