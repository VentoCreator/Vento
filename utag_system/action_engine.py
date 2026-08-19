"""
Telegram ChatAction engine — explicit CANCEL overrides and 4s action loops.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Dict, List, Optional, Tuple, Union

from pyrogram import Client
from pyrogram.enums import ChatAction
from pyrogram.errors import FloodWait

logger = logging.getLogger(__name__)

ACTION_LOOP_INTERVAL = 4.0  # Telegram expires chat actions after ~5 seconds

# (settings_key, display_label, primary ChatAction or None for privacy toggles)
FLAT_ACTION_TOGGLES: List[Tuple[str, str, Optional[ChatAction]]] = [
    ("action_status_typing", "Typing", ChatAction.TYPING),
    ("action_status_playing", "Playing game", ChatAction.PLAYING),
    ("action_status_record_audio", "Recording audio", ChatAction.RECORD_AUDIO),
    ("action_status_upload_media", "Uploading photo/video/document", ChatAction.UPLOAD_PHOTO),
    ("action_status_choose_sticker", "Choosing sticker", ChatAction.CHOOSE_STICKER),
    ("privacy_online_status", "Online status", None),
    ("privacy_mark_as_read", "Mark as read", None),
]

# Expanded rotation sequences for composite toggles
ACTION_ROTATION: Dict[str, List[ChatAction]] = {
    "action_status_record_audio": [
        ChatAction.RECORD_AUDIO,
        ChatAction.RECORD_VIDEO_NOTE,
    ],
    "action_status_upload_media": [
        ChatAction.UPLOAD_PHOTO,
        ChatAction.UPLOAD_VIDEO,
        ChatAction.UPLOAD_DOCUMENT,
    ],
}

CHAT_ACTION_TOGGLE_KEYS = {key for key, _, action in FLAT_ACTION_TOGGLES if action is not None}

SETTINGS_KEY_GHOST_READ = "privacy_ghost_read"
SETTINGS_KEY_MARK_AS_READ = "privacy_mark_as_read"
SETTINGS_KEY_ONLINE_STATUS = "privacy_online_status"

_keeper_registry: Dict[str, "ActionStatusKeeper"] = {}


def toggle_on(settings: dict, key: str) -> bool:
    if key == SETTINGS_KEY_MARK_AS_READ:
        if SETTINGS_KEY_MARK_AS_READ in settings:
            return bool(settings.get(SETTINGS_KEY_MARK_AS_READ, True))
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


def get_toggle_by_key(key: str) -> Optional[Tuple[str, str, Optional[ChatAction]]]:
    for item in FLAT_ACTION_TOGGLES:
        if item[0] == key:
            return item
    return None


def toggle_button_label(label: str, enabled: bool) -> str:
    state = "🟢 ON" if enabled else "🔴 OFF"
    return f"{label}: {state}"


def _action_value(action: Union[ChatAction, str]) -> str:
    return action.value if isinstance(action, ChatAction) else action


def get_enabled_chat_actions(
    settings: dict,
    utag_settings: Optional[dict] = None,
) -> List[ChatAction]:
    """Return ChatActions that should be actively shown (respects UTag typing gate)."""
    enabled: List[ChatAction] = []
    for key, _label, primary in FLAT_ACTION_TOGGLES:
        if primary is None:
            continue
        if not toggle_on(settings, key):
            continue
        if key == "action_status_typing" and utag_settings is not None:
            if not utag_settings.get("utag_typing_status", True):
                continue
        if key in ACTION_ROTATION:
            enabled.extend(ACTION_ROTATION[key])
        else:
            enabled.append(primary)
    return enabled


def any_chat_action_enabled(settings: dict, utag_settings: Optional[dict] = None) -> bool:
    return bool(get_enabled_chat_actions(settings, utag_settings))


class ActionEngine:
    """Central ChatAction controller with explicit CANCEL overrides."""

    @staticmethod
    async def send_action(
        client: Client,
        chat_id: int,
        action: Union[ChatAction, str],
        duration: float = 0.0,
    ) -> bool:
        action_str = _action_value(action)
        try:
            await client.send_chat_action(chat_id, action_str)
            if duration > 0:
                await asyncio.sleep(duration)
            return True
        except FloodWait as e:
            logger.warning(
                "[ActionEngine] FloodWait %ss action=%s chat=%s",
                e.value,
                action_str,
                chat_id,
            )
            await asyncio.sleep(e.value + 1)
            try:
                await client.send_chat_action(chat_id, action_str)
                if duration > 0:
                    await asyncio.sleep(duration)
                return True
            except Exception as exc:
                logger.error("[ActionEngine] Retry failed action=%s: %s", action_str, exc)
                return False
        except Exception as exc:
            logger.error(
                "[ActionEngine] send_action failed action=%s chat=%s: %s",
                action_str,
                chat_id,
                exc,
            )
            return False

    @staticmethod
    async def cancel_chat_action(client: Client, chat_id: int) -> bool:
        """Explicitly clear any lingering chat action on Telegram servers."""
        return await ActionEngine.send_action(client, chat_id, ChatAction.CANCEL)

    @staticmethod
    async def apply_pre_dispatch_override(
        client: Client,
        chat_id: int,
        settings: dict,
        utag_settings: Optional[dict] = None,
    ) -> None:
        """
        Before sending a message or starting a loop iteration:
        cancel active indicators when all relevant actions are OFF.
        """
        if not any_chat_action_enabled(settings, utag_settings):
            await ActionEngine.cancel_chat_action(client, chat_id)

    @staticmethod
    async def send_utag_typing(client: Client, chat_id: int, settings: dict) -> None:
        """
        UTag pre-send hook: obeys global typing toggle AND utag_typing_status.
        Keeper loop maintains the indicator; here we only enforce CANCEL when OFF.
        """
        utag_settings = settings  # same dict holds utag_typing_status
        if not any_chat_action_enabled(settings, utag_settings):
            await ActionEngine.cancel_chat_action(client, chat_id)

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

    @staticmethod
    async def cancel_on_chats(client: Client, chat_ids: List[int]) -> None:
        for chat_id in chat_ids:
            try:
                await ActionEngine.cancel_chat_action(client, chat_id)
            except Exception as exc:
                logger.debug("[ActionEngine] cancel_on_chats chat=%s: %s", chat_id, exc)

    @staticmethod
    async def on_toggle_off(
        client: Client,
        user_id: int,
        toggle_key: str,
        active_chat_ids: List[int],
    ) -> None:
        """Instantly cancel status when a chat-action toggle is turned OFF."""
        if toggle_key not in CHAT_ACTION_TOGGLE_KEYS:
            return
        await ActionEngine.stop_keepers_for_user(user_id)
        targets = list(set(active_chat_ids + [user_id]))
        await ActionEngine.cancel_on_chats(client, targets)

    @staticmethod
    def start_keeper(
        process_key: str,
        client: Client,
        chat_id: int,
        settings_provider: Callable[[], dict],
        utag_settings_provider: Optional[Callable[[], dict]] = None,
    ) -> "ActionStatusKeeper":
        ActionEngine.stop_keeper(process_key)
        keeper = ActionStatusKeeper(
            process_key=process_key,
            client=client,
            chat_id=chat_id,
            settings_provider=settings_provider,
            utag_settings_provider=utag_settings_provider,
        )
        _keeper_registry[process_key] = keeper
        keeper.start()
        return keeper

    @staticmethod
    def stop_keeper(process_key: str) -> None:
        keeper = _keeper_registry.pop(process_key, None)
        if keeper:
            keeper.stop()

    @staticmethod
    async def stop_keeper_async(process_key: str, client: Client, chat_id: int) -> None:
        ActionEngine.stop_keeper(process_key)
        try:
            await ActionEngine.cancel_chat_action(client, chat_id)
        except Exception:
            pass

    @staticmethod
    async def stop_keepers_for_user(user_id: int) -> None:
        prefix = f"{user_id}_"
        for key in list(_keeper_registry.keys()):
            if key.startswith(prefix):
                keeper = _keeper_registry.pop(key, None)
                if keeper:
                    keeper.stop()


class ActionStatusKeeper:
    """Background loop: refresh enabled ChatActions every 4 seconds during active tasks."""

    def __init__(
        self,
        process_key: str,
        client: Client,
        chat_id: int,
        settings_provider: Callable[[], dict],
        utag_settings_provider: Optional[Callable[[], dict]] = None,
    ):
        self.process_key = process_key
        self.client = client
        self.chat_id = chat_id
        self.settings_provider = settings_provider
        self.utag_settings_provider = utag_settings_provider or settings_provider
        self._stop_flag = False
        self._task: Optional[asyncio.Task] = None
        self._rotation_index = 0

    def start(self) -> None:
        self._task = asyncio.create_task(self._run_loop(), name=f"ActionKeeper:{self.process_key}")

    def stop(self) -> None:
        self._stop_flag = True
        if self._task and not self._task.done():
            self._task.cancel()

    async def _run_loop(self) -> None:
        try:
            while not self._stop_flag:
                settings = self.settings_provider()
                utag_settings = self.utag_settings_provider()
                enabled = get_enabled_chat_actions(settings, utag_settings)

                if not enabled:
                    await ActionEngine.cancel_chat_action(self.client, self.chat_id)
                else:
                    action = enabled[self._rotation_index % len(enabled)]
                    self._rotation_index += 1
                    await ActionEngine.send_action(self.client, self.chat_id, action)

                await asyncio.sleep(ACTION_LOOP_INTERVAL)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ActionKeeper] loop error process=%s: %s", self.process_key, exc)
