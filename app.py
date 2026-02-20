import os
import time
import base64
import json
import requests
import threading
import random
from datetime import datetime
import pytz
from flask import Flask, request
from openai import OpenAI

app = Flask(__name__)

# --- 環境變數 ---
LINE_TOKEN = os.getenv("LINE_TOKEN")
V1API_KEY = os.getenv("V1API_KEY") 
V1API_BASE_URL = "https://vg.v1api.cc/v1"

# --- 模型設定 ---
TEXT_MODEL = "gemini-2.5-pro-06-05"
VISION_MODEL = "gpt-4o"

client = OpenAI(api_key=V1API_KEY, base_url=V1API_BASE_URL)

# --- 1. 資源與記憶管理 ---

def load_system_prompt():
    try:
        with open("character_prompt.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except:
        return "妳扮演言辰祭，說話簡潔、使用 \\ 分隔。"

def load_emojis():
    if os.path.exists('emojis.json'):
        try:
            with open('emojis.json', 'r', encoding='utf-8') as f:
                return json.load(f)
        except: return {}
    return {}

def load_chat_context():
    if os.path.exists('chat_contexts.json'):
        try:
            with open('chat_contexts.json', 'r', encoding='utf-8') as f:
                return json.load(f)
        except: return {}
    return {}

def save_chat_context(context):
    try:
        with open('chat_contexts.json', 'w', encoding='utf-8') as f:
            json.dump(context, f, ensure_ascii=False, indent=4)
    except: pass

message_bundles = {}
message_timers = {}

# --- 2. 核心邏輯 ---

def get_ai_reply(user_id, content, is_image=False):
    all_contexts = load_chat_context()
    tw_tz = pytz.timezone('Asia/Taipei')
    time_str = datetime.now(tw_tz).strftime("%Y-%m-%d %H:%M:%S")
    
    if user_id not in all_contexts:
        all_contexts[user_id] = [{"role": "system", "content": load_system_prompt()}]
    
    all_contexts[user_id][0] = {"role": "system", "content": load_system_prompt()}

    if is_image:
        final_content = f"（現在時間：{time_str}，紀瞳傳送了照片，請回應內容並視情況使用表情貼代碼）\n{content}"
    else:
        final_content = f"（現在時間：{time_str}）\n{content}"

    all_contexts[user_id].append({"role": "user", "content": final_content})
    history = [all_contexts[user_id][0]] + all_contexts[user_id][-10:]

    try:
        response = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=history,
            temperature=0.7,
            max_tokens=300
        )
        reply = response.choices[0].message.content
        all_contexts[user_id].append({"role": "assistant", "content": reply})
        save_chat_context(all_contexts)
        return reply
    except Exception as e:
        print(f"API Error: {e}")
        return "（言辰祭似乎在處理公務...）"

# --- 3. LINE 回覆功能 (加入 0.1 機率控制) ---

def reply_to_line(reply_token, text):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    
    emoji_config = load_emojis()
    found_emoji = None
    clean_text = text

    # 1. 強化解析：先檢查有沒有代碼，不管在哪個位置
    for tag, config in emoji_config.items():
        if tag in text:
            found_emoji = config
            # 徹底移除所有發現的代碼標籤，避免出現在 LINE 上
            clean_text = clean_text.replace(tag, "").strip()
    
    # 如果 AI 的代碼寫法不標準，我們再用正則表達式做最後掃描（確保 [表情_xxx] 消失）
    import re
    clean_text = re.sub(r'\[表情_[^\]]+\]', '', clean_text).strip()

    # 2. 處理文字換行
    processed_text = clean_text.replace('\\', '\n')
    segments = [s.strip() for s in processed_text.split('\n') if s.strip()][:4]
    
    # 如果文字被刪光了（只有代碼），至少留個空格避免報錯
    if not segments:
        line_messages = [{"type": "text", "text": " "}]
    else:
        line_messages = [{"type": "text", "text": s} for s in segments]

    # 3. --- 0.1 機率控制 ---
    # 只有 AI 有寫代碼，且抽中 10% 機率時，才發送表情貼
    if found_emoji and (random.random() < 1.0):
        line_messages.append({
            "type": "text",
            "text": "$",
            "emojis": [{"index": 0, "productId": found_emoji["productId"], "emojiId": found_emoji["emojiId"]}]
        })
    # 如果妳想測試功能是否正常，可以先暫時把 < 0.1 改成 < 1.0，成功後再改回 0.1

    delay = min(1.5 + (len(clean_text) * 0.1), 7)
    time.sleep(delay)
    
    payload = {"replyToken": reply_token, "messages": line_messages}
    requests.post(url, headers=headers, json=payload)

# --- 4. Webhook 處理 ---

def process_bundle(reply_token, user_id):
    if user_id in message_bundles:
        combined_text = "；".join(message_bundles[user_id])
        del message_bundles[user_id]
        reply_to_line(reply_token, get_ai_reply(user_id, combined_text))

@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_json()
    if not body or "events" not in body: return "OK", 200
    event = body["events"][0]
    token = event.get("replyToken")
    user_id = event["source"].get("userId", "default_user")
    if not token: return "OK", 200

    msg = event.get("message", {})
    if msg.get("type") == "text":
        user_input = msg.get("text")
        if user_id not in message_bundles: message_bundles[user_id] = []
        message_bundles[user_id].append(user_input)
        if user_id in message_timers: message_timers[user_id].cancel()
        t = threading.Timer(10.0, process_bundle, args=[token, user_id])
        message_timers[user_id] = t
        t.start()
        
    elif msg.get("type") == "image":
        url = f"https://api-data.line.me/v2/bot/message/{msg['id']}/content"
        r = requests.get(url, headers={"Authorization": f"Bearer {LINE_TOKEN}"})
        if r.status_code == 200:
            img_b64 = base64.b64encode(r.content).decode('utf-8')
            image_content = [
                {"type": "text", "text": "請簡短描述照片內容，並視情況使用表情貼代碼。"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
            ]
            reply_text = get_ai_reply(user_id, image_content, is_image=True)
            reply_to_line(token, reply_text)

    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

