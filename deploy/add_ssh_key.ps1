# Один раз: добавить SSH-ключ этого компьютера на сервер, чтобы дальше входить без пароля.
# Запуск из папки бота:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\add_ssh_key.ps1
param([string]$Server)
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

$pubKey = (Get-Content -Raw "$env:USERPROFILE\.ssh\id_ed25519.pub").Trim()

Write-Host ''
Write-Host "Подключаюсь к $Server" -ForegroundColor Cyan
Write-Host 'Когда появится "password:", введите пароль root от сервера (символы не отображаются) и нажмите Enter.' -ForegroundColor Yellow
Write-Host ''

$remote = "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && (grep -qxF '$pubKey' ~/.ssh/authorized_keys || echo '$pubKey' >> ~/.ssh/authorized_keys) && chmod 600 ~/.ssh/authorized_keys && echo KEY_ADDED"
ssh -o StrictHostKeyChecking=accept-new $Server $remote

if ($LASTEXITCODE -eq 0) {
    Write-Host ''
    Write-Host 'Готово: ключ добавлен. Это окно можно закрыть.' -ForegroundColor Green
} else {
    Write-Host ''
    Write-Host 'Не получилось (неверный пароль?). Запустите скрипт ещё раз.' -ForegroundColor Red
}
