import logging
import os
import requests
import base64
import asyncio
import time
from starlette.applications import Starlette
from starlette.responses import Response, PlainTextResponse
from starlette.routing import Route
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import uvicorn

# === НАСТРОЙКИ ===
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO")
GITHUB_FILE_PATH = os.environ.get("GITHUB_FILE_PATH")
AUTHORIZED_USER_IDS = os.environ.get("AUTHORIZED_USER_IDS", "")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")
PORT = int(os.environ.get("PORT", 8000))

# OpenRouter API настройки (бесплатная модель)
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
# Используем бесплатную модель DeepSeek через OpenRouter
MODEL_NAME = "deepseek/deepseek-chat:free"

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

_cached_context = {"content": "", "sha": "", "last_check": 0}
CACHE_TTL = 1800

ALLOWED_USERS = []
if AUTHORIZED_USER_IDS:
    try:
        ALLOWED_USERS = [int(uid.strip()) for uid in AUTHORIZED_USER_IDS.split(',') if uid.strip()]
    except ValueError:
        logger.error("Ошибка парсинга AUTHORIZED_USER_IDS")

def is_user_authorized(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS

def get_context_file():
    global _cached_context
    now = time.time()
    if _cached_context["content"] and (now - _cached_context["last_check"]) < CACHE_TTL:
        return _cached_context["content"]

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            new_sha = data.get("sha", "")
            if new_sha == _cached_context["sha"] and _cached_context["content"]:
                _cached_context["last_check"] = now
                return _cached_context["content"]

            content_b64 = data.get("content", "")
            content = base64.b64decode(content_b64).decode("utf-8")
            _cached_context = {"content": content, "sha": new_sha, "last_check": now}
            logger.info("Контекст обновлён из GitHub")
            return content
        else:
            logger.error(f"Ошибка загрузки файла: {response.status_code}")
    except Exception as e:
        logger.error(f"Исключение при загрузке контекста: {e}")
    return _cached_context["content"] or "Ошибка загрузки контекста"

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
        global _cached_context
        _cached_context["last_check"] = 0
    else:
        logger.error(f"Ошибка обновления журнала: {put_resp.status_code}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_authorized(user_id):
        return
    await update.message.reply_text("Бот запущен. Используйте команду 'Включи [ИМЯ АГЕНТА]' для активации агента.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_authorized(user_id):
        return

    user_message = update.message.text
    context_content = get_context_file()

    system_prompt = f"""Ты — ИИ-агент Максима Мошкина. Работай строго по ролям из файла ниже.

=== ФАЙЛ КОНТЕКСТА ===
{context_content}
=== КОНЕЦ ФАЙЛА ===

ПРАВИЛА:
- Если сообщение начинается с "Включи" или "Активируй" — переключись на указанного агента (ГЛАВРЕД, СТРАТЕГ, ХУДОЖНИК, БАИНГ, ТЕХНАРЬ) и отвечай в его стиле.
- Если явной команды нет, используй последнюю активную роль (по умолчанию ГЛАВРЕД).
- Отвечай только на русском.
- В конце каждого ответа добавляй блок === ИТОГИ ДЛЯ ЖУРНАЛА === с краткой выжимкой (1-3 предложения).
- Не объясняй свои действия, не упоминай API, модели, технические детали.
"""

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": RENDER_EXTERNAL_URL,
        "X-Title": "MyUniverse Bot"
    }
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        "temperature": 0.5,
        "max_tokens": 1500,
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            ai_response_text = response.json()["choices"][0]["message"]["content"]
            await update.message.reply_text(ai_response_text)

            journal_start = ai_response_text.find("=== ИТОГИ ДЛЯ ЖУРНАЛА ===")
            if journal_start != -1:
                journal_block = ai_response_text[journal_start:]
                save_journal_block(journal_block)
            return

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                wait_time = 2 ** attempt
                logger.warning(f"Rate limit (429). Попытка {attempt + 1}/{max_retries}. Ожидание {wait_time} сек.")
                await asyncio.sleep(wait_time)
                continue
            else:
                logger.error(f"Ошибка OpenRouter API: {e.response.status_code}")
                await update.message.reply_text("Произошла ошибка при обработке запроса. Попробуй позже.")
                return
        except Exception as e:
            logger.error(f"Неизвестная ошибка: {e}")
            await update.message.reply_text("Произошла ошибка при обработке запроса.")
            return

async def telegram_webhook(request: Request):
    app = request.app.state.tg_app
    try:
        update = Update.de_json(await request.json(), app.bot)
        await app.update_queue.put(update)
    except Exception as e:
        logger.error(f"Ошибка webhook: {e}")
    return Response()

async def healthcheck(_):
    return PlainTextResponse("OK")

async def self_ping():
    while True:
        await asyncio.sleep(600)
        try:
            requests.get(f"{RENDER_EXTERNAL_URL}/healthcheck", timeout=5)
        except Exception:
            pass

async def main():
    logger.info("Запуск бота через OpenRouter (бесплатный тариф)")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await app.initialize()
    await app.start()
    await app.bot.set_webhook(f"{RENDER_EXTERNAL_URL}/telegram")

    asyncio.create_task(self_ping())

    starlette_app = Starlette(routes=[
        Route("/telegram", telegram_webhook, methods=["POST"]),
        Route("/healthcheck", healthcheck, methods=["GET"]),
    ])
    starlette_app.state.tg_app = app

    config = uvicorn.Config(starlette_app, host="0.0.0.0", port=PORT, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

    await app.stop()
    await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
