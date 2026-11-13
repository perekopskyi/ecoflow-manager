"""Telegram dashboards for EcoFlow stations.

Existing chats and groups keep using the keys from the environment.
A new private chat enters its own access key and secret key, picks a
station, and then gets a separate dashboard.
"""

import asyncio
import json
import logging
import os
import stat
import sys
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from dashboard import AlertState, charge_restart, fetch_snapshot, render, transition_alerts
from ecoflow_client import EcoflowClient, EcoflowError, load_env

CHAT_FILE = Path(".telegram_chat_id")
MESSAGE_FILE = Path(".dashboard_message_id")
CHATS_FILE = Path(".telegram_chats.json")
GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}
ACCESS, SECRET, STATION = range(3)


def env_record(message_id):
    return {"source": "env", "message_id": message_id}


def user_record(access_key, secret_key, sn, message_id=None):
    return {
        "source": "user",
        "message_id": message_id,
        "access_key": access_key,
        "secret_key": secret_key,
        "sn": sn,
    }


def normalize_record(value):
    if isinstance(value, dict):
        record = {"source": value.get("source", "env"), "message_id": value.get("message_id")}
        if record["source"] == "user":
            record["access_key"] = value.get("access_key")
            record["secret_key"] = value.get("secret_key")
            record["sn"] = value.get("sn")
        return record
    return env_record(value)


def load_chats():
    """chat id -> subscription. Old message-id values become env records."""
    if CHATS_FILE.exists():
        stored = json.loads(CHATS_FILE.read_text(encoding="utf-8"))
        chats = {str(chat_id): normalize_record(value) for chat_id, value in stored.items()}
        if any(not isinstance(value, dict) or value.get("source") is None for value in stored.values()):
            save_chats(chats)
        return chats

    chats = {}
    legacy_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not legacy_id and CHAT_FILE.exists():
        legacy_id = CHAT_FILE.read_text(encoding="utf-8").strip()
    if legacy_id:
        message_id = None
        if MESSAGE_FILE.exists() and MESSAGE_FILE.read_text(encoding="utf-8").strip():
            message_id = int(MESSAGE_FILE.read_text(encoding="utf-8").strip())
        chats[str(int(legacy_id))] = env_record(message_id)
        save_chats(chats)
    return chats


def save_chats(chats):
    CHATS_FILE.write_text(json.dumps(chats), encoding="utf-8")
    os.chmod(CHATS_FILE, stat.S_IRUSR | stat.S_IWUSR)


def is_subscribed(chat_id):
    return str(chat_id) in load_chats()


def subscribe_env(chat_id):
    chats = load_chats()
    key = str(chat_id)
    if key not in chats:
        chats[key] = env_record(None)
        save_chats(chats)
    return True


def subscribe_user(chat_id, access_key, secret_key, sn):
    chats = load_chats()
    chats[str(chat_id)] = user_record(access_key, secret_key, sn)
    save_chats(chats)


def forget_chat(chat_id):
    chats = load_chats()
    if str(chat_id) not in chats:
        return
    chats.pop(str(chat_id))
    save_chats(chats)


def set_message_id(chat_id, message_id):
    chats = load_chats()
    record = chats.get(str(chat_id))
    if record is None:
        return
    record["message_id"] = message_id
    save_chats(chats)


def client_for(record):
    if record.get("source") == "user":
        return EcoflowClient(record["access_key"], record["secret_key"], record["sn"])
    return EcoflowClient.from_env()


def keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔄 Обновить", callback_data="refresh"),
                InlineKeyboardButton("Перезапустить", callback_data="restart"),
            ],
            [InlineKeyboardButton("Остановить", callback_data="stop")],
        ]
    )


async def delete_quietly(bot, chat_id, message_id):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except (BadRequest, Forbidden):
        return


async def upsert_dashboard(bot, chat_id, text):
    record = load_chats().get(str(chat_id))
    message_id = None if record is None else record.get("message_id")
    if message_id is not None:
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=keyboard(),
            )
            return
        except BadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
    message = await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard())
    set_message_id(chat_id, message.message_id)


async def drop_dashboard_message(bot, chat_id):
    record = load_chats().get(str(chat_id))
    message_id = None if record is None else record.get("message_id")
    set_message_id(chat_id, None)
    if message_id:
        await delete_quietly(bot, chat_id, message_id)


async def publish(bot, chat_id, text, alerts, repost_dashboard=False):
    try:
        if repost_dashboard:
            await drop_dashboard_message(bot, chat_id)
        for alert in alerts:
            await bot.send_message(chat_id=chat_id, text=alert)
        await upsert_dashboard(bot, chat_id, text)
    except Forbidden:
        logging.warning("Bot was removed from chat %s", chat_id)
        forget_chat(chat_id)


async def update_chat(application, bot, chat_id, repost=None):
    record = load_chats().get(str(chat_id))
    if record is None:
        return None
    if repost is not None:
        _, message_id = repost
        set_message_id(chat_id, None)
        await delete_quietly(bot, chat_id, message_id)
        if message_id > 1:
            await delete_quietly(bot, chat_id, message_id - 1)
    alerts_map = application.bot_data["alerts"]
    previous = alerts_map.get(str(chat_id), AlertState())
    snapshot = await asyncio.to_thread(fetch_snapshot, client_for(record))
    alerts, state = transition_alerts(
        previous,
        snapshot,
        application.bot_data["low_soc"],
    )
    repost_dashboard = charge_restart(previous, state)
    alerts_map[str(chat_id)] = state
    await publish(bot, chat_id, render(snapshot), alerts, repost_dashboard=repost_dashboard)
    return snapshot


async def refresh(application, bot, only_chat=None, repost=None):
    async with application.bot_data["lock"]:
        if only_chat is None:
            chat_ids = [int(chat_id) for chat_id in load_chats()]
        else:
            chat_ids = [only_chat]
        for chat_id in chat_ids:
            manual = repost if repost is not None and repost[0] == chat_id else None
            await update_chat(application, bot, chat_id, manual)


async def poll_job(context: ContextTypes.DEFAULT_TYPE):
    await refresh(context.application, context.bot)


def clear_pending_keys(context):
    context.user_data.pop("access_key", None)
    context.user_data.pop("secret_key", None)
    context.user_data.pop("stations", None)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat is None or update.message is None:
        return ConversationHandler.END
    if is_subscribed(chat.id):
        await update.message.reply_text("Обновляю дашборд.")
        await refresh(context.application, context.bot, only_chat=chat.id)
        return ConversationHandler.END
    if chat.type in GROUP_TYPES:
        subscribe_env(chat.id)
        await update.message.reply_text("Дашборд подключён к этому чату.")
        await refresh(context.application, context.bot, only_chat=chat.id)
        return ConversationHandler.END
    clear_pending_keys(context)
    await update.message.reply_text("Пришлите access key от EcoFlow.")
    return ACCESS


async def receive_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message is None or not message.text:
        return ACCESS
    context.user_data["access_key"] = message.text.strip()
    await delete_quietly(context.bot, message.chat_id, message.message_id)
    await message.chat.send_message("Теперь пришлите secret key.")
    return SECRET


async def receive_secret(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message is None or not message.text:
        return SECRET
    secret_key = message.text.strip()
    await delete_quietly(context.bot, message.chat_id, message.message_id)
    access_key = context.user_data.get("access_key", "")
    context.user_data["secret_key"] = secret_key
    await message.chat.send_message("Запрашиваю список станций…")
    try:
        payload = await asyncio.to_thread(
            EcoflowClient(access_key, secret_key, "pending").list_devices
        )
    except EcoflowError as exc:
        clear_pending_keys(context)
        await message.chat.send_message(
            f"EcoFlow отклонил ключи: {exc}. Пришлите access key ещё раз."
        )
        return ACCESS
    except Exception:
        logging.warning("Device list request failed")
        clear_pending_keys(context)
        await message.chat.send_message(
            "Не удалось связаться с EcoFlow. Пришлите access key ещё раз."
        )
        return ACCESS
    devices = payload.get("data") or []
    if not devices:
        clear_pending_keys(context)
        await message.chat.send_message("На этих ключах нет станций. Пришлите access key ещё раз.")
        return ACCESS
    stations = {}
    buttons = []
    for device in devices:
        sn = str(device.get("sn") or "")
        if not sn:
            continue
        name = device.get("productName") or "Станция"
        stations[sn] = name
        buttons.append([InlineKeyboardButton(f"{name} ({sn})", callback_data=f"sn:{sn}")])
    if not stations:
        clear_pending_keys(context)
        await message.chat.send_message("Список станций пуст. Пришлите access key ещё раз.")
        return ACCESS
    context.user_data["stations"] = stations
    await message.chat.send_message(
        "Выберите станцию.",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return STATION


async def choose_station(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None or query.message is None or not query.data:
        return STATION
    sn = query.data.removeprefix("sn:")
    stations = context.user_data.get("stations") or {}
    access_key = context.user_data.get("access_key")
    secret_key = context.user_data.get("secret_key")
    if sn not in stations or not access_key or not secret_key:
        await query.answer("Выбор устарел. Начните с /start", show_alert=True)
        clear_pending_keys(context)
        return ConversationHandler.END
    subscribe_user(query.message.chat_id, access_key, secret_key, sn)
    clear_pending_keys(context)
    await query.answer("Станция выбрана")
    await query.edit_message_text(f"Подключена {stations[sn]}.")
    await refresh(context.application, context.bot, only_chat=query.message.chat_id)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_pending_keys(context)
    if update.message is not None:
        await update.message.reply_text("Подключение отменено.")
    return ConversationHandler.END


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat is None or update.message is None:
        return
    if not is_subscribed(chat.id):
        await update.message.reply_text("Сначала отправьте /start в этом чате.")
        return
    await refresh(context.application, context.bot, only_chat=chat.id)


async def stop_dashboard(application, bot, chat_id):
    async with application.bot_data["lock"]:
        record = load_chats().get(str(chat_id))
        if record is None:
            return False
        message_id = record.get("message_id")
        forget_chat(chat_id)
        application.bot_data["alerts"].pop(str(chat_id), None)
        if message_id:
            await delete_quietly(bot, chat_id, message_id)
        return True


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat is None or update.message is None:
        return
    stopped = await stop_dashboard(context.application, context.bot, chat.id)
    if stopped:
        await update.message.reply_text("Дашборд остановлен. Ключи этого чата удалены.")
    else:
        await update.message.reply_text("В этом чате нет дашборда.")


async def on_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None or query.message is None:
        return
    if not is_subscribed(query.message.chat_id):
        await query.answer("Нет доступа", show_alert=True)
        return
    await query.answer("Обновляю")
    await refresh(context.application, context.bot, only_chat=query.message.chat_id)


async def on_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None or query.message is None:
        return
    if not is_subscribed(query.message.chat_id):
        await query.answer("Нет доступа", show_alert=True)
        return
    await query.answer("Перезапускаю")
    await refresh(
        context.application,
        context.bot,
        only_chat=query.message.chat_id,
        repost=(query.message.chat_id, query.message.message_id),
    )


async def on_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None or query.message is None:
        return
    if not is_subscribed(query.message.chat_id):
        await query.answer("Нет доступа", show_alert=True)
        return
    await query.answer("Останавливаю")
    await stop_dashboard(context.application, context.bot, query.message.chat_id)
    await context.bot.send_message(
        query.message.chat_id,
        "Дашборд остановлен. Ключи этого чата удалены.",
    )


async def on_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    membership = update.my_chat_member
    if membership is None or membership.chat.type not in GROUP_TYPES:
        return
    status = membership.new_chat_member.status
    if status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
        subscribe_env(membership.chat.id)
        await context.bot.send_message(
            membership.chat.id,
            "Дашборд подключён к этому чату.",
        )
        await refresh(context.application, context.bot, only_chat=membership.chat.id)
        return
    if status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
        forget_chat(membership.chat.id)
        context.application.bot_data["alerts"].pop(str(membership.chat.id), None)


async def post_init(application: Application):
    application.bot_data["lock"] = asyncio.Lock()
    application.bot_data.setdefault("alerts", {})


def main():
    load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        sys.exit("Задайте TELEGRAM_BOT_TOKEN в .env (токен от @BotFather).")

    low_soc = float(os.environ.get("ECOFLOW_LOW_SOC", "20"))
    interval = float(os.environ.get("ECOFLOW_POLL_SECONDS", "15"))
    application = Application.builder().token(token).post_init(post_init).build()
    application.bot_data["alerts"] = {}
    application.bot_data["low_soc"] = low_soc
    onboarding = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ACCESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_access)],
            SECRET: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_secret)],
            STATION: [CallbackQueryHandler(choose_station, pattern=r"^sn:")],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
        ],
    )
    application.add_handler(onboarding)
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CallbackQueryHandler(on_refresh, pattern="^refresh$"))
    application.add_handler(CallbackQueryHandler(on_restart, pattern="^restart$"))
    application.add_handler(CallbackQueryHandler(on_stop, pattern="^stop$"))
    application.add_handler(ChatMemberHandler(on_membership, ChatMemberHandler.MY_CHAT_MEMBER))
    if application.job_queue is None:
        sys.exit("Установите python-telegram-bot[job-queue], без него нет фонового опроса.")
    application.job_queue.run_repeating(poll_job, interval=interval, first=1)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    print(f"Бот запущен. Опрос каждые {interval:g} с. Напишите боту /start.", flush=True)
    application.run_polling()


if __name__ == "__main__":
    main()
