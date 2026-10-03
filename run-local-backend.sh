#!/bin/bash
# 本地启动 Plane 后端(连 226 中间件)。用法:
#   ./run-local-backend.sh migrate   # 跑迁移(首次/改模型后)
#   ./run-local-backend.sh server    # 起 API (runserver, 端口 8000)
#   ./run-local-backend.sh worker    # 起 celery worker
#   ./run-local-backend.sh beat      # 起 celery beat
set -e
cd "$(dirname "$0")/apps/api"

# 加载 env(连 226 的 Postgres 5433 / Redis 6379 / MinIO 9000 / RabbitMQ 5672)
set -a; source .env; set +a
export DJANGO_SETTINGS_MODULE=plane.settings.local

PY=../../.venv/bin/python

case "$1" in
  migrate)
    exec $PY manage.py migrate --settings=plane.settings.local --no-input
    ;;
  server)
    exec $PY manage.py runserver 0.0.0.0:8000 --settings=plane.settings.local
    ;;
  worker)
    exec $PY -m celery -A plane worker -l info
    ;;
  beat)
    exec $PY -m celery -A plane beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
    ;;
  backfill-pages)
    shift
    exec $PY manage.py backfill_page_markdown --settings=plane.settings.local "$@"
    ;;
  *)
    echo "用法: $0 {migrate|server|worker|beat|backfill-pages}" >&2
    exit 1
    ;;
esac
