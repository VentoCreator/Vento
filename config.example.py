"""
config.example.py — PRODUCTION-SAFE TEMPLATE

This is the deploy-time template for the app configuration module.
It mirrors the full structure and public API of the local `config.py`
so that every `from config import ...` in the codebase keeps working.

It contains ZERO real credentials. All secret values are read from
environment variables (Railway Variables) at runtime.

Deploy usage (Railway Start Command):
    cp config.example.py config.py && python main.py

The real local `config.py` is git-ignored and is NEVER committed.
"""

import os
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
DATA_DIR = os.path.join(BASE_DIR, "data")
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")
DATABASE_DIR = os.path.join(DATA_DIR, "database")
DB_PATH = os.path.join(DATABASE_DIR, "bot_database.db")

os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(DATABASE_DIR, exist_ok=True)


def load_config():
    # NOTE: values are read from environment variables only.
    # Environment variables take precedence; no hardcoded secrets here.
    config = {
        "API_ID": int(os.getenv("API_ID", 0)),
        "API_HASH": os.getenv("API_HASH", ""),
        "BOT_TOKEN": os.getenv("BOT_TOKEN", ""),
        "SUPER_ADMIN_ID": int(os.getenv("SUPER_ADMIN_ID", 0)),
        "SECOND_ADMIN_ID": int(os.getenv("SECOND_ADMIN_ID", 0)),
        "ADMIN_REPORT_CHAT_ID": int(os.getenv("ADMIN_REPORT_CHAT_ID", 0)),
    }

    # Optional JSON overlay (config.json is git-ignored and normally absent).
    # Env values always win when present.
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            file_config = json.load(f)
            for key, value in file_config.items():
                if key not in os.environ or os.getenv(key) is None:
                    config[key] = value

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)
    return config


config = load_config()

API_ID = config.get("API_ID")
API_HASH = config.get("API_HASH")
BOT_TOKEN = config.get("BOT_TOKEN")
SUPER_ADMIN_ID = config.get("SUPER_ADMIN_ID", 0)
SECOND_ADMIN_ID = config.get("SECOND_ADMIN_ID", 0)
ADMIN_REPORT_CHAT_ID = config.get("ADMIN_REPORT_CHAT_ID", 0)
OWNER_ID = SUPER_ADMIN_ID  # Owner asosiy admin
ADMIN_IDS = [SUPER_ADMIN_ID, SECOND_ADMIN_ID]


async def load_admin_ids_from_db():
    """Bazadan admin ID larini yuklash"""
    global ADMIN_IDS
    from database import get_all_admins
    admins = await get_all_admins()
    ADMIN_IDS = [admin["admin_id"] for admin in admins]
    return ADMIN_IDS


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


async def has_permission(user_id: int, permission: str) -> bool:
    """Adminning ma'lum huquqiga ega ekanligini tekshirish"""
    if not is_admin(user_id):
        return False
    if is_owner(user_id):
        return True  # Owner har doim barcha huquqlarga ega
    from database import get_admin_info
    admin_info = await get_admin_info(user_id)
    if not admin_info:
        return False
    return admin_info.get(permission, False)


async def can_broadcast(user_id: int) -> bool:
    """Broadcast huquqini tekshirish"""
    return await has_permission(user_id, "can_broadcast")


async def can_ban(user_id: int) -> bool:
    """Ban huquqini tekshirish"""
    return await has_permission(user_id, "can_ban")


async def can_clear_db(user_id: int) -> bool:
    """DB tozalash huquqini tekshirish"""
    return await has_permission(user_id, "can_clear_db")


async def can_manage_users(user_id: int) -> bool:
    """User boshqarish huquqini tekshirish"""
    return await has_permission(user_id, "can_manage_users")


async def can_add_admin(user_id: int) -> bool:
    """Admin qo'shish huquqini tekshirish"""
    return await has_permission(user_id, "can_add_admin")


user_states = {}
login_data = {}
user_clients = {}
stop_flags = {}
pause_flags = {}
user_settings = {}
user_custom_commands = {}
bot_client = None
