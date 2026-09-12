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


def test_portal_regions_and_merge():
    """传送门：范围 5×5（3×3 完全加载 + 16 lazy）；重叠的装置框合并成一个外接框。"""
    from chunklag import loaders
    rs = loaders.portal_regions({(0, 0)})
    check("传送门只出一层 region", len(rs) == 1, str(len(rs)))
    check("传送门范围为 5×5", len(rs[0][2]) == 25, str(len(rs[0][2])))

    two = loaders.portal_regions({(0, 0), (3, 0)})   # 两装置相隔 3 → 7×7 框重叠
    check("重叠前仍是 2 个 region", len(two) == 2, str(len(two)))
    merged = loaders.merge_region_boxes(two)
    check("重叠框合并为 1 个", len(merged) == 1, str(len(merged)))
    check("合并后覆盖两装置区块", {(0, 0), (3, 0)} <= merged[0][2], "")

    far = loaders.merge_region_boxes(loaders.portal_regions({(0, 0), (50, 50)}))
    check("不相交的框保持独立", len(far) == 2, str(len(far)))


def test_render_map_with_portal():
    """回归：识别到传送门时 render_map_for 要能带上常加载区 region。

    曾因多传半径参数（portal_regions(portal, 1)）在真实存档上报
    TypeError: portal_regions() takes 1 positional argument but 2 were given。
    """
    import main as main_mod
    res = main_mod.analyze_world(FAKE_WORLD, "0")[0][2]
    res.portal_loader_chunks = {(0, 0), (1, 0)}  # 模拟识别到成对的地狱门装置
    out = os.path.join(ROOT, "tests", "map_portal_test.html")
    # 传 player 才走「玩家模拟区 ∪ 常加载区」分支（合成存档 level.dat 里没有玩家位置）
    msg = main_mod.render_map_for(FAKE_WORLD, res, out, simdist=2,
                                  player=(8.0, 64.0, 8.0, "minecraft:overworld"), top_n=5)
    check("带传送门常加载区渲染不崩", os.path.exists(out), msg)
    txt = open(out, encoding="utf-8").read()
    check("传送门 region 已注入", "传送门常加载区" in txt or "portal" in txt, "")
    check("常加载区已并入地图数据", "regions" in txt, "")


def test_portal_pair_filter():
    """回归：只有主世界/下界**两侧**都有地狱门+红石装置，才算常加载装置。

    2026-09-12 用户拍板：地狱门常加载靠实体在两个维度之间循环，单侧识别到的一律不标注
    （纯门与普通方块无异）。生电存档实测：主世界 4 个门区块全无红石、下界装置带红石但
    主世界侧无红石装置 → 整个存档不该出现门常加载框（旧判据只看 nether_portal 方块）。
    """
    import json
    import re

    import main as main_mod
    from chunklag import loaders
    from make_fixture import PORTAL_WORLD, build_portal_world

    build_portal_world()
    res = main_mod.analyze_world(PORTAL_WORLD, "0")[0][2]
    check("主世界扫到 3 个门区块",
          res.portal_chunks == {(0, 0), (2, 0), (108, 0)}, str(sorted(res.portal_chunks)))
    check("主世界只把成对的 (2,0) 算常加载装置",
          res.portal_loader_chunks == {(2, 0)}, str(sorted(res.portal_loader_chunks)))

    nres = main_mod.analyze_world(PORTAL_WORLD, "-1")[0][2]
    check("下界配对装置算常加载",
          nres.portal_loader_chunks == {(0, 0)}, str(sorted(nres.portal_loader_chunks)))

    check("主世界→下界 区块 ÷8",
          loaders.to_mate_chunks({(506, 1793)}, True) == {(63, 224)}, "")
    check("下界→主世界 区块 ×8（1 块扩成 8×8）",
          loaders.to_mate_chunks({(0, 0)}, False) == {(dx, dz) for dx in range(8) for dz in range(8)}, "")

    check("本侧无红石 → 不通过",
          loaders.portal_loaders({(0, 0)}, set(), {(0, 0)}, {(0, 0)}, True) == set(), "")
    check("对面没门 → 不通过",
          loaders.portal_loaders({(0, 0)}, {(0, 0)}, set(), set(), True) == set(), "")
    check("对面有门但无红石 → 不通过",
          loaders.portal_loaders({(0, 0)}, {(0, 0)}, {(0, 0)}, set(), True) == set(), "")
    check("两侧齐 → 通过",
          loaders.portal_loaders({(0, 0)}, {(0, 0)}, {(0, 0)}, {(0, 0)}, True) == {(0, 0)}, "")
    # 配对容差：门对不必精确落在 8:1 换算点（生存_2 实测偏差 1 区块）
    check("容差内（偏差 1 区块）→ 通过",
          loaders.portal_loaders({(9, -35)}, {(9, -35)}, {(0, -4)}, {(0, -4)}, True) == {(9, -35)}, "")
    check("容差边界（8 区块）→ 通过",
          loaders.portal_loaders({(0, 0)}, {(0, 0)}, {(8, 0)}, {(8, 0)}, True) == {(0, 0)}, "")
    check("超出容差（9 区块）→ 不通过",
          loaders.portal_loaders({(0, 0)}, {(0, 0)}, {(9, 0)}, {(9, 0)}, True) == set(), "")

    out = os.path.join(ROOT, "tests", "map_portal_filter_test.html")
    msg = main_mod.render_map_for(PORTAL_WORLD, res, out, simdist=2,
                                  player=(8.0, 64.0, 8.0, "minecraft:overworld"), top_n=5)
    check("纯门/单侧门存档渲染不崩", os.path.exists(out), msg)
    txt = open(out, encoding="utf-8").read()
    m = re.search(r'<script id="mapdata" type="application/json">(.*?)</script>', txt, re.S)
    regions = json.loads(m.group(1))["regions"] if m else []
    portal_regs = [r for r in regions if r["type"] == "portal"]
    check("地图里只剩 1 个门常加载框", len(portal_regs) == 1, str(len(portal_regs)))
    check("框覆盖成对装置 (2,0)",
          bool(portal_regs) and [2, 0] in portal_regs[0]["chunks"], str(portal_regs))
    check("提示已忽略 2 个门区块", "未成对/无证据2 已忽略" in msg, msg)


def test_top_excludes_zero():
    """回归：TOP 榜只收**有卡顿因子**的区块（终端报告 / 汇总 / 地图红块共用同一份榜）。

    2026-09-12 用户反馈「部分无卡顿区域被红色色块覆盖」：并集地图里非零区块只有 7 个，
    旧逻辑把 13 个 0 分区块也顶进 TOP 20，前端照单给它们画了半透明红方块。
    """
    import json
    import re
    import types

    import main as main_mod
    from chunklag import analyze, mapdata
    from chunklag.factors import FACTOR_WEIGHTS

    zero = {k: 0 for k in FACTOR_WEIGHTS}
    hot = dict(zero)
    hot["entities_hostile"] = 2                       # 2×3 = 6 分

    d = mapdata.build_map_data(
        types.SimpleNamespace(chunk_entries={(0, 0): hot, (1, 0): zero, (2, 0): zero}),
        top_n=20)
    check("地图 TOP 只收非零区块", [t["x"] for t in d["top"]] == [0], str(d["top"]))

    res = analyze.analyze(((0, 0, hot), (1, 0, zero)), world_name="t", data_version=1)
    check("终端 TOP 榜只收非零区块",
          [(c[0], c[1]) for c in res.top_chunks] == [(0, 0)], str(res.top_chunks))
    check("全零存档 TOP 榜为空",
          analyze.analyze(((0, 0, zero),), world_name="t", data_version=1).top_chunks == [], "")

    # 端到端：portal_world 里全是门/红石方块（无实体、评分 0）→ 地图不该有任何红块
    from make_fixture import PORTAL_WORLD, build_portal_world
    build_portal_world()
    pres = main_mod.analyze_world(PORTAL_WORLD, "0")[0][2]
    out = os.path.join(ROOT, "tests", "map_topmark_test.html")
    main_mod.render_map_for(PORTAL_WORLD, pres, out, simdist=2,
                            player=(8.0, 64.0, 8.0, "minecraft:overworld"), top_n=20)
    txt = open(out, encoding="utf-8").read()
    m = re.search(r'<script id="mapdata" type="application/json">(.*?)</script>', txt, re.S)
    check("零分存档地图不带红块数据", json.loads(m.group(1))["top"] == [], "")


def test_new_layout_and_modern_loaders():
    """新版布局（26.x dimensions/ + players/data）+ 1.21.2+ 加载器判据（2026-09-12）。

    实测依据（用户 26.2 存档）：末影珍珠**不在 entities 分区**，而是存在
    players/data/<uuid>.dat 的 ender_pearls 列表里；新版 level.dat 无 Data 包裹、
    出生点是 spawn.pos；矿车地狱门加载器证据 = 传送门 + 矿车实体 + 已激活的动力铁轨。
    """
    import main as main_mod
    from chunklag import layout, loaders
    from make_fixture import NEW_WORLD, build_new_layout_world

    build_new_layout_world()

    dims = dict(layout.dimension_dirs(NEW_WORLD))
    check("新版布局识别出 2 个维度",
          set(dims) == {"minecraft:overworld", "minecraft:the_nether"}, str(sorted(dims)))
    check("新版出生点 spawn.pos 解析",
          layout.spawn_position(NEW_WORLD)[:3] == (0, -60, 0), str(layout.spawn_position(NEW_WORLD)))

    results = main_mod.analyze_world(NEW_WORLD, "all")
    check("全维度分析出 2 个结果", len(results) == 2, str(len(results)))
    ow = [r for r in results if r[2].dimension_key == "0"][0][2]
    nw = [r for r in results if r[2].dimension_key == "-1"][0][2]
    check("主世界扫到门区块 (0,0)", ow.portal_chunks == {(0, 0)}, str(sorted(ow.portal_chunks)))
    check("矿车+激活铁轨 → 主世界算加载器",
          ow.portal_loader_chunks == {(0, 0)}, str(sorted(ow.portal_loader_chunks)))
    check("下界配对侧也算加载器",
          nw.portal_loader_chunks == {(0, 0)}, str(sorted(nw.portal_loader_chunks)))

    pearls = loaders.read_ender_pearls(NEW_WORLD)
    check("读到玩家数据里的末影珍珠", len(pearls) == 1, str(pearls))
    check("珍珠维度与区块正确",
          bool(pearls) and pearls[0]["dim"] == "minecraft:overworld"
          and pearls[0]["chunk"] == (0, 0), str(pearls))
    check("珍珠分派到主世界维度", len(ow.pearls) == 1 and len(nw.pearls) == 0, "")
    check("珍珠 region 为 3×3",
          len(loaders.pearl_regions(pearls)[0][2]) == 9, "")
    check("珍珠 region 类型=pearl",
          loaders.pearl_regions(pearls)[0][0] == "pearl", "")

    # 矿车加载器证据：三项都要（激活铁轨 + 矿车）
    check("激活铁轨+矿车 → 有证据",
          loaders.loader_evidence({(0, 0)}, set(), {(0, 0)}, {(0, 0)}) == {(0, 0)}, "")
    check("只有激活铁轨、无矿车 → 无证据",
          loaders.loader_evidence({(0, 0)}, set(), {(0, 0)}, set()) == set(), "")
    check("只有矿车、无激活铁轨 → 无证据",
          loaders.loader_evidence({(0, 0)}, set(), set(), {(0, 0)}) == set(), "")
    check("红石器件仍算证据",
          loaders.loader_evidence({(0, 0)}, {(0, 0)}, set(), set()) == {(0, 0)}, "")
    check("邻域内证据也算（3×3）",
          loaders.loader_evidence({(5, 5)}, set(), {(6, 5)}, {(6, 5)}) == {(5, 5)}, "")

    # 激活 / 未激活的动力铁轨（方块状态 Properties）
    from chunklag import factors
    from make_fixture import C, S, L, B, _chunk_nbt_blocks, _TAG_MAP  # noqa: F401
    def rail_chunk(powered):
        props = {"powered": powered}
        raw = _chunk_nbt_blocks(0, 0, [("minecraft:powered_rail", props)])
        from chunklag import nbt as nbt_mod
        return nbt_mod.parse_nbt(raw)
    check("已激活动力铁轨 → True", factors.has_active_powered_rail(rail_chunk("true")), "")
    check("未激活动力铁轨 → False", not factors.has_active_powered_rail(rail_chunk("false")), "")

    # 端到端：地图里同时出现门框与珍珠框
    out = os.path.join(ROOT, "tests", "map_new_layout_test.html")
    msg = main_mod.render_map_for(NEW_WORLD, ow, out, simdist=2, top_n=5)
    check("新版布局渲染不崩", os.path.exists(out), msg)
    txt = open(out, encoding="utf-8").read()
    check("地图含珍珠强加载区", "珍珠强加载区" in txt, "")
    check("地图含传送门常加载区", "传送门常加载区" in txt, "")
    check("提示含珍珠数量", "珍珠: 1 颗" in msg, msg)


if __name__ == "__main__":
    build()
    test_nbt()
    test_leveldat()
    test_region()
    test_analysis()
    test_report()
    test_map()
    test_portal_regions_and_merge()
    test_render_map_with_portal()
    test_portal_pair_filter()
    test_top_excludes_zero()
    test_new_layout_and_modern_loaders()
    print("\n===== 结果: %d 通过 / %d 失败 =====" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
