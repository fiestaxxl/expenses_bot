from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Добавить трату")],
        [KeyboardButton(text="📊 Отчёты"), KeyboardButton(text="⚙️ Категории")],
    ],
    resize_keyboard=True,
    persistent=True,
)
