import asyncio
import logging
import os
import uuid
from typing import Dict

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, CallbackQuery

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Из Variables ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CLOTHOFF_TOKEN = os.getenv("CLOTHOFF_TOKEN")

if not TELEGRAM_TOKEN or not CLOTHOFF_TOKEN:
    raise ValueError("Токены не заданы!")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

BASE_URL = "https://test-bot-production-8a33.up.railway.app"  # твой домен
WEBHOOK_PATH = "/clothoff-webhook"
WEBHOOK_URL = f"{BASE_URL}{WEBHOOK_PATH}"

pending_requests: Dict[str, int] = {}  # gen_id → chat_id

def get_undress_button():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Раздеть", callback_data="undress")]
    ])

@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "Привет! 👋\nЯ раздеваю фото с помощью Clothoff AI.\nНажми кнопку и отправь фото в одежде 🔥",
        reply_markup=get_undress_button()
    )

@dp.callback_query(lambda c: c.data == "undress")
async def undress_button_handler(callback: CallbackQuery):
    await callback.message.edit_text("Кидай фото — сейчас раздену 😉 (5–15 сек)")
    await callback.answer()

@dp.message(lambda m: m.photo)
async def photo_handler(message: Message):
    if message.chat.id in pending_requests.values():
        await message.answer("Подожди завершения предыдущей обработки...")
        return

    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    
    temp_path = f"temp_{uuid.uuid4()}.jpg"
    await bot.download_file(file.file_path, temp_path)
    
    gen_id = str(uuid.uuid4())
    pending_requests[gen_id] = message.chat.id
    
    async with aiohttp.ClientSession() as session:
        form = aiohttp.FormData()
        form.add_field("photo", open(temp_path, "rb"), filename="input.jpg")
        form.add_field("cloth", "naked")  # или "nude" — протестируй, если не сработает
        form.add_field("webhook_url", WEBHOOK_URL)
        form.add_field("unique_id", gen_id)
        
        headers = {"Authorization": f"Bearer {CLOTHOFF_TOKEN}"}
        
        try:
            async with session.post("https://public-api.clothoff.net/undress", data=form, headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    await message.answer(f"Clothoff ошибка: {resp.status} — {text[:200]}")
                    del pending_requests[gen_id]
                    os.remove(temp_path)
                    return
                
                data = await resp.json()
                logger.info(f"Clothoff ответ: {data}")
                await message.answer("Обрабатываю... жди 5–15 сек ⏳")
        except Exception as e:
            logger.error(e)
            await message.answer("Ошибка отправки в API.")
            if gen_id in pending_requests:
                del pending_requests[gen_id]
    
    os.remove(temp_path)

# Запуск polling (основной процесс)
async def main():
    logger.info("Бот запускается в режиме polling...")
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
