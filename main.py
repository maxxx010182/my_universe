import logging
import os
import requests
import base64
import asyncio
from starlette.applications import Starlette
from starlette.responses import Response, PlainTextResponse
from starlette.requests import Request
from starlette.routing import Route
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import uvicorn

# --- НАСТРОЙКИ ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO")
GITHUB_FILE_PATH = os.environ.get("GITHUB_FILE_PATH")
AUTHORIZED_USER_IDS = os.environ.get("AUTHORIZED_USER_IDS", "")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")
PORT = int(os.environ.get("PORT", 8000))

ALLOWED_USERS = []
if AUTHORIZED_USER_IDS:
    try:
        ALLOWED_USERS = [int(uid.strip()) for uid in AUTHORIZED_USER_IDS.split(',') if uid.strip()]
    except ValueError:
        logging.error("Ошибка парсинга AUTHORIZED_USER_IDS. Проверьте, что ID указаны через запятую.")

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

def is_user_authorized(user_id: int) -> bool:
    # ВРЕМЕННО ОТКЛЮЧЕНО ДЛЯ ДИАГНОСТИКИ
    logger.info(f"Проверка авторизации для user_id={user_id} (фильтр отключён)")
    return True

def load_context_file():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    logger.info(f"Загрузка контекста из {url}")
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        content_b64 = response.json().get("content", "")
        return base64.b64decode(content_b64).decode("utf-8")
    else:
        logger.error(f"Ошибка загрузки файла: {response.status_code}")
        return "Ошибка загрузки контекста"

def save_journal_block(journal_text):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    get_resp = requests.get(url, headers=headers)
    if get_resp.status_code != 200:
        logger.error("Не удалось получить файл для обновления")
        return
    file_data = get_resp.json()
    current_content = base64.b64decode(file_data["content"]).decode("utf-8")
    sha = file_data["sha"]
    if journal_text.strip() not in current_content:
        new_content = current_content.rstrip() + "\n\n" + journal_text.strip()
    else:
        new_content = current_content
    update_payload = {
        "message": "Автообновление журнала",
        "content": base64.b64encode(new_content.encode("utf-8")).decode("utf-8"),
        "sha": sha,
        "branch": "main"
    }
    put_resp = requests.put(url, json=update_payload, headers=headers)
    if put_resp.status_code in (200, 201):
        logger.info("Журнал успешно обновлён")
    else:
        logger.error(f"Ошибка обновления журнала: {put_resp.status_code}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Обработчик /start вызван. user_id={update.effective_user.id}")
    user_id = update.effective_user.id
    if not is_user_authorized(user_id):
        logger.warning(f"Неавторизованный доступ от user_id: {user_id}")
        return
    await update.message.reply_text("Бот запущен. Отправьте сообщение, и оно будет обработано с учетом контекста.")
    logger.info("Ответ на /start отправлен")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Обработчик сообщения вызван. user_id={update.effective_user.id}, текст={update.message.text[:50] if update.message.text else 'нет текста'}")
    user_id = update.effective_user.id
    if not is_user_authorized(user_id):
        logger.warning(f"Неавторизованный доступ от user_id: {user_id}")
        return

    user_message = update.message.text
    logger.info("Загрузка контекста...")
    context_file_content = load_context_file()
    logger.info(f"Контекст загружен, длина {len(context_file_content)} символов")

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
        logger.info("Отправка запроса к DeepSeek API...")
        response = requests.post("https://api.deepseek.com/v1/chat/completions", json=deepseek_payload, headers=headers, timeout=30)
        response.raise_for_status()
        ai_response_text = response.json()["choices"][0]["message"]["content"]
        logger.info(f"Ответ от DeepSeek получен, длина {len(ai_response_text)}")
        await update.message.reply_text(ai_response_text)
        logger.info("Ответ отправлен в Telegram")

        journal_start = ai_response_text.find("=== ИТОГИ ДЛЯ ЖУРНАЛА ===")
        if journal_start != -1:
            journal_block = ai_response_text[journal_start:]
            save_journal_block(journal_block)

    except Exception as e:
        logger.error(f"Ошибка при обращении к DeepSeek API: {e}", exc_info=True)
        await update.message.reply_text("Произошла ошибка при обработке запроса.")

async def telegram_webhook(request: Request):
    logger.info("Webhook вызван")
    app = request.app.state.tg_app
    data = await request.body()
    try:
        update = Update.de_json(await request.json(), app.bot)
        logger.info(f"Получен update: {update.update_id}")
        await app.update_queue.put(update)
    except Exception as e:
        logger.error(f"Ошибка разбора update: {e}", exc_info=True)
    return Response()

async def healthcheck(_):
    return PlainTextResponse("OK")

async def self_ping():
    while True:
        await asyncio.sleep(600)
        try:
            requests.get(f"{RENDER_EXTERNAL_URL}/healthcheck", timeout=5)
            logger.info("Self-ping successful")
        except Exception as e:
            logger.error(f"Self-ping failed: {e}")

async def main():
    logger.info("Проверка переменных окружения:")
    logger.info(f"TELEGRAM_BOT_TOKEN: {'установлен' if TELEGRAM_BOT_TOKEN else 'ОТСУТСТВУЕТ'}")
    logger.info(f"DEEPSEEK_API_KEY: {'установлен' if DEEPSEEK_API_KEY else 'ОТСУТСТВУЕТ'}")
    logger.info(f"GITHUB_TOKEN: {'установлен' if GITHUB_TOKEN else 'ОТСУТСТВУЕТ'}")
    logger.info(f"GITHUB_REPO: {GITHUB_REPO}")
    logger.info(f"GITHUB_FILE_PATH: {GITHUB_FILE_PATH}")
    logger.info(f"RENDER_EXTERNAL_URL: {RENDER_EXTERNAL_URL}")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await app.initialize()
    webhook_url = f"{RENDER_EXTERNAL_URL}/telegram"
    logger.info(f"Установка вебхука: {webhook_url}")
    await app.bot.set_webhook(webhook_url)

    asyncio.create_task(self_ping())

    starlette_app = Starlette(routes=[
        Route("/telegram", telegram_webhook, methods=["POST"]),
        Route("/healthcheck", healthcheck, methods=["GET"]),
    ])
    starlette_app.state.tg_app = app

    config = uvicorn.Config(starlette_app, host="0.0.0.0", port=PORT, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()
    await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
