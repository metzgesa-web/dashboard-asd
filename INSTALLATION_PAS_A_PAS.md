# 💛🖤 Guide de Déploiement Cloud & Affichage Smart TV
## Tableau de Bord Numérique — AS Durlinsdorf (Fondé en 1969 • FFF #523732)

> 📌 **Consultez également le guide dédié :** [`GUIDE_SMART_TV_KIOSK.md`](./GUIDE_SMART_TV_KIOSK.md) pour la configuration détaillée de **Fully Kiosk Browser** et **Amazon Silk Browser** sur Smart TV / Fire TV / Chromecast.

---

## 📁 1. Architecture du Projet (100 % Cloud / Web App)

```text
Tableau de Bord ASD/
├── app.py                      # Application Flask Cloud-Ready (SSE temps réel, Auth PIN, Météo SVG, QR Codes)
├── fff_scraper.py              # Module FFF en arrière-plan (API DOFA officielle + Cache mémoire & disque)
├── database.json               # Base de données JSON (Équipes ASD, Buvette, Sponsors, Annonces, Événement)
├── requirements.txt            # Dépendances Python compatibles Render / Railway / PythonAnywhere
├── Procfile                    # Fichier de lancement Cloud universel (Gunicorn WSGI)
├── render.yaml                 # Blueprint de déploiement automatique pour Render.com
├── railway.json                # Configuration de déploiement pour Railway.app
├── GUIDE_SMART_TV_KIOSK.md     # Guide pas-à-pas d'affichage Kiosque sur Smart TV / Fire TV / Chromecast
├── templates/
│   ├── tv.html                 # Vue publique /tv plein écran responsive (720p / 1080p / 4K) + Mode Kiosque
│   └── admin.html              # Interface /admin Mobile-First protégée par Code PIN modifiable (défaut : 1969)
└── static/
    ├── css/custom.css          # Charte graphique Noir Profond (#0B0F17) & Jaune Or (#FFD700)
    ├── js/tailwind.js          # Tailwind CSS autonome
    └── logo_asd.png            # Blason officiel AS Durlinsdorf
```

---

## 💻 2. Tester en Local sur votre PC (Windows / Mac / Linux)

1. Installez les dépendances Python :
   ```powershell
   pip install -r requirements.txt
   ```
2. Lancez le serveur :
   ```powershell
   python app.py
   ```
3. Ouvrez votre navigateur :
   - **Vue Smart TV** : `http://localhost:5000/tv`
   - **Interface d'Administration** : `http://localhost:5000/admin` (Code PIN par défaut : **`1969`**)

---

## ☁️ 3. Déployer dans le Cloud (Render / Railway / PythonAnywhere)

Suivez les étapes détaillées dans [`GUIDE_SMART_TV_KIOSK.md`](./GUIDE_SMART_TV_KIOSK.md) pour mettre l'application en ligne gratuitement et l'afficher sur le téléviseur du Club-House.
