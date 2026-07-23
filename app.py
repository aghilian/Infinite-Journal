import base64
import hashlib
import hmac
import html
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


APP_NAME = "The Journal"
DATA_DIR = Path(os.environ.get("THEJOURNAL_DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "journal.db"
ASSET_DIR = DATA_DIR / "assets"
STATIC_DIR = Path(__file__).parent / "static"
SESSION_DAYS = 14
PBKDF2_ITERATIONS = 240_000
MAX_JSON_BYTES = 12_000_000
MAX_ASSET_BYTES = 8_000_000
ALLOWED_IMAGE_TYPES = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
ALLOWED_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "div",
    "em",
    "h2",
    "h3",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "s",
    "strong",
    "u",
    "ul",
}
VOID_TAGS = {"br", "img"}


def utc_now():
    return datetime.now(timezone.utc)


def today_key():
    return datetime.now().strftime("%Y-%m-%d")


def db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class NoteSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag not in ALLOWED_TAGS:
            return
        safe_attrs = []
        attr_map = {name.lower(): value for name, value in attrs if value is not None}
        if tag == "a":
            href = attr_map.get("href", "").strip()
            if href.startswith(("http://", "https://", "mailto:")):
                safe_attrs.append(("href", href))
                safe_attrs.append(("target", "_blank"))
                safe_attrs.append(("rel", "noreferrer"))
        elif tag == "img":
            src = attr_map.get("src", "").strip()
            if src.startswith("/assets/"):
                safe_attrs.append(("src", src))
                safe_attrs.append(("alt", attr_map.get("alt", "")[:160]))
                safe_attrs.append(("loading", "lazy"))
        attrs_text = "".join(f' {name}="{html.escape(value, quote=True)}"' for name, value in safe_attrs)
        self.parts.append(f"<{tag}{attrs_text}>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        self.parts.append(html.escape(data))

    def get_html(self):
        return "".join(self.parts)


def sanitize_note(content):
    if "<" not in content and ">" not in content:
        return "<p>" + html.escape(content).replace("\n", "<br>") + "</p>" if content else ""
    parser = NoteSanitizer()
    parser.feed(content)
    return parser.get_html()


def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return base64.b64encode(salt).decode(), base64.b64encode(digest).decode()


def verify_password(password, salt_b64, digest_b64):
    salt = base64.b64decode(salt_b64.encode())
    _, candidate = password_hash(password, salt)
    return hmac.compare_digest(candidate, digest_b64)


def init_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notes (
                note_date TEXT PRIMARY KEY,
                content TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            """
        )
        user_count = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
        if user_count == 0:
            username = os.environ.get("THEJOURNAL_USER", "admin")
            password = os.environ.get("THEJOURNAL_PASSWORD")
            if not password:
                password = secrets.token_urlsafe(18)
                (DATA_DIR / "initial-password.txt").write_text(
                    f"username={username}\npassword={password}\n", encoding="utf-8"
                )
            salt, digest = password_hash(password)
            conn.execute(
                "INSERT INTO users (username, salt, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (username, salt, digest, utc_now().isoformat()),
            )


def token_digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_cookie(header):
    cookie = SimpleCookie()
    if header:
        cookie.load(header)
    return cookie


class JournalHandler(BaseHTTPRequestHandler):
    server_version = "TheJournal/1.0"

    def log_message(self, fmt, *args):
        print("%s - - [%s] %s" % (self.client_address[0], self.log_date_time_string(), fmt % args))

    def send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text, status=HTTPStatus.OK, content_type="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_binary(self, body, content_type, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=31536000")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_JSON_BYTES:
            raise ValueError("Request too large")
        data = self.rfile.read(length)
        return json.loads(data.decode("utf-8") or "{}")

    def current_user(self):
        token = parse_cookie(self.headers.get("Cookie")).get("tj_session")
        if not token:
            return None
        digest = token_digest(token.value)
        with db() as conn:
            row = conn.execute(
                """
                SELECT users.id, users.username
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE token_hash = ? AND expires_at > ?
                """,
                (digest, int(time.time())),
            ).fetchone()
            return dict(row) if row else None

    def require_user(self):
        user = self.current_user()
        if not user:
            self.send_json({"error": "Authentication required"}, HTTPStatus.UNAUTHORIZED)
            return None
        return user

    def set_session_cookie(self, token):
        secure = os.environ.get("THEJOURNAL_COOKIE_SECURE", "false").lower() == "true"
        cookie = f"tj_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_DAYS * 86400}"
        if secure:
            cookie += "; Secure"
        self.send_header("Set-Cookie", cookie)

    def clear_session_cookie(self):
        self.send_header(
            "Set-Cookie",
            "tj_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0",
        )

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            self.send_text("ok")
            return
        if path.startswith("/static/"):
            self.serve_static(path.removeprefix("/static/"))
            return
        if path.startswith("/assets/"):
            user = self.require_user()
            if user:
                self.serve_asset(path.removeprefix("/assets/"))
            return
        if path == "/api/me":
            user = self.current_user()
            self.send_json({"authenticated": bool(user), "user": user})
            return
        if path == "/api/journal":
            user = self.require_user()
            if not user:
                return
            self.get_journal()
            return
        self.serve_index()

    def do_HEAD(self):
        path = urlparse(self.path).path
        if path == "/health":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/login":
                self.login()
            elif path == "/api/logout":
                self.logout()
            elif path == "/api/journal/today":
                user = self.require_user()
                if user:
                    self.save_today()
            elif path == "/api/password":
                user = self.require_user()
                if user:
                    self.change_password(user["id"])
            elif path == "/api/assets":
                user = self.require_user()
                if user:
                    self.upload_asset()
            else:
                self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except (json.JSONDecodeError, ValueError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def serve_index(self):
        content = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.send_text(content, content_type="text/html; charset=utf-8")

    def serve_static(self, name):
        safe_name = Path(name).name
        path = STATIC_DIR / safe_name
        if not path.exists():
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        content_types = {
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".ico": "image/x-icon",
        }
        self.send_text(
            path.read_text(encoding="utf-8"),
            content_type=content_types.get(path.suffix, "text/plain; charset=utf-8"),
        )

    def serve_asset(self, name):
        safe_name = Path(name).name
        if safe_name != name or not re.match(r"^[a-f0-9]{32}\.(gif|jpg|png|webp)$", safe_name):
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        path = ASSET_DIR / safe_name
        if not path.exists():
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_binary(path.read_bytes(), content_type)

    def login(self):
        payload = self.read_json()
        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", ""))
        with db() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if not row or not verify_password(password, row["salt"], row["password_hash"]):
                self.send_json({"error": "Invalid username or password"}, HTTPStatus.UNAUTHORIZED)
                return
            token = secrets.token_urlsafe(36)
            expires_at = int((utc_now() + timedelta(days=SESSION_DAYS)).timestamp())
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (token_digest(token), row["id"], expires_at),
            )
        body = json.dumps({"ok": True}).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.set_session_cookie(token)
        self.end_headers()
        self.wfile.write(body)

    def logout(self):
        token = parse_cookie(self.headers.get("Cookie")).get("tj_session")
        if token:
            with db() as conn:
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_digest(token.value),))
        body = json.dumps({"ok": True}).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.clear_session_cookie()
        self.end_headers()
        self.wfile.write(body)

    def get_journal(self):
        current_day = today_key()
        with db() as conn:
            today = conn.execute("SELECT * FROM notes WHERE note_date = ?", (current_day,)).fetchone()
            older = conn.execute(
                """
                SELECT note_date, content, updated_at
                FROM notes
                WHERE note_date < ? AND length(trim(content)) > 0
                ORDER BY note_date DESC
                LIMIT 60
                """,
                (current_day,),
            ).fetchall()
        self.send_json(
            {
                "appName": APP_NAME,
                "today": {
                    "date": current_day,
                    "content": today["content"] if today else "",
                    "updatedAt": today["updated_at"] if today else None,
                },
                "older": [dict(row) for row in older],
            }
        )

    def save_today(self):
        payload = self.read_json()
        content = sanitize_note(str(payload.get("content", "")))
        current_day = today_key()
        updated_at = utc_now().isoformat()
        with db() as conn:
            conn.execute(
                """
                INSERT INTO notes (note_date, content, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(note_date) DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at
                """,
                (current_day, content, updated_at),
            )
        self.send_json({"ok": True, "date": current_day, "updatedAt": updated_at})

    def upload_asset(self):
        payload = self.read_json()
        data_url = str(payload.get("dataUrl", ""))
        match = re.match(r"^data:(image/(?:gif|jpeg|png|webp));base64,(.+)$", data_url, re.DOTALL)
        if not match:
            self.send_json({"error": "Unsupported image data"}, HTTPStatus.BAD_REQUEST)
            return
        content_type, encoded = match.groups()
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_ASSET_BYTES:
            self.send_json({"error": "Image must be 8 MB or smaller"}, HTTPStatus.BAD_REQUEST)
            return
        ASSET_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"{secrets.token_hex(16)}{ALLOWED_IMAGE_TYPES[content_type]}"
        path = ASSET_DIR / filename
        path.write_bytes(raw)
        self.send_json({"ok": True, "url": f"/assets/{filename}"})

    def change_password(self, user_id):
        payload = self.read_json()
        current = str(payload.get("currentPassword", ""))
        new = str(payload.get("newPassword", ""))
        if len(new) < 12:
            self.send_json({"error": "New password must be at least 12 characters"}, HTTPStatus.BAD_REQUEST)
            return
        with db() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row or not verify_password(current, row["salt"], row["password_hash"]):
                self.send_json({"error": "Current password is incorrect"}, HTTPStatus.UNAUTHORIZED)
                return
            salt, digest = password_hash(new)
            conn.execute(
                "UPDATE users SET salt = ?, password_hash = ? WHERE id = ?",
                (salt, digest, user_id),
            )
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        self.send_json({"ok": True})


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), JournalHandler)
    print(f"{APP_NAME} listening on :{port}")
    server.serve_forever()
