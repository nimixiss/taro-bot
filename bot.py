import os
import telebot
import json
import random
import requests
import time
from datetime import datetime
from telebot.apihelper import ApiTelegramException
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo

# === Настройки ===
TOKEN = os.getenv("BOT_TOKEN")
CARDS_FOLDER = "images"
WEBAPP_URL = "https://nimixiss.github.io/tarot-webapp/"

ADMIN_ID = 220493509  # это ты :)
single_card_usage = {}  # {user_id: 'YYYY-MM-DD'}

# === Загрузка данных ===
with open("tarot_cards.json", "r", encoding="utf-8") as f:
    tarot_deck = json.load(f)

TOPICS_FILE = "tarot_cards_topics.json"
if os.path.exists(TOPICS_FILE):
    with open(TOPICS_FILE, "r", encoding="utf-8") as f:
        tarot_topics = json.load(f)
else:
    # Файл с темами может отсутствовать на некоторых развёртываниях.
    # В этом случае используем данные из tarot_cards.json, если они
    # уже содержат тематические значения.
    tarot_topics = {}
    for card_name, card_data in tarot_deck.items():
        if isinstance(card_data, dict):
            filtered_topics = {
                topic: values
                for topic, values in card_data.items()
                if isinstance(values, list)
            }
            if filtered_topics:
                tarot_topics[card_name] = filtered_topics


def _collect_all_meanings(card_data):
    """Возвращает плоский список значений карты из любых доступных структур."""
    if isinstance(card_data, list):
        return [value for value in card_data if isinstance(value, str)]

    if isinstance(card_data, dict):
        collected = []
        for values in card_data.values():
            if isinstance(values, list):
                collected.extend(v for v in values if isinstance(v, str))
        return collected

    return []

TOPIC_TO_KEY = {
    "❤️ Любовь": "love",
    "💼 Карьера": "career",
    "💰 Финансы": "finance",
    "🧘‍♀️ Здоровье": "health",
    "🧿 Совет дня": "advice",
}

with open("combinations.json", "r", encoding="utf-8") as f:
    combinations_3cards = json.load(f)

TWO_CARDS_URL = "https://raw.githubusercontent.com/nimixiss/tarot-webapp/main/two_card_combinations_full.json"
try:
    response = requests.get(TWO_CARDS_URL, timeout=15)
    response.raise_for_status()
    combinations_2cards = response.json()
except requests.RequestException as exc:
    combinations_2cards = {}
    print(
        f"Не удалось загрузить комбинации для двух карт: {exc}",
        flush=True,
    )

bot = telebot.TeleBot(TOKEN)


def _build_main_menu() -> ReplyKeyboardMarkup:
    """Создаёт главное меню с раскладами."""
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(
        KeyboardButton("🃏 Одна карта"),
        KeyboardButton("🔮 Три карты"),
    )
    markup.add(
        KeyboardButton("🧿 Две карты", web_app=WebAppInfo(url=WEBAPP_URL)),
    )
    return markup


# === Главное меню ===
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.send_message(
        message.chat.id,
        "🌙 Привет! Я Таро-бот. Выбери расклад:",
        reply_markup=_build_main_menu(),
    )

# === Одна карта с лимитом и выбором темы ===

SINGLE_CARD_TOPICS = [
    "❤️ Любовь",
    "💼 Карьера",
    "💰 Финансы",
    "🧘‍♀️ Здоровье",
    "🧿 Совет дня",
]

def _has_used_single_card_today(user_id: int) -> bool:
    """Проверяем, тянул ли пользователь карту сегодня."""
    today = datetime.utcnow().date().isoformat()
    return single_card_usage.get(user_id) == today

def _mark_single_card_used_today(user_id: int) -> None:
    today = datetime.utcnow().date().isoformat()
    single_card_usage[user_id] = today

@bot.message_handler(func=lambda msg: msg.text == "🃏 Одна карта")
def ask_single_card_topic(message):
    user_id = message.from_user.id

    # Админ (ты) может пользоваться без ограничений
    if user_id != ADMIN_ID and _has_used_single_card_today(user_id):
        bot.send_message(
            message.chat.id,
            "✨ Вселенная уже ответила тебе сегодня. "
            "Приходи завтра, когда энергия обновится 🌙",
        )
        return

    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("❤️ Любовь"), KeyboardButton("💼 Карьера"))
    markup.add(KeyboardButton("💰 Финансы"), KeyboardButton("🧘‍♀️ Здоровье"))
    markup.add(KeyboardButton("🧿 Совет дня"))
    msg = bot.send_message(
        message.chat.id,
        "Выбери сферу, о которой хочешь спросить:",
        reply_markup=markup,
    )
    bot.register_next_step_handler(msg, send_single_card_with_topic, user_id)
def send_single_card_with_topic(message, user_id: int):
    topic = message.text

    if topic not in SINGLE_CARD_TOPICS:
        bot.send_message(
            message.chat.id,
            "Я жду выбор одной из сфер: любовь, карьера, финансы, здоровье или совет дня 💫",
        )
        return

    # Тянем карту
    card = random.choice(list(tarot_deck.keys()))
    category_key = TOPIC_TO_KEY[topic]

    # Берём значение по категории из tarot_topics
    if card in tarot_topics and category_key in tarot_topics[card]:
        meaning_list = tarot_topics[card][category_key]
        meaning = random.choice(meaning_list)
    else:
        # запасной вариант — если вдруг для карты нет записей в новом файле
        fallback_values = _collect_all_meanings(tarot_deck.get(card))
        if fallback_values:
            meaning = random.choice(fallback_values)
        else:
            meaning = "Значение не найдено — доверься своей интуиции."

    # Запоминаем, что пользователь уже тянул карту сегодня (кроме админа)
    if user_id != ADMIN_ID:
        _mark_single_card_used_today(user_id)

    _send_single_card_reply(message.chat.id, card, topic, meaning)


def _send_single_card_reply(chat_id: int, card: str, topic: str, meaning: str) -> None:
    caption = (
        f"🃏 *{card}*\n"
        f"Сфера: {topic}\n"
        f"_{meaning}_"
    )

    path = os.path.join(CARDS_FOLDER, f"{card}.png")
    if os.path.exists(path):
        with open(path, "rb") as photo:
            bot.send_photo(
                chat_id,
                photo,
                caption=caption,
                parse_mode="Markdown",
                reply_markup=_build_main_menu(),
            )
            return

    bot.send_message(
        chat_id,
        caption,
        parse_mode="Markdown",
        reply_markup=_build_main_menu(),
    )

# === Три карты ===
@bot.message_handler(func=lambda msg: msg.text == "🔮 Три карты")
def send_three_cards(message):
    key = random.choice(list(combinations_3cards.keys()))
    selected_cards = key.split("|")
    meaning = combinations_3cards[key]
    names = "\n".join([f"• {card}" for card in selected_cards])
    bot.send_message(message.chat.id, f"🔮 *Три карты:*\n\n{names}\n\n{meaning}", parse_mode="Markdown")

# === Обработка WebApp данных ===
@bot.message_handler(content_types=['web_app_data'])
def handle_web_app_data(message):
    try:
        data = json.loads(message.web_app_data.data)
        card1 = data.get("card1")
        card2 = data.get("card2")

        if not card1 or not card2:
            bot.send_message(message.chat.id, "Ошибка: не удалось получить карты.")
            return

        sorted_key = "|".join(sorted([card1, card2]))
        meaning = combinations_2cards.get(sorted_key)

        if meaning:
            response = f"🧿 *Две карты:*\n\n• {card1}\n• {card2}\n\n{meaning}"
            bot.send_message(message.chat.id, response, parse_mode="Markdown")
        else:
            bot.send_message(message.chat.id, "❌ Ошибка: трактовка не найдена.")
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка обработки: {e}")

# === Запуск бота ===
def _start_polling(
    *,
    timeout: int = 60,
    long_polling_timeout: int = 30,
    max_retries: int = 3,
    retry_delay: int = 5,
) -> None:
    """Запускает long polling, обрабатывая конфликт 409 от Telegram.

    Параметры таймаутов оставлены именованными, чтобы оставаться совместимыми
    с существующими вызовами вроде ``bot.infinity_polling(long_polling_timeout=30)``
    из основной ветки.
    """

    try:
        bot.remove_webhook()
    except ApiTelegramException as exc:
        print(f"Не удалось снять webhook: {exc}", flush=True)

    attempts = 0
    while True:
        try:
            bot.infinity_polling(timeout=timeout, long_polling_timeout=long_polling_timeout)
            return
        except ApiTelegramException as exc:
            if exc.error_code != 409:
                raise

            attempts += 1
            print(
                "Polling остановлен: обнаружен другой активный процесс бота. "
                "Через несколько секунд попробуем снова...",
                flush=True,
            )

            if attempts >= max_retries:
                print(
                    "Не удалось получить управление ботом. "
                    "Убедись, что не осталось других запущенных экземпляров.",
                    flush=True,
                )
                return

            time.sleep(retry_delay)


if __name__ == "__main__":
    _start_polling(timeout=60, long_polling_timeout=30)
