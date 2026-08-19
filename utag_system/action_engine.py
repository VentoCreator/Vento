"""
Telegram ChatAction engine, privacy controls, and ghost-mode helpers.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional, Tuple

from pyrogram import Client
from pyrogram.errors import FloodWait

logger = logging.getLogger(__name__)

# (settings_key, label, pyrogram_action_name)
CHAT_ACTION_CATALOG: Dict[str, List[Tuple[str, str, str]]] = {
    "text": [
        ("action_status_typing", "⌨️ Typing", "typing"),
    ],
    "media": [
        ("action_status_upload_photo", "📷 Rasm yuklash", "upload_photo"),
        ("action_status_record_video", "🎬 Video yozish", "record_video"),
        ("action_status_upload_video", "📹 Video yuklash", "upload_video"),
    ],
    "audio": [
        ("action_status_record_audio", "🎙 Audio yozish", "record_audio"),
        ("action_status_upload_audio", "🔊 Audio yuklash", "upload_audio"),
    ],
    "file_location": [
        ("action_status_upload_document", "📄 Hujjat yuklash", "upload_document"),
        ("action_status_find_location", "📍 Joylashuv", "find_location"),
    ],
    "video_note": [
        ("action_status_record_video_note", "⭕ Video xabar yozish", "record_video_note"),
        ("action_status_upload_video_note", "🔵 Video xabar yuklash", "upload_video_note"),
    ],
    "interactive": [
        ("action_status_choose_sticker", "🎨 Sticker tanlash", "choose_sticker"),
        ("action_status_playing", "🎮 O'yin o'ynash", "playing"),
        ("action_status_speaking", "🗣 Ovozli chat", "speaking"),
    ],
}

CATEGORY_LABELS = {
    "text": "📝 Matn",
    "media": "🖼 Media",
    "audio": "🎵 Audio",
    "file_location": "📁 Fayl & Joy",
    "video_note": "⭕ Video xabar",
    "interactive": "🎮 Interaktiv",
}

# Legacy keys migrated from the first Action Status panel
LEGACY_ACTION_KEY_MAP = {
    "action_status_record_voice": "action_status_record_audio",
    "action_status_choose_sticker": "action_status_choose_sticker",
    "action_status_playing": "action_status_playing",
}

PRIVACY_ONLINE_MODES = {
    "normal": "⚪ Normal (Telegram default)",
    "offline": "👻 Offline (Ghost)",
    "online": "🟢 Doim online",
}

SETTINGS_KEY_ACTIVE_ACTION = "action_status_active_action"
SETTINGS_KEY_GHOST_READ = "privacy_ghost_read"
SETTINGS_KEY_ONLINE_MODE = "privacy_online_mode"


def _all_actions() -> List[Tuple[str, str, str]]:
    items: List[Tuple[str, str, str]] = []
    for group in CHAT_ACTION_CATALOG.values():
        items.extend(group)
    return items


def get_action_by_key(key: str) -> Optional[Tuple[str, str, str]]:
    for item in _all_actions():
        if item[0] == key:
            return item
    migrated = LEGACY_ACTION_KEY_MAP.get(key)
    if migrated:
        return get_action_by_key(migrated)
    return None


def get_action_by_name(action_name: str) -> Optional[Tuple[str, str, str]]:
    for item in _all_actions():
        if item[2] == action_name:
            return item
    return None


def is_action_enabled(settings: dict, key: str) -> bool:
    migrated = LEGACY_ACTION_KEY_MAP.get(key, key)
    if migrated in settings:
        return bool(settings.get(migrated))
    return bool(settings.get(key, False))


def get_enabled_actions(settings: dict) -> List[Tuple[str, str, str]]:
    enabled = []
    for item in _all_actions():
        if is_action_enabled(settings, item[0]):
            enabled.append(item)
    return enabled


def get_active_action(settings: dict) -> Optional[str]:
    return settings.get(SETTINGS_KEY_ACTIVE_ACTION)


def status_label(enabled: bool) -> str:
    return "✅ Yoqilgan" if enabled else "❌ O'chirilgan"


class ActionEngine:
    """Central ChatAction + privacy controller."""

    @staticmethod
    async def send_action(
        client: Client,
        chat_id: int,
        action: str,
        duration: float = 0.0,
    ) -> bool:
        try:
            await client.send_chat_action(chat_id, action)
            if duration > 0:
                await asyncio.sleep(duration)
            return True
        except FloodWait as e:
            logger.warning("[ActionEngine] FloodWait %ss for action=%s chat=%s", e.value, action, chat_id)
            await asyncio.sleep(e.value + 1)
            try:
                await client.send_chat_action(chat_id, action)
                if duration > 0:
                    await asyncio.sleep(duration)
                return True
            except Exception as exc:
                logger.error("[ActionEngine] Retry failed action=%s: %s", action, exc)
                return False
        except Exception as exc:
            logger.error("[ActionEngine] send_action failed action=%s chat=%s: %s", action, chat_id, exc)
            return False

    @staticmethod
    async def cancel_action(client: Client, chat_id: int) -> bool:
        return await ActionEngine.send_action(client, chat_id, "cancel")

    @staticmethod
    async def send_utag_typing(client: Client, chat_id: int, settings: dict) -> None:
        """UTag-only typing hook (controlled by utag_typing_status)."""
        if not settings.get("utag_typing_status", True):
            return
        await ActionEngine.send_action(client, chat_id, "typing", duration=0.5)

    @staticmethod
    async def send_active_or_enabled(
        client: Client,
        chat_id: int,
        settings: dict,
        duration: float = 2.0,
    ) -> Optional[str]:
        active = get_active_action(settings)
        if active:
            ok = await ActionEngine.send_action(client, chat_id, active, duration)
            return active if ok else None
        enabled = get_enabled_actions(settings)
        if len(enabled) == 1:
            ok = await ActionEngine.send_action(client, chat_id, enabled[0][2], duration)
            return enabled[0][2] if ok else None
        return None

    @staticmethod
    async def safe_mark_as_read(
        client: Client,
        chat_id: int,
        settings: dict,
        max_id: int = 0,
    ) -> bool:
        if settings.get(SETTINGS_KEY_GHOST_READ, False):
            logger.debug("[ActionEngine] Ghost mode: skip mark_as_read chat=%s", chat_id)
            return False
        try:
            await client.read_chat_history(chat_id, max_id=max_id)
            return True
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
            try:
                await client.read_chat_history(chat_id, max_id=max_id)
                return True
            except Exception as exc:
                logger.error("[ActionEngine] read_chat_history retry failed: %s", exc)
                return False
        except Exception as exc:
            logger.error("[ActionEngine] read_chat_history failed chat=%s: %s", chat_id, exc)
            return False

    @staticmethod
    async def apply_online_presence(client: Client, settings: dict) -> None:
        mode = settings.get(SETTINGS_KEY_ONLINE_MODE, "normal")
        if mode == "normal":
            return
        try:
            from pyrogram.raw.functions.account import UpdateStatus

            offline = mode == "offline"
            await client.invoke(UpdateStatus(offline=offline))
            logger.debug("[ActionEngine] Online presence applied mode=%s offline=%s", mode, offline)
        except Exception as exc:
            logger.error("[ActionEngine] apply_online_presence failed: %s", exc)

    @staticmethod
    async def apply_privacy_after_action(client: Client, settings: dict) -> None:
        """Re-apply ghost/offline presence after an API action if configured."""
        mode = settings.get(SETTINGS_KEY_ONLINE_MODE, "normal")
        if mode in ("offline", "online"):
            await ActionEngine.apply_online_presence(client, settings)
