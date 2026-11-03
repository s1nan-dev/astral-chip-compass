# 测试指南 · 推荐和打分

这是[《测试指南》](../测试指南.md)第八节的一章：每个测试文件一节，每节的结构见那里第八节的开头。


这一章管推荐引擎：打分、理由、刷新、方案计算，以及命令行参数到对局状态的换算。改了 advisor/engine/ 或 advisor/pool.py 先看这里。

## test_engine.py：用真实资料库检查推荐引擎的整套行为——同一次三选一该给什么结果、理由怎么写、各分项怎么算

- **测试观点**（按测试类分组）：
  - 游戏内置推荐分照不照游戏公式算（`OfficialScoreTest`）：基础分、适配角色分（适配和普通适配两张名单都在时两项相加）、成长分看轮次、标签分要职业分值过门槛（`hero_classes`）、关键词分只算治愈、星光、标记三个流派（充能不算）、内置分计入总分时整体乘 0.5；推出来的角标和实机样本标注（`samples/labels.json`）对得上。
  - 筹码池和「最左格」的概率对不对（`PoolTest`）：已持有和地图发放的筹码不进池、地图专属筹码只在本图出现、两个流派会锁掉其余流派、第一次刷出的最左格只从职业筹码池（池表）出、刷新后的最左格另有一套开关（现在按职业权重选池、池按筹码的职业标签建，池表外带职业标签的磨刀石、电磁炮也能出；池表里的筹码职业标签和池表一致）、同流派加权。
  - 一次推荐整体对不对（`AdviceTest`、`ShownPercentTest`、`PicksLeftTest`、`LateFloorTest`、`EndlessPendingPicksTest`、`DifficultyCapTest`、`MapAverageTest`、`StateTest`）：各选项推荐度合计 100%、取整时凑满 100%（最大余数法）、剩余三选一次数的推算、地图待识别时按候选地图平均。剩余三选一次数里：3 星以后、轮次已知时任务和筹码商店不低于 `[picks] late_rate` ×（存活条件下预计还剩的轮数 + 0.5）（读到 / 没读到当前格、正好在进度上限前最后一轮、到了进度上限、轮次读不出、0～2 星不加、只往上取）；已拿循环往复、额外那次还没给（`State.endless_pending`）时另加 1 次、封顶也加 1，按次数分开算时排在剩下的升星后面、按任务来源，3 星以后下一次三选一的主体就是它，前瞻、三个概率、刷新留几次都跟着；被剩余轮次封顶时封顶多加的那 1 次留给它、升星只占剩余的轮数，没有那一次时照旧先算升星（`context.levelups_ahead`，`test_capped_keeps_endless_pick`）；之后只剩那一次时前瞻说明不写职业分值那一句（那一次三格全随机、不看职业，`test_only_endless_left_no_class_shift`）；到期轮次（`State.endless_due`）不早于进度上限时不加，和给循环往复估值时同一口径，到期轮次不知道、进度上限只是估计时照原来加（`test_due_after_cap`）；0～2 星时那一次的等级按到期时预计的星数（`context.task_tiers`，`test_endless_tier_at_due`）；额外那次给了以后（`State.endless_given`：这一次就是它，或它选到的筹码在已有筹码里）不算进已经有过的任务和商店三选一（`test_given_is_not_task_or_shop`、`test_given_round_known`）；知道具体难度（`State.difficulty_name`）时进度上限取那一难度的一列，读到极限时一局的筹码任务数取极限的，其余难度和不知道难度时照旧。
  - 前瞻、刷新、收入、循环往复的估值（`AdviceTest`、`LookaheadTopStarTest`、`LookaheadSplitTest`、`EndlessCycleTest`、`IncomeTest`、`EdgeWordingTest`）：前瞻把之后出现率的变化算进来、拿走后不再出现不算损失、影响的几次按次数分开算（剩下的升星先算满、按下一星的升星，循环往复那一次还没给时接着按任务来源，其余按任务和筹码商店的占比混合：0～1 星全按升星，2 星多出的半次按混合（循环往复那一次还没给时按它），3 星以后全按混合，说明里不再写升星；任务和筹码商店的占比都为 0 时按混合的次数为 0，3 星以后也不加下限）、选择后锁掉的流派里写了本局不再出现的筹码不再另写「→ 0%」、刷新代价随剩余刷新次数和剩余选择次数变、收入按剩余兑现次数折算。
  - 各分项的写法（`ReasonWordingTest`、`TendencyTest`、`RefreshWordingTest`、`MutatorTest`、`SynergyTableTest`、`EdgeWordingTest`）：理由不出现特性名、用书面语写触发条件和数值，界面一律叫「刷新」，附注里不写分值。
  - 资料库未收录时能降级运行（`DegradedTest`、`PlayerSourceTest`）：未收录的筹码按平均水平算、未收录或适配表里缺的角色只按内置推荐和通用规则算；名称匹配（`match_chip`）和玩家思路来源合并。
  - 适配表的内容和引擎机制（`ComboTest`、`ComboContentTest`、`TeammateTest`、`TeammateContentTest`、`TeammateNeedTest`、`SurvivalDifficultyTest`、`ChargeBuildTest`、`HealKnifeTest`、`AnticipationPicksTest`、`SupportFlyingStarTest`、`KeepSchoolsTest`、`SourceRuleTest`）：组合、队友调分、难度加分、开套预判、美工刀预判（美工刀、治愈来源、配套筹码三个概率按次数分开算：剩下的升星按下一星的升星，其余按任务和筹码商店的占比混合；剩余次数被剩余轮次封顶、少于剩余升星次数时全按升星；系数 0～3 星同一个）、辅助角色的飞星、推荐流派保住；体系来源（`rules.toml [sources]`，规则的 `needs_source`、`without_source`）：充能那两张筹码没有来源时不照加分、标记那三张不管有没有来源都照原来的分加，自己或队友是 `[sources]` 里 `heroes` 列的角色算有来源，推算「拿了以后」时假设拿到的筹码也算来源，默认推荐列了这几张的角色不受影响。
  - 锁定的基准和实机复盘（`BaselineScenarioTest`、`PlayerCoreTest`、`CoreVsRefreshTest`、`AlLiveReviewTest`、`FennyLiveReviewTest`、`NancyLiveReviewTest`、`DeterminismTest`）：几组固定局面的第一名不能变、真人对局复盘后定的首推、结果不随哈希种子变。
- **检证对象**：
  - `advisor/engine/scorer.py`（`Scorer`、`advise`）、`advisor/engine/parts.py`（各分项与总分）、`advisor/engine/advice.py`（同系列比较、必选补分、队友刚需、附注、推荐度）、`advisor/engine/advice_text.py`（`shown_percents`、`percent_text`、短句）、`advisor/engine/lookahead.py`、`advisor/engine/reroll.py`、`advisor/engine/chances.py`、`advisor/engine/context.py`
  - `advisor/pool.py`（`State`、`player_pool`、`slot_dists`、`owned_schools`、`expected_best`、`expected_best_mix`、`appear_prob_mix`、`rarity_row`、`difficulty_of`、`prob_above`）
  - `advisor/data.py`（`load_db`、`match_chip`、玩家思路的读取与校验）
  - 资料库：`data/chips.json`、`data/heroes.json`、`data/maps.json`、`data/rules.toml`、`data/synergy.toml`、`data/series.toml`
  - 展示与周边：`advisor/ui/text.py`、`advisor/cli.py`、`advisor/diagnostics.py`；`advisor/vision/chip_select.py` 的 `_unrecorded_slots`；`tools/make_review.py` 的 `combo_section`
- **什么时候要跑**：改 `advisor/engine/` 里任何一个文件；改 `advisor/pool.py` 的筹码池或概率；改 `advisor/data.py` 的载入、名称匹配或玩家思路处理；改 `data/` 下的资料——筹码、角色、地图、`rules.toml`、`synergy.toml`、`series.toml`（改了分值和文字以后这个文件是最先该跑的）；改 `advisor/ui/text.py`、`advisor/cli.py`、`advisor/diagnostics.py` 的输出；换了实机样本标注（`samples/labels.json`）以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_engine`（上次约 78 秒）
- **需要什么**：仓库里的资料库和 `samples/labels.json`（游戏推荐角标的样本标注）。不需要截图、显卡或真实窗口。
- **输出怎么看**：同总则，另有：没有跳过用例，也不写临时文件；`OfficialScoreTest.test_badges_on_real_samples` 按游戏的判法推角标（这一组有内置推荐分 ≥ 100 的候选时这些都有「强烈推荐」，没有时 ≥ 50 里最高的有「推荐」），把「核对的筹码数、跳过的筹码数」写进断言（现在是 (132, 0)：标注里原因待查的两个按现在的判法都对得上，照样核对；还对不上的才跳过），改样本或改角标算法时这两个数要一起改；`DeterminismTest` 会另起 Python 进程（换哈希种子重算同一批局面），失败时断言里带子进程的报错原文；`ShownPercentTest.test_real_advice` 会跑很多角色，失败时子测试会报出是哪个角色、哪个星级。
- **出错时一般是哪里不对**：
  - `OfficialScoreTest` 失败 → 内置分的公式或阈值改了（`advisor/engine/parts.py` 里内置分那几项、`data/rules.toml` 的 `[official]`）；如果只是有意改了角标判定，`samples/labels.json` 的 `badge` 标注和断言的 (132, 0) 要一起改。
  - `PoolTest` 失败 → 筹码池的构成、最左格的概率或开关改了（`advisor/pool.py` 的 `player_pool`、`slot_dists`、`class_tags`、`reroll_leftmost_mode`，`data/rules.toml` 的 `[pool]`）。
  - `AdviceTest` / `ShownPercentTest` 失败 → 推荐度、百分比取整或推荐理由的组织改了（`advisor/engine/advice.py`、`advisor/engine/advice_text.py`）。
  - `LookaheadTopStarTest` 失败 → 前瞻看的下一次三选一改了：来源和占比（`advisor/engine/context.py` 的 `next_sources`、`mixed_sources`，`data/rules.toml` 的 `[picks]`）、两种来源的分布和加权（`future_setup`、`future`，`advisor/pool.py` 的 `expected_best_mix`、`appear_prob_mix`），或 3 星以后职业分值那一句的写法（`CLASS_SHIFT_SHOP_REASON`、`CLASS_SHIFT_SHOP_SHORT`）。
  - `AnticipationPicksTest` / `LookaheadSplitTest` 失败 → 之后几次三选一的分法改了：升星、循环往复那一次和任务、筹码商店各算几次（`advisor/engine/context.py` 的 `split_picks`）、几类的来源和占比（`levelup_sources`、`endless_sources`、`mixed_sources`），三个预判按来源分别算再加权（`advisor/engine/chances.py` 的 `take_chance`），前瞻几类的期望（`advisor/engine/lookahead.py` 的 `lookahead_on`、`_split_on`，`future` 的 `mixed`、`endless`），或预判分的系数（`data/rules.toml` 的 `[heal_knife] anticipation_factor`、`[charge_build] factor`）。`HealKnifeTest.test_anticipation_factor_basis` 失败 → 定系数时用的实机局面算出的概率变了，系数要照那里写的办法重新定。
  - `LateFloorTest` / `EndlessPendingPicksTest` / `DifficultyCapTest` 失败 → 剩余三选一次数的推算改了：3 星以后的下限（`advisor/engine/context.py` 的 `_late_floor`、`_late_rounds`，`data/rules.toml` 的 `[picks] late_rate`、`late_from`）、循环往复还没给的那 1 次（`_picks_left`、`PicksLeft.endless`、`split_picks`、`next_sources`、`endless_sources`），或按具体难度取的进度上限和极限的任务数（`advisor/data.py` 的 `MapInfo.cap_for`、`tasks_for`，`context.chip_tasks_on`，`data/maps.json` 的 `progress_caps`、`extreme`）。
  - `BaselineScenarioTest` / `CoreVsRefreshTest` / `PlayerCoreTest` 失败 → 这次改动动了打分，锁定的第一名变了。先确认改分是不是有意的，不是改预期就完事；确属有意（按攻略修订）时，把断言里的期望一起改。
  - `SynergyTableTest` / `ComboContentTest` / `TeammateContentTest` / `SurvivalDifficultyTest` / `SupportFlyingStarTest` / `KeepSchoolsTest` / `SourceRuleTest` 失败 → `data/synergy.toml` 的内容或 `data/rules.toml` 对应段改了；先分清是资料写错还是引擎读错。`SourceRuleTest` 另看来源判断（`advisor/engine/context.py` 的 `held_sources`、`advisor/data.py` 的 `Rule.applies`）。
  - `ReasonWordingTest` / `RefreshWordingTest` / `EdgeWordingTest` 失败 → 理由措辞改了（`advisor/engine/advice_text.py` 里的常量、`data/synergy.toml` 的 `reason`）；有意改文字时把断言的预期一起改。
  - `DegradedTest` / `PlayerSourceTest` 失败 → 名称匹配（`advisor/data.py` 的 `match_chip`）或玩家思路的载入、合并改了。
  - `PicksLeftTest` / `MapAverageTest` / `IncomeTest` / `EndlessCycleTest` 失败 → `data/rules.toml` 的 `[picks]`、`[income]`、`[endless_cycle]` 或地图资料改了；有意改数值时把断言一起改。
  - `DeterminismTest` 失败 → 引擎里又出现了依赖集合遍历顺序或哈希种子的代码，或平局比较的精度变了（`advisor/pool.py` 的 `tie_key`）。
  - `AlLiveReviewTest` / `FennyLiveReviewTest` / `NancyLiveReviewTest` 失败 → 复盘时锁定的那些规则又变了；这几处断言的是具体首推，改了就更新期望。

## test_engine_sanity.py：推荐引擎在大量随机局面和几组固定局面下的健全性

- **测试观点**：按测试类分。`FuzzTest`（35 个角色 × 10 个固定种子随机局面）：不出异常；三个候选同序返回、`best` 是候选之一或「刷新」；数字有限、推荐度合计 100%；首推是推荐度最高且总分不低于其余参与比较的候选；刷新 0 次时不出「刷新」选项；刷新的连续刷新估值随剩余三选一变化；已有 10 个筹码时照常；分项之和 = 不计前瞻分值、总分 = 不计前瞻分值 + 前瞻分、分项名在允许表内；同输入两次输出完全相同；附注随局面出现；悬浮窗文字里不出现分值说法；候选地图随地图专属筹码缩小；前瞻理由里提到的筹码都在本局筹码池里。另有 `CrossLaunchTest`（两个哈希种子各起一个进程，结果相同）、`InconsistentInputTest`（不合游戏规则的输入照常给推荐）、`NightmareHelmetTest`（噩梦及以上只提高摩托头盔-高级的「难度」分项）、`SeriesSanityTest`（同系列严格上位替代）、`RerollChainSanityTest`（连续刷新估值不降低）、`TeammateNeedSanityTest`（队友刚需只提到第二位）、`UnrecordedTest`（未收录筹码按同稀有度平均分、不计前瞻）、`CoreFirstTest`（必拿筹码优先于未列入的筹码）、`SupportFlyingStarTest`（辅助角色金色飞星优于星币锤）、`PoolTest`（筹码池排除已持有、别地图专属、被锁流派的筹码）、`CommonSenseTest`（每角色适配表里加分最高的筹码首推）。
- **检证对象**：整个推荐引擎（`advisor/engine/`，主要是 `scorer.py` 的 `advise`/`Scorer`）、`advisor/pool.py`（`player_pool`、`owned_schools`、`State`/`Teammate`）、`advisor/data.py`（`chips`、`heroes`、`rules`、`series`、`needs`、`mutators`、`map_info`）。
- **什么时候要跑**：改了推荐引擎的任何一部分，或改了 `advisor/data.py` 的资料（筹码、角色、规则、同系列表、队友刚需表、地图资料）以后。这是引擎的兜底测试，改动面越大越该跑。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_engine_sanity`（上次约 125 秒）。
- **需要什么**：没有识别样本/显卡/真实窗口；`CrossLaunchTest` 会另起 Python 子进程（用当前解释器）。
- **输出怎么看**：失败信息里给可直接运行复现代码（形如 `advise(load_db(), State(...), [...])`），`FuzzTest` 的异常会集中列在 `test_no_exception` 的说明里。
- **出错时一般是哪里不对**：
  - `FuzzTest.test_no_exception` 失败 → 引擎在某个局面抛异常；报错里有复现代码。
  - `test_shape`/`test_numbers_finite`/`test_percent_sum`/`test_best_has_top_percent`/`test_parts_add_up`/`test_notes_follow_state`/`test_map_candidates_follow_map_chips`/`test_lookahead_reason_names_pool_chips` 失败 → 对应的基本约束被改坏（`advise`/`Scorer` 里候选取值、百分比合计、分项求和、附注、前瞻理由）。
  - `test_zero_rerolls_never_refresh`/`test_reroll_chain_follows_picks` 失败 → 刷新的生成条件或连续刷新估值（`advisor/engine/reroll.py`）。
  - `test_no_score_wording_in_overlay_text` 失败 → 引擎整句/短句里出现了分值说法（悬浮窗不写分数）。
  - `test_corpus_covers_dimensions` 失败 → `corpus` 的生成方法或种子改了，覆盖面缩水。
  - `CrossLaunchTest.test_same_result_across_hash_seeds` 失败 → 求和顺序随哈希种子变化（遍历集合累加的地方）。
  - `InconsistentInputTest.test_still_gives_valid_advice` 失败 → 不合规则的输入（重复候选、三四个流派、别地图专属筹码）下抛异常或约束不成立。
  - `NightmareHelmetTest.test_gold_helmet_not_lower` 失败 → 噩梦及以上摩托头盔-高级的「难度」分项（`advisor/engine/parts.py` 的 `_survival_parts`、`data/rules.toml` 的 `[survival]`）。
  - `SeriesSanityTest.test_low_never_first` 失败 → 同系列严格上位替代的处理（`data/series.toml`、`advisor/engine/advice.py`）。`RerollChainSanityTest` 失败 → 连续刷新估值。`TeammateNeedSanityTest` 失败 → 队友刚需排第二（`advisor/engine/advice.py` 的 `_need_second`、`db.needs`）。
  - `UnrecordedTest` 失败 → 未收录筹码的中性分（`Scorer.neutral_score`；它照使用说明「推荐的写法和规则」里的「按同稀有度筹码的平均水平计算，不计前瞻」写）。`CoreFirstTest` 失败 → 必拿优先（`advisor/engine/advice.py` 的 `_core_first`、`rules.toml [synergy]` 的 core/core_lead）。
  - `SupportFlyingStarTest` 失败 → 金色飞星与星币锤的对照（辅助角色的资料或分值变了）。`PoolTest` 失败 → `player_pool` 的排除规则。`CommonSenseTest` 失败 → 适配表里加分最高的筹码没被首推（资料或引擎改动）。
  - 这条测试里硬编码的数字（角色数 35、`checked` 门槛等）在资料增删后要一起改。

## test_engine_cache_order.py：打分缓存按不同顺序填写时结果必须逐位相同

- **测试观点**：`CacheOrderTest` 的 3 个用例。① 抽到的局面确实覆盖了地图（已知/待识别）和队友（有/无），并且有几个「多张候选地图 + 角色带美工刀设置」的局面；② 每种查询顺序（正序、倒序、先查刷新、旋转、每项重复两次）各建一个 `Scorer` 从头算，结果和基准逐位相同；同一个 `Scorer` 上按各顺序重查（读缓存）也相同；③ 按筹码方案计算时同样顺序无关，另查档位前瞻（`tier_lookahead`、`tier_ahead`、`tier_future` 的拿 / 不拿两边）和前瞻混在一起按各种顺序查时不串。覆盖 `Memo` 的十三项缓存（`score`、`parts`、`setup`、`values`、`future`、`endless`、`endless_chain`、`knife`、`heal`、`partner`、`reroll`、`lookahead_keys`、`survival`），按筹码方案计算时另有档位前瞻的两项（`tier_future`、`tier_values`，测试里的 `SCHEME_CACHES`）。
- **检证对象**：`advisor/engine/context.py` 的 `Memo`（各项缓存的字典）和 `Scorer`（`advisor/engine/scorer.py`）；`advisor/engine/chances.py`（`heal_knife`/`heal_chance`/`knife_chance`/`partner_chance`）；`advisor/engine/lookahead.py`（`future_setup`/`lookahead_on`）；`advisor/engine/parts.py`（`survival_values`）；`advisor/engine/reroll.py`（`reroll_dists`）；`advisor/pool.py`。局面来自 `tests/test_engine_sanity.py` 的 `corpus`。
- **什么时候要跑**：改了任何缓存的键或组织方式（`advisor/engine/context.py` 的 `Memo`、`pool_and_dists`），或 `advisor/engine/scorer.py`、`parts.py`、`chances.py`、`lookahead.py`、`reroll.py` 里读写这些缓存的地方以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_engine_cache_order`（上次约 15 秒）。
- **需要什么**：没有特别要求。
- **输出怎么看**：失败信息里会写明是哪一种顺序（如「新建的 Scorer 按 reversed 顺序查，结果不同」）。这条测试只能查出「换个顺序结果就变」的错，查不出「每种顺序都错得一样」，那种要靠按数值核对的测试（如 `test_engine`）。
- **出错时一般是哪里不对**：
  - `test_order_independent` 失败（「新建的 Scorer 按 X 顺序查，结果不同」）→ 某处缓存写回的值依赖调用顺序，或某个缓存的键漏了东西。先看不同的是哪个 probe 键，前缀 `score`/`parts`/`future`/`lookahead`/`knife`/`heal`/`partner` 分别指向 `context.py`/`parts.py`/`chances.py`/`lookahead.py` 里的缓存函数。
  - 报「这几个缓存一个局面都没写到」或「这几个概率缓存在每个局面里都只存了同一个值」→ 缓存改名/改组后测试里的 `CACHES`、`CHANCE_CACHES` 没跟着改，或局面（`EXTRA`）不再覆盖多地图 + 美工刀；这时要改测试里的 `CACHES`/`EXTRA`（属「该跟着改」）。
  - `test_cases_cover_dimensions` 失败 → `corpus` 或 `STRIDE` 改了，覆盖面变化，按新的覆盖调整。
  - `test_order_independent_with_scheme` 失败 → 方案分支（`advisor/engine/scheme_rank.py`、`parts._scheme_school_parts`、`lookahead.py` 的 `tier_values`/`tier_future`）里填缓存的方式让结果依赖顺序；报「档位前瞻的缓存一个局面都没写到」→ 档位前瞻的缓存改了名字或不再经 `Memo`，跟着改测试里的 `SCHEME_CACHES`。

## test_engine_frame_cache.py：一帧共用分布缓存（`advise` 的 `cache` 参数）时结果与单算逐位相同

- **测试观点**：三类。① `FrameCacheTest`：同一帧的几次调用（星数拿不准时按原星数和高一星各算一次、按方案算时再按默认推荐算一次）共用一份缓存、各建一份、完全不给缓存，三组结果逐位相同；同样的输入命中缓存、新建的一帧缓存是空的；`pool_and_dists` 的键逐项核（改动角色、已有筹码、地图、星数、来源、tier、概率行、最左格稀有度、刷新标志、难度、排除的候选任何一项都要重算）。② `FrameCombosTest`：几类局面（地图待识别带/不带候选地图、普通难度、画面读到概率行、任务来源、候选有未收录、方案标了循环往复档位）下，一帧的各种调用组合正着、倒着各算一遍，结果与不给缓存相同，并确认缓存确实被命中。③ `FrameOrderTest`：`test_engine_cache_order` 的各种顺序各建一个 `Scorer`、共用一个 `Cache`，结果相同。
- **检证对象**：`advisor/engine/context.py` 的 `Cache` 和 `pool_and_dists`（分布缓存的键）；`advisor/engine/scorer.py` 的 `advise`。局面来自 `tests/test_engine_sanity.py` 的 `corpus`，顺序工具来自 `tests/test_engine_cache_order.py`。
- **什么时候要跑**：改了 `advisor/engine/context.py` 的 `Cache`、`pool_and_dists`，或 `advise` 使用缓存的方式以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_engine_frame_cache`（上次约 35 秒）。
- **需要什么**：没有特别要求。
- **输出怎么看**：失败信息里会点名漏项的键（如「键漏了「地图」」）或「共用的缓存一次都没有命中」。
- **出错时一般是哪里不对**：
  - `FrameCacheTest.test_shared_cache_matches_single` 失败 → 共用缓存时算错（去看 `advisor/engine/context.py` 的 `pool_and_dists`）。
  - `FrameCacheTest.test_cache_reused_but_frames_independent` 失败 → 同样输入没命中缓存，或新建 `Cache` 不为空（`Cache.dists` 的初值/键）。
  - `FrameCacheTest.test_key_complete` 失败（「键漏了「X」」）→ `pool_and_dists` 的键少了那一项输入（去看 `advisor/engine/context.py` 的 `pool_and_dists`）。
  - `FrameCombosTest` 失败 → 一帧里几种调用组合结果不同，或报「共用的缓存一次都没有命中」（cache 没传进 `pool_and_dists`）。
  - `FrameOrderTest` 失败 → 共用 `Cache` 后按别的顺序读出错。

## test_engine_edge_cases.py：推荐引擎的三处边界

- **测试观点**：三类。① `EmptyOfferTest`：候选为空时明确拒绝并给出原因（`ValueError`，消息是 `EMPTY_OFFER`），按默认推荐和按方案都一样；两个候选、重复候选照常出结果。② `SeriesChainTest`：同系列三张（低→高）连成链时只对调相邻的一对，推荐度不轮换。③ `PicksLeftDedupTest`：已有筹码里同一个记了两次时只数一次。
- **检证对象**：`advisor/engine/scorer.py` 的 `advise`（空候选判断和 `EMPTY_OFFER`）；`advisor/engine/advice.py` 的 `series_near`（同系列接近时对调先后，与 `advisor/engine/scheme_rank.py` 的方案版一致）；`advisor/engine/context.py` 的 `Scorer.picks`（预计剩余三选一，`PicksLeft`）。
- **什么时候要跑**：改了 `advisor/engine/scorer.py`（空候选处理）、`advisor/engine/advice.py` 的 `series_near`、`advisor/engine/context.py` 的 `PicksLeft`/`Scorer.picks` 以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_engine_edge_cases`（上次约 0.1 秒）。
- **需要什么**：没有特别要求。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `EmptyOfferTest.test_rejected_with_reason` 失败 → 候选为空时没抛 `ValueError`，或消息不是 `EMPTY_OFFER`（去看 `advisor/engine/scorer.py` 的 `advise`）。
  - `SeriesChainTest.test_chain_swaps_once` 失败 → 三张连成链时重复对调（去看 `advisor/engine/advice.py` 的 `series_near`）。
  - `PicksLeftDedupTest.test_duplicate_owned_counted_once` 失败 → 数已有筹码时没去重（去看 `advisor/engine/context.py` 的 `PicksLeft`）。

## test_after_pick_classes.py：推算「拿了某个筹码以后」的推荐分时，游戏内置推荐里的标签分也按拿了以后的职业算

- **测试观点**：分两类（`AfterPickClassesTest` 的 2 个用例）。① 按「已有筹码 + 这一张」算职业时，多出来的职业确实来自这一张（`hero_classes`）；② 候选筹码的开套预判（判断「之后会选的配套筹码」时给之后的候选打分）跟着这个变化，总分和显示推荐度也对得上。
- **检证对象**：`advisor/engine/context.py` 的 `hero_classes`（按已有筹码算角色当前职业）；`advisor/engine/scorer.py` 的 `Scorer.official_parts`（游戏内置推荐分的分项，含「标签分」）和 `advise`；开套预判那一项在 `advisor/engine/parts.py`（`origin == "charge_build"`）。
- **什么时候要跑**：改了 `advisor/engine/context.py` 的 `hero_classes`、`advisor/engine/parts.py` 里「标签分」和开套预判（charge_build）的算法，或 `data/rules.toml` 的 `[official]`（tag、scale）分值以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_after_pick_classes`（上次约 0.3 秒）。
- **需要什么**：没有特别要求（纯资料和引擎计算，不用识别样本、显卡、真实窗口）。
- **输出怎么看**：同总则。`subTest` 会把失败定位到具体断言。
- **出错时一般是哪里不对**：
  - `AfterPickClassesTest.test_classes_follow_assumed_owned` 失败 → `hero_classes` 没把传入的「假定已有」用于职业判断，或「标签分」仍按现在的职业算（去看 `advisor/engine/context.py` 的 `hero_classes`、`advisor/engine/scorer.py` 的 `official_parts`）。
  - `AfterPickClassesTest.test_charge_anticipation_follows` 失败 → 开套预判没有跟着「拿了以后」重新打分，或引擎总分/推荐度算法变了（去看 `advisor/engine/parts.py` 里 `origin == "charge_build"` 的分项、`advisor/engine/advice.py` 的 `advise`）。
  - 断言里的数（配套概率 29%、总分 11.99、推荐度 94%/<1%/2%/4%）是照当时资料算的；有意改了资料或算法时，要把这些预期一起改。

## test_income_progress.py：进度领先时攒钱筹码的收入折算

- **测试观点**：`IncomeProgressTest` 的 4 个用例。① 当前进度格领先于轮次时，收入折算的扣分按「整局的进度上限」当分母算（剩下的兑现次数更少，扣得更多，且不少于不领先时）；② 没读到当前格、或当前格等于轮次时，分子分母都按进度上限算，两种情形结果相同；③ 整句里「还剩几次轮次奖励」按进度条算，写的进度上限是读到的数；④ 同一组候选里，扣分变化后总分和首推对得上。
- **检证对象**：`advisor/engine/parts.py` 的 `income_part`（收入折算那一项）；`advisor/engine/scorer.py` 的 `Scorer.parts` 和 `advise`；`advisor/data.py` 的 `map_info[...].progress_caps`；`advisor/engine/context.py` 的 `reward_rounds_left`。
- **什么时候要跑**：改了 `advisor/engine/parts.py` 的收入折算、`advisor/data.py` 的 `progress_caps`（地图进度上限资料），或 `data/rules.toml` 的 `[income]` 以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_income_progress`（上次约 0.1 秒）。
- **需要什么**：没有特别要求。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `test_leading_progress_deducts_more` 失败 → 进度领先时的分数没按整局进度上限算（去看 `advisor/engine/parts.py` 的 `income_part`、`advisor/engine/context.py` 的 `reward_rounds_left`）。
  - `test_not_leading_unchanged` 失败 → 没读到当前格或当前格等于轮次时的结果变了。
  - `test_reason_counts_by_progress` 失败 → 整句里的剩余次数或进度上限文字变了。
  - `test_advice_follows` 失败 → 总分或首推变了；断言里的数（−54/−24/−39、总分 20.93）是照当时资料算的，有意改资料或算法时要把预期一起改。

## test_need_useless_school.py：队友刚需排第二的前提「不会把自己锁进对自己没用的流派」

- **测试观点**：两类。`DefaultAdviceTest`：默认推荐里「有用的流派」是角色的推荐流派，只有拿了以后流派名额占满、推荐流派再也进不去时才不提。用例覆盖：没有推荐流派的角色（拿了以后还有空位）会提；最后一个名额留给了推荐流派时照样提；已进入方案外流派、推荐流派本局进不全时不提。`SchemeTest`：按方案计算时「有用的流派」是列进方案的流派，口径相同（还有空位就提，挤掉列进方案的流派就不提，方案一个流派都没列时哪个都不算没用）。
- **检证对象**：`advisor/engine/scorer.py` / `advisor/engine/parts.py` 的 `Scorer.locks_useless_school`；`advisor/engine/advice.py` 的队友刚需排第二（`NEED_ORIGIN`、`_need_second`）；`advisor/engine/scheme_rank.py`（方案模式的先后）；`data/rules.toml` 的 `[pool] max_schools`。
- **什么时候要跑**：改了 `locks_useless_school`、队友刚需排第二，或「流派名额/锁定」相关算法以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_need_useless_school`（上次约 0.3 秒）。
- **需要什么**：没有特别要求。
- **输出怎么看**：同总则；失败时对照断言里的先后（`order(adv)`）判断是放宽了还是收紧过度。
- **出错时一般是哪里不对**：
  - `DefaultAdviceTest`/`SchemeTest` 里断言某张不该被提的筹码却出现在 `lifted(adv)` 里（或反过来）→ `locks_useless_school` 的判定变了（去看 `advisor/engine/scorer.py` 的 `locks_useless_school`、`advisor/engine/advice.py` 的 `_need_second`）。
  - `order(adv)` 的顺序不符 → 刚需把筹码提到了第二位以外的位置，或方案模式的排序（`scheme_rank.py`）变了。
  - `SchemeTest.test_no_listed_school_never_blocks` 失败 → 方案一个流派都没列时错误地判成了「锁进没用的流派」。

## test_reroll_bonus.py：从画面上的刷新次数推断「每升一星刷新 +1」

- **测试观点**：`RerollBonusTest` 的 5 个用例。① 升星后第一次三选一读到比升星前多 1 次刷新 → 判定有这项加成；② 次数相同 → 判定没有；③ 差值不是 0 或 1（例如中间拿了循环往复 +3）→ 不下结论；④ 读不出刷新次数时，默认值按推断结果算（有加成按 2 + 星数，没有只算开局的 2 次），局面记下这是按默认值补的（`State.rerolls_known` 为 False），推荐里不按多出来的刷新算；读到了的为 True；⑤ 判定为新的一局时清空推断结果。
- **检证对象**：`advisor/session/core.py` 的 `Session._observe_rerolls`、`_default_rerolls` 和 `build_state` 里的 `rerolls_known`；`advisor/session/game_state.py` 的 `per_star_rerolls` 字段；`advisor/engine/reroll.py` 的 `default_rerolls`；`data/rules.toml` 的 `[reroll] per_star`。用 `tests/test_session.py` 的 `ready`/`frame`/`close` 造识别结果。
- **什么时候要跑**：改了 `advisor/session/core.py` 的刷新次数推断或默认值、`advisor/session/game_state.py` 的 `per_star_rerolls`、`advisor/engine/reroll.py` 的 `default_rerolls`，或 `[reroll]` 规则以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_reroll_bonus`（上次约 3.7 秒）。
- **需要什么**：没有识别样本/显卡/真实窗口（直接用假的识别结果建 `Session`，不依赖界面库和图像库）。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `test_one_more_after_level_up_means_bonus` / `test_same_count_means_no_bonus` 失败 → 升星前后差值判断反了或没判断（去看 `advisor/session/core.py` 的 `_observe_rerolls`）。
  - `test_other_differences_decide_nothing` 失败 → 差值不是 0/1 时错误地下结论。
  - `test_unreadable_count_uses_inferred_default` 失败 → 读不出刷新次数时的默认值没按推断结果算（去看 `core.py` 的 `_default_rerolls`、`advisor/engine/reroll.py` 的 `default_rerolls`）。
  - `test_reset_by_new_game` 失败 → 新的一局没清掉 `per_star_rerolls`。

## test_reroll_chain.py：刷新有多出来的（含最后一次三选一）时「刷新」按最多连续刷新几次估值的递推

- **测试观点**：`ExpectedMaxWithTest`：`expected_max_with`（带下限的最大值期望）和按定义直接求和一致、下限边界正确；`LastPickTest`：之后的候选三选一为 0 时（实机那一局最后一次三选一，进度读成走到头），刷新代价为 0、连着刷到满意为止，估值等于测试里独立递推的结果，且随剩余刷新次数单调不减、概率按「最多 k 次之内」递推，三个候选的分值不受影响；照实际读到的进度（3 星、还没到进度上限）时之后的三选一按下限约 0.5 次，留 2 次、多出 1 次（`test_real_reading_not_final`）；`OtherCasesTest`：剩余刷新没有比之后的三选一多出来时照旧只算刷新一次；没有刷新次数时不出「刷新」；地图待识别时每张候选地图分别递推再平均；`SpareRerollTest`：刷新次数读到了时，之后每次三选一留 1 次刷新（次数向上取整，浮点末位的误差不多留）、再多留 1 次以防估少，剩余刷新比这多出来时按多出来的次数连着刷、代价为 0（`test_split`，含之后的三选一不超过 1e-9 次按没有算）；凛 3 星、读到 4 次刷新，第 11～13 轮（之后约 0.67、0.4、0.3 次，3 星以后有下限）留 2 次、连刷 2 次，到了进度上限（第 16 轮）4 次都能用（`test_rin_example`）；没有多出来的照旧扣代价，留下的正好等于剩余次数时也一样（`test_no_spare_unchanged`）；自己的方案里刷新那一行的级数递推和代价同样按多出来的，没读出刷新次数时同默认推荐（`test_scheme_view`）；`EndlessCycleRerollsTest`：循环往复给的 3 次刷新加上以后按同一个留法分，留下的以内照原来的模型、多出来的只值下一次三选一多连刷一次的提升（和测试里独立递推的结果一致；之后不到 1 次三选一时按下一次三选一出现的机会打折），并且每一次不超过原来的模型给那一次的值（凛 3 星第 9 轮那样连刷的提升更高的，封顶后等于原来的模型；本来就更低的照旧，`test_capped_at_old_model`），没读出刷新次数、之后没有三选一时照旧；`UnreadRerollsTest`：刷新次数没读出来（`State.rerolls_known` 为 False）时不按多出来的算，之后还有三选一就只刷一次、按公式扣代价，之后没有三选一时照样连着刷，不写这一项时按读到了算；`CliTest`：命令行刷新那一行的文字（含「按最多连续刷新 N 次估计」、留了几次和多留的 1 次、刷新次数没读出来的注明）、刷新概率句、JSON 里的 `chain`、`kept`、`spare`、`need`、`known`，`--rerolls`、`--rerolls-used` 都不给时按没读出刷新次数算。
- **检证对象**：`advisor/engine/reroll.py`（`reroll`、`reroll_split`、`reroll_cost`、`reroll_chain`、`reroll_view`、`RerollEval.chain`/`kept`/`spare`/`need`/`known`）；`advisor/engine/context.py`（`keep_count`、`reroll_keep`、`later_picks`、`REROLL_MARGIN`）；`advisor/engine/parts.py`（`_endless_rerolls`、`_endless_chain_gain`、`_endless_chain`）；`advisor/pool.py`（`State.rerolls_known`、`expected_max_with`、`expected_best`、`slot_dists`）；`advisor/engine/lookahead.py`（`lookahead_on`）；`advisor/engine/scorer.py`（`Scorer.reroll`）；`advisor/cli.py`（`render`、`to_json`、`CHAIN_NOTE`、`reroll_p_text`、`UNREAD_REROLLS`）；`advisor/engine/advice_text.py`（`SPARE_NOTE`、`UNREAD_NOTE`、`reroll_cost_note`）。
- **什么时候要跑**：改了 `advisor/engine/reroll.py`、刷新留几次（`advisor/engine/context.py` 的 `keep_count`）、循环往复给的刷新的估值（`parts.py` 的 `_endless_rerolls`）、`advisor/pool.py` 的 `expected_max_with`/`expected_best`、`advise`/`Scorer.reroll`，或命令行的刷新文字以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_reroll_chain`（上次约 0.2 秒）。
- **需要什么**：没有特别要求。
- **输出怎么看**：`CliTest` 捕获命令行标准输出后逐字断言；其余同总则。
- **出错时一般是哪里不对**：
  - `ExpectedMaxWithTest` 失败 → `expected_max_with` 的递推或下限（floor）算错（去看 `advisor/pool.py`）。
  - `LastPickTest.test_chain_follows_rerolls` / `test_value_matches_recursion` / `test_monotonic_in_rerolls` 失败 → 连续刷新的估值没按最优停止递推（去看 `advisor/engine/reroll.py` 的 `reroll`/`reroll_chain`、`advisor/pool.py` 的 `expected_max_with`）。
  - `LastPickTest.test_reroll_first_ahead_of_actual_pick` 失败 → 刷新的先后或首推变了（这一局已持有友情徽章，刷新出的大碗炖肉另有组合加分，刷新排在实际选的缓冲盾牌前面）。
  - `test_candidates_unchanged` 失败 → 改刷新估值时误改了候选分值。
  - `OtherCasesTest.test_more_picks_keep_single_reroll` 失败 → 剩余刷新没有多出来时错误地按连续刷新估值；`test_unknown_map_averages_each_map` 失败 → 地图待识别时没按每张候选地图分别递推再平均。
  - `SpareRerollTest` / `UnreadRerollsTest` 失败 → 留给之后的次数、多出来的次数或多出来时的代价算错，或没读出刷新次数时没照不分多出来的算法（去看 `advisor/engine/context.py` 的 `keep_count`、`advisor/engine/reroll.py` 的 `reroll_split`、`reroll_cost`、`reroll_chain`，方案模式看 `reroll_view`）。
  - `EndlessCycleRerollsTest` 失败 → 循环往复给的刷新的估值变了（`advisor/engine/parts.py` 的 `_endless_rerolls`、`_endless_chain_gain`，多出来的那几次以原来的模型封顶；`data/rules.toml` 的 `[endless_cycle]` 注释写了算法）。
  - `CliTest` 失败 → 命令行刷新一行的措辞、`CHAIN_NOTE`、`SPARE_NOTE`、`UNREAD_NOTE` 或 JSON 字段变了（有意改措辞时把断言一起改）。

## test_clock.py：测「今天」这个日期从哪来，以及钉死它以后限时地图的上下线和复制默认推荐记的日期，另外查同分筹码的先后不随哈希种子变

- **测试观点**（按测试类）：
  - `ClockTest`：`today()` 认得合法的 `ACC_TODAY`（严格的 `YYYY-MM-DD`），忽略不合法的写法（日期不存在如 `2026-02-30`、别的格式如 `2026/10/14`、前后带空格、`20010203`、周日期写法等），没设时退回系统日期。
  - `PinDateTest`：钉死的日期真的生效——限时地图「异变图书馆」的结束日期是 2026-10-14，10-14 在役、10-15 不在役；取「今天」的三处（地图是否在役、已结束的地图集合、复制默认推荐记的日期）都按它走。
  - `HashSeedTest`：同分筹码按名字排先后；两个哈希种子各起一个进程跑同一批局面，三个候选的总分要逐字相同。
- **检证对象**：`advisor/clock.py` 的 `today()`（`ENV` = `ACC_TODAY`）；`advisor/data.py` 的 `load_db` 与 `MapInfo.active_maps`；`advisor/vision/hud.py` 的 `retired_maps`；`advisor/schemes.py` 的 `scheme_from_default`（复制默认推荐记的日期）；`advisor/pool.py` 的 `_cdf_steps`（同分筹码的排序）。局面经 `tests/test_engine_sanity.py` 的 `corpus` 生成。
- **什么时候要跑**：改 `advisor/clock.py`；改 `advisor/data.py` 里取日期的部分、`advisor/vision/hud.py` 的 `retired_maps`、`advisor/schemes.py` 的 `scheme_from_default`；改 `advisor/pool.py` 的 `_cdf_steps` 或任何影响同分筹码先后、累加顺序的地方；改 `data/maps.json` 里限时地图的起止日期（钉死的日期是照它写的）；改 `tests/test_engine_sanity.py` 的 `corpus`。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_clock`（上次约 5 秒）
- **需要什么**：没有特别要求；`HashSeedTest` 会另起两个 Python 进程（各限时 300 秒）跑同一批局面。
- **输出怎么看**：同总则，另有：没有跳过用例，也不写临时文件；`HashSeedTest` 的子进程失败时，断言里会带它最后 2000 字的报错原文；它还会核对输出行数不少于 40 行，是防止批次空跑（局面太少这条测试就失去意义）。
- **出错时一般是哪里不对**：
  - `ClockTest.test_accepts_valid_env` / `test_ignores_invalid_env` / `test_unset_falls_back_to_system_date` 失败 → `advisor/clock.py` 的 `today()` 改了接受或拒绝的格式（它只该认严格的 `YYYY-MM-DD`）。
  - `PinDateTest.test_map_active_follows_pinned_date` 失败 → 限时地图的上下线判定或 `data/maps.json` 里的日期改了；日期是有意改的，这里的 2026-10-14 / 2026-10-15 要一起改。
  - `PinDateTest.test_retired_maps_follow_pinned_date` 失败 → `advisor/vision/hud.py` 的 `retired_maps` 没跟着「现役地图」走。
  - `PinDateTest.test_copied_date_follows_pinned_date` 失败 → `advisor/schemes.py` 的 `scheme_from_default` 记的日期不是「今天」，或显式传入的日期没有优先。
  - `HashSeedTest.test_totals_identical_across_hash_seeds` 失败 → 引擎里又出现了依赖集合遍历顺序的地方（总分由累加得出），去看 `advisor/pool.py` 的 `_cdf_steps` 和打分里遍历集合的代码；两个种子的输出对不上时先看哪一行候选的总分不同。
  - `HashSeedTest` 报的是子进程退出码非 0（而不是总分对不上）→ 多半是 `tests/test_engine_sanity.py` 的 `corpus` 或它引用的资料坏了，不是排序问题。

## test_difficulty.py：本局难度（档位和难度名、按进度上限推算、噩梦及以上摩托头盔-高级的「难度」分项）

- **测试观点**：按测试类分。`LevelTest`：难度档位和难度名的常量表、`level_of_name`、`classify_difficulties`（一组难度名归到哪个档位）。`DifficultiesAtTest`：地图资料里各难度对应哪一格（`difficulties_at`），缺一列时推算不出；四个难度都对不上、只有极限对得上时是极限（御魂庆典 17 格），和疯狂相同时照旧只列疯狂（`test_extreme_column`）。`InferTest`：按地图和进度上限推算难度（资料完整时每张地图每个难度都不得与推算矛盾；几个已知地图的档位/难度名/说明句；推算不出时的说明；园林中庭停用推算）。`StateTest`：`State` 的难度字段含义。`NightmareTest`：噩梦及以上只提高摩托头盔-高级的「难度」分项，其他筹码不变；难度待识别时整句的写法；文字里不写特性名和分值；一局的推荐随之变化。`CliTest`：命令行 `--difficulty` 的显示和 JSON 分项。`RulesFileTest`：`data/rules.toml` 里阿尔被动「每 6 层星光」的写法。
- **检证对象**：`advisor/data.py`（`classify_difficulties`、`infer_difficulty`、`level_of_name`、`MapInfo.difficulties_at`/`progress_caps`、`DIFFICULTIES`/`DIFFICULTY_NAMES`/`LEVEL_*`）；`advisor/engine/parts.py`（`nightmare_plus`、`_survival_parts`）；`advisor/pool.py` 的 `State`；`advisor/cli.py`；`data/rules.toml`（`[difficulty]`、`[survival]`、`[extreme]`）、`data/` 的地图资料。用到 `tests/test_engine.py` 的 `BANNED_WORDING`。
- **什么时候要跑**：改了按进度上限推算难度、难度名/档位的映射、噩梦及以上摩托头盔-高级的分项、命令行的难度显示，或 `[difficulty]`/`[survival]`/`[extreme]` 规则以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_difficulty`（上次约 2.2 秒）。
- **需要什么**：没有特别要求。
- **输出怎么看**：`CliTest` 捕获命令行标准输出后断言；其余同总则。
- **出错时一般是哪里不对**：
  - `LevelTest` 失败 → 难度档位或难度名的表变了（看 `advisor/data.py` 的 `LEVEL_*`/`DIFFICULTY_NAMES`）。
  - `DifficultiesAtTest`/`InferTest` 失败 → 按进度上限推算和资料不符，或说明句文字变了（看 `advisor/data.py` 的 `difficulties_at`/`infer_difficulty`；有意改文字时改断言）。
  - `InferTest.test_cap_untrusted` 失败 → 园林中庭停用推算、`[difficulty] cap_untrusted` 的设置没生效。
  - `StateTest` 失败 → `State` 的难度字段（`advisor/pool.py`）。
  - `NightmareTest` 失败 → 噩梦及以上摩托头盔-高级的「难度」分项、难度待识别时的整句、或文字里混进了特性名/分值（看 `advisor/engine/parts.py` 的 `nightmare_plus`/`_survival_parts`）。
  - `CliTest` 失败 → 命令行 `--difficulty` 的显示或 JSON 分项变了。
  - `RulesFileTest.test_al_starlight_layers` 失败 → `data/rules.toml` 里阿尔被动的层数写法被改动（这属「该跟着改」的对照，改资料时同步改）。

## test_video_scoring.py：按攻略视频结论定的打分

- **测试观点**：按测试类分。`ClassPointsTest`：职业分值累加（升星和筹码商店最左格选池的权重 = 初始职业分值 + 已拿筹码的职业分值），攻略视频给的排序只有按累加才算得出。`MechanicsTest`：真梦梓的运动手环、赛克斯的减速和手持风扇、循环往复的等待回合与等级、大碗炖肉与半血打法的冲突。`HealDiscountTest`：物理输出给美工刀的加分按之后获得治愈来源的概率折算。`OpinionValuesTest`：按攻略观点给的分值（速度轮滑、转账、弱筹码、地图专属筹码、生存类特性、极限难度、水乡古镇摩托头盔、组合与队友、银行卡晚拿折算、流派资料）。`PlayerLayerTest`：按攻略视频整理的玩家思路（档位、条件规则、同系列一致、原文写法；资料里现在没有角色写这一层，用测试用的默认推荐档位 `tests/support/player_layer.toml`）。`SeriesNearTest`：同系列两张总分接近时稀有度高的一张排前，卡片先后、首推、推荐度和推荐全文一致。`DataTest`：特性规则的 `exclude`。
- **检证对象**：`advisor/pool.py`（`class_weights`、`slot_dists`、`player_pool`、`appear_prob`、`owned_schools`）；`advisor/data.py`（`_merge_players`、`player_synergy`、`hero_synergy`、`trait_rules`、`combos`、`SOURCE_GUIDE`）；`advisor/engine/parts.py`、`lookahead.py`、`advice.py` 的 `series_near`、`chances.py` 的 `heal_chance`；`advisor/ui/text.py`（`ordered_options`、`advice_text`）；`data/` 下的资料（`rules.toml`、适配表）。
- **什么时候要跑**：改了职业分值累加、适配表资料（特性/玩家思路/组合/队友）、前瞻里的职业变化说明、美工刀折算、`series_near`，或 `data/rules.toml` 的 `[income]`/`[endless_cycle]`/`[extreme]`/`[survival]` 以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_video_scoring`（上次约 2 秒）。
- **需要什么**：没有特别要求。
- **输出怎么看**：多数用例是「照资料核数值/文字」的对照；`SeriesNearTest` 用 `mock` 把前瞻改成固定值来构造局面，属实现细节。
- **出错时一般是哪里不对**：
  - `ClassPointsTest` 失败 → 职业分值累加（`advisor/pool.py` 的 `class_weights`）或最左格分布、前瞻里的职业变化说明变了。
  - `MechanicsTest` 失败 → 真梦梓运动手环、赛克斯减速/风扇、循环往复的等待回合与等级、大碗炖肉与半血的机制（`advisor/engine/parts.py`、适配表）。
  - `HealDiscountTest` 失败 → 美工刀的治愈来源折算（`advisor/engine/chances.py` 的 `heal_chance`、`parts.py`）。
  - `OpinionValuesTest` 失败 → 某条观点分值、极限难度/水乡古镇摩托头盔、组合/队友、银行卡晚拿折算或流派资料变了（对照 `data/` 资料）。
  - `PlayerLayerTest` 失败 → 按攻略视频整理的玩家思路（档位、条件规则、同系列一致、原文写法）变了（看 `advisor/data.py` 的 `_merge_players`）。
  - `SeriesNearTest` 失败 → 同系列接近时排前的规则（`advisor/engine/advice.py` 的 `series_near`）或方案下的写法。
  - `DataTest` 失败 → 特性规则的 `exclude` 没生效（看 `advisor/data.py`）。
  - 这些大多是照资料核的对照，改了资料就要把断言一起改。

## test_scheme_scoring.py：筹码方案（自己的方案）的打分和它与默认推荐的互斥

- **测试观点**：按测试类分。`WordingTest`：方案理由的标签、悬停整句、附注、位次的写法逐字正确。`TierPartsTest`：标了档位的筹码用档位代替玩家思路和特性，内置分、组合、队友、难度、收入折算照算，且必选不参与收入折算。`MutualExclusionTest`：没标的筹码不用默认推荐任何部分（四档、带条件的规则、推荐流派、必选补分），并且插一次按方案算不影响之后的默认推荐。`SchoolPartsTest`：目标流派倾向、锁定保护、被锁流派按不选、`locks_useless_school` 的判定。`OrderTest`：卡片按档位和顺位排（必选 > 推荐 > 可选 > 未标记 > 不选 > 其他流派；同档按顺位，未标记按总分），几处例外见下面四类。`SameTierTest`：同一档的推荐、可选按档位前瞻排先后（后一张两样都更好才对调：档位前瞻高出不少于 `[scheme] same_tier_near`，正好等于线也算、差一点不算；卡片上写的数（必选、推荐在下一次三选一里至少出现一张的机会）按卡片的取整不比前一张低（方案里没有必选、推荐时比卡片上写的可选的），取整后相等也算，相等时悬停另写的可选的那个数也不能更低；几张时冒泡到没有可调的，只比相邻两张）、两样方向相反时照顺位（排在最前的写「前瞻差不多，照方案顺位」，`MOVE_MIXED`；例如小町 0 星第 2 轮那一组）、只有一张或档位不同时不调、预计之后没有三选一时照顺位，卡片上的「前瞻优于 / 不如 / 差不多」标签。`TierAheadTest`：档位前瞻的各档分值（没标的取 `unmarked_lookahead`、被锁的流派按 `skip`）、和前瞻同一套算法但缓存分开、默认推荐和边界局面（已持有、未收录、之后没有三选一）为 0、说明里说的是哪几档。`TierAheadChanceTest`：档位前瞻卡片上的数是「至少出现一张的机会」：只说一张时和那一张的出现率逐位相同（`appear_any_mix` 对 `appear_prob_mix`），几张时不小于最大的一张、不大于各张相加（也不超过 100%），和另写的一份算法（直接从 `future_setup` 的分布算）对得上（一种来源、两种来源加几张候选地图、最左格没有分布三种局面），方案里标得再多也不超过 100%，可选时的整句写法、括号里照旧是各自的出现率；说的只有一张时括号里只写它的名字、不把同一个数再写一遍，它的出现率取整后没变时照旧不写括号（`test_one_chip`）。`LostTest`：选了会少拿必选、推荐的往后排（`reach` 的流派搭配，候选是必选、推荐时只比必选个数），能保住的几张写在 `Loss.behind`，前面没有能保住的时能保住的不选、被锁的写在 `Loss.keep_last`、选哪张都会少拿时 `Loss.alone`（悬停各接一句），避免不了的灰色、最后一次三选一不挪。`DemotionTest`：只按剩下几次兑现算、收入扣掉一半以上的推荐、可选按低一档排、排在那一档已有的后面（正好一半也降），满 3 星后的减半、晚拿银行卡的减半照算进分里、不算进要不要降（非经济型角色第 7 轮刚满 3 星不降），轮次读不出时不降，必选不降，和刷新比时按低一档。`RerollRuleTest`：方案里刷新什么时候排第一（未标记时概率过半还要按分值扣掉代价也划算；不划算时「预计不如」的悬停按有没有刷新代价写刷新次数紧或刷新会放弃本次的三个候选）、平均级数、代价模型、概率文字。`PercentTest`：推荐度和先后一致、0/100% 的写法。`SeriesTierTest`：同系列按档位判是否上位替代。`TeammateNeedTest`：方案里刚需只在按总分排的未标记筹码之间提，提过以后不在卡片上第二位时理由写实际的位次。`SoftLockTest`：软锁和走偏的各种走法、附注。`InvariantTest`：自动生成局面（三张候选 × 四档 + 未标记 × 有无刷新）下的不变量（位次照方案；先后按有效档和顺位，同一档倒过顺位的档位前瞻一定高出不少于线、卡片上的数也不更低，卡片上的数在 0～100% 之间、同一次三选一的几张「现在」那个数相同，拿不到的分组、不选和被锁在最后；刷新的位置和首推；推荐度和先后一致）。`CopyDefaultTest`：「复制为我的方案」的映射和基准首推。`PlanTest`：`Plan` 的归一化、`plan_of`、`Advice.scheme_schools`。`SessionSchemeTest`：`Session` 按当前角色的方案算、切换/编辑正在用的方案后立即重算、方案内容参与「是不是同一个三选一」的判断。`LogbookTest`：推荐日志记下方案和默认首推、校准不计入按方案推荐的记录。`CliTest`：命令行推荐全文按方案写位次，默认推荐不变。
- **检证对象**：`advisor/engine/` 的方案分支（`scorer.py` 的 `advise(scheme=...)`、`Scorer`，`scheme_rank.py`，`lookahead.py` 的档位前瞻，`parts.py` 的 `default_player_match`/`income_part`/`income_cut`，`context.py` 的 `Plan`/`plan_of`）；`advisor/pool.py` 的 `appear_any`/`appear_any_mix`；`advisor/schemes.py`（`Scheme`、`SchemeStore`、`scheme_from_default`、`default_tiers`、`target_schools`、`to_text`/`to_short`）；`advisor/session/`（按方案算、切换、推荐日志的 `scheme` 记录）；`advisor/ui/text.py`（`scheme_reroll_pills`、`advice_text`、`ordered_options`）；`advisor/logbook.py`；`advisor/cli.py`；`data/rules.toml` 的 `[scheme]`、`[synergy]`。
- **什么时候要跑**：改了方案计算（`advisor/engine/` 的方案分支）、方案存储/分享（`advisor/schemes.py`）、按方案推荐相关的界面文字（`advisor/ui/text.py`）、推荐日志（`advisor/logbook.py`）、命令行的方案全文（`advisor/cli.py`），或 `[scheme]` 规则以后。覆盖面很宽。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_scheme_scoring`（上次约 39 秒）。
- **需要什么**：没有识别样本/显卡/真实窗口；`LogbookTest`、`SessionSchemeTest` 会在测试沙箱里建临时文件夹。用到 `tests/support/sandbox.py`。
- **输出怎么看**：`subTest` 会把失败定位到具体局面（如 `scheme=`、`tiers=`、`调用=`）。`InvariantTest` 失败信息里带完整复现代码。
- **出错时一般是哪里不对**：
  - `WordingTest`/`PercentTest`/`OrderTest` 失败 → 方案的理由标签、悬停整句、位次或卡片先后不符（去看 `advisor/engine/` 里的 `SCHEME_*` 常量和 `scheme_rank.py` 的 `place_text`/`lost_text`）。
  - `TierPartsTest`/`MutualExclusionTest`/`SchoolPartsTest` 失败 → 档位没代替玩家思路/特性、内置分等没照算、没标的筹码错误地用了默认推荐的一部分、目标流派/锁定保护判定变了（看 `advisor/engine/parts.py`、`scorer.py`）。
  - `RerollRuleTest`/`SeriesTierTest`/`TeammateNeedTest`/`SoftLockTest` 失败 → 方案里刷新的排名规则、同系列按档位、刚需只在未标记间提、软锁/走偏的附注（看 `advisor/engine/reroll.py`、`scheme_rank.py`、`advice.py`）。
  - `SameTierTest`/`TierAheadTest`/`TierAheadChanceTest` 失败 → 档位前瞻（`advisor/engine/lookahead.py` 的 `tier_values`、`tier_future`、`tier_lookahead`、`tier_ahead`，卡片上的数 `_avg_any` 和 `advisor/pool.py` 的 `appear_any`、`appear_any_mix`）或同一档的对调（`scheme_rank.py` 的 `_same_tier`、`_passes`、`rate_key`、`_moves`、`[scheme] same_tier_near`、`unmarked_lookahead`）变了；卡片标签看 `advisor/ui/text.py` 的 `move_pills`、`_move_rates`、`scheme_ahead`。
  - `LostTest` 失败 → 「拿不到的往后」（`scheme_rank.py` 的 `reach`、`lost_kind`、`loss`、`advise_scheme` 里的分组）或悬停后接的一句（`advisor/ui/text.py` 的 `_loss_tail`）变了。
  - `DemotionTest` 失败 → 收入降档（`scheme_rank.py` 的 `demotion`、`effective`，`parts.py` 的 `income_cut`、`IncomeCut.left_cut`、`[scheme] income_demote`）变了。
  - `InvariantTest.test_combinations` 失败 → 自动生成局面下的某条不变量被打破；失败信息带复现代码。
  - `CopyDefaultTest`/`PlanTest` 失败 → 「复制为我的方案」映射、`Plan` 归一化或 `plan_of` 变了（看 `advisor/schemes.py`、`advisor/engine/context.py`）。
  - `SessionSchemeTest`/`LogbookTest` 失败 → `Session` 按方案算或重算的时机、推荐日志/校准提醒的局数变了（看 `advisor/session/`、`advisor/logbook.py`）。`LogbookTest.write_mixed_records` 写的那几条推荐日志另给 `tests/test_dev_tools.py` 的 `CalibrateSchemeRecordsTest` 借用（核对校准工具 `tools/calibrate.py` 不计入按方案推荐的记录），改它的时候那边一起跑。
  - `CliTest` 失败 → 命令行方案全文或 JSON 字段变了；有意改措辞时把断言一起改。

## test_scheme_reroll_per_map.py：按方案计算时连续刷新的递推按每张候选地图分别做再平均

- **测试观点**：`SchemeRerollPerMapTest` 的 2 个用例。① 地图待识别、两张候选地图时，刷新那一项的平均级数 = 两张地图各自（地图已知时）的平均级数取平均；② 由平均级数得到的结论标签（预计优于/相当/预计不如）和先后对得上。
- **检证对象**：`advisor/engine/scorer.py` 的 `Scorer.reroll`（方案分支）；`advisor/engine/scheme_rank.py`；`advisor/engine/reroll.py`；`advisor/pool.py` 的 `slot_dists`。
- **什么时候要跑**：改了方案模式下刷新的连续刷新估值、`scheme_rank.py` 或 `reroll.py` 以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_scheme_reroll_per_map`（上次约 0.02 秒）。
- **需要什么**：没有特别要求。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `test_average_of_each_map` 失败 → 方案模式的连续刷新递推没按每张候选地图分别做再平均（去看 `advisor/engine/scorer.py` 的 `Scorer.reroll`）。
  - `test_verdict` 失败 → 平均级数或结论标签变了；预期数（1.9387、2 级）是照当时资料算的，有意改资料或算法时把断言一起改。

## test_cli_options.py：测命令行画面读数那几项参数算得对不对、名字对不上时有没有提示、资料读不出来时怎么退出

- **测试观点**
  - `ScreenArgsTest`：把画面上读到的几项（进度上限、当前格、概率行、最左格稀有度、已完成任务数、具体难度名）和按同样状态直接算的结果逐项对照；具体难度名「极限」按资料的加分算、状态行难度写法不变；`--difficulty` 和 `--difficulty-name` 对不上时报错；不给这几项参数时各项都是「不知道」；`--endless-due`（循环往复那一次的到期轮次）进 `State.endless_pending`、`endless_due`，「预计之后还有筹码三选一」那一行写出这 1 次，到期在进度上限以后时不写，`--owned` 里没有循环往复时报错（`test_endless_due_option`）。
  - `NameNoteTest`：写错的筹码名按最像的计算并在错误输出提示一句（只差破折号这类写法的不提示）；`--unrecorded` 让对不上的候选按资料库未收录计算、并在输出里注明，不加时照旧换成最像的、没有够像的就报找不到。
  - `DataErrorTest`：推荐离不开的资料读不出来时，只把原因写到退出信息里，不打出调用栈。
- **检证对象**：`advisor/cli.py` 的 `main`、`build_state`、`difficulty_of`、`resolve_chip`、`to_json`、`render`、`difficulty_text` 和常量 `GUESSED_NOTE`、`UNRECORDED_NOTE`、`DIFFICULTY_CHOICES`、`DIFFICULTY_NAMES_OF`；`advisor/data.py` 的 `load_db`、`level_of_name`、`LEVEL_NIGHTMARE`、`DataError`；`advisor/engine/` 的 `advise`；`advisor/pool.py` 的 `State`。
- **什么时候要跑**：改 `advisor/cli.py` 的参数表或参数到状态的转换（`build_state`）；改难度名与档位的换算（`difficulty_of`、`advisor/data.py` 里的档位/难度名）；改筹码名匹配与提示文字（`resolve_chip`、`GUESSED_NOTE`、`UNRECORDED_NOTE`）；改 `to_json` 的输出结构或 `advisor/engine/` 里推荐分项（`parts`）；改 `State` 的字段；改命令行对资料读错的处理。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_cli_options`（上次约 0.5 秒；不是慢测试、实机测试）
- **需要什么**：没有特别要求（用仓库里的资料文件跑，不需要截图或显卡）。
- **输出怎么看**：同总则；用例把推荐结果当 JSON 比对，失败信息里会指出是哪一项字段对不上。
- **出错时一般是哪里不对**
  - `ScreenArgsTest.test_same_as_state` 失败 → 新参数没有正确进 `State`（`advisor/cli.py` 的 `build_state`），或 `to_json` 的输出和直接算的不一致。
  - `ScreenArgsTest.test_difficulty_name` 失败 → `difficulty_of` 的档位换算、状态行难度的写法（`difficulty_text`），或资料里「极限」档的加分（`rules.toml [extreme]`）改了；有意改数值/文字时断言里那几处要一起改。
  - `ScreenArgsTest.test_difficulty_conflict` 失败 → `--difficulty` 与 `--difficulty-name` 的搭配表或报错文字改了。
  - `ScreenArgsTest.test_defaults_unchanged` 失败 → 新参数的默认值被改成非「不知道」了。
  - `ScreenArgsTest.test_endless_due_option` 失败 → `--endless-due` 没进 `State`（`build_state`），或 `render` 里「预计之后还有筹码三选一」那一行的写法、引擎按到期轮次判断打不打得到（`advisor/engine/context.py` 的 `_endless_too_late`）改了。
  - `NameNoteTest.test_guessed_name_noted` 失败 → 相似度判定或提示文字（`resolve_chip`、`GUESSED_NOTE`）改了，或「只差写法直接认」那一步失效。
  - `NameNoteTest.test_unrecorded_option` 失败 → `--unrecorded` 的处理或「未收录」的输出写法（`UNRECORDED_NOTE`）改了。
  - `DataErrorTest.test_unreadable_data_exits_with_reason` 失败 → `advisor/cli.py` 的 `main` 不再把 `DataError` 变成只带原因的退出，改成抛异常或打调用栈了。
