import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".tmp_vendor"))

import paramiko


HOST = os.environ.get("OPENSAILS_SSH_HOST", "opensails.ca")
USER = os.environ.get("OPENSAILS_SSH_USER", "root")
PASSWORD = os.environ["OPENSAILS_SSH_PASSWORD"]


COMMANDS = [
    "curl -kIs --resolve thejournal.opensails.ca:443:127.0.0.1 https://thejournal.opensails.ca/ | sed -n '1,20p'",
    "docker logs --tail 80 thejournal",
    "docker exec thejournal python -c 'from app import db; c=db(); print(tuple(c.execute(\"select count(*), min(context), max(context) from notes\").fetchone()))'",
]


client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASSWORD, timeout=20, banner_timeout=20)
failed = False
for command in COMMANDS:
    stdin, stdout, stderr = client.exec_command(command, timeout=60)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    code = stdout.channel.recv_exit_status()
    print(f"$ {command}\nexit={code}")
    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)
    failed = failed or code != 0
client.close()
sys.exit(1 if failed else 0)
