"""
Module de synchronisation FFF officiel (`https://api-dofa.fff.fr/api/`)
pour l'AS Durlinsdorf (Fondé en 1969 — Affiliation FFF N° 523732 — cl_no = 9029)

Fonctionnement 100 % connecté à l'API publique DOFA de la FFF :
1. Identification automatique du `cl_no` exact de l'AS Durlinsdorf (`9029`, affiliation `523732`)
   via `https://api-dofa.fff.fr/api/clubs`.
2. Récupération de la liste officielle des équipes engagées :
   `https://api-dofa.fff.fr/api/clubs/{cl_no}/equipes`
3. Récupération des derniers résultats et du calendrier officiel :
   `https://api-dofa.fff.fr/api/clubs/{cl_no}/resultat`
   `https://api-dofa.fff.fr/api/clubs/{cl_no}/calendrier`
   `https://api-dofa.fff.fr/api/clubs/{cl_no}/matchs`
4. Récupération du classement officiel de chaque poule (`cp_no`, `ph_no`, `po_no`) :
   `https://api-dofa.fff.fr/api/compets/{cp_no}/phases/{ph_no}/poules/{po_no}/classement_journees`
5. Aucune donnée fictive : seules les équipes disposant d'un vrai classement FFF officiel sont conservées.
"""

from __future__ import annotations

from collections import deque
import datetime
import io
import logging
import os
import socket
import time
from typing import Any, Dict, List, Optional, Tuple

import requests as std_requests
import urllib3
import urllib3.util.connection as urllib3_cn

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    Image = None
    HAS_PIL = False

try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    cffi_requests = None
    HAS_CURL_CFFI = False

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

logger = logging.getLogger("ASD_FFF_Scraper")

# Constantes officielles FFF pour l'AS Durlinsdorf
FFF_API_BASE = "https://api-dofa.fff.fr/api"
ASD_AFFILIATION_NUMBER = 523732
ASD_DEFAULT_CL_NO = 9029
ASD_KEYWORDS = ("durlinsdorf", "a.s. durlinsdorf", "as durlinsdorf", "durlinsdorf a.s", "durlinsdorf epsud")


def is_asd_team_name(name: str) -> bool:
    """Vérifie si le libellé d'équipe correspond à l'AS Durlinsdorf."""
    if not name:
        return False
    norm = name.lower().strip()
    return any(kw in norm for kw in ASD_KEYWORDS)


class FFFScraper:
    """
    Client officiel pour l'API publique DOFA de la FFF (`https://api-dofa.fff.fr/api/`).
    Utilise l'empreinte TLS Chrome (`curl_cffi`) pour traverser le pare-feu Akamai de la FFF
    et intègre un cache mémoire (TTL) en plus du cache persistant `database.json`
    afin de ne jamais surcharger l'API officielle de la FFF.
    """

    def __init__(self, timeout: int = 12, cache_ttl: int = 900) -> None:
        self.timeout = timeout
        self.cache_ttl = cache_ttl  # Durée de mise en cache mémoire par défaut : 15 min (900s)
        self._api_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self.std_session = std_requests.Session()
        self.std_session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/ld+json, application/json",
            "Origin": "https://epreuves.fff.fr",
            "Referer": "https://epreuves.fff.fr/",
        })

    def clear_cache(self) -> None:
        """Vide le cache mémoire HTTP (utilisé lors d'une synchronisation manuelle forcée)."""
        self._api_cache.clear()

    def api_get(self, endpoint: str, use_cache: bool = True) -> Optional[Dict[str, Any]]:
        """
        Exécute une requête GET sur `https://api-dofa.fff.fr/api/<endpoint>`
        avec mise en cache mémoire (TTL) pour préserver l'API officielle FFF.
        """
        clean_ep = endpoint.lstrip("/")
        now = time.time()

        if use_cache and clean_ep in self._api_cache:
            cached_ts, cached_payload = self._api_cache[clean_ep]
            if now - cached_ts < self.cache_ttl:
                return cached_payload

        url = f"{FFF_API_BASE}/{clean_ep}"
        result: Optional[Dict[str, Any]] = None

        # 1. Requête avec empreinte TLS navigateur via curl_cffi (évite le 403 Akamai FFF)
        if HAS_CURL_CFFI and cffi_requests is not None:
            try:
                resp = cffi_requests.get(
                    url,
                    impersonate="chrome",
                    headers={"Accept": "application/ld+json, application/json"},
                    timeout=self.timeout,
                    verify=False,
                )
                if resp.status_code == 200:
                    result = resp.json()
                else:
                    logger.warning("API FFF %s -> HTTP %d", url, resp.status_code)
            except Exception as exc:
                logger.debug("Erreur curl_cffi sur %s : %s", url, exc)

        # 2. Repli sur requests standard
        if result is None:
            try:
                resp = self.std_session.get(url, timeout=self.timeout, verify=False)
                if resp.status_code == 200:
                    result = resp.json()
            except Exception as exc:
                logger.debug("Erreur requests sur %s : %s", url, exc)

        # 3. Repli sur la commande système curl si présente dans le conteneur Cloud
        if result is None:
            try:
                import json as _json
                import subprocess as _sp

                proc = _sp.run(
                    [
                        "curl",
                        "-fsSL",
                        "-k",
                        "--max-time",
                        str(self.timeout),
                        "-H",
                        "Accept: application/ld+json, application/json",
                        "-H",
                        "Origin: https://epreuves.fff.fr",
                        "-H",
                        "Referer: https://epreuves.fff.fr/",
                        "-A",
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                        url,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout + 2,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    result = _json.loads(proc.stdout)
            except Exception as exc:
                logger.debug("Erreur curl CLI sur %s : %s", url, exc)

        if result is not None:
            self._api_cache[clean_ep] = (now, result)
            return result

        # Si l'API FFF est momentanément indisponible mais qu'une entrée expirée existe en mémoire, on la réutilise
        if clean_ep in self._api_cache:
            return self._api_cache[clean_ep][1]

        return None

    # -------------------------------------------------------------------------
    # 1. DÉCOUVERTE AUTOMATIQUE DU NUMÉRO INTERNE DE CLUB (cl_no = 9029)
    # -------------------------------------------------------------------------
    def find_club_cl_no(
        self,
        affiliation_number: int = ASD_AFFILIATION_NUMBER,
        club_name_query: str = "DURLINSDORF",
    ) -> int:
        """
        Récupère et vérifie automatiquement le vrai `cl_no` de l'AS Durlinsdorf sur `api-dofa.fff.fr`.
        - Vérifie d'abord `/api/clubs/9029` (correspondant à l'affiliation 523732 - A.S. DURLINSDORF).
        - Interroge `/api/clubs?nom=DURLINSDORF` et effectue une recherche dichotomique par numéro
          d'affiliation si nécessaire.
        """
        # 1. Vérification directe du cl_no officiel 9029
        direct = self.api_get(f"clubs/{ASD_DEFAULT_CL_NO}")
        if direct and (
            direct.get("affiliation_number") == affiliation_number
            or club_name_query.upper() in str(direct.get("name", "")).upper()
        ):
            return int(direct["cl_no"])

        # 2. Test via l'endpoint /api/clubs?nom=DURLINSDORF
        search_res = self.api_get(f"clubs?nom={club_name_query}")
        if search_res and isinstance(search_res.get("hydra:member"), list):
            for club in search_res["hydra:member"]:
                if (
                    club.get("affiliation_number") == affiliation_number
                    or club_name_query.upper() in str(club.get("name", "")).upper()
                ):
                    return int(club["cl_no"])

        # 3. Recherche dichotomique sur /api/clubs/{cl_no} par numéro d'affiliation FFF
        lo, hi = 82, 23732
        while lo <= hi:
            mid = (lo + hi) // 2
            club_data = None
            curr_id = mid
            for offset in (0, 1, -1, 2, -2):
                cand = self.api_get(f"clubs/{mid + offset}")
                if cand and "cl_no" in cand:
                    club_data = cand
                    curr_id = mid + offset
                    break
            if not club_data:
                break
            aff = int(club_data.get("affiliation_number") or 0)
            if 0 < aff < 100000:
                aff += 500000
            if aff == affiliation_number or club_name_query.upper() in str(club_data.get("name", "")).upper():
                return int(curr_id)
            if aff < affiliation_number:
                lo = mid + 1
            else:
                hi = mid - 1

        return ASD_DEFAULT_CL_NO

    # -------------------------------------------------------------------------
    # 1b. RÉCUPÉRATION ET SAUVEGARDE DU BLASON OFFICIEL FFF (`logo`)
    # -------------------------------------------------------------------------
    def fetch_and_save_club_logo(
        self,
        cl_no: int = ASD_DEFAULT_CL_NO,
        dest_path: Optional[str] = None,
        force: bool = False,
    ) -> Optional[str]:
        """
        Récupère l'URL officielle du blason du club depuis `https://api-dofa.fff.fr/api/clubs/{cl_no}`
        (champ `logo`), télécharge l'image et l'enregistre dans `static/logo_asd.png`.
        """
        base_dir = os.path.abspath(os.path.dirname(__file__))
        if dest_path is None:
            dest_path = os.path.join(base_dir, "static", "logo_asd.png")

        if os.path.exists(dest_path) and not force:
            return dest_path

        club_data = self.api_get(f"clubs/{cl_no}")
        logo_url = str((club_data or {}).get("logo") or "").strip()
        if not logo_url:
            logo_url = f"https://cdn-transverse.azureedge.net/phlogos/BC{ASD_AFFILIATION_NUMBER}.jpg"

        raw_bytes: Optional[bytes] = None
        if HAS_CURL_CFFI and cffi_requests is not None:
            try:
                r = cffi_requests.get(logo_url, impersonate="chrome", timeout=self.timeout, verify=False)
                if r.status_code == 200 and r.content:
                    raw_bytes = r.content
            except Exception as exc:
                logger.debug("Erreur téléchargement logo via curl_cffi (%s): %s", logo_url, exc)

        if not raw_bytes:
            try:
                r = self.std_session.get(logo_url, timeout=self.timeout, verify=False)
                if r.status_code == 200 and r.content:
                    raw_bytes = r.content
            except Exception as exc:
                logger.debug("Erreur téléchargement logo via requests (%s): %s", logo_url, exc)

        # Repli hors-ligne sur static/img/logo_asd.jpg si présent
        if not raw_bytes:
            fallback_jpg = os.path.join(base_dir, "static", "img", "logo_asd.jpg")
            if os.path.exists(fallback_jpg):
                with open(fallback_jpg, "rb") as f:
                    raw_bytes = f.read()

        if not raw_bytes:
            return None

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        if HAS_PIL and Image is not None:
            try:
                img = Image.open(io.BytesIO(raw_bytes)).convert("RGBA")
                w, h = img.size
                pixels = img.load()

                # Détourage propre par flood-fill depuis les 4 coins extérieurs
                # (supprime le carré blanc externe JPEG tout en gardant le ballon blanc intact)
                visited = [[False] * h for _ in range(w)]
                q: deque[Tuple[int, int]] = deque()
                for sx, sy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
                    r0, g0, b0, _ = pixels[sx, sy]
                    if r0 > 235 and g0 > 235 and b0 > 235:
                        q.append((sx, sy))
                        visited[sx][sy] = True

                while q:
                    x, y = q.popleft()
                    pixels[x, y] = (255, 255, 255, 0)
                    for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                        if 0 <= nx < w and 0 <= ny < h and not visited[nx][ny]:
                            rv, gv, bv, _ = pixels[nx, ny]
                            if rv > 232 and gv > 232 and bv > 232:
                                visited[nx][ny] = True
                                q.append((nx, ny))

                # Recadrage automatique au plus près du blason
                bbox = img.getbbox()
                if bbox:
                    img = img.crop(bbox)

                img.save(dest_path, format="PNG")
                logger.info("Blason officiel FFF téléchargé et enregistré dans %s", dest_path)
                return dest_path
            except Exception as exc:
                logger.warning("Conversion PNG du logo échouée (%s), sauvegarde directe.", exc)

        with open(dest_path, "wb") as f:
            f.write(raw_bytes)
        return dest_path

    # -------------------------------------------------------------------------
    # 2. RÉCUPÉRATION DES RÉSULTATS, CALENDRIERS ET MATCHS DU CLUB
    # -------------------------------------------------------------------------
    def fetch_club_matches(self, cl_no: int) -> List[Dict[str, Any]]:
        """
        Récupère l'ensemble des rencontres (résultats joués et calendrier à venir)
        du club `cl_no` depuis les endpoints officiels :
        - `/api/clubs/{cl_no}/resultat`
        - `/api/clubs/{cl_no}/calendrier`
        - `/api/clubs/{cl_no}/matchs?page=1..N`
        """
        matches_by_id: Dict[Any, Dict[str, Any]] = {}

        # Endpoints rapides (derniers résultats & prochains matchs immédiats)
        for ep in (f"clubs/{cl_no}/resultat", f"clubs/{cl_no}/calendrier"):
            data = self.api_get(ep)
            if data and isinstance(data.get("hydra:member"), list):
                for m in data["hydra:member"]:
                    ma_no = m.get("ma_no")
                    if ma_no:
                        matches_by_id[ma_no] = m

        # Pagination complète de `/api/clubs/{cl_no}/matchs` pour couvrir toutes les équipes
        page = 1
        max_pages = 6
        while page <= max_pages:
            data = self.api_get(f"clubs/{cl_no}/matchs?page={page}")
            if not data or not isinstance(data.get("hydra:member"), list):
                break
            members = data["hydra:member"]
            if not members:
                break
            for m in members:
                ma_no = m.get("ma_no")
                if ma_no:
                    matches_by_id[ma_no] = m

            view = data.get("hydra:view") or {}
            if not view.get("hydra:next"):
                break
            page += 1

        return list(matches_by_id.values())

    # -------------------------------------------------------------------------
    # 3. CLASSEMENT OFFICIEL D'UNE POULE (`/classement_journees`)
    # -------------------------------------------------------------------------
    def fetch_poule_standings(
        self, cp_no: int, ph_no: int, po_no: int, cl_no: int = ASD_DEFAULT_CL_NO
    ) -> Tuple[List[Dict[str, Any]], str]:
        """
        Interroge `https://api-dofa.fff.fr/api/compets/{cp_no}/phases/{ph_no}/poules/{po_no}/classement_journees`
        et retourne `(standings, poule_name)`.
        Si la poule n'a pas de classement, retourne `([], "")`.
        """
        data = self.api_get(f"compets/{cp_no}/phases/{ph_no}/poules/{po_no}/classement_journees")
        if not data or not isinstance(data.get("hydra:member"), list):
            return [], ""

        members = data["hydra:member"]
        if not members:
            return [], ""

        poule_name = ""
        first_poule = members[0].get("poule") or {}
        if first_poule.get("name"):
            poule_name = str(first_poule["name"]).strip()

        # Compter combien d'équipes du même club sont dans la même poule
        standings: List[Dict[str, Any]] = []
        for idx, row in enumerate(members, start=1):
            eq = row.get("equipe") or {}
            eq_club = eq.get("club") or {}
            eq_cl_no = eq_club.get("cl_no")

            base_name = (
                eq.get("short_name")
                or eq.get("short_name_ligue")
                or f"Équipe {idx}"
            ).strip()

            team_code = eq.get("code") or eq.get("number")
            cat_code = str(eq.get("category_code") or "")

            # Affiche le numéro d'équipe (ex: PAYS RHENAN F.C 2, DURLINSDORF A.S 1)
            if team_code and int(team_code) >= 1:
                if cat_code == "SEM" and int(team_code) > 1:
                    club_display = f"{base_name} {team_code}"
                elif cat_code != "SEM":
                    club_display = f"{base_name} {team_code}"
                else:
                    club_display = base_name
            else:
                club_display = base_name

            pts = int(row.get("point_count", 0) or 0)
            played = int(row.get("total_games_count", 0) or 0)
            won = int(row.get("won_games_count", 0) or 0)
            drawn = int(row.get("draw_games_count", 0) or 0)
            lost = int(row.get("lost_games_count", 0) or 0)
            gf = int(row.get("goals_for_count", 0) or 0)
            ga = int(row.get("goals_against_count", 0) or 0)
            raw_diff = row.get("goals_diff")
            diff = int(raw_diff) if raw_diff is not None and (gf - ga >= 0 or int(raw_diff) <= 0) else (gf - ga)
            rank = int(row.get("rank", idx) or idx)

            is_asd = (eq_cl_no == cl_no) or is_asd_team_name(club_display)

            standings.append({
                "pos": rank,
                "club": club_display,
                "pts": pts,
                "played": played,
                "won": won,
                "drawn": drawn,
                "lost": lost,
                "gf": gf,
                "ga": ga,
                "diff": diff,
                "is_asd": is_asd,
            })

        standings.sort(key=lambda r: r["pos"])
        return standings, poule_name

    # -------------------------------------------------------------------------
    # 4. FORMATAGE DES MATCHS ET CALCUL DE LA FORME RÉCENTE
    # -------------------------------------------------------------------------
    @staticmethod
    def _format_team_display(eq: Dict[str, Any], cl_no: int) -> str:
        if not eq:
            return "À déterminer"
        eq_cl_no = (eq.get("club") or {}).get("cl_no")
        short = str(eq.get("short_name") or "Équipe").strip()
        code = eq.get("code") or eq.get("number")
        if eq_cl_no == cl_no or is_asd_team_name(short):
            return f"AS Durlinsdorf {code}" if code and int(code) > 1 else "AS Durlinsdorf"
        if code and int(code) > 1:
            return f"{short} {code}"
        return short

    @classmethod
    def _format_match_entity(cls, m: Dict[str, Any], cl_no: int) -> Dict[str, Any]:
        """Convertit un MatchEntity de l'API FFF DOFA au format d'affichage TV."""
        home = m.get("home") or {}
        away = m.get("away") or {}

        home_is_asd = (home.get("club") or {}).get("cl_no") == cl_no or is_asd_team_name(home.get("short_name", ""))
        home_name = cls._format_team_display(home, cl_no)
        away_name = cls._format_team_display(away, cl_no)

        venue = "Domicile" if home_is_asd else "Extérieur"
        opponent = away_name if home_is_asd else home_name

        raw_date = m.get("date") or ""
        date_str = "Date à définir"
        if raw_date and len(raw_date) >= 10:
            try:
                dt = datetime.date.fromisoformat(raw_date[:10])
                date_str = dt.strftime("%d/%m/%Y")
            except ValueError:
                date_str = raw_date[:10]

        raw_time = str(m.get("time") or "15H00").strip().upper().replace("H", ":")
        if len(raw_time) == 4 and ":" in raw_time:
            raw_time = "0" + raw_time

        h_score = m.get("home_score")
        a_score = m.get("away_score")

        comp_name = (m.get("competition") or {}).get("name") or "District d'Alsace"
        poule_name = (m.get("poule") or {}).get("name") or ""
        comp_full = f"{comp_name} • {poule_name}".strip(" •")

        return {
            "ma_no": m.get("ma_no"),
            "home_team": home_name,
            "away_team": away_name,
            "home_score": int(h_score) if h_score is not None else None,
            "away_score": int(a_score) if a_score is not None else None,
            "opponent": opponent,
            "venue": venue,
            "date": date_str,
            "time": raw_time,
            "competition": comp_full,
            "note": "",
        }

    @staticmethod
    def _extract_team_matches_and_form(
        all_matches: List[Dict[str, Any]],
        cl_no: int,
        category_code: str,
        team_number: int,
        team_code: int,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], List[str]]:
        """
        Filtre les matchs appartenant à une équipe précise du club et retourne
        `(last_match, next_match, form_list)`.
        """
        team_matches: List[Dict[str, Any]] = []
        for m in all_matches:
            h = m.get("home")
            a = m.get("away")
            # Ignore les journées exemptes (sans adversaire)
            if not h or not a:
                continue

            h_is_team = (
                (h.get("club") or {}).get("cl_no") == cl_no
                and h.get("category_code") == category_code
                and (h.get("number") == team_number or h.get("code") == team_code)
            )
            a_is_team = (
                (a.get("club") or {}).get("cl_no") == cl_no
                and a.get("category_code") == category_code
                and (a.get("number") == team_number or a.get("code") == team_code)
            )
            if h_is_team or a_is_team:
                team_matches.append(m)

        today_iso = datetime.date.today().isoformat()

        played: List[Dict[str, Any]] = []
        upcoming: List[Dict[str, Any]] = []

        for m in team_matches:
            hs = m.get("home_score")
            as_ = m.get("away_score")
            m_date = (m.get("date") or "")[:10]
            if hs is not None and as_ is not None:
                played.append(m)
            elif not m_date or m_date >= today_iso:
                upcoming.append(m)

        played.sort(key=lambda x: (x.get("date") or ""))
        upcoming.sort(key=lambda x: (x.get("date") or "9999-99-99"))

        last_match = FFFScraper._format_match_entity(played[-1], cl_no) if played else {}
        next_match = FFFScraper._format_match_entity(upcoming[0], cl_no) if upcoming else {}

        # Calcul de la série de forme sur les 5 derniers matchs joués
        form: List[str] = []
        for pm in played[-5:]:
            h = pm.get("home") or {}
            home_is_asd = (h.get("club") or {}).get("cl_no") == cl_no or is_asd_team_name(h.get("short_name", ""))
            hs = int(pm.get("home_score") or 0)
            as_ = int(pm.get("away_score") or 0)
            asd_goals = hs if home_is_asd else as_
            opp_goals = as_ if home_is_asd else hs
            if asd_goals > opp_goals:
                form.append("V")
            elif asd_goals == opp_goals:
                form.append("N")
            else:
                form.append("D")

        return last_match, next_match, form

    # -------------------------------------------------------------------------
    # 5. SYNCHRONISATION COMPLÈTE DES VRAIES ÉQUIPES DE L'AS DURLINSDORF
    # -------------------------------------------------------------------------
    def sync_all_teams(
        self,
        existing_teams: Optional[List[Dict[str, Any]]] = None,
        force_manual: bool = False,
    ) -> Dict[str, Any]:
        """
        Interroge en direct l'API officielle `https://api-dofa.fff.fr/api/` :
        1. Trouve le `cl_no` de l'AS Durlinsdorf (`9029`).
        2. Récupère `/api/clubs/{cl_no}/equipes`.
        3. Récupère `/api/clubs/{cl_no}/resultat`, `/calendrier` et `/matchs`.
        4. Pour chaque équipe ayant un engagement en championnat (`type == "CH"`),
           récupère `/api/compets/{cp_no}/phases/{ph_no}/poules/{po_no}/classement_journees`.
        5. Ignore strictement toute équipe sans classement réel (zéro donnée fictive).
        6. Conserve le choix d'affichage (`active`: coché/décoché dans `/admin`) de chaque équipe.
        """
        existing_by_id: Dict[str, Dict[str, Any]] = {}
        if existing_teams:
            for t in existing_teams:
                tid = str(t.get("id") or "")
                if tid:
                    existing_by_id[tid] = t

        if force_manual:
            self.clear_cache()

        cl_no = self.find_club_cl_no()
        # Télécharge et enregistre automatiquement le vrai blason FFF dans static/logo_asd.png s'il n'existe pas encore
        self.fetch_and_save_club_logo(cl_no=cl_no, force=False)
        equipes_data = self.api_get(f"clubs/{cl_no}/equipes")

        if not equipes_data or not isinstance(equipes_data.get("hydra:member"), list):
            # Mode hors-ligne : on retourne uniquement les vraies équipes déjà en cache ayant un classement
            cached_valid = [
                t for t in (existing_teams or [])
                if t.get("standings") and len(t["standings"]) > 0 and t.get("fff_cp_no")
            ]
            return {
                "cl_no": cl_no,
                "teams": cached_valid,
                "last_sync": datetime.datetime.now().strftime("%d/%m/%Y à %H:%M:%S"),
                "status": "offline_cache",
                "synced_count": 0,
                "cached_count": len(cached_valid),
                "details": ["API FFF injoignable : conservation du cache réel local."],
            }

        all_matches = self.fetch_club_matches(cl_no)
        raw_equipes = equipes_data["hydra:member"]

        synced_teams: List[Dict[str, Any]] = []
        details: List[str] = []
        now_short = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")

        for eq in raw_equipes:
            cat_code = str(eq.get("category_code") or "SEM").strip()
            cat_label = str(eq.get("category_label") or cat_code).strip()
            team_number = int(eq.get("number") or 1)
            team_code = int(eq.get("code") or 1)

            # Recherche de l'engagement en Championnat (type == 'CH')
            engagements = eq.get("engagements") or []
            champ_eng = None
            for eng in engagements:
                comp = eng.get("competition") or {}
                if comp.get("type") == "CH":
                    champ_eng = eng
                    break

            if not champ_eng:
                # Pas de championnat pour cette équipe -> on ne l'affiche pas (zéro donnée fictive)
                continue

            comp = champ_eng.get("competition") or {}
            phase = champ_eng.get("phase") or {}
            poule = champ_eng.get("poule") or {}

            cp_no = int(comp.get("cp_no") or 0)
            comp_name = str(comp.get("name") or "Championnat Alsace").strip()
            ph_no = int(phase.get("number") or 1)
            po_no = int(poule.get("stage_number") or 1)

            if not cp_no or not po_no:
                continue

            team_id = f"{cat_code.lower()}-{team_code}"
            prev_team = existing_by_id.get(team_id, {})

            # Récupération du classement officiel de la poule
            standings, poule_name = self.fetch_poule_standings(cp_no, ph_no, po_no, cl_no=cl_no)
            if not standings and prev_team.get("standings"):
                standings = prev_team["standings"]
            if not standings:
                # Si l'équipe n'a pas de classement publié, on ne l'affiche pas
                continue

            # Nommage clair et officiel de l'équipe
            if cat_code == "SEM":
                team_name = f"Seniors {team_code} — {comp_name}"
                short_tab = f"SENIORS {team_code}"
            else:
                team_name = f"{cat_code} ({team_code}) — {comp_name}"
                short_tab = f"{cat_code} {team_code}"

            full_comp_label = f"{comp_name} - {poule_name}".strip(" -")

            # Si l'admin a activé la surcharge manuelle sur cette équipe précise
            if prev_team.get("manual_override") and not force_manual:
                preserved = dict(prev_team)
                preserved["standings"] = standings
                synced_teams.append(preserved)
                details.append(f"{team_name}: Mode manuel conservé ({len(standings)} équipes)")
                continue

            last_match, next_match, form = self._extract_team_matches_and_form(
                all_matches, cl_no, cat_code, team_number, team_code
            )

            fff_url = (
                f"https://epreuves.fff.fr/competition/engagement/{cp_no}"
                f"/phase/{ph_no}/poule/{po_no}/classement"
            )

            team_obj: Dict[str, Any] = {
                "id": team_id,
                "cl_no": cl_no,
                "category_code": cat_code,
                "team_number": team_number,
                "team_code": team_code,
                "name": prev_team.get("name") or team_name,
                "short_name": prev_team.get("short_name") or short_tab,
                "category": cat_label,
                "competition": full_comp_label,
                "fff_cp_no": str(cp_no),
                "fff_ph_no": str(ph_no),
                "fff_po_no": str(po_no),
                "fff_url": fff_url,
                "active": bool(prev_team.get("active", True)),
                "manual_override": False,
                "form": form,
                "last_synced_at": now_short,
                "last_match": last_match,
                "next_match": next_match,
                "standings": standings,
            }
            synced_teams.append(team_obj)
            details.append(f"{team_name} ({full_comp_label}) : {len(standings)} équipes classées")

        # Tri logique : Seniors 1, 2, 3 d'abord, puis équipes de jeunes (U13 1, 2, 3)
        def sort_key(t: Dict[str, Any]) -> Tuple[int, int]:
            is_senior = 0 if t.get("category_code") == "SEM" else 1
            return (is_senior, int(t.get("team_code") or 99))

        synced_teams = [
            t for t in synced_teams
            if isinstance(t.get("standings"), list) and len(t["standings"]) > 0
        ]
        synced_teams.sort(key=sort_key)

        if not synced_teams and existing_teams:
            cached_valid = [
                t for t in existing_teams
                if isinstance(t.get("standings"), list) and len(t["standings"]) > 0
            ]
            if cached_valid:
                return {
                    "cl_no": cl_no,
                    "teams": cached_valid,
                    "last_sync": datetime.datetime.now().strftime("%d/%m/%Y à %H:%M:%S"),
                    "status": "offline_cache",
                    "synced_count": 0,
                    "cached_count": len(cached_valid),
                    "details": ["Aucun classement FFF récupéré : conservation du cache local valide."],
                }

        return {
            "cl_no": cl_no,
            "teams": synced_teams,
            "last_sync": datetime.datetime.now().strftime("%d/%m/%Y à %H:%M:%S"),
            "status": "online",
            "synced_count": len(synced_teams),
            "cached_count": 0,
            "details": details,
        }


def should_auto_sync(last_sync_epoch: float, now: Optional[datetime.datetime] = None) -> bool:
    """
    Politique de planification intelligente :
    - Samedi et Dimanche après-midi (12h00 à 21h00) : toutes les 30 minutes (1800s)
    - Reste de la semaine : toutes les 2 heures (7200s)
    """
    if now is None:
        now = datetime.datetime.now()

    elapsed = time.time() - last_sync_epoch
    is_weekend_afternoon = (now.weekday() in (5, 6)) and (12 <= now.hour <= 21)
    interval = 1800 if is_weekend_afternoon else 7200
    return elapsed >= interval
