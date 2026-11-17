import os
import random
import sqlite3
import threading
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from words import LEVELS

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ["BOT_TOKEN"]

# ---------- So'zlar ----------
WORDS = []
BY_LEVEL = {lvl: [] for lvl in LEVELS}
for lvl, items in LEVELS.items():
    for en, uz, ex in items:
        w = dict(id=len(WORDS), level=lvl, en=en, uz=uz, ex=ex)
        WORDS.append(w)
        BY_LEVEL[lvl].append(w)

ORDER = [5, 6, 7, 8]
PL_TOTAL = 20  # daraja testidagi savollar soni

# ---------- Baza (progress) ----------
db = sqlite3.connect("bot.db", check_same_thread=False)
db.execute(
    "CREATE TABLE IF NOT EXISTS progress("
    "uid INTEGER, wid INTEGER, c INTEGER DEFAULT 0, w INTEGER DEFAULT 0, "
    "PRIMARY KEY(uid, wid))"
)
db.commit()


def get_stat(uid, wid):
    r = db.execute("SELECT c, w FROM progress WHERE uid=? AND wid=?", (uid, wid)).fetchone()
    return r if r else (0, 0)


def record(uid, wid, ok):
    c, w = get_stat(uid, wid)
    c += 1 if ok else 0
    w += 0 if ok else 1
    db.execute("INSERT OR REPLACE INTO progress VALUES(?,?,?,?)", (uid, wid, c, w))
    db.commit()


def is_mastered(uid, wid):
    c, w = get_stat(uid, wid)
    return c - w >= 2


def mastered_count(uid, lvl):
    return sum(1 for x in BY_LEVEL[lvl] if is_mastered(uid, x["id"]))


def pick_word(uid, pool):
    weights = []
    for x in pool:
        c, w = get_stat(uid, x["id"])
        weights.append(0.2 if c - w >= 2 else 1 + w)
    return random.choices(pool, weights=weights, k=1)[0]


# ---------- Menyu ----------
def menu_markup(uid):
    rows = []
    for lvl in ORDER:
        rows.append([B(f"📘 Ielts {lvl}  ({mastered_count(uid, lvl)}/{len(BY_LEVEL[lvl])})", callback_data=f"s|{lvl}")])
    rows.append([B("🎯 Daraja testi", callback_data="s|P"), B("🔁 Xatolar", callback_data="s|E")])
    rows.append([B("📊 Statistika", callback_data="st")])
    rows.append([B(f"📖 {l}", callback_data=f"l|{l}") for l in ORDER])
    return M(rows)


MENU_TEXT = (
    "👋 <b>IELTS Vocabulary Quiz</b>\n\n"
    "Darajani tanla yoki <b>Daraja testi</b> orqali lug'at darajangni aniqla.\n"
    "Bir so'zni 2 marta to'g'ri topsang, u yodlangan hisoblanadi ✅\n"
    "📖 tugmalari – daraja bo'yicha so'zlar ro'yxati."
)


def back_markup():
    return M([[B("🏠 Menyu", callback_data="m")]])


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(MENU_TEXT, parse_mode="HTML", reply_markup=menu_markup(uid))


# ---------- Savol berish ----------
def make_question(w, header=""):
    others = [x for x in BY_LEVEL[w["level"]] if x["id"] != w["id"]]
    options = random.sample(others, 3) + [w]
    random.shuffle(options)
    return options


async def ask(q, ctx, uid, mode):
    header = ""
    if mode == "P":
        pl = ctx.user_data.get("pl")
        if not pl:
            pl = ctx.user_data["pl"] = {"q": 0, "s": {l: [0, 0] for l in ORDER}, "seen": set()}
        if pl["q"] >= PL_TOTAL:
            return await q.edit_message_text(result_text(pl), parse_mode="HTML", reply_markup=back_markup())
        lvl = ORDER[pl["q"] % 4]
        pool = [x for x in BY_LEVEL[lvl] if x["id"] not in pl["seen"]]
        w = random.choice(pool)
        pl["seen"].add(w["id"])
        header = f"🎯 Daraja testi {pl['q'] + 1}/{PL_TOTAL}\n\n"
    elif mode == "E":
        pool = []
        for x in WORDS:
            c, wr = get_stat(uid, x["id"])
            if wr > c:
                pool.append(x)
        if not pool:
            return await q.edit_message_text("🎉 Hozircha xato so'zlaring yo'q!", reply_markup=back_markup())
        w = random.choice(pool)
        header = "🔁 Xatolar ustida ishlash\n\n"
    else:
        lvl = int(mode)
        pool = BY_LEVEL[lvl]
        w = pick_word(uid, pool)
        done = mastered_count(uid, lvl)
        header = f"📘 Ielts {lvl}  •  yodlangan: {done}/{len(pool)}\n\n"
        if done == len(pool):
            header += "🏆 Bu darajani tugatding! Keyingisiga o'tishing mumkin.\n\n"

    options = make_question(w)
    d = random.choice(["e", "u"])  # e: en->uz, u: uz->en
    if d == "e":
        text = f"{header}❓ <b>{w['en']}</b>\n\nMa'nosi nima?"
        labels = [o["uz"] for o in options]
    else:
        text = f"{header}❓ <b>{w['uz']}</b>\n\nInglizcha so'zi qaysi?"
        labels = [o["en"] for o in options]
    kb = [[B(lab, callback_data=f"a|{mode}|{d}|{w['id']}|{o['id']}")] for lab, o in zip(labels, options)]
    kb.append([B("🏠 Menyu", callback_data="m")])
    await q.edit_message_text(text, parse_mode="HTML", reply_markup=M(kb))


# ---------- Javob ----------
async def answer(q, ctx, uid, parts):
    _, mode, d, c, ch = parts
    w = WORDS[int(c)]
    ok = int(c) == int(ch)
    record(uid, w["id"], ok)

    more = "n|" + mode
    btn_label = "Keyingi ➡️"
    if mode == "P":
        pl = ctx.user_data.get("pl")
        if pl:
            pl["s"][w["level"]][1] += 1
            if ok:
                pl["s"][w["level"]][0] += 1
            pl["q"] += 1
            if pl["q"] >= PL_TOTAL:
                btn_label = "🏁 Natijani ko'rish"

    mark = "✅ To'g'ri!" if ok else "❌ Noto'g'ri."
    text = (
        f"{mark}\n\n<b>{w['en']}</b> = {w['uz']}\n"
        f"<i>{w['ex']}</i>"
    )
    kb = M([[B(btn_label, callback_data=more)], [B("🏠 Menyu", callback_data="m")]])
    await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)


# ---------- Daraja testi natijasi ----------
def result_text(pl):
    lines = ["🏁 <b>Daraja testi natijasi</b>\n"]
    passed = None
    for lvl in ORDER:
        c, n = pl["s"][lvl]
        pct = round(100 * c / n) if n else 0
        lines.append(f"Ielts {lvl}: {c}/{n}  ({pct}%)")
        if n and c / n >= 0.6:
            passed = lvl
    if passed:
        est = f"Ielts {passed}"
        nxt = f"Maqsad: {passed + 1 if passed < 8 else 8}-daraja so'zlarini yodla."
    else:
        est = "Ielts 5 dan past"
        nxt = "Ielts 5 bo'limidan boshla."
    lines.append(f"\n📈 Lug'at bo'yicha taxminiy darajang: <b>{est}</b>")
    lines.append(nxt)
    lines.append("\n⚠️ Bu faqat lug'at bo'yicha taxmin. Haqiqiy Ielts bali Listening, Reading, Writing, Speaking'ga ham bog'liq.")
    return "\n".join(lines)


# ---------- Statistika ----------
def stats_text(uid):
    lines = ["📊 <b>Statistika</b>\n"]
    for lvl in ORDER:
        m = mastered_count(uid, lvl)
        t = len(BY_LEVEL[lvl])
        bar = "█" * round(10 * m / t) + "░" * (10 - round(10 * m / t))
        lines.append(f"Ielts {lvl}: {bar} {m}/{t}")
    rows = db.execute("SELECT SUM(c), SUM(w) FROM progress WHERE uid=?", (uid,)).fetchone()
    c, w = (rows[0] or 0), (rows[1] or 0)
    if c + w:
        lines.append(f"\nUmumiy aniqlik: {round(100 * c / (c + w))}%  ({c + w} ta javob)")
    t7 = mastered_count(uid, 7) + mastered_count(uid, 8)
    lines.append(f"\n🎯 Ielts 7 maqsadi: 7 va 8 darajadan {t7}/{len(BY_LEVEL[7]) + len(BY_LEVEL[8])} so'z yodlangan.")
    return "\n".join(lines)


def list_text(lvl):
    lines = [f"📖 <b>Ielts {lvl} so'zlari</b>\n"]
    for x in BY_LEVEL[lvl]:
        lines.append(f"• <b>{x['en']}</b> – {x['uz']}")
    return "\n".join(lines)


# ---------- Callback router ----------
async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    parts = q.data.split("|")
    act = parts[0]

    if act == "m":
        await q.edit_message_text(MENU_TEXT, parse_mode="HTML", reply_markup=menu_markup(uid))
    elif act == "s":  # start
        if parts[1] == "P":
            ctx.user_data.pop("pl", None)
        await ask(q, ctx, uid, parts[1])
    elif act == "n":  # next
        await ask(q, ctx, uid, parts[1])
    elif act == "a":
        await answer(q, ctx, uid, parts)
    elif act == "st":
        await q.edit_message_text(stats_text(uid), parse_mode="HTML", reply_markup=back_markup())
    elif act == "l":
        await q.edit_message_text(list_text(int(parts[1])), parse_mode="HTML", reply_markup=back_markup())


# ---------- Render uchun kichik web server ----------
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


def serve():
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 10000))), H).serve_forever()


def main():
    threading.Thread(target=serve, daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler(["start", "menu"], start))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.run_polling()


if __name__ == "__main__":
    main()
