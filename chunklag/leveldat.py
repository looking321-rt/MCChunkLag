# -*- coding: utf-8 -*-
"""
level.dat 解析器 —— 读取世界元信息（世界名 / 版本 / 数据版本）。

level.dat 是 gzip 压缩的 NBT，顶层 compound 含 "Data" 键，
Data 下有 LevelName（世界名）、Version{Id}（版本号）、DataVersion（数据版本）。
"""
import gzip
import os

from . import nbt


def parse_level_dat(path):
    """
    读取 level.dat，返回 (Data 的 dict, DataVersion)。
    Data 含 LevelName / Version 等。失败返回 (None, None)。
    """
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None, None

    data = None
    # 通常是 gzip；个别情况未压缩
    for raw_data in (gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw,) + (
        (raw,) if raw[:2] != b"\x1f\x8b" else (),
    ):
        try:
            top = nbt.parse_nbt(raw_data)
            data = top.get("Data", top)
            break
        except Exception:
            continue

    if data is None:
        return None, None

    data_version = None
    version = data.get("Version") or {}
    if isinstance(version, dict):
        data_version = version.get("Id")
    if data_version is None and "DataVersion" in data:
        data_version = data["DataVersion"]

    return data, data_version


def get_world_name(data):
    if not data:
        return "(未知世界)"
    name = data.get("LevelName")
    return name if name else "(未命名世界)"


def get_version(data):
    if not data:
        return None
    return data.get("DataVersion")


def describe_world(path):
    """读取存档目录，返回 (世界名, DataVersion)。供 CLI 展示。"""
    ldat = os.path.join(path, "level.dat")
    if not os.path.exists(ldat):
        return "(无 level.dat)", None
    data, dv = parse_level_dat(ldat)
    return get_world_name(data), dv
