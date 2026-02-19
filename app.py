import os
import time
import base64
import requests
import threading
from flask import Flask, request, abort
from openai import OpenAI

app = Flask(__name__)

# --- 環境變數設定 (請在 Railway 設定) ---
LINE_TOKEN = os.getenv("LINE_TOKEN")
V1API_KEY = os.getenv("V1API_KEY") 
V1API_BASE_URL = "https://vg.v1api.cc/v1"
# 指定模型名稱 (請根據 v1api 官網提供的型號填寫)
TARGET_MODEL = "gemini-2.0-flash" 

# --- 初始化 OpenAI 客戶端 (對接 v1api) ---
client = OpenAI(
    api_key=V1API_KEY,
    base_url=V1API_BASE_URL
)

def load_system_prompt():
    """從外部檔案讀取言辰祭的人設"""
    try:
        with open("character_prompt.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return "妳扮演言辰祭，一個冷淡但寵溺妻子的總經理。說話簡潔、使用 \\ 分隔。"

# 訊息打包與定時器
message_bundles = {}
message_timers = {}

# --- 功能函式 ---

def get_line_image_base64(message_id):
    """下載 LINE 圖片並轉為 Base64"""
    url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"
    headers = {"Authorization": f"Bearer {LINE_TOKEN}"}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        return base64.b64encode(r.content).decode('utf-8')
    return None

def get_v1api_reply(user_id, content_list):
    """透過 v1api 獲取回覆 (支援文字與圖片)"""
    try:
        # 建立訊息列表
        messages = [
            {"role": "system", "content": load_system_prompt()},
            {"role": "user", "content": content_list}
        ]
        
        response = client.chat.completions.create(
            model=TARGET_MODEL,
            messages=messages,
            temperature=0.7
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"API Error: {e}")
        return "（言辰祭似乎在處理公事，隨口應了一聲，沒聽清妳說什麼。）"

def reply_to_line(reply_token, text):
    """將回覆送回 LINE (處理 \ 分隔符號)"""
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    
    # 處理 \ 分隔多則訊息
    processed_text = text.replace('\\', '\n')
    raw_segments = processed_text.split('\n')
    segments = [s.strip() for s in raw_segments if s.strip()][:5]
    line_messages = [{"type": "text", "text": s} for s in segments]

    # 模擬打字延遲
    delay = 1.0 + (len(text) * 0.15)
    time.sleep(min(delay, 7))

    data = {"replyToken": reply_token, "messages": line_messages}
    requests.post(url, headers=headers, json=data)

def process_bundle(reply_token, user_id):
    """處理 5 秒內打包的訊息"""
    if user_id in message_bundles:
        combined_text = "；".join(message_bundles[user_id])
        del message_bundles[user_id]
        
        # 傳送文字列表給 AI
        reply_text = get_v1api_reply(user_id, [{"type": "text", "text": combined_text}])
        if reply_text:
            reply_to_line(reply_token, reply_text)

# --- Webhook 主入口 ---

@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_json()
    if not body or "events" not in body:
        return "OK", 200

    event = body["events"][0]
    token = event.get("replyToken")
    user_id = event["source"].get("userId", "default_user")
    if not token: return "OK", 200

    msg = event.get("message", {})
    msg_type = msg.get("type")

    # 1. 文字訊息 (5秒打包)
    if msg_type == "text":
        user_input = msg.get("text")
        if user_id not in message_bundles:
            message_bundles[user_id] = []
        message_bundles[user_id].append(user_input)
        
        if user_id in message_timers:
            message_timers[user_id].cancel()
        
        t = threading.Timer(5.0, process_bundle, args=[token, user_id])
        message_timers[user_id] = t
        t.start()

    # 2. 圖片訊息
    elif msg_type == "image":
        img_b64 = get_line_image_base64(msg["id"])
        if img_b64:
            content = [
                {"type": "text", "text": "這是紀瞳傳給你的照片，請看圖回覆她。"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
            ]
            reply_text = get_v1api_reply(user_id, content)
            reply_to_line(token, reply_text)

    # 3. 貼圖訊息
    elif msg_type == "sticker":
        keywords = ", ".join(msg.get("keywords", ["一個貼圖"]))
        reply_text = get_v1api_reply(user_id, [{"type": "text", "text": f"（紀瞳傳送了代表「{keywords}」的貼圖）"}])
        reply_to_line(token, reply_text)

    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
