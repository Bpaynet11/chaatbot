# -*- coding: utf-8 -*-
"""
Gemini Telegram chatbot — Render uchun.
Ikki rejimda ishlaydi:
  1. Botning o'ziga yozilgan xabarlarga javob beradi
  2. Telegram Business orqali ulansangiz, shaxsiy akkauntingizga
     kelgan xabarlarga siz oflayn bo'lganingizda javob beradi

Render Environment Variables:
    TELEGRAM_TOKEN  - @BotFather dan olingan token
    GEMINI_API_KEY  - https://aistudio.google.com/apikey dan olingan kalit
"""

import os
import traceback
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import google.generativeai as genai
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
PORT = int(os.environ.get("PORT", 10000))

genai.configure(api_key=GEMINI_API_KEY)
MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
model = genai.GenerativeModel(MODEL_NAME)

# Har bir suhbat uchun alohida tarix
chats = {}

# Business connection egalarining ID'lari (o'z xabarimizga javob bermaslik uchun)
owner_ids = {}


def get_chat(chat_key: str):
    if chat_key not in chats:
        chats[chat_key] = model.start_chat(history=[])
    return chats[chat_key]


def ask_gemini(chat_key: str, text: str):
    """Geminidan javob olish; xato bo'lsa tarixni tozalab qayta urinadi."""
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


# --- HTTP server: Render bepul tarifi uchun kerak ---
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


# --- 1-rejim: botning o'ziga yozilgan xabarlar ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    await msg.chat.send_action("typing")

    answer = ask_gemini(f"direct_{msg.chat.id}", msg.text)
    if not answer:
        return

    for i in range(0, len(answer), 4000):
        await msg.reply_text(answer[i:i + 4000])


# --- 2-rejim: Telegram Business orqali kelgan xabarlar ---
async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.business_message
    if not msg or not msg.text or not msg.from_user:
        return

    bc_id = msg.business_connection_id

    # Akkaunt egasining ID'sini aniqlash (bir marta, keyin keshdan)
    if bc_id not in owner_ids:
        try:
            conn = await context.bot.get_business_connection(bc_id)
            owner_ids[bc_id] = conn.user.id
        except Exception:
            traceback.print_exc()
            return

    # Egasi o'zi yozgan xabarga javob bermaslik
    if msg.from_user.id == owner_ids[bc_id]:
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


# --- TASHXIS: har bir kelgan yangilanishni logga yozish ---
async def log_all_updates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kinds = []
    if update.message: kinds.append("message")
    if update.business_message: kinds.append("business_message")
    if update.business_connection: kinds.append("business_connection")
    if update.edited_business_message: kinds.append("edited_business_message")
    if not kinds: kinds.append("boshqa_tur")
    print(f"UPDATE KELDI: {', '.join(kinds)}")


def main():
    threading.Thread(target=run_http_server, daemon=True).start()
    print(f"Model: {MODEL_NAME}")
    print("TASHXIS REJIMI YONIQ - har bir update logga yoziladi")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Tashxis: hamma update'larni ko'rish (group=-1 birinchi ishlaydi)
    from telegram.ext import TypeHandler
    app.add_handler(TypeHandler(Update, log_all_updates), group=-1)

    # Business xabarlar (shaxsiy akkaunt orqali)
    app.add_handler(
        MessageHandler(filters.UpdateType.BUSINESS_MESSAGE, handle_business_message)
    )
    # Oddiy xabarlar (botning o'ziga)
    app.add_handler(
        MessageHandler(filters.TEXT & filters.UpdateType.MESSAGE, handle_message)
    )

    # Business yangilanishlarini ham qabul qilish
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
