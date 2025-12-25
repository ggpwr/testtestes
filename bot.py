# -*- coding: utf-8 -*-
import telebot
from telebot import types
import configparser
import random
import time
import sys
import os
from datetime import datetime
import pytz
import json

# Настройка кодировки
sys.stdout.reconfigure(encoding='utf-8')

# Загрузка конфигурации
config = configparser.ConfigParser()
config.read('config.ini', encoding='utf-8')
BOT_TOKEN = config.get('BotConfig', 'BOT_TOKEN', fallback='')
operators = config.get('BotConfig', 'operators', fallback='')
operators = [int(x) for x in operators.split(',') if x.strip()]
WAIT_TIME = int(config.get('BotConfig', 'time_wait_for_send_message', fallback=60))
ADMIN_ID = int(config.get('BotConfig', 'admin_id', fallback='0'))
CONFIG_FILE = 'config.ini'
DATA_FILE = 'bot_data.json'

# Инициализация бота
bot = telebot.TeleBot(BOT_TOKEN)

# Хранилище данных
users = {}  # user_id: {'captcha': bool, 'last_msg': time, 'username': str}
waiting_answers = {}  # operator_id: {'user_id': int, 'waiting': bool}
messages_queue = []  # [{'user_id': int, 'text': str, 'type': str, 'time': float}]
user_messages = {}  # user_id: [{'text': str, 'time': float, 'answered': bool}]
operator_stats = {}  # operator_id: {'answered': int, 'response_time': float}
answer_templates = {}  # Шаблоны ответов
system_settings = {  # Настройки системы
    'auto_greet': True,
    'notify_operators': True,
    'max_queue_size': 100,
    'captcha_enabled': True,
    'work_hours_start': 9,
    'work_hours_end': 21,
    'work_hours_enabled': False
}

# =============================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =============================

def is_admin(user_id):
    """Проверить, является ли пользователь администратором"""
    return user_id == ADMIN_ID

def load_data():
    """Загрузить данные из файла"""
    global users, user_messages, operator_stats, answer_templates, system_settings
    
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                users = data.get('users', {})
                user_messages = data.get('user_messages', {})
                operator_stats = data.get('operator_stats', {})
                answer_templates = data.get('answer_templates', {})
                # Обновляем настройки системы, сохраняя значения по умолчанию для отсутствующих ключей
                loaded_settings = data.get('system_settings', {})
                for key in system_settings:
                    if key in loaded_settings:
                        system_settings[key] = loaded_settings[key]
                print(f"✅ Данные загружены: {len(users)} пользователей")
    except Exception as e:
        print(f"❌ Ошибка загрузки данных: {e}")

def save_data():
    """Сохранить данные в файл"""
    try:
        data = {
            'users': users,
            'user_messages': user_messages,
            'operator_stats': operator_stats,
            'answer_templates': answer_templates,
            'system_settings': system_settings
        }
        
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"❌ Ошибка сохранения данных: {e}")
        return False

def save_config():
    """Сохранить конфигурацию"""
    try:
        config['BotConfig'] = {
            'BOT_TOKEN': BOT_TOKEN,
            'operators': ','.join(map(str, operators)),
            'time_wait_for_send_message': str(WAIT_TIME),
            'admin_id': str(ADMIN_ID)
        }
        
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            config.write(f)
        return True
    except Exception as e:
        print(f"❌ Ошибка сохранения конфига: {e}")
        return False

def get_moscow_time():
    """Получить московское время"""
    tz = pytz.timezone('Europe/Moscow')
    return datetime.now(tz).strftime('%H:%M %d.%m.%Y')

def format_user_info(user_id, username="", first_name=""):
    """Форматировать информацию о пользователе"""
    info = f"🆔 ID: {user_id}"
    if first_name:
        info += f"\n👤 Имя: {first_name}"
    if username:
        info += f"\n📱 @{username}"
    info += f"\n🕒 Время: {get_moscow_time()}"
    return info

def save_message_to_queue(user_id, text, msg_type="text"):
    """Сохранить сообщение в очередь"""
    # Проверка на максимальный размер очереди
    if len(messages_queue) >= system_settings['max_queue_size']:
        # Удаляем самое старое сообщение
        if messages_queue:
            messages_queue.pop(0)
    
    messages_queue.append({
        'user_id': user_id,
        'text': text,
        'type': msg_type,
        'time': time.time()
    })
    
    # Сохраняем в историю пользователя
    if user_id not in user_messages:
        user_messages[user_id] = []
    user_messages[user_id].append({
        'text': text,
        'time': time.time(),
        'answered': False
    })

def get_next_message_for_operator(operator_id):
    """Получить следующее сообщение для оператора"""
    if not messages_queue:
        return None
    
    # Получаем самое старое необработанное сообщение
    for msg in messages_queue[:20]:  # Берем только последние 20
        # Проверяем, не отвечает ли уже другой оператор
        user_id = msg['user_id']
        if not any(op.get('user_id') == user_id for op in waiting_answers.values()):
            waiting_answers[operator_id] = {
                'user_id': user_id,
                'waiting': True,
                'message': msg
            }
            return msg
    return None

def get_user_unanswered_count(user_id):
    """Получить количество неотвеченных сообщений пользователя"""
    if user_id not in user_messages:
        return 0
    return sum(1 for msg in user_messages[user_id] if not msg['answered'])

def is_work_time():
    """Проверить рабочее время"""
    if not system_settings.get('work_hours_enabled', False):
        return True
    
    try:
        now = datetime.now(pytz.timezone('Europe/Moscow'))
        current_hour = now.hour
        start = system_settings.get('work_hours_start', 9)
        end = system_settings.get('work_hours_end', 21)
        
        return start <= current_hour < end
    except:
        return True

# =============================
# КЛАВИАТУРЫ
# =============================

def main_menu():
    """Главное меню"""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("✉️ Написать оператору"),
        types.KeyboardButton("📋 Инструкция"),
        types.KeyboardButton("📊 Статистика")
    )
    kb.add(
        types.KeyboardButton("📞 Контакты")
    )
    return kb

def operator_menu():
    """Меню оператора"""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("📬 Взять сообщение"),
        types.KeyboardButton("💬 Ответить"),
        types.KeyboardButton("📊 Статистика"),
        types.KeyboardButton("🎯 Инфопанель")
    )
    kb.add(
        types.KeyboardButton("⚙️ Управление"),
        types.KeyboardButton("🔄 Сбросить ответ"),
        types.KeyboardButton("💾 Сохранить данные")
    )
    return kb

def back_button():
    """Кнопка Назад"""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("🔙 Назад"))
    return kb

def answer_buttons(user_id):
    """Кнопки для ответа оператора"""
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📝 Ответить", callback_data=f"reply_{user_id}"),
        types.InlineKeyboardButton("✅ Решено", callback_data=f"solve_{user_id}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{user_id}"),
        types.InlineKeyboardButton("📋 История", callback_data=f"history_{user_id}")
    )
    return kb

def settings_menu():
    """Меню настроек"""
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("👥 Операторы", callback_data="menu_operators"),
        types.InlineKeyboardButton("⚙️ Система", callback_data="menu_system"),
        types.InlineKeyboardButton("📝 Шаблоны", callback_data="menu_templates"),
        types.InlineKeyboardButton("🕒 Время работы", callback_data="menu_worktime"),
        types.InlineKeyboardButton("🧹 Очистка", callback_data="menu_cleanup"),
        types.InlineKeyboardButton("💾 Экспорт", callback_data="menu_export")
    )
    return kb

def operators_menu():
    """Меню управления операторами"""
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("➕ Добавить оператора", callback_data="add_operator"),
        types.InlineKeyboardButton("➖ Удалить оператора", callback_data="remove_operator"),
        types.InlineKeyboardButton("📋 Список операторов", callback_data="list_operators"),
        types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_settings")
    )
    return kb

def system_menu():
    """Меню настроек системы"""
    auto_greet = "✅" if system_settings['auto_greet'] else "❌"
    notify = "✅" if system_settings['notify_operators'] else "❌"
    captcha = "✅" if system_settings['captcha_enabled'] else "❌"
    
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton(f"{auto_greet} Автоприветствие", callback_data="toggle_greet"),
        types.InlineKeyboardButton(f"{notify} Уведомления", callback_data="toggle_notify"),
        types.InlineKeyboardButton(f"{captcha} Капча", callback_data="toggle_captcha"),
        types.InlineKeyboardButton("📏 Лимит очереди", callback_data="set_queue_limit"),
        types.InlineKeyboardButton("⏱️ Таймаут", callback_data="set_timeout"),
        types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_settings")
    )
    return kb

def templates_menu():
    """Меню шаблонов"""
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📋 Список шаблонов", callback_data="list_templates"),
        types.InlineKeyboardButton("➕ Добавить шаблон", callback_data="add_template"),
        types.InlineKeyboardButton("✏️ Редактировать", callback_data="edit_template"),
        types.InlineKeyboardButton("🗑️ Удалить шаблон", callback_data="delete_template"),
        types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_settings")
    )
    return kb

def worktime_menu():
    """Меню времени работы"""
    enabled = "✅" if system_settings.get('work_hours_enabled', False) else "❌"
    start = system_settings.get('work_hours_start', 9)
    end = system_settings.get('work_hours_end', 21)
    
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton(f"{enabled} Режим работы", callback_data="toggle_worktime"),
        types.InlineKeyboardButton(f"🕘 Начало: {start}:00", callback_data="set_work_start"),
        types.InlineKeyboardButton(f"🕘 Конец: {end}:00", callback_data="set_work_end"),
        types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_settings")
    )
    return kb

def cleanup_menu():
    """Меню очистки"""
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🗑️ Очистить очередь", callback_data="clean_queue"),
        types.InlineKeyboardButton("🧹 Очистить историю", callback_data="clean_history"),
        types.InlineKeyboardButton("📊 Сброс статистики", callback_data="reset_stats"),
        types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_settings")
    )
    return kb

# =============================
# ОБРАБОТЧИКИ СООБЩЕНИЙ
# =============================

@bot.message_handler(commands=['start'])
def start_command(message):
    """Обработка команды /start"""
    user_id = message.from_user.id
    
    if user_id in operators:
        # Оператор
        bot.send_message(
            user_id,
            "👮 *Панель оператора*\n\n"
            "📊 В очереди: *{} сообщений*\n"
            "👥 Пользователей: *{} человек*\n\n"
            "Выберите действие:".format(len(messages_queue), len(users)),
            reply_markup=operator_menu(),
            parse_mode="Markdown"
        )
    else:
        # Обычный пользователь
        if user_id not in users:
            users[user_id] = {
                'captcha': False, 
                'last_msg': 0,
                'username': message.from_user.username or "",
                'first_name': message.from_user.first_name or "",
                'messages_sent': 0,
                'joined': time.time()
            }
            send_welcome(message)
        else:
            bot.send_message(
                user_id,
                "👋 С возвращением!\n\n"
                "📊 Ваша статистика:\n"
                "• Сообщений отправлено: *{}*\n"
                "• Неотвеченных: *{}*\n\n"
                "Выберите действие:".format(
                    users[user_id]['messages_sent'],
                    get_user_unanswered_count(user_id)
                ),
                reply_markup=main_menu(),
                parse_mode="Markdown"
            )

def send_welcome(message):
    """Отправить приветственное сообщение"""
    user_id = message.from_user.id
    
    if system_settings['auto_greet']:
        welcome_text = (
            "🎉 *Добро пожаловать в Анонимный Чат!*\n\n"
            "🤖 *Наши возможности:*\n"
            "• Анонимное общение с операторами\n"
            "• Поддержка фото, видео, документов\n"
            "• Быстрые ответы (обычно в течение 15 мин)\n\n"
            "🔐 Для начала решите простой пример:"
        )
        
        bot.send_message(user_id, welcome_text, parse_mode="Markdown")
    
    if system_settings['captcha_enabled']:
        send_captcha(user_id)
    else:
        users[user_id]['captcha'] = True
        bot.send_message(
            user_id,
            "✅ *Регистрация успешна!*\n\n"
            "Теперь вы можете пользоваться всеми функциями бота.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

@bot.message_handler(func=lambda m: True)
def handle_message(message):
    """Обработка всех текстовых сообщений"""
    user_id = message.from_user.id
    text = message.text
    
    # Проверка на оператора
    if user_id in operators:
        handle_operator_message(message)
        return
    
    # Проверка на нового пользователя
    if user_id not in users:
        users[user_id] = {
            'captcha': False, 
            'last_msg': 0,
            'username': message.from_user.username or "",
            'first_name': message.from_user.first_name or "",
            'messages_sent': 0,
            'joined': time.time()
        }
        send_welcome(message)
        return
    
    # Проверка капчи
    if system_settings['captcha_enabled'] and not users[user_id]['captcha']:
        check_captcha(message)
        return
    
    # Проверка рабочего времени
    if not is_work_time():
        bot.send_message(
            user_id,
            "⏰ *Бот временно не работает*\n\n"
            "Рабочее время: с {}:00 до {}:00 (МСК)\n"
            "Пожалуйста, обратитесь позже.".format(
                system_settings.get('work_hours_start', 9),
                system_settings.get('work_hours_end', 21)
            ),
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )
        return
    
    # Обработка кнопок меню
    if text == "✉️ Написать оператору":
        bot.send_message(
            user_id,
            "📝 *Напишите ваше сообщение оператору:*\n\n"
            "💡 *Советы:*\n"
            "• Будьте конкретны в вопросе\n"
            "• Прикрепите фото/видео если нужно\n"
            "• Укажите контакты для обратной связи\n"
            "• Один оператор ответит в течение 15 минут\n\n"
            "⏳ Среднее время ответа: *12 минут*",
            reply_markup=back_button(),
            parse_mode="Markdown"
        )
        users[user_id]['writing'] = True
        
    elif text == "📋 Инструкция":
        show_instruction(user_id)
        
    elif text == "📊 Статистика":
        show_user_stats(user_id)
        
    elif text == "📞 Контакты":
        show_contacts(user_id)
        
    elif text == "🔙 Назад":
        users[user_id].pop('writing', None)
        bot.send_message(
            user_id,
            "🏠 *Главное меню*",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )
        
    elif users[user_id].get('writing'):
        # Пользователь пишет сообщение оператору
        process_user_message(message)
        
    else:
        bot.send_message(
            user_id,
            "Выберите действие в меню 👆",
            reply_markup=main_menu()
        )

# =============================
# ФУНКЦИИ ПОЛЬЗОВАТЕЛЯ
# =============================

def process_user_message(message):
    """Обработка сообщения от пользователя"""
    user_id = message.from_user.id
    text = message.text
    
    # Проверка антифлуда
    current_time = time.time()
    if current_time - users[user_id]['last_msg'] < WAIT_TIME:
        remaining = int(WAIT_TIME - (current_time - users[user_id]['last_msg']))
        bot.send_message(
            user_id,
            f"⏳ Подождите {remaining} секунд перед следующим сообщением",
            reply_markup=back_button()
        )
        return
    
    # Проверка длины текста
    if text and len(text) < 5:
        bot.send_message(
            user_id,
            "📏 Сообщение слишком короткое (минимум 5 символов)",
            reply_markup=back_button()
        )
        return
    
    # Формируем информацию об отправителе
    user_info = format_user_info(user_id, 
                               users[user_id]['username'],
                               users[user_id]['first_name'])
    
    # Сохраняем в очередь
    save_message_to_queue(user_id, text)
    users[user_id]['messages_sent'] += 1
    users[user_id]['last_msg'] = current_time
    
    # Уведомляем операторов если включено
    if system_settings['notify_operators']:
        notify_operators(user_id, text, user_info)
    
    # Подтверждение пользователю
    bot.send_message(
        user_id,
        "✅ *Сообщение отправлено в очередь!*\n\n"
        "📊 Ваша позиция в очереди: *№{}*\n"
        "⏳ Ожидайте ответа оператора\n"
        "💡 Вы можете отправить еще информацию, пока ждете".format(len(messages_queue)),
        reply_markup=back_button(),
        parse_mode="Markdown"
    )
    
    # Автосохранение данных
    save_data()

def notify_operators(user_id, text, user_info):
    """Уведомить операторов о новом сообщении"""
    for operator_id in operators:
        try:
            # Создаем кнопки для быстрого ответа
            kb = answer_buttons(user_id)
            
            # Отправляем сообщение оператору
            bot.send_message(
                operator_id,
                f"📩 *НОВОЕ СООБЩЕНИЕ #{len(messages_queue)}*\n\n"
                f"{user_info}\n\n"
                f"💬 *Сообщение:*\n{text}\n\n"
                f"⏳ В очереди: *{len(messages_queue)}* сообщений",
                parse_mode="Markdown",
                reply_markup=kb
            )
        except Exception as e:
            print(f"Ошибка отправки оператору {operator_id}: {e}")

def show_instruction(user_id):
    """Показать инструкцию"""
    instruction = (
        "📖 *ИНСТРУКЦИЯ ПО ИСПОЛЬЗОВАНИЮ*\n\n"
        "1️⃣ *Как отправить сообщение:*\n"
        "• Нажмите '✉️ Написать оператору'\n"
        "• Введите текст (от 5 символов)\n"
        "• Можно прикрепить фото/видео/документ\n\n"
        "2️⃣ *Время ответа:*\n"
        "• Обычно: 5-15 минут\n"
        "• Пиковое время: до 30 минут\n"
        "• Время работы: {}:00-{}:00 (МСК)\n\n"
        "3️⃣ *Конфиденциальность:*\n"
        "• Ваши данные анонимны\n"
        "• Операторы видят только ваш ID\n"
        "• История сохраняется 30 дней".format(
            system_settings.get('work_hours_start', 9),
            system_settings.get('work_hours_end', 21)
        )
    )
    bot.send_message(user_id, instruction, parse_mode="Markdown", reply_markup=main_menu())

def show_user_stats(user_id):
    """Показать статистику пользователя"""
    user = users.get(user_id, {})
    
    # Вычисляем время в системе
    if 'joined' in user:
        days = int((time.time() - user['joined']) / 86400)
    else:
        days = 0
    
    stats = (
        f"📊 *ВАША СТАТИСТИКА*\n\n"
        f"👤 Аккаунт создан: *{days} дней назад*\n"
        f"✉️ Сообщений отправлено: *{user.get('messages_sent', 0)}*\n"
        f"⏱️ Неотвеченных: *{get_user_unanswered_count(user_id)}*\n"
        f"🏆 Рейтинг активности: *{user.get('messages_sent', 0) // 10} уровень*\n\n"
        f"💡 *Рекорды системы:*\n"
        f"• Самый активный: {max(users.values(), key=lambda x: x.get('messages_sent', 0)).get('messages_sent', 0) if users else 0} сообщений\n"
        f"• Всего пользователей: {len(users)}\n"
        f"• Всего ответов: {sum(1 for msgs in user_messages.values() for msg in msgs if msg.get('answered')) if user_messages else 0}"
    )
    
    bot.send_message(user_id, stats, parse_mode="Markdown", reply_markup=main_menu())

def show_contacts(user_id):
    """Показать контакты"""
    contacts = (
        "📞 *КОНТАКТЫ И ПОДДЕРЖКА*\n\n"
        "👥 *Основные контакты:*\n"
        "• Телеграм канал: @anonymous_channel\n"
        "• Почта: support@anonymous.ru\n"
        "• Сайт: anonymous-chat.ru\n\n"
        "🛠 *Техническая поддержка:*\n"
        "• Для срочных вопросов\n"
        "• По проблемам с ботом\n"
        "• Предложения по улучшению\n\n"
        "⏰ *Время работы поддержки:*\n"
        "• Пн-Пт: {}:00-{}:00 (МСК)\n"
        "• Сб-Вс: {}:00-{}:00 (МСК)".format(
            system_settings.get('work_hours_start', 9),
            system_settings.get('work_hours_end', 21),
            max(10, system_settings.get('work_hours_start', 9)),
            min(18, system_settings.get('work_hours_end', 21))
        )
    )
    bot.send_message(user_id, contacts, parse_mode="Markdown", reply_markup=main_menu())

# =============================
# СИСТЕМА КАПЧИ
# =============================

def send_captcha(user_id):
    """Отправить капчу"""
    # Более сложная капча
    operations = ['+', '-', '*']
    op = random.choice(operations)
    
    if op == '+':
        num1 = random.randint(10, 50)
        num2 = random.randint(10, 50)
        answer = num1 + num2
    elif op == '-':
        num1 = random.randint(50, 100)
        num2 = random.randint(10, 49)
        answer = num1 - num2
    else:  # '*'
        num1 = random.randint(2, 9)
        num2 = random.randint(2, 9)
        answer = num1 * num2
    
    users[user_id]['captcha_answer'] = answer
    users[user_id]['captcha_question'] = f"{num1} {op} {num2}"
    
    bot.send_message(
        user_id,
        f"🔐 *Проверка безопасности*\n\nРешите пример:\n`{num1} {op} {num2} = ?`\n\n"
        "💡 *Подсказка:* Это нужно для защиты от ботов",
        parse_mode="Markdown"
    )

def check_captcha(message):
    """Проверить капчу"""
    user_id = message.from_user.id
    user_text = message.text
    
    try:
        user_answer = int(user_text)
        correct_answer = users[user_id].get('captcha_answer', 0)
        
        if user_answer == correct_answer:
            users[user_id]['captcha'] = True
            
            success_msg = (
                "✅ *Проверка пройдена!*\n\n"
                "Теперь вы можете пользоваться всеми функциями бота."
            )
            
            bot.send_message(
                user_id,
                success_msg,
                reply_markup=main_menu(),
                parse_mode="Markdown"
            )
        else:
            bot.send_message(
                user_id,
                "❌ *Неверный ответ*\n\n"
                f"Попробуйте еще раз: `{users[user_id].get('captcha_question', '?')} = ?`",
                parse_mode="Markdown"
            )
            
    except ValueError:
        bot.send_message(
            user_id,
            "❌ *Введите число*\n\n"
            "Пожалуйста, введите только число (без пробелов и других символов):",
            parse_mode="Markdown"
        )

# =============================
# ОБРАБОТКА МЕДИА
# =============================

@bot.message_handler(content_types=['photo', 'video', 'document', 'voice'])
def handle_media(message):
    """Обработка медиафайлов"""
    user_id = message.from_user.id
    
    # Проверка на оператора
    if user_id in operators:
        handle_operator_media(message)
        return
    
    # Проверка на нового пользователя
    if user_id not in users or not users[user_id]['captcha']:
        bot.send_message(user_id, "Сначала пройдите проверку безопасности")
        return
    
    # Проверка режима написания
    if not users[user_id].get('writing'):
        bot.send_message(user_id, "Нажмите '✉️ Написать оператору' для отправки файлов")
        return
    
    # Проверка антифлуда
    current_time = time.time()
    if current_time - users[user_id]['last_msg'] < WAIT_TIME:
        remaining = int(WAIT_TIME - (current_time - users[user_id]['last_msg']))
        bot.send_message(user_id, f"⏳ Подождите {remaining} секунд")
        return
    
    # Формируем информацию
    user_info = format_user_info(user_id, 
                               users[user_id]['username'],
                               users[user_id]['first_name'])
    
    caption = message.caption or ""
    if caption:
        user_info += f"\n📝 Подпись: {caption}"
    
    # Отправляем операторам если включены уведомления
    if system_settings['notify_operators']:
        for operator_id in operators:
            try:
                if message.photo:
                    file_id = message.photo[-1].file_id
                    text = f"📷 Фото\n\n{user_info}"
                    bot.send_photo(operator_id, file_id, caption=text)
                
                elif message.video:
                    file_id = message.video.file_id
                    text = f"🎬 Видео\n\n{user_info}"
                    bot.send_video(operator_id, file_id, caption=text)
                
                elif message.document:
                    file_id = message.document.file_id
                    text = f"📎 Документ\n\n{user_info}"
                    bot.send_document(operator_id, file_id, caption=text)
                
                elif message.voice:
                    file_id = message.voice.file_id
                    text = f"🎤 Голосовое сообщение\n\n{user_info}"
                    bot.send_voice(operator_id, file_id, caption=text)
                    
            except Exception as e:
                print(f"Ошибка отправки медиа оператору {operator_id}: {e}")
    
    # Сохраняем в историю
    media_type = "фото" if message.photo else "видео" if message.video else "документ" if message.document else "голосовое"
    save_message_to_queue(user_id, f"[{media_type.upper()}] {caption}", media_type)
    users[user_id]['messages_sent'] += 1
    users[user_id]['last_msg'] = current_time
    
    # Подтверждение пользователю
    bot.send_message(
        user_id,
        f"✅ *{media_type.capitalize()} отправлено операторам!*",
        reply_markup=back_button(),
        parse_mode="Markdown"
    )
    
    # Автосохранение
    save_data()

# =============================
# ФУНКЦИИ ОПЕРАТОРА
# =============================

def handle_operator_message(message):
    """Обработка сообщений оператора"""
    user_id = message.from_user.id
    text = message.text
    
    # Проверка админских команд
    if text.startswith('/'):
        handle_admin_command(message)
        return
    
    # Обработка меню оператора
    if text == "📬 Взять сообщение":
        get_next_message(user_id)
        
    elif text == "💬 Ответить":
        if user_id in waiting_answers and waiting_answers[user_id]['waiting']:
            reply_to_user(message)
        else:
            bot.send_message(user_id, "Сначала возьмите сообщение из очереди")
            
    elif text == "📊 Статистика":
        show_operator_stats(user_id)
        
    elif text == "🎯 Инфопанель":
        show_info_panel(user_id)
        
    elif text == "⚙️ Управление":
        if is_admin(user_id):
            bot.send_message(
                user_id,
                "⚙️ *Панель управления системой*",
                parse_mode="Markdown",
                reply_markup=settings_menu()
            )
        else:
            bot.send_message(user_id, "❌ Недостаточно прав")
            
    elif text == "🔄 Сбросить ответ":
        if user_id in waiting_answers:
            waiting_answers.pop(user_id)
            bot.send_message(user_id, "✅ Контекст ответа сброшен")
        else:
            bot.send_message(user_id, "Нет активного контекста для сброса")
            
    elif text == "💾 Сохранить данные":
        if save_data():
            bot.send_message(user_id, "✅ Данные сохранены")
        else:
            bot.send_message(user_id, "❌ Ошибка сохранения")
            
    elif text.startswith("/template"):
        use_template(message)
        
    else:
        # Если оператор в режиме ответа
        if user_id in waiting_answers and waiting_answers[user_id]['waiting']:
            reply_to_user(message)

def handle_admin_command(message):
    """Обработка админских команд"""
    user_id = message.from_user.id
    text = message.text
    
    if text == "/admin":
        if is_admin(user_id):
            bot.send_message(
                user_id,
                "👑 *Панель администратора*\n\n"
                f"🤖 Бот работает: *{datetime.now().strftime('%d.%m.%Y %H:%M')}*\n"
                f"👥 Операторов: *{len(operators)}*\n"
                f"📊 Пользователей: *{len(users)}*\n"
                f"⏳ Очередь: *{len(messages_queue)}*\n\n"
                "⚙️ Для настроек нажмите 'Управление' в меню",
                parse_mode="Markdown",
                reply_markup=operator_menu()
            )
        else:
            bot.send_message(user_id, "❌ Недостаточно прав")
    
    elif text.startswith("/addop"):
        if is_admin(user_id):
            try:
                new_op = int(text.split()[1])
                if new_op not in operators:
                    operators.append(new_op)
                    save_config()
                    bot.send_message(user_id, f"✅ Оператор {new_op} добавлен")
                else:
                    bot.send_message(user_id, "❌ Оператор уже существует")
            except:
                bot.send_message(user_id, "❌ Использование: /addop <user_id>")
    
    elif text.startswith("/delop"):
        if is_admin(user_id):
            try:
                del_op = int(text.split()[1])
                if del_op == ADMIN_ID:
                    bot.send_message(user_id, "❌ Нельзя удалить администратора")
                elif del_op not in operators:
                    bot.send_message(user_id, "❌ Оператор не найден")
                else:
                    operators.remove(del_op)
                    save_config()
                    bot.send_message(user_id, f"✅ Оператор {del_op} удален")
            except:
                bot.send_message(user_id, "❌ Использование: /delop <user_id>")
    
    elif text.startswith("/broadcast"):
        broadcast_message(message)

def get_next_message(operator_id):
    """Взять следующее сообщение из очереди"""
    msg = get_next_message_for_operator(operator_id)
    
    if not msg:
        bot.send_message(
            operator_id,
            "📭 *Очередь пуста*\n\nНет новых сообщений для обработки.",
            parse_mode="Markdown",
            reply_markup=operator_menu()
        )
        return
    
    user_id = msg['user_id']
    user_info = format_user_info(user_id, 
                               users.get(user_id, {}).get('username', ''),
                               users.get(user_id, {}).get('first_name', ''))
    
    response = (
        f"📩 *СООБЩЕНИЕ ИЗ ОЧЕРЕДИ*\n\n"
        f"{user_info}\n\n"
        f"💬 *Текст:*\n{msg['text']}\n\n"
        f"📊 *Статистика пользователя:*\n"
        f"• Сообщений отправлено: {users.get(user_id, {}).get('messages_sent', 0)}\n"
        f"• В системе: {int((time.time() - users.get(user_id, {}).get('joined', time.time())) / 86400)} дней\n\n"
        f"🛠 *Действия:*\n"
        f"• Напишите ответ прямо здесь\n"
        f"• Или нажмите '💬 Ответить' для шаблона"
    )
    
    bot.send_message(operator_id, response, parse_mode="Markdown", reply_markup=operator_menu())

def reply_to_user(message):
    """Ответить пользователю"""
    operator_id = message.from_user.id
    text = message.text
    
    if operator_id not in waiting_answers or not waiting_answers[operator_id]['waiting']:
        bot.send_message(operator_id, "Сначала возьмите сообщение из очереди")
        return
    
    user_data = waiting_answers[operator_id]
    target_user_id = user_data['user_id']
    
    try:
        # Отправляем ответ пользователю
        response_text = (
            f"📩 *Ответ оператора:*\n\n"
            f"{text}\n\n"
            f"🕒 Время ответа: {get_moscow_time()}\n"
            
        )
        
        bot.send_message(target_user_id, response_text, parse_mode="Markdown")
        
        # Обновляем статистику
        if target_user_id in user_messages:
            for msg in user_messages[target_user_id]:
                if not msg['answered']:
                    msg['answered'] = True
                    break
        
        # Обновляем статистику оператора
        if operator_id not in operator_stats:
            operator_stats[operator_id] = {'answered': 0, 'response_time': []}
        operator_stats[operator_id]['answered'] += 1
        
        # Уведомляем оператора
        bot.send_message(
            operator_id,
            f"✅ *Ответ отправлен!*\n\n"
            f"👤 Пользователь: {target_user_id}\n"
            f"📝 Длина ответа: {len(text)} символов\n"
            f"🏆 Всего ответов: {operator_stats[operator_id]['answered']}",
            parse_mode="Markdown",
            reply_markup=operator_menu()
        )
        
        # Удаляем из очереди если есть
        if messages_queue and messages_queue[0]['user_id'] == target_user_id:
            messages_queue.pop(0)
        
        # Сбрасываем контекст
        waiting_answers.pop(operator_id, None)
        
        # Автосохранение
        save_data()
        
    except Exception as e:
        bot.send_message(operator_id, f"❌ Ошибка отправки: {str(e)}")

def handle_operator_media(message):
    """Обработка медиа от оператора"""
    operator_id = message.from_user.id
    
    if operator_id not in waiting_answers or not waiting_answers[operator_id]['waiting']:
        bot.send_message(operator_id, "Сначала возьмите сообщение из очереди")
        return
    
    user_data = waiting_answers[operator_id]
    target_user_id = user_data['user_id']
    
    try:
        if message.photo:
            file_id = message.photo[-1].file_id
            caption = f"📸 *Фото от оператора*\n\n🕒 {get_moscow_time()}"
            bot.send_photo(target_user_id, file_id, caption=caption, parse_mode="Markdown")
            
        elif message.video:
            file_id = message.video.file_id
            caption = f"🎬 *Видео от оператора*\n\n🕒 {get_moscow_time()}"
            bot.send_video(target_user_id, file_id, caption=caption, parse_mode="Markdown")
            
        elif message.document:
            file_id = message.document.file_id
            caption = f"📎 *Документ от оператора*\n\n🕒 {get_moscow_time()}"
            bot.send_document(target_user_id, file_id, caption=caption, parse_mode="Markdown")
        
        bot.send_message(
            operator_id,
            "✅ Медиа отправлено пользователю",
            reply_markup=operator_menu()
        )
        
    except Exception as e:
        bot.send_message(operator_id, f"❌ Ошибка отправки: {str(e)}")

def show_operator_stats(operator_id):
    """Показать статистику оператора"""
    stats = operator_stats.get(operator_id, {'answered': 0, 'response_time': []})
    
    total_answered = sum(op.get('answered', 0) for op in operator_stats.values())
    
    response = (
        f"📊 *ВАША СТАТИСТИКА*\n\n"
        f"🎯 Ответов отправлено: *{stats['answered']}*\n"
        f"🏆 Место в рейтинге: *{get_operator_rank(operator_id)}*\n"
        f"👥 Всего ответов всеми: *{total_answered}*\n\n"
        f"📈 *ОЧЕРЕДЬ:*\n"
        f"• Сообщений в очереди: *{len(messages_queue)}*\n"
        f"• Пользователей онлайн: *{len([u for u in users if time.time() - users[u].get('last_msg', 0) < 3600])}*\n"
        f"• Новых за сутки: *{len([u for u in users if time.time() - users[u].get('joined', 0) < 86400])}*"
    )
    
    bot.send_message(operator_id, response, parse_mode="Markdown", reply_markup=operator_menu())

def get_operator_rank(operator_id):
    """Получить место оператора в рейтинге"""
    sorted_ops = sorted(operator_stats.items(), 
                       key=lambda x: x[1].get('answered', 0), 
                       reverse=True)
    
    for i, (op_id, _) in enumerate(sorted_ops, 1):
        if op_id == operator_id:
            return i
    return len(sorted_ops) + 1

def show_info_panel(operator_id):
    """Показать информационную панель"""
    panel = (
        f"🎯 *ИНФОПАНЕЛЬ ОПЕРАТОРА*\n\n"
        f"🕒 Время системы: {get_moscow_time()}\n"
        f"🤖 Версия бота: 2.0\n"
        f"📅 Запущен: {datetime.now().strftime('%d.%m.%Y')}\n\n"
        f"📊 *СИСТЕМНЫЕ ПОКАЗАТЕЛИ:*\n"
        f"• Операторов онлайн: {len([op for op in operators if time.time() - operator_stats.get(op, {}).get('last_active', 0) < 300])}\n"
        f"• Среднее время ответа: {calculate_average_response_time()} мин\n"
        f"• Эффективность: {calculate_efficiency()}%\n"
        f"• Автоприветствие: {'ВКЛ' if system_settings['auto_greet'] else 'ВЫКЛ'}\n"
        f"• Капча: {'ВКЛ' if system_settings['captcha_enabled'] else 'ВЫКЛ'}\n\n"
        f"💡 *ПОЛЕЗНЫЕ КОМАНДЫ:*\n"
        f"• /admin - панель администратора\n"
        f"• /addop <id> - добавить оператора\n"
        f"• /delop <id> - удалить оператора\n"
        f"• /template <номер> - использовать шаблон"
    )
    
    bot.send_message(operator_id, panel, parse_mode="Markdown", reply_markup=operator_menu())

def calculate_average_response_time():
    """Рассчитать среднее время ответа"""
    if not messages_queue:
        return 0
    oldest = min(messages_queue, key=lambda x: x['time'])
    return round((time.time() - oldest['time']) / 60, 1)

def calculate_efficiency():
    """Рассчитать эффективность системы"""
    total_messages = sum(len(msgs) for msgs in user_messages.values())
    answered = sum(1 for msgs in user_messages.values() for msg in msgs if msg.get('answered', False))
    
    if total_messages == 0:
        return 0
    return round((answered / total_messages) * 100, 1)

def broadcast_message(message):
    """Рассылка сообщения всем пользователям"""
    operator_id = message.from_user.id
    
    # Проверка прав (только админ)
    if not is_admin(operator_id):
        bot.send_message(operator_id, "❌ Недостаточно прав")
        return
    
    try:
        text = message.text.replace('/broadcast ', '', 1)
        
        if not text:
            bot.send_message(operator_id, "❌ Использование: /broadcast <текст>")
            return
        
        # Подсчет пользователей
        sent = 0
        failed = 0
        
        for user_id in list(users.keys()):
            try:
                bot.send_message(
                    user_id,
                    f"📢 *Важное сообщение от администратора:*\n\n{text}",
                    parse_mode="Markdown"
                )
                sent += 1
            except:
                failed += 1
            time.sleep(0.1)  # Защита от флуда
        
        bot.send_message(
            operator_id,
            f"✅ *Рассылка завершена!*\n\n"
            f"📤 Отправлено: {sent}\n"
            f"❌ Не отправлено: {failed}",
            parse_mode="Markdown"
        )
        
    except Exception as e:
        bot.send_message(operator_id, f"❌ Ошибка: {str(e)}")

def use_template(message):
    """Использовать шаблон ответа"""
    operator_id = message.from_user.id
    
    try:
        parts = message.text.split(' ', 1)
        if len(parts) < 2:
            # Показать список шаблонов
            if not answer_templates:
                bot.send_message(operator_id, "❌ Шаблоны не настроены")
                return
            
            templates_list = "📝 *Доступные шаблоны:*\n\n"
            for key, template in answer_templates.items():
                templates_list += f"• /template {key}: {template['name']}\n"
            
            bot.send_message(operator_id, templates_list, parse_mode="Markdown")
            return
        
        template_key = parts[1]
        
        if template_key not in answer_templates:
            bot.send_message(operator_id, f"❌ Шаблон {template_key} не найден")
            return
        
        if operator_id not in waiting_answers or not waiting_answers[operator_id]['waiting']:
            bot.send_message(operator_id, "❌ Сначала возьмите сообщение из очереди")
            return
        
        user_data = waiting_answers[operator_id]
        template = answer_templates[template_key]
        
        # Отправляем шаблон
        response_text = (
            f"📩 *Ответ оператора:*\n\n"
            f"{template['text']}\n\n"
            f"🕒 Время ответа: {get_moscow_time()}\n"
        
        )
        
        bot.send_message(user_data['user_id'], response_text, parse_mode="Markdown")
        bot.send_message(operator_id, f"✅ Шаблон '{template['name']}' отправлен")
        
        # Обновляем статистику
        if operator_id not in operator_stats:
            operator_stats[operator_id] = {'answered': 0}
        operator_stats[operator_id]['answered'] += 1
        
        # Сбрасываем контекст
        waiting_answers.pop(operator_id, None)
        
    except Exception as e:
        bot.send_message(operator_id, f"❌ Ошибка: {str(e)}")

# =============================
# ИНЛАЙН КНОПКИ (УПРАВЛЕНИЕ)
# =============================

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    """Обработка инлайн кнопок"""
    operator_id = call.from_user.id
    
    # Проверка прав администратора
    if not is_admin(operator_id):
        bot.answer_callback_query(call.id, "❌ Недостаточно прав", show_alert=True)
        return
    
    if call.data.startswith("reply_"):
        user_id = int(call.data.split("_")[1])
        start_operator_reply(operator_id, user_id)
        
    elif call.data.startswith("solve_"):
        user_id = int(call.data.split("_")[1])
        mark_as_solved(operator_id, user_id)
        
    elif call.data.startswith("reject_"):
        user_id = int(call.data.split("_")[1])
        reject_message(operator_id, user_id)
        
    elif call.data.startswith("history_"):
        user_id = int(call.data.split("_")[1])
        show_user_history(operator_id, user_id)
        
    # Меню управления
    elif call.data == "menu_operators":
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="👥 *Управление операторами*",
            parse_mode="Markdown",
            reply_markup=operators_menu()
        )
        
    elif call.data == "menu_system":
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="⚙️ *Настройки системы*",
            parse_mode="Markdown",
            reply_markup=system_menu()
        )
        
    elif call.data == "menu_templates":
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="📝 *Управление шаблонами*",
            parse_mode="Markdown",
            reply_markup=templates_menu()
        )
        
    elif call.data == "menu_worktime":
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="🕒 *Настройка времени работы*",
            parse_mode="Markdown",
            reply_markup=worktime_menu()
        )
        
    elif call.data == "menu_cleanup":
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="🧹 *Очистка данных*",
            parse_mode="Markdown",
            reply_markup=cleanup_menu()
        )
        
    elif call.data == "back_to_settings":
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="⚙️ *Панель управления системой*",
            parse_mode="Markdown",
            reply_markup=settings_menu()
        )
    
    # Операторы
    elif call.data == "add_operator":
        add_operator_dialog(operator_id, call.message.message_id)
        
    elif call.data == "remove_operator":
        remove_operator_dialog(operator_id, call.message.message_id)
        
    elif call.data == "list_operators":
        list_operators(operator_id, call.message.message_id)
    
    # Система
    elif call.data == "toggle_greet":
        toggle_setting('auto_greet', operator_id, call.message.message_id)
        
    elif call.data == "toggle_notify":
        toggle_setting('notify_operators', operator_id, call.message.message_id)
        
    elif call.data == "toggle_captcha":
        toggle_setting('captcha_enabled', operator_id, call.message.message_id)
        
    elif call.data == "set_queue_limit":
        set_queue_limit_dialog(operator_id, call.message.message_id)
        
    elif call.data == "set_timeout":
        set_timeout_dialog(operator_id, call.message.message_id)
    
    # Шаблоны
    elif call.data == "list_templates":
        list_templates(operator_id, call.message.message_id)
        
    elif call.data == "add_template":
        add_template_dialog(operator_id, call.message.message_id)
        
    elif call.data == "edit_template":
        edit_template_dialog(operator_id, call.message.message_id)
        
    elif call.data == "delete_template":
        delete_template_dialog(operator_id, call.message.message_id)
    
    # Время работы
    elif call.data == "toggle_worktime":
        toggle_worktime(operator_id, call.message.message_id)
        
    elif call.data == "set_work_start":
        set_work_start_dialog(operator_id, call.message.message_id)
        
    elif call.data == "set_work_end":
        set_work_end_dialog(operator_id, call.message.message_id)
    
    # Очистка
    elif call.data == "clean_queue":
        clean_queue(operator_id, call.message.message_id)
        
    elif call.data == "clean_history":
        clean_history_dialog(operator_id, call.message.message_id)
        
    elif call.data == "reset_stats":
        reset_stats_dialog(operator_id, call.message.message_id)
    
    bot.answer_callback_query(call.id)

def start_operator_reply(operator_id, user_id):
    """Начать ответ пользователю"""
    waiting_answers[operator_id] = {
        'user_id': user_id,
        'waiting': True
    }
    
    bot.send_message(
        operator_id,
        f"💬 *Режим ответа пользователю {user_id}*\n\n"
        f"Напишите ответ в этом чате.\n"
        f"Можно отправлять текст и медиафайлы.\n\n"
        f"Для отмены нажмите '🔄 Сбросить ответ'",
        parse_mode="Markdown"
    )

def mark_as_solved(operator_id, user_id):
    """Пометить как решенное"""
    if user_id in user_messages:
        for msg in user_messages[user_id]:
            msg['answered'] = True
    
    bot.send_message(operator_id, f"✅ Вопрос пользователя {user_id} помечен как решенный")
    
    # Уведомляем пользователя
    try:
        bot.send_message(
            user_id,
            "✅ *Ваш вопрос решен*\n\n"
            "Оператор поместил ваш вопрос как решенный. "
            "Если у вас есть новые вопросы - напишите нам!",
            parse_mode="Markdown"
        )
    except:
        pass
    
    save_data()

def reject_message(operator_id, user_id):
    """Отклонить сообщение"""
    # Удаляем из очереди
    global messages_queue
    messages_queue = [msg for msg in messages_queue if msg['user_id'] != user_id]
    
    bot.send_message(operator_id, f"❌ Сообщение пользователя {user_id} отклонено")
    
    # Уведомляем пользователя
    try:
        bot.send_message(
            user_id,
            "❌ *Ваше сообщение отклонено*\n\n"
            "Оператор отклонил ваше сообщение. "
            "Пожалуйста, сформулируйте вопрос более четко.",
            parse_mode="Markdown"
        )
    except:
        pass
    
    save_data()

def show_user_history(operator_id, user_id):
    """Показать историю пользователя"""
    if user_id not in user_messages:
        bot.send_message(operator_id, "История пуста")
        return
    
    history = f"📋 *История пользователя {user_id}:*\n\n"
    
    for i, msg in enumerate(user_messages[user_id][-10:], 1):  # Последние 10 сообщений
        time_str = datetime.fromtimestamp(msg['time']).strftime('%H:%M %d.%m')
        status = "✅" if msg.get('answered', False) else "⏳"
        preview = msg['text'][:50] + "..." if len(msg['text']) > 50 else msg['text']
        history += f"{i}. {time_str} {status}: {preview}\n"
    
    bot.send_message(operator_id, history, parse_mode="Markdown")

# =============================
# ФУНКЦИИ УПРАВЛЕНИЯ
# =============================

def add_operator_dialog(operator_id, message_id):
    """Диалог добавления оператора"""
    msg = bot.send_message(
        operator_id,
        "👥 *Добавление оператора*\n\n"
        "Введите ID пользователя, которого хотите добавить:",
        parse_mode="Markdown"
    )
    
    bot.register_next_step_handler(msg, process_add_operator, message_id)

def process_add_operator(message, original_message_id):
    """Обработка добавления оператора"""
    try:
        new_op = int(message.text)
        
        if new_op in operators:
            bot.send_message(message.chat.id, "❌ Этот пользователь уже оператор")
        else:
            operators.append(new_op)
            save_config()
            
            bot.send_message(message.chat.id, f"✅ Оператор {new_op} добавлен")
            
            # Возвращаемся к меню
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=original_message_id,
                text="👥 *Управление операторами*",
                parse_mode="Markdown",
                reply_markup=operators_menu()
            )
    except ValueError:
        bot.send_message(message.chat.id, "❌ Введите числовой ID")

def remove_operator_dialog(operator_id, message_id):
    """Диалог удаления оператора"""
    if len(operators) <= 1:
        bot.send_message(operator_id, "❌ Нельзя удалить последнего оператора")
        return
    
    ops_list = "\n".join([f"• {op_id}" for op_id in operators if op_id != ADMIN_ID])
    
    msg = bot.send_message(
        operator_id,
        f"👥 *Удаление оператора*\n\n"
        f"Текущие операторы:\n{ops_list}\n\n"
        f"Введите ID оператора для удаления:",
        parse_mode="Markdown"
    )
    
    bot.register_next_step_handler(msg, process_remove_operator, message_id)

def process_remove_operator(message, original_message_id):
    """Обработка удаления оператора"""
    try:
        del_op = int(message.text)
        
        if del_op == ADMIN_ID:
            bot.send_message(message.chat.id, "❌ Нельзя удалить администратора")
        elif del_op not in operators:
            bot.send_message(message.chat.id, "❌ Оператор не найден")
        else:
            operators.remove(del_op)
            save_config()
            
            bot.send_message(message.chat.id, f"✅ Оператор {del_op} удален")
            
            # Возвращаемся к меню
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=original_message_id,
                text="👥 *Управление операторов*",
                parse_mode="Markdown",
                reply_markup=operators_menu()
            )
    except ValueError:
        bot.send_message(message.chat.id, "❌ Введите числовой ID")

def list_operators(operator_id, message_id):
    """Список операторов"""
    ops_list = "\n".join([f"• {op_id} {'👑' if op_id == ADMIN_ID else '👤'}" for op_id in operators])
    
    bot.edit_message_text(
        chat_id=operator_id,
        message_id=message_id,
        text=f"📋 *Список операторов*\n\n{ops_list}\n\nВсего: {len(operators)}",
        parse_mode="Markdown",
        reply_markup=operators_menu()
    )

def toggle_setting(setting_name, operator_id, message_id):
    """Переключение настройки"""
    current_value = system_settings.get(setting_name, False)
    system_settings[setting_name] = not current_value
    
    save_data()
    
    # Обновляем меню
    setting_names = {
        'auto_greet': 'Автоприветствие',
        'notify_operators': 'Уведомления операторов',
        'captcha_enabled': 'Капча'
    }
    
    status = "✅ ВКЛ" if system_settings[setting_name] else "❌ ВЫКЛ"
    
    bot.edit_message_text(
        chat_id=operator_id,
        message_id=message_id,
        text=f"⚙️ *Настройки системы*\n\n{setting_names[setting_name]}: {status}",
        parse_mode="Markdown",
        reply_markup=system_menu()
    )

def set_queue_limit_dialog(operator_id, message_id):
    """Диалог установки лимита очереди"""
    msg = bot.send_message(
        operator_id,
        f"📏 *Установка лимита очереди*\n\n"
        f"Текущий лимит: {system_settings.get('max_queue_size', 100)} сообщений\n\n"
        f"Введите новый лимит (10-1000):",
        parse_mode="Markdown"
    )
    
    bot.register_next_step_handler(msg, process_queue_limit, message_id)

def process_queue_limit(message, original_message_id):
    """Обработка установки лимита очереди"""
    try:
        limit = int(message.text)
        
        if 10 <= limit <= 1000:
            system_settings['max_queue_size'] = limit
            save_data()
            
            bot.send_message(message.chat.id, f"✅ Лимит очереди установлен: {limit}")
            
            # Возвращаемся к меню
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=original_message_id,
                text="⚙️ *Настройки системы*",
                parse_mode="Markdown",
                reply_markup=system_menu()
            )
        else:
            bot.send_message(message.chat.id, "❌ Лимит должен быть от 10 до 1000")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Введите число")

def set_timeout_dialog(operator_id, message_id):
    """Диалог установки таймаута"""
    msg = bot.send_message(
        operator_id,
        f"⏱️ *Установка таймаута*\n\n"
        f"Текущий таймаут: {WAIT_TIME} секунд\n\n"
        f"Введите новый таймаут (10-3600 секунд):",
        parse_mode="Markdown"
    )
    
    bot.register_next_step_handler(msg, process_timeout, message_id)

def process_timeout(message, original_message_id):
    """Обработка установки таймаута"""
    try:
        timeout = int(message.text)
        
        if 10 <= timeout <= 3600:
            global WAIT_TIME
            WAIT_TIME = timeout
            save_config()
            
            bot.send_message(message.chat.id, f"✅ Таймаут установлен: {timeout} сек")
            
            # Возвращаемся к меню
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=original_message_id,
                text="⚙️ *Настройки системы*",
                parse_mode="Markdown",
                reply_markup=system_menu()
            )
        else:
            bot.send_message(message.chat.id, "❌ Таймаут должен быть от 10 до 3600 секунд")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Введите число")

def list_templates(operator_id, message_id):
    """Список шаблонов"""
    if not answer_templates:
        text = "📝 *Шаблоны ответов*\n\nШаблоны не настроены"
    else:
        text = "📝 *Шаблоны ответов*\n\n"
        for key, template in answer_templates.items():
            text += f"• {key}: {template['name']}\n"
            text += f"  {template['text'][:50]}...\n\n"
    
    bot.edit_message_text(
        chat_id=operator_id,
        message_id=message_id,
        text=text,
        parse_mode="Markdown",
        reply_markup=templates_menu()
    )

def add_template_dialog(operator_id, message_id):
    """Диалог добавления шаблона"""
    msg = bot.send_message(
        operator_id,
        "➕ *Добавление шаблона*\n\n"
        "Введите название шаблона:",
        parse_mode="Markdown"
    )
    
    bot.register_next_step_handler(msg, process_add_template_name, message_id)

def process_add_template_name(message, original_message_id):
    """Обработка названия шаблона"""
    template_name = message.text
    
    msg = bot.send_message(
        message.chat.id,
        f"📝 *Название: {template_name}*\n\n"
        f"Теперь введите текст шаблона:",
        parse_mode="Markdown"
    )
    
    bot.register_next_step_handler(msg, process_add_template_text, original_message_id, template_name)

def process_add_template_text(message, original_message_id, template_name):
    """Обработка текста шаблона"""
    template_text = message.text
    
    # Генерируем ключ
    key = str(len(answer_templates) + 1)
    answer_templates[key] = {
        'name': template_name,
        'text': template_text
    }
    
    save_data()
    
    bot.send_message(message.chat.id, f"✅ Шаблон '{template_name}' добавлен")
    
    # Возвращаемся к меню
    bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=original_message_id,
        text="📝 *Управление шаблонами*",
        parse_mode="Markdown",
        reply_markup=templates_menu()
    )

def edit_template_dialog(operator_id, message_id):
    """Диалог редактирования шаблона"""
    if not answer_templates:
        bot.send_message(operator_id, "❌ Шаблоны не настроены")
        return
    
    templates_list = "📝 *Редактирование шаблона*\n\n"
    for key, template in answer_templates.items():
        templates_list += f"{key}: {template['name']}\n"
    
    msg = bot.send_message(
        operator_id,
        templates_list + "\nВведите номер шаблона для редактирования:",
        parse_mode="Markdown"
    )
    
    bot.register_next_step_handler(msg, process_edit_template_select, message_id)

def process_edit_template_select(message, original_message_id):
    """Обработка выбора шаблона для редактирования"""
    key = message.text
    
    if key not in answer_templates:
        bot.send_message(message.chat.id, "❌ Шаблон не найден")
        return
    
    template = answer_templates[key]
    
    msg = bot.send_message(
        message.chat.id,
        f"✏️ *Редактирование шаблона {key}: {template['name']}*\n\n"
        f"Текущий текст:\n{template['text']}\n\n"
        f"Введите новый текст:",
        parse_mode="Markdown"
    )
    
    bot.register_next_step_handler(msg, process_edit_template_text, original_message_id, key)

def process_edit_template_text(message, original_message_id, key):
    """Обработка нового текста шаблона"""
    new_text = message.text
    answer_templates[key]['text'] = new_text
    
    save_data()
    
    bot.send_message(message.chat.id, f"✅ Шаблон {key} обновлен")
    
    # Возвращаемся к меню
    bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=original_message_id,
        text="📝 *Управление шаблонами*",
        parse_mode="Markdown",
        reply_markup=templates_menu()
    )

def delete_template_dialog(operator_id, message_id):
    """Диалог удаления шаблона"""
    if not answer_templates:
        bot.send_message(operator_id, "❌ Шаблоны не настроены")
        return
    
    templates_list = "🗑️ *Удаление шаблона*\n\n"
    for key, template in answer_templates.items():
        templates_list += f"{key}: {template['name']}\n"
    
    msg = bot.send_message(
        operator_id,
        templates_list + "\nВведите номер шаблона для удаления:",
        parse_mode="Markdown"
    )
    
    bot.register_next_step_handler(msg, process_delete_template, message_id)

def process_delete_template(message, original_message_id):
    """Обработка удаления шаблона"""
    key = message.text
    
    if key not in answer_templates:
        bot.send_message(message.chat.id, "❌ Шаблон не найден")
        return
    
    template_name = answer_templates[key]['name']
    del answer_templates[key]
    
    save_data()
    
    bot.send_message(message.chat.id, f"✅ Шаблон '{template_name}' удален")
    
    # Возвращаемся к меню
    bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=original_message_id,
        text="📝 *Управление шаблонами*",
        parse_mode="Markdown",
        reply_markup=templates_menu()
    )

def toggle_worktime(operator_id, message_id):
    """Переключение режима работы"""
    current_value = system_settings.get('work_hours_enabled', False)
    system_settings['work_hours_enabled'] = not current_value
    
    save_data()
    
    status = "✅ ВКЛ" if system_settings['work_hours_enabled'] else "❌ ВЫКЛ"
    
    bot.edit_message_text(
        chat_id=operator_id,
        message_id=message_id,
        text=f"🕒 *Настройка времени работы*\n\nРежим работы: {status}",
        parse_mode="Markdown",
        reply_markup=worktime_menu()
    )

def set_work_start_dialog(operator_id, message_id):
    """Диалог установки времени начала работы"""
    msg = bot.send_message(
        operator_id,
        f"🕘 *Установка времени начала работы*\n\n"
        f"Текущее время: {system_settings.get('work_hours_start', 9)}:00\n\n"
        f"Введите час начала работы (0-23):",
        parse_mode="Markdown"
    )
    
    bot.register_next_step_handler(msg, process_work_start, message_id)

def process_work_start(message, original_message_id):
    """Обработка времени начала работы"""
    try:
        hour = int(message.text)
        
        if 0 <= hour <= 23:
            system_settings['work_hours_start'] = hour
            save_data()
            
            bot.send_message(message.chat.id, f"✅ Время начала работы установлено: {hour}:00")
            
            # Возвращаемся к меню
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=original_message_id,
                text="🕒 *Настройка времени работы*",
                parse_mode="Markdown",
                reply_markup=worktime_menu()
            )
        else:
            bot.send_message(message.chat.id, "❌ Час должен быть от 0 до 23")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Введите число")

def set_work_end_dialog(operator_id, message_id):
    """Диалог установки времени окончания работы"""
    msg = bot.send_message(
        operator_id,
        f"🕘 *Установка времени окончания работы*\n\n"
        f"Текущее время: {system_settings.get('work_hours_end', 21)}:00\n\n"
        f"Введите час окончания работы (0-23):",
        parse_mode="Markdown"
    )
    
    bot.register_next_step_handler(msg, process_work_end, message_id)

def process_work_end(message, original_message_id):
    """Обработка времени окончания работы"""
    try:
        hour = int(message.text)
        
        if 0 <= hour <= 23:
            system_settings['work_hours_end'] = hour
            save_data()
            
            bot.send_message(message.chat.id, f"✅ Время окончания работы установлено: {hour}:00")
            
            # Возвращаемся к меню
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=original_message_id,
                text="🕒 *Настройка времени работы*",
                parse_mode="Markdown",
                reply_markup=worktime_menu()
            )
        else:
            bot.send_message(message.chat.id, "❌ Час должен быть от 0 до 23")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Введите число")

def clean_queue(operator_id, message_id):
    """Очистка очереди"""
    global messages_queue
    count = len(messages_queue)
    messages_queue.clear()
    
    bot.edit_message_text(
        chat_id=operator_id,
        message_id=message_id,
        text=f"🧹 *Очистка данных*\n\n✅ Очередь очищена: удалено {count} сообщений",
        parse_mode="Markdown",
        reply_markup=cleanup_menu()
    )
    
    # Уведомляем операторов
    for op_id in operators:
        if op_id != operator_id:
            try:
                bot.send_message(op_id, f"⚠️ Очередь очищена администратором. Удалено {count} сообщений")
            except:
                pass

def clean_history_dialog(operator_id, message_id):
    """Диалог очистки истории"""
    user_count = len(user_messages)
    total_messages = sum(len(msgs) for msgs in user_messages.values())
    
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Да, очистить", callback_data="confirm_clean_history"),
        types.InlineKeyboardButton("❌ Нет, отмена", callback_data="menu_cleanup")
    )
    
    bot.edit_message_text(
        chat_id=operator_id,
        message_id=message_id,
        text=f"🧹 *Очистка истории*\n\n"
             f"Будет удалено:\n"
             f"• История {user_count} пользователей\n"
             f"• {total_messages} сообщений\n\n"
             f"Вы уверены?",
        parse_mode="Markdown",
        reply_markup=kb
    )

@bot.callback_query_handler(func=lambda call: call.data == "confirm_clean_history")
def confirm_clean_history(call):
    """Подтверждение очистки истории"""
    global user_messages
    user_count = len(user_messages)
    total_messages = sum(len(msgs) for msgs in user_messages.values())
    
    user_messages.clear()
    save_data()
    
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"🧹 *Очистка данных*\n\n✅ История очищена:\n• Пользователей: {user_count}\n• Сообщений: {total_messages}",
        parse_mode="Markdown",
        reply_markup=cleanup_menu()
    )

def reset_stats_dialog(operator_id, message_id):
    """Диалог сброса статистики"""
    ops_count = len(operator_stats)
    total_answered = sum(op.get('answered', 0) for op in operator_stats.values())
    
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Да, сбросить", callback_data="confirm_reset_stats"),
        types.InlineKeyboardButton("❌ Нет, отмена", callback_data="menu_cleanup")
    )
    
    bot.edit_message_text(
        chat_id=operator_id,
        message_id=message_id,
        text=f"📊 *Сброс статистики*\n\n"
             f"Будет сброшено:\n"
             f"• Статистика {ops_count} операторов\n"
             f"• {total_answered} ответов\n\n"
             f"Вы уверены?",
        parse_mode="Markdown",
        reply_markup=kb
    )

@bot.callback_query_handler(func=lambda call: call.data == "confirm_reset_stats")
def confirm_reset_stats(call):
    """Подтверждение сброса статистики"""
    global operator_stats
    ops_count = len(operator_stats)
    total_answered = sum(op.get('answered', 0) for op in operator_stats.values())
    
    operator_stats.clear()
    save_data()
    
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"🧹 *Очистка данных*\n\n✅ Статистика сброшена:\n• Операторов: {ops_count}\n• Ответов: {total_answered}",
        parse_mode="Markdown",
        reply_markup=cleanup_menu()
    )

# =============================
# ЗАПУСК БОТА
# =============================

def run_bot():
    """Запуск бота"""
    print("=" * 50)
    print("🤖 АНОНИМНЫЙ ЧАТ-БОТ v2.0")
    print("=" * 50)
    print(f"Операторов: {len(operators)}")
    print(f"Админ: {ADMIN_ID if ADMIN_ID else 'Не задан'}")
    print(f"Таймаут: {WAIT_TIME} сек")
    print(f"Токен: {'✅ OK' if BOT_TOKEN else '❌ НЕ НАЙДЕН'}")
    print("=" * 50)
    
    # Загрузка данных
    load_data()
    print(f"Загружено пользователей: {len(users)}")
    print(f"Загружено сообщений: {sum(len(msgs) for msgs in user_messages.values())}")
    print(f"Загружено шаблонов: {len(answer_templates)}")
    
    if not BOT_TOKEN:
        print("❌ Ошибка: Добавьте BOT_TOKEN в config.ini")
        return
    
    print("🚀 Бот запущен...")
    print("💡 Система готова к работе!")
    
    # Автозапуск для операторов
    for op_id in operators:
        try:
            bot.send_message(op_id, "🔄 Бот перезапущен и готов к работе!")
        except:
            pass
    
    # Планировщик автосохранения
    import threading
    
    def auto_save():
        """Автосохранение данных"""
        while True:
            time.sleep(300)  # 5 минут
            if save_data():
                print(f"💾 Автосохранение: {datetime.now().strftime('%H:%M:%S')}")
    
    # Запуск автосохранения в отдельном потоке
    save_thread = threading.Thread(target=auto_save, daemon=True)
    save_thread.start()
    
    while True:
        try:
            bot.polling(none_stop=True, timeout=60)
        except Exception as e:
            print(f"⚠️ Ошибка: {e}")
            time.sleep(5)

if __name__ == "__main__":
    run_bot()