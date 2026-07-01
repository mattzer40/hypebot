#!/usr/bin/env python3
"""
migrate_emojis.py
Extrai emojis do bot.py, baixa do CDN e sobe como Application Emojis.
Uso: python migrate_emojis.py
"""
import re, json, base64, time, sys, os
import urllib.request, urllib.error

BOT_PY = os.path.join(os.path.dirname(__file__), "bot.py")


# ── 1. Extrai emojis únicos ─────────────────────────────────────────────────
def extract_emojis(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    matches = re.findall(r"<:([a-zA-Z0-9_]+):(\d+)>", content)
    seen = {}
    for name, eid in matches:
        if eid not in seen:
            seen[eid] = name
    return seen  # {old_id: name}


# ── 2. Baixa imagem do CDN ───────────────────────────────────────────────────
def download_emoji(emoji_id):
    for ext in ("png", "gif"):
        url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.read(), ext
        except Exception:
            continue
    return None, None


# ── 3. Sobe como Application Emoji ──────────────────────────────────────────
def upload_emoji(token, app_id, name, image_data, ext):
    # Nome deve ter 2-32 chars, só letras/números/underscore
    safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", name)[:32]
    if len(safe_name) < 2:
        safe_name = "emoji_" + safe_name

    mime = "image/gif" if ext == "gif" else "image/png"
    b64  = base64.b64encode(image_data).decode()
    payload = json.dumps({"name": safe_name, "image": f"data:{mime};base64,{b64}"}).encode()

    req = urllib.request.Request(
        f"https://discord.com/api/v10/applications/{app_id}/emojis",
        data=payload,
        headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        # 429 Rate limit
        if e.code == 429:
            retry = json.loads(body).get("retry_after", 2)
            print(f"rate limit → aguardando {retry}s")
            time.sleep(float(retry) + 0.5)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        return {"error": f"HTTP {e.code}: {body[:200]}"}


# ── 4. Atualiza bot.py ───────────────────────────────────────────────────────
def update_bot_py(mapping):
    with open(BOT_PY, "r", encoding="utf-8") as f:
        content = f.read()
    for old_id, new_id in mapping.items():
        content = content.replace(old_id, new_id)
    with open(BOT_PY, "w", encoding="utf-8") as f:
        f.write(content)


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  MIGRADOR DE EMOJIS — Application Emojis do Discord")
    print("=" * 60)
    print()

    token  = input("1) Cole o TOKEN do bot (Bot > Token no portal): ").strip()
    app_id = input("2) Cole o APPLICATION ID do bot (Informações Gerais): ").strip()
    print()

    if not token or not app_id:
        print("❌ Token ou App ID vazio. Abortando.")
        sys.exit(1)

    # Verifica quantos emojis o app já tem (limite: 2000)
    req = urllib.request.Request(
        f"https://discord.com/api/v10/applications/{app_id}/emojis",
        headers={"Authorization": f"Bot {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            existing = json.loads(r.read()).get("items", [])
        existing_names = {e["name"] for e in existing}
        existing_ids   = {e["name"]: e["id"] for e in existing}
        print(f"  Bot já tem {len(existing)} application emojis cadastrados.")
    except Exception as e:
        print(f"❌ Não consegui verificar emojis existentes: {e}")
        existing_names, existing_ids = set(), {}

    print("\nExtraindo emojis do bot.py...")
    emojis = extract_emojis(BOT_PY)
    print(f"  {len(emojis)} emojis únicos encontrados.\n")

    mapping = {}   # old_id → new_id
    errors  = []

    for idx, (old_id, name) in enumerate(emojis.items(), 1):
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", name)[:32]
        prefix    = f"[{idx:>3}/{len(emojis)}] {name}:{old_id}"

        # Já existe no app?
        if safe_name in existing_names:
            new_id = existing_ids[safe_name]
            mapping[old_id] = new_id
            print(f"{prefix} → já existe ({new_id}) ✓")
            continue

        print(f"{prefix} → baixando...", end=" ", flush=True)
        image_data, ext = download_emoji(old_id)
        if image_data is None:
            print("ERRO (sem imagem no CDN)")
            errors.append(old_id)
            continue

        print("subindo...", end=" ", flush=True)
        result = upload_emoji(token, app_id, name, image_data, ext)

        if "id" in result:
            new_id = result["id"]
            mapping[old_id] = new_id
            print(f"OK → {new_id} ✓")
        else:
            print(f"ERRO → {result.get('error', result)[:120]}")
            errors.append(old_id)

        time.sleep(0.4)  # evita rate limit

    # Salva mapping
    mapping_path = os.path.join(os.path.dirname(__file__), "emoji_mapping.json")
    with open(mapping_path, "w") as f:
        json.dump(mapping, f, indent=2)
    print(f"\n📄 Mapping salvo em emoji_mapping.json ({len(mapping)} emojis migrados)")

    if errors:
        print(f"⚠️  {len(errors)} erros: {errors}")

    if not mapping:
        print("\n❌ Nenhum emoji migrado. Verifique o token e o app_id.")
        return

    # Atualiza bot.py
    print("\nAtualizando bot.py com os novos IDs...")
    update_bot_py(mapping)
    print("✅ bot.py atualizado!")
    print()
    print("=" * 60)
    print("  PRÓXIMO PASSO: faça deploy no Railway")
    print("  Os emojis agora pertencem ao próprio bot —")
    print("  funcionam em qualquer servidor sem depender de")
    print("  nenhum servidor externo.")
    print("=" * 60)


if __name__ == "__main__":
    main()
