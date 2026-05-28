-- Migration 005: Setup wizard initialized flag

ALTER TABLE config ADD COLUMN initialized_at TEXT;
