# -*- coding: utf-8 -*-
"""
实体分区读取 —— 1.16+ 把实体存到独立的 world/entities/r.<rx>.<rz>.mca，
不再嵌在 region 区块 NBT 里。要分析实体类卡顿因子必须读这里。

entities 分区文件结构与 region 相同（32x32 定位表），每个位置的 NBT 顶层是
compound，含 "Entities" 列表（每项是实体 compound，有 id / Pos 等）。
"""
import os

from . import region
from .factors import _entity_factor


class EntityPartition:
    def __init__(self, base_dir):
        """
        base_dir 兼容三种传法：
          · 维度目录（新布局 dimensions/<ns>/<dim>/、旧布局 世界根 / DIM-1）→ 自动取其中的 entities/
          · 世界根目录（旧布局 <world>/entities/）
          · entities 目录本身
        """
        cand = os.path.join(base_dir, "entities")
        self.dir = cand if os.path.isdir(cand) else base_dir
        self._cache = {}

    def exists(self):
        return os.path.isdir(self.dir)

    def entities_at(self, x, z):
        """返回世界坐标 (x,z) 区块的实体列表（list of compound）。"""
        rx, rz = x >> 5, z >> 5
        path = os.path.join(self.dir, "r.%d.%d.mca" % (rx, rz))
        if not os.path.exists(path):
            return []
        rf = self._cache.get(path)
        if rf is None:
            try:
                rf = region.RegionFile(path)
            except Exception:
                return []
            self._cache[path] = rf
        try:
            d = rf.read_chunk(x, z)
        except Exception:
            return []
        if not isinstance(d, dict):
            return []
        lvl = d.get("Level", d) if isinstance(d, dict) else d
        ents = lvl.get("entities") if isinstance(lvl, dict) else None
        if not isinstance(ents, list):
            ents = lvl.get("Entities") if isinstance(lvl, dict) else None
        if not isinstance(ents, list):
            return []
        return [e for e in ents if isinstance(e, dict)]

    def merged_counts(self, x, z, counts):
        """把 (x,z) 区块的实体计数合并进 counts（就地修改并返回）。"""
        for entity in self.entities_at(x, z):
            key = _entity_factor(entity)
            counts[key] = counts.get(key, 0) + 1
        return counts
