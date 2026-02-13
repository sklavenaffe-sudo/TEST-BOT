import asyncio
import logging
import os
import uuid

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.filters.content_type import ContentType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

import requests
from fastapi import FastAPI, Request

# Ключи прямо в коде для тестов
TELEGRAM_TOKEN = "8224405732:AAG36lqqApmEmrAMGm4ikhu4fIG5Zvm-pRs"
CLOTHOFF_API_KEY = "b8f2922a81aac1bab2f7c1d28b2f6d5be9705f73"
APP_URL = "https://test-bot-production-8a33.up.railway.app"  # Твой Railway URL

# Логи
logging.basicConfig(level=logging.INFO)

# FastAPI app
app = FastAPI()

# Aiogram
bot = Bot(token=TELEGRAM_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Состояния
class Form(StatesGroup):
    waiting_photo = State()

# Временное хранилище для pending задач (id: chat_id)
pending_tasks = {}

# Startup: установка webhook для Telegram
async def on_startup():
    webhook_url = f"{APP_URL}/telegram_webhook"  # Эндпоинт для Telegram updates
    await bot.set_webhook(webhook_url)
    logging.info(f"Webhook установлен: {webhook_url}")

dp.startup.register(on_startup)

# /start
@dp.message(CommandStart())
async def start_command(message: types.Message, state: FSMContext):
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Раздеть", callback_data="undress")]
    ])
    await message.answer("Привет! Нажми кнопку для раздевания.", reply_markup=keyboard)

# Inline кнопка
@dp.callback_query(lambda c: c.data == "undress")
async def handle_undress(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_photo)
    await callback.message.answer("Отправь фото для обработки.")
    await callback.answer()

# Обработка фото
@dp.message(Form.waiting_photo, content_types=ContentType.PHOTO)
async def process_photo(message: types.Message, state: FSMContext):
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    image_bytes = await bot.download_file(file_info.file_path)

    task_id = str(uuid.uuid4())
    pending_tasks[task_id] = message.chat.id

    # Отправка на Clothoff API
    url = "https://public-api.clothoff.net/undress"
    files = {'cloth': ('image.jpg', image_bytes, 'image/jpeg')}
    data = {
        'id': task_id,
        'webhook': f"{APP_URL}/clothoff_webhook",
        'cloth_type': 'naked'  # По умолчанию naked
    }
    headers = {
        'x-api-key': CLOTHOFF_API_KEY,
        'accept': 'application/json'
    }

    try:
        response = requests.post(url, files=files, data=data, headers=headers)
        if response.status_code == 200:
            await message.answer("Фото в обработке... Жди результат (5-10 сек).")
        else:
            await message.answer(f"Ошибка: {response.text}")
            del pending_tasks[task_id]
    except Exception as e:
        await message.answer(f"Ошибка отправки: {str(e)}")
        del pending_tasks[task_id]

    await state.clear()

# Эндпоинт для Clothoff webhook
@app.post("/clothoff_webhook")
async def clothoff_webhook_handler(request: Request):
    data = await request.json()
    task_id = data.get('id')
    if task_id in pending_tasks:
        chat_id = pending_tasks.pop(task_id)
        result = data.get('result', {})  # Предполагаем структуру {'image': 'url'}
        image_url = result.get('image')  # Или base64, если так
        if image_url:
            await bot.send_photo(chat_id=chat_id, photo=image_url)
        else:
            await bot.send_message(chat_id=chat_id, text="Ошибка в результате.")
    return {"status": "ok"}

# Эндпоинт для Telegram updates
@app.post("/telegram_webhook")
async def telegram_webhook_handler(request: Request):
    update = await request.json()
    telegram_update = types.Update(**update)
    await dp.feed_update(bot=bot, update=telegram_update)
    return {"status": "ok"}

# Запуск
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
