#!/usr/bin/env python3
"""
ange_analyse.py : analyses prédéfinies sur tdc_serveur.db (lecture seule).
Lancées depuis l'onglet « Requête » en écrivant :  ANALYSE <nom> [parametre=valeur ...]

  LIEN         pertes ANGE sans contrepartie ANGE <-> gains TAXE sans contrepartie TAXE (flux internes retirés des deux côtés)
  TAXE_CHAINE  reconstitue la chaîne de la TAXE : qui nourrit qui, points d'entrée de TDC de l'extérieur
  RAPPORT      tout en un seul résultat compact (LAG, résidus par jour et par joueur, attaquants, départs, oscillations)
  RESIDUS      pertes de TDC de joueurs ANGE (hors chasseurs) qu'AUCUN gain d'un autre joueur ANGE n'explique
               (égalité exacte ou somme de 2, plage 50-150 %).  vue=joueur (défaut) | jour | detail
  ATTAQUANTS   qui a pu prendre ce TDC : joueurs hors ANGE dans la plage dont le gain >= la perte, classés par répétition
               du même couple (victime, attaquant) ; compte aussi les montants exacts et les sommes de 2 attaquants
  LAG          décalage éventuel de l'API entre une perte ANGE et le gain correspondant
  DEPARTS      joueurs qui étaient ANGE et ne le sont plus
  OSCILLATIONS joueurs d'une alliance dont le TDC chute puis remonte vite (manoeuvre pour entrer dans la plage)
Paramètres : depuis=202610070000  seuil=1000  top=30  nmin=3  alliance=TAXE  (attaquants : alliance=TAXE pour filtrer)
Règle de plage : pour attaquer, le TDC de l'attaquant est entre 50 % et 150 % de celui de sa cible.
"""
import collections, os, re, sys, sqlite3, types
from datetime import datetime

PLAGE_MIN, PLAGE_MAX = 0.5, 1.5
MAX_LIGNES = 3000


def chasseurs_ini():
    defaut = "Cylo, Fannou, Fhezz, FourmiDiable, Judalos, Kalipzio, LaVague, Scooby-Doo, Shaka, Tatsuo, Zorka, mousse85, willo"
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from ange_watch import lire_config
        return {x.lower() for x in lire_config()["chasseurs"]}
    except Exception:
        return {x.strip().lower() for x in defaut.split(",")}


def dans_plage(prev_attaquant, prev_cible):
    return bool(prev_cible) and prev_cible > 0 and PLAGE_MIN <= prev_attaquant / prev_cible <= PLAGE_MAX


# Période exclue de toutes les analyses de TDC (décision d'Arno 11/11 : bascule vers la chaîne unique, le 31/10 heure de Paris ne compte pas).
# Les versions de la base sont en UTC : le 31/10 Paris = de 30/10 23:00 UTC (inclus) à 31/10 23:00 UTC (exclu).
PERIODES_EXCLUES = (("202610302300", "202610312300"),)


def jour_exclu(v0, v1=None):
    for a, b in PERIODES_EXCLUES:
        if a <= v0 < b or (v1 is not None and a <= v1 < b):
            return True
    return False


def charger(conn, depuis):
    data, versions = {}, []
    for v, p, a, t in conn.execute("SELECT version, pseudo, alliance, tdc FROM tdc WHERE version >= ? ORDER BY version", (depuis,)):
        if v not in data:
            data[v] = {}
            versions.append(v)
        data[v][p] = (sys.intern(a or ""), t)
    return versions, data


def apparier(L, G):
    """Retire les pertes expliquées par des gains DU MÊME GROUPE (ANGE ou TAXE) : égalité exacte, puis gain = somme de 2
    pertes, puis perte = somme de 2 gains. Plage 50-150 % (le gagnant est l'attaquant).
    Retourne (flux [(perdant, gagnant, montant)], pertes restantes, gains restants)."""
    L = sorted(L, key=lambda x: -x[1])
    usedL, usedG, flux = set(), set(), []
    parm = collections.defaultdict(list)
    for j, (q, g, qv) in enumerate(G):
        parm[g].append(j)
    for i, (p, m, pv) in enumerate(L):
        for j in parm.get(m, ()):
            if j not in usedG and G[j][0] != p and dans_plage(G[j][2], pv):
                usedL.add(i); usedG.add(j); flux.append((p, G[j][0], m))
                break
    # gain = somme de 2 pertes (recherche par dictionnaire : O(gains x pertes), pas O(gains x pertes^2))
    restL = [i for i in range(len(L)) if i not in usedL]
    parL = collections.defaultdict(list)
    for i in restL:
        parL[L[i][1]].append(i)
    for j, (q, g, qv) in enumerate(G):
        if j in usedG:
            continue
        fait = False
        for i1 in restL:
            if i1 in usedL or L[i1][1] >= g or not dans_plage(qv, L[i1][2]):
                continue
            for i2 in parL.get(g - L[i1][1], ()):
                if i2 != i1 and i2 not in usedL and dans_plage(qv, L[i2][2]):
                    usedL.update((i1, i2)); usedG.add(j); fait = True
                    flux += [(L[i1][0], q, L[i1][1]), (L[i2][0], q, L[i2][1])]
                    break
            if fait:
                break
    # perte = somme de 2 gains
    restG = [j for j in range(len(G)) if j not in usedG]
    parG = collections.defaultdict(list)
    for j in restG:
        parG[G[j][1]].append(j)
    for i, (p, m, pv) in enumerate(L):
        if i in usedL:
            continue
        fait = False
        for j1 in restG:
            if j1 in usedG or G[j1][1] >= m or not dans_plage(G[j1][2], pv):
                continue
            for j2 in parG.get(m - G[j1][1], ()):
                if j2 != j1 and j2 not in usedG and dans_plage(G[j2][2], pv):
                    usedL.add(i); usedG.update((j1, j2)); fait = True
                    flux += [(p, G[j1][0], G[j1][1]), (p, G[j2][0], G[j2][1])]
                    break
            if fait:
                break
    return flux, [L[i] for i in range(len(L)) if i not in usedL], [G[j] for j in range(len(G)) if j not in usedG]


def apparier_interne(L, G):
    return apparier(L, G)[1]


def preparer(versions, data, ch, seuil, cible="TAXE"):
    """Une seule passe : pour chaque heure, pertes/gains ANGE, pertes résiduelles, gagnants hors ANGE, départs."""
    etapes, alliance, etapes_t = [], {}, []
    bas = {}
    for v0, v1 in zip(versions, versions[1:]):
        if jour_exclu(v0, v1):
            continue
        A, B = data[v0], data[v1]
        L, G, GO, DEP, GO5 = [], [], [], [], collections.Counter()
        TL, TG = [], []
        for p, (al1, t1) in B.items():
            a0 = A.get(p)
            if a0 is None:
                continue
            k = bas.get(p)
            if k is None:
                k = bas[p] = p.lower() in ch
            if k:
                continue
            al0, t0 = a0
            d = t1 - t0
            if al0 == "ANGE" and al1 == "ANGE":
                if d <= -seuil:
                    L.append((p, -d, t0))
                elif d >= seuil:
                    G.append((p, d, t0))
            elif al0 == cible and al1 == cible:
                if d <= -seuil:
                    TL.append((p, -d, t0))
                elif d >= seuil:
                    TG.append((p, d, t0))
                if d >= seuil:
                    alliance[p] = al1
                    GO.append((p, al1, d, t0))
                    if d >= 5000:
                        GO5[d] += 1
            elif al0 == "ANGE":
                DEP.append((p, al1, t1))
            elif al1 != "ANGE":
                alliance[p] = al1
                if d >= seuil:
                    GO.append((p, al1, d, t0))
                    if d >= 5000:
                        GO5[d] += 1
        res = apparier_interne(L, G)
        etapes.append((v1, L, res, GO, DEP, GO5))
        etapes_t.append((v1,) + apparier(TL, TG))
    return etapes, alliance, etapes_t


def fmt(v):
    return datetime.strptime(v, "%Y%m%d%H%M").strftime("%d/%m %H:%M")


# ---------- analyses (travaillent sur les étapes préparées) ----------
def a_residus(etapes, vue, top):
    ev = [(v, p, m, pv) for v, L, res, GO, DEP, G5 in etapes for p, m, pv in res]
    if vue == "detail":
        return ["version", "joueur_ange", "perte_tdc_residuelle", "tdc_avant"], [[fmt(v), p, m, pv] for v, p, m, pv in ev][:top]
    if vue == "jour":
        j = collections.defaultdict(lambda: [0, 0, set(), 0])
        for v, L, res, GO, DEP, G5 in etapes:
            j[v[:8]][3] += sum(m for p, m, pv in L)
            for p, m, pv in res:
                j[v[:8]][0] += 1; j[v[:8]][1] += m; j[v[:8]][2].add(p)
        return (["jour", "pertes_ange_total", "dont_residuelles_tdc", "nb_pertes_residuelles", "joueurs_touches"],
                [[d, a[3], a[1], a[0], len(a[2])] for d, a in sorted(j.items())][:top])
    j = collections.defaultdict(lambda: [0, 0, ""])
    for v, p, m, pv in ev:
        j[p][0] += 1; j[p][1] += m; j[p][2] = v
    lignes = sorted(j.items(), key=lambda x: -x[1][1])
    return (["joueur_ange", "nb_heures", "total_tdc_residuel", "moyenne", "derniere"],
            [[p, a[0], a[1], a[1] // a[0], fmt(a[2])] for p, a in lignes][:top])


def a_attaquants(etapes, seuil, nmin, top, alliance_filtre=None, alliance_de=None):
    n_cand, n_exact, n_s2, tot = (collections.Counter() for _ in range(4))
    n_res, freq = collections.Counter(), collections.Counter()
    nb = max(len(etapes), 1)
    for v, L, res, GO, DEP, G5 in etapes:
        for q, al, d, t0 in GO:
            freq[q] += 1
        for p, m, pv in res:
            n_res[p] += 1
            cand = [(q, d, t0) for q, al, d, t0 in GO if dans_plage(t0, pv)]
            for q, d, t0 in cand:
                if d >= m:
                    n_cand[(p, q)] += 1; tot[(p, q)] += m
                    if d == m:
                        n_exact[(p, q)] += 1
            if m >= 5000:
                parm = collections.defaultdict(list)
                for q, d, t0 in cand:
                    if d < m:
                        parm[d].append(q)
                vus = set()
                for d1, qs in parm.items():
                    for q1 in qs:
                        for q2 in parm.get(m - d1, ()):
                            if q2 != q1:
                                vus.add(q1); vus.add(q2)
                for q in vus:
                    n_s2[(p, q)] += 1
    lignes = []
    for (p, q) in set(n_cand) | set(n_s2):
        al = (alliance_de or {}).get(q, "")
        if alliance_filtre and al != alliance_filtre:
            continue
        n, ne, n2 = n_cand[(p, q)], n_exact[(p, q)], n_s2[(p, q)]
        attendu = max(n_res[p] * (freq[q] / nb), 1e-9)
        lift = n / attendu
        if not (ne >= 2 or n2 >= 3 or (n >= nmin and lift >= 2)):
            continue
        lignes.append([p, q, al, n, ne, n2, n_res[p], round(lift, 1), tot[(p, q)]])
    lignes.sort(key=lambda r: (-(r[4] * 3 + r[5]), -r[7], -r[3]))
    return (["victime_ange", "attaquant_possible", "alliance", "nb_fois_possible", "nb_fois_montant_exact", "nb_fois_somme_de_2",
             "nb_pertes_victime", "lift_vs_hasard", "total_tdc"], lignes[:top])


def a_lag(etapes):
    pertes = [[m for p, m, pv in L if m >= 5000] for v, L, res, GO, DEP, G5 in etapes]
    gains = [G5 for v, L, res, GO, DEP, G5 in etapes]
    out = []
    for dec in (0, 1, 2, 3, 6, 24):
        out.append([dec, sum(1 for k, ms in enumerate(pertes) if k + dec < len(gains) for m in ms if gains[k + dec][m])])
    return ["decalage_en_versions (0 = meme heure)", "coincidences_exactes_perte_ANGE_gain_hors_ANGE (>=5000)"], out


def a_departs(etapes, top):
    lignes = [[fmt(v), p, al or "(aucune)", t] for v, L, res, GO, DEP, G5 in etapes for p, al, t in DEP]
    return ["version", "joueur", "nouvelle_alliance", "tdc"], lignes[:top]


def a_oscillations(versions, data, seuil, alliance, top):
    hist = collections.defaultdict(list)
    for v in versions:
        for p, (a, t) in data[v].items():
            if a == alliance:
                hist[p].append((v, t))
    lignes = []
    for p, h in hist.items():
        n = tot = 0
        for i in range(1, len(h) - 3):
            chute = h[i - 1][1] - h[i][1]
            if chute >= max(seuil * 5, 0.15 * h[i - 1][1]) and max(t for _, t in h[i + 1:i + 4]) - h[i][1] >= 0.8 * chute:
                n += 1; tot += chute
        if n:
            lignes.append([p, n, tot, tot // n, fmt(h[-1][0])])
    lignes.sort(key=lambda r: -r[1])
    return ["joueur", "nb_chutes_puis_remontees", "total_tdc_chute", "chute_moyenne", "derniere_vue"], lignes[:top]


def rapport(versions, data, etapes, alliance, seuil, nmin, top):
    """Toutes les analyses dans un seul tableau : une ligne de titre par section."""
    lignes = []

    def section(titre, cols, rows):
        lignes.append(["##", titre] + [""] * 8)
        lignes.append([""] + list(cols))
        for r in rows:
            lignes.append([""] + list(r))
    c, r = a_lag(etapes);            section("LAG API : coïncidences exactes selon le décalage", c, r)
    c, r = a_residus(etapes, "jour", 60); section("RESIDUS par jour : TDC perdu par les joueurs ANGE sans contrepartie ANGE", c, r[-14:])
    c, r = a_residus(etapes, "joueur", top); section("RESIDUS par joueur : qui perd le plus sans contrepartie ANGE", c, r)
    c, r = a_attaquants(etapes, seuil, nmin, top, None, alliance); section("ATTAQUANTS (toutes alliances) : couples qui se répètent", c, r)
    c, r = a_attaquants(etapes, seuil, nmin, top, "TAXE", alliance); section("ATTAQUANTS TAXE seulement", c, r)
    c, r = a_departs(etapes, top);   section("DEPARTS d'ANGE", c, r)
    c, r = a_oscillations(versions, data, seuil, "TAXE", top); section("OSCILLATIONS TAXE : chutes puis remontées", c, r)
    return ["section", "c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9"], lignes


def a_lien(etapes, etapes_t, top):
    """Pertes ANGE sans contrepartie ANGE  <->  gains TAXE sans contrepartie TAXE (les flux internes des deux camps sont retirés).
    Égalité exacte ou somme de 2 gains, plage 50-150 %. Comparaison avec des décalages sans lien (hasard)."""
    n = len(etapes)

    def balayage(decal, paires=None, paires2=None):
        c = c2 = 0
        for k in range(n):
            k2 = k + decal
            if not (0 <= k2 < n):
                continue
            pertes = etapes[k][2]
            gains = etapes_t[k2][3]
            if not pertes or not gains:
                continue
            par = collections.defaultdict(list)
            for q, d, t0 in gains:
                par[d].append((q, t0))
            for p, m, pv in pertes:
                for q, t0 in par.get(m, ()):
                    if dans_plage(t0, pv):
                        c += 1
                        if paires is not None:
                            paires[(p, q)][0] += 1; paires[(p, q)][1] += m; paires[(p, q)][2] = etapes[k][0]
                if m >= 5000:
                    vus = set()
                    for d1, lst in par.items():
                        if d1 >= m:
                            continue
                        for q1, t1 in lst:
                            if not dans_plage(t1, pv):
                                continue
                            for q2, t2 in par.get(m - d1, ()):
                                if q2 != q1 and dans_plage(t2, pv):
                                    vus.add((q1, q2) if q1 < q2 else (q2, q1))
                    for q1, q2 in vus:
                        c2 += 1
                        if paires2 is not None:
                            for q in (q1, q2):
                                paires2[(p, q)][0] += 1; paires2[(p, q)][1] += m; paires2[(p, q)][2] = etapes[k][0]
        return c, c2

    lignes = [["##", "Coincidences ANGE(perte sans contrepartie) / TAXE(gain sans contrepartie) : decalage 0 = vraie attaque, autres = hasard"] + [""] * 8,
              ["", "decalage_en_versions", "montants_exacts", "somme_de_2_gains"]]
    for dec in (0, 24, 48, -24, -48, 72):
        c, c2 = balayage(dec)
        lignes.append(["", dec, c, c2])
    p0 = collections.defaultdict(lambda: [0, 0, ""])
    p2 = collections.defaultdict(lambda: [0, 0, ""])
    balayage(0, p0, p2)
    for titre, pp in (("Couples ANGE -> TAXE a montant exact (decalage 0)", p0), ("Couples ANGE -> TAXE : perte = somme de 2 gains TAXE", p2)):
        lignes.append(["##", titre] + [""] * 8)
        lignes.append(["", "victime_ange", "attaquant_taxe", "nb_fois", "total_tdc", "derniere_version"])
        for (p, q), (nb, tot, v) in sorted(pp.items(), key=lambda x: (-x[1][0], -x[1][1]))[:top]:
            lignes.append(["", p, q, nb, tot, fmt(v)])
    return ["section", "c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9"], lignes


def a_taxe_chaine(etapes_t, top):
    """Reconstitue la chaîne de la TAXE : flux internes (qui nourrit qui) et points d'entrée de TDC venant de l'extérieur."""
    aretes, tot_a = collections.Counter(), collections.Counter()
    recu, envoye = collections.Counter(), collections.Counter()
    rg_tot, rg_n, rl_tot, rl_n = (collections.Counter() for _ in range(4))
    for v, flux, rl, rg in etapes_t:
        for p, q, m in flux:
            aretes[(p, q)] += 1; tot_a[(p, q)] += m; envoye[p] += m; recu[q] += m
        for q, d, t0 in rg:
            rg_tot[q] += d; rg_n[q] += 1
        for p, m, t0 in rl:
            rl_tot[p] += m; rl_n[p] += 1
    lignes = [["##", "TAXE : flux internes les plus frequents (perdant -> gagnant)"] + [""] * 8,
              ["", "perdant", "gagnant", "nb_fois", "total_tdc"]]
    for (p, q), nb in aretes.most_common(top):
        lignes.append(["", p, q, nb, tot_a[(p, q)]])
    lignes.append(["##", "TAXE : points d'entree de TDC (gains sans perte TAXE correspondante)"] + [""] * 8)
    lignes.append(["", "joueur", "gain_externe_total", "nb_heures", "recu_en_interne", "envoye_en_interne"])
    for q, t in rg_tot.most_common(top):
        lignes.append(["", q, t, rg_n[q], recu[q], envoye[q]])
    lignes.append(["##", "TAXE : joueurs qui perdent du TDC sans contrepartie TAXE (sorties vers l'exterieur)"] + [""] * 8)
    lignes.append(["", "joueur", "perte_externe_totale", "nb_heures", "recu_en_interne", "envoye_en_interne"])
    for p, t in rl_tot.most_common(top):
        lignes.append(["", p, t, rl_n[p], recu[p], envoye[p]])
    return ["section", "c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9"], lignes


def executer_analyse_lot(conn, commandes):
    """Exécute plusieurs commandes ANALYSE avec UNE SEULE lecture de la base (par jeu de paramètres identique)."""
    cache, res = {}, []
    ch = chasseurs_ini()
    for commande in commandes:
        try:
            mots = commande.split()
            if len(mots) < 2 or mots[0].upper() != "ANALYSE":
                raise ValueError("Format : ANALYSE <RAPPORT|RESIDUS|ATTAQUANTS|LAG|DEPARTS|OSCILLATIONS> [parametre=valeur]")
            nom = mots[1].upper()
            prm = dict(m.split("=", 1) for m in mots[2:] if "=" in m)
            depuis, seuil = prm.get("depuis", "202610070000"), int(prm.get("seuil", 1000))
            top, nmin = int(prm.get("top", 30)), int(prm.get("nmin", 3))
            cle = (depuis, seuil)
            if cle not in cache:
                versions, data = charger(conn, depuis)
                etapes, alliance, etapes_t = preparer(versions, data, ch, seuil)
                cache[cle] = (versions, data, etapes, alliance, etapes_t)
            versions, data, etapes, alliance, etapes_t = cache[cle]
            if nom == "RAPPORT":
                out = rapport(versions, data, etapes, alliance, seuil, nmin, int(prm.get("top", 20)))
            elif nom == "RESIDUS":
                out = a_residus(etapes, prm.get("vue", "joueur"), top)
            elif nom == "ATTAQUANTS":
                out = a_attaquants(etapes, seuil, nmin, top, prm.get("alliance"), alliance)
            elif nom == "LIEN":
                out = a_lien(etapes, etapes_t, top)
            elif nom == "TAXE_CHAINE":
                out = a_taxe_chaine(etapes_t, top)
            elif nom == "LAG":
                out = a_lag(etapes)
            elif nom == "DEPARTS":
                out = a_departs(etapes, top)
            elif nom == "OSCILLATIONS":
                out = a_oscillations(versions, data, seuil, prm.get("alliance", "TAXE"), top)
            elif nom == "NOTES":
                try:
                    import importlib, sys
                    mn = sys.modules.get("ange_note_activite")
                    mn = importlib.reload(mn) if mn else importlib.import_module("ange_note_activite")
                    date_j1 = prm.get("jour")
                    ecrire  = bool(int(prm.get("ecrire", 0)))
                    if date_j1:
                        notes = mn.calculer_et_stocker(None, date_j1, ecrire=ecrire)
                        cols = ["Joueur", "Poste", "Type", "Note J", "Placement", "Attaques",
                                "Malus", "Moy 7j", "H bonnes/total", "Nb attaques",
                                "Nb chasses", "TDC chasse", "Remarque"]
                        lignes_out = []
                        for n in sorted(notes, key=lambda x: (x.get("note_finale") or 10,
                                                               mn._rang_tri(x.get("poste", "")))):
                            hb = (f"{n.get('heures_bonnes','?')}/{n.get('heures_totales','?')}"
                                  if n.get("heures_totales") else "—")
                            lignes_out.append([
                                n["pseudo"], n.get("poste", ""), n.get("type", ""),
                                mn._note_str(n.get("note_finale")), mn._note_str(n.get("note_placement")),
                                mn._note_str(n.get("note_attaques")), mn._note_str(n.get("malus")),
                                mn._note_str(n.get("note_7j")), hb,
                                str(n.get("nb_attaques") if n.get("nb_attaques") is not None else "—"),
                                str(n.get("nb_chasses") if n.get("nb_chasses") is not None else "—"),
                                mn._fm(n.get("tdc_chasse")), n.get("remarque", "")
                            ])
                        prefix = ["[Stocke en DB]" if ecrire else "[Simulation — non stocke]"]
                        out = (cols, [prefix + [""] * (len(cols)-1)] + lignes_out, None)
                    else:
                        texte = mn.simuler(nb_jours=5)
                        out = (["Simulation notes activite (5 derniers jours)"],
                               [[l] for l in texte.split("\n")], None)
                except Exception as e_n:
                    raise ValueError(f"NOTES : {e_n}")
            else:
                fn = None
                try:
                    import importlib, ange_extra
                    importlib.reload(ange_extra)
                    fn = ange_extra.ANALYSES.get(nom)
                except ImportError:
                    pass
                if fn is None:
                    raise ValueError("Analyse inconnue : " + nom)
                ctx = types.SimpleNamespace(conn=conn, versions=versions, data=data, etapes=etapes, alliance=alliance, etapes_t=etapes_t,
                                            prm=prm, top=top, nmin=nmin, seuil=seuil, depuis=depuis, ch=ch, fmt=fmt,
                                            dans_plage=dans_plage, apparier=apparier, preparer=preparer)
                out = fn(ctx)
            res.append((out[0], out[1], None))
        except Exception as e:
            res.append(([], [], str(e)))
    return res


def verifier_sql(sql):
    t = sql.strip().rstrip(";").strip()
    if not re.match(r"^(select|with)\b", t, re.I):
        raise ValueError("Seulement SELECT (ou WITH ... SELECT), ou ANALYSE ...")
    if ";" in t:
        raise ValueError("Une seule requête à la fois (pas de « ; » au milieu).")
    return t


def executer_tout(conn, commandes):
    """Point d'entrée du service : SQL (lecture seule) ou ANALYSE. Retourne [(colonnes, lignes, tronque, erreur)]."""
    out = [None] * len(commandes)
    idx = [i for i, c in enumerate(commandes) if c.strip().upper().startswith("ANALYSE")]
    if idx:
        for i, (cols, lignes, err) in zip(idx, executer_analyse_lot(conn, [commandes[i].strip() for i in idx])):
            out[i] = (cols, [list(r) for r in lignes[:MAX_LIGNES]], len(lignes) > MAX_LIGNES, err)
    for i, c in enumerate(commandes):
        if out[i] is None:
            try:
                cur = conn.execute(verifier_sql(c))
                cols = [d[0] for d in cur.description]
                lignes = cur.fetchmany(MAX_LIGNES + 1)
                out[i] = (cols, [list(r) for r in lignes[:MAX_LIGNES]], len(lignes) > MAX_LIGNES, None)
            except Exception as e:
                out[i] = ([], [], False, str(e))
    return out


def AUTOTEST():
    """Lancé par le service avant d'installer une nouvelle version : si ça échoue, l'ancienne version est conservée."""
    import random
    c = sqlite3.connect(":memory:")
    c.execute("create table tdc(version,pseudo,alliance,tdc)")
    random.seed(1)
    st = {"A%d" % i: ("ANGE", 100000 + i * 1000) for i in range(1, 6)}
    st.update({"T%d" % i: ("TAXE", 100000 + i * 1000) for i in range(1, 5)})
    st.update({"O%d" % i: ("X", 100000) for i in range(1, 4)})
    rows = []
    for h in range(80):
        v = "2026100%d%02d00" % (7 + h // 24, h % 24)
        if h % 4 == 0 and h:
            st["A1"] = ("ANGE", st["A1"][1] - 4000); st["T1"] = ("TAXE", st["T1"][1] + 4000)
        if h % 3 == 0 and h:
            st["T2"] = ("TAXE", st["T2"][1] - 3000); st["T3"] = ("TAXE", st["T3"][1] + 3000)
        rows += [(v, p, al, t) for p, (al, t) in st.items()]
    c.executemany("insert into tdc values(?,?,?,?)", rows)
    cmds = ["ANALYSE RAPPORT depuis=202610000000 top=5", "ANALYSE LIEN depuis=202610000000", "ANALYSE TAXE_CHAINE depuis=202610000000",
            "ANALYSE RESIDUS vue=jour depuis=202610000000", "ANALYSE ATTAQUANTS nmin=2 depuis=202610000000", "SELECT count(*) AS n FROM tdc"]
    for cmd, (cols, lignes, tr, err) in zip(cmds, executer_tout(c, cmds)):
        assert err is None, f"{cmd} : {err}"
    assert executer_tout(c, ["delete from tdc"])[0][3], "le SQL d'écriture doit être refusé"
    try:   # les extensions éventuelles doivent aussi tenir
        import importlib, ange_extra
        importlib.reload(ange_extra)
        for nom in ange_extra.ANALYSES:
            err = executer_tout(c, ["ANALYSE " + nom + " depuis=202610000000"])[0][3]
            assert err is None, f"extension {nom} : {err}"
    except ImportError:
        pass
    return True


def executer_analyse(conn, commande):
    cols, lignes, err = executer_analyse_lot(conn, [commande])[0]
    if err:
        raise ValueError(err)
    return cols, lignes


if __name__ == "__main__":
    c = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tdc_serveur.db"))
    c.execute("PRAGMA query_only=ON")
    cols, lignes = executer_analyse(c, " ".join(["ANALYSE"] + sys.argv[1:]))
    print(cols)
    for l in lignes[:60]:
        print(l)
# version 4 : jour 31/10 exclu
