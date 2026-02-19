import os
import time
import base64
import json
import requests
import threading
from flask import Flask, request
from openai import OpenAI

app = Flask(__name__)

# --- 環境變數設定 ---
LINE_TOKEN = os.getenv("LINE_TOKEN")
V1API_KEY = os.getenv("V1API_KEY") 
V1API_BASE_URL = "https://vg.v1api.cc/v1"
TARGET_MODEL = "gemini-2.0-flash" # 使用你清單中有的模型

client = OpenAI(api_key=V1API_KEY, base_url=V1API_BASE_URL)

# --- 記憶與人設管理 ---

def load_system_prompt():
    """讀取 character_prompt.txt"""
    try:
        with open("character_prompt.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except:
        return "妳扮演言辰祭，一個冷淡但寵溺妻子的總經理。說話簡潔、使用 \\ 分隔。"

def load_chat_context():
    """從 .json 讀取所有人的記憶"""
    if os.path.exists('chat_contexts.json'):
        try:
            with open('chat_contexts.json', 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"讀取 JSON 失敗: {e}")
            return {}
    return {}

def save_chat_context(context):
    """將目前的記憶存入 .json"""
    try:
        with open('chat_contexts.json', 'w', encoding='utf-8') as f:
            json.dump(context, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"儲存 JSON 失敗: {e}")

# 訊息打包定時器（處理連發訊息）
message_bundles = {}
message_timers = {}

# --- AI 核心邏輯 (含上下文記憶) ---

def get_v1api_reply(user_id, current_content):
    """呼叫 AI 並管理記憶"""
    all_contexts = load_chat_context()
    
    # 1. 初始化或取得該使用者的歷史紀錄
    if user_id not in all_contexts:
        all_contexts[user_id] = [{"role": "system", "content": load_system_prompt()}]
    else:
        # 確保 system prompt 永遠是最新的
        all_contexts[user_id][0] = {"role": "system", "content": load_system_prompt()}

    # 2. 加入用戶目前的訊息
    all_contexts[user_id].append({"role": "user", "content": current_content})
    
    # 3. 記憶長度限制：保留 System Prompt + 最近 10 則對話 (避免 Token 爆量)
    history = [all_contexts[user_id][0]] + all_contexts[user_id][-10:]

    try:
        response = client.chat.completions.create(
            model=TARGET_MODEL,
            messages=history,
            temperature=0.7
        )
        reply = response.choices[0].message.content
        
        # 4. 儲存 AI 的回覆到記憶
        all_contexts[user_id].append({"role": "assistant", "content": reply})
        
        # 5. 持久化存入 json 檔案
        save_chat_context(all_contexts)
        
        return reply
    except Exception as e:
        print(f"API Error: {e}")
        if "429" in str(e):
            return "（言辰祭淡淡地看著文件：『妳今天話太多了，安靜一會。』）"
        return "（言辰祭似乎在忙，沒聽清妳說什麼。）"

# --- LINE 互動功能 ---

def reply_to_line(reply_token, text):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    
    # 處理分隔符號 \
    processed_text = text.replace('\\', '\n')
    segments = [s.strip() for s in processed_text.split('\n') if s.strip()][:5]
    line_messages = [{"type": "text", "text": s} for s in segments]

    # 模擬打字延遲 (讓機器人更像人)
    time.sleep(min(1.0 + (len(text) * 0.1), 6))
    requests.post(url, headers=headers, json={"replyToken": reply_token, "messages": line_messages})

def process_bundle(reply_token, user_id):
    if user_id in message_bundles:
        combined_text = "；".join(message_bundles[user_id])
        del message_bundles[user_id]
        reply_text = get_v1api_reply(user_id, combined_text)
        reply_to_line(reply_token, reply_text)

@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_json()
    if not body or "events" not in body or not body["events"]:
        return "OK", 200

    event = body["events"][0]
    token = event.get("replyToken")
    user_id = event["source"].get("userId", "default_user")
    if not token: return "OK", 200

    msg = event.get("message", {})
    msg_type = msg.get("type")

    # 處理文字訊息 (打包邏輯)
    if msg_type == "text":
        user_input = msg.get("text")
        if user_id not in message_bundles:
            message_bundles[user_id] = []
        message_bundles[user_id].append(user_input)
        
        if user_id in message_timers:
            message_timers[user_id].cancel()
        
        # 5秒內沒新訊息就打包發送
        t = threading.Timer(5.0, process_bundle, args=[token, user_id])
        message_timers[user_id] = t
        t.start()

    # 處理圖片訊息
    elif msg_type == "image":
        url = f"https://api-data.line.me/v2/bot/message/{msg['id']}/content"
        r = requests.get(url, headers={"Authorization": f"Bearer {LINE_TOKEN}"})
        if r.status_code == 200:
            img_b64 = base64.b64encode(r.content).decode('utf-8')
            content = [
                {"type": "text", "text": "紀瞳傳給你的照片。"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
            ]
            reply_text = get_v1api_reply(user_id, content)
            reply_to_line(token, reply_text)

    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
