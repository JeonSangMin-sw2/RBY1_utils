from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, title TEXT);
CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY, case_id TEXT NOT NULL, kind TEXT NOT NULL,
  sha256 TEXT NOT NULL, size INTEGER NOT NULL, stored_path TEXT NOT NULL,
  original_name TEXT NOT NULL, status TEXT NOT NULL, detail_code TEXT,
  UNIQUE(case_id, kind, sha256), FOREIGN KEY(case_id) REFERENCES cases(id)
);
CREATE TABLE IF NOT EXISTS provenance (
  id INTEGER PRIMARY KEY, artifact_id INTEGER NOT NULL, original_name TEXT NOT NULL,
  member_name TEXT, FOREIGN KEY(artifact_id) REFERENCES artifacts(id)
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, case_id TEXT NOT NULL, state TEXT NOT NULL, detail_code TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, FOREIGN KEY(case_id) REFERENCES cases(id)
);
CREATE TABLE IF NOT EXISTS progress (
  job_id TEXT NOT NULL, seq INTEGER NOT NULL, payload TEXT NOT NULL,
  PRIMARY KEY(job_id, seq), FOREIGN KEY(job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, artifact_id INTEGER NOT NULL, source_name TEXT NOT NULL,
  member_name TEXT, line INTEGER NOT NULL, byte_offset INTEGER NOT NULL,
  raw_digest TEXT NOT NULL, excerpt TEXT NOT NULL, severity TEXT NOT NULL,
  category TEXT NOT NULL, signature TEXT NOT NULL, component TEXT, joint TEXT,
  command TEXT, result TEXT, time_value REAL, time_basis TEXT, time_raw TEXT,
  diagnostic_json TEXT NOT NULL DEFAULT '[]',
  FOREIGN KEY(artifact_id) REFERENCES artifacts(id)
);
CREATE INDEX IF NOT EXISTS events_case_time ON events(artifact_id, time_value, line);
CREATE INDEX IF NOT EXISTS events_search ON events(severity, category, component, joint);
CREATE TABLE IF NOT EXISTS time_observations (
  id INTEGER PRIMARY KEY, artifact_id INTEGER NOT NULL, event_id INTEGER,
  basis TEXT NOT NULL, value REAL, raw TEXT NOT NULL, source_sequence INTEGER NOT NULL,
  precision TEXT NOT NULL, timezone_known INTEGER NOT NULL,
  parse_status TEXT NOT NULL, discontinuity_group INTEGER NOT NULL DEFAULT 0,
  monotonic INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY(artifact_id) REFERENCES artifacts(id),
  FOREIGN KEY(event_id) REFERENCES events(id)
);
CREATE INDEX IF NOT EXISTS time_observations_event
  ON time_observations(event_id, basis, source_sequence);
CREATE INDEX IF NOT EXISTS time_observations_source
  ON time_observations(artifact_id, basis, source_sequence);
CREATE TABLE IF NOT EXISTS correlations (
  id INTEGER PRIMARY KEY, artifact_id INTEGER NOT NULL,
  request_event_id INTEGER NOT NULL, result_event_id INTEGER NOT NULL,
  basis TEXT NOT NULL, delta REAL, confidence TEXT NOT NULL,
  explanation TEXT NOT NULL, causal INTEGER NOT NULL DEFAULT 0,
  UNIQUE(request_event_id, result_event_id),
  FOREIGN KEY(artifact_id) REFERENCES artifacts(id),
  FOREIGN KEY(request_event_id) REFERENCES events(id),
  FOREIGN KEY(result_event_id) REFERENCES events(id)
);
CREATE INDEX IF NOT EXISTS correlations_event
  ON correlations(request_event_id, result_event_id);
CREATE TABLE IF NOT EXISTS warnings (
  id INTEGER PRIMARY KEY, artifact_id INTEGER, code TEXT NOT NULL,
  message TEXT NOT NULL, member_name TEXT,
  FOREIGN KEY(artifact_id) REFERENCES artifacts(id)
);
CREATE TABLE IF NOT EXISTS chart_samples (
  artifact_id INTEGER NOT NULL, sample_time REAL NOT NULL, name TEXT NOT NULL,
  value REAL NOT NULL, kind TEXT NOT NULL,
  FOREIGN KEY(artifact_id) REFERENCES artifacts(id)
);
CREATE INDEX IF NOT EXISTS chart_samples_window
  ON chart_samples(artifact_id, name, sample_time);
CREATE INDEX IF NOT EXISTS chart_samples_name_window
  ON chart_samples(name, sample_time, artifact_id);
CREATE INDEX IF NOT EXISTS chart_samples_art_time
  ON chart_samples(artifact_id, sample_time);
CREATE TABLE IF NOT EXISTS analysis_runs (
  id INTEGER PRIMARY KEY, case_id TEXT NOT NULL, job_id TEXT,
  schema_version INTEGER NOT NULL, started_at TEXT NOT NULL, completed_at TEXT,
  FOREIGN KEY(case_id) REFERENCES cases(id)
);
CREATE TABLE IF NOT EXISTS incidents (
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, case_id TEXT NOT NULL,
  family TEXT NOT NULL, title TEXT NOT NULL, severity TEXT NOT NULL,
  primary_event_id INTEGER NOT NULL, start_time REAL, end_time REAL,
  time_basis TEXT, start_raw TEXT, end_raw TEXT,
  meaning TEXT NOT NULL, summary TEXT NOT NULL,
  confidence TEXT NOT NULL, confidence_reason TEXT NOT NULL,
  occurrence_count INTEGER NOT NULL, event_count INTEGER NOT NULL,
  affected_components TEXT NOT NULL DEFAULT '[]',
  affected_joints TEXT NOT NULL DEFAULT '[]',
  affected_power_rails TEXT NOT NULL DEFAULT '[]',
  detected_flags TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES analysis_runs(id) ON DELETE CASCADE,
  FOREIGN KEY(case_id) REFERENCES cases(id),
  FOREIGN KEY(primary_event_id) REFERENCES events(id)
);
CREATE INDEX IF NOT EXISTS incidents_case_time
  ON incidents(case_id, start_time, id);
CREATE INDEX IF NOT EXISTS incidents_case_family
  ON incidents(case_id, severity, family);
CREATE TABLE IF NOT EXISTS incident_events (
  incident_id INTEGER NOT NULL, event_id INTEGER NOT NULL,
  role TEXT NOT NULL, rank INTEGER NOT NULL, relation TEXT NOT NULL,
  PRIMARY KEY(incident_id, event_id),
  FOREIGN KEY(incident_id) REFERENCES incidents(id) ON DELETE CASCADE,
  FOREIGN KEY(event_id) REFERENCES events(id)
);
CREATE INDEX IF NOT EXISTS incident_events_event ON incident_events(event_id);
CREATE TABLE IF NOT EXISTS incident_hypotheses (
  id INTEGER PRIMARY KEY, incident_id INTEGER NOT NULL, rank INTEGER NOT NULL,
  text TEXT NOT NULL, confidence TEXT NOT NULL, rationale TEXT NOT NULL,
  source_rule_id TEXT NOT NULL,
  FOREIGN KEY(incident_id) REFERENCES incidents(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS incident_actions (
  id INTEGER PRIMARY KEY, incident_id INTEGER NOT NULL, kind TEXT NOT NULL,
  priority INTEGER NOT NULL, text TEXT NOT NULL, source_rule_id TEXT NOT NULL,
  FOREIGN KEY(incident_id) REFERENCES incidents(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS incident_csv_links (
  incident_id INTEGER NOT NULL, artifact_id INTEGER NOT NULL,
  delta_seconds REAL NOT NULL, confidence TEXT NOT NULL, reason TEXT NOT NULL,
  PRIMARY KEY(incident_id, artifact_id),
  FOREIGN KEY(incident_id) REFERENCES incidents(id) ON DELETE CASCADE,
  FOREIGN KEY(artifact_id) REFERENCES artifacts(id)
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            try:
                connection.execute("ALTER TABLE cases ADD COLUMN title TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                connection.execute("ALTER TABLE incidents ADD COLUMN detected_flags TEXT NOT NULL DEFAULT '[]'")
            except sqlite3.OperationalError:
                pass

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()
