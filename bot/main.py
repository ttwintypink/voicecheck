from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from bot.config import load_settings, Settings
from bot.db import Database
from bot.cogs.tracking import VoiceTrackingCog
from bot.cogs.stats import StatsCog


logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("voice-tracker-bot")


class VoiceTrackerBot(commands.Bot):
    def __init__(self, settings: Settings, db: Database) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.voice_states = True

        super().__init__(command_prefix="!", intents=intents)
        self.settings = settings
        self.db = db

    async def setup_hook(self) -> None:
        await self.db.connect()
        await self.add_cog(VoiceTrackingCog(self, self.db, self.settings))
        await self.add_cog(StatsCog(self, self.db, self.settings))

        if self.settings.command_sync_guild_only and self.settings.guild_id:
            guild_obj = discord.Object(id=self.settings.guild_id)
            self.tree.copy_global_to(guild=guild_obj)
            synced = await self.tree.sync(guild=guild_obj)
            log.info("Synced %s guild command(s) to guild %s", len(synced), self.settings.guild_id)
        else:
            synced = await self.tree.sync()
            log.info("Synced %s global command(s)", len(synced))

    async def on_ready(self) -> None:
        log.info("Bot is ready: %s (%s)", self.user, self.user.id if self.user else "unknown")

    async def close(self) -> None:
        tracking_cog = self.get_cog("VoiceTrackingCog")
        if isinstance(tracking_cog, VoiceTrackingCog):
            await tracking_cog.flush_all_live_sessions()
        await self.db.close()
        await super().close()


async def main() -> None:
    settings = load_settings()
    db = Database(settings.database_path)
    bot = VoiceTrackerBot(settings=settings, db=db)
    try:
        await bot.start(settings.token)
    finally:
        await bot.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
