import io
import os
import secrets
import sys
import tarfile
from pathlib import Path

import paramiko


HOST = os.environ.get("OPENSAILS_SSH_HOST", "opensails.ca")
USER = os.environ.get("OPENSAILS_SSH_USER", "root")
PASSWORD = os.environ["OPENSAILS_SSH_PASSWORD"]
REMOTE_DIR = "/srv/thejournal"
DOMAIN = "thejournal.opensails.ca"
ROOT = Path(__file__).parent


def run(client, command, timeout=180):
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    if code != 0:
        raise RuntimeError(f"Command failed ({code}): {command}\n{out}\n{err}")
    return out.strip()


def archive_bytes():
    buffer = io.BytesIO()
    include = [
        "app.py",
        "Dockerfile",
        "docker-compose.yml",
        "README.md",
        "static/index.html",
        "static/styles.css",
        "static/app.js",
    ]
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name in include:
            tar.add(ROOT / name, arcname=name)
    buffer.seek(0)
    return buffer


def main():
    initial_password = secrets.token_urlsafe(18)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, timeout=20, banner_timeout=20)
    sftp = client.open_sftp()

    run(client, f"mkdir -p {REMOTE_DIR}")
    remote_tar = "/tmp/thejournal-deploy.tar.gz"
    with sftp.file(remote_tar, "wb") as remote:
        remote.write(archive_bytes().read())
    run(client, f"tar -xzf {remote_tar} -C {REMOTE_DIR}")
    run(client, f"rm -f {remote_tar}")

    env_content = f"THEJOURNAL_PASSWORD={initial_password}\n"
    with sftp.file(f"{REMOTE_DIR}/.env", "w") as remote:
        remote.write(env_content)
    run(client, f"chmod 600 {REMOTE_DIR}/.env")

    run(
        client,
        "rm -f /etc/nginx/sites-enabled/thejournal.opensails.ca "
        "/etc/nginx/sites-available/thejournal.opensails.ca",
    )

    run(client, f"cd {REMOTE_DIR} && docker compose up -d --build", timeout=600)
    ps = run(client, "docker ps --filter name=thejournal --format '{{.Names}} {{.Status}} {{.Ports}}'")
    container_ip = run(
        client,
        "docker inspect thejournal --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'",
    )
    health = run(client, f"curl -fsS http://{container_ip}:8000/health")
    print(f"deployed={DOMAIN}")
    print(f"container={ps}")
    print(f"health={health}")
    print("username=admin")
    print(f"password={initial_password}")

    sftp.close()
    client.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
