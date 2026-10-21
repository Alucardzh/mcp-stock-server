"""服务器元工具：工具清单枚举、akproxy 额度查询。"""

import json
import os
import urllib.request

from mcp_app import mcp


@mcp.tool()
def get_akproxy_token_info() -> str:
    """查询 akshare-proxy 服务的 积分剩余额度。"""
    token = os.getenv("AKPROXY_TOKEN", "")
    if not token:
        return "AKPROXY_TOKEN 未配置"
    url = f"http://101.201.173.125:47001/api/token/{token}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.read().decode("utf-8")
    except Exception as e:  # noqa: BLE001
        return f"查询失败: {e}"


@mcp.tool()
async def list_tools_tool() -> str:
    """枚举本服务器提供的全部 MCP 工具清单（名称/用途/参数）。

    用于排查"有哪些工具可用"及快速检索工具名，无需翻源码或客户端文档。

    Returns:
        str: JSON，含 count 与 tools 数组，每个工具含
            name / description / parameters(name/type/required/default)
    """
    tools = await mcp.get_tools()
    items = []
    for name in sorted(tools):
        tool = tools[name]
        schema = tool.parameters or {}
        required = set(schema.get("required", []))
        params = [
            {
                "name": pname,
                "type": pschema.get("type", ""),
                "required": pname in required,
                "default": pschema.get("default"),
            }
            for pname, pschema in (schema.get("properties") or {}).items()
        ]
        items.append(
            {
                "name": tool.name,
                "description": (tool.description or "").strip(),
                "parameters": params,
            }
        )
    return json.dumps(
        {"success": True, "count": len(items), "tools": items},
        ensure_ascii=False,
        indent=2,
    )
