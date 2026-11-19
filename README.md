# ange_watch — Suivi automatique alliance Fourmizzz

> Bot surveillance TDC + convois pour une alliance serveur S5.
> Chemin VPS : ~/ange_watch/ — Derniere MAJ : novembre 2026

---

## Sommaire

1. Presentation rapide
2. Architecture
3. Prerequis et installation
4. Configuration (ange_watch.ini)
5. Description de chaque fichier Python
6. Services en production
7. Base de donnees SQLite
8. Google Sheets impliques
9. Modifier du code (auto-deploiement)
10. Commandes manuelles utiles
11. Regles strictes a respecter
12. Depannage
13. Historique des changements majeurs

---

## 1. Presentation rapide

**Fourmizzz** est un jeu de strategie en ligne de gestion de fourmilieres.
La metrique principale est le **TDC** (Terrain De Chasse) : chaque joueur en possede un certain
montant, qu'il peut augmenter en attaquant d'autres joueurs ou en chassant.

Ce depot contient **deux sous-systemes** :

| Sous-systeme | But | Comment il tourne |
|---|---|---|
| **Suivi TDC** (`ange_watch.py`) | Surveille les flux TDC de l'alliance | Cron toutes les heures |
| **Convois + requetes** (`ange_requete.py`) | Gere convois de bois, commandes SQL/ANALYSE | Service systemd continu |

---

## 2. Architecture

```
Fourmizzz API S5 (https://s5.fourmizzz.fr/api/exports/)
        |
        v cron toutes les heures
  ange_watch.py  ---ecrit--->  tdc_serveur.db  (SQLite, historique TDC)
        |                            ^
        v                            | lecture seule
  flux_tdc.csv    ange_requete.py ---+
  bilan_horaire.csv  (service systemd)
  ange_watch_etat.json   |
                         +-- toutes les minutes : tick suivi + convois
                         +-- detecte ange_*.py modifies -> recharge si AUTOTEST OK
                         +-- lit l'onglet Requete -> execute SQL/ANALYSE

  Alertes Discord  <---
  Google Sheets    <--- lecture/ecriture (gspread + service_account.json)
```

**Fichiers d'etat runtime** (generes automatiquement, non versiones) :
- `ange_watch_etat.json` : derniere version API traitee
- `tdc_serveur.db` : base SQLite (~500 Mo, toutes les versions horaires)
- `flux_tdc.csv` / `bilan_horaire.csv` : exports CSV
- `convois_*.json` : etats des convois en cours
- `log.txt` / `logs/` : journaux d'execution

---

## 3. Prerequis et installation

### Dependances systeme

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip
```

### Environnement Python

```bash
cd ~/ange_watch
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install gspread google-auth requests
```

`gspread` est **obligatoire** pour ecrire dans les Google Sheets.
Sans `gspread`, le suivi TDC local (CSV + SQLite) fonctionne, mais aucune ecriture vers les sheets ne sera possible.

### Fichiers non versiones a fournir

| Fichier | Contenu | Obligatoire |
|---|---|---|
| `service_account.json` | Cle JSON du compte de service Google (acces Sheets/Drive) | Oui |
| `ange_watch.ini` | Configuration de l'alliance (voir section 4) | Oui |

Copier et adapter :
```bash
cp ange_watch.ini.exemple ange_watch.ini
# Editer ange_watch.ini avec tes valeurs reelles
```

### Installation du service systemd

```bash
bash install_auto.sh
# Ce script :
# 1. Compile ange_requete.py pour detecter les erreurs de syntaxe
# 2. Installe le service systemd ange-requete
# 3. L'active au demarrage (systemctl enable)
# 4. Le demarre immediatement
```

Verification :
```bash
systemctl status ange-requete
journalctl -u ange-requete -f
```

### Installation des crons

```bash
crontab -e
# Ajouter ces deux lignes :
2,5,8,11,14 * * * * flock -n /tmp/ange_watch.lock -c "cd ~/ange_watch && ~/ange_watch/venv/bin/python ange_watch.py >> log.txt 2>&1"
0 23 * * * ~/ange_watch/venv/bin/python3 ~/ange_watch/appliquer_redistrib_nuit.py >> ~/ange_watch/logs/redistrib_nuit.log 2>&1
```

Le script `ange_watch.py` tourne aux minutes 2,5,8,11,14 (5 passes par heure).
Le `flock` evite les executions en parallele si une passe est lente.

---

## 4. Configuration (ange_watch.ini)

**Ne jamais commiter ce fichier** : il contient des secrets (webhook Discord, ID des sheets Google).
Le fichier `ange_watch.ini.exemple` est le modele a copier.

```ini
[general]
; Tag de l'alliance a surveiller
alliance = MON_ALLIANCE
; Perte d'un joueur plus petite que ca (cases) ignoree dans les alertes
seuil_perte = 1000
; Alliance mise en avant dans les alertes
surveiller = ALLIANCE_CIBLE
; Ces joueurs chassent (creent du TDC) : leurs gains ne comptent pas comme echanges
chasseurs = Joueur1, Joueur2, Joueur3
; Seuils d'alerte Discord (en cases de TDC)
seuil_alerte_sortant = 20000
seuil_alerte_taxe = 5000
seuil_alerte_inconnu = 40000

[google]
; ID du Google Sheet Suivi
; L'ID est dans l'URL : https://docs.google.com/spreadsheets/d/<ID>/edit
sheet_id = <ID_SHEET_SUIVI>
; Optionnel : ID du sheet prive
sheet_chaine_id =
; Chemin vers la cle JSON du compte de service Google
credentials = service_account.json

[discord]
; URL du webhook Discord pour les alertes (laisser vide pour desactiver)
webhook =
```

Apres chaque changement dans `ange_watch.ini`, le service relit la config automatiquement au prochain tick.

---

## 5. Description de chaque fichier Python

### Scripts principaux

#### ange_watch.py — Surveillance TDC horaire

Lance par cron toutes les heures (minutes 2,5,8,11,14).
Compare deux versions consecutives de l'API Fourmizzz pour detecter les echanges de TDC :

- **SORTANT** : un joueur de l'alliance perd des cases qu'un joueur adverse gagne exactement
- **ENTRANT** : un joueur de l'alliance gagne des cases prises a un joueur adverse
- **INTERNE** : echange entre deux joueurs de l'alliance (non alerte, compte dans le bilan)
- **NON EXPLIQUE** : perte sans contrepartie identifiable

Sorties : `tdc_serveur.db`, `flux_tdc.csv`, `bilan_horaire.csv`, alerte Discord si seuil depasse.

Premiere utilisation (rattrapage) :
```bash
venv/bin/python3 ange_watch.py --rattrapage 48
```

#### ange_requete.py — Demon principal (service systemd)

Tourne en continu (--daemon). Chaque **minute** il :
1. Verifie si un fichier `ange_*.py` a change sur disque (md5) -> compile + lance AUTOTEST() -> installe si ca passe
2. Lance `ange_suivi.tick()` (suivi horaire des qu'une nouvelle version API est disponible)
3. Lit l'onglet **Requete** du sheet Suivi -> execute les commandes SQL SELECT ou ANALYSE
4. Lance `ange_convois_tick.tick()` (veille des changements de membres/postes)

```bash
venv/bin/python3 ange_requete.py           # une seule passe (test)
venv/bin/python3 ange_requete.py --daemon  # mode demon
```

### Suivi TDC et analyses

#### ange_suivi.py — Suivi automatique de l'alliance

Appele a chaque nouvelle version horaire. Met a jour dans le sheet Suivi :
- **Chasse** : TDC chasse par chasseur et par jour
- **Chasse horaire** : heures ou chaque chasseur chasse
- **Fuites** : pertes avec preuve (joueur adverse qui gagne exactement le meme montant)
- **Attaquants** : classement des joueurs/alliances qui prennent du TDC
- **Activite** : attaques/jour, heures de jeu, retards de convois, score par membre
- **Classement** : maillons forts et faibles
- **Recap** : resume du jour (envoye aussi sur Discord a 8h00 Paris)

#### ange_alliances.py — Suivi des alliances adverses

Pour chaque alliance listee dans l'onglet Alliances du sheet Suivi :
- Cree et met a jour un onglet "Alliance TAG" : chiffres cles, TDC echange, arrivees/departs
- Alerte Discord si un joueur de l'alliance prend du TDC (preuve sure)

#### ange_analyse.py — Analyses SQL predefinies

Accessibles depuis l'onglet Requete du sheet (commande `ANALYSE NOM [param=val]`) :

| Analyse | Description |
|---|---|
| LIEN | Correlation entre pertes alliees et gains d'une alliance adverse |
| TAXE_CHAINE | Reconstitue la chaine d'une alliance adverse (qui nourrit qui) |
| RAPPORT | Tout en un : LAG, residus, attaquants, departs, oscillations |
| RESIDUS | Pertes internes qu'aucun gain interne n'explique |
| ATTAQUANTS | Joueurs adverses ayant probablement pris du TDC |
| LAG | Decalage API entre perte et gain contrepartie |
| DEPARTS | Joueurs qui ont quitte l'alliance |
| OSCILLATIONS | Manoeuvres pour entrer dans la plage d'attaque |

#### ange_extra.py — Analyses complementaires

Analyses supplementaires ajoutees sans toucher a `ange_analyse.py` (charge a chaud par le demon).

#### ange_attaques.py — Reconstruction des attaques

Identifie qui a attaque qui dans la chaine, avec estimation de l'heure d'envoi et du temps de trajet.
- Formule de trajet : `0.9^VA x 637200 x (1 - exp(-distance/350))` avec distance euclidienne
- Marge de TDC de la cible : +/- 15% (le TDC bouge pendant l'heure)

#### ange_note_activite.py — Notes d'activite quotidiennes

Calcule chaque matin a 5h00 Paris la note de J-1 par joueur :
- Passeurs/Greniers : `moyenne(note_placement, note_attaques)` sur 10, avec malus
- Chasseurs : `TDC_J1 / moyenne_7j x 10` (plafonne a 10)

### Convois

#### ange_convois.py — Coeur du module convois

Lecture du sheet prive (`charger_prive`), gestion des commandes/actions, quotas Google Sheets,
`avec_reprise()` pour les erreurs 429.

#### ange_convois_plan.py — Calcul du plan (pur, sans ecriture)

Algorithme de **flot a cout minimal** : cout = distance^3, contrainte limiter_partenaires = 12.
Priorite dans l'ordre : distance, O (ouvrieres), TDP.
Peut etre teste sans toucher aucun sheet.

#### ange_convois_ecr.py — Calcul + ecriture du plan de convois

Lance ange_convois_plan, ecrit dans les sheets : Base convois, Dettes, Historique,
et complete les formules des nouveaux joueurs.

#### ange_convois_nuit.py — Logique de cloture de nuit

Port Python de la fonction Apps Script `journeeChaines_`. Partie 1 = logique pure, partie 2 = Sheets.
Securite : refuse toute ecriture hors copies TEST, sauf config explicite.

#### ange_convois_prod.py — Cloture de minuit en PRODUCTION

S'execute uniquement si le fichier `convois_prod_arme.json` existe pour le jour concerne.
Garde-fous : controle a blanc avant ecriture, sauvegarde complete, ecriture atomique (batchUpdate), idempotent.

#### ange_convois_tick.py — Veille des membres/postes

Chaque minute, compare la liste des membres depuis le sheet prive avec l'etat precedent.
Si changement -> appelle `ange_convois_live.synchroniser`.
MODE (fichier `convois_mode.txt`) : "test" (defaut), "off", ou "prod".

#### ange_convois_live.py — Application des changements en direct

Quand un joueur change de poste, entre, ou sort de la chaine :
- Cree/masque/supprime/protege l'onglet du joueur dans les 3 fichiers publics
- Retire les convois lies au joueur des onglets des autres

#### ange_convois_arrivants.py — Suivi des mouvements de la chaine

Journal complet (entrees, changements de poste, sorties, hors chaine/retour) dans l'onglet Mouvements.
Gestion du statut **Nouveau** : exclut les nouveaux arrivants du calcul des convois pendant 1 jour.

#### ange_convois_controle.py — Controle horaire des livraisons

Verifie que "a envoyer = a recevoir" par joueur. Alerte Discord pour les anomalies.

#### ange_convois_redistrib.py — Redistribution des dettes (cas special)

Utilise quand un Grenier sort de la chaine avec des dettes importantes.
Ecrit directement dans les onglets publics des Greniers au lieu du sheet Dettes.

#### Autres modules convois

| Fichier | Role |
|---|---|
| ange_convois_pages.py | Pages individuelles joueurs |
| ange_convois_entetes.py | En-tetes des onglets |
| ange_convois_annexes.py | Donnees annexes |
| ange_convois_onglets.py | Gestion de l'ordre des onglets |
| ange_convois_forms.py | Liste deroulante Pseudo dans les formulaires |
| ange_convois_reponses.py | Traitement des reponses de formulaires |
| ange_convois_tdclive.py | TDC en temps reel |
| ange_convois_alertes.py | Alertes convois |
| ange_convois_malus.py | Systeme de malus (prepare, non active) |
| ange_convois_chasse.py | Bloc chasse dans les onglets |

#### ange_accueil.py — Page d'accueil du sheet Suivi

Recree l'onglet Accueil a chaque releve avec les liens vers tous les onglets.

#### appliquer_redistrib_nuit.py — Redistribution nuit (cron)

Lance par cron a 23h00 UTC (00h00 Paris) apres le plan de minuit.
Ajoute les lignes de redistribution dans les onglets publics des Greniers.

---

## 6. Services en production

### Service systemd ange-requete

```bash
systemctl status ange-requete          # statut
journalctl -u ange-requete -f          # logs en direct
systemctl restart ange-requete         # redemarrer
systemctl stop ange-requete            # arreter
```

**Ne pas redemarrer entre 21h30 et 23h00 (heure de Paris)** : la cloture des convois est en cours.

### Crons actifs

```
# Suivi TDC toutes les heures (5 passes pour rattraper si une rate)
2,5,8,11,14 * * * * flock -n /tmp/ange_watch.lock -c "cd ~/ange_watch && ~/ange_watch/venv/bin/python ange_watch.py >> log.txt 2>&1"

# Redistribution dettes nuit (00h00 Paris = 23h00 UTC)
0 23 * * * ~/ange_watch/venv/bin/python3 ~/ange_watch/appliquer_redistrib_nuit.py >> ~/ange_watch/logs/redistrib_nuit.log 2>&1
```

---

## 7. Base de donnees SQLite (tdc_serveur.db)

Fichier unique non versionne (~500 Mo). Contient une photo horaire du TDC de chaque joueur depuis octobre 2026.

### Schema de la table principale : tdc

```sql
CREATE TABLE tdc (
    version  TEXT,    -- format AAAAMMJJHHMM (UTC), ex: "202611190900"
    pseudo   TEXT,    -- nom du joueur en jeu
    alliance TEXT,    -- tag de l'alliance (ex: "MON_ALLIANCE", "ADVERSAIRE", "")
    tdc      INTEGER  -- TDC du joueur a cette version horaire
);
CREATE INDEX idx_tdc_version ON tdc(version);
CREATE INDEX idx_tdc_pseudo  ON tdc(pseudo);
```

### Requetes utiles

```sql
-- Derniere version disponible
SELECT MAX(version) FROM tdc;

-- TDC des membres de l'alliance a la derniere heure
SELECT pseudo, tdc FROM tdc
WHERE version = (SELECT MAX(version) FROM tdc)
  AND alliance = 'MON_ALLIANCE'
ORDER BY tdc DESC;

-- Evolution d'un joueur sur 7 jours
SELECT version, tdc FROM tdc
WHERE pseudo = 'NomDuJoueur'
  AND version >= strftime('%Y%m%d%H%M', datetime('now', '-7 days'))
ORDER BY version;

-- Flux entre deux heures consecutives
SELECT a.pseudo, b.tdc - a.tdc AS variation, a.alliance
FROM tdc a JOIN tdc b ON a.pseudo = b.pseudo
WHERE a.version = '202611190800'
  AND b.version = '202611190900'
  AND a.alliance = 'MON_ALLIANCE'
ORDER BY variation;
```

Le code accede toujours a cette base en lecture seule via `PRAGMA query_only=ON`
(sauf ange_watch.py qui ecrit).

---

## 8. Google Sheets impliques

**Ne jamais supprimer ni reinitialiser ces sheets** : ils contiennent l'historique de l'alliance.
Les IDs reels sont dans `ange_watch.ini` (non versionne, a demander a l'admin).

| Sheet | Role |
|---|---|
| **Suivi** (public alliance) | Boite aux lettres VPS (onglet Requete), flux TDC, bilans, recaps, classements |
| **Prive - Chaine joueurs** | Source de verite des postes, dettes, TDC moyen, nouveaux, interdits — **NE JAMAIS CASSER** |
| **Public - Greniers** | Convois par grenier (un onglet par joueur actif) |
| **Public - Passeurs** | Convois par passeur |
| **Public - Chasseurs** | Convois par chasseur |

### Droits necessaires pour le compte de service

Le `service_account.json` doit etre **editeur** sur les 5 sheets ci-dessus.
Partager chaque sheet avec l'email du compte de service (format `xxx@xxx.iam.gserviceaccount.com`).

### Onglet Requete (boite aux lettres du VPS)

Interface principale pour envoyer des commandes au VPS sans acces SSH :
- **Colonne A** : la commande (SQL SELECT ou ANALYSE ...)
- **Colonne B** : ecrire `OUI` pour l'executer -> le demon repond `FAIT hh:mm · N lignes -> R<n>`
- **Resultat** dans l'onglet R<n> correspondant
- **Cellule E1** : statut en direct du demon (mis a jour chaque minute)

---

## 9. Modifier du code (auto-deploiement)

**Le demon `ange-requete` surveille chaque minute le repertoire ~/ange_watch/.**
Quand le MD5 d'un fichier `ange_*.py` change :
1. Compilation Python (`py_compile`)
2. Execution de `AUTOTEST()` si elle existe dans le module
3. Si tout passe : le module est recharge en memoire **sans redemarrage**
4. Si ca plante : l'ancienne version reste en production, erreur dans les logs

**Workflow de modification :**

```bash
# 1. Editer le fichier directement sur le VPS
nano ~/ange_watch/ange_suivi.py

# 2. Verifier la syntaxe
venv/bin/python3 -m py_compile ange_suivi.py && echo "OK"

# 3. Le demon detecte le changement dans la minute
journalctl -u ange-requete -f | grep "AUTOTEST|reload|ERREUR"
```

`ange_requete.py` lui-meme **n'est jamais remplace par le demon**.
Pour le modifier : `systemctl stop ange-requete` -> editer -> `systemctl start ange-requete`.

---

## 10. Commandes manuelles utiles

### Depuis l'onglet Requete du sheet Suivi

```
ANALYSE RAPPORT
ANALYSE RESIDUS vue=detail depuis=202611010000
ANALYSE ATTAQUANTS alliance=ALLIANCE_CIBLE top=20
ANALYSE LIEN
ANALYSE DEPARTS
ANALYSE LAG
ANALYSE OSCILLATIONS

SELECT pseudo, tdc FROM tdc WHERE version = (SELECT MAX(version) FROM tdc) AND alliance = 'MON_ALLIANCE' ORDER BY tdc DESC

ANALYSE CONVOIS action=prod_etat
ANALYSE CONVOIS action=coherence cible=prod
ANALYSE CONVOIS action=pages_ecrire cible=prod
ANALYSE CONVOIS action=plan_prod_sim pseudo=NomDuJoueur
ANALYSE CONVOIS action=veille_blanc
```

### Depuis le terminal VPS

```bash
cd ~/ange_watch

# Lancer une passe manuelle du suivi TDC
venv/bin/python3 ange_watch.py

# Rattrapage des 48 dernieres heures
venv/bin/python3 ange_watch.py --rattrapage 48

# Tester le plan de convois sans rien ecrire
venv/bin/python3 -c "import ange_convois_ecr as E; print(E.comparer())"

# Redistribution dettes nuit manuellement
venv/bin/python3 appliquer_redistrib_nuit.py
```

---

## 11. Regles strictes a respecter

### NE JAMAIS faire

1. **Modifier quoi que ce soit entre 21h30 et 23h00 (heure de Paris)** : la cloture des convois
   est en cours. Un bug a cet instant corrompt les donnees de tous les joueurs pour la nuit.

2. **Supprimer definitivement un fichier** : toujours creer un `.bak` d'abord.

3. **Executer `installerDeclencheursChaines` dans Apps Script** : tout est gere par le VPS,
   ce script reinitialiserait la configuration et casserait les convois.

4. **Ecrire en production sans armer** : pour la cloture de minuit en prod, `convois_prod_arme.json`
   doit exister pour le jour concerne. Creer ce fichier sans verification peut corrompre
   les sheets de tous les joueurs.

5. **Commiter `ange_watch.ini` ou `service_account.json`** : ces fichiers contiennent des secrets.

### A toujours faire

- Tester avant d'activer : ecrire=non / copies TEST, AUTOTEST(), simulation d'abord
- Sauvegarder avant de modifier un fichier JSON d'etat important
- Verifier la syntaxe avant de pusher un fichier Python
- Lire `CONTEXTE_ANGE.md` en entier avant d'intervenir : il contient les regles du jeu
  et les conventions specifiques a cette alliance

---

## 12. Depannage

### Le service ange-requete ne tourne pas

```bash
systemctl status ange-requete
journalctl -u ange-requete --since "1 hour ago"
systemctl restart ange-requete
```

### Les feuilles Google ne se mettent plus a jour

1. Verifier que `service_account.json` existe et est valide (non expire)
2. Verifier que le compte de service a toujours les droits editeur sur les sheets
3. Regarder les logs pour les erreurs gspread ou 429
4. Les 429 (quota depasse) se reglent seuls via `avec_reprise()` — attendre quelques minutes

### Erreur "attempt to write a readonly database"

La base `notes_activite.db` a probablement ete creee en root. Corriger :
```bash
chown ubuntu:ubuntu ~/ange_watch/notes_activite.db
chmod 644 ~/ange_watch/notes_activite.db
```

### Le cron TDC ne tourne plus

```bash
# Verifier les logs
tail -50 ~/ange_watch/log.txt

# Tester manuellement
cd ~/ange_watch && venv/bin/python3 ange_watch.py

# Verifier le lock (si bloque par un process mort)
ls -la /tmp/ange_watch.lock
rm /tmp/ange_watch.lock   # si le process n'existe plus
```

### Un module a plante apres une modif de code

Le demon garde l'ancienne version en memoire. Pour forcer la detection :
```bash
touch ~/ange_watch/ange_suivi.py   # change le MD5
journalctl -u ange-requete -f      # surveiller les logs
```

### La chaine depasse ~94 joueurs

Certaines plages sont codees en dur (`A3:AH96`, `$C$97`). Si la chaine approche 94 lignes,
agrandir ces plages dans :
- `ange_convois.py` -> fonction `lire_prive()`
- `ange_suivi.py`
- `ange_convois_arrivants.py` -> fonction `lire_membres()`

---

## 13. Historique des changements majeurs

| Date | Changement |
|---|---|
| Oct. 2026 | Debut de l'enregistrement de tdc_serveur.db |
| Nov. 2026 | Migration complete Apps Script -> VPS, arret de installerDeclencheursChaines |
| Nov. 2026 | Statut Nouveau (arrivants), distance euclidienne corrigee, deploiement direct via VPS |
| Nov. 2026 | Redistribution des dettes d'un Grenier sorti de la chaine, cron 23h00 UTC |

---

## Notes pour le successeur

- **Avant d'intervenir** : lire `CONTEXTE_ANGE.md` en entier. Il contient les regles du jeu,
  la mecanique des convois, les conventions de l'alliance et les decisions de conception importantes.
- **Les IDs des Google Sheets** sont dans `ange_watch.ini` (non versionne, a demander a l'admin).
- **Prudence avec la production** : toujours tester sur les copies TEST (`ange_convois.ouvrir_test`)
  avant de passer en prod.
- **Le webhook Discord** dans `ange_watch.ini` est a regenerer si l'URL a ete exposee :
  aller dans Parametres du serveur Discord -> Integrations -> Webhooks -> regenerer l'URL,
  puis mettre a jour `ange_watch.ini`.
