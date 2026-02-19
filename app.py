import os
import time
import base64
import requests
import threading
from flask import Flask, request, abort
from openai import OpenAI

app = Flask(__name__)

# --- 環境變數設定 ---
LINE_TOKEN = os.getenv("LINE_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_KEY")
ASST_ID = os.getenv("ASSISTANT_ID")

client = OpenAI(api_key=OPENAI_KEY)

# 暫存區：用於存放使用者的訊息包裹與計時器
message_bundles = {}
message_timers = {}
user_thread_id = None

# --- 功能函式 ---

def get_line_image_base64(message_id):
    """下載 LINE 圖片並轉為 Base64"""
    url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"
    headers = {"Authorization": f"Bearer {LINE_TOKEN}"}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        return base64.b64encode(r.content).decode('utf-8')
    return None

def get_asst_reply(user_input):
    """使用 Assistants API (Threads) 獲取有記憶的回覆"""
    global user_thread_id
    try:
        if user_thread_id is None:
            thread = client.beta.threads.create()
            user_thread_id = thread.id

        client.beta.threads.messages.create(
            thread_id=user_thread_id,
            role="user",
            content=user_input
        )

        run = client.beta.threads.runs.create(
            thread_id=user_thread_id,
            assistant_id=ASST_ID
        )

        while True:
            run_status = client.beta.threads.runs.retrieve(
                thread_id=user_thread_id, 
                run_id=run.id
            )
            if run_status.status == "completed":
                break
            elif run_status.status in ["failed", "cancelled", "expired"]:
                return "（言辰祭冷冷地看了眼手機，似乎不想理妳。）"
            time.sleep(1)

        messages = client.beta.threads.messages.list(thread_id=user_thread_id)
        return messages.data[0].content[0].text.value
    except Exception as e:
        print(f"Threads Error: {e}")
        return "（言辰祭皺了下眉，似乎在處理公事，沒聽清妳說什麼。）"

def call_ai_vision_only(text, base64_image):
    """視覺分析：扮演言辰祭的雙眼"""
    try:
        headers = {
            "Authorization": f"Bearer {OPENAI_KEY}",
            "Content-Type": "application/json"
        }
        user_content = [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
        ]
        data = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": user_content}]
        }
        r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=data)
        return r.json()["choices"][0]["message"]["content"]
    except:
        return "一張模糊的照片。"

def reply_to_line(reply_token, text):
    """發送訊息回 LINE 並加入動態打字延遲"""
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    
    processed_text = text.replace('\\', '\n')
    raw_segments = processed_text.split('\n')
    segments = [s.strip() for s in raw_segments if s.strip()][:5]
    line_messages = [{"type": "text", "text": s} for s in segments]

    # 動態延遲：基礎 1.5s + 每個字 0.2s
    typing_delay = 1.5 + (len(text) * 0.2)
    if typing_delay > 8: typing_delay = 8
    time.sleep(typing_delay)

    data = {"replyToken": reply_token, "messages": line_messages}
    requests.post(url, headers=headers, json=data)

def process_bundled_messages(reply_token, messages):
    """合併訊息後統一呼叫 AI"""
    # 將多則訊息串接
    combined_input = "；".join(messages)
    # 呼叫 AI (這會清除該使用者的包裹，由 webhook 觸發)
    reply_text = get_asst_reply(combined_input)
    if reply_text:
        reply_to_line(reply_token, reply_text)
    
    # 清空包裹紀錄
    bundle_key = "current_session"
    if bundle_key in message_bundles:
        del message_bundles[bundle_key]

# --- Webhook 主入口 ---

@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_json()
    if not body or "events" not in body or len(body["events"]) == 0:
        return "OK", 200

    event = body["events"][0]
    token = event.get("replyToken")
    if not token: return "OK", 200

    msg = event.get("message", {})
    msg_type = msg.get("type")
    
    # 這裡使用固定的 key (單人使用)
    bundle_key = "current_session"

    if msg_type == "text":
        user_input = msg.get("text")
        
        # 初始化包裹
        if bundle_key not in message_bundles:
            message_bundles[bundle_key] = []
        
        message_bundles[bundle_key].append(user_input)

        # 取消之前的計時器，重新計時
        if bundle_key in message_timers:
            message_timers[bundle_key].cancel()

        # 設定 5 秒緩衝時間
        t = threading.Timer(5.0, process_bundled_messages, args=[token, message_bundles[bundle_key]])
        message_timers[bundle_key] = t
        t.start()

    elif msg_type == "image":
        img_base64 = get_line_image_base64(msg["id"])
        image_description = call_ai_vision_only("請用一句話描述照片內容。", img_base64)
        final_prompt = f"（紀瞳傳送照片，內容是：{image_description}。請以此性格回應）"
        reply_text = get_asst_reply(final_prompt)
        reply_to_line(token, reply_text)

    elif msg_type == "sticker":
        keywords = ", ".join(msg.get("keywords", []))
        prompt = f"（紀瞳傳送貼圖，心情：{keywords}。請回應）"
        reply_text = get_asst_reply(prompt)
        reply_to_line(token, reply_text)

    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
