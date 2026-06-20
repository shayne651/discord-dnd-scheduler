"""
bot.py — Entry point. Run with: python bot.py
"""

import discord
from discord.ext import commands

import config
import database as db

# ── Intents ────────────────────────────────────────────────────────────────────
# We need Members intent to fetch role members for consensus detection.
intents = discord.Intents.default()
intents.members = True

bot = discord.Bot(intents=intents)


# ── Startup ────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"[Bot] Logged in as {bot.user} (id: {bot.user.id})")
    print(f"[Bot] Serving guild: {config.GUILD_ID}")

    # Seed config row on first run; only pass channel IDs if set in .env
    seed: dict = {"session_day": config.SESSION_DAY_INT}
    if config.POLL_CHANNEL_ID:
        seed["poll_channel_id"] = config.POLL_CHANNEL_ID
    if config.DM_NOTIFY_CHANNEL_ID:
        seed["dm_channel_id"] = config.DM_NOTIFY_CHANNEL_ID
    db.upsert_config(config.GUILD_ID, **seed)

    # Sync slash commands to the specific guild for instant availability
    # (global sync can take up to an hour)
    await bot.sync_commands(guild_ids=[config.GUILD_ID])
    print("[Bot] Slash commands synced.")


# ── Load cogs ──────────────────────────────────────────────────────────────────

COGS = [
    "cogs.setup",
    "cogs.admin",
    "cogs.poll",
    "cogs.availability",
    "cogs.scheduler",
]

for cog in COGS:
    bot.load_extension(cog)
    print(f"[Bot] Loaded cog: {cog}")


# ── Error handler ──────────────────────────────────────────────────────────────

@bot.event
async def on_application_command_error(ctx: discord.ApplicationContext, error: Exception):
    if isinstance(error, commands.MissingPermissions):
        await ctx.respond("❌ You don't have permission to use this command.", ephemeral=True)
    else:
        print(f"[Error] Unhandled command error: {error}")
        await ctx.respond(
            "⚠️ Something went wrong. Please try again or contact the DM.", ephemeral=True
        )
        raise error


# ── Run ─────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("[DB] Running migrations...")
    db.run_migrations()
    print("[Bot] Starting...")
    bot.run(config.BOT_TOKEN)
