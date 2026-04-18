import logging
import os
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- НАСТРОЙКИ (Замените на свои значения) ---
TELEGRAM_BOT_TOKEN = "8792081322:AAEC0j2WhW2jJQXMBviJy8tbIR5M67SGUoE"
DEEPSEEK_API_KEY = "sk-85543108341b4d26bc2257365a21b615"
GITHUB_RAW_FILE_URL = "https://raw.githubusercontent.com/ВАШ_ЮЗЕРНЕЙМ/ВАШ_РЕПОЗИТОРИЙ/main/my_universe.txt"
# --- КОНЕЦ НАСТРОЕК ---

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

def load_context_file():
    try:
        response = requests.get(GITHUB_RAW_FILE_URL)
        response.raise_for_status()
        return response.text
    except Exception as e:
        logging.error(f"Не удалось загрузить файл контекста: {e}")
        return "Ошибка загрузки контекста"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Бот запущен. Отправьте сообщение, и оно будет обработано с учетом контекста.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    context_file_content = load_context_file()

    deepseek_payload = {
        "model": "deepseek-chat",
        "messages": [
            {
                "role": "system",
                "content": f"Ты — ИИ-агент, работающий с этим контекстом:\n\n{context_file_content}\n\nВсегда в конце ответа добавляй блок === ИТОГИ ДЛЯ ЖУРНАЛА ===, в котором будет краткая выжимка для сохранения в файл my_universe.txt.",
            },
            {"role": "user", "content": user_message},
        ],
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

    try:
        response = requests.post("https://api.deepseek.com/v1/chat/completions", json=deepseek_payload, headers=headers)
        response.raise_for_status()
        ai_response_text = response.json()["choices"][0]["message"]["content"]
        await update.message.reply_text(ai_response_text)

        # Извлекаем блок с итогами для журнала
        journal_start = ai_response_text.find("=== ИТОГИ ДЛЯ ЖУРНАЛА ===")
        if journal_start != -1:
            journal_block = ai_response_text[journal_start:]
            print(f"ЖУРНАЛ:\n{journal_block}")

    except Exception as e:
        logging.error(f"Ошибка при обращении к DeepSeek API: {e}")
        await update.message.reply_text("Произошла ошибка при обработке запроса.")

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()