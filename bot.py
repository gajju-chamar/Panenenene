import asyncio
import os
import random
import re
import tempfile
import io
import shutil

from PIL import Image
from telegram import (
    Update,
    InputSticker,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.error import TelegramError

from config import (
    TOKEN,
    OWNER_ID,
    STICKER_LOG_CHANNEL_ID,
    INBOX_CHANNEL_ID,
)

from storage import (
    load_users,
    save_user,
    track_message,
    get_user_for_msg,
    get_pack,
    save_pack,
    get_video_pack,
    save_video_pack,
)
from responses import START, PROC, SUCCESS, FAIL, RANDOM_TEXT, DELULU, IMAGINE


# ═══════════════════════════════════════════════════════════════════════════════
#  Commands
# ═══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or update.effective_chat.type != "private":
        return
    if not update.effective_user or not update.message:
        return
    await save_user(update.effective_user.id)
    await update.message.reply_text(random.choice(START))


async def cmd_mypack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or update.effective_chat.type != "private":
        return
    if not update.effective_user or not update.message:
        return

    user_id = update.effective_user.id
    static_pack = await get_pack(user_id)
    video_pack = await get_video_pack(user_id)

    if not static_pack and not video_pack:
        await update.message.reply_text(
            "*hic* you don't have a pack yet~!! 🥺\n"
            "send me a video, photo, gif, or sticker and i'll create one right away 🌸🫶"
        )
        return

    text = "*hic* here's your personal sticker pack(s)~!! 🥺🌸\n\n"
    buttons = []

    if static_pack:
        text += f"🖼 static stickers: https://t.me/addstickers/{static_pack}\n"
        buttons.append(InlineKeyboardButton("🖼 static pack", url=f"https://t.me/addstickers/{static_pack}"))

    if video_pack:
        text += f"🎥 video stickers: https://t.me/addstickers/{video_pack}\n"
        buttons.append(InlineKeyboardButton("🎥 video pack", url=f"https://t.me/addstickers/{video_pack}"))

    text += "\nadd them to telegram and use your stickers everywhere 🫶"
    kb = InlineKeyboardMarkup([buttons]) if buttons else None
    await update.message.reply_text(text, reply_markup=kb)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if (
        not update.effective_chat
        or update.effective_chat.type != "private"
        or not update.message
    ):
        return
    await update.message.reply_text(
        "/start - begin\n"
        "/mypack - get your sticker pack link(s)\n"
        "/help - show commands\n"
        "/delulu - get a delulu thought"
    )


async def cmd_delulu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if (
        not update.effective_chat
        or update.effective_chat.type != "private"
        or not update.message
    ):
        return
    await update.message.reply_text(random.choice(DELULU))


async def cmd_kang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or update.effective_chat.type != "private":
        return
    if not update.effective_user or not update.message:
        return

    replied = update.message.reply_to_message
    if not replied or not replied.sticker:
        await update.message.reply_text(
            "*hic* reply to a sticker with /kang to steal it into your pack~!! 🥺🌸"
        )
        return

    await save_user(update.effective_user.id)
    sticker = replied.sticker
    is_anim = bool(sticker.is_animated or sticker.is_video)

    status_msg = await update.message.reply_text(random.choice(PROC))

    try:
        file = await context.bot.get_file(sticker.file_id)

        if is_anim:
            with tempfile.TemporaryDirectory() as tmp:
                dst = os.path.join(tmp, "stk.webm")
                await file.download_to_drive(dst)
                if os.path.getsize(dst) > 262144:
                    await status_msg.edit_text("oh no... *hic* video too big (max 256kb) 🥺")
                    return
                sticker_input_obj = InputSticker(
                    sticker=open(dst, "rb").read(), emoji_list=["🌸"], format="video"
                )
                pack_name = await _kang_add(context, update.effective_user, sticker_input_obj, is_anim)
        else:
            byte_arr = await file.download_as_bytearray()
            webp_bytes = await convert_image_to_webp(bytes(byte_arr))
            sticker_input_obj = InputSticker(
                sticker=webp_bytes, emoji_list=["🌸"], format="static"
            )
            pack_name = await _kang_add(context, update.effective_user, sticker_input_obj, is_anim)

        await status_msg.delete()

        if pack_name:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🌸 view your pack", url=f"https://t.me/addstickers/{pack_name}")
            ]])
            await update.message.reply_text("kanged~!! sticker stolen into your pack 🥺🫶", reply_markup=kb)
        else:
            await update.message.reply_text(random.choice(FAIL))

    except Exception as e:
        print(f"Kang Error: {e}")
        await status_msg.edit_text(random.choice(FAIL))


# ═══════════════════════════════════════════════════════════════════════════════
#  Bot Command Sync
# ═══════════════════════════════════════════════════════════════════════════════

async def set_bot_commands(app: Application):
    await app.bot.set_my_commands(
        [
            BotCommand("start", "Start the bot 🌸"),
            BotCommand("mypack", "Get your sticker pack link 🫶"),
            BotCommand("delulu", "Get a delulu thought 🥺"),
            BotCommand("help", "Show commands"),
        ]
    )
    print("✅ Commands synced to Telegram UI!")


# ═══════════════════════════════════════════════════════════════════════════════
#  Media Conversion
# ═══════════════════════════════════════════════════════════════════════════════

async def convert_video_to_webm(src: str, dst: str):
    if not shutil.which("ffmpeg"):
        print("⚠️ FFmpeg NOT FOUND. Creating dummy file for testing...")
        with open(dst, "wb") as f:
            f.write(b"dummy")
        return

    cmd_convert = [
        "ffmpeg", "-y", "-i", src,
        "-t", "3",
        "-vf", "scale=w=if(gt(a\\,1)\\,512\\,-2):h=if(gt(a\\,1)\\,-2\\,512)",
        "-c:v", "libvpx-vp9",
        "-pix_fmt", "yuva420p",
        "-b:v", "256k",
        "-crf", "32",
        "-row-mt", "1",
        "-cpu-used", "8",
        "-deadline", "realtime",
        "-an",
        "-f", "webm",
        dst,
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd_convert,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await process.communicate()


async def convert_image_to_webp(in_bytes: bytes) -> bytes:
    loop = asyncio.get_event_loop()

    def process():
        with Image.open(io.BytesIO(in_bytes)) as img:
            img = img.convert("RGBA")
            img.thumbnail((512, 512), Image.Resampling.LANCZOS)
            out_io = io.BytesIO()
            img.save(out_io, "WEBP", quality=95)
            return out_io.getvalue()

    return await loop.run_in_executor(None, process)


# ═══════════════════════════════════════════════════════════════════════════════
#  Pack Management
# ═══════════════════════════════════════════════════════════════════════════════

async def _kang_add(context, user, sticker_input: InputSticker, is_animated: bool) -> str | None:
    """Add a pre-built InputSticker directly into the user's pack."""
    bot_username = context.bot.username.lstrip("@").lower()
    if is_animated:
        short_name = f"packv_{user.id}_by_{bot_username}"
        get_fn = get_video_pack
        save_fn = save_video_pack
    else:
        short_name = f"pack_{user.id}_by_{bot_username}"
        get_fn = get_pack
        save_fn = save_pack
    print(f"🔧 Pack name: {short_name}")

    existing_pack = await get_fn(user.id)

    try:
        if existing_pack:
            await context.bot.add_sticker_to_set(
                user_id=user.id,
                name=short_name,
                sticker=sticker_input,
            )
        else:
            safe_name = re.sub(r"[^\w\s]", "", user.first_name or "User")[:28].strip()
            title = f"{safe_name}'s Pack" if safe_name else "My Pack"
            try:
                await context.bot.create_new_sticker_set(
                    user_id=user.id,
                    name=short_name,
                    title=title,
                    stickers=[sticker_input],
                    sticker_type="regular",
                )
                await save_fn(user.id, short_name)
                print(f"✅ Created new pack '{short_name}' for user {user.id}")
            except TelegramError as te:
                err = str(te).lower()
                if "already occupied" in err or "name is occupied" in err:
                    await save_fn(user.id, short_name)
                    await context.bot.add_sticker_to_set(
                        user_id=user.id,
                        name=short_name,
                        sticker=sticker_input,
                    )
                    print(f"♻️ Re-linked existing pack '{short_name}' for user {user.id}")
                else:
                    raise te
        return short_name
    except TelegramError as te:
        print(f"Kang TelegramError for user {user.id}: {te}")
        return None
    except Exception as e:
        print(f"Kang Pack Error for user {user.id}: {e}")
        return None

async def add_to_personal_pack(context, user, sticker_data, is_animated: bool) -> str | None:
    """
    Add a sticker to the user's personal pack.
    - Static stickers go into pack_{user.id}_by_{bot_username}
    - Video/animated stickers go into packv_{user.id}_by_{bot_username}
    Telegram does not allow mixing static and video stickers in one pack,
    so we maintain two separate packs transparently.
    """
    bot_username = context.bot.username.lstrip("@").lower()
    if is_animated:
        short_name = f"packv_{user.id}_by_{bot_username}"
        fmt = "video"
        get_fn = get_video_pack
        save_fn = save_video_pack
    else:
        short_name = f"pack_{user.id}_by_{bot_username}"
        fmt = "static"
        get_fn = get_pack
        save_fn = save_pack
    print(f"🔧 Pack name: {short_name}")

    # Build the InputSticker object
    try:
        if isinstance(sticker_data, str):
            with open(sticker_data, "rb") as f:
                raw = f.read()
            sticker_input = InputSticker(sticker=raw, emoji_list=["🌸"], format=fmt)
        else:
            sticker_input = InputSticker(sticker=sticker_data, emoji_list=["🌸"], format=fmt)
    except Exception as e:
        print(f"Pack InputSticker Error: {e}")
        return None

    existing_pack = await get_fn(user.id)

    try:
        if existing_pack:
            # Pack already exists — just append
            await context.bot.add_sticker_to_set(
                user_id=user.id,
                name=short_name,
                sticker=sticker_input,
            )
        else:
            # Create a brand new pack for this user
            safe_name = re.sub(r"[^\w\s]", "", user.first_name or "User")[:28].strip()
            title = f"{safe_name}'s Pack" if safe_name else "My Pack"
            try:
                await context.bot.create_new_sticker_set(
                    user_id=user.id,
                    name=short_name,
                    title=title,
                    stickers=[sticker_input],
                    sticker_type="regular",
                )
                await save_fn(user.id, short_name)
                print(f"✅ Created new pack '{short_name}' for user {user.id}")
            except TelegramError as te:
                err = str(te).lower()
                if "already occupied" in err or "name is occupied" in err:
                    # Pack exists on Telegram but not in DB — re-link and add sticker
                    await save_fn(user.id, short_name)
                    await context.bot.add_sticker_to_set(
                        user_id=user.id,
                        name=short_name,
                        sticker=sticker_input,
                    )
                    print(f"♻️ Re-linked existing pack '{short_name}' for user {user.id}")
                else:
                    raise te

        return short_name

    except TelegramError as te:
        print(f"Pack TelegramError for user {user.id}: {te}")
        return None
    except Exception as e:
        print(f"Pack Error for user {user.id}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Core Processing Logic
# ═══════════════════════════════════════════════════════════════════════════════

async def _finish_sticker(update, context, sticker_data, is_animated: bool, status_msg):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    # File size guard for video stickers
    if is_animated and isinstance(sticker_data, str):
        if os.path.exists(sticker_data) and os.path.getsize(sticker_data) > 262144:
            await status_msg.edit_text("oh no... *hic* video too big (max 256kb) 🥺 try a shorter clip!!")
            return

    # Send the converted sticker back to the user
    try:
        data_to_send = open(sticker_data, "rb") if isinstance(sticker_data, str) else io.BytesIO(sticker_data)
        await msg.reply_sticker(sticker=data_to_send)
        if isinstance(sticker_data, str):
            data_to_send.close()
        await status_msg.delete()
    except Exception as e:
        await status_msg.edit_text(random.choice(FAIL))
        print(f"Send Error: {e}")
        return

    # Log the sticker to the log channel
    if STICKER_LOG_CHANNEL_ID:
        try:
            log_data = open(sticker_data, "rb") if isinstance(sticker_data, str) else io.BytesIO(sticker_data)
            await context.bot.send_sticker(
                chat_id=int(STICKER_LOG_CHANNEL_ID),
                sticker=log_data,
            )
            if isinstance(sticker_data, str):
                log_data.close()
        except Exception as e:
            print(f"Log Send Error: {e}")

    # Add to personal pack
    pack_name = await add_to_personal_pack(context, user, sticker_data, is_animated)
    if pack_name:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🌸 view your pack", url=f"https://t.me/addstickers/{pack_name}")
        ]])
        await msg.reply_text("added to your pack~!! 🥺🫶", reply_markup=kb)


# ═══════════════════════════════════════════════════════════════════════════════
#  Media Handler
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not update.effective_chat or update.effective_chat.type != "private":
        return
    if not update.effective_user:
        return

    await save_user(update.effective_user.id)

    video_target = (
        msg.video
        or msg.animation
        or (
            msg.document
            if msg.document and (msg.document.mime_type or "").startswith("video/")
            else None
        )
    )
    photo_target = msg.photo[-1] if msg.photo else None
    sticker_target = msg.sticker if msg.sticker else None

    status_msg = await msg.reply_text(random.choice(PROC))

    try:
        if video_target:
            with tempfile.TemporaryDirectory() as tmp:
                src = os.path.join(tmp, "in.mp4")
                dst = os.path.join(tmp, "out.webm")
                file = await context.bot.get_file(video_target.file_id)
                await file.download_to_drive(src)
                await convert_video_to_webm(src, dst)
                await _finish_sticker(update, context, dst, True, status_msg)

        elif photo_target:
            file = await context.bot.get_file(photo_target.file_id)
            byte_arr = await file.download_as_bytearray()
            webp_bytes = await convert_image_to_webp(bytes(byte_arr))
            await _finish_sticker(update, context, webp_bytes, False, status_msg)

        elif sticker_target:
            is_anim = bool(sticker_target.is_animated or sticker_target.is_video)
            file = await context.bot.get_file(sticker_target.file_id)
            byte_arr = await file.download_as_bytearray()
            if not is_anim:
                webp_bytes = await convert_image_to_webp(bytes(byte_arr))
                await _finish_sticker(update, context, webp_bytes, False, status_msg)
            else:
                with tempfile.TemporaryDirectory() as tmp:
                    dst = os.path.join(tmp, "stk.webm")
                    await file.download_to_drive(dst)
                    await _finish_sticker(update, context, dst, True, status_msg)

    except Exception as e:
        print(f"General Error: {e}")
        await status_msg.edit_text(random.choice(FAIL))


# ═══════════════════════════════════════════════════════════════════════════════
#  Bootstrap
# ═══════════════════════════════════════════════════════════════════════════════

async def post_init(application: Application):
    commands = [
        BotCommand("start", "Start the bot 🌸"),
        BotCommand("mypack", "Get your sticker pack link 🫶"),
        BotCommand("kang", "Reply to a sticker to steal it into your pack 🌸"),
        BotCommand("delulu", "Get a delulu thought 🥺"),
        BotCommand("help", "Show commands"),
    ]
    await application.bot.set_my_commands(commands)
    print("✅ Commands synced to Telegram UI!")


def main():
    if not TOKEN:
        print("❌ Set TOKEN in config/env")
        return

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("mypack", cmd_mypack))
    app.add_handler(CommandHandler("kang", cmd_kang))
    app.add_handler(CommandHandler("delulu", cmd_delulu))
    app.add_handler(CommandHandler("help", cmd_help))

    media_filter = (
        filters.VIDEO | filters.ANIMATION | filters.PHOTO |
        filters.Sticker.ALL | filters.Document.VIDEO
    )
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & media_filter, handle_media))

    print("🌸 Sticker Bot is in Turbo Mode~!!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
