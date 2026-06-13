import sqlite3
import json
from typing import Optional
from models import FlightStatus, normalize_status, FlightSnapshotData, now_ist
from config import DB_PATH


class Database:
    def __init__(self, db_path: str = str(DB_PATH)):
        self.db_path = db_path
        self._init_db()
        self._migrate_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tracked_flights (
                    flight_number TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    added_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS flight_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flight_number TEXT NOT NULL,
                    flight_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    flight_state TEXT,
                    flight_status TEXT,
                    departure_time TEXT,
                    updated_departure_time TEXT,
                    arrival_time TEXT,
                    updated_arrival_time TEXT,
                    aircraft_type TEXT,
                    tail_number TEXT,
                    raw_json TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    notified INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS flight_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flight_number TEXT NOT NULL,
                    flight_date TEXT NOT NULL,
                    old_status TEXT,
                    new_status TEXT,
                    event_time TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_snapshots_flight_date
                    ON flight_snapshots(flight_number, flight_date);
                CREATE INDEX IF NOT EXISTS idx_events_flight
                    ON flight_events(flight_number, flight_date);
            """)

    def _migrate_db(self):
        migrations = [
            "ALTER TABLE flight_snapshots ADD COLUMN origin_code TEXT",
            "ALTER TABLE flight_snapshots ADD COLUMN origin_city TEXT",
            "ALTER TABLE flight_snapshots ADD COLUMN origin_name TEXT",
            "ALTER TABLE flight_snapshots ADD COLUMN origin_terminal TEXT",
            "ALTER TABLE flight_snapshots ADD COLUMN origin_gate TEXT",
            "ALTER TABLE flight_snapshots ADD COLUMN dest_code TEXT",
            "ALTER TABLE flight_snapshots ADD COLUMN dest_city TEXT",
            "ALTER TABLE flight_snapshots ADD COLUMN dest_name TEXT",
            "ALTER TABLE flight_snapshots ADD COLUMN dest_terminal TEXT",
            "ALTER TABLE flight_snapshots ADD COLUMN dest_gate TEXT",
            "ALTER TABLE flight_snapshots ADD COLUMN departure_local TEXT",
            "ALTER TABLE flight_snapshots ADD COLUMN updated_departure_local TEXT",
            "ALTER TABLE flight_snapshots ADD COLUMN arrival_local TEXT",
            "ALTER TABLE flight_snapshots ADD COLUMN updated_arrival_local TEXT",
            "ALTER TABLE flight_snapshots ADD COLUMN notified INTEGER DEFAULT 0",
        ]
        with self._conn() as conn:
            for m in migrations:
                try:
                    conn.execute(m)
                except sqlite3.OperationalError:
                    pass

    def add_tracked_flight(self, flight_number: str):
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO tracked_flights (flight_number, enabled, added_at)
                   VALUES (?, 1, ?)""",
                (flight_number.upper(), now_ist()),
            )

    def remove_tracked_flight(self, flight_number: str):
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM tracked_flights WHERE flight_number = ?",
                (flight_number.upper(),),
            )

    def get_tracked_flights(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tracked_flights WHERE enabled = 1"
            ).fetchall()
            return [dict(r) for r in rows]

    def is_tracked(self, flight_number: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM tracked_flights WHERE flight_number = ? AND enabled = 1",
                (flight_number.upper(),),
            ).fetchone()
            return row is not None

    def save_snapshot(self, data: FlightSnapshotData, notified: bool = False):
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO flight_snapshots
                   (flight_number, flight_date, status,
                    flight_state, flight_status,
                    departure_time, updated_departure_time,
                    arrival_time, updated_arrival_time,
                    origin_code, origin_city, origin_name,
                    origin_terminal, origin_gate,
                    dest_code, dest_city, dest_name,
                    dest_terminal, dest_gate,
                    departure_local, updated_departure_local,
                    arrival_local, updated_arrival_local,
                    aircraft_type, tail_number,
                    raw_json, collected_at, notified)
                   VALUES (?, ?, ?,
                           ?, ?,
                           ?, ?,
                           ?, ?,
                           ?, ?, ?,
                           ?, ?,
                           ?, ?, ?,
                           ?, ?,
                           ?, ?,
                           ?, ?,
                           ?, ?,
                           ?, ?, ?)""",
                (
                    data.flight_number,
                    data.flight_date,
                    data.status,
                    data.flight_state,
                    data.flight_status,
                    data.departure_time,
                    data.updated_departure_time,
                    data.arrival_time,
                    data.updated_arrival_time,
                    data.origin_code,
                    data.origin_city,
                    data.origin_name,
                    data.origin_terminal,
                    data.origin_gate,
                    data.dest_code,
                    data.dest_city,
                    data.dest_name,
                    data.dest_terminal,
                    data.dest_gate,
                    data.departure_local,
                    data.updated_departure_local,
                    data.arrival_local,
                    data.updated_arrival_local,
                    data.aircraft_type,
                    data.tail_number,
                    data.raw_json,
                    data.collected_at,
                    1 if notified else 0,
                ),
            )

    def get_latest_snapshot(self, flight_number: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM flight_snapshots
                   WHERE flight_number = ?
                   ORDER BY id DESC LIMIT 1""",
                (flight_number.upper(),),
            ).fetchone()
            return dict(row) if row else None

    def get_latest_event(self, flight_number: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM flight_events
                   WHERE flight_number = ?
                   ORDER BY id DESC LIMIT 1""",
                (flight_number.upper(),),
            ).fetchone()
            return dict(row) if row else None

    def save_event(self, flight_number: str, flight_date: str,
                   old_status: Optional[str], new_status: str):
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO flight_events
                   (flight_number, flight_date, old_status, new_status, event_time)
                   VALUES (?, ?, ?, ?, ?)""",
                (flight_number.upper(), flight_date, old_status, new_status, now_ist()),
            )

    def get_history(self, flight_number: str, limit: int = 7) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM flight_snapshots
                   WHERE flight_number = ?
                   ORDER BY id DESC LIMIT ?""",
                (flight_number.upper(), limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_history_range(self, flight_number: str, since: str, until: str | None = None) -> list[dict]:
        with self._conn() as conn:
            if until:
                rows = conn.execute(
                    """SELECT * FROM flight_snapshots
                       WHERE flight_number = ? AND flight_date >= ? AND flight_date <= ?
                       ORDER BY id DESC""",
                    (flight_number.upper(), since, until),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM flight_snapshots
                       WHERE flight_number = ? AND flight_date >= ?
                       ORDER BY id DESC""",
                    (flight_number.upper(), since),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_all_snapshots(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM flight_snapshots ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]

    def parse_snapshot_from_api(
        self, flight_number: str, api_data: dict
    ) -> Optional[FlightSnapshotData]:
        flights = api_data.get("data", {}).get("flights", [])
        if not flights:
            return None

        f = flights[0]
        flight_state = f.get("flightState")
        flight_status = f.get("flightStatus")
        status = normalize_status(flight_state, flight_status)
        flight_date = f.get("flightDateUtc", "").split("T")[0]

        origin = f.get("origin", {})
        dest = f.get("destination", {})

        return FlightSnapshotData(
            flight_number=flight_number.upper(),
            flight_date=flight_date,
            status=status,
            flight_state=flight_state,
            flight_status=flight_status,
            departure_time=origin.get("departureTime"),
            updated_departure_time=origin.get("updatedDepartureTime"),
            departure_local=origin.get("departureLocalTime"),
            updated_departure_local=origin.get("updatedDepartureLocalTime"),
            arrival_time=dest.get("arrivalTime"),
            updated_arrival_time=dest.get("updatedArrivalTime"),
            arrival_local=dest.get("arrivalLocalTime"),
            updated_arrival_local=dest.get("updatedArrivalLocalTime"),
            origin_code=origin.get("airportCode"),
            origin_city=origin.get("airportCity"),
            origin_name=origin.get("airportName"),
            origin_terminal=origin.get("airportTerminal"),
            origin_gate=origin.get("gate"),
            dest_code=dest.get("airportCode"),
            dest_city=dest.get("airportCity"),
            dest_name=dest.get("airportName"),
            dest_terminal=dest.get("airportTerminal"),
            dest_gate=dest.get("gate"),
            aircraft_type=f.get("airCraftTypeName"),
            tail_number=f.get("tailNumber"),
            raw_json=json.dumps(api_data, ensure_ascii=False),
            collected_at=now_ist(),
        )
