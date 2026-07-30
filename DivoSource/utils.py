import urllib.parse

def format_account_info(phone: str, status: str) -> str:
    return f"📱 <b>الرقم:</b> {phone}\n🔋 <b>الحالة:</b> {status}"

def format_header(text: str) -> str:
    return f"✨ <b>{text}</b> ✨"

def format_separator() -> str:
    return "────────────────────"

async def is_admin(user_id: int, owner_id: int) -> bool:
    return user_id == owner_id

def parse_proxy(proxy_url: str):
  
    if not proxy_url:
        return None
    try:
        parsed = urllib.parse.urlparse(proxy_url)
        proxy_dict = {
            'proxy_type': parsed.scheme.lower(), # 'http', 'socks5', 'socks4'
            'addr': parsed.hostname,
            'port': parsed.port,
            'rdns': True
        }
        if parsed.username:
            proxy_dict['username'] = parsed.username
        if parsed.password:
            proxy_dict['password'] = parsed.password
            
        return proxy_dict
    except Exception:
        return None

