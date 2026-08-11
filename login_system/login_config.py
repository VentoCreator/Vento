"""
Login Configuration - System settings and constants
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class LoginSettings:
    """Login system settings"""
    
    # Session settings
    session_timeout: int = 600  # 10 minutes
    max_concurrent_logins: int = 50
    max_login_attempts: int = 5
    
    # Phone validation
    phone_min_length: int = 8
    phone_max_length: int = 15
    require_country_code: bool = True
    
    # Code validation
    code_length: int = 6
    code_retry_limit: int = 3
    
    # Password validation
    password_min_length: int = 1
    password_retry_limit: int = 3
    
    # Admin approval
    require_admin_approval: bool = True
    auto_approve_admins: bool = True
    
    # Security
    cleanup_pending_on_failure: bool = True
    cleanup_pending_on_cancel: bool = True
    
    # Messages
    messages: dict = None
    
    def __post_init__(self):
        if self.messages is None:
            self.messages = {
                "welcome": "👋 Xush kelibsiz! Botdan foydalanish uchun akkauntni ulang.",
                "phone_request": "📱 Telefon raqamingizni xalqaro formatda kiriting:\nMasalan: +998901234567",
                "phone_invalid": "❌ Noto'g'ri format. Xalqaro formatda kiriting:\n`+998901234567`",
                "code_sent": "📨 **Kod yuborildi!**\n\nTelegram raqamingizga kod yuborildi.\n\nKodni yuboring (probel tashlab kiriting):",
                "code_invalid": "❌ Kod noto'g'ri yoki muddati o'tgan.\n\nQaytadan kodni kiriting:",
                "password_request": "🔐 **Ikki bosqichli tekshiruv (2FA) yoqilgan!**\n\nParolni yuboring:",
                "password_invalid": "❌ Parol xato.\n\nQaytadan parol kiriting:",
                "login_success": "✅ **Muvaffaqiyatli login qilindi!**\n\n⏳ Admin tasdiqlashini kuting.",
                "login_success_admin": "✅ **Admin, muvaffaqiyatli kirdingiz!**\n\nAkkauntingiz botga ulandi.",
                "session_error": "❌ Sessiya tugagan, qaytadan bosing. /start",
                "session_move_error": "❌ Sessiya faylini ko'chirishda xatolik. Qaytadan /start bosing.",
                "generic_error": "❌ Xatolik yuz berdi. Qaytadan /start bosing.",
                "cancelled": "❌ Bekor qilindi. Qaytadan boshlash uchun `/start` yuboring.",
                "approved": "🎉 **Tabriklaymiz!**\n\nAkkauntingiz admin tomonidan tasdiqlandi.\nBotdan foydalanish uchun /start bosing.",
                "rejected": "❌ Sizning so'rovingiz admin tomonidan rad etildi.\n\nBatafsil ma'lumot uchun admin bilan bog'laning.",
            }


class LoginConstants:
    """Login system constants"""
    
    # States
    STATE_IDLE = "idle"
    STATE_WAITING_PHONE = "waiting_for_phone"
    STATE_WAITING_CODE = "waiting_for_code"
    STATE_WAITING_PASSWORD = "waiting_for_password"
    STATE_WAITING_ADMIN_APPROVAL = "waiting_for_admin_approval"
    STATE_COMPLETED = "completed"
    STATE_FAILED = "failed"
    
    # Callback data
    CALLBACK_CANCEL_LOGIN = "cancel_login"
    CALLBACK_ADMIN_APPROVE_PREFIX = "admin_approve_"
    CALLBACK_ADMIN_REJECT_PREFIX = "admin_reject_"
    CALLBACK_ADMIN_INVOICE_PREFIX = "admin_invoice_"
    CALLBACK_CHECK_APPROVAL = "check_admin_approval"
    
    # Buttons
    BUTTON_CANCEL = "❌ Bekor qilish"
    BUTTON_APPROVE = "✅ Tasdiqlash"
    BUTTON_REJECT = "❌ Rad etish"
    BUTTON_INVOICE = "💳 To'lov fakturasini yuborish"
    BUTTON_CHECK_APPROVAL = "🔄 Tasdiqlashni tekshirish"
    
    # Time limits
    CODE_EXPIRY_MINUTES = 5
    SESSION_EXPIRY_MINUTES = 10
    APPROVAL_TIMEOUT_HOURS = 24


# Default settings instance
default_settings = LoginSettings()