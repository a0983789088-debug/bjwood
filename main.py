import os
import json
import tempfile
from datetime import datetime
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, AudioMessage, TextMessage, TextSendMessage
import openai
import gspread
from google.oauth2.service_account import Credentials
from anthropic import Anthropic

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
openai.api_key = OPENAI_API_KEY
claude = Anthropic(api_key=ANTHROPIC_API_KEY)


def get_sheet():
    d = json.loads(GOOGLE_CREDENTIALS_JSON)
    sc = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    c = Credentials.from_service_account_info(d, scopes=sc)
    return gspread.authorize(c).open_by_key(GOOGLE_SHEET_ID).sheet1


def ensure_header(s):
    if not s.row_values(1):
        s.append_row(["Name", "Spec", "Supplier", "Qty", "Price", "Total", "Date"])


def transcribe(b):
    with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as f:
        f.write(b)
        p = f.name
    with open(p, "rb") as af:
        t = openai.Audio.transcribe("whisper-1", af, language="zh")
    os.unlink(p)
    return t["text"]


def parse(text):
    today = datetime.now().strftime("%Y/%m/%d")
    prompt = (
        "You are an inventory assistant. Extract purchase info from: " + text + "\n"
        "Return JSON only: product_name, spec, supplier, quantity, unit_price, total_cost, "
        "date (YYYY/MM/DD format, use " + today + " if unknown). No other text."
    )
    response = claude.messages.create(
        model="claude-opus-4-5",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(raw)


def save(data):
    s = get_sheet()
    ensure_header(s)
    s.append_row([
        data.get("product_name") or "",
        data.get("spec") or "",
        data.get("supplier") or "",
        data.get("quantity") or "",
        data.get("unit_price") or "",
        data.get("total_cost") or "",
        data.get("date") or datetime.now().strftime("%Y/%m/%d"),
    ])


def build_reply(data, text):
    def v(x):
        return str(x) if x not in (None, "", "null") else "(unknown)"
    lines = [
        "OK Recorded!",
        "",
        "Input: " + text,
        "",
        "Product: " + v(data.get("product_name")),
        "Spec: " + v(data.get("spec")),
        "Supplier: " + v(data.get("supplier")),
        "Qty: " + v(data.get("quantity")),
        "Price: " + v(data.get("unit_price")),
        "Total: " + v(data.get("total_cost")),
        "Date: " + v(data.get("date")),
        "",
        "Saved to Google Sheet.",
    ]
    return "\n".join(lines)


@app.route("/callback", methods=["POST"])
def callback():
    sig = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, sig)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@handler.add(MessageEvent, message=AudioMessage)
def handle_audio(event):
    try:
        audio = line_bot_api.get_message_content(event.message.id).content
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="Processing..."))
        text = transcribe(audio)
        data = parse(text)
        save(data)
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text=build_reply(data, text))
        )
    except Exception as e:
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text="Error: " + str(e))
        )


@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    try:
        data = parse(event.message.text)
        save(data)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=build_reply(data, event.message.text))
        )
    except Exception as e:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="Error: " + str(e))
        )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
