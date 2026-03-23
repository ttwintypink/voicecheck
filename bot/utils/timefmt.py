from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PeriodOption:
    key: str
    seconds: int | None
    label_ru: str


PERIODS: dict[str, PeriodOption] = {
    "1d": PeriodOption("1d", 86400, "1 день"),
    "7d": PeriodOption("7d", 7 * 86400, "7 дней"),
    "30d": PeriodOption("30d", 30 * 86400, "30 дней"),
    "all": PeriodOption("all", None, "всё время"),
}


def format_duration(total_seconds: int) -> str:
    seconds = max(0, int(total_seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, sec = divmod(rem, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}д")
    if hours:
        parts.append(f"{hours}ч")
    if minutes:
        parts.append(f"{minutes}м")
    if sec and not parts:
        parts.append(f"{sec}с")
    return " ".join(parts) if parts else "0м"
