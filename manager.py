"""
NATA® Bot Manager
=================
Gerencia múltiplas instâncias do bot — uma por cliente.
Cada cliente tem seu próprio token, configurações e pasta isolada.

Uso:
    python manager.py              # inicia todos os clientes ativos
    python manager.py --list       # lista clientes e status
    python manager.py --add        # adiciona novo cliente interativamente
    python manager.py --stop ID    # para a instância de um cliente
"""

import json
import os
import sys
import subprocess
import time
import signal
import shutil
from datetime import date, datetime
from pathlib import Path

# ── Caminhos ─────────────────────────────────────────────────────────────────
CODE_DIR       = Path(__file__).parent
DATA_DIR       = Path(os.environ.get("DATA_DIR", str(CODE_DIR)))
DATA_DIR.mkdir(exist_ok=True)
BOT_SCRIPT     = CODE_DIR / "bot.py"
CLIENTS_DIR    = DATA_DIR / "clientes"          # no volume persistente

# customers.json fica no volume (persiste entre deploys).
# Na 1ª execução ou se estiver ausente, copia do código como seed.
CUSTOMERS_FILE      = DATA_DIR / "customers.json"
_CUSTOMERS_SEED     = CODE_DIR / "customers.json"
_force_seed = os.environ.get("FORCE_SEED_CUSTOMERS", "0") == "1"
_file_empty = CUSTOMERS_FILE.exists() and CUSTOMERS_FILE.stat().st_size <= 4
_seed_customers_set = bool(os.environ.get("SEED_CUSTOMERS", "").strip())
if not _seed_customers_set and (_force_seed or not CUSTOMERS_FILE.exists() or _file_empty) and _CUSTOMERS_SEED.exists():
    import shutil as _sh
    _sh.copy2(_CUSTOMERS_SEED, CUSTOMERS_FILE)
    print(f"[manager] customers.json copiado do código → volume ({CUSTOMERS_FILE})", flush=True)
DEFAULT_AVATAR = CODE_DIR / "default_avatar.png"
# Fallback: se não existir em /app/ (ex: não foi incluído no deploy), usa o volume persistente
if not DEFAULT_AVATAR.exists():
    DEFAULT_AVATAR = DATA_DIR / "default_avatar.png"

CLIENTS_DIR.mkdir(exist_ok=True)

# ── Seed bot_settings.json por bot via env var ────────────────────────────────
# SEED_BOT_SETTINGS_<bot_id>=<base64>  → restaura/mescla se ausente
# FORCE_SEED_BOT_SETTINGS_<bot_id>=1  → força sobrescrita total mesmo se existir
import base64 as _b64_bs, gzip as _gzip_bs, json as _json_bs
for _env_key, _env_val in os.environ.items():
    if not _env_key.startswith("SEED_BOT_SETTINGS_"):
        continue
    _bot_id = _env_key[len("SEED_BOT_SETTINGS_"):]
    _bot_dir = CLIENTS_DIR / _bot_id
    _settings_path = _bot_dir / "bot_settings.json"
    _force_bs = os.environ.get(f"FORCE_SEED_BOT_SETTINGS_{_bot_id}", "0") == "1"
    try:
        _raw = _b64_bs.b64decode(_env_val.strip())
        if _raw[:2] == b'\x1f\x8b':
            _content = _gzip_bs.decompress(_raw).decode("utf-8")
        else:
            _content = _raw.decode("utf-8")
        _seed_data = _json_bs.loads(_content)
        _bot_dir.mkdir(parents=True, exist_ok=True)

        if _force_bs:
            # Força substituição total — só usar em emergência
            _settings_path.write_text(_content, encoding="utf-8")
            print(f"[manager] bot_settings.json FORÇADO para '{_bot_id}'", flush=True)
        elif not _settings_path.exists():
            # Volume limpo: restaura do SEED
            _settings_path.write_text(_content, encoding="utf-8")
            print(f"[manager] bot_settings.json restaurado do SEED para '{_bot_id}'", flush=True)
        else:
            # Volume intacto: mescla — configurações do cliente têm prioridade
            # O SEED só adiciona guilds/chaves que estejam faltando no volume
            _existing = _json_bs.loads(_settings_path.read_text(encoding="utf-8"))
            _changed = False
            for _gid, _seed_guild in _seed_data.items():
                if _gid not in _existing:
                    _existing[_gid] = _seed_guild
                    _changed = True
                else:
                    for _k, _v in _seed_guild.items():
                        if _k not in _existing[_gid]:
                            _existing[_gid][_k] = _v
                            _changed = True
            if _changed:
                _settings_path.write_text(
                    _json_bs.dumps(_existing, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
                print(f"[manager] bot_settings.json mesclado (novas chaves) para '{_bot_id}'", flush=True)
    except Exception as _e_bs:
        print(f"[manager] erro ao restaurar bot_settings.json para '{_bot_id}': {_e_bs}", flush=True)

# ── Seed bot_prefix.txt por bot via env var ──────────────────────────────────
# SEED_BOT_PREFIX_<bot_id>=<prefixo>  → restaura bot_prefix.txt se ausente
for _env_key, _env_val in os.environ.items():
    if not _env_key.startswith("SEED_BOT_PREFIX_"):
        continue
    _bot_id = _env_key[len("SEED_BOT_PREFIX_"):]
    _bot_dir = CLIENTS_DIR / _bot_id
    _prefix_path = _bot_dir / "bot_prefix.txt"
    if _prefix_path.exists():
        continue
    try:
        _bot_dir.mkdir(parents=True, exist_ok=True)
        _prefix_path.write_text(_env_val.strip(), encoding="utf-8")
        print(f"[manager] bot_prefix.txt restaurado para '{_bot_id}': {_env_val.strip()}", flush=True)
    except Exception as _e_pfx:
        print(f"[manager] erro ao restaurar bot_prefix.txt para '{_bot_id}': {_e_pfx}", flush=True)

# ── Migração: copia settings da pasta de código para o volume na 1ª vez ───────
_code_clients = CODE_DIR / "clientes"
if _code_clients.exists() and DATA_DIR != CODE_DIR:
    for _src in _code_clients.iterdir():
        if _src.is_dir():
            _dst = CLIENTS_DIR / _src.name
            _dst.mkdir(exist_ok=True)
            for _f in ("bot_settings.json", "bot_prefix.txt", ".env"):
                _fsrc = _src / _f
                _fdst = _dst / _f
                if _fsrc.exists() and not _fdst.exists():
                    shutil.copy2(_fsrc, _fdst)

# ── Estado em memória ─────────────────────────────────────────────────────────
_processes: dict[str, subprocess.Popen] = {}


def load_customers() -> list[dict]:
    if not CUSTOMERS_FILE.exists():
        return []
    with open(CUSTOMERS_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_customers(customers: list[dict]) -> None:
    with open(CUSTOMERS_FILE, "w", encoding="utf-8") as f:
        json.dump(customers, f, ensure_ascii=False, indent=2)


def is_expired(customer: dict) -> bool:
    try:
        exp = datetime.strptime(customer.get("expira", ""), "%Y-%m-%d").date()
        return exp < date.today()
    except ValueError:
        return False


def customer_dir(cid: str) -> Path:
    d = CLIENTS_DIR / cid
    d.mkdir(exist_ok=True)
    return d


def start_instance(customer: dict) -> subprocess.Popen | None:
    cid   = customer["id"]
    token = customer.get("token", "").strip()

    if not token or token == "TOKEN_DO_BOT_AQUI":
        print(f"  [AVISO] Cliente '{cid}' sem token configurado — pulando.")
        return None

    if is_expired(customer):
        print(f"  [AVISO] Cliente '{cid}' expirado em {customer.get('expira')} — pulando.")
        return None

    cdir        = customer_dir(cid)
    env_file    = cdir / ".env"
    settings_file = cdir / "bot_settings.json"

    # Sempre atualiza o .env com o token mais recente do customers.json
    env_file.write_text(f"DISCORD_TOKEN={token}\n", encoding="utf-8")


    # Copia avatar padrão para a pasta do cliente
    # (se existir e o cliente não tiver ou tiver arquivo vazio/inválido)
    client_avatar = cdir / "default_avatar.png"
    _avatar_missing = not client_avatar.exists() or client_avatar.stat().st_size == 0
    if DEFAULT_AVATAR.exists() and _avatar_missing:
        shutil.copy(DEFAULT_AVATAR, client_avatar)

    # Variáveis de ambiente para essa instância
    env = os.environ.copy()
    env["BOT_ENV_FILE"]      = str(env_file)
    env["BOT_SETTINGS_FILE"] = str(settings_file)
    env["BOT_PREFIX_FILE"]   = str(cdir / "bot_prefix.txt")
    env["BOT_AVATAR_FILE"]   = str(client_avatar)
    env["BOT_BANNER_FILE"]   = str(cdir / "bot_banner.png")
    env["BOT_EXPIRA"]        = str(customer.get("expira", "") or "")
    guild_id = str(customer.get("guild_id", "") or "").strip()
    if guild_id:
        env["BOT_GUILD_ID"] = guild_id

    log_file = open(cdir / "bot.log", "a", encoding="utf-8")
    log_file.write(f"\n{'='*50}\n[{datetime.now()}] Iniciando instância '{cid}'\n")

    proc = subprocess.Popen(
        [sys.executable, str(BOT_SCRIPT)],
        env=env,
        stdout=None,    # herda stdout do manager → aparece nos logs do Railway
        stderr=None,    # stderr também para stdout → visível nos logs do Railway
        cwd=str(CODE_DIR),
    )
    print(f"  ✅ '{cid}' ({customer.get('nome', '')}) iniciado — PID {proc.pid}")
    return proc


def run_all() -> None:
    customers = load_customers()
    if not customers:
        print("Nenhum cliente cadastrado em customers.json")
        return

    print(f"\n🚀 Iniciando {len(customers)} cliente(s)...\n")
    _tokens_vistos: set[str] = set()
    for c in customers:
        if not c.get("ativo", True):
            print(f"  ⏸️  '{c['id']}' desativado — pulando.")
            continue
        _tok = (c.get("token") or "").strip()
        if _tok and _tok in _tokens_vistos:
            print(f"  ⚠️  '{c['id']}' tem token duplicado — pulando para evitar conflito.")
            continue
        if _tok:
            _tokens_vistos.add(_tok)
        proc = start_instance(c)
        if proc:
            _processes[c["id"]] = proc

    print(f"\n✅ {len(_processes)} instância(s) rodando. Monitorando...\n")

    # ── Backoff por cliente: evita loop de reconexões que viola ToS do Discord ──
    # restart_count[cid]    → quantas vezes crashou consecutivamente
    # next_restart_at[cid]  → timestamp mínimo para próxima tentativa
    restart_count:    dict[str, int]   = {cid: 0 for cid in _processes}
    next_restart_at:  dict[str, float] = {cid: 0 for cid in _processes}
    # Clientes desativados por excesso de crashes — não reiniciar automaticamente
    disabled_by_crashes: set[str] = set()

    # Backoff: 30s → 60s → 120s → 300s → 600s → 1800s → parar após 8 falhas
    BACKOFF_DELAYS = [30, 60, 120, 300, 600, 1800, 1800]
    MAX_CRASHES    = 8   # desativa o cliente após 8 crashes consecutivos

    # Loop de monitoramento — reinicia com backoff exponencial
    try:
        while True:
            time.sleep(15)
            now = time.time()
            # Verifica se algum cliente expirado foi renovado no dashboard
            # Só reinicia se NÃO foi desativado por crashes (evita loop infinito)
            for _c in load_customers():
                _cid = _c["id"]
                if (_cid not in _processes
                        and _cid not in disabled_by_crashes
                        and _c.get("ativo")
                        and not is_expired(_c)):
                    _tok = (_c.get("token") or "").strip()
                    if _tok and _tok != "TOKEN_DO_BOT_AQUI":
                        print(f"  🟢 '{_cid}' reativado — iniciando...", flush=True)
                        _np = start_instance(_c)
                        if _np:
                            _np._start_ts = time.time()
                            _processes[_cid]      = _np
                            restart_count[_cid]   = 0
                            next_restart_at[_cid] = 0

            for cid, proc in list(_processes.items()):
                ret = proc.poll()
                if ret is None:
                    # processo ainda rodando — zera contador de crashes
                    if restart_count.get(cid, 0) > 0:
                        uptime = now - getattr(proc, '_start_ts', now)
                        if uptime > 120:  # estável há mais de 2 minutos → reset
                            restart_count[cid] = 0
                    continue

                # processo morreu
                crashes = restart_count.get(cid, 0)

                if crashes >= MAX_CRASHES:
                    print(f"  🚫 '{cid}' crashou {crashes}x — DESATIVANDO para evitar ban do Discord.")
                    disabled_by_crashes.add(cid)  # protege contra reinício automático
                    try:
                        del _processes[cid]
                    except KeyError:
                        pass
                    continue

                if now < next_restart_at.get(cid, 0):
                    # ainda no período de espera — não reinicia agora
                    continue

                delay = BACKOFF_DELAYS[min(crashes, len(BACKOFF_DELAYS) - 1)]
                print(f"  ⚠️  '{cid}' parou (código {ret}), crash #{crashes + 1}. "
                      f"Aguardando {delay}s antes de reiniciar...")

                restart_count[cid]   = crashes + 1
                next_restart_at[cid] = now + delay

            # reinicia os que já esperaram o backoff
            # Relê customers.json para capturar mudanças de data feitas pelo dashboard
            customers = load_customers()
            for cid in list(next_restart_at.keys()):
                if cid in _processes and _processes[cid].poll() is not None:
                    if now >= next_restart_at[cid]:
                        customer = next((c for c in customers if c["id"] == cid), None)
                        if customer and customer.get("ativo") and not is_expired(customer):
                            new_proc = start_instance(customer)
                            if new_proc:
                                new_proc._start_ts = time.time()
                                _processes[cid]    = new_proc
                                next_restart_at[cid] = 0
                        else:
                            try:
                                del _processes[cid]
                            except KeyError:
                                pass

    except KeyboardInterrupt:
        print("\n\n⛔ Encerrando todas as instâncias...")
        for cid, proc in _processes.items():
            proc.terminate()
            print(f"  🛑 '{cid}' encerrado.")


def cmd_list() -> None:
    customers = load_customers()
    print(f"\n{'ID':<15} {'Nome':<25} {'Ativo':<8} {'Expira':<12} {'Status'}")
    print("-" * 70)
    for c in customers:
        cid    = c["id"]
        nome   = c.get("nome", "")[:24]
        ativo  = "✅" if c.get("ativo") else "⏸️"
        expira = c.get("expira", "—")
        exp    = "⚠️ Expirado" if is_expired(c) else "OK"
        pid    = _processes[cid].pid if cid in _processes else "—"
        print(f"{cid:<15} {nome:<25} {ativo:<8} {expira:<12} {exp} (PID: {pid})")
    print()


def cmd_add() -> None:
    print("\n➕ Adicionar novo cliente\n")
    cid   = input("ID único (sem espaços, ex: cliente2): ").strip()
    nome  = input("Nome do cliente: ").strip()
    token = input("Token do bot Discord: ").strip()
    expira = input("Data de expiração (YYYY-MM-DD, ex: 2026-12-31): ").strip()

    customers = load_customers()
    if any(c["id"] == cid for c in customers):
        print(f"❌ Já existe um cliente com ID '{cid}'.")
        return

    customers.append({
        "id": cid,
        "nome": nome,
        "token": token,
        "ativo": True,
        "expira": expira,
    })
    save_customers(customers)
    print(f"\n✅ Cliente '{cid}' adicionado com sucesso!")
    print(f"   Pasta de dados: clientes/{cid}/")
    print(f"   Para iniciar: python manager.py\n")


def cmd_stop(cid: str) -> None:
    if cid in _processes:
        _processes[cid].terminate()
        del _processes[cid]
        print(f"🛑 Instância '{cid}' encerrada.")
    else:
        print(f"❌ Nenhuma instância ativa com ID '{cid}'.")


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--list" in args:
        cmd_list()
    elif "--add" in args:
        cmd_add()
    elif "--stop" in args:
        idx = args.index("--stop")
        cid = args[idx + 1] if idx + 1 < len(args) else ""
        cmd_stop(cid)
    elif "--migrate-only" in args:
        # Apenas migração — sem iniciar bots (o dashboard cuida disso)
        print("[manager] Migração concluída. Bots gerenciados pelo dashboard.", flush=True)
    else:
        run_all()
