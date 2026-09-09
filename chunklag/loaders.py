# -*- coding: utf-8 -*-
"""
常加载区块识别 —— 找出"非玩家也能保持加载"的区块（出生点/forceload/mod），
用于与玩家模拟区并集，补全卡顿热力图（MC 加载机制见 Minecraft Wiki）。

来源：
  - 出生点 spawn chunks：level.dat 的 SpawnX/Z + 游戏规则 spawnChunkRadius(默认2)
  - /forceload：level.dat 的 ForcedChunks（vanilla 强制加载，负载等级31）
  - mod 强制加载：data/chunks.dat(FTB) 的 ForgeForced[*].ModForced[*].Blocks(方块坐标)
"""
import gzip
import math
import os

from . import nbt
from . import leveldat


def _world_chunk(x, z):
    return (int(math.floor(x / 16)), int(math.floor(z / 16)))


def read_spawn_chunks(world_dir, radius=2):
    """出生点恒加载区：出生点区块 + 半径 radius（spawnChunkRadius，默认2）。"""
    ldat = os.path.join(world_dir, "level.dat")
    if not os.path.exists(ldat):
        return set()
    data, _dv = leveldat.parse_level_dat(ldat)
    if not data:
        return set()
    sx, sz = data.get("SpawnX"), data.get("SpawnZ")
    if sx is None or sz is None:
        return set()
    cx, cz = _world_chunk(sx, sz)
    return {(cx + dx, cz + dz) for dx in range(-radius, radius + 1)
            for dz in range(-radius, radius + 1)}


def read_forced_chunks(world_dir):
    """vanilla /forceload 强制加载区块（level.dat 的 ForcedChunks）。"""
    ldat = os.path.join(world_dir, "level.dat")
    if not os.path.exists(ldat):
        return set()
    data, _dv = leveldat.parse_level_dat(ldat)
    if not data:
        return set()
    fc = data.get("ForcedChunks")
    if not isinstance(fc, list) or len(fc) < 2:
        return set()
    # 成对 [x,z,x,z,...]
    return {(fc[i], fc[i + 1]) for i in range(0, len(fc) - 1, 2)}


def _mod_blocks_to_chunks(blocks):
    """把 mod 强制加载的 Blocks(方块坐标列表) 转成区块集合。"""
    chunks = set()
    for b in blocks:
        if isinstance(b, dict) and "X" in b and "Z" in b:
            chunks.add(_world_chunk(b["X"], b["Z"]))
    return chunks


def read_mod_forced(world_dir):
    """
    读取 mod 强制加载区块（FTB chunks.dat 的 ForgeForced）。
    返回 [(mod名, 区块集合), ...]。
    """
    path = os.path.join(world_dir, "data", "chunks.dat")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "rb") as f:
            raw = f.read()
        raw = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
        top = nbt.parse_nbt(raw)
    except Exception:
        return []
    data = top.get("data", top) if isinstance(top, dict) else {}
    forge = data.get("ForgeForced") or data.get("Forced") or []
    results = []
    for entry in forge:
        if not isinstance(entry, dict):
            continue
        mod = entry.get("Mod", "未知mod")
        mod_forced = entry.get("ModForced") or []
        chunks = set()
        for mf in mod_forced:
            if isinstance(mf, dict):
                blocks = mf.get("Blocks")
                if isinstance(blocks, list):
                    chunks |= _mod_blocks_to_chunks(blocks)
        if chunks:
            results.append((mod, chunks))
    return results


def collect_regions(world_dir, spawn_radius=2):
    """汇总所有常加载区，返回 [(type, label, 区块集合), ...]。"""
    regions = []
    spawn = read_spawn_chunks(world_dir, spawn_radius)
    if spawn:
        regions.append(("spawn", "出生点恒加载区", spawn))
    forced = read_forced_chunks(world_dir)
    if forced:
        regions.append(("forced", "/forceload", forced))
    for mod, chunks in read_mod_forced(world_dir):
        regions.append(("mod", "mod常加载:" + mod, chunks))
    return regions
