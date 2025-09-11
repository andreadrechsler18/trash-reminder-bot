import os
import json
from datetime import datetime, timedelta
import pytz
import requests
import schedule
import time
import re
import pdfplumber
from flask import Flask, request
from twilio.rest import Client

# ==== Twilio Config ====
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = "whatsapp:+14155238886"

# ==== Lower Merion Config ====
TOKEN_URL = "https://www.lowermerion.org/Home/GetToken"
SEARCH_URL = "https://flex.visioninternet.com/api/FeFlexComponent/Get"
COMPONENT_GUID = "f05e2a62-e807-4f30-b450-c2c48770ba5c"
LIST_UNIQUE_NAME = "VHWQOE27X21B7R8"

ZONE_URLS = {
    "Zone 1": "https://www.lowermerion.org/departments/public-works-department/refuse-and-recycling/holiday-collection/holiday-collection-zone-one",
    "Zone 2": "https://www.lowermerion.org/departments/public-works-department/refuse-and-recycling/holiday-collection/holiday-collection-zone-two",
    "Zone 3": "https://www.lowermerion.org/departments/public-works-department/refuse-and-recycling/holiday-collection/holiday-collection-zone-three",
    "Zone 4": "https://www.lowermerion.org/departments/public-works-department/refuse-and-recycling/holiday-collection/holiday-collection-zone-four"
}

# ==== Storage (in-memory for now) ====
USERS_FILE = "users.json"

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    return []

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)

USERS = load_users()

# ---------------------------
# Robust recycling schedule loader (with static fallback)
# ---------------------------

REMOTE_PDF_URL = "https://content.govdelivery.com/attachments/PALOWERMERION/2024/12/19/file_attachments/3108697/2025%20Recycling%20Schedule%20Final.pdf"

# Static fallback mapping (ISO date -> type) built from the official PDF table you provided
RECYCLING_SCHEDULE_STATIC = {
  "2025-01-06": "Paper", "2025-01-13": "Commingled", "2025-01-20": "Paper", "2025-01-27": "Commingled",
  "2025-02-03": "Paper", "2025-02-10": "Commingled", "2025-02-17": "Paper", "2025-02-24": "Commingled",
  "2025-03-03": "Paper", "2025-03-10": "Commingled", "2025-03-17": "Paper", "2025-03-24": "Commingled", "2025-03-31": "Paper",
  "2025-04-07": "Commingle", "2025-04-14": "Paper", "2025-04-21": "Commingle", "2025-04-28": "Paper",
  "2025-05-05": "Commingle", "2025-05-12": "Paper", "2025-05-19": "Commingle", "2025-05-26": "Paper",
  "2025-06-02": "Commingle", "2025-06-09": "Paper", "2025-06-16": "Commingle", "2025-06-23": "Paper", "2025-06-30": "Commingle",
  "2025-07-07": "Paper", "2025-07-14": "Commingle", "2025-07-21": "Paper", "2025-07-28": "Commingle",
  "2025-08-04": "Paper", "2025-08-11": "Commingle", "2025-08-18": "Paper", "2025-08-25": "Commingle",
  "2025-09-01": "Paper", "2025-09-08": "Commingle", "2025-09-15": "Paper", "2025-09-22": "Commingle", "2025-09-29": "Paper",
  "2025-10-06": "Commingle", "2025-10-13": "Paper", "2025-10-20": "Commingle", "2025-10-27": "Paper",
  "2025-11-03": "Commingle", "2025-11-10": "Paper", "2025-11-17": "Commingle", "2025-11-24": "Paper",
  "2025-12-01": "Commingle", "2025-12-08": "Paper", "2025-12-15": "Commingle", "2025-12-22": "Paper", "2025-12-29": "Commingle"
}

def clean_day_string(day_str):
    """Remove ordinal suffix and convert to int if possible."""
    try:
        return int(re.sub(r'(st|nd|rd|th)$', '', day_str.strip().lower()))
    except Exception:
        raise

def parse_text_lines_into_schedule(text, year=2025):
    """
    Parse lines of text extracted from the PDF and return a dict:
      { datetime.date(...) : "Paper" | "Commingled" }
    This accepts lines like: "January   6th, 20th   13th, 27th"
    """
    schedule_map = {}
    months = {
        "January":1,"February":2,"March":3,"April":4,"May":5,"June":6,
        "July":7,"August":8,"September":9,"October":10,"November":11,"December":12
    }

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Try to detect a leading month name
        parts = line.split()
        first = parts[0]
        if first not in months:
            continue

        month_name = first
        # Attempt to split remainder into two columns by two-or-more spaces
        remainder = line.split(month_name,1)[1].strip()
        cols = re.split(r"\s{2,}", remainder)
        paper_col = cols[0] if len(cols)>0 else ""
        comm_col = cols[1] if len(cols)>1 else ""

        paper_dates = [p.strip() for p in paper_col.split(",") if p.strip()]
        comm_dates  = [c.strip() for c in comm_col.split(",") if c.strip()]

        for d in paper_dates:
            try:
                day = clean_day_string(d)
                dt = datetime.strptime(f"{month_name} {day} {year}", "%B %d %Y").date()
                schedule_map[dt] = "Paper"
            except Exception:
                # ignore unparsable tokens
                continue
        for d in comm_dates:
            try:
                day = clean_day_string(d)
                dt = datetime.strptime(f"{month_name} {day} {year}", "%B %d %Y").date()
                schedule_map[dt] = "Commingled"
            except Exception:
                continue

    return schedule_map

def try_pdfplumber_extract(pdf_path):
    """Return concatenated text from pdfplumber pages or empty str."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            texts = []
            for i, page in enumerate(pdf.pages, start=1):
                t = page.extract_text() or ""
                texts.append(t)
            return "\n".join(texts)
    except Exception as e:
        print("pdfplumber error:", e)
        return ""

def load_recycling_schedule(pdf_path_local="recycling_schedule_2025.pdf"):
    """
    Robust loader: try local file -> try remote file -> fallback to STATIC mapping.
    Returns a dict keyed with datetime.date objects.
    """
    schedule_map = {}
    # 1) try local file
    if os.path.exists(pdf_path_local):
        print(f"Attempting to parse local PDF: {pdf_path_local}")
        text = try_pdfplumber_extract(pdf_path_local)
        if text and len(text.strip())>20:
            schedule_map = parse_text_lines_into_schedule(text)
            print("Parsed local PDF text length:", len(text))
        else:
            print("Local PDF text extraction returned empty or too short.")
    else:
        print("Local PDF not found:", pdf_path_local)

    # 2) if still empty, try remote PDF download and parse
    if not schedule_map:
        try:
            print("Attempting to download remote official PDF for parsing...")
            r = requests.get(REMOTE_PDF_URL, timeout=15)
            if r.status_code == 200 and len(r.content)>1000:
                tmp_path = "/tmp/remote_recycling_schedule.pdf"
                with open(tmp_path, "wb") as f:
                    f.write(r.content)
                text = try_pdfplumber_extract(tmp_path)
                if text and len(text.strip())>20:
                    schedule_map = parse_text_lines_into_schedule(text)
                    print("Parsed remote PDF text length:", len(text))
                else:
                    print("Remote PDF text extraction returned empty or too short.")
            else:
                print("Failed to download remote PDF or file too small, status:", r.status_code)
        except Exception as e:
            print("Error downloading/parsing remote PDF:", e)

    # 3) Fallback to static mapping if still empty
    if not schedule_map:
        print("⚠ Falling back to static built-in schedule (2025).")
        for iso, t in RECYCLING_SCHEDULE_STATIC.items():
            schedule_map[datetime.fromisoformat(iso).date()] = t
        print(f"✅ Using static recycling schedule with {len(schedule_map)} weeks.")

    else:
        print(f"✅ Loaded recycling schedule with {len(schedule_map)} weeks from PDF parsing.")

    return schedule_map

# Build the runtime mapping (date objects -> type)
RECYCLING_SCHEDULE = load_recycling_schedule()
# get_recycling_type_for_date uses RECYCLING_SCHEDULE below as before:
def get_recycling_type_for_date(check_date):
    monday = check_date - timedelta(days=check_date.weekday())
    return RECYCLING_SCHEDULE.get(monday, "Recycling")



# ==== Township API ====
def get_auth_token():
    r = requests.get(TOKEN_URL)
    r.raise_for_status()
    return r.json().get("access_token")

def lookup_zone_by_address(address):
    token = get_auth_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://www.lowermerion.org",
        "Referer": "https://www.lowermerion.org/"
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
        "Data": {
            "componentGuid": COMPONENT_GUID,
            "listUniqueName": LIST_UNIQUE_NAME
        }
    }
    resp = requests.post(SEARCH_URL, headers=headers, json=payload)
    resp.raise_for_status()
    results = resp.json().get("Data", {}).get("Items", [])
    if not results:
        return None
    for item in results:
        if "Zone" in item:
            return item["Zone"]
    return None

# ==== Holiday Shift Checker ====
def get_next_holiday_shift(zone):
    if zone not in ZONE_URLS:
        return None
    url = ZONE_URLS[zone]
    r = requests.get(url)
    r.raise_for_status()
    html = r.text
    matches = re.findall(r"(\w+ \d{1,2}, 2025).*?([Mm]onday|[Tt]uesday|[Ww]ednesday|[Tt]hursday|[Ff]riday)", html, re.S)
    today = datetime.now().date()
    for date_str, new_day in matches:
        try:
            holiday_date = datetime.strptime(date_str, "%B %d, %Y").date()
            if holiday_date >= today:
                return f"Holiday week starting {holiday_date.strftime('%B %d')}: Collection is on {new_day}."
        except ValueError:
            continue
    return None

# ==== Messaging ====
client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

def send_whatsapp_message(to, message):
    msg = client.messages.create(
        from_=TWILIO_WHATSAPP_FROM,
        body=message,
        to=to
    )
    print(f"📩 Twilio confirmation SID: {msg.sid}")

# ==== Weekly Reminder Scheduler ====
def send_weekly_reminders():
    tz = pytz.timezone("US/Eastern")
    today = datetime.now(tz).date()
    tomorrow = today + timedelta(days=1)

    for user in USERS:
        zone = lookup_zone_by_address(user["street_address"])
        if not zone:
            continue

        recycling_type = get_recycling_type_for_date(tomorrow)
        holiday_shift = get_next_holiday_shift(zone)

        reminder = f"Reminder: Tomorrow is trash day for {user['street_address']} ({zone}).\nRecycling type: {recycling_type}."
        if holiday_shift:
            reminder += f"\nNote: {holiday_shift}"

        send_whatsapp_message(user["phone_number"], reminder)

# ==== Flask App ====
app = Flask(__name__)

@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    print(f"Received JSON payload: {data}")
    phone = data.get("phone_number")
    address = data.get("street_address")
    consent = data.get("consent", "").lower()

    if not phone or not address:
        return {"status": "error", "message": "Missing phone or address"}, 400

    if "agree" in consent:
        USERS.append({"phone_number": phone, "street_address": address})
        save_users(USERS)
        send_whatsapp_message(phone, f"✅ You are subscribed for trash reminders for {address}.")
        return {"status": "ok"}
    else:
        return {"status": "error", "message": "Consent not given"}, 400

# ==== Schedule job ====
schedule.every().monday.at("19:00").do(send_weekly_reminders)
schedule.every().tuesday.at("19:00").do(send_weekly_reminders)
schedule.every().wednesday.at("19:00").do(send_weekly_reminders)
schedule.every().thursday.at("19:00").do(send_weekly_reminders)
schedule.every().friday.at("19:00").do(send_weekly_reminders)

if __name__ == "__main__":
    # Start scheduler loop
    while True:
        schedule.run_pending()
        time.sleep(60)
