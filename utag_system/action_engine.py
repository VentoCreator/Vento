"""
Telegram ChatAction engine and privacy helpers (flat settings model).
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional, Tuple

from pyrogram import Client
from pyrogram.errors import FloodWait

logger = logging.getLogger(__name__)

# (settings_key, display_label, optional pyrogram action name)
FLAT_ACTION_TOGGLES: List[Tuple[str, str, Optional[str]]] = [
    ("action_status_typing", "Typing", "typing"),
    ("action_status_playing", "Playing game", "playing"),
    ("action_status_record_audio", "Recording audio", "record_audio"),
    ("action_status_upload_media", "Uploading photo/video/document", "upload_photo"),
    ("action_status_choose_sticker", "Choosing sticker", "choose_sticker"),
    ("privacy_online_status", "Online status", None),
    ("privacy_mark_as_read", "Mark as read", None),
]

SETTINGS_KEY_GHOST_READ = "privacy_ghost_read"  # legacy alias
SETTINGS_KEY_MARK_AS_READ = "privacy_mark_as_read"
SETTINGS_KEY_ONLINE_STATUS = "privacy_online_status"


def toggle_on(settings: dict, key: str) -> bool:
    """Return ON/OFF for a flat toggle key (handles legacy ghost-read storage)."""
    if key == SETTINGS_KEY_MARK_AS_READ:
        if SETTINGS_KEY_MARK_AS_READ in settings:
            return bool(settings.get(SETTINGS_KEY_MARK_AS_READ, True))
        # Legacy: ghost_read True => mark_as_read OFF
        return not bool(settings.get(SETTINGS_KEY_GHOST_READ, False))
    return bool(settings.get(key, False))


def set_toggle(settings: dict, key: str, enabled: bool) -> None:
    if key == SETTINGS_KEY_MARK_AS_READ:
        settings[SETTINGS_KEY_MARK_AS_READ] = enabled
        settings[SETTINGS_KEY_GHOST_READ] = not enabled
        return
    settings[key] = enabled


def flip_toggle(settings: dict, key: str) -> None:
    set_toggle(settings, key, not toggle_on(settings, key))


def get_toggle_by_key(key: str) -> Optional[Tuple[str, str, Optional[str]]]:
    for item in FLAT_ACTION_TOGGLES:
        if item[0] == key:
            return item
    return None


def toggle_button_label(label: str, enabled: bool) -> str:
    state = "🟢 ON" if enabled else "🔴 OFF"
    return f"{label}: {state}"


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
            logger.warning("[ActionEngine] FloodWait %ss action=%s chat=%s", e.value, action, chat_id)
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
            logger.error("[ActionEngine] send_action failed action=%s: %s", action, exc)
            return False

    @staticmethod
    async def send_utag_typing(client: Client, chat_id: int, settings: dict) -> None:
        """UTag-only typing (controlled by utag_typing_status in UTag settings)."""
        if not settings.get("utag_typing_status", True):
            return
        await ActionEngine.send_action(client, chat_id, "typing", duration=0.5)

    @staticmethod
    async def safe_mark_as_read(
        client: Client,
        chat_id: int,
        settings: dict,
        max_id: int = 0,
    ) -> bool:
        if not toggle_on(settings, SETTINGS_KEY_MARK_AS_READ):
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
            logger.error("[ActionEngine] read_chat_history failed: %s", exc)
            return False

    @staticmethod
    async def apply_online_presence(client: Client, settings: dict) -> None:
        if not toggle_on(settings, SETTINGS_KEY_ONLINE_STATUS):
            try:
                from pyrogram.raw.functions.account import UpdateStatus

                await client.invoke(UpdateStatus(offline=True))
            except Exception as exc:
                logger.error("[ActionEngine] apply offline failed: %s", exc)
            return
        try:
            from pyrogram.raw.functions.account import UpdateStatus

            await client.invoke(UpdateStatus(offline=False))
        except Exception as exc:
            logger.error("[ActionEngine] apply online failed: %s", exc)
