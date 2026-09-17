---
name: fix-prompt
description: >-
  面向「批量修复缺陷 / 整改质检反馈」场景的统一 skill：从在线电子表格（实时取数，唯一数据源）中
  筛选「指定负责人 + 测试结论不通过 + 整改未打钩」的功能点，按功能子模块归组，
  为 coding agent 构造结构化整改提示词。★ 核心交互：用斜杠命令 `fix-prompt` + 功能子模块名触发，
  **只返回一段针对该子模块、可直接复制给 coding agent 的提示词文本**；不自动批量生成、不写入执行目录。
  用户复制文本 → 粘贴给 coding agent 编码 → 验收后回表格打钩 → 循环下一个子模块。
  触发词：fix-prompt、整改提示词、功能子模块、测试反馈、缺陷清单、coding agent 提示词、在线表格整改。
  数据源通过内置共享数据接口（tdoc_datasource）实时读取，带 TTL 缓存可透明刷新，不依赖本地 CSV 副本。
  文档标识 / 筛选口径 / 列映射全部由 config.json 驱动，改配置即可适配别的表格。
---

# 整改提示词 Skill（fix-prompt）

> **一句话**：把「质检表里没通过、也没整改的功能点」，按**功能子模块**自动归组，
> 按需生成**一段结构化提示词**，你复制粘贴给 coding agent 去修。

> **核心交互**：
> 1. 斜杠命令 `fix-prompt` + 功能子模块名触发 → skill 只返回**一段**可复制的提示词文本；
> 2. 用户复制该文本 → 粘贴给 coding agent 执行编码；
> 3. 编码完成后用户验收、截图，并回在线表格把该子模块的「整改情况」改为已整改；
> 4. 循环下一个子模块。
>
> **不自动批量生成、不写入执行目录**——每次触发只针对单个功能子模块，
> 保持 skill 作为可分享目录包的独立性。

---

## 1. 目录结构（自包含、可分享目录包）

```
fix-prompt/
├── SKILL.md                 # 本文件：调用说明 + 工作流 + 适配要点（供 agent 阅读）
├── README.md                # 给人看的总引导：是什么、解决什么、怎么上手
├── config.json              # 可配置项：文档标识 / 筛选口径 / 列映射
├── tencentdocs.py           # 数据层：在线文档 MCP 调用入口（凭据内存透传，不落盘）
├── tdoc_datasource.py       # 数据层：共享数据接口 get_sheet_rows() + TTL 缓存（唯一数据源）
├── build_prompt.py          # 提示词构造入口（--submodule 单模块文本 / --list）
├── gen_filtered.py          # （可选）从同一数据源导出离线快照 data/filtered.csv
├── references/
│   ├── auth.md              # 授权说明（票据加载链 / 多 agent 共用 / 撤销 / 错误码）
│   ├── prompt_template.md   # 单模块提示词模板与设计要点
│   └── import.md            # 安装与适配指南（含 5 分钟离线试跑、适配自己表格的步骤、FAQ）
├── examples/
│   ├── demo_table.csv       # 演示表格（两行表头 + 全列布局），无需授权即可试跑
│   └── sample_output.md     # 两种风格的提示词输出示例
├── .cache/                  # 取数缓存（自动刷新，非手工副本；已 gitignore）
├── .secrets/                # 约定放共享凭据文件的位置（已 gitignore，绝不进包分享）
└── data/                    # 可选离线快照 filtered.csv（非 build_prompt 必需；已 gitignore）
```

---

## 2. 调用方式（按需、单模块、返回文本）

### 2.1 先判断能不能取数

```bash
python3 tencentdocs.py tdoc_init
#   READY          → 票据已注入，可继续
#   ERROR:no_token → 未授权，见 references/auth.md
```

### 2.2 标准交互（★ 用户实际流程）

```bash
# 不确定子模块名字时，先列出可选项
python3 build_prompt.py --list

# 触发单个子模块：输出一段可直接复制的提示词文本（打印到 stdout）
python3 build_prompt.py --submodule "订单管理-订单查询"

# 源表刚更新过，强制刷新缓存再生成
python3 build_prompt.py --submodule "订单管理-订单查询" --refresh

# 离线兜底：用本地「两行表头 + 全列布局」CSV 输出（不联网、不需授权）
python3 build_prompt.py --submodule "订单管理-订单查询" --data examples/demo_table.csv
```

> 在 OpenCode 等支持斜杠命令的客户端里：`/fix-prompt 订单管理-订单查询`，
> agent 运行上面的 `--submodule`，并把返回的提示词文本**原样**交给你复制。
>
> ⚠️ **agent 拿到的就是最终交付物**：不要改写、不要精简、不要「顺便解释一下」，
> 直接把 stdout 原文贴给用户。

### 2.3 输出形态

`# 功能子模块整改提示词` + `## 功能子模块` + （可选）`## 功能子模块简介` +
`## 功能点清单` + `## 你的工作`。
这段文本自包含、**不含任何授权信息**，复制后直接粘贴给 coding agent 即可。
两种风格（逐条描述 / 整组共享描述）的实例见 `examples/sample_output.md`。

---

## 3. 授权（简述；细节见 references/auth.md）

- **票据来源优先级**：环境变量 `TDOC_OAUTH_ACCESS_TOKEN` / `TDOC_ONEID_ACCESS_TOKEN`
  → 共享 token 文件 `TDOC_SHARED_TOKEN_FILE`（`tdoc_datasource` 会自动探测包内 `.secrets/tdoc_token.json`）
  → 宿主 token provider（`CODEBUDDY_MCP_CONFIG`）。
- **凭据不落盘、不进包**：token 只在请求头里内存透传。共享 token 文件必须放包外或包内 `.secrets/`。
- **不要凭记忆拼参数**：调用 MCP 工具前先用
  `python3 tencentdocs.py tdoc_schema <service> <tool>` 拿真实参数定义。

---

## 4. 用户实际工作流（循环）

| 步骤 | 动作 | 负责方 |
|---|---|---|
| 1 | 斜杠命令 `fix-prompt` + 功能子模块名 → 拿到一段提示词文本 | agent（本 skill） |
| 2 | 复制提示词文本，粘贴给 coding agent 执行编码 | 你 |
| 3 | coding agent 先给改造方案、编码、逐条修复测试反馈、自测 | coding agent |
| 4 | 你验收 + 截图，回在线表格把该子模块的「整改情况」改为已整改 | 你 |
| 5 | 循环下一个子模块（回到步骤 1） | 你 |
| 6 | 分享给同事：把整个 `fix-prompt/` 目录包给对方（先确认包内无凭据） | 你 |

> 本 skill **不在步骤 1 自动批量生成全部提示词、也不写 `prompts/` 目录、不驱动 agent 读目录执行**——
> 一切按单模块、按需、手动复制的节奏进行。

---

## 5. 数据源与联动

- **唯一数据源 = 在线表格**：`build_prompt.py` 通过内置 `tdoc_datasource.get_sheet_rows()` 实时取数，
  **不把远程表格复制/导出为本地 CSV 再读取**。
- **共享数据接口**：`tdoc_datasource` 是数据层唯一出口，缓存按 `(file_id, sheet_id)` 落 `.cache/`，
  默认 TTL 300s；过期或 `--refresh` 自动重取，**不靠手工导出**。
- **断网兜底**：实时取数失败且本地有旧缓存时，回退旧缓存并打 `WARN`，不静默失败。
- **可选离线快照**：`gen_filtered.py` 可从同一数据源导出 `data/filtered.csv` 供审计/留痕；
  但 `build_prompt.py` 不依赖它，离线运行请用 `--data` 指向一份**两行表头 + 全列布局**的 CSV。
- **数据一致性**：源表更新后，重跑 `--submodule <名称> --refresh` 即反映最新。

---

## 6. 配置（config.json，部分字段可被环境变量覆盖）

| config.json 字段 | 含义 | 环境变量覆盖 |
|---|---|---|
| `doc.file_id` / `doc.sheet_id` | 目标在线表标识 | `FIXPROMPT_DOC_FILE_ID` / `FIXPROMPT_DOC_SHEET_ID` |
| `filter.developer` | 负责人筛选值（留空 = 不筛） | — |
| `filter.test_conclusion_contains` | 测试结论包含串（留空 = 不筛） | — |
| `filter.fix_status` | 整改情况值，如 `FALSE`（留空 = 不筛） | — |
| `columns` | 逻辑列名 → 表头引用（裸列名 或「大组标题/列名」）；值为空串表示该列不启用 | — |

**逻辑列名是代码里固定的**（`build_prompt.py` 的 `LOGICAL_COLUMNS`），
只有 `功能子模块` 必需（归组依据），其余可置空降级。
运行时按**两行表头**动态解析为实际列索引，**表头插列 / 删列不会错位**；
表头引用找不到或裸名重名时会直接报错并列出可用列。

修改 `config.json` 即可适配不同文档 / 不同筛选口径，无需改代码。

---

## 7. 注意事项

- **匹配规则**：子模块名精确 → 归一化（`>`/`＞` → `-`、去空格、忽略大小写）→ 双向包含模糊匹配；
  命中多个候选时会报错并列出候选，请补全层级后精确指定。
- **合并单元格前向填充**：`功能子模块` / `功能描述` 通常是合并单元格（仅组首行有值）。
  实现上必须**先按原始表序对全量行做组内前向填充、再筛选**——否则中间未命中的组首行被筛掉后，
  其后续空行会丢失子模块归属或功能描述。这是本 skill 最容易踩的坑，改动时别破坏这个顺序。
- **提示词面向全栈 coding agent**，要求「先给改造方案、再编码」，逐条对照测试反馈修复。
  正文文案集中在 `build_prompt.py` 的 `render_prompt()` 一处，要改措辞改那里。
- 缓存 TTL 用环境变量 `TDOC_CACHE_TTL` 调整；调试取数加 `TDOC_DEBUG=1`。
- 本 skill 默认只输出文本、不生成文件；如需落盘存档，自行把终端输出重定向到文件即可。
