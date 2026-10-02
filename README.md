# chatAI

## 账号与新界面

首次访问页面时，使用邮箱和至少 8 位密码注册；后续可登录、切换和删除个人会话。账号、登录会话和聊天历史保存在本机 SQLite 数据库中，密码以 PBKDF2 哈希形式保存，登录凭据通过 HttpOnly Cookie 管理。每个账号拥有独立的会话和 Markdown 知识库。界面支持中文/English、亮色/暗色主题，并适配手机屏幕；显示偏好保存在当前浏览器中。

## 本地模型（RTX 5060 8GB）

默认使用 Ollama 在本机运行 Qwen3 4B 量化模型，不再调用阿里云 API。
模型下载约 2.5GB，实际显存还包括运行缓存；项目将上下文和单次输出上限都设为 4096 tokens。聊天记忆、流式回复和 RAG 接口保留。

1. 准备脚本会通过 winget 自动安装 Ollama 并启动服务；没有 winget 时手动安装 [Ollama Windows](https://ollama.com/download/windows)。首次安装和模型下载需要数 GB 空间。
2. 安装 Python 3.12 和 Node.js。若已有 `backend/.venv` 但其 Python 已卸载，先把该目录重命名为 `.venv.old`，让启动脚本创建新环境。
3. 在项目根目录运行：

```powershell
.\setup-local-model.ps1
.\start.ps1 -Install
```

后续启动运行 `.\start.ps1`，访问 http://localhost:5173。
首次准备模型需要联网下载，下载后本地聊天无需云端 API 密钥。

`backend/Modelfile` 定义模型与上下文限制；修改后重新运行准备脚本。
`backend/.env.example` 给出直接运行后端时的配置，复制为 `.env` 即可使用。
旧 `OPENAI_*` 配置不再参与模型选择，现有密钥保留在本地文件中。
启动脚本通过 `-Model` 和 `-BaseUrl` 覆盖本地模型及服务地址。
运行 `ollama ps` 可查看 GPU 加载情况；实际性能以本机运行结果为准。
模型不可用时普通聊天返回 HTTP 503，流式聊天返回 error 事件，不再生成示例回复。

## 本地 Markdown 向量知识库

点击聊天页右上方的“知识库”上传、预览或删除 Markdown 文件。支持 UTF-8 `.md` 文件，单个最大 2 MB；同名文件重新上传会更新内容。文件、切分片段和向量保存在本机 `backend/data/rag.sqlite`。

本地嵌入使用阿里巴巴开源 Qwen3-Embedding 0.6B（约 639 MB），通过 Ollama `/api/embed` 批量生成向量。启动前运行 `.\setup-local-model.ps1` 下载嵌入模型；上传后聊天会用查询指令向量检索相关片段，回答中引用文件名。没有相关资料时，助手会说明知识库里没找到。

参考：[Qwen3-Embedding 模型及大小](https://ollama.com/library/qwen3-embedding)、[Ollama Embeddings API](https://docs.ollama.com/api/embed)。

参考：[模型大小](https://ollama.com/library/qwen3:4b)、[Ollama 兼容接口与上下文设置](https://docs.ollama.com/api/openai-compatibility)。
## MCP Server (Weather Service)

本项目包含一个 Model Context Protocol (MCP) 服务器，提供天气查询功能。

## MCP Server (Windows Shell)

本项目还包含一个支持在 Windows 系统上执行 Shell 命令的 MCP 服务器。

> **⚠️ 警告**: 此服务器允许执行任意系统命令。请仅在受信任的环境中使用，并小心操作。

### 功能

- **run_command**: 执行 Windows 命令行指令 (cmd.exe 环境)。
  - 参数: `command` (命令字符串), `cwd` (可选工作目录)

### 安装依赖

两个 MCP 服务器共用相同的依赖：

```bash
cd backend
pip install -r requirements.txt
```

### 配置指南 (Claude Desktop)

你可以同时配置多个 MCP 服务器。请将以下内容合并到你的配置文件中：

```json
{
  "mcpServers": {
    "weather-service": {
      "command": "python",
      "args": ["ABSOLUTE_PATH_TO_YOUR_PROJECT/backend/mcp_server.py"]
    },
    "windows-shell": {
      "command": "python",
      "args": ["ABSOLUTE_PATH_TO_YOUR_PROJECT/backend/mcp_shell.py"]
    }
  }
}
```

请将 `ABSOLUTE_PATH_TO_YOUR_PROJECT` 替换为项目的实际绝对路径。
例如：`D:\\newData\\chatAI\\backend\\mcp_shell.py`

### 调试

调试 Shell 服务器：

```bash
python backend/mcp_shell.py
```
