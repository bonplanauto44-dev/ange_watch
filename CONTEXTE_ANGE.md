# Contexte ANGE / Fourmizzz — à lire en premier (Claude du VPS)

Dernière mise à jour : 13/11/2026. Propriétaire : Arno (pseudo en jeu **Octo**), français, tutoiement, réponses COURTES en français simple, pas de tableaux, il travaille souvent depuis son téléphone.

## 1. Le jeu (Fourmizzz, serveur s5)
- On joue des fourmilières. **TDC** = terrain de chasse (la mesure de puissance). On gagne du TDC en attaquant un autre joueur (le TDC pris est gagné en totalité par l'attaquant, perdu à l'unité près par la cible, impact simultané) ou en **chassant** (attaque vers « personne » : TDC gratuit, la seule création de TDC côté ANGE).
- **Règle d'attaque** : on peut attaquer une cible dont le TDC est entre **50 % et 300 %** du sien. Dans la chaîne on n'attaque que les joueurs de poste plus bas ; **les chasseurs n'attaquent personne dans l'alliance** (ils chassent).
- Statistiques d'un joueur : **TDP** (technologie de ponte), **O** (ouvrières), **VA** (vitesse d'attaque), **CdF** (combien de fourmis il envoie pour savoir ce qu'une attaque peut prendre), **FdF** (force de frappe), étable à pucerons. 1 O porte 13 bois ; il faut 1 O par unité de TDC pour occuper le terrain. Temps de ponte d'une O = 1 min × 0,9^TDP.
- **Distance = ligne droite (euclidienne)** entre les positions (x, y) : `sqrt(dx² + dy²)`. PAS |dx|+|dy| (erreur corrigée le 13/11).
- **Temps de trajet d'une attaque** (secondes) : `0,9^VA × 637200 × (1 − exp(−distance/350))`. Exemple vérifié par Arno : VA 10, distance 76 → environ 11 h 56. Ne pas utiliser « 26 min/case », c'est faux.
- Colonisation : un chasseur colonise un grenier ; le jeu prend environ 29 % de la récolte du colonisé (le sheet compte 25 % par défaut, colonne « taux colo réel »).

## 2. La chaîne unique ANGE
- Depuis le 31/10/2026 une **chaîne unique** (~79 joueurs). Trois types de postes : **Grenier 1…5** (reçoivent les convois de bois, tiennent le plus gros TDC), **Passeur 1…8**, **Chasseur**. Le but : chacun peut attaquer celui du dessous avec une marge de TDC, et le bois (convois) descend de façon régulière.
- Seuls les greniers et passeurs hauts livrent ; les chasseurs ont une dette. **Convois = bois uniquement**, calculés **une fois par soir à 22h** sur la **moyenne du TDC de J-1 (fenêtre 22h→22h)** ; aucune modification des convois en cours de journée (sauf joueur sorti ou changement de poste : seulement sur sa page).
- Le plan est un flot à coût minimal : coût = distance³ (exposant 3, Arno refuse 4), meilleure répartition globale, `limiter_partenaires` 12. Deux joueurs du même foyer (« Interdits ») ne se livrent jamais.
- **Hors chaîne** = case Y cochée dans « Chaîne unique » (ou ligne barrée) : visible nulle part dans les convois, pas d'onglet (sauf liste « Attaque seulement » : on l'attaque / il attaque mais pas de convois). Seule la liste « Chaîne unique » du sheet privé compte (l'onglet « Hors chaîne » est ignoré). « Cible à descendre » = joueur qui ne joue plus, affiché comme cible à ceux qui l'ont à portée.
  - **Mécanisme d'exclusion (côté sheet)** : formule colonne AF = `=IF(OR($C61="";$Y61=TRUE);"";…)` → renvoie vide quand Y est coché. Le code Python saute le joueur (`d is None`). Colonnes Z et AD (récoltes) = vides ; AA et AE (flux colonisation) = 0 ; flux du joueur colonisé par un hors-chaîne = 0 via COUNTIFS sur Y. → aucun delta, aucun convoi, aucun flux.
  - **Dettes** : restent dans l'onglet "Dettes" (non effacées), mais **gelées** — le plan ne les rembourse pas tant que le joueur est hors chaîne (absent de `joueurs`). Reprennent automatiquement au retour en chaîne.
- **Statut Nouveau (depuis le 13/11)** : tout joueur qui ENTRE dans la chaîne a le statut Nouveau : il est visible partout (onglet, listes, formulaire) mais **exclu du calcul des convois** jusqu'au « dernier plan sans convoi » (jour d'entrée + 1). Onglet **Nouveaux** du sheet privé (Pseudo, Poste, Entré le, Pas de convoi jusqu'au plan du) ; supprimer une ligne le fait entrer au calcul suivant. Onglet **Mouvements** = journal complet (entrées, changements de poste, sorties, hors chaîne/retour). Module `ange_convois_arrivants.py`. **Différence clé avec hors chaîne** : le statut Nouveau est un filtre Python (`exclus_nouveaux` dans `charger_prive`/`lire_base`) ; le hors chaîne est un filtre sheet (formule colonne AF). Même résultat (`d is None` → exclu de `joueurs`), mécanismes distincts.
- Pour placer un nouveau : la **distance (position x,y)** et l'absence de trous dans les postes comptent d'abord, puis O, puis TDP ; le TDC bouge trop vite.

## 3. Les fichiers Google (ID)
- **Sheet privé « Chaîne de TDC - Base de données joueurs »** : `1WSkmn_I44Ugn7Koq9bKqzP9KH1ucBF4-xsrZj7q35tQ` — **ne jamais le supprimer ni le casser**. Onglets clés : `Chaîne unique` (ligne 2 = en-têtes, A groupe « 2.1 » stocké en nombre, B poste, C pseudo, Y = hors chaîne, formules Z..AH = récolte/flux/delta, AC = TDC moyen d'hier, AF = delta), `Data` (formulaires : pseudo, X, Y, TDC, rang, O, TDP, armée, VA, poste, dernière MAJ, CdF, FdF…), `TDC live`, `TDC moyenne`, `Dettes`, `Base convois`, `Historique…`, `Attaque seulement`, `Ne pas attaquer`, `Cible à descendre`, `Interdits`, `Nouveaux`, `Mouvements`, `Mode` (ID des 3 formulaires en B8:B10), `Contrôle convois`.
- **3 fichiers publics** (un onglet par joueur actif, visibles de l'alliance) : Greniers `17NWquA5VyOl2NkYz_HTgQaI5te-BVGxv1dtoeiVbHi0`, Passeurs `11KkWMvxkIZH2mpePNOgdr8I7ZPeXbUjI4Q6zwgy7JMk`, Chasseurs `1ZfAzwGrxLFfW9klIh5O4QgnTurTk3af52hccqKMxHiQ`. Les onglets joueurs : A9:I20 = livraisons à faire (nom, dû, tout envoyé, envois 1-4, retard…), A25:B36 = réceptions, listes d'attaque, onglet `Modèle` caché pour créer un joueur.
- **Sheet « Suivi ANGE »** (boîte aux lettres des commandes, onglet `Requête`) : `1Px_nI00WarXo_MtyzG7rsHMnsSLRyge1GNrXhhWY5yY`. Cellules d'état : E1 code, E2 suivi, E3/E4 clôture de nuit, E5 santé, E6 veille. Pour lancer une commande : colonne A = `ANALYSE CONVOIS action=… clé=valeur` ou une requête SQL `select …`, colonne B = `OUI` ; le démon répond `FAIT hh:mm · N lignes → R<n>` et écrit le résultat dans l'onglet `R<n>`. Actions utiles : `plan_prod_sim pseudo=X` (plan de prod calculé SANS écrire, test du statut Nouveau), `veille_blanc`, `coherence cible=prod`, `pages_ecrire cible=prod`, `prod_etat`.
- **Dossier Drive de code** `1SLK0pfNK2pDN_RRRTsQZiPvWjY4-XWOb` (privé, « ange_code ») — ⚠️ plus utilisé pour déployer des scripts. L'agent IA écrit directement dans `/home/ubuntu/ange_watch/` ; Drive peut rester comme archive mais n'est plus la source de déploiement.

## 4. Le VPS (OVH, Ubuntu) — `/home/ubuntu/ange_watch`
- Service systemd **`ange-requete`** (`ange_requete.py --daemon`) : boucle chaque minute. Il (1) **détecte les `ange_*.py` modifiés** (md5 changé sur disque — l'agent IA écrit directement dans `/home/ubuntu/ange_watch/`, Drive n'est plus dans la boucle), **compile + lance `AUTOTEST()`**, n'active que si ça passe ; (2) lance `ange_suivi.tick` (suivi horaire : base SQLite `tdc_serveur.db`, une photo par version horaire de l'API Fourmizzz, depuis le 01/10 ; l'UTC pour `version = AAAAMMJJHHmm`) ; (3) traite les commandes de l'onglet Requête ; (4) tick convois (`ange_convois_tick.tick`).
- Modules convois : `ange_convois.py` (cœur : lecture du privé `charger_prive`, commande/actions, quotas, `avec_reprise` pour les 429), `ange_convois_plan.py` (calcul du plan, pur), `ange_convois_nuit.py` (logique de clôture), `ange_convois_ecr.py` (moyenne J-1 + plan + Base/Dettes/Historique + complétion automatique des formules des nouveaux), `ange_convois_prod.py` (clôture de minuit en PRODUCTION, armée via `convois_prod_arme.json`, écrit les 3 fichiers publics), `ange_convois_live.py` + `ange_convois_tick.py` (veille : changement de membre/poste appliqué en direct sur les onglets), `ange_convois_pages.py` / `entetes` / `annexes` / `onglets` / `chasse` (en-têtes, listes d'attaque, distances, ordre, bloc chasse), `ange_convois_controle.py` (contrôle horaire « à envoyer = à recevoir » + alerte Discord des livraisons anormales), `ange_convois_forms.py` (liste déroulante Pseudo des formulaires), `ange_convois_reponses.py` (réponses des formulaires), `ange_convois_tdclive.py`, `ange_convois_alertes.py`, `ange_convois_arrivants.py`. Suivi : `ange_suivi.py`, `ange_alliances.py` (TAXE, AAA, TYR…), `ange_attaques.py` (qui a attaqué qui), `ange_analyse.py`.
- Alertes : Discord via `ange_convois_prod.alerter`. Quota Google Sheets : limites par MINUTE (pas de solde) ; un 429 se règle par `avec_reprise`.
- Plus aucun Apps Script : tout est sur le VPS (décision du 12/11). Ne **jamais** exécuter `installerDeclencheursChaines`.

## 5. Règles de travail (strictes)
1. **Aucune installation / redémarrage / écriture de prod entre 21h30 et 23h00** (clôture de 22h). Elle écrit tout à minuit pour le jour suivant (`jour du plan` = demain à partir de midi).
2. **Ne rien supprimer définitivement** (corbeille / `.bak` seulement). Toujours sauvegarder avant de modifier un fichier.
3. Tester avant d'activer (`AUTOTEST`), simulation d'abord (`ecrire=non`), puis prod.
4. Ne jamais poster de secrets ; `service_account.json` et fichiers `.json` d'état ne se touchent pas.
5. Réponses à Arno : courtes, en français, sans tableaux ; dire ce qui est fait, pas le détail de l'effort.

## 6. Historique récent

- **13/11** : Morbifouette ajouté (Passeur 5, statut Nouveau), suivi mouvements installé, distance euclidienne dans tous les modules.
- **16/11** : Redistribution dettes djuman (Grenier 5 hors chaîne) — `ange_convois_redistrib.py` + `appliquer_redistrib_nuit.py`.
  - Mécanique : les Greniers ont delta >= 0 → le plan ignore les dettes classiques. On écrit **directement dans les onglets publics** au lieu du sheet Dettes.
  - Jour 1 : 28 lignes ajoutées (2 par Grenier × 14) — 28/28 tests OK.
  - Jours 2-7 : cron **50 23 * * * UTC** (00h50 Paris) lance `appliquer_redistrib_nuit.py` après le plan de minuit.
  - Suivi Telegram 8h00 : `checkDettesRedistrib()` lit `dettes_redistrib.json` (restant décroissant, tests d'intégrité).
  - `dettes_redistrib.json` : `actif`, `jour_actuel`, `restant_actuel` (décroissance linéaire), `dernier_test {ok,total,erreurs,date}`, `historique_jours`.

## 7. Limite connue à surveiller

- Certaines plages sont codées `A3:AH96` / `$C$97` ; si la chaîne dépasse ~94 lignes il faudra les agrandir.

## 8. Système de malus convois (préparé, non activé)

- **Fichiers** : `malus.json` (config modifiable direct) + `ange_convois_malus.py` (module)
- **Principe** : réduire temporairement la part de bois d'un joueur pdt X jours.
  - Delta < 0 (chasseur/passeur) : reçoit `montant_par_jour` de moins chaque jour.
  - Delta > 0 (grenier) : livre `montant_par_jour` de moins chaque jour.
- **Activation** : décommenter `MALUS_HOOK` et `MALUS_TICK` dans `ange_convois_ecr.py` (2 lignes chacun).
- **CLI** : `python3 ange_convois_malus.py action=ajouter pseudo=X montant=N jours=N [raison=texte]`
           `python3 ange_convois_malus.py action=lister / supprimer / tick / resume`
- **AUTOTEST** : passe ✅ — validé par le démon avant activation.

