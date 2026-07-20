# -*- coding: utf-8 -*-
"""
Gemini API bilan ishlaydigan Telegram chatbot — Render versiyasi
----------------------------------------------------------------
Faqat savolga javob beradi va suhbatni davom ettiradi.
Hech qanday ortiqcha xabar yubormaydi.

Kalitlar Environment Variables orqali olinadi:
    TELEGRAM_TOKEN
    GEMINI_API_KEY
"""

import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import google.generativeai as genai
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    filters,
)

# ====== SOZLAMALAR (Render Environment'dan olinadi) ======
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
PORT = int(os.environ.get("PORT", 10000))

# Gemini ni sozlash
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# Har bir foydalanuvchi uchun alohida suhbat tarixi
chats = {}


def get_chat(user_id: int):
    """Foydalanuvchi uchun suhbat sessiyasini olish (tarix saqlanadi)."""
    if user_id not in chats:
        chats[user_id] = model.start_chat(history=[])
    return chats[user_id]


# ====== HTTP SERVER (Render bepul tarifi uchun) ======
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def run_http_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


# ====== ASOSIY JAVOB ======
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    await update.message.chat.send_action("typing")

    try:
        chat = get_chat(user_id)
        response = chat.send_message(text)
        answer = response.text
    except Exception:
        # Xatolik bo'lsa, suhbatni yangidan boshlab qayta urinadi
        chats.pop(user_id, None)
        try:
            chat = get_chat(user_id)
            response = chat.send_message(text)
            answer = response.text
        except Exception:
            return  # javob berolmasa, jim turadi

    # Telegram xabar limiti 4096 belgi — uzun javobni bo'lib yuborish
    for i in range(0, len(answer), 4000):
        await update.message.reply_text(answer[i:i + 4000])


# ====== ISHGA TUSHIRISH ======
def main():
    threading.Thread(target=run_http_server, daemon=True).start()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Barcha matnli xabarlar (shu jumladan /start ham) to'g'ridan-to'g'ri Geminiga boradi
    app.add_handler(MessageHandler(filters.TEXT, handle_message))

    app.run_polling()


if __name__ == "__main__":
    main()
