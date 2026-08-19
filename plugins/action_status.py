"""
Action Sozlamalari — flat one-click toggle panel.
"""
from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from config import user_settings
from session_manager import get_user_client
from utag_system.action_engine import (
    ActionEngine,
    FLAT_ACTION_TOGGLES,
    get_toggle_by_key,
    toggle_button_label,
    toggle_on,
    flip_toggle,
)

logger = logging.getLogger(__name__)


def _ensure_settings(user_id: int) -> dict:
    if user_id not in user_settings:
        user_settings[user_id] = {}
    return user_settings[user_id]


def _edit_cq(cq: CallbackQuery, text: str, buttons: list | None = None):
    if buttons is None:
        return cq.message.edit_text(text)
    return cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))


def _build_flat_menu(settings: dict, back: str) -> tuple[str, list]:
    text = (
        "⚙️ **Action Sozlamalari**\n\n"
        "Har bir tugmani bosing — ON/OFF almashtiriladi.\n"
        "💡 UTag typing alohida: Utag → Sozlamalar."
    )
    buttons = []
    for key, label, _action in FLAT_ACTION_TOGGLES:
        enabled = toggle_on(settings, key)
        buttons.append([
            InlineKeyboardButton(
                toggle_button_label(label, enabled),
                callback_data=f"action_flat_toggle_{key}",
            )
        ])
    buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data=back)])
    return text, buttons


async def _render_menu(cq: CallbackQuery, settings: dict) -> None:
    back = settings.get("action_status_back", "menu_main")
    text, buttons = _build_flat_menu(settings, back)
    await _edit_cq(cq, text, buttons)


@Client.on_callback_query(filters.regex("^action_status_menu(_account)?$"))
async def action_status_entry(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    user_id = cq.from_user.id
    settings = _ensure_settings(user_id)
    settings["action_status_back"] = "menu_main" if cq.matches[0].group(1) else "utag_settings"
    await _render_menu(cq, settings)
    await cq.answer()


@Client.on_callback_query(filters.regex("^action_flat_toggle_(.+)$"))
async def action_flat_toggle(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    user_id = cq.from_user.id
    key = cq.matches[0].group(1)
    if not get_toggle_by_key(key):
        await cq.answer("Noma'lum sozlama", show_alert=True)
        return

    settings = _ensure_settings(user_id)
    flip_toggle(settings, key)

    # Apply online presence immediately when toggled
    if key == "privacy_online_status":
        try:
            user_client = await get_user_client(user_id)
            await ActionEngine.apply_online_presence(user_client, settings)
        except Exception as exc:
            logger.warning("[ActionStatus] online toggle apply failed user=%s: %s", user_id, exc)

    await _render_menu(cq, settings)
    await cq.answer("✅ Yangilandi")
