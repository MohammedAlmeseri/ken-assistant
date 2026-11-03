"""Google Calendar MCP server (stdio), our own so the safety rules stay in our hands.

    python -m assistant.calendar_mcp --auth   # one time: opens the browser to allow access
    python -m assistant.calendar_mcp          # what mcp_servers.json runs

Tools: list_events and find_free_time (read), create_event. The agent's allowlist hides any
"create" tool from the model, so only the app calls create_event, after the user clicks
"Add to calendar" on an event card. The OAuth client is the one the Gmail server already uses.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

# ----------------------------------------------------------------------------
# OAuth scopes and token files
#   Read events and free/busy, create events. Reuses the Gmail server's Google OAuth client.
# ----------------------------------------------------------------------------

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly",  # calendar list + free/busy
          "https://www.googleapis.com/auth/calendar.events"]  # read events, add new ones
KEYS = Path.home() / ".gmail-mcp" / "gcp-oauth.keys.json"
TOKEN = Path.home() / ".calendar-mcp" / "token.json"
NOT_CONNECTED = ("Google Calendar is not connected yet. Open Settings and click "
                 "'Connect Google Calendar' (or run: python -m assistant.calendar_mcp --auth).")


# ----------------------------------------------------------------------------
# Parsing dates and times
#   'today', 'friday', ISO dates; naive times are local.
# ----------------------------------------------------------------------------

def local_tz():
    return datetime.now().astimezone().tzinfo


def parse_day(value: str, today: date | None = None) -> date:
    """'today', 'tomorrow', a weekday name ('friday' = the next one) or an ISO date."""
    today = today or datetime.now().date()
    v = (value or "today").strip().lower()
    if v in ("", "today"):
        return today
    if v == "tomorrow":
        return today + timedelta(days=1)
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    if v in days:
        ahead = (days.index(v) - today.weekday()) % 7
        return today + timedelta(days=ahead or 7)
    return date.fromisoformat(v[:10])


def parse_when(value: str) -> datetime:
    """ISO date-time in local time ('2026-10-30T14:00'); an explicit offset is kept."""
    t = datetime.fromisoformat(value.strip().replace(" ", "T", 1))
    return t if t.tzinfo else t.replace(tzinfo=local_tz())


# ----------------------------------------------------------------------------
# Formatting events for the model
#   One short line per event, so a whole week fits in a few hundred tokens.
# ----------------------------------------------------------------------------

def _event_time(raw: dict) -> tuple[datetime | None, bool]:
    if "dateTime" in raw:
        return datetime.fromisoformat(raw["dateTime"].replace("Z", "+00:00")).astimezone(local_tz()), False
    if "date" in raw:
        return datetime.combine(date.fromisoformat(raw["date"]), time(0), local_tz()), True
    return None, False


def format_events(events: list[dict]) -> str:
    """One line per event, oldest first: 'Thu 30 Oct 14:00-15:00 · Title · @ place · (calendar)'."""
    lines = []
    for e in events:
        start, all_day = _event_time(e.get("start", {}))
        end, _ = _event_time(e.get("end", {}))
        if start is None:
            continue
        when = f"{start:%a %d %b}" + (" (all day)" if all_day else f" {start:%H:%M}" + (f"-{end:%H:%M}" if end else ""))
        parts = [when, e.get("summary") or "(no title)"]
        if e.get("location"):
            parts.append("@ " + e["location"])
        if e.get("_calendar"):
            parts.append(f"({e['_calendar']})")
        if e.get("_response") in ("needsAction", "tentative"):
            parts.append("[you haven't accepted]" if e["_response"] == "needsAction" else "[maybe]")
        lines.append(" · ".join(parts))
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# Free time
#   Gaps of at least N minutes between busy intervals (any order, may overlap).
# ----------------------------------------------------------------------------

def free_slots(busy: list[tuple[datetime, datetime]], start: datetime, end: datetime,
               minutes: int) -> list[tuple[datetime, datetime]]:
    """Gaps of at least `minutes` between start and end, given busy intervals (any order, may overlap)."""
    slots, cursor = [], start
    for b_start, b_end in sorted(busy):
        if b_end <= cursor:
            continue
        if b_start - cursor >= timedelta(minutes=minutes):
            slots.append((cursor, min(b_start, end)))
        cursor = max(cursor, b_end)
        if cursor >= end:
            break
    if end - cursor >= timedelta(minutes=minutes):
        slots.append((cursor, end))
    return [(a, b) for a, b in slots if b - a >= timedelta(minutes=minutes)]


# ----------------------------------------------------------------------------
# Google Calendar API wrapper
#   All calendars the user selected; declined events dropped; unanswered invites marked.
# ----------------------------------------------------------------------------

class Calendar:
    def __init__(self, token: Path = TOKEN):
        self.token = token
        self._service = None

    def service(self):
        if self._service is None:
            if not self.token.exists():
                raise RuntimeError(NOT_CONNECTED)
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds = Credentials.from_authorized_user_file(str(self.token), SCOPES)
            if not creds.valid:
                creds.refresh(Request())
                self.token.write_text(creds.to_json(), encoding="utf-8")
            self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def events(self, start: datetime, end: datetime, query: str = "") -> list[dict]:
        svc = self.service()
        out = []
        for cal in svc.calendarList().list().execute().get("items", []):
            if not cal.get("selected") and not cal.get("primary"):
                continue  # calendars hidden in Google Calendar stay hidden here
            kwargs = {"calendarId": cal["id"], "timeMin": start.isoformat(), "timeMax": end.isoformat(),
                      "singleEvents": True, "orderBy": "startTime", "maxResults": 50}
            if query:
                kwargs["q"] = query
            for e in svc.events().list(**kwargs).execute().get("items", []):
                if e.get("status") == "cancelled":
                    continue
                me = next((a for a in e.get("attendees", []) if a.get("self")), None)
                if me and me.get("responseStatus") == "declined":
                    continue
                e["_calendar"] = "" if cal.get("primary") else cal.get("summary", "")
                e["_response"] = me.get("responseStatus") if me else ""
                out.append(e)
        return sorted(out, key=lambda e: _event_time(e.get("start", {}))[0] or start)

    def busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        body = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "items": [{"id": "primary"}]}
        cals = self.service().freebusy().query(body=body).execute().get("calendars", {})
        return [(parse_when(b["start"].replace("Z", "+00:00")), parse_when(b["end"].replace("Z", "+00:00")))
                for b in cals.get("primary", {}).get("busy", [])]

    def create(self, summary: str, start: datetime, end: datetime, location: str = "", description: str = "") -> dict:
        body = {"summary": summary, "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()}}
        if location:
            body["location"] = location
        if description:
            body["description"] = description
        # sendUpdates="none": never emails anyone; we add no attendees anyway.
        return self.service().events().insert(calendarId="primary", body=body, sendUpdates="none").execute()


# ----------------------------------------------------------------------------
# The MCP server
#   Tools: list_events, find_free_time, create_event. Ken hides create_event from the model;
#   only the event card's Add button calls it.
# ----------------------------------------------------------------------------

def build_server(calendar: Calendar | None = None):
    from mcp.server.mcpserver import MCPServer

    cal = calendar or Calendar()
    server = MCPServer("calendar")

    @server.tool()
    def list_events(start: str = "today", days: int = 1, query: str = "") -> str:
        """List calendar events. start: 'today', 'tomorrow', a weekday or YYYY-MM-DD; days: how many days."""
        try:
            first = datetime.combine(parse_day(start), time(0), local_tz())
            events = cal.events(first, first + timedelta(days=max(1, min(int(days), 31))), query)
        except Exception as exc:
            return f"Error: {exc}"
        return format_events(events) or "No events."

    @server.tool()
    def find_free_time(day: str = "today", minutes: int = 60, from_time: str = "09:00", to_time: str = "22:00") -> str:
        """Free slots of at least `minutes` on a day ('today', 'tomorrow', weekday or YYYY-MM-DD)."""
        try:
            d = parse_day(day)
            start = datetime.combine(d, time.fromisoformat(from_time), local_tz())
            end = datetime.combine(d, time.fromisoformat(to_time), local_tz())
            now = datetime.now(local_tz())
            if start < now < end:  # today: only from now on, rounded up to the next quarter hour
                start = (now + timedelta(minutes=15 - now.minute % 15)).replace(second=0, microsecond=0)
            slots = free_slots(cal.busy(start, end), start, end, int(minutes))
        except Exception as exc:
            return f"Error: {exc}"
        if not slots:
            return f"No free {minutes}-minute slot on {d:%a %d %b} between {from_time} and {to_time}."
        return f"Free on {d:%a %d %b}: " + ", ".join(f"{a:%H:%M}-{b:%H:%M}" for a, b in slots)

    @server.tool()
    def create_event(summary: str, start: str, end: str, location: str = "", description: str = "") -> str:
        """Add an event to the primary calendar. start/end: ISO local date-time."""
        try:
            s, e = parse_when(start), parse_when(end)
            if e <= s:
                return "Error: the end must be after the start."
            event = cal.create(summary, s, e, location, description)
        except Exception as exc:
            return f"Error: {exc}"
        return f"Added: {format_events([event])}"

    return server


# ----------------------------------------------------------------------------
# One-time consent (--auth)
#   Opens Google's consent page and saves the token. Run from Settings → Accounts.
# ----------------------------------------------------------------------------

def authorize() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not KEYS.exists():
        sys.exit(f"OAuth client file not found: {KEYS} (the Gmail setup puts it there).")
    flow = InstalledAppFlow.from_client_secrets_file(str(KEYS), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    TOKEN.parent.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    print(f"Google Calendar connected. Token saved to {TOKEN}")


if __name__ == "__main__":
    if "--auth" in sys.argv:
        authorize()
    else:
        build_server().run("stdio")
