import os
import time
import json
import requests
import threading
import random
import re
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

# --- 模型設定 ---
TEXT_MODEL = "gemini-2.5-pro-06-05"
client = OpenAI(api_key=V1API_KEY, base_url=V1API_BASE_URL)

# --- 1. Google Sheets 權限設定 ---
# --- 1. Google Sheets 權限設定 (修改版) ---
def get_sheet():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        
        # 優先從環境變數讀取 JSON 內容
        creds_json = os.getenv("GOOGLE_CREDS")
        
        if creds_json:
            # 解析環境變數中的 JSON 字串
            info = json.loads(creds_json)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(info, scope)
        else:
            # 如果變數不存在，才找實體檔案 (備用)
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
        return "妳扮演言辰祭。外在冷淡寡言，面對紀瞳會變溫柔。說話簡潔，不使用表情符號。"
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
temp_logs = []  # 用於快取對話以供稍後摘要

# --- 3. 核心邏輯：AI 回覆 ---
def get_ai_reply(user_id, content):
    all_contexts = load_chat_context()
    tw_tz = pytz.timezone('Asia/Taipei')
    time_str = datetime.now(tw_tz).strftime("%m/%d %H:%M")
    
    if user_id not in all_contexts:
        all_contexts[user_id] = [{"role": "system", "content": load_system_prompt()}]
    
    # 每次更新 system prompt 確保性格最新
    all_contexts[user_id][0] = {"role": "system", "content": load_system_prompt()}
    all_contexts[user_id].append({"role": "user", "content": f"[Time: {time_str}]\n{content}"})
    
    history = [all_contexts[user_id][0]] + all_contexts[user_id][-6:]

    try:
        response = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=history,
            temperature=0.7
        )
        full_reply = response.choices[0].message.content
        
        # 將對話記錄暫存，待會統整存入 Sheets
        temp_logs.append(f"紀瞳: {content} | 言辰祭: {full_reply}")
        
        all_contexts[user_id].append({"role": "assistant", "content": full_reply})
        save_chat_context(all_contexts)
        return full_reply 
    except Exception as e:
        print(f"AI API Error: {e}")
        return "...（在忙，沒看手機）"

# --- 4. 自動摘要任務 (每 30 分鐘檢查一次) ---
def summarize_and_save_task():
    global temp_logs
    if temp_logs:
        try:
            current_logs = "\n".join(temp_logs)
            temp_logs = [] # 立即清空，避免重複寫入
            
            summary_prompt = [
                {"role": "system", "content": "你是言辰祭的內心。請根據以下對話摘要成一段 100 字內的內容統整，作為之後 Threads 的貼文靈感。"},
                {"role": "user", "content": current_logs}
            ]
            
            response = client.chat.completions.create(model=TEXT_MODEL, messages=summary_prompt)
            summary = response.choices[0].message.content.strip()
            
            sheet = get_sheet()
            if sheet:
                tw_tz = pytz.timezone('Asia/Taipei')
                time_now = datetime.now(tw_tz).strftime("%Y-%m-%d %H:%M")
                sheet.append_row([time_now, summary, "Automatic Summary"])
                print(f"[Sheet] 已存入摘要: {summary}")
        except Exception as e:
            print(f"[Sheet] 儲存失敗: {e}")
            
    # 設定下次執行時間 (600秒 = 10分鐘)
    threading.Timer(1800, summarize_and_save_task).start()

# 啟動背景任務
threading.Timer(1800, summarize_and_save_task).start()

# --- 5. LINE 回覆功能 ---
def reply_to_line(reply_token, text, raw_input=""):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    
    # 清理標籤與符號
    display_text = re.sub(r'\[表情_[^\]]+\]', '', text)
    display_text = re.sub(r'[\(\[][0-9\/\-\s:]+[\)\]]', '', display_text).strip()
    processed_text = display_text.replace('\\', '\n')
    
    segments = [s.strip() for s in processed_text.split('\n') if s.strip()][:4]
    line_messages = [{"type": "text", "text": s} for s in segments] if segments else [{"type": "text", "text": "..."}]

    payload = {"replyToken": reply_token, "messages": line_messages[:5]}
    requests.post(url, headers=headers, json=payload)

# --- 6. Webhook 處理 (7秒防抖打包) ---
def process_bundle(reply_token, user_id):
    if user_id in message_bundles and message_bundles[user_id]:
        combined_text = "；".join(message_bundles[user_id])
        message_bundles[user_id] = [] # 清空緩存
        
        reply_text = get_ai_reply(user_id, combined_text)
        reply_to_line(reply_token, reply_text, raw_input=combined_text)

@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_json()
    if not body or "events" not in body: return "OK", 200
    
    event = body["events"][0]
    if event.get("type") == "message" and event["message"].get("type") == "text":
        reply_token = event.get("replyToken")
        user_id = event["source"].get("userId", "default_user")
        user_input = event["message"].get("text")
        
        if user_id not in message_bundles: message_bundles[user_id] = []
        message_bundles[user_id].append(user_input)
        
        # 防抖邏輯：如果 7 秒內有新訊息，就取消舊的計時器
        if user_id in message_timers:
            message_timers[user_id].cancel()
        
        # 重新設定 7 秒計時器
        t = threading.Timer(7.0, process_bundle, args=[reply_token, user_id])
        message_timers[user_id] = t
        t.start()
        
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))





