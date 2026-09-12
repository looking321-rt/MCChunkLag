# -*- coding: utf-8 -*-
"""
临时诊断：列出存档里的地狱门装置，并逐装置给出「跨维度成对判定」的中间值。

判据 = 主世界/下界两侧都有地狱门 + 两侧都有红石装置才标注常加载（见 loaders.portal_loaders）。
用法: python tests/diag_portal.py <saves目录|单个世界目录>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chunklag import leveldat, region, factors, loaders
from scan import discover_worlds

_REDSTONE_BE = {r.replace("minecraft:", "") for r in factors._REDSTONE_BE_IDS}


def scan_portals(rdir):
    """轻量扫描某维度 region：返回 (门区块集, 门+红石区块集, 每区块红石证据)。"""
    portal, armed, evidence = set(), set(), {}
    if not os.path.isdir(rdir):
        return portal, armed, evidence
    for cx, cz, nbt in region.scan_region_dir(rdir):
        if not factors.has_portal(nbt):
            continue
        portal.add((cx, cz))
        names = factors._chunk_block_names(nbt)
        lvl = nbt.get("Level") if isinstance(nbt.get("Level"), dict) else nbt
        bes = [factors._norm_id(b.get("id")) for b in factors._extract_block_entities(lvl)]
        blocks = sorted(n.replace("minecraft:", "") for n in names if n in factors._REDSTONE_BLOCKS)
        bes_hit = sorted({b for b in bes if b in _REDSTONE_BE})
        evidence[(cx, cz)] = blocks + ["<BE>" + b for b in bes_hit]
        if blocks or bes_hit:
            armed.add((cx, cz))
    return portal, armed, evidence


def report_side(label, mine, mate, is_overworld, mate_label):
    portal, armed, evidence = mine
    m_portal, m_armed, _ = mate
    if not portal:
        return
    tol = loaders.PORTAL_MATE_TOLERANCE
    kept = loaders.portal_loaders(portal, armed, m_portal, m_armed, is_overworld)
    mate_devices = [(loaders.nether_chunks(d, not is_overworld), d)
                    for d in loaders.cluster_chunks(m_portal)]
    for comp in sorted(loaders.cluster_chunks(portal), key=lambda c: sorted(c)[0]):
        cx, cz = loaders.cluster_center(comp)
        mine_n = loaders.nether_chunks(comp, is_overworld)
        gaps = [(loaders._chebyshev_gap(mine_n, mn), md) for mn, md in mate_devices]
        gaps = [(g, d) for g, d in gaps if g is not None]
        near = min(gaps)[0] if gaps else None
        mate_wired = any(g <= tol and (d & m_armed) for g, d in gaps)
        ev = sorted({e for c in comp for e in evidence.get(c, [])})
        if comp <= kept:
            verdict, why = "算常加载装置", ""
        else:
            verdict = "不算"
            if not (comp & armed):
                why = "（本侧无红石装置）"
            elif near is None:
                why = "（%s 侧没有地狱门）" % mate_label
            elif near > tol:
                why = "（%s 侧最近门装置距离 %d 区块 > 容差 %d）" % (mate_label, near, tol)
            else:
                why = "（配对侧无红石装置）"
        print("  [%s] 装置@%s 区块%d 红石=%s → %s侧%s → %s%s"
              % (label, (cx, cz), len(comp), ",".join(ev) if ev else "无",
                 mate_label,
                 "无门装置" if near is None else "最近门装置距离%d 红石=%s" % (near, "有" if mate_wired else "无"),
                 verdict, why))


def main():
    for wdir in discover_worlds(sys.argv[1]):
        try:
            name, _dv = leveldat.describe_world(wdir)
            ow = scan_portals(os.path.join(wdir, "region"))
            nw = scan_portals(os.path.join(wdir, "DIM-1", "region"))
        except Exception as exc:
            print("!! %s 分析失败: %s" % (wdir, exc))
            continue
        if not ow[0] and not nw[0]:
            continue
        print("=" * 70)
        print("世界: %s  (%s)" % (name, wdir))
        report_side("主世界", ow, nw, True, "下界")
        report_side("下界", nw, ow, False, "主世界")
        print("  主世界门区块 %d / 下界门区块 %d" % (len(ow[0]), len(nw[0])))


if __name__ == "__main__":
    main()
