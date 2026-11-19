"""ange_convois_forms.py : port de majPseudosFormulairesSiChange_ : la liste déroulante « Pseudo » des 3 formulaires (Greniers / Passeurs / Chasseurs)
= joueurs de la famille de postes, hors chaîne exclus. Mise à jour dès que la liste des joueurs ou un poste change (contrôle toutes les 2 min).
Désarmé par défaut (convois_forms_mode.txt : off | prod). Nécessite l'API Google Forms et le compte du VPS éditeur des 3 formulaires."""
import json, os, re, time
import ange_convois as C
import ange_convois_nuit as N
import ange_convois_pages as PG

MODE_FICHIER = os.path.join(C.DOSSIER, "convois_forms_mode.txt")
ETAT_FICHIER = os.path.join(C.DOSSIER, "convois_forms_etat.json")
T_PSEUDO = "Pseudo"
FAMILLES = ["Grenier", "Passeur", "Chasseur"]
API = "https://forms.googleapis.com/v1/forms/"


def mode_forms():
    try:
        v = open(MODE_FICHIER, encoding="utf-8").read().strip().lower()
        return v if v in ("prod",) else "off"
    except OSError:
        return "off"


def famille(poste):
    p = str(poste).strip()
    return "Grenier" if p.startswith("Grenier") else "Passeur" if p.startswith("Passeur") else "Chasseur"


def listes_noms(lignes_cu):
    """{famille: [pseudos triés]} depuis 'Chaîne unique'!A3:Y202 (hors chaîne exclus) ; reproduit majChoixPseudosFormulaires."""
    out = {f: [] for f in FAMILLES}
    for r in lignes_cu:
        r = list(r) + [""] * (25 - len(r))
        pseudo = str(r[2]).strip()
        if not pseudo or r[24] is True or str(r[1]).strip() not in N.POSTES:
            continue
        out[famille(r[1])].append(pseudo)
    return {f: sorted(v, key=PG.cle_tri) for f, v in out.items()}


def signature(lignes_cu):
    s = []
    for r in lignes_cu:
        r = list(r) + [""] * (25 - len(r))
        if str(r[2]).strip() and r[24] is not True:
            s.append(str(r[1]).strip() + "|" + str(r[2]).strip())
    return ";".join(sorted(s))


def session():
    import configparser
    from google.oauth2.service_account import Credentials
    from google.auth.transport.requests import AuthorizedSession
    c = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    c.read(os.path.join(C.DOSSIER, "ange_watch.ini"), encoding="utf-8")
    cred = c.get("google", "credentials", fallback="service_account.json")
    if not os.path.isabs(cred):
        cred = os.path.join(C.DOSSIER, cred)
    return AuthorizedSession(Credentials.from_service_account_file(cred, scopes=["https://www.googleapis.com/auth/forms.body"]))


def ids_formulaires(prive):
    v = C.lire_plages(prive, ["m"], ["'Mode'!B8:B10"])["m"]
    return [str((v[i] if i < len(v) and v[i] else [""])[0]).strip() for i in range(3)]


def lire_formulaire(s, fid):
    r = s.get(API + fid, timeout=40)
    if r.status_code != 200:
        raise RuntimeError("Forms HTTP %d : %s" % (r.status_code, r.text[:150]))
    return r.json()


def trouver_question(form):
    for i, it in enumerate(form.get("items", [])):
        if it.get("title") == T_PSEUDO and "questionItem" in it:
            return i, it
    return None, None


def choix_actuels(item):
    return [o.get("value") for o in item["questionItem"]["question"].get("choiceQuestion", {}).get("options", [])]


def requete_maj(index, item, noms):
    q = item["questionItem"]["question"]
    return {"updateItem": {"item": {"itemId": item["itemId"], "title": T_PSEUDO,
                                    "questionItem": {"question": {"required": q.get("required", True),
                                                                  "choiceQuestion": {"type": "DROP_DOWN", "options": [{"value": n} for n in noms]}}}},
                           "location": {"index": index}, "updateMask": "questionItem.question.choiceQuestion.options"}}


def verifier(prive):
    """Lecture seule : accès aux 3 formulaires, nombre de choix actuel vs attendu."""
    s = session()
    cu = C.lire_plages(prive, ["cu"], ["'Chaîne unique'!A3:Y202"])["cu"]
    att = listes_noms(cu)
    res = []
    for fam, fid in zip(FAMILLES, ids_formulaires(prive)):
        if not fid:
            res.append([fam, "identifiant du formulaire absent"])
            continue
        try:
            f = lire_formulaire(s, fid)
            i, it = trouver_question(f)
            if it is None:
                res.append([fam, "question « Pseudo » introuvable"])
                continue
            act = choix_actuels(it)
            res.append([fam, "accès OK · choix actuels %d · attendus %d · manquants %s · en trop %s" % (
                len(act), len(att[fam]), sorted(set(att[fam]) - set(act))[:5], sorted(set(act) - set(att[fam]))[:5])])
        except Exception as e:
            res.append([fam, "ERREUR " + str(e)[:200]])
    return res


def mettre_a_jour(prive, forcer=False):
    s = session()
    cu = C.lire_plages(prive, ["cu"], ["'Chaîne unique'!A3:Y202"])["cu"]
    att = listes_noms(cu)
    msgs = []
    for fam, fid in zip(FAMILLES, ids_formulaires(prive)):
        if not fid or not att[fam]:
            continue
        f = lire_formulaire(s, fid)
        i, it = trouver_question(f)
        if it is None:
            raise RuntimeError("formulaire %s : question « Pseudo » introuvable" % fam)
        if choix_actuels(it) == att[fam] and not forcer:
            continue
        r = s.post(API + fid + ":batchUpdate", json={"requests": [requete_maj(i, it, att[fam])]}, timeout=40)
        if r.status_code != 200:
            raise RuntimeError("formulaire %s : HTTP %d %s" % (fam, r.status_code, r.text[:150]))
        msgs.append("%s : %d pseudos" % (fam, len(att[fam])))
    return msgs


def tick(sh=None):
    if mode_forms() != "prod":
        return None
    try:
        etat = json.load(open(ETAT_FICHIER))
    except Exception:
        etat = {}
    if time.time() - etat.get("t", 0) < 120:
        return None
    etat["t"] = time.time()
    json.dump(etat, open(ETAT_FICHIER, "w"))
    try:
        import ange_convois_prod as PR
        prive = C.avec_reprise(lambda: PR.ouvrir_prod("prive", PR.jour_du_plan()))
        cu = C.avec_reprise(lambda: C.lire_plages(prive, ["cu"], ["'Chaîne unique'!A3:Y202"])["cu"])
        if etat.get("erreurs"):                       # lecture réussie : on repart de zéro (le compteur ne doit compter que des échecs CONSÉCUTIFS)
            etat["erreurs"] = 0
            json.dump(etat, open(ETAT_FICHIER, "w"))
        sig = signature(cu)
        if sig == etat.get("sig"):
            return None
        if len([1 for r in cu if r and len(r) > 2 and str(r[2]).strip()]) < 70:
            raise RuntimeError("chaîne lue incomplète")
        msgs = C.avec_reprise(lambda: mettre_a_jour(prive))
        etat["sig"], etat["erreurs"] = sig, 0
        json.dump(etat, open(ETAT_FICHIER, "w"))
        return "FORMULAIRES mis à jour : " + " ; ".join(msgs) if msgs else None
    except Exception as e:
        etat["erreurs"] = etat.get("erreurs", 0) + 1
        json.dump(etat, open(ETAT_FICHIER, "w"))
        if etat["erreurs"] == 3:
            try:
                import ange_convois_prod as PR2
                PR2.alerter("⚠ Formulaires (VPS) : mise à jour impossible : %s" % str(e)[:200])
            except Exception:
                pass
        return "FORMULAIRES erreur : %s" % str(e)[:200]


def AUTOTEST():
    cu = [["3.1", "Grenier 1", "Zed", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", False],
          ["3.1", "Passeur 2", "ané", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", False],
          ["3.1", "Passeur 2", "Abel", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", True]]
    l = listes_noms(cu)
    assert l["Grenier"] == ["Zed"] and l["Passeur"] == ["ané"] and l["Chasseur"] == []
    assert signature(cu) == "Grenier 1|Zed;Passeur 2|ané"
    it = {"itemId": "x1", "title": "Pseudo", "questionItem": {"question": {"required": True, "choiceQuestion": {"type": "DROP_DOWN", "options": [{"value": "a"}]}}}}
    r = requete_maj(2, it, ["a", "b"])
    assert r["updateItem"]["location"]["index"] == 2 and len(r["updateItem"]["item"]["questionItem"]["question"]["choiceQuestion"]["options"]) == 2
    return True
# v2 forms : reprise 429, compteur d'erreurs consecutives
