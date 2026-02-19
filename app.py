import os
import time
import base64
import requests
import threading
from flask import Flask, request, abort
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

app = Flask(__name__)

# --- 環境變數設定 (請在 Railway 設定) ---
LINE_TOKEN = os.getenv("LINE_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

# --- Gemini 初始化 ---
genai.configure(api_key=GEMINI_KEY)

def load_system_prompt():
    """讀取外部人設檔案"""
    try:
        with open("character_prompt.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return "妳扮演言辰祭，一個冷淡但寵溺妻子的總經理。"

# 初始化模型
# 這裡嘗試使用最基礎的名稱，並確保加上 models/ 前綴
try:
    # 方案 A: 嘗試使用相容性最高的 Flash 版本
    model_name_to_use = "models/gemini-1.5-flash" 
    model = genai.GenerativeModel(
        model_name=model_name_to_use,
        system_instruction=load_system_prompt()
    )
    print(f"✅ 成功初始化模型: {model_name_to_use}")
except Exception as e:
    # 方案 B: 如果 A 失敗，嘗試使用 Pro 版本
    print(f"⚠️ {model_name_to_use} 啟動失敗，嘗試切換模型...")
    model = genai.GenerativeModel(
        model_name="models/gemini-1.5-pro",
        system_instruction=load_system_prompt()
    )

# 儲存對話紀錄、訊息包裹、定時器
chat_sessions = {}
message_bundles = {}
message_timers = {}

# --- 功能函式 ---

def get_line_image_bytes(message_id):
    """下載 LINE 圖片"""
    url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"
    headers = {"Authorization": f"Bearer {LINE_TOKEN}"}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        return r.content
    return None

def get_gemini_reply(user_id, content):
    """呼叫 Gemini 獲取回覆"""
    if user_id not in chat_sessions:
        chat_sessions[user_id] = model.start_chat(history=[])
    
    try:
        # 關閉安全過濾以確保人設對話流暢
        safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
        response = chat_sessions[user_id].send_message(content, safety_settings=safety_settings)
        return response.text
    except Exception as e:
        print(f"Gemini Error: {e}")
        return "（言辰祭皺了下眉，似乎在處理公事，沒聽清妳說什麼。）"

def reply_to_line(reply_token, text):
    """將回覆送回 LINE (支援 \ 分隔多則訊息)"""
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    
    # 處理 \ 換行並拆分訊息 (最多5則)
    processed_text = text.replace('\\', '\n')
    raw_segments = processed_text.split('\n')
    segments = [s.strip() for s in raw_segments if s.strip()][:5]
    line_messages = [{"type": "text", "text": s} for s in segments]

    # 模擬打字延遲 (根據字數調整，最長 8 秒)
    delay = 1.5 + (len(text) * 0.15)
    time.sleep(min(delay, 8))

    data = {"replyToken": reply_token, "messages": line_messages}
    requests.post(url, headers=headers, json=data)

def process_bundle(reply_token, user_id):
    """處理打包後的文字訊息"""
    if user_id in message_bundles:
        combined_text = "；".join(message_bundles[user_id])
        del message_bundles[user_id]
        
        reply_text = get_gemini_reply(user_id, combined_text)
        if reply_text:
            reply_to_line(reply_token, reply_text)

# --- Webhook 主入口 ---

@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_json()
    if not body or "events" not in body or len(body["events"]) == 0:
        return "OK", 200

    event = body["events"][0]
    token = event.get("replyToken")
    user_id = event["source"].get("userId", "default_user")
    if not token: return "OK", 200

    msg = event.get("message", {})
    msg_type = msg.get("type")

    # 1. 文字訊息處理 (含 5 秒打包邏輯)
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

    # 2. 圖片訊息處理 (Gemini 視覺辨識)
    elif msg_type == "image":
        img_bytes = get_line_image_bytes(msg["id"])
        if img_bytes:
            content = [
                "這是紀瞳傳給你的照片，請看圖並用言辰祭的性格回覆她。",
                {"mime_type": "image/jpeg", "data": img_bytes}
            ]
            reply_text = get_gemini_reply(user_id, content)
            reply_to_line(token, reply_text)

    # 3. 貼圖訊息處理 (讀取貼圖關鍵字)
    elif msg_type == "sticker":
        keywords = ", ".join(msg.get("keywords", ["一個貼圖"]))
        reply_text = get_gemini_reply(user_id, f"（紀瞳傳送了一個代表「{keywords}」的貼圖，請依性格回應）")
        reply_to_line(token, reply_text)

    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


