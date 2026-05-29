import asyncio
import json
import os
import random
import sqlite3
import threading
import time
import logging
from contextlib import contextmanager

from flask import Flask
from telegram import (
    Update, BotCommand, BotCommandScopeDefault,
    BotCommandScopeAllGroupChats, ChatMember,
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ChatMemberHandler, ContextTypes, filters,
)

# =========================
# CONFIG
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "/tmp/bot_data.db")

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# =========================
# FLASK (health check for Render)
# =========================

web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "Bot is running"


def run_web():
    port = int(os.getenv("PORT", 10000))
    logging.info("Starting Flask on port %s", port)
    web_app.run(host="0.0.0.0", port=port)


# =========================
# DATABASE
# =========================

@contextmanager
def get_db():
    db_dir = os.path.dirname(os.path.abspath(DB_PATH))
    try:
        os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
    except (PermissionError, OSError):
        fallback = "/tmp/bot_data.db"
        logging.warning("Cannot write to %s, falling back to %s", DB_PATH, fallback)
        conn = sqlite3.connect(fallback)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS members (
                chat_id   INTEGER,
                user_id   INTEGER,
                username  TEXT    DEFAULT '',
                full_name TEXT    DEFAULT '',
                msg_count INTEGER DEFAULT 0,
                points    INTEGER DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS raffles (
                chat_id      INTEGER PRIMARY KEY,
                prize        TEXT,
                creator_id   INTEGER,
                participants TEXT    DEFAULT '[]',
                active       INTEGER DEFAULT 1
            )
        """)


def upsert_member(chat_id, user_id, username="", full_name="",
                  add_msg=False, add_points=0):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO members (chat_id, user_id, username, full_name, msg_count, points)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                username  = excluded.username,
                full_name = excluded.full_name,
                msg_count = msg_count + ?,
                points    = points    + ?
        """, (
            chat_id, user_id, username, full_name,
            1 if add_msg else 0, add_points,
            1 if add_msg else 0, add_points,
        ))


def get_members(chat_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM members WHERE chat_id = ? ORDER BY msg_count DESC",
            (chat_id,)
        ).fetchall()


def get_member(chat_id, user_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM members WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id)
        ).fetchone()


def get_top(chat_id, by="points", limit=10):
    with get_db() as conn:
        return conn.execute(
            f"SELECT * FROM members WHERE chat_id = ? ORDER BY {by} DESC LIMIT ?",
            (chat_id, limit)
        ).fetchall()


# =========================
# ACTIVE GAMES  {chat_id: {"number": int, "started_by": int, "attempts": int}}
# =========================

active_games: dict = {}


# =========================
# HELPERS
# =========================

def mention(user):
    return f"@{user.username}" if user.username else user.full_name


def fmt_name(row):
    return f"@{row['username']}" if row["username"] else row["full_name"]


MEDALS = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


def only_group(chat):
    return chat.type in ("group", "supergroup")


# =========================
# MESSAGE TRACKER
# =========================

async def track_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    user = update.effective_user
    chat = update.effective_chat
    if not only_group(chat):
        return

    upsert_member(chat.id, user.id, user.username or "", user.full_name,
                  add_msg=True, add_points=1)

    # Guessing game check
    game = active_games.get(chat.id)
    if not game or not update.message.text:
        return
    text = update.message.text.strip()
    if not text.isdigit():
        return

    guess = int(text)
    game["attempts"] += 1
    target = game["number"]

    if guess == target:
        del active_games[chat.id]
        upsert_member(chat.id, user.id, user.username or "", user.full_name,
                      add_points=50)
        await update.message.reply_text(
            f"🎉 {mention(user)} đoán đúng rồi! Số là *{target}*\n"
            f"Sau {game['attempts']} lần đoán — +50 điểm! 🏆",
            parse_mode="Markdown",
        )
    else:
        diff = abs(guess - target)
        if guess < target:
            direction = f"🔼 Số bí mật nằm *trên* {guess}"
        else:
            direction = f"🔽 Số bí mật nằm *dưới* {guess}"

        if diff <= 5:
            temp = "🔥🔥🔥 Cực kỳ gần!"
        elif diff <= 15:
            temp = "🔥 Rất gần!"
        elif diff <= 30:
            temp = "🌡️ Gần rồi!"
        else:
            temp = "❄️ Lạnh!"
        await update.message.reply_text(f"{direction}\n{temp}", parse_mode="Markdown")


# =========================
# COMMANDS
# =========================

async def seed_members_from_api(bot, chat_id: int):
    """Fetch admins from Telegram API and seed into DB."""
    try:
        admins = await bot.get_chat_administrators(chat_id)
        for cm in admins:
            u = cm.user
            if not u.is_bot:
                upsert_member(chat_id, u.id, u.username or "", u.full_name)
        logging.info("Seeded %d admins for chat %s", len(admins), chat_id)
    except Exception:
        logging.exception("seed_members_from_api failed | chat_id=%s", chat_id)


async def handle_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track members joining/leaving via ChatMemberUpdated events."""
    result = update.chat_member or update.my_chat_member
    if not result:
        return

    chat = result.chat
    new = result.new_chat_member
    u = new.user

    if u.is_bot:
        # Bot itself was added to a group — seed admins
        if new.status in (ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER):
            await seed_members_from_api(context.bot, chat.id)
        return

    if new.status in (ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER):
        upsert_member(chat.id, u.id, u.username or "", u.full_name)
        logging.info("Member joined | chat=%s | user=%s", chat.id, u.id)
    elif new.status in (ChatMember.LEFT, ChatMember.BANNED):
        logging.info("Member left | chat=%s | user=%s", chat.id, u.id)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if user:
        upsert_member(chat.id, user.id, user.username or "", user.full_name)
    # Auto-seed admins when /start is called in a group
    if only_group(chat):
        await seed_members_from_api(context.bot, chat.id)
    await update.message.reply_text(
        "🤖 *Bot Nhóm*\n\n"
        "📋 Danh sách lệnh:\n"
        "🎲 /random — chọn ngẫu nhiên thành viên\n"
        "🎰 /roll \\[max\\] — tung xúc xắc\n"
        "🎯 /guess — bắt đầu đoán số\n"
        "🛑 /stopguess — dừng đoán số\n"
        "🎁 /raffle <giải thưởng> — tạo bốc thăm\n"
        "✋ /join — tham gia bốc thăm\n"
        "🏁 /draw — quay số\n"
        "📊 /stats — thống kê tin nhắn\n"
        "🏆 /top — bảng xếp hạng điểm\n"
        "💰 /points — xem điểm của bạn\n"
        "🎁 /gift @user <điểm> — tặng điểm",
        parse_mode="Markdown",
    )


async def cmd_random(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not only_group(chat):
        await update.message.reply_text("❌ Lệnh này chỉ dùng trong nhóm.")
        return
    members = get_members(chat.id)
    if len(members) < 2:
        await update.message.reply_text(
            "⚠️ Chưa đủ thành viên (cần ít nhất 2 người đã nhắn tin trong nhóm)."
        )
        return
    chosen = random.choice(members)
    name = f"*{chosen['full_name']}*" + (f" (@{chosen['username']})" if chosen["username"] else "")
    await update.message.reply_text(
        f"🎲 *Kết quả random:*\n\n👤 {name}",
        parse_mode="Markdown",
    )


async def cmd_roll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    max_val = 6
    if context.args:
        try:
            max_val = max(2, min(int(context.args[0]), 1_000_000))
        except ValueError:
            pass
    result = random.randint(1, max_val)
    await update.message.reply_text(
        f"🎰 {mention(user)} tung xúc xắc \\(1–{max_val}\\):\n\n*{result}*",
        parse_mode="MarkdownV2",
    )


async def cmd_guess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not only_group(chat):
        await update.message.reply_text("❌ Lệnh này chỉ dùng trong nhóm.")
        return
    if chat.id in active_games:
        await update.message.reply_text("⚠️ Đang có game đoán số. Dùng /stopguess để dừng.")
        return
    number = random.randint(1, 100)
    active_games[chat.id] = {
        "number": number,
        "started_by": update.effective_user.id,
        "attempts": 0,
    }
    await update.message.reply_text(
        "🎯 *Trò chơi đoán số bắt đầu!*\n\n"
        "Mình đang nghĩ một số từ *1 đến 100*\n"
        "Ai đoán đúng trước nhận *+50 điểm!* 🏆\n\n"
        "Hãy gửi một số để đoán 👇",
        parse_mode="Markdown",
    )


async def cmd_stopguess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    game = active_games.pop(chat.id, None)
    if not game:
        await update.message.reply_text("Không có game đoán số nào đang diễn ra.")
        return
    await update.message.reply_text(
        f"🛑 Game đoán số đã dừng. Đáp án là *{game['number']}*",
        parse_mode="Markdown",
    )


async def cmd_raffle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not only_group(chat):
        await update.message.reply_text("❌ Lệnh này chỉ dùng trong nhóm.")
        return
    if not context.args:
        await update.message.reply_text("Dùng: /raffle <giải thưởng>\nVí dụ: /raffle iPhone 16")
        return
    prize = " ".join(context.args)
    user = update.effective_user
    with get_db() as conn:
        existing = conn.execute(
            "SELECT 1 FROM raffles WHERE chat_id = ? AND active = 1", (chat.id,)
        ).fetchone()
        if existing:
            await update.message.reply_text("⚠️ Đang có bốc thăm. Dùng /draw để quay số trước.")
            return
        first = json.dumps([{"id": user.id, "name": user.full_name, "username": user.username or ""}])
        conn.execute(
            "INSERT OR REPLACE INTO raffles (chat_id, prize, creator_id, participants, active) VALUES (?, ?, ?, ?, 1)",
            (chat.id, prize, user.id, first),
        )
    await update.message.reply_text(
        f"🎁 *Bốc thăm bắt đầu!*\n\n"
        f"Giải thưởng: *{prize}*\n"
        f"Tổ chức bởi: {mention(user)}\n\n"
        f"✋ Dùng /join để tham gia\n"
        f"🏁 Dùng /draw để quay số",
        parse_mode="Markdown",
    )


async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    with get_db() as conn:
        raffle = conn.execute(
            "SELECT * FROM raffles WHERE chat_id = ? AND active = 1", (chat.id,)
        ).fetchone()
        if not raffle:
            await update.message.reply_text("Không có bốc thăm nào đang diễn ra.")
            return
        participants = json.loads(raffle["participants"])
        if any(p["id"] == user.id for p in participants):
            await update.message.reply_text(f"✅ {mention(user)} đã tham gia rồi!")
            return
        participants.append({"id": user.id, "name": user.full_name, "username": user.username or ""})
        conn.execute(
            "UPDATE raffles SET participants = ? WHERE chat_id = ? AND active = 1",
            (json.dumps(participants), chat.id),
        )
    await update.message.reply_text(
        f"✅ *{user.full_name}* đã tham gia! Tổng: {len(participants)} người",
        parse_mode="Markdown",
    )


async def cmd_draw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    with get_db() as conn:
        raffle = conn.execute(
            "SELECT * FROM raffles WHERE chat_id = ? AND active = 1", (chat.id,)
        ).fetchone()
        if not raffle:
            await update.message.reply_text("Không có bốc thăm nào đang diễn ra.")
            return
        # Only creator or group admin can draw
        if raffle["creator_id"] != user.id:
            try:
                cm = await chat.get_member(user.id)
                if cm.status not in (ChatMember.ADMINISTRATOR, ChatMember.OWNER):
                    await update.message.reply_text("❌ Chỉ người tạo bốc thăm hoặc admin mới có thể quay số.")
                    return
            except Exception:
                await update.message.reply_text("❌ Không thể xác nhận quyền của bạn.")
                return
        participants = json.loads(raffle["participants"])
        if len(participants) < 2:
            await update.message.reply_text("⚠️ Cần ít nhất 2 người tham gia.")
            return
        winner = random.choice(participants)
        conn.execute(
            "UPDATE raffles SET active = 0 WHERE chat_id = ? AND active = 1", (chat.id,)
        )
    upsert_member(chat.id, winner["id"], winner["username"], winner["name"], add_points=100)
    winner_mention = f"@{winner['username']}" if winner["username"] else winner["name"]
    await update.message.reply_text(
        f"🎊 *Kết quả bốc thăm!*\n\n"
        f"Giải thưởng: *{raffle['prize']}*\n"
        f"Số người tham gia: {len(participants)}\n\n"
        f"🏆 Người thắng: *{winner_mention}*\n\n"
        f"Chúc mừng! +100 điểm 🎉",
        parse_mode="Markdown",
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not only_group(chat):
        await update.message.reply_text("❌ Lệnh này chỉ dùng trong nhóm.")
        return
    top = get_top(chat.id, by="msg_count", limit=10)
    if not top:
        await update.message.reply_text("Chưa có dữ liệu thống kê.")
        return
    text = "📊 *Thống kê tin nhắn (Top 10)*\n\n"
    for i, row in enumerate(top):
        medal = MEDALS[i] if i < len(MEDALS) else f"{i+1}."
        text += f"{medal} {fmt_name(row)} — {row['msg_count']} tin\n"
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not only_group(chat):
        await update.message.reply_text("❌ Lệnh này chỉ dùng trong nhóm.")
        return
    top = get_top(chat.id, by="points", limit=10)
    if not top:
        await update.message.reply_text("Chưa có dữ liệu xếp hạng.")
        return
    text = "🏆 *Bảng xếp hạng điểm*\n\n"
    for i, row in enumerate(top):
        medal = MEDALS[i] if i < len(MEDALS) else f"{i+1}."
        text += f"{medal} {fmt_name(row)} — {row['points']} điểm\n"
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    row = get_member(chat.id, user.id)
    if not row:
        await update.message.reply_text("Bạn chưa có điểm. Hãy nhắn tin trong nhóm để tích điểm!")
        return
    with get_db() as conn:
        rank = conn.execute(
            "SELECT COUNT(*) AS c FROM members WHERE chat_id = ? AND points > ?",
            (chat.id, row["points"]),
        ).fetchone()["c"] + 1
    await update.message.reply_text(
        f"💰 *Điểm của {user.full_name}*\n\n"
        f"Điểm: *{row['points']}*\n"
        f"Tin nhắn: {row['msg_count']}\n"
        f"Hạng: *#{rank}*",
        parse_mode="Markdown",
    )


async def cmd_gift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    target_user = None
    amount = 0

    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
        try:
            amount = int(context.args[0]) if context.args else 0
        except (ValueError, IndexError):
            amount = 0
    elif context.args and len(context.args) >= 2:
        try:
            amount = int(context.args[-1])
        except ValueError:
            await update.message.reply_text("Số điểm không hợp lệ.")
            return
        uname = context.args[0].lstrip("@")
        with get_db() as conn:
            found = conn.execute(
                "SELECT * FROM members WHERE chat_id = ? AND username = ?",
                (chat.id, uname),
            ).fetchone()
        if not found:
            await update.message.reply_text("Không tìm thấy người dùng này trong nhóm.")
            return

        class _FakeUser:
            def __init__(self, r):
                self.id = r["user_id"]
                self.full_name = r["full_name"]
                self.username = r["username"]

        target_user = _FakeUser(found)
    else:
        await update.message.reply_text(
            "Dùng: /gift @user <điểm>\nHoặc reply tin nhắn rồi: /gift <điểm>"
        )
        return

    if not target_user or amount <= 0:
        await update.message.reply_text("Số điểm không hợp lệ (phải > 0).")
        return
    if target_user.id == user.id:
        await update.message.reply_text("❌ Không thể tặng điểm cho chính mình!")
        return

    sender = get_member(chat.id, user.id)
    if not sender or sender["points"] < amount:
        current = sender["points"] if sender else 0
        await update.message.reply_text(f"❌ Không đủ điểm. Hiện có: {current}")
        return

    upsert_member(chat.id, user.id, user.username or "", user.full_name, add_points=-amount)
    upsert_member(chat.id, target_user.id, target_user.username or "", target_user.full_name,
                  add_points=amount)
    target_mention = f"@{target_user.username}" if target_user.username else target_user.full_name
    await update.message.reply_text(
        f"🎁 {mention(user)} đã tặng *{amount} điểm* cho {target_mention}!",
        parse_mode="Markdown",
    )


# =========================
# BOT SETUP
# =========================

async def post_init(app):
    commands = [
        BotCommand("start",     "Hiển thị menu"),
        BotCommand("random",    "Chọn ngẫu nhiên thành viên"),
        BotCommand("roll",      "Tung xúc xắc: /roll [max]"),
        BotCommand("guess",     "Bắt đầu đoán số"),
        BotCommand("stopguess", "Dừng đoán số"),
        BotCommand("raffle",    "Tạo bốc thăm: /raffle <giải>"),
        BotCommand("join",      "Tham gia bốc thăm"),
        BotCommand("draw",      "Quay số bốc thăm"),
        BotCommand("stats",     "Thống kê tin nhắn"),
        BotCommand("top",       "Bảng xếp hạng điểm"),
        BotCommand("points",    "Xem điểm của bạn"),
        BotCommand("gift",      "Tặng điểm: /gift @user <điểm>"),
    ]
    # Default scope (private chat)
    await app.bot.set_my_commands(commands, scope=BotCommandScopeDefault())
    # Group scope — shows autocomplete when typing / in groups
    await app.bot.set_my_commands(commands, scope=BotCommandScopeAllGroupChats())
    logging.info("Bot commands registered for default + group scopes")


# =========================
# BOT RUNNER
# =========================

def mask_token(v):
    if not v:
        return "MISSING"
    return v[:4] + "***" + v[-4:] if len(v) > 8 else "***"


def run_bot():
    logging.info("BOT_TOKEN: %s", mask_token(BOT_TOKEN))
    if not BOT_TOKEN:
        raise RuntimeError("Thiếu BOT_TOKEN")

    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("random",    cmd_random))
    app.add_handler(CommandHandler("roll",      cmd_roll))
    app.add_handler(CommandHandler("guess",     cmd_guess))
    app.add_handler(CommandHandler("stopguess", cmd_stopguess))
    app.add_handler(CommandHandler("raffle",    cmd_raffle))
    app.add_handler(CommandHandler("join",      cmd_join))
    app.add_handler(CommandHandler("draw",      cmd_draw))
    app.add_handler(CommandHandler("stats",     cmd_stats))
    app.add_handler(CommandHandler("top",       cmd_top))
    app.add_handler(CommandHandler("points",    cmd_points))
    app.add_handler(CommandHandler("gift",      cmd_gift))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_message))

    # Track member join/leave and bot being added to groups
    app.add_handler(ChatMemberHandler(handle_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(handle_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

    logging.info("Bot started with polling...")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "chat_member", "my_chat_member"],
    )


if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    run_bot()
