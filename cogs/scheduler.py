"""
cogs/scheduler.py — Fully-automatic recurring cycle scheduler.

Runs once a day. When config.next_cycle_date is due and no poll is open
(meaning nobody has overridden this cycle via /cantmake or /startpoll), it
auto-creates the Discord Scheduled Event for the campaign's default day,
announces it, and queues up the cycle after that per recurrence_weeks.

If a poll is open, this loop does nothing — the poll's own resolution
(cogs/poll.py: _close_poll_winner / _confirm_no_days) is responsible for
setting the next next_cycle_date once it closes.
"""

from datetime import date, timedelta

import discord
from discord.ext import commands, tasks

import database as db
from utils.dates import day_int_to_name, friendly_date
from cogs.poll import create_scheduled_event_for_day


class SchedulerCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.check_cycle.start()

    def cog_unload(self):
        self.check_cycle.cancel()

    @tasks.loop(hours=24)
    async def check_cycle(self):
        cfg = db.get_config()
        if not cfg or not cfg.get("initialized_at"):
            return

        next_cycle_raw = cfg.get("next_cycle_date")
        if not next_cycle_raw:
            return  # nothing pending — an open poll is likely deciding this cycle

        if db.get_open_poll():
            return  # a poll is already deciding this cycle

        next_cycle_date = date.fromisoformat(next_cycle_raw)
        if date.today() < next_cycle_date:
            return  # not due yet

        guild = self.bot.get_guild(int(cfg["guild_id"]))
        if not guild:
            return

        day = int(cfg["session_day"])
        recurrence_weeks = int(cfg.get("recurrence_weeks") or 1)

        event = await create_scheduled_event_for_day(guild, day, next_cycle_date, cfg)

        db.upsert_config(
            guild.id,
            current_event_id=event.id if event else None,
            next_cycle_date=(next_cycle_date + timedelta(weeks=recurrence_weeks)).isoformat(),
        )

        poll_channel_id = cfg.get("poll_channel_id")
        if poll_channel_id:
            channel = guild.get_channel(int(poll_channel_id))
            if channel:
                label = f"{day_int_to_name(day)} ({friendly_date(next_cycle_date)})"
                msg = f"📅 Next session auto-scheduled for **{label}**."
                if event:
                    msg += f"\n[View event & get notified]({event.url})"
                msg += "\nCan't make it? Run `/cantmake` to open a poll for this week instead."
                await channel.send(msg)

    @check_cycle.before_loop
    async def before_check_cycle(self):
        await self.bot.wait_until_ready()


def setup(bot: discord.Bot):
    bot.add_cog(SchedulerCog(bot))
