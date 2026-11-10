-- Migration: add telegram_chat_id to users table
-- Compatible with SQLite and PostgreSQL
ALTER TABLE users ADD COLUMN telegram_chat_id VARCHAR(100);
