# -*- coding: utf-8 -*-
"""
探针：新版存档布局（26.x：dimensions/<ns>/<dim>/ + players/data/）的加载器证据扫描。

用途：验证 1.21.2+ 「矿车地狱门加载器 / 末影珍珠加载器」在离线存档里的可读证据。
用法: python tests/diag_new_layout.py <存档目录>
"""
import collections
import gzip
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from chunklag import leveldat, region, nbt

KEY_BLOCKS = ("nether_portal", "obsidian", "powered_rail", "detector_rail",
              "activator_rail", "rail", "soul_sand", "magma_block", "end_gateway")


def dim_dirs(world):
    """返回 [(标签, 维度目录)]，兼容新布局(dimensions/)与旧布局(region/ + DIM-1)。"""
    out = []
    base = os.path.join(world, "dimensions")
    if os.path.isdir(base):
        for ns in sorted(os.listdir(base)):
            nsp = os.path.join(base, ns)
            if not os.path.isdir(nsp):
                continue
            for dim in sorted(os.listdir(nsp)):
                p = os.path.join(nsp, dim)
                if os.path.isdir(p):
                    out.append(("%s:%s" % (ns, dim), p))
    for sub, name in (("", "旧:主世界"), ("DIM-1", "旧:下界"), ("DIM1", "旧:末地")):
        p = os.path.join(world, sub) if sub else world
        if os.path.isdir(os.path.join(p, "region")):
            out.append((name, p))
    return out


def palette_entries(sec):
    bs = sec.get("block_states")
    pal = bs.get("palette") if isinstance(bs, dict) else None
    if not isinstance(pal, list):
        pal = sec.get("palette")
    return pal or []


def scan_blocks(label, dim_dir):
    rdir = os.path.join(dim_dir, "region")
    if not os.path.isdir(rdir):
        return
    total = 0
    hits = []
    for cx, cz, chunk in region.scan_region_dir(rdir):
        total += 1
        lvl = chunk.get("Level") if isinstance(chunk.get("Level"), dict) else chunk
        names = {}
        for sec in lvl.get("sections") or []:
            for p in palette_entries(sec):
                if isinstance(p, dict):
                    props = p.get("Properties") or {}
                    names[str(p.get("Name"))] = props
        hit = {k: v for k, v in names.items() if any(b in k for b in KEY_BLOCKS)}
        if hit:
            hits.append((cx, cz, hit))
    print("  [%s] 区块 %d 个；含关键方块的区块 %d 个" % (label, total, len(hits)))
    for cx, cz, hit in hits[:40]:
        print("      (%d,%d) %s" % (cx, cz, hit))


def scan_entities(label, dim_dir):
    edir = os.path.join(dim_dir, "entities")
    if not os.path.isdir(edir):
        return
    ids = collections.Counter()
    interesting = []
    for cx, cz, ch in region.scan_region_dir(edir):
        lvl = ch.get("Level") if isinstance(ch.get("Level"), dict) else ch
        ents = lvl.get("Entities")
        if not isinstance(ents, list):
            ents = lvl.get("entities")
        if not isinstance(ents, list):
            continue
        for e in ents:
            if not isinstance(e, dict):
                continue
            eid = str(e.get("id"))
            ids[eid] += 1
            if "minecart" in eid or "ender_pearl" in eid or eid.endswith(":item"):
                interesting.append((cx, cz, eid, e.get("Pos")))
    if ids:
        print("  [%s] 实体: %s" % (label, dict(ids)))
    elif os.path.isdir(edir):
        print("  [%s] entities 目录存在但无实体" % label)
    for cx, cz, eid, pos in interesting:
        print("      (%d,%d) %s Pos=%s" % (cx, cz, eid, pos))


def scan_players(world):
    for sub in (os.path.join("players", "data"), "playerdata"):
        pdir = os.path.join(world, sub)
        if not os.path.isdir(pdir):
            continue
        print(" 玩家数据目录: %s" % pdir)
        for name in sorted(os.listdir(pdir)):
            p = os.path.join(pdir, name)
            if not os.path.isfile(p):
                continue
            try:
                raw = open(p, "rb").read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                top = nbt.parse_nbt(raw)
            except Exception as exc:
                print("   %s 解析失败: %s" % (name, exc))
                continue
            keys = sorted(top.keys()) if isinstance(top, dict) else []
            print("   %s 顶层键: %s" % (name, keys))
            if isinstance(top, dict):
                for k, v in top.items():
                    if "pearl" in k.lower():
                        print("      >>> %s = %r" % (k, v))
        return
    print(" 找不到玩家数据目录（players/data 与 playerdata 都没有）")


def main():
    world = sys.argv[1]
    print("=" * 78)
    print("存档: %s" % world)
    name, dv = leveldat.describe_world(world)
    print("世界名=%s  DataVersion=%s" % (name, dv))
    ld = os.path.join(world, "level.dat")
    try:
        data, _ = leveldat.parse_level_dat(ld)
        print("level.dat 顶层键: %s" % sorted(data.keys())[:40])
        d = data.get("Data", data)
        print("Data 键(部分): %s" % sorted(d.keys())[:40])
    except Exception as exc:
        print("level.dat 解析失败: %s" % exc)
    scan_players(world)
    for label, d in dim_dirs(world):
        print("-" * 70)
        print("维度 %s -> %s" % (label, d))
        scan_blocks(label, d)
        scan_entities(label, d)


if __name__ == "__main__":
    main()
