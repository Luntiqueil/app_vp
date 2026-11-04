#!/bin/bash
# Запуск приложения: Mac не засыпает, пока оно работает, а при сбое оно перезапускается.
cd "$(dirname "$0")" || exit 1
while true; do
  caffeinate -i .venv/bin/python -m photoapp
  echo "Приложение остановилось, перезапуск через 5 секунд (Ctrl+C — выйти)..."
  sleep 5
done
