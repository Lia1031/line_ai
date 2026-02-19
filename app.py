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
model = genai.GenerativeModel(
    model_name="gemini-1.5-pro",
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
    headers = {"Authorization": f"Bearer {
