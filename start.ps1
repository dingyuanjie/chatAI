Param(
  [int]$FrontendPort = 5173,
  [int]$BackendPort = 8000,
  [switch]$Install,
  [string]$ApiKey,
  [string]$BaseUrl,
  [string]$Model
)

function Write-Info($msg) { Write-Host "[INFO] $msg" -ForegroundColor Cyan }
function Write-Warn($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "[ERR ] $msg" -ForegroundColor Red }

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"

function Load-Env($envPath) {
  if (Test-Path $envPath) {
    Write-Info "Loading environment from $envPath"
    Get-Content $envPath | ForEach-Object {
      if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
        $name = $matches[1]
        $val = $matches[2].Trim()
        $val = $val.Trim('"').Trim("'")
        [System.Environment]::SetEnvironmentVariable($name, $val)
      }
    }
  } else {
    Write-Warn "Env file not found: $envPath (skip)"
  }
}

function Load-EnvFallback {
  $names = @("OPENAI_API_KEY","OPENAI_API_BASE","OPENAI_MODEL")
  foreach ($n in $names) {
    $vUser    = [System.Environment]::GetEnvironmentVariable($n, "User")
    $vMachine = [System.Environment]::GetEnvironmentVariable($n, "Machine")
    $vProc    = [System.Environment]::GetEnvironmentVariable($n, "Process")
    $v = $vProc
    if (-not $v) { $v = $vUser }
    if (-not $v) { $v = $vMachine }
    if ($v) {
      [System.Environment]::SetEnvironmentVariable($n, $v)
      Write-Info "Loaded $n from system environment"
    }
  }
}

function Override-EnvFromArgs {
  if ($ApiKey) { [System.Environment]::SetEnvironmentVariable("OPENAI_API_KEY", $ApiKey); Write-Info "OPENAI_API_KEY set from args" }
  if ($BaseUrl) { [System.Environment]::SetEnvironmentVariable("OPENAI_API_BASE", $BaseUrl); Write-Info "OPENAI_API_BASE set from args" }
  if ($Model) { [System.Environment]::SetEnvironmentVariable("OPENAI_MODEL", $Model); Write-Info "OPENAI_MODEL set from args" }
}

function Ensure-Backend {
  Set-Location $backend
  if (!(Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Info "Creating Python venv"
    python -m venv .venv
  }
  if ($Install) {
    Write-Info "Installing backend dependencies"
    .\.venv\Scripts\python -m pip install --upgrade pip
    .\.venv\Scripts\python -m pip install -r requirements.txt
  }
}

function Ensure-Frontend {
  Set-Location $frontend
  if (!(Test-Path ".\node_modules")) {
    Write-Info "Installing frontend dependencies"
    npm install
  } elseif ($Install) {
    Write-Info "Refreshing frontend dependencies per --Install"
    npm install
  }
}

function Free-Port($port) {
  try {
    $pid = (Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue | Where-Object { $_.State -eq 'Listen' } | Select-Object -First 1 -ExpandProperty OwningProcess)
    if ($pid) {
      Write-Warn "Port $port occupied by PID $pid. Killing..."
      Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
      Start-Sleep -Milliseconds 500
    }
  } catch {
    $line = (netstat -ano | Select-String (":$port") | Select-Object -First 1)
    if ($line) {
      $parts = $line.ToString().Trim().Split()
      $pidNum = $parts[$parts.Length-1]
      Write-Warn "Port $port occupied by PID $pidNum (netstat). Killing..."
      try {
        $pidInt = [int]$pidNum
      } catch {
        $pidInt = [int]([string]$pidNum).Trim()
      }
      Stop-Process -Id $pidInt -Force -ErrorAction SilentlyContinue
      Start-Sleep -Milliseconds 500
    }
  }
}

# Load environment from backend .env if present
Load-Env (Join-Path $backend ".env")
Load-EnvFallback
Override-EnvFromArgs

$key = [System.Environment]::GetEnvironmentVariable("OPENAI_API_KEY")
$base = [System.Environment]::GetEnvironmentVariable("OPENAI_API_BASE")
$model = [System.Environment]::GetEnvironmentVariable("OPENAI_MODEL")
if (-not $key) {
  Write-Warn 'OPENAI_API_KEY not set. Backend will use local fallback. Set via backend/.env, system env, or -ApiKey.'
}

# Start backend
Ensure-Backend
Write-Info "Starting backend on port $BackendPort"
Free-Port $BackendPort
Start-Process -WindowStyle Normal -WorkingDirectory $backend powershell -ArgumentList "-NoExit","-Command",".\\.venv\\Scripts\\python -m uvicorn app.main:app --host 0.0.0.0 --port $BackendPort"

# Start frontend
Ensure-Frontend
Write-Info "Starting frontend on port $FrontendPort"
Start-Process -WindowStyle Normal -WorkingDirectory $frontend powershell -ArgumentList "-NoExit","-Command","npm run dev -- --port $FrontendPort"

# End
