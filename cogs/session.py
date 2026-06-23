"""
cogs/session.py — Record D&D sessions for offline transcription.

Commands:
  /session start — join the configured session voice channel and start
                   recording one WAV file per speaker
  /session end   — stop recording, save the files + a manifest.json, and
                   (optionally) notify a transcription webhook

Transcription itself is out of scope — this cog only captures speaker-
separated audio and hands the files off.
"""

import json
import re
import wave
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands

import config
import database as db
from utils.messages import (
    session_already_recording, session_ended_summary, session_error_voice_connect,
    session_no_voice_channel, session_not_recording, session_started, session_stopping,
)

# Discord voice is always decoded to 48kHz 16-bit stereo PCM.
PCM_SAMPLE_RATE = 48000
PCM_CHANNELS = 2
PCM_SAMPLE_WIDTH = 2  # bytes

# py-cord 2.8's voice-receive rewrite added a SinkEventRouter/PacketDecoder that
# expect every Sink to define __sink_listeners__, walk_children(), and is_opus(),
# but the legacy Sink/WaveSink classes we use were never updated with them —
# start_recording() raises AttributeError before any audio capture starts. The
# legacy sinks never dispatch sink events and always want decoded PCM (they all
# feed raw s16le into wave/ffmpeg), so it's safe to fill these in as empty/False.
# Drop this once pycord ships a fix for
# https://github.com/Pycord-Development/pycord/issues/3139.
if not hasattr(discord.sinks.Sink, "__sink_listeners__"):
    discord.sinks.Sink.__sink_listeners__ = ()
    discord.sinks.Sink.walk_children = lambda self: ()
    discord.sinks.Sink.is_opus = lambda self: False


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", name) or "unknown"


def _write_wav(path: Path, pcm_bytes: bytes) -> None:
    """Write raw 48kHz/16-bit/stereo PCM to a proper .wav file.

    py-cord's own Sink.cleanup()/format_audio() path isn't wired up to the
    current VoiceClient (it references attributes that no longer exist), so
    we finalize the recording ourselves instead of relying on it.
    """
    with wave.open(str(path), "wb") as f:
        f.setnchannels(PCM_CHANNELS)
        f.setsampwidth(PCM_SAMPLE_WIDTH)
        f.setframerate(PCM_SAMPLE_RATE)
        f.writeframes(pcm_bytes)


class SessionCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        # guild_id -> {voice_client, sink, session_id, session_number, started_at, reader_error}
        self.active: dict[int, dict] = {}

    session = discord.SlashCommandGroup("session", "Record D&D sessions for transcription")

    @session.command(name="start", description="Join the session voice channel and start recording")
    async def start(self, ctx: discord.ApplicationContext):
        guild_id = ctx.guild_id

        if guild_id in self.active or db.get_active_session_recording(guild_id):
            await ctx.respond(session_already_recording(), ephemeral=True)
            return

        cfg = db.get_config()
        voice_channel_id = cfg.get("voice_channel_id") if cfg else None
        if not voice_channel_id:
            await ctx.respond(session_no_voice_channel(), ephemeral=True)
            return

        channel = ctx.guild.get_channel(int(voice_channel_id))
        if not isinstance(channel, discord.VoiceChannel):
            await ctx.respond(session_no_voice_channel(), ephemeral=True)
            return

        await ctx.response.defer(ephemeral=True)

        try:
            vc = await channel.connect()
        except Exception as e:
            print(f"[Session] Voice connect failed: {e}")
            await ctx.followup.send(session_error_voice_connect(), ephemeral=True)
            return

        state: dict = {"reader_error": None}

        def after(error: Exception | None):
            state["reader_error"] = error

        sink = discord.sinks.WaveSink()
        try:
            vc.start_recording(sink, after)
        except Exception as e:
            print(f"[Session] start_recording failed: {e}")
            await vc.disconnect()
            await ctx.followup.send(session_error_voice_connect(), ephemeral=True)
            return

        session_id, session_number = db.start_session_recording(guild_id, channel.id, ctx.author.id)

        state.update({
            "voice_client": vc,
            "sink": sink,
            "session_id": session_id,
            "session_number": session_number,
            "started_at": datetime.now(timezone.utc),
        })
        self.active[guild_id] = state

        await ctx.followup.send(session_started(channel.name, session_number), ephemeral=True)

    @session.command(name="end", description="Stop recording and save the session's audio")
    async def end(self, ctx: discord.ApplicationContext):
        guild_id = ctx.guild_id
        state = self.active.pop(guild_id, None)
        if not state:
            await ctx.respond(session_not_recording(), ephemeral=True)
            return

        await ctx.respond(session_stopping(), ephemeral=True)

        vc: discord.VoiceClient = state["voice_client"]
        sink = state["sink"]
        session_id = state["session_id"]
        session_number = state["session_number"]
        started_at: datetime = state["started_at"]

        try:
            vc.stop_recording()
        except Exception as e:
            print(f"[Session] stop_recording failed: {e}")

        if state.get("reader_error"):
            print(f"[Session] Audio reader reported an error: {state['reader_error']}")

        try:
            folder = self._session_folder(guild_id, session_number, started_at)
            files_meta = self._write_speaker_files(sink, ctx.guild, folder)
            self._write_manifest(folder, session_number, ctx.guild, ctx.author, started_at, files_meta)
        except Exception as e:
            print(f"[Session] Failed to save recording files: {e}")
            db.fail_session_recording(session_id)
            await vc.disconnect()
            await ctx.followup.send(session_error_voice_connect(), ephemeral=True)
            return

        await vc.disconnect()

        relative_folder = folder.relative_to(Path(config.RECORDINGS_DIR).resolve())
        db.complete_session_recording(session_id, str(relative_folder))

        webhook_ok = None
        if config.TRANSCRIBE_WEBHOOK_URL:
            webhook_ok = await self._notify_webhook(session_number, started_at, relative_folder, files_meta)

        duration = datetime.now(timezone.utc) - started_at
        minutes, seconds = divmod(int(duration.total_seconds()), 60)
        duration_str = f"{minutes}m {seconds}s"

        await ctx.followup.send(
            session_ended_summary(session_number, len(files_meta), duration_str, webhook_ok),
            ephemeral=True,
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    def _session_folder(self, guild_id: int, session_number: int, started_at: datetime) -> Path:
        folder = (
            Path(config.RECORDINGS_DIR).resolve()
            / str(guild_id)
            / f"session-{session_number:04d}-{started_at:%Y%m%d}"
        )
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _write_speaker_files(self, sink, guild: discord.Guild, folder: Path) -> list[dict]:
        files_meta = []
        for user_id, audio_data in sink.audio_data.items():
            member = guild.get_member(user_id)
            display_name = member.display_name if member else f"user-{user_id}"

            buf = audio_data.file
            buf.seek(0)
            pcm_bytes = buf.read()

            filename = f"{_safe_name(display_name)}_{user_id}.wav"
            _write_wav(folder / filename, pcm_bytes)

            files_meta.append({
                "user_id": str(user_id),
                "display_name": display_name,
                "filename": filename,
            })
        return files_meta

    def _write_manifest(
        self,
        folder: Path,
        session_number: int,
        guild: discord.Guild,
        started_by: discord.Member,
        started_at: datetime,
        files_meta: list[dict],
    ) -> None:
        manifest = {
            "session_number": session_number,
            "guild_id": str(guild.id),
            "guild_name": guild.name,
            "started_by": str(started_by.id),
            "started_by_name": started_by.display_name,
            "started_at": started_at.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "files": files_meta,
        }
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2))

    async def _notify_webhook(
        self, session_number: int, started_at: datetime, relative_folder: Path, files_meta: list[dict]
    ) -> bool:
        payload = {
            "session_number": session_number,
            "date": started_at.date().isoformat(),
            "folder": str(relative_folder),
            "files": files_meta,
        }
        headers = {}
        if config.TRANSCRIBE_WEBHOOK_SECRET:
            headers["Authorization"] = f"Bearer {config.TRANSCRIBE_WEBHOOK_SECRET}"

        try:
            async with aiohttp.ClientSession() as http:
                async with http.post(
                    config.TRANSCRIBE_WEBHOOK_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status >= 400:
                        print(f"[Session] Webhook returned HTTP {resp.status}")
                        return False
                    return True
        except Exception as e:
            print(f"[Session] Webhook notify failed: {e}")
            return False


def setup(bot: discord.Bot):
    bot.add_cog(SessionCog(bot))
