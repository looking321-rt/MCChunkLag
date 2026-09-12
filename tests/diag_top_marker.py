# -*- coding: utf-8 -*-
"""
诊断：TOP 红块是否溢出、盖住了零分（无卡顿）区块。

渲染逻辑（chunklag/mapview.py render()）：
  - 底图：每区块 1 像素离屏位图，按 scale 放大绘制，无平滑（imageSmoothingEnabled=false）
  - TOP 红块：fillStyle='rgba(255,60,60,.5)'，尺寸 = max(scale, 3) 像素，
    **左上角对齐区块左上角** sx(t.x), sy(t.z)
  - fit()：scale = clamp(min(窗口W/mapW, 窗口H/mapZ), 1.6, 80)

scale < 3 时红块比一个区块还大，且只往右/下溢出 → 视觉上"红块盖到旁边的无卡顿区块"。

用法: python tests/diag_top_marker.py <map.html>
"""
import json
import re
import sys

WINDOWS = [(1920, 1080), (1600, 900), (1366, 768), (2560, 1440)]


def load(path):
    txt = open(path, encoding="utf-8").read()
    m = re.search(r'<script id="mapdata" type="application/json">(.*?)</script>', txt, re.S)
    return json.loads(m.group(1))


def main():
    d = load(sys.argv[1])
    b = d["bounds"]
    mw = b["maxX"] - b["minX"] + 1
    mz = b["maxZ"] - b["minZ"] + 1
    score = {(c["x"], c["z"]): c["s"] for c in d["chunks"]}
    print("地图范围 %d×%d 区块，有数据区块 %d，q50=%d q90=%d"
          % (mw, mz, len(score), d.get("q50", 0), d.get("q90", 0)))
    print()
    for W, H in WINDOWS:
        s = max(min(W / mw, H / mz), 1.6)
        size = max(s, 3.0)
        print("窗口 %4dx%-4d → fit scale=%.2f px/区块；红块 %.1fpx = %.2f 个区块宽（溢出 %.2f 区块）"
              % (W, H, s, size, size / s, max(0.0, (size - s) / s)))

    print()
    print("TOP 区块与其邻居评分：")
    zero_right = zero_down = 0
    for t in d["top"]:
        x, z, sc = t["x"], t["z"], t["s"]
        r = score.get((x + 1, z), None)
        dn = score.get((x, z + 1), None)
        if r == 0:
            zero_right += 1
        if dn == 0:
            zero_down += 1
        print("  区块(%d,%d) 评分%-4d 右邻=%-5s 下邻=%-5s"
              % (x, z, sc, "无数据" if r is None else r, "无数据" if dn is None else dn))
    n = len(d["top"])
    print("→ TOP %d 个里，右邻为 0 分 %d 个、下邻为 0 分 %d 个（红块正是往右下溢出）"
          % (n, zero_right, zero_down))


if __name__ == "__main__":
    main()
