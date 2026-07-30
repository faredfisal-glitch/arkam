import os
from telethon.tl.functions.account import UpdateProfileRequest, UpdateUsernameRequest
from telethon.tl.functions.photos import UploadProfilePhotoRequest, DeletePhotosRequest, GetUserPhotosRequest
from telethon.tl.functions.stories import SendStoryRequest
from telethon.tl.types import InputMediaUploadedPhoto
from DivoSource.tasks import execute_for_phone

async def _action_update_first_name(client, phone, first_name):
    try:
        await client(UpdateProfileRequest(first_name=first_name))
        return "✅ تم تعديل الاسم الأول بنجاح"
    except Exception as e:
        return f"❌ فشل التعديل: {str(e)}"

async def edit_first_name_task(phone: str, first_name: str):
    return await execute_for_phone(phone, _action_update_first_name, first_name)

async def _action_update_last_name(client, phone, last_name):
    try:
        await client(UpdateProfileRequest(last_name=last_name))
        return "✅ تم تعديل الاسم الثاني بنجاح"
    except Exception as e:
        return f"❌ فشل التعديل: {str(e)}"

async def edit_last_name_task(phone: str, last_name: str):
    return await execute_for_phone(phone, _action_update_last_name, last_name)

async def _action_update_username(client, phone, username):
    try:
        await client(UpdateUsernameRequest(username=username))
        return "✅ تم تعديل اليوزر بنجاح"
    except Exception as e:
        return f"❌ فشل التعديل: {str(e)}"

async def edit_username_task(phone: str, username: str):
    return await execute_for_phone(phone, _action_update_username, username)

async def _action_delete_username(client, phone):
    try:
        await client(UpdateUsernameRequest(username=""))
        return "✅ تم حذف اليوزر بنجاح"
    except Exception as e:
        return f"❌ فشل الحذف: {str(e)[:30]}"

async def delete_username_task(phone: str):
    return await execute_for_phone(phone, _action_delete_username)

async def _action_update_bio(client, phone, bio):
    try:
        await client(UpdateProfileRequest(about=bio))
        return "✅ تم تعديل البايو بنجاح"
    except Exception as e:
        return f"❌ فشل التعديل: {str(e)}"

async def edit_bio_task(phone: str, bio: str):
    return await execute_for_phone(phone, _action_update_bio, bio)

async def _action_delete_bio(client, phone):
    try:
        await client(UpdateProfileRequest(about=""))
        return "✅ تم حذف البايو بنجاح"
    except Exception as e:
        return f"❌ فشل الحذف: {str(e)[:30]}"

async def delete_bio_task(phone: str):
    return await execute_for_phone(phone, _action_delete_bio)

async def _action_add_photo(client, phone, photo_path):
    try:
        await client(UploadProfilePhotoRequest(file=await client.upload_file(photo_path)))
        return "✅ تم رفع الصورة بنجاح"
    except Exception as e:
        return f"❌ فشل رفع الصورة: {str(e)[:30]}"
    finally:
        if os.path.exists(photo_path):
            try:
                os.remove(photo_path)
            except Exception:
                pass

async def add_photo_task(phone: str, photo_path: str):
    return await execute_for_phone(phone, _action_add_photo, photo_path)

async def _action_delete_photo(client, phone):
    try:
        photos = await client(GetUserPhotosRequest(user_id='me', offset=0, max_id=0, limit=100))
        if photos.photos:
            await client(DeletePhotosRequest(id=photos.photos))
            return "✅ تم حذف صور الحساب بنجاح"
        return "⚠️ لا توجد صور للحذف"
    except Exception as e:
        return f"❌ فشل الحذف: {str(e)[:30]}"

async def delete_photo_task(phone: str):
    return await execute_for_phone(phone, _action_delete_photo)

async def _action_add_story(client, phone, media_path):
    try:
        # Telethon doesn't easily support stories using client.send_story for older versions,
        # but SendStoryRequest exists in newer versions although tricky without a specific proper setup.
        # We'll try the direct SendStoryRequest or an alternative. Pyrogram is not in use here for execution (mostly telethon string gen).
        # WAIT: Does Pyrogram support stories in Client.send_story?
        # Pyrogram actually isn't easily supported. We can try client.send_file("me", ...) if stories fail? No, user explicitly wants story.
        # We will try SendStoryRequest if it fails catch and return error.
        me = await client.get_me()
        uploaded = await client.upload_file(media_path)
        media = InputMediaUploadedPhoto(file=uploaded)
        # Using simple method if available via telethon's recent support? No, SendStoryRequest is complex. Let's use it minimally or just error natively if unsupported.
        # It's better to just use raw request:
        try:
            from telethon.tl.functions.stories import SendStoryRequest
            from telethon.tl.types import InputMediaUploadedDocument, InputPeerSelf
            # Using basic file send if it's photo to peer me but for story:
            try:
                # Need to use telethon v1.33+
                await client(SendStoryRequest(peer=InputPeerSelf(), media=media, privacy_rules=[]))
            except Exception as e:
                # Some issues with privacy rules, try default
                from telethon.tl.types import InputPrivacyValueAllowAll
                await client(SendStoryRequest(peer=InputPeerSelf(), media=media, privacy_rules=[InputPrivacyValueAllowAll()]))
        except ImportError:
            return "❌ مكتبة Telethon الحالية لا تدعم القصص (Stories)"
        return "✅ تم نشر الستوري بنجاح"
    except Exception as e:
        return f"❌ فشل النشر: {str(e)[:40]}"
    finally:
        if os.path.exists(media_path):
            try:
                os.remove(media_path)
            except Exception:
                pass


async def add_story_task(phone: str, media_path: str):
    return await execute_for_phone(phone, _action_add_story, media_path)
