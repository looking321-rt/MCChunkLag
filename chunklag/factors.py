# -*- coding: utf-8 -*-
"""
卡顿因子提取 —— 从区块 NBT 里数出"这个区块会持续干多少活"的各类因子。

原理：spark 靠运行时采样得"哪个函数占多少 CPU"；离线存档没有运行时数据，
只能从**存档里存的实体 / 方块实体**反推"这个区块平时要处理多少逻辑"。
评分是**启发式**（不是 mspt 实测），用于横向对比哪些区块更可能卡。

factor key = 大类(entities/be) + 子类。chunk_score = Σ(count × weight)。
"""
# 因子定义（用于报告分组展示 + 权重）
#
# 权重口径（2026-09-15 重做，用户拍板「掉落物 800~1000 个也不怎么卡」）：
#   **权重以 0.01 为最小单位**（600 = 6.00 基准分/个），值越大 = 每 tick 干活越多。
#   分档依据 = MC 服务端 tick 循环里各东西的实际开销：
#     · 每 tick 必做（漏斗每 tick 扫上方物品；刷怪笼持续尝试生成；红石元件海量方块更新）
#     · 有 AI/寻路（怪 > 村民 > 动物）
#     · 静态/条件 tick（箱子不 tick、熔炉只在燃烧时 tick、红石自更新类只在状态变化时动）
#     · 极轻（**掉落物按「堆」计**：同格同物品自动合并成一个实体，tick 只做重力/碰撞/拾取，
#       且 6000 tick=5 分钟后消失 —— 所以 3 分/堆，1000 个物品≈16 堆≈48 分，几乎不影响排序）
#   ⚠️ 这些数字是**待校准的经验值**：拿到真实卡顿点（spark/体感）后只改这张表即可。
FACTOR_GROUPS = [
    ("实体(entities)", [
        ("entities_hostile", "敌对怪物", 300),
        ("entities_villager", "村民", 250),
        ("entities_vehicle", "载具/矿车", 150),
        ("entities_animal", "动物", 100),
        ("entities_other", "其它实体", 50),
        ("entities_item", "掉落物/经验球(按堆)", 3),
    ]),
    ("方块实体(block_entities)", [
        ("be_hopper", "漏斗(每tick扫)", 600),
        ("be_spawner", "刷怪笼", 500),
        ("be_redstone", "红石自更新(活塞/音符/指令块)", 300),
        ("be_furnace", "熔炉/机器", 50),
        ("be_container", "容器(箱/桶/潜影盒，静态)", 30),
        ("be_other", "其它方块实体", 50),
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

# 容器方块实体（**静态**：箱子/桶/潜影盒不每 tick 干活，只有被比较器读时才醒）
_CONTAINER = {
    "chest", "barrel", "shulker_box", "trapped_chest",
    "minecraft:chest", "minecraft:barrel",
    "minecraft:shulker_box", "minecraft:trapped_chest",
}

# 漏斗（**每 tick** 搜索上方物品实体 + 尝试传输，是经典 MSPT 大户，单独一档）
_HOPPER = {"hopper", "minecraft:hopper"}

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


def _entity_id(entity):
    """实体 id（归一化，去 minecraft: 前缀）。1.19+ 部分实体把 id 放在 entity_data 下。"""
    eid = entity.get("id")
    if eid is None:
        edata = entity.get("entity_data")
        if isinstance(edata, dict):
            eid = edata.get("id")
    return _norm_id(eid)


def _entity_factor(entity):
    """单个实体 → factor key（无则 None）。"""
    eid = _entity_id(entity)
    if not eid:
        return "entities_other"
    if "minecart" in eid:
        return "entities_vehicle"
    if eid in _HOSTILE:
        return "entities_hostile"
    if eid == "villager":
        return "entities_villager"
    if eid in ("item", "experience_orb"):
        return "entities_item"
    return "entities_other"


def _be_factor(be):
    """单个方块实体 → factor key。"""
    bid = _norm_id(be.get("id"))
    if not bid:
        return "be_other"
    if bid in ("spawner", "mob_spawner"):
        return "be_spawner"
    if bid in _HOPPER:
        return "be_hopper"
    if bid in _CONTAINER:
        return "be_container"
    if bid in _REDSTONE:
        return "be_redstone"
    if bid in _FURNACE:
        return "be_furnace"
    return "be_other"


def has_portal(nbt_dict):
    """检测区块是否含下界传送门方块 nether_portal，用于识别传送门常加载区。"""
    return any(n.endswith("nether_portal") for n in _chunk_block_names(nbt_dict))


_REDSTONE_BLOCKS = {
    "minecraft:redstone_wire", "minecraft:repeater", "minecraft:comparator",
    "minecraft:piston", "minecraft:sticky_piston", "minecraft:observer",
    "minecraft:redstone_lamp", "minecraft:dropper", "minecraft:dispenser",
    "minecraft:hopper", "minecraft:note_block", "minecraft:redstone_block",
    "minecraft:target", "minecraft:detector_rail", "minecraft:powered_rail",
    "minecraft:daylight_detector",
}
_REDSTONE_BE_IDS = {
    "minecraft:dropper", "minecraft:dispenser", "minecraft:hopper",
    "minecraft:piston", "minecraft:sticky_piston", "minecraft:observer",
    "minecraft:command_block", "minecraft:repeater", "minecraft:comparator",
}


def _chunk_block_entries(nbt_dict):
    """
    收集区块 palette 的 [(方块名小写, Properties 字典), ...]。

    注意：MC 1.18+ 方块 palette 在 section.block_states.palette（非 section.palette），
    旧版/构造数据才在 section.palette —— 两种都兼容。Properties 是方块状态
    （如 powered_rail 的 powered: "true"），新版（26.x 实测）照样存在。
    """
    level = nbt_dict.get("Level") if isinstance(nbt_dict, dict) else None
    if not isinstance(level, dict):
        level = nbt_dict or {}
    out = []
    for sec in level.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        bs = sec.get("block_states") if isinstance(sec, dict) else None
        pal = bs.get("palette") if isinstance(bs, dict) else None
        if not isinstance(pal, list):
            pal = sec.get("palette")
        for p in pal or []:
            if not isinstance(p, dict):
                continue
            nm = str(p.get("Name", "")).lower()
            if not nm:
                continue
            props = p.get("Properties")
            out.append((nm, props if isinstance(props, dict) else {}))
    return out


def _chunk_block_names(nbt_dict):
    """收集区块 palette 里所有方块名（归一化）。"""
    return {n for n, _p in _chunk_block_entries(nbt_dict)}


def has_active_powered_rail(nbt_dict):
    """
    区块是否含**已激活**的动力铁轨（powered_rail 且 Properties.powered == "true"）。

    2026-09-12 用户指定的矿车地狱门加载器关键证据之一：
    1.21.2 起矿车穿门冷却 15s → 0.5s，只需让矿车在铁轨上循环跑，被激活的动力铁轨
    就是"矿车确实在循环"的硬证据（未激活的动力铁轨只是普通轨道，不算）。
    """
    for name, props in _chunk_block_entries(nbt_dict):
        if name.endswith("powered_rail") and str(props.get("powered", "")).lower() == "true":
            return True
    return False


def is_portal_loader(nbt_dict):
    """
    疑似传送门常加载器：同一区块同时有 黑曜石(门框) + 红石器件。
    黑曜石+红石组合比纯 nether_portal 更鲁棒（门方块可能未保存，但黑曜石框架+红石装置在）。
    """
    names = _chunk_block_names(nbt_dict)
    if "minecraft:obsidian" not in names:
        return False
    level = nbt_dict.get("Level") if isinstance(nbt_dict, dict) else None
    if not isinstance(level, dict):
        level = nbt_dict or {}
    # 红石方块实体
    for be in _extract_block_entities(level):
        if _norm_id(be.get("id")) in {r.replace("minecraft:", "") for r in _REDSTONE_BE_IDS}:
            return True
    # 红石方块
    return any(n in names for n in _REDSTONE_BLOCKS)


def has_obsidian(nbt_dict):
    """区块是否含黑曜石（地狱门框架）。"""
    return "minecraft:obsidian" in _chunk_block_names(nbt_dict)


def has_redstone_kit(nbt_dict):
    """区块是否含红石器件（dropper/hopper/红石方块等），用作常加载器信号。"""
    names = _chunk_block_names(nbt_dict)
    if any(n in names for n in _REDSTONE_BLOCKS):
        return True
    level = nbt_dict.get("Level") if isinstance(nbt_dict, dict) else None
    if not isinstance(level, dict):
        level = nbt_dict or {}
    ids = {r.replace("minecraft:", "") for r in _REDSTONE_BE_IDS}
    for be in _extract_block_entities(level):
        if _norm_id(be.get("id")) in ids:
            return True
    return False


def analyze_chunk(nbt_dict):
    """
    分析一个区块，返回 {factor_key: count}。
    兼容：有 Level 包裹（1.18 前） vs 平铺（1.20.5+）。

    计数口径：每个实体/方块实体按 **1 个**计；掉落物是「一堆 = 一个 item 实体」，
    所以**按堆计**（不再看堆里的 Count —— 物品个数不影响服务端 tick 开销）。
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
    """区块卡顿分 = Σ(count × weight)。权重单位见 FACTOR_GROUPS（600 = 6.00 基准分）。"""
    total = 0
    for key, cnt in counts.items():
        total += cnt * FACTOR_WEIGHTS.get(key, 1)
    return total
