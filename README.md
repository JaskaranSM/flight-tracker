# ✈️ Flight Tracker Bot

> A Telegram bot that tracks Air India flights in real time — merging an authoritative airline data source with live FlightAware aircraft positions to deliver rich status updates, reliability analytics, and live in-flight visualisation.

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white">
  <img alt="python-telegram-bot" src="https://img.shields.io/badge/python--telegram--bot-22.x-26A5E4?logo=telegram&logoColor=white">
  <img alt="Playwright" src="https://img.shields.io/badge/Playwright-stealth-2EAD33?logo=playwright&logoColor=white">
  <img alt="SQLite" src="https://img.shields.io/badge/SQLite-WAL-003B57?logo=sqlite&logoColor=white">
  <img alt="Docker" src="https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white">
</p>

---

## Overview

Airlines publish flight status, but the data is shallow and the live picture is missing. **Flight Tracker Bot** continuously polls Air India's flight-status API and enriches it with live aircraft telemetry from FlightAware, then surfaces everything through a clean Telegram interface — from a single flight's gate and delay to a fleet-wide live map of where each aircraft is right now.

It was built to answer practical questions about a specific set of routes: *Is my flight on time? Has it taken off? Where is it? How reliable is this route, and does it have a working backup?*

## Highlights

- 🛰️ **Dual-source data fusion** — the Air India scraper is the source of truth for schedule, status, aircraft and delays; FlightAware is layered on top *only* for live position, altitude and ground speed. Each source owns exactly what it's best at.
- 📡 **Live in-flight visualisation** — `/pollstatus` renders a per-flight progress bar, altitude, speed, distance flown/remaining and a computed ETA, auto-refreshing in place via Telegram message edits.
- 🤖 **Anti-bot scraping** — Air India sits behind Akamai; data is captured with Playwright + stealth by intercepting the underlying `status-by-fln` XHR rather than parsing brittle HTML.
- 🧠 **Reliability analytics & insights** — a 0–100 reliability score, delay percentiles (avg / max / P95), rotation-dependency analysis (does a late inbound aircraft sink the next leg?), backup-flight integrity, and improving/declining trend detection.
- ⏱️ **Adaptive polling** — poll frequency scales with how imminent a flight is (hourly when >12h out, down to every few minutes near departure, and stops once the flight has landed) to minimise load.
- 📑 **Pagination & live updates** — tracks any number of flights with 4-per-page inline-keyboard navigation, editing a single message instead of spamming the chat.
- 📤 **Data export** — one-command CSV / XLSX dumps of every captured snapshot for offline analysis.
- 🐳 **Containerised** — ships with a Dockerfile (Chromium + Playwright pre-installed) and `docker-compose` for a one-command deploy.

## Live status preview

A flight in the air, as shown by `/pollstatus`:

```
🛫 AI482  HWR → DEL

Aircraft: VT-EDE (A320neo)
Status: En Route

HWR ●──────✈──────● DEL

Progress: 52%
Distance: 142 / 275 km
Remaining: 134 km

Altitude: FL350 (35,000 ft)
Ground Speed: 523 kt (969 km/h)

ETA: 15:48 IST
Last Update: 15:36 IST
Next poll: 4m
```

The view adapts to each flight state — `Scheduled` (plane at the gate), `En Route` (live tracking), `Arrived` (✅), and `Cancelled` (❌) — and gracefully falls back to schedule-only data if FlightAware is unavailable, so the command never fails.

## Architecture

```
                  ┌──────────────────────────┐
   Telegram  ◀───▶│        FlightBot         │  commands, pagination,
                  │   (python-telegram-bot)  │  live message edits
                  └────────────┬─────────────┘
                               │
        ┌──────────────────────┼───────────────────────┐
        ▼                      ▼                        ▼
┌───────────────┐     ┌─────────────────┐      ┌────────────────────┐
│   Scheduler   │     │    Database     │      │ FlightAware client │
│ adaptive poll │────▶│  SQLite (WAL)   │◀─────│ live coords / alt  │
│ + notifications│    │ snapshots/events│      │ / speed (on demand)│
└───────┬───────┘     └────────┬────────┘      └────────────────────┘
        │                      │
        ▼                      ▼
┌───────────────┐     ┌─────────────────┐
│ Air India     │     │ Analytics &     │
│ scraper       │     │ Insights        │
│ (Playwright)  │     │ scores, trends  │
└───────────────┘     └─────────────────┘
```

**Air India scraper** → status · aircraft · schedule · delays
**FlightAware client** → coordinates · altitude · speed · timestamp
The two are merged in the presentation layer to produce the final output.

## Tech stack

| Area | Choice |
|------|--------|
| Language | Python 3.12 (`asyncio` throughout) |
| Bot framework | `python-telegram-bot` 22 |
| Scraping | Playwright + `playwright-stealth` (XHR interception) |
| Live tracking | `requests` + `BeautifulSoup` against FlightAware's track-poll endpoint |
| Storage | SQLite in WAL mode |
| Scheduling | `apscheduler` + a custom adaptive poll loop |
| Export | `openpyxl` (XLSX), stdlib `csv` |
| Deployment | Docker + docker-compose |

## Commands

| Command | Description |
|---------|-------------|
| `/track AI482` | Start tracking a flight |
| `/untrack AI482` | Stop tracking |
| `/list` | List tracked flights |
| `/status AI482` | Current status (schedule, gate, delays, aircraft) |
| `/pollstatus` | **Live status view for all flights** (progress bar, telemetry, pagination) |
| `/refresh AI482` · `/refresh_all` | Force a re-fetch |
| `/fetch AI484 20260529` | Fetch a specific date |
| `/history AI482 7` | Recent history |
| `/stats AI482` | Reliability statistics (on-time %, delay percentiles) |
| `/insights AI482` | Reliability score, rotation dependency, backup integrity, trend |
| `/export_csv` · `/export_xlsx` | Export all snapshots |

## Getting started

### Prerequisites
- Python 3.12+ (or just Docker)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

### Configuration

All configuration is via environment variables — no secrets in source.

| Variable | Purpose | Default |
|----------|---------|---------|
| `FLIGHT_TRACKER_BOT_TOKEN` | Telegram bot token **(required)** | — |
| `TRACKED_FLIGHTS` | Comma-separated flight numbers | `AI481,AI482,AI483,AI484` |
| `AUTHORIZED_CHAT_IDS` | Comma-separated chat IDs allowed to receive pushes | — |
| `POLL_INTERVAL_SECONDS` | Base scheduler tick | `5` |
| `MSG_EDIT_INTERVAL` | Live-message refresh cadence (s) | `5` |
| `FA_CF_BM`, `FA_CFLB`, `FA_OPTANON_CONSENT`, … | FlightAware session cookies for live tracking (optional) | — |

> Without the `FA_*` cookies the bot still runs — `/pollstatus` simply shows schedule data with a "Live tracking unavailable" note.

### Run with Docker (recommended)

```bash
export FLIGHT_TRACKER_BOT_TOKEN=<your-token>
docker compose up --build -d
```

### Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium
export FLIGHT_TRACKER_BOT_TOKEN=<your-token>
python main.py
```

## Project structure

```
flight-tracker/
├── main.py                 # entrypoint: wires DB, scheduler, bot together
├── bot.py                  # Telegram handlers, /pollstatus live view, pagination
├── scraper.py              # Playwright stealth scraper (Air India)
├── flightaware_client.py   # live position / altitude / speed + Haversine maths
├── scheduler.py            # adaptive polling loop + change notifications
├── database.py             # SQLite layer (snapshots, events, tracked flights)
├── models.py               # status normalisation, IST handling, data models
├── analytics.py            # reliability, delay percentiles, CSV/XLSX export
├── insights.py             # reliability score, rotation/backup/trend analysis
├── config.py               # env-driven configuration
└── Dockerfile / docker-compose.yml
```

## Engineering notes

A few decisions worth calling out:

- **Single source of truth per field.** Rather than letting two data providers fight, each owns a strict slice — the airline owns *status*, FlightAware owns *position*. This keeps the merge logic predictable and the output trustworthy.
- **Expensive lookups stay lazy.** FlightAware is only queried for flights actually in the air, and results are cached (45s TTL) so the 5-second live-refresh loop never hammers the endpoint.
- **Resilient by default.** Every external call degrades gracefully — a failed scrape, a missing cookie, or an empty track all fall back instead of crashing a command.
- **IST-correct throughout.** All times are normalised to Asia/Kolkata for display while storing UTC, avoiding the classic timezone drift bugs.

---

<sub>Built with Python and a healthy distrust of brittle HTML. Bot tokens and session cookies are configured exclusively via environment variables.</sub>
