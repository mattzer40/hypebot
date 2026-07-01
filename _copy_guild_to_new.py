"""
Copia TUDO das settings do servidor antigo para o novo em todos os bots.
Servidor antigo: 1506247261011578890 (teste bot)
Servidor novo:   835695342291779645  (teste hype)

Atualiza os SEED_BOT_SETTINGS_* no Railway para persistir apos restart.
"""
import urllib.request, json, gzip, base64, copy, subprocess, sys

DASHBOARD_URL    = "https://hypebot-production-62c1.up.railway.app"
DASHBOARD_SECRET = "66e8aa01984654baacbf83593587260bb6c69d9907818eb16cf233ffede10ec8"

OLD_GUILD = "1506247261011578890"
NEW_GUILD = "835695342291779645"

BOT_DIR = __file__[:__file__.rfind("\\")]


def railway_set(name: str, value: str) -> bool:
    ps_cmd = f'cd "{BOT_DIR}"; railway variables set "{name}={value}"'
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_cmd],
        capture_output=True, text=True
    )
    return result.returncode == 0


def main():
    print("Buscando settings de todos os bots...")
    url = f"{DASHBOARD_URL}/api/admin/backup-settings?secret={DASHBOARD_SECRET}"
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.loads(r.read())

    backups  = data.get("backups", {})
    prefixes = data.get("prefixes", {})

    updated = 0
    skipped = 0

    for bot_id, b64val in backups.items():
        raw     = gzip.decompress(base64.b64decode(b64val))
        settings = json.loads(raw)

        if OLD_GUILD not in settings:
            print(f"  SKIP bot {bot_id} — nao tem servidor antigo")
            skipped += 1
            continue

        old_cfg = copy.deepcopy(settings[OLD_GUILD])
        settings[NEW_GUILD] = old_cfg

        new_raw    = json.dumps(settings, ensure_ascii=False).encode("utf-8")
        new_b64    = base64.b64encode(gzip.compress(new_raw)).decode()

        var_name   = f"SEED_BOT_SETTINGS_{bot_id}"
        ok         = railway_set(var_name, new_b64)
        status     = "OK" if ok else "ERRO"
        print(f"  {status} bot {bot_id} — copiou {OLD_GUILD} -> {NEW_GUILD}")
        if ok:
            updated += 1

    print(f"\n{updated} bots atualizados, {skipped} pulados (nao tinham o servidor antigo).")
    print("\nAs configuracoes serao carregadas na proxima vez que o bot reiniciar.")
    print("Para aplicar agora: reinicie o servico no painel do Railway.")


if __name__ == "__main__":
    main()
