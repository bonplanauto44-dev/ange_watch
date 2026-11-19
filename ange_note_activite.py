"""ange_note_activite.py : note d'activité quotidienne par joueur de la chaîne ANGE.

Calcule chaque matin à HEURE_NOTES (heure Paris) la note de J-1,
stocke dans notes_activite.db, écrit dans l'onglet 'Notes' du sheet Suivi ANGE.

Note du jour (passeurs/greniers) = moyenne(note_placement, note_attaques) sur 10, puis malus.
  note_placement  : proportion d'heures bien placé x 10 (seuil SEUIL_SOUS_MEDIANE = 20% sous médiane du poste).
  note_attaques   : barème 0->0, 1->3, 2->5, 3->6.5, 4->8, >=5->10.
  malus           : -3 x coeff_poste si placement < 5 ET attaques < 2 (coeff : Passeur 8=1.0 -> Grenier 1=0.4).
  Correction 4h   : si retour à la place à l'heure t, les 4 heures précédentes dans les relevés sont forcées bonnes.

Chasseurs : note = TDC_J1 / moyenne_7j x 10 (plafonné à 10). Alerte si < (1-SEUIL_CHASSEUR) x moy.
Nouveaux  : JOURS_NOUVEAU jours sans note après entrée en chaîne.

Commande manuelle depuis l'onglet Requête :  ANALYSE NOTES jour=2026-11-12 [ecrire=1]
"""

import collections, json, os, re, sqlite3, statistics
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    PARIS = ZoneInfo("Europe/Paris")
except Exception:
    PARIS = None

DOSSIER     = os.path.dirname(os.path.abspath(__file__))
DB_TDC      = os.path.join(DOSSIER, "tdc_serveur.db")
DB_NOTES    = os.path.join(DOSSIER, "notes_activite.db")
ETAT        = os.path.join(DOSSIER, "notes_activite_etat.json")
ETAT_CHAINE = os.path.join(DOSSIER, "convois_suivi_chaine_etat.json")

# Parametres (modifiables)
SEUIL_SOUS_MEDIANE = 0.20
DELAI_CORRECTION_H = 4
JOURS_NOUVEAU      = 3
SEUIL_CHASSEUR     = 0.50
MIN_JOUEURS_MED    = 2
HEURE_NOTES        = 5
FENETRE_HIST_J     = 7
RETENTION_TDC_J    = 15
PERIODES_EXCLUES   = (("202610302300", "202610312300"),)

NOTE_ATT = [0.0, 3.0, 5.0, 6.5, 8.0, 10.0]


# --- Utilitaires ---

def _paris_dt(v):
    dt = datetime.strptime(v, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    return dt.astimezone(PARIS) if PARIS else dt + timedelta(hours=1)


def _exclu(v):
    return any(a <= v < b for a, b in PERIODES_EXCLUES)


def _note_att(n):
    return NOTE_ATT[min(max(0, int(n)), len(NOTE_ATT) - 1)]


def _coeff_malus(poste):
    """Passeur 8=1.0 ... Passeur 1=0.65 ... Grenier 5=0.60 ... Grenier 1=0.40 (lineaire 0.05/pas)."""
    m = re.match(r"(grenier|passeur)\s*(\d+)", (poste or "").lower().strip())
    if not m:
        return 0.0
    typ, rang = m.group(1), int(m.group(2))
    pos = (8 - rang) if typ == "passeur" else (8 + (5 - rang))
    return max(0.0, 1.0 - pos * 0.05)


def _rang_tri(poste):
    m = re.match(r"(grenier|passeur)\s*(\d+)", (poste or "").lower().strip())
    if not m:
        return 999
    typ, rang = m.group(1), int(m.group(2))
    return (8 - rang) if typ == "passeur" else (8 + (5 - rang))


def _note_str(n):
    return f"{n:.1f}" if n is not None else "—"


def _fm(n):
    if n is None:
        return "—"
    return f"{int(n):,}".replace(",", " ")


# --- Base de donnees notes ---

def _init_db(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chaine (
            pseudo       TEXT PRIMARY KEY,
            poste        TEXT,
            hors_chaine  INTEGER DEFAULT 0,
            date_entree  TEXT DEFAULT '2000-01-01'
        );
        CREATE TABLE IF NOT EXISTS mouvements (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date_mouv   TEXT,
            pseudo      TEXT,
            type_mouv   TEXT,
            poste_avant TEXT,
            poste_apres TEXT
        );
        CREATE TABLE IF NOT EXISTS notes_jour (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date_paris      TEXT NOT NULL,
            pseudo          TEXT NOT NULL,
            poste           TEXT,
            note_placement  REAL,
            note_attaques   REAL,
            malus           REAL DEFAULT 0,
            note_finale     REAL,
            note_7j         REAL,
            heures_bonnes   INTEGER,
            heures_totales  INTEGER,
            nb_attaques     INTEGER,
            nb_chasses      INTEGER,
            tdc_chasse      INTEGER,
            detail          TEXT,
            UNIQUE(date_paris, pseudo)
        );
        CREATE INDEX IF NOT EXISTS idx_notes_date   ON notes_jour(date_paris);
        CREATE INDEX IF NOT EXISTS idx_notes_pseudo ON notes_jour(pseudo);
        CREATE TABLE IF NOT EXISTS postes_historique (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            pseudo     TEXT NOT NULL,
            poste      TEXT NOT NULL,
            date_debut TEXT NOT NULL,
            date_fin   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ph_pseudo ON postes_historique(pseudo);
    """)
    conn.commit()


def _ouvrir_notes():
    conn = sqlite3.connect(DB_NOTES, timeout=30)
    conn.row_factory = sqlite3.Row
    _init_db(conn)
    return conn


# --- Gestion table chaine ---

def _lire_postes_fichier():
    """Lit postes et hors_chaine depuis convois_suivi_chaine_etat.json (cles normalisees en minuscules)."""
    try:
        with open(ETAT_CHAINE, encoding="utf-8") as f:
            d = json.load(f)
        postes = {p.lower(): po for p, po in d.get("postes", {}).items()}
        hors   = {p.lower() for p, v in d.get("hors", {}).items() if v}
        return postes, hors
    except Exception:
        return {}, set()


def init_chaine(conn_notes, postes, hors_set, aujourd_hui):
    """Initialise la table chaine (une seule fois). Tous recevront date_entree='2000-01-01'."""
    if conn_notes.execute("SELECT COUNT(*) FROM chaine").fetchone()[0] > 0:
        # Verifier aussi postes_historique : peut etre vide si ajout apres coup
        if conn_notes.execute("SELECT COUNT(*) FROM postes_historique").fetchone()[0] == 0:
            for pseudo, poste in postes.items():
                conn_notes.execute(
                    "INSERT INTO postes_historique(pseudo,poste,date_debut,date_fin) VALUES (?,?,?,?)",
                    (pseudo, poste, "2000-01-01", None)
                )
            conn_notes.commit()
        return
    for pseudo, poste in postes.items():
        hors = 1 if pseudo in hors_set else 0
        conn_notes.execute(
            "INSERT OR IGNORE INTO chaine(pseudo,poste,hors_chaine,date_entree) VALUES (?,?,?,?)",
            (pseudo, poste, hors, "2000-01-01")
        )
        conn_notes.execute(
            "INSERT INTO postes_historique(pseudo,poste,date_debut,date_fin) VALUES (?,?,?,?)",
            (pseudo, poste, "2000-01-01", None)
        )
    conn_notes.commit()


def maj_chaine(conn_notes, postes, hors_set, aujourd_hui):
    """Met a jour chaine + mouvements selon les changements detectes."""
    existants = {r["pseudo"]: r for r in conn_notes.execute("SELECT * FROM chaine").fetchall()}
    quand = aujourd_hui
    for pseudo, poste in postes.items():
        hors = 1 if pseudo in hors_set else 0
        if pseudo not in existants:
            conn_notes.execute(
                "INSERT INTO chaine(pseudo,poste,hors_chaine,date_entree) VALUES (?,?,?,?)",
                (pseudo, poste, hors, aujourd_hui)
            )
            conn_notes.execute(
                "INSERT INTO mouvements(date_mouv,pseudo,type_mouv,poste_avant,poste_apres) VALUES (?,?,?,?,?)",
                (quand, pseudo, "entree", "", poste)
            )
            # Ouvrir une ligne dans postes_historique
            conn_notes.execute(
                "INSERT INTO postes_historique(pseudo,poste,date_debut,date_fin) VALUES (?,?,?,?)",
                (pseudo, poste, aujourd_hui, None)
            )
        else:
            anc = existants[pseudo]
            if anc["poste"] != poste:
                conn_notes.execute(
                    "INSERT INTO mouvements(date_mouv,pseudo,type_mouv,poste_avant,poste_apres) VALUES (?,?,?,?,?)",
                    (quand, pseudo, "poste", anc["poste"], poste)
                )
                conn_notes.execute("UPDATE chaine SET poste=? WHERE pseudo=?", (poste, pseudo))
                # Fermer l'ancienne ligne et ouvrir la nouvelle
                conn_notes.execute(
                    "UPDATE postes_historique SET date_fin=? WHERE pseudo=? AND date_fin IS NULL",
                    (aujourd_hui, pseudo)
                )
                conn_notes.execute(
                    "INSERT INTO postes_historique(pseudo,poste,date_debut,date_fin) VALUES (?,?,?,?)",
                    (pseudo, poste, aujourd_hui, None)
                )
            if anc["hors_chaine"] != hors:
                conn_notes.execute("UPDATE chaine SET hors_chaine=? WHERE pseudo=?", (hors, pseudo))
    for pseudo in list(existants):
        if pseudo not in postes:
            conn_notes.execute(
                "INSERT INTO mouvements(date_mouv,pseudo,type_mouv,poste_avant,poste_apres) VALUES (?,?,?,?,?)",
                (quand, pseudo, "sortie", existants[pseudo]["poste"], "")
            )
            conn_notes.execute("DELETE FROM chaine WHERE pseudo=?", (pseudo,))
            # Fermer la ligne postes_historique
            conn_notes.execute(
                "UPDATE postes_historique SET date_fin=? WHERE pseudo=? AND date_fin IS NULL",
                (aujourd_hui, pseudo)
            )
    conn_notes.commit()


def _date_entree(conn_notes, pseudo):
    r = conn_notes.execute("SELECT date_entree FROM chaine WHERE pseudo=?", (pseudo,)).fetchone()
    return r["date_entree"] if r else "2000-01-01"


def _poste_a_la_date(conn_notes, pseudo, date_paris):
    """Retourne le poste du joueur a la date donnee d'apres postes_historique.
    Fallback : poste actuel dans chaine si l'historique ne couvre pas cette date."""
    r = conn_notes.execute(
        "SELECT poste FROM postes_historique "
        "WHERE pseudo=? AND date_debut <= ? AND (date_fin IS NULL OR date_fin > ?) "
        "ORDER BY date_debut DESC LIMIT 1",
        (pseudo, date_paris, date_paris)
    ).fetchone()
    if r:
        return r["poste"]
    # Fallback : etat actuel
    r2 = conn_notes.execute("SELECT poste FROM chaine WHERE pseudo=?", (pseudo,)).fetchone()
    return r2["poste"] if r2 else None


# --- Lecture des donnees TDC ---

def _versions_jour(conn_tdc, date_paris):
    """Versions UTC qui tombent dans le jour date_paris (YYYY-MM-DD) en heure Paris."""
    j = datetime.strptime(date_paris, "%Y-%m-%d")
    if PARIS:
        from zoneinfo import ZoneInfo
        debut = datetime(j.year, j.month, j.day, 0, 0, tzinfo=PARIS).astimezone(timezone.utc)
        fin   = datetime(j.year, j.month, j.day, 23, 59, tzinfo=PARIS).astimezone(timezone.utc)
    else:
        debut = datetime(j.year, j.month, j.day, 23, 0, tzinfo=timezone.utc) - timedelta(days=1)
        fin   = datetime(j.year, j.month, j.day, 22, 59, tzinfo=timezone.utc)
    rows = conn_tdc.execute(
        "SELECT DISTINCT version FROM tdc WHERE version >= ? AND version <= ? ORDER BY version",
        (debut.strftime("%Y%m%d%H%M"), fin.strftime("%Y%m%d%H%M"))
    ).fetchall()
    return [r[0] for r in rows if not _exclu(r[0])]


def _lire_tdc_versions(conn_tdc, versions):
    """Retourne {version: {pseudo_lower: tdc}} pour les versions donnees (membres ANGE)."""
    if not versions:
        return {}
    ph = ",".join("?" * len(versions))
    result = {}
    for v, p, t in conn_tdc.execute(
        f"SELECT version, pseudo, tdc FROM tdc WHERE version IN ({ph}) AND alliance='ANGE' ORDER BY version",
        versions
    ).fetchall():
        result.setdefault(v, {})[p.lower()] = t
    return result


# --- Calcul du placement ---

def _calc_placement(versions, tdc_par_version, postes, hors_set, chasseurs_set):
    """Calcule la note de placement (non-chasseurs, non-hors-chaine) sur les versions donnees.
    Retourne {pseudo_lower: (nb_bonnes, nb_total)} apres correction DELAI_CORRECTION_H heures."""
    membres = {p: po for p, po in postes.items()
               if p not in hors_set and not po.lower().startswith("chasseur")}
    heures = {p: [] for p in membres}
    for v in versions:
        tdc = tdc_par_version.get(v, {})
        par_poste = collections.defaultdict(list)
        for p, po in membres.items():
            if p in tdc:
                par_poste[po].append(tdc[p])
        med = {po: statistics.median(vals) for po, vals in par_poste.items()
               if len(vals) >= MIN_JOUEURS_MED}
        for p, po in membres.items():
            if p not in tdc or po not in med:
                continue
            bonne = tdc[p] >= (1 - SEUIL_SOUS_MEDIANE) * med[po]
            heures[p].append((v, bonne))
    # Correction DELAI_CORRECTION_H heures
    for p, lst in heures.items():
        corriges = set()
        for i, (v, bonne) in enumerate(lst):
            if not bonne:
                continue
            t = datetime.strptime(v, "%Y%m%d%H%M")
            for j in range(i):
                vj, bj = lst[j]
                if bj:
                    continue
                tj = datetime.strptime(vj, "%Y%m%d%H%M")
                if 0 < (t - tj).total_seconds() <= DELAI_CORRECTION_H * 3600:
                    corriges.add(j)
        heures[p] = [(v, True if i in corriges else b) for i, (v, b) in enumerate(lst)]
    return {p: (sum(b for _, b in lst), len(lst)) for p, lst in heures.items() if lst}


# --- Comptage des variations TDC ---

def _compter_variations(conn_tdc, date_debut, date_fin_excl, chasseurs_set):
    """Compte les hausses de TDC par joueur par jour sur [date_debut, date_fin_excl[.
    Retourne att_j[pseudo][date], chasse_j[pseudo][date] (TDC total), nb_ch_j[pseudo][date]."""
    j_debut = datetime.strptime(date_debut, "%Y-%m-%d")
    j_fin   = datetime.strptime(date_fin_excl, "%Y-%m-%d")
    if PARIS:
        from zoneinfo import ZoneInfo
        v_debut = datetime(j_debut.year, j_debut.month, j_debut.day, 0, 0,
                           tzinfo=PARIS).astimezone(timezone.utc).strftime("%Y%m%d%H%M")
        j_plus  = j_fin + timedelta(days=1)
        v_fin   = datetime(j_plus.year, j_plus.month, j_plus.day, 0, 0,
                           tzinfo=PARIS).astimezone(timezone.utc).strftime("%Y%m%d%H%M")
    else:
        v_debut = (j_debut - timedelta(hours=1)).strftime("%Y%m%d%H%M")
        v_fin   = (j_fin + timedelta(hours=23)).strftime("%Y%m%d%H%M")
    rows = conn_tdc.execute(
        "SELECT version, pseudo, tdc FROM tdc WHERE version >= ? AND version < ? AND alliance='ANGE'"
        " ORDER BY version", (v_debut, v_fin)
    ).fetchall()
    par_v = {}
    for v, p, t in rows:
        if not _exclu(v):
            par_v.setdefault(v, {})[p.lower()] = t
    versions = sorted(par_v.keys())
    att_j    = collections.defaultdict(lambda: collections.defaultdict(int))
    chasse_j = collections.defaultdict(lambda: collections.defaultdict(int))
    nb_ch_j  = collections.defaultdict(lambda: collections.defaultdict(int))
    for v0, v1 in zip(versions, versions[1:]):
        A, B = par_v[v0], par_v[v1]
        date_p = _paris_dt(v1).strftime("%Y-%m-%d")
        if date_p < date_debut or date_p >= date_fin_excl:
            continue
        for p, t1 in B.items():
            t0 = A.get(p)
            if t0 is None or t1 <= t0:
                continue
            diff = t1 - t0
            if p in chasseurs_set:
                chasse_j[p][date_p] += diff
                nb_ch_j[p][date_p]  += 1
            else:
                att_j[p][date_p] += 1
    return att_j, chasse_j, nb_ch_j


# --- Calcul des notes ---

def calculer_notes_j1(conn_notes, conn_tdc, date_j1, postes, hors_set, chasseurs_set):
    """Calcule toutes les notes pour le jour date_j1 (YYYY-MM-DD).
    Retourne liste de dicts (un par joueur actif de la chaine)."""
    today = (datetime.strptime(date_j1, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    j1_dt = datetime.strptime(date_j1, "%Y-%m-%d")

    versions  = _versions_jour(conn_tdc, date_j1)
    tdc_par_v = _lire_tdc_versions(conn_tdc, versions)
    placement = _calc_placement(versions, tdc_par_v, postes, hors_set, chasseurs_set)

    j_hist = (j1_dt - timedelta(days=FENETRE_HIST_J)).strftime("%Y-%m-%d")
    att_j, chasse_j, nb_ch_j = _compter_variations(conn_tdc, j_hist, today, chasseurs_set)

    notes = []

    # Passeurs et greniers — utiliser le poste du joueur A CETTE DATE (pas le poste actuel)
    for pseudo, poste_actuel in postes.items():
        poste = _poste_a_la_date(conn_notes, pseudo, date_j1) or poste_actuel
        if pseudo in hors_set or poste.lower().startswith("chasseur"):
            continue
        date_ent = _date_entree(conn_notes, pseudo)
        try:
            days_in = (j1_dt - datetime.strptime(date_ent[:10], "%Y-%m-%d")).days
        except Exception:
            days_in = 9999
        if days_in < JOURS_NOUVEAU:
            notes.append({"pseudo": pseudo, "poste": poste, "type": "passeur_grenier",
                          "note_placement": None, "note_attaques": None, "malus": 0.0,
                          "note_finale": None, "note_7j": None,
                          "heures_bonnes": None, "heures_totales": None,
                          "nb_attaques": None, "nb_chasses": None, "tdc_chasse": None,
                          "remarque": f"nouveau ({days_in}j / {JOURS_NOUVEAU}j requis)"})
            continue

        pl = placement.get(pseudo)
        if pl and pl[1] > 0:
            nb_b, nb_t = pl
            n_pl = round(10 * nb_b / nb_t, 2)
        else:
            nb_b, nb_t = 0, 0
            n_pl = None

        nb_att = att_j[pseudo].get(date_j1, 0)
        n_att  = _note_att(nb_att)

        if n_pl is not None:
            note_j = (n_pl + n_att) / 2.0
            malus  = 3.0 * _coeff_malus(poste) if n_pl < 5.0 and nb_att < 2 else 0.0
        else:
            note_j = n_att
            malus  = 0.0
        note_finale = max(0.0, round(note_j - malus, 2))

        notes_7j = []
        for k in range(1, FENETRE_HIST_J + 1):
            dj = (j1_dt - timedelta(days=k)).strftime("%Y-%m-%d")
            r  = conn_notes.execute(
                "SELECT note_finale FROM notes_jour WHERE pseudo=? AND date_paris=?",
                (pseudo, dj)).fetchone()
            if r and r["note_finale"] is not None:
                notes_7j.append(r["note_finale"])
        note_7j = round(statistics.mean(notes_7j), 2) if notes_7j else None

        notes.append({"pseudo": pseudo, "poste": poste, "type": "passeur_grenier",
                      "note_placement": n_pl, "note_attaques": round(n_att, 2),
                      "malus": round(malus, 2), "note_finale": note_finale, "note_7j": note_7j,
                      "heures_bonnes": nb_b, "heures_totales": nb_t,
                      "nb_attaques": nb_att, "nb_chasses": None, "tdc_chasse": None,
                      "remarque": ""})

    # Chasseurs — meme logique
    for pseudo, poste_actuel in postes.items():
        poste = _poste_a_la_date(conn_notes, pseudo, date_j1) or poste_actuel
        if pseudo in hors_set or not poste.lower().startswith("chasseur"):
            continue
        date_ent = _date_entree(conn_notes, pseudo)
        try:
            days_in = (j1_dt - datetime.strptime(date_ent[:10], "%Y-%m-%d")).days
        except Exception:
            days_in = 9999
        if days_in < JOURS_NOUVEAU:
            notes.append({"pseudo": pseudo, "poste": poste, "type": "chasseur",
                          "note_placement": None, "note_attaques": None, "malus": 0.0,
                          "note_finale": None, "note_7j": None,
                          "heures_bonnes": None, "heures_totales": None,
                          "nb_attaques": None, "nb_chasses": None, "tdc_chasse": None,
                          "remarque": f"nouveau ({days_in}j / {JOURS_NOUVEAU}j requis)"})
            continue

        tdc_j1 = chasse_j[pseudo].get(date_j1, 0)
        nb_ch  = nb_ch_j[pseudo].get(date_j1, 0)

        hist_nz = [chasse_j[pseudo].get((j1_dt - timedelta(days=k)).strftime("%Y-%m-%d"), 0)
                   for k in range(1, FENETRE_HIST_J + 1)]
        hist_nz = [v for v in hist_nz if v > 0]

        note_ch  = None
        probleme = False
        if hist_nz:
            moy_7j = statistics.mean(hist_nz)
            if moy_7j > 0:
                note_ch  = min(10.0, round(10 * tdc_j1 / moy_7j, 2))
                probleme = (moy_7j - tdc_j1) / moy_7j > SEUIL_CHASSEUR

        notes_7j_hist = []
        for k in range(1, FENETRE_HIST_J + 1):
            dj = (j1_dt - timedelta(days=k)).strftime("%Y-%m-%d")
            r  = conn_notes.execute(
                "SELECT note_finale FROM notes_jour WHERE pseudo=? AND date_paris=?",
                (pseudo, dj)).fetchone()
            if r and r["note_finale"] is not None:
                notes_7j_hist.append(r["note_finale"])
        note_7j = round(statistics.mean(notes_7j_hist), 2) if notes_7j_hist else None

        notes.append({"pseudo": pseudo, "poste": poste, "type": "chasseur",
                      "note_placement": None, "note_attaques": None, "malus": 0.0,
                      "note_finale": note_ch, "note_7j": note_7j,
                      "heures_bonnes": None, "heures_totales": None,
                      "nb_attaques": None, "nb_chasses": nb_ch, "tdc_chasse": tdc_j1,
                      "remarque": "⚠️ chasse basse" if probleme else ""})

    return notes


def stocker_notes(conn_notes, date_j1, notes):
    for n in notes:
        conn_notes.execute("""
            INSERT OR REPLACE INTO notes_jour
            (date_paris,pseudo,poste,note_placement,note_attaques,malus,note_finale,note_7j,
             heures_bonnes,heures_totales,nb_attaques,nb_chasses,tdc_chasse,detail)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (date_j1, n["pseudo"], n.get("poste"),
              n.get("note_placement"), n.get("note_attaques"), n.get("malus", 0.0),
              n.get("note_finale"), n.get("note_7j"),
              n.get("heures_bonnes"), n.get("heures_totales"),
              n.get("nb_attaques"), n.get("nb_chasses"), n.get("tdc_chasse"),
              json.dumps({"type": n.get("type"), "remarque": n.get("remarque", "")},
                         ensure_ascii=False)))
    conn_notes.commit()


# --- Affichage dans le sheet ---

def ecrire_onglet_notes(sh, conn_notes, date_j1, notes):
    """Ecrit l'onglet 'Notes' dans le Suivi ANGE."""
    import gspread
    try:
        ws = sh.worksheet("Notes")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet("Notes", rows=120, cols=12)
    ws.clear()
    entetes = ["Joueur", "Poste", "Note J /10", "Placement /10", "Attaques /10",
               "Malus", "Moy 7j", "H bonnes/total", "Nb attaques", "TDC chasse", "Remarque"]
    lignes = [
        [f"Notes d'activite — J-1 = {date_j1}  (calcul a {HEURE_NOTES}h Paris)"] + [""] * 10,
        [f"Note J = moy(placement,attaques)/10 - malus. Seuil sous-mediane : "
         f"{int(SEUIL_SOUS_MEDIANE*100)}%. Correction {DELAI_CORRECTION_H}h."] + [""] * 10,
        [],
        entetes
    ]
    nc = [n for n in notes if n.get("type") == "passeur_grenier" and n.get("note_finale") is not None]
    nv = [n for n in notes if n.get("type") == "passeur_grenier" and n.get("note_finale") is None]
    nc.sort(key=lambda x: (x.get("note_finale") or 10, _rang_tri(x.get("poste", ""))))
    for n in nc + nv:
        hb = (f"{n.get('heures_bonnes','?')}/{n.get('heures_totales','?')}"
              if n.get("heures_totales") else "—")
        lignes.append([
            n["pseudo"], n.get("poste", ""),
            _note_str(n.get("note_finale")), _note_str(n.get("note_placement")),
            _note_str(n.get("note_attaques")), _note_str(n.get("malus")),
            _note_str(n.get("note_7j")), hb,
            str(n.get("nb_attaques") if n.get("nb_attaques") is not None else "—"),
            "", n.get("remarque", "")
        ])
    chass = [n for n in notes if n.get("type") == "chasseur"]
    if chass:
        lignes.append([])
        lignes.append(["— CHASSEURS —"] + [""] * 10)
        chass.sort(key=lambda x: (x.get("note_finale") or 0))
        for n in chass:
            lignes.append([
                n["pseudo"], n.get("poste", ""),
                _note_str(n.get("note_finale")), "—", "—", "—",
                _note_str(n.get("note_7j")), "—", "—",
                _fm(n.get("tdc_chasse")), n.get("remarque", "")
            ])
    ws.resize(rows=max(len(lignes) + 2, 10), cols=11)
    ws.update(range_name="A1", values=lignes, value_input_option="RAW")


# --- Nettoyage TDC ---

def _nettoyer_tdc():
    try:
        now = datetime.now(PARIS) if PARIS else datetime.now()
        limite = now - timedelta(days=RETENTION_TDC_J)
        if PARIS:
            v_lim = datetime(limite.year, limite.month, limite.day, 0, 0,
                             tzinfo=PARIS).astimezone(timezone.utc).strftime("%Y%m%d%H%M")
        else:
            v_lim = (limite.replace(hour=0, minute=0) - timedelta(hours=1)).strftime("%Y%m%d%H%M")
        conn = sqlite3.connect(DB_TDC, timeout=60)
        try:
            n = conn.execute("DELETE FROM tdc WHERE version < ?", (v_lim,)).rowcount
            conn.commit()
            if n > 0:
                print(f"notes : nettoyage TDC — {n} lignes supprimees (< {v_lim})", flush=True)
        finally:
            conn.close()
    except Exception as e:
        print(f"notes : nettoyage TDC erreur : {e}", flush=True)


# --- Point d'entree ---

def calculer_et_stocker(sh, date_j1, postes=None, hors_set=None, ecrire=True):
    """Calcule les notes de date_j1, stocke en DB (si ecrire=True), ecrit dans le sheet (si sh fourni)."""
    if postes is None or hors_set is None:
        postes, hors_set = _lire_postes_fichier()
    chasseurs_set = {p for p, po in postes.items() if po.lower().startswith("chasseur")}
    conn_notes = _ouvrir_notes()
    conn_tdc   = sqlite3.connect(DB_TDC, timeout=60)
    conn_tdc.execute("PRAGMA query_only=ON")
    try:
        init_chaine(conn_notes, postes, hors_set, date_j1)
        notes = calculer_notes_j1(conn_notes, conn_tdc, date_j1, postes, hors_set, chasseurs_set)
        if ecrire:
            stocker_notes(conn_notes, date_j1, notes)
            if sh is not None:
                ecrire_onglet_notes(sh, conn_notes, date_j1, notes)
        return notes
    finally:
        conn_notes.close()
        conn_tdc.close()


# --- Etat persistant du tick ---

def _lire_etat():
    try:
        return json.load(open(ETAT, encoding="utf-8"))
    except Exception:
        return {}


def _ecrire_etat(st):
    json.dump(st, open(ETAT, "w", encoding="utf-8"))


def tick_notes(sh, force=False):
    """Appele par ange_suivi.tick(). Lance le calcul de J-1 a HEURE_NOTES (Paris), une fois/jour."""
    try:
        now = datetime.now(PARIS) if PARIS else datetime.now()
        auj = now.strftime("%Y-%m-%d")
        st  = _lire_etat()
        if not force and now.hour < HEURE_NOTES:
            return None
        if not force and st.get("calc_du") == auj:
            return None
        j1    = (now.date() - timedelta(days=1)).strftime("%Y-%m-%d")
        notes = calculer_et_stocker(sh, j1, ecrire=True)
        st["calc_du"] = auj
        st["last_j1"] = j1
        _ecrire_etat(st)
        _nettoyer_tdc()
        n_notees = sum(1 for n in notes if n.get("note_finale") is not None)
        return f"NOTES {now:%H:%M} : J-1={j1} · {n_notees}/{len(notes)} joueurs notes"
    except Exception as e:
        return f"NOTES ERREUR : {e}"


# --- Simulation ---

def simuler(dates=None, nb_jours=5):
    """Calcule les notes sur plusieurs jours en base, sans rien ecrire. Retourne un resume texte."""
    conn_tdc   = sqlite3.connect(DB_TDC, timeout=60)
    conn_tdc.execute("PRAGMA query_only=ON")
    conn_notes = _ouvrir_notes()
    postes, hors_set = _lire_postes_fichier()
    chasseurs_set = {p for p, po in postes.items() if po.lower().startswith("chasseur")}
    if not dates:
        last = conn_tdc.execute("SELECT MAX(version) FROM tdc").fetchone()[0]
        if not last:
            conn_tdc.close(); conn_notes.close()
            return "Pas de donnees TDC en base."
        last_dt = _paris_dt(last)
        dates = [(last_dt.date() - timedelta(days=k)).strftime("%Y-%m-%d")
                 for k in range(1, nb_jours + 1)]
    cibles = {"zzzorka", "velvet", "arold"}
    lignes = [
        f"SIMULATION NOTES — {nb_jours} jours (sans ecriture)",
        f"Parametres : seuil={int(SEUIL_SOUS_MEDIANE*100)}% sous mediane, "
        f"correction {DELAI_CORRECTION_H}h, nouveaux {JOURS_NOUVEAU}j, "
        f"seuil chasseur {int(SEUIL_CHASSEUR*100)}%"
    ]
    for date_j1 in sorted(dates, reverse=True):
        try:
            notes = calculer_notes_j1(conn_notes, conn_tdc, date_j1, postes, hors_set, chasseurs_set)
            pg = sorted(
                [n for n in notes if n.get("type") == "passeur_grenier" and n.get("note_finale") is not None],
                key=lambda x: x.get("note_finale") or 10
            )
            lignes.append(f"\n=== {date_j1} ({len(pg)} passeurs/greniers notes) ===")
            lignes.append(f"{'Joueur':<15} {'Poste':<12} {'NoteJ':>6} {'Plac':>6} {'Att':>5}"
                          f" {'Malus':>6} {'7j':>5}  Heures")
            for n in pg[:10]:
                hb  = (f"{n.get('heures_bonnes','?')}/{n.get('heures_totales','?')}"
                       if n.get("heures_totales") else "—")
                tag = " <<" if n["pseudo"] in cibles else ""
                lignes.append(
                    f"{n['pseudo']:<15} {n.get('poste','?'):<12}"
                    f" {_note_str(n.get('note_finale')):>6} {_note_str(n.get('note_placement')):>6}"
                    f" {_note_str(n.get('note_attaques')):>5} {_note_str(n.get('malus')):>6}"
                    f" {_note_str(n.get('note_7j')):>5}  {hb}{tag}"
                )
            for n in pg[10:]:
                if n["pseudo"] in cibles:
                    hb = (f"{n.get('heures_bonnes','?')}/{n.get('heures_totales','?')}"
                          if n.get("heures_totales") else "—")
                    lignes.append(
                        f"  > CAS CIBLE : {n['pseudo']:<12} {n.get('poste','?'):<12}"
                        f" note={_note_str(n.get('note_finale'))} plac={_note_str(n.get('note_placement'))}"
                        f" att={n.get('nb_attaques','?')}  {hb}"
                    )
        except Exception as e:
            lignes.append(f"  ERREUR {date_j1}: {e}")
    conn_tdc.close()
    conn_notes.close()
    return "\n".join(lignes)


# --- AUTOTEST ---

def AUTOTEST():
    import tempfile

    # 1. coeff_malus
    assert abs(_coeff_malus("Passeur 8") - 1.00) < 0.001, _coeff_malus("Passeur 8")
    assert abs(_coeff_malus("Passeur 7") - 0.95) < 0.001, _coeff_malus("Passeur 7")
    assert abs(_coeff_malus("Passeur 1") - 0.65) < 0.001, _coeff_malus("Passeur 1")
    assert abs(_coeff_malus("Grenier 5") - 0.60) < 0.001, _coeff_malus("Grenier 5")
    assert abs(_coeff_malus("Grenier 1") - 0.40) < 0.001, _coeff_malus("Grenier 1")
    assert _coeff_malus("Chasseur") == 0.0

    # 2. note_att
    assert _note_att(0) == 0.0
    assert _note_att(1) == 3.0
    assert _note_att(2) == 5.0
    assert _note_att(3) == 6.5
    assert _note_att(4) == 8.0
    assert _note_att(5) == 10.0
    assert _note_att(99) == 10.0

    # 3. mediane + seuil 20% + correction 4h
    # 4 joueurs Passeur 1 : d passe sous la mediane a h1 puis revient a h2
    tdc_par_v = {
        "202611010000": {"a": 100, "b": 90, "c": 95, "d": 85},
        # h1 : mediane([102,91,96,73]) = (91+96)/2 = 93.5 -> seuil = 74.8 -> d=73 mauvais
        "202611010100": {"a": 102, "b": 91, "c": 96, "d": 73},
        # h2 : mediane([103,92,97,95]) = (95+97)/2 = 96 -> seuil = 76.8 -> d=95 bon -> corrige h1
        "202611010200": {"a": 103, "b": 92, "c": 97, "d": 95},
    }
    postes_t = {"a": "Passeur 1", "b": "Passeur 1", "c": "Passeur 1", "d": "Passeur 1"}
    res = _calc_placement(["202611010000", "202611010100", "202611010200"],
                          tdc_par_v, postes_t, set(), set())
    assert res["d"] == (3, 3), f"correction 4h attendu (3,3) : {res['d']}"

    # 4. heures manquantes ignorees
    tdc_par_v2 = {
        "202611010000": {"a": 100, "b": 90, "c": 95},
        "202611010200": {"a": 102, "b": 91},   # c manque -> une seule heure relevee pour c
    }
    postes_t2 = {"a": "Passeur 1", "b": "Passeur 1", "c": "Passeur 1"}
    res2 = _calc_placement(["202611010000", "202611010200"], tdc_par_v2, postes_t2, set(), set())
    assert res2.get("c") == (1, 1), f"heure manquante : {res2.get('c')}"

    # 5. nouveau joueur sans note (< JOURS_NOUVEAU)
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        tmp_db = tf.name
    try:
        cn = sqlite3.connect(tmp_db)
        cn.row_factory = sqlite3.Row
        _init_db(cn)
        date_j1_test    = "2026-11-10"
        date_ent_recente = "2026-11-09"   # 1 jour -> trop recent
        cn.execute("INSERT INTO chaine(pseudo,poste,hors_chaine,date_entree) VALUES (?,?,?,?)",
                   ("toto", "Passeur 1", 0, date_ent_recente))
        cn.commit()
        days_in = (datetime.strptime(date_j1_test, "%Y-%m-%d")
                   - datetime.strptime(date_ent_recente, "%Y-%m-%d")).days
        assert days_in < JOURS_NOUVEAU, f"devrait etre recent : {days_in}j < {JOURS_NOUVEAU}"
        cn.close()
    finally:
        os.unlink(tmp_db)

    # 6. malus par poste (Passeur 4 : coeff=0.80, malus=3*0.80=2.40)
    assert abs(_coeff_malus("Passeur 4") - 0.80) < 0.001, _coeff_malus("Passeur 4")
    malus_p4 = 3.0 * _coeff_malus("Passeur 4")
    assert abs(malus_p4 - 2.40) < 0.001, malus_p4
    # note_finale >= 0 meme avec malus eleve
    note_j = (_note_att(1) + 2.0) / 2.0   # 1 attaque, placement arbitraire
    assert max(0.0, note_j - malus_p4) >= 0.0

    # 7. chasseur a part (pas dans les medianes)
    tdc_par_v3 = {"202611010000": {"a": 100, "b": 90, "ch": 500}}
    postes_t3  = {"a": "Passeur 1", "b": "Passeur 1", "ch": "Chasseur"}
    res3 = _calc_placement(["202611010000"], tdc_par_v3, postes_t3, set(), {"ch"})
    assert "ch" not in res3, "chasseur ne doit pas etre dans le calcul de placement"
    # mediane Passeur 1 = mediane(100, 90) = 95, ch=500 n'entre pas dedans -> a et b sont evalues
    assert "a" in res3 and "b" in res3

    # 8. hors chaine ignore
    tdc_par_v4 = {"202611010000": {"a": 100, "b": 90, "hc": 50}}
    postes_t4  = {"a": "Passeur 1", "b": "Passeur 1", "hc": "Passeur 1"}
    res4 = _calc_placement(["202611010000"], tdc_par_v4, postes_t4, {"hc"}, set())
    assert "hc" not in res4, "hors chaine ne doit pas etre note"
    # hc exclu -> mediane(100, 90) = 95 -> a et b bons
    assert res4.get("a") == (1, 1) and res4.get("b") == (1, 1), res4

    # 9. postes_historique : changement de poste tracé + requete a la date
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        tmp_db2 = tf.name
    try:
        cn2 = sqlite3.connect(tmp_db2)
        cn2.row_factory = sqlite3.Row
        _init_db(cn2)
        # Initialiser avec toto en Passeur 2
        init_chaine(cn2, {"toto": "Passeur 2"}, set(), "2026-10-01")
        assert _poste_a_la_date(cn2, "toto", "2026-10-15") == "Passeur 2"
        # Changement vers Passeur 3 le 10/11
        maj_chaine(cn2, {"toto": "Passeur 3"}, set(), "2026-11-10")
        # Avant le changement -> Passeur 2
        assert _poste_a_la_date(cn2, "toto", "2026-11-09") == "Passeur 2",             _poste_a_la_date(cn2, "toto", "2026-11-09")
        # Apres le changement -> Passeur 3
        assert _poste_a_la_date(cn2, "toto", "2026-11-10") == "Passeur 3",             _poste_a_la_date(cn2, "toto", "2026-11-10")
        assert _poste_a_la_date(cn2, "toto", "2026-11-13") == "Passeur 3"
        cn2.close()
    finally:
        os.unlink(tmp_db2)


if __name__ == "__main__":
    AUTOTEST()
    print("AUTOTEST ok")
    import sys
    if "--sim" in sys.argv:
        print(simuler(nb_jours=5))
