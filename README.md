# ange_watch — Automatisation des convois Fourmizzz

> Systeme de gestion des convois de bois pour une alliance Fourmizzz (serveur S5).
> Chemin sur le VPS : ~/ange_watch/ — Derniere MAJ : novembre 2026

---

## Sommaire

1. Concept : la chaine et les convois
2. Calcul du plan de convois
3. Cycle quotidien de cloture
4. Ajouter un joueur (statut Nouveau)
5. Mettre un joueur hors chaine
6. Changement de poste
7. Les dettes
8. Les Interdits (meme foyer)
9. Systeme de malus (prepare, non active)
10. Architecture technique
11. Google Sheets impliques
12. Services en production
13. Base de donnees SQLite (tdc_serveur.db)
14. Modifier du code (auto-deploiement)
15. Description des fichiers Python
16. Commandes utiles
17. Regles strictes
18. Depannage

---

## 1. Concept : la chaine et les convois

### Le TDC et les postes

Dans Fourmizzz, le TDC (Terrain De Chasse) est la mesure de puissance de chaque joueur.
L'alliance organise ses membres en **chaine unique** : une hierarchie de ~80 joueurs repartis
en trois types de postes.

| Poste | Role dans la chaine | Convois |
|---|---|---|
| **Grenier 1…5** | Tiennent le plus gros TDC, en haut de la chaine | Recoivent du bois |
| **Passeur 1…8** | Intermediaires | Recoivent et envoient du bois |
| **Chasseur** | Creent du TDC en chassant (attaque vers "personne") | Ont une dette (recoivent) |

Chaque joueur peut attaquer les joueurs de poste inferieur dans une plage de TDC (50% a 300% du sien).
Le bois (convois) circule vers le haut : les passeurs hauts et les greniers livrent aux autres.

### Ce que sont les convois

**Convois = transferts de bois entre joueurs**. 1 ouvriere (O) porte 13 bois.
Le plan de convois repond a la question : *qui livre combien a qui, chaque soir ?*

Le but est que chaque joueur ait assez de bois pour maintenir/augmenter son TDC
en fonction de son poste dans la chaine.

**Regle cle** : les convois sont calcules **une seule fois par soir a 22h00 (heure de Paris)**.
On ne les modifie pas en cours de journee, sauf si un joueur sort de la chaine ou change de poste
(dans ce cas seulement sa page est mise a jour).

---

## 2. Calcul du plan de convois

### Inputs

Le plan est calcule a partir des donnees du sheet prive (`charger_prive`) :

| Donnee | Source | Description |
|---|---|---|
| TDC moyen J-1 | Colonne AC du sheet prive | Moyenne TDC sur la fenetre 22h->22h de la veille |
| Delta | Colonne AF du sheet prive | Ce que chaque joueur doit recevoir (negatif) ou livrer (positif) |
| Position X, Y | Colonne Data du sheet prive | Coordonnees du joueur sur la carte |
| Ouvrières (O) | Colonne Data | Capacite de transport : 1 O porte 13 bois |
| TDP | Colonne Data | Technologie de ponte (vitesse de production d ouvrières) |
| Poste | Colonne B du sheet prive | Grenier/Passeur/Chasseur + numero |
| Interdits | Onglet Interdits | Paires de joueurs du meme foyer (ne se livrent jamais) |
| Hors chaine | Colonne Y du sheet prive | Si Y=TRUE, joueur exclu du calcul |
| Statut Nouveau | Onglet Nouveaux du sheet prive | Exclu du calcul pendant j+1 apres entree |

### Algorithme : flot a cout minimal

Le code Python (`ange_convois_plan.py`) resout un **probleme de flot a cout minimal** :

- **Chaque joueur** est soit un fournisseur (delta > 0 : livre du bois) soit un demandeur (delta < 0 : recoit du bois)
- **Contrainte principale** : un joueur ne peut pas avoir plus de `limiter_partenaires` = 12 partenaires de livraison
- **Contrainte Interdits** : deux joueurs du meme foyer ne se livrent jamais
- **Fonction de cout** : `distance^3` ou distance = `sqrt(dx^2 + dy^2)` (euclidienne, PAS |dx|+|dy|)

L exposant 3 favorise fortement les paires proches. L exposant 4 serait encore plus restrictif
(refuse lors de la conception du systeme).

**Priorite pour placer un nouveau joueur** (humain, pas automatique) :
1. Distance et absence de trous dans les postes
2. Nombre d ouvrières (O)
3. TDP
Le TDC bouge trop vite pour servir de critere de placement.

### Sorties

Le plan produit une liste de livraisons : (expediteur, destinataire, montant en bois).
`ange_convois_ecr.py` ecrit ensuite dans les sheets :
- `Base convois` du sheet prive
- `Dettes` du sheet prive
- `Historique` du sheet prive
- Onglets joueurs des 3 fichiers publics (Greniers, Passeurs, Chasseurs)

---

## 3. Cycle quotidien de cloture

```
22h00 Paris  : calcul du plan (ange_convois_ecr.py) + ecriture sheet prive
               -> Base convois, Dettes, Historique mis a jour
               -> le plan calcule les convois de DEMAIN (fenetre TDC de ce soir 22h -> demain 22h)

00h00 Paris  : cloture en PRODUCTION (ange_convois_prod.py)
               -> verifie que convois_prod_arme.json existe pour ce jour
               -> controle a blanc d abord (coherence)
               -> sauvegarde complete du jour precedent
               -> ecriture atomique (batchUpdate) dans les 3 fichiers publics
               -> idempotent : peut etre re-execute sans double-ecriture

00h50 Paris  : redistribution dettes si active (appliquer_redistrib_nuit.py)
               -> cas special : dettes d un grenier sorti de la chaine
```

**Ne rien modifier entre 21h30 et 23h00 (heure de Paris).**
La cloture de 22h est en cours. Un bug a cet instant corrompt les donnees de tous les joueurs.

### Armer la production

La cloture de minuit n ecrit en production **que si** le fichier `convois_prod_arme.json`
existe pour le jour courant. Ce garde-fou evite une ecriture accidentelle.

```bash
# Verifier si la production est armee
ANALYSE CONVOIS action=prod_etat   # depuis l onglet Requete du sheet

# Verifier la coherence avant ecriture
ANALYSE CONVOIS action=coherence cible=prod

# Forcer la re-ecriture des pages (si besoin)
ANALYSE CONVOIS action=pages_ecrire cible=prod
```

---

## 4. Ajouter un joueur (statut Nouveau)

### Comment ajouter un joueur

1. Remplir les colonnes dans l onglet `Data` du sheet prive (pseudo, X, Y, TDC, O, TDP, VA, poste...)
2. Ajouter une ligne dans l onglet `Chaine unique` du sheet prive (colonne B = poste, C = pseudo, Y = FALSE)
3. L onglet du joueur est cree automatiquement dans les 3 fichiers publics par `ange_convois_live.py`

### Le statut Nouveau

Tout joueur qui ENTRE dans la chaine recoit automatiquement le statut **Nouveau** :

- Il est **visible partout** : son onglet est cree, il apparait dans les listes et formulaires
- Il est **exclu du calcul des convois** pendant 1 jour (jusqu au plan du soir du lendemain)
- L onglet `Nouveaux` du sheet prive contient : Pseudo, Poste, Entre le, Pas de convoi jusqu au plan du

**Pourquoi ce delai ?** Pour eviter qu un joueur entre dans le plan avec des donnees TDC trop fraiches
(la moyenne J-1 n est pas encore stabilisee).

**Forcer l entree immediate au calcul** : supprimer la ligne du joueur dans l onglet `Nouveaux`.

**Tester le plan SANS ecrire** pour voir si le nouveau est bien integre :
```
ANALYSE CONVOIS action=plan_prod_sim pseudo=NomDuJoueur  # depuis l onglet Requete
```

### Mecanisme technique

Le statut Nouveau est un **filtre Python** dans `charger_prive()` / `lire_base()` :
- La variable `exclus_nouveaux` contient la liste des joueurs en statut Nouveau
- Ces joueurs ont `d is None` dans la structure de donnees -> absents de `joueurs` -> pas de convois

**Difference avec hors chaine** : le hors chaine est un filtre SHEET (formule colonne AF = vide si Y=TRUE).
Les deux arrivent au meme resultat (`d is None`), mais les mecanismes sont distincts.

Le journal complet des entrees/sorties/changements est dans l onglet `Mouvements` du sheet prive
(gere par `ange_convois_arrivants.py`).

---

## 5. Mettre un joueur hors chaine

**Hors chaine** = le joueur reste dans le sheet mais ne participe plus aux convois.
Usage typique : joueur inactif, absent temporaire, ou qui quitte l alliance.

### Comment mettre un joueur hors chaine

Dans l onglet `Chaine unique` du sheet prive : **cocher la case Y** de la ligne du joueur
(ou barrer la ligne). C est tout - le systeme reagit automatiquement.

### Ce qui se passe automatiquement

1. **Cote sheet** : la formule colonne AF renvoie vide (`=IF(OR($C61="";$Y61=TRUE);""…)`)
   -> les colonnes de recolte/flux/delta passent a 0 ou vide
2. **Cote Python** : le joueur a `d is None` -> absent de `joueurs` -> exclu du prochain plan
3. **Onglet** : l onglet du joueur est **masque** (pas supprime) dans les 3 fichiers publics
4. **Listes d attaque** : le joueur est retire des listes des autres joueurs
5. **Exception : liste Attaque seulement** : on l attaque / il attaque, mais sans convois

### Les dettes en cas de mise hors chaine

Les dettes du joueur restent dans l onglet `Dettes` - elles ne sont **pas effacees**.
Elles sont **gelees** : le plan ne les rembourse pas tant que le joueur est hors chaine.
Elles reprennent automatiquement si le joueur revient dans la chaine (decocher Y).

### Cas special : redistribution des dettes d un Grenier hors chaine

Les Greniers ont un delta >= 0 (ils livrent). Quand un Grenier sort avec des dettes importantes,
le plan normal ne peut pas les absorber. Mecanisme special :
- `ange_convois_redistrib.py` calcule la redistribution
- Les lignes de remboursement sont ecrites **directement dans les onglets publics** des Greniers
  (et non dans l onglet Dettes)
- `appliquer_redistrib_nuit.py` applique la redistribution chaque nuit pendant N jours (cron 00h50 Paris)

### Retour dans la chaine

Decocher la case Y. Le joueur passe par le statut Nouveau (1 jour sans convois),
ses dettes gelees reprennent, son onglet est restaure.

---

## 6. Changement de poste

**Changement de poste** = un joueur passe de Passeur 3 a Grenier 2, ou de Chasseur a Passeur 1, etc.

### Comment changer le poste d un joueur

Dans l onglet `Chaine unique` du sheet prive : **modifier la colonne B** (poste) du joueur.
Le demon detecte le changement au prochain tick (chaque minute).

### Ce qui se passe automatiquement

`ange_convois_tick.py` surveille en permanence les changements dans le sheet prive.
Quand un changement de poste est detecte, `ange_convois_live.synchroniser()` est appele :

1. L ancien onglet du joueur est **deplace/renomme** dans le bon fichier public
   (si le type change : Passeur -> Grenier, l onglet migre du fichier Passeurs vers le fichier Greniers)
2. Les **listes d attaque** des autres joueurs sont recalculees
3. Les **formules** de l onglet du joueur sont mises a jour pour son nouveau poste
4. Le changement est enregistre dans l onglet **Mouvements** du sheet prive

### Impact sur les convois

- Le prochain plan de convois (22h) utilisera le nouveau poste
- Le delta du joueur change (un Chasseur qui devient Passeur passe d un delta negatif a positif)
- **Pas de statut Nouveau** pour un changement de poste (contrairement a une entree dans la chaine) :
  le joueur entre immediatement dans le plan avec son nouveau poste

---

## 7. Les dettes

Les dettes representent le bois qu un joueur doit encore recevoir ou livrer en plus de ses convois normaux.
Elles s accumulent quand un joueur n a pas pu livrer (ou n a pas ete livre) les jours precedents.

### Stockage

L onglet **Dettes** du sheet prive contient une ligne par joueur avec le solde de sa dette.

### Integration dans le plan

Le plan de convois integre les dettes : un joueur avec une dette positive livre un peu plus que son
delta normal jusqu a ce que la dette soit remboursee.

### Cas particuliers

| Situation | Comportement |
|---|---|
| Joueur hors chaine | Dettes gelees, non remboursees jusqu au retour |
| Joueur en statut Nouveau | Pas de convois donc pas de remboursement pendant j+1 |
| Grenier hors chaine avec grosses dettes | Redistribution directe dans les onglets publics (voir section 5) |

### Controle des dettes

```bash
# Verification a blanc depuis l onglet Requete :
ANALYSE CONVOIS action=coherence cible=prod

# Voir l etat des dettes de redistribution :
# lire dettes_redistrib.json dans ~/ange_watch/
```

---

## 8. Les Interdits (meme foyer)

Deux joueurs du **meme foyer** ne doivent jamais se livrer de bois l un a l autre.
Cela creerait une circularite (le bois revient toujours au meme foyer).

### Gestion

L onglet **Interdits** du sheet prive contient les paires de joueurs concernes.
L algorithme de plan respecte cette contrainte : il ne cree jamais de livraison entre deux joueurs interdits,
meme si c est geometriquement optimal.

### Ajouter une paire Interdits

Ajouter une ligne dans l onglet `Interdits` du sheet prive (deux colonnes : Pseudo1, Pseudo2).
Le prochain plan le prendra en compte.

---

## 9. Systeme de malus (prepare, non active)

Permet de reduire temporairement la part de bois d un joueur pendant X jours.

**Fichiers** : `malus.json` (config modifiable direct) + `ange_convois_malus.py` (module)

**Principe** :
- Delta < 0 (chasseur/passeur) : recoit `montant_par_jour` de moins chaque jour
- Delta > 0 (grenier) : livre `montant_par_jour` de moins chaque jour

**CLI** :
```bash
venv/bin/python3 ange_convois_malus.py action=ajouter pseudo=NomJoueur montant=5000 jours=3 raison=retard
venv/bin/python3 ange_convois_malus.py action=lister
venv/bin/python3 ange_convois_malus.py action=supprimer pseudo=NomJoueur
venv/bin/python3 ange_convois_malus.py action=resume
```

**Activation** : decommenter `MALUS_HOOK` et `MALUS_TICK` dans `ange_convois_ecr.py` (2 lignes chacun).

---

## 10. Architecture technique

```
Fourmizzz API S5 (https://s5.fourmizzz.fr/api/exports/)
        |
        v cron toutes les heures
  ange_watch.py  ---ecrit--->  tdc_serveur.db  (SQLite, historique TDC)

  ange_requete.py (service systemd ange-requete, boucle 1 minute)
        |
        +-- detecte ange_*.py modifies -> recharge si AUTOTEST OK
        +-- suivi TDC horaire (ange_suivi.tick)
        +-- lit onglet Requete -> execute SQL/ANALYSE
        +-- tick convois (ange_convois_tick.tick)
        +-- calcul plan 22h (ange_convois_ecr)
        +-- cloture minuit (ange_convois_prod)

  Alertes Discord  <---
  Google Sheets    <--- lecture/ecriture (gspread + service_account.json)
```

**Quota Google Sheets** : limites par MINUTE (pas de solde cumulatif). Un 429 se regle
automatiquement via `avec_reprise()` dans `ange_convois.py` — attendre quelques secondes.

---

## 11. Google Sheets impliques

**Ne jamais supprimer ni reinitialiser ces sheets.** Les IDs reels sont dans `ange_watch.ini`.

| Sheet | Acces | Role principal |
|---|---|---|
| **Prive - Chaine joueurs** | Editeur | Source de verite : postes, dettes, TDC moyen, Nouveaux, Interdits — **NE JAMAIS CASSER** |
| **Public - Greniers** | Editeur | Un onglet par Grenier actif : livraisons a faire, receptions |
| **Public - Passeurs** | Editeur | Un onglet par Passeur actif |
| **Public - Chasseurs** | Editeur | Un onglet par Chasseur actif |
| **Suivi** | Editeur | Boite aux lettres VPS (onglet Requete), flux TDC, bilans |

### Onglets du sheet prive (cles)

| Onglet | Contenu |
|---|---|
| `Chaine unique` | Ligne par joueur : col.A=groupe, B=poste, C=pseudo, Y=hors-chaine, AC=TDC moy J-1, AF=delta |
| `Data` | Stats joueurs : X, Y, O, TDP, VA, CdF, FdF, poste, derniere MAJ |
| `Dettes` | Solde de dettes par joueur |
| `Base convois` | Plan du jour (expediteur, destinataire, montant) |
| `Historique` | Historique des plans precedents |
| `Interdits` | Paires de joueurs du meme foyer |
| `Nouveaux` | Joueurs en attente de premiere inclusion dans le plan |
| `Mouvements` | Journal entrees/sorties/changements de poste |
| `Controle convois` | Verification a envoyer = a recevoir |
| `Attaque seulement` | Joueurs hors chaine qu on attaque quand meme |

### Onglets joueurs (fichiers publics)

Chaque joueur actif a son propre onglet dans le fichier public de son type (Grenier/Passeur/Chasseur) :
- `A9:I20` : livraisons a faire (nom, du, tout envoye, envois 1-4, retard...)
- `A25:B36` : receptions attendues
- Listes d attaque (qui attaquer, qui peut attaquer ce joueur)

### Droits necessaires

Le `service_account.json` doit etre **editeur** sur les 5 sheets.
Partager chaque sheet avec l email du compte de service (format `xxx@xxx.iam.gserviceaccount.com`).

### Onglet Requete (interface de commande)

Interface pour envoyer des commandes au VPS sans acces SSH :
- **Colonne A** : commande (`ANALYSE CONVOIS action=...` ou requete SQL)
- **Colonne B** : ecrire `OUI` pour executer
- **Reponse** : `FAIT hh:mm · N lignes -> R<n>` + resultat dans l onglet R<n>
- **Cellule E1** : statut du demon (mis a jour chaque minute)

---

## 12. Services en production

### Service systemd ange-requete

```bash
systemctl status ange-requete
journalctl -u ange-requete -f
systemctl restart ange-requete
```

**Ne pas redemarrer entre 21h30 et 23h00 (heure de Paris).**

### Prerequis et installation

```bash
cd ~/ange_watch
python3 -m venv venv
venv/bin/pip install gspread google-auth requests
bash install_auto.sh   # installe + active le service systemd
```

Fichiers a fournir (non versiones) :
- `service_account.json` : cle JSON du compte de service Google
- `ange_watch.ini` : configuration (copier depuis `ange_watch.ini.exemple`)

### Crons actifs

```
# Suivi TDC (5 passes par heure)
2,5,8,11,14 * * * * flock -n /tmp/ange_watch.lock -c "cd ~/ange_watch && ~/ange_watch/venv/bin/python ange_watch.py >> log.txt 2>&1"

# Redistribution dettes nuit (00h50 Paris = 23h50 UTC)
50 23 * * * ~/ange_watch/venv/bin/python3 ~/ange_watch/appliquer_redistrib_nuit.py >> ~/ange_watch/logs/redistrib_nuit.log 2>&1
```

---

## 13. Base de donnees SQLite (tdc_serveur.db)

Historique horaire du TDC de tous les joueurs du serveur (~500 Mo).

```sql
CREATE TABLE tdc (
    version  TEXT,    -- AAAAMMJJHHMM UTC, ex: "202611190900"
    pseudo   TEXT,    -- nom du joueur
    alliance TEXT,    -- tag d alliance
    tdc      INTEGER  -- TDC a cette heure
);
```

Le code Python accede toujours en lecture seule (`PRAGMA query_only=ON`).
Seul `ange_watch.py` ecrit dans cette base.

---

## 14. Modifier du code (auto-deploiement)

Le demon `ange-requete` surveille chaque minute tous les fichiers `ange_*.py`.
Quand le MD5 d un fichier change :
1. Compilation Python (py_compile)
2. Execution de `AUTOTEST()` si elle existe
3. Si OK : module recharge en memoire **sans redemarrage**
4. Si erreur : ancienne version conservee, erreur dans les logs

```bash
# Editer, verifier la syntaxe, puis le demon recharge dans la minute
nano ~/ange_watch/ange_convois_plan.py
venv/bin/python3 -m py_compile ange_convois_plan.py && echo OK
journalctl -u ange-requete -f | grep AUTOTEST
```

`ange_requete.py` lui-meme doit etre modifie manuellement :
`systemctl stop ange-requete` -> editer -> `systemctl start ange-requete`

---

## 15. Description des fichiers Python

### Convois (fichiers principaux)

| Fichier | Role |
|---|---|
| ange_convois.py | Coeur : lecture sheet prive, dispatch commandes/actions, with_reprise pour 429 |
| ange_convois_plan.py | Algorithme flot cout minimal — SANS ecriture (testable seul) |
| ange_convois_ecr.py | Calcul plan + ecriture sheet prive (Base convois, Dettes, Historique) |
| ange_convois_prod.py | Cloture PRODUCTION : ecrit dans les 3 fichiers publics a minuit |
| ange_convois_nuit.py | Logique de cloture (port Python de journeeChaines_) |
| ange_convois_tick.py | Veille membres/postes chaque minute |
| ange_convois_live.py | Applique les changements en direct sur les onglets |
| ange_convois_arrivants.py | Statut Nouveau, journal Mouvements |
| ange_convois_redistrib.py | Redistribution dettes grenier hors chaine |
| appliquer_redistrib_nuit.py | Applique redistribution chaque nuit (cron 00h50 Paris) |
| ange_convois_controle.py | Controle horaire a envoyer = a recevoir |
| ange_convois_malus.py | Systeme malus (non active) |

### Convois (onglets et mise en page)

| Fichier | Role |
|---|---|
| ange_convois_pages.py | Pages individuelles joueurs |
| ange_convois_entetes.py | En-tetes des onglets |
| ange_convois_annexes.py | Donnees annexes |
| ange_convois_onglets.py | Ordre des onglets |
| ange_convois_forms.py | Liste deroulante Pseudo dans les formulaires |
| ange_convois_reponses.py | Traitement des reponses de formulaires |
| ange_convois_chasse.py | Bloc chasse dans les onglets |
| ange_convois_tdclive.py | TDC en temps reel |
| ange_convois_alertes.py | Alertes convois |
| ange_accueil.py | Page d accueil du sheet Suivi |

### Suivi TDC et analyses

| Fichier | Role |
|---|---|
| ange_watch.py | Surveillance TDC horaire, ecriture tdc_serveur.db |
| ange_requete.py | Demon principal systemd |
| ange_suivi.py | Suivi chasse, fuites, activite, recap Discord |
| ange_alliances.py | Suivi des alliances adverses |
| ange_attaques.py | Reconstruction attaques avec estimation temps trajet |
| ange_analyse.py | Analyses SQL predefinies (LIEN, TAXE_CHAINE, RAPPORT...) |
| ange_extra.py | Analyses complementaires (charge a chaud) |
| ange_note_activite.py | Notes d activite quotidiennes par joueur |

---

## 16. Commandes utiles

### Depuis l onglet Requete du sheet Suivi

```
# Plan et production
ANALYSE CONVOIS action=prod_etat                             # etat de la prod
ANALYSE CONVOIS action=coherence cible=prod                  # verification avant ecriture
ANALYSE CONVOIS action=pages_ecrire cible=prod               # forcer re-ecriture
ANALYSE CONVOIS action=plan_prod_sim pseudo=NomDuJoueur      # simuler plan sans ecrire
ANALYSE CONVOIS action=veille_blanc                          # test de la veille

# TDC
SELECT pseudo, tdc FROM tdc WHERE version=(SELECT MAX(version) FROM tdc) AND alliance='MON_ALLIANCE' ORDER BY tdc DESC

# Analyses
ANALYSE RAPPORT
ANALYSE DEPARTS
```

### Depuis le terminal VPS

```bash
cd ~/ange_watch

# Plan convois sans ecrire
venv/bin/python3 -c "import ange_convois_ecr as E; print(E.comparer())"

# Redistribution dettes nuit manuellement
venv/bin/python3 appliquer_redistrib_nuit.py

# Rattrapage TDC
venv/bin/python3 ange_watch.py --rattrapage 48

# Malus
venv/bin/python3 ange_convois_malus.py action=lister
venv/bin/python3 ange_convois_malus.py action=ajouter pseudo=X montant=5000 jours=3
```

---

## 17. Regles strictes

1. **Rien entre 21h30 et 23h00 (Paris)** — cloture en cours
2. **Ne pas supprimer** de fichier sans faire un .bak d abord
3. **Ne jamais executer `installerDeclencheursChaines`** dans Apps Script
4. **Ne pas armer la prod sans verification** — `convois_prod_arme.json` doit exister
5. **Ne pas commiter `ange_watch.ini` ou `service_account.json`**
6. **Lire `CONTEXTE_ANGE.md`** avant d intervenir

---

## 18. Depannage

### Le service ange-requete ne tourne pas
```bash
systemctl status ange-requete
journalctl -u ange-requete --since "1 hour ago"
systemctl restart ange-requete
```

### Les feuilles Google ne se mettent plus a jour
- Verifier que `service_account.json` est valide (non expire)
- Verifier les droits editeur du compte de service
- 429 ? Se regle seul via `avec_reprise()` — attendre quelques minutes

### Le cron TDC ne tourne plus
```bash
tail -50 ~/ange_watch/log.txt
ls -la /tmp/ange_watch.lock
rm /tmp/ange_watch.lock   # si process mort
```

### La chaine depasse ~94 joueurs
Plages codees en dur `A3:AH96` / `$C$97`. Agrandir dans :
- `ange_convois.py` -> `lire_prive()`
- `ange_suivi.py`
- `ange_convois_arrivants.py` -> `lire_membres()`

### Notes pour le successeur

- **Lire `CONTEXTE_ANGE.md`** : contient les regles du jeu, la mecanique detaillee et l historique
- **IDs des sheets** : dans `ange_watch.ini` (non versionne, a demander a l admin)
- **Toujours tester** avant la prod : `ecrire=non`, copies TEST, `AUTOTEST()`
- **Webhook Discord** dans `ange_watch.ini` : regenerer si l URL a ete exposee
  (Discord -> Parametres serveur -> Integrations -> Webhooks -> regenerer)
