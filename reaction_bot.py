"""
50 Emoji Auto-Reaction Bot
Har message pe random emoji reaction deta hai.
Admin panel + Access keys + Credits system included.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import secrets
import sqlite3
import string
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, Update,
    ReactionTypeEmoji,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CallbackQueryHandler, CommandHandler,
    ContextTypes, ConversationHandler, MessageHandler, filters,
)

# ===========================================================================
# CONFIG
# ===========================================================================
BOT_TOKEN     = os.getenv("BOT_TOKEN", "8782521724:AAERLFvM-OsgRUxbw-JCckdqA0wdb64b670").strip()
DB_PATH       = os.getenv("DB_PATH", "reaction_bot.db").strip() or "reaction_bot.db"
OWNER_ID      = int(os.getenv("OWNER_ID", "8250721152").strip())
_env_admins   = os.getenv("ADMIN_IDS", str(OWNER_ID)).strip()
ADMIN_IDS: set[int] = {OWNER_ID}
for x in _env_admins.split(","):
    if x.strip().isdigit():
        ADMIN_IDS.add(int(x.strip()))

SUPER_ADMIN_NAME = "@indiancyberhub247"
SUPER_ADMIN_LINK = "https://t.me/indiancyberhub247"

# 🔥 50 ALLOWED REACTION EMOJIS (Telegram official list)
EMOJI_POOL = [
    "👍", "👎", "❤️", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱",
    "🤬", "😢", "🎉", "🤩", "🤮", "💩", "🙏", "👌", "🕊", "🤡",
    "🥱", "🥴", "😍", "🐳", "❤️‍🔥", "🌚", "🌭", "💯", "🤣", "⚡",
    "🍌", "🏆", "💔", "🤨", "😐", "🍓", "🍾", "💋", "🖕", "😈",
    "😴", "😭", "🤓", "👻", "👨‍💻", "👀", "🎃", "😇", "😨", "🤝",
]

# Reactions per user customization ke liye
DEFAULT_REACTIONS = EMOJI_POOL[:]

# ===========================================================================
# LOGGING
# ===========================================================================
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("reaction-bot")

# ===========================================================================
# DATABASE
# ===========================================================================
_DB_LOCK = threading.Lock()

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()

@contextmanager
def _conn():
    with _DB_LOCK:
        conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level="IMMEDIATE")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

def init_db() -> None:
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                first_seen  TEXT NOT NULL,
                last_seen   TEXT NOT NULL,
                username    TEXT,
                credits     INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS access_codes (
                code        TEXT PRIMARY KEY,
                created_at  TEXT NOT NULL,
                expires_at  TEXT NOT NULL,
                max_uses    INTEGER NOT NULL,
                uses        INTEGER NOT NULL DEFAULT 0,
                active      INTEGER NOT NULL DEFAULT 1,
                created_by  INTEGER
            );
            CREATE TABLE IF NOT EXISTS user_codes (
                user_id      INTEGER NOT NULL,
                code         TEXT NOT NULL,
                activated_at TEXT NOT NULL,
                expires_at   TEXT NOT NULL,
                PRIMARY KEY (user_id, code)
            );
            CREATE TABLE IF NOT EXISTS react_logs (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id   INTEGER NOT NULL,
                msg_id    INTEGER NOT NULL,
                user_id   INTEGER,
                emoji     TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id     INTEGER PRIMARY KEY,
                enabled     INTEGER NOT NULL DEFAULT 1,
                custom_emoji TEXT
            );
            CREATE TABLE IF NOT EXISTS rate_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id   INTEGER NOT NULL,
                timestamp TEXT NOT NULL
            );
        """)

def upsert_user(user_id: int, username: str) -> None:
    now = _iso(_now())
    with _conn() as c:
        c.execute("""
            INSERT INTO users (user_id, first_seen, last_seen, username, credits)
            VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(user_id) DO UPDATE SET
                last_seen = excluded.last_seen,
                username  = excluded.username
        """, (user_id, now, now, username or ""))

def get_credits(user_id: int) -> int:
    with _conn() as c:
        row = c.execute("SELECT credits FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row["credits"] if row else 0

def add_credits(user_id: int, amount: int) -> int:
    with _conn() as c:
        c.execute("""
            INSERT INTO users (user_id, first_seen, last_seen, username, credits)
            VALUES (?, ?, ?, '', ?)
            ON CONFLICT(user_id) DO UPDATE SET credits = MAX(0, credits + ?)
        """, (user_id, _iso(_now()), _iso(_now()), max(0, amount), amount))
        row = c.execute("SELECT credits FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row["credits"] if row else 0

def deduct_credit(user_id: int) -> bool:
    with _conn() as c:
        cur = c.execute(
            "UPDATE users SET credits = credits - 1 WHERE user_id = ? AND credits > 0",
            (user_id,),
        )
        return cur.rowcount > 0

_ALPHABET = string.ascii_uppercase + string.digits

def _rand_block(n: int = 4) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))

def _gen_code() -> str:
    return f"RXN-{_rand_block()}-{_rand_block()}"

def create_code(hours: int, max_uses: int, created_by: Optional[int] = None) -> str:
    now = _now()
    expires = now + timedelta(hours=hours)
    with _conn() as c:
        for _ in range(10):
            code = _gen_code()
            try:
                c.execute("""
                    INSERT INTO access_codes
                        (code, created_at, expires_at, max_uses, uses, active, created_by)
                    VALUES (?, ?, ?, ?, 0, 1, ?)
                """, (code, _iso(now), _iso(expires), max_uses, created_by))
                return code
            except sqlite3.IntegrityError:
                continue
    raise RuntimeError("Code generation failed")

def activate_code(user_id: int, code: str) -> tuple[bool, dict | str]:
    code = code.upper().strip()
    now = _now()
    with _conn() as c:
        row = c.execute("SELECT * FROM access_codes WHERE code = ?", (code,)).fetchone()
        if row is None:
            return False, "Key does not exist."
        if not row["active"]:
            return False, "Key revoked."
        if row["uses"] >= row["max_uses"]:
            return False, "Max uses reached."
        try:
            exp = datetime.fromisoformat(row["expires_at"])
        except Exception:
            return False, "Bad expiry."
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= now:
            return False, "Key expired."
        already = c.execute(
            "SELECT 1 FROM user_codes WHERE user_id = ? AND code = ?",
            (user_id, code),
        ).fetchone()
        if already:
            return False, "Already activated."

        c.execute(
            "INSERT INTO user_codes (user_id, code, activated_at, expires_at) VALUES (?, ?, ?, ?)",
            (user_id, code, _iso(now), _iso(exp)),
        )
        c.execute("UPDATE access_codes SET uses = uses + 1 WHERE code = ?", (code,))
    return True, {"code": code, "expires_at": exp}

def user_active_key_info(user_id: int) -> Optional[dict]:
    now = _now()
    with _conn() as c:
        rows = c.execute("""
            SELECT ac.code, ac.expires_at, ac.active, uc.activated_at
            FROM user_codes uc
            JOIN access_codes ac ON ac.code = uc.code
            WHERE uc.user_id = ?
            ORDER BY uc.activated_at DESC
        """, (user_id,)).fetchall()
    for r in rows:
        if not r["active"]:
            continue
        try:
            exp = datetime.fromisoformat(r["expires_at"])
        except Exception:
            continue
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp > now:
            return {"code": r["code"], "expires_at": exp}
    return None

def revoke_code(code: str) -> bool:
    with _conn() as c:
        cur = c.execute("UPDATE access_codes SET active = 0 WHERE code = ? AND active = 1",
                        (code.upper(),))
        return cur.rowcount > 0

def list_codes(limit: int = 30) -> list[sqlite3.Row]:
    with _conn() as c:
        return c.execute("""
            SELECT code, created_at, expires_at, max_uses, uses, active
            FROM access_codes ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()

def list_users(limit: int = 50) -> list[sqlite3.Row]:
    with _conn() as c:
        return c.execute("""
            SELECT user_id, username, last_seen, credits FROM users
            ORDER BY last_seen DESC LIMIT ?
        """, (limit,)).fetchall()

def log_reaction(chat_id: int, msg_id: int, user_id: Optional[int], emoji: str) -> None:
    with _conn() as c:
        c.execute("""
            INSERT INTO react_logs (chat_id, msg_id, user_id, emoji, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (chat_id, msg_id, user_id, emoji, _iso(_now())))

def get_chat_enabled(chat_id: int) -> bool:
    with _conn() as c:
        row = c.execute("SELECT enabled FROM chat_settings WHERE chat_id = ?",
                        (chat_id,)).fetchone()
        return bool(row["enabled"]) if row else True

def set_chat_enabled(chat_id: int, enabled: bool) -> None:
    with _conn() as c:
        c.execute("""
            INSERT INTO chat_settings (chat_id, enabled) VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET enabled = excluded.enabled
        """, (chat_id, 1 if enabled else 0))

def set_chat_custom_emoji(chat_id: int, emojis: str) -> None:
    with _conn() as c:
        c.execute("""
            INSERT INTO chat_settings (chat_id, custom_emoji) VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET custom_emoji = excluded.custom_emoji
        """, (chat_id, emojis))

def get_chat_emoji_pool(chat_id: int) -> list[str]:
    with _conn() as c:
        row = c.execute("SELECT custom_emoji FROM chat_settings WHERE chat_id = ?",
                        (chat_id,)).fetchone()
    if row and row["custom_emoji"]:
        # space-separated custom emojis
        parts = [e.strip() for e in row["custom_emoji"].split() if e.strip()]
        valid = [e for e in parts if e in EMOJI_POOL]
        return valid or DEFAULT_REACTIONS
    return DEFAULT_REACTIONS

def rate_limit_ok(user_id: int, max_calls: int = 30, window_seconds: int = 60) -> bool:
    cutoff = _iso(_now() - timedelta(seconds=window_seconds))
    with _conn() as c:
        n = c.execute(
            "SELECT COUNT(*) AS n FROM rate_log WHERE user_id = ? AND timestamp >= ?",
            (user_id, cutoff),
        ).fetchone()["n"]
        if n >= max_calls:
            return False
        c.execute("INSERT INTO rate_log (user_id, timestamp) VALUES (?, ?)",
                  (user_id, _iso(_now())))
        return True

def get_stats() -> dict:
    day_ago = _iso(_now() - timedelta(hours=24))
    with _conn() as c:
        users = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        total_keys = c.execute("SELECT COUNT(*) AS n FROM access_codes").fetchone()["n"]
        active_keys = c.execute(
            "SELECT COUNT(*) AS n FROM access_codes WHERE active = 1 AND expires_at > ?",
            (_iso(_now()),),
        ).fetchone()["n"]
        total_reacts = c.execute("SELECT COUNT(*) AS n FROM react_logs").fetchone()["n"]
        reacts_24h = c.execute(
            "SELECT COUNT(*) AS n FROM react_logs WHERE timestamp >= ?",
            (day_ago,),
        ).fetchone()["n"]
    return {
        "users": users, "total_keys": total_keys, "active_keys": active_keys,
        "total_reacts": total_reacts, "reacts_24h": reacts_24h,
    }

# ===========================================================================
# HELPERS
# ===========================================================================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def touch_user(update: Update) -> None:
    u = update.effective_user
    if u is not None:
        upsert_user(u.id, u.username or "")

def contact_block() -> str:
    return (
        "🎟 Access Key ke liye DM kare:\n"
        f"[{SUPER_ADMIN_NAME}]({SUPER_ADMIN_LINK})"
    )

def access_denied_text() -> str:
    return (
        "🔐 *Access Required*\n\n"
        "Reaction bot use karne ke liye valid key required hai.\n\n"
        "Key mile to:\n"
        "`/activate YOUR-KEY`\n\n"
        f"{contact_block()}"
    )

def check_access(user_id: int) -> bool:
    if is_admin(user_id):
        return True
    if user_active_key_info(user_id) is not None:
        return True
    if get_credits(user_id) > 0:
        return True
    return False

def main_menu(admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🔥 Reaction Info", callback_data="u:info"),
         InlineKeyboardButton("💎 My Credits", callback_data="u:credits")],
        [InlineKeyboardButton("🎟 Activate Key", callback_data="u:activate"),
         InlineKeyboardButton("📊 My Status", callback_data="u:status")],
        [InlineKeyboardButton("🎨 Emoji List", callback_data="u:emojis"),
         InlineKeyboardButton("ℹ️ Help", callback_data="u:help")],
    ]
    if admin:
        rows.append([InlineKeyboardButton("🛡 Admin Panel", callback_data="a:panel")])
    return InlineKeyboardMarkup(rows)

def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎟 Generate Key", callback_data="a:newcode")],
        [InlineKeyboardButton("📋 Active Keys", callback_data="a:codes"),
         InlineKeyboardButton("🚫 Revoke Key", callback_data="a:revoke")],
        [InlineKeyboardButton("👥 Users", callback_data="a:users"),
         InlineKeyboardButton("💎 Add Credits", callback_data="a:addcredits")],
        [InlineKeyboardButton("📊 Statistics", callback_data="a:stats")],
        [InlineKeyboardButton("⬅️ Back", callback_data="a:back")],
    ])

# ===========================================================================
# CORE — REACTION FUNCTION
# ===========================================================================
async def react_to_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg:
        return

    chat = update.effective_chat
    uid = update.effective_user.id if update.effective_user else None

    # Ignore bot's own messages
    if update.effective_user and update.effective_user.is_bot:
        return

    # Check chat enabled
    if not get_chat_enabled(chat.id):
        return

    # Access check for non-admin in private? 
    # Group/channel me sabke liye reaction chahiye, to access check skip karte hain.
    # Sirf private chat me access check lagayenge.
    if chat.type == "private":
        if not check_access(uid):
            return

    # Rate limit
    if uid and not rate_limit_ok(uid, max_calls=30, window_seconds=60):
        return

    pool = get_chat_emoji_pool(chat.id)
    emoji = random.choice(pool)

    try:
        await msg.set_reaction([ReactionTypeEmoji(emoji=emoji)])
        log_reaction(chat.id, msg.message_id, uid, emoji)
        log.info("Reacted %s to msg %s in chat %s", emoji, msg.message_id, chat.id)
    except Exception as e:
        log.warning("Reaction failed: %s", e)

# ===========================================================================
# USER COMMANDS
# ===========================================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    touch_user(update)
    uid = update.effective_user.id
    admin_flag = "🛡 *You are an ADMIN.*\n\n" if is_admin(uid) else ""
    text = (
        "🔥 *50 Emoji Auto-Reaction Bot*\n\n"
        f"{admin_flag}"
        "📌 *Ye bot kya karta hai:*\n"
        "• Group/channel me har message pe *random emoji* se react karta hai\n"
        "• 50+ Telegram-allowed emojis ka pool\n"
        "• Har chat me custom emoji set kar sakte ho\n\n"
        "📱 *Kaise use karein:*\n"
        "1️⃣ Bot ko group me add karo\n"
        "2️⃣ Bot ko *admin* banao\n"
        "3️⃣ Bas! Har message pe auto react karega\n\n"
        "💎 Credits: `{}`\n\n"
        "• /activate CODE – key activate\n"
        "• /credits – balance\n"
        "• /status – status\n"
        "• /emojis – 50 emoji list\n"
        "• /toggle – group me on/off (admin)\n"
        "• /setemoji – custom emoji set (admin)\n"
        "• /help – help"
    ).format(get_credits(uid))
    if is_admin(uid):
        text += "\n\n🛡 `/admin` – Admin Panel"
    await update.message.reply_text(
        text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu(is_admin(uid))
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    touch_user(update)
    uid = update.effective_user.id
    text = (
        "ℹ️ *Help*\n\n"
        "*Setup:*\n"
        "1️⃣ Bot ko group me add karo\n"
        "2️⃣ Bot ko admin banao (reaction permission ke saath)\n"
        "3️⃣ Done! Har message pe react karega\n\n"
        "*Commands:*\n"
        "`/start` – main menu\n"
        "`/activate CODE` – key activate\n"
        "`/credits` – balance\n"
        "`/status` – status\n"
        "`/emojis` – 50 emoji list\n"
        "`/toggle` – reaction on/off (group admin)\n"
        "`/setemoji 👍 🔥 ❤️` – custom emoji set (group admin)\n"
        "`/clearemoji` – custom emoji reset\n"
        "`/help` – ye message\n\n"
        f"{contact_block()}"
    )
    if is_admin(uid):
        text += (
            "\n\n*Bot Admin:*\n"
            "`/admin` – panel\n"
            "`/newcode [hours] [uses]`\n"
            "`/addcredits USER_ID AMOUNT`\n"
            "`/revoke CODE`\n`/codes`\n`/users`\n`/stats`"
        )
    await update.message.reply_text(
        text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu(is_admin(uid))
    )

async def cmd_activate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    touch_user(update)
    if not context.args:
        await update.message.reply_text(
            "Usage: `/activate RXN-XXXX-XXXX`\n\n" + contact_block(),
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    code = context.args[0].strip().upper()
    ok, info = activate_code(update.effective_user.id, code)
    if not ok:
        await update.message.reply_text(
            f"❌ *Invalid Key*\n\nReason: {info}\n\n" + contact_block(),
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    exp_str = info["expires_at"].strftime("%Y-%m-%d %H:%M UTC")
    await update.message.reply_text(
        "✅ *Access Activated*\n\n"
        f"🔑 Key: `{info['code']}`\n"
        f"⏳ Valid till: `{exp_str}`\n\n"
        "Ab aap unlimited reaction bot use kar sakte ho.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu(is_admin(update.effective_user.id)),
    )

async def cmd_credits(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    touch_user(update)
    uid = update.effective_user.id
    bal = get_credits(uid)
    has_key = user_active_key_info(uid) is not None
    if is_admin(uid):
        text = f"💎 Credits: *{bal}*\n🛡 Admin: *Unlimited*"
    elif has_key:
        text = f"💎 Credits: *{bal}*\n♾ *Unlimited* (active key)"
    else:
        text = f"💎 Credits: *{bal}*"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN,
                                    reply_markup=main_menu(is_admin(uid)))

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    touch_user(update)
    uid = update.effective_user.id
    info = user_active_key_info(uid)
    allowed = check_access(uid)
    key_line = f"`{info['code']}`" if info else "—"
    exp_line = info["expires_at"].strftime("%Y-%m-%d %H:%M UTC") if info else "—"
    text = (
        "📊 *Your Status*\n\n"
        f"🆔 User ID: `{uid}`\n"
        f"🔐 Access: *{'🟢 Active' if allowed else '🔴 Inactive'}*\n"
        f"🎟 Key: {key_line}\n"
        f"⏳ Expiry: `{exp_line}`\n"
        f"💎 Credits: `{get_credits(uid)}`\n"
        f"🛡 Admin: `{'Yes' if is_admin(uid) else 'No'}`"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN,
                                    reply_markup=main_menu(is_admin(uid)))

async def cmd_emojis(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    touch_user(update)
    # 50 emojis, 10 per line
    lines = []
    for i in range(0, len(EMOJI_POOL), 10):
        lines.append("  ".join(EMOJI_POOL[i:i+10]))
    text = (
        "🎨 *50 Reaction Emojis Pool*\n\n"
        + "\n".join(lines) +
        "\n\n_Ye sab Telegram-allowed reactions hain._\n"
        "Bot inme se random emoji har message pe use karta hai."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Group me reaction on/off (chat admin only)."""
    chat = update.effective_chat
    uid = update.effective_user.id
    if chat.type == "private":
        await update.message.reply_text("Ye command group me use karo.")
        return
    # chat admin check
    try:
        member = await chat.get_member(uid)
        if member.status not in ("administrator", "creator") and not is_admin(uid):
            await update.message.reply_text("⛔ Sirf group admin ye kar sakta hai.")
            return
    except Exception:
        pass
    current = get_chat_enabled(chat.id)
    set_chat_enabled(chat.id, not current)
    await update.message.reply_text(
        f"✅ Reactions {'ON' if not current else 'OFF'} kar diye is group me."
    )

async def cmd_setemoji(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Custom emoji set for this chat."""
    chat = update.effective_chat
    uid = update.effective_user.id
    if chat.type == "private":
        await update.message.reply_text("Group me use karo.")
        return
    try:
        member = await chat.get_member(uid)
        if member.status not in ("administrator", "creator") and not is_admin(uid):
            await update.message.reply_text("⛔ Sirf group admin.")
            return
    except Exception:
        pass
    if not context.args:
        await update.message.reply_text(
            "Usage: `/setemoji 👍 🔥 ❤️`\n\n"
            "Ye emojis allowed hain:\n" + " ".join(EMOJI_POOL),
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    valid = [a for a in context.args if a in EMOJI_POOL]
    if not valid:
        await update.message.reply_text(
            "❌ Koi valid emoji nahi mila.\nAllowed: " + " ".join(EMOJI_POOL)
        )
        return
    set_chat_custom_emoji(chat.id, " ".join(valid))
    await update.message.reply_text(
        f"✅ Custom emoji set: {' '.join(valid)}\n"
        f"Ab is group me sirf inhi me se react hoga."
    )

async def cmd_clearemoji(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    uid = update.effective_user.id
    if chat.type == "private":
        await update.message.reply_text("Group me use karo.")
        return
    try:
        member = await chat.get_member(uid)
        if member.status not in ("administrator", "creator") and not is_admin(uid):
            await update.message.reply_text("⛔ Sirf group admin.")
            return
    except Exception:
        pass
    set_chat_custom_emoji(chat.id, "")
    await update.message.reply_text("✅ Custom emoji reset. Ab 50 emoji pool use hoga.")

# ===========================================================================
# ADMIN
# ===========================================================================
def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        u = update.effective_user
        if u is None or not is_admin(u.id):
            if update.message:
                await update.message.reply_text("⛔ Unauthorized.")
            elif update.callback_query:
                await update.callback_query.answer("⛔ Unauthorized", show_alert=True)
            return
        touch_user(update)
        return await func(update, context)
    return wrapper

@admin_only
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🛡 *ADMIN PANEL*\n\n"
        f"👑 Owner: `{OWNER_ID}`\n"
        f"👥 Admins: `{len(ADMIN_IDS)}`\n\n"
        "*Commands:*\n"
        "`/newcode [hours] [uses]`\n"
        "`/addcredits USER_ID AMOUNT`\n"
        "`/revoke CODE`\n`/codes`\n`/users`\n`/stats`"
    )
    if update.callback_query:
        try:
            await update.callback_query.message.edit_text(
                text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu())
        except Exception:
            await update.callback_query.message.reply_text(
                text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu())
    else:
        await update.message.reply_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu())

@admin_only
async def cmd_newcode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hours, uses = 24, 5
    args = context.args or []
    try:
        if len(args) >= 1: hours = int(args[0])
        if len(args) >= 2: uses = int(args[1])
    except ValueError:
        await update.message.reply_text("Usage: `/newcode [hours] [uses]`",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    if not (1 <= hours <= 876000) or not (1 <= uses <= 10000):
        await update.message.reply_text("Hours 1-876000, Uses 1-10000")
        return
    code = create_code(hours=hours, max_uses=uses, created_by=update.effective_user.id)
    await update.message.reply_text(
        "✅ *Key Generated*\n\n"
        f"🔑 `{code}`\n"
        f"⏳ `{hours} hours`\n"
        f"👥 `{uses}` uses",
        parse_mode=ParseMode.MARKDOWN,
    )

@admin_only
async def cmd_addcredits(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Usage: `/addcredits USER_ID AMOUNT`",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    try:
        target, amount = int(context.args[0]), int(context.args[1])
    except ValueError:
        await update.message.reply_text("Numbers do.")
        return
    new_bal = add_credits(target, amount)
    await update.message.reply_text(
        f"✅ `{target}` → `{amount:+d}` credits\n💎 Balance: `{new_bal}`",
        parse_mode=ParseMode.MARKDOWN,
    )

@admin_only
async def cmd_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: `/revoke RXN-XXXX-XXXX`",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    ok = revoke_code(context.args[0])
    await update.message.reply_text("✅ Revoked." if ok else "❌ Not found.")

@admin_only
async def cmd_codes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = list_codes(30)
    if not rows:
        await update.message.reply_text("No keys. Use `/newcode 24 5`",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    lines = ["📋 *Recent Keys*", ""]
    now = _now()
    for r in rows:
        try:
            exp = datetime.fromisoformat(r["expires_at"])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            ok = r["active"] and exp > now and r["uses"] < r["max_uses"]
        except Exception:
            ok = False
        lines.append(
            f"`{r['code']}` – {r['uses']}/{r['max_uses']} – "
            f"exp {r['expires_at'][:16]} – {'✅' if ok else '❌'}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

@admin_only
async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = list_users(50)
    if not rows:
        await update.message.reply_text("No users.")
        return
    lines = ["👥 *Recent Users*", ""]
    for r in rows:
        uname = f"@{r['username']}" if r["username"] else "—"
        lines.append(f"`{r['user_id']}` – {uname} – 💎{r['credits']} – {r['last_seen'][:16]}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

@admin_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = get_stats()
    text = (
        "📊 *Statistics*\n\n"
        f"👥 Users: `{s['users']}`\n"
        f"🎟 Active keys: `{s['active_keys']}`\n"
        f"🎟 Total keys: `{s['total_keys']}`\n"
        f"🔥 Total reactions: `{s['total_reacts']}`\n"
        f"🔥 Reactions (24h): `{s['reacts_24h']}`"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ===========================================================================
# GENERATE KEY CONVERSATION
# ===========================================================================
GEN_HOURS, GEN_USES = range(2)

async def gen_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(update.effective_user.id):
        await q.answer("⛔ Unauthorized", show_alert=True)
        return ConversationHandler.END
    context.user_data["gen"] = {}
    await q.message.reply_text(
        "🎟 *Generate Key* — Step 1/2\n\n"
        "Validity hours bhejo (e.g. `720` = 30 din).\n"
        "Cancel: /cancel",
        parse_mode=ParseMode.MARKDOWN,
    )
    return GEN_HOURS

async def gen_hours(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    txt = (update.message.text or "").strip()
    if not txt.isdigit() or not (1 <= int(txt) <= 876000):
        await update.message.reply_text("❌ 1–876000 ke beech integer.")
        return GEN_HOURS
    context.user_data["gen"]["hours"] = int(txt)
    await update.message.reply_text("👥 *Step 2/2* — Max uses:", parse_mode=ParseMode.MARKDOWN)
    return GEN_USES

async def gen_uses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    txt = (update.message.text or "").strip()
    if not txt.isdigit() or not (1 <= int(txt) <= 10000):
        await update.message.reply_text("❌ 1–10000 ke beech integer.")
        return GEN_USES
    g = context.user_data.pop("gen", {})
    hours = g.get("hours", 24)
    uses = int(txt)
    code = create_code(hours=hours, max_uses=uses, created_by=update.effective_user.id)
    await update.message.reply_text(
        "✅ *Key Generated*\n\n"
        f"🔑 `{code}`\n"
        f"⏳ `{hours} hours`\n"
        f"👥 `{uses}` uses",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END

async def gen_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("gen", None)
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

# ===========================================================================
# CALLBACKS
# ===========================================================================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    touch_user(update)
    data = q.data or ""
    uid = update.effective_user.id

    if data == "u:info":
        await q.message.reply_text(
            "🔥 *Reaction Bot Info*\n\n"
            "• Bot ko group me add karo\n"
            "• Bot ko *admin* banao\n"
            "• Har message pe 50 me se random emoji react karega\n\n"
            "Group admin `/toggle` se on/off kar sakta hai,\n"
            "aur `/setemoji` se custom list set kar sakta hai.",
            parse_mode=ParseMode.MARKDOWN,
        )
    elif data == "u:credits":
        await cmd_credits(update, context)
    elif data == "u:status":
        await cmd_status(update, context)
    elif data == "u:emojis":
        await cmd_emojis(update, context)
    elif data == "u:activate":
        await q.message.reply_text(
            "Apna key bhejo:\n`/activate RXN-XXXX-XXXX`\n\n" + contact_block(),
            parse_mode=ParseMode.MARKDOWN,
        )
    elif data == "u:help":
        await cmd_help(update, context)
    elif data == "a:panel":
        if not is_admin(uid):
            await q.answer("⛔", show_alert=True); return
        await cmd_admin(update, context)
    elif data == "a:codes":
        if not is_admin(uid): await q.answer("⛔", show_alert=True); return
        await cmd_codes(update, context)
    elif data == "a:revoke":
        if not is_admin(uid): await q.answer("⛔", show_alert=True); return
        await q.message.reply_text("Use: `/revoke RXN-XXXX-XXXX`",
                                   parse_mode=ParseMode.MARKDOWN)
    elif data == "a:users":
        if not is_admin(uid): await q.answer("⛔", show_alert=True); return
        await cmd_users(update, context)
    elif data == "a:addcredits":
        if not is_admin(uid): await q.answer("⛔", show_alert=True); return
        await q.message.reply_text("Use: `/addcredits USER_ID AMOUNT`",
                                   parse_mode=ParseMode.MARKDOWN)
    elif data == "a:stats":
        if not is_admin(uid): await q.answer("⛔", show_alert=True); return
        await cmd_stats(update, context)
    elif data == "a:back":
        try:
            await q.message.edit_text(
                "🏠 *Main Menu*", parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_menu(is_admin(uid)))
        except Exception:
            await q.message.reply_text(
                "🏠 *Main Menu*", parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_menu(is_admin(uid)))

# ===========================================================================
# ERROR
# ===========================================================================
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled exception", exc_info=context.error)

# ===========================================================================
# MAIN
# ===========================================================================
async def run() -> None:
    init_db()
    log.info("Owner: %s | Admins: %s", OWNER_ID, ADMIN_IDS)
    log.info("Emoji pool size: %d", len(EMOJI_POOL))

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Conversation first
    gen_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(gen_entry, pattern=r"^a:newcode$")],
        states={
            GEN_HOURS: [MessageHandler(filters.TEXT & ~filters.COMMAND, gen_hours)],
            GEN_USES:  [MessageHandler(filters.TEXT & ~filters.COMMAND, gen_uses)],
        },
        fallbacks=[CommandHandler("cancel", gen_cancel)],
        per_chat=True, per_user=True,
    )
    app.add_handler(gen_conv)

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("activate", cmd_activate))
    app.add_handler(CommandHandler("credits", cmd_credits))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("emojis", cmd_emojis))
    app.add_handler(CommandHandler("toggle", cmd_toggle))
    app.add_handler(CommandHandler("setemoji", cmd_setemoji))
    app.add_handler(CommandHandler("clearemoji", cmd_clearemoji))

    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("newcode", cmd_newcode))
    app.add_handler(CommandHandler("addcredits", cmd_addcredits))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CommandHandler("codes", cmd_codes))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("stats", cmd_stats))

    app.add_handler(CallbackQueryHandler(on_callback))

    # 🔥 Core: har message pe react
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Sticker.ALL |
         filters.Document.ALL | filters.AUDIO | filters.VOICE | filters.VIDEO_NOTE)
        & ~filters.COMMAND,
        react_to_message,
    ))

    app.add_error_handler(on_error)

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    log.info("✅ Reaction bot started!")

    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down…")

if __name__ == "__main__":
    main()
