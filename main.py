import os
import json
import asyncio
import gspread
from datetime import datetime
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from google.oauth2.service_account import Credentials

# --- Инициализация ---
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
SHEET_ID = os.getenv("SHEET_ID")
G_CREDS_INFO = os.getenv("G_CREDS")

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = FastAPI()

def get_sheets_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds_dict = json.loads(G_CREDS_INFO)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)

# --- Логика таблицы ---

def get_user_data(user_id):
    """Возвращает полные данные пользователя: строку, статус, текущий день и дату последнего действия."""
    try:
        client = get_sheets_client()
        sheet = client.open_by_key(SHEET_ID).worksheet("Users")
        cell = sheet.find(str(user_id), in_column=2)
        if cell:
            row_data = sheet.row_values(cell.row)
            # Дозаполняем, если ячейки пустые
            while len(row_data) < 6:
                row_data.append("")
            return {
                "row": cell.row, 
                "status": row_data[3], 
                "current_day": row_data[4],
                "last_action": row_data[5],
                "sheet": sheet
            }
        return None
    except Exception as e:
        print(f"Ошибка получения данных: {e}")
        return None

def authorize_by_phone(phone, user_id):
    try:
        client = get_sheets_client()
        sheet = client.open_by_key(SHEET_ID).worksheet("Users")
        clean_phone = phone.replace("+", "").strip()
        cell = sheet.find(clean_phone, in_column=1)
        if cell:
            status = sheet.cell(cell.row, 4).value
            if status == 'blocked':
                return "blocked"
            sheet.update_cell(cell.row, 2, str(user_id))
            return "success"
        return "not_found"
    except Exception as e:
        print(f"Ошибка авторизации: {e}")
        return "error"

# --- Обработка команд ---

@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    user_info = get_user_data(message.from_user.id)
    
    if user_info:
        if user_info["status"] == "blocked":
            await message.answer("❌ Ваш доступ к материалам курса заблокирован.")
            return
        
        day = int(user_info["current_day"]) if user_info["current_day"] else 1
        await message.answer(f"С возвращением! Продолжаем обучение (День {day}).")
        await send_step(message.from_user.id, day, 1)
    else:
        btn = [[KeyboardButton(text="📱 Подтвердить номер", request_contact=True)]]
        markup = ReplyKeyboardMarkup(keyboard=btn, resize_keyboard=True, one_time_keyboard=True)
        await message.answer(
            "Привет! Для доступа к курсу нужно подтвердить номер телефона, который вы указывали при оплате.",
            reply_markup=markup
        )

@dp.message(F.contact)
async def handle_contact(message: types.Message):
    user_id = message.from_user.id
    phone = message.contact.phone_number
    result = authorize_by_phone(phone, user_id)
    
    if result == "success":
        await message.answer("✅ Доступ подтвержден! Начинаем.", reply_markup=ReplyKeyboardRemove())
        await send_step(user_id, 0, 1)
    elif result == "blocked":
        await message.answer("❌ Ваш доступ заблокирован.")
    else:
        await message.answer("🚫 Вашего номера нет в списке. Если это ошибка — напишите админу @kpp_all")

async def send_step(user_id, day, step):
    try:
        user_info = get_user_data(user_id)
        if not user_info or user_info["status"] == "blocked":
            await bot.send_message(user_id, "❌ Доступ ограничен.")
            return

        client = get_sheets_client()
        content_sheet = client.open_by_key(SHEET_ID).worksheet("Content")
        records = content_sheet.get_all_records()
        
        data = next((r for r in records if str(r['day']) == str(day) and str(r['step']) == str(step)), None)
        
        if data:
            # Отправка контента с ЗАЩИТОЙ от пересылки
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=CHANNEL_ID,
                message_id=data['msg_id'],
                protect_content=True, # Ученик не сможет переслать или сохранить
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Далее ➡️", callback_data=f"next:{day}:{int(step)+1}")]
                ])
            )
            # Запись текущего дня
            user_info["sheet"].update_cell(user_info["row"], 5, str(day))
        else:
            # Логика перехода на следующий день с проверкой даты
            next_day_exists = any(r for r in records if str(r['day']) == str(int(day) + 1))
            
            if next_day_exists:
                today_str = datetime.now().strftime("%Y-%m-%d")
                if user_info["last_action"] == today_str:
                    await bot.send_message(user_id, "🌟 На сегодня это всё! Следующий блок откроется завтра.")
                else:
                    await bot.send_message(
                        user_id, 
                        f"🏁 День {day} пройден! Готовы начать следующий блок?",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text=f"Начать День {int(day)+1} 🚀", callback_data=f"next:{int(day)+1}:1")]
                        ])
                    )
                    # Фиксируем дату завершения дня в колонку F
                    user_info["sheet"].update_cell(user_info["row"], 6, today_str)
            else:
                await bot.send_message(user_id, "🎉 Поздравляем! Вы полностью завершили курс.")
        
    except Exception as e:
        print(f"Ошибка в send_step: {e}")
        await bot.send_message(user_id, "Ошибка при загрузке. Попробуйте позже.")

@dp.callback_query(F.data.startswith("next:"))
async def handle_next(callback: types.CallbackQuery):
    _, day, next_step = callback.data.split(":")
    await callback.answer()
    await send_step(callback.from_user.id, day, int(next_step))

# --- Вебхук ---
@app.post("/api/webhook")
async def webhook(request: Request):
    update = types.Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}
