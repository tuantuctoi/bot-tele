import os
import time
import threading
import logging
import requests

from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
VIOTP_TOKEN = os.getenv("VIOTP_TOKEN")
ADMIN_IDS_ENV = os.getenv("ADMIN_IDS", "")

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

# Flask app để Render thấy service có port đang chạy
web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "Bot is running"


def run_web():
    port = int(os.getenv("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_rate_limited(user_id: int) -> bool:
    now = time.time()
    last = last_command_time.get(user_id, 0)

    if now - last < RATE_LIMIT_SECONDS:
        return True

    last_command_time[user_id] = now
    return False


def viotp_get(path: str, params: dict | None = None) -> dict:
    params = params or {}
    params["token"] = VIOTP_TOKEN

    try:
        res = requests.get(
            f"{VIOTP_API}{path}",
            params=params,
            timeout=20,
        )
        logging.info("VIOTP URL: %s", res.url)
        logging.info("VIOTP RESPONSE: %s", res.text)
        return res.json()
    except Exception as e:
        logging.exception("VIOTP request error")
        return {
            "success": False,
            "message": str(e),
        }


async def guard(update: Update) -> bool:
    user = update.effective_user

    if not update.message or not user:
        return False

    if not is_admin(user.id):
        await update.message.reply_text("Không có quyền sử dụng bot.")
        return False

    if is_rate_limited(user.id):
        await update.message.reply_text(
            "Bạn thao tác quá nhanh, vui lòng thử lại sau vài giây."
        )
        return False

    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return

    await update.message.reply_text(
        "Bot VIOTP\n\n"
        "/balance - kiểm tra số dư\n"
        "/services - danh sách dịch vụ\n"
        "/buy <service_id> - thuê số\n"
        "/code <request_id> - lấy OTP\n"
        "/id - xem Telegram ID"
    )


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(f"Telegram ID của bạn là: {user.id}")


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return

    data = viotp_get("/users/balance")

    if data.get("success"):
        await update.message.reply_text(f"Số dư: {data['data']['balance']}")
    else:
        await update.message.reply_text(f"Lỗi: {data.get('message')}")


async def services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return

    data = viotp_get("/service/getv2", {"country": "vn"})

    if not data.get("success"):
        await update.message.reply_text(f"Lỗi: {data.get('message')}")
        return

    text = "Danh sách dịch vụ:\n\n"

    for s in data.get("data", [])[:50]:
        text += f"{s.get('id')} - {s.get('name')} - {s.get('price')}đ\n"

    for i in range(0, len(text), 3900):
        await update.message.reply_text(text[i:i + 3900])


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return

    if len(context.args) < 1:
        await update.message.reply_text("Dùng: /buy <service_id>\nVí dụ: /buy 1")
        return

    service_id = context.args[0]

    data = viotp_get("/request/getv2", {
        "serviceId": service_id,
    })

    if data.get("success"):
        d = data["data"]

        await update.message.reply_text(
            "Thuê số thành công\n\n"
            f"SĐT: {d.get('phone_number')}\n"
            f"Request ID: {d.get('request_id')}\n"
            f"Số dư còn lại: {d.get('balance')}\n\n"
            f"Lấy OTP:\n/code {d.get('request_id')}"
        )
    else:
        await update.message.reply_text(f"Lỗi: {data.get('message')}")


async def code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return

    if len(context.args) < 1:
        await update.message.reply_text(
            "Dùng: /code <request_id>\nVí dụ: /code 123456"
        )
        return

    request_id = context.args[0]

    data = viotp_get("/session/getv2", {
        "requestId": request_id,
    })

    if not data.get("success"):
        await update.message.reply_text(f"Lỗi: {data.get('message')}")
        return

    d = data["data"]
    status = d.get("Status")

    if status == 1:
        await update.message.reply_text(
            f"OTP: {d.get('Code')}\n\n"
            f"SĐT: {d.get('Phone')}\n"
            f"Dịch vụ: {d.get('ServiceName')}\n\n"
            f"Nội dung:\n{d.get('SmsContent')}"
        )
    elif status == 0:
        await update.message.reply_text(
            "Đang chờ OTP. Vui lòng thử lại sau 5-10 giây.\n\n"
            f"/code {request_id}"
        )
    elif status == 2:
        await update.message.reply_text("Phiên đã hết hạn.")
    else:
        await update.message.reply_text(f"Trạng thái không xác định: {d}")


def run_bot():
    if not BOT_TOKEN:
        raise RuntimeError("Thiếu BOT_TOKEN")

    if not VIOTP_TOKEN:
        raise RuntimeError("Thiếu VIOTP_TOKEN")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", my_id))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("services", services))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("code", code))

    logging.info("Bot started with polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    run_bot()