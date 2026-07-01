#!/usr/bin/env python3
"""Diagnóstico do autopostador — roda no Railway via: railway run python3 check_autopostador.py"""
import json, os
from pathlib import Path

data_dir = Path(os.environ.get("DATA_DIR", "/data"))
clients  = data_dir / "clientes"

if not clients.exists():
    print(f"[!] Diretório {clients} não encontrado")
    raise SystemExit(1)

found = False
for cdir in sorted(clients.iterdir()):
    sf = cdir / "bot_settings.json"
    if not sf.exists():
        continue
    try:
        s = json.loads(sf.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[!] {cdir.name}: erro ao ler settings: {e}")
        continue
    for gid, gs in s.items():
        if not isinstance(gs, dict):
            continue
        enabled = gs.get("gifs_autopostador_enabled", False)
        cats = {k: gs[k] for k in gs if k.startswith("gifs_autopostador_") and k not in ("gifs_autopostador_enabled","gifs_autopostador_interval","gifs_autopostador_last_posted","gifs_autopostador_sent") and gs.get(k)}
        if enabled or cats:
            found = True
            print(f"\nCliente: {cdir.name} | Guild: {gid}")
            print(f"  enabled : {enabled}")
            print(f"  canais  : {cats}")
            lp = gs.get("gifs_autopostador_last_posted", {})
            print(f"  last_posted: {lp}")

if not found:
    print("Nenhum autopostador configurado/ativo encontrado.")

# Verifica logs recentes
print("\n--- Últimas 20 linhas de cada bot.log ---")
for cdir in sorted(clients.iterdir()):
    lf = cdir / "bot.log"
    if lf.exists():
        lines = lf.read_text(encoding="utf-8", errors="replace").splitlines()
        print(f"\n=== {cdir.name} ===")
        for l in lines[-20:]:
            print(l)
