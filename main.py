import os
import json
from datetime import date, datetime, timedelta
import pytz
import requests
import schedule
import time
import re
import pdfplumber
from flask import Flask, request, Response
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse

# ==== Twilio Config ====
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = "whatsapp:+6106382707"

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

# --- WhatsApp template helper  ---
import os, json
from twilio.rest import Client

# read from env (already set in Render Web + Cron)
TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN  = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_WHATSAPP_FROM = os.environ["TWILIO_WHATSAPP_FROM"]

def send_whatsapp_template(to: str, template_sid: str, variables: dict | None = None):
    """
    Send a WhatsApp *template* (approved in Twilio Content).
    - to: 'whatsapp:+1XXXXXXXXXX'
    - template_sid: the HX... Content SID
    - variables: dict of {"1": "value for {{1}}", "2": "..."} as strings
    """
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    return client.messages.create(
        from_=TWILIO_WHATSAPP_FROM,
        to=to,
        content_sid=template_sid,
        content_variables=json.dumps(variables or {})
    )
# --- end helper ---


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

# Replace <FORM_URL> with your Google Form URL (or remove the link text)
FORM_URL = "https://forms.gle/ziXa2nyFr9Mdtgbw8"

@app.route("/whatsapp_webhook", methods=["POST"])
def whatsapp_webhook():
    # Debug: print incoming form values (you'll see this in Render logs)
    print("Twilio incoming webhook:", dict(request.form))

    from_number = request.form.get("From")  # e.g. "whatsapp:+13029812102"
    body = (request.form.get("Body") or "").strip()
    resp = MessagingResponse()

    # Basic safety
    if not from_number:
        resp.message("No sender number detected.")
        return Response(str(resp), mimetype="application/xml")

    lower = body.lower()

    # Unsubscribe: user texts STOP
    if "stop" in lower and len(lower) <= 10:
        removed = False
        for u in USERS:
            if u.get("phone_number") == from_number:
                USERS.remove(u)
                save_users(USERS)
                removed = True
                break
        if removed:
            resp.message("You have been unsubscribed from Trash reminders. To re-subscribe, use the Google Form or reply START.")
        else:
            resp.message("We couldn't find your subscription. To sign up, please fill the Google Form: " + FORM_URL)
        return Response(str(resp), mimetype="application/xml")

    # Quick info: what's recycling this week
    if "recycl" in lower:
        tz = pytz.timezone("US/Eastern")
        today = datetime.now(tz).date()
        recycling = get_recycling_type_for_date(today)
        resp.message(
            f"This week is *{recycling}* recycling.\n"
            f"To receive nightly reminders the evening before your pickup, sign up here: {FORM_URL}"
        )
        return Response(str(resp), mimetype="application/xml")

    # Quick info: trash/pickup day (uses stored user info)
    if any(k in lower for k in ("trash", "pickup", "garbage")):
        user = next((u for u in USERS if u.get("phone_number") == from_number), None)
        if user:
            tz = pytz.timezone("US/Eastern")
            tomorrow = datetime.now(tz).date() + timedelta(days=1)
            rec_type = get_recycling_type_for_date(tomorrow)
            addr = user.get("street_address")
            zone = lookup_zone_by_address(addr) if addr else None
            holiday_note = get_next_holiday_shift(zone) if zone else None

            msg = f"Tomorrow is trash day for {addr} ({zone}).\nRecycling: {rec_type}."
            if holiday_note:
                msg += f"\nNote: {holiday_note}"
            resp.message(msg)
        else:
            resp.message(
                "I don't have you on file. To subscribe, please use the Google Form: "
                f"{FORM_URL}\nOr reply with: JOIN <your address> (if you'd like in-chat signup)."
            )
        return Response(str(resp), mimetype="application/xml")

    # Default/help reply
    resp.message(
        "Hi — I can tell you whether it's Paper or Commingled recycling this week, "
        "and send nightly reminders. Try:\n• \"What's recycling this week?\"\n• \"When's trash pickup?\"\n• \"STOP\" to unsubscribe."
    )
    return Response(str(resp), mimetype="application/xml")

# --- TEMP TEST ROUTE: remove after testing ---
@app.route("/test_welcome")
def test_welcome():
    from os import environ as env
    msg = send_whatsapp_template(
        to="whatsapp:+13029812102",
        template_sid=env["TWILIO_TEMPLATE_SID_WELCOME"],  # must be the HX... for the WhatsApp-approved “welcome” template
        variables={}
    )
    return f"OK, SID={msg.sid}"
# --- END TEMP ROUTE ---

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
