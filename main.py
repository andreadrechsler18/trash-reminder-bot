import os
import json
from datetime import date, datetime, timedelta
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
# Recycling schedule loader (static)
# ---------------------------
# Static 2025 schedule from Lower Merion Township PDF
RECYCLING_SCHEDULE = {
    date(2025, 1, 6): "Paper",      date(2025, 1, 13): "Commingled",
    date(2025, 1, 20): "Paper",     date(2025, 1, 27): "Commingled",
    date(2025, 2, 3): "Paper",      date(2025, 2, 10): "Commingled",
    date(2025, 2, 17): "Paper",     date(2025, 2, 24): "Commingled",
    date(2025, 3, 3): "Paper",      date(2025, 3, 10): "Commingled",
    date(2025, 3, 17): "Paper",     date(2025, 3, 24): "Commingled",
    date(2025, 3, 31): "Paper",
    date(2025, 4, 7): "Commingled", date(2025, 4, 14): "Paper",
    date(2025, 4, 21): "Commingled",date(2025, 4, 28): "Paper",
    date(2025, 5, 5): "Commingled", date(2025, 5, 12): "Paper",
    date(2025, 5, 19): "Commingled",date(2025, 5, 26): "Paper",
    date(2025, 6, 2): "Commingled", date(2025, 6, 9): "Paper",
    date(2025, 6, 16): "Commingled",date(2025, 6, 23): "Paper",
    date(2025, 6, 30): "Commingled",
    date(2025, 7, 7): "Paper",      date(2025, 7, 14): "Commingled",
    date(2025, 7, 21): "Paper",     date(2025, 7, 28): "Commingled",
    date(2025, 8, 4): "Paper",      date(2025, 8, 11): "Commingled",
    date(2025, 8, 18): "Paper",     date(2025, 8, 25): "Commingled",
    date(2025, 9, 1): "Paper",      date(2025, 9, 8): "Commingled",
    date(2025, 9, 15): "Paper",     date(2025, 9, 22): "Commingled",
    date(2025, 9, 29): "Paper",
    date(2025, 10, 6): "Commingled",date(2025, 10, 13): "Paper",
    date(2025, 10, 20): "Commingled",date(2025, 10, 27): "Paper",
    date(2025, 11, 3): "Commingled",date(2025, 11, 10): "Paper",
    date(2025, 11, 17): "Commingled",date(2025, 11, 24): "Paper",
    date(2025, 12, 1): "Commingled",date(2025, 12, 8): "Paper",
    date(2025, 12, 15): "Commingled",date(2025, 12, 22): "Paper",
    date(2025, 12, 29): "Commingled",
}

def get_recycling_type_for_date(check_date):
    """Return recycling type for the week containing check_date."""
    monday = check_date - timedelta(days=check_date.weekday())
    return RECYCLING_SCHEDULE.get(monday, "Recycling")

print(f"✅ Using static recycling schedule with {len(RECYCLING_SCHEDULE)} weeks.")

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
