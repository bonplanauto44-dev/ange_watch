# ange_watch — Suivi automatique de l'alliance ANGE (Fourmizzz S5)

> Propriétaire : **Arno** (pseudo en jeu **Octo**), alliance **ANGE**, serveur **S5**  
> VPS : `ubuntu@162.19.226.16` → `/home/ubuntu/ange_watch/`  
> Dernière mise à jour : novembre 2026

---

## Sommaire

1. [Présentation rapide](#1-présentation-rapide)
2. [Architecture](#2-architecture)
3. [Prérequis et installation](#3-prérequis-et-installation)
4. [Configuration (ange_watch.ini)](#4-configuration-ange_watchini)
5. [Description de chaque fichier Python](#5-description-de-chaque-fichier-python)
6. [Services en production](#6-services-en-production)
7. [Base de données SQLite (tdc_serveur.db)](#7-base-de-données-sqlite-tdc_serveurdb)
8. [Google Sheets impliqués](#8-google-sheets-impliqués)
9. [Modifier du code (auto-déploiement)](#9-modifier-du-code-auto-déploiement)
10. [Commandes manuelles utiles](#10-commandes-manuelles-utiles)
11. [Règles strictes à respecter](#11-règles-strictes-à-respecter)
12. [Dépannage](#12-dépannage)
13. [Historique des changements majeurs](#13-historique-des-changements-majeurs)

---

## 1. Présentation rapide

**Fourmizzz** est un jeu de stratégie en ligne où les joueurs gèrent des fourmilières. La métrique principale est le **TDC** (Terrain De Chasse) : chaque joueur en possède un certain montant, qu'il peut augmenter en attaquant d'autres joueurs ou en chassant. L'outil surveille automatiquement l'alliance **ANGE** du serveur 5 (`s5.fourmizzz.fr`).

Ce dépôt contient **deux sous-systèmes** qui tournent en parallèle sur le VPS :

| Sous-système | But | Comment il tourne |
|---|---|---|
| **Suivi TDC** (`ange_watch.py`) | Surveille chaque heure les flux de TDC de l'alliance (qui perd, qui gagne, échanges avec TAXE…) | Cron toutes les heures |
| **Convois + requêtes** (`ange_requete.py`) | Gère les convois de bois entre joueurs (calcul du plan quotidien, écriture dans les sheets Google), traite les commandes SQL/analyse depuis l'onglet « Requête » du sheet Suivi ANGE | Service systemd `ange-requete` tournant en continu |

---

## 2. Architecture

```
Fourmizzz API S5  ──────────────────────────────────────────────────────────────────┐
(https://s5.fourmizzz.fr/api/exports/)                                              │
        │                                                                             │
        ▼ cron toutes les heures                                                      │
  ange_watch.py          ─── écrit ──→  tdc_serveur.db  (SQLite, historique TDC)    │
        │                                     ▲                                       │
        │                                     │ lecture seule                        │
        ▼                                     │                                       │
  flux_tdc.csv           ange_requete.py ─────┘                                     │
  bilan_horaire.csv      (service systemd)                                           │
  ange_watch_etat.json        │                                                       │
        │                     ├── toutes les minutes : tick suivi + convois          │
        │                     ├── détecte ange_*.py modifiés → recharge si AUTOTEST OK│
        │                     ├── lit l'onglet Requête → exécute SQL/ANALYSE         │
        │                     └── traite commandes convois                           │
        │                                                                             │
  Alertes Discord  ◄─────────────────────────────────────────────────────────────────┘
  Google Sheets    ◄─── lecture/écriture (gspread + service_account.json)
```

**Fichiers d'état runtime** (générés automatiquement, non versionnés) :
- `ange_watch_etat.json` — dernière version API traitée
- `tdc_serveur.db` — base SQLite (toutes les versions horaires, depuis le 01/10/2026)
- `flux_tdc.csv` / `bilan_horaire.csv` — exports CSV
- `convois_*.json` — états des convois en cours
- `*_etat.json` — états divers des modules
- `log.txt` / `logs/` — journaux d'exécution

---

## 3. Prérequis et installation

### Dépendances système

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip
```

### Environnement Python

```bash
cd /home/ubuntu/ange_watch
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install gspread google-auth requests
```

> `gspread` est **obligatoire** pour l'écriture dans les Google Sheets.  
> Sans `gspread`, le suivi TDC local (CSV + SQLite) fonctionne, mais aucune écriture vers les sheets ne sera possible.

### Fichiers non versionnés à fournir

| Fichier | Contenu | Obligatoire |
|---|---|---|
| `service_account.json` | Clé JSON du compte de service Google (accès Sheets/Drive) | Oui |
| `ange_watch.ini` | Configuration (voir section 4) | Oui |

Copier et adapter :
```bash
cp ange_watch.ini.exemple ange_watch.ini
# Éditer ange_watch.ini avec tes valeurs
```

### Installation du service systemd

```bash
bash install_auto.sh
# Ce script :
# 1. Compile ange_requete.py pour détecter les erreurs de syntaxe
# 2. Installe le service systemd ange-requete
# 3. L'active au démarrage (systemctl enable)
# 4. Le démarre immédiatement
```

Vérification :
```bash
systemctl status ange-requete
journalctl -u ange-requete -f   # logs en direct
```

### Installation des crons

```bash
crontab -e
# Ajouter ces deux lignes :
2,5,8,11,14 * * * * flock -n /tmp/ange_watch.lock -c "cd /home/ubuntu/ange_watch && /home/ubuntu/ange_watch/venv/bin/python ange_watch.py >> log.txt 2>&1"
0 23 * * * /home/ubuntu/ange_watch/venv/bin/python3 /home/ubuntu/ange_watch/appliquer_redistrib_nuit.py >> /home/ubuntu/ange_watch/logs/redistrib_nuit.log 2>&1
```

---

## 4. Configuration (`ange_watch.ini`)

**Ne jamais commiter ce fichier** — il contient des secrets (webhook Discord, ID du sheet privé).  
Le fichier `ange_watch.ini.exemple` est le modèle à copier.

```ini
[general]
alliance = ANGE
; Perte d'un joueur ANGE plus petite que ça (cases) ignorée dans les alertes
seuil_perte = 1000
; Alliance mise en avant dans les alertes (en plus de toutes les alliances analysées)
surveiller = TAXE
; Ces joueurs chassent (créent du TDC) — leurs gains ne sont pas comptés comme échanges
; La liste réelle est lue depuis le sheet privé si sheet_chaine_id est renseigné
chasseurs = Cylo, Fannou, Fhezz, FourmiDiable, Judalos, Kalipzio, LaVague, Scooby-Doo, Shaka, Tatsuo, Zorka, mousse85, willo
; Seuils d'alerte Discord (en cases de TDC)
seuil_alerte_sortant = 20000
seuil_alerte_taxe = 5000
seuil_alerte_inconnu = 40000

[google]
; ID du Google Sheet "Suivi ANGE" (onglet Requête, Flux TDC, Bilan horaire…)
sheet_id = 1Px_nI00WarXo_MtyzG7rsHMnsSLRyge1GNrXhhWY5yY
; Optionnel : ID du sheet privé (pour lire la liste des chasseurs automatiquement)
sheet_chaine_id =
; Chemin vers la clé JSON du compte de service Google
credentials = service_account.json

[discord]
; URL du webhook Discord pour les alertes (laisser vide pour désactiver)
webhook = https://discord.com/api/webhooks/XXXXXX/YYYYYY
```

> ⚠️ **Après chaque changement dans `ange_watch.ini`**, le service relit la config automatiquement au prochain tick (pas besoin de redémarrer).

---

## 5. Description de chaque fichier Python

### Scripts principaux

#### `ange_watch.py` — Surveillance TDC horaire
Lancé par cron toutes les heures (minutes 2, 5, 8, 11, 14). Compare deux versions consécutives de l'API Fourmizzz pour détecter les échanges de TDC :
- **SORTANT** : un joueur ANGE perd des cases qu'un joueur hors ANGE gagne exactement
- **ENTRANT** : un joueur ANGE gagne des cases prises à un joueur hors ANGE
- **INTERNE** : échange entre deux joueurs ANGE (non alerté, compté dans le bilan)
- **NON EXPLIQUÉ** : perte sans contrepartie identifiable

Sorties : `tdc_serveur.db`, `flux_tdc.csv`, `bilan_horaire.csv`, alerte Discord si seuil dépassé.

Première utilisation (rattrapage) :
```bash
venv/bin/python3 ange_watch.py --rattrapage 48   # traite les 48 dernières heures
```

#### `ange_requete.py` — Démon principal (service systemd)
Tourne en continu (`--daemon`). Chaque **minute** il :
1. Vérifie si un fichier `ange_*.py` a changé sur disque (md5) → compile + lance `AUTOTEST()` → installe si ça passe
2. Lance `ange_suivi.tick()` (suivi horaire dès qu'une nouvelle version API est disponible)
3. Lit l'onglet **Requête** du sheet Suivi ANGE (lignes 2→21) → exécute les commandes `SQL SELECT` ou `ANALYSE ...`
4. Lance `ange_convois_tick.tick()` (veille des changements de membres/postes)

```bash
# Lancer manuellement (une seule passe, pour test)
venv/bin/python3 ange_requete.py

# Lancer en mode démon
venv/bin/python3 ange_requete.py --daemon
```

### Suivi TDC et analyses

#### `ange_suivi.py` — Suivi automatique ANGE
Appelé à chaque nouvelle version horaire. Met à jour dans le sheet Suivi ANGE :
- **Chasse** : TDC chassé par chasseur et par jour
- **Chasse horaire** : heures où chaque chasseur chasse
- **Fuites** : pertes ANGE avec preuve (joueur hors ANGE qui gagne exactement le même montant)
- **Attaquants** : classement des joueurs/alliances qui prennent du TDC à ANGE
- **Activité** : attaques/jour, heures de jeu, retards de convois, score par membre
- **Classement** : maillons forts et faibles
- **Récap** : résumé du jour (envoyé aussi sur Discord à 8h00 Paris)

#### `ange_alliances.py` — Suivi des alliances adverses
Pour chaque alliance suivie dans l'onglet « Alliances » (TAXE, AAA, TYR…) :
- Crée et met à jour un onglet « Alliance TAG » : chiffres clés, TDC échangé, arrivées/départs, évolution
- Alerte Discord si un joueur de l'alliance prend du TDC à ANGE (preuve sûre)

#### `ange_analyse.py` — Analyses SQL prédéfinies
Accessibles depuis l'onglet Requête du sheet (commande `ANALYSE NOM [param=val]`) :
- `LIEN` : corrélation entre pertes ANGE et gains d'une alliance (décalage temporel)
- `TAXE_CHAINE` : reconstitue la chaîne de la TAXE (qui nourrit qui)
- `RAPPORT` : tout en un (LAG, résidus, attaquants, départs, oscillations)
- `RESIDUS` : pertes ANGE qu'aucun gain interne n'explique
- `ATTAQUANTS` : joueurs hors ANGE ayant probablement pris du TDC
- `LAG` : décalage API entre perte ANGE et gain contrepartie
- `DEPARTS` : joueurs qui étaient ANGE et ne le sont plus
- `OSCILLATIONS` : manœuvres pour entrer dans la plage d'attaque

#### `ange_extra.py` — Analyses complémentaires
Analyses supplémentaires ajoutées sans toucher à `ange_analyse.py` (chargé à chaud).

#### `ange_attaques.py` — Reconstruction des attaques
Identifie qui a attaqué qui dans la chaîne, avec estimation de l'heure d'envoi et du temps de trajet :
- Formule de trajet : `0.9^VA × 637200 × (1 − exp(−distance/350))` (distance = euclidienne)
- Marge de TDC de la cible : ±15% (le TDC bouge pendant l'heure)

#### `ange_note_activite.py` — Notes d'activité quotidiennes
Calcule chaque matin à 5h00 Paris la note de J-1 par joueur :
- **Passeurs/Greniers** : `moyenne(note_placement, note_attaques)` sur 10, avec malus
- **Chasseurs** : `TDC_J1 / moyenne_7j × 10` (plafonné à 10)

### Convois

#### `ange_convois.py` — Cœur du module convois
Lecture du sheet privé (`charger_prive`), gestion des commandes/actions, quotas Google Sheets, `avec_reprise()` pour les erreurs 429.

#### `ange_convois_plan.py` — Calcul du plan (pur, sans écriture)
Algorithme de **flot à coût minimal** : coût = distance³, contrainte `limiter_partenaires` = 12.  
Priorité dans l'ordre : distance, O (ouvrières), TDP.  
Peut être testé sans toucher aucun sheet.

#### `ange_convois_ecr.py` — Calcul + écriture du plan de convois
Lance `ange_convois_plan`, écrit dans les sheets : Base convois, Dettes, Historique, et complète les formules des nouveaux joueurs.

#### `ange_convois_nuit.py` — Logique de clôture de nuit
Port Python de la fonction Apps Script `journeeChaines_`. Partie 1 = logique pure, partie 2 = lecture/écriture Sheets.  
**Sécurité** : refuse toute écriture hors copies TEST, sauf config explicite.

#### `ange_convois_prod.py` — Clôture de minuit en PRODUCTION
S'exécute uniquement si le fichier `convois_prod_arme.json` existe pour le jour concerné.  
Garde-fous : contrôle à blanc avant écriture, sauvegarde complète, écriture atomique (un seul batchUpdate), idempotent.

#### `ange_convois_tick.py` — Veille des membres/postes
Chaque minute, compare la liste des membres depuis le sheet privé avec l'état précédent.  
Si changement (arrivée, départ, changement de poste) → appelle `ange_convois_live.synchroniser`.  
**MODE** (fichier `convois_mode.txt`) : `test` (défaut), `off`, ou `prod`.

#### `ange_convois_live.py` — Application des changements en direct
Quand un joueur change de poste, entre, ou sort de la chaîne :
- Crée/masque/supprime/protège l'onglet du joueur dans les 3 fichiers publics
- Retire les convois liés au joueur des onglets des autres

#### `ange_convois_arrivants.py` — Suivi des mouvements de la chaîne
Journal complet (entrées, changements de poste, sorties, hors chaîne/retour) dans l'onglet **Mouvements** du sheet privé.  
Gestion du statut **Nouveau** : exclut les nouveaux arrivants du calcul des convois pendant 1 jour.

#### `ange_convois_controle.py` — Contrôle horaire des livraisons
Vérifie que « à envoyer = à recevoir » par joueur. Alerte Discord pour les anomalies.

#### `ange_convois_redistrib.py` — Redistribution des dettes (cas spécial)
Utilisé quand un Grenier sort de la chaîne avec des dettes importantes (ex. djuman, 16/11/2026).  
Écrit directement dans les onglets publics des Greniers au lieu du sheet Dettes.

#### Autres modules convois
- `ange_convois_pages.py` — pages individuelles joueurs
- `ange_convois_entetes.py` — en-têtes des onglets
- `ange_convois_annexes.py` — données annexes
- `ange_convois_onglets.py` — gestion de l'ordre des onglets
- `ange_convois_forms.py` — liste déroulante Pseudo dans les formulaires
- `ange_convois_reponses.py` — traitement des réponses de formulaires
- `ange_convois_tdclive.py` — TDC en temps réel
- `ange_convois_alertes.py` — alertes convois
- `ange_convois_malus.py` — système de malus (préparé, non activé)
- `ange_convois_chasse.py` — bloc chasse dans les onglets
- `ange_convois_plan.py` — algorithme de flot (voir ci-dessus)

#### `ange_accueil.py` — Page d'accueil du sheet Suivi ANGE
Recrée l'onglet « Accueil » à chaque relevé avec les liens vers tous les onglets.

#### `appliquer_redistrib_nuit.py` — Redistribution nuit (cron)
Lancé par cron à 23h00 UTC (00h00 Paris) après le plan de minuit.  
Ajoute les lignes de redistribution dans les onglets publics des Greniers.

---

## 6. Services en production

### Service systemd `ange-requete`

```bash
# Statut
systemctl status ange-requete

# Logs en direct
journalctl -u ange-requete -f

# Redémarrer
systemctl restart ange-requete

# Arrêter
systemctl stop ange-requete
```

**Tournant depuis le 11/11/2026, PID stable.** Ne pas redémarrer entre 21h30 et 23h00 (clôture des convois).

### Crons (crontab de l'utilisateur `ubuntu`)

```
# Suivi TDC toutes les heures (minutes 2,5,8,11,14 pour rattraper si une passe rate)
2,5,8,11,14 * * * * flock -n /tmp/ange_watch.lock -c "cd /home/ubuntu/ange_watch && /home/ubuntu/ange_watch/venv/bin/python ange_watch.py >> log.txt 2>&1"

# Redistribution dettes nuit (00h00 Paris = 23h00 UTC)
0 23 * * * /home/ubuntu/ange_watch/venv/bin/python3 /home/ubuntu/ange_watch/appliquer_redistrib_nuit.py >> /home/ubuntu/ange_watch/logs/redistrib_nuit.log 2>&1
```

### Dashboard BPA (onglet Fourmizzz)

Le dashboard BPA (`http://162.19.226.16:3001`) dispose d'un onglet **Fourmizzz** qui lit en lecture seule `tdc_serveur.db` via les routes `/api/fourmizzz/*` (server.js).

---

## 7. Base de données SQLite (`tdc_serveur.db`)

Fichier unique non versionné (`~500 Mo`), rempli depuis le 01/10/2026.

### Table principale : `tdc`

```sql
CREATE TABLE tdc (
    version TEXT,    -- format AAAA MM JJ HH mm (UTC), ex: "202611190900"
    pseudo  TEXT,    -- nom du joueur
    alliance TEXT,   -- tag de l'alliance (ex: "ANGE", "TAXE", "")
    tdc     INTEGER  -- TDC du joueur à cette version
);
CREATE INDEX idx_tdc_version ON tdc(version);
CREATE INDEX idx_tdc_pseudo  ON tdc(pseudo);
```

### Requêtes utiles

```sql
-- Dernière version disponible
SELECT MAX(version) FROM tdc;

-- TDC des membres ANGE à la dernière heure
SELECT pseudo, tdc FROM tdc
WHERE version = (SELECT MAX(version) FROM tdc)
  AND alliance = 'ANGE'
ORDER BY tdc DESC;

-- Évolution d'un joueur sur 7 jours
SELECT version, tdc FROM tdc
WHERE pseudo = 'Octo'
  AND version >= strftime('%Y%m%d%H%M', datetime('now', '-7 days'))
ORDER BY version;

-- Flux entrant/sortant d'une heure à l'autre
SELECT a.pseudo, b.tdc - a.tdc AS variation, a.alliance
FROM tdc a JOIN tdc b ON a.pseudo = b.pseudo
WHERE a.version = '202611190800'
  AND b.version = '202611190900'
  AND a.alliance = 'ANGE'
ORDER BY variation;
```

> **Accès en lecture seule depuis le code** : `c.execute("PRAGMA query_only=ON")` est appliqué systématiquement dans `ange_requete.py` et `ange_analyse.py`.

---

## 8. Google Sheets impliqués

> ⚠️ **Ne jamais supprimer ni réinitialiser ces sheets** — ils contiennent l'historique complet de l'alliance.

| Sheet | ID | Usage |
|---|---|---|
| **Suivi ANGE** (public) | `1Px_nI00WarXo_MtyzG7rsHMnsSLRyge1GNrXhhWY5yY` | Boîte aux lettres VPS (onglet Requête), flux TDC, bilans, récaps, classements |
| **Privé — Chaîne joueurs** | `1WSkmn_I44Ugn7Koq9bKqzP9KH1ucBF4-xsrZj7q35tQ` | Source de vérité des postes, dettes, TDC moyen, nouveaux, interdits — **NE JAMAIS CASSER** |
| **Public — Greniers** | `17NWquA5VyOl2NkYz_HTgQaI5te-BVGxv1dtoeiVbHi0` | Convois par grenier (un onglet par joueur) |
| **Public — Passeurs** | `11KkWMvxkIZH2mpePNOgdr8I7ZPeXbUjI4Q6zwgy7JMk` | Convois par passeur |
| **Public — Chasseurs** | `1ZfAzwGrxLFfW9klIh5O4QgnTurTk3af52hccqKMxHiQ` | Convois par chasseur |

### Droits nécessaires pour le compte de service

Le `service_account.json` doit être **éditeur** sur :
- Le sheet privé Chaîne joueurs
- Le sheet Suivi ANGE
- Les 3 sheets publics (Greniers, Passeurs, Chasseurs)

Le compte de service doit aussi avoir accès au **dossier Drive** `ange_code` si la mise à jour automatique via Drive est utilisée (actuellement désactivée — l'agent IA écrit directement dans `/home/ubuntu/ange_watch/`).

---

## 9. Modifier du code (auto-déploiement)

**Le démon `ange-requete` surveille chaque minute le répertoire `/home/ubuntu/ange_watch/`.**  
Quand le MD5 d'un fichier `ange_*.py` change :
1. Compilation Python (`py_compile`)
2. Exécution de `AUTOTEST()` si elle existe dans le module
3. Si tout passe : le module est rechargé en mémoire
4. Si ça plante : l'ancienne version reste en production, erreur dans les logs

**Workflow de modification** :

```bash
# 1. Éditer le fichier (depuis le VPS ou via l'agent IA BPA Dashboard)
nano /home/ubuntu/ange_watch/ange_suivi.py

# 2. Vérifier la syntaxe manuellement si besoin
venv/bin/python3 -m py_compile ange_suivi.py

# 3. Le démon détecte le changement et le charge automatiquement (attendre ~1 min)
journalctl -u ange-requete -f | grep "AUTOTEST\|reload\|ERREUR"
```

> **`ange_requete.py` lui-même n'est jamais remplacé par le démon** (il ne se recharge pas lui-même). Pour le modifier, arrêter le service, éditer, redémarrer.

---

## 10. Commandes manuelles utiles

Les commandes s'envoient depuis le **Google Sheet Suivi ANGE**, onglet **Requête** :
- Colonne A : la commande
- Colonne B : écrire `OUI` pour l'exécuter
- Statut en colonne B, résultat dans l'onglet `R<numéro de ligne>`

### Commandes d'analyse

```
ANALYSE RAPPORT
ANALYSE RESIDUS vue=detail depuis=202611010000
ANALYSE ATTAQUANTS alliance=TAXE top=20
ANALYSE LIEN
ANALYSE DEPARTS
ANALYSE LAG
ANALYSE OSCILLATIONS

SELECT pseudo, tdc FROM tdc WHERE version = (SELECT MAX(version) FROM tdc) AND alliance = 'ANGE' ORDER BY tdc DESC
```

### Commandes de convois

```
ANALYSE CONVOIS action=prod_etat
ANALYSE CONVOIS action=coherence cible=prod
ANALYSE CONVOIS action=pages_ecrire cible=prod
ANALYSE CONVOIS action=plan_prod_sim pseudo=Octo
ANALYSE CONVOIS action=veille_blanc
```

### Depuis le terminal VPS

```bash
# Lancer une passe manuelle du suivi TDC
cd /home/ubuntu/ange_watch
venv/bin/python3 ange_watch.py

# Rattrapage des 48 dernières heures
venv/bin/python3 ange_watch.py --rattrapage 48

# Tester le plan de convois sans écriture
venv/bin/python3 -c "
import ange_convois_ecr as E
print(E.comparer())
"

# Notes d'activité manuellement pour un jour précis
# (via l'onglet Requête : ANALYSE NOTES jour=2026-11-18 ecrire=1)

# Redistribution dettes nuit manuellement
venv/bin/python3 appliquer_redistrib_nuit.py
```

---

## 11. Règles strictes à respecter

### ⛔ Ne JAMAIS faire

1. **Modifier quoi que ce soit entre 21h30 et 23h00 (heure de Paris)** — la clôture des convois est en cours. Un bug à cet instant peut corrompre les données de tous les joueurs pour la nuit.

2. **Supprimer définitivement un fichier** — toujours utiliser la corbeille ou créer un `.bak`.

3. **Exécuter `installerDeclencheursChaines`** dans Apps Script — tout est géré par le VPS depuis le 12/11/2026, ce script réinitialiserait la configuration.

4. **Écrire en production sans armer** — pour la clôture de minuit en prod, `convois_prod_arme.json` doit exister. Ne pas l'armer sans vérification préalable.

5. **Commiter `ange_watch.ini` ou `service_account.json`** — ce sont des secrets.

### ✅ À toujours faire

- **Tester avant d'activer** : `ecrire=non` / copies TEST, `AUTOTEST()`, simulation d'abord
- **Sauvegarder avant de modifier** un fichier JSON d'état important
- **Vérifier la syntaxe** avant de pusher un fichier Python
- Répondre à Arno en **français, avec des réponses courtes** (il est souvent sur téléphone)

---

## 12. Dépannage

### Le service ange-requete ne tourne pas

```bash
systemctl status ange-requete
journalctl -u ange-requete --since "1 hour ago"
# Redémarrer
systemctl restart ange-requete
```

### Les feuilles Google ne se mettent plus à jour

1. Vérifier que `service_account.json` existe et est valide
2. Vérifier que le compte de service a toujours les droits sur les sheets
3. Regarder les logs pour les erreurs `gspread` ou `429`
4. Les 429 se règlent seuls via `avec_reprise()` — attendre quelques minutes

### Erreur `attempt to write a readonly database`

La base `notes_activite.db` peut avoir ses droits changés. Corriger :
```bash
chown ubuntu:ubuntu /home/ubuntu/ange_watch/notes_activite.db
chmod 644 /home/ubuntu/ange_watch/notes_activite.db
```

### Le cron TDC ne tourne plus

```bash
# Vérifier les logs
tail -50 /home/ubuntu/ange_watch/log.txt

# Tester manuellement
cd /home/ubuntu/ange_watch && venv/bin/python3 ange_watch.py

# Vérifier le lock
ls -la /tmp/ange_watch.lock
```

### Un module a planté après une modif de code

Le démon garde l'ancienne version. Pour forcer le rechargement :
```bash
# Toucher le fichier pour changer son timestamp (et forcer la recompilation)
touch /home/ubuntu/ange_watch/ange_suivi.py
# Surveiller les logs
journalctl -u ange-requete -f
```

### La chaîne dépasse ~94 joueurs

Certaines plages sont codées en dur (`A3:AH96`, `$C$97`). Si la chaîne approche 94 lignes, chercher et agrandir ces plages dans :
- `ange_convois.py` → `lire_prive()`
- `ange_suivi.py`
- `ange_convois_arrivants.py` → `lire_membres()`

---

## 13. Historique des changements majeurs

| Date | Changement |
|---|---|
| 01/10/2026 | Début de l'enregistrement de `tdc_serveur.db` |
| 12/11/2026 | Migration complète Apps Script → VPS, arrêt de `installerDeclencheursChaines` |
| 13/11/2026 | Statut Nouveau (arrivants), distance euclidienne, Morbifouette (Passeur 5), déploiement direct via VPS (Drive plus dans la boucle) |
| 16/11/2026 | Redistribution dettes djuman (Grenier 5 hors chaîne), cron 23h00 UTC |

---

## Notes pour le successeur

- **Pseudo Arno en jeu** : Octo
- **Interlocuteur habituel** : Arno, par Telegram, réponses courtes en français, sans tableaux
- **Ticket d'entrée technique** : lire `CONTEXTE_ANGE.md` en entier avant de toucher quoi que ce soit — il contient les règles du jeu et les conventions de l'alliance (distance euclidienne, formule de trajet, hors chaîne vs Nouveau, etc.)
- **Prudence avec la production** : toujours tester sur les copies TEST (`ange_convois.ouvrir_test`) avant de passer en prod
- **Le webhook Discord** dans `ange_watch.ini` est à **régénérer** (l'ancien a été exposé brièvement lors de la création du repo GitHub — aller dans les paramètres du serveur Discord → Intégrations → Webhooks → régénérer l'URL)
