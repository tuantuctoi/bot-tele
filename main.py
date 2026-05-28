import asyncio
import hashlib
import hmac
import json
import os
import time
import threading
import logging
import requests
from functools import wraps
from urllib.parse import parse_qsl, unquote

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# =========================
# CONFIG
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
VIOTP_TOKEN = os.getenv("VIOTP_TOKEN")
ADMIN_IDS_ENV = os.getenv("ADMIN_IDS", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")

VIOTP_API = "https://api.viotp.com"

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

ADMIN_IDS = {
    int(x.strip())
    for x in ADMIN_IDS_ENV.split(",")
    if x.strip().isdigit()
}

RATE_LIMIT_SECONDS = 3
last_command_time = {}

# Flask app để Render có port web service
_FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend", "dist")
web_app = Flask(__name__, static_folder=_FRONTEND_DIR, static_url_path="/")
CORS(web_app)


# =========================
# INIT DATA AUTH
# =========================

def verify_init_data(init_data_raw: str) -> dict | None:
    """Validate Telegram WebApp initData. Returns parsed user dict or None."""
    try:
        params = dict(parse_qsl(init_data_raw, strict_parsing=True))
        received_hash = params.pop("hash", None)
        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(params.items())
        )

        secret_key = hmac.new(
            b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256
        ).digest()

        expected_hash = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected_hash, received_hash):
            return None

        user_json = params.get("user")
        return json.loads(unquote(user_json)) if user_json else {}

    except Exception:
        logging.exception("verify_init_data failed")
        return None


def require_auth(f):
    """Flask decorator: validate initData header, restrict to ADMIN_IDS."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        init_data_raw = request.headers.get("Authorization", "")
        user = verify_init_data(init_data_raw)

        if user is None:
            logging.warning("API auth failed | path=%s", request.path)
            return jsonify({"success": False, "message": "Unauthorized"}), 401

        user_id = user.get("id")
        if ADMIN_IDS and user_id not in ADMIN_IDS:
            logging.warning("API non-admin blocked | user_id=%s", user_id)
            return jsonify({"success": False, "message": "Forbidden"}), 403

        return f(*args, **kwargs)
    return wrapper


# =========================
# FLASK API ROUTES
# =========================

@web_app.route("/api/balance")
@require_auth
def api_balance():
    data = viotp_get("/users/balance")
    return jsonify(data)


@web_app.route("/api/networks")
@require_auth
def api_networks():
    data = viotp_get("/networks/get")
    return jsonify(data)


@web_app.route("/api/services")
@require_auth
def api_services():
    country = request.args.get("country", "vn")
    data = viotp_get("/service/getv2", {"country": country})
    return jsonify(data)


@web_app.route("/api/buy")
@require_auth
def api_buy():
    params = {"serviceId": request.args.get("serviceId", "")}
    for key in ("network", "prefix", "exceptPrefix", "number", "country"):
        val = request.args.get(key)
        if val:
            params[key] = val
    data = viotp_get("/request/getv2", params)
    return jsonify(data)


@web_app.route("/api/code/<request_id>")
@require_auth
def api_code(request_id):
    data = viotp_get("/session/getv2", {"requestId": request_id})
    return jsonify(data)


@web_app.route("/api/history")
@require_auth
def api_history():
    params = {}
    for key in ("service", "status", "limit", "fromDate", "toDate"):
        val = request.args.get(key)
        if val:
            params[key] = val
    data = viotp_get("/session/historyv2", params)
    return jsonify(data)


@web_app.route("/", defaults={"path": ""})
@web_app.route("/<path:path>")
def serve_frontend(path):
    if path and os.path.exists(os.path.join(_FRONTEND_DIR, path)):
        return send_from_directory(_FRONTEND_DIR, path)
    return send_from_directory(_FRONTEND_DIR, "index.html")


def run_web():
    port = int(os.getenv("PORT", 10000))
    logging.info("Starting Flask web server on port %s", port)
    web_app.run(host="0.0.0.0", port=port)


# =========================
# LOG HELPERS
# =========================

def log_command(update: Update, command_name: str):
    user = update.effective_user
    chat = update.effective_chat
    message = update.message

    logging.info("========== COMMAND RECEIVED ==========")
    logging.info("Command: %s", command_name)
    logging.info("User ID: %s", user.id if user else None)
    logging.info("Username: @%s", user.username if user and user.username else None)
    logging.info("Full name: %s", user.full_name if user else None)
    logging.info("Chat ID: %s", chat.id if chat else None)
    logging.info("Chat type: %s", chat.type if chat else None)
    logging.info("Text: %s", message.text if message else None)
    logging.info("======================================")


def mask_token(value: str | None) -> str:
    if not value:
        return "MISSING"

    if len(value) <= 8:
        return "***"

    return value[:4] + "***" + value[-4:]


# =========================
# SECURITY / RATE LIMIT
# =========================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_rate_limited(user_id: int) -> bool:
    now = time.time()
    last = last_command_time.get(user_id, 0)

    if now - last < RATE_LIMIT_SECONDS:
        return True

    last_command_time[user_id] = now
    return False


async def guard(update: Update) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    message = update.message

    logging.info(
        "Guard check | user_id=%s | chat_id=%s | text=%s",
        user.id if user else None,
        chat.id if chat else None,
        message.text if message else None,
    )

    if not update.message or not user:
        logging.warning("Guard failed: missing message or user")
        return False

    if not is_admin(user.id):
        logging.warning("Unauthorized user blocked | user_id=%s", user.id)
        await update.message.reply_text("Không có quyền sử dụng bot.")
        return False

    if is_rate_limited(user.id):
        logging.warning("Rate limited | user_id=%s", user.id)
        await update.message.reply_text(
            "Bạn thao tác quá nhanh, vui lòng thử lại sau vài giây."
        )
        return False

    logging.info("Guard passed | user_id=%s", user.id)
    return True


# =========================
# VIOTP API
# =========================

def viotp_get(path: str, params: dict | None = None) -> dict:
    params = params or {}
    params["token"] = VIOTP_TOKEN

    safe_params = {
        k: "***" if k == "token" else v
        for k, v in params.items()
    }

    try:
        logging.info("VIOTP request | path=%s | params=%s", path, safe_params)

        res = requests.get(
            f"{VIOTP_API}{path}",
            params=params,
            timeout=20,
        )

        logging.info("VIOTP HTTP status: %s", res.status_code)
        logging.info("VIOTP response text: %s", res.text)

        return res.json()

    except Exception as e:
        logging.exception("VIOTP request error")
        return {
            "success": False,
            "message": str(e),
        }


# =========================
# COMMANDS
# =========================

async def open_app(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_command(update, "/app")

    if not await guard(update):
        return

    if not WEBAPP_URL:
        await update.message.reply_text("WEBAPP_URL chưa được cấu hình.")
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Mở VIOTP Mini App", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    await update.message.reply_text("Bấm nút bên dưới để mở Mini App:", reply_markup=keyboard)

    logging.info("Replied /app button | url=%s", WEBAPP_URL)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_command(update, "/start")

    if not await guard(update):
        return

    text = (
        "Bot VIOTP\n\n"
        "/app - mở Mini App (giao diện đầy đủ)\n"
        "/balance - kiểm tra số dư\n"
        "/services - danh sách dịch vụ\n"
        "/buy <service_id> - thuê số\n"
        "/code <request_id> - lấy OTP\n"
        "/id - xem Telegram ID"
    )

    if WEBAPP_URL:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Mở Mini App", web_app=WebAppInfo(url=WEBAPP_URL))]
        ])
        await update.message.reply_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text)

    logging.info("Replied /start menu")


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_command(update, "/id")

    user = update.effective_user
    await update.message.reply_text(f"Telegram ID của bạn là: {user.id}")

    logging.info("Replied /id | user_id=%s", user.id)


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_command(update, "/balance")

    if not await guard(update):
        return

    logging.info("Calling VIOTP balance API")

    data = viotp_get("/users/balance")

    logging.info("Balance API result: %s", data)

    if data.get("success"):
        balance_value = data["data"]["balance"]

        await update.message.reply_text(f"Số dư: {balance_value}")

        logging.info("Replied balance | balance=%s", balance_value)
    else:
        await update.message.reply_text(f"Lỗi: {data.get('message')}")

        logging.warning("Balance failed | message=%s", data.get("message"))


async def services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_command(update, "/services")

    if not await guard(update):
        return

    logging.info("Calling VIOTP services API")

    data = viotp_get("/service/getv2", {"country": "vn"})

    logging.info("Services API result success=%s", data.get("success"))

    if not data.get("success"):
        await update.message.reply_text(f"Lỗi: {data.get('message')}")

        logging.warning("Services failed | message=%s", data.get("message"))
        return

    services_list = data.get("data", [])

    logging.info("Services count: %s", len(services_list))

    text = "Danh sách dịch vụ:\n\n"

    for s in services_list[:50]:
        text += f"{s.get('id')} - {s.get('name')} - {s.get('price')}đ\n"

    for i in range(0, len(text), 3900):
        await update.message.reply_text(text[i:i + 3900])

    logging.info("Replied services list")


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_command(update, "/buy")

    logging.info("Buy args: %s", context.args)

    if not await guard(update):
        return

    if len(context.args) < 1:
        await update.message.reply_text("Dùng: /buy <service_id>\nVí dụ: /buy 1")

        logging.warning("Buy missing service_id")
        return

    service_id = context.args[0]

    logging.info("Calling VIOTP buy API | service_id=%s", service_id)

    data = viotp_get("/request/getv2", {
        "serviceId": service_id,
    })

    logging.info("Buy API result: %s", data)

    if data.get("success"):
        d = data["data"]

        await update.message.reply_text(
            "Thuê số thành công\n\n"
            f"SĐT: {d.get('phone_number')}\n"
            f"Request ID: {d.get('request_id')}\n"
            f"Số dư còn lại: {d.get('balance')}\n\n"
            f"Lấy OTP:\n/code {d.get('request_id')}"
        )

        logging.info(
            "Buy success | service_id=%s | request_id=%s | phone=%s",
            service_id,
            d.get("request_id"),
            d.get("phone_number"),
        )
    else:
        await update.message.reply_text(f"Lỗi: {data.get('message')}")

        logging.warning("Buy failed | message=%s", data.get("message"))


async def code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_command(update, "/code")

    logging.info("Code args: %s", context.args)

    if not await guard(update):
        return

    if len(context.args) < 1:
        await update.message.reply_text(
            "Dùng: /code <request_id>\nVí dụ: /code 123456"
        )

        logging.warning("Code missing request_id")
        return

    request_id = context.args[0]

    POLL_INTERVAL = 5       # giây giữa mỗi lần kiểm tra
    MAX_WAIT = 5 * 60       # tổng thời gian tối đa (5 phút)
    max_attempts = MAX_WAIT // POLL_INTERVAL

    status_msg = await update.message.reply_text(
        f"Đang chờ OTP cho request `{request_id}`...\n"
        f"Tự động kiểm tra mỗi {POLL_INTERVAL}s, tối đa {MAX_WAIT // 60} phút.",
        parse_mode="Markdown",
    )

    logging.info(
        "Starting OTP polling | request_id=%s | max_attempts=%s",
        request_id, max_attempts,
    )

    for attempt in range(1, max_attempts + 1):
        await asyncio.sleep(POLL_INTERVAL)

        logging.info(
            "OTP poll attempt %s/%s | request_id=%s",
            attempt, max_attempts, request_id,
        )

        data = viotp_get("/session/getv2", {"requestId": request_id})

        if not data.get("success"):
            logging.warning("Code poll error | message=%s", data.get("message"))
            await status_msg.edit_text(f"Lỗi khi kiểm tra OTP: {data.get('message')}")
            return

        d = data["data"]
        status = d.get("Status")

        logging.info(
            "OTP poll status | request_id=%s | status=%s | attempt=%s",
            request_id, status, attempt,
        )

        if status == 1:
            await status_msg.edit_text(
                f"OTP: `{d.get('Code')}`\n\n"
                f"SĐT: {d.get('Phone')}\n"
                f"Dịch vụ: {d.get('ServiceName')}\n\n"
                f"Nội dung:\n{d.get('SmsContent')}",
                parse_mode="Markdown",
            )

            logging.info(
                "OTP received | request_id=%s | code=%s | attempt=%s",
                request_id, d.get("Code"), attempt,
            )
            return

        elif status == 2:
            await status_msg.edit_text("Phiên đã hết hạn.")

            logging.info("OTP expired | request_id=%s", request_id)
            return

        elif status == 0:
            remaining = (max_attempts - attempt) * POLL_INTERVAL
            await status_msg.edit_text(
                f"Đang chờ OTP cho request `{request_id}`...\n"
                f"Lần kiểm tra: {attempt}/{max_attempts} — còn ~{remaining}s",
                parse_mode="Markdown",
            )

        else:
            await status_msg.edit_text(f"Trạng thái không xác định: {d}")

            logging.warning(
                "Unknown OTP status | request_id=%s | data=%s",
                request_id, d,
            )
            return

    await status_msg.edit_text(
        f"Hết thời gian chờ OTP ({MAX_WAIT // 60} phút) cho request `{request_id}`.",
        parse_mode="Markdown",
    )

    logging.warning("OTP polling timeout | request_id=%s", request_id)


# =========================
# BOT SETUP
# =========================

async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start", "Hiển thị menu bot"),
        BotCommand("app", "Mở Mini App"),
        BotCommand("id", "Xem Telegram ID của bạn"),
        BotCommand("balance", "Kiểm tra số dư VIOTP"),
        BotCommand("services", "Xem danh sách dịch vụ"),
        BotCommand("buy", "Thuê số: /buy <service_id>"),
        BotCommand("code", "Lấy OTP: /code <request_id>"),
    ])

    logging.info("Bot commands have been set")


# =========================
# BOT RUNNER
# =========================

def run_bot():
    logging.info("BOT_TOKEN: %s", mask_token(BOT_TOKEN))
    logging.info("VIOTP_TOKEN: %s", mask_token(VIOTP_TOKEN))
    logging.info("ADMIN_IDS: %s", ADMIN_IDS)

    if not BOT_TOKEN:
        raise RuntimeError("Thiếu BOT_TOKEN")

    if not VIOTP_TOKEN:
        raise RuntimeError("Thiếu VIOTP_TOKEN")

    if not ADMIN_IDS:
        logging.warning("ADMIN_IDS đang trống. Không ai ngoài lệnh /id dùng được bot.")

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("app", open_app))
    app.add_handler(CommandHandler("id", my_id))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("services", services))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("code", code))

    logging.info("Bot started with polling...")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    run_bot()