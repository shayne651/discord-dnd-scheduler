-- Migration 006: Store the D&D category name in config so it persists across restarts

ALTER TABLE config ADD COLUMN category_name TEXT NOT NULL DEFAULT 'D&D';
