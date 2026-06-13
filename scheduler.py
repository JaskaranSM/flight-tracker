import asyncio
import logging
from datetime import datetime

from database import Database
from analytics import Analytics
from models import (
    IST, compute_next_poll_interval, FlightStatus, now_ist,
    fmt_ist, fmt_date, delay_str, compute_delay_min, short_city,
)
import config

logger = logging.getLogger(__name__)

EMOJI = {
    FlightStatus.ARRIVED.value: "\u2705",
    FlightStatus.DEPARTED.value: "\u2705",
    FlightStatus.BOARDING.value: "\u2705",
    FlightStatus.TAXIING.value: "\u2705",
    FlightStatus.DELAYED.value: "\u26a0\ufe0f",
    FlightStatus.CANCELLED.value: "\U0001f6a8",
    FlightStatus.SCHEDULED.value: "\U0001f4c5",
}


def _route(snap: dict) -> str:
    oc = snap.get("origin_city") or snap.get("origin_code") or "???"
    dc = snap.get("dest_city") or snap.get("dest_code") or "???"
    oc_code = snap.get("origin_code") or ""
    dc_code = snap.get("dest_code") or ""
    return f"{short_city(oc)} ({oc_code}) \u2192 {short_city(dc)} ({dc_code})"


def _departure_detail(snap: dict) -> str:
    upd = snap.get("updated_departure_time")
    sched = snap.get("departure_time")
    gate = snap.get("origin_gate") or ""
    term = snap.get("origin_terminal") or ""
    extra = ""
    if gate and gate != "N/A":
        extra += f" | Gate {gate}"
    if term and term != "N/A":
        extra += f" | Terminal {term}"

    lines = [f"Departure: {fmt_ist(upd)}{delay_str(sched, upd)}"]
    lines.append(f"  Scheduled: {fmt_ist(sched)}{extra}")
    return "\n".join(lines)


def _arrival_detail(snap: dict) -> str:
    upd = snap.get("updated_arrival_time")
    sched = snap.get("arrival_time")
    gate = snap.get("dest_gate") or ""
    term = snap.get("dest_terminal") or ""
    extra = ""
    if gate and gate != "N/A":
        extra += f" | Gate {gate}"
    if term and term != "N/A":
        extra += f" | Terminal {term}"

    lines = [f"Arrival: {fmt_ist(upd)}{delay_str(sched, upd)}"]
    lines.append(f"  Scheduled: {fmt_ist(sched)}{extra}")
    return "\n".join(lines)


def _aircraft(snap: dict) -> str:
    ac = snap.get("aircraft_type") or ""
    tail = snap.get("tail_number") or ""
    if ac and tail:
        return f"Aircraft: {ac} ({tail})"
    if ac:
        return f"Aircraft: {ac}"
    if tail:
        return f"Tail: {tail}"
    return ""


def _notify_cancelled(flight: str, snap: dict, db: Database) -> str:
    backup = config.BACKUP_PAIRS.get(flight)
    date = fmt_date(snap.get("flight_date"))
    route = _route(snap)

    if not backup:
        return (
            f"\U0001f6a8 {flight} CANCELLED\n\n"
            f"{date} — {route}\n\n"
            f"No backup configured."
        )

    backup_snap = db.get_latest_snapshot(backup)
    if not backup_snap or backup_snap["status"] == FlightStatus.CANCELLED.value:
        return (
            f"\U0001f6a8 CRITICAL\n\n"
            f"{date}\n\n"
            f"{flight} CANCELLED\n"
            f"{backup} CANCELLED\n\n"
            f"No flights available on this route today."
        )

    return (
        f"\U0001f6a8 {flight} CANCELLED\n\n"
        f"{date} — {route}\n\n"
        f"Backup {backup}: AVAILABLE"
    )


def _notify_delayed(flight: str, snap: dict) -> str:
    date = fmt_date(snap.get("flight_date"))
    route = _route(snap)
    dep_delay = compute_delay_min(snap.get("departure_time"), snap.get("updated_departure_time"))
    arr_delay = compute_delay_min(snap.get("arrival_time"), snap.get("updated_arrival_time"))
    max_delay = max(d for d in [dep_delay, arr_delay] if d is not None) if any(
        d is not None for d in [dep_delay, arr_delay]
    ) else None

    lines = [
        f"\u26a0\ufe0f {flight} DELAYED",
        "",
        f"{date} — {route}",
    ]
    if max_delay is not None and max_delay > 0:
        lines.append(f"Delay: {max_delay} min")
    lines.append("")
    lines.append(_departure_detail(snap))
    lines.append(_arrival_detail(snap))

    ac = _aircraft(snap)
    if ac:
        lines.append("")
        lines.append(ac)

    return "\n".join(lines)


def _notify_arrived(flight: str, snap: dict) -> str:
    date = fmt_date(snap.get("flight_date"))
    route = _route(snap)
    status = snap.get("flight_status") or "ON_TIME"

    lines = [
        f"\u2705 {flight} ARRIVED",
        "",
        f"{date} — {route}",
        f"Status: {status}",
        "",
        _departure_detail(snap),
        _arrival_detail(snap),
    ]

    ac = _aircraft(snap)
    if ac:
        lines.append("")
        lines.append(ac)

    return "\n".join(lines)


def _notify_departed(flight: str, snap: dict) -> str:
    date = fmt_date(snap.get("flight_date"))
    route = _route(snap)

    lines = [
        f"\u2705 {flight} DEPARTED",
        "",
        f"{date} — {route}",
        "",
        _departure_detail(snap),
        _arrival_detail(snap),
    ]

    ac = _aircraft(snap)
    if ac:
        lines.append("")
        lines.append(ac)

    return "\n".join(lines)


def _notify_taxiing(flight: str, snap: dict) -> str:
    date = fmt_date(snap.get("flight_date"))
    route = _route(snap)

    lines = [
        f"\u2705 {flight} TAXIING",
        "",
        f"{date} — {route}",
        "",
        _departure_detail(snap),
        _arrival_detail(snap),
    ]

    ac = _aircraft(snap)
    if ac:
        lines.append("")
        lines.append(ac)

    return "\n".join(lines)


def _notify_scheduled(flight: str, snap: dict) -> str:
    date = fmt_date(snap.get("flight_date"))
    route = _route(snap)
    return (
        f"\U0001f4c5 {flight} SCHEDULED\n\n"
        f"{date} — {route}\n\n"
        f"Departure: {fmt_ist(snap.get('departure_time'))}"
    )


def _notify_state_change(flight: str, snap: dict,
                          old_status: str, new_status: str,
                          db: Database) -> str:
    if old_status != FlightStatus.AIRBORNE.value and new_status == FlightStatus.AIRBORNE.value:
        return _notify_departed(flight, snap)
    if new_status == FlightStatus.CANCELLED.value:
        return _notify_cancelled(flight, snap, db)
    elif new_status == FlightStatus.DELAYED.value:
        return _notify_delayed(flight, snap)
    elif new_status == FlightStatus.ARRIVED.value:
        return _notify_arrived(flight, snap)
    elif new_status == FlightStatus.DEPARTED.value:
        return _notify_departed(flight, snap)
    elif new_status == FlightStatus.TAXIING.value:
        return _notify_taxiing(flight, snap)
    else:
        return _notify_scheduled(flight, snap)


class Scheduler:
    def __init__(self, db: Database, analytics: Analytics,
                 scraper_func, notify_func):
        self.db = db
        self.analytics = analytics
        self.scraper = scraper_func
        self.notify = notify_func
        self._task: asyncio.Task | None = None

    async def _poll(self, flight_number: str, date_str: str | None = None):
        flight_number = flight_number.upper()
        latest = self.db.get_latest_snapshot(flight_number)

        if date_str is None:
            date_str = datetime.now(IST).strftime("%Y%m%d")

        logger.info(f"Polling {flight_number} for {date_str}...")
        try:
            num = flight_number.replace("AI", "")
            api_data = await self.scraper(int(num), date_str)
        except Exception as e:
            logger.error(f"Scraper failed for {flight_number}: {e}")
            return

        if api_data is None:
            logger.warning(f"No API data for {flight_number} on {date_str}")
            return None

        snapshot = self.db.parse_snapshot_from_api(flight_number, api_data)
        if not snapshot:
            logger.warning(f"Could not parse snapshot for {flight_number} on {date_str}")
            return None

        old_status = latest["status"] if latest else FlightStatus.SCHEDULED.value
        needs_notify = old_status != snapshot.status and (not latest or not latest.get("notified"))

        self.db.save_snapshot(snapshot, notified=needs_notify)

        if needs_notify:
            logger.info(f"{flight_number}: {old_status} -> {snapshot.status}")
            self.db.save_event(
                flight_number, snapshot.flight_date, old_status, snapshot.status
            )
            snap_dict = dict(
                flight_number=snapshot.flight_number,
                flight_date=snapshot.flight_date,
                status=snapshot.status,
                flight_state=snapshot.flight_state,
                flight_status=snapshot.flight_status,
                departure_time=snapshot.departure_time,
                updated_departure_time=snapshot.updated_departure_time,
                arrival_time=snapshot.arrival_time,
                updated_arrival_time=snapshot.updated_arrival_time,
                origin_code=snapshot.origin_code,
                origin_city=snapshot.origin_city,
                origin_name=snapshot.origin_name,
                origin_terminal=snapshot.origin_terminal,
                origin_gate=snapshot.origin_gate,
                dest_code=snapshot.dest_code,
                dest_city=snapshot.dest_city,
                dest_name=snapshot.dest_name,
                dest_terminal=snapshot.dest_terminal,
                dest_gate=snapshot.dest_gate,
                aircraft_type=snapshot.aircraft_type,
                tail_number=snapshot.tail_number,
                collected_at=snapshot.collected_at,
            )
            msg = _notify_state_change(
                flight_number, snap_dict,
                old_status, snapshot.status, self.db,
            )
            await self.notify(msg)

        return snapshot

    async def poll_flight(self, flight_number: str):
        return await self._poll(flight_number)

    async def poll_flight_for_date(self, flight_number: str, date_str: str):
        return await self._poll(flight_number, date_str)

    async def poll_loop(self):
        logger.info("Poll loop started")
        while True:
            try:
                flights = self.db.get_tracked_flights()
                if not flights:
                    await asyncio.sleep(60)
                    continue

                for f in flights:
                    fn = f["flight_number"]
                    latest = self.db.get_latest_snapshot(fn)
                    if latest:
                        interval = compute_next_poll_interval(
                            latest["status"], latest.get("departure_time"), latest.get("flight_date")
                        )
                        if interval < 0:
                            logger.info(f"[{fn}] Polling complete (final status reached)")
                            continue
                        try:
                            elapsed = (
                                datetime.fromisoformat(now_ist())
                                - datetime.fromisoformat(latest["collected_at"])
                            ).total_seconds() / 60
                            remaining_min = max(0, round(interval * 0.8 - elapsed))
                            if elapsed < interval * 0.8:
                                logger.info(f"[{fn}] Will be polled in ~{remaining_min}m")
                                continue
                        except (ValueError, TypeError):
                            pass
                    try:
                        await self.poll_flight(fn)
                    except Exception:
                        logger.exception(f"[{fn}] Poll failed")

                await asyncio.sleep(config.POLL_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Poll loop error: {e}")
                await asyncio.sleep(60)

    async def poll_all(self):
        flights = self.db.get_tracked_flights()
        if not flights:
            logger.info("No flights to poll at startup")
            return
        logger.info(f"Polling {len(flights)} flight(s) at startup...")
        for f in flights:
            try:
                await self.poll_flight(f["flight_number"])
            except Exception:
                logger.exception(f"[{f['flight_number']}] Startup poll failed")

    def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self.poll_loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
