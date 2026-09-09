# -*- coding: utf-8 -*-
"""
CLI 入口：分析一份 MC 存档的区块卡顿原因。

用法：
  python main.py <存档目录>                 # 分析主世界，终端报告
  python main.py <存档目录> --dim -1        # 下界
  python main.py <存档目录> --dim all       # 全维度
  python main.py <存档目录> --top 20        # 最卡 TOP N（默认10）
  python main.py <存档目录> --html out.html # 输出 HTML
"""
import argparse
import os
import sys

from chunklag import leveldat, region, factors, analyze, report
from chunklag.entitypart import EntityPartition

# 维度 → (region 子目录, 中文名)
DIMENSIONS = {
    "0": ("", "主世界"),
    "-1": ("DIM-1", "下界"),
    "1": ("DIM1", "末地"),
}


def find_region_dirs(world_dir, dim_sel):
    """返回 [(region_path, dim_name)]。"""
    result = []
    if dim_sel == "all":
        keys = ["0", "-1", "1"]
    else:
        keys = [dim_sel]
    for key in keys:
        sub, name = DIMENSIONS.get(key, ("", "主世界"))
        rdir = os.path.join(world_dir, sub, "region") if sub else os.path.join(world_dir, "region")
        if os.path.isdir(rdir):
            result.append((rdir, name))
    return result


def analyze_world(world_dir, dim_sel="0", limit_chunks=0):
    """分析存档，返回 (AnalysisResult, 维度名)。dim_sel=-1/0/1/all。"""
    world_name, data_version = leveldat.describe_world(world_dir)
    entity_part = EntityPartition(world_dir)  # 1.16+ 实体分区（无则忽略）

    all_results = []
    for rdir, dim_name in find_region_dirs(world_dir, dim_sel):
        def gen():
            n = 0
            for cx, cz, nbt_data in region.scan_region_dir(rdir):
                counts = factors.analyze_chunk(nbt_data)
                if entity_part.exists():
                    entity_part.merged_counts(cx, cz, counts)
                yield cx, cz, counts
                n += 1
                if limit_chunks and n >= limit_chunks:
                    break
        res = analyze.analyze(gen(), world_name=world_name, data_version=data_version)
        res.dimension = dim_name
        all_results.append((rdir, dim_name, res))
    return all_results


def main(argv=None):
    parser = argparse.ArgumentParser(description="MC 存档区块卡顿原因分析器")
    parser.add_argument("world", help="存档目录（需含 level.dat + region/）")
    parser.add_argument("--dim", default="0", help="维度: 0主世界(默认) -1下界 1末地 all全维度")
    parser.add_argument("--top", type=int, default=10, help="最卡 TOP N（默认10）")
    parser.add_argument("--limit-chunks", type=int, default=0,
                        help="每个维度最多分析多少区块（0=不限，用于快速预览）")
    parser.add_argument("--html", help="输出 HTML 报告的路径")
    args = parser.parse_args(argv)

    world_dir = args.world
    if not os.path.isdir(world_dir):
        print("错误: 目录不存在 -> %s" % world_dir, file=sys.stderr)
        return 1

    results = analyze_world(world_dir, args.dim, args.limit_chunks)
    if not results:
        print("警告: 该存档目录没有找到 region/ 区块数据。", file=sys.stderr)
        return 2

    # 终端报告
    for rdir, dim_name, res in results:
        print(report.render_text(res, top_n=args.top))
        print()

    # HTML 报告
    if args.html:
        html_parts = []
        for rdir, dim_name, res in results:
            html_parts.append(report.render_html(res, top_n=args.top))
        with open(args.html, "w", encoding="utf-8") as f:
            f.write("\n".join(html_parts))
        print("已输出 HTML: %s" % args.html)

    return 0


if __name__ == "__main__":
    sys.exit(main())
