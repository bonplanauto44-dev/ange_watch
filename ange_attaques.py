"""ange_attaques.py : qui a attaqué qui (joueurs de la chaîne seulement), temps de trajet et heure d'envoi estimée.

Principe (règles d'Arno) : le TDC d'un joueur de la chaîne ne peut aller qu'à un autre joueur de la chaîne ; un chasseur n'attaque personne
(sa hausse = chasse, TDC gratuit). Donc, sur chaque heure, la hausse de chaque non-chasseur doit être exactement égale à la somme de ce qu'ont
perdu un ou plusieurs joueurs. Une attaque n'est possible que si :
  - la cible a un poste plus bas que l'attaquant (règle de la chaîne),
  - TDC cible entre 50 % et 300 % du TDC de l'attaquant (marge de 15 % car le TDC bouge pendant l'heure).
Temps de trajet (secondes) = 0,9^VA * 637200 * (1 - exp(-distance / 350)), distance = ligne droite entre les deux positions.
Une hausse vue entre deux versions horaires a touché pendant cette heure ; l'attaque est partie (heure d'impact - trajet).
Sortie : attaques identifiées avec leur niveau de certitude ; ce qui ne peut pas être identifié est listé, jamais deviné.
"""
import collections, itertools, math, re, statistics
from datetime import datetime, timedelta

MARGE = 0.15
FENETRE_ECART_CHASSE = 5.0   # heures minimum entre deux chasses d'un même chasseur
SEUIL_VICTIMES = 12          # au-delà, la recherche exhaustive est abandonnée (non résolu)
LIMITE_NOEUDS = 300000
VA_MIN_COHERENTE = 1


def rang(poste):
    p = (poste or "").lower().strip()
    m = re.match(r"(grenier|passeur)\s*(\d+)", p)
    if m:
        return int(m.group(2)) + (0 if m.group(1) == "grenier" else 10)
    if p.startswith("chasseur"):
        return 100
    return None


def temps_trajet(dist, va):
    return 0.9 ** va * 637200 * (1 - math.exp(-dist / 350.0))


def distance(a, b):
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def va_effective(infos, chasseurs, postes):
    """VA utilisée pour chaque joueur de la chaîne (non chasseur) : la sienne si elle est connue, sinon la médiane des VA connues des joueurs de TDP voisin."""
    connus = [(i["tdp"], i["va"]) for p, i in infos.items() if i.get("va", 0) >= VA_MIN_COHERENTE and i.get("tdp") and p not in chasseurs]
    res, estimees = {}, {}
    for p, poste in postes.items():
        if rang(poste) is None or p in chasseurs:
            continue
        i = infos.get(p, {})
        if i.get("va", 0) >= VA_MIN_COHERENTE:
            res[p] = i["va"]
        else:
            tdp = i.get("tdp")
            if connus:
                voisins = sorted(connus, key=lambda c: abs(c[0] - tdp) if tdp else 0)[:7]
                res[p] = statistics.median(v for _, v in voisins)
            else:
                res[p] = 10
            estimees[p] = res[p]
    return res, estimees


def _autorise(a, v, ta, tv, niveau, rg):
    if a == v:
        return False
    if not (0.5 * ta * (1 - MARGE) <= tv <= 3 * ta * (1 + MARGE)):
        return False
    ra, rv = rg.get(a), rg.get(v)
    if niveau == 0:
        return ra is not None and rv is not None and rv > ra
    if niveau == 1:
        return ra is not None and rv is not None and rv >= ra
    return True


def _sous_ensembles(victimes, cible, ok_fn, taille=3):
    """Sous-ensembles (jusqu'à `taille` victimes autorisées) dont les pertes font exactement `cible`."""
    cands = [(v, m) for v, m in victimes if m <= cible and ok_fn(v)]
    res = []
    if len(cands) > 14:
        taille = min(taille, 2)
    for k in range(1, taille + 1):
        for comb in itertools.combinations(cands, k):
            if sum(m for _, m in comb) == cible:
                res.append(tuple(v for v, _ in comb))
    return res


def _resoudre(gains, pertes, t0, rg, niveaux=(0, 1, 2)):
    """gains {att: montant}, pertes {vic: montant}.
    Cherche l'attribution qui explique EXACTEMENT le plus de TDC gagné : chaque attaquant a pris à une ou plusieurs victimes dont les pertes
    font exactement son gain. Retourne (attaques [(att, vic, montant)], certitude, niveau de règle, non_expliques [(att, gain)])."""
    if not gains:
        return [], "sûr", 0, []
    V = sorted(pertes.items(), key=lambda x: -x[1])
    meilleur = None
    for niveau in niveaux:
        G = sorted(gains.items(), key=lambda x: -x[1])
        cand = [_sous_ensembles(V, m, lambda v, a=a: _autorise(a, v, t0.get(a, 0), t0.get(v, 0), niveau, rg)) for a, m in G]
        sols, noeuds = [], [0]
        best = [-1]
        choix = [None] * len(G)
        reste_gain = [sum(m for _, m in G[i:]) for i in range(len(G) + 1)]

        def rec(i, utilises, expl):
            noeuds[0] += 1
            if noeuds[0] > LIMITE_NOEUDS or expl + reste_gain[i] < best[0]:
                return
            if i == len(G):
                if expl > best[0]:
                    best[0] = expl
                    sols.clear()
                if expl == best[0] and len(sols) <= 20:
                    sols.append(list(choix))
                return
            for sub in cand[i]:
                if not (set(sub) & utilises):
                    choix[i] = sub
                    rec(i + 1, utilises | set(sub), expl + G[i][1])
            choix[i] = None
            rec(i + 1, utilises, expl)
        rec(0, frozenset(), 0)
        if sols and (meilleur is None or best[0] > meilleur[0]):
            meilleur = (best[0], niveau, G, sols)
        if meilleur and meilleur[0] == sum(m for _, m in gains.items()):
            break
    if meilleur is None:
        return [], "sûr", 2, list(gains.items())
    _, niveau, G, sols = meilleur
    s = sols[0]
    att, non = [], []
    for i, sub in enumerate(s):
        if sub is None:
            non.append(G[i])
        else:
            for v in sub:
                att.append((G[i][0], v, pertes[v]))
    cert = "sûr" if len(sols) == 1 else f"ambigu ({len(sols) if len(sols) <= 20 else '20+'} possibilités)"
    return att, cert, niveau, non


def _flux(gains, exact, souples, t0, rg):
    """Les pertes des non-chasseurs sont EXACTES ; celles des chasseurs sont seulement bornées (leur chasse masque la perte : écart observé = chasse - perte),
    donc un chasseur peut absorber n'importe quelle part >= sa baisse observée.
    Retourne (f1, f2, arêtes) : f1 = TDC placé sur les victimes exactes, f2 = total placé (exactes + chasseurs), au meilleur niveau de règle."""
    best = None
    for niveau in (0, 1, 2):
        ar_e = {a: [v for v in exact if _autorise(a, v, t0.get(a, 0), t0.get(v, 0), niveau, rg)] for a in gains}
        # un chasseur chasse en continu : son TDC au moment de l'attaque peut être plus haut que celui du relevé (seule la borne haute compte)
        ar_s = {a: [h for h in souples if _autorise(a, h, t0.get(a, 0), max(t0.get(h, 0), 0.5 * t0.get(a, 0)), niveau, rg)] for a in gains}
        f1 = _maxflow(gains, exact, ar_e)
        merged = dict(exact)
        merged.update({h: 10 ** 12 for h in souples})
        ar = {a: ar_e[a] + ar_s[a] for a in gains}
        f2 = _maxflow(gains, merged, ar)
        cle = (f1, f2)
        if best is None or cle > best[:2]:
            best = (f1, f2, ar, niveau)
        if f1 == sum(exact.values()) and f2 == sum(gains.values()):
            break
    return best


def _maxflow(gains, pertes, ar):
    G, P = list(gains), list(pertes)
    cap = collections.defaultdict(int)
    adj = collections.defaultdict(set)

    def add(u, v, c):
        cap[(u, v)] += c
        adj[u].add(v)
        adj[v].add(u)
    for i, a in enumerate(G):
        add(0, 2 + i, gains[a])
        for v in ar[a]:
            add(2 + i, 2 + len(G) + P.index(v), 10 ** 12)
    for j, v in enumerate(P):
        add(2 + len(G) + j, 1, pertes[v])
    total = 0
    while True:
        prev = {0: None}
        q = collections.deque([0])
        while q and 1 not in prev:
            u = q.popleft()
            for w in adj[u]:
                if w not in prev and cap[(u, w)] > 0:
                    prev[w] = u
                    q.append(w)
        if 1 not in prev:
            return total
        f, w = 10 ** 18, 1
        while prev[w] is not None:
            f = min(f, cap[(prev[w], w)])
            w = prev[w]
        w = 1
        while prev[w] is not None:
            cap[(prev[w], w)] -= f
            cap[(w, prev[w])] += f
            w = prev[w]
        total += f


def analyser(versions, data, chasseurs, postes, infos, paris, exclu, perte_chasseurs=None):
    """versions/data comme ange_suivi.charger. postes {pseudo minuscule: poste}, infos {pseudo minuscule: {x,y,va,tdp}}.
    Retourne dict : att[p][jour], att_h[p][h], attaques[], non_resolus[], stats{}, va_est{}."""
    rg = {p: rang(po) for p, po in postes.items() if rang(po) is not None}
    va, va_est = va_effective(infos, chasseurs, postes)
    att = collections.defaultdict(lambda: collections.defaultdict(int))
    att_h = collections.defaultdict(lambda: [0] * 24)
    attaques, non_res, heures, anomalies, prises_ext = [], [], [], [], []
    st = collections.Counter()
    est_ch = perte_chasseurs or {}
    typ, rond_ch = _chasse_habituelle(est_ch)
    temps_vis = collections.defaultdict(list)       # chasseur -> heures des chasses déjà vues
    for (v, h), (c, perte) in est_ch.items():
        if c > 0:
            temps_vis[h].append(datetime.strptime(v, "%Y%m%d%H%M"))
    chasse_cachee = collections.defaultdict(dict)    # chasseur -> {version: chasse retrouvée}
    compta = []                                        # une ligne de comptabilité de chasse par heure
    for v0, v1 in zip(versions, versions[1:]):
        if exclu(v0) or exclu(v1):
            continue
        A, B = data[v0], data[v1]
        gains, pertes, t0, delta, chasse = {}, {}, {}, {}, {}
        ext = collections.defaultdict(list)       # montant -> joueurs HORS ANGE qui gagnent exactement ce montant
        for p, (al1, t1) in B.items():
            a0 = A.get(p)
            lp = p.lower()
            if a0 is not None and a0[0] != "ANGE" and al1 != "ANGE" and t1 - a0[1] >= 1000:
                ext[t1 - a0[1]].append((p, al1, a0[1]))
            if a0 is None or a0[0] != "ANGE" or al1 != "ANGE":
                continue
            d = t1 - a0[1]
            t0[lp] = a0[1]
            if d == 0:
                continue
            delta[lp] = (a0[1], t1, d)
            if rg and lp not in rg and d > 0:
                # pas dans « Chaîne unique » = hors chaîne : son gain compte comme celui d'un extérieur, jamais comme une attaque de la chaîne
                if d >= 1000:
                    ext[d].append((p, "ANGE hors chaîne", a0[1]))
                continue
            if lp in chasseurs:
                if d < 0:
                    pertes[lp] = -d          # un chasseur peut perdre du TDC
                else:
                    chasse[lp] = d           # hausse d'un chasseur = chasse (TDC gratuit), non analysée ici
                    pe = est_ch.get((v1, lp), (0, 0))[1]
                    if pe:
                        pertes[lp] = pe      # perte masquée par la chasse, estimée (chasse habituelle - hausse)
                continue
            (gains if d > 0 else pertes)[lp] = abs(d)
        st["intervalles"] += 1
        # pertes d'un membre qu'un seul joueur HORS ANGE gagne exactement (montant non rond = preuve forte) : retirées de l'attribution interne
        for vv, mm in list(pertes.items()):
            tv = t0.get(vv, 0)
            cand = [c for c in ext.get(mm, []) if tv and (1 / 3) * (1 - MARGE) <= c[2] / tv <= 2 * (1 + MARGE)]
            if cand:
                recupere = any(g == mm for g in gains.values())   # un joueur d'ANGE gagne exactement le même montant : c'est sans doute interne
                forte = len(cand) == 1 and not recupere
                prises_ext.append((v1, vv, mm, [c[0] for c in cand], [c[1] for c in cand], "sûre" if forte else ("possible (récupéré aussi par un membre d'ANGE)" if recupere else "possible (plusieurs coupables possibles)")))
                if forte:
                    del pertes[vv]
                    st["prises_ext_sures"] += 1
                else:
                    st["prises_ext_possibles"] += 1
        if not gains and not pertes:
            _comptabilite(compta, chasse_cachee, v1, delta, est_ch, [], 0, chasseurs, t0, typ, rond_ch, v1_prises=[p for p in prises_ext if p[0] == v1], temps=temps_vis)
            continue
        r_att, cert_all, niveau_all, non = _resoudre(gains, pertes, t0, rg)
        utilises0 = {v for _, v, _ in r_att}
        pertes_rest = {p: m for p, m in pertes.items() if p not in utilises0}
        gains_rest = dict(non)
        exact = {p: m for p, m in pertes_rest.items() if p not in chasseurs}
        souples = {h: pertes_rest.get(h, 0) for h in t0 if h in chasseurs and h not in utilises0}
        cat, ar = "rien à expliquer", {}
        if gains_rest or exact or any(souples.values()):
            f1, f2, ar, _niv = _flux(gains_rest, exact, souples, t0, rg) if gains_rest else (0, 0, {}, 2)
            sg, se, slb = sum(gains_rest.values()), sum(exact.values()), sum(souples.values())
            if f1 == se and f2 == sg and f2 - f1 >= slb:
                cat = ("équilibré (pertes partagées entre plusieurs attaquants)" if sg == se else
                       "surplus de gains : attaque(s) sur chasseur(s), perte masquée par leur chasse")
            elif f1 < se:
                cat = "pertes de non-chasseurs non couvertes (TDC perdu sans attaquant possible : hors ANGE ? règles ?)"
            elif f2 < sg:
                cat = "gains sans victime possible selon les règles de poste / ratio"
            else:
                cat = "perte de chasseur non couverte"
        st["heures_" + cat.split(" ")[0]] += 1
        _comptabilite(compta, chasse_cachee, v1, delta, est_ch, non, gains_rest, chasseurs, t0, typ, rond_ch, v1_prises=[p for p in prises_ext if p[0] == v1], temps=temps_vis)
        cand_chass = list(souples)
        if cat.startswith("pertes") or cat.startswith("perte de chasseur"):
            ecart = (sum(exact.values()) - f1) if cat.startswith("pertes") else 0
            for vv, mm in sorted(exact.items(), key=lambda x: -x[1]):
                possibles = [a for a in gains_rest if vv in ar.get(a, [])]
                seul = _maxflow(gains_rest, {vv: mm}, {a: [vv] if vv in ar.get(a, []) else [] for a in gains_rest}) if gains_rest else 0
                if cat.startswith("pertes") and (not possibles or seul < mm):
                    anomalies.append((v1, vv, mm, "aucun attaquant ANGE possible" if not possibles else "gains ANGE insuffisants", possibles))
                    st["pertes_certaines"] += 1
            for hh, mm in souples.items():
                if mm and cat.startswith("perte de chasseur"):
                    anomalies.append((v1, hh, mm, "baisse de chasseur sans attaquant ANGE", []))
            st["pertes_ecart_total"] += ecart
        for a, m in non:
            allowed = list(ar.get(a, []))
            cands = [c for c in allowed if c not in chasseurs]
            hunters_ok = [c for c in allowed if c in chasseurs]
            non_res.append((v1, a, m, cat, cands, hunters_ok))
            st["non_resolu"] += 1
            trs = []
            ia = infos.get(a)
            for c in cands + hunters_ok:
                ic = infos.get(c)
                if ia and ic and "x" in ia and "x" in ic:
                    trs.append(temps_trajet(distance(ia, ic), va.get(a, 10)))
            tr = (sum(trs) / len(trs)) if trs else None
            _compter(att, att_h, a, v0, tr or 0, paris, aff_tr=tr)
            if tr is None:
                st["sans_distance"] += 1
        res = r_att
        certitude = {x[:2]: cert_all for x in r_att}
        r = (r_att, cert_all, niveau_all, non)
        utilises = {v for _, v, _ in r_att}
        sans_att = [(p, m) for p, m in pertes.items() if p not in utilises]
        heures.append({"v1": v1, "gains": sum(gains.values()), "pertes": sum(pertes.values()), "chasse": sum(chasse.values()),
                       "expl": sum(gains.values()) - sum(m for _, m in non), "non": list(non), "sans_att": sans_att,
                       "lignes": _lignes_heure(v1, delta, r_att, non, sans_att, postes, chasseurs)})
        for a, vic, m in res:
            cert = certitude.get((a, vic), "sûr")
            niv_regle = r[2]
            tr = None
            ia, iv = infos.get(a), infos.get(vic)
            d = None
            if ia and iv and all(k in ia and k in iv for k in ("x", "y")):
                d = distance(ia, iv)
                tr = temps_trajet(d, va.get(a, 10))
            attaques.append({"v0": v0, "v1": v1, "att": a, "vic": vic, "montant": m, "dist": d, "trajet": tr, "cert": cert,
                             "va_est": a in va_est, "regle": ("plus bas" if niv_regle == 0 else ("même rang accepté" if niv_regle == 1 else "règle de poste NON respectée"))})
            st["sur" if cert == "sûr" else "ambigu"] += 1
            if tr is None:
                st["sans_distance"] += 1
            _compter(att, att_h, a, v0, tr or 0, paris, aff_tr=tr)
    _tests_decalage(heures, st)
    return {"att": att, "att_h": att_h, "attaques": attaques, "non_resolus": non_res, "stats": dict(st), "va_est": va_est, "heures": heures, "anomalies": anomalies, "prises_ext": prises_ext, "chasse_cachee": dict(chasse_cachee), "compta": compta}


def _chasse_habituelle(est):
    """Par chasseur : taille habituelle d'une chasse (médiane des hausses propres) et habitude de chasses rondes."""
    propres = collections.defaultdict(list)
    for (v, h), (c, perte) in est.items():
        if perte == 0 and c > 0:
            propres[h].append(c)
    typ, rond = {}, {}
    for h, l in propres.items():
        if len(l) >= 3:
            typ[h] = statistics.median(l)
            rond[h] = sum(1 for x in l if x % 1000 == 0) >= 0.7 * len(l)
    return typ, rond


def _comptabilite(compta, cachee, v1, delta, est, non, gains_rest, chasseurs, t0, typ, rond, v1_prises=(), temps=None):
    """Net horaire d'ANGE = chasse de l'heure (+ entrées - sorties par l'extérieur).
    1) chasse déjà vue : hausses des chasseurs corrigées de leurs pertes ;
    2) le reste est expliqué par des gains RONDS sans victime de non-chasseurs (passeurs qui chassent) quand leur somme tombe juste ;
    3) puis par des chasses cachées derrière une baisse de chasseur (taille habituelle) ;
    4) ce qui reste, positif ou négatif (négatif = TDC sorti vers l'extérieur ?), est inexpliqué."""
    T = sum(d for (_a, _b, d) in delta.values())
    vis = {h: est[(v1, h)][0] for h in chasseurs if (v1, h) in est}
    vis_tot = sum(vis.values())
    ronds_all = [(a, m) for a, m in non if m % 1000 == 0 and a not in chasseurs]
    informatif = [(a, m) for a, m in non if m % 1000 != 0 and a not in chasseurs]
    reste0 = T - vis_tot
    def _proche(h):
        """un chasseur ne lance pas deux chasses à moins de ~5 h d'écart (min. observé 5,6 h sur FourmiDiable) : on écarte ceux qui chassent déjà autour de cette heure."""
        if not temps:
            return False
        t = datetime.strptime(v1, "%Y%m%d%H%M")
        pris = list(temps.get(h, [])) + [datetime.strptime(w, "%Y%m%d%H%M") for w in cachee.get(h, {})]
        return any(abs((t - u).total_seconds()) < FENETRE_ECART_CHASSE * 3600 for u in pris)
    cands = [h for h in typ if h in t0 and h not in vis and h in chasseurs]
    proches = {h for h in cands if _proche(h)}   # chasse déjà vue à moins de 5 h : possible (plusieurs expéditions) mais moins probable
    choisis_ronds, ajout = [], {}
    if reste0 > 500:
        best = None
        n = len(ronds_all)
        for k in range(0, min(n, 5) + 1):
            for S in itertools.combinations(ronds_all, k):
                rem = reste0 - sum(m for _, m in S)
                if rem < -200:
                    continue
                if abs(rem) <= 200:
                    sc, hid = (0.0 - 0.001 * k), ()
                else:
                    hid, tot, sc = (), 0, None
                    for kk in (1, 2, 3):
                        bh = None
                        for sub in itertools.combinations(cands, kk):
                            tt = sum(typ[h] for h in sub)
                            tol = 0.1 * tt + (1000 if any(rond.get(h) for h in sub) else 0)
                            if abs(tt - rem) <= tol:
                                bonus = sum(0.04 for h in sub if h in delta and delta[h][2] < -0.3 * typ[h])
                                s2 = abs(tt - rem) / max(rem, 1) - bonus + 0.1 * sum(1 for h in sub if h in proches)
                                if bh is None or s2 < bh[0]:
                                    bh = (s2, sub, tt)
                        if bh:
                            sc, hid, tot = bh[0] + 0.05, bh[1], bh[2]
                            break
                    if sc is None:
                        continue
                if best is None or sc < best[0]:
                    best = (sc, S, hid, rem)
        if best:
            choisis_ronds = list(best[1])
            hid, rem = best[2], best[3]
            if hid:
                tot = sum(typ[h] for h in hid)
                for h in hid:
                    val = rem * typ[h] / tot
                    ajout[h] = int(round(val / 1000.0) * 1000) if rond.get(h) else int(round(val))
                    cachee[h][v1] = ajout[h]
    reste = reste0 - sum(m for _, m in choisis_ronds) - sum(ajout.values())
    compta.append({"v1": v1, "net": T, "vis": vis, "ronds": choisis_ronds, "ronds_ecartes": [x for x in ronds_all if x not in choisis_ronds],
                   "non_ronds": informatif, "cachee": ajout, "reste": reste})


def feuille_chasse(res, hp, fm, nb=72):
    """Comptabilité de la chasse heure par heure : net d'ANGE = chasse de l'heure ; ce qui est vu, ce qui est retrouvé, ce qui reste."""
    L = [["🏠 Accueil"],
         ["Net horaire d'ANGE = chasse de l'heure (si rien n'entre ni ne sort par l'extérieur). Chasse vue = hausses des chasseurs corrigées de leurs pertes. "
          "Chasse de non-chasseur = gain rond sans victime d'un passeur dont la somme tombe exactement sur le net. Chasse cachée = chasse retrouvée derrière une baisse de chasseur, d'après la taille habituelle de ses chasses. Reste négatif = du TDC est sorti d'ANGE (à vérifier dans « Pertes à élucider »). Reste positif = chasse non retrouvée."],
         [], ["Heure (Paris)", "Net ANGE", "Chasse vue", "Chasse de non-chasseurs", "Chasse cachée retrouvée", "Reste inexpliqué", "Détail"]]
    for c in sorted(res["compta"], key=lambda x: x["v1"], reverse=True)[:nb]:
        det = []
        if c["vis"]:
            det.append("vue : " + ", ".join(f"{h} {fm(m)}" for h, m in sorted(c["vis"].items())))
        if c["ronds"]:
            det.append("chasse de non-chasseur : " + ", ".join(f"{a} {fm(m)}" for a, m in c["ronds"]))
        if c["ronds_ecartes"]:
            det.append("gains ronds écartés (attaques) : " + ", ".join(f"{a} {fm(m)}" for a, m in c["ronds_ecartes"]))
        if c["cachee"]:
            det.append("cachée : " + ", ".join(f"{h} {fm(m)}" for h, m in sorted(c["cachee"].items())))
        L.append([hp(c["v1"]), c["net"], sum(c["vis"].values()), sum(m for _, m in c["ronds"]), sum(c["cachee"].values()), c["reste"], " | ".join(det)])
    return L


def _lignes_heure(v1, delta, r_att, non, sans_att, postes, chasseurs):
    """Une ligne par joueur ANGE dont le TDC a changé : avant, après, écart et rôle expliqué."""
    pris = collections.defaultdict(list)       # attaquant -> [(victime, montant)]
    subis = collections.defaultdict(list)      # victime -> [(attaquant, montant)]
    for a, v, m in r_att:
        pris[a].append((v, m))
        subis[v].append((a, m))
    non_g = dict(non)
    non_p = dict(sans_att)
    out = []
    for p, (t0, t1, d) in sorted(delta.items(), key=lambda x: -x[1][2]):
        if p in chasseurs and d > 0:
            role = "chasse"
        elif p in pris:
            role = "a pris à " + ", ".join(f"{v} ({m})" for v, m in pris[p])
        elif p in subis:
            role = "pris par " + ", ".join(f"{a} ({m})" for a, m in subis[p])
        elif p in non_g:
            role = "GAIN SANS VICTIME"
        elif p in non_p:
            role = "PERTE SANS ATTAQUANT"
        else:
            role = "?"
        if p in pris and p in subis:
            role += " · et pris par " + ", ".join(f"{a} ({m})" for a, m in subis[p])
        out.append((p, postes.get(p, "hors chaîne / inconnu"), t0, t1, d, role))
    return out


def _tests_decalage(heures, st):
    """Pour chaque gain sans victime : existe-t-il une perte sans attaquant EXACTEMENT égale (a) dans la même heure mais refusée par les règles,
    (b) 1 à 3 heures avant ou après (décalage de mise à jour) ?"""
    for i, h in enumerate(heures):
        for p, m in h["non"]:
            if any(l == m for _, l in h["sans_att"]):
                st["gain_perte_egale_meme_heure"] += 1
            else:
                for j in (i - 1, i + 1, i - 2, i + 2, i - 3, i + 3):
                    if 0 <= j < len(heures) and any(l == m for _, l in heures[j]["sans_att"]):
                        st["gain_perte_egale_autre_heure"] += 1
                        break
            if m % 1000 == 0:
                st["gains_ronds_sans_victime"] += 1


def _compter(att, att_h, a, v0, trajet, paris, aff_tr=None):
    """Impact au milieu de l'heure (v0 + 30 min), envoi = impact - trajet (en secondes)."""
    imp = paris(v0) + timedelta(minutes=30)
    envoi = imp - timedelta(seconds=trajet if aff_tr is not None else 0)
    att[a][envoi.strftime("%Y-%m-%d")] += 1
    att_h[a][envoi.hour] += 1


def hms(s):
    s = int(round(s))
    return f"{s // 3600}h{(s % 3600) // 60:02d}m{s % 60:02d}"


def feuille(res, postes, infos, chasseurs, paris, fm, hp):
    """Lignes de l'onglet « Attaques détail »."""
    st = res["stats"]
    n = st.get("sur", 0) + st.get("ambigu", 0)
    tot = n + st.get("non_resolu", 0)
    L = [["Qui a attaqué qui, joueurs de la chaîne seulement. Heure d'envoi estimée = heure d'impact - temps de trajet (0,9^VA × 637 200 × (1 - e^(-distance/350)))."],
         [f"Hausses expliquées : {n} sur {tot} ({round(100 * n / tot) if tot else 0} %) · sûres (une seule combinaison possible) : {st.get('sur', 0)} · ambiguës : {st.get('ambigu', 0)} · non identifiées : {st.get('non_resolu', 0)} · sans distance (position manquante) : {st.get('sans_distance', 0)}"],
         []]
    L += [["VA MANQUANTE OU À 0 : valeur estimée (médiane des VA de TDP voisin), à faire corriger par le joueur dans le formulaire"],
          ["Joueur", "Poste", "TDP", "VA utilisée"]]
    for p, v in sorted(res["va_est"].items(), key=lambda x: postes.get(x[0], "")):
        L.append([p, postes.get(p, ""), infos.get(p, {}).get("tdp", ""), v])
    sans_pos = [p for p, po in postes.items() if rang(po) is not None and p not in chasseurs and not ("x" in infos.get(p, {}) and "y" in infos.get(p, {}))]
    L += [[], ["POSITION MANQUANTE (distance impossible à calculer)"], ["Joueur", "Poste"]] + [[p, postes[p]] for p in sorted(sans_pos)]
    L += [[], ["DIAGNOSTIC DES HAUSSES SANS VICTIME"],
          [f"Gains sans victime : {st.get('non_resolu', 0)} · dont montants ronds (multiples de 1000) : {st.get('gains_ronds_sans_victime', 0)} · "
           f"avec une perte égale la même heure mais refusée par les règles (poste / ratio) : {st.get('gain_perte_egale_meme_heure', 0)} · "
           f"avec une perte égale 1 à 3 h avant ou après (décalage de relevé) : {st.get('gain_perte_egale_autre_heure', 0)}"],
          [f"Heures où tout s'équilibre exactement (pertes partagées entre plusieurs attaquants) : {st.get('heures_équilibré', 0)} · "
           f"surplus de gains = attaques sur chasseurs (perte masquée par la chasse) : {st.get('heures_surplus', 0)} · "
           f"pertes de non-chasseurs non couvertes : {st.get('heures_pertes', 0)} · gains sans victime possible : {st.get('heures_gains', 0)} · perte de chasseur non couverte : {st.get('heures_perte', 0)}"],
          [], ["Joueurs avec des gains sans victime (14 jours)"], ["Joueur", "Poste", "Nb", "TDC total", "dont montants ronds"]]
    par = collections.defaultdict(lambda: [0, 0, 0])
    for v1, a, m, why, _c, _h in res["non_resolus"]:
        e = par[a]
        e[0] += 1
        e[1] += m
        e[2] += (m % 1000 == 0)
    for a, e in sorted(par.items(), key=lambda x: -x[1][0])[:40]:
        L.append([a, postes.get(a, "hors chaîne / inconnu"), e[0], e[1], e[2]])
    L += [[], ["HAUSSES NON IDENTIFIÉES (les 60 dernières)"], ["Heure (Paris)", "Joueur", "TDC gagné", "Situation de l'heure", "Victimes possibles"]]
    for v1, a, m, why, cands, hunters_ok in sorted(res["non_resolus"], key=lambda x: x[0], reverse=True)[:60]:
        L.append([hp(v1), a, m, why, ", ".join(cands) if cands else (f"{len(hunters_ok)} chasseur(s) possibles" if hunters_ok else "")])
    L += [[], ["ATTAQUES IDENTIFIÉES (les 400 dernières)"],
          ["Impact (Paris)", "Attaquant", "Victime", "TDC pris", "Distance", "Trajet", "Envoi estimé (Paris)", "Certitude", "Règle de poste", "VA estimée ?"]]
    for x in sorted(res["attaques"], key=lambda x: x["v1"], reverse=True)[:400]:
        imp = paris(x["v0"]) + timedelta(minutes=30)
        env = (imp - timedelta(seconds=x["trajet"])).strftime("%d/%m %Hh%M") if x["trajet"] is not None else "?"
        L.append([hp(x["v1"]), x["att"], x["vic"], x["montant"], round(x["dist"], 1) if x["dist"] is not None else "?",
                  hms(x["trajet"]) if x["trajet"] is not None else "?", env, x["cert"], x["regle"], "oui" if x["va_est"] else ""])
    return L


def feuille_heures(res, hp, nb=24):
    """Récap heure par heure (dernières `nb` heures) : bilan, puis chaque joueur dont le TDC a changé."""
    L = [["Heure par heure : pour chaque relevé, ce que chaque joueur ANGE a gagné ou perdu et ce que l'attribution en comprend. "
          "Règle : TDC gagné = TDC perdu, à l'unité près. GAIN SANS VICTIME / PERTE SANS ATTAQUANT = ce qui reste à expliquer."], []]
    for h in sorted(res["heures"], key=lambda x: x["v1"], reverse=True)[:nb]:
        L.append([f"{hp(h['v1'])} · gagné par les non-chasseurs : {h['gains']} · perdu par les membres : {h['pertes']} · chasse : {h['chasse']} · "
                  f"expliqué : {h['expl']} · gains sans victime : {len(h['non'])} ({sum(m for _, m in h['non'])}) · pertes sans attaquant : {len(h['sans_att'])} ({sum(m for _, m in h['sans_att'])})"])
        L.append(["Joueur", "Poste", "TDC avant", "TDC après", "Écart", "Explication"])
        for p, poste, t0, t1, d, role in h["lignes"]:
            L.append([p, poste, t0, t1, d, role])
        L.append([])
    return L


def feuille_anomalies(res, postes, chasseurs, hp, fm):
    """Joueurs dont une perte de TDC n'a AUCUN attaquant ANGE possible : à élucider (fuite vers l'extérieur, attaque hors règles...)."""
    an = res["anomalies"]
    st = res["stats"]
    L = [["Pertes de TDC qu'aucune attaque d'un membre ANGE ne peut expliquer (règles de poste et ratio comprises). À élucider une par une."],
         [f"{len(an)} pertes sur {st.get('heures_pertes', 0)} heures concernées. Dans les autres heures concernées, plusieurs victimes se partagent le manque et on ne sait pas laquelle est en cause."],
         [], ["PAR JOUEUR (14 jours)"], ["Joueur", "Poste", "Nb pertes", "TDC perdu", "Cause", "Dernière (Paris)"]]
    par = collections.defaultdict(lambda: [0, 0, set(), ""])
    for v1, p, m, why, poss in an:
        e = par[p]
        e[0] += 1
        e[1] += m
        e[2].add(why)
        e[3] = max(e[3], v1)
    for p, e in sorted(par.items(), key=lambda x: (-x[1][0], -x[1][1])):
        L.append([p, postes.get(p, "hors chaîne / inconnu") + (" (chasseur)" if p in chasseurs else ""), e[0], e[1], " ; ".join(sorted(e[2])), hp(e[3])])
    pe = res["prises_ext"]
    L += [[], [f"PRISES PAR L'EXTÉRIEUR : perte d'un membre égale, au point près, au gain d'un joueur hors ANGE la même heure ({st.get('prises_ext_sures', 0)} sûres, {st.get('prises_ext_possibles', 0)} possibles)"],
          ["Heure (Paris)", "Victime ANGE", "TDC perdu", "Pris par (hors ANGE)", "Alliance", "Certitude"]]
    for v1, vic, m, noms, als, cert in sorted(pe, key=lambda x: x[0], reverse=True)[:120]:
        L.append([hp(v1), vic, m, ", ".join(noms), ", ".join(a or "(aucune)" for a in als), cert])
    L += [[], ["DÉTAIL des pertes sans attaquant ANGE possible (les 80 dernières)"], ["Heure (Paris)", "Joueur", "TDC perdu", "Cause", "Attaquants ANGE possibles"]]
    for v1, p, m, why, poss in sorted(an, key=lambda x: x[0], reverse=True)[:80]:
        L.append([hp(v1), p, m, why, ", ".join(poss)])
    return L


def AUTOTEST():
    _c, _k = [], collections.defaultdict(dict)
    _delta = {"a": (100000, 100000 - 20000, -20000), "b": (100000, 100000 + 26000, 26000)}
    _comptabilite(_c, _k, "202611010100", _delta, {("202611010100", "b"): (26000, 0)}, [], {}, {"a", "b", "c"}, {"a": 1, "b": 1, "c": 1},
                  {"a": 26000, "b": 26000, "c": 26000}, {"a": False, "b": False, "c": False})
    assert _c[0]["net"] == 6000 and not _k, _c
    _c, _k = [], collections.defaultdict(dict)
    _delta = {"a": (100000, 80000, -20000), "b": (100000, 126000, 26000), "g": (50000, 96000, 46000)}
    _comptabilite(_c, _k, "202611010100", _delta, {("202611010100", "b"): (26000, 0)}, [], {}, {"a", "b", "c"}, {"a": 1, "b": 1, "c": 1},
                  {"a": 26000, "b": 26000, "c": 26000}, {"a": False, "b": False, "c": False})
    assert _k["a"]["202611010100"] in range(25000, 27000), (_c, dict(_k))
    # trajets de référence donnés par Arno (VA 10, position 39/44)
    me = {"x": 39, "y": 44}
    for x, y, att in ((21, 24, 16441), (24, 34, 11154), (89, 42, 29599), (21, 60, 14774)):
        assert round(temps_trajet(distance(me, {"x": x, "y": y}), 10)) == att
    rg = {"a": 1, "b": 2, "c": 3, "d": 3}
    t0 = {"a": 1000, "b": 900, "c": 800, "d": 700}
    r = _resoudre({"a": 150}, {"c": 100, "d": 50}, t0, rg)
    assert r and sorted(r[0]) == [("a", "c", 100), ("a", "d", 50)] and r[1] == "sûr" and not r[3], r
    r = _resoudre({"a": 100, "b": 100}, {"c": 100, "d": 100}, t0, rg)
    assert r and r[1].startswith("ambigu"), r
    r = _resoudre({"a": 10}, {"c": 11}, t0, rg)
    assert r[0] == [] and r[3] == [("a", 10)], r
    r = _resoudre({"a": 100, "b": 77}, {"c": 100, "d": 50}, t0, rg)       # b inexpliqué (attaque hors ANGE), a sûr
    assert r[0] == [("a", "c", 100)] and r[3] == [("b", 77)], r
    assert rang("Grenier 2") < rang("Passeur 1") < rang("Chasseur") and rang("?") is None
    f1, f2, ar, _n = _flux({'x': 100, 'y': 50}, {'u': 80, 'v': 70}, {}, {'x': 1000, 'y': 1000, 'u': 900, 'v': 900}, {})
    assert (f1, f2) == (150, 150), (f1, f2)      # une victime partagée entre deux attaquants
    g = {'jimmy': 53982, 'hermes': 15337, 'thething': 9163, 'dob': 1500}
    ex = {'zourite': 3200, 'bomric': 9778, 'wyrock': 14811, 'misterambr': 20000, 'blackeagle': 24730}
    f1, f2, ar, _n = _flux(g, ex, {'zorka': 5963}, {'jimmy': 95421, 'hermes': 113871, 'thething': 67293, 'dob': 38000, 'zourite': 39855, 'zorka': 22625, 'bomric': 48890, 'wyrock': 74056, 'misterambr': 110357, 'blackeagle': 86081}, {'jimmy': 3, 'hermes': 5, 'thething': 7, 'zourite': 8, 'bomric': 8, 'wyrock': 5, 'misterambr': 4, 'blackeagle': 7, 'zorka': 100})
    assert f1 == 72519 and f2 == 79982, (f1, f2)      # cas réel 18h06 : le reste va à un chasseur
    # chaîne complète synthétique : a (Grenier 1) prend 100 à c (Passeur 1) entre v0 et v1
    from datetime import datetime
    vs = ["202611010000", "202611010100"]
    data = {vs[0]: {"A": ("ANGE", 1000), "C": ("ANGE", 800), "H": ("ANGE", 50)}, vs[1]: {"A": ("ANGE", 1100), "C": ("ANGE", 700), "H": ("ANGE", 80)}}
    postes = {"a": "Grenier 1", "c": "Passeur 1", "h": "Chasseur"}
    infos = {"a": {"x": 39, "y": 44, "va": 10, "tdp": 45}, "c": {"x": 21, "y": 24, "va": 10, "tdp": 44}}
    out = analyser(vs, data, {"h"}, postes, infos, lambda v: datetime.strptime(v, "%Y%m%d%H%M"), lambda v: False)
    assert len(out["attaques"]) == 1 and out["attaques"][0]["att"] == "a" and out["attaques"][0]["vic"] == "c", out
    assert round(out["attaques"][0]["trajet"]) == 16441
    assert out["att_h"]["a"][(datetime(2026, 11, 1, 0, 30) - timedelta(seconds=16441)).hour] == 1
    feuille_heures(out, lambda v: v)
    feuille_anomalies(out, postes, {'h'}, lambda v: v, lambda n: n)
    feuille(out, postes, infos, {"h"}, lambda v: datetime.strptime(v, "%Y%m%d%H%M"), lambda n: str(n), lambda v: v)


if __name__ == "__main__":
    AUTOTEST()
    print("ok")
