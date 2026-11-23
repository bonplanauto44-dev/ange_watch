# Contexte Fourmizzz — a lire en premier

Derniere mise a jour : 23/11/2026.

## 1. Le jeu (Fourmizzz, serveur S5)

- On joue des fourmilieres. **TDC** = terrain de chasse (la mesure de puissance). On gagne du TDC en attaquant un autre joueur (le TDC pris est gagne en totalite par l'attaquant, perdu a l'unite pres par la cible) ou en **chassant** (attaque vers "personne" : TDC gratuit, la seule creation de TDC).
- **Regle d'attaque** : on peut attaquer une cible dont le TDC est entre **50% et 300%** du sien. Dans la chaine on n'attaque que les joueurs de poste plus bas ; **les chasseurs n'attaquent personne dans l'alliance** (ils chassent).
- Statistiques d'un joueur : **TDP** (technologie de ponte), **O** (ouvrieres), **VA** (vitesse d'attaque), **CdF** (force de frappe de detection), **FdF** (force de frappe). 1 O porte 13 bois ; il faut 1 O par unite de TDC pour occuper le terrain. Temps de ponte d'une O = 1 min x 0,9^TDP.
- **Distance = ligne droite (euclidienne)** entre les positions (x, y) : sqrt(dx^2 + dy^2). PAS |dx|+|dy| (erreur corrigee le 13/11).
- **Temps de trajet d'une attaque** (secondes) : 0,9^VA x 637200 x (1 - exp(-distance/350)). Exemple verifie : VA 10, distance 76 -> environ 11h56. Ne pas utiliser "26 min/case", c'est faux.
- Colonisation : un chasseur colonise un grenier ; le jeu prend environ 29% de la recolte du colonise (le sheet compte 25% par defaut, colonne "taux colo reel").

## 2. La chaine unique

- Depuis le 31/10/2026 une **chaine unique** (~79 joueurs). Trois types de postes : **Grenier 1...5** (recoivent les convois de bois, tiennent le plus gros TDC), **Passeur 1...8**, **Chasseur**. Le but : chacun peut attaquer celui du dessous avec une marge de TDC, et le bois (convois) descend de facon reguliere.
- Seuls les greniers et passeurs hauts livrent ; les chasseurs ont une dette. **Convois = bois uniquement**, calcules **une fois par soir a 22h** sur la **moyenne du TDC de J-1 (fenetre 22h->22h)** ; aucune modification en cours de journee (sauf joueur sorti ou changement de poste : seulement sur sa page).
- Le plan est un flot a cout minimal : cout = distance^3 (exposant 3), meilleure repartition globale, `limiter_partenaires` 12. Deux joueurs du meme foyer ("Interdits") ne se livrent jamais.
- **Hors chaine** = case Y cochee dans "Chaine unique" (ou ligne barree) : visible nulle part dans les convois, pas d'onglet (sauf liste "Attaque seulement"). Seule la liste "Chaine unique" du sheet prive compte.
  - **Mecanisme d'exclusion (cote sheet)** : formule colonne AF = `=IF(OR($C61="";$Y61=TRUE);"";...)` -> renvoie vide quand Y est coche. Le code Python saute le joueur (`d is None`). Colonnes Z et AD (recoltes) = vides ; AA et AE (flux colonisation) = 0 -> aucun delta, aucun convoi.
  - **Dettes** : restent dans l'onglet "Dettes" (non effacees), mais **gelees** tant que le joueur est hors chaine. Reprennent au retour.
- **Statut Nouveau** : tout joueur qui ENTRE dans la chaine est exclu du calcul des convois jusqu'au lendemain. Onglet **Nouveaux** du sheet prive (Pseudo, Poste, Entre le, Pas de convoi jusqu'au plan du) ; supprimer une ligne le fait entrer au calcul suivant.
  - **Difference avec hors chaine** : le statut Nouveau est un filtre Python (`exclus_nouveaux`) ; le hors chaine est un filtre sheet (formule colonne AF). Meme resultat (`d is None`), mecanismes distincts.
- Pour placer un nouveau : la **distance (position x,y)** et l'absence de trous dans les postes comptent d'abord, puis O, puis TDP.

## 3. Les fichiers Google (IDs a mettre dans ange_watch.ini)

- **Sheet prive "Chaine de TDC - Base de donnees joueurs"** : `<ID_SHEET_PRIVE>` — **ne jamais le supprimer ni le casser**. Onglets cles : `Chaine unique` (A=groupe, B=poste, C=pseudo, Y=hors-chaine, AC=TDC moy J-1, AF=delta), `Data`, `TDC live`, `TDC moyenne`, `Dettes`, `Base convois`, `Historique`, `Attaque seulement`, `Ne pas attaquer`, `Cible a descendre`, `Interdits`, `Nouveaux`, `Mouvements`, `Mode`, `Controle convois`.
- **3 fichiers publics** (un onglet par joueur actif) :
  - Greniers : `<ID_SHEET_GRENIERS>`
  - Passeurs : `<ID_SHEET_PASSEURS>`
  - Chasseurs : `<ID_SHEET_CHASSEURS>`
  - Onglets joueurs : A9:I20 = livraisons a faire, A25:B36 = receptions.
- **Sheet Suivi** (boite aux lettres des commandes, onglet Requete) : `<ID_SHEET_SUIVI>`. Cellules d'etat : E1=code, E2=suivi, E3/E4=cloture nuit, E5=sante, E6=veille.
- **Dossier Drive de code** : plus utilise pour deployer. L'agent IA ecrit directement dans `~/ange_watch/` ; Drive peut rester comme archive.

## 4. Le VPS — ~/ange_watch/

- Service systemd **`ange-requete`** (`ange_requete.py --daemon`) : boucle chaque minute. Il (1) detecte les `ange_*.py` modifies (md5), compile + lance `AUTOTEST()`, n'active que si ca passe ; (2) lance `ange_suivi.tick` (suivi horaire, SQLite `tdc_serveur.db`) ; (3) traite les commandes de l'onglet Requete ; (4) tick convois.
- Modules convois : `ange_convois.py` (coeur), `ange_convois_plan.py` (calcul pur), `ange_convois_nuit.py` (logique cloture), `ange_convois_ecr.py` (ecriture plan), `ange_convois_prod.py` (production minuit), `ange_convois_live.py` + `ange_convois_tick.py` (veille), `ange_convois_arrivants.py` (Nouveau/Mouvements), `ange_convois_redistrib.py` (dettes grenier hors chaine).
- Plus aucun Apps Script : tout est sur le VPS. Ne **jamais** executer `installerDeclencheursChaines`.

## 5. Regles de travail (strictes)

1. **Aucune installation / redemarrage / ecriture de prod entre 21h30 et 23h00** (cloture de 22h).
2. **Ne rien supprimer definitivement** (corbeille / `.bak` seulement).
3. Tester avant d'activer (`AUTOTEST`, `ecrire=non`), puis prod.
4. Ne jamais poster de secrets ; `service_account.json` et fichiers `.json` d'etat ne se touchent pas.

## 6. Historique recent

- **13/11** : joueur X ajoute (Passeur 5, statut Nouveau), suivi mouvements installe, distance euclidienne dans tous les modules.
- **16/11** : redistribution dettes joueur Y (Grenier 5 hors chaine) — `ange_convois_redistrib.py` + `appliquer_redistrib_nuit.py`.
  - Mecanique : les Greniers ont delta >= 0 -> le plan ignore les dettes classiques. On ecrit **directement dans les onglets publics** au lieu du sheet Dettes.
  - Jours 2-7 : cron 23h50 UTC lance `appliquer_redistrib_nuit.py` apres le plan de minuit.

## 7. Limite connue a surveiller

- Certaines plages sont codees `A3:AH96` / `$C$97` ; si la chaine depasse ~94 lignes il faudra les agrandir.

## 8. Systeme de malus convois (prepare, non active)

- **Fichiers** : `malus.json` (config modifiable direct) + `ange_convois_malus.py`
- **Principe** : reduire temporairement la part de bois d'un joueur pendant X jours.
  - Delta < 0 (chasseur/passeur) : recoit `montant_par_jour` de moins chaque jour.
  - Delta > 0 (grenier) : livre `montant_par_jour` de moins chaque jour.
- **Activation** : decommenter `MALUS_HOOK` et `MALUS_TICK` dans `ange_convois_ecr.py` (2 lignes).
- **CLI** : `python3 ange_convois_malus.py action=ajouter pseudo=X montant=N jours=N [raison=texte]`
## 9. Corrections et incidents notables

- **23/11/2026 — Bug fusion P7+P8** : suppression de "Passeur 8" de N.POSTES avait rendu 9 destinataires (Bomric, Wiwi, Tontonlaitue, etc.) invisibles au code → lignes de livraison retirées des onglets greniers et mises en parking. Corrections :
  - "Passeur 8" remis dans POSTES (`ange_convois_nuit.py`) — la fusion doit être gérée dans la feuille
  - `signature()` utilise désormais `m["g"]` (groupe fichier) plutôt que le poste exact (`ange_convois_tick.py`) — un renommage de poste dans le même fichier ne déclenche plus de purge
  - Restauration des 9 lignes depuis convois_parking_prod.json (breto→Bomric coché ✓, Octo→Tontonlaitue 3 envois ✓, etc.)
