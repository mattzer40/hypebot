#!/usr/bin/env python3
"""
add_guild_emojis.py
Extrai emojis do bot.py e adiciona como emojis do servidor.
"""
import re, json, base64, time, os, asyncio
import urllib.request, urllib.error
import aiohttp

BOT_PY   = os.path.join(os.path.dirname(__file__), "bot.py")
GUILD_ID = "1480958892103176222"

HEADERS = {
    "User-Agent": "DiscordBot (https://github.com/Rapptz/discord.py, 2.3.2)",
    "X-Ratelimit-Precision": "millisecond",
}

def load_token():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    with open(env_path, "r") as f:
        for line in f:
            if line.startswith("DISCORD_TOKEN="):
                return line.split("=", 1)[1].strip()
    return None

def extract_emojis(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    static   = re.findall(r"<:([a-zA-Z0-9_]+):(\d+)>",  content)
    animated = re.findall(r"<a:([a-zA-Z0-9_]+):(\d+)>", content)
    seen = {}
    for name, eid in static + animated:
        if eid not in seen:
            seen[eid] = name
    return seen  # {id: name}

async def download_emoji(session, emoji_id):
    for ext in ("gif", "png"):
        url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    return await r.read(), ext
        except Exception:
            continue
    return None, None

async def get_existing_emojis(session, token, guild_id):
    url = f"https://discord.com/api/v10/guilds/{guild_id}/emojis"
    headers = {**HEADERS, "Authorization": f"Bot {token}"}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status == 200:
                return await r.json()
            body = await r.text()
            if r.status == 403:
                print("ERRO 403: Bot sem permissao 'Gerenciar Expressoes' no servidor.")
                print("Solucao: Configuracoes do Servidor > Cargos > cargo do bot > ative 'Gerenciar Expressoes'")
            else:
                print(f"Erro ao buscar emojis: HTTP {r.status} - {body[:200]}")
    except Exception as e:
        print(f"Erro de conexao: {e}")
    return []

async def upload_to_guild(session, token, guild_id, name, image_data, ext):
    safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", name)[:32]
    if len(safe_name) < 2:
        safe_name = "emoji_" + safe_name

    mime    = "image/gif" if ext == "gif" else "image/png"
    b64     = base64.b64encode(image_data).decode()
    payload = {"name": safe_name, "image": f"data:{mime};base64,{b64}"}
    headers = {**HEADERS, "Authorization": f"Bot {token}", "Content-Type": "application/json"}

    url = f"https://discord.com/api/v10/guilds/{guild_id}/emojis"

    for attempt in range(2):
        try:
            async with session.post(url, headers=headers, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=20)) as r:
                body = await r.text()
                if r.status == 201:
                    return json.loads(body)
                if r.status == 429:
                    data = json.loads(body)
                    wait = data.get("retry_after", 2)
                    print(f"  rate limit -> aguardando {wait:.1f}s", end=" ", flush=True)
                    await asyncio.sleep(float(wait) + 0.5)
                    continue
                return {"error": f"HTTP {r.status}: {body[:200]}"}
        except Exception as e:
            return {"error": str(e)}
    return {"error": "max retries"}

async def main():
    print("=" * 60)
    print("  ADICIONANDO EMOJIS AO SERVIDOR DA NATA")
    print("=" * 60)

    token = load_token()
    if not token:
        print("ERRO: Token nao encontrado no .env")
        return

    print(f"\nServidor: {GUILD_ID}")

    async with aiohttp.ClientSession() as session:
        existing = await get_existing_emojis(session, token, GUILD_ID)
        existing_names = {e["name"] for e in existing}
        print(f"Emojis ja no servidor: {len(existing)}")

        print("\nExtraindo emojis do bot.py...")
        emojis = extract_emojis(BOT_PY)
        print(f"{len(emojis)} emojis unicos encontrados.\n")

        ok, skip, errors = 0, 0, []

        for idx, (eid, name) in enumerate(emojis.items(), 1):
            safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", name)[:32]
            prefix = f"[{idx:>2}/{len(emojis)}] {name}"

            if safe_name in existing_names:
                print(f"{prefix} -> ja existe, pulando")
                skip += 1
                continue

            print(f"{prefix} -> baixando...", end=" ", flush=True)
            image_data, ext = await download_emoji(session, eid)
            if image_data is None:
                print("ERRO (imagem nao encontrada no CDN)")
                errors.append(name)
                continue

            print("enviando...", end=" ", flush=True)
            result = await upload_to_guild(session, token, GUILD_ID, name, image_data, ext)

            if "id" in result:
                print(f"OK (id: {result['id']})")
                ok += 1
            else:
                err = result.get("error", str(result))[:150]
                print(f"ERRO -> {err}")
                errors.append(name)

            await asyncio.sleep(0.5)

    print("\n" + "=" * 60)
    print(f"  Adicionados: {ok} | Ja existiam: {skip} | Erros: {len(errors)}")
    if errors:
        print(f"  Com erro: {errors}")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
