import base64
import os
import socket
import sqlite3
from io import BytesIO

import pyotp
import qrcode
from flask import Flask, render_template, request, redirect, url_for, session
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
# In production set SECRET_KEY to a long random value (e.g. `python -c "import secrets; print(secrets.token_hex(32))"`).
# The fallback exists only so local development works out of the box.
app.secret_key = os.environ.get("SECRET_KEY", "dev-insecure-secret-change-me")

DATABASE = os.environ.get("DATABASE", "users.db")


def init_db():
    conn = sqlite3.connect(DATABASE)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            two_factor_enabled INTEGER NOT NULL DEFAULT 0,
            two_factor_secret TEXT
        )
    """)

    conn.commit()
    conn.close()
    ensure_user_columns()


def ensure_user_columns():
    conn = sqlite3.connect(DATABASE)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}

    if "two_factor_enabled" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN two_factor_enabled INTEGER NOT NULL DEFAULT 0")

    if "two_factor_secret" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN two_factor_secret TEXT")

    conn.commit()
    conn.close()


def create_user(email, password):
    hashed_password = generate_password_hash(password)
    secret = pyotp.random_base32()

    conn = sqlite3.connect(DATABASE)
    conn.execute(
        "INSERT INTO users(email, password, two_factor_enabled, two_factor_secret) VALUES(?,?,0,?)",
        (email, hashed_password, secret),
    )
    conn.commit()
    user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    return user_id, secret


def get_user_by_email(email):
    conn = sqlite3.connect(DATABASE)
    user = conn.execute(
        "SELECT id, email, password, two_factor_enabled, two_factor_secret FROM users WHERE email=?",
        (email,),
    ).fetchone()
    conn.close()
    return user


def get_user_by_id(user_id):
    conn = sqlite3.connect(DATABASE)
    user = conn.execute(
        "SELECT id, email, password, two_factor_enabled, two_factor_secret FROM users WHERE id=?",
        (user_id,),
    ).fetchone()
    conn.close()
    return user


def update_two_factor_setup(user_id, secret):
    conn = sqlite3.connect(DATABASE)
    conn.execute(
        "UPDATE users SET two_factor_enabled=1, two_factor_secret=? WHERE id=?",
        (secret, user_id),
    )
    conn.commit()
    conn.close()


def build_qr_code(secret, email):
    totp = pyotp.totp.TOTP(secret)
    uri = totp.provisioning_uri(name=email, issuer_name="Login App")
    image = qrcode.make(uri)
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    encoded = base64.b64encode(buffered.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{encoded}"


def find_available_port(start_port=5000, max_tries=10):
    for port in range(start_port, start_port + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    return start_port + max_tries


# Ensure the schema exists at import time so the app works under any WSGI
# server (gunicorn, uWSGI, ...), not just when run directly via `python app.py`.
init_db()


@app.route("/")
def home():
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            return render_template("register.html", error="Please enter an email and password")

        try:
            user_id, secret = create_user(email, password)
            session["pending_2fa_user_id"] = user_id
            session["pending_2fa_email"] = email
            session["pending_2fa_secret"] = secret
            return redirect(url_for("two_factor_setup"))
        except sqlite3.IntegrityError:
            return render_template("register.html", error="Email already exists")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = get_user_by_email(email)

        if user and check_password_hash(user[2], password):
            if user[3] == 1:
                session["pending_2fa_user_id"] = user[0]
                session["pending_2fa_email"] = user[1]
                session["pending_2fa_secret"] = user[4]
                return redirect(url_for("two_factor_verify"))

            secret = user[4] or pyotp.random_base32()
            if not user[4]:
                conn = sqlite3.connect(DATABASE)
                conn.execute("UPDATE users SET two_factor_secret=? WHERE id=?", (secret, user[0]))
                conn.commit()
                conn.close()

            session["pending_2fa_user_id"] = user[0]
            session["pending_2fa_email"] = user[1]
            session["pending_2fa_secret"] = secret
            return redirect(url_for("two_factor_setup"))

        return render_template("login.html", error="Invalid email or password")

    return render_template("login.html")


@app.route("/two-factor/setup", methods=["GET", "POST"])
def two_factor_setup():
    if "pending_2fa_user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["pending_2fa_user_id"]
    user = get_user_by_id(user_id)

    if user and user[3] == 1:
        session["user_id"] = user[0]
        session["email"] = user[1]
        session.pop("pending_2fa_user_id", None)
        session.pop("pending_2fa_secret", None)
        return redirect(url_for("dashboard"))

    secret = session.get("pending_2fa_secret") or user[4]
    email = session.get("pending_2fa_email") or user[1]

    if request.method == "POST":
        code = request.form.get("code", "").strip()
        totp = pyotp.TOTP(secret)

        if totp.verify(code, valid_window=1):
            update_two_factor_setup(user_id, secret)
            session["user_id"] = user_id
            session["email"] = email
            session.pop("pending_2fa_user_id", None)
            session.pop("pending_2fa_secret", None)
            return redirect(url_for("dashboard"))

        return render_template(
            "two_factor_setup.html",
            email=email,
            qr_code=build_qr_code(secret, email),
            secret=secret,
            error="Invalid code. Please try again.",
        )

    return render_template(
        "two_factor_setup.html",
        email=email,
        qr_code=build_qr_code(secret, email),
        secret=secret,
    )


@app.route("/two-factor/verify", methods=["GET", "POST"])
def two_factor_verify():
    if "pending_2fa_user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["pending_2fa_user_id"]
    user = get_user_by_id(user_id)

    if not user:
        session.clear()
        return redirect(url_for("login"))

    if request.method == "POST":
        code = request.form.get("code", "").strip()
        secret = user[4]
        totp = pyotp.TOTP(secret)

        if totp.verify(code, valid_window=1):
            session["user_id"] = user[0]
            session["email"] = user[1]
            session.pop("pending_2fa_user_id", None)
            session.pop("pending_2fa_secret", None)
            return redirect(url_for("dashboard"))

        return render_template("two_factor_verify.html", error="Invalid code. Please try again.")

    return render_template("two_factor_verify.html")


@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    return render_template("dashboard.html", email=session["email"])


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    configured_port = int(os.environ.get("PORT", "5000"))
    selected_port = configured_port if os.environ.get("PORT") else find_available_port(configured_port)

    if not os.environ.get("PORT") and selected_port != configured_port:
        print(f"Port {configured_port} is busy. Starting on port {selected_port} instead.")

    debug = os.environ.get("FLASK_DEBUG", "1").lower() in ("1", "true", "yes")
    app.run(debug=debug, host="0.0.0.0", port=selected_port)