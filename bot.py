import os
import telebot
import json
import random
import requests
import time
import threading
from typing import Dict
from datetime import datetime
from telebot.apihelper import ApiTelegramException
from telebot.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    WebAppInfo,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
)

# === Настройки ===
TOKEN = os.getenv("BOT_TOKEN")
CARDS_FOLDER = "images"
WEBAPP_URL = "https://nimixiss.github.io/tarot-webapp/"
CONSULTATION_URL = "https://t.me/helenatarotbot"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USAGE_STORAGE_PATH = os.path.join(BASE_DIR, "single_card_usage.json")

# Для Telegram Stars при продаже цифровых услуг можно передавать
# пустой provider_token – это корректно по официальной документации.
# Если когда-нибудь захочешь использовать свой токен, можно
# выставить его через переменную окружения.
STARS_PROVIDER_TOKEN = os.getenv("STARS_PROVIDER_TOKEN", "")

CONSULTATION_PRICE_STARS = 100  # сколько звёзд стоит консультация
CONSULTATION_PRICE_UNITS = CONSULTATION_PRICE_STARS  # 1⭐️ = 100 минимальных единиц XTR
CONSULTATION_PAYLOAD = "consultation_stars_100"
CONSULTATION_TITLE = "Личная консультация"
CONSULTATION_DESCRIPTION = (
    f"Оплата консультации с тарологом за {CONSULTATION_PRICE_STARS} звёзд Telegram. "
    "После успешной оплаты ты получишь ссылку на бот @helenatarotbot."
)
CONSULTATION_START_PARAMETER = "consultation"
CONSULTATION_SUCCESS_MESSAGE = (
    "✨ Благодарю за оплату! Чтобы продолжить, напиши в бот @helenatarotbot."
)

ADMIN_ID = 220493509  # это ты :)
single_card_usage: Dict[str, str] = {}  # {user_id: 'YYYY-MM-DD'}
_usage_lock = threading.Lock()


def _load_single_card_usage() -> None:
    """Загружает историю вытягивания карт из файла."""
    global single_card_usage

    if not os.path.exists(USAGE_STORAGE_PATH):
        single_card_usage = {}
        return

    try:
        with open(USAGE_STORAGE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"Не удалось загрузить историю вытягивания карт: {exc}",
            flush=True,
        )
        single_card_usage = {}
        return

    if isinstance(data, dict):
        single_card_usage = {
            str(user_id): date_str
            for user_id, date_str in data.items()
            if isinstance(date_str, str)
        }
    else:
        single_card_usage = {}


def _save_single_card_usage() -> None:
    """Сохраняет историю вытягивания карт в файл."""
    try:
        tmp_path = f"{USAGE_STORAGE_PATH}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(single_card_usage, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, USAGE_STORAGE_PATH)
    except OSError as exc:
        print(
            f"Не удалось сохранить историю вытягивания карт: {exc}",
            flush=True,
        )


_load_single_card_usage()

# Для режима с одной картой формируем «колоду», чтобы карты не повторялись,
# пока не будут вытянуты все 78.
_shuffled_single_card_deck: list[str] = []

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


def _normalize_two_card_key(card1: str, card2: str) -> str:
    """Возвращает ключ для двух карт в отсортированном виде."""

    return "|".join(sorted([card1.strip(), card2.strip()]))


def _normalize_two_card_combinations(raw_data) -> Dict[str, str]:
    """Приводит данные раскладов на две карты к словарю."""

    normalized: Dict[str, str] = {}

    if isinstance(raw_data, dict):
        for key, value in raw_data.items():
            if not isinstance(key, str):
                continue

            meaning = None
            if isinstance(value, str):
                meaning = value.strip()
            elif isinstance(value, dict):
                meaning_value = value.get("meaning")
                if isinstance(meaning_value, str):
                    meaning = meaning_value.strip()

            if not meaning:
                continue

            if "|" in key:
                parts = key.split("|", 1)
            elif "," in key:
                parts = key.split(",", 1)
            else:
                parts = key.split()

            if len(parts) != 2:
                continue

            normalized[_normalize_two_card_key(parts[0], parts[1])] = meaning

    elif isinstance(raw_data, list):
        for item in raw_data:
            if not isinstance(item, dict):
                continue

            cards = item.get("cards")
            meaning = item.get("meaning")

            if not isinstance(cards, (list, tuple)) or len(cards) != 2:
                card1 = item.get("card1")
                card2 = item.get("card2")
                cards = [card1, card2]

            if not isinstance(meaning, str):
                continue

            card1, card2 = cards
            if not isinstance(card1, str) or not isinstance(card2, str):
                continue

            normalized[_normalize_two_card_key(card1, card2)] = meaning.strip()

    return normalized


TWO_CARDS_URL = "https://raw.githubusercontent.com/nimixiss/tarot-webapp/main/two_card_combinations_full.json"
try:
    response = requests.get(TWO_CARDS_URL, timeout=15)
    response.raise_for_status()
    combinations_2cards_raw = response.json()
    combinations_2cards = _normalize_two_card_combinations(combinations_2cards_raw)
except requests.RequestException as exc:
    combinations_2cards = {}
    print(
        f"Не удалось загрузить комбинации для двух карт: {exc}",
        flush=True,
    )


def _get_two_card_meaning(card1: str, card2: str) -> str | None:
    """Возвращает толкование для пары карт, если оно известно."""

    if not (isinstance(card1, str) and isinstance(card2, str)):
        return None

    key = _normalize_two_card_key(card1, card2)
    meaning = combinations_2cards.get(key)
    if isinstance(meaning, str) and meaning.strip():
        return meaning

    return None


def _pick_random_card_meaning(card_name: str) -> str | None:
    """Возвращает случайное значение для отдельной карты."""

    data = tarot_deck.get(card_name)
    meanings = _collect_all_meanings(data)
    if meanings:
        return random.choice(meanings)

    return None


def _draw_general_two_card_fallback() -> tuple[str, str, str] | None:
    """Создаёт толкование по отдельным картам, если комбинаций нет."""

    deck_cards = [card for card in tarot_deck.keys() if isinstance(card, str)]
    if len(deck_cards) < 2:
        return None

    card1, card2 = random.sample(deck_cards, 2)
    meaning1 = _pick_random_card_meaning(card1)
    meaning2 = _pick_random_card_meaning(card2)

    parts = ["(Резервное толкование по отдельным картам)"]
    if meaning1:
        parts.append(f"• {card1}: {meaning1}")
    else:
        parts.append(f"• {card1}: значение не найдено.")

    if meaning2:
        parts.append(f"• {card2}: {meaning2}")
    else:
        parts.append(f"• {card2}: значение не найдено.")

    return card1, card2, "\n".join(parts)

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


def _build_consultation_keyboard() -> InlineKeyboardMarkup:
    """Кнопка с предложением личной консультации."""
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton(
            f"Получить консультацию за {CONSULTATION_PRICE_STARS}⭐️",
            callback_data="buy_consultation",
        )
    )
    return markup


def _send_consultation_offer(chat_id: int) -> None:
    """
    Отправляет предложение о личной консультации.

    Даже если STARS_PROVIDER_TOKEN пустой, для цифровых услуг оплата
    звёздами по доке Telegram разрешена, поэтому мы просто шлём инвойс
    с тем, что есть.
    """
    bot.send_message(
        chat_id,
        f"💫 Хочешь разобрать вопрос глубже? Доступна личная консультация "
        f"с тарологом за {CONSULTATION_PRICE_STARS} звёзд Telegram.",
        reply_markup=_build_consultation_keyboard(),
    )


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
    with _usage_lock:
        return single_card_usage.get(str(user_id)) == today


def _mark_single_card_used_today(user_id: int) -> None:
    today = datetime.utcnow().date().isoformat()
    with _usage_lock:
        single_card_usage[str(user_id)] = today
        _save_single_card_usage()


def _draw_random_card() -> str:
    """Возвращает случайную карту, гарантируя равномерный обход колоды."""
    global _shuffled_single_card_deck

    if not _shuffled_single_card_deck:
        _shuffled_single_card_deck = list(tarot_deck.keys())
        random.shuffle(_shuffled_single_card_deck)

    return _shuffled_single_card_deck.pop()


@bot.message_handler(func=lambda msg: msg.text == "🃏 Одна карта")
def ask_single_card_topic(message):
    user_id = message.from_user.id

    # Админ (ты) может пользоваться без ограничений
    if user_id != ADMIN_ID and _has_used_single_card_today(user_id):
        bot.send_message(
            message.chat.id,
            "✨ Вселенная уже ответила тебе сегодня. "
            "Приходи завтра, когда энергия обновится 🌙\n\n"
            f"Хочешь глубже разобрать вопрос? Можешь заказать личную "
            f"консультацию за {CONSULTATION_PRICE_STARS} звёзд Telegram.",
        )
        _send_consultation_offer(message.chat.id)
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
    card = _draw_random_card()
    _mark_single_card_used_today(user_id)
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

    # В первую очередь пытаемся взять расширенные описания из tarot_deck.
    card_data = tarot_deck.get(card)
    if isinstance(card_data, dict):
        expanded_values = card_data.get(category_key)
        if isinstance(expanded_values, list):
            meaning_list = [value for value in expanded_values if isinstance(value, str)]

    _send_single_card_reply(message.chat.id, card, topic, meaning)

    if user_id != ADMIN_ID:
        _send_consultation_offer(message.chat.id)


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


def _draw_random_two_card_combination():
    """Возвращает случайную комбинацию для расклада на две карты."""
    if not combinations_2cards:
        return None

    key = random.choice(list(combinations_2cards.keys()))
    cards = key.split("|", 1)
    if len(cards) != 2:
        return None

    card1, card2 = cards
    meaning = combinations_2cards.get(key)
    if not isinstance(meaning, str):
        return None

    return card1, card2, meaning


# === Оплата консультации звёздами ===

@bot.callback_query_handler(func=lambda call: call.data == "buy_consultation")
def handle_buy_consultation(call):
    prices = [
        LabeledPrice(
            label="Личная консультация",
            amount=CONSULTATION_PRICE_UNITS,
        )
    ]

    try:
        bot.send_invoice(
            call.message.chat.id,
            CONSULTATION_TITLE,
            CONSULTATION_DESCRIPTION,
            CONSULTATION_PAYLOAD,
            STARS_PROVIDER_TOKEN,  # может быть пустой строкой – это ок
            "XTR",
            prices,
            start_parameter=CONSULTATION_START_PARAMETER,
        )
    except ApiTelegramException as exc:
        bot.answer_callback_query(
            call.id,
            "Не удалось открыть оплату. Попробуй ещё раз чуть позже.",
            show_alert=True,
        )
        print(f"Ошибка отправки счёта: {exc}", flush=True)
        return

    bot.answer_callback_query(call.id)


@bot.pre_checkout_query_handler(func=lambda query: True)
def process_pre_checkout_query(pre_checkout_query):
    if pre_checkout_query.invoice_payload != CONSULTATION_PAYLOAD:
        bot.answer_pre_checkout_query(
            pre_checkout_query.id,
            ok=False,
            error_message="Не удалось обработать оплату. Попробуй позже.",
        )
        return

    bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@bot.message_handler(content_types=['successful_payment'])
def successful_payment_handler(message):
    payload = message.successful_payment.invoice_payload
    if payload != CONSULTATION_PAYLOAD:
        return

    payment = message.successful_payment
    if payment.currency != "XTR" or payment.total_amount != CONSULTATION_PRICE_UNITS:
        print(
            "Получена успешная оплата с некорректными параметрами: "
            f"currency={payment.currency}, amount={payment.total_amount}",
            flush=True,
        )
        return

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton(
            "Перейти к консультации",
            url=CONSULTATION_URL,
        )
    )

    bot.send_message(
        message.chat.id,
        CONSULTATION_SUCCESS_MESSAGE,
        reply_markup=markup,
    )


# === Три карты ===
@bot.message_handler(func=lambda msg: msg.text == "🔮 Три карты")
def send_three_cards(message):
    key = random.choice(list(combinations_3cards.keys()))
    selected_cards = key.split("|")
    meaning = combinations_3cards[key]
    names = "\n".join([f"• {card}" for card in selected_cards])
    bot.send_message(
        message.chat.id,
        f"🔮 *Три карты:*\n\n{names}\n\n{meaning}",
        parse_mode="Markdown",
    )


# === Обработка WebApp данных ===
@bot.message_handler(content_types=['web_app_data'])
def handle_web_app_data(message):
    try:
        data = json.loads(message.web_app_data.data)
        card1 = data.get("card1")
        card2 = data.get("card2")

        user_id = getattr(getattr(message, "from_user", None), "id", None)

        limit_flags = [
            "limit_exceeded",
            "limitExceeded",
            "daily_limit",
            "dailyLimit",
        ]
        limit_detected = any(bool(data.get(flag)) for flag in limit_flags)

        error_value = data.get("error")
        if isinstance(error_value, str) and "limit" in error_value.lower():
            limit_detected = True

        if not card1 or not card2:
            if user_id == ADMIN_ID:
                fallback = _draw_random_two_card_combination()
                if fallback:
                    card1, card2, meaning = fallback
                    response = (
                        "🧿 *Две карты:*\n\n"
                        f"• {card1}\n"
                        f"• {card2}\n\n"
                        f"{meaning}"
                    )
                    bot.send_message(message.chat.id, response, parse_mode="Markdown")
                    return

            if limit_detected:
                bot.send_message(
                    message.chat.id,
                    "✨ Сегодня лимит на расклад из двух карт уже исчерпан. "
                    "Попробуй снова завтра.",
                )
            else:
                bot.send_message(message.chat.id, "Ошибка: не удалось получить карты.")
            return

        meaning = _get_two_card_meaning(card1, card2)

        if meaning:
            _send_two_card_message(message.chat.id, card1, card2, meaning)
        else:
            if user_id == ADMIN_ID:
                fallback = _draw_random_two_card_combination()
                if fallback:
                    card1, card2, meaning = fallback
                    response = (
                        "🧿 *Две карты:*\n\n"
                        f"• {card1}\n"
                        f"• {card2}\n\n"
                        f"{meaning}"
                    )
                    bot.send_message(message.chat.id, response, parse_mode="Markdown")
                    return

            bot.send_message(message.chat.id, "❌ Ошибка: трактовка не найдена.")
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка обработки: {e}")


# === Запуск бота ===
if __name__ == "__main__":
    bot.polling(timeout=60, long_polling_timeout=30)
