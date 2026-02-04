# chatAI

## MCP Server (Weather Service)

本项目包含一个 Model Context Protocol (MCP) 服务器，提供天气查询功能。

### 功能

- **get_weather**: 查询全球任意城市的天气情况（包括温度、湿度、风速）。
  - 数据源：Open-Meteo (无需 API Key)

### 安装依赖

在使用之前，请确保安装了必要的 Python 依赖：

```bash
cd backend
pip install -r requirements.txt
```

### 配置指南 (Claude Desktop)

要将此 MCP 服务器连接到 Claude Desktop，请按照以下步骤操作：

1. 打开 Claude Desktop 配置文件：
   - Windows: `%APPDATA%\Claude\claude_desktop_config.json`
   - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

2. 添加 `weather-service` 配置：

```json
{
  "mcpServers": {
    "weather-service": {
      "command": "python",
      "args": ["ABSOLUTE_PATH_TO_YOUR_PROJECT/backend/mcp_server.py"]
    }
  }
}
```

请将 `ABSOLUTE_PATH_TO_YOUR_PROJECT` 替换为项目的实际绝对路径。
例如：`D:\\newData\\chatAI\\backend\\mcp_server.py`

### 调试

你可以通过以下命令检查服务器是否能正常启动（无报错即正常，因为它等待 stdio 输入）：

```bash
python backend/mcp_server.py
```
