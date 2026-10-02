# -*- coding: utf-8 -*-
"""Qt 设计令牌层 —— 全项目**唯一的色值定义处**，界面文件只引用角色名。

来源：`D:\\办事软件\\DS工作区_UI\\projects\\ui_002_MCChunkLag\\prototype\\tokens.py`
（设计工作区交付的设计稿，与落地形态同技术栈）。权威色值清单：
`D:\\办事软件\\DS工作区_UI\\design-system\\设计系统最小清单.md`（15 个颜色角色）。
落地时**逐字节沿用**其色值、尺度、QSS 与字体兜底，只改模块说明与导入位置。

用法：
    from chunklag import ui_tokens
    mode = ui_tokens.apply(app)      # 装字体 + 设样式表，返回生效模式（light / dark）
    ui_tokens.C["accent"]            # 需要裸色值时（自绘占比条 / 表格前景），仅限消费者读取

## 15 个颜色角色
`bg` / `surface` / `surface2` / `surface3` / `border` / `text` / `textMuted` /
`accent` / `onAccent` / `accentContainer` / `onAccentContainer` /
`danger` / `onDanger` / `dangerContainer` / `onDangerContainer`

## 两条 Qt 落地修正（设计侧实测踩坑，均有截图证据）
- **Meter 轨道不用 `--surface3`**：它铺在卡片 `--surface` 上只差一档、轨道隐形（填充像悬空）。
  本文件用 `--border`（有意偏离 design-system 规格，已回报设计工作区）。
- **徽章必须由调用方 `setFixedHeight(24)`**：QLabel 加圆角**不会**自动成胶囊，
  不固定高度会被 layout 撑成方块（实测被撑成 130×130）。
"""
from __future__ import annotations

import os
from string import Template

from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication

# ============================== 颜色（15 角色，唯一色值定义处） ==============================

LIGHT = {
    "bg": "#F9F9FE",
    "surface": "#F4F3F9",
    "surface2": "#EEEDF3",
    "surface3": "#E8E7ED",
    "border": "#C7C5D2",
    "text": "#1C1B1F",
    "textMuted": "#474650",
    "accent": "#4954B8",
    "onAccent": "#FFFFFF",
    "accentContainer": "#E3DFFF",
    "onAccentContainer": "#091748",
    "danger": "#B3261E",
    "onDanger": "#FFFFFF",
    "dangerContainer": "#F9DEDC",
    "onDangerContainer": "#410E0B",
}

DARK = {
    "bg": "#131317",
    "surface": "#1C1B1F",
    "surface2": "#201F23",
    "surface3": "#2A292E",
    "border": "#474650",
    "text": "#E3E2E7",
    "textMuted": "#C7C5D2",
    "accent": "#C5C0FB",
    "onAccent": "#0C2977",
    "accentContainer": "#283D9C",
    "onAccentContainer": "#E3DFFF",
    "danger": "#F2B8B5",
    "onDanger": "#601410",
    "dangerContainer": "#8C1D18",
    "onDangerContainer": "#F9DEDC",
}

MODE = "light"
C = dict(LIGHT)  # 当前生效色表（原地更新）

# ============================== 尺度（对齐 design-system 刻度） ==============================

RADIUS = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "full": 999}
SPACE = {"1": 4, "2": 8, "3": 12, "4": 16, "6": 24, "8": 32}
FONT_PX = {"caption": 12, "body": 14, "cardTitle": 16, "section": 20, "pageTitle": 28}
# 控件高度：分段 32 / 按钮 36 / 表格行 44 / 徽章 24（design-system 组件规格）
CONTROL_H = {"segment": 32, "button": 36, "row": 44, "badge": 24}

# ============================== 字体 ==============================

UI_FONTS = ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Noto Sans CJK SC")
MONO_FONTS = ("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier New")
# 离屏渲染下系统字库可能为空（实测只有 2 个族）→ 退到按字体文件加载
FONT_FILES = (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhl.ttc",
              r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\DejaVuSans.ttf")

_families: dict[str, str] = {}


def _pick(candidates: tuple[str, ...], allow_file: bool) -> str:
    key = candidates[0]
    if key in _families:
        return _families[key]
    have = set(QFontDatabase.families())
    name = next((n for n in candidates if n in have), "")
    if not name and allow_file:
        for path in FONT_FILES:
            if not os.path.isfile(path):
                continue
            fid = QFontDatabase.addApplicationFont(path)
            if fid != -1:
                loaded = QFontDatabase.applicationFontFamilies(fid)
                if loaded:
                    name = loaded[0]
                    break
    _families[key] = name
    return name


def ui_family() -> str:
    # ⚠️ 没有 QGuiApplication 实例时**绝不能碰 QFontDatabase**：Qt 会直接 abort（0xC0000409），
    # 表现为"import 阶段无声崩溃、连 print 都没有"。本模块在导入时会算一次 QSS，
    # 所以这里必须兜底成候选名，等 apply()（app 已存在）时再真正解析。
    if QGuiApplication.instance() is None:
        return UI_FONTS[0]
    return _pick(UI_FONTS, True)


def mono_family() -> str:
    if QGuiApplication.instance() is None:
        return MONO_FONTS[0]
    return _pick(MONO_FONTS, False) or ui_family()


def install_fonts(app) -> str:
    family = ui_family()
    if family:
        font = QFont(family)
        font.setPixelSize(FONT_PX["body"])
        app.setFont(font)
    return family


# ============================== 样式表 ==============================

_QSS = Template("""
QWidget { color: $text; font-family: "$ui_font"; font-size: ${f_body}px; }
QMainWindow, QDialog, #Root { background: $bg; }

/* 容器：三级 surface 表达层级（硬约束 6：不靠阴影堆叠） */
#Card   { background: $surface;  border: 1px solid $border; border-radius: ${r_md}px; }
#Inset  { background: $surface2; border: 1px solid $border; border-radius: ${r_sm}px; }
#Raised { background: $surface3; border-radius: ${r_sm}px; }

/* 文本层级 */
#PageTitle  { font-size: ${f_section}px; font-weight: 600; }
#CardTitle  { font-size: ${f_cardTitle}px; font-weight: 600; }
#Caption    { font-size: ${f_caption}px; color: $textMuted; }
#StatValue  { font-family: "$mono_font"; font-size: ${f_pageTitle}px; font-weight: 600; }
#StatLabel  { font-size: ${f_caption}px; color: $textMuted; }
#Num        { font-family: "$mono_font"; }

/* 按钮：主 --accent / 次描边 / 幽灵 / 危险 */
QPushButton {
    background: $surface; border: 1px solid $border; border-radius: ${r_sm}px;
    padding: 6px 16px; min-height: ${btn_h}px; color: $text;
}
QPushButton:hover { background: $surface3; }
QPushButton:disabled { color: $textMuted; background: $surface2; border-color: $border; }
QPushButton#Primary { background: $accent; color: $onAccent; border: none; font-weight: 600; }
QPushButton#Primary:hover { background: $accentContainer; color: $onAccentContainer; }
QPushButton#Primary:disabled { background: $surface3; color: $textMuted; }
QPushButton#Danger { color: $danger; border-color: $danger; }
QPushButton#Danger:hover { background: $dangerContainer; color: $onDangerContainer; }
QPushButton#Ghost { background: transparent; border: none; color: $accent; padding: 4px 8px; min-height: 0; }
QPushButton#Ghost:hover { background: $surface3; }
/* 折叠项（「▸ 高级…」）：无底无框 + 次要文字色，默认收起时不占高度 */
QPushButton#FoldToggle {
    background: transparent; border: none; color: $textMuted; text-align: left;
    font-size: ${f_caption}px; padding: 2px 0px; min-height: 0;
}
QPushButton#FoldToggle:hover { color: $accent; }

/* 分段控件：胶囊组（段间无缝）——选中项 --accentContainer
   ⚠️ QSS 里 border-radius 简写与 border-*-radius 长写**混用不可靠**（实测单端圆角不生效），
   所以圆角一律长写、四角写全 */
QPushButton#Segment, QPushButton#SegFirst, QPushButton#SegMid, QPushButton#SegLast {
    background: $surface2; border: 1px solid $border;
    border-top-left-radius: ${r_sm}px; border-top-right-radius: ${r_sm}px;
    border-bottom-left-radius: ${r_sm}px; border-bottom-right-radius: ${r_sm}px;
    padding: 4px 14px; min-height: ${seg_h}px; color: $textMuted;
}
QPushButton#Segment:checked, QPushButton#SegFirst:checked,
QPushButton#SegMid:checked, QPushButton#SegLast:checked {
    background: $accentContainer; color: $onAccentContainer;
    border-color: $accentContainer; font-weight: 600;
}
QPushButton#SegFirst {
    border-top-left-radius: ${seg_r}px; border-bottom-left-radius: ${seg_r}px;
    border-top-right-radius: 0px; border-bottom-right-radius: 0px;
}
QPushButton#SegMid {
    border-top-left-radius: 0px; border-top-right-radius: 0px;
    border-bottom-left-radius: 0px; border-bottom-right-radius: 0px;
}
QPushButton#SegLast {
    border-top-right-radius: ${seg_r}px; border-bottom-right-radius: ${seg_r}px;
    border-top-left-radius: 0px; border-bottom-left-radius: 0px;
}

/* 输入 */
QLineEdit, QComboBox, QSpinBox {
    background: $surface; border: 1px solid $border; border-radius: ${r_sm}px;
    padding: 6px 10px; min-height: 22px; color: $text;
    selection-background-color: $accent; selection-color: $onAccent;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: $accent; }
QLineEdit:disabled { color: $textMuted; background: $surface2; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background: $surface; border: 1px solid $border; border-radius: ${r_sm}px;
    selection-background-color: $accentContainer; selection-color: $onAccentContainer; outline: none;
}
/* 复选框：**必须自定义 indicator**，否则暗色下 Qt 用浅色默认样式，
   未选中的框会渲染成纯白实心块（亮色截图看不出来，暗色一眼看到） */
QCheckBox { color: $text; spacing: 6px; }
QCheckBox::indicator {
    width: 16px; height: 16px; background: $surface;
    border: 1px solid $border; border-radius: ${r_xs}px;
}
QCheckBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; }

/* 表格：表头 --surface2、行高 44、行间 1px --border、数值右对齐(由调用方设) */
QTableView {
    background: $surface; border: 1px solid $border; border-radius: ${r_sm}px;
    gridline-color: transparent; outline: none;
    selection-background-color: $accentContainer; selection-color: $onAccentContainer;
}
QTableView::item { padding: 0px 8px; border-bottom: 1px solid $border; }
QHeaderView::section {
    background: $surface2; color: $textMuted; border: none;
    border-bottom: 1px solid $border; padding: 8px; font-weight: 600;
}
QTableCornerButton::section { background: $surface2; border: none; }

/* 进度条：轨道用 --border（偏离 design-system 的 --surface3，理由见文件头） */
QProgressBar { background: $border; border: none; border-radius: 3px; text-align: center; }
QProgressBar::chunk { background: $accent; border-radius: 3px; }

/* 徽章：调用方须 setFixedHeight(24)，否则被 layout 撑成方块 */
#Badge {
    background: $accentContainer; color: $onAccentContainer;
    border-radius: ${badge_r}px; font-size: ${f_caption}px; font-weight: 600;
    padding: 0px 10px;
}
#BadgeDanger {
    background: $dangerContainer; color: $onDangerContainer;
    border-radius: ${badge_r}px; font-size: ${f_caption}px; font-weight: 600;
    padding: 0px 10px;
}
#BadgeMuted {
    background: $surface3; color: $textMuted;
    border-radius: ${badge_r}px; font-size: ${f_caption}px; font-weight: 600;
    padding: 0px 10px;
}

/* 提示条：左 3px 竖条用独立 QFrame 画（避开 QSS 单边边框+圆角的渲染坑） */
#Note { background: $accentContainer; border-radius: ${r_sm}px; }
#NoteBar { background: $accent; border-radius: 1px; }
#NoteText { color: $onAccentContainer; font-size: ${f_caption}px; }

/* 占比条（自绘组件，见原型里的 RatioBar） */
#RatioTrack { background: $border; border-radius: 3px; }
#RatioFill  { background: $accent; border-radius: 3px; }

/* 日志抽屉：唯一的真浮层（单层 QGraphicsDropShadowEffect 由代码加） */
#Drawer { background: $surface; border: 1px solid $border; border-radius: ${r_md}px; }
QTextEdit#LogView {
    background: $surface2; border: 1px solid $border; border-radius: ${r_sm}px;
    color: $text; padding: 6px;
}

QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $border; border-radius: ${r_full}px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: $textMuted; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: $border; border-radius: ${r_full}px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0px; width: 0px; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QSplitter::handle { background: transparent; width: 8px; }
QStatusBar { background: $surface; border-top: 1px solid $border; color: $textMuted; }
QStatusBar::item { border: none; }
QToolTip {
    background: $text; color: $surface; border: none;
    padding: 4px 8px; border-radius: ${r_xs}px;
}
""")


def build_qss(colors: dict, mode: str) -> str:
    tokens = dict(colors)
    tokens.update({f"r_{k}": str(v) for k, v in RADIUS.items()})
    tokens.update({f"s_{k}": str(v) for k, v in SPACE.items()})
    tokens.update({f"f_{k}": str(v) for k, v in FONT_PX.items()})
    tokens["btn_h"] = str(CONTROL_H["button"] - 14)   # padding 上下各 6 + 内容高
    tokens["seg_h"] = str(CONTROL_H["segment"] - 10)
    tokens["badge_r"] = str(CONTROL_H["badge"] // 2)  # 胶囊 = 高度一半
    tokens["seg_r"] = str(CONTROL_H["segment"] // 2)  # 分段控件两端胶囊半径
    tokens["ui_font"] = ui_family()
    tokens["mono_font"] = mono_family()
    return _QSS.substitute(tokens)


def set_mode(mode: str) -> str:
    global QSS, MODE
    mode = "dark" if str(mode).strip().lower() == "dark" else "light"
    C.clear()
    C.update(DARK if mode == "dark" else LIGHT)
    MODE = mode
    QSS = build_qss(C, mode)
    return mode


def resolve_mode(app=None) -> str:
    """跟随系统；取不到就浅色。"""
    gui_app = app or QGuiApplication.instance()
    if gui_app is not None:
        try:
            from PySide6.QtCore import Qt

            scheme = gui_app.styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return "dark"
        except Exception:
            pass
    return "light"


def apply(app) -> str:
    """启动时调用：定模式 → 装字体 → 设样式表。返回生效模式。"""
    mode = set_mode(resolve_mode(app))
    install_fonts(app)
    app.setStyleSheet(QSS)
    return mode


QSS = build_qss(LIGHT, "light")
