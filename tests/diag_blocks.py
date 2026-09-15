# -*- coding: utf-8 -*-
"""
诊断：某个区块的方块构成 —— 用来看「红石元件计数」是否可信。

背景（2026-09-15 踩坑）：红石元件是按**个数**计分的，个数必须解码 section 的位压缩
`block_states.data`。当时按「紧凑跨 long」解，位错位导致索引随机命中，把 3.2 万个方块
虚报成 32909 个侦测器（全图虚高到 67 万）。本工具用**两种打包方式对照解码** + 越界统计
来判断哪种自洽：

    python tests/diag_blocks.py <存档目录> <区块X> <区块Z>

自洽判据：越界索引应当为 0（解出的索引不能超出 palette 长度），且构成要像真实地形
（石头/空气/泥土为主）。MC 实际用的是 **padded**（每 long 装 64//bits 个，entry 不跨边界）。
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chunklag import factors, region  # noqa: E402


def decode(bs, mode):
    """按指定打包方式解出 palette 索引频次（mode: padded / compact）。"""
    pal, data = bs["palette"], bs["data"]
    bits = max(4, (len(pal) - 1).bit_length())
    mask = (1 << bits) - 1
    per = 64 // bits
    counts = collections.Counter()
    if mode == "padded":
        for li, v in enumerate(data):
            u = v & 0xFFFFFFFFFFFFFFFF
            for k in range(per):
                pos = li * per + k
                if pos >= 4096:
                    break
                counts[(u >> (k * bits)) & mask] += 1
    else:
        stream = 0
        for i, v in enumerate(data):
            stream |= (v & 0xFFFFFFFFFFFFFFFF) << (64 * i)
        for i in range(4096):
            counts[(stream >> (i * bits)) & mask] += 1
    return counts


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    world = argv[1]
    if argv[2] == "all":                      # 全图按方块名汇总（判断是哪类元件主导）
        total = collections.Counter()
        chunks = 0
        for x, z, nbt_data in region.scan_region_dir(os.path.join(world, "region")):
            chunks += 1
            level = nbt_data.get("Level") if isinstance(nbt_data.get("Level"), dict) else nbt_data
            for sec in level.get("sections") or []:
                bs = sec.get("block_states") or {}
                pal = bs.get("palette")
                if not isinstance(pal, list):
                    continue
                wanted = {i: p.get("Name") for i, p in enumerate(pal)
                          if isinstance(p, dict)
                          and factors._is_redstone_block_name(p.get("Name"))}
                if not wanted:
                    continue
                counts = factors._section_index_counts(bs)
                for i, name in wanted.items():
                    if counts.get(i):
                        total[name] += counts[i]
        print("主世界 %d 个区块 · 红石元件方块合计 %d" % (chunks, sum(total.values())))
        for name, n in total.most_common(20):
            print("   %-42s %d" % (name, n))
        return 0
    cx, cz = int(argv[2]), int(argv[3])
    found = False
    for x, z, nbt_data in region.scan_region_dir(os.path.join(world, "region")):
        if (x, z) != (cx, cz):
            continue
        found = True
        level = nbt_data.get("Level") if isinstance(nbt_data.get("Level"), dict) else nbt_data
        total = collections.Counter()
        for sec in level.get("sections") or []:
            bs = sec.get("block_states") or {}
            pal = bs.get("palette")
            if not isinstance(pal, list) or not isinstance(bs.get("data"), list):
                continue
            wanted = {i for i, p in enumerate(pal)
                      if isinstance(p, dict) and factors._is_redstone_block_name(p.get("Name"))}
            if not wanted:
                continue
            bits = max(4, (len(pal) - 1).bit_length())
            print("=== Y=%s palette=%d bits=%d data=%d 红石索引=%s"
                  % (sec.get("Y"), len(pal), bits, len(bs["data"]), sorted(wanted)))
            for mode in ("padded", "compact"):
                cnt = decode(bs, mode)
                over = sum(v for k, v in cnt.items() if k >= len(pal))
                mark = "OK " if over == 0 else "越界!"
                print("  [%s] %s 越界=%d 总=%d 种类=%d"
                      % (mode, mark, over, sum(cnt.values()), len(cnt)))
                for idx, n in cnt.most_common(5):
                    name = pal[idx].get("Name") if idx < len(pal) else "<越界>"
                    print("     idx=%-3s %-42s %d" % (idx, name, n))
            for i in wanted:
                total[pal[i].get("Name")] += factors._section_index_counts(bs).get(i, 0)
        print("---- (%d,%d) 红石元件合计 %d" % (cx, cz, sum(total.values())))
        for name, n in total.most_common(10):
            print("   %-42s %d" % (name, n))
    if not found:
        print("该 region 文件里没找到区块 (%d,%d)" % (cx, cz))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
