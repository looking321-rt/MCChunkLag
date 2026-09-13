# -*- coding: utf-8 -*-
"""
扫描引擎 —— 可中断、带进度回调的批量扫描，供 GUI（`chunklag/gui.py`）与 CLI（`scan.py`）共用。

产出：
  <out>/<序号>_<世界名>/<维度名>/map.html     每「世界 × 维度」一份交互热力图
  <out>/<序号>_<世界名>/<维度名>/report.txt   （opts.txt）spark 式文字报告
  <out>/汇总.txt                              所有「世界 × 维度」对比一览（按总卡顿分降序）

设计要点：
  · **进度分母**：计划阶段只 `stat` 出各 region 文件字节（不读内容），解析阶段每读完
    一个文件推进一次；跨维度**补扫**（地狱门配对判定）产生的计划外文件会同步抬高分母，
    既不虚报也不倒退。
  · **中断是软停**：已完成的世界照常出图出报告，用户不白等。
  · `ScanCancelled` 继承 `BaseException` —— `region.scan_region_dir` 里有
    `except Exception: continue`（给坏文件用的），普通异常会被它吞掉，取消就静默失效。
"""
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field

# 本包可能被 `python -c "import chunklag.scanjob"` 这类方式导入，而 main.py 在项目根
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from main import analyze_world, find_region_dirs, render_map_for   # noqa: E402

from . import leveldat                                              # noqa: E402

# 扫描时跳过的目录（世界目录里不会有这些，但从 .minecraft / 整合包根扫进来时会有）
# 只列**绝不可能藏着世界**的重目录（材质包/依赖库动辄上万文件，遍历它们纯属浪费）；
# 启动器目录名五花八门（PCL 的「地图」「整合包」、各种中文包名），不能靠白名单反着挑。
SKIP_DIRS = {".git", "node_modules", "__pycache__", "logs", "backups", "screenshots",
             "crash-reports", "resourcepacks", "shaderpacks", "mods", "config",
             "assets", "libraries", "cache", "runtime", "jre"}

# 维度下拉选项（值 → 显示名）；后端 analyze_world 的 --dim 语义：0/-1/1/all
DIM_CHOICES = (("0", "主世界"), ("-1", "下界"), ("1", "末地"), ("all", "全部维度"))


class ScanCancelled(BaseException):
    """用户中断扫描。

    必须继承 BaseException：`region.scan_region_dir` 用 `except Exception: continue`
    跳过坏文件，普通异常会被它吞掉 → 取消静默失效（这是设计约束，别改成 Exception）。
    """


class ScanError(Exception):
    """无法开始扫描（目录不存在 / 目录下没有存档 / 全都没有区块数据）。"""


def discover_worlds(root, max_depth=8):
    """
    找存档世界目录：root 本身含 level.dat 就直接用；否则向下找（命中世界后不再往世界内部走）。
    兼容 saves/世界名 与 versions/<版本>/saves/世界名 这类嵌套。

    深度 8 是「发现」与「扫描」共用的口径（保持一致，否则会出现"列表里有、扫却找不到"）：
    hmcl 是 6 层，PCL 整合包是 `PCL/整合包/<包名>/.minecraft/versions/<版本>/saves/<世界>` 8 层。
    重目录（assets/libraries/resourcepacks/mods 等）由 SKIP_DIRS 剪掉，实测扫两大启动器
    36 个世界仅 1.5s。
    """
    root = os.path.abspath(root)
    if os.path.exists(os.path.join(root, "level.dat")):
        return [root]
    base = root.rstrip("\\/").count(os.sep)
    found = []
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        if "level.dat" in files:
            found.append(cur)
            dirs[:] = []
            continue
        if cur.count(os.sep) - base >= max_depth:
            dirs[:] = []
    return sorted(found)


def safe_name(name, fallback="world"):
    """世界名 → 安全的文件夹名（去掉 Windows 非法字符）。"""
    s = re.sub(r'[\\/:*?"<>|]+', "_", (name or "").strip())
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:60] or fallback


def region_files(region_dir):
    """返回 (文件路径集合, 总字节)。只 stat 不读内容 —— 计划阶段的开销就靠它控制。"""
    files, total = set(), 0
    try:
        names = sorted(os.listdir(region_dir))
    except OSError:
        return files, total
    for name in names:
        if not name.endswith(".mca"):
            continue
        path = os.path.join(region_dir, name)
        try:
            total += os.path.getsize(path)
        except OSError:
            continue
        files.add(path)
    return files, total


def plan_world(world_dir, dim_sel):
    """
    一个世界的扫描计划：[(region_dir, 维度名, 维度 id, 文件集合, 总字节)]。
    维度为空的（该维度没有 region）直接剔除。
    """
    items = []
    for rdir, dim_name, _key, _dim_dir, dim_id in find_region_dirs(world_dir, dim_sel):
        files, total = region_files(rdir)
        if files:
            items.append((rdir, dim_name, dim_id, files, total))
    return items


def world_info(world_dir):
    """世界名 + 版本名（读不到时回退目录名 / None）。"""
    data, _dv = leveldat.parse_level_dat(os.path.join(world_dir, "level.dat"))
    if not data:
        return os.path.basename(os.path.abspath(world_dir)), None
    return leveldat.get_world_name(data), leveldat.get_version_name(data)


@dataclass
class ScanOptions:
    """扫描参数（GUI 与 CLI 共用的一份）。"""
    path: str
    out: str = "output"
    dim: str = "0"
    simdist: int = 10
    top: int = 20
    txt: bool = False


@dataclass
class WorldResult:
    """一个「世界 × 维度」的扫描结果；skipped 非空表示该世界没出图（附原因）。"""
    name: str
    dim: str = ""
    out_dir: str = ""
    chunks: int = 0
    score: int = 0
    top: str = "-"
    seconds: float = 0.0
    skipped: str = ""

    @property
    def map_path(self):
        return os.path.join(self.out_dir, "map.html") if self.out_dir else ""


@dataclass
class _PlanItem:
    world_dir: str
    name: str
    dims: list = field(default_factory=list)
    region_dirs: dict = field(default_factory=dict)   # region_dir → 维度名


class _ProgressHook:
    """
    把 region 解析回调折算成进度事件（**只在扫描线程里被调用**）。

    进度按 region 文件结算：进入新文件时结算上一个文件的字节（读完才算数）。
    """

    EMIT_INTERVAL = 0.12        # 事件节流（秒），避免大存档每区块都刷一条

    def __init__(self, job, item, world_i, world_n):
        self._job = job
        self._item = item
        self._world_i = world_i
        self._world_n = world_n
        self._path = ""
        self._size = 0          # 当前文件字节（读完才计入 done）
        self._dim = ""
        self._last = 0.0

    # ---- region.scan_region_dir 调用的两个钩子 ----
    def on_file(self, path, size):
        self._settle()
        self._job.note_file(path, size)      # 计划外文件（补扫对面维度）→ 分母同步增长
        self._path = path
        self._size = size
        self._dim = self._item.region_dirs.get(os.path.dirname(path), self._dim)
        self._emit(force=True)

    def on_chunk(self):
        self._job.count_chunk()
        if self._job.cancel_requested:
            raise ScanCancelled()
        self._emit()

    def settle(self):
        """世界扫完时结算最后一个文件（否则尾文件的字节永远不计入进度）。"""
        self._settle()
        self._emit(force=True)

    def _settle(self):
        if self._size:
            self._job.add_done(self._size)
            self._size = 0

    def _emit(self, force=False):
        now = time.time()
        if not force and now - self._last < self.EMIT_INTERVAL:
            return
        self._last = now
        self._job.emit_progress(world=self._item.name, world_i=self._world_i,
                                world_n=self._world_n, dim=self._dim,
                                file=os.path.basename(self._path))


class ScanJob:
    """一次批量扫描。`run()` 在**调用方线程**里跑 —— GUI 自己开工作线程，别在 UI 线程调用。"""

    def __init__(self, options, on_event=None, on_log=None):
        self.opts = options
        self._on_event = on_event
        self._on_log = on_log
        self._cancel = threading.Event()
        self._known = set()
        self._total = 0.0
        self._done = 0.0
        self._chunks = 0
        self._t0 = 0.0
        self.was_cancelled = False

    # ---------------- 外部控制 ----------------
    @property
    def cancel_requested(self):
        return self._cancel.is_set()

    def cancel(self):
        """请求中断：当前世界扫完手上的区块就停，已完成的世界照常出报告。"""
        self._cancel.set()

    def snapshot(self):
        """当前进度快照（供 UI 兜底刷新；事件流之外的只读视图）。"""
        elapsed = time.time() - self._t0 if self._t0 else 0.0
        return {"done": self._done, "total": self._total, "chunks": self._chunks,
                "elapsed": elapsed, "eta": _eta(self._done, self._total, elapsed),
                "percent": (100.0 * self._done / self._total) if self._total else 0.0}

    # ---------------- 进度状态（由 hook 在扫描线程里更新） ----------------
    def note_file(self, path, size):
        if path in self._known:
            return
        self._known.add(path)
        self._total += size          # 补扫产生的额外工作量

    def count_chunk(self):
        self._chunks += 1

    def add_done(self, size):
        self._done += size

    def emit_progress(self, world="", world_i=0, world_n=0, dim="", file=""):
        snap = self.snapshot()
        ev = {"kind": "progress", "world": world, "world_i": world_i, "world_n": world_n,
              "dim": dim, "file": file}
        ev.update(snap)
        self._emit(ev)

    # ---------------- 事件 / 日志 ----------------
    def _emit(self, ev):
        ev.setdefault("elapsed", time.time() - self._t0 if self._t0 else 0.0)
        if self._on_event is not None:
            try:
                self._on_event(ev)
            except Exception:
                pass                       # UI 回调出错不能毁掉扫描

    def _log(self, text):
        if self._on_log is not None:
            try:
                self._on_log(text)
            except Exception:
                pass

    def _mark_cancelled(self, where):
        """记一次中断（只记一次）—— 中断点有多个，用户必须能从日志看出为什么停了。"""
        if self.was_cancelled:
            return
        self.was_cancelled = True
        self._log("   已中断（%s）：已完成的结果照常出报告" % where)

    # ---------------- 主流程 ----------------
    def run(self):
        """
        执行扫描，返回 [WorldResult]。无法开始（目录不存在 / 没有存档）抛 ScanError；
        被中断不抛异常 —— 返回已完成的那些结果，`was_cancelled` 为 True。
        """
        self._t0 = time.time()
        self._emit({"kind": "phase", "text": "正在查找存档…"})

        root = os.path.abspath(self.opts.path)
        if not os.path.isdir(root):
            raise ScanError("目录不存在：%s" % root)
        worlds = discover_worlds(root)
        if not worlds:
            raise ScanError("没有找到存档：该目录下没有含 level.dat 的世界。")

        self._emit({"kind": "phase", "text": "正在统计待扫描数据量…"})
        plan = []
        for wdir in worlds:
            name, _ver = world_info(wdir)
            dims = plan_world(wdir, self.opts.dim)
            item = _PlanItem(world_dir=wdir, name=name, dims=dims)
            for rdir, dim_name, _dim_id, files, total in dims:
                item.region_dirs[rdir] = dim_name
                self._known.update(files)
                self._total += total
            plan.append(item)

        self._emit({"kind": "plan", "worlds": len(plan), "total": self._total,
                    "path": root, "dim": self.opts.dim})
        self._log("共 %d 个世界，待解析 %.1f MB（维度：%s）"
                  % (len(plan), self._total / 1048576.0, _dim_text(self.opts.dim)))
        if self.opts.out:
            os.makedirs(self.opts.out, exist_ok=True)

        results = []
        for i, item in enumerate(plan, 1):
            if self._cancel.is_set():
                self._mark_cancelled("停止后续世界")
                break
            self._log("[%d/%d] %s" % (i, len(plan), item.name))
            if not item.dims:
                reason = "跳过：没有 region 区块数据"
                self._log("   " + reason)
                results.append(WorldResult(name=item.name, skipped=reason))
                continue

            hook = _ProgressHook(self, item, i, len(plan))
            t_world = time.time()
            try:
                all_results = analyze_world(item.world_dir, self.opts.dim, hook=hook)
            except ScanCancelled:
                self._mark_cancelled("当前世界未完成，不计入结果")
            except Exception as exc:                      # 单世界坏掉不影响整批
                reason = "跳过（分析失败）: %s" % exc
                self._log("   " + reason)
                results.append(WorldResult(name=item.name, skipped=reason))
                all_results = []
            finally:
                hook.settle()
            if self.was_cancelled:
                break

            if not all_results:
                reason = "跳过：没有 region 区块数据"
                self._log("   " + reason)
                results.append(WorldResult(name=item.name, skipped=reason))
                continue

            world_scan = time.time() - t_world        # 世界扫描耗时（多维度的行都会带上）
            for _rdir, dim_name, res, _dim_dir in all_results:
                if self._cancel.is_set():
                    self._mark_cancelled("停止后续维度")
                    break
                results.append(self._finish_one(item, i, len(plan), dim_name, res,
                                                 base_seconds=world_scan))
            if self.was_cancelled:
                break

        text = summary_text(results, cancelled=self.was_cancelled)
        if results and self.opts.out:
            with open(os.path.join(self.opts.out, "汇总.txt"), "w", encoding="utf-8") as f:
                f.write(text + "\n")
        self._emit({"kind": "done", "text": text, "cancelled": self.was_cancelled,
                    "results": results, "out": os.path.abspath(self.opts.out)})
        return results

    def _finish_one(self, item, world_i, world_n, dim_name, res, base_seconds=0.0):
        """
        渲染一个维度的地图 + 文字报告，返回结果条目。

        base_seconds = 该世界的扫描耗时（同一世界的每个维度行都带上，外加本维度的渲染耗时）。
        早先只算渲染 → 结果表/汇总里"耗时"显示 0.0s，看起来像没扫（真机 生电 实测踩到）。
        """
        t0 = time.time()
        out_dir = os.path.join(self.opts.out,
                               "%02d_%s" % (world_i, safe_name(item.name, "world%d" % world_i)),
                               safe_name(dim_name, "dim"))
        os.makedirs(out_dir, exist_ok=True)
        self._emit({"kind": "phase", "text": "正在生成地图：%s / %s" % (item.name, dim_name)})
        msg = render_map_for(item.world_dir, res, os.path.join(out_dir, "map.html"),
                             simdist=self.opts.simdist, top_n=self.opts.top)
        top = res.top_chunks[0] if res.top_chunks else None
        top_txt = "(%d,%d)=%d" % (top[0], top[1], top[2]) if top else "-"
        self._log("   %s：区块 %d | 总卡顿分 %d | 最卡 %s"
                  % (dim_name, res.total_chunks, res.total_score, top_txt))
        self._log("   " + msg)
        if self.opts.txt:
            from . import report
            with open(os.path.join(out_dir, "report.txt"), "w", encoding="utf-8") as f:
                f.write(report.render_text(res, top_n=self.opts.top))
        wr = WorldResult(name=item.name, dim=dim_name, out_dir=out_dir,
                         chunks=res.total_chunks, score=res.total_score,
                         top=top_txt, seconds=base_seconds + (time.time() - t0))
        self._log("   → %s（%.1fs）" % (out_dir, wr.seconds))
        self._emit({"kind": "world_done", "result": wr, "world_i": world_i, "world_n": world_n})
        return wr


def _eta(done, total, elapsed):
    """线性外推的剩余秒数；信息不足返回 None。"""
    if done <= 0 or total <= done:
        return None
    return elapsed / done * (total - done)


def _dim_text(dim_sel):
    for value, label in DIM_CHOICES:
        if value == str(dim_sel):
            return label
    return str(dim_sel)


def summary_text(results, cancelled=False):
    """汇总文本（写 汇总.txt，也直接贴到日志里）。"""
    ok = [r for r in results if not r.skipped]
    skipped = [r for r in results if r.skipped]
    lines = ["批量扫描汇总（%d 项%s，按总卡顿分降序）"
             % (len(ok), "，已中断" if cancelled else ""), ""]
    if cancelled:
        lines.append("⚠ 扫描被中断：以下只包含中断前已完成的结果。")
        lines.append("")
    if ok:
        lines.append("世界 | 维度 | 区块数 | 总卡顿分 | 最卡区块 | 耗时")
        for r in sorted(ok, key=lambda x: -x.score):
            lines.append("%s | %s | %d | %d | %s | %.1fs"
                         % (r.name, r.dim, r.chunks, r.score, r.top, r.seconds))
    else:
        lines.append("（没有成功扫描出结果的项）")
    if skipped:
        lines.append("")
        lines.append("跳过：")
        for r in skipped:
            lines.append("%s | %s" % (r.name, r.skipped))
    return "\n".join(lines)
