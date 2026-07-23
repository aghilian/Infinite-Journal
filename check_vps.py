import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".tmp_vendor"))

import paramiko


HOST = os.environ.get("OPENSAILS_SSH_HOST", "opensails.ca")
USER = os.environ.get("OPENSAILS_SSH_USER", "root")
PASSWORD = os.environ["OPENSAILS_SSH_PASSWORD"]


def run(client, command):
    stdin, stdout, stderr = client.exec_command(command, timeout=30)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    code = stdout.channel.recv_exit_status()
    print(f"$ {command}\nexit={code}")
    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)


client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASSWORD, timeout=20, banner_timeout=20)

for command in [
    "hostname",
    "hostname -I",
    "docker ps --format '{{.Names}} {{.Ports}}'",
    "ss -ltnp | grep -E ':80|:443|:8097' || true",
    "systemctl is-active nginx || true",
    "systemctl status nginx --no-pager -l | sed -n '1,80p' || true",
    "docker compose version || docker-compose version",
    "sed -n '1,240p' /etc/nginx/sites-available/opensails.ca 2>/dev/null || true",
    "sed -n '1,240p' /etc/nginx/sites-available/default 2>/dev/null || true",
    "docker inspect traefik-traefik-1 --format '{{json .Config.Labels}}' 2>/dev/null || true",
    "sed -n '1,240p' /docker/traefik/docker-compose.yml 2>/dev/null || true",
    "docker network ls --format '{{.Name}}'",
    "docker inspect opensails-opensails-1 --format '{{json .Config.Labels}}' 2>/dev/null || true",
    "docker inspect opensails-opensails-1 --format '{{json .NetworkSettings.Networks}}' 2>/dev/null || true",
    "docker inspect thejournal --format '{{json .Config.Labels}}' 2>/dev/null || true",
    "curl -kIs --resolve thejournal.opensails.ca:443:127.0.0.1 https://thejournal.opensails.ca/ | sed -n '1,20p' || true",
    "getent hosts thejournal.opensails.ca || true",
    "find /root -maxdepth 3 -name docker-compose.yml -o -name compose.yml 2>/dev/null",
    "ls -la /opt",
    "ls -la /etc/nginx/sites-enabled 2>/dev/null || true",
    "ls -la /etc/caddy 2>/dev/null || true",
]:
    run(client, command)

client.close()
