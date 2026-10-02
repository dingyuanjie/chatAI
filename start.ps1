Param(
  [int]$FrontendPort = 5173,
  [int]$BackendPort = 8000,
  [switch]$Install,
  [string]$BaseUrl = 'http://127.0.0.1:11434/v1',
  [string]$Model = 'chatai-local',
  [string]$ResearchModel = 'qwen3:4b-instruct-2507-q4_K_M'
)
$ErrorActionPreference = 'Stop'
$backend = Join-Path $PSScriptRoot 'backend'
$frontend = Join-Path $PSScriptRoot 'frontend'
$env:OLLAMA_BASE_URL = $BaseUrl
$env:LOCAL_MODEL = $Model
$env:RESEARCH_MODEL = $ResearchModel
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
if ($ResearchModel -notin $models.data.id -and "${ResearchModel}:latest" -notin $models.data.id) {
  throw "Research model $ResearchModel is missing. Run .\setup-local-model.ps1 to download it, then restart."
}
$embeddingModel = 'qwen3-embedding:0.6b'
$embeddingAvailable = $false
try {
  $localModels = Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5
  $embeddingAvailable = $embeddingModel -in $localModels.models.name
} catch { }
if (-not $embeddingAvailable) {
  throw "The RAG embedding model is missing. Run .\setup-local-model.ps1 to download $embeddingModel, then restart."
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
$frontendNodeModules = Join-Path $frontend 'node_modules'
$managedRoots = @{}
$unmanagedListeners = @()

foreach ($service in @(
  @{ Name = 'backend'; Port = $BackendPort },
  @{ Name = 'frontend'; Port = $FrontendPort }
)) {
  $listenerIds = Get-NetTCPConnection -LocalPort $service.Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($listenerId in $listenerIds) {
    $chain = @()
    $seen = @{}
    $currentId = [int]$listenerId
    while ($currentId -gt 0 -and -not $seen.ContainsKey($currentId)) {
      $seen[$currentId] = $true
      $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$currentId" -ErrorAction SilentlyContinue
      if (-not $processInfo) { break }
      $chain += $processInfo
      $currentId = [int]$processInfo.ParentProcessId
    }

    $root = $null
    if ($service.Name -eq 'backend') {
      $root = $chain | Where-Object {
        $_.ExecutablePath -and ([IO.Path]::GetFullPath($_.ExecutablePath) -ieq [IO.Path]::GetFullPath($pythonExe)) -and
        $_.CommandLine -match '(?i)-m\s+uvicorn\s+app\.main:app\b' -and
        $_.CommandLine -match "(?i)--port\s+$($service.Port)(\s|$)"
      } | Select-Object -First 1
    } else {
      $normalizedNodeModules = [IO.Path]::GetFullPath($frontendNodeModules).Replace('/', '\')
      $listener = $chain | Select-Object -First 1
      $listenerCommand = if ($listener) { ([string]$listener.CommandLine).Replace('/', '\') } else { '' }
      $isProjectVite = $listenerCommand -like "*$normalizedNodeModules*" -and
        $listenerCommand -match '(?i)vite[\\/]bin[\\/]vite\.js' -and
        $listenerCommand -match "(?i)--port\s+$($service.Port)(\s|$)"
      if ($isProjectVite) {
        $root = $chain | Where-Object {
          $_.CommandLine -match '(?i)npm(?:-cli\.js)?["'']?\s+run\s+dev' -and
          $_.CommandLine -match "(?i)--port\s+$($service.Port)(\s|$)"
        } | Select-Object -First 1
        if (-not $root) { $root = $listener }
      }
    }

    if ($root) {
      $rootId = [string]$root.ProcessId
      $managedRoots[$rootId] = $service
    }
    else { $unmanagedListeners += "port $($service.Port) (PID $listenerId)" }
  }
}

if ($unmanagedListeners.Count -gt 0) {
  throw "Cannot safely restart the app because these ports are used by another service: $($unmanagedListeners -join ', '). Stop it or choose another port."
}

foreach ($processId in $managedRoots.Keys) {
  Write-Host "Stopping existing chatAI $($managedRoots[$processId].Name) (PID $processId)..."
  & taskkill.exe /PID ([int]$processId) /T /F | Out-Null
  if ($LASTEXITCODE -ne 0 -and (Get-Process -Id ([int]$processId) -ErrorAction SilentlyContinue)) {
    throw "Could not stop the existing chatAI process (PID $processId)."
  }
}

foreach ($port in @($BackendPort, $FrontendPort)) {
  $released = $false
  for ($attempt = 0; $attempt -lt 15; $attempt++) {
    if (-not (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
      $released = $true
      break
    }
    Start-Sleep -Seconds 1
  }
  if (-not $released) { throw "Port $port is still in use after stopping the existing chatAI service." }
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
