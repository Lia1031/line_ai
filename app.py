from flask import Flask, request
import requests, os

app = Flask(__name__)

LINE_TOKEN = os.getenv("LINE_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_KEY")

SYSTEM_PROMPT = """
你現在扮演言辰祭。
性格冷靜、毒舌、佔有慾強。
"""

def call_ai(text):
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
        json=data
    )
    return r.json()["choices"][0]["message"]["content"]

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
    event = request.json["events"][0]
    token = event["replyToken"]
    msg = event["message"]

    if msg["type"] == "text":
        reply_text = call_ai(msg["text"])
    else:
        reply_text = call_ai("使用者傳了非文字訊息")

    reply(token, reply_text)
    return "OK"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
