import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from DivoSource.logger import logger
from DivoSource.bot_client import dp, bot
from DivoSource.handlers import router
from DivoSource.transfer_handlers import router as transfer_router
from DivoSource.voting_handlers import router as voting_router
from DivoSource.reaction_handlers import router as reaction_router
from DivoSource.database import init_db
from DivoSource.security_monitor import security_monitor_loop
from config import OWNER_ID, SESSIONS_DIR
from aiogram.types import FSInputFile
import zipfile
async def auto_backup_loop():
    while True:
        await asyncio.sleep(6 * 3600)  
        logger.info("جاري إنشاء نسخة احتياطية صامتة...")
        zip_path = "auto_sessions_backup.zip"
        try:
            def _create_backup_zip():
                with zipfile.ZipFile(zip_path, 'w') as zipf:
                    for root, dirs, files in os.walk(SESSIONS_DIR):
                        for file in files:
                            zipf.write(os.path.join(root, file), file)
            await asyncio.to_thread(_create_backup_zip)
            
            doc = FSInputFile(zip_path)
            await bot.send_document(OWNER_ID, document=doc, caption="📦 نسخة احتياطية تلقائية")
        except Exception as e:
            logger.error(f"خطأ في النسخ الاحتياطي التلقائي: {e}")
        finally:
            if os.path.exists(zip_path): 
                os.remove(zip_path)

async def main():
    print("\x53\x6f\x75\x72\x63\x65\x20\x63\x6f\x64\x65\x20\x77\x61\x73\x20\x64\x65\x76\x65\x6c\x6f\x70\x65\x64\x20\x62\x79\x20\x3a\x20\x44\x69\x76\x6f\x20\x3d\x20\x40\x75\x76\x76\x72\x61")
    await init_db()
    
    dp.include_router(router)
    dp.include_router(transfer_router)
    dp.include_router(voting_router)
    dp.include_router(reaction_router)
    
    backup_task = asyncio.create_task(auto_backup_loop())
    security_task = asyncio.create_task(security_monitor_loop())
    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
         logger.error(f"توقف البوت بشكل غير متوقع: {e}")
    finally:
         logger.info("جاري إغلاق جميع المهام الخلفية...")
         backup_task.cancel()
         security_task.cancel()
         
         pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
         for task in pending:
             task.cancel()
         
         if pending:
             logger.info(f"جاري انتظار إغلاق {len(pending)} مهمة معلقة...")
             await asyncio.gather(*pending, return_exceptions=True)
             
         await bot.session.close()
         logger.info("تم إيقاف البوت بنجاح.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass

