# -*- coding: utf-8 -*-
"""
扫描存档 GUI —— Tkinter（Python 标准库，零依赖）。

流程：选存档 → 选维度 → 开始扫描（后台线程 + 实时进度）→ 结果一览（双击开地图）。
报告仍走 HTML：每「世界 × 维度」一份 `map.html`，界面里**不内嵌渲染**，所以再大的存档也不卡。

启动：
  双击 `启动界面.bat`（可把存档文件夹拖到 bat 上，直接把路径带进来）
  或 `python gui.py [存档目录]`

线程模型：扫描跑在工作线程，只往 `queue` 丢事件；UI 线程每 100ms 排空队列刷新控件
（Tk 不是线程安全的，子线程绝不碰控件）。
"""
import glob
import json
import os
import queue
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

# main.py / scan.py 在项目根，而本文件可能被 `python chunklag/gui.py` 这样直接跑
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from chunklag.scanjob import (DIM_CHOICES, ScanError, ScanJob, ScanOptions,   # noqa: E402
                              discover_worlds, world_info)

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".mcchunklag.json")


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


def find_worlds(root, max_depth=6):
    """
    列出 root 下的存档世界：名称 / 路径 / 版本 / 最后修改时间，按最近游玩倒序。
    深度放宽到 6 —— 兼容 `Minecraft/hmcl/.minecraft/versions/<版本>/saves/<世界>` 这类深层嵌套。
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


# ---------------------------------------------------------------- 界面
class ScanGui:
    def __init__(self, root, initial_path=None):
        self.root = root
        self.cfg = load_config()
        self.q = queue.Queue()
        self.job = None
        self.worker = None
        self.results = []              # Treeview 行序 → WorldResult
        self.found = []                # 发现到的存档
        self._closing = False
        self._build(initial_path or self.cfg.get("last_path", ""))
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.report_callback_exception = self._on_tk_error
        self.root.after(100, self._pump)

    # ---------------- 构建界面 ----------------
    def _build(self, initial_path):
        root = self.root
        root.title("MC 存档卡顿扫描器")
        root.minsize(880, 620)
        root.geometry(self.cfg.get("geometry") or "940x720")
        root.columnconfigure(0, weight=1)
        for r, w in ((4, 1), (5, 2)):
            root.rowconfigure(r, weight=w)

        # ① 存档
        box1 = ttk.LabelFrame(root, text=" ① 选择存档 ")
        box1.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        box1.columnconfigure(1, weight=1)

        ttk.Label(box1, text="存档路径").grid(row=0, column=0, sticky="w", padx=(8, 4), pady=5)
        self.var_path = tk.StringVar(value=initial_path)
        self.ent_path = ttk.Entry(box1, textvariable=self.var_path)
        self.ent_path.grid(row=0, column=1, sticky="ew", pady=5)
        self.btn_browse = ttk.Button(box1, text="浏览…", width=8, command=self._pick_dir)
        self.btn_browse.grid(row=0, column=2, padx=4, pady=5)
        ttk.Button(box1, text="粘贴", width=6,
                   command=self._paste_path).grid(row=0, column=3, padx=(0, 8), pady=5)

        ttk.Label(box1, text="已发现存档").grid(row=1, column=0, sticky="w", padx=(8, 4), pady=(0, 5))
        self.var_found = tk.StringVar()
        self.cmb_found = ttk.Combobox(box1, textvariable=self.var_found, state="readonly")
        self.cmb_found.grid(row=1, column=1, sticky="ew", pady=(0, 5))
        self.cmb_found.bind("<<ComboboxSelected>>", self._on_found_pick)
        self.btn_find = ttk.Button(box1, text="查找存档", width=10, command=self._discover)
        self.btn_find.grid(row=1, column=2, columnspan=2, padx=4, pady=(0, 5), sticky="ew")

        # ② 扫描选项
        box2 = ttk.LabelFrame(root, text=" ② 扫描选项 ")
        box2.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
        box2.columnconfigure(5, weight=1)

        ttk.Label(box2, text="扫描维度").grid(row=0, column=0, sticky="w", padx=(8, 4), pady=5)
        self.var_dim = tk.StringVar(value=self.cfg.get("dim_label", DIM_CHOICES[0][1]))
        self.cmb_dim = ttk.Combobox(box2, textvariable=self.var_dim, state="readonly",
                                    width=12, values=[label for _v, label in DIM_CHOICES])
        self.cmb_dim.grid(row=0, column=1, sticky="w", pady=5)

        self.var_txt = tk.BooleanVar(value=bool(self.cfg.get("txt", False)))
        self.chk_txt = ttk.Checkbutton(box2, text="同时输出文字报告 report.txt",
                                       variable=self.var_txt)
        self.chk_txt.grid(row=0, column=2, columnspan=2, sticky="w", padx=12, pady=5)

        self.var_adv = tk.BooleanVar(value=bool(self.cfg.get("advanced", False)))
        ttk.Checkbutton(box2, text="高级选项", variable=self.var_adv,
                        command=self._toggle_advanced).grid(row=0, column=4, columnspan=2,
                                                            sticky="e", padx=8, pady=5)

        self.adv = ttk.Frame(box2)
        self.adv.grid(row=1, column=0, columnspan=6, sticky="ew")
        self.adv.columnconfigure(5, weight=1)
        ttk.Label(self.adv, text="模拟距离").grid(row=0, column=0, sticky="w", padx=(8, 2), pady=(0, 6))
        self.var_sim = tk.IntVar(value=int(self.cfg.get("simdist", 10) or 10))
        self.spn_sim = ttk.Spinbox(self.adv, from_=2, to=32, width=5, textvariable=self.var_sim)
        self.spn_sim.grid(row=0, column=1, sticky="w", pady=(0, 6))
        ttk.Label(self.adv, text="TOP N").grid(row=0, column=2, sticky="w", padx=(10, 2), pady=(0, 6))
        self.var_top = tk.IntVar(value=int(self.cfg.get("top", 20) or 20))
        self.spn_top = ttk.Spinbox(self.adv, from_=1, to=200, width=5, textvariable=self.var_top)
        self.spn_top.grid(row=0, column=3, sticky="w", pady=(0, 6))
        ttk.Label(self.adv, text="输出目录").grid(row=1, column=0, sticky="w", padx=(8, 2), pady=(0, 6))
        self.var_out = tk.StringVar(value=self.cfg.get("out", "") or os.path.join(_ROOT, "output"))
        self.ent_out = ttk.Entry(self.adv, textvariable=self.var_out)
        self.ent_out.grid(row=1, column=1, columnspan=4, sticky="ew", pady=(0, 6))
        self.btn_out = ttk.Button(self.adv, text="浏览…", width=8, command=self._pick_out)
        self.btn_out.grid(row=1, column=5, sticky="e", padx=4, pady=(0, 6))
        self._toggle_advanced()

        # ③ 操作
        bar = ttk.Frame(root)
        bar.grid(row=2, column=0, sticky="ew", padx=10, pady=4)
        bar.columnconfigure(2, weight=1)
        self.btn_start = ttk.Button(bar, text="开始扫描", command=self._start, width=12)
        self.btn_start.grid(row=0, column=0)
        self.btn_cancel = ttk.Button(bar, text="中断扫描", command=self._cancel,
                                     width=12, state="disabled")
        self.btn_cancel.grid(row=0, column=1, padx=6)
        self.var_state = tk.StringVar(value="就绪：选好存档后点「开始扫描」")
        ttk.Label(bar, textvariable=self.var_state, foreground="#1a5").grid(
            row=0, column=2, sticky="e")

        # ④ 进度
        box3 = ttk.LabelFrame(root, text=" ③ 扫描进度 ")
        box3.grid(row=3, column=0, sticky="ew", padx=10, pady=4)
        box3.columnconfigure(0, weight=1)
        self.bar = ttk.Progressbar(box3, mode="determinate", maximum=100.0)
        self.bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        self.var_pct = tk.StringVar(value="0%")
        ttk.Label(box3, textvariable=self.var_pct, width=6).grid(row=0, column=1, padx=(0, 8),
                                                                 pady=(6, 2))
        self.var_detail = tk.StringVar(value="尚未开始")
        ttk.Label(box3, textvariable=self.var_detail, anchor="w",
                  foreground="#444").grid(row=1, column=0, columnspan=2, sticky="ew",
                                          padx=8, pady=(0, 6))

        # ⑤ 日志
        box4 = ttk.LabelFrame(root, text=" 日志 ")
        box4.grid(row=4, column=0, sticky="nsew", padx=10, pady=4)
        box4.columnconfigure(0, weight=1)
        box4.rowconfigure(0, weight=1)
        self.log = ScrolledText(box4, height=8, wrap="word", state="disabled",
                                background="#fbfbfb")
        self.log.grid(row=0, column=0, sticky="nsew", padx=8, pady=6)
        self.log.tag_configure("warn", foreground="#b35c00")

        # ⑥ 结果
        box5 = ttk.LabelFrame(root, text=" 结果一览（双击一行用浏览器打开该地图） ")
        box5.grid(row=5, column=0, sticky="nsew", padx=10, pady=4)
        box5.columnconfigure(0, weight=1)
        box5.rowconfigure(0, weight=1)
        cols = ("world", "dim", "chunks", "score", "top", "time")
        heads = ("世界", "维度", "区块数", "总卡顿分", "最卡区块", "耗时")
        widths = (200, 110, 80, 90, 140, 80)
        self.tree = ttk.Treeview(box5, columns=cols, show="headings", height=6)
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="center" if c != "world" else "w")
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=6)
        sb = ttk.Scrollbar(box5, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=6)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.tag_configure("skip", foreground="#b35c00")
        self.tree.bind("<Double-1>", self._open_selected_map)

        # ⑦ 底部
        foot = ttk.Frame(root)
        foot.grid(row=6, column=0, sticky="ew", padx=10, pady=(4, 10))
        foot.columnconfigure(2, weight=1)
        ttk.Button(foot, text="打开输出目录", command=self._open_out).grid(row=0, column=0)
        ttk.Button(foot, text="打开选中地图", command=self._open_selected_map).grid(
            row=0, column=1, padx=6)
        self.var_auto = tk.BooleanVar(value=bool(self.cfg.get("auto_open", True)))
        ttk.Checkbutton(foot, text="扫描完成后自动打开第一份地图",
                        variable=self.var_auto).grid(row=0, column=3, sticky="e")

    # ---------------- 小交互 ----------------
    def _toggle_advanced(self):
        if self.var_adv.get():
            self.adv.grid()
        else:
            self.adv.grid_remove()

    def _pick_dir(self):
        path = filedialog.askdirectory(title="选择存档目录（可填 .minecraft / saves / 单个世界）",
                                       initialdir=self.var_path.get() or os.path.expanduser("~"))
        if path:
            self.var_path.set(os.path.normpath(path))
            self._start_discover(path)      # 选完顺手找一下里面有哪些世界

    def _pick_out(self):
        path = filedialog.askdirectory(title="选择输出目录",
                                       initialdir=self.var_out.get() or _ROOT)
        if path:
            self.var_out.set(os.path.normpath(path))

    def _paste_path(self):
        try:
            text = self.root.clipboard_get().strip().strip('"')
        except Exception:
            text = ""
        if text:
            self.var_path.set(os.path.normpath(text))

    def _cancel(self):
        if self.job is not None:
            self.job.cancel()
            self.var_state.set("正在中断…（当前世界收尾后停止）")
            self.btn_cancel.configure(state="disabled")

    # ---------------- 发现存档 ----------------
    def _discover(self):
        root = self.var_path.get().strip()
        if root and os.path.isdir(root):
            self._start_discover(root)
            return
        roots = [d for d in default_scan_dirs() if os.path.isdir(d)]
        if not roots:
            messagebox.showinfo("没找到常见位置",
                                "没探测到常见启动器的存档目录。\n"
                                "请在路径框填 .minecraft 或 saves 目录后再点「查找存档」。")
            return
        self._start_discover_many(roots)

    def _start_discover(self, root):
        self._start_discover_many([root])

    def _start_discover_many(self, roots):
        self._log_line("正在查找存档：%s" % "；".join(roots))
        self.var_state.set("正在查找存档…")
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

    def _on_found_pick(self, _event=None):
        idx = self.cmb_found.current()
        if 0 <= idx < len(self.found):
            self.var_path.set(self.found[idx]["dir"])

    def _on_found(self, ev):
        self.found = ev["worlds"]
        labels = []
        for w in self.found:
            ver = ("　·　%s" % w["version"]) if w["version"] else ""
            labels.append("%s%s　—　%s" % (w["name"], ver, w["dir"]))
        self.cmb_found.configure(values=labels)
        if labels:
            self.cmb_found.current(0)
            self.var_path.set(self.found[0]["dir"])
            self.var_state.set("找到 %d 个存档，已自动选中最近玩的那个" % len(labels))
            self._log_line("找到 %d 个存档：" % len(labels))
            for w in self.found:
                self._log_line("   %s%s → %s" % (w["name"], ("（%s）" % w["version"]) if w["version"] else "", w["dir"]))
        else:
            self.var_state.set("没找到存档")
            self._log_line("在 %s 下没找到含 level.dat 的世界。" % "；".join(ev["roots"]), warn=True)

    # ---------------- 扫描 ----------------
    def _start(self):
        path = self.var_path.get().strip()
        if not path:
            messagebox.showwarning("还没选存档", "请先选择存档目录（或点「查找存档」）。")
            return
        if not os.path.isdir(path):
            messagebox.showwarning("路径不存在", "这个目录不存在：\n%s" % path)
            return

        dim_label = self.var_dim.get()
        dim = next((v for v, label in DIM_CHOICES if label == dim_label), "0")
        try:
            simdist = max(2, min(32, int(self.var_sim.get())))
            top = max(1, min(200, int(self.var_top.get())))
        except (tk.TclError, ValueError):
            messagebox.showwarning("参数不对", "模拟距离与 TOP N 要填整数。")
            return
        out = self.var_out.get().strip() or os.path.join(_ROOT, "output")

        self.results = []
        self.tree.delete(*self.tree.get_children())
        self.bar.configure(value=0.0)
        self.var_pct.set("0%")
        self.var_detail.set("正在准备…")
        self._log_line("─" * 60)
        self._log_line("开始扫描：%s（维度 %s）" % (path, dim_label))

        opts = ScanOptions(path=path, out=out, dim=dim, simdist=simdist,
                           top=top, txt=bool(self.var_txt.get()))
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
        state = "disabled" if running else "normal"
        for w in (self.btn_start, self.btn_browse, self.btn_find, self.cmb_found,
                  self.cmb_dim, self.chk_txt, self.ent_path, self.ent_out,
                  self.btn_out, self.spn_sim, self.spn_top):
            try:
                w.configure(state=state)
            except tk.TclError:
                pass
        self.cmb_found.configure(state="disabled" if running else "readonly")
        self.cmb_dim.configure(state="disabled" if running else "readonly")
        self.btn_cancel.configure(state="normal" if running else "disabled")
        if running:
            self.var_state.set("扫描中…")

    # ---------------- 事件泵（UI 线程） ----------------
    def _pump(self):
        if self._closing:
            return
        try:
            while True:
                ev = self.q.get_nowait()
                self._handle(ev)
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    def _handle(self, ev):
        kind = ev.get("kind")
        if kind == "log":
            self._log_line(ev["text"])
        elif kind == "phase":
            self.var_detail.set(ev["text"])
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
            messagebox.showerror("扫描出错", ev["text"])
            self._set_running(False)
            self.var_state.set("出错了，看日志")
        elif kind == "thread_end":
            self.worker = None
            if self.job is not None and self.job.cancel_requested:
                self._set_running(False)
            elif self.var_state.get() == "扫描中…":
                self._set_running(False)

    def _on_progress(self, ev):
        pct = ev.get("percent") or 0.0
        self.bar.configure(value=min(100.0, pct))
        self.var_pct.set("%.0f%%" % min(100.0, pct))
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
        self.var_detail.set("　·　".join(bits))

    def _add_result(self, r):
        self.results.append(r)
        if r.skipped:
            self.tree.insert("", "end", tags=("skip",),
                             values=(r.name, "—", "—", "—", "—", r.skipped))
        else:
            self.tree.insert("", "end", values=(r.name, r.dim, format(r.chunks, ","),
                                                r.score, r.top, fmt_seconds(r.seconds)))

    def _on_done(self, ev):
        self._set_running(False)
        self.bar.configure(value=100.0)
        self.var_pct.set("100%")
        ok = [r for r in ev["results"] if not r.skipped]
        if ev.get("cancelled"):
            self.var_state.set("已中断：已完成 %d 项（结果已保留）" % len(ok))
            self.var_detail.set("扫描被中断；已完成的结果已出图，可在下方双击打开")
        else:
            self.var_state.set("完成：%d 项" % len(ok))
            self.var_detail.set("扫描完成，输出目录：%s" % ev.get("out", ""))
        for line in ev["text"].splitlines():
            self._log_line(line)
        self.cfg["last_out"] = ev.get("out", "")
        if ok and self.var_auto.get():
            self._open_path(ok[0].map_path)
        elif not ok:
            messagebox.showinfo("没有结果",
                                "这次没扫出可用结果 —— 看看日志里的「跳过」原因。")

    # ---------------- 打开动作 ----------------
    def _open_path(self, path):
        if not path or not os.path.exists(path):
            messagebox.showinfo("打不开", "文件不存在：\n%s" % path)
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(path)
            else:
                import webbrowser
                webbrowser.open("file://" + path)
        except OSError as exc:
            messagebox.showwarning("打不开", "%s\n%s" % (path, exc))

    def _open_out(self):
        out = self.var_out.get().strip() or os.path.join(_ROOT, "output")
        if not os.path.isdir(out):
            messagebox.showinfo("还没有输出", "输出目录还不存在：\n%s" % out)
            return
        self._open_path(out)

    def _open_selected_map(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        if 0 <= idx < len(self.results):
            self._open_path(self.results[idx].map_path)

    # ---------------- 日志 ----------------
    def _log_line(self, text, warn=False):
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M:%S ") + str(text) + "\n",
                        () if not warn else ("warn",))
        self.log.see("end")
        self.log.configure(state="disabled")

    # ---------------- 收尾 ----------------
    def _on_tk_error(self, exc_type, exc_value, exc_tb):
        messagebox.showerror("界面出错", "".join(traceback.format_exception_only(exc_type, exc_value)))

    def _save_cfg(self):
        try:
            geo = self.root.geometry()
        except tk.TclError:
            geo = self.cfg.get("geometry")
        self.cfg.update({
            "last_path": self.var_path.get().strip(),
            "out": self.var_out.get().strip(),
            "dim_label": self.var_dim.get(),
            "simdist": self.var_sim.get(),
            "top": self.var_top.get(),
            "txt": bool(self.var_txt.get()),
            "advanced": bool(self.var_adv.get()),
            "auto_open": bool(self.var_auto.get()),
            "geometry": geo,
        })
        save_config(self.cfg)

    def _on_close(self):
        if self.worker is not None and self.worker.is_alive():
            if not messagebox.askyesno("正在扫描", "扫描还没结束，中断并退出？\n（已完成的结果会保留在输出目录）"):
                return
            if self.job is not None:
                self.job.cancel()
            self.worker.join(timeout=5)
        self._closing = True
        self._save_cfg()
        self.root.destroy()


# ---------------------------------------------------------------- 启动
def _enable_dpi():
    """高分屏不发虚（失败无害）。"""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)          # Win8.1+
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()            # 老系统兜底
        except Exception:
            pass


def _system_dpi():
    try:
        import ctypes
        return float(ctypes.windll.user32.GetDpiForSystem())
    except Exception:
        return 96.0


def _setup_fonts(root):
    """中文界面用雅黑；字体缺失时 Tk 会自动回退。"""
    try:
        import tkinter.font as tkfont
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(name).configure(family="Microsoft YaHei UI", size=10)
    except Exception:
        pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    _enable_dpi()
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", max(1.0, _system_dpi() / 72.0))
    except tk.TclError:
        pass
    _setup_fonts(root)
    ScanGui(root, initial_path=argv[0] if argv else None)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
