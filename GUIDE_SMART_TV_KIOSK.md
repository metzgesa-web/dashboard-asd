# 📺 Guide Complet : Déploiement Cloud & Affichage Kiosque sur Smart TV (Sans Raspberry Pi)
## Tableau de Bord Numérique — AS Durlinsdorf (Fondé en 1969 • FFF #523732)

Ce guide explique comment héberger gratuitement votre application web dans le **Cloud** (Render, Railway ou PythonAnywhere) et comment l'afficher en **plein écran permanent (Mode Kiosque)** sur n'importe quelle **Smart TV** ou **clé HDMI (Fire TV Stick / Chromecast avec Google TV)**, sans aucun Raspberry Pi.

---

## ☁️ Partie 1 : Héberger l'Application Gratuitement dans le Cloud

Le projet contient déjà tous les fichiers nécessaires pour un déploiement automatique :
- [`requirements.txt`](./requirements.txt) : Dépendances Python (`Flask`, `gunicorn`, `requests`, `curl_cffi`, `beautifulsoup4`, `qrcode`, `Pillow`)
- [`Procfile`](./Procfile) : Commande de démarrage universelle (`web: gunicorn app:app --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:$PORT`)
- [`render.yaml`](./render.yaml) : Configuration prête à l'emploi pour **Render.com**
- [`railway.json`](./railway.json) : Configuration prête à l'emploi pour **Railway.app**

### Option 1 : Déployer sur Render.com (100 % Gratuit — Recommandé)
1. Créez un dépôt GitHub (privé ou public) et envoyez-y les fichiers de ce dossier (`app.py`, `fff_scraper.py`, `database.json`, `requirements.txt`, `Procfile`, `render.yaml`, `templates/`, `static/`).
2. Créez un compte gratuit sur **[Render.com](https://render.com/)**.
3. Cliquez sur **New +** ➔ **Web Service** et connectez votre dépôt GitHub.
4. Render détecte automatiquement la configuration :
   - **Runtime** : `Python 3`
   - **Build Command** : `pip install -r requirements.txt`
   - **Start Command** : `gunicorn app:app --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:$PORT`
   - **Instance Type** : `Free`
5. Cliquez sur **Create Web Service**. En 2 minutes, vous obtenez votre URL officielle HTTPS :
   - **URL de la TV** : `https://as-durlinsdorf-tv-dashboard.onrender.com/tv`
   - **URL d'Administration (Smartphone)** : `https://as-durlinsdorf-tv-dashboard.onrender.com/admin` *(Code PIN par défaut : `1969`)*

> **Astuce Anti-Veille Render** : Tant que la télévision du Club-House est allumée sur `/tv`, la page envoie automatiquement un signal toutes les 4 minutes vers `/health`, ce qui empêche le serveur gratuit Render de se mettre en veille !

---

### Option 2 : Déployer sur PythonAnywhere (Gratuit sans carte bancaire)
1. Créez un compte gratuit (**Beginner**) sur **[PythonAnywhere.com](https://www.pythonanywhere.com/)**.
2. Dans l'onglet **Files**, importez les fichiers du projet dans `/home/<votre_nom>/asd-dashboard/`.
3. Dans l'onglet **Consoles** ➔ **Bash**, installez les librairies :
   ```bash
   cd ~/asd-dashboard
   pip install --user -r requirements.txt
   ```
4. Dans l'onglet **Web** ➔ **Add a new web app** ➔ **Manual configuration (Python 3.10/3.11)** :
   - Ouvrez le fichier **WSGI configuration file** et remplacez son contenu par :
     ```python
     import sys
     path = '/home/<votre_nom>/asd-dashboard'
     if path not in sys.path:
         sys.path.append(path)
     from app import app as application
     ```
5. Cliquez sur le bouton vert **Reload** : votre site est en ligne sur `https://<votre_nom>.pythonanywhere.com/tv` !

---

## 📺 Partie 2 : Afficher la Page `/tv` en Mode Kiosque sur la Télévision

Vous avez **3 solutions simples et gratuites** selon votre équipement TV au Club-House :

---

### 🥇 Méthode A : Avec **Fully Kiosk Browser** (Sur Google TV / Chromecast / Android TV / Fire TV)
*Idéal si votre télévision tourne sous Android TV / Google TV (Sony, TCL, Philips, Xiaomi) ou si vous branchez un Chromecast avec Google TV / Fire TV Stick.*

**Avantage** : Démarre **automatiquement** dès qu'on allume la télévision, masque 100 % des barres de menus et empêche l'écran de se mettre en veille.

1. **Installer Fully Kiosk Browser** :
   - **Sur Android TV / Chromecast Google TV** : Ouvrez le *Google Play Store* sur la TV, cherchez **Fully Kiosk Browser** et installez-le.
   - **Sur Fire TV Stick** : Ouvrez l'application gratuite **Downloader** (dans l'Amazon Appstore), tapez le code ou l'URL officielle `https://www.fully-kiosk.com/files/FullyKioskBrowser.apk` (édition Fire OS) et installez-la.
2. **Configurer l'URL du Tableau de Bord** :
   - Lancez **Fully Kiosk Browser**.
   - Dans **Start URL**, saisissez l'adresse de votre vue TV :
     `https://<votre-domaine-cloud>/tv`
3. **Activer les 3 options Kiosque essentielles** :
   - **Web Content Settings** ➔ Activez **Enable Fullscreen Mode** *(Plein écran total)*.
   - **Device Management** ➔ Activez **Keep Screen On** *(Empêche la TV de passer en veille)*.
   - **Device Management** ➔ Activez **Launch on Boot** *(Lance automatiquement le tableau de bord dès qu'on allume la TV)*.
4. Cliquez sur **Start Using Fully Kiosk** : le tableau de bord s'affiche immédiatement en plein écran !

---

### 🥈 Méthode B : Avec **Amazon Silk Browser** (100 % Natif & Gratuit sur Fire TV Stick)
*Si vous utilisez une clé **Amazon Fire TV Stick** et souhaitez utiliser le navigateur officiel préinstallé.*

1. Depuis l'accueil du Fire TV Stick, ouvrez le navigateur gratuit **Internet (Amazon Silk)**.
2. Dans la barre d'adresse en haut, tapez l'URL de votre vue TV :
   `https://<votre-domaine-cloud>/tv`
3. Ajoutez la page aux **Favoris** (icône **Ajouter un signet ⭐**) pour la retrouver en 1 clic sur l'écran d'accueil.
4. **Passer en vrai Plein Écran sans barre d'adresse** :
   - Avec le curseur de la télécommande Fire TV, cliquez sur le bouton jaune **`[ ⛶ Plein Écran TV ]`** situé en haut à droite à côté de l'horloge.
   - La barre du navigateur disparaît aussitôt. Au bout de 4 secondes sans bouger la télécommande, le bouton et le curseur disparaissent automatiquement pour laisser un écran 100 % propre !

> **Empêcher la mise en veille du Fire TV Stick** :
> Allez dans **Paramètres (⚙️)** ➔ **Affichage et sons** ➔ **Économiseur d'écran** ➔ Réglez **Démarrer l'économiseur d'écran** sur **Jamais**.

---

### 🥉 Méthode C : Directement sur le Navigateur Intégré d'une Smart TV (Samsung Tizen / LG WebOS)
*Sans aucune clé TV ni boîtier externe : uniquement le téléviseur connecté au Wi-Fi du Club-House.*

1. Allumez la Smart TV et ouvrez l'application préinstallée **Navigateur Internet** (*Samsung Internet* ou *LG Web Browser*).
2. Entrez l'adresse : `https://<votre-domaine-cloud>/tv` et ajoutez-la en **Page d'accueil / Favori**.
3. Dans les options du navigateur Smart TV (en haut à droite) :
   - Décochez *"Afficher la barre d'onglets / barre d'adresse automatiquement"*.
   - Ou cliquez simplement avec la télécommande sur le bouton jaune **`[ ⛶ Plein Écran TV ]`** directement sur la page `/tv` : l'application active aussitôt le mode Plein Écran HTML5 + le verrou anti-veille (*Screen Wake Lock*).
4. Dans les réglages généraux de la TV (*Paramètres ➔ Général ➔ Éco / Alimentation*), désactivez l'**Arrêt automatique après 4 heures**.

---

## 📱 Partie 3 : Piloter la TV depuis votre Smartphone (`/admin`)

1. Sur n'importe quel smartphone (en 4G/5G ou en Wi-Fi), ouvrez :
   `https://<votre-domaine-cloud>/admin`
   *(ou scannez le QR Code imprimé depuis l'onglet **PIN / QR**).*
2. Entrez votre **Code PIN à 4 chiffres** (par défaut : **`1969`**, modifiable à tout moment dans l'onglet **PIN / QR**).
3. Toutes vos actions mettent à jour la Smart TV **instantanément (en moins d'une seconde)** sans toucher à la télécommande de la TV :
   - **4 modes en 1 clic** : *Classements FFF*, *Carte de la Buvette*, *Mode Rotation Mixte*, *Synchronisation FFF*.
   - **Gestion de la Buvette** : Ajout/modification de produits, tarifs, formats, bascule 1-clic *En stock / En Rupture*, badge *Nouveau*.
   - **Annonces & Sponsors** : Publication de messages dans la bannière défilante, import de logos sponsors et d'affiches d'événements directement depuis la galerie photo du téléphone.
