#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_prompt.py —— 为 coding agent 构造「单功能子模块整改提示词」（fix-prompt）。

设计原则：
  - **按需、单模块**：每次用「斜杠命令 fix-prompt + 功能子模块名」触发，只生成**一段**
    针对该子模块、可直接复制给 coding agent 的提示词文本（打印到 stdout）。
  - **不自动批量生成、不写入执行目录**：本脚本默认只输出文本，不产生 prompts/ 目录、
    不驱动 agent 读目录执行。用户自行复制文本 → 粘贴给 coding agent 编码 →
    验收后回在线表格打钩 → 循环下一个子模块。
  - **数据源可插拔**：默认走同目录 tdoc_datasource.get_sheet_rows() 实时读取腾讯文档在线表格
    （带 TTL 缓存，可 --refresh 透明刷新）；加 --data 指定本地 CSV（两行表头、全列布局）
    可完全离线运行，便于先试跑再接入授权。
  - **全配置驱动**：文档标识 / 筛选口径 / 列映射全部来自 config.json，无需改代码即可适配别的表格。

用法（标准交互）：
  python3 build_prompt.py --list                              # 列出可选功能子模块及数量（不确定名字时）
  python3 build_prompt.py --submodule "订单管理-订单查询"        # 输出一段提示词文本（可复制）
  python3 build_prompt.py --submodule "..." --refresh          # 强制刷新在线取数缓存后输出
  python3 build_prompt.py --submodule "..." --data examples/demo_table.csv   # 离线试跑

环境变量：
  FIXPROMPT_CONFIG          指定另一份 config.json 的路径（默认取同目录 config.json）
  FIXPROMPT_DOC_FILE_ID     覆盖 config.doc.file_id
  FIXPROMPT_DOC_SHEET_ID    覆盖 config.doc.sheet_id
"""
import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("FIXPROMPT_CONFIG") or os.path.join(HERE, "config.json")

# 代码内固定的逻辑列名（供提示词渲染与筛选使用）；
# 它们各自对应你表里的哪个表头，由 config.columns 决定。
LOGICAL_COLUMNS = [
    "功能子模块",   # 归组依据，必填
    "功能点名称",
    "功能描述",
    "菜单路径",
    "负责人",
    "测试结论",
    "测试反馈",
    "整改情况",
]
GROUPING_COLUMN = "功能子模块"

# 内置默认表头引用：逻辑列名 == 表头列名（裸列名）。
# 你的表列名不同 / 有重名列时，改 config.json 的 columns 覆盖即可。
_BUILTIN_COLUMN_REFS = {c: c for c in LOGICAL_COLUMNS}


# ── 配置加载（config.json，可被环境变量覆盖） ────────────────────────────────
def _load_config():
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:  # noqa: BLE001
            print(f"WARN: 读取配置 {CONFIG_PATH} 失败，改用内置默认值：{e}", file=sys.stderr)
    else:
        print(f"WARN: 未找到配置 {CONFIG_PATH}，改用内置默认值。", file=sys.stderr)
    return {}


CFG = _load_config()

# 列定位：columns 值为「表头引用」（裸列名 或 大组标题/列名），
# 运行时扫描两行表头动态解析为实际列索引，表头插列/删列不再导致错位。
# 重名列（如多个「测试结论」「备注」）必须用「大组标题/列名」精确定位。
COLUMN_REFS = dict(_BUILTIN_COLUMN_REFS)
for _k, _v in (CFG.get("columns") or {}).items():
    COLUMN_REFS[_k] = "" if _v is None else str(_v)

# 运行时解析出的 逻辑列名 -> 实际列索引；未启用的列不在其中
COL = {}


def resolve_doc_ids():
    """在线表标识：环境变量 > config.json。没有内置默认值，必须显式配置。"""
    doc = CFG.get("doc") or {}

    def _s(x):
        return str(x).strip() if x is not None else ""

    file_id = _s(os.environ.get("FIXPROMPT_DOC_FILE_ID")) or _s(doc.get("file_id"))
    sheet_id = _s(os.environ.get("FIXPROMPT_DOC_SHEET_ID")) or _s(doc.get("sheet_id"))
    # 占位符未替换时视为未配置
    if file_id.startswith("<") or sheet_id.startswith("<"):
        return "", ""
    return file_id, sheet_id


def resolve_filter():
    """筛选口径。任一值为空串表示该条件不参与筛选。"""
    f = CFG.get("filter") or {}

    def _s(x):
        return "" if x is None else str(x).strip()

    return {
        "developer": _s(f.get("developer")),
        "conclusion": _s(f.get("test_conclusion_contains")),
        "fix": _s(f.get("fix_status")),
    }


def _import_datasource():
    """懒加载共享数据源接口（同目录内置）。"""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import tdoc_datasource  # noqa: F401
        return tdoc_datasource
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"无法加载数据源模块（tdoc_datasource）：{e}\n"
            f"请确认 {HERE} 下存在该文件，或用 --data 指定本地 CSV 离线运行。"
        )


def resolve_columns(raw_rows):
    """扫描两行表头（rows[0] 大组标题 / rows[1] 列名），把 COLUMN_REFS 解析为实际列索引。

    表头引用规则：
      - 裸列名（全表唯一时），如「功能点名称」；
      - 「大组标题/列名」（重名列必用），如「内测记录/测试结论」。
    找不到或裸名重名时报错并列出可用列；引用为空串表示该列不启用（跳过）。
    """
    rows = list(raw_rows) + [[], []]
    top, sub = rows[0], rows[1]
    bare, full, group = {}, {}, ""
    for i in range(max(len(top), len(sub))):
        t = top[i].strip() if i < len(top) else ""
        if t:
            group = t
        n = sub[i].strip() if i < len(sub) else ""
        name = n or t
        if not name:
            continue
        bare.setdefault(name, []).append(i)
        full.setdefault(f"{group}/{name}", i)

    global COL
    COL = {}
    for logical in LOGICAL_COLUMNS:
        ref = COLUMN_REFS.get(logical, "")
        if not ref:
            continue  # 显式置空 = 该列不参与
        idxs = bare.get(ref) if "/" not in ref else None
        if idxs:
            if len(idxs) > 1:
                raise SystemExit(
                    f"列「{ref}」在表头出现 {len(idxs)} 次，请在 config.columns 里用"
                    f"「大组标题/列名」精确定位（逻辑列：{logical}）。可用：{sorted(full)}")
            COL[logical] = idxs[0]
        elif ref in full:
            COL[logical] = full[ref]
        else:
            raise SystemExit(
                f"表头中找不到列「{ref}」（逻辑列：{logical}）。可用：{sorted(full)}")

    if GROUPING_COLUMN not in COL:
        raise SystemExit(
            f"config.columns 必须为「{GROUPING_COLUMN}」配置一个确实存在的表头引用（按它归组）。")
    return COL


def get(row, name):
    """按逻辑列名取单元格值；该列未启用时返回空串。"""
    i = COL.get(name)
    if i is None:
        return ""
    return row[i] if len(row) > i else ""


def _set(r, name, val):
    """按逻辑列名写回单元格（必要时补齐行长度）；该列未启用时静默跳过。"""
    i = COL.get(name)
    if i is None:
        return
    if len(r) <= i:
        r.extend([""] * (i - len(r) + 1))
    r[i] = val


def apply_filter(raw_rows):
    """raw_rows: 含 2 行表头的全量行。返回筛选 + 前向填充后的数据行。

    「功能子模块」「功能描述」通常是合并单元格（仅组首行有值），必须先按原始表序
    对全量行做组内前向填充，再筛选；否则中间未命中的组首行会被丢弃，
    导致其后续空行丢失子模块归属或功能描述（如组首行测试已通过被筛掉时）。
    """
    flt = resolve_filter()
    data = [r for r in raw_rows[2:] if r]

    last_sub = ""
    last_desc = ""
    for r in data:
        cur = get(r, GROUPING_COLUMN).strip()
        if cur:
            last_sub = cur
            last_desc = ""  # 新组开始，重置描述继承
        elif last_sub:
            _set(r, GROUPING_COLUMN, last_sub)
        d = get(r, "功能描述").strip()
        if d:
            last_desc = d
        elif last_desc:
            _set(r, "功能描述", last_desc)

    def keep(r):
        if flt["developer"] and get(r, "负责人").strip() != flt["developer"]:
            return False
        if flt["conclusion"] and flt["conclusion"] not in get(r, "测试结论"):
            return False
        if flt["fix"] and get(r, "整改情况").strip().upper() != flt["fix"].upper():
            return False
        return True

    return [r for r in data if keep(r)]


def load_rows(args):
    """返回筛选+前向填充后的数据行。实时（默认）或离线（--data）。"""
    if args.data:
        if not os.path.isfile(args.data):
            raise SystemExit(f"--data 指定的文件不存在：{args.data}")
        with open(args.data, encoding="utf-8") as f:
            rows = list(csv.reader(f))
        resolve_columns(rows)
        return apply_filter(rows)

    ds = _import_datasource()
    file_id, sheet_id = resolve_doc_ids()
    if not file_id or not sheet_id:
        raise SystemExit(
            "未配置在线表标识。请在 config.json 的 doc.file_id / doc.sheet_id 填入目标表格标识，"
            "或设置环境变量 FIXPROMPT_DOC_FILE_ID / FIXPROMPT_DOC_SHEET_ID；\n"
            "也可以先用 --data examples/demo_table.csv 离线试跑。")
    raw = ds.get_sheet_rows(file_id, sheet_id, refresh=args.refresh)
    resolve_columns(raw)
    return apply_filter(raw)


def norm(s):
    """子模块名归一化：全角/半角箭头转连字符、去空白、忽略大小写。"""
    return s.replace("＞", "-").replace(">", "-").replace(" ", "").strip().lower()


def find_group(rows, name):
    """先精确匹配，再归一化匹配，最后双向包含模糊匹配；返回 [(子模块名, 行列表)]。"""
    by_sub = {}
    for r in rows:
        by_sub.setdefault(get(r, GROUPING_COLUMN), []).append(r)
    if name in by_sub:
        return [(name, by_sub[name])]
    nname = norm(name)
    for k, v in by_sub.items():
        if norm(k) == nname:
            return [(k, v)]
    return [(k, v) for k, v in by_sub.items() if nname in norm(k) or norm(k) in nname]


def render_prompt(submodule, rows):
    """渲染单段提示词文本（可直接复制给 coding agent）。

    功能描述两种风格：
      - 组内共享一段描述（合并单元格写在组首行）→ 提升为「功能子模块简介」章节；
      - 各功能点描述各自独立 → 逐条列在功能点下。
    """
    has_desc = "功能描述" in COL
    first_desc = get(rows[0], "功能描述").strip()
    shared_desc = has_desc and bool(first_desc) and all(
        get(r, "功能描述").strip() in ("", first_desc) for r in rows[1:])

    lines = [
        "# 功能子模块整改提示词",
        "",
        f"## 功能子模块\n{submodule}",
        "",
    ]
    if shared_desc:
        lines += [f"## 功能子模块简介\n{first_desc}", ""]
    lines.append(f"## 功能点清单（共 {len(rows)} 个）")
    lines.append("")
    for i, r in enumerate(rows, 1):
        name = get(r, "功能点名称").strip() or f"（未命名功能点 {i}）"
        lines.append(f"### 功能点 {i}：{name}")
        if not shared_desc and has_desc:
            desc = get(r, "功能描述").strip()
            lines.append(f"- 功能说明：{desc if desc else '（无）'}")
        if "菜单路径" in COL:
            menu = get(r, "菜单路径").strip()
            lines.append(f"- 菜单页路径：{menu if menu else '（无）'}")
        if "测试反馈" in COL:
            fb = get(r, "测试反馈").strip()
            lines.append(f"- 测试反馈：{fb if fb else '（无）'}")
        lines.append("")
    lines.append("## 你的工作")
    lines.append(
        "你是一名全栈开发 agent。请基于本项目（前端 + 后端 + 数据库等全栈代码），"
        f"对上述「{submodule}」下的全部功能点测试反馈进行全栈改造。要求：")
    lines.append("1. **先给出改造方案**：明确影响范围、涉及的前后端文件/接口、数据结构变更、"
                 "实现步骤与风险；方案经确认后再动手编码。")
    lines.append("2. **逐条对照测试反馈**：定位每条不通过的根因并修复，确保与功能说明一致。")
    lines.append("3. **自测与验证**：修复后给出自测步骤与预期结果，说明如何在对应菜单页路径下验证。")
    lines.append("4. 保持改动最小且聚焦本子模块，不引入无关变更。")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(
        description="构造「单功能子模块整改提示词」——按需、单模块、输出一段可复制的文本")
    ap.add_argument("--submodule", help="功能子模块名称，如 订单管理-订单查询")
    ap.add_argument("--list", action="store_true", help="列出所有可选功能子模块及功能点数量")
    ap.add_argument("--data", help="离线模式：直接读取本地 CSV（两行表头、全列布局），跳过在线取数")
    ap.add_argument("--refresh", action="store_true", help="强制刷新在线取数缓存（重新从在线表格取数）")
    args = ap.parse_args()

    rows = load_rows(args)
    if not rows:
        print("提示：当前筛选条件下没有任何功能点。请检查 config.json 的 filter 口径"
              "（字段留空即表示该条件不参与筛选）。", file=sys.stderr)
        return 0

    by_sub = {}
    for r in rows:
        by_sub.setdefault(get(r, GROUPING_COLUMN), []).append(r)
    ordered = sorted(by_sub.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    # 列出可选子模块（未给名字或显式 --list 时）
    if args.list or not args.submodule:
        print(f"可选功能子模块（共 {len(ordered)} 个）—— 名称 | 功能点数量：")
        for k, v in ordered:
            print(f"  {len(v):>2} | {k}")
        if not args.submodule:
            print("\n用法：在斜杠命令后接功能子模块名，例如")
            print("  /fix-prompt 订单管理-订单查询")
            print('（脚本对应：python3 build_prompt.py --submodule "<名称>"）')
        return 0

    # 单模块模式：输出一段可复制的提示词文本
    groups = find_group(rows, args.submodule.strip())
    if not groups:
        print(f"ERROR: 未找到功能子模块匹配「{args.submodule}」", file=sys.stderr)
        print("可用子模块请先用 --list 查看。", file=sys.stderr)
        return 1
    if len(groups) > 1:
        print("匹配到多个子模块，请精确指定其一：", file=sys.stderr)
        for k, v in groups:
            print(f"  {len(v)} | {k}", file=sys.stderr)
        return 1

    submodule, grp = groups[0]
    print(render_prompt(submodule, grp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
