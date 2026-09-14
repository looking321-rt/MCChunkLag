# -*- coding: utf-8 -*-
"""
CLI 入口：分析一份 MC 存档的区块卡顿原因。

用法：
  python main.py <存档目录>                 # 分析主世界，终端报告
  python main.py <存档目录> --dim -1        # 下界
  python main.py <存档目录> --dim all       # 全维度
  python main.py <存档目录> --top 20        # 最卡 TOP N（默认10）
  python main.py <存档目录> --html out.html # 输出 HTML
  python main.py <存档目录> --dim all --map out.html
                                            # 每维度一份图（out/主世界/map.html …），左上角可切换
  python main.py <存档目录> --map out.html --scoped
                                            # 只画「玩家模拟区 ∪ 常加载区」（默认全量）
"""
import argparse
import os
import sys

from chunklag import leveldat, region, factors, analyze, report, layout, loaders
from chunklag.entitypart import EntityPartition


def find_region_dirs(world_dir, dim_sel):
    """
    返回 [(region_path, dim_name, dim_key, dim_dir, dim_id)]。

    维度目录由 layout.dimension_dirs 解析：新布局（26.x 起）dimensions/<ns>/<dim>/、
    旧布局 region/ + DIM-1 + DIM1，以及**两者混存**（1.20 整合包 + 自定义维度 mod）都支持；
    dim_sel 仍是 0/-1/1/all（自定义维度只在 all 时分析）。
    """
    result = []
    for dim_id, dim_dir in layout.dimension_dirs(world_dir):
        key = layout.dim_key(dim_id)
        if dim_sel != "all" and key != dim_sel:
            continue
        result.append((os.path.join(dim_dir, "region"), layout.dim_label(dim_id),
                       key, dim_dir, dim_id))
    return result


def _scan_dimension(dim_dir, hook=None):
    """
    轻量扫描一个维度（只挑门/铁轨，不统计卡顿因子）：
    返回 (含门区块, 带加载器证据的门区块)。供跨维度成对判定用。
    hook 见 analyze_world（补扫的 region 文件不在计划分母里，由 hook 自行补分母）。
    """
    portal, redstone, rail = set(), set(), set()
    rdir = os.path.join(dim_dir, "region")
    if os.path.isdir(rdir):
        for cx, cz, nbt_data in region.scan_region_dir(rdir, hook=hook):
            if not factors.has_portal(nbt_data):
                continue
            portal.add((cx, cz))
            if factors.has_redstone_kit(nbt_data):
                redstone.add((cx, cz))
            if factors.has_active_powered_rail(nbt_data):
                rail.add((cx, cz))
    minecart = loaders.read_minecart_chunks(os.path.join(dim_dir, "entities"))
    return portal, loaders.loader_evidence(portal, redstone, rail, minecart)


def _resolve_portal_loaders(world_dir, portal_info, all_results, hook=None):
    """
    跨维度成对判定（主世界 ↔ 下界）：两侧都有地狱门 + 加载器证据才算常加载装置。

    本维度扫到带证据的门装置时才去轻量补扫对面维度（否则不必扫，省时间）。
    末地等其它维度不参与地狱门常加载判定。
    """
    by_dim = {res.dimension_id: res for _rdir, _name, res, _d in all_results}
    for key, mate in (("0", "-1"), ("-1", "0")):
        dim_id, mate_id = layout.DIM_SEL_TO_ID[key], layout.DIM_SEL_TO_ID[mate]
        if mate_id in portal_info or dim_id not in by_dim:
            continue
        portal, armed = portal_info.get(dim_id, (set(), set()))
        if not (portal & armed):
            continue        # 本侧没有任何带证据的门装置 → 必然不成对，不必扫对面（省时间）
        mate_dir = layout.dim_dir(world_dir, mate_id)
        if mate_dir:
            portal_info[mate_id] = _scan_dimension(mate_dir, hook=hook)   # 无该维度 → 留空 = 不成对

    for dim_id, res in by_dim.items():
        key = layout.dim_key(dim_id)
        if key not in ("0", "-1"):
            res.portal_loader_chunks = set()      # 自定义维度不参与地狱门成对判定
            continue
        mate_id = layout.DIM_SEL_TO_ID["-1" if key == "0" else "0"]
        portal, armed = portal_info.get(dim_id, (set(), set()))
        m_portal, m_armed = portal_info.get(mate_id, (set(), set()))
        res.portal_loader_chunks = loaders.portal_loaders(
            portal, armed, m_portal, m_armed, key == "0")


def analyze_world(world_dir, dim_sel="0", limit_chunks=0, hook=None):
    """
    分析存档，返回 [(region_dir, 维度名, AnalysisResult, 维度目录)]。dim_sel=-1/0/1/all。

    hook（可选，默认 None = 原行为）= 进度/中断钩子（见 chunklag.scanjob）：
    每个 region 文件与区块都会回调，钩子可抛 ScanCancelled 中断扫描。
    """
    world_name, data_version = leveldat.describe_world(world_dir)
    pearls = loaders.read_ender_pearls(world_dir)      # 末影珍珠加载器（存在玩家数据里）

    all_results = []
    portal_info = {}                     # dim_id → (含门区块, 带加载器证据区块)
    for rdir, dim_name, key, dim_dir, dim_id in find_region_dirs(world_dir, dim_sel):
        entity_part = EntityPartition(dim_dir)         # 1.16+ 实体分区（无则忽略）
        portal_chunks, redstone_chunks, rail_chunks = set(), set(), set()
        def gen():
            n = 0
            for cx, cz, nbt_data in region.scan_region_dir(rdir, hook=hook):
                # 真门判据：区块含 nether_portal 方块（黑曜石/红石太常见，会大量误报）；
                # 是否算常加载器另由 _resolve_portal_loaders 跨维度成对判定。
                if factors.has_portal(nbt_data):
                    portal_chunks.add((cx, cz))
                    if factors.has_redstone_kit(nbt_data):
                        redstone_chunks.add((cx, cz))
                    if factors.has_active_powered_rail(nbt_data):
                        rail_chunks.add((cx, cz))
                counts = factors.analyze_chunk(nbt_data)
                # 实体因子合并：1.16+ 实体（含掉落物，按物品个数计）在 entities 分区，
                # 区块 NBT 里读不到，必须从这里补。
                if entity_part.exists():
                    entity_part.merged_counts(cx, cz, counts)
                yield cx, cz, counts
                n += 1
                if limit_chunks and n >= limit_chunks:
                    break
        res = analyze.analyze(gen(), world_name=world_name, data_version=data_version)
        minecart_chunks = loaders.read_minecart_chunks(os.path.join(dim_dir, "entities"))
        portal_armed = loaders.loader_evidence(
            portal_chunks, redstone_chunks, rail_chunks, minecart_chunks)
        res.portal_chunks = portal_chunks
        res.portal_armed_chunks = portal_armed
        res.portal_rail_chunks = rail_chunks
        res.minecart_chunks = minecart_chunks
        res.pearls = [p for p in pearls if p["dim"] == dim_id]
        res.dimension = dim_name
        res.dimension_key = key
        res.dimension_id = dim_id
        portal_info[dim_id] = (portal_chunks, portal_armed)
        all_results.append((rdir, dim_name, res, dim_dir))

    _resolve_portal_loaders(world_dir, portal_info, all_results, hook=hook)
    return all_results


def render_map_for(world_dir, res, out_path, simdist=10, player=None, top_n=10,
                   scoped=False, nav=None):
    """
    把某个维度的分析结果渲染成交互地图 HTML。

    **默认全量**（scoped=False）：该维度所有区块都画（2026-09-14 起）；
    scoped=True 走旧口径「玩家模拟区 ∪ 常加载区」，只画并集内区块。

    维度语义（2026-09-14 修正）：
      · 常加载区按**本维度**取（loaders.collect_regions(dim_id=...)）—— 出生点恒加载
        只有主世界有，下界/末地不再冒出主世界坐标的绿框。
      · 玩家模拟区只在**玩家当前所在维度**才画：早期不分维度，玩家在主世界时下界/末地
        图上也会画出玩家区块（用户反馈「切换下界也能看到玩家坐标」）。
    返回一行描述文字（供 CLI / 批量扫描打印）。player=None 时自动从存档读。
    """
    from chunklag.mapview import render_html_map
    from chunklag import mapdata as mapdata_mod

    if player is None:
        player = leveldat.read_player_position(world_dir)
    dim_id = getattr(res, "dimension_id", None) or "minecraft:overworld"
    player_here = None
    if player and (player[3] or "minecraft:overworld") == dim_id:
        player_here = player

    regions = loaders.collect_regions(world_dir, dim_id=dim_id)
    note = ""
    all_portal = getattr(res, "portal_chunks", None) or set()
    portal = getattr(res, "portal_loader_chunks", None) or set()
    devs = loaders.merge_region_boxes(loaders.portal_regions(portal)) if portal else []
    if all_portal:
        regions.extend(devs)
        note += (" | 地狱门: 门区块%d → 常加载框%d; 未成对/无证据%d 已忽略"
                 % (len(all_portal), len(devs), len(all_portal) - len(portal)))
    pearls = getattr(res, "pearls", None) or []
    if pearls:
        regions.extend(loaders.merge_region_boxes(loaders.pearl_regions(pearls)))
        note += " | 珍珠: %d 颗 → 强加载区" % len(pearls)

    data = mapdata_mod.build_union_map(res, player_here, simdist, regions,
                                       top_n=top_n, union_only=scoped)
    if player_here:
        scope = "玩家模拟区∪常加载区" if scoped else "全量"
        msg = ("%s地图: 区块 %d | 玩家区块(%d,%d) 模拟距离%d → 加载区%d×%d | 常加载区: %s%s"
               % (scope, data["total"], int(player_here[0] // 16), int(player_here[2] // 16),
                  simdist, 2 * simdist + 1, 2 * simdist + 1,
                  ", ".join(r[1] for r in regions) if regions else "无",
                  note))
    else:
        msg = ("全量地图: 区块 %d | 该维度无玩家记录（或玩家不在本维度）"
               " | 常加载区: %s%s"
               % (data["total"],
                  ", ".join(r[1] for r in regions) if regions else "无",
                  note))
    render_html_map(data, out_path, top_n=top_n, nav=nav)
    return msg


def dim_nav(items, cur_dir):
    """
    维度切换按钮数据（HTML 左上角那个「主世界 / 下界 / 末地」切换条）。

    items = [(维度名, 该维度图的目录)]，cur_dir = 当前这张图所在目录。
    只有一个维度时返回 None（不出按钮）。href 用相对路径 + URL 编码
    —— 目录名常含中文（「主世界」），不编码在 file:// 下也能用，但编码后更稳。
    """
    import urllib.parse

    if len(items) < 2:
        return None
    cur = os.path.abspath(cur_dir)
    nav = []
    for name, d in items:
        rel = os.path.relpath(os.path.join(d, "map.html"), cur_dir)
        href = "/".join(urllib.parse.quote(p)
                        for p in rel.replace("\\", "/").split("/"))
        nav.append({"label": name, "href": href,
                    "active": os.path.abspath(d) == cur})
    return nav


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
    parser.add_argument("--scoped", action="store_true",
                        help="只画「玩家模拟区 ∪ 常加载区」（旧口径）；默认全量输出该维度所有区块")
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
    for rdir, dim_name, res, _dim_dir in results:
        print(report.render_text(res, top_n=args.top))
        print()

    # HTML 报告
    if args.html:
        html_parts = []
        for rdir, dim_name, res, _dim_dir in results:
            html_parts.append(report.render_html(res, top_n=args.top))
        with open(args.html, "w", encoding="utf-8") as f:
            f.write("\n".join(html_parts))
        print("已输出 HTML: %s" % args.html)

    # HTML 交互地图（单个维度 → 指定路径；--dim all 多维度 → 每维度一个子目录 + 左上角切换按钮）
    if args.map:
        player = (args.player[0], 0.0, args.player[1], "minecraft:overworld") if args.player else None
        if len(results) > 1:
            base = os.path.splitext(args.map)[0]
            items = [(dim_name, os.path.join(base, dim_name))
                     for _rdir, dim_name, _res, _d in results]
            for (_rdir, _dim_name, res, _d), (_n, out_dir) in zip(results, items):
                os.makedirs(out_dir, exist_ok=True)
                print(render_map_for(world_dir, res, os.path.join(out_dir, "map.html"),
                                     simdist=args.simdist, player=player, top_n=args.top,
                                     scoped=args.scoped, nav=dim_nav(items, out_dir)))
            print("已输出 HTML 交互地图: %s（每维度一份，页面左上角可切换）" % base)
        else:
            res = results[0][2]
            print(render_map_for(world_dir, res, args.map, simdist=args.simdist,
                                 player=player, top_n=args.top, scoped=args.scoped))
            print("已输出 HTML 交互地图: %s" % args.map)

    return 0


if __name__ == "__main__":
    sys.exit(main())
