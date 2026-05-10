import os
from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_URL

client = AsyncIOMotorClient(MONGO_URL)
db = client["sticker_bot_db"]

# Collections
users_col = db["users"]
packs_col = db["packs"]
msg_track_col = db["message_tracking"]

async def save_user(user_id: int):
    await users_col.update_one({"user_id": user_id}, {"$set": {"user_id": user_id}}, upsert=True)

async def get_pack(user_id: int):
    doc = await packs_col.find_one({"user_id": user_id})
    return doc["pack_name"] if doc else None

async def save_pack(user_id: int, pack_name: str):
    await packs_col.update_one({"user_id": user_id}, {"$set": {"pack_name": pack_name}}, upsert=True)

async def load_users():
    cursor = users_col.find({}, {"user_id": 1})
    return {doc["user_id"] async for doc in cursor}

# Forwarding / Inbox tracking
def track_message(msg_id, user_id):
    # This can stay sync or async; for simplicity here:
    asyncio.create_task(msg_track_col.insert_one({"msg_id": msg_id, "user_id": user_id}))

async def get_user_for_msg(msg_id):
    doc = await msg_track_col.find_one({"msg_id": msg_id})
    return doc["user_id"] if doc else None
