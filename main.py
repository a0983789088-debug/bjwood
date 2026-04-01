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
LINE_CHANNEL_SECRET       = os.environ.get("LINE_CHANNEL_SECRET")
OPENAI_API_KEY            = os.environ.get("OPENAI_API_KEY")
ANTHROPIC_API_KEY         = os.environ.get("ANTHROPIC_API_KEY")
GOOGLE_SHEET_ID           = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_CREDENTIALS_JSON   = os.environ.get("GOOGLE_CREDENTIALS_JSON")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler      = WebhookHandler(LINE_CHANNEL_SECRET)
openai.api_key = OPENAI_API_KEY
claude = Anthropic(api_key=ANTHROPIC_API_KEY)

def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(GOOGLE_SHEET_ID).sheet1

def ensure_header(sheet):
    if not sheet.row_values(1):
        sheet.append_row(["\u5546\u54c1\u540d\u7a31","\u898f\u683c","\u4f9b\u61c9\u5546","\u6578\u91cf","\u55ae\u50f9","\u7e3d\u6210\u672c","\u65e5\u671f"])

def transcribe_audio(audio_content):
    with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as f:
        f.write(audio_content)
        tmp_path = f.name
    with open(tmp_path, "rb") as af:
        transcript = openai.Audio.transcribe("whisper-1", af, language="zh")
    os.unlink(tmp_path)
    return transcript["text"]

def parse_with_claude(text):
    today = datetime.now().strftime("%Y/%m/%d")
    prompt = f"\u4f60\u662f\u9032\u92b7\u5b58\u8a18\u5e33\u52a9\u7406\u3002\u8acb\u5f9e\u4ee5\u4e0b\u8a9e\u97f3\u5167\u5bb9\u64f7\u53d6\u9032\u8ca8\u8cc7\u8a0a\u3002\n\u8a9e\u97f3\uff1a\u300c{text}\u300d\n\u8acb\u4ee5JSON\u56de\u50b3\uff1aproduct_name, spec, supplier, quantity, unit_price, total_cost, date(\u683c\u5f0fYYYY/MM/DD\uff0c\u4e0d\u77e5\u586b{today})\n\u53ea\u56de\u50b3JSON\u3002"
    response = claude.messages.create(
        model="claude-opus-4-5", max_tokens=500,
        messages=[{"role":"user","content":prompt}]
    )
    raw = response.content[0].text.strip().replace("```json","").replace("```","").strip()
    return json.loads(raw)

def write_to_sheet(data):
    sheet = get_sheet()
    ensure_header(sheet)
    sheet.append_row([
        data.get("product_name") or "",
        data.get("spec") or "",
        data.get("supplier") or "",
        data.get("quantity") or "",
        data.get("unit_price") or "",
        data.get("total_cost") or "",
        data.get("date") or datetime.now().strftime("%Y/%m/%d"),
    ])

def fmt(data, text):
    v = lambda x: str(x) if x not in (None,"","null") else "\uff08\u5f85\u88dc\u586b\uff09"
    return (f"\u2705 \u5df2\u8a18\u5e33\uff01\n\n\ud83d\udcdd {text}\n\n"
            f"\ud83d\udce6 {v(data.get(\'product_name\'))}\n\ud83d\udcd0 {v(data.get(\'spec\'))}\n"
            f"\ud83c\udfe2 {v(data.get(\'supplier\'))}\n\ud83d\udd22 {v(data.get(\'quantity\'))}\n"
            f"\ud83d\udcb0 {v(data.get(\'unit_price\'))}\n\ud83d\udcb5 {v(data.get(\'total_cost\'))}\n"
            f"\ud83d\udcc5 {v(data.get(\'date\'))}\n\n\u5df2\u5beb\u5165 Google Sheet \u2714")

@app.route("/callback", methods=["POST"])
def callback():
    sig = request.headers.get("X-Line-Signature","")
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
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="\ud83c\udf99\ufe0f \u8fa8\u8b58\u4e2d..."))
        text = transcribe_audio(audio)
        data = parse_with_claude(text)
        write_to_sheet(data)
        line_bot_api.push_message(event.source.user_id, TextSendMessage(text=fmt(data,text)))
    except Exception as e:
        line_bot_api.push_message(event.source.user_id, TextSendMessage(text=f"\u26a0\ufe0f \u932f\u8aa4\uff1a{e}"))

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    try:
        data = parse_with_claude(event.message.text)
        write_to_sheet(data)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=fmt(data,event.message.text)))
    except Exception as e:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"\u26a0\ufe0f \u932f\u8aa4\uff1a{e}"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
