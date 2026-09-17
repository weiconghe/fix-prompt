# 在线文档授权 —— 鉴权技术参考

> 配套 `SKILL.md` 第 3 节。本文件描述 `tencentdocs.py` 实际的票据加载链，以及多 agent 共用同一份授权时的落地模式。

## 1. 票据加载链（`tencentdocs._load_tokens` 优先级）

每次工具调用都重新执行 `_load_tokens()`，按以下顺序取 token：

1. **环境变量（最高优先级）**
   - `TDOC_OAUTH_ACCESS_TOKEN` → C 端 OAuth token，请求头 `Authorization: Bearer <token>`
   - `TDOC_ONEID_ACCESS_TOKEN` → SaaS 端 OneID token，请求头 `X-Oneid-Access-Token: <token>`
   - 二者可同时非空（双票），由服务端决定用哪个。

2. **共享 token 文件（可选兜底）**
   - 当环境变量未注入时，读 `TDOC_SHARED_TOKEN_FILE` 指向的 JSON：`{"oauth": "...", "oneid": "..."}`。
   - `tdoc_datasource` 在环境变量缺失时会自动探测包内约定位置 `./.secrets/tdoc_token.json`，
     让 agent 进程无需额外 `export` 即可取票。
   - 要求文件存在且可读；任何解析异常被吞掉并走下一步。
   - 安全约定：该文件须 `0600`、仅属主可读，且**不**进入版本控制 / 共享盘（已在 `.gitignore` 排除）。

3. **宿主 token provider（WorkBuddy 等宿主环境）**
   - 读环境变量 `CODEBUDDY_MCP_CONFIG`，取 `mcpServers.connector-proxy` 的 `url` + `Authorization`。
   - 向 `<proxy_url 去掉 /mcp>/internal/tencent-docs/tokens` 发 GET，取 `personal.token` / `enterprise.token`。
   - 该请求固定不走代理（宿主本地）。

4. 都拿不到 → 返回 `("", "")`，上层打印 `ERROR:no_token`。

## 2. HTTP 透传细节

- `Content-Type: application/json`；`Accept: application/json, text/event-stream`。
- token 仅在本次请求的头里存在，函数返回后即被 GC，**不写磁盘、不进日志**。
- 兼容 SSE：若响应 `Content-Type` 含 `text/event-stream`，抽取最后一条 `data:` JSON 作为结果。
- 默认走系统代理（`urllib` 读 `HTTP_PROXY` / `HTTPS_PROXY`）；加 `--no-proxy` 用空 `ProxyHandler` 绕过。
- 端点可在 `tencentdocs.py` 顶部三行内切换（个人版 / 企业版），也可用环境变量 `TDOC_API_BASE_URL` 覆盖。

## 3. 多 agent 共享授权的两种落地模式

### 模式 A：宿主环境变量注入（推荐）

```
[宿主的在线文档连接器] --授权--> 拿到 access_token
        │
        └─ 为每个 agent 会话注入 TDOC_OAUTH_ACCESS_TOKEN
                    │
              [agent 进程] --tencentdocs.py--> 在线文档 MCP
```

- 隔离性：进程级。各 agent 内存中的 token 互不可见。
- 轮换：宿主更新环境变量，agent 下次调用自动用新 token。
- 撤销：宿主断开连接器 → 不再注入 → `tdoc_init` 报 `no_token`。

### 模式 B：共享 token 文件（无环境变量注入能力时）

```
[宿主 / 密钥脚本] 写 TDOC_SHARED_TOKEN_FILE (0600) {"oauth":..., "oneid":...}
        │
        ├─[agent-1] 读同一文件 ─┐
        ├─[agent-2] 读同一文件 ─┤──> tencentdocs.py --> 在线文档 MCP
        └─[agent-N] 读同一文件 ─┘
```

- 生成方式：在有授权的会话里执行
  `python3 tencentdocs.py tdoc_export_token "<路径>/tdoc_token.json"`，
  它会取 live token 并写为 `0600` 的 `{"oauth":..., "oneid":...}`。
- 该文件**必须放在 skill 包目录之外**（或包内已被 gitignore 的 `.secrets/`），且**不**进入版本控制；
  Windows/NTFS 上 `0600` 仅尽力而为，真实隔离靠文件位置与不提交。
- 隔离性：文件级。所有 agent 共享同一份 token → **无法在 token 层做 agent 间隔离**，
  隔离只能靠「用哪个账号授权」+ agent 侧 `file_id` 使用范围约定。
- 风险：文件若权限过宽会被任意本地用户读取 → 必须限制为仅属主可读。
- 撤销：删除 / 清空该文件，所有 agent 立即失去授权。

## 4. 自建 OAuth（完全脱离宿主时）

仅当 agent 运行在没有宿主注入的环境、也无法从别处导出 token 时需要：

1. 在开放平台注册应用 → 拿 `client_id` / `client_secret`。
2. 拼授权 URL 让用户登录授权，回调拿 `code`。
3. 用 `code` 换 `access_token` + `refresh_token`。
4. 仅把 `access_token` 暴露给本 skill（环境变量或隔离文件）；`client_secret` / `refresh_token` 存密钥库。
5. `access_token` 过期时用 `refresh_token` 换发，写回环境变量 / 隔离文件。

## 5. 常见错误码

| 输出 | 含义 | 处理 |
|---|---|---|
| `ERROR:no_token` | 票据加载链全部为空 | 按本文第 1～4 节逐级确认；先跑 `python3 tencentdocs.py tdoc_init` |
| `ERROR:http_failed - HTTP 401/403` | 票据无效、过期或无该文档权限 | 重新授权；确认账号确实有该表格的访问权限 |
| `ERROR:http_failed - HTTP 400` | 参数不合法（缺 `file_id` / 范围越界等） | 用 `tdoc_schema` 查参数定义，按定义传参 |
| `ERROR:non_json_response` | 返回不是预期 JSON（多为被网关 / 代理拦截） | 试 `--no-proxy`；检查代理设置 |
| 取到 0 行 | 表格为空或 `sheet_id` 不对 | 核对 `config.doc.sheet_id` |

> **调用纪律**：调用任何 MCP 工具前，先用 `python3 tencentdocs.py tdoc_schema <service> <tool>`
> 拿到该工具的真实参数定义，按定义传参，**不要凭记忆或猜测拼参数**。
