-- Migration 001: Initial schema

CREATE TABLE IF NOT EXISTS config (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    guild_id          TEXT    NOT NULL,
    session_day       INTEGER NOT NULL DEFAULT 5,
    poll_channel_id   TEXT,
    dm_channel_id     TEXT,
    player_role_id    TEXT,
    dm_role_id        TEXT,
    updated_at        TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS active_poll (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    message_id    TEXT    NOT NULL,
    channel_id    TEXT    NOT NULL,
    created_by    TEXT    NOT NULL,
    week_start    TEXT    NOT NULL,
    is_closed     INTEGER NOT NULL DEFAULT 0,
    closed_reason TEXT,
    created_at    TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS poll_days (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    poll_id     INTEGER NOT NULL REFERENCES active_poll(id) ON DELETE CASCADE,
    day_of_week INTEGER NOT NULL,
    UNIQUE (poll_id, day_of_week)
);

CREATE TABLE IF NOT EXISTS votes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    poll_id     INTEGER NOT NULL REFERENCES active_poll(id) ON DELETE CASCADE,
    user_id     TEXT    NOT NULL,
    day_of_week INTEGER NOT NULL,
    voted_at    TEXT    DEFAULT (datetime('now')),
    UNIQUE (poll_id, user_id, day_of_week)
);

CREATE TABLE IF NOT EXISTS cant_make (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    poll_id        INTEGER NOT NULL REFERENCES active_poll(id) ON DELETE CASCADE,
    user_id        TEXT    NOT NULL,
    available_days TEXT    NOT NULL DEFAULT '[]',
    declared_at    TEXT    DEFAULT (datetime('now')),
    UNIQUE (poll_id, user_id)
);

CREATE TABLE IF NOT EXISTS tiebreaker_poll (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    week_start  TEXT    NOT NULL,
    message_id  TEXT    NOT NULL,
    channel_id  TEXT    NOT NULL,
    tied_days   TEXT    NOT NULL,
    is_closed   INTEGER NOT NULL DEFAULT 0,
    winner_day  INTEGER,
    created_at  TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tiebreaker_votes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tb_id       INTEGER NOT NULL REFERENCES tiebreaker_poll(id) ON DELETE CASCADE,
    user_id     TEXT    NOT NULL,
    day_of_week INTEGER NOT NULL,
    voted_at    TEXT    DEFAULT (datetime('now')),
    UNIQUE (tb_id, user_id, day_of_week)
);

CREATE TABLE IF NOT EXISTS end_times (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    week_start  TEXT NOT NULL,
    end_time    TEXT NOT NULL,
    notified_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS session_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start      TEXT    NOT NULL,
    original_day    INTEGER,
    rescheduled_to  INTEGER,
    cancelled       INTEGER NOT NULL DEFAULT 0,
    outcome_reason  TEXT,
    recorded_at     TEXT    DEFAULT (datetime('now'))
);
