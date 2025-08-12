from flask import Flask, request, jsonify
import os, json, traceback
from twilio.rest import Client

app = Flask(__name__)

DATA_FILE = "users.json"

# Load Twilio credentials from environment variables
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM")  # should be like 'whatsapp:+14155238886'

# --- ADDED for WhatsApp Sandbox ---
TWILIO_SANDBOX_JOIN_CODE = os.environ.get("join settlers-sail")  
# -----------------------------------

# Create Twilio client only if creds exist
twilio_client = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    try:
        twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    except Exception as e:
        print("⚠️ Twilio client init failed:", e)
        twilio_client = None

# Load existing users from file
if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, "r") as f:
            user_data = json.load(f)
    except Exception:
        user_data = []
else:
    user_data = []


def normalize_whatsapp_number(raw):
    """Return a string like 'whatsapp:+13029812102' or None if invalid."""
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
    """Return True if consent looks affirmative."""
    if raw is None:
        return False
    s = str(raw).strip().lower()
    return ("yes" in s) or ("agree" in s) or ("i agree" in s) or (s == "true")


@app.route("/add_user", methods=["POST"])
def add_user():
    try:
        data = request.get_json(force=True, silent=True)
        print("Received JSON payload:", data)

        if not data:
            print("❌ No JSON payload or bad content-type.")
            return jsonify({"error": "No JSON payload or bad content-type"}), 400

        address = data.get("street_address") or data.get("address") or data.get("addr")
        phone = data.get("phone_number") or data.get("phone") or data.get("whatsapp")
        consent_raw = data.get("consent")

        if not address or not phone:
            return jsonify({"error": "Missing required fields (address or phone)"}), 400

        consent = parse_consent(consent_raw)
        if not consent:
            print(f"❌ Consent not given for phone {phone}")
            return jsonify({"error": "Consent required"}), 403

        phone_whatsapp = normalize_whatsapp_number(phone)
        if not phone_whatsapp:
            return jsonify({"error": "Invalid phone number format"}), 400

        if any(u.get("phone") == phone_whatsapp for u in user_data):
            print(f"ℹ️ User already exists: {phone_whatsapp}")
            return jsonify({"status": "already_exists"}), 200

        new_user = {"address": address, "phone": phone_whatsapp, "consent": True}
        user_data.append(new_user)
        with open(DATA_FILE, "w") as f:
            json.dump(user_data, f, indent=2)

        print(f"✅ Added user: {phone_whatsapp} at {address}")

        # Try to send confirmation via Twilio if configured
        if twilio_client and TWILIO_WHATSAPP_FROM:
            try:
                # --- ADDED for WhatsApp Sandbox ---
                body_text = (
                    "Hi — you've been signed up for Lower Merion trash & recycling reminders. "
                    "Reply STOP to unsubscribe."
                )
                if TWILIO_SANDBOX_JOIN_CODE:
                    body_text = (
                        f"Hi — you've been signed up for Lower Merion trash & recycling reminders.\n\n"
                        f"⚠️ If you haven't joined the sandbox today, send this message to {TWILIO_WHATSAPP_FROM} on WhatsApp first:\n\n"
                        f"{TWILIO_SANDBOX_JOIN_CODE}\n\n"
                        "Reply STOP to unsubscribe."
                    )
                # -----------------------------------

                msg = twilio_client.messages.create(
                    from_=TWILIO_WHATSAPP_FROM,
                    to=phone_whatsapp,
                    body=body_text
                )
                print("📩 Twilio confirmation SID:", getattr(msg, "sid", None))
            except Exception as e:
                print("⚠️ Twilio send error:", str(e))

        return jsonify({"status": "success", "user": new_user}), 201

    except Exception as e:
        tb = traceback.format_exc()
        print("❌ Exception in /add_user:\n", tb)
        return jsonify({"error": "internal_server_error", "trace": tb}), 500



