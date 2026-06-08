-- Migration: Payment systems
-- Adds global payment systems registry and per-salon payment toggle

CREATE TABLE IF NOT EXISTS payment_systems (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    provider VARCHAR(50) NOT NULL COMMENT 'easytip, stripe, liqpay, etc.',
    config JSON DEFAULT NULL COMMENT 'Provider-specific config (URLs, percentages)',
    status ENUM('active', 'inactive') DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE salons ADD COLUMN payment_enabled BOOLEAN DEFAULT FALSE;
ALTER TABLE salons ADD COLUMN payment_system_id INT DEFAULT NULL;

-- Seed: EasyTip (current London payment system)
INSERT INTO payment_systems (name, provider, config, status) VALUES
('EasyTip London', 'easytip', JSON_OBJECT(
    'token_url', 'https://auth.easytip.net/realms/easytip/protocol/openid-connect/token',
    'api_url', 'https://uk-api.easytip.net/api/v1/salons/appointments',
    'status_url', 'https://uk-api.easytip.net/api/v1/salons/appointments/{appointmentId}',
    'deposit_percent', 50,
    'vat_percent', 13
), 'active');

-- Enable payment for London salons
UPDATE salons SET payment_enabled = TRUE, payment_system_id = 1 WHERE code IN ('l1', 'l2');
