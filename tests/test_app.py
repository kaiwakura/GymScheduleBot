from fastapi.testclient import TestClient
from icalendar import Calendar

from app.main import app
from tests.test_sources import SAMPLE_ICS

client = TestClient(app)


def test_home_page_loads():
    r = client.get("/")
    assert r.status_code == 200
    assert "GymScheduleBot" in r.text


def test_demo_plan_renders_calendar_and_sessions():
    r = client.post("/plan", data={"source": "demo:busy", "week": "next", "commitment": "moderate"})
    assert r.status_code == 200
    assert "Push/Pull/Legs" in r.text
    assert "Download .ics" in r.text


def test_notes_change_the_plan():
    r = client.post(
        "/plan",
        data={
            "source": "demo:light",
            "week": "next",
            "commitment": "dedicated",
            "notes": "no gym on sundays, saturdays off",
        },
    )
    assert "no Sun" in r.text and "no Sat" in r.text  # parsed-note chips
    assert "PPL + Upper/Lower" in r.text  # 7 open days -> 5 once the weekend is blocked


def test_ics_upload():
    r = client.post(
        "/plan",
        data={"source": "ics", "week": "next"},
        files={"ics_file": ("cal.ics", SAMPLE_ICS, "text/calendar")},
    )
    assert r.status_code == 200
    assert "Week of" in r.text


def test_bad_ics_shows_friendly_error():
    r = client.post("/plan", data={"source": "ics"}, files={"ics_file": ("x.ics", b"nope", "text/calendar")})
    assert "valid .ics" in r.text


def test_google_requires_connection():
    r = client.post("/plan", data={"source": "google"})
    assert "Connect your Google Calendar first" in r.text


def test_export_ics_download():
    page = client.post("/plan", data={"source": "demo:light", "week": "next"}).text
    import html
    import re

    plan_json = html.unescape(re.search(r'name="plan_json" value="([^"]+)"', page).group(1))
    r = client.post("/export.ics", data={"plan_json": plan_json})
    assert r.headers["content-type"].startswith("text/calendar")
    assert len(list(Calendar.from_ical(r.content).walk("VEVENT"))) >= 1


def test_home_page_defaults_are_filled_and_css_is_relative():
    """Regression: the empty form must submit cleanly, and CSS must load behind proxies (e.g. Codespaces)."""
    page = client.get("/").text
    assert 'name="buffer_minutes" min="0" max="60" step="5" value="15"' in page
    assert 'href="/static/style.css"' in page
