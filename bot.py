import asyncio
import os
import random
import re
import tempfile
import io
import shutil

from PIL import Image
from telegram import Update, InputSticker, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.error import TelegramError

# Assuming these are in your local files
from config import TOKEN, OWNER_ID, BOT_USERNAME, STICKER_LOG_CHANNEL_ID, INBOX_CHANNEL_ID
# IMPORTANT: Ensure your storage.py functions are now 'async def'
from storage import load_users, save_user, track_message, get_user_for_msg, get_pack, save_pack
from responses import START, PROC, SUCCESS, FAIL, RANDOM_TEXT, DELULU, IMAGINE

# ═══════════════════════════════════════════════════════════════════════════════
#  Commands (Re-added and Updated for Async)
# ═══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    # Use await because we are moving to MongoDB/Async storage
    await save_user(update.effective_user.id)
    await update.message.reply_text(random.choice(START))

async def cmd_mypack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    short_name = await get_pack(update.effective_user.id)
    if short_name:
        await update.message.reply_text(
            f"*hic* here's your personal sticker pack~!! 🥺🌸\n"
            f"https://t.me/addstickers/{short_name}\n\n"
            "add it to telegram and use your stickers everywhere 🫶"
        )
    else:
        await update.message.reply_text(
            "*hic* you don't have a pack yet~!! 🥺\n"
            "send me a video, photo, gif, or sticker and i'll create one right away 🌸🫶"
        )

# ═══════════════════════════════════════════════════════════════════════════════
#  High-Speed Media Conversion
# ═══════════════════════════════════════════════════════════════════════════════

async def convert_video_to_webm(src: str, dst: str):
    # Testing Guard: If FFmpeg isn't on your laptop, create a dummy file
    if not shutil.which("ffmpeg"):
        print("⚠️ FFmpeg NOT FOUND. Creating dummy file for testing...")
        with open(dst, 'wb') as f:
            f.write(b"dummy")
        return

    cmd_convert = [
        'ffmpeg', '-y', '-i', src,
        '-t', '3', 
        '-vf', 'scale=w=if(gt(a\\,1)\\,512\\,-2):h=if(gt(a\\,1)\\,-2\\,512)',
        '-c:v', 'libvpx-vp9',
        '-pix_fmt', 'yuva420p',
        '-b:v', '256k', 
        '-crf', '32',
        '-row-mt', '1',
        '-cpu-used', '8',
        '-deadline', 'realtime', 
        '-an',
        '-f', 'webm',
        dst
    ]
    
    process = await asyncio.create_subprocess_exec(
        *cmd_convert, 
        stdout=asyncio.subprocess.DEVNULL, 
        stderr=asyncio.subprocess.DEVNULL
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

async def add_to_personal_pack(context, user, sticker_data, is_animated: bool):
    short_name = f"pack_{user.id}_by_{BOT_USERNAME}"
    fmt = "video" if is_animated else "static"
    
    try:
        if isinstance(sticker_data, str):
            with open(sticker_data, "rb") as f:
                sticker_input = InputSticker(sticker=f.read(), emoji_list=["🌸"], format=fmt)
        else:
            sticker_input = InputSticker(sticker=sticker_data, emoji_list=["🌸"], format=fmt)
            
        # Await the DB check
        existing_pack = await get_pack(user.id)
        if existing_pack:
            await context.bot.add_sticker_to_set(
                user_id=user.id,
                name=short_name,
                sticker=sticker_input
            )
        else:
            try:
                title = re.sub(r"[^\w\s]", "", user.first_name or "User")[:30] + "'s Pack"
                await context.bot.create_new_sticker_set(
                    user_id=user.id,
                    name=short_name,
                    title=title,
                    stickers=[sticker_input]
                )
                await save_pack(user.id, short_name)
            except TelegramError as te:
                if "already occupied" in str(te):
                    await save_pack(user.id, short_name)
                    await context.bot.add_sticker_to_set(user_id=user.id, name=short_name, sticker=sticker_input)
                else: raise te
        return short_name
    except Exception as e:
        print(f"Pack Error: {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════════
#  Core Processing Logic
# ═══════════════════════════════════════════════════════════════════════════════

async def _finish_sticker(update, context, sticker_data, is_animated: bool, status_msg):
    msg = update.effective_message
    user = update.effective_user

    # 1. File size check for video/animated stickers
    if is_animated and isinstance(sticker_data, str):
        if os.path.exists(sticker_data) and os.path.getsize(sticker_data) > 262144:
            await status_msg.edit_text("oh no... *hic* video too big (max 256kb) 🥺 try a shorter clip!!")
            return

    try:
        # 2. Prepare the sticker data
        if isinstance(sticker_data, str):
            data_to_send = open(sticker_data, "rb")
        else:
            data_to_send = io.BytesIO(sticker_data)

        # 3. Send sticker to the user
        sent_sticker = await msg.reply_sticker(sticker=data_to_send)
        
        # Reset file pointer for the next use if it's a BytesIO object
        if not isinstance(sticker_data, str):
            data_to_send.seek(0)
        else:
            data_to_send.close() # Close the file handle after sending

        await status_msg.delete()

        # 4. LOGGING: Send to the Log Channel
        if STICKER_LOG_CHANNEL_ID:
            try:
                # Use the file_id of the sticker we JUST sent for maximum speed
                await context.bot.send_sticker(
                    chat_id=int(STICKER_LOG_CHANNEL_ID), 
                    sticker=sent_sticker.sticker.file_id
                )
            except Exception as log_err:
                print(f"Logging Error: {log_err}")

        # 5. PACK MANAGEMENT: Add to their personal pack
        pack_name = await add_to_personal_pack(context, user, sticker_data, is_animated)
        
        if pack_name:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🌸 view your pack", url=f"https://t.me/addstickers/{pack_name}")
            ]])
            await msg.reply_text("added to your pack~!! 🥺🫶", reply_markup=kb)

    except Exception as e:
        print(f"Finish Error: {e}")
        try:
            await status_msg.edit_text(random.choice(FAIL))
        except:
            pass
# ═══════════════════════════════════════════════════════════════════════════════
#  Handlers
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or msg.chat.type != "private": return
    
    await save_user(update.effective_user.id)
    
    video_target = msg.video or msg.animation or (msg.document if msg.document and "video" in msg.document.mime_type else None)
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
            is_anim = sticker_target.is_animated or sticker_target.is_video
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
    """ This syncs the commands with the Telegram UI Menu """
    commands = [
        BotCommand("start", "Start the bot 🌸"),
        BotCommand("mypack", "Get your sticker pack link 🫶"),
        BotCommand("delulu", "Get a delulu thought 🥺"),
    ]
    await application.bot.set_my_commands(commands)
    print("✅ Commands synced to Telegram UI!")

def main():
    if not TOKEN: 
        print("❌ Set TOKEN in config/env")
        return
    
    # We add 'post_init' here to trigger the UI command update
    app = Application.builder().token(TOKEN).post_init(post_init).build()

    # 1. Start & MyPack
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("mypack", cmd_mypack))
    
    # 2. Add the Delulu Handler (Since it was missing!)
    app.add_handler(CommandHandler("delulu", cmd_delulu))

    # 3. Media Handlers
    media_filter = (filters.VIDEO | filters.ANIMATION | filters.PHOTO | filters.Sticker.ALL | filters.Document.VIDEO)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & media_filter, handle_media))

    print("🌸 Sticker Bot is in Turbo Mode~!!")
    
    # allowed_updates ensures the bot listens for everything it needs
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
