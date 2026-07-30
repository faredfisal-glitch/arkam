import imaplib
import email
import re
import asyncio
import time
import random
import hashlib
from DivoSource.logger import logger
from config import GMAIL_USER, GMAIL_APP_PASSWORD

def generate_dot_variant(email_addr, phone=None):
    """توليد نسخة احترافية من البريد (بدون + لتجنب الحظر السريع) تعتمد على النقاط وحالة الأحرف"""
    if not email_addr or "@gmail.com" not in email_addr.lower():
        return email_addr
        
    try:
        username, domain = email_addr.split('@')
        # تنظيف اليوزر تماماً
        username = username.split('+')[0].replace('.', '')
        
        # استخدام الرقم كبذرة لضمان الثبات
        state = random.getstate()
        random.seed(phone if phone else username)
        
        chars = list(username)
        # 1. تغيير حالة الأحرف عشوائياً (خدعة فعالة جداً وغير مريبة)
        for i in range(len(chars)):
            if chars[i].isalpha():
                if random.choice([True, False]):
                    chars[i] = chars[i].upper()
                else:
                    chars[i] = chars[i].lower()
        
        # 2. إضافة نقاط في أماكن عشوائية
        if len(chars) > 2:
            num_dots = random.randint(1, min(2, len(chars) - 1))
            indices = sorted(random.sample(range(1, len(chars)), num_dots))
            offset = 0
            for idx in indices:
                chars.insert(idx + offset, '.')
                offset += 1
        
        # 3. إضافة علامة + ورقم من 1 إلى 9 (بناءً على طلب المستخدم)
        plus_suffix = f"+{random.randint(1, 9)}"
        
        random.setstate(state)
        return "".join(chars) + plus_suffix + "@" + domain
    except Exception as e:
        logger.error(f"Error generating email variant: {e}")
        return email_addr

async def fetch_telegram_code(max_retries=15):
    """البحث عن كود تليجرام في Gmail مع تحسينات للسرعة"""
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        logger.warning("Gmail credentials not set in config.py")
        return None

    def _get_code():
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=10)
            mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            mail.select("inbox")
            
            # البحث عن الرسائل غير المقروءة أولاً
            status, messages = mail.search(None, '(UNSEEN FROM "noreply@telegram.org")')
            
            if status != "OK" or not messages[0]:
                status, messages = mail.search(None, '(FROM "noreply@telegram.org")')
                
            code = None
            if status == "OK" and messages[0]:
                msg_ids = messages[0].split()
                last_msg_id = msg_ids[-1]
                status, msg_data = mail.fetch(last_msg_id, "(RFC822)")
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        
                        body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() == "text/plain":
                                    body = part.get_payload(decode=True).decode()
                        else:
                            body = msg.get_payload(decode=True).decode()
                            
                        match = re.search(r'\b(\d(?:\s*\d){4,5})\b', body)
                        if match:
                            code = match.group(1).replace(" ", "").replace("\t", "")
                            mail.store(last_msg_id, '+FLAGS', '\\Seen')
                            
            mail.logout()
            return code
        except Exception as e:
            logger.error(f"IMAP Error: {e}")
            return None

    for attempt in range(max_retries):
        await asyncio.sleep(3) # فحص كل 3 ثواني بدلاً من 4 للسرعة
        code = await asyncio.to_thread(_get_code)
        if code:
            return code
    return None
