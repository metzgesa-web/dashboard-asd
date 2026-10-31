"""
Application Principale Flask — Tableau de Bord Numérique 100 % Cloud / Web App
Club-House de l'AS Durlinsdorf (Fondé en 1969 — Affiliation FFF #523732)

Fonctionnalités Cloud & Smart TV (Sans Raspberry Pi) :
- Hébergeable gratuitement dans le Cloud (Render, Railway, PythonAnywhere) via Gunicorn / WSGI
- Vue publique `/tv` plein écran responsive (HTML5 / Tailwind CSS) compatible toutes résolutions Smart TV (720p, 1080p, 4K) :
  * Horloge numérique, date complète et météo locale en direct (Open-Meteo avec icônes 100 % SVG)
  * Blocs "Dernier résultat joué" et "Prochain match"
  * Tableau officiel complet du classement FFF (POS, CLUB, PTS, J, G, N, P, DIFF)
  * Carrousel des partenaires, Événement à la une, Carte de la Buvette et Bannière défilante (annonces + sponsors)
  * Mode Kiosque Web intégré (API Plein Écran HTML5 + Wake Lock anti-veille + reconnexion automatique)
- Interface d'administration `/admin` Mobile-First sécurisée par code PIN modifiable (défaut : 1969)
- Synchronisation FFF en arrière-plan (`fff_scraper.py`) avec cache multi-niveaux (mémoire + `database.json`)
"""

from __future__ import annotations

import base64
import copy
import datetime
import io
import json
import logging
import os
import queue
import socket
import threading
import time
import uuid
from functools import wraps
from typing import Any, Callable, Dict, List, Optional

import qrcode
import requests
from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

from fff_scraper import FFFScraper, is_asd_team_name, should_auto_sync

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ASD_Cloud_App")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.json")
PRINTABLE_QR_PATH = os.path.join(BASE_DIR, "qr_code_imprimable.html")
CLUB_LOGO_PATH = os.path.join(BASE_DIR, "static", "logo_asd.png")
UPLOAD_SPONSORS_DIR = os.path.join(BASE_DIR, "static", "uploads", "sponsors")
UPLOAD_EVENTS_DIR = os.path.join(BASE_DIR, "static", "uploads", "events")

os.makedirs(UPLOAD_SPONSORS_DIR, exist_ok=True)
os.makedirs(UPLOAD_EVENTS_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "svg", "webp", "gif"}

app = Flask(__name__)
# Support natif des reverse-proxys HTTPS Cloud (Render, Railway, PythonAnywhere, Cloudflare)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)  # type: ignore[assignment]
app.secret_key = os.environ.get("ASD_SECRET_KEY", "asd-durlinsdorf-1969-stade-grumbach-cloud-secret-key")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 Mo max par upload image
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# Verrou global et gestion d'état temps réel (SSE + Polling)
db_lock = threading.RLock()
state_version = int(time.time() * 1000)
sse_clients: List[queue.Queue] = []
sse_lock = threading.Lock()

# Cache météo en mémoire (rafraîchi toutes les 15 minutes) — 100 % SVG vectoriel (aucun emoji Unicode)
weather_cache: Dict[str, Any] = {
    "timestamp": 0.0,
    "data": {
        "temp": 14,
        "weather_code": 1,
        "description": "Peu nuageux",
        "icon": "partly_cloudy",
        "icon_key": "partly_cloudy",
        "wind_kmh": 8,
        "city": "Durlinsdorf",
        "updated_at": "--:--",
    },
}

# Initialisation du scraper FFF avec cache mémoire intégré
fff_scraper = FFFScraper(cache_ttl=900)
last_fff_sync_epoch = time.time()

# Cache mémoire pour l'URL Cloud publique détectée et les QR Codes Base64
network_cache: Dict[str, Any] = {
    "timestamp": 0.0,
    "ip": "127.0.0.1",
    "detected_base_url": "",
}
qr_b64_cache: Dict[str, str] = {}
tv_reload_id = 0

# Protection Anti-Bruteforce sur le Code PIN (5 essais max -> blocage 10 minutes par IP)
MAX_PIN_ATTEMPTS = 5
PIN_LOCKOUT_SECONDS = 600
pin_bruteforce_lock = threading.Lock()
pin_attempts_by_ip: Dict[str, Dict[str, Any]] = {}


# =============================================================================
# DÉTECTION AUTOMATIQUE DE L'URL PUBLIQUE CLOUD & GÉNÉRATION DE QR CODES
# =============================================================================
def get_local_ip() -> str:
    """Détecte l'adresse IP locale de la machine (utile lors des tests en local avant déploiement Cloud)."""
    now = time.time()
    if now - network_cache["timestamp"] < 60 and network_cache["ip"] != "127.0.0.1":
        return network_cache["ip"]

    detected_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            detected_ip = ip
    except Exception:
        pass

    network_cache["ip"] = detected_ip
    network_cache["timestamp"] = now
    return detected_ip


def get_public_base_url() -> str:
    """
    Détermine automatiquement l'URL publique de la Web App Cloud :
    1. Variable d'environnement explicite `PUBLIC_BASE_URL` ou `RENDER_EXTERNAL_URL`
    2. Domaine Railway `RAILWAY_PUBLIC_DOMAIN` ou PythonAnywhere
    3. URL de la requête HTTP entrante (`request.host_url`) derrière le proxy HTTPS
    4. Repli sur l'URL locale (`http://<ip>:<port>`) en développement local.
    """
    # 1. Variables d'environnement Cloud (Render, Railway, configuration manuelle)
    for env_key in ("PUBLIC_BASE_URL", "RENDER_EXTERNAL_URL"):
        val = os.environ.get(env_key, "").strip().rstrip("/")
        if val:
            return val

    railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip().rstrip("/")
    if railway_domain:
        if not railway_domain.startswith("http"):
            return f"https://{railway_domain}"
        return railway_domain

    # 2. Détection dynamique via la requête HTTP active
    try:
        if request and request.host:
            host = request.host.strip()
            scheme = request.headers.get("X-Forwarded-Proto", request.scheme or "http").split(",")[0].strip()
            if not host.startswith("127.0.0.1") and not host.startswith("localhost"):
                base = f"{scheme}://{host}".rstrip("/")
                network_cache["detected_base_url"] = base
                return base
    except RuntimeError:
        pass

    if network_cache.get("detected_base_url"):
        return str(network_cache["detected_base_url"])

    # 3. Repli pour test local sur PC
    port = int(os.environ.get("PORT", 5000))
    ip = get_local_ip()
    if port in (80, 443):
        return f"http://{ip}"
    return f"http://{ip}:{port}"


def get_tv_url() -> str:
    """Retourne l'URL complète de la vue Smart TV (`/tv`)."""
    return f"{get_public_base_url()}/tv"


def get_admin_url() -> str:
    """Retourne l'URL complète de l'interface d'administration (`/admin`)."""
    return f"{get_public_base_url()}/admin"


def generate_qr_base64(url: str, fill_color: str = "#0B0F17", back_color: str = "#FFD700") -> str:
    """Génère un QR Code haute lisibilité encodé en Base64 PNG (mis en cache par URL)."""
    if not url:
        return ""
    cache_key = f"{url}|{fill_color}|{back_color}"
    if cache_key in qr_b64_cache:
        return qr_b64_cache[cache_key]

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color=fill_color, back_color=back_color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
    qr_b64_cache[cache_key] = b64
    return b64


def generate_printable_qr_html(
    admin_url: Optional[str] = None,
    tv_url: Optional[str] = None,
    pin: Optional[str] = None,
) -> str:
    """
    Génère et sauvegarde la fiche imprimable A4 (`qr_code_imprimable.html`)
    contenant le QR Code d'administration Smartphone (`/admin`) et le QR Code / Lien Smart TV (`/tv`).
    """
    if not admin_url:
        admin_url = get_admin_url()
    if not tv_url:
        tv_url = get_tv_url()
    if not pin:
        pin = get_configured_pin()

    def _make_print_qr(target_url: str) -> str:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=12,
            border=2,
        )
        qr.add_data(target_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#0B0F17", back_color="#FFFFFF")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"

    qr_admin_b64 = _make_print_qr(admin_url)
    qr_tv_b64 = _make_print_qr(tv_url)

    if not os.path.exists(CLUB_LOGO_PATH):
        try:
            fff_scraper.fetch_and_save_club_logo(force=False)
        except Exception:
            pass

    logo_b64 = ""
    if os.path.exists(CLUB_LOGO_PATH):
        try:
            with open(CLUB_LOGO_PATH, "rb") as lf:
                raw_logo = lf.read()
                mime = "image/svg+xml" if raw_logo.lstrip().startswith(b"<") else "image/png"
                logo_b64 = f"data:{mime};base64,{base64.b64encode(raw_logo).decode('ascii')}"
        except Exception:
            logo_b64 = ""

    logo_img_html = (
        f'<img src="{logo_b64}" alt="Blason AS Durlinsdorf" class="logo" />'
        if logo_b64
        else '<div class="logo-fallback">ASD 1969</div>'
    )

    generated_date = datetime.datetime.now().strftime("%d/%m/%Y à %H:%M")

    html_content = f"""<!DOCTYPE html>
<html lang="fr" translate="no" class="notranslate">
<head>
  <meta charset="UTF-8" />
  <meta name="google" content="notranslate" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AS DURLINSDORF - Fiche d'Accès Cloud Smart TV &amp; Administration</title>
  <style>
    @page {{
      size: A4 portrait;
      margin: 10mm;
    }}
    * {{
      box-sizing: border-box;
      -webkit-print-color-adjust: exact !important;
      print-color-adjust: exact !important;
    }}
    body {{
      margin: 0;
      padding: 20px;
      background-color: #0B0F17;
      color: #FFFFFF;
      font-family: 'Segoe UI', system-ui, -apple-system, Roboto, Arial, sans-serif;
      display: flex;
      flex-direction: column;
      align-items: center;
      min-height: 100vh;
    }}
    .toolbar {{
      width: 100%;
      max-width: 780px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 16px;
      gap: 12px;
    }}
    .btn-print {{
      background-color: #FFD700;
      color: #0B0F17;
      border: none;
      border-radius: 12px;
      padding: 12px 24px;
      font-size: 15px;
      font-weight: 900;
      text-transform: uppercase;
      letter-spacing: 1px;
      cursor: pointer;
      box-shadow: 0 8px 20px rgba(255, 215, 0, 0.35);
    }}
    .btn-back {{
      color: #FFD700;
      text-decoration: none;
      font-weight: 700;
      font-size: 14px;
      border: 1px solid rgba(255, 215, 0, 0.4);
      padding: 10px 18px;
      border-radius: 10px;
    }}
    .sheet {{
      width: 100%;
      max-width: 780px;
      background: linear-gradient(180deg, #111827 0%, #0B0F17 100%);
      border: 4px solid #FFD700;
      border-radius: 26px;
      padding: 26px 28px;
      text-align: center;
      box-shadow: 0 20px 50px rgba(0, 0, 0, 0.75);
    }}
    .header-badge {{
      display: inline-block;
      background-color: #FFD700;
      color: #0B0F17;
      font-weight: 900;
      font-size: 12px;
      padding: 5px 16px;
      border-radius: 999px;
      letter-spacing: 2px;
      text-transform: uppercase;
      margin-bottom: 10px;
    }}
    .logo {{
      width: 82px;
      height: 82px;
      object-fit: contain;
      display: block;
      margin: 0 auto 8px auto;
    }}
    h1 {{
      margin: 0;
      font-size: 24px;
      font-weight: 900;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: #FFFFFF;
    }}
    .subtitle {{
      margin-top: 4px;
      font-size: 13px;
      font-weight: 800;
      color: #FFD700;
      text-transform: uppercase;
      letter-spacing: 1.2px;
    }}
    .pin-banner {{
      display: inline-flex;
      align-items: center;
      gap: 14px;
      background-color: #FFD700;
      color: #0B0F17;
      padding: 8px 24px;
      border-radius: 14px;
      margin: 14px 0;
    }}
    .pin-label {{
      font-size: 13px;
      font-weight: 900;
      text-transform: uppercase;
    }}
    .pin-code {{
      font-size: 24px;
      font-weight: 900;
      letter-spacing: 5px;
      font-family: Consolas, Monaco, monospace;
    }}
    .grid-2 {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 18px;
      margin-top: 10px;
      text-align: center;
    }}
    .card {{
      background-color: #0B0F17;
      border: 2px solid #10B981;
      border-radius: 20px;
      padding: 18px 16px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: space-between;
    }}
    .card-tv {{
      border-color: #FFD700;
    }}
    .card-badge {{
      display: inline-block;
      padding: 5px 14px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 900;
      text-transform: uppercase;
      letter-spacing: 0.8px;
      margin-bottom: 8px;
    }}
    .badge-admin {{
      background-color: #10B981;
      color: #0B0F17;
    }}
    .badge-tv {{
      background-color: #FFD700;
      color: #0B0F17;
    }}
    .qr-box {{
      background-color: #FFFFFF;
      border: 4px solid #FFD700;
      border-radius: 18px;
      padding: 10px;
      width: 215px;
      height: 215px;
      margin: 10px auto;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .qr-box-admin {{
      border-color: #10B981;
    }}
    .qr-box img {{
      width: 100%;
      height: 100%;
      object-fit: contain;
    }}
    .url-label {{
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
      color: #94A3B8;
      margin-top: 6px;
    }}
    .url-val {{
      font-family: Consolas, Monaco, monospace;
      font-size: 13px;
      font-weight: 900;
      color: #FFD700;
      word-break: break-all;
      margin-top: 4px;
    }}
    .url-val-green {{
      color: #34D399;
    }}
    .info-bar {{
      margin-top: 16px;
      background-color: rgba(255, 215, 0, 0.08);
      border: 2px solid rgba(255, 215, 0, 0.45);
      border-radius: 16px;
      padding: 12px 16px;
      text-align: left;
      font-size: 12px;
      color: #E2E8F0;
      line-height: 1.45;
    }}
    .footer-note {{
      margin-top: 12px;
      font-size: 11px;
      color: #64748B;
      text-transform: uppercase;
      letter-spacing: 1px;
    }}
    @media print {{
      body {{
        background-color: #FFFFFF;
        padding: 0;
      }}
      .no-print {{
        display: none !important;
      }}
      .sheet {{
        box-shadow: none;
        max-width: 100%;
      }}
    }}
  </style>
</head>
<body>
  <div class="toolbar no-print">
    <a href="/admin" class="btn-back">← Retour à l'Administration</a>
    <button type="button" onclick="window.print()" class="btn-print">🖨️ Imprimer cette fiche A4</button>
  </div>

  <div class="sheet">
    <div class="header-badge">CLUB-HOUSE • DEPUIS 1969 • APPLICATION WEB 100 % CLOUD</div>
    {logo_img_html}
    <h1>AS DURLINSDORF - Affichage Dynamique Smart TV</h1>
    <div class="subtitle">Pilotage par Smartphone (4G / Wi-Fi) &amp; Affichage Direct sur Navigateur TV</div>

    <div class="pin-banner">
      <span class="pin-label">🔐 Code PIN Administration :</span>
      <span class="pin-code">{pin}</span>
    </div>

    <div class="grid-2">
      <!-- COLONNE 1 : QR CODE TÉLÉCOMMANDE ADMIN SMARTPHONE -->
      <div class="card">
        <div>
          <span class="card-badge badge-admin">📱 Télécommande Admin (Smartphone)</span>
          <div style="font-size: 12px; color: #E2E8F0; font-weight: 700;">
            Scannez ce QR Code avec votre téléphone pour gérer la buvette, les annonces et le mode de la TV
          </div>
        </div>
        <div class="qr-box qr-box-admin">
          <img src="{qr_admin_b64}" alt="QR Code Administration" />
        </div>
        <div style="width: 100%;">
          <div class="url-label">Lien Administration (/admin)</div>
          <div class="url-val url-val-green">{admin_url}</div>
        </div>
      </div>

      <!-- COLONNE 2 : LIEN & QR CODE AFFICHAGE SMART TV -->
      <div class="card card-tv">
        <div>
          <span class="card-badge badge-tv">📺 Affichage Plein Écran (Smart TV)</span>
          <div style="font-size: 12px; color: #E2E8F0; font-weight: 700;">
            Ouvrez cette adresse dans le navigateur internet de la Smart TV ou clé Fire TV / Google TV
          </div>
        </div>
        <div class="qr-box">
          <img src="{qr_tv_b64}" alt="QR Code Vue TV" />
        </div>
        <div style="width: 100%;">
          <div class="url-label">Lien Direct Smart TV (/tv)</div>
          <div class="url-val">{tv_url}</div>
        </div>
      </div>
    </div>

    <div class="info-bar">
      <strong style="color: #FFD700;">📺 Affichage sur la TV (Sans Raspberry Pi) :</strong>
      Ouvrez simplement <strong style="color: #FFD700;">{tv_url}</strong> sur le navigateur de la Smart TV (ou via <em>Fully Kiosk Browser</em> / <em>Amazon Silk</em> sur Fire TV Stick). Cliquez sur le bouton <strong>Plein Écran</strong> en haut à droite de la TV.
      <br />
      <strong style="color: #34D399;">📱 Télécommande Dirigeants &amp; Bénévoles :</strong> Scannez le QR Code vert ou rendez-vous sur <strong style="color: #34D399;">{admin_url}</strong> (Code PIN : <strong>{pin}</strong>).
    </div>

    <div class="footer-note">
      AS Durlinsdorf (Affiliation FFF #523732) • Tableau de Bord Cloud Smart TV • Généré le {generated_date}
    </div>
  </div>
</body>
</html>
"""
    try:
        with open(PRINTABLE_QR_PATH, "w", encoding="utf-8") as f:
            f.write(html_content)
    except Exception as exc:
        logger.debug("Écriture optionnelle de qr_code_imprimable.html ignorée : %s", exc)

    return html_content


# =============================================================================
# BASE DE DONNÉES JSON ATOMIQUE & TEMPS RÉEL (SSE)
# =============================================================================
def load_db() -> Dict[str, Any]:
    """Charge la base de données locale de manière thread-safe."""
    with db_lock:
        if not os.path.exists(DB_PATH):
            return {"settings": {"admin_pin": "1969", "display_mode": "competitions"}, "teams": []}
        try:
            with open(DB_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.error("Erreur lecture database.json : %s", exc)
            return {"settings": {"admin_pin": "1969", "display_mode": "competitions"}, "teams": []}


def save_db(data: Dict[str, Any], broadcast: bool = True) -> None:
    """Sauvegarde atomique de `database.json` et notification SSE temps réel."""
    global state_version
    with db_lock:
        tmp_path = f"{DB_PATH}.tmp.{os.getpid()}.{threading.get_ident()}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, DB_PATH)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        state_version = int(time.time() * 1000)

    if broadcast:
        notify_sse_clients()


def notify_sse_clients() -> None:
    """Notifie tous les écrans TV connectés en Server-Sent Events (SSE)."""
    payload = get_public_state()
    msg = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    with sse_lock:
        stale: List[queue.Queue] = []
        for q in sse_clients:
            try:
                q.put_nowait(msg)
            except queue.Full:
                stale.append(q)
        for q in stale:
            if q in sse_clients:
                sse_clients.remove(q)


def get_club_logo_url() -> str:
    """Retourne l'URL locale du logo du club avec un paramètre de version anti-cache."""
    db = load_db()
    custom_b64 = db.get("settings", {}).get("club_logo_b64")
    if custom_b64 and str(custom_b64).startswith("data:image/"):
        return str(custom_b64)
    if os.path.exists(CLUB_LOGO_PATH):
        mtime = int(os.path.getmtime(CLUB_LOGO_PATH))
        return f"/static/logo_asd.png?v={mtime}"
    return "/static/logo_asd.png"


def get_public_state() -> Dict[str, Any]:
    """Construit l'état complet (sans le code PIN ni les secrets) envoyé à la Smart TV et aux clients."""
    db = copy.deepcopy(load_db())
    settings = db.get("settings", {})

    # Masque les secrets dans l'état public
    settings.pop("admin_pin", None)
    settings.pop("cloudflare_tunnel_token", None)
    settings.pop("ngrok_authtoken", None)
    settings.pop("ntfy_topic", None)
    settings.pop("wifi_credentials", None)
    settings.pop("club_logo_b64", None)

    base_url = get_public_base_url()
    admin_url = f"{base_url}/admin"
    tv_url = f"{base_url}/tv"

    qr_admin_b64 = generate_qr_base64(admin_url, fill_color="#0B0F17", back_color="#FFD700")
    qr_tv_b64 = generate_qr_base64(tv_url, fill_color="#0B0F17", back_color="#FFD700")

    return {
        "version": state_version,
        "server_time": datetime.datetime.now().isoformat(),
        "public_base_url": base_url,
        "tv_url": tv_url,
        "admin_url": admin_url,
        "club_logo_url": get_club_logo_url(),
        "qr_code_b64": qr_admin_b64,
        "qr_tv_b64": qr_tv_b64,
        "tv_reload_id": tv_reload_id,
        "remote_tunnel": {
            "status": "online",
            "public_base_url": base_url,
            "public_admin_url": admin_url,
            "public_tv_url": tv_url,
            "qr_remote_b64": qr_admin_b64,
        },
        "settings": settings,
        "featured_event": db.get("featured_event", {}),
        "ticker_messages": db.get("ticker_messages", []),
        "sponsors": db.get("sponsors", []),
        "buvette_categories": db.get("buvette_categories", []),
        "buvette_items": db.get("buvette_items", []),
        "teams": [
            t for t in db.get("teams", [])
            if isinstance(t.get("standings"), list) and len(t["standings"]) > 0
        ],
    }


# =============================================================================
# MÉTÉO LOCALE EN DIRECT (OPEN-METEO) — ICÔNES 100 % SVG VECTORIELLES
# =============================================================================
def wmo_code_to_french(code: int, is_day: int = 1) -> Dict[str, str]:
    """Traduit les codes météo WMO d'Open-Meteo en libellés et clés d'icônes SVG vectorielles."""
    mapping = {
        0: ("Ciel dégagé", "sun" if is_day else "moon"),
        1: ("Peu nuageux", "partly_cloudy" if is_day else "moon"),
        2: ("Partiellement voilé", "partly_cloudy"),
        3: ("Couvert", "cloudy"),
        45: ("Brouillard", "fog"),
        48: ("Brouillard givrant", "fog"),
        51: ("Bruine légère", "drizzle"),
        53: ("Bruine modérée", "drizzle"),
        55: ("Bruine dense", "rain"),
        61: ("Pluie faible", "drizzle"),
        63: ("Pluie modérée", "rain"),
        65: ("Pluie forte", "heavy_rain"),
        71: ("Neige faible", "snow"),
        73: ("Neige modérée", "snow"),
        75: ("Chutes de neige", "snow"),
        80: ("Averses légères", "drizzle"),
        81: ("Averses modérées", "rain"),
        82: ("Fortes averses", "heavy_rain"),
        95: ("Orage", "thunder"),
        96: ("Orage et grêle", "thunder"),
        99: ("Orage violent", "thunder"),
    }
    desc, icon_key = mapping.get(code, ("Variable", "partly_cloudy"))
    return {"description": desc, "icon": icon_key, "icon_key": icon_key}


def fetch_durlinsdorf_weather() -> Dict[str, Any]:
    """Récupère la météo en direct à Durlinsdorf (47.486, 7.241) via Open-Meteo (cache de 15 min)."""
    now = time.time()
    if now - weather_cache["timestamp"] < 900:
        return weather_cache["data"]

    db = load_db()
    settings = db.get("settings", {})
    lat = settings.get("weather_lat", 47.486)
    lon = settings.get("weather_lon", 7.241)

    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,weather_code,wind_speed_10m,is_day"
        f"&timezone=Europe%2FParis"
    )
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            payload = resp.json()
            curr = payload.get("current", {})
            temp = round(float(curr.get("temperature_2m", 14.0)))
            code = int(curr.get("weather_code", 1))
            wind = round(float(curr.get("wind_speed_10m", 5.0)))
            is_day = int(curr.get("is_day", 1))
            wmo = wmo_code_to_french(code, is_day)

            weather_cache["data"] = {
                "temp": temp,
                "weather_code": code,
                "description": wmo["description"],
                "icon": wmo["icon_key"],
                "icon_key": wmo["icon_key"],
                "wind_kmh": wind,
                "city": "Durlinsdorf",
                "updated_at": datetime.datetime.now().strftime("%H:%M"),
            }
            weather_cache["timestamp"] = now
    except Exception as exc:
        logger.debug("Météo Open-Meteo hors-ligne (utilisation du cache) : %s", exc)

    return weather_cache["data"]


# =============================================================================
# AUTHENTIFICATION PAR CODE PIN (DÉFAUT : 1969) + PROTECTION ANTI-BRUTEFORCE
# =============================================================================
def get_configured_pin() -> str:
    env_pin = os.environ.get("ASD_ADMIN_PIN", "").strip()
    if env_pin and len(env_pin) == 4 and env_pin.isdigit():
        return env_pin
    db = load_db()
    return str(db.get("settings", {}).get("admin_pin", "1969")).strip()


def get_client_ip() -> str:
    """Récupère l'adresse IP réelle du client derrière un reverse-proxy Cloud."""
    cf_ip = request.headers.get("CF-Connecting-IP", "").strip()
    if cf_ip:
        return cf_ip
    xff = request.headers.get("X-Forwarded-For", "").strip()
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"


def get_ip_lockout_remaining(ip: str) -> int:
    """Retourne le nombre de secondes restantes de blocage Anti-Bruteforce (0 si non bloqué)."""
    now = time.time()
    with pin_bruteforce_lock:
        rec = pin_attempts_by_ip.get(ip)
        if not rec:
            return 0
        locked_until = float(rec.get("locked_until", 0.0))
        if locked_until > now:
            return int(locked_until - now) + 1
        if locked_until > 0 and locked_until <= now:
            pin_attempts_by_ip.pop(ip, None)
        return 0


def register_failed_pin_attempt(ip: str) -> tuple[int, int]:
    """Enregistre un échec de code PIN (5 tentatives erronées -> blocage de 10 minutes)."""
    now = time.time()
    with pin_bruteforce_lock:
        rec = pin_attempts_by_ip.setdefault(ip, {"fails": 0, "locked_until": 0.0})
        if rec["locked_until"] > 0 and rec["locked_until"] <= now:
            rec["fails"] = 0
            rec["locked_until"] = 0.0
        rec["fails"] = int(rec["fails"]) + 1
        fails = rec["fails"]
        if fails >= MAX_PIN_ATTEMPTS:
            rec["locked_until"] = now + PIN_LOCKOUT_SECONDS
            logger.warning(
                "ALERTE SÉCURITÉ : IP %s bloquée pour 10 minutes après %d codes PIN erronés.",
                ip,
                fails,
            )
            return fails, PIN_LOCKOUT_SECONDS
        return fails, 0


def clear_pin_attempts(ip: str) -> None:
    """Réinitialise le compteur d'échecs de cette IP après un code PIN valide."""
    with pin_bruteforce_lock:
        pin_attempts_by_ip.pop(ip, None)


def is_admin_authenticated() -> bool:
    ip = get_client_ip()
    if get_ip_lockout_remaining(ip) > 0:
        return False
    if session.get("admin_authenticated") is True:
        return True
    header_pin = request.headers.get("X-Admin-Pin", "").strip()
    if header_pin:
        if header_pin == get_configured_pin():
            clear_pin_attempts(ip)
            return True
        register_failed_pin_attempt(ip)
    return False


def admin_required(f: Callable) -> Callable:
    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        ip = get_client_ip()
        rem = get_ip_lockout_remaining(ip)
        if rem > 0:
            mins = max(1, (rem + 59) // 60)
            return jsonify({
                "ok": False,
                "error": f"Trop de tentatives. Accès bloqué pendant {mins} min ({rem}s).",
                "locked": True,
                "retry_after_seconds": rem,
                "auth_required": True,
            }), 429
        if not is_admin_authenticated():
            return jsonify({"ok": False, "error": "Code PIN requis ou invalide", "auth_required": True}), 401
        return f(*args, **kwargs)

    return decorated


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def file_bytes_to_data_uri(raw_bytes: bytes, ext: str) -> str:
    """Convertit une image uploadée en Data URI Base64 pour persistance Cloud dans `database.json`."""
    ext_low = ext.lower()
    if ext_low == "svg" or raw_bytes.lstrip().startswith(b"<"):
        mime = "image/svg+xml"
    elif ext_low in ("jpg", "jpeg"):
        mime = "image/jpeg"
    elif ext_low == "webp":
        mime = "image/webp"
    elif ext_low == "gif":
        mime = "image/gif"
    else:
        mime = "image/png"
    b64 = base64.b64encode(raw_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


@app.after_request
def add_no_cache_headers(response: Response) -> Response:
    """Empêche le navigateur de la Smart TV ou du smartphone de mettre en cache d'anciens états."""
    if request.path in ("/", "/tv", "/admin", "/admin/print-qr", "/health") or request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# =============================================================================
# ROUTES VUES HTML (/tv, /, /admin, /health)
# =============================================================================
@app.route("/health")
@app.route("/api/ping")
def health_check() -> Response:
    """Endpoint de santé léger pour les hébergeurs Cloud (Render, Railway) et le maintien d'activité TV."""
    return jsonify({
        "status": "ok",
        "version": state_version,
        "server_time": datetime.datetime.now().isoformat(),
    })


@app.route("/")
def index() -> Response:
    """Redirige la racine vers l'écran TV principal (`/tv`)."""
    return redirect(url_for("tv_view"))


@app.route("/tv")
def tv_view() -> str:
    """Vue publique `/tv` optimisée en plein écran responsive pour Smart TV."""
    get_public_base_url()
    initial_state = get_public_state()
    initial_weather = fetch_durlinsdorf_weather()
    return render_template(
        "tv.html",
        initial_state=initial_state,
        initial_weather=initial_weather,
    )


@app.route("/admin")
def admin_view() -> str:
    """Interface Mobile-First d'Administration (`/admin`) protégée par code PIN."""
    get_public_base_url()
    initial_state = get_public_state()
    return render_template(
        "admin.html",
        initial_state=initial_state,
        authenticated=is_admin_authenticated(),
    )


# =============================================================================
# ENDPOINTS PUBLICS TEMPS RÉEL (TV, SSE, MÉTÉO, QR CODE)
# =============================================================================
@app.route("/api/state")
@app.route("/api/data")
def api_state() -> Response:
    """Retourne l'état complet courant pour le polling rapide de la Smart TV et l'Admin."""
    get_public_base_url()
    return jsonify(get_public_state())


@app.route("/api/stream")
def api_stream() -> Response:
    """Flux Server-Sent Events (SSE) pour mise à jour instantanée sur la Smart TV."""
    get_public_base_url()

    def event_stream() -> Any:
        q: queue.Queue = queue.Queue(maxsize=25)
        with sse_lock:
            sse_clients.append(q)
        try:
            init_payload = get_public_state()
            yield f"data: {json.dumps(init_payload, ensure_ascii=False)}\n\n"
            while True:
                try:
                    msg = q.get(timeout=20)
                    yield msg
                except queue.Empty:
                    yield f": heartbeat {int(time.time())}\n\n"
        finally:
            with sse_lock:
                if q in sse_clients:
                    sse_clients.remove(q)

    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return Response(event_stream(), headers=headers)


@app.route("/api/weather")
def api_weather() -> Response:
    """Retourne la météo locale de Durlinsdorf (avec icônes SVG et cache hors-ligne)."""
    return jsonify(fetch_durlinsdorf_weather())


@app.route("/admin/print-qr")
def admin_print_qr() -> Response:
    """Génère et affiche la fiche A4 prête à imprimer (`qr_code_imprimable.html`)."""
    html_doc = generate_printable_qr_html(
        admin_url=get_admin_url(),
        tv_url=get_tv_url(),
        pin=get_configured_pin(),
    )
    return Response(html_doc, mimetype="text/html; charset=utf-8")


@app.route("/portail")
@app.route("/admin/portail-permanent")
def admin_portail_permanent() -> Response:
    """Redirige vers `/admin`."""
    return redirect(url_for("admin_view"))


@app.route("/api/qrcode.png")
def api_qrcode_png() -> Response:
    """
    Génère dynamiquement l'image PNG du QR Code :
    - `?target=admin` ou `?target=remote` : URL d'administration (`/admin`)
    - `?target=tv` ou `?target=local`     : URL de la Smart TV (`/tv`)
    """
    target = str(request.args.get("target") or "admin").lower()
    if target in ("tv", "local"):
        qr_target_url = get_tv_url()
        filename = "qrcode_smart_tv_as_durlinsdorf.png"
    else:
        qr_target_url = get_admin_url()
        filename = "qrcode_admin_as_durlinsdorf.png"

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=12,
        border=2,
    )
    qr.add_data(qr_target_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0B0F17", back_color="#FFD700")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    as_attachment = request.args.get("download") in ("1", "true", "yes")
    return send_file(
        buf,
        mimetype="image/png",
        as_attachment=as_attachment,
        download_name=filename,
    )


# =============================================================================
# ENDPOINTS D'ADMINISTRATION (`/api/admin/...`)
# =============================================================================
@app.route("/api/admin/login", methods=["POST"])
def api_admin_login() -> Any:
    """Vérifie le code PIN à 4 chiffres (défaut : 1969) avec protection Anti-Bruteforce."""
    ip = get_client_ip()
    rem = get_ip_lockout_remaining(ip)
    if rem > 0:
        mins = max(1, (rem + 59) // 60)
        return jsonify({
            "ok": False,
            "locked": True,
            "retry_after_seconds": rem,
            "error": f"Sécurité : Trop d'essais. Réessayez dans {mins} min ({rem}s).",
        }), 429

    body = request.get_json(silent=True) or {}
    pin = str(body.get("pin", "")).strip()
    if pin == get_configured_pin():
        clear_pin_attempts(ip)
        session["admin_authenticated"] = True
        session.permanent = True
        return jsonify({"ok": True, "message": "Authentification réussie", "state": get_public_state()})

    fails, lockout_sec = register_failed_pin_attempt(ip)
    if lockout_sec > 0:
        return jsonify({
            "ok": False,
            "locked": True,
            "retry_after_seconds": lockout_sec,
            "error": "5 essais erronés : accès bloqué pendant 10 minutes par sécurité.",
        }), 429

    remaining_tries = max(0, MAX_PIN_ATTEMPTS - fails)
    return jsonify({
        "ok": False,
        "remaining_attempts": remaining_tries,
        "error": f"Code PIN incorrect ({remaining_tries} essai{'s' if remaining_tries > 1 else ''} restant{'s' if remaining_tries > 1 else ''})",
    }), 403


@app.route("/api/admin/logout", methods=["POST"])
def api_admin_logout() -> Response:
    """Déconnecte la session d'administration."""
    session.pop("admin_authenticated", None)
    return jsonify({"ok": True})


@app.route("/api/admin/pin", methods=["POST"])
@admin_required
def api_admin_change_pin() -> Any:
    """Modifie le code PIN d'administration."""
    body = request.get_json(silent=True) or {}
    new_pin = str(body.get("new_pin", "")).strip()
    if not (len(new_pin) == 4 and new_pin.isdigit()):
        return jsonify({"ok": False, "error": "Le code PIN doit comporter exactement 4 chiffres."}), 400

    db = load_db()
    db.setdefault("settings", {})["admin_pin"] = new_pin
    save_db(db, broadcast=False)
    generate_printable_qr_html(admin_url=get_admin_url(), tv_url=get_tv_url(), pin=new_pin)
    return jsonify({"ok": True, "message": "Code PIN mis à jour avec succès."})


@app.route("/api/admin/mode", methods=["POST"])
@admin_required
def api_admin_mode() -> Any:
    """
    Change instantanément le mode de la TV (`competitions`, `buvette`, `mixed`)
    et/ou les paramètres d'affichage (durées de rotation, vitesse ticker).
    """
    body = request.get_json(silent=True) or {}
    db = load_db()
    settings = db.setdefault("settings", {})

    if "display_mode" in body:
        mode = str(body["display_mode"]).strip()
        if mode in ("competitions", "buvette", "mixed"):
            settings["display_mode"] = mode

    int_fields = (
        ("team_rotation_seconds", 5, 120),
        ("mixed_competitions_seconds", 15, 600),
        ("mixed_buvette_seconds", 10, 600),
        ("sponsor_rotation_seconds", 3, 60),
        ("ticker_speed_seconds", 10, 120),
    )
    for key, min_v, max_v in int_fields:
        if key in body:
            try:
                val = int(body[key])
                settings[key] = max(min_v, min(max_v, val))
            except (ValueError, TypeError):
                pass

    if "show_sponsors_in_ticker" in body:
        settings["show_sponsors_in_ticker"] = bool(body["show_sponsors_in_ticker"])

    save_db(db, broadcast=True)
    return jsonify({"ok": True, "state": get_public_state()})


# -----------------------------------------------------------------------------
# GESTION DE LA SYNCHRONISATION FFF & DES ÉQUIPES
# -----------------------------------------------------------------------------
def run_fff_sync(force_manual: bool = False) -> Dict[str, Any]:
    """Exécute la synchronisation FFF et sauvegarde les résultats dans `database.json`."""
    global last_fff_sync_epoch
    db = load_db()
    teams = db.get("teams", [])
    report = fff_scraper.sync_all_teams(teams, force_manual=force_manual)
    valid_teams = [
        t for t in (report.get("teams") or [])
        if isinstance(t.get("standings"), list) and len(t["standings"]) > 0
    ]

    with db_lock:
        fresh_db = load_db()
        if valid_teams:
            fresh_db["teams"] = valid_teams
        fresh_db.setdefault("settings", {})["last_fff_sync"] = report["last_sync"]
        fresh_db["settings"]["fff_sync_status"] = report["status"]
        if report.get("cl_no"):
            fresh_db["settings"]["club_cl_no"] = report["cl_no"]
        save_db(fresh_db, broadcast=True)

    last_fff_sync_epoch = time.time()
    return report


@app.route("/api/admin/teams/toggle", methods=["POST"])
@admin_required
def api_admin_toggle_team() -> Any:
    """Active ou désactive instantanément l'affichage d'une équipe du club sur la TV."""
    body = request.get_json(silent=True) or {}
    team_id = str(body.get("id") or "").strip()
    active_val = body.get("active")

    db = load_db()
    for t in db.get("teams", []):
        if t.get("id") == team_id:
            if active_val is None:
                t["active"] = not bool(t.get("active", True))
            else:
                t["active"] = bool(active_val)
            break

    save_db(db, broadcast=True)
    return jsonify({"ok": True, "state": get_public_state()})


@app.route("/api/admin/fff/sync", methods=["POST"])
@admin_required
def api_admin_fff_sync() -> Response:
    """Force la synchronisation immédiate des données FFF."""
    body = request.get_json(silent=True) or {}
    force_manual = bool(body.get("force_manual", True))
    report = run_fff_sync(force_manual=force_manual)
    return jsonify({
        "ok": True,
        "report": report,
        "state": get_public_state(),
    })


@app.route("/api/admin/teams", methods=["POST"])
@admin_required
def api_admin_save_team() -> Any:
    """Crée ou met à jour une équipe FFF (configuration, score manuel, classement)."""
    body = request.get_json(silent=True) or {}
    team_id = str(body.get("id") or "").strip()
    if not team_id:
        team_id = f"team-{uuid.uuid4().hex[:8]}"

    db = load_db()
    teams = db.setdefault("teams", [])

    existing_idx = next((i for i, t in enumerate(teams) if t.get("id") == team_id), None)
    target = copy.deepcopy(teams[existing_idx]) if existing_idx is not None else {
        "id": team_id,
        "name": "Nouvelle Équipe ASD",
        "short_name": "ASD",
        "category": "Seniors",
        "competition": "District Alsace",
        "fff_slug": "",
        "fff_cp_no": "",
        "fff_ph_no": "1",
        "fff_po_no": "",
        "fff_url": "",
        "active": True,
        "manual_override": False,
        "form": ["V", "N", "V"],
        "last_match": {},
        "next_match": {},
        "standings": [],
    }

    for field in (
        "name",
        "short_name",
        "category",
        "category_code",
        "competition",
        "fff_slug",
        "fff_cp_no",
        "fff_ph_no",
        "fff_po_no",
        "fff_url",
        "last_synced_at",
    ):
        if field in body:
            target[field] = str(body[field] or "").strip()

    for int_field in ("cl_no", "team_number", "team_code"):
        if int_field in body and body[int_field] not in (None, ""):
            try:
                target[int_field] = int(body[int_field])
            except (ValueError, TypeError):
                pass

    if "form" in body and isinstance(body["form"], list):
        target["form"] = [str(x).strip().upper() for x in body["form"] if str(x).strip()]

    if "active" in body:
        target["active"] = bool(body["active"])
    if "manual_override" in body:
        target["manual_override"] = bool(body["manual_override"])

    if "last_match" in body and isinstance(body["last_match"], dict):
        lm = body["last_match"]
        h_score = lm.get("home_score")
        a_score = lm.get("away_score")
        target["last_match"] = {
            "home_team": str(lm.get("home_team") or "AS Durlinsdorf").strip(),
            "away_team": str(lm.get("away_team") or "Adversaire").strip(),
            "home_score": int(h_score) if h_score not in (None, "") else None,
            "away_score": int(a_score) if a_score not in (None, "") else None,
            "opponent": str(lm.get("opponent") or lm.get("away_team") or "Adversaire").strip(),
            "venue": str(lm.get("venue") or "Domicile").strip(),
            "date": str(lm.get("date") or "").strip(),
            "time": str(lm.get("time") or "15:00").strip(),
            "competition": str(lm.get("competition") or target.get("competition") or "").strip(),
            "note": str(lm.get("note") or "").strip(),
        }

    if "next_match" in body and isinstance(body["next_match"], dict):
        nm = body["next_match"]
        target["next_match"] = {
            "home_team": str(nm.get("home_team") or "AS Durlinsdorf").strip(),
            "away_team": str(nm.get("away_team") or "Adversaire").strip(),
            "home_score": None,
            "away_score": None,
            "opponent": str(nm.get("opponent") or nm.get("away_team") or "Adversaire").strip(),
            "venue": str(nm.get("venue") or "Domicile").strip(),
            "date": str(nm.get("date") or "").strip(),
            "time": str(nm.get("time") or "15:00").strip(),
            "competition": str(nm.get("competition") or target.get("competition") or "").strip(),
            "note": str(nm.get("note") or "").strip(),
        }

    if "standings" in body and isinstance(body["standings"], list):
        cleaned_standings = []
        for idx, row in enumerate(body["standings"], start=1):
            if not isinstance(row, dict):
                continue
            club_name = str(row.get("club") or f"Club {idx}").strip()
            gf_val = int(row.get("gf", 0) or 0)
            ga_val = int(row.get("ga", 0) or 0)
            raw_diff = row.get("diff")
            diff_val = int(raw_diff) if raw_diff is not None else (gf_val - ga_val)
            cleaned_standings.append({
                "pos": int(row.get("pos", idx) or idx),
                "club": club_name,
                "pts": int(row.get("pts", 0) or 0),
                "played": int(row.get("played", 0) or 0),
                "won": int(row.get("won", 0) or 0),
                "drawn": int(row.get("drawn", 0) or 0),
                "lost": int(row.get("lost", 0) or 0),
                "gf": gf_val,
                "ga": ga_val,
                "diff": diff_val,
                "is_asd": bool(row.get("is_asd")) or is_asd_team_name(club_name),
            })
        cleaned_standings.sort(key=lambda r: r["pos"])
        target["standings"] = cleaned_standings

    if existing_idx is not None:
        teams[existing_idx] = target
    else:
        teams.append(target)

    save_db(db, broadcast=True)
    return jsonify({"ok": True, "team": target, "state": get_public_state()})


@app.route("/api/admin/teams/<team_id>", methods=["DELETE"])
@admin_required
def api_admin_delete_team(team_id: str) -> Response:
    """Supprime une équipe de la configuration."""
    db = load_db()
    db["teams"] = [t for t in db.get("teams", []) if t.get("id") != team_id]
    save_db(db, broadcast=True)
    return jsonify({"ok": True, "state": get_public_state()})


# -----------------------------------------------------------------------------
# GESTION DE LA CARTE DE LA BUVETTE
# -----------------------------------------------------------------------------
@app.route("/api/admin/buvette/item", methods=["POST"])
@admin_required
def api_admin_save_buvette_item() -> Any:
    """Ajoute ou modifie un produit de la buvette."""
    body = request.get_json(silent=True) or {}
    item_id = str(body.get("id") or "").strip() or f"b-{uuid.uuid4().hex[:6]}"
    name = str(body.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Le nom de l'article est obligatoire."}), 400

    category = str(body.get("category") or "Boissons fraîches").strip()
    fmt = str(body.get("format") or "").strip()
    try:
        price = round(float(str(body.get("price", 0)).replace(",", ".")), 2)
    except ValueError:
        price = 0.0

    available = bool(body.get("available", True))
    is_new = bool(body.get("is_new", False))

    db = load_db()
    cats = db.setdefault("buvette_categories", [
        "Boissons fraîches",
        "Bières & Vins",
        "Restauration / Petite faim",
        "Café & Desserts",
    ])
    if category not in cats:
        cats.append(category)

    items = db.setdefault("buvette_items", [])
    existing_idx = next((i for i, x in enumerate(items) if x.get("id") == item_id), None)

    item_obj = {
        "id": item_id,
        "category": category,
        "name": name,
        "format": fmt,
        "price": price,
        "available": available,
        "is_new": is_new,
    }

    if existing_idx is not None:
        items[existing_idx] = item_obj
    else:
        items.append(item_obj)

    save_db(db, broadcast=True)
    return jsonify({"ok": True, "item": item_obj, "state": get_public_state()})


@app.route("/api/admin/buvette/toggle", methods=["POST"])
@admin_required
def api_admin_toggle_buvette_item() -> Any:
    """Bascule rapidement l'état En stock/Rupture ou le badge Nouveau d'un produit."""
    body = request.get_json(silent=True) or {}
    item_id = str(body.get("id") or "").strip()
    field = str(body.get("field") or "available").strip()

    db = load_db()
    items = db.get("buvette_items", [])
    for item in items:
        if item.get("id") == item_id:
            if field in ("available", "is_new"):
                item[field] = not bool(item.get(field, False))
            break

    save_db(db, broadcast=True)
    return jsonify({"ok": True, "state": get_public_state()})


@app.route("/api/admin/buvette/item/<item_id>", methods=["DELETE"])
@admin_required
def api_admin_delete_buvette_item(item_id: str) -> Response:
    """Supprime un produit de la buvette."""
    db = load_db()
    db["buvette_items"] = [x for x in db.get("buvette_items", []) if x.get("id") != item_id]
    save_db(db, broadcast=True)
    return jsonify({"ok": True, "state": get_public_state()})


@app.route("/api/admin/buvette/categories", methods=["POST"])
@admin_required
def api_admin_buvette_categories() -> Any:
    """Met à jour la liste des catégories de la buvette."""
    body = request.get_json(silent=True) or {}
    categories = body.get("categories")
    if isinstance(categories, list) and categories:
        clean_cats = [str(c).strip() for c in categories if str(c).strip()]
        if clean_cats:
            db = load_db()
            db["buvette_categories"] = clean_cats
            save_db(db, broadcast=True)
    return jsonify({"ok": True, "state": get_public_state()})


# -----------------------------------------------------------------------------
# GESTION DU BANDEAU DÉFILANT (TICKER)
# -----------------------------------------------------------------------------
@app.route("/api/admin/ticker", methods=["POST"])
@admin_required
def api_admin_save_ticker() -> Any:
    """Ajoute, modifie ou active/désactive un message du bandeau défilant."""
    body = request.get_json(silent=True) or {}
    db = load_db()

    if "ticker_speed_seconds" in body:
        try:
            speed = max(10, min(120, int(body["ticker_speed_seconds"])))
            db.setdefault("settings", {})["ticker_speed_seconds"] = speed
        except (ValueError, TypeError):
            pass

    if "text" in body or "id" in body:
        msg_id = str(body.get("id") or "").strip() or f"msg-{uuid.uuid4().hex[:6]}"
        messages = db.setdefault("ticker_messages", [])
        existing_idx = next((i for i, m in enumerate(messages) if m.get("id") == msg_id), None)

        if existing_idx is not None:
            if "text" in body and str(body["text"]).strip():
                messages[existing_idx]["text"] = str(body["text"]).strip()
            if "active" in body:
                messages[existing_idx]["active"] = bool(body["active"])
        else:
            text = str(body.get("text") or "").strip()
            if text:
                messages.append({
                    "id": msg_id,
                    "text": text,
                    "active": bool(body.get("active", True)),
                })

    save_db(db, broadcast=True)
    return jsonify({"ok": True, "state": get_public_state()})


@app.route("/api/admin/ticker/<msg_id>", methods=["DELETE"])
@admin_required
def api_admin_delete_ticker(msg_id: str) -> Response:
    """Supprime un message du bandeau défilant."""
    db = load_db()
    db["ticker_messages"] = [m for m in db.get("ticker_messages", []) if m.get("id") != msg_id]
    save_db(db, broadcast=True)
    return jsonify({"ok": True, "state": get_public_state()})


# -----------------------------------------------------------------------------
# GESTION DES SPONSORS (UPLOAD DEPUIS LA GALERIE DU SMARTPHONE)
# -----------------------------------------------------------------------------
@app.route("/api/admin/sponsors", methods=["POST"])
@admin_required
def api_admin_save_sponsor() -> Any:
    """
    Ajoute ou met à jour un sponsor.
    Supporte `multipart/form-data` (upload d'image depuis smartphone) ou `application/json`.
    """
    db = load_db()
    sponsors = db.setdefault("sponsors", [])

    if request.content_type and "multipart/form-data" in request.content_type:
        sp_id = str(request.form.get("id") or "").strip() or f"sp-{uuid.uuid4().hex[:6]}"
        name = str(request.form.get("name") or "").strip()
        tagline = str(request.form.get("tagline") or "").strip()
        try:
            duration = max(3, min(60, int(request.form.get("duration_seconds", 8))))
        except ValueError:
            duration = 8
        active = str(request.form.get("active", "true")).lower() in ("true", "1", "yes", "on")

        logo_url = str(request.form.get("logo_url") or "").strip()
        file = request.files.get("logo_file")
        if file and file.filename and allowed_file(file.filename):
            ext = file.filename.rsplit(".", 1)[1].lower()
            raw_bytes = file.read()
            if raw_bytes:
                safe_name = secure_filename(f"sponsor_{sp_id}_{int(time.time())}.{ext}")
                save_path = os.path.join(UPLOAD_SPONSORS_DIR, safe_name)
                try:
                    with open(save_path, "wb") as f:
                        f.write(raw_bytes)
                except OSError:
                    pass
                logo_url = file_bytes_to_data_uri(raw_bytes, ext)
    else:
        body = request.get_json(silent=True) or {}
        sp_id = str(body.get("id") or "").strip() or f"sp-{uuid.uuid4().hex[:6]}"
        name = str(body.get("name") or "").strip()
        tagline = str(body.get("tagline") or "").strip()
        try:
            duration = max(3, min(60, int(body.get("duration_seconds", 8))))
        except ValueError:
            duration = 8
        active = bool(body.get("active", True))
        logo_url = str(body.get("logo_url") or "").strip()

    existing_idx = next((i for i, s in enumerate(sponsors) if s.get("id") == sp_id), None)
    if existing_idx is not None:
        current = sponsors[existing_idx]
        if name:
            current["name"] = name
        if tagline or "tagline" in (request.form if request.form else (request.get_json(silent=True) or {})):
            current["tagline"] = tagline
        if logo_url:
            current["logo_url"] = logo_url
        current["duration_seconds"] = duration
        current["active"] = active
    else:
        if not name:
            return jsonify({"ok": False, "error": "Le nom du sponsor est requis."}), 400
        sponsors.append({
            "id": sp_id,
            "name": name,
            "tagline": tagline or "Partenaire Officiel AS Durlinsdorf",
            "logo_url": logo_url or "/static/logo_asd.png",
            "duration_seconds": duration,
            "active": active,
        })

    save_db(db, broadcast=True)
    return jsonify({"ok": True, "state": get_public_state()})


@app.route("/api/admin/sponsors/<sp_id>", methods=["DELETE"])
@admin_required
def api_admin_delete_sponsor(sp_id: str) -> Response:
    """Supprime un sponsor."""
    db = load_db()
    db["sponsors"] = [s for s in db.get("sponsors", []) if s.get("id") != sp_id]
    save_db(db, broadcast=True)
    return jsonify({"ok": True, "state": get_public_state()})


# -----------------------------------------------------------------------------
# GESTION DE L'ENCART "ÉVÉNEMENT À LA UNE"
# -----------------------------------------------------------------------------
@app.route("/api/admin/event", methods=["POST"])
@admin_required
def api_admin_save_event() -> Response:
    """Met à jour l'encart 'Événement à la une' (texte + affiche optionnelle depuis smartphone)."""
    db = load_db()
    ev = db.setdefault("featured_event", {})

    if request.content_type and "multipart/form-data" in request.content_type:
        for field in ("badge", "title", "subtitle", "description", "highlight_price"):
            if field in request.form:
                ev[field] = str(request.form.get(field) or "").strip()
        if "enabled" in request.form:
            ev["enabled"] = str(request.form.get("enabled")).lower() in ("true", "1", "yes", "on")

        file = request.files.get("image_file")
        if file and file.filename and allowed_file(file.filename):
            ext = file.filename.rsplit(".", 1)[1].lower()
            raw_bytes = file.read()
            if raw_bytes:
                safe_name = secure_filename(f"event_{int(time.time())}.{ext}")
                save_path = os.path.join(UPLOAD_EVENTS_DIR, safe_name)
                try:
                    with open(save_path, "wb") as f:
                        f.write(raw_bytes)
                except OSError:
                    pass
                ev["image_url"] = file_bytes_to_data_uri(raw_bytes, ext)
    else:
        body = request.get_json(silent=True) or {}
        for field in ("badge", "title", "subtitle", "description", "highlight_price", "image_url"):
            if field in body:
                ev[field] = str(body[field] or "").strip()
        if "enabled" in body:
            ev["enabled"] = bool(body["enabled"])

    save_db(db, broadcast=True)
    return jsonify({"ok": True, "state": get_public_state()})


# -----------------------------------------------------------------------------
# GESTION DU LOGO OFFICIEL DU CLUB (UPLOAD PERSONNALISÉ OU SYNCHRO FFF)
# -----------------------------------------------------------------------------
@app.route("/api/admin/logo", methods=["POST"])
@admin_required
def api_admin_upload_logo() -> Any:
    """Importe un logo personnalisé (.png, .jpg, .svg, .webp) et met à jour la TV en temps réel."""
    global state_version
    file = request.files.get("logo_file")
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "Aucun fichier image sélectionné."}), 400
    if not allowed_file(file.filename):
        return jsonify({"ok": False, "error": "Format non supporté (utilisez PNG, JPG, SVG ou WEBP)."}), 400

    ext = file.filename.rsplit(".", 1)[1].lower()
    raw_bytes = file.read()
    if not raw_bytes:
        return jsonify({"ok": False, "error": "Le fichier envoyé est vide."}), 400

    os.makedirs(os.path.dirname(CLUB_LOGO_PATH), exist_ok=True)

    saved_bytes = raw_bytes
    if ext != "svg":
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(raw_bytes)).convert("RGBA")
            out_buf = io.BytesIO()
            img.save(out_buf, format="PNG")
            saved_bytes = out_buf.getvalue()
            ext = "png"
        except Exception as exc:
            logger.warning("Sauvegarde directe du logo sans conversion Pillow : %s", exc)

    try:
        with open(CLUB_LOGO_PATH, "wb") as f:
            f.write(saved_bytes)
    except OSError:
        pass

    db = load_db()
    db.setdefault("settings", {})["club_logo_b64"] = file_bytes_to_data_uri(saved_bytes, ext)
    save_db(db, broadcast=True)

    return jsonify({
        "ok": True,
        "club_logo_url": get_club_logo_url(),
        "state": get_public_state(),
    })


@app.route("/api/admin/logo/fff", methods=["POST"])
@admin_required
def api_admin_restore_fff_logo() -> Any:
    """Retélécharge le blason officiel depuis l'API FFF (`https://api-dofa.fff.fr/api/clubs/9029`)."""
    cl_no = fff_scraper.find_club_cl_no()
    path = fff_scraper.fetch_and_save_club_logo(cl_no=cl_no, dest_path=CLUB_LOGO_PATH, force=True)
    if not path:
        return jsonify({"ok": False, "error": "Impossible de récupérer le blason sur l'API FFF."}), 500

    db = load_db()
    db.setdefault("settings", {}).pop("club_logo_b64", None)
    save_db(db, broadcast=True)

    return jsonify({
        "ok": True,
        "club_logo_url": get_club_logo_url(),
        "state": get_public_state(),
    })


@app.route("/api/admin/reload-tv", methods=["POST"])
@admin_required
def api_admin_reload_tv() -> Any:
    """Ordonne à tous les écrans Smart TV (`/tv`) connectés d'exécuter un `window.location.reload()` à distance."""
    global state_version, tv_reload_id
    tv_reload_id += 1
    state_version = int(time.time() * 1000)
    notify_sse_clients()
    return jsonify({"ok": True, "tv_reload_id": tv_reload_id, "state": get_public_state()})


# =============================================================================
# PLANIFICATEUR D'ARRIÈRE-PLAN FFF (AVEC CACHE ANTI-SURCHARGE API)
# =============================================================================
def background_scheduler_loop() -> None:
    """
    Boucle démon légère en arrière-plan qui :
    - Vérifie au démarrage si le cache `database.json` contient déjà les classements FFF.
    - Synchronise périodiquement les données de la FFF (toutes les 30 min le week-end après-midi,
      toutes les 2h en semaine) sans jamais surcharger l'API officielle.
    """
    logger.info("Démarrage du planificateur Cloud de synchronisation FFF en tâche de fond.")
    time.sleep(3)
    try:
        initial_teams = load_db().get("teams", [])
        if not initial_teams or not any(t.get("standings") for t in initial_teams):
            logger.info("Aucune équipe classée en cache au démarrage : synchronisation FFF initiale...")
            run_fff_sync(force_manual=False)
    except Exception as exc:
        logger.warning("Erreur lors de la synchronisation FFF initiale : %s", exc)

    while True:
        time.sleep(60)
        try:
            if should_auto_sync(last_fff_sync_epoch):
                logger.info("Déclenchement automatique de la synchronisation FFF planifiée...")
                run_fff_sync(force_manual=False)
        except Exception as exc:
            logger.error("Erreur dans la boucle de synchronisation FFF : %s", exc)


scheduler_thread = threading.Thread(target=background_scheduler_loop, daemon=True)
scheduler_thread.start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    base_url = get_public_base_url()
    logger.info("=================================================================")
    logger.info(" TABLEAU DE BORD CLOUD AS DURLINSDORF (DEPUIS 1969) DÉMARRÉ")
    logger.info(" -> Vue Smart TV (/tv) : %s/tv", base_url)
    logger.info(" -> Admin (/admin)     : %s/admin (PIN: %s)", base_url, get_configured_pin())
    logger.info("=================================================================")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)
