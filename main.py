import asyncio
import logging
import os
import uuid
from typing import Dict

import aiohttp
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, CallbackQuery
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Из Railway Variables (добавь их в дашборд!) ===
TELEGRAM_TOKEN = os.getenv("8224405732:AAG36lqqApmEmrAMGm4ikhu4fIG5Zvm-pRs")
CLOTHOFF_TOKEN = os.getenv("b8f2922a81aac1bab2f7c1d28b2f6d5be9705f73")  # только строка без "Bearer "

BASE_URL = "https://test-bot-production-8a33.up.railway.app"  # ← твой домен!
WEBHOOK_PATH = "/clothoff-webhook"
WEBHOOK_URL = f"{BASE_URL}{WEBHOOK_PATH}"

if not TELEGRAM_TOKEN or not CLOTHOFF_TOKEN:
    raise ValueError("TELEGRAM_TOKEN или CLOTHOFF_TOKEN не заданы в переменных окружения!")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
router = Router()
app = FastAPI()

# Хранилище: gen_id → chat_id
pending_requests: Dict[str, int] = {}

def get_undress_button():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Раздеть", callback_data="undress")]
    ])

@router.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "Привет! 👋\nЯ бот на базе Clothoff API — раздеваю фото по твоему запросу.\n"
        "Нажми кнопку ниже, отправь фото (лучше в одежде, четкое) и подожди результат 🔥",
        reply_markup=get_undress_button()
    )

@router.callback_query(lambda c: c.data == "undress")
async def undress_button_handler(callback: CallbackQuery):
    await callback.message.edit_text("Кидай фото — сейчас раздену 😉\n(обработка ~5–15 сек)")
    await callback.answer()

@router.message(lambda m: m.photo)
async def photo_handler(message: Message):
    if message.chat.id in pending_requests.values():
        await message.answer("Подожди, предыдущее фото ещё обрабатывается...")
        return

    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    
    temp_path = f"temp_{uuid.uuid4()}.jpg"
    await bot.download_file(file.file_path, temp_path)
    
    gen_id = str(uuid.uuid4())
    pending_requests[gen_id] = message.chat.id
    
    async with aiohttp.ClientSession() as session:
        form = aiohttp.FormData()
        form.add_field("photo", open(temp_path, "rb"), filename="input.jpg", content_type="image/jpeg")
        form.add_field("cloth", "naked")  # Ключевой параметр для полного раздевания
        form.add_field("webhook_url", WEBHOOK_URL)
        form.add_field("unique_id", gen_id)  # Чтобы точно сопоставить
        
        headers = {
            "Authorization": f"Bearer {CLOTHOFF_TOKEN}",
            "Accept": "application/json"
        }
        
        try:
            async with session.post("https://public-api.clothoff.net/undress", data=form, headers=headers) as resp:
                text = await resp.text()
                if resp.status != 200:
                    await message.answer(f"Ошибка API: {resp.status} — {text[:200]}")
                    if gen_id in pending_requests:
                        del pending_requests[gen_id]
                    os.remove(temp_path)
                    return
                
                data = await resp.json()
                logger.info(f"Clothoff init response: {data}")
                await message.answer("Фото в обработке... Ожидай 5–15 секунд ⏳ (иногда дольше)")
        except Exception as e:
            logger.error(f"Request error: {e}")
            await message.answer("Ошибка соединения с API. Попробуй позже.")
            if gen_id in pending_requests:
                del pending_requests[gen_id]
    
    if os.path.exists(temp_path):
        os.remove(temp_path)

@app.post(WEBHOOK_PATH)
async def clothoff_webhook(request: Request):
    try:
        data = await request.json()
        logger.info(f"Webhook от Clothoff: {data}")
        
        gen_id = data.get("unique_id") or data.get("id_gen")
        result_url = data.get("result_url") or data.get("url") or data.get("image_url") or data.get("generated_image")
        status = data.get("status")
        
        if status and status.lower() != "completed":
            logger.warning(f"Non-completed status: {status}")
        
        chat_id = pending_requests.pop(gen_id, None) if gen_id else None
        
        if chat_id and result_url:
            async with aiohttp.ClientSession() as session:
                async with session.get(result_url) as r:
                    if r.status == 200:
                        result_path = f"result_{gen_id[:8]}.jpg"
                        with open(result_path, "wb") as f:
                            f.write(await r.read())
                        
                        await bot.send_photo(
                            chat_id,
                            FSInputFile(result_path),
                            caption="Готово! 🔥\n\nХочешь ещё? Нажми 'Раздеть' снова."
                        )
                        await bot.send_message(chat_id, "Если нужно — кидай новое фото 😉")
                        os.remove(result_path)
                    else:
                        await bot.send_message(chat_id, "Не удалось скачать результат (ошибка скачивания). Попробуй заново.")
        else:
            logger.warning("Webhook без нужных полей или chat_id не найден")
        
        return JSONResponse(status_code=200, content={"status": "received"})
    except Exception as e:
        logger.error(f"Webhook crash: {e}")
        return JSONResponse(status_code=500, content={"status": "error"})

@app.get("/")
async def root():
    return {"status": "online", "message": "Clothoff Undress Bot MVP работает 🚀"}

# Для Railway — uvicorn запускает app, aiogram polling в фоне не нужен, т.к. webhook от TG не используем (polling)
# Но если хочешь polling — добавь asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

