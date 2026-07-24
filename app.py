import base64
import hashlib
import hmac
import html
import io
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


APP_NAME = "The Journal"
DATA_DIR = Path(os.environ.get("THEJOURNAL_DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "journal.db"
ASSET_DIR = DATA_DIR / "assets"
BACKUP_DIR = DATA_DIR / "backups"
STATIC_DIR = Path(__file__).parent / "static"
SESSION_DAYS = 14
PBKDF2_ITERATIONS = 240_000
MAX_JSON_BYTES = 12_000_000
MAX_ASSET_BYTES = 8_000_000
MAX_BACKUP_BYTES = 100_000_000
MAX_IMPORT_BYTES = 1_000_000
MAX_IMPORT_ENTRIES = 300
BACKUP_INTERVAL_SECONDS = 24 * 60 * 60
MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
WEEKDAYS = {
    "mon",
    "monday",
    "tue",
    "tues",
    "tuesday",
    "wed",
    "wednesday",
    "thu",
    "thur",
    "thurs",
    "thursday",
    "fri",
    "friday",
    "sat",
    "saturday",
    "sun",
    "sunday",
}
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
    "hr",
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
LOGIN_ATTEMPTS = {}
CONTEXTS = {"personal", "work"}
PERSONAL_PIN_TOKENS = {}
PIN_ATTEMPTS = {}
PIN_TOKEN_SECONDS = 30 * 60


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


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"br", "p", "div", "li", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)

    def get_text(self):
        return re.sub(r"\n{3,}", "\n\n", "".join(self.parts)).strip()


def note_text(content):
    if "<" not in content and ">" not in content:
        return content.strip()
    parser = TextExtractor()
    parser.feed(content)
    return parser.get_text()


def word_count(content):
    text = note_text(content or "")
    return len(re.findall(r"\b[\w'-]+\b", text))


def normalize_tags(tags):
    raw = tags if isinstance(tags, list) else str(tags or "").split(",")
    cleaned = []
    seen = set()
    for tag in raw:
        value = re.sub(r"\s+", " ", str(tag).strip().lower())
        value = re.sub(r"[^a-z0-9 _-]", "", value).strip(" -_")
        if value and value not in seen:
            cleaned.append(value[:40])
            seen.add(value)
    return ", ".join(cleaned[:20])


def normalize_context(value):
    context = str(value or "personal").strip().lower()
    if context not in CONTEXTS:
        raise ValueError("Invalid context")
    return context


def validate_pin(pin):
    value = str(pin or "")
    if not re.fullmatch(r"\d{4}", value):
        raise ValueError("PIN must be exactly 4 digits")
    return value


def validate_date(value):
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        raise ValueError("Invalid date")
    return value


def expand_year(year):
    value = int(year)
    if value < 100:
        return 2000 + value if value < 70 else 1900 + value
    return value


def valid_date_parts(year, month, day):
    try:
        return datetime(int(year), int(month), int(day)).strftime("%Y-%m-%d")
    except ValueError:
        return None


def clean_import_date_line(line):
    value = str(line or "").strip()
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1].strip()
    value = re.sub(r"^\s*(?:date\s*[:\-]\s*)", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(\d{1,2})(st|nd|rd|th)\b", r"\1", value, flags=re.IGNORECASE)
    value = re.sub(r"[,\u2013\u2014]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    parts = value.split()
    while parts and parts[0].lower().strip(".") in WEEKDAYS:
        parts.pop(0)
    return " ".join(parts)


def parse_import_date(line, date_order="mdy"):
    original = str(line or "").strip()
    value = clean_import_date_line(original)
    if not value:
        return None

    match = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", value)
    if match:
        parsed = valid_date_parts(match.group(1), match.group(2), match.group(3))
        return {"date": parsed, "raw": original, "warning": ""} if parsed else None

    match = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", value)
    if match:
        first, second, year = match.groups()
        year = expand_year(year)
        first_int = int(first)
        second_int = int(second)
        warning = ""
        if first_int > 12 and second_int <= 12:
            month, day = second, first
        elif second_int > 12 and first_int <= 12:
            month, day = first, second
        else:
            if date_order == "dmy":
                day, month = first, second
                warning = "Ambiguous numeric date; interpreted as day/month/year."
            else:
                month, day = first, second
                warning = "Ambiguous numeric date; interpreted as month/day/year."
        parsed = valid_date_parts(year, month, day)
        return {"date": parsed, "raw": original, "warning": warning} if parsed else None

    tokens = value.split()
    lowered = [token.lower().strip(".") for token in tokens]
    month_index = next((index for index, token in enumerate(lowered) if token in MONTHS), None)
    if month_index is not None:
        month = MONTHS[lowered[month_index]]
        numbers = [int(token) for token in re.findall(r"\b\d{1,4}\b", value)]
        if len(numbers) >= 2:
            year_candidates = [number for number in numbers if number > 31 or len(str(number)) == 4]
            year = expand_year(year_candidates[-1] if year_candidates else numbers[-1])
            day_candidates = [number for number in numbers if number != year and 1 <= number <= 31]
            day = day_candidates[0] if day_candidates else None
            if day:
                parsed = valid_date_parts(year, month, day)
                return {"date": parsed, "raw": original, "warning": ""} if parsed else None

    try:
        parsed = datetime.strptime(value, "%B %d %Y").strftime("%Y-%m-%d")
        return {"date": parsed, "raw": original, "warning": ""}
    except ValueError:
        return None


def parse_import_entries(raw_text, default_context="personal", date_order="mdy"):
    text = str(raw_text or "").replace("\r\n", "\n").replace("\r", "\n")
    if len(text.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise ValueError("Import text is too large")
    context = normalize_context(default_context)
    entries = []
    current = None
    leading = []

    for line in text.split("\n"):
        parsed = parse_import_date(line, date_order)
        if parsed:
            if current:
                current["text"] = "\n".join(current.pop("lines")).strip()
                entries.append(current)
            elif leading and any(item.strip() for item in leading):
                entries.append(
                    {
                        "id": f"unparsed-{len(entries) + 1}",
                        "date": "",
                        "rawDate": "",
                        "text": "\n".join(leading).strip(),
                        "context": context,
                        "action": "skip",
                        "hasExisting": False,
                        "warning": "Text before the first date was not imported.",
                    }
                )
            current = {
                "id": f"entry-{len(entries) + 1}",
                "date": parsed["date"],
                "rawDate": parsed["raw"],
                "lines": [],
                "context": context,
                "action": "append",
                "hasExisting": False,
                "warning": parsed["warning"],
            }
        elif current:
            current["lines"].append(line)
        else:
            leading.append(line)

    if current:
        current["text"] = "\n".join(current.pop("lines")).strip()
        entries.append(current)
    elif leading and any(item.strip() for item in leading):
        entries.append(
            {
                "id": "unparsed-1",
                "date": "",
                "rawDate": "",
                "text": "\n".join(leading).strip(),
                "context": context,
                "action": "skip",
                "hasExisting": False,
                "warning": "No date lines were detected.",
            }
        )

    cleaned = []
    for entry in entries[:MAX_IMPORT_ENTRIES]:
        entry["text"] = str(entry.get("text") or "").strip()
        if not entry["text"] and entry.get("date"):
            entry["warning"] = "Date detected with no note text."
            entry["action"] = "skip"
        cleaned.append(entry)
    return cleaned


def add_import_conflicts(entries):
    dated = [(entry["date"], entry["context"]) for entry in entries if entry.get("date")]
    if not dated:
        return entries
    with db() as conn:
        for entry in entries:
            if not entry.get("date"):
                continue
            row = conn.execute(
                "SELECT length(trim(content)) AS content_length, length(trim(tags)) AS tag_length FROM notes WHERE note_date = ? AND context = ?",
                (entry["date"], entry["context"]),
            ).fetchone()
            has_existing = bool(row and ((row["content_length"] or 0) > 0 or (row["tag_length"] or 0) > 0))
            entry["hasExisting"] = has_existing
            entry["action"] = "append" if has_existing else "replace"
    return entries


def note_to_markdown(row):
    tags = row["tags"] or ""
    body = note_text(row["content"] or "")
    lines = [f"# {row['note_date']} - {row['context'].title()}", ""]
    if tags:
        lines.extend([f"Tags: {tags}", ""])
    lines.append(body)
    return "\n".join(lines).strip() + "\n"


def backup_payload():
    with db() as conn:
        notes = [
            dict(row)
            for row in conn.execute(
                "SELECT note_date, context, content, tags, updated_at FROM notes ORDER BY note_date ASC, context ASC"
            ).fetchall()
        ]
    return {
        "app": APP_NAME,
        "version": 1,
        "created_at": utc_now().isoformat(),
        "notes": notes,
    }


def create_backup_bytes():
    buffer = io.BytesIO()
    payload = backup_payload()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("journal.json", json.dumps(payload, indent=2))
        if ASSET_DIR.exists():
            for asset in ASSET_DIR.iterdir():
                if asset.is_file() and re.match(r"^[a-f0-9]{32}\.(gif|jpg|png|webp)$", asset.name):
                    archive.write(asset, f"assets/{asset.name}")
    buffer.seek(0)
    return buffer.getvalue()


def write_server_backup():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = BACKUP_DIR / f"the-journal-backup-{stamp}.zip"
    path.write_bytes(create_backup_bytes())
    backups = sorted(BACKUP_DIR.glob("the-journal-backup-*.zip"), key=lambda item: item.stat().st_mtime)
    for old in backups[:-14]:
        old.unlink(missing_ok=True)
    return path


def start_backup_scheduler():
    def loop():
        while True:
            time.sleep(BACKUP_INTERVAL_SECONDS)
            try:
                write_server_backup()
            except Exception as exc:
                print(f"scheduled backup failed: {exc}")

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()


def import_backup(raw):
    with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
        names = set(archive.namelist())
        if "journal.json" not in names:
            raise ValueError("Backup is missing journal.json")
        payload = json.loads(archive.read("journal.json").decode("utf-8"))
        if payload.get("app") != APP_NAME:
            raise ValueError("Backup does not look like a The Journal backup")
        notes = payload.get("notes") or []
        restored_assets = 0
        ASSET_DIR.mkdir(parents=True, exist_ok=True)
        for name in names:
            if not name.startswith("assets/"):
                continue
            filename = Path(name).name
            if re.match(r"^[a-f0-9]{32}\.(gif|jpg|png|webp)$", filename):
                (ASSET_DIR / filename).write_bytes(archive.read(name))
                restored_assets += 1
        restored_notes = 0
        with db() as conn:
            for note in notes:
                note_date = validate_date(note.get("note_date"))
                content = sanitize_note(str(note.get("content") or ""))
                tags = normalize_tags(note.get("tags") or "")
                context = normalize_context(note.get("context") or "personal")
                updated_at = str(note.get("updated_at") or utc_now().isoformat())
                conn.execute(
                    """
                    INSERT INTO notes (note_date, context, content, tags, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(note_date, context) DO UPDATE SET
                        content = excluded.content,
                        tags = excluded.tags,
                        updated_at = excluded.updated_at
                    """,
                    (note_date, context, content, tags, updated_at),
                )
                restored_notes += 1
    return {"notes": restored_notes, "assets": restored_assets}


def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return base64.b64encode(salt).decode(), base64.b64encode(digest).decode()


def verify_password(password, salt_b64, digest_b64):
    salt = base64.b64decode(salt_b64.encode())
    _, candidate = password_hash(password, salt)
    return hmac.compare_digest(candidate, digest_b64)


def personal_token_digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def init_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                pin_salt TEXT,
                pin_hash TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notes (
                note_date TEXT PRIMARY KEY,
                context TEXT NOT NULL DEFAULT 'personal',
                content TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(notes)").fetchall()}
        if "tags" not in columns:
            conn.execute("ALTER TABLE notes ADD COLUMN tags TEXT NOT NULL DEFAULT ''")
            columns.add("tags")
        if "context" not in columns:
            conn.execute("ALTER TABLE notes ADD COLUMN context TEXT NOT NULL DEFAULT 'personal'")
            columns.add("context")
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list(notes)").fetchall()}
        if "idx_notes_date_context" not in indexes:
            rows = conn.execute("SELECT note_date, context, content, tags, updated_at FROM notes").fetchall()
            conn.execute("ALTER TABLE notes RENAME TO notes_legacy")
            conn.execute(
                """
                CREATE TABLE notes (
                    note_date TEXT NOT NULL,
                    context TEXT NOT NULL DEFAULT 'personal',
                    content TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (note_date, context)
                )
                """
            )
            for row in rows:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO notes (note_date, context, content, tags, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        row["note_date"],
                        normalize_context(row["context"] or "personal"),
                        row["content"] or "",
                        row["tags"] or "",
                        row["updated_at"],
                    ),
                )
            conn.execute("DROP TABLE notes_legacy")
            conn.execute("CREATE INDEX idx_notes_date_context ON notes (note_date, context)")
        user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "pin_salt" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN pin_salt TEXT")
        if "pin_hash" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN pin_hash TEXT")
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

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'",
        )
        super().end_headers()

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

    def send_download(self, body, filename, content_type):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
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

    def personal_pin_is_set(self, user_id):
        with db() as conn:
            row = conn.execute("SELECT pin_hash FROM users WHERE id = ?", (user_id,)).fetchone()
        return bool(row and row["pin_hash"])

    def personal_token_valid(self, user_id):
        query = parse_qs(urlparse(self.path).query)
        token = self.headers.get("X-Personal-Token", "") or (query.get("personalToken") or [""])[0]
        if not token:
            return False
        now = time.time()
        digest = personal_token_digest(token)
        expiry = PERSONAL_PIN_TOKENS.get((user_id, digest), 0)
        if expiry <= now:
            PERSONAL_PIN_TOKENS.pop((user_id, digest), None)
            return False
        PERSONAL_PIN_TOKENS[(user_id, digest)] = now + PIN_TOKEN_SECONDS
        return True

    def require_personal_access(self, user, context):
        if context != "personal" or not self.personal_pin_is_set(user["id"]):
            return True
        if self.personal_token_valid(user["id"]):
            return True
        self.send_json({"error": "Personal PIN required", "pinRequired": True}, HTTPStatus.LOCKED)
        return False

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
        if path == "/favicon.ico":
            self.serve_static("favicon.ico")
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
        if path == "/api/personal-pin":
            user = self.require_user()
            if user:
                self.personal_pin_status(user)
            return
        if path == "/api/journal":
            user = self.require_user()
            if not user:
                return
            self.get_journal()
            return
        if path == "/api/note":
            user = self.require_user()
            if user:
                self.get_note()
            return
        if path == "/api/search":
            user = self.require_user()
            if user:
                self.search_notes()
            return
        if path == "/api/export":
            user = self.require_user()
            if user:
                self.export_notes()
            return
        if path == "/api/backup":
            user = self.require_user()
            if user:
                self.download_backup()
            return
        if path == "/api/backups":
            user = self.require_user()
            if user:
                self.list_backups()
            return
        self.serve_index()

    def do_HEAD(self):
        path = urlparse(self.path).path
        if path == "/health":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            return
        if path == "/favicon.ico":
            self.send_static_head("favicon.ico")
            return
        if path.startswith("/static/"):
            self.send_static_head(path.removeprefix("/static/"))
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
            elif path == "/api/personal-pin":
                user = self.require_user()
                if user:
                    self.set_personal_pin(user)
            elif path == "/api/personal-pin/unlock":
                user = self.require_user()
                if user:
                    self.unlock_personal_pin(user)
            elif path == "/api/assets":
                user = self.require_user()
                if user:
                    self.upload_asset()
            elif path == "/api/backups":
                user = self.require_user()
                if user:
                    self.create_server_backup()
            elif path == "/api/restore":
                user = self.require_user()
                if user:
                    self.restore_backup()
            elif path == "/api/import/preview":
                user = self.require_user()
                if user:
                    self.preview_import(user)
            elif path == "/api/import/commit":
                user = self.require_user()
                if user:
                    self.commit_import(user)
            else:
                self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except (json.JSONDecodeError, ValueError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def serve_index(self):
        content = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.send_text(content, content_type="text/html; charset=utf-8")

    def serve_static(self, name):
        path = self.static_path(name)
        if not path:
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_static_head(self, name):
        path = self.static_path(name)
        if not path:
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.end_headers()

    def static_path(self, name):
        safe_name = Path(name).name
        path = STATIC_DIR / safe_name
        if safe_name != name or not path.exists() or not path.is_file():
            return None
        return path

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
        rate_key = f"{self.client_address[0]}:{username}"
        attempts = LOGIN_ATTEMPTS.get(rate_key, [])
        now = time.time()
        attempts = [stamp for stamp in attempts if now - stamp < 900]
        if len(attempts) >= 5:
            self.send_json({"error": "Too many failed attempts. Try again later."}, HTTPStatus.TOO_MANY_REQUESTS)
            return
        with db() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if not row or not verify_password(password, row["salt"], row["password_hash"]):
                attempts.append(now)
                LOGIN_ATTEMPTS[rate_key] = attempts
                self.send_json({"error": "Invalid username or password"}, HTTPStatus.UNAUTHORIZED)
                return
            LOGIN_ATTEMPTS.pop(rate_key, None)
            conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (int(time.time()),))
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
        query = parse_qs(urlparse(self.path).query)
        context = normalize_context((query.get("context") or ["personal"])[0])
        user = self.current_user()
        if user and not self.require_personal_access(user, context):
            return
        with db() as conn:
            today = conn.execute(
                "SELECT * FROM notes WHERE note_date = ? AND context = ?",
                (current_day, context),
            ).fetchone()
            older = conn.execute(
                """
                SELECT note_date, context, content, tags, updated_at
                FROM notes
                WHERE note_date < ? AND context = ? AND length(trim(content)) > 0
                ORDER BY note_date DESC
                LIMIT 500
                """,
                (current_day, context),
            ).fetchall()
        older_notes = []
        for index, row in enumerate(older):
            expanded = index < 3
            older_notes.append(
                {
                    "note_date": row["note_date"],
                    "context": row["context"],
                    "content": row["content"] if expanded else "",
                    "tags": row["tags"] if expanded else "",
                    "updated_at": row["updated_at"],
                    "word_count": word_count(row["content"] or ""),
                    "collapsed": not expanded,
                }
            )
        self.send_json(
            {
                "appName": APP_NAME,
                "today": {
                    "date": current_day,
                    "context": context,
                    "content": today["content"] if today else "",
                    "tags": today["tags"] if today else "",
                    "updatedAt": today["updated_at"] if today else None,
                },
                "older": older_notes,
            }
        )

    def save_today(self):
        payload = self.read_json()
        content = sanitize_note(str(payload.get("content", "")))
        tags = normalize_tags(payload.get("tags", ""))
        context = normalize_context(payload.get("context", "personal"))
        user = self.current_user()
        if user and not self.require_personal_access(user, context):
            return
        current_day = today_key()
        updated_at = utc_now().isoformat()
        with db() as conn:
            conn.execute(
                """
                INSERT INTO notes (note_date, context, content, tags, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(note_date, context) DO UPDATE SET
                    content = excluded.content,
                    tags = excluded.tags,
                    updated_at = excluded.updated_at
                """,
                (current_day, context, content, tags, updated_at),
            )
        self.send_json({"ok": True, "date": current_day, "updatedAt": updated_at})

    def get_note(self):
        query = parse_qs(urlparse(self.path).query)
        note_date = validate_date((query.get("date") or [""])[0])
        context = normalize_context((query.get("context") or ["personal"])[0])
        user = self.current_user()
        if user and not self.require_personal_access(user, context):
            return
        with db() as conn:
            row = conn.execute(
                "SELECT note_date, context, content, tags, updated_at FROM notes WHERE note_date = ? AND context = ?",
                (note_date, context),
            ).fetchone()
        self.send_json({"note": dict(row) if row else {"note_date": note_date, "context": context, "content": "", "tags": "", "updated_at": None}})

    def search_notes(self):
        query = parse_qs(urlparse(self.path).query)
        term = str((query.get("q") or [""])[0]).strip()
        tag = normalize_tags((query.get("tag") or [""])[0])
        context = normalize_context((query.get("context") or ["personal"])[0])
        user = self.current_user()
        if user and not self.require_personal_access(user, context):
            return
        if len(term) < 2 and not tag:
            self.send_json({"results": []})
            return
        with db() as conn:
            rows = conn.execute(
                """
                SELECT note_date, context, content, tags, updated_at
                FROM notes
                WHERE context = ? AND (length(trim(content)) > 0 OR length(trim(tags)) > 0)
                ORDER BY note_date DESC
                LIMIT 500
                """,
                (context,),
            ).fetchall()
        results = []
        needle = term.lower()
        for row in rows:
            text = note_text(row["content"] or "")
            tags = row["tags"] or ""
            if tag and tag not in [item.strip() for item in tags.split(",")]:
                continue
            if needle and needle not in text.lower() and needle not in tags.lower():
                continue
            snippet_source = text or tags
            index = snippet_source.lower().find(needle) if needle else 0
            start = max(index - 70, 0)
            snippet = snippet_source[start : start + 180].strip()
            results.append(
                {
                    "note_date": row["note_date"],
                    "context": row["context"],
                    "tags": tags,
                    "snippet": snippet,
                    "updated_at": row["updated_at"],
                }
            )
            if len(results) >= 50:
                break
        self.send_json({"results": results})

    def export_notes(self):
        query = parse_qs(urlparse(self.path).query)
        start = (query.get("from") or [""])[0]
        end = (query.get("to") or [""])[0]
        file_format = (query.get("format") or ["html"])[0].lower()
        context = normalize_context((query.get("context") or ["personal"])[0])
        user = self.current_user()
        if user and not self.require_personal_access(user, context):
            return
        if start:
            validate_date(start)
        if end:
            validate_date(end)
        if file_format not in {"html", "md"}:
            raise ValueError("Unsupported export format")
        clauses = ["context = ?", "length(trim(content)) > 0"]
        params = [context]
        if start:
            clauses.append("note_date >= ?")
            params.append(start)
        if end:
            clauses.append("note_date <= ?")
            params.append(end)
        with db() as conn:
            rows = conn.execute(
                f"""
                SELECT note_date, context, content, tags, updated_at
                FROM notes
                WHERE {' AND '.join(clauses)}
                ORDER BY note_date ASC
                """,
                params,
            ).fetchall()
        stamp = datetime.now().strftime("%Y%m%d")
        if file_format == "md":
            body = "\n\n".join(note_to_markdown(row) for row in rows).encode("utf-8")
            filename = f"the-journal-export-{stamp}.md"
            content_type = "text/markdown; charset=utf-8"
        else:
            sections = []
            for row in rows:
                tags = html.escape(row["tags"] or "")
                tag_html = f"<p><strong>Tags:</strong> {tags}</p>" if tags else ""
                title = f"{row['note_date']} - {row['context'].title()}"
                sections.append(f"<article><h1>{html.escape(title)}</h1>{tag_html}{row['content'] or ''}</article>")
            body = (
                "<!doctype html><html><head><meta charset='utf-8'><title>The Journal Export</title>"
                "<style>body{font-family:system-ui;max-width:860px;margin:48px auto;line-height:1.6}"
                "article{border-bottom:1px solid #ddd;padding:0 0 32px;margin:0 0 32px}"
                "img{max-width:100%;height:auto}</style></head><body>"
                + "".join(sections)
                + "</body></html>"
            ).encode("utf-8")
            filename = f"the-journal-export-{stamp}.html"
            content_type = "text/html; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def download_backup(self):
        user = self.current_user()
        if user and not self.require_personal_access(user, "personal"):
            return
        body = create_backup_bytes()
        filename = f"the-journal-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
        self.send_download(body, filename, "application/zip")

    def list_backups(self):
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backups = []
        for path in sorted(BACKUP_DIR.glob("the-journal-backup-*.zip"), reverse=True):
            stat = path.stat()
            backups.append({"name": path.name, "size": stat.st_size, "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat()})
        self.send_json({"backups": backups[:14]})

    def create_server_backup(self):
        user = self.current_user()
        if user and not self.require_personal_access(user, "personal"):
            return
        path = write_server_backup()
        self.send_json({"ok": True, "name": path.name, "size": path.stat().st_size})

    def restore_backup(self):
        user = self.current_user()
        if user and not self.require_personal_access(user, "personal"):
            return
        payload = self.read_json()
        data_url = str(payload.get("dataUrl", ""))
        match = re.match(r"^data:(?:application/(?:zip|x-zip-compressed)|application/octet-stream);base64,(.+)$", data_url, re.DOTALL)
        if not match:
            self.send_json({"error": "Upload a The Journal .zip backup"}, HTTPStatus.BAD_REQUEST)
            return
        raw = base64.b64decode(match.group(1), validate=True)
        if len(raw) > MAX_BACKUP_BYTES:
            self.send_json({"error": "Backup must be 100 MB or smaller"}, HTTPStatus.BAD_REQUEST)
            return
        result = import_backup(raw)
        self.send_json({"ok": True, **result})

    def preview_import(self, user):
        payload = self.read_json()
        context = normalize_context(payload.get("context", "personal"))
        if not self.require_personal_access(user, context):
            return
        date_order = str(payload.get("dateOrder", "mdy")).lower()
        if date_order not in {"mdy", "dmy"}:
            raise ValueError("Invalid date preference")
        entries = parse_import_entries(payload.get("text", ""), context, date_order)
        add_import_conflicts(entries)
        warnings = sum(1 for entry in entries if entry.get("warning"))
        importable = sum(1 for entry in entries if entry.get("date") and entry.get("text"))
        self.send_json({"entries": entries, "warnings": warnings, "importable": importable, "limit": MAX_IMPORT_ENTRIES})

    def commit_import(self, user):
        payload = self.read_json()
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list):
            raise ValueError("Import entries are required")
        if len(raw_entries) > MAX_IMPORT_ENTRIES:
            raise ValueError("Too many import entries")
        actions = {"append", "replace", "skip"}
        entries = []
        needs_personal = False
        for raw in raw_entries:
            if not isinstance(raw, dict):
                raise ValueError("Invalid import entry")
            action = str(raw.get("action", "skip")).lower()
            if action not in actions:
                raise ValueError("Invalid import action")
            if action == "skip":
                continue
            note_date = validate_date(raw.get("date"))
            context = normalize_context(raw.get("context", "personal"))
            if context == "personal":
                needs_personal = True
            text = str(raw.get("text", "")).strip()
            if not text:
                continue
            entries.append({"date": note_date, "context": context, "text": text, "action": action})
        if needs_personal and not self.require_personal_access(user, "personal"):
            return
        if not entries:
            self.send_json({"ok": True, "imported": 0, "appended": 0, "replaced": 0, "skipped": len(raw_entries), "backup": None})
            return
        backup = write_server_backup()
        updated_at = utc_now().isoformat()
        imported = appended = replaced = 0
        with db() as conn:
            for entry in entries:
                existing = conn.execute(
                    "SELECT content FROM notes WHERE note_date = ? AND context = ?",
                    (entry["date"], entry["context"]),
                ).fetchone()
                imported_html = sanitize_note(entry["text"])
                if entry["action"] == "append" and existing and (existing["content"] or "").strip():
                    divider = "<hr><p><strong>Imported note</strong></p>"
                    content = f"{existing['content']}{divider}{imported_html}"
                    appended += 1
                else:
                    content = imported_html
                    replaced += 1
                conn.execute(
                    """
                    INSERT INTO notes (note_date, context, content, tags, updated_at)
                    VALUES (?, ?, ?, '', ?)
                    ON CONFLICT(note_date, context) DO UPDATE SET
                        content = excluded.content,
                        updated_at = excluded.updated_at
                    """,
                    (entry["date"], entry["context"], content, updated_at),
                )
                imported += 1
        self.send_json(
            {
                "ok": True,
                "imported": imported,
                "appended": appended,
                "replaced": replaced,
                "skipped": max(len(raw_entries) - imported, 0),
                "backup": backup.name,
            }
        )

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

    def personal_pin_status(self, user):
        self.send_json({"isSet": self.personal_pin_is_set(user["id"])})

    def set_personal_pin(self, user):
        payload = self.read_json()
        pin = validate_pin(payload.get("pin"))
        salt, digest = password_hash(pin)
        with db() as conn:
            conn.execute(
                "UPDATE users SET pin_salt = ?, pin_hash = ? WHERE id = ?",
                (salt, digest, user["id"]),
            )
        token = secrets.token_urlsafe(24)
        PERSONAL_PIN_TOKENS[(user["id"], personal_token_digest(token))] = time.time() + PIN_TOKEN_SECONDS
        self.send_json({"ok": True, "token": token})

    def unlock_personal_pin(self, user):
        payload = self.read_json()
        pin = validate_pin(payload.get("pin"))
        rate_key = f"{self.client_address[0]}:{user['id']}:pin"
        now = time.time()
        attempts = [stamp for stamp in PIN_ATTEMPTS.get(rate_key, []) if now - stamp < 900]
        if len(attempts) >= 8:
            self.send_json({"error": "Too many PIN attempts. Try again later."}, HTTPStatus.TOO_MANY_REQUESTS)
            return
        with db() as conn:
            row = conn.execute("SELECT pin_salt, pin_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
        if not row or not row["pin_hash"]:
            self.send_json({"error": "Personal PIN is not set"}, HTTPStatus.BAD_REQUEST)
            return
        if not verify_password(pin, row["pin_salt"], row["pin_hash"]):
            attempts.append(now)
            PIN_ATTEMPTS[rate_key] = attempts
            self.send_json({"error": "Invalid PIN"}, HTTPStatus.UNAUTHORIZED)
            return
        PIN_ATTEMPTS.pop(rate_key, None)
        token = secrets.token_urlsafe(24)
        PERSONAL_PIN_TOKENS[(user["id"], personal_token_digest(token))] = time.time() + PIN_TOKEN_SECONDS
        self.send_json({"ok": True, "token": token})

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
    try:
        write_server_backup()
    except Exception as exc:
        print(f"startup backup failed: {exc}")
    start_backup_scheduler()
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), JournalHandler)
    print(f"{APP_NAME} listening on :{port}")
    server.serve_forever()
