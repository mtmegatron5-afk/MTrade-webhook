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
# TELEGRAM FUNCTION
# =====================================

def send_telegram(message):

    if not BOT_TOKEN or not CHAT_ID:
        print("Missing Telegram env variables")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": str(message)
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=10
        )

        print("Telegram response:", response.text)

    except Exception as e:

        print("Telegram error:", str(e))

# =====================================
# WEBHOOK ROUTE
# =====================================

@app.route("/webhook", methods=["POST"])
def webhook():

    try:

        # Accept ANY TradingView payload
        raw_data = request.get_data(as_text=True)

        # Prevent crashes from empty payloads
        if raw_data is None:
            raw_data = ""

        raw_data = str(raw_data)

        print("RAW ALERT:", raw_data)

        # Limit huge messages
        raw_data = raw_data[:3500]

        # Send to Telegram
        try:

            send_telegram(
                f"📩 ALERT RECEIVED:\n\n{raw_data}"
            )

        except Exception as tg_error:

            print("Telegram error:", str(tg_error))

        # ALWAYS return success
        return "OK", 200

    except Exception as e:

        print("Webhook error:", str(e))

        # NEVER fail TradingView
        return "OK", 200

# =====================================
# START SERVER
# =====================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000
    )
