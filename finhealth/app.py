import io, os, re, json, time, hmac, sqlite3
from datetime import date
from xml.sax.saxutils import escape
from flask import Flask, render_template, request, jsonify, send_file, session
from werkzeug.security import generate_password_hash, check_password_hash
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30)
DB = os.environ.get("DB_PATH", "fiscalfit.db")
ADMIN_EMAIL = (os.environ.get("ADMIN_EMAIL") or "admin@gmail.com").lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD") or "Admin@123"
FAILS = {}


def q(sql, a=(), one=False, many=False):
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT UNIQUE, pw TEXT, data TEXT DEFAULT '{}', hist TEXT DEFAULT '[]')")
    c.execute("CREATE TABLE IF NOT EXISTS tx(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, kind TEXT, cat TEXT, amt REAL, note TEXT, d TEXT)")
    try:
        c.execute("ALTER TABLE users ADD COLUMN pic TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        cur = c.execute(sql, a)
        r = cur.fetchone() if one else cur.fetchall() if many else None
        c.commit()
        return r
    finally:
        c.close()


def me():
    uid = session.get("uid")
    return q("SELECT * FROM users WHERE id=?", (uid,), True) if uid else None


def pub(u):
    return dict(name=u["name"], email=u["email"], data=json.loads(u["data"]), hist=json.loads(u["hist"]), pic=u["pic"] or "")


def err(msg, code=400):
    return jsonify(error=msg), code
EXP = ["food", "transport", "utilities", "health", "education", "entertainment", "shopping", "other"]
LEVELS = [(0, "Unsafe", "#ef4444", "Your finances need urgent attention. Start with the red tips below."),
          (40, "Medium", "#f59e0b", "You are coping, but one shock could hurt. Close the gaps below."),
          (60, "Safe", "#22c55e", "You are on solid ground. A few upgrades will make you bulletproof."),
          (80, "Very safe", "#00b894", "Excellent! Keep this discipline going and keep growing.")]
CAPS = {"food": .15, "transport": .10, "utilities": .08, "entertainment": .07, "shopping": .08}


def num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def clip(x):
    return max(0.0, min(1.0, x))


def inr(x):
    return f"{x:,.0f}"


def analyze(d):
    g = lambda k: max(0.0, num(d.get(k)))
    age = g("age") or 30
    sal, passive = g("salary"), g("passive")
    income = max(sal + passive, 1)
    hs = d.get("housing", "rent")
    exp = {k: g(k) for k in EXP}
    rent = g("rent") if hs == "rent" else 0
    emi = g("emi") if hs == "loan" else 0
    demi = g("debt_emi")
    spend = sum(exp.values()) + rent
    outgo = spend + emi + demi
    save = income - outgo
    sr = save / income
    emerg = g("emergency")
    em_m = emerg / outgo if outgo else 0
    dti = (emi + demi) / income
    sip, fd, stocks, land, gold, oth = (g(k) for k in ("sip", "fd", "stocks", "land", "gold", "other_assets"))
    home = g("home_price") if hs != "rent" else 0
    loan = g("loan_out") if hs == "loan" else 0
    liab = loan + g("other_debt")
    nw = emerg + fd + stocks + land + gold + oth + home - liab
    target = age * income * 12 / 10
    term, hlth, par = bool(d.get("term")), bool(d.get("health_ins")), bool(d.get("parents"))
    cover = g("cover")
    div = sum(x > 0 for x in (fd, stocks, sip, gold, land))
    ins = 30 * term + 30 * hlth + 20 * par + (20 * clip(cover / (sal * 120)) if sal else 0)
    subs = [("Savings rate", 100 * clip(sr / .2), f"You save {sr*100:.0f}% of income (goal 20%+)"),
            ("Emergency fund", 100 * clip(em_m / 6), f"Covers {em_m:.1f} of 6 recommended months"),
            ("Debt load", 100 if dti <= .2 else 100 * clip(1 - (dti - .2) / .3), f"EMIs take {dti*100:.0f}% of income (keep under 35%)"),
            ("Insurance", ins, "Term plan, health cover, parents' cover"),
            ("Investing", 60 * clip(sip / income / .15) + 40 * min(1, div / 3), f"SIP is {sip/income*100:.0f}% of income across {div} asset types"),
            ("Net worth", 100 * clip(nw / target) if nw > 0 and target else 0, f"Rs. {inr(nw)} vs age benchmark Rs. {inr(target)}")]
    score = round(sum(s[1] * w for s, w in zip(subs, [.25, .20, .20, .15, .10, .10])))
    lvl = max(i for i, l in enumerate(LEVELS) if score >= l[0])

    yrs, r = int(g("sip_years")), g("sip_ret") / 1200
    fv = lambda m: sip * m if r == 0 else sip * (((1 + r) ** m - 1) / r) * (1 + r)
    series = [round(fv(12 * y)) for y in range(yrs + 1)]
    sipd = dict(years=yrs, fv=series[-1], invested=round(sip * 12 * yrs), series=series, inv=[round(sip * 12 * y) for y in range(yrs + 1)])

    loan_note, tot_int = "", 0
    if hs == "loan":
        tot_int = max(0, emi * 12 * g("loan_years") - loan)
        ratio = emi / income
        verdict = "comfortable" if ratio <= .3 else "stretched" if ratio <= .4 else "too high for your income"
        loan_note = f"Home loan EMI is {ratio*100:.0f}% of income, which is {verdict}. You will still pay about Rs. {inr(tot_int)} as interest."

    tips = []
    tip = lambda p, i, t, x: tips.append(dict(p=p, icon=i, title=t, text=x))
    if not term:
        tip(0, "🛡️", "Get a term life plan", f"No term cover means your family carries the risk. Aim for about Rs. {inr(sal*120)} (10x yearly salary). Term plans are cheap when you are young.")
    if not hlth:
        tip(0, "🏥", "Buy health insurance now", "One hospital stay can wipe out years of savings. Take a Rs. 10 lakh+ family floater and protect your emergency fund.")
    if not par:
        tip(1, "👪", "Cover your parents", "Buy a separate senior-citizen health policy for parents so their bills never touch your savings.")
    if em_m < 6:
        tip(0 if em_m < 3 else 1, "💧", "Grow your emergency fund", f"Target Rs. {inr(outgo*6)} (6 months of outgo). Auto-transfer Rs. {inr(max(500, income*.1))} a month into a liquid fund or sweep-in FD until you reach it.")
    if sr < 0:
        tip(0, "🚨", "You spend more than you earn", f"You are short by Rs. {inr(-save)} a month. Cut wants first, and pause new EMIs and purchases.")
    elif sr < .2:
        tip(1, "🎯", "Raise your savings rate", f"Try the 50/30/20 rule: 50% needs, 30% wants, 20% savings. Reaching 20% means saving Rs. {inr(income*.2)} a month. Pay yourself first on salary day.")
    if dti > .35:
        tip(0, "⛓️", "Reduce your debt load", "Use the avalanche method: pay the highest-interest loan first. Put bonuses and one extra EMI a year into principal, take no new loans, and ask lenders for a lower rate.")
    if g("other_debt") > 0:
        tip(0 if demi > 0 else 1, "💳", "Clear personal and card debt first", "Credit card and personal loans cost 15-40% a year, far more than any investment earns. Clear these before investing extra.")
    if hs == "loan":
        tip(1, "🏠", "Finish your home loan faster", f"Add a small EMI step-up each year or prepay once a year. Early prepayment saves the most interest, and your remaining interest is about Rs. {inr(tot_int)}." + (" Your rate is high, so compare a balance transfer." if g("loan_rate") > 9 else ""))
    if hs == "rent" and rent / income > .3:
        tip(1, "🔑", "Rent is heavy", f"Rent is {rent/income*100:.0f}% of income. Aim for under 30% by sharing, moving closer to work, or negotiating.")
    over = sorted(((exp[k] / income - c, k) for k, c in CAPS.items() if exp[k] / income > c), reverse=True)
    for gap, k in over[:2]:
        tip(1, "✂️", f"Trim {k} spending", f"{k.title()} is {exp[k]/income*100:.0f}% of income (healthy is about {CAPS[k]*100:.0f}%). Track it for a month, set a weekly cap and cut 15% to save Rs. {inr(exp[k]*.15)} a month.")
    if sip == 0:
        tip(1, "📈", "Start a SIP", f"Begin with Rs. {inr(income*.1)} a month in a diversified index fund. Time in the market matters more than amount.")
    elif sip / income < .15:
        tip(2, "🪜", "Step up your SIP", "Increase your SIP by 10% each year or whenever your salary rises. It compounds quietly.")
    if g("sip_ret") > 14:
        tip(2, "⚠️", "Use realistic returns", "Plan with 10-12% yearly for equity funds. Higher assumptions lead to nasty surprises.")
    if (land + gold) > .6 * max(1, nw + liab - home) and land + gold > 0:
        tip(2, "🧱", "Too much in land and gold", "Land and gold are hard to sell quickly. Keep growth money in liquid, diversified assets.")
    if passive == 0:
        tip(2, "🌱", "Build passive income", "Dividend funds, rent, interest, or a small online business can add a second income and speed up your goals.")
    if nw < target:
        tip(2, "🏔️", "Close the net worth gap", f"A common benchmark for your age is Rs. {inr(target)}. Consistent saving plus SIPs closes this over time.")
    if not any(t["p"] < 2 for t in tips):
        tip(2, "🏆", "Strong base, keep going", "Review insurance, investments and spending once a year, and raise your cover as income grows.")
    tips.sort(key=lambda t: t["p"])

    corpus, bal, contrib, fy = outgo * 12 * 25, fd + stocks, max(save, sip), None
    for y in range(1, 61):
        bal = (bal + 12 * contrib) * 1.10
        if bal >= corpus:
            fy = y
            break
    fire = dict(corpus=round(corpus), years=fy, age=int(age + fy) if fy else None)
    labels = {k: v for k, v in exp.items()}
    if rent: labels["rent"] = rent
    if emi: labels["home loan EMI"] = emi
    if demi: labels["other EMIs"] = demi
    return dict(score=score, level=lvl, label=LEVELS[lvl][1], color=LEVELS[lvl][2], blurb=LEVELS[lvl][3],
                subs=[dict(name=a, score=round(b), note=c) for a, b, c in subs],
                m=dict(income=income, outgo=outgo, save=save, sr=sr, em=em_m, dti=dti, nw=nw, target=target, liab=liab, spend=spend, debt=emi + demi),
                expenses={k: v for k, v in labels.items() if v > 0}, sip=sipd, loan_note=loan_note, tips=tips, fire=fire)


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/signup")
def signup():
    d = request.get_json(force=True, silent=True) or {}
    name, email, pw = (d.get("name") or "").strip()[:40], (d.get("email") or "").strip().lower()[:120], d.get("password") or ""
    if not name or "@" not in email or "." not in email or email == ADMIN_EMAIL:
        return err("Enter your name and a valid email.")
    if len(pw) < 8:
        return err("Password must be at least 8 characters.")
    try:
        q("INSERT INTO users(name,email,pw) VALUES(?,?,?)", (name, email, generate_password_hash(pw)))
    except sqlite3.IntegrityError:
        return err("An account with this email already exists.", 409)
    u = q("SELECT * FROM users WHERE email=?", (email,), True)
    session.clear()
    session.permanent, session["uid"] = True, u["id"]
    return jsonify(pub(u))


@app.post("/api/login")
def login():
    d = request.get_json(force=True, silent=True) or {}
    email = (d.get("email") or "").strip().lower()
    if email == ADMIN_EMAIL:
        ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()
        now = time.time()
        FAILS[ip] = [t for t in FAILS.get(ip, []) if now - t < 600]
        if len(FAILS[ip]) >= 5:
            return err("Too many attempts. Try again in 10 minutes.", 429)
        if hmac.compare_digest((d.get("password") or "").encode(), ADMIN_PASSWORD.encode()):
            session.clear()
            session["admin"] = True
            return jsonify(admin=True, name="Admin", email=ADMIN_EMAIL)
        FAILS[ip].append(now)
        return err("Wrong email or password.", 401)
    u = q("SELECT * FROM users WHERE email=?", (email,), True)
    if not u or not check_password_hash(u["pw"], d.get("password") or ""):
        return err("Wrong email or password.", 401)
    session.clear()
    session.permanent, session["uid"] = True, u["id"]
    return jsonify(pub(u))


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def api_me():
    if session.get("admin"):
        return jsonify(admin=True, name="Admin", email=ADMIN_EMAIL)
    u = me()
    return jsonify(pub(u)) if u else err("Not logged in.", 401)


@app.post("/api/save")
def save():
    u = me()
    if not u:
        return err("Please log in.", 401)
    d = request.get_json(force=True, silent=True) or {}
    res, hist, today = analyze(d), json.loads(u["hist"]), date.today().isoformat()
    hist = [h for h in hist if h["d"] != today] + [dict(d=today, s=res["score"])]
    q("UPDATE users SET data=?, hist=? WHERE id=?", (json.dumps(d), json.dumps(hist[-30:]), u["id"]))
    return jsonify(result=res, hist=hist[-30:])


@app.post("/api/delete")
def delete():
    u = me()
    if not u:
        return err("Please log in.", 401)
    if not check_password_hash(u["pw"], (request.get_json(force=True, silent=True) or {}).get("password") or ""):
        return err("Password is incorrect.", 401)
    q("DELETE FROM tx WHERE uid=?", (u["id"],))
    q("DELETE FROM users WHERE id=?", (u["id"],))
    session.clear()
    return jsonify(ok=True)


@app.post("/api/profile")
def profile():
    u = me()
    if not u:
        return err("Please log in.", 401)
    d = request.get_json(force=True, silent=True) or {}
    name, email = (d.get("name") or "").strip()[:40], (d.get("email") or "").strip().lower()[:120]
    if not name or "@" not in email or "." not in email or email == ADMIN_EMAIL:
        return err("Enter your name and a valid email.")
    try:
        q("UPDATE users SET name=?, email=? WHERE id=?", (name, email, u["id"]))
    except sqlite3.IntegrityError:
        return err("That email is already used by another account.", 409)
    return jsonify(pub(me()))


@app.post("/api/password")
def password():
    u = me()
    if not u:
        return err("Please log in.", 401)
    d = request.get_json(force=True, silent=True) or {}
    if not check_password_hash(u["pw"], d.get("old") or ""):
        return err("Current password is incorrect.", 401)
    if len(d.get("new") or "") < 8:
        return err("New password must be at least 8 characters.")
    q("UPDATE users SET pw=? WHERE id=?", (generate_password_hash(d["new"]), u["id"]))
    return jsonify(ok=True)


@app.post("/api/avatar")
def avatar():
    u = me()
    if not u:
        return err("Please log in.", 401)
    pic = (request.get_json(force=True, silent=True) or {}).get("pic") or ""
    if pic and (len(pic) > 200000 or not re.fullmatch(r"data:image/jpeg;base64,[A-Za-z0-9+/=]+", pic)):
        return err("Invalid image.")
    q("UPDATE users SET pic=? WHERE id=?", (pic, u["id"]))
    return jsonify(ok=True)


def tx_rows(u):
    return [dict(id=r["id"], kind=r["kind"], cat=r["cat"], amt=r["amt"], note=r["note"], d=r["d"])
            for r in q("SELECT * FROM tx WHERE uid=? ORDER BY d DESC, id DESC", (u["id"],), many=True)]


def tx_clean(d):
    amt = num(d.get("amt"))
    day = (d.get("d") or date.today().isoformat())[:10]
    try:
        date.fromisoformat(day)
    except ValueError:
        return None
    if amt <= 0 or amt > 1e10:
        return None
    return ("income" if d.get("kind") == "income" else "expense", (d.get("cat") or "Other")[:30], amt, (d.get("note") or "")[:80], day)


@app.get("/api/tx")
def tx_list():
    u = me()
    return jsonify(tx_rows(u)) if u else err("Please log in.", 401)


@app.post("/api/tx")
def tx_add():
    u = me()
    if not u:
        return err("Please log in.", 401)
    t = tx_clean(request.get_json(force=True, silent=True) or {})
    if not t:
        return err("Enter a valid amount and date.")
    q("INSERT INTO tx(uid,kind,cat,amt,note,d) VALUES(?,?,?,?,?,?)", (u["id"], *t))
    return jsonify(tx_rows(u))


@app.put("/api/tx/<int:i>")
def tx_edit(i):
    u = me()
    if not u:
        return err("Please log in.", 401)
    t = tx_clean(request.get_json(force=True, silent=True) or {})
    if not t:
        return err("Enter a valid amount and date.")
    q("UPDATE tx SET kind=?, cat=?, amt=?, note=?, d=? WHERE id=? AND uid=?", (*t, i, u["id"]))
    return jsonify(tx_rows(u))


@app.delete("/api/tx/<int:i>")
def tx_del(i):
    u = me()
    if not u:
        return err("Please log in.", 401)
    q("DELETE FROM tx WHERE id=? AND uid=?", (i, u["id"]))
    return jsonify(tx_rows(u))


@app.get("/api/tx/pdf")
def tx_pdf():
    u = me()
    if not u:
        return err("Please log in.", 401)
    rows = tx_rows(u)[:1000]
    inc = sum(r["amt"] for r in rows if r["kind"] == "income")
    exp = sum(r["amt"] for r in rows if r["kind"] != "income")
    ss, buf = getSampleStyleSheet(), io.BytesIO()
    data = [["Date", "Type", "Category", "Note", "Amount (Rs.)"]] + [[r["d"], r["kind"].title(), r["cat"], (r["note"] or "")[:34], ("+" if r["kind"] == "income" else "-") + inr(r["amt"])] for r in rows]
    t = Table(data, repeatRows=1, colWidths=[62, 52, 80, 190, 80])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#9ccc3c")), ("GRID", (0, 0), (-1, -1), .3, colors.HexColor("#cbd5d3")),
                           ("FONTSIZE", (0, 0), (-1, -1), 8), ("ALIGN", (4, 0), (4, -1), "RIGHT"), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f6f5")])]))
    el = [Paragraph(f"Transactions for {escape(u['name'])}", ss["Title"]),
          Paragraph(f"Generated {date.today():%d %b %Y} | Income Rs. {inr(inc)} | Expenses Rs. {inr(exp)} | Balance Rs. {inr(inc - exp)}", ss["Normal"]), Spacer(1, 12), t]
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36).build(el)
    buf.seek(0)
    return send_file(buf, mimetype="application/pdf", as_attachment=True, download_name="transactions.pdf")


def is_admin():
    return session.get("admin") is True


@app.get("/api/admin/users")
def adm_users():
    if not is_admin():
        return err("Forbidden.", 403)
    st = {r["uid"]: r for r in q("SELECT uid, COUNT(*) n, SUM(CASE WHEN kind='income' THEN amt ELSE 0 END) i, SUM(CASE WHEN kind!='income' THEN amt ELSE 0 END) e FROM tx GROUP BY uid", many=True)}
    out = []
    for u in q("SELECT id, name, email, hist FROM users ORDER BY id DESC", many=True):
        s, h = st.get(u["id"]), json.loads(u["hist"])
        out.append(dict(id=u["id"], name=u["name"], email=u["email"], n=s["n"] if s else 0, i=s["i"] if s else 0, e=s["e"] if s else 0, score=h[-1]["s"] if h else None))
    return jsonify(out)


@app.get("/api/admin/user/<int:i>")
def adm_user(i):
    if not is_admin():
        return err("Forbidden.", 403)
    u = q("SELECT * FROM users WHERE id=?", (i,), True)
    if not u:
        return err("User not found.", 404)
    return jsonify(dict(pub(u), id=u["id"], tx=tx_rows(u)))


@app.post("/api/admin/user/<int:i>/password")
def adm_pw(i):
    if not is_admin():
        return err("Forbidden.", 403)
    new = (request.get_json(force=True, silent=True) or {}).get("new") or ""
    if len(new) < 8:
        return err("New password must be at least 8 characters.")
    if not q("SELECT id FROM users WHERE id=?", (i,), True):
        return err("User not found.", 404)
    q("UPDATE users SET pw=? WHERE id=?", (generate_password_hash(new), i))
    return jsonify(ok=True)


@app.delete("/api/admin/user/<int:i>")
def adm_del(i):
    if not is_admin():
        return err("Forbidden.", 403)
    q("DELETE FROM tx WHERE uid=?", (i,))
    q("DELETE FROM users WHERE id=?", (i,))
    return jsonify(ok=True)


@app.post("/api/analyze")
def api_analyze():
    return jsonify(analyze(request.get_json(force=True, silent=True) or {}))


@app.post("/api/report")
def api_report():
    d = request.get_json(force=True, silent=True) or {}
    r, name = analyze(d), escape((d.get("name") or "Friend")[:40])
    ss, col = getSampleStyleSheet(), colors.HexColor(analyze(d)["color"])
    big = ParagraphStyle("b", parent=ss["Title"], fontSize=38, textColor=col)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=colors.HexColor("#0f1f2e"))
    m = r["m"]
    buf = io.BytesIO()
    el = [Paragraph(f"Financial Health Report for {name}", ss["Title"]), Paragraph(date.today().strftime("%d %b %Y"), ss["Normal"]), Spacer(1, 10),
          Paragraph(f"{r['score']}/100 - {r['label']}", big), Paragraph(r["blurb"], ss["Normal"]), Spacer(1, 12), Paragraph("Key numbers", h2)]

    def table(rows, widths):
        t = Table(rows, colWidths=widths)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#cbd5d3")), ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#f1f6f5")]), ("FONTSIZE", (0, 0), (-1, -1), 9), ("PADDING", (0, 0), (-1, -1), 6)]))
        return t
    el.append(table([["Monthly income", f"Rs. {inr(m['income'])}"], ["Monthly outgo", f"Rs. {inr(m['outgo'])}"], ["Monthly savings", f"Rs. {inr(m['save'])} ({m['sr']*100:.0f}%)"],
                     ["Emergency fund cover", f"{m['em']:.1f} months"], ["Debt-to-income", f"{m['dti']*100:.0f}%"], ["Net worth", f"Rs. {inr(m['nw'])}"]], [200, 300]))
    if r["sip"]["years"]:
        el += [Spacer(1, 6), Paragraph(f"SIP projection: Rs. {inr(r['sip']['invested'])} invested grows to about Rs. {inr(r['sip']['fv'])} in {r['sip']['years']} years.", ss["Normal"])]
    if r["loan_note"]:
        el += [Spacer(1, 6), Paragraph(escape(r["loan_note"]), ss["Normal"])]
    el += [Spacer(1, 10), Paragraph("Score breakdown", h2), table([[s["name"], f"{s['score']}/100", s["note"]] for s in r["subs"]], [110, 60, 330]), Spacer(1, 10), Paragraph("Your improvement plan", h2)]
    for i, t in enumerate(r["tips"], 1):
        pr = ["URGENT", "IMPORTANT", "NICE TO HAVE"][t["p"]]
        el += [Paragraph(f"<b>{i}. {escape(t['title'])}</b> ({pr})", ss["Normal"]), Paragraph(escape(t["text"]), ss["BodyText"]), Spacer(1, 4)]
    el.append(Paragraph("<i>This report is an educational estimate, not licensed financial advice.</i>", ss["Italic"]))
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40).build(el)
    buf.seek(0)
    return send_file(buf, mimetype="application/pdf", as_attachment=True, download_name="financial-health-report.pdf")


if __name__ == "__main__":
    app.run(debug=True)
