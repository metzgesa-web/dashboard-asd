"""
Module de Tunnel HTTPS Permanent Ngrok (Domaine Statique Fixe)
pour l'AS Durlinsdorf (Depuis 1969).

Fonctionnalités 100 % en espace utilisateur (AUCUN `sudo` requis) :
1. Supprime définitivement tout appel à `ntfy.sh` et `trycloudflare.com` (aucune notification envoyée).
2. Détecte automatiquement l'OS et l'architecture matérielle sur le Raspberry Pi (`aarch64`/`arm64`, `armv7l`/`arm`)
   et télécharge si besoin l'exécutable officiel `ngrok` dans `./bin/ngrok`.
3. Configure le token d'authentification avec :
   `./bin/ngrok config add-authtoken 3LHyjPCsQxTOnpXl90k0RVUwKoh_7fkbVKKdHKUrjGR9ZQ88G`
4. Lance en arrière-plan le tunnel sur le domaine statique :
   `./bin/ngrok http --domain=energetic-ember-image.ngrok-free.dev 5000 --log=stdout`
5. Vérifie toutes les 20 secondes l'état de la connexion Internet et du processus Ngrok, et relance
   automatiquement la commande si la connexion Internet a coupé puis est revenue.
"""

from __future__ import annotations

import datetime
import io
import logging
import os
import platform
import socket
import stat
import struct
import subprocess
import tarfile
import threading
import time
import zipfile
from typing import Any, Callable, Dict, Optional

import requests
import urllib3
import urllib3.util.connection as urllib3_cn

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

logger = logging.getLogger("ASD_Remote_Tunnel")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
BIN_DIR = os.path.join(BASE_DIR, "bin")
TUNNEL_LOG_PATH = os.path.join(BASE_DIR, "tunnel.log")

NGROK_AUTHTOKEN = "3LHyjPCsQxTOnpXl90k0RVUwKoh_7fkbVKKdHKUrjGR9ZQ88G"
NGROK_STATIC_DOMAIN = "energetic-ember-image.ngrok-free.dev"
NGROK_PUBLIC_BASE_URL = f"https://{NGROK_STATIC_DOMAIN}"
NGROK_PUBLIC_ADMIN_URL = f"https://{NGROK_STATIC_DOMAIN}/admin"

CHECK_INTERVAL_SECONDS = 20


def get_ngrok_download_info() -> tuple[str, str, str]:
    """
    Détermine l'URL officielle de téléchargement de `ngrok` v3, le format d'archive ('tgz' ou 'zip')
    et le chemin local `./bin/ngrok` selon l'OS et l'architecture (`uname -m` sur Raspberry Pi).
    """
    system = platform.system().lower()
    machine = platform.machine().lower()
    if hasattr(os, "uname"):
        try:
            machine = os.uname().machine.strip().lower()
        except Exception:
            pass
    bits = struct.calcsize("P") * 8

    base_url = "https://bin.equinox.io/c/bNyj1mQVY4c"

    if system == "windows":
        if "64" in machine or bits == 64:
            archive = "ngrok-v3-stable-windows-amd64.zip"
        else:
            archive = "ngrok-v3-stable-windows-386.zip"
        return f"{base_url}/{archive}", "zip", os.path.join(BIN_DIR, "ngrok.exe")

    if system == "darwin":
        if "arm" in machine or "aarch64" in machine:
            archive = "ngrok-v3-stable-darwin-arm64.zip"
        else:
            archive = "ngrok-v3-stable-darwin-amd64.zip"
        return f"{base_url}/{archive}", "zip", os.path.join(BIN_DIR, "ngrok")

    # Linux (Raspberry Pi ARM64 / ARMv7 / x86_64)
    if machine in ("aarch64", "arm64"):
        archive = "ngrok-v3-stable-linux-arm64.tgz"
    elif machine in ("armv7l", "armv6l", "armhf") or "arm" in machine:
        archive = "ngrok-v3-stable-linux-arm.tgz"
    elif machine in ("x86_64", "amd64"):
        archive = "ngrok-v3-stable-linux-amd64.tgz"
    else:
        archive = "ngrok-v3-stable-linux-386.tgz"

    return f"{base_url}/{archive}", "tgz", os.path.join(BIN_DIR, "ngrok")


def cleanup_legacy_cloudflared() -> None:
    """Arrête et supprime tout ancien processus ou binaire `cloudflared`."""
    if platform.system().lower() == "linux":
        try:
            subprocess.run(
                ["pkill", "-9", "-f", "cloudflared"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )
        except Exception:
            pass

    if os.path.isdir(BIN_DIR):
        for fname in os.listdir(BIN_DIR):
            if fname.startswith("cloudflared"):
                try:
                    os.remove(os.path.join(BIN_DIR, fname))
                except OSError:
                    pass


def ensure_ngrok_binary() -> Optional[str]:
    """
    Vérifie la présence de l'exécutable officiel `ngrok` dans `./bin/` et le télécharge
    automatiquement s'il est absent.
    """
    os.makedirs(BIN_DIR, exist_ok=True)
    cleanup_legacy_cloudflared()

    download_url, archive_type, binary_path = get_ngrok_download_info()

    if os.path.exists(binary_path) and os.path.getsize(binary_path) > 1_000_000:
        if platform.system().lower() != "windows":
            try:
                st = os.stat(binary_path)
                os.chmod(binary_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
            except OSError:
                pass
        return binary_path

    logger.info("Téléchargement automatique de ngrok depuis %s ...", download_url)
    tmp_binary_path = f"{binary_path}.tmp"
    try:
        with requests.get(download_url, stream=True, timeout=60, verify=False) as resp:
            resp.raise_for_status()
            archive_bytes = io.BytesIO(resp.content)

        if archive_type == "tgz":
            with tarfile.open(fileobj=archive_bytes, mode="r:gz") as tar:
                member = next((m for m in tar.getmembers() if os.path.basename(m.name) == "ngrok"), None)
                if member is None:
                    raise RuntimeError("Exécutable 'ngrok' introuvable dans l'archive .tgz")
                extracted = tar.extractfile(member)
                if extracted is None:
                    raise RuntimeError("Impossible d'extraire 'ngrok' depuis l'archive .tgz")
                with open(tmp_binary_path, "wb") as out_f:
                    out_f.write(extracted.read())
        else:
            with zipfile.ZipFile(archive_bytes) as zf:
                target_name = next(
                    (name for name in zf.namelist() if os.path.basename(name).lower() in ("ngrok", "ngrok.exe")),
                    None,
                )
                if target_name is None:
                    raise RuntimeError("Exécutable 'ngrok' introuvable dans l'archive .zip")
                with zf.open(target_name) as z_in, open(tmp_binary_path, "wb") as out_f:
                    out_f.write(z_in.read())

        os.replace(tmp_binary_path, binary_path)
        if platform.system().lower() != "windows":
            st = os.stat(binary_path)
            os.chmod(binary_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        logger.info("Binaire officiel ngrok installé avec succès dans %s", binary_path)
        return binary_path
    except Exception as exc:
        logger.error("Échec du téléchargement ou de l'extraction de ngrok : %s", exc)
        if os.path.exists(tmp_binary_path):
            try:
                os.remove(tmp_binary_path)
            except OSError:
                pass
        return None


def configure_ngrok_authtoken(binary_path: str, authtoken: str = NGROK_AUTHTOKEN) -> bool:
    """
    Configure le token d'authentification Ngrok via :
    `./bin/ngrok config add-authtoken <NGROK_AUTHTOKEN>`
    """
    token = (authtoken or NGROK_AUTHTOKEN).strip()
    if not token:
        return False

    exec_cmd = "./bin/ngrok" if platform.system().lower() != "windows" else binary_path
    cmd = [exec_cmd, "config", "add-authtoken", token]
    try:
        res = subprocess.run(
            cmd,
            cwd=BASE_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
            check=False,
            text=True,
        )
        if res.returncode == 0:
            logger.info("Token Ngrok configuré avec succès (%s config add-authtoken).", exec_cmd)
            return True
        logger.warning("Retour configuration token Ngrok (code=%s) : %s", res.returncode, (res.stdout or "").strip())
    except Exception as exc:
        logger.error("Erreur lors de la configuration du token Ngrok : %s", exc)
    return False


def check_internet_alive(timeout: float = 3.0) -> bool:
    """Vérifie rapidement si la connexion Internet du Club-House est active."""
    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53)):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def disable_wifi_power_save() -> None:
    """Désactive la mise en veille Wi-Fi sur Raspberry Pi pour maintenir le tunnel 24h/24."""
    if platform.system().lower() != "linux":
        return
    for cmd in (
        ["iw", "dev", "wlan0", "set", "power_save", "off"],
        ["iwconfig", "wlan0", "power", "off"],
        ["sudo", "-n", "iw", "dev", "wlan0", "set", "power_save", "off"],
        ["sudo", "-n", "iwconfig", "wlan0", "power", "off"],
    ):
        try:
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )
        except Exception:
            pass


class RemoteTunnelManager:
    """
    Superviseur du tunnel permanent Ngrok en arrière-plan :
    - Domaine statique fixe : `https://energetic-ember-image.ngrok-free.dev`
    - Lance : `./bin/ngrok http --domain=energetic-ember-image.ngrok-free.dev 5000 --log=stdout`
    - Vérifie toutes les 20 secondes l'état de la connexion Internet et du processus Ngrok :
      si la connexion Internet a coupé puis est revenue (ou si le processus s'est arrêté),
      relance automatiquement la commande.
    """

    def __init__(
        self,
        local_port: int = 5000,
        on_url_change: Optional[Callable[[str], None]] = None,
        get_settings_fn: Optional[Callable[[], Dict[str, Any]]] = None,
    ) -> None:
        self.local_port = local_port
        self.on_url_change = on_url_change
        self.get_settings_fn = get_settings_fn

        self.static_domain: str = NGROK_STATIC_DOMAIN
        self.public_base_url: str = NGROK_PUBLIC_BASE_URL
        self.registered: bool = True
        self.status: str = "starting"  # starting | online | reconnecting | error
        self.last_error: str = ""
        self.started_at: str = ""
        self.internet_online: bool = True

        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._restart_requested = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def get_info(self, **_kwargs: Any) -> Dict[str, Any]:
        """Retourne l'état courant du tunnel permanent Ngrok pour l'API et `/admin`."""
        with self._lock:
            return {
                "status": self.status,
                "registered": True,
                "static_domain": self.static_domain,
                "public_base_url": NGROK_PUBLIC_BASE_URL,
                "public_admin_url": NGROK_PUBLIC_ADMIN_URL,
                "started_at": self.started_at,
                "last_error": self.last_error,
            }

    def start(self) -> None:
        """Démarre le superviseur Ngrok en tâche de fond."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        disable_wifi_power_save()
        self._thread = threading.Thread(
            target=self._supervisor_loop,
            daemon=True,
            name="ASD_Ngrok_Tunnel",
        )
        self._thread.start()

    def request_restart(self) -> None:
        """Force le redémarrage du processus Ngrok."""
        self._restart_requested.set()
        self._terminate_process()

    def stop(self) -> None:
        """Arrête proprement le sous-processus Ngrok."""
        self._stop_event.set()
        self._terminate_process()

    def _terminate_process(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=4)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if platform.system().lower() == "linux":
            try:
                subprocess.run(
                    ["pkill", "-9", "-f", f"ngrok http --domain={NGROK_STATIC_DOMAIN}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                    check=False,
                )
            except Exception:
                pass

    def _build_command(self, binary_path: str) -> list[str]:
        exec_cmd = "./bin/ngrok" if platform.system().lower() != "windows" else binary_path
        return [
            exec_cmd,
            "http",
            f"--domain={NGROK_STATIC_DOMAIN}",
            str(self.local_port),
            "--log=stdout",
        ]

    def _launch_ngrok_process(self, binary_path: str) -> Optional[subprocess.Popen]:
        """Configure le token et lance `./bin/ngrok http --domain=energetic-ember-image.ngrok-free.dev 5000 --log=stdout`."""
        self._terminate_process()
        configure_ngrok_authtoken(binary_path, NGROK_AUTHTOKEN)
        cmd = self._build_command(binary_path)
        logger.info("Lancement du tunnel permanent Ngrok : %s", " ".join(cmd))

        creationflags = 0
        if platform.system().lower() == "windows":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        log_writer = open(TUNNEL_LOG_PATH, "w", encoding="utf-8", errors="replace", buffering=1)
        proc = subprocess.Popen(
            cmd,
            cwd=BASE_DIR,
            stdout=log_writer,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        with self._lock:
            self._proc = proc
            self.public_base_url = NGROK_PUBLIC_BASE_URL
            self.registered = True
            self.status = "online"
            self.last_error = ""
            self.started_at = datetime.datetime.now().strftime("%d/%m/%Y à %H:%M:%S")

        if self.on_url_change:
            try:
                self.on_url_change(NGROK_PUBLIC_BASE_URL)
            except Exception as exc:
                logger.debug("Erreur callback on_url_change : %s", exc)

        return proc

    def _supervisor_loop(self) -> None:
        """
        Boucle principale de supervision (vérification toutes les 20 secondes) :
        - S'assure que `./bin/ngrok` est installé et configuré avec `NGROK_AUTHTOKEN`.
        - Lance `./bin/ngrok http --domain=energetic-ember-image.ngrok-free.dev 5000 --log=stdout`.
        - Vérifie toutes les 20 secondes si la connexion Internet a coupé puis est revenue
          (ou si le processus `ngrok` s'est arrêté) et relance automatiquement la commande.
        """
        while not self._stop_event.is_set():
            self._restart_requested.clear()

            if not check_internet_alive():
                with self._lock:
                    self.internet_online = False
                    self.status = "reconnecting"
                    self.last_error = "En attente de connexion Internet..."
                time.sleep(CHECK_INTERVAL_SECONDS)
                continue

            binary_path = ensure_ngrok_binary()
            if not binary_path:
                with self._lock:
                    self.status = "error"
                    self.last_error = "Impossible de télécharger ngrok (vérifiez la connexion Internet)."
                time.sleep(CHECK_INTERVAL_SECONDS)
                continue

            try:
                proc = self._launch_ngrok_process(binary_path)
                was_internet_online = True
                with self._lock:
                    self.internet_online = True

                # Vérification toutes les 20 secondes
                while not self._stop_event.is_set() and not self._restart_requested.is_set():
                    time.sleep(CHECK_INTERVAL_SECONDS)
                    if self._stop_event.is_set() or self._restart_requested.is_set():
                        break

                    disable_wifi_power_save()
                    internet_now = check_internet_alive()
                    proc_dead = proc is None or (proc.poll() is not None)

                    if not internet_now:
                        if was_internet_online:
                            logger.warning("Coupure Internet détectée : en attente du retour de la connexion...")
                        was_internet_online = False
                        with self._lock:
                            self.internet_online = False
                            self.status = "reconnecting"
                        continue

                    # La connexion Internet est active :
                    if not was_internet_online:
                        logger.info(
                            "Connexion Internet rétablie après coupure : relance automatique de Ngrok (%s)...",
                            NGROK_STATIC_DOMAIN,
                        )
                        was_internet_online = True
                        with self._lock:
                            self.internet_online = True
                        proc = self._launch_ngrok_process(binary_path)
                        continue

                    if proc_dead:
                        exit_code = proc.poll() if proc else None
                        logger.warning(
                            "Le processus Ngrok s'est arrêté (code=%s) alors qu'Internet est actif : relance immédiate...",
                            exit_code,
                        )
                        proc = self._launch_ngrok_process(binary_path)
                        continue

                    # Tronque préventivement tunnel.log s'il dépasse 2 Mo
                    try:
                        if os.path.exists(TUNNEL_LOG_PATH) and os.path.getsize(TUNNEL_LOG_PATH) > 2_000_000:
                            with open(TUNNEL_LOG_PATH, "w", encoding="utf-8"):
                                pass
                    except OSError:
                        pass

            except Exception as exc:
                with self._lock:
                    self.status = "reconnecting"
                    self.last_error = str(exc)
                logger.warning("Erreur dans le superviseur Ngrok (%s), nouvelle tentative dans 20s...", exc)
                time.sleep(CHECK_INTERVAL_SECONDS)
            finally:
                self._terminate_process()
