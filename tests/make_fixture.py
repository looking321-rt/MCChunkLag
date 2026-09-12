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


def D(n):
    return ("double", n)


def F(n):
    return ("float", n)


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
    if kind == "double":
        return struct.pack(">d", spec[1])
    if kind == "float":
        return struct.pack(">f", spec[1])
    if kind == "byte":
        return _enc_byte(spec[1])
    if kind == "string":
        return _enc_string(spec[1])
    if kind == "list":
        elem_type, elems = spec[1], spec[2]
        return (struct.pack(">b", elem_type) + struct.pack(">i", len(elems))
                + b"".join(_enc_value(e) for e in elems))
    raise ValueError("未知 spec: %r" % (spec,))


_TAG_MAP = {"compound": 10, "int": 3, "byte": 1, "string": 8, "list": 9,
            "double": 6, "float": 5}


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


PORTAL_WORLD = os.path.join(HERE, "portal_world")


def _chunk_nbt_blocks(x, z, block_names, block_entity_ids=()):
    """
    构造带方块 palette 的区块 NBT（1.18+ 结构：section.block_states.palette）。

    block_names 元素：字符串（方块名）或 (名字, {"powered": "true"}) 元组（带方块状态）。
    """
    pal = []
    for b in block_names:
        if isinstance(b, tuple):
            pal.append(C({"Name": S(b[0]),
                          "Properties": C({k: S(v) for k, v in b[1].items()})}))
        else:
            pal.append(C({"Name": S(b)}))
    sections = L(10, [C({
        "Y": B(0),
        "block_states": C({"palette": L(10, pal)}),
    })])
    be = L(10, [C({"id": S(bid)}) for bid in block_entity_ids])
    root = C({
        "DataVersion": I(3465),
        "Level": C({
            "xPos": I(x),
            "zPos": I(z),
            "sections": sections,
            "block_entities": be,
        }),
    })
    return enc_compound(root)


def build_portal_world():
    """
    合成「门判据」测试世界（主世界 3 个装置 + 下界 1 个装置，覆盖跨维度成对判据）：
      主世界 (0,0)   纯装饰门（黑曜石+传送门方块，0 红石）  → 本侧无红石 → 不算
      主世界 (2,0)   门+红石，下界 (0,0) 有配对门+红石      → 两侧成对 → 算常加载装置
      主世界 (108,0) 门+红石，但换算点下界 (13,0) 无门      → 只单侧识别 → 不算
      下界   (0,0)   门+红石，对面主世界装置带红石          → 算常加载装置
    """
    for sub in ("region", os.path.join("DIM-1", "region")):
        os.makedirs(os.path.join(PORTAL_WORLD, sub), exist_ok=True)
    level = enc_compound(C({
        "Data": C({
            "LevelName": S("门判据测试"),
            "DataVersion": I(3465),
            "Version": C({"Id": I(3465)}),
        }),
    }))
    with open(os.path.join(PORTAL_WORLD, "level.dat"), "wb") as f:
        f.write(gzip.compress(level))

    pure = ["minecraft:obsidian", "minecraft:nether_portal"]
    wired = pure + ["minecraft:redstone_wire", "minecraft:repeater"]
    near = [(0, 0, _chunk_nbt_blocks(0, 0, pure)),
            (2, 0, _chunk_nbt_blocks(2, 0, wired))]
    far = [(108, 0, _chunk_nbt_blocks(108, 0, wired))]   # ÷8 → 下界 (13,0)，超出配对容差
    nether = [(0, 0, _chunk_nbt_blocks(0, 0, wired))]
    for fname, chunks in (("r.0.0.mca", near), ("r.3.0.mca", far)):
        with open(os.path.join(PORTAL_WORLD, "region", fname), "wb") as f:
            f.write(_build_region(chunks))
    with open(os.path.join(PORTAL_WORLD, "DIM-1", "region", "r.0.0.mca"), "wb") as f:
        f.write(_build_region(nether))
    print("已生成门判据测试世界 -> %s" % PORTAL_WORLD)


NEW_WORLD = os.path.join(HERE, "new_world")
NEW_DATA_VERSION = 4903          # 26.2


def _entity_chunk_nbt(x, z, entity_ids):
    """实体分区区块 NBT（1.16+：顶层直接含 Entities 列表）。"""
    ents = L(10, [C({"id": S(eid)}) for eid in entity_ids])
    root = C({"DataVersion": I(NEW_DATA_VERSION), "xPos": I(x), "zPos": I(z),
              "Entities": ents})
    return enc_compound(root)


def _player_dat_bytes(pos, pearls):
    """玩家数据 NBT（26.x：players/data/<uuid>.dat；末影珍珠在 ender_pearls 列表里）。"""
    pearl_specs = [C({"id": S("minecraft:ender_pearl"),
                      "Pos": L(6, [D(v) for v in p]),
                      "ender_pearl_dimension": S("minecraft:overworld")}) for p in pearls]
    root = C({
        "DataVersion": I(NEW_DATA_VERSION),
        "Pos": L(6, [D(v) for v in pos]),
        "Dimension": S("minecraft:overworld"),
        "ender_pearls": L(10, pearl_specs),
    })
    return gzip.compress(enc_compound(root))


def build_new_layout_world():
    """
    合成「新版布局 + 1.21.2+ 加载器」测试世界（26.x 结构）：

      dimensions/minecraft/overworld/region   (0,0) 地狱门 + 已激活动力铁轨
      dimensions/minecraft/overworld/entities (0,0) 矿车实体（矿车地狱门加载器证据）
      dimensions/minecraft/the_nether/region  (0,0) 地狱门 + 已激活动力铁轨（配对侧）
      players/data/<uuid>.dat                ender_pearls 一颗（Pos[8,-50,8] → 区块 (0,0)）
    """
    ow = os.path.join(NEW_WORLD, "dimensions", "minecraft", "overworld")
    nw = os.path.join(NEW_WORLD, "dimensions", "minecraft", "the_nether")
    for d in (os.path.join(ow, "region"), os.path.join(ow, "entities"),
              os.path.join(nw, "region"), os.path.join(NEW_WORLD, "players", "data")):
        os.makedirs(d, exist_ok=True)

    level = enc_compound(C({
        "DataVersion": I(NEW_DATA_VERSION),
        "LevelName": S("新版布局测试"),
        "spawn": C({"pos": L(3, [I(0), I(-60), I(0)]), "pitch": F(0.0), "yaw": F(0.0),
                    "dimension": S("minecraft:overworld")}),
        "Version": C({"Id": I(NEW_DATA_VERSION), "Name": S("26.2")}),
    }))
    with open(os.path.join(NEW_WORLD, "level.dat"), "wb") as f:
        f.write(gzip.compress(level))

    portal = ["minecraft:obsidian", "minecraft:nether_portal"]
    active_rail = ("minecraft:powered_rail", {"powered": "true", "shape": "north_south"})
    with open(os.path.join(ow, "region", "r.0.0.mca"), "wb") as f:
        f.write(_build_region([(0, 0, _chunk_nbt_blocks(0, 0, portal + [active_rail]))]))
    with open(os.path.join(nw, "region", "r.0.0.mca"), "wb") as f:
        f.write(_build_region([(0, 0, _chunk_nbt_blocks(0, 0, portal + [active_rail]))]))
    with open(os.path.join(ow, "entities", "r.0.0.mca"), "wb") as f:
        f.write(_build_region([(0, 0, _entity_chunk_nbt(0, 0, ["minecraft:minecart"]))]))
    pearl_dat = os.path.join(NEW_WORLD, "players", "data",
                             "00000000-0000-0000-0000-000000000001.dat")
    with open(pearl_dat, "wb") as f:
        f.write(_player_dat_bytes([-141.0, -60.0, 41.0], [[8.0, -50.0, 8.0]]))
    print("已生成新版布局测试世界 -> %s" % NEW_WORLD)


MIXED_WORLD = os.path.join(HERE, "mixed_world")


def build_mixed_layout_world():
    """
    合成「混合布局」测试世界（1.20 整合包 + 自定义维度 mod 的真实形态）：

      level.dat（旧版格式：SpawnX/SpawnZ）
      region/r.0.0.mca                                        主世界（旧布局）
      DIM-1/region/r.0.0.mca                                  下界（旧布局）
      dimensions/twilightforest/twilight_forest/region/...    自定义维度（mod）

    2026-09-12 踩坑：早期实现"有 dimensions/ 就整个按新布局解析"，导致这种存档
    只剩自定义维度、主世界被漏掉 → CLI 报"没有 region 区块数据"（001 存档实测）。
    """
    tf = os.path.join(MIXED_WORLD, "dimensions", "twilightforest", "twilight_forest")
    for d in (os.path.join(MIXED_WORLD, "region"), os.path.join(MIXED_WORLD, "DIM-1", "region"),
              os.path.join(tf, "region")):
        os.makedirs(d, exist_ok=True)

    level = enc_compound(C({
        "Data": C({
            "LevelName": S("混合布局测试"),
            "DataVersion": I(3465),
            "Version": C({"Id": I(3465), "Name": S("1.20.1")}),
            "SpawnX": I(0), "SpawnY": I(64), "SpawnZ": I(0),
        }),
    }))
    with open(os.path.join(MIXED_WORLD, "level.dat"), "wb") as f:
        f.write(gzip.compress(level))

    blocks = ["minecraft:obsidian", "minecraft:nether_portal"]
    with open(os.path.join(MIXED_WORLD, "region", "r.0.0.mca"), "wb") as f:
        f.write(_build_region([(0, 0, _chunk_nbt_blocks(0, 0, blocks))]))
    with open(os.path.join(MIXED_WORLD, "DIM-1", "region", "r.0.0.mca"), "wb") as f:
        f.write(_build_region([(0, 0, _chunk_nbt_blocks(0, 0, blocks))]))
    with open(os.path.join(tf, "region", "r.0.0.mca"), "wb") as f:
        f.write(_build_region([(0, 0, _chunk_nbt_blocks(0, 0, blocks))]))
    print("已生成混合布局测试世界 -> %s" % MIXED_WORLD)


if __name__ == "__main__":
    build()
    build_portal_world()
    build_new_layout_world()
    build_mixed_layout_world()
