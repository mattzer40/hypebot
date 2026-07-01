"""
Copia as configurações de um guild para outro no arquivo bot_settings.json.
Uso: python _transfer_guild_settings.py

Copia: 1506247261011578890 → 835695342291779645
"""
import json
import os
import sys
from pathlib import Path

OLD_GUILD = "1506247261011578890"
NEW_GUILD = "835695342291779645"

HERE = Path(os.path.dirname(os.path.abspath(__file__)))
DATA = Path(os.environ.get("DATA_DIR", str(HERE)))


def find_settings_files():
    candidates = []

    # 1. BOT_SETTINGS_FILE explícito
    env_sf = os.environ.get("BOT_SETTINGS_FILE")
    if env_sf:
        candidates.append(Path(env_sf))

    # 2. Padrão: ao lado de bot.py
    candidates.append(HERE / "bot_settings.json")

    # 3. DATA_DIR
    candidates.append(DATA / "bot_settings.json")

    # 4. bot_settings.json de cada cliente
    clientes = DATA / "clientes"
    if clientes.exists():
        for cdir in sorted(clientes.iterdir()):
            sf = cdir / "bot_settings.json"
            if sf.exists():
                candidates.append(sf)

    return candidates


def main():
    found = None
    for sf in find_settings_files():
        if not sf.exists():
            continue
        try:
            with open(sf, encoding="utf-8") as f:
                data = json.load(f)
            if OLD_GUILD in data:
                found = (sf, data)
                break
        except Exception as e:
            print(f"  [!] Erro ao ler {sf}: {e}")

    if found is None:
        # Lista todos os arquivos encontrados para diagnóstico
        print(f"[!] Guild {OLD_GUILD} não encontrado em nenhum bot_settings.json")
        print("\nArquivos verificados:")
        for sf in find_settings_files():
            exists = sf.exists()
            guilds = []
            if exists:
                try:
                    with open(sf, encoding="utf-8") as f:
                        d = json.load(f)
                    guilds = list(d.keys())[:5]
                except Exception:
                    guilds = ["<erro ao ler>"]
            print(f"  {'[OK]' if exists else '[NÃO EXISTE]'} {sf}")
            if guilds:
                print(f"       Guilds: {guilds}")
        sys.exit(1)

    sf, data = found
    print(f"[OK] Encontrado em: {sf}")
    print(f"[OK] Guild de origem: {OLD_GUILD}")

    if NEW_GUILD in data:
        print(f"[!] Guild destino {NEW_GUILD} já existe — será SOBRESCRITO com os dados do servidor antigo.")
        resp = input("Continuar? [s/N] ").strip().lower()
        if resp != "s":
            print("Abortado.")
            sys.exit(0)

    # Faz a cópia
    import copy
    data[NEW_GUILD] = copy.deepcopy(data[OLD_GUILD])
    print(f"[OK] Configuracoes copiadas: {OLD_GUILD} -> {NEW_GUILD}")

    # Remove referências ao guild antigo em dono_call_channels, etc. se necessário
    # (deixa as IDs de canal como estão — o Discord pode ter IDs diferentes no novo servidor,
    #  mas isso é normal e o usuário pode reconfigurar via painel)

    with open(sf, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[OK] Salvo em: {sf}")
    print("\nConfiguração copiada com sucesso!")
    print(f"  Servidor antigo: {OLD_GUILD} (teste bot)")
    print(f"  Servidor novo:   {NEW_GUILD} (teste hype)")
    print("\nReinicie o bot para carregar as novas configuracoes.")


if __name__ == "__main__":
    main()
