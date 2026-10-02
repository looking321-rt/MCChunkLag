# -*- coding: utf-8 -*-
"""
扫描存档 GUI —— PySide6（Qt 6）版本。

流程：选存档 → 选维度 → 开始扫描（后台线程 + 实时进度）→ 结果三视图（构成 / 榜单 / 对比）。
报告仍走 HTML：每「世界 × 维度」一份 `map.html`，界面里**不内嵌渲染**，所以再大的存档也不卡。

设计来源：`D:\\办事软件\\DS工作区_UI\\projects\\ui_002_MCChunkLag`（设计工作区的第一版界面设计）；
色值与尺度唯一出处是 `chunklag/ui_tokens.py`，本文件**不写任何裸色值**。

启动：
  双击 `启动界面.bat`（可把存档文件夹拖到 bat 上，直接把路径带进来）
  或 `python chunklag/gui.py [存档目录]`

线程模型：扫描跑在工作线程，只往 `queue` 丢事件；UI 线程用 QTimer 每 100ms 排空队列刷新控件
（Qt 控件不是线程安全的，子线程绝不碰控件）。
"""
import glob
import json
import os
import queue
import sys
import threading
import time
import traceback

# main.py / scan.py 在项目根，而本文件可能被 `python chunklag/gui.py` 这样直接跑
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt, QTimer, QItemSelectionModel                        # noqa: E402
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor         # noqa: E402
from PySide6.QtWidgets import (                                               # noqa: E402
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox,
    QFileDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QSizePolicy,
    QSpinBox, QStackedWidget, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget,
)

from chunklag import ui_tokens                                               # noqa: E402
from chunklag.scanjob import (DIM_CHOICES, ScanError, ScanJob, ScanOptions,   # noqa: E402
                              discover_worlds, world_info)

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".mcchunklag.json")

VIEWS = ("compose", "rank", "compare")
VIEW_LABELS = {"compose": "构成", "rank": "榜单", "compare": "对比"}

ADD_MAP_HINT = "离线读存档 · 估算卡顿风险"

# 免责声明：口径照抄 report.py:68 的说明（评分是启发式估算、此处是基础分）。
DISCLAIMER = ("评分为启发式离线估算（Σ 计数 × 经验权重），非真实 mspt —— "
              "对比各区块相对高低即可，真实 mspt 需 spark 连运行中的服务端测量。"
              "此处是基础分（不受加载状态影响）。")


# ---------------------------------------------------------------- 纯函数（可自测）
def fmt_size(num_bytes):
    """字节 → 人读大小。"""
    n = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%.0f %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return "%.1f GB" % n


def fmt_eta(seconds):
    """剩余秒数 → '剩约 1 分 20 秒'；估算不出返回空串。"""
    if seconds is None or seconds < 0:
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return "剩约 %d 秒" % seconds
    if seconds < 3600:
        return "剩约 %d 分 %d 秒" % (seconds // 60, seconds % 60)
    return "剩约 %d 小时 %d 分" % (seconds // 3600, (seconds % 3600) // 60)


def fmt_seconds(seconds):
    """耗时 → '12.3s' / '2分05s'。"""
    s = float(seconds or 0)
    if s < 60:
        return "%.1fs" % s
    return "%d分%02ds" % (int(s) // 60, int(s) % 60)


def find_worlds(root, max_depth=8):
    """
    列出 root 下的存档世界：名称 / 路径 / 版本 / 最后修改时间，按最近游玩倒序。

    深度放宽到 8：启动器嵌套层数不一 —— hmcl 是
    `Minecraft/hmcl/.minecraft/versions/<版本>/saves/<世界>`（6 层），
    PCL 整合包是 `Minecraft/PCL/整合包/<包名>/.minecraft/versions/<版本>/saves/<世界>`（8 层）。
    重目录（assets/libraries/resourcepacks/mods 等）由 scanjob.SKIP_DIRS 剪掉，遍历不会失控。
    """
    out = []
    for wdir in discover_worlds(root, max_depth=max_depth):
        name, version = world_info(wdir)
        try:
            mtime = os.path.getmtime(os.path.join(wdir, "level.dat"))
        except OSError:
            mtime = 0.0
        out.append({"name": name, "dir": wdir, "version": version or "", "mtime": mtime})
    out.sort(key=lambda w: -w["mtime"])
    return out


def default_scan_dirs():
    """常见启动器的存档位置（只查已知模式，不递归全盘扫）。"""
    home = os.path.expanduser("~")
    bases = [os.environ.get("APPDATA") or "", home, os.environ.get("USERPROFILE") or ""]
    mcs = []
    for base in bases:
        if base:
            mcs.append(os.path.join(base, ".minecraft"))
    for base in (os.environ.get("USERPROFILE") or home,):
        for launcher in ("curseforge", "modrinth", "lunarclient", "feather", "prismlauncher"):
            mcs.append(os.path.join(base, launcher))
    found = []
    for mc in mcs:
        for pattern in ("saves", os.path.join("versions", "*", "saves"),
                        os.path.join("instances", "*", ".minecraft", "saves")):
            for d in glob.glob(os.path.join(mc, pattern)):
                if os.path.isdir(d) and d not in found:
                    found.append(d)
    return found


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_config(data):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass          # 配置存不下来不该影响使用


def _top_score(r):
    """「最卡区块」列 `(x,z)=分数` → 分数（解析不出来给 -1，让它们沉底）。"""
    try:
        return int(str(r.top).rsplit("=", 1)[1])
    except (AttributeError, IndexError, ValueError):
        return -1


def _top_coord(r):
    """「最卡区块」卡片只显示坐标 `(x,z)`（分数另有一张卡，两个数不混在一个标签里）。"""
    text = str(getattr(r, "top", "") or "")
    if "=" in text:
        text = text.rsplit("=", 1)[0]
    return text or "—"


# ---------------------------------------------------------------- 对话框（可替换）
class _Dialogs:
    """
    模态对话框包装。

    测试里整对象替换（`gui.dialogs = Stub()`）—— **GUI 测试绝不能弹真模态框**：
    没人点，进程会永久挂死（Tk 版实测挂 240s、退出码 1 且无 traceback）。
    """

    def info(self, title, text):
        QMessageBox.information(None, title, text)

    def warn(self, title, text):
        QMessageBox.warning(None, title, text)

    def error(self, title, text):
        QMessageBox.critical(None, title, text)

    def ask(self, title, text):
        btn = QMessageBox.StandardButton
        return QMessageBox.question(None, title, text, btn.Yes | btn.No) == btn.Yes


dialogs = _Dialogs()


# ---------------------------------------------------------------- 组件工厂
# 只组合 design-system 已列出的组件，不自造新组件；色值一律走角色名。
def _label(text, obj="", mono=False, align=None):
    lb = QLabel(text)
    if obj:
        lb.setObjectName(obj)
    if mono:
        f = QFont(ui_tokens.mono_family())
        f.setPixelSize(ui_tokens.FONT_PX["body"])
        lb.setFont(f)
    if align is not None:
        lb.setAlignment(align)
    return lb


def card(title="", spacing=8):
    """卡片容器（Card：--surface / 圆角 12 / 内边距 16 / 1px --border）。"""
    frame = QFrame()
    frame.setObjectName("Card")
    box = QVBoxLayout(frame)
    box.setContentsMargins(16, 16, 16, 16)
    box.setSpacing(spacing)
    if title:
        box.addWidget(_label(title, "CardTitle"))
    return frame, box


def badge(text, kind="accent"):
    """徽章 / 状态胶囊（高 24）。**必须固定高度**，否则被 layout 撑成方块。"""
    lb = QLabel(text)
    lb.setObjectName({"accent": "Badge", "danger": "BadgeDanger"}.get(kind, "BadgeMuted"))
    lb.setFixedHeight(ui_tokens.CONTROL_H["badge"])
    lb.setAlignment(Qt.AlignCenter)
    return lb


def meter(pct, width=None):
    """占比条 / 进度条（Meter：高 6、圆角 3；轨道用 --border，理由见 ui_tokens 文件头）。"""
    bar = QProgressBar()
    bar.setRange(0, 1000)
    bar.setValue(max(0, min(1000, int(round(pct * 10)))))
    bar.setTextVisible(False)
    bar.setFixedHeight(6)
    if width is not None:
        bar.setFixedWidth(width)
    return bar


def stat_card(value, label):
    """统计块（StatCard：28 字号等宽数值 + 12 字号标签，右对齐）。"""
    frame = QFrame()
    frame.setObjectName("Card")
    box = QVBoxLayout(frame)
    box.setContentsMargins(16, 12, 16, 12)
    box.setSpacing(2)
    box.addWidget(_label(value, "StatValue", mono=True, align=Qt.AlignRight))
    box.addWidget(_label(label, "StatLabel", align=Qt.AlignRight))
    return frame


def note(text):
    """提示条（Note：--accentContainer / 左侧 3px --accent 竖条 / 圆角 8）。"""
    frame = QFrame()
    frame.setObjectName("Note")
    row = QHBoxLayout(frame)
    row.setContentsMargins(12, 10, 12, 10)
    row.setSpacing(10)
    bar = QFrame()
    bar.setObjectName("NoteBar")
    bar.setFixedWidth(3)
    row.addWidget(bar)
    lb = _label(text, "NoteText")
    lb.setWordWrap(True)
    row.addWidget(lb, 1)
    return frame


def make_table(headers, stretch_col=0, right_cols=(), min_section=64):
    """
    数据表（表头 --surface2 / 行高 44 / 行间 1px --border）。

    right_cols = 数值列下标：表头与单元格**都**右对齐。表头默认居中时，
    数值列的表头与数字会错开、整行看着散（设计侧截图才发现）。
    min_section = 节最小宽度：拉伸列在窄窗口下会被压扁到读不出内容
    （实测 1000px 宽时「测试MC存档」被截成「测…」）——给它一个下限，
    宁可让表格在**容器内**横向滚动，也不丢信息。
    """
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    for i in right_cols:
        t.horizontalHeaderItem(i).setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(ui_tokens.CONTROL_H["row"])
    t.setShowGrid(False)
    # 单元格**绝不折行**：宽度不够时用省略号（Qt 的 elide），否则世界名/坐标会被折成两行
    # （窄窗口 1000px 实测："测试MC存档" 折成「测试MC存」+「档」）
    t.setWordWrap(False)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    hh = t.horizontalHeader()
    hh.setMinimumSectionSize(min_section)
    hh.setSectionResizeMode(QHeaderView.Fixed)
    hh.setSectionResizeMode(stretch_col, QHeaderView.Stretch)
    hh.setHighlightSections(False)
    return t


def cell(text, right=False, mono=False, muted=False, danger=False):
    it = QTableWidgetItem(text)
    it.setTextAlignment((Qt.AlignRight if right else Qt.AlignLeft) | Qt.AlignVCenter)
    if mono:
        f = QFont(ui_tokens.mono_family())
        f.setPixelSize(ui_tokens.FONT_PX["body"])
        it.setFont(f)
    if muted:
        it.setForeground(QColor(ui_tokens.C["textMuted"]))
    if danger:
        it.setForeground(QColor(ui_tokens.C["danger"]))
    return it


def meter_cell(pct):
    """表里的一格占比条（自绘需求：Meter + 右对齐百分比，避免把百分比挤成第二行）。"""
    holder = QWidget()
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(8, 0, 8, 0)
    lay.setSpacing(8)
    lay.addWidget(meter(pct), 1)
    pct_lb = _label("%.1f%%" % pct, mono=True, align=Qt.AlignRight | Qt.AlignVCenter)
    pct_lb.setFixedWidth(52)
    lay.addWidget(pct_lb)
    return holder


def bar_cell(pct):
    """表里的一格纯条（榜单评分条）。"""
    holder = QWidget()
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(8, 0, 8, 0)
    lay.addWidget(meter(pct))
    return holder


# ---------------------------------------------------------------- 三个结果视图 + 空态
def fill_compose(table, rows, total_score):
    """
    构成：占比树（大类 → 因子），每行带占比条。← 本版设计的核心。

    行序来自引擎（组块连续：组间、组内都按加权贡献降序）——用户的核心问题是
    "哪种原因占大头"，降序才能让答案落在第一行而不必滚动。
    """
    table.setRowCount(0)
    total = float(total_score) or 1.0
    prev_group = None
    for group, label, count, weight, contrib in rows:
        if group != prev_group:
            prev_group = group
            gsum = sum(x[4] for x in rows if x[0] == group)
            r = table.rowCount()
            table.insertRow(r)
            table.setSpan(r, 0, 1, 5)
            it = cell("%s　　贡献 %.1f%%" % (group, 100.0 * gsum / total))
            it.setBackground(QColor(ui_tokens.C["surface2"]))
            it.setForeground(QColor(ui_tokens.C["textMuted"]))
            table.setItem(r, 0, it)
        r = table.rowCount()
        table.insertRow(r)
        pct = 100.0 * contrib / total
        table.setItem(r, 0, cell(label))
        table.setItem(r, 1, cell("{:,}".format(count), right=True, mono=True))
        table.setItem(r, 2, cell("{:,}".format(weight), right=True, mono=True, muted=True))
        table.setItem(r, 3, cell("{:,}".format(contrib), right=True, mono=True))
        table.setCellWidget(r, 4, meter_cell(pct))
    table.setColumnWidth(1, 80)
    table.setColumnWidth(2, 70)
    table.setColumnWidth(3, 110)
    table.setColumnWidth(4, 210)


def fill_rank(table, rows, labels):
    """榜单：最卡 TOP 区块（只收有卡顿因子的区块，空则走空态）。"""
    table.setRowCount(0)
    top = max((r[2] for r in rows), default=0) or 1
    for i, (cx, cz, score, counts) in enumerate(rows, 1):
        r = table.rowCount()
        table.insertRow(r)
        table.setItem(r, 0, cell(str(i), right=True, mono=True, muted=True))
        table.setItem(r, 1, cell("(%d, %d)" % (cx, cz), mono=True))
        table.setItem(r, 2, cell("{:,}".format(score), right=True, mono=True))
        table.setCellWidget(r, 3, bar_cell(100.0 * score / top))
        detail = "  ".join("%s ×%s" % (labels.get(k, k), "{:,}".format(v))
                           for k, v in sorted(counts.items(), key=lambda kv: -kv[1]) if v > 0)
        table.setItem(r, 4, cell(detail, muted=True))
    table.setColumnWidth(0, 40)
    table.setColumnWidth(1, 110)
    table.setColumnWidth(2, 90)
    table.setColumnWidth(3, 130)


def fill_compare(table, results, sort_col=None, sort_desc=True):
    """
    对比：多「世界 × 维度」一览。

    被跳过的世界**恒排最后**（它们没有可比数值），且用 danger 语义标出（状态点 + 文字，
    不只靠颜色）；原因照实写。排序只换位置，**咬住当前选中项**（交给调用方恢复）。
    """
    ok = [r for r in results if not r.skipped]
    bad = [r for r in results if r.skipped]
    keys = {
        "world": lambda r: r.name,
        "dim": lambda r: r.dim,
        "chunks": lambda r: r.chunks,
        "score": lambda r: r.score,
        "top": _top_score,
        "time": lambda r: r.seconds,
    }
    if sort_col in keys:
        ok.sort(key=keys[sort_col], reverse=sort_desc)

    table.setRowCount(0)
    prev_world = None
    for r in ok + bad:
        row = table.rowCount()
        table.insertRow(row)
        if r.skipped:
            table.setItem(row, 0, cell(r.name, danger=True))
            table.setItem(row, 1, cell("● 跳过", danger=True))
            for c in (2, 3):
                table.setItem(row, c, cell("—", right=True, mono=True, danger=True))
            table.setItem(row, 4, cell(r.skipped, danger=True))
            table.setItem(row, 5, cell("—", right=True, mono=True, danger=True))
            continue
        # 同一世界多维度只写一次世界名（合并观感）
        table.setItem(row, 0, cell(r.name if r.name != prev_world else ""))
        prev_world = r.name
        table.setItem(row, 1, cell(r.dim))
        table.setItem(row, 2, cell("{:,}".format(r.chunks), right=True, mono=True))
        table.setItem(row, 3, cell("{:,}".format(r.score), right=True, mono=True))
        table.setItem(row, 4, cell(r.top, mono=True))
        table.setItem(row, 5, cell(fmt_seconds(r.seconds), right=True, mono=True, muted=True))
    # 世界名给**固定**宽度：拉伸列在窄窗口下会被压到读不出来（实测 1000px 时「测试MC存档」
    # 先是被截成「测…」、加了 minimumSectionSize 后又折成两行）。宽度不够就让「最卡区块」
    # 那列去伸/缩——它是次要信息，允许省略。
    table.setColumnWidth(0, 160)
    table.setColumnWidth(1, 100)
    table.setColumnWidth(2, 100)
    table.setColumnWidth(3, 110)
    table.setColumnWidth(5, 100)
    table.horizontalHeader().setMinimumSectionSize(100)


def empty_view():
    """空态（Empty：居中、24 字号符号 + 一行说明 + 一行补充；不放假数据、不画插画）。"""
    frame = QWidget()
    lay = QVBoxLayout(frame)
    lay.setAlignment(Qt.AlignCenter)
    lay.setSpacing(12)
    icon = _label("○", align=Qt.AlignCenter)
    f = QFont(ui_tokens.ui_family())
    f.setPixelSize(24)
    icon.setFont(f)
    icon.setStyleSheet("color: %s;" % ui_tokens.C["textMuted"])   # 角色值，非裸色值
    lay.addWidget(icon)
    lay.addWidget(_label("选好存档后点「开始扫描」", align=Qt.AlignCenter))
    lay.addWidget(_label("结果会在这里按「构成 / 榜单 / 对比」三种视图展示",
                         "Caption", align=Qt.AlignCenter))
    return frame


# ---------------------------------------------------------------- 主窗口
class ScanGui(QWidget):
    def __init__(self, initial_path=None, parent=None):
        super().__init__(parent)
        self.setObjectName("Root")
        self.setWindowTitle("MC 存档卡顿扫描器")
        self.resize(1180, 760)
        self.setMinimumSize(1000, 660)

        self.cfg = load_config()
        self.q = queue.Queue()
        self.job = None
        self.worker = None
        self.results = []              # 结果清单（与对比表行序一致）
        self.found = []                # 发现到的存档
        self.focus = None              # 当前展示的结果（**咬住对象**，不咬索引）
        self._sort_col = None          # 当前排序列（None = 完成顺序）
        self._sort_desc = True
        self._closing = False
        self._last_log = ""
        self._build(initial_path if initial_path is not None else self.cfg.get("last_path", ""))
        self._restore_geometry()

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._pump)
        self._timer.start()

    # ---------------- 构建界面 ----------------
    def _build(self, initial_path):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._top_bar())

        body = QHBoxLayout()
        body.setContentsMargins(16, 16, 16, 8)
        body.setSpacing(16)
        body.addWidget(self._left_panel(initial_path))
        body.addWidget(self._right_panel(), 1)
        root.addLayout(body, 1)

        root.addWidget(self._status_bar())
        self._build_drawer()

    # ---- 顶栏：标题 + 状态胶囊 ----
    def _top_bar(self):
        bar = QFrame()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(12)
        lay.addWidget(_label("MC 存档卡顿扫描器", "PageTitle"))
        lay.addWidget(_label(ADD_MAP_HINT, "Caption"))
        lay.addStretch(1)
        self.pill = badge("就绪", kind="muted")
        lay.addWidget(self.pill)
        return bar

    # ---- 左栏：任务（存档 / 设置 / 操作 / 进度） ----
    def _left_panel(self, initial_path):
        panel = QWidget()
        panel.setFixedWidth(320)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)

        # ① 选择存档
        c1, b1 = card("① 选择存档")
        self.ent_path = QLineEdit(initial_path or "")
        self.ent_path.setPlaceholderText("存档目录 / saves / .minecraft 均可")
        self.ent_path.setClearButtonEnabled(True)
        b1.addWidget(self.ent_path)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_browse = QPushButton("浏览…")
        self.btn_browse.clicked.connect(self._pick_dir)
        self.btn_paste = QPushButton("粘贴")
        self.btn_paste.clicked.connect(self._paste_path)
        row.addWidget(self.btn_browse)
        row.addWidget(self.btn_paste)
        row.addStretch(1)
        b1.addLayout(row)
        self.lb_found = _label("已发现 0 个存档", "Caption")
        b1.addWidget(self.lb_found)
        self.cmb_found = QComboBox()
        # 下拉框只显示「当前选中项」；把多个名字塞进显示行会被截断（设计侧截图发现）
        self.cmb_found.currentIndexChanged.connect(self._on_found_pick)
        b1.addWidget(self.cmb_found)
        self.btn_find = QPushButton("查找存档")
        self.btn_find.clicked.connect(self._discover)
        b1.addWidget(self.btn_find)
        lay.addWidget(c1)

        # ② 扫描设置
        c2, b2 = card("② 扫描设置")
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(_label("维度"))
        self.cmb_dim = QComboBox()
        self.cmb_dim.addItems([label for _v, label in DIM_CHOICES])
        last_dim = self.cfg.get("dim_label", DIM_CHOICES[0][1])
        for i, (_v, label) in enumerate(DIM_CHOICES):
            if label == last_dim:
                self.cmb_dim.setCurrentIndex(i)
        row2.addWidget(self.cmb_dim, 1)
        b2.addLayout(row2)
        self.chk_txt = QCheckBox("同时输出文字报告 report.txt")
        self.chk_txt.setChecked(bool(self.cfg.get("txt", False)))
        b2.addWidget(self.chk_txt)

        self.btn_adv = QPushButton("▸ 高级（模拟距离 / TOP N / 输出目录）")
        self.btn_adv.setObjectName("FoldToggle")
        self.btn_adv.setCursor(Qt.PointingHandCursor)
        self.btn_adv.clicked.connect(self._toggle_advanced)
        b2.addWidget(self.btn_adv)
        self.adv_panel = self._advanced_panel()
        self.adv_panel.setVisible(bool(self.cfg.get("advanced", False)))
        self.btn_adv.setText(("▾ " if self.adv_panel.isVisible() else "▸ ") +
                             "高级（模拟距离 / TOP N / 输出目录）")
        b2.addWidget(self.adv_panel)
        lay.addWidget(c2)

        # ③ 主操作
        ops = QHBoxLayout()
        ops.setSpacing(8)
        self.btn_start = QPushButton("开始扫描")
        self.btn_start.setObjectName("Primary")
        self.btn_start.setMinimumHeight(ui_tokens.CONTROL_H["button"])
        self.btn_start.clicked.connect(self._start_scan)
        self.btn_cancel = QPushButton("中断")
        self.btn_cancel.setMinimumHeight(ui_tokens.CONTROL_H["button"])
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel)
        ops.addWidget(self.btn_start, 1)
        ops.addWidget(self.btn_cancel)
        lay.addLayout(ops)

        # ④ 扫描进度
        c3, b3 = card("③ 扫描进度")
        self.bar = meter(0.0)
        b3.addWidget(self.bar)
        self.lb_detail = _label("尚未开始", "Caption")
        b3.addWidget(self.lb_detail)
        lay.addWidget(c3)
        lay.addStretch(1)
        return panel

    def _advanced_panel(self):
        """高级项（展开前不占高度）：模拟距离 / TOP N / 输出目录 / 完成后自动打开。"""
        panel = QWidget()
        g = QVBoxLayout(panel)
        g.setContentsMargins(0, 4, 0, 0)
        g.setSpacing(8)

        r1 = QHBoxLayout()
        r1.setSpacing(8)
        r1.addWidget(_label("模拟距离"))
        self.spn_sim = QSpinBox()
        self.spn_sim.setRange(2, 32)
        self.spn_sim.setValue(max(2, min(32, int(self.cfg.get("simdist", 10) or 10))))
        r1.addWidget(self.spn_sim)
        r1.addSpacing(8)
        r1.addWidget(_label("TOP N"))
        self.spn_top = QSpinBox()
        self.spn_top.setRange(1, 200)
        self.spn_top.setValue(max(1, min(200, int(self.cfg.get("top", 20) or 20))))
        r1.addWidget(self.spn_top)
        r1.addStretch(1)
        g.addLayout(r1)

        r2 = QHBoxLayout()
        r2.setSpacing(8)
        self.ent_out = QLineEdit(self.cfg.get("out", "") or os.path.join(_ROOT, "output"))
        r2.addWidget(self.ent_out, 1)
        self.btn_out = QPushButton("浏览…")
        self.btn_out.clicked.connect(self._pick_out)
        r2.addWidget(self.btn_out)
        g.addLayout(r2)

        self.chk_auto = QCheckBox("扫描完成后自动打开第一份地图")
        self.chk_auto.setChecked(bool(self.cfg.get("auto_open", True)))
        g.addWidget(self.chk_auto)
        return panel

    def _toggle_advanced(self):
        show = not self.adv_panel.isVisible()
        self.adv_panel.setVisible(show)
        self.btn_adv.setText(("▾ " if show else "▸ ") + "高级（模拟距离 / TOP N / 输出目录）")

    # ---- 右栏：结果（统计行 / 分段控件 / 内容区） ----
    def _right_panel(self):
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)

        # 统计行：空态时整行不显示（没有数据就没有统计，放空卡片 + "—" 看着像坏了）
        self.stat_row = QWidget()
        srow = QHBoxLayout(self.stat_row)
        srow.setContentsMargins(0, 0, 0, 0)
        srow.setSpacing(16)
        self.stat_chunks = stat_card("0", "区块数")
        self.stat_ticked = stat_card("0", "会被 tick")
        self.stat_score = stat_card("0", "总卡顿分")
        self.stat_top = stat_card("—", "最卡区块")
        for w in (self.stat_chunks, self.stat_ticked, self.stat_score, self.stat_top):
            srow.addWidget(w, 1)
        self.stat_row.setVisible(False)
        lay.addWidget(self.stat_row)

        # 分段控件：〔构成〕〔榜单〕〔对比〕胶囊组
        seg = QHBoxLayout()
        seg.setSpacing(0)
        seg.addWidget(_label("结果", "CardTitle"))
        seg.addSpacing(16)
        self.seg_group = QButtonGroup(self)
        self.seg_group.setExclusive(True)
        last = len(VIEWS) - 1
        self.seg_buttons = {}
        for i, v in enumerate(VIEWS):
            btn = QPushButton(VIEW_LABELS[v])
            btn.setObjectName("SegFirst" if i == 0 else ("SegLast" if i == last else "SegMid"))
            btn.setCheckable(True)
            btn.setMinimumHeight(ui_tokens.CONTROL_H["segment"])
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, name=v: self._show_view(name))
            self.seg_group.addButton(btn, i)
            self.seg_buttons[v] = btn
            seg.addWidget(btn)
        seg.addStretch(1)
        lay.addLayout(seg)
        self.seg_buttons["compose"].setChecked(True)

        # 内容卡片（占据剩余全部高度）
        holder, hb = card()
        self.stack = QStackedWidget()
        self.stack.addWidget(empty_view())                      # 0 空态
        self.compose_table = make_table(
            ["因子", "数量", "权重", "加权贡献", "占比"], stretch_col=0,
            right_cols=(1, 2, 3, 4))
        self.stack.addWidget(self._scrollable(self.compose_table))
        self.rank_table = make_table(
            ["#", "区块坐标", "评分", "", "因子明细"], stretch_col=4, right_cols=(0, 2))
        self.rank_table.cellDoubleClicked.connect(lambda *_: self._open_focus_map())
        self.stack.addWidget(self._scrollable(self.rank_table))
        self.cmp_table = make_table(
            ["世界", "维度", "区块数", "总卡顿分", "最卡区块", "耗时"], stretch_col=4,
            right_cols=(2, 3, 5))
        self.cmp_table.cellDoubleClicked.connect(self._open_row_map)
        self.cmp_table.itemSelectionChanged.connect(self._on_row_selected)
        self.cmp_table.horizontalHeader().sectionClicked.connect(self._sort_by)
        self.stack.addWidget(self._scrollable(self.cmp_table))
        hb.addWidget(self.stack, 1)
        hb.addWidget(note(DISCLAIMER))
        lay.addWidget(holder, 1)
        self._set_content_enabled(False)
        return panel

    @staticmethod
    def _scrollable(widget):
        """表格放进卡片内滚动（窄窗口下容器内滚动是允许的，页面本身不溢出）。"""
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(widget)
        return holder

    # ---- 底部状态条 ----
    def _status_bar(self):
        bar = QFrame()
        bar.setObjectName("Inset")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 8, 16, 8)
        lay.setSpacing(12)
        self.lb_lastlog = _label("就绪：选好存档后点「开始扫描」", "Caption")
        self.lb_lastlog.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay.addWidget(self.lb_lastlog, 1)
        self.btn_log = QPushButton("日志 ▾")
        self.btn_log.setObjectName("Ghost")
        self.btn_log.clicked.connect(self._toggle_drawer)
        self.btn_open_out = QPushButton("打开输出目录")
        self.btn_open_out.setObjectName("Ghost")
        self.btn_open_out.clicked.connect(self._open_out)
        self.btn_open_map = QPushButton("打开地图")
        self.btn_open_map.setObjectName("Ghost")
        self.btn_open_map.clicked.connect(self._open_focus_map)
        for b in (self.btn_log, self.btn_open_out, self.btn_open_map):
            lay.addWidget(b)
        return bar

    # ---- 日志抽屉（本界面唯一的真浮层，单层阴影） ----
    def _build_drawer(self):
        self.drawer = QFrame(self)
        self.drawer.setObjectName("Drawer")
        lay = QVBoxLayout(self.drawer)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(_label("日志", "CardTitle"))
        head.addStretch(1)
        close = QPushButton("收起 ▴")
        close.setObjectName("Ghost")
        close.clicked.connect(self._toggle_drawer)
        head.addWidget(close)
        lay.addLayout(head)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("LogView")
        lay.addWidget(self.log, 1)
        shadow = QGraphicsDropShadowEffect(self.drawer)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 90))
        self.drawer.setGraphicsEffect(shadow)
        self.drawer.setVisible(False)

    def _toggle_drawer(self):
        show = not self.drawer.isVisible()
        self.drawer.setVisible(show)
        self.btn_log.setText("日志 ▴" if show else "日志 ▾")
        if show:
            self._layout_drawer()

    def _layout_drawer(self):
        """抽屉贴在窗口底部、覆盖主体下半部（浮层，不挤压布局）。"""
        if self.drawer is None:
            return
        w = max(320, self.width() - 32)
        h = int(max(200, self.height() * 0.45))
        self.drawer.setGeometry(16, self.height() - h - 48, w, h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_drawer()

    # ---------------- 小交互 ----------------
    def _pick_dir(self):
        path = QFileDialog.getExistingDirectory(
            self, "选择存档目录（可填 .minecraft / saves / 单个世界）",
            self.ent_path.text().strip() or os.path.expanduser("~"))
        if path:
            path = os.path.normpath(path)
            self.ent_path.setText(path)
            self._remember_root(path)       # 记下来，下次不用再手输
            self._start_discover(path)      # 选完顺手找一下里面有哪些世界

    def _pick_out(self):
        path = QFileDialog.getExistingDirectory(
            self, "选择输出目录", self.ent_out.text().strip() or _ROOT)
        if path:
            self.ent_out.setText(os.path.normpath(path))

    def _paste_path(self):
        text = QApplication.clipboard().text().strip().strip('"')
        if text:
            self.ent_path.setText(os.path.normpath(text))

    def _cancel(self):
        if self.job is not None:
            self.job.cancel()
            self.lb_detail.setText("正在中断…（当前世界收尾后停止）")
            self.btn_cancel.setEnabled(False)

    # ---------------- 发现存档 ----------------
    def _discover(self):
        root = self.ent_path.text().strip()
        if root and os.path.isdir(root):
            self._remember_root(root)
            self._start_discover(root)
            return
        # 路径框空着：先扫常见启动器位置，再补上**用户以前用过**的位置
        # （本机玩家常把 MC 装在自选目录，光靠默认候选会一个都找不到）
        roots = [d for d in default_scan_dirs() if os.path.isdir(d)]
        for r in self.cfg.get("scan_roots", []):
            if os.path.isdir(r) and r not in roots:
                roots.append(r)
        if not roots:
            dialogs.info("没找到常见位置",
                         "没探测到常见启动器的存档目录。\n"
                         "请在路径框填 .minecraft 或 saves 目录后再点「查找存档」。")
            return
        self._start_discover_many(roots)

    def _remember_root(self, path):
        """记住用户用过的存档位置（上限 5 个），下次点「查找存档」直接扫它。"""
        path = os.path.normpath(path)
        roots = [r for r in self.cfg.get("scan_roots", []) if r != path]
        roots.insert(0, path)
        self.cfg["scan_roots"] = roots[:5]

    def _start_discover(self, root):
        self._start_discover_many([root])

    def _start_discover_many(self, roots):
        self._log_line("正在查找存档：%s" % "；".join(roots))
        self.lb_detail.setText("正在查找存档…")
        t = threading.Thread(target=self._discover_worker, args=(roots,), daemon=True)
        t.start()

    def _discover_worker(self, roots):
        worlds = []
        for root in roots:
            try:
                worlds.extend(find_worlds(root))
            except Exception:
                continue
        seen, uniq = set(), []
        for w in worlds:
            if w["dir"] in seen:
                continue
            seen.add(w["dir"])
            uniq.append(w)
        uniq.sort(key=lambda w: -w["mtime"])
        self.q.put({"kind": "found", "worlds": uniq, "roots": roots})

    def _on_found_pick(self, idx):
        if 0 <= idx < len(self.found):
            self.ent_path.setText(self.found[idx]["dir"])

    def _on_found(self, ev):
        self.found = ev["worlds"]
        self.cmb_found.blockSignals(True)
        self.cmb_found.clear()
        for w in self.found:
            ver = ("（%s）" % w["version"]) if w["version"] else ""
            self.cmb_found.addItem(w["name"] + ver)
            self.cmb_found.setItemData(self.cmb_found.count() - 1, w["dir"], Qt.ToolTipRole)
        self.cmb_found.blockSignals(False)
        n = len(self.found)
        self.lb_found.setText("已发现 %d 个存档" % n)
        if n:
            self.cmb_found.setCurrentIndex(0)
            self.ent_path.setText(self.found[0]["dir"])
            self.lb_detail.setText("已自动选中最近玩的那个")
            self._log_line("找到 %d 个存档：" % n)
            for w in self.found:
                self._log_line("   %s%s → %s" % (
                    w["name"], ("（%s）" % w["version"]) if w["version"] else "", w["dir"]))
        else:
            self.lb_detail.setText("没找到存档")
            self._log_line("在 %s 下没找到含 level.dat 的世界。" % "；".join(ev["roots"]), warn=True)

    # ---------------- 扫描 ----------------
    def _start_scan(self):
        path = self.ent_path.text().strip()
        if not path:
            dialogs.warn("还没选存档", "请先选择存档目录（或点「查找存档」）。")
            return
        if not os.path.isdir(path):
            dialogs.warn("路径不存在", "这个目录不存在：\n%s" % path)
            return

        dim_label = self.cmb_dim.currentText()
        dim = next((v for v, label in DIM_CHOICES if label == dim_label), "0")
        simdist = max(2, min(32, int(self.spn_sim.value())))
        top = max(1, min(200, int(self.spn_top.value())))
        out = self.ent_out.text().strip() or os.path.join(_ROOT, "output")

        self.results = []
        self.focus = None
        self._sort_col, self._sort_desc = None, True
        self._refresh_all()
        self.bar.setValue(0)
        self.lb_detail.setText("正在准备…")
        self._set_pill("扫描中 0%")
        self._log_line("─" * 60)
        self._log_line("开始扫描：%s（维度 %s）" % (path, dim_label))

        opts = ScanOptions(path=path, out=out, dim=dim, simdist=simdist,
                           top=top, txt=bool(self.chk_txt.isChecked()))
        self.job = ScanJob(opts, on_event=self.q.put, on_log=self._on_log_from_thread)
        self._set_running(True)
        self.worker = threading.Thread(target=self._run_job, daemon=True)
        self.worker.start()

    def _run_job(self):
        try:
            self.job.run()
        except ScanError as exc:
            self.q.put({"kind": "error", "text": str(exc)})
        except Exception:
            self.q.put({"kind": "error", "text": "扫描失败：\n" + traceback.format_exc()})
        finally:
            self.q.put({"kind": "thread_end"})

    def _on_log_from_thread(self, text):
        self.q.put({"kind": "log", "text": text})

    def _set_running(self, running):
        for w in (self.btn_start, self.btn_browse, self.btn_paste, self.btn_find,
                  self.cmb_found, self.cmb_dim, self.chk_txt, self.ent_path,
                  self.ent_out, self.btn_out, self.spn_sim, self.spn_top,
                  self.chk_auto, self.btn_adv):
            w.setEnabled(not running)
        self.btn_cancel.setEnabled(running)
        if running:
            self.lb_detail.setText("扫描中…")

    def _set_pill(self, text, kind="accent"):
        self.pill.setText(text)
        self.pill.setObjectName({"accent": "Badge", "danger": "BadgeDanger"}.get(kind, "BadgeMuted"))
        # 改 objectName 后要重新应用样式表，否则旧规则仍生效
        self.pill.style().unpolish(self.pill)
        self.pill.style().polish(self.pill)

    # ---------------- 事件泵（UI 线程） ----------------
    def _pump(self):
        if self._closing:
            return
        while True:
            try:
                ev = self.q.get_nowait()
            except queue.Empty:
                break
            self._handle(ev)

    def _handle(self, ev):
        kind = ev.get("kind")
        if kind == "log":
            self._log_line(ev["text"])
        elif kind == "phase":
            self.lb_detail.setText(ev["text"])
        elif kind == "plan":
            self._log_line("待解析 %.1f MB" % (ev["total"] / 1048576.0))
        elif kind == "progress":
            self._on_progress(ev)
        elif kind == "world_done":
            self._add_result(ev["result"])
        elif kind == "found":
            self._on_found(ev)
        elif kind == "done":
            self._on_done(ev)
        elif kind == "error":
            self._log_line(ev["text"], warn=True)
            dialogs.error("扫描出错", ev["text"])
            self._set_running(False)
            self._set_pill("扫描出错", kind="danger")
            self.lb_detail.setText("出错了，看日志")
        elif kind == "thread_end":
            self.worker = None
            if self.job is not None and self.job.cancel_requested:
                self._set_running(False)
            elif self.pill.text().startswith("扫描中"):
                self._set_running(False)

    def _on_progress(self, ev):
        pct = min(100.0, ev.get("percent") or 0.0)
        self.bar.setValue(int(round(pct * 10)))
        self._set_pill("扫描中 %.0f%%" % pct)
        bits = []
        if ev.get("world"):
            bits.append("世界 %d/%d %s" % (ev.get("world_i", 0), ev.get("world_n", 0),
                                           ev["world"]))
        if ev.get("dim"):
            bits.append(ev["dim"])
        if ev.get("file"):
            bits.append(ev["file"])
        bits.append("已扫 %s 区块" % "{:,}".format(ev.get("chunks", 0)))
        bits.append("已用 %s" % fmt_seconds(ev.get("elapsed", 0)))
        eta = fmt_eta(ev.get("eta"))
        if eta:
            bits.append(eta)
        self.lb_detail.setText(" · ".join(bits))

    def _add_result(self, r):
        self.results.append(r)
        if self.focus is None and not r.skipped:
            self.focus = r               # 首个有结果的世界自动聚焦
        self._refresh_all()

    # ---------------- 结果刷新 ----------------
    def _refresh_all(self):
        """结果变了就整体刷一遍（行数少、代价小；不做增量更新的复杂度）。"""
        fill_compare(self.cmp_table, self.results, self._sort_col, self._sort_desc)
        self._restore_selection()
        self._refresh_focus()
        self._update_headings()
        self._set_content_enabled(bool(self.results))

    def _restore_selection(self):
        """
        重建表后**咬住**当前选中行（排序/新结果只是换位置，不该把选中丢掉）。

        ⚠️ 这里必须走 `selectionModel()`，**不能用 `QTableWidget.selectRow()`**：
        后者在离屏/未显示窗口下不发效（实测 hasSelection 恒 False），
        真机上表现为"点列头排序后选中项就没了"。
        """
        sm = self.cmp_table.selectionModel()
        if sm is None:
            return
        sm.clearSelection()
        if self.focus is None:
            return
        for row in range(self.cmp_table.rowCount()):
            idx = self._row_index(row)
            if 0 <= idx < len(self.results) and self.results[idx] is self.focus:
                sm.select(self.cmp_table.model().index(row, 0),
                          QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
                return

    def _row_index(self, row):
        """对比表行 → self.results 下标（跳过项恒在尾部，与结果表行序一致）。"""
        order = [r for r in self.results if not r.skipped] + \
                [r for r in self.results if r.skipped]
        if self._sort_col:
            keys = {
                "world": lambda r: r.name, "dim": lambda r: r.dim,
                "chunks": lambda r: r.chunks, "score": lambda r: r.score,
                "top": _top_score, "time": lambda r: r.seconds,
            }
            ok = [r for r in self.results if not r.skipped]
            bad = [r for r in self.results if r.skipped]
            ok.sort(key=keys[self._sort_col], reverse=self._sort_desc)
            order = ok + bad
        if 0 <= row < len(order):
            return self.results.index(order[row])
        return -1

    def _refresh_focus(self):
        """统计行 + 构成 + 榜单 都跟着当前聚焦的结果走。"""
        r = self.focus
        if r is None:
            self.stat_row.setVisible(False)
            return
        self.stat_row.setVisible(True)
        ticked = getattr(r, "ticked", 0) or 0
        pct = (100.0 * ticked / r.chunks) if r.chunks else 0.0
        self._set_stat(self.stat_chunks, "{:,}".format(r.chunks), "区块数")
        self._set_stat(self.stat_ticked, "{:,}".format(ticked), "会被 tick（%.0f%%）" % pct)
        self._set_stat(self.stat_score, "{:,}".format(r.score), "总卡顿分")
        self._set_stat(self.stat_top, _top_coord(r), "最卡区块")
        fill_compose(self.compose_table, r.factors, r.score)
        fill_rank(self.rank_table, r.top_rows, _factor_labels())

    @staticmethod
    def _set_stat(card_widget, value, label):
        box = card_widget.layout()
        box.itemAt(0).widget().setText(value)
        box.itemAt(1).widget().setText(label)

    def _set_content_enabled(self, enabled):
        for btn in self.seg_buttons.values():
            btn.setEnabled(enabled)
        if not enabled:
            self.stack.setCurrentIndex(0)
        else:
            # 有结果就离开空态页 —— 否则扫完右栏还写着"选好存档后点「开始扫描」"（实测踩到）
            self.stack.setCurrentIndex(self._current_view_index())

    def _current_view_index(self):
        """当前选中段对应的页下标（空态是 0，所以往后错一位）。"""
        for i, name in enumerate(VIEWS):
            if self.seg_buttons[name].isChecked():
                return i + 1
        return 1

    def _show_view(self, name):
        if not self.results:
            return
        self.stack.setCurrentIndex(VIEWS.index(name) + 1)

    # ---------------- 结果表排序 ----------------
    def _sort_by(self, col):
        """点列头排序：同列再点反向。被跳过的世界**恒排最后**（它们没有可比数值）。"""
        cols = ("world", "dim", "chunks", "score", "top", "time")
        if not (0 <= col < len(cols)):
            return
        name = cols[col]
        if self._sort_col == name:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col = name
            self._sort_desc = name in ("chunks", "score", "top", "time")   # 数值列默认从大到小
        self._refresh_all()

    def _update_headings(self):
        heads = ("世界", "维度", "区块数", "总卡顿分", "最卡区块", "耗时")
        cols = ("world", "dim", "chunks", "score", "top", "time")
        for i, (c, head) in enumerate(zip(cols, heads)):
            if c == self._sort_col:
                head += " ▼" if self._sort_desc else " ▲"
            self.cmp_table.horizontalHeaderItem(i).setText(head)
            right = c in ("chunks", "score", "time")
            self.cmp_table.horizontalHeaderItem(i).setTextAlignment(
                (Qt.AlignRight if right else Qt.AlignLeft) | Qt.AlignVCenter)

    def _on_row_selected(self):
        rows = self.cmp_table.selectionModel().selectedRows() if self.cmp_table.selectionModel() else []
        if not rows:
            return
        idx = self._row_index(rows[0].row())
        if 0 <= idx < len(self.results):
            self.focus = self.results[idx]      # 咬住选中项，构成/榜单/统计行跟着切
            self._refresh_focus()

    def _on_done(self, ev):
        self._set_running(False)
        self.bar.setValue(1000)
        ok = [r for r in ev["results"] if not r.skipped]
        if ev.get("cancelled"):
            self._set_pill("已中断：已完成 %d 项" % len(ok), kind="muted")
            self.lb_detail.setText("扫描被中断；已完成的结果已出图，双击可打开")
        else:
            self._set_pill("已完成 %d 项" % len(ok))
            self.lb_detail.setText("扫描完成，输出目录：%s" % ev.get("out", ""))
        for line in ev["text"].splitlines():
            self._log_line(line)
        self.cfg["last_out"] = ev.get("out", "")
        if ok and self.chk_auto.isChecked():
            self._open_path(ok[0].map_path)
        elif not ok:
            dialogs.info("没有结果", "这次没扫出可用结果 —— 看看日志里的「跳过」原因。")

    # ---------------- 打开动作 ----------------
    def _open_path(self, path):
        if not path or not os.path.exists(path):
            dialogs.info("打不开", "文件不存在：\n%s" % path)
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(path)
            else:
                import webbrowser
                webbrowser.open("file://" + path)
        except OSError as exc:
            dialogs.warn("打不开", "%s\n%s" % (path, exc))

    def _open_out(self):
        out = self.ent_out.text().strip() or os.path.join(_ROOT, "output")
        if not os.path.isdir(out):
            dialogs.info("还没有输出", "输出目录还不存在：\n%s" % out)
            return
        self._open_path(out)

    def _open_focus_map(self):
        """打开当前聚焦（或选中行）的那份地图。"""
        r = self.focus
        if r is None:
            dialogs.info("还没有结果", "先扫一次，或先在「对比」里选一行。")
            return
        if not r.map_path:
            # 被跳过的世界没有 map.html —— 说清楚为什么，而不是弹"文件不存在"
            dialogs.info("这一项没有地图", "%s\n%s" % (r.name, r.skipped or "该项没有输出地图"))
            return
        self._open_path(r.map_path)

    def _open_row_map(self, row, _col=0):
        """对比表双击某行 → 打开那一行的地图。"""
        idx = self._row_index(row)
        if 0 <= idx < len(self.results):
            r = self.results[idx]
            if not r.map_path:
                dialogs.info("这一项没有地图",
                             "%s\n%s" % (r.name, r.skipped or "该项没有输出地图"))
                return
            self._open_path(r.map_path)

    # ---------------- 日志 ----------------
    def _log_line(self, text, warn=False):
        stamp = time.strftime("%H:%M:%S ")
        self._last_log = stamp + str(text)
        short = self._last_log if len(self._last_log) <= 64 else self._last_log[:63] + "…"
        self.lb_lastlog.setText(short)
        self.lb_lastlog.setToolTip(self._last_log)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(ui_tokens.C["danger"] if warn else ui_tokens.C["text"]))
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(stamp + str(text) + "\n", fmt)
        self.log.setTextCursor(cursor)
        self.log.ensureCursorVisible()

    # ---------------- 键盘 ----------------
    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Escape:
            if self.drawer.isVisible():
                self._toggle_drawer()           # Esc 退出当前上下文（先收浮层）
                return
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            # Enter 触发主操作（不在输入框里时）
            if not isinstance(self.focusWidget(), (QLineEdit, QSpinBox)) and \
                    self.btn_start.isEnabled():
                self._start_scan()
                return
        super().keyPressEvent(event)

    # ---------------- 收尾 ----------------
    def _restore_geometry(self):
        geo = self.cfg.get("geometry") or ""
        try:
            if "x" in geo:
                w, h = geo.split("x", 1)
                self.resize(max(1000, int(w)), max(660, int(h)))
        except (ValueError, TypeError):
            pass

    def _save_cfg(self):
        self.cfg.update({
            "last_path": self.ent_path.text().strip(),
            "out": self.ent_out.text().strip(),
            "dim_label": self.cmb_dim.currentText(),
            "simdist": int(self.spn_sim.value()),
            "top": int(self.spn_top.value()),
            "txt": bool(self.chk_txt.isChecked()),
            "advanced": bool(self.adv_panel.isVisible()),
            "auto_open": bool(self.chk_auto.isChecked()),
            "geometry": "%dx%d" % (self.width(), self.height()),
        })
        save_config(self.cfg)

    def closeEvent(self, event):
        if self.worker is not None and self.worker.is_alive():
            if not dialogs.ask("正在扫描", "扫描还没结束，中断并退出？\n（已完成的结果会保留在输出目录）"):
                event.ignore()
                return
            if self.job is not None:
                self.job.cancel()
            self.worker.join(timeout=5)
        self._closing = True
        self._timer.stop()          # 先停定时器再销毁窗口
        self._save_cfg()
        event.accept()


_FACTOR_LABELS = None


def _factor_labels():
    """因子 key → 中文名（榜单明细用）；只读一次 factors 表。"""
    global _FACTOR_LABELS
    if _FACTOR_LABELS is None:
        from chunklag.mapdata import factor_labels
        _FACTOR_LABELS = factor_labels()
    return _FACTOR_LABELS


# ---------------------------------------------------------------- 启动
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    app = QApplication.instance() or QApplication(argv[:1] or ["mcchunklag"])
    ui_tokens.apply(app)                    # 跟随系统明暗 + 装字体 + 设样式表
    win = ScanGui(initial_path=argv[0] if argv else None)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
