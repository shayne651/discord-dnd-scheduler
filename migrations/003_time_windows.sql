-- Migration 003: Per-day time windows, voice channel, timezone, session duration

CREATE TABLE IF NOT EXISTS day_time_windows (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    poll_id     INTEGER NOT NULL REFERENCES active_poll(id) ON DELETE CASCADE,
    user_id     TEXT    NOT NULL,
    day_of_week INTEGER NOT NULL,
    start_mins  INTEGER,
    end_mins    INTEGER,
    UNIQUE(poll_id, user_id, day_of_week)
);

ALTER TABLE config ADD COLUMN voice_channel_id TEXT;
ALTER TABLE config ADD COLUMN min_session_hours REAL DEFAULT 2.0;
ALTER TABLE config ADD COLUMN timezone_offset INTEGER DEFAULT 0;
