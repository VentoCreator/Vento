"""
Login Handlers - Telegram message and callback handlers
"""
from pyrogram import Client, filters, ContinuePropagation, StopPropagation
from pyrogram.types import Message, CallbackQuery
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from login_system.login_core import LoginService, LoginError, SessionError
from login_system.login_config import LoginConstants, default_settings
from config import SUPER_ADMIN_ID, SECOND_ADMIN_ID, SESSIONS_DIR, API_ID, API_HASH, is_admin, user_states
from database import register_known_user
from error_handler import handle_errors
import logging

logger = logging.getLogger(__name__)


class LoginHandlers:
    """Telegram login handlers"""
    
    def __init__(self, login_service: LoginService):
        self.login_service = login_service
        self.settings = default_settings
    
    def _is_admin(self, user_id: int) -> bool:
        """Check if user is admin"""
        return is_admin(user_id)
    
    @handle_errors("login", "user_id", auto_retry=False)
    async def handle_phone_input(self, client: Client, message: Message):
        """Handle phone number input with validation and formatting"""
        from pyrogram import StopPropagation
        
        user_id = message.from_user.id
        phone = message.text.strip()
        
        try:
            # STRICT VALIDATION: Only process if message contains + OR has 9+ digits
            # This prevents 4-6 digit SMS codes from being processed as phone numbers
            digits_only = phone.replace("+", "").replace(" ", "").replace("-", "")
            if not ("+" in phone or len(digits_only) >= 9):
                # Not a phone number - let other handlers process it
                raise ContinuePropagation
            
            # Delete user message
            try:
                await message.delete()
            except:
                pass
            
            # Format phone number: remove spaces, ensure + prefix
            phone = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
            if not phone.startswith("+"):
                phone = "+" + phone
            
            # Submit phone
            msg = await message.reply_text("🔄 Kod yuborilmoqda...")
            
            success, message_text, client_obj, phone_code_hash = await self.login_service.submit_phone(user_id, phone)
            
            if success:
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton(LoginConstants.BUTTON_CANCEL, callback_data=LoginConstants.CALLBACK_CANCEL_LOGIN)]
                ])
                
                await msg.edit_text(
                    self.settings.messages["code_sent"],
                    reply_markup=keyboard
                )
            else:
                await msg.edit_text(f"❌ {message_text}\n\nQaytadan `/start` bosing.")
        
        except Exception as e:
            await message.reply_text(f"❌ Xatolik: {e}\n\nQaytadan `/start` bosing.")
        
        # StopPropagation to prevent message from reaching other handlers
        raise StopPropagation
        
        # StopPropagation to prevent message from reaching other handlers
        raise StopPropagation
    
    @handle_errors("login", "user_id", auto_retry=False)
    async def handle_code_input(self, client: Client, message: Message):
        """Handle verification code input"""
        from login_system import LoginState
        from config import user_states
        from pyrogram.errors import SessionPasswordNeeded
        
        user_id = message.from_user.id
        code = message.text.strip().replace(" ", "").replace("-", "")
        
        try:
            # STRICT VALIDATION: Only process 4-6 digit numeric strings as codes
            # This prevents phone numbers from being processed as verification codes
            if not code.isdigit() or len(code) < 4 or len(code) > 6:
                # Not a valid code format - let other handlers process it
                raise ContinuePropagation
            
            # Delete user message
            try:
                await message.delete()
            except:
                pass
            
            # Submit code
            msg = await message.reply_text("🔄 Kod tekshirilmoqda...")
            
            success, message_text, needs_password = await self.login_service.submit_code(user_id, code)
            
            if success and needs_password:
                # Update both state systems to WAITING_PASSWORD
                user_states[user_id] = "waiting_for_password"
                
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton(LoginConstants.BUTTON_CANCEL, callback_data=LoginConstants.CALLBACK_CANCEL_LOGIN)]
                ])
                
                await msg.edit_text(
                    "🔐 Akkauntingizda 2-qadamli tasdiqlash (2FA parol) yoqilgan.\n\nIltimos, 2FA parolingizni kiriting:",
                    reply_markup=keyboard
                )
            elif success:
                await self._handle_login_success(client, message, user_id, msg)
            else:
                await msg.edit_text(f"❌ {message_text}\n\nQaytadan kodni kiriting:")
        
        except SessionPasswordNeeded as e:
            # Explicitly catch SessionPasswordNeeded and transition to password state
            user_states[user_id] = "waiting_for_password"
            await self.login_service.state_manager.update_state(user_id, LoginState.WAITING_PASSWORD)
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(LoginConstants.BUTTON_CANCEL, callback_data=LoginConstants.CALLBACK_CANCEL_LOGIN)]
            ])
            
            await msg.edit_text(
                "🔐 Akkauntingizda 2-qadamli tasdiqlash (2FA parol) yoqilgan.\n\nIltimos, 2FA parolingizni kiriting:",
                reply_markup=keyboard
            )
        except Exception as e:
            await message.reply_text(f"❌ Xatolik: {e}\n\nQaytadan `/start` bosing.")
        
        # StopPropagation to prevent message from reaching other handlers
        raise StopPropagation
    
    @handle_errors("login", "user_id", auto_retry=False)
    async def handle_password_input(self, client: Client, message: Message):
        """Handle 2FA password input"""
        from config import user_states
        from pyrogram import StopPropagation
        
        user_id = message.from_user.id
        password = message.text
        
        try:
            # Delete user message
            try:
                await message.delete()
            except:
                pass
            
            # Submit password
            msg = await message.reply_text("🔄 Parol tekshirilmoqda...")
            
            success, message_text, needs_password = await self.login_service.submit_password(user_id, password)
            
            if success:
                # Clear old state after successful 2FA
                user_states.pop(user_id, None)
                await self._handle_login_success(client, message, user_id, msg)
            else:
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton(LoginConstants.BUTTON_CANCEL, callback_data=LoginConstants.CALLBACK_CANCEL_LOGIN)]
                ])
                
                await msg.edit_text(
                    f"❌ {message_text}\n\nQaytadan parol kiriting:",
                    reply_markup=keyboard
                )
        
        except Exception as e:
            await message.reply_text(f"❌ Xatolik: {e}\n\nQaytadan `/start` bosing.")
        
        # StopPropagation to prevent message from reaching other handlers
        raise StopPropagation
    
    async def _handle_login_success(self, client: Client, message: Message, user_id: int, msg):
        """Handle successful login"""
        from login_system import LoginState
        from database_adapter import LoginDatabaseAdapter
        
        logger.info(f"User {user_id} logged in successfully, processing database updates.")
        # Register user in database
        await register_known_user(user_id, message.from_user.username, message.from_user.first_name)
        
        # CRITICAL: Set is_active = 1 in database on successful login
        try:
            await LoginDatabaseAdapter.set_user_active_status(user_id, True)
        except Exception as e:
            logger.error(f"CRITICAL: Failed to set user active in DB on login for user {user_id}: {e}")
        
        if self._is_admin(user_id):
            # Admin - auto approve
            await self.login_service.approve_login(user_id)
            user_states.pop(user_id, None)
            logger.info(f"Admin user {user_id} approved automatically.")
            
            await msg.edit_text(self.settings.messages["login_success_admin"])
            
            # Send main menu
            from plugins.menu import get_main_keyboard
            kb_reply = await get_main_keyboard(user_id)
            await message.reply_text("🏠 **Bosh menyu**", reply_markup=kb_reply)
        else:
            # Regular user - wait for admin approval
            user_states[user_id] = "waiting_for_admin_approval"
            logger.info(f"Regular user {user_id} waiting for admin approval.")
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(LoginConstants.BUTTON_CHECK_APPROVAL, callback_data=LoginConstants.CALLBACK_CHECK_APPROVAL)]
            ])
            
            await msg.edit_text(
                self.settings.messages["login_success"],
                reply_markup=keyboard
            )
            
            # Notify admin
            await self._notify_admin(client, message.from_user)
    
    async def _notify_admin(self, client: Client, user):
        """Notify admin about new user"""
        user_id = user.id
        name = user.first_name
        username = f"@{user.username}" if user.username else "yo'q"
        
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(LoginConstants.BUTTON_APPROVE, callback_data=f"{LoginConstants.CALLBACK_ADMIN_APPROVE_PREFIX}{user_id}"),
                InlineKeyboardButton(LoginConstants.BUTTON_REJECT, callback_data=f"{LoginConstants.CALLBACK_ADMIN_REJECT_PREFIX}{user_id}"),
            ],
            [InlineKeyboardButton(LoginConstants.BUTTON_INVOICE, callback_data=f"{LoginConstants.CALLBACK_ADMIN_INVOICE_PREFIX}{user_id}")]
        ])
        
        text = (
            "👤 **Yangi foydalanuvchi!**\n\n"
            f"Ism: **{name}**\n"
            f"Username: {username}\n"
            f"ID: `{user_id}`\n\n"
            "Nima qilamiz?"
        )
        
        try:
            await client.send_message(SUPER_ADMIN_ID, text, reply_markup=keyboard)
        except:
            pass
    
    async def handle_cancel_login(self, client: Client, callback_query: CallbackQuery):
        """Handle login cancellation"""
        user_id = callback_query.from_user.id
        
        await self.login_service.cancel_login(user_id)
        user_states.pop(user_id, None)
        logger.info(f"User {user_id} cancelled the login process.")
        
        await callback_query.message.edit_text(self.settings.messages["cancelled"])
        await callback_query.answer("Bekor qilindi", show_alert=True)
    
    async def handle_admin_approve(self, client: Client, callback_query: CallbackQuery, target_id: int):
        """Handle admin approval"""
        admin_id = callback_query.from_user.id
        if not self._is_admin(admin_id):
            await callback_query.answer("⛔️ Ruxsat yo'q!", show_alert=True)
            return
        
        from database import add_or_update_user
        from database_adapter import LoginDatabaseAdapter
        import time
        
        # Give 1 month subscription
        expiry = int(time.time()) + 30 * 24 * 3600
        await add_or_update_user(target_id, expiry)
        
        # CRITICAL: Set is_active = 1 in database on admin approval
        try:
            await LoginDatabaseAdapter.set_user_active_status(target_id, True)
        except Exception as e:
            logger.error(f"CRITICAL: Failed to set user active in DB on admin approval for {target_id}: {e}")
        
        # Approve login
        await self.login_service.approve_login(target_id)
        user_states.pop(target_id, None)
        logger.info(f"Admin {admin_id} approved login for user {target_id}.")
        
        # Get user info
        username = ""
        try:
            user_info = await client.get_users(target_id)
            if user_info.username:
                username = f"@{user_info.username} "
            elif user_info.first_name:
                username = f"{user_info.first_name} "
        except:
            pass
        
        await callback_query.message.edit_text(
            f"✅ {username}[`{target_id}`] tasdiqlandi va 1 oylik obuna berildi."
        )
        
        # Notify user
        try:
            await client.send_message(
                target_id,
                self.settings.messages["approved"]
            )
        except:
            pass
        
        await callback_query.answer("Tasdiqlandi!", show_alert=True)
    
    async def handle_admin_reject(self, client: Client, callback_query: CallbackQuery, target_id: int):
        """Handle admin rejection"""
        admin_id = callback_query.from_user.id
        if not self._is_admin(admin_id):
            await callback_query.answer("⛔️ Ruxsat yo'q!", show_alert=True)
            return
        
        # Reject login
        await self.login_service.reject_login(target_id)
        user_states.pop(target_id, None)
        logger.info(f"Admin {admin_id} rejected login for user {target_id}.")
        
        # Get user info
        username = ""
        try:
            user_info = await client.get_users(target_id)
            if user_info.username:
                username = f"@{user_info.username} "
            elif user_info.first_name:
                username = f"{user_info.first_name} "
        except:
            pass
        
        await callback_query.message.edit_text(f"❌ {username}[`{target_id}`] rad etildi.")
        
        # Notify user
        try:
            await client.send_message(
                target_id,
                self.settings.messages["rejected"]
            )
        except:
            pass
        
        await callback_query.answer("Rad etildi!", show_alert=True)
    
    async def handle_check_approval(self, client: Client, callback_query: CallbackQuery):
        """Handle approval status check"""
        user_id = callback_query.from_user.id
        from login_system import LoginState
        
        session = await self.login_service.state_manager.get_session(user_id)
        
        if session and session.state == LoginState.COMPLETED:
            user_states.pop(user_id, None)
            logger.info(f"User {user_id} checked approval, confirmed COMPLETED, cleared state.")
            await callback_query.message.edit_text(
                self.settings.messages["approved"]
            )
            # Send main menu
            from plugins.menu import get_main_keyboard
            kb_reply = await get_main_keyboard(user_id)
            await callback_query.message.reply_text("🏠 **Bosh menyu**", reply_markup=kb_reply)
            await callback_query.answer("Tasdiqlandi!", show_alert=True)
        else:
            await callback_query.answer("⏳ Hali tasdiqlanmadi. Admin javobini kuting.", show_alert=True)

    async def handle_admin_invoice(self, client: Client, callback_query: CallbackQuery, target_id: int):
        """Handle admin sending invoice to user"""
        admin_id = callback_query.from_user.id
        if not self._is_admin(admin_id):
            await callback_query.answer("⛔️ Ruxsat yo'q!", show_alert=True)
            return

        from pyrogram.types import LabeledPrice
        try:
            prices = [LabeledPrice("⭐️ Obuna sotib olish", 100)]
            payload = f"stars_payment_{target_id}"
            await client.send_invoice(
                chat_id=target_id,
                title="⭐️ Obuna sotib olish",
                description="Vento botidan 30 kun to'liq foydalanish uchun Stars orqali to'lov qiling.",
                payload=payload,
                currency="XTR",
                prices=prices
            )
            
            # Get user username/name
            username = ""
            try:
                user_info = await client.get_users(target_id)
                if user_info.username:
                    username = f"@{user_info.username} "
                elif user_info.first_name:
                    username = f"{user_info.first_name} "
            except:
                pass
                
            await callback_query.message.edit_text(
                f"✅ {username}[`{target_id}`] foydalanuvchisiga 100 Stars to'lov fakturasi yuborildi."
            )
            await callback_query.answer("Faktura yuborildi!", show_alert=True)
        except Exception as e:
            logger.error(f"Fakturani yuborishda xatolik target_id={target_id}: {e}")
            await callback_query.answer(f"❌ Faktura yuborishda xatolik: {e}", show_alert=True)


# Initialize login service
login_service = LoginService(API_ID, API_HASH, SESSIONS_DIR)
login_handlers = LoginHandlers(login_service)


# Register handlers
@Client.on_message(filters.private & filters.text, group=-5)  # High priority to catch before other plugins
@handle_errors("login", "user_id", auto_retry=False)
async def login_phone_handler(client: Client, message: Message):
    """Handle phone number input"""
    from login_system import LoginState
    from config import user_states
    
    user_id = message.from_user.id
    
    # Check both old user_states and new LoginStateManager for backward compatibility
    old_state = user_states.get(user_id)
    session = await login_service.state_manager.get_session(user_id)
    new_state = session.state if session else None
    
    # Accept either old string state or new enum state
    if old_state != "waiting_for_phone" and new_state != LoginState.WAITING_PHONE:
        raise ContinuePropagation
    
    await login_handlers.handle_phone_input(client, message)


@Client.on_message(filters.private & filters.text, group=-5)  # High priority to catch before other plugins
@handle_errors("login", "user_id", auto_retry=False)
async def login_code_handler(client: Client, message: Message):
    """Handle verification code input"""
    from login_system import LoginState
    from config import user_states
    
    user_id = message.from_user.id
    
    # Check both old user_states and new LoginStateManager for backward compatibility
    old_state = user_states.get(user_id)
    session = await login_service.state_manager.get_session(user_id)
    new_state = session.state if session else None
    
    # Accept either old string state or new enum state
    if old_state != "waiting_for_code" and new_state != LoginState.WAITING_CODE:
        raise ContinuePropagation
    
    await login_handlers.handle_code_input(client, message)


@Client.on_message(filters.private & filters.text, group=-5)  # High priority to catch before other plugins
@handle_errors("login", "user_id", auto_retry=False)
async def login_password_handler(client: Client, message: Message):
    """Handle 2FA password input"""
    from login_system import LoginState
    from config import user_states
    
    user_id = message.from_user.id
    
    # Check both old user_states and new LoginStateManager for backward compatibility
    old_state = user_states.get(user_id)
    session = await login_service.state_manager.get_session(user_id)
    new_state = session.state if session else None
    
    # Accept either old string state or new enum state
    if old_state != "waiting_for_password" and new_state != LoginState.WAITING_PASSWORD:
        raise ContinuePropagation
    
    await login_handlers.handle_password_input(client, message)


@Client.on_callback_query(filters.regex("^cancel_login$"))
@handle_errors("login", "user_id", auto_retry=False)
async def cancel_login_callback(client: Client, callback_query: CallbackQuery):
    """Handle login cancellation"""
    await login_handlers.handle_cancel_login(client, callback_query)


def _admin_filter(_, __, callback_query: CallbackQuery):
    """Admin callback filter"""
    return callback_query.from_user and is_admin(callback_query.from_user.id)


_admin_callback_filter = filters.create(_admin_filter)


@Client.on_callback_query(filters.regex(r"^admin_approve_(\d+)$") & _admin_callback_filter)
@handle_errors("login", "user_id", auto_retry=False)
async def admin_approve_callback(client: Client, callback_query: CallbackQuery):
    """Handle admin approval"""
    target_id = int(callback_query.matches[0].group(1))
    await login_handlers.handle_admin_approve(client, callback_query, target_id)


@Client.on_callback_query(filters.regex(r"^admin_reject_(\d+)$") & _admin_callback_filter)
@handle_errors("login", "user_id", auto_retry=False)
async def admin_reject_callback(client: Client, callback_query: CallbackQuery):
    """Handle admin rejection"""
    target_id = int(callback_query.matches[0].group(1))
    await login_handlers.handle_admin_reject(client, callback_query, target_id)


@Client.on_callback_query(filters.regex("^check_admin_approval$"))
@handle_errors("login", "user_id", auto_retry=False)
async def check_approval_callback(client: Client, callback_query: CallbackQuery):
    """Handle approval status check"""
    await login_handlers.handle_check_approval(client, callback_query)


@Client.on_callback_query(filters.regex(r"^admin_invoice_(\d+)$") & _admin_callback_filter)
@handle_errors("login", "user_id", auto_retry=False)
async def admin_invoice_callback(client: Client, callback_query: CallbackQuery):
    """Handle admin sending invoice"""
    target_id = int(callback_query.matches[0].group(1))
    await login_handlers.handle_admin_invoice(client, callback_query, target_id)
