# -*- coding: utf-8 -*-
"""
诊断：看某张地图 HTML 的色块分档与加载系数分布。

用途（2026-09-15 起）：色块阈值按本图非零区块的 p50/p75/p90 自适应 —— 换权重尺度后
若发现"全图顶格红"（阈值没跟上）或"看不到红块"（红档用了 p99），先用它确认阈值与分布是否匹配。

    python tests/diag_color.py output/01_世界/主世界/map.html
"""
import collections
import json
import os
import re
import sys


def load(path):
    html = open(path, encoding="utf-8").read()
    m = re.search(r'<script id="mapdata" type="application/json">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    path = argv[1]
    if os.path.isdir(path):
        path = os.path.join(path, "map.html")
    data = load(path)
    bands = data.get("bands") or [300, 600, 1200]
    b0, b1, b2 = bands

    def band(s):
        if s <= 0:
            return "灰(0)"
        if s <= b0:
            return "绿<=%d" % b0
        if s <= b1:
            return "黄<=%d" % b1
        if s <= b2:
            return "橘<=%d" % b2
        return "红>%d" % b2

    print("地图: %s" % path)
    print("区块 %s · 会被 tick %s · 阈值 p50/p75/p90 = %s"
          % (data.get("total"), data.get("loaded"), bands))
    dist = collections.Counter(band(c["s"]) for c in data["chunks"])
    print("色块分布: %s" % dict(dist))
    for name, arr in (("会被 tick", [c for c in data["chunks"] if c["l"] > 0]),
                      ("不会被 tick", [c for c in data["chunks"] if c["l"] == 0])):
        print("  %s(%d): %s" % (name, len(arr),
                                dict(collections.Counter(band(c["s"]) for c in arr))))
    nz = sorted(c["s"] for c in data["chunks"] if c["s"] > 0)
    if nz:
        print("非零区块 %d 个 · min=%d p50=%d p90=%d max=%d"
              % (len(nz), nz[0], nz[len(nz) // 2], nz[int(len(nz) * 0.9)], nz[-1]))
        print("分数频次 TOP8: %s" % collections.Counter(nz).most_common(8))
    print("TOP5(有效分): %s" % [(t["x"], t["z"], t["s"], t["e"]) for t in data["top"][:5]])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
