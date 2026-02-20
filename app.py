import os
import time
import base64
import json
import requests
import threading
from flask import Flask, request
from openai import OpenAI
from datetime import datetime
import pytz

app = Flask(__name__)

# --- 環境變數 ---
LINE_TOKEN = os.getenv("LINE_TOKEN")
V1API_KEY = os.getenv("V1API_KEY") 
V1API_BASE_URL = "https://vg.v1api.cc/v1"

# --- 模型設定 ---
TEXT_MODEL = "gemini-2.5-pro-06-05"     # 文字對話用 Gemini
VISION_MODEL = "gpt-4o"                 # 圖片辨識用 GPT-4o

client = OpenAI(api_key=V1API_KEY, base_url=V1API_BASE_URL)

# --- 1. 記憶與人設管理 ---

def load_system_prompt():
    try:
        with open("character_prompt.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except:
        return "妳扮演言辰祭，一個冷淡但寵溺女友的大學生。說話簡潔、使用 \\ 分隔。"

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

# 訊息打包定時器
message_bundles = {}
message_timers = {}

# --- 2. 核心邏輯 (加入時間感知與記憶限制) ---

def get_ai_reply(user_id, content, is_image=False):
    all_contexts = load_chat_context()
    
    # --- 加入時間感知 ---
    tw_tz = pytz.timezone('Asia/Taipei')
    now = datetime.now(tw_tz)
    time_str = now.strftime("%Y-%m-%d %H:%M:%S")
    
    if user_id not in all_contexts:
        all_contexts[user_id] = [{"role": "system", "content": load_system_prompt()}]
    
    # 確保 system prompt 隨時更新
    all_contexts[user_id][0] = {"role": "system", "content": load_system_prompt()}

    # --- 處理輸入內容 ---
    if is_image:
        try:
            vision_res = client.chat.completions.create(
                model=VISION_MODEL,
                messages=[{"role": "user", "content": content}]
            )
            description = vision_res.choices[0].message.content
            final_content = f"（紀瞳在 {time_str} 傳送了照片，內容是：{description}。請以此回應）"
        except:
            final_content = f"（紀瞳在 {time_str} 傳送了一張照片）"
    else:
        # 在文字訊息前偷偷塞入時間，讓言辰祭知道現在幾點
        final_content = f"（現在時間：{time_str}）\n{content}"

    # 加入新訊息
    all_contexts[user_id].append({"role": "user", "content": final_content})
    
    # --- 嚴格限制記憶：只保留 System Prompt + 最近 10 則對話 ---
    # 這能有效將 Token 消耗控制在合理範圍
    history = [all_contexts[user_id][0]] + all_contexts[user_id][-10:]

    try:
        response = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=history,
            temperature=0.7,
            max_tokens=250  # 限制回覆長度，節省 Token
        )
        reply = response.choices[0].message.content
        
        # 儲存回覆
        all_contexts[user_id].append({"role": "assistant", "content": reply})
        save_chat_context(all_contexts)
        return reply
    except Exception as e:
        print(f"API Error: {e}")
        return "（言辰祭似乎在忙...）"

# --- 3. LINE 功能 ---

def reply_to_line(reply_token, text):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    processed_text = text.replace('\\', '\n')
    segments = [s.strip() for s in processed_text.split('\n') if s.strip()][:5]
    line_messages = [{"type": "text", "text": s} for s in segments]
    
    # 模擬打字延遲 (依字數變動)
    delay = min(1.5 + (len(text) * 0.1), 7)
    time.sleep(delay)
    
    requests.post(url, headers=headers, json={"replyToken": reply_token, "messages": line_messages})

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
    if not token: return "OK", 200

    msg = event.get("message", {})
    msg_type = msg.get("type")

    if msg_type == "text":
        user_input = msg.get("text")
        if user_id not in message_bundles:
            message_bundles[user_id] = []
        message_bundles[user_id].append(user_input)
        
        if user_id in message_timers:
            message_timers[user_id].cancel()
        
        # 打字不快，設定 10 秒等待打包
        t = threading.Timer(10.0, process_bundle, args=[token, user_id])
        message_timers[user_id] = t
        t.start()
        
    elif msg_type == "image":
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
