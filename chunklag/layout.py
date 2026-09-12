# -*- coding: utf-8 -*-
"""
存档布局解析 —— 兼容新旧两种世界目录结构（26.x 起 Mojang 改了布局）。

新布局（1.21.x 后期 ~ 26.x）：
  <world>/level.dat
  <world>/dimensions/<namespace>/<dim>/{region,entities,poi}/*.mca
  <world>/players/data/<uuid>.dat          # 玩家数据（原名 playerdata/）
  <world>/data/...                          # mod 常加载等
旧布局（1.20 及更早）：
  <world>/region/*.mca、<world>/entities/*.mca           # 主世界
  <world>/DIM-1/{region,entities}/*.mca                  # 下界
  <world>/DIM1/{region,entities}/*.mca                   # 末地
  <world>/playerdata/<uuid>.dat
"""
import os

# 维度 id → 中文名
DIM_LABELS = {
    "minecraft:overworld": "主世界",
    "minecraft:the_nether": "下界",
    "minecraft:the_end": "末地",
}

# CLI 的 --dim 取值 → 维度 id（保持 0/-1/1 语义，兼容旧脚本）
DIM_SEL_TO_ID = {
    "0": "minecraft:overworld",
    "-1": "minecraft:the_nether",
    "1": "minecraft:the_end",
}
DIM_ID_TO_KEY = {v: k for k, v in DIM_SEL_TO_ID.items()}

# 旧布局的子目录名
_LEGACY_SUBDIRS = {
    "": "minecraft:overworld",
    "DIM-1": "minecraft:the_nether",
    "DIM1": "minecraft:the_end",
}

# 标准维度排序权重（主世界/下界/末地优先，自定义维度（如 twilightforest:twilight_forest）排后面）
_DIM_ORDER = {"minecraft:overworld": 0, "minecraft:the_nether": 1, "minecraft:the_end": 2}


def dimension_dirs(world_dir):
    """
    返回 [(dim_id, 维度目录)]，只含**确实存在 region/** 的维度。

    兼容三种情况（**可混存**）：
      · 新布局：`<world>/dimensions/<namespace>/<dim>/{region,…}`
      · 旧布局：`<world>/region`（主世界）、`<world>/DIM-1/region`、`<world>/DIM1/region`
      · **混存**：1.20 时代装了自定义维度 mod（如暮色森林）会在 `<world>/dimensions/`
        下开维度目录，而主世界仍在 `<world>/region` —— 两种必须都收！
        （2026-09-12 踩坑：早期写成"有 dimensions/ 就全按新布局"，导致「001」整合包存档
        只剩暮色森林维度、主世界被漏掉 → CLI 报"没有 region 区块数据"）

    顺序：主世界 → 下界 → 末地 → 其它自定义维度（保证批量扫描 results[0] 是主世界）。
    """
    out = []
    seen = set()

    base = os.path.join(world_dir, "dimensions")
    if os.path.isdir(base):
        for ns in sorted(os.listdir(base)):
            nsp = os.path.join(base, ns)
            if not os.path.isdir(nsp):
                continue
            for dim in sorted(os.listdir(nsp)):
                d = os.path.join(nsp, dim)
                if not os.path.isdir(os.path.join(d, "region")):
                    continue
                dim_id = "%s:%s" % (ns, dim)
                if dim_id not in seen:
                    seen.add(dim_id)
                    out.append((dim_id, d))

    for sub, dim_id in _LEGACY_SUBDIRS.items():
        if dim_id in seen:
            continue
        d = os.path.join(world_dir, sub) if sub else world_dir
        if os.path.isdir(os.path.join(d, "region")):
            seen.add(dim_id)
            out.append((dim_id, d))

    out.sort(key=lambda it: (_DIM_ORDER.get(it[0], 9), it[0]))
    return out


def dim_dir(world_dir, dim_id):
    """按维度 id 取维度目录（不存在返回 None）。"""
    for did, d in dimension_dirs(world_dir):
        if did == dim_id:
            return d
    return None


def dim_label(dim_id):
    return DIM_LABELS.get(dim_id, dim_id)


def dim_key(dim_id):
    """维度 id → 旧语义 key（0/-1/1）；自定义维度返回 None。"""
    return DIM_ID_TO_KEY.get(dim_id)


def player_data_files(world_dir):
    """玩家数据文件（新版 players/data/*.dat，旧版 playerdata/*.dat）；跳过 .dat_old 备份。"""
    for sub in (os.path.join("players", "data"), "playerdata"):
        d = os.path.join(world_dir, sub)
        if not os.path.isdir(d):
            continue
        files = []
        for name in sorted(os.listdir(d)):
            if not name.endswith(".dat"):
                continue                      # xxx.dat_old 之类备份自带 _old 后缀，不会以 .dat 结尾
            files.append(os.path.join(d, name))
        if files:
            return files
    return []


def spawn_position(world_dir):
    """
    出生点 → (x, y, z, dim_id) 或 None。
    新版 level.dat：spawn: {pos:[x,y,z], dimension:"minecraft:overworld", yaw, pitch}
    旧版 level.dat：Data.SpawnX / SpawnY / SpawnZ
    """
    from . import leveldat

    data, _dv = leveldat.parse_level_dat(os.path.join(world_dir, "level.dat"))
    if not data:
        return None
    spawn = data.get("spawn")
    if isinstance(spawn, dict) and isinstance(spawn.get("pos"), list) and len(spawn["pos"]) >= 3:
        p = spawn["pos"]
        return (p[0], p[1], p[2], spawn.get("dimension", "minecraft:overworld"))
    sx, sz = data.get("SpawnX"), data.get("SpawnZ")
    if sx is not None and sz is not None:
        return (sx, data.get("SpawnY", 64), sz, "minecraft:overworld")
    return None
