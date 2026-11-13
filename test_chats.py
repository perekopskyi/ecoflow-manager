"""Subscription storage. No network and no real keys."""

import os
import stat
import tempfile
from pathlib import Path

import bot


def use_temp_files():
    folder = Path(tempfile.mkdtemp())
    bot.CHATS_FILE = folder / "chats.json"
    bot.CHAT_FILE = folder / "legacy_chat"
    bot.MESSAGE_FILE = folder / "legacy_message"
    os.environ.pop("TELEGRAM_CHAT_ID", None)
    return folder


def test_legacy_chat_is_env_without_keys():
    folder = use_temp_files()
    (folder / "legacy_chat").write_text("42", encoding="utf-8")
    (folder / "legacy_message").write_text("7", encoding="utf-8")
    record = bot.load_chats()["42"]
    assert record["source"] == "env"
    assert record["message_id"] == 7
    assert "access_key" not in record
    assert "secret_key" not in record
    bot.subscribe_env(-5)
    assert bot.load_chats()["42"]["message_id"] == 7
    assert bot.load_chats()["-5"]["source"] == "env"


def test_user_keys_are_stored_and_stop_removes_them():
    use_temp_files()
    bot.subscribe_env(111)
    bot.subscribe_user(222, "access-key", "secret-key", "SN1")
    saved = bot.load_chats()["222"]
    assert saved["source"] == "user"
    assert saved["access_key"] == "access-key"
    assert saved["secret_key"] == "secret-key"
    assert saved["sn"] == "SN1"
    assert "111" in bot.load_chats()
    mode = stat.S_IMODE(bot.CHATS_FILE.stat().st_mode)
    assert mode == 0o600
    bot.forget_chat(222)
    assert "222" not in bot.load_chats()
    assert "111" in bot.load_chats()


def test_old_message_id_file_is_read_as_env():
    use_temp_files()
    bot.CHATS_FILE.write_text('{"7": 3}', encoding="utf-8")
    record = bot.load_chats()["7"]
    assert record == {"source": "env", "message_id": 3}
