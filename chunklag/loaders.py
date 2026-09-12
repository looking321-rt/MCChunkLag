# -*- coding: utf-8 -*-
"""
常加载区块识别 —— 找出"非玩家也能保持加载"的区块（出生点/forceload/mod），
用于与玩家模拟区并集，补全卡顿热力图（MC 加载机制见 Minecraft Wiki）。

来源：
  - 出生点 spawn chunks：level.dat 的 SpawnX/Z + 游戏规则 spawnChunkRadius(默认2)
  - /forceload：level.dat 的 ForcedChunks（vanilla 强制加载，负载等级31）
  - mod 强制加载：data/chunks.dat(FTB) 的 ForgeForced[*].ModForced[*].Blocks(方块坐标)
"""
import gzip
import math
import os

from . import nbt
from . import leveldat


def _world_chunk(x, z):
    return (int(math.floor(x / 16)), int(math.floor(z / 16)))


def read_spawn_chunks(world_dir, radius=2):
    """出生点恒加载区：出生点区块 + 半径 radius（spawnChunkRadius，默认2）。"""
    ldat = os.path.join(world_dir, "level.dat")
    if not os.path.exists(ldat):
        return set()
    data, _dv = leveldat.parse_level_dat(ldat)
    if not data:
        return set()
    sx, sz = data.get("SpawnX"), data.get("SpawnZ")
    if sx is None or sz is None:
        return set()
    cx, cz = _world_chunk(sx, sz)
    return {(cx + dx, cz + dz) for dx in range(-radius, radius + 1)
            for dz in range(-radius, radius + 1)}


def read_forced_chunks(world_dir):
    """vanilla /forceload 强制加载区块（level.dat 的 ForcedChunks）。"""
    ldat = os.path.join(world_dir, "level.dat")
    if not os.path.exists(ldat):
        return set()
    data, _dv = leveldat.parse_level_dat(ldat)
    if not data:
        return set()
    fc = data.get("ForcedChunks")
    if not isinstance(fc, list) or len(fc) < 2:
        return set()
    # 成对 [x,z,x,z,...]
    return {(fc[i], fc[i + 1]) for i in range(0, len(fc) - 1, 2)}


def _mod_blocks_to_chunks(blocks):
    """把 mod 强制加载的 Blocks(方块坐标列表) 转成区块集合。"""
    chunks = set()
    for b in blocks:
        if isinstance(b, dict) and "X" in b and "Z" in b:
            chunks.add(_world_chunk(b["X"], b["Z"]))
    return chunks


def _decode_chunk(v):
    """FTB chunks.dat 的 Chunk long 编码 = (z<<32)+x（低32位=x有符号，高32位=z）。"""
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    low = v & 0xFFFFFFFF
    x = low if low < 2**31 else low - 2**32
    return (x, v >> 32)


def _expand(chunks, r):
    """把一组中心区块扩展为周边 r 的方块区域（常加载会带动相邻区块）。"""
    return {(cx + dx, cz + dz) for cx, cz in chunks
            for dx in range(-r, r + 1) for dz in range(-r, r + 1)}


def cluster_chunks(chunks):
    """把相邻(切比雪夫距离≤1)的区块聚成连通分量，每个分量=一个传送门装置。"""
    remaining = set(chunks)
    clusters = []
    while remaining:
        seed = remaining.pop()
        comp = {seed}
        stack = [seed]
        while stack:
            cx, cz = stack.pop()
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    n = (cx + dx, cz + dz)
                    if n in remaining:
                        remaining.discard(n)
                        comp.add(n)
                        stack.append(n)
        clusters.append(comp)
    return clusters


def cluster_center(comp):
    """取连通分量的中心区块（质心四舍五入）。"""
    xs = [c[0] for c in comp]
    zs = [c[1] for c in comp]
    return (int(round(sum(xs) / len(xs))), int(round(sum(zs) / len(zs))))


def portal_region(portal_chunks, radius=1):
    """
    传送门常加载区：把识别到的区块**聚类成若干「地狱门装置」**，再以每个装置中心
    按游戏机制向外扩展 radius 圈。

    Wiki：实体穿过传送门 → 对面板区块 + 周围 8 区块（3×3）。默认 radius=1 即 3×3。
    （不是对每个识别区块各自扩，避免相邻装置把范围膨胀）
    """
    out = set()
    for comp in cluster_chunks(portal_chunks):
        cx, cz = cluster_center(comp)
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                out.add((cx + dx, cz + dz))
    return out


def portal_regions(portal_chunks):
    """
    返回 **每个地狱门装置一个 region**：[(type, label, chunks), ...]。

    只保留最外层「整体层 7×7」（它已含 3×3 实体层与 5×5 红石层）——三层都画会
    糊成一片看不清；7×7 即传送门区块加载器的影响范围。

    portal_chunks 应已由 `portal_loaders` 做过成对判定，这里只负责画框。
    """
    out = []
    for comp in cluster_chunks(portal_chunks):
        cx, cz = cluster_center(comp)
        s = {(cx + dx, cz + dz) for dx in range(-3, 3 + 1)
             for dz in range(-3, 3 + 1)}
        out.append(("portal", "传送门常加载区", s))
    return out


def to_mate_chunks(chunks, from_overworld):
    """
    把一侧维度的区块集合映射到对面维度：主世界→下界 1/8（÷8），下界→主世界 ×8。
    （主世界 1 区块 16 方块 = 下界 2 方块，故 8 个主世界区块对应 1 个下界区块）
    """
    if from_overworld:
        return {(cx // 8, cz // 8) for cx, cz in chunks}
    return {(8 * cx + dx, 8 * cz + dz)
            for cx, cz in chunks for dx in range(8) for dz in range(8)}


def portal_loaders(portal_chunks, armed_chunks, mate_portal, mate_armed, is_overworld):
    """
    返回**算「地狱门常加载装置」的门区块集合**（装置级判定，2026-09-12 用户拍板）。

    判据：地狱门常加载靠实体在主世界/下界之间循环，所以必须
    **两侧都装了地狱门、且两侧都有红石装置**；只在一侧识别到的一律不标注
    （单个地狱门与普通方块无异，不对面加载）。

    - portal_chunks / armed_chunks：本维度 含门 / 含门+红石 的区块
    - mate_portal / mate_armed：对面维度（主世界↔下界）的两类区块
    - is_overworld：本维度是否主世界（决定坐标缩放方向）

    装置级语义：先聚类成装置，整装置一起通过/一起否掉；对面只要有一个配对装置
    带红石，本装置就算加载器。
    """
    armed = set(armed_chunks)
    mate_armed = set(mate_armed)
    mate_devices = cluster_chunks(mate_portal)
    out = set()
    for comp in cluster_chunks(portal_chunks):
        if not (comp & armed):
            continue                      # 本侧无红石装置 → 不是实体循环加载器
        mapped = to_mate_chunks(comp, is_overworld)
        for m_comp in mate_devices:
            if (mapped & m_comp) and (m_comp & mate_armed):
                out |= comp               # 对面有配对门装置且有红石 → 整个装置算加载器
                break
    return out


def merge_region_boxes(regions):
    """
    把**相互重叠**的同类型 region 合并成一个外接框：两个框交叠时只留外围线条，
    不再各画一圈让内部线条交叉糊在一起。不相交的保持独立。
    """
    boxes = []
    for typ, label, chunks in regions:
        if not chunks:
            continue
        xs = [c[0] for c in chunks]
        zs = [c[1] for c in chunks]
        boxes.append([typ, label, min(xs), max(xs), min(zs), max(zs), set(chunks)])

    changed = True
    while changed:
        changed = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if a[0] != b[0]:
                    continue
                # 任一轴不重叠 → 不相交（闭区间：贴边重叠 1 格也算相交）
                if a[2] > b[3] or b[2] > a[3] or a[4] > b[5] or b[4] > a[5]:
                    continue
                boxes[i] = [a[0], a[1], min(a[2], b[2]), max(a[3], b[3]),
                            min(a[4], b[4]), max(a[5], b[5]), a[6] | b[6]]
                boxes.pop(j)
                changed = True
                break
            if changed:
                break

    out = []
    for typ, label, mnx, mxx, mnz, mxz, _chunks in boxes:
        out.append((typ, label, {(x, z) for x in range(mnx, mxx + 1)
                                 for z in range(mnz, mxz + 1)}))
    return out


def read_mod_forced(world_dir, expand=1):
    """
    读取 mod 强制加载区块（FTB chunks.dat 的 ForgeForced）。
    每个 mod 的常加载来源 = Blocks(方块坐标) + Chunk(long 编码)，再扩展周边 expand。
    返回 [(mod名, 区块集合), ...]。
    """
    path = os.path.join(world_dir, "data", "chunks.dat")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "rb") as f:
            raw = f.read()
        raw = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
        top = nbt.parse_nbt(raw)
    except Exception:
        return []
    data = top.get("data", top) if isinstance(top, dict) else {}
    forge = data.get("ForgeForced") or data.get("Forced") or []
    results = []
    for entry in forge:
        if not isinstance(entry, dict):
            continue
        mod = entry.get("Mod", "未知mod")
        mod_forced = entry.get("ModForced") or []
        chunks = set()
        for mf in mod_forced:
            if not isinstance(mf, dict):
                continue
            blocks = mf.get("Blocks")
            if isinstance(blocks, list):
                chunks |= _mod_blocks_to_chunks(blocks)
            ck = _decode_chunk(mf.get("Chunk")) if mf.get("Chunk") is not None else None
            if ck:
                chunks.add(ck)
        if expand > 0 and chunks:
            chunks = _expand(chunks, expand)
        if chunks:
            results.append((mod, chunks))
    return results


def collect_regions(world_dir, spawn_radius=2):
    """汇总所有常加载区，返回 [(type, label, 区块集合), ...]。"""
    regions = []
    spawn = read_spawn_chunks(world_dir, spawn_radius)
    if spawn:
        regions.append(("spawn", "出生点恒加载区", spawn))
    forced = read_forced_chunks(world_dir)
    if forced:
        regions.append(("forced", "/forceload", forced))
    for mod, chunks in read_mod_forced(world_dir):
        regions.append(("mod", "mod常加载:" + mod, chunks))
    return regions
