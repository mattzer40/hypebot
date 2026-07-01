"""
Copia todos os emojis do servidor antigo para o novo via Discord API.
Antigo: 1506247261011578890
Novo:   835695342291779645
"""
import urllib.request, urllib.error, json, base64, time, sys, os

OLD_GUILD = "1506247261011578890"
NEW_GUILD = "835695342291779645"

# Tenta tokens dos bots até achar um com acesso
TOKENS = [
    os.environ.get("NATA_SOURCE_TOKEN", ""),
]

def api(method, path, token, body=None):
    url = f"https://discord.com/api/v10{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method,
        headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {}

def find_token_with_access(guild_id):
    for tok in TOKENS:
        if not tok:
            continue
        status, _ = api("GET", f"/guilds/{guild_id}/emojis", tok)
        if status == 200:
            return tok
    return None

def main():
    print(f"Procurando token com acesso ao servidor antigo ({OLD_GUILD})...")
    src_token = find_token_with_access(OLD_GUILD)
    if not src_token:
        print("ERRO: nenhum bot tem acesso ao servidor antigo.")
        print("O bot pode ter sido removido do servidor antigo.")
        sys.exit(1)

    print(f"Procurando token com acesso ao servidor novo ({NEW_GUILD})...")
    dst_token = find_token_with_access(NEW_GUILD)
    if not dst_token:
        print("ERRO: nenhum bot tem acesso ao servidor novo.")
        sys.exit(1)

    # Lista emojis do servidor antigo
    _, emojis = api("GET", f"/guilds/{OLD_GUILD}/emojis", src_token)
    if not emojis:
        print("Nenhum emoji encontrado no servidor antigo.")
        return

    print(f"\n{len(emojis)} emojis encontrados. Copiando...")

    # Lista emojis já existentes no novo servidor para não duplicar
    _, existing = api("GET", f"/guilds/{NEW_GUILD}/emojis", dst_token)
    existing_names = {e["name"] for e in existing} if isinstance(existing, list) else set()

    ok = 0
    skip = 0
    fail = 0

    for emoji in emojis:
        name = emoji["name"]
        emoji_id = emoji["id"]
        animated = emoji.get("animated", False)
        ext = "gif" if animated else "png"
        url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}?size=128"

        if name in existing_names:
            print(f"  SKIP {name} (ja existe)")
            skip += 1
            continue

        # Baixa a imagem
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                img_data = r.read()
        except Exception as e:
            print(f"  ERRO download {name}: {e}")
            fail += 1
            continue

        # Codifica em base64 para a API
        mime = "image/gif" if animated else "image/png"
        b64 = base64.b64encode(img_data).decode()
        image_data = f"data:{mime};base64,{b64}"

        # Sobe no novo servidor
        status, resp = api("POST", f"/guilds/{NEW_GUILD}/emojis", dst_token,
                           body={"name": name, "image": image_data})

        if status in (200, 201):
            print(f"  OK  {name} {'(gif)' if animated else ''}")
            existing_names.add(name)
            ok += 1
        elif status == 429:
            # Rate limit
            retry = resp.get("retry_after", 1)
            print(f"  Rate limit — aguardando {retry:.1f}s...")
            time.sleep(float(retry) + 0.5)
            # Tenta de novo
            status, resp = api("POST", f"/guilds/{NEW_GUILD}/emojis", dst_token,
                               body={"name": name, "image": image_data})
            if status in (200, 201):
                print(f"  OK  {name} (retry)")
                ok += 1
            else:
                print(f"  ERRO {name}: {status} {resp.get('message','')}")
                fail += 1
        else:
            print(f"  ERRO {name}: {status} {resp.get('message','')}")
            fail += 1

        time.sleep(0.5)  # evita rate limit

    print(f"\nConcluido: {ok} copiados, {skip} pulados, {fail} erros.")

if __name__ == "__main__":
    main()
