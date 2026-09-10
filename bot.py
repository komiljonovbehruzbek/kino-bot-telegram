import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web

# Render beradigan portni olish (bo'lmasa 8080 ishlatadi)
import os

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = "8915091466:AAEFRagvpxXnao-TqfznNz3Y3npPdjfWwAY"
ADMIN_ID = 6449321994

# --- BIR NECHTA KANALLAR RO'YXATI ---
# Har bir kanal uchun: ID, nomi va taklif havolasi (invite link/username)
CHANNELS = [
    {
        "id": -1004489090250,
        "name": "1-Kanal (Kino Olam)",
        "url": "https://t.me/+_CKO-gy8fPQ4OWE6"
    },
    {
        "id": -1002198373500,
        "name": "2-Kanal (Shaxsiy)",
        "url": "https://t.me/dasturlash_nam"
    }
]

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- DATABASE (SQLITE) SOZLAMALARI ---
conn = sqlite3.connect("kino_bot.db", check_same_thread=False)
cursor = conn.cursor()

# Jadvallarni yaratish
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS movies (
    code TEXT PRIMARY KEY,
    file_id TEXT
)
""")
conn.commit()

# --- FSM (STATES) XABAR TARQATISH UCHUN ---
class BroadcastState(StatesGroup):
    waiting_for_message = State()

# --- YORDAMCHI FUNKSIYALAR ---

def add_user(user_id: int):
    """Foydalanuvchini bazaga qo'shish."""
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()

def get_stats():
    """Statistikani olish."""
    cursor.execute("SELECT COUNT(*) FROM users")
    users_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM movies")
    movies_count = cursor.fetchone()[0]
    return users_count, movies_count

def add_movie(code: str, file_id: str):
    """Kino qo'shish."""
    cursor.execute("INSERT OR REPLACE INTO movies (code, file_id) VALUES (?, ?)", (code, file_id))
    conn.commit()

def get_movie(code: str):
    """Kino faylini kodi bo'yicha olish."""
    cursor.execute("SELECT file_id FROM movies WHERE code = ?", (code,))
    row = cursor.fetchone()
    return row[0] if row else None

async def get_unsubscribed_channels(user_id: int) -> list:
    unsubscribed = []
    for channel in CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel["id"], user_id=user_id)
            if member.status not in ["creator", "administrator", "member"]:
                unsubscribed.append(channel)
        except TelegramBadRequest:
            unsubscribed.append(channel)
    return unsubscribed

def get_sub_keyboard(unsubscribed_channels: list) -> InlineKeyboardMarkup:
    keyboard = []
    for ch in unsubscribed_channels:
        keyboard.append([InlineKeyboardButton(text=f"📢 {ch['name']}", url=ch["url"])])
    keyboard.append([InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_subscription")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def admin_keyboard():
    """Admin menyusi tugmalari."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Statistika"), KeyboardButton(text="📢 Xabar tarqatish")]
        ],
        resize_keyboard=True
    )

# --- HANDLERLAR ---

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    add_user(message.from_user.id)

    if message.from_user.id == ADMIN_ID:
        await message.answer(
            f"Salom, Admin {message.from_user.first_name}!\nAdmin paneldan foydalanishingiz mumkin:",
            reply_markup=admin_keyboard()
        )
    else:
        await message.answer(
            f"Salom, {message.from_user.first_name}!\n"
            "Kino ko'rish uchun **kino kodini** kiriting:"
        )

# --- ADMIN PANEL FUNKSIYALARI ---

@dp.message(F.text == "📊 Statistika", F.from_user.id == ADMIN_ID)
async def stats_handler(message: types.Message):
    users_count, movies_count = get_stats()
    await message.answer(
        f"📊 **Bot Statistikasi:**\n\n"
        f"👥 Foydalanuvchilar: **{users_count} ta**\n"
        f"🎬 Joylangan kinolar: **{movies_count} ta**",
        parse_mode="Markdown"
    )

@dp.message(F.text == "📢 Xabar tarqatish", F.from_user.id == ADMIN_ID)
async def broadcast_start(message: types.Message, state: FSMContext):
    await state.set_state(BroadcastState.waiting_for_message)
    await message.answer(
        "Foydalanuvchilarga yubormoqchi bo'lgan xabaringizni kiriting (Matn, Rasm, Video yoki Post):\n\n"
        "*(Bekor qilish uchun /cancel buyrug'ini yuboring)*"
    )

@dp.message(Command("cancel"), BroadcastState.waiting_for_message)
async def broadcast_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Xabar tarqatish bekor qilindi.", reply_markup=admin_keyboard())

@dp.message(BroadcastState.waiting_for_message, F.from_user.id == ADMIN_ID)
async def broadcast_send(message: types.Message, state: FSMContext):
    await state.clear()

    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()

    success = 0
    failed = 0

    status_msg = await message.answer("⏳ Xabar tarqatish boshlandi...")

    for user in users:
        user_id = user[0]
        try:
            await message.copy_to(chat_id=user_id)
            success += 1
            await asyncio.sleep(0.05)  # Telegram spam limitiga tushmaslik uchun kichik tanaffus
        except (TelegramForbiddenError, TelegramBadRequest):
            failed += 1
        except Exception:
            failed += 1

    await status_msg.edit_text(
        f"✅ **Xabar tarqatish yakunlandi!**\n\n"
        f"🟢 Yuborildi: **{success}** ta\n"
        f"🔴 Etib bormadi (block qilgan): **{failed}** ta",
        parse_mode="Markdown"
    )

# --- ADMIN: KINO QO'SHISH ---
@dp.message(F.video & (F.from_user.id == ADMIN_ID))
async def add_movie_handler(message: types.Message):
    file_id = message.video.file_id
    caption = message.caption

    if caption and caption.isdigit():
        add_movie(caption, file_id)
        await message.reply(f"✅ Kino bazaga saqlandi!\nKod: `{caption}`", parse_mode="Markdown")
    else:
        await message.reply("⚠️ Videoga (caption) faqat **raqamli kod** yozib yuboring! Masalan: 101")

# --- KINO KODINI QIDIRISH VA OBUNA TEKSHIRUV ---

@dp.callback_query(F.data == "check_subscription")
async def check_sub_callback(call: types.CallbackQuery):
    unsubscribed = await get_unsubscribed_channels(call.from_user.id)
    if not unsubscribed:
        await call.message.delete()
        await call.message.answer("✅ Obunangiz tasdiqlandi. Endi kino kodini yozishingiz mumkin.")
    else:
        await call.answer("❌ Hali barcha kanallarga obuna bo'lmadingiz!", show_alert=True)

@dp.message(F.text)
async def get_movie_handler(message: types.Message):
    add_user(message.from_user.id)
    code = message.text.strip()

    # 1. Obuna tekshirish
    unsubscribed = await get_unsubscribed_channels(message.from_user.id)
    if unsubscribed:
        await message.answer(
            "⚠️ Kinoni yuklab olish uchun quyidagi kanallarga obuna bo'ling:",
            reply_markup=get_sub_keyboard(unsubscribed)
        )
        return

    # 2. Kod raqam ekanligini tekshirish
    if not code.isdigit():
        await message.answer("Iltimos, faqat kino kodini kiriting!")
        return

    # 3. Bazadan qidirish
    file_id = get_movie(code)
    if file_id:
        await message.answer_video(video=file_id, caption=f"🎬 Siz so'ragan kino (Kod: {code})")
    else:
        await message.answer("❌ Bu kod bo'yicha kino topilmadi.")

async def handle(request):
    return web.Response(text="Bot 24/7 faol ishlamoqda!")

async def main():
    print("Bot va Admin Panel ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
