"""Google Calendar: OAuth sign-in, reading busy times, and adding workouts.

Privacy by design: we only ask for *free/busy* information when reading, never
event titles or details. Writing uses the events scope so we can add workouts.

The functions that talk to the API take a ``service`` object, so tests can pass
a fake one instead of hitting Google.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from app.models import BusyBlock, WeeklyPlan

SCOPES = [
    "https://www.googleapis.com/auth/calendar.freebusy",
    "https://www.googleapis.com/auth/calendar.events",
]
APP_TAG = "gymschedulebot"  # stored on every event we create, so we can find them later

# Google may return scopes in a different order/format than requested; don't treat that as an error.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


def is_configured() -> bool:
    return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))


def _flow(state: str | None = None) -> Flow:
    client_config = {
        "web": {
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")
    return Flow.from_client_config(client_config, scopes=SCOPES, state=state, redirect_uri=redirect_uri)


def start_login() -> tuple[str, str, str | None]:
    """Returns (url to send the user to, state, PKCE code verifier)."""
    flow = _flow()
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    return url, state, flow.code_verifier


def finish_login(code: str, state: str, code_verifier: str | None) -> dict[str, Any]:
    """Exchange the code Google sent back for tokens. Returns JSON-safe credential info."""
    flow = _flow(state)
    flow.code_verifier = code_verifier
    flow.fetch_token(code=code)
    creds = flow.credentials
    # Only the user's own tokens go into their session cookie. The app's client
    # secret stays on the server: the cookie is signed, not encrypted, so the
    # user could read anything stored in it.
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
    }


def build_service(creds_info: dict[str, Any]):
    creds = Credentials(
        token=creds_info.get("token"),
        refresh_token=creds_info.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=SCOPES,
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def fetch_busy(service, start: datetime, end: datetime, timezone: str) -> list[BusyBlock]:
    """Busy blocks on the user's primary calendar between ``start`` and ``end``."""
    body = {
        "timeMin": start.isoformat(),
        "timeMax": end.isoformat(),
        "timeZone": timezone,
        "items": [{"id": "primary"}],
    }
    response = service.freebusy().query(body=body).execute()
    periods = response.get("calendars", {}).get("primary", {}).get("busy", [])
    return [BusyBlock(datetime.fromisoformat(p["start"]), datetime.fromisoformat(p["end"])) for p in periods]


def workout_event(workout, split_name: str, timezone: str) -> dict[str, Any]:
    return {
        "summary": f"Workout: {workout.name}",
        "description": f"{split_name}\n\n" + "\n".join(workout.exercises) + "\n\nPlanned by GymScheduleBot",
        "start": {"dateTime": workout.start.isoformat(), "timeZone": timezone},
        "end": {"dateTime": workout.end.isoformat(), "timeZone": timezone},
        "colorId": "10",  # green
        "extendedProperties": {"private": {"app": APP_TAG}},
    }


def add_workouts(service, plan: WeeklyPlan, timezone: str) -> int:
    """Create one calendar event per workout. Returns how many were created."""
    for w in plan.workouts:
        service.events().insert(calendarId="primary", body=workout_event(w, plan.split_name, timezone)).execute()
    return len(plan.workouts)
