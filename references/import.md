# 安装与适配指南

`fix-prompt` 是一个**自包含**的 skill：数据层与提示词构造层在同一目录，
把整个目录包拷走即可使用，无需再拼装别的 skill。
使用方式是「**按需单模块、复制文本**」，不是自动批量写目录。

---

## 一、5 分钟上手（不用授权，先跑通）

包内自带一份演示表格（`examples/demo_table.csv`，两行表头、全列布局）。
不接任何授权、不联网，直接验证脚本可用：

```bash
cd fix-prompt

# 1) 看筛选结果里有哪几个功能子模块
python3 build_prompt.py --list --data examples/demo_table.csv

# 2) 针对其中一个子模块，产出可直接复制给 coding agent 的提示词
python3 build_prompt.py --submodule "订单管理-订单查询" --data examples/demo_table.csv
```

预期输出见 `examples/sample_output.md`。
**这一步通过，说明环境没问题**，剩下的只是把它接到你自己的表格上。

> `--data` 只接受「两行表头 + 全列布局」的 CSV，也就是和你在线表格一样的结构，
> 不是筛选后的结果文件。

---

## 二、接到你自己的在线表格

### 1. 放置目录

把整个 `fix-prompt/` 拷到你的 agent 的 skill 目录下，例如：

```
<你的项目>/.opencode/skills/fix-prompt/
<你的项目>/.claude/skills/fix-prompt/
```

（放哪里不影响脚本运行 —— 脚本只依赖自身目录和 `config.json`。）

### 2. 改 `config.json`

```jsonc
{
  "doc": {
    "file_id": "你的表格 file_id",     // 在线表格 URL 里那串标识
    "sheet_id": "你的工作表 sheet_id"  // 具体某个 tab 的 id
  },
  "filter": {
    "developer": "你的名字",           // 留空 = 不按负责人筛选
    "test_conclusion_contains": "不通过",
    "fix_status": "FALSE"              // 留空 = 不按整改情况筛选
  },
  "columns": {
    // 左边是逻辑列名（代码里固定，不能改），右边是你表里的表头引用
    "功能子模块": "功能子模块",
    "功能点名称": "功能点名称",
    "功能描述":   "功能描述",
    "菜单路径":   "菜单路径",
    "负责人":     "负责人",
    "测试结论":   "内测记录/测试结论",
    "测试反馈":   "内测记录/测试反馈",
    "整改情况":   "内测记录/整改情况"
  }
}
```

**表头引用两种写法：**

| 写法 | 用在什么时候 | 例子 |
|---|---|---|
| 裸列名 | 该列名在全表唯一 | `"功能点名称"` |
| `大组标题/列名` | 列名重复（多个组下都叫「备注」）时必须用 | `"内测记录/测试反馈"` |

> 两行表头指：第 1 行是大组标题（可为空），第 2 行是具体列名。
> `build_prompt.py` 运行时会实时扫描这两行解析列号，**所以你在表里插列、删列都不会错位**。
> 列名写错时脚本会直接报错并列出「可用列」，照着改即可。

**列缺失可以留空降级**：如果你的表没有「菜单路径」这一列，
把 `"菜单路径"` 置为 `""`，该列就既不参与筛选、也不出现在提示词里。
只有 `功能子模块` 是必需项（归组依据）。

### 3. 完成授权

```bash
python3 tencentdocs.py tdoc_init
# 期望输出：READY
# 若输出 ERROR:no_token，见 references/auth.md
```

授权细节（票据从哪来、多 agent 怎么共用、怎么撤销）见 `references/auth.md`。

### 4. 实时生成提示词

```bash
python3 build_prompt.py --list                       # 看有哪些子模块
python3 build_prompt.py --submodule "你的子模块名"     # 产出提示词文本
python3 build_prompt.py --submodule "你的子模块名" --refresh   # 表格刚改过，强制重取
```

在你的 agent 里以斜杠命令触发：`/fix-prompt 你的子模块名`。

---

## 三、非宿主环境的 agent（opencode / Claude Code 等）

如果 agent 不在有宿主的客户端里运行，宿主不会注入 `CODEBUDDY_MCP_CONFIG`，
上面的「授权连接器 → 自动注入」路径不成立。改用**共享 token 文件**桥接：

1. 在**有授权**的会话里导出一份 token 文件（放在包外，或包内已被 gitignore 的 `.secrets/`）：

   ```bash
   python3 tencentdocs.py tdoc_export_token "/绝对路径/外部/tdoc_token.json"
   ```

2. 让那个 agent 在启动前设置：

   ```bash
   export TDOC_SHARED_TOKEN_FILE="/绝对路径/外部/tdoc_token.json"
   ```

   之后它运行 `build_prompt.py --list` / `--submodule` 即可实时取数。

3. token 过期后，回到步骤 1 重跑 `tdoc_export_token` 刷新。

> 完全脱离宿主、又无法从别处导出 token 的场景，需自行用开放平台
> `client_id` / `client_secret` 走 OAuth 拿 `access_token`，再写入该 JSON 文件（见 `references/auth.md` 第 4 节）。

---

## 四、安全与隔离（团队必读）

- **各人各自授权**：票据由各自宿主注入，agent 之间不共享 token；
  不要互传 `TDOC_*_ACCESS_TOKEN`。
- **凭据不进包**：`tdoc_token.json` 之类的文件必须放在 skill 包目录**之外**
  （或包内 `.secrets/`，已在 `.gitignore` 排除）；分享目录包前先确认包内没有凭据。
- **只读为主**：本 skill 只**读取**在线表格做筛选与生成提示词；
  整改完成后由**人**回表里打钩 —— 不自动写回，避免误改质检数据。
- **不提交产物**：`.cache/`（取数缓存）、`data/*.csv`（离线快照）均为生成物，已在 `.gitignore` 排除。

---

## 五、常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `ERROR:no_token` | 未授权。跑 `python3 tencentdocs.py tdoc_init` 定位，见 `references/auth.md` |
| 表头中找不到列「X」 | `config.columns` 里的列名和表里不一致。报错信息会列出「可用列」，对着改 |
| 列「X」在表头出现 N 次 | 列名重复，改用 `大组标题/列名` 写法精确定位 |
| 提示：当前筛选条件下没有任何功能点 | `config.filter` 口径写错，或确实都整改完了。字段留空即不参与筛选 |
| 匹配到多个子模块 | 子模块名不够精确。支持精确 → 归一化 → 包含三级匹配，报错会列出候选 |
| 未配置在线表标识 | 还没填 `config.doc.file_id` / `sheet_id`；也可先用 `--data` 离线跑 |
| 数据是旧的 | 缓存默认 TTL 300s；加 `--refresh` 强制重取，或用 `TDOC_CACHE_TTL` 调整 |
| 想调试取数过程 | `TDOC_DEBUG=1` 看票据与请求细节 |
