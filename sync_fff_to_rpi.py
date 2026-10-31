"""
Synchronise les vraies données officielles FFF (cl_no = 9029, A.S. Durlinsdorf #523732)
sur le PC, met à jour `database.json` local, et injecte immédiatement les 6 équipes
(résultats, prochains matchs et classements complets) sur le Raspberry Pi (192.168.1.96:5000).
"""

from __future__ import annotations

import json
import os
import requests
from fff_scraper import FFFScraper

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.json")
RPI_URL = os.environ.get("RPI_URL", "http://192.168.1.96:5000")
ADMIN_PIN = os.environ.get("ADMIN_PIN", "1969")


def main() -> None:
    scraper = FFFScraper()
    with open(DB_PATH, "r", encoding="utf-8") as f:
        db = json.load(f)

    print("1. Interrogation de l'API officielle DOFA FFF (https://api-dofa.fff.fr/api/)...")
    report = scraper.sync_all_teams(db.get("teams", []), force_manual=True)
    teams = [
        t for t in (report.get("teams") or [])
        if isinstance(t.get("standings"), list) and len(t["standings"]) > 0
    ]

    print(f"   -> cl_no détecté : {report.get('cl_no')} | Équipes avec classement complet : {len(teams)}")
    for t in teams:
        st = t.get("standings", [])
        lm = t.get("last_match", {})
        nm = t.get("next_match", {})
        print(
            f"   * {t['id']} | {t['name']} ({t['competition']}) -> {len(st)} clubs classés\n"
            f"     - Dernier match  : {lm.get('home_team')} {lm.get('home_score')} - {lm.get('away_score')} {lm.get('away_team')} ({lm.get('date')})\n"
            f"     - Prochain match : {nm.get('home_team')} vs {nm.get('away_team')} ({nm.get('date')} à {nm.get('time')})"
        )

    if not teams:
        print("ERREUR : Aucune équipe récupérée.")
        return

    db["teams"] = teams
    db.setdefault("settings", {})["last_fff_sync"] = report["last_sync"]
    db["settings"]["fff_sync_status"] = report["status"]
    db["settings"]["club_cl_no"] = report["cl_no"]

    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    print(f"\n2. Fichier local {DB_PATH} mis à jour avec succès ({len(teams)} équipes).")

    print(f"\n3. Envoi des {len(teams)} équipes et de leurs classements au Raspberry Pi ({RPI_URL})...")
    headers = {"X-Admin-Pin": ADMIN_PIN, "Content-Type": "application/json"}
    try:
        requests.get(f"{RPI_URL}/api/state", timeout=4)
    except Exception:
        pass

    for t in teams:
        resp = requests.post(f"{RPI_URL}/api/admin/teams", headers=headers, json=t, timeout=10)
        print(f"   -> Équipe {t['id']} ({t['short_name']}) envoyée : HTTP {resp.status_code}")

    # Rafraîchissement instantané de l'écran TV Kiosk
    r_reload = requests.post(f"{RPI_URL}/api/admin/reload-tv", headers=headers, timeout=10)
    print(f"   -> Ordre de rechargement envoyé à l'écran /tv : HTTP {r_reload.status_code}")

    # Vérification finale de l'état public servi à la TV du Raspberry Pi
    state = requests.get(f"{RPI_URL}/api/state", timeout=10).json()
    rpi_teams = state.get("teams", [])
    print(f"\n4. VÉRIFICATION FINALE SUR LE RASPBERRY PI (/api/state) : {len(rpi_teams)} équipes actives :")
    for rt in rpi_teams:
        st_rows = rt.get("standings", [])
        asd_row = next((r for r in st_rows if r.get("is_asd")), {})
        print(
            f"   [OK] {rt['id']} ({rt['name']}) : {len(st_rows)} lignes de classement "
            f"(ASD : {asd_row.get('pos')}e, {asd_row.get('pts')} pts, diff {asd_row.get('diff')}) | "
            f"Dernier match : {rt.get('last_match', {}).get('date')} | "
            f"Prochain match : {rt.get('next_match', {}).get('date')}"
        )


if __name__ == "__main__":
    main()
