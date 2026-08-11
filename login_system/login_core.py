"""
Login Core - Authentication business logic
"""
import os
import shutil
import asyncio
import logging
from typing import Optional, Tuple, Dict, Any
from pyrogram import Client, StopPropagation
from pyrogram.errors import (
    PhoneCodeInvalid, PhoneCodeExpired,
    SessionPasswordNeeded, PhoneNumberInvalid,
    AuthKeyUnregistered, AuthKeyDuplicated, 
    SessionExpired, SessionRevoked
)

from login_system.login_states import LoginState, LoginSession, LoginStateManager

logger = logging.getLogger(__name__)


class LoginError(Exception):
    """Base login error"""
    pass


class SessionError(LoginError):
    """Session related error"""
    pass


class ValidationError(LoginError):
    """Validation error"""
    pass


class AuthenticationError(LoginError):
    """Authentication error"""
    pass


class SessionManager:
    """Manages session file operations"""
    
    def __init__(self, sessions_dir: str):
        self.sessions_dir = sessions_dir
        self.pending_dir = os.path.join(sessions_dir, "pending")
        os.makedirs(self.pending_dir, exist_ok=True)
        os.makedirs(self.sessions_dir, exist_ok=True)
    
    def get_pending_session_path(self, user_id: int) -> str:
        """Get pending session file path"""
        return os.path.join(self.pending_dir, f"user_{user_id}")
    
    def get_final_session_path(self, user_id: int) -> str:
        """Get final session file path"""
        return os.path.join(self.sessions_dir, f"user_{user_id}")
    
    def cleanup_pending(self, user_id: int):
        """Clean up pending session files"""
        for ext in (".session", ".session-journal"):
            path = os.path.join(self.pending_dir, f"user_{user_id}{ext}")
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
    
    def move_session_to_final(self, user_id: int) -> bool:
        """Move session from pending to final directory (overwrites existing session for re-login)"""
        src = os.path.join(self.pending_dir, f"user_{user_id}.session")
        dst = os.path.join(self.sessions_dir, f"user_{user_id}.session")
        
        try:
            if os.path.exists(src):
                # shutil.move will overwrite existing session file (supports re-login)
                shutil.move(src, dst)
            
            # Move journal file if exists (also overwrites)
            src_j = src + "-journal"
            dst_j = dst + "-journal"
            if os.path.exists(src_j):
                shutil.move(src_j, dst_j)
            
            return True
        except Exception as e:
            logger.warning("Session move error: %s", e)
            return False
    
    def session_exists(self, user_id: int) -> bool:
        """Check if session file exists"""
        session_path = self.get_final_session_path(user_id) + ".session"
        return os.path.exists(session_path)


class PhoneValidator:
    """Validates phone numbers"""
    
    @staticmethod
    def validate(phone: str) -> Tuple[bool, str]:
        """
        Validate phone number format
        
        Returns:
            (is_valid, error_message)
        """
        phone = phone.strip()
        
        if not phone.startswith("+"):
            return False, "Telefon raqami + bilan boshlanishi kerak"
        
        if not phone[1:].isdigit():
            return False, "Telefon raqami faqat raqamlardan iborat bo'lishi kerak"
        
        if len(phone) < 8:
            return False, "Telefon raqami juda qisqa"
        
        return True, ""


class AuthManager:
    """Manages authentication operations"""
    
    def __init__(self, api_id: int, api_hash: str, session_manager: SessionManager):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_manager = session_manager
    
    async def send_code(self, user_id: int, phone: str) -> Tuple[Client, str]:
        """
        Send verification code to phone number
        
        Returns:
            (client, phone_code_hash)
        """
        session_name = self.session_manager.get_pending_session_path(user_id)
        
        try:
            from pyrogram import Client as PyroClient
            client = PyroClient(
                session_name,
                api_id=self.api_id,
                api_hash=self.api_hash,
                phone_number=phone,
                device_model="Vento Client",
                app_version="Vento Userbot v3.0",
                system_version="Windows 11 Pro 24H2"
            )
            await client.connect()
            sent = await client.send_code(phone)
            
            return client, sent.phone_code_hash
            
        except PhoneNumberInvalid as e:
            raise ValidationError("Telefon raqam noto'g'ri")
        except Exception as e:
            raise LoginError(f"Kod yuborishda xatolik: {e}")
    
    async def verify_code(self, client: Client, phone: str, phone_code_hash: str, code: str) -> bool:
        """
        Verify authentication code
        
        Returns:
            True if 2FA is needed, False if login complete
        """
        try:
            await client.sign_in(phone, phone_code_hash, code)
            return False  # Login complete
        except SessionPasswordNeeded:
            return True  # 2FA needed
        except (PhoneCodeInvalid, PhoneCodeExpired) as e:
            raise AuthenticationError("Kod noto'g'ri yoki muddati o'tgan")
        except Exception as e:
            raise LoginError(f"Kod tekshirishda xatolik: {e}")
    
    async def verify_password(self, client: Client, password: str) -> bool:
        """
        Verify 2FA password
        
        Returns:
            True if successful
        """
        try:
            await client.check_password(password)
            return True
        except Exception as e:
            raise AuthenticationError(f"Parol xato: {e}")
    
    async def complete_login(self, client: Client, user_id: int, phone: str = None) -> bool:
        """
        Complete login process
        Handles both new logins and re-logins by overwriting existing sessions
        
        Returns:
            True if successful
        """
        try:
            if client.is_connected:
                await client.disconnect()
            
            # Move session to final directory (overwrites existing session if present)
            if not self.session_manager.move_session_to_final(user_id):
                raise SessionError("Sessiya faylini ko'chirishda xatolik")
            
            # Verify session file exists
            if not self.session_manager.session_exists(user_id):
                raise SessionError("Sessiya fayli topilmadi")
            
            # Save to database (updates existing record if present)
            try:
                from database_adapter import LoginDatabaseAdapter
                from error_handler import log_system_event
                
                # Get user info from client if available
                session_data = {
                    "expiry_date": 0,  # Will be set by subscription system
                    "username": None,
                    "first_name": None
                }
                
                await LoginDatabaseAdapter.save_user_session(user_id, phone or "", session_data)
                await log_system_event("login_system", user_id, "login_complete", f"Phone: {phone}")
                
            except Exception as db_error:
                # Don't fail login if database save fails
                logger.warning("Database save failed (non-critical): %s", db_error)
            
            return True
            
        except Exception as e:
            raise LoginError(f"Login tugatishda xatolik: {e}")


class LoginService:
    """Main login service coordinating all components"""
    
    def __init__(self, api_id: int, api_hash: str, sessions_dir: str):
        self.session_manager = SessionManager(sessions_dir)
        self.phone_validator = PhoneValidator()
        self.auth_manager = AuthManager(api_id, api_hash, self.session_manager)
        self.state_manager = LoginStateManager()
    
    async def start_login(self, user_id: int) -> LoginSession:
        """Start login process for user (handles both new logins and re-logins)"""
        # Create or get existing session
        session = await self.state_manager.get_session(user_id)
        if not session:
            session = await self.state_manager.create_session(user_id)
        
        # Update state to waiting for phone (will overwrite existing session on completion)
        await self.state_manager.update_state(user_id, LoginState.WAITING_PHONE)
        return session
    
    async def submit_phone(self, user_id: int, phone: str) -> Tuple[bool, str, Optional[Client], Optional[str]]:
        """
        Submit phone number
        
        Returns:
            (success, message, client, phone_code_hash)
        """
        # Validate phone
        is_valid, error_msg = self.phone_validator.validate(phone)
        if not is_valid:
            # Log failed validation
            try:
                from error_handler import log_system_event
                await log_system_event("login_system", user_id, "phone_validation_failed", error_msg)
            except Exception:
                pass
            return False, error_msg, None, None
        
        # Send code
        try:
            client, phone_code_hash = await self.auth_manager.send_code(user_id, phone)
            
            # CRITICAL: IMMEDIATELY set state to WAITING_CODE before responding to user
            # Update both state systems for compatibility
            from config import user_states
            await self.state_manager.update_state(
                user_id, 
                LoginState.WAITING_CODE,
                phone=phone,
                client=client,
                phone_code_hash=phone_code_hash
            )
            user_states[user_id] = "waiting_for_code"
            
            # Log successful code send
            try:
                from database_adapter import LoginDatabaseAdapter
                await LoginDatabaseAdapter.log_login_attempt(user_id, phone, False)  # Not complete yet
            except Exception:
                pass
            
            return True, "Kod yuborildi", client, phone_code_hash
            
        except ValidationError as e:
            await self.state_manager.update_state(user_id, LoginState.FAILED)
            return False, str(e), None, None
        except LoginError as e:
            await self.state_manager.update_state(user_id, LoginState.FAILED)
            self.session_manager.cleanup_pending(user_id)
            return False, str(e), None, None
    
    async def submit_code(self, user_id: int, code: str) -> Tuple[bool, str, bool]:
        """
        Submit verification code
        
        Returns:
            (success, message, needs_password)
        """
        from config import user_states
        
        session = await self.state_manager.get_session(user_id)
        if not session or session.state != LoginState.WAITING_CODE:
            return False, "Sessiya topilmadi", False
        
        try:
            needs_password = await self.auth_manager.verify_code(
                session.client,
                session.phone,
                session.phone_code_hash,
                code
            )
            
            if needs_password:
                # Update both state systems to WAITING_PASSWORD
                await self.state_manager.update_state(user_id, LoginState.WAITING_PASSWORD)
                user_states[user_id] = "waiting_for_password"
                return True, "2FA parol kerak", True
            else:
                # Complete login
                success = await self.auth_manager.complete_login(session.client, user_id, session.phone)
                if success:
                    # Log successful login
                    try:
                        from database_adapter import LoginDatabaseAdapter
                        from error_handler import log_system_event
                        await LoginDatabaseAdapter.log_login_attempt(user_id, session.phone, True)
                        await log_system_event("login_system", user_id, "login_success", f"Phone: {session.phone}")
                    except Exception:
                        pass
                    
                    await self.state_manager.update_state(user_id, LoginState.WAITING_ADMIN_APPROVAL)
                    return True, "Login muvaffaqiyatli", False
                else:
                    return False, "Login tugatishda xatolik", False
                    
        except AuthenticationError as e:
            # Log authentication error
            try:
                from error_handler import global_error_handler
                await global_error_handler.handle_error(e, "login_system", user_id)
            except Exception:
                pass
            return False, str(e), False
        except LoginError as e:
            await self.state_manager.update_state(user_id, LoginState.FAILED)
            self.session_manager.cleanup_pending(user_id)
            # Log login error
            try:
                from error_handler import global_error_handler
                await global_error_handler.handle_error(e, "login_system", user_id)
            except Exception:
                pass
            return False, str(e), False
    
    async def submit_password(self, user_id: int, password: str) -> Tuple[bool, str, bool]:
        """
        Submit 2FA password
        
        Returns:
            (success, message, needs_password)
        """
        session = await self.state_manager.get_session(user_id)
        if not session or session.state != LoginState.WAITING_PASSWORD:
            return False, "Sessiya topilmadi", False
        
        try:
            await self.auth_manager.verify_password(session.client, password)
            
            # Complete login
            success = await self.auth_manager.complete_login(session.client, user_id, session.phone)
            if success:
                # Log successful login
                try:
                    from database_adapter import LoginDatabaseAdapter
                    from error_handler import log_system_event
                    await LoginDatabaseAdapter.log_login_attempt(user_id, session.phone, True)
                    await log_system_event("login_system", user_id, "login_success", f"Phone: {session.phone} (2FA)")
                except Exception:
                    pass
                
                await self.state_manager.update_state(user_id, LoginState.WAITING_ADMIN_APPROVAL)
                return True, "Login muvaffaqiyatli", False
            else:
                return False, "Login tugatishda xatolik", False
                
        except AuthenticationError as e:
            # Log authentication error
            try:
                from error_handler import global_error_handler
                await global_error_handler.handle_error(e, "login_system", user_id)
            except Exception:
                pass
            return False, str(e), False
        except LoginError as e:
            await self.state_manager.update_state(user_id, LoginState.FAILED)
            self.session_manager.cleanup_pending(user_id)
            # Log login error
            try:
                from error_handler import global_error_handler
                await global_error_handler.handle_error(e, "login_system", user_id)
            except Exception:
                pass
            return False, str(e), False
    
    async def cancel_login(self, user_id: int) -> bool:
        """Cancel login process"""
        await self.state_manager.update_state(user_id, LoginState.FAILED)
        self.session_manager.cleanup_pending(user_id)
        await self.state_manager.cleanup_session(user_id)
        return True
    
    async def approve_login(self, user_id: int) -> bool:
        """Approve user login (admin action)"""
        return await self.state_manager.update_state(user_id, LoginState.COMPLETED)
    
    async def reject_login(self, user_id: int) -> bool:
        """Reject user login (admin action)"""
        await self.state_manager.update_state(user_id, LoginState.FAILED)
        self.session_manager.cleanup_pending(user_id)
        await self.state_manager.cleanup_session(user_id)
        return True