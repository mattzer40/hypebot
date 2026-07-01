import os, sys

# Lista /data
def list_dir(path, depth=0):
    if depth > 4:
        return
    try:
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            size = ""
            if os.path.isfile(full):
                try:
                    size = f" ({os.path.getsize(full)} bytes)"
                except:
                    pass
            print("  " * depth + name + size)
            if os.path.isdir(full):
                list_dir(full, depth + 1)
    except PermissionError as e:
        print("  " * depth + f"[sem permissão: {e}]")

print("=== /data ===")
list_dir("/data")
print("\n=== /app ===")
list_dir("/app")
sys.stdout.flush()
