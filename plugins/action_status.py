"""
Action Status & Privacy Management Panel — full ChatAction + Ghost Mode UI.
"""
from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import user_settings, user_states
from session_manager import get_user_client
from utag_system.action_engine import (
    ActionEngine,
    CATEGORY_LABELS,
    CHAT_ACTION_CATALOG,
    PRIVACY_ONLINE_MODES,
    SETTINGS_KEY_ACTIVE_ACTION,
    SETTINGS_KEY_GHOST_READ,
    SETTINGS_KEY_ONLINE_MODE,
    get_action_by_key,
    get_action_by_name,
    get_active_action,
    is_action_enabled,
    status_label,
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


def _back_callback(settings: dict) -> str:
    return settings.get("action_status_back", "menu_main")


def _build_hub_menu(settings: dict, back: str) -> tuple[str, list]:
    ghost = settings.get(SETTINGS_KEY_GHOST_READ, False)
    online = PRIVACY_ONLINE_MODES.get(settings.get(SETTINGS_KEY_ONLINE_MODE, "normal"), "⚪ Normal")
    active = get_active_action(settings)
    active_label = get_action_by_name(active)[1] if active and get_action_by_name(active) else "— Tanlanmagan —"
    enabled_count = sum(1 for cat in CHAT_ACTION_CATALOG.values() for item in cat if is_action_enabled(settings, item[0]))

    text = (
        "🎭 **Action Status & Privacy**\n\n"
        f"🔘 Faol action: {active_label}\n"
        f"✅ Yoqilgan actionlar: {enabled_count} ta\n"
        f"👻 Ghost Read: {status_label(ghost)}\n"
        f"🌐 Online holat: {online}\n\n"
        "💡 UTag ichidagi **Typing** faqat tagging vaqtida ishlaydi.\n"
        "Bu panel global ChatAction va maxfiylik sozlamalarini boshqaradi."
    )
    buttons = [
        [InlineKeyboardButton("📋 Chat Actions", callback_data="action_status_categories")],
        [InlineKeyboardButton("👻 Privacy & Ghost Mode", callback_data="action_status_privacy")],
        [InlineKeyboardButton("🛑 Cancel action (tozalash)", callback_data="action_status_cancel_test")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data=back)],
    ]
    return text, buttons


def _build_categories_menu(back: str) -> tuple[str, list]:
    text = "📋 **Chat Actions — Kategoriyalar**\n\nKerakli toifani tanlang:"
    buttons = []
    for cat_key, cat_label in CATEGORY_LABELS.items():
        buttons.append([InlineKeyboardButton(cat_label, callback_data=f"action_status_cat_{cat_key}")])
    buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="action_status_hub")])
    return text, buttons


def _build_category_menu(settings: dict, category: str) -> tuple[str, list]:
    items = CHAT_ACTION_CATALOG.get(category, [])
    label = CATEGORY_LABELS.get(category, category)
    lines = [f"{label} **Action Status**\n"]
    buttons = []
    active = get_active_action(settings)

    for key, item_label, action_name in items:
        enabled = is_action_enabled(settings, key)
        star = " ⭐" if active == action_name else ""
        lines.append(f"{item_label}: {status_label(enabled)}{star}")
        short = item_label.split(" ", 1)[0]
        buttons.append([
            InlineKeyboardButton(f"{short} {'✅' if enabled else '❌'}", callback_data=f"action_status_toggle_{key}"),
            InlineKeyboardButton("⭐", callback_data=f"action_status_set_active_{action_name}"),
            InlineKeyboardButton("▶️", callback_data=f"action_status_test_{action_name}"),
        ])

    if category == "text":
        typing_utag = settings.get("utag_typing_status", True)
        lines.append(f"\n💡 UTag typing: {status_label(typing_utag)} (Utag → Sozlamalar)")

    buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="action_status_categories")])
    return "\n".join(lines), buttons


def _build_privacy_menu(settings: dict) -> tuple[str, list]:
    ghost = settings.get(SETTINGS_KEY_GHOST_READ, False)
    online = settings.get(SETTINGS_KEY_ONLINE_MODE, "normal")
    text = (
        "👻 **Privacy & Ghost Mode**\n\n"
        f"📭 Ghost Read (o'qilgan deb ko'rsatmaslik): {status_label(ghost)}\n"
        f"🌐 Online holat: {PRIVACY_ONLINE_MODES.get(online, online)}\n\n"
        "**Ghost Read** yoqilganda xabarlar o'qilgan deb belgilanmaydi.\n"
        "**Offline** rejimida akkaunt offline ko'rinadi."
    )
    buttons = [
        [
            InlineKeyboardButton("👻 Ghost Read ON", callback_data="action_status_ghost_on"),
            InlineKeyboardButton("👻 Ghost Read OFF", callback_data="action_status_ghost_off"),
        ],
        [InlineKeyboardButton("⚪ Normal online", callback_data="action_status_online_normal")],
        [InlineKeyboardButton("👻 Offline (Ghost)", callback_data="action_status_online_offline")],
        [InlineKeyboardButton("🟢 Doim online", callback_data="action_status_online_always")],
        [InlineKeyboardButton("🔄 Holatni qo'llash", callback_data="action_status_apply_presence")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="action_status_hub")],
    ]
    return text, buttons


@Client.on_callback_query(filters.regex("^action_status_menu(_account)?$"))
async def action_status_entry(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    user_id = cq.from_user.id
    settings = _ensure_settings(user_id)
    back = "menu_main" if cq.matches[0].group(1) else "utag_settings"
    settings["action_status_back"] = back
    text, buttons = _build_hub_menu(settings, back)
    await _edit_cq(cq, text, buttons)
    await cq.answer()


@Client.on_callback_query(filters.regex("^action_status_hub$"))
async def action_status_hub(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    settings = _ensure_settings(cq.from_user.id)
    text, buttons = _build_hub_menu(settings, _back_callback(settings))
    await _edit_cq(cq, text, buttons)
    await cq.answer()


@Client.on_callback_query(filters.regex("^action_status_categories$"))
async def action_status_categories(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    text, buttons = _build_categories_menu("action_status_hub")
    await _edit_cq(cq, text, buttons)
    await cq.answer()


@Client.on_callback_query(filters.regex("^action_status_cat_(.+)$"))
async def action_status_category(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    category = cq.matches[0].group(1)
    if category not in CHAT_ACTION_CATALOG:
        await cq.answer("Noma'lum kategoriya", show_alert=True)
        return
    settings = _ensure_settings(cq.from_user.id)
    text, buttons = _build_category_menu(settings, category)
    await _edit_cq(cq, text, buttons)
    await cq.answer()


@Client.on_callback_query(filters.regex("^action_status_toggle_(.+)$"))
async def action_status_toggle(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    key = cq.matches[0].group(1)
    if not get_action_by_key(key):
        await cq.answer("Noma'lum action", show_alert=True)
        return
    settings = _ensure_settings(cq.from_user.id)
    settings[key] = not is_action_enabled(settings, key)
    category = next((c for c, items in CHAT_ACTION_CATALOG.items() if any(i[0] == key for i in items)), None)
    if category:
        text, buttons = _build_category_menu(settings, category)
        await _edit_cq(cq, text, buttons)
    await cq.answer("Sozlama yangilandi")


@Client.on_callback_query(filters.regex("^action_status_set_active_(.+)$"))
async def action_status_set_active(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    action_name = cq.matches[0].group(1)
    if action_name == "cancel":
        await cq.answer("Cancel faqat tozalash uchun", show_alert=True)
        return
    if not get_action_by_name(action_name):
        await cq.answer("Noma'lum action", show_alert=True)
        return
    settings = _ensure_settings(cq.from_user.id)
    settings[SETTINGS_KEY_ACTIVE_ACTION] = action_name
    category = next((c for c, items in CHAT_ACTION_CATALOG.items() if any(i[2] == action_name for i in items)), None)
    if category:
        text, buttons = _build_category_menu(settings, category)
        await _edit_cq(cq, text, buttons)
    await cq.answer(f"⭐ Faol action: {action_name}")


@Client.on_callback_query(filters.regex("^action_status_test_(.+)$"))
async def action_status_test(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    user_id = cq.from_user.id
    action_name = cq.matches[0].group(1)
    settings = _ensure_settings(user_id)
    try:
        user_client = await get_user_client(user_id)
        if action_name == "cancel":
            ok = await ActionEngine.cancel_action(user_client, user_id)
        else:
            ok = await ActionEngine.send_action(user_client, user_id, action_name, duration=2.0)
        await ActionEngine.apply_privacy_after_action(user_client, settings)
        if ok:
            await cq.answer(f"✅ '{action_name}' yuborildi (Saved Messages)", show_alert=True)
        else:
            await cq.answer("❌ Action yuborilmadi", show_alert=True)
    except Exception as exc:
        logger.error("[ActionStatus] test failed user=%s action=%s: %s", user_id, action_name, exc)
        await cq.answer(f"❌ Xatolik: {exc}", show_alert=True)


@Client.on_callback_query(filters.regex("^action_status_cancel_test$"))
async def action_status_cancel_test(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    user_id = cq.from_user.id
    settings = _ensure_settings(user_id)
    try:
        user_client = await get_user_client(user_id)
        ok = await ActionEngine.cancel_action(user_client, user_id)
        await ActionEngine.apply_privacy_after_action(user_client, settings)
        await cq.answer("✅ Cancel yuborildi" if ok else "❌ Cancel yuborilmadi", show_alert=True)
    except Exception as exc:
        await cq.answer(f"❌ {exc}", show_alert=True)


@Client.on_callback_query(filters.regex("^action_status_privacy$"))
async def action_status_privacy(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    settings = _ensure_settings(cq.from_user.id)
    text, buttons = _build_privacy_menu(settings)
    await _edit_cq(cq, text, buttons)
    await cq.answer()


@Client.on_callback_query(filters.regex("^action_status_ghost_(on|off)$"))
async def action_status_ghost_toggle(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    settings = _ensure_settings(cq.from_user.id)
    settings[SETTINGS_KEY_GHOST_READ] = cq.matches[0].group(1) == "on"
    text, buttons = _build_privacy_menu(settings)
    await _edit_cq(cq, text, buttons)
    await cq.answer("Ghost Read yangilandi")


@Client.on_callback_query(filters.regex("^action_status_online_(normal|offline|always)$"))
async def action_status_online_mode(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    mode_map = {"normal": "normal", "offline": "offline", "always": "online"}
    settings = _ensure_settings(cq.from_user.id)
    settings[SETTINGS_KEY_ONLINE_MODE] = mode_map[cq.matches[0].group(1)]
    text, buttons = _build_privacy_menu(settings)
    await _edit_cq(cq, text, buttons)
    await cq.answer("Online holat yangilandi")


@Client.on_callback_query(filters.regex("^action_status_apply_presence$"))
async def action_status_apply_presence(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    user_id = cq.from_user.id
    settings = _ensure_settings(user_id)
    try:
        user_client = await get_user_client(user_id)
        await ActionEngine.apply_online_presence(user_client, settings)
        await cq.answer("✅ Online holat qo'llanildi", show_alert=True)
    except Exception as exc:
        await cq.answer(f"❌ {exc}", show_alert=True)
