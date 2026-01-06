import os, io, csv, textwrap, requests
import json
import re
from datetime import date, datetime, timedelta
from typing import Optional, Dict, Any

import pytz
import requests
from flask import Flask, request, Response, jsonify
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import csv, io  # add with your other imports


# ──────────────────────────────────────────────────────────────────────────────
# Configuration & Environment
# ──────────────────────────────────────────────────────────────────────────────

# Twilio (must be set in Render → Environment for both Web and Cron services)
TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
# e.g. whatsapp:+16107728845  (← set this to your new business sender)
TWILIO_WHATSAPP_FROM = os.environ["TWILIO_WHATSAPP_FROM"]

# Template SIDs (Twilio Content API HX… values) — set these in env.
TWILIO_TEMPLATE_SID_WEEKLY_BASIC   = os.environ.get("TWILIO_TEMPLATE_SID_REMINDER_BASIC", "")
TWILIO_TEMPLATE_SID_WEEKLY_HOLIDAY = os.environ.get("TWILIO_TEMPLATE_SID_REMINDER_HOLIDAY", "")
TWILIO_TEMPLATE_SID_WELCOME        = os.environ.get("TWILIO_TEMPLATE_SID_WELCOME", "")

SHEET_CSV_URL   = os.getenv("SHEET_CSV_URL", "").strip()
SHEET_COL_ADDR  = os.getenv("SHEET_COL_ADDRESS", "Street Address")
SHEET_COL_PHONE = os.getenv("SHEET_COL_PHONE", "Phone Number")
SHEET_COL_CONS  = os.getenv("SHEET_COL_CONSENT", "Consent to Receive Messages")
CONSENT_OK = [s.strip().lower() for s in os.getenv("SHEET_CONSENT_OK", "agree,yes,true,1").split(",")]

# Lower Merion endpoints
TOKEN_URL  = "https://www.lowermerion.org/Home/GetToken"
SEARCH_URL = "https://flex.visioninternet.com/api/FeFlexComponent/Get"
COMPONENT_GUID     = "f05e2a62-e807-4f30-b450-c2c48770ba5c"
LIST_UNIQUE_NAME   = "VHWQOE27X21B7R8"

ZONE_URLS: Dict[str, str] = {
    "Zone 1": "https://www.lowermerion.org/departments/public-works-department/refuse-and-recycling/holiday-collection/holiday-collection-zone-one",
    "Zone 2": "https://www.lowermerion.org/departments/public-works-department/refuse-and-recycling/holiday-collection/holiday-collection-zone-two",
    "Zone 3": "https://www.lowermerion.org/departments/public-works-department/refuse-and-recycling/holiday-collection/holiday-collection-zone-three",
    "Zone 4": "https://www.lowermerion.org/departments/public-works-department/refuse-and-recycling/holiday-collection/holiday-collection-zone-four",
}

# Storage
USERS_FILE = "users.json"

# Public opt-in form (optional, shown in /whatsapp_webhook help text)
FORM_URL = os.environ.get("PUBLIC_SIGNUP_URL", "https://forms.gle/your-google-form-id")

# 2026 recycling: seed the FIRST "Paper" Monday (adjust to match LM 2026 PDF).
# The LM schedule is alternate-week Paper/Commingled; holiday shifts handled separately.
# Use the first Monday that is labeled "Week Beginning" under Paper in the 2026 PDF you linked.
# The Monday for Jan 5, 2026 is a reasonable default; update once you confirm the PDF’s first Paper Monday.
RECYCLE_2026_FIRST_PAPER_ISO = os.environ.get("RECYCLE_2026_FIRST_PAPER_ISO", "2026-01-05")

# ──────────────────────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────────────────────

def get_auth_token() -> str:
    """Fetch the JWT used by LM’s component API. Handles JSON and quoted-string responses."""
    r = requests.get(TOKEN_URL, timeout=10)
    r.raise_for_status()
    try:
        data = r.json()
        tok = data.get("access_token") or data.get("token")
        if tok:
            return tok
    except ValueError:
        pass
    return r.text.strip().strip('"').strip()

def normalize_whatsapp_number(raw: Optional[str], default_cc: str = "+1") -> str:
    """Return 'whatsapp:+1xxxxxxxxxx' from assorted inputs."""
    if not raw:
        return ""
    s = re.sub(r"[^\d+]", "", raw)
    if not s:
        return ""
    if not s.startswith("+"):
        s = default_cc + s
    return f"whatsapp:{s}"

UNIT_TOKENS = r"(?:apt|apartment|unit|ste|suite|#|fl|floor|bldg|building)"
def street_number_and_name(addr: Optional[str]) -> str:
    """Extract just 'number + street name' from a full address."""
    if not addr:
        return ""
    a = addr.strip()
    if re.search(r"\bP\.?\s*O\.?\s*Box\b", a, flags=re.I):
        return a.split(",")[0].strip()
    first = a.split(",")[0]
    first = re.sub(rf"\b{UNIT_TOKENS}\b.*$", "", first, flags=re.I).strip()
    return re.sub(r"\s{2,}", " ", first)

def build_alternating_schedule(start_monday: date, weeks: int = 53) -> Dict[date, str]:
    """Alternate Paper/Commingled by week, starting with Paper on start_monday."""
    sched: Dict[date, str] = {}
    for i in range(weeks):
        d = start_monday + timedelta(weeks=i)
        sched[d] = "Paper" if i % 2 == 0 else "Commingled"
    return sched

def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())

# ──────────────────────────────────────────────────────────────────────────────
# Lower Merion lookups
# ──────────────────────────────────────────────────────────────────────────────

def lookup_zone_by_address(address: str) -> Optional[str]:
    """Return 'Zone 1'..'Zone 4' for a given street address via LM component API."""
    if not address:
        return None
    try:
        token = get_auth_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://www.lowermerion.org",
        }
        payload = {
            "pageSize": 20,
            "pageNumber": 1,
            "sortOptions": [],
            "searchText": address,
            "searchFields": ["Address"],
            "searchOperator": "OR",
            "searchSeparator": ",",
            "filterOptions": [],
            "Data": {"componentGuid": COMPONENT_GUID, "listUniqueName": LIST_UNIQUE_NAME},
        }
        resp = requests.post(SEARCH_URL, headers=..., json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("items") or data.get("Items") or data.get("Data") or []
        if isinstance(rows, dict):
            rows = rows.get("Items", [])
        for row in rows:
            # Try direct field names first
            for k in ("Refuse & Recycling Holiday Zone", "Holiday Zone", "Zone", "RefuseZone"):
                v = row.get(k)
                if isinstance(v, str) and "zone" in v.lower():
                    return v.strip().title()
            # Otherwise scan any string field
            for v in row.values():
                if isinstance(v, str) and "zone" in v.lower():
                    return v.strip().title()
    except Exception as e:
        print("lookup_zone_by_address error:", e)
    return None

def get_next_holiday_shift(zone: Optional[str], ref_date: Optional[date] = None) -> Optional[str]:
    """
    Return a short note like 'Labor Day: collection on Wednesday.' for the ISO week containing ref_date.
    Looks up the 'Holiday Collection – Zone X' page, reads the table (Date | Holiday | New Collection Day).
    Falls back to regex if bs4 isn't available.
    """
    if not zone or zone not in ZONE_URLS:
        return None

    if ref_date is None:
        ref_date = datetime.now(pytz.timezone("US/Eastern")).date()

    try:
        html = requests.get(ZONE_URLS[zone], timeout=15).text
    except Exception as e:
        print("holiday fetch error:", e)
        return None

    week_mon = ref_date - timedelta(days=ref_date.weekday())
    week_sun = week_mon + timedelta(days=6)

    # --- helper to parse date strings we might see on the page
    def parse_dt(s: str) -> Optional[date]:
        s = s.strip()
        for fmt in ("%A, %B %d, %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                pass
        return None

    # --- Preferred: parse the table with BeautifulSoup
    try:
        from bs4 import BeautifulSoup  # type: ignore
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")
        if table:
            for tr in table.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) < 2:
                    continue
                # Try to locate a date in the first 1–2 cells
                dt = parse_dt(cells[0]) or (parse_dt(cells[1]) if len(cells) > 1 else None)
                if not dt:
                    continue
                if not (week_mon <= dt <= week_sun):
                    continue

                # Guess the holiday name & target weekday from remaining cells
                holiday_name = ""
                if len(cells) >= 2:
                    # Prefer the cell that's not the date cell
                    holiday_name = cells[1] if parse_dt(cells[0]) else cells[0]

                mday = re.search(r"(Monday|Tuesday|Wednesday|Thursday|Friday)", " ".join(cells[1:]), re.I)
                if mday:
                    new_day = mday.group(1).title()
                    name = holiday_name or "Holiday"
                    return f"{name}: collection on {new_day}."
                else:
                    name = holiday_name or dt.strftime("%b %d")
                    return f"{name}: collection may shift."
    except Exception as e:
        print("bs4 holiday parse error:", e)

    # --- Fallback: regex over the raw HTML if a table parse wasn't possible
    yr = ref_date.year
    # capture 'Holiday Name' near the date, and a weekday somewhere after
    pat = rf"(?P<name>[A-Za-z&\-\s]{{3,}})?[^<]{{0,120}}(?P<date>(?:Monday,\s*)?[A-Za-z]+\s+\d{{1,2}},\s*{yr}).{{0,220}}?(?P<weekday>Monday|Tuesday|Wednesday|Thursday|Friday)"
    for m in re.finditer(pat, html, flags=re.I | re.S):
        ds = m.group("date")
        dt = parse_dt(ds) or parse_dt(re.sub(r"^Monday,\s*", "", ds))
        if not dt:
            continue
        if not (week_mon <= dt <= week_sun):
            continue
        name = (m.group("name") or "").strip()
        name = re.sub(r"\s+", " ", name)
        # Clean obvious boilerplate fragments that aren't holiday names
        if not name or len(name) < 3 or re.search(r"(date|holiday|collection|week|zone)", name, re.I):
            name = "Holiday"
        new_day = m.group("weekday").title()
        return f"{name}: collection on {new_day}."

    return None


# ──────────────────────────────────────────────────────────────────────────────
# Recycling schedule (2025 fallback + 2026 generated by seed)
# ──────────────────────────────────────────────────────────────────────────────

# If you still want the published 2025 map, keep it here (truncated example):
# RECYCLING_SCHEDULE_2025 = {date(2025,1,6):"Paper", date(2025,1,13):"Commingled", ...}

def parse_iso(d: str) -> date:
    y, m, d_ = d.split("-")
    return date(int(y), int(m), int(d_))

# Seed 2026 alternating schedule from first Paper-Monday
try:
    FIRST_PAPER_2026 = parse_iso(RECYCLE_2026_FIRST_PAPER_ISO)
except Exception:
    FIRST_PAPER_2026 = date(2026, 1, 5)  # safe default; adjust if LM PDF differs

RECYCLING_SCHEDULE: Dict[date, str] = {
    # **Optionally** include 2025 fallback here if you want two-year horizon.
    # **Else** rely on 2026 generator + future-year extension via the same pattern.
    **build_alternating_schedule(FIRST_PAPER_2026, weeks=53),
}

def get_recycling_type_for_date(d: date) -> str:
    """Return 'Paper' or 'Commingled' for the Monday of the week containing date d."""
    monday = monday_of(d)
    return RECYCLING_SCHEDULE.get(monday, "Paper")  # default to Paper if missing

# ──────────────────────────────────────────────────────────────────────────────
# Messaging helpers
# ──────────────────────────────────────────────────────────────────────────────

_twilio_client = None
def twilio_client() -> Client:
    global _twilio_client
    if _twilio_client is None:
        _twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    return _twilio_client

def send_whatsapp_template(to: str, template_sid: str, variables: Optional[Dict[str, Any]] = None):
    """Send a WhatsApp *template* via Twilio Content API."""
    if not template_sid:
        raise RuntimeError("Template SID not configured in environment.")
    payload = {
        "from_": TWILIO_WHATSAPP_FROM,
        "to": to,
        "content_sid": template_sid,
        "content_variables": json.dumps(variables or {}),
    }
    return twilio_client().messages.create(**payload)

# ──────────────────────────────────────────────────────────────────────────────
# Persistence
# ──────────────────────────────────────────────────────────────────────────────

def load_users() -> list[dict]:
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r") as f:
            try:
                return json.load(f)
            except Exception:
                return []
    return []

def save_users(users: list[dict]) -> None:
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)

USERS: list[dict] = load_users()

# ──────────────────────────────────────────────────────────────────────────────
# CSV loader and 'where to get subscribers' helper
# ──────────────────────────────────────────────────────────────────────────────
def load_users_from_sheet(csv_url: str) -> list[dict]:
    if not csv_url:
        return []
    resp = requests.get(csv_url, timeout=15)
    resp.raise_for_status()
    rdr = csv.DictReader(io.StringIO(resp.text))
    users = []
    for row in rdr:
        addr = (row.get(SHEET_COL_ADDR, "") or "").strip()
        phone = (row.get(SHEET_COL_PHONE, "") or "").strip()
        consent = (row.get(SHEET_COL_CONS, "") or "").strip().lower()
        if not addr or not phone:
            continue
        # accept blank consent or any value containing an allowed token
        if consent and not any(tok in consent for tok in CONSENT_OK):
            continue
        users.append({
            "phone": normalize_whatsapp_number(phone),
            "street_address": addr,
            "street_label": street_number_and_name(addr),
        })
    return users

def current_subscribers() -> list[dict]:
    """Return current subscribers from the Sheet if configured; else fall back to in-memory USERS."""
    if SHEET_CSV_URL:
        try:
            subs = load_users_from_sheet(SHEET_CSV_URL)
            if subs:
                return subs
        except Exception as e:
            print(f"⚠️ Failed to load SHEET_CSV_URL: {e}")
    # fallback: in-memory (from / webhook)
    return USERS or []


# ──────────────────────────────────────────────────────────────────────────────
# Flask app & routes
# ──────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)

@app.route("/", methods=["POST"])
def webhook():
    """
    Google Form -> Apps Script POSTs JSON here:
      { street_address, phone_number, consent }
    We upsert the subscriber into USERS and (only if new) send the WELCOME template.
    """
    try:
        data = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400

    raw_phone = (data.get("phone_number") or data.get("phone") or "").strip()
    address   = (data.get("street_address") or data.get("address") or "").strip()
    consent   = (data.get("consent") or "").strip().lower()

    if not raw_phone or not address:
        return jsonify({"status": "error", "message": "Missing phone or address"}), 400
    if "agree" not in consent:
        return jsonify({"status": "error", "message": "Consent not granted"}), 400

    phone = normalize_whatsapp_number(raw_phone)          # -> 'whatsapp:+1…'
    if not phone:
        return jsonify({"status": "error", "message": "Invalid phone"}), 400

    street_label = street_number_and_name(address)

    # Upsert (avoid duplicates)
    existing = next(
        (u for u in USERS if u.get("phone") == phone or u.get("phone_number") == phone),
        None
    )
    is_new = existing is None
    if existing:
        existing["phone"] = phone
        existing["street_address"] = address
        existing["street_label"] = street_label
    else:
        USERS.append({"phone": phone, "street_address": address, "street_label": street_label})

    try:
        save_users(USERS)
    except Exception as e:
        print("Error saving USERS:", e)

    # Send WELCOME only on first subscribe
    if is_new and TWILIO_TEMPLATE_SID_WELCOME:
        try:
            msg = send_whatsapp_template(
                to=phone,
                template_sid=TWILIO_TEMPLATE_SID_WELCOME,
                variables={}
            )
            print(f"📩 welcome sid={msg.sid}")
        except Exception as e:
            print("⚠️ Welcome template send failed:", e)

    return jsonify({"status": "ok"})

@app.route("/whatsapp_webhook", methods=["POST"])
def whatsapp_webhook():
    """Inbound WhatsApp messages (session replies)."""
    form = dict(request.form)
    print("Twilio incoming webhook:", form)
    from_number = (form.get("From") or "").strip()  # e.g., 'whatsapp:+1...'
    body = (form.get("Body") or "").strip()
    resp = MessagingResponse()

    if not from_number:
        resp.message("Missing sender.")
        return Response(str(resp), mimetype="application/xml")

    lower = body.lower()

    # Unsubscribe
    if lower in {"stop", "stop all", "cancel", "unsubscribe", "quit", "end"}:
        removed = False
        for i in range(len(USERS) - 1, -1, -1):
            p = USERS[i].get("phone") or USERS[i].get("phone_number")
            if p and p.lower() == from_number.lower():
                USERS.pop(i); removed = True
        if removed:
            try: save_users(USERS)
            except Exception as e: print("save_users STOP error:", e)
            resp.message("You are unsubscribed from trash & recycling reminders.")
        else:
            resp.message("You are not currently subscribed.")
        return Response(str(resp), mimetype="application/xml")

    # Quick “recycling” Q&A (works for anyone)
    if "recycl" in lower:
        tz = pytz.timezone("US/Eastern")
        today = datetime.now(tz).date()
        rtype = get_recycling_type_for_date(today)
        resp.message(f"This week is {rtype} recycling.\nReply STOP to unsubscribe.")
        return Response(str(resp), mimetype="application/xml")

    # “trash / pickup” for subscribed users
    if "trash" in lower or "pickup" in lower:
        user = next((u for u in USERS
                     if (u.get("phone") or u.get("phone_number","")).lower() == from_number.lower()), None)
        if user:
            tz = pytz.timezone("US/Eastern")
            tomorrow = datetime.now(tz).date() + timedelta(days=1)
            street_lbl = user.get("street_label") or street_number_and_name(user.get("street_address", ""))
            rtype = get_recycling_type_for_date(tomorrow)
            zone  = lookup_zone_by_address(user.get("street_address", ""))
            note  = get_next_holiday_shift(zone, ref_date=tomorrow) if zone else None
            msg = f"Reminder: Tomorrow is trash day for {street_lbl}.\nRecycling: {rtype}."
            if note: msg += f"\n{note}"
            resp.message(msg)
        else:
            form_url = os.environ.get("PUBLIC_SIGNUP_URL", "")
            resp.message(f"I don’t have you on file. Use the sign-up form: {form_url}\nOr reply “JOIN 123 Main St, Town, ZIP”.")
        return Response(str(resp), mimetype="application/xml")

    # Help text
    form_url = os.environ.get("PUBLIC_SIGNUP_URL", "")
    resp.message(
        "Hi! I can send trash & recycling timing reminders.\n"
        "• Text your address to subscribe (or use the form).\n"
        "• Ask: “What’s recycling this week?”\n"
        "• Reply STOP to unsubscribe.\n"
        f"{form_url}"
    )
    return Response(str(resp), mimetype="application/xml")

from flask import jsonify

@app.route("/run_reminders_now")
@app.route("/run_reminders_now", methods=["GET"])
def run_reminders_now():
    """Manually trigger the reminder job and always return JSON (never 500 on None)."""
    try:
        results = send_weekly_reminders()
    except Exception as e:
        # If the worker itself blew up, return a JSON error instead of a 500 stacktrace
        print("send_weekly_reminders raised:", e)
        return jsonify({"count": 0, "results": [], "error": str(e)}), 500

    if not isinstance(results, list):
        # If the worker returned None or something unexpected, coerce to [] so len() is safe
        print(f"send_weekly_reminders returned {type(results)}; coercing to []")
        results = []

    return jsonify({"count": len(results), "results": results})

# ──────────────────────────────────────────────────────────────────────────────
# Reminder engine (called by cron; not scheduled in-process here)
# ──────────────────────────────────────────────────────────────────────────────

def send_weekly_reminders() -> list[dict]:
    """
    Build and send reminders for all known subscribers.
    Returns a list of per-recipient result dicts; never returns None.
    """
    results: list[dict] = []

    # 1) Load subscribers from your sheet helper (or fall back to USERS)
    try:
        subs = current_subscribers()  # must return a list of dicts with at least phone/address
    except Exception as e:
        print("current_subscribers() error:", e)
        return results  # safe: empty list means nothing to send

    if not subs:
        print("No subscribers found by current_subscribers()")
        return results

    tz = pytz.timezone("US/Eastern")
    tomorrow = (datetime.now(tz).date() + timedelta(days=1))

    # 2) Iterate and send
    for u in subs:
        # Normalise fields defensively
        phone_raw = (u.get("phone") or u.get("phone_number") or "").strip()
        phone = normalize_whatsapp_number(phone_raw) if phone_raw else ""
        addr_full = (u.get("street_address") or u.get("address") or "").strip()
        street_label = (u.get("street_label") or street_number_and_name(addr_full)).strip()

        if not phone:
            results.append({"phone": phone_raw, "status": "skipped", "error": "invalid_or_missing_phone"})
            continue
        if not addr_full:
            results.append({"phone": phone, "status": "skipped", "error": "missing_address"})
            continue

        # Lookup for this recipient
        try:
            zone = lookup_zone_by_address(addr_full)
        except Exception as e:
            print(f"lookup_zone_by_address error for {addr_full}: {e}")
            zone = None

        try:
            recycling_type = get_reliable_recycling_type_for_date(tomorrow)  # or get_recycling_type_for_date
        except Exception as e:
            print("get_recycling_type_for_date error:", e)
            recycling_type = "Recycling"

        try:
            holiday_note = get_next_hotly_shift(zone, tomorrow) if zone else None  # or get_next_holiday_shift
        except Exception as e:
            print("get_next_holiday_shift error:", e)
            holiday_note = None

        # Choose template IDs from env
        template_basic   = os.environ.get("TWILIO_TEMPLATE_SID_REMINDER_BASIC", "")
        template_holiday = os.environ.get("TWILIO_TEMPLATE_SID_REMINDER_HOLIDAY", "")

        template_sid = template_holiday if holiday_note and template_holiday else template_basic
        if not template_sid:
            results.append({"phone": phone, "status": "skipped", "error": "missing_template_sid"})
            continue

        # Map variables for your approved templates:
        # BASIC:   {{1}} = street_label, {{2}} = recycling_type
        # HOLIDAY: {{1}} = street_label, {{2}} = recycling_type, {{3}} = holiday_note
        vars_map = {"1": street_label, "2": recycling_type}
        if holiday_note:
            vars_map["3"] = str(holiday_note)

        # Send and record outcome
        try:
            msg = send_whatsapp_template(
                to=phone,
                template_sid=template_sid,
                variables=vars_map
            )
            results.append({
                "phone": phone,
                "template": template_sid,
                "status": "queued",
                "sid": getattr(msg, "sid", None),
            })
        except Exception as e:
            print(f"send_whatsapp_template error for {phone}: {e}")
            results.append({
                "phone": phone,
                "template": template_sid,
                "status": "error",
                "error": str(e),
            })

    return results
###########temporary, delete from final version:
@app.route("/_debug_worker_type")
def _debug_worker_type():
    r = send_weekly_reminders()
    return {"type": str(type(r)), "is_list": isinstance(r, list), "len_safe": 0 if not isinstance(r, list) else len(r)}

@app.route("/subs_debug")
def subs_debug():
    info = {"source": "memory", "env": {}, "count": 0, "sample": []}
    try:
        info["env"] = {
            "SHEET_CSV_URL": bool(os.getenv("SHEET_CSV_URL")),
            "SHEET_COL_ADDRESS": os.getenv("SHEET_COL_ADDRESS"),
            "SHEET_COL_PHONE": os.getenv("SHEET_COL_PHONE"),
            "SHEET_COL_CONSENT": os.getenv("SHEET_COL_CONSENT"),
            "SHEET_CONSENT_OK": os.getenv("SHEET_CONSENT_OK", "agree,yes,true,1"),
        }
        subs = current_subscribers()
        info["source"] = "sheet" if os.getenv("SHEET_CSV_URL") else "memory"
        info["count"] = len(subs)
        info["sample"] = subs[:3]
    except Exception as e:
        info["error"] = str(e)
    return info

import os, io, csv, textwrap, requests

@app.route("/csv_debug")
def csv_debug():
    url = os.getenv("SHEET_CSV_URL")
    out = {"has_url": bool(url), "status": None, "headers": [], "first_rows": []}
    if not url:
        return out
    try:
        r = requests.get(url, timeout=10)
        out["status"] = r.status_code
        txt = r.text
        # show first 5 lines to inspect headers
        lines = txt.splitlines()
        out["first_rows"] = lines[:5]
        # parse headers as DictReader sees them
        rdr = csv.reader(io.StringIO(txt))
        out["headers"] = next(rdr, [])
    except Exception as e:
        out["error"] = str(e)
    return out

@app.route("/run_reminders_for_date")
def run_reminders_for_date():
    """
    Test the reminder pipeline as if tomorrow were a specific date.
    Call: /run_reminders_for_date?iso=2026-09-07  (YYYY-MM-DD)
    Returns JSON with queued Twilio SIDs.
    """
    iso = request.args.get("iso", "").strip()
    if not iso:
        return jsonify({"error": "Provide ?iso=YYYY-MM-DD"}), 400
    try:
        fake = datetime.strptime(iso, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD."}), 400

    results = []
    try:
        subs = current_subscribers()
        tz_fake = pytz.timezone("US/Eastern")
        seen = set()
        for u in subs:
            phone = normalize_whatsapp_number(u.get("phone") or u.get("phone_number", ""))
            if not phone or phone in seen:
                continue
            seen.add(phone)

            addr = u.get("street_address", "") or ""
            label = u.get("street_label") or street_number_and_name(addr)

            # in run_reminders_for_date, after you compute label/addr
            zone_param = request.args.get("zone", "").strip().title()  # e.g., "Zone 3"
            zone = zone_param if zone_param in {"Zone 1","Zone 2","Zone 3","Zone 4"} else None

            # only call lookup if you didn't get a zone param
            if not zone and addr:
                zone = lookup_zone_by_address(addr)
            holiday_note = get_next_holiday_shift(zone, ref_date=fake) if zone else None


            # choose HOLIDAY if note exists, otherwise BASIC
            template_sid = (os.environ.get("TWILIO_TEMPLATE_SID_REMINDER_HOLIDAY")
                            if holiday_note else os.environ.get("TWILIO_TEMPLATE_SID_REMINDER_BASIC"))
            if not template_sid:
                results.append({"phone": phone, "status": "skipped", "error": "missing_template_sid"})
                continue

            # 2-var BASIC ({{1}}=street, {{2}}=recycling) + HOLIDAY adds {{3}}=holiday note
            rtype = get_recycling_type_for_date(fake)
            vars_map = {"1": label, "2": rtype}
            if holiday_note:
                vars_map["3"] = holiday_note

            try:
                msg = send_whatsapp_template(to=phone, template_sid=template_sid, variables=vars_map)
                results.append({"phone": phone, "sid": getattr(msg, "sid", None),
                                "template": template_sid, "vars": vars_map, "status": "queued"})
            except Exception as e:
                results.append({"phone": phone, "template": template_sid, "vars": vars_map,
                                "status": "failed", "error": str(e)})
    except Exception as e:
        return jsonify({"error": str(e), "results": results}), 500

    return jsonify({"count": len(results), "results": results})

@app.route("/env_check")
def env_check():
    return jsonify({
        "sender": os.environ.get("TWILIO_WHATSAPP_FROM"),
        "sid_basic_set": bool(os.environ.get("TWILIO_TEMPLATE_SID_REMINDER_BASIC")),
        "sid_holiday_set": bool(os.environ.get("TWILIO_TEMPLATE_SID_REMINDER_HOLIDAY")),
        "welcome_set": bool(os.environ.get("TWILIO_TEMPLATE_SID_WELCOME")),
        "sheet_csv_url_set": bool(os.environ.get("SHEET_CSV_URL")),
        "sheet_cols": {
            "address": os.environ.get("SHEET_COL_ADDRESS"),
            "phone":   os.environ.get("SHEET_COL_PHONE"),
            "consent": os.environ.get("SHEET_COL_CONSENT"),
            "consent_ok": os.environ.get("SHEET_CONSENT_OK", "agree,yes,true,1"),
        },
        "account_sid_prefix": (os.environ.get("TWILIO_ACCOUNT_SID") or "")[:10] + "…"
    })

@app.route("/holiday_debug")
def holiday_debug():
    iso  = request.args.get("iso", "").strip()
    zone = (request.args.get("zone", "") or "").title()  # e.g., Zone 3
    if not iso or not zone:
        return jsonify({"error": "Use ?iso=YYYY-MM-DD&zone=Zone%203"}), 400
    try:
        fake = datetime.strptime(iso, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Bad iso date"}), 400
    note = get_next_holiday_shift(zone, ref_date=fake)
    return jsonify({"iso": iso, "zone": zone, "holiday_note": note})

######

# Note: no if __name__ == '__main__' run-loop here; the Web service should not
# run a scheduler. Your Render Cron should import this module and call
# send_weekly_reminders() at 8:00 PM ET (via a small `cron.py`).
