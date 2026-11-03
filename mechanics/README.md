# 游戏机制

这些文档讲《星引擎Party/吉星派对》（Astral Party）的玩法机制：规则、计算方式、筹码和角色之间怎么相互作用，以及地图机关等。游戏数据（角色、筹码、地图、畸变组件的名称和数值）单独放在 `data/` 下的 JSON 里，以那里的为准；游戏更新后直接改 `data/` 里的 JSON，再运行 `tools/check_data.py` 检查。

| 文档 | 内容 |
|---|---|
| [chips.md](chips.md) | 筹码：获取途径、三选一生成、刷新、内置推荐分，以及筹码之间的交互细节 |
| [characters.md](characters.md) | 角色：技能、潜能、关键词状态、角色联动、3 星满级面板 |
| [maps.md](maps.md) | 地图：地块效果、合作挑战各地图的模式与首领、标准对战地图和限时玩法；单张地图见 `maps/` 下的文件 |
| [maps/](maps/) | 地块总表和每张合作挑战地图的详细资料 |
| [cards.md](cards.md) | 与卡牌相关的角色技能和筹码速查 |
| [monsters.md](monsters.md) | 怪物：数值、行为、技能和打法 |
| [co-op.md](co-op.md) | 合作挑战：胜负条件、轮次流程、升星、难度、商店、畸变组件 |
| [combat.md](combat.md) | 战斗结算：最终值、伤害、防御与闪避、反击和击倒 |

这些文档只在开发目录里，不随发行的程序发布。

内容改编自 Astral Party Wiki（<https://astralparty.miraheze.org/>），按知识共享 署名-相同方式共享 4.0 国际（CC BY-SA 4.0）授权，与 `data/` 相同。数值、中文名称和效果原文照游戏内和吉星派对 WIKI（<https://wiki.biligame.com/starengine/>）核对；对吉星派对 WIKI 只核对事实，没有转载它的文章。
