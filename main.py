import os
import json
import asyncio
import gspread
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
    # Парсим JSON прямо здесь
    creds_dict = json.loads(G_CREDS_INFO)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)

# --- Логика авторизации ---

def find_user_by_id(user_id):
    try:
        client = get_sheets_client()
        sheet = client.open_by_key(SHEET_ID).worksheet("Users")
        cell = sheet.find(str(user_id), in_column=2) 
        return cell if cell else None
    except:
        return None

def authorize_by_phone(phone, user_id):
    try:
        client = get_sheets_client()
        sheet = client.open_by_key(SHEET_ID).worksheet("Users")
        clean_phone = phone.replace("+", "").strip()
        cell = sheet.find(clean_phone, in_column=1)
        if cell:
            # Получаем статус (4-я колонка)
            status = sheet.cell(cell.row, 4).value
            if status == 'blocked':
                return "blocked"
            # Записываем ID (2-я колонка)
            sheet.update_cell(cell.row, 2, str(user_id))
            return "success"
        return "not_found"
    except:
        return "error"

# --- Обработка команд ---

@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    if find_user_by_id(message.from_user.id):
        await message.answer("С возвращением! Продолжаем обучение.")
        await send_step(message.from_user.id, 1, 1)
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
        await send_step(user_id, 1, 1)
    elif result == "blocked":
        await message.answer("❌ Ваш доступ временно заблокирован.")
    else:
        await message.answer("🚫 Вашего номера нет в списке. Если это ошибка — напишите админу @kpp_all")

async def send_step(user_id, day, step):
    try:
        client = get_sheets_client()
        sheet = client.open_by_key(SHEET_ID).worksheet("Content")
        records = sheet.get_all_records()
        
        data = next((r for r in records if str(r['day']) == str(day) and str(r['step']) == str(step)), None)
        
        if not data:
            await bot.send_message(user_id, "На сегодня это всё! Увидимся завтра.")
            return

        await bot.copy_message(
            chat_id=user_id,
            from_chat_id=CHANNEL_ID,
            message_id=data['msg_id'],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Далее ➡️", callback_data=f"next:{day}:{int(step)+1}")]
            ])
        )
    except Exception as e:
        await bot.send_message(user_id, "Произошла ошибка при загрузке контента. Попробуйте позже.")

@dp.callback_query(F.data.startswith("next:"))
async def handle_next(callback: types.CallbackQuery):
    _, day, next_step = callback.data.split(":")
    await callback.answer()
    await send_step(callback.from_user.id, day, int(next_step))

# --- Вебхук для Vercel ---
@app.post("/api/webhook")
async def webhook(request: Request):
    update = types.Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}
