import os
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, request, abort
from apscheduler.schedulers.background import BackgroundScheduler
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# === LINE env ===
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
# 指定只通知的群組（可選）；若留空就會改用 broadcast
LINE_GROUP_ID = os.environ.get("LINE_GROUP_ID", "").strip()

# 若你的伺服器是 UTC、而你要用台灣時間，可設 TIME_OFFSET_MINUTES=480 (UTC+8)
TIME_OFFSET_MINUTES = int(os.environ.get("TIME_OFFSET_MINUTES", "0"))

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

DB_FILE = "boss.db"


def now_local():
    """回傳套用時差後的現在時間"""
    return datetime.utcnow() + timedelta(minutes=TIME_OFFSET_MINUTES)


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


@app.route("/", methods=["GET"])
def home():
    return "Boss Timer Bot is running!"


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()

    # 在群組中輸入 groupid 取得群組ID
    if text.lower() == "groupid" and getattr(event.source, "type", "") == "group":
        gid = event.source.group_id
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"群組ID：{gid}"))
        return

    reply = process_command(text)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))


def process_command(text: str) -> str:
    parts = text.split()
    if not parts:
        return "指令：\n新增 名稱 地點 分鐘\n名稱 死亡 HH:MM\n清單\n名稱 通報開/通報關"

    cmd = parts[0]

    # 新增 名稱 地點 分鐘
    if cmd == "新增" and len(parts) >= 4:
        name, location, respawn = parts[1], parts[2], int(parts[3])
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO bosses (name, location, respawn_minutes) VALUES (?, ?, ?)",
            (name, location, respawn),
        )
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

        c.execute(
            "UPDATE bosses SET last_death=?, next_spawn=? WHERE name=?",
            (death_time.strftime("%H:%M"), next_spawn.strftime("%H:%M"), name),
        )
        conn.commit()
        conn.close()
        return f"☠️ 已設定 {name} 死亡時間 {time_str}\n預測重生時間：{next_spawn.strftime('%H:%M')}"

    # 清單
    if cmd == "清單":
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute(
            "SELECT name, location, respawn_minutes, last_death, next_spawn, notify FROM bosses"
        )
        bosses = c.fetchall()
        conn.close()
        if not bosses:
            return "目前沒有任何 Boss 資料。"
        lines = []
        for name, location, mins, last_death, next_spawn, notify in bosses:
            lines.append(
                f"🐲 {name}（{location}）\n"
                f"重生間隔：{mins} 分鐘\n"
                f"死亡時間：{last_death or '未設定'}\n"
                f"下次重生：{next_spawn or '未設定'}\n"
                f"通知：{'開' if notify else '關'}"
            )
        return "\n\n".join(lines)

    # 名稱 通報開 / 名稱 通報關
    if len(parts) == 2 and parts[1] in ["通報開", "通報關"]:
        name = parts[0]
        val = 1 if parts[1] == "通報開" else 0
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("UPDATE bosses SET notify=? WHERE name=?", (val, name))
        conn.commit()
        conn.close()
        return f"🔔 {name} 通報已{'開啟' if val else '關閉'}"

    return "指令錯誤，請用：\n新增 名稱 地點 分鐘\n名稱 死亡 HH:MM\n清單\n名稱 通報開/通報關"


def parse_time(hhmm: str) -> datetime:
    """把 HH:MM 解析成年月日+時間（以今天、套用時差）。若輸入時間已過，就視為今天的該時間。"""
    now = now_local()
    h, m = map(int, hhmm.split(":"))
    t = datetime(now.year, now.month, now.day, h, m)
    # 讓使用者可填過去時間（例如 03:15），不自動跳到明天；這樣 respawn 才會 +interval 算到今天
    return t


def send_msg(text: str):
    """只推指定群組；若未設定群組 ID，則 broadcast"""
    try:
        if LINE_GROUP_ID:
            line_bot_api.push_message(LINE_GROUP_ID, TextSendMessage(text=text))
        else:
            line_bot_api.broadcast(TextSendMessage(text=text))
    except Exception as e:
        print("Send message error:", e)


# === 每分鐘檢查一次：到點前 5 分鐘提醒 ===
def check_boss():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name, location, next_spawn, notify FROM bosses WHERE next_spawn IS NOT NULL")
    rows = c.fetchall()
    conn.close()

    now_str = now_local().strftime("%H:%M")
    for name, location, next_spawn, notify in rows:
        if not notify:
            continue
        try:
            nh, nm = map(int, next_spawn.split(":"))
            spawn_time = now_local().replace(hour=nh, minute=nm, second=0, microsecond=0)
            notify_time = (spawn_time - timedelta(minutes=5)).strftime("%H:%M")
            if now_str == notify_time:
                msg = f"⚠️ {name} 即將於 5 分鐘後在 {location} 重生！請準備進場！"
                send_msg(msg)
        except Exception as e:
            print("Notify error:", e)


scheduler = BackgroundScheduler()
scheduler.add_job(check_boss, "interval", minutes=1)
scheduler.start()

# for Railway local start
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
