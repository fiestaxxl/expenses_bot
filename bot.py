import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from config import config
from database import Database
from handlers import add, reports, categories
from handlers.scheduler import send_monthly_report

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

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        send_monthly_report,
        CronTrigger(day=1, hour=9, minute=0),
        args=[bot, db],
        id="monthly_report",
    )

    @dp.startup()
    async def on_startup():
        scheduler.start()
        logger.info("Scheduler started — monthly report on day=1 at 09:00")

    @dp.shutdown()
    async def on_shutdown():
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

    logger.info("Bot started")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
