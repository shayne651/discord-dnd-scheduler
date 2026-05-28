"""
cogs/setup.py — One-time initialization wizard.

  /init — Guided setup. Blocked once the server is already configured.
          Re-enabled after /wipe.

Flow:
  1. WelcomeView         → overview + Start button
  2. RoleChoiceView      → create new roles OR pick existing
     2a. ExistingRoleView → two RoleSelects (if using existing)
  3. UserAssignView      → assign DM / PC roles to members (optional)
  4. ScheduleConfigModal → session day, time, timezone, duration, category name
  → _run_setup()         → creates roles, channels, permissions, saves config
"""

from __future__ import annotations
from datetime import datetime

import discord
from discord.ext import commands

import config
import database as db
from utils.dates import DAY_NAMES


def _is_initialized() -> bool:
    row = db.get_config()
    return bool(row and row.get("initialized_at"))


# ── Embeds ────────────────────────────────────────────────────────────────────

def _welcome_embed() -> discord.Embed:
    return discord.Embed(
        title="🎲 D&D Bot Setup Wizard",
        description=(
            "Let's get your server configured. This wizard will:\n\n"
            "**1.** Set up **DM** and **PC** roles\n"
            "**2.** Assign those roles to your players\n"
            "**3.** Create a **D&D category** with text and voice channels\n"
            "**4.** Configure your **session schedule** and timezone\n\n"
            "You can change anything later with individual `/set…` commands."
        ),
        color=discord.Color.gold(),
    )


def _role_choice_embed() -> discord.Embed:
    return discord.Embed(
        title="🎭 Step 1 — Roles",
        description=(
            "Should I **create new** Dungeon Master and PC roles, "
            "or do you want to **pick existing** roles from your server?"
        ),
        color=discord.Color.blurple(),
    )


def _existing_role_embed() -> discord.Embed:
    return discord.Embed(
        title="🔍 Step 1 — Select Existing Roles",
        description="Pick the roles that correspond to **DM** and **PC** (player characters).",
        color=discord.Color.blurple(),
    )


def _user_assign_embed() -> discord.Embed:
    return discord.Embed(
        title="👥 Step 2 — Assign Roles to Players",
        description=(
            "Select who gets the **DM** role and who gets the **PC** role.\n"
            "*(Both selectors are optional — you can assign roles in Discord manually later.)*"
        ),
        color=discord.Color.blurple(),
    )


# ── Step 1: Welcome ───────────────────────────────────────────────────────────

class WelcomeView(discord.ui.View):
    def __init__(self, ctx: discord.ApplicationContext):
        super().__init__(timeout=600)
        self.ctx = ctx

    @discord.ui.button(label="Start Setup →", style=discord.ButtonStyle.primary, emoji="🎲")
    async def start(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.edit_message(
            embed=_role_choice_embed(),
            view=RoleChoiceView(self.ctx),
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.edit_message(content="Setup cancelled.", embed=None, view=None)
        self.stop()


# ── Step 1a: Role choice ──────────────────────────────────────────────────────

class RoleChoiceView(discord.ui.View):
    def __init__(self, ctx: discord.ApplicationContext):
        super().__init__(timeout=600)
        self.ctx = ctx

    @discord.ui.button(label="✨ Create new roles", style=discord.ButtonStyle.primary)
    async def create_new(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.edit_message(
            embed=_user_assign_embed(),
            view=UserAssignView(self.ctx, create_roles=True),
        )

    @discord.ui.button(label="🔍 Use existing roles", style=discord.ButtonStyle.secondary)
    async def use_existing(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.edit_message(
            embed=_existing_role_embed(),
            view=ExistingRoleView(self.ctx),
        )

    @discord.ui.button(label="← Back", style=discord.ButtonStyle.secondary)
    async def back(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=_welcome_embed(), view=WelcomeView(self.ctx))


# ── Step 1b: Existing role pickers ────────────────────────────────────────────

class ExistingRoleView(discord.ui.View):
    def __init__(self, ctx: discord.ApplicationContext):
        super().__init__(timeout=600)
        self.ctx = ctx
        self.dm_role_id: int | None = None
        self.pc_role_id: int | None = None

    @discord.ui.role_select(placeholder="Select DM role", min_values=1, max_values=1, row=0)
    async def dm_role_select(self, select: discord.ui.RoleSelect, interaction: discord.Interaction):
        self.dm_role_id = select.values[0].id
        await interaction.response.defer()

    @discord.ui.role_select(placeholder="Select PC (player) role", min_values=1, max_values=1, row=1)
    async def pc_role_select(self, select: discord.ui.RoleSelect, interaction: discord.Interaction):
        self.pc_role_id = select.values[0].id
        await interaction.response.defer()

    @discord.ui.button(label="Next →", style=discord.ButtonStyle.primary, row=2)
    async def next_btn(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not self.dm_role_id or not self.pc_role_id:
            await interaction.response.send_message(
                "Please select both a DM role and a PC role first.", ephemeral=True
            )
            return
        await interaction.response.edit_message(
            embed=_user_assign_embed(),
            view=UserAssignView(self.ctx, create_roles=False, dm_role_id=self.dm_role_id, pc_role_id=self.pc_role_id),
        )

    @discord.ui.button(label="← Back", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=_role_choice_embed(), view=RoleChoiceView(self.ctx))


# ── Step 2: User assignment ───────────────────────────────────────────────────

class UserAssignView(discord.ui.View):
    def __init__(
        self,
        ctx: discord.ApplicationContext,
        create_roles: bool = True,
        dm_role_id: int | None = None,
        pc_role_id: int | None = None,
    ):
        super().__init__(timeout=600)
        self.ctx = ctx
        self.create_roles = create_roles
        self.dm_role_id = dm_role_id
        self.pc_role_id = pc_role_id
        self.dm_user_ids: list[int] = []
        self.pc_user_ids: list[int] = []

    @discord.ui.user_select(placeholder="DM — select one person", min_values=0, max_values=1, row=0)
    async def dm_user(self, select: discord.ui.UserSelect, interaction: discord.Interaction):
        self.dm_user_ids = [u.id for u in select.values]
        await interaction.response.defer()

    @discord.ui.user_select(placeholder="PCs — select all players", min_values=0, max_values=25, row=1)
    async def pc_users(self, select: discord.ui.UserSelect, interaction: discord.Interaction):
        self.pc_user_ids = [u.id for u in select.values]
        await interaction.response.defer()

    @discord.ui.button(label="Next →", style=discord.ButtonStyle.primary, row=2)
    async def next_btn(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.send_modal(
            ScheduleConfigModal(
                ctx=self.ctx,
                create_roles=self.create_roles,
                dm_role_id=self.dm_role_id,
                pc_role_id=self.pc_role_id,
                dm_user_ids=self.dm_user_ids,
                pc_user_ids=self.pc_user_ids,
            )
        )

    @discord.ui.button(label="← Back", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, button: discord.ui.Button, interaction: discord.Interaction):
        if self.create_roles:
            await interaction.response.edit_message(embed=_role_choice_embed(), view=RoleChoiceView(self.ctx))
        else:
            await interaction.response.edit_message(embed=_existing_role_embed(), view=ExistingRoleView(self.ctx))


# ── Step 3: Schedule config modal ─────────────────────────────────────────────

class ScheduleConfigModal(discord.ui.Modal):
    def __init__(
        self,
        ctx: discord.ApplicationContext,
        create_roles: bool,
        dm_role_id: int | None,
        pc_role_id: int | None,
        dm_user_ids: list[int],
        pc_user_ids: list[int],
    ):
        super().__init__(title="📅 Step 3 — Schedule")
        self.ctx = ctx
        self.create_roles = create_roles
        self.dm_role_id = dm_role_id
        self.pc_role_id = pc_role_id
        self.dm_user_ids = dm_user_ids
        self.pc_user_ids = pc_user_ids

        self.add_item(discord.ui.InputText(
            label="Session day",
            placeholder="e.g. Saturday",
            value="Saturday",
            max_length=20,
        ))
        self.add_item(discord.ui.InputText(
            label="Session start time",
            placeholder="e.g. 7pm or 19:00",
            value="7pm",
            max_length=20,
        ))
        self.add_item(discord.ui.InputText(
            label="Timezone  (UTC offset or 'detect')",
            placeholder="-5 for EST · +1 for CET · 'detect' for server clock",
            required=False,
            max_length=10,
        ))
        self.add_item(discord.ui.InputText(
            label="Min session length (hours)",
            placeholder="e.g. 3 or 3.5",
            value="2",
            max_length=5,
        ))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        day_raw  = self.children[0].value.strip()
        time_raw = self.children[1].value.strip()
        tz_raw   = (self.children[2].value or "").strip().lower()
        dur_raw  = self.children[3].value.strip()

        day_int = next(
            (i for i, d in enumerate(DAY_NAMES) if d.lower().startswith(day_raw.lower())),
            5,
        )

        if tz_raw in ("detect", "auto", ""):
            tz_offset = int(datetime.now().astimezone().utcoffset().total_seconds() / 3600)
        else:
            try:
                tz_offset = int(tz_raw.lstrip("+"))
            except ValueError:
                tz_offset = 0

        try:
            min_hours = max(0.5, float(dur_raw))
        except ValueError:
            min_hours = 2.0

        view = ChannelConfigView(
            ctx=self.ctx,
            create_roles=self.create_roles,
            dm_role_id=self.dm_role_id,
            pc_role_id=self.pc_role_id,
            dm_user_ids=self.dm_user_ids,
            pc_user_ids=self.pc_user_ids,
            session_day=day_int,
            session_time=time_raw,
            timezone_offset=tz_offset,
            min_session_hours=min_hours,
        )
        try:
            await self.ctx.edit(
                content=(
                    "**📋 Step 4 — Channels**\n"
                    "Configure the category and channels that will be created for your campaign."
                ),
                embed=None,
                view=view,
            )
        except Exception:
            await interaction.followup.send(
                "Something went wrong moving to the channel step.", ephemeral=True
            )


# ── Step 4: Channel config view + modal ───────────────────────────────────────

class ChannelConfigView(discord.ui.View):
    def __init__(
        self,
        ctx: discord.ApplicationContext,
        create_roles: bool,
        dm_role_id: int | None,
        pc_role_id: int | None,
        dm_user_ids: list[int],
        pc_user_ids: list[int],
        session_day: int,
        session_time: str,
        timezone_offset: int,
        min_session_hours: float,
    ):
        super().__init__(timeout=600)
        self.ctx = ctx
        self.create_roles = create_roles
        self.dm_role_id = dm_role_id
        self.pc_role_id = pc_role_id
        self.dm_user_ids = dm_user_ids
        self.pc_user_ids = pc_user_ids
        self.session_day = session_day
        self.session_time = session_time
        self.timezone_offset = timezone_offset
        self.min_session_hours = min_session_hours

    def _modal(self) -> "ChannelConfigModal":
        return ChannelConfigModal(
            ctx=self.ctx,
            create_roles=self.create_roles,
            dm_role_id=self.dm_role_id,
            pc_role_id=self.pc_role_id,
            dm_user_ids=self.dm_user_ids,
            pc_user_ids=self.pc_user_ids,
            session_day=self.session_day,
            session_time=self.session_time,
            timezone_offset=self.timezone_offset,
            min_session_hours=self.min_session_hours,
        )

    @discord.ui.button(label="Configure Channels →", style=discord.ButtonStyle.primary)
    async def configure(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.send_modal(self._modal())
        self.stop()


class ChannelConfigModal(discord.ui.Modal):
    def __init__(
        self,
        ctx: discord.ApplicationContext,
        create_roles: bool,
        dm_role_id: int | None,
        pc_role_id: int | None,
        dm_user_ids: list[int],
        pc_user_ids: list[int],
        session_day: int,
        session_time: str,
        timezone_offset: int,
        min_session_hours: float,
    ):
        super().__init__(title="📋 Step 4 — Channels")
        self.ctx = ctx
        self.create_roles = create_roles
        self.dm_role_id = dm_role_id
        self.pc_role_id = pc_role_id
        self.dm_user_ids = dm_user_ids
        self.pc_user_ids = pc_user_ids
        self.session_day = session_day
        self.session_time = session_time
        self.timezone_offset = timezone_offset
        self.min_session_hours = min_session_hours

        default_channels = "\n".join(config.EXTRA_CHANNELS)

        self.add_item(discord.ui.InputText(
            label="Category name",
            placeholder="e.g. D&D  or  Adventure Guild",
            value="D&D",
            max_length=50,
        ))
        self.add_item(discord.ui.InputText(
            label="Extra channels — one per line",
            placeholder="general\nmaps\nnotes\npictures",
            value=default_channels,
            style=discord.InputTextStyle.long,
            required=False,
            max_length=300,
        ))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        cat_raw = self.children[0].value.strip() or "D&D"
        channels_raw = (self.children[1].value or "").strip()
        extra_channel_names = [
            line.strip().lower().replace(" ", "-")
            for line in channels_raw.splitlines()
            if line.strip()
        ]

        result = await _run_setup(
            guild=interaction.guild,
            create_roles=self.create_roles,
            dm_role_id=self.dm_role_id,
            pc_role_id=self.pc_role_id,
            dm_user_ids=self.dm_user_ids,
            pc_user_ids=self.pc_user_ids,
            session_day=self.session_day,
            session_time=self.session_time,
            timezone_offset=self.timezone_offset,
            min_session_hours=self.min_session_hours,
            category_name=cat_raw,
            extra_channel_names=extra_channel_names,
        )

        try:
            await self.ctx.edit(content=result, embed=None, view=None)
        except Exception:
            await interaction.followup.send(result, ephemeral=True)


# ── Core setup logic ──────────────────────────────────────────────────────────

async def _run_setup(
    guild: discord.Guild,
    create_roles: bool,
    dm_role_id: int | None,
    pc_role_id: int | None,
    dm_user_ids: list[int],
    pc_user_ids: list[int],
    session_day: int,
    session_time: str,
    timezone_offset: int,
    min_session_hours: float,
    category_name: str,
    extra_channel_names: list[str],
) -> str:
    errors: list[str] = []

    # 1. Roles
    dm_role: discord.Role | None = None
    pc_role: discord.Role | None = None

    if create_roles:
        try:
            dm_role = await guild.create_role(name="Dungeon Master", color=discord.Color.red(), mentionable=True)
            pc_role = await guild.create_role(name="PC", color=discord.Color.blue(), mentionable=True)
            dm_role_id = dm_role.id
            pc_role_id = pc_role.id
        except discord.Forbidden:
            errors.append("⚠️ Couldn't create roles — grant the bot **Manage Roles** permission.")
    else:
        dm_role = guild.get_role(dm_role_id) if dm_role_id else None
        pc_role = guild.get_role(pc_role_id) if pc_role_id else None

    # 2. Assign roles to members
    for uid, role, label in [
        *[(uid, dm_role, "DM") for uid in dm_user_ids],
        *[(uid, pc_role, "PC") for uid in pc_user_ids],
    ]:
        if not role:
            continue
        try:
            member = guild.get_member(uid) or await guild.fetch_member(uid)
        except discord.NotFound:
            continue
        try:
            await member.add_roles(role)
        except discord.Forbidden:
            errors.append(
                f"⚠️ Couldn't assign {label} role to **{member.display_name}** — "
                "grant the bot **Manage Roles** and ensure its role is above the roles it creates."
            )

    # 3. Create channels
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }
    if dm_role:
        overwrites[dm_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            connect=True, speak=True,
        )
    if pc_role:
        overwrites[pc_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            connect=True, speak=True,
        )

    text_channel: discord.TextChannel | None = None
    voice_channel: discord.VoiceChannel | None = None
    extra_channels: list[discord.TextChannel] = []
    try:
        category = await guild.create_category(category_name, overwrites=overwrites)
        text_channel = await guild.create_text_channel(
            "scheduling", category=category, overwrites=overwrites,
            topic="Session scheduling — use /cantmake to declare availability.",
        )
        for ch_name in extra_channel_names:
            ch = await guild.create_text_channel(ch_name, category=category, overwrites=overwrites)
            extra_channels.append(ch)
        voice_channel = await guild.create_voice_channel(
            "D&D Session", category=category, overwrites=overwrites,
        )
    except discord.Forbidden:
        errors.append("⚠️ Couldn't create channels — grant the bot **Manage Channels** permission.")

    # 4. Save config
    cfg: dict = {
        "session_day": session_day,
        "session_time": session_time,
        "timezone_offset": timezone_offset,
        "min_session_hours": min_session_hours,
        "category_name": category_name,
        "initialized_at": datetime.utcnow().isoformat(),
    }
    if dm_role_id:
        cfg["dm_role_id"] = dm_role_id
    if pc_role_id:
        cfg["player_role_id"] = pc_role_id
    if text_channel:
        cfg["poll_channel_id"] = text_channel.id
        cfg["dm_channel_id"] = text_channel.id
    if voice_channel:
        cfg["voice_channel_id"] = voice_channel.id
    if text_channel:  # category is always created when text_channel is
        cfg["category_id"] = text_channel.category_id

    db.upsert_config(guild.id, **cfg)

    # 5. Build summary
    sign = "+" if timezone_offset >= 0 else ""
    lines = ["✅ **D&D Bot is ready!**\n"]

    lines.append("📋 **Channels**")
    lines.append(f"• {text_channel.mention} — polls & announcements" if text_channel else "• *(channel creation failed)*")
    for ch in extra_channels:
        lines.append(f"• {ch.mention}")
    lines.append(f"• **{voice_channel.name}** — voice channel for sessions\n" if voice_channel else "• *(voice channel creation failed)*\n")

    lines.append("🎭 **Roles**")
    lines.append(f"• {dm_role.mention} — Dungeon Master" if dm_role else "• *(DM role not configured)*")
    lines.append(f"• {pc_role.mention} — Players\n" if pc_role else "• *(PC role not configured)*\n")

    if dm_user_ids or pc_user_ids:
        lines.append("👤 **Role assignments**")
        for uid in dm_user_ids:
            m = guild.get_member(uid)
            if m:
                lines.append(f"• {m.display_name} → Dungeon Master")
        for uid in pc_user_ids:
            m = guild.get_member(uid)
            if m:
                lines.append(f"• {m.display_name} → PC")
        lines.append("")

    lines.append("📅 **Schedule**")
    lines.append(f"• Session: **{DAY_NAMES[session_day]}s at {session_time}**")
    lines.append(f"• Timezone: **UTC{sign}{timezone_offset}**")
    lines.append(f"• Min session length: **{min_session_hours}h**")

    if errors:
        lines += ["", "**Warnings:**"] + errors
        lines.append("Fix the permissions above and re-run `/init`, or use individual commands to finish setup.")

    return "\n".join(lines)


# ── Cog ───────────────────────────────────────────────────────────────────────

class SetupCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @discord.slash_command(
        name="init",
        description="First-time setup wizard. Creates channels, roles, and configures the bot.",
    )
    async def init(self, ctx: discord.ApplicationContext):
        if _is_initialized():
            await ctx.respond(
                "⚠️ This server is already configured.\n"
                "Use `/wipe` to reset the campaign, then `/init` to start fresh.",
                ephemeral=True,
            )
            return

        await ctx.respond(embed=_welcome_embed(), view=WelcomeView(ctx), ephemeral=True)


def setup(bot: discord.Bot):
    bot.add_cog(SetupCog(bot))
