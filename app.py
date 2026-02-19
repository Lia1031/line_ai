from flask import Flask, request, abort
import requests, os

app = Flask(__name__)

LINE_TOKEN = os.getenv("LINE_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_KEY")

SYSTEM_PROMPT = """
你現在扮演言辰祭。
性格冷靜、毒舌、佔有慾強。
"""

def call_ai(text):
    try:
        headers = {
            "Authorization": f"Bearer {OPENAI_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text}
            ]
        }
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=20
        )
        
        response_data = r.json()
        
        # 如果發生錯誤，印出完整的回傳內容以便排錯
        if "error" in response_data:
            print(f"OpenAI 官方錯誤訊息: {response_data['error']['message']}")
            return f"（言辰祭冷冷地看著你，似乎在忍耐什麼：{response_data['error']['code']}）"

        return response_data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"程式執行錯誤: {e}")
        return "（言辰祭挑了挑眉，似乎不想理你...）"

def reply(reply_token, text):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Authorization": f"Bearer {LINE_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}]
    }
    requests.post(url, headers=headers, json=data)

@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_json()
    
    # --- 關鍵修正：處理 LINE Verify 的空資料 ---
    if not body or "events" not in body or len(body["events"]) == 0:
        return "OK", 200 
    # ---------------------------------------

    event = body["events"][0]
    
    # 判斷是否有 replyToken (有些 event 沒有)
    if "replyToken" not in event:
        return "OK", 200

    token = event["replyToken"]
    msg = event.get("message", {})

    if msg.get("type") == "text":
        user_text = msg.get("text")
        reply_text = call_ai(user_text)
    else:
        reply_text = "言辰祭冷冷地看了你一眼，對這東西沒興趣。"

    reply(token, reply_text)
    return "OK", 200 # 確保回傳 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

