# -*- coding: utf-8 -*-
"""
自测：扫描引擎（进度 / 中断 / 输出结构）+ GUI 纯函数与冒烟。

运行（在项目根）：
  python tests/test_scanjob.py
"""
import os
import shutil
import sys

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
        check("深度 4 覆盖 versions/<版本>/saves/<世界>",
              len(discover_worlds(nested_root)) == 1, str(discover_worlds(nested_root)))

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


def test_gui_smoke():
    """创建真实窗口跑一遍（无桌面会话时会失败 → 记 SKIP，不算失败）。"""
    try:
        import tkinter as tk
    except ImportError as exc:
        skip("GUI 冒烟", "没有 tkinter：%s" % exc)
        return
    try:
        root = tk.Tk()
    except Exception as exc:
        skip("GUI 冒烟", "无法创建窗口：%s" % exc)
        return

    from chunklag import gui
    old_cfg = gui.CONFIG_PATH
    with TempDir("gui") as tmp:
        gui.CONFIG_PATH = os.path.join(tmp, "cfg.json")
        try:
            root.withdraw()
            win = gui.ScanGui(root, initial_path=FAKE_WORLD)
            root.update()
            check("窗口建起来了", win.ent_path.get() == FAKE_WORLD, win.ent_path.get())
            check("维度下拉给了 4 个选项",
                  win.cmb_dim["values"] and len(win.cmb_dim["values"]) == 4,
                  str(win.cmb_dim["values"]))
            check("中断按钮初始不可用", str(win.btn_cancel["state"]) == "disabled", "")
            win._log_line("冒烟测试")
            win._on_found({"kind": "found", "roots": [tmp],
                           "worlds": [{"name": "W1", "dir": FAKE_WORLD,
                                       "version": "1.20.1", "mtime": 1.0}]})
            check("发现存档后自动填入路径", win.ent_path.get() == FAKE_WORLD, win.ent_path.get())
            check("发现列表生成下拉项", len(win.cmb_found["values"]) == 1,
                  str(win.cmb_found["values"]))
            win._on_found({"kind": "found", "roots": [tmp], "worlds": []})
            check("没找到存档时给提示且不崩", "没找到" in win.var_state.get(), win.var_state.get())
            win._on_progress({"kind": "progress", "world": "W", "world_i": 1, "world_n": 2,
                              "dim": "主世界", "file": "r.0.0.mca", "chunks": 1024,
                              "percent": 12.5, "elapsed": 3.0, "eta": 21.0, "done": 1, "total": 8})
            check("进度条跟着进度事件走", abs(win.bar["value"] - 12.5) < 1e-6, str(win.bar["value"]))
            check("明细行显示世界/维度/文件", "r.0.0.mca" in win.var_detail.get(),
                  win.var_detail.get())
            win._add_result(scanjob.WorldResult(name="W", dim="主世界", out_dir=tmp,
                                                chunks=3, score=7, top="(0,0)=7", seconds=1.2))
            win._add_result(scanjob.WorldResult(name="坏", skipped="跳过：没有 region 区块数据"))
            check("结果表插入两行", len(win.tree.get_children()) == 2, "")
            win._on_done({"kind": "done", "text": "汇总", "cancelled": False,
                          "results": win.results, "out": tmp})
            check("完成后按钮恢复可用", str(win.btn_start["state"]) == "normal", "")
            win._set_running(True)
            check("扫描中开始按钮禁用", str(win.btn_start["state"]) == "disabled", "")
            check("扫描中中断按钮可用", str(win.btn_cancel["state"]) == "normal", "")
            win._set_running(False)
            win._save_cfg()
            check("配置写下来了", os.path.exists(gui.CONFIG_PATH), gui.CONFIG_PATH)
            win._closing = True
            root.destroy()
        finally:
            gui.CONFIG_PATH = old_cfg


if __name__ == "__main__":
    build()                      # 生成合成存档 tests/fake_world
    test_discover_and_plan()
    test_scan_and_progress()
    test_cancel_inside_world()
    test_cancel_keeps_finished_worlds()
    test_cancel_must_be_base_exception()
    test_errors_and_edge_cases()
    test_gui_helpers()
    test_gui_smoke()
    print("\n===== 结果: %d 通过 / %d 失败 / %d 跳过 =====" % (PASS, FAIL, SKIP))
    sys.exit(1 if FAIL else 0)
