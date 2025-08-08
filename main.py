from flask import Flask, request, jsonify
import json
import os
from twilio.rest import Client

app = Flask(__name__)

# === File storage ===
DATA_FILE = "users.json"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")  # Set in Render settings

# === Twilio credentials from environment variables ===
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.environ.get("TWILIO_WHATSAPP_NUMBER")

if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
else:
    twilio_client = None

# Load existing users
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        try:
            user_data = json.load(f)
        except json.JSONDecodeError:
            user_data = []
else:
    user_data = []

@app.route('/')
def home():
    return "Webhook is live!"

@app.route('/add_user', methods=['POST'])
def add_user():
    data = request.get_json()
    address = data.get('address')
    phone = data.get('phone')
    consent = data.get('consent')

    if not consent:
        print(f"❌ Consent not given for: {phone}")
        return jsonify({"status": "consent_not_given"}), 403

    if any(u['phone'] == phone for u in user_data):
        print(f"ℹ️ User already exists: {phone}")
        return jsonify({"status": "already_exists"}), 200

    # Save new user
    user_data.append({
        "address": address,
        "phone": phone
    })
    with open(DATA_FILE, "w") as f:
        json.dump(user_data, f, indent=2)

    print(f"✅ Added user: {phone} at {address}")

    # Send WhatsApp confirmation
    if twilio_client:
        try:
            twilio_client.messages.create(
                from_=f"whatsapp:{TWILIO_WHATSAPP_NUMBER}",
                to=f"whatsapp:{phone}",
                body=(
                    f"Hi! You’re now signed up for the Lower Merion trash & recycling reminders. "
                    f"We’ll send you a message the night before your trash day."
                )
            )
            print(f"📩 WhatsApp confirmation sent to {phone}")
        except Exception as e:
            print(f"⚠️ Failed to send WhatsApp message: {e}")

    return jsonify({"status": "success"}), 200

@app.route('/users', methods=['GET'])
def get_users():
    password = request.args.get("password")
    if password != ADMIN_PASSWORD:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(user_data), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)


############test
app = Flask(__name__)

@app.route("/test_message", methods=["GET"])
def test_message():
    try:
        account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
        auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
        from_whatsapp_number = os.environ.get("TWILIO_WHATSAPP_NUMBER")

        client = Client(account_sid, auth_token)

        to_number = "whatsapp:+13029812102"  # your number in WhatsApp format

        message = client.messages.create(
            from_=from_whatsapp_number,
            body="✅ Test: Your trash reminder bot is working!",
            to=to_number
        )

        return jsonify({"status": "sent", "sid": message.sid})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)})