-- Migration 009: DM private voice channel (pull individual players aside, invisible to PCs)

ALTER TABLE config ADD COLUMN dm_private_voice_channel_id TEXT;
