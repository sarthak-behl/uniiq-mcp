CREATE TABLE IF NOT EXISTS universities (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL UNIQUE,
    url                 TEXT,
    acceptance_rate     REAL,
    avg_gpa             REAL,
    avg_sat             INTEGER,
    avg_act             INTEGER,
    required_ap_classes INTEGER,
    application_deadline TEXT,
    scholarship_deadline TEXT,
    required_essays     INTEGER,
    requires_interview  INTEGER DEFAULT 0,
    notes               TEXT,
    last_updated        TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS requirements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    university_id   INTEGER NOT NULL REFERENCES universities(id) ON DELETE CASCADE,
    category        TEXT NOT NULL,   -- 'academic', 'test', 'extracurricular', 'essay', 'financial'
    label           TEXT NOT NULL,
    min_value       REAL,
    preferred_value REAL,
    unit            TEXT,            -- 'gpa_points', 'sat_points', 'act_points', 'courses', 'hours'
    is_required     INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_requirements_university ON requirements(university_id);
CREATE INDEX IF NOT EXISTS idx_universities_name ON universities(name);
