#тест
from aiogram import Bot, Dispatcher, types
from google_calendar import delete_event_from_calendar, MOSCOW_CALENDAR_ID, KRASNODAR_CALENDAR_ID
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from aiogram.utils import executor
from aiogram.dispatcher import FSMContext
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher.filters.state import State, StatesGroup
import logging
import re
import locale
from datetime import datetime, timedelta
import json
import os
import asyncio

locale.setlocale(locale.LC_TIME, "ru_RU.UTF-8")

API_TOKEN = "7684865098:AAFDNDRPBEty31cypZBcthDi9LCKbB8o5rQ"
ADMIN_CHAT_ID = 5887875855

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

class BookingStates(StatesGroup):
    waiting_for_city = State()
    waiting_for_name = State()
    waiting_for_phone = State()
    waiting_for_date = State()
    waiting_for_slot = State()

class AdminStates(StatesGroup):
    choosing_city = State()
    choosing_date = State()
    entering_time = State()

available_slots = ["12:00", "14:00", "16:00"]
BOOKINGS_FILE = "bookings.json"

# Создание файла при запуске, если он отсутствует
if not os.path.exists(BOOKINGS_FILE):
    with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f, ensure_ascii=False, indent=2)

# Для хранения последних сообщений пользователей (даже без FSM)
last_messages = {}

@dp.callback_query_handler(lambda c: c.data.startswith("city:"), state=BookingStates.waiting_for_city)
async def process_city(callback_query: types.CallbackQuery, state: FSMContext):
    city_code = callback_query.data.split(":")[1]
    user_id = callback_query.from_user.id
    await callback_query.answer()

    # Проверка на существующую запись
    data = load_bookings()
    for key in data:
        for time, info in data[key].items():
            if info.get("user_id") == user_id:
                kb = InlineKeyboardMarkup(row_width=1)
                kb.add(
                    InlineKeyboardButton("✅ Перезаписаться", callback_data="resign:yes"),
                    InlineKeyboardButton("🚫 Оставить как есть", callback_data="resign:no"),
                    InlineKeyboardButton("➕ Новая запись", callback_data=f"resign:new:{city_code}")
                )
                await send_clean_message(state, callback_query.message.chat.id, "📌 У вас уже есть запись. Что сделать?", reply_markup=kb)
                await state.finish()
                return

    # Если записи нет — идём дальше
    await state.update_data(city=city_code)
    msg = await callback_query.message.edit_text("Как вас зовут? (только буквы)")
    await state.update_data(last_bot_msg_id=msg.message_id)
    await BookingStates.waiting_for_name.set()

# ========== СЛОТЫ И ДАТЫ ==========
def load_bookings():
    if os.path.exists(BOOKINGS_FILE):
        with open(BOOKINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_booking(city, date_str, time, name, phone, user_id, event_id=None):
    if len(time) == 2:
        time += ":00"
    data = load_bookings()
    key = f"{date_str}_{city}"
    if key not in data:
        data[key] = {}
    data[key][time] = {
        "name": name,
        "phone": phone,
        "user_id": user_id,
        "event_id": event_id
    }
    with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_booked_slots(city, date_str):
    data = load_bookings()
    key = f"{date_str}_{city}"
    day_bookings = data.get(key, {})

    # Фильтруем только реальные записи (а не заготовки от админа)
    return [
        time for time, info in day_bookings.items()
        if info.get("user_id") != "admin_add"
    ]

def generate_ics_file(name, slot_time, city):
    if len(slot_time) == 2:
        slot_time += ":00"
    now = datetime.now()
    date = now.date()
    dt_start = datetime.strptime(f"{date} {slot_time}", "%Y-%m-%d %H:%M")
    dt_end = dt_start + timedelta(hours=1)
    filename = f"calendar_{name}.ics"
    ics = f"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:Демонстрация оборудования
DTSTART;TZID=Europe/Moscow:{dt_start.strftime('%Y%m%dT%H%M%S')}
DTEND;TZID=Europe/Moscow:{dt_end.strftime('%Y%m%dT%H%M%S')}
LOCATION:{'Москва' if city == 'msk' else 'Краснодар'}
DESCRIPTION:Вы записаны на демонстрацию от СНК Лазер
END:VEVENT
END:VCALENDAR"""
    with open(filename, "w", encoding="utf-8") as f:
        f.write(ics)
    return filename

def get_week_dates(offset=0, city=None):
    today = datetime.today().date()
    start = today + timedelta(weeks=offset)
    dates = []
    all_days = [start + timedelta(days=i) for i in range(7)]

    data = load_bookings()
    available_days = set()

    if city:
        for d in all_days:
            key = f"{d.strftime('%Y-%m-%d')}_{city}"
            if key in data and data[key]:  # есть хотя бы один слот
                available_days.add(d)

    for d in all_days:
        if d in available_days or d.weekday() < 5:
            dates.append(d)

    return dates

async def show_date_keyboard(message_or_callback, state: FSMContext, offset=0):
    data = await state.get_data()
    city = data.get("city")
    dates = get_week_dates(offset, city=city)
    kb = InlineKeyboardMarkup(row_width=2)
    for d in dates:
        label = d.strftime("%d.%m")
        callback_data = f"date:{d.strftime('%Y-%m-%d')}"
        kb.insert(InlineKeyboardButton(label, callback_data=callback_data))

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ Предыдущая", callback_data="date_nav:prev"))
    nav.append(InlineKeyboardButton("➡️ Следующая", callback_data="date_nav:next"))
    kb.row(*nav)

    await state.update_data(week_offset=offset)

    data = await state.get_data()
    msg_id = data.get("last_bot_msg_id")

    try:
        if isinstance(message_or_callback, types.CallbackQuery):
            await message_or_callback.message.edit_text("📅 Выберите дату:", reply_markup=kb)
        elif isinstance(message_or_callback, types.Message) and msg_id:
            await bot.edit_message_text(
                chat_id=message_or_callback.chat.id,
                message_id=msg_id,
                text="📅 Выберите дату:",
                reply_markup=kb
            )
        else:
            await message_or_callback.answer("📅 Выберите дату:", reply_markup=kb)
    except Exception as e:
        logging.warning(f"[show_date_keyboard] редактирование не удалось: {e}")
        await send_clean_message(state, message_or_callback.chat.id, "📅 Выберите дату:", reply_markup=kb
        )
    except Exception as e:
        logging.error(f"Ошибка при отображении клавиатуры дат: {e}")

async def send_clean_message(target, chat_id: int = None, text: str = None, reply_markup=None):
    if isinstance(target, FSMContext):
        state = target
        data = await state.get_data()
        msg_id = data.get("last_bot_msg_id")
        chat_id = data.get("chat_id") if "chat_id" in data else chat_id
    else:
        state = None
        msg_id = last_messages.get(target)
        chat_id = target

    # Удаляем предыдущее сообщение
    if msg_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            logging.info(f"[send_clean_message] Удалено сообщение {msg_id}")
        except Exception as e:
            logging.warning(f"[send_clean_message] Не удалось удалить {msg_id}: {e}")

    # Отправляем новое сообщение
    msg = await bot.send_message(chat_id, text, reply_markup=reply_markup)

    # Сохраняем ID последнего сообщения в FSM и глобально
    if state:
        await state.update_data(last_bot_msg_id=msg.message_id)
    last_messages[chat_id] = msg.message_id  # ✅ Всегда

    logging.info(f"[send_clean_message] Отправлено новое сообщение {msg.message_id}")

@dp.callback_query_handler(lambda c: c.data == "admin:panel", state="*")
async def admin_panel(callback_query: types.CallbackQuery, state: FSMContext):
    current_state = await state.get_state()

    if current_state is not None:
        kb = InlineKeyboardMarkup().add(
            InlineKeyboardButton("↩ Вернуться в начало", callback_data="admin:go_start")
        )
        await callback_query.message.edit_text(
            "❗ Админка доступна только в начале. Завершите текущую запись или нажмите /start.",
            reply_markup=kb
        )
        return

    # ✅ Если всё ок — показываем панель
    await callback_query.answer()
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📋 Записи по датам", callback_data="admin:view_dates"),
        InlineKeyboardButton("🧾 Удалить по одной", callback_data="admin:delete_select"),
        InlineKeyboardButton("🗑 Очистить Москву", callback_data="admin:clear_msk"),
        InlineKeyboardButton("🗑 Очистить Краснодар", callback_data="admin:clear_kras"),
        InlineKeyboardButton("➕ Добавить слот", callback_data="admin:add_slot"),
        InlineKeyboardButton("⬅️ Назад", callback_data="back:to_start")
    )
    await callback_query.message.edit_text("🛠 Админ-панель", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data in ["admin:clear_msk", "admin:clear_kras"])
async def confirm_city_clear(callback_query: types.CallbackQuery):
    city = "Москва" if "msk" in callback_query.data else "Краснодар"
    code = "msk" if "msk" in callback_query.data else "kras"

    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("❌ Подтвердить удаление", callback_data=f"admin:clear_city:{code}"),
        InlineKeyboardButton("⬅️ Отмена", callback_data="admin:panel")
    )
    await callback_query.message.edit_text(f"⚠️ Удалить все записи в городе {city}?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("admin:clear_city:"))
async def clear_city_bookings(callback_query: types.CallbackQuery, state: FSMContext):
    city_code = callback_query.data.split(":")[2]
    await callback_query.answer()

    data = load_bookings()
    keys_to_delete = [k for k in data if k.endswith(f"_{city_code}")]

    from google_calendar import delete_event_from_calendar, MOSCOW_CALENDAR_ID, KRASNODAR_CALENDAR_ID

    for k in keys_to_delete:
        day_data = data.get(k, {})
        for time, info in list(day_data.items()):
            event_id = info.get("event_id")
            if event_id:
                calendar_id = MOSCOW_CALENDAR_ID if city_code == "msk" else KRASNODAR_CALENDAR_ID
                try:
                    delete_event_from_calendar(calendar_id, event_id)
                    logging.info(f"[google] Удалено событие {event_id} из {calendar_id}")
                except Exception as e:
                    logging.warning(f"[google] Не удалось удалить {event_id}: {e}")
        del data[k]  # удаляем запись из словаря

    with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    await callback_query.message.edit_text(f"🗑 Все записи в городе {'Москва' if city_code == 'msk' else 'Краснодар'} удалены.")
    await asyncio.sleep(2)
    await admin_panel(callback_query, state)

@dp.callback_query_handler(lambda c: c.data == "back:to_start")
async def back_to_start(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.finish()

    # Удалим последнее сообщение, даже если FSM нет
    chat_id = callback_query.message.chat.id
    last_msg_id = last_messages.get(chat_id)
    if last_msg_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=last_msg_id)
        except Exception as e:
            logging.warning(f"[back_to_start] Не удалось удалить сообщение: {e}")

    is_admin = callback_query.from_user.id == ADMIN_CHAT_ID
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Да", callback_data="begin:yes"),
        InlineKeyboardButton("❌ Нет", callback_data="begin:no"),
        InlineKeyboardButton("📣 Связаться с нами", callback_data="show:contacts")
    )
    if is_admin:
        kb.insert(InlineKeyboardButton("⚙️ Админка", callback_data="admin:panel"))

    await send_clean_message(state, chat_id, "👋 Хотите записаться на демонстрацию?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "admin:view_dates")
async def admin_view_dates(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    data = load_bookings()

    if not data:
        await callback_query.message.edit_text("📋 Пока нет ни одной записи.\n\n🔙 Возвращаюсь в админку...")
        await asyncio.sleep(2)
        await admin_panel(callback_query, state)
        return

    lines = ["📅 Записи по датам:"]
    for key in sorted(data.keys()):
        date_str, city_code = key.split("_")
        try:
            formatted = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m")
        except:
            formatted = date_str
        city = "Москва" if city_code == "msk" else "Краснодар"

        # ✅ Считаем только реальные записи
        real_bookings = [
            t for t, info in data[key].items()
            if info and info.get("user_id") and info.get("user_id") != "admin_add"
        ]
        count = len(real_bookings)

        lines.append(f"• {formatted} — {city}: {count} чел.")

    lines.append("\n⬅️ Нажмите /start для возврата")
    await callback_query.message.edit_text("\n".join(lines))

@dp.callback_query_handler(lambda c: c.data == "admin:clear_confirm")
async def admin_clear_confirm(callback_query: types.CallbackQuery):
    await callback_query.answer()
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("❌ Подтвердить удаление", callback_data="admin:clear_all"),
        InlineKeyboardButton("⬅️ Отмена", callback_data="admin:panel")
    )
    await callback_query.message.edit_text("⚠️ Вы уверены, что хотите удалить все записи?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "admin:clear_all")
async def admin_clear_all(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()

    if os.path.exists(BOOKINGS_FILE):
        os.remove(BOOKINGS_FILE)

    await callback_query.message.edit_text("🗑 Все записи удалены.\n\n🔙 Возвращаюсь в админку...")
    await asyncio.sleep(2)
    await admin_panel(callback_query, state)

@dp.callback_query_handler(lambda c: c.data == "admin:delete_select")
async def admin_delete_select(callback_query: types.CallbackQuery):
    await callback_query.answer()
    data = load_bookings()

    if not data:
        await callback_query.message.edit_text("📋 Нет записей для удаления.\n\n🔙 Возвращаюсь в админку...")
        await asyncio.sleep(2)
        state = dp.current_state(chat=callback_query.message.chat.id, user=callback_query.from_user.id)
        await admin_panel(callback_query, state)
        return

    kb = InlineKeyboardMarkup(row_width=1)
    for key in sorted(data.keys()):
        date_str, city = key.split("_")
        try:
            formatted_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m")
        except:
            formatted_date = date_str

        label = f"{formatted_date} — {'Москва' if city == 'msk' else 'Краснодар'}"
        kb.add(InlineKeyboardButton(label, callback_data=f"admin:deldate:{key}"))

    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="admin:panel"))
    await callback_query.message.edit_text("📅 Выберите дату для удаления записей:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("admin:deldate:"))
async def admin_choose_record(callback_query: types.CallbackQuery):
    await callback_query.answer()
    key = callback_query.data.split(":", 2)[2]  # формат YYYY-MM-DD_msk

    data = load_bookings()
    day_data = data.get(key)

    if not day_data:
        await callback_query.message.edit_text("😕 На эту дату записей нет.\n\n🔙 Возвращаюсь в админку...")
        await asyncio.sleep(2)
        state = dp.current_state(chat=callback_query.message.chat.id, user=callback_query.from_user.id)
        await admin_panel(callback_query, state)
        return

    kb = InlineKeyboardMarkup(row_width=1)
    for time, info in day_data.items():
        label = f"{time} — {info['name']} ({info['phone']})"
        kb.add(InlineKeyboardButton(label, callback_data=f"admin:deluser:{key}:{time}"))

    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="admin:delete_select"))
    await callback_query.message.edit_text("👤 Выберите запись для удаления:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("admin:deluser:"))
async def admin_delete_user(callback_query: types.CallbackQuery):
    await callback_query.answer()
    _, _, key, time = callback_query.data.split(":", 3)

    data = load_bookings()
    day_data = data.get(key)
    city = key.split("_")[1]  # msk или kras

    if day_data and time in day_data:
        info = day_data[time]
        event_id = info.get("event_id")

        # Удаляем из Google Календаря
        if event_id:
            calendar_id = MOSCOW_CALENDAR_ID if city == "msk" else KRASNODAR_CALENDAR_ID
            try:
                delete_event_from_calendar(calendar_id, event_id)
                logging.info(f"✅ Событие {event_id} удалено из календаря {calendar_id}")
            except Exception as e:
                logging.warning(f"❌ Не удалось удалить событие из календаря: {e}")

        # Удаляем слот
        deleted = day_data.pop(time)

        # Удаляем ключ даты, если слоты закончились
        if not day_data:
            data.pop(key)

        # Сохраняем обновлённые данные
        with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # Если ещё остались записи на эту дату
        if key in data:
            kb = InlineKeyboardMarkup(row_width=1)
            for t, info in data[key].items():
                label = f"{t} — {info['name']} ({info['phone']})"
                kb.add(InlineKeyboardButton(label, callback_data=f"admin:deluser:{key}:{t}"))
            kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="admin:delete_select"))

            await callback_query.message.edit_text(
                f"🗑 Удалено:\n{time} — {deleted['name']} ({deleted['phone']})\n\n👤 Выберите следующую запись для удаления:",
                reply_markup=kb
            )
        else:
            await callback_query.message.edit_text(
                f"🗑 Удалено:\n{time} — {deleted['name']} ({deleted['phone']})\n\n📅 На этой дате больше нет записей. Возвращаюсь назад..."
            )
            await asyncio.sleep(2)
            await admin_delete_select(callback_query)
    else:
        await callback_query.message.edit_text("😕 Запись не найдена.")

@dp.callback_query_handler(lambda c: c.data == "show:contacts")
async def show_socials(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()

    text = "📣 Свяжитесь с нами или подпишитесь в соцсетях:"
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📷 Instagram", url="https://www.instagram.com/snk_laser/"),
        InlineKeyboardButton("🌐 Сайт", url="https://snklaser.ru"),
        InlineKeyboardButton("📺 YouTube", url="https://www.youtube.com/@snklaser"),
        InlineKeyboardButton("📘 VK", url="https://vk.com/snk_laser"),
        InlineKeyboardButton("💬 Telegram", url="https://t.me/snk_laser"),
        InlineKeyboardButton("📞 WhatsApp", url="https://wa.me/79951530030"),
        InlineKeyboardButton("⬅️ Назад", callback_data="back:to_start")
    )

    await send_clean_message(callback_query.message.chat.id, text=text, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("date_nav:"), state=BookingStates.waiting_for_date)
async def handle_date_nav(callback_query: types.CallbackQuery, state: FSMContext):
    direction = callback_query.data.split(":")[1]
    data = await state.get_data()
    logging.info(f"[show_socials] last_bot_msg_id = {data.get('last_bot_msg_id')}")
    offset = data.get("week_offset", 0)
    if direction == "next":
        offset += 1
    elif direction == "prev":
        offset = max(0, offset - 1)
    await show_date_keyboard(callback_query, state, offset)

@dp.callback_query_handler(lambda c: c.data.startswith("date:"), state=BookingStates.waiting_for_date)
async def process_date(callback_query: types.CallbackQuery, state: FSMContext):
    date_str = callback_query.data.split(":")[1]
    await state.update_data(date=date_str)

    data = await state.get_data()
    city = data.get("city")

    bookings = load_bookings()
    key = f"{date_str}_{city}"
    day_slots = bookings.get(key, {})

    free_slots = []

    # Добавляем стандартные слоты, если они не заняты
    for slot in available_slots:
        if slot not in day_slots:
            free_slots.append(slot)
        elif not day_slots[slot].get("user_id") or day_slots[slot].get("user_id") == "admin_add":
            free_slots.append(slot)

    # Добавляем вручную добавленные слоты, если они не заняты
    for slot, info in day_slots.items():
        if slot not in free_slots and (not info.get("user_id") or info.get("user_id") == "admin_add"):
            free_slots.append(slot)

    # Если свободных слотов нет — показываем ошибку и возвращаем выбор даты
    if not free_slots:
        await callback_query.answer("⛔ Нет доступных слотов на эту дату.")
        await send_clean_message(state, callback_query.message.chat.id, "😕 На эту дату нет свободных слотов. Попробуйте выбрать другую дату.")
        await BookingStates.waiting_for_date.set()
        await show_date_keyboard(callback_query, state)
        return

    # Показываем слоты
    kb = InlineKeyboardMarkup(row_width=2)
    for slot in sorted(free_slots):
        kb.insert(InlineKeyboardButton(slot, callback_data=f"slot:{slot}"))
    kb.row(InlineKeyboardButton("⬅️ Назад", callback_data="back:to_dates"))

    await callback_query.answer()
    await send_clean_message(state, callback_query.message.chat.id, "🕒 Выберите свободный слот:", reply_markup=kb)
    await BookingStates.waiting_for_slot.set()

@dp.callback_query_handler(lambda c: c.data == "back:to_dates", state=BookingStates.waiting_for_slot)
async def back_to_dates(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await BookingStates.waiting_for_date.set()
    await show_date_keyboard(callback_query, state)

@dp.callback_query_handler(lambda c: c.data.startswith("slot:"), state=BookingStates.waiting_for_slot)
async def process_slot(callback_query: types.CallbackQuery, state: FSMContext):
    slot_time = callback_query.data.split(":")[1]
    if len(slot_time) == 2:
        slot_time += ":00"

    data = await state.get_data()
    city_code = data.get("city")
    date_str = data.get("date")
    
    # Проверяем, не занят ли слот
    if slot_time in get_booked_slots(city_code, date_str):
        await callback_query.answer()
        await callback_query.message.answer("⛔ Этот слот уже занят. Пожалуйста, выберите другой.")
        return

    await callback_query.answer()
    await state.update_data(slot=slot_time)

    # Сохраняем бронь
    name = data.get("name")
    phone = data.get("phone")
    user_id = callback_query.from_user.id
    save_booking(city_code, date_str, slot_time, name, phone, user_id)

    from google_calendar import add_event_to_calendar

    # Добавление в Google Календарь
    event_id = add_event_to_calendar(name, phone, city_code, date_str, slot_time)

    # Сохраняем бронь с event_id
    save_booking(city_code, date_str, slot_time, name, phone, user_id, event_id=event_id)

    formatted_time = datetime.strptime(slot_time, "%H:%M").strftime("%H:%M")
    date_text = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %d %B")

    msg_text = (
        f"📝 Новая запись:\n"
        f"👤 Имя: {name}\n"
        f"📞 Телефон: {phone}\n"
        f"🏙️ Город: {'Москва' if city_code == 'msk' else 'Краснодар'}\n"
        f"📅 Дата: {date_text}\n"
        f"🕒 Время: {formatted_time}"
    )

    # Отправка администратору — исправленный вызов
    await send_clean_message(ADMIN_CHAT_ID, text=msg_text)

    # Подтверждение пользователю
    await send_clean_message(state, callback_query.message.chat.id, "🎉 Вы успешно записались! Мы ждём вас на демонстрации.")

    # Предложение добавить в календарь
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📆 Добавить в календарь", callback_data="calendar:add"),
        InlineKeyboardButton("⛔ Пропустить", callback_data="calendar:skip")
    )
    await callback_query.message.answer("Хотите добавить событие в календарь?", reply_markup=kb)

    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "calendar:add")
async def send_calendar(callback_query: types.CallbackQuery, state: FSMContext):
    bookings = load_bookings()
    user_id = callback_query.from_user.id

    for key in bookings:
        for time, data in bookings[key].items():
            if data.get("user_id") == user_id:
                city = key.split("_")[1]
                filename = generate_ics_file(data["name"], time, city)

                await bot.send_document(user_id, InputFile(filename))
                os.remove(filename)

                # ⏳ Пауза 5 секунд
                await asyncio.sleep(5)

                # ✅ Подтверждение
                await send_clean_message(state, callback_query.message.chat.id, "🎉 Вы успешно записались! Мы ждём вас на демонстрации.")

                # ✉️ Дополнительное сообщение
                await callback_query.message.answer("Если бот понадобится снова — просто нажмите /start")

                # 🧹 Завершение FSM-сессии
                await state.finish()
                return

    await callback_query.message.answer("😕 Не удалось найти запись для календаря.")

@dp.callback_query_handler(lambda c: c.data == "calendar:skip")
async def skip_calendar(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await callback_query.message.edit_text("Хорошо, запись завершена. Если передумаете — просто нажмите /start 😊")

async def send_reminders():
    REMINDER_TIMES = {
        "1day": timedelta(days=1),
        "2hour": timedelta(hours=2)
    }

    while True:
        try:
            now = datetime.now()
            bookings = load_bookings()
            updated = False

            for key, slots in bookings.items():
                date_str, city = key.split("_")

                for time_str, info in slots.items():
                    # Пропускаем пустые или неправильные записи
                    if not info or not isinstance(info, dict):
                        continue

                    user_id = info.get("user_id")
                    name = info.get("name")
                    phone = info.get("phone")

                    if not user_id or user_id == "admin_add":
                        continue

                    # Проверка корректного времени
                    if len(time_str) == 2:
                        time_str += ":00"
                    try:
                        slot_time = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                    except ValueError:
                        logging.warning(f"[НАПОМИНАНИЕ] Некорректный формат времени: {time_str}")
                        continue

                    # Проверяем, отправлялись ли уже напоминания
                    reminded = info.get("reminded", {"1day": False, "2hour": False})

                    for label, delta in REMINDER_TIMES.items():
                        time_until = slot_time - now
                        if not reminded.get(label) and timedelta(0) <= time_until <= delta + timedelta(minutes=1):
                            try:
                                reminder_text = (
                                    f"🔔 Напоминание: демонстрация начнется в {slot_time.strftime('%H:%M')} "
                                    f"— уже через {'2 часа' if label == '2hour' else 'день'}!"
                                )

                                await send_clean_message(user_id, reminder_text)
                                reminded[label] = True
                                info["reminded"] = reminded
                                updated = True

                                logging.info(f"[НАПОМИНАНИЕ] {label} отправлено → {user_id} на {time_str}")
                            except Exception as e:
                                logging.warning(f"❌ Не удалось отправить напоминание {label} пользователю {user_id}: {e}")

            if updated:
                with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
                    json.dump(bookings, f, ensure_ascii=False, indent=2)

            await asyncio.sleep(60)

        except Exception as e:
            logging.error(f"Ошибка в send_reminders: {e}")
            await asyncio.sleep(30)

@dp.callback_query_handler(lambda c: c.data == "begin:no")
async def handle_no(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await callback_query.message.answer("Хорошо, если передумаете — просто нажмите /start")

@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()

    is_admin = message.from_user.id == ADMIN_CHAT_ID

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Да", callback_data="begin:yes"),
        InlineKeyboardButton("❌ Нет", callback_data="begin:no"),
        InlineKeyboardButton("📣 Связаться с нами", callback_data="show:contacts")
    )
    if is_admin:
        kb.insert(InlineKeyboardButton("⚙️ Админка", callback_data="admin:panel"))

    await send_clean_message(state, message.chat.id, "👋 Добро пожаловать! Хотите записаться на демонстрацию?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "begin:yes")
async def handle_yes(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("Москва", callback_data="city:msk"),
        InlineKeyboardButton("Краснодар", callback_data="city:kras")
    )
    await send_clean_message(state, callback_query.message.chat.id, "🏙️ Выберите город:", reply_markup=kb)
    await BookingStates.waiting_for_city.set()

@dp.message_handler(state=BookingStates.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if not re.fullmatch(r"[а-яА-ЯёЁa-zA-Z\- ]+", name):
        await send_clean_message(state, message.chat.id, "❗ Имя может содержать только буквы. Попробуйте снова:")
        return
    await state.update_data(name=name)
    await send_clean_message(state, message.chat.id, "📞 Введите номер телефона (начинается с 8 или +7, 10 цифр после)")
    await BookingStates.waiting_for_phone.set()

    try:
        await message.delete()
    except:
        pass

    data = await state.get_data()
    msg_id = data.get("last_bot_msg_id")

    try:
        msg = await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=msg_id,
            text="📞 Введите номер телефона (начинается с 8 или +7, 10 цифр после)"
        )
    except:
        await send_clean_message(state, message.chat.id, "📞 Введите номер телефона (начинается с 8 или +7, 10 цифр после)")

    await BookingStates.waiting_for_phone.set()

@dp.message_handler(state=BookingStates.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip().replace(" ", "")
    if not re.fullmatch(r"^(\+7|8)[0-9]{10}$", phone):
        await send_clean_message(state, message.chat.id, "❗ Неверный формат телефона. Попробуйте снова:")
        return

    await state.update_data(phone=phone)
    await state.update_data(week_offset=0)

    try:
        await message.delete()
    except:
        pass

    data = await state.get_data()
    msg_id = data.get("last_bot_msg_id")

    try:
        msg = await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=msg_id,
            text="📅 Выберите дату:"
        )
    except:
        await send_clean_message(state, message_or_callback.message.chat.id, "📅 Выберите дату:", reply_markup=kb)

    await state.update_data(last_bot_msg_id=msg.message_id)

    # ✅ Главное: всегда переход к следующему состоянию
    await BookingStates.waiting_for_phone.set()
    
    # Добавляем логирование текущего состояния
    logging.info(f"User {message.from_user.id} ввёл телефон: {phone}")
    
    # вызываем функцию показа клавиатуры дат с await
    await BookingStates.waiting_for_date.set()
    await show_date_keyboard(message, state, offset=0)

@dp.callback_query_handler(lambda c: c.data == "admin:add_slot")
async def admin_add_slot_start(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("Москва", callback_data="admin:slot_city:msk"),
        InlineKeyboardButton("Краснодар", callback_data="admin:slot_city:kras")
    )
    await send_clean_message(state, callback_query.message.chat.id, "🏙️ Выберите город для слота:", reply_markup=kb)
    await AdminStates.choosing_city.set()

@dp.callback_query_handler(lambda c: c.data.startswith("admin:slot_city:"), state=AdminStates.choosing_city)
async def admin_slot_choose_city(callback_query: types.CallbackQuery, state: FSMContext):
    city = callback_query.data.split(":")[2]
    await state.update_data(slot_city=city, week_offset=0)
    await AdminStates.choosing_date.set()
    await show_slot_date_keyboard(callback_query, state)

async def show_slot_date_keyboard(callback_query, state: FSMContext, offset=0):
    today = datetime.today().date()
    start = today + timedelta(weeks=offset)
    dates = [start + timedelta(days=i) for i in range(7)]

    kb = InlineKeyboardMarkup(row_width=2)
    for d in dates:
        label = d.strftime("%d.%m (%a)")
        callback_data = f"admin:slot_date:{d.strftime('%Y-%m-%d')}"
        kb.insert(InlineKeyboardButton(label, callback_data=callback_data))

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ Назад", callback_data="admin:slot_nav:prev"))
    nav.append(InlineKeyboardButton("➡️ Далее", callback_data="admin:slot_nav:next"))
    kb.row(*nav)

    await state.update_data(week_offset=offset)
    await send_clean_message(state, callback_query.message.chat.id, "📅 Выберите дату для нового слота:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("admin:slot_nav:"), state=AdminStates.choosing_date)
async def slot_date_nav(callback_query: types.CallbackQuery, state: FSMContext):
    direction = callback_query.data.split(":")[2]
    data = await state.get_data()
    offset = data.get("week_offset", 0)
    if direction == "next":
        offset += 1
    elif direction == "prev":
        offset = max(0, offset - 1)
    await show_slot_date_keyboard(callback_query, state, offset)

@dp.callback_query_handler(lambda c: c.data.startswith("admin:slot_date:"), state=AdminStates.choosing_date)
async def admin_slot_choose_date(callback_query: types.CallbackQuery, state: FSMContext):
    date = callback_query.data.split(":")[2]
    await state.update_data(slot_date=date)

    data = await state.get_data()
    city = data.get("slot_city")

    # Загружаем занятые слоты на эту дату
    booked = get_booked_slots(city, date)
    if booked:
        slots_text = "\n".join([f"• {s}" for s in sorted(booked)])
        text = f"📅 Уже занятые слоты на {date}:\n{slots_text}\n\n🕒 Введите новое время в формате ЧЧ:ММ:"
    else:
        text = "✅ Пока свободно.\n\n🕒 Введите новое время в формате ЧЧ:ММ:"

    await send_clean_message(state, callback_query.message.chat.id, text)
    await AdminStates.entering_time.set()

@dp.message_handler(state=AdminStates.entering_time)
async def admin_slot_enter_time(message: types.Message, state: FSMContext):
    time_text = message.text.strip()
    try:
        datetime.strptime(time_text, "%H:%M")
    except ValueError:
        await message.answer("❗ Неверный формат. Введите время как ЧЧ:ММ (например, 14:30)")
        return

    data = await state.get_data()
    city = data["slot_city"]
    date = data["slot_date"]

    # сохраняем "пустой слот" в BOOKINGS_FILE, если ещё не занят
    bookings = load_bookings()
    key = f"{date}_{city}"
    if key not in bookings:
        bookings[key] = {}
    if time_text in bookings[key]:
        kb = InlineKeyboardMarkup()
        kb.add(
            InlineKeyboardButton("🔁 Попробовать другое время", callback_data="admin:slot_repeat_time"),
            InlineKeyboardButton("⬅️ Назад", callback_data="admin:panel")
        )
        await send_clean_message(state, message.chat.id, f"⚠️ Слот {time_text} уже существует.", reply_markup=kb)
        return
    else:
        bookings[key][time_text] = {}
        with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(bookings, f, ensure_ascii=False, indent=2)

        kb = InlineKeyboardMarkup()
        kb.add(
            InlineKeyboardButton("➕ Добавить ещё", callback_data="admin:add_another"),
            InlineKeyboardButton("⬅️ Назад", callback_data="admin:panel")
        )
        await send_clean_message(state, message.chat.id, f"✅ Слот {time_text} добавлен на {date} ({'Москва' if city == 'msk' else 'Краснодар'})", reply_markup=kb)

    # НЕ вызываем state.finish() — остаёмся в состоянии AdminStates.entering_time

@dp.callback_query_handler(lambda c: c.data == "admin:add_another", state="*")
async def admin_add_another(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()

    # Восстанавливаем город и дату (если не удалились после state.finish())
    data = await state.get_data()
    city = data.get("slot_city")
    date = data.get("slot_date")

    if not city or not date:
        await send_clean_message(state, callback_query.message.chat.id, "⚠️ Ошибка: не удалось восстановить город или дату. Пожалуйста, начните заново.")
        await state.finish()
        return

    # Повторно сохраняем данные в FSM (на случай потери)
    await state.update_data(slot_city=city, slot_date=date)

    await send_clean_message(state, callback_query.message.chat.id, "🕒 Введите время в формате ЧЧ:ММ:")
    await AdminStates.entering_time.set()

@dp.callback_query_handler(lambda c: c.data == "admin:go_start", state="*")
async def admin_go_start(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.finish()

    is_admin = callback_query.from_user.id == ADMIN_CHAT_ID

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Да", callback_data="begin:yes"),
        InlineKeyboardButton("❌ Нет", callback_data="begin:no"),
        InlineKeyboardButton("📣 Связаться с нами", callback_data="show:contacts")
    )
    if is_admin:
        kb.insert(InlineKeyboardButton("⚙️ Админка", callback_data="admin:panel"))

    await send_clean_message(state, callback_query.message.chat.id, "👋 Добро пожаловать! Хотите записаться на демонстрацию?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "admin:slot_repeat_time", state=AdminStates.entering_time)
async def repeat_slot_time(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await send_clean_message(callback_query.message.chat.id, text="🕒 Введите другое время в формате ЧЧ:ММ:")

@dp.callback_query_handler(lambda c: c.data == "resign:no")
async def handle_resign_no(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.finish()

    is_admin = callback_query.from_user.id == ADMIN_CHAT_ID

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Да", callback_data="begin:yes"),
        InlineKeyboardButton("❌ Нет", callback_data="begin:no"),
        InlineKeyboardButton("📣 Связаться с нами", callback_data="show:contacts")
    )
    if is_admin:
        kb.insert(InlineKeyboardButton("⚙️ Админка", callback_data="admin:panel"))

    await send_clean_message(callback_query.message.chat.id, text="👋 Хотите записаться на демонстрацию?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "resign:yes")
async def proceed_resign(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()

    user_id = callback_query.from_user.id
    data = load_bookings()
    deleted = False

    from google_calendar import delete_event_from_calendar, MOSCOW_CALENDAR_ID, KRASNODAR_CALENDAR_ID

    for key in list(data.keys()):
        city = key.split("_")[1]
        for time in list(data[key].keys()):
            info = data[key][time]
            if info.get("user_id") == user_id:
                event_id = info.get("event_id")
                if event_id:
                    calendar_id = MOSCOW_CALENDAR_ID if city == "msk" else KRASNODAR_CALENDAR_ID
                    try:
                        delete_event_from_calendar(calendar_id, event_id)
                        logging.info(f"[google] Событие {event_id} удалено из календаря {calendar_id}")
                    except Exception as e:
                        logging.warning(f"[google] Ошибка удаления события: {e}")
                        info = data[key][time]
                        event_id = info.get("event_id")
                        city = key.split("_")[1]
                        if event_id:
                            calendar_id = MOSCOW_CALENDAR_ID if city == "msk" else KRASNODAR_CALENDAR_ID
                            try:
                                delete_event_from_calendar(calendar_id, event_id)
                                logging.info(f"Удалено из календаря: {event_id}")
                            except Exception as e:
                                logging.warning(f"❌ Не удалось удалить событие {event_id} из календаря: {e}")
                del data[key][time]
                deleted = True
        if not data[key]:
            del data[key]

    if deleted:
        with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    msg = await callback_query.message.edit_text("Как вас зовут? (только буквы)")
    await state.update_data(last_bot_msg_id=msg.message_id)
    await state.set_state(BookingStates.waiting_for_name.state)

@dp.callback_query_handler(lambda c: c.data.startswith("resign:new:"))
async def create_additional_booking(callback_query: types.CallbackQuery, state: FSMContext):
    city_code = callback_query.data.split(":")[2]
    await callback_query.answer()
    await state.update_data(city=city_code)
    msg = await callback_query.message.edit_text("Как вас зовут? (только буквы)")
    await state.update_data(last_bot_msg_id=msg.message_id)
    await BookingStates.waiting_for_name.set()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(send_reminders())
    executor.start_polling(dp, skip_updates=True)

from aiogram import Bot, Dispatcher, types
from google_calendar import delete_event_from_calendar, MOSCOW_CALENDAR_ID, KRASNODAR_CALENDAR_ID
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from aiogram.utils import executor
from aiogram.dispatcher import FSMContext
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher.filters.state import State, StatesGroup
import logging
import re
import locale
from datetime import datetime, timedelta
import json
import os
import asyncio

locale.setlocale(locale.LC_TIME, "ru_RU.UTF-8")

API_TOKEN = "7684865098:AAFDNDRPBEty31cypZBcthDi9LCKbB8o5rQ"
ADMIN_CHAT_ID = 5887875855

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

class BookingStates(StatesGroup):
    waiting_for_city = State()
    waiting_for_name = State()
    waiting_for_phone = State()
    waiting_for_date = State()
    waiting_for_slot = State()

class AdminStates(StatesGroup):
    choosing_city = State()
    choosing_date = State()
    entering_time = State()

available_slots = ["12:00", "14:00", "16:00"]
BOOKINGS_FILE = "bookings.json"

# Создание файла при запуске, если он отсутствует
if not os.path.exists(BOOKINGS_FILE):
    with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f, ensure_ascii=False, indent=2)

# Для хранения последних сообщений пользователей (даже без FSM)
last_messages = {}

@dp.callback_query_handler(lambda c: c.data.startswith("city:"), state=BookingStates.waiting_for_city)
async def process_city(callback_query: types.CallbackQuery, state: FSMContext):
    city_code = callback_query.data.split(":")[1]
    user_id = callback_query.from_user.id
    await callback_query.answer()

    # Проверка на существующую запись
    data = load_bookings()
    for key in data:
        for time, info in data[key].items():
            if info.get("user_id") == user_id:
                kb = InlineKeyboardMarkup(row_width=1)
                kb.add(
                    InlineKeyboardButton("✅ Перезаписаться", callback_data="resign:yes"),
                    InlineKeyboardButton("🚫 Оставить как есть", callback_data="resign:no"),
                    InlineKeyboardButton("➕ Новая запись", callback_data=f"resign:new:{city_code}")
                )
                await send_clean_message(state, callback_query.message.chat.id, "📌 У вас уже есть запись. Что сделать?", reply_markup=kb)
                await state.finish()
                return

    # Если записи нет — идём дальше
    await state.update_data(city=city_code)
    msg = await callback_query.message.edit_text("Как вас зовут? (только буквы)")
    await state.update_data(last_bot_msg_id=msg.message_id)
    await BookingStates.waiting_for_name.set()

# ========== СЛОТЫ И ДАТЫ ==========
def load_bookings():
    if os.path.exists(BOOKINGS_FILE):
        with open(BOOKINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_booking(city, date_str, time, name, phone, user_id, event_id=None):
    if len(time) == 2:
        time += ":00"
    data = load_bookings()
    key = f"{date_str}_{city}"
    if key not in data:
        data[key] = {}
    data[key][time] = {
        "name": name,
        "phone": phone,
        "user_id": user_id,
        "event_id": event_id
    }
    with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_booked_slots(city, date_str):
    data = load_bookings()
    key = f"{date_str}_{city}"
    day_bookings = data.get(key, {})

    # Фильтруем только реальные записи (а не заготовки от админа)
    return [
        time for time, info in day_bookings.items()
        if info.get("user_id") != "admin_add"
    ]

def generate_ics_file(name, slot_time, city):
    if len(slot_time) == 2:
        slot_time += ":00"
    now = datetime.now()
    date = now.date()
    dt_start = datetime.strptime(f"{date} {slot_time}", "%Y-%m-%d %H:%M")
    dt_end = dt_start + timedelta(hours=1)
    filename = f"calendar_{name}.ics"
    ics = f"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:Демонстрация оборудования
DTSTART;TZID=Europe/Moscow:{dt_start.strftime('%Y%m%dT%H%M%S')}
DTEND;TZID=Europe/Moscow:{dt_end.strftime('%Y%m%dT%H%M%S')}
LOCATION:{'Москва' if city == 'msk' else 'Краснодар'}
DESCRIPTION:Вы записаны на демонстрацию от СНК Лазер
END:VEVENT
END:VCALENDAR"""
    with open(filename, "w", encoding="utf-8") as f:
        f.write(ics)
    return filename

def get_week_dates(offset=0, city=None):
    today = datetime.today().date()
    start = today + timedelta(weeks=offset)
    dates = []
    all_days = [start + timedelta(days=i) for i in range(7)]

    data = load_bookings()
    available_days = set()

    if city:
        for d in all_days:
            key = f"{d.strftime('%Y-%m-%d')}_{city}"
            if key in data and data[key]:  # есть хотя бы один слот
                available_days.add(d)

    for d in all_days:
        if d in available_days or d.weekday() < 5:
            dates.append(d)

    return dates

async def show_date_keyboard(message_or_callback, state: FSMContext, offset=0):
    data = await state.get_data()
    city = data.get("city")
    dates = get_week_dates(offset, city=city)
    kb = InlineKeyboardMarkup(row_width=2)
    for d in dates:
        label = d.strftime("%d.%m")
        callback_data = f"date:{d.strftime('%Y-%m-%d')}"
        kb.insert(InlineKeyboardButton(label, callback_data=callback_data))

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ Предыдущая", callback_data="date_nav:prev"))
    nav.append(InlineKeyboardButton("➡️ Следующая", callback_data="date_nav:next"))
    kb.row(*nav)

    await state.update_data(week_offset=offset)

    data = await state.get_data()
    msg_id = data.get("last_bot_msg_id")

    try:
        if isinstance(message_or_callback, types.CallbackQuery):
            await message_or_callback.message.edit_text("📅 Выберите дату:", reply_markup=kb)
        elif isinstance(message_or_callback, types.Message) and msg_id:
            await bot.edit_message_text(
                chat_id=message_or_callback.chat.id,
                message_id=msg_id,
                text="📅 Выберите дату:",
                reply_markup=kb
            )
        else:
            await message_or_callback.answer("📅 Выберите дату:", reply_markup=kb)
    except Exception as e:
        logging.warning(f"[show_date_keyboard] редактирование не удалось: {e}")
        await send_clean_message(state, message_or_callback.chat.id, "📅 Выберите дату:", reply_markup=kb
        )
    except Exception as e:
        logging.error(f"Ошибка при отображении клавиатуры дат: {e}")

async def send_clean_message(target, chat_id: int = None, text: str = None, reply_markup=None):
    if isinstance(target, FSMContext):
        state = target
        data = await state.get_data()
        msg_id = data.get("last_bot_msg_id")
        chat_id = data.get("chat_id") if "chat_id" in data else chat_id
    else:
        state = None
        msg_id = last_messages.get(target)
        chat_id = target

    # Удаляем предыдущее сообщение
    if msg_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            logging.info(f"[send_clean_message] Удалено сообщение {msg_id}")
        except Exception as e:
            logging.warning(f"[send_clean_message] Не удалось удалить {msg_id}: {e}")

    # Отправляем новое сообщение
    msg = await bot.send_message(chat_id, text, reply_markup=reply_markup)

    # Сохраняем ID последнего сообщения в FSM и глобально
    if state:
        await state.update_data(last_bot_msg_id=msg.message_id)
    last_messages[chat_id] = msg.message_id  # ✅ Всегда

    logging.info(f"[send_clean_message] Отправлено новое сообщение {msg.message_id}")

@dp.callback_query_handler(lambda c: c.data == "admin:panel", state="*")
async def admin_panel(callback_query: types.CallbackQuery, state: FSMContext):
    current_state = await state.get_state()

    if current_state is not None:
        kb = InlineKeyboardMarkup().add(
            InlineKeyboardButton("↩ Вернуться в начало", callback_data="admin:go_start")
        )
        await callback_query.message.edit_text(
            "❗ Админка доступна только в начале. Завершите текущую запись или нажмите /start.",
            reply_markup=kb
        )
        return

    # ✅ Если всё ок — показываем панель
    await callback_query.answer()
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📋 Записи по датам", callback_data="admin:view_dates"),
        InlineKeyboardButton("🧾 Удалить по одной", callback_data="admin:delete_select"),
        InlineKeyboardButton("🗑 Очистить Москву", callback_data="admin:clear_msk"),
        InlineKeyboardButton("🗑 Очистить Краснодар", callback_data="admin:clear_kras"),
        InlineKeyboardButton("➕ Добавить слот", callback_data="admin:add_slot"),
        InlineKeyboardButton("⬅️ Назад", callback_data="back:to_start")
    )
    await callback_query.message.edit_text("🛠 Админ-панель", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data in ["admin:clear_msk", "admin:clear_kras"])
async def confirm_city_clear(callback_query: types.CallbackQuery):
    city = "Москва" if "msk" in callback_query.data else "Краснодар"
    code = "msk" if "msk" in callback_query.data else "kras"

    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("❌ Подтвердить удаление", callback_data=f"admin:clear_city:{code}"),
        InlineKeyboardButton("⬅️ Отмена", callback_data="admin:panel")
    )
    await callback_query.message.edit_text(f"⚠️ Удалить все записи в городе {city}?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("admin:clear_city:"))
async def clear_city_bookings(callback_query: types.CallbackQuery, state: FSMContext):
    city_code = callback_query.data.split(":")[2]
    await callback_query.answer()

    data = load_bookings()
    keys_to_delete = [k for k in data if k.endswith(f"_{city_code}")]

    from google_calendar import delete_event_from_calendar, MOSCOW_CALENDAR_ID, KRASNODAR_CALENDAR_ID

    for k in keys_to_delete:
        day_data = data.get(k, {})
        for time, info in list(day_data.items()):
            event_id = info.get("event_id")
            if event_id:
                calendar_id = MOSCOW_CALENDAR_ID if city_code == "msk" else KRASNODAR_CALENDAR_ID
                try:
                    delete_event_from_calendar(calendar_id, event_id)
                    logging.info(f"[google] Удалено событие {event_id} из {calendar_id}")
                except Exception as e:
                    logging.warning(f"[google] Не удалось удалить {event_id}: {e}")
        del data[k]  # удаляем запись из словаря

    with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    await callback_query.message.edit_text(f"🗑 Все записи в городе {'Москва' if city_code == 'msk' else 'Краснодар'} удалены.")
    await asyncio.sleep(2)
    await admin_panel(callback_query, state)

@dp.callback_query_handler(lambda c: c.data == "back:to_start")
async def back_to_start(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.finish()

    # Удалим последнее сообщение, даже если FSM нет
    chat_id = callback_query.message.chat.id
    last_msg_id = last_messages.get(chat_id)
    if last_msg_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=last_msg_id)
        except Exception as e:
            logging.warning(f"[back_to_start] Не удалось удалить сообщение: {e}")

    is_admin = callback_query.from_user.id == ADMIN_CHAT_ID
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Да", callback_data="begin:yes"),
        InlineKeyboardButton("❌ Нет", callback_data="begin:no"),
        InlineKeyboardButton("📣 Связаться с нами", callback_data="show:contacts")
    )
    if is_admin:
        kb.insert(InlineKeyboardButton("⚙️ Админка", callback_data="admin:panel"))

    await send_clean_message(state, chat_id, "👋 Хотите записаться на демонстрацию?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "admin:view_dates")
async def admin_view_dates(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    data = load_bookings()

    if not data:
        await callback_query.message.edit_text("📋 Пока нет ни одной записи.\n\n🔙 Возвращаюсь в админку...")
        await asyncio.sleep(2)
        await admin_panel(callback_query, state)
        return

    lines = ["📅 Записи по датам:"]
    for key in sorted(data.keys()):
        date_str, city_code = key.split("_")
        try:
            formatted = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m")
        except:
            formatted = date_str
        city = "Москва" if city_code == "msk" else "Краснодар"

        # ✅ Считаем только реальные записи
        real_bookings = [
            t for t, info in data[key].items()
            if info and info.get("user_id") and info.get("user_id") != "admin_add"
        ]
        count = len(real_bookings)

        lines.append(f"• {formatted} — {city}: {count} чел.")

    lines.append("\n⬅️ Нажмите /start для возврата")
    await callback_query.message.edit_text("\n".join(lines))

@dp.callback_query_handler(lambda c: c.data == "admin:clear_confirm")
async def admin_clear_confirm(callback_query: types.CallbackQuery):
    await callback_query.answer()
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("❌ Подтвердить удаление", callback_data="admin:clear_all"),
        InlineKeyboardButton("⬅️ Отмена", callback_data="admin:panel")
    )
    await callback_query.message.edit_text("⚠️ Вы уверены, что хотите удалить все записи?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "admin:clear_all")
async def admin_clear_all(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()

    if os.path.exists(BOOKINGS_FILE):
        os.remove(BOOKINGS_FILE)

    await callback_query.message.edit_text("🗑 Все записи удалены.\n\n🔙 Возвращаюсь в админку...")
    await asyncio.sleep(2)
    await admin_panel(callback_query, state)

@dp.callback_query_handler(lambda c: c.data == "admin:delete_select")
async def admin_delete_select(callback_query: types.CallbackQuery):
    await callback_query.answer()
    data = load_bookings()

    if not data:
        await callback_query.message.edit_text("📋 Нет записей для удаления.\n\n🔙 Возвращаюсь в админку...")
        await asyncio.sleep(2)
        state = dp.current_state(chat=callback_query.message.chat.id, user=callback_query.from_user.id)
        await admin_panel(callback_query, state)
        return

    kb = InlineKeyboardMarkup(row_width=1)
    for key in sorted(data.keys()):
        date_str, city = key.split("_")
        try:
            formatted_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m")
        except:
            formatted_date = date_str

        label = f"{formatted_date} — {'Москва' if city == 'msk' else 'Краснодар'}"
        kb.add(InlineKeyboardButton(label, callback_data=f"admin:deldate:{key}"))

    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="admin:panel"))
    await callback_query.message.edit_text("📅 Выберите дату для удаления записей:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("admin:deldate:"))
async def admin_choose_record(callback_query: types.CallbackQuery):
    await callback_query.answer()
    key = callback_query.data.split(":", 2)[2]  # формат YYYY-MM-DD_msk

    data = load_bookings()
    day_data = data.get(key)

    if not day_data:
        await callback_query.message.edit_text("😕 На эту дату записей нет.\n\n🔙 Возвращаюсь в админку...")
        await asyncio.sleep(2)
        state = dp.current_state(chat=callback_query.message.chat.id, user=callback_query.from_user.id)
        await admin_panel(callback_query, state)
        return

    kb = InlineKeyboardMarkup(row_width=1)
    for time, info in day_data.items():
        label = f"{time} — {info['name']} ({info['phone']})"
        kb.add(InlineKeyboardButton(label, callback_data=f"admin:deluser:{key}:{time}"))

    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="admin:delete_select"))
    await callback_query.message.edit_text("👤 Выберите запись для удаления:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("admin:deluser:"))
async def admin_delete_user(callback_query: types.CallbackQuery):
    await callback_query.answer()
    _, _, key, time = callback_query.data.split(":", 3)

    data = load_bookings()
    day_data = data.get(key)
    city = key.split("_")[1]  # msk или kras

    if day_data and time in day_data:
        info = day_data[time]
        event_id = info.get("event_id")

        # Удаляем из Google Календаря
        if event_id:
            calendar_id = MOSCOW_CALENDAR_ID if city == "msk" else KRASNODAR_CALENDAR_ID
            try:
                delete_event_from_calendar(calendar_id, event_id)
                logging.info(f"✅ Событие {event_id} удалено из календаря {calendar_id}")
            except Exception as e:
                logging.warning(f"❌ Не удалось удалить событие из календаря: {e}")

        # Удаляем слот
        deleted = day_data.pop(time)

        # Удаляем ключ даты, если слоты закончились
        if not day_data:
            data.pop(key)

        # Сохраняем обновлённые данные
        with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # Если ещё остались записи на эту дату
        if key in data:
            kb = InlineKeyboardMarkup(row_width=1)
            for t, info in data[key].items():
                label = f"{t} — {info['name']} ({info['phone']})"
                kb.add(InlineKeyboardButton(label, callback_data=f"admin:deluser:{key}:{t}"))
            kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="admin:delete_select"))

            await callback_query.message.edit_text(
                f"🗑 Удалено:\n{time} — {deleted['name']} ({deleted['phone']})\n\n👤 Выберите следующую запись для удаления:",
                reply_markup=kb
            )
        else:
            await callback_query.message.edit_text(
                f"🗑 Удалено:\n{time} — {deleted['name']} ({deleted['phone']})\n\n📅 На этой дате больше нет записей. Возвращаюсь назад..."
            )
            await asyncio.sleep(2)
            await admin_delete_select(callback_query)
    else:
        await callback_query.message.edit_text("😕 Запись не найдена.")

@dp.callback_query_handler(lambda c: c.data == "show:contacts")
async def show_socials(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()

    text = "📣 Свяжитесь с нами или подпишитесь в соцсетях:"
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📷 Instagram", url="https://www.instagram.com/snk_laser/"),
        InlineKeyboardButton("🌐 Сайт", url="https://snklaser.ru"),
        InlineKeyboardButton("📺 YouTube", url="https://www.youtube.com/@snklaser"),
        InlineKeyboardButton("📘 VK", url="https://vk.com/snk_laser"),
        InlineKeyboardButton("💬 Telegram", url="https://t.me/snk_laser"),
        InlineKeyboardButton("📞 WhatsApp", url="https://wa.me/79951530030"),
        InlineKeyboardButton("⬅️ Назад", callback_data="back:to_start")
    )

    await send_clean_message(callback_query.message.chat.id, text=text, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("date_nav:"), state=BookingStates.waiting_for_date)
async def handle_date_nav(callback_query: types.CallbackQuery, state: FSMContext):
    direction = callback_query.data.split(":")[1]
    data = await state.get_data()
    logging.info(f"[show_socials] last_bot_msg_id = {data.get('last_bot_msg_id')}")
    offset = data.get("week_offset", 0)
    if direction == "next":
        offset += 1
    elif direction == "prev":
        offset = max(0, offset - 1)
    await show_date_keyboard(callback_query, state, offset)

@dp.callback_query_handler(lambda c: c.data.startswith("date:"), state=BookingStates.waiting_for_date)
async def process_date(callback_query: types.CallbackQuery, state: FSMContext):
    date_str = callback_query.data.split(":")[1]
    await state.update_data(date=date_str)

    data = await state.get_data()
    city = data.get("city")

    bookings = load_bookings()
    key = f"{date_str}_{city}"
    day_slots = bookings.get(key, {})

    free_slots = []

    # Добавляем стандартные слоты, если они не заняты
    for slot in available_slots:
        if slot not in day_slots:
            free_slots.append(slot)
        elif not day_slots[slot].get("user_id") or day_slots[slot].get("user_id") == "admin_add":
            free_slots.append(slot)

    # Добавляем вручную добавленные слоты, если они не заняты
    for slot, info in day_slots.items():
        if slot not in free_slots and (not info.get("user_id") or info.get("user_id") == "admin_add"):
            free_slots.append(slot)

    # Если свободных слотов нет — показываем ошибку и возвращаем выбор даты
    if not free_slots:
        await callback_query.answer("⛔ Нет доступных слотов на эту дату.")
        await send_clean_message(state, callback_query.message.chat.id, "😕 На эту дату нет свободных слотов. Попробуйте выбрать другую дату.")
        await BookingStates.waiting_for_date.set()
        await show_date_keyboard(callback_query, state)
        return

    # Показываем слоты
    kb = InlineKeyboardMarkup(row_width=2)
    for slot in sorted(free_slots):
        kb.insert(InlineKeyboardButton(slot, callback_data=f"slot:{slot}"))
    kb.row(InlineKeyboardButton("⬅️ Назад", callback_data="back:to_dates"))

    await callback_query.answer()
    await send_clean_message(state, callback_query.message.chat.id, "🕒 Выберите свободный слот:", reply_markup=kb)
    await BookingStates.waiting_for_slot.set()

@dp.callback_query_handler(lambda c: c.data == "back:to_dates", state=BookingStates.waiting_for_slot)
async def back_to_dates(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await BookingStates.waiting_for_date.set()
    await show_date_keyboard(callback_query, state)

@dp.callback_query_handler(lambda c: c.data.startswith("slot:"), state=BookingStates.waiting_for_slot)
async def process_slot(callback_query: types.CallbackQuery, state: FSMContext):
    slot_time = callback_query.data.split(":")[1]
    if len(slot_time) == 2:
        slot_time += ":00"

    data = await state.get_data()
    city_code = data.get("city")
    date_str = data.get("date")
    
    # Проверяем, не занят ли слот
    if slot_time in get_booked_slots(city_code, date_str):
        await callback_query.answer()
        await callback_query.message.answer("⛔ Этот слот уже занят. Пожалуйста, выберите другой.")
        return

    await callback_query.answer()
    await state.update_data(slot=slot_time)

    # Сохраняем бронь
    name = data.get("name")
    phone = data.get("phone")
    user_id = callback_query.from_user.id
    save_booking(city_code, date_str, slot_time, name, phone, user_id)

    from google_calendar import add_event_to_calendar

    # Добавление в Google Календарь
    add_event_to_calendar(name, phone, city_code, date_str, slot_time)

    formatted_time = datetime.strptime(slot_time, "%H:%M").strftime("%H:%M")
    date_text = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %d %B")

    msg_text = (
        f"📝 Новая запись:\n"
        f"👤 Имя: {name}\n"
        f"📞 Телефон: {phone}\n"
        f"🏙️ Город: {'Москва' if city_code == 'msk' else 'Краснодар'}\n"
        f"📅 Дата: {date_text}\n"
        f"🕒 Время: {formatted_time}"
    )

    # Отправка администратору — исправленный вызов
    await send_clean_message(ADMIN_CHAT_ID, text=msg_text)

    # Подтверждение пользователю
    await send_clean_message(state, callback_query.message.chat.id, "🎉 Вы успешно записались! Мы ждём вас на демонстрации.")

    # Предложение добавить в календарь
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📆 Добавить в календарь", callback_data="calendar:add"),
        InlineKeyboardButton("⛔ Пропустить", callback_data="calendar:skip")
    )
    await callback_query.message.answer("Хотите добавить событие в календарь?", reply_markup=kb)

    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "calendar:add")
async def send_calendar(callback_query: types.CallbackQuery, state: FSMContext):
    bookings = load_bookings()
    user_id = callback_query.from_user.id

    for key in bookings:
        for time, data in bookings[key].items():
            if data.get("user_id") == user_id:
                city = key.split("_")[1]
                filename = generate_ics_file(data["name"], time, city)

                await bot.send_document(user_id, InputFile(filename))
                os.remove(filename)

                # ⏳ Пауза 5 секунд
                await asyncio.sleep(5)

                # ✅ Подтверждение
                await send_clean_message(state, callback_query.message.chat.id, "🎉 Вы успешно записались! Мы ждём вас на демонстрации.")

                # ✉️ Дополнительное сообщение
                await callback_query.message.answer("Если бот понадобится снова — просто нажмите /start")

                # 🧹 Завершение FSM-сессии
                await state.finish()
                return

    await callback_query.message.answer("😕 Не удалось найти запись для календаря.")

@dp.callback_query_handler(lambda c: c.data == "calendar:skip")
async def skip_calendar(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await callback_query.message.edit_text("Хорошо, запись завершена. Если передумаете — просто нажмите /start 😊")

async def send_reminders():
    REMINDER_TIMES = {
        "1day": timedelta(days=1),
        "2hour": timedelta(hours=2)
    }

    while True:
        try:
            now = datetime.now()
            bookings = load_bookings()
            updated = False

            for key, slots in bookings.items():
                date_str, city = key.split("_")

                for time_str, info in slots.items():
                    # Пропускаем пустые или неправильные записи
                    if not info or not isinstance(info, dict):
                        continue

                    user_id = info.get("user_id")
                    name = info.get("name")
                    phone = info.get("phone")

                    if not user_id or user_id == "admin_add":
                        continue

                    # Проверка корректного времени
                    if len(time_str) == 2:
                        time_str += ":00"
                    try:
                        slot_time = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                    except ValueError:
                        logging.warning(f"[НАПОМИНАНИЕ] Некорректный формат времени: {time_str}")
                        continue

                    # Проверяем, отправлялись ли уже напоминания
                    reminded = info.get("reminded", {"1day": False, "2hour": False})

                    for label, delta in REMINDER_TIMES.items():
                        time_until = slot_time - now
                        if not reminded.get(label) and timedelta(0) <= time_until <= delta + timedelta(minutes=1):
                            try:
                                reminder_text = (
                                    f"🔔 Напоминание: демонстрация начнется в {slot_time.strftime('%H:%M')} "
                                    f"— уже через {'2 часа' if label == '2hour' else 'день'}!"
                                )

                                await send_clean_message(user_id, reminder_text)
                                reminded[label] = True
                                info["reminded"] = reminded
                                updated = True

                                logging.info(f"[НАПОМИНАНИЕ] {label} отправлено → {user_id} на {time_str}")
                            except Exception as e:
                                logging.warning(f"❌ Не удалось отправить напоминание {label} пользователю {user_id}: {e}")

            if updated:
                with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
                    json.dump(bookings, f, ensure_ascii=False, indent=2)

            await asyncio.sleep(60)

        except Exception as e:
            logging.error(f"Ошибка в send_reminders: {e}")
            await asyncio.sleep(30)

@dp.callback_query_handler(lambda c: c.data == "begin:no")
async def handle_no(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await callback_query.message.answer("Хорошо, если передумаете — просто нажмите /start")

@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()

    is_admin = message.from_user.id == ADMIN_CHAT_ID

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Да", callback_data="begin:yes"),
        InlineKeyboardButton("❌ Нет", callback_data="begin:no"),
        InlineKeyboardButton("📣 Связаться с нами", callback_data="show:contacts")
    )
    if is_admin:
        kb.insert(InlineKeyboardButton("⚙️ Админка", callback_data="admin:panel"))

    await send_clean_message(state, message.chat.id, "👋 Добро пожаловать! Хотите записаться на демонстрацию?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "begin:yes")
async def handle_yes(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("Москва", callback_data="city:msk"),
        InlineKeyboardButton("Краснодар", callback_data="city:kras")
    )
    await send_clean_message(state, callback_query.message.chat.id, "🏙️ Выберите город:", reply_markup=kb)
    await BookingStates.waiting_for_city.set()

@dp.message_handler(state=BookingStates.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if not re.fullmatch(r"[а-яА-ЯёЁa-zA-Z\- ]+", name):
        await send_clean_message(state, message.chat.id, "❗ Имя может содержать только буквы. Попробуйте снова:")
        return
    await state.update_data(name=name)
    await send_clean_message(state, message.chat.id, "📞 Введите номер телефона (начинается с 8 или +7, 10 цифр после)")
    await BookingStates.waiting_for_phone.set()

    try:
        await message.delete()
    except:
        pass

    data = await state.get_data()
    msg_id = data.get("last_bot_msg_id")

    try:
        msg = await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=msg_id,
            text="📞 Введите номер телефона (начинается с 8 или +7, 10 цифр после)"
        )
    except:
        await send_clean_message(state, message.chat.id, "📞 Введите номер телефона (начинается с 8 или +7, 10 цифр после)")

    await BookingStates.waiting_for_phone.set()

@dp.message_handler(state=BookingStates.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip().replace(" ", "")
    if not re.fullmatch(r"^(\+7|8)[0-9]{10}$", phone):
        await send_clean_message(state, message.chat.id, "❗ Неверный формат телефона. Попробуйте снова:")
        return

    await state.update_data(phone=phone)
    await state.update_data(week_offset=0)

    try:
        await message.delete()
    except:
        pass

    data = await state.get_data()
    msg_id = data.get("last_bot_msg_id")

    try:
        msg = await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=msg_id,
            text="📅 Выберите дату:"
        )
    except:
        await send_clean_message(state, message_or_callback.message.chat.id, "📅 Выберите дату:", reply_markup=kb)

    await state.update_data(last_bot_msg_id=msg.message_id)

    # ✅ Главное: всегда переход к следующему состоянию
    await BookingStates.waiting_for_phone.set()
    
    # Добавляем логирование текущего состояния
    logging.info(f"User {message.from_user.id} ввёл телефон: {phone}")
    
    # вызываем функцию показа клавиатуры дат с await
    await BookingStates.waiting_for_date.set()
    await show_date_keyboard(message, state, offset=0)

@dp.callback_query_handler(lambda c: c.data == "admin:add_slot")
async def admin_add_slot_start(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("Москва", callback_data="admin:slot_city:msk"),
        InlineKeyboardButton("Краснодар", callback_data="admin:slot_city:kras")
    )
    await send_clean_message(state, callback_query.message.chat.id, "🏙️ Выберите город для слота:", reply_markup=kb)
    await AdminStates.choosing_city.set()

@dp.callback_query_handler(lambda c: c.data.startswith("admin:slot_city:"), state=AdminStates.choosing_city)
async def admin_slot_choose_city(callback_query: types.CallbackQuery, state: FSMContext):
    city = callback_query.data.split(":")[2]
    await state.update_data(slot_city=city, week_offset=0)
    await AdminStates.choosing_date.set()
    await show_slot_date_keyboard(callback_query, state)

async def show_slot_date_keyboard(callback_query, state: FSMContext, offset=0):
    today = datetime.today().date()
    start = today + timedelta(weeks=offset)
    dates = [start + timedelta(days=i) for i in range(7)]

    kb = InlineKeyboardMarkup(row_width=2)
    for d in dates:
        label = d.strftime("%d.%m (%a)")
        callback_data = f"admin:slot_date:{d.strftime('%Y-%m-%d')}"
        kb.insert(InlineKeyboardButton(label, callback_data=callback_data))

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ Назад", callback_data="admin:slot_nav:prev"))
    nav.append(InlineKeyboardButton("➡️ Далее", callback_data="admin:slot_nav:next"))
    kb.row(*nav)

    await state.update_data(week_offset=offset)
    await send_clean_message(state, callback_query.message.chat.id, "📅 Выберите дату для нового слота:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("admin:slot_nav:"), state=AdminStates.choosing_date)
async def slot_date_nav(callback_query: types.CallbackQuery, state: FSMContext):
    direction = callback_query.data.split(":")[2]
    data = await state.get_data()
    offset = data.get("week_offset", 0)
    if direction == "next":
        offset += 1
    elif direction == "prev":
        offset = max(0, offset - 1)
    await show_slot_date_keyboard(callback_query, state, offset)

@dp.callback_query_handler(lambda c: c.data.startswith("admin:slot_date:"), state=AdminStates.choosing_date)
async def admin_slot_choose_date(callback_query: types.CallbackQuery, state: FSMContext):
    date = callback_query.data.split(":")[2]
    await state.update_data(slot_date=date)

    data = await state.get_data()
    city = data.get("slot_city")

    # Загружаем занятые слоты на эту дату
    booked = get_booked_slots(city, date)
    if booked:
        slots_text = "\n".join([f"• {s}" for s in sorted(booked)])
        text = f"📅 Уже занятые слоты на {date}:\n{slots_text}\n\n🕒 Введите новое время в формате ЧЧ:ММ:"
    else:
        text = "✅ Пока свободно.\n\n🕒 Введите новое время в формате ЧЧ:ММ:"

    await send_clean_message(state, callback_query.message.chat.id, text)
    await AdminStates.entering_time.set()

@dp.message_handler(state=AdminStates.entering_time)
async def admin_slot_enter_time(message: types.Message, state: FSMContext):
    time_text = message.text.strip()
    try:
        datetime.strptime(time_text, "%H:%M")
    except ValueError:
        await message.answer("❗ Неверный формат. Введите время как ЧЧ:ММ (например, 14:30)")
        return

    data = await state.get_data()
    city = data["slot_city"]
    date = data["slot_date"]

    # сохраняем "пустой слот" в BOOKINGS_FILE, если ещё не занят
    bookings = load_bookings()
    key = f"{date}_{city}"
    if key not in bookings:
        bookings[key] = {}
    if time_text in bookings[key]:
        kb = InlineKeyboardMarkup()
        kb.add(
            InlineKeyboardButton("🔁 Попробовать другое время", callback_data="admin:slot_repeat_time"),
            InlineKeyboardButton("⬅️ Назад", callback_data="admin:panel")
        )
        await send_clean_message(state, message.chat.id, f"⚠️ Слот {time_text} уже существует.", reply_markup=kb)
        return
    else:
        bookings[key][time_text] = {}
        with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(bookings, f, ensure_ascii=False, indent=2)

        kb = InlineKeyboardMarkup()
        kb.add(
            InlineKeyboardButton("➕ Добавить ещё", callback_data="admin:add_another"),
            InlineKeyboardButton("⬅️ Назад", callback_data="admin:panel")
        )
        await send_clean_message(state, message.chat.id, f"✅ Слот {time_text} добавлен на {date} ({'Москва' if city == 'msk' else 'Краснодар'})", reply_markup=kb)

    # НЕ вызываем state.finish() — остаёмся в состоянии AdminStates.entering_time

@dp.callback_query_handler(lambda c: c.data == "admin:add_another", state="*")
async def admin_add_another(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()

    # Восстанавливаем город и дату (если не удалились после state.finish())
    data = await state.get_data()
    city = data.get("slot_city")
    date = data.get("slot_date")

    if not city or not date:
        await send_clean_message(state, callback_query.message.chat.id, "⚠️ Ошибка: не удалось восстановить город или дату. Пожалуйста, начните заново.")
        await state.finish()
        return

    # Повторно сохраняем данные в FSM (на случай потери)
    await state.update_data(slot_city=city, slot_date=date)

    await send_clean_message(state, callback_query.message.chat.id, "🕒 Введите время в формате ЧЧ:ММ:")
    await AdminStates.entering_time.set()

@dp.callback_query_handler(lambda c: c.data == "admin:go_start", state="*")
async def admin_go_start(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.finish()

    is_admin = callback_query.from_user.id == ADMIN_CHAT_ID

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Да", callback_data="begin:yes"),
        InlineKeyboardButton("❌ Нет", callback_data="begin:no"),
        InlineKeyboardButton("📣 Связаться с нами", callback_data="show:contacts")
    )
    if is_admin:
        kb.insert(InlineKeyboardButton("⚙️ Админка", callback_data="admin:panel"))

    await send_clean_message(state, callback_query.message.chat.id, "👋 Добро пожаловать! Хотите записаться на демонстрацию?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "admin:slot_repeat_time", state=AdminStates.entering_time)
async def repeat_slot_time(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await send_clean_message(callback_query.message.chat.id, text="🕒 Введите другое время в формате ЧЧ:ММ:")

@dp.callback_query_handler(lambda c: c.data == "resign:no")
async def handle_resign_no(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.finish()

    is_admin = callback_query.from_user.id == ADMIN_CHAT_ID

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Да", callback_data="begin:yes"),
        InlineKeyboardButton("❌ Нет", callback_data="begin:no"),
        InlineKeyboardButton("📣 Связаться с нами", callback_data="show:contacts")
    )
    if is_admin:
        kb.insert(InlineKeyboardButton("⚙️ Админка", callback_data="admin:panel"))

    await send_clean_message(callback_query.message.chat.id, text="👋 Хотите записаться на демонстрацию?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "resign:yes")
async def proceed_resign(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()

    user_id = callback_query.from_user.id
    data = load_bookings()
    deleted = False

    from google_calendar import delete_event_from_calendar, MOSCOW_CALENDAR_ID, KRASNODAR_CALENDAR_ID

    for key in list(data.keys()):
        city = key.split("_")[1]
        for time in list(data[key].keys()):
            info = data[key][time]
            if info.get("user_id") == user_id:
                event_id = info.get("event_id")
                if event_id:
                    calendar_id = MOSCOW_CALENDAR_ID if city == "msk" else KRASNODAR_CALENDAR_ID
                    try:
                        delete_event_from_calendar(calendar_id, event_id)
                        logging.info(f"[google] Событие {event_id} удалено из календаря {calendar_id}")
                    except Exception as e:
                        logging.warning(f"[google] Ошибка удаления события: {e}")
                        info = data[key][time]
                        event_id = info.get("event_id")
                        city = key.split("_")[1]
                        if event_id:
                            calendar_id = MOSCOW_CALENDAR_ID if city == "msk" else KRASNODAR_CALENDAR_ID
                            try:
                                delete_event_from_calendar(calendar_id, event_id)
                                logging.info(f"Удалено из календаря: {event_id}")
                            except Exception as e:
                                logging.warning(f"❌ Не удалось удалить событие {event_id} из календаря: {e}")
                del data[key][time]
                deleted = True
        if not data[key]:
            del data[key]

    if deleted:
        with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    msg = await callback_query.message.edit_text("Как вас зовут? (только буквы)")
    await state.update_data(last_bot_msg_id=msg.message_id)
    await state.set_state(BookingStates.waiting_for_name.state)

@dp.callback_query_handler(lambda c: c.data.startswith("resign:new:"))
async def create_additional_booking(callback_query: types.CallbackQuery, state: FSMContext):
    city_code = callback_query.data.split(":")[2]
    await callback_query.answer()
    await state.update_data(city=city_code)
    msg = await callback_query.message.edit_text("Как вас зовут? (только буквы)")
    await state.update_data(last_bot_msg_id=msg.message_id)
    await BookingStates.waiting_for_name.set()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(send_reminders())
    executor.start_polling(dp, skip_updates=True)

from aiogram import Bot, Dispatcher, types
from google_calendar import delete_event_from_calendar, MOSCOW_CALENDAR_ID, KRASNODAR_CALENDAR_ID
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from aiogram.utils import executor
from aiogram.dispatcher import FSMContext
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher.filters.state import State, StatesGroup
import logging
import re
import locale
from datetime import datetime, timedelta
import json
import os
import asyncio

locale.setlocale(locale.LC_TIME, "ru_RU.UTF-8")

API_TOKEN = "7684865098:AAFDNDRPBEty31cypZBcthDi9LCKbB8o5rQ"
ADMIN_CHAT_ID = 5887875855

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

class BookingStates(StatesGroup):
    waiting_for_city = State()
    waiting_for_name = State()
    waiting_for_phone = State()
    waiting_for_date = State()
    waiting_for_slot = State()

class AdminStates(StatesGroup):
    choosing_city = State()
    choosing_date = State()
    entering_time = State()

available_slots = ["12:00", "14:00", "16:00"]
BOOKINGS_FILE = "bookings.json"

# Создание файла при запуске, если он отсутствует
if not os.path.exists(BOOKINGS_FILE):
    with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f, ensure_ascii=False, indent=2)

# Для хранения последних сообщений пользователей (даже без FSM)
last_messages = {}

@dp.callback_query_handler(lambda c: c.data.startswith("city:"), state=BookingStates.waiting_for_city)
async def process_city(callback_query: types.CallbackQuery, state: FSMContext):
    city_code = callback_query.data.split(":")[1]
    user_id = callback_query.from_user.id
    await callback_query.answer()

    # Проверка на существующую запись
    data = load_bookings()
    for key in data:
        for time, info in data[key].items():
            if info.get("user_id") == user_id:
                kb = InlineKeyboardMarkup(row_width=1)
                kb.add(
                    InlineKeyboardButton("✅ Перезаписаться", callback_data="resign:yes"),
                    InlineKeyboardButton("🚫 Оставить как есть", callback_data="resign:no"),
                    InlineKeyboardButton("➕ Новая запись", callback_data=f"resign:new:{city_code}")
                )
                await send_clean_message(state, callback_query.message.chat.id, "📌 У вас уже есть запись. Что сделать?", reply_markup=kb)
                await state.finish()
                return

    # Если записи нет — идём дальше
    await state.update_data(city=city_code)
    msg = await callback_query.message.edit_text("Как вас зовут? (только буквы)")
    await state.update_data(last_bot_msg_id=msg.message_id)
    await BookingStates.waiting_for_name.set()

# ========== СЛОТЫ И ДАТЫ ==========
def load_bookings():
    if os.path.exists(BOOKINGS_FILE):
        with open(BOOKINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_booking(city, date_str, time, name, phone, user_id, event_id=None):
    if len(time) == 2:
        time += ":00"
    data = load_bookings()
    key = f"{date_str}_{city}"
    if key not in data:
        data[key] = {}
    data[key][time] = {
        "name": name,
        "phone": phone,
        "user_id": user_id,
        "event_id": event_id
    }
    with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_booked_slots(city, date_str):
    data = load_bookings()
    key = f"{date_str}_{city}"
    day_bookings = data.get(key, {})

    # Фильтруем только реальные записи (а не заготовки от админа)
    return [
        time for time, info in day_bookings.items()
        if info.get("user_id") != "admin_add"
    ]

def generate_ics_file(name, slot_time, city):
    if len(slot_time) == 2:
        slot_time += ":00"
    now = datetime.now()
    date = now.date()
    dt_start = datetime.strptime(f"{date} {slot_time}", "%Y-%m-%d %H:%M")
    dt_end = dt_start + timedelta(hours=1)
    filename = f"calendar_{name}.ics"
    ics = f"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:Демонстрация оборудования
DTSTART;TZID=Europe/Moscow:{dt_start.strftime('%Y%m%dT%H%M%S')}
DTEND;TZID=Europe/Moscow:{dt_end.strftime('%Y%m%dT%H%M%S')}
LOCATION:{'Москва' if city == 'msk' else 'Краснодар'}
DESCRIPTION:Вы записаны на демонстрацию от СНК Лазер
END:VEVENT
END:VCALENDAR"""
    with open(filename, "w", encoding="utf-8") as f:
        f.write(ics)
    return filename

def get_week_dates(offset=0, city=None):
    today = datetime.today().date()
    start = today + timedelta(weeks=offset)
    dates = []
    all_days = [start + timedelta(days=i) for i in range(7)]

    data = load_bookings()
    available_days = set()

    if city:
        for d in all_days:
            key = f"{d.strftime('%Y-%m-%d')}_{city}"
            if key in data and data[key]:  # есть хотя бы один слот
                available_days.add(d)

    for d in all_days:
        if d in available_days or d.weekday() < 5:
            dates.append(d)

    return dates

async def show_date_keyboard(message_or_callback, state: FSMContext, offset=0):
    data = await state.get_data()
    city = data.get("city")
    dates = get_week_dates(offset, city=city)
    kb = InlineKeyboardMarkup(row_width=2)
    for d in dates:
        label = d.strftime("%d.%m")
        callback_data = f"date:{d.strftime('%Y-%m-%d')}"
        kb.insert(InlineKeyboardButton(label, callback_data=callback_data))

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ Предыдущая", callback_data="date_nav:prev"))
    nav.append(InlineKeyboardButton("➡️ Следующая", callback_data="date_nav:next"))
    kb.row(*nav)

    await state.update_data(week_offset=offset)

    data = await state.get_data()
    msg_id = data.get("last_bot_msg_id")

    try:
        if isinstance(message_or_callback, types.CallbackQuery):
            await message_or_callback.message.edit_text("📅 Выберите дату:", reply_markup=kb)
        elif isinstance(message_or_callback, types.Message) and msg_id:
            await bot.edit_message_text(
                chat_id=message_or_callback.chat.id,
                message_id=msg_id,
                text="📅 Выберите дату:",
                reply_markup=kb
            )
        else:
            await message_or_callback.answer("📅 Выберите дату:", reply_markup=kb)
    except Exception as e:
        logging.warning(f"[show_date_keyboard] редактирование не удалось: {e}")
        await send_clean_message(state, message_or_callback.chat.id, "📅 Выберите дату:", reply_markup=kb
        )
    except Exception as e:
        logging.error(f"Ошибка при отображении клавиатуры дат: {e}")

async def send_clean_message(target, chat_id: int = None, text: str = None, reply_markup=None):
    if isinstance(target, FSMContext):
        state = target
        data = await state.get_data()
        msg_id = data.get("last_bot_msg_id")
        chat_id = data.get("chat_id") if "chat_id" in data else chat_id
    else:
        state = None
        msg_id = last_messages.get(target)
        chat_id = target

    # Удаляем предыдущее сообщение
    if msg_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            logging.info(f"[send_clean_message] Удалено сообщение {msg_id}")
        except Exception as e:
            logging.warning(f"[send_clean_message] Не удалось удалить {msg_id}: {e}")

    # Отправляем новое сообщение
    msg = await bot.send_message(chat_id, text, reply_markup=reply_markup)

    # Сохраняем ID последнего сообщения в FSM и глобально
    if state:
        await state.update_data(last_bot_msg_id=msg.message_id)
    last_messages[chat_id] = msg.message_id  # ✅ Всегда

    logging.info(f"[send_clean_message] Отправлено новое сообщение {msg.message_id}")

@dp.callback_query_handler(lambda c: c.data == "admin:panel", state="*")
async def admin_panel(callback_query: types.CallbackQuery, state: FSMContext):
    current_state = await state.get_state()

    if current_state is not None:
        kb = InlineKeyboardMarkup().add(
            InlineKeyboardButton("↩ Вернуться в начало", callback_data="admin:go_start")
        )
        await callback_query.message.edit_text(
            "❗ Админка доступна только в начале. Завершите текущую запись или нажмите /start.",
            reply_markup=kb
        )
        return

    # ✅ Если всё ок — показываем панель
    await callback_query.answer()
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📋 Записи по датам", callback_data="admin:view_dates"),
        InlineKeyboardButton("🧾 Удалить по одной", callback_data="admin:delete_select"),
        InlineKeyboardButton("🗑 Очистить Москву", callback_data="admin:clear_msk"),
        InlineKeyboardButton("🗑 Очистить Краснодар", callback_data="admin:clear_kras"),
        InlineKeyboardButton("➕ Добавить слот", callback_data="admin:add_slot"),
        InlineKeyboardButton("⬅️ Назад", callback_data="back:to_start")
    )
    await callback_query.message.edit_text("🛠 Админ-панель", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data in ["admin:clear_msk", "admin:clear_kras"])
async def confirm_city_clear(callback_query: types.CallbackQuery):
    city = "Москва" if "msk" in callback_query.data else "Краснодар"
    code = "msk" if "msk" in callback_query.data else "kras"

    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("❌ Подтвердить удаление", callback_data=f"admin:clear_city:{code}"),
        InlineKeyboardButton("⬅️ Отмена", callback_data="admin:panel")
    )
    await callback_query.message.edit_text(f"⚠️ Удалить все записи в городе {city}?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("admin:clear_city:"))
async def clear_city_bookings(callback_query: types.CallbackQuery, state: FSMContext):
    city_code = callback_query.data.split(":")[2]
    await callback_query.answer()

    data = load_bookings()
    keys_to_delete = [k for k in data if k.endswith(f"_{city_code}")]

    from google_calendar import delete_event_from_calendar, MOSCOW_CALENDAR_ID, KRASNODAR_CALENDAR_ID
    calendar_id = MOSCOW_CALENDAR_ID if city_code == "msk" else KRASNODAR_CALENDAR_ID

    for key in keys_to_delete:
        day_data = data.get(key, {})
        for time, info in day_data.items():
            event_id = info.get("event_id")
            if event_id:
                try:
                    delete_event_from_calendar(calendar_id, event_id)
                    logging.info(f"[google] Удалено событие {event_id} из календаря {calendar_id}")
                except Exception as e:
                    logging.warning(f"[google] ❌ Ошибка при удалении события {event_id}: {e}")
        del data[key]

    with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    await callback_query.message.edit_text(f"🗑 Все записи в городе {'Москва' if city_code == 'msk' else 'Краснодар'} удалены.")
    await asyncio.sleep(2)
    await admin_panel(callback_query, state)

@dp.callback_query_handler(lambda c: c.data == "back:to_start")
async def back_to_start(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.finish()

    # Удалим последнее сообщение, даже если FSM нет
    chat_id = callback_query.message.chat.id
    last_msg_id = last_messages.get(chat_id)
    if last_msg_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=last_msg_id)
        except Exception as e:
            logging.warning(f"[back_to_start] Не удалось удалить сообщение: {e}")

    is_admin = callback_query.from_user.id == ADMIN_CHAT_ID
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Да", callback_data="begin:yes"),
        InlineKeyboardButton("❌ Нет", callback_data="begin:no"),
        InlineKeyboardButton("📣 Связаться с нами", callback_data="show:contacts")
    )
    if is_admin:
        kb.insert(InlineKeyboardButton("⚙️ Админка", callback_data="admin:panel"))

    await send_clean_message(state, chat_id, "👋 Хотите записаться на демонстрацию?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "admin:view_dates")
async def admin_view_dates(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    data = load_bookings()

    if not data:
        await callback_query.message.edit_text("📋 Пока нет ни одной записи.\n\n🔙 Возвращаюсь в админку...")
        await asyncio.sleep(2)
        await admin_panel(callback_query, state)
        return

    lines = ["📅 Записи по датам:"]
    for key in sorted(data.keys()):
        date_str, city_code = key.split("_")
        try:
            formatted = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m")
        except:
            formatted = date_str
        city = "Москва" if city_code == "msk" else "Краснодар"

        # ✅ Считаем только реальные записи
        real_bookings = [
            t for t, info in data[key].items()
            if info and info.get("user_id") and info.get("user_id") != "admin_add"
        ]
        count = len(real_bookings)

        lines.append(f"• {formatted} — {city}: {count} чел.")

    lines.append("\n⬅️ Нажмите /start для возврата")
    await callback_query.message.edit_text("\n".join(lines))

@dp.callback_query_handler(lambda c: c.data == "admin:clear_confirm")
async def admin_clear_confirm(callback_query: types.CallbackQuery):
    await callback_query.answer()
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("❌ Подтвердить удаление", callback_data="admin:clear_all"),
        InlineKeyboardButton("⬅️ Отмена", callback_data="admin:panel")
    )
    await callback_query.message.edit_text("⚠️ Вы уверены, что хотите удалить все записи?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "admin:clear_all")
async def admin_clear_all(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()

    if os.path.exists(BOOKINGS_FILE):
        os.remove(BOOKINGS_FILE)

    await callback_query.message.edit_text("🗑 Все записи удалены.\n\n🔙 Возвращаюсь в админку...")
    await asyncio.sleep(2)
    await admin_panel(callback_query, state)

@dp.callback_query_handler(lambda c: c.data == "admin:delete_select")
async def admin_delete_select(callback_query: types.CallbackQuery):
    await callback_query.answer()
    data = load_bookings()

    if not data:
        await callback_query.message.edit_text("📋 Нет записей для удаления.\n\n🔙 Возвращаюсь в админку...")
        await asyncio.sleep(2)
        state = dp.current_state(chat=callback_query.message.chat.id, user=callback_query.from_user.id)
        await admin_panel(callback_query, state)
        return

    kb = InlineKeyboardMarkup(row_width=1)
    for key in sorted(data.keys()):
        date_str, city = key.split("_")
        try:
            formatted_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m")
        except:
            formatted_date = date_str

        label = f"{formatted_date} — {'Москва' if city == 'msk' else 'Краснодар'}"
        kb.add(InlineKeyboardButton(label, callback_data=f"admin:deldate:{key}"))

    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="admin:panel"))
    await callback_query.message.edit_text("📅 Выберите дату для удаления записей:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("admin:deldate:"))
async def admin_choose_record(callback_query: types.CallbackQuery):
    await callback_query.answer()
    key = callback_query.data.split(":", 2)[2]  # формат YYYY-MM-DD_msk

    data = load_bookings()
    day_data = data.get(key)

    if not day_data:
        await callback_query.message.edit_text("😕 На эту дату записей нет.\n\n🔙 Возвращаюсь в админку...")
        await asyncio.sleep(2)
        state = dp.current_state(chat=callback_query.message.chat.id, user=callback_query.from_user.id)
        await admin_panel(callback_query, state)
        return

    kb = InlineKeyboardMarkup(row_width=1)
    for time, info in day_data.items():
        label = f"{time} — {info['name']} ({info['phone']})"
        kb.add(InlineKeyboardButton(label, callback_data=f"admin:deluser:{key}:{time}"))

    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="admin:delete_select"))
    await callback_query.message.edit_text("👤 Выберите запись для удаления:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("admin:deluser:"))
async def admin_delete_user(callback_query: types.CallbackQuery):
    await callback_query.answer()
    _, _, key, time = callback_query.data.split(":", 3)

    data = load_bookings()
    day_data = data.get(key)
    city = key.split("_")[1]  # msk или kras

    if day_data and time in day_data:
        info = day_data[time]
        event_id = info.get("event_id")

        # Удаляем из Google Календаря
        if event_id:
            calendar_id = MOSCOW_CALENDAR_ID if city == "msk" else KRASNODAR_CALENDAR_ID
            try:
                delete_event_from_calendar(calendar_id, event_id)
                logging.info(f"✅ Событие {event_id} удалено из календаря {calendar_id}")
            except Exception as e:
                logging.warning(f"❌ Не удалось удалить событие из календаря: {e}")

        # Удаляем слот
        deleted = day_data.pop(time)

        # Удаляем ключ даты, если слоты закончились
        if not day_data:
            data.pop(key)

        # Сохраняем обновлённые данные
        with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # Если ещё остались записи на эту дату
        if key in data:
            kb = InlineKeyboardMarkup(row_width=1)
            for t, info in data[key].items():
                label = f"{t} — {info['name']} ({info['phone']})"
                kb.add(InlineKeyboardButton(label, callback_data=f"admin:deluser:{key}:{t}"))
            kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="admin:delete_select"))

            await callback_query.message.edit_text(
                f"🗑 Удалено:\n{time} — {deleted['name']} ({deleted['phone']})\n\n👤 Выберите следующую запись для удаления:",
                reply_markup=kb
            )
        else:
            await callback_query.message.edit_text(
                f"🗑 Удалено:\n{time} — {deleted['name']} ({deleted['phone']})\n\n📅 На этой дате больше нет записей. Возвращаюсь назад..."
            )
            await asyncio.sleep(2)
            await admin_delete_select(callback_query)
    else:
        await callback_query.message.edit_text("😕 Запись не найдена.")

@dp.callback_query_handler(lambda c: c.data == "show:contacts")
async def show_socials(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()

    text = "📣 Свяжитесь с нами или подпишитесь в соцсетях:"
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📷 Instagram", url="https://www.instagram.com/snk_laser/"),
        InlineKeyboardButton("🌐 Сайт", url="https://snklaser.ru"),
        InlineKeyboardButton("📺 YouTube", url="https://www.youtube.com/@snklaser"),
        InlineKeyboardButton("📘 VK", url="https://vk.com/snk_laser"),
        InlineKeyboardButton("💬 Telegram", url="https://t.me/snk_laser"),
        InlineKeyboardButton("📞 WhatsApp", url="https://wa.me/79951530030"),
        InlineKeyboardButton("⬅️ Назад", callback_data="back:to_start")
    )

    await send_clean_message(callback_query.message.chat.id, text=text, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("date_nav:"), state=BookingStates.waiting_for_date)
async def handle_date_nav(callback_query: types.CallbackQuery, state: FSMContext):
    direction = callback_query.data.split(":")[1]
    data = await state.get_data()
    logging.info(f"[show_socials] last_bot_msg_id = {data.get('last_bot_msg_id')}")
    offset = data.get("week_offset", 0)
    if direction == "next":
        offset += 1
    elif direction == "prev":
        offset = max(0, offset - 1)
    await show_date_keyboard(callback_query, state, offset)

@dp.callback_query_handler(lambda c: c.data.startswith("date:"), state=BookingStates.waiting_for_date)
async def process_date(callback_query: types.CallbackQuery, state: FSMContext):
    date_str = callback_query.data.split(":")[1]
    await state.update_data(date=date_str)

    data = await state.get_data()
    city = data.get("city")

    bookings = load_bookings()
    key = f"{date_str}_{city}"
    day_slots = bookings.get(key, {})

    free_slots = []

    # Добавляем стандартные слоты, если они не заняты
    for slot in available_slots:
        if slot not in day_slots:
            free_slots.append(slot)
        elif not day_slots[slot].get("user_id") or day_slots[slot].get("user_id") == "admin_add":
            free_slots.append(slot)

    # Добавляем вручную добавленные слоты, если они не заняты
    for slot, info in day_slots.items():
        if slot not in free_slots and (not info.get("user_id") or info.get("user_id") == "admin_add"):
            free_slots.append(slot)

    # Если свободных слотов нет — показываем ошибку и возвращаем выбор даты
    if not free_slots:
        await callback_query.answer("⛔ Нет доступных слотов на эту дату.")
        await send_clean_message(state, callback_query.message.chat.id, "😕 На эту дату нет свободных слотов. Попробуйте выбрать другую дату.")
        await BookingStates.waiting_for_date.set()
        await show_date_keyboard(callback_query, state)
        return

    # Показываем слоты
    kb = InlineKeyboardMarkup(row_width=2)
    for slot in sorted(free_slots):
        kb.insert(InlineKeyboardButton(slot, callback_data=f"slot:{slot}"))
    kb.row(InlineKeyboardButton("⬅️ Назад", callback_data="back:to_dates"))

    await callback_query.answer()
    await send_clean_message(state, callback_query.message.chat.id, "🕒 Выберите свободный слот:", reply_markup=kb)
    await BookingStates.waiting_for_slot.set()

@dp.callback_query_handler(lambda c: c.data == "back:to_dates", state=BookingStates.waiting_for_slot)
async def back_to_dates(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await BookingStates.waiting_for_date.set()
    await show_date_keyboard(callback_query, state)

@dp.callback_query_handler(lambda c: c.data.startswith("slot:"), state=BookingStates.waiting_for_slot)
async def process_slot(callback_query: types.CallbackQuery, state: FSMContext):
    slot_time = callback_query.data.split(":")[1]
    if len(slot_time) == 2:
        slot_time += ":00"

    data = await state.get_data()
    city_code = data.get("city")
    date_str = data.get("date")
    
    # Проверяем, не занят ли слот
    if slot_time in get_booked_slots(city_code, date_str):
        await callback_query.answer()
        await callback_query.message.answer("⛔ Этот слот уже занят. Пожалуйста, выберите другой.")
        return

    await callback_query.answer()
    await state.update_data(slot=slot_time)

    # Сохраняем бронь
    name = data.get("name")
    phone = data.get("phone")
    user_id = callback_query.from_user.id
    save_booking(city_code, date_str, slot_time, name, phone, user_id)

    from google_calendar import add_event_to_calendar

    # Добавление в Google Календарь
    add_event_to_calendar(name, phone, city_code, date_str, slot_time)

    formatted_time = datetime.strptime(slot_time, "%H:%M").strftime("%H:%M")
    date_text = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %d %B")

    msg_text = (
        f"📝 Новая запись:\n"
        f"👤 Имя: {name}\n"
        f"📞 Телефон: {phone}\n"
        f"🏙️ Город: {'Москва' if city_code == 'msk' else 'Краснодар'}\n"
        f"📅 Дата: {date_text}\n"
        f"🕒 Время: {formatted_time}"
    )

    # Отправка администратору — исправленный вызов
    await send_clean_message(ADMIN_CHAT_ID, text=msg_text)

    # Подтверждение пользователю
    await send_clean_message(state, callback_query.message.chat.id, "🎉 Вы успешно записались! Мы ждём вас на демонстрации.")

    # Предложение добавить в календарь
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📆 Добавить в календарь", callback_data="calendar:add"),
        InlineKeyboardButton("⛔ Пропустить", callback_data="calendar:skip")
    )
    await callback_query.message.answer("Хотите добавить событие в календарь?", reply_markup=kb)

    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "calendar:add")
async def send_calendar(callback_query: types.CallbackQuery, state: FSMContext):
    bookings = load_bookings()
    user_id = callback_query.from_user.id

    for key in bookings:
        for time, data in bookings[key].items():
            if data.get("user_id") == user_id:
                city = key.split("_")[1]
                filename = generate_ics_file(data["name"], time, city)

                await bot.send_document(user_id, InputFile(filename))
                os.remove(filename)

                # ⏳ Пауза 5 секунд
                await asyncio.sleep(5)

                # ✅ Подтверждение
                await send_clean_message(state, callback_query.message.chat.id, "🎉 Вы успешно записались! Мы ждём вас на демонстрации.")

                # ✉️ Дополнительное сообщение
                await callback_query.message.answer("Если бот понадобится снова — просто нажмите /start")

                # 🧹 Завершение FSM-сессии
                await state.finish()
                return

    await callback_query.message.answer("😕 Не удалось найти запись для календаря.")

@dp.callback_query_handler(lambda c: c.data == "calendar:skip")
async def skip_calendar(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await callback_query.message.edit_text("Хорошо, запись завершена. Если передумаете — просто нажмите /start 😊")

async def send_reminders():
    REMINDER_TIMES = {
        "1day": timedelta(days=1),
        "2hour": timedelta(hours=2)
    }

    while True:
        try:
            now = datetime.now()
            bookings = load_bookings()
            updated = False

            for key, slots in bookings.items():
                date_str, city = key.split("_")

                for time_str, info in slots.items():
                    # Пропускаем пустые или неправильные записи
                    if not info or not isinstance(info, dict):
                        continue

                    user_id = info.get("user_id")
                    name = info.get("name")
                    phone = info.get("phone")

                    if not user_id or user_id == "admin_add":
                        continue

                    # Проверка корректного времени
                    if len(time_str) == 2:
                        time_str += ":00"
                    try:
                        slot_time = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                    except ValueError:
                        logging.warning(f"[НАПОМИНАНИЕ] Некорректный формат времени: {time_str}")
                        continue

                    # Проверяем, отправлялись ли уже напоминания
                    reminded = info.get("reminded", {"1day": False, "2hour": False})

                    for label, delta in REMINDER_TIMES.items():
                        time_until = slot_time - now
                        if not reminded.get(label) and timedelta(0) <= time_until <= delta + timedelta(minutes=1):
                            try:
                                reminder_text = (
                                    f"🔔 Напоминание: демонстрация начнется в {slot_time.strftime('%H:%M')} "
                                    f"— уже через {'2 часа' if label == '2hour' else 'день'}!"
                                )

                                await send_clean_message(user_id, reminder_text)
                                reminded[label] = True
                                info["reminded"] = reminded
                                updated = True

                                logging.info(f"[НАПОМИНАНИЕ] {label} отправлено → {user_id} на {time_str}")
                            except Exception as e:
                                logging.warning(f"❌ Не удалось отправить напоминание {label} пользователю {user_id}: {e}")

            if updated:
                with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
                    json.dump(bookings, f, ensure_ascii=False, indent=2)

            await asyncio.sleep(60)

        except Exception as e:
            logging.error(f"Ошибка в send_reminders: {e}")
            await asyncio.sleep(30)

@dp.callback_query_handler(lambda c: c.data == "begin:no")
async def handle_no(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await callback_query.message.answer("Хорошо, если передумаете — просто нажмите /start")

@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()

    is_admin = message.from_user.id == ADMIN_CHAT_ID

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Да", callback_data="begin:yes"),
        InlineKeyboardButton("❌ Нет", callback_data="begin:no"),
        InlineKeyboardButton("📣 Связаться с нами", callback_data="show:contacts")
    )
    if is_admin:
        kb.insert(InlineKeyboardButton("⚙️ Админка", callback_data="admin:panel"))

    await send_clean_message(state, message.chat.id, "👋 Добро пожаловать! Хотите записаться на демонстрацию?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "begin:yes")
async def handle_yes(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("Москва", callback_data="city:msk"),
        InlineKeyboardButton("Краснодар", callback_data="city:kras")
    )
    await send_clean_message(state, callback_query.message.chat.id, "🏙️ Выберите город:", reply_markup=kb)
    await BookingStates.waiting_for_city.set()

@dp.message_handler(state=BookingStates.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if not re.fullmatch(r"[а-яА-ЯёЁa-zA-Z\- ]+", name):
        await send_clean_message(state, message.chat.id, "❗ Имя может содержать только буквы. Попробуйте снова:")
        return
    await state.update_data(name=name)
    await send_clean_message(state, message.chat.id, "📞 Введите номер телефона (начинается с 8 или +7, 10 цифр после)")
    await BookingStates.waiting_for_phone.set()

    try:
        await message.delete()
    except:
        pass

    data = await state.get_data()
    msg_id = data.get("last_bot_msg_id")

    try:
        msg = await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=msg_id,
            text="📞 Введите номер телефона (начинается с 8 или +7, 10 цифр после)"
        )
    except:
        await send_clean_message(state, message.chat.id, "📞 Введите номер телефона (начинается с 8 или +7, 10 цифр после)")

    await BookingStates.waiting_for_phone.set()

@dp.message_handler(state=BookingStates.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip().replace(" ", "")
    if not re.fullmatch(r"^(\+7|8)[0-9]{10}$", phone):
        await send_clean_message(state, message.chat.id, "❗ Неверный формат телефона. Попробуйте снова:")
        return

    await state.update_data(phone=phone)
    await state.update_data(week_offset=0)

    try:
        await message.delete()
    except:
        pass

    data = await state.get_data()
    msg_id = data.get("last_bot_msg_id")

    try:
        msg = await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=msg_id,
            text="📅 Выберите дату:"
        )
    except:
        await send_clean_message(state, message_or_callback.message.chat.id, "📅 Выберите дату:", reply_markup=kb)

    await state.update_data(last_bot_msg_id=msg.message_id)

    # ✅ Главное: всегда переход к следующему состоянию
    await BookingStates.waiting_for_phone.set()
    
    # Добавляем логирование текущего состояния
    logging.info(f"User {message.from_user.id} ввёл телефон: {phone}")
    
    # вызываем функцию показа клавиатуры дат с await
    await BookingStates.waiting_for_date.set()
    await show_date_keyboard(message, state, offset=0)

@dp.callback_query_handler(lambda c: c.data == "admin:add_slot")
async def admin_add_slot_start(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("Москва", callback_data="admin:slot_city:msk"),
        InlineKeyboardButton("Краснодар", callback_data="admin:slot_city:kras")
    )
    await send_clean_message(state, callback_query.message.chat.id, "🏙️ Выберите город для слота:", reply_markup=kb)
    await AdminStates.choosing_city.set()

@dp.callback_query_handler(lambda c: c.data.startswith("admin:slot_city:"), state=AdminStates.choosing_city)
async def admin_slot_choose_city(callback_query: types.CallbackQuery, state: FSMContext):
    city = callback_query.data.split(":")[2]
    await state.update_data(slot_city=city, week_offset=0)
    await AdminStates.choosing_date.set()
    await show_slot_date_keyboard(callback_query, state)

async def show_slot_date_keyboard(callback_query, state: FSMContext, offset=0):
    today = datetime.today().date()
    start = today + timedelta(weeks=offset)
    dates = [start + timedelta(days=i) for i in range(7)]

    kb = InlineKeyboardMarkup(row_width=2)
    for d in dates:
        label = d.strftime("%d.%m (%a)")
        callback_data = f"admin:slot_date:{d.strftime('%Y-%m-%d')}"
        kb.insert(InlineKeyboardButton(label, callback_data=callback_data))

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ Назад", callback_data="admin:slot_nav:prev"))
    nav.append(InlineKeyboardButton("➡️ Далее", callback_data="admin:slot_nav:next"))
    kb.row(*nav)

    await state.update_data(week_offset=offset)
    await send_clean_message(state, callback_query.message.chat.id, "📅 Выберите дату для нового слота:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("admin:slot_nav:"), state=AdminStates.choosing_date)
async def slot_date_nav(callback_query: types.CallbackQuery, state: FSMContext):
    direction = callback_query.data.split(":")[2]
    data = await state.get_data()
    offset = data.get("week_offset", 0)
    if direction == "next":
        offset += 1
    elif direction == "prev":
        offset = max(0, offset - 1)
    await show_slot_date_keyboard(callback_query, state, offset)

@dp.callback_query_handler(lambda c: c.data.startswith("admin:slot_date:"), state=AdminStates.choosing_date)
async def admin_slot_choose_date(callback_query: types.CallbackQuery, state: FSMContext):
    date = callback_query.data.split(":")[2]
    await state.update_data(slot_date=date)

    data = await state.get_data()
    city = data.get("slot_city")

    # Загружаем занятые слоты на эту дату
    booked = get_booked_slots(city, date)
    if booked:
        slots_text = "\n".join([f"• {s}" for s in sorted(booked)])
        text = f"📅 Уже занятые слоты на {date}:\n{slots_text}\n\n🕒 Введите новое время в формате ЧЧ:ММ:"
    else:
        text = "✅ Пока свободно.\n\n🕒 Введите новое время в формате ЧЧ:ММ:"

    await send_clean_message(state, callback_query.message.chat.id, text)
    await AdminStates.entering_time.set()

@dp.message_handler(state=AdminStates.entering_time)
async def admin_slot_enter_time(message: types.Message, state: FSMContext):
    time_text = message.text.strip()
    try:
        datetime.strptime(time_text, "%H:%M")
    except ValueError:
        await message.answer("❗ Неверный формат. Введите время как ЧЧ:ММ (например, 14:30)")
        return

    data = await state.get_data()
    city = data["slot_city"]
    date = data["slot_date"]

    # сохраняем "пустой слот" в BOOKINGS_FILE, если ещё не занят
    bookings = load_bookings()
    key = f"{date}_{city}"
    if key not in bookings:
        bookings[key] = {}
    if time_text in bookings[key]:
        kb = InlineKeyboardMarkup()
        kb.add(
            InlineKeyboardButton("🔁 Попробовать другое время", callback_data="admin:slot_repeat_time"),
            InlineKeyboardButton("⬅️ Назад", callback_data="admin:panel")
        )
        await send_clean_message(state, message.chat.id, f"⚠️ Слот {time_text} уже существует.", reply_markup=kb)
        return
    else:
        bookings[key][time_text] = {}
        with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(bookings, f, ensure_ascii=False, indent=2)

        kb = InlineKeyboardMarkup()
        kb.add(
            InlineKeyboardButton("➕ Добавить ещё", callback_data="admin:add_another"),
            InlineKeyboardButton("⬅️ Назад", callback_data="admin:panel")
        )
        await send_clean_message(state, message.chat.id, f"✅ Слот {time_text} добавлен на {date} ({'Москва' if city == 'msk' else 'Краснодар'})", reply_markup=kb)

    # НЕ вызываем state.finish() — остаёмся в состоянии AdminStates.entering_time

@dp.callback_query_handler(lambda c: c.data == "admin:add_another", state="*")
async def admin_add_another(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()

    # Восстанавливаем город и дату (если не удалились после state.finish())
    data = await state.get_data()
    city = data.get("slot_city")
    date = data.get("slot_date")

    if not city or not date:
        await send_clean_message(state, callback_query.message.chat.id, "⚠️ Ошибка: не удалось восстановить город или дату. Пожалуйста, начните заново.")
        await state.finish()
        return

    # Повторно сохраняем данные в FSM (на случай потери)
    await state.update_data(slot_city=city, slot_date=date)

    await send_clean_message(state, callback_query.message.chat.id, "🕒 Введите время в формате ЧЧ:ММ:")
    await AdminStates.entering_time.set()

@dp.callback_query_handler(lambda c: c.data == "admin:go_start", state="*")
async def admin_go_start(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.finish()

    is_admin = callback_query.from_user.id == ADMIN_CHAT_ID

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Да", callback_data="begin:yes"),
        InlineKeyboardButton("❌ Нет", callback_data="begin:no"),
        InlineKeyboardButton("📣 Связаться с нами", callback_data="show:contacts")
    )
    if is_admin:
        kb.insert(InlineKeyboardButton("⚙️ Админка", callback_data="admin:panel"))

    await send_clean_message(state, callback_query.message.chat.id, "👋 Добро пожаловать! Хотите записаться на демонстрацию?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "admin:slot_repeat_time", state=AdminStates.entering_time)
async def repeat_slot_time(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await send_clean_message(callback_query.message.chat.id, text="🕒 Введите другое время в формате ЧЧ:ММ:")

@dp.callback_query_handler(lambda c: c.data == "resign:no")
async def handle_resign_no(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.finish()

    is_admin = callback_query.from_user.id == ADMIN_CHAT_ID

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Да", callback_data="begin:yes"),
        InlineKeyboardButton("❌ Нет", callback_data="begin:no"),
        InlineKeyboardButton("📣 Связаться с нами", callback_data="show:contacts")
    )
    if is_admin:
        kb.insert(InlineKeyboardButton("⚙️ Админка", callback_data="admin:panel"))

    await send_clean_message(callback_query.message.chat.id, text="👋 Хотите записаться на демонстрацию?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "resign:yes")
async def proceed_resign(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()

    user_id = callback_query.from_user.id
    data = load_bookings()
    deleted = False

    from google_calendar import delete_event_from_calendar, MOSCOW_CALENDAR_ID, KRASNODAR_CALENDAR_ID

    for key in list(data.keys()):
        city = key.split("_")[1]
        for time in list(data[key].keys()):
            info = data[key][time]
            if info.get("user_id") == user_id:
                event_id = info.get("event_id")
                if event_id:
                    calendar_id = MOSCOW_CALENDAR_ID if city == "msk" else KRASNODAR_CALENDAR_ID
                    try:
                        delete_event_from_calendar(calendar_id, event_id)
                        logging.info(f"[google] Событие {event_id} удалено из календаря {calendar_id}")
                    except Exception as e:
                        logging.warning(f"[google] Ошибка удаления события: {e}")
                        info = data[key][time]
                        event_id = info.get("event_id")
                        city = key.split("_")[1]
                        if event_id:
                            calendar_id = MOSCOW_CALENDAR_ID if city == "msk" else KRASNODAR_CALENDAR_ID
                            try:
                                delete_event_from_calendar(calendar_id, event_id)
                                logging.info(f"Удалено из календаря: {event_id}")
                            except Exception as e:
                                logging.warning(f"❌ Не удалось удалить событие {event_id} из календаря: {e}")
                del data[key][time]
                deleted = True
        if not data[key]:
            del data[key]

    if deleted:
        with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    msg = await callback_query.message.edit_text("Как вас зовут? (только буквы)")
    await state.update_data(last_bot_msg_id=msg.message_id)
    await state.set_state(BookingStates.waiting_for_name.state)

@dp.callback_query_handler(lambda c: c.data.startswith("resign:new:"))
async def create_additional_booking(callback_query: types.CallbackQuery, state: FSMContext):
    city_code = callback_query.data.split(":")[2]
    await callback_query.answer()
    await state.update_data(city=city_code)
    msg = await callback_query.message.edit_text("Как вас зовут? (только буквы)")
    await state.update_data(last_bot_msg_id=msg.message_id)
    await BookingStates.waiting_for_name.set()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(send_reminders())
    executor.start_polling(dp, skip_updates=True)

from aiogram import Bot, Dispatcher, types
from google_calendar import delete_event_from_calendar, MOSCOW_CALENDAR_ID, KRASNODAR_CALENDAR_ID
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from aiogram.utils import executor
from aiogram.dispatcher import FSMContext
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher.filters.state import State, StatesGroup
import logging
import re
import locale
from datetime import datetime, timedelta
import json
import os
import asyncio

locale.setlocale(locale.LC_TIME, "ru_RU.UTF-8")

API_TOKEN = "7684865098:AAFDNDRPBEty31cypZBcthDi9LCKbB8o5rQ"
ADMIN_CHAT_ID = 5887875855

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

class BookingStates(StatesGroup):
    waiting_for_city = State()
    waiting_for_name = State()
    waiting_for_phone = State()
    waiting_for_date = State()
    waiting_for_slot = State()

class AdminStates(StatesGroup):
    choosing_city = State()
    choosing_date = State()
    entering_time = State()

available_slots = ["12:00", "14:00", "16:00"]
BOOKINGS_FILE = "bookings.json"

# Создание файла при запуске, если он отсутствует
if not os.path.exists(BOOKINGS_FILE):
    with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f, ensure_ascii=False, indent=2)

# Для хранения последних сообщений пользователей (даже без FSM)
last_messages = {}

@dp.callback_query_handler(lambda c: c.data.startswith("city:"), state=BookingStates.waiting_for_city)
async def process_city(callback_query: types.CallbackQuery, state: FSMContext):
    city_code = callback_query.data.split(":")[1]
    user_id = callback_query.from_user.id
    await callback_query.answer()

    # Проверка на существующую запись
    data = load_bookings()
    for key in data:
        for time, info in data[key].items():
            if info.get("user_id") == user_id:
                kb = InlineKeyboardMarkup(row_width=1)
                kb.add(
                    InlineKeyboardButton("✅ Перезаписаться", callback_data="resign:yes"),
                    InlineKeyboardButton("🚫 Оставить как есть", callback_data="resign:no"),
                    InlineKeyboardButton("➕ Новая запись", callback_data=f"resign:new:{city_code}")
                )
                await send_clean_message(state, callback_query.message.chat.id, "📌 У вас уже есть запись. Что сделать?", reply_markup=kb)
                await state.finish()
                return

    # Если записи нет — идём дальше
    await state.update_data(city=city_code)
    msg = await callback_query.message.edit_text("Как вас зовут? (только буквы)")
    await state.update_data(last_bot_msg_id=msg.message_id)
    await BookingStates.waiting_for_name.set()

# ========== СЛОТЫ И ДАТЫ ==========
def load_bookings():
    if os.path.exists(BOOKINGS_FILE):
        with open(BOOKINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_booking(city, date_str, time, name, phone, user_id, event_id=None):
    if len(time) == 2:
        time += ":00"
    data = load_bookings()
    key = f"{date_str}_{city}"
    if key not in data:
        data[key] = {}
    data[key][time] = {
        "name": name,
        "phone": phone,
        "user_id": user_id,
        "event_id": event_id
    }
    with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_booked_slots(city, date_str):
    data = load_bookings()
    key = f"{date_str}_{city}"
    day_bookings = data.get(key, {})

    # Фильтруем только реальные записи (а не заготовки от админа)
    return [
        time for time, info in day_bookings.items()
        if info.get("user_id") != "admin_add"
    ]

def generate_ics_file(name, slot_time, city):
    if len(slot_time) == 2:
        slot_time += ":00"
    now = datetime.now()
    date = now.date()
    dt_start = datetime.strptime(f"{date} {slot_time}", "%Y-%m-%d %H:%M")
    dt_end = dt_start + timedelta(hours=1)
    filename = f"calendar_{name}.ics"
    ics = f"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:Демонстрация оборудования
DTSTART;TZID=Europe/Moscow:{dt_start.strftime('%Y%m%dT%H%M%S')}
DTEND;TZID=Europe/Moscow:{dt_end.strftime('%Y%m%dT%H%M%S')}
LOCATION:{'Москва' if city == 'msk' else 'Краснодар'}
DESCRIPTION:Вы записаны на демонстрацию от СНК Лазер
END:VEVENT
END:VCALENDAR"""
    with open(filename, "w", encoding="utf-8") as f:
        f.write(ics)
    return filename

def get_week_dates(offset=0, city=None):
    today = datetime.today().date()
    start = today + timedelta(weeks=offset)
    dates = []
    all_days = [start + timedelta(days=i) for i in range(7)]

    data = load_bookings()
    available_days = set()

    if city:
        for d in all_days:
            key = f"{d.strftime('%Y-%m-%d')}_{city}"
            if key in data and data[key]:  # есть хотя бы один слот
                available_days.add(d)

    for d in all_days:
        if d in available_days or d.weekday() < 5:
            dates.append(d)

    return dates

async def show_date_keyboard(message_or_callback, state: FSMContext, offset=0):
    data = await state.get_data()
    city = data.get("city")
    dates = get_week_dates(offset, city=city)
    kb = InlineKeyboardMarkup(row_width=2)
    for d in dates:
        label = d.strftime("%d.%m")
        callback_data = f"date:{d.strftime('%Y-%m-%d')}"
        kb.insert(InlineKeyboardButton(label, callback_data=callback_data))

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ Предыдущая", callback_data="date_nav:prev"))
    nav.append(InlineKeyboardButton("➡️ Следующая", callback_data="date_nav:next"))
    kb.row(*nav)

    await state.update_data(week_offset=offset)

    data = await state.get_data()
    msg_id = data.get("last_bot_msg_id")

    try:
        if isinstance(message_or_callback, types.CallbackQuery):
            await message_or_callback.message.edit_text("📅 Выберите дату:", reply_markup=kb)
        elif isinstance(message_or_callback, types.Message) and msg_id:
            await bot.edit_message_text(
                chat_id=message_or_callback.chat.id,
                message_id=msg_id,
                text="📅 Выберите дату:",
                reply_markup=kb
            )
        else:
            await message_or_callback.answer("📅 Выберите дату:", reply_markup=kb)
    except Exception as e:
        logging.warning(f"[show_date_keyboard] редактирование не удалось: {e}")
        await send_clean_message(state, message_or_callback.chat.id, "📅 Выберите дату:", reply_markup=kb
        )
    except Exception as e:
        logging.error(f"Ошибка при отображении клавиатуры дат: {e}")

async def send_clean_message(target, chat_id: int = None, text: str = None, reply_markup=None):
    if isinstance(target, FSMContext):
        state = target
        data = await state.get_data()
        msg_id = data.get("last_bot_msg_id")
        chat_id = data.get("chat_id") if "chat_id" in data else chat_id
    else:
        state = None
        msg_id = last_messages.get(target)
        chat_id = target

    # Удаляем предыдущее сообщение
    if msg_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            logging.info(f"[send_clean_message] Удалено сообщение {msg_id}")
        except Exception as e:
            logging.warning(f"[send_clean_message] Не удалось удалить {msg_id}: {e}")

    # Отправляем новое сообщение
    msg = await bot.send_message(chat_id, text, reply_markup=reply_markup)

    # Сохраняем ID последнего сообщения в FSM и глобально
    if state:
        await state.update_data(last_bot_msg_id=msg.message_id)
    last_messages[chat_id] = msg.message_id  # ✅ Всегда

    logging.info(f"[send_clean_message] Отправлено новое сообщение {msg.message_id}")

@dp.callback_query_handler(lambda c: c.data == "admin:panel", state="*")
async def admin_panel(callback_query: types.CallbackQuery, state: FSMContext):
    current_state = await state.get_state()

    if current_state is not None:
        kb = InlineKeyboardMarkup().add(
            InlineKeyboardButton("↩ Вернуться в начало", callback_data="admin:go_start")
        )
        await callback_query.message.edit_text(
            "❗ Админка доступна только в начале. Завершите текущую запись или нажмите /start.",
            reply_markup=kb
        )
        return

    # ✅ Если всё ок — показываем панель
    await callback_query.answer()
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📋 Записи по датам", callback_data="admin:view_dates"),
        InlineKeyboardButton("🧾 Удалить по одной", callback_data="admin:delete_select"),
        InlineKeyboardButton("🗑 Очистить Москву", callback_data="admin:clear_msk"),
        InlineKeyboardButton("🗑 Очистить Краснодар", callback_data="admin:clear_kras"),
        InlineKeyboardButton("➕ Добавить слот", callback_data="admin:add_slot"),
        InlineKeyboardButton("⬅️ Назад", callback_data="back:to_start")
    )
    await callback_query.message.edit_text("🛠 Админ-панель", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data in ["admin:clear_msk", "admin:clear_kras"])
async def confirm_city_clear(callback_query: types.CallbackQuery):
    city = "Москва" if "msk" in callback_query.data else "Краснодар"
    code = "msk" if "msk" in callback_query.data else "kras"

    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("❌ Подтвердить удаление", callback_data=f"admin:clear_city:{code}"),
        InlineKeyboardButton("⬅️ Отмена", callback_data="admin:panel")
    )
    await callback_query.message.edit_text(f"⚠️ Удалить все записи в городе {city}?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("admin:clear_city:"))
async def clear_city_bookings(callback_query: types.CallbackQuery, state: FSMContext):
    city_code = callback_query.data.split(":")[2]
    await callback_query.answer()

    data = load_bookings()
    keys_to_delete = [k for k in data if k.endswith(f"_{city_code}")]
    for k in keys_to_delete:
        del data[k]

    with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    await callback_query.message.edit_text(f"🗑 Все записи в городе {'Москва' if city_code == 'msk' else 'Краснодар'} удалены.")
    await asyncio.sleep(2)
    await admin_panel(callback_query, state)

@dp.callback_query_handler(lambda c: c.data == "back:to_start")
async def back_to_start(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.finish()

    # Удалим последнее сообщение, даже если FSM нет
    chat_id = callback_query.message.chat.id
    last_msg_id = last_messages.get(chat_id)
    if last_msg_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=last_msg_id)
        except Exception as e:
            logging.warning(f"[back_to_start] Не удалось удалить сообщение: {e}")

    is_admin = callback_query.from_user.id == ADMIN_CHAT_ID
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Да", callback_data="begin:yes"),
        InlineKeyboardButton("❌ Нет", callback_data="begin:no"),
        InlineKeyboardButton("📣 Связаться с нами", callback_data="show:contacts")
    )
    if is_admin:
        kb.insert(InlineKeyboardButton("⚙️ Админка", callback_data="admin:panel"))

    await send_clean_message(state, chat_id, "👋 Хотите записаться на демонстрацию?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "admin:view_dates")
async def admin_view_dates(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    data = load_bookings()

    if not data:
        await callback_query.message.edit_text("📋 Пока нет ни одной записи.\n\n🔙 Возвращаюсь в админку...")
        await asyncio.sleep(2)
        await admin_panel(callback_query, state)
        return

    lines = ["📅 Записи по датам:"]
    for key in sorted(data.keys()):
        date_str, city_code = key.split("_")
        try:
            formatted = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m")
        except:
            formatted = date_str
        city = "Москва" if city_code == "msk" else "Краснодар"

        # ✅ Считаем только реальные записи
        real_bookings = [
            t for t, info in data[key].items()
            if info and info.get("user_id") and info.get("user_id") != "admin_add"
        ]
        count = len(real_bookings)

        lines.append(f"• {formatted} — {city}: {count} чел.")

    lines.append("\n⬅️ Нажмите /start для возврата")
    await callback_query.message.edit_text("\n".join(lines))

@dp.callback_query_handler(lambda c: c.data == "admin:clear_confirm")
async def admin_clear_confirm(callback_query: types.CallbackQuery):
    await callback_query.answer()
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("❌ Подтвердить удаление", callback_data="admin:clear_all"),
        InlineKeyboardButton("⬅️ Отмена", callback_data="admin:panel")
    )
    await callback_query.message.edit_text("⚠️ Вы уверены, что хотите удалить все записи?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "admin:clear_all")
async def admin_clear_all(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()

    if os.path.exists(BOOKINGS_FILE):
        os.remove(BOOKINGS_FILE)

    await callback_query.message.edit_text("🗑 Все записи удалены.\n\n🔙 Возвращаюсь в админку...")
    await asyncio.sleep(2)
    await admin_panel(callback_query, state)

@dp.callback_query_handler(lambda c: c.data == "admin:delete_select")
async def admin_delete_select(callback_query: types.CallbackQuery):
    await callback_query.answer()
    data = load_bookings()

    if not data:
        await callback_query.message.edit_text("📋 Нет записей для удаления.\n\n🔙 Возвращаюсь в админку...")
        await asyncio.sleep(2)
        state = dp.current_state(chat=callback_query.message.chat.id, user=callback_query.from_user.id)
        await admin_panel(callback_query, state)
        return

    kb = InlineKeyboardMarkup(row_width=1)
    for key in sorted(data.keys()):
        date_str, city = key.split("_")
        try:
            formatted_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m")
        except:
            formatted_date = date_str

        label = f"{formatted_date} — {'Москва' if city == 'msk' else 'Краснодар'}"
        kb.add(InlineKeyboardButton(label, callback_data=f"admin:deldate:{key}"))

    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="admin:panel"))
    await callback_query.message.edit_text("📅 Выберите дату для удаления записей:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("admin:deldate:"))
async def admin_choose_record(callback_query: types.CallbackQuery):
    await callback_query.answer()
    key = callback_query.data.split(":", 2)[2]  # формат YYYY-MM-DD_msk

    data = load_bookings()
    day_data = data.get(key)

    if not day_data:
        await callback_query.message.edit_text("😕 На эту дату записей нет.\n\n🔙 Возвращаюсь в админку...")
        await asyncio.sleep(2)
        state = dp.current_state(chat=callback_query.message.chat.id, user=callback_query.from_user.id)
        await admin_panel(callback_query, state)
        return

    kb = InlineKeyboardMarkup(row_width=1)
    for time, info in day_data.items():
        label = f"{time} — {info['name']} ({info['phone']})"
        kb.add(InlineKeyboardButton(label, callback_data=f"admin:deluser:{key}:{time}"))

    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="admin:delete_select"))
    await callback_query.message.edit_text("👤 Выберите запись для удаления:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("admin:deluser:"))
async def admin_delete_user(callback_query: types.CallbackQuery):
    await callback_query.answer()
    _, _, key, time = callback_query.data.split(":", 3)

    data = load_bookings()
    day_data = data.get(key)
    city = key.split("_")[1]  # msk или kras

    if day_data and time in day_data:
        info = day_data[time]
        event_id = info.get("event_id")

        # Удаляем из Google Календаря
        if event_id:
            calendar_id = MOSCOW_CALENDAR_ID if city == "msk" else KRASNODAR_CALENDAR_ID
            try:
                delete_event_from_calendar(calendar_id, event_id)
                logging.info(f"✅ Событие {event_id} удалено из календаря {calendar_id}")
            except Exception as e:
                logging.warning(f"❌ Не удалось удалить событие из календаря: {e}")

        # Удаляем слот
        deleted = day_data.pop(time)

        # Удаляем ключ даты, если слоты закончились
        if not day_data:
            data.pop(key)

        # Сохраняем обновлённые данные
        with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # Если ещё остались записи на эту дату
        if key in data:
            kb = InlineKeyboardMarkup(row_width=1)
            for t, info in data[key].items():
                label = f"{t} — {info['name']} ({info['phone']})"
                kb.add(InlineKeyboardButton(label, callback_data=f"admin:deluser:{key}:{t}"))
            kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="admin:delete_select"))

            await callback_query.message.edit_text(
                f"🗑 Удалено:\n{time} — {deleted['name']} ({deleted['phone']})\n\n👤 Выберите следующую запись для удаления:",
                reply_markup=kb
            )
        else:
            await callback_query.message.edit_text(
                f"🗑 Удалено:\n{time} — {deleted['name']} ({deleted['phone']})\n\n📅 На этой дате больше нет записей. Возвращаюсь назад..."
            )
            await asyncio.sleep(2)
            await admin_delete_select(callback_query)
    else:
        await callback_query.message.edit_text("😕 Запись не найдена.")

@dp.callback_query_handler(lambda c: c.data == "show:contacts")
async def show_socials(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()

    text = "📣 Свяжитесь с нами или подпишитесь в соцсетях:"
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📷 Instagram", url="https://www.instagram.com/snk_laser/"),
        InlineKeyboardButton("🌐 Сайт", url="https://snklaser.ru"),
        InlineKeyboardButton("📺 YouTube", url="https://www.youtube.com/@snklaser"),
        InlineKeyboardButton("📘 VK", url="https://vk.com/snk_laser"),
        InlineKeyboardButton("💬 Telegram", url="https://t.me/snk_laser"),
        InlineKeyboardButton("📞 WhatsApp", url="https://wa.me/79951530030"),
        InlineKeyboardButton("⬅️ Назад", callback_data="back:to_start")
    )

    await send_clean_message(callback_query.message.chat.id, text=text, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("date_nav:"), state=BookingStates.waiting_for_date)
async def handle_date_nav(callback_query: types.CallbackQuery, state: FSMContext):
    direction = callback_query.data.split(":")[1]
    data = await state.get_data()
    logging.info(f"[show_socials] last_bot_msg_id = {data.get('last_bot_msg_id')}")
    offset = data.get("week_offset", 0)
    if direction == "next":
        offset += 1
    elif direction == "prev":
        offset = max(0, offset - 1)
    await show_date_keyboard(callback_query, state, offset)

@dp.callback_query_handler(lambda c: c.data.startswith("date:"), state=BookingStates.waiting_for_date)
async def process_date(callback_query: types.CallbackQuery, state: FSMContext):
    date_str = callback_query.data.split(":")[1]
    await state.update_data(date=date_str)

    data = await state.get_data()
    city = data.get("city")

    bookings = load_bookings()
    key = f"{date_str}_{city}"
    day_slots = bookings.get(key, {})

    free_slots = []

    # Добавляем стандартные слоты, если они не заняты
    for slot in available_slots:
        if slot not in day_slots:
            free_slots.append(slot)
        elif not day_slots[slot].get("user_id") or day_slots[slot].get("user_id") == "admin_add":
            free_slots.append(slot)

    # Добавляем вручную добавленные слоты, если они не заняты
    for slot, info in day_slots.items():
        if slot not in free_slots and (not info.get("user_id") or info.get("user_id") == "admin_add"):
            free_slots.append(slot)

    # Если свободных слотов нет — показываем ошибку и возвращаем выбор даты
    if not free_slots:
        await callback_query.answer("⛔ Нет доступных слотов на эту дату.")
        await send_clean_message(state, callback_query.message.chat.id, "😕 На эту дату нет свободных слотов. Попробуйте выбрать другую дату.")
        await BookingStates.waiting_for_date.set()
        await show_date_keyboard(callback_query, state)
        return

    # Показываем слоты
    kb = InlineKeyboardMarkup(row_width=2)
    for slot in sorted(free_slots):
        kb.insert(InlineKeyboardButton(slot, callback_data=f"slot:{slot}"))
    kb.row(InlineKeyboardButton("⬅️ Назад", callback_data="back:to_dates"))

    await callback_query.answer()
    await send_clean_message(state, callback_query.message.chat.id, "🕒 Выберите свободный слот:", reply_markup=kb)
    await BookingStates.waiting_for_slot.set()

@dp.callback_query_handler(lambda c: c.data == "back:to_dates", state=BookingStates.waiting_for_slot)
async def back_to_dates(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await BookingStates.waiting_for_date.set()
    await show_date_keyboard(callback_query, state)

@dp.callback_query_handler(lambda c: c.data.startswith("slot:"), state=BookingStates.waiting_for_slot)
async def process_slot(callback_query: types.CallbackQuery, state: FSMContext):
    slot_time = callback_query.data.split(":")[1]
    if len(slot_time) == 2:
        slot_time += ":00"

    data = await state.get_data()
    city_code = data.get("city")
    date_str = data.get("date")
    
    # Проверяем, не занят ли слот
    if slot_time in get_booked_slots(city_code, date_str):
        await callback_query.answer()
        await callback_query.message.answer("⛔ Этот слот уже занят. Пожалуйста, выберите другой.")
        return

    await callback_query.answer()
    await state.update_data(slot=slot_time)

    # Сохраняем бронь
    name = data.get("name")
    phone = data.get("phone")
    user_id = callback_query.from_user.id
    save_booking(city_code, date_str, slot_time, name, phone, user_id)

    from google_calendar import add_event_to_calendar

    # Добавление в Google Календарь
    add_event_to_calendar(name, phone, city_code, date_str, slot_time)

    formatted_time = datetime.strptime(slot_time, "%H:%M").strftime("%H:%M")
    date_text = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %d %B")

    msg_text = (
        f"📝 Новая запись:\n"
        f"👤 Имя: {name}\n"
        f"📞 Телефон: {phone}\n"
        f"🏙️ Город: {'Москва' if city_code == 'msk' else 'Краснодар'}\n"
        f"📅 Дата: {date_text}\n"
        f"🕒 Время: {formatted_time}"
    )

    # Отправка администратору — исправленный вызов
    await send_clean_message(ADMIN_CHAT_ID, text=msg_text)

    # Подтверждение пользователю
    await send_clean_message(state, callback_query.message.chat.id, "🎉 Вы успешно записались! Мы ждём вас на демонстрации.")

    # Предложение добавить в календарь
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📆 Добавить в календарь", callback_data="calendar:add"),
        InlineKeyboardButton("⛔ Пропустить", callback_data="calendar:skip")
    )
    await callback_query.message.answer("Хотите добавить событие в календарь?", reply_markup=kb)

    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "calendar:add")
async def send_calendar(callback_query: types.CallbackQuery, state: FSMContext):
    bookings = load_bookings()
    user_id = callback_query.from_user.id

    for key in bookings:
        for time, data in bookings[key].items():
            if data.get("user_id") == user_id:
                city = key.split("_")[1]
                filename = generate_ics_file(data["name"], time, city)

                await bot.send_document(user_id, InputFile(filename))
                os.remove(filename)

                # ⏳ Пауза 5 секунд
                await asyncio.sleep(5)

                # ✅ Подтверждение
                await send_clean_message(state, callback_query.message.chat.id, "🎉 Вы успешно записались! Мы ждём вас на демонстрации.")

                # ✉️ Дополнительное сообщение
                await callback_query.message.answer("Если бот понадобится снова — просто нажмите /start")

                # 🧹 Завершение FSM-сессии
                await state.finish()
                return

    await callback_query.message.answer("😕 Не удалось найти запись для календаря.")

@dp.callback_query_handler(lambda c: c.data == "calendar:skip")
async def skip_calendar(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await callback_query.message.edit_text("Хорошо, запись завершена. Если передумаете — просто нажмите /start 😊")

async def send_reminders():
    REMINDER_TIMES = {
        "1day": timedelta(days=1),
        "2hour": timedelta(hours=2)
    }

    while True:
        try:
            now = datetime.now()
            bookings = load_bookings()
            updated = False

            for key, slots in bookings.items():
                date_str, city = key.split("_")

                for time_str, info in slots.items():
                    # Пропускаем пустые или неправильные записи
                    if not info or not isinstance(info, dict):
                        continue

                    user_id = info.get("user_id")
                    name = info.get("name")
                    phone = info.get("phone")

                    if not user_id or user_id == "admin_add":
                        continue

                    # Проверка корректного времени
                    if len(time_str) == 2:
                        time_str += ":00"
                    try:
                        slot_time = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                    except ValueError:
                        logging.warning(f"[НАПОМИНАНИЕ] Некорректный формат времени: {time_str}")
                        continue

                    # Проверяем, отправлялись ли уже напоминания
                    reminded = info.get("reminded", {"1day": False, "2hour": False})

                    for label, delta in REMINDER_TIMES.items():
                        time_until = slot_time - now
                        if not reminded.get(label) and timedelta(0) <= time_until <= delta + timedelta(minutes=1):
                            try:
                                reminder_text = (
                                    f"🔔 Напоминание: демонстрация начнется в {slot_time.strftime('%H:%M')} "
                                    f"— уже через {'2 часа' if label == '2hour' else 'день'}!"
                                )

                                await send_clean_message(user_id, reminder_text)
                                reminded[label] = True
                                info["reminded"] = reminded
                                updated = True

                                logging.info(f"[НАПОМИНАНИЕ] {label} отправлено → {user_id} на {time_str}")
                            except Exception as e:
                                logging.warning(f"❌ Не удалось отправить напоминание {label} пользователю {user_id}: {e}")

            if updated:
                with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
                    json.dump(bookings, f, ensure_ascii=False, indent=2)

            await asyncio.sleep(60)

        except Exception as e:
            logging.error(f"Ошибка в send_reminders: {e}")
            await asyncio.sleep(30)

@dp.callback_query_handler(lambda c: c.data == "begin:no")
async def handle_no(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await callback_query.message.answer("Хорошо, если передумаете — просто нажмите /start")

@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()

    is_admin = message.from_user.id == ADMIN_CHAT_ID

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Да", callback_data="begin:yes"),
        InlineKeyboardButton("❌ Нет", callback_data="begin:no"),
        InlineKeyboardButton("📣 Связаться с нами", callback_data="show:contacts")
    )
    if is_admin:
        kb.insert(InlineKeyboardButton("⚙️ Админка", callback_data="admin:panel"))

    await send_clean_message(state, message.chat.id, "👋 Добро пожаловать! Хотите записаться на демонстрацию?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "begin:yes")
async def handle_yes(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("Москва", callback_data="city:msk"),
        InlineKeyboardButton("Краснодар", callback_data="city:kras")
    )
    await send_clean_message(state, callback_query.message.chat.id, "🏙️ Выберите город:", reply_markup=kb)
    await BookingStates.waiting_for_city.set()

@dp.message_handler(state=BookingStates.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if not re.fullmatch(r"[а-яА-ЯёЁa-zA-Z\- ]+", name):
        await send_clean_message(state, message.chat.id, "❗ Имя может содержать только буквы. Попробуйте снова:")
        return
    await state.update_data(name=name)
    await send_clean_message(state, message.chat.id, "📞 Введите номер телефона (начинается с 8 или +7, 10 цифр после)")
    await BookingStates.waiting_for_phone.set()

    try:
        await message.delete()
    except:
        pass

    data = await state.get_data()
    msg_id = data.get("last_bot_msg_id")

    try:
        msg = await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=msg_id,
            text="📞 Введите номер телефона (начинается с 8 или +7, 10 цифр после)"
        )
    except:
        await send_clean_message(state, message.chat.id, "📞 Введите номер телефона (начинается с 8 или +7, 10 цифр после)")

    await BookingStates.waiting_for_phone.set()

@dp.message_handler(state=BookingStates.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip().replace(" ", "")
    if not re.fullmatch(r"^(\+7|8)[0-9]{10}$", phone):
        await send_clean_message(state, message.chat.id, "❗ Неверный формат телефона. Попробуйте снова:")
        return

    await state.update_data(phone=phone)
    await state.update_data(week_offset=0)

    try:
        await message.delete()
    except:
        pass

    data = await state.get_data()
    msg_id = data.get("last_bot_msg_id")

    try:
        msg = await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=msg_id,
            text="📅 Выберите дату:"
        )
    except:
        await send_clean_message(state, message_or_callback.message.chat.id, "📅 Выберите дату:", reply_markup=kb)

    await state.update_data(last_bot_msg_id=msg.message_id)

    # ✅ Главное: всегда переход к следующему состоянию
    await BookingStates.waiting_for_phone.set()
    
    # Добавляем логирование текущего состояния
    logging.info(f"User {message.from_user.id} ввёл телефон: {phone}")
    
    # вызываем функцию показа клавиатуры дат с await
    await BookingStates.waiting_for_date.set()
    await show_date_keyboard(message, state, offset=0)

@dp.callback_query_handler(lambda c: c.data == "admin:add_slot")
async def admin_add_slot_start(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("Москва", callback_data="admin:slot_city:msk"),
        InlineKeyboardButton("Краснодар", callback_data="admin:slot_city:kras")
    )
    await send_clean_message(state, callback_query.message.chat.id, "🏙️ Выберите город для слота:", reply_markup=kb)
    await AdminStates.choosing_city.set()

@dp.callback_query_handler(lambda c: c.data.startswith("admin:slot_city:"), state=AdminStates.choosing_city)
async def admin_slot_choose_city(callback_query: types.CallbackQuery, state: FSMContext):
    city = callback_query.data.split(":")[2]
    await state.update_data(slot_city=city, week_offset=0)
    await AdminStates.choosing_date.set()
    await show_slot_date_keyboard(callback_query, state)

async def show_slot_date_keyboard(callback_query, state: FSMContext, offset=0):
    today = datetime.today().date()
    start = today + timedelta(weeks=offset)
    dates = [start + timedelta(days=i) for i in range(7)]

    kb = InlineKeyboardMarkup(row_width=2)
    for d in dates:
        label = d.strftime("%d.%m (%a)")
        callback_data = f"admin:slot_date:{d.strftime('%Y-%m-%d')}"
        kb.insert(InlineKeyboardButton(label, callback_data=callback_data))

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ Назад", callback_data="admin:slot_nav:prev"))
    nav.append(InlineKeyboardButton("➡️ Далее", callback_data="admin:slot_nav:next"))
    kb.row(*nav)

    await state.update_data(week_offset=offset)
    await send_clean_message(state, callback_query.message.chat.id, "📅 Выберите дату для нового слота:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("admin:slot_nav:"), state=AdminStates.choosing_date)
async def slot_date_nav(callback_query: types.CallbackQuery, state: FSMContext):
    direction = callback_query.data.split(":")[2]
    data = await state.get_data()
    offset = data.get("week_offset", 0)
    if direction == "next":
        offset += 1
    elif direction == "prev":
        offset = max(0, offset - 1)
    await show_slot_date_keyboard(callback_query, state, offset)

@dp.callback_query_handler(lambda c: c.data.startswith("admin:slot_date:"), state=AdminStates.choosing_date)
async def admin_slot_choose_date(callback_query: types.CallbackQuery, state: FSMContext):
    date = callback_query.data.split(":")[2]
    await state.update_data(slot_date=date)

    data = await state.get_data()
    city = data.get("slot_city")

    # Загружаем занятые слоты на эту дату
    booked = get_booked_slots(city, date)
    if booked:
        slots_text = "\n".join([f"• {s}" for s in sorted(booked)])
        text = f"📅 Уже занятые слоты на {date}:\n{slots_text}\n\n🕒 Введите новое время в формате ЧЧ:ММ:"
    else:
        text = "✅ Пока свободно.\n\n🕒 Введите новое время в формате ЧЧ:ММ:"

    await send_clean_message(state, callback_query.message.chat.id, text)
    await AdminStates.entering_time.set()

@dp.message_handler(state=AdminStates.entering_time)
async def admin_slot_enter_time(message: types.Message, state: FSMContext):
    time_text = message.text.strip()
    try:
        datetime.strptime(time_text, "%H:%M")
    except ValueError:
        await message.answer("❗ Неверный формат. Введите время как ЧЧ:ММ (например, 14:30)")
        return

    data = await state.get_data()
    city = data["slot_city"]
    date = data["slot_date"]

    # сохраняем "пустой слот" в BOOKINGS_FILE, если ещё не занят
    bookings = load_bookings()
    key = f"{date}_{city}"
    if key not in bookings:
        bookings[key] = {}
    if time_text in bookings[key]:
        kb = InlineKeyboardMarkup()
        kb.add(
            InlineKeyboardButton("🔁 Попробовать другое время", callback_data="admin:slot_repeat_time"),
            InlineKeyboardButton("⬅️ Назад", callback_data="admin:panel")
        )
        await send_clean_message(state, message.chat.id, f"⚠️ Слот {time_text} уже существует.", reply_markup=kb)
        return
    else:
        bookings[key][time_text] = {}
        with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(bookings, f, ensure_ascii=False, indent=2)

        kb = InlineKeyboardMarkup()
        kb.add(
            InlineKeyboardButton("➕ Добавить ещё", callback_data="admin:add_another"),
            InlineKeyboardButton("⬅️ Назад", callback_data="admin:panel")
        )
        await send_clean_message(state, message.chat.id, f"✅ Слот {time_text} добавлен на {date} ({'Москва' if city == 'msk' else 'Краснодар'})", reply_markup=kb)

    # НЕ вызываем state.finish() — остаёмся в состоянии AdminStates.entering_time

@dp.callback_query_handler(lambda c: c.data == "admin:add_another", state="*")
async def admin_add_another(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()

    # Восстанавливаем город и дату (если не удалились после state.finish())
    data = await state.get_data()
    city = data.get("slot_city")
    date = data.get("slot_date")

    if not city or not date:
        await send_clean_message(state, callback_query.message.chat.id, "⚠️ Ошибка: не удалось восстановить город или дату. Пожалуйста, начните заново.")
        await state.finish()
        return

    # Повторно сохраняем данные в FSM (на случай потери)
    await state.update_data(slot_city=city, slot_date=date)

    await send_clean_message(state, callback_query.message.chat.id, "🕒 Введите время в формате ЧЧ:ММ:")
    await AdminStates.entering_time.set()

@dp.callback_query_handler(lambda c: c.data == "admin:go_start", state="*")
async def admin_go_start(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.finish()

    is_admin = callback_query.from_user.id == ADMIN_CHAT_ID

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Да", callback_data="begin:yes"),
        InlineKeyboardButton("❌ Нет", callback_data="begin:no"),
        InlineKeyboardButton("📣 Связаться с нами", callback_data="show:contacts")
    )
    if is_admin:
        kb.insert(InlineKeyboardButton("⚙️ Админка", callback_data="admin:panel"))

    await send_clean_message(state, callback_query.message.chat.id, "👋 Добро пожаловать! Хотите записаться на демонстрацию?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "admin:slot_repeat_time", state=AdminStates.entering_time)
async def repeat_slot_time(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await send_clean_message(callback_query.message.chat.id, text="🕒 Введите другое время в формате ЧЧ:ММ:")

@dp.callback_query_handler(lambda c: c.data == "resign:no")
async def handle_resign_no(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.finish()

    is_admin = callback_query.from_user.id == ADMIN_CHAT_ID

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Да", callback_data="begin:yes"),
        InlineKeyboardButton("❌ Нет", callback_data="begin:no"),
        InlineKeyboardButton("📣 Связаться с нами", callback_data="show:contacts")
    )
    if is_admin:
        kb.insert(InlineKeyboardButton("⚙️ Админка", callback_data="admin:panel"))

    await send_clean_message(callback_query.message.chat.id, text="👋 Хотите записаться на демонстрацию?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "resign:yes")
async def proceed_resign(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()

    user_id = callback_query.from_user.id
    data = load_bookings()
    deleted = False

    from google_calendar import delete_event_from_calendar, MOSCOW_CALENDAR_ID, KRASNODAR_CALENDAR_ID

    for key in list(data.keys()):
        city = key.split("_")[1]
        for time in list(data[key].keys()):
            info = data[key][time]
            if info.get("user_id") == user_id:
                event_id = info.get("event_id")
                if event_id:
                    calendar_id = MOSCOW_CALENDAR_ID if city == "msk" else KRASNODAR_CALENDAR_ID
                    try:
                        delete_event_from_calendar(calendar_id, event_id)
                        logging.info(f"[google] Событие {event_id} удалено из календаря {calendar_id}")
                    except Exception as e:
                        logging.warning(f"[google] Ошибка удаления события: {e}")
                        info = data[key][time]
                        event_id = info.get("event_id")
                        city = key.split("_")[1]
                        if event_id:
                            calendar_id = MOSCOW_CALENDAR_ID if city == "msk" else KRASNODAR_CALENDAR_ID
                            try:
                                delete_event_from_calendar(calendar_id, event_id)
                                logging.info(f"Удалено из календаря: {event_id}")
                            except Exception as e:
                                logging.warning(f"❌ Не удалось удалить событие {event_id} из календаря: {e}")
                del data[key][time]
                deleted = True
        if not data[key]:
            del data[key]

    if deleted:
        with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    msg = await callback_query.message.edit_text("Как вас зовут? (только буквы)")
    await state.update_data(last_bot_msg_id=msg.message_id)
    await state.set_state(BookingStates.waiting_for_name.state)

@dp.callback_query_handler(lambda c: c.data.startswith("resign:new:"))
async def create_additional_booking(callback_query: types.CallbackQuery, state: FSMContext):
    city_code = callback_query.data.split(":")[2]
    await callback_query.answer()
    await state.update_data(city=city_code)
    msg = await callback_query.message.edit_text("Как вас зовут? (только буквы)")
    await state.update_data(last_bot_msg_id=msg.message_id)
    await BookingStates.waiting_for_name.set()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(send_reminders())
    executor.start_polling(dp, skip_updates=True)

# placeholder - actual bot.py was reset. Please reinsert content if needed.
# Тест автодеплоя Mon Apr 14 12:01:24 UTC 2025
