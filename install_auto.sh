#!/bin/bash
# Installation UNIQUE de la mise à jour automatique. À lancer depuis ~/ange_watch après avoir copié ange_requete.py
set -e
cd "$HOME/ange_watch"
[ -f ange_requete.py ] && [ -f service_account.json ] || { echo "ERREUR : ange_requete.py ou service_account.json manquant"; exit 1; }
venv/bin/python -m py_compile ange_requete.py
venv/bin/pip install -q --upgrade google-auth requests >/dev/null 2>&1 || true
sudo tee /etc/systemd/system/ange-requete.service >/dev/null <<EOF
[Unit]
Description=ANGE requetes Sheet + mise a jour du code
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$HOME/ange_watch
ExecStart=$HOME/ange_watch/venv/bin/python ange_requete.py --daemon
Restart=always
RestartSec=15
Nice=10
IOSchedulingClass=idle
MemoryMax=1200M
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$HOME/ange_watch
InaccessiblePaths=$HOME/.ssh
PrivateTmp=true
ProtectKernelModules=true
ProtectControlGroups=true

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable ange-requete >/dev/null 2>&1
sudo systemctl restart ange-requete
sleep 8
systemctl is-active ange-requete
journalctl -u ange-requete -n 8 --no-pager
