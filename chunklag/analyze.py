# -*- coding: utf-8 -*-
"""
spark 式聚合分析 —— 把"每个区块的卡顿因子计数"聚成一张占比树。

对应 spark 的调用树思想：
  - spark：每个线程调用栈采到几次 → 该路径占多少 CPU
  - 这里：每个因子计数 × 权重 → 该原因占多少"卡顿贡献"

产出一：
  1. 总览：总区块数 / 总卡顿分 / 各大类占比（哪种原因占大头）
  2. 下钻：某因子占比高 → 是哪些区块贡献的（集中在哪些区块）
  3. 排名：最卡 TOP 区块（按 chunk_score）
"""
from dataclasses import dataclass, field

from .factors import FACTOR_GROUPS, FACTOR_WEIGHTS, chunk_score


@dataclass
class FactorStat:
    """单个因子的聚合统计。"""
    key: str
    weight: int
    label: str
    group: str
    count: int = 0
    chunks: list = field(default_factory=list)  # [(cx, cz, count)] 贡献该因子的区块

    @property
    def contribution(self):
        return self.count * self.weight


@dataclass
class AnalysisResult:
    world_name: str
    data_version: object
    total_chunks: int = 0
    chunk_entries: dict = field(default_factory=dict)  # (cx, cz) -> counts
    factor_stats: dict = field(default_factory=dict)    # key -> FactorStat
    total_score: int = 0
    top_chunks: list = field(default_factory=list)      # [(cx, cz, score, counts)]

    @property
    def groups(self):
        """按 FACTOR_GROUPS 顺序返回 (group_name, [FactorStat])。"""
        yield from _group_stats(self.factor_stats)


def _label_map():
    mapping = {}
    for group_name, items in FACTOR_GROUPS:
        for key, label, weight in items:
            mapping[key] = (group_name, label, weight)
    return mapping


def _group_stats(factor_stats):
    for group_name, items in FACTOR_GROUPS:
        stats = [factor_stats[k] for k, _l, _w in items if k in factor_stats]
        if stats:
            yield group_name, stats


def analyze(chunk_iter, world_name="(未知世界)", data_version=None):
    """
    输入：iterable of (cx, cz, counts_dict)
    返回：AnalysisResult
    """
    mapping = _label_map()
    result = AnalysisResult(world_name=world_name, data_version=data_version)
    factor_stats = {}

    for key, (group, label, weight) in mapping.items():
        factor_stats[key] = FactorStat(key=key, weight=weight,
                                       label=label, group=group)

    for cx, cz, counts in chunk_iter:
        result.total_chunks += 1
        score = chunk_score(counts)
        result.total_score += score
        result.chunk_entries[(cx, cz)] = counts
        for key, cnt in counts.items():
            if cnt <= 0 or key not in factor_stats:
                continue
            fs = factor_stats[key]
            fs.count += cnt
            fs.chunks.append((cx, cz, cnt))

    result.factor_stats = factor_stats

    # TOP 榜：按区块卡顿分降序
    ranked = sorted(
        result.chunk_entries.items(),
        key=lambda kv: chunk_score(kv[1]),
        reverse=True,
    )
    result.top_chunks = [(cx, cz, chunk_score(counts), counts)
                         for (cx, cz), counts in ranked]
    return result
