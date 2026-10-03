# 本地模型环境准备脚本：确保 Ollama 已安装并运行，再依次拉取聊天、科研、向量模型，
# 最后从仓库中的 Modelfile 创建统一的聊天模型别名。大模型下载可能占用数 GB 磁盘空间。
$ErrorActionPreference = 'Stop'
# 首先查找 PATH 中的 Ollama；若未加入 PATH，则检查 Windows 默认安装位置。
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    $installed = Join-Path $env:LOCALAPPDATA 'Programs/Ollama/ollama.exe'
    if (Test-Path $installed) { $ollama = Get-Item $installed }
    else {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            throw 'Install Ollama from https://ollama.com/download/windows, then run this script again.'
        }
        Write-Host 'Installing Ollama. The application and model require several GB of disk space.'
        & winget install --id Ollama.Ollama --exact --source winget --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
        if ($LASTEXITCODE -ne 0 -or !(Test-Path $installed)) { throw 'Ollama installation failed. Install from https://ollama.com/download/windows and retry.' }
        $ollama = Get-Item $installed
    }
}
# 统一解析 Ollama 可执行程序路径，并通过本地 tags API 判断服务是否已启动。
$ollamaExe = if ($ollama.Source) { $ollama.Source } else { $ollama.FullName }
try { $null = Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 3 }
catch {
    Write-Host 'Starting local Ollama service...'
    Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WindowStyle Hidden
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Seconds 1
        try {
            $null = Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2
            $ready = $true
            break
        } catch { }
    }
    if (-not $ready) { throw 'Ollama did not start. Check the Ollama logs and retry.' }
}
# 聊天与科研使用不同用途的模型；向量模型单独服务于 Markdown 知识库的嵌入与检索。
& $ollamaExe pull qwen3:4b
if ($LASTEXITCODE -ne 0) { throw 'Model download failed.' }
& $ollamaExe pull qwen3:4b-instruct-2507-q4_K_M
if ($LASTEXITCODE -ne 0) { throw 'Research model download failed.' }
& $ollamaExe create chatai-local -f (Join-Path $PSScriptRoot 'backend/Modelfile')
if ($LASTEXITCODE -ne 0) { throw 'Model creation failed.' }
& $ollamaExe pull qwen3-embedding:0.6b
if ($LASTEXITCODE -ne 0) { throw 'Embedding model download failed.' }
Write-Host 'Local chat and embedding models ready. Run .\start.ps1 -Install to start chatAI.'
