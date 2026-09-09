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

    ranked = sorted(entries.items(), key=lambda kv: chunk_score(kv[1]), reverse=True)
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


def build_union_map(result, player_xyz, sim_dist, regions, top_n=20):
    """
    三源并集地图：玩家模拟区 ∪ 常加载区(出生点/forceload/mod)。

    - 玩家模拟区：以玩家区块为中心、边长(2*sim_dist+1)正方形（MC 仿真距离）。
    - 常加载区：regions 为 [(type,label,区块集合)]（出自 loaders.collect_regions）。
    只显示并集内区块；regions 用于前端给"常加载区"加彩色边界/标记。
    """
    import math
    px, _py, pz = player_xyz[0], player_xyz[1], player_xyz[2]
    pcx = int(math.floor(px / 16))
    pcz = int(math.floor(pz / 16))
    s = sim_dist

    union = {(pcx + dx, pcz + dz) for dx in range(-s, s + 1)
             for dz in range(-s, s + 1)}
    for _t, _l, cs in regions:
        union |= set(cs)

    entries = {k: v for k, v in result.chunk_entries.items() if k in union}
    mock = types.SimpleNamespace(chunk_entries=entries)
    data = build_map_data(mock, top_n=top_n)
    data["player"] = {
        "blockX": px, "blockZ": pz, "chunkX": pcx, "chunkZ": pcz,
        "sim_dist": s, "load_width": 2 * s + 1,
    }
    data["regions"] = [{"type": t, "label": l, "chunks": sorted(cs)}
                       for t, l, cs in regions]
    data["total_all"] = len(result.chunk_entries)
    return data
