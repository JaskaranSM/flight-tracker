import os
import requests
from bs4 import BeautifulSoup
import re
import json

from dataclasses import dataclass
from typing import Optional
from math import radians, sin, cos, sqrt, atan2


# Airport coordinates in [longitude, latitude] order, matching FlightAware.
# Used as the primary source for route endpoints; FlightAware's activityLog
# coordinates are the fallback for airports not listed here.
AIRPORT_COORDS = {
    "DEL": (77.1031, 28.5665),   # Delhi - Indira Gandhi Intl
    "HWR": (75.7561, 30.7484),   # Ludhiana - Halwara Intl
}


def distance_meters(coord1, coord2):
    """
    coord format: [longitude, latitude]
    returns distance in meters
    """

    lon1, lat1 = coord1
    lon2, lat2 = coord2

    R = 6371000  # Earth radius in meters

    lat1 = radians(lat1)
    lon1 = radians(lon1)
    lat2 = radians(lat2)
    lon2 = radians(lon2)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        sin(dlat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    )

    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return R * c

# FlightAware session cookies. These are redacted — populate from your own
# browser session (e.g. via environment variables) before use.
cookies = {
    '_fa-recovery-reshow-freq': os.environ.get('FA_RECOVERY_RESHOW_FREQ', ''),
    '__Secure-fa-web-redirect': os.environ.get('FA_WEB_REDIRECT', ''),
    '__cf_bm': os.environ.get('FA_CF_BM', ''),
    '__cflb': os.environ.get('FA_CFLB', ''),
    'OptanonConsent': os.environ.get('FA_OPTANON_CONSENT', ''),
}

headers = {
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'accept-language': 'en-CA,en-GB;q=0.9,en-US;q=0.8,en;q=0.7',
    'priority': 'u=0, i',
    'referer': 'https://www.flightaware.com/live/flight/AIC481',
    'sec-ch-ua': '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Linux"',
    'sec-fetch-dest': 'document',
    'sec-fetch-mode': 'navigate',
    'sec-fetch-site': 'same-origin',
    'sec-fetch-user': '?1',
    'upgrade-insecure-requests': '1',
    'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
}

def extract_token(flight_code):
    response = requests.get(f'https://www.flightaware.com/live/flight/{flight_code}', cookies=cookies, headers=headers)
    soup = BeautifulSoup(response.text, "html.parser")
    script_tags = soup.find_all('script')
    for tag in script_tags:
        if "trackpollGlobals" in tag.text:
            match = re.search('"TOKEN":"(.*?)"', tag.text)
            token = match.group(1)
            return token
        
def poll_status(token):
    headers = {
        'sec-ch-ua-platform': '"Linux"',
        'Referer': 'https://www.flightaware.com/live/flight/AIC102',
        'X-Requested-With': 'XMLHttpRequest',
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'sec-ch-ua': '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
    }
    params = {
        'token': token,
        'locale': 'en_CA',
        'summary': '1',
    }
    response = requests.get('https://www.flightaware.com/ajax/trackpoll.rvt', params=params, headers=headers)
    return response.json()

def extract_latest_track_coords(flight_status_json):
    for k in flight_status_json["flights"]:
        track = flight_status_json["flights"][k].get("track") or []
        if track:
            return track[-1]
    return None

def extract_origin_coords(flight_status_json):
     for k in flight_status_json["flights"]:
        for flight in flight_status_json["flights"][k].get("activityLog", {}).get("flights", []):
            return flight["origin"]["coord"]
     return None

def extract_destination_coords(flight_status_json):
     for k in flight_status_json["flights"]:
        for flight in flight_status_json["flights"][k].get("activityLog", {}).get("flights", []):
            return flight["destination"]["coord"]
     return None


def distance_km(coord1, coord2):
    """coord format: [longitude, latitude]; returns distance in kilometres."""
    return distance_meters(coord1, coord2) / 1000


def to_flightaware_ident(flight_number):
    """Map an Air India IATA flight number to its FlightAware ICAO ident.

    e.g. "AI481" -> "AIC481". Non-AI / already-ICAO idents pass through.
    """
    fn = (flight_number or "").upper().strip()
    m = re.fullmatch(r"AI(\d+)", fn)
    if m:
        return f"AIC{m.group(1)}"
    return fn


@dataclass
class LiveTracking:
    lon: float
    lat: float
    altitude_fl: int          # flight level, e.g. 350 == FL350 == 35,000 ft
    groundspeed_kt: int       # knots
    timestamp: int            # epoch seconds of the latest track point
    origin_coord: Optional[tuple] = None        # [lon, lat] from FlightAware
    dest_coord: Optional[tuple] = None          # [lon, lat] from FlightAware

    @property
    def coord(self):
        return [self.lon, self.lat]


def fetch_live_tracking(flight_number) -> Optional[LiveTracking]:
    """Fetch the latest live aircraft position for a flight from FlightAware.

    Blocking (uses `requests`); callers in async code should wrap this in
    `asyncio.to_thread`. Returns None on any failure (token miss, network
    error, empty track) so callers can fall back gracefully.
    """
    try:
        ident = to_flightaware_ident(flight_number)
        token = extract_token(ident)
        if not token:
            return None
        status = poll_status(token)
        latest = extract_latest_track_coords(status)
        if not latest or not latest.get("coord"):
            return None
        lon, lat = latest["coord"]
        return LiveTracking(
            lon=lon,
            lat=lat,
            altitude_fl=latest.get("alt") or 0,
            groundspeed_kt=latest.get("gs") or 0,
            timestamp=latest.get("timestamp") or 0,
            origin_coord=extract_origin_coords(status),
            dest_coord=extract_destination_coords(status),
        )
    except Exception:
        return None


if __name__ == "__main__":
    token = extract_token("AIC102")
    status = poll_status(token)
    latest_coord = extract_latest_track_coords(status)

    origin = extract_origin_coords(status)
    dest = extract_destination_coords(status)

    total_distance = distance_meters(origin, dest)
    print(f"{total_distance:.0f} meters")
    print(f"{total_distance/1000:.1f} km")

    distance_remaining = distance_meters(dest, latest_coord["coord"])

    print(f"{distance_remaining:.0f} meters")
    print(f"{distance_remaining/1000:.1f} km")
