-- Migration 007: Store the category channel ID so wipe can delete it

ALTER TABLE config ADD COLUMN category_id TEXT;
