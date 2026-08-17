import aiosqlite
import random
import string
from contextlib import asynccontextmanager
from config import DB_PATH

import asyncio
import logging

logger = logging.getLogger(__name__)

# Write lock for serialized database updates (inserts, updates, deletes, replaces, alters)
db_write_lock = asyncio.Lock()

class SafeCursorContextManager:
    """Wrapper that matches aiosqlite's execute/executemany return object.
    It can be awaited directly: cursor = await db.execute(...)
    Or used as an async context manager: async with db.execute(...) as cursor:
    """
    def __init__(self, connection, query_runner):
        self._connection = connection
        self._query_runner = query_runner
        self._cursor = None

    def __await__(self):
        return self._query_runner().__await__()

    async def __aenter__(self):
        self._cursor = await self._query_runner()
        return self._cursor

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._cursor:
            await self._cursor.close()
        return False

class SafeDatabaseConnection:
    """Wrapper around aiosqlite connection to automatically manage global write locking."""
    def __init__(self, conn):
        self._conn = conn
        self._write_locked = False

    def execute(self, sql, parameters=None):
        async def _run():
            sql_clean = sql.strip().upper()
            # Clean comments to identify the first word/operation type
            words = [w for w in sql_clean.split() if w and not w.startswith("--")]
            is_write = False
            if words:
                first_word = words[0].strip("()[]{},;\"'`")
                is_write = first_word in ["INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER"]

            if is_write and not self._write_locked:
                await db_write_lock.acquire()
                self._write_locked = True
            
            if parameters is not None:
                return await self._conn.execute(sql, parameters)
            else:
                return await self._conn.execute(sql)

        return SafeCursorContextManager(self, _run)

    def executemany(self, sql, seq_of_parameters):
        async def _run():
            sql_clean = sql.strip().upper()
            # Clean comments to identify the first word/operation type
            words = [w for w in sql_clean.split() if w and not w.startswith("--")]
            is_write = False
            if words:
                first_word = words[0].strip("()[]{},;\"'`")
                is_write = first_word in ["INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER"]

            if is_write and not self._write_locked:
                await db_write_lock.acquire()
                self._write_locked = True
            
            return await self._conn.executemany(sql, seq_of_parameters)

        return SafeCursorContextManager(self, _run)

    async def commit(self):
        try:
            res = await self._conn.commit()
        except Exception:
            # Roll back so we never leave a lingering, un-committed write transaction open on
            # this connection (an open writer is what makes other connections/processes report
            # "database is locked"), then ensure the process-wide write lock is released.
            try:
                await self._conn.rollback()
            except Exception:
                pass
            if self._write_locked:
                db_write_lock.release()
                self._write_locked = False
            raise
        if self._write_locked:
            db_write_lock.release()
            self._write_locked = False
        return res

    async def rollback(self):
        res = await self._conn.rollback()
        if self._write_locked:
            db_write_lock.release()
            self._write_locked = False
        return res

    async def close(self):
        res = await self._conn.close()
        if self._write_locked:
            db_write_lock.release()
            self._write_locked = False
        return res

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._write_locked:
            db_write_lock.release()
            self._write_locked = False
        return False

@asynccontextmanager
async def get_db_connection():
    """Returns an isolated connection per context, configures WAL, normal sync, and timeout."""
    conn = await aiosqlite.connect(DB_PATH, timeout=60.0)
    safe_conn = SafeDatabaseConnection(conn)
    try:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA cache_size=-16000")
        yield safe_conn
    finally:
        if safe_conn._write_locked:
            # A write was performed but never committed/rolled back while the connection was in
            # use. Roll it back before closing so no lingering write transaction is left open that
            # could block other connections/processes ("database is locked"), then release the
            # process-wide write lock so the next write cannot deadlock on it.
            try:
                await conn.rollback()
            except Exception:
                pass
            db_write_lock.release()
            safe_conn._write_locked = False
        await conn.close()


async def init_db():
    async with get_db_connection() as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                expiry_date INTEGER DEFAULT 0,
                warned BOOLEAN DEFAULT 0,
                username TEXT,
                first_name TEXT,
                is_active INTEGER DEFAULT 1
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS scraped_groups (
                group_id TEXT PRIMARY KEY,
                group_title TEXT,
                date_scraped INTEGER,
                owner_id INTEGER DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS scraped_members (
                user_id INTEGER,
                username TEXT,
                first_name TEXT,
                group_id TEXT,
                PRIMARY KEY (user_id, group_id),
                FOREIGN KEY(group_id) REFERENCES scraped_groups(group_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS stats (
                key TEXT PRIMARY KEY,
                value INTEGER DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY,
                violation_count INTEGER DEFAULT 1
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS free_users (
                user_id INTEGER PRIMARY KEY
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                payment_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                currency TEXT NOT NULL,
                invoice_payload TEXT,
                status TEXT NOT NULL DEFAULT 'paid',
                grant_status TEXT NOT NULL DEFAULT 'pending',
                granted_expiry INTEGER DEFAULT 0,
                created_at INTEGER DEFAULT 0,
                granted_at INTEGER DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS known_users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                joined_date INTEGER,
                last_seen INTEGER,
                language TEXT DEFAULT 'uz'
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS updates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                created_by INTEGER NOT NULL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS read_updates (
                user_id INTEGER NOT NULL,
                update_id INTEGER NOT NULL,
                PRIMARY KEY (user_id, update_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS admin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                target_id INTEGER,
                details TEXT,
                timestamp INTEGER NOT NULL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id INTEGER PRIMARY KEY,
                disable_update_notifications INTEGER DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_limits (
                user_id INTEGER PRIMARY KEY,
                last_nakrutka_time INTEGER DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                timestamp INTEGER NOT NULL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS admins (
                admin_id INTEGER PRIMARY KEY,
                joined_date INTEGER NOT NULL,
                admin_date INTEGER NOT NULL,
                can_add_admin INTEGER DEFAULT 1,
                can_ban INTEGER DEFAULT 1,
                can_clear_db INTEGER DEFAULT 1,
                can_broadcast INTEGER DEFAULT 1,
                can_manage_users INTEGER DEFAULT 1
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS massdm_progress (
                user_id INTEGER NOT NULL,
                group_id TEXT NOT NULL,
                last_index INTEGER DEFAULT 0,
                updated_at INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, group_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS massdm_settings (
                user_id INTEGER PRIMARY KEY,
                auto_stop_on_high_risk INTEGER DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS massdm_auto_stopped (
                stop_key TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                group_id TEXT NOT NULL,
                resume_after INTEGER DEFAULT 0,
                reason TEXT,
                message_to_copy_id INTEGER DEFAULT 0,
                delay_hours INTEGER DEFAULT 0,
                created_at INTEGER DEFAULT 0
            )
        ''')
        try:
            await db.execute("ALTER TABLE scraped_groups ADD COLUMN owner_id INTEGER DEFAULT 0")
        except:
            pass
            
        try:
            await db.execute("ALTER TABLE users ADD COLUMN username TEXT")
            await db.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
        except:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1")
        except:
            pass
        try:
            await db.execute("ALTER TABLE user_preferences ADD COLUMN utag_atag_cmd TEXT DEFAULT 'atag'")
        except:
            pass
        try:
            await db.execute("ALTER TABLE user_preferences ADD COLUMN utag_stop_cmd TEXT DEFAULT 'stop'")
        except:
            pass
        try:
            await db.execute("ALTER TABLE user_preferences ADD COLUMN utag_pause_cmd TEXT DEFAULT 'pause'")
        except:
            pass
        try:
            await db.execute("ALTER TABLE user_preferences ADD COLUMN utag_resume_cmd TEXT DEFAULT 'resume'")
        except:
            pass
        try:
            await db.execute("ALTER TABLE known_users ADD COLUMN language TEXT DEFAULT 'uz'")
        except:
            pass
        
        from config import ADMIN_IDS
        import time
        now = int(time.time())
        for admin_id in ADMIN_IDS:
            async with db.execute("SELECT admin_id FROM admins WHERE admin_id = ?", (admin_id,)) as cursor:
                if not await cursor.fetchone():
                    await db.execute('''
                        INSERT INTO admins (admin_id, joined_date, admin_date, can_add_admin, can_ban, can_clear_db, can_broadcast, can_manage_users)
                        VALUES (?, ?, ?, 1, 1, 1, 1, 1)
                    ''', (admin_id, now, now))
        
        try:
            async with db.execute("SELECT group_id, group_title FROM scraped_groups") as cursor:
                rows = await cursor.fetchall()
            for gid, gtitle in rows:
                if gtitle and ("\n" in gtitle or len(gtitle) > 60):
                    clean_title = gtitle.split("\n")[0].strip()[:60] or "Baza"
                    await db.execute("UPDATE scraped_groups SET group_title = ? WHERE group_id = ?", (clean_title, gid))
        except:
            pass
        
        await create_complaint_table(db)

        await db.execute('''
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL,
                receiver_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                photo_file_id TEXT,
                timestamp INTEGER NOT NULL,
                is_read INTEGER DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS chat_blocks (
                blocker_id INTEGER NOT NULL,
                blocked_id INTEGER NOT NULL,
                timestamp INTEGER NOT NULL,
                PRIMARY KEY (blocker_id, blocked_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS chat_mutes (
                muter_id INTEGER NOT NULL,
                muted_id INTEGER NOT NULL,
                timestamp INTEGER NOT NULL,
                PRIMARY KEY (muter_id, muted_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS chat_terms_accepted (
                user_id INTEGER PRIMARY KEY,
                accepted_at INTEGER NOT NULL
            )
        ''')
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS utag_timers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                message_text TEXT NOT NULL,
                interval_minutes INTEGER NOT NULL,
                repeat_count INTEGER DEFAULT 1,
                repeat_delay INTEGER DEFAULT 5,
                is_active INTEGER DEFAULT 1,
                last_sent INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL,
                UNIQUE(user_id, chat_id)
            )
        ''')

        await db.execute('''
            CREATE TABLE IF NOT EXISTS utag_custom_commands (
                user_id INTEGER NOT NULL,
                command TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (user_id, command)
            )
        ''')

        await db.commit()

        # CRITICAL RECOVERY POLICY:
        # Check for interrupted/un-recovered Mass DM tasks.
        # Any entry in massdm_progress that does not have an entry in massdm_auto_stopped
        # will be marked as RECOVERY_REQUIRED.
        try:
            import time
            now = int(time.time())
            async with db.execute("SELECT user_id, group_id, last_index FROM massdm_progress") as cursor:
                progress_rows = await cursor.fetchall()
            
            for uid, gid, last_idx in progress_rows:
                stop_key = f"recovery_{uid}_{gid}"
                # Check if already has auto_stopped entry
                async with db.execute("SELECT 1 FROM massdm_auto_stopped WHERE stop_key = ?", (stop_key,)) as check_cur:
                    exists = await check_cur.fetchone()
                
                if not exists:
                    # Insert into massdm_auto_stopped as RECOVERY_REQUIRED
                    await db.execute('''
                        INSERT INTO massdm_auto_stopped (stop_key, user_id, group_id, resume_after, reason, message_to_copy_id, delay_hours, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (stop_key, uid, gid, 0, "RECOVERY_REQUIRED", 0, 0, now))
            await db.commit()

        except Exception as recovery_err:
            # Shield startup from crashing due to recovery check failures
            logger.warning("Error checking and recovering interrupted tasks on startup: %s", recovery_err)


async def get_user_subscription(user_id):
    async with get_db_connection() as db:
        async with db.execute("SELECT expiry_date FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0]
            return 0

# ---------------------------------------------------------------------------
# Payment system helpers (Telegram Stars / XTR)
# ---------------------------------------------------------------------------

async def grant_subscription(user_id: int, days: int = 30) -> int:
    """Grant/extend a subscription.

    Idempotent extension rule:
        new_expiry = max(current_expiry, now) + days
    """
    import time
    now = int(time.time())
    current = await get_user_subscription(user_id)
    base = max(current, now)
    new_expiry = base + days * 86400

    async with get_db_connection() as db:
        await db.execute(
            "INSERT INTO users (user_id, expiry_date, warned, username, first_name, is_active) "
            "VALUES (?, ?, 0, NULL, NULL, 1) "
            "ON CONFLICT(user_id) DO UPDATE SET expiry_date = ?, warned = 0, is_active = 1",
            (user_id, new_expiry, new_expiry)
        )
        await db.commit()

    return new_expiry


async def record_payment(payment_id: str, user_id: int, amount: int, currency: str, invoice_payload: str = "") -> bool:
    """Persist a successful payment receipt.

    Returns True if the receipt was newly inserted, False if it already existed
    (i.e. a duplicate delivery of the same telegram_payment_charge_id).
    """
    import time
    async with get_db_connection() as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO payments "
            "(payment_id, user_id, amount, currency, invoice_payload, status, grant_status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'paid', 'pending', ?)",
            (payment_id, user_id, amount, currency, invoice_payload, int(time.time()))
        )
        await db.commit()
        return cur.rowcount > 0


async def get_payment_record(payment_id: str):
    """Return the stored payment receipt dict or None."""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT payment_id, user_id, amount, currency, invoice_payload, status, grant_status, granted_expiry, created_at, granted_at "
            "FROM payments WHERE payment_id = ?", (payment_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                cols = [d[0] for d in cursor.description]
                return dict(zip(cols, row))
            return None


async def is_payment_granted(payment_id: str) -> bool:
    """True if a payment receipt exists AND its subscription has already been granted."""
    rec = await get_payment_record(payment_id)
    if not rec:
        return False
    return rec.get("grant_status") == "granted"


async def mark_payment_granted(payment_id: str, granted_expiry: int) -> None:
    """Mark a payment receipt as 'granted' after the subscription update commits."""
    import time
    async with get_db_connection() as db:
        await db.execute(
            "UPDATE payments SET grant_status = 'granted', granted_expiry = ?, granted_at = ? WHERE payment_id = ?",
            (granted_expiry, int(time.time()), payment_id)
        )
        await db.commit()


async def add_or_update_user(user_id, expiry_date, username=None, first_name=None):
    async with get_db_connection() as db:
        await db.execute('''
            INSERT INTO users (user_id, expiry_date, warned, username, first_name, is_active)
            VALUES (?, ?, 0, ?, ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET expiry_date = ?, warned = 0, username = ?, first_name = ?, is_active = is_active
        ''', (user_id, expiry_date, username, first_name, expiry_date, username, first_name))
        await db.commit()

async def set_user_active_status(user_id: int, is_active: bool) -> bool:
    """Set user active status (1 = active, 0 = inactive/logged out)"""
    async with get_db_connection() as db:
        await db.execute("UPDATE users SET is_active = ? WHERE user_id = ?", (1 if is_active else 0, user_id))
        await db.commit()
        return True

async def get_user_active_status(user_id: int) -> bool:
    """Get user active status (returns True if active, False if inactive/logged out)"""
    async with get_db_connection() as db:
        async with db.execute("SELECT is_active FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return bool(row[0]) if row else False

async def remove_user(user_id):
    async with get_db_connection() as db:
        await db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        await db.commit()

async def get_all_users():
    async with get_db_connection() as db:
        async with db.execute("SELECT user_id, expiry_date, warned, username, first_name FROM users") as cursor:
            rows = await cursor.fetchall()
            return [{"user_id": r[0], "expiry_date": r[1], "warned": r[2], "username": r[3], "first_name": r[4]} for r in rows]

async def mark_user_warned(user_id):
    async with get_db_connection() as db:
        await db.execute("UPDATE users SET warned = 1 WHERE user_id = ?", (user_id,))
        await db.commit()

async def add_scraped_group(group_id, group_title, date_scraped, owner_id=0):
    group_title = (group_title or "Baza").split("\n")[0].strip()[:60]
    async with get_db_connection() as db:
        await db.execute('''
            INSERT OR REPLACE INTO scraped_groups (group_id, group_title, date_scraped, owner_id)
            VALUES (?, ?, ?, ?)
        ''', (group_id, group_title, date_scraped, owner_id))
        await db.commit()

async def add_scraped_member(user_id, username, first_name, group_id):
    async with get_db_connection() as db:
        await db.execute('''
            INSERT OR IGNORE INTO scraped_members (user_id, username, first_name, group_id)
            VALUES (?, ?, ?, ?)
        ''', (user_id, username, first_name, group_id))
        await db.commit()

async def add_scraped_members_batch(members_batch):
    """Adds a batch of scraped members to the database in a single transaction."""
    async with get_db_connection() as db:
        await db.executemany('''
            INSERT OR IGNORE INTO scraped_members (user_id, username, first_name, group_id)
            VALUES (?, ?, ?, ?)
        ''', members_batch)
        await db.commit()

async def get_members_by_group(group_id):
    async with get_db_connection() as db:
        async with db.execute("SELECT user_id, username, first_name FROM scraped_members WHERE group_id = ?", (group_id,)) as cursor:
            rows = await cursor.fetchall()
            return [{"user_id": r[0], "username": r[1], "first_name": r[2]} for r in rows]

async def stream_members_by_group(group_id, offset=0):
    async with get_db_connection() as db:
        async with db.execute("SELECT user_id, username, first_name FROM scraped_members WHERE group_id = ? LIMIT -1 OFFSET ?", (group_id, offset)) as cursor:
            async for row in cursor:
                yield {"user_id": row[0], "username": row[1], "first_name": row[2]}

async def get_all_scraped_groups(owner_id=None):
    """Guruhlar ro'yxati. owner_id berilsa - faqat o'shaning bazalari."""
    async with get_db_connection() as db:
        if owner_id is not None:
            async with db.execute(
                "SELECT group_id, group_title, date_scraped, owner_id FROM scraped_groups WHERE owner_id = ?",
                (owner_id,)
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with db.execute(
                "SELECT group_id, group_title, date_scraped, owner_id FROM scraped_groups"
            ) as cursor:
                rows = await cursor.fetchall()
        return [{"group_id": r[0], "group_title": r[1], "date_scraped": r[2], "owner_id": r[3]} for r in rows]

async def search_groups(query: str, limit: int = 20):
    """Guruhlarni nomi bo'yicha qidirish (ILIKE case-insensitive)"""
    async with get_db_connection() as db:
        search_pattern = f"%{query}%"
        async with db.execute(
            """SELECT group_id, group_title, date_scraped, owner_id 
               FROM scraped_groups 
               WHERE group_title LIKE ? COLLATE NOCASE
               ORDER BY date_scraped DESC 
               LIMIT ?""",
            (search_pattern, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"group_id": r[0], "group_title": r[1], "date_scraped": r[2], "owner_id": r[3]} for r in rows]

async def get_group_member_count(group_id: str) -> int:
    """Guruh a'zolari sonini olish"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT COUNT(*) FROM scraped_members WHERE group_id = ?",
            (group_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_user_info_from_scraped(user_id):
    """Scraped members jadvidan user ma'lumotlarini olish"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT username, first_name FROM scraped_members WHERE user_id = ? LIMIT 1",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"username": row[0], "first_name": row[1]}
            return None

async def get_all_scraped_groups_admin():
    """Admin uchun: barcha foydalanuvchilarning bazalari + kim yig'ganligini ko'rsatish."""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT group_id, group_title, date_scraped, owner_id FROM scraped_groups ORDER BY owner_id, date_scraped DESC"
        ) as cursor:
            rows = await cursor.fetchall()
        return [{"group_id": r[0], "group_title": r[1], "date_scraped": r[2], "owner_id": r[3]} for r in rows]

async def delete_scraped_group(group_id):
    async with get_db_connection() as db:
        await db.execute("DELETE FROM scraped_members WHERE group_id = ?", (group_id,))
        await db.execute("DELETE FROM scraped_groups WHERE group_id = ?", (group_id,))
        await db.commit()

async def delete_all_scraped_groups():
    async with get_db_connection() as db:
        await db.execute("DELETE FROM scraped_members")
        await db.execute("DELETE FROM scraped_groups")
        await db.commit()

async def get_violation_count(user_id):
    async with get_db_connection() as db:
        async with db.execute("SELECT violation_count FROM banned_users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def add_violation(user_id):
    async with get_db_connection() as db:
        async with db.execute("SELECT violation_count FROM banned_users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
        count = row[0] if row else 0
        new_count = count + 1
        if count == 0:
            await db.execute("INSERT INTO banned_users (user_id, violation_count) VALUES (?, ?)", (user_id, new_count))
        else:
            await db.execute("UPDATE banned_users SET violation_count = ? WHERE user_id = ?", (new_count, user_id))
        await db.commit()
        return new_count

async def remove_ban(user_id):
    async with get_db_connection() as db:
        await db.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
        await db.commit()



async def get_group_info(group_id):
    """Bitta guruh haqida to'liq ma'lumot"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT group_id, group_title, date_scraped, owner_id FROM scraped_groups WHERE group_id = ?",
            (group_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {"group_id": row[0], "group_title": row[1], "date_scraped": row[2], "owner_id": row[3]}

async def get_members_by_group_paginated(group_id, offset=0, limit=50):
    """Guruh a'zolarini sahifalab olish"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT user_id, username, first_name FROM scraped_members WHERE group_id = ? LIMIT ? OFFSET ?",
            (group_id, limit, offset)
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"user_id": r[0], "username": r[1], "first_name": r[2]} for r in rows]

async def add_manual_members(group_id, members):
    """Qo'lda a'zolar qo'shish"""
    async with get_db_connection() as db:
        for m in members:
            await db.execute(
                "INSERT OR IGNORE INTO scraped_members (user_id, username, first_name, group_id) VALUES (?, ?, ?, ?)",
                (m.get("user_id"), m.get("username"), m.get("first_name", ""), group_id)
            )
        await db.commit()

async def generate_unique_group_id():
    """10 xonali unikal ID (faqat raqam) yaratish"""
    async with get_db_connection() as db:
        while True:
            new_id = ''.join(random.choices(string.digits, k=10))
            async with db.execute("SELECT 1 FROM scraped_groups WHERE group_id = ?", (new_id,)) as cursor:
                if not await cursor.fetchone():
                    return new_id

async def get_group_id_by_title(group_title: str, owner_id: int = None):
    """Guruh nomiga (va owner_id ga) qarab bazadagi ID ni qaytaradi (merge uchun)"""
    async with get_db_connection() as db:
        if owner_id is not None:
            async with db.execute(
                "SELECT group_id FROM scraped_groups WHERE group_title = ? AND owner_id = ?",
                (group_title, owner_id)
            ) as cursor:
                row = await cursor.fetchone()
        else:
            async with db.execute(
                "SELECT group_id FROM scraped_groups WHERE group_title = ?",
                (group_title,)
            ) as cursor:
                row = await cursor.fetchone()
        return row[0] if row else None

async def update_group_date(group_id: str, date_scraped: int):
    """Guruh oxirgi scrape sanasini yangilash"""
    async with get_db_connection() as db:
        await db.execute("UPDATE scraped_groups SET date_scraped = ? WHERE group_id = ?", (date_scraped, group_id))
        await db.commit()

async def clean_users_without_username():
    """Username'siz barcha userlarni o'chirish"""
    async with get_db_connection() as db:
        cursor = await db.execute("DELETE FROM scraped_members WHERE username IS NULL OR username = ''")
        deleted_count = cursor.rowcount
        await db.commit()
        return deleted_count


async def add_free_user(user_id: int):
    async with get_db_connection() as db:
        await db.execute("INSERT OR IGNORE INTO free_users (user_id) VALUES (?)", (user_id,))
        await db.commit()

async def remove_free_user(user_id: int):
    async with get_db_connection() as db:
        await db.execute("DELETE FROM free_users WHERE user_id = ?", (user_id,))
        await db.commit()

async def is_free_user(user_id: int) -> bool:
    async with get_db_connection() as db:
        async with db.execute("SELECT 1 FROM free_users WHERE user_id = ?", (user_id,)) as cursor:
            return bool(await cursor.fetchone())

async def get_all_free_users():
    async with get_db_connection() as db:
        async with db.execute("""
            SELECT f.user_id, s.username, s.first_name 
            FROM free_users f 
            LEFT JOIN scraped_members s ON f.user_id = s.user_id
        """) as cursor:
            rows = await cursor.fetchall()
            return [{"user_id": r[0], "username": r[1], "first_name": r[2]} for r in rows]


async def register_known_user(user_id: int, username=None, first_name=None, language='uz'):
    """Foydalanuvchini ro'yxatdan o'tkazish (birinchi marta joined_date saqlanadi)"""
    import time
    now = int(time.time())
    async with get_db_connection() as db:
        async with db.execute("SELECT joined_date FROM known_users WHERE user_id = ?", (user_id,)) as cursor:
            existing = await cursor.fetchone()
        if existing:
            await db.execute(
                "UPDATE known_users SET username = ?, first_name = ?, last_seen = ?, language = ? WHERE user_id = ?",
                (username, first_name, now, language, user_id)
            )
        else:
            await db.execute(
                "INSERT INTO known_users (user_id, username, first_name, joined_date, last_seen, language) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, username, first_name, now, now, language)
            )
        await db.commit()

async def get_known_user(user_id: int):
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT user_id, username, first_name, joined_date, last_seen, language FROM known_users WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "user_id": row[0], "username": row[1], "first_name": row[2],
                "joined_date": row[3], "last_seen": row[4], "language": row[5]
            }

async def update_user_language(user_id: int, language: str):
    """Foydalanuvchi tilini yangilash"""
    async with get_db_connection() as db:
        await db.execute(
            "UPDATE known_users SET language = ? WHERE user_id = ?",
            (language, user_id)
        )
        await db.commit()

async def get_all_registered_user_ids():
    """Barcha ma'lum foydalanuvchi ID lari"""
    async with get_db_connection() as db:
        async with db.execute("""
            SELECT DISTINCT user_id FROM (
                SELECT user_id FROM known_users
                UNION SELECT user_id FROM users
                UNION SELECT user_id FROM free_users
                UNION SELECT user_id FROM banned_users
                UNION SELECT owner_id AS user_id FROM scraped_groups WHERE owner_id > 0
            ) ORDER BY user_id DESC
        """) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

async def search_users(query: str):
    """ID yoki username bo'yicha qidirish"""
    query = query.strip().lstrip("@")
    async with get_db_connection() as db:
        if query.isdigit():
            uid = int(query)
            async with db.execute("""
                SELECT DISTINCT user_id FROM (
                    SELECT user_id FROM known_users WHERE user_id = ?
                    UNION SELECT user_id FROM users WHERE user_id = ?
                    UNION SELECT user_id FROM free_users WHERE user_id = ?
                    UNION SELECT user_id FROM banned_users WHERE user_id = ?
                    UNION SELECT owner_id AS user_id FROM scraped_groups WHERE owner_id = ?
                )
            """, (uid, uid, uid, uid, uid)) as cursor:
                rows = await cursor.fetchall()
                return [r[0] for r in rows]
        async with db.execute(
            "SELECT user_id FROM known_users WHERE username LIKE ? COLLATE NOCASE",
            (f"%{query}%",)
        ) as cursor:
            rows = await cursor.fetchall()
            ids = [r[0] for r in rows]
        if not ids:
            async with db.execute(
                "SELECT DISTINCT user_id FROM scraped_members WHERE username LIKE ? COLLATE NOCASE LIMIT 20",
                (f"%{query}%",)
            ) as cursor:
                rows = await cursor.fetchall()
                ids = [r[0] for r in rows]
        return ids

async def get_user_database_stats(owner_id: int):
    """Foydalanuvchi bazalari statistikasi"""
    groups = await get_all_scraped_groups(owner_id=owner_id)
    total_members = 0
    for g in groups:
        total_members += await get_group_member_count(g["group_id"])
    return {"group_count": len(groups), "total_members": total_members, "groups": groups}

async def delete_user_databases(owner_id: int):
    """Foydalanuvchining barcha bazalarini o'chirish"""
    groups = await get_all_scraped_groups(owner_id=owner_id)
    for g in groups:
        await delete_scraped_group(g["group_id"])
    return len(groups)

async def get_all_banned_users():
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT user_id, violation_count FROM banned_users ORDER BY violation_count DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"user_id": r[0], "violation_count": r[1]} for r in rows]

async def get_admin_stats():
    """Admin dashboard statistikasi"""
    async with get_db_connection() as db:
        stats = {}
        for table, key in [
            ("known_users", "total_known"),
            ("users", "subscribed"),
            ("free_users", "free"),
            ("banned_users", "banned"),
            ("scraped_groups", "databases"),
            ("scraped_members", "total_scraped_members"),
        ]:
            async with db.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
                row = await cursor.fetchone()
                stats[key] = row[0] if row else 0
        import time
        now = int(time.time())
        async with db.execute("SELECT COUNT(*) FROM users WHERE expiry_date > ?", (now,)) as cursor:
            row = await cursor.fetchone()
            stats["active_subs"] = row[0] if row else 0
        return stats

async def get_user_full_profile(user_id: int):
    """Foydalanuvchi haqida to'liq ma'lumot"""
    known = await get_known_user(user_id)
    sub_expiry = await get_user_subscription(user_id)
    is_free = await is_free_user(user_id)
    violations = await get_violation_count(user_id)
    db_stats = await get_user_database_stats(user_id)

    username = known.get("username") if known else None
    first_name = known.get("first_name") if known else None
    if not username or not first_name:
        scraped = await get_user_info_from_scraped(user_id)
        if scraped:
            username = username or scraped.get("username")
            first_name = first_name or scraped.get("first_name")

    async with get_db_connection() as db:
        async with db.execute(
            "SELECT expiry_date, warned, username, first_name FROM users WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            user_row = await cursor.fetchone()
    if user_row:
        username = username or user_row[2]
        first_name = first_name or user_row[3]

    return {
        "user_id": user_id,
        "username": username,
        "first_name": first_name,
        "joined_date": known.get("joined_date") if known else None,
        "last_seen": known.get("last_seen") if known else None,
        "expiry_date": sub_expiry,
        "warned": user_row[1] if user_row else False,
        "is_free": is_free,
        "violation_count": violations,
        "database_count": db_stats["group_count"],
        "total_members": db_stats["total_members"],
        "groups": db_stats["groups"],
    }


async def add_update(title: str, content: str, created_by: int):
    """Yangi yangilanish qo'shish"""
    import time
    now = int(time.time())
    async with get_db_connection() as db:
        cursor = await db.execute(
            "INSERT INTO updates (title, content, created_at, created_by) VALUES (?, ?, ?, ?)",
            (title, content, now, created_by)
        )
        await db.commit()
        return cursor.lastrowid

async def get_all_updates():
    """Barcha yangilanishlarni olish"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT id, title, content, created_at, created_by FROM updates ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"id": r[0], "title": r[1], "content": r[2], "created_at": r[3], "created_by": r[4]} for r in rows]

async def get_latest_update():
    """Eng oxirgi yangilanishni olish"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT id, title, content, created_at, created_by FROM updates ORDER BY created_at DESC LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {"id": row[0], "title": row[1], "content": row[2], "created_at": row[3], "created_by": row[4]}

async def get_update_by_id(update_id: int):
    """ID bo'yicha yangilanishni olish"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT id, title, content, created_at, created_by FROM updates WHERE id = ?",
            (update_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {"id": row[0], "title": row[1], "content": row[2], "created_at": row[3], "created_by": row[4]}

async def delete_update(update_id: int):
    """Yangilanishni o'chirish"""
    async with get_db_connection() as db:
        await db.execute("DELETE FROM updates WHERE id = ?", (update_id,))
        await db.execute("DELETE FROM read_updates WHERE update_id = ?", (update_id,))
        await db.commit()

async def mark_update_read(user_id: int, update_id: int):
    """Yangilanishni o'qilgan deb belgilash"""
    async with get_db_connection() as db:
        await db.execute(
            "INSERT OR IGNORE INTO read_updates (user_id, update_id) VALUES (?, ?)",
            (user_id, update_id)
        )
        await db.commit()

async def has_user_read_update(user_id: int, update_id: int) -> bool:
    """Foydalanuvchi yangilanishni o'qiganmi?"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT 1 FROM read_updates WHERE user_id = ? AND update_id = ?",
            (user_id, update_id)
        ) as cursor:
            return bool(await cursor.fetchone())

async def get_unread_updates_count(user_id: int) -> int:
    """O'qilmagan yangilanishlar soni"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT COUNT(*) FROM updates WHERE id NOT IN (SELECT update_id FROM read_updates WHERE user_id = ?)",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_update_notification_pref(user_id: int) -> bool:
    """Yangilanish bildirishnomasi o'chirilganmi?"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT disable_update_notifications FROM user_preferences WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row[0]) if row else False

async def get_user_utag_commands(user_id: int) -> dict:
    """Foydalanuvchining utag komandalarini olish"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT utag_atag_cmd, utag_stop_cmd, utag_pause_cmd, utag_resume_cmd FROM user_preferences WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
    if row:
        return {
            "atag": row[0] if row[0] else "atag",
            "stop": row[1] if row[1] else "stop",
            "pause": row[2] if row[2] else "pause",
            "resume": row[3] if row[3] else "resume",
        }
    return {"atag": "atag", "stop": "stop", "pause": "pause", "resume": "resume"}


async def save_user_utag_command(user_id: int, cmd_type: str, command: str):
    """Utag komandasini saqlash"""
    existing = await get_user_utag_commands(user_id)
    atag = command if cmd_type == "atag" else existing["atag"]
    stop = command if cmd_type == "stop" else existing["stop"]
    pause = command if cmd_type == "pause" else existing["pause"]
    resume = command if cmd_type == "resume" else existing["resume"]
    
    async with get_db_connection() as db:
        await db.execute('''
            INSERT INTO user_preferences (user_id, utag_atag_cmd, utag_stop_cmd, utag_pause_cmd, utag_resume_cmd)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET utag_atag_cmd=?, utag_stop_cmd=?, utag_pause_cmd=?, utag_resume_cmd=?
        ''', (user_id, atag, stop, pause, resume, atag, stop, pause, resume))
        await db.commit()


async def set_update_notification_pref(user_id: int, disabled: bool):
    """Yangilanish bildirishnomasi sozlamasini o'zgartirish"""
    async with get_db_connection() as db:
        await db.execute('''
            INSERT INTO user_preferences (user_id, disable_update_notifications)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET disable_update_notifications=?
        ''', (user_id, 1 if disabled else 0, 1 if disabled else 0))
        await db.commit()

async def get_last_nakrutka_time(user_id: int) -> int:
    async with get_db_connection() as db:
        async with db.execute("SELECT last_nakrutka_time FROM user_limits WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def update_last_nakrutka_time(user_id: int, timestamp: int):
    async with get_db_connection() as db:
        await db.execute('''
            INSERT INTO user_limits (user_id, last_nakrutka_time)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET last_nakrutka_time = ?
        ''', (user_id, timestamp, timestamp))
        await db.commit()

async def log_user_action(user_id: int, action: str):
    """Foydalanuvchi amalini tarixga yozib qo'yadi"""
    import time
    now = int(time.time())
    async with get_db_connection() as db:
        await db.execute(
            "INSERT INTO user_actions (user_id, action, timestamp) VALUES (?, ?, ?)",
            (user_id, action, now)
        )
        await db.commit()

async def get_user_recent_actions(user_id: int, limit: int = 10):
    """Foydalanuvchining oxirgi amallarini olib beradi"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT action, timestamp FROM user_actions WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
            (user_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"action": r[0], "timestamp": r[1]} for r in rows]


async def add_admin(admin_id: int, joined_date: int, admin_date: int):
    """Yangi admin qo'shish"""
    async with get_db_connection() as db:
        await db.execute('''
            INSERT OR REPLACE INTO admins (admin_id, joined_date, admin_date, can_add_admin, can_ban, can_clear_db, can_broadcast, can_manage_users)
            VALUES (?, ?, ?, 1, 1, 1, 1, 1)
        ''', (admin_id, joined_date, admin_date))
        await db.commit()

async def remove_admin(admin_id: int):
    """Adminlikdan olish"""
    async with get_db_connection() as db:
        await db.execute("DELETE FROM admins WHERE admin_id = ?", (admin_id,))
        await db.commit()

async def get_all_admins():
    """Barcha adminlarni olish"""
    async with get_db_connection() as db:
        async with db.execute("""
            SELECT admin_id, joined_date, admin_date, can_add_admin, can_ban, can_clear_db, can_broadcast, can_manage_users
            FROM admins
        """) as cursor:
            rows = await cursor.fetchall()
            return [{
                "admin_id": r[0], "joined_date": r[1], "admin_date": r[2],
                "can_add_admin": bool(r[3]), "can_ban": bool(r[4]), "can_clear_db": bool(r[5]),
                "can_broadcast": bool(r[6]), "can_manage_users": bool(r[7])
            } for r in rows]

async def log_admin_action(admin_id: int, action: str, target_id: int = None, details: str = None):
    """Admin amalini log qilish (audit trail)"""
    import time
    now = int(time.time())
    async with get_db_connection() as db:
        await db.execute(
            "INSERT INTO admin_logs (admin_id, action, target_id, details, timestamp) VALUES (?, ?, ?, ?, ?)",
            (admin_id, action, target_id, details, now)
        )
        await db.commit()

async def get_admin_logs(limit: int = 50, admin_id: int = None):
    """Admin loglarini olish"""
    async with get_db_connection() as db:
        if admin_id:
            async with db.execute(
                "SELECT id, admin_id, action, target_id, details, timestamp FROM admin_logs WHERE admin_id = ? ORDER BY timestamp DESC LIMIT ?",
                (admin_id, limit)
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with db.execute(
                "SELECT id, admin_id, action, target_id, details, timestamp FROM admin_logs ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ) as cursor:
                rows = await cursor.fetchall()
        return [{
            "id": r[0], "admin_id": r[1], "action": r[2], "target_id": r[3],
            "details": r[4], "timestamp": r[5]
        } for r in rows]

async def get_admin_info(admin_id: int):
    """Admin haqida ma'lumot olish"""
    async with get_db_connection() as db:
        async with db.execute("""
            SELECT admin_id, joined_date, admin_date, can_add_admin, can_ban, can_clear_db, can_broadcast, can_manage_users
            FROM admins WHERE admin_id = ?
        """, (admin_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "admin_id": row[0], "joined_date": row[1], "admin_date": row[2],
                "can_add_admin": bool(row[3]), "can_ban": bool(row[4]), "can_clear_db": bool(row[5]),
                "can_broadcast": bool(row[6]), "can_manage_users": bool(row[7])
            }

async def update_admin_permission(admin_id: int, permission: str, value: bool):
    """Admin huquqini o'zgartirish"""
    async with get_db_connection() as db:
        await db.execute(f"UPDATE admins SET {permission} = ? WHERE admin_id = ?", (1 if value else 0, admin_id))
        await db.commit()


async def create_complaint_table(db=None):
    """Shikoyatlar jadvalini yaratish"""
    if db is not None:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS complaints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                subject TEXT NOT NULL,
                message TEXT NOT NULL,
                photo_file_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at INTEGER NOT NULL,
                read_at INTEGER,
                admin_reply TEXT,
                replied_at INTEGER
            )
        ''')
    else:
        async with get_db_connection() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS complaints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    subject TEXT NOT NULL,
                    message TEXT NOT NULL,
                    photo_file_id TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at INTEGER NOT NULL,
                    read_at INTEGER,
                    admin_reply TEXT,
                    replied_at INTEGER
                )
            ''')
            await conn.commit()

async def add_complaint(user_id: int, username: str, first_name: str, subject: str, message: str, photo_file_id: str = None) -> int:
    """Yangi shikoyat qo'shish, shikoyat ID sini qaytaradi"""
    import time
    now = int(time.time())
    async with get_db_connection() as db:
        cursor = await db.execute(
            "INSERT INTO complaints (user_id, username, first_name, subject, message, photo_file_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, username, first_name, subject, message, photo_file_id, now)
        )
        await db.commit()
        return cursor.lastrowid

async def get_complaint_by_id(complaint_id: int):
    """ID bo'yicha shikoyatni olish"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT id, user_id, username, first_name, subject, message, photo_file_id, status, created_at, read_at, admin_reply, replied_at FROM complaints WHERE id = ?",
            (complaint_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0], "user_id": row[1], "username": row[2], "first_name": row[3],
                "subject": row[4], "message": row[5], "photo_file_id": row[6],
                "status": row[7], "created_at": row[8], "read_at": row[9],
                "admin_reply": row[10], "replied_at": row[11]
            }

async def get_all_complaints(limit: int = 50, offset: int = 0):
    """Barcha shikoyatlarni olish (sahifalab)"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT id, user_id, username, first_name, subject, message, photo_file_id, status, created_at, read_at, admin_reply, replied_at FROM complaints ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ) as cursor:
            rows = await cursor.fetchall()
            return [{
                "id": r[0], "user_id": r[1], "username": r[2], "first_name": r[3],
                "subject": r[4], "message": r[5], "photo_file_id": r[6],
                "status": r[7], "created_at": r[8], "read_at": r[9],
                "admin_reply": r[10], "replied_at": r[11]
            } for r in rows]

async def get_complaint_count(status: str = None):
    """Shikoyatlar sonini olish (status bo'yicha)"""
    if status:
        async with get_db_connection() as db:
            async with db.execute("SELECT COUNT(*) FROM complaints WHERE status = ?", (status,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0
    else:
        async with get_db_connection() as db:
            async with db.execute("SELECT COUNT(*) FROM complaints") as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

async def get_pending_complaints(limit: int = 50, offset: int = 0):
    """Kutilayotgan shikoyatlarni olish"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT id, user_id, username, first_name, subject, message, photo_file_id, status, created_at, read_at, admin_reply, replied_at FROM complaints WHERE status = 'pending' ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ) as cursor:
            rows = await cursor.fetchall()
            return [{
                "id": r[0], "user_id": r[1], "username": r[2], "first_name": r[3],
                "subject": r[4], "message": r[5], "photo_file_id": r[6],
                "status": r[7], "created_at": r[8], "read_at": r[9],
                "admin_reply": r[10], "replied_at": r[11]
            } for r in rows]

async def get_complaints_by_status(status: str, limit: int = 50, offset: int = 0):
    """Shikoyatlarni status bo'yicha olish"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT id, user_id, username, first_name, subject, message, photo_file_id, status, created_at, read_at, admin_reply, replied_at FROM complaints WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (status, limit, offset)
        ) as cursor:
            rows = await cursor.fetchall()
            return [{
                "id": r[0], "user_id": r[1], "username": r[2], "first_name": r[3],
                "subject": r[4], "message": r[5], "photo_file_id": r[6],
                "status": r[7], "created_at": r[8], "read_at": r[9],
                "admin_reply": r[10], "replied_at": r[11]
            } for r in rows]

async def has_accepted_chat_terms(user_id: int) -> bool:
    """Foydalanuvchi chat shartlarini qabul qilganmi?"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT 1 FROM chat_terms_accepted WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            return bool(await cursor.fetchone())

async def accept_chat_terms(user_id: int):
    """Chat shartlarini qabul qilish"""
    import time
    now = int(time.time())
    async with get_db_connection() as db:
        await db.execute(
            "INSERT OR IGNORE INTO chat_terms_accepted (user_id, accepted_at) VALUES (?, ?)",
            (user_id, now)
        )
        await db.commit()

async def send_chat_message(sender_id: int, receiver_id: int, message: str, photo_file_id: str = None) -> int:
    """Yangi chat xabarini yuborish"""
    import time
    now = int(time.time())
    async with get_db_connection() as db:
        cursor = await db.execute(
            "INSERT INTO chat_messages (sender_id, receiver_id, message, photo_file_id, timestamp) VALUES (?, ?, ?, ?, ?)",
            (sender_id, receiver_id, message, photo_file_id, now)
        )
        await db.commit()
        return cursor.lastrowid

async def get_chat_messages(user_id: int, other_user_id: int, limit: int = 50, offset: int = 0):
    """Ikki user o'rtasidagi xabarlarni olish"""
    async with get_db_connection() as db:
        async with db.execute(
            """SELECT id, sender_id, receiver_id, message, photo_file_id, timestamp, is_read 
               FROM chat_messages 
               WHERE (sender_id = ? AND receiver_id = ?) OR (sender_id = ? AND receiver_id = ?)
               ORDER BY timestamp DESC LIMIT ? OFFSET ?""",
            (user_id, other_user_id, other_user_id, user_id, limit, offset)
        ) as cursor:
            rows = await cursor.fetchall()
            return [{
                "id": r[0], "sender_id": r[1], "receiver_id": r[2],
                "message": r[3], "photo_file_id": r[4],
                "timestamp": r[5], "is_read": bool(r[6])
            } for r in rows]

async def mark_chat_messages_read(user_id: int, other_user_id: int):
    """Xabarlarni o'qilgan deb belgilash"""
    async with get_db_connection() as db:
        await db.execute(
            "UPDATE chat_messages SET is_read = 1 WHERE sender_id = ? AND receiver_id = ?",
            (other_user_id, user_id)
        )
        await db.commit()

async def get_unread_chat_count(user_id: int) -> int:
    """O'qilmagan chat xabarlari soni"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE receiver_id = ? AND is_read = 0",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_chat_history(user_id: int, other_user_id: int = None, limit: int = 50, offset: int = 0):
    """Chat tarixini olish.
    
    Agar other_user_id berilsa → ikki user o'rtasidagi xabarlar (get_chat_messages bilan bir xil).
    Agar other_user_id berilmasa → foydalanuvchining BARCHA chatlari ro'yxati.
    """
    if other_user_id:
        return await get_chat_messages(user_id, other_user_id, limit, offset)
    
    # All chats for user
    async with get_db_connection() as db:
        async with db.execute(
            """SELECT DISTINCT
               CASE WHEN sender_id = ? THEN receiver_id ELSE sender_id END as other_user_id,
               MAX(timestamp) as last_timestamp
               FROM chat_messages
               WHERE sender_id = ? OR receiver_id = ?
               GROUP BY other_user_id
               ORDER BY last_timestamp DESC""",
            (user_id, user_id, user_id)
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"other_user_id": r[0], "last_timestamp": r[1]} for r in rows]


async def block_user(blocker_id: int, blocked_id: int):
    """Foydalanuvchini bloklash"""
    import time
    now = int(time.time())
    async with get_db_connection() as db:
        await db.execute(
            "INSERT OR REPLACE INTO chat_blocks (blocker_id, blocked_id, timestamp) VALUES (?, ?, ?)",
            (blocker_id, blocked_id, now)
        )
        await db.commit()

async def unblock_user(blocker_id: int, blocked_id: int):
    """Foydalanuvchini blokdan chiqarish"""
    async with get_db_connection() as db:
        await db.execute(
            "DELETE FROM chat_blocks WHERE blocker_id = ? AND blocked_id = ?",
            (blocker_id, blocked_id)
        )
        await db.commit()

async def is_user_blocked(blocker_id: int, blocked_id: int) -> bool:
    """Bloklanganmi?"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT 1 FROM chat_blocks WHERE blocker_id = ? AND blocked_id = ?",
            (blocker_id, blocked_id)
        ) as cursor:
            return bool(await cursor.fetchone())

async def get_blocked_users(user_id: int):
    """Foydalanuvchi tomonidan bloklanganlar ro'yxati"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT blocked_id, timestamp FROM chat_blocks WHERE blocker_id = ?",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"blocked_id": r[0], "timestamp": r[1]} for r in rows]

async def mute_user(muter_id: int, muted_id: int):
    """Foydalanuvchini mute qilish"""
    import time
    now = int(time.time())
    async with get_db_connection() as db:
        await db.execute(
            "INSERT OR REPLACE INTO chat_mutes (muter_id, muted_id, timestamp) VALUES (?, ?, ?)",
            (muter_id, muted_id, now)
        )
        await db.commit()

async def unmute_user(muter_id: int, muted_id: int):
    """Foydalanuvchini mute'dan chiqarish"""
    async with get_db_connection() as db:
        await db.execute(
            "DELETE FROM chat_mutes WHERE muter_id = ? AND muted_id = ?",
            (muter_id, muted_id)
        )
        await db.commit()

async def is_user_muted(muter_id: int, muted_id: int) -> bool:
    """Mute qilinganmi?"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT 1 FROM chat_mutes WHERE muter_id = ? AND muted_id = ?",
            (muter_id, muted_id)
        ) as cursor:
            return bool(await cursor.fetchone())

async def get_muted_users(user_id: int):
    """Foydalanuvchi tomonidan mute qilinganlar ro'yxati"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT muted_id, timestamp FROM chat_mutes WHERE muter_id = ?",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"muted_id": r[0], "timestamp": r[1]} for r in rows]

async def get_all_chats_for_owner(user_id: int):
    """Owner uchun barcha chatlar ro'yxati"""
    async with get_db_connection() as db:
        async with db.execute("""
            SELECT DISTINCT 
                CASE WHEN sender_id = ? THEN receiver_id ELSE sender_id END as other_user_id
            FROM chat_messages
            WHERE sender_id = ? OR receiver_id = ?
        """, (user_id, user_id, user_id)) as cursor:
            rows = await cursor.fetchall()
            return [{"user_id": r[0]} for r in rows]

async def get_chat_messages_for_owner(user_id: int, other_user_id: int, limit: int = 50, offset: int = 0):
    """Owner uchun chat xabarlarini olish"""
    return await get_chat_messages(user_id, other_user_id, limit, offset)

async def delete_chat(user_id: int, other_user_id: int):
    """Chat xabarlarini o'chirish"""
    async with get_db_connection() as db:
        await db.execute(
            "DELETE FROM chat_messages WHERE (sender_id = ? AND receiver_id = ?) OR (sender_id = ? AND receiver_id = ?)",
            (user_id, other_user_id, other_user_id, user_id)
        )
        await db.commit()

async def has_chat_before(user_id: int, other_user_id: int) -> bool:
    """Oldin chat bo'lganmi?"""
    async with get_db_connection() as db:
        async with db.execute(
            """SELECT 1 FROM chat_messages 
               WHERE (sender_id = ? AND receiver_id = ?) OR (sender_id = ? AND receiver_id = ?)""",
            (user_id, other_user_id, other_user_id, user_id)
        ) as cursor:
            return bool(await cursor.fetchone())

async def mark_complaint_read(complaint_id: int):
    """Shikoyatni o'qilgan deb belgilash"""
    import time
    now = int(time.time())
    async with get_db_connection() as db:
        await db.execute("UPDATE complaints SET read_at = ?, status = 'read' WHERE id = ?", (now, complaint_id))
        await db.commit()

async def reply_to_complaint(complaint_id: int, admin_reply: str):
    """Shikoyatga javob yozish"""
    import time
    now = int(time.time())
    async with get_db_connection() as db:
        await db.execute(
            "UPDATE complaints SET admin_reply = ?, replied_at = ?, status = 'replied' WHERE id = ?",
            (admin_reply, now, complaint_id)
        )
        await db.commit()



async def add_utag_timer(user_id: int, chat_id: int, message_text: str, interval_minutes: int, repeat_count: int = 1, repeat_delay: int = 5):
    """Yangi utag taymer qo'shish yoki yangilash"""
    import time
    now = int(time.time())
    async with get_db_connection() as db:
        await db.execute('''
            INSERT OR REPLACE INTO utag_timers (user_id, chat_id, message_text, interval_minutes, repeat_count, repeat_delay, is_active, last_sent, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?)
        ''', (user_id, chat_id, message_text, interval_minutes, repeat_count, repeat_delay, now))
        await db.commit()

async def get_utag_timer(user_id: int, chat_id: int):
    """Guruh uchun taymer sozlamalarini olish"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT id, user_id, chat_id, message_text, interval_minutes, repeat_count, repeat_delay, is_active, last_sent, created_at FROM utag_timers WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0], "user_id": row[1], "chat_id": row[2],
                "message_text": row[3], "interval_minutes": row[4],
                "repeat_count": row[5], "repeat_delay": row[6],
                "is_active": bool(row[7]), "last_sent": row[8], "created_at": row[9]
            }

async def get_user_utag_timers(user_id: int):
    """Foydalanuvchining barcha taymerlarini olish"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT id, chat_id, message_text, interval_minutes, repeat_count, repeat_delay, is_active, last_sent FROM utag_timers WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [{
                "id": r[0], "chat_id": r[1], "message_text": r[2],
                "interval_minutes": r[3], "repeat_count": r[4], "repeat_delay": r[5],
                "is_active": bool(r[6]), "last_sent": r[7]
            } for r in rows]

async def update_utag_timer_last_sent(timer_id: int, timestamp: int):
    """Taymerning last_sent maydonini yangilash"""
    async with get_db_connection() as db:
        await db.execute("UPDATE utag_timers SET last_sent = ? WHERE id = ?", (timestamp, timer_id))
        await db.commit()

async def set_utag_timer_active(timer_id: int, is_active: bool):
    """Taymer faolligini o'zgartirish"""
    async with get_db_connection() as db:
        await db.execute("UPDATE utag_timers SET is_active = ? WHERE id = ?", (1 if is_active else 0, timer_id))
        await db.commit()

async def delete_utag_timer(user_id: int, chat_id: int):
    """Taymerni o'chirish"""
    async with get_db_connection() as db:
        await db.execute("DELETE FROM utag_timers WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
        await db.commit()

async def get_all_active_utag_timers():
    """Barcha faol taymerlarni olish (background task uchun)"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT id, user_id, chat_id, message_text, interval_minutes, repeat_count, repeat_delay, last_sent FROM utag_timers WHERE is_active = 1"
        ) as cursor:
            rows = await cursor.fetchall()
            return [{
                "id": r[0], "user_id": r[1], "chat_id": r[2],
                "message_text": r[3], "interval_minutes": r[4],
                "repeat_count": r[5], "repeat_delay": r[6], "last_sent": r[7]
            } for r in rows]


async def save_massdm_progress(user_id: int, group_id: str, last_index: int):
    """MassDM progressni bazaga saqlash (bot restartda ham saqlanadi)"""
    import time
    now = int(time.time())
    async with get_db_connection() as db:
        await db.execute('''
            INSERT INTO massdm_progress (user_id, group_id, last_index, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, group_id) DO UPDATE SET last_index = ?, updated_at = ?
        ''', (user_id, group_id, last_index, now, last_index, now))
        await db.commit()

async def get_massdm_progress(user_id: int, group_id: str) -> int:
    """MassDM progressni bazadan olish"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT last_index FROM massdm_progress WHERE user_id = ? AND group_id = ?",
            (user_id, group_id)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def reset_massdm_progress(user_id: int, group_id: str):
    """MassDM progressni o'chirish (start fresh)"""
    async with get_db_connection() as db:
        await db.execute(
            "DELETE FROM massdm_progress WHERE user_id = ? AND group_id = ?",
            (user_id, group_id)
        )
        await db.commit()

async def save_massdm_setting(user_id: int, auto_stop_on_high_risk: bool):
    """MassDM user sozlamasini bazaga saqlash"""
    async with get_db_connection() as db:
        await db.execute('''
            INSERT INTO massdm_settings (user_id, auto_stop_on_high_risk)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET auto_stop_on_high_risk = ?
        ''', (user_id, 1 if auto_stop_on_high_risk else 0, 1 if auto_stop_on_high_risk else 0))
        await db.commit()

async def get_massdm_setting(user_id: int) -> bool:
    """MassDM user sozlamasini bazadan olish"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT auto_stop_on_high_risk FROM massdm_settings WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row[0]) if row else False

async def save_auto_stopped_task(stop_key: str, user_id: int, group_id: str, resume_after: int, reason: str, message_to_copy_id: int, delay_hours: int):
    """Auto-stopped taskni bazaga saqlash"""
    import time
    now = int(time.time())
    async with get_db_connection() as db:
        await db.execute('''
            INSERT OR REPLACE INTO massdm_auto_stopped (stop_key, user_id, group_id, resume_after, reason, message_to_copy_id, delay_hours, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (stop_key, user_id, group_id, resume_after, reason, message_to_copy_id, delay_hours, now))
        await db.commit()

async def get_auto_stopped_task(stop_key: str):
    """Auto-stopped taskni bazadan olish"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT stop_key, user_id, group_id, resume_after, reason, message_to_copy_id, delay_hours, created_at FROM massdm_auto_stopped WHERE stop_key = ?",
            (stop_key,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "stop_key": row[0], "user_id": row[1], "group_id": row[2],
                "resume_after": row[3], "reason": row[4],
                "message_to_copy_id": row[5], "delay_hours": row[6], "created_at": row[7]
            }

async def delete_auto_stopped_task(stop_key: str):
    """Auto-stopped taskni bazadan o'chirish"""
    async with get_db_connection() as db:
        await db.execute("DELETE FROM massdm_auto_stopped WHERE stop_key = ?", (stop_key,))
        await db.commit()

async def get_all_auto_stopped_tasks_for_user(user_id: int):
    """Foydalanuvchining barcha auto-stopped tasklarini olish"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT stop_key, user_id, group_id, resume_after, reason, message_to_copy_id, delay_hours, created_at FROM massdm_auto_stopped WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [{
                "stop_key": r[0], "user_id": r[1], "group_id": r[2],
                "resume_after": r[3], "reason": r[4],
                "message_to_copy_id": r[5], "delay_hours": r[6], "created_at": r[7]
            } for r in rows]

async def save_user_custom_command(user_id: int, command: str, message: str, created_at: int):
    """Save custom command to database"""
    async with get_db_connection() as db:
        await db.execute('''
            INSERT OR REPLACE INTO utag_custom_commands (user_id, command, message, created_at)
            VALUES (?, ?, ?, ?)
        ''', (user_id, command, message, created_at))
        await db.commit()

async def delete_user_custom_command(user_id: int, command: str):
    """Delete custom command from database"""
    async with get_db_connection() as db:
        await db.execute(
            "DELETE FROM utag_custom_commands WHERE user_id = ? AND command = ?",
            (user_id, command)
        )
        await db.commit()

async def get_user_custom_commands(user_id: int):
    """Get all custom commands for a specific user"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT user_id, command, message, created_at FROM utag_custom_commands WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [{
                "user_id": r[0],
                "command": r[1],
                "message": r[2],
                "created_at": r[3]
            } for r in rows]

async def get_all_custom_commands():
    """Get all custom commands in the database (for cache initialization)"""
    async with get_db_connection() as db:
        async with db.execute(
            "SELECT user_id, command, message, created_at FROM utag_custom_commands"
        ) as cursor:
            rows = await cursor.fetchall()
            return [{
                "user_id": r[0],
                "command": r[1],
                "message": r[2],
                "created_at": r[3]
            } for r in rows]
