import asyncio
import logging
import os
import uuid

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, CallbackQuery

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CLOTHOFF_TOKEN = os.getenv("CLOTHOFF_TOKEN")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

pending = {}  # gen_id → chat_id

@dp.message(CommandStart())
async def start(m: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Раздеть", callback_data="undress")]
    ])
    await m.answer("Привет! Нажми кнопку и отправь фото", reply_markup=kb)

@dp.callback_query(lambda c: c.data == "undress")
async def btn(c: CallbackQuery):
    await c.message.edit_text("Кидай фото")
    await c.answer()

@dp.message(lambda m: m.photo)
async def photo(m: Message):
    photo = m.photo[-1]
    file = await bot.get_file(photo.file_id)
    temp = f"tmp_{uuid.uuid4()}.jpg"
    await bot.download_file(file.file_path, temp)

    gen_id = str(uuid.uuid4())
    pending[gen_id] = m.chat.id

    form = aiohttp.FormData()
    form.add_field("photo", open(temp, "rb"), filename="photo.jpg")
    form.add_field("cloth", "naked")
    form.add_field("webhook_url", "https://test-bot-production-8a33.up.railway.app/webhook")  # пока заглушка
    form.add_field("unique_id", gen_id)

    headers = {"Authorization": f"Bearer {CLOTHOFF_TOKEN}"}

    async with aiohttp.ClientSession() as s:
        async with s.post("https://public-api.clothoff.net/undress", data=form, headers=headers) as r:
            if r.status != 200:
                await m.answer("Ошибка API")
            else:
                await m.answer("Обрабатываю...")

    os.remove(temp)

async def main():
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
