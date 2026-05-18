from flask import Flask, request, jsonify
import requests
import json
import os
from datetime import datetime

app = Flask(__name__)

# =========================================================
# TELEGRAM CONFIG
# =========================================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# =========================================================
# STORAGE
# =========================================================

active_trades = {}

stats = {
    "wins": 0,
    "losses": 0,
    "tp1_hits": 0,
    "tp2_hits": 0,
    "tp3_hits": 0,
    "closed_trades": 0
}

# =========================================================
# TELEGRAM
# =========================================================

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

# =========================================================
# HELPERS
# =========================================================

def now_string():

    return datetime.utcnow().strftime("%d %b %Y %H:%M")

def create_trade(data):

    trade_id = data.get("trade_id")

    if not trade_id:
        return

    if trade_id in active_trades:
        return

    trade = {
        "trade_id": trade_id,
        "symbol": data.get("ticker", "UNKNOWN"),
        "timeframe": data.get("timeframe", "N/A"),
        "direction": data.get("action", "").upper(),

        "source": data.get("source", "N/A"),
        "preset": data.get("preset", "N/A"),

        "entry": data.get("entry"),
        "sl": data.get("sl"),

        "tp1": data.get("tp1"),
        "tp2": data.get("tp2"),
        "tp3": data.get("tp3"),

        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "sl_hit": False,

        "is_win": False,
        "closed": False,

        "opened": now_string()
    }

    active_trades[trade_id] = trade

    direction_emoji = "🟢" if trade["direction"] == "BUY" else "🔴"

    msg = f"""
{direction_emoji} {trade["direction"]} {trade["symbol"]} | {trade["timeframe"]}
Src: {trade["source"]} | Preset: {trade["preset"]}

Entry: {trade["entry"]}
SL: {trade["sl"]}

TP1: {trade["tp1"]}
TP2: {trade["tp2"]}
TP3: {trade["tp3"]}

Opened: {trade["opened"]}
"""

    send_telegram(msg)

def handle_tp1(data):

    trade_id = data.get("trade_id")

    if trade_id not in active_trades:
        return

    trade = active_trades[trade_id]

    if trade["closed"]:
        return

    if trade["tp1_hit"]:
        return

    trade["tp1_hit"] = True
    trade["is_win"] = True

    stats["tp1_hits"] += 1

    msg = f"""
🎯 TP1 HIT — {trade["symbol"]} | {trade["timeframe"]}

Src: {trade["source"]} | Preset: {trade["preset"]}

TP1: {trade["tp1"]}

Hit: {now_string()}
"""

    send_telegram(msg)

def handle_tp2(data):

    trade_id = data.get("trade_id")

    if trade_id not in active_trades:
        return

    trade = active_trades[trade_id]

    if trade["closed"]:
        return

    if trade["tp2_hit"]:
        return

    trade["tp2_hit"] = True
    trade["is_win"] = True

    stats["tp2_hits"] += 1

    msg = f"""
🎯 TP2 HIT — {trade["symbol"]} | {trade["timeframe"]}

Src: {trade["source"]} | Preset: {trade["preset"]}

TP2: {trade["tp2"]}

Hit: {now_string()}
"""

    send_telegram(msg)

def handle_tp3(data):

    trade_id = data.get("trade_id")

    if trade_id not in active_trades:
        return

    trade = active_trades[trade_id]

    if trade["closed"]:
        return

    if trade["tp3_hit"]:
        return

    trade["tp3_hit"] = True
    trade["is_win"] = True
    trade["closed"] = True

    stats["tp3_hits"] += 1
    stats["wins"] += 1
    stats["closed_trades"] += 1

    msg = f"""
🏆 FULL TP HIT — {trade["symbol"]} | {trade["timeframe"]}

TP1 ✓
TP2 ✓
TP3 ✓

Closed: {now_string()}
"""

    send_telegram(msg)

def handle_sl(data):

    trade_id = data.get("trade_id")

    if trade_id not in active_trades:
        return

    trade = active_trades[trade_id]

    if trade["closed"]:
        return

    if trade["sl_hit"]:
        return

    trade["sl_hit"] = True
    trade["closed"] = True

    stats["closed_trades"] += 1

    # DIRECT SL ONLY
    if not trade["tp1_hit"] and not trade["tp2_hit"] and not trade["tp3_hit"]:

        stats["losses"] += 1

        msg = f"""
🛑 STOP LOSS HIT — {trade["symbol"]} | {trade["timeframe"]}

Src: {trade["source"]} | Preset: {trade["preset"]}

SL: {trade["sl"]}

Closed: {now_string()}
"""

        send_telegram(msg)

# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    return jsonify({
        "status": "running"
    })

# =========================================================
# STATS
# =========================================================

@app.route("/stats")
def get_stats():

    total_closed = stats["wins"] + stats["losses"]

    win_rate = 0

    if total_closed > 0:
        win_rate = round(
            (stats["wins"] / total_closed) * 100,
            2
        )

    return jsonify({
        "wins": stats["wins"],
        "losses": stats["losses"],
        "tp1_hits": stats["tp1_hits"],
        "tp2_hits": stats["tp2_hits"],
        "tp3_hits": stats["tp3_hits"],
        "closed_trades": stats["closed_trades"],
        "open_trades": len(
            [t for t in active_trades.values() if not t["closed"]]
        ),
        "win_rate": win_rate
    })

# =========================================================
# CLEAR
# =========================================================

@app.route("/clear")
def clear():

    global active_trades
    global stats

    active_trades = {}

    stats = {
        "wins": 0,
        "losses": 0,
        "tp1_hits": 0,
        "tp2_hits": 0,
        "tp3_hits": 0,
        "closed_trades": 0
    }

    return jsonify({
        "status": "cleared"
    })

# =========================================================
# WEBHOOK
# =========================================================

@app.route("/webhook", methods=["POST"])
def webhook():

    try:

        raw_data = request.get_data(as_text=True)

        if raw_data is None:
            raw_data = ""

        raw_data = str(raw_data).strip()

        print("RAW ALERT:", raw_data)

        # PLAIN TEXT FALLBACK
        if not raw_data.startswith("{"):

            send_telegram(
                f"📩 ALERT RECEIVED:\n\n{raw_data}"
            )

            return "OK", 200

        # JSON ALERT
        data = json.loads(raw_data)

        action = data.get("action")
        event = data.get("event")

        # ENTRY
        if action == "buy" or action == "sell":

            create_trade(data)

        # TP1
        elif event == "tp1_hit":

            handle_tp1(data)

        # TP2
        elif event == "tp2_hit":

            handle_tp2(data)

        # TP3
        elif event == "tp3_hit":

            handle_tp3(data)

        # SL
        elif event == "sl_hit":

            handle_sl(data)

        return "OK", 200

    except Exception as e:

        print("Webhook error:", str(e))

        return "OK", 200

# =========================================================
# START SERVER
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000
    )
