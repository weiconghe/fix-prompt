#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tdoc_datasource.py —— 在线电子表格的共享数据源接口。

本模块是「数据层」的唯一出口：消费方 import 本模块后调用 get_sheet_rows()，
即可拿到在线表格的最新数据，**无需把远程表格导出/复制为本地 CSV 再读取**。

特性：
  - 实时取数：底层走 tencentdocs.call_tool('sheet-mcp', 'get_cell_data', ...)，
    始终以在线表格为唯一数据源。
  - 透明缓存：取数结果按 (file_id, sheet_id) 缓存到同目录 .cache/，默认 TTL 300s；
    过期或 refresh=True 时自动重取。缓存是"可透明刷新"的，不是手工维护的快照副本。
  - 断网兜底：实时取数失败且本地有旧缓存时，回退旧缓存并告警，不静默失败。
  - 不落盘敏感信息：只缓存表格内容（非凭据）；凭据由 tencentdocs 在内存中透传。

用法（供其他脚本 / agent 调用）：
    import sys; sys.path.insert(0, r"<本目录>")
    from tdoc_datasource import get_sheet_rows
    rows = get_sheet_rows("<file_id>", "<sheet_id>")   # list[list[str]]，含 2 行表头

独立自测：
    python3 tdoc_datasource.py            # 用 config.json 里的 doc 标识试取一次
"""
import csv
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, ".cache")
DEFAULT_TTL = int(os.environ.get("TDOC_CACHE_TTL", "300"))  # 秒
CHUNK = 200          # 每次 get_cell_data 拉取的行数
END_COL = 30         # 每次拉取的列数（0..END_COL-1，足够覆盖筛选所需列）

# 让本模块能 import 同目录下的 tencentdocs（调用入口）
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# ── 共享凭据文件（多 agent 场景的可选兜底）──────────────────────────────────
# 契约见 tencentdocs._load_tokens：环境变量未注入 TDOC_*_ACCESS_TOKEN 时，
# 读 TDOC_SHARED_TOKEN_FILE 指向的 JSON {"oauth":..., "oneid":...}。
# 若使用方未显式设置该环境变量，本模块会尝试包内约定位置 ./.secrets/tdoc_token.json，
# 让 agent 进程无需额外 export 即可取票。
# 生成方式：python3 tencentdocs.py tdoc_export_token "<本目录>/.secrets/tdoc_token.json"
# ⚠️ 该文件含实时凭据，已在 .gitignore 中排除，切勿随包分享或提交。
if not os.environ.get("TDOC_SHARED_TOKEN_FILE"):
    _default_tk = os.path.join(HERE, ".secrets", "tdoc_token.json")
    if os.path.isfile(_default_tk):
        os.environ["TDOC_SHARED_TOKEN_FILE"] = _default_tk

from tencentdocs import call_tool  # noqa: E402


def _cache_path(file_id, sheet_id):
    return os.path.join(CACHE_DIR, f"sheet_{file_id}_{sheet_id}.json")


def _fetch_range(file_id, sheet_id, start, end):
    """拉取 [start, end) 行；成功返回 list[list[str]]（含可能的尾部空行），失败返回 None。"""
    args = {
        "file_id": file_id,
        "sheet_id": sheet_id,
        "start_row": start,
        "start_col": 0,
        "end_row": end,
        "end_col": END_COL,
        "return_csv": True,
    }
    res, err = call_tool("sheet-mcp", "get_cell_data", args)
    if err or not isinstance(res, dict) or "result" not in res:
        return None
    try:
        # content[0].text 是 JSON 字符串，需再解析一次
        inner = json.loads(res["result"]["content"][0]["text"])
        csv_text = inner["csv_data"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return None
    if not csv_text.strip():
        return []
    return list(csv.reader(io.StringIO(csv_text)))


def _find_end(file_id, sheet_id, lo, hi):
    """[lo, hi) 已知越界报错，二分求出「最大 idx 使 [lo, idx) 成功」的 idx。"""
    if hi - lo <= 1:
        return lo
    mid = (lo + hi) // 2
    if _fetch_range(file_id, sheet_id, lo, mid) is not None:
        return _find_end(file_id, sheet_id, mid, hi)
    return _find_end(file_id, sheet_id, lo, mid)


def _fetch_all(file_id, sheet_id):
    """分页拉取整张表（含 2 行表头），返回 list[list[str]]。

    在线表格接口在请求范围超出表尾时会报错而非返回空，故：
    正常按 CHUNK 窗口推进；某次越界时，二分定位表尾并把最后一段补齐，避免漏行。
    """
    all_rows = []
    start = 0
    while True:
        rows = _fetch_range(file_id, sheet_id, start, start + CHUNK)
        if rows is None:
            end = _find_end(file_id, sheet_id, start, start + CHUNK)
            if end > start:
                tail = _fetch_range(file_id, sheet_id, start, end)
                if tail:
                    all_rows.extend(tail)
            break
        real = [r for r in rows if r]
        all_rows.extend(rows)
        if len(real) < CHUNK:
            break  # 本批不足一窗，确为表尾
        start += CHUNK
    return all_rows


def get_sheet_rows(file_id, sheet_id, refresh=False, ttl=DEFAULT_TTL):
    """返回在线表格的全部行（含 2 行表头），list[list[str]]。

    refresh=True 时忽略缓存强制重取；否则命中且未过期的缓存直接返回（透明刷新）。
    """
    cp = _cache_path(file_id, sheet_id)
    if not refresh and os.path.isfile(cp):
        try:
            with open(cp, encoding="utf-8") as f:
                rec = json.load(f)
            # 空行数的缓存视为无效（可能是历史 bug 写入的脏数据），不直接返回
            if rec.get("rows") and time.time() - rec.get("ts", 0) < ttl:
                return rec.get("rows", [])
        except Exception:  # noqa: BLE001
            pass  # 缓存损坏则重取
    rows = _fetch_all(file_id, sheet_id)
    if not rows or len(rows) <= 2:
        # 取数失败/空表（常见于未授权）：绝不覆盖旧缓存，优先回退过期缓存兜底
        if os.path.isfile(cp):
            try:
                with open(cp, encoding="utf-8") as f:
                    old = json.load(f).get("rows", [])
                if old:
                    print("WARN: 实时取数失败（可能未授权），已回退使用过期缓存；"
                          "授权后可加 --refresh 强制刷新。", file=sys.stderr)
                    return old
            except Exception:  # noqa: BLE001
                pass
        raise RuntimeError(
            "在线表格实时取数失败（可能未授权），且本地无可用缓存。"
            "请先完成授权（python3 tencentdocs.py tdoc_init 检查，详见 references/auth.md），"
            "或用 --data <本地CSV> 离线运行。"
        )
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cp, "w", encoding="utf-8") as f:
        json.dump({"ts": time.time(), "rows": rows}, f, ensure_ascii=False)
    return rows


def clear_cache(file_id=None, sheet_id=None):
    """清理缓存。不传参清空全部；传 file_id/sheet_id 清空指定表。"""
    if file_id is None:
        if os.path.isdir(CACHE_DIR):
            for fn in os.listdir(CACHE_DIR):
                if fn.startswith("sheet_") and fn.endswith(".json"):
                    os.remove(os.path.join(CACHE_DIR, fn))
        return
    cp = _cache_path(file_id, sheet_id)
    if os.path.isfile(cp):
        os.remove(cp)


if __name__ == "__main__":
    # 简单自测：读取同目录 config.json 里的 doc 标识试取一次
    _cfg = {}
    _cp = os.path.join(HERE, "config.json")
    if os.path.isfile(_cp):
        with open(_cp, encoding="utf-8") as _f:
            _cfg = json.load(_f)
    _doc = _cfg.get("doc") or {}
    _fid = os.environ.get("FIXPROMPT_DOC_FILE_ID") or _doc.get("file_id") or ""
    _sid = os.environ.get("FIXPROMPT_DOC_SHEET_ID") or _doc.get("sheet_id") or ""
    if not _fid or not _sid or str(_fid).startswith("<"):
        print("未配置 doc.file_id / doc.sheet_id（config.json），跳过自测。")
        sys.exit(0)
    r = get_sheet_rows(_fid, _sid)
    print(f"取到 {len(r)} 行（含表头）")
