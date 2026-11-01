"""
Pont de synchronisation FFF -> Cloud PythonAnywhere (`sync_to_cloud.py`)
Club : AS Durlinsdorf (Fondé en 1969 — Affiliation FFF #523732 — cl_no = 9029)

Fonctionnement :
1. Exécute `fff_scraper.py` (en local sur PC ou via GitHub Actions) pour récupérer
   les vrais classements, résultats joués et prochains matchs de toutes les équipes
   de l'AS Durlinsdorf sur l'API officielle `https://api-dofa.fff.fr/api/`.
2. Met à jour `database.json` localement si présent.
3. Envoie les données en `POST` JSON vers `https://metzgesa.pythonanywhere.com/api/push-fff-cache`.
4. Si la route `/api/push-fff-cache` n'est pas encore déployée sur PythonAnywhere (HTTP 404),
   effectue automatiquement un repli via `/api/admin/teams` pour mettre à jour le serveur quand même.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Tuple

import requests
import urllib3

from fff_scraper import FFF_BROWSER_HEADERS, FFF_FALLBACK_PROXIES, FFFScraper

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuration Windows UTF-8 dans le terminal
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.json")

CLOUD_BASE_URL = os.environ.get("ASD_CLOUD_URL", "https://metzgesa.pythonanywhere.com").strip().rstrip("/")
PUSH_ENDPOINT = f"{CLOUD_BASE_URL}/api/push-fff-cache"
ADMIN_PIN = os.environ.get("ASD_ADMIN_PIN", "1969").strip()

# En-têtes HTTP complets imitant un navigateur français venant du site officiel FFF
BROWSER_HEADERS: Dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/ld+json, application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Referer": "https://epreuves.fff.fr/",
    "Origin": "https://epreuves.fff.fr",
}

# Liste des proxies de secours utilisés automatiquement si l'appel direct à api-dofa.fff.fr échoue ou renvoie 403/429
FALLBACK_PROXIES: Tuple[str, ...] = (
    "https://api.codetabs.com/v1/proxy?quest=",
    "https://api.allorigins.win/raw?url=",
    "https://corsproxy.io/?",
)


def load_local_teams() -> List[Dict[str, Any]]:
    """Charge les équipes existantes de `database.json` si présent pour préserver les préférences."""
    if not os.path.exists(DB_PATH):
        return []
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            db = json.load(f)
            return db.get("teams", []) if isinstance(db, dict) else []
    except Exception:
        return []


def update_local_db(report: Dict[str, Any], valid_teams: List[Dict[str, Any]]) -> None:
    """Met à jour `database.json` localement si le fichier existe."""
    if not os.path.exists(DB_PATH):
        return
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            db = json.load(f)
        if not isinstance(db, dict):
            return
        db["teams"] = valid_teams
        settings = db.setdefault("settings", {})
        settings["last_fff_sync"] = report.get("last_sync")
        settings["fff_sync_status"] = "online"
        settings["club_cl_no"] = report.get("cl_no", 9029)
        with open(DB_PATH, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"[INFO] Mise à jour locale de database.json ignorée : {exc}")


def main() -> int:
    print("=" * 74)
    print(" PONT DE SYNCHRONISATION FFF -> CLOUD PYTHONANYWHERE (AS DURLINSDORF #523732)")
    print("=" * 74)

    existing_teams = load_local_teams()
    scraper = FFFScraper(
        timeout=15,
        fallback_proxies=FALLBACK_PROXIES,
        verbose=True,
    )
    scraper.std_session.headers.update(BROWSER_HEADERS)

    print("\n1. Interrogation de l'API officielle FFF (https://api-dofa.fff.fr/api/)...")
    print(f"   • En-têtes HTTP configurés : Origin={BROWSER_HEADERS['Origin']} | Accept-Language={BROWSER_HEADERS['Accept-Language']}")
    print(f"   • Proxies de secours prêts : {', '.join(FALLBACK_PROXIES)}")
    print("   • Détail des tentatives HTTP en direct :")

    report = scraper.sync_all_teams(existing_teams=existing_teams, force_manual=True)
    attempt_logs: List[str] = report.get("attempt_logs") or scraper.attempt_logs
    valid_teams = [
        t for t in (report.get("teams") or [])
        if isinstance(t.get("standings"), list) and len(t["standings"]) > 0
    ]

    print(f"\n   • Total des tentatives HTTP effectuées : {len(attempt_logs)}")

    if not valid_teams or report.get("status") != "online":
        print("ERREUR : Impossible de récupérer les classements officiels depuis l'API FFF.")
        if attempt_logs:
            print("Récapitulatif des tentatives HTTP :")
            for entry in attempt_logs:
                print(f"   - {entry}")
        return 1

    total_standings_rows = sum(len(t.get("standings", [])) for t in valid_teams)
    print(
        f"   -> Succès FFF : cl_no = {report.get('cl_no')} | "
        f"{len(valid_teams)} équipes classées récupérées ({total_standings_rows} lignes de classement au total)\n"
    )

    for idx, t in enumerate(valid_teams, start=1):
        st = t.get("standings", [])
        asd_row = next((r for r in st if r.get("is_asd")), {})
        pos_str = f"{asd_row.get('pos')}e ({asd_row.get('pts')} pts)" if asd_row else "Classé"
        lm = t.get("last_match") or {}
        nm = t.get("next_match") or {}
        lm_str = (
            f"{lm.get('home_team')} {lm.get('home_score')}-{lm.get('away_score')} {lm.get('away_team')} ({lm.get('date')})"
            if lm.get("home_score") is not None
            else "Aucun résultat récent"
        )
        nm_str = (
            f"{nm.get('home_team')} vs {nm.get('away_team')} ({nm.get('date')} à {nm.get('time')})"
            if nm.get("home_team")
            else "Calendrier en attente"
        )
        print(f"   [{idx}] {t['name']} ({t['competition']})")
        print(f"       • Classement : {len(st)} clubs | Position ASD : {pos_str}")
        print(f"       • Dernier    : {lm_str}")
        print(f"       • Prochain   : {nm_str}")

    update_local_db(report, valid_teams)

    print(f"\n2. Envoi des données vers {PUSH_ENDPOINT} ...")
    payload = {
        "cl_no": report.get("cl_no", 9029),
        "affiliation_number": 523732,
        "last_sync": report.get("last_sync"),
        "status": "online",
        "source": "sync_to_cloud.py",
        "force_manual": False,
        "teams": valid_teams,
    }
    headers = {
        "Content-Type": "application/json",
        "X-Admin-Pin": ADMIN_PIN,
    }

    cloud_session = requests.Session()
    try:
        resp = cloud_session.post(PUSH_ENDPOINT, json=payload, headers=headers, timeout=20, verify=False)
        print(f"   [HTTP] [CLOUD/POST] {PUSH_ENDPOINT} -> HTTP {resp.status_code}")
    except Exception as exc:
        print(f"ERREUR RÉSEAU lors de la connexion à {PUSH_ENDPOINT} : {exc}")
        return 1

    if resp.status_code == 200:
        try:
            res_json = resp.json()
        except Exception:
            res_json = {}
        synced_count = res_json.get("synced_count", len(valid_teams))
        print(f"   -> Réponse serveur HTTP 200 : {json.dumps(res_json, ensure_ascii=False)[:180]}...")
        print("\n" + "=" * 74)
        print(
            f" ✅ SYNCHRONISATION CLOUD RÉUSSIE : {synced_count} équipes et {total_standings_rows} clubs classés "
            f"envoyés sur {CLOUD_BASE_URL} !"
        )
        print("=" * 74)
        return 0

    if resp.status_code == 404:
        print(
            f"\n[ATTENTION] La route {PUSH_ENDPOINT} a renvoyé HTTP 404 (app.py sur PythonAnywhere "
            f"n'a pas encore été rechargé avec la nouvelle route)."
        )
        print(f"   -> Tentative d'envoi direct via {CLOUD_BASE_URL}/api/admin/teams (Code PIN {ADMIN_PIN})...")
        pushed_fallback = 0
        for t in valid_teams:
            r_team = requests.post(
                f"{CLOUD_BASE_URL}/api/admin/teams",
                json=t,
                headers=headers,
                timeout=15,
                verify=False,
            )
            if r_team.status_code == 200:
                pushed_fallback += 1
        requests.post(f"{CLOUD_BASE_URL}/api/admin/reload-tv", json={}, headers=headers, timeout=10, verify=False)
        print(
            f"   -> Repli terminé : {pushed_fallback}/{len(valid_teams)} équipes injectées sur {CLOUD_BASE_URL}.\n"
            f"   -> Pensez à ré-uploader `app.py` et `templates/tv.html` sur PythonAnywhere puis cliquer sur Reload."
        )
        return 0 if pushed_fallback == len(valid_teams) else 1

    print(f"ERREUR HTTP {resp.status_code} depuis {PUSH_ENDPOINT} : {resp.text[:300]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
