#!/bin/bash
# HypeBot - Script de inicialização no Railway
# Inicia o gerenciador de bots em background e o dashboard web

set -e

export DATA_DIR="${DATA_DIR:-/data}"
export PYTHONUNBUFFERED=1

echo "=== HypeBot iniciando ==="
echo "DATA_DIR: $DATA_DIR"

# Garante que a pasta de dados existe
mkdir -p "$DATA_DIR/clientes"

# Executa migração de dados (copia settings do código para o volume /data)
echo "Executando migração de dados..."
/opt/venv/bin/python manager.py --migrate-only
echo "Migração concluída."

# Aguarda 1 segundo
sleep 1

# Inicia o dashboard web (processo principal)
echo "Iniciando dashboard na porta $PORT..."
exec /opt/venv/bin/gunicorn dashboard:app \
  --bind "0.0.0.0:${PORT:-5500}" \
  --workers 1 \
  --timeout 120 \
  --access-logfile - \
  --error-logfile -
