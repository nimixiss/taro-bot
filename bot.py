import os
import telebot
import json
import random
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo

# === Настройки ===
TOKEN = "8340920027:AAEjQWkTemEkikLbDT2J9JDgXrSIvU8Pryk"
CARDS_FOLDER = "images"
WEBAPP_URL = "https://nimixiss.github.io/tarot-webapp/"

# === Загрузка данных ===
with open("tarot_cards.json", "r", encoding="utf-8") as f:
    tarot_deck = json.load(f)

with open("combinations.json", "r", encoding="utf-8") as f:
    combinations_3cards = json.load(f)

with open("webapp/two_card_combinations_full.json", "r", encoding="utf-8") as f:
    combinations_2cards = json.load(f)

bot = telebot.TeleBot(TOKEN)

# === Главное меню ===
@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(
        KeyboardButton("🃏 Одна карта"),
        KeyboardButton("🔮 Три карты")
    )
    markup.add(
        KeyboardButton("🧿 Две карты", web_app=WebAppInfo(url=WEBAPP_URL))
    )
    bot.send_message(message.chat.id, "🌙 Привет! Я Таро-бот. Выбери расклад:", reply_markup=markup)

# === Одна карта ===
@bot.message_handler(func=lambda msg: msg.text == "🃏 Одна карта")
def send_single_card(message):
    card = random.choice(list(tarot_deck.keys()))
    meaning = random.choice(tarot_deck[card])
    path = os.path.join(CARDS_FOLDER, f"{card}.png")
    if os.path.exists(path):
        with open(path, "rb") as photo:
            bot.send_photo(message.chat.id, photo, caption=f"🃏 *{card}*\n_{meaning}_", parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, f"🃏 *{card}*\n_{meaning}_", parse_mode="Markdown")

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
bot.polling(timeout=60, long_polling_timeout=30)
