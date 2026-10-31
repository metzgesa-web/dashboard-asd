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
