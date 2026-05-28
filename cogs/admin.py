"""
cogs/admin.py — Admin commands restricted to the DM role.

Commands:
  /setschedule  — Set the regular session day and optional time (e.g. Fridays at 7pm)
  /setday       — Quick shortcut to change just the session day
  /setchannel   — Point the bot at poll/notify channels
  /setrole      — Set the player role
  /setdmrole    — Set the DM role (grants access to these commands)
  /resetpoll    — Cancel and wipe the current poll
  /wipe         — End-of-campaign reset: removes roles and all session data
  /status       — Show current configuration and poll state
"""

import discord
from discord import option
from discord.ext import commands

import database as db
import config
from utils.dates import day_int_to_name, next_session_date, week_start_str
from utils.messages import (
    resetpoll_done, wipe_confirm_prompt, wipe_done, wipe_role_error, schedule_set
)
from cogs.poll import post_new_poll


DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _has_dm_role(ctx: discord.ApplicationContext) -> bool:
    """Return True if the invoking member has the configured DM role."""
    cfg = db.get_config()
    if not cfg or not cfg.get("dm_role_id"):
        # No DM role set yet — allow anyone (bootstrapping)
        return True
    return any(str(r.id) == cfg["dm_role_id"] for r in ctx.author.roles)


# ── Wipe confirmation view ────────────────────────────────────────────────────

class WipeConfirmView(discord.ui.View):
    def __init__(self, invoker_id: int):
        super().__init__(timeout=60)
        self.invoker_id = invoker_id

    @discord.ui.button(label="Yes, wipe everything", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("Only the DM who triggered this can confirm.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        self.disable_all_items()

        cfg_row = db.get_config()
        role_errors: list[str] = []

        # ── Delete active Discord poll messages ───────────────────────────────
        for get_fn, msg_key, ch_key in [
            (db.get_open_poll, "message_id", "channel_id"),
            (db.get_open_tiebreaker, "message_id", "channel_id"),
        ]:
            record = get_fn()
            if record:
                try:
                    ch = interaction.guild.get_channel(int(record[ch_key]))
                    if ch:
                        msg = await ch.fetch_message(int(record[msg_key]))
                        await msg.delete()
                except (discord.NotFound, discord.HTTPException):
                    pass

        # ── Strip roles from members, then delete the roles ──────────────────
        roles_to_delete: list[discord.Role] = []
        for role_key in ("player_role_id", "dm_role_id"):
            role_id = cfg_row.get(role_key) if cfg_row else None
            if not role_id:
                continue
            role = interaction.guild.get_role(int(role_id))
            if role:
                roles_to_delete.append(role)

        if roles_to_delete:
            async for member in interaction.guild.fetch_members(limit=None):
                member_roles = [r for r in roles_to_delete if r in member.roles]
                if not member_roles:
                    continue
                try:
                    await member.remove_roles(*member_roles, reason="Campaign wipe")
                except discord.Forbidden:
                    role_errors.extend(r.name for r in member_roles)
                    break
                except discord.HTTPException:
                    pass

            for role in roles_to_delete:
                try:
                    await role.delete(reason="Campaign wipe")
                except (discord.Forbidden, discord.HTTPException):
                    role_errors.append(f"{role.name} (delete failed)")

        # ── Delete category and all its channels ──────────────────────────────
        category_id = cfg_row.get("category_id") if cfg_row else None
        if category_id:
            category = interaction.guild.get_channel(int(category_id))
            if category and isinstance(category, discord.CategoryChannel):
                for ch in list(category.channels):
                    try:
                        await ch.delete(reason="Campaign wipe")
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                try:
                    await category.delete(reason="Campaign wipe")
                except (discord.Forbidden, discord.HTTPException):
                    pass

        # ── Wipe database ─────────────────────────────────────────────────────
        db.wipe_campaign_data(interaction.guild_id)

        if role_errors:
            msg = wipe_done() + "\n\n" + wipe_role_error(", ".join(set(role_errors)))
        else:
            msg = wipe_done()

        await interaction.followup.send(msg, ephemeral=True)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("This isn't your confirmation dialog.", ephemeral=True)
            return
        await interaction.response.edit_message(content="Wipe cancelled.", view=None)
        self.stop()


# ── Cog ───────────────────────────────────────────────────────────────────────

class AdminCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    # ── /setschedule ──────────────────────────────────────────────────────────

    @discord.slash_command(
        name="setschedule",
        description="[DM] Set the regular session day and optional start time.",
    )
    @option("day", description="Day of the week", choices=DAYS)
    @option("time", description='Start time, e.g. "7pm" or "7:30 PM" (optional)', required=False)
    async def setschedule(
        self,
        ctx: discord.ApplicationContext,
        day: str,
        time: str = None,
    ):
        if not _has_dm_role(ctx):
            await ctx.respond("❌ Only the DM can use this command.", ephemeral=True)
            return

        day_int = DAYS.index(day)
        kwargs: dict = {"session_day": day_int}
        if time:
            kwargs["session_time"] = time
        db.upsert_config(ctx.guild_id, **kwargs)
        await ctx.respond(schedule_set(day, time), ephemeral=True)

    # ── /setday (quick shortcut) ───────────────────────────────────────────────

    @discord.slash_command(name="setday", description="[DM] Quickly change the regular session day.")
    @option("day", description="Day of the week", choices=DAYS)
    async def setday(self, ctx: discord.ApplicationContext, day: str):
        if not _has_dm_role(ctx):
            await ctx.respond("❌ Only the DM can use this command.", ephemeral=True)
            return

        day_int = DAYS.index(day)
        db.upsert_config(ctx.guild_id, session_day=day_int)
        await ctx.respond(f"✅ Regular session day updated to **{day}**.", ephemeral=True)

    # ── /setchannel ───────────────────────────────────────────────────────────

    @discord.slash_command(
        name="setchannel",
        description="[DM] Set a channel for polls or DM notifications.",
    )
    @option("channel_type", description="Which channel to set", choices=["poll", "dm_notify"])
    @option("channel", description="The channel to use", type=discord.TextChannel)
    async def setchannel(
        self,
        ctx: discord.ApplicationContext,
        channel_type: str,
        channel: discord.TextChannel,
    ):
        if not _has_dm_role(ctx):
            await ctx.respond("❌ Only the DM can use this command.", ephemeral=True)
            return

        col = "poll_channel_id" if channel_type == "poll" else "dm_channel_id"
        db.upsert_config(ctx.guild_id, **{col: channel.id})
        label = "Poll channel" if channel_type == "poll" else "DM notification channel"
        await ctx.respond(f"✅ {label} set to {channel.mention}.", ephemeral=True)

    # ── /setrole ──────────────────────────────────────────────────────────────

    @discord.slash_command(
        name="setrole",
        description="[DM] Set the Discord role that identifies all D&D players.",
    )
    @option("role", description="The player role", type=discord.Role)
    async def setrole(self, ctx: discord.ApplicationContext, role: discord.Role):
        if not _has_dm_role(ctx):
            await ctx.respond("❌ Only the DM can use this command.", ephemeral=True)
            return

        db.upsert_config(ctx.guild_id, player_role_id=role.id)
        await ctx.respond(
            f"✅ Player role set to {role.mention}. Anyone with this role "
            "(including the DM) counts toward poll consensus.",
            ephemeral=True,
        )

    # ── /setdmrole ────────────────────────────────────────────────────────────

    @discord.slash_command(
        name="setdmrole",
        description="Set the role that grants access to admin commands.",
    )
    @option("role", description="The DM role", type=discord.Role)
    async def setdmrole(self, ctx: discord.ApplicationContext, role: discord.Role):
        if not _has_dm_role(ctx):
            await ctx.respond("❌ Only the DM can use this command.", ephemeral=True)
            return

        db.upsert_config(ctx.guild_id, dm_role_id=role.id)
        await ctx.respond(
            f"✅ DM role set to {role.mention}. Only members with this role "
            "can run admin commands.",
            ephemeral=True,
        )

    # ── /setvoicechannel ──────────────────────────────────────────────────────

    @discord.slash_command(
        name="setvoicechannel",
        description="[DM] Set the voice channel used for Discord Scheduled Events.",
    )
    @option("channel", description="The voice channel to use", type=discord.VoiceChannel)
    async def setvoicechannel(self, ctx: discord.ApplicationContext, channel: discord.VoiceChannel):
        if not _has_dm_role(ctx):
            await ctx.respond("❌ Only the DM can use this command.", ephemeral=True)
            return
        db.upsert_config(ctx.guild_id, voice_channel_id=channel.id)
        await ctx.respond(
            f"✅ Voice channel set to **{channel.name}**. "
            "A Discord event will be created here when the poll closes.",
            ephemeral=True,
        )

    # ── /settimezone ──────────────────────────────────────────────────────────

    @discord.slash_command(
        name="settimezone",
        description="[DM] Set the UTC offset for scheduling, or detect it from the server's clock.",
    )
    @option("offset", description="UTC offset in whole hours, e.g. -5 or 1", type=int, required=False)
    @option("detect", description="Auto-detect from the server's local clock", type=bool, required=False, default=False)
    async def settimezone(self, ctx: discord.ApplicationContext, offset: int = None, detect: bool = False):
        if not _has_dm_role(ctx):
            await ctx.respond("❌ Only the DM can use this command.", ephemeral=True)
            return

        if detect:
            from datetime import datetime
            local_utcoffset = datetime.now().astimezone().utcoffset()
            offset = int(local_utcoffset.total_seconds() / 3600)
        elif offset is None:
            await ctx.respond(
                "❌ Provide an `offset` (e.g. `-5`) or set `detect: True` to use the server clock.",
                ephemeral=True,
            )
            return

        if not -12 <= offset <= 14:
            await ctx.respond("❌ Offset must be between -12 and +14.", ephemeral=True)
            return

        db.upsert_config(ctx.guild_id, timezone_offset=offset)
        sign = "+" if offset >= 0 else ""
        source = " *(detected from server clock)*" if detect else ""
        await ctx.respond(f"✅ Timezone set to **UTC{sign}{offset}**{source}.", ephemeral=True)

    # ── /setduration ──────────────────────────────────────────────────────────

    @discord.slash_command(
        name="setduration",
        description="[DM] Set the minimum session length in hours (used for event end time).",
    )
    @option("hours", description="Session length in hours, e.g. 3 or 3.5", type=str)
    async def setduration(self, ctx: discord.ApplicationContext, hours: str):
        if not _has_dm_role(ctx):
            await ctx.respond("❌ Only the DM can use this command.", ephemeral=True)
            return
        try:
            h = float(hours)
            if h <= 0 or h > 24:
                raise ValueError
        except ValueError:
            await ctx.respond("❌ Please provide a valid number of hours (e.g. `3` or `3.5`).", ephemeral=True)
            return
        db.upsert_config(ctx.guild_id, min_session_hours=h)
        await ctx.respond(f"✅ Minimum session duration set to **{h} hour(s)**.", ephemeral=True)

    # ── /startpoll ────────────────────────────────────────────────────────────

    @discord.slash_command(
        name="startpoll",
        description="[DM] Open this week's availability poll.",
    )
    async def startpoll(self, ctx: discord.ApplicationContext):
        if not _has_dm_role(ctx):
            await ctx.respond("❌ Only the DM can use this command.", ephemeral=True)
            return
        if not db.is_configured():
            await ctx.respond("❌ Run `/init` to configure the bot before starting a poll.", ephemeral=True)
            return
        if db.get_open_poll():
            await ctx.respond("There is already an open poll this week.", ephemeral=True)
            return

        week = week_start_str()
        db.create_poll(0, 0, ctx.author.id, week)
        msg = await post_new_poll(ctx.guild, ctx.author.id, week)

        cfg_row = db.get_config()
        poll_channel_id = cfg_row.get("poll_channel_id") if cfg_row else None
        channel_mention = f"<#{poll_channel_id}>" if poll_channel_id else "the poll channel"
        if msg:
            await ctx.respond(f"✅ Poll posted in {channel_mention}!", ephemeral=True)
        else:
            await ctx.respond(
                "⚠️ Poll created but couldn't post the message — check that the poll channel is configured.",
                ephemeral=True,
            )

    # ── /resetpoll ────────────────────────────────────────────────────────────

    @discord.slash_command(
        name="resetpoll",
        description="[DM] Cancel and delete the current scheduling poll.",
    )
    async def resetpoll(self, ctx: discord.ApplicationContext):
        if not _has_dm_role(ctx):
            await ctx.respond("❌ Only the DM can use this command.", ephemeral=True)
            return
        if not db.is_configured():
            await ctx.respond("❌ Run `/init` to configure the bot first.", ephemeral=True)
            return

        poll = db.get_open_poll()
        if not poll:
            await ctx.respond("There is no active poll to reset.", ephemeral=True)
            return

        try:
            channel = ctx.guild.get_channel(int(poll["channel_id"]))
            if channel:
                msg = await channel.fetch_message(int(poll["message_id"]))
                await msg.delete()
        except (discord.NotFound, discord.HTTPException):
            pass

        db.close_poll("admin_reset")
        await ctx.respond(resetpoll_done(), ephemeral=True)

    # ── /wipe ─────────────────────────────────────────────────────────────────

    @discord.slash_command(
        name="wipe",
        description="[DM] End-of-campaign reset: removes all roles, session data, and character names.",
    )
    async def wipe(self, ctx: discord.ApplicationContext):
        if not _has_dm_role(ctx):
            await ctx.respond("❌ Only the DM can use this command.", ephemeral=True)
            return
        if not db.is_configured():
            await ctx.respond("❌ Nothing to wipe — the bot hasn't been set up yet. Run `/init` first.", ephemeral=True)
            return

        view = WipeConfirmView(ctx.author.id)
        await ctx.respond(wipe_confirm_prompt(), view=view, ephemeral=True)

    # ── /status ───────────────────────────────────────────────────────────────

    @discord.slash_command(name="status", description="Show bot configuration and current poll state.")
    async def status(self, ctx: discord.ApplicationContext):
        cfg = db.get_config()
        if not cfg:
            await ctx.respond(
                "⚠️ Bot not configured yet. Run `/setschedule`, `/setchannel`, `/setrole`, "
                "and `/setdmrole` to get started.",
                ephemeral=True,
            )
            return

        session_day_name = day_int_to_name(cfg["session_day"])
        next_sess = next_session_date(cfg["session_day"])
        session_time = cfg.get("session_time") or "*(not set)*"

        poll_ch    = f"<#{cfg['poll_channel_id']}>"  if cfg.get("poll_channel_id")  else "*(not set)*"
        dm_ch      = f"<#{cfg['dm_channel_id']}>"    if cfg.get("dm_channel_id")    else "*(not set)*"
        p_role     = f"<@&{cfg['player_role_id']}>"  if cfg.get("player_role_id")   else "*(not set)*"
        dm_role    = f"<@&{cfg['dm_role_id']}>"      if cfg.get("dm_role_id")       else "*(not set)*"
        voice_ch   = f"<#{cfg['voice_channel_id']}>" if cfg.get("voice_channel_id") else "*(not set)*"
        tz_offset  = int(cfg.get("timezone_offset") or 0)
        tz_str     = f"UTC{'+' if tz_offset >= 0 else ''}{tz_offset}"
        duration   = float(cfg.get("min_session_hours") or 2.0)

        embed = discord.Embed(title="📋 D&D Bot Status", color=discord.Color.blurple())
        embed.add_field(
            name="Regular session",
            value=f"{session_day_name}s at {session_time} (next: {next_sess.strftime('%b %-d')})",
            inline=False,
        )
        embed.add_field(name="Poll channel",       value=poll_ch,   inline=True)
        embed.add_field(name="DM notify channel",  value=dm_ch,     inline=True)
        embed.add_field(name="Player role",        value=p_role,    inline=True)
        embed.add_field(name="DM role",            value=dm_role,   inline=True)
        embed.add_field(name="Voice channel",      value=voice_ch,  inline=True)
        embed.add_field(name="Timezone",           value=tz_str,    inline=True)
        embed.add_field(name="Min session length", value=f"{duration}h", inline=True)

        # Day blocks
        blocked = db.get_blocked_days_for_guild(ctx.guild_id)
        if blocked:
            lines = []
            for day_int, uids in sorted(blocked.items()):
                names = []
                for uid in uids:
                    m = ctx.guild.get_member(int(uid))
                    names.append(m.display_name if m else f"<@{uid}>")
                lines.append(f"**{day_int_to_name(day_int)}**: {', '.join(names)}")
            embed.add_field(name="Blocked days", value="\n".join(lines), inline=False)

        poll = db.get_open_poll()
        if poll:
            votes = db.get_votes_for_poll(1)
            total = sum(len(v) for v in votes.values())
            embed.add_field(
                name="Active poll",
                value=f"Open — week of {poll['week_start']}, {total} vote(s) cast",
                inline=False,
            )
        else:
            embed.add_field(name="Active poll", value="None", inline=False)

        await ctx.respond(embed=embed, ephemeral=True)


def setup(bot: discord.Bot):
    bot.add_cog(AdminCog(bot))
