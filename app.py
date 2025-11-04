import os
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, request, abort
from apscheduler.schedulers.background import BackgroundScheduler
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# LINE 驗證設定
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

DB_FILE = "boss.db"


def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS bosses (
        name TEXT PRIMARY KEY,
        location TEXT,
        respawn_minutes INTEGER,
        last_death TEXT,
        next_spawn TEXT,
        notify INTEGER DEFAULT 1
    )
    """)
    conn.commit()
    conn.close()


init_db()


@app.route("/", methods=['GET'])
def home():
    return "Boss Timer Bot is running!"


@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    reply = process_command(text)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))


def process_command(text):
    parts = text.split()
    if not parts:
        return "請輸入指令，例如：\n新增 飛龍 山谷 180\n或輸入 清單"

    cmd = parts[0]

    # 新增 Boss
    if cmd == "新增" and len(parts) >= 4:
        name, location, respawn = parts[1], parts[2], int(parts[3])
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO bosses (name, location, respawn_minutes) VALUES (?, ?, ?)",
                  (name, location, respawn))
        conn.commit()
        conn.close()
        return f"✅ 已新增 {name}（{location}）重生間隔 {respawn} 分鐘"

    # 設定死亡時間
    if len(parts) == 3 and parts[1] == "死亡":
        name, time_str = parts[0], parts[2]
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT respawn_minutes FROM bosses WHERE name=?", (name,))
        row = c.fetchone()
        if not row:
            conn.close()
            return f"❌ 找不到 {name}，請先用『新增』指令建立"
        respawn = row[0]
        death_time = parse_time(time_str)
        next_spawn = death_time + timedelta(minutes=respawn)
        c.execute("UPDATE bosses SET last_death=?, next_spawn=? WHERE name=?",
                  (death_time.strftime("%H:%M"), next_spawn.strftime("%H:%M"), name))
        conn.commit()
        conn.close()
        return f"☠️ 已設定 {name} 死亡時間 {time_str}\n預測重生時間：{next_spawn.strftime('%H:%M')}"

    # 顯示清單
    if cmd == "清單":
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT name, location, respawn_minutes, last_death, next_spawn, notify FROM bosses")
        bosses = c.fetchall()
        conn.close()
        if not bosses:
            return "目前沒有任何 Boss 資料。"
        msg = ""
        for b in bosses:
            msg += f"🐲 {b[0]}（{b[1]}）\n重生間隔：{b[2]} 分鐘\n死亡時間：{b[3] or '未設定'}\n下次重生：{b[4] or '未設定'}\n通知：{'開' if b[5] else '關'}\n\n"
        return msg.strip()

    # 通報開關
    if len(parts) == 2 and parts[1] in ["通報開", "通報關"]:
        name, state = parts[0], parts[1]
        val = 1 if state == "通報開" else 0
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("UPDATE bosses SET notify=? WHERE name=?", (val, name))
        conn.commit()
        conn.close()
        return f"🔔 {name} 通報已{'開啟' if val else '關閉'}"

    return "指令錯誤，請用以下格式：\n新增 名稱 地點 重生分鐘\n名稱 死亡 HH:MM\n清單\n名稱 通報開/通報關"


def parse_time(tstr):
    now = datetime.now()
    h, m = map(int, tstr.split(":"))
    return datetime(now.year, now.month, now.day, h, m)


# === 自動檢查 Boss 即將重生 ===
def check_boss():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name, location, next_spawn, notify FROM bosses WHERE next_spawn IS NOT NULL")
    rows = c.fetchall()
    conn.close()

    now = datetime.now().strftime("%H:%M")
    for name, location, next_spawn, notify in rows:
        if not notify:
            continue
        try:
            # 提醒時間 = 預測重生時間 - 5 分鐘
            nh, nm = map(int, next_spawn.split(":"))
            spawn_time = datetime.now().replace(hour=nh, minute=nm, second=0, microsecond=0)
            notify_time = (spawn_time - timedelta(minutes=5)).strftime("%H:%M")
            if now == notify_time:
                msg = f"⚠️ {name} 即將於 5 分鐘後在 {location} 重生！請準備進場！"
                line_bot_api.broadcast(TextSendMessage(text=msg))
        except Exception as e:
            print("Notify error:", e)


scheduler = BackgroundScheduler()
scheduler.add_job(check_boss, "interval", minutes=1)
scheduler.start()

# 啟動應用
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
