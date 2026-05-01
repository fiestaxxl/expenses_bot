import os
from dataclasses import dataclass, field
from datetime import date
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    BOT_TOKEN: str
    OWNER_ID: int
    DB_PATH: str = "expenses.db"
    TIMEZONE: str = "Europe/Moscow"

    def __post_init__(self):
        if not self.BOT_TOKEN:
            raise ValueError("BOT_TOKEN is not set in .env")
        if not self.OWNER_ID:
            raise ValueError("OWNER_ID is not set in .env")

    def today(self) -> date:
        """Current date in the configured timezone."""
        from datetime import datetime
        return datetime.now(tz=ZoneInfo(self.TIMEZONE)).date()


config = Config(
    BOT_TOKEN=os.getenv("BOT_TOKEN", ""),
    OWNER_ID=int(os.getenv("OWNER_ID", "0")),
    DB_PATH=os.getenv("DB_PATH", "expenses.db"),
    TIMEZONE=os.getenv("TIMEZONE", "Europe/Moscow"),
)
