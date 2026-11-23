#!/bin/sh
set -eu

# When started as root, make sure the mounted data folders are writable by the
# unprivileged "app" user (top level only: files are created by app anyway),
# then re-exec this script as that user.
if [ "$(id -u)" = "0" ]; then
  for d in "$MEDIA_DIR" "$MODELS_DIR"; do
    mkdir -p "$d"
    [ "$(stat -c %u "$d")" = "$(id -u app)" ] || chown app:app "$d"
  done
  exec setpriv --reuid=app --regid=app --init-groups "$0" "$@"
fi

case "${1:-web}" in
  web)
    alembic upgrade head
    exec uvicorn app.main:app \
      --host 0.0.0.0 --port 8000 \
      --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}" \
      --no-server-header --timeout-keep-alive 75
    ;;
  worker)
    exec python -m app.worker
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  *)
    exec "$@"
    ;;
esac
