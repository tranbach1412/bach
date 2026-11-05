import os, sqlite3, secrets, time
from functools import wraps
from urllib.parse import urlparse
from flask import Flask, render_template, request, redirect, url_for, session, flash, g, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "codehub.db")
app = Flask(__name__)

# ---- Cấu hình online -------------------------------------------------------
# Đặt CODEHUB_ENV=production khi chạy trên Internet (bắt buộc có CODEHUB_SECRET).
PRODUCTION = os.environ.get("CODEHUB_ENV", "").lower() == "production"
DEFAULT_SECRET = "change-this-secret-before-deploying"
app.secret_key = os.environ.get("CODEHUB_SECRET", DEFAULT_SECRET)
if PRODUCTION and app.secret_key == DEFAULT_SECRET:
    raise RuntimeError("Hãy đặt biến môi trường CODEHUB_SECRET (chuỗi ngẫu nhiên dài) khi chạy online.")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=PRODUCTION,   # cookie chỉ gửi qua HTTPS khi online
)
if PRODUCTION:
    # Chạy sau reverse proxy (Nginx, Caddy, Render, Railway...) có HTTPS
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(_):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',
        points INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        token_version INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS contests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT DEFAULT '',
        difficulty TEXT NOT NULL DEFAULT 'Medium',
        reward INTEGER NOT NULL DEFAULT 100,
        contest_date TEXT DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        contest_id INTEGER NOT NULL REFERENCES contests(id) ON DELETE CASCADE,
        note TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'Pending',
        awarded INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, contest_id)
    );
    """)
    # Nâng cấp DB cũ: thêm cột token_version nếu chưa có
    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
    if "token_version" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0")
    # First-run administrator. Change this password after signing in.
    exists = conn.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
    if not exists:
        conn.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                     ("admin", generate_password_hash("Admin@12345"), "admin"))
    conn.commit()
    conn.close()

init_db()  # chạy một lần khi khởi động

@app.before_request
def load_user_and_check_origin():
    # Chặn POST từ website khác (CSRF) mà không cần sửa template
    if request.method == "POST":
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            abort(403)
    g.user = None
    uid = session.get("user_id")
    if uid:
        row = db().execute("SELECT id,username,role,token_version FROM users WHERE id=?", (uid,)).fetchone()
        # Đổi/reset mật khẩu sẽ làm token_version tăng -> phiên đăng nhập cũ bị đăng xuất
        if row and row["token_version"] == session.get("tv", 0):
            g.user = row
        else:
            session.clear()

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not g.user:
            flash("Hãy đăng nhập để tiếp tục.", "warning")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped

def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not g.user:
            return redirect(url_for("login"))
        if g.user["role"] != "admin":
            flash("Bạn không có quyền truy cập trang quản trị.", "danger")
            return redirect(url_for("dashboard"))
        return fn(*args, **kwargs)
    return wrapped

# Giới hạn đăng nhập sai: 5 lần / 5 phút cho mỗi (IP, tên đăng nhập)
_fails = {}
def too_many_fails(key):
    now = time.time()
    _fails[key] = [t for t in _fails.get(key, []) if now - t < 300]
    return len(_fails[key]) >= 5

@app.route("/")
def home():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return render_template("home.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if len(username) < 3 or len(password) < 8:
            flash("Tên tài khoản cần ít nhất 3 ký tự, mật khẩu ít nhất 8 ký tự.", "danger")
        else:
            try:
                db().execute("INSERT INTO users(username,password_hash) VALUES(?,?)",
                             (username, generate_password_hash(password)))
                db().commit()
                flash("Tạo tài khoản thành công. Hãy đăng nhập.", "success")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("Tên tài khoản đã tồn tại.", "danger")
    return render_template("auth.html", mode="register")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        key = (request.remote_addr, username.lower())
        if too_many_fails(key):
            flash("Bạn nhập sai quá nhiều lần. Hãy thử lại sau 5 phút.", "danger")
            return render_template("auth.html", mode="login")
        user = db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            _fails.pop(key, None)
            session.clear()
            session["user_id"] = user["id"]
            session["tv"] = user["token_version"]
            return redirect(url_for("dashboard"))
        _fails.setdefault(key, []).append(time.time())
        flash("Sai tên tài khoản hoặc mật khẩu.", "danger")
    return render_template("auth.html", mode="login")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

@app.route("/dashboard")
@login_required
def dashboard():
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    contests = conn.execute("""
        SELECT c.*, s.status AS my_status FROM contests c
        LEFT JOIN submissions s ON s.contest_id=c.id AND s.user_id=?
        ORDER BY c.contest_date DESC, c.id DESC
    """, (session["user_id"],)).fetchall()
    ranking = conn.execute("SELECT username,points FROM users ORDER BY points DESC, username LIMIT 10").fetchall()
    done = conn.execute("SELECT COUNT(*) n FROM submissions WHERE user_id=? AND status='Approved'",
                        (session["user_id"],)).fetchone()["n"]
    return render_template("dashboard.html", user=user, contests=contests, ranking=ranking, done=done)

@app.route("/contest/<int:contest_id>", methods=["GET", "POST"])
@login_required
def contest_detail(contest_id):
    conn = db()
    contest = conn.execute("SELECT * FROM contests WHERE id=?", (contest_id,)).fetchone()
    if not contest:
        flash("Không tìm thấy contest.", "danger")
        return redirect(url_for("dashboard"))
    previous = conn.execute("SELECT * FROM submissions WHERE contest_id=? AND user_id=?",
                            (contest_id, session["user_id"])).fetchone()
    if request.method == "POST":
        note = request.form.get("note", "").strip()[:2000]
        if previous:
            flash("Bạn đã gửi kết quả contest này rồi.", "warning")
        else:
            conn.execute("INSERT INTO submissions(user_id,contest_id,note) VALUES(?,?,?)",
                         (session["user_id"], contest_id, note))
            conn.commit()
            flash("Đã gửi kết quả để Admin xét duyệt.", "success")
            return redirect(url_for("contest_detail", contest_id=contest_id))
    return render_template("contest.html", contest=contest, previous=previous)

@app.route("/admin")
@admin_required
def admin():
    conn = db()
    contests = conn.execute("SELECT * FROM contests ORDER BY id DESC").fetchall()
    submissions = conn.execute("""
        SELECT s.*, u.username, c.title, c.reward
        FROM submissions s JOIN users u ON u.id=s.user_id
        JOIN contests c ON c.id=s.contest_id
        ORDER BY CASE s.status WHEN 'Pending' THEN 0 ELSE 1 END, s.id DESC
    """).fetchall()
    users = conn.execute("SELECT id,username,role,points,created_at FROM users ORDER BY points DESC").fetchall()
    return render_template("admin.html", contests=contests, submissions=submissions, users=users)

@app.route("/admin/contest", methods=["POST"])
@admin_required
def create_contest():
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    difficulty = request.form.get("difficulty", "Medium")
    date = request.form.get("contest_date", "").strip()
    try:
        reward = max(0, min(100000, int(request.form.get("reward", "100"))))
    except ValueError:
        reward = 100
    if title:
        db().execute("INSERT INTO contests(title,description,difficulty,reward,contest_date) VALUES(?,?,?,?,?)",
                     (title, description, difficulty, reward, date))
        db().commit()
        flash("Đã tạo contest.", "success")
    else:
        flash("Tên contest không được để trống.", "danger")
    return redirect(url_for("admin"))

@app.route("/admin/contest/<int:contest_id>/delete", methods=["POST"])
@admin_required
def delete_contest(contest_id):
    db().execute("DELETE FROM contests WHERE id=?", (contest_id,))
    db().commit()
    flash("Đã xóa contest và các lượt nộp liên quan.", "success")
    return redirect(url_for("admin"))

@app.route("/admin/submission/<int:submission_id>/<action>", methods=["POST"])
@admin_required
def review_submission(submission_id, action):
    conn = db()
    sub = conn.execute("SELECT * FROM submissions WHERE id=?", (submission_id,)).fetchone()
    if not sub:
        flash("Không tìm thấy lượt nộp.", "danger")
    elif sub["status"] == "Pending" and action in ("approve", "reject"):
        if action == "approve":
            reward = conn.execute("SELECT reward FROM contests WHERE id=?", (sub["contest_id"],)).fetchone()["reward"]
            conn.execute("UPDATE submissions SET status='Approved', awarded=? WHERE id=?", (reward, submission_id))
            conn.execute("UPDATE users SET points=points+? WHERE id=?", (reward, sub["user_id"]))
            flash("Đã duyệt và cộng điểm.", "success")
        else:
            conn.execute("UPDATE submissions SET status='Rejected' WHERE id=?", (submission_id,))
            flash("Đã từ chối lượt nộp.", "success")
        conn.commit()
    return redirect(url_for("admin"))

@app.route("/admin/users")
@admin_required
def admin_users():
    users = db().execute("""
        SELECT u.id, u.username, u.role, u.points, u.created_at,
               (SELECT COUNT(*) FROM submissions s WHERE s.user_id=u.id) AS submissions
        FROM users u ORDER BY u.role='admin' DESC, u.username COLLATE NOCASE
    """).fetchall()
    return render_template("admin_users.html", users=users)

@app.route("/admin/user/<int:user_id>/password", methods=["POST"])
@admin_required
def admin_reset_password(user_id):
    conn = db()
    target = conn.execute("SELECT id,username FROM users WHERE id=?", (user_id,)).fetchone()
    if not target:
        flash("Không tìm thấy người dùng.", "danger")
        return redirect(url_for("admin_users"))
    new = request.form.get("new_password", "")
    generated = False
    if not new:                       # để trống -> tự sinh mật khẩu ngẫu nhiên
        new = secrets.token_urlsafe(9)
        generated = True
    if len(new) < 8:
        flash("Mật khẩu mới cần ít nhất 8 ký tự.", "danger")
        return redirect(url_for("admin_users"))
    conn.execute("UPDATE users SET password_hash=?, token_version=token_version+1 WHERE id=?",
                 (generate_password_hash(new), user_id))
    conn.commit()
    app.logger.warning("ADMIN %s (id=%s) reset password of user %s (id=%s)",
                       g.user["username"], g.user["id"], target["username"], target["id"])
    if user_id == g.user["id"]:       # admin tự đổi cho mình: giữ phiên hiện tại
        session["tv"] = conn.execute("SELECT token_version FROM users WHERE id=?", (user_id,)).fetchone()[0]
    if generated:
        flash(f"Mật khẩu mới của {target['username']}: {new} (chỉ hiện một lần, hãy gửi cho người dùng).", "success")
    else:
        flash(f"Đã đặt lại mật khẩu cho {target['username']}.", "success")
    return redirect(url_for("admin_users"))

@app.route("/account/password", methods=["POST"])
@login_required
def change_password():
    old = request.form.get("old_password", "")
    new = request.form.get("new_password", "")
    user = db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    if not check_password_hash(user["password_hash"], old):
        flash("Mật khẩu hiện tại không đúng.", "danger")
    elif len(new) < 8:
        flash("Mật khẩu mới cần ít nhất 8 ký tự.", "danger")
    else:
        db().execute("UPDATE users SET password_hash=?, token_version=token_version+1 WHERE id=?",
                     (generate_password_hash(new), session["user_id"]))
        db().commit()
        session["tv"] = user["token_version"] + 1
        flash("Đã đổi mật khẩu.", "success")
    return redirect(url_for("dashboard"))

if __name__ == "__main__":
    # Local: 127.0.0.1. Muốn mở cho máy khác trong mạng: đặt CODEHUB_HOST=0.0.0.0
    # Online thật nên chạy bằng gunicorn/waitress sau HTTPS (xem README), không dùng app.run.
    app.run(host=os.environ.get("CODEHUB_HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "8000")), debug=False)

