import os
import asyncio
import logging
from dotenv import load_dotenv
from pyrogram import Client, filters, errors
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ForceReply
)
from motor.motor_asyncio import AsyncIOMotorClient
from aiohttp import web

# ---------------- LOAD ENV ----------------
load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME")
HTTP_PORT = int(os.getenv("HTTP_PORT", 8080))  # default port 8080

# ---------------- LOGGING ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ---------------- DATABASE ----------------
class Database:
    def __init__(self):
        self.client = AsyncIOMotorClient(MONGO_URI)
        self.db = self.client[DB_NAME]
        self.users = self.db.users

    async def get_dump_channel(self, user_id: int):
        user = await self.users.find_one({"_id": user_id})
        return user["dump_channel"] if user else None

    async def set_dump_channel(self, user_id: int, channel_id: int):
        await self.users.update_one(
            {"_id": user_id},
            {"$set": {"dump_channel": channel_id}},
            upsert=True
        )

db = Database()

# ---------------- BOT ----------------
class Bot(Client):
    def __init__(self):
        super().__init__(
            name="DumpBot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            workers=5,
            sleep_threshold=30
        )

    async def start(self):
        await super().start()
        me = await self.get_me()
        logger.info(f"Bot started as @{me.username}")

bot = Bot()

# ---------------- MIRROR / DUMP HANDLER ---------------- #
@bot.on_message(
    filters.private &
    (
        filters.text |
        filters.document |
        filters.video |
        filters.audio |
        filters.photo |
        filters.animation |
        filters.sticker
    )
)
async def dump_handler(client: Client, message: Message):
    if message.text and message.text.startswith("/"):
        return

    user_id = message.from_user.id
    dump_channel = await db.get_dump_channel(user_id)

    if not dump_channel:
        if message.text:
            return
        await message.reply_text(
            "❌ You have not set a dump channel.\nUse /settings to configure it."
        )
        return

    try:
        await message.copy(dump_channel)

    except errors.FloodWait as e:
        logger.warning(f"FloodWait {e.value}s for user {user_id}")
        await asyncio.sleep(e.value)
        await message.copy(dump_channel)

    except Exception as e:
        logger.error(f"Dump error for user {user_id}: {e}")
        await message.reply_text("❌ Failed to dump this message.")

# ---------------- COMMANDS ---------------- #
@bot.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    await message.reply_text(
        "👋 **User Dump Bot**\n\n"
        "Send me any text or file and I will dump it to *your* channel.\n"
        "Use /settings to configure your dump channel."
    )

@bot.on_message(filters.command("settings") & filters.private)
async def settings_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    dump_channel = await db.get_dump_channel(user_id)

    text = (
        "**⚙ Your Settings**\n\n"
        f"📦 Dump Channel ID:\n`{dump_channel or 'Not Set'}`"
    )

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("➕ Set Dump Channel", callback_data="set_dump")]]
    )

    await message.reply_text(text, reply_markup=keyboard)

# ---------------- CALLBACK ---------------- #
@bot.on_callback_query(filters.regex("^set_dump$"))
async def set_dump_callback(client: Client, callback_query):
    await callback_query.message.delete()
    await callback_query.message.reply_text(
        "**Send your Dump Channel ID**\n\n"
        "Example:\n`-1001234567890`\n\n"
        "⚠ Bot must be admin in that channel",
        reply_markup=ForceReply(selective=True)
    )

# ---------------- SAVE CHANNEL ---------------- #
@bot.on_message(filters.private & filters.reply & filters.text)
async def save_dump_channel(client: Client, message: Message):
    if not message.reply_to_message:
        return

    if "Dump Channel ID" not in message.reply_to_message.text:
        return

    try:
        channel_id = int(message.text.strip())
        user_id = message.from_user.id

        await db.set_dump_channel(user_id, channel_id)

        await message.reply_text(
            f"✅ Dump channel saved for you:\n`{channel_id}`"
        )

    except ValueError:
        await message.reply_text("❌ Invalid channel ID. Send numbers only.")

# ---------------- HEALTH CHECK (PM) ---------------- #
@bot.on_message(filters.command("health") & filters.private)
async def health_check(client: Client, message: Message):
    try:
        await db.client.admin.command("ping")
        await message.reply_text("✅ Bot is running!\n✅ MongoDB connection OK!")
    except Exception as e:
        await message.reply_text(f"❌ Something is wrong!\n{e}")

# ---------------- HTTP HEALTH ENDPOINT ---------------- #
async def http_health(request):
    try:
        await db.client.admin.command("ping")
        return web.Response(text="OK")
    except:
        return web.Response(text="MongoDB connection failed", status=500)

async def start_web():
    app = web.Application()
    app.add_routes([web.get("/health", http_health)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HTTP_PORT)
    await site.start()
    logger.info(f"HTTP health check running on port {HTTP_PORT}")

# ---------------- RUN BOT ---------------- #
if __name__ == "__main__":
    # start HTTP server in background
    bot.loop.create_task(start_web())
    bot.run()
