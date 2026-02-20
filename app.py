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

app = Flask(__name__)

# --- 環境變數 ---
LINE_TOKEN = os.getenv("LINE_TOKEN")
V1API_KEY = os.getenv("V1API_KEY")
V1API_BASE_URL = "https://vg.v1api.cc/v1"

# --- 模型設定 ---
TEXT_MODEL = "gemini-2.0-flash-exp"

client = OpenAI(api_key=V1API_KEY, base_url=V1API_BASE_URL)

# --- 1. 資源與記憶管理 ---

def load_system_prompt():
    try:
        with open("character_prompt.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except:
        return "妳扮演言辰祭。說話簡潔、使用 \\ 分隔。"

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

def get_ai_reply(user_id, content):
    all_contexts = load_chat_context()
    tw_tz = pytz.timezone('Asia/Taipei')
    time_str = datetime.now(tw_tz).strftime("%m/%d %H:%M")
    
    if user_id not in all_contexts:
        all_contexts[user_id] = [{"role": "system", "content": load_system_prompt()}]
    
    all_contexts[user_id][0] = {"role": "system", "content": load_system_prompt()}
    all_contexts[user_id].append({"role": "user", "content": f"[Time: {time_str}]\n{content}"})
    
    # 限制記憶：保留最近 6 則
    history = [all_contexts[user_id][0]] + all_contexts[user_id][-6:]

    try:
        response = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=history,
            temperature=0.7,
            max_tokens=250
        )
        full_reply = response.choices[0].message.content
        
        # 清理記憶中的標籤與時間
        clean_reply = re.sub(r'\[表情_[^\]]+\]', '', full_reply)
        clean_reply = re.sub(r'[\(\[][0-9\/\-\s:]+[\)\]]', '', clean_reply)
        clean_reply = clean_reply.strip()

        all_contexts[user_id].append({"role": "assistant", "content": clean_reply})
        save_chat_context(all_contexts)
        
        return full_reply 
    except Exception as e:
        print(f"AI API Error: {e}")
        return "（低頭滑著手機，沒注意到妳說話）"

# --- 3. LINE 回覆功能 ---

def reply_to_line(reply_token, text, raw_input=""):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    
    emoji_config = load_emojis()
    found_emoji = None
    
    for tag, config in emoji_config.items():
        if tag in text:
            found_emoji = config
            break
    
    # 顯示文字清理
    display_text = re.sub(r'\[表情_[^\]]+\]', '', text)
    display_text = re.sub(r'[\(\[][0-9\/\-\s:]+[\)\]]', '', display_text)
    display_text = display_text.strip()

    processed_text = display_text.replace('\\', '\n')
    segments = [s.strip() for s in processed_text.split('\n') if s.strip()][:4]
    
    line_messages = [{"type": "text", "text": s} for s in segments] if segments else [{"type": "text", "text": "..."}]

    # 表情貼邏輯
    user_asked = "表情貼" in raw_input
    if found_emoji and (random.random() < 0.3 or user_asked):
        line_messages.append({
            "type": "text",
            "text": "$",
            "emojis": [{"index": 0, "productId": found_emoji["productId"], "emojiId
