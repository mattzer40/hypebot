import urllib.request, json, gzip, base64, time, sys

url = "https://hypebot-production-62c1.up.railway.app/api/admin/backup-settings?secret=66e8aa01984654baacbf83593587260bb6c69d9907818eb16cf233ffede10ec8"

try:
    with urllib.request.urlopen(url, timeout=20) as r:
        data = json.loads(r.read())
    backups = data.get("backups", {})
    prefixes = data.get("prefixes", {})

    print("=== Configs no volume apos deploy ===")
    for bot_id, b64 in backups.items():
        raw = gzip.decompress(base64.b64decode(b64))
        settings = json.loads(raw)
        for guild_id, cfg in settings.items():
            has_protecao = cfg.get("protecao_cargos_enabled", False)
            if has_protecao:
                print(f"  Bot {bot_id} guild {guild_id}: protecao_cargos ATIVO")
        print(f"  Bot {bot_id}: {len(raw)} bytes, {len(settings)} servidores")

    print("Prefixos:", {k: v for k, v in prefixes.items()})
    print("TOTAL bots com settings:", len(backups))
except Exception as e:
    print(f"ERRO: {e}", file=sys.stderr)
    sys.exit(1)
