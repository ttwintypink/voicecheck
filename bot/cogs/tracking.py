from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import discord
from discord.ext import commands

from bot.config import Settings
from bot.db import Database


log = logging.getLogger(__name__)


@dataclass(slots=True)
class LiveSession:
    guild_id: int
    user_id: int
    channel_id: int
    channel_name: str
    joined_ts: int
    is_afk: bool


class VoiceTrackingCog(commands.Cog):
    def __init__(self, bot: commands.Bot, db: Database, settings: Settings) -> None:
        self.bot = bot
        self.db = db
        self.settings = settings
        self.live_sessions: dict[tuple[int, int], LiveSession] = {}

    def _is_trackable_member(self, member: discord.Member) -> bool:
        return not (self.settings.ignore_bots and member.bot)

    def _is_trackable_channel(self, channel: discord.VoiceChannel | discord.StageChannel | None) -> bool:
        return channel is not None and channel.id not in self.settings.excluded_channel_ids

    def _is_afk_channel(self, channel: discord.abc.GuildChannel | None) -> bool:
        return channel is not None and channel.id in self.settings.afk_channel_ids

    def _human_members_in_channel(self, channel: discord.VoiceChannel | discord.StageChannel | None) -> list[discord.Member]:
        if channel is None:
            return []
        humans: list[discord.Member] = []
        for member in channel.members:
            if self._is_trackable_member(member):
                humans.append(member)
        return humans

    def _counted_seconds_for_member(
        self,
        member: discord.Member | None,
        channel: discord.VoiceChannel | discord.StageChannel | None,
        joined_ts: int,
        leave_ts: int,
        is_afk_channel: bool,
    ) -> tuple[int, int, int, int]:
        duration = max(0, leave_ts - joined_ts)
        if duration <= 0:
            return 0, 0, 0, 0

        counted = duration
        solo_seconds = 0
        self_deaf_seconds = 0
        muted_deafened_seconds = 0

        if is_afk_channel:
            return 0, duration, 0, 0

        if not self.settings.anti_afk_enabled or member is None or channel is None:
            return counted, solo_seconds, self_deaf_seconds, muted_deafened_seconds

        voice = member.voice
        if self.settings.anti_afk_exclude_self_deaf and voice is not None and voice.self_deaf:
            self_deaf_seconds = duration
            counted = 0

        if (
            counted > 0
            and self.settings.anti_afk_exclude_muted_and_deafened
            and voice is not None
            and voice.self_mute
            and voice.self_deaf
        ):
            muted_deafened_seconds = duration
            counted = 0

        if counted > 0 and self.settings.anti_afk_exclude_solo:
            humans = self._human_members_in_channel(channel)
            if len(humans) <= 1:
                grace = self.settings.anti_afk_solo_grace_minutes * 60
                allowed = min(duration, grace)
                solo_seconds = max(0, duration - allowed)
                counted = min(counted, allowed)

        return counted, solo_seconds, self_deaf_seconds, muted_deafened_seconds

    async def cog_load(self) -> None:
        await self.seed_existing_voice_states()

    async def seed_existing_voice_states(self) -> None:
        await self.bot.wait_until_ready()
        seeded = 0
        now_ts = int(time.time())
        for guild in self.bot.guilds:
            for voice_channel in guild.voice_channels:
                if not self._is_trackable_channel(voice_channel):
                    continue
                for member in voice_channel.members:
                    if not self._is_trackable_member(member):
                        continue
                    key = (guild.id, member.id)
                    self.live_sessions[key] = LiveSession(
                        guild_id=guild.id,
                        user_id=member.id,
                        channel_id=voice_channel.id,
                        channel_name=voice_channel.name,
                        joined_ts=now_ts,
                        is_afk=self._is_afk_channel(voice_channel),
                    )
                    seeded += 1
        if seeded:
            log.info("Seeded %s active voice sessions after startup.", seeded)

    async def finalize_session(
        self,
        session: LiveSession,
        leave_ts: int,
        member: discord.Member | None = None,
        channel: discord.VoiceChannel | discord.StageChannel | None = None,
    ) -> None:
        duration = max(0, leave_ts - session.joined_ts)
        if duration <= 0:
            return
        counted, solo_seconds, self_deaf_seconds, muted_deafened_seconds = self._counted_seconds_for_member(
            member=member,
            channel=channel,
            joined_ts=session.joined_ts,
            leave_ts=leave_ts,
            is_afk_channel=session.is_afk,
        )
        await self.db.add_session(
            guild_id=session.guild_id,
            user_id=session.user_id,
            channel_id=session.channel_id,
            channel_name=session.channel_name,
            join_ts=session.joined_ts,
            leave_ts=leave_ts,
            duration_seconds=duration,
            counted_seconds=counted,
            is_afk=session.is_afk,
            solo_seconds=solo_seconds,
            self_deaf_seconds=self_deaf_seconds,
            muted_deafened_seconds=muted_deafened_seconds,
        )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if not self._is_trackable_member(member):
            return

        before_channel = before.channel if self._is_trackable_channel(before.channel) else None
        after_channel = after.channel if self._is_trackable_channel(after.channel) else None

        state_only_change = before_channel == after_channel and before_channel is not None and (
            before.self_deaf != after.self_deaf or before.self_mute != after.self_mute
        )

        key = (member.guild.id, member.id)
        now_ts = int(time.time())

        if before_channel != after_channel or state_only_change:
            old_session = self.live_sessions.get(key)
            if old_session is not None:
                await self.finalize_session(old_session, now_ts, member=member, channel=before_channel)
                self.live_sessions.pop(key, None)

        if after_channel is not None:
            self.live_sessions[key] = LiveSession(
                guild_id=member.guild.id,
                user_id=member.id,
                channel_id=after_channel.id,
                channel_name=after_channel.name,
                joined_ts=now_ts,
                is_afk=self._is_afk_channel(after_channel),
            )

    async def flush_all_live_sessions(self) -> None:
        now_ts = int(time.time())
        items = list(self.live_sessions.items())
        self.live_sessions.clear()
        for (_, user_id), session in items:
            guild = self.bot.get_guild(session.guild_id)
            member = guild.get_member(user_id) if guild is not None else None
            channel = guild.get_channel(session.channel_id) if guild is not None else None
            await self.finalize_session(session, now_ts, member=member, channel=channel)
        if items:
            log.info("Flushed %s live sessions before shutdown.", len(items))
