Param(
  [int]$FrontendPort = 5173,
  [int]$BackendPort = 8000,
  [switch]$Install,
  [string]$BaseUrl = 'http://127.0.0.1:11434/v1',
  [string]$Model = 'chatai-local'
)
$ErrorActionPreference = 'Stop'
$backend = Join-Path $PSScriptRoot 'backend'
$frontend = Join-Path $PSScriptRoot 'frontend'
$env:OLLAMA_BASE_URL = $BaseUrl
$env:LOCAL_MODEL = $Model
$env:CHATAI_BACKEND_URL = "http://127.0.0.1:$BackendPort"
try {
  $models = Invoke-RestMethod ($BaseUrl.TrimEnd('/') + '/models') -TimeoutSec 5
} catch {
  if ($BaseUrl.TrimEnd('/') -eq 'http://127.0.0.1:11434/v1') {
    & (Join-Path $PSScriptRoot 'setup-local-model.ps1')
    $models = Invoke-RestMethod ($BaseUrl.TrimEnd('/') + '/models') -TimeoutSec 5
  } else { throw "Cannot connect to model service at $BaseUrl." }
}
if ($Model -notin $models.data.id -and "${Model}:latest" -notin $models.data.id) {
  if ($Model -eq 'chatai-local' -and $BaseUrl.TrimEnd('/') -eq 'http://127.0.0.1:11434/v1') {
    & (Join-Path $PSScriptRoot 'setup-local-model.ps1')
  } else { throw "Model $Model is missing from $BaseUrl." }
}
$pythonExe = Join-Path $backend '.venv/Scripts/python.exe'
if (!(Test-Path $pythonExe)) {
  if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv (Join-Path $backend '.venv') }
  else { & python -m venv (Join-Path $backend '.venv') }
  if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11 or 3.12 and retry.' }
  $Install = $true
}
& $pythonExe -c 'import sys; print(sys.version)'
if ($LASTEXITCODE -ne 0) { throw 'Broken backend/.venv. Install Python 3.12, rename the old .venv, then rerun with -Install.' }
if ($Install) {
  & $pythonExe -m pip install -r (Join-Path $backend 'requirements.txt')
  if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }
}
Push-Location $frontend
try {
  if ($Install -or !(Test-Path 'node_modules')) {
    & npm install
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
  }
} finally { Pop-Location }
foreach ($port in @($BackendPort, $FrontendPort)) {
  if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $port is already in use. Stop that service or choose another port."
  }
}
Start-Process -WindowStyle Hidden -WorkingDirectory $backend -FilePath $pythonExe -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port',"$BackendPort" -RedirectStandardOutput (Join-Path $backend 'server.log') -RedirectStandardError (Join-Path $backend 'server-error.log')
Start-Process -WindowStyle Hidden -WorkingDirectory $frontend -FilePath 'powershell.exe' -ArgumentList '-NoProfile','-Command',"npm run dev -- --port $FrontendPort" -RedirectStandardOutput (Join-Path $frontend 'server.log') -RedirectStandardError (Join-Path $frontend 'server-error.log')
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
  Start-Sleep -Seconds 1
  try {
    $null = Invoke-WebRequest "http://127.0.0.1:$BackendPort/openapi.json" -UseBasicParsing -TimeoutSec 2
    $null = Invoke-WebRequest "http://localhost:$FrontendPort" -UseBasicParsing -TimeoutSec 2
    $ready = $true
    break
  } catch { }
}
if (-not $ready) { throw 'Application startup failed. Check backend/server-error.log and frontend/server-error.log.' }
Write-Host "chatAI is ready at http://localhost:$FrontendPort using local model $Model."
