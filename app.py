import os
import time
import base64
import requests
from flask import Flask, request, abort
from openai import OpenAI

app = Flask(__name__)

# --- 環境變數設定 ---
LINE_TOKEN = os.getenv("LINE_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_KEY")
ASST_ID = os.getenv("ASSISTANT_ID")

client = OpenAI(api_key=OPENAI_KEY)

# 暫存 Thread ID (只有妳一個人在用，這樣設定最簡單)
# 如果未來想給很多人用，這裡需要存入資料庫
user_thread_id = None

# --- 言辰祭角色設定 ---
SYSTEM_PROMPT = """
(此處已在 OpenAI 後台設定，程式碼內保持 call_ai 備用或直接套用)
"""

# --- 功能函式 ---

def get_line_image_base64(message_id):
    """下載 LINE 圖片並轉為 Base64"""
    url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"
    headers = {"Authorization": f"Bearer {LINE_TOKEN}"}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        return base64.b64encode(r.content).decode('utf-8')
    return None

def get_asst_reply(user_input, base64_image=None):
    """使用 Assistants API (Threads) 獲取有記憶的回覆"""
    global user_thread_id
    try:
        # 1. 確保有 Thread
        if user_thread_id is None:
            thread = client.beta.threads.create()
            user_thread_id = thread.id

        # 2. 處理內容 (支援文字與圖片)
        content = [{"type": "text", "text": user_input}]
        if base64_image:
            # 注意：Assistants API 處理圖片通常需要先上傳檔案
            # 這裡我們先維持 Vision API 的邏輯來處理圖片，或簡單讓 AI 知道有圖
            content.append({"type": "text", "text": "（妳看著這張照片，依照妳的性格給予回應）"})

        # 3. 將訊息加入 Thread
        client.beta.threads.messages.create(
            thread_id=user_thread_id,
            role="user",
            content=user_input
        )

        # 4. 執行 Run
        run = client.beta.threads.runs.create(
            thread_id=user_thread_id,
            assistant_id=ASST_ID
        )

        # 5. 等待回覆 (Polling)
        while run.status != "completed":
            time.sleep(0.5)
            run = client.beta.threads.retrieve(thread_id=user_thread_id, run_id=run.id)

        # 6. 取得回覆內容
        messages = client.beta.threads.messages.list(thread_id=user_thread_id)
        return messages.data[0].content[0].text.value
    
    except Exception as e:
        print(f"Threads Error: {e}")
        return "（言辰祭皺了下眉，似乎在處理公事，沒聽清妳說什麼。）"

def call_ai_vision_only(text, base64_image):
    """當有圖片時，使用 Vision 模式（因為 Threads 下傳圖片邏輯較不同，此為穩定方案）"""
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
        return "（言辰祭盯著照片看了很久，沒說話。）"

def reply_to_line(reply_token, text):
    """發送訊息回 LINE 並加入模擬打字延遲"""
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Authorization": f"Bearer {LINE_TOKEN}",
        "Content-Type": "application/json"
    }
    
    processed_text = text.replace('\\', '\n')
    raw_segments = processed_text.split('\n')
    segments = [s.strip() for s in raw_segments if s.strip()][:5]
    line_messages = [{"type": "text", "text": s} for s in segments]

    # 模擬言辰祭打字的沉穩感
    time.sleep(1.5)

    data = {
        "replyToken": reply_token,
        "messages": line_messages
    }
    requests.post(url, headers=headers, json=data)

# --- Webhook 主入口 ---

@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_json()
    if not body or "events" not in body or len(body["events"]) == 0:
        return "OK", 200

    event = body["events"][0]
    token = event.get("replyToken")
    if not token:
        return "OK", 200

    msg = event.get("message", {})
    msg_type = msg.get("type")
    
    reply_text = ""

    if msg_type == "text":
        # 使用有記憶的 Threads 模式
        reply_text = get_asst_reply(msg.get("text"))
    
    elif msg_type == "image":
        # 讀圖模式 (目前 Vision 與 Threads 記憶整合較難，優先保證讀圖成功)
        img_base64 = get_line_image_base64(msg["id"])
        reply_text = call_ai_vision_only("（紀瞳傳送了一張照片，請以此性格回覆）", img_base64)

    elif msg_type == "sticker":
        keywords = ", ".join(msg.get("keywords", []))
        prompt = f"（紀瞳傳送了一個貼圖，心情大概是：{keywords}。妳對此的回應是？）"
        reply_text = get_asst_reply(prompt)

    if reply_text:
        reply_to_line(token, reply_text)
    
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
