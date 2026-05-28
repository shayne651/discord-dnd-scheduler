-- Migration 004: Per-player timezone preferences

CREATE TABLE IF NOT EXISTS player_timezone_offsets (
    guild_id         TEXT    NOT NULL,
    user_id          TEXT    NOT NULL,
    timezone_offset  INTEGER NOT NULL,
    set_at           TEXT    DEFAULT (datetime('now')),
    UNIQUE(guild_id, user_id)
);
