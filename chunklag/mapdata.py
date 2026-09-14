# -*- coding: utf-8 -*-
"""
地图数据导出 —— 把分析结果整理成前端可渲染的 JSON 结构。

结构：
{
  "bounds": {"minX", "maxX", "minZ", "maxZ"},   # 区块坐标范围
  "q50": n, "q90": n,                            # 评分分位数（用于着色分档）
  "chunks": [{"x", "z", "s", "f": {factor_key:count}}],  # 每区块（f 只含非零）
  "top": [{"x", "z", "s"}],                      # 最卡 TOP 区块
}
"""
import types

from .factors import chunk_score


def build_map_data(result, top_n=20):
    entries = result.chunk_entries
    if not entries:
        return {"bounds": None, "q50": 0, "q90": 0, "chunks": [], "top": [],
                "total": 0}

    xs = [k[0] for k in entries]
    zs = [k[1] for k in entries]
    bounds = {"minX": min(xs), "maxX": max(xs), "minZ": min(zs), "maxZ": max(zs)}

    chunks = []
    scores = []
    for (x, z), counts in entries.items():
        s = chunk_score(counts)
        scores.append(s)
        f = {k: v for k, v in counts.items() if v > 0}
        chunks.append({"x": x, "z": z, "s": s, "f": f})

    scores.sort()
    q50 = scores[len(scores) // 2] if scores else 0
    q90 = scores[int(len(scores) * 0.9)] if scores else 999

    # TOP 榜只收有卡顿因子的区块：0 分区块上榜会被前端画成红块，
    # 看起来像"无卡顿区域被红色色块覆盖"（2026-09-12 用户反馈；并集地图里非零区块往往
    # 少于 top_n，旧逻辑会把一堆 0 分区块顶进榜）。
    # 同分时按区块坐标定序：并列区块很多（如多个 9 分）时，仅按分排序会因字典序不稳定
    # 让某次上榜的区块下次掉出榜，看起来像"热点随机消失"。
    ranked = [(k, v) for k, v in entries.items() if chunk_score(v) > 0]
    ranked.sort(key=lambda kv: (-chunk_score(kv[1]), kv[0][0], kv[0][1]))
    top = [{"x": x, "z": z, "s": chunk_score(c)} for (x, z), c in ranked[:top_n]]

    return {"bounds": bounds, "q50": q50, "q90": q90,
            "chunks": chunks, "top": top, "total": len(chunks)}


# 因子 key → 中文名（供前端 tooltip / 图例）
def factor_labels():
    from .factors import FACTOR_GROUPS
    labels = {}
    for _group, items in FACTOR_GROUPS:
        for key, label, _w in items:
            labels[key] = label
    return labels


def build_player_map(result, player_xyz, sim_dist, top_n=20):
    """
    生成『玩家已加载范围』的地图数据。

    MC Java 仿真距离 sim_dist：以玩家区块为中心、边长 (2*sim_dist+1) 的
    **正方形**区块区域（切比雪夫距离）内才被加载并 tick（真正卡顿来源）。
    只保留该范围内区块，其余丢弃；并带玩家标注信息。
    """
    import math
    px, _py, pz = player_xyz[0], player_xyz[1], player_xyz[2]
    pcx = int(math.floor(px / 16))
    pcz = int(math.floor(pz / 16))
    s = sim_dist

    entries = {k: v for k, v in result.chunk_entries.items()
               if abs(k[0] - pcx) <= s and abs(k[1] - pcz) <= s}
    mock = types.SimpleNamespace(chunk_entries=entries)
    data = build_map_data(mock, top_n=top_n)
    data["player"] = {
        "blockX": px, "blockZ": pz, "chunkX": pcx, "chunkZ": pcz,
        "sim_dist": s, "load_width": 2 * s + 1,
    }
    data["total_all"] = len(result.chunk_entries)
    return data


def build_union_map(result, player_xyz, sim_dist, regions, top_n=20, union_only=False):
    """
    「常加载区叠加」地图数据。

    **默认全量**（union_only=False，2026-09-14 起）：该维度所有区块都进图，
    regions（出生点/forceload/mod/传送门/珍珠）只作为**彩色外框**叠加。
    早期默认只画「玩家模拟区 ∪ 常加载区」，并集之外的区块在地图上**根本不存在** ——
    用户报「主世界 18,-58 一堆掉落物识别不出来」，实测那份图只有 441/3762 个区块，
    其它 88% 被裁掉了（同一次反馈还要求下界/末地先直接出全扫描图）。
    union_only=True 保留旧口径（CLI `--scoped`），用于"只看玩家加载区"的场景。

    - player_xyz：玩家位置 (x, y, z, dim)；**None = 该维度没有玩家**（比如玩家在主世界时看下界），
      此时不写 player 字段、不画玩家模拟区，但常加载框照画。
    - regions：[(type, label, 区块集合)]（出自 loaders.collect_regions）。
    """
    import math

    entries = dict(result.chunk_entries)
    data_player = None
    if player_xyz is not None:
        px, _py, pz = player_xyz[0], player_xyz[1], player_xyz[2]
        pcx = int(math.floor(px / 16))
        pcz = int(math.floor(pz / 16))
        s = sim_dist
        data_player = {
            "blockX": px, "blockZ": pz, "chunkX": pcx, "chunkZ": pcz,
            "sim_dist": s, "load_width": 2 * s + 1,
        }
        if union_only:
            union = {(pcx + dx, pcz + dz) for dx in range(-s, s + 1)
                     for dz in range(-s, s + 1)}
            for _t, _l, cs in regions:
                union |= set(cs)
            entries = {k: v for k, v in entries.items() if k in union}

    mock = types.SimpleNamespace(chunk_entries=entries)
    data = build_map_data(mock, top_n=top_n)
    if data_player:
        data["player"] = data_player
    data["regions"] = [{"type": t, "label": l, "chunks": sorted(cs)}
                       for t, l, cs in regions]
    data["total_all"] = len(result.chunk_entries)
    return data
