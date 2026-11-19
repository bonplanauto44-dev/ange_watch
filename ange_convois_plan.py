# Calcul du plan de convois (port Python de majConvois / repartirConvois_ / limiterPartenaires_ d'Apps Script).
# Fonctions pures : aucune écriture. Validation : comparer avec l'onglet « Historique plan convois ».
import math

JOURS_LISSAGE = 7


def repartir_convois(S, R, dist, interdit, puissance=3):
    """Flot à coût minimal (coût = distance^puissance). S, R : dict pseudo->quantité."""
    ss = sorted(k for k in S if S[k] > 0)
    rr = sorted(k for k in R if R[k] > 0)
    n = len(ss) + len(rr) + 2
    SRC, SNK = n - 2, n - 1
    to, cap, cost = [], [], []
    adj = [[] for _ in range(n)]

    def arete(u, v, c, w):
        adj[u].append(len(to)); to.append(v); cap.append(c); cost.append(w)
        adj[v].append(len(to)); to.append(u); cap.append(0); cost.append(-w)

    INF = 10 ** 15
    for i, s in enumerate(ss):
        arete(SRC, i, S[s], 0)
    for j, r in enumerate(rr):
        arete(len(ss) + j, SNK, R[r], 0)
    lien = []
    for i, s in enumerate(ss):
        for j, r in enumerate(rr):
            if s == r or interdit(s, r):
                continue
            lien.append((s, r, len(to)))
            arete(i, len(ss) + j, INF, dist(s, r) ** puissance)
    while True:
        dd = [math.inf] * n
        pa = [-1] * n
        inq = [False] * n
        dd[SRC] = 0
        q = [SRC]
        inq[SRC] = True
        h = 0
        while h < len(q):
            u = q[h]; h += 1
            inq[u] = False
            for e in adj[u]:
                if cap[e] > 0 and dd[u] + cost[e] < dd[to[e]] - 1e-9:
                    dd[to[e]] = dd[u] + cost[e]
                    pa[to[e]] = e
                    if not inq[to[e]]:
                        inq[to[e]] = True
                        q.append(to[e])
        if pa[SNK] < 0:
            break
        f = INF
        v = SNK
        while v != SRC:
            f = min(f, cap[pa[v]]); v = to[pa[v] ^ 1]
        v = SNK
        while v != SRC:
            cap[pa[v]] -= f; cap[pa[v] ^ 1] += f; v = to[pa[v] ^ 1]
    lignes, servi = [], {}
    for s, r, e in lien:
        qte = cap[e ^ 1]
        if qte > 0:
            lignes.append({"s": s, "r": r, "q": qte})
            servi[r] = servi.get(r, 0) + qte
    reste = {r: R[r] - servi.get(r, 0) for r in rr if R[r] - servi.get(r, 0) > 0}
    return lignes, reste


def limiter_partenaires(lignes, maxi, ok):
    """Au plus `maxi` partenaires par joueur (12 lignes d'envoi / 12 de réception dans un onglet)."""
    out, inn = {}, {}

    def ajoute(s, r, q):
        out.setdefault(s, {}); inn.setdefault(r, {})
        out[s][r] = out[s].get(r, 0) + q
        inn[r][s] = inn[r].get(s, 0) + q

    def retire(s, r, q):
        out[s][r] -= q; inn[r][s] -= q
        if out[s][r] <= 0:
            del out[s][r]; del inn[r][s]

    for l in lignes:
        ajoute(l["s"], l["r"], l["q"])
    nb = lambda o: len(o or {})

    def exces():
        return sum(max(0, nb(out[x]) - maxi) for x in out) + sum(max(0, nb(inn[x]) - maxi) for x in inn)

    for _ in range(500):
        ligne = None
        for s in list(out):
            if not ligne and nb(out[s]) > maxi:
                r = sorted(out[s], key=lambda a: out[s][a])[0]; ligne = (s, r)
        if not ligne:
            for r in list(inn):
                if not ligne and nb(inn[r]) > maxi:
                    s = sorted(inn[r], key=lambda a: inn[r][a])[0]; ligne = (s, r)
        if not ligne:
            break
        s, r = ligne
        q = out[s][r]
        fait = False
        for s2 in list(inn[r]):
            if fait or s2 == s:
                continue
            for r2 in list(out[s2]):
                if fait or r2 == r or not out[s].get(r2, 0) > 0 or out[s2][r2] < q:
                    continue
                retire(s, r, q); retire(s2, r2, q); ajoute(s, r2, q); ajoute(s2, r, q)
                fait = True
        if not fait and ok:
            avant = exces()
            for s2 in list(out):
                if fait or s2 == s or not ok(s2, r):
                    continue
                for r2 in list(out[s]):
                    if fait or r2 == r or not out[s2].get(r2, 0) >= q:
                        continue
                    retire(s, r, q); retire(s2, r2, q); ajoute(s, r2, q); ajoute(s2, r, q)
                    if exces() < avant:
                        fait = True
                    else:
                        retire(s, r2, q); retire(s2, r, q); ajoute(s, r, q); ajoute(s2, r2, q)
        if not fait:
            break
    return [{"s": s, "r": r, "q": q} for s in out for r, q in out[s].items()]


def calcul_plan(joueurs, dist, interdits, dettes, livre=None):
    """joueurs : liste de dict {pseudo, delta, livreur}. dettes : liste de dict {d,c,m,ini}.
    livre : {expediteur: {destinataire: quantité}} déjà livré aujourd'hui.
    Retourne (lignes, dettes_apres, couverture)."""
    ok = lambda a, b: (a, b) not in interdits and (b, a) not in interdits
    tous = {j["pseudo"]: dict(j) for j in joueurs}
    if livre:
        for s, d in livre.items():
            for r, q in d.items():
                if not q > 0:
                    continue
                S, R = tous.get(s), tous.get(r)
                if S and S["delta"] > 0:
                    S["delta"] = max(0, S["delta"] - q)
                if R and R["delta"] < 0:
                    R["delta"] = min(0, R["delta"] + q)
    dettes = [dict(x) for x in dettes]
    for x in dettes:
        D, C = tous.get(x["d"]), tous.get(x["c"])
        if not D or not C or D["delta"] >= 0 or not ok(x["d"], x["c"]):
            continue
        plafond = math.ceil(x["ini"] / JOURS_LISSAGE)
        p = min(x["m"], -D["delta"], plafond)
        if p > 0:
            D["delta"] += p; C["delta"] -= p; x["m"] -= p
    surplusN = {}
    for k, e in tous.items():
        if not e["livreur"] and e["delta"] > 0:
            surplusN[k] = e["delta"]; e["delta"] = 0
    totalS = sum(e["delta"] for e in tous.values() if e["delta"] > 0)
    totalR = sum(-e["delta"] for e in tous.values() if e["delta"] < 0)
    couverture = 1.0
    if totalR > totalS and totalR > 0:
        couverture = totalS / totalR
        for e in tous.values():
            if e["delta"] < 0:
                e["delta"] = -math.floor(-e["delta"] * couverture)
    SX = {k: e["delta"] for k, e in tous.items() if e["delta"] > 0}
    RX = {k: -e["delta"] for k, e in tous.items() if e["delta"] < 0}
    totS, totR = sum(SX.values()), sum(RX.values())
    if totR > 0 and totS > totR:
        part = totR / totS
        SX = {k: math.ceil(v * part) for k, v in SX.items()}
    lignes, restR = repartir_convois(SX, RX, dist, lambda a, b: not ok(a, b), 3)
    lignes = limiter_partenaires(lignes, 12, ok)
    pN = sorted(((n, r, dist(n, r)) for n in surplusN for r in restR if n != r and ok(n, r)), key=lambda t: t[2])
    for n, r, _ in pN:
        q = min(surplusN.get(n, 0), restR.get(r, 0))
        if q > 0:
            dettes.append({"d": n, "c": r, "m": q, "ini": q}); surplusN[n] -= q; restR[r] -= q
    return lignes, [x for x in dettes if x["m"] > 0], couverture
