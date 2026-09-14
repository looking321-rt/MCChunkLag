# -*- coding: utf-8 -*-
"""
常加载区块识别 —— 找出"非玩家也能保持加载"的区块（出生点/forceload/mod），
用于在热力图上叠加常加载框（MC 加载机制见 Minecraft Wiki）。

来源：
  - 出生点 spawn chunks：level.dat 的 SpawnX/Z（或新版 spawn.pos）+ spawnChunkRadius(默认2)
    ⚠ **只有主世界有**这个机制 —— 下界/末地没有出生点常加载，见 collect_regions
  - /forceload：level.dat 的 ForcedChunks（vanilla 强制加载，负载等级31）
  - mod 强制加载：data/chunks.dat(FTB) 的 ForgeForced[*].ModForced[*].Blocks(方块坐标)
"""
import gzip
import math
import os

from . import nbt
from . import leveldat
from . import layout
from . import region


def _world_chunk(x, z):
    return (int(math.floor(x / 16)), int(math.floor(z / 16)))


def spawn_chunks_from(sp, radius=2):
    """由出生点 (x, y, z, dim_id) 展开出生点恒加载区块集合。"""
    cx, cz = _world_chunk(sp[0], sp[2])
    return {(cx + dx, cz + dz) for dx in range(-radius, radius + 1)
            for dz in range(-radius, radius + 1)}


def read_spawn_chunks(world_dir, radius=2):
    """
    出生点恒加载区：出生点区块 + 半径 radius（spawnChunkRadius，默认2）。

    兼容新布局（26.x 的 `spawn: {pos,dimension}`）与旧布局（`SpawnX/SpawnZ`），
    见 layout.spawn_position。

    ⚠ 这里**不带维度过滤**（纯几何展开，供诊断/回归用）；要不要画到某张图上，
    由 `collect_regions(dim_id=...)` 决定 —— 出生点恒加载是**主世界专有**机制。
    """
    from . import layout

    sp = layout.spawn_position(world_dir)
    if not sp:
        return set()
    return spawn_chunks_from(sp, radius)


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

    范围 = **5×5**（wiki：实体穿门后对面区块**完全加载 3×3**、外围 **16 个 lazy**，
    合计 5×5 —— 2026-09-12 按官方口径从早期的 7×7 修正）。

    portal_chunks 应已由 `portal_loaders` 做过成对判定，这里只负责画框。
    """
    out = []
    for comp in cluster_chunks(portal_chunks):
        cx, cz = cluster_center(comp)
        s = {(cx + dx, cz + dz) for dx in range(-2, 2 + 1)
             for dz in range(-2, 2 + 1)}
        out.append(("portal", "传送门常加载区", s))
    return out


def read_ender_pearls(world_dir):
    """
    读取玩家数据里的末影珍珠 —— 1.21.2+ 的「末影珍珠加载器」。

    实测（26.2 存档）：珍珠**不在** entities/*.mca，而是存在玩家数据的
    `ender_pearls` 列表里（每个元素是一份完整实体 NBT：Pos / ender_pearl_dimension /
    Owner …）—— 玩家登出时珍珠从世界移除、登入时按这份数据重新加载，所以它一点落地
    就会消失，能在存档里留下的珍珠**必然被静滞住（=正在持续加载区块）**。

    返回 [{"x", "y", "z", "dim", "chunk"}]（chunk = 珍珠所在区块）。
    """
    import math

    out = []
    for path in layout.player_data_files(world_dir):
        top = leveldat.parse_gzip_nbt(path)
        if not isinstance(top, dict):
            continue
        pearls = top.get("ender_pearls")
        if not isinstance(pearls, list):
            continue
        for p in pearls:
            if not isinstance(p, dict):
                continue
            pos = p.get("Pos")
            if not isinstance(pos, list) or len(pos) < 3:
                continue
            dim = p.get("ender_pearl_dimension") or p.get("Dimension") or "minecraft:overworld"
            out.append({
                "x": pos[0], "y": pos[1], "z": pos[2], "dim": dim,
                "chunk": (int(math.floor(pos[0] / 16)), int(math.floor(pos[2] / 16))),
            })
    return out


def pearl_regions(pearls):
    """
    末影珍珠加载区：**每颗珍珠一个 region**。

    范围 = **3×3**（wiki：珍珠完全加载它所在的 1 个区块 + 外围 8 个 lazy，
    比地狱门的 3×3 完全加载小）。
    """
    out = []
    for p in pearls:
        cx, cz = p["chunk"]
        s = {(cx + dx, cz + dz) for dx in range(-1, 1 + 1)
             for dz in range(-1, 1 + 1)}
        out.append(("pearl", "珍珠强加载区", s))
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


def nether_chunks(chunks, is_overworld):
    """把一侧维度的区块集合换算到「下界区块尺度」：主世界 ÷8，下界原样（配对比较用）。"""
    if is_overworld:
        return {(cx // 8, cz // 8) for cx, cz in chunks}
    return set(chunks)


def _chebyshev_gap(a, b):
    """两个区块集合的最小切比雪夫距离（重叠/相邻=0）；任一为空返回 None。"""
    if not a or not b:
        return None
    return min(max(abs(x1 - x2), abs(z1 - z2)) for x1, z1 in a for x2, z2 in b)


# 配对容差（下界区块，1 区块 = 16 方块）：门对不必精确落在 8:1 换算点上——
# 玩家进传送门时游戏会在目标点附近找/生成配对门（生存_2 实测偏差 1 区块）。
PORTAL_MATE_TOLERANCE = 8


# 矿车家族实体（1.21.2+ 矿车地狱门加载器里的"循环实体"）
MINECART_IDS = {
    "minecart", "chest_minecart", "hopper_minecart", "furnace_minecart",
    "tnt_minecart", "command_block_minecart", "spawner_minecart",
}


def read_minecart_chunks(entities_dir):
    """
    扫实体分区，返回**含矿车实体**的区块集合。

    矿车地狱门加载器（1.21.2+）的关键证据之一：矿车是持久实体，会被写进区块存档，
    所以离线存档能看到它（对比：末影珍珠存在玩家数据里，见 read_ender_pearls）。
    """
    out = set()
    if not os.path.isdir(entities_dir):
        return out
    for cx, cz, chunk in region.scan_region_dir(entities_dir):
        lvl = chunk.get("Level") if isinstance(chunk.get("Level"), dict) else chunk
        ents = lvl.get("Entities")
        if not isinstance(ents, list):
            ents = lvl.get("entities")
        for e in ents or []:
            if not isinstance(e, dict):
                continue
            eid = str(e.get("id", "")).lower().replace("minecraft:", "")
            if eid in MINECART_IDS:
                out.add((cx, cz))
                break
    return out


def loader_evidence(portal_chunks, redstone_chunks, rail_chunks, minecart_chunks, radius=1):
    """
    「门装置带加载器证据」的区块集合（装置级判定）。证据满足**任一**即可：

      a) 装置邻域内有**红石器件** —— 物品循环式加载器（老做法）
      b) 装置邻域内**同时**有**已激活的动力铁轨**与**矿车实体** —— 矿车地狱门加载器
         （1.21.2+，用户 2026-09-12 指定：关键扫描 矿车 + 传送门方块 + 激活的动力铁轨）

    装置级语义：聚类后一块满足 → 整个装置计入（跨区块装置不被拆散）。
    """
    redstone_chunks = set(redstone_chunks)
    rail_chunks = set(rail_chunks)
    minecart_chunks = set(minecart_chunks)
    out = set()
    for comp in cluster_chunks(portal_chunks):
        near = _expand(comp, radius)
        if near & redstone_chunks:
            out |= comp
        elif (near & rail_chunks) and (near & minecart_chunks):
            out |= comp
    return out


def portal_loaders(portal_chunks, armed_chunks, mate_portal, mate_armed,
                   is_overworld, tolerance=PORTAL_MATE_TOLERANCE):
    """
    返回**算「地狱门常加载装置」的门区块集合**（装置级判定，2026-09-12 用户拍板）。

    判据：地狱门常加载靠实体在主世界/下界之间循环，所以必须
    **两侧都装了地狱门、且两侧都有红石装置**；只在一侧识别到的一律不标注
    （单个地狱门与普通方块无异，不对面加载）。

    - portal_chunks / armed_chunks：本维度 含门 / 含门+红石 的区块
    - mate_portal / mate_armed：对面维度（主世界↔下界）的两类区块
    - is_overworld：本维度是否主世界（决定坐标缩放方向）
    - tolerance：配对容差（下界区块），两侧装置换算到下界尺度后距离 ≤ 它即算配对

    装置级语义：先聚类成装置，整装置一起通过/一起否掉；对面只要有一个配对装置
    带红石，本装置就算加载器。
    """
    armed = set(armed_chunks)
    mate_armed = set(mate_armed)
    mate_devices = [(nether_chunks(comp, not is_overworld), comp)
                    for comp in cluster_chunks(mate_portal)]
    out = set()
    for comp in cluster_chunks(portal_chunks):
        if not (comp & armed):
            continue                      # 本侧无红石装置 → 不是实体循环加载器
        mine = nether_chunks(comp, is_overworld)
        for mate_n, mate_comp in mate_devices:
            gap = _chebyshev_gap(mine, mate_n)
            if gap is not None and gap <= tolerance and (mate_comp & mate_armed):
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


def collect_regions(world_dir, spawn_radius=2, dim_id="minecraft:overworld"):
    """
    汇总**指定维度**的常加载区，返回 [(type, label, 区块集合), ...]。

    维度语义（2026-09-14 修正，用户反馈「下界/末地怎么也有出生点常加载区块」）：
      · **出生点恒加载区只有主世界有** —— MC 的 spawn chunks 是主世界专有机制，
        下界/末地**没有**（末地只有出生平台/返回门，那都不是常加载）。判断依据是
        出生点自带的维度字段（新版 `spawn.dimension`；旧版 SpawnX/Z 无该字段 = 主世界）。
      · vanilla `/forceload` 的 ForcedChunks 记在 level.dat（主世界的表）→ 只画主世界。
      · mod 常加载（FTB chunks.dat 的 ForgeForced）条目**不带维度信息** → 保守只画主世界，
        避免在下界/末地图上凭空冒出主世界坐标的框。
    """
    regions = []
    sp = layout.spawn_position(world_dir)
    if sp and (sp[3] or "minecraft:overworld") == dim_id:
        spawn = spawn_chunks_from(sp, spawn_radius)
        if spawn:
            regions.append(("spawn", "出生点恒加载区", spawn))
    if dim_id == "minecraft:overworld":
        forced = read_forced_chunks(world_dir)
        if forced:
            regions.append(("forced", "/forceload", forced))
        for mod, chunks in read_mod_forced(world_dir):
            regions.append(("mod", "mod常加载:" + mod, chunks))
    return regions
