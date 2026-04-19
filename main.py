
ИНСТРУКЦИИ:
1. Внимательно прочитай файл контекста. В нём описаны несколько агентов: ГЛАВРЕД, СТРАТЕГ, ХУДОЖНИК, БАИНГ, ТЕХНАРЬ.
2. Если пользователь пишет команду, начинающуюся с "Включи" или "Активируй", ты должен переключиться на соответствующего агента и ответить в его стиле.
3. Если пользователь пишет "Переключиться на [ИМЯ АГЕНТА]", ты также меняешь роль.
4. Если явной команды нет, используй последнюю активную роль (по умолчанию - ГЛАВРЕД).
5. Отвечай только на русском языке (если не указано иное).
6. Следуй тону и стилю выбранного агента, как описано в файле.
7. В конце каждого ответа добавляй блок === ИТОГИ ДЛЯ ЖУРНАЛА ===, в котором кратко (1-3 предложения) описано, что было сделано. Этот блок будет автоматически сохранён в файл my_universe.txt.

НЕ ОБЪЯСНЯЙ свои действия. НЕ УПОМИНАЙ технические детали API или моделей. Просто выполняй роль.
"""

    try:
        chat_completion = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            model=MODEL_NAME,
            temperature=0.5,
            max_tokens=4096,
        )
        ai_response_text = chat_completion.choices[0].message.content
        await update.message.reply_text(ai_response_text)

        journal_start = ai_response_text.find("=== ИТОГИ ДЛЯ ЖУРНАЛА ===")
        if journal_start != -1:
            journal_block = ai_response_text[journal_start:]
            save_journal_block(journal_block)

    except Exception as e:
        logger.error(f"Ошибка Groq API: {e}", exc_info=True)
        await update.message.reply_text("Произошла ошибка при обработке запроса.")

async def telegram_webhook(request: Request):
    app = request.app.state.tg_app
    data = await request.body()
    try:
        update = Update.de_json(await request.json(), app.bot)
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
    logger.info(f"GROQ_API_KEY: {'установлен' if GROQ_API_KEY else 'ОТСУТСТВУЕТ'}")
    logger.info(f"GITHUB_TOKEN: {'установлен' if GITHUB_TOKEN else 'ОТСУТСТВУЕТ'}")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await app.initialize()
    await app.start()

    webhook_url = f"{RENDER_EXTERNAL_URL}/telegram"
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

    await app.stop()
    await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
