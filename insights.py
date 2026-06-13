import json
from datetime import datetime
from typing import Optional
from database import Database
from config import BACKUP_PAIRS


def _per_date(snapshots: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for s in snapshots:
        d = s.get("flight_date", "")
        if d not in grouped or s["id"] > grouped[d]["id"]:
            grouped[d] = s
    return list(grouped.values())


def _compute_delays(snapshots: list[dict]) -> list[int]:
    delays = []
    for s in snapshots:
        if s.get("arrival_time") and s.get("updated_arrival_time"):
            try:
                orig = datetime.fromisoformat(s["arrival_time"].replace("Z", "+00:00"))
                upd = datetime.fromisoformat(s["updated_arrival_time"].replace("Z", "+00:00"))
                d = int((upd - orig).total_seconds() / 60)
                if d > 0:
                    delays.append(d)
            except (ValueError, TypeError):
                continue
    return delays


class Insights:
    def __init__(self, db: Database):
        self.db = db

    def rotation_dependency(self, flight_number: str) -> dict:
        snapshots = _per_date(self.db.get_history(flight_number, limit=100))
        results = []
        for s in snapshots:
            try:
                raw = json.loads(s.get("raw_json", "{}"))
                prev = raw.get("data", {}).get("flights", [{}])[0].get("prevFlight")
                if not prev:
                    continue
                prev_fn = f"{prev.get('carrierCode', 'AI')}{prev['flightNumber']}"
                prev_tail = prev.get("tailNumber")
                curr_tail = s.get("tail_number")
                same_aircraft = bool(prev_tail and curr_tail and prev_tail == curr_tail)
                prev_snap = self.db.get_latest_snapshot(prev_fn)
                prev_status = prev_snap["status"] if prev_snap else None
                results.append({
                    "flight_date": s["flight_date"],
                    "prev_flight": prev_fn,
                    "same_aircraft": same_aircraft,
                    "prev_status": prev_status,
                    "curr_status": s["status"],
                })
            except (json.JSONDecodeError, KeyError, IndexError):
                continue

        n = len(results)
        if not n:
            return {"available": False, "detail": "No rotation data (prevFlight)"}

        prev_cancelled = sum(1 for r in results if r["prev_status"] == "cancelled")
        prev_delayed = sum(1 for r in results if r["prev_status"] == "delayed")
        both_delayed = sum(1 for r in results if r["prev_status"] == "delayed" and r["curr_status"] == "delayed")
        both_cancelled = sum(1 for r in results if r["prev_status"] == "cancelled" and r["curr_status"] == "cancelled")
        same_ac = sum(1 for r in results if r["same_aircraft"])

        return {
            "available": True,
            "days_with_data": n,
            "same_aircraft_days": same_ac,
            "prev_cancelled_days": prev_cancelled,
            "prev_delayed_days": prev_delayed,
            "both_delayed_days": both_delayed,
            "both_cancelled_days": both_cancelled,
            "affected_days": both_delayed + both_cancelled,
            "affected_pct": round((both_delayed + both_cancelled) / n * 100, 1) if n else 0.0,
        }

    def backup_integrity(self, flight_number: str) -> dict:
        backup = BACKUP_PAIRS.get(flight_number)
        if not backup:
            return {"available": False, "detail": "No backup pair in config"}
        primary = _per_date(self.db.get_history(flight_number, limit=10000))
        backup_history = _per_date(self.db.get_history(backup, limit=10000))

        cancelled: set[str] = {s["flight_date"] for s in primary if s["status"] == "cancelled"}
        backup_status: dict[str, str] = {}
        for s in backup_history:
            d = s["flight_date"]
            if d not in backup_status:
                backup_status[d] = s["status"]

        avail = sum(1 for d in cancelled if backup_status.get(d) and backup_status[d] != "cancelled")
        total = len(cancelled)
        return {
            "available": True,
            "backup_flight": backup,
            "cancellation_days": total,
            "backup_available_days": avail,
            "backup_unavailable_days": total - avail,
            "coverage_pct": round(avail / total * 100, 1) if total else 100.0,
        }

    def trend_analysis(self, flight_number: str) -> dict:
        snaps = _per_date(self.db.get_history(flight_number, limit=10000))
        if not snaps:
            return {"available": False, "detail": "No data"}
        snaps.sort(key=lambda s: s["flight_date"])
        recent = snaps[-7:] if len(snaps) >= 7 else snaps
        overall = snaps

        def _m(snaps_list):
            n = len(snaps_list)
            if not n:
                return None
            cancelled = sum(1 for s in snaps_list if s["status"] == "cancelled")
            delayed = sum(1 for s in snaps_list if s["status"] == "delayed")
            operated = sum(1 for s in snaps_list if s["status"] in ("arrived", "departed", "airborne", "scheduled", "boarding", "boarding_closed", "taxiing"))
            delays = _compute_delays(snaps_list)
            return {
                "days": n,
                "cancelled": cancelled,
                "delayed": delayed,
                "operated": operated,
                "cancellation_pct": round(cancelled / n * 100, 1),
                "delay_pct": round(delayed / n * 100, 1),
                "on_time_pct": round(operated / n * 100, 1),
                "avg_delay_min": round(sum(delays) / len(delays), 1) if delays else 0.0,
            }

        rm = _m(recent)
        om = _m(overall)
        if not rm or not om:
            return {"available": False, "detail": "Insufficient data"}

        improvements = (
            (rm["on_time_pct"] - om["on_time_pct"])
            - (rm["cancellation_pct"] - om["cancellation_pct"])
            - (rm["delay_pct"] - om["delay_pct"])
            - (rm["avg_delay_min"] - om["avg_delay_min"]) / 10
        )
        direction = "improving" if improvements > 2 else "declining" if improvements < -2 else "stable"

        return {
            "available": True,
            "recent": rm,
            "overall": om,
            "direction": direction,
        }

    def reliability_score(self, flight_number: str) -> dict:
        rot = self.rotation_dependency(flight_number)
        backup = self.backup_integrity(flight_number)
        trend = self.trend_analysis(flight_number)

        score = 100.0
        deductions = {"cancellation": 0, "delay": 0, "rotation": 0}
        bonuses = {"backup": 0, "trend": 0}

        if trend.get("available"):
            om = trend["overall"]
            deductions["cancellation"] = om["cancelled"] * 5
            deductions["delay"] = om["delayed"] * 2
            score -= deductions["cancellation"] + deductions["delay"]

        if rot.get("available"):
            deductions["rotation"] = rot["affected_days"] * 3
            score -= deductions["rotation"]

        if backup.get("available") and backup["coverage_pct"] >= 80:
            bonuses["backup"] = 10
            score += 10
        if trend.get("available") and trend["direction"] == "improving":
            bonuses["trend"] = 5
            score += 5

        score = max(0, min(100, round(score)))

        return {
            "score": score,
            "breakdown": deductions | bonuses,
        }

    def all(self, flight_number: str) -> dict:
        return {
            "flight_number": flight_number,
            "rotation": self.rotation_dependency(flight_number),
            "backup": self.backup_integrity(flight_number),
            "trend": self.trend_analysis(flight_number),
            "reliability": self.reliability_score(flight_number),
        }
