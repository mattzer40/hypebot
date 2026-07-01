"""
Deploy seguro: faz backup dos configs no Railway antes de subir o código.
Uso: python deploy.py
"""
import urllib.request, json, subprocess, sys, os

DASHBOARD_URL = "https://hypebot-production-62c1.up.railway.app"
SECRET = os.environ.get(
    "DASHBOARD_SECRET",
    "66e8aa01984654baacbf83593587260bb6c69d9907818eb16cf233ffede10ec8"
)

def _ps_set(name: str, value: str) -> bool:
    """Define uma env var no Railway via PowerShell (sem limite de linha do cmd)."""
    bot_dir = os.path.dirname(os.path.abspath(__file__))
    ps_cmd = f'cd "{bot_dir}"; railway variables set "{name}={value}"'
    r = subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-NoProfile", "-Command", ps_cmd],
                       capture_output=True, text=True)
    return r.returncode == 0

def backup_and_update_seeds() -> bool:
    print("[deploy] Buscando configs atuais do volume...")
    url = f"{DASHBOARD_URL}/api/admin/backup-settings?secret={SECRET}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"[deploy] ERRO ao buscar backup: {e}")
        return False

    backups  = data.get("backups", {})
    prefixes = data.get("prefixes", {})

    if not backups:
        print("[deploy] Nenhum bot encontrado no volume — pulando backup.")
        return True

    errors = 0
    for bot_id, b64val in backups.items():
        ok = _ps_set(f"SEED_BOT_SETTINGS_{bot_id}", b64val)
        print(f"  {'OK' if ok else 'ERRO'} settings {bot_id} ({len(b64val)} chars)")
        if not ok: errors += 1

    for bot_id, prefix in prefixes.items():
        if not prefix: continue
        ok = _ps_set(f"SEED_BOT_PREFIX_{bot_id}", prefix)
        print(f"  {'OK' if ok else 'ERRO'} prefix  {bot_id} = {prefix}")
        if not ok: errors += 1

    if errors:
        print(f"[deploy] {errors} erro(s) no backup.")
        return False
    print(f"[deploy] Backup atualizado: {list(backups.keys())}")
    return True

def deploy():
    print("[deploy] Iniciando deploy seguro...")
    if not backup_and_update_seeds():
        resp = input("[deploy] Falha no backup. Continuar mesmo assim? (s/N): ")
        if resp.strip().lower() != "s":
            print("[deploy] Deploy cancelado.")
            sys.exit(1)

    print("[deploy] Subindo código...")
    r = subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-NoProfile", "-Command", "railway up --detach"])
    if r.returncode != 0:
        print("[deploy] ERRO no deploy.")
        sys.exit(1)
    print("[deploy] Deploy enviado com sucesso!")

if __name__ == "__main__":
    deploy()
