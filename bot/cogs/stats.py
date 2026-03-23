from __future__ import annotations

import io
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.db import Database
from bot.utils.timefmt import PERIODS, format_duration
from bot.cogs.tracking import VoiceTrackingCog


class StatsCog(commands.Cog):
    def __init__(self, bot: commands.Bot, db: Database, settings: Settings) -> None:
        self.bot = bot
        self.db = db
        self.settings = settings
        self.tz = ZoneInfo(settings.timezone)

    def _after_ts(self, period_key: str) -> int | None:
        period = PERIODS[period_key]
        return None if period.seconds is None else int(time.time()) - period.seconds

    def _resolve_user(self, guild: discord.Guild, user_id: int) -> str:
        member = guild.get_member(user_id)
        return member.mention if member is not None else f"<@{user_id}>"

    def _live_extra_for_session(self, session, after_ts: int | None, include_afk: bool) -> int:
        if session is None:
            return 0
        if not include_afk and session.is_afk:
            return 0
        start_ts = max(session.joined_ts, after_ts) if after_ts is not None else session.joined_ts
        return max(0, int(time.time()) - start_ts)

    def _append_live_time(
        self,
        tracking_cog: VoiceTrackingCog | None,
        guild: discord.Guild,
        user_id: int,
        after_ts: int | None,
        totals: dict[int, int],
        total_sum: int,
        include_afk: bool,
    ) -> tuple[dict[int, int], int]:
        if tracking_cog is None:
            return totals, total_sum
        session = tracking_cog.live_sessions.get((guild.id, user_id))
        extra = self._live_extra_for_session(session, after_ts, include_afk)
        if extra <= 0 or session is None:
            return totals, total_sum
        totals = dict(totals)
        totals[session.channel_id] = totals.get(session.channel_id, 0) + extra
        return totals, total_sum + extra

    def _format_recent_time(self, unix_ts: int) -> str:
        dt = datetime.fromtimestamp(unix_ts, tz=self.tz)
        return dt.strftime("%d.%m %H:%M")

    async def period_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        current = current.lower().strip()
        return [
            app_commands.Choice(name=f"{key} — {opt.label_ru}", value=key)
            for key, opt in PERIODS.items()
            if current in key or current in opt.label_ru.lower()
        ][:25]

    async def channel_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        guild = interaction.guild
        if guild is None:
            return []
        current = current.lower().strip()
        choices: list[app_commands.Choice[str]] = []
        for channel in sorted(guild.voice_channels, key=lambda c: (c.position, c.id)):
            if channel.id in self.settings.excluded_channel_ids:
                continue
            if current and current not in channel.name.lower():
                continue
            choices.append(app_commands.Choice(name=channel.name, value=str(channel.id)))
        return choices[:25]

    @app_commands.command(name="active", description="Показать активность пользователя в войсах")
    @app_commands.describe(user="Пользователь", period="1d / 7d / 30d / all", include_afk="Учитывать AFK")
    async def active(self, interaction: discord.Interaction, user: discord.Member, period: str, include_afk: bool = False) -> None:
        await interaction.response.defer()
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("Эта команда работает только на сервере.", ephemeral=True)
            return

        after_ts = self._after_ts(period)
        totals = await self.db.get_channel_totals(guild.id, user.id, after_ts, include_afk)
        total_sum = await self.db.get_total_time(guild.id, user.id, after_ts, include_afk)
        session_count = await self.db.get_session_count(guild.id, user.id, after_ts, include_afk)

        tracking_cog = self.bot.get_cog("VoiceTrackingCog")
        if isinstance(tracking_cog, VoiceTrackingCog):
            totals, total_sum = self._append_live_time(tracking_cog, guild, user.id, after_ts, totals, total_sum, include_afk)

        channels = [c for c in sorted(guild.voice_channels, key=lambda c: (c.position, c.id)) if c.id not in self.settings.excluded_channel_ids]
        if not include_afk:
            channels = [c for c in channels if c.id not in self.settings.afk_channel_ids]
        channel_lines = [f"📞 • **{c.name}** — `{format_duration(totals.get(c.id, 0))}`" for c in channels] or ["Нет подходящих голосовых каналов."]

        best_channel = guild.get_channel(max(totals, key=totals.get)) if totals else None
        embed = discord.Embed(
            title=f"Активность в войсах: {user.display_name}",
            description=f"Период: **{PERIODS[period].label_ru}**",
            color=discord.Color.purple(),
        )
        embed.add_field(name="По каналам", value="\n".join(channel_lines[:25]), inline=False)
        summary = [f"**Итоговое время:** `{format_duration(total_sum)}`", f"**Количество сессий:** `{session_count}`"]
        if best_channel is not None:
            summary.append(f"**Топ-канал:** {best_channel.mention} — `{format_duration(totals.get(best_channel.id, 0))}`")
        embed.add_field(name="Итог", value="\n".join(summary), inline=False)
        embed.set_thumbnail(url=user.display_avatar.url)
        await interaction.followup.send(embed=embed)

    @active.autocomplete("period")
    async def active_period_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await self.period_autocomplete(interaction, current)

    @app_commands.command(name="together", description="Показать, с кем пользователь сидел в войсе")
    @app_commands.describe(user="Пользователь", period="1d / 7d / 30d / all", include_afk="Учитывать AFK")
    async def together(self, interaction: discord.Interaction, user: discord.Member, period: str, include_afk: bool = False) -> None:
        await interaction.response.defer()
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("Эта команда работает только на сервере.", ephemeral=True)
            return
        rows = await self.db.get_together_totals(guild.id, user.id, self._after_ts(period), include_afk)
        lines = [f"`#{idx}` {self._resolve_user(guild, other_user_id)} — `{format_duration(seconds)}`" for idx, (other_user_id, seconds) in enumerate(rows[:15], start=1)]
        embed = discord.Embed(
            title=f"С кем сидел {user.display_name}",
            description=f"Период: **{PERIODS[period].label_ru}**",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Совместное время", value="\n".join(lines) if lines else "Нет данных.", inline=False)
        await interaction.followup.send(embed=embed)

    @together.autocomplete("period")
    async def together_period_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await self.period_autocomplete(interaction, current)

    @app_commands.command(name="sessions", description="Последние голосовые сессии пользователя")
    async def sessions(self, interaction: discord.Interaction, user: discord.Member) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("Эта команда работает только на сервере.", ephemeral=True)
            return
        rows = await self.db.get_recent_sessions(guild.id, user.id, limit=10)
        lines: list[str] = []
        for row in rows:
            afk_mark = " [AFK]" if int(row["is_afk"]) else ""
            extra_notes: list[str] = []
            if int(row["solo_seconds"] or 0):
                extra_notes.append(f"solo: {format_duration(int(row['solo_seconds']))}")
            if int(row["self_deaf_seconds"] or 0):
                extra_notes.append(f"self-deaf: {format_duration(int(row['self_deaf_seconds']))}")
            if int(row["muted_deafened_seconds"] or 0):
                extra_notes.append(f"mute+deaf: {format_duration(int(row['muted_deafened_seconds']))}")
            note = f" | {'; '.join(extra_notes)}" if extra_notes else ""
            lines.append(
                f"**{row['channel_name']}**{afk_mark}: `{self._format_recent_time(int(row['join_ts']))}` → `{self._format_recent_time(int(row['leave_ts']))}` | total `{format_duration(int(row['duration_seconds']))}` | counted `{format_duration(int(row['counted_seconds']))}`{note}"
            )
        embed = discord.Embed(title=f"Последние голосовые сессии — {user.display_name}", color=discord.Color.dark_purple())
        embed.description = "\n".join(lines) if lines else "Сессий пока нет."
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="voicetop", description="Топ по онлайну в войсах")
    @app_commands.describe(period="1d / 7d / 30d / all", include_afk="Учитывать AFK")
    async def voicetop(self, interaction: discord.Interaction, period: str, include_afk: bool = False) -> None:
        await interaction.response.defer()
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("Эта команда работает только на сервере.", ephemeral=True)
            return
        rows = await self.db.get_leaderboard(guild.id, self._after_ts(period), include_afk, limit=15)
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for index, (user_id, seconds) in enumerate(rows, start=1):
            member = guild.get_member(user_id)
            if member is not None and self.settings.ignore_bots and member.bot:
                continue
            lines.append(f"{medals.get(index, f'`#{index}`')} {self._resolve_user(guild, user_id)} — `{format_duration(seconds)}`")
        embed = discord.Embed(title="Топ активности в войсах", description=f"Период: **{PERIODS[period].label_ru}**", color=discord.Color.fuchsia())
        embed.add_field(name="Рейтинг", value="\n".join(lines) if lines else "Нет данных.", inline=False)
        await interaction.followup.send(embed=embed)

    @voicetop.autocomplete("period")
    async def voicetop_period_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await self.period_autocomplete(interaction, current)

    @app_commands.command(name="channeltop", description="Топ активности в конкретном войсе")
    @app_commands.describe(channel_id="ID или название войса из автоподсказки", period="1d / 7d / 30d / all", include_afk="Учитывать AFK")
    async def channeltop(self, interaction: discord.Interaction, channel_id: str, period: str, include_afk: bool = False) -> None:
        await interaction.response.defer()
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("Эта команда работает только на сервере.", ephemeral=True)
            return
        try:
            parsed_channel_id = int(channel_id)
        except ValueError:
            await interaction.followup.send("Неверный channel_id.", ephemeral=True)
            return
        channel = guild.get_channel(parsed_channel_id)
        if channel is None or not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            await interaction.followup.send("Голосовой канал не найден.", ephemeral=True)
            return
        rows = await self.db.get_channel_leaderboard(guild.id, channel.id, self._after_ts(period), include_afk, limit=15)
        lines = [f"`#{index}` {self._resolve_user(guild, user_id)} — `{format_duration(seconds)}`" for index, (user_id, seconds) in enumerate(rows, start=1)]
        embed = discord.Embed(title=f"Топ канала: {channel.name}", description=f"Период: **{PERIODS[period].label_ru}**", color=discord.Color.teal())
        embed.add_field(name="Рейтинг", value="\n".join(lines) if lines else "Нет данных.", inline=False)
        await interaction.followup.send(embed=embed)

    @channeltop.autocomplete("period")
    async def channeltop_period_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await self.period_autocomplete(interaction, current)

    @channeltop.autocomplete("channel_id")
    async def channeltop_channel_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await self.channel_autocomplete(interaction, current)

    @app_commands.command(name="exportstats", description="Экспорт статистики пользователя в CSV")
    @app_commands.describe(user="Пользователь", period="1d / 7d / 30d / all", include_afk="Учитывать AFK")
    async def exportstats(self, interaction: discord.Interaction, user: discord.Member, period: str, include_afk: bool = False) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("Эта команда работает только на сервере.", ephemeral=True)
            return
        csv_bytes = await self.db.export_user_sessions_csv(guild.id, user.id, self._after_ts(period), include_afk)
        filename = f"voice_stats_{guild.id}_{user.id}_{period}.csv"
        file = discord.File(io.BytesIO(csv_bytes), filename=filename)
        await interaction.followup.send(
            content=f"Экспорт готов: **{user.display_name}**, период **{PERIODS[period].label_ru}**.",
            file=file,
            ephemeral=True,
        )

    @exportstats.autocomplete("period")
    async def exportstats_period_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await self.period_autocomplete(interaction, current)
