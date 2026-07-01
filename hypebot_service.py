"""
HypeBot Service
===============
Mantém o dashboard e o túnel rodando 24/7.
Auto-reinicia se qualquer um cair.
"""
import subprocess
import time
import sys
import os

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
# pythonw.exe não abre janela de terminal
_py_dir   = os.path.dirname(sys.executable)
PYTHONW   = os.path.join(_py_dir, "pythonw.exe")
PYTHON    = PYTHONW if os.path.exists(PYTHONW) else sys.executable
SSH_KEY   = r"C:\Users\Anderson Chaves\.ssh\id_ed25519"

LOG_FILE = os.path.join(BASE_DIR, "service.log")

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def start_dashboard():
    log("Iniciando dashboard...")
    return subprocess.Popen(
        [PYTHON, os.path.join(BASE_DIR, "dashboard.py")],
        cwd=BASE_DIR,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

def run_tunnel():
    subprocess.run(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=999",
            "-o", "ExitOnForwardFailure=yes",
            "-i", SSH_KEY,
            "-R", "80:localhost:5500",
            "plan@localhost.run",
        ],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

# ── Start ──────────────────────────────────────────────────────────────────────
log("=== HypeBot Service iniciando ===")
log("URL: https://21473d90e2694b.lhr.life")

dash_proc = start_dashboard()
time.sleep(4)
log("Dashboard OK.")

# ── Loop principal ─────────────────────────────────────────────────────────────
while True:
    log("Conectando tunel SSH...")
    run_tunnel()
    log("Tunel desconectado. Aguardando 15s para reconectar...")

    # Verifica e reinicia o dashboard se tiver caído
    if dash_proc.poll() is not None:
        log("Dashboard caiu! Reiniciando...")
        dash_proc = start_dashboard()
        time.sleep(4)

    time.sleep(15)
