import asyncio
import sys
import logging
import time
import os

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

if sys.platform != "win32":
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())

from pyrogram import Client, idle
from config import API_ID, API_HASH, BOT_TOKEN, BASE_DIR, SESSIONS_DIR, bot_client
import config
from database import init_db, get_all_users, remove_user, mark_user_warned, get_known_user
from locales import get_text
from queue_manager import queue_manager
from plugins.utag import utag_timer_background_task
from login_system import login_service
from service_initializer import initialize_services

app = Client(
    "empire_bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workdir=BASE_DIR,
    plugins=dict(root="plugins"),
    device_model="Vento Client",
    app_version="Vento Userbot v3.0",
    system_version="Windows 11 Pro 24H2"
)

async def subscription_checker():
    """Vaqti-vaqti bilan obunalarni tekshirib turuvchi fon jarayoni (Background task)"""
    while True:
        try:
            now = int(time.time())
            users = await get_all_users()
            for user in users:
                user_id = user["user_id"]
                expiry = user["expiry_date"]
                warned = user["warned"]
                
                user_info = await get_known_user(user_id)
                lang = user_info.get("language", "uz") if user_info else "uz"
                
                if 0 < expiry < now:
                    try:
                        await app.send_message(
                            user_id, 
                            get_text("subscription_expired", lang)
                        )
                    except Exception:
                        pass
                    
                    await remove_user(user_id)
                    logger.info(f"Foydalanuvchi {user_id} obunasi tugadi va o'chirildi.")
                    # Sessiya faylini o'chirmaymiz - Owner panelida akkaunt qaytarish uchun kerak
                
                elif 0 < expiry - now <= 86400 and not warned:
                    try:
                        await app.send_message(
                            user_id,
                            get_text("subscription_warning", lang)
                        )
                        await mark_user_warned(user_id)
                    except Exception:
                        pass
                        
                await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Subscription checker xatosi: {e}")
        
        await asyncio.sleep(3600)  # Har 1 soatda tekshiradi

async def main():
    logger.info("Ma'lumotlar bazasi tayyorlanmoqda...")
    await init_db()
    
    logger.info("Adminlar ro'yxati yuklanmoqda...")
    await config.load_admin_ids_from_db()
    logger.info(f"Jami {len(config.ADMIN_IDS)} ta admin yuklandi.")
    
    logger.info("Modular xizmatlar ishga tushmoqda...")
    services_initialized = await initialize_services()
    if not services_initialized:
        logger.error("Xizmatlarni ishga tushirish muvaffaqiyatsiz tugadi!")
        return
    
    logger.info("Bot ishga tushmoqda...")
    config.bot_client = app
    await app.start()
    
    logger.info("Bot tayyor! Smart plaginlar yuklandi.")

    # Keep a reference to every fire-and-forget task AND attach a done-callback so that, if a
    # background task dies with an exception (which otherwise silently becomes "Task exception was
    # never retrieved" and permanently disables that subsystem while the event loop keeps running),
    # the failure is logged instead of disappearing. The references also prevent the tasks from
    # being garbage-collected mid-flight.
    await _spawn_guarded("Subscription Checker", subscription_checker())
    logger.info("Obuna tekshiruvchi (Subscription Checker) ishga tushdi.")
    
    await _spawn_guarded("UTAG Timer", utag_timer_background_task(app))
    logger.info("UTAG Timer (avtomatik /game) ishga tushdi.")
    
    await queue_manager.start()
    logger.info("Queue manager ishga tushdi.")
    
    # Start login system cleanup task
    await _spawn_guarded("Login Cleanup", _login_cleanup_task())
    logger.info("Login system cleanup task ishga tushdi.")
    
    await idle()
    
    await app.stop()
    logger.info("Bot to'xtatildi.")


# Strong references to every supervised background task so they are never garbage-collected and so
# their completion is observable (a task exception is no longer silently swallowed).
_background_tasks: set = set()


def _log_task_failure(future: asyncio.Task):
    if future.cancelled():
        logger.warning("[BACKGROUND] A background task was cancelled.")
        return
    exc = future.exception()
    if exc is not None:
        logger.exception(
            "[BACKGROUND] A background task died with an unhandled exception. "
            "The affected subsystem is no longer running: %r", exc,
            exc_info=(type(exc), exc, exc.__traceback__)
        )
    _background_tasks.discard(future)


async def _spawn_guarded(name: str, coroutine) -> asyncio.Task:
    """Create a fire-and-forget task, but retain a reference and log any failure.

    The framework and this bot spawn many tasks with ``asyncio.create_task(...)`` and never keep a
    reference nor a done-callback, so when one raises it is reported only as *"Task exception was
    never retrieved"* and the corresponding subsystem silently stops while the event loop keeps
    running (exactly the "bot stopped responding but the timer still ticks" symptom). This helper
    makes those failures visible and prevents silent task loss.
    """
    task = asyncio.create_task(coroutine)
    _background_tasks.add(task)
    task.add_done_callback(_log_task_failure)
    logger.info("Background task started: %s", name)
    return task


async def _login_cleanup_task():
    """Cleanup expired login sessions"""
    while True:
        try:
            await login_service.state_manager.cleanup_expired_sessions()
        except Exception as e:
            logger.error(f"Login cleanup error: {e}")
        await asyncio.sleep(300)  # Every 5 minutes

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Dastur foydalanuvchi tomonidan to'xtatildi.")