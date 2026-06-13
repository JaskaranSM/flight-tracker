from enum import Enum
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional


class FlightStatus(str, Enum):
    SCHEDULED = "scheduled"
    BOARDING = "boarding"
    BOARDING_CLOSED = "boarding_closed"
    TAXIING = "taxiing"
    DELAYED = "delayed"
    DEPARTED = "departed"
    AIRBORNE = "airborne"
    ARRIVED = "arrived"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"
    SCRAPE_FAILED = "scrape_failed"


IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> str:
    return datetime.now(IST).isoformat()


def normalize_status(flight_state: Optional[str], flight_status: Optional[str]) -> str:
    if not flight_state:
        return FlightStatus.SCHEDULED.value

    state = flight_state.upper()

    if state == "CANCELLED":
        return FlightStatus.CANCELLED.value
    if state == "ARRIVED":
        return FlightStatus.ARRIVED.value
    if state == "NOW BOARDING":
        return FlightStatus.BOARDING.value
    if state == "BOARDING CLOSED":
        return FlightStatus.BOARDING_CLOSED.value
    if state == "DEPARTED":
        if flight_status and flight_status.upper() == "DELAYED":
            return FlightStatus.DELAYED.value
        return FlightStatus.DEPARTED.value
    if state == "TAXIING":
        return FlightStatus.TAXIING.value
    if state == "IN FLIGHT":
        return FlightStatus.AIRBORNE.value

    return FlightStatus.UNKNOWN.value


def compute_next_poll_interval(status: str,
                               departure_time_utc: str | None = None,
                               flight_date: str | None = None) -> int:
    if status in (FlightStatus.ARRIVED.value, FlightStatus.CANCELLED.value):
        if flight_date:
            today = datetime.now(IST).strftime("%Y-%m-%d")
            if flight_date < today:
                return 10
        return -1

    if departure_time_utc:
        try:
            dep = datetime.fromisoformat(departure_time_utc.replace("Z", "+00:00"))
            now = datetime.now(IST)
            hours_until = (dep - now).total_seconds() / 3600
            if hours_until > 12:
                return 60
            elif hours_until > 0:
                return 30
        except (ValueError, TypeError):
            pass

    return 10


def extract_flight_date(data: dict) -> Optional[str]:
    flights = data.get("data", {}).get("flights", [])
    if not flights:
        return None
    return flights[0].get("flightDateUtc", "").split("T")[0]


def fmt_ist(utc_str: str | None) -> str:
    if not utc_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        return dt.astimezone(IST).strftime("%H:%M IST")
    except (ValueError, TypeError):
        return "N/A"


def fmt_date(d: str | None) -> str:
    if not d:
        return "N/A"
    try:
        return d.split("T")[0]
    except (ValueError, AttributeError):
        return "N/A"


def compute_delay_min(scheduled_utc: str | None, updated_utc: str | None) -> int | None:
    if not scheduled_utc or not updated_utc:
        return None
    try:
        sched = datetime.fromisoformat(scheduled_utc.replace("Z", "+00:00"))
        upd = datetime.fromisoformat(updated_utc.replace("Z", "+00:00"))
        return int((upd - sched).total_seconds() / 60)
    except (ValueError, TypeError):
        return None


def delay_str(scheduled_utc: str | None, updated_utc: str | None) -> str:
    d = compute_delay_min(scheduled_utc, updated_utc)
    if d is None:
        return ""
    if d > 0:
        return f" (+{d} min)"
    elif d < 0:
        return f" ({d} min)"
    return " (on time)"


def short_city(name: str | None) -> str:
    if not name:
        return ""
    return name.title().replace(" International", "").replace(" Intl", "")


@dataclass
class FlightSnapshotData:
    flight_number: str
    flight_date: str
    status: str
    flight_state: Optional[str] = None
    flight_status: Optional[str] = None

    departure_time: Optional[str] = None
    updated_departure_time: Optional[str] = None
    departure_local: Optional[str] = None
    updated_departure_local: Optional[str] = None

    arrival_time: Optional[str] = None
    updated_arrival_time: Optional[str] = None
    arrival_local: Optional[str] = None
    updated_arrival_local: Optional[str] = None

    origin_code: Optional[str] = None
    origin_city: Optional[str] = None
    origin_name: Optional[str] = None
    origin_terminal: Optional[str] = None
    origin_gate: Optional[str] = None

    dest_code: Optional[str] = None
    dest_city: Optional[str] = None
    dest_name: Optional[str] = None
    dest_terminal: Optional[str] = None
    dest_gate: Optional[str] = None

    aircraft_type: Optional[str] = None
    tail_number: Optional[str] = None

    raw_json: str = ""
    collected_at: str = ""
