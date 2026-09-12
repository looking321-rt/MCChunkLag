# -*- coding: utf-8 -*-
"""
临时诊断：扫所有存档里含 nether_portal 的区块，看它们到底有没有红石装置。
用法: python tests/diag_portal.py <saves目录>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chunklag import leveldat, region, factors
from scan import discover_worlds


def diag_world(wdir):
    name, _dv = leveldat.describe_world(wdir)
    hits = []
    for sub, dim in (("", "主世界"), ("DIM-1", "下界"), ("DIM1", "末地")):
        rdir = os.path.join(wdir, sub, "region") if sub else os.path.join(wdir, "region")
        if not os.path.isdir(rdir):
            continue
        for cx, cz, nbt in region.scan_region_dir(rdir):
            if not factors.has_portal(nbt):
                continue
            names = factors._chunk_block_names(nbt)
            lvl = nbt.get("Level") if isinstance(nbt.get("Level"), dict) else nbt
            bes = [factors._norm_id(b.get("id")) for b in factors._extract_block_entities(lvl)]
            rb = sorted(n for n in names if n in factors._REDSTONE_BLOCKS)
            rbe = sorted(set(b for b in bes if b in {r.replace("minecraft:", "") for r in factors._REDSTONE_BE_IDS}))
            hits.append({
                "dim": dim, "c": (cx, cz),
                "obsidian": "minecraft:obsidian" in names,
                "redstone_blocks": rb, "redstone_be": rbe,
                "all_be": sorted(set(bes)),
                "n_blocks": len(names),
            })
    return name, hits


def main():
    root = sys.argv[1]
    worlds = discover_worlds(root)
    for wdir in worlds:
        try:
            name, hits = diag_world(wdir)
        except Exception as exc:
            print("!! %s 分析失败: %s" % (wdir, exc))
            continue
        if not hits:
            continue
        print("=" * 70)
        print("世界: %s  (%s)" % (name, wdir))
        for h in hits:
            has_rs = bool(h["redstone_blocks"] or h["redstone_be"])
            print("  [%s] 区块%s 黑曜石=%s 红石装置=%s" % (
                h["dim"], h["c"], h["obsidian"], "有" if has_rs else "无"))
            print("      红石方块: %s" % (h["redstone_blocks"] or "-"))
            print("      红石方块实体: %s" % (h["redstone_be"] or "-"))
            print("      全部方块实体: %s" % (h["all_be"] or "-"))
        print("  合计 %d 个门区块，其中带红石 %d，纯门 %d" % (
            len(hits), sum(1 for h in hits if h["redstone_blocks"] or h["redstone_be"]),
            sum(1 for h in hits if not (h["redstone_blocks"] or h["redstone_be"]))))


if __name__ == "__main__":
    main()
