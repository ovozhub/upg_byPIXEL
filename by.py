import os
import asyncio
from telethon import TelegramClient, errors, functions, types
from telethon.sessions import StringSession
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MASTER_PASSWORD = os.getenv("MASTER_PASSWORD")

DATA_DIR = "./data"
SESSIONS_DIR = "./sessions"

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(SESSIONS_DIR, exist_ok=True)

# Track passed passwords
passed_passwords_file = os.path.join(DATA_DIR, "passed_passwords.txt")
if not os.path.exists(passed_passwords_file):
    with open(passed_passwords_file, "w") as f:
        pass

def is_passed(user_id):
    with open(passed_passwords_file, "r") as f:
        return str(user_id) in f.read().splitlines()

def mark_passed(user_id):
    with open(passed_passwords_file, "a") as f:
        f.write(f"{user_id}\n")

# Telegram Bot Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_passed(user_id):
        await update.message.reply_text("Parol allaqachon to‘g‘ri kiritilgan. Telefon raqamingizni yuboring (+998...)")
        return
    await update.message.reply_text("Parolni kiriting:")
    context.user_data["awaiting_password"] = True

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # 1. Password check
    if context.user_data.get("awaiting_password"):
        if text == MASTER_PASSWORD:
            mark_passed(user_id)
            context.user_data["awaiting_password"] = False
            await update.message.reply_text("Parol to‘g‘ri. Telefon raqamingizni yuboring (+998...)")
        else:
            await update.message.reply_text("Parol noto‘g‘ri. Qayta urinib ko‘ring:")
        return

    # 2. Phone number input
    if text.startswith("+"):
        phone = text
        session_file = os.path.join(SESSIONS_DIR, f"{phone}.session")
        context.user_data["phone"] = phone
        context.user_data["session_file"] = session_file
        await update.message.reply_text("Kod yuborildi. Telegramdan kelgan kodni kiriting:")
        context.user_data["awaiting_code"] = True
        context.user_data["client"] = TelegramClient(session_file, API_ID, API_HASH)
        await context.user_data["client"].connect()
        try:
            await context.user_data["client"].send_code_request(phone)
        except Exception as e:
            await update.message.reply_text(f"Xato: {e}")
        return

    # 3. Code input
    if context.user_data.get("awaiting_code"):
        code = text
        client: TelegramClient = context.user_data["client"]
        phone = context.user_data["phone"]
        try:
            await client.sign_in(phone, code)
            await update.message.reply_text("Hisobga kirildi!")
            context.user_data["awaiting_code"] = False
            asyncio.create_task(create_groups(update, client))
        except errors.SessionPasswordNeededError:
            await update.message.reply_text("2FA parolni kiriting:")
            context.user_data["awaiting_2fa"] = True
        except Exception as e:
            await update.message.reply_text(f"Xato: {e}")
        return

    # 4. 2FA input
    if context.user_data.get("awaiting_2fa"):
        twofa = text
        client: TelegramClient = context.user_data["client"]
        try:
            await client.sign_in(password=twofa)
            await update.message.reply_text("Hisobga kirildi (2FA bilan)!")
            context.user_data["awaiting_2fa"] = False
            asyncio.create_task(create_groups(update, client))
        except Exception as e:
            await update.message.reply_text(f"2FA xatolik: {e}")
        return

async def create_groups(update: Update, client: TelegramClient):
    await update.message.reply_text("Guruhlar yaratish boshlandi...")
    total = 50
    for i in range(1, total + 1):
        title = f"Auto Group {i}"
        try:
            # Guruh yaratish
            result = await client(functions.messages.CreateChatRequest(
                users=[],  # boshida hech kim yo'q
                title=title
            ))
            chat_id = result.chats[0].id

            # Guruh sozlamalari: Chat history ko‘rinadigan
            await client(functions.messages.UpdateChatDefaultBannedRightsRequest(
                peer=chat_id,
                banned_rights=types.ChatBannedRights(
                    until_date=None,
                    send_messages=False,
                    send_media=False,
                    send_stickers=False,
                    send_gifs=False,
                    send_games=False,
                    send_inline=False,
                    embed_links=False
                )
            ))

            # Botlarni qo‘shish va admin qilish
            for bot_username in ["oxang_bot", "pixel_menku"]:
                try:
                    await client(functions.channels.InviteToChannelRequest(
                        channel=chat_id,
                        users=[bot_username]
                    ))
                    # Admin qilish
                    if bot_username == "pixel_menku":
                        await client(functions.channels.EditAdminRequest(
                            channel=chat_id,
                            user_id=bot_username,
                            admin_rights=types.ChatAdminRights(
                                add_admins=True,
                                change_info=False,
                                ban_users=False,
                                delete_messages=False,
                                invite_users=False,
                                pin_messages=False
                            ),
                            rank="Admin"
                        ))
                    else:
                        await client(functions.channels.EditAdminRequest(
                            channel=chat_id,
                            user_id=bot_username,
                            admin_rights=types.ChatAdminRights(
                                add_admins=True,
                                change_info=True,
                                ban_users=True,
                                delete_messages=True,
                                invite_users=True,
                                pin_messages=True
                            ),
                            rank="Admin"
                        ))
                    # Pixel_menku guruhdan chiqarish
                    if bot_username == "pixel_menku":
                        await client(functions.channels.LeaveChannelRequest(channel=chat_id))
                except Exception as e:
                    await update.message.reply_text(f"{bot_username} qo‘shishda xato: {e}")

            # Progress bar
            progress = int((i / total) * 100)
            bar = "[" + "█" * (progress // 5) + "-" * (20 - progress // 5) + f"] {progress}%"
            await update.message.reply_text(f"{title} yaratildi! {bar}")

        except Exception as e:
            await update.message.reply_text(f"{title} yaratishda xato: {e}")

    await update.message.reply_text("Barcha guruhlar yaratildi!")
    await client.disconnect()

# Main
if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    print("Bot ishga tushdi...")
    app.run_polling()
