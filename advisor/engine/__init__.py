"""推荐引擎：给三个候选筹码打分，算前瞻和刷新的期望，输出百分比和理由。

总分 = 游戏内置推荐分 × official.scale + 适配分（玩家思路或自动层、组合、队友、难度、收入折算）+ 推荐流派分 + 前瞻分。
推荐流派分：属于推荐流派的筹码加分；角色自动层写了 keep_schools（推荐流派都要尽量保住，或只保其中几个）时，选择后会让
要保的流派本局再也进不全的其他流派筹码扣分（parts._keep_schools_part；只保几个时没列出的推荐流派也算其他流派，扣分可按
角色写 keep_schools_penalty），已进入其他流派、要保的流派已经进不全时，已进入的那个流派按推荐流派加分
（parts._adopted_schools）。只用于默认推荐。
地图待识别时，前瞻、刷新期望和剩余三选一次数按候选地图（State.map_candidates，没有就用全部现役地图）平均。
普通难度（State.difficulty 为 NORMAL）时，前瞻、刷新期望、循环往复的稀有度概率用普通难度的概率表，
进度上限取普通难度一列；困难及以上按困难及以上的概率表计算，进度上限在知道具体难度（State.difficulty_name：画面上读到的，
或按进度上限推算出唯一一个的）时取那一难度的一列，否则取疯狂难度一列；难度名是极限时一局的筹码任务数也取极限那一列
（data.MapInfo.cap_for、tasks_for）。
具体难度（State.difficulty_level）另影响「难度」分项：噩梦及以上时摩托头盔-高级按 rules.toml [survival] nightmare_* 加分，
困难、分不出困难还是噩梦、难度读不到时维持困难及以上的算法；难度名是极限时另有 rules.toml [extreme] 的几个筹码加分。
剩余三选一次数（context.PicksLeft）：剩余升星 + 任务和筹码商店（按 rules.toml [picks] end_round 收窄；3 星以后、轮次已知时
不低于 late_rate 那个下限）+ 循环往复给的额外那次（已拿循环往复、那一次还没给时，State.endless_pending）。额外那次不占任务和
筹码商店的份额：给了以后（这一次就是它、或它选到的筹码在已有筹码里，State.endless_given）不算进已经有过的次数。
之后的三选一（前瞻、刷新、开套预判、美工刀预判、治愈来源折算）按职业分值累加选最左格的职业筹码池（pool.slot_dists 的 class_owned）。
前瞻、美工刀预判、治愈来源折算和开套预判看的之后几次三选一按次数分开算（context.split_picks）：剩下的升星一定会来，先算满，
按下一星的升星；循环往复那一次还没给时接着算，按任务来源（context.endless_sources）；其余是任务和筹码商店，按两者在一局里的
占比混合（context.mixed_sources）。3 星以后不再升星。
同系列筹码（data/series.toml）同时出现时：被严格上位替代的低一级不参与比较（推荐度为 0），同系列的候选之间写对比行；
只在这一次三选一里调整，各候选的分值、前瞻和刷新的期望照常计算（advice.mark_series）。不算上位替代的两张总分接近时
（rules.toml [advice] series_near）稀有度高的一张排在前面，只对调两张的推荐度和先后（advice.series_near）。
「刷新」：刷新次数读到了时，剩余刷新比之后的三选一（次数向上取整，每次留 1 次，再多留 1 次以防估少）多出来的部分现在
用掉不算代价，可以连续使用：有多出来的时按最多连续刷新这么多次、刷到满意为止递推估值，代价为 0；没有多出来的时只按刷新
一次估值、扣刷新代价（reroll.reroll，RerollEval.chain、kept；留法见 context.reroll_keep）。本次之后预计没有筹码三选一是
全部多出来的特例。刷新次数没读出来、按默认值补的（State.rerolls_known 为 False）时不按多出来的算。
按筹码方案计算（advise 的 scheme，分值在 rules.toml [scheme]）：方案标了档位的筹码按档位代替玩家思路和角色特性；没标的筹码
不用默认推荐的任何部分（四档、带条件的规则、推荐流派、必选补分），只按角色特性、组合、队友、难度、收入折算、前瞻算
（自己的方案和默认推荐互斥：作者没标的筹码，默认推荐不替作者推出去）；目标流派里没标的筹码另加目标流派倾向，会让目标流派本局不再出现的
没标的筹码扣锁定保护，被锁成不选的流派里没标的筹码按不选计分，这几项只影响分值和未标记之间的先后。卡片上按档位排：
必选 > 推荐 > 可选 > 未标记 > 不选 > 被锁的其他流派，必选照顺位，推荐、可选按档位前瞻（lookahead.tier_lookahead，差不多、
或和卡片上写的数——下一次三选一里至少出现一张方案里的必选、推荐的机会——方向相反时照顺位）；只按剩下几次兑现算、收入扣掉
一半以上的推荐、可选按低一档排，选了会让方案里更要紧的必选、推荐本局拿不到的排到保得住的候选后面。
「刷新」按档位算平均或按刷出排在前面的概率（未标记时还要扣掉代价也划算）决定排在哪里，另算每张候选「选了之后拿不到……」
的提醒和推荐区的附注（scheme_rank.advise_scheme，结果在 Advice.ranking）。前瞻、刷新的期望和配合队友都用同一套分值，
自动跟着方案变。scheme 为 None（默认推荐）时不走这些分支。

各模块（只往下导入，不互相循环导入；对外的名字都从本包导入，不依赖模块的划分）：
- scorer：Scorer（一次推荐的上下文加对外的方法）和 advise（给出一次推荐）；
- context：一次推荐的上下文（已有筹码、地图、进度上限、剩余三选一次数、各项缓存）、引擎用的筹码方案 Plan、角色的职业；
- parts：打分的各分项和总分；chances：之后选到某筹码的概率（美工刀、治愈来源、配套筹码）；
- lookahead：前瞻；reroll：刷新的估值（默认推荐按分值，自己的方案按档位）；
- advice：一次推荐的组织（同系列、必选补分、队友刚需、推荐度、附注、默认推荐的先后）；scheme_rank：按自己的方案排先后；
- advice_text：界面、命令行、诊断包共用的写法和理由标签的短句。
"""
# 以下都是对外的名字（各模块里定义，这里统一导出）
from ..pool import tie_key  # 比较总分、百分比时用的键（定义在 pool，刷新的概率也用它；其他模块从 engine 取）
from .advice import (CORE_LEAD, LOW_NOTE, NEED_LEAD, NEED_ORIGIN, NORMAL_NOTE, SERIES_NEAR_TIP, STARLIGHT_NO_USE,
                     STARLIGHT_USES, Advice, ChipEval, map_note, mutator_notes)
from .advice_text import (BOT_MARK, CHAIN_NOTE, CHANCE_OVER, CHANCE_UNDER, DESC_LIMIT, PERCENT_OVER, PERCENT_UNDER,
                          PLACE_LOCKED, PLACE_LOCKED_TEXT, PLACE_MARKED, PLACE_STRAYED, PLACE_TIER, PLACE_UNMARKED,
                          PLACE_UNMARKED_TEXT, PLAYER_HEAD, REROLL, REROLL_P, REROLL_P_TOP, REROLL_SLOT, SCHEME_TIER_WORD,
                          SHORT_LIMIT, SPARE_NOTE, SUPERSEDED, UNREAD_NOTE, chance_text, compare_text, percent_text,
                          place_text, reroll_cost_note, reroll_counts, rival_word, shown_int, shown_percent_texts,
                          shown_percents)
from .chances import HEAL_DISCOUNT_ORIGIN, HEAL_DISCOUNT_REASON, HEAL_DISCOUNT_SHORT
from .context import (REROLL_MARGIN, Cache, PicksLeft, Plan, hero_classes, hero_tendency, plan_of, pool_and_dists,
                      reward_rounds_left)
from .lookahead import (CLASS_SHIFT_REASON, CLASS_SHIFT_SHOP_REASON, CLASS_SHIFT_SHOP_SHORT, CLASS_SHIFT_SHORT,
                        TIER_AHEAD_REASON, TIER_AHEAD_SHORT, TierAhead)
from .parts import (ADOPTED_GONE_REASON, ADOPTED_LEFT_REASON, ADOPTED_SHORT, DEFAULT_SCHEME, EXEMPT_OWNED_NOTE,
                    EXTREME_SHORT, KEEP_SCHOOLS_COUNT, KEEP_SCHOOLS_GONE_REASON, KEEP_SCHOOLS_GONE_SHORT,
                    KEEP_SCHOOLS_LEFT_REASON, KEEP_SCHOOLS_LEFT_SHORT, LATE_INCOME_NOTE, NIGHTMARE_REASON,
                    NIGHTMARE_SHORT, OFFICIAL_LABELS, SCHEME_CORE_ORIGIN, SCHEME_LABEL, SCHEME_LOCK_ORIGIN,
                    SCHEME_LOCKED_ORIGIN, SCHEME_ORIGIN, SCHEME_TARGET_ORIGIN, SCHEME_TARGET_REASON, SCHEME_TARGET_SHORT,
                    SCHEME_TIER_KEY, SCHEME_TIER_ORIGINS, SCHEME_TIER_REASON, SCHEME_TIER_SHORT,
                    UNKNOWN_DIFFICULTY_LEAD, UNKNOWN_DIFFICULTY_SUBJECT, Part, endless_wait)
from .reroll import (BASIS_TIER, BASIS_UNMARKED, LEVEL_KEYS, REROLL_LEVEL_EVEN, VERDICT_BETTER, VERDICT_EVEN,
                     VERDICT_EVEN_BETTER, VERDICT_EVEN_WORSE, VERDICT_WORSE, RerollEval, RerollView, default_rerolls)
from .scheme_rank import (LOSS_NOW, LOSS_ONE_LEFT, LOSS_TIP, LOST_CORE, LOST_FIRST, MOVE_AHEAD, MOVE_BEHIND, MOVE_LAST,
                          MOVE_LEAD, MOVE_MIXED, MOVE_NEAR, NOTE_LOST, NOTE_STRAYED, SCHEME_LOST_MAX, SCHEME_LOST_MORE,
                          SCHEME_LOST_NOTE, SCHEME_STRAYED_NOTE, SERIES_NEAR_TIP_SCHEME, TIER_LEVEL, UNMARKED_LEVEL,
                          Demotion, Loss, Move, Place, Ranking, SchemeNote)
from .scorer import EMPTY_OFFER, Scorer, advise
