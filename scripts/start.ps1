<#
  Prova Pronta - abre o aplicativo neste computador em http://localhost:8000
  Deixe esta janela aberta enquanto usa o Prova Pronta; feche-a para encerrar.
#>
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $Root 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host "O Prova Pronta ainda não foi instalado. Clique em 'Instalar Prova Pronta.bat' primeiro." -ForegroundColor Yellow
    Read-Host 'Pressione Enter para fechar'
    exit 1
}
$url = 'http://localhost:8000'
Write-Host "Abrindo o Prova Pronta em $url ..." -ForegroundColor Cyan
Write-Host 'Deixe esta janela aberta enquanto estiver usando. Para encerrar, feche-a.' -ForegroundColor Gray
Start-Job -ScriptBlock { param($u) Start-Sleep -Seconds 3; Start-Process $u } -ArgumentList $url | Out-Null
& $python -m uvicorn --app-dir (Join-Path $Root 'backend') app.main:app --host 127.0.0.1 --port 8000
