# -*- coding: utf-8 -*-
"""
合成测试存档生成器 —— 造一个带"卡顿因子"的假世界，验证解析+分析链路。

生成到 tests/fake_world/：
  - level.dat          世界名/版本
  - region/r.0.0.mca   3 个有卡顿因子的区块

NBT 用极简元组标记编码（只在测试里用，不跟生产耦合）：
  C(dict) -> compound | I(n) -> int | S(s) -> string | L(elem_type, [..]) -> list | B(n) -> byte
"""
import gzip
import os
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE_WORLD = os.path.join(HERE, "fake_world")


# ---------- 极简 NBT 编码 ----------
# 简写：C(dict)=compound, I(n)=int, S(s)=string, L(type,list)=list, B(n)=byte
def C(d):
    return ("compound", d)


def I(n):
    return ("int", n)


def S(s):
    return ("string", s)


def L(elem_type, elems):
    return ("list", elem_type, elems)


def B(n):
    return ("byte", n)


def _tcp_string(s):
    b = s.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def _enc_int(v):
    return struct.pack(">i", v)


def _enc_byte(v):
    return struct.pack(">b", v)


def _enc_string(s):
    return _tcp_string(s)


def _enc_value(spec):
    kind = spec[0]
    if kind == "compound":
        return _enc_compound_body(spec[1])
    if kind == "int":
        return _enc_int(spec[1])
    if kind == "byte":
        return _enc_byte(spec[1])
    if kind == "string":
        return _enc_string(spec[1])
    if kind == "list":
        elem_type, elems = spec[1], spec[2]
        return (struct.pack(">b", elem_type) + struct.pack(">i", len(elems))
                + b"".join(_enc_value(e) for e in elems))
    raise ValueError("未知 spec: %r" % (spec,))


_TAG_MAP = {"compound": 10, "int": 3, "byte": 1, "string": 8, "list": 9}


def _enc_compound_body(d):
    buf = b""
    for name, spec in d.items():
        tag_type = _TAG_MAP[spec[0]]
        buf += struct.pack(">b", tag_type) + _tcp_string(name) + _enc_value(spec)
    return buf + struct.pack(">b", 0)


def enc_compound(spec):
    """顶层命名 compound（root 名用空串）。spec 须为 C(dict)。"""
    if spec[0] != "compound":
        raise ValueError("enc_compound 需 C(dict)，实际 %r" % (spec,))
    return struct.pack(">b", 10) + _tcp_string("") + _enc_compound_body(spec[1])


# ---------- 构造区块 ----------
def _chunk_nbt(x, z, entity_ids, block_entity_ids):
    """构造一个区块的 NBT（老格式：Level 包裹）。"""
    entities = L(10, [C({"id": S(eid)}) for eid in entity_ids])
    be = L(10, [C({"id": S(bid)}) for bid in block_entity_ids])
    root = C({
        "DataVersion": I(3465),
        "Level": C({
            "xPos": I(x),
            "zPos": I(z),
            "entities": entities,
            "block_entities": be,
        }),
    })
    return enc_compound(root)


# ---------- 打包 region ----------
def _build_region(chunks):
    """chunks: list of (x, z, nbt_bytes)。返回 .mca 内容 bytes。"""
    header_size = 8192
    buf = bytearray(header_size)
    pos = 2  # 起始扇区号（0 和 1 是 header 4KB+4KB）
    location = {}

    for x, z, nbt_bytes in chunks:
        comp_data = zlib.compress(nbt_bytes)
        length = len(comp_data) + 1        # 含压缩类型字节（MC 标准）
        record = struct.pack(">I", length) + struct.pack(">b", 2) + comp_data
        n_sectors = (len(record) + 4095) // 4096

        start = pos * 4096
        if start + len(record) > len(buf):
            buf.extend(b"\x00" * (start + len(record) - len(buf)))
        buf[start:start + len(record)] = record

        # 定位表：index = z_local*32 + x_local
        x_local, z_local = x & 31, z & 31
        location[z_local * 32 + x_local] = (pos << 8) | n_sectors
        pos += n_sectors

    # 写入定位表（前 4KB）
    for idx, val in location.items():
        struct.pack_into(">I", buf, idx * 4, val)

    return bytes(buf)


def build():
    os.makedirs(FAKE_WORLD, exist_ok=True)
    region_dir = os.path.join(FAKE_WORLD, "region")
    os.makedirs(region_dir, exist_ok=True)

    # level.dat
    level = enc_compound(C({
        "Data": C({
            "LevelName": S("测试MC存档"),
            "DataVersion": I(3465),
            "Version": C({"Id": I(3465)}),
        }),
    }))
    with open(os.path.join(FAKE_WORLD, "level.dat"), "wb") as f:
        f.write(gzip.compress(level))

    # region：3 个区块
    chunks = [
        (0, 0, _chunk_nbt(0, 0, ["minecraft:zombie"], [])),                         # 1 hostile
        (1, 0, _chunk_nbt(1, 0, ["minecraft:zombie"] * 5, ["minecraft:spawner"])),  # 5 hostile + 1 spawner
        (0, 1, _chunk_nbt(0, 1, ["minecraft:villager"], ["minecraft:chest"])),      # 1 villager + 1 chest
    ]
    mca = _build_region(chunks)
    with open(os.path.join(region_dir, "r.0.0.mca"), "wb") as f:
        f.write(mca)

    print("已生成合成存档 -> %s" % FAKE_WORLD)


if __name__ == "__main__":
    build()
