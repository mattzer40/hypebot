#!/usr/bin/env python3
"""
auto_guild_id.py
Detecta automaticamente o guild_id de cada cliente via Discord API
e atualiza o customers.json no Railway dashboard.
"""
import json, urllib.request, urllib.error, base64, struct, time, hmac, hashlib, os
from pathlib import Path

SECRET       = "66e8aa01984654baacbf83593587260bb6c69d9907818eb16cf233ffede10ec8"
BASE_URL     = "https://hypebot.up.railway.app"
EMOJI_GUILD  = 1506247261011578890   # servidor de emojis — ignorar
DISCORD_API  = "https://discord.com/api/v10"


# ── Forja sessão Flask ────────────────────────────────────────────────────────
def _flask_session_cookie(uid: str) -> str:
    """Cria cookie de sessão Flask válido sem precisar fazer login."""
    try:
        from itsdangerous import TimestampSigner
        payload = json.dumps({"uid": uid}, separators=(",", ":"))
        b64     = base64.b64encode(payload.encode()).decode().rstrip("=")
        signer  = TimestampSigner(SECRET, sep=".", key_derivation="hmac")
        return "session=" + signer.sign(b64).decode()
    except ImportError:
        print("Instalando itsdangerous...")
        os.system("pip install itsdangerous -q")
        from itsdangerous import TimestampSigner
        payload = json.dumps({"uid": uid}, separators=(",", ":"))
        b64     = base64.b64encode(payload.encode()).decode().rstrip("=")
        signer  = TimestampSigner(SECRET, sep=".", key_derivation="hmac")
        return "session=" + signer.sign(b64).decode()


# ── Chamadas ao dashboard ─────────────────────────────────────────────────────
def dashboard_get(path: str, cookie: str):
    req = urllib.request.Request(BASE_URL + path, headers={"Cookie": cookie})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def dashboard_put(path: str, data: dict, cookie: str):
    body = json.dumps(data).encode()
    req  = urllib.request.Request(
        BASE_URL + path, data=body,
        headers={"Cookie": cookie, "Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


# ── Discord API ───────────────────────────────────────────────────────────────
def get_bot_guilds(token: str) -> list[dict]:
    """Retorna lista de guilds que o bot está."""
    req = urllib.request.Request(
        DISCORD_API + "/users/@me/guilds",
        headers={"Authorization": f"Bot {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"    ⚠️  Discord API erro {e.code}: token inválido?")
        return []


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  AUTO GUILD ID — detecta servidor de cada cliente")
    print("=" * 60)

    # Cria sessão admin
    cookie = _flask_session_cookie("mtzerak")

    # Busca clientes do dashboard
    print("\nBuscando clientes no dashboard...")
    try:
        resp = dashboard_get("/api/customers", cookie)
    except Exception as e:
        print(f"❌ Não consegui acessar o dashboard: {e}")
        print("   Verifique se https://hypebot.up.railway.app está online.")
        return

    customers = resp if isinstance(resp, list) else resp.get("customers", [])
    if not customers:
        print("❌ Nenhum cliente encontrado.")
        return

    print(f"  {len(customers)} cliente(s) encontrado(s).\n")

    for c in customers:
        cid   = c["id"]
        nome  = c.get("nome", cid)
        token = c.get("token", "").strip()

        print(f"[{nome}] ({cid})")

        if not token or "TOKEN_DO_BOT" in token:
            print("    ⏭️  Sem token válido — pulando.\n")
            continue

        # Se já tem guild_id configurado, pula
        if c.get("guild_id"):
            print(f"    ✅ guild_id já configurado: {c['guild_id']}\n")
            continue

        # Busca guilds via Discord API
        print("    🔍 Buscando servidores via Discord API...")
        guilds = get_bot_guilds(token)

        if not guilds:
            print("    ❌ Sem guilds ou token inválido.\n")
            continue

        # Filtra servidor de emojis
        valid = [g for g in guilds if int(g["id"]) != EMOJI_GUILD]

        if not valid:
            print(f"    ⚠️  Bot só está no servidor de emojis — adicione-o ao servidor do cliente primeiro.\n")
            continue

        # Mostra opções se houver mais de um servidor
        if len(valid) == 1:
            chosen = valid[0]
            print(f"    🎯 Servidor detectado: {chosen['name']} ({chosen['id']})")
        else:
            print(f"    📋 Múltiplos servidores encontrados:")
            for i, g in enumerate(valid):
                print(f"       [{i}] {g['name']} ({g['id']})")
            idx = input(f"    Escolha o índice para {nome}: ").strip()
            try:
                chosen = valid[int(idx)]
            except (ValueError, IndexError):
                print("    ❌ Índice inválido — pulando.\n")
                continue

        guild_id = chosen["id"]

        # Atualiza no dashboard
        print(f"    💾 Salvando guild_id={guild_id}...")
        try:
            result = dashboard_put(f"/api/customers/{cid}", {
                "nome":     c.get("nome", ""),
                "token":    token,
                "app_id":   c.get("app_id", ""),
                "guild_id": guild_id,
                "expira":   c.get("expira", ""),
                "ativo":    c.get("ativo", True),
            }, cookie)
            if result.get("ok"):
                print(f"    ✅ Atualizado! Bot restringido ao servidor {chosen['name']}\n")
            else:
                print(f"    ❌ Erro ao salvar: {result}\n")
        except Exception as e:
            print(f"    ❌ Erro HTTP: {e}\n")

        time.sleep(0.5)

    print("\n" + "=" * 60)
    print("  CONCLUÍDO! Os bots agora só respondem nos servidores")
    print("  configurados. Reinicie os bots no dashboard se necessário.")
    print("=" * 60)


if __name__ == "__main__":
    main()
