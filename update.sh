#!/bin/bash

LOG_FILE="$HOME/projects/SNKLaser/appointments/update.log"
echo "==================== $(date '+%Y-%m-%d %H:%M:%S') ====================" >> "$LOG_FILE"

{
    echo "🔄 Обновление проекта..."
    git pull

    echo "📦 Обновление зависимостей..."
    source venv/bin/activate
    pip install -r requirements.txt

    echo "🔁 Перезапуск бота..."
    sudo systemctl restart appointments-bot

    echo "✅ Готово! Бот обновлён и перезапущен."
} >> "$LOG_FILE" 2>&1
