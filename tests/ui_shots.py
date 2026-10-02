# -*- coding: utf-8 -*-
"""
界面预览图 + 硬指标断言（离屏渲染，自测用）。

用法（在项目根）：
    python tests/ui_shots.py                 # 出图到 tests/ui_preview/ 并跑断言
    python tests/ui_shots.py --out D:\\tmp\\shots

为什么要有它：折行 / 溢出 / 低对比这类问题**看代码看不出来**，只能出图目视；
而"有没有裸色值""徽章是不是固定高 24""占比文字有没有画出来"这类**能自动验的就别靠眼睛**
（后者实测被缩放图骗过一次）。字形缺失一律用 QFontMetrics.inFont 探针判。
"""
import argparse
import io
import os
import re
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication                                    # noqa: E402

from chunklag import gui, ui_tokens                                           # noqa: E402
from chunklag.scanjob import WorldResult                                      # noqa: E402
from make_fixture import FAKE_WORLD                                           # noqa: E402

CFG = os.path.join(HERE, "_shots_cfg.json")
DEFAULT_OUT = os.path.join(HERE, "ui_preview")
ICONS = ("○", "▸", "▾", "●")
SRC_FILES = ("gui.py", "ui_tokens.py", "scanjob.py")

# ⚠️ 预览图要进公开仓库，界面上的**路径必须脱敏**：真路径会把本机目录结构
# （用户名 / 工作区名 / 项目路径）带进图片里，图里改不掉、只能出图时就用假路径。
DEMO_PATH = r"D:\saves\生存001"
DEMO_OUT = r"D:\saves\output"
DEMO_LOG = (
    "正在查找存档：D:\\saves",
    "开始扫描：D:\\saves\\生存001（维度 全部维度）",
    "待解析 24.6 MB",
    "[1/1] 生存001",
    "   主世界：区块 3762 | 总卡顿分 1347300 | 最卡 (118,-92)=42800",
    "   下界：区块 812 | 总卡顿分 214600 | 最卡 (-42,18)=12400",
    "   → D:\\saves\\..\\output\\01_生存001\\主世界（48.2s）",
    "批量扫描汇总（2 项，按总卡顿分降序）",
    "世界 | 维度 | 区块数 | 总卡顿分 | 最卡区块 | 耗时",
    "生存001 | 主世界 | 3762 | 1347300 | (118,-92)=42800 | 48.2s",
)


def scan_once(app):
    """真扫一次合成存档：拿真实 WorldResult，并保留窗口（它的日志就是抽屉要截的内容）。"""
    win = gui.ScanGui(initial_path=FAKE_WORLD)
    win.ent_out.setText(os.path.join(HERE, "_shots_out"))
    win.chk_auto.setChecked(False)
    win.cmb_dim.setCurrentText("全部维度")
    win._start_scan()
    deadline = time.time() + 120
    while time.time() < deadline:
        app.processEvents()
        win._pump()
        if win.worker is None:
            break
        time.sleep(0.02)
    return list(win.results), win


def build_win(app, mode, results=(), view="compose", running=False, size=(1180, 760)):
    """按指定模式重建窗口（主题色在构建时取角色值，所以换模式必须重建）。"""
    ui_tokens.set_mode(mode)
    app.setStyleSheet(ui_tokens.QSS)
    win = gui.ScanGui()
    win.resize(*size)
    win.ent_path.setText(DEMO_PATH)      # 脱敏：见 DEMO_PATH 注释
    win.ent_out.setText(DEMO_OUT)        # 同上（高级折叠里也会显示）
    for r in results:
        win._add_result(r)
    if running:
        win._on_progress({"kind": "progress", "world": "生存001", "world_i": 1, "world_n": 1,
                          "dim": "主世界", "file": "r.0.0.mca", "chunks": 2408,
                          "percent": 64.0, "elapsed": 31.0, "eta": 17.0})
        win._set_running(True)
        win._log_line("正在解析 r.0.0.mca（2.4 MB）")
    if results:
        win._show_view(view)
    return win


def _leaks(win, needles=("ds工作区", "办事软件", "Users\\")):
    """
    收集窗口内所有文本里含本机路径特征的片段。

    预览图是**要进公开仓库的图片**：图里的字改不掉，只能在出图这一步守住。
    """
    from PySide6.QtWidgets import (QLabel, QLineEdit, QTableWidget, QTextEdit)

    def hit(text):
        return any(n in text for n in needles)

    bad = []
    for w in win.findChildren(QLabel):
        if hit(w.text()):
            bad.append(w.text()[:60])
    for w in win.findChildren(QLineEdit):
        if hit(w.text()):
            bad.append(w.text()[:60])
    for w in win.findChildren(QTextEdit):
        if hit(w.toPlainText()):
            bad.append("(日志正文)")
    for t in win.findChildren(QTableWidget):
        for r in range(t.rowCount()):
            for c in range(t.columnCount()):
                it = t.item(r, c)
                if it is not None and hit(it.text()):
                    bad.append(it.text()[:60])
    return bad


def shot(app, win, out_dir, name, leaks=None):
    win.show()
    app.processEvents()
    bad = _leaks(win)
    if bad and leaks is not None:
        leaks.extend((name, b) for b in bad)
        print("  !! %s 含本机路径特征：%s" % (name, bad[:2]))
    ok = win.grab().save(os.path.join(out_dir, name + ".png"))
    win.hide()
    win.close()
    print("%-18s %s" % (name, "OK" if ok else "保存失败"))
    return ok


# ---------------------------------------------------------------- 自动断言
def run_asserts(app):
    fails = []

    def need(cond, what, detail=""):
        print("[%s] %s%s" % ("PASS" if cond else "FAIL", what, "" if cond else "  " + str(detail)))
        if not cond:
            fails.append(what)

    # ① 唯一色值定义处：界面文件里不得出现裸色值
    for name in ("gui.py",):
        src = io.open(os.path.join(ROOT, "chunklag", name), encoding="utf-8").read()
        bad = sorted(set(re.findall(r"#[0-9A-Fa-f]{6}\b", src)))
        need(not bad, "%s 无裸色值" % name, bad)

    # ② 不得有外部请求（CDN / 字体 / 图标库）
    urls = []
    for name in SRC_FILES:
        src = io.open(os.path.join(ROOT, "chunklag", name), encoding="utf-8").read()
        urls += re.findall(r"https?://[^\s\"')]+", src)
    need(not urls, "无外部请求", urls)

    # ③ 令牌层：15 个角色亮暗成对齐全
    need(set(ui_tokens.LIGHT) == set(ui_tokens.DARK) and len(ui_tokens.LIGHT) == 15,
         "颜色角色 15 个且亮暗成对",
         "%d/%d" % (len(ui_tokens.LIGHT), len(ui_tokens.DARK)))

    # ④ 组件规格：徽章高 24 / 进度条高 6 / 表格行高 44
    need(gui.badge("x").height() == 24, "徽章固定高 24", gui.badge("x").height())
    need(gui.meter(50).height() == 6, "进度条高 6", gui.meter(50).height())

    win = gui.ScanGui()
    win.resize(1180, 760)
    need(win.cmp_table.verticalHeader().defaultSectionSize() == 44,
         "表格行高 44", win.cmp_table.verticalHeader().defaultSectionSize())
    need(not win.cmp_table.wordWrap() and not win.compose_table.wordWrap()
         and not win.rank_table.wordWrap(),
         "表格单元格不折行（窄窗口下用省略号）", "")
    need(win.stat_row.isHidden() and win.stack.currentIndex() == 0,
         "空态：统计行隐藏 + 内容区显示空态", win.stack.currentIndex())
    need(all(not b.isEnabled() for b in win.seg_buttons.values()),
         "空态：分段按钮禁用", "")

    # ⑤ 占比格真的画出百分比文字（像素级验证——这一条被缩放图骗过）
    wr = WorldResult(name="生存001", dim="主世界", out_dir=ROOT, chunks=3762, score=1024200,
                     top="(118,-92)=42800", seconds=48.2,
                     factors=[("方块实体(block_entities)", "漏斗(每tick扫)", 1204, 600, 722400),
                              ("实体(entities)", "敌对怪物", 486, 300, 145800)],
                     top_rows=[(118, -92, 42800, {"be_hopper": 62})], ticked=441)
    win._add_result(wr)
    win.show()
    app.processEvents()
    need(win.stack.currentIndex() == 1, "有结果后离开空态页", win.stack.currentIndex())
    holder = win.compose_table.cellWidget(1, 4)
    dark = 0
    if holder is not None:
        img = holder.grab().toImage()
        lb = holder.layout().itemAt(1).widget().geometry()
        for y in range(max(0, lb.y()), min(img.height(), lb.y() + lb.height())):
            for x in range(max(0, lb.x()), min(img.width(), lb.x() + lb.width())):
                if img.pixelColor(x, y).lightness() < 140:
                    dark += 1
    need(holder is not None and dark > 0, "构成视图占比格画出了百分比文字", "暗像素=%d" % dark)
    need(win.rank_table.rowCount() == 1, "榜单行数 = TOP 明细数", win.rank_table.rowCount())
    win.close()

    # ⑥ 字形探针（缺字形别靠眼睛看缩放图）
    from PySide6.QtGui import QFont, QFontMetrics
    fm = QFontMetrics(QFont(ui_tokens.ui_family()))
    missing = [c for c in ICONS if not fm.inFont(c)]
    need(not missing, "界面符号都有字形", missing)
    print("     字体=%s" % ui_tokens.ui_family())
    return fails


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--no-shots", action="store_true", help="只跑断言，不出图")
    args = ap.parse_args(argv)

    gui.CONFIG_PATH = CFG
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    app = QApplication(["ui-shots"])
    ui_tokens.apply(app)

    print("先真扫一次合成存档（截图上的数据都来自这次真实扫描）…")
    results, scanned = scan_once(app)
    ok = [r for r in results if not r.skipped]
    print("扫到 %d 项（%s）\n" % (len(ok), ", ".join("%s/%s" % (r.name, r.dim) for r in ok)))

    if not args.no_shots:
        leaks = []
        made = []
        shot_ = lambda w, n: shot(app, w, out_dir, n, leaks)          # noqa: E731
        for mode in ("light", "dark"):
            made.append(shot_(build_win(app, mode, results, "compose"), "compose_" + mode))
            made.append(shot_(build_win(app, mode, results, "rank"), "rank_" + mode))
            made.append(shot_(build_win(app, mode, results, "compare"), "compare_" + mode))
            made.append(shot_(build_win(app, mode), "empty_" + mode))
        made.append(shot_(build_win(app, "light", results, "compose", running=True),
                          "running_light"))
        made.append(shot_(build_win(app, "light", results, "compare", size=(1000, 660)),
                          "narrow_light"))
        # 日志抽屉用**真扫那个窗口**（状态是真的），但日志正文换成脱敏样本 ——
        # 真日志里带着本机绝对路径，不该进公开仓库的图。
        scanned._show_view("compose")
        scanned.ent_path.setText(DEMO_PATH)
        scanned.ent_out.setText(DEMO_OUT)
        scanned.lb_detail.setText("扫描完成，输出目录：%s" % DEMO_OUT)
        scanned.log.clear()
        for line in DEMO_LOG:
            scanned._log_line(line)
        scanned._toggle_drawer()
        made.append(shot_(scanned, "drawer_light"))
        print("\n共 %d 张 → %s" % (sum(1 for m in made if m), out_dir))
        if leaks:
            print("!! 预览图含本机路径特征 %d 处，禁止入库" % len(leaks))
            return 1
        print("脱敏检查：预览图里没有本机路径特征 ✅")
    else:
        scanned.close()

    print("== 硬指标断言 ==")
    fails = run_asserts(app)
    print("\n===== 断言：%d 项失败 =====" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
