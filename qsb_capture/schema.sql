PRAGMA user_version=1;
CREATE TABLE bodies(sha256 TEXT PRIMARY KEY, bytes INTEGER NOT NULL, payload BLOB NOT NULL);
CREATE TABLE responses(id TEXT PRIMARY KEY, sweep TEXT NOT NULL, kind TEXT NOT NULL,
 symbol TEXT, type TEXT, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
 status INTEGER, body_sha256 TEXT REFERENCES bodies(sha256), metadata TEXT NOT NULL);
CREATE INDEX response_time ON responses(started_at);
CREATE TABLE instrument_versions(id TEXT PRIMARY KEY, trading_date TEXT NOT NULL,
 symbol TEXT NOT NULL, type TEXT NOT NULL, subtype TEXT NOT NULL, definition_json TEXT NOT NULL);
CREATE INDEX instrument_lookup ON instrument_versions(trading_date,type,symbol);
CREATE TABLE legs(instrument_id TEXT REFERENCES instrument_versions(id), sequence INTEGER,
 osi_symbol TEXT, root TEXT, expiry TEXT, strike TEXT, signed_ratio TEXT, side TEXT,
 PRIMARY KEY(instrument_id,sequence));
CREATE TABLE reference_membership(response_id TEXT REFERENCES responses(id), instrument_id TEXT REFERENCES instrument_versions(id),
 PRIMARY KEY(response_id,instrument_id));
CREATE TABLE depth_states(id TEXT PRIMARY KEY, data_json TEXT NOT NULL);
CREATE TABLE quote_levels(state_id TEXT REFERENCES depth_states(id), side TEXT, position INTEGER,
 quantity TEXT NOT NULL, price TEXT NOT NULL, extra_json TEXT NOT NULL, PRIMARY KEY(state_id,side,position));
CREATE TABLE trade_windows(id TEXT PRIMARY KEY, data_json TEXT NOT NULL);
CREATE TABLE trade_entries(window_id TEXT REFERENCES trade_windows(id), position INTEGER,
 source_time TEXT NOT NULL, quantity TEXT NOT NULL, price TEXT NOT NULL, PRIMARY KEY(window_id,position));
CREATE TABLE observations(response_id TEXT PRIMARY KEY REFERENCES responses(id),
 reference_id TEXT, instrument_id TEXT REFERENCES instrument_versions(id), trading_date TEXT,
 symbol TEXT NOT NULL, type TEXT NOT NULL, observed_at TEXT NOT NULL, source_time TEXT NOT NULL,
 depth_id TEXT REFERENCES depth_states(id), trades_id TEXT REFERENCES trade_windows(id),
 bid_count INTEGER NOT NULL, ask_count INTEGER NOT NULL, trade_count INTEGER NOT NULL);
CREATE INDEX observation_lookup ON observations(type,symbol,observed_at);
CREATE TABLE reports(id TEXT PRIMARY KEY, observed_at TEXT NOT NULL, data_json TEXT NOT NULL);
