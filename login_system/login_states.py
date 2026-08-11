"""
Login State Machine - User authentication state management
"""
import asyncio
from enum import Enum
from typing import Optional, Dict, Any
from dataclasses import dataclass


class LoginState(Enum):
    """Login process states"""
    IDLE = "idle"
    WAITING_PHONE = "waiting_for_phone"
    WAITING_CODE = "waiting_for_code"
    WAITING_PASSWORD = "waiting_for_password"
    WAITING_ADMIN_APPROVAL = "waiting_for_admin_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    LOGGED_OUT = "logged_out"


@dataclass
class LoginSession:
    """Login session data"""
    user_id: int
    state: LoginState
    phone: Optional[str] = None
    client: Optional[Any] = None  # Pyrogram client
    phone_code_hash: Optional[str] = None
    created_at: float = 0
    updated_at: float = 0
    
    def __post_init__(self):
        import time
        if self.created_at == 0:
            self.created_at = time.time()
        self.updated_at = time.time()


class LoginStateManager:
    """Manages login states and sessions"""
    
    VALID_TRANSITIONS = {
        LoginState.IDLE: [LoginState.WAITING_PHONE],
        LoginState.WAITING_PHONE: [LoginState.WAITING_CODE, LoginState.FAILED],
        LoginState.WAITING_CODE: [LoginState.WAITING_PASSWORD, LoginState.WAITING_ADMIN_APPROVAL, LoginState.COMPLETED, LoginState.FAILED],
        LoginState.WAITING_PASSWORD: [LoginState.WAITING_ADMIN_APPROVAL, LoginState.COMPLETED, LoginState.FAILED],
        LoginState.WAITING_ADMIN_APPROVAL: [LoginState.COMPLETED, LoginState.FAILED],
        LoginState.COMPLETED: [LoginState.IDLE, LoginState.LOGGED_OUT],
        LoginState.FAILED: [LoginState.IDLE, LoginState.WAITING_PHONE],
        LoginState.LOGGED_OUT: [LoginState.WAITING_PHONE],
    }
    
    def __init__(self):
        self._sessions: Dict[int, LoginSession] = {}
        self._lock = asyncio.Lock()
        self._session_timeout = 600  # 10 minutes
    
    async def create_session(self, user_id: int) -> LoginSession:
        """Create new login session"""
        async with self._lock:
            session = LoginSession(
                user_id=user_id,
                state=LoginState.IDLE
            )
            self._sessions[user_id] = session
            return session
    
    async def get_session(self, user_id: int) -> Optional[LoginSession]:
        """Get user's login session"""
        async with self._lock:
            session = self._sessions.get(user_id)
            if session:
                # Check timeout
                import time
                if time.time() - session.updated_at > self._session_timeout:
                    await self.cleanup_session(user_id)
                    return None
            return session
    
    async def update_state(self, user_id: int, new_state: LoginState, **kwargs) -> bool:
        """Update session state with validation"""
        async with self._lock:
            session = self._sessions.get(user_id)
            if not session:
                return False
            
            # Validate transition
            current_state = session.state
            if new_state not in self.VALID_TRANSITIONS.get(current_state, []):
                return False
            
            # Update state
            session.state = new_state
            import time
            session.updated_at = time.time()
            
            # Update additional data
            for key, value in kwargs.items():
                if hasattr(session, key):
                    setattr(session, key, value)
            
            return True
    
    async def cleanup_session(self, user_id: int):
        """Clean up user's login session"""
        async with self._lock:
            session = self._sessions.pop(user_id, None)
            if session and session.client:
                try:
                    if hasattr(session.client, 'is_connected') and session.client.is_connected:
                        await session.client.disconnect()
                except:
                    pass
    
    async def get_all_sessions(self) -> Dict[int, LoginSession]:
        """Get all active sessions"""
        async with self._lock:
            return self._sessions.copy()
    
    async def cleanup_expired_sessions(self):
        """Clean up expired sessions"""
        import time
        async with self._lock:
            current_time = time.time()
            expired_users = [
                user_id for user_id, session in self._sessions.items()
                if current_time - session.updated_at > self._session_timeout
            ]
            
            for user_id in expired_users:
                await self.cleanup_session(user_id)
    
    async def get_session_count(self) -> int:
        """Get count of active sessions"""
        async with self._lock:
            return len(self._sessions)


# Global state manager instance
login_state_manager = LoginStateManager()