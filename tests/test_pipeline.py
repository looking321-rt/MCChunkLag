# -*- coding: utf-8 -*-
"""
自测：验证 NBT/region/leveldat 解析 + 完整分析链路 + 报告渲染。

运行（在项目根）：
  python tests/test_pipeline.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from chunklag import nbt, region, leveldat
from make_fixture import build, FAKE_WORLD

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS] %s" % name)
    else:
        FAIL += 1
        print("[FAIL] %s  %s" % (name, detail))


def test_nbt():
    mf = __import__("make_fixture")
    d = nbt.parse_nbt(mf.enc_compound(mf.C({"a": mf.I(1), "b": mf.S("hello")})))
    check("nbt 回读 compound 顶层", d.get("a") == 1 and d.get("b") == "hello", str(d))


def test_leveldat():
    name, dv = leveldat.describe_world(FAKE_WORLD)
    check("level.dat 世界名", name == "测试MC存档", name)
    check("level.dat DataVersion", dv == 3465, str(dv))


def test_region():
    region_dir = os.path.join(FAKE_WORLD, "region")
    chunks = list(region.scan_region_dir(region_dir))
    check("region 扫到 3 个区块", len(chunks) == 3, str(len(chunks)))
    # 验证区块坐标
    coords = sorted((cx, cz) for cx, cz, _ in chunks)
    check("区块坐标正确", coords == [(0, 0), (0, 1), (1, 0)], str(coords))


def test_analysis():
    import main as main_mod
    results = main_mod.analyze_world(FAKE_WORLD, "0")
    check("只分析主世界 1 个维度", len(results) == 1, str(len(results)))
    res = results[0][2]
    check("总区块数=3", res.total_chunks == 3, str(res.total_chunks))
    check("敌对怪物总数=6", res.factor_stats['entities_hostile'].count == 6,
          str(res.factor_stats['entities_hostile'].count))
    check("刷怪笼=1", res.factor_stats['be_spawner'].count == 1)
    check("村民=1", res.factor_stats['entities_villager'].count == 1)
    check("容器=1", res.factor_stats['be_container'].count == 1)

    # TOP 榜：最卡的是 (1,0)
    top = res.top_chunks[0]
    check("最卡区块=(1,0)", top[0] == 1 and top[1] == 0, str(top[:2]))
    check("最卡区块评分=19", top[2] == 19, str(top[2]))

    # 下钻：敌对怪物集中在 (1,0)
    hs = res.factor_stats['entities_hostile']
    top_hs = sorted(hs.chunks, key=lambda c: c[2], reverse=True)[0]
    check("敌对怪物最集中=(1,0)x5", top_hs[0] == 1 and top_hs[1] == 0 and top_hs[2] == 5,
          str(top_hs))


def test_report():
    import main as main_mod
    from chunklag import report as report_mod
    res = main_mod.analyze_world(FAKE_WORLD, "0")[0][2]
    text = report_mod.render_text(res)
    html = report_mod.render_html(res)
    check("终端报告非空", len(text) > 50, str(len(text)))
    check("HTML 报告含 table", "<table" in html, "")
    # 找个极简世界（无因子）也应不崩
    empty = main_mod.analyze_world(FAKE_WORLD, "-1")  # 下界 region 不存在 → 空
    check("空维度分析不崩", empty == [], str(empty))


def test_map():
    import main as main_mod
    from chunklag import mapdata, mapview
    res = main_mod.analyze_world(FAKE_WORLD, "0")[0][2]
    d = mapdata.build_map_data(res, top_n=5)
    check("地图 bounds 正确",
          d["bounds"]["minX"] == 0 and d["bounds"]["maxX"] == 1
          and d["bounds"]["minZ"] == 0 and d["bounds"]["maxZ"] == 1,
          str(d["bounds"]))
    check("地图 total=3", d["total"] == 3, str(d["total"]))
    check("地图 top 首个=(1,0)",
          d["top"][0]["x"] == 1 and d["top"][0]["z"] == 0, str(d["top"][0]))
    out = os.path.join(ROOT, "tests", "map_test.html")
    mapview.render_html_map(res, out, top_n=5)
    txt = open(out, encoding="utf-8").read()
    check("HTML 地图含 canvas", "<canvas" in txt, "")
    check("HTML 地图含内嵌JSON", "application/json" in txt, "")


def test_render_map_with_portal():
    """回归：识别到传送门时 render_map_for 要能带上分层 region。

    曾因多传半径参数（portal_regions(portal, 1)）在真实存档上报
    TypeError: portal_regions() takes 1 positional argument but 2 were given。
    """
    import main as main_mod
    res = main_mod.analyze_world(FAKE_WORLD, "0")[0][2]
    res.portal_chunks = {(0, 0), (1, 0)}  # 模拟识别到传送门所在区块
    out = os.path.join(ROOT, "tests", "map_portal_test.html")
    # 传 player 才走「玩家模拟区 ∪ 常加载区」分支（合成存档 level.dat 里没有玩家位置）
    msg = main_mod.render_map_for(FAKE_WORLD, res, out, simdist=2,
                                  player=(8.0, 64.0, 8.0, "minecraft:overworld"), top_n=5)
    check("带传送门常加载区渲染不崩", os.path.exists(out), msg)
    txt = open(out, encoding="utf-8").read()
    check("传送门三层 region 已注入",
          "portal_core" in txt and "portal_red" in txt and "portal_lazy" in txt, "")
    check("常加载区已并入地图数据", "regions" in txt, "")


if __name__ == "__main__":
    build()
    test_nbt()
    test_leveldat()
    test_region()
    test_analysis()
    test_report()
    test_map()
    test_render_map_with_portal()
    print("\n===== 结果: %d 通过 / %d 失败 =====" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
