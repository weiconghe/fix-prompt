#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_filtered.py —— （可选）把筛选后的数据导出为本地快照 CSV。

说明：本文件**不是 build_prompt.py 的必需依赖**。build_prompt 默认通过共享数据源接口
（tdoc_datasource）实时取数；本脚本仅用于需要离线快照 / 审计留痕时，
从同一数据源生成一份 data/filtered.csv（与线上同源，可随时 --refresh 重建）。

用法：
  python3 gen_filtered.py [--out data/filtered.csv] [--refresh] [--data other.csv]
"""
import argparse
import csv
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ⚠️ 必须用「模块」方式导入而不是 from ... import COL：
# resolve_columns() 会重新绑定 build_prompt.COL（新 dict），
# 若按值导入 Col，拿到的是导入时的旧空 dict，导出的会是空行。
import build_prompt as bp  # noqa: E402

DEFAULT_OUT = os.path.join(HERE, "data", "filtered.csv")


def main():
    ap = argparse.ArgumentParser(description="把筛选后的功能点导出为本地快照 CSV（可选）")
    ap.add_argument("--out", default=DEFAULT_OUT, help="输出快照 CSV 路径")
    ap.add_argument("--refresh", action="store_true", help="强制刷新在线取数缓存")
    ap.add_argument("--data", help="离线：直接读本地 CSV（跳过在线取数）")
    args = ap.parse_args()

    rows = bp.load_rows(SimpleNamespace(data=args.data, refresh=args.refresh))

    # 只导出当前实际启用的列（config.columns 里置空的列不出现）；
    # 用 bp.COL 而非导入时快照，确保拿到 resolve_columns() 之后的结果。
    out_cols = [c for c in bp.LOGICAL_COLUMNS if c in bp.COL]

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(out_cols)
        for r in rows:
            w.writerow([bp.get(r, c) for c in out_cols])
    print(f"已写出 {len(rows)} 条快照（{len(out_cols)} 列）-> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
