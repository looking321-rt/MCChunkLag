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
    check("最卡区块评分=2000（5 怪×300 + 刷怪笼×500）", top[2] == 2000, str(top[2]))

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


def test_mixed_layout_world():
    """混合布局回归：旧布局 region/ + DIM-1/ 与 dimensions/<mod>/<dim>/ 混存时**都要收**。

    2026-09-12 用户报「001 存档扫不出来（跳过：没有 region 区块数据）」：那份 1.20.1 整合包
    存档装了自定义维度 mod（暮色森林），于是同时存在 dimensions/ 与旧布局的 region/；
    早期实现"有 dimensions/ 就全按新布局"→ 只剩自定义维度、主世界被漏 → 结果为空。
    """
    import main as main_mod
    from chunklag import layout
    from make_fixture import MIXED_WORLD, build_mixed_layout_world

    build_mixed_layout_world()
    dims = [d for d, _p in layout.dimension_dirs(MIXED_WORLD)]
    check("混合布局收齐 3 个维度",
          dims == ["minecraft:overworld", "minecraft:the_nether",
                   "twilightforest:twilight_forest"], str(dims))

    results = main_mod.analyze_world(MIXED_WORLD, "all")
    check("全维度分析出 3 个结果", len(results) == 3, str(len(results)))
    check("results[0] 是主世界（不是自定义维度）",
          results[0][2].dimension_id == "minecraft:overworld", results[0][2].dimension_id)
    custom = [r[2] for r in results if r[2].dimension_id.startswith("twilightforest")]
    check("自定义维度也被分析", len(custom) == 1, "")
    check("自定义维度 key 为 None", custom and custom[0].dimension_key is None, "")
    check("自定义维度不参与地狱门成对判定",
          bool(custom) and custom[0].portal_loader_chunks == set(), "")

    only_ow = main_mod.analyze_world(MIXED_WORLD, "0")
    check("--dim 0 只出主世界", len(only_ow) == 1 and only_ow[0][2].dimension_id == "minecraft:overworld",
          str(len(only_ow)))


def _map_json(path):
    """从生成的 map.html 里取回内嵌的地图数据 JSON。"""
    import json
    import re

    txt = open(path, encoding="utf-8").read()
    m = re.search(r'<script id="mapdata" type="application/json">(.*?)</script>', txt, re.S)
    return json.loads(m.group(1))


def test_dimension_scoping_and_full_map():
    """回归（2026-09-14 用户反馈四条）：

    ① 出生点恒加载**只有主世界有** —— MC 的 spawn chunks 是主世界专有机制，
       下界/末地没有，「下界/末地图上冒出绿框」是 bug；
    ② 玩家模拟区只在玩家**当前所在维度**画 —— 否则玩家在主世界时，下界图上也会
       出现主世界坐标的玩家标记；
    ③ 地图默认**全量**：旧口径只画「玩家模拟区 ∪ 常加载区」，实测那份模组测试存档
       只画了 441/3762 个区块 → 用户报「18,-58 一堆掉落物识别不出来」；
    ④ 同世界多维度出图时，每张图左上角带维度切换按钮。
    """
    import main as main_mod
    from chunklag import loaders, scanjob
    from make_fixture import FAKE_WORLD, MIXED_WORLD, build_mixed_layout_world

    build_mixed_layout_world()

    # ① 常加载区按维度过滤
    ow_regs = [t for t, _l, _c in loaders.collect_regions(MIXED_WORLD, dim_id="minecraft:overworld")]
    nw_regs = [t for t, _l, _c in loaders.collect_regions(MIXED_WORLD, dim_id="minecraft:the_nether")]
    tf_regs = [t for t, _l, _c in loaders.collect_regions(
        MIXED_WORLD, dim_id="twilightforest:twilight_forest")]
    check("主世界有出生点恒加载区", "spawn" in ow_regs, str(ow_regs))
    check("下界没有出生点恒加载区", "spawn" not in nw_regs, str(nw_regs))
    check("自定义维度没有出生点恒加载区", "spawn" not in tf_regs, str(tf_regs))

    # ②③ 维度过滤 + 全量口径（用 FAKE_WORLD：3 个区块、无出生点、无玩家）
    res = main_mod.analyze_world(FAKE_WORLD, "0")[0][2]
    out = os.path.join(ROOT, "tests", "map_dim_test.html")
    main_mod.render_map_for(FAKE_WORLD, res, out, simdist=0, top_n=5,
                            player=(8.0, 64.0, 8.0, "minecraft:the_nether"))
    d = _map_json(out)
    check("玩家不在本维度 → 地图不标玩家", "player" not in d, str(d.get("player")))

    main_mod.render_map_for(FAKE_WORLD, res, out, simdist=0, top_n=5,
                            player=(8.0, 64.0, 8.0, "minecraft:overworld"))
    d = _map_json(out)
    check("玩家在本维度 → 地图标玩家", bool(d.get("player")), str(d.get("player")))
    check("默认全量（total == total_all）",
          d["total"] == d["total_all"] == 3, "%s/%s" % (d["total"], d["total_all"]))

    main_mod.render_map_for(FAKE_WORLD, res, out, simdist=0, top_n=5, scoped=True,
                            player=(8.0, 64.0, 8.0, "minecraft:overworld"))
    ds = _map_json(out)
    check("--scoped 只画玩家模拟区∪常加载区",
          ds["total"] < ds["total_all"], "%s/%s" % (ds["total"], ds["total_all"]))

    # ④ 维度切换按钮数据
    nav = main_mod.dim_nav([("主世界", os.path.join(ROOT, "tests", "navx", "主世界")),
                            ("下界", os.path.join(ROOT, "tests", "navx", "下界"))],
                           os.path.join(ROOT, "tests", "navx", "下界"))
    check("多维度 → 出 2 个切换项", bool(nav) and len(nav) == 2, str(nav))
    check("当前维度高亮",
          [n["label"] for n in nav if n["active"]] == ["下界"], str(nav))
    check("兄弟维度是相对链接且已编码",
          nav[0]["href"] == "../%E4%B8%BB%E4%B8%96%E7%95%8C/map.html", str(nav[0]))
    check("单维度 → 不出切换条", main_mod.dim_nav([("主世界", "x")], "x") is None, "")

    # ④′ 端到端：全维度批量扫描 → 每张图都有切换条，且只有主世界图有出生点框
    outdir = os.path.join(ROOT, "tests", "dim_out")
    job = scanjob.ScanJob(scanjob.ScanOptions(path=MIXED_WORLD, out=outdir, dim="all",
                                              simdist=2, top=5))
    results = [r for r in job.run() if not r.skipped]
    check("混合布局世界扫出 3 个维度", len(results) == 3, str([r.dim for r in results]))
    by_dim = {r.dim: r for r in results}
    check("每个维度各一份 map.html",
          all(os.path.exists(r.map_path) for r in results), str([r.map_path for r in results]))
    for r in results:
        html = open(r.map_path, encoding="utf-8").read()
        check("「%s」图带维度切换条" % r.dim, 'id="dimnav"' in html, "")
    spawn_dims = {r.dim for r in results
                  if any(x["type"] == "spawn" for x in _map_json(r.map_path).get("regions", []))}
    check("只有主世界图有出生点恒加载框",
          spawn_dims == {"主世界"}, str(spawn_dims))


def test_item_stack_counting():
    """掉落物按**堆**计、且权重极低（2026-09-15 用户拍板「800~1000 掉落物也不怎么卡」）。

    机制依据（MC 服务端 tick）：一堆掉落物 = **一个 item 实体**（同格同物品自动合并，≤64），
    实体 tick 只做重力/碰撞/合并/拾取判定，而且 6000 tick（5 分钟）后消失 —— 所以
    **物品个数不代表 tick 开销，堆数（实体数）才是**，且权重只有 3/堆（漏斗 600/个）。
    2026-09-14~15 的旧口径是"按物品个数"（一叠 64 个计 64 分），会把 384 个物品的区块
    顶进最卡 TOP —— 与实测体感不符，已废弃，勿退回。
    """
    import types

    import main as main_mod
    from chunklag import factors, mapdata
    from chunklag import nbt as _nbt
    from make_fixture import C, I, S, L, enc_compound

    def items_nbt(counts):
        ents = L(10, [C({"id": S("minecraft:item"),
                         "Item": C({"id": S("minecraft:stone"), "Count": I(n)})})
                      for n in counts])
        return enc_compound(C({"Level": C({"xPos": I(0), "zPos": I(0), "entities": ents})}))

    # analyze_chunk 收的是**解析后的 NBT dict**（不是原始字节）——
    # 直接喂 bytes 会静默数出 0（踩过一次：bytes 没有 .get，Level 分支全落空）
    parsed = _nbt.parse_nbt(items_nbt([64, 64, 1]))
    c = factors.analyze_chunk(parsed)
    check("3 个 item 实体 = 3 堆（不看堆里的 Count）", c["entities_item"] == 3,
          str(c["entities_item"]))

    # 1.20.5+ 字段小写化：item: {id, count} —— 实体类型只看 id，同样按 1 堆计
    small = _nbt.parse_nbt(enc_compound(C({"Level": C({"entities": L(10, [
        C({"id": S("minecraft:item"),
           "item": C({"id": S("minecraft:stone"), "count": I(16)})})])})})))
    check("新版小写字段同样按堆计", factors.analyze_chunk(small)["entities_item"] == 1,
          str(factors.analyze_chunk(small)["entities_item"]))
    # 经验球与掉落物同档（都是实体 tick 极轻的那类）
    orb = _nbt.parse_nbt(enc_compound(C({"Level": C({"entities": L(10, [
        C({"id": S("minecraft:experience_orb")})])})})))
    check("经验球也归 entities_item",
          factors.analyze_chunk(orb)["entities_item"] == 1, "")

    # 权重档位护栏（防止将来有人把掉落物权重改回高位）
    w_item = factors.FACTOR_WEIGHTS["entities_item"]
    check("100 堆掉落物（6000+ 个物品）仍比单个漏斗轻",
          100 * w_item < factors.FACTOR_WEIGHTS["be_hopper"],
          "%d×100 vs %d" % (w_item, factors.FACTOR_WEIGHTS["be_hopper"]))
    check("单堆掉落物比单个动物还低一个量级",
          10 * w_item < factors.FACTOR_WEIGHTS["entities_animal"],
          "%d×10 vs %d" % (w_item, factors.FACTOR_WEIGHTS["entities_animal"]))
    check("漏斗与箱子分开计档（每 tick 扫 vs 静态）",
          factors.FACTOR_WEIGHTS["be_hopper"] > 10 * factors.FACTOR_WEIGHTS["be_container"],
          "%d vs %d" % (factors.FACTOR_WEIGHTS["be_hopper"],
                        factors.FACTOR_WEIGHTS["be_container"]))

    # 双层分：基础分 s 不受加载影响（着色用），有效分 e = s × 加载系数（排序用）
    zero = {k: 0 for k in factors.FACTOR_WEIGHTS}
    hot = dict(zero)
    hot["entities_item"] = 16                          # 16 堆掉落物
    hot["be_hopper"] = 2                               # 2 个漏斗
    res = types.SimpleNamespace(chunk_entries={(1, -4): hot, (0, 0): zero})
    d = mapdata.build_map_data(res, top_n=5, load_coefs={(1, -4): 2.0})
    c1 = [x for x in d["chunks"] if x["x"] == 1][0]
    c0 = [x for x in d["chunks"] if x["x"] == 0][0]
    expect_s = 16 * w_item + 2 * factors.FACTOR_WEIGHTS["be_hopper"]
    check("基础分 = Σ(计数×权重)", c1["s"] == expect_s, "%s vs %s" % (c1["s"], expect_s))
    check("常加载区有效分 = 基础分×2", c1["e"] == expect_s * 2 and c1["l"] == 2.0,
          "%s / l=%s" % (c1["e"], c1["l"]))
    check("未被 tick 的区块有效分=0（基础分仍保留）",
          c0["e"] == 0 and c0["l"] == 0, "%s / l=%s" % (c0["e"], c0["l"]))
    check("TOP 只收有效分>0 的区块",
          [ (t["x"], t["z"]) for t in d["top"] ] == [(1, -4)], str(d["top"]))
    check("is_loaded 计数 = 会被 tick 的区块数", d["loaded"] == 1, str(d["loaded"]))

    # 无加载信息（load_coefs=None）时退化为旧行为：有效分 == 基础分
    d2 = mapdata.build_map_data(res, top_n=5)
    check("不做加载判定时 e == s",
          all(x["e"] == x["s"] for x in d2["chunks"]), "")

    # 色块分档阈值必须**随分布自适应**（固定阈值 + 权重换尺度 = 全图顶格红，2026-09-15 修）
    multi = types.SimpleNamespace(chunk_entries={
        (0, 0): dict(zero, be_container=1),        # 30  → 1 个静态箱子
        (1, 0): dict(zero, entities_hostile=1),    # 300 → 1 只怪
        (2, 0): dict(zero, entities_hostile=1),    # 300
        (3, 0): dict(zero, be_hopper=1),           # 600 → 1 个漏斗
        (4, 0): dict(zero, be_hopper=2),           # 1200
    })
    dm2 = mapdata.build_map_data(multi, top_n=20)
    check("色块阈值 = 非零区块 p50/p75/p90（最热 10% 必红，不会红档为空）",
          dm2["bands"] == [300, 600, 1200], str(dm2["bands"]))

    # 端到端：build_union_map 按「常加载×2 / 玩家区×1 / 其余×0」给系数
    res3 = types.SimpleNamespace(chunk_entries={(0, 0): dict(hot), (1, 1): dict(hot),
                                                (5, 5): dict(hot)})
    dm = mapdata.build_union_map(res3, (8.0, 64.0, 8.0, "minecraft:overworld"), 1,
                                 [("spawn", "出生点", {(0, 0)})], top_n=5)
    by = {(x["x"], x["z"]): x for x in dm["chunks"]}
    check("常加载区块系数 2（优先于玩家区）", by[(0, 0)]["l"] == 2.0, str(by[(0, 0)]))
    check("玩家模拟区内系数 1", by[(1, 1)]["l"] == 1.0, str(by[(1, 1)]))
    check("两处都不在 → 不会被 tick（系数 0）", by[(5, 5)]["l"] == 0.0, str(by[(5, 5)]))
    check("有效分>0 的区块 = 会被 tick 的两个",
          dm["loaded"] == 2 and len(dm["top"]) == 2, "%s / %s" % (dm["loaded"], len(dm["top"])))

    # 端到端：真存档口径 → 地图 HTML 里掉落物与其它因子同口径展示
    res2 = main_mod.analyze_world(FAKE_WORLD, "0")[0][2]
    out = os.path.join(ROOT, "tests", "map_items_test.html")
    main_mod.render_map_for(FAKE_WORLD, res2, out, simdist=2, top_n=5)
    txt = open(out, encoding="utf-8").read()
    check("HTML 没有掉落物单独榜单", "掉落物 TOP" not in txt and "itemlist" not in txt, "")
    check("HTML 不在色块上标掉落物个数", "ITEMS" not in txt and "黄字" not in txt, "")
    check("HTML 掉落物随因子列表展示（不再单独成行）",
          "keys.map(k=>`${LABELS[k]||k}: ${c.f[k]}`)" in txt and "'entities_item'" not in txt,
          "")
    check("HTML 标注双层分（基础分/有效分）",
          "基础分" in txt and "有效分" in txt and "加载系数" in txt, "")


def test_redstone_block_counting():
    """红石元件的**方块**（红石粉/中继器/侦测器/按钮…）要按个数计入评分（2026-09-15）。

    Why：这些是普通方块、不在 block_entities 里，早期「只数方块实体」时整类漏掉；而 Wiki
    点名的 MSPT 大户恰是「红石元件（尤其红石粉）造成海量方块更新/光照更新」。
    个数必须解码 `section.block_states.data`（4096 个 16³ 方块的位压缩 long 数组；
    1.16/20w17a 起紧凑无填充、entry 可跨 long 边界，bits = max(4, ceil(log2(palette)))）。
    """
    from chunklag import factors

    def pack_padded(indices, bits=4):
        """MC 实际用的打包：每 long 装 64//bits 个 entry，余位浪费、**entry 不跨 long 边界**。"""
        per = 64 // bits
        longs = [0] * ((len(indices) + per - 1) // per)
        mask = (1 << bits) - 1
        for i, v in enumerate(indices):
            li, off = divmod(i, per)
            longs[li] |= (v & mask) << (off * bits)
        return [x - (1 << 64) if x >= (1 << 63) else x for x in longs]

    def pack_compact(indices, bits=4):
        """紧凑跨 long 打包（本项目**不是**这种；保留兼容分支的用例）。"""
        longs = [0] * ((len(indices) * bits + 63) // 64)
        mask = (1 << bits) - 1
        for i, v in enumerate(indices):
            li, off = divmod(i * bits, 64)
            longs[li] |= (v & mask) << off
            if off + bits > 64:
                longs[li + 1] |= v >> (64 - off)
        return [x - (1 << 64) if x >= (1 << 63) else x for x in longs]

    def chunk_with(palette_names, data):
        return {"sections": [{"block_states": {
            "palette": [{"Name": "minecraft:" + n} for n in palette_names],
            "data": data,
        }}]}

    def indices(first_idx, first_n, second_idx=0, second_n=0):
        seq = [first_idx] * first_n + [second_idx] * second_n
        return seq + [0] * (4096 - len(seq))

    # 1) 基本计数（bits=4，padded 与 compact 等价）——
    #    这些合成数据没写 Properties，红石粉/中继器都算"未通电"→ 落在**待机档**
    c = factors.count_tick_blocks(chunk_with(
        ["stone", "redstone_wire", "repeater"], pack_padded(indices(1, 17, 2, 3))))
    check("红石粉 17 + 中继器 3 = 20（未通电 → 待机档）",
          c.get("blocks_redstone_idle") == 20 and c.get("blocks_redstone", 0) == 0, str(c))

    # 2) **回归**：bits=5（palette 30 项）—— 手抖写成"紧凑跨 long"会位错位、虚报成百上千倍。
    #    实测存档正是这种段（palette=30 → data 342 longs = ceil(4096/12)，padded）。
    pal30 = ["stone"] * 5 + ["redstone_wire"] + ["stone"] * 24
    d_padded = pack_padded(indices(5, 1000), 5)
    check("bits=5 padded：342 longs（与实测一致）", len(d_padded) == 342, str(len(d_padded)))
    c2 = factors.count_tick_blocks(chunk_with(pal30, d_padded)).get("blocks_redstone_idle")
    check("bits=5 padded 解出 1000 个红石粉（不虚高）", c2 == 1000, str(c2))
    c2b = factors.count_tick_blocks(
        chunk_with(pal30, pack_compact(indices(5, 1000), 5))).get("blocks_redstone_idle")
    check("bits=5 紧凑打包也兼容", c2b == 1000, str(c2b))

    # 3) 健全性检查：位宽/打包判错 → 大量越界索引 → 宁可返回 0 也不虚高
    bogus = {"sections": [{"block_states": {
        "palette": [{"Name": "minecraft:stone"}, {"Name": "minecraft:redstone_wire"}],
        "data": pack_padded(indices(1, 4096), 5),      # palette 只有 2 项却按 bits=5 写
    }}]}
    check("位宽判错时返回 0（不虚高）",
          factors.count_tick_blocks(bogus).get("blocks_redstone_idle", 0) == 0,
          str(factors.count_tick_blocks(bogus)))

    # 4) 按钮/压力板按后缀匹配；活塞等方块实体不在这里重复计
    c3 = factors.count_tick_blocks(chunk_with(
        ["stone", "oak_button", "stone_pressure_plate"],
        pack_padded(indices(1, 5, 2, 2)))).get("blocks_redstone_idle")
    check("按钮/压力板按后缀识别 = 7", c3 == 7, str(c3))
    c4 = factors.count_tick_blocks(chunk_with(
        ["stone", "piston", "comparator"],
        pack_padded(indices(1, 100, 2, 4)))).get("blocks_redstone_idle")
    check("活塞不计入（避免与 be_piston 重复），比较器计 4", c4 == 4, str(c4))

    # 5) 无 data 的段（palette 长度 1）不猜 4096 个
    no_data = {"sections": [{"block_states": {
        "palette": [{"Name": "minecraft:redstone_wire"}]}}]}
    check("无 data 的段不误算成 4096 个",
          factors.count_tick_blocks(no_data).get("blocks_redstone_idle", 0) == 0, "")

    # 6) analyze_chunk 集成 + 权重档位
    counts = factors.analyze_chunk(chunk_with(
        ["stone", "redstone_wire"], pack_padded(indices(1, 64))))
    check("analyze_chunk 计入 blocks_redstone_idle=64", counts["blocks_redstone_idle"] == 64,
          str(counts["blocks_redstone_idle"]))
    w = factors.FACTOR_WEIGHTS
    check("权重档位：静态容器 < 活跃红石元件 < 漏斗",
          w["be_container"] < w["blocks_redstone"] < w["be_hopper"],
          "%s / %s / %s" % (w["be_container"], w["blocks_redstone"], w["be_hopper"]))


def test_factor_taxonomy():
    """因子细分口径（2026-09-15 用户拍板）。

    实体按「每 tick 干多少活」分 18 档（BOSS/矿车三档/船/下落方块/激活 TNT/动物/宠物/
    飞行/水生/弹射物/盔甲架…），方块实体按「每 tick 必做 / 持续生成 / 触发才动 / 静态」分档，
    方块侧补上流体刻、火、生长类，并读方块状态区分**红石活跃 vs 待机**。

    三条本次修掉的失效点用断言钉死（都是实测真实存档发现的）：
      1) `_ANIMAL` 集合定义了却**从未被 _entity_factor 引用** → 生存001 里 4763 个动物
         （鸡 867/羊 833/猪 687…）全掉进 entities_other 兜底档；
      2) `sculk_sensor` 既是方块实体又被当红石元件方块 → 双重计分（实测 10260 个）；
      3) `dropper`/`dispenser` 被归到熔炉档（`be_furnace`）。
    """
    from chunklag import factors

    def ent(eid):
        return factors._entity_factor({"id": "minecraft:" + eid})

    # --- 实体细分 ---
    check("敌对怪 → entities_hostile", ent("zombie") == "entities_hostile", ent("zombie"))
    check("守夜人 → BOSS 档", ent("warden") == "entities_boss", ent("warden"))
    check("远古怪 → BOSS 档", ent("elder_guardian") == "entities_boss")
    check("牛 → 陆生动物（_ANIMAL 曾未接线）", ent("cow") == "entities_animal", ent("cow"))
    check("鸡 → 陆生动物", ent("chicken") == "entities_animal")
    check("鳕鱼 → 水生", ent("cod") == "entities_aquatic", ent("cod"))
    check("蝙蝠 → 飞行", ent("bat") == "entities_flying")
    check("蜜蜂 → 飞行（不是陆生动物）", ent("bee") == "entities_flying", ent("bee"))
    check("狼 → 宠物", ent("wolf") == "entities_pet")
    check("羊驼 → 宠物（不是陆生动物）", ent("llama") == "entities_pet", ent("llama"))
    check("铁傀儡 → 傀儡档", ent("iron_golem") == "entities_golem")
    check("村民 → 村民档", ent("villager") == "entities_villager")
    check("流浪商人 → 村民档", ent("wandering_trader") == "entities_villager")
    check("普通矿车", ent("minecart") == "entities_minecart")
    check("箱矿车 → 运输矿车档", ent("chest_minecart") == "entities_minecart_cargo")
    check("漏斗矿车 → 运输矿车档", ent("hopper_minecart") == "entities_minecart_cargo")
    check("刷怪笼矿车 → 特殊矿车档", ent("spawner_minecart") == "entities_minecart_special")
    check("船（1.19+ 木种前缀）", ent("oak_boat") == "entities_boat", ent("oak_boat"))
    check("旧版 boat id 也归船", ent("boat") == "entities_boat")
    check("箱船", ent("oak_chest_boat") == "entities_boat")
    check("下落的方块", ent("falling_block") == "entities_falling")
    check("激活的 TNT（实体）", ent("tnt") == "entities_tnt")
    check("TNT 矿车不被当作 TNT 实体", ent("tnt_minecart") == "entities_minecart_special")
    check("盔甲架 → 展示类", ent("armor_stand") == "entities_decor")
    check("物品展示框 → 展示类", ent("item_frame") == "entities_decor")
    check("箭 → 弹射物", ent("arrow") == "entities_projectile")
    check("经验球 → 掉落物档", ent("experience_orb") == "entities_item")

    # --- 方块实体细分 ---
    def be(bid, **kw):
        d = {"id": "minecraft:" + bid}
        d.update(kw)
        return factors._be_factor(d)

    check("漏斗 → 每 tick 档", be("hopper") == "be_hopper")
    check("刷怪笼", be("spawner") == "be_spawner")
    check("试炼刷怪箱", be("trial_spawner") == "be_trial_spawner")
    check("幽匿感测器 → 幽匿档", be("sculk_sensor") == "be_sculk")
    check("命令方块 → 命令档", be("command_block") == "be_command")
    check("粘性活塞 → 活塞档", be("sticky_piston") == "be_piston")
    check("投掷器 → 红石 IO（曾错归熔炉档）", be("dropper") == "be_redstone_io", be("dropper"))
    check("发射器 → 红石 IO", be("dispenser") == "be_redstone_io")
    check("熔炉（没在烧）→ 待机档", be("furnace") == "be_machine", be("furnace"))
    check("熔炉（BurnTime>0）→ 烧炼档", be("furnace", BurnTime=200) == "be_smelting")
    check("酿造台（BrewTime>0）→ 烧炼档", be("brewing_stand", BrewTime=100) == "be_smelting")
    check("营火（CookingTimes 有值）→ 烧炼档",
          be("campfire", CookingTimes=[100, 0, 0, 0]) == "be_smelting")
    check("箱子 → 静态容器", be("chest") == "be_container")
    check("末影箱 → 单列档（无库存）", be("ender_chest") == "be_ender_chest")
    check("潜影盒（带颜色前缀）", be("red_shulker_box") == "be_shulker", be("red_shulker_box"))
    check("告示牌 → 静态", be("sign") == "be_static")
    check("床（带颜色前缀）→ 静态", be("red_bed") == "be_static")
    check("mod 未知方块实体 → 兜底", be("dummy") == "be_other")

    # --- 方块细分（含活跃状态判定）---
    def blk(name, **props):
        return factors._block_factor("minecraft:" + name, props)

    check("未通电红石粉 → 待机档", blk("redstone_wire", power="0") == "blocks_redstone_idle")
    check("通电红石粉（power=7）→ 活跃档", blk("redstone_wire", power="7") == "blocks_redstone")
    check("已触发侦测器 → 活跃档", blk("observer", powered="true") == "blocks_redstone")
    check("未触发侦测器 → 待机档", blk("observer", powered="false") == "blocks_redstone_idle")
    check("点亮的中继器 → 活跃档", blk("repeater", powered="true") == "blocks_redstone")
    check("红石块 → 待机档（静态电源，不给高权重）",
          blk("redstone_block") == "blocks_redstone_idle")
    check("红石灯 → 待机档", blk("redstone_lamp") == "blocks_redstone_idle")
    check("音符盒 → 待机档", blk("note_block") == "blocks_redstone_idle")
    check("按钮/压力板按后缀识别",
          blk("oak_button") == "blocks_redstone_idle"
          and blk("stone_pressure_plate") == "blocks_redstone_idle")
    check("活塞（方块形态）不在方块侧计（避免与 be_piston 双计）", blk("piston") is None)
    check("幽匿感测器不在方块侧计（避免与 be_sculk 双计）", blk("sculk_sensor") is None)
    # --- 流体与生长类**必须不计分**（2026-09-15 加进来后实测撤掉，别再退回）---
    # 理由：①随机刻与方块数量无关（每区块按 randomTickSpeed 固定抽样，138 万个作物与 1000 个
    # 抽样次数一样）②稳态流体不再产生 scheduled tick，存档无法区分"正在流"与"早已静止"。
    # 实测代价：生存001 里这两类独吞 **95.6% 总分**，TOP 12 全是 lava/water/growt，
    # 玩家装置被完全挤出榜单；生电 TOP2/3/5 同样被水域占掉。
    check("流动水不计分（曾独吞 25.7% 总分）", blk("water", level="3") is None)
    check("下落水（level=8）也不计分", blk("water", level="8") is None)
    check("水源不计分", blk("water", level="0") is None)
    check("流动岩浆不计分（曾独吞 29.6% 总分）", blk("lava", level="2") is None)
    check("小麦不计分（曾独吞 40.1% 总分）", blk("wheat") is None)
    check("紫水晶母岩不计分", blk("budding_amethyst") is None)
    check("树叶/发光地衣不计分（自然装饰海量，会淹掉装置热点）",
          blk("oak_leaves") is None and blk("glow_lichen") is None)
    check("火仍然计分（会持续 tick，且只存在于活动中）", blk("fire") == "blocks_fire")
    check("流体/生长类因子已从权重表移除",
          "blocks_water" not in factors.FACTOR_WEIGHTS
          and "blocks_lava" not in factors.FACTOR_WEIGHTS
          and "blocks_growth" not in factors.FACTOR_WEIGHTS)

    # --- 端到端：一个 section 里多种方块各归各档 ---
    seq = [1] * 3 + [2] * 2 + [3] * 100 + [4] * 7 + [5] * 4 + [6] * 9   # palette 7 项 → bits=4
    per = 16
    longs = [0] * ((len(seq) + per - 1) // per)
    for i, v in enumerate(seq):
        li, off = divmod(i, per)
        longs[li] |= v << (off * 4)
    longs = [x - (1 << 64) if x >= (1 << 63) else x for x in longs]      # NBT 是**有符号** long
    chunk = {"sections": [{"block_states": {
        "palette": [
            {"Name": "minecraft:stone"},
            {"Name": "minecraft:redstone_wire", "Properties": {"power": "0"}},
            {"Name": "minecraft:observer", "Properties": {"powered": "true"}},
            {"Name": "minecraft:water", "Properties": {"level": "0"}},
            {"Name": "minecraft:water", "Properties": {"level": "5"}},
            {"Name": "minecraft:lava", "Properties": {"level": "1"}},
            {"Name": "minecraft:fire"},
        ],
        "data": longs,
    }}]}
    tb = factors.count_tick_blocks(chunk)
    check("端到端：待机红石粉 3 + 活跃侦测器 2",
          tb.get("blocks_redstone_idle") == 3 and tb.get("blocks_redstone") == 2, str(tb))
    check("端到端：水源/流动水/流动岩浆/小麦全都不计分",
          not (set(tb) & {"blocks_water", "blocks_lava", "blocks_growth"}), str(tb))
    check("端到端：火计 9 个", tb.get("blocks_fire") == 9, str(tb))


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
    test_mixed_layout_world()
    test_dimension_scoping_and_full_map()
    test_item_stack_counting()
    test_redstone_block_counting()
    test_factor_taxonomy()
    print("\n===== 结果: %d 通过 / %d 失败 =====" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
