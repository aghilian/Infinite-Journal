import os
import shlex
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".tmp_vendor"))

import paramiko


HOST = os.environ.get("OPENSAILS_SSH_HOST", "opensails.ca")
USER = os.environ.get("OPENSAILS_SSH_USER", "root")
PASSWORD = os.environ["OPENSAILS_SSH_PASSWORD"]
NEW_PASSWORD = os.environ["THEJOURNAL_NEW_PASSWORD"]


def run(client, command):
    stdin, stdout, stderr = client.exec_command(command, timeout=60)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    code = stdout.channel.recv_exit_status()
    if code != 0:
        raise RuntimeError(f"Command failed ({code}): {command}\n{out}\n{err}")
    return out


script = """
import os
import sqlite3
from app import DB_PATH, password_hash

new_password = os.environ["THEJOURNAL_NEW_PASSWORD"]
salt, digest = password_hash(new_password)
conn = sqlite3.connect(DB_PATH)
conn.execute(
    "UPDATE users SET salt = ?, password_hash = ? WHERE username = ?",
    (salt, digest, "admin"),
)
conn.execute("DELETE FROM sessions")
conn.commit()
conn.close()
print("admin password reset")
"""

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASSWORD, timeout=20, banner_timeout=20)

quoted_password = shlex.quote(NEW_PASSWORD)
quoted_script = shlex.quote(script)
command = (
    f"cd /srv/thejournal && printf '%s\\n' THEJOURNAL_PASSWORD={quoted_password} > .env "
    f"&& chmod 600 .env "
    f"&& docker exec -e THEJOURNAL_NEW_PASSWORD={quoted_password} thejournal "
    f"python -c {quoted_script}"
)
print(run(client, command))

client.close()
