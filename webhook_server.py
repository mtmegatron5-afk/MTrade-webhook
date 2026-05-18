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

signals = []

stats = {
    "wins": 0,
    "losses": 0,
    "tp1_hits": 0,
    "tp2_hits": 0,
    "tp3_hits": 0,
    "closed_trades": 0
}

pair_stats = {}
combo_stats = {}
session_stats = {}
timeframe_stats = {}

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

def get_session():

    hour = datetime.utcnow().hour

    if 0 <= hour < 7:
        return "Asia"

    elif 7 <= hour < 13:
        return "London"

    elif 13 <= hour < 22:
        return "New York"

    return "Off Hours"

def best_performer(stat_dict):

    best_name = "N/A"
    best_score = -1

    for key, value in stat_dict.items():

        wins = value["wins"]
        losses = value["losses"]

        total = wins + losses

        if total == 0:
            continue

        wr = wins / total

        if wr > best_score:
            best_score = wr
            best_name = key

    return best_name

# =========================================================
# CLUSTER STATS
# =========================================================

def update_cluster_stats(trade, result_type):

    symbol = trade["symbol"]
    combo = f'{trade["source"]} + {trade["preset"]}'
    timeframe = trade["timeframe"]
    session = get_session()

    if symbol not in pair_stats:
        pair_stats[symbol] = {"wins": 0, "losses": 0}

    if combo not in combo_stats:
        combo_stats[combo] = {"wins": 0, "losses": 0}

    if session not in session_stats:
        session_stats[session] = {"wins": 0, "losses": 0}

    if timeframe not in timeframe_stats:
        timeframe_stats[timeframe] = {"wins": 0, "losses": 0}

    if result_type == "win":

        pair_stats[symbol]["wins"] += 1
        combo_stats[combo]["wins"] += 1
        session_stats[session]["wins"] += 1
        timeframe_stats[timeframe]["wins"] += 1

    elif result_type == "loss":

        pair_stats[symbol]["losses"] += 1
        combo_stats[combo]["losses"] += 1
        session_stats[session]["losses"] += 1
        timeframe_stats[timeframe]["losses"] += 1

# =========================================================
# TRADE ENGINE
# =========================================================

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

    msg = f'''
{direction_emoji} {trade["direction"]} {trade["symbol"]} | {trade["timeframe"]}
Src: {trade["source"]} | Preset: {trade["preset"]}

Entry: {trade["entry"]}
SL: {trade["sl"]}

TP1: {trade["tp1"]}
TP2: {trade["tp2"]}
TP3: {trade["tp3"]}

Opened: {trade["opened"]}
'''

    send_telegram(msg)

def handle_tp1(data):

    trade_id = data.get("trade_id")

    if trade_id not in active_trades:
        return

    trade = active_trades[trade_id]

    if trade["tp1_hit"]:
        return

    trade["tp1_hit"] = True
    trade["is_win"] = True

    stats["tp1_hits"] += 1

    msg = f'''
🎯 TP1 HIT — {trade["symbol"]} | {trade["timeframe"]}

TP1: {trade["tp1"]}

Hit: {now_string()}
'''

    send_telegram(msg)

def handle_tp2(data):

    trade_id = data.get("trade_id")

    if trade_id not in active_trades:
        return

    trade = active_trades[trade_id]

    if trade["tp2_hit"]:
        return

    trade["tp2_hit"] = True
    trade["is_win"] = True

    stats["tp2_hits"] += 1

    msg = f'''
🎯 TP2 HIT — {trade["symbol"]} | {trade["timeframe"]}

TP2: {trade["tp2"]}

Hit: {now_string()}
'''

    send_telegram(msg)

def handle_tp3(data):

    trade_id = data.get("trade_id")

    if trade_id not in active_trades:
        return

    trade = active_trades[trade_id]

    if trade["tp3_hit"]:
        return

    trade["tp3_hit"] = True
    trade["is_win"] = True
    trade["closed"] = True

    stats["tp3_hits"] += 1
    stats["wins"] += 1
    stats["closed_trades"] += 1

    update_cluster_stats(trade, "win")

    msg = f'''
🏆 FULL TP HIT — {trade["symbol"]} | {trade["timeframe"]}

TP1 ✓
TP2 ✓
TP3 ✓

Closed: {now_string()}
'''

    send_telegram(msg)

def handle_sl(data):

    trade_id = data.get("trade_id")

    if trade_id not in active_trades:
        return

    trade = active_trades[trade_id]

    if trade["sl_hit"]:
        return

    trade["sl_hit"] = True
    trade["closed"] = True

    stats["closed_trades"] += 1

    if not trade["tp1_hit"] and not trade["tp2_hit"] and not trade["tp3_hit"]:

        stats["losses"] += 1

        update_cluster_stats(trade, "loss")

        msg = f'''
🛑 STOP LOSS HIT — {trade["symbol"]} | {trade["timeframe"]}

SL: {trade["sl"]}

Closed: {now_string()}
'''

        send_telegram(msg)

# =========================================================
# REPORTS
# =========================================================

def generate_cluster_report():

    closed_trades = stats["wins"] + stats["losses"]

    open_trades = len([
        t for t in active_trades.values()
        if not t["closed"]
    ])

    win_rate = 0
    loss_rate = 0

    if closed_trades > 0:

        win_rate = round(
            (stats["wins"] / closed_trades) * 100,
            1
        )

        loss_rate = round(
            (stats["losses"] / closed_trades) * 100,
            1
        )

    best_pair = best_performer(pair_stats)
    best_combo = best_performer(combo_stats)
    best_session = best_performer(session_stats)
    best_timeframe = best_performer(timeframe_stats)

    report = f'''
📊 CLUSTER REPORT

Closed Trades: {closed_trades}
Open Trades: {open_trades}

🏆 Full TP Trades: {stats["tp3_hits"]}
🎯 TP1 Hits: {stats["tp1_hits"]}
🎯 TP2 Hits: {stats["tp2_hits"]}

🛑 Direct SL Losses: {stats["losses"]}

📈 Win Rate: {win_rate}%
📉 Loss Rate: {loss_rate}%

━━━━━━━━━━━━━━

📊 Best Pair: {best_pair}
⚙️ Best Combo: {best_combo}
🕒 Best Performance Session: {best_session}
⏱ Best Timeframe: {best_timeframe}

━━━━━━━━━━━━━━

⚠️ This is not financial advice.
Trade responsibly. Past performance does not guarantee future results.
'''

    return report

# =========================================================
# ROUTES
# =========================================================

@app.route("/")
def home():

    return jsonify({
        "status": "running"
    })

@app.route("/stats")
def get_stats():

    return jsonify({
        "cluster_report": generate_cluster_report()
    })

@app.route("/clear")
def clear():

    global active_trades
    global stats
    global signals

    active_trades = {}

    signals = []

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
# SIGNAL QUEUE
# =========================================================

@app.route("/signals")
def get_signals():

    global signals

    out = signals.copy()

    signals = []

    return jsonify(out)

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

        if not raw_data.startswith("{"):

            send_telegram(
                "📩 ALERT RECEIVED:\n\n" + raw_data
            )
            
            return "OK", 200

        data = json.loads(raw_data)

        action = data.get("action")
        event = data.get("event")

        if action == "buy" or action == "sell":

            signals.append(data)

            create_trade(data)

        elif event == "tp1_hit":

            signals.append(data)

            handle_tp1(data)

        elif event == "tp2_hit":

            signals.append(data)

            handle_tp2(data)

        elif event == "tp3_hit":

            signals.append(data)

            handle_tp3(data)

        elif event == "sl_hit":

            signals.append(data)

            handle_sl(data)

        return "OK", 200

    except Exception as e:

        print("Webhook error:", str(e))

        return "OK", 200

# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000
    )
