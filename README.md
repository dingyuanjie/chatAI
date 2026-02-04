# chatAI

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
