from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "y", "yes", "on", "true"}


def _parse_int_set(value: str | None) -> set[int]:
    result: set[int] = set()
    for part in (value or "").split(","):
        part = part.strip()
        if part:
            result.add(int(part))
    return result


@dataclass(frozen=True, slots=True)
class Settings:
    token: str
    guild_id: int | None
    command_sync_guild_only: bool
    ignore_bots: bool
    excluded_channel_ids: set[int]
    afk_channel_ids: set[int]
    timezone: str
    database_path: str
    anti_afk_enabled: bool
    anti_afk_exclude_solo: bool
    anti_afk_solo_grace_minutes: int
    anti_afk_exclude_self_deaf: bool
    anti_afk_exclude_muted_and_deafened: bool


def load_settings() -> Settings:
    load_dotenv()

    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_TOKEN is empty. Put the bot token in .env or environment variables.")

    guild_id_raw = os.getenv("GUILD_ID", "").strip()
    guild_id = int(guild_id_raw) if guild_id_raw else None

    return Settings(
        token=token,
        guild_id=guild_id,
        command_sync_guild_only=_parse_bool(os.getenv("COMMAND_SYNC_GUILD_ONLY"), True),
        ignore_bots=_parse_bool(os.getenv("IGNORE_BOTS"), True),
        excluded_channel_ids=_parse_int_set(os.getenv("EXCLUDED_CHANNEL_IDS")),
        afk_channel_ids=_parse_int_set(os.getenv("AFK_CHANNEL_IDS")),
        timezone=os.getenv("TIMEZONE", "UTC").strip() or "UTC",
        database_path=os.getenv("DATABASE_PATH", "voice_activity.sqlite3").strip() or "voice_activity.sqlite3",
        anti_afk_enabled=_parse_bool(os.getenv("ANTI_AFK_ENABLED"), True),
        anti_afk_exclude_solo=_parse_bool(os.getenv("ANTI_AFK_EXCLUDE_SOLO"), True),
        anti_afk_solo_grace_minutes=max(0, int(os.getenv("ANTI_AFK_SOLO_GRACE_MINUTES", "10").strip() or "10")),
        anti_afk_exclude_self_deaf=_parse_bool(os.getenv("ANTI_AFK_EXCLUDE_SELF_DEAF"), True),
        anti_afk_exclude_muted_and_deafened=_parse_bool(os.getenv("ANTI_AFK_EXCLUDE_MUTED_AND_DEAFENED"), True),
    )
