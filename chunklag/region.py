# -*- coding: utf-8 -*-
"""
region (.mca) 区域文件解析器 —— 读取 Minecraft Anvil 格式的 .mca。

格式（设计总纲·坑）：
  - 文件头 8KB：
      · 前 4KB = 定位表（location），1024 个 uint32，
        (offset << 8) | count，offset=扇区号(4KB 单元)，count=占几个 4KB 扇区。
        索引 = z_local*32 + x_local。
      · 后 4KB = 时间戳表（timestamp，本次不关心）。
  - 每个 chunk 数据位于 offset*4096 处：
      前 4 字节 = 长度(length，含后面的压缩类型字节)
      1 字节 = 压缩类型（1=gzip, 2=zlib, 3=无压缩）
      剩余 = 压缩后的 NBT。
"""
import gzip
import os
import struct
import zlib

from . import nbt


class RegionError(Exception):
    pass


COMPRESSION_MAP = {1: "gzip", 2: "zlib", 3: "none"}


def _decompress(compression, data):
    """按压缩类型解压 chunk 数据，返回原始 NBT 字节。"""
    if compression == 1:
        return gzip.decompress(data)
    if compression == 2:
        return zlib.decompress(data)
    if compression == 3:
        return data
    raise RegionError("未知压缩类型: %d" % compression)


class RegionFile:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            self.data = f.read()

    def _location(self, x_local, z_local):
        """返回 (offset, count)；空块返回 (0,0)。"""
        index = z_local * 32 + x_local
        val = struct.unpack_from(">I", self.data, index * 4)[0]
        return val >> 8, val & 0xFF

    def read_chunk(self, x, z):
        """
        读取世界坐标 (x,z) 的区块，返回 NBT dict。
        不存在/损坏返回 None。
        """
        x_local, z_local = x & 31, z & 31
        offset, count = self._location(x_local, z_local)
        if offset == 0 or count == 0:
            return None

        pos = offset * 4096
        if pos + 5 > len(self.data):
            return None

        length = struct.unpack_from(">I", self.data, pos)[0]
        compression = self.data[pos + 4]

        # 标准 MC：length 含压缩类型字节 → 压缩数据 = length-1 字节
        payload_start = pos + 5
        payload_len = length - 1
        if payload_len <= 0 or payload_start + payload_len > len(self.data):
            # 兼容：有的实现 length 不含压缩类型字节
            payload_len = length
        compressed = self.data[payload_start:payload_start + payload_len]

        try:
            raw = _decompress(compression, compressed)
        except Exception:
            return None
        try:
            return nbt.parse_nbt(raw)
        except Exception:
            return None

    def iter_chunks(self):
        """迭代所有非空 chunk，yield (chunk_x, chunk_z, nbt)。"""
        region_x, region_z = self.region_coords()
        for z_local in range(32):
            for x_local in range(32):
                offset, count = self._location(x_local, z_local)
                if offset == 0 or count == 0:
                    continue
                chunk = self.read_chunk(region_x * 32 + x_local, region_z * 32 + z_local)
                if chunk is not None:
                    yield region_x * 32 + x_local, region_z * 32 + z_local, chunk

    def region_coords(self):
        """从文件名 r.<rx>.<rz>.mca 解析 region 坐标，失败返回 (0,0)。"""
        base = os.path.basename(self.path)
        if base.startswith("r.") and base.endswith(".mca"):
            try:
                parts = base[2:-4].split(".")
                return int(parts[0]), int(parts[1])
            except (ValueError, IndexError):
                pass
        return 0, 0


# 便捷函数：扫描目录里所有 .mca，迭代全部区块
def scan_region_dir(region_dir, hook=None):
    """
    扫描目录里所有 .mca，迭代全部区块。

    hook（可选）= 进度/中断钩子（见 chunklag.scanjob）：每进入一个 .mca 调
    `on_file(path, size)`，每读完一个区块调 `on_chunk()`。默认 None 时行为与原来完全一致。

    ⚠️ 中断信号必须继承 BaseException（如 ScanCancelled）—— 下面的 `except Exception`
    是给坏文件的，普通异常会被它吞掉，取消就静默失效了。
    """
    for name in sorted(os.listdir(region_dir)):
        if not name.endswith(".mca"):
            continue
        path = os.path.join(region_dir, name)
        try:
            rf = RegionFile(path)
            if hook is not None:
                hook.on_file(path, len(rf.data))
            for cx, cz, nbt_data in rf.iter_chunks():
                yield cx, cz, nbt_data
                if hook is not None:
                    hook.on_chunk()
        except Exception:
            continue
