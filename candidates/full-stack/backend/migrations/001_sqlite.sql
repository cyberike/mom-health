CREATE TABLE IF NOT EXISTS schema_versions (version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS samples (
 seq INTEGER PRIMARY KEY AUTOINCREMENT,
 sample_id TEXT NOT NULL UNIQUE,
 device_id TEXT NOT NULL,
 synthetic INTEGER NOT NULL CHECK (synthetic IN (0,1)),
 canonical TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS samples_device_seq ON samples(device_id, seq);
CREATE TABLE IF NOT EXISTS batches (
 batch_id TEXT PRIMARY KEY,
 canonical TEXT NOT NULL,
 response TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_versions(version) VALUES (1);
