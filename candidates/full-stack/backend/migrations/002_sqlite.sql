CREATE TABLE IF NOT EXISTS gateway_status (
 device_id TEXT NOT NULL,
 synthetic INTEGER NOT NULL CHECK (synthetic IN (0,1)),
 canonical TEXT NOT NULL,
 server_received_at TEXT NOT NULL,
 PRIMARY KEY (device_id, synthetic)
);
INSERT OR IGNORE INTO schema_versions(version) VALUES (2);
