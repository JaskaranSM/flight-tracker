import os
from pathlib import Path

BASE_DIR = Path(__file__).parent

BOT_TOKEN = os.environ.get("FLIGHT_TRACKER_BOT_TOKEN", "")

DB_PATH = BASE_DIR / "data" / "flights.db"
LOG_DIR = BASE_DIR / "logs"
EXPORT_DIR = BASE_DIR / "exports"
CSV_DIR = EXPORT_DIR / "csv"
XLSX_DIR = EXPORT_DIR / "xlsx"

DEFAULT_FLIGHTS = ["AI481", "AI482", "AI483", "AI484"]
TRACKED_FLIGHTS = os.environ.get(
    "TRACKED_FLIGHTS", ",".join(DEFAULT_FLIGHTS)
).split(",")

AUTHORIZED_CHAT_IDS = [
    int(x) for x in os.environ.get("AUTHORIZED_CHAT_IDS", "").split(",") if x
]

BACKUP_PAIRS: dict[str, str] = {"AI482": "AI484"}

POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "5"))
SCRAPE_TIMEOUT = int(os.environ.get("SCRAPE_TIMEOUT", "30"))
MSG_EDIT_INTERVAL = int(os.environ.get("MSG_EDIT_INTERVAL", "5"))

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
