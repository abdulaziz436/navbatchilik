import json
import os
import datetime
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ====== SOZLAMALAR ======
TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_PASSWORD = "997632882"
TZ = ZoneInfo("Asia/Tashkent")
DATA_FILE = os.path.join(os.path.dirname(__file__), "duty_data.json")
NOTIFY_HOUR = 9    # xabar har kuni shu soatda yuboriladi (24 soatlik format)
NOTIFY_MINUTE = 0
# =========================

awaiting_password = set()   # admin parol kutilayotgan chat_id lar
admin_mode = set()          # admin rejimidagi chat_id lar


def today_str():
    return datetime.datetime.now(TZ).strftime("%Y-%m-%d")


def month_name(m):
    names = ["yanvar", "fevral", "mart", "aprel", "may", "iyun",
             "iyul", "avgust", "sentyabr", "oktyabr", "noyabr", "dekabr"]
    return names[m - 1]


def fmt_date(s):
    y, m, d = s.split("-")
    return f"{int(d)}-{month_name(int(m))} {y}"


def load_data():
    if not os.path.exists(DATA_FILE):
        return {"registered": {}, "users": [], "state": None, "history": []}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def sorted_users(users):
    return sorted(users, key=lambda u: u["order"])


def next_user_id(users, current_id):
    lst = sorted_users(users)
    if not lst:
        return None
    ids = [u["id"] for u in lst]
    if current_id not in ids:
        return lst[0]["id"]
    idx = ids.index(current_id)
    return lst[(idx + 1) % len(lst)]["id"]


# ---------- Foydalanuvchi komandalar ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    uid = str(update.effective_user.id)
    name = update.effective_user.full_name
    data["registered"][uid] = {"name": name}
    save_data(data)
    await update.message.reply_text(
        f"Salom, {name}! Siz ro'yxatdan o'tdingiz.\n"
        f"Admin sizni navbatchilik ro'yxatiga qo'shgach, navbatingiz kelganda shu yerga xabar keladi.\n\n"
        f"/holat — joriy navbatni ko'rish\n"
        f"/tarix — navbatchilik tarixi"
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    users, state = data.get("users", []), data.get("state")
    if not users or not state:
        await update.message.reply_text("Hali navbatchilar ro'yxati sozlanmagan. Admin /admin orqali sozlaydi.")
        return
    current = next((u for u in users if u["id"] == state["current_user_id"]), None)
    status_txt = "✅ Bajarildi" if state["status"] == "done" else "⏳ Kutilmoqda"
    lines = [
        f"📅 {fmt_date(state['current_date'])}",
        f"👤 Navbat: {current['name'] if current else '—'}",
        f"Holat: {status_txt}",
        "", "Navbat tartibi:",
    ]
    for u in sorted_users(users):
        mark = "👉 " if u["id"] == state["current_user_id"] else "    "
        lines.append(f"{mark}{u['order']}. {u['name']}")
    await update.message.reply_text("\n".join(lines))


async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    hist = data.get("history", [])[-20:][::-1]
    if not hist:
        await update.message.reply_text("Hozircha tarix yo'q.")
        return
    lines = []
    for h in hist:
        mark = "✅" if h["status"] == "done" else "❌"
        lines.append(f"{mark} {fmt_date(h['date'])} — {h['user_name']}")
    await update.message.reply_text("\n".join(lines))


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    awaiting_password.add(update.effective_chat.id)
    await update.message.reply_text("Admin parolini kiriting:")


async def send_admin_menu(chat_id, context):
    kb = [
        [InlineKeyboardButton("➕ Ishtirokchi qo'shish", callback_data="admin_add")],
        [InlineKeyboardButton("📋 Ro'yxat", callback_data="admin_list")],
        [InlineKeyboardButton("🔄 Navbatni qo'lda belgilash", callback_data="admin_force")],
        [InlineKeyboardButton("↩️ Bugungi holatni qaytadan 'kutilmoqda' qilish", callback_data="admin_reset")],
        [InlineKeyboardButton("🚪 Chiqish", callback_data="admin_exit")],
    ]
    await context.bot.send_message(chat_id, "🔐 Admin panel:", reply_markup=InlineKeyboardMarkup(kb))


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    if chat_id in awaiting_password:
        awaiting_password.discard(chat_id)
        if text == ADMIN_PASSWORD:
            admin_mode.add(chat_id)
            await send_admin_menu(chat_id, context)
        else:
            await update.message.reply_text("❌ Parol noto'g'ri.")
        return


# ---------- Inline tugmalar (callback) ----------

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data_str = query.data
    chat_id = query.message.chat_id
    await query.answer()

    data = load_data()

    if data_str == "confirm_done":
        state = data.get("state")
        uid = str(query.from_user.id)
        if not state or state["current_user_id"] != uid:
            await query.answer("Bu sizning navbatingiz emas.", show_alert=True)
            return
        if state["status"] == "done":
            await query.answer("Allaqachon tasdiqlangan.", show_alert=True)
            return
        user = next((u for u in data["users"] if u["id"] == uid), None)
        state["status"] = "done"
        state["confirmed_at"] = datetime.datetime.now(TZ).isoformat()
        data["history"].append({
            "date": state["current_date"], "user_id": uid,
            "user_name": user["name"] if user else "Noma'lum", "status": "done",
        })
        save_data(data)
        await query.edit_message_text(
            f"✅ Rahmat, {user['name'] if user else ''}! Bugungi navbatchilik bajarildi deb belgilandi."
        )
        return

    if chat_id not in admin_mode:
        await query.answer("Admin rejimidan chiqib ketgansiz. /admin ni qayta yuboring.", show_alert=True)
        return

    users = data.get("users", [])

    if data_str == "admin_add":
        in_duty_ids = {u["id"] for u in users}
        candidates = [(uid, info["name"]) for uid, info in data["registered"].items() if uid not in in_duty_ids]
        if not candidates:
            await query.edit_message_text(
                "Qo'shish uchun hech kim yo'q. Ishtirokchi avval botga /start yuborishi kerak."
            )
            return
        kb = [[InlineKeyboardButton(name, callback_data=f"add_{uid}")] for uid, name in candidates]
        kb.append([InlineKeyboardButton("↩️ Orqaga", callback_data="admin_menu")])
        await query.edit_message_text("Kimni navbatga qo'shamiz?", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data_str.startswith("add_"):
        new_id = data_str[4:]
        name = data["registered"].get(new_id, {}).get("name", "Noma'lum")
        max_order = max([u["order"] for u in users], default=0)
        users.append({"id": new_id, "name": name, "order": max_order + 1})
        data["users"] = users
        if not data.get("state") or not data["state"].get("current_user_id"):
            data["state"] = {"current_user_id": new_id, "current_date": today_str(),
                              "status": "pending", "notified_date": None}
        save_data(data)
        await query.edit_message_text(f"✅ {name} navbatga qo'shildi.")
        return

    if data_str == "admin_list":
        if not users:
            await query.edit_message_text("Hali hech kim qo'shilmagan.")
            return
        lines = [f"{u['order']}. {u['name']}" for u in sorted_users(users)]
        await query.edit_message_text("Navbat ro'yxati:\n" + "\n".join(lines))
        return

    if data_str == "admin_force":
        if not users:
            await query.edit_message_text("Avval ishtirokchi qo'shing.")
            return
        kb = [[InlineKeyboardButton(u["name"], callback_data=f"force_{u['id']}")] for u in sorted_users(users)]
        await query.edit_message_text("Joriy navbatni kimga belgilaymiz?", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data_str.startswith("force_"):
        uid = data_str[6:]
        data["state"] = {"current_user_id": uid, "current_date": today_str(),
                          "status": "pending", "notified_date": None}
        save_data(data)
        name = next((u["name"] for u in users if u["id"] == uid), "")
        await query.edit_message_text(f"✅ Joriy navbat {name}ga belgilandi.")
        return

    if data_str == "admin_reset":
        if data.get("state"):
            data["state"]["current_date"] = today_str()
            data["state"]["status"] = "pending"
            data["state"]["notified_date"] = None
            save_data(data)
        await query.edit_message_text("✅ Bugungi holat 'kutilmoqda'ga qaytarildi.")
        return

    if data_str == "admin_exit":
        admin_mode.discard(chat_id)
        await query.edit_message_text("Admin rejimidan chiqdingiz.")
        return

    if data_str == "admin_menu":
        await query.message.delete()
        await send_admin_menu(chat_id, context)
        return


# ---------- Kunlik ish: rollover + bildirishnoma ----------

async def daily_job(app):
    data = load_data()
    users, state = data.get("users", []), data.get("state")
    if not users or not state:
        return
    today = today_str()

    if state["current_date"] != today:
        if state["status"] == "done":
            nid = next_user_id(users, state["current_user_id"])
            state = {"current_user_id": nid, "current_date": today,
                     "status": "pending", "notified_date": None}
        else:
            missed_user = next((u for u in users if u["id"] == state["current_user_id"]), None)
            if state.get("current_user_id"):
                data["history"].append({
                    "date": state["current_date"], "user_id": state["current_user_id"],
                    "user_name": missed_user["name"] if missed_user else "Noma'lum",
                    "status": "missed",
                })
            state["current_date"] = today
            state["notified_date"] = None
        data["state"] = state
        save_data(data)

    if state.get("notified_date") == today:
        return

    current = next((u for u in users if u["id"] == state["current_user_id"]), None)
    if not current:
        return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Bajardim", callback_data="confirm_done")]])
    try:
        await app.bot.send_message(
            int(current["id"]),
            f"📋 Bugun ({fmt_date(today)}) 111-xonada navbatchilik sizga tegishli.\n"
            f"Bajarganingizdan so'ng pastdagi tugmani bosing.",
            reply_markup=kb,
        )
        state["notified_date"] = today
        data["state"] = state
        save_data(data)
    except Exception as e:
        print("Xabar yuborishda xatolik:", e)


def run_keepalive():
    port = int(os.environ.get("PORT", 8080))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, *args):
            pass

    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN muhit o'zgaruvchisi topilmadi. Uni sozlang.")

    threading.Thread(target=run_keepalive, daemon=True).start()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("holat", status_cmd))
    app.add_handler(CommandHandler("tarix", history_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(daily_job, "cron", hour=NOTIFY_HOUR, minute=NOTIFY_MINUTE, args=[app])
    scheduler.start()

    print("Bot ishga tushdi.")
    app.run_polling()


if __name__ == "__main__":
    main()
