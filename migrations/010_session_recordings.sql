-- Migration 010: Session voice recordings (/session start, /session end)

CREATE TABLE session_recordings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id       TEXT NOT NULL,
    channel_id     TEXT NOT NULL,
    session_number INTEGER NOT NULL,
    started_by     TEXT NOT NULL,
    started_at     TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at       TEXT,
    folder_path    TEXT,
    status         TEXT NOT NULL DEFAULT 'recording'  -- recording | completed | failed
);
