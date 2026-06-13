# Flight Monitor Bot — Design Document (v2)

## 1. Overview

This system is a 24/7 flight monitoring bot designed to track Air India flights and build a historical reliability database.

Initial focus:

* AI481 (DEL → HWR)
* AI482 (HWR → DEL)
* AI483 (DEL → HWR)
* AI484 (HWR → DEL)

The system polls Air India's flight status system using a Playwright-based scraper and stores operational data in SQLite.

Primary goals:

* Track cancellations
* Track delays
* Track aircraft assignments
* Build route reliability statistics
* Validate AI482 ↔ AI484 backup strategy
* Send Telegram notifications on meaningful changes

---

# 2. Data Source

## IMPORTANT

Flight data collection is already implemented.

The AI should NOT redesign the scraping layer.

The scraper implementation exists in:

```text
scraper.py
```

The AI should treat `scraper.py` as the source of truth for obtaining flight status data.

The AI should read and understand:

```text
scraper.py
```

before making changes to any scraping-related functionality.

---

## Example API Response

Example response payload:

```text
airindia_flight_status.json
```

This file contains a representative response returned by Air India's backend.

The AI should use this file when designing:

* models
* parsers
* database schema
* state normalization logic

Do not hardcode assumptions outside the response format demonstrated in:

```text
airindia_flight_status.json
```

---

# 3. System Architecture

```text
                    ┌──────────────┐
                    │ Telegram Bot │
                    └──────┬───────┘
                           │
                           ▼
                   ┌──────────────┐
                   │ Core Service │
                   └──────┬───────┘
                          │
        ┌─────────────────┴─────────────────┐
        ▼                                   ▼
 Scheduler                         Notification Engine
        │
        ▼
 Playwright Scraper
 (scraper.py)
        │
        ▼
 SQLite Database
```

---

# 4. Project Structure

Suggested layout:

```text
project/
│
├── scraper.py
├── bot.py
├── scheduler.py
├── database.py
├── models.py
├── analytics.py
├── config.py
│
├── airindia_flight_status.json
│
├── exports/
│   ├── csv/
│   └── xlsx/
│
├── data/
│   └── flights.db
│
└── logs/
```

---

# 5. Core Components

## 5.1 Scheduler

Responsible for periodic polling.

Recommended library:

```text
APScheduler
```

Polling intervals:

| Flight State              | Poll Interval                     |
| ------------------------- | --------------------------------- |
| Future flight (>12h away) | 60 min                            |
| Future flight (<12h away) | 30 min                            |
| Day of operation          | 10 min                            |
| Airborne                  | 10 min                            |
| Arrived                   | Stop polling for that flight/date |

---

## 5.2 Scraper Layer

Implemented in:

```text
scraper.py
```

Responsibilities:

* Launch Playwright
* Obtain flight data
* Return parsed JSON
* Handle retries
* Handle browser failures

The rest of the system should treat scraper output as trusted input.

Expected return type:

```python
dict
```

matching the structure in:

```text
airindia_flight_status.json
```

---

## 5.3 Flight State Normalizer

Convert Air India flight states into internal statuses.

### Raw Values

Examples:

```text
flightState
------------
ARRIVED
DEPARTED
CANCELLED
```

```text
flightStatus
------------
EARLY
ON_TIME
DELAYED
```

---

### Internal Status Enum

```python
class FlightStatus:
    SCHEDULED = "scheduled"
    DELAYED = "delayed"
    DEPARTED = "departed"
    AIRBORNE = "airborne"
    ARRIVED = "arrived"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"
    SCRAPE_FAILED = "scrape_failed"
```

---

## 5.4 State Change Engine

Only generate notifications when state changes.

Rule:

```python
if current_status != previous_status:
    create_event()
    notify()
```

---

# 6. Database Design

SQLite database:

```text
data/flights.db
```

---

## tracked_flights

Contains user-selected flights.

```sql
CREATE TABLE tracked_flights (
    flight_number TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL,
    added_at TEXT NOT NULL
);
```

---

## flight_snapshots

Stores every successful poll.

```sql
CREATE TABLE flight_snapshots (
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

    collected_at TEXT NOT NULL
);
```

---

## flight_events

Stores status transitions.

```sql
CREATE TABLE flight_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    flight_number TEXT NOT NULL,
    flight_date TEXT NOT NULL,

    old_status TEXT,
    new_status TEXT,

    event_time TEXT NOT NULL
);
```

---

# 7. Telegram Commands

## Flight Tracking

```text
/track AI481
/track AI482
/track AI483
/track AI484
```

Adds flight to tracking list.

---

```text
/untrack AI482
```

Stops tracking.

---

```text
/list
```

Lists tracked flights.

---

## Flight Status

```text
/status AI482
```

Returns:

* Current state
* Delay information
* Aircraft type
* Tail number
* Last update time

---

## History

```text
/history AI482 7
```

Returns last 7 operational records.

---

## Statistics

```text
/stats AI482
```

Returns:

* Total observed days
* Operated days
* Cancelled days
* Average delay
* On-time percentage

---

## Export

```text
/export_csv
```

Exports snapshots.

---

```text
/export_xlsx
```

Exports spreadsheet.

---

# 8. Notification Rules

Only notify on meaningful changes.

Examples:

```text
Scheduled -> Delayed
```

```text
Scheduled -> Departed
```

```text
Departed -> Arrived
```

```text
Scheduled -> Cancelled
```

---

## Example Notification

```text
🚨 AI482 CANCELLED

Date: 2026-06-18

Backup AI484: AVAILABLE
```

---

## Example Delay Notification

```text
⚠️ AI484 DELAYED

Original Arrival: 15:55 IST
Updated Arrival: 17:20 IST
```

---

# 9. Backup Flight Logic

Primary concern:

```text
AI482
```

Backup:

```text
AI484
```

Logic:

```text
If AI482 is cancelled:

    Check AI484

    If AI484 operational:
        Notify "Backup Available"

    If AI484 cancelled:
        Notify Critical Alert
```

Critical alert:

```text
🚨 CRITICAL

AI482 CANCELLED
AI484 CANCELLED

No HWR → DEL flights available today.
```

---

# 10. Analytics

Important metrics:

## Route Reliability

```text
Observed Days
Operated Days
Cancelled Days
```

---

## Delay Metrics

```text
Average Delay
Maximum Delay
95th Percentile Delay
```

---

## Backup Reliability

```text
Days AI482 Cancelled
Days AI484 Cancelled
Days Both Cancelled
```

This metric is the primary objective of the project.

---

# 11. Failure Handling

## Scraper Failure

Do NOT interpret scraper failures as cancellations.

Use:

```python
SCRAPE_FAILED
```

and retry later.

---

## Browser Failure

* Log error
* Retry
* Preserve previous state

---

## Invalid JSON

* Save raw response
* Log parsing error
* Do not overwrite previous good data

---

# 12. Deployment

Environment:

```text
Ubuntu Server
Python 3.11+
Playwright
SQLite
Telegram Bot
```

Runs continuously on a homelab server.

Recommended:

```text
systemd service
```

or

```text
Docker container
```

---

# 13. Primary Success Criteria

By July 2026, the system should be able to answer:

1. How reliable is AI482?
2. How reliable is AI484?
3. How often are both unavailable?
4. Is HWR → DEL a safe feeder flight for AC42?
5. What is the average operational delay on the route?

The most important metric is:

```text
Days where BOTH AI482 and AI484 were unavailable.
```

Everything else is secondary.
