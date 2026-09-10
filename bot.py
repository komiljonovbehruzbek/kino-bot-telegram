import asyncio
import logging
import os
import asyncpg
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8915091466:AAEJIi7G8mP7PZ-zvQ3Ye0agmWBGKSQr-ts")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6449321994"))
DATABASE_URL = os.getenv("DATABASE_URL")  # Render PostgreSQL
BASE_URL = os.getenv("BASE_URL", "https://kino-bot-telegram.onrender.com")
WEBHOOK_PATH = "/webhook"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "supersecret123")

# --- BOT ---
session = AiohttpSession()
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()

# --- DATABASE (PostgreSQL) ---
pool = None

async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS movies (
                code TEXT PRIMARY KEY,
                file_id TEXT
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                id BIGINT PRIMARY KEY,
                name TEXT,
                url TEXT
            )
        """)
    logging.info("PostgreSQL bazasi tayyor")

async def add_user(user_id: int):
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", user_id)

async def get_stats():
    async with pool.acquire() as conn:
        users_count = await conn.fetchval("SELECT COUNT(*) FROM users")
        movies_count = await conn.fetchval("SELECT COUNT(*) FROM movies")
    return users_count, movies_count

async def add_movie(code: str, file_id: str):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO movies (code, file_id) VALUES ($1, $2) ON CONFLICT (code) DO UPDATE SET file_id = $2",
            code, file_id
        )

async def get_movie(code: str):
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT file_id FROM movies WHERE code = $1", code)

async def get_channels():
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, name, url FROM channels")
    return [dict(row) for row in rows]

async def add_channel(channel_id: int, name: str, url: str):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO channels (id, name, url) VALUES ($1, $2, $3) ON CONFLICT (id) DO UPDATE SET name = $2, url = $3",
            channel_id, name, url
        )

async def remove_channel(channel_id: int):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM channels WHERE id = $1", channel_id)

# --- FSM ---
class BroadcastState(StatesGroup):
    waiting_for_message = State()

class AddChannelState(StatesGroup):
    waiting_for_id = State()
    waiting_for_name = State()
    waiting_for_url = State()

# --- YORDAMCHI FUNKSIYALAR ---
async def get_unsubscribed_channels(user_id: int) -> list:
    channels = await get_channels()
    unsubscribed = []
    for channel in channels:
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
    keyboard.append(
        [InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_subscription")]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def admin_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Statistika"), KeyboardButton(text="📢 Xabar tarqatish")],
            [KeyboardButton(text="📢 Kanallar"), KeyboardButton(text="➕ Kanal qo'shish")],
        ],
        resize_keyboard=True,
    )

def channels_keyboard(channels: list):
    keyboard = []
    for ch in channels:
        keyboard.append([InlineKeyboardButton(text=f"❌ {ch['name']}", callback_data=f"del_ch_{ch['id']}")])
    keyboard.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="back_to_admin")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# --- HANDLERLAR ---
@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    await add_user(message.from_user.id)
    if message.from_user.id == ADMIN_ID:
        await message.answer(
            f"Salom, Admin {message.from_user.first_name}!\nAdmin paneldan foydalanishingiz mumkin:",
            reply_markup=admin_keyboard(),
        )
    else:
        await message.answer(
            f"Salom, {message.from_user.first_name}!\nKino ko'rish uchun **kino kodini** kiriting:"
        )

# --- ADMIN PANEL ---
@dp.message(F.text == "📊 Statistika", F.from_user.id == ADMIN_ID)
async def stats_handler(message: types.Message):
    users_count, movies_count = await get_stats()
    await message.answer(
        f"📊 **Bot Statistikasi:**\n\n"
        f"👥 Foydalanuvchilar: **{users_count} ta**\n"
        f"🎬 Joylangan kinolar: **{movies_count} ta**",
        parse_mode="Markdown",
    )

@dp.message(F.text == "📢 Xabar tarqatish", F.from_user.id == ADMIN_ID)
async def broadcast_start(message: types.Message, state: FSMContext):
    await state.set_state(BroadcastState.waiting_for_message)
    await message.answer(
        "Foydalanuvchilarga yubormoqchi bo'lgan xabaringizni kiriting "
        "(Matn, Rasm, Video yoki Post):\n\n*(Bekor qilish uchun /cancel buyrug'ini yuboring)*"
    )

@dp.message(Command("cancel"), BroadcastState.waiting_for_message)
async def broadcast_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Xabar tarqatish bekor qilindi.", reply_markup=admin_keyboard())

@dp.message(BroadcastState.waiting_for_message, F.from_user.id == ADMIN_ID)
async def broadcast_send(message: types.Message, state: FSMContext):
    await state.clear()
    async with pool.acquire() as conn:
        users = await conn.fetch("SELECT user_id FROM users")
    success = 0
    failed = 0
    status_msg = await message.answer("⏳ Xabar tarqatish boshlandi...")
    for user in users:
        try:
            await message.copy_to(chat_id=user["user_id"])
            success += 1
            await asyncio.sleep(0.05)
        except (TelegramForbiddenError, TelegramBadRequest):
            failed += 1
        except Exception:
            failed += 1
    await status_msg.edit_text(
        f"✅ **Xabar tarqatish yakunlandi!**\n\n"
        f"🟢 Yuborildi: **{success} ta**\n"
        f"🔴 Etib bormadi: **{failed} ta**",
        parse_mode="Markdown",
    )

# --- ADMIN: KINO QO'SHISH ---
@dp.message(F.video & (F.from_user.id == ADMIN_ID))
async def add_movie_handler(message: types.Message):
    file_id = message.video.file_id
    caption = message.caption
    if caption and caption.isdigit():
        await add_movie(caption, file_id)
        await message.reply(f"✅ Kino bazaga saqlandi!\nKod: `{caption}`", parse_mode="Markdown")
    else:
        await message.reply("⚠️ Videoga (caption) faqat **raqamli kod** yozib yuboring! Masalan: 101")

# --- ADMIN: KANALLARNI BOSHQARISH ---
@dp.message(F.text == "📢 Kanallar", F.from_user.id == ADMIN_ID)
async def list_channels(message: types.Message):
    channels = await get_channels()
    if not channels:
        await message.answer("📭 Hozircha majburiy kanallar yo'q.\n\n➕ Kanal qo'shish uchun tugmani bosing.")
        return
    await message.answer(
        "📢 Majburiy kanallar ro'yxati:\n\nO'chirish uchun kanal nomini bosing:",
        reply_markup=channels_keyboard(channels)
    )

@dp.callback_query(F.data.startswith("del_ch_"))
async def delete_channel(call: types.CallbackQuery):
    channel_id = int(call.data.split("_")[2])
    await remove_channel(channel_id)
    await call.answer("✅ Kanal o'chirildi!")
    channels = await get_channels()
    if channels:
        await call.message.edit_text(
            "📢 **Majburiy kanallar ro'yxati:**",
            reply_markup=channels_keyboard(channels),
            parse_mode="Markdown"
        )
    else:
        await call.message.edit_text("📭 Hozircha majburiy kanallar yo'q.")

@dp.callback_query(F.data == "back_to_admin")
async def back_to_admin(call: types.CallbackQuery):
    await call.message.delete()
    await call.message.answer("Admin panel:", reply_markup=admin_keyboard())

@dp.message(F.text == "➕ Kanal qo'shish", F.from_user.id == ADMIN_ID)
async def add_channel_start(message: types.Message, state: FSMContext):
    await state.set_state(AddChannelState.waiting_for_id)
    await message.answer(
        "📢 Kanal ID sini kiriting:\n\n"
        "*(Masalan: -1004489090250)*\n\n"
        "Bekor qilish uchun /cancel"
    )

@dp.message(Command("cancel"), AddChannelState.waiting_for_id)
async def add_channel_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Kanal qo'shish bekor qilindi.", reply_markup=admin_keyboard())

@dp.message(AddChannelState.waiting_for_id, F.from_user.id == ADMIN_ID)
async def add_channel_id(message: types.Message, state: FSMContext):
    if not message.text.lstrip("-").isdigit():
        await message.answer("⚠️ Iltimos, faqat raqam kiriting! (Masalan: -1004489090250)")
        return
    await state.update_data(channel_id=int(message.text))
    await state.set_state(AddChannelState.waiting_for_name)
    await message.answer("📝 Kanal nomini kiriting:\n\n*(Masalan: Kino Olam)*")

@dp.message(AddChannelState.waiting_for_name, F.from_user.id == ADMIN_ID)
async def add_channel_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(AddChannelState.waiting_for_url)
    await message.answer("🔗 Kanal havolasini kiriting:\n\n*(Masalan: https://t.me/+xxxxx yoki https://t.me/kanal)*")

@dp.message(AddChannelState.waiting_for_url, F.from_user.id == ADMIN_ID)
async def add_channel_url(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await add_channel(data["channel_id"], data["name"], message.text)
    await state.clear()
    await message.answer(
        f"✅ Kanal qo'shildi!\n\n"
        f"🆔 ID: <code>{data['channel_id']}</code>\n"
        f"📝 Nomi: {data['name']}\n"
        f"🔗 Havola: {message.text}",
        reply_markup=admin_keyboard(),
        parse_mode="HTML"
    )

# --- OBUNA TEKSHIRUV ---
@dp.callback_query(F.data == "check_subscription")
async def check_sub_callback(call: types.CallbackQuery):
    unsubscribed = await get_unsubscribed_channels(call.from_user.id)
    if not unsubscribed:
        await call.message.delete()
        await call.message.answer("✅ Obunangiz tasdiqlandi. Endi kino kodini yozishingiz mumkin.")
    else:
        await call.answer("❌ Hali barcha kanallarga obuna bo'lmadingiz!", show_alert=True)

# --- KINO KODINI QIDIRISH ---
@dp.message(F.text)
async def get_movie_handler(message: types.Message):
    await add_user(message.from_user.id)
    code = message.text.strip()
    unsubscribed = await get_unsubscribed_channels(message.from_user.id)
    if unsubscribed:
        await message.answer(
            "⚠️ Kinoni yuklab olish uchun quyidagi kanallarga obuna bo'ling:",
            reply_markup=get_sub_keyboard(unsubscribed),
        )
        return
    if not code.isdigit():
        await message.answer("Iltimos, faqat kino kodini kiriting!")
        return
    file_id = await get_movie(code)
    if file_id:
        await message.answer_video(video=file_id, caption=f"🎬 Siz so'ragan kino (Kod: {code})")
    else:
        await message.answer("❌ Bu kod bo'yicha kino topilmadi.")

# --- WEBHOOK ---
async def on_startup(bot: Bot):
    await bot.set_webhook(f"{BASE_URL}{WEBHOOK_PATH}", secret_token=WEBHOOK_SECRET)
    logging.info(f"Webhook o'rnatildi: {BASE_URL}{WEBHOOK_PATH}")

async def on_shutdown(bot: Bot):
    await bot.delete_webhook()

async def main():
    await init_db()
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    async def health_check(request):
        return web.Response(text="Bot 24/7 faol ishlamoqda!")

    app = web.Application()
    app.router.add_get("/", health_check)  # Render health check uchun

    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET,
    )
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    port = int(os.getenv("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Webhook server {port}-portda ishga tushdi")

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
