import os, glob, sys

log_dirs = glob.glob("/data/clientes/*/bot.log")
if not log_dirs:
    print("Nenhum log encontrado em /data/clientes/")
    sys.exit(0)

for path in sorted(log_dirs):
    print(f"\n=== {path} ===")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        print("".join(lines[-25:]))
    except Exception as e:
        print(f"ERRO: {e}")

sys.stdout.flush()
