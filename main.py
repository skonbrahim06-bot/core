#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Super Protection Bot — Final Unified (All requested commands & menu)
- Combines previous protection, admin, developer, service and games placeholders.
- Adds many textual handlers (Arabic) as requested and places them in logical groups.
- Most game/service commands are implemented as safe placeholders that respond/help;
  you can expand each implementation later (APIs, DB models, game state machines).
- Per-chat settings stored in SQLite; warns stored in SQLite (optional Redis counters).
- Fancy "الاوامر" menu with inline keyboard (m1..m6) that links to the groups.
Notes:
- Put TELEGRAM_BOT_TOKEN in env (required).
- Optionally set REDIS_URL, AUDIT_CHAT_ID, DEVELOPER_USERNAME.
- This file is large; test in a safe group before production.
"""

import os
import re
import time
import json
import uuid
import sqlite3
import logging
import threading
import io
import zipfile
from collections import deque
from typing import Tuple, Dict, Optional

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# Optional redis
try:
    import redis
except Exception:
    redis = None

# -------------------- Configuration --------------------
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")
REDIS_URL = os.environ.get("REDIS_URL")
AUDIT_CHAT_ID = os.environ.get("AUDIT_CHAT_ID")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "C_R_B_X255BOT")
IMAGE_URL = os.environ.get("IMAGE_URL", "https://www2.0zz0.com/2025/12/30/19/531271001.jpg")
DEVELOPER_USERNAME = os.environ.get("DEVELOPER_USERNAME", "C_R_B_X")  # without @

if TOKEN == "PUT_YOUR_TOKEN_HERE":
    logging.warning("Set TELEGRAM_BOT_TOKEN env var; current token is placeholder.")

# -------------------- Bot init --------------------
bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(message)s",
                    handlers=[logging.FileHandler("bot_super_protection_final.log", encoding="utf-8"),
                              logging.StreamHandler()])

# -------------------- DB and Locks --------------------
DB_PATH = "super_protection_final.db"
DB_LOCK = threading.Lock()

def init_db():
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS chats (
                        chat_id INTEGER PRIMARY KEY,
                        settings_json TEXT
                    )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS warns (
                        chat_id INTEGER,
                        user_id INTEGER,
                        count INTEGER,
                        last_ts INTEGER,
                        PRIMARY KEY (chat_id, user_id)
                    )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS globals (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )""")
        conn.commit()
        conn.close()

def set_global(key: str, value: str):
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cur = conn.cursor()
        cur.execute("REPLACE INTO globals (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()

def get_global(key: str) -> Optional[str]:
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cur = conn.cursor()
        cur.execute("SELECT value FROM globals WHERE key = ?", (key,))
        row = cur.fetchone()
        conn.close()
    return row[0] if row else None

if AUDIT_CHAT_ID:
    try:
        set_global("AUDIT_CHAT_ID", AUDIT_CHAT_ID)
    except Exception:
        logging.exception("storing AUDIT_CHAT_ID failed")

# -------------------- Redis client (optional) --------------------
redis_client = None
if REDIS_URL and redis is not None:
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        logging.info("Connected to Redis")
    except Exception:
        logging.exception("Failed connecting to Redis; falling back to SQLite for counters")

# -------------------- Settings/Warns helpers --------------------
def save_chat_settings(chat_id: int, settings: dict):
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cur = conn.cursor()
        cur.execute("REPLACE INTO chats (chat_id, settings_json) VALUES (?, ?)", (chat_id, json.dumps(settings, ensure_ascii=False)))
        conn.commit()
        conn.close()

def load_chat_settings(chat_id: int) -> dict:
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cur = conn.cursor()
        cur.execute("SELECT settings_json FROM chats WHERE chat_id = ?", (chat_id,))
        row = cur.fetchone()
        conn.close()
    if row:
        try:
            return json.loads(row[0])
        except Exception:
            pass
    default = {
        "anti_flood": True, "flood_threshold": 5, "flood_window": 10,
        "anti_links": True, "anti_invite": True,
        "whitelist": [], "captcha_timeout": 60,
        "max_warns": 3,
        "auto_actions": {"1": "warn", "2": "mute", "3": "ban"},
        # Lock toggles
        "lock_forward": False, "lock_links": False, "lock_gifs": False,
        "lock_photos": False, "lock_videos": False,
        # Feature toggles
        "feature_addme": True, "feature_adhkar": True, "feature_pair": True,
        "feature_avatar": True, "feature_fun": True, "feature_welcome": True,
        "feature_responses": True, "feature_warns": True, "feature_id": True,
        "feature_link": True, "feature_kickme": True, "feature_protection": True
    }
    save_chat_settings(chat_id, default)
    return default

def get_all_chats() -> list:
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cur = conn.cursor()
        cur.execute("SELECT chat_id FROM chats")
        rows = [r[0] for r in cur.fetchall()]
        conn.close()
    return rows

def get_warn_count_sql(chat_id: int, user_id: int) -> int:
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cur = conn.cursor()
        cur.execute("SELECT count FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        row = cur.fetchone()
        conn.close()
    return row[0] if row else 0

def add_warn_sql(chat_id: int, user_id: int) -> int:
    now = int(time.time())
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cur = conn.cursor()
        cur.execute("SELECT count FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        row = cur.fetchone()
        if row:
            new_count = row[0] + 1
            cur.execute("UPDATE warns SET count=?, last_ts=? WHERE chat_id=? AND user_id=?", (new_count, now, chat_id, user_id))
        else:
            new_count = 1
            cur.execute("INSERT INTO warns (chat_id, user_id, count, last_ts) VALUES (?, ?, ?, ?)", (chat_id, user_id, new_count, now))
        conn.commit()
        conn.close()
    return new_count

def clear_warns_sql(chat_id: int, user_id: int):
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cur = conn.cursor()
        cur.execute("DELETE FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        conn.commit()
        conn.close()

def incr_warn(chat_id: int, user_id: int) -> int:
    if redis_client:
        key = f"warn:{chat_id}:{user_id}"
        return int(redis_client.incr(key))
    else:
        return add_warn_sql(chat_id, user_id)

def get_warn(chat_id: int, user_id: int) -> int:
    if redis_client:
        key = f"warn:{chat_id}:{user_id}"
        v = redis_client.get(key)
        return int(v) if v else 0
    else:
        return get_warn_count_sql(chat_id, user_id)

def clear_warns(chat_id: int, user_id: int):
    if redis_client:
        key = f"warn:{chat_id}:{user_id}"
        redis_client.delete(key)
    else:
        clear_warns_sql(chat_id, user_id)

# -------------------- In-memory and regex --------------------
user_msg_windows: Dict[Tuple[int, int], deque] = {}
captcha_store: Dict[str, dict] = {}
recent_messages: Dict[int, deque] = {}  # chat_id -> deque of (message_id, ts)
CLEANUP_INTERVAL = 30

LINK_RE = re.compile(r"(https?://|t\.me/|telegram\.me/|bit\.ly/|goo\.gl/)", re.I)
INVITE_RE = re.compile(r"(t\.me/joinchat|t\.me\/\+|t\.me\/invite|telegram\.me\/joinchat)", re.I)

# -------------------- Utilities --------------------
def audit_log(text: str):
    audit = get_global("AUDIT_CHAT_ID") or os.environ.get("AUDIT_CHAT_ID")
    if not audit:
        return
    try:
        bot.send_message(audit, f"📝 Audit: {text}")
    except Exception:
        logging.exception("audit_log failed")

def precheck_bot_permissions(chat_id: int, required: list) -> Tuple[bool, list]:
    try:
        me = bot.get_me()
        member = bot.get_chat_member(chat_id, me.id)
    except Exception:
        return False, ["bot_not_in_chat_or_network_error"]
    if member.status == "creator":
        return True, []
    missing = []
    for perm in required:
        val = False
        if hasattr(member, perm):
            val = getattr(member, perm)
        else:
            val = member.__dict__.get(perm, False)
        if not val:
            missing.append(perm)
    return len(missing) == 0, missing

def is_admin(chat_id: int, user_id: int) -> bool:
    try:
        member = bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        logging.exception("is_admin check failed")
        return False

def is_developer(user) -> bool:
    username = getattr(user, "username", "")
    if not username:
        return False
    return username.lower().lstrip("@") == DEVELOPER_USERNAME.lower().lstrip("@")

def is_whitelisted(settings: dict, user) -> bool:
    wl = settings.get("whitelist", [])
    username = getattr(user, "username", None)
    if str(user.id) in [str(x) for x in wl]:
        return True
    if username and username.lower() in [str(x).lower().lstrip("@") for x in wl]:
        return True
    return False

def record_message_and_count(chat_id: int, user_id: int, window_seconds: int) -> int:
    key = (chat_id, user_id)
    now = time.time()
    q = user_msg_windows.setdefault(key, deque())
    q.append(now)
    while q and now - q[0] > window_seconds:
        q.popleft()
    return len(q)

def restrict_member_temporarily(chat_id: int, user_id: int, minutes: int = 60):
    try:
        until_date = int(time.time()) + minutes * 60
        bot.restrict_chat_member(chat_id, user_id, until_date=until_date,
                                 can_send_messages=False, can_send_media_messages=False,
                                 can_send_other_messages=False, can_add_web_page_previews=False)
    except Exception:
        logging.exception("restrict_member_temporarily failed")
        return False
    return True

def unrestrict_member(chat_id: int, user_id: int):
    try:
        bot.restrict_chat_member(chat_id, user_id,
                                 can_send_messages=True, can_send_media_messages=True,
                                 can_send_other_messages=True, can_add_web_page_previews=True)
    except Exception:
        logging.exception("unrestrict_member failed")

def ban_member(chat_id: int, user_id: int):
    try:
        bot.ban_chat_member(chat_id, user_id)
        audit_log(f"Ban: chat={chat_id} user={user_id}")
    except Exception:
        logging.exception("ban_member failed")

def kick_member(chat_id: int, user_id: int, delay_seconds: float = 0.5):
    try:
        bot.ban_chat_member(chat_id, user_id)
        threading.Timer(delay_seconds, lambda: safe_unban(chat_id, user_id)).start()
        audit_log(f"Kick: chat={chat_id} user={user_id}")
    except Exception:
        logging.exception("kick_member failed")

def safe_unban(chat_id: int, user_id: int):
    try:
        bot.unban_chat_member(chat_id, user_id)
    except Exception:
        logging.exception("safe_unban failed")

# -------------------- Cleanup worker --------------------
def cleanup_worker():
    while True:
        try:
            now = time.time()
            expired = [k for k, v in list(captcha_store.items()) if v.get("expires_at", 0) <= now]
            for k in expired:
                data = captcha_store.pop(k, None)
                if data:
                    try:
                        bot.kick_chat_member(data["chat_id"], data["user_id"])
                        bot.unban_chat_member(data["chat_id"], data["user_id"])
                        bot.send_message(data["chat_id"], f"🛡️ تم طرد العضو لعدم اجتياز الكابتشا.")
                        audit_log(f"Captcha timeout kick chat={data['chat_id']} user={data['user_id']}")
                    except Exception:
                        logging.exception("cleanup captcha kick failed")
            to_delete = []
            for k, dq in list(user_msg_windows.items()):
                if not dq or (time.time() - dq[-1] > 600):
                    to_delete.append(k)
            for k in to_delete:
                user_msg_windows.pop(k, None)
            for cid, dq in list(recent_messages.items()):
                if len(dq) > 5000:
                    recent_messages[cid] = deque(list(dq)[-2000:], maxlen=2000)
        except Exception:
            logging.exception("cleanup_worker error")
        time.sleep(CLEANUP_INTERVAL)

threading.Thread(target=cleanup_worker, daemon=True).start()

# -------------------- Message recording --------------------
@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'video', 'audio', 'document', 'sticker', 'voice', 'video_note'])
def record_messages(message):
    try:
        dq = recent_messages.setdefault(message.chat.id, deque(maxlen=2000))
        ts = message.date.timestamp() if hasattr(message.date, "timestamp") else time.time()
        dq.append((message.message_id, ts))
    except Exception:
        logging.exception("record_messages failed")
    # allow other handlers

# -------------------- Fancy admin menu (m1..m6) --------------------
@bot.message_handler(func=lambda message: message.text == "الاوامر" and message.chat.type in ['group', 'supergroup'])
def send_admin_menu(message):
    admin_text = (
        "أهلاً بك عزيزي في قائمة الاوامر :\n"
        "━━━━━━━━━━━━━\n"
        "• م1 : اوامر الادمنيه\n"
        "• م2 : اوامر العدادات\n"
        "• م3 : اوامر القفل/الفتح - التعطيل/التفعيل\n"
        "• م4 : اوامر التسليه\n"
        "• م5 : الوامر الخدميه\n"
        "• م6 : اللعاب\n"
        "━━━━━━━━━━━━━"
    )
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("❶", callback_data="m1"),
               InlineKeyboardButton("❷", callback_data="m2"),
               InlineKeyboardButton("❸", callback_data="m3"))
    markup.row(InlineKeyboardButton("اوامر التسليه", callback_data="m4"),
               InlineKeyboardButton("اوامر الخدميه", callback_data="m5"))
    markup.add(InlineKeyboardButton("اللعاب", callback_data="m6"))
    try:
        bot.send_message(message.chat.id, admin_text, reply_markup=markup, reply_to_message_id=message.message_id)
    except Exception:
        logging.exception("failed to send fancy menu")

# map callback buttons to expanded text menus
@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("m"))
def menu_callback(c):
    key = c.data
    if key == "m1":
        text = (
            "م1 — أوامر الادمنيه:\n"
            "- رفع/تنزيل: مالك اساسي, مالك, مشرف, منشئ, مدير, ادمن, مميز\n"
            "- تنزيل الكل (رد)\n"
            "- مسح رتبة معينة (مسح الالكي، مسح النشئي، ...)\n"
            "- تحذير / تحذير نهائي / مسح تحذيراته\n            "
        )
    elif key == "m2":
        text = "م2 — أوامر العدادات:\n- /set_flood <count> <window>\n- تفعيل/تعطيل الميزات... إلخ."
    elif key == "m3":
        text = "م3 — أوامر القفل/الفتح:\n- قفل/فتح الروابط، الصور، الفيديو، التحركات، التوجيه ...\n- التفعيل/التعطيل العام للميزات."
    elif key == "m4":
        text = "م4 — أوامر التسليه: (رتب تسليه، الزواج، زوجي/زوجتي، طلق، ألعاب تفاعلية...)"
    elif key == "m5":
        text = "م5 — الأوامر الخدميه: (افتارات، بايو، تحميل، زخرف، من ضافني، قوقل ...)"
    elif key == "m6":
        text = "م6 — اللعاب: (حروف، انقليزي، رياضيات، صور، روليت، تحديات، بنك، اقتصاد...)"
    else:
        text = "قائمة غير معروفة."
    try:
        bot.answer_callback_query(c.id, text[:200] if len(text) > 200 else text)
    except Exception:
        pass

# -------------------- Captcha handlers --------------------
@bot.message_handler(content_types=['new_chat_members'])
def new_member_handler(message):
    try:
        settings = load_chat_settings(message.chat.id)
        for member in message.new_chat_members:
            if member.is_bot:
                continue
            if is_whitelisted(settings, member) or is_admin(message.chat.id, member.id):
                continue
            try:
                bot.restrict_chat_member(message.chat.id, member.id,
                                         can_send_messages=False, can_send_media_messages=False,
                                         can_send_other_messages=False, can_add_web_page_previews=False)
            except Exception:
                logging.exception("restrict new member failed")
            nonce = str(uuid.uuid4())
            captcha_store[nonce] = {"chat_id": message.chat.id, "user_id": member.id, "expires_at": int(time.time()) + settings.get("captcha_timeout", 60)}
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("✅ أنا إنسان", callback_data=f"captcha:{nonce}"))
            bot.send_message(message.chat.id, f"مرحباً {member.first_name} 👋 اضغط الزر خلال {settings.get('captcha_timeout',60)} ثانية.", reply_markup=markup)
    except Exception:
        logging.exception("new_member_handler error")

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("captcha:"))
def captcha_callback(call):
    try:
        nonce = call.data.split(":",1)[1]
        data = captcha_store.get(nonce)
        if not data:
            bot.answer_callback_query(call.id, "انتهت صلاحية الكابتشا أو غير صالحة.")
            return
        if call.from_user.id != data["user_id"]:
            bot.answer_callback_query(call.id, "هذه الكابتشا ليست لك.")
            return
        unrestrict_member(data["chat_id"], data["user_id"])
        bot.answer_callback_query(call.id, "تم التحقق بنجاح.")
        try:
            bot.edit_message_text("✅ تم التحقق بنجاح. مرحبًا بك!", chat_id=call.message.chat.id, message_id=call.message.message_id)
        except Exception:
            pass
        captcha_store.pop(nonce, None)
        audit_log(f"Captcha solved chat={data['chat_id']} user={data['user_id']}")
    except Exception:
        logging.exception("captcha_callback failed"); 
        try: bot.answer_callback_query(call.id, "خطأ داخلي عند التحقق.") 
        except: pass

# -------------------- Admin textual commands (lifting/demoting ranks) --------------------
# Utility: generic promote/demote
def promote_user(chat_id:int, user_id:int, role:str):
    # role indicates label only; we'll grant admin perms for most roles
    try:
        bot.promote_chat_member(chat_id, user_id,
                                can_change_info=True,
                                can_delete_messages=True,
                                can_restrict_members=True,
                                can_invite_users=True,
                                can_pin_messages=True,
                                can_promote_members=False)
        return True
    except Exception:
        logging.exception("promote_user failed")
        return False

def demote_user(chat_id:int, user_id:int):
    try:
        bot.promote_chat_member(chat_id, user_id,
                  
