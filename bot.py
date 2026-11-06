import csv
import html
import io
import os
import telebot
import json
import random
import requests
import time
import threading
from collections import Counter
from typing import Dict
from datetime import datetime, timedelta, timezone
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
STATS_DIR = os.path.join(BASE_DIR, "stats")

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
    "После успешной оплаты ты получишь инструкцию, как продолжить."
)
CONSULTATION_START_PARAMETER = "consultation"
CONSULTATION_SUCCESS_MESSAGE = (
    "✨ Благодарю за оплату! Чтобы продолжить, напиши в бот @helenatarotbot."
)
CONSULTATION_MENU_LABEL = "💫 Расклад с тарологом за 100⭐️"
BACK_TO_MENU_LABEL = "⬅️ Назад"
CARD_OF_DAY_BUTTON_LABEL = "🗓️ Карта дня"

ADMIN_ID = 220493509  # это ты :)
READING_TYPE_SINGLE = "single"
READING_TYPE_TWO_CARDS = "two_cards"
READING_TYPE_THREE_CARDS = "three_cards"
READING_TYPE_YES_NO = "yes_no"

YES_NO_BUTTON_LABEL = "🎯 Ответ да/нет"
YES_NO_CALLBACK_DRAW = "yes_no_draw"
YES_NO_PROMPT_TEXT = (
    "🎯 Сконцентрируйся на своём вопросе, а когда будешь готова — нажми кнопку, "
    "чтобы получить ответ."
)

single_card_usage: Dict[str, Dict[str, str]] = {}
_usage_lock = threading.Lock()
_daily_stats: Dict[str, Dict[str, int]] = {}


DAILY_EVENT_START = "start"
DAILY_EVENT_SINGLE_CARD_BUTTON = "single_card_button"
DAILY_EVENT_SINGLE_CARD_READING = "single_card_reading"
DAILY_EVENT_TWO_CARDS_READING = "two_cards_reading"
DAILY_EVENT_THREE_CARDS_BUTTON = "three_cards_button"
DAILY_EVENT_THREE_CARDS_READING = "three_cards_reading"
DAILY_EVENT_YES_NO_BUTTON = "yes_no_button"
DAILY_EVENT_YES_NO_READING = "yes_no_reading"


DAILY_EVENT_LABELS = {
    DAILY_EVENT_START: "Команда /start",
    DAILY_EVENT_SINGLE_CARD_BUTTON: "Нажатия «Одна карта»",
    DAILY_EVENT_SINGLE_CARD_READING: "Расклады на одну карту",
    DAILY_EVENT_TWO_CARDS_READING: "Расклады на две карты",
    DAILY_EVENT_THREE_CARDS_BUTTON: "Нажатия «Три карты»",
    DAILY_EVENT_THREE_CARDS_READING: "Расклады на три карты",
    DAILY_EVENT_YES_NO_BUTTON: "Нажатия «Ответ да/нет»",
    DAILY_EVENT_YES_NO_READING: "Ответы да/нет",
}


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
        normalized: Dict[str, Dict[str, str]] = {}

        for user_id, value in data.items():
            str_user_id = str(user_id)

            if isinstance(value, dict):
                normalized[str_user_id] = {
                    str(key): str(date_str)
                    for key, date_str in value.items()
                    if isinstance(key, str) and isinstance(date_str, str)
                }
                continue

            if isinstance(value, str):
                normalized[str_user_id] = {READING_TYPE_SINGLE: value}

        single_card_usage = normalized
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


def _get_daily_stats_file_path(date_str: str) -> str:
    return os.path.join(STATS_DIR, f"{date_str}.json")


def _load_daily_stats_for_date(date_str: str) -> dict[str, int]:
    path = _get_daily_stats_file_path(date_str)

    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"Не удалось загрузить статистику за {date_str}: {exc}",
            flush=True,
        )
        return {}

    if not isinstance(raw_data, dict):
        return {}

    normalized: dict[str, int] = {}
    for key, value in raw_data.items():
        if isinstance(key, str) and isinstance(value, int):
            normalized[key] = value

    return normalized


def _save_daily_stats(date_str: str, data: dict[str, int]) -> None:
    path = _get_daily_stats_file_path(date_str)
    tmp_path = f"{path}.tmp"

    try:
        os.makedirs(STATS_DIR, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except OSError as exc:
        print(
            f"Не удалось сохранить статистику за {date_str}: {exc}",
            flush=True,
        )


def _initialize_daily_stats() -> None:
    try:
        os.makedirs(STATS_DIR, exist_ok=True)
    except OSError as exc:
        print(f"Не удалось создать директорию статистики: {exc}", flush=True)
        return

    today = datetime.now(timezone.utc).date().isoformat()
    with _usage_lock:
        _daily_stats[today] = _load_daily_stats_for_date(today)


def _increment_daily_event(event_name: str) -> None:
    today = datetime.now(timezone.utc).date().isoformat()

    with _usage_lock:
        stats = _daily_stats.get(today)
        if stats is None:
            stats = _load_daily_stats_for_date(today)
            _daily_stats[today] = stats

        stats[event_name] = stats.get(event_name, 0) + 1
        _save_daily_stats(today, stats)

        for stored_date in list(_daily_stats.keys()):
            if stored_date != today:
                _daily_stats.pop(stored_date, None)


def _format_event_label(event_name: str) -> str:
    return DAILY_EVENT_LABELS.get(event_name, event_name)


def _format_daily_stats(date_str: str, stats: dict[str, int]) -> str:
    if not stats:
        return f"За {date_str} пока нет записей."

    lines = [f"📊 Статистика за {date_str}:"]

    for event_name, count in sorted(stats.items()):
        lines.append(f"• {_format_event_label(event_name)}: {count}")

    return "\n".join(lines)


def _prepare_stats_csv() -> tuple[str, io.BytesIO, Counter[str]] | None:
    if not os.path.isdir(STATS_DIR):
        return None

    files = [
        entry
        for entry in os.listdir(STATS_DIR)
        if entry.endswith(".json") and os.path.isfile(os.path.join(STATS_DIR, entry))
    ]

    if not files:
        return None

    totals: Counter[str] = Counter()
    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow(["date", "event", "count"])
    has_rows = False

    for filename in sorted(files):
        date_part = filename[:-5]
        stats = _load_daily_stats_for_date(date_part)

        if not stats:
            continue

        for event_name, count in stats.items():
            writer.writerow([date_part, event_name, count])
            totals[event_name] += count
            has_rows = True

    if not has_rows:
        return None

    data = csv_buffer.getvalue().encode("utf-8")
    binary = io.BytesIO(data)
    filename = "stats_export.csv"
    binary.name = filename
    binary.seek(0)

    return filename, binary, totals


_initialize_daily_stats()

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
    _raw_three_card_data = json.load(f)

CARD_OF_DAY_FILE = "cardoftheday.json"
try:
    with open(CARD_OF_DAY_FILE, "r", encoding="utf-8") as f:
        raw_card_of_day_data = json.load(f)
    if isinstance(raw_card_of_day_data, dict):
        card_of_day_schedule: Dict[str, Dict[str, str]] = {}
        for date_key, payload in raw_card_of_day_data.items():
            if not isinstance(date_key, str):
                continue
            if not isinstance(payload, dict):
                continue
            card = payload.get("card")
            meaning = payload.get("meaning")
            if isinstance(card, str) and isinstance(meaning, str):
                card_of_day_schedule[date_key.strip()] = {
                    "card": card.strip(),
                    "meaning": meaning.strip(),
                }
    else:
        card_of_day_schedule = {}
except (OSError, json.JSONDecodeError) as exc:
    print(f"Не удалось загрузить карты дня: {exc}", flush=True)
    card_of_day_schedule = {}

YES_NO_FILE = "yesnot.json"
if os.path.exists(YES_NO_FILE):
    try:
        with open(YES_NO_FILE, "r", encoding="utf-8") as f:
            _yes_no_answers: Dict[str, str] = {
                str(name): str(value)
                for name, value in json.load(f).items()
                if isinstance(name, str) and isinstance(value, str)
            }
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Не удалось загрузить файл ответов да/нет: {exc}", flush=True)
        _yes_no_answers = {}
else:
    _yes_no_answers = {}


def _normalize_three_card_combinations(raw_data) -> tuple[Dict[str, dict[str, str]], list[tuple[str, str]]]:
    """Подготавливает расклады на три карты и формирует общий пул."""

    normalized: Dict[str, dict[str, str]] = {}
    fallback_pool: list[tuple[str, str]] = []

    if not isinstance(raw_data, dict):
        return normalized, fallback_pool

    for topic_key, topic_data in raw_data.items():
        if not (isinstance(topic_key, str) and isinstance(topic_data, dict)):
            continue

        topic_combinations: dict[str, str] = {}
        for combo_key, meaning in topic_data.items():
            if not (isinstance(combo_key, str) and isinstance(meaning, str)):
                continue

            cards = [part.strip() for part in combo_key.split("|") if isinstance(part, str) and part.strip()]
            if len(cards) != 3:
                continue

            normalized_key = "|".join(cards)
            clean_meaning = meaning.strip()
            topic_combinations[normalized_key] = clean_meaning
            fallback_pool.append((normalized_key, clean_meaning))

        if topic_combinations:
            normalized[topic_key] = topic_combinations

    return normalized, fallback_pool


_MAJOR_ARCANA_IMAGE_MAP = {
    "шут (0)": "fool",
    "маг (i)": "magician",
    "верховная жрица (ii)": "high_priestess",
    "императрица (iii)": "empress",
    "император (iv)": "emperor",
    "иерофант (v)": "hierophant",
    "влюбленные (vi)": "lovers",
    "колесница (vii)": "chariot",
    "сила (viii)": "strength",
    "отшельник (ix)": "hermit",
    "колесо фортуны (x)": "wheel_of_fortune",
    "справедливость (xi)": "justice",
    "повешенный (xii)": "hanged_man",
    "смерть (xiii)": "death",
    "умереность (xiv)": "temperance",
    "умеренность (xiv)": "temperance",
    "дьявол (xv)": "devil",
    "башня (xvi)": "tower",
    "звезда (xvii)": "star",
    "луна (xviii)": "moon",
    "солнце (xix)": "sun",
    "суд (xx)": "judgement",
    "мир (xxi)": "world",
}

_RANK_IMAGE_MAP = {
    "туз": "ace",
    "двойка": "two",
    "тройка": "three",
    "четверка": "four",
    "пятерка": "five",
    "шестерка": "six",
    "семерка": "seven",
    "восьмерка": "eight",
    "девятка": "nine",
    "десятка": "ten",
    "паж": "page",
    "рыцарь": "knight",
    "королева": "queen",
    "король": "king",
}

_SUIT_IMAGE_MAP = {
    "жезлов": "wands",
    "жезла": "wands",
    "жезлы": "wands",
    "кубков": "cups",
    "кубки": "cups",
    "кубка": "cups",
    "мечей": "swords",
    "мечи": "swords",
    "меча": "swords",
    "пентакли": "pentacles",
    "пентаклей": "pentacles",
    "пентакля": "pentacles",
}


def _normalize_card_key(name: str) -> str:
    return name.strip().lower().replace("ё", "е")


def _get_card_image_basename(card_name: str) -> str | None:
    normalized = _normalize_card_key(card_name)

    major = _MAJOR_ARCANA_IMAGE_MAP.get(normalized)
    if major:
        return major

    parts = normalized.split()
    if len(parts) < 2:
        return None

    rank = parts[0]
    suit = parts[-1]

    rank_en = _RANK_IMAGE_MAP.get(rank)
    suit_en = _SUIT_IMAGE_MAP.get(suit)

    if rank_en and suit_en:
        return f"{rank_en}_of_{suit_en}"

    return None


def _get_card_image_path(card_name: str) -> str | None:
    basename = _get_card_image_basename(card_name)
    if not basename:
        return None

    path = os.path.join(CARDS_FOLDER, f"{basename}.png")
    if os.path.exists(path):
        return path

    return None


def _draw_yes_no_answer() -> tuple[str, str] | None:
    if not _yes_no_answers:
        return None

    card = random.choice(list(_yes_no_answers.keys()))
    return card, _yes_no_answers[card]


def _build_yes_no_prompt_keyboard() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔁 Получить ответ", callback_data=YES_NO_CALLBACK_DRAW))
    return markup


def _build_yes_no_repeat_keyboard() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔁 Спросить ещё", callback_data=YES_NO_CALLBACK_DRAW))
    return markup


combinations_3cards_by_topic, _three_card_fallback_pool = _normalize_three_card_combinations(
    _raw_three_card_data
)


def _normalize_two_card_key(card1: str, card2: str) -> str:
    """Возвращает ключ для двух карт в отсортированном виде."""

    return "|".join(sorted([card1.strip(), card2.strip()]))


def _draw_three_card_reading(topic_key: str) -> tuple[list[str], str] | None:
    """Выбирает расклад из трёх карт по теме или из общего пула."""

    topic_combinations = combinations_3cards_by_topic.get(topic_key)

    if isinstance(topic_combinations, dict) and topic_combinations:
        entries = list(topic_combinations.items())
    else:
        entries = list(_three_card_fallback_pool)

    if not entries:
        return None

    combo_key, meaning = random.choice(entries)
    cards = [part.strip() for part in combo_key.split("|") if part.strip()]

    if len(cards) != 3:
        return None

    return cards, meaning


def _split_two_card_key(key: str) -> list[str]:
    """Разбивает ключ расклада на названия карт."""

    if "|" in key:
        parts = key.split("|")
    elif "," in key:
        parts = key.split(",")
    else:
        parts = key.split()

    return [part.strip() for part in parts if isinstance(part, str) and part.strip()]


def _extract_two_card_meaning(value) -> str | None:
    """Достаёт текст толкования из разных структур данных."""

    if isinstance(value, str):
        value = value.strip()
        return value or None

    if isinstance(value, dict):
        for key in ("meaning", "text", "description", "value"):
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested.strip()

    return None


def _normalize_two_card_combinations(raw_data) -> Dict[str, str]:
    """Приводит данные раскладов на две карты к словарю."""

    normalized: Dict[str, str] = {}

    def _add_pair(card1: str, card2: str, meaning: str) -> None:
        if not (isinstance(card1, str) and isinstance(card2, str) and isinstance(meaning, str)):
            return

        card1 = card1.strip()
        card2 = card2.strip()
        meaning = meaning.strip()

        if not card1 or not card2 or not meaning:
            return

        normalized[_normalize_two_card_key(card1, card2)] = meaning

    def _process(obj) -> None:
        if isinstance(obj, dict):
            cards_field = obj.get("cards")
            meaning_field = _extract_two_card_meaning(obj)

            if isinstance(cards_field, (list, tuple)) and len(cards_field) >= 2 and meaning_field:
                cards = [
                    card for card in cards_field if isinstance(card, str) and card.strip()
                ]
                if len(cards) >= 2:
                    _add_pair(cards[0], cards[1], meaning_field)

            else:
                card1 = obj.get("card1")
                card2 = obj.get("card2")
                if isinstance(card1, str) and isinstance(card2, str) and meaning_field:
                    _add_pair(card1, card2, meaning_field)

            for key, value in obj.items():
                if isinstance(key, str):
                    parts = _split_two_card_key(key)
                    if len(parts) == 2:
                        meaning = _extract_two_card_meaning(value)
                        if meaning:
                            _add_pair(parts[0], parts[1], meaning)
                            continue

                _process(value)

        elif isinstance(obj, list):
            for item in obj:
                _process(item)

    _process(raw_data)

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
        KeyboardButton("🧿 Две карты", web_app=WebAppInfo(url=WEBAPP_URL)),
    )
    markup.add(
        KeyboardButton("🔮 Три карты"),
        KeyboardButton(YES_NO_BUTTON_LABEL),
    )
    markup.add(KeyboardButton(CARD_OF_DAY_BUTTON_LABEL))
    markup.add(KeyboardButton(CONSULTATION_MENU_LABEL))
    return markup


_TOPIC_SELECTION_LAYOUT = (
    ("❤️ Любовь", "💼 Карьера"),
    ("💰 Финансы", "🧘‍♀️ Здоровье"),
    ("🧿 Совет дня",),
)


def _build_topic_selection_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура с выбором тематики расклада."""

    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    for row in _TOPIC_SELECTION_LAYOUT:
        buttons = [KeyboardButton(title) for title in row]
        markup.add(*buttons)
    markup.add(KeyboardButton(BACK_TO_MENU_LABEL))
    return markup


TOPIC_SELECTION_KEYBOARD = _build_topic_selection_keyboard()


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


def _get_card_of_day_for_date(date: datetime | None = None) -> tuple[str, str] | None:
    target_date = (date or datetime.now(timezone.utc).date()).isoformat()
    payload = card_of_day_schedule.get(target_date)

    if not isinstance(payload, dict):
        return None

    card = payload.get("card")
    meaning = payload.get("meaning")

    if isinstance(card, str) and isinstance(meaning, str):
        return card, meaning

    return None


def _send_card_of_day_message(chat_id: int, card: str, meaning: str) -> None:
    caption = (
        "🗓️ <b>Карта дня</b>\n"
        f"<b>{html.escape(card)}</b>\n\n"
        f"{html.escape(meaning)}"
    )

    path = _get_card_image_path(card)
    if path:
        with open(path, "rb") as photo:
            bot.send_photo(
                chat_id,
                photo,
                caption=caption,
                parse_mode="HTML",
                reply_markup=_build_main_menu(),
            )
        return

    bot.send_message(
        chat_id,
        caption,
        parse_mode="HTML",
        reply_markup=_build_main_menu(),
    )


_DAILY_LIMIT_MESSAGES = {
    READING_TYPE_SINGLE: (
        "✨ Вселенная уже ответила тебе сегодня. Приходи завтра, когда "
        "энергия обновится 🌙"
    ),
    READING_TYPE_YES_NO: (
        "✨ Сегодня лимит на ответ да/нет уже исчерпан. Приходи завтра, "
        "а если хочешь разобрать вопрос глубже — доступен расклад с "
        "тарологом за 100 звёзд 🌙"
    ),
    READING_TYPE_TWO_CARDS: (
        "✨ Сегодня лимит на расклад из двух карт уже исчерпан. Приходи "
        "завтра за новой энергией 🌙"
    ),
    READING_TYPE_THREE_CARDS: (
        "✨ Сегодня лимит на расклад из трёх карт уже исчерпан. Приходи "
        "завтра за новой энергией 🌙"
    ),
}


def _send_daily_limit_message(chat_id: int, reading_type: str) -> None:
    text = _DAILY_LIMIT_MESSAGES.get(reading_type)

    if text is None:
        text = (
            "✨ На сегодня лимит раскладов исчерпан. Попробуй снова завтра."
        )

    bot.send_message(chat_id, text)
    _send_consultation_offer(chat_id)


# === Главное меню ===
@bot.message_handler(commands=['start'])
def send_welcome(message):
    _increment_daily_event(DAILY_EVENT_START)
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if user_id is None:
        user_id = getattr(getattr(message, "chat", None), "id", None)
    _register_user_id(user_id)
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


def _has_used_reading_today(user_id: int, reading_type: str) -> bool:
    """Проверяет, делал ли пользователь расклад указанного типа сегодня."""
    today = datetime.now(timezone.utc).date().isoformat()
    with _usage_lock:
        return (
            single_card_usage.get(str(user_id), {}).get(reading_type) == today
        )


def _mark_reading_used_today(user_id: int, reading_type: str) -> None:
    """Помечает расклад указанного типа выполненным сегодня."""
    today = datetime.now(timezone.utc).date().isoformat()
    with _usage_lock:
        user_usage = single_card_usage.setdefault(str(user_id), {})
        user_usage[reading_type] = today
        _save_single_card_usage()


def _has_used_single_card_today(user_id: int) -> bool:
    """Проверяем, тянул ли пользователь карту сегодня."""
    return _has_used_reading_today(user_id, READING_TYPE_SINGLE)


def _mark_single_card_used_today(user_id: int) -> None:
    _mark_reading_used_today(user_id, READING_TYPE_SINGLE)


def _has_used_two_cards_today(user_id: int) -> bool:
    return _has_used_reading_today(user_id, READING_TYPE_TWO_CARDS)


def _mark_two_cards_used_today(user_id: int) -> None:
    _mark_reading_used_today(user_id, READING_TYPE_TWO_CARDS)


def _has_used_three_cards_today(user_id: int) -> bool:
    return _has_used_reading_today(user_id, READING_TYPE_THREE_CARDS)


def _mark_three_cards_used_today(user_id: int) -> None:
    _mark_reading_used_today(user_id, READING_TYPE_THREE_CARDS)


def _has_used_yes_no_today(user_id: int) -> bool:
    return _has_used_reading_today(user_id, READING_TYPE_YES_NO)


def _mark_yes_no_used_today(user_id: int) -> None:
    _mark_reading_used_today(user_id, READING_TYPE_YES_NO)


def _register_user_id(user_id: int | None) -> None:
    """Добавляет пользователя в хранилище, если его там ещё нет."""

    if user_id is None:
        return

    str_user_id = str(user_id)

    with _usage_lock:
        if str_user_id in single_card_usage:
            return

        single_card_usage[str_user_id] = {}
        _save_single_card_usage()


def _collect_known_user_ids() -> list[int]:
    """Возвращает список идентификаторов пользователей для рассылки."""

    with _usage_lock:
        user_ids: list[int] = []

        for stored_id in single_card_usage.keys():
            try:
                user_ids.append(int(stored_id))
            except (TypeError, ValueError):
                continue

    if ADMIN_ID not in user_ids:
        user_ids.append(ADMIN_ID)

    return user_ids


def _broadcast_message_to_all(text: str) -> tuple[int, int, int]:
    """Отправляет сообщение всем известным пользователям."""

    delivered = 0
    failed = 0
    recipients = _collect_known_user_ids()

    if not recipients:
        return 0, delivered, failed

    for chat_id in recipients:
        try:
            bot.send_message(chat_id, text)
            delivered += 1
        except ApiTelegramException as exc:
            failed += 1
            print(
                f"Не удалось отправить сообщение пользователю {chat_id}: {exc}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 - хотим залогировать любые ошибки
            failed += 1
            print(
                f"Не удалось отправить сообщение пользователю {chat_id}: {exc}",
                flush=True,
            )

        time.sleep(0.1)

    return len(recipients), delivered, failed


def _send_broadcast_summary(message, total: int, delivered: int, failed: int) -> None:
    if total == 0:
        bot.send_message(
            message.chat.id,
            "Пока некого уведомлять — список пользователей пуст.",
        )
        return

    lines = [
        "📣 Рассылка завершена.",
        f"Всего получателей: {total}",
        f"Успешно доставлено: {delivered}",
        f"С ошибкой: {failed}",
    ]

    bot.send_message(message.chat.id, "\n".join(lines))


def _perform_broadcast(message, text: str) -> None:
    total, delivered, failed = _broadcast_message_to_all(text)
    _send_broadcast_summary(message, total, delivered, failed)


def _draw_random_card() -> str:
    """Возвращает случайную карту, гарантируя равномерный обход колоды."""
    global _shuffled_single_card_deck

    if not _shuffled_single_card_deck:
        _shuffled_single_card_deck = list(tarot_deck.keys())
        random.shuffle(_shuffled_single_card_deck)

    return _shuffled_single_card_deck.pop()


@bot.message_handler(commands=["stats"])
def handle_stats_command(message):
    user = getattr(message, "from_user", None)
    user_id = getattr(user, "id", None)

    if user_id != ADMIN_ID:
        bot.reply_to(message, "Команда доступна только администратору.")
        return

    text = (message.text or "").strip()
    parts = text.split()
    today = datetime.now(timezone.utc).date()

    if len(parts) == 1:
        date_str = today.isoformat()
        stats = _load_daily_stats_for_date(date_str)
        bot.send_message(message.chat.id, _format_daily_stats(date_str, stats))
        return

    command_arg = parts[1].lower()

    if command_arg in ("today", "сегодня"):
        date_str = today.isoformat()
        stats = _load_daily_stats_for_date(date_str)
        bot.send_message(message.chat.id, _format_daily_stats(date_str, stats))
        return

    if command_arg in ("yesterday", "вчера"):
        date_str = (today - timedelta(days=1)).isoformat()
        stats = _load_daily_stats_for_date(date_str)
        bot.send_message(message.chat.id, _format_daily_stats(date_str, stats))
        return

    if command_arg in ("export", "csv", "выгрузка"):
        result = _prepare_stats_csv()
        if result is None:
            bot.send_message(message.chat.id, "Выгрузить нечего — нет файлов статистики.")
            return

        filename, buffer, totals = result
        summary_lines = [f"📈 {filename} готов."]

        if totals:
            summary_lines.append("")
            summary_lines.append("Итоги по всем дням:")
            for event_name, count in sorted(totals.items()):
                summary_lines.append(f"• {_format_event_label(event_name)}: {count}")

        caption = "\n".join(summary_lines)
        bot.send_document(
            message.chat.id,
            buffer,
            caption=caption,
        )
        return

    date_candidate = parts[1]
    try:
        requested_date = datetime.fromisoformat(date_candidate).date()
    except ValueError:
        bot.send_message(
            message.chat.id,
            "Не понял дату. Используй формат ГГГГ-ММ-ДД или команды export/today/yesterday.",
        )
        return

    date_str = requested_date.isoformat()
    stats = _load_daily_stats_for_date(date_str)
    bot.send_message(message.chat.id, _format_daily_stats(date_str, stats))


@bot.message_handler(commands=["broadcast"])
def handle_broadcast_command(message):
    user = getattr(message, "from_user", None)
    user_id = getattr(user, "id", None)

    if user_id != ADMIN_ID:
        bot.reply_to(message, "Команда доступна только администратору.")
        return

    _register_user_id(user_id)

    raw_text = (message.text or "").strip()
    parts = raw_text.split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""

    if payload:
        _perform_broadcast(message, payload)
        return

    prompt = bot.send_message(
        message.chat.id,
        "Отправь текст рассылки одним сообщением. Чтобы отменить, напиши /cancel.",
    )
    bot.register_next_step_handler(prompt, _handle_broadcast_text_step)


def _handle_broadcast_text_step(message):
    user = getattr(message, "from_user", None)
    user_id = getattr(user, "id", None)

    if user_id != ADMIN_ID:
        bot.reply_to(message, "Команда доступна только администратору.")
        return

    _register_user_id(user_id)

    text = (message.text or "").strip()

    if not text or text.lower() in {"/cancel", "cancel", "отмена"}:
        bot.send_message(message.chat.id, "Рассылка отменена.")
        return

    _perform_broadcast(message, text)


@bot.message_handler(func=lambda msg: msg.text == YES_NO_BUTTON_LABEL)
def prompt_yes_no_reading(message):
    _increment_daily_event(DAILY_EVENT_YES_NO_BUTTON)
    user = getattr(message, "from_user", None)
    user_id = getattr(user, "id", None)
    if user_id is None:
        user_id = getattr(getattr(message, "chat", None), "id", None)

    _register_user_id(user_id)

    if (
        user_id is not None
        and user_id != ADMIN_ID
        and _has_used_yes_no_today(user_id)
    ):
        _send_daily_limit_message(message.chat.id, READING_TYPE_YES_NO)
        return

    if not _yes_no_answers:
        bot.send_message(
            message.chat.id,
            "Сейчас ответы да/нет недоступны. Попробуй немного позже.",
            reply_markup=_build_main_menu(),
        )
        return

    bot.send_message(
        message.chat.id,
        YES_NO_PROMPT_TEXT,
        reply_markup=_build_yes_no_prompt_keyboard(),
    )


@bot.message_handler(func=lambda msg: msg.text == CARD_OF_DAY_BUTTON_LABEL)
def handle_card_of_day_button(message):
    user = getattr(message, "from_user", None)
    user_id = getattr(user, "id", None)
    if user_id is None:
        user_id = getattr(getattr(message, "chat", None), "id", None)

    _register_user_id(user_id)

    result = _get_card_of_day_for_date()

    if result is None:
        bot.send_message(
            message.chat.id,
            "✨ На сегодня карта дня ещё не готова. Загляни позже!",
            reply_markup=_build_main_menu(),
        )
        return

    card, meaning = result
    _send_card_of_day_message(message.chat.id, card, meaning)


@bot.message_handler(func=lambda msg: msg.text == "🃏 Одна карта")
def ask_single_card_topic(message):
    _increment_daily_event(DAILY_EVENT_SINGLE_CARD_BUTTON)
    user = getattr(message, "from_user", None)
    user_id = getattr(user, "id", None)
    if user_id is None:
        user_id = getattr(getattr(message, "chat", None), "id", None)

    _register_user_id(user_id)

    # Админ (ты) может пользоваться без ограничений
    if (
        user_id is not None
        and user_id != ADMIN_ID
        and _has_used_single_card_today(user_id)
    ):
        _send_daily_limit_message(message.chat.id, READING_TYPE_SINGLE)
        return

    msg = bot.send_message(
        message.chat.id,
        "Выбери сферу, о которой хочешь спросить:",
        reply_markup=_build_topic_selection_keyboard(),
    )
    bot.register_next_step_handler(msg, send_single_card_with_topic, user_id)


@bot.message_handler(func=lambda msg: msg.text == CONSULTATION_MENU_LABEL)
def show_consultation_offer(message):
    """Показывает предложение консультации из главного меню."""
    _send_consultation_offer(message.chat.id)


def send_single_card_with_topic(message, user_id: int | None):
    topic = message.text

    if topic == BACK_TO_MENU_LABEL:
        bot.send_message(
            message.chat.id,
            "Возвращаемся в главное меню 🌙",
            reply_markup=_build_main_menu(),
        )
        return

    if topic not in SINGLE_CARD_TOPICS:
        bot.send_message(
            message.chat.id,
            "Я жду выбор одной из сфер: любовь, карьера, финансы, здоровье или совет дня 💫",
        )
        return

    # Тянем карту
    card = _draw_random_card()
    if user_id is not None:
        _mark_single_card_used_today(user_id)
    _increment_daily_event(DAILY_EVENT_SINGLE_CARD_READING)
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

    if user_id is None or user_id != ADMIN_ID:
        _send_consultation_offer(message.chat.id)


def _send_single_card_reply(chat_id: int, card: str, topic: str, meaning: str) -> None:
    caption = (
        f"🃏 *{card}*\n"
        f"Сфера: {topic}\n"
        f"_{meaning}_"
    )

    path = _get_card_image_path(card)
    if path:
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


@bot.callback_query_handler(func=lambda call: getattr(call, "data", None) == YES_NO_CALLBACK_DRAW)
def handle_yes_no_callback(call):
    user = getattr(call, "from_user", None)
    user_id = getattr(user, "id", None)
    _register_user_id(user_id)

    if (
        user_id is not None
        and user_id != ADMIN_ID
        and _has_used_yes_no_today(user_id)
    ):
        bot.answer_callback_query(
            call.id,
            text="Сегодня лимит по ответу да/нет уже исчерпан.",
            show_alert=True,
        )
        message = getattr(call, "message", None)
        if message is not None:
            _send_daily_limit_message(message.chat.id, READING_TYPE_YES_NO)
        return

    result = _draw_yes_no_answer()

    if result is None:
        bot.answer_callback_query(
            call.id,
            text="Сейчас ответы недоступны. Попробуй позже.",
            show_alert=True,
        )
        message = getattr(call, "message", None)
        if message is not None:
            bot.send_message(
                message.chat.id,
                "Сейчас ответы да/нет недоступны. Попробуй немного позже.",
                reply_markup=_build_main_menu(),
            )
        return

    _increment_daily_event(DAILY_EVENT_YES_NO_READING)

    card, answer = result
    caption = f"🎯 *{card}*\nОтвет: *{answer}*"
    image_path = _get_card_image_path(card)
    message = getattr(call, "message", None)

    bot.answer_callback_query(call.id, text="✨ Ответ готов!")

    if message is None:
        return

    chat_id = message.chat.id
    reply_markup = _build_yes_no_repeat_keyboard()

    if image_path:
        with open(image_path, "rb") as photo:
            bot.send_photo(
                chat_id,
                photo,
                caption=caption,
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )
        if user_id is not None and user_id != ADMIN_ID:
            _mark_yes_no_used_today(user_id)
        return

    bot.send_message(
        chat_id,
        caption,
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )
    if user_id is not None and user_id != ADMIN_ID:
        _mark_yes_no_used_today(user_id)


def _send_two_card_message(
    chat_id: int, card1: str, card2: str, meaning: str, *, user_id: int | None = None
) -> None:
    response = (
        "🧿 *Две карты:*\n\n"
        f"• {card1}\n"
        f"• {card2}\n\n"
        f"{meaning}"
    )

    if user_id is not None:
        _mark_two_cards_used_today(user_id)

    _increment_daily_event(DAILY_EVENT_TWO_CARDS_READING)

    bot.send_message(
        chat_id,
        response,
        parse_mode="Markdown",
        reply_markup=_build_main_menu(),
    )

    if user_id is not None and user_id != ADMIN_ID:
        _send_consultation_offer(chat_id)


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
def ask_three_card_topic(message):
    _increment_daily_event(DAILY_EVENT_THREE_CARDS_BUTTON)
    user_id = getattr(getattr(message, "from_user", None), "id", None)

    _register_user_id(user_id)

    if (
        user_id is not None
        and user_id != ADMIN_ID
        and _has_used_three_cards_today(user_id)
    ):
        _send_daily_limit_message(message.chat.id, READING_TYPE_THREE_CARDS)
        return

    prompt = bot.send_message(
        message.chat.id,
        "Выбери сферу для расклада из трёх карт:",
        reply_markup=_build_topic_selection_keyboard(),
    )
    bot.register_next_step_handler(prompt, send_three_cards_with_topic)


def send_three_cards_with_topic(message):
    topic = message.text
    user_id = getattr(getattr(message, "from_user", None), "id", None)

    _register_user_id(user_id)

    if (
        user_id is not None
        and user_id != ADMIN_ID
        and _has_used_three_cards_today(user_id)
    ):
        _send_daily_limit_message(message.chat.id, READING_TYPE_THREE_CARDS)
        return

    if topic == BACK_TO_MENU_LABEL:
        bot.send_message(
            message.chat.id,
            "Возвращаемся в главное меню 🌙",
            reply_markup=_build_main_menu(),
        )
        return

    if topic == BACK_TO_MENU_LABEL:
        bot.send_message(
            message.chat.id,
            "Возвращаемся в главное меню 🌙",
            reply_markup=_build_main_menu(),
        )
        return

    if topic not in SINGLE_CARD_TOPICS:
        prompt = bot.send_message(
            message.chat.id,
            "Я жду выбор одной из сфер: любовь, карьера, финансы, здоровье или совет дня 💫",
            reply_markup=_build_topic_selection_keyboard(),
        )
        bot.register_next_step_handler(prompt, send_three_cards_with_topic)
        return

    topic_key = TOPIC_TO_KEY.get(topic)
    result = _draw_three_card_reading(topic_key) if topic_key else None

    if not result:
        bot.send_message(
            message.chat.id,
            "Не удалось подобрать расклад. Попробуй ещё раз чуть позже.",
            reply_markup=_build_main_menu(),
        )
        return

    cards, meaning = result
    if user_id is not None:
        _mark_three_cards_used_today(user_id)

    _increment_daily_event(DAILY_EVENT_THREE_CARDS_READING)

    names = "\n".join(f"• {card}" for card in cards)
    bot.send_message(
        message.chat.id,
        f"🔮 *Три карты — {topic}:*\n\n{names}\n\n{meaning}",
        parse_mode="Markdown",
        reply_markup=_build_main_menu(),
    )

    if user_id is not None and user_id != ADMIN_ID:
        _send_consultation_offer(message.chat.id)


# === Обработка WebApp данных ===
@bot.message_handler(content_types=['web_app_data'])
def handle_web_app_data(message):
    try:
        data = json.loads(message.web_app_data.data)
        card1 = data.get("card1")
        card2 = data.get("card2")

        user_id = getattr(getattr(message, "from_user", None), "id", None)

        _register_user_id(user_id)

        if (
            user_id is not None
            and user_id != ADMIN_ID
            and _has_used_two_cards_today(user_id)
        ):
            _send_daily_limit_message(message.chat.id, READING_TYPE_TWO_CARDS)
            return

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
                    fallback_card1, fallback_card2, fallback_meaning = fallback
                    _send_two_card_message(
                        message.chat.id,
                        fallback_card1,
                        fallback_card2,
                        fallback_meaning,
                        user_id=user_id,
                    )
                    return

            if limit_detected:
                _send_daily_limit_message(message.chat.id, READING_TYPE_TWO_CARDS)
            else:
                bot.send_message(message.chat.id, "Ошибка: не удалось получить карты.")
            return

        meaning = _get_two_card_meaning(card1, card2)

        if meaning:
            _send_two_card_message(
                message.chat.id,
                card1,
                card2,
                meaning,
                user_id=user_id,
            )
        else:
            if user_id == ADMIN_ID:
                fallback = _draw_random_two_card_combination()
                if fallback:
                    fallback_card1, fallback_card2, fallback_meaning = fallback
                    _send_two_card_message(
                        message.chat.id,
                        fallback_card1,
                        fallback_card2,
                        fallback_meaning,
                        user_id=user_id,
                    )
                    return

            bot.send_message(message.chat.id, "❌ Ошибка: трактовка не найдена.")
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка обработки: {e}")


# === Запуск бота ===
if __name__ == "__main__":
    # Telegram cannot use getUpdates while a webhook is configured.
    # Keep pending updates so users do not lose unread messages.
    if not bot.remove_webhook():
        raise RuntimeError("Failed to remove Telegram webhook before polling")
    print("Telegram webhook removed; starting polling", flush=True)
    bot.polling(timeout=60, long_polling_timeout=30)
