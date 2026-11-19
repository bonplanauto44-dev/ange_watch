"""ange_extra.py : analyses ajoutées sans toucher à ange_analyse.py (chargé à chaque demande). Format : ANALYSES[NOM] = fonction(ctx) -> (colonnes, lignes)."""
import collections


def _gains_tous(ctx):
    """Pour chaque heure k (version k -> k+1) : tous les gains >= seuil des joueurs hors ANGE (non chasseurs), + chasseurs ANGE."""
    out = []
    for v0, v1 in zip(ctx.versions, ctx.versions[1:]):
        if '202610302300' <= v0 < '202610312300' or '202610302300' <= v1 < '202610312300':   # 31/10 Paris exclu (bascule chaîne unique), versions en UTC
            continue
        A, B = ctx.data[v0], ctx.data[v1]
        g = []
        for p, (al1, t1) in B.items():
            a0 = A.get(p)
            if a0 is None:
                continue
            al0, t0 = a0
            d = t1 - t0
            if d < ctx.seuil:
                continue
            chasseur = p.lower() in ctx.ch
            if al0 == "ANGE" and al1 == "ANGE" and not chasseur:
                continue                      # gains internes déjà traités par les étapes
            label = "CHASSEURS(ANGE)" if chasseur else (al1 or "(aucune)")
            g.append((p, label, d, t0))
        out.append(g)
    return out


def alliance_lien(ctx):
    """Pour chaque alliance de destination : combien de pertes ANGE sans contrepartie (RESIDUS) correspondent EXACTEMENT à un gain d'un de
    ses joueurs (plage 50-150 %), a decalage 0, comparé au hasard (décalages +-24/48/72 h sans lien). 'exces' = vrais - hasard."""
    G = _gains_tous(ctx)
    n = len(ctx.etapes)
    seuil_min = int(ctx.prm.get("min", 3000))

    def compte(dec):
        c = collections.Counter()
        for k in range(n):
            k2 = k + dec
            if not (0 <= k2 < n):
                continue
            pertes = [(p, m, pv) for p, m, pv in ctx.etapes[k][2] if m >= seuil_min]
            if not pertes:
                continue
            par = collections.defaultdict(list)
            for q, lab, d, t0 in G[k2]:
                par[d].append((q, lab, t0))
            for p, m, pv in pertes:
                labs = set()
                for q, lab, t0 in par.get(m, ()):
                    if ctx.dans_plage(t0, pv):
                        labs.add(lab)
                for lab in labs:
                    c[lab] += 1
        return c

    vrai = compte(0)
    bases = [compte(d) for d in (24, 48, 72, -24, -48, -72)]
    labs = set(vrai)
    for b in bases:
        labs |= set(b)
    npertes = sum(1 for k in range(n) for p, m, pv in ctx.etapes[k][2] if m >= seuil_min)
    lignes = []
    for lab in labs:
        moy = sum(b[lab] for b in bases) / len(bases)
        lignes.append([lab, vrai[lab], round(moy, 1), round(vrai[lab] - moy, 1), round(vrai[lab] / moy, 2) if moy else ""])
    lignes.sort(key=lambda r: -r[3])
    lignes.insert(0, ["(total pertes ANGE sans contrepartie >= %d)" % seuil_min, npertes, "", "", ""])
    return ["alliance_destinataire", "coincidences_decalage_0", "hasard_moyen", "exces", "rapport"], lignes[: ctx.top * 3]


def _pearson(x, y):
    n = len(x)
    if n < 3:
        return 0.0
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return 0.0
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sxx * syy) ** 0.5


def correl(ctx):
    """Correlation horaire entre les pertes ANGE sans contrepartie (serie) et le TDC gagne chaque heure par chaque alliance (TAXE : flux internes retires).
    Une vraie fuite ANGE -> alliance X donne un pic de correlation a decalage 0, absent aux decalages eloignes (24/48/72/96 h = hasard).
    Colonne z = (corr0 - moyenne des decalages eloignes) / ecart-type."""
    n = len(ctx.etapes)
    perte = [sum(m for p, m, pv in ctx.etapes[k][2]) for k in range(n)]
    G = _gains_tous(ctx)
    series = collections.defaultdict(lambda: [0.0] * n)
    for k in range(n):
        for q, lab, d, t0 in G[k]:
            if lab == "TAXE":
                continue
            series[lab][k] += d
        for q, d, t0 in ctx.etapes_t[k][3]:          # TAXE : gains sans contrepartie TAXE
            series["TAXE (flux internes retires)"][k] += d
    lignes = []
    for lab, ser in series.items():
        if sum(1 for v in ser if v > 0) < 50:
            continue
        def c(dec):
            if dec >= 0:
                return _pearson(perte[: n - dec] if dec else perte, ser[dec:])
            return _pearson(perte[-dec:], ser[: n + dec])
        c0 = c(0)
        loin = [c(d) for d in (24, 48, 72, 96, -24, -48, -72, -96)]
        m = sum(loin) / len(loin)
        sd = (sum((v - m) ** 2 for v in loin) / len(loin)) ** 0.5
        z = (c0 - m) / sd if sd > 1e-9 else 0.0
        lignes.append([lab, round(c0, 3), round(m, 3), round(sd, 3), round(z, 1), round(c(1), 3), round(c(-1), 3), round(sum(ser))])
    lignes.sort(key=lambda r: -r[4])
    return ["alliance", "corr_decalage_0", "corr_hasard_moyen", "ecart_type_hasard", "z", "corr_+1h", "corr_-1h", "tdc_gagne_total"], lignes[: ctx.top * 3]


ANALYSES = {"ALLIANCE_LIEN": alliance_lien, "CORREL": correl}


def suspects(ctx):
    """Pour chaque joueur HORS ANGE : nombre de fois ou il gagne, dans la MEME heure, au moins le montant d'une perte ANGE sans contrepartie
    (plage 50-150 %, gain entre 1x et 3x la perte), compare au meme compte avec des decalages sans lien (+-24/48/72/96 h).
    Un 'passeur' qui recoit le TDC d'ANGE (puis le redistribue, meme via plusieurs alliances) ressort avec un exces et un z eleve.
    exacts = nombre de fois ou le gain est EXACTEMENT egal a la perte."""
    import bisect
    n = len(ctx.etapes)
    G = _gains_tous(ctx)
    alliance_de = {}
    for k in range(n):
        for q, lab, d, t0 in G[k]:
            alliance_de[q] = lab
    tri = []
    for k in range(n):
        g = sorted(((d, q, t0) for q, lab, d, t0 in G[k] if lab != "CHASSEURS(ANGE)"))
        tri.append(([x[0] for x in g], g))
    mini = int(ctx.prm.get("min", 3000))
    lags = [0, 24, 48, 72, 96, -24, -48, -72, -96]

    def compte(dec):
        c, ex = collections.Counter(), collections.Counter()
        for k in range(n):
            k2 = k + dec
            if not (0 <= k2 < n):
                continue
            ds, g = tri[k2]
            if not ds:
                continue
            for p, m, pv in ctx.etapes[k][2]:
                if m < mini:
                    continue
                i = bisect.bisect_left(ds, m)
                j = bisect.bisect_right(ds, 3 * m)
                vus = set()
                for d, q, t0 in g[i:j]:
                    if q not in vus and ctx.dans_plage(t0, pv):
                        vus.add(q)
                        c[q] += 1
                        if d == m:
                            ex[q] += 1
        return c, ex
    res = [compte(d) for d in lags]
    vrai, vex = res[0]
    lignes = []
    for q in vrai:
        loin = [r[0][q] for r in res[1:]]
        m_ = sum(loin) / len(loin)
        sd = (sum((v - m_) ** 2 for v in loin) / len(loin)) ** 0.5
        z = (vrai[q] - m_) / max(sd, 0.7)
        exloin = sum(r[1][q] for r in res[1:]) / len(res[1:])
        if vrai[q] >= 8:
            lignes.append([q, alliance_de.get(q, ""), vrai[q], round(m_, 1), round(vrai[q] - m_, 1), round(z, 1), vex[q], round(exloin, 1)])
    lignes.sort(key=lambda r: -(r[5] + 2 * (r[6] - r[7])))
    return ["joueur", "alliance", "fois_au_decalage_0", "hasard_moyen", "exces", "z", "montants_exacts", "exacts_hasard"], lignes[: ctx.top * 2]


ANALYSES["SUSPECTS"] = suspects


def detail(ctx):
    """Preuves ligne par ligne pour des joueurs precis : joueurs=a,b,c  [tol=0.02]
    Liste chaque heure ou le joueur (hors ANGE) a gagne un montant egal (ou a tol pres, au-dessus) a une perte ANGE sans contrepartie,
    plage 50-150 % respectee. Colonne 'exact' = oui si montant identique."""
    noms = {x.strip().lower() for x in ctx.prm.get("joueurs", "").split(",") if x.strip()}
    if not noms:
        return ["info"], [["Preciser joueurs=pseudo1,pseudo2"]]
    tol = float(ctx.prm.get("tol", 0.02))
    mini = int(ctx.prm.get("min", 1000))
    G = _gains_tous(ctx)
    lignes = []
    for k in range(len(ctx.etapes)):
        v = ctx.etapes[k][0]
        for q, lab, d, t0 in G[k]:
            if q.lower() not in noms:
                continue
            for p, m, pv in ctx.etapes[k][2]:
                if m >= mini and m <= d <= m * (1 + tol) and ctx.dans_plage(t0, pv):
                    lignes.append([ctx.fmt(v), q, lab, p, m, d, round(t0 / pv, 2), "oui" if d == m else "non"])
    lignes.sort(key=lambda r: (r[1], r[0]))
    return ["heure", "joueur_hors_ange", "alliance", "victime_ange", "perte_victime", "gain_joueur", "ratio_tdc_attaquant/victime", "exact"], lignes[: int(ctx.prm.get("max", 400))]


ANALYSES["DETAIL"] = detail

# jour 31/10 exclu (aligné sur ange_analyse.JOURS_EXCLUS)


def fuite_heure(ctx):
    """FUITE : pour chaque heure, parmi TOUS les joueurs ANGE (chasseurs compris) présents aux deux relevés, apparie exactement les pertes
    aux gains (1 perte = 1 à 3 gains, ou 1 gain = 2 à 3 pertes, sans règle 50-150 %). Reste = perte_non_expliquee (fuite possible, le TDC ne
    peut pas aller à un autre membre) et gain_non_explique (chasse = TDC créé, ou attaques mélangées). Lignes : heures avec perte non expliquée >= min."""
    import itertools
    mini = int(ctx.prm.get("min", 1000))
    out, tot = [], collections.Counter()
    for v0, v1 in zip(ctx.versions, ctx.versions[1:]):
        if '202610302300' <= v0 < '202610312300' or '202610302300' <= v1 < '202610312300':
            continue
        A, B = ctx.data[v0], ctx.data[v1]
        L, G = {}, {}
        for p, (al1, t1) in B.items():
            a0 = A.get(p)
            if a0 is None or a0[0] != "ANGE" or al1 != "ANGE":
                continue
            d = t1 - a0[1]
            if d < 0:
                L[p] = -d
            elif d > 0:
                G[p] = d
        perte_tot = sum(L.values()); gain_tot = sum(G.values())
        progres = True
        while progres and L and G:
            progres = False
            # une perte = somme de 1 a 3 gains
            for p in sorted(L, key=lambda x: -L[x]):
                m = L[p]
                gs = [q for q in G if G[q] <= m]
                trouve = None
                for k in (1, 2, 3):
                    if k > len(gs):
                        break
                    for c in itertools.combinations(gs, k):
                        if sum(G[q] for q in c) == m:
                            trouve = c; break
                    if trouve:
                        break
                if trouve:
                    for q in trouve:
                        del G[q]
                    del L[p]; progres = True
            # un gain = somme de 2 a 3 pertes
            for q in sorted(G, key=lambda x: -G[x]):
                m = G[q]
                ls = [p for p in L if L[p] <= m]
                trouve = None
                for k in (2, 3):
                    if k > len(ls):
                        break
                    for c in itertools.combinations(ls, k):
                        if sum(L[p] for p in c) == m:
                            trouve = c; break
                    if trouve:
                        break
                if trouve:
                    for p in trouve:
                        del L[p]
                    del G[q]; progres = True
        pr, gr = sum(L.values()), sum(G.values())
        tot["perte_non_expl"] += pr; tot["perte"] += perte_tot; tot["gain_non_expl"] += gr; tot["gain"] += gain_tot
        if pr >= mini:
            out.append([v1, perte_tot, gain_tot, gain_tot - perte_tot, pr, gr,
                        ", ".join(f"{p}:{m}" for p, m in sorted(L.items(), key=lambda x: -x[1])[:8]),
                        ", ".join(f"{p}:{m}" for p, m in sorted(G.items(), key=lambda x: -x[1])[:8])])
    out.append(["TOTAL", tot["perte"], tot["gain"], tot["gain"] - tot["perte"], tot["perte_non_expl"], tot["gain_non_expl"], "", ""])
    return ["heure", "pertes_ANGE", "gains_ANGE", "net", "perte_non_expliquee", "gain_non_explique", "pertes_non_expliquees", "gains_non_expliques"], out


ANALYSES["FUITE"] = fuite_heure


def convois(ctx):
    import importlib, ange_convois
    importlib.reload(ange_convois)
    return ange_convois.commande(ctx.prm)


ANALYSES["CONVOIS"] = convois
