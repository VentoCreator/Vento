import asyncio
from pyrogram import Client

UNLOCK_KEYWORDS = ["qush", "erkin", "free", "premium", "unlimited", "no limits"]

async def send_and_check_unlock(client: Client, max_attempts: int = 5) -> bool:
    """
    Send messages to @spambot and check if unlocked
    
    Args:
        client: Pyrogram client instance
        max_attempts: Maximum number of attempts to unlock
    
    Returns:
        bool: True if unlocked, False otherwise
    """
    for attempt in range(max_attempts):
        try:
            await client.send_message("spambot", "/start")
            await asyncio.sleep(1)
            
            await client.send_message("spambot", "/start")
            await asyncio.sleep(2)
            
            async for message in client.get_chat_history("spambot", limit=5):
                if message.from_user and message.from_user.username == "SpamBot":
                    if message.text:
                        text = message.text.lower()
                        if any(keyword in text for keyword in UNLOCK_KEYWORDS):
                            print(f"✅ Spambot unlocked! Keyword found: {message.text[:50]}")
                            return True
            
            print(f"⏳ Attempt {attempt + 1}/{max_attempts} - no unlock keywords found")
            await asyncio.sleep(2)
            
        except Exception as e:
            print(f"Spambot unlock xatosi (attempt {attempt + 1}): {e}")
            await asyncio.sleep(2)
    
    return False

async def unlock_with_message(client: Client, message: str = "free", max_attempts: int = 3) -> bool:
    """
    Send specific message to @spambot and check for unlock
    
    Args:
        client: Pyrogram client instance
        message: Message to send
        max_attempts: Maximum number of attempts
    
    Returns:
        bool: True if unlocked, False otherwise
    """
    for attempt in range(max_attempts):
        try:
            await client.send_message("spambot", message)
            await asyncio.sleep(2)
            
            async for msg in client.get_chat_history("spambot", limit=5):
                if msg.from_user and msg.from_user.username == "SpamBot":
                    if msg.text:
                        text = msg.text.lower()
                        if any(keyword in text for keyword in UNLOCK_KEYWORDS):
                            print(f"✅ Spambot unlocked with message! Keyword found: {msg.text[:50]}")
                            return True
            
            print(f"⏳ Message attempt {attempt + 1}/{max_attempts} - no unlock keywords found")
            await asyncio.sleep(2)
            
        except Exception as e:
            print(f"Spambot message unlock xatosi (attempt {attempt + 1}): {e}")
            await asyncio.sleep(2)
    
    return False

async def check_if_locked(client: Client) -> bool:
    """
    Check if account is currently locked by checking recent spambot messages
    
    Args:
        client: Pyrogram client instance
    
    Returns:
        bool: True if locked, False if unlocked
    """
    try:
        async for message in client.get_chat_history("spambot", limit=3):
            if message.from_user and message.from_user.username == "SpamBot":
                if message.text:
                    text = message.text.lower()
                    if any(keyword in text for keyword in UNLOCK_KEYWORDS):
                        return False
        return True
    except Exception as e:
        print(f"Check lock status xatosi: {e}")
        return True  # Assume locked if we can't check
