from flask import Flask, request, jsonify
import requests
import json
import os
import csv
import threading
import time
import queue

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

app = Flask(__name__)

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
STATE_FILE = os.getenv("TRADE_STATE_FILE", "trade_state.json")
TELEGRAM_MAX_RETRIES = int(os.getenv("TELEGRAM_MAX_RETRIES", "5"))
TELEGRAM_MIN_INTERVAL_SECONDS = float(os.getenv("TELEGRAM_MIN_INTERVAL_SECONDS", "0.5"))

LONDON_TZ = ZoneInfo("Europe/London")

# =========================================================
# STORAGE
# =========================================================

active_trades = {}
signals = []
seen_unmatched_updates = set()
telegram_send_lock = threading.Lock()
last_telegram_send_at = 0.0
telegram_outbox = queue.Queue(maxsize=int(os.getenv("TELEGRAM_QUEUE_MAXSIZE", "1000")))

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

last_daily_report = ""
last_weekly_report = ""
last_monthly_report = ""

# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(message, reply_to_message_id=None, reply_to_trade_id=None, store_message_id_for_trade_id=None):

    if not BOT_TOKEN or not CHAT_ID:
        print("Missing Telegram config")
        return None

    item = {
        "message": str(message).strip(),
        "reply_to_message_id": reply_to_message_id,
        "reply_to_trade_id": reply_to_trade_id,
        "store_message_id_for_trade_id": store_message_id_for_trade_id
    }

    try:
        telegram_outbox.put(item, timeout=2)
        print("Telegram queued. Queue size:", telegram_outbox.qsize())

    except queue.Full:
        print("Telegram queue full. Sending directly as fallback.")
        return send_telegram_now(item)

    return None

def send_telegram_now(item):

    global last_telegram_send_at

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    reply_to_message_id = item.get("reply_to_message_id")
    reply_to_trade_id = item.get("reply_to_trade_id")

    if reply_to_trade_id and not reply_to_message_id:
        trade = active_trades.get(reply_to_trade_id)

        if trade:
            reply_to_message_id = trade.get("telegram_message_id")

    payload = {
        "chat_id": CHAT_ID,
        "text": item.get("message", "")
    }

    if reply_to_message_id:
        payload["reply_parameters"] = {
            "message_id": reply_to_message_id,
            "allow_sending_without_reply": True
        }

    with telegram_send_lock:

        for attempt in range(TELEGRAM_MAX_RETRIES):

            wait_time = TELEGRAM_MIN_INTERVAL_SECONDS - (time.time() - last_telegram_send_at)

            if wait_time > 0:
                time.sleep(wait_time)

            try:

                response = requests.post(
                    url,
                    json=payload,
                    timeout=10
                )

                last_telegram_send_at = time.time()
                print("Telegram response:", response.text)

                result = response.json()

                if result.get("ok"):
                    return result.get("result", {}).get("message_id")

                retry_after = result.get("parameters", {}).get("retry_after")

                if retry_after is not None:
                    sleep_for = float(retry_after) + 0.5
                    print("Telegram rate limited. Sleeping:", sleep_for)
                    time.sleep(sleep_for)
                    continue

            except Exception as e:

                print("Telegram error:", str(e))

            time.sleep(1 + attempt)

    return None

def telegram_sender_worker():

    while True:

        item = telegram_outbox.get()

        try:
            message_id = send_telegram_now(item)
            trade_id = item.get("store_message_id_for_trade_id")

            if trade_id and message_id and trade_id in active_trades:
                active_trades[trade_id]["telegram_message_id"] = message_id
                save_trade_state()

        except Exception as e:
            print("Telegram worker error:", str(e))

        finally:
            telegram_outbox.task_done()

# =========================================================
# HELPERS
# =========================================================

def now_string():
    return datetime.now(LONDON_TZ).strftime("%d %b %Y %H:%M")

def clean_symbol(symbol):

    text = str(symbol or "UNKNOWN").strip()

    if ":" in text:
        text = text.split(":", 1)[1]

    text = text.upper()

    if text in ("XAUUSD", "GOLD"):
        return "GOLD"

    return text

def format_timeframe(timeframe):

    text = str(timeframe or "N/A").strip()
    upper = text.upper()

    mapping = {
        "1": "1min",
        "3": "3min",
        "5": "5min",
        "15": "15min",
        "30": "30min",
        "45": "45min",
        "60": "1h",
        "120": "2h",
        "180": "3h",
        "240": "4h",
        "1440": "1D",
        "D": "1D",
        "1D": "1D",
        "W": "1W",
        "1W": "1W",
        "M": "1M",
        "1M": "1M"
    }

    return mapping.get(upper, text)

def save_trade_state():

    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"active_trades": active_trades}, f)

    except Exception as e:
        print("State save error:", str(e))

def load_trade_state():

    global active_trades

    try:
        if not os.path.exists(STATE_FILE):
            return

        with open(STATE_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)

        active_trades = saved.get("active_trades", {})
        print("Loaded trade state:", len(active_trades), "trades")

    except Exception as e:
        print("State load error:", str(e))

def unmatched_update_key(data, label):

    return "|".join([
        str(label or ""),
        str(data.get("trade_id") or ""),
        str(data.get("ticker") or ""),
        str(data.get("timeframe") or ""),
        str(data.get("price") or "")
    ])

def update_price(data, trade, price_field):

    if data.get("price") is not None:
        return data.get("price")

    if data.get(price_field) is not None:
        return data.get(price_field)

    if trade and trade.get(price_field) is not None:
        return trade.get(price_field)

    return "N/A"

def send_unmatched_update(data, label, emoji, price_field, closed=False):

    key = unmatched_update_key(data, label)

    if key in seen_unmatched_updates:
        print("Duplicate unmatched update ignored:", key)
        return

    seen_unmatched_updates.add(key)

    if len(seen_unmatched_updates) > 2000:
        seen_unmatched_updates.clear()

    symbol = clean_symbol(data.get("ticker", "UNKNOWN"))
    timeframe = format_timeframe(data.get("timeframe", "N/A"))
    price = update_price(data, None, price_field)

    msg = f'{emoji} {label} {symbol} | {timeframe}\nPrice: {price}'

    if closed:
        msg += "\nClosed"

    print(
        "Unmatched update sent without reply tag:",
        label,
        "trade_id=",
        data.get("trade_id")
    )

    send_telegram(msg)

def get_session():

    hour = datetime.now(LONDON_TZ).hour

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

        if total <= 0:
            continue

        wr = wins / total

        if wr > best_score:
            best_score = wr
            best_name = key

    return best_name

load_trade_state()

# =========================================================
# CLUSTER TRACKING
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

    raw_symbol = data.get("ticker", "UNKNOWN")
    raw_timeframe = data.get("timeframe", "N/A")

    trade = {
        "trade_id": trade_id,
        "symbol": clean_symbol(raw_symbol),
        "raw_symbol": raw_symbol,
        "timeframe": format_timeframe(raw_timeframe),
        "raw_timeframe": raw_timeframe,
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
        "closed": False,
        "opened": now_string(),
        "telegram_message_id": None
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

    send_telegram(msg, store_message_id_for_trade_id=trade_id)
    save_trade_state()

def handle_tp1(data):

    trade_id = data.get("trade_id")

    if trade_id not in active_trades:
        send_unmatched_update(data, "TP1", "🎯", "tp1")
        return

    trade = active_trades[trade_id]

    if trade.get("closed"):
        print("TP1 ignored because trade is already closed:", trade_id)
        return

    if trade["tp1_hit"]:
        return

    trade["tp1_hit"] = True

    stats["tp1_hits"] += 1

    msg = f'🎯 TP1 {trade["symbol"]} | {trade["timeframe"]}\nPrice: {trade["tp1"]}'

    send_telegram(msg, reply_to_trade_id=trade_id)
    save_trade_state()
    
def handle_tp2(data):

    trade_id = data.get("trade_id")

    if trade_id not in active_trades:
        send_unmatched_update(data, "TP2", "🎯", "tp2")
        return

    trade = active_trades[trade_id]

    if trade.get("closed"):
        print("TP2 ignored because trade is already closed:", trade_id)
        return

    if trade["tp2_hit"]:
        return

    trade["tp2_hit"] = True

    stats["tp2_hits"] += 1

    msg = f'🎯 TP2 {trade["symbol"]} | {trade["timeframe"]}\nPrice: {trade["tp2"]}'

    send_telegram(msg, reply_to_trade_id=trade_id)
    save_trade_state()
    
def handle_tp3(data):

    trade_id = data.get("trade_id")

    if trade_id not in active_trades:
        send_unmatched_update(data, "TP3", "🏆", "tp3", closed=True)
        return

    trade = active_trades[trade_id]

    if trade.get("closed"):
        print("TP3 ignored because trade is already closed:", trade_id)
        return

    if trade["tp3_hit"]:
        return

    trade["tp3_hit"] = True
    trade["closed"] = True

    stats["tp3_hits"] += 1
    stats["wins"] += 1
    stats["closed_trades"] += 1

    update_cluster_stats(trade, "win")

    with open("trades.csv", "a", newline="") as f:

        writer = csv.writer(f)

        writer.writerow([
            trade["symbol"],
            trade["source"],
            trade["preset"],
            trade["timeframe"],
            get_session(),
            "TP3"
        ])

    msg = f'🏆 TP3 {trade["symbol"]} | {trade["timeframe"]}\nPrice: {trade["tp3"]}\nClosed'

    send_telegram(msg, reply_to_trade_id=trade_id)
    save_trade_state()

def handle_sl(data):

    trade_id = data.get("trade_id")

    if trade_id not in active_trades:
        send_unmatched_update(data, "SL", "🛑", "sl", closed=True)
        return

    trade = active_trades[trade_id]

    if trade.get("closed"):
        print("SL ignored because trade is already closed:", trade_id)
        return

    if trade["sl_hit"]:
        return

    trade["sl_hit"] = True
    trade["closed"] = True

    stats["closed_trades"] += 1

    # DIRECT LOSS
    if not trade["tp1_hit"] and not trade["tp2_hit"] and not trade["tp3_hit"]:

        stats["losses"] += 1

        update_cluster_stats(trade, "loss")

        with open("trades.csv", "a", newline="") as f:

            writer = csv.writer(f)

            writer.writerow([
                trade["symbol"],
                trade["source"],
                trade["preset"],
                trade["timeframe"],
                get_session(),
                "SL"
            ])

        msg = f'🛑 SL {trade["symbol"]} | {trade["timeframe"]}\nPrice: {trade["sl"]}\nClosed'

        send_telegram(msg, reply_to_trade_id=trade_id)
        save_trade_state()

    else:

        update_cluster_stats(trade, "win")

        result_type = "TP1_SL"

        if trade["tp2_hit"]:
            result_type = "TP2_SL"

        with open("trades.csv", "a", newline="") as f:

            writer = csv.writer(f)

            writer.writerow([
                trade["symbol"],
                trade["source"],
                trade["preset"],
                trade["timeframe"],
                get_session(),
                result_type
            ])

        msg = f'⚠️ CLOSED {trade["symbol"]} | {trade["timeframe"]}\nResult: {result_type}'

        send_telegram(msg, reply_to_trade_id=trade_id)
        save_trade_state()

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
# AUTO REPORTS
# =========================================================

def daily_report_scheduler():

    global last_daily_report

    while True:

        now = datetime.now(LONDON_TZ)
        current_key = now.strftime("%Y-%m-%d")

        if now.hour == 23 and now.minute == 59:

            if last_daily_report != current_key:

                send_telegram(generate_cluster_report())

                print("Daily report sent.")

                last_daily_report = current_key

        time.sleep(20)

def weekly_report_scheduler():

    global last_weekly_report

    while True:

        now = datetime.now(LONDON_TZ)
        current_key = now.strftime("%Y-%W")

        if now.weekday() == 4 and now.hour == 23 and now.minute == 59:

         if last_weekly_report != current_key:

            send_telegram(
                "📈 WEEKLY REPORT\\n\\n" +
                 generate_cluster_report()
            )

            print("Weekly report sent.")

            last_weekly_report = current_key

        time.sleep(20)

def monthly_report_scheduler():

    global last_monthly_report

    while True:

        now = datetime.now(LONDON_TZ)
        current_key = now.strftime("%Y-%m")

        tomorrow = now + timedelta(days=1)

        is_last_day = tomorrow.month != now.month

        if is_last_day and now.hour == 23 and now.minute == 59:

         if last_monthly_report != current_key:

            send_telegram( 
                "📊 MONTHLY REPORT\\n\\n" +
                generate_cluster_report()
            )
 
            print("Monthly report sent.")

            last_monthly_report = current_key

        time.sleep(20)

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
    global signals
    global stats
    global seen_unmatched_updates

    active_trades = {}
    signals = []
    seen_unmatched_updates = set()

    stats = {
        "wins": 0,
        "losses": 0,
        "tp1_hits": 0,
        "tp2_hits": 0,
        "tp3_hits": 0,
        "closed_trades": 0
    }

    save_trade_state()

    return jsonify({
        "status": "cleared"
    })

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

        # =====================================
        # PLAIN TEXT ALERTS
        # =====================================

        if not raw_data.startswith("{"):

            send_telegram(
                "📩 ALERT RECEIVED:\\n\\n" + raw_data
            )

            return "OK", 200

        # =====================================
        # JSON ALERTS
        # =====================================

        data = json.loads(raw_data)

        action = data.get("action")
        event = data.get("event")

        # =====================================
        # BUY / SELL
        # =====================================

        if action == "buy" or action == "sell":

            signals.append(data)

            create_trade(data)

        # =====================================
        # TP1
        # =====================================

        elif event == "tp1_hit":

            signals.append(data)

            handle_tp1(data)

        # =====================================
        # TP2
        # =====================================

        elif event == "tp2_hit":

            signals.append(data)

            handle_tp2(data)

        # =====================================
        # TP3
        # =====================================

        elif event == "tp3_hit":

            signals.append(data)

            handle_tp3(data)

        # =====================================
        # SL
        # =====================================

        elif event == "sl_hit":

            signals.append(data)

            handle_sl(data)

        return "OK", 200

    except Exception as e:

        print("Webhook error:", str(e))

        return "OK", 200

# =========================================================
# START THREADS
# =========================================================

threading.Thread(
    target=telegram_sender_worker,
    daemon=True
).start()

threading.Thread(
    target=daily_report_scheduler,
    daemon=True
).start()

threading.Thread(
    target=weekly_report_scheduler,
    daemon=True
).start()

threading.Thread(
    target=monthly_report_scheduler,
    daemon=True
).start()

# =========================================================
# START SERVER
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000
    )
