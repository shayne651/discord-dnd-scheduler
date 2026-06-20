"""
cogs/availability.py — Player self-service commands.

  /cantmake   — Declare you can't make the regular session; pick available days.
  /endtime    — Notify the DM channel of an end-time constraint (no poll effect).
  /myvotes    — See your current poll votes.
  /blockday   — Permanently block a day from appearing in polls (e.g. always busy Tuesdays).
  /unblockday — Remove a personal day block.
  /setnick    — Set your character name (shown in polls alongside your Discord name).
"""

import discord
from discord import option
from discord.ext import commands

import database as db
from utils.dates import day_int_to_name, week_start_str, parse_time_range, minutes_to_time_str
from utils.messages import (
    cantmake_already_submitted,
    poll_created,
    endtime_dm_notification,
    endtime_confirmed,
    blockday_added,
    blockday_already,
    unblockday_removed,
    unblockday_not_found,
    blockday_poll_updated,
    nick_set,
)
from cogs.poll import post_new_poll, refresh_poll_message, check_consensus, supersede_auto_schedule


DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

DAY_LABELS = [
    ("Mon", 0), ("Tue", 1), ("Wed", 2), ("Thu", 3),
    ("Fri", 4), ("Sat", 5), ("Sun", 6),
]


# ── Day-picker ephemeral view ─────────────────────────────────────────────────

class DayPickerView(discord.ui.View):
    """
    Ephemeral view shown after /cantmake.
    Players toggle days they ARE available, then hit Submit.
    Days the player has personally blocked are pre-disabled and excluded.
    """

    def __init__(self, user_id: int, guild_id: int):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.selected: set[int] = set()
        self._user_blocks = set(db.get_user_day_blocks(guild_id, user_id)) | set(db.get_campaign_blocked_days(guild_id))
        self._guild_id = guild_id
        self._rebuild_buttons()

    def _rebuild_buttons(self):
        self.clear_items()
        for label, day_int in DAY_LABELS:
            is_blocked = day_int in self._user_blocks
            active = day_int in self.selected
            btn = discord.ui.Button(
                label=("✓ " if active else "") + label,
                style=(
                    discord.ButtonStyle.success if active
                    else discord.ButtonStyle.secondary
                ),
                custom_id=f"pick_{day_int}",
                row=day_int // 4,
                disabled=is_blocked,
            )
            if not is_blocked:
                btn.callback = self._toggle_day
            self.add_item(btn)

        submit = discord.ui.Button(
            label="✅ Submit",
            style=discord.ButtonStyle.success,
            custom_id="picker_submit",
            row=2,
        )
        submit.callback = self._submit
        self.add_item(submit)

        none_btn = discord.ui.Button(
            label="No days at all",
            style=discord.ButtonStyle.danger,
            custom_id="picker_none",
            row=2,
        )
        none_btn.callback = self._none
        self.add_item(none_btn)

    async def _toggle_day(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This picker is not for you.", ephemeral=True)
            return

        day = int(interaction.custom_id.split("_")[1])
        if day in self.selected:
            self.selected.discard(day)
        else:
            self.selected.add(day)

        self._rebuild_buttons()
        await interaction.response.edit_message(view=self)

    async def _submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This picker is not for you.", ephemeral=True)
            return
        if self.selected:
            await interaction.response.send_modal(
                TimeWindowModal(sorted(self.selected), self.user_id, interaction.guild_id)
            )
        else:
            await _process_cantmake(interaction, [])
        self.stop()

    async def _none(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This picker is not for you.", ephemeral=True)
            return
        await _process_cantmake(interaction, [])
        self.stop()


# ── Time window modal ─────────────────────────────────────────────────────────

class TimeWindowModal(discord.ui.Modal):
    """
    Shown after the day picker — lets each player optionally enter an availability
    window (e.g. '10am - 5pm') for each day they selected. Blank = all day.
    """

    def __init__(self, selected_days: list[int], user_id: int, guild_id: int):
        cfg_row = db.get_config()
        server_tz = int((cfg_row or {}).get("timezone_offset") or 0)
        player_tz = db.get_player_timezone(guild_id, user_id)
        self.player_tz = player_tz if player_tz is not None else server_tz
        self.server_tz = server_tz

        sign = "+" if self.player_tz >= 0 else ""
        tz_label = f"UTC{sign}{self.player_tz}"
        super().__init__(title=f"Your available times ({tz_label})")
        self.selected_days = selected_days
        self.user_id = user_id
        self.guild_id = guild_id

        self.day_inputs: list[tuple[int, discord.ui.InputText]] = []
        for day in selected_days[:5]:  # Discord modal limit: 5 components
            inp = discord.ui.InputText(
                label=day_int_to_name(day),
                placeholder="e.g. 10am – 5pm  (leave blank = available all day)",
                required=False,
                max_length=50,
            )
            self.add_item(inp)
            self.day_inputs.append((day, inp))

    async def callback(self, interaction: discord.Interaction):
        windows: dict[int, tuple[int, int]] = {}
        bad: list[str] = []
        tz_shift = (self.server_tz - self.player_tz) * 60  # minutes to add to convert player→server tz

        for day, inp in self.day_inputs:
            val = inp.value.strip() if inp.value else ""
            if val:
                parsed = parse_time_range(val)
                if parsed:
                    start, end = parsed
                    # Convert from player's timezone to server's timezone for storage
                    start = max(0, min(1439, start + tz_shift))
                    end = max(0, min(1439, end + tz_shift))
                    if start < end:
                        windows[day] = (start, end)
                    else:
                        bad.append(f"**{day_int_to_name(day)}**: \"{val}\" (crosses midnight after timezone conversion — enter all day)")
                else:
                    bad.append(f"**{day_int_to_name(day)}**: \"{val}\"")

        if bad:
            await interaction.response.send_message(
                "⚠️ Couldn't parse the following times (use a format like `10am - 5pm`):\n"
                + "\n".join(bad)
                + "\n\nEverything else was saved.",
                ephemeral=True,
            )
            await _process_cantmake(interaction, self.selected_days, windows, from_modal=True, already_responded=True)
        else:
            await _process_cantmake(interaction, self.selected_days, windows, from_modal=True)


# ── Core cantmake logic ───────────────────────────────────────────────────────

async def _process_cantmake(
    interaction: discord.Interaction,
    available_days: list[int],
    windows: dict[int, tuple[int, int]] | None = None,
    *,
    from_modal: bool = False,
    already_responded: bool = False,
):
    """Called after the player submits the day picker (directly or via modal)."""
    week = week_start_str()
    poll = db.get_open_poll()

    days_str = (
        ", ".join(day_int_to_name(d) for d in sorted(available_days))
        if available_days else "no days"
    )

    # Build a short time-window summary for the confirmation message
    window_notes = ""
    if windows:
        parts = [
            f"{day_int_to_name(d)}: {minutes_to_time_str(s)}–{minutes_to_time_str(e)}"
            for d, (s, e) in sorted(windows.items())
        ]
        window_notes = "  *(windows: " + ", ".join(parts) + ")*"

    if poll:
        db.upsert_cant_make(1, interaction.user.id, available_days)
        if windows:
            db.upsert_day_time_windows(1, interaction.user.id, windows)
        await refresh_poll_message(interaction.guild, interaction.user.id)

        content = (
            f"✅ Got it! Your availability (**{days_str}**) has been recorded "
            f"and the poll updated.{window_notes}"
        )
        if not already_responded:
            if from_modal:
                await interaction.response.send_message(content, ephemeral=True)
            else:
                await interaction.response.edit_message(content=content, view=None)

        poll = db.get_open_poll()
        if poll:
            votes = db.get_votes_for_poll(1)
            blocked_days = db.get_blocked_days_for_guild(interaction.guild_id)
            await check_consensus(interaction, poll, votes, blocked_days)
    else:
        cfg_row = db.get_config()
        if cfg_row and (cfg_row.get("current_event_id") or cfg_row.get("next_cycle_date")):
            await supersede_auto_schedule(interaction.guild, cfg_row)

        db.create_poll(0, 0, interaction.user.id, week)
        db.upsert_cant_make(1, interaction.user.id, available_days)
        if windows:
            db.upsert_day_time_windows(1, interaction.user.id, windows)

        msg = await post_new_poll(interaction.guild, interaction.user.id, week)
        content = poll_created(interaction.user.display_name)
        if msg:
            content += f"\n\nYour available days: **{days_str}**.{window_notes}"
        if not already_responded:
            if from_modal:
                await interaction.response.send_message(content, ephemeral=True)
            else:
                await interaction.response.edit_message(content=content, view=None)


# ── Cog ───────────────────────────────────────────────────────────────────────

class AvailabilityCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    # ── /cantmake ─────────────────────────────────────────────────────────────

    @discord.slash_command(
        name="cantmake",
        description="Let everyone know you can't make the regular session and pick your available days.",
    )
    async def cantmake(self, ctx: discord.ApplicationContext):
        view = DayPickerView(ctx.author.id, ctx.guild_id)
        await ctx.respond(
            "**Which days work for you this week?**\n"
            "Select all days you're available, then hit **Submit**.\n"
            "*(Days you've permanently blocked are greyed out.)*",
            view=view,
            ephemeral=True,
        )

    # ── /endtime ──────────────────────────────────────────────────────────────

    @discord.slash_command(
        name="endtime",
        description="Notify the DM that you need to end by a specific time this session.",
    )
    @option("time", description='When you need to leave, e.g. "10pm" or "9:30 PM"')
    async def endtime(self, ctx: discord.ApplicationContext, time: str):
        cfg_row = db.get_config()
        dm_channel_id = cfg_row.get("dm_channel_id") if cfg_row else None

        week = week_start_str()
        db.record_end_time(ctx.guild_id, ctx.author.id, week, time)

        if dm_channel_id:
            dm_channel = ctx.guild.get_channel(int(dm_channel_id))
            if dm_channel:
                await dm_channel.send(
                    endtime_dm_notification(ctx.author.display_name, time)
                )

        await ctx.respond(endtime_confirmed(), ephemeral=True)

    # ── /myvotes ──────────────────────────────────────────────────────────────

    @discord.slash_command(
        name="myvotes",
        description="See which days you've voted for in the current poll.",
    )
    async def myvotes(self, ctx: discord.ApplicationContext):
        poll = db.get_open_poll()
        if not poll:
            await ctx.respond("There is no active poll right now.", ephemeral=True)
            return

        voted_days = db.get_user_votes(1, ctx.author.id)
        blocks = db.get_user_day_blocks(ctx.guild_id, ctx.author.id)
        lines = []
        if voted_days:
            lines.append("**Your votes:** " + ", ".join(day_int_to_name(d) for d in sorted(voted_days)))
        else:
            lines.append("You haven't voted yet. Click the day buttons on the poll message!")
        if blocks:
            lines.append("**Your blocked days:** " + ", ".join(day_int_to_name(d) for d in sorted(blocks)))
        await ctx.respond("\n".join(lines), ephemeral=True)

    # ── /blockday ─────────────────────────────────────────────────────────────

    @discord.slash_command(
        name="blockday",
        description="Permanently block a day from scheduling polls (e.g. you're always busy Tuesdays).",
    )
    @option("day", description="The day to block", choices=DAYS)
    async def blockday(self, ctx: discord.ApplicationContext, day: str):
        day_int = DAYS.index(day)
        added = db.add_day_block(ctx.guild_id, ctx.author.id, day_int)

        if not added:
            await ctx.respond(blockday_already(day), ephemeral=True)
            return

        # Remove their existing vote for this day in any open poll
        poll = db.get_open_poll()
        poll_updated = False
        if poll:
            db.remove_vote_for_day(1, ctx.author.id, day_int)
            await refresh_poll_message(ctx.guild, ctx.author.id)
            poll_updated = True

        msg = blockday_added(day)
        if poll_updated:
            msg += "\n" + blockday_poll_updated(day)
        await ctx.respond(msg, ephemeral=True)

    # ── /unblockday ───────────────────────────────────────────────────────────

    @discord.slash_command(
        name="unblockday",
        description="Remove a personal day block so the day can appear in polls again.",
    )
    @option("day", description="The day to unblock", choices=DAYS)
    async def unblockday(self, ctx: discord.ApplicationContext, day: str):
        day_int = DAYS.index(day)
        removed = db.remove_day_block(ctx.guild_id, ctx.author.id, day_int)

        if not removed:
            await ctx.respond(unblockday_not_found(day), ephemeral=True)
            return

        # Refresh poll if one is open (day is now unblocked)
        if db.get_open_poll():
            await refresh_poll_message(ctx.guild, ctx.author.id)

        await ctx.respond(unblockday_removed(day), ephemeral=True)

    # ── /setnick ──────────────────────────────────────────────────────────────

    @discord.slash_command(
        name="setnick",
        description="Set your character name — shown next to your name in scheduling polls.",
    )
    @option("name", description="Your character's name")
    async def setnick(self, ctx: discord.ApplicationContext, name: str):
        # Sanity-check length
        if len(name) > 64:
            await ctx.respond("Character name must be 64 characters or fewer.", ephemeral=True)
            return

        db.upsert_nickname(ctx.guild_id, ctx.author.id, name)

        # Refresh current poll so names update immediately
        if db.get_open_poll():
            await refresh_poll_message(ctx.guild, ctx.author.id)

        await ctx.respond(nick_set(name), ephemeral=True)

    # ── /setmytimezone ────────────────────────────────────────────────────────

    @discord.slash_command(
        name="setmytimezone",
        description="Set your personal timezone so your availability times are interpreted correctly.",
    )
    @option("offset", description="Your UTC offset in whole hours, e.g. -5 or 1", type=int, required=False)
    @option("detect", description="Auto-detect from the server's local clock", type=bool, required=False, default=False)
    async def setmytimezone(self, ctx: discord.ApplicationContext, offset: int = None, detect: bool = False):
        if detect:
            from datetime import datetime
            local_utcoffset = datetime.now().astimezone().utcoffset()
            offset = int(local_utcoffset.total_seconds() / 3600)
        elif offset is None:
            await ctx.respond(
                "❌ Provide an `offset` (e.g. `-5`) or set `detect: True` to use the server clock.\n"
                "Your timezone is used to interpret times you enter in `/cantmake`.",
                ephemeral=True,
            )
            return

        if not -12 <= offset <= 14:
            await ctx.respond("❌ Offset must be between -12 and +14.", ephemeral=True)
            return

        db.set_player_timezone(ctx.guild_id, ctx.author.id, offset)
        sign = "+" if offset >= 0 else ""
        source = " *(detected from server clock)*" if detect else ""
        await ctx.respond(
            f"✅ Your timezone is set to **UTC{sign}{offset}**{source}. "
            "Times you enter in `/cantmake` will be interpreted in this timezone.",
            ephemeral=True,
        )


def setup(bot: discord.Bot):
    bot.add_cog(AvailabilityCog(bot))
