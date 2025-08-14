from flask import Flask, request, jsonify
import os, json, traceback, requests
from twilio.rest import Client
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import pytz

app = Flask(__name__)

DATA_FILE = "users.json"

# Twilio credentials
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM")

twilio_client = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    try:
        twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    except Exception as e:
        print("⚠️ Twilio client init failed:", e)

# Load user data
if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, "r") as f:
            user_data = json.load(f)
    except Exception:
        user_data = []
else:
    user_data = []

# === Helpers ===
def normalize_whatsapp_number(raw):
    if not raw:
        return None
    raw = str(raw).strip()
    if raw.startswith("whatsapp:"):
        num = raw.split("whatsapp:")[1]
        if num.startswith("+"):
            return "whatsapp:" + num
        else:
            return "whatsapp:+" + num if num.isdigit() else None
    stripped = "".join(ch for ch in raw if ch.isdigit() or ch == "+")
    if stripped.startswith("+"):
        return "whatsapp:" + stripped
    if stripped.isdigit():
        return "whatsapp:+1" + stripped if len(stripped) >= 7 else None
    return None

def parse_consent(raw):
    if raw is None:
        return False
    s = str(raw).strip().lower()
    return ("yes" in s) or ("agree" in s) or ("i agree" in s) or (s == "true")

# === Township Data Fetch ===
COMPONENT_GUID = "f05e2a62-e807-4f30-b450-c2c48770ba5c"
LIST_UNIQUE_NAME = "VHWQOE27X21B7R8"

ZONE_URLS = {
    "zone 1": "https://www.lowermerion.org/departments/public-works-department/refuse-and-recycling/holiday-collection/holiday-collection-zone-one",
    "zone 2": "https://www.lowermerion.org/departments/public-works-department/refuse-and-recycling/holiday-collection/holiday-collection-zone-two",
    "zone 3": "https://www.lowermerion.org/departments/public-works-department/refuse-and-recycling/holiday-collection/holiday-collection-zone-three",
    "zone 4": "https://www.lowermerion.org/departments/public-works-department/refuse-and-recycling/holiday-collection/holiday-collection-zone-four",
}

def get_auth_token():
    r = requests.get("https://www.lowermerion.org/Home/GetToken")
    r.raise_for_status()
    return r.text.strip('"')

def lookup_zone(address):
    token = get_auth_token()
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
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://www.lowermerion.org",
        "Referer": "https://www.lowermerion.org/",
    }
    resp = requests.post(
        "https://flex.visioninternet.com/api/FeFlexComponent/Get",
        json=payload,
        headers=headers
    )
    resp.raise_for_status()
    data = resp.json()
    items = data.get("items", [])
    if not items:
        return None
    zone_info = items[0].get("Refuse & Recycling", "")
    return zone_info.lower()

def get_next_holiday_for_zone(zone_name):
    url = ZONE_URLS.get(zone_name)
    if not url:
        return None
    html = requests.get(url).text
    # Very naive parse — find first date in table
    import re
    match = re.search(r"(\w+\s+\d{1,2},\s+\d{4}).*?([\w\s]+)", html, re.S)
    if match:
        return {"date": match.group(1), "note": match.group(2).strip()}
    return None

def get_recycling_type_for_week(address):
    # Placeholder: actual parsing from LM schedule guide would go here
    # For now just alternate weeks: even=paper, odd=commingled
    week_num = datetime.now().isocalendar()[1]
    return "Paper" if week_num % 2 == 0 else "Commingled"

# === Reminder Logic ===
def send_weekly_reminders():
    print("📅 Running weekly reminder job...")
    for user in user_data:
        addr = user.get("address")
        phone = user.get("phone")
        if not addr or not phone:
            continue
        try:
            zone = lookup_zone(addr)
            recycle_type = get_recycling_type_for_week(addr)
            holiday = get_next_holiday_for_zone(zone) if zone else None

            msg_text = f"Reminder: Tomorrow is trash day in {zone.title() if zone else 'your area'}.\nRecycling: {recycle_type}."
            if holiday:
                msg_text += f"\nHoliday Schedule Change: {holiday['note']} ({holiday['date']})."

            if twilio_client and TWILIO_WHATSAPP_FROM:
                twilio_client.messages.create(
                    from_=TWILIO_WHATSAPP_FROM,
                    to=phone,
                    body=msg_text
                )
                print(f"✅ Sent reminder to {phone}")
            else:
                print(f"ℹ️ Would send to {phone}: {msg_text}")
        except Exception as e:
            print(f"⚠️ Reminder send failed for {phone}: {e}")

# === Flask Routes ===
@app.route("/add_user", methods=["POST"])
def add_user():
    try:
        data = request.get_json(force=True, silent=True)
        print("Received JSON payload:", data)
        if not data:
            return jsonify({"error": "No JSON payload or bad content-type"}), 400

        address = data.get("street_address") or data.get("address") or data.get("addr")
        phone = data.get("phone_number") or data.get("phone") or data.get("whatsapp")
        consent_raw = data.get("consent")

        if not address or not phone:
            return jsonify({"error": "Missing required fields (address or phone)"}), 400

        consent = parse_consent(consent_raw)
        if not consent:
            return jsonify({"error": "Consent required"}), 403

        phone_whatsapp = normalize_whatsapp_number(phone)
        if not phone_whatsapp:
            return jsonify({"error": "Invalid phone number format"}), 400

        if any(u.get("phone") == phone_whatsapp for u in user_data):
            return jsonify({"status": "already_exists"}), 200

        new_user = {"address": address, "phone": phone_whatsapp, "consent": True}
        user_data.append(new_user)
        with open(DATA_FILE, "w") as f:
            json.dump(user_data, f, indent=2)

        if twilio_client and TWILIO_WHATSAPP_FROM:
            try:
                twilio_client.messages.create(
                    from_=TWILIO_WHATSAPP_FROM,
                    to=phone_whatsapp,
                    body=("Hi — you've been signed up for Lower Merion trash & recycling reminders. "
                          "Reply STOP to unsubscribe.")
                )
            except Exception as e:
                print("⚠️ Twilio send error:", str(e))

        return jsonify({"status": "success", "user": new_user}), 201

    except Exception as e:
        tb = traceback.format_exc()
        print("❌ Exception in /add_user:\n", tb)
        return jsonify({"error": "internal_server_error", "trace": tb}), 500

# === Scheduler Setup ===
scheduler = BackgroundScheduler(timezone=pytz.timezone("US/Eastern"))
# Run every Monday at 7 PM ET (night before Tuesday trash day example)
scheduler.add_job(send_weekly_reminders, 'cron', day_of_week='mon', hour=19, minute=0)
scheduler.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
