# Expenses Bot

Личный Telegram-бот для учёта расходов. Добавляй траты одним сообщением, смотри отчёты с графиками, управляй категориями.

## Возможности

- **Быстрое добавление** — отправь сумму (например `500`), выбери категорию, день и необязательный комментарий
- **Отчёты с графиками** — по категориям, доли (pie), по дням, тренд по месяцам, всё время
- **Управление категориями** — добавить, переименовать, удалить прямо из чата
- **Постоянное меню** — три кнопки внизу экрана: ➕ Добавить трату / 📊 Отчёты / ⚙️ Категории
- **Отмена последней траты** — команда `/undo`
- **Приватность** — бот отвечает только владельцу (`OWNER_ID`)

## Структура

```
expenses-bot/
├── bot.py                 # точка входа, регистрация роутеров и команд
├── config.py              # конфигурация из .env
├── database.py            # SQLite через aiosqlite
├── charts.py              # графики Plotly → PNG
├── keyboards.py           # главная Reply-клавиатура
├── handlers/
│   ├── add.py             # флоу добавления траты (FSM)
│   ├── categories.py      # управление категориями
│   └── reports.py        # отчёты и /start
├── .env.example
├── requirements.txt
└── expenses-bot.service   # systemd unit для деплоя
```

## Установка

**Требования:** Python 3.11+

```bash
git clone <repo>
cd expenses-bot

python -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

Скопируй `.env.example` в `.env` и заполни:

```bash
cp .env.example .env
```

```env
BOT_TOKEN=your_token_here   # токен от @BotFather
OWNER_ID=your_telegram_id   # узнать у @userinfobot
DB_PATH=expenses.db         # путь к базе (необязательно менять)
```

## Запуск

```bash
source venv/bin/activate
python bot.py
```

## Деплой через systemd

Скопируй `expenses-bot.service` в `/etc/systemd/system/`, замени `YOUR_USER` на имя пользователя сервера:

```bash
sudo cp expenses-bot.service /etc/systemd/system/
sudo nano /etc/systemd/system/expenses-bot.service  # поменяй YOUR_USER

sudo systemctl daemon-reload
sudo systemctl enable expenses-bot
sudo systemctl start expenses-bot

sudo systemctl status expenses-bot
sudo journalctl -u expenses-bot -f  # логи
```

## Использование

| Действие | Способ |
|---|---|
| Добавить трату | Кнопка **➕ Добавить трату** или отправить сумму напрямую: `500` |
| Отчёты | Кнопка **📊 Отчёты** или `/report` |
| Категории | Кнопка **⚙️ Категории** или `/categories` |
| Последние 10 трат | `/last` |
| Отменить последнюю трату | `/undo` |

### Добавление траты

```
Пользователь: 1500
Бот: Выбери категорию: [Еда] [Транспорт] [Жильё] ...
Пользователь: → Еда
Бот: День расхода: [Сегодня (1)] [Другой день]
Пользователь: → Сегодня
Бот: Комментарий (необязательно): [Без комментария]
Пользователь: кофе и сэндвич
Бот: ✅ 1 500 ₽ — Еда, 1.05.2026 · кофе и сэндвич
```

### Категории по умолчанию

Еда, Транспорт, Жильё, Здоровье, Развлечения, Одежда, Прочее

Категории можно переименовывать — все существующие траты обновятся автоматически.

## Зависимости

| Пакет | Версия | Назначение |
|---|---|---|
| aiogram | 3.7.0 | Telegram Bot API |
| aiosqlite | 0.20.0 | Async SQLite |
| plotly | 5.22.0 | Графики |
| kaleido | 0.2.1 | Рендер графиков в PNG |
| python-dotenv | 1.0.1 | Загрузка `.env` |
