"""
cogs/poll.py — Poll message, button interactions, consensus detection, tiebreaker.

Architecture notes:
  - PollView / TiebreakerView use stable custom_id strings so they survive bot restarts.
  - An asyncio.Lock per poll prevents race conditions when two players click simultaneously.
  - Views are re-registered in bot.on_ready via bot.add_view().
  - Blocked days (player_day_blocks) are fetched fresh on every render; blocked buttons
    are disabled and shown with 🚫 in the embed.
  - Character names (player_nicknames) are shown in parentheses next to Discord names.
"""

import asyncio
import json
import random
from datetime import date, timedelta

import discord
from discord.ext import commands

import database as db
from utils.dates import (
    day_int_to_name, day_int_to_short,
    week_start_str,
    date_for_day_this_week, friendly_date,
    compute_session_start, build_event_datetimes, minutes_to_time_str,
)
from utils.messages import (
    poll_title, poll_footer, poll_cancelled_no_days, no_days_confirm,
    no_consensus_nudge, tiebreaker_intro, tiebreaker_result, poll_closed_winner,
)

# One lock per poll/tiebreaker ID (always id=1 but future-proofed)
_poll_locks: dict[int, asyncio.Lock] = {}
_tb_locks:   dict[int, asyncio.Lock] = {}


def _poll_lock(poll_id: int = 1) -> asyncio.Lock:
    if poll_id not in _poll_locks:
        _poll_locks[poll_id] = asyncio.Lock()
    return _poll_locks[poll_id]


def _tb_lock(tb_id: int = 1) -> asyncio.Lock:
    if tb_id not in _tb_locks:
        _tb_locks[tb_id] = asyncio.Lock()
    return _tb_locks[tb_id]


# ── Display helpers ───────────────────────────────────────────────────────────

def _player_display(uid: str, guild: discord.Guild, nicknames: dict[str, str]) -> str:
    """Discord name, with character name in italics if set. e.g. 'Shayne *(Gandalf)*'"""
    if uid == "campaign":
        return "🛑 *(campaign setting)*"
    member = guild.get_member(int(uid))
    discord_name = member.display_name if member else f"<@{uid}>"
    char = nicknames.get(str(uid))
    return f"{discord_name} *({char})*" if char else discord_name


# ── Embed builders ────────────────────────────────────────────────────────────

def build_poll_embed(
    poll: dict,
    votes: dict[int, list[str]],
    cant_make_ids: list[str],
    guild: discord.Guild,
    blocked_days: dict[int, list[str]],
    nicknames: dict[str, str],
    cfg_row: dict | None = None,
) -> discord.Embed:
    week = poll["week_start"]

    # Build description with session info
    session_info = ""
    if cfg_row:
        day_name = day_int_to_name(cfg_row.get("session_day", 5))
        time_str = cfg_row.get("session_time")
        recurrence_weeks = int(cfg_row.get("recurrence_weeks") or 1)
        cadence = f" (every {recurrence_weeks} weeks)" if recurrence_weeks > 1 else ""
        session_info = f"Regular session: **{day_name}s**{cadence}" + (f" at **{time_str}**" if time_str else "")

    embed = discord.Embed(
        title=poll_title(week),
        description=(
            (session_info + "\n\n" if session_info else "")
            + "Vote for **every day** that works for you — multiple votes allowed!"
        ),
        color=discord.Color.gold(),
    )

    for day in range(7):
        day_date = date_for_day_this_week(day)
        field_name_prefix = f"{day_int_to_name(day)} {day_date.strftime('%b %-d')}"

        if day in blocked_days:
            blocker_names = [_player_display(uid, guild, nicknames) for uid in blocked_days[day]]
            embed.add_field(
                name=f"🚫 {field_name_prefix} — Blocked",
                value=f"Unavailable for: {', '.join(blocker_names)}",
                inline=False,
            )
        else:
            day_voters = votes.get(day, [])
            names = [_player_display(uid, guild, nicknames) for uid in day_voters]
            value = ", ".join(names) if names else "*No votes yet*"
            embed.add_field(
                name=f"{field_name_prefix} — {len(day_voters)} vote(s)",
                value=value,
                inline=False,
            )

    if cant_make_ids:
        cant_names = [_player_display(uid, guild, nicknames) for uid in cant_make_ids]
        embed.add_field(
            name="⚠️ Can't make the regular session",
            value=", ".join(cant_names),
            inline=False,
        )

    embed.set_footer(text=poll_footer())
    return embed


def build_tiebreaker_embed(
    tied_days: list[int],
    tb_votes: dict[int, list[str]],
    guild: discord.Guild,
    nicknames: dict[str, str],
) -> discord.Embed:
    day_names = [day_int_to_name(d) for d in tied_days]
    embed = discord.Embed(
        title="🎲 Tiebreaker — Pick your preferred day",
        description=tiebreaker_intro(day_names),
        color=discord.Color.purple(),
    )
    for day in tied_days:
        voters = tb_votes.get(day, [])
        names = [_player_display(uid, guild, nicknames) for uid in voters]
        embed.add_field(
            name=f"{day_int_to_name(day)} — {len(voters)} vote(s)",
            value=", ".join(names) if names else "*No votes yet*",
            inline=True,
        )
    embed.set_footer(text="Vote for your preferred day. Highest count wins.")
    return embed


# ── Auto-schedule helpers ─────────────────────────────────────────────────────
# Shared between the /init wizard, the recurring scheduler, and poll resolution
# so a "session" only ever gets created in one place.

async def create_scheduled_event_for_day(
    guild: discord.Guild,
    day: int,
    event_date: date,
    cfg_row: dict,
    start_mins: int | None = None,
) -> discord.ScheduledEvent | None:
    """Create a Discord Scheduled Event for `day` on `event_date` using the configured voice channel."""
    voice_channel_id = cfg_row.get("voice_channel_id")
    if not voice_channel_id:
        print("[Bot] Skipping event creation: no voice channel configured (run /setvoicechannel)")
        return None

    voice_channel = guild.get_channel(int(voice_channel_id))
    if not voice_channel:
        print(f"[Bot] Skipping event creation: voice channel {voice_channel_id} not found in guild")
        return None

    week_start_iso = week_start_str(event_date)
    start_dt, end_dt = build_event_datetimes(day, week_start_iso, start_mins, cfg_row)

    print(f"[Bot] Creating scheduled event: {day_int_to_name(day)} {start_dt} → {end_dt} in #{voice_channel.name}")
    try:
        event = await guild.create_scheduled_event(
            name="D&D Session",
            description=f"Scheduled session for {friendly_date(event_date)}.",
            start_time=start_dt,
            end_time=end_dt,
            location=voice_channel,
        )
        print(f"[Bot] Event created: {event.url}")
        return event
    except discord.Forbidden as e:
        print(f"[Bot] Event creation forbidden — bot needs Manage Events permission: {e}")
    except discord.HTTPException as e:
        print(f"[Bot] Event creation failed (HTTP {e.status}): {e.text}")
    return None


async def cancel_event(guild: discord.Guild, event_id: str | None) -> None:
    """Best-effort delete of a previously created Discord Scheduled Event."""
    if not event_id:
        return
    try:
        event = guild.get_scheduled_event(int(event_id)) or await guild.fetch_scheduled_event(int(event_id))
        if event:
            await event.delete()
    except (discord.NotFound, discord.HTTPException, ValueError):
        pass


async def supersede_auto_schedule(guild: discord.Guild, cfg_row: dict) -> None:
    """
    Cancel any auto-scheduled event for the upcoming cycle and clear cycle tracking,
    since a poll is about to decide this cycle instead. Called right before a fresh
    poll is opened (via /cantmake or /startpoll).
    """
    await cancel_event(guild, cfg_row.get("current_event_id"))
    db.upsert_config(guild.id, current_event_id=None, next_cycle_date=None)


# ── Poll View ─────────────────────────────────────────────────────────────────

class PollView(discord.ui.View):
    """
    The main scheduling poll.
    custom_ids are stable strings so button callbacks survive restarts.
    """

    def __init__(self):
        super().__init__(timeout=None)

    def _make_buttons(
        self,
        votes: dict[int, list[str]],
        user_id: int,
        blocked_days: dict[int, list[str]],
    ) -> None:
        """Add day buttons and the 'no days' button, disabling any blocked days."""
        self.clear_items()
        for day in range(7):
            is_blocked = day in blocked_days
            count = len(votes.get(day, []))
            voted = str(user_id) in votes.get(day, [])

            if is_blocked:
                label = f"🚫 {day_int_to_short(day)}"
                style = discord.ButtonStyle.secondary
            elif voted:
                label = f"{day_int_to_short(day)} ✓ ({count})"
                style = discord.ButtonStyle.success
            else:
                label = f"{day_int_to_short(day)} ({count})" if count else day_int_to_short(day)
                style = discord.ButtonStyle.secondary

            btn = discord.ui.Button(
                label=label,
                style=style,
                custom_id=f"vote_day_{day}",
                row=day // 4,
                disabled=is_blocked,
            )
            btn.callback = self._day_callback
            self.add_item(btn)

        no_days_btn = discord.ui.Button(
            label="❌ No days work for me",
            style=discord.ButtonStyle.danger,
            custom_id="no_days_work",
            row=2,
        )
        no_days_btn.callback = self._no_days_callback
        self.add_item(no_days_btn)

    async def _day_callback(self, interaction: discord.Interaction):
        day = int(interaction.custom_id.split("_")[-1])
        await _handle_vote(interaction, day)

    async def _no_days_callback(self, interaction: discord.Interaction):
        await _handle_no_days(interaction)


class ConfirmNoDaysView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Confirm — cancel D&D this week", style=discord.ButtonStyle.danger)
    async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction):
        await _confirm_no_days(interaction)
        self.stop()

    @discord.ui.button(label="Never mind", style=discord.ButtonStyle.secondary)
    async def cancel(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.send_message("Okay, no change.", ephemeral=True)
        self.stop()


# ── Tiebreaker View ───────────────────────────────────────────────────────────

class TiebreakerView(discord.ui.View):
    def __init__(self, tied_days: list[int]):
        super().__init__(timeout=None)
        for day in tied_days:
            btn = discord.ui.Button(
                label=day_int_to_name(day),
                style=discord.ButtonStyle.primary,
                custom_id=f"tb_day_{day}",
            )
            btn.callback = self._tb_callback
            self.add_item(btn)

    async def _tb_callback(self, interaction: discord.Interaction):
        day = int(interaction.custom_id.split("_")[-1])
        await _handle_tiebreaker_vote(interaction, day)


# ── Core interaction handlers ─────────────────────────────────────────────────

async def _handle_vote(interaction: discord.Interaction, day: int):
    async with _poll_lock():
        poll = db.get_open_poll()
        if not poll:
            await interaction.response.send_message(
                "This poll is no longer active.", ephemeral=True
            )
            return

        # Safety check: reject if day is blocked (button should already be disabled)
        blocked_days = db.get_blocked_days_for_guild(interaction.guild_id)
        if day in blocked_days:
            blocker_names = []
            for uid in blocked_days[day]:
                if uid == "campaign":
                    blocker_names.append("the campaign schedule")
                    continue
                m = interaction.guild.get_member(int(uid))
                blocker_names.append(m.display_name if m else f"<@{uid}>")
            await interaction.response.send_message(
                f"🚫 {day_int_to_name(day)} is blocked by {', '.join(blocker_names)}.",
                ephemeral=True,
            )
            return

        db.toggle_vote(1, interaction.user.id, day)

        nicknames = db.get_all_nicknames(interaction.guild_id)
        votes = db.get_votes_for_poll(1)
        cant_ids = db.get_cant_make_users(1)
        cfg_row = db.get_config()
        embed = build_poll_embed(poll, votes, cant_ids, interaction.guild, blocked_days, nicknames, cfg_row)

        view = PollView()
        view._make_buttons(votes, interaction.user.id, blocked_days)

        await interaction.response.edit_message(embed=embed, view=view)

        await check_consensus(interaction, poll, votes, blocked_days)


async def check_consensus(
    interaction: discord.Interaction,
    poll: dict,
    votes: dict[int, list[str]],
    blocked_days: dict[int, list[str]],
):
    """
    Consensus = every member of the player role has voted for that day.
    /cantmake available days are merged into votes so both paths count.
    Blocked days are excluded from consideration entirely.
    """
    cfg_row = db.get_config()
    player_role_id = cfg_row.get("player_role_id") if cfg_row else None
    if not player_role_id:
        return

    role = interaction.guild.get_role(int(player_role_id))
    if not role:
        return

    roster_ids = {str(m.id) for m in role.members}
    if not roster_ids:
        return

    # Merge /cantmake available days into votes so both submission paths count
    cant_make_data = db.get_cant_make_available_days(1)
    cant_make_submitters = set(cant_make_data.keys())
    combined_votes: dict[int, list[str]] = {day: list(voters) for day, voters in votes.items()}
    for user_id, avail_days in cant_make_data.items():
        for day in avail_days:
            if user_id not in combined_votes.get(day, []):
                combined_votes.setdefault(day, []).append(user_id)

    consensus_days = []
    for day, voters in combined_votes.items():
        if day in blocked_days:
            continue
        if roster_ids.issubset(set(voters)):
            consensus_days.append(day)

    if not consensus_days:
        # All voted = clicked at least one day button OR submitted /cantmake (even with no days)
        all_voted = all(
            any(str(uid) in combined_votes.get(d, []) for d in range(7) if d not in blocked_days)
            or str(uid) in cant_make_submitters
            for uid in roster_ids
        )
        if all_voted:
            poll_channel_id = cfg_row.get("poll_channel_id") if cfg_row else None
            if poll_channel_id:
                channel = interaction.guild.get_channel(int(poll_channel_id))
                if channel:
                    await channel.send(no_consensus_nudge(role.mention))
        return

    if len(consensus_days) == 1:
        await _close_poll_winner(interaction, consensus_days[0], poll)
    else:
        await _start_tiebreaker(interaction, consensus_days, poll)


async def _close_poll_winner(interaction: discord.Interaction, day: int, poll: dict):
    db.close_poll("consensus")

    cfg_row = db.get_config()
    session_day = cfg_row["session_day"] if cfg_row else 5
    db.record_history(poll["week_start"], session_day, rescheduled_to=day, reason="consensus")

    day_name = day_int_to_name(day)
    day_date = date_for_day_this_week(day)

    # Compute session start time from per-day time windows
    all_windows = db.get_day_time_windows_for_poll(1)
    min_hours = float((cfg_row or {}).get("min_session_hours") or 2.0)
    start_mins = compute_session_start(day, all_windows, min_hours)
    start_dt, end_dt = build_event_datetimes(day, poll["week_start"], start_mins, cfg_row or {})

    # Always show the time (derived from start_dt, which already applied fallbacks)
    display_mins = start_dt.hour * 60 + start_dt.minute
    time_str = f" at {minutes_to_time_str(display_mins)}"
    label = f"{day_name} ({friendly_date(day_date)}){time_str}"

    # Cancel any event the auto-scheduler already created for this cycle before
    # creating the real one for the winning day (they may differ).
    await cancel_event(interaction.guild, (cfg_row or {}).get("current_event_id"))

    event = await create_scheduled_event_for_day(interaction.guild, day, day_date, cfg_row or {}, start_mins)
    event_url = event.url if event else None

    # The winning day becomes the new default — future cycles auto-schedule on it
    # unless overridden again via /cantmake. Queue up the next cycle per cadence.
    recurrence_weeks = int((cfg_row or {}).get("recurrence_weeks") or 1)
    next_cycle_date = day_date + timedelta(weeks=recurrence_weeks)
    db.upsert_config(
        interaction.guild_id,
        session_day=day,
        current_event_id=event.id if event else None,
        next_cycle_date=next_cycle_date.isoformat(),
    )

    poll_channel_id = (cfg_row or {}).get("poll_channel_id")
    if poll_channel_id:
        channel = interaction.guild.get_channel(int(poll_channel_id))
        if channel:
            try:
                msg = await channel.fetch_message(int(poll["message_id"]))
                closed_embed = msg.embeds[0] if msg.embeds else discord.Embed()
                closed_embed.title = f"✅ SCHEDULED — {day_name} {friendly_date(day_date)}{time_str}"
                closed_embed.color = discord.Color.green()
                await msg.edit(embed=closed_embed, view=discord.ui.View())
            except (discord.NotFound, discord.HTTPException):
                pass
            await channel.send(poll_closed_winner(label, event_url))


async def _start_tiebreaker(interaction: discord.Interaction, tied_days: list[int], poll: dict):
    db.close_poll("tiebreaker")

    cfg_row = db.get_config()
    poll_channel_id = cfg_row.get("poll_channel_id") if cfg_row else None
    if not poll_channel_id:
        return

    channel = interaction.guild.get_channel(int(poll_channel_id))
    if not channel:
        return

    nicknames = db.get_all_nicknames(interaction.guild_id)
    tb_view = TiebreakerView(tied_days)
    tb_embed = build_tiebreaker_embed(tied_days, {}, interaction.guild, nicknames)
    msg = await channel.send(embed=tb_embed, view=tb_view)

    db.create_tiebreaker(msg.id, channel.id, poll["week_start"], tied_days)


async def _handle_no_days(interaction: discord.Interaction):
    poll = db.get_open_poll()
    if not poll:
        await interaction.response.send_message("This poll is no longer active.", ephemeral=True)
        return
    confirm_view = ConfirmNoDaysView()
    await interaction.response.send_message(no_days_confirm(), view=confirm_view, ephemeral=True)


async def _confirm_no_days(interaction: discord.Interaction):
    async with _poll_lock():
        poll = db.get_open_poll()
        if not poll:
            await interaction.response.send_message("Poll is already closed.", ephemeral=True)
            return

        db.close_poll("no_days")

        cfg_row = db.get_config()
        session_day = cfg_row["session_day"] if cfg_row else 5
        db.record_history(poll["week_start"], session_day, cancelled=True, reason="no_days")

        # Queue up the next cycle per cadence, anchored to the cancelled cycle's date
        # (not "today") so a biweekly campaign doesn't drift to weekly.
        recurrence_weeks = int((cfg_row or {}).get("recurrence_weeks") or 1)
        cancelled_session_date = date.fromisoformat(poll["week_start"]) + timedelta(days=session_day)
        next_date = cancelled_session_date + timedelta(weeks=recurrence_weeks)
        db.upsert_config(interaction.guild_id, current_event_id=None, next_cycle_date=next_date.isoformat())

        poll_channel_id = cfg_row.get("poll_channel_id") if cfg_row else None
        if poll_channel_id:
            channel = interaction.guild.get_channel(int(poll_channel_id))
            if channel:
                try:
                    msg = await channel.fetch_message(int(poll["message_id"]))
                    closed_embed = msg.embeds[0] if msg.embeds else discord.Embed()
                    closed_embed.title = "❌ CANCELLED — D&D is off this week"
                    closed_embed.color = discord.Color.red()
                    await msg.edit(embed=closed_embed, view=discord.ui.View())
                except (discord.NotFound, discord.HTTPException):
                    pass
                await channel.send(poll_cancelled_no_days(next_date))

        await interaction.response.send_message("D&D has been cancelled for this week.", ephemeral=True)


async def _handle_tiebreaker_vote(interaction: discord.Interaction, day: int):
    async with _tb_lock():
        tb = db.get_open_tiebreaker()
        if not tb:
            await interaction.response.send_message(
                "This tiebreaker is no longer active.", ephemeral=True
            )
            return

        tied_days = json.loads(tb["tied_days"])
        db.toggle_tiebreaker_vote(1, interaction.user.id, day)
        tb_votes = db.get_tiebreaker_votes(1)
        nicknames = db.get_all_nicknames(interaction.guild_id)

        embed = build_tiebreaker_embed(tied_days, tb_votes, interaction.guild, nicknames)
        view = TiebreakerView(tied_days)
        await interaction.response.edit_message(embed=embed, view=view)

        # Check if all roster members have voted
        cfg_row = db.get_config()
        player_role_id = cfg_row.get("player_role_id") if cfg_row else None
        if not player_role_id:
            return

        role = interaction.guild.get_role(int(player_role_id))
        if not role:
            return

        roster_ids = {str(m.id) for m in role.members}
        all_voted_ids = {uid for voters in tb_votes.values() for uid in voters}
        if not roster_ids.issubset(all_voted_ids):
            return

        # Resolve winner
        max_votes = max(len(v) for v in tb_votes.values()) if tb_votes else 0
        winners = [d for d in tied_days if len(tb_votes.get(d, [])) == max_votes]
        was_random = len(winners) > 1
        winner = random.choice(winners)

        db.close_tiebreaker(winner)

        poll_channel_id = cfg_row.get("poll_channel_id") if cfg_row else None
        if poll_channel_id:
            channel = interaction.guild.get_channel(int(poll_channel_id))
            if channel:
                try:
                    msg = await channel.fetch_message(int(tb["message_id"]))
                    closed_embed = embed.copy()
                    closed_embed.title = f"🎉 Winner: {day_int_to_name(winner)}"
                    closed_embed.color = discord.Color.green()
                    await msg.edit(embed=closed_embed, view=discord.ui.View())
                except (discord.NotFound, discord.HTTPException):
                    pass
                await channel.send(tiebreaker_result(day_int_to_name(winner), was_random))


# ── Public helpers used by availability.py ────────────────────────────────────

async def post_new_poll(guild: discord.Guild, created_by: int, week: str) -> discord.Message | None:
    """Post a fresh poll message. The DB row must already exist before calling this."""
    cfg_row = db.get_config()
    if not cfg_row or not cfg_row.get("poll_channel_id"):
        return None

    channel = guild.get_channel(int(cfg_row["poll_channel_id"]))
    if not channel:
        return None

    blocked_days = db.get_blocked_days_for_guild(guild.id)
    nicknames = db.get_all_nicknames(guild.id)
    votes = db.get_votes_for_poll(1)
    cant_ids = db.get_cant_make_users(1)
    poll = db.get_open_poll()

    embed = build_poll_embed(poll, votes, cant_ids, guild, blocked_days, nicknames, cfg_row)
    view = PollView()
    view._make_buttons(votes, created_by, blocked_days)

    msg = await channel.send(embed=embed, view=view)
    db.update_poll_message_id(msg.id)
    return msg


async def refresh_poll_message(guild: discord.Guild, requesting_user_id: int) -> None:
    """Re-render and edit the existing poll message with the latest state."""
    poll = db.get_open_poll()
    if not poll:
        return

    channel = guild.get_channel(int(poll["channel_id"]))
    if not channel:
        return

    try:
        msg = await channel.fetch_message(int(poll["message_id"]))
    except (discord.NotFound, discord.HTTPException):
        return

    blocked_days = db.get_blocked_days_for_guild(guild.id)
    nicknames = db.get_all_nicknames(guild.id)
    votes = db.get_votes_for_poll(1)
    cant_ids = db.get_cant_make_users(1)
    cfg_row = db.get_config()

    embed = build_poll_embed(poll, votes, cant_ids, guild, blocked_days, nicknames, cfg_row)
    view = PollView()
    view._make_buttons(votes, requesting_user_id, blocked_days)
    await msg.edit(embed=embed, view=view)


# ── Cog ───────────────────────────────────────────────────────────────────────

class PollCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        """Re-register persistent views so buttons keep working after a restart."""
        poll = db.get_open_poll()
        if poll:
            self.bot.add_view(PollView())

        tb = db.get_open_tiebreaker()
        if tb:
            tied_days = json.loads(tb["tied_days"])
            self.bot.add_view(TiebreakerView(tied_days))


def setup(bot: discord.Bot):
    bot.add_cog(PollCog(bot))
