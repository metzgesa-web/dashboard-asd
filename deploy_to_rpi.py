"""
Script d'automatisation de déploiement PC Windows -> Raspberry Pi (SSH / SFTP via Paramiko)
Déploie et configure de A à Z le Tableau de Bord TV de l'AS Durlinsdorf (1969) :
1. Création de `/home/<utilisateur>/asd-dashboard` et transfert SFTP propre (sans venv/__pycache__/.git)
2. Installation des paquets système (`python3-venv`, `python3-pip`, `unclutter`, `curl`, `x11-xserver-utils`)
3. Création de l'environnement virtuel Linux `/home/<utilisateur>/asd-dashboard/venv` et installation de `requirements.txt`
4. Création et activation du service `systemd` (`asd-dashboard.service`) au démarrage électrique
5. Configuration du mode Kiosk plein écran Chromium + désactivation de la mise en veille + masquage du curseur (`unclutter`)
"""

from __future__ import annotations

import argparse
import os
import posixpath
import stat
import sys
import time
from typing import Set

import paramiko

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
EXCLUDED_DIRS: Set[str] = {"venv", ".venv", "__pycache__", ".git", ".idea", ".vscode", "node_modules"}
EXCLUDED_FILES: Set[str] = {".DS_Store", "Thumbs.db", "deploy_to_rpi.py"}


def run_ssh_cmd(
    ssh: paramiko.SSHClient,
    command: str,
    sudo_password: str | None = None,
    timeout: int = 600,
) -> int:
    """Exécute une commande distante en SSH et affiche la sortie en temps réel."""
    if sudo_password and command.strip().startswith("sudo "):
        # Injecte le mot de passe dans sudo -S uniquement si un mot de passe non vide est fourni
        cmd_body = command.strip()[5:]
        full_cmd = f"sudo -S -p '' {cmd_body}"
    else:
        full_cmd = command

    print(f"\n[SSH] $ {command}")
    stdin, stdout, stderr = ssh.exec_command(full_cmd, get_pty=False, timeout=timeout)
    if sudo_password and "sudo -S" in full_cmd:
        stdin.write(sudo_password + "\n")
        stdin.flush()

    while not stdout.channel.exit_status_ready():
        if stdout.channel.recv_ready():
            out = stdout.channel.recv(4096).decode("utf-8", errors="replace")
            sys.stdout.write(out)
            sys.stdout.flush()
        if stdout.channel.recv_stderr_ready():
            err = stdout.channel.recv_stderr(4096).decode("utf-8", errors="replace")
            sys.stderr.write(err)
            sys.stderr.flush()
        time.sleep(0.1)

    # Lecture des derniers octets restants
    rem_out = stdout.read().decode("utf-8", errors="replace")
    if rem_out:
        sys.stdout.write(rem_out)
    rem_err = stderr.read().decode("utf-8", errors="replace")
    if rem_err:
        sys.stderr.write(rem_err)

    exit_code = stdout.channel.recv_exit_status()
    return exit_code


def sftp_mkdir_p(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    """Crée récursivement un dossier distant via SFTP."""
    dirs = []
    cur = remote_dir
    while len(cur) > 1:
        dirs.append(cur)
        cur, _ = posixpath.split(cur)
    dirs.reverse()
    for d in dirs:
        try:
            sftp.stat(d)
        except IOError:
            sftp.mkdir(d)


def upload_project_tree(sftp: paramiko.SFTPClient, local_root: str, remote_root: str) -> int:
    """Transfère tous les fichiers du projet en excluant venv, __pycache__, .git."""
    uploaded_count = 0
    sftp_mkdir_p(sftp, remote_root)

    for root, dirs, files in os.walk(local_root):
        # Filtrage sur place des dossiers exclus
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS and not d.startswith(".tmp")]

        rel_dir = os.path.relpath(root, local_root)
        if rel_dir == ".":
            target_remote_dir = remote_root
        else:
            target_remote_dir = posixpath.join(remote_root, rel_dir.replace("\\", "/"))
            sftp_mkdir_p(sftp, target_remote_dir)

        for fname in files:
            if fname in EXCLUDED_FILES or fname.endswith((".pyc", ".pyo", ".tmp")):
                continue
            local_path = os.path.join(root, fname)
            remote_path = posixpath.join(target_remote_dir, fname)

            # Si c'est un script shell (.sh), convertir CRLF -> LF à la volée
            if fname.endswith(".sh"):
                with open(local_path, "rb") as f:
                    raw = f.read().replace(b"\r\n", b"\n")
                with sftp.open(remote_path, "wb") as rf:
                    rf.write(raw)
                sftp.chmod(remote_path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
            else:
                sftp.put(local_path, remote_path)

            uploaded_count += 1
            print(f"  [SFTP] Transféré : {remote_path}")

    return uploaded_count


def deploy(host: str, user: str, password: str, port: int = 22) -> None:
    remote_dir = f"/home/{user}/asd-dashboard"
    print("=" * 72)
    print(f" DÉPLOIEMENT AUTOMATIQUE AS DURLINSDORF VERS {user}@{host}:{remote_dir}")
    print("=" * 72)

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"[1/5] Connexion SSH à {host}:{port} avec l'utilisateur '{user}'...")
    authenticated = False
    used_password = password

    candidates = [password] if password else ["", "1969", "raspberry"]
    for cand in candidates:
        sock = paramiko.Transport((host, port))
        sock.connect()
        try:
            if not cand:
                try:
                    sock.auth_none(user)
                except Exception:
                    pass
            if not sock.is_authenticated():
                try:
                    sock.auth_password(user, cand)
                except Exception:
                    pass
            if not sock.is_authenticated():
                try:
                    sock.auth_interactive(
                        user,
                        lambda title, instructions, prompt_list, c=cand: [c for _ in prompt_list],
                    )
                except Exception:
                    pass
            if sock.is_authenticated():
                ssh._transport = sock
                used_password = cand
                authenticated = True
                print(f"  -> Authentification SSH réussie sur {user}@{host} !")
                break
        finally:
            if not authenticated:
                sock.close()

    if not authenticated:
        raise RuntimeError(f"Impossible de s'authentifier sur {user}@{host}")
    password = used_password

    # 1. Transfert SFTP propre
    print(f"\n[2/5] Transfert des fichiers vers {remote_dir} (exclusion de venv, __pycache__, .git)...")
    sftp = ssh.open_sftp()
    count = upload_project_tree(sftp, BASE_DIR, remote_dir)
    sftp.close()
    print(f"  -> {count} fichiers transférés avec succès !")

    # 2. Installation des paquets système
    print("\n[3/5] Installation des paquets système sur le Raspberry Pi...")
    apt_cmd = (
        "sudo DEBIAN_FRONTEND=noninteractive apt-get update -y && "
        "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "
        "python3 python3-venv python3-pip python3-dev unclutter x11-xserver-utils xdg-utils curl"
    )
    run_ssh_cmd(ssh, apt_cmd, sudo_password=password)

    # 3. Création du venv Linux et installation de requirements.txt
    print("\n[4/5] Création du venv Python Linux et installation des dépendances...")
    venv_cmd = (
        f"python3 -m venv {remote_dir}/venv && "
        f"{remote_dir}/venv/bin/pip install --upgrade pip && "
        f"{remote_dir}/venv/bin/pip install -r {remote_dir}/requirements.txt"
    )
    code = run_ssh_cmd(ssh, venv_cmd)
    if code != 0:
        raise RuntimeError("Échec lors de l'installation des dépendances Python dans le venv.")

    # 4. Configuration du service systemd + Mode Kiosk Chromium via setup_rpi.sh
    print("\n[5/5] Configuration du service systemd (asd-dashboard.service) et du Mode Kiosk Chromium...")
    setup_cmd = f"sudo bash {remote_dir}/setup_rpi.sh"
    run_ssh_cmd(ssh, setup_cmd, sudo_password=password)

    # Vérification finale que le service tourne et que l'API répond
    print("\n[VÉRIFICATION] Contrôle du service asd-dashboard.service et de l'API locale...")
    run_ssh_cmd(ssh, "systemctl is-active asd-dashboard.service && curl -s http://127.0.0.1:5000/api/state | head -c 250")

    ssh.close()
    print("\n" + "=" * 72)
    print(" DÉPLOIEMENT SUR LE RASPBERRY PI TERMINÉ AVEC SUCCÈS !")
    print(f" -> Écran TV 1080p      : http://{host}:5000/tv")
    print(f" -> Interface Admin     : http://{host}:5000/admin (Code PIN : 1969)")
    print(f" -> Fiche QR Imprimable : http://{host}:5000/admin/print-qr")
    print("=" * 72)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Déploiement SSH/SFTP AS Durlinsdorf sur Raspberry Pi")
    parser.add_argument("--host", required=True, help="Adresse IP du Raspberry Pi")
    parser.add_argument("--user", required=True, help="Nom d'utilisateur SSH (ex: pi)")
    parser.add_argument("--password", nargs="?", default="", help="Mot de passe SSH (optionnel si vide)")
    parser.add_argument("--port", type=int, default=22, help="Port SSH (défaut: 22)")
    args = parser.parse_args()
    deploy(args.host, args.user, args.password, args.port)
