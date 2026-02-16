import os
import io
import csv
import textwrap
import json
import re
from datetime import date, datetime, timedelta
from typing import Optional, Dict, Any
from functools import lru_cache

import pytz
import requests
from flask import Flask, request, Response, jsonify
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse


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
SHEET_COL_ZONE  = os.getenv("SHEET_COL_ZONE", "Zone")  # optional column in your Sheet
SHEET_COL_DAY   = os.getenv("SHEET_COL_DAY", "Collection Day")  # optional column
SHEET_COL_CONS  = os.getenv("SHEET_COL_CONSENT", "Consent to Receive Messages")
CONSENT_OK = [s.strip().lower() for s in os.getenv("SHEET_CONSENT_OK", "agree,yes,true,1").split(",")]

WEEKDAY_RX = re.compile(r"\b(Monday|Tuesday|Wednesday|Thursday|Friday)\b", re.I)
HOLIDAY_RULES_JSON     = os.getenv("HOLIDAY_RULES_JSON", "").strip()
HOLIDAY_OVERRIDES_JSON = os.getenv("HOLIDAY_OVERRIDES_JSON", "").strip()
try:
    HOLIDAY_RULES = json.loads(HOLIDAY_RULES_JSON) if HOLIDAY_RULES_JSON else {}
except Exception:
    HOLIDAY_RULES = {}
try:
    HOLIDAY_OVERRIDES = json.loads(HOLIDAY_OVERRIDES_JSON) if HOLIDAY_OVERRIDES_JSON else {}
except Exception:
    HOLIDAY_OVERRIDES = {}


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

# Load address lookup data from CSV (generated from township Excel file)
_ADDRESS_LOOKUP_CACHE: Optional[Dict[str, Dict[str, str]]] = None

def _load_address_lookup() -> Dict[str, Dict[str, str]]:
    """Load address lookup data from CSV file into memory."""
    global _ADDRESS_LOOKUP_CACHE
    if _ADDRESS_LOOKUP_CACHE is not None:
        return _ADDRESS_LOOKUP_CACHE

    lookup = {}
    csv_path = os.path.join(os.path.dirname(__file__), "address_lookup.csv")

    if not os.path.exists(csv_path):
        print(f"Warning: {csv_path} not found. Address lookup will not work.")
        _ADDRESS_LOOKUP_CACHE = {}
        return _ADDRESS_LOOKUP_CACHE

    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                addr = row['address'].strip().upper()
                lookup[addr] = {
                    'zone': row['zone'],
                    'collection_day': row['collection_day']
                }
        print(f"Loaded {len(lookup)} addresses from lookup file")
    except Exception as e:
        print(f"Error loading address lookup: {e}")
        lookup = {}

    _ADDRESS_LOOKUP_CACHE = lookup
    return _ADDRESS_LOOKUP_CACHE

def lookup_zone_by_address(address: str) -> Optional[Dict[str, str]]:
    """
    Return {'zone': 'Zone 1', 'collection_day': 'Monday'} for a given address.
    Uses local address_lookup.csv file generated from township data.
    Returns None if lookup fails.
    """
    if not address:
        return None

    # Load lookup data
    lookup = _load_address_lookup()
    if not lookup:
        return None

    # Normalize address for lookup
    addr_normalized = street_number_and_name(address).strip().upper()

    # Try exact match first
    if addr_normalized in lookup:
        return lookup[addr_normalized]

    # Try fuzzy match - look for addresses that contain the street number + name
    addr_parts = addr_normalized.split()
    if len(addr_parts) >= 2:
        street_num = addr_parts[0]
        for lookup_addr, data in lookup.items():
            if lookup_addr.startswith(street_num + " "):
                # Check if street name matches (at least first word)
                lookup_parts = lookup_addr.split()
                if len(lookup_parts) >= 2 and len(addr_parts) >= 2:
                    if lookup_parts[1].startswith(addr_parts[1][:3]):  # First 3 chars of street name
                        return data

    return None

def _parse_date(txt: str, year: int) -> date | None:
    """Accept 'Monday, December 25, 2025' or 'December 25, 2025'."""
    txt = " ".join(txt.split())
    for fmt in ("%A, %B %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            pass
    # remove leading weekday and try again
    m = re.sub(r"^[A-Za-z]+,\s*", "", txt)
    try:
        return datetime.strptime(m, "%B %d, %Y").date()
    except ValueError:
        return None

@lru_cache(maxsize=16)
def _scrape_zone_index(zone: str, year: int) -> list[dict]:
    """
    Load holiday collection schedule using rule-based system.
    Returns: [{'date': date, 'name': 'Christmas Day', 'weekday': 'Friday'}, ...]

    Uses the official Lower Merion Township holiday shift chart:
    - If holiday falls on Monday/Tuesday/etc., collection shifts per zone rules
    - Rules are defined in holiday_rules.py based on township chart
    """
    try:
        from holiday_rules import get_all_holidays_for_year, get_shifted_collection_day
    except ImportError:
        print("Warning: holiday_rules.py not found, returning empty holiday list")
        return []

    # Get the official holidays for this year
    holidays = get_all_holidays_for_year(year)
    if not holidays:
        print(f"Warning: No holiday data for year {year}")
        return []

    entries: list[dict] = []

    for holiday in holidays:
        holiday_date = holiday["date"]
        holiday_name = holiday["name"]

        # Use the chart rules to determine shifted collection day
        shifted_day = get_shifted_collection_day(holiday_date, zone)

        entries.append({
            "date": holiday_date,
            "name": holiday_name,
            "weekday": shifted_day
        })

    return entries

def get_next_holiday_shift(zone: str | None, ref_date: date | None = None) -> str | None:
    """
    Return 'Christmas Day: collection on Friday.' for the ISO week that contains ref_date.
    Scrapes the public Zone page. No tokens. No address lookup.
    """
    if not zone or zone not in ZONE_URLS:
        return None
    if ref_date is None:
        ref_date = datetime.now(pytz.timezone("US/Eastern")).date()

    wk_mon = ref_date - timedelta(days=ref_date.weekday())
    wk_sun = wk_mon + timedelta(days=6)

    # Build or fetch the index for this zone/year
    idx = _scrape_zone_index(zone, ref_date.year)
    # Find any entry whose date falls in the same ISO week
    for r in idx:
        if wk_mon <= r["date"] <= wk_sun:
            holiday_name = r['name']
            holiday_date = r['date']
            shifted_day = r["weekday"]

            if shifted_day:
                # Holiday causes a shift - show new day
                weekday_name = holiday_date.strftime("%A")
                return f"{holiday_name} on {weekday_name}. Pickup shifted to {shifted_day} this week."
            else:
                # Holiday doesn't affect pickup (falls on weekend)
                return f"{holiday_name} this week. Your regular pickup schedule is unchanged."

    # No holiday this week
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
        addr    = (row.get(SHEET_COL_ADDR, "")  or "").strip()
        phone   = (row.get(SHEET_COL_PHONE, "") or "").strip()
        consent = (row.get(SHEET_COL_CONS, "")  or "").strip().lower()
        zone_in = (row.get(SHEET_COL_ZONE, "")  or "").strip().title()  # "Zone 3" or ""
        day_in  = (row.get(SHEET_COL_DAY, "")   or "").strip().title()  # "Monday" or ""

        if not addr or not phone:
            continue
        # accept blank consent or any value containing an allowed token
        if consent and not any(tok in consent for tok in CONSENT_OK):
            continue

        # normalize "Zone X" and collection day
        zone = zone_in if zone_in in {"Zone 1","Zone 2","Zone 3","Zone 4"} else None
        collection_day = day_in if day_in in {"Monday","Tuesday","Wednesday","Thursday","Friday"} else None

        user_dict = {
            "phone": normalize_whatsapp_number(phone),
            "street_address": addr,
            "street_label": street_number_and_name(addr),
        }
        if zone:
            user_dict["zone"] = zone
        if collection_day:
            user_dict["collection_day"] = collection_day

        users.append(user_dict)
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
# holiday helpers 
# ──────────────────────────────────────────────────────────────────────────────
def holiday_note_from_overrides(zone: str | None, d: date) -> Optional[str]:
    if not zone:
        return None
    return (HOLIDAY_OVERRIDES.get(d.isoformat(), {}) or {}).get(zone)

def us_holiday_in_week(d: date) -> Optional[str]:
    wk_mon = d - timedelta(days=d.weekday())
    wk_sun = wk_mon + timedelta(days=6)
    y = wk_mon.year
    def in_week(m, dd):
        t = date(y, m, dd)
        return wk_mon <= t <= wk_sun
    # fixed
    if in_week(1,1):   return "New Year's Day"
    if in_week(6,19):  return "Juneteenth"
    if in_week(7,4):   return "Independence Day"
    if in_week(11,11): return "Veterans Day"
    if in_week(12,25): return "Christmas Day"
    # floating
    import calendar
    def nth_weekday(month, weekday, n):
        days = [dt for dt in calendar.Calendar().itermonthdates(y, month) if dt.month==month and dt.weekday()==weekday]
        return days[n-1]
    def last_weekday(month, weekday):
        days = [dt for dt in calendar.Calendar().itermonthdates(y, month) if dt.month==month and dt.weekday()==weekday]
        return days[-1]
    if wk_mon <= nth_weekday(1,0,3)  <= wk_sun: return "Martin Luther King Jr. Day"
    if wk_mon <= nth_weekday(2,0,3)  <= wk_sun: return "Presidents Day"
    if wk_mon <= last_weekday(5,0)   <= wk_sun: return "Memorial Day"
    if wk_mon <= nth_weekday(9,0,1)  <= wk_sun: return "Labor Day"
    if wk_mon <= nth_weekday(10,0,2) <= wk_sun: return "Columbus/Indigenous Peoples Day"
    # Thanksgiving: 4th Thu of Nov
    if wk_mon <= nth_weekday(11,3,4) <= wk_sun: return "Thanksgiving Day"
    return None

def holiday_note_from_rules(zone: str | None, d: date) -> Optional[str]:
    if not zone:
        return None
    name = us_holiday_in_week(d)
    if not name:
        return None
    wd = (HOLIDAY_RULES.get(zone, {}) or {}).get(name)  # e.g., "Friday"
    return f"{name}: collection on {wd}." if wd else f"{name}: collection may shift."


# ──────────────────────────────────────────────────────────────────────────────
# Flask app & routes
# ──────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)

@app.route("/", methods=["GET", "HEAD", "POST"])
def webhook():
    """
    Health check (GET/HEAD) or Google Form webhook (POST).
    POST: { street_address, phone_number, consent, zone?, collection_day? }
    Upsert subscriber; save zone/collection_day if provided; lookup if missing; send WELCOME only on first subscribe.
    """
    # Health check for GET/HEAD requests
    if request.method in ["GET", "HEAD"]:
        return jsonify({"status": "ok", "service": "trash-reminder-bot"}), 200

    # Webhook handler for POST requests
    try:
        data = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400

    # 1) pull fields
    raw_phone = (data.get("phone_number") or data.get("phone") or "").strip()
    address   = (data.get("street_address") or data.get("address") or "").strip()
    consent   = (data.get("consent") or "").strip().lower()
    zone_in   = (data.get("zone") or "").title()  # e.g., "Zone 3" (may be "")
    day_in    = (data.get("collection_day") or "").title()  # e.g., "Monday" (may be "")

    if not raw_phone or not address:
        return jsonify({"status": "error", "message": "Missing phone or address"}), 400
    if "agree" not in consent:
        return jsonify({"status": "error", "message": "Consent not granted"}), 400

    # 2) normalize + derive
    phone        = normalize_whatsapp_number(raw_phone)       # -> 'whatsapp:+1…'
    if not phone:
        return jsonify({"status": "error", "message": "Invalid phone"}), 400
    street_label = street_number_and_name(address)

    # 3) normalize/validate zone and collection_day
    zone = zone_in if zone_in in {"Zone 1","Zone 2","Zone 3","Zone 4"} else None
    collection_day = day_in if day_in in {"Monday","Tuesday","Wednesday","Thursday","Friday"} else None

    # 3b) If zone or collection_day missing, try lookup
    if not zone or not collection_day:
        try:
            lookup_result = lookup_zone_by_address(address)
            if lookup_result:
                if not zone:
                    zone = lookup_result.get("zone")
                if not collection_day:
                    collection_day = lookup_result.get("collection_day")
        except Exception as e:
            print(f"⚠️ Zone lookup failed for {address}: {e}")

    # 4) upsert by phone (avoid duplicates)
    existing = next((u for u in USERS
                     if (u.get("phone") == phone or u.get("phone_number") == phone)), None)
    is_new = existing is None

    if existing:
        existing["phone"] = phone
        existing["street_address"] = address
        existing["street_label"] = street_label
        if zone:
            existing["zone"] = zone
        if collection_day:
            existing["collection_day"] = collection_day
    else:
        rec = {"phone": phone, "street_address": address, "street_label": street_label}
        if zone:
            rec["zone"] = zone
        if collection_day:
            rec["collection_day"] = collection_day
        USERS.append(rec)

    try:
        save_users(USERS)
    except Exception as e:
        print("Error saving USERS:", e)

    # 5) send WELCOME only once (first subscribe)
    if is_new and TWILIO_TEMPLATE_SID_WELCOME:
        try:
            msg = send_whatsapp_template(
                to=phone,
                template_sid=TWILIO_TEMPLATE_SID_WELCOME,
                variables={"1": street_label}
            )
            print(f"📩 welcome sid={msg.sid} to {phone} with address={street_label}")
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
            tomorrow   = datetime.now(tz).date() + timedelta(days=1)
            street_lbl = user.get("street_label") or street_number_and_name(user.get("street_address", ""))
            rtype      = get_recycling_type_for_date(tomorrow)
            zone       = (user.get("zone") or "").title()  # ← prefer saved zone
            # build holiday note: overrides -> scrape -> rules (no live zone lookup)
            note = None
            if zone in {"Zone 1","Zone 2","Zone 3","Zone 4"}:
                try:
                    note = holiday_note_from_overrides(zone, tomorrow)
                except NameError:
                    note = None
                if note is None:
                    try:
                        note = get_next_holiday_shift(zone, ref_date=tomorrow)
                    except Exception as e:
                        print(f"get_next_holiday_shift error for {zone}:", e)
                        note = None
                if note is None:
                    try:
                        note = holiday_note_from_rules(zone, tomorrow)
                    except NameError:
                        note = None

            msg = f"Reminder: Tomorrow is trash day for {street_lbl}.\nRecycling: {rtype}."
            if note:
                msg += f"\n{note}"
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
    Sends reminders to current subscribers.
    Uses saved 'zone' and 'collection_day' on each subscriber; DOES NOT call township lookups here.
    Only sends reminder if tomorrow matches the user's collection_day.
    Holiday note order: per-date overrides -> scrape by zone -> local rules.
    Returns a list of outcome dicts for debugging.
    """
    results: list[dict] = []

    # Load subs (sheet or memory)
    try:
        subs = current_subscribers()  # or USERS if you don't have this helper
    except Exception as e:
        print("current_subscribers() error:", e)
        return results

    if not subs:
        print("No subscribers found.")
        return results

    tz = pytz.timezone("US/Eastern")
    tomorrow = datetime.now(tz).date() + timedelta(days=1)
    tomorrow_weekday = tomorrow.strftime("%A")  # "Monday", "Tuesday", etc.

    seen: set[str] = set()
    for u in subs:
        # ---- normalize basic fields
        phone_raw = (u.get("phone") or u.get("phone_number") or "").strip()
        phone = normalize_whatsapp_number(phone_raw) if phone_raw else ""
        addr_full = (u.get("street_address") or u.get("address") or "").strip()
        street_label = (u.get("street_label") or street_number_and_name(addr_full)).strip()
        zone = (u.get("zone") or "").title()  # "Zone X" if stored
        collection_day = (u.get("collection_day") or "").title()  # "Monday", "Tuesday", etc.

        if not phone or phone in seen:
            continue
        seen.add(phone)
        if not addr_full:
            results.append({"phone": phone, "status": "skipped", "error": "missing_address"})
            continue

        # ---- CRITICAL: Check if tomorrow is this user's collection day
        if not collection_day:
            results.append({"phone": phone, "status": "skipped", "error": "missing_collection_day"})
            continue

        if collection_day != tomorrow_weekday:
            # Not this user's collection day - skip silently
            continue

        # ---- recycling type for the week
        try:
            recycling_type = get_recycling_type_for_date(tomorrow)
        except Exception as e:
            print("get_recycling_type_for_date error:", e)
            recycling_type = "Recycling"

        # ---- build holiday note WITHOUT any live zone lookup
        holiday_note = None
        if zone in {"Zone 1", "Zone 2", "Zone 3", "Zone 4"}:
            # 1) explicit per-date override (optional, if you added HOLIDAY_OVERRIDES_JSON)
            try:
                holiday_note = holiday_note_from_overrides(zone, tomorrow)
            except NameError:
                holiday_note = None

            # 2) try public page scrape by zone (your bs4 parser)
            if holiday_note is None:
                try:
                    holiday_note = get_next_holiday_shift(zone, ref_date=tomorrow)
                except Exception as e:
                    print(f"get_next_holiday_shift error for {zone}:", e)
                    holiday_note = None

            # 3) local rules fallback (HOLIDAY_RULES_JSON)
            if holiday_note is None:
                try:
                    holiday_note = holiday_note_from_rules(zone, tomorrow)
                except NameError:
                    holiday_note = None

        # ---- choose template (BASIC vs HOLIDAY) and map variables
        template_basic   = os.environ.get("TWILIO_TEMPLATE_SID_REMINDER_BASIC", "")
        template_holiday = os.environ.get("TWILIO_TEMPLATE_SID_REMINDER_HOLIDAY", "")
        template_sid = template_holiday if (holiday_note and template_holiday) else template_basic

        outcome = {"phone": phone, "template": template_sid, "sid": None,
                   "status": "skipped", "error": None}
        if not template_sid:
            outcome["error"] = "missing_template_sid"
            results.append(outcome)
            continue

        # Build variable map based on template type
        # BASIC: {{1}}=address, {{2}}=recycling_type
        # HOLIDAY: {{1}}=address, {{2}}=holiday_note, {{3}}=recycling_type
        if holiday_note:
            vars_map = {"1": street_label, "2": holiday_note, "3": recycling_type}
        else:
            vars_map = {"1": street_label, "2": recycling_type}

        # ---- send
        try:
            msg = send_whatsapp_template(to=phone, template_sid=template_sid, variables=vars_map)
            outcome.update({"sid": getattr(msg, "sid", None), "status": "queued"})
            print(f"Queued reminder sid={outcome['sid']} to {phone} vars={vars_map}")
        except Exception as e:
            outcome.update({"status": "error", "error": str(e)})
            print(f"❌ send failed for {phone}: {e}")

        results.append(outcome)

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
    Call: /run_reminders_for_date?iso=YYYY-MM-DD
          (optional) &zone=Zone%203  ← for ad-hoc testing only
    Returns JSON with queued Twilio SIDs.
    """
    iso = (request.args.get("iso") or "").strip()
    if not iso:
        return jsonify({"error": "Provide ?iso=YYYY-MM-DD"}), 400
    try:
        fake = datetime.strptime(iso, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD."}), 400

    # Optional per-request zone override (testing only)
    zone_param = (request.args.get("zone") or "").title()
    if zone_param not in {"Zone 1", "Zone 2", "Zone 3", "Zone 4"}:
        zone_param = None

    results: list[dict] = []
    try:
        subs = current_subscribers()  # or USERS if you prefer
        seen: set[str] = set()

        for u in subs:
            phone = normalize_whatsapp_number(u.get("phone") or u.get("phone_number", ""))
            if not phone or phone in seen:
                continue
            seen.add(phone)

            addr = (u.get("street_address") or "").strip()
            label = (u.get("street_label") or street_number_and_name(addr)).strip()

            # ---- use saved zone (or testing override); DO NOT live-lookup here
            zone_saved = (u.get("zone") or "").title()
            zone = zone_param or (zone_saved if zone_saved in {"Zone 1","Zone 2","Zone 3","Zone 4"} else None)

            # ---- build holiday note: overrides -> scrape -> local rules
            holiday_note = None
            if zone:
                # 1) explicit per-date override (if you wired HOLIDAY_OVERRIDES_JSON)
                try:
                    holiday_note = holiday_note_from_overrides(zone, fake)
                except NameError:
                    holiday_note = None
                # 2) scrape the zone’s holiday page (bs4 parser)
                if holiday_note is None:
                    try:
                        holiday_note = get_next_holiday_shift(zone, ref_date=fake)
                    except Exception as e:
                        print(f"get_next_holiday_shift error for {zone}:", e)
                        holiday_note = None
                # 3) fallback rules (HOLIDAY_RULES_JSON)
                if holiday_note is None:
                    try:
                        holiday_note = holiday_note_from_rules(zone, fake)
                    except NameError:
                        holiday_note = None

            # ---- choose template
            tpl_basic   = os.environ.get("TWILIO_TEMPLATE_SID_REMINDER_BASIC", "")
            tpl_holiday = os.environ.get("TWILIO_TEMPLATE_SID_REMINDER_HOLIDAY", "")
            template_sid = tpl_holiday if (holiday_note and tpl_holiday) else tpl_basic
            if not template_sid:
                results.append({"phone": phone, "status": "skipped", "error": "missing_template_sid"})
                continue

            # ---- variables: BASIC {{1}}=address, {{2}}=recycling; HOLIDAY {{1}}=address, {{2}}=holiday, {{3}}=recycling
            rtype = get_recycling_type_for_date(fake)
            if holiday_note:
                vars_map = {"1": label, "2": holiday_note, "3": rtype}
            else:
                vars_map = {"1": label, "2": rtype}

            try:
                msg = send_whatsapp_template(to=phone, template_sid=template_sid, variables=vars_map)
                results.append({
                    "phone": phone,
                    "template": template_sid,
                    "vars": vars_map,
                    "sid": getattr(msg, "sid", None),
                    "status": "queued"
                })
            except Exception as e:
                results.append({
                    "phone": phone,
                    "template": template_sid,
                    "vars": vars_map,
                    "sid": None,
                    "status": "failed",
                    "error": str(e)
                })

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

@app.route("/holiday_scrape_debug")
def holiday_scrape_debug():
    """
    Inspect what the holiday parser sees for a given zone/date.
    Call: /holiday_scrape_debug?iso=2025-12-25&zone=Zone%203
    """
    iso  = (request.args.get("iso") or "").strip()
    zone = (request.args.get("zone") or "").strip().title()
    if not iso or zone not in {"Zone 1","Zone 2","Zone 3","Zone 4"}:
        return jsonify({"error":"Use ?iso=YYYY-MM-DD&zone=Zone%201..4"}), 400

    try:
        d = datetime.strptime(iso, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error":"Bad iso date"}), 400

    # Get the raw scraped data
    idx = _scrape_zone_index(zone, d.year)
    entries_serialized = [
        {
            "date": entry["date"].isoformat(),
            "name": entry["name"],
            "weekday": entry["weekday"]
        }
        for entry in idx
    ]

    note = get_next_holiday_shift(zone, ref_date=d)
    return jsonify({
        "iso": iso,
        "zone": zone,
        "year": d.year,
        "holiday_note": note,
        "all_entries": entries_serialized,
        "entry_count": len(entries_serialized)
    })

@app.route("/raw_html_debug")
def raw_html_debug():
    """
    Show the raw HTML content from the zone holiday page for debugging.
    Call: /raw_html_debug?zone=Zone%203
    Uses Playwright to bypass bot detection.
    """
    zone = (request.args.get("zone") or "").strip().title()
    if zone not in {"Zone 1","Zone 2","Zone 3","Zone 4"}:
        return jsonify({"error":"Use ?zone=Zone%201..4"}), 400

    url = ZONE_URLS[zone]

    try:
        from playwright.sync_api import sync_playwright
        from flask import Response

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            html = page.content()
            browser.close()

        # Return as plain text with proper content type
        return Response(html, mimetype='text/html')
    except Exception as e:
        return jsonify({"error": str(e), "zone": zone, "url": url}), 500

@app.route("/holiday_trace")
def holiday_trace():
    """
    Deep-dive trace of the holiday logic for a given address/date/zone.
    Usage examples:
      /holiday_trace?address=230%20Ardleigh%20Rd,%20Penn%20Valley,%20PA&iso=2025-12-25
      /holiday_trace?iso=2025-12-25&zone=Zone%203
    Returns JSON describing each step:
      - token fetch & status
      - zone source (param/cache/lookup) and value
      - holiday page URL for that zone
      - parsed holiday row for the ISO week
      - final 'holiday_note' string (the {{3}} value)
    """
    iso  = (request.args.get("iso") or "").strip()
    addr = (request.args.get("address") or "").strip()
    zone_param = (request.args.get("zone") or "").strip().title()

    out = {
        "inputs": {"iso": iso, "address": addr, "zone_param": zone_param},
        "token": {"status": None, "error": None},
        "zone": {"value": None, "source": None, "error": None},
        "holiday_page": {"url": None},
        "parse": {"match_week": None, "holiday_name": None, "new_weekday": None, "error": None},
        "holiday_note": None
    }

    # 1) Parse date
    if not iso:
        return jsonify({"error": "Provide ?iso=YYYY-MM-DD"}), 400
    try:
        ref_date = datetime.strptime(iso, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Invalid iso format, use YYYY-MM-DD"}), 400

    # 2) Resolve zone (param beats cache beats lookup)
    zone = None
    if zone_param in {"Zone 1", "Zone 2", "Zone 3", "Zone 4"}:
        zone = zone_param
        out["zone"]["source"] = "param"
    else:
        # Try to find in USERS cache if an address matches
        if addr:
            cache = next((u for u in USERS
                          if (u.get("street_address","").strip().lower() == addr.strip().lower())
                          and u.get("zone")), None)
            if cache and cache.get("zone") in {"Zone 1","Zone 2","Zone 3","Zone 4"}:
                zone = cache.get("zone")
                out["zone"]["source"] = "cache"

        # If still missing, try live lookup (may 403 on Render)
        if not zone and addr:
            try:
                token = get_auth_token()
                out["token"]["status"] = "ok" if token else "empty"
                lookup_result = lookup_zone_by_address(addr)
                if lookup_result:
                    zone = lookup_result.get("zone")
                out["zone"]["source"] = "lookup"
            except Exception as e:
                out["token"]["status"] = "error"
                out["token"]["error"] = str(e)
                out["zone"]["error"] = "address lookup failed (token/site blocked)"

    out["zone"]["value"] = zone

    # Abort early if we still don't have a zone
    if zone not in {"Zone 1","Zone 2","Zone 3","Zone 4"}:
        out["holiday_note"] = None
        return jsonify(out)

    # 3) Build zone page URL
    page_url = ZONE_URLS.get(zone)
    out["holiday_page"]["url"] = page_url

    # 4) Ask the parser for this week
    try:
        note = get_next_holiday_shift(zone, ref_date=ref_date)
        out["holiday_note"] = note
        if note:
            # Try to extract "Holiday Name" and "Weekday" for reporting
            m = re.search(r"^(.*?):\s*collection on\s*(Monday|Tuesday|Wednesday|Thursday|Friday)\.?$", note, re.I)
            if m:
                out["parse"]["holiday_name"] = m.group(1)
                out["parse"]["new_weekday"] = m.group(2).title()
        else:
            out["parse"]["error"] = "no holiday shift found for this ISO week"
    except Exception as e:
        out["parse"]["error"] = str(e)

    return jsonify(out)

@app.route("/health")
def health():
    """Simple health check endpoint for monitoring."""
    return jsonify({"status": "ok"}), 200

#######################

# Note: no if __name__ == '__main__' run-loop here; the Web service should not
# run a scheduler. Your Render Cron should import this module and call
# send_weekly_reminders() at 8:00 PM ET (via a small `cron.py`).
