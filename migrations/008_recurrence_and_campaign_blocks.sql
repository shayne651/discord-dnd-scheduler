-- Migration 008: Recurring cadence, campaign-wide day blocks, auto-schedule tracking

-- How often (in weeks) the campaign repeats. 1 = every week.
ALTER TABLE config ADD COLUMN recurrence_weeks INTEGER NOT NULL DEFAULT 1;

-- Date (ISO) of the next session the auto-scheduler should create an event for.
-- NULL means nothing is pending auto-creation (e.g. an open poll is deciding the cycle instead).
ALTER TABLE config ADD COLUMN next_cycle_date TEXT;

-- Discord scheduled event ID for the currently pending/most recent cycle, so it can be
-- cancelled if a poll overrides it.
ALTER TABLE config ADD COLUMN current_event_id TEXT;

-- Campaign-wide blocked days (e.g. "we never play Fridays"), independent of any one player.
CREATE TABLE IF NOT EXISTS campaign_day_blocks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    TEXT    NOT NULL,
    day_of_week INTEGER NOT NULL,
    blocked_at  TEXT    DEFAULT (datetime('now')),
    UNIQUE (guild_id, day_of_week)
);
