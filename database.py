"""
database.py — SQLite connection, migration runner, and CRUD helpers.

Migrations live in migrations/NNN_description.sql and are applied in order.
To add a new migration: create migrations/002_your_change.sql and restart the bot.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

DB_PATH = str(Path(__file__).parent / "dnd_schedule.db")
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


# ── Connection ────────────────────────────────────────────────────────────────

@contextmanager
def get_conn():
    """Context manager that yields a connection, commits on success, rolls back on error."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Migration runner ──────────────────────────────────────────────────────────

def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               version     TEXT PRIMARY KEY,
               applied_at  TEXT DEFAULT (datetime('now'))
           )"""
    )
    conn.commit()


def run_migrations() -> None:
    """
    Apply any pending migrations from the migrations/ folder.

    File naming: NNN_description.sql  (e.g. 001_initial.sql)
    Migrations are applied in filename order. Already-applied ones are skipped.
    Each file is executed as a single transaction.
    """
    with get_conn() as conn:
        _ensure_migrations_table(conn)

    sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not sql_files:
        print("[DB] No migration files found in migrations/")
        return

    for sql_file in sql_files:
        version = sql_file.stem  # e.g. "001_initial"
        with get_conn() as conn:
            already = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version=?", (version,)
            ).fetchone()
            if already:
                continue

            print(f"[DB] Applying migration: {sql_file.name}")
            sql = sql_file.read_text()
            conn.executescript(sql)
            # executescript auto-commits, so we re-open to record the version
        with get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)", (version,)
            )

    print("[DB] Migrations up to date.")


# ── config ────────────────────────────────────────────────────────────────────

def upsert_config(guild_id: int, **kwargs) -> None:
    """Insert or update the single config row. Pass column=value kwargs to update."""
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM config WHERE id=1").fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO config (id, guild_id) VALUES (1, ?)", (str(guild_id),)
            )
        for key, value in kwargs.items():
            conn.execute(
                f"UPDATE config SET {key}=?, updated_at=datetime('now') WHERE id=1",
                (str(value) if value is not None else None,),
            )


def get_config() -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM config WHERE id=1").fetchone()
        return dict(row) if row else None


# ── helpers ───────────────────────────────────────────────────────────────────

def is_configured() -> bool:
    """True if /init has been completed and not wiped."""
    row = get_config()
    return bool(row and row.get("initialized_at"))


# ── active_poll ───────────────────────────────────────────────────────────────

def get_open_poll() -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM active_poll WHERE id=1 AND is_closed=0"
        ).fetchone()
        return dict(row) if row else None


def create_poll(message_id: int, channel_id: int, created_by: int, week_start: str) -> None:
    """Replace any existing poll row with a fresh one, seeded with all 7 days."""
    with get_conn() as conn:
        conn.execute("DELETE FROM active_poll WHERE id=1")
        conn.execute(
            """INSERT INTO active_poll (id, message_id, channel_id, created_by, week_start)
               VALUES (1, ?, ?, ?, ?)""",
            (str(message_id), str(channel_id), str(created_by), week_start),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO poll_days (poll_id, day_of_week) VALUES (1, ?)",
            [(d,) for d in range(7)],
        )


def update_poll_message_id(message_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE active_poll SET message_id=? WHERE id=1", (str(message_id),)
        )


def close_poll(reason: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE active_poll SET is_closed=1, closed_reason=? WHERE id=1", (reason,)
        )


# ── votes ─────────────────────────────────────────────────────────────────────

def toggle_vote(poll_id: int, user_id: int, day_of_week: int) -> bool:
    """Toggle a vote for a day. Returns True if ADDED, False if REMOVED."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM votes WHERE poll_id=? AND user_id=? AND day_of_week=?",
            (poll_id, str(user_id), day_of_week),
        ).fetchone()
        if existing:
            conn.execute(
                "DELETE FROM votes WHERE poll_id=? AND user_id=? AND day_of_week=?",
                (poll_id, str(user_id), day_of_week),
            )
            return False
        conn.execute(
            "INSERT INTO votes (poll_id, user_id, day_of_week) VALUES (?, ?, ?)",
            (poll_id, str(user_id), day_of_week),
        )
        return True


def get_votes_for_poll(poll_id: int) -> dict[int, list[str]]:
    """Returns {day_of_week: [user_id, ...]} for every day 0-6."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT day_of_week, user_id FROM votes WHERE poll_id=?", (poll_id,)
        ).fetchall()
    result: dict[int, list[str]] = {d: [] for d in range(7)}
    for row in rows:
        result[row["day_of_week"]].append(row["user_id"])
    return result


def get_user_votes(poll_id: int, user_id: int) -> list[int]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT day_of_week FROM votes WHERE poll_id=? AND user_id=?",
            (poll_id, str(user_id)),
        ).fetchall()
    return [r["day_of_week"] for r in rows]


# ── cant_make ─────────────────────────────────────────────────────────────────

def upsert_cant_make(poll_id: int, user_id: int, available_days: list[int]) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO cant_make (poll_id, user_id, available_days)
               VALUES (?, ?, ?)
               ON CONFLICT(poll_id, user_id) DO UPDATE SET
                 available_days=excluded.available_days,
                 declared_at=datetime('now')""",
            (poll_id, str(user_id), json.dumps(available_days)),
        )


def get_cant_make_users(poll_id: int) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id FROM cant_make WHERE poll_id=?", (poll_id,)
        ).fetchall()
    return [r["user_id"] for r in rows]


def get_cant_make_available_days(poll_id: int) -> dict[str, list[int]]:
    """Returns {user_id: [available_day_int, ...]} for all /cantmake entries."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id, available_days FROM cant_make WHERE poll_id=?", (poll_id,)
        ).fetchall()
    return {r["user_id"]: json.loads(r["available_days"]) for r in rows}


# ── day_time_windows ──────────────────────────────────────────────────────────

def upsert_day_time_windows(poll_id: int, user_id: int, windows: dict[int, tuple[int, int]]) -> None:
    """Save per-day time windows. windows = {day_of_week: (start_mins, end_mins)}"""
    with get_conn() as conn:
        for day, (start, end) in windows.items():
            conn.execute(
                """INSERT INTO day_time_windows (poll_id, user_id, day_of_week, start_mins, end_mins)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(poll_id, user_id, day_of_week) DO UPDATE SET
                     start_mins=excluded.start_mins, end_mins=excluded.end_mins""",
                (poll_id, str(user_id), day, start, end),
            )


def get_day_time_windows_for_poll(poll_id: int) -> dict[str, dict[int, tuple[int, int]]]:
    """Returns {user_id: {day_of_week: (start_mins, end_mins)}}"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id, day_of_week, start_mins, end_mins FROM day_time_windows WHERE poll_id=?",
            (poll_id,),
        ).fetchall()
    result: dict[str, dict[int, tuple[int, int]]] = {}
    for row in rows:
        result.setdefault(row["user_id"], {})[row["day_of_week"]] = (row["start_mins"], row["end_mins"])
    return result


# ── tiebreaker ────────────────────────────────────────────────────────────────

def create_tiebreaker(
    message_id: int, channel_id: int, week_start: str, tied_days: list[int]
) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM tiebreaker_poll WHERE id=1")
        conn.execute(
            """INSERT INTO tiebreaker_poll (id, week_start, message_id, channel_id, tied_days)
               VALUES (1, ?, ?, ?, ?)""",
            (week_start, str(message_id), str(channel_id), json.dumps(tied_days)),
        )


def get_open_tiebreaker() -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM tiebreaker_poll WHERE id=1 AND is_closed=0"
        ).fetchone()
        return dict(row) if row else None


def update_tiebreaker_message_id(message_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE tiebreaker_poll SET message_id=? WHERE id=1", (str(message_id),)
        )


def close_tiebreaker(winner_day: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE tiebreaker_poll SET is_closed=1, winner_day=? WHERE id=1",
            (winner_day,),
        )


def toggle_tiebreaker_vote(tb_id: int, user_id: int, day_of_week: int) -> bool:
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM tiebreaker_votes WHERE tb_id=? AND user_id=? AND day_of_week=?",
            (tb_id, str(user_id), day_of_week),
        ).fetchone()
        if existing:
            conn.execute(
                "DELETE FROM tiebreaker_votes WHERE tb_id=? AND user_id=? AND day_of_week=?",
                (tb_id, str(user_id), day_of_week),
            )
            return False
        conn.execute(
            "INSERT INTO tiebreaker_votes (tb_id, user_id, day_of_week) VALUES (?, ?, ?)",
            (tb_id, str(user_id), day_of_week),
        )
        return True


def get_tiebreaker_votes(tb_id: int) -> dict[int, list[str]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT day_of_week, user_id FROM tiebreaker_votes WHERE tb_id=?", (tb_id,)
        ).fetchall()
    result: dict[int, list[str]] = {}
    for row in rows:
        result.setdefault(row["day_of_week"], []).append(row["user_id"])
    return result


# ── end_times ─────────────────────────────────────────────────────────────────

def record_end_time(guild_id: int, user_id: int, week_start: str, end_time: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO end_times (guild_id, user_id, week_start, end_time)
               VALUES (?, ?, ?, ?)""",
            (str(guild_id), str(user_id), week_start, end_time),
        )


# ── player_day_blocks ─────────────────────────────────────────────────────────

def add_day_block(guild_id: int, user_id: int, day_of_week: int) -> bool:
    """Add a permanent day block for a player. Returns True if new, False if already existed."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM player_day_blocks WHERE guild_id=? AND user_id=? AND day_of_week=?",
            (str(guild_id), str(user_id), day_of_week),
        ).fetchone()
        if existing:
            return False
        conn.execute(
            "INSERT INTO player_day_blocks (guild_id, user_id, day_of_week) VALUES (?, ?, ?)",
            (str(guild_id), str(user_id), day_of_week),
        )
        return True


def remove_day_block(guild_id: int, user_id: int, day_of_week: int) -> bool:
    """Remove a permanent day block. Returns True if removed, False if it didn't exist."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM player_day_blocks WHERE guild_id=? AND user_id=? AND day_of_week=?",
            (str(guild_id), str(user_id), day_of_week),
        ).fetchone()
        if not existing:
            return False
        conn.execute(
            "DELETE FROM player_day_blocks WHERE guild_id=? AND user_id=? AND day_of_week=?",
            (str(guild_id), str(user_id), day_of_week),
        )
        return True


def get_blocked_days_for_guild(guild_id: int) -> dict[int, list[str]]:
    """
    Returns {day_of_week: [user_id, ...]} for every blocked day in the guild.
    Campaign-wide blocks (set via /blockcampaignday) are merged in under the
    sentinel user_id "campaign" so callers don't need to know the difference.
    """
    with get_conn() as conn:
        campaign_rows = conn.execute(
            "SELECT day_of_week FROM campaign_day_blocks WHERE guild_id=?",
            (str(guild_id),),
        ).fetchall()
        rows = conn.execute(
            "SELECT day_of_week, user_id FROM player_day_blocks WHERE guild_id=?",
            (str(guild_id),),
        ).fetchall()
    result: dict[int, list[str]] = {}
    for row in campaign_rows:
        result.setdefault(row["day_of_week"], []).append("campaign")
    for row in rows:
        result.setdefault(row["day_of_week"], []).append(row["user_id"])
    return result


# ── campaign_day_blocks ───────────────────────────────────────────────────────

def add_campaign_day_block(guild_id: int, day_of_week: int) -> bool:
    """Add a campaign-wide day block. Returns True if new, False if already existed."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM campaign_day_blocks WHERE guild_id=? AND day_of_week=?",
            (str(guild_id), day_of_week),
        ).fetchone()
        if existing:
            return False
        conn.execute(
            "INSERT INTO campaign_day_blocks (guild_id, day_of_week) VALUES (?, ?)",
            (str(guild_id), day_of_week),
        )
        return True


def remove_campaign_day_block(guild_id: int, day_of_week: int) -> bool:
    """Remove a campaign-wide day block. Returns True if removed, False if it didn't exist."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM campaign_day_blocks WHERE guild_id=? AND day_of_week=?",
            (str(guild_id), day_of_week),
        ).fetchone()
        if not existing:
            return False
        conn.execute(
            "DELETE FROM campaign_day_blocks WHERE guild_id=? AND day_of_week=?",
            (str(guild_id), day_of_week),
        )
        return True


def get_campaign_blocked_days(guild_id: int) -> list[int]:
    """Return list of day_of_week ints blocked campaign-wide."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT day_of_week FROM campaign_day_blocks WHERE guild_id=?",
            (str(guild_id),),
        ).fetchall()
    return [r["day_of_week"] for r in rows]


def remove_all_votes_for_day(poll_id: int, day_of_week: int) -> None:
    """Remove every player's vote for a day (used when the day becomes campaign-blocked)."""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM votes WHERE poll_id=? AND day_of_week=?",
            (poll_id, day_of_week),
        )


def get_user_day_blocks(guild_id: int, user_id: int) -> list[int]:
    """Return list of day_of_week ints blocked by this player."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT day_of_week FROM player_day_blocks WHERE guild_id=? AND user_id=?",
            (str(guild_id), str(user_id)),
        ).fetchall()
    return [r["day_of_week"] for r in rows]


def remove_vote_for_day(poll_id: int, user_id: int, day_of_week: int) -> None:
    """Remove a user's vote for a specific day (used when they block that day)."""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM votes WHERE poll_id=? AND user_id=? AND day_of_week=?",
            (poll_id, str(user_id), day_of_week),
        )


# ── player_nicknames ──────────────────────────────────────────────────────────

def upsert_nickname(guild_id: int, user_id: int, character_name: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO player_nicknames (guild_id, user_id, character_name)
               VALUES (?, ?, ?)
               ON CONFLICT(guild_id, user_id) DO UPDATE SET
                 character_name=excluded.character_name,
                 set_at=datetime('now')""",
            (str(guild_id), str(user_id), character_name),
        )


def remove_nickname(guild_id: int, user_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM player_nicknames WHERE guild_id=? AND user_id=?",
            (str(guild_id), str(user_id)),
        )


def get_all_nicknames(guild_id: int) -> dict[str, str]:
    """Returns {user_id: character_name} for all players with a set nickname."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id, character_name FROM player_nicknames WHERE guild_id=?",
            (str(guild_id),),
        ).fetchall()
    return {r["user_id"]: r["character_name"] for r in rows}


# ── player_timezone_offsets ───────────────────────────────────────────────────

def set_player_timezone(guild_id: int, user_id: int, offset: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO player_timezone_offsets (guild_id, user_id, timezone_offset)
               VALUES (?, ?, ?)
               ON CONFLICT(guild_id, user_id) DO UPDATE SET
                 timezone_offset=excluded.timezone_offset,
                 set_at=datetime('now')""",
            (str(guild_id), str(user_id), offset),
        )


def get_player_timezone(guild_id: int, user_id: int) -> int | None:
    """Returns the player's configured UTC offset, or None if not set."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT timezone_offset FROM player_timezone_offsets WHERE guild_id=? AND user_id=?",
            (str(guild_id), str(user_id)),
        ).fetchone()
    return row["timezone_offset"] if row else None


# ── campaign wipe ─────────────────────────────────────────────────────────────

def wipe_campaign_data(guild_id: int) -> None:
    """
    Delete all campaign-specific data for a guild.
    Config (channels, roles, session day/time) is preserved.
    """
    with get_conn() as conn:
        conn.execute("DELETE FROM active_poll")         # cascades → poll_days, votes, cant_make
        conn.execute("DELETE FROM tiebreaker_poll")     # cascades → tiebreaker_votes
        conn.execute("DELETE FROM end_times WHERE guild_id=?", (str(guild_id),))
        conn.execute("DELETE FROM session_history")
        conn.execute("DELETE FROM player_day_blocks WHERE guild_id=?", (str(guild_id),))
        conn.execute("DELETE FROM campaign_day_blocks WHERE guild_id=?", (str(guild_id),))
        conn.execute("DELETE FROM player_nicknames WHERE guild_id=?", (str(guild_id),))
        conn.execute("DELETE FROM player_timezone_offsets WHERE guild_id=?", (str(guild_id),))
        conn.execute(
            "UPDATE config SET initialized_at=NULL, category_id=NULL, "
            "poll_channel_id=NULL, dm_channel_id=NULL, voice_channel_id=NULL, "
            "player_role_id=NULL, dm_role_id=NULL, next_cycle_date=NULL, "
            "current_event_id=NULL WHERE id=1"
        )


# ── session_history ────────────────────────────────────────────────────────────

def record_history(
    week_start: str,
    original_day: int,
    rescheduled_to: int | None = None,
    cancelled: bool = False,
    reason: str = "",
) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO session_history
               (week_start, original_day, rescheduled_to, cancelled, outcome_reason)
               VALUES (?, ?, ?, ?, ?)""",
            (week_start, original_day, rescheduled_to, int(cancelled), reason),
        )


# ── session_recordings ────────────────────────────────────────────────────────

def start_session_recording(guild_id: int, channel_id: int, started_by: int) -> tuple[int, int]:
    """Create a new 'recording' row. Returns (id, session_number); session_number auto-increments per guild."""
    with get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM session_recordings WHERE guild_id=?",
            (str(guild_id),),
        ).fetchone()["n"]
        session_number = count + 1
        cur = conn.execute(
            """INSERT INTO session_recordings (guild_id, channel_id, session_number, started_by)
               VALUES (?, ?, ?, ?)""",
            (str(guild_id), str(channel_id), session_number, str(started_by)),
        )
        return cur.lastrowid, session_number


def get_active_session_recording(guild_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM session_recordings WHERE guild_id=? AND status='recording'",
            (str(guild_id),),
        ).fetchone()
        return dict(row) if row else None


def complete_session_recording(session_id: int, folder_path: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE session_recordings
               SET status='completed', ended_at=datetime('now'), folder_path=?
               WHERE id=?""",
            (folder_path, session_id),
        )


def fail_session_recording(session_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE session_recordings SET status='failed', ended_at=datetime('now') WHERE id=?",
            (session_id,),
        )


def fail_stale_session_recordings() -> int:
    """Mark any still-'recording' rows as 'failed' (called on bot startup, since
    in-memory voice/sink state can't survive a restart). Returns rows affected."""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE session_recordings SET status='failed', ended_at=datetime('now') WHERE status='recording'"
        )
        return cur.rowcount
