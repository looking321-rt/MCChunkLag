# -*- coding: utf-8 -*-
"""
批量扫描 —— 给一个 saves 目录（或单个世界目录），每个世界出一份卡顿热力图 HTML。

用法：
  python scan.py <saves目录或世界目录> [--out output] [--dim 0] [--simdist 10]
                 [--top 20] [--txt]

产出：
  output/<序号>_<世界名>/map.html    每世界一份交互热力图（浏览器打开）
  output/<序号>_<世界名>/report.txt  （--txt 时）spark 式文字报告
  output/汇总.txt                    所有世界的对比一览（按总卡顿分排序）
"""
import argparse
import os
import re
import sys
import time

from chunklag import leveldat, report
from main import analyze_world, render_map_for

# 扫描时跳过的目录（世界目录里不会有这些，但从 .minecraft 上层扫进来时会有）
SKIP_DIRS = {".git", "node_modules", "__pycache__", "logs", "backups", "screenshots"}


def discover_worlds(root, max_depth=4):
    """
    找存档世界目录：root 本身含 level.dat 就直接用；否则向下找（命中世界后不再往世界内部走）。
    兼容 saves/世界名 与 versions/<版本>/saves/世界名 这类嵌套。
    """
    root = os.path.abspath(root)
    if os.path.exists(os.path.join(root, "level.dat")):
        return [root]
    base = root.rstrip("\\/").count(os.sep)
    found = []
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        if "level.dat" in files:
            found.append(cur)
            dirs[:] = []
            continue
        if cur.count(os.sep) - base >= max_depth:
            dirs[:] = []
    return sorted(found)


def safe_name(name, fallback="world"):
    """世界名 → 安全的文件夹名（去掉 Windows 非法字符）。"""
    s = re.sub(r'[\\/:*?"<>|]+', "_", (name or "").strip())
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:60] or fallback


def scan(args):
    worlds = discover_worlds(args.path)
    if not worlds:
        print("没有找到存档：该目录下没有含 level.dat 的世界。", file=sys.stderr)
        return 2
    os.makedirs(args.out, exist_ok=True)

    summary = []
    for i, wdir in enumerate(worlds, 1):
        name, _dv = leveldat.describe_world(wdir)
        print("[%d/%d] %s" % (i, len(worlds), name))
        t0 = time.time()
        try:
            results = analyze_world(wdir, args.dim)
        except Exception as exc:  # 单个世界坏掉不影响整批
            print("   跳过（分析失败）: %s" % exc)
            continue
        if not results:
            print("   跳过：没有 region 区块数据")
            continue

        res = results[0][2]
        out_dir = os.path.join(args.out, "%02d_%s" % (i, safe_name(name, "world%d" % i)))
        os.makedirs(out_dir, exist_ok=True)
        msg = render_map_for(wdir, res, os.path.join(out_dir, "map.html"),
                             simdist=args.simdist, top_n=args.top)
        top = res.top_chunks[0] if res.top_chunks else None
        top_txt = "(%d,%d)=%d" % (top[0], top[1], top[2]) if top else "-"
        summary.append({"name": name, "dir": out_dir, "chunks": res.total_chunks,
                        "score": res.total_score, "top": top_txt})
        print("   区块 %d | 总卡顿分 %d | 最卡 %s" % (res.total_chunks, res.total_score, top_txt))
        print("   " + msg)
        if args.txt:
            with open(os.path.join(out_dir, "report.txt"), "w", encoding="utf-8") as f:
                f.write(report.render_text(res, top_n=args.top))
        print("   → %s（%.1fs）" % (out_dir, time.time() - t0))

    if not summary:
        print("所有世界都没有可分析的区块数据。", file=sys.stderr)
        return 3

    lines = ["批量扫描汇总（%d 个世界，按总卡顿分降序）" % len(summary), ""]
    lines.append("世界 | 区块数 | 总卡顿分 | 最卡区块")
    for s in sorted(summary, key=lambda x: -x["score"]):
        lines.append("%s | %d | %d | %s" % (s["name"], s["chunks"], s["score"], s["top"]))
    text = "\n".join(lines)
    with open(os.path.join(args.out, "汇总.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")

    print()
    print(text)
    print()
    print("输出目录: %s" % os.path.abspath(args.out))
    if args.open and hasattr(os, "startfile"):
        try:
            os.startfile(os.path.abspath(args.out))
        except OSError:
            pass  # 打不开资源管理器不影响已生成的结果
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="批量扫描多个 MC 存档，每个世界出一份卡顿热力图")
    parser.add_argument("path", help="saves 目录（或单个世界目录）")
    parser.add_argument("--out", default="output", help="输出目录（默认 ./output）")
    parser.add_argument("--dim", default="0", help="维度: 0主世界(默认) -1下界 1末地 all")
    parser.add_argument("--simdist", type=int, default=10, help="玩家模拟距离(区块)，默认10")
    parser.add_argument("--top", type=int, default=20, help="最卡 TOP N（默认20）")
    parser.add_argument("--txt", action="store_true", help="额外出每世界 report.txt 文字报告")
    parser.add_argument("--open", action="store_true", help="跑完打开输出目录")
    args = parser.parse_args(argv)

    if not os.path.isdir(args.path):
        print("错误: 目录不存在 -> %s" % args.path, file=sys.stderr)
        return 1
    return scan(args)


if __name__ == "__main__":
    sys.exit(main())
