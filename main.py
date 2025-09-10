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

# ==== PDF Recycling Schedule Parser ====
def clean_day_string(day_str):
    """Remove ordinal suffix and convert to integer."""
    return int(re.sub(r'(st|nd|rd|th)$', '', day_str.strip().lower()))

def load_recycling_schedule(pdf_path="recycling_schedule_2025.pdf"):
    """
    Parse Lower Merion recycling PDF by text, not tables.
    Extracts month, paper weeks, commingle weeks.
    """
    if not os.path.exists(pdf_path):
        print(f"⚠ Recycling schedule PDF not found: {pdf_path}")
        return {}

    schedule_map = {}
    year = 2025
    months = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ]

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            for line in text.splitlines():
                parts = line.strip().split()
                if not parts:
                    continue
                month = parts[0]
                if month not in months:
                    continue

                # line looks like: "January   6th, 20th   13th, 27th"
                try:
                    row = line.split()
                    # find month explicitly, then split remaining columns
                    cols = line.split(month, 1)[1].strip()
                    # split by two or more spaces (since paper/commingle are columns)
                    cols_split = re.split(r"\s{2,}", cols)
                    paper_str = cols_split[0] if len(cols_split) > 0 else ""
                    commingle_str = cols_split[1] if len(cols_split) > 1 else ""

                    paper_weeks = [d.strip() for d in paper_str.split(",") if d.strip()]
                    commingle_weeks = [d.strip() for d in commingle_str.split(",") if d.strip()]

                    for d in paper_weeks:
                        try:
                            day_num = clean_day_string(d)
                            week_start = datetime.strptime(f"{month} {day_num} {year}", "%B %d %Y").date()
                            schedule_map[week_start] = "Paper"
                        except Exception:
                            pass

                    for d in commingle_weeks:
                        try:
                            day_num = clean_day_string(d)
                            week_start = datetime.strptime(f"{month} {day_num} {year}", "%B %d %Y").date()
                            schedule_map[week_start] = "Commingle"
                        except Exception:
                            pass

                except Exception as e:
                    print(f"⚠ Parse error on line: {line} ({e})")

    print(f"✅ Loaded recycling schedule with {len(schedule_map)} weeks.")
    return schedule_map


RECYCLING_SCHEDULE = load_recycling_schedule()

def get_recycling_type_for_date(check_date):
    """
    Given a date, return the recycling type (Paper/Commingle) for that week.
    The township PDF uses 'Week Beginning' (Monday), so we align any date
    to its week's Monday before lookup.
    """
    monday = check_date - timedelta(days=check_date.weekday())
    if monday in RECYCLING_SCHEDULE:
        return RECYCLING_SCHEDULE[monday]
    else:
        return "Recycling"

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
