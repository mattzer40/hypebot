#!/usr/bin/env python3
"""
HypeBot Dashboard
===================
Interface web para gerenciar clientes, prefixos e donos do bot.

Instalar dependência:
    pip install flask

Iniciar:
    python dashboard.py

Acesse: http://localhost:5500
"""

import json
import os
import sys
import hashlib
import secrets
import threading
import subprocess as _subprocess
import time as _time
import base64
import urllib.request
from pathlib import Path
from datetime import date, datetime
from functools import wraps

try:
    from flask import Flask, jsonify, request, render_template_string, redirect, session
except ImportError:
    print("❌ Flask não instalado. Execute: pip install flask")
    sys.exit(1)

# ── Paths ─────────────────────────────────────────────────────────────────────
CODE_DIR       = Path(__file__).parent
DATA_DIR       = Path(os.environ.get("DATA_DIR", str(CODE_DIR)))
DATA_DIR.mkdir(exist_ok=True)
CUSTOMERS_FILE = DATA_DIR / "customers.json"
CONFIG_FILE    = DATA_DIR / "dashboard_config.json"
CLIENTS_DIR    = DATA_DIR / "clientes"
CLIENTS_DIR.mkdir(exist_ok=True)

# ── Auto-download default_avatar.png se não existir ───────────────────────────
_DEFAULT_AVATAR_URL = os.environ.get(
    "DEFAULT_AVATAR_URL",
    "https://cdn.discordapp.com/attachments/1510337421290639552/"
    "1511407671712940092/24q67xm.png"
    "?ex=6a205795&is=6a1f0615&hm=ec81c9597c75992ee403858cd33d5a6f90ea568ce34996ec47099c63d9b08691&"
)
_avatar_file = CODE_DIR / "default_avatar.png"
if not _avatar_file.exists() and _DEFAULT_AVATAR_URL:
    try:
        _req = urllib.request.Request(_DEFAULT_AVATAR_URL, headers={"User-Agent": "HypeBot/1.0"})
        with urllib.request.urlopen(_req, timeout=20) as _r:
            _avatar_file.write_bytes(_r.read())
        print(f"[avatar] default_avatar.png baixado ({_avatar_file.stat().st_size} bytes)", flush=True)
    except Exception as _e:
        print(f"[avatar] falha ao baixar default_avatar.png: {_e}", flush=True)

# Apaga o hash MD5 para forçar reaplicação do avatar nos bots na próxima inicialização
_avatar_md5 = CODE_DIR / "default_avatar.png.md5"
if _avatar_md5.exists():
    try:
        _avatar_md5.unlink()
        print("[avatar] hash resetado — avatar será reaplicado no próximo startup", flush=True)
    except Exception:
        pass

# ── Seed data from environment variables (first-run migration) ─────────────────
import base64 as _b64_seed
def _seed_file(path: Path, env_var: str):
    """Write file from base64-encoded env var.
    Only overwrites if file is missing, unless FORCE_SEED_<KEY>=1 is set.
    E.g. FORCE_SEED_CUSTOMERS=1 forces overwrite of customers.json from SEED_CUSTOMERS.
    """
    key = env_var.removeprefix("SEED_")  # SEED_CUSTOMERS → CUSTOMERS
    force = os.environ.get(f"FORCE_SEED_{key}", "0") == "1"
    if path.exists() and path.stat().st_size > 4 and not force:
        return
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        return
    try:
        content = _b64_seed.b64decode(raw).decode("utf-8")
        path.write_text(content, encoding="utf-8")
        action = "forçado" if force else "restaurado"
        print(f"[seed] {path.name} {action} de {env_var}", flush=True)
    except Exception as e:
        print(f"[seed] Erro ao restaurar {path.name}: {e}", flush=True)

_seed_file(CONFIG_FILE,    "SEED_CONFIG")
_seed_file(CUSTOMERS_FILE, "SEED_CUSTOMERS")

# Migração: o arquivo do código é AUTORITATIVO — substitui o volume completamente.
# Isso garante que clientes removidos do código também sejam removidos do volume.
# Clientes adicionados via dashboard UI são preservados pois ficam só no volume
# e não são afetados por deploys futuros (a lógica abaixo só sobrescreve na diferença).
_code_customers = CODE_DIR / "customers.json"
if _code_customers.exists():
    import json as _json_seed
    try:
        _code_list = _json_seed.loads(_code_customers.read_text(encoding="utf-8"))
        _code_ids  = {c["id"] for c in _code_list}

        if CUSTOMERS_FILE.exists():
            _vol_list = _json_seed.loads(CUSTOMERS_FILE.read_text(encoding="utf-8"))
            _vol_map  = {c["id"]: c for c in _vol_list}

            # 1. Remove do volume clientes que não estão mais no código
            _removed = [c for c in _vol_list if c["id"] not in _code_ids
                        # Preserva clientes adicionados via UI (não existem no código local)
                        # EXCETO se o código foi implantado com a intenção de remover (cleanup)
                        ]
            # MODO LIMPEZA: remove do volume tudo que não está no código
            _cleanup = os.environ.get("CUSTOMERS_CLEANUP", "0") == "1"
            if _cleanup:
                _vol_list = [c for c in _vol_list if c["id"] in _code_ids]
                for _rc in _removed:
                    if _rc["id"] not in _code_ids:
                        print(f"[seed] cliente removido (cleanup): {_rc.get('nome', _rc['id'])}")

            # 2. Adiciona do código clientes que não estão no volume
            _changed = _cleanup  # marca como alterado se fez cleanup
            for _cc in _code_list:
                _cid = _cc["id"]
                if _cid not in {c["id"] for c in _vol_list}:
                    _vol_list.append(_cc)
                    _changed = True
                    print(f"[seed] novo cliente adicionado: {_cc.get('nome', _cid)}")

            if _changed:
                CUSTOMERS_FILE.write_text(
                    _json_seed.dumps(_vol_list, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        else:
            CUSTOMERS_FILE.write_text(
                _json_seed.dumps(_code_list, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"[seed] customers.json criado no volume com {len(_code_list)} cliente(s).")
    except Exception as _e:
        print(f"[seed] erro ao mesclar customers.json: {_e}")

# ── Limpeza automática por servidor (AUTO_CLEANUP_GUILD=<guild_id>) ────────────
_auto_cleanup_guild = os.environ.get("AUTO_CLEANUP_GUILD", "").strip()
if _auto_cleanup_guild and _auto_cleanup_guild.isdigit() and CUSTOMERS_FILE.exists():
    import urllib.request as _ureq_ac
    import urllib.error   as _uerr_ac
    import json as _json_ac

    def _bot_in_guild(token: str, guild_id: str) -> bool:
        try:
            _req = _ureq_ac.Request(
                "https://discord.com/api/v10/users/@me/guilds",
                headers={"Authorization": f"Bot {token}", "User-Agent": "HypeBot/1.0"},
            )
            with _ureq_ac.urlopen(_req, timeout=10) as _r:
                _guilds = _json_ac.loads(_r.read())
            return guild_id in {str(g["id"]) for g in _guilds}
        except Exception:
            return False

    try:
        _all = _json_ac.loads(CUSTOMERS_FILE.read_text(encoding="utf-8"))
        _keep, _removed_ac = [], []
        for _c in _all:
            _tok = _c.get("token", "").strip()
            if not _tok or _tok == "TOKEN_DO_BOT_AQUI":
                _removed_ac.append(_c)
                print(f"[cleanup] removido (sem token): {_c.get('nome', _c['id'])}", flush=True)
            elif _bot_in_guild(_tok, _auto_cleanup_guild):
                _keep.append(_c)
                print(f"[cleanup] mantido: {_c.get('nome', _c['id'])}", flush=True)
            else:
                _removed_ac.append(_c)
                print(f"[cleanup] removido (não está no servidor {_auto_cleanup_guild}): {_c.get('nome', _c['id'])}", flush=True)

        if _removed_ac:
            CUSTOMERS_FILE.write_text(
                _json_ac.dumps(_keep, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"[cleanup] {len(_removed_ac)} cliente(s) removido(s), {len(_keep)} mantido(s).", flush=True)
        else:
            print(f"[cleanup] todos os {len(_keep)} clientes estão no servidor. Nada removido.", flush=True)
    except Exception as _e_ac:
        print(f"[cleanup] erro na limpeza automática: {_e_ac}", flush=True)


app = Flask(__name__)


# ── Auto-backup de configs via Railway API ────────────────────────────────────

def _railway_push_seed(bot_id: str, settings_path: Path, prefix_path: Path) -> None:
    """Empurra bot_settings.json e bot_prefix.txt para env vars SEED_* do Railway.

    Requer RAILWAY_API_TOKEN, RAILWAY_PROJECT_ID, RAILWAY_ENVIRONMENT_ID,
    RAILWAY_SERVICE_ID (os três últimos são injetados automaticamente pelo Railway).
    """
    import gzip as _gz, base64 as _b64, urllib.request as _ureq, json as _rjson

    token      = os.environ.get("RAILWAY_API_TOKEN", "")
    project_id = os.environ.get("RAILWAY_PROJECT_ID", "")
    env_id     = os.environ.get("RAILWAY_ENVIRONMENT_ID", "")
    service_id = os.environ.get("RAILWAY_SERVICE_ID", "")

    if not all([token, project_id, env_id, service_id]):
        return

    def _upsert(name: str, value: str) -> None:
        body = _rjson.dumps({
            "query": "mutation v($i:VariableUpsertInput!){variableUpsert(input:$i)}",
            "variables": {"i": {
                "projectId": project_id,
                "environmentId": env_id,
                "serviceId": service_id,
                "name": name,
                "value": value,
            }}
        }).encode()
        req = _ureq.Request(
            "https://backboard.railway.app/graphql/v2",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "Mozilla/5.0",
            },
        )
        with _ureq.urlopen(req, timeout=15) as resp:
            result = _rjson.loads(resp.read())
            if result.get("errors"):
                print(f"[seed-auto] Railway API erro ({name}): {result['errors']}", flush=True)

    if settings_path.exists():
        raw = settings_path.read_bytes()
        b64 = _b64.b64encode(_gz.compress(raw)).decode()
        _upsert(f"SEED_BOT_SETTINGS_{bot_id}", b64)

    if prefix_path.exists():
        prefix = prefix_path.read_text(encoding="utf-8").strip()
        if prefix:
            _upsert(f"SEED_BOT_PREFIX_{bot_id}", prefix)

    print(f"[seed-auto] backup atualizado para '{bot_id}'", flush=True)


# ── Bot Manager ───────────────────────────────────────────────────────────────

class BotManager:
    """Gerencia subprocessos dos bots Discord, um por cliente."""

    # Backoff de reinício: 30s → 60s → 120s → 300s → 600s → 1800s
    _BACKOFF = [30, 60, 120, 300, 600, 1800]
    _MAX_CRASHES = 8  # desativa após 8 crashes consecutivos

    def __init__(self):
        self._procs:         dict[str, _subprocess.Popen] = {}
        self._tokens:        dict[str, str]   = {}  # cid → token
        self._lock           = threading.Lock()
        self._crash_count:   dict[str, int]   = {}  # cid → crashes consecutivos
        self._next_restart:  dict[str, float] = {}  # cid → timestamp mínimo p/ próximo restart
        self._launch_ts:     dict[str, float] = {}  # cid → quando foi lançado (para reset do contador)
        self._bad_token:     set[str]          = set()  # cids com token inválido (exit 4) — não reiniciar

    def start_loop(self):
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        _time.sleep(5)  # aguarda módulo inicializar completamente
        try:
            self._kill_orphans()
        except Exception as _e:
            print(f"[manager] erro em _kill_orphans: {_e}", flush=True)

        _seed_mtimes:      dict[str, float] = {}  # bot_id → última mtime conhecida
        _seed_dirty_since: dict[str, float] = {}  # bot_id → quando detectou mudança
        _seed_known:       set[str]         = set()  # bot_ids com SEED já existente no Railway
        _SETTLE_DELAY     = 120  # 2 min: espera cliente terminar de configurar
        _SETTLE_DELAY_NEW = 15   # 15s: novo bot sem SEED — empurra rápido

        # Pré-popula _seed_known com bots que já têm SEED no Railway (env vars existentes)
        for _ek in os.environ:
            if _ek.startswith("SEED_BOT_SETTINGS_"):
                _seed_known.add(_ek[len("SEED_BOT_SETTINGS_"):])

        while True:
            try:
                self._sync()
            except Exception as _e:
                import traceback as _tb
                print(f"[manager] ERRO em _sync: {type(_e).__name__}: {_e}", flush=True)
                print(_tb.format_exc(), flush=True)

            # ── Auto-backup de configs para env vars do Railway ───────────────
            # Monitora bot_settings.json e bot_prefix.txt de cada cliente.
            # Quando algo muda, aguarda 2 min (cliente terminando de configurar)
            # — ou 15s para bots novos sem SEED ainda —
            # e então empurra para SEED_BOT_SETTINGS_<id> / SEED_BOT_PREFIX_<id>
            # via Railway API — sem causar redeploy se RAILWAY_API_TOKEN estiver definido.
            if os.environ.get("RAILWAY_API_TOKEN"):
                try:
                    now_t = _time.time()
                    if CLIENTS_DIR.exists():
                        for _d in CLIENTS_DIR.iterdir():
                            if not _d.is_dir():
                                continue
                            _bid = _d.name
                            _sf  = _d / "bot_settings.json"
                            _pf  = _d / "bot_prefix.txt"
                            _mtime = (
                                (_sf.stat().st_mtime  if _sf.exists() else 0) +
                                (_pf.stat().st_mtime  if _pf.exists() else 0)
                            )
                            if _mtime != _seed_mtimes.get(_bid, 0):
                                # Mudou — anota quando detectou (se ainda não estava sujo)
                                if _bid not in _seed_dirty_since:
                                    _seed_dirty_since[_bid] = now_t
                                _seed_mtimes[_bid] = _mtime
                            elif _bid in _seed_dirty_since:
                                # Arquivo estabilizou — espera o tempo de acomodação
                                # Bots novos (sem SEED) usam delay curto para backup imediato
                                _delay = _SETTLE_DELAY if _bid in _seed_known else _SETTLE_DELAY_NEW
                                if now_t - _seed_dirty_since[_bid] >= _delay:
                                    _seed_dirty_since.pop(_bid)
                                    try:
                                        _railway_push_seed(_bid, _sf, _pf)
                                        _seed_known.add(_bid)  # marca como tendo SEED
                                    except Exception as _ep:
                                        print(f"[seed-auto] erro ao empurrar {_bid}: {_ep}", flush=True)
                except Exception as _es:
                    print(f"[seed-auto] erro no monitor: {_es}", flush=True)

            _time.sleep(30)

    def _kill_orphans(self):
        """Encerra processos Python rodando bot.py que não são gerenciados por esta instância."""
        import psutil as _ps
        try:
            bot_script = str(CODE_DIR / "bot.py")
            current_pid = os.getpid()
            killed = 0
            for proc in _ps.process_iter(["pid", "cmdline"]):
                try:
                    cmd = proc.info.get("cmdline") or []
                    if (
                        any("bot.py" in str(arg) for arg in cmd)
                        and proc.pid != current_pid
                        and proc.pid not in {p.pid for p in self._procs.values() if p}
                    ):
                        proc.terminate()
                        killed += 1
                        print(f"[manager] processo órfão encerrado: PID {proc.pid}", flush=True)
                except Exception:
                    pass
            if killed:
                print(f"[manager] {killed} processo(s) órfão(s) encerrado(s) no startup", flush=True)
        except ImportError:
            # psutil não instalado — tenta via /proc (Linux/Railway)
            try:
                import subprocess as _sp
                bot_script_name = "bot.py"
                result = _sp.run(
                    ["pgrep", "-f", bot_script_name],
                    capture_output=True, text=True
                )
                current_pids = {p.pid for p in self._procs.values() if p}
                current_pids.add(os.getpid())
                for line in result.stdout.strip().splitlines():
                    try:
                        pid = int(line.strip())
                        if pid not in current_pids:
                            _sp.run(["kill", str(pid)], capture_output=True)
                            print(f"[manager] processo órfão encerrado via pgrep: PID {pid}", flush=True)
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception as _e:
            print(f"[manager] erro ao limpar órfãos: {_e}", flush=True)

    def _token_in_use_by(self, token: str, exclude_cid: str = "") -> str:
        """Retorna o cid que já está rodando com esse token (excluindo exclude_cid), ou ''."""
        for cid, tok in self._tokens.items():
            if cid == exclude_cid:
                continue
            if tok == token:
                proc = self._procs.get(cid)
                if proc and proc.poll() is None:
                    return cid
        return ""

    def _sync(self):
        customers = load_customers()
        active = {
            c["id"]: c for c in customers
            if c.get("ativo", True) and not is_expired(c)
                and c.get("token", "").strip() not in ("", "TOKEN_DO_BOT_AQUI")
        }
        if not active:
            total = len(customers)
            print(f"[manager] _sync: {total} cliente(s) no arquivo, nenhum ativo/válido para iniciar", flush=True)
        # Índice token → primeiro cid ativo (detecta duplicatas sem depender de processo vivo)
        _first_for_token: dict[str, str] = {}
        for cid, c in active.items():
            tok = c.get("token", "").strip()
            if tok not in _first_for_token:
                _first_for_token[tok] = cid

        now = _time.time()

        with self._lock:
            # ── Para bots que não estão mais na lista ativa ───────────────────
            for cid in list(self._procs):
                if cid not in active:
                    try:
                        self._procs[cid].terminate()
                    except Exception:
                        pass
                    self._tokens.pop(cid, None)
                    self._crash_count.pop(cid, None)
                    self._next_restart.pop(cid, None)
                    self._bad_token.discard(cid)
                    del self._procs[cid]

            # ── Verifica e reinicia bots com backoff ──────────────────────────
            for cid, c in active.items():
                proc = self._procs.get(cid)

                # Processo rodando normalmente
                if proc is not None and proc.poll() is None:
                    # Estável há mais de 2 minutos → zera contador de crashes
                    if self._crash_count.get(cid, 0) > 0:
                        if now - self._launch_ts.get(cid, now) > 120:
                            self._crash_count[cid] = 0
                    continue

                # Processo morreu ou nunca foi iniciado
                # Exit code 4 = token inválido/revogado — não reiniciar nunca
                if proc is not None:
                    exit_code = proc.poll()
                    if exit_code == 4:
                        if cid not in self._bad_token:
                            self._bad_token.add(cid)
                            print(f"[manager] '{cid}' token inválido (exit 4) — desativado permanentemente", flush=True)
                        continue

                crashes = self._crash_count.get(cid, 0)

                # Nunca reiniciar bot marcado como token inválido
                if cid in self._bad_token:
                    continue

                # Desativado por excesso de crashes — auto-reset após 30 min
                if crashes >= self._MAX_CRASHES:
                    last_op = self._next_restart.get(cid, 0)
                    if now - last_op < 1800:   # 30 min de cooldown desde o último crash
                        continue
                    # Cooldown expirou → reseta e tenta novamente
                    print(f"[manager] '{cid}' auto-reset após desativação por crashes — tentando novamente", flush=True)
                    self._crash_count.pop(cid, None)
                    self._next_restart.pop(cid, None)
                    crashes = 0

                # Ainda dentro do período de backoff
                if now < self._next_restart.get(cid, 0):
                    continue

                token = c.get("token", "").strip()

                # Bloqueia token duplicado
                first_owner = _first_for_token.get(token, cid)
                if first_owner != cid:
                    print(f"[manager] token duplicado: '{cid}' = '{first_owner}' — pulando", flush=True)
                    continue
                conflict = self._token_in_use_by(token, exclude_cid=cid)
                if conflict:
                    print(f"[manager] token em uso por '{conflict}' — '{cid}' aguardando", flush=True)
                    continue

                # Agenda backoff quando o processo morreu pela primeira vez neste ciclo
                # (next_restart == 0 significa que ainda não agendamos reinício para este crash)
                if proc is not None and self._next_restart.get(cid, 0) == 0:
                    delay = self._BACKOFF[min(crashes, len(self._BACKOFF) - 1)]
                    self._crash_count[cid]  = crashes + 1
                    self._next_restart[cid] = now + delay
                    print(f"[manager] '{cid}' crashou #{crashes + 1} (exit_code={exit_code}) — aguardando {delay}s", flush=True)
                    continue

                # Aqui chegamos quando:
                # 1. proc is None (primeira vez) → inicia imediatamente
                # 2. backoff expirou (next_restart <= now) → relança após crash
                p = self._launch(c)
                if p:
                    self._procs[cid]     = p
                    self._tokens[cid]    = token
                    self._launch_ts[cid] = now
                    self._next_restart.pop(cid, None)  # limpa backoff após reinício
                    _time.sleep(3)  # espaçamento entre lançamentos

    def _launch(self, customer: dict):
        cid   = customer["id"]
        token = customer.get("token", "").strip()
        cdir  = CLIENTS_DIR / cid
        cdir.mkdir(exist_ok=True)
        env_file      = cdir / ".env"
        settings_file = cdir / "bot_settings.json"
        avatar_file   = cdir / "bot_avatar.png"   # arquivo específico por cliente

        # Copia o avatar padrão HYPE para o cliente (se não tiver avatar customizado).
        # Garante hash único por cliente — evita que um bot "barre" os outros.
        if not avatar_file.exists() or avatar_file.stat().st_size == 0:
            _default = CODE_DIR / "default_avatar.png"
            if not _default.exists():
                _default = DATA_DIR / "default_avatar.png"
            if _default.exists():
                import shutil as _sh_av
                _sh_av.copy(_default, avatar_file)
                # Remove hash antigo para forçar reaplicação no Discord
                _av_md5 = Path(str(avatar_file) + ".md5")
                if _av_md5.exists():
                    _av_md5.unlink()

        if not env_file.exists():
            env_file.write_text(f"DISCORD_TOKEN={token}\n", encoding="utf-8")
        else:
            env_file.write_text(f"DISCORD_TOKEN={token}\n", encoding="utf-8")

        bot_script = CODE_DIR / "bot.py"
        if not bot_script.exists():
            return None

        env = os.environ.copy()
        env["BOT_ENV_FILE"]      = str(env_file)
        env["BOT_SETTINGS_FILE"] = str(settings_file)
        env["BOT_PREFIX_FILE"]   = str(cdir / "bot_prefix.txt")
        env["BOT_AVATAR_FILE"]   = str(avatar_file)
        env["BOT_BANNER_FILE"]   = str(cdir / "bot_banner.png")
        env["DATA_DIR"]          = str(DATA_DIR)
        env["PYTHONUNBUFFERED"]  = "1"
        env["BOT_EXPIRA"]        = str(customer.get("expira", "") or "")
        guild_id = str(customer.get("guild_id", "") or "").strip()
        if guild_id:
            env["BOT_GUILD_ID"] = guild_id
        # Aponta o bot para o dashboard para buscar imagens do postador automático
        # Usa PORT (Railway) ou DASHBOARD_PORT (local) para garantir porta correta
        _dash_port = int(os.environ.get("PORT") or os.environ.get("DASHBOARD_PORT", 5500))
        env.setdefault("BOT_DASHBOARD_URL", f"http://localhost:{_dash_port}")

        log_path = cdir / "bot.log"
        try:
            log_f = open(log_path, "a", encoding="utf-8")
            log_f.write(f"\n{'='*40}\n[{datetime.now()}] Iniciando bot '{cid}'\n")
            proc = _subprocess.Popen(
                [sys.executable, str(bot_script)],
                env=env, stdout=_subprocess.PIPE, stderr=_subprocess.STDOUT,
                cwd=str(CODE_DIR),
            )

            # Encaminha a saída do bot tanto para o log-file quanto para o stdout do dashboard
            # (Railway captura o stdout do processo principal — gunicorn)
            def _relay(p, lf, prefix):
                try:
                    for line in iter(p.stdout.readline, b""):
                        decoded = line.decode("utf-8", errors="replace").rstrip()
                        lf.write(decoded + "\n")
                        lf.flush()
                        print(f"[bot:{prefix}] {decoded}", flush=True)
                except Exception:
                    pass
                finally:
                    try: lf.close()
                    except Exception: pass

            _relay_thread = threading.Thread(
                target=_relay, args=(proc, log_f, cid[:8]), daemon=True
            )
            _relay_thread.start()
            return proc
        except Exception as e:
            try:
                with open(log_path, "a") as lf:
                    lf.write(f"Erro ao iniciar: {e}\n")
            except Exception:
                pass
            return None

    def status(self) -> dict:
        now = _time.time()
        with self._lock:
            result = {
                cid: {
                    "running":    proc.poll() is None,
                    "pid":        proc.pid,
                    "crashes":    self._crash_count.get(cid, 0),
                    "backoff":    max(0, int(self._next_restart.get(cid, 0) - now)),
                    "bad_token":  cid in self._bad_token,
                }
                for cid, proc in self._procs.items()
            }
        return result

    def start_bot(self, cid: str) -> tuple[bool, str]:
        """Retorna (ok, erro). erro='' quando iniciado com sucesso."""
        customers = load_customers()
        c = next((x for x in customers if x["id"] == cid), None)
        if not c:
            return False, "Cliente não encontrado."
        token = c.get("token", "").strip()
        # Bloqueia token duplicado em qualquer cliente ativo, independente de processo vivo
        for other in customers:
            if other["id"] == cid:
                continue
            if other.get("token", "").strip() == token and other.get("ativo", True):
                nome = other.get("nome") or other["id"]
                return False, f"Token já está atribuído ao cliente '{nome}' ({other['id']}). Cada bot precisa de um token Discord único."
        with self._lock:
            old = self._procs.get(cid)
            if old and old.poll() is None:
                return True, ""  # already running
            conflict = self._token_in_use_by(token, exclude_cid=cid)
            if conflict:
                return False, f"Token já em uso pelo cliente '{conflict}'. Dois bots não podem usar o mesmo token Discord."
            p = self._launch(c)
            if p:
                self._procs[cid]     = p
                self._tokens[cid]    = token
                self._launch_ts[cid] = _time.time()
                # Reinício manual reseta contadores e marcação de token inválido
                self._crash_count.pop(cid, None)
                self._next_restart.pop(cid, None)
                self._bad_token.discard(cid)
                return True, ""
        return False, "Falha ao iniciar o processo."

    def duplicate_token_conflict(self, cid: str) -> str:
        """Retorna o cid conflitante se o token desse cliente já está em uso por outro, ou ''."""
        customers = load_customers()
        c = next((x for x in customers if x["id"] == cid), None)
        if not c:
            return ""
        token = c.get("token", "").strip()
        if not token:
            return ""
        with self._lock:
            return self._token_in_use_by(token, exclude_cid=cid)

    def stop_bot(self, cid: str):
        with self._lock:
            proc = self._procs.pop(cid, None)
            self._tokens.pop(cid, None)
            if proc:
                try:
                    proc.terminate()
                except Exception:
                    pass

    def restart_bot(self, cid: str) -> tuple[bool, str]:
        self.stop_bot(cid)
        _time.sleep(3)  # aguarda gateway do Discord fechar a sessão anterior
        return self.start_bot(cid)

    def restart_all(self) -> dict:
        """Para e reinicia todos os bots ativos. Retorna {cid: ok}."""
        customers = load_customers()
        results = {}
        with self._lock:
            for cid, proc in list(self._procs.items()):
                try:
                    proc.terminate()
                except Exception:
                    pass
            self._procs.clear()
            self._tokens.clear()
        _time.sleep(2)
        seen_tokens: set[str] = set()
        for c in customers:
            if not c.get("ativo", True):
                continue
            token = c.get("token", "").strip()
            if not token or token == "TOKEN_DO_BOT_AQUI":
                continue
            if token in seen_tokens:
                results[c["id"]] = False
                print(f"[manager] restart_all: token duplicado para '{c['id']}' — ignorado", flush=True)
                continue
            seen_tokens.add(token)
            p = self._launch(c)
            if p:
                with self._lock:
                    self._procs[c["id"]] = p
                    self._tokens[c["id"]] = token
                results[c["id"]] = True
            else:
                results[c["id"]] = False
        return results


# ── Restauração final de customers.json antes de iniciar o manager ────────────
# Detecta e corrige customers.json vazio/corrompido usando SEED_CUSTOMERS.
# Roda DEPOIS de _seed_file e do merge, garantindo dados válidos.
_cfix_raw = os.environ.get("SEED_CUSTOMERS", "").strip()
if _cfix_raw:
    try:
        import base64 as _cfix_b64, json as _cfix_json
        _cfix_seed = _cfix_json.loads(_cfix_b64.b64decode(_cfix_raw).decode("utf-8"))
        _cfix_vol: list = []
        if CUSTOMERS_FILE.exists():
            try:
                _cfix_vol = _cfix_json.loads(CUSTOMERS_FILE.read_text(encoding="utf-8"))
            except Exception:
                _cfix_vol = []
        _cfix_size = CUSTOMERS_FILE.stat().st_size if CUSTOMERS_FILE.exists() else 0
        print(f"[cfix] customers.json: {_cfix_size}B, {len(_cfix_vol)} entr{'y' if len(_cfix_vol)==1 else 'ies'}, seed has {len(_cfix_seed)}", flush=True)
        if not isinstance(_cfix_vol, list) or len(_cfix_vol) == 0:
            if isinstance(_cfix_seed, list) and _cfix_seed:
                CUSTOMERS_FILE.write_text(
                    _cfix_json.dumps(_cfix_seed, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(f"[cfix] customers.json restaurado de SEED_CUSTOMERS ({len(_cfix_seed)} clientes)", flush=True)
    except Exception as _cfix_e:
        print(f"[cfix] erro: {_cfix_e}", flush=True)

bot_manager = BotManager()
bot_manager.start_loop()


# ── Config + secret key ───────────────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {"owners": []}
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _init_secret() -> str:
    cfg = load_config()
    if "secret_key" not in cfg:
        cfg["secret_key"] = secrets.token_hex(32)
        save_config(cfg)
    return cfg["secret_key"]


app.secret_key = _init_secret()


# ── Password helpers ──────────────────────────────────────────────────────────

def _hash(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return h, salt


def _verify(password: str, stored: str, salt: str) -> bool:
    return _hash(password, salt)[0] == stored


# ── Owner lookup ──────────────────────────────────────────────────────────────

def get_owner(oid: str) -> dict | None:
    return next((o for o in load_config().get("owners", []) if o["id"] == oid), None)


def current_owner() -> dict | None:
    return get_owner(session.get("uid", ""))


# ── Auth decorators ───────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def w(*a, **kw):
        if "uid" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Não autenticado"}), 401
            return redirect("/login")
        return f(*a, **kw)
    return w


def super_required(f):
    @wraps(f)
    def w(*a, **kw):
        if "uid" not in session:
            return jsonify({"ok": False, "error": "Não autenticado"}), 401
        o = get_owner(session["uid"])
        if not o or o.get("role") != "super":
            return jsonify({"ok": False, "error": "Apenas super admins"}), 403
        return f(*a, **kw)
    return w


# ── Customer helpers ──────────────────────────────────────────────────────────

def load_customers() -> list[dict]:
    if not CUSTOMERS_FILE.exists():
        return []
    with open(CUSTOMERS_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_customers(lst: list[dict]) -> None:
    with open(CUSTOMERS_FILE, "w", encoding="utf-8") as f:
        json.dump(lst, f, ensure_ascii=False, indent=2)


def is_expired(c: dict) -> bool:
    try:
        return datetime.strptime(c.get("expira", ""), "%Y-%m-%d").date() < date.today()
    except ValueError:
        return False


def customer_status(c: dict) -> str:
    if not c.get("ativo", True):
        return "disabled"
    if is_expired(c):
        return "expired"
    return "active"


def load_settings(cid: str) -> dict:
    f = CLIENTS_DIR / cid / "bot_settings.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_settings(cid: str, data: dict) -> None:
    d = CLIENTS_DIR / cid
    d.mkdir(exist_ok=True)
    (d / "bot_settings.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def first_prefix(settings: dict) -> str:
    for gs in settings.values():
        if isinstance(gs, dict) and "prefix" in gs:
            return gs["prefix"]
    return "n!"


def read_log(cid: str, n: int = 60) -> str:
    f = CLIENTS_DIR / cid / "bot.log"
    if not f.exists():
        return "(sem log)"
    try:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except Exception:
        return "(erro ao ler log)"


# ── HTML templates ────────────────────────────────────────────────────────────

_SHARED_CSS = """
<style>
:root{
  --bg:#050709;--surface:#0d1117;--card:#111820;--border:#1a2535;
  --accent:#0891b2;--accent2:#22d3ee;
  --green:#22c55e;--red:#ef4444;--yellow:#f59e0b;
  --text:#e2e8f0;--muted:#64748b;--input-bg:#080d14;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh}
.logo{font-size:1.8rem;font-weight:800;text-align:center;margin-bottom:4px;
  background:linear-gradient(135deg,#22d3ee,#0891b2);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent}
.form-group{margin-bottom:16px}
label{display:block;font-size:.78rem;font-weight:600;color:var(--muted);
  text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
input,select{width:100%;background:var(--input-bg);border:1px solid var(--border);
  color:var(--text);padding:10px 14px;border-radius:8px;font-size:.9rem;
  font-family:inherit;outline:none;transition:border-color .15s}
input:focus,select:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(8,145,178,.15)}
.err{background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);
  color:#ef4444;padding:10px 14px;border-radius:8px;font-size:.85rem;margin-bottom:16px}
select option{background:var(--surface)}
</style>
"""

LOGIN_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HypeBot — Login</title>""" + _SHARED_CSS + """
<style>
body{
  display:flex;align-items:center;justify-content:center;
  min-height:100vh;padding:20px;
  background:radial-gradient(ellipse at 50% 0%,rgba(8,145,178,.08) 0%,transparent 60%),var(--bg);
}
.card{
  background:var(--card);
  border:1px solid rgba(8,145,178,.2);
  border-radius:20px;
  padding:40px 36px 36px;
  width:100%;max-width:380px;
  box-shadow:0 0 60px rgba(8,145,178,.06),0 24px 48px rgba(0,0,0,.5);
  position:relative;overflow:hidden;
}
.card::before{
  content:'';position:absolute;top:-80px;left:50%;transform:translateX(-50%);
  width:260px;height:160px;
  background:radial-gradient(ellipse,rgba(8,145,178,.12) 0%,transparent 70%);
  pointer-events:none;
}
.logo-wrap{display:flex;align-items:center;justify-content:center;gap:10px;margin-bottom:6px}
.logo-icon{width:36px;height:36px;border-radius:50%;border:2px solid rgba(8,145,178,.4);flex-shrink:0}
.sub{text-align:center;color:var(--muted);font-size:.82rem;margin-bottom:28px;letter-spacing:.02em}
.divider{border:none;border-top:1px solid var(--border);margin:20px 0}
.btn{
  width:100%;padding:12px;border-radius:10px;border:none;cursor:pointer;
  font-size:.95rem;font-weight:700;
  background:linear-gradient(135deg,#22d3ee,#0891b2);
  color:#000;margin-top:8px;
  transition:opacity .15s,transform .1s;
  box-shadow:0 4px 16px rgba(8,145,178,.3);
  letter-spacing:.02em;
}
.btn:hover{opacity:.9;transform:translateY(-1px)}
.btn:active{transform:translateY(0)}
@media(max-width:420px){
  .card{padding:32px 20px 28px;border-radius:16px}
  .logo{font-size:1.5rem}
}
</style>
</head>
<body>
<div class="card">
  <div class="logo-wrap">
    <img class="logo-icon" src="/bot-avatar.png" onerror="this.style.display='none'">
    <div class="logo">HypeBot</div>
  </div>
  <div class="sub">Bot Manager Dashboard</div>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  <form method="POST">
    <div class="form-group">
      <label>Usuário</label>
      <input name="username" autocomplete="username" required autofocus>
    </div>
    <div class="form-group">
      <label>Senha</label>
      <input type="password" name="password" autocomplete="current-password" required>
    </div>
    <button type="submit" class="btn">Entrar</button>
  </form>
</div>
</body>
</html>"""

SETUP_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="UTF-8"><title>HypeBot — Configuração</title>""" + _SHARED_CSS + """
<style>
body{display:flex;align-items:center;justify-content:center}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;
  padding:40px;width:100%;max-width:420px}
.sub{text-align:center;color:var(--muted);font-size:.85rem;margin-bottom:6px}
.desc{text-align:center;color:var(--muted);font-size:.8rem;margin-bottom:24px;line-height:1.5}
.btn{width:100%;padding:11px;border-radius:8px;border:none;cursor:pointer;
  font-size:.95rem;font-weight:700;background:var(--accent);color:#fff;
  margin-top:8px;transition:opacity .15s}
.btn:hover{opacity:.85}
</style>
</head>
<body>
<div class="card">
  <div class="logo">HypeBot</div>
  <div class="sub">Configuração Inicial</div>
  <div class="desc">Crie sua conta de administrador principal</div>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  <form method="POST">
    <div class="form-group">
      <label>Seu nome</label>
      <input name="nome" required autofocus placeholder="Anderson">
    </div>
    <div class="form-group">
      <label>Nome de usuário</label>
      <input name="username" required pattern="[a-zA-Z0-9_-]+" placeholder="anderson" title="Apenas letras, números, - e _">
    </div>
    <div class="form-group">
      <label>Senha</label>
      <input type="password" name="password" required minlength="6" placeholder="Mínimo 6 caracteres">
    </div>
    <div class="form-group">
      <label>Confirmar senha</label>
      <input type="password" name="confirm" required>
    </div>
    <button type="submit" class="btn">Criar conta de Super Admin</button>
  </form>
</div>
</body>
</html>"""

MAIN_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<title>HypeBot Dashboard</title>
<style>
:root{
  --bg:#050709;--surface:#0d1117;--card:#111820;--border:#1a2535;
  --accent:#0891b2;--accent2:#22d3ee;
  --green:#22c55e;--red:#ef4444;--yellow:#f59e0b;
  --text:#e2e8f0;--muted:#64748b;--input-bg:#080d14;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh}

/* Header */
header{background:var(--surface);border-bottom:1px solid var(--border);padding:12px 28px;
  display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100;gap:16px}
.logo{font-size:1.3rem;font-weight:800;flex-shrink:0;
  background:linear-gradient(135deg,#22d3ee,#0891b2);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;letter-spacing:1px}

/* Nav tabs */
.nav-tabs{display:flex;gap:4px;flex:1;justify-content:center}
.nav-tab{padding:7px 18px;border-radius:8px;border:none;background:transparent;
  color:var(--muted);cursor:pointer;font-size:.85rem;font-weight:600;transition:all .15s}
.nav-tab:hover{color:var(--text);background:rgba(8,145,178,.15)}
.nav-tab.active{background:var(--accent);color:#fff}

/* User menu */
.user-menu{display:flex;align-items:center;gap:10px;flex-shrink:0}
.avatar{width:32px;height:32px;border-radius:50%;background:linear-gradient(135deg,#22d3ee,#0891b2);
  display:flex;align-items:center;justify-content:center;font-weight:800;font-size:.85rem;color:#fff;flex-shrink:0}
.uname{font-size:.82rem;color:var(--muted);white-space:nowrap}

/* Layout */
main{padding:28px;max-width:1200px;margin:0 auto}

/* Stats */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:16px;margin-bottom:28px}
.stat-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px;text-align:center}
.stat-num{font-size:2rem;font-weight:800;
  background:linear-gradient(135deg,#22d3ee,#0891b2);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent}
.stat-label{font-size:.8rem;color:var(--muted);margin-top:4px}

/* Section */
.section-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
.section-title{font-size:1.05rem;font-weight:700}

/* Table */
.table-wrap{background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden}
table{width:100%;border-collapse:collapse}
th{background:var(--surface);padding:11px 16px;text-align:left;font-size:.73rem;
   color:var(--muted);text-transform:uppercase;letter-spacing:.5px;font-weight:600}
td{padding:13px 16px;border-bottom:1px solid var(--border);font-size:.9rem;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(8,145,178,.04)}

/* Badges */
.badge{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:20px;font-size:.73rem;font-weight:700}
.badge::before{content:'';width:6px;height:6px;border-radius:50%}
.badge.active{background:rgba(34,197,94,.15);color:var(--green)}.badge.active::before{background:var(--green)}
.badge.expired{background:rgba(239,68,68,.15);color:var(--red)}.badge.expired::before{background:var(--red)}
.badge.disabled{background:rgba(100,116,139,.15);color:var(--muted)}.badge.disabled::before{background:var(--muted)}
.badge.super{background:rgba(34,211,238,.2);color:#22d3ee}.badge.super::before{background:#22d3ee}
.badge.admin{background:rgba(100,116,139,.15);color:var(--muted)}.badge.admin::before{background:var(--muted)}
.badge.bot-on{background:rgba(34,197,94,.15);color:var(--green)}.badge.bot-on::before{background:var(--green)}
.badge.bot-off{background:rgba(239,68,68,.15);color:var(--red)}.badge.bot-off::before{background:var(--red)}

/* Buttons */
.btn{display:inline-flex;align-items:center;gap:6px;padding:7px 14px;border-radius:8px;
     border:none;cursor:pointer;font-size:.8rem;font-weight:600;
     transition:opacity .15s,transform .1s;text-decoration:none}
.btn:hover{opacity:.85;transform:translateY(-1px)}.btn:active{transform:translateY(0)}
.btn-primary{background:var(--accent);color:#fff}
.btn-ghost{background:var(--border);color:var(--text)}
.btn-danger{background:rgba(239,68,68,.2);color:var(--red)}
.btn-warn{background:rgba(245,158,11,.15);color:var(--yellow)}
.btn-success{background:rgba(34,197,94,.2);color:var(--green)}
.btn-sm{padding:5px 10px;font-size:.75rem}
.actions{display:flex;gap:6px;flex-wrap:wrap}

/* Prefix chip */
.prefix-chip{display:inline-block;background:var(--input-bg);border:1px solid var(--border);
  color:#22d3ee;font-family:monospace;padding:2px 8px;border-radius:6px;font-size:.9rem;font-weight:700}

/* Modal */
.overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:200;
  align-items:center;justify-content:center;padding:20px;backdrop-filter:blur(4px)}
.overlay.open{display:flex}
.modal{background:var(--card);border:1px solid var(--border);border-top:3px solid var(--accent);
  border-radius:16px;padding:26px;width:100%;max-width:520px;max-height:90vh;
  overflow-y:auto;position:relative;box-shadow:0 24px 60px rgba(0,0,0,.55)}
.modal-title{font-size:1.1rem;font-weight:700;margin-bottom:20px;display:flex;align-items:center;gap:8px}
.modal-close{position:absolute;top:16px;right:16px;background:rgba(255,255,255,.06);
  border:1px solid var(--border);color:var(--muted);width:30px;height:30px;
  border-radius:50%;cursor:pointer;font-size:.85rem;
  display:flex;align-items:center;justify-content:center;transition:all .15s}
.modal-close:hover{background:rgba(239,68,68,.2);border-color:var(--red);color:var(--red)}
.form-group{margin-bottom:16px}
.form-label{display:block;font-size:.75rem;font-weight:700;color:var(--muted);
  text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px}
input,select,textarea{width:100%;background:var(--input-bg);border:1px solid var(--border);
  color:var(--text);padding:10px 14px;border-radius:8px;font-size:.9rem;font-family:inherit;
  transition:border-color .15s;outline:none}
input:focus,select:focus,textarea:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(8,145,178,.15)}
select option{background:var(--surface)}
.check-row{display:flex;align-items:center;gap:10px;cursor:pointer}
.check-row input[type=checkbox]{width:18px;height:18px;cursor:pointer;accent-color:var(--accent)}
.check-row span{font-size:.9rem;font-weight:500}
/* Password toggle */
.pwd-wrap{position:relative}
.pwd-wrap input{padding-right:42px}
.pwd-toggle{position:absolute;right:10px;top:50%;transform:translateY(-50%);
  background:none;border:none;color:var(--muted);cursor:pointer;font-size:.9rem;
  padding:4px;width:auto;transition:color .15s}
.pwd-toggle:hover{color:var(--accent)}

/* Guild prefix table */
.guild-table{width:100%;border-collapse:collapse;font-size:.84rem;border-radius:10px;overflow:hidden}
.guild-table th{background:var(--surface);padding:9px 12px;font-size:.7rem;letter-spacing:.4px;font-weight:700}
.guild-table tr:hover td{background:rgba(255,255,255,.02)}
.guild-table td{padding:8px 12px;border-bottom:1px solid var(--border)}
.guild-table tr:last-child td{border-bottom:none}
.guild-table input{padding:5px 8px;font-size:.84rem}

/* Log */
.log-box{background:#0d1117;border:1px solid var(--border);border-radius:8px;padding:14px;
  font-family:monospace;font-size:.72rem;line-height:1.7;max-height:200px;
  overflow-y:auto;color:#7ee787;white-space:pre-wrap;word-break:break-all}

/* Toast */
.toast-wrap{position:fixed;bottom:24px;right:24px;display:flex;flex-direction:column;gap:8px;z-index:999}
.toast{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px 18px;
  font-size:.85rem;display:flex;align-items:center;gap:10px;animation:slideIn .2s ease;max-width:360px}
.toast.success{border-color:var(--green)}.toast.error{border-color:var(--red)}.toast.warn{border-color:#f59e0b;color:#f59e0b}
@keyframes slideIn{from{transform:translateX(100%);opacity:0}to{transform:translateX(0);opacity:1}}

.empty{text-align:center;padding:50px 20px;color:var(--muted)}
.empty-icon{font-size:3rem;margin-bottom:12px}
.divider{border:none;border-top:1px solid var(--border);margin:20px 0}
.sub-label{font-size:.72rem;font-weight:700;color:var(--accent);text-transform:uppercase;
  letter-spacing:.7px;margin-bottom:10px;display:flex;align-items:center;gap:6px}
.you-tag{font-size:.7rem;background:rgba(8,145,178,.2);color:#22d3ee;
  padding:2px 7px;border-radius:10px;font-weight:600;margin-left:6px}

@media(max-width:700px){
  main{padding:12px}
  header{padding:10px 12px;flex-wrap:wrap;gap:8px}
  .logo{font-size:1.1rem}
  .stats{grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:16px}
  .stat-card{padding:14px 10px}
  .stat-num{font-size:1.6rem}
  th,td{padding:9px 8px;font-size:.8rem}
  .nav-tabs{gap:2px;flex:1 1 100%;order:3;justify-content:flex-start;overflow-x:auto;padding-bottom:2px}
  .nav-tab{padding:6px 10px;font-size:.76rem;white-space:nowrap}
  .user-menu{order:2}
  .uname{display:none}
  .col-pfx,.col-exp{display:none}
  .section-header{flex-direction:column;align-items:flex-start;gap:8px}
  .actions{flex-wrap:wrap;gap:4px}
  .btn{font-size:.76rem;padding:6px 11px}
  .modal{padding:18px 16px;border-radius:14px}
  .table-wrap{overflow-x:auto}
  table{min-width:340px}
  .toast-wrap{bottom:16px;right:12px;left:12px}
  .toast{max-width:100%}
}
@media(max-width:420px){
  .stats{grid-template-columns:1fr 1fr}
  .nav-tab{padding:5px 8px;font-size:.72rem}
}
</style>
</head>
<body>

<header>
  <div class="logo" style="display:flex;align-items:center;gap:8px">
    <img src="/bot-avatar.png" style="width:32px;height:32px;border-radius:50%;object-fit:cover" onerror="this.style.display='none'">
    HypeBot
  </div>
  <div class="nav-tabs">
    <button class="nav-tab active" id="btn-tab-clientes" onclick="showTab('clientes')">🤖 Clientes</button>
    <button class="nav-tab" id="btn-tab-donos" onclick="showTab('donos')" style="display:none">👑 Donos</button>
  </div>
  <div class="user-menu">
    <button class="btn btn-warn btn-sm" onclick="restartAll(this)" title="Reiniciar todos os bots">🔄 Reiniciar Todos</button>
    <div class="avatar" id="hdr-avatar">?</div>
    <span class="uname" id="hdr-name"></span>
    <a href="/logout" class="btn btn-ghost btn-sm">Sair</a>
  </div>
</header>

<main>

<!-- ─────────────── TAB: CLIENTES ─────────────── -->
<div id="tab-clientes">
  <div class="stats">
    <div class="stat-card"><div class="stat-num" id="s-total">—</div><div class="stat-label">Total</div></div>
    <div class="stat-card"><div class="stat-num" id="s-active">—</div><div class="stat-label">Ativos</div></div>
    <div class="stat-card"><div class="stat-num" id="s-bots">—</div><div class="stat-label">Bots Online</div></div>
    <div class="stat-card"><div class="stat-num" id="s-expired">—</div><div class="stat-label">Expirados</div></div>
    <div class="stat-card"><div class="stat-num" id="s-disabled">—</div><div class="stat-label">Desativados</div></div>
  </div>
  <div class="section-header">
    <div class="section-title">🤖 Clientes</div>
    <div style="display:flex;gap:8px">
      <button class="btn btn-danger btn-sm" onclick="openCleanGuild()" title="Verificar e excluir bots fora de um servidor">🗑️ Limpar por Servidor</button>
      <button class="btn btn-primary" onclick="openAdd()">＋ Novo Cliente</button>
    </div>
  </div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>ID / Nome</th><th>Status</th><th class="col-bot">Bot</th><th class="col-pfx">Prefixo</th><th class="col-exp">Expira</th><th>Ações</th></tr></thead>
      <tbody id="tbody"><tr><td colspan="6"><div class="empty"><div class="empty-icon">⏳</div><div>Carregando...</div></div></td></tr></tbody>
    </table>
  </div>
</div>

<!-- ─────────────── TAB: DONOS ─────────────── -->
<div id="tab-donos" style="display:none">
  <div class="section-header">
    <div class="section-title">👑 Donos do Sistema</div>
    <button class="btn btn-primary" onclick="openAddOwner()">＋ Adicionar Dono</button>
  </div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>Usuário / Nome</th><th>Cargo</th><th>Ações</th></tr></thead>
      <tbody id="owners-body"><tr><td colspan="3"><div class="empty"><div class="empty-icon">⏳</div><div>Carregando...</div></div></td></tr></tbody>
    </table>
  </div>
</div>

</main>

<!-- ─── Modal: Add Customer ─── -->
<div class="overlay" id="m-add">
  <div class="modal">
    <button class="modal-close" onclick="close_('m-add')">✕</button>
    <div class="modal-title">➕ Novo Cliente</div>
    <form onsubmit="submitAdd(event)">
      <div class="form-group"><label class="form-label">ID único (sem espaços)</label>
        <input id="a-id" placeholder="ex: cliente2" required pattern="[a-zA-Z0-9_-]+"></div>
      <div class="form-group"><label class="form-label">Nome do Cliente</label>
        <input id="a-nome" placeholder="ex: João Silva" required></div>
      <div class="form-group"><label class="form-label">Token do Bot Discord</label>
        <div class="pwd-wrap">
          <input type="password" id="a-token" placeholder="MTU..." required autocomplete="new-password" oninput="updateInviteLink('a',this.value)">
          <button type="button" class="pwd-toggle" onclick="togglePwd('a-token',this)" title="Mostrar/ocultar token">👁</button>
        </div></div>
      <div class="form-group" id="a-invite-wrap" style="display:none">
        <label class="form-label">🔗 Link para adicionar o bot ao servidor</label>
        <div style="display:flex;gap:8px;align-items:center">
          <input id="a-invite-url" readonly style="font-size:.78rem;color:#22d3ee;cursor:pointer" onclick="this.select()">
          <a id="a-invite-btn" href="#" target="_blank" rel="noopener"
             class="btn btn-primary btn-sm" style="white-space:nowrap;flex-shrink:0">Adicionar ↗</a>
        </div>
      </div>
      <div class="form-group"><label class="form-label">Prefixo padrão</label>
        <input id="a-prefix" placeholder="n!" maxlength="10" value="n!"></div>
      <div class="form-group"><label class="form-label">Data de Expiração</label>
        <input type="date" id="a-expira" required></div>
      <div class="form-group">
        <label class="check-row"><input type="checkbox" id="a-ativo" checked><span>Ativar imediatamente</span></label>
      </div>
      <div style="display:flex;gap:10px;justify-content:flex-end">
        <button type="button" class="btn btn-ghost" onclick="close_('m-add')">Cancelar</button>
        <button type="submit" class="btn btn-primary">Adicionar</button>
      </div>
    </form>
  </div>
</div>

<!-- ─── Modal: Edit Customer ─── -->
<div class="overlay" id="m-edit">
  <div class="modal">
    <button class="modal-close" onclick="close_('m-edit')">✕</button>
    <div class="modal-title">✏️ <span id="e-title"></span></div>
    <form onsubmit="submitEdit(event)">
      <input type="hidden" id="e-cid">
      <div class="form-group"><label class="form-label">Nome</label><input id="e-nome" required></div>
      <div class="form-group"><label class="form-label">Token do Bot Discord</label>
        <div class="pwd-wrap">
          <input type="password" id="e-token" required autocomplete="new-password">
          <button type="button" class="pwd-toggle" onclick="togglePwd('e-token',this)" title="Mostrar/ocultar token">👁</button>
        </div></div>
      <div class="form-group">
        <label class="form-label">Application ID <span style="color:var(--muted);font-weight:400;text-transform:none">(encontre em <a href="https://discord.com/developers/applications" target="_blank" style="color:#22d3ee">discord.com/developers</a> → seu app → "Application ID")</span></label>
        <input id="e-appid" placeholder="ex: 1234567890123456789" pattern="[0-9]+" oninput="onAppIdInput('e',this.value)">
      </div>
      <div class="form-group" id="e-invite-wrap" style="display:none">
        <label class="form-label">🔗 Link para adicionar o bot ao servidor</label>
        <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
          <input id="e-guild-id" placeholder="ID do servidor do cliente (para restringir o link)"
            pattern="[0-9]*" title="Apenas números" style="font-size:.82rem"
            oninput="onGuildIdInput('e')">
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          <input id="e-invite-url" readonly style="font-size:.78rem;color:#22d3ee;cursor:pointer" onclick="this.select()">
          <a id="e-invite-btn" href="#" target="_blank" rel="noopener"
             class="btn btn-primary btn-sm" style="white-space:nowrap;flex-shrink:0">Adicionar ↗</a>
        </div>
        <div style="font-size:.75rem;color:var(--muted);margin-top:4px">
          💡 Preencha o ID acima para gerar um link que só adiciona naquele servidor.
        </div>
        <div style="margin-top:8px">
          <a id="e-hype-invite-btn" href="#" target="_blank" rel="noopener"
             class="btn btn-sm" style="background:#5865F2;color:#fff;white-space:nowrap;width:100%;text-align:center;display:block">
            🎮 Adicionar ao servidor HYPE (emojis) ↗
          </a>
        </div>
      </div>
      <div class="form-group"><label class="form-label">Data de Expiração</label><input type="date" id="e-expira" required></div>
      <div class="form-group">
        <label class="check-row"><input type="checkbox" id="e-ativo"><span>Ativo</span></label>
      </div>
      <div style="display:flex;gap:10px;justify-content:flex-end">
        <button type="button" class="btn btn-ghost" onclick="close_('m-edit')">Cancelar</button>
        <button type="submit" class="btn btn-primary">Salvar</button>
      </div>
    </form>
    <hr class="divider">
    <div class="sub-label">🔧 Prefixos por Servidor</div>
    <div id="guild-wrap" style="margin-top:8px">
      <div style="color:var(--muted);font-size:.85rem">Nenhum servidor registrado.<br><small>Aparece após a primeira interação com o bot.</small></div>
    </div>
    <hr class="divider">
    <div class="sub-label">📝 Descrição do Bot <span style="color:var(--muted);font-weight:400;font-size:.76rem;text-transform:none">— aparece no perfil do bot (máx. 400 caracteres)</span></div>
    <div style="margin-top:8px" id="bio-wrap">
      <textarea id="e-bio" rows="3" maxlength="400"
        placeholder="Ex: Bot de gerenciamento do servidor NATA®"
        oninput="document.getElementById('e-bio-count').textContent=this.value.length+'/400'"
        style="width:100%;resize:vertical;font-size:.85rem;box-sizing:border-box"></textarea>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:5px">
        <span id="e-bio-count" style="font-size:.75rem;color:var(--muted)">0/400</span>
        <button type="button" class="btn btn-primary btn-sm"
          onclick="saveBio(document.getElementById('e-cid').value)">Salvar Descrição</button>
      </div>
    </div>
    <hr class="divider">
    <div class="sub-label">📋 Log Recente</div>
    <div class="log-box" id="e-log">(sem log)</div>
  </div>
</div>

<!-- ─── Modal: Limpar por Servidor ─── -->
<div class="overlay" id="m-clean-guild">
  <div class="modal" style="max-width:560px">
    <button class="modal-close" onclick="close_('m-clean-guild')">✕</button>
    <div class="modal-title">🗑️ Excluir bots fora de um servidor</div>
    <div id="cg-step1">
      <p style="color:var(--muted);font-size:.88rem;margin-bottom:16px">
        Informe o ID do servidor. Os bots que <strong>não estiverem</strong> nele serão listados para exclusão.
      </p>
      <div class="form-group">
        <label class="form-label">ID do Servidor Discord</label>
        <input id="cg-guild-id" placeholder="ex: 1506247261011578890" pattern="[0-9]+" style="font-family:monospace">
      </div>
      <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:8px">
        <button type="button" class="btn btn-ghost" onclick="close_('m-clean-guild')">Cancelar</button>
        <button type="button" class="btn btn-primary" onclick="checkBotsInGuild(this)">🔍 Verificar</button>
      </div>
    </div>
    <div id="cg-step2" style="display:none">
      <p style="font-size:.85rem;color:var(--muted);margin-bottom:12px" id="cg-summary"></p>
      <div id="cg-list" style="margin-bottom:16px"></div>
      <div style="display:flex;gap:10px;justify-content:flex-end">
        <button type="button" class="btn btn-ghost" onclick="cgBack()">← Voltar</button>
        <button type="button" class="btn btn-danger" id="cg-confirm-btn" onclick="confirmCleanGuild(this)">🗑️ Excluir selecionados</button>
      </div>
    </div>
  </div>
</div>

<!-- ─── Modal: Delete Customer ─── -->
<div class="overlay" id="m-del">
  <div class="modal" style="max-width:400px;text-align:center">
    <button class="modal-close" onclick="close_('m-del')">✕</button>
    <div style="font-size:2.5rem;margin-bottom:12px">⚠️</div>
    <div class="modal-title" style="justify-content:center">Remover Cliente</div>
    <p style="color:var(--muted);font-size:.9rem;margin-bottom:20px">
      Remover <strong id="del-name"></strong>?<br>
      <small>Os arquivos em <code>clientes/</code> serão mantidos.</small>
    </p>
    <input type="hidden" id="del-cid">
    <div style="display:flex;gap:10px;justify-content:center">
      <button class="btn btn-ghost" onclick="close_('m-del')">Cancelar</button>
      <button class="btn btn-danger" onclick="doDelete()">Remover</button>
    </div>
  </div>
</div>

<!-- ─── Modal: Retirar Bot ─── -->
<div class="overlay" id="m-leave">
  <div class="modal" style="max-width:420px;text-align:center">
    <button class="modal-close" onclick="close_('m-leave')">✕</button>
    <div style="font-size:2.8rem;margin-bottom:10px">🚪</div>
    <div class="modal-title" style="justify-content:center">Retirar Bot do Servidor</div>
    <p style="color:var(--muted);font-size:.88rem;margin-bottom:8px">
      Tem certeza que deseja retirar o bot do servidor<br>
      <strong id="leave-name" style="color:var(--text);font-size:.95rem"></strong>?
    </p>
    <p style="color:rgba(239,68,68,.8);font-size:.78rem;margin-bottom:22px;background:rgba(239,68,68,.08);
      border:1px solid rgba(239,68,68,.2);border-radius:8px;padding:8px 12px">
      ⚠️ O bot vai sair imediatamente e deixará de responder nesse servidor.
    </p>
    <input type="hidden" id="leave-cid">
    <input type="hidden" id="leave-gid">
    <div style="display:flex;gap:10px;justify-content:center">
      <button class="btn btn-ghost" onclick="close_('m-leave')">Cancelar</button>
      <button class="btn btn-danger" onclick="doLeaveGuild()" style="gap:6px">🚪 Retirar Bot</button>
    </div>
  </div>
</div>

<!-- ─── Modal: Add Owner ─── -->
<div class="overlay" id="m-add-owner">
  <div class="modal">
    <button class="modal-close" onclick="close_('m-add-owner')">✕</button>
    <div class="modal-title">👑 Adicionar Dono</div>
    <form onsubmit="submitAddOwner(event)">
      <div class="form-group"><label class="form-label">Nome de exibição</label>
        <input id="ao-nome" placeholder="ex: João Parceiro" required></div>
      <div class="form-group"><label class="form-label">Nome de usuário (login)</label>
        <input id="ao-id" placeholder="ex: joao" required pattern="[a-zA-Z0-9_-]+" title="Apenas letras, números, - e _"></div>
      <div class="form-group"><label class="form-label">Senha</label>
        <input type="password" id="ao-pwd" required minlength="6" placeholder="Mínimo 6 caracteres"></div>
      <div class="form-group"><label class="form-label">Confirmar senha</label>
        <input type="password" id="ao-confirm" required></div>
      <div class="form-group"><label class="form-label">Cargo</label>
        <select id="ao-role">
          <option value="admin">👤 Admin — gerencia clientes</option>
          <option value="super">🔰 Super Admin — gerencia tudo</option>
        </select>
      </div>
      <div style="display:flex;gap:10px;justify-content:flex-end">
        <button type="button" class="btn btn-ghost" onclick="close_('m-add-owner')">Cancelar</button>
        <button type="submit" class="btn btn-primary">Adicionar</button>
      </div>
    </form>
  </div>
</div>

<!-- ─── Modal: Change Password ─── -->
<div class="overlay" id="m-pwd">
  <div class="modal" style="max-width:420px">
    <button class="modal-close" onclick="close_('m-pwd')">✕</button>
    <div class="modal-title">🔑 Alterar Senha — <span id="pwd-name"></span></div>
    <form onsubmit="submitPwd(event)">
      <input type="hidden" id="pwd-oid">
      <div class="form-group" id="pwd-old-wrap">
        <label class="form-label">Senha atual</label>
        <input type="password" id="pwd-old" placeholder="Sua senha atual">
      </div>
      <div class="form-group"><label class="form-label">Nova senha</label>
        <input type="password" id="pwd-new" required minlength="6" placeholder="Mínimo 6 caracteres"></div>
      <div class="form-group"><label class="form-label">Confirmar nova senha</label>
        <input type="password" id="pwd-confirm" required></div>
      <div style="display:flex;gap:10px;justify-content:flex-end">
        <button type="button" class="btn btn-ghost" onclick="close_('m-pwd')">Cancelar</button>
        <button type="submit" class="btn btn-primary">Alterar Senha</button>
      </div>
    </form>
  </div>
</div>

<!-- ─── Modal: Delete Owner ─── -->
<div class="overlay" id="m-del-owner">
  <div class="modal" style="max-width:400px;text-align:center">
    <button class="modal-close" onclick="close_('m-del-owner')">✕</button>
    <div style="font-size:2.5rem;margin-bottom:12px">⚠️</div>
    <div class="modal-title" style="justify-content:center">Remover Dono</div>
    <p style="color:var(--muted);font-size:.9rem;margin-bottom:20px">
      Remover <strong id="del-owner-name"></strong> do sistema?
    </p>
    <input type="hidden" id="del-owner-id">
    <div style="display:flex;gap:10px;justify-content:center">
      <button class="btn btn-ghost" onclick="close_('m-del-owner')">Cancelar</button>
      <button class="btn btn-danger" onclick="doDeleteOwner()">Remover</button>
    </div>
  </div>
</div>

<!-- Toasts -->
<div class="toast-wrap" id="toasts"></div>

<script>
/* inject current user from server */
const ME = {{ me | tojson }};
const IS_SUPER = ME.role === 'super';

/* init header */
document.getElementById('hdr-avatar').textContent = (ME.nome||'?')[0].toUpperCase();
document.getElementById('hdr-name').textContent   = ME.nome;
if (IS_SUPER) document.getElementById('btn-tab-donos').style.display = '';

/* ── Tabs ── */
function showTab(name) {
  ['clientes','donos'].forEach(t => {
    document.getElementById('tab-'+t).style.display      = t===name ? '' : 'none';
    document.getElementById('btn-tab-'+t).classList.toggle('active', t===name);
  });
  if (name === 'donos') loadOwners();
}

/* ── Modals ── */
function open_(id)  { document.getElementById(id).classList.add('open'); }
function close_(id) { document.getElementById(id).classList.remove('open'); }
function togglePwd(id, btn) {
  const el = document.getElementById(id);
  if (el.type === 'password') { el.type = 'text'; btn.textContent = '🙈'; }
  else { el.type = 'password'; btn.textContent = '👁'; }
}
document.querySelectorAll('.overlay').forEach(el =>
  el.addEventListener('click', e => { if(e.target===el) close_(el.id); })
);

/* ═══════════════════════════════════════════════════
   CUSTOMERS
═══════════════════════════════════════════════════ */
let _list = [];

async function load() {
  try {
    const r = await fetch('/api/customers');
    if (r.status === 401) { location.href='/login'; return; }
    _list = await r.json();
    renderStats(); renderTable();
  } catch(e) { toast('❌ Erro ao carregar','error'); }
}

function renderStats() {
  document.getElementById('s-total').textContent    = _list.length;
  document.getElementById('s-active').textContent   = _list.filter(c=>c.status==='active').length;
  document.getElementById('s-bots').textContent     = _list.filter(c=>c.bot_running).length;
  document.getElementById('s-expired').textContent  = _list.filter(c=>c.status==='expired').length;
  document.getElementById('s-disabled').textContent = _list.filter(c=>c.status==='disabled').length;
}

function renderTable() {
  const tbody = document.getElementById('tbody');
  if (!_list.length) {
    tbody.innerHTML = `<tr><td colspan="6"><div class="empty">
      <div class="empty-icon">🤖</div>
      <div style="margin-bottom:16px">Nenhum cliente cadastrado</div>
      <button class="btn btn-primary" onclick="openAdd()">Adicionar primeiro cliente</button>
    </div></td></tr>`;
    return;
  }
  const lb = {active:'Ativo',expired:'Expirado',disabled:'Desativado'};
  tbody.innerHTML = _list.map(c => {
    const canRun = c.status === 'active';
    const conflictWarn = c.token_conflict
      ? `<div style="color:#f59e0b;font-size:.72rem;margin-top:3px" title="Token idêntico ao cliente '${esc(c.token_conflict)}' — isso causa conflito de conexão Discord">⚠️ Token duplicado com <b>${esc(c.token_conflict)}</b></div>`
      : '';
    const badTokenWarn = c.bad_token
      ? `<div style="color:#ef4444;font-size:.72rem;margin-top:3px">🔑 Token inválido — atualize o token e reinicie</div>`
      : '';
    const botBadge = !canRun
      ? '<span class="badge disabled">⏸️ Inativo</span>'
      : c.bad_token
        ? '<span class="badge expired">🔑 Token inválido</span>'
        : c.bot_running
          ? '<span class="badge bot-on">🟢 Online</span>'
          : '<span class="badge bot-off">🔴 Offline</span>';
    const botBtns = !canRun ? '' : c.bot_running
      ? `<button class="btn btn-warn btn-sm" title="Reiniciar bot" onclick="botAction('${esc(c.id)}','restart')">🔄</button>
         <button class="btn btn-danger btn-sm" title="Parar bot" onclick="botAction('${esc(c.id)}','stop')">⏹</button>`
      : `<button class="btn btn-success btn-sm" onclick="botAction('${esc(c.id)}','start')">▶ Iniciar</button>`;
    return `
    <tr>
      <td><div style="font-weight:700">${esc(c.id)}</div>
          <div style="font-size:.78rem;color:var(--muted)">${esc(c.nome||'')}</div>
          ${c.bot_name ? `<div style="font-size:.72rem;color:#22d3ee;margin-top:2px">@${esc(c.bot_name)}</div>` : ''}
          ${conflictWarn}${badTokenWarn}</td>
      <td><span class="badge ${c.status}">${lb[c.status]||c.status}</span></td>
      <td class="col-bot">${botBadge}</td>
      <td class="col-pfx"><span class="prefix-chip">${esc(c.prefix||'n!')}</span></td>
      <td class="col-exp" style="color:var(--muted);font-size:.85rem">${esc(c.expira||'—')}</td>
      <td><div class="actions">
        <button class="btn btn-ghost btn-sm" onclick="openEdit('${esc(c.id)}')">✏️</button>
        ${botBtns}
        <button class="btn btn-danger btn-sm" onclick="openDel('${esc(c.id)}','${esc(c.nome||c.id)}')">🗑️</button>
      </div></td>
    </tr>`;
  }).join('');
}

async function botAction(cid, action) {
  const msgs = {start:'▶ Bot iniciado!', stop:'⏹ Bot parado', restart:'🔄 Bot reiniciado!'};
  const r = await api('POST', `/api/customers/${cid}/bot/${action}`);
  if (r.ok) {
    toast(msgs[action]||'✅ OK','success');
    if (r.warning) toast('⚠️ '+r.warning,'warn');
    load();
  } else {
    toast('❌ '+(r.error||'Erro'),'error');
  }
}

/* Add */
function openAdd() {
  ['a-id','a-nome','a-token'].forEach(id => document.getElementById(id).value='');
  document.getElementById('a-prefix').value = 'n!';
  document.getElementById('a-ativo').checked = true;
  document.getElementById('a-invite-wrap').style.display = 'none';
  const d = new Date(); d.setFullYear(d.getFullYear()+1);
  document.getElementById('a-expira').value = d.toISOString().split('T')[0];
  open_('m-add');
}

async function submitAdd(e) {
  e.preventDefault();
  const r = await api('POST','/api/customers',{
    id:   document.getElementById('a-id').value.trim(),
    nome: document.getElementById('a-nome').value.trim(),
    token:document.getElementById('a-token').value.trim(),
    default_prefix: document.getElementById('a-prefix').value.trim()||'n!',
    expira:document.getElementById('a-expira').value,
    ativo: document.getElementById('a-ativo').checked,
  });
  if(r.ok){toast('✅ Cliente adicionado!','success');close_('m-add');load();}
  else toast('❌ '+(r.error||'Erro'),'error');
}

/* Edit */
async function openEdit(cid) {
  const c = _list.find(x=>x.id===cid); if(!c) return;
  document.getElementById('e-cid').value   = cid;
  document.getElementById('e-title').textContent = cid;
  document.getElementById('e-nome').value  = c.nome||'';
  document.getElementById('e-token').value = c.token||'';
  document.getElementById('e-appid').value    = c.app_id||'';
  document.getElementById('e-guild-id').value = c.guild_id||'';
  document.getElementById('e-expira').value   = c.expira||'';
  document.getElementById('e-ativo').checked  = c.ativo!==false;
  // Reset invite link while loading
  document.getElementById('e-invite-wrap').style.display = 'none';
  document.getElementById('e-log').textContent   = '⏳ carregando...';
  document.getElementById('guild-wrap').innerHTML = '⏳ carregando...';
  open_('m-edit');
  // Show invite link if app_id already saved, otherwise try server endpoint
  if (c.app_id) {
    showInviteLink('e', c.app_id);
  } else {
    fetch('/api/customers/'+cid+'/bot/invite').then(r=>r.json()).then(d=>{
      if (d.ok && d.url) {
        document.getElementById('e-invite-url').value = d.url;
        document.getElementById('e-invite-btn').href  = d.url;
        document.getElementById('e-invite-wrap').style.display = '';
        // Auto-fill the app_id field too
        if (d.app_id) {
          document.getElementById('e-appid').value = d.app_id;
          showInviteLink('e', d.app_id);
        }
      }
    }).catch(()=>{});
  }
  fetch('/api/customers/'+cid+'/log').then(r=>r.json()).then(d=>{
    document.getElementById('e-log').textContent = d.log||'(sem log)';
  });
  fetch('/api/customers/'+cid+'/settings').then(r=>r.json()).then(d=>renderGuilds(cid,d.settings||{}));
  // Carrega descrição atual do bot
  const bioEl = document.getElementById('e-bio');
  const bioCount = document.getElementById('e-bio-count');
  bioEl.value = ''; bioEl.disabled = true; bioEl.placeholder = '⏳ carregando...';
  bioCount.textContent = '0/400';
  fetch('/api/customers/'+cid+'/bot/profile').then(r=>r.json()).then(d=>{
    bioEl.disabled = false;
    if(d.ok){
      bioEl.value = d.description||'';
      bioCount.textContent = bioEl.value.length+'/400';
      bioEl.placeholder = 'Ex: Bot de gerenciamento do servidor NATA®';
    } else {
      bioEl.placeholder = 'Não foi possível carregar — verifique o token';
    }
  }).catch(()=>{ bioEl.disabled=false; bioEl.placeholder='Erro ao carregar'; });
}

async function saveBio(cid){
  const btn = event && event.target;
  const orig = btn ? btn.textContent : '';
  if(btn){ btn.disabled=true; btn.textContent='⏳'; }
  const desc = document.getElementById('e-bio').value.trim();
  const r = await api('POST','/api/customers/'+cid+'/bot/description',{description:desc});
  if(btn){ btn.disabled=false; btn.textContent=orig; }
  if(r.ok){
    if(r.warning) toast('⏳ '+r.warning,'warn');
    else toast('✅ Descrição atualizada!','success');
  } else toast('❌ '+(r.error||'Erro ao salvar descrição'),'error');
}

function renderGuilds(cid, settings) {
  const wrap = document.getElementById('guild-wrap');
  // Filtra o guild "0" (configuração padrão interna, não é servidor real)
  const entries = Object.entries(settings).filter(([gid]) => gid !== '0');
  if(!entries.length){
    wrap.innerHTML='<div style="color:var(--muted);font-size:.85rem">Nenhum servidor registrado ainda.<br><small>Aparece após a primeira interação com o bot.</small></div>';
    return;
  }
  let h='<table class="guild-table"><thead><tr><th>Servidor</th><th>Prefixo</th><th></th></tr></thead><tbody>';
  for(const[gid,gs] of entries){
    const p=(typeof gs==='object'&&gs.prefix)?gs.prefix:'n!';
    const name=(typeof gs==='object'&&gs.guild_name)?gs.guild_name:'';
    const label = name ? `<span style="color:var(--text)">${esc(name)}</span><br><small style="color:var(--muted);font-family:monospace">${esc(gid)}</small>` : `<span style="font-family:monospace;font-size:.78rem">${esc(gid)}</span>`;
    h+=`<tr>
      <td>${label}</td>
      <td><input id="pfx-${esc(gid)}" value="${esc(p)}" style="max-width:90px" maxlength="10"></td>
      <td style="display:flex;gap:6px;align-items:center">
        <button type="button" class="btn btn-primary btn-sm" onclick="savePfx('${esc(cid)}','${esc(gid)}')">Salvar</button>
        <button type="button" class="btn btn-danger btn-sm" onclick="leaveGuild('${esc(cid)}','${esc(gid)}','${esc(name||gid)}')" title="Retirar bot deste servidor">🚪 Retirar Bot</button>
      </td>
    </tr>`;
  }
  h+='</tbody></table>';
  wrap.innerHTML=h;
}

async function savePfx(cid,gid){
  const el=document.getElementById('pfx-'+gid); if(!el) return;
  const prefix=el.value.trim();
  if(!prefix||prefix.length>10||prefix.includes(' ')){toast('❌ Prefixo inválido','error');return;}
  const r=await api('POST',`/api/customers/${cid}/prefix`,{guild_id:gid,prefix});
  if(r.ok) toast(`✅ Prefixo atualizado!`,'success');
  else toast('❌ '+(r.error||'Erro'),'error');
}

function leaveGuild(cid, gid, name) {
  document.getElementById('leave-cid').value = cid;
  document.getElementById('leave-gid').value = gid;
  document.getElementById('leave-name').textContent = name || gid;
  open_('m-leave');
}

/* ═══════════════════════════════════════════════════
   LIMPAR POR SERVIDOR
═══════════════════════════════════════════════════ */
function openCleanGuild() {
  document.getElementById('cg-guild-id').value = '';
  document.getElementById('cg-step1').style.display = '';
  document.getElementById('cg-step2').style.display = 'none';
  open_('m-clean-guild');
}
function cgBack() {
  document.getElementById('cg-step1').style.display = '';
  document.getElementById('cg-step2').style.display = 'none';
}
let _cgToDelete = [];
async function checkBotsInGuild(btn) {
  const gid = document.getElementById('cg-guild-id').value.trim();
  if (!/^\\d{10,}$/.test(gid)) { toast('❌ ID de servidor inválido','error'); return; }
  btn.disabled = true; btn.textContent = '⏳ Verificando...';
  const r = await api('POST', '/api/admin/check-guild-membership', {guild_id: gid});
  btn.disabled = false; btn.textContent = '🔍 Verificar';
  if (!r.ok) { toast('❌ ' + (r.error||'Erro'), 'error'); return; }
  _cgToDelete = r.not_in_guild || [];
  const inGuild = r.in_guild || [];
  const sumEl = document.getElementById('cg-summary');
  sumEl.innerHTML = `<strong style="color:var(--green)">${inGuild.length}</strong> bot(s) estão no servidor &nbsp;|&nbsp; <strong style="color:var(--red)">${_cgToDelete.length}</strong> NÃO estão`;
  const listEl = document.getElementById('cg-list');
  if (!_cgToDelete.length) {
    listEl.innerHTML = '<div style="color:var(--green);font-size:.88rem">✅ Todos os bots estão neste servidor. Nada para excluir.</div>';
    document.getElementById('cg-confirm-btn').style.display = 'none';
  } else {
    document.getElementById('cg-confirm-btn').style.display = '';
    listEl.innerHTML = '<div style="font-size:.78rem;color:var(--muted);margin-bottom:8px">Bots que serão <strong style="color:var(--red)">excluídos</strong>:</div>'
      + _cgToDelete.map(c => `
        <div style="display:flex;align-items:center;gap:8px;padding:6px 10px;background:rgba(239,68,68,.08);
             border:1px solid rgba(239,68,68,.2);border-radius:8px;margin-bottom:6px">
          <input type="checkbox" id="cg-chk-${esc(c.id)}" checked style="width:16px;height:16px;accent-color:var(--red)">
          <label for="cg-chk-${esc(c.id)}" style="cursor:pointer;flex:1">
            <span style="font-weight:700">${esc(c.id)}</span>
            <span style="color:var(--muted);font-size:.8rem;margin-left:6px">${esc(c.nome||'')}</span>
          </label>
          <span style="font-size:.72rem;color:var(--muted)">${esc(c.reason||'')}</span>
        </div>`).join('');
  }
  document.getElementById('cg-step1').style.display = 'none';
  document.getElementById('cg-step2').style.display = '';
}
async function confirmCleanGuild(btn) {
  const toDelete = _cgToDelete.filter(c => document.getElementById('cg-chk-'+c.id)?.checked);
  if (!toDelete.length) { toast('Nenhum bot selecionado','warn'); return; }
  btn.disabled = true; btn.textContent = '⏳ Excluindo...';
  let ok = 0, fail = 0;
  for (const c of toDelete) {
    const r = await api('DELETE', '/api/customers/'+c.id);
    if (r.ok) ok++; else fail++;
  }
  btn.disabled = false; btn.textContent = '🗑️ Excluir selecionados';
  close_('m-clean-guild');
  toast(`✅ ${ok} excluído(s)${fail ? ' | ❌ '+fail+' falha(s)' : ''}`, ok ? 'success' : 'error');
  load();
}

async function syncAppIcon(cid, btn) {
  const msg = document.getElementById('e-sync-icon-msg');
  btn.disabled = true;
  btn.textContent = '⏳ Atualizando...';
  msg.style.color = 'var(--muted)';
  msg.textContent = 'Enviando ícone ao Discord...';
  const r = await api('POST', `/api/customers/${cid}/apply-avatar`);
  btn.disabled = false;
  btn.textContent = '🔄 Atualizar ícone OAuth2';
  if (r.ok) {
    msg.style.color = 'var(--green)';
    msg.textContent = '✅ Ícone atualizado! Aguarde alguns segundos e recarregue o link de convite.';
  } else {
    msg.style.color = 'var(--red)';
    msg.textContent = '❌ ' + (r.error || 'Falha ao atualizar.');
  }
}

async function doLeaveGuild() {
  const cid  = document.getElementById('leave-cid').value;
  const gid  = document.getElementById('leave-gid').value;
  const name = document.getElementById('leave-name').textContent;
  close_('m-leave');
  const r = await api('POST', `/api/customers/${cid}/bot/leave-guild`, {guild_id: gid});
  if (r.ok) {
    toast(`✅ Bot saiu do servidor "${name}"`, 'success');
    fetch('/api/customers/'+cid+'/settings').then(res=>res.json()).then(d=>renderGuilds(cid, d.settings||{}));
  } else {
    toast('❌ ' + (r.error || 'Erro ao sair do servidor'), 'error');
  }
}

async function submitEdit(e){
  e.preventDefault();
  const cid=document.getElementById('e-cid').value;
  const r=await api('PUT','/api/customers/'+cid,{
    nome:     document.getElementById('e-nome').value.trim(),
    token:    document.getElementById('e-token').value.trim(),
    app_id:   document.getElementById('e-appid').value.trim(),
    guild_id: document.getElementById('e-guild-id').value.trim(),
    expira:   document.getElementById('e-expira').value,
    ativo:    document.getElementById('e-ativo').checked,
  });
  if(r.ok){toast('✅ Salvo!','success');close_('m-edit');load();}
  else toast('❌ '+(r.error||'Erro'),'error');
}

/* Delete customer */
function openDel(cid,nome){
  document.getElementById('del-cid').value=cid;
  document.getElementById('del-name').textContent=nome;
  open_('m-del');
}
async function doDelete(){
  const cid=document.getElementById('del-cid').value;
  const r=await api('DELETE','/api/customers/'+cid);
  if(r.ok){toast('✅ Removido','success');close_('m-del');load();}
  else toast('❌ '+(r.error||'Erro'),'error');
}

/* ═══════════════════════════════════════════════════
   OWNERS
═══════════════════════════════════════════════════ */
let _owners = [];

async function loadOwners() {
  const r = await fetch('/api/owners');
  if(!r.ok){ return; }
  _owners = await r.json();
  renderOwners();
}

function renderOwners() {
  const tbody = document.getElementById('owners-body');
  if(!_owners.length){
    tbody.innerHTML='<tr><td colspan="3"><div class="empty"><div class="empty-icon">👑</div><div>Nenhum dono cadastrado</div></div></td></tr>';
    return;
  }
  tbody.innerHTML = _owners.map(o => {
    const isMe = o.id === ME.id;
    const roleBadge = o.role==='super'
      ? '<span class="badge super">🔰 Super Admin</span>'
      : '<span class="badge admin">👤 Admin</span>';
    const youTag = isMe ? '<span class="you-tag">você</span>' : '';
    const canRemove = IS_SUPER && !isMe;
    return `<tr>
      <td>
        <div style="font-weight:700">${esc(o.id)}${youTag}</div>
        <div style="font-size:.78rem;color:var(--muted)">${esc(o.nome||'')}</div>
      </td>
      <td>${roleBadge}</td>
      <td><div class="actions">
        <button class="btn btn-warn btn-sm" onclick="openPwd('${esc(o.id)}','${esc(o.nome||o.id)}',${isMe})">🔑 Senha</button>
        ${canRemove ? `<button class="btn btn-danger btn-sm" onclick="openDelOwner('${esc(o.id)}','${esc(o.nome||o.id)}')">🗑️</button>` : ''}
      </div></td>
    </tr>`;
  }).join('');
}

/* Add owner */
function openAddOwner(){
  ['ao-nome','ao-id','ao-pwd','ao-confirm'].forEach(id=>document.getElementById(id).value='');
  document.getElementById('ao-role').value='admin';
  open_('m-add-owner');
}

async function submitAddOwner(e){
  e.preventDefault();
  const pwd=document.getElementById('ao-pwd').value;
  const confirm=document.getElementById('ao-confirm').value;
  if(pwd!==confirm){toast('❌ As senhas não coincidem','error');return;}
  const r=await api('POST','/api/owners',{
    id:   document.getElementById('ao-id').value.trim(),
    nome: document.getElementById('ao-nome').value.trim(),
    password: pwd,
    role: document.getElementById('ao-role').value,
  });
  if(r.ok){toast('✅ Dono adicionado!','success');close_('m-add-owner');loadOwners();}
  else toast('❌ '+(r.error||'Erro'),'error');
}

/* Change password */
function openPwd(oid, nome, isMe){
  document.getElementById('pwd-oid').value=oid;
  document.getElementById('pwd-name').textContent=nome;
  document.getElementById('pwd-old').value='';
  document.getElementById('pwd-new').value='';
  document.getElementById('pwd-confirm').value='';
  /* show old-password field only when changing own password */
  document.getElementById('pwd-old-wrap').style.display = isMe ? '' : 'none';
  document.getElementById('pwd-old').required = isMe;
  open_('m-pwd');
}

async function submitPwd(e){
  e.preventDefault();
  const oid=document.getElementById('pwd-oid').value;
  const isMe=oid===ME.id;
  const newPwd=document.getElementById('pwd-new').value;
  const confirm=document.getElementById('pwd-confirm').value;
  if(newPwd!==confirm){toast('❌ As senhas não coincidem','error');return;}
  const body={password:newPwd};
  if(isMe) body.old_password=document.getElementById('pwd-old').value;
  const r=await api('PUT','/api/owners/'+oid+'/password',body);
  if(r.ok){toast('✅ Senha alterada!','success');close_('m-pwd');}
  else toast('❌ '+(r.error||'Erro'),'error');
}

/* Delete owner */
function openDelOwner(oid,nome){
  document.getElementById('del-owner-id').value=oid;
  document.getElementById('del-owner-name').textContent=nome;
  open_('m-del-owner');
}
async function doDeleteOwner(){
  const oid=document.getElementById('del-owner-id').value;
  const r=await api('DELETE','/api/owners/'+oid);
  if(r.ok){toast('✅ Dono removido','success');close_('m-del-owner');loadOwners();}
  else toast('❌ '+(r.error||'Erro'),'error');
}

/* ── Helpers ── */
async function api(method,url,body){
  try{
    const opts={method,headers:{'Content-Type':'application/json'}};
    if(body) opts.body=JSON.stringify(body);
    const r=await fetch(url,opts);
    if(r.status===401){location.href='/login';return{ok:false};}
    return await r.json();
  }catch(e){return{ok:false,error:String(e)};}
}

function toast(msg,type='success'){
  const w=document.getElementById('toasts');
  const el=document.createElement('div');
  el.className='toast '+type;el.textContent=msg;
  w.appendChild(el);setTimeout(()=>el.remove(),3500);
}

/* ── Invite link helpers ── */
const HYPE_GUILD_ID = '835695342291779645';

function buildInviteUrl(appId) {
  return `https://discord.com/oauth2/authorize?client_id=${appId}&permissions=8&scope=bot%20applications.commands`;
}

function buildRestrictedInviteUrl(appId, guildId) {
  return `https://discord.com/oauth2/authorize?client_id=${appId}&permissions=8&scope=bot%20applications.commands&guild_id=${guildId}&disable_guild_select=true`;
}

function buildHypeInviteUrl(appId) {
  return buildRestrictedInviteUrl(appId, HYPE_GUILD_ID);
}

function showInviteLink(prefix, appId) {
  const wrap = document.getElementById(prefix+'-invite-wrap');
  if (!appId || !/^\\d{10,}$/.test(appId)) { wrap.style.display='none'; return; }

  // Se o guild_id do cliente estiver preenchido, restringe o link a esse servidor
  const guildInput = document.getElementById(prefix+'-guild-id');
  const clientGuildId = guildInput ? guildInput.value.trim() : '';
  const mainUrl = (clientGuildId && /^\\d{10,}$/.test(clientGuildId))
    ? buildRestrictedInviteUrl(appId, clientGuildId)
    : buildInviteUrl(appId);
  document.getElementById(prefix+'-invite-url').value = mainUrl;
  document.getElementById(prefix+'-invite-btn').href  = mainUrl;

  // Botão HYPE → sempre restrito ao servidor HYPE
  const hypeBtn = document.getElementById(prefix+'-hype-invite-btn');
  if (hypeBtn) hypeBtn.href = buildHypeInviteUrl(appId);

  wrap.style.display = '';
}

function onAppIdInput(prefix, val) {
  showInviteLink(prefix, val.trim());
}

// Atualiza o link quando o guild_id do cliente muda
function onGuildIdInput(prefix) {
  const appIdEl = document.getElementById(prefix+'-appid');
  if (appIdEl && appIdEl.value.trim()) showInviteLink(prefix, appIdEl.value.trim());
}

// kept for backwards compat (add modal token field still calls this)
function updateInviteLink(prefix, token) {
  if (!token) return;
  try {
    const b64 = token.split('.')[0].replace(/-/g,'+').replace(/_/g,'/');
    const pad = (4 - b64.length%4)%4;
    const clientId = atob(b64 + '='.repeat(pad));
    showInviteLink(prefix, clientId);
  } catch(e) {}
}

function esc(s){
  if(s==null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

async function restartAll(btn) {
  if (!confirm('Reiniciar TODOS os bots? Eles ficarão offline por alguns segundos.')) return;
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = '⏳ Reiniciando...';
  const r = await api('POST', '/api/bots/restart-all');
  btn.disabled = false;
  btn.textContent = orig;
  if (r.ok) {
    toast('✅ ' + r.started + ' bot(s) reiniciado(s)' + (r.failed ? ' · ❌ ' + r.failed + ' falha(s)' : ''), 'success');
    setTimeout(load, 3000);
  } else {
    toast('❌ ' + (r.error || 'Erro ao reiniciar'), 'error');
  }
}

async function applyAvatarAll(btn) {
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = '⏳ Aplicando...';
  const r = await api('POST', '/api/apply-avatar-all');
  btn.disabled = false;
  btn.textContent = orig;
  if (r.ok) toast('✅ Avatar reaplicado em ' + r.count + ' bot(s)', 'success');
  else toast('❌ ' + (r.error || 'Erro ao aplicar avatar'), 'error');
}

async function setAvatarFromUrl(btn) {
  const url = document.getElementById('avatarUrlInput').value.trim();
  if (!url) { toast('❌ Cole a URL da imagem primeiro', 'error'); return; }
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = '⏳ Baixando...';
  const r = await api('POST', '/api/set-avatar-from-url', { url });
  btn.disabled = false;
  btn.textContent = orig;
  if (r.ok) {
    toast('✅ Foto definida e aplicada em ' + r.count + ' bot(s)! (' + Math.round(r.bytes/1024) + ' KB)', 'success');
    // Atualiza a imagem do logo no header
    document.querySelectorAll('img[src="/bot-avatar.png"]').forEach(img => {
      img.src = '/bot-avatar.png?t=' + Date.now();
    });
  } else {
    toast('❌ ' + (r.error || 'Erro ao definir foto'), 'error');
  }
}

/* ── Init ── */
load();
setInterval(load, 30000);
</script>
</body>
</html>"""


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/setup", methods=["GET", "POST"])
def setup():
    cfg = load_config()
    if cfg.get("owners"):
        return redirect("/")
    error = None
    if request.method == "POST":
        nome     = request.form.get("nome", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm", "")
        if not nome or not username or not password:
            error = "Preencha todos os campos."
        elif password != confirm:
            error = "As senhas não coincidem."
        elif len(password) < 6:
            error = "Senha deve ter pelo menos 6 caracteres."
        else:
            h, salt = _hash(password)
            cfg["owners"] = [{
                "id":     username,
                "nome":   nome,
                "hash":   h,
                "salt":   salt,
                "role":   "super",
            }]
            save_config(cfg)
            session["uid"] = username
            return redirect("/")
    return render_template_string(SETUP_HTML, error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    cfg = load_config()
    if not cfg.get("owners"):
        return redirect("/setup")
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        owner = next((o for o in cfg["owners"] if o["id"] == username), None)
        if owner and _verify(password, owner["hash"], owner["salt"]):
            session["uid"] = username
            return redirect("/")
        error = "Usuário ou senha incorretos."
    return render_template_string(LOGIN_HTML, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/")
@login_required
def index():
    from flask import make_response
    owner = get_owner(session["uid"])
    me = {"id": owner["id"], "nome": owner["nome"], "role": owner.get("role", "admin")}
    resp = make_response(render_template_string(MAIN_HTML, me=me))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"]        = "no-cache"
    resp.headers["Expires"]       = "0"
    return resp


# ── Aplicar avatar padrão HYPE via REST (sem precisar do bot conectado) ───────

def _apply_default_avatar_to_token(token: str) -> bool:
    """Aplica o avatar/ícone padrão HYPE ao bot via REST API do Discord.

    Atualiza AMBOS:
    - /users/@me  → avatar do bot (aparece nas mensagens)
    - /applications/@me → ícone da aplicação (aparece no link de convite OAuth2)
    """
    avatar_path = CODE_DIR / "default_avatar.png"
    if not avatar_path.exists():
        print("[avatar_api] default_avatar.png não encontrado", flush=True)
        return False
    try:
        img_bytes = avatar_path.read_bytes()

        # Redimensiona para 256x256 para evitar payload grande (>1MB causa timeout/rejeição)
        try:
            from PIL import Image as _PILImage
            import io as _io
            _img = _PILImage.open(_io.BytesIO(img_bytes)).convert("RGBA")
            _img = _img.resize((256, 256), _PILImage.LANCZOS)
            _buf = _io.BytesIO()
            _img.save(_buf, format="PNG", optimize=True)
            img_bytes = _buf.getvalue()
            print(f"[avatar_api] imagem redimensionada para 256x256 ({len(img_bytes)} bytes)", flush=True)
        except Exception as _re:
            print(f"[avatar_api] resize ignorado: {_re}", flush=True)

        b64      = base64.b64encode(img_bytes).decode()
        data_uri = f"data:image/png;base64,{b64}"

        def _patch(endpoint: str, field: str) -> int:
            payload = json.dumps({field: data_uri}).encode()
            req = urllib.request.Request(
                f"https://discord.com/api/v10/{endpoint}",
                data=payload,
                headers={
                    "Authorization": f"Bot {token}",
                    "Content-Type": "application/json",
                },
                method="PATCH",
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return resp.status
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="ignore")[:200]
                except Exception:
                    pass
                print(f"[avatar_api] HTTP {e.code} em /{endpoint}: {body}", flush=True)
                return e.code
            except Exception as _exc:
                print(f"[avatar_api] erro em /{endpoint}: {_exc}", flush=True)
                return 0

        # 1. Avatar do bot (aparece nas mensagens do Discord)
        s1 = _patch("users/@me", "avatar")
        print(f"[avatar_api] /users/@me -> {s1}", flush=True)

        # 2. Ícone da aplicação (aparece na página de auth OAuth2)
        s2 = _patch("applications/@me", "icon")
        print(f"[avatar_api] /applications/@me -> {s2}", flush=True)

        ok = s1 in (200, 201) or s2 in (200, 201)
        if not ok:
            print(f"[avatar_api] FALHOU — avatar={s1} icon={s2} size={len(img_bytes)}b", flush=True)
        return ok
    except Exception as e:
        print(f"[avatar_api] erro inesperado: {e}", flush=True)
        return False


def _apply_avatar_bytes_to_token(token: str, img_bytes: bytes) -> bool:
    """Aplica avatar/ícone OAuth2 a partir de bytes já carregados."""
    try:
        try:
            from PIL import Image as _PILImage
            import io as _io
            _img = _PILImage.open(_io.BytesIO(img_bytes)).convert("RGBA")
            _img = _img.resize((256, 256), _PILImage.LANCZOS)
            _buf = _io.BytesIO()
            _img.save(_buf, format="PNG", optimize=True)
            img_bytes = _buf.getvalue()
        except Exception:
            pass

        b64      = base64.b64encode(img_bytes).decode()
        data_uri = f"data:image/png;base64,{b64}"

        def _patch(endpoint: str, field: str) -> int:
            payload = json.dumps({field: data_uri}).encode()
            req = urllib.request.Request(
                f"https://discord.com/api/v10/{endpoint}",
                data=payload,
                headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
                method="PATCH",
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return resp.status
            except urllib.error.HTTPError as e:
                body = ""
                try: body = e.read().decode("utf-8", errors="ignore")[:200]
                except Exception: pass
                print(f"[avatar_api] HTTP {e.code} em {endpoint}: {body}", flush=True)
                return e.code
            except Exception as exc:
                print(f"[avatar_api] erro em {endpoint}: {exc}", flush=True)
                return 0

        s1 = _patch("users/@me", "avatar")
        s2 = _patch("applications/@me", "icon")
        print(f"[avatar_api] /users/@me={s1} /applications/@me={s2} size={len(img_bytes)}b", flush=True)
        return s1 in (200, 201) or s2 in (200, 201)
    except Exception as e:
        print(f"[avatar_api] erro inesperado: {e}", flush=True)
        return False


def _apply_avatar_async(token: str):
    """Aplica o avatar em background sem bloquear a resposta HTTP."""
    def _run():
        _time.sleep(3)   # aguarda o bot iniciar e o token ser válido no Discord
        ok = _apply_default_avatar_to_token(token)
        if not ok:
            # Tenta uma segunda vez após 10s em caso de rate limit inicial
            _time.sleep(10)
            ok = _apply_default_avatar_to_token(token)
        print(f"[avatar_api] {'OK' if ok else 'FALHOU'}", flush=True)
    threading.Thread(target=_run, daemon=True).start()


# ── Descrição (bio) do bot via Discord API ─────────────────────────────────────

def _get_bot_profile(token: str) -> dict:
    """Busca nome e descrição do bot via GET /applications/@me."""
    try:
        req = urllib.request.Request(
            "https://discord.com/api/v10/applications/@me",
            headers={"Authorization": f"Bot {token}", "User-Agent": "HypeBot/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            return {"name": data.get("name", ""), "description": data.get("description", "")}
    except Exception as e:
        print(f"[bot_profile] erro ao buscar perfil: {e}", flush=True)
        return {}


def _set_bot_description(token: str, description: str) -> tuple[bool, str]:
    """Define a descrição/bio do bot via PATCH /applications/@me."""
    try:
        payload = json.dumps({"description": description[:400]}).encode()
        req = urllib.request.Request(
            "https://discord.com/api/v10/applications/@me",
            data=payload,
            headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
            method="PATCH",
        )
        with urllib.request.urlopen(req, timeout=20):
            return True, ""
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode("utf-8", errors="ignore")[:300]
        except Exception: pass
        return False, f"HTTP {e.code}: {body}"
    except Exception as ex:
        return False, str(ex)


# ── Source Images API ─────────────────────────────────────────────────────────
# Cache em memória: {category: {"images": [...], "ts": float}}
_src_img_cache: dict = {}
_SRC_CACHE_TTL = 1800  # 30 min

# ── Temp Image Hosting (para preview de thumbnails no embed builder) ───────────
_temp_images: dict = {}   # {token: (bytes, content_type, expires_at)}
_TEMP_IMG_TTL = 1800      # 30 min (suficiente para uma sessão de embed builder)


@app.route("/api/temp-image/<token>")
def serve_temp_image(token):
    """Serve uma imagem temporária — usada pelo embed builder para preview de thumbnail."""
    item = _temp_images.get(token)
    if not item:
        return "Not found", 404
    data, ct, exp = item
    if _time.time() > exp:
        _temp_images.pop(token, None)
        return "Expired", 404
    return data, 200, {
        "Content-Type": ct,
        "Cache-Control": "public, max-age=1800",
        "Access-Control-Allow-Origin": "*",
    }


@app.route("/api/temp-image", methods=["POST"])
def upload_temp_image():
    """Recebe bytes de imagem e retorna uma URL temporária — chamado pelo bot."""
    import secrets as _sec
    data = request.data
    if not data or len(data) < 100:
        return jsonify({"ok": False, "error": "sem dados"}), 400
    ct  = request.content_type or "image/png"
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
           "image/gif": "gif", "image/webp": "webp"}.get(ct.split(";")[0].strip(), "png")
    token = _sec.token_urlsafe(20) + "." + ext
    _temp_images[token] = (data, ct, _time.time() + _TEMP_IMG_TTL)
    # Limpa tokens expirados (housekeeping leve)
    _exp_keys = [k for k, v in list(_temp_images.items()) if _time.time() > v[2]]
    for k in _exp_keys:
        _temp_images.pop(k, None)
    dash_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if dash_url:
        base_url = f"https://{dash_url}"
    else:
        port = int(os.environ.get("PORT") or os.environ.get("DASHBOARD_PORT", 5500))
        base_url = f"http://localhost:{port}"
    url = f"{base_url}/api/temp-image/{token}"
    return jsonify({"ok": True, "url": url})

# Palavras-chave do Pinterest por categoria
_PINTEREST_QUERIES = {
    "masc_icons":  ["male icons aesthetic", "boy icons aesthetic", "male pfp aesthetic"],
    "fem_icons":   ["female icons aesthetic", "girl icons aesthetic", "feminine pfp aesthetic"],
    "fem_gifs":    ["female gif aesthetic", "girl gif aesthetic", "woman gif aesthetic"],
    "masc_gifs":   ["male gif aesthetic", "boy gif aesthetic", "man gif aesthetic"],
    "banners":     ["discord banner aesthetic", "banner aesthetic dark", "profile banner aesthetic"],
    "animes":      ["anime icons aesthetic", "anime pfp aesthetic", "anime girl icon", "anime boy icon"],
}

def _fetch_pinterest(query: str, limit: int = 40) -> list:
    """Busca imagens no Pinterest via scraping do HTML.
    Usa múltiplas estratégias de extração para cobrir diferentes formatos da página."""
    import urllib.request as _ur, urllib.parse as _up, re as _re, json as _json
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
                  "image/webp,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    url = f"https://www.pinterest.com/search/pins/?q={_up.quote(query)}&rs=typed"
    images = []
    try:
        req = _ur.Request(url, headers=headers)
        with _ur.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", errors="ignore")

        seen = set()

        # Normaliza escapes antes de rodar as regexes
        html_n = html.replace("\\/", "/").replace("\\u002F", "/").replace("%2F", "/")

        # ── Estratégia 1: URLs diretas no HTML (formato antigo) ──────────────
        raw_urls = _re.findall(
            r'https://i\.pinimg\.com/(?:originals|736x|564x|474x|236x|170x|75x75)'
            r'/[0-9a-f/]{10,}\.(?:jpg|jpeg|png|gif|webp)',
            html_n, _re.IGNORECASE
        )

        # ── Estratégia 2: URLs em atributos JSON/data dentro do HTML ─────────
        json_urls = _re.findall(
            r'"(?:url|src|image_url|orig)"\s*:\s*"(https://i\.pinimg\.com/[^"]{10,}\.(?:jpg|jpeg|png|gif|webp))"',
            html_n, _re.IGNORECASE
        )
        raw_urls.extend(json_urls)

        # ── Estratégia 3: __PWS_DATA__ JSON ──────────────────────────────────
        pws_match = _re.search(r'__PWS_DATA__\s*=\s*(\{.{100,}?\})\s*;', html_n)
        if pws_match:
            pws_urls = _re.findall(
                r'https://i\.pinimg\.com/[^\s"\'\\]{10,}\.(?:jpg|jpeg|png|gif|webp)',
                pws_match.group(1), _re.IGNORECASE
            )
            raw_urls.extend(pws_urls)

        for raw in raw_urls:
            # Normaliza para originals (melhor qualidade)
            clean = _re.sub(r'/(?:736x|564x|474x|236x|170x|75x75)/', '/originals/', raw)
            clean = clean.split("?")[0]  # remove query string
            if clean not in seen:
                seen.add(clean)
                fname = clean.split("/")[-1]
                images.append({"url": clean, "filename": fname})
            if len(images) >= limit:
                break

    except Exception as _pe:
        print(f"[pinterest] erro ({query}): {_pe}", flush=True)
    return images

_SRC_CAT_NAMES = {
    "masc_icons":  ["male-icons",   "masc-icons",   "icons-masculinos"],
    "fem_icons":   ["female-icons", "fem-icons",    "icons-femininos"],
    "fem_gifs":    ["female-gifs",  "fem-gifs",     "gifs-femininos"],
    "masc_gifs":   ["male-gifs",    "masc-gifs",    "gifs-masculinos"],
    "banners":     ["banners",      "banner"],
    "animes":      ["animes", "anime", "female-animes-icons", "male-animes-icons",
                   "female-animes-gifs", "male-animes-gifs"],
}
_SRC_IMG_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")

@app.route("/api/v1/source-images/<category>")
def api_source_images(category):
    """Retorna URLs de imagens da categoria — chamado pelos bots clientes."""
    import urllib.request as _ur, json as _json, random as _rand
    now = _time.time()
    cached = _src_img_cache.get(category)
    if cached and now - cached["ts"] < _SRC_CACHE_TTL:
        return jsonify({"ok": True, "images": cached["images"]})

    guild_id   = os.environ.get("NATA_GUILD_ID", "1480530206505697404")
    names      = _SRC_CAT_NAMES.get(category, [])

    if not names:
        return jsonify({"ok": False, "error": "categoria inválida"}), 400

    # ── Coleta todos os tokens disponíveis ────────────────────────────────────────
    # Prioridade: NATA_SOURCE_TOKEN > BOT_TOKEN principal > tokens dos clientes
    _all_tokens: list[str] = []
    for _env_key in ("NATA_SOURCE_TOKEN", "BOT_TOKEN", "DISCORD_TOKEN"):
        _t = os.environ.get(_env_key, "").strip()
        if _t and _t not in _all_tokens:
            _all_tokens.append(_t)
    try:
        for _cd in sorted(CLIENTS_DIR.iterdir()):
            _ef = _cd / ".env"
            if not _ef.exists():
                continue
            for _line in _ef.read_text(errors="ignore").splitlines():
                # Clientes usam DISCORD_TOKEN= (escrito pelo manager.py)
                if _line.startswith("DISCORD_TOKEN=") or _line.startswith("BOT_TOKEN="):
                    _tok = _line.split("=", 1)[1].strip().strip('"\'')
                    if _tok and _tok not in ("TOKEN_DO_BOT_AQUI", "") and _tok not in _all_tokens:
                        _all_tokens.append(_tok)
                    break
    except Exception as _te:
        print(f"[source-images] erro ao listar tokens: {_te}", flush=True)
    print(f"[source-images] {len(_all_tokens)} token(s) disponíveis para {category}", flush=True)

    images = []

    # ── Tenta buscar do servidor 1480530206505697404 com cada token disponível ────
    for _tok in _all_tokens:
        if len(images) >= 20:
            break
        try:
            _headers = {"Authorization": f"Bot {_tok}", "User-Agent": "HypeBot/1.0"}
            req = _ur.Request(
                f"https://discord.com/api/v10/guilds/{guild_id}/channels",
                headers=_headers,
            )
            with _ur.urlopen(req, timeout=15) as r:
                channels = _json.loads(r.read())

            for ch in channels:
                if ch.get("type") not in (0, 5, 15):
                    continue
                ch_name = ch.get("name", "").lower().lstrip("・•·﹒∙⋅.・ -")
                if ch_name not in names:
                    continue
                req2 = _ur.Request(
                    f"https://discord.com/api/v10/channels/{ch['id']}/messages?limit=100",
                    headers=_headers,
                )
                with _ur.urlopen(req2, timeout=15) as r2:
                    messages = _json.loads(r2.read())
                for msg in messages:
                    for att in msg.get("attachments", []):
                        fname = att.get("filename", "")
                        if fname.lower().endswith(_SRC_IMG_EXTS):
                            images.append({"url": att["url"], "filename": fname})
                    for emb in msg.get("embeds", []):
                        for field in ("image", "thumbnail"):
                            img_data = emb.get(field) or {}
                            url = img_data.get("url", "") or img_data.get("proxy_url", "")
                            if url:
                                fname = url.split("?")[0].split("/")[-1]
                                if fname.lower().endswith(_SRC_IMG_EXTS):
                                    images.append({"url": url, "filename": fname})
                    if len(images) >= 100:
                        break
                if len(images) >= 20:
                    break
            if images:
                print(f"[source-images] {len(images)} imagens coletadas de {guild_id} ({category})", flush=True)
        except Exception as _discord_e:
            print(f"[source-images] token falhou em {guild_id} ({category}): {_discord_e}", flush=True)

    # ── Pinterest (fallback 1) ─────────────────────────────────────────────────────
    if len(images) < 5:
        for pq in _PINTEREST_QUERIES.get(category, []):
            pinterest_imgs = _fetch_pinterest(pq, limit=40)
            for img in pinterest_imgs:
                if img["url"] not in {x["url"] for x in images}:
                    images.append(img)
            if len(images) >= 150:
                break

    # ── Reddit JSON (fallback 2) — busca posts com imagens diretas i.redd.it ────────
    if len(images) < 5:
        _REDDIT_SUBS = {
            "masc_icons": ["PFPs", "maleicon", "discordpfp", "maleaesthetic"],
            "fem_icons":  ["PFPs", "femaleicon", "discordpfp", "femaleaesthetic"],
            "fem_gifs":   ["wholesomegifs", "animegifs", "Animewallpaper"],
            "masc_gifs":  ["wholesomegifs", "animegifs", "Animewallpaper"],
            "banners":    ["Amoledbackgrounds", "wallpapers", "wallpaper"],
            "animes":     ["AnimeWallpaper", "Animewallpaper", "awwnime"],
        }
        _IMG_EXTS_RED = (".jpg", ".jpeg", ".png", ".gif", ".webp")
        seen_urls = {x["url"] for x in images}
        for sub in _REDDIT_SUBS.get(category, []):
            try:
                req_r = _ur.Request(
                    f"https://www.reddit.com/r/{sub}/hot.json?limit=50",
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; HypeBot/2.0)",
                        "Accept": "application/json",
                    },
                )
                with _ur.urlopen(req_r, timeout=15) as rr:
                    rdata = _json.loads(rr.read())
                for post in rdata.get("data", {}).get("children", []):
                    p = post.get("data", {})
                    if p.get("is_video") or p.get("over_18"):
                        continue
                    # i.redd.it URLs diretas (sem autenticação)
                    url = p.get("url", "")
                    if "i.redd.it" in url and url.lower().endswith(_IMG_EXTS_RED):
                        fname = url.split("/")[-1]
                        if url not in seen_urls:
                            images.append({"url": url, "filename": fname})
                            seen_urls.add(url)
                    # gallery_data com media_metadata
                    for _mid, _mdata in (p.get("media_metadata") or {}).items():
                        if (_mdata or {}).get("status") != "valid":
                            continue
                        _s = (_mdata.get("s") or {})
                        _img_url = _s.get("u", "").replace("&amp;", "&")
                        if _img_url and _img_url not in seen_urls:
                            images.append({"url": _img_url, "filename": f"{_mid}.jpg"})
                            seen_urls.add(_img_url)
                    if len(images) >= 60:
                        break
                print(f"[source-images] Reddit r/{sub} → {len(images)} imgs ({category})", flush=True)
                if len(images) >= 20:
                    break
            except Exception as _re:
                print(f"[source-images] Reddit r/{sub} falhou: {_re}", flush=True)

    # ── APIs de imagem gratuitas ──────────────────────────────────────────────────
    seen_api = {x["url"] for x in images}

    # Mapeamento categoria → chamadas de API
    # Definição das fontes por categoria
    # Formato: (url, método, body_json, list_key, url_key, default_filename)
    # APIs de imagem gratuitas sem autenticação
    # Formato: (url, método, body, list_key, url_key, filename_padrão)
    _API_CALLS = {
        "masc_icons": [
            ("https://nekos.best/api/v2/husbando?amount=20",   "GET",  None, "results", "url", "husbando.png"),
            ("https://nekos.best/api/v2/kitsune?amount=20",    "GET",  None, "results", "url", "kitsune.png"),
            ("https://nekos.best/api/v2/husbando?amount=20",   "GET",  None, "results", "url", "husbando2.png"),
            ("https://api.waifu.im/search/?included_tags=maid&excluded_tags=hentai&many=true","GET",None,"images","url","waifuim.png"),
        ],
        "fem_icons": [
            ("https://nekos.best/api/v2/waifu?amount=20",      "GET",  None, "results", "url", "waifu.png"),
            ("https://nekos.best/api/v2/neko?amount=20",       "GET",  None, "results", "url", "neko.png"),
            ("https://nekos.best/api/v2/waifu?amount=20",      "GET",  None, "results", "url", "waifu2.png"),
            ("https://api.waifu.im/search/?included_tags=waifu&excluded_tags=hentai&many=true","GET",None,"images","url","waifuim.png"),
            ("https://api.waifu.im/search/?included_tags=selfies&excluded_tags=hentai&many=true","GET",None,"images","url","selfie.png"),
            ("https://api.waifu.pics/many/sfw/waifu",          "POST", {}, "files",  None,  "waifu.png"),
        ],
        "fem_gifs": [
            ("https://nekos.best/api/v2/neko?amount=20",       "GET",  None, "results", "url", "neko.gif"),
            ("https://nekos.best/api/v2/waifu?amount=20",      "GET",  None, "results", "url", "waifu.gif"),
            ("https://api.waifu.im/search/?included_tags=waifu&is_gif=true&excluded_tags=hentai&many=true","GET",None,"images","url","waifuim.gif"),
            ("https://api.waifu.pics/many/sfw/neko",           "POST", {}, "files",  None,  "neko.gif"),
        ],
        "masc_gifs": [
            ("https://nekos.best/api/v2/husbando?amount=20",   "GET",  None, "results", "url", "husbando.gif"),
            ("https://nekos.best/api/v2/kitsune?amount=20",    "GET",  None, "results", "url", "kitsune.gif"),
            ("https://nekos.best/api/v2/husbando?amount=20",   "GET",  None, "results", "url", "husbando2.gif"),
        ],
        "banners": [
            ("https://nekos.best/api/v2/waifu?amount=20",      "GET",  None, "results", "url", "banner.png"),
            ("https://nekos.best/api/v2/neko?amount=20",       "GET",  None, "results", "url", "banner2.png"),
            ("https://api.waifu.im/search/?included_tags=uniform&is_gif=false&excluded_tags=hentai&many=true","GET",None,"images","url","banner_waifu.png"),
            ("https://api.waifu.im/search/?included_tags=waifu&is_gif=false&excluded_tags=hentai&many=true","GET",None,"images","url","banner_waifu2.png"),
        ],
        "animes": [
            ("https://nekos.best/api/v2/waifu?amount=20",      "GET",  None, "results", "url", "anime.png"),
            ("https://nekos.best/api/v2/neko?amount=20",       "GET",  None, "results", "url", "neko.png"),
            ("https://nekos.best/api/v2/kitsune?amount=20",    "GET",  None, "results", "url", "kitsune.png"),
            ("https://api.waifu.im/search/?included_tags=waifu&excluded_tags=hentai&many=true","GET",None,"images","url","anime_waifu.png"),
            ("https://api.waifu.pics/many/sfw/waifu",          "POST", {}, "files",  None,  "anime.png"),
        ],
    }

    for api_url, method, body_json, list_key, url_key, def_fname in _API_CALLS.get(category, []):
        if len(images) >= 200:
            break
        try:
            _headers_a = {"User-Agent": "HypeBot/2.0", "Accept": "application/json",
                          "Content-Type": "application/json"}
            if method == "POST":
                _body = _json.dumps(body_json or {}).encode()
                req_a = _ur.Request(api_url, data=_body, headers=_headers_a, method="POST")
            else:
                req_a = _ur.Request(api_url, headers=_headers_a)
            with _ur.urlopen(req_a, timeout=12) as ra:
                data_a = _json.loads(ra.read())

            items = data_a
            if list_key and isinstance(data_a, dict):
                items = data_a.get(list_key, [])

            added = 0
            for item in (items if isinstance(items, list) else []):
                if url_key:
                    img_url = (item or {}).get(url_key, "") if isinstance(item, dict) else ""
                else:
                    img_url = item if isinstance(item, str) else ""

                if img_url and img_url not in seen_api:
                    fname = img_url.split("?")[0].split("/")[-1] or def_fname
                    if not any(fname.lower().endswith(e) for e in _SRC_IMG_EXTS):
                        fname = def_fname
                    images.append({"url": img_url, "filename": fname})
                    seen_api.add(img_url)
                    added += 1

            print(f"[source-images] {api_url[:55]}… +{added} ({category})", flush=True)
        except Exception as _ae:
            print(f"[source-images] API falhou ({api_url[:55]}…): {_ae}", flush=True)

    _rand.shuffle(images)
    images = images[:200]

    # Cache normal se tiver imagens; cache curto (5 min) se vazio para re-tentar logo
    _src_img_cache[category] = {"images": images, "ts": now if images else now - (_SRC_CACHE_TTL - 300)}
    print(f"[source-images] {category}: {len(images)} imagens retornadas", flush=True)
    return jsonify({"ok": True, "images": images})


@app.route("/api/v1/admin/customers-guilds")
def api_admin_customers_guilds():
    """Admin: retorna lista de clientes com suas guilds configuradas."""
    import traceback as _tb
    try:
        secret = request.args.get("secret", "")
        expected = os.environ.get("DASHBOARD_SECRET", "66e8aa01984654baacbf83593587260bb6c69d9907818eb16cf233ffede10ec8")
        if secret != expected:
            return jsonify({"ok": False, "error": "forbidden"}), 403
        customers = load_customers()
        result = []
        for c in customers:
            cid = str(c.get("id", ""))
            guild_id = str(c.get("guild_id", "") or "")
            settings_guilds = []
            try:
                sf = CLIENTS_DIR / cid / "bot_settings.json"
                if sf.exists():
                    raw = sf.read_text(encoding="utf-8", errors="ignore")
                    data = json.loads(raw)
                    settings_guilds = [str(k) for k in data.keys() if str(k) != "0"]
            except Exception:
                pass
            result.append({
                "id": cid,
                "nome": str(c.get("nome", "")),
                "guild_id": guild_id,
                "settings_guilds": settings_guilds,
                "expira": str(c.get("expira", "")),
            })
        return jsonify({"ok": True, "customers": result})
    except Exception as _e:
        return jsonify({"ok": False, "error": str(_e), "trace": _tb.format_exc()}), 500


@app.route("/api/v1/clear-image-cache")
def api_clear_image_cache():
    """Limpa o cache de imagens fonte (força re-busca na próxima chamada)."""
    _src_img_cache.clear()
    return jsonify({"ok": True, "msg": "Cache limpo."})


@app.route("/api/v1/debug-pinterest")
def api_debug_pinterest():
    """Debug: testa scraping do Pinterest."""
    import urllib.request as _ur, urllib.parse as _up, re as _re
    query = "male icons aesthetic"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "pt-BR,pt;q=0.9",
    }
    url = f"https://www.pinterest.com/search/pins/?q={_up.quote(query)}"
    try:
        req = _ur.Request(url, headers=headers)
        with _ur.urlopen(req, timeout=20) as r:
            status = r.status
            html = r.read().decode("utf-8", errors="ignore")
        # Verifica se __PWS_DATA__ existe
        has_pws = "__PWS_DATA__" in html
        has_pinimg = "pinimg.com" in html
        # Extrai amostra de URLs pinimg (mesmo padrão da _fetch_pinterest)
        html_n = html.replace("\\/", "/").replace("\\u002F", "/")
        urls = _re.findall(
            r'https://i\.pinimg\.com/(?:originals|736x|564x|474x|236x|170x|75x75)'
            r'/[^\s"\'<>\\]{8,}\.(?:jpg|jpeg|png|gif|webp)',
            html_n, _re.IGNORECASE
        )
        urls = list(dict.fromkeys(urls))  # deduplica
        return jsonify({
            "status": status,
            "html_len": len(html),
            "has_pws_data": has_pws,
            "has_pinimg_url": has_pinimg,
            "pinimg_urls_found": len(urls),
            "sample_urls": urls[:3],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/v1/debug-channels")
def api_debug_channels():
    """Debug: lista canais e testa fetch de mensagens no canal male-icons."""
    import urllib.request as _ur, json as _json
    src_token = os.environ.get("NATA_SOURCE_TOKEN", "")
    guild_id  = os.environ.get("NATA_GUILD_ID", "1480530206505697404")
    if not src_token:
        return jsonify({"ok": False, "error": "NATA_SOURCE_TOKEN não configurado"}), 400
    headers = {"Authorization": f"Bot {src_token}", "User-Agent": "HypeBot/1.0"}
    try:
        req = _ur.Request(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers=headers)
        with _ur.urlopen(req, timeout=15) as r:
            channels = _json.loads(r.read())

        result = {"ok": True, "total": len(channels), "channels": [], "msg_test": None}
        test_ch_id = None
        img_exts = (".jpg", ".jpeg", ".png", ".gif", ".webp")

        for c in channels:
            ch_raw  = c["name"]
            ch_name = ch_raw.lower().lstrip("・•·﹒∙⋅.・ -")
            matched = c["type"] in (0, 5, 15) and ch_name == "male-icons"
            result["channels"].append({"id": c["id"], "name": ch_raw, "type": c["type"],
                                       "stripped": ch_name, "matched": matched})
            if matched and test_ch_id is None:
                test_ch_id = c["id"]

        # Testa busca de mensagens no canal matched
        if test_ch_id:
            try:
                req2 = _ur.Request(
                    f"https://discord.com/api/v10/channels/{test_ch_id}/messages?limit=20",
                    headers=headers)
                with _ur.urlopen(req2, timeout=15) as r2:
                    msgs = _json.loads(r2.read())
                imgs_found = []
                for m in msgs:
                    for att in m.get("attachments", []):
                        if att.get("filename","").lower().endswith(img_exts):
                            imgs_found.append(att["filename"])
                # Analisa estrutura das primeiras mensagens
                sample = []
                for m in msgs[:3]:
                    sample.append({
                        "attachments": len(m.get("attachments", [])),
                        "embeds": len(m.get("embeds", [])),
                        "has_content": bool(m.get("content", "")),
                        "embed_imgs": [e.get("image", {}).get("url","")[:60] for e in m.get("embeds",[]) if e.get("image")],
                        "att_names": [a.get("filename","") for a in m.get("attachments", [])],
                    })
                result["msg_test"] = {"channel_id": test_ch_id, "messages": len(msgs),
                                      "images": imgs_found[:5], "sample": sample}
            except Exception as me:
                result["msg_test"] = {"error": str(me)}

        return jsonify(result)
    except Exception as e:
        # Retorna 200 com detalhe do erro em vez de 500 opaco
        return jsonify({"ok": False, "error": str(e),
                        "hint": "Verifique se NATA_SOURCE_TOKEN é válido e se o bot está no servidor NATA_GUILD_ID"})


# ── Customer API ──────────────────────────────────────────────────────────────

@app.route("/api/customers", methods=["GET"])
@login_required
def api_list():
    customers  = load_customers()
    bot_status = bot_manager.status()
    # Detecta tokens duplicados entre clientes
    token_counts: dict[str, list[str]] = {}
    for c in customers:
        tok = c.get("token", "").strip()
        if tok and tok != "TOKEN_DO_BOT_AQUI":
            token_counts.setdefault(tok, []).append(c["id"])
    result = []
    for c in customers:
        settings = load_settings(c["id"])
        bs = bot_status.get(c["id"], {})
        tok = c.get("token", "").strip()
        conflicts = [x for x in token_counts.get(tok, []) if x != c["id"]]
        # Pega bot_name de qualquer guild nas settings
        bot_name_val = ""
        for gs in settings.values():
            if isinstance(gs, dict) and gs.get("bot_name"):
                bot_name_val = gs["bot_name"]
                break
        result.append({
            **c,
            "status":         customer_status(c),
            "prefix":         first_prefix(settings),
            "bot_running":    bs.get("running", False),
            "bot_pid":        bs.get("pid"),
            "token_conflict": conflicts[0] if conflicts else None,
            "bad_token":      bs.get("bad_token", False),
            "bot_name":       bot_name_val,
        })
    return jsonify(result)


@app.route("/api/customers", methods=["POST"])
@login_required
def api_add():
    data = request.get_json(force=True) or {}
    cid  = data.get("id", "").strip()
    if not cid:
        return jsonify({"ok": False, "error": "ID obrigatório"}), 400
    customers = load_customers()
    if any(c["id"] == cid for c in customers):
        return jsonify({"ok": False, "error": f"ID '{cid}' já existe"}), 400
    default_prefix = data.get("default_prefix", "n!").strip() or "n!"
    # Inicializa o diretório do cliente com settings vazio e prefixo correto
    _cdir = CLIENTS_DIR / cid
    _cdir.mkdir(exist_ok=True)
    (_cdir / "bot_settings.json").write_text("{}", encoding="utf-8")
    (_cdir / "bot_prefix.txt").write_text(default_prefix, encoding="utf-8")
    new_customer = {
        "id":     cid,
        "nome":   data.get("nome", ""),
        "token":  data.get("token", ""),
        "ativo":  data.get("ativo", True),
        "expira": data.get("expira", ""),
    }
    customers.append(new_customer)
    save_customers(customers)
    # Auto-start the bot right away if the customer is active and has a real token
    token = new_customer["token"].strip()
    if new_customer["ativo"] and token and token != "TOKEN_DO_BOT_AQUI":
        ok, err = bot_manager.start_bot(cid)
        # Aplica avatar independente de o bot ter iniciado ou não
        _apply_avatar_async(token)
        # Empurra SEED inicial imediatamente para que um redeploy preserve o prefixo
        if os.environ.get("RAILWAY_API_TOKEN"):
            def _push_new_seed(_c=_cdir, _id=cid):
                try:
                    _railway_push_seed(_id, _c / "bot_settings.json", _c / "bot_prefix.txt")
                    print(f"[seed-new] SEED inicial enviado para '{_id}'", flush=True)
                except Exception as _e_ns:
                    print(f"[seed-new] erro ao enviar SEED para '{_id}': {_e_ns}", flush=True)
            threading.Thread(target=_push_new_seed, daemon=True).start()
        if not ok and err:
            return jsonify({"ok": True, "warning": err})
    return jsonify({"ok": True})


@app.route("/api/customers/<cid>", methods=["PUT"])
@login_required
def api_update(cid):
    data = request.get_json(force=True) or {}
    customers = load_customers()
    for c in customers:
        if c["id"] == cid:
            old_token = c.get("token", "")
            c["nome"]   = data.get("nome",   c.get("nome", ""))
            c["token"]  = data.get("token",  c.get("token", ""))
            c["expira"] = data.get("expira", c.get("expira", ""))
            c["ativo"]  = data.get("ativo",  c.get("ativo", True))
            if "app_id" in data:
                c["app_id"] = data["app_id"].strip()
            if "guild_id" in data:
                c["guild_id"] = data["guild_id"].strip()
            save_customers(customers)
            token = c["token"].strip()
            # Restart immediately with new settings if the customer should be running
            if c.get("ativo", True) and token and token != "TOKEN_DO_BOT_AQUI" and not is_expired(c):
                ok, err = bot_manager.restart_bot(cid)
                # Reaplica avatar automaticamente quando o token muda
                if token != old_token.strip():
                    _apply_avatar_async(token)
                if not ok and err:
                    return jsonify({"ok": True, "warning": err})
            else:
                bot_manager.stop_bot(cid)
            return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Cliente não encontrado"}), 404


@app.route("/api/customers/<cid>", methods=["DELETE"])
@login_required
def api_delete(cid):
    customers = load_customers()
    new_list = [c for c in customers if c["id"] != cid]
    if len(new_list) == len(customers):
        return jsonify({"ok": False, "error": "Cliente não encontrado"}), 404
    save_customers(new_list)
    bot_manager.stop_bot(cid)  # kill the process if running
    return jsonify({"ok": True})




@app.route("/api/customers/<cid>/settings", methods=["GET"])
@login_required
def api_settings(cid):
    return jsonify({"ok": True, "settings": load_settings(cid)})


@app.route("/api/admin/backup-settings", methods=["GET"])
def api_backup_settings():
    """Exporta bot_settings.json e bot_prefix.txt como gzip+base64 para backup em env vars."""
    import gzip as _gz, base64 as _b64
    secret = request.args.get("secret", "")
    expected = os.environ.get("DASHBOARD_SECRET", "66e8aa01984654baacbf83593587260bb6c69d9907818eb16cf233ffede10ec8")
    if secret != expected:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    settings_result = {}
    prefix_result = {}
    if CLIENTS_DIR.exists():
        for d in CLIENTS_DIR.iterdir():
            if not d.is_dir():
                continue
            f = d / "bot_settings.json"
            if f.exists():
                try:
                    raw = f.read_bytes()
                    settings_result[d.name] = _b64.b64encode(_gz.compress(raw)).decode()
                except Exception as e:
                    settings_result[d.name] = f"ERRO: {e}"
            p = d / "bot_prefix.txt"
            if p.exists():
                try:
                    prefix_result[d.name] = p.read_text(encoding="utf-8").strip()
                except Exception as e:
                    prefix_result[d.name] = f"ERRO: {e}"
    return jsonify({"ok": True, "backups": settings_result, "prefixes": prefix_result})


@app.route("/api/admin/check-guild-membership", methods=["POST"])
@login_required
def api_check_guild_membership():
    """Verifica quais clientes têm o bot no servidor informado e quais não têm."""
    import urllib.request as _ureq
    import urllib.error  as _uerr

    data     = request.get_json(force=True) or {}
    guild_id = str(data.get("guild_id", "")).strip()
    if not guild_id.isdigit():
        return jsonify({"ok": False, "error": "guild_id inválido"}), 400

    customers = load_customers()
    in_guild     = []
    not_in_guild = []

    for c in customers:
        cid   = c["id"]
        nome  = c.get("nome", cid)
        token = c.get("token", "").strip()

        if not token or token == "TOKEN_DO_BOT_AQUI":
            not_in_guild.append({"id": cid, "nome": nome, "reason": "sem token"})
            continue

        try:
            req = _ureq.Request(
                "https://discord.com/api/v10/users/@me/guilds",
                headers={"Authorization": f"Bot {token}", "User-Agent": "HypeBot/1.0"},
            )
            with _ureq.urlopen(req, timeout=10) as r:
                guilds = json.loads(r.read())
            guild_ids = {str(g["id"]) for g in guilds}
            if guild_id in guild_ids:
                in_guild.append({"id": cid, "nome": nome})
            else:
                not_in_guild.append({"id": cid, "nome": nome, "reason": "não está no servidor"})
        except _uerr.HTTPError as e:
            not_in_guild.append({"id": cid, "nome": nome, "reason": f"erro Discord {e.code}"})
        except Exception as e:
            not_in_guild.append({"id": cid, "nome": nome, "reason": f"erro: {str(e)[:40]}"})

    return jsonify({"ok": True, "in_guild": in_guild, "not_in_guild": not_in_guild})


@app.route("/api/admin/auto-guild-ids", methods=["POST"])
def api_auto_guild_ids():
    """Detecta e salva o guild_id de cada cliente via Discord API.
    Auth: Bearer {secret_key}"""
    import urllib.request as _ureq
    import urllib.error  as _uerr

    # Autenticação via secret key
    auth = request.headers.get("Authorization", "")
    cfg  = load_config()
    expected = "Bearer " + cfg.get("secret_key", "")
    if auth != expected:
        return jsonify({"ok": False, "error": "Não autorizado"}), 401

    EMOJI_GUILD = 835695342291779645
    DISCORD     = "https://discord.com/api/v10"
    results     = []

    customers = load_customers()
    for c in customers:
        cid   = c["id"]
        token = c.get("token", "").strip()
        nome  = c.get("nome", cid)

        if not token or "TOKEN_DO_BOT" in token:
            results.append({"cid": cid, "nome": nome, "status": "sem_token"})
            continue

        if c.get("guild_id"):
            results.append({"cid": cid, "nome": nome, "status": "ja_configurado", "guild_id": c["guild_id"]})
            continue

        # Chama Discord API
        try:
            req = _ureq.Request(
                DISCORD + "/users/@me/guilds",
                headers={"Authorization": f"Bot {token}"},
            )
            with _ureq.urlopen(req, timeout=8) as r:
                guilds = json.loads(r.read())
        except _uerr.HTTPError as e:
            results.append({"cid": cid, "nome": nome, "status": f"discord_erro_{e.code}"})
            continue
        except Exception as e:
            results.append({"cid": cid, "nome": nome, "status": f"erro_{str(e)[:50]}"})
            continue

        valid = [g for g in guilds if int(g["id"]) != EMOJI_GUILD]
        if not valid:
            results.append({"cid": cid, "nome": nome, "status": "sem_servidor_valido"})
            continue

        # Pega o primeiro servidor válido (ou o que tiver mais membros)
        chosen = max(valid, key=lambda g: g.get("approximate_member_count", 0)) if len(valid) > 1 else valid[0]
        c["guild_id"] = chosen["id"]
        results.append({
            "cid": cid, "nome": nome,
            "status": "ok",
            "guild_id": chosen["id"],
            "guild_name": chosen.get("name", "?"),
        })

    save_customers(customers)
    for c in customers:
        if c.get("guild_id") and c.get("ativo"):
            bot_manager.restart_bot(c["id"])  # ignora erro de conflito aqui (já resolvido pelo operador)

    return jsonify({"ok": True, "results": results})


@app.route("/api/admin/verify-owner", methods=["POST"])
def api_verify_owner():
    """Verifica credenciais de um owner do dashboard.
    Auth: Bearer {secret_key}"""
    auth = request.headers.get("Authorization", "")
    cfg  = load_config()
    expected = "Bearer " + cfg.get("secret_key", "")
    if auth != expected:
        return jsonify({"ok": False, "error": "Não autorizado"}), 401

    data     = request.get_json(force=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    owner = next((o for o in cfg.get("owners", []) if o["id"] == username), None)
    if not owner or not _verify(password, owner.get("hash", ""), owner.get("salt", "")):
        return jsonify({"ok": False, "error": "Usuário ou senha incorretos"}), 401

    return jsonify({
        "ok":   True,
        "id":   owner["id"],
        "nome": owner["nome"],
        "role": owner.get("role", "admin"),
    })


@app.route("/api/admin/set-guild-id", methods=["POST"])
def api_set_guild_id():
    """Define o guild_id de um cliente e reinicia o bot.
    Auth: Bearer {secret_key}"""
    auth = request.headers.get("Authorization", "")
    cfg  = load_config()
    expected = "Bearer " + cfg.get("secret_key", "")
    if auth != expected:
        return jsonify({"ok": False, "error": "Não autorizado"}), 401

    data     = request.get_json(force=True) or {}
    cid      = str(data.get("cid", "")).strip()
    guild_id = str(data.get("guild_id", "")).strip()

    if not cid or not guild_id:
        return jsonify({"ok": False, "error": "cid e guild_id são obrigatórios"}), 400

    customers = load_customers()
    for c in customers:
        if c["id"] == cid:
            c["guild_id"] = guild_id
            save_customers(customers)
            bot_manager.restart_bot(cid)
            return jsonify({"ok": True, "cid": cid, "guild_id": guild_id})

    return jsonify({"ok": False, "error": f"Cliente '{cid}' não encontrado"}), 404


@app.route("/api/admin/update-bot-name", methods=["POST"])
def api_update_bot_name():
    """Atualiza o bot_name de um cliente pelo guild_id.
    Auth: Bearer {secret_key}"""
    auth = request.headers.get("Authorization", "")
    cfg  = load_config()
    expected = "Bearer " + cfg.get("secret_key", "")
    if auth != expected:
        return jsonify({"ok": False, "error": "Não autorizado"}), 401

    data     = request.get_json(force=True) or {}
    cid      = str(data.get("cid", "")).strip()
    bot_name = str(data.get("bot_name", "")).strip()

    if not cid or not bot_name:
        return jsonify({"ok": False, "error": "cid e bot_name são obrigatórios"}), 400

    settings = load_settings(cid)
    if not settings:
        return jsonify({"ok": False, "error": f"Cliente '{cid}' não encontrado"}), 404

    for gs in settings.values():
        if isinstance(gs, dict):
            gs["bot_name"] = bot_name
    save_settings(cid, settings)
    return jsonify({"ok": True, "cid": cid, "bot_name": bot_name})


@app.route("/api/customers/<cid>/prefix", methods=["POST"])
@login_required
def api_set_prefix(cid):
    data     = request.get_json(force=True) or {}
    guild_id = str(data.get("guild_id", "")).strip()
    prefix   = str(data.get("prefix",   "")).strip()
    if not prefix or len(prefix) > 10 or " " in prefix:
        return jsonify({"ok": False, "error": "Prefixo inválido"}), 400
    if not guild_id:
        return jsonify({"ok": False, "error": "guild_id obrigatório"}), 400
    settings = load_settings(cid)
    if guild_id in settings and isinstance(settings[guild_id], dict):
        settings[guild_id]["prefix"] = prefix
    else:
        settings[guild_id] = {"prefix": prefix}
    save_settings(cid, settings)
    return jsonify({"ok": True})


@app.route("/api/customers/<cid>/log", methods=["GET"])
@login_required
def api_log(cid):
    return jsonify({"ok": True, "log": read_log(cid)})




@app.route("/api/customers/<cid>/bot/invite", methods=["GET"])
@login_required
def api_bot_invite(cid):
    """Retorna o link de convite usando app_id salvo ou extraído do token."""
    import base64 as _b64

    customers = load_customers()
    c = next((x for x in customers if x["id"] == cid), None)
    if not c:
        return jsonify({"ok": False, "error": "Cliente não encontrado"}), 404

    # Prefer manually saved app_id
    app_id = c.get("app_id", "").strip()

    # Fallback: decode from token first segment
    if not app_id:
        token = c.get("token", "").strip()
        if token and token != "TOKEN_DO_BOT_AQUI":
            try:
                part = token.split(".")[0]
                pad = (4 - len(part) % 4) % 4
                decoded = _b64.b64decode(part + "=" * pad).decode("utf-8", errors="replace").strip()
                if decoded.isdigit() and len(decoded) >= 10:
                    app_id = decoded
            except Exception:
                pass

    if not app_id:
        return jsonify({"ok": False, "error": "Application ID não encontrado. Preencha o campo no formulário."}), 400

    ALLOWED_GUILD = "835695342291779645"
    url = (
        f"https://discord.com/oauth2/authorize"
        f"?client_id={app_id}&permissions=8&scope=bot%20applications.commands"
        f"&guild_id={ALLOWED_GUILD}&disable_guild_select=true"
    )
    return jsonify({"ok": True, "app_id": app_id, "url": url})


@app.route("/api/admin/token-diag", methods=["GET"])
def api_token_diag():
    """Diagnóstico: verifica validade dos tokens do volume e acesso ao servidor novo."""
    import urllib.request as _ureq, urllib.error as _uerr
    secret = request.args.get("secret", "")
    expected = os.environ.get("DASHBOARD_SECRET", "66e8aa01984654baacbf83593587260bb6c69d9907818eb16cf233ffede10ec8")
    if secret != expected:
        return jsonify({"ok": False, "error": "forbidden"}), 403

    NEW_GUILD = "835695342291779645"
    results = []

    all_tokens = []
    for env_key in ("NATA_SOURCE_TOKEN", "DISCORD_TOKEN"):
        t = os.environ.get(env_key, "").strip()
        if t:
            all_tokens.append(("env:" + env_key, t))
    for c in load_customers():
        t = c.get("token", "").strip()
        if t:
            all_tokens.append((c.get("nome", c["id"]), t))

    for tok_name, tok in all_tokens:
        entry = {"name": tok_name}
        req = _ureq.Request("https://discord.com/api/v10/users/@me",
                            headers={"Authorization": f"Bot {tok}"})
        try:
            with _ureq.urlopen(req, timeout=8) as r:
                me = json.loads(r.read())
                entry["token_ok"] = True
                entry["username"] = me.get("username")
                entry["id"] = me.get("id")
        except _uerr.HTTPError as e:
            entry["token_ok"] = False
            entry["token_err"] = e.code
            results.append(entry)
            continue

        req2 = _ureq.Request(f"https://discord.com/api/v10/guilds/{NEW_GUILD}/emojis",
                             headers={"Authorization": f"Bot {tok}"})
        try:
            with _ureq.urlopen(req2, timeout=8) as r:
                entry["emoji_access"] = True
                entry["emoji_count"] = len(json.loads(r.read()))
        except _uerr.HTTPError as e:
            entry["emoji_access"] = False
            entry["emoji_err"] = e.code
        results.append(entry)

    return jsonify({"ok": True, "results": results})


@app.route("/api/admin/leave-guild", methods=["POST"])
def api_admin_leave_guild():
    """Faz todos os bots saírem de um servidor específico."""
    import urllib.request as _ureq, urllib.error as _uerr
    secret = request.args.get("secret", "")
    expected = os.environ.get("DASHBOARD_SECRET", "66e8aa01984654baacbf83593587260bb6c69d9907818eb16cf233ffede10ec8")
    if secret != expected:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    guild_id = request.args.get("guild_id", "").strip()
    if not guild_id:
        return jsonify({"ok": False, "error": "guild_id obrigatorio"}), 400

    results = []
    all_tokens = []
    for env_key in ("NATA_SOURCE_TOKEN", "DISCORD_TOKEN"):
        t = os.environ.get(env_key, "").strip()
        if t:
            all_tokens.append(("env:" + env_key, t))
    for c in load_customers():
        t = c.get("token", "").strip()
        if t:
            all_tokens.append((c.get("nome", c["id"]), t))

    for tok_name, tok in all_tokens:
        req = _ureq.Request(
            f"https://discord.com/api/v10/users/@me/guilds/{guild_id}",
            method="DELETE",
            headers={"Authorization": f"Bot {tok}", "User-Agent": "HypeBot/1.0"}
        )
        try:
            with _ureq.urlopen(req, timeout=10):
                results.append({"name": tok_name, "status": "saiu"})
        except _uerr.HTTPError as e:
            status = "nao_estava" if e.code == 404 else f"erro_{e.code}"
            results.append({"name": tok_name, "status": status})
        except Exception as e:
            results.append({"name": tok_name, "status": f"erro: {str(e)[:40]}"})

    saiu = sum(1 for r in results if r["status"] == "saiu")
    return jsonify({"ok": True, "saiu": saiu, "total": len(results), "results": results})


@app.route("/api/admin/migrate-emojis", methods=["POST"])
def api_migrate_emojis():
    """Copia emojis do servidor antigo para o novo usando tokens dos bots do volume."""
    import urllib.request as _ureq, urllib.error as _uerr, base64 as _b64, time as _time, re as _re
    secret = request.args.get("secret", "")
    expected = os.environ.get("DASHBOARD_SECRET", "66e8aa01984654baacbf83593587260bb6c69d9907818eb16cf233ffede10ec8")
    if secret != expected:
        return jsonify({"ok": False, "error": "forbidden"}), 403

    NEW_GUILD = "835695342291779645"

    # Lê emojis usados no bot.py
    bot_py = Path(__file__).parent / "bot.py"
    content = bot_py.read_text(encoding="utf-8") if bot_py.exists() else ""
    found = _re.findall(r"<(a?):(\w+):(\d+)>", content)
    emoji_list = list({eid: (anim == "a", name) for anim, name, eid in found}.items())

    # Acha token com MANAGE_EMOJIS no novo servidor
    # Inclui: token principal (NATA_SOURCE_TOKEN / DISCORD_TOKEN) + todos os clientes
    good_tok = None
    existing = set()
    tok_debug = []
    all_tokens = []
    for env_key in ("NATA_SOURCE_TOKEN", "DISCORD_TOKEN"):
        t = os.environ.get(env_key, "").strip()
        if t:
            all_tokens.append(("env:" + env_key, t))
    customers = load_customers()
    for c in customers:
        t = c.get("token", "").strip()
        if t:
            all_tokens.append((c.get("nome", c["id"]), t))

    for tok_name, tok in all_tokens:
        req = _ureq.Request(f"https://discord.com/api/v10/guilds/{NEW_GUILD}/emojis",
                            headers={"Authorization": f"Bot {tok}"})
        try:
            with _ureq.urlopen(req, timeout=8) as r:
                existing = {e["name"] for e in json.loads(r.read())}
                good_tok = tok
                tok_debug.append(f"OK: {tok_name}")
                break
        except _uerr.HTTPError as e:
            tok_debug.append(f"ERR {e.code}: {tok_name}")

    if not good_tok:
        return jsonify({"ok": False, "error": "Nenhum bot tem MANAGE_EMOJIS no servidor novo.", "debug": tok_debug}), 400

    results = []
    for eid, (animated, name) in emoji_list:
        if name in existing:
            results.append({"name": name, "status": "skip"})
            continue
        ext = "gif" if animated else "png"
        cdn = f"https://cdn.discordapp.com/emojis/{eid}.{ext}?size=128"
        try:
            with _ureq.urlopen(_ureq.Request(cdn, headers={"User-Agent": "Mozilla/5.0"}), timeout=15) as r:
                img = r.read()
        except Exception as e:
            results.append({"name": name, "status": "erro_download", "detail": str(e)[:80]})
            continue
        mime = "image/gif" if animated else "image/png"
        body = json.dumps({"name": name, "image": f"data:{mime};base64,{_b64.b64encode(img).decode()}"}).encode()
        req2 = _ureq.Request(f"https://discord.com/api/v10/guilds/{NEW_GUILD}/emojis",
                             data=body, method="POST",
                             headers={"Authorization": f"Bot {good_tok}", "Content-Type": "application/json"})
        try:
            with _ureq.urlopen(req2, timeout=15) as r:
                results.append({"name": name, "status": "ok"})
                existing.add(name)
        except _uerr.HTTPError as e:
            body_err = e.read().decode()[:120]
            if e.code == 429:
                _time.sleep(2)
            results.append({"name": name, "status": f"erro_{e.code}", "detail": body_err})
        _time.sleep(0.6)

    ok_count = sum(1 for r in results if r["status"] == "ok")
    return jsonify({"ok": True, "total": len(emoji_list), "copied": ok_count, "results": results})


# ── Bot Control API ───────────────────────────────────────────────────────────

@app.route("/api/customers/<cid>/bot/start", methods=["POST"])
@login_required
def api_bot_start(cid):
    ok, err = bot_manager.start_bot(cid)
    if ok:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": err or "Não foi possível iniciar o bot. Verifique o token e se o bot.py existe."}), 400


@app.route("/api/customers/<cid>/bot/stop", methods=["POST"])
@login_required
def api_bot_stop(cid):
    bot_manager.stop_bot(cid)
    return jsonify({"ok": True})


@app.route("/api/customers/<cid>/bot/leave-guild", methods=["POST"])
@login_required
def api_leave_guild(cid):
    """Faz o bot sair de um servidor específico via Discord API e remove das settings."""
    data     = request.get_json(force=True) or {}
    guild_id = str(data.get("guild_id", "")).strip()
    if not guild_id:
        return jsonify({"ok": False, "error": "guild_id obrigatório"}), 400

    customers = load_customers()
    c = next((x for x in customers if x["id"] == cid), None)
    if not c:
        return jsonify({"ok": False, "error": "Cliente não encontrado"}), 404

    token = c.get("token", "").strip()

    # 1. Tenta sair do servidor via Discord API diretamente (não depende do
    #    processo do bot estar de pé nem do watchdog de settings perceber a mudança)
    discord_ok  = False
    discord_msg = ""
    if token and token != "TOKEN_DO_BOT_AQUI":
        try:
            req = urllib.request.Request(
                f"https://discord.com/api/v10/users/@me/guilds/{guild_id}",
                headers={"Authorization": f"Bot {token}", "User-Agent": "HypeBot/1.0"},
                method="DELETE",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                discord_ok = resp.status == 204
        except urllib.error.HTTPError as e:
            # 404 = bot já não está no servidor → tudo bem, apenas limpa settings
            # 403 = sem permissão (ex: bot é dono) → apenas limpa settings
            discord_msg = {
                404: "Bot não estava no servidor.",
                403: "Sem permissão no Discord (bot pode ser dono do servidor).",
                401: "Token inválido.",
            }.get(e.code, f"Discord API retornou {e.code}.")
        except Exception as ex:
            discord_msg = str(ex)

    # 2. Sempre remove o guild das settings locais (cleanup do dashboard)
    settings = load_settings(cid)
    removed  = guild_id in settings
    if removed:
        settings.pop(guild_id, None)
        save_settings(cid, settings)

    if discord_ok or removed:
        msg = "Bot retirado do servidor com sucesso."
        if discord_msg:
            msg += f" ({discord_msg})"
        return jsonify({"ok": True, "message": msg})

    return jsonify({"ok": False, "error": discord_msg or "Servidor não encontrado nas settings."}), 400


@app.route("/api/customers/<cid>/bot/restart", methods=["POST"])
@login_required
def api_bot_restart(cid):
    ok, err = bot_manager.restart_bot(cid)
    if ok:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": err or "Não foi possível reiniciar o bot."}), 400


@app.route("/api/customers/<cid>/bot/profile", methods=["GET"])
@login_required
def api_bot_profile(cid):
    """Retorna nome e descrição atual do bot.
    Descrição: lê bot_desc_saved.txt (local, sempre atualizado) — sem chamar Discord GET.
    Nome: bot_settings.json → Discord API como fallback.
    """
    customers = load_customers()
    c = next((x for x in customers if x["id"] == cid), None)
    if not c:
        return jsonify({"ok": False, "error": "Cliente não encontrado"}), 404
    token = c.get("token", "").strip()
    if not token or token == "TOKEN_DO_BOT_AQUI":
        return jsonify({"ok": False, "error": "Token não configurado"}), 400

    # Descrição: arquivo local é a fonte de verdade (evita Cloudflare no GET)
    cdir        = CLIENTS_DIR / cid
    saved_desc  = ""
    saved_file  = cdir / "bot_desc_saved.txt"
    if saved_file.exists():
        try:
            saved_desc = saved_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    # Nome: tenta ler de bot_settings.json (já gravado pelo on_ready)
    bot_name = c.get("bot_name", "")
    if not bot_name:
        sf = cdir / "bot_settings.json"
        if sf.exists():
            try:
                d = json.loads(sf.read_text(encoding="utf-8"))
                # bot_name fica em qualquer guild settings
                for v in d.values():
                    if isinstance(v, dict) and v.get("bot_name"):
                        bot_name = v["bot_name"]
                        break
            except Exception:
                pass

    # Fallback: Discord API para o nome (não bloqueada normalmente)
    if not bot_name:
        profile = _get_bot_profile(token)
        if profile:
            bot_name = profile.get("name", "")
            if not saved_desc:
                saved_desc = profile.get("description", "")
        elif not saved_desc:
            # Sem nome e sem descrição — falha definitiva
            return jsonify({"ok": False, "error": "Não foi possível buscar o perfil — verifique o token"}), 502

    return jsonify({"ok": True, "name": bot_name, "description": saved_desc})


@app.route("/api/customers/<cid>/bot/description", methods=["POST"])
@login_required
def api_set_description(cid):
    """Define a descrição/bio do bot.

    Escreve um arquivo de sinalização no diretório do cliente. O bot o lê no
    watchdog loop (a cada ~8s) e faz a chamada Discord de dentro do próprio
    processo — evitando bloqueios do Cloudflare para requests externos.
    """
    customers = load_customers()
    c = next((x for x in customers if x["id"] == cid), None)
    if not c:
        return jsonify({"ok": False, "error": "Cliente não encontrado"}), 404
    token = c.get("token", "").strip()
    if not token or token == "TOKEN_DO_BOT_AQUI":
        return jsonify({"ok": False, "error": "Token não configurado"}), 400
    data = request.get_json(force=True) or {}
    description = str(data.get("description", "")).strip()[:400]

    cdir     = CLIENTS_DIR / cid
    req_file = cdir / "bot_desc_pending.txt"
    res_file = cdir / "bot_desc_result.txt"

    # Remove resultado anterior
    try: res_file.unlink()
    except FileNotFoundError: pass

    # Escreve o pedido para o bot processar
    req_file.write_text(description, encoding="utf-8")

    # Aguarda resposta do bot por até 12 segundos (watchdog roda a cada 8s)
    deadline = _time.time() + 12
    while _time.time() < deadline:
        _time.sleep(0.5)
        if res_file.exists():
            result = res_file.read_text(encoding="utf-8").strip()
            try: res_file.unlink()
            except Exception: pass
            if result == "ok":
                return jsonify({"ok": True})
            return jsonify({"ok": False, "error": result}), 500

    # Timeout — bot vai processar em breve mesmo assim
    return jsonify({"ok": True, "warning": "Descrição sendo aplicada em segundo plano (pode levar alguns segundos)"})


@app.route("/api/customers/<cid>/apply-avatar", methods=["POST"])
@login_required
def api_apply_avatar_customer(cid):
    """Aplica o avatar/ícone OAuth2 para um cliente específico.
    Usa o avatar customizado do cliente se existir, senão usa o default_avatar.png."""
    customers = load_customers()
    c = next((x for x in customers if x["id"] == cid), None)
    if not c:
        return jsonify({"ok": False, "error": "Cliente não encontrado"}), 404
    token = c.get("token", "").strip()
    if not token or token == "TOKEN_DO_BOT_AQUI":
        return jsonify({"ok": False, "error": "Token não configurado"}), 400

    # Prefere o avatar customizado do cliente
    client_avatar = CLIENTS_DIR / cid / "bot_avatar.png"
    if client_avatar.exists() and client_avatar.stat().st_size > 0:
        ok = _apply_avatar_bytes_to_token(token, client_avatar.read_bytes())
    else:
        ok = _apply_default_avatar_to_token(token)

    if ok:
        return jsonify({"ok": True, "msg": "Ícone OAuth2 atualizado com sucesso!"})
    return jsonify({"ok": False, "error": "Falha ao aplicar — verifique os logs do servidor"}), 500


@app.route("/api/bots/restart-all", methods=["POST"])
@login_required
def api_restart_all():
    """Para e reinicia todos os bots ativos."""
    results = bot_manager.restart_all()
    ok_count  = sum(1 for v in results.values() if v)
    err_count = sum(1 for v in results.values() if not v)
    return jsonify({"ok": True, "started": ok_count, "failed": err_count, "details": results})


# ── Owners API ────────────────────────────────────────────────────────────────

@app.route("/api/owners", methods=["GET"])
@super_required
def api_owners_list():
    cfg = load_config()
    # Return owners without password data
    result = [
        {"id": o["id"], "nome": o["nome"], "role": o.get("role", "admin")}
        for o in cfg.get("owners", [])
    ]
    return jsonify(result)


@app.route("/api/owners", methods=["POST"])
@super_required
def api_owners_add():
    data     = request.get_json(force=True) or {}
    oid      = data.get("id", "").strip()
    nome     = data.get("nome", "").strip()
    password = data.get("password", "")
    role     = data.get("role", "admin")

    if not oid or not nome or not password:
        return jsonify({"ok": False, "error": "ID, nome e senha são obrigatórios"}), 400
    if len(password) < 6:
        return jsonify({"ok": False, "error": "Senha deve ter pelo menos 6 caracteres"}), 400
    if role not in ("admin", "super"):
        role = "admin"

    cfg = load_config()
    if any(o["id"] == oid for o in cfg.get("owners", [])):
        return jsonify({"ok": False, "error": f"Usuário '{oid}' já existe"}), 400

    h, salt = _hash(password)
    cfg.setdefault("owners", []).append({
        "id":   oid,
        "nome": nome,
        "hash": h,
        "salt": salt,
        "role": role,
    })
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/owners/<oid>", methods=["DELETE"])
@super_required
def api_owners_delete(oid):
    if oid == session.get("uid"):
        return jsonify({"ok": False, "error": "Você não pode remover sua própria conta"}), 400
    cfg = load_config()
    owners = cfg.get("owners", [])
    new_owners = [o for o in owners if o["id"] != oid]
    if len(new_owners) == len(owners):
        return jsonify({"ok": False, "error": "Usuário não encontrado"}), 404
    # Prevent removing the last super admin
    supers = [o for o in new_owners if o.get("role") == "super"]
    if not supers:
        return jsonify({"ok": False, "error": "Não é possível remover o único Super Admin"}), 400
    cfg["owners"] = new_owners
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/owners/<oid>/password", methods=["PUT"])
@login_required
def api_owners_password(oid):
    data        = request.get_json(force=True) or {}
    new_password = data.get("password", "")
    old_password = data.get("old_password", "")
    requester   = get_owner(session["uid"])

    if len(new_password) < 6:
        return jsonify({"ok": False, "error": "Senha deve ter pelo menos 6 caracteres"}), 400

    target = get_owner(oid)
    if not target:
        return jsonify({"ok": False, "error": "Usuário não encontrado"}), 404

    is_self  = (oid == session["uid"])
    is_super = requester and requester.get("role") == "super"

    # Must be self or super admin to change password
    if not is_self and not is_super:
        return jsonify({"ok": False, "error": "Acesso negado"}), 403

    # If changing own password, verify old password
    if is_self:
        if not _verify(old_password, target["hash"], target["salt"]):
            return jsonify({"ok": False, "error": "Senha atual incorreta"}), 400

    cfg = load_config()
    for o in cfg.get("owners", []):
        if o["id"] == oid:
            o["hash"], o["salt"] = _hash(new_password)
            save_config(cfg)
            return jsonify({"ok": True})

    return jsonify({"ok": False, "error": "Erro ao salvar"}), 500


# ── Avatar padrão como rota estática ─────────────────────────────────────────

@app.route("/bot-avatar.png")
def bot_avatar_png():
    """Serve o default_avatar.png como imagem para uso no site."""
    from flask import send_file, abort
    avatar_path = CODE_DIR / "default_avatar.png"
    if not avatar_path.exists():
        abort(404)
    return send_file(str(avatar_path), mimetype="image/png")


@app.route("/bot-banner.png")
def bot_banner_png():
    """Serve o default_banner.png como embed banner permanente."""
    from flask import send_file, abort
    banner_path = CODE_DIR / "default_banner.png"
    if not banner_path.exists():
        abort(404)
    return send_file(str(banner_path), mimetype="image/png")


@app.route("/api/apply-avatar-all", methods=["POST"])
@login_required
def api_apply_avatar_all():
    """Aplica o default_avatar.png como avatar + ícone OAuth2 em todos os bots ativos."""
    avatar_path = CODE_DIR / "default_avatar.png"
    if not avatar_path.exists():
        return jsonify({"ok": False, "error": "default_avatar.png não encontrado"}), 400

    customers = load_customers()
    count = 0
    errors = []
    for c in customers:
        if not c.get("ativo", True):
            continue
        token = c.get("token", "").strip()
        if not token or token == "TOKEN_DO_BOT_AQUI":
            continue
        try:
            ok = _apply_default_avatar_to_token(token)
            if ok:
                count += 1
            else:
                errors.append(c.get("id", "?"))
        except Exception as e:
            errors.append(f"{c.get('id','?')}: {e}")

    return jsonify({"ok": True, "count": count, "errors": errors})


@app.route("/api/set-avatar-from-url", methods=["POST"])
@login_required
def api_set_avatar_from_url():
    """Baixa imagem de uma URL, salva como default_avatar.png e aplica em todos os bots."""
    data = request.get_json(force=True) or {}
    url  = (data.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "URL obrigatória"}), 400

    # Baixa a imagem
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "HypeBot/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            img_bytes = resp.read()
    except Exception as e:
        return jsonify({"ok": False, "error": f"Falha ao baixar imagem: {e}"}), 400

    if not img_bytes or len(img_bytes) < 100:
        return jsonify({"ok": False, "error": "Imagem inválida ou vazia"}), 400

    # Salva como default_avatar.png
    avatar_path = CODE_DIR / "default_avatar.png"
    try:
        avatar_path.write_bytes(img_bytes)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Falha ao salvar arquivo: {e}"}), 500

    # Aplica em todos os bots ativos
    customers = load_customers()
    count  = 0
    errors = []
    for c in customers:
        if not c.get("ativo", True):
            continue
        token = c.get("token", "").strip()
        if not token or token == "TOKEN_DO_BOT_AQUI":
            continue
        try:
            ok = _apply_default_avatar_to_token(token)
            if ok:
                count += 1
            else:
                errors.append(c.get("id", "?"))
        except Exception as e:
            errors.append(f"{c.get('id','?')}: {e}")

    return jsonify({
        "ok":    True,
        "bytes": len(img_bytes),
        "count": count,
        "errors": errors,
    })


# ── Render Avatar + Decoration (Discord-style) ────────────────────────────────

@app.route("/api/v1/render-avatar-deco")
def api_render_avatar_deco():
    """
    Gera um avatar circular com a decoração (GIF ou PNG) sobreposta no padrão Discord.
    Query params:
      av  — URL do avatar
      deco — URL da decoração (opcional)
    Retorna PNG ou GIF.
    """
    import urllib.request as _ur
    import io as _io

    try:
        from PIL import Image, ImageDraw, ImageSequence
    except ImportError:
        return jsonify({"error": "Pillow não instalado no servidor"}), 500

    av_url   = request.args.get("av",   "").strip()
    deco_url = request.args.get("deco", "").strip()

    if not av_url:
        return jsonify({"error": "Parâmetro 'av' obrigatório"}), 400

    # Proporções exatas do Discord:
    #   avatar visual = 80 px  →  decoração = 112 px  (fator 1.40)
    # Usamos 320 px para qualidade, canvas = 448 px.
    AV_SIZE   = 320
    CANVAS    = int(AV_SIZE * 1.40)   # 448 px
    AV_OFFSET = (CANVAS - AV_SIZE) // 2  # 64 px de margem em cada lado

    def _dl(url: str) -> bytes:
        req = _ur.Request(url, headers={"User-Agent": "HypeBot/1.0"})
        with _ur.urlopen(req, timeout=15) as r:
            return r.read()

    # ── Avatar ────────────────────────────────────────────────────────────────
    try:
        av_bytes = _dl(av_url)
    except Exception as e:
        return jsonify({"error": f"Falha ao baixar avatar: {e}"}), 502

    av_img = Image.open(_io.BytesIO(av_bytes)).convert("RGBA").resize(
        (AV_SIZE, AV_SIZE), Image.LANCZOS
    )
    # Máscara circular para o avatar
    av_mask = Image.new("L", (AV_SIZE, AV_SIZE), 0)
    ImageDraw.Draw(av_mask).ellipse((0, 0, AV_SIZE - 1, AV_SIZE - 1), fill=255)
    av_circle = Image.new("RGBA", (AV_SIZE, AV_SIZE), (0, 0, 0, 0))
    av_circle.paste(av_img, mask=av_mask)

    # Avatar centralizado no canvas maior (que vai receber a decoração)
    av_canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    av_canvas.paste(av_circle, (AV_OFFSET, AV_OFFSET))

    if not deco_url:
        out = _io.BytesIO()
        av_canvas.save(out, format="PNG")
        out.seek(0)
        return out.read(), 200, {"Content-Type": "image/png", "Cache-Control": "max-age=300"}

    # ── Decoração ─────────────────────────────────────────────────────────────
    try:
        deco_bytes = _dl(deco_url)
    except Exception:
        out = _io.BytesIO()
        av_canvas.save(out, format="PNG")
        out.seek(0)
        return out.read(), 200, {"Content-Type": "image/png"}

    deco_img = Image.open(_io.BytesIO(deco_bytes))
    is_gif   = getattr(deco_img, "is_animated", False) or deco_img.format == "GIF"

    def _composite_frame(deco_frame: Image.Image) -> Image.Image:
        """Sobrepõe um frame da decoração sobre o canvas do avatar."""
        df = deco_frame.convert("RGBA").resize((CANVAS, CANVAS), Image.LANCZOS)
        base = av_canvas.copy()
        # A decoração fica POR CIMA do avatar (como o Discord faz)
        base.alpha_composite(df)
        # Clipar o resultado final a um círculo de tamanho CANVAS
        # para que o anel/decoração não ultrapasse a borda circular visível
        canvas_mask = Image.new("L", (CANVAS, CANVAS), 0)
        ImageDraw.Draw(canvas_mask).ellipse((0, 0, CANVAS - 1, CANVAS - 1), fill=255)
        result = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
        result.paste(base, mask=canvas_mask)
        return result

    if is_gif:
        frames    = []
        durations = []
        for frame in ImageSequence.Iterator(deco_img):
            durations.append(frame.info.get("duration", 100))
            frames.append(_composite_frame(frame))

        out = _io.BytesIO()
        frames[0].save(
            out,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            loop=0,
            duration=durations,
            optimize=False,
            disposal=2,
        )
        out.seek(0)
        return out.read(), 200, {
            "Content-Type": "image/gif",
            "Cache-Control": "max-age=300",
        }
    else:
        composite = _composite_frame(deco_img)
        out = _io.BytesIO()
        composite.save(out, format="PNG")
        out.seek(0)
        return out.read(), 200, {
            "Content-Type": "image/png",
            "Cache-Control": "max-age=300",
        }


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("DASHBOARD_PORT", 5500))
    print(f"\n{'='*50}")
    print(f"  HypeBot Dashboard")
    print(f"  http://localhost:{port}")
    print(f"{'='*50}\n")

    cfg = load_config()
    if not cfg.get("owners"):
        print("  ⚠️  Nenhum dono cadastrado.")
        print(f"  -> Acesse http://localhost:{port}/setup para criar sua conta.\n")
    else:
        print(f"  Donos: {len(cfg['owners'])}")
        print(f"  Pressione Ctrl+C para parar.\n")

    app.run(host="0.0.0.0", port=port, debug=False)
