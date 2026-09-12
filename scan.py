# -*- coding: utf-8 -*-
"""
批量扫描 —— 给一个 saves 目录（或单个世界目录），每个世界出卡顿热力图 HTML。

用法：
  python scan.py <saves目录或世界目录> [--out output] [--dim 0] [--simdist 10]
                 [--top 20] [--txt] [--open]

产出：
  output/<序号>_<世界名>/<维度名>/map.html    每「世界 × 维度」一份交互热力图（浏览器打开）
  output/<序号>_<世界名>/<维度名>/report.txt  （--txt 时）spark 式文字报告
  output/汇总.txt                             所有「世界 × 维度」对比一览（按总卡顿分降序）

扫描逻辑本身在 `chunklag/scanjob.py`（可中断 + 进度回调），CLI 与 GUI 共用同一份；
本文件只做参数解析与终端打印。
"""
import argparse
import os
import sys

from chunklag.scanjob import (ScanError, ScanJob, ScanOptions,   # noqa: F401
                              discover_worlds, safe_name)

__all__ = ["discover_worlds", "safe_name", "scan", "main"]


def scan(args):
    job = ScanJob(ScanOptions(path=args.path, out=args.out, dim=args.dim,
                              simdist=args.simdist, top=args.top, txt=args.txt),
                  on_event=_printer(), on_log=print)
    try:
        results = job.run()
    except ScanError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not any(not r.skipped for r in results):
        print("所有世界都没有可分析的区块数据。", file=sys.stderr)
        return 3

    if args.open and hasattr(os, "startfile"):
        try:
            os.startfile(os.path.abspath(args.out))
        except OSError:
            pass  # 打不开资源管理器不影响已生成的结果
    return 0


def _printer():
    """终端事件打印（汇总只在 done 时打一次，避免与 on_log 重复）。"""
    def on_event(ev):
        if ev["kind"] == "done":
            print()
            print(ev["text"])
            print()
            print("输出目录: %s" % ev["out"])
    return on_event


def main(argv=None):
    parser = argparse.ArgumentParser(description="批量扫描多个 MC 存档，每个世界每维度出一份卡顿热力图")
    parser.add_argument("path", help="saves 目录（或单个世界目录）")
    parser.add_argument("--out", default="output", help="输出目录（默认 ./output）")
    parser.add_argument("--dim", default="0", help="维度: 0主世界(默认) -1下界 1末地 all")
    parser.add_argument("--simdist", type=int, default=10, help="玩家模拟距离(区块)，默认10")
    parser.add_argument("--top", type=int, default=20, help="最卡 TOP N（默认20）")
    parser.add_argument("--txt", action="store_true", help="额外出每世界 report.txt 文字报告")
    parser.add_argument("--open", action="store_true", help="跑完打开输出目录")
    args = parser.parse_args(argv)

    if not os.path.isdir(args.path):
        print("错误: 目录不存在 -> %s" % args.path, file=sys.stderr)
        return 1
    return scan(args)


if __name__ == "__main__":
    sys.exit(main())
