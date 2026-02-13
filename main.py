import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import time
import os
from flask import Flask, request, jsonify
import threading
import io  # Для обработки байтов как файла

# API ключи и URL'ы прямо в коде (для тестов, риски на тебе)
TG_TOKEN = '8224405732:AAG36lqqApmEmrAMGm4ikhu4fIG5Zvm-pRs'
API_KEY = 'b8f2922a81aac1bab2f7c1d28b2f6d5be9705f73'
API_BASE_URL = 'https://api.grtkniv.net/api'
UNDRESS_ENDPOINT = f'{API_BASE_URL}/imageGenerations/undress'  # Предполагаемый; если другой - подправь
STATUS_ENDPOINT = f'{API_BASE_URL}/imageGenerations/status/{{task_id}}'  # Для polling
RAILWAY_DOMAIN = 'https://test-bot-production-8a33.up.railway.app'  # Твой публичный домен

bot = telebot.TeleBot(TG_TOKEN)

# Словарь для хранения состояний пользователей
user_states = {}  # {user_id: {'waiting': bool, 'step': str, 'chat_id': int, 'waiting_msg_id': int, 'task_id': str}}
photo_files = {}  # {user_id: bytes} временно для API
webhook_results = {}  # {task_id: bytes} для результатов от API-webhook

# Flask app для webhook'ов (TG и API)
app = Flask(__name__)

@app.route('/tg_webhook', methods=['POST'])
def telegram_webhook():
    """Webhook для Telegram updates"""
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return ''
    else:
        return 'Forbidden', 403

@app.route('/api_webhook', methods=['POST'])
def api_webhook():
    """Webhook от твоего API для async результатов"""
    try:
        data = request.json
        task_id = data.get('task_id')
        status = data.get('status')
        if status == 'completed':
            result_url = data.get('result_url')  # Или data.get('result_image') если bytes
            if result_url:
                resp = requests.get(result_url)
                if resp.status_code == 200:
                    webhook_results[task_id] = resp.content
                    # Здесь можно уведомить бота, но используем polling как fallback
            return jsonify({'status': 'received'})
        elif status == 'failed':
            webhook_results[task_id] = None  # Флаг ошибки
            return jsonify({'status': 'received'})
    except Exception as e:
        print(f"Webhook error: {e}")
    return jsonify({'status': 'ok'})

def run_flask():
    """Запуск Flask сервера на Railway порту"""
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)

# TG Bot handlers
@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("Раздеть", callback_data='undress_start'))
    bot.send_message(chat_id, "Привет! Я бот для раздевания по фото. Нажми кнопку ниже.", reply_markup=markup)
    user_states[user_id] = {'waiting': False, 'step': 'idle', 'chat_id': chat_id}

@bot.callback_query_handler(func=lambda call: call.data == 'undress_start')
def undress_start(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id)
    bot.send_message(chat_id, "Скинь фото (JPEG/PNG) для обработки. Убедись, что на фото человек в полный рост.")
    user_states[user_id] = {'waiting': True, 'step': 'waiting_photo', 'chat_id': chat_id}

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    user_id = message.from_user.id
    state = user_states.get(user_id, {})
    if state.get('waiting') and state.get('step') == 'waiting_photo':
        chat_id = state['chat_id']
        waiting_msg = bot.send_message(chat_id, "🔄 Обрабатываю фото... Это может занять 1-2 минуты.")
        user_states[user_id]['waiting_msg_id'] = waiting_msg.message_id
        
        # Скачай фото (берём самое большое)
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        photo_files[user_id] = downloaded_file  # Байты фото
        
        # Отправь на API
        task_id = process_undress_api(user_id)
        if task_id:
            user_states[user_id]['task_id'] = task_id
            # Поллинг статуса в фоне (fallback к webhook)
            threading.Thread(target=poll_status, args=(user_id,)).start()
        else:
            bot.edit_message_text("❌ Ошибка отправки на API. Попробуй заново.", chat_id, waiting_msg.message_id)
            cleanup_user(user_id)
    else:
        bot.send_message(message.chat.id, "Сначала нажми /start и кнопку 'Раздеть'.")

def process_undress_api(user_id):
    """Отправка фото на API undress"""
    photo_bytes = photo_files[user_id]
    files = {'image': ('input.jpg', photo_bytes, 'image/jpeg')}
    headers = {'Authorization': f'Bearer {API_KEY}'}
    
    try:
        response = requests.post(UNDRESS_ENDPOINT, files=files, headers=headers, timeout=30)
        print(f"API Response: {response.status_code} - {response.text}")  # Лог для дебага
        if response.status_code == 200:
            data = response.json()
            task_id = data.get('task_id')  # Async
            if task_id:
                return task_id
            else:
                # Если sync, верни результат напрямую (предполагаем url или base64, но bytes)
                result_url = data.get('result_url')
                if result_url:
                    resp = requests.get(result_url)
                    if resp.status_code == 200:
                        send_result(user_id, resp.content)
                        return None
                return None
        else:
            print(f"API Error: {response.text}")
            return None
    except Exception as e:
        print(f"API Request Error: {e}")
        return None

def poll_status(user_id):
    """Polling статуса задачи (каждые 5 сек, до 2 мин)"""
    state = user_states[user_id]
    task_id = state['task_id']
    chat_id = state['chat_id']
    waiting_msg_id = state['waiting_msg_id']
    max_attempts = 24  # 2 мин
    for attempt in range(max_attempts):
        time.sleep(5)
        headers = {'Authorization': f'Bearer {API_KEY}'}
        status_resp = requests.get(STATUS_ENDPOINT.format(task_id=task_id), headers=headers, timeout=10)
        if status_resp.status_code == 200:
            data = status_resp.json()
            status = data.get('status')
            if status == 'completed':
                # Проверь webhook_results сначала (если webhook сработал)
                result_bytes = webhook_results.get(task_id)
                if not result_bytes:
                    result_url = data.get('result_url')
                    if result_url:
                        resp = requests.get(result_url)
                        result_bytes = resp.content if resp.status_code == 200 else None
                if result_bytes:
                    send_result(user_id, result_bytes)
                else:
                    bot.edit_message_text("❌ Ошибка получения результата.", chat_id, waiting_msg_id)
                cleanup_user(user_id)
                return
            elif status == 'failed' or status == 'error':
                bot.edit_message_text("❌ Ошибка обработки на сервере.", chat_id, waiting_msg_id)
                cleanup_user(user_id)
                return
        # Лог прогресса
        if attempt % 6 == 0:  # Каждые 30 сек
            bot.edit_message_text(f"🔄 Обрабатываю... ({attempt*5 // 60} мин)", chat_id, waiting_msg_id)
    # Таймаут
    bot.edit_message_text("⏰ Таймаут обработки. Попробуй заново.", chat_id, waiting_msg_id)
    cleanup_user(user_id)

def send_result(user_id, result_bytes):
    """Отправка результата в чат"""
    state = user_states[user_id]
    chat_id = state['chat_id']
    waiting_msg_id = state['waiting_msg_id']
    bot.edit_message_text("✅ Готово! 😏", chat_id, waiting_msg_id)
    bot.send_photo(chat_id, result_bytes, caption="Вот результат раздевания по фото.")

def cleanup_user(user_id):
    """Очистка состояния пользователя"""
    if user_id in user_states:
        del user_states[user_id]
    if user_id in photo_files:
        del photo_files[user_id]
    # Очистка webhook_results не трогаем, или по таймауту

# Установка webhook для TG (выполни один раз при деплое или вручную)
def setup_webhook():
    webhook_url = f'{RAILWAY_DOMAIN}/tg_webhook'
    bot.remove_webhook()
    bot.set_webhook(url=webhook_url)
    print(f"Webhook установлен: {webhook_url}")

if __name__ == '__main__':
    # Для Railway: webhook предпочтительнее polling
    setup_webhook()  # Установит webhook автоматически при запуске (если нужно - закомментируй и сделай вручную)
    
    # Запуск Flask в фоне
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    print("Сервер запущен. TG webhook: https://test-bot-production-8a33.up.railway.app/tg_webhook")
    print("API webhook: https://test-bot-production-8a33.up.railway.app/api_webhook (укажи в настройках твоего API)")
    print("Для теста: /start в TG боте")
    
    # Если webhook не сработает, fallback на polling (но для Railway - webhook must)
    # bot.polling(none_stop=True)  # Раскомментируй для локального теста
    # Вместо этого сервер просто висит (Flask обрабатывает)
    flask_thread.join()  # Держим процесс живым
