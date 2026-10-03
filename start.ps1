# 项目本地启动/重启入口。脚本会先完成依赖和模型预检，再只重启能够确认属于
# 当前项目的前后端进程；如果端口被其他程序占用，会停止并提示，避免误杀用户进程。
#
# 参数说明：FrontendPort / BackendPort 用于本地开发服务端口；Install 强制安装依赖；
# BaseUrl、Model 和 ResearchModel 用于本地 Ollama 连接与模型选择。全局设置页保存的
# 运行时模型配置仍由后端读取，本脚本参数主要用于模型预检和本地首次配置。
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
# 依赖预检阶段：目录存在不等于包齐全。后端通过实际导入和 pip check 两道检查，
# 前端通过 npm 的直接依赖树检查；失败时依据锁定文件/requirements 自动补齐后复查。
$pythonExe = Join-Path $backend '.venv/Scripts/python.exe'
if (!(Test-Path $pythonExe)) {
  Write-Host 'Creating the backend Python environment...'
  if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv (Join-Path $backend '.venv') }
  else { & python -m venv (Join-Path $backend '.venv') }
  if ($LASTEXITCODE -ne 0 -or !(Test-Path $pythonExe)) { throw 'Could not create backend/.venv. Install Python 3.11 or 3.12 and retry.' }
  $Install = $true
}
& $pythonExe -c 'import sys; print(sys.version)' 1>$null 2>$null
if ($LASTEXITCODE -ne 0) { throw 'Broken backend/.venv. Install Python 3.12, rename the old .venv, then rerun with -Install.' }
$backendImportCheck = 'import fastapi, uvicorn, pydantic, langchain, langchain_core, langchain_community, langchain_openai, dotenv, sqlalchemy, mcp, sse_starlette, httpx, multipart'
& $pythonExe -c $backendImportCheck 1>$null 2>$null
$backendImportExit = $LASTEXITCODE
& $pythonExe -m pip check 1>$null 2>$null
$backendPipCheckExit = $LASTEXITCODE
if ($Install -or $backendImportExit -ne 0 -or $backendPipCheckExit -ne 0) {
  Write-Host 'Installing or repairing backend dependencies from requirements.txt...'
  & $pythonExe -m pip install -r (Join-Path $backend 'requirements.txt')
  if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }
}
& $pythonExe -c $backendImportCheck 1>$null 2>$null
if ($LASTEXITCODE -ne 0) { throw 'Backend dependencies are still incomplete after installation. Check backend/requirements.txt and the pip output.' }
& $pythonExe -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Backend dependency versions are inconsistent after installation.' }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'Node.js/npm is not installed. Install the current Node.js LTS release, then rerun this script.' }
Push-Location $frontend
# 模型预检阶段：确认 OpenAI-compatible 接口、本地对话/科研模型以及向量模型已就绪。
# 本地 Ollama 默认地址不可达时，委托 setup-local-model.ps1 安装或启动 Ollama。
try {
  $frontendDependenciesReady = $false
  if (!$Install -and (Test-Path 'node_modules')) {
    & npm.cmd ls --depth=0 1>$null 2>$null
    $frontendDependenciesReady = $LASTEXITCODE -eq 0
  }
  if ($Install -or !$frontendDependenciesReady) {
    Write-Host 'Installing or repairing frontend dependencies from package-lock.json...'
    & npm.cmd install
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
  }
  & npm.cmd ls --depth=0 1>$null 2>$null
  if ($LASTEXITCODE -ne 0) { throw 'Frontend dependencies are still incomplete after installation. Check frontend/package.json and package-lock.json.' }
} finally { Pop-Location }
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
$frontendNodeModules = Join-Path $frontend 'node_modules'
# 端口安全检查：向上遍历监听进程的父进程链，验证可执行文件、项目路径和启动参数，
# 只把可识别的 chatAI 服务放入待重启列表；任一端口由外部程序占用时整体中止。
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

# 先结束确认属于本项目的进程树，再等待两个端口真正释放，避免新旧后端并存或误连旧前端。
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
# 分别以隐藏窗口启动 API 与 Vite 开发服务器；标准输出/错误写入各自日志文件，方便排错。
Start-Process -WindowStyle Hidden -WorkingDirectory $backend -FilePath $pythonExe -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port',"$BackendPort" -RedirectStandardOutput (Join-Path $backend 'server.log') -RedirectStandardError (Join-Path $backend 'server-error.log')
Start-Process -WindowStyle Hidden -WorkingDirectory $frontend -FilePath 'powershell.exe' -ArgumentList '-NoProfile','-Command',"npm run dev -- --port $FrontendPort" -RedirectStandardOutput (Join-Path $frontend 'server.log') -RedirectStandardError (Join-Path $frontend 'server-error.log')
# 启动就绪检查同时访问 API 文档和前端页面；只有两端都能响应才向用户报告成功。
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
Write-Host "chatAI is ready at http://localhost:$FrontendPort using the configured global model settings."
