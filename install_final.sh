#!/bin/bash
# Installation finale : service de requêtes (systemd) + vérifications. À lancer une seule fois sur le VPS.
set -e
cd "$HOME/ange_watch"
echo "== 1. Fichiers présents ?"
for f in ange_watch.py ange_requete.py ange_analyse.py ange_watch.ini service_account.json tdc_serveur.db; do
  [ -e "$f" ] && echo "   ok  $f" || { echo "   MANQUE $f"; exit 1; }
done
venv/bin/python -m py_compile ange_watch.py ange_requete.py ange_analyse.py && echo "   syntaxe ok"
echo "== 2. Ancien cron de requêtes supprimé, cron horaire conservé"
( crontab -l 2>/dev/null | grep -v ange_requete ) | crontab - || true
crontab -l | grep ange_watch.py >/dev/null && echo "   cron horaire ange_watch present" || {
  (crontab -l 2>/dev/null; echo "10 * * * * cd $HOME/ange_watch && venv/bin/python ange_watch.py >> log.txt 2>&1") | crontab -
  echo "   cron horaire ange_watch ajoute"; }
echo "== 3. Service systemd"
sudo tee /etc/systemd/system/ange-requete.service >/dev/null <<EOF
[Unit]
Description=ANGE : requetes sur la base TDC depuis le Google Sheet
After=network-online.target
Wants=network-online.target
[Service]
User=$USER
WorkingDirectory=$HOME/ange_watch
ExecStart=$HOME/ange_watch/venv/bin/python $HOME/ange_watch/ange_requete.py --daemon
Restart=always
RestartSec=15
Nice=10
IOSchedulingClass=idle
MemoryMax=1200M
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now ange-requete
sleep 8
systemctl is-active ange-requete
echo "== 4. Derniers messages du service"
journalctl -u ange-requete -n 8 --no-pager
echo "== Termine. Cron :"
crontab -l
