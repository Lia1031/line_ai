import os
import time
import base64
import json
import requests
import threading
from flask import Flask, request
from openai import OpenAI

app = Flask(__name__)

# --- 環境變數 ---
LINE_TOKEN = os.getenv("LINE_TOKEN")
V1API_KEY = os.getenv("V1API_KEY") 
V1API_BASE_URL = "https://vg.v1api.cc/v1"

# --- 模型設定 ---
TEXT_MODEL = "gemini-2.5-pro-06-05"     # 文字對話用 Gemini
VISION_MODEL = "gpt-4o"       # 圖片辨識用 GPT-4o-mini (更便宜且精準)

client = OpenAI(api_key=V1API_KEY, base_url=V1API_BASE_URL)

# --- 記憶與人設管理 ---

def load_system_prompt():
    try:
        with open("character_prompt.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except:
        return "妳扮演言辰祭，一個冷淡但寵溺妻子的總經理。說話簡潔、使用 \\ 分隔。"

def load_chat_context():
    if os.path.exists('chat_contexts.json'):
        try:
            with open('chat_contexts.json', 'r', encoding='utf-8') as f:
                return json.load(f)
        except: return {}
    return {}

def save_chat_context(context):
    with open('chat_contexts.json', 'w', encoding='utf-8') as f:
        json.dump(context, f, ensure_ascii=False, indent=4)

# 訊息打包定時器
message_bundles = {}
message_timers = {}

# --- 核心邏輯 ---

def get_ai_reply(user_id, content, is_image=False):
    """
    如果是圖片，先用 GPT-4o-mini 轉成文字描述
    如果是文字，直接用 Gemini 回覆
    """
    all_contexts = load_chat_context()
    if user_id not in all_contexts:
        all_contexts[user_id] = [{"role": "system", "content": load_system_prompt()}]
    
    # 確保 system prompt 隨時更新
    all_contexts[user_id][0] = {"role": "system", "content": load_system_prompt()}

    # --- 處理圖片辨識 (GPT-4o-mini) ---
    if is_image:
        try:
            # 請求 GPT-4o-mini 幫忙看圖
            vision_res = client.chat.completions.create(
                model=VISION_MODEL,
                messages=[{"role": "user", "content": content}]
            )
            description = vision_res.choices[0].message.content
            # 將圖片描述轉化為文字情境給 Gemini
            final_content = f"（紀瞳傳送了一張照片，內容是：{description}。請以言辰祭的性格回應）"
        except:
            final_content = "（紀瞳傳送了一張照片）"
    else:
        final_content = content

    # --- 處理文字對話 (Gemini) ---
    all_contexts[user_id].append({"role": "user", "content": final_content})
    history = [all_contexts[user_id][0]] + all_contexts[user_id][-12:]

    try:
        response = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=history,
            temperature=0.7
        )
        reply = response.choices[0].message.content
        all_contexts[user_id].append({"role": "assistant", "content": reply})
        save_chat_context(all_contexts)
        return reply
    except Exception as e:
        print(f"API Error: {e}")
        return "（言辰祭似乎在忙...）"

# --- LINE 功能 ---

def reply_to_line(reply_token, text):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    processed_text = text.replace('\\', '\n')
    segments = [s.strip() for s in processed_text.split('\n') if s.strip()][:5]
    line_messages = [{"type": "text", "text": s} for s in segments]
    time.sleep(min(1.5 + (len(text) * 0.1), 7))
    requests.post(url, headers=headers, json={"replyToken": reply_token, "messages": line_messages})

def process_bundle(reply_token, user_id):
    if user_id in message_bundles:
        # 將妳 10 秒內傳的所有訊息用「；」串起來
        combined_text = "；".join(message_bundles[user_id])
        del message_bundles[user_id]
        
        # 呼叫 AI (Gemini 文字對話)
        reply_text = get_ai_reply(user_id, combined_text)
        reply_to_line(reply_token, reply_text)

@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_json()
    if not body or "events" not in body: return "OK", 200
    event = body["events"][0]
    token = event.get("replyToken")
    user_id = event["source"].get("userId", "default_user")
    if not token: return "OK", 200

    msg = event.get("message", {})
    msg_type = msg.get("type")

    if msg_type == "text":
        user_input = msg.get("text")
        if user_id not in message_bundles:
            message_bundles[user_id] = []
        message_bundles[user_id].append(user_input)
        
        # 每傳一則新訊息，就取消舊計時器，重新數 10 秒
        if user_id in message_timers:
            message_timers[user_id].cancel()
        
        # 設定 10 秒等待
        t = threading.Timer(10.0, process_bundle, args=[token, user_id])
        message_timers[user_id] = t
        t.start()
        
    elif msg_type == "image":
        # 下載照片交給 GPT-4o-mini 辨識
        url = f"https://api-data.line.me/v2/bot/message/{msg['id']}/content"
        r = requests.get(url, headers={"Authorization": f"Bearer {LINE_TOKEN}"})
        if r.status_code == 200:
            img_b64 = base64.b64encode(r.content).decode('utf-8')
            content = [
                {"type": "text", "text": "請簡短描述這張照片的內容。"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
            ]
            reply_text = get_ai_reply(user_id, content, is_image=True)
            reply_to_line(token, reply_text)

    elif msg_type == "sticker":
        kw = ", ".join(msg.get("keywords", ["一個貼圖"]))
        reply_text = get_ai_reply(user_id, f"（紀瞳傳送了代表「{kw}」的貼圖）")
        reply_to_line(token, reply_text)

    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

