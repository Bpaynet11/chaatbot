# -*- coding: utf-8 -*-
"""
Gemini Telegram chatbot — Render uchun.
KO'P EGALI (multi-tenant): istalgan odam bu botni o'zining Telegram
Business sozlamalaridan ulab, shaxsiy yordamchi sifatida ishlata oladi.
Hech qanday /start yoki qo'shimcha sozlash shart emas — Telegram
Business ulanishning o'zi yetarli.

Rejimlar:
  1. Botning o'ziga yozilgan xabarlarga javob beradi
  2. Telegram Business orqali ulagan HAR BIR odamning shaxsiy
     akkauntiga kelgan xabarlarga, o'sha odam nomidan javob beradi

Aqlli boshqaruv (har bir ega uchun ALOHIDA ishlaydi):
  - Ega biror chatda o'zi javob yozsa, bot o'sha chatda
    PAUSE_MINUTES daqiqa jim turadi (standart: 30)
  - Ega botning o'ziga /off yozsa, FAQAT O'ZINING business javoblari
    to'xtaydi (boshqalarga taalluqli emas), /on bilan qayta yoqiladi

Render Environment Variables:
    TELEGRAM_TOKEN  - @BotFather dan olingan token
    GEMINI_API_KEY  - https://aistudio.google.com/apikey dan olingan kalit
    PAUSE_MINUTES   - (ixtiyoriy) ega yozgandan keyingi pauza, standart 30
"""

import os
import time
import asyncio
import traceback
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import re
import html as html_lib

import google.generativeai as genai
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters


def md_to_html(text: str) -> str:
    """Gemini'ning Markdown javobini Telegram HTML formatiga o'girish."""
    code_blocks = []
    def save_block(m):
        code_blocks.append(m.group(1))
        return f"\x00BLOCK{len(code_blocks)-1}\x00"
    text = re.sub(r"```[a-zA-Z0-9]*\n?(.*?)```", save_block, text, flags=re.DOTALL)

    inline_codes = []
    def save_inline(m):
        inline_codes.append(m.group(1))
        return f"\x00INLINE{len(inline_codes)-1}\x00"
    text = re.sub(r"`([^`\n]+)`", save_inline, text)

    text = html_lib.escape(text)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"^(\s*)[\*\-]\s+", r"\1• ", text, flags=re.MULTILINE)

    for i, block in enumerate(code_blocks):
        safe = html_lib.escape(block.strip())
        text = text.replace(f"\x00BLOCK{i}\x00", f"<pre>{safe}</pre>")
    for i, inline in enumerate(inline_codes):
        safe = html_lib.escape(inline)
        text = text.replace(f"\x00INLINE{i}\x00", f"<code>{safe}</code>")

    return text


async def send_pretty(bot, chat_id: int, answer: str, bc_id=None):
    """Javobni chiroyli formatda yuborish; format xato bersa oddiy matnga o'tadi."""
    pretty = md_to_html(answer)
    for i in range(0, len(pretty), 4000):
        chunk = pretty[i:i + 4000]
        kwargs = {"chat_id": chat_id, "text": chunk}
        if bc_id:
            kwargs["business_connection_id"] = bc_id
        try:
            await bot.send_message(parse_mode="HTML", **kwargs)
        except Exception:
            plain = answer[i:i + 4000]
            kwargs["text"] = plain
            await bot.send_message(**kwargs)


TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
PORT = int(os.environ.get("PORT", 10000))
PAUSE_MINUTES = int(os.environ.get("PAUSE_MINUTES", 30))

genai.configure(api_key=GEMINI_API_KEY)
MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")

model = genai.GenerativeModel(MODEL_NAME)

# --- Har bir "ega" (business connection) uchun alohida holat ---
chats = {}              # chat_key -> Gemini suhbat sessiyasi
owner_names = {}        # bc_id -> ega ismi (Telegram profilidan olinadi)
owner_ids = {}          # bc_id -> ega user_id
owner_bc_ids = {}       # user_id -> shu odamga tegishli bc_id'lar to'plami
paused_chats = {}       # (bc_id, chat_id) -> ega oxirgi yozgan vaqt
disabled_owners = set() # /off qilgan egalarning user_id'lari


def business_model_for(bc_id: str):
    """Har bir ega uchun o'z ismi bilan tayyorlangan model olish."""
    name = owner_names.get(bc_id, "Xo'jayin")
    prompt = f"""Sen {name}ning Telegram'dagi shaxsiy yordamchisisan.
{name} hozir telefonda emas (oflayn), shuning uchun unga yozgan odamlarga
sen javob beryapsan.

Qoidalar:
- Suhbat boshida (faqat birinchi javobingda) qisqa qilib tanishtir: sen
  {name}ning yordamchisi ekaningni, u hozir aloqada emasligini va
  imkon bo'lishi bilan o'zi javob berishini ayt.
- O'zingni {name}man deb ko'rsatma — sen uning yordamchisisan.
- Suhbatdosh qaysi tilda yozsa, o'sha tilda javob ber (o'zbek, rus yoki boshqa).
- Xushmuomala, samimiy va qisqa javob ber. Keraksiz uzun matn yozma.
- Muhim yoki shoshilinch gap bo'lsa, uni {name}ga yetkazilishini aytib,
  xotirjam qil.
- Oddiy savollarga bilganingcha yordam berishing mumkin, lekin {name}
  nomidan va'da berma, uning shaxsiy ishlari haqida taxmin qilma."""
    return genai.GenerativeModel(MODEL_NAME, system_instruction=prompt)


def get_chat(chat_key: str, bc_id: str = None):
    if chat_key not in chats:
        m = business_model_for(bc_id) if bc_id else model
        chats[chat_key] = m.start_chat(history=[])
    return chats[chat_key]


def ask_gemini(chat_key: str, text: str, bc_id: str = None):
    try:
        return get_chat(chat_key, bc_id).send_message(text).text
    except Exception:
        print("GEMINI XATOSI (1-urinish):")
        traceback.print_exc()
        chats.pop(chat_key, None)
        try:
            return get_chat(chat_key, bc_id).send_message(text).text
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


# --- 1-rejim: botning o'ziga yozilgan xabarlar (buyruqlar shu yerda) ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text or not msg.from_user:
        return

    user_id = msg.from_user.id
    text = msg.text.strip().lower()

    # Bu odam biror business ulanishga ega bo'lsa, /off /on unga tegishli
    if user_id in owner_bc_ids:
        if text == "/off":
            disabled_owners.add(user_id)
            await msg.reply_text("Shaxsiy yordamchingiz to'xtatildi. Yoqish: /on")
            return
        if text == "/on":
            disabled_owners.discard(user_id)
            await msg.reply_text("Shaxsiy yordamchingiz yoqildi ✅")
            return

    await msg.chat.send_action("typing")
    answer = await asyncio.to_thread(ask_gemini, f"direct_{msg.chat.id}", msg.text)
    if not answer:
        return
    await send_pretty(context.bot, msg.chat.id, answer)


# --- 2-rejim: Telegram Business xabarlari (har bir ega uchun alohida) ---
async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.business_message
    if not msg or not msg.text or not msg.from_user:
        return

    bc_id = msg.business_connection_id

    # Egani birinchi marta ko'rsak, ma'lumotlarini olib keshlaymiz
    if bc_id not in owner_ids:
        try:
            conn = await context.bot.get_business_connection(bc_id)
            owner_ids[bc_id] = conn.user.id
            owner_names[bc_id] = conn.user.first_name or "Xo'jayin"
            owner_bc_ids.setdefault(conn.user.id, set()).add(bc_id)
        except Exception:
            traceback.print_exc()
            return

    owner_id = owner_ids[bc_id]

    # Ega o'zi yozdi -> shu chatda pauza boshlanadi
    if msg.from_user.id == owner_id:
        paused_chats[(bc_id, msg.chat.id)] = time.time()
        return

    # Shu ega /off qilgan bo'lsa jim turadi
    if owner_id in disabled_owners:
        return

    # Ega yaqinda yozgan bo'lsa jim turadi
    last = paused_chats.get((bc_id, msg.chat.id))
    if last and time.time() - last < PAUSE_MINUTES * 60:
        return

    answer = await asyncio.to_thread(
        ask_gemini, f"biz_{bc_id}_{msg.chat.id}", msg.text, bc_id
    )
    if not answer:
        return

    await send_pretty(context.bot, msg.chat.id, answer, bc_id=bc_id)


def main():
    threading.Thread(target=run_http_server, daemon=True).start()
    print(f"Model: {MODEL_NAME} | Pauza: {PAUSE_MINUTES} daqiqa | Ko'p egali rejim")

    app = Application.builder().token(TELEGRAM_TOKEN).concurrent_updates(True).build()
    app.add_handler(
        MessageHandler(filters.UpdateType.BUSINESS_MESSAGE, handle_business_message)
    )
    app.add_handler(
        MessageHandler(filters.TEXT & filters.UpdateType.MESSAGE, handle_message)
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
