from google.oauth2 import service_account
from googleapiclient.discovery import build
import datetime

# 🔑 Замените на путь к вашему credentials.json
SERVICE_ACCOUNT_FILE = "credentials.json"
SCOPES = ["https://www.googleapis.com/auth/calendar"]

# ✅ Укажи реальные ID календарей для каждого города
MOSCOW_CALENDAR_ID = "4a20ck8hdf5mquh0tj5b338bu8@group.calendar.google.com"
KRASNODAR_CALENDAR_ID = "ef7a8cafcc2edc315de2f5541d8a3afcea17585d9ab3b96e7c9ddef730207bc3@group.calendar.google.com"

def get_calendar_service():
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    service = build("calendar", "v3", credentials=credentials)
    return service

def add_event_to_calendar(name, phone, city, date_str, time_str):
    service = get_calendar_service()
    calendar_id = MOSCOW_CALENDAR_ID if city == "msk" else KRASNODAR_CALENDAR_ID

    start_datetime = f"{date_str}T{time_str}:00"
    end_time = (datetime.datetime.strptime(time_str, "%H:%M") + datetime.timedelta(hours=1)).time()
    end_datetime = f"{date_str}T{end_time.strftime('%H:%M')}:00"

    event = {
        "summary": f"Демонстрация: {name}",
        "description": f"Телефон: {phone}",
        "start": {
            "dateTime": start_datetime,
            "timeZone": "Europe/Moscow",
        },
        "end": {
            "dateTime": end_datetime,
            "timeZone": "Europe/Moscow",
        },
    }

    event_result = service.events().insert(calendarId=calendar_id, body=event).execute()
    return event_result.get("id")

def delete_event_from_calendar(calendar_id, event_id):
    service = get_calendar_service()
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
