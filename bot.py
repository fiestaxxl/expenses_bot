import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, MenuButtonCommands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from config import config
from database import Database
from handlers import add, reports, categories, import_statement, expense_list, settings, budgets
from handlers.scheduler import send_monthly_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

BOT_COMMANDS = [
    BotCommand(command="start", description="🏠 Главное меню и справка"),
    BotCommand(command="add", description="➕ Добавить трату"),
    BotCommand(command="list", description="📋 Траты за период — посмотреть и поправить"),
    BotCommand(command="limits", description="💰 Лимиты месяца — остатки по категориям"),
    BotCommand(command="report", description="📊 Отчёты и графики"),
    BotCommand(command="last", description="🕐 Последние 10 трат"),
    BotCommand(command="categories", description="⚙️ Категории: добавить/переименовать"),
    BotCommand(command="imports", description="📄 Импорты выписок — история и отмена"),
    BotCommand(command="settings", description="🔧 Настройки импорта выписок"),
    BotCommand(command="undo", description="↩️ Отменить последнюю трату"),
    BotCommand(command="cancel", description="✖️ Прервать текущее действие"),
]


async def main():
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    db = Database()
    await db.init()

    dp["db"] = db

    dp.include_router(import_statement.router)
    dp.include_router(expense_list.router)
    dp.include_router(budgets.router)
    dp.include_router(settings.router)
    dp.include_router(categories.router)
    dp.include_router(add.router)
    dp.include_router(reports.router)

    await bot.set_my_commands(BOT_COMMANDS)
    # постоянная кнопка «Меню» слева от поля ввода — со всеми командами
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())

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
