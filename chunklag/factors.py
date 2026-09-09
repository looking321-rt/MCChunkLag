# -*- coding: utf-8 -*-
"""
卡顿因子提取 —— 从区块 NBT 里数出"这个区块会持续干多少活"的各类因子。

原理：spark 靠运行时采样得"哪个函数占多少 CPU"；离线存档没有运行时数据，
只能从**存档里存的实体 / 方块实体**反推"这个区块平时要处理多少逻辑"。
评分是**启发式**（不是 mspt 实测），用于横向对比哪些区块更可能卡。

factor key = 大类(entities/be) + 子类。chunk_score = Σ(count × weight)。
"""
# 因子定义（用于报告分组展示 + 权重）
FACTOR_GROUPS = [
    ("实体(entities)", [
        ("entities_hostile", "敌对怪物", 3),
        ("entities_villager", "村民", 2),
        ("entities_vehicle", "载具/矿车", 2),
        ("entities_animal", "动物", 1),
        ("entities_item", "物品/经验球", 1),
        ("entities_other", "其它实体", 1),
    ]),
    ("方块实体(block_entities)", [
        ("be_spawner", "刷怪笼", 4),
        ("be_container", "容器(箱/漏斗/潜影盒)", 2),
        ("be_redstone", "红石自更新(活塞/音符/指令块)", 2),
        ("be_furnace", "熔炉/机器", 2),
        ("be_other", "其它方块实体", 1),
    ]),
]

# 权重速查表
FACTOR_WEIGHTS = {k: w for _, items in FACTOR_GROUPS for k, _, w in items}

# 敌对怪物集合
_HOSTILE = {
    "zombie", "skeleton", "creeper", "spider", "cave_spider", "enderman",
    "witch", "blaze", "ghast", "slime", "magma_cube", "phantom", "drowned",
    "husk", "stray", "pillager", "ravager", "vindicator", "evoker", "vex",
    "shulker", "guardian", "elder_guardian", "hoglin", "zoglin",
    "piglin_brute", "wither_skeleton", "zombie_villager", "silverfish",
    "endermite", "breeze", "stray", "bogged", "skeleton_horse",
}

# 动物（passive）集合（非敌对、非村民、非物品）
_ANIMAL = {
    "cow", "pig", "sheep", "chicken", "horse", "donkey", "mule", "cat",
    "wolf", "rabbit", "fox", "bee", "panda", "goat", "frog", "ocelot",
    "allay", "axolotl", "camel", "sniffer", "armadillo", "parrot", "strider",
    "turtle", "villager", "wandering_trader", "trader_llama",
}

# 容器方块实体
_CONTAINER = {
    "chest", "barrel", "hopper", "shulker_box", "trapped_chest",
    "minecraft:chest", "minecraft:barrel", "minecraft:hopper",
    "minecraft:shulker_box", "minecraft:trapped_chest",
}

# 红石自更新类方块实体
_REDSTONE = {
    "piston", "sticky_piston", "moving_piston", "note_block", "command_block",
    "repeater", "comparator", "structure_block", "jigsaw",
}

# 熔炉/机器类
_FURNACE = {
    "furnace", "smoker", "blast_furnace", "brewing_stand", "campfire",
    "blast_furnace", "smoker", "dispenser", "dropper",
}


def _norm_id(raw_id):
    """归一化实体/方块实体 id：去掉 minecraft: 前缀，转小写。"""
    if not raw_id:
        return ""
    return str(raw_id).lower().replace("minecraft:", "")


def _get_ids(container, key):
    """从 compound 里取某键（兼容同名大小写变体），返回 list of compound。"""
    if isinstance(container, dict):
        if key in container and isinstance(container[key], list):
            yield from container[key]
    yield from ()


def _extract_entities(level):
    """返回实体列表（list of dict）。兼容 entities/Entities 大小写。"""
    for key in ("entities", "Entities"):
        val = level.get(key) if isinstance(level, dict) else None
        if isinstance(val, list):
            return [e for e in val if isinstance(e, dict)]
    return []


def _extract_block_entities(level):
    """返回方块实体列表。兼容 block_entities/TileEntities。"""
    for key in ("block_entities", "TileEntities"):
        val = level.get(key) if isinstance(level, dict) else None
        if isinstance(val, list):
            return [b for b in val if isinstance(b, dict)]
    return []


def _entity_factor(entity):
    """单个实体 → factor key（无则 None）。"""
    eid = entity.get("id")
    if eid is None:
        # 1.19+ 部分实体 id 在 entity_data 下
        edata = entity.get("entity_data")
        if isinstance(edata, dict):
            eid = edata.get("id")
    eid = _norm_id(eid)
    if not eid:
        return "entities_other"
    if "minecart" in eid:
        return "entities_vehicle"
    if eid in _HOSTILE:
        return "entities_hostile"
    if eid == "villager":
        return "entities_villager"
    if eid in ("item", "experience_orb") or eid == "item":
        return "entities_item"
    return "entities_other"


def _be_factor(be):
    """单个方块实体 → factor key。"""
    bid = _norm_id(be.get("id"))
    if not bid:
        return "be_other"
    if bid in ("spawner", "mob_spawner"):
        return "be_spawner"
    if bid in _CONTAINER:
        return "be_container"
    if bid in _REDSTONE:
        return "be_redstone"
    if bid in _FURNACE:
        return "be_furnace"
    return "be_other"


def analyze_chunk(nbt_dict):
    """
    分析一个区块，返回 {factor_key: count}。
    兼容：有 Level 包裹（1.18 前） vs 平铺（1.20.5+）。
    """
    level = nbt_dict.get("Level") if isinstance(nbt_dict, dict) else None
    if not isinstance(level, dict):
        level = nbt_dict or {}

    counts = {k: 0 for k in FACTOR_WEIGHTS}

    for entity in _extract_entities(level):
        key = _entity_factor(entity)
        counts[key] = counts.get(key, 0) + 1

    for be in _extract_block_entities(level):
        key = _be_factor(be)
        counts[key] = counts.get(key, 0) + 1

    return counts


def chunk_score(counts):
    """区块卡顿分 = Σ(count × weight)。"""
    total = 0
    for key, cnt in counts.items():
        total += cnt * FACTOR_WEIGHTS.get(key, 1)
    return total
