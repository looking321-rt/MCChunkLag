# -*- coding: utf-8 -*-
"""
诊断：真实存档里**实际出现**的实体 id / 方块实体 id / 会 tick 的方块，
对照当前 factors 分类，看哪些掉了没归类的兜底桶（entities_other / be_other）。

用途：加分类之前先用它核对"该加哪些"（避免凭印象加一堆没出现过的种类）。

用法：
    set PYTHONIOENCODING=utf-8
    python tests/diag_ids.py <存档目录>            # 默认主世界
    python tests/diag_ids.py <存档目录> --dim -1   # 下界（0/-1/1/all）
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chunklag import factors, layout, region          # noqa: E402
from chunklag.entitypart import EntityPartition       # noqa: E402

# 「会 tick 的方块」候选（非方块实体）：流体刻 / 火蔓延 / 随机刻生长 / 光照更新源…
# 这些目前**完全没计分**，用出现频次判断值不值得加因子。
_WATCH = (
    "water", "lava", "fire", "bubble_column", "nether_portal", "end_portal",
    "ice", "snow", "leaves", "vine", "kelp", "bamboo", "sugar_cane", "cactus",
    "crop", "wheat", "carrot", "potato", "beetroot", "sapling", "mushroom",
    "stem", "berry", "amethyst", "copper", "sculk", "campfire", "magma",
    "sponge", "chorus", "cocoa", "farmland", "cave_vines", "glow_lichen",
    "tnt", "detector_rail", "powered_rail", "rail",
)


def _want():
    if "--dim" in sys.argv:
        arg = sys.argv[sys.argv.index("--dim") + 1]
        if arg == "all":
            return None
        return {"0": "minecraft:overworld", "-1": "minecraft:the_nether",
                "1": "minecraft:the_end"}.get(arg, arg)
    return "minecraft:overworld"


def _level(nbt):
    lv = nbt.get("Level") if isinstance(nbt, dict) else None
    return lv if isinstance(lv, dict) else (nbt or {})


def _dump(title, counter, factor_of, other_key):
    print("\n== %s ==" % title)
    total = sum(counter.values())
    print("共 %d 个 / %d 种" % (total, len(counter)))
    rows = []
    for name, cnt in counter.most_common():
        k = factor_of(name)
        rows.append((cnt, name, k))
    for cnt, name, k in rows:
        mark = "  <<< 落到兜底" if k == other_key else ""
        print("  %-38s %8d  → %s%s" % (name, cnt, k, mark))
    miss = [(n, c) for c, n, k in rows if k == other_key]
    if miss:
        print("  ⚠️ 兜底桶里有 %d 种，合计 %d 个：%s"
              % (len(miss), sum(c for _n, c in miss), ", ".join(n for n, _c in miss)))


def main():
    world = sys.argv[1]
    want = _want()
    ents = collections.Counter()
    ent_factor = {}
    bes = collections.Counter()
    be_factor = {}
    blocks = collections.Counter()
    chunks = 0

    for dim_id, ddir in layout.dimension_dirs(world):
        if want and dim_id != want:
            continue
        print("[维度] %s (%s)" % (layout.dim_label(dim_id), dim_id))
        part = EntityPartition(ddir)
        if part.exists():
            for _cx, _cz, nbt in region.scan_region_dir(part.dir):
                for e in factors._extract_entities(nbt):
                    eid = factors._entity_id(e) or "<无 id>"
                    ents[eid] += 1
                    ent_factor[eid] = factors._entity_factor(e)
        for _cx, _cz, nbt in region.scan_region_dir(os.path.join(ddir, "region")):
            chunks += 1
            lv = _level(nbt)
            for e in factors._extract_entities(lv):
                eid = factors._entity_id(e) or "<无 id>"
                ents[eid] += 1
                ent_factor[eid] = factors._entity_factor(e)
            for b in factors._extract_block_entities(lv):
                bid = factors._norm_id(b.get("id")) or "<无 id>"
                bes[bid] += 1
                be_factor[bid] = factors._be_factor(b)
            for n, _p in factors._chunk_block_entries(nbt):
                blocks[n] += 1

    print("[区块] %d" % chunks)
    _dump("实体 id（来自 entities 分区 + 区块 NBT）", ents,
          lambda n: ent_factor.get(n, "?"), "entities_other")
    _dump("方块实体 id", bes, lambda n: be_factor.get(n, "?"), "be_other")

    print("\n== palette 里出现过的「会 tick 的方块」（含该方块的 section 数）==")
    hit = sorted(((c, n) for n, c in blocks.items()
                  if any(w in factors._norm_id(n) for w in _WATCH)), reverse=True)
    for c, n in hit:
        print("  %-38s %8d" % (n, c))
    if not hit:
        print("  （无）")


if __name__ == "__main__":
    main()
