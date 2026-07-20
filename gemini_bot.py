# -*- coding: utf-8 -*-
"""
Gemini Telegram chatbot — Render uchun.

Rejimlar:
  1. Botning o'ziga yozilgan xabarlarga javob beradi
  2. Telegram Business orqali shaxsiy akkauntingizga kelgan xabarlarga javob beradi

Aqlli boshqaruv:
  - Siz biror chatda o'zingiz javob yozsangiz, bot o'sha chatda
    PAUSE_MINUTES daqiqa jim turadi (standart: 30)
  - Botning o'ziga /off yozsangiz business javoblar butunlay to'xtaydi,
    /on yozsangiz qayta yoqiladi

Render Environment Variables:
    TELEGRAM_TOKEN  - @BotFather dan olingan token
    GEMINI_API_KEY  - https://aistudio.google.com/apikey dan olingan kalit
    PAUSE_MINUTES   - (ixtiyoriy) egasi yozgandan keyingi pauza, standart 30
"""

import os
import time
import traceback
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import google.generativeai as genai
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
PORT = int(os.environ.get("PORT", 10000))
PAUSE_MINUTES = int(os.environ.get("PAUSE_MINUTES", 30))

genai.configure(api_key=GEMINI_API_KEY)
MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
model = genai.GenerativeModel(MODEL_NAME)

chats = {}          # suhbat tarixlari
owner_ids = {}      # business connection -> egasining user id'si
paused_chats = {}   # chat_id -> egasi oxirgi yozgan vaqt
business_enabled = True  # /on va /off bilan boshqariladi


def get_chat(chat_key: str):
    if chat_key not in chats:
        chats[chat_key] = model.start_chat(history=[])
    return chats[chat_key]


def ask_gemini(chat_key: str, text: str):
    try:
        return get_chat(chat_key).send_message(text).text
    except Exception:
        print("GEMINI XATOSI (1-urinish):")
        traceback.print_exc()
        chats.pop(chat_key, None)
        try:
            return get_chat(chat_key).send_message(text).text
        except Exception:
            print("GEMINI XATOSI (2-urinish):")
            traceback.print_exc()
            return None


# --- HTTP server: Render bepul tarifi uchun ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def run_http_server():
    HTTPServer(("0.0.0.0", PORT), HealthHandler).serve_forever()


def is_owner(user_id: int) -> bool:
    return user_id in owner_ids.values()


# --- 1-rejim: botning o'ziga yozilgan xabarlar ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global business_enabled
    msg = update.message
    if not msg or not msg.text:
        return

    text = msg.text.strip().lower()

    # Egasidan boshqaruv buyruqlari
    if msg.from_user and is_owner(msg.from_user.id):
        if text == "/off":
            business_enabled = False
            await msg.reply_text("Business javoblar to'xtatildi. Yoqish: /on")
            return
        if text == "/on":
            business_enabled = True
            await msg.reply_text("Business javoblar yoqildi ✅")
            return

    await msg.chat.send_action("typing")
    answer = ask_gemini(f"direct_{msg.chat.id}", msg.text)
    if not answer:
        return
    for i in range(0, len(answer), 4000):
        await msg.reply_text(answer[i:i + 4000])


# --- 2-rejim: Telegram Business xabarlari ---
async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.business_message
    if not msg or not msg.text or not msg.from_user:
        return

    bc_id = msg.business_connection_id

    # Egasining ID'sini aniqlash (keshlanadi)
    if bc_id not in owner_ids:
        try:
            conn = await context.bot.get_business_connection(bc_id)
            owner_ids[bc_id] = conn.user.id
        except Exception:
            traceback.print_exc()
            return

    # Egasi o'zi yozdi -> bu chatda pauza boshlanadi
    if msg.from_user.id == owner_ids[bc_id]:
        paused_chats[msg.chat.id] = time.time()
        return

    # /off qilingan bo'lsa jim turadi
    if not business_enabled:
        return

    # Egasi yaqinda yozgan bo'lsa jim turadi
    last = paused_chats.get(msg.chat.id)
    if last and time.time() - last < PAUSE_MINUTES * 60:
        return

    answer = ask_gemini(f"biz_{bc_id}_{msg.chat.id}", msg.text)
    if not answer:
        return

    for i in range(0, len(answer), 4000):
        await context.bot.send_message(
            chat_id=msg.chat.id,
            text=answer[i:i + 4000],
            business_connection_id=bc_id,
        )


def main():
    threading.Thread(target=run_http_server, daemon=True).start()
    print(f"Model: {MODEL_NAME} | Pauza: {PAUSE_MINUTES} daqiqa")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(
        MessageHandler(filters.UpdateType.BUSINESS_MESSAGE, handle_business_message)
    )
    app.add_handler(
        MessageHandler(filters.TEXT & filters.UpdateType.MESSAGE, handle_message)
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
