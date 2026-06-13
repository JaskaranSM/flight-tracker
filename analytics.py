import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from database import Database
from config import CSV_DIR, XLSX_DIR


class Analytics:
    def __init__(self, db: Database):
        self.db = db

    def route_reliability(self, flight_number: str) -> dict:
        snapshots = self.db.get_history(flight_number, limit=10000)
        if not snapshots:
            return {}

        operated = 0
        cancelled = 0
        observed = len(snapshots)

        for s in snapshots:
            if s["status"] == "cancelled":
                cancelled += 1
            elif s["status"] in ("arrived", "departed", "delayed", "airborne", "boarding", "boarding_closed", "taxiing"):
                operated += 1

        return {
            "observed_days": observed,
            "operated_days": operated,
            "cancelled_days": cancelled,
        }

    def delay_metrics(self, flight_number: str) -> dict:
        snapshots = self.db.get_history(flight_number, limit=10000)
        delays = []

        for s in snapshots:
            if s["status"] == "delayed" and s["arrival_time"] and s["updated_arrival_time"]:
                try:
                    orig = datetime.fromisoformat(s["arrival_time"].replace("Z", "+00:00"))
                    upd = datetime.fromisoformat(s["updated_arrival_time"].replace("Z", "+00:00"))
                    delay_min = int((upd - orig).total_seconds() / 60)
                    if delay_min > 0:
                        delays.append(delay_min)
                except (ValueError, TypeError):
                    continue

        if not delays:
            return {}

        delays.sort()
        n = len(delays)
        p95_idx = max(0, int(n * 0.95) - 1)

        return {
            "average_delay": round(sum(delays) / n, 1),
            "max_delay": max(delays),
            "p95_delay": delays[p95_idx],
            "total_delayed_records": n,
        }

    def backup_reliability(self, primary: str, backup: str) -> dict:
        primary_snapshots = self.db.get_history(primary, limit=10000)
        backup_snapshots = self.db.get_history(backup, limit=10000)

        primary_cancelled = sum(1 for s in primary_snapshots if s["status"] == "cancelled")
        backup_cancelled = sum(1 for s in backup_snapshots if s["status"] == "cancelled")

        return {
            f"{primary}_cancelled_days": primary_cancelled,
            f"{backup}_cancelled_days": backup_cancelled,
        }

    def export_csv(self) -> Path:
        snapshots = self.db.get_all_snapshots()
        CSV_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = CSV_DIR / f"flight_snapshots_{ts}.csv"

        with open(path, "w", newline="") as f:
            if not snapshots:
                return path
            writer = csv.DictWriter(f, fieldnames=snapshots[0].keys())
            writer.writeheader()
            writer.writerows(snapshots)

        return path

    def export_xlsx(self) -> Path:
        from openpyxl import Workbook

        snapshots = self.db.get_all_snapshots()
        XLSX_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = XLSX_DIR / f"flight_snapshots_{ts}.xlsx"

        wb = Workbook()
        ws = wb.active
        ws.title = "Snapshots"

        if snapshots:
            ws.append(list(snapshots[0].keys()))
            for row in snapshots:
                ws.append(list(row.values()))

        wb.save(path)
        return path
