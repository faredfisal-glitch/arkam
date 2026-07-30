import os
import sqlite3
import base64
import struct
from telethon.sessions import StringSession, SQLiteSession
from config import SESSIONS_DIR, API_ID

def get_session_path(phone: str):
    return os.path.join(SESSIONS_DIR, f"session_{phone.replace('+', '')}.session")

async def generate_telethon_string(phone: str):
    path = get_session_path(phone)
    if not os.path.exists(path):
        return None
    
    try:
        # Use Telethon's own logic to convert SQLite to String
        session = SQLiteSession(path)
        # We need to load it to get the data
        from telethon.crypto import AuthKey
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute("SELECT dc_id, server_address, port, auth_key FROM sessions")
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return None
            
        # Telethon StringSession format: 
        # (version, dc_id, ip, port, auth_key)
        # However, StringSession.save() expects the session object to have these.
        # Let's do it manually to be safe and avoid async issues with session files.
        import ipaddress
        dc_id, ip, port, auth_key = row
        ip_bytes = ipaddress.ip_address(ip).packed
        data = struct.pack(">B B 4s H 256s", 1, dc_id, ip_bytes, port, auth_key)
        return base64.urlsafe_b64encode(data).decode('ascii')
    except Exception as e:
        print(f"Error generating Telethon string: {e}")
        return None

async def generate_pyrogram_string(phone: str):
    path = get_session_path(phone)
    if not os.path.exists(path):
        return None
    
    try:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute("SELECT dc_id, auth_key FROM sessions")
        row = cur.fetchone()
        
        # Try to get user_id from entities or version
        # In Telethon, the 'entities' table usually has the owner if you've fetched 'me'
        cur.execute("SELECT id FROM entities WHERE id > 0 LIMIT 1")
        user_row = cur.fetchone()
        user_id = user_row[0] if user_row else 0
        
        conn.close()
        
        if not row:
            return None
            
        dc_id, auth_key = row
        
        # Pyrogram V2 String Format:
        # Packed: DC_ID (1) + API_ID (4) + TEST_MODE (1) + AUTH_KEY (256) + USER_ID (8) + IS_BOT (1)
        # Note: API_ID is normally 4 bytes. We use the one from config.
        api_id_int = int(API_ID)
        test_mode = False
        is_bot = False
        
        # Format string for struct.pack
        # > : Big Endian
        # B : Unsigned Char (1 byte) -> DC_ID
        # i : Signed Int (4 bytes) -> API_ID
        # ? : Bool (1 byte) -> TEST_MODE
        # 256s : 256 bytes -> AUTH_KEY
        # Q : Unsigned Long Long (8 bytes) -> USER_ID
        # ? : Bool (1 byte) -> IS_BOT
        
        packed = struct.pack(">Bi?256sQ?", dc_id, api_id_int, test_mode, auth_key, user_id, is_bot)
        return base64.urlsafe_b64encode(packed).decode('ascii')
    except Exception as e:
        print(f"Error generating Pyrogram string: {e}")
        return None
