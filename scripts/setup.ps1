<#
  Prova Pronta - instalação no Windows.
  Prepara o Python, o site, os dados de OCR em português e o arquivo .env.
  Uso: clique duas vezes em "Instalar Prova Pronta.bat" (ou rode este script no PowerShell).
#>
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "    OK  $text" -ForegroundColor Green }
function Warn($text) { Write-Host "    !!  $text" -ForegroundColor Yellow }

function Rest($array) { if ($array.Length -gt 1) { return , $array[1..($array.Length - 1)] } return , @() }

function Find-Python {
    foreach ($candidate in @('py -3.12', 'py -3.13', 'python')) {
        $parts = $candidate.Split(' ')
        $exe = Get-Command $parts[0] -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        $probe = @(Rest $parts) + @('-c', 'import sys; print(sys.version_info >= (3, 12))')
        # The Microsoft Store "python" alias prints an error instead of running: treat it as missing.
        try { $result = & $parts[0] @probe 2>$null } catch { $result = $null }
        if ($result -eq 'True') { return , $parts }
    }
    $local = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
    if (Test-Path $local) { return , @($local) }
    return $null
}

Step 'Verificando programas necessários'
$python = Find-Python
if (-not $python) {
    Warn 'Python 3.12 não encontrado. Instale com:  winget install Python.Python.3.12'
    exit 1
}
Ok "Python: $($python -join ' ')"

$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node -and (Test-Path 'C:\Program Files\nodejs\node.exe')) { $env:Path = "C:\Program Files\nodejs;$env:Path"; $node = Get-Command node }
if (-not $node) { Warn 'Node.js não encontrado. Instale com:  winget install OpenJS.NodeJS.LTS'; exit 1 }
Ok "Node.js: $(node --version)"

$soffice = @('C:\Program Files\LibreOffice\program\soffice.exe') | Where-Object { Test-Path $_ }
if ($soffice) { Ok 'LibreOffice (gera o PDF)' } else { Warn 'LibreOffice não encontrado: sem ele o PDF usa o Microsoft Word, se houver. Instale com: winget install TheDocumentFoundation.LibreOffice' }

$tesseract = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
if (Test-Path $tesseract) { Ok 'Tesseract (lê fotos e PDFs digitalizados)' } else { Warn 'Tesseract não encontrado: fotos não serão lidas. Instale com: winget install UB-Mannheim.TesseractOCR' }

Step 'Preparando o ambiente Python (backend)'
$venvPython = Join-Path $Root 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    $pyArgs = @(Rest $python) + @('-m', 'venv', 'backend\.venv')
    & $python[0] @pyArgs
}
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install --quiet -e "backend[dev,ai]"
Ok 'Dependências do backend instaladas'

Step 'Baixando os dados de OCR em português'
$tessdata = Join-Path $Root 'backend\vendor\tessdata'
New-Item -ItemType Directory -Force $tessdata | Out-Null
if (-not (Test-Path (Join-Path $tessdata 'por.traineddata'))) {
    Invoke-WebRequest 'https://github.com/tesseract-ocr/tessdata_best/raw/main/por.traineddata' -OutFile (Join-Path $tessdata 'por.traineddata')
}
foreach ($lang in @('osd', 'eng')) {
    $src = "C:\Program Files\Tesseract-OCR\tessdata\$lang.traineddata"
    if ((Test-Path $src) -and -not (Test-Path (Join-Path $tessdata "$lang.traineddata"))) { Copy-Item $src $tessdata }
}
Ok 'Português pronto para o OCR'

Step 'Instalando e compilando o site (frontend)'
Push-Location (Join-Path $Root 'frontend')
npm ci --no-fund --no-audit
npm run build
Pop-Location
Ok 'Site compilado'

Step 'Configuração'
if (-not (Test-Path (Join-Path $Root '.env'))) {
    Copy-Item (Join-Path $Root '.env.example') (Join-Path $Root '.env')
    Ok 'Arquivo .env criado (e-mails ficam em modo de teste até configurar o envio)'
} else { Ok '.env já existe (mantido)' }

Write-Host "`nTudo pronto! Para abrir, clique duas vezes em 'Iniciar Prova Pronta.bat'." -ForegroundColor Green
