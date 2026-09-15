# -*- coding: utf-8 -*-
"""
卡顿因子提取 —— 从区块 NBT 里数出"这个区块会持续干多少活"的各类因子。

原理：spark 靠运行时采样得"哪个函数占多少 CPU"；离线存档没有运行时数据，
只能从**存档里存的实体 / 方块实体 / 会 tick 的方块**反推"这个区块平时要处理多少逻辑"。
评分是**启发式**（不是 mspt 实测），用于横向对比哪些区块更可能卡。

factor key = 大类(entities/be/blocks) + 子类。chunk_score = Σ(count × weight)。

## 分类口径（2026-09-15 细分，用户拍板）

### 实体（entities_*）
按"每 tick 干多少活"分档：BOSS > 敌对 > 村民 > 傀儡 > 漏斗矿车/命令方块矿车 >
箱/漏斗矿车 > 下落方块/激活 TNT > 普通矿车/动物/宠物/船 > 飞行 > 水生 > 弹射物 >
盔甲架/展示框（静态）；掉落物按「堆」计且权重极低（见下）。

### 方块实体（be_*）
每 tick 必做的（漏斗）> 持续生成的（刷怪笼/试炼刷怪箱）> 会 tick 的装置（幽匿系/命令方块/
活塞/合成器）> 触发才动的（发射器投掷器）> **烧炼中**的机器 > 静态容器（箱/桶/潜影盒）。
⚠️ 熔炉族按 `BurnTime/BrewTime/CookingTimes` 区分"正在烧"与"待机"——同一台熔炉两种状态
开销差一个量级，只数个数会把一堆静止熔炉算成高负载。

### 方块（blocks_*，普通方块，需解码位压缩数组才能按个数计）
只收**红石元件**（活跃/待机分开）与**火**。
⚠️ **流体（流动水/岩浆）与生长类作物故意不收**（2026-09-15 加进来后又撤掉，实测教训）：
   · 随机刻与方块数量无关 —— 每区块按 randomTickSpeed 固定抽样，138 万个作物与 1000 个抽样次数一样；
   · 稳态流体不再产生 scheduled tick，存档无法区分"正在流"与"早已静止"。
   按个数计会量纲爆炸：实测生存001 里这两类独吞 **95.6% 总分**，TOP 12 全是 lava/water/growt，
   玩家装置被完全挤出榜单。自然装饰（树叶/藤蔓/地衣/雪/冰）同理不收。
   想要这些信息请用 `tests/diag_ids.py`（诊断工具，只统计不计分）。
"""
# ---------------------------------------------------------------------------
# 一、因子表（分组展示 + 权重）
# ---------------------------------------------------------------------------
# 权重口径（2026-09-15 重做，用户拍板「掉落物 800~1000 个也不怎么卡」）：
#   **权重以 0.01 为最小单位**（600 = 6.00 基准分/个），值越大 = 每 tick 干活越多。
#   分档依据 = MC 服务端 tick 循环里各东西的实际开销：
#     · 每 tick 必做（漏斗扫上方物品；刷怪笼持续尝试生成）
#     · 持续 AI/寻路（BOSS > 敌对 > 村民(POI 寻路) > 傀儡 > 动物）
#     · 条件 tick（熔炉只在燃烧时 tick、发射器只在被触发时动、静态容器不 tick）
#     · 极轻（**掉落物按「堆」计**：同格同物品自动合并成一个实体，tick 只做重力/碰撞/拾取，
#       且 6000 tick=5 分钟后消失 —— 所以 3 分/堆，1000 个物品≈16 堆≈48 分，几乎不影响排序）
#   ⚠️ 这些数字是**待校准的经验值**：拿到真实卡顿点（spark/体感）后只改这张表即可。
FACTOR_GROUPS = [
    ("实体(entities)", [
        ("entities_boss", "BOSS(凋灵/末影龙/守夜人等)", 600),
        ("entities_minecart_special", "TNT/刷怪笼/命令方块矿车", 600),
        ("entities_minecart_cargo", "箱/漏斗/熔炉矿车", 400),
        ("entities_hostile", "敌对怪物", 300),
        ("entities_villager", "村民/流浪商人", 250),
        ("entities_tnt", "激活的TNT", 200),
        ("entities_golem", "傀儡(铁/雪)", 150),
        ("entities_minecart", "普通矿车", 150),
        ("entities_falling", "下落的方块", 150),
        ("entities_animal", "陆生动物", 100),
        ("entities_pet", "宠物/坐骑(狼猫马驴羊驼)", 100),
        ("entities_boat", "船/箱船", 100),
        ("entities_flying", "飞行生物(蝙蝠鹦鹉蜜蜂)", 80),
        ("entities_aquatic", "水生生物(鱼/鱿鱼/龟/美西螈)", 60),
        ("entities_projectile", "弹射物(箭/三叉戟/珍珠)", 30),
        ("entities_other", "其它实体", 30),
        ("entities_decor", "盔甲架/展示框/画(静态)", 20),
        ("entities_item", "掉落物/经验球(按堆)", 3),
    ]),
    ("方块实体(block_entities)", [
        ("be_hopper", "漏斗(每tick扫)", 600),
        ("be_spawner", "刷怪笼", 500),
        ("be_trial_spawner", "试炼刷怪箱", 500),
        ("be_sculk", "幽匿系(感测器/尖啸体/催化体)", 400),
        ("be_command", "命令方块/结构方块", 400),
        ("be_piston", "活塞/粘性活塞", 300),
        ("be_crafter", "合成器", 300),
        ("be_redstone_io", "发射器/投掷器", 150),
        ("be_smelting", "熔炉族(正在烧炼)", 150),
        ("be_bee", "蜂箱/蜂巢", 120),
        ("be_beacon", "信标/潮涌核心", 100),
        ("be_jukebox", "唱片机(播放中)", 100),
        ("be_daylight", "阳光传感器", 60),
        ("be_container", "容器(箱/陷阱箱/木桶，静态)", 30),
        ("be_shulker", "潜影盒", 30),
        ("be_machine", "熔炉族(待机)", 10),
        ("be_ender_chest", "末影箱(无库存)", 10),
        ("be_static", "静态(告示牌/旗帜/床/讲台/刷沙块)", 10),
        ("be_other", "其它方块实体(含 mod)", 30),
    ]),
    ("方块(blocks)", [
        # 红石元件**方块**（红石粉/中继器/比较器/侦测器/红石火把…）不是方块实体，
        # 只数 block_entities 时整类漏掉；Wiki 点名「红石元件（尤其红石粉）造成海量
        # 方块更新/光照更新」是 MSPT 大户。按**个数**计（解码 section.block_states.data）。
        ("blocks_redstone", "红石元件-活跃(通电的粉/中继器/侦测器)", 150),
        ("blocks_redstone_idle", "红石元件-待机(红石块/灯/拉杆/按钮)", 30),
        # 火会持续 tick（蔓延/熄灭），且**只有活动中才有火**（自然火很快烧完）→ 按个数计有意义
        ("blocks_fire", "火/灵魂火", 150),
    ]),
]

# 权重速查表
FACTOR_WEIGHTS = {k: w for _, items in FACTOR_GROUPS for k, _, w in items}

# ---------------------------------------------------------------------------
# 二、实体 id 集合（归一化后比较：小写、无 minecraft: 前缀）
# ---------------------------------------------------------------------------
# BOSS：AI 最重、每 tick 大量寻路/目标选择/特殊技能
_BOSS = {"wither", "ender_dragon", "warden", "elder_guardian"}

# 敌对怪物（会主动索敌 + 寻路）
_HOSTILE = {
    "zombie", "skeleton", "creeper", "spider", "cave_spider", "enderman",
    "witch", "blaze", "ghast", "slime", "magma_cube", "phantom", "drowned",
    "husk", "stray", "pillager", "ravager", "vindicator", "evoker", "vex",
    "shulker", "guardian", "hoglin", "zoglin", "piglin", "piglin_brute",
    "zombie_villager", "silverfish", "endermite", "breeze", "bogged",
    "zombified_piglin", "illusioner", "giant", "skeleton_horse",
}

# 村民类（POI/工作站点寻路、交易 AI，比普通动物贵）
_VILLAGER = {"villager", "wandering_trader"}

# 傀儡：有寻路 + 索敌（铁傀儡攻击、雪傀儡投雪球）
_GOLEM = {"iron_golem", "snow_golem"}

# 陆生动物（被动，数量大）
_ANIMAL = {
    "cow", "pig", "sheep", "chicken", "rabbit", "fox", "panda", "goat",
    "frog", "camel", "sniffer", "armadillo", "mooshroom", "polar_bear",
    "tadpole",
}

# 宠物 / 坐骑（跟随主人寻路；马驴骡羊驼还带载具逻辑）
_PET = {
    "wolf", "cat", "ocelot", "horse", "donkey", "mule", "trader_llama",
    "llama", "zombie_horse",
}

# 飞行（蝙蝠乱飞、鹦鹉跟随、蜜蜂采蜜寻路）—— ⚠️ vex 归敌对（_HOSTILE 先判定）
_FLYING = {"bat", "parrot", "allay", "bee"}

# 水生（游动 AI 较轻，且多在水里不常被加载）
_AQUATIC = {
    "cod", "salmon", "tropical_fish", "pufferfish", "squid", "glow_squid",
    "dolphin", "turtle", "axolotl",
}

# 弹射物/效果实体（tick 只做飞行与碰撞；箭落地后基本静止）
_PROJECTILE = {
    "arrow", "spectral_arrow", "trident", "snowball", "egg", "ender_pearl",
    "potion", "experience_bottle", "llama_spit", "shulker_bullet",
    "fireball", "small_fireball", "dragon_fireball", "wither_skull",
    "firework_rocket", "wind_charge", "breeze_wind_charge", "fishing_bobber",
    "eye_of_ender", "evoker_fangs", "area_effect_cloud", "lightning_bolt",
}

# 静态展示类（几乎不 tick，只有实体遍历成本）
_DECOR = {
    "armor_stand", "item_frame", "glow_item_frame", "painting", "leash_knot",
    "item_display", "block_display", "text_display", "marker", "interaction",
}

# 矿车三档（1.21.2 起矿车穿门冷却降到 0.5s，矿车加载器成为常加载手段）
_MINECART_SPECIAL = {"tnt_minecart", "spawner_minecart", "command_block_minecart"}
_MINECART_CARGO = {"chest_minecart", "hopper_minecart", "furnace_minecart"}

# ---------------------------------------------------------------------------
# 三、方块实体 id 集合
# ---------------------------------------------------------------------------
_CONTAINER = {"chest", "barrel", "trapped_chest"}          # 静态：不 tick，被比较器读才醒
_SHULKER = {"shulker_box"}                                  # 静态（内容随方块走）
_ENDER_CHEST = {"ender_chest"}                              # 无库存（玩家末影箱在玩家数据里）
_HOPPER = {"hopper"}                                        # **每 tick** 扫上方物品，经典 MSPT 大户
_SPAWNER = {"spawner", "mob_spawner"}
_TRIAL_SPAWNER = {"trial_spawner"}
_SCULK = {"sculk_sensor", "calibrated_sculk_sensor", "sculk_shrieker", "sculk_catalyst"}
_COMMAND = {"command_block", "chain_command_block", "repeating_command_block",
            "structure_block", "jigsaw"}
_PISTON = {"piston", "sticky_piston", "moving_piston"}
_CRAFTER = {"crafter"}
_REDSTONE_IO = {"dispenser", "dropper"}                     # 被触发才动（不像漏斗每 tick）
_BEE = {"beehive", "bee_nest"}
_BEACON = {"beacon", "conduit"}
_JUKEBOX = {"jukebox"}
_DAYLIGHT = {"daylight_detector"}
# 熔炉族：**燃烧时才 tick**（BurnTime>0 / BrewTime>0 / CookingTimes 有值）
_SMELTER = {"furnace", "blast_furnace", "smoker", "brewing_stand",
            "campfire", "soul_campfire"}
# 静态方块实体（存数据但不 tick）
_STATIC_BE = {
    "sign", "hanging_sign", "banner", "wall_banner", "bed", "lectern", "skull",
    "brushable_block", "decorated_pot", "chiseled_bookshelf", "end_portal",
    "end_gateway", "bell",
}


# ---------------------------------------------------------------------------
# 四、归类函数
# ---------------------------------------------------------------------------
def _norm_id(raw_id):
    """归一化实体/方块实体 id：去掉 minecraft: 前缀，转小写。"""
    if not raw_id:
        return ""
    return str(raw_id).lower().replace("minecraft:", "")


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
    """
    单个实体 → factor key（无则 None）。

    判定顺序有讲究：矿车家族必须先于"载具/宠物"判定（`hopper_minecart` 里含 minecart，
    `chest_boat` 里含 boat），否则细分档位会被更宽的规则吃掉。
    """
    eid = _entity_id(entity)
    if not eid:
        return "entities_other"
    if eid in ("item", "experience_orb"):
        return "entities_item"
    if eid in _MINECART_SPECIAL:
        return "entities_minecart_special"
    if eid in _MINECART_CARGO:
        return "entities_minecart_cargo"
    if "minecart" in eid:                       # 普通矿车（含 mod 变体）
        return "entities_minecart"
    if "boat" in eid or eid.endswith("_raft"):  # 船/箱船（1.19+ id 带木种前缀）
        return "entities_boat"
    if eid == "falling_block":
        return "entities_falling"
    if eid == "tnt":
        return "entities_tnt"
    if eid in _BOSS:
        return "entities_boss"
    if eid in _HOSTILE:
        return "entities_hostile"
    if eid in _VILLAGER:
        return "entities_villager"
    if eid in _GOLEM:
        return "entities_golem"
    if eid in _DECOR:
        return "entities_decor"
    if eid in _PROJECTILE:
        return "entities_projectile"
    if eid in _FLYING:
        return "entities_flying"
    if eid in _PET:
        return "entities_pet"
    if eid in _ANIMAL:
        return "entities_animal"
    if eid in _AQUATIC or eid.endswith("_fish") or eid.endswith("_squid"):
        return "entities_aquatic"
    return "entities_other"


def _be_active(be):
    """熔炉族是否**正在烧炼**（决定 be_smelting 还是 be_machine 档）。"""
    for key in ("BurnTime", "BrewTime", "cook_time", "cooking_time"):
        v = be.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            return True
    for key in ("CookingTimes", "cooking_times"):        # 营火：4 个槽的进度
        v = be.get(key)
        if isinstance(v, list) and any(isinstance(x, (int, float)) and x > 0 for x in v):
            return True
    return False


def _be_factor(be):
    """单个方块实体 → factor key。"""
    bid = _norm_id(be.get("id"))
    if not bid:
        return "be_other"
    if bid in _HOPPER:
        return "be_hopper"
    if bid in _SPAWNER:
        return "be_spawner"
    if bid in _TRIAL_SPAWNER:
        return "be_trial_spawner"
    if bid in _SCULK:
        return "be_sculk"
    if bid in _COMMAND:
        return "be_command"
    if bid in _PISTON:
        return "be_piston"
    if bid in _CRAFTER:
        return "be_crafter"
    if bid in _REDSTONE_IO:
        return "be_redstone_io"
    if bid in _SMELTER:
        return "be_smelting" if _be_active(be) else "be_machine"
    if bid in _BEE:
        return "be_bee"
    if bid in _BEACON:
        return "be_beacon"
    if bid in _JUKEBOX:
        return "be_jukebox"
    if bid in _DAYLIGHT:
        return "be_daylight"
    if bid == "vault":
        return "be_crafter"                      # 宝库：被触发时才吐战利品，与合成器同档
    if bid in _CONTAINER:
        return "be_container"
    if bid in _SHULKER:
        return "be_shulker"
    if bid in _ENDER_CHEST:
        return "be_ender_chest"
    if bid.endswith("_shulker_box"):
        return "be_shulker"
    if bid.endswith("_bed"):
        return "be_static"
    if bid in _STATIC_BE:
        return "be_static"
    return "be_other"


# ---------------------------------------------------------------------------
# 五、会 tick 的普通方块（需要解码位压缩数组才能按个数计）
# ---------------------------------------------------------------------------
# ⚠️ 活塞/粘性活塞**同时是方块实体**（_PISTON），方块侧必须返回 None 避免双计；
#    幽匿感测器/阳光传感器是方块实体，同理不在方块侧计。
#    红石块/红石灯是**静态电源**（不每 tick 干活，红石灯只在状态变化时更新一次），
#    所以归"待机"档而不是活跃档 —— 否则一面装饰性红石灯墙会被算成高负载。
# ⚠️ **刻意不按个数计分的方块类别**（2026-09-15 实测踩坑，别再退回）：
#   · 流体（water/lava 的 level≠0）：**稳态流体不产生 scheduled tick** —— 存档无法区分
#     "正在流动"与"早已静止"，凡 level≠0 就计等于把整片海洋/洞穴岩浆池算成活动。
#   · 生长类（作物/树苗/甘蔗/树叶/紫水晶…）：**随机刻与方块数量无关** —— MC 每 tick 按
#     randomTickSpeed 在每 section 抽固定次数，138 万个作物和 1000 个作物的抽样次数一样，
#     只是抽中方块的处理成本不同 —— 按个数计是量纲错误。
#   实测证据（三个真实存档）：加进来后生存001 的「生长类+流体」独吞 **95.6% 总分**，
#   TOP 12 全是 lava/water/growt，**玩家装置被完全挤出榜单**；生电同样出现水域挤占 TOP2/3/5。
#   想要这类信息请用 `tests/diag_ids.py`（诊断工具，只统计不计分）。
_RS_ACTIVE_NAMES = {"redstone_wire", "repeater", "comparator", "observer"}
_RS_IDLE_NAMES = {
    "redstone_block", "redstone_lamp", "lever", "target", "tripwire_hook",
    "tripwire", "note_block", "powered_rail", "detector_rail", "activator_rail",
}


def _block_factor(name, props):
    """一个 palette 条目（方块名 + 方块状态）→ factor key；不计分的返回 None。"""
    n = _norm_id(name)
    if not n:
        return None
    # --- 红石元件：活跃 vs 待机（读方块状态判是否通电） ---
    if n in _RS_ACTIVE_NAMES:
        if n == "redstone_wire":
            return "blocks_redstone" if str(props.get("power", "0")) != "0" else "blocks_redstone_idle"
        if n in ("repeater", "comparator", "observer"):
            on = str(props.get("powered", "")).lower() == "true"
            return "blocks_redstone" if on else "blocks_redstone_idle"
    if n in ("redstone_torch", "redstone_wall_torch"):
        on = str(props.get("lit", "")).lower() == "true"
        return "blocks_redstone" if on else "blocks_redstone_idle"
    if n in _RS_IDLE_NAMES:
        return "blocks_redstone_idle"
    if n.endswith("_button") or n.endswith("_pressure_plate"):
        return "blocks_redstone_idle"
    # 注：活塞/粘性活塞是**方块实体**（be_piston，见 _PISTON），不在这里；幽匿感测器/
    #     阳光传感器同理（be_sculk / be_daylight）。两处都不收，避免双计分。
    # 火：唯一按个数计的非红石方块 —— 会持续 tick（蔓延/熄灭）且**只存在于活动中**
    if n in ("fire", "soul_fire"):
        return "blocks_fire"
    # 流体与生长类**刻意返回 None**（量纲错误，理由见上方大段注释与实测证据）
    return None


def _section_index_counts(bs):
    """
    解出 section 里每个 palette 索引出现多少次（block_states.data → 索引频次）。

    ⚠️ 打包方式**实测踩坑记录（2026-09-15，务必别退回）**：MC 的 long 数组是
    **padded（每个 long 内取整、entry 不跨 long 边界）** —— 每 long 装 `64 // bits` 个 entry，
    余位浪费；longs 数 = ceil(4096 / (64 // bits))。
    实测证据：某 section palette=30 → bits=5 → data 长度 **342**，正是
    ceil(4096 / 12) = 342；若按"紧凑跨 long"解应得 320 —— 我第一版就是按紧凑写的，
    结果位错位、索引随机命中 observer，把 3.2 万个方块虚报成 32909 个侦测器
    （全图虚高到 67 万，一眼假）。这里两种都兼容（按 data 长度就近判别），
    并加**越界索引健全性检查**：解出的索引若大量超出 palette 范围，说明判错 → 返回空（宁可漏，不可虚高）。
    """
    pal = bs.get("palette")
    data = bs.get("data")
    if not isinstance(pal, list) or not isinstance(data, list) or not data:
        return {}
    bits = max(4, (len(pal) - 1).bit_length())
    mask = (1 << bits) - 1
    per_long = 64 // bits
    n_padded = (4096 + per_long - 1) // per_long          # 不跨 long 边界
    n_compact = (4096 * bits + 63) // 64                   # 紧凑跨 long
    padded = abs(len(data) - n_padded) <= abs(len(data) - n_compact)

    counts = {}
    over = 0
    if padded:
        for li, v in enumerate(data):
            if not isinstance(v, int):
                continue
            u = v & 0xFFFFFFFFFFFFFFFF
            base = li * per_long
            for k in range(per_long):
                pos = base + k
                if pos >= 4096:
                    break
                idx = (u >> (k * bits)) & mask
                if idx >= len(pal):
                    over += 1
                counts[idx] = counts.get(idx, 0) + 1
    else:
        stream = 0
        for i, v in enumerate(data):
            if isinstance(v, int):
                stream |= (v & 0xFFFFFFFFFFFFFFFF) << (64 * i)
        for i in range(4096):
            idx = (stream >> (i * bits)) & mask
            if idx >= len(pal):
                over += 1
            counts[idx] = counts.get(idx, 0) + 1

    if over > 4096 * 0.01:                                  # >1% 越界 = 打包/位宽判错
        return {}
    return counts


def _sections(nbt_dict):
    level = nbt_dict.get("Level") if isinstance(nbt_dict, dict) else None
    if not isinstance(level, dict):
        level = nbt_dict or {}
    return [s for s in (level.get("sections") or []) if isinstance(s, dict)]


def _block_states_of(sec):
    bs = sec.get("block_states")
    if isinstance(bs, dict):
        return bs
    return {"palette": sec.get("palette"), "data": sec.get("data")}   # 旧版挂在 section 上


def count_tick_blocks(nbt_dict):
    """
    统计区块里「会 tick 的普通方块」（红石元件/流体/火/生长类），返回 {factor_key: 个数}。

    只对 palette 里**含目标方块**的 section 解码 —— 大多数段是石头/空气，跳过它们
    才不会把扫描拖慢（实测加进来后仍是秒级）。
    """
    out = {}
    for sec in _sections(nbt_dict):
        bs = _block_states_of(sec)
        pal = bs.get("palette")
        if not isinstance(pal, list):
            continue
        wanted = {}
        for i, p in enumerate(pal):
            if not isinstance(p, dict):
                continue
            props = p.get("Properties")
            key = _block_factor(p.get("Name"), props if isinstance(props, dict) else {})
            if key:
                wanted[i] = key
        if not wanted:
            continue
        counts = _section_index_counts(bs)
        for i, key in wanted.items():
            c = counts.get(i, 0)
            if c:
                out[key] = out.get(key, 0) + c
    return out


# ---------------------------------------------------------------------------
# 六、常加载装置判据（供 loaders/main 使用）
# ---------------------------------------------------------------------------
def _chunk_block_entries(nbt_dict):
    """
    收集区块 palette 的 [(方块名小写, Properties 字典), ...]。

    注意：MC 1.18+ 方块 palette 在 section.block_states.palette（非 section.palette），
    旧版/构造数据才在 section.palette —— 两种都兼容。Properties 是方块状态
    （如 powered_rail 的 powered: "true"），新版（26.x 实测）照样存在。
    """
    out = []
    for sec in _sections(nbt_dict):
        pal = _block_states_of(sec).get("palette")
        for p in (pal if isinstance(pal, list) else []):
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
    for be in _extract_block_entities(_level_of(nbt_dict)):
        if _norm_id(be.get("id")) in {r.replace("minecraft:", "") for r in _REDSTONE_BE_IDS}:
            return True
    return any(n in names for n in _REDSTONE_BLOCKS)


def has_obsidian(nbt_dict):
    """区块是否含黑曜石（地狱门框架）。"""
    return "minecraft:obsidian" in _chunk_block_names(nbt_dict)


def has_redstone_kit(nbt_dict):
    """区块是否含红石器件（dropper/hopper/红石方块等），用作常加载器信号。"""
    names = _chunk_block_names(nbt_dict)
    if any(n in names for n in _REDSTONE_BLOCKS):
        return True
    ids = {r.replace("minecraft:", "") for r in _REDSTONE_BE_IDS}
    for be in _extract_block_entities(_level_of(nbt_dict)):
        if _norm_id(be.get("id")) in ids:
            return True
    return False


# ---------------------------------------------------------------------------
# 七、主入口
# ---------------------------------------------------------------------------
def _level_of(nbt_dict):
    """区块 NBT 的实体/方块实体所在层：1.18 前有 Level 包裹，之后平铺在顶层。"""
    level = nbt_dict.get("Level") if isinstance(nbt_dict, dict) else None
    return level if isinstance(level, dict) else (nbt_dict or {})


def analyze_chunk(nbt_dict):
    """
    分析一个区块，返回 {factor_key: count}。
    兼容：有 Level 包裹（1.18 前） vs 平铺（1.20.5+）。

    计数口径：每个实体/方块实体按 **1 个**计；掉落物是「一堆 = 一个 item 实体」，
    所以**按堆计**（不再看堆里的 Count —— 物品个数不影响服务端 tick 开销）。
    """
    level = _level_of(nbt_dict)
    counts = {k: 0 for k in FACTOR_WEIGHTS}

    for entity in _extract_entities(level):
        key = _entity_factor(entity)
        counts[key] = counts.get(key, 0) + 1

    for be in _extract_block_entities(level):
        key = _be_factor(be)
        counts[key] = counts.get(key, 0) + 1

    # 会 tick 的普通方块（红石元件/流体/火/生长类）需要解码位压缩数组，按**个数**计入
    for key, cnt in count_tick_blocks(nbt_dict).items():
        counts[key] = counts.get(key, 0) + cnt

    return counts


def chunk_score(counts):
    """区块卡顿分 = Σ(count × weight)。权重单位见 FACTOR_GROUPS（600 = 6.00 基准分）。"""
    total = 0
    for key, cnt in counts.items():
        total += cnt * FACTOR_WEIGHTS.get(key, 1)
    return total
