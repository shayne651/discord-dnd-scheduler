-- Migration 002: Personal day blocks, character nicknames, session time

-- Add session time to config (e.g. "7pm")
ALTER TABLE config ADD COLUMN session_time TEXT;

-- Per-player permanent day blocks (e.g. "I can never do Tuesdays")
CREATE TABLE IF NOT EXISTS player_day_blocks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    TEXT    NOT NULL,
    user_id     TEXT    NOT NULL,
    day_of_week INTEGER NOT NULL,
    blocked_at  TEXT    DEFAULT (datetime('now')),
    UNIQUE (guild_id, user_id, day_of_week)
);

-- Per-player character names displayed in polls
CREATE TABLE IF NOT EXISTS player_nicknames (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id       TEXT NOT NULL,
    user_id        TEXT NOT NULL,
    character_name TEXT NOT NULL,
    set_at         TEXT DEFAULT (datetime('now')),
    UNIQUE (guild_id, user_id)
);
