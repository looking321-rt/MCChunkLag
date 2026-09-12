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


def _dim_region_dir(world_dir, key):
    """维度 key(0/-1/1) → region 目录路径。"""
    sub, _name = DIMENSIONS.get(key, ("", "主世界"))
    return os.path.join(world_dir, sub, "region") if sub else os.path.join(world_dir, "region")


def find_region_dirs(world_dir, dim_sel):
    """返回 [(region_path, dim_name, dim_key)]。"""
    result = []
    if dim_sel == "all":
        keys = ["0", "-1", "1"]
    else:
        keys = [dim_sel]
    for key in keys:
        rdir = _dim_region_dir(world_dir, key)
        if os.path.isdir(rdir):
            result.append((rdir, DIMENSIONS.get(key, ("", "主世界"))[1], key))
    return result


def _scan_portal_chunks(rdir):
    """轻量扫描：只挑含地狱门的区块（不统计卡顿因子），供跨维度配对判定用。"""
    portal, armed = set(), set()
    for cx, cz, nbt_data in region.scan_region_dir(rdir):
        if factors.has_portal(nbt_data):
            portal.add((cx, cz))
            if factors.has_redstone_kit(nbt_data):
                armed.add((cx, cz))
    return portal, armed


def _resolve_portal_loaders(world_dir, portal_info, all_results):
    """
    跨维度成对判定（主世界 ↔ 下界）：两侧都有地狱门 + 红石装置才算常加载装置。

    本维度扫到门时才去轻量补扫对面维度（没门就不用扫，省时间）。
    末地等其它维度不参与地狱门常加载判定。
    """
    from chunklag import loaders

    by_dim = {res.dimension_key: res for _rdir, _name, res in all_results}
    for key, mate in (("0", "-1"), ("-1", "0")):
        if mate in portal_info or key not in by_dim:
            continue
        portal, armed = portal_info.get(key, (set(), set()))
        if not (portal & armed):
            continue        # 本侧没有任何带红石的门装置 → 必然不成对，不必扫对面（省时间）
        mate_dir = _dim_region_dir(world_dir, mate)
        if os.path.isdir(mate_dir):
            portal_info[mate] = _scan_portal_chunks(mate_dir)   # 无该维度 → 留空 = 不成对

    for key, res in by_dim.items():
        if key not in ("0", "-1"):
            res.portal_loader_chunks = set()
            continue
        mate = "-1" if key == "0" else "0"
        portal, armed = portal_info.get(key, (set(), set()))
        m_portal, m_armed = portal_info.get(mate, (set(), set()))
        res.portal_loader_chunks = loaders.portal_loaders(
            portal, armed, m_portal, m_armed, key == "0")


def analyze_world(world_dir, dim_sel="0", limit_chunks=0):
    """分析存档，返回 [(region_dir, 维度名, AnalysisResult)]。dim_sel=-1/0/1/all。"""
    world_name, data_version = leveldat.describe_world(world_dir)
    entity_part = EntityPartition(world_dir)  # 1.16+ 实体分区（无则忽略）

    all_results = []
    portal_info = {}                     # dim key → (含门区块, 含门+红石区块)
    for rdir, dim_name, key in find_region_dirs(world_dir, dim_sel):
        portal_chunks = set()
        portal_armed = set()
        def gen():
            n = 0
            for cx, cz, nbt_data in region.scan_region_dir(rdir):
                # 真门判据：区块含 nether_portal 方块（黑曜石/红石太常见，会大量误报）；
                # 是否算常加载器另由 _resolve_portal_loaders 跨维度成对判定。
                if factors.has_portal(nbt_data):
                    portal_chunks.add((cx, cz))
                    if factors.has_redstone_kit(nbt_data):
                        portal_armed.add((cx, cz))
                counts = factors.analyze_chunk(nbt_data)
                if entity_part.exists():
                    entity_part.merged_counts(cx, cz, counts)
                yield cx, cz, counts
                n += 1
                if limit_chunks and n >= limit_chunks:
                    break
        res = analyze.analyze(gen(), world_name=world_name, data_version=data_version)
        res.portal_chunks = portal_chunks
        res.portal_armed_chunks = portal_armed
        res.dimension = dim_name
        res.dimension_key = key
        portal_info[key] = (portal_chunks, portal_armed)
        all_results.append((rdir, dim_name, res))

    _resolve_portal_loaders(world_dir, portal_info, all_results)
    return all_results


def render_map_for(world_dir, res, out_path, simdist=10, player=None, top_n=10):
    """
    把某个维度的分析结果渲染成交互地图 HTML（玩家模拟区 ∪ 常加载区）。

    返回一行描述文字（供 CLI / 批量扫描打印）。player=None 时自动从存档读玩家位置。
    """
    from chunklag.mapview import render_html_map
    from chunklag import mapdata as mapdata_mod

    if player is None:
        player = leveldat.read_player_position(world_dir)
    if player:
        from chunklag import loaders
        regions = loaders.collect_regions(world_dir)
        all_portal = getattr(res, "portal_chunks", None) or set()
        portal = getattr(res, "portal_loader_chunks", None) or set()
        # 每装置一个 7×7 框；重叠框合并成一个外接框。只有两侧成对的门装置才画
        devs = loaders.merge_region_boxes(loaders.portal_regions(portal)) if portal else []
        regions.extend(devs)
        portal_note = ""
        if all_portal:
            portal_note = (" | 地狱门: 门区块%d → 常加载框%d; 未成对/无红石%d 已忽略"
                           % (len(all_portal), len(devs), len(all_portal) - len(portal)))
        data = mapdata_mod.build_union_map(res, player, simdist, regions, top_n=top_n)
        msg = ("三源并集: 玩家区块(%d,%d) 模拟距离%d → 加载区%d×%d | 常加载区: %s%s"
               % (int(player[0] // 16), int(player[2] // 16), simdist,
                  2 * simdist + 1, 2 * simdist + 1,
                  ", ".join(r[1] for r in regions) if regions else "无",
                  portal_note))
    else:
        data = mapdata_mod.build_map_data(res, top_n=top_n)
        msg = "无玩家位置记录 → 输出全量地图"
    render_html_map(data, out_path, top_n=top_n)
    return msg


def main(argv=None):
    parser = argparse.ArgumentParser(description="MC 存档区块卡顿原因分析器")
    parser.add_argument("world", help="存档目录（需含 level.dat + region/）")
    parser.add_argument("--dim", default="0", help="维度: 0主世界(默认) -1下界 1末地 all全维度")
    parser.add_argument("--top", type=int, default=10, help="最卡 TOP N（默认10）")
    parser.add_argument("--limit-chunks", type=int, default=0,
                        help="每个维度最多分析多少区块（0=不限，用于快速预览）")
    parser.add_argument("--html", help="输出 HTML 报告的路径")
    parser.add_argument("--map", help="输出 HTML 交互地图的路径")
    parser.add_argument("--simdist", type=int, default=10,
                        help="玩家模拟距离(区块)，默认10，决定加载的正方形边长(2s+1)")
    parser.add_argument("--player", nargs=2, type=float, metavar=("X", "Z"),
                        help="手动指定玩家方块坐标X Z（默认自动从存档读玩家位置）")
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

    # HTML 交互地图（取第一个维度/主世界）
    if args.map:
        res = results[0][2]
        player = (args.player[0], 0.0, args.player[1], "minecraft:overworld") if args.player else None
        print(render_map_for(world_dir, res, args.map, simdist=args.simdist,
                             player=player, top_n=args.top))
        print("已输出 HTML 交互地图: %s" % args.map)

    return 0


if __name__ == "__main__":
    sys.exit(main())
