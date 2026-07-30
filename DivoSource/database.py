import aiosqlite
import os

from config import DB_PATH
from DivoSource.logger import logger

async def init_db():
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        await db.execute('PRAGMA journal_mode=WAL')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS accounts (
                phone TEXT PRIMARY KEY,
                session_name TEXT,
                status TEXT,
                proxy TEXT,
                twofa_password TEXT
            )
        ''')
        async with db.execute("PRAGMA table_info(accounts)") as cursor:
            columns = [row[1] async for row in cursor]
        if "twofa_password" not in columns:
            await db.execute('ALTER TABLE accounts ADD COLUMN twofa_password TEXT')
        if "added_by" not in columns:
            await db.execute('ALTER TABLE accounts ADD COLUMN added_by TEXT')
        if "email" not in columns:
            await db.execute('ALTER TABLE accounts ADD COLUMN email TEXT')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS proxies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proxy_url TEXT,
                status TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS suppliers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                display_name TEXT,
                info TEXT
            )
        ''')
        async with db.execute("PRAGMA table_info(suppliers)") as cursor:
            cols = [row[1] async for row in cursor]
        if "display_name" not in cols:
            await db.execute('ALTER TABLE suppliers ADD COLUMN display_name TEXT')

        await db.execute('''
            CREATE TABLE IF NOT EXISTS buyers (
                user_id TEXT PRIMARY KEY,
                name TEXT,
                allowed_limit INTEGER,
                pulled_count INTEGER DEFAULT 0
            )
        ''')
        async with db.execute("PRAGMA table_info(buyers)") as cursor:
            cols = [row[1] async for row in cursor]
        if "name" not in cols:
            await db.execute('ALTER TABLE buyers ADD COLUMN name TEXT')

        await db.execute('''
            CREATE TABLE IF NOT EXISTS transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transfer_type TEXT,
                source_link TEXT,
                target_link TEXT,
                status TEXT,
                success_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                target_successful_count INTEGER,
                members_data TEXT
            )
        ''')
        async with db.execute("PRAGMA table_info(transfers)") as cursor:
            cols = [row[1] async for row in cursor]
        if "members_data" not in cols:
            await db.execute('ALTER TABLE transfers ADD COLUMN members_data TEXT')
        if "adds_per_account" not in cols:
            await db.execute('ALTER TABLE transfers ADD COLUMN adds_per_account INTEGER DEFAULT 40')

        await db.execute('''
            CREATE TABLE IF NOT EXISTS votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vote_type TEXT,
                target_link TEXT,
                target_count INTEGER,
                success_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                status TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS vote_accounts (
                vote_id INTEGER,
                phone TEXT,
                UNIQUE(vote_id, phone)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_link TEXT,
                reaction_type TEXT,
                target_count INTEGER,
                success_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                status TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS reaction_accounts (
                reaction_id INTEGER,
                phone TEXT,
                UNIQUE(reaction_id, phone)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id TEXT PRIMARY KEY,
                last_video_date TEXT,
                extra_videos INTEGER DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS referrals (
                new_user_id TEXT PRIMARY KEY,
                referrer_id TEXT
            )
        ''')
        await db.commit()
    logger.info("تم تهيئة قاعدة البيانات بنجاح.")

async def add_account(phone: str, session_name: str, proxy: str = None, twofa_password: str = None, added_by: str = None):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        await db.execute(
            'INSERT OR REPLACE INTO accounts (phone, session_name, status, proxy, twofa_password, added_by) VALUES (?, ?, ?, ?, ?, ?)',
            (phone, session_name, 'active', proxy, twofa_password, str(added_by) if added_by else None)
        )
        await db.commit()

async def get_accounts():
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        async with db.execute('SELECT phone, session_name, status, proxy, twofa_password, email FROM accounts') as cursor:
            return await cursor.fetchall()

async def get_account_by_phone(phone: str):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        async with db.execute(
            'SELECT phone, session_name, status, proxy, twofa_password FROM accounts WHERE phone = ?',
            (phone,)
        ) as cursor:
            return await cursor.fetchone()

async def update_account_status(phone: str, status: str):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        await db.execute('UPDATE accounts SET status = ? WHERE phone = ?', (status, phone))
        await db.commit()

async def update_account_2fa_password(phone: str, twofa_password: str = None):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        await db.execute(
            'UPDATE accounts SET twofa_password = ? WHERE phone = ?',
            (twofa_password, phone)
        )
        await db.commit()

async def update_account_email(phone: str, email: str = None):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        await db.execute(
            'UPDATE accounts SET email = ? WHERE phone = ?',
            (email, phone)
        )
        await db.commit()

async def delete_account_from_db(phone: str):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        await db.execute('DELETE FROM accounts WHERE phone = ?', (phone,))
        await db.commit()

async def get_proxies():
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        async with db.execute('SELECT proxy_url FROM proxies WHERE status="active"') as cursor:
            return [row[0] async for row in cursor]

async def add_reseller(user_id: str, display_name: str = None):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        await db.execute(
            'INSERT INTO suppliers (name, display_name, info) VALUES (?, ?, ?)',
            (str(user_id), display_name, "reseller")
        )
        await db.commit()

async def remove_reseller(user_id: str):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        await db.execute('DELETE FROM suppliers WHERE name = ?', (str(user_id),))
        await db.commit()

async def get_all_resellers():
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        async with db.execute('SELECT name, display_name FROM suppliers') as cursor:
            return await cursor.fetchall()

async def is_reseller(user_id: int):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        async with db.execute('SELECT 1 FROM suppliers WHERE name = ?', (str(user_id),)) as cursor:
            return await cursor.fetchone() is not None

async def get_seller_stats(user_id: str):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        async with db.execute('SELECT COUNT(*) FROM accounts WHERE added_by = ?', (str(user_id),)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def add_buyer(user_id: str, limit: int, name: str = None):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        await db.execute(
            'INSERT OR REPLACE INTO buyers (user_id, name, allowed_limit, pulled_count) VALUES (?, ?, ?, ?)',
            (str(user_id), name, limit, 0)
        )
        await db.commit()

async def remove_buyer(user_id: str):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        await db.execute('DELETE FROM buyers WHERE user_id = ?', (str(user_id),))
        await db.commit()

async def get_all_buyers():
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        async with db.execute('SELECT user_id, name, allowed_limit, pulled_count FROM buyers') as cursor:
            return await cursor.fetchall()

async def is_buyer(user_id: int):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        async with db.execute('SELECT 1 FROM buyers WHERE user_id = ?', (str(user_id),)) as cursor:
            return await cursor.fetchone() is not None

async def get_buyer_info(user_id: int):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        async with db.execute('SELECT allowed_limit, pulled_count FROM buyers WHERE user_id = ?', (str(user_id),)) as cursor:
            return await cursor.fetchone()

async def increment_buyer_pulls(user_id: int):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        await db.execute('UPDATE buyers SET pulled_count = pulled_count + 1 WHERE user_id = ?', (str(user_id),))
        await db.commit()

# --- Transfer Management ---
async def create_transfer(transfer_type: str, source_link: str, target_link: str, target_count: int, members_data: str = None, adds_per_account: int = 40):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        cursor = await db.execute(
            'INSERT INTO transfers (transfer_type, source_link, target_link, status, target_successful_count, members_data, adds_per_account) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (transfer_type, source_link, target_link, 'running', target_count, members_data, adds_per_account)
        )
        await db.commit()
        return cursor.lastrowid

async def get_transfer(transfer_id: int):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        async with db.execute('SELECT id, transfer_type, source_link, target_link, status, success_count, failed_count, target_successful_count, members_data, adds_per_account FROM transfers WHERE id = ?', (transfer_id,)) as cursor:
            return await cursor.fetchone()

async def update_transfer_status(transfer_id: int, status: str):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        await db.execute('UPDATE transfers SET status = ? WHERE id = ?', (status, transfer_id))
        await db.commit()

async def increment_transfer_progress(transfer_id: int, success: bool = True):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        if success:
            await db.execute('UPDATE transfers SET success_count = success_count + 1 WHERE id = ?', (transfer_id,))
        else:
            await db.execute('UPDATE transfers SET failed_count = failed_count + 1 WHERE id = ?', (transfer_id,))
        await db.commit()

async def update_transfer_members_data(transfer_id: int, members_data: str):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        await db.execute('UPDATE transfers SET members_data = ? WHERE id = ?', (members_data, transfer_id))
        await db.commit()

# --- Voting Management ---
async def create_vote(vote_type: str, target_link: str, target_count: int):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        cursor = await db.execute(
            'INSERT INTO votes (vote_type, target_link, target_count, status) VALUES (?, ?, ?, ?)',
            (vote_type, target_link, target_count, 'running')
        )
        await db.commit()
        return cursor.lastrowid

async def get_vote(vote_id: int):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        async with db.execute('SELECT id, vote_type, target_link, target_count, success_count, failed_count, status FROM votes WHERE id = ?', (vote_id,)) as cursor:
            return await cursor.fetchone()

async def update_vote_status(vote_id: int, status: str):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        await db.execute('UPDATE votes SET status = ? WHERE id = ?', (status, vote_id))
        await db.commit()

async def increment_vote_progress(vote_id: int, success: bool = True):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        if success:
            await db.execute('UPDATE votes SET success_count = success_count + 1 WHERE id = ?', (vote_id,))
        else:
            await db.execute('UPDATE votes SET failed_count = failed_count + 1 WHERE id = ?', (vote_id,))
        await db.commit()

async def add_vote_account(vote_id: int, phone: str):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        try:
            await db.execute('INSERT INTO vote_accounts (vote_id, phone) VALUES (?, ?)', (vote_id, phone))
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

async def has_account_voted(vote_id: int, phone: str):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        async with db.execute('SELECT 1 FROM vote_accounts WHERE vote_id = ? AND phone = ?', (vote_id, phone)) as cursor:
            return await cursor.fetchone() is not None

# --- Reaction Management ---
async def create_reaction(target_link: str, reaction_type: str, target_count: int):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        cursor = await db.execute(
            'INSERT INTO reactions (target_link, reaction_type, target_count, status) VALUES (?, ?, ?, ?)',
            (target_link, reaction_type, target_count, 'running')
        )
        await db.commit()
        return cursor.lastrowid

async def get_reaction(reaction_id: int):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        async with db.execute('SELECT id, target_link, reaction_type, target_count, success_count, failed_count, status FROM reactions WHERE id = ?', (reaction_id,)) as cursor:
            return await cursor.fetchone()

async def update_reaction_status(reaction_id: int, status: str):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        await db.execute('UPDATE reactions SET status = ? WHERE id = ?', (status, reaction_id))
        await db.commit()

async def increment_reaction_progress(reaction_id: int, success: bool = True):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        if success:
            await db.execute('UPDATE reactions SET success_count = success_count + 1 WHERE id = ?', (reaction_id,))
        else:
            await db.execute('UPDATE reactions SET failed_count = failed_count + 1 WHERE id = ?', (reaction_id,))
        await db.commit()

async def add_reaction_account(reaction_id: int, phone: str):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        try:
            await db.execute('INSERT INTO reaction_accounts (reaction_id, phone) VALUES (?, ?)', (reaction_id, phone))
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

async def has_account_reacted(reaction_id: int, phone: str):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        async with db.execute('SELECT 1 FROM reaction_accounts WHERE reaction_id = ? AND phone = ?', (reaction_id, phone)) as cursor:
            return await cursor.fetchone() is not None

async def get_all_reactions():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT id, target_link, reaction_type, target_count, success_count, failed_count, status FROM reactions ORDER BY id DESC LIMIT 20') as cursor:
            return await cursor.fetchall()

async def get_or_create_user_stats(user_id: str):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        async with db.execute('SELECT last_video_date, extra_videos FROM user_stats WHERE user_id = ?', (str(user_id),)) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"last_video_date": row[0], "extra_videos": row[1]}
            
            # إنشاء قيمة افتراضية
            await db.execute(
                'INSERT INTO user_stats (user_id, last_video_date, extra_videos) VALUES (?, ?, ?)',
                (str(user_id), "", 0)
            )
            await db.commit()
            return {"last_video_date": "", "extra_videos": 0}

async def update_last_video_date(user_id: str, date_str: str):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        await db.execute('UPDATE user_stats SET last_video_date = ? WHERE user_id = ?', (date_str, str(user_id)))
        await db.commit()

async def consume_extra_video(user_id: str):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        await db.execute('UPDATE user_stats SET extra_videos = MAX(0, extra_videos - 1) WHERE user_id = ?', (str(user_id),))
        await db.commit()

async def record_referral(new_user_id: str, referrer_id: str):
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        # منع إحالة النفس
        if str(new_user_id) == str(referrer_id):
            return False
        # التحقق مما إذا كان المستخدم مسجلاً إحالة مسبقاً لمنع التكرار
        async with db.execute('SELECT 1 FROM referrals WHERE new_user_id = ?', (str(new_user_id),)) as cursor:
            if await cursor.fetchone():
                return False
        # التحقق مما إذا كان قد سجل حسابه بالفعل لمنع إحالة الحسابات القديمة
        async with db.execute('SELECT 1 FROM accounts WHERE added_by = ?', (str(new_user_id),)) as cursor:
            if await cursor.fetchone():
                return False
                
        await db.execute(
            'INSERT OR IGNORE INTO referrals (new_user_id, referrer_id) VALUES (?, ?)',
            (str(new_user_id), str(referrer_id))
        )
        await db.commit()
        return True

async def reward_referrer_if_any(new_user_id: str):
    """التحقق من وجود إحالة لهذا المستخدم ومكافأة المحيل بـ +1 فيديو"""
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        async with db.execute('SELECT referrer_id FROM referrals WHERE new_user_id = ?', (str(new_user_id),)) as cursor:
            row = await cursor.fetchone()
            if row:
                referrer_id = row[0]
                # التحقق والتأكد من وجود سجل للمحيل أولاً
                async with db.execute('SELECT 1 FROM user_stats WHERE user_id = ?', (str(referrer_id),)) as check_cursor:
                    if not await check_cursor.fetchone():
                        await db.execute('INSERT INTO user_stats (user_id, last_video_date, extra_videos) VALUES (?, ?, ?)', (str(referrer_id), "", 0))
                
                await db.execute('UPDATE user_stats SET extra_videos = extra_videos + 1 WHERE user_id = ?', (str(referrer_id),))
                # حذف الإحالة لعدم تكرار المكافأة
                await db.execute('DELETE FROM referrals WHERE new_user_id = ?', (str(new_user_id),))
                await db.commit()
                return referrer_id
        return None
