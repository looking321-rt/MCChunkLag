# -*- coding: utf-8 -*-
"""
自测：扫描引擎（进度 / 中断 / 输出结构）+ GUI 纯函数与冒烟。

运行（在项目根）：
  python tests/test_scanjob.py
"""
import os
import shutil
import sys
import time

# GUI 测试用离屏平台：无需桌面会话、也不弹真窗口（真机跑测试不会闪窗）
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from chunklag import region, scanjob                                    # noqa: E402
from chunklag.scanjob import (ScanCancelled, ScanError, ScanJob,        # noqa: E402
                              ScanOptions, discover_worlds, plan_world,
                              safe_name, summary_text, world_info)
from make_fixture import build, FAKE_WORLD                              # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0

# 测试临时目录落在项目内（`.gitignore` 已忽略）：DSH 沙箱下系统 temp 里不能再建子目录
TMP_ROOT = os.path.join(HERE, "_tmp_scan")


class TempDir:
    """项目内一次性临时目录（用完即删）。"""

    def __init__(self, name):
        self.path = os.path.join(TMP_ROOT, name)

    def __enter__(self):
        shutil.rmtree(self.path, ignore_errors=True)
        os.makedirs(self.path)
        return self.path

    def __exit__(self, *_exc):
        shutil.rmtree(self.path, ignore_errors=True)
        return False


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS] %s" % name)
    else:
        FAIL += 1
        print("[FAIL] %s  %s" % (name, detail))


def skip(name, why):
    global SKIP
    SKIP += 1
    print("[SKIP] %s  %s" % (name, why))


# ---------------------------------------------------------------- 测试数据
def make_saves(root):
    """
    造一个 saves 目录：
      世界A  主世界 + 下界（复制同一份 region）
      世界B  只有主世界
      坏世界 只有 level.dat（无 region）→ 应被跳过且不影响整批
    """
    saves = os.path.join(root, "saves")
    a = os.path.join(saves, "世界A")
    shutil.copytree(FAKE_WORLD, a)
    shutil.copytree(os.path.join(a, "region"), os.path.join(a, "DIM-1", "region"))
    b = os.path.join(saves, "世界B")
    shutil.copytree(FAKE_WORLD, b)
    bad = os.path.join(saves, "坏世界")
    os.makedirs(bad)
    shutil.copy(os.path.join(FAKE_WORLD, "level.dat"), bad)
    return saves


class Collector:
    """收集引擎事件与日志（run() 在调用线程里跑，不用加锁）。"""

    def __init__(self):
        self.events = []
        self.logs = []

    def on_event(self, ev):
        self.events.append(ev)

    def on_log(self, text):
        self.logs.append(text)

    def kinds(self, kind):
        return [e for e in self.events if e["kind"] == kind]


# ---------------------------------------------------------------- 用例
def test_discover_and_plan():
    with TempDir("discover") as tmp:
        saves = make_saves(tmp)
        worlds = discover_worlds(saves)
        check("discover_worlds 找到 3 个世界（含坏世界）", len(worlds) == 3, str(worlds))

        # 深层嵌套（versions/<版本>/saves/<世界>）也要命中
        nested_root = os.path.join(tmp, "mc")
        nested = os.path.join(nested_root, "versions", "1.20.1", "saves", "深层世界")
        os.makedirs(os.path.dirname(nested))
        shutil.copytree(FAKE_WORLD, nested)
        check("默认深度覆盖 versions/<版本>/saves/<世界>",
              len(discover_worlds(nested_root)) == 1, str(discover_worlds(nested_root)))

        # PCL 整合包式深嵌套（8 层）——「发现」与「扫描」必须同一口径，否则列表里有却扫不到
        deep = os.path.join(tmp, "mc2", "PCL", "整合包", "某整合包", ".minecraft",
                            "versions", "1.18.2-Forge", "saves", "深层世界")
        os.makedirs(os.path.dirname(deep))
        shutil.copytree(FAKE_WORLD, deep)
        check("默认深度覆盖 PCL 整合包 8 层嵌套",
              discover_worlds(os.path.join(tmp, "mc2")) == [deep],
              str(discover_worlds(os.path.join(tmp, "mc2"))))

        # 重目录（mods 等）不进去找 —— 里面不可能有世界，遍历它们是纯浪费
        fake = os.path.join(tmp, "mc2", "PCL", "整合包", "某整合包", ".minecraft", "mods", "伪世界")
        os.makedirs(fake)
        shutil.copy(os.path.join(FAKE_WORLD, "level.dat"), fake)
        check("mods 里的伪世界被剪掉",
              discover_worlds(os.path.join(tmp, "mc2")) == [deep],
              str(discover_worlds(os.path.join(tmp, "mc2"))))

        a = os.path.join(saves, "世界A")
        one = plan_world(a, "0")
        check("plan_world 只取主世界", len(one) == 1 and one[0][1] == "主世界", str(one))
        all_dims = plan_world(a, "all")
        check("plan_world all 取主世界+下界", len(all_dims) == 2
              and [d[1] for d in all_dims] == ["主世界", "下界"], str([d[1] for d in all_dims]))
        check("plan 统计到字节数", all_dims[0][4] > 0, str(all_dims[0][4]))
        check("坏世界没有可扫维度", plan_world(os.path.join(saves, "坏世界"), "0") == [])

        name, ver = world_info(FAKE_WORLD)
        check("world_info 读到世界名", name == "测试MC存档", name)
        check("safe_name 清掉非法字符", safe_name('a/b:c*?') == "a_b_c_", safe_name('a/b:c*?'))


def test_scan_and_progress():
    with TempDir("scan") as tmp:
        saves = make_saves(tmp)
        out = os.path.join(tmp, "out")
        col = Collector()
        job = ScanJob(ScanOptions(path=saves, out=out, dim="all", top=5), col.on_event, col.on_log)
        results = job.run()

        ok = [r for r in results if not r.skipped]
        skipped = [r for r in results if r.skipped]
        check("4 项 = A 两维度 + B 一维度 + 坏世界跳过", len(results) == 4, str(len(results)))
        check("成功项 = 3（A 两维度 + B 一维度）", len(ok) == 3, str([(r.name, r.dim) for r in ok]))
        check("坏世界被跳过并给出原因", len(skipped) == 1 and "没有 region" in skipped[0].skipped,
              str(skipped))

        plans = col.kinds("plan")
        check("收到 plan 事件且总字节 > 0", len(plans) == 1 and plans[0]["total"] > 0, str(plans))

        prog = col.kinds("progress")
        check("收到多条进度事件", len(prog) >= 2, str(len(prog)))
        dones = [p["done"] for p in prog]
        check("进度 done 单调不减", all(b >= a for a, b in zip(dones, dones[1:])), str(dones))
        check("进度最终走满（done == total）",
              abs(prog[-1]["done"] - prog[-1]["total"]) < 1e-6,
              "%s vs %s" % (prog[-1]["done"], prog[-1]["total"]))
        check("进度事件带世界/维度/文件信息",
              all(p["world"] and p["dim"] and p["file"] for p in prog),
              str(prog[-1]))
        check("进度百分比在 0~100", all(0 <= p["percent"] <= 100.001 for p in prog), "")

        dones_ev = col.kinds("done")
        check("收到 done 事件且未被标记中断",
              len(dones_ev) == 1 and dones_ev[0]["cancelled"] is False, str(dones_ev))
        check("汇总文本含维度列", "世界 | 维度 | 区块数" in dones_ev[0]["text"], dones_ev[0]["text"])
        check("汇总含跳过清单", "跳过：" in dones_ev[0]["text"], "")

        # 输出结构：<世界>/<维度>/map.html
        map_paths = [r.map_path for r in ok]
        check("每项都有 map.html", all(os.path.exists(p) for p in map_paths), str(map_paths))
        check("输出结构含维度子目录",
              all(os.path.basename(os.path.dirname(p)) in ("主世界", "下界") for p in map_paths),
              str(map_paths))
        check("写出一份汇总.txt", os.path.exists(os.path.join(out, "汇总.txt")), out)
        check("世界序号前缀区分同名世界",
              len({os.path.dirname(os.path.dirname(p)) for p in map_paths}) == 2, str(map_paths))


def test_cancel_inside_world():
    """世界内中断：软停不抛异常，本世界结果不计入。"""
    with TempDir("cancel_inner") as tmp:
        saves = make_saves(tmp)
        col = Collector()
        job = ScanJob(ScanOptions(path=saves, out=os.path.join(tmp, "out"), dim="0"),
                      col.on_event, col.on_log)

        def cancel_on_first_chunk(ev):
            col.on_event(ev)
            if ev["kind"] == "progress" and ev.get("chunks", 0) >= 1:
                job.cancel()

        # 合成存档只有几个区块、毫秒级扫完，默认 0.12s 节流会把进度事件全吃掉；
        # 关掉节流才能在世界**扫到一半**时按下「中断扫描」（真机大存档不节流也够密）。
        interval = scanjob._ProgressHook.EMIT_INTERVAL
        scanjob._ProgressHook.EMIT_INTERVAL = 0.0
        try:
            job._on_event = cancel_on_first_chunk      # 事件回调里中断（模拟点「中断扫描」）
            results = job.run()
        finally:
            scanjob._ProgressHook.EMIT_INTERVAL = interval

        check("中断不抛异常", True)
        check("标记为已中断", job.was_cancelled is True, "")
        check("被中断的世界不出结果", results == [] or all(r.skipped for r in results),
              str([(r.name, r.dim) for r in results]))
        check("日志写明已中断", any("已中断" in t for t in col.logs), str(col.logs[-3:]))


def test_cancel_keeps_finished_worlds():
    """世界间中断：已完成的世界结果必须保留（这是「软停」的核心价值）。"""
    with TempDir("cancel_outer") as tmp:
        saves = make_saves(tmp)
        col = Collector()
        job = ScanJob(ScanOptions(path=saves, out=os.path.join(tmp, "out"), dim="all"),
                      col.on_event, col.on_log)

        def cancel_after_first_world(ev):
            col.on_event(ev)
            if ev["kind"] == "world_done":
                job.cancel()

        job._on_event = cancel_after_first_world
        results = job.run()

        ok = [r for r in results if not r.skipped]
        check("中断后仍有已完成结果", len(ok) >= 1, str(len(ok)))
        check("已完成结果都来自同一个世界",
              len({os.path.dirname(os.path.dirname(r.out_dir)) for r in ok}) == 1,
              str([r.out_dir for r in ok]))
        check("已完成结果的地图文件真的存在", all(os.path.exists(r.map_path) for r in ok), "")
        check("标记为已中断", job.was_cancelled is True, "")
        check("汇总里提示已中断", "已中断" in summary_text(results, cancelled=True), "")
        done_ev = col.kinds("done")
        check("done 事件带 cancelled=True", done_ev and done_ev[0]["cancelled"] is True, "")


def test_cancel_must_be_base_exception():
    """
    反向验证：中断信号必须继承 BaseException。

    region.scan_region_dir 用 `except Exception: continue` 跳过坏文件 ——
    普通异常会被它吞掉，取消就静默失效；这里用两条断言把这个约束钉死。
    """
    check("ScanCancelled 继承 BaseException", issubclass(ScanCancelled, BaseException), "")
    check("ScanCancelled 不是 Exception 子类", not issubclass(ScanCancelled, Exception), "")

    class FakeCancel(Exception):
        """故意写错的取消信号（Exception 子类）—— 用来证明它会被吞。"""

    class ThrowHook:
        def on_file(self, path, size):
            pass

        def on_chunk(self):
            raise FakeCancel("wrapped")

    region_dir = os.path.join(FAKE_WORLD, "region")
    swallowed = True
    try:
        list(region.scan_region_dir(region_dir, hook=ThrowHook()))
    except FakeCancel:
        swallowed = False
    check("Exception 子类的取消会被扫描循环吞掉（所以必须用 BaseException）", swallowed, "")

    class RealThrowHook(ThrowHook):
        def on_chunk(self):
            raise ScanCancelled()

    escaped = False
    try:
        list(region.scan_region_dir(region_dir, hook=RealThrowHook()))
    except ScanCancelled:
        escaped = True
    check("ScanCancelled 能穿透扫描循环（取消真的生效）", escaped, "")


def test_errors_and_edge_cases():
    col = Collector()
    job = ScanJob(ScanOptions(path=os.path.join(ROOT, "不存在的目录"), out="out"),
                  col.on_event, col.on_log)
    raised = ""
    try:
        job.run()
    except ScanError as exc:
        raised = str(exc)
    check("目录不存在 → ScanError", "目录不存在" in raised, raised)

    with TempDir("errors") as tmp:
        empty = os.path.join(tmp, "空目录")
        os.makedirs(empty)
        col2 = Collector()
        job2 = ScanJob(ScanOptions(path=empty, out=os.path.join(tmp, "out")),
                       col2.on_event, col2.on_log)
        raised2 = ""
        try:
            job2.run()
        except ScanError as exc:
            raised2 = str(exc)
        check("目录里没有存档 → ScanError", "没有找到存档" in raised2, raised2)


def test_finish_one_includes_scan_time():
    """
    结果里的「耗时」必须含**世界扫描时间**，不能只算渲染。

    真机踩过：生电存档扫了 17s，结果表与汇总却显示 0.0s（只计了出图那一下），
    看起来像根本没扫。这条断言就是钉住 base_seconds 被算进去。
    """
    import main as main_mod
    from chunklag.scanjob import _PlanItem
    with TempDir("finish_one") as tmp:
        res = main_mod.analyze_world(FAKE_WORLD, "0")[0][2]
        job = ScanJob(ScanOptions(path=FAKE_WORLD, out=os.path.join(tmp, "out"), dim="0"))
        item = _PlanItem(world_dir=FAKE_WORLD, name="测试世界")
        wr = job._finish_one(item, 1, 1, "主世界", res, base_seconds=7.5)
        check("耗时含世界扫描时间（base_seconds）", wr.seconds >= 7.5, str(wr.seconds))
        check("渲染照样出图", os.path.exists(wr.map_path), wr.map_path)
        wr2 = job._finish_one(item, 1, 1, "主世界", res)
        check("不传 base_seconds 时退化为纯渲染耗时", wr2.seconds < 7.5, str(wr2.seconds))


def test_gui_helpers():
    from chunklag import gui
    check("fmt_size 用 MB/GB", gui.fmt_size(3 * 1048576) == "3.0 MB", gui.fmt_size(3 * 1048576))
    check("fmt_eta 秒级", gui.fmt_eta(45) == "剩约 45 秒", gui.fmt_eta(45))
    check("fmt_eta 分级", gui.fmt_eta(80) == "剩约 1 分 20 秒", gui.fmt_eta(80))
    check("fmt_eta 未知返回空", gui.fmt_eta(None) == "", gui.fmt_eta(None))
    check("fmt_seconds 分钟格式", gui.fmt_seconds(125) == "2分05s", gui.fmt_seconds(125))
    dirs = gui.default_scan_dirs()
    check("default_scan_dirs 返回列表", isinstance(dirs, list), str(type(dirs)))

    with TempDir("helpers") as tmp:
        saves = make_saves(tmp)
        found = gui.find_worlds(saves)
        check("find_worlds 列出世界", len(found) == 3, str(len(found)))
        check("find_worlds 带世界名与版本",
              all(w["name"] and "version" in w for w in found), str(found[:1]))
        check("find_worlds 按最近游玩倒序",
              all(a["mtime"] >= b["mtime"] for a, b in zip(found, found[1:])), "")


class _DialogStub:
    """
    替掉 gui.dialogs：测试里弹出的模态框**没人点确定**，会把进程永久挂住
    （Tk 版实测：一次跑挂 240s、一次退出码 1 且无 traceback）。真机上是人来点，
    测试里必须换成记录器。
    """

    def __init__(self):
        self.calls = []

    def _rec(self, kind, title, text=""):
        self.calls.append((kind, title))
        return True

    def info(self, title, text):
        return self._rec("info", title, text)

    def warn(self, title, text):
        return self._rec("warning", title, text)

    def error(self, title, text):
        return self._rec("error", title, text)

    def ask(self, title, text):
        return self._rec("ask", title, text)

    @property
    def errors(self):
        return [c for c in self.calls if c[0] == "error"]


_APP = None


def qt_app():
    """离屏 QApplication（单例）。没有 PySide6 时返回 None，用例记 SKIP。"""
    global _APP
    if _APP is not None:
        return _APP
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return None
    from chunklag import ui_tokens
    _APP = QApplication.instance() or QApplication(["mcchunklag-selftest"])
    ui_tokens.apply(_APP)
    return _APP


def _drive(app, win, timeout=90):
    """
    驱动扫描（测试里不用真事件循环）：排空队列 + 处理 Qt 事件，直到工作线程结束。

    真机靠 QTimer 每 100ms 调 `_pump`；测试直接调，避免依赖定时器精度。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        win._pump()
        if win.worker is None:
            return True
        time.sleep(0.02)
    return False


def _stat_value(card_widget):
    return card_widget.layout().itemAt(0).widget().text()


def _stat_label(card_widget):
    return card_widget.layout().itemAt(1).widget().text()


def _table_names(table):
    """对比表第 0 列的行文本（同世界多维度时后续行为空）。"""
    return [table.item(r, 0).text() for r in range(table.rowCount())]


def _select_row(win, row):
    """
    模拟用户点选一行。

    ⚠️ 不能用 `QTableWidget.selectRow()`：它在离屏/未显示窗口下不发效
    （实测 hasSelection 恒 False），而 Qt 内部真实点击走的就是 selectionModel。
    """
    from PySide6.QtCore import QItemSelectionModel
    t = win.cmp_table
    t.selectionModel().select(t.model().index(row, 0),
                              QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)


def test_gui_smoke():
    """创建真实窗口跑一遍（离屏平台，无需桌面会话）。"""
    app = qt_app()
    if app is None:
        skip("GUI 冒烟", "没有 PySide6")
        return

    from chunklag import gui
    old_cfg = gui.CONFIG_PATH
    old_dlg = gui.dialogs
    stub = _DialogStub()
    with TempDir("gui") as tmp:
        gui.CONFIG_PATH = os.path.join(tmp, "cfg.json")
        gui.dialogs = stub
        try:
            win = gui.ScanGui(initial_path=FAKE_WORLD)
            check("窗口建起来了", win.ent_path.text() == FAKE_WORLD, win.ent_path.text())
            check("窗口尺寸 1180x760（需求规格）",
                  (win.width(), win.height()) == (1180, 760), str((win.width(), win.height())))
            check("最小尺寸 1000x660（需求规格）",
                  (win.minimumWidth(), win.minimumHeight()) == (1000, 660), "")
            check("维度下拉给了 4 个选项", win.cmb_dim.count() == 4, str(win.cmb_dim.count()))
            check("中断按钮初始不可用", not win.btn_cancel.isEnabled(), "")
            check("状态胶囊初始为「就绪」", win.pill.text() == "就绪", win.pill.text())
            win._log_line("冒烟测试")
            win._on_found({"kind": "found", "roots": [tmp],
                           "worlds": [{"name": "W1", "dir": FAKE_WORLD,
                                       "version": "1.20.1", "mtime": 1.0}]})
            check("发现存档后自动填入路径", win.ent_path.text() == FAKE_WORLD, win.ent_path.text())
            check("发现列表生成下拉项", win.cmb_found.count() == 1, str(win.cmb_found.count()))
            win._on_found({"kind": "found", "roots": [tmp], "worlds": []})
            check("没找到存档时给提示且不崩", "没找到" in win.lb_detail.text(), win.lb_detail.text())
            win._on_progress({"kind": "progress", "world": "W", "world_i": 1, "world_n": 2,
                              "dim": "主世界", "file": "r.0.0.mca", "chunks": 1024,
                              "percent": 12.5, "elapsed": 3.0, "eta": 21.0, "done": 1, "total": 8})
            check("进度条跟着进度事件走", abs(win.bar.value() / 10.0 - 12.5) < 1e-6,
                  str(win.bar.value()))
            check("状态胶囊显示百分比", win.pill.text() == "扫描中 12%", win.pill.text())
            check("明细行显示世界/维度/文件", "r.0.0.mca" in win.lb_detail.text(),
                  win.lb_detail.text())
            check("明细行带预计剩余", "剩约 21 秒" in win.lb_detail.text(), win.lb_detail.text())
            win.chk_auto.setChecked(False)
            win._add_result(scanjob.WorldResult(name="W", dim="主世界", out_dir=tmp,
                                                chunks=3, score=7, top="(0,0)=7", seconds=1.2))
            win._add_result(scanjob.WorldResult(name="坏", skipped="跳过：没有 region 区块数据"))
            check("对比表插入两行", win.cmp_table.rowCount() == 2, str(win.cmp_table.rowCount()))
            win._on_done({"kind": "done", "text": "汇总", "cancelled": False,
                          "results": win.results, "out": tmp})
            check("完成后按钮恢复可用", win.btn_start.isEnabled(), "")
            check("完成后状态胶囊报完成", "完成" in win.pill.text(), win.pill.text())
            win._set_running(True)
            check("扫描中开始按钮禁用", not win.btn_start.isEnabled(), "")
            check("扫描中中断按钮可用", win.btn_cancel.isEnabled(), "")
            win._set_running(False)
            win._save_cfg()
            check("配置写下来了", os.path.exists(gui.CONFIG_PATH), gui.CONFIG_PATH)
            check("配置里有窗口尺寸", bool(gui.load_config().get("geometry")), "")
            for i in range(7):                    # 常用位置：去重 + 上限 5
                win._remember_root(os.path.join(tmp, "root%d" % i))
            roots = list(win.cfg["scan_roots"])
            check("只记住最近 5 个位置", roots and len(roots) == 5, str(roots))
            check("最新的排最前", roots[0] == os.path.normpath(os.path.join(tmp, "root6")), roots[0])
            win._remember_root(roots[2])
            check("重复位置提到最前而不重复",
                  len(win.cfg["scan_roots"]) == 5 and win.cfg["scan_roots"][0] == roots[2]
                  and win.cfg["scan_roots"].count(roots[2]) == 1, str(win.cfg["scan_roots"]))
            check("全程没弹错误框", not stub.errors, str(stub.errors))
            win.close()
        finally:
            gui.CONFIG_PATH = old_cfg
            gui.dialogs = old_dlg


def test_gui_views():
    """
    三视图与空态（本版界面的灵魂所在）。

    钉住三件事：①空态真的显示且统计行不占位；②「构成」按贡献降序、最大项落在首行
    （用户的问题是"哪种原因占大头"）；③「榜单」只收有卡顿因子的区块、行数与 TOP 明细一致。
    """
    app = qt_app()
    if app is None:
        skip("GUI 视图", "没有 PySide6")
        return

    from chunklag import gui
    old_cfg = gui.CONFIG_PATH
    old_dlg = gui.dialogs
    with TempDir("gui_views") as tmp:
        gui.CONFIG_PATH = os.path.join(tmp, "cfg.json")
        gui.dialogs = _DialogStub()
        try:
            win = gui.ScanGui()
            check("空态：统计行隐藏", win.stat_row.isHidden(), "")
            check("空态：内容区停在空态页", win.stack.currentIndex() == 0,
                  str(win.stack.currentIndex()))
            check("空态：三个分段按钮禁用",
                  all(not b.isEnabled() for b in win.seg_buttons.values()), "")
            check("空态：底部提示「开始扫描」", "开始扫描" in win.lb_lastlog.text(),
                  win.lb_lastlog.text())

            # 形状与量级取自项目实测（生存001：3762 区块 / 441 会被 tick）
            factors = [
                ("方块实体(block_entities)", "漏斗(每tick扫)", 1204, 600, 722400),
                ("方块实体(block_entities)", "刷怪笼", 312, 500, 156000),
                ("实体(entities)", "敌对怪物", 486, 300, 145800),
            ]
            top_rows = [(118, -92, 42800, {"be_hopper": 62}),
                        (64, 128, 31200, {"be_hopper": 48})]
            wr = scanjob.WorldResult(name="生存001", dim="主世界", out_dir=tmp,
                                     chunks=3762, score=1024200, top="(118,-92)=42800",
                                     seconds=48.2, factors=factors, top_rows=top_rows,
                                     ticked=441)
            win._add_result(wr)

            check("有结果后统计行显示", not win.stat_row.isHidden(), "")
            check("统计卡：区块数", _stat_value(win.stat_chunks) == "3,762",
                  _stat_value(win.stat_chunks))
            check("统计卡：会被 tick 的绝对值", _stat_value(win.stat_ticked) == "441",
                  _stat_value(win.stat_ticked))
            check("统计卡：会被 tick 带百分比", "12%" in _stat_label(win.stat_ticked),
                  _stat_label(win.stat_ticked))
            check("统计卡：总卡顿分", _stat_value(win.stat_score) == "1,024,200",
                  _stat_value(win.stat_score))
            check("统计卡：最卡区块只给坐标（分数不混用同一标签）",
                  _stat_value(win.stat_top) == "(118,-92)", _stat_value(win.stat_top))
            check("有结果后分段按钮可用",
                  all(b.isEnabled() for b in win.seg_buttons.values()), "")

            # 构成视图：首行是组头，且是贡献最大的那个组
            first = win.compose_table.item(0, 0).text()
            check("构成视图首行是最大贡献组", first.startswith("方块实体"), first)
            check("构成视图行数 = 组头 + 因子行", win.compose_table.rowCount() == 5,
                  str(win.compose_table.rowCount()))
            pcts = []
            for r in range(1, win.compose_table.rowCount()):
                holder = win.compose_table.cellWidget(r, 4)
                if holder is not None:
                    pcts.append(holder.layout().itemAt(1).widget().text())
            check("构成视图每行带占比", len(pcts) == 3, str(pcts))
            check("构成视图占比按降序", float(pcts[0].rstrip("%")) >
                  float(pcts[-1].rstrip("%")), str(pcts))

            win._show_view("rank")
            check("切到榜单视图", win.stack.currentIndex() == 2, str(win.stack.currentIndex()))
            check("榜单行数 = TOP 明细数", win.rank_table.rowCount() == 2,
                  str(win.rank_table.rowCount()))
            check("榜单第一行是评分最高的区块",
                  win.rank_table.item(0, 2).text() == "42,800", win.rank_table.item(0, 2).text())
            check("榜单带因子明细（key 映射成中文名）",
                  "漏斗(每tick扫) ×62" in win.rank_table.item(0, 4).text(),
                  win.rank_table.item(0, 4).text())

            win._show_view("compare")
            check("切到对比视图", win.stack.currentIndex() == 3, str(win.stack.currentIndex()))

            win._open_focus_map()          # 地图不存在 → info（不能是 error）
            check("打开不存在的地图走 info 而非 error",
                  [c[0] for c in gui.dialogs.calls] == ["info"],
                  str(gui.dialogs.calls))
            win.close()
        finally:
            gui.CONFIG_PATH = old_cfg
            gui.dialogs = old_dlg


def test_worldresult_carries_analysis():
    """
    界面「构成 / 榜单」的数据来源：WorldResult 必须把因子聚合与 TOP 明细带出来。

    背景（2026-10-02 Qt 界面落地）：早先 `_finish_one` 只取 `top_chunks[0]` 拼成字符串、
    丢掉整个 AnalysisResult，界面因此做不出"哪种原因占大头"（只能去开 HTML 报告）。
    """
    import main as main_mod
    from chunklag.scanjob import _PlanItem
    with TempDir("carry") as tmp:
        res = main_mod.analyze_world(FAKE_WORLD, "0")[0][2]
        job = ScanJob(ScanOptions(path=FAKE_WORLD, out=os.path.join(tmp, "out"),
                                  dim="0", top=5))
        item = _PlanItem(world_dir=FAKE_WORLD, name="测试世界")
        wr = job._finish_one(item, 1, 1, "主世界", res)

        check("渲染时把加载判定结果挂回 res（ticked 的来源）",
              hasattr(res, "loaded_chunks"), "")
        check("ticked 取自地图加载判定", wr.ticked == getattr(res, "loaded_chunks", -1),
              "%s vs %s" % (wr.ticked, getattr(res, "loaded_chunks", None)))
        check("ticked 不超过区块总数", 0 <= wr.ticked <= wr.chunks,
              "%s/%s" % (wr.ticked, wr.chunks))
        check("WorldResult 带出因子聚合", bool(wr.factors), str(len(wr.factors)))
        check("因子行是 (组,label,count,weight,贡献) 五元组",
              all(len(x) == 5 for x in wr.factors), str(wr.factors[:1]))
        check("因子行只收 count>0", all(x[2] > 0 for x in wr.factors), str(wr.factors[:1]))

        # 组块连续：组间按组总贡献降序、组内按因子贡献降序（界面靠"组名变了"插组头行）
        order, blocks = [], []
        for g, _l, _c, _w, contrib in wr.factors:
            if g != (order[-1][0] if order else None):
                order.append((g, contrib))
                blocks.append((g, [contrib]))
            else:
                order[-1] = (g, order[-1][1] + contrib)
                blocks[-1][1].append(contrib)
        totals = [t for _g, t in order]
        check("组间按组贡献降序", totals == sorted(totals, reverse=True), str(totals))
        check("组内按因子贡献降序",
              all(block == sorted(block, reverse=True) for _g, block in blocks), str(blocks))
        check("组块连续（同组因子不被别的组打断）",
              len(set(g for g, _t in order)) == len(order), str([g for g, _t in order]))

        check("带出 TOP 明细", len(wr.top_rows) <= 5 and all(len(t) == 4 for t in wr.top_rows),
              str(len(wr.top_rows)))
        scores = [t[2] for t in wr.top_rows]
        check("TOP 明细按评分降序", scores == sorted(scores, reverse=True), str(scores))
        check("TOP 明细不含 0 分区块（0 分不是最卡）", all(s > 0 for s in scores), str(scores))


def test_gui_end_to_end():
    """
    GUI ↔ 引擎真实端到端：点「开始扫描」→ 工作线程跑完 → 结果表/输出文件都对。

    冒烟测试只喂假事件，这条才能抓到"线程 / 队列 / 状态流转"这类真问题。
    """
    app = qt_app()
    if app is None:
        skip("GUI 端到端", "没有 PySide6")
        return

    from chunklag import gui
    old_cfg = gui.CONFIG_PATH
    old_dlg = gui.dialogs
    stub = _DialogStub()
    with TempDir("gui_e2e") as tmp:
        gui.CONFIG_PATH = os.path.join(tmp, "cfg.json")
        gui.dialogs = stub
        try:
            saves = make_saves(tmp)
            out = os.path.join(tmp, "out")
            win = gui.ScanGui(initial_path=saves)
            win.ent_out.setText(out)
            win.chk_auto.setChecked(False)          # 别真去开浏览器
            win.cmb_dim.setCurrentText("主世界")
            win._start_scan()
            check("点开始后进入运行态", not win.btn_start.isEnabled(), "")

            check("端到端在 90s 内跑完", _drive(app, win), "超时")
            check("后台线程跑完并回收", win.worker is None, "仍在跑（超时）")
            ok = [r for r in win.results if not r.skipped]
            check("对比表行数 == 结果数", win.cmp_table.rowCount() == len(win.results),
                  "%d vs %d" % (win.cmp_table.rowCount(), len(win.results)))
            check("至少一项扫出结果", len(ok) >= 1, str([(r.name, r.dim) for r in win.results]))
            check("地图文件真的生成了", all(os.path.exists(r.map_path) for r in ok),
                  str([r.map_path for r in ok]))
            check("状态胶囊显示完成", "完成" in win.pill.text(), win.pill.text())
            check("结果目录含维度子层",
                  all(os.path.basename(os.path.dirname(r.map_path)) in ("主世界", "下界")
                      for r in ok), str([r.out_dir for r in ok]))
            check("日志里有汇总表头", "世界 | 维度" in win.log.toPlainText(), "")
            check("按钮回到可用态", win.btn_start.isEnabled(), "")
            check("端到端没弹错误框", not stub.errors, str(stub.errors))
            check("结果带出了因子聚合（构成视图有数据）",
                  all(r.factors for r in ok), str([len(r.factors) for r in ok]))

            win._remember_root(saves)
            win._save_cfg()
            check("常用位置写进配置文件",
                  gui.load_config().get("scan_roots") == [os.path.normpath(saves)],
                  str(gui.load_config().get("scan_roots")))

            # 跳过项不能炸（没有 map.html 时给提示而不是崩）
            skip_rows = [r for r in win.results if r.skipped]
            if skip_rows:
                row = [i for i in range(win.cmp_table.rowCount())
                       if win.cmp_table.item(i, 1).text().startswith("●")]
                _select_row(win, row[0])
                check("跳过项的 map_path 为空", skip_rows[0].map_path == "", "")
                win._open_focus_map()        # 走一遍真实分支（对话框已被替身接住）
                check("跳过项提示走的是 info 而非错误", not stub.errors, str(stub.errors))
                check("跳过行有 danger 状态点 + 文字（不只靠颜色）",
                      win.cmp_table.item(row[0], 1).text() == "● 跳过", "")

            # ---- 对比表排序（用可控数据，避免依赖真实扫描的分数分布）----
            win.results = []
            win.focus = None
            mk = scanjob.WorldResult
            win._add_result(mk(name="甲", dim="主世界", chunks=100, score=5,
                               top="(0,0)=5", seconds=1.0))
            win._add_result(mk(name="乙", dim="主世界", chunks=900, score=99,
                               top="(1,1)=99", seconds=9.0))
            win._add_result(mk(name="丙", skipped="跳过：没有 region 区块数据"))
            win._add_result(mk(name="丁", dim="下界", chunks=500, score=42,
                               top="(2,2)=42", seconds=4.0))

            win._sort_by(3)                   # 第 3 列 = 总卡顿分
            check("点「总卡顿分」列 → 降序", _table_names(win.cmp_table) == ["乙", "丁", "甲", "丙"],
                  str(_table_names(win.cmp_table)))
            check("被跳过的世界恒排最后",
                  win.cmp_table.rowCount() == 4
                  and win.cmp_table.item(3, 1).text() == "● 跳过", "")
            check("表头标出排序方向", "▼" in win.cmp_table.horizontalHeaderItem(3).text(),
                  win.cmp_table.horizontalHeaderItem(3).text())

            win._sort_by(3)
            check("同列再点一次 → 反向（升序）",
                  _table_names(win.cmp_table) == ["甲", "丁", "乙", "丙"],
                  str(_table_names(win.cmp_table)))

            win._sort_by(0)                   # 世界列（文本）
            names = [n for n in _table_names(win.cmp_table)[:3]]
            check("文本列默认升序", names == sorted(names), str(names))

            # 排序换位置时不能丢选中（"选中项要咬住"）
            _select_row(win, 2)
            picked = win.cmp_table.item(2, 0).text()
            check("点选一行后聚焦跟着走", win.focus is not None and win.focus.name == picked,
                  "%s vs %s" % (picked, win.focus.name if win.focus else None))
            win._sort_by(2)                   # 区块数列
            sel = win.cmp_table.selectedIndexes()
            check("排序后选中项仍咬住同一行",
                  bool(sel) and win.cmp_table.item(sel[0].row(), 0).text() == picked,
                  "%s → %s" % (picked,
                               win.cmp_table.item(sel[0].row(), 0).text() if sel else "无"))

            # ---- 第二轮：全部维度（同一世界要出多个维度的行 + 各自 map.html）----
            win.cmb_dim.setCurrentText("全部维度")
            win._start_scan()
            check("第二轮开始：对比表被清空复位", win.cmp_table.rowCount() == 0,
                  str(win.cmp_table.rowCount()))
            check("第二轮也跑完了", _drive(app, win), "超时")
            ok2 = [r for r in win.results if not r.skipped]
            dims = {r.dim for r in ok2}
            check("全部维度：主世界与下界都出了结果", dims == {"主世界", "下界"}, str(dims))
            check("每维度各有自己的目录与地图",
                  len({r.out_dir for r in ok2}) == len(ok2)
                  and all(os.path.exists(r.map_path) for r in ok2),
                  str([(r.dim, r.out_dir) for r in ok2]))
            check("第二轮也没弹错误框", not stub.errors, str(stub.errors))
            check("多维度结果都能切换聚焦（构成视图跟着走）",
                  all(h.factors for h in ok2) and win.focus in ok2 + [None], "")
            win.close()
        finally:
            gui.CONFIG_PATH = old_cfg
            gui.dialogs = old_dlg


if __name__ == "__main__":
    build()                      # 生成合成存档 tests/fake_world
    test_discover_and_plan()
    test_scan_and_progress()
    test_cancel_inside_world()
    test_cancel_keeps_finished_worlds()
    test_cancel_must_be_base_exception()
    test_errors_and_edge_cases()
    test_finish_one_includes_scan_time()
    test_gui_helpers()
    test_gui_smoke()
    test_gui_views()
    test_worldresult_carries_analysis()
    test_gui_end_to_end()
    print("\n===== 结果: %d 通过 / %d 失败 / %d 跳过 =====" % (PASS, FAIL, SKIP))
    sys.exit(1 if FAIL else 0)
