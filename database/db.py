"""SQLite data access layer — thin wrappers around the schema."""

import sqlite3
import os
from pathlib import Path
from typing import Any

_SCHEMA = Path(__file__).parent / "schema.sql"
_DEFAULT_DB = os.getenv("DB_PATH", "./uniiq.db")


def connect(db_path: str = _DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    with open(_SCHEMA) as f:
        conn.executescript(f.read())
    conn.commit()
    return conn


def upsert_university(conn: sqlite3.Connection, data: dict) -> int:
    conn.execute(
        """
        INSERT INTO universities
            (name, url, acceptance_rate, avg_gpa, avg_sat, avg_act,
             required_ap_classes, application_deadline, scholarship_deadline,
             required_essays, requires_interview, notes, last_updated)
        VALUES
            (:name, :url, :acceptance_rate, :avg_gpa, :avg_sat, :avg_act,
             :required_ap_classes, :application_deadline, :scholarship_deadline,
             :required_essays, :requires_interview, :notes, datetime('now'))
        ON CONFLICT(name) DO UPDATE SET
            url                  = excluded.url,
            acceptance_rate      = excluded.acceptance_rate,
            avg_gpa              = excluded.avg_gpa,
            avg_sat              = excluded.avg_sat,
            avg_act              = excluded.avg_act,
            required_ap_classes  = excluded.required_ap_classes,
            application_deadline = excluded.application_deadline,
            scholarship_deadline = excluded.scholarship_deadline,
            required_essays      = excluded.required_essays,
            requires_interview   = excluded.requires_interview,
            notes                = excluded.notes,
            last_updated         = datetime('now')
        """,
        data,
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM universities WHERE name = ?", (data["name"],)
    ).fetchone()
    return row["id"]


def upsert_requirements(conn: sqlite3.Connection, university_id: int, reqs: list[dict]):
    conn.execute("DELETE FROM requirements WHERE university_id = ?", (university_id,))
    conn.executemany(
        """
        INSERT INTO requirements (university_id, category, label, min_value, preferred_value, unit, is_required)
        VALUES (:university_id, :category, :label, :min_value, :preferred_value, :unit, :is_required)
        """,
        [{**r, "university_id": university_id} for r in reqs],
    )
    conn.commit()


def get_university(conn: sqlite3.Connection, name: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM universities WHERE name LIKE ?", (f"%{name}%",)
    ).fetchone()
    return dict(row) if row else None


def get_requirements(conn: sqlite3.Connection, university_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM requirements WHERE university_id = ?", (university_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def list_universities(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM universities ORDER BY name").fetchall()
    return [r["name"] for r in rows]
