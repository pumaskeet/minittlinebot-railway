import os
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, request, abort
from apscheduler.schedulers.background import BackgroundScheduler
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# LINE keys from env
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
# 指定通知的群組（可選）
LINE_GROUP_ID = os.environ.get("LINE_GROUP_ID", "").strip()

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
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()

    # 在群組裡輸入 groupid 取得群組ID
    if text.lower() == "groupid" and event.source.type == "group":
        gid = event.source.group_id
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"群組ID：{gid}"))
        return

    reply = process_command(text)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

def process_command(text):
    parts = text.split()
    if not parts:
        return "請輸入指令：\n新增 名稱 地點 分鐘\n名稱 死亡 HH:MM\n清單\n名稱 通報開/通報關"

    cmd = parts[0]

    # 新增 名稱 地點 分鐘
    if cmd == "新增" and len(parts) >= 4:
        name, location, respawn = parts[1], parts[2], int(parts[3])
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO bosses (name, location, respawn_minutes) VALUES (?, ?, ?)",
                  (name, location, respawn))
        conn.commit()
        conn.close()
        return f"✅ 已新增 {name}（{location}）重生間隔 {respawn} 分鐘"

    # 名稱 死亡 HH:MM
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

    # 清單
    if cmd == "清單":
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT name, location, respawn_minutes, last_death, next_spawn, notify FROM bosses")
        bosses = c.fetchall()
        conn.close()
        if not bosses:
            return "目前沒有任何 Boss 資料。"
        msg = []
        for name, location, mins, last_death, next_spawn, notify