# fix-prompt —— 把测试反馈表里的未通过项，变成 coding agent 能直接执行的整改提示词

## 它解决什么问题

多人协作的项目里，测试反馈通常登记在一张在线表格上：哪条功能点没通过、谁负责、
整改了没有。到了整改阶段，实际动作是：

1. 人工在表里按「负责人 + 测试结论不通过 + 整改未打钩」筛一遍；
2. 把筛出来的条目按功能模块分组；
3. 逐条抄进提示词，喂给 AI 编码助手；
4. 改完回表里打钩，循环下一组。

第 1～3 步纯手工，条目一多就出错：**漏抄、抄错归属、模块混在一起、改了哪几条说不清**。

`fix-prompt` 把这三步自动化成一句话：

```
/fix-prompt <功能子模块名>
```

→ 直接返回**一段**自包含的提示词文本，复制粘贴给 coding agent 即可。

## 核心思路（不是"大而全的批量生成器"）

刻意做成**按需、单模块、返回文本**，而不是一次性生成全部提示词：

- **一次只处理一个功能子模块** —— 和真实工作节奏一致（一个模块改完验收打钩，再下一个）；
- **只返回文本，不写目录** —— skill 目录保持干净、可整包分享，不产生一堆中间产物；
- **人始终在环里** —— 复制、粘贴、验收、打钩都是人做，AI 不碰测试反馈数据。表格只读，不自动写回。

## 5 分钟上手（无需授权、无需联网）

包内自带演示表格，先跑通再接入自己的表：

```bash
cd fix-prompt

python3 build_prompt.py --list --data examples/demo_table.csv
#   可选功能子模块（共 2 个）—— 名称 | 功能点数量：
#      3 | 库存管理-库存盘点
#      2 | 订单管理-订单查询

python3 build_prompt.py --submodule "订单管理-订单查询" --data examples/demo_table.csv
#   → 一段可直接复制给 coding agent 的整改提示词
```

完整输出示例见 [`examples/sample_output.md`](examples/sample_output.md)。

## 接到自己的表格上

1. 把整个 `fix-prompt/` 目录拷到你的 agent skill 目录下；
2. 编辑 `config.json`，填三个东西：
   - `doc.file_id` / `doc.sheet_id` —— 你的在线表格标识；
   - `filter` —— 筛选口径（负责人 / 测试结论 / 整改情况，**任意字段留空即不参与筛选**）；
   - `columns` —— 逻辑列名 → 你表里的表头引用；
3. 完成授权：`python3 tencentdocs.py tdoc_init`，看到 `READY` 即可；
4. `python3 build_prompt.py --submodule "<你的子模块名>"`。

详细步骤（含表头引用怎么写、非宿主机怎么接、常见问题）见
[`references/import.md`](references/import.md)。

## 目录说明

| 路径 | 说明 |
|---|---|
| `SKILL.md` | 给 agent 读的调用说明（触发词、交互约定、适配要点） |
| `config.json` | 唯一需要改的文件：文档标识 / 筛选口径 / 列映射 |
| `build_prompt.py` | 主入口：`--list` 列子模块，`--submodule` 出提示词 |
| `tdoc_datasource.py` | 数据层：在线表格取数 + TTL 缓存（唯一数据源出口） |
| `tencentdocs.py` | MCP 调用入口，纯标准库、跨平台、凭据不落盘 |
| `gen_filtered.py` | 可选：导出离线快照 CSV 供审计 |
| `references/auth.md` | 授权与多 agent 共用方案、错误码对照 |
| `references/prompt_template.md` | 提示词模板与设计要点（改措辞看这里） |
| `references/import.md` | 安装与适配指南 + FAQ |
| `examples/` | 演示表格与两种风格的输出示例 |

## 几个值得一提的工程细节

- **两行表头动态解析列号**：表里插列、删列都不会导致取错列；
  重名列（多个组下都叫「备注」这种）用 `大组标题/列名` 精确定位，列名写错直接报错并列出可用列。
- **合并单元格前向填充先于筛选**：功能子模块、功能描述通常是合并单元格（只有组首行有值）。
  实现上先对全量行做组内前向填充、再筛选 —— 否则组首行被筛掉后，
  它下面那一批空行会丢失模块归属。这个顺序反了就会静默出错。
- **透明缓存 + 断网兜底**：取数结果按 `(file_id, sheet_id)` 缓存，TTL 默认 300s，
  `--refresh` 强制重取；实时取数失败但有旧缓存时回退并打 `WARN`，不静默失败。
- **降级友好**：表格里没有某列时，把 `config.columns` 对应项置空即可，
  该列既不参与筛选也不出现在提示词里 —— 不需要改代码。
- **凭据零落盘**：token 只在 HTTP 请求头里内存透传，不写磁盘、不进日志。

## 安全

- 表格**只读**，整改状态由人工回表打钩，本工具不自动写回测试反馈数据；
- 凭据文件（`tdoc_token.json` 等）必须放在包外或包内 `.secrets/`（已 gitignore），
  **分享目录包前先确认包内没有凭据**；
- `.cache/`、`data/*.csv`、`__pycache__/` 均为生成物，已 gitignore。

## 运行环境

Python 3.8+，**仅用标准库**（`urllib` / `csv` / `json`），无需 `pip install`，Windows / macOS / Linux 均可。

## 许可

[MIT](LICENSE) —— 可自由使用、修改、分发，保留版权声明即可。
