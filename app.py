import os
import time
import base64
import json
import requests
import threading
import random
import re
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
TEXT_MODEL = "gemini-2.0-flash" 
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
                data = json.load(f)
                return data
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

# --- 2. 核心邏輯 (移除記憶中的代碼以省 Token) ---

def get_ai_reply(user_id, content, is_image=False):
    all_contexts = load_chat_context()
    tw_tz = pytz.timezone('Asia/Taipei')
    time_str = datetime.now(tw_tz).strftime("%Y-%m-%d %H:%M:%S")
    
    if user_id not in all_contexts:
        all_contexts[user_id] = [{"role": "system", "content": load_system_prompt()}]
    
    all_contexts[user_id][0] = {"role": "system", "content": load_system_prompt()}

    if is_image:
        final_content = f"（{time_str}，紀瞳傳照片）\n{content}"
    else:
        final_content = f"（{time_str}）\n{content}"

    all_contexts[user_id].append({"role": "user", "content": final_content})
    history = [all_contexts[user_id][0]] + all_contexts[user_id][-6:]

    try:
        response = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=history,
            temperature=0.7,
            max_tokens=200
        )
        full_reply = response.choices[0].message.content
        
        # 存入記憶前刪除標籤，確保 Token 不會爆炸
        clean_reply = re.sub(r'\[表情_[^\]]+\]', '', full_reply).strip()
        all_contexts[user_id].append({"role": "assistant", "content": clean_reply})
        save_chat_context(all_contexts)
        
        return full_reply
    except:
        return "（忙碌中...）"

# --- 3. LINE 回覆功能 (測試版：100% 發送表情貼) ---

def reply_to_line(reply_token, text):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    
    emoji_config = load_emojis()
    
    # 測試版邏輯：不管 AI 有沒有寫代碼，都從 pool 裡抽一個出來
    if emoji_config:
        chosen_tag = random.choice(list(emoji_config.keys()))
        found_emoji = emoji_config[chosen_tag]
    else:
        found_emoji = None

    # 強制清除文字中所有 [表情_xxx] 格式，避免殘留
    clean_text = re.sub(r'\[表情_[^\]]+\]', '', text).strip()

    processed_text = clean_text.replace('\\', '\n')
    segments = [s.strip() for s in processed_text.split('\n') if s.strip()][:4]
    
    if not segments:
        line_messages = [{"type": "text", "text": "..."}]
    else:
        line_messages = [{"type": "text", "text": s} for s in segments]

    # --- 100% 發送表情貼 ---
    if found_emoji:
        line_messages.append({
            "type": "text",
            "text": "$",
            "emojis": [{
                "index": 0,
                "productId": found_emoji["productId"],
                "emojiId": found_emoji["emojiId"]
            }]
        })

    time.sleep(1) # 測試時縮短延遲
    
    payload = {"replyToken": reply_token, "messages": line_messages}
    res = requests.post(url, headers=headers, json=payload)
    print(f"LINE API Response: {res.status_code}, {res.text}") # 在 Logs 裡看報錯

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
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
