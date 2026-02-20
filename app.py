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

# --- 模型設定：使用妳目前最穩定的模型 ---
TEXT_MODEL = "gemini-2.0-flash-exp" 

client = OpenAI(api_key=V1API_KEY, base_url=V1API_BASE_URL)

# --- 1. 資源與記憶管理 ---

def load_system_prompt():
    try:
        with open("character_prompt.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except:
        return "妳扮演言辰祭，一個冷淡但寵溺妻子的總經理。說話簡潔、使用 \\ 分隔。"

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

# --- 2. 核心邏輯 (含記憶清理與情緒感知) ---

def get_ai_reply(user_id, content):
    all_contexts = load_chat_context()
    tw_tz = pytz.timezone('Asia/Taipei')
    time_str = datetime.now(tw_tz).strftime("%Y-%m-%d %H:%M:%S")
    
    if user_id not in all_contexts:
        all_contexts[user_id] = [{"role": "system", "content": load_system_prompt()}]
    
    # 每次對話都載入最新人設
    all_contexts[user_id][0] = {"role": "system", "content": load_system_prompt()}
    all_contexts[user_id].append({"role": "user", "content": f"（{time_str}）\n{content}"})
    
    # 限制記憶：保留最近 6 則訊息，有效控制 Token
    history = [all_contexts[user_id][0]] + all_contexts[user_id][-6:]

    try:
        response = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=history,
            temperature=0.7,
            max_tokens=250
        )
        full_reply = response.choices[0].message.content
        
        # --- 重要：在存入記憶前，刪除回覆中的表情標籤 ---
        # 這樣下一次對話時，AI 不會被舊的標籤干擾，也能節省 Token
        clean_reply_for_memory = re.sub(r'\[表情_[^\]]+\]', '', full_reply).strip()
        all_contexts[user_id].append({"role": "assistant", "content": clean_reply_for_memory})
        save_chat_context(all_contexts)
        
        return full_reply
    except Exception as e:
        print(f"AI API Error: {e}")
        return "（言辰祭忙碌中...）"

# --- 3. LINE 回覆功能 (0.1 機率與自動解析) ---

def reply_to_line(reply_token, text):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    
    emoji_config = load_emojis()
    found_emoji = None
    
    # 解析 AI 回覆中是否包含 emojis.json 裡的標籤
    clean_text = text
    for tag, config in emoji_config.items():
        if tag in text:
            found_emoji = config
            break
    
    # 強制使用正則表達式清除所有 [表情_xxx] 文字，保證不出現在對話框
    clean_text = re.sub(r'\[表情_[^\]]+\]', '', clean_text).strip()

    processed_text = clean_text.replace('\\', '\n')
    segments = [s.strip() for s in processed_text.split('\n') if s.strip()][:4]
    
    if not segments:
        line_messages = [{"type": "text", "text": "..."}]
    else:
        line_messages = [{"type": "text", "text": s} for s in segments]

    # --- 0.1 機率控制邏輯 ---
    # 只有當 AI 決定要發表情貼 (found_emoji 不為空) 且 擲骰子成功 (10% 機率)
    if found_emoji and (random.random() < 0.7) :
        line_messages.append({
            "type": "text",
            "text": "$",
            "emojis": [{
                "index": 0, 
                "productId": found_emoji["productId"], 
                "emojiId": found_emoji["emojiId"]
            }]
        })

    # 模擬打字感，延遲回覆
    delay = min(1.5 + (len(clean_text) * 0.1), 7)
    time.sleep(delay)
    
    payload = {"replyToken": reply_token, "messages": line_messages}
    requests.post(url, headers=headers, json=payload)

# --- 4. Webhook 處理 (10秒打包) ---

def process_bundle(reply_token, user_id):
    if user_id in message_bundles:
        combined_text = "；".join(message_bundles[user_id])
        del message_bundles[user_id]
        reply_text = get_ai_reply(user_id, combined_text)
        reply_to_line(reply_token, reply_text)

@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_json()
    if not body or "events" not in body: return "OK", 200
    event = body["events"][0]
    token = event.get("replyToken")
    user_id = event["source"].get("userId", "default_user")
    
    if event.get("type") == "message" and event["message"].get("type") == "text":
        user_input = event["message"].get("text")
        if user_id not in message_bundles: message_bundles[user_id] = []
        message_bundles[user_id].append(user_input)
        if user_id in message_timers: message_timers[user_id].cancel()
        t = threading.Timer(10.0, process_bundle, args=[token, user_id])
        message_timers[user_id] = t
        t.start()
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))




