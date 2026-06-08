-- Migration: Add CRM retry queue columns to appointmentsleads table
-- Run this SQL on your MySQL database before deploying the new code

-- Add new columns for retry queue functionality
ALTER TABLE appointmentsleads
ADD COLUMN IF NOT EXISTS retry_count INT DEFAULT 0,
ADD COLUMN IF NOT EXISTS next_retry_at DATETIME DEFAULT NULL,
ADD COLUMN IF NOT EXISTS last_error TEXT DEFAULT NULL,
ADD COLUMN IF NOT EXISTS salon_code VARCHAR(10) DEFAULT NULL;

-- Create index for efficient queue queries
-- This index helps the retry worker find pending appointments quickly
CREATE INDEX IF NOT EXISTS idx_retry_queue
ON appointmentsleads (paymentStatus, actualState, next_retry_at);

-- Note: If your MySQL version doesn't support "IF NOT EXISTS" for columns,
-- use these alternative commands:

-- ALTER TABLE appointmentsleads ADD COLUMN retry_count INT DEFAULT 0;
-- ALTER TABLE appointmentsleads ADD COLUMN next_retry_at DATETIME DEFAULT NULL;
-- ALTER TABLE appointmentsleads ADD COLUMN last_error TEXT DEFAULT NULL;
-- ALTER TABLE appointmentsleads ADD COLUMN salon_code VARCHAR(10) DEFAULT NULL;
-- CREATE INDEX idx_retry_queue ON appointmentsleads (paymentStatus, actualState, next_retry_at);
