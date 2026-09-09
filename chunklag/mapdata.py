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
