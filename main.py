from flask import Flask, request, jsonify
from twilio.rest import Client
import os
import datetime

app = Flask(__name__)

# In-memory user store (replace with DB for production)
users = []

# Load Twilio credentials from environment
account_sid = os.getenv('TWILIO_ACCOUNT_SID')
auth_token = os.getenv('TWILIO_AUTH_TOKEN')
whatsapp_from = os.getenv('TWILIO_WHATSAPP_FROM')

client = Client(account_sid, auth_token)

@app.route('/')
def home():
    return "Trash Reminder Bot is running!"

@app.route('/test_message')
def test_message():
    to = request.args.get('to')
    if not to:
        return "Missing 'to' parameter", 400

    try:
        message = client.messages.create(
            body="This is a test WhatsApp message from Trash Reminder Bot!",
            from_=whatsapp_from,
            to=to
        )
        return f"Test message sent to {to}. Message SID: {message.sid}"
    except Exception as e:
        return f"Failed to send message: {str(e)}", 500

@app.route('/add_user', methods=['POST'])
def add_user():
    data = request.get_json()
    if not data:
        return "Missing JSON data", 400

    street_address = data.get('street_address')
    phone_number = data.get('phone_number')
    consent = data.get('consent')

    if not (street_address and phone_number and consent):
        return "Missing required fields", 400

    # Simple check for duplicates
    if any(u['phone_number'] == phone_number for u in users):
        return "User already registered", 409

    users.append({
        'street_address': street_address,
        'phone_number': phone_number,
        'consent': consent,
        'added_at': datetime.datetime.utcnow().isoformat()
    })

    return jsonify({"message": "User added successfully"}), 201

@app.route('/send_reminders')
def send_reminders():
    # Placeholder: you would add your logic here to:
    # - Check today's date & holiday rules
    # - Determine trash/recycling type per user address
    # - Send WhatsApp reminders to each user who consented

    # For now, just simulate sending a reminder to all users
    sent = 0
    failed = 0
    for user in users:
        if user['consent'].lower() != 'yes':
            continue
        to = user['phone_number']
        try:
            client.messages.create(
                body=f"Reminder: Trash day is tomorrow at {user['street_address']}. Don't forget to put out your bins!",
                from_=whatsapp_from,
                to=to
            )
            sent += 1
        except Exception:
            failed += 1

    return jsonify({
        "sent": sent,
        "failed": failed,
        "total": len(users)
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
