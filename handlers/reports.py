import io
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.filters import Command
from datetime import date
from database import Database
from config import config
from keyboards import MAIN_MENU
import charts

router = Router()
router.message.filter(F.from_user.id == config.OWNER_ID)

MONTH_NAMES = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}


def report_keyboard():
    today = date.today()
    m, y = today.month, today.year
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 По категориям", callback_data=f"rep:bar:{m}:{y}"),
            InlineKeyboardButton(text="🥧 Доли", callback_data=f"rep:pie:{m}:{y}"),
        ],
        [
            InlineKeyboardButton(text="📅 По дням", callback_data=f"rep:daily:{m}:{y}"),
            InlineKeyboardButton(text="📈 Тренд за год", callback_data=f"rep:trend:{m}:{y}"),
        ],
        [
            InlineKeyboardButton(text="🌍 Всё время", callback_data="rep:alltime:0:0"),
            InlineKeyboardButton(text="📋 Последние 10", callback_data="rep:last:0:0"),
        ],
    ])


@router.message(Command("start"))
async def cmd_start(message: Message, db: Database):
    today = date.today()
    total = await db.get_monthly_total(today.month, today.year)
    await message.answer(
        f"👋 Привет! Я бот для учёта расходов.\n\n"
        f"<b>Как добавить трату:</b>\n"
        f"• Нажми кнопку <b>➕ Добавить трату</b> внизу экрана\n"
        f"• Или просто отправь сумму — например: <code>500</code> или <code>1990.50</code>\n\n"
        f"Дальше я спрошу категорию, день и комментарий.\n\n"
        f"<b>Текущий месяц ({MONTH_NAMES[today.month]}):</b> <b>{total:,.0f} ₽</b>",
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )


@router.message(Command("report"))
@router.message(F.text == "📊 Отчёты")
async def cmd_report(message: Message, db: Database):
    today = date.today()
    total = await db.get_monthly_total(today.month, today.year)
    await message.answer(
        f"<b>Отчёты</b>\n\n"
        f"Текущий месяц: <b>{total:,.0f} ₽</b> ({MONTH_NAMES[today.month]} {today.year})\n\n"
        f"Что показать?",
        parse_mode="HTML",
        reply_markup=report_keyboard(),
    )


@router.message(Command("last"))
async def cmd_last(message: Message, db: Database):
    await send_last(message, db)


async def send_last(message: Message, db: Database):
    rows = await db.get_last_expenses(10)
    if not rows:
        await message.answer("Трат пока нет.")
        return
    lines = []
    for amount, category, day, month, year, comment in rows:
        comment_str = f" — {comment}" if comment else ""
        lines.append(f"• {day:02d}.{month:02d}.{year}  <b>{amount:,.0f} ₽</b>  {category}{comment_str}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.callback_query(F.data.startswith("rep:"))
async def handle_report(callback: CallbackQuery, db: Database):
    _, kind, m_str, y_str = callback.data.split(":")
    month, year = int(m_str), int(y_str)
    today = date.today()

    await callback.answer("Строю график…")
    await callback.message.answer("⏳ Генерирую...")

    try:
        if kind == "bar":
            data = await db.get_monthly_by_category(month, year)
            if not data:
                await callback.message.answer("Нет данных за этот месяц.")
                return
            img = charts.chart_monthly_by_category(data, month, year)
            total = await db.get_monthly_total(month, year)
            caption = f"📊 {MONTH_NAMES[month]} {year} — итого <b>{total:,.0f} ₽</b>"

        elif kind == "pie":
            data = await db.get_monthly_by_category(month, year)
            if not data:
                await callback.message.answer("Нет данных за этот месяц.")
                return
            img = charts.chart_monthly_pie(data, month, year)
            caption = f"🥧 Доли категорий — {MONTH_NAMES[month]} {year}"

        elif kind == "daily":
            data = await db.get_daily_expenses(month, year)
            if not data:
                await callback.message.answer("Нет данных за этот месяц.")
                return
            img = charts.chart_daily(data, month, year)
            caption = f"📅 Расходы по дням — {MONTH_NAMES[month]} {year}"

        elif kind == "trend":
            data = await db.get_monthly_totals_by_year(today.year)
            if not data:
                await callback.message.answer("Нет данных за этот год.")
                return
            img = charts.chart_yearly_trend(data, today.year)
            caption = f"📈 Тренд по месяцам — {today.year}"

        elif kind == "alltime":
            data = await db.get_all_time_by_category()
            if not data:
                await callback.message.answer("Нет данных.")
                return
            img = charts.chart_alltime_by_category(data)
            caption = "🌍 Все расходы по категориям"

        elif kind == "last":
            await send_last(callback.message, db)
            return

        else:
            return

        await callback.message.answer_photo(
            BufferedInputFile(img, filename="chart.png"),
            caption=caption,
            parse_mode="HTML"
        )

    except Exception as e:
        await callback.message.answer(f"Ошибка при генерации графика: {e}")
