import os
import shlex
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".tmp_vendor"))

import paramiko


HOST = os.environ.get("OPENSAILS_SSH_HOST", "opensails.ca")
USER = os.environ.get("OPENSAILS_SSH_USER", "root")
PASSWORD = os.environ["OPENSAILS_SSH_PASSWORD"]


script = """
import sqlite3
from datetime import datetime
from app import DB_PATH, utc_now

today = datetime.now().strftime("%Y-%m-%d")
conn = sqlite3.connect(DB_PATH)
conn.execute(
    "INSERT INTO notes (note_date, content, updated_at) VALUES (?, ?, ?) "
    "ON CONFLICT(note_date) DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at",
    (today, "", utc_now().isoformat()),
)
conn.commit()
conn.close()
print("today cleared")
"""


client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASSWORD, timeout=20, banner_timeout=20)
stdin, stdout, stderr = client.exec_command(
    f"docker exec thejournal python -c {shlex.quote(script)}",
    timeout=60,
)
out = stdout.read().decode("utf-8", "replace").strip()
err = stderr.read().decode("utf-8", "replace").strip()
code = stdout.channel.recv_exit_status()
client.close()
if out:
    print(out)
if err:
    print(err, file=sys.stderr)
sys.exit(code)
