from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

# =====================================
# TELEGRAM CONFIG
# =====================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# =====================================
# HOME ROUTE
# =====================================

@app.route("/")
def home():

    return jsonify({
        "status": "running"
    })

# =====================================
# SEND TELEGRAM MESSAGE
# =====================================

def send_telegram(message):

    if not BOT_TOKEN or not CHAT_ID:
        print("Missing Telegram env variables")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": message
    }

    try:

        response = requests.post(url, json=payload)

        print("Telegram response:", response.text)

    except Exception as e:

        print("Telegram error:", str(e))

# =====================================
# WEBHOOK ROUTE
# =====================================

@app.route("/webhook", methods=["POST"])
def webhook():

    try:

        raw_data = request.data.decode("utf-8")

        print("RAW ALERT:", raw_data)

        send_telegram(f"📩 ALERT RECEIVED:\n\n{raw_data}")

        return jsonify({
            "status": "success"
        }), 200

    except Exception as e:

        print("Webhook error:", str(e))

        return jsonify({
            "error": str(e)
        }), 400

# =====================================
# START SERVER
# =====================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000
    )
