"""ange_convois_tick.py : veille des membres/postes de la feuille privée (appelé chaque minute par le service via ange_suivi.tick).
Quand la liste des (joueur, poste, hors chaîne, attaque seulement) change, seuls les onglets concernés sont mis à jour
(ange_convois_live.synchroniser) : onglet masqué / supprimé / créé + convois liés retirés des autres onglets.
MODE (fichier convois_mode.txt à côté du code) : « test » (défaut) = copies TEST uniquement ; « off » = ne fait rien.
« prod » = changements immédiats dans les fichiers de production (nécessite l'armement permanent)."""
import json, os, time

DOSSIER = os.path.dirname(os.path.abspath(__file__))
ETAT = os.path.join(DOSSIER, "convois_veille.json")
MODE = os.path.join(DOSSIER, "convois_mode.txt")
FICHIERS = ("greniers", "passeurs", "chasseurs")


def mode():
    try:
        return open(MODE, encoding="utf-8").read().strip().lower() or "test"
    except OSError:
        return "test"


def signature(membres):
    # Groupe (fichier : 3.1/3.2/3.3) au lieu du poste exact : un renommage de poste
    # dans le même fichier (ex. Passeur 8 → Passeur 7) ne déclenche plus de purge des convois.
    return sorted((m["pseudo"], m["g"], bool(m["hors"]), bool(m["seul"])) for m in membres)


def differences(avant, apres):
    """Pure : (arrivés, partis, changés) entre deux signatures."""
    a = {x[0]: x for x in avant or []}
    b = {x[0]: x for x in apres}
    return [p for p in b if p not in a], [p for p in a if p not in b], [p for p in b if p in a and tuple(a[p]) != tuple(b[p])]


ETAT_NUIT = os.path.join(DOSSIER, "convois_nuit.json")


def nuit(sh):
    """Répétition de nuit (TEST) : après 22h45 Paris, recalcule le plan du jour sur la copie privée TEST puis le compare à celui
    que la production a publié. Résumé dans « Requête »!E3. N'écrit JAMAIS en production."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Europe/Paris"))
    hm = (now.hour, now.minute)
    if not ((22, 45) <= hm < (23, 59)):
        return None
    jour = now.strftime("%Y-%m-%d")
    try:
        if json.load(open(ETAT_NUIT)).get("jour") == jour:
            return None
    except (OSError, ValueError):
        pass
    import importlib, sys
    for nom in ("ange_convois_nuit", "ange_convois", "ange_convois_ecr"):
        m = sys.modules.get(nom)
        if m:
            importlib.reload(m)
    import ange_convois_ecr as E
    json.dump({"jour": jour, "debut": time.strftime("%H:%M")}, open(ETAT_NUIT, "w"))      # une seule tentative par nuit
    res = E.ecrire_tout(jour, True)
    cmp = E.comparer()
    d = dict(cmp)
    txt = "NUIT TEST %s %s : plan %s lignes ; Convois %s identiques %s ; moyenne %s ; base %s ; dettes %s" % (
        jour, time.strftime("%H:%M"), dict(res).get("lignes du plan"), d.get("Convois test/prod"), d.get("Convois identiques"),
        d.get("TDC moyenne identique"), d.get("Base convois identique (hors cible)"), d.get("Dettes test/prod"))
    json.dump({"jour": jour, "fin": time.strftime("%H:%M"), "txt": txt}, open(ETAT_NUIT, "w"))
    try:
        sh.worksheet("Requête").update(range_name="E3", values=[[txt[:300]]])
    except Exception:
        pass
    return txt


ETAT_PROD = os.path.join(DOSSIER, "convois_veille_prod.json")


def veille_prod(sh):
    """Changements de membres/postes appliqués IMMÉDIATEMENT dans les fichiers de PRODUCTION (onglet masqué / supprimé / créé,
    livraisons conservées). Suspendu de 21h50 à 23h00 (clôture de 22h). Sauvegarde JSON avant toute écriture, alerte Discord."""
    import importlib, sys
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Europe/Paris"))
    hm = (now.hour, now.minute)
    if (21, 50) <= hm < (23, 0):
        return None
    for nom in ("ange_convois_nuit", "ange_convois", "ange_convois_live", "ange_convois_prod"):
        m = sys.modules.get(nom)
        if m:
            importlib.reload(m)
    import ange_convois as C, ange_convois_live as L, ange_convois_prod as PR
    jp = PR.jour_du_plan(now)
    if not PR.armement(jp):
        return None
    L.CIBLE = "prod"
    try:
        prive = PR.ouvrir_prod("prive", jp)
        membres = C.charger_prive(prive)[5]
        if len(membres) < 70:
            return "VEILLE PROD : seulement %d membres lus, rien fait" % len(membres)
        sig = signature(membres)
        try:
            etat_p = json.load(open(ETAT_PROD))
        except (OSError, ValueError):
            etat_p = {}
        ancien = etat_p.get("sig")
        if time.time() - etat_p.get("err", 0) < 300:
            return None                      # échec récent : on réessaie toutes les 5 min, pas chaque minute
        if ancien is not None and [tuple(x) for x in ancien] == [tuple(x) for x in sig]:
            return None
        msgs = []
        if ancien is not None:
            n, p, c = differences(ancien, sig)
            msgs.append("arrivés %s ; partis %s ; changés %s" % (n, p, c))
            fichiers = {cle: PR.ouvrir_prod(cle, jp) for cle in FICHIERS}
            try:
                PR.sauvegarder(prive, fichiers, "live_" + now.strftime("%Y%m%d_%H%M"))
            except Exception as ex:
                return "VEILLE PROD : sauvegarde impossible (%s), rien fait" % str(ex)[:120]
            try:
                for (ph, cle), r in L.synchroniser_tout(prive, True).items():
                    txt = ", ".join("%s=%s" % (k, v) for k, v in r if k in ("creer", "masquer", "montrer", "supprimer", "onglets à purger", "livraisons reprises") and v not in ("-", 0))
                    if txt:
                        msgs.append(cle + "/" + ph + ": " + txt)
            except Exception as ex:
                if time.time() - etat_p.get("err_alerte", 0) > 3600:
                    PR.alerter("⚠ VEILLE PROD : changement de chaîne NON appliqué (%s). Apps Script l'appliquera à :01 ; nouvel essai du VPS toutes les 5 min." % str(ex)[:300])
                    etat_p["err_alerte"] = time.time()
                etat_p["err"] = time.time()
                json.dump(etat_p, open(ETAT_PROD, "w"))
                return "VEILLE PROD ERREUR : " + str(ex)[:200]       # la signature n'est pas mise à jour : nouvelle tentative à la minute suivante
        json.dump({"sig": sig, "date": time.strftime("%Y-%m-%d %H:%M:%S")}, open(ETAT_PROD, "w"))
        txt = "VEILLE PROD : " + (" | ".join(msgs) if msgs else "première lecture")
        if ancien is not None:
            try:      # un changement de chaîne relance tout de suite l'écriture des pages (en-têtes, listes, annexes, alertes) au lieu d'attendre l'heure suivante
                fp = os.path.join(DOSSIER, "convois_pages_etat.json")
                pe = json.load(open(fp))
                pe.update({"ok_ver": "", "cle": "chgt", "essais": 0, "dernier": 0})
                json.dump(pe, open(fp, "w"))
            except Exception:
                pass
            PR.alerter("🔄 " + txt)
            try:
                sh.worksheet("Requête").update(range_name="E6", values=[[txt[:300]]])
            except Exception:
                pass
        return txt
    finally:
        L.CIBLE = "test"


def tick(_sh=None):
    try:   # clôture de minuit en PRODUCTION : n'agit que si elle a été armée explicitement (voir ange_convois_prod)
        import importlib, sys
        for nom in ("ange_convois_nuit", "ange_convois", "ange_convois_ecr", "ange_convois_prod"):
            m = sys.modules.get(nom)
            if m:
                importlib.reload(m)
        import ange_convois_prod as PR
        msg_p = PR.tick(_sh)
        if msg_p:
            print(msg_p, flush=True)
    except ImportError:
        pass
    except Exception as e:
        print("NUIT PROD erreur :", str(e)[:200], flush=True)
    try:   # liste des pseudos des formulaires (désarmé tant que convois_forms_mode.txt n'est pas prod)
        import importlib, sys
        m_fm = sys.modules.get("ange_convois_forms")
        if m_fm:
            importlib.reload(m_fm)
        import ange_convois_forms as FM
        msg_fm = FM.tick(_sh)
        if msg_fm:
            print(msg_fm, flush=True)
    except ImportError:
        pass
    except Exception as e:
        print("FORMULAIRES erreur :", str(e)[:200], flush=True)
    try:   # nouveaux entrants : hors chaîne (mais visibles) jusqu'au lendemain, pas de convoi dans le plan de la nuit suivante
        import importlib, sys
        m_ar = sys.modules.get("ange_convois_arrivants")
        if m_ar:
            importlib.reload(m_ar)
        import ange_convois_arrivants as AR
        msg_ar = AR.tick(_sh)
        if msg_ar:
            print(msg_ar, flush=True)
    except ImportError:
        pass
    except Exception as e:
        print("ARRIVANTS erreur :", str(e)[:200], flush=True)
    try:   # réponses aux formulaires (port de traiterReponsePoste) : off | observe | prod
        import importlib, sys
        m_rp = sys.modules.get("ange_convois_reponses")
        if m_rp:
            importlib.reload(m_rp)
        import ange_convois_reponses as RP
        msg_rp = RP.tick(_sh)
        if msg_rp:
            print(msg_rp, flush=True)
    except ImportError:
        pass
    except Exception as e:
        print("RÉPONSES erreur :", str(e)[:200], flush=True)
    try:   # TDC live (port de majTdcLive), désarmé tant que convois_tdclive_mode.txt n'est pas test/prod
        import importlib, sys
        m_tl = sys.modules.get("ange_convois_tdclive")
        if m_tl:
            importlib.reload(m_tl)
        import ange_convois_tdclive as TL
        msg_tl = TL.tick(_sh)
        if msg_tl:
            print(msg_tl, flush=True)
    except ImportError:
        pass
    except Exception as e:
        print("TDC LIVE erreur :", str(e)[:200], flush=True)
    try:   # listes d'attaque horaires (désarmées tant que convois_pages_mode.txt n'est pas test/prod)
        import importlib, sys
        m_pg = sys.modules.get("ange_convois_pages")
        if m_pg:
            importlib.reload(m_pg)
        import ange_convois_pages as PG
        msg_pg = PG.tick_horaire()
        if msg_pg:
            print(msg_pg, flush=True)
    except ImportError:
        pass
    except Exception as e:
        print("PAGES erreur :", str(e)[:200], flush=True)
    if mode() == "prod":
        try:
            msg_v = veille_prod(_sh)
            if msg_v:
                print(msg_v, flush=True)
            return msg_v
        except Exception as e:
            print("VEILLE PROD erreur :", str(e)[:200], flush=True)
            return None
    if mode() != "test":
        return None
    try:
        msg_n = nuit(_sh)
        if msg_n:
            print(msg_n, flush=True)
    except Exception as e:
        print("NUIT TEST erreur :", str(e)[:200], flush=True)
    import importlib, sys
    for nom in ("ange_convois_nuit", "ange_convois", "ange_convois_live"):   # le service ne redémarre pas : on recharge les modules mis à jour
        m = sys.modules.get(nom)
        if m:
            importlib.reload(m)
    import ange_convois as C, ange_convois_live as L
    prive = C.ouvrir_test("prive")
    membres = C.charger_prive(prive)[5]
    if not membres:
        return "VEILLE : aucun membre lu, rien fait"
    sig = signature(membres)
    try:
        ancien = json.load(open(ETAT)).get("sig")
    except (OSError, ValueError):
        ancien = None
    if ancien is not None and [tuple(x) for x in ancien] == [tuple(x) for x in sig]:
        return None
    msgs = []
    if ancien is not None:
        n, p, c = differences(ancien, sig)
        msgs.append("arrivés %s ; partis %s ; changés %s" % (n, p, c))
        for (ph, cle), r in L.synchroniser_tout(prive, True).items():
            txt = ", ".join("%s=%s" % (k, v) for k, v in r if k in ("creer", "masquer", "montrer", "supprimer", "onglets à purger", "livraisons reprises") and v not in ("-", 0))
            if txt:
                msgs.append(cle + "/" + ph + ": " + txt)
    json.dump({"sig": sig, "date": time.strftime("%Y-%m-%d %H:%M:%S")}, open(ETAT, "w"))
    return "VEILLE TEST : " + (" | ".join(msgs) if msgs else "première lecture")


def AUTOTEST():
    s1 = [("A", "Grenier 1", False, False), ("B", "Passeur 1", False, False)]
    s2 = [("A", "Grenier 1", True, False), ("C", "Chasseur", False, False)]
    assert differences(s1, s2) == (["C"], ["B"], ["A"])
    assert differences(None, s2)[0] == ["A", "C"]
    return True
# v2 relance pages apres changement
# v3 arrivants
