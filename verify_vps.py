import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".tmp_vendor"))

import paramiko


HOST = os.environ.get("OPENSAILS_SSH_HOST", "opensails.ca")
USER = os.environ.get("OPENSAILS_SSH_USER", "root")
PASSWORD = os.environ["OPENSAILS_SSH_PASSWORD"]
APP_PASSWORD = os.environ["THEJOURNAL_TEST_PASSWORD"]


def run(client, command):
    stdin, stdout, stderr = client.exec_command(command, timeout=60)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    code = stdout.channel.recv_exit_status()
    print(f"$ {command}\nexit={code}")
    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)
    return code


client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASSWORD, timeout=20, banner_timeout=20)

commands = [
    "curl -kIs --resolve thejournal.opensails.ca:443:127.0.0.1 https://thejournal.opensails.ca/ | sed -n '1,12p'",
    (
        "tmp=$(mktemp) && "
        "curl -ksS --resolve thejournal.opensails.ca:443:127.0.0.1 "
        "-c $tmp -H 'Content-Type: application/json' "
        f"-d '{{\"username\":\"admin\",\"password\":\"{APP_PASSWORD}\"}}' "
        "https://thejournal.opensails.ca/api/login >/dev/null && "
        "curl -ksS --resolve thejournal.opensails.ca:443:127.0.0.1 "
        "-b $tmp https://thejournal.opensails.ca/api/journal | "
        "python3 -c \"import sys,json; d=json.load(sys.stdin); print(d['today']['date'], len(d['today']['content']))\"; "
        "rm -f $tmp"
    ),
    "getent hosts thejournal.opensails.ca || true",
]

failed = False
for command in commands:
    failed = run(client, command) != 0 or failed

client.close()
sys.exit(1 if failed else 0)
