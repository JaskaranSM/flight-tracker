import asyncio
import time
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

import config
import flightaware_client as fa
from flightaware_client import LiveTracking
from config import BOT_TOKEN, BACKUP_PAIRS, AUTHORIZED_CHAT_IDS
from database import Database
from analytics import Analytics
from insights import Insights
from scheduler import Scheduler
from models import fmt_ist, fmt_date, delay_str, compute_delay_min, short_city, now_ist, compute_next_poll_interval, IST


def _route_line(snap: dict) -> str:
    oc = snap.get("origin_code") or "???"
    dc = snap.get("dest_code") or "???"
    return f"{oc} \u2192 {dc}"


def _departure_block(snap: dict) -> str:
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


def _arrival_block(snap: dict) -> str:
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


def _aircraft_line(snap: dict) -> str:
    ac = snap.get("aircraft_type") or ""
    tail = snap.get("tail_number") or ""
    if ac and tail:
        return f"Aircraft: {ac} ({tail})"
    if ac:
        return f"Aircraft: {ac}"
    if tail:
        return f"Tail: {tail}"
    return ""


POLLSTATUS_PAGE_SIZE = 4
PROGRESS_WIDTH = 12
FA_CACHE_TTL = 45  # seconds; the edit loop runs every MSG_EDIT_INTERVAL (5s)
FLIGHT_SEPARATOR = "—" * 18  # divider drawn between flights in /pollstatus

# Scraper statuses for which a flight is in the air / on the move, so a
# FlightAware live-position lookup is worthwhile.
ACTIVE_STATES = {"departed", "airborne", "taxiing", "delayed"}
SCHEDULED_STATES = {"scheduled", "boarding", "boarding_closed"}

_STATUS_LABELS = {
    "scheduled": "Scheduled",
    "boarding": "Boarding",
    "boarding_closed": "Boarding Closed",
    "taxiing": "Taxiing",
    "delayed": "Delayed",
    "departed": "En Route",
    "airborne": "En Route",
    "arrived": "Arrived",
    "cancelled": "Cancelled",
    "unknown": "Unknown",
    "scrape_failed": "Unavailable",
}


def _pretty_status(status: str) -> str:
    return _STATUS_LABELS.get(status, (status or "").replace("_", " ").title())


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _aircraft_value(snap: dict) -> str:
    tail = snap.get("tail_number") or ""
    ac = snap.get("aircraft_type") or ""
    if tail and ac:
        return f"{tail} ({ac})"
    return tail or ac or ""


def _progress_bar(progress: float, state: str = "enroute", width: int = PROGRESS_WIDTH) -> str:
    if state == "scheduled":
        return "✈●" + "─" * width + "●"
    if state == "arrived":
        return "●" + "─" * width + "●✈"
    pos = round(_clamp01(progress) * width)
    return "●" + "─" * pos + "✈" + "─" * (width - pos) + "●"


def _fmt_altitude(fl) -> str:
    fl = int(fl)
    return f"FL{fl} ({fl * 100:,} ft)"


def _fmt_speed(kt) -> str:
    kt = int(kt)
    kmh = round(kt * 1.852)
    return f"{kt} kt ({kmh:,} km/h)"


def _ts_ist(ts) -> str:
    if not ts:
        return "N/A"
    try:
        return datetime.fromtimestamp(ts, IST).strftime("%H:%M IST")
    except (ValueError, OSError, TypeError, OverflowError):
        return "N/A"


def _eta_text(snap: dict, live: "LiveTracking | None", remaining_km) -> str:
    # Prefer Air India's own arrival estimate when present.
    ai = snap.get("updated_arrival_time") or snap.get("arrival_time")
    if ai:
        t = fmt_ist(ai)
        if t != "N/A":
            return t
    # Otherwise derive ETA from live tracking.
    if live and live.groundspeed_kt > 0 and remaining_km is not None:
        kmh = live.groundspeed_kt * 1.852
        eta = datetime.now(IST) + timedelta(hours=remaining_km / kmh)
        return eta.strftime("%H:%M IST")
    return "N/A"


def _bar_line(oc: str, dc: str, bar: str) -> str:
    # Monospace so the route bar keeps a consistent width across rows.
    return f"`{oc} {bar} {dc}`"


def _next_poll_text(snap: dict | None) -> str:
    """Approximate time until the scheduler next polls this flight.

    Mirrors the scheduler's cadence (interval * 0.8 - elapsed); see
    scheduler.py and models.compute_next_poll_interval.
    """
    if not snap:
        return "pending"
    interval = compute_next_poll_interval(
        snap["status"], snap.get("departure_time"), snap.get("flight_date")
    )
    if interval < 0:
        return "complete"
    try:
        elapsed = (
            datetime.fromisoformat(now_ist())
            - datetime.fromisoformat(snap["collected_at"])
        ).total_seconds() / 60
        rem_min = max(0, round(interval * 0.8 - elapsed))
        return f"{rem_min}m" if rem_min > 0 else "now"
    except (ValueError, TypeError):
        return "unknown"


def _render_flight(flight_number: str, snap: dict | None, live: "LiveTracking | None") -> str:
    body = _render_flight_body(flight_number, snap, live)
    return f"{body}\n_Next poll: {_next_poll_text(snap)}_"


def _render_flight_body(flight_number: str, snap: dict | None, live: "LiveTracking | None") -> str:
    if not snap:
        return f"\U0001f6eb *{flight_number}*\n\n_Live tracking unavailable._"

    oc = snap.get("origin_code") or "???"
    dc = snap.get("dest_code") or "???"
    route = f"{oc} → {dc}"
    status = snap.get("status") or "unknown"
    aircraft = _aircraft_value(snap)
    tail = snap.get("tail_number") or ""
    ac_type = snap.get("aircraft_type") or ""

    def header(emoji: str) -> list[str]:
        lines = [f"{emoji} *{flight_number}*  {route}", ""]
        if tail and ac_type:
            lines.append(f"Aircraft: `{tail}` ({ac_type})")
        elif aircraft:
            lines.append(f"Aircraft: `{aircraft}`")
        return lines

    if status == "cancelled":
        return f"❌ *{flight_number}*  {route}\n\nStatus: *Cancelled*"

    if status == "arrived":
        lines = header("✅")
        lines.append("Status: *Arrived*")
        lines.append("")
        lines.append(_bar_line(oc, dc, _progress_bar(1.0, "arrived")))
        lines.append("")
        lines.append(f"Arrival: *{fmt_ist(snap.get('updated_arrival_time') or snap.get('arrival_time'))}*")
        return "\n".join(lines)

    if status in SCHEDULED_STATES:
        lines = header("\U0001f6eb")
        lines.append(f"Status: *{_pretty_status(status)}*")
        lines.append("")
        lines.append(_bar_line(oc, dc, _progress_bar(0.0, "scheduled")))
        lines.append("")
        lines.append(f"STD: *{fmt_ist(snap.get('departure_time'))}*")
        return "\n".join(lines)

    if status in ACTIVE_STATES:
        lines = header("\U0001f6eb")
        lines.append(f"Status: *{_pretty_status(status)}*")
        if live is None:
            lines.append("")
            lines.append("_Live tracking unavailable._")
            return "\n".join(lines)

        # Endpoints: airport registry first, FlightAware coords as fallback.
        o = fa.AIRPORT_COORDS.get(oc) or live.origin_coord
        d = fa.AIRPORT_COORDS.get(dc) or live.dest_coord
        cur = live.coord
        if o and d:
            total = fa.distance_km(o, d)
            flown = fa.distance_km(o, cur)
            remaining = fa.distance_km(cur, d)
            progress = _clamp01(flown / total) if total else 0.0
        else:
            total = flown = remaining = None
            progress = 0.0

        lines.append("")
        lines.append(_bar_line(oc, dc, _progress_bar(progress, "enroute")))
        lines.append("")
        lines.append(f"Progress: *{round(progress * 100)}%*")
        if total is not None:
            lines.append(f"Distance: {round(flown)} / {round(total)} km")
            lines.append(f"Remaining: {round(remaining)} km")
        lines.append("")
        lines.append(f"Altitude: `{_fmt_altitude(live.altitude_fl)}`")
        lines.append(f"Ground Speed: `{_fmt_speed(live.groundspeed_kt)}`")
        lines.append("")
        lines.append(f"ETA: *{_eta_text(snap, live, remaining)}*")
        lines.append(f"_Last Update: {_ts_ist(live.timestamp)}_")
        return "\n".join(lines)

    # unknown / scrape_failed / anything else
    lines = header("\U0001f6eb")
    lines.append(f"Status: *{_pretty_status(status)}*")
    lines.append("")
    lines.append("_Live tracking unavailable._")
    return "\n".join(lines)


class FlightBot:
    def __init__(self, db: Database, analytics: Analytics, scheduler: Scheduler | None = None):
        self.db = db
        self.analytics = analytics
        self.insights = Insights(db)
        self.scheduler = scheduler
        self._chat_ids: set[int] = set(AUTHORIZED_CHAT_IDS)
        self.application: Application | None = Application.builder().token(BOT_TOKEN).build()

        self.application.add_handler(CommandHandler("start", self._start))
        self.application.add_handler(CommandHandler("track", self._track))
        self.application.add_handler(CommandHandler("untrack", self._untrack))
        self.application.add_handler(CommandHandler("list", self._list))
        self.application.add_handler(CommandHandler("status", self._status))
        self.application.add_handler(CommandHandler("refresh", self._refresh))
        self.application.add_handler(CommandHandler("refresh_all", self._refresh_all))
        self.application.add_handler(CommandHandler("history", self._history))
        self.application.add_handler(CommandHandler("stats", self._stats))
        self.application.add_handler(CommandHandler("export_csv", self._export_csv))
        self.application.add_handler(CommandHandler("export_xlsx", self._export_xlsx))
        self.application.add_handler(CommandHandler("insights", self._insights))
        self.application.add_handler(CommandHandler("fetch", self._fetch))
        self.application.add_handler(CommandHandler("pollstatus", self._pollstatus))
        self.application.add_handler(
            CallbackQueryHandler(self._pollstatus_callback, pattern=r"^pollstatus_page_")
        )
        self._pollstatus_msgs: dict[int, int] = {}
        self._pollstatus_tasks: dict[int, asyncio.Task] = {}
        self._pollstatus_page: dict[int, int] = {}
        # flight_number -> (monotonic_fetched_at, LiveTracking | None)
        self._fa_cache: dict[str, tuple[float, "LiveTracking | None"]] = {}

    async def _start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cid = update.effective_chat.id
        self._chat_ids.add(cid)
        await update.message.reply_text(
            "Flight Tracker Bot active.\n\n"
            "Commands:\n"
            "/track AI482 — Track a flight\n"
            "/untrack AI482 — Stop tracking\n"
            "/list — List tracked flights\n"
            "/status AI482 — Current flight status\n"
            "/refresh AI482 — Force re-fetch a flight\n"
            "/refresh_all — Re-fetch all tracked flights\n"
            "/fetch AI484 20260529 — Fetch data for a specific date\n"
            "/history AI482 7 — Recent history\n"
            "/stats AI482 — Reliability statistics\n"
            "/insights AI482 — Comprehensive insights\n"
            "/pollstatus — Live poll status for all flights\n"
            "/export_csv — Export snapshots to CSV\n"
            "/export_xlsx — Export snapshots to XLSX"
        )

    async def _track(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /track AI481")
            return
        flight = context.args[0].upper()
        self.db.add_tracked_flight(flight)
        self._chat_ids.add(update.effective_chat.id)
        await update.message.reply_text(f"Tracking {flight}.")

    async def _untrack(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /untrack AI482")
            return
        flight = context.args[0].upper()
        self.db.remove_tracked_flight(flight)
        await update.message.reply_text(f"Stopped tracking {flight}.")

    async def _list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        flights = self.db.get_tracked_flights()
        if not flights:
            await update.message.reply_text("No flights tracked.")
            return
        lines = [f"{f['flight_number']} (since {fmt_date(f['added_at'])})" for f in flights]
        await update.message.reply_text("Tracked flights:\n" + "\n".join(lines))

    async def _status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /status AI482")
            return
        flight = context.args[0].upper()
        snap = self.db.get_latest_snapshot(flight)
        if not snap:
            await update.message.reply_text(f"No data for {flight}.")
            return

        lines = [
            f"*{flight} — {_route_line(snap)}*",
            f"Status: {snap['status'].upper()}"
            + (f" ({snap['flight_status']})" if snap.get("flight_status") else ""),
            "",
            _departure_block(snap),
            _arrival_block(snap),
        ]

        ac = _aircraft_line(snap)
        if ac:
            lines.append("")
            lines.append(ac)

        lines.append("")
        lines.append(f"\U0001f504 Updated: {fmt_date(snap.get('collected_at'))}")

        await update.message.reply_text("\n".join(lines))

    async def _refresh(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /refresh AI482")
            return
        flight = context.args[0].upper()
        if not self.db.is_tracked(flight):
            await update.message.reply_text(f"{flight} is not being tracked. Use /track first.")
            return
        if not self.scheduler:
            await update.message.reply_text("Scheduler not available.")
            return
        await update.message.reply_text(f"Refetching {flight}...")
        try:
            await self.scheduler.poll_flight(flight)
        except Exception as e:
            await update.message.reply_text(f"Refresh failed: {e}")
            return
        snap = self.db.get_latest_snapshot(flight)
        if snap:
            lines = [
                f"*{flight} — {_route_line(snap)}*",
                f"Status: {snap['status'].upper()}"
                + (f" ({snap['flight_status']})" if snap.get("flight_status") else ""),
                "",
                _departure_block(snap),
                _arrival_block(snap),
            ]
            ac = _aircraft_line(snap)
            if ac:
                lines.append("")
                lines.append(ac)
            lines.append("")
            lines.append(f"\U0001f504 {fmt_date(snap.get('collected_at'))}")
            await update.message.reply_text("\n".join(lines))
        else:
            await update.message.reply_text(f"No data returned for {flight}.")

    async def _refresh_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        flights = self.db.get_tracked_flights()
        if not flights:
            await update.message.reply_text("No flights tracked.")
            return
        if not self.scheduler:
            await update.message.reply_text("Scheduler not available.")
            return
        await update.message.reply_text(f"Refetching {len(flights)} flight(s)...")
        results = []
        for f in flights:
            fn = f["flight_number"]
            try:
                await self.scheduler.poll_flight(fn)
                snap = self.db.get_latest_snapshot(fn)
                s = snap["status"].upper() if snap else "no data"
                results.append(f"{fn}: {s}")
            except Exception as e:
                results.append(f"{fn}: error — {e}")
        await update.message.reply_text("\n".join(results))

    async def _history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /history AI482 7")
            return
        flight = context.args[0].upper()
        limit = int(context.args[1]) if len(context.args) > 1 else 7
        snapshots = self.db.get_history(flight, limit)
        if not snapshots:
            await update.message.reply_text(f"No history for {flight}.")
            return
        lines = [f"*{flight} — Last {limit} records*"]
        for s in snapshots:
            date = fmt_date(s.get("flight_date"))
            st = s["status"].upper()
            route = _route_line(s)
            dep = fmt_ist(s.get("updated_departure_time"))
            arr = fmt_ist(s.get("updated_arrival_time"))
            lines.append(f"{date} | {st} | {route} | {dep}\u2192{arr}")
        await update.message.reply_text("\n".join(lines))

    async def _stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /stats AI482")
            return
        flight = context.args[0].upper()
        rel = self.analytics.route_reliability(flight)
        if not rel:
            await update.message.reply_text(f"No data for {flight}.")
            return

        delays = self.analytics.delay_metrics(flight)
        total = rel["observed_days"]
        otp = round((rel["operated_days"] / total) * 100, 1) if total else 0

        lines = [
            f"*{flight} Statistics*",
            f"Observed: {rel['observed_days']} days",
            f"Operated: {rel['operated_days']} days",
            f"Cancelled: {rel['cancelled_days']} days",
            f"On-Time: {otp}%",
        ]
        if delays:
            lines.append(f"Avg Delay: {delays['average_delay']} min")
            lines.append(f"Max Delay: {delays['max_delay']} min")
            lines.append(f"P95 Delay: {delays['p95_delay']} min")

        if flight in BACKUP_PAIRS:
            bk = BACKUP_PAIRS[flight]
            br = self.analytics.backup_reliability(flight, bk)
            lines.append("")
            lines.append(f"*Backup ({bk})*")
            lines.append(f"{flight} cancelled: {br.get(f'{flight}_cancelled_days', 'N/A')}")
            lines.append(f"{bk} cancelled: {br.get(f'{bk}_cancelled_days', 'N/A')}")

        await update.message.reply_text("\n".join(lines))

    async def _export_csv(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        path = self.analytics.export_csv()
        with open(path, "rb") as f:
            await update.message.reply_document(document=f, filename=path.name)
        await update.message.reply_text(f"CSV exported: {path}")

    async def _export_xlsx(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        path = self.analytics.export_xlsx()
        with open(path, "rb") as f:
            await update.message.reply_document(document=f, filename=path.name)
        await update.message.reply_text(f"XLSX exported: {path}")

    async def _fetch(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args or len(context.args) < 2:
            await update.message.reply_text("Usage: /fetch AI484 20260529")
            return
        flight = context.args[0].upper()
        date_str = context.args[1]
        if len(date_str) != 8 or not date_str.isdigit():
            await update.message.reply_text("Date must be in YYYYMMDD format (e.g. 20260529)")
            return
        if not self.scheduler:
            await update.message.reply_text("Scheduler not available.")
            return
        await update.message.reply_text(f"Fetching {flight} for {date_str}...")
        try:
            snap = await self.scheduler.poll_flight_for_date(flight, date_str)
        except Exception as e:
            await update.message.reply_text(f"Fetch failed: {e}")
            return
        if snap is None:
            await update.message.reply_text(f"No data returned for {flight} on {date_str}.")
            return
        lines = [
            f"*{flight} — {_route_line({'origin_code': snap.origin_code, 'dest_code': snap.dest_code})}*",
            f"Date: {snap.flight_date}",
            f"Status: {snap.status.upper()}"
            + (f" ({snap.flight_status})" if snap.flight_status else ""),
            "",
            _departure_block({
                "updated_departure_time": snap.updated_departure_time,
                "departure_time": snap.departure_time,
                "origin_gate": snap.origin_gate,
                "origin_terminal": snap.origin_terminal,
            }),
            _arrival_block({
                "updated_arrival_time": snap.updated_arrival_time,
                "arrival_time": snap.arrival_time,
                "dest_gate": snap.dest_gate,
                "dest_terminal": snap.dest_terminal,
            }),
        ]
        ac = _aircraft_line({
            "aircraft_type": snap.aircraft_type,
            "tail_number": snap.tail_number,
        })
        if ac:
            lines.append("")
            lines.append(ac)
        lines.append("")
        lines.append(f"\U0001f504 {fmt_date(snap.collected_at)}")
        await update.message.reply_text("\n".join(lines))

    async def _insights(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /insights AI482")
            return
        flight = context.args[0].upper()
        if not self.db.is_tracked(flight):
            await update.message.reply_text(f"{flight} is not being tracked.")
            return

        i = self.insights.all(flight)
        lines = [f"*{flight} — Insights*"]

        rel = i["reliability"]
        lines.append(f"*Reliability Score:* {rel['score']}/100")

        rot = i["rotation"]
        if rot.get("available"):
            lines.append("")
            lines.append(f"*Rotation Dependency:*")
            lines.append(f"Same aircraft: {rot['same_aircraft_days']}/{rot['days_with_data']} days")
            lines.append(f"Affected by prevFlight: {rot['affected_days']} days ({rot['affected_pct']}%)")
            lines.append(f"Prev cancelled: {rot['prev_cancelled_days']} | Prev delayed: {rot['prev_delayed_days']}")
            lines.append(f"Both delayed: {rot['both_delayed_days']} | Both cancelled: {rot['both_cancelled_days']}")
        else:
            lines.append("")
            lines.append(f"*Rotation Dependency:* {rot.get('detail', 'N/A')}")

        bk = i["backup"]
        if bk.get("available"):
            lines.append("")
            lines.append(f"*Backup Integrity ({bk['backup_flight']}):*")
            lines.append(f"Cancellation days: {bk['cancellation_days']}")
            lines.append(f"Backup available: {bk['backup_available_days']} | Unavailable: {bk['backup_unavailable_days']}")
            lines.append(f"Coverage: {bk['coverage_pct']}%")
        else:
            lines.append("")
            lines.append(f"*Backup:* {bk.get('detail', 'N/A')}")

        tr = i["trend"]
        if tr.get("available"):
            r, o = tr["recent"], tr["overall"]
            direction_icon = "\u2191" if tr["direction"] == "improving" else "\u2193" if tr["direction"] == "declining" else "\u2192"
            lines.append("")
            lines.append(f"*Trend Analysis* {direction_icon} {tr['direction']}")
            lines.append(f"Recent ({r['days']}d): {r['cancellation_pct']}% cancelled, {r['delay_pct']}% delayed, {r['avg_delay_min']}min avg delay")
            lines.append(f"Overall ({o['days']}d): {o['cancellation_pct']}% cancelled, {o['delay_pct']}% delayed, {o['avg_delay_min']}min avg delay")
        else:
            lines.append("")
            lines.append(f"*Trend Analysis:* {tr.get('detail', 'N/A')}")

        lines.append("")
        lines.append("*Breakdown*")
        bd = rel["breakdown"]
        lines.append(f"Start: 100 | Cancellations: \u2212{bd['cancellation']} | Delays: \u2212{bd['delay']} | Rotation: \u2212{bd['rotation']} | Backup: +{bd['backup']} | Trend: +{bd['trend']}")

        await update.message.reply_text("\n".join(lines))

    async def _get_live_cached(self, flight_number: str) -> "LiveTracking | None":
        """FlightAware live tracking for a flight, cached for FA_CACHE_TTL seconds."""
        cached = self._fa_cache.get(flight_number)
        if cached and (time.monotonic() - cached[0]) < FA_CACHE_TTL:
            return cached[1]
        live = await asyncio.to_thread(fa.fetch_live_tracking, flight_number)
        self._fa_cache[flight_number] = (time.monotonic(), live)
        return live

    async def _build_pollstatus_view(self, chat_id: int):
        """Build (text, reply_markup) for the current page of tracked flights."""
        flights = self.db.get_tracked_flights()
        if not flights:
            return "No flights tracked.", None

        total = len(flights)
        pages = (total + POLLSTATUS_PAGE_SIZE - 1) // POLLSTATUS_PAGE_SIZE
        page = max(0, min(self._pollstatus_page.get(chat_id, 0), pages - 1))
        self._pollstatus_page[chat_id] = page

        start = page * POLLSTATUS_PAGE_SIZE
        page_flights = flights[start:start + POLLSTATUS_PAGE_SIZE]

        snaps = [self.db.get_latest_snapshot(f["flight_number"]) for f in page_flights]

        # Fetch FlightAware data only for in-progress flights, concurrently.
        async def live_for(snap):
            if snap and snap.get("status") in ACTIVE_STATES:
                return await self._get_live_cached(snap["flight_number"])
            return None

        lives = await asyncio.gather(*(live_for(s) for s in snaps))

        blocks = [
            _render_flight(f["flight_number"], snap, live)
            for f, snap, live in zip(page_flights, snaps, lives)
        ]
        text = f"\n\n{FLIGHT_SEPARATOR}\n\n".join(blocks)

        markup = None
        if pages > 1:
            text += f"\n\n{FLIGHT_SEPARATOR}\n\n_Page {page + 1}/{pages}_"
            row = []
            if page > 0:
                row.append(InlineKeyboardButton("⬅ Previous", callback_data=f"pollstatus_page_{page - 1}"))
            if page < pages - 1:
                row.append(InlineKeyboardButton("Next ➡", callback_data=f"pollstatus_page_{page + 1}"))
            markup = InlineKeyboardMarkup([row])

        return text, markup

    async def _pollstatus(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cid = update.effective_chat.id
        self._chat_ids.add(cid)
        self._pollstatus_page[cid] = 0

        old_task = self._pollstatus_tasks.pop(cid, None)
        if old_task:
            old_task.cancel()
            try:
                await old_task
            except asyncio.CancelledError:
                pass

        old_msg_id = self._pollstatus_msgs.pop(cid, None)
        if old_msg_id:
            try:
                await self.application.bot.delete_message(chat_id=cid, message_id=old_msg_id)
            except Exception:
                pass

        text, markup = await self._build_pollstatus_view(cid)
        msg = await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
        self._pollstatus_msgs[cid] = msg.message_id
        self._pollstatus_tasks[cid] = asyncio.create_task(self._pollstatus_edit_loop(cid))

    async def _pollstatus_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        cid = query.message.chat.id
        try:
            page = int(query.data.rsplit("_", 1)[1])
        except (ValueError, IndexError):
            page = 0
        self._pollstatus_page[cid] = page
        self._pollstatus_msgs[cid] = query.message.message_id

        text, markup = await self._build_pollstatus_view(cid)
        try:
            await self.application.bot.edit_message_text(
                chat_id=cid, message_id=query.message.message_id,
                text=text, reply_markup=markup, parse_mode="Markdown",
            )
        except Exception:
            pass
        await query.answer()

    async def _pollstatus_edit_loop(self, chat_id: int):
        last_text = None
        try:
            while True:
                await asyncio.sleep(config.MSG_EDIT_INTERVAL)
                msg_id = self._pollstatus_msgs.get(chat_id)
                if msg_id is None:
                    break
                text, markup = await self._build_pollstatus_view(chat_id)
                if text == last_text:
                    continue
                last_text = text
                try:
                    await self.application.bot.edit_message_text(
                        chat_id=chat_id, message_id=msg_id, text=text,
                        reply_markup=markup, parse_mode="Markdown",
                    )
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass

    async def send_message(self, text: str):
        if not self.application:
            return
        tasks = []
        for cid in self._chat_ids:
            tasks.append(self.application.bot.send_message(chat_id=cid, text=text))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self):
        app = self.application
        try:
            await app.initialize()
            await app.updater.start_polling()
            await app.start()
            try:
                await asyncio.Event().wait()
            finally:
                await app.stop()
                await app.shutdown()
        except Exception:
            pass
