"""
utils/messages.py — Centralised message string templates.
"""

from datetime import date
from utils.dates import friendly_date, next_session_date


def poll_footer() -> str:
    return "Vote for every day that works for you — you can pick multiple!"


def poll_title(week_of: str) -> str:
    return f"📅 D&D Scheduling Poll — Week of {week_of}"


def endtime_dm_notification(username: str, end_time: str) -> str:
    return f"⏰ **{username}** needs to end by **{end_time}** this session. Plan accordingly."


def endtime_confirmed() -> str:
    return "✅ Your end time has been shared with the DM channel."


def cantmake_already_submitted() -> str:
    return "You've already submitted your availability for this week's poll. The poll has been updated."


def poll_created(username: str) -> str:
    return f"Got it, **{username}**! A scheduling poll has been posted."


def poll_cancelled_no_days(next_date: date) -> str:
    return (
        f"❌ **D&D is off this week.** "
        f"See you next session on **{friendly_date(next_date)}**!"
    )


def no_days_confirm() -> str:
    return (
        "⚠️ Are you sure? Clicking **Confirm** will cancel D&D for this week "
        "and notify everyone."
    )


def no_consensus_nudge(role_mention: str) -> str:
    return (
        f"{role_mention} Everyone has voted but no single day works for everyone. "
        "Please vote for any additional days you could make if needed!"
    )


def tiebreaker_intro(days: list[str]) -> str:
    day_list = ", ".join(f"**{d}**" for d in days)
    return f"🎲 Multiple days work for everyone ({day_list})! Vote for your **preferred** day:"


def tiebreaker_result(day_name: str, was_random: bool) -> str:
    suffix = " *(chosen randomly from a tie)*" if was_random else ""
    return f"🎉 D&D is scheduled for **{day_name}** this week!{suffix}"


def poll_closed_winner(day_name: str, event_url: str | None = None) -> str:
    msg = f"✅ Everyone's in for **{day_name}**! See you then."
    if event_url:
        msg += f"\n📅 [View event & get notified]({event_url})"
    return msg


def resetpoll_done() -> str:
    return "🗑️ The current poll has been cancelled and cleared."


# ── day blocks ────────────────────────────────────────────────────────────────

def blockday_added(day_name: str) -> str:
    return (
        f"🚫 **{day_name}** has been blocked. It will be excluded from future polls. "
        "Use `/unblockday` to remove the block."
    )


def blockday_already(day_name: str) -> str:
    return f"You already have **{day_name}** blocked."


def unblockday_removed(day_name: str) -> str:
    return f"✅ **{day_name}** is unblocked and will appear in future polls again."


def unblockday_not_found(day_name: str) -> str:
    return f"You don't have **{day_name}** blocked."


def blockday_poll_updated(day_name: str) -> str:
    return f"The current poll has been updated to reflect your **{day_name}** block."


# ── character nicknames ───────────────────────────────────────────────────────

def nick_set(char_name: str) -> str:
    return (
        f"⚔️ Character name set to **{char_name}**. "
        "It will appear next to your name in scheduling polls."
    )


def nick_cleared() -> str:
    return "Your character name has been cleared."


# ── wipe ─────────────────────────────────────────────────────────────────────

def wipe_confirm_prompt() -> str:
    return (
        "⚠️ **This will permanently:**\n"
        "• Delete all polls, votes, and session history\n"
        "• Clear all character names\n"
        "• Clear all personal day blocks\n"
        "• Remove the **player** and **DM** roles from every member\n\n"
        "This cannot be undone. Are you sure the campaign is over?"
    )


def wipe_done() -> str:
    return (
        "✅ **Campaign wiped.** All session data, character names, and day blocks have been "
        "cleared, and roles have been removed. You're ready to start a new campaign!"
    )


def wipe_role_error(role_name: str) -> str:
    return (
        f"⚠️ Wipe complete, but couldn't remove the **{role_name}** role from some members. "
        "Make sure the bot's role is higher than the player/DM roles in Server Settings → Roles."
    )


# ── schedule ──────────────────────────────────────────────────────────────────

def schedule_set(day_name: str, time_str: str | None) -> str:
    if time_str:
        return f"✅ Regular session set to **{day_name}s at {time_str}**."
    return f"✅ Regular session day set to **{day_name}s**."


# ── session recording ────────────────────────────────────────────────────────

def session_no_voice_channel() -> str:
    return (
        "⚠️ No session voice channel is configured yet. "
        "Ask the DM to run `/setvoicechannel` first."
    )


def session_already_recording() -> str:
    return "⚠️ A session recording is already in progress. Use `/session end` to stop it."


def session_not_recording() -> str:
    return "There's no active recording to end."


def session_started(channel_name: str, session_number: int) -> str:
    return f"🔴 **Recording session #{session_number}** in **#{channel_name}**. Use `/session end` when you're done."


def session_stopping() -> str:
    return "⏳ Stopping recording and saving files..."


def session_ended_summary(
    session_number: int, speaker_count: int, duration_str: str, webhook_ok: bool | None
) -> str:
    msg = (
        f"✅ **Session #{session_number} recording saved** — "
        f"{speaker_count} speaker file(s), {duration_str}."
    )
    if webhook_ok is True:
        msg += "\nTranscription service notified."
    elif webhook_ok is False:
        msg += "\n⚠️ Couldn't notify the transcription service — you'll need to trigger it manually."
    return msg


def session_error_voice_connect() -> str:
    return (
        "⚠️ Couldn't join or record the voice channel. This is most likely a Discord "
        "voice/encryption issue on the bot's end — check the bot logs for details."
    )
