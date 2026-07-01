"""Backup de configs de todos os bots para env vars do Railway via CLI PowerShell."""
import urllib.request, json, subprocess, sys

DASHBOARD_URL    = "https://hypebot-production-62c1.up.railway.app"
DASHBOARD_SECRET = "66e8aa01984654baacbf83593587260bb6c69d9907818eb16cf233ffede10ec8"

def railway_set_ps(name: str, value: str) -> bool:
    """Usa PowerShell para contornar limite de linha do cmd.exe no Windows."""
    ps_cmd = f'cd "{__file__[:__file__.rfind(chr(92))]}"; railway variables set "{name}={value}"'
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_cmd],
        capture_output=True, text=True
    )
    return result.returncode == 0

def main():
    print("Buscando configs do volume...")
    url = f"{DASHBOARD_URL}/api/admin/backup-settings?secret={DASHBOARD_SECRET}"
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.loads(r.read())

    backups  = data.get("backups", {})
    prefixes = data.get("prefixes", {})
    errors   = 0

    for bot_id, b64val in backups.items():
        ok = railway_set_ps(f"SEED_BOT_SETTINGS_{bot_id}", b64val)
        print(f"  {'OK' if ok else 'ERRO'} settings {bot_id} ({len(b64val)} chars)")
        if not ok: errors += 1

    for bot_id, prefix in prefixes.items():
        if not prefix: continue
        ok = railway_set_ps(f"SEED_BOT_PREFIX_{bot_id}", prefix)
        print(f"  {'OK' if ok else 'ERRO'} prefix  {bot_id} = {prefix}")
        if not ok: errors += 1

    if errors:
        print(f"\n{errors} erro(s) no backup.")
        sys.exit(1)
    else:
        print(f"\nBackup completo: {len(backups)} bots, {len(prefixes)} prefixos.")

if __name__ == "__main__":
    main()
