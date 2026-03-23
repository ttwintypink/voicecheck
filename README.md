# Discord Voice Tracker Bot (Linux)

Готовый Discord-бот под Linux для отслеживания активности в голосовых каналах.

## Что умеет

- логирует вход, выход и переход между голосовыми каналами;
- сохраняет статистику в SQLite;
- показывает, сколько времени пользователь провёл в каждом войсе;
- считает, с кем пользователь проводил время в одном голосовом канале;
- выводит общий топ активности и топ по конкретному каналу;
- экспортирует статистику пользователя в CSV за нужный период;
- имеет анти-AFK логику.

## Команды

- `/active @user 1d` — статистика по времени в войсах
- `/together @user 7d` — с кем пользователь сидел в войсе
- `/sessions @user` — последние сессии с пометками anti-AFK
- `/voicetop 30d` — общий топ по войсам
- `/channeltop Voice-3 7d` — топ по конкретному войсу
- `/exportstats @user 30d` — экспорт CSV за выбранный период

## Анти-AFK

Бот умеет не засчитывать или ограничивать время в таких кейсах:

- AFK-канал сервера
- соло-сидение в войсе дольше grace-периода
- self-deaf
- self-mute + self-deaf одновременно

### Как это работает сейчас

- `AFK_CHANNEL_IDS` — эти каналы не засчитываются
- `ANTI_AFK_SOLO_GRACE_MINUTES=10` — если человек один в войсе, засчитываются только первые 10 минут такой сессии
- `ANTI_AFK_EXCLUDE_SELF_DEAF=true` — если пользователь self-deaf, время не засчитывается
- `ANTI_AFK_EXCLUDE_MUTED_AND_DEAFENED=true` — если пользователь одновременно self-mute и self-deaf, время не засчитывается

## Структура

```text
voice_tracker_bot/
├─ bot/
│  ├─ cogs/
│  │  ├─ stats.py
│  │  └─ tracking.py
│  ├─ utils/
│  │  └─ timefmt.py
│  ├─ config.py
│  ├─ db.py
│  └─ main.py
├─ .env.example
├─ requirements.txt
└─ start.sh
```

## Настройка `.env`

```env
DISCORD_TOKEN=PASTE_YOUR_TOKEN_HERE
GUILD_ID=123456789012345678
COMMAND_SYNC_GUILD_ONLY=true
IGNORE_BOTS=true
EXCLUDED_CHANNEL_IDS=
AFK_CHANNEL_IDS=
TIMEZONE=Europe/Berlin
DATABASE_PATH=voice_activity.sqlite3
ANTI_AFK_ENABLED=true
ANTI_AFK_EXCLUDE_SOLO=true
ANTI_AFK_SOLO_GRACE_MINUTES=10
ANTI_AFK_EXCLUDE_SELF_DEAF=true
ANTI_AFK_EXCLUDE_MUTED_AND_DEAFENED=true
```

## Важно

- старая статистика задним числом не подтягивается;
- экспорт делает CSV по завершённым сессиям;
- anti-AFK логика применяется к завершённым сессиям и учитывает состояние пользователя на момент закрытия текущего сегмента сессии;
- при смене mute/deaf бот режет текущую сессию на сегменты, чтобы статистика была точнее.
