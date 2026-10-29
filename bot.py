import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
FAQ_PATH = BASE_DIR / "faq.json"
DB_PATH = BASE_DIR / "operator_messages.sqlite3"

router = Router()


class OperatorRequest(StatesGroup):
    waiting_for_message = State()


def load_faq() -> list[dict[str, Any]]:
    """Load FAQ records from faq.json.

    Expected item format:
    {
        "id": "price",
        "button": "Стоимость",
        "keywords": ["цена", "стоимость"],
        "answer": "..."
    }
    """
    with FAQ_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError("faq.json must contain a JSON array")

    for item in data:
        if not isinstance(item, dict):
            raise ValueError("Each FAQ item must be an object")
        for field in ("id", "button", "keywords", "answer"):
            if field not in item:
                raise ValueError(f"FAQ item is missing required field: {field}")
        if not isinstance(item["keywords"], list):
            raise ValueError("FAQ field 'keywords' must be an array")

    return data


FAQ_ITEMS = load_faq()
FAQ_BY_ID = {str(item["id"]): item for item in FAQ_ITEMS}


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS operator_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL,
                username TEXT,
                message_text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.commit()


def save_operator_message(message: Message) -> None:
    if message.from_user is None or message.text is None:
        return

    created_at = datetime.now(timezone.utc).isoformat()
    username = message.from_user.username

    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            INSERT INTO operator_messages (
                telegram_user_id,
                username,
                message_text,
                created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (message.from_user.id, username, message.text, created_at),
        )
        connection.commit()


def build_main_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=item["button"], callback_data=f"faq:{item['id']}")]
        for item in FAQ_ITEMS
    ]
    buttons.append([InlineKeyboardButton(text="Связаться с оператором", callback_data="operator")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def find_faq_answer(text: str) -> str | None:
    normalized_text = text.casefold()

    for item in FAQ_ITEMS:
        for keyword in item["keywords"]:
            if str(keyword).casefold() in normalized_text:
                return str(item["answer"])

    return None


@router.message(CommandStart())
async def handle_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Привет! Я учебный FAQ-бот. Выберите вопрос из списка или напишите свой вопрос текстом.",
        reply_markup=build_main_keyboard(),
    )


@router.callback_query(F.data.startswith("faq:"))
async def handle_faq_callback(callback: CallbackQuery) -> None:
    faq_id = str(callback.data).removeprefix("faq:")
    faq_item = FAQ_BY_ID.get(faq_id)

    if faq_item is None:
        await callback.answer("Вопрос не найден", show_alert=True)
        return

    if callback.message is not None:
        await callback.message.answer(str(faq_item["answer"]), reply_markup=build_main_keyboard())

    await callback.answer()


@router.callback_query(F.data == "operator")
async def handle_operator_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(OperatorRequest.waiting_for_message)

    if callback.message is not None:
        await callback.message.answer("Напишите сообщение для оператора. Я сохраню его и передам команде.")

    await callback.answer()


@router.message(OperatorRequest.waiting_for_message, F.text)
async def handle_operator_message(message: Message, state: FSMContext) -> None:
    save_operator_message(message)
    await state.clear()
    await message.answer(
        "Спасибо! Ваше сообщение сохранено. Оператор свяжется с вами при первой возможности.",
        reply_markup=build_main_keyboard(),
    )


@router.message(F.text)
async def handle_text(message: Message) -> None:
    if message.text is None:
        return

    answer = find_faq_answer(message.text)

    if answer is not None:
        await message.answer(answer, reply_markup=build_main_keyboard())
        return

    await message.answer(
        "Я не нашёл точный ответ. Выберите вопрос кнопкой ниже или свяжитесь с оператором.",
        reply_markup=build_main_keyboard(),
    )


async def main() -> None:
    load_dotenv(BASE_DIR / ".env")
    token = os.getenv("BOT_TOKEN")

    if not token:
        raise RuntimeError("BOT_TOKEN is not set in .env")

    init_db()

    bot = Bot(token=token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())