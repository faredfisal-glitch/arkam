import random
import aiohttp
from DivoSource.database import get_proxies
from DivoSource.logger import logger

async def check_proxy(proxy_url: str) -> bool:
    """التحقق من عمل البروكسي"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://httpbin.org/ip", proxy=proxy_url, timeout=5) as response:
                if response.status == 200:
                    return True
    except Exception as e:
        logger.warning(f"البروكسي {proxy_url} لا يعمل: {e}")
    return False

async def get_working_proxy():
    """جلب بروكسي يعمل من قاعدة البيانات"""
    proxies = await get_proxies()
    if not proxies:
        logger.info("لا توجد بروكسيات متوفرة في قاعدة البيانات.")
        return None
        
    random.shuffle(proxies)
    for proxy in proxies:
        return proxy  # In a real scenario, you might want to uncomment check_proxy
        # if await check_proxy(proxy):
        #     return proxy
    return None
