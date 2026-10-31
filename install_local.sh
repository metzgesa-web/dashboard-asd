#!/usr/bin/env bash
# =============================================================================
# INSTALLATION AUTOMATIQUE SANS MOT DE PASSE SUDO — AS DURLINSDORF (1969)
# Télécharge, installe et lance le Tableau de Bord TV + Mode Kiosk 1080p
# =============================================================================
set -e

PC_URL="http://192.168.1.29:8080"
APP_DIR="$HOME/asd-dashboard"
PORT=5000

echo "======================================================================"
echo "  INSTALLATION TABLEAU DE BORD TV — AS DURLINSDORF (DEPUIS 1969)"
echo "======================================================================"

# 1. Création du dossier et téléchargement INCONDITIONNEL de l'archive depuis le PC (192.168.1.29:8080)
mkdir -p "$APP_DIR"
cd "$APP_DIR"

echo "[1/5] Téléchargement de la dernière version depuis $PC_URL/asd-dashboard.tar.gz ..."
curl -fsSL "$PC_URL/asd-dashboard.tar.gz" -o /tmp/asd-dashboard.tar.gz
tar -xzf /tmp/asd-dashboard.tar.gz -C "$APP_DIR"
rm -f /tmp/asd-dashboard.tar.gz
echo " -> Fichiers extraits et mis à jour dans $APP_DIR"

# 2 & 3. Environnement Python (rapide si déjà installé)
if [ -x "$APP_DIR/venv/bin/python" ] && "$APP_DIR/venv/bin/python" -c "import flask" 2>/dev/null; then
    echo "[2/5] Environnement Python déjà prêt (mise à jour rapide)."
    PYTHON_EXEC="$APP_DIR/venv/bin/python"
    GUNICORN_EXEC="$APP_DIR/venv/bin/gunicorn"
else
    if sudo -n true 2>/dev/null; then
        echo "[2/5] Sudo sans mot de passe détecté : vérification des paquets système..."
        sudo -n DEBIAN_FRONTEND=noninteractive apt-get update -y >/dev/null 2>&1 || true
        sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv python3-pip unclutter x11-xserver-utils curl >/dev/null 2>&1 || true
    else
        echo "[2/5] Mode 100% sans sudo activé (installation dans l'espace utilisateur $HOME)."
    fi

    echo "[3/5] Configuration de l'environnement Python et des dépendances..."
    if python3 -m venv "$APP_DIR/venv" 2>/dev/null; then
        "$APP_DIR/venv/bin/pip" install --upgrade pip -q
        "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q
        PYTHON_EXEC="$APP_DIR/venv/bin/python"
        GUNICORN_EXEC="$APP_DIR/venv/bin/gunicorn"
    else
        echo " -> python3-venv absent, installation directe dans l'espace utilisateur (--user)..."
        python3 -m pip install --user --break-system-packages -r "$APP_DIR/requirements.txt" -q 2>/dev/null || \
        python3 -m pip install --user -r "$APP_DIR/requirements.txt" -q
        PYTHON_EXEC="python3"
        GUNICORN_EXEC="$HOME/.local/bin/gunicorn"
    fi
fi

# 3b. Vérification / Téléchargement du binaire Linux ARM cloudflared selon uname -m
mkdir -p "$APP_DIR/bin"
rm -f "$APP_DIR/bin/cloudflared.exe" 2>/dev/null || true
ARCH=$(uname -m)
if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
    CF_BIN="$APP_DIR/bin/cloudflared-linux-arm64"
    CF_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64"
elif [ "$ARCH" = "armv7l" ] || [ "$ARCH" = "armv6l" ]; then
    CF_BIN="$APP_DIR/bin/cloudflared-linux-arm"
    CF_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm"
else
    CF_BIN="$APP_DIR/bin/cloudflared-linux-amd64"
    CF_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
fi
if [ ! -f "$CF_BIN" ] || [ $(wc -c < "$CF_BIN" 2>/dev/null || echo 0) -lt 1000000 ]; then
    echo " -> Téléchargement de cloudflared pour $ARCH ($CF_URL)..."
    curl -fsSL "$CF_URL" -o "$CF_BIN.tmp" && mv "$CF_BIN.tmp" "$CF_BIN"
fi
chmod +x "$CF_BIN" 2>/dev/null || true

# 4. Configuration du démarrage automatique du serveur Flask/Gunicorn
echo "[4/5] Configuration du démarrage automatique du serveur..."
if sudo -n true 2>/dev/null; then
    # Service systemd système si sudo -n est disponible
    sudo -n tee /etc/systemd/system/asd-dashboard.service > /dev/null <<EOF
[Unit]
Description=Tableau de Bord Affichage Dynamique AS Durlinsdorf (1969)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
Environment="PATH=$APP_DIR/venv/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PORT=$PORT"
Environment="PYTHONUNBUFFERED=1"
ExecStart=$PYTHON_EXEC $APP_DIR/app.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
    sudo -n systemctl daemon-reload || true
    sudo -n systemctl enable asd-dashboard.service || true
    sudo -n systemctl restart asd-dashboard.service || true
    if command -v raspi-config >/dev/null 2>&1; then
        sudo -n raspi-config nonint do_blanking 1 || true
    fi
else
    # Service systemd utilisateur (sans sudo)
    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/asd-dashboard.service" <<EOF
[Unit]
Description=Tableau de Bord Affichage Dynamique AS Durlinsdorf (1969)
After=network.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
Environment="PATH=$APP_DIR/venv/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PORT=$PORT"
Environment="PYTHONUNBUFFERED=1"
ExecStart=$PYTHON_EXEC $APP_DIR/app.py
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable --now asd-dashboard.service 2>/dev/null || true
fi

# 5. Configuration du script Kiosk plein écran + Autostart graphique (X11 & Wayland/Bookworm)
echo "[5/5] Configuration du Mode Kiosk plein écran 1080p..."
if command -v chromium >/dev/null 2>&1; then
    CHROMIUM_BIN="chromium"
elif command -v chromium-browser >/dev/null 2>&1; then
    CHROMIUM_BIN="chromium-browser"
else
    CHROMIUM_BIN="chromium-browser"
fi

cat > "$APP_DIR/start_kiosk.sh" <<EOF
#!/usr/bin/env bash
# Script de démarrage autonome (Serveur + Kiosk TV 1080p) — AS Durlinsdorf
# Corrige l'écran blanc sur Raspberry Pi OS (X11 & Wayland/Bookworm)

cd "$APP_DIR"

# 1. Supprimer unclutter -root (qui crée une fenêtre blanche opaque sous Wayland/Xwayland)
pkill -9 unclutter 2>/dev/null || true

# 2. Détecter l'affichage graphique actif (X11 et/ou Wayland sur Raspberry Pi OS Bookworm)
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

# 3. Si une mise à jour vient d'être extraite (fichier .update_flag présent), redémarrer proprement app.py + cloudflared
if [ -f "$APP_DIR/.update_flag" ]; then
    rm -f "$APP_DIR/.update_flag"
    pkill -9 -f "cloudflared" 2>/dev/null || true
    pkill -9 -f "$APP_DIR/app.py" 2>/dev/null || true
    sleep 1
    if sudo -n true 2>/dev/null && systemctl is-enabled --quiet asd-dashboard.service 2>/dev/null; then
        sudo -n systemctl restart asd-dashboard.service 2>/dev/null || true
    elif systemctl --user is-enabled --quiet asd-dashboard.service 2>/dev/null; then
        systemctl --user restart asd-dashboard.service 2>/dev/null || true
    else
        nohup $PYTHON_EXEC "$APP_DIR/app.py" > "$APP_DIR/server.log" 2>&1 &
    fi
    sleep 2
else
    sleep 2
fi

# 4. Si le serveur Flask ne tourne pas encore sur le port $PORT, le démarrer en tâche de fond
if ! curl -fsS -o /dev/null "http://127.0.0.1:$PORT/api/state" 2>/dev/null; then
    nohup $PYTHON_EXEC "$APP_DIR/app.py" > "$APP_DIR/server.log" 2>&1 &
fi

# 5. Attendre que la page /tv réponde HTTP 200 avant d'ouvrir Chromium
for i in \$(seq 1 45); do
    if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/tv" 2>/dev/null; then
        break
    fi
    sleep 1
done

# 6. Désactiver la mise en veille de l'écran (sans unclutter -root)
if [ -n "\${DISPLAY:-}" ]; then
    xset s off 2>/dev/null || true
    xset -dpms 2>/dev/null || true
    xset s noblank 2>/dev/null || true
fi

# 7. Fermer toute ancienne instance bloquée de Chromium et préconfigurer la langue FR sans traduction
pkill -f "asd-kiosk-profile" 2>/dev/null || true
pkill -9 chromium 2>/dev/null || true
pkill -9 chromium-browser 2>/dev/null || true
rm -rf /tmp/asd-kiosk-profile "\$HOME/.config/chromium/Singleton"* "\$HOME/.cache/chromium/Default/GPUCache" 2>/dev/null || true
mkdir -p /tmp/asd-kiosk-profile/Default
printf '%s\n' '{"translate":{"enabled":false},"translate_blocked_languages":["fr","fr-FR","en","en-US","de"],"intl":{"accept_languages":"fr-FR,fr"},"profile":{"exit_type":"Normal","exited_cleanly":true}}' > /tmp/asd-kiosk-profile/Default/Preferences

export LANG="fr_FR.UTF-8"
export LANGUAGE="fr_FR:fr"

# 8. Lancer Chromium en mode Kiosk sans bulle de traduction et sans bug GPU VC4
exec $CHROMIUM_BIN \\
    --user-data-dir=/tmp/asd-kiosk-profile \\
    --kiosk \\
    --app="http://127.0.0.1:$PORT/tv?v=\$(date +%s)" \\
    --lang=fr \\
    --accept-lang=fr-FR,fr \\
    --disable-translate \\
    --disable-features=Translate,TranslateUI,TranslateSubFrames \\
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
    "http://127.0.0.1:$PORT/tv?v=\$(date +%s)"
EOF

chmod +x "$APP_DIR/start_kiosk.sh"

# Autostart standard XDG (compatible LXDE-pi, Wayfire et Labwc sur Raspberry Pi OS)
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/asd-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=AS Durlinsdorf TV Kiosk
Comment=Affichage Dynamique Club-House AS Durlinsdorf
Exec=$APP_DIR/start_kiosk.sh
X-GNOME-Autostart-enabled=true
Terminal=false
EOF

# Redémarrage propre du serveur Flask + Tunnel HTTPS avec la nouvelle version (une seule instance unique)
pkill -9 unclutter 2>/dev/null || true
pkill -9 -f "cloudflared" 2>/dev/null || true
pkill -9 -f "$APP_DIR/app.py" 2>/dev/null || true
sleep 1
if sudo -n true 2>/dev/null; then
    sudo -n systemctl restart asd-dashboard.service 2>/dev/null || true
else
    systemctl --user restart asd-dashboard.service 2>/dev/null || true
fi
sleep 2
if ! curl -fsS -o /dev/null "http://127.0.0.1:$PORT/api/state" 2>/dev/null; then
    nohup $PYTHON_EXEC "$APP_DIR/app.py" > "$APP_DIR/server.log" 2>&1 &
    sleep 3
fi

# Relance immédiate du Kiosk sur l'écran TV branché au Raspberry Pi
nohup "$APP_DIR/start_kiosk.sh" >/dev/null 2>&1 &

echo " -> Attente de l'enregistrement du tunnel Cloudflare (http2)..."
TUNNEL_URL=""
for i in $(seq 1 25); do
    TUNNEL_URL=$($PYTHON_EXEC -c "import urllib.request, json; d=json.loads(urllib.request.urlopen('http://127.0.0.1:$PORT/api/state').read()); print(d.get('remote_tunnel',{}).get('public_admin_url',''))" 2>/dev/null || true)
    if [ -n "$TUNNEL_URL" ]; then
        break
    fi
    sleep 1
done

RPI_IP=$(hostname -I | awk '{print $1}')
echo "======================================================================"
echo " INSTALLATION ET DÉMARRAGE TERMINÉS AVEC SUCCÈS !"
echo " -> Processus cloudflared : $(pgrep -a cloudflared || echo 'En cours de démarrage')"
echo " -> Écran TV 1080p        : http://$RPI_IP:$PORT/tv"
echo " -> Admin Wi-Fi Local     : http://$RPI_IP:$PORT/admin (PIN : 1969)"
echo " -> Admin 4G / Internet   : ${TUNNEL_URL:-En cours d'enregistrement}"
echo "======================================================================"
