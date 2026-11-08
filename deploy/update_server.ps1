# Установить или обновить бота на сервере (Debian/Ubuntu).
# Запуск из папки бота:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\update_server.ps1          # код
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\update_server.ps1 -Token   # код + новый токен из .env
# Без -Token токен (.env) и напоминания (reminders.db) на сервере не перезаписываются;
# .env с компьютера используется только при первой установке.
param([string]$Server, [switch]$Token)
$ErrorActionPreference = 'Stop'

# Адрес сервера: параметр -Server или файл deploy\server.txt (строка вида root@1.2.3.4).
# server.txt не попадает в git — пример в deploy\server.example.txt
if (-not $Server) {
    $cfg = Join-Path $PSScriptRoot 'server.txt'
    if (Test-Path $cfg) { $Server = (Get-Content -Raw $cfg).Trim() }
}
if (-not $Server) {
    Write-Host 'Не указан сервер. Создайте файл deploy\server.txt со строкой вида root@1.2.3.4' -ForegroundColor Red
    Write-Host 'или запустите скрипт с параметром -Server root@1.2.3.4' -ForegroundColor Red
    exit 1
}

$upload = '/root/reminder-bot-upload'
$ssh = @('-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new')

Set-Location (Split-Path -Parent $PSScriptRoot)

function Check($step) {
    if ($LASTEXITCODE -ne 0) { Write-Host "Ошибка на шаге: $step" -ForegroundColor Red; exit 1 }
}

# Все .py бота из корня папки (тесты в tests/ не нужны на сервере)
$files = @(Get-ChildItem -File -Filter '*.py' | ForEach-Object { $_.Name }) +
         @('requirements.txt', 'deploy/install.sh', 'deploy/reminder-bot.service')
if (Test-Path '.env') { $files += '.env' }
elseif ($Token) { Write-Host 'Нет файла .env с BOT_TOKEN=...' -ForegroundColor Red; exit 1 }

$installArgs = ''
if ($Token) { $installArgs = '--replace-env' }

Write-Host "Загружаю файлы на $Server..." -ForegroundColor Cyan
ssh @ssh $Server "rm -rf $upload && install -d -m 700 $upload"; Check 'подготовка папки'
scp @ssh @files "${Server}:$upload/"; Check 'копирование'

Write-Host 'Устанавливаю и перезапускаю бота...' -ForegroundColor Cyan
ssh @ssh $Server "bash $upload/install.sh $installArgs; rc=`$?; rm -rf $upload; exit `$rc"; Check 'установка'

Write-Host 'Готово: бот обновлён и перезапущен.' -ForegroundColor Green
