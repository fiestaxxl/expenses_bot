from calendar import monthrange
from aiogram import Bot
from aiogram.types import BufferedInputFile
from database import Database
from config import config
import charts

MONTH_NAMES = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}


async def send_monthly_report(bot: Bot, db: Database):
    today = config.today()
    if today.month == 1:
        month, year = 12, today.year - 1
    else:
        month, year = today.month - 1, today.year

    total = await db.get_monthly_total(month, year)
    if total == 0:
        return

    by_cat = await db.get_monthly_by_category(month, year)
    daily = await db.get_daily_expenses(month, year)
    trend = await db.get_monthly_totals_by_year(year)

    days_in_month = monthrange(year, month)[1]
    avg_per_day = total / days_in_month
    active_days = len({r[0] for r in daily})

    lines = [f"📅 <b>Итоги {MONTH_NAMES[month]} {year}</b>\n"]
    lines.append(f"Всего потрачено: <b>{total:,.0f} ₽</b>")
    lines.append(f"Средний расход в день: <b>{avg_per_day:,.0f} ₽</b>  ({active_days} из {days_in_month} дней)")
    lines.append("")
    lines.append("<b>По категориям:</b>")
    for cat, amount in by_cat:
        pct = amount / total * 100
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        lines.append(f"• {cat}: <b>{amount:,.0f} ₽</b>  {pct:.1f}%")

    if by_cat:
        top_cat, top_amount = by_cat[0]
        lines.append(f"\nБольше всего потрачено на <b>{top_cat}</b> — {top_amount:,.0f} ₽ ({top_amount/total*100:.1f}%)")

    await bot.send_message(config.OWNER_ID, "\n".join(lines), parse_mode="HTML")

    if by_cat:
        img = charts.chart_monthly_by_category(by_cat, month, year)
        await bot.send_photo(config.OWNER_ID, BufferedInputFile(img, "bar.png"),
                             caption=f"📊 По категориям — {MONTH_NAMES[month]} {year}")

        img = charts.chart_monthly_pie(by_cat, month, year)
        await bot.send_photo(config.OWNER_ID, BufferedInputFile(img, "pie.png"),
                             caption=f"🥧 Доли категорий — {MONTH_NAMES[month]} {year}")

    if daily:
        img = charts.chart_daily(daily, month, year)
        await bot.send_photo(config.OWNER_ID, BufferedInputFile(img, "daily.png"),
                             caption=f"📅 По дням — {MONTH_NAMES[month]} {year}")

    if trend:
        img = charts.chart_yearly_trend(trend, year)
        await bot.send_photo(config.OWNER_ID, BufferedInputFile(img, "trend.png"),
                             caption=f"📈 Тренд за {year} год")
