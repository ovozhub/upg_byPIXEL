"""
Telegram group-creation + Telethon session login bot
Single-file example combining python-telegram-bot (for user interaction) and Telethon (for user account actions).

Features implemented (best-effort):
1) /start asks for a one-time password "PIKSEL".
2) After correct password, asks for phone number (+998...).
3) Sends login code via Telethon and asks user to send the code.
4) Handles 2FA (if required) by asking for password and signing in.
5) After sign-in, attempts to create 50 supergroups and updates progress to the user.
6) For each created group: invites two bot accounts ("oxang_bot" and "pixel_menku") and promotes them to admins.
7) When done, the bot leaves all created groups for that session.
8) Sessions are stored in `sessions/{phone}.session` by Telethon.

Important notes / limitations:
- You MUST set API_ID and API_HASH (from my.telegram.org) and the telegram bot token used by python-telegram-bot.
- Some Telegram-specific operations (exact control of "chat history for new members", removing a user without banning, and fine-grained admin rights) may require minor adjustments depending on Telethon version and Telegram behavior. See inline TODO comments.
- This script is a best-effort implementation and needs testing. Read the comments and adapt for your environment.

Dependencies:
- pip install telethon python-telegram-bot==20.4 asyncio

Run:
python telegram_group_creator_bot.py

"""

import asyncio
import json
import os
from pathlib import Path
from telethon import TelegramClient, errors, functions, types
from telethon.errors import SessionPasswordNeededError
from telegram import Update, ForceReply
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters, ConversationHandler

# ---------- CONFIG ----------
# Put your API_ID and API_HASH here or set environment variables TELEGRAM_API_ID and TELEGRAM_API_HASH
API_ID = int(os.environ.get('TELEGRAM_API_ID', 'YOUR_API_ID'))
API_HASH = os.environ.get('TELEGRAM_API_HASH', 'YOUR_API_HASH')
# Bot token for python-telegram-bot (the UI bot interacting with the user)
BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', 'YOUR_TELEGRAM_BOT_TOKEN')

# The password user must send at /start
MASTER_PASSWORD = 'PIKSEL'

# Where to store simple state / sessions
DATA_DIR = Path('./data')
SESSIONS_DIR = Path('./sessions')
DATA_DIR.mkdir(exist_ok=True)
SESSIONS_DIR.mkdir(exist_ok=True)

# Simple persistent store for which users passed the master password earlier
PASSED_FILE = DATA_DIR / 'passed_passwords.json'
if not PASSED_FILE.exists():
    PASSED_FILE.write_text(json.dumps({}))

# Bots to invite
BOT_USERNAMES = ['oxang_bot', 'pixel_menku']

# Conversation states
PASSWORD, PHONE, CODE, TWOFA, RUNNING = range(5)

# Helper functions
def mark_passed(user_id: int):
    data = json.loads(PASSED_FILE.read_text())
    data[str(user_id)] = True
    PASSED_FILE.write_text(json.dumps(data))

def has_passed(user_id: int) -> bool:
    data = json.loads(PASSED_FILE.read_text())
    return data.get(str(user_id), False)

async def create_telethon_client_for(phone: str) -> TelegramClient:
    """Create (but do not start) a Telethon client using a session file named after the phone."""
    session_path = str(SESSIONS_DIR / phone)
    client = TelegramClient(session_path, API_ID, API_HASH)
    return client

# ---------- Handlers for python-telegram-bot UI ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Assalomu alaykum. Iltimos parolni kiriting:')
    return PASSWORD

async def password_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == MASTER_PASSWORD:
        mark_passed(update.effective_user.id)
        await update.message.reply_text('Parol tog\'ri. Telefon raqamingizni + bilan yuboring (misol: +998991234567):')
        return PHONE
    else:
        await update.message.reply_text('Noto\'g\'ri parol. /start qayta bering.')
        return ConversationHandler.END

async def phone_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.startswith('+'):
        await update.message.reply_text("Iltimos '+' bilan telefon yuboring.")
        return PHONE

    context.user_data['phone'] = phone
    # create telethon client and send code
    client = await create_telethon_client_for(phone)
    try:
        await client.connect()
        sent = await client.send_code_request(phone)
        # store phone_code_hash info for later
        context.user_data['phone_code_hash'] = getattr(sent, 'phone_code_hash', None)
        # store client in user_data for later use
        context.user_data['telethon_client'] = client
        await update.message.reply_text('Kod yuborildi. Telegramdan kelgan kodni kiriting:')
        return CODE
    except Exception as e:
        await update.message.reply_text(f'Xatolik kod yuborishda: {e}')
        try:
            await client.disconnect()
        except: pass
        return ConversationHandler.END

async def code_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    phone = context.user_data.get('phone')
    client: TelegramClient = context.user_data.get('telethon_client')

    if client is None or phone is None:
        await update.message.reply_text('Ichki xato: client topilmadi. /start qayta bering.')
        return ConversationHandler.END

    try:
        # Attempt sign in
        await client.sign_in(phone=phone, code=code)
    except SessionPasswordNeededError:
        # 2FA required
        await update.message.reply_text('Sizda 2FA yoqilgan. Iltimos, 2FA parolni kiriting:')
        return TWOFA
    except errors.PhoneCodeInvalidError:
        await update.message.reply_text('Noto\'g\'ri kod. Iltimos /start dan qayta urinib ko\'ring.')
        await client.disconnect()
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f'Kirishda xato: {e}')
        try:
            await client.disconnect()
        except: pass
        return ConversationHandler.END

    # If we reached here, sign-in succeeded
    await update.message.reply_text('Hisob muvaffaqiyatli ulandi! Guruhlar yaratilishi boshlanadi...')
    # spawn background task to do group creation while providing progress updates
    asyncio.create_task(run_group_creation_sequence(update, context, client))
    return RUNNING

async def twofa_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    client: TelegramClient = context.user_data.get('telethon_client')
    try:
        await client.sign_in(password=password)
    except Exception as e:
        await update.message.reply_text(f'2FA kirishda xato: {e} - /start qayta urinib ko\'ring.')
        try:
            await client.disconnect()
        except: pass
        return ConversationHandler.END

    await update.message.reply_text('2FA orqali muvaffaqiyatli ulandingiz. Guruhlar yaratilishi boshlanadi...')
    asyncio.create_task(run_group_creation_sequence(update, context, client))
    return RUNNING

# ---------- Core group-creation flow (Telethon) ----------
async def run_group_creation_sequence(update: Update, context: ContextTypes.DEFAULT_TYPE, client: TelegramClient):
    """Create 50 groups, invite bots, set admin rights, and show progress animated text to the user."""
    phone = context.user_data.get('phone')
    created_channels = []
    total = 50

    try:
        for i in range(1, total + 1):
            title = f"AutoGroup {phone} #{i}"
            about = f"Avto yaratilgan guruh {i}/{total}"
            try:
                result = await client(functions.channels.CreateChannelRequest(
                    title=title,
                    about=about,
                    megagroup=True
                ))
                channel = result.chats[0]
                created_channels.append(channel)

                # TODO: Set "chat history" visible for new members - Telethon doesn't expose a direct helper for GUI flag.
                # This might require using EditAdmin/permissions or a specific channel setting; left as TODO.

                # Invite/add bots
                for bot_username in BOT_USERNAMES:
                    try:
                        # resolve bot
                        bot_entity = await client.get_entity(bot_username)
                        await client(functions.channels.InviteToChannelRequest(
                            channel=channel,
                            users=[bot_entity]
                        ))
                        # promote bot to admin with some rights
                        rights = types.ChatAdminRights(
                            change_info=True,
                            post_messages=True,
                            edit_messages=True,
                            delete_messages=True,
                            ban_users=True,
                            invite_users=True,
                            pin_messages=True,
                            add_admins=(bot_username == 'pixel_menku')  # only pixel_menku will have add_admins True
                        )
                        try:
                            await client(functions.channels.EditAdminRequest(
                                channel=channel,
                                user_id=bot_entity,
                                admin_rights=rights,
                                rank='Admin'
                            ))
                        except Exception as e:
                            # some bots cannot be promoted or permission rejected
                            pass

                        # If the requirement is to remove pixel_menku from the group after promoting and not ban:
                        if bot_username == 'pixel_menku':
                            try:
                                # Kick participant then unban to simulate remove but not block
                                await client(functions.channels.EditBannedRequest(
                                    channel=channel,
                                    participant=bot_entity,
                                    banned_rights=types.ChatBannedRights(
                                        until_date=None,
                                        view_messages=True
                                    )
                                ))
                                # Unban (allow them to be added again later) - this method may vary
                                await client(functions.channels.EditBannedRequest(
                                    channel=channel,
                                    participant=bot_entity,
                                    banned_rights=types.ChatBannedRights(
                                        until_date=0,
                                        view_messages=False
                                    )
                                ))
                            except Exception:
                                # Fallback: attempt to kick (may ban). This area often requires tuning.
                                try:
                                    await client.kick_participant(channel, bot_entity)
                                except Exception:
                                    pass

                    except Exception as e:
                        # can't find or add bot; continue
                        pass

                # Update progress for user with an animated-looking bar
                pct = int((i / total) * 100)
                bar_len = 20
                filled = int((pct / 100) * bar_len)
                bar = '█' * filled + '-' * (bar_len - filled)
                await update.message.reply_text(f"[{bar}] {pct}%  — {i}/{total} guruh yaratildi.")

                # small wait to avoid hitting flood limits
                await asyncio.sleep(1)

            except Exception as e:
                # if channel creation failed, report and continue
                await update.message.reply_text(f'Guruh yaratishda xato ({i}): {e}')
                await asyncio.sleep(1)

        # All groups created; leave them
        for ch in created_channels:
            try:
                await client(functions.channels.LeaveChannelRequest(channel=ch))
            except Exception:
                pass

        await update.message.reply_text('Barcha guruhlar yaratildi va bot ulardan chiqib ketdi. Sessiya tozalanyapti.')

    except Exception as main_e:
        await update.message.reply_text(f'Yaratish markazida umumiy xato: {main_e}')

    finally:
        # Optionally delete session file after done
        try:
            await client.disconnect()
        except: pass
        # remove session file
        session_file = SESSIONS_DIR / phone
        for ext in ('.session', '.session-journal'):
            p = session_file.with_suffix(ext)
            if p.exists():
                try:
                    p.unlink()
                except: pass

    return

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Jarayon bekor qilindi.')
    return ConversationHandler.END

# ---------- Main (start the UI bot) ----------
def main():
    if BOT_TOKEN.startswith('YOUR_') or API_ID == 'YOUR_API_ID' or API_HASH == 'YOUR_API_HASH':
        print('Iltimos konfiguratsiyani to\'ldiring: TELEGRAM_API_ID, TELEGRAM_API_HASH va TG_BOT_TOKEN muhim.')
        return

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, password_handler)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, phone_handler)],
            CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, code_handler)],
            TWOFA: [MessageHandler(filters.TEXT & ~filters.COMMAND, twofa_handler)],
            RUNNING: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: None)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True
    )

    application.add_handler(conv)

    print('Bot ishlamoqda...')
    application.run_polling()

if __name__ == '__main__':
    main()
