import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from config import config
from database import Database
from handlers import add, reports, categories

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

BOT_COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="add", description="Добавить трату"),
    BotCommand(command="report", description="Отчёты"),
    BotCommand(command="categories", description="Управление категориями"),
    BotCommand(command="last", description="Последние 10 трат"),
    BotCommand(command="undo", description="Отменить последнюю трату"),
]


async def main():
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    db = Database()
    await db.init()

    dp["db"] = db

    dp.include_router(categories.router)
    dp.include_router(add.router)
    dp.include_router(reports.router)

    await bot.set_my_commands(BOT_COMMANDS)

    logger.info("Bot started")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
