from flask import Flask, request, jsonify
import json
import os

app = Flask(__name__)

DATA_FILE = "users.json"
ADMIN_PASSWORD = "trashadmin123"  # Change this to something private

# Load existing users from file when server starts
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

    if consent:
        # Check if phone already exists to avoid duplicates
        if not any(u['phone'] == phone for u in user_data):
            user_data.append({
                "address": address,
                "phone": phone
            })
            with open(DATA_FILE, "w") as f:
                json.dump(user_data, f, indent=2)
            print(f"✅ Added user: {phone} at {address}")
            return jsonify({"status": "success"}), 200
        else:
            print(f"ℹ️ User already exists: {phone}")
            return jsonify({"status": "already_exists"}), 200
    else:
        print(f"❌ Consent not given for: {phone}")
        return jsonify({"status": "consent_not_given"}), 403

@app.route('/users', methods=['GET'])
def get_users():
    password = request.args.get("password")
    if password != ADMIN_PASSWORD:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(user_data), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
