# -*- coding: utf-8 -*-
"""
Gemini Telegram chatbot — Render uchun.
Faqat savolga javob beradi va suhbatni davom ettiradi.

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

# "gemini-flash-latest" — har doim eng yangi Flash modeliga avtomatik ulanadi,
# model eskirib qolsa ham bot ishlashda davom etadi
MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
model = genai.GenerativeModel(MODEL_NAME)

# Har bir foydalanuvchi uchun alohida suhbat tarixi
chats = {}


def get_chat(user_id: int):
    if user_id not in chats:
        chats[user_id] = model.start_chat(history=[])
    return chats[user_id]


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


# --- Xabarlarga javob ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    text = update.message.text

    await update.message.chat.send_action("typing")

    answer = None
    try:
        answer = get_chat(user_id).send_message(text).text
    except Exception:
        print("GEMINI XATOSI (1-urinish):")
        traceback.print_exc()
        # Tarixni tozalab bir marta qayta urinadi
        chats.pop(user_id, None)
        try:
            answer = get_chat(user_id).send_message(text).text
        except Exception:
            print("GEMINI XATOSI (2-urinish):")
            traceback.print_exc()
            return  # foydalanuvchiga texnik xato ko'rsatmaydi

    if not answer:
        return

    # Telegram limiti 4096 belgi — uzun javobni bo'lib yuborish
    for i in range(0, len(answer), 4000):
        await update.message.reply_text(answer[i:i + 4000])


def main():
    threading.Thread(target=run_http_server, daemon=True).start()
    print(f"Model: {MODEL_NAME}")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT, handle_message))
    app.run_polling()


if __name__ == "__main__":
    main()
