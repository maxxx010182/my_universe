import logging
import os
import requests
import base64
import asyncio
import time
from groq import Groq, RateLimitError
from starlette.applications import Starlette
from starlette.responses import Response, PlainTextResponse
from starlette.requests import Request
from starlette.routing import Route
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import uvicorn

# --- НАСТРОЙКИ ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO")
GITHUB_FILE_PATH = os.environ.get("GITHUB_FILE_PATH")
AUTHORIZED_USER_IDS = os.environ.get("AUTHORIZED_USER_IDS", "")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")
PORT = int(os.environ.get("PORT", 8000))

groq_client = Groq(api_key=GROQ_API_KEY)
FALLBACK_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "mixtral-8x7b-32768",
]

# --- КЭШИРОВАНИЕ КОНТЕКСТА ---
_cached_context = {"content": "", "sha": "", "last_check": 0}
CACHE_TTL = 1800  # проверять обновления раз в 30 минут

ALLOWED_USERS = []
if AUTHORIZED_USER_IDS:
    try:
        ALLOWED_USERS = [int(uid.strip()) for uid in AUTHORIZED_USER_IDS.split(',') if uid.strip()]
    except ValueError:
        logging.error("Ошибка парсинга AUTHORIZED_USER_IDS. Проверьте, что ID указаны через запятую.")

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

def is_user_authorized(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS

def get_context_file():
    """Возвращает содержимое файла контекста с кэшированием."""
    global _cached_context
    now = time.time()

    # Если кэш свежий — возвращаем
    if _cached_context["content"] and (now - _cached_context["last_check"]) < CACHE_TTL:
        return _cached_context["content"]

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            new_sha = data.get("sha", "")
            # Если SHA не изменился — продлеваем кэш
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

    # Fallback — возвращаем старый кэш или ошибку
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
        # Сбрасываем кэш, чтобы бот увидел изменения
        global _cached_context
        _cached_context["last_check"] = 0
    else:
        logger.error(f"Ошибка обновления журнала: {put_resp.status_code}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_authorized(user_id):
        return
    await update.message.reply_text("Бот запущен. Используйте команду 'Включи [ИМЯ АГЕНТА]' для активации нужной роли.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_authorized(user_id):
        return

    user_message = update.message.text
    context_content = get_context_file()

    # Облегчённый системный промпт
    system_prompt = f"""Ты — ИИ-агент Максима Мошкина. Твоя задача — строго следовать ролям, описанным в файле контекста ниже.

=== НАЧАЛО ФАЙЛА КОНТЕКСТА ===
{context_content}
=== КОНЕЦ ФАЙЛА КОНТЕКСТА ===

ВАЖНЫЕ ПРАВИЛА:
1. В файле контекста есть раздел "ПРОФИЛИ АГЕНТОВ" с описаниями: ГЛАВРЕД, СТРАТЕГ, ХУДОЖНИК, БАИНГ, ТЕХНАРЬ.
2. Если пользователь пишет "Включи [ИМЯ]" или "Активируй [ИМЯ]" — найди этого агента в файле и отвечай строго в его стиле.
3. Если пользователь спрашивает об умениях агентов — перечисли их кратко, основываясь ТОЛЬКО на информации из файла.
4. Если описание агента отсутствует в файле — скажи: "Этот агент пока не настроен. Использую ГЛАВРЕДА по умолчанию."
5. Отвечай на русском. В конце — блок === ИТОГИ ДЛЯ ЖУРНАЛА === (1-3 предложения).
"""

    last_exception = None
    for model_name in FALLBACK_MODELS:
        try:
            logger.info(f"Запрос к модели {model_name}")
            chat_completion = groq_client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                model=model_name,
                temperature=0.5,
                max_tokens=1500,  # уменьшено для экономии
            )
            ai_response_text = chat_completion.choices[0].message.content
            await update.message.reply_text(ai_response_text)

            journal_start = ai_response_text.find("=== ИТОГИ ДЛЯ ЖУРНАЛА ===")
            if journal_start != -1:
                journal_block = ai_response_text[journal_start:]
                save_journal_block(journal_block)
            return

        except RateLimitError as e:
            logger.warning(f"Модель {model_name} исчерпала лимит")
            last_exception = e
            continue
        except Exception as e:
            logger.error(f"Ошибка модели {model_name}: {e}", exc_info=True)
            last_exception = e
            break

    logger.error("Все модели недоступны")
    await update.message.reply_text("Все голосовые ассистенты временно заняты. Попробуйте позже.")

async def telegram_webhook(request: Request):
    app = request.app.state.tg_app
    try:
        update = Update.de_json(await request.json(), app.bot)
        await app.update_queue.put(update)
    except Exception as e:
        logger.error(f"Ошибка webhook: {e}", exc_info=True)
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
    logger.info("Запуск бота...")
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
