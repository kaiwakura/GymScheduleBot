"""FastAPI web app: routes, form handling and the calendar view.

Run locally with:  uvicorn app.main:app --reload
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.ai.assistant import explain_plan, parse_notes
from app.models import BusyBlock, Commitment, Preferences, TimeOfDay, WeeklyPlan, Workout
from app.scheduler.planner import build_plan, local_now, week_start_for
from app.sources import google_calendar
from app.sources.demo import DEMO_CALENDARS, demo_busy_blocks
from app.sources.ics import InvalidCalendarFile, parse_ics, plan_to_ics

load_dotenv()
log = logging.getLogger("gymschedulebot")
BASE_DIR = Path(__file__).parent
DEFAULT_TZ = os.getenv("DEFAULT_TIMEZONE", "America/Los_Angeles")
MAX_ICS_BYTES = 2 * 1024 * 1024
VIEW_START_HOUR, VIEW_END_HOUR = 6, 22  # hours shown in the calendar grid

app = FastAPI(title="GymScheduleBot")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET") or secrets.token_hex(32),
    same_site="lax",
    https_only=os.getenv("OAUTHLIB_INSECURE_TRANSPORT") != "1",
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


# =============================================================== helpers ===
def _page(request: Request, **context) -> HTMLResponse:
    context.setdefault("form", {})
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "google_configured": google_calendar.is_configured(),
            "google_connected": "google_creds" in request.session,
            "ai_enabled": bool(os.getenv("ANTHROPIC_API_KEY")),
            "demo_calendars": {k: v[0] for k, v in DEMO_CALENDARS.items()},
            **context,
        },
    )


def _position(start: datetime, end: datetime, day: date, tz: ZoneInfo) -> dict | None:
    """CSS top/height (in %) for a block inside a day column, or None if not visible."""
    view_start = datetime.combine(day, datetime.min.time(), tzinfo=tz) + timedelta(hours=VIEW_START_HOUR)
    view_end = view_start + timedelta(hours=VIEW_END_HOUR - VIEW_START_HOUR)
    start, end = max(start.astimezone(tz), view_start), min(end.astimezone(tz), view_end)
    if start >= end:
        return None
    total = (view_end - view_start).total_seconds()
    return {
        "top": (start - view_start).total_seconds() / total * 100,
        "height": (end - start).total_seconds() / total * 100,
    }


def _fmt(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def build_week_view(plan: WeeklyPlan, busy: list[BusyBlock], tz_name: str) -> list[dict]:
    tz = ZoneInfo(tz_name)
    days = []
    for offset in range(7):
        day = plan.week_start + timedelta(days=offset)
        busy_items = []
        for b in busy:
            if pos := _position(b.start, b.end, day, tz):
                busy_items.append(
                    {**pos, "title": b.title, "time": f"{_fmt(b.start.astimezone(tz))}–{_fmt(b.end.astimezone(tz))}"}
                )
        workout_items = []
        for w in plan.workouts:
            if w.day == day and (pos := _position(w.start, w.end, day, tz)):
                workout_items.append({**pos, "name": w.name, "time": f"{_fmt(w.start)}–{_fmt(w.end)}"})
        days.append(
            {
                "label": day.strftime("%a"),
                "date": day.strftime("%b %d").replace(" 0", " "),
                "free": day in plan.free_days,
                "busy": busy_items,
                "workouts": workout_items,
            }
        )
    return days


def plan_to_json(plan: WeeklyPlan) -> str:
    return json.dumps(
        {
            "week_start": plan.week_start.isoformat(),
            "split_name": plan.split_name,
            "workouts": [
                {"name": w.name, "start": w.start.isoformat(), "end": w.end.isoformat(), "exercises": list(w.exercises)}
                for w in plan.workouts
            ],
        }
    )


def plan_from_json(raw: str) -> WeeklyPlan:
    data = json.loads(raw)
    workouts = [
        Workout(
            w["name"],
            frozenset(),
            tuple(w["exercises"]),
            datetime.fromisoformat(w["start"]),
            datetime.fromisoformat(w["end"]),
        )  # fmt: skip
        for w in data["workouts"]
    ]
    return WeeklyPlan(date.fromisoformat(data["week_start"]), data["split_name"], workouts, [], len(workouts))


# ================================================================ routes ===
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    flash = request.session.pop("flash", None)
    return _page(request, flash=flash)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/plan", response_class=HTMLResponse)
async def make_plan(
    request: Request,
    source: str = Form("demo:busy"),
    week: str = Form("next"),
    commitment: Commitment = Form(Commitment.MODERATE),
    preferred_time: TimeOfDay = Form(TimeOfDay.ANY),
    session_minutes: int = Form(60),
    buffer_minutes: int = Form(15),
    notes: str = Form(""),
    timezone: str = Form(DEFAULT_TZ),
    ics_file: UploadFile | None = File(None),
):
    form = {
        "source": source, "week": week, "commitment": commitment.value,
        "preferred_time": preferred_time.value, "session_minutes": session_minutes,
        "buffer_minutes": buffer_minutes, "notes": notes,
    }  # fmt: skip
    try:
        tz = ZoneInfo(timezone)
    except Exception:
        timezone, tz = DEFAULT_TZ, ZoneInfo(DEFAULT_TZ)

    prefs = Preferences(
        timezone=timezone,
        commitment=commitment,
        preferred_time=preferred_time,
        session_minutes=max(20, min(180, session_minutes)),
        buffer_minutes=max(0, min(60, buffer_minutes)),
    )
    parsed = parse_notes(notes[:1000])
    prefs = parsed.apply(prefs)

    now = local_now(prefs)
    week_start = week_start_for(now.date()) + timedelta(days=7 if week == "next" else 0)
    window_start = datetime.combine(week_start, datetime.min.time(), tzinfo=tz)
    window_end = window_start + timedelta(days=7)

    # ---- load busy time from the chosen calendar source
    try:
        if source.startswith("demo:") and source[5:] in DEMO_CALENDARS:
            busy = demo_busy_blocks(source[5:], week_start, timezone)
        elif source == "ics":
            if ics_file is None or not ics_file.filename:
                return _page(request, form=form, error="Choose an .ics file to upload.")
            data = await ics_file.read(MAX_ICS_BYTES + 1)
            if len(data) > MAX_ICS_BYTES:
                return _page(request, form=form, error="That file is too large (2 MB max).")
            busy = parse_ics(data, window_start, window_end, timezone)
        elif source == "google":
            creds = request.session.get("google_creds")
            if not creds:
                return _page(request, form=form, error="Connect your Google Calendar first.")
            service = google_calendar.build_service(creds)
            busy = google_calendar.fetch_busy(service, window_start, window_end, timezone)
        else:
            return _page(request, form=form, error="Unknown calendar source.")
    except InvalidCalendarFile as exc:
        return _page(request, form=form, error=str(exc))
    except Exception:
        log.exception("Failed to load calendar")
        return _page(request, form=form, error="Couldn't read your calendar. Try reconnecting or another source.")

    busy += parsed.busy_blocks(week_start, timezone)
    plan = build_plan(busy, week_start, prefs, now=now if week == "this" else None)
    plan.explanation = explain_plan(plan)

    return _page(
        request,
        form=form,
        plan=plan,
        parsed=parsed,
        week_view=build_week_view(plan, busy, timezone),
        plan_json=plan_to_json(plan),
        hours=list(range(VIEW_START_HOUR, VIEW_END_HOUR)),
    )


@app.post("/export.ics")
def export_ics(plan_json: str = Form(...)):
    plan = plan_from_json(plan_json)
    return Response(
        plan_to_ics(plan),
        media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="gymschedulebot-workouts.ics"'},
    )


# ------------------------------------------------------- Google Calendar ---
@app.get("/auth/google/login")
def google_login(request: Request):
    if not google_calendar.is_configured():
        request.session["flash"] = "Google Calendar isn't set up on this server yet. See the README."
        return RedirectResponse("/", status_code=303)
    url, state, verifier = google_calendar.start_login()
    request.session["oauth_state"] = state
    request.session["oauth_verifier"] = verifier
    return RedirectResponse(url)


@app.get("/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    expected = request.session.pop("oauth_state", None)
    verifier = request.session.pop("oauth_verifier", None)
    if error or not code or state != expected:
        request.session["flash"] = "Google sign-in was cancelled or failed. Please try again."
        return RedirectResponse("/", status_code=303)
    try:
        request.session["google_creds"] = google_calendar.finish_login(code, state, verifier)
        request.session["flash"] = "Google Calendar connected."
    except Exception:
        log.exception("OAuth token exchange failed")
        request.session["flash"] = "Couldn't finish connecting to Google. Please try again."
    return RedirectResponse("/", status_code=303)


@app.post("/auth/google/logout")
def google_logout(request: Request):
    request.session.pop("google_creds", None)
    request.session["flash"] = "Disconnected from Google Calendar."
    return RedirectResponse("/", status_code=303)


@app.post("/google/add")
def google_add(request: Request, plan_json: str = Form(...), timezone: str = Form(DEFAULT_TZ)):
    creds = request.session.get("google_creds")
    if not creds:
        request.session["flash"] = "Connect your Google Calendar first."
        return RedirectResponse("/", status_code=303)
    try:
        plan = plan_from_json(plan_json)
        count = google_calendar.add_workouts(google_calendar.build_service(creds), plan, timezone)
        request.session["flash"] = f"Added {count} workout(s) to your Google Calendar."
    except Exception:
        log.exception("Adding events failed")
        request.session["flash"] = "Couldn't add the workouts to Google Calendar."
    return RedirectResponse("/", status_code=303)
