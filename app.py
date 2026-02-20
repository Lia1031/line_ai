import os
import time
import json
import requests
import threading
import random
import re
import base64
from datetime import datetime
import pytz
from flask import Flask, request
from openai import OpenAI
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# --- 環境變數 ---
LINE_TOKEN = os.getenv("LINE_TOKEN")
V1API_KEY = os.getenv("V1API_KEY")
V1API_BASE_URL = "https://vg.v1api.cc/v1"
SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME")
MY_LINE_USER_ID = os.getenv("MY_LINE_USER_ID") 

# --- 模型設定 ---
# 使用具備多模態能力的 2.5 Pro
TEXT_MODEL = "gemini-2.5-pro-06-05"
client = OpenAI(api_key=V1API_KEY, base_url=V1API_BASE_URL)

# --- 1. Google Sheets 權限設定 ---
def get_sheet():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_json = os.getenv("GOOGLE_CREDS")
        if creds_json:
            info = json.loads(creds_json)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(info, scope)
        else:
            creds = ServiceAccountCredentials.from_json_keyfile_name("creds.json", scope)
        gc = gspread.authorize(creds)
        return gc.open(SHEET_NAME).sheet1
    except Exception as e:
        print(f"Google Sheet 連線失敗: {e}")
        return None

# --- 2. 資源管理 ---
def load_system_prompt():
    try:
        if os.path.exists("character_prompt.txt"):
            with open("character_prompt.txt", "r", encoding="utf-8") as f:
                return f.read().strip()
        return "妳扮演言辰祭。冷淡寡言，對紀瞳溫柔。說話簡潔，不使用驚嘆號。"
    except:
        return "扮演言辰祭，說話簡潔冷淡。"

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
temp_logs = []

# --- 3. 核心發送邏輯 (支援表情貼) ---
def send_line_message(target, text, is_reply=True):
    url = "https://api.line.me/v2/bot/message/reply" if is_reply else "https://api.line.me/v2/bot/message/push"
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    
    emoji_config = load_emojis()
    found_emoji = None
    for tag, config in emoji_config.items():
        if tag in text:
            found_emoji = config
            break

    display_text = re.sub(r'\[表情_[^\]]+\]', '', text)
    display_text = re.sub(r'[\(\[][0-9\/\-\s:]+[\)\]]', '', display_text).strip()
    processed_text = display_text.replace('\\', '\n')
    
    segments = [s.strip() for s in processed_text.split('\n') if s.strip()][:4]
    line_messages = [{"type": "text", "text": s} for s in segments] if segments else [{"type": "text", "text": "..."}]
    
    if found_emoji and random.random() < 0.7:
        line_messages.append({
            "type": "text",
            "text": "$",
            "emojis": [{"index": 0, "productId": found_emoji["productId"], "emojiId": found_emoji["emojiId"]}]
        })
    
    payload = {"replyToken": target, "messages": line_messages} if is_reply else {"to": target, "messages": line_messages}
    requests.post(url, headers=headers, json=payload)

# --- 4. 圖片與回覆邏輯 (含 Vision) ---
def get_ai_reply(user_id, content, is_image=False):
    all_contexts = load_chat_context()
    tw_tz = pytz.timezone('Asia/Taipei')
    time_str = datetime.now(tw_tz).strftime("%m/%d %H:%M")
    
    if user_id not in all_contexts:
        all_contexts[user_id] = [{"role": "system", "content": load_system_prompt()}]
    
    all_contexts[user_id][0] = {"role": "system", "content": load_system_prompt()}
    
    # 建立訊息內容
    if is_image:
        # 圖片模式下的 content 為包含 base64 的 list
        user_msg = {"role": "user", "content": content}
    else:
        user_msg = {"role": "user", "content": f"[Time: {time_str}]\n{content}"}

    all_contexts[user_id].append(user_msg)
    history = [all_contexts[user_id][0]] + all_contexts[user_id][-6:]

    try:
        response = client.chat.completions.create(model=TEXT_MODEL, messages=history, temperature=0.7)
        full_reply = response.choices[0].message.content
        temp_logs.append(f"紀瞳: {'[圖片訊息]' if is_image else content} | 言辰祭: {full_reply}")
        
        # 存入記憶前清理 (Vision 的內容不建議存太大，這裡存簡短描述即可)
        save_msg = "[傳送了一張照片]" if is_image else content
        all_contexts[user_id][-1] = {"role": "user", "content": f"[Time: {time_str}]\n{save_msg}"}
        all_contexts[user_id].append({"role": "assistant", "content": full_reply})
        save_chat_context(all_contexts)
        return full_reply 
    except Exception as e:
        print(f"AI API Error: {e}")
        return "...（沒看手機）"

# --- 5. 自動化任務 ---
def auto_interact_task():
    if MY_LINE_USER_ID:
        try:
            prompt = [{"role": "system", "content": load_system_prompt()}, {"role": "user", "content": "（主動傳送關心給紀瞳）"}]
            response = client.chat.completions.create(model=TEXT_MODEL, messages=prompt, temperature=0.8)
            send_line_message(MY_LINE_USER_ID, response.choices[0].message.content, is_reply=False)
        except Exception as e:
            print(f"[AutoPush] 失敗: {e}")
    threading.Timer(10800, auto_interact_task).start()

def summarize_and_save_task():
    global temp_logs
    if temp_logs:
        try:
            current_logs = "\n".join(temp_logs)
            temp_logs = []
            summary_prompt = [{"role": "system", "content": "摘要對話。"}, {"role": "user", "content": current_logs}]
            response = client.chat.completions.create(model=TEXT_MODEL, messages=summary_prompt)
            summary = response.choices[0].message.content.strip()
            sheet = get_sheet()
            if sheet:
                tw_tz = pytz.timezone('Asia/Taipei')
                time_now = datetime.now(tw_tz).strftime("%Y-%m-%d %H:%M")
                sheet.append_row([time_now, summary, "Automatic Summary"])
        except Exception as e:
            print(f"[Sheet] 失敗: {e}")
    threading.Timer(1800, summarize_and_save_task).start()

threading.Timer(10800, auto_interact_task).start()
threading.Timer(1800, summarize_and_save_task).start()

# --- 6. Webhook ---
def process_bundle(reply_token, user_id):
    if user_id in message_bundles and message_bundles[user_id]:
        combined_text = "；".join(message_bundles[user_id])
        message_bundles[user_id] = []
        reply_text = get_ai_reply(user_id, combined_text)
        send_line_message(reply_token, reply_text, is_reply=True)

@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_json()
    if not body or "events" not in body: return "OK", 200
    event = body["events"][0]
    reply_token = event.get("replyToken")
    user_id = event["source"].get("userId", "default_user")
    msg = event.get("message", {})
    msg_type = msg.get("type")

    if not reply_token: return "OK", 200

    if msg_type == "text":
        if user_id not in message_bundles: message_bundles[user_id] = []
        message_bundles[user_id].append(msg.get("text"))
        if user_id in message_timers: message_timers[user_id].cancel()
        t = threading.Timer(7.0, process_bundle, args=[reply_token, user_id])
        message_timers[user_id] = t
        t.start()

    elif msg_type == "sticker":
        keywords = msg.get("keywords", ["貼圖"])
        user_input = f"（紀瞳傳送了貼圖：{', '.join(keywords)}）"
        if user_id not in message_bundles: message_bundles[user_id] = []
        message_bundles[user_id].append(user_input)
        if user_id in message_timers: message_timers[user_id].cancel()
        t = threading.Timer(7.0, process_bundle, args=[reply_token, user_id])
        message_timers[user_id] = t
        t.start()

    elif msg_type == "image":
        # 下載圖片並轉 Base64
        msg_id = msg["id"]
        img_url = f"https://api-data.line.me/v2/bot/message/{msg_id}/content"
        headers = {"Authorization": f"Bearer {LINE_TOKEN}"}
        img_res = requests.get(img_url, headers=headers)
        
        if img_res.status_code == 200:
            base64_img = base64.b64encode(img_res.content).decode('utf-8')
            content_list = [
                {"type": "text", "text": "這是紀瞳傳給你的照片，請依照你的人設給予回覆。"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}}
            ]
            reply_text = get_ai_reply(user_id, content_list, is_image=True)
            send_line_message(reply_token, reply_text, is_reply=True)

    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
