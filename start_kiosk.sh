#!/usr/bin/env bash
# Script de démarrage 100% autonome (Serveur Flask + Kiosk TV 1080p) — AS Durlinsdorf

USER_NAME="${USER:-$(whoami)}"
APP_DIR="/home/$USER_NAME/asd-dashboard"
if [ ! -d "$APP_DIR" ]; then
    APP_DIR="$HOME/asd-dashboard"
fi
PORT="${PORT:-5000}"

# 1. Se placer dans /home/$USER/asd-dashboard
cd "$APP_DIR" || exit 1

# Auto-configuration du démarrage automatique (X11 / Wayland / labwc)
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/asd-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Tableau de Bord TV AS Durlinsdorf
Comment=Affichage Dynamique 1080p Club-House ASD 1969
Exec=$APP_DIR/start_kiosk.sh
Terminal=false
X-GNOME-Autostart-enabled=true
EOF

mkdir -p "$HOME/.config/labwc"
touch "$HOME/.config/labwc/autostart"
sed -i '/start_kiosk\.sh/d' "$HOME/.config/labwc/autostart" 2>/dev/null || true
echo "$APP_DIR/start_kiosk.sh &" >> "$HOME/.config/labwc/autostart"
chmod +x "$HOME/.config/labwc/autostart" 2>/dev/null || true

# Création / mise à jour de daily_reboot.sh et enregistrement dans le crontab utilisateur à 03h00 (sans doublon)
cat > "$APP_DIR/daily_reboot.sh" <<'EOF_REBOOT'
#!/usr/bin/env bash
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
EOF_REBOOT
chmod +x "$APP_DIR/daily_reboot.sh" 2>/dev/null || true

CRON_LINE="0 3 * * * /bin/bash /home/$USER_NAME/asd-dashboard/daily_reboot.sh >> /home/$USER_NAME/asd-dashboard/reboot.log 2>&1"
(crontab -l 2>/dev/null | grep -v "daily_reboot.sh" ; echo "$CRON_LINE") | crontab - 2>/dev/null || true

# Autorisation de redémarrage sans mot de passe (sudo -n reboot) si sudoers est accessible
if sudo -n true 2>/dev/null; then
    echo "$USER_NAME ALL=(ALL) NOPASSWD: /sbin/reboot, /usr/sbin/reboot, /bin/systemctl" | sudo -n tee /etc/sudoers.d/010_asd_reboot >/dev/null 2>&1 || true
    sudo -n chmod 0440 /etc/sudoers.d/010_asd_reboot 2>/dev/null || true
fi

# Pré-enregistrement automatique du Wi-Fi du Club-House ("Livebox-20f2" / "Durlinsdorf1969")
# sans supprimer ni déconnecter le Wi-Fi actuellement actif
if command -v nmcli >/dev/null 2>&1; then
    nmcli connection delete "ClubHouse-ASD" 2>/dev/null || true
    nmcli connection add type wifi con-name "ClubHouse-ASD" ifname wlan0 ssid "Livebox-20f2" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "Durlinsdorf1969" connection.autoconnect yes 2>/dev/null || true
fi
if command -v wpa_cli >/dev/null 2>&1; then
    if ! wpa_cli -i wlan0 list_networks 2>/dev/null | grep -Fq "Livebox-20f2"; then
        NET_ID="$(wpa_cli -i wlan0 add_network 2>/dev/null | tail -n 1)"
        if [[ "$NET_ID" =~ ^[0-9]+$ ]]; then
            wpa_cli -i wlan0 set_network "$NET_ID" ssid '"Livebox-20f2"' >/dev/null 2>&1 || true
            wpa_cli -i wlan0 set_network "$NET_ID" psk '"Durlinsdorf1969"' >/dev/null 2>&1 || true
            wpa_cli -i wlan0 enable_network "$NET_ID" >/dev/null 2>&1 || true
            wpa_cli -i wlan0 save_config >/dev/null 2>&1 || true
        fi
    fi
fi

# Nettoyage anciens processus cloudflared et préparation bin/ngrok
pkill -9 unclutter 2>/dev/null || true
pkill -9 -f "cloudflared" 2>/dev/null || true
mkdir -p "$APP_DIR/bin"
rm -f "$APP_DIR/bin/cloudflared"* 2>/dev/null || true
chmod +x "$APP_DIR/bin/ngrok" 2>/dev/null || true

# Détection de l'affichage graphique (X11 et/ou Wayland)
export DISPLAY="${DISPLAY:-:0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
if [ -z "${WAYLAND_DISPLAY:-}" ]; then
    for wl_sock in wayland-0 wayland-1; do
        if [ -S "$XDG_RUNTIME_DIR/$wl_sock" ]; then
            export WAYLAND_DISPLAY="$wl_sock"
            break
        fi
    done
fi

# 2. Lancer d'abord le serveur Python en tâche de fond : ./venv/bin/python app.py > server.log 2>&1 &
if [ -f "$APP_DIR/.update_flag" ]; then
    rm -f "$APP_DIR/.update_flag"
    pkill -9 -f "cloudflared" 2>/dev/null || true
    pkill -9 -f "ngrok" 2>/dev/null || true
    pkill -9 -f "app.py" 2>/dev/null || true
    sleep 1
fi

if ! curl -fsS -o /dev/null "http://127.0.0.1:$PORT/tv" 2>/dev/null; then
    pkill -9 -f "app.py" 2>/dev/null || true
    if [ -x "./venv/bin/python" ]; then
        nohup ./venv/bin/python app.py > server.log 2>&1 &
    else
        nohup python3 app.py > server.log 2>&1 &
    fi
fi

# 3. Attendre 5 secondes puis vérifier avec curl que http://127.0.0.1:5000/tv répond
sleep 5
for i in $(seq 1 45); do
    if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/tv" 2>/dev/null; then
        echo "Serveur AS Durlinsdorf opérationnel sur http://127.0.0.1:$PORT/tv"
        break
    fi
    sleep 1
done

# Synchronisation FFF automatique en tâche de fond dès le démarrage du Kiosk
curl -fsS -X POST -H "X-Admin-Pin: 1969" -H "Content-Type: application/json" -d "{}" "http://127.0.0.1:$PORT/api/admin/fff/sync" >/dev/null 2>&1 &

# Désactiver la mise en veille de l'écran
if [ -n "${DISPLAY:-}" ]; then
    xset s off 2>/dev/null || true
    xset -dpms 2>/dev/null || true
    xset s noblank 2>/dev/null || true
fi

# 4. Lancer Chromium en mode Kiosk plein écran (sans bulle de traduction)
if command -v chromium >/dev/null 2>&1; then
    CHROMIUM_BIN="chromium"
elif command -v chromium-browser >/dev/null 2>&1; then
    CHROMIUM_BIN="chromium-browser"
else
    CHROMIUM_BIN="chromium-browser"
fi

pkill -f "asd-kiosk-profile" 2>/dev/null || true
pkill -9 chromium 2>/dev/null || true
pkill -9 chromium-browser 2>/dev/null || true
rm -rf /tmp/asd-kiosk-profile "$HOME/.config/chromium/Singleton"* "$HOME/.cache/chromium/Default/GPUCache" 2>/dev/null || true
mkdir -p /tmp/asd-kiosk-profile/Default
printf '%s\n' '{"translate":{"enabled":false},"translate_blocked_languages":["fr","fr-FR","en","en-US","de"],"intl":{"accept_languages":"fr-FR,fr"},"profile":{"exit_type":"Normal","exited_cleanly":true}}' > /tmp/asd-kiosk-profile/Default/Preferences

export LANG="fr_FR.UTF-8"
export LANGUAGE="fr_FR:fr"

exec "$CHROMIUM_BIN" \
    --user-data-dir=/tmp/asd-kiosk-profile \
    --kiosk \
    --app="http://127.0.0.1:$PORT/tv?v=$(date +%s)" \
    --lang=fr \
    --accept-lang=fr-FR,fr \
    --disable-translate \
    --disable-features=Translate,TranslateUI,TranslateSubFrames \
    --disable-gpu \
    --disable-gpu-compositing \
    --disable-software-rasterizer \
    --password-store=basic \
    --no-first-run \
    --no-default-browser-check \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-restore-session-state \
    --check-for-update-interval=31536000 \
    --autoplay-policy=no-user-gesture-required \
    --start-fullscreen \
    "http://127.0.0.1:$PORT/tv?v=$(date +%s)"
