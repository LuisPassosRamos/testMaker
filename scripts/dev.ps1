<#
  Development mode: API with auto-reload on :8000 and Vite (hot reload) on :5173.
#>
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $Root 'backend\.venv\Scripts\python.exe'
Start-Process -FilePath $python -ArgumentList @('-m', 'uvicorn', '--app-dir', (Join-Path $Root 'backend'), 'app.main:app', '--reload', '--port', '8000') -WorkingDirectory (Join-Path $Root 'backend')
Push-Location (Join-Path $Root 'frontend')
npm run dev
Pop-Location
