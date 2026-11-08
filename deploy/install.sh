#!/usr/bin/env bash
# Установка или обновление бота на сервере (Debian/Ubuntu).
# Запускать от root из папки, куда загружены файлы бота вместе с этим скриптом:
#   bash install.sh                  # установка / обновление кода
#   bash install.sh --replace-env    # то же + заменить токен (.env) на загруженный
# .env и reminders.db копируются, только если на сервере их ещё нет —
# при обновлении кода токен и напоминания на сервере не затираются.
set -euo pipefail

APP_DIR=/opt/reminder-bot
APP_USER=reminderbot
SERVICE=reminder-bot
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
REPLACE_ENV=0
[ "${1:-}" = "--replace-env" ] && REPLACE_ENV=1

echo "==> Python"
if ! python3 -c 'import venv, ensurepip' 2>/dev/null; then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv >/dev/null
fi
python3 -c 'import sys; assert sys.version_info >= (3, 10), "нужен Python 3.10+, а тут " + sys.version'
python3 --version

echo "==> Пользователь и папка $APP_DIR"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
install -d -o "$APP_USER" -g "$APP_USER" -m 750 "$APP_DIR"

echo "==> Файлы бота"
install -o "$APP_USER" -g "$APP_USER" -m 640 \
    "$SRC_DIR"/main.py "$SRC_DIR"/storage.py "$SRC_DIR"/keyboards.py "$SRC_DIR"/scheduler.py \
    "$SRC_DIR"/requirements.txt "$APP_DIR"/
for f in .env reminders.db; do
    if [ -f "$SRC_DIR/$f" ] && { [ ! -f "$APP_DIR/$f" ] || { [ "$f" = .env ] && [ "$REPLACE_ENV" = 1 ]; }; }; then
        install -o "$APP_USER" -g "$APP_USER" -m 600 "$SRC_DIR/$f" "$APP_DIR/$f"
        echo "    скопирован $f"
    fi
done
if [ ! -f "$APP_DIR/.env" ]; then
    echo "!!! Нет $APP_DIR/.env с BOT_TOKEN=... — бот не запустится" >&2
    exit 1
fi

echo "==> Библиотеки (venv)"
[ -x "$APP_DIR/venv/bin/python" ] || runuser -u "$APP_USER" -- python3 -m venv "$APP_DIR/venv"
runuser -u "$APP_USER" -- "$APP_DIR/venv/bin/pip" install -q --disable-pip-version-check -r "$APP_DIR/requirements.txt"

echo "==> Служба systemd"
install -m 644 "$SRC_DIR/$SERVICE.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1
systemctl restart "$SERVICE"
sleep 5
systemctl --no-pager --lines=15 status "$SERVICE" || true
