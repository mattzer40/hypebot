#!/usr/bin/env python3
"""
Verifica quais bots do customers.json NÃO estão em um servidor Discord
e os remove automaticamente.

Uso:
    python clean_guild.py <guild_id>

Exemplo:
    python clean_guild.py 1506247261011578890
"""

import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

DISCORD_API = "https://discord.com/api/v10"
DATA_DIR    = Path(__file__).parent


def check_bot_in_guild(token: str, guild_id: str) -> tuple[bool, str]:
    """Retorna (esta_no_servidor, motivo)."""
    try:
        req = urllib.request.Request(
            f"{DISCORD_API}/users/@me/guilds",
            headers={"Authorization": f"Bot {token}", "User-Agent": "HypeBot/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            guilds = json.loads(r.read())
        ids = {str(g["id"]) for g in guilds}
        if guild_id in ids:
            return True, "OK"
        return False, "não está no servidor"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "token inválido (401)"
        return False, f"erro Discord {e.code}"
    except Exception as ex:
        return False, f"erro: {str(ex)[:60]}"


def main():
    if len(sys.argv) < 2:
        print("Uso: python clean_guild.py <guild_id>")
        sys.exit(1)

    target_guild = sys.argv[1].strip()
    if not target_guild.isdigit():
        print("❌ guild_id deve ser numérico")
        sys.exit(1)

    # Carrega customers.json do volume (DATA_DIR) se existir, senão do código
    customers_file = Path(
        __import__("os").environ.get("DATA_DIR", str(DATA_DIR))
    ) / "customers.json"

    if not customers_file.exists():
        print(f"❌ {customers_file} não encontrado")
        sys.exit(1)

    customers = json.loads(customers_file.read_text(encoding="utf-8"))
    print(f"\n📋 {len(customers)} cliente(s) carregado(s) de {customers_file}\n")

    in_guild     = []
    not_in_guild = []

    for c in customers:
        cid   = c["id"]
        nome  = c.get("nome", cid)
        token = c.get("token", "").strip()

        if not token or token == "TOKEN_DO_BOT_AQUI":
            print(f"  ⚠️  {nome} ({cid}) — sem token, será removido")
            not_in_guild.append(c)
            continue

        ok, reason = check_bot_in_guild(token, target_guild)
        if ok:
            print(f"  ✅ {nome} ({cid}) — no servidor")
            in_guild.append(c)
        else:
            print(f"  ❌ {nome} ({cid}) — {reason}")
            not_in_guild.append(c)

    print(f"\n{'='*50}")
    print(f"✅ No servidor:   {len(in_guild)}")
    print(f"❌ Fora/inválido: {len(not_in_guild)}")

    if not not_in_guild:
        print("\nNada para remover. ✓")
        return

    print(f"\n{'='*50}")
    print("Serão REMOVIDOS do customers.json:")
    for c in not_in_guild:
        print(f"  🗑️  {c.get('nome', c['id'])} ({c['id']})")

    confirm = input("\nConfirmar exclusão? [s/N]: ").strip().lower()
    if confirm != "s":
        print("Cancelado.")
        return

    customers_file.write_text(
        json.dumps(in_guild, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n✅ customers.json atualizado — {len(not_in_guild)} removido(s), {len(in_guild)} mantido(s).")
    print("Reinicie o dashboard para aplicar as mudanças.")


if __name__ == "__main__":
    main()
