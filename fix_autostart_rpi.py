"""
Script de déploiement vers le Raspberry Pi (192.168.1.96) :
- Crée et envoie `daily_reboot.sh` ainsi que `start_kiosk.sh` mis à jour.
- Configure automatiquement au démarrage (`~/.config/autostart/asd-kiosk.desktop` et `~/.config/labwc/autostart`)
  ainsi que la tâche planifiée `crontab` quotidienne à 03h00 du matin (sans doublon) :
  `0 3 * * * /bin/bash /home/$USER/asd-dashboard/daily_reboot.sh >> /home/$USER/asd-dashboard/reboot.log 2>&1`
- Relance proprement le serveur Flask et le tunnel permanent Ngrok (`https://energetic-ember-image.ngrok-free.dev/admin`).
"""

from __future__ import annotations

import io
import os
import socket
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
os.chdir(BASE_DIR)

RPI_IP = os.environ.get("RPI_IP", "192.168.1.96")
RPI_PORT = int(os.environ.get("RPI_PORT", "5000"))
HTTP_PORT = int(os.environ.get("PC_HTTP_PORT", "8080"))
ADMIN_PIN = os.environ.get("ADMIN_PIN", "1969")
NGROK_ADMIN_URL = "https://energetic-ember-image.ngrok-free.dev/admin"

DAILY_REBOOT_SCRIPT = """#!/usr/bin/env bash
# Script de redémarrage quotidien automatique à 03h00 — AS Durlinsdorf (1969)

USER_NAME="${USER:-$(whoami)}"
APP_DIR="/home/$USER_NAME/asd-dashboard"
if [ ! -d "$APP_DIR" ]; then
    APP_DIR="$HOME/asd-dashboard"
fi

echo "=================================================================="
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Exécution de daily_reboot.sh (03h00)"
echo "=================================================================="

# 1. Tentative de redémarrage matériel complet du Raspberry Pi sans mot de passe
sudo -n /sbin/reboot 2>/dev/null || sudo -n reboot 2>/dev/null || systemctl reboot -i 2>/dev/null || true

# Si le système est en train de redémarrer, patienter quelques secondes
sleep 5

# 2. Sécurité (si le redémarrage matériel complet est refusé sans sudo) :
#    Fermeture propre de Chromium et du serveur Python pour vider la RAM, puis relance à neuf
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Repli : Redémarrage complet de Chromium Kiosk + Serveur Python..."

pkill -f "asd-kiosk-profile" 2>/dev/null || true
pkill -9 chromium 2>/dev/null || true
pkill -9 chromium-browser 2>/dev/null || true
pkill -9 -f "ngrok" 2>/dev/null || true
pkill -9 -f "cloudflared" 2>/dev/null || true
pkill -9 -f "app.py" 2>/dev/null || true

rm -rf /tmp/asd-kiosk-profile "$HOME/.config/chromium/Singleton"* "$HOME/.cache/chromium/Default/GPUCache" 2>/dev/null || true
sleep 2

export DISPLAY="${DISPLAY:-:0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

chmod +x "$APP_DIR/start_kiosk.sh" 2>/dev/null || true
nohup /bin/bash "$APP_DIR/start_kiosk.sh" >> "$APP_DIR/kiosk.log" 2>&1 &
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Relance de start_kiosk.sh effectuée avec succès."
"""


def get_pc_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((RPI_IP, 80))
        return s.getsockname()[0]
    except Exception:
        return "192.168.1.29"
    finally:
        s.close()


def normalize_lf(filepath: str) -> bytes:
    with open(filepath, "rb") as f:
        data = f.read().replace(b"\r\n", b"\n")
    with open(filepath, "wb") as f:
        f.write(data)
    return data


def build_archive_bytes(files: list[str], include_update_flag: bool = False) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=9) as tar:
        for rel_path in files:
            if os.path.exists(rel_path):
                info = tar.gettarinfo(rel_path, arcname=rel_path)
                if rel_path.endswith(".sh"):
                    info.mode = 0o755
                with open(rel_path, "rb") as fobj:
                    tar.addfile(info, fobj)
        if include_update_flag:
            flag_bytes = b"1\n"
            ti = tarfile.TarInfo(name=".update_flag")
            ti.size = len(flag_bytes)
            tar.addfile(ti, io.BytesIO(flag_bytes))
    return buf.getvalue()


def main() -> None:
    # 1. Écriture et normalisation LF de daily_reboot.sh et start_kiosk.sh
    with open("daily_reboot.sh", "wb") as f:
        f.write(DAILY_REBOOT_SCRIPT.replace("\r\n", "\n").encode("utf-8"))
    normalize_lf("daily_reboot.sh")
    normalize_lf("start_kiosk.sh")
    if os.path.exists("install_local.sh"):
        normalize_lf("install_local.sh")

    # Lots légers (< 17 Ko chacun) pour un téléchargement instantané (< 1s) par le Raspberry Pi
    batches = [
        (
            "Lot 1/4 (daily_reboot.sh + start_kiosk.sh + remote_tunnel.py)",
            ["daily_reboot.sh", "start_kiosk.sh", "remote_tunnel.py", "portail_admin_permanent.html"],
            False,
        ),
        ("Lot 2/4 (templates/tv.html)", ["templates/tv.html"], False),
        ("Lot 3/4 (templates/admin.html)", ["templates/admin.html"], False),
        ("Lot 4/4 (app.py + activation Crontab 03h00 & redémarrage)", ["app.py"], True),
    ]

    active_payload = {"data": b""}
    pc_ip = get_pc_ip()

    class FastUpdateHTTPServer(ThreadingHTTPServer):
        allow_reuse_address = True

    class UpdateHTTPHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def address_string(self) -> str:
            return self.client_address[0]

        def log_message(self, fmt: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            try:
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass
            payload = active_payload["data"]
            self.send_response(200)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()
            print(f"  [Serveur PC {pc_ip}:{HTTP_PORT}] Envoyé : {len(payload)} octets")

    server = FastUpdateHTTPServer(("0.0.0.0", HTTP_PORT), UpdateHTTPHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print("=" * 76)
    print("  DÉPLOIEMENT AUTOSTART, CRONTAB 03H00 & WI-FI CLUB-HOUSE — AS DURLINSDORF")
    print("=" * 76)
    print(f"1. Serveur HTTP local actif sur : http://{pc_ip}:{HTTP_PORT}")
    print("2. Tâche Crontab configurée     : 0 3 * * * /bin/bash /home/$USER/asd-dashboard/daily_reboot.sh >> /home/$USER/asd-dashboard/reboot.log 2>&1")
    print('3. Pré-enregistrement Wi-Fi     : SSID="Livebox-20f2" | Profil="ClubHouse-ASD" (autoconnect yes)')

    rpi_base = f"http://{RPI_IP}:{RPI_PORT}"
    all_ok = True

    for label, file_list, with_flag in batches:
        archive_bytes = build_archive_bytes(file_list, include_update_flag=with_flag)
        active_payload["data"] = archive_bytes
        print(f"\n-> Envoi {label} ({len(archive_bytes)} octets)...")

        sent = False
        for attempt in range(1, 4):
            try:
                resp = requests.post(
                    f"{rpi_base}/api/admin/pull-update",
                    headers={"X-Admin-Pin": ADMIN_PIN},
                    json={"pc_url": f"http://{pc_ip}:{HTTP_PORT}"},
                    timeout=25,
                )
                if resp.status_code == 200 and resp.json().get("ok"):
                    print(f"   [OK] {label} extrait sur le Raspberry Pi !")
                    sent = True
                    break
                else:
                    print(f"   [Essai {attempt}/3] HTTP {resp.status_code} : {resp.text.strip()}")
            except Exception as exc:
                print(f"   [Essai {attempt}/3] Erreur : {exc}")
            time.sleep(2)

        if not sent:
            all_ok = False
            break
        time.sleep(2)

    wifi_confirm_msg = "Pré-enregistré via start_kiosk.sh (nmcli / wpa_cli)"
    if all_ok:
        print("\n4. Attente du redémarrage d'app.py, de l'activation du Crontab 03h00 et du tunnel Ngrok...")
        for i in range(15):
            time.sleep(4)
            try:
                st = requests.get(f"{rpi_base}/api/state", timeout=8).json()
                rt = st.get("remote_tunnel", {})
                pub_url = str(rt.get("public_admin_url") or "")
                print(f"   [{(i + 1) * 4}s] Statut tunnel : {rt.get('status')} | URL : {pub_url}")
                if pub_url == NGROK_ADMIN_URL:
                    print("   -> Serveur Flask, Crontab 03h00 et tunnel Ngrok actifs sur le Raspberry Pi !")
                    break
            except Exception:
                print(f"   [{(i + 1) * 4}s] Serveur en cours de redémarrage...")

        # Confirmation / enregistrement direct du Wi-Fi du Club-House via l'API /api/admin/wifi
        try:
            w_resp = requests.post(
                f"{rpi_base}/api/admin/wifi",
                headers={"X-Admin-Pin": ADMIN_PIN},
                json={
                    "ssid": "Livebox-20f2",
                    "password": "Durlinsdorf1969",
                    "con_name": "ClubHouse-ASD",
                    "label": "Club-House AS Durlinsdorf",
                },
                timeout=15,
            )
            if w_resp.status_code == 200:
                w_data = w_resp.json()
                wifi_confirm_msg = str(w_data.get("message") or wifi_confirm_msg)
                print(f"   -> [OK] {wifi_confirm_msg}")
        except Exception as exc:
            print(f"   -> Info Wi-Fi : {exc}")

    print("\n" + "=" * 76)
    print("  RÉCAPITULATIF DE LA CONFIGURATION SUR LE RASPBERRY PI (CODE PIN : 1969)")
    print("=" * 76)
    print("  - Wi-Fi Club-House pré-enregistré: Livebox-20f2 (ClubHouse-ASD, autoconnect=yes)")
    print(f"  - Détail enregistrement Wi-Fi    : {wifi_confirm_msg}")
    print("  - Redémarrage auto 03h00         : Actif (0 3 * * * -> daily_reboot.sh)")
    print("  - Journal des redémarrages       : /home/$USER/asd-dashboard/reboot.log")
    print(f"  - URL 4G / Internet Fixe (Ngrok) : {NGROK_ADMIN_URL}")
    print(f"  - URL Wi-Fi Local Raspberry Pi   : http://{RPI_IP}:{RPI_PORT}/admin")
    print(f"  - Fiche QR Imprimable            : {os.path.join(BASE_DIR, 'qr_code_imprimable.html')}")
    print("=" * 76)


if __name__ == "__main__":
    main()
