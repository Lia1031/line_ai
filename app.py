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

# 訊息包裹與定時器
message_bundles = {}
message_timers = {}
user_thread_id = None

# --- 角色設定 (保險起見，在這裡也放一份簡單的) ---
SYSTEM_PROMPT = "你扮演言辰祭，一個冷淡但寵溺妻子的總經理。說話簡潔、不用驚嘆號、使用 \\ 分隔句子。"

# --- 功能函式 ---

def get_line_image_base64(message_id):
    url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"
    headers = {"Authorization": f"Bearer {LINE_TOKEN}"}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        return base64.b64encode(r.content).decode('utf-8')
    return None

def get_asst_reply(user_input):
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
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content}
            ]
        }
        r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=data, timeout=25)
        return r.json()["choices"][0]["message"]["content"]
    except:
        return "一張普通的照片。"

def reply_to_line(reply_token, text):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    
    processed_text = text.replace('\\', '\n')
    raw_segments = processed_text.split('\n')
    segments = [s.strip() for s in raw_segments if s.strip()][:5]
    line_messages = [{"type": "text", "text": s} for s in segments]

    # 動態模擬打字延遲
    typing_delay = 1.5 + (len(text) * 0.2)
    if typing_delay > 8: typing_delay = 8
    time.sleep(typing_delay)

    data = {"replyToken": reply_token, "messages": line_messages}
    requests.post(url, headers=headers, json=data)

def process_bundle(reply_token, bundle_key):
    if bundle_key in message_bundles:
        combined_text = "；".join(message_bundles[bundle_key])
        del message_bundles[bundle_key]
        
        reply_text = get_asst_reply(combined_text)
        if reply_text:
            reply_to_line(reply_token, reply_text)

# --- Webhook ---

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
    bundle_key = "user_1"

    if msg_type == "text":
        user_input = msg.get("text")
        if bundle_key not in message_bundles:
            message_bundles[bundle_key] = []
        message_bundles[bundle_key].append(user_input)
        
        if bundle_key in message_timers:
            message_timers[bundle_key].cancel()
            
        t = threading.Timer(5.0, process_bundle, args=[token, bundle_key])
        message_timers[bundle_key] = t
        t.start()

    elif msg_type == "image":
        img_base64 = get_line_image_base64(msg["id"])
        image_desc = call_ai_vision_only("請用一句話客觀描述這張照片內容。", img_base64)
        reply_text = get_asst_reply(f"（紀瞳傳送照片：{image_desc}。請以此性格回應）")
        reply_to_line(token, reply_text)

    elif msg_type == "sticker":
        keywords = ", ".join(msg.get("keywords", []))
        reply_text = get_asst_reply(f"（紀瞳傳送貼圖：{keywords}。妳的回應是？）")
        reply_to_line(token, reply_text)

    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
