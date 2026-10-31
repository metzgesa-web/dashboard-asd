#!/usr/bin/env bash
# =============================================================================
# SCRIPT D'INSTALLATION AUTOMATIQUE — TABLEAU DE BORD AS DURLINSDORF (1969)
# Compatible Raspberry Pi OS (Bookworm & Bullseye - 32-bit / 64-bit)
# =============================================================================
set -euo pipefail

YELLOW='\033[1;33m'
GREEN='\033[1;32m'
RED='\033[1;31m'
NC='\033[0m'

echo -e "${YELLOW}======================================================================${NC}"
echo -e "${YELLOW}   INSTALLATION DU TABLEAU DE BORD TV — AS DURLINSDORF (DEPUIS 1969)  ${NC}"
echo -e "${YELLOW}======================================================================${NC}"

# Détection de l'utilisateur et du dossier du projet
APP_USER="${SUDO_USER:-$(whoami)}"
APP_HOME=$(eval echo "~$APP_USER")
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT=5000

echo -e "${GREEN}[1/6] Mise à jour des paquets système et installation des outils Kiosk...${NC}"
sudo DEBIAN_FRONTEND=noninteractive apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    unclutter \
    x11-xserver-utils \
    xdg-utils \
    curl

# Détection ou installation de Chromium (selon la version de Raspberry Pi OS)
if command -v chromium >/dev/null 2>&1; then
    CHROMIUM_BIN="chromium"
elif command -v chromium-browser >/dev/null 2>&1; then
    CHROMIUM_BIN="chromium-browser"
elif apt-cache show chromium >/dev/null 2>&1; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y chromium
    CHROMIUM_BIN="chromium"
else
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y chromium-browser
    CHROMIUM_BIN="chromium-browser"
fi

echo -e "${GREEN}[2/6] Création de l'environnement virtuel Python (venv) et dépendances...${NC}"
if [ ! -x "$APP_DIR/venv/bin/gunicorn" ]; then
    sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
    sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip
    sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"
else
    echo " -> Environnement virtuel (venv) déjà prêt."
fi

echo -e "${GREEN}[3/6] Création du service systemd (asd-dashboard.service)...${NC}"
sudo tee /etc/systemd/system/asd-dashboard.service > /dev/null <<EOF
[Unit]
Description=Tableau de Bord Affichage Dynamique AS Durlinsdorf (1969)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment="PATH=${APP_DIR}/venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PORT=${PORT}"
Environment="PYTHONUNBUFFERED=1"
ExecStart=${APP_DIR}/venv/bin/gunicorn --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:${PORT} app:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable asd-dashboard.service
sudo systemctl restart asd-dashboard.service

echo -e "${GREEN}[4/6] Désactivation de la mise en veille de l'écran (Screen Blanking)...${NC}"
if command -v raspi-config >/dev/null 2>&1; then
    sudo raspi-config nonint do_blanking 1 || true
fi

echo -e "${GREEN}[5/6] Création du script de lancement Kiosk (start_kiosk.sh)...${NC}"
cat > "$APP_DIR/start_kiosk.sh" <<EOF
#!/usr/bin/env bash
# Script de démarrage Kiosk plein écran pour AS Durlinsdorf

pkill -9 unclutter 2>/dev/null || true
export DISPLAY="\${DISPLAY:-:0}"
export XDG_RUNTIME_DIR="\${XDG_RUNTIME_DIR:-/run/user/\$(id -u)}"
if [ -z "\${WAYLAND_DISPLAY:-}" ]; then
    for wl_sock in wayland-0 wayland-1; do
        if [ -S "\$XDG_RUNTIME_DIR/\$wl_sock" ]; then
            export WAYLAND_DISPLAY="\$wl_sock"
            break
        fi
    done
fi

sleep 3

# Attendre que la page /tv réponde HTTP 200
for i in \$(seq 1 45); do
    if curl -fsS -o /dev/null "http://127.0.0.1:${PORT}/tv" 2>/dev/null; then
        break
    fi
    sleep 1
done

if [ -n "\${DISPLAY:-}" ]; then
    xset s off 2>/dev/null || true
    xset -dpms 2>/dev/null || true
    xset s noblank 2>/dev/null || true
fi

pkill -f "asd-kiosk-profile" 2>/dev/null || true
pkill -9 chromium 2>/dev/null || true
pkill -9 chromium-browser 2>/dev/null || true
rm -rf /tmp/asd-kiosk-profile "\$HOME/.config/chromium/Singleton"* "\$HOME/.cache/chromium/Default/GPUCache" 2>/dev/null || true
mkdir -p /tmp/asd-kiosk-profile

# Lancer Chromium en mode Kiosk 1080p plein écran sans bug d'écran blanc GPU
exec ${CHROMIUM_BIN} \\
    --user-data-dir=/tmp/asd-kiosk-profile \\
    --kiosk \\
    --app="http://127.0.0.1:${PORT}/tv" \\
    --disable-gpu \\
    --disable-gpu-compositing \\
    --disable-software-rasterizer \\
    --password-store=basic \\
    --no-first-run \\
    --no-default-browser-check \\
    --noerrdialogs \\
    --disable-infobars \\
    --disable-session-crashed-bubble \\
    --disable-restore-session-state \\
    --check-for-update-interval=31536000 \\
    --autoplay-policy=no-user-gesture-required \\
    --start-fullscreen \\
    "http://127.0.0.1:${PORT}/tv"
EOF

chmod +x "$APP_DIR/start_kiosk.sh"
chown "$APP_USER:$APP_USER" "$APP_DIR/start_kiosk.sh"

echo -e "${GREEN}[6/6] Configuration de l'ouverture automatique au démarrage du bureau...${NC}"
AUTOSTART_DIR="$APP_HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"

cat > "$AUTOSTART_DIR/asd-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=AS Durlinsdorf TV Kiosk
Comment=Affichage Dynamique Club-House AS Durlinsdorf
Exec=${APP_DIR}/start_kiosk.sh
X-GNOME-Autostart-enabled=true
Terminal=false
EOF

chown -R "$APP_USER:$APP_USER" "$AUTOSTART_DIR"

LOCAL_IP=$(hostname -I | awk '{print $1}')
echo -e "${YELLOW}======================================================================${NC}"
echo -e "${GREEN} INSTALLATION TERMINÉE AVEC SUCCÈS !${NC}"
echo -e " -> Vue TV Locale      : ${YELLOW}http://127.0.0.1:${PORT}/tv${NC}"
echo -e " -> Admin Smartphone   : ${YELLOW}http://${LOCAL_IP}:${PORT}/admin${NC} (Code PIN : 1969)"
echo -e " -> Statut du service  : ${GREEN}$(sudo systemctl is-active asd-dashboard.service)${NC}"
echo -e " Redémarrez le Raspberry Pi avec 'sudo reboot' pour lancer le mode Kiosk."
echo -e "${YELLOW}======================================================================${NC}"
