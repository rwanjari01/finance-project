import os
from flask import Flask, render_template, request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
from functools import wraps
from datetime import date

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key-for-production")
DB = os.environ.get("DB_PATH", "finance.db")

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS transactions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        type TEXT NOT NULL CHECK(type IN ('income','expense')),
        amount REAL NOT NULL,
        category TEXT NOT NULL,
        description TEXT DEFAULT '',
        date TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS budgets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        category TEXT NOT NULL,
        amount REAL NOT NULL,
        month TEXT NOT NULL,
        UNIQUE(user_id, category, month),
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS goals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        target_amount REAL NOT NULL,
        saved_amount REAL DEFAULT 0,
        deadline TEXT DEFAULT '',
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")
    conn.commit()
    conn.close()

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"success": False, "message": "Login required"}), 401
        return fn(*args, **kwargs)
    return wrapper

@app.route("/")
def index():
    return render_template("index.html")

@app.post("/api/signup")
def signup():
    data = request.get_json() or {}
    name = data.get("name","").strip()
    email = data.get("email","").strip().lower()
    password = data.get("password","")
    if not name or not email or not password:
        return jsonify(success=False, message="All fields are required"), 400
    if len(password) < 6:
        return jsonify(success=False, message="Password must be at least 6 characters"), 400
    conn = db()
    try:
        cur = conn.execute("INSERT INTO users(name,email,password) VALUES(?,?,?)",
                           (name,email,generate_password_hash(password)))
        conn.commit()
        uid = cur.lastrowid
        session["user_id"] = uid
        session["user_name"] = name
        return jsonify(success=True, message="Account created", user={"name":name,"email":email})
    except sqlite3.IntegrityError:
        return jsonify(success=False, message="Email already registered"), 409
    finally:
        conn.close()

@app.post("/api/login")
def login():
    data = request.get_json() or {}
    email = data.get("email","").strip().lower()
    password = data.get("password","")
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    conn.close()
    if not user or not check_password_hash(user["password"], password):
        return jsonify(success=False, message="Invalid email or password"), 401
    session["user_id"] = user["id"]
    session["user_name"] = user["name"]
    return jsonify(success=True, user={"name":user["name"],"email":user["email"]})

@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(success=True)

@app.get("/api/me")
def me():
    if "user_id" not in session:
        return jsonify(logged_in=False)
    conn = db()
    user = conn.execute("SELECT id,name,email FROM users WHERE id=?", (session["user_id"],)).fetchone()
    conn.close()
    return jsonify(logged_in=bool(user), user=dict(user) if user else None)

@app.get("/api/dashboard")
@login_required
def dashboard():
    uid = session["user_id"]
    conn = db()
    totals = conn.execute("""SELECT
        COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE 0 END),0) income,
        COALESCE(SUM(CASE WHEN type='expense' THEN amount ELSE 0 END),0) expense
        FROM transactions WHERE user_id=?""",(uid,)).fetchone()
    recent = conn.execute("""SELECT id,type,amount,category,description,date
        FROM transactions WHERE user_id=? ORDER BY date DESC,id DESC LIMIT 8""",(uid,)).fetchall()
    cats = conn.execute("""SELECT category, SUM(amount) amount FROM transactions
        WHERE user_id=? AND type='expense' GROUP BY category ORDER BY amount DESC""",(uid,)).fetchall()
    monthly = conn.execute("""SELECT substr(date,1,7) month,
        COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE 0 END),0) income,
        COALESCE(SUM(CASE WHEN type='expense' THEN amount ELSE 0 END),0) expense
        FROM transactions WHERE user_id=? GROUP BY substr(date,1,7) ORDER BY month DESC LIMIT 6""",(uid,)).fetchall()
    conn.close()
    income=float(totals["income"]); expense=float(totals["expense"])
    return jsonify(success=True, income=income, expense=expense, balance=income-expense,
                   savings=income-expense, recent=[dict(x) for x in recent],
                   categories=[dict(x) for x in cats],
                   monthly=[dict(x) for x in reversed(monthly)])

@app.get("/api/transactions")
@login_required
def transactions():
    conn=db()
    rows=conn.execute("""SELECT id,type,amount,category,description,date
        FROM transactions WHERE user_id=? ORDER BY date DESC,id DESC""",(session["user_id"],)).fetchall()
    conn.close()
    return jsonify(success=True, transactions=[dict(x) for x in rows])

@app.post("/api/transactions")
@login_required
def add_transaction():
    data=request.get_json() or {}
    typ=data.get("type"); category=data.get("category","").strip()
    desc=data.get("description","").strip(); dt=data.get("date") or str(date.today())
    try: amount=float(data.get("amount"))
    except: return jsonify(success=False,message="Enter a valid amount"),400
    if typ not in ("income","expense") or amount <= 0 or not category:
        return jsonify(success=False,message="Type, amount and category are required"),400
    conn=db()
    conn.execute("""INSERT INTO transactions(user_id,type,amount,category,description,date)
                    VALUES(?,?,?,?,?,?)""",(session["user_id"],typ,amount,category,desc,dt))
    conn.commit(); conn.close()
    return jsonify(success=True)

@app.delete("/api/transactions/<int:tid>")
@login_required
def delete_transaction(tid):
    conn=db()
    conn.execute("DELETE FROM transactions WHERE id=? AND user_id=?",(tid,session["user_id"]))
    conn.commit(); conn.close()
    return jsonify(success=True)

@app.get("/api/budgets")
@login_required
def budgets():
    uid=session["user_id"]; month=request.args.get("month",date.today().strftime("%Y-%m"))
    conn=db()
    rows=conn.execute("""SELECT b.id,b.category,b.amount,b.month,
        COALESCE((SELECT SUM(t.amount) FROM transactions t
        WHERE t.user_id=b.user_id AND t.type='expense' AND t.category=b.category
        AND substr(t.date,1,7)=b.month),0) spent
        FROM budgets b WHERE b.user_id=? AND b.month=? ORDER BY b.category""",(uid,month)).fetchall()
    conn.close()
    return jsonify(success=True,budgets=[dict(x) for x in rows])

@app.post("/api/budgets")
@login_required
def add_budget():
    data=request.get_json() or {}; category=data.get("category","").strip()
    month=data.get("month") or date.today().strftime("%Y-%m")
    try: amount=float(data.get("amount"))
    except: return jsonify(success=False,message="Invalid amount"),400
    if not category or amount<=0: return jsonify(success=False,message="Category and amount required"),400
    conn=db()
    conn.execute("""INSERT INTO budgets(user_id,category,amount,month) VALUES(?,?,?,?)
                    ON CONFLICT(user_id,category,month) DO UPDATE SET amount=excluded.amount""",
                 (session["user_id"],category,amount,month))
    conn.commit(); conn.close()
    return jsonify(success=True)

@app.delete("/api/budgets/<int:bid>")
@login_required
def delete_budget(bid):
    conn=db(); conn.execute("DELETE FROM budgets WHERE id=? AND user_id=?",(bid,session["user_id"]))
    conn.commit(); conn.close(); return jsonify(success=True)

@app.get("/api/goals")
@login_required
def goals():
    conn=db(); rows=conn.execute("SELECT * FROM goals WHERE user_id=? ORDER BY id DESC",(session["user_id"],)).fetchall()
    conn.close(); return jsonify(success=True,goals=[dict(x) for x in rows])

@app.post("/api/goals")
@login_required
def add_goal():
    data=request.get_json() or {}
    name=data.get("name","").strip()
    try:
        target=float(data.get("target_amount")); saved=float(data.get("saved_amount",0))
    except: return jsonify(success=False,message="Invalid amount"),400
    if not name or target<=0 or saved<0: return jsonify(success=False,message="Enter valid goal details"),400
    conn=db(); conn.execute("""INSERT INTO goals(user_id,name,target_amount,saved_amount,deadline)
                               VALUES(?,?,?,?,?)""",
                            (session["user_id"],name,target,saved,data.get("deadline","")))
    conn.commit(); conn.close(); return jsonify(success=True)

@app.put("/api/goals/<int:gid>")
@login_required
def update_goal(gid):
    data=request.get_json() or {}
    try: saved=float(data.get("saved_amount"))
    except: return jsonify(success=False,message="Invalid amount"),400
    conn=db(); conn.execute("UPDATE goals SET saved_amount=? WHERE id=? AND user_id=?",
                            (max(0,saved),gid,session["user_id"]))
    conn.commit(); conn.close(); return jsonify(success=True)

@app.delete("/api/goals/<int:gid>")
@login_required
def delete_goal(gid):
    conn=db(); conn.execute("DELETE FROM goals WHERE id=? AND user_id=?",(gid,session["user_id"]))
    conn.commit(); conn.close(); return jsonify(success=True)

init_db()
if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=debug, host="0.0.0.0", port=port)
