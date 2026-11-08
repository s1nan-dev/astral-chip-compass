# 测试指南 · 悬浮窗

这是[《测试指南》](../测试指南.md)第八节的一章：每个测试文件一节，每节的结构见那里第八节的开头。


这一章管悬浮窗本体和拆出去的部件（advisor/ui/overlay.py、overlay_parts/）：卡片、提示、跟随与窗口层次、后台识别线程、退出收尾、全局热键。

## test_overlay_scenarios.py：用一局真人对局的剧本驱动悬浮窗，核对整段流程、用词、提示和可点击元素

- **测试观点**
  - `RealGameFlowTest`：照着剧本走一整局（启动时已有筹码未知 → 请打开「查看筹码」面板 → 三选一给推荐、选中、记录 → 漏记后补录 → 读到进度上限 → 记录和推荐日志），每一步核对界面写出来的字。
  - `MidgameMisjudgeTest`：对局中途误判「新的一局」时，「识别」卡片和「已有筹码」卡片各有一行提示和「撤销」，点任一个都恢复，并自动存一份不含截图的诊断包。
  - `GameOverScenarioTest`：本局难度随对局变化（困难及以上 → 读到红叉后噩梦及以上），结算画面判为结束（「对局 已结束」），结算之后从选角界面开始的新一局不可撤销、只作说明。
  - `KeyTextTest`：剧本里确实出现打开面板、打开任务栏、记录、补录、来源、刷新次数这几条提示；对局中途误判、撤销以后状态行写明误判时的识别记录存在哪；没看到结算画面、直接看到选角界面的新一局和上一局结束以后的新一局照常量写；补录行筹码按钮的悬停说明写「补录：筹码名」。
  - `EveryStepTest`：剧本每一步都在「一行显示」和「全部展开」两种方式下核对：需要操作的提示看得见、用词、不写分值、可点击的元素像按钮、提示写明点哪里。
  - `SeriesScenarioTest` / `SchemeScenarioTest` / `RefreshScenarioTest`：同系列筹码同时出现（上位替代）、用自己的「筹码方案」打同一局（位次）、刷新时中间帧保留旧推荐。
  - `WatcherReviewRecordTest` / `SwitchReplayTest`：识别暂停/恢复写进识别记录；一局真人对局的读数回放，核对切出游戏、被盖住、最小化时的识别状态和窗口层次。
  - `WeirdEventTest`：各种事件带奇怪的字段（`None`、空列表、未知的筹码名、超长筹码名）时悬浮窗不抛异常、之后照常工作。
- **检证对象**：`advisor/ui/overlay.py` 的 `Overlay`；`advisor/ui/overlay_parts/app.py` 的 `OverlaySession`、`UiSettings`；`advisor/ui/overlay_parts/watcher.py` 的 `Watcher`；`advisor/ui/overlay_parts/wording.py` 的提示常量；`advisor/session/`（`Session`、`Event`、`split_extra`）；`advisor/engine/`（`advise`、`place_text`）；`advisor/schemes.py`（`Scheme`、`SchemeStore`、`get_catalog`）；`advisor/pool.py` 的 `State`；`advisor/ui/services.py` 的 `Services`。
- **什么时候要跑**：改了 `advisor/ui/overlay.py`、`advisor/ui/overlay_parts/`（`app.py`、`watcher.py`、`wording.py` 等）、`advisor/ui/text.py`、`advisor/session/`、`advisor/engine/`、`advisor/schemes.py`，或改动了任何界面提示文字、卡片显示方式时。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_scenarios`（上次约 6.5 秒）。
- **需要什么**：`samples/labels.json`（一局真人对局的标注）。用到标注的那几个剧本类（`RealGameFlowTest`、`MidgameMisjudgeTest`、`GameOverScenarioTest`、`KeyTextTest`、`EveryStepTest`、`SchemeScenarioTest`）缺它时整个跳过，用的是每个类头上的 `@needs_labels`（`@unittest.skipUnless(samples.samples_ok("labels.json"), ...)`），跳过原因由 `samples.samples_reason("labels.json")` 拼，形如「没有识别样本：缺 samples\labels.json（准备方法见 docs/测试指南.md「准备识别样本」）」；`SeriesScenarioTest`、`RefreshScenarioTest`、`WatcherReviewRecordTest`、`WeirdEventTest` 不用样本，照常跑。样本目录用环境变量 `ACC_SAMPLES_DIR` 指（没设时是仓库根目录的 `samples/`）。`SwitchReplayTest` 另外读随仓库发布的 `tests/data/replay_20261004_switch.json`，不需要样本、不会跳过。界面一律用 Qt 的离屏平台，不弹窗。
  - 用到的标注字段（`labels.json` 里 `file` 以 `_real`/`_1600x855` 结尾的那些）：`file`、`offer`（3 个候选筹码名）、`selected` 或 `picked`（绿框）、`rerolls`、`probabilities`、`rarity`、`team`、`owned`（面板上的已有筹码）、`screen`（结算画面的读数：地图、难度、轮数）。
- **输出怎么看**：缺样本时这几条的跳过原因就是上面那句。`ScenarioBase` 里有个 `counts` 计数器，`assert_wording`/`assert_no_scores` 等每次实际查了一处就加一，`EveryStepTest` 末尾用 `assertGreater` 核对确实查够了（例如「文字」> 2000 处）——这一步失败说明检查落空了（说明改动的方向让检查不再命中），不一定是界面写错。
- **出错时一般是哪里不对**
  - `RealGameFlowTest.*` 失败 → 多半改了 `advisor/session/` 的事件顺序/推荐/记录，或 `advisor/ui/overlay.py` 的显示（去看 `Session.on_hud`/`on_frame`、`Overlay.handle`）。
  - `MidgameMisjudgeTest.*` 失败 → 误判新一局的判定、两条提示、撤销恢复或诊断包（去看 `advisor/session/newgame.py`、`Overlay` 里 `newgame` 分支）。
  - `GameOverScenarioTest.*` 失败 → 难度推算或结算画面改了（去看 `advisor/session/difficulty.py`、`advisor/ui/overlay_parts/wording.py`）。
  - `KeyTextTest.test_key_texts` 失败 → 剧本里某条提示不再出现，或撤销、新一局、补录按钮悬停说明的写法改了（去看 `advisor/ui/overlay_parts/wording.py`、`advisor/session/`）；有意改的话把本文件顶部的常量一起改。
  - `assert_prompts_visible` 失败 → 卡片被隐藏/收起、提示或按钮没显示（去看 `Overlay.card_open`、`notice_model`）。
  - `assert_wording` 失败 → 界面出现了口语、「我」「你」、旧说法或「None」（去看对应文字常量）。
  - `assert_no_scores` 失败 → 屏幕上写了分值、总分或整句后面的分值括注（去看 `advisor/ui/text.py`）。
  - `assert_clickables` 失败 → 可点击元素没有手形光标、没有底色或框线（去看 `Overlay.styleSheet`）。
  - `WatcherReviewRecordTest` / `SwitchReplayTest` 失败 → 识别暂停/恢复规则或窗口层次改了（去看 `advisor/ui/overlay_parts/watcher.py`、`advisor/ui/winapi.py`）。
  - `WeirdEventTest.*` 失败 → `Overlay.handle` 某种事件分支没兜住 `None`/空值（去看 `Overlay.handle`）。
  - **改了预期就该跟着改**：剧本里那些提示原文是照界面上的实际写法抄的常量；界面文字是有意改的就把本文件顶部的 `PANEL_PROMPT` 等常量一起改。

## test_overlay_behavior.py：悬浮窗按识别事件显示出来的文字和卡片开合（行为级）

- **测试观点**：只用公开探针（摘要、状态行、提示行、各卡片的开合、是否让边框变色等）断言屏幕上看得见的东西；事件全部由真实 Session 产生，按识别流程的顺序交给悬浮窗。
  - 三选一给出推荐：打开「推荐」卡片、列出三个候选筹码、首推标「★」、摘要有「刷新 ×N」；三选一期间收起这张卡片后同一次里不再自动展开，下一次三选一时自动展开（`AdviceEventTest`）。
  - 选中后关闭：记下选择；选的是首推时「推荐」卡片收起、可回看上一次推荐；选的不是首推时问「未选择首推的原因」并给出四个原因按钮和问句右边的「×」（`PickedEventTest`）。
  - 一个都没认出来就关了时不请补录；候选筹码没读全时列出改正入口；点刷新后的中间帧保留刷新前的推荐、只标「正在刷新」（`IdleEventTest`、`UnreadableEventTest`、`RefreshingEventTest`）。
  - 角色待识别时推荐暂停，请用户在「筹码方案」卡片的角色下拉框里选角色，并让边框变色（`HeroNeededEventTest`）。
  - 漏记时补录行列出候选筹码按钮；同一组候选再次出现时撤销刚记下的筹码，补录行的说法写明是「再次出现、已撤销」（`MissedPickEventTest`、`PickRevertedEventTest`）。
  - 星数增加却没有对应三选一时提示补录；本局结束时写「对局 已结束」、需要操作的提示收起、边框不再变色，上一次推荐和已有筹码保留（`UnrecordedGainEventTest`、`GameOverEventTest`）。
  - 新的一局：对局中途判定的可以撤销（撤销后已有筹码恢复），结算画面之后的是不可撤销的说明（`NewGameEventTest`）。
  - 「查看筹码」面板：请打开、读到、队友页签；读到任务栏上的地图；`status` 只刷新摘要（`PanelEventsTest`、`MapReadEventTest`、`StatusEventTest`）。

- **检证对象**：`advisor/ui/overlay.py` 的 `Overlay.handle`、`handle_events`；`advisor/ui/overlay_parts/dispatch.py` 的 `EventRouter`、`EventHandlers`；各卡片部件（`advice_card.py`、`owned_card.py`、`scheme_card.py`、`status.py`）和 `notices.py`；文字在 `advisor/ui/overlay_parts/wording.py`；事件来自 `advisor/session/`。

- **什么时候要跑**：改了悬浮窗的事件分派、任何一张卡片、状态胶囊或摘要的排法、事件种类表；拆动 `advisor/ui/overlay.py` 的结构以后（这批测试刻画的是「现在」的正确行为，结构整理时一行都不该改）。这个文件里的 `BehaviorBase`、`OFFER`、`RESULT` 被别的悬浮窗测试文件共用，改动时要留意 `test_overlay_advice`、`test_overlay_event_branches`、`test_overlay_late_events`、`test_overlay_follow` 等一起受影响。

- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_behavior`（上次约 7 秒）。

- **需要什么**：没有特别要求。喂的是真实 Session 产生的事件，不用识别样本截图；后台线程改为直接调用；用离屏的 Qt，不弹窗口、不写文件。

- **输出怎么看**：同总则。

- **出错时一般是哪里不对**：
  - 某一类的卡片或文字不对 → 多半是对应事件的处理（`overlay_parts/dispatch.py` 的 `EventHandlers.on_*`）或那张卡片的部件改坏了。
  - 摘要、状态行、提示行的文字不对 → 多半是 `overlay_parts/status.py` 或 `overlay_parts/wording.py` 里改动了写法。
  - 卡片该自动展开/收起却没做到 → 多半是 `overlay_parts/layout.py` 的摆放规则或卡片的开合状态改坏了。
  - 边框该变色却没变 → 多半是「需要您操作」的提示状态（`NoticeModel.needs_action`）没传对。
  - 有意改了界面文案或布局时，要一并改这里的预期。

## test_overlay_parts.py：不建窗口，单独测悬浮窗拆出去的几个纯计算模块

- **测试观点**：从四个测试类检查——① `TakeViewTest`：刷新时取的会话快照（`state.take_view`）里列表是复制的；本局已结束时不再请用户选地图、补录；读不到的项按读不到算；星币拆账是「取走」式、只在要时取一次；升星花费只在读到星币数和星数时算、算错只写星币数；读某一项时抛异常只让那一组按读不到算。② `StatusTest`：状态胶囊与一行摘要的纯计算——识别本身状态的先后（已暂停 ＞ 暂不可用 ＞ 照常进行）；各项的先后与写法（写明主体、对局中待识别标橙）；一行摘要的取舍与缩短；状态胶囊悬停说明的分行；角色胶囊的写法；等待文字。③ `NoticeModelTest`：提示行怎么组装——对局中途判定的新一局两种写法与所属区域；面板提示的几种写法；补录与皮肤可用的提示；全局提示的顺序（「免费软件」声明排最后）。④ `OfferLogTest`：推荐日志的编号与记账——同一次三选一沿用编号；刷新换编号并记「刷新」；撤销、改正、撤销新一局；对局结束只记一次；三选一刚出现时读错（刷新次数读成 0、候选筹码名也读错）、补认改正后重算的推荐仍沿用同一个编号（不当成刷新）；记账认作另一次三选一（推荐事件的 `select_no` 变了，例如紧接着出现、概率行一样的两次）时换新编号、不按刷新次数另判（界面仍算显示着也一样），同一次里照旧按刷新次数判刷新（`test_session_says_another_select`）；同一组候选关了又出现、`select_no` 加了一时照旧沿用原来的编号（`test_reopened_keeps_id_with_select_no`）；补录的选择记 `via="supplement"`；认得出这一局的客户端时随三选一传给推荐日志（`client`），认不出时不传这个参数。
- **检证对象**：`advisor/ui/overlay_parts/state.py`（`take_view`、`egg_inputs`、`SessionView`）、`advisor/ui/overlay_parts/status.py`（`capsule_bit`、`activity_tip`、`status_bits`、`pill_bits`、`status_tip`、`hero_pill`、`waiting_text`、`line_items`、`summary_tip`）、`advisor/ui/overlay_parts/notices.py`（`NoticeModel.rows/area/tips/needs_action`）、`advisor/ui/overlay_parts/records.py`（`OfferLog`）、`advisor/ui/overlay_parts/geometry.py`（`window_plan`）、`advisor/ui/overlay_parts/wording.py`（各文案常量）；`advisor/ui/services.py` 的 `Services`；`tests/support/fakes.py` 的 `FakeLogbook`。
- **什么时候要跑**：改了上面这些纯计算函数或 `wording.py` 的文案常量；改了会话快照读的那些字段（`SessionView`、`take_view` 读的接口）或 `Services` 的接口时。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_parts`（上次约 0.006 秒）。
- **需要什么**：没有特别要求。不建悬浮窗、不建 QApplication，只喂数据看算出来的结果。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `TakeViewTest.test_copies_lists` 失败 → `state.take_view` 里列表没做复制，识别线程之后原地增删会改动快照。
  - `TakeViewTest.test_game_over_masks_ask_and_missed` 失败 → 本局已结束时没把「请选地图／补录」压掉。
  - `TakeViewTest.test_ledger_taken_only_when_asked`、`test_coin_cost` 失败 → 星币拆账的取用时机或升星花费的计算改了。
  - `StatusTest.test_status_bits`、`test_pill_bits`、`test_capsule_precedence` 失败 → `status.py` 里状态优先级、胶囊先后或各项写法改了。
  - `StatusTest.test_line_items`、`test_summary_tip` 失败 → 一行摘要的取舍、缩短步骤或悬停说明拼接改了。
  - `NoticeModelTest.test_global_rows_order`、`test_midgame_new_game` 失败 → `notices.py` 里提示行顺序、区域或文案改了（顺序/文案是有意改的，就把断言的预期一起改）。
  - `OfferLogTest.*` 失败 → `records.py` 的 `OfferLog` 编号或记账逻辑改了；`test_reread_fix_keeps_id` 单独失败时，多半是把补认改正后重算的推荐当成了刷新（换了编号）；`test_session_says_another_select`、`test_reopened_keeps_id_with_select_no` 失败 → `on_advice` 没按事件的 `select_no` 认是不是同一次（`offer_select`），或把又出现的同一组候选当成了另一次。

## test_overlay_prompts.py：需要用户操作的提示与「本局的开始结束」

- **测试观点**：从两个测试类检查——① `OverlayPromptTest`：需要操作时边框变色；各条新事件的提示放在所属卡片里——地图要用户选时边框变色、「查看筹码」面板的提示放在「已有筹码」卡片且面板读到后收起、面板提示期间已有筹码写「已有筹码待识别」；角色待识别时推荐暂停、请在下拉框选角色（一行显示时「筹码方案」卡片随之出现）；撤销新一局后按恢复后的状态决定面板提示是否显示；队友页签的提示不按时间收起；对局中途判定的新一局（轮次、星数、角色）排在最上、边框变色，「已有筹码」卡片里另有「撤销」；星数增加需补录与补录栏搜索；同一组候选关闭后又出现时撤销刚记的选择；地图发放的筹码行；标题栏「补录筹码」按有没有来源增减与突出；本局识别到「神秘宝藏」时所有角色都出补录按钮；两个来源并列成一句；补录时有不经三选一的来源、又没有待补录的升星（按 Session 记着的待补录个数，不按提示行最近一次的个数）才记为不经三选一获得，「未检测到选择」的补录行还在、补的可能就是那次选的（那组里认出的一个，或那组有认不出的格子）时不记；上一局状态还在时读到的组件留给下一局；角色待识别时不写职业胶囊和职业那一句；「打开任务栏」那一行在全部展开时也要读到轮次（进了对局）才显示，请用户选地图的那一行照常显示；队友、畸变组件、不支持的情况；新按钮都是带底色框线的按钮；界面文字不含口语字样。② `GameOverTest`：对局结束的显示与保留——摘要与状态胶囊以「对局 已结束」开头、悬停写依据；收起只对进行中有意义的提示、边框不变色；「未选择首推的原因」照常问；推荐日志只记一次；结束后看到选角界面、或连续两次读到同一轮次时的两种走法；手动模式的等待文字。
- **检证对象**：`advisor/ui/overlay.py`（`Overlay.handle`、`refresh`、`undo_new_game`、`_waiting_text`、`needs_attention` 及提示行组装）、`advisor/ui/overlay_parts/app.py`（`OverlaySession`、`UiSettings`）、`advisor/ui/overlay_parts/wording.py`（`PROMPT_HERO`、`GAME_OVER_*`、`EXTRA_CHIP_*` 等文案常量）、`advisor/ui/text.py`（`tip_lines`、`tip_paragraphs_html`）；`advisor/session`（`Event` 与各 `on_*` 读数）；`tests/support/overlay_cases.py`（`frame`、`button`、`choose_hero`、`expire_flash` 等）。
- **什么时候要跑**：改了 `overlay.py` 的事件处理或提示逻辑、面板提示与地图选择行的显示条件、新一局撤销、对局结束的显示与保留、补录按钮的显示条件，或改 `wording.py` 里这些文案时。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_prompts`（上次约 4.4 秒）。
- **需要什么**：Qt 离屏平台（不弹窗口）；不需要识别样本或显卡。个别用例用假时钟跳过约 15 秒的一次性提示。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `test_attention_border`、`test_panel_prompt_and_read` 失败 → 边框变色的判断或面板提示的显示/收起改了。
  - `test_hero_needed_then_choose`、`test_hero_needed_choose_same_hero` 失败 → 角色待识别时推荐暂停、请选角色的提示或「筹码方案」卡片的出现条件改了。
  - `test_midgame_new_game_needs_action`、`test_star_and_hero_new_game_are_midgame`、`test_select_screen_new_game_stays_info` 失败 → 对局中途判定新一局的两种提示行、区域或「只作说明」的判断改了。
  - `test_new_game_undo*`、`test_undo_new_game_restores_panel_prompt_state` 失败 → 撤销新一局恢复的状态或提示收起改了。
  - `test_unrecorded_gain_and_search`、`test_pick_reverted` 失败 → 补录流程或「候选再次出现时撤销选择」改了。
  - `test_granted_chips_dragon_palace` 失败 → 地图发放的筹码行改了。
  - `test_no_class_while_hero_pending` 失败 → `overlay_parts/status.py` 角色待识别时又按上一局的角色写了职业胶囊或悬停里职业那一句；`test_map_hint_waits_for_game` 失败 → `scheme_card.show_map_row` 的 `map_entered`、或 `layout.apply_mode` 里地图选择行的显示条件改了。
  - `test_extra_chip_source_highlights_add`、`test_mysterious_treasure_quick_add`、`test_extra_chip_sources_joined` 失败 → 「补录筹码」按钮的显示条件或文案改了（组件名和资料不一致时也会先在这里失败，资料里改了名字时要一起改）。
  - `test_quick_add_marks_direct_only_with_source_and_no_unrecorded`、`test_quick_add_direct_follows_session_unrecorded`、`test_quick_add_not_direct_while_missed_pick_pending` 失败 → 补录时记不记为不经三选一获得（`Overlay.add_owned` 交给 `Session.add_chip` 的 `direct`）的条件改了：要有不经三选一的来源，待补录的升星按同一把锁里取的快照（`unrecorded`）算，「未检测到选择」的补录行（`missed_offer`）还在、补的可能就是那次选的时不记。
  - `GameOverTest` 各用例失败 → 对局结束的判定/显示、保留内容或推荐日志「只记一次」的逻辑改了。
  - `test_select_screen_starts_next_game`、`test_round_starts_next_game` 失败 → 结束后进入下一局的依据或说明文字改了。

## test_overlay_reason_text.py：理由标签的短句与悬停说明的排版

- **测试观点**：从两个测试类检查——① `ReasonShortTest`：各类理由标签的短句取舍与悬停整句——成长分、角色分、标签分、关键词分、推荐流派、筹码方案、组合、预判、队友、难度、收入折算、循环往复、职业倾向、未收录；每类的长度上限、不写分值、悬停就是原来的整句、短句里的数字和名字取自整句、不出现特性内部名和口语字样；前瞻短句取哪一句；刷新那一行；九成左右的理由标签不超过 18 个字。② `TipLinesTest`：悬停说明的排版——筹码名/角色名/地图名/畸变组件名/常用术语/「X」流派整体不拆；断点优先落在标点之后、其次是左引号左括号之前；两行放得下就不分三行；行首不放「的」等虚词；没设置名称表时的旧规则；悬浮窗建好资料库后设置名称表；长的路径在分隔符处换行；卡片里自动换行的文字用不换行连接符保持词不拆、且不占宽度、英文长串照旧能断。
- **检证对象**：`advisor/ui/text.py`（`part_pill`、`tag_pill`、`lookahead_pill`、`short_text`、`reroll_pills`、`scheme_reroll_pills`、`share_texts`、`loss_pills`、`place_tip`、悬停排版的 `tip_lines`/`tip_html`/`tip_paragraphs_html`/`keep_words`/`plain_words`/`set_tip_names`/`tip_names`、`option_views`、`breakdown_rows`/`breakdown_html`、`net_parts`、`advice_summary`/`advice_text`）；`advisor/engine`（`Scorer`、`advise`、`lift_core`、`advice_text.free_short`，以及 `Place`/`Loss`/`RerollView` 等）；`advisor/ui/overlay_parts/factory.py`（`WrapLabel`、`WrapTaggedText`、`_text_label`）；`advisor/ui/overlay.py`（建好资料库后设置名称表）。用真资料库（`data`）和推荐引擎。
- **什么时候要跑**：改了 `text.py` 的短句、悬停排版或断行规则；改了 `data` 下适配表里的理由或短句（`reason`、`short`）；改了推荐引擎生成的理由整句；或改了卡片里自动换行文字的处理（`keep_words`/`plain_words`）时。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_reason_text`（上次约 6.9 秒）。
- **需要什么**：Qt 离屏平台；要用真资料库和推荐引擎，所以要能读到 `data` 下的资料文件。
- **输出怎么看**：同总则。断行相关的用例失败时，断言消息里会列出被拆开的名称或各行的断点位置。
- **出错时一般是哪里不对**：
  - `test_official_parts`、`test_player_layer`、`test_auto_layer_free_text`、`test_combos`、`test_teammates` 等失败 → 对应分项的短句或整句改了；某条写了短句的资料规则（`short`）超长或末尾带数字也会在这里失败。
  - `test_difficulty_income_endless`、`test_tendency_and_unrecorded`、`test_anticipations` 失败 → 难度、收入折算、循环往复、职业倾向、未收录或预判的短句/整句改了。
  - `test_lookahead_short_choice`、`test_lookahead_lost_offer_chip`、`test_lookahead_short_from_reason` 失败 → 前瞻短句的取舍或整句写法改了。
  - `test_reroll_pills`、`test_most_pills_fit_one_line` 失败 → 刷新那一行的标签或理由标签长度上限改了（比例断言会随资料浮动）。
  - `TipLinesTest.test_names_and_terms_kept_whole`、`test_punctuation_first`、`test_real_texts` 失败 → `tip_lines` 的断行规则或不拆的词语表改了。
  - `test_two_lines_when_they_fit`、`test_hammer_chance_and_path_breaks` 失败 → 两行/多行的判断或路径换行改了。
  - `test_card_labels_keep_words`、`test_english_message_still_wraps` 失败 → `keep_words` 的插入规则改了（哪类空格换成不换行空格）。
  - `test_overlay_sets_names` 失败 → 悬浮窗建好后设置名称表这一步改了。

## test_overlay_refresh_batch.py：刷新合并（一次操作里的几次重画合成一次）

- **测试观点**：从多个测试类检查——① `FrameBatchTest`、`DeliverBatchTest`：一帧整批事件（`handle_events`）或一次交出多个事件（`router.deliver`）只真正刷新一次，显示和逐个处理时逐项相同；中间一个事件出错时记进错误记录、后面的照常处理、最后照常刷新一次。② `NewGameRestoreBatchTest`：晚到的 `newgame` 与要重画的推荐合并成一次刷新（批处理和 `router.handle` 各自都要合并）。③ `FixConfirmBatchTest`：改正候选的一串刷新合成一次。④ `RecomputeBatchTest`：切换方案重算合成一次。⑤ `MapChangedBatchTest`：选地图（难度胶囊跟着变）合成一次。⑥ `HiddenWindowTest`：窗口没显示时不合并，每次都真刷新。⑦ `BatchErrorTest`：合并范围里出错、出的那次刷新自己也出错时，往外抛的仍是原来那个错，刷新的错交给错误记录。⑧ `FrameCombosTest`：一帧几个事件的多种组合，一行显示与全部展开各做一遍，整批与逐个的文字、开合、高度、整窗截图都相同，且整批只真刷新一次。⑨ `CollapsedCardTest`：收起的卡片记着收起时已在显示的提示，一帧里先消失又出现的同一条提示让它照旧展开，这时中间那次刷新照做。
- **检证对象**：`advisor/ui/overlay.py`（`handle`、`handle_events`、`refresh`/`_refresh_now`/`_batch`/`_leave_batch`）、`advisor/ui/overlay_parts/dispatch.py`（`EventRouter.handle/deliver`）、`advisor/ui/overlay_parts/layout.py`（`apply_mode` 被当作「真刷新」的计数点）、`advisor/ui/overlay_parts/app.py`（`OverlaySession`、`UiSettings`）、`advisor/session/` 的 `split_extra`；`tests/test_overlay_behavior.py` 的 `BehaviorBase`。
- **什么时候要跑**：改了 `overlay.py` 的刷新合并、`EventRouter`、`layout.apply_mode`，或改了各类交互触发的刷新时。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_refresh_batch`（上次约 34.7 秒）。
- **需要什么**：Qt 离屏平台；窗口要在离屏平台上显示着（窗口没显示时不合并，所以必须先显示）。
- **输出怎么看**：同总则。计数靠给 `layout.apply_mode` 打桩；失败时断言消息会写「一帧整批处理只该真正刷新一次」。
- **出错时一般是哪里不对**：
  - `test_frame_events_refresh_once`、`test_deliver_refresh_once_and_matches_one_by_one` 失败 → 批量处理的合并范围或顺序改了。
  - `test_middle_error_does_not_block_the_rest` 失败 → 批处理里单个事件出错的处理改了（应记进错误记录、后面照常、最后照常刷新一次）。
  - `test_restore_refreshes_once`、`test_router_handle_merges_restore` 失败 → 晚到 `newgame` 的重画合并改了。
  - `test_fix_confirm_refreshes_once`、`test_scheme_switch_refreshes_once`、`test_map_changed_refreshes_once` 失败 → 对应交互触发的刷新次数改了。
  - `test_hidden_window_refreshes_every_time` 失败 → 「窗口没显示时不合并」的条件改了。
  - `BatchErrorTest.*` 失败 → 批处理出错时异常传递或刷新出错的上报改了。
  - `FrameCombosTest.*` 失败 → 整批与逐个处理结果不一致（合并丢了中间状态），看是哪一种组合。
  - `test_hero_card_reopens_for_next_offer` 失败 → 「收起卡片记提示、重新展开」的逻辑改了。

## test_overlay_robustness.py：悬浮窗不常走的路径（出错、并发、后台）

- **测试观点**：从多个测试类检查——① `ProbeErrorTest`：查看游戏窗口出错时这一次不算数（不当成找不到游戏窗口、不暂停识别），错误记进错误记录、同一个错只整条记一次。② `RefreshGuardTest`：刷新时一块出错只记进错误记录，其余几块照画，之后不出错了照常画回来。③ `EventKindsTest`：代码里建的事件种类都在悬浮窗认识的种类里（用 AST 扫 `advisor/session/` 各模块和 `vision/pipeline.py` 里建的事件名）；不认识的事件只刷新、记一笔。④ `ExportTest`：导出诊断的写盘与读识别记录放在后台线程做、界面线程一次都不调用；正在写诊断包时撤销了误判的新一局，撤销时的存档排着等前一份写完接着存；后台任务中途抛出时照样告诉界面（按失败提示、按钮又能用、排着的照存）。⑤ `AttentionTest`：`needs_attention` 和边框用同一个判断。
- **检证对象**：`advisor/ui/overlay.py`（`_guarded` 分块刷新、`needs_attention`/`_update_attention`、`export_diagnostics`、`follower.poll`）、`advisor/ui/overlay_parts/dispatch.py`（`EVENT_KINDS`）、`advisor/ui/overlay_parts/owned_card.py`（`chip_tip` 被当作会出错的绘制步骤）、`advisor/ui/winapi.py`（`GameProbe`）、`advisor/ui/overlay_parts/wording.py`（`NO_GAME`、`UNDONE_SAVED`）、`advisor/ui/overlay_parts/background.py`（诊断导出）、`advisor/session` 各模块与 `advisor/vision/pipeline.py`（被 AST 扫描）；`tests/support/errors.py`（`capture_errors`）、`tests/support/sandbox.py`（`REFRESH_TITLE`）、`tests/support/waits.py`；`tests/test_overlay_behavior.py` 的 `BehaviorBase`。
- **什么时候要跑**：改了 `overlay.py` 的分块刷新兜底、错误记录文案、导出诊断的线程安排；在 `advisor/session` 或 `vision/pipeline.py` 里新增了一种事件（`EVENT_KINDS` 要同步）时。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_robustness`（上次约 2.0 秒）。
- **需要什么**：Qt 离屏平台；不找真实游戏窗口、不写用户的文件（探测函数、诊断模块、识别记录器都是假的）。
- **输出怎么看**：错误记录写进每个测试自己的临时文件。`RefreshGuardTest` 的注释说明测试沙箱按错误标题认出漏接的分块刷新出错（会改退出码），所以标题改写时那边要一起改。
- **出错时一般是哪里不对**：
  - `test_probe_error_keeps_last_state` 失败 → 探测函数出错时的兜底改了（把出错当成了「找不到游戏窗口」）。
  - `test_one_block_failing_does_not_stop_others` 失败 → 分块刷新的出错隔离或错误记录标题改了。
  - `test_all_emitted_kinds_known` 失败 → 有新的会话/识别事件没加进 `EVENT_KINDS`（漏登记或事件名改了）。
  - `test_unknown_kind_refreshes_and_logs` 失败 → 不认识事件的兜底或记录文案改了。
  - `ExportTest.*` 失败 → 导出诊断的线程安排、排队或失败上报改了。
  - `test_same_as_border` 失败 → `needs_attention` 和边框判断不再一致。

## test_overlay_room_undo.py：房间画面结束的局，之后判出的新一局能撤销

- **测试观点**：组队房间要隔一会儿连续读到两次才结束本局（只读到一次不算）；结束后按轮次开始的新一局和对局中途判出的新一局一样带「撤销」、按「需要您操作」显示，撤销后恢复上一局的已有筹码、「对局 已结束」一起撤回；从选角界面开始的不可撤销（只在开局时出现），只作一次性说明。`RejoinAfterMenuTest`：主菜单结束的局又连回同一局时不开新的一局，悬浮窗回到结束之前的样子（「对局 已结束」和等下一局的那句收起、换回等三选一的那句，已有筹码在，「查看筹码」面板提示按结束前放回），不另加提示；推荐日志不换局，真正结束时再记一条结束。
- **检证对象**：`advisor/ui/overlay.py`（`undo_new_game`、提示行）、`advisor/session`（`on_out_of_game` 读组队房间和主菜单、`on_select_screen`、`on_hud`、`can_undo_new_game`、`game_over`）、`advisor/ui/overlay_parts/dispatch.py`（`on_status` 的 `rejoined`）、`advisor/ui/overlay_parts/wording.py`（`NEWGAME_TEXT`、`NEWGAME_MIDGAME`、`NEWGAME_OWNED`）；搭法和 `tests/test_overlay_behavior.py` 的 `BehaviorBase` 相同。
- **什么时候要跑**：改了「房间画面结束本局」的判定（连续两次读到）、结束后新一局的依据或可撤销性、撤销后恢复的状态，或改这几句文案时。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_room_undo`（上次约 0.8 秒）。
- **需要什么**：Qt 离屏平台。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `end_by_room` 相关失败 → 「组队房间要连续两次读到才结束本局」的判定改了。
  - `test_select_after_room_not_undoable` 失败 → 从选角界面开始的新一局被误当成可撤销，或说明文字改了。
  - `test_round_after_room_undoable` 失败 → 误认出的房间结束后、按轮次开新一局的显示或撤销恢复改了。
  - `test_rejoin_restores_view` 失败 → 连回同一局时悬浮窗的处理（`dispatch.py` 的 `on_status` 里 `rejoined` 那一支、`records.py` 的 `OfferLog.end_revoked`），或记账那边连回的条件（`advisor/session/offgame.py` 的 `rejoinable`）改了。

## test_overlay_scheme_card.py：「筹码方案」卡片

- **测试观点**：从多个测试类检查——① `SchemeCardTest`：卡片布局（角色下拉框在上、方案下拉框在下，三个按钮跨两行，「作业站」打开当前角色的方案列表）、下拉框不加颜色、只列当前角色的方案、切换方案立即按当前三选一重算、编辑窗口与导入/导出窗口的接线、在编辑窗口里给当前角色新建/复制后悬浮窗当场换用（没有三选一时只出前半句提示，有三选一时按新方案重算；给别的角色建的不换，角色待识别时给卡片上显示的角色建的照样换）、方案下拉框换用后编辑窗口重标「使用中」、导入后「使用这套方案」三种结果（当场换用、记下给别的角色、方案已删掉）、方案文件读取结果与个人目录退回的提示、方案名/说明里的网页标记原样显示、滚轮不切换方案、识别记录。② `SchemeCardHeightTest`：全部展开的高度与三选一期间的位置。③ `SchemeOptionTextTest`：用自己的方案时推荐卡片上的文字（位次、马上拿不到（算少拿的橙色、悬停后接能保住的几张，避免不了的灰色）、只剩一个流派名额、只写悬停、全部必选、走偏、同系列、刷新那一行（含收入降档按低一档比、未标记时刷新次数紧的「预计不如」、刷新有多出来的「留 2 次，多出 3 次不必留」（悬停写之后的三选一按几次留、另多留 1 次以防估少）、剩余刷新次数没读出来时「消耗 1 次刷新」的悬停写明是估计的）），不开窗口。④ `SchemeAdviceCardTest`：这些文字画在卡片上的样子（位次在候选第一行、首推样式、同一档按档位前瞻排先后的标签、摘要与回看）。⑤ `AdviceBasisTest`：「推荐」卡片头那段「角色 · 方案」（默认推荐和我的方案、手动指定的角色后接「（手动指定）」、按推荐自己算的角色和方案写而不跟下拉框、角色待识别和卡片收起时不显示、回看上一次推荐时写上一次的、七套皮肤在窄的悬浮窗里截短后放得下且不挤掉区域名）。⑥ `HeroBoxGroupsTest`：角色下拉框的组名行（每组前面一行、不能选、按角色找下标和选中项都对；键盘上下、滚轮、按首字母跳都跳过组名；推荐暂停时点选同一个角色照样算选了一次）。⑦ `ComboRowDrawingTest`：下拉列表每一行的画法（鼠标所在行的高亮框、当前值左侧的强调色短条、不能选的分组标题不高亮并画成粗体淡色的标题、行高）。
- **检证对象**：`advisor/ui/overlay.py`（`_scheme_chosen`、`_use_scheme`、`_scheme_added`、`_use_requested`、`_recompute`、`_schemes_changed`、`_schemes_imported`、`_hero_changed`、`_hero_restore_clicked`、角色/方案下拉框、`diagnostic_state`）、`advisor/ui/overlay_parts/scheme_card.py`（`SchemeCard`）、`advisor/ui/overlay_parts/advice_card.py`（`AdviceCard.basis`、`show_basis`、`fit_basis`）、`advisor/ui/overlay_parts/dialogs.py`（`Dialogs` 打开编辑/导入导出窗口、`show_used`、`show_use_result`）、`advisor/schemes.py`（`SchemeStore`、`Scheme`、`get_catalog`、`default_schools`、`LoadNotice`）、`advisor/ui/text.py`（`option_views`、`advice_summary`、`advice_text`、`scheme_reroll_pills`、`loss_pills`、`place_tip`、`move_pills`、`demotion_pills`、`scheme_ahead` 等）、`advisor/ui/widgets.py`（`ComboRowDelegate`、`draw_combo_rows`、`fill_hero_combo`、`add_combo_header`、`Pill`）、`advisor/engine`（`plan_of`、`Place`、`Loss`、`RerollView` 等）、`advisor/ui/overlay_parts/geometry.py`（`live_placement`）、`advisor/ui/overlay_parts/wording.py`（`SCHEMES_*` 文案）；方案文件放测试沙箱临时文件夹，编辑/导入导出窗口用假窗口。
- **什么时候要跑**：改了「筹码方案」卡片、方案存储（`advisor/schemes.py`）、自己方案的推荐文字（`text.py` 的位次/拿不到等）、下拉列表画法，或改这几句提示文案时。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_scheme_card`（上次约 30.3 秒）。
- **需要什么**：Qt 离屏平台；方案文件要可写（测试写进沙箱临时文件夹）；不开真实的编辑/导入导出窗口（用假窗口）。
- **输出怎么看**：同总则。有的用例把方案文件写进沙箱临时文件夹；诊断包相关的用例读 `background.snapshot()`。
- **出错时一般是哪里不对**：
  - `test_scheme_card_layout`、`test_combos_not_colored` 失败 → 卡片布局或下拉框的样式改了。
  - `test_scheme_items`、`test_unrecorded_hero` 失败 → 方案下拉框的列表、分组标题或未收录角色的处理改了。
  - `test_tooltips`、`test_markup_shown_as_text` 失败 → 悬停说明或方案名/说明的转义（网页标记要原样显示）改了。
  - `test_collapse_remembered` 失败 → 卡片收起状态的保存/恢复改了。
  - `test_advice_card`、`test_switch_recomputes`、`test_switch_without_offer`、`test_switch_with_recognition_thread` 失败 → 方案模式下的推荐文字或切换重算改了。
  - `test_editor_window`、`test_share_window`、`test_edit_current_scheme_recomputes` 失败 → 编辑、导入/导出窗口的接线改了。
  - `test_editor_new_*`、`test_dropdown_switch_marks_editor`、`test_import_use_now_later_gone` 失败 → 新建、复制以后换用（`_scheme_added`、`_overlay_hero`）、换用的公共入口（`_use_scheme`，提示 `SCHEME_SWITCHED`、`SCHEME_SET`）、「使用这套方案」的结果（`_use_requested`）或重标「使用中」（`Dialogs.show_used`）改了。
  - `AdviceBasisTest.*` 失败 → 「推荐」卡片头那段「角色 · 方案」的写法（`wording.scheme_summary`、`ADVICE_BASIS_*`）、什么时候显示（`AdviceCard.basis`）或截短（`fit_basis`、`basis_room`）改了；`test_fits_every_skin` 失败多半是哪套皮肤的卡片头变宽了。
  - `HeroBoxGroupsTest.*` 失败 → 角色下拉框的组名行（`widgets.fill_hero_combo`、`add_combo_header`）或按角色找下标的代码改了。
  - `test_scheme_file_notices`、`test_storage_fallback_notice` 失败 → 方案文件读取结果或个人目录退回的提示改了。
  - `test_warn_notes_orange`、`test_advice_text_first_line` 失败 → 方案附注的颜色或诊断包推荐全文的第一行改了。
  - `SchemeOptionTextTest.*` 失败 → 自己方案的位次/拿不到/收入降档/刷新那一行的文字改了（是有意改的就改断言的预期）。`SchemeAdviceCardTest.test_move_pills_on_card` 失败 → 同一档的「前瞻优于 / 不如 / 差不多」标签（`text.py` 的 `move_pills`）或档位前瞻的标签（`scheme_ahead`）改了。
  - `ComboRowDrawingTest.*` 失败 → 下拉列表的鼠标高亮框、当前值短条、分组标题的画法或行高改了。

## test_scheme_map_switch.py：对局里按地图自动换筹码方案（悬浮窗这一层）

- **测试观点**：从三个测试类检查——① `OverlayMapSchemeTest`：三选一开着时认出地图，按换成的方案立即重算（推荐日志记在同一次三选一上），出一次性提示 `MAP_SCHEME_SWITCHED`，方案下拉框和卡片头摘要都是换成的那套，保存的方案不变（不调 `SchemeStore.set_current`），识别记录记一条 scheme（via 为 map，带地图名）；只提示一次，之后刷新不再提示；快照里有「本局按地图换用了……」那一行要的几项，诊断包里有实际用的、保存的、按哪张地图换的。地图从三选一里的地图专属筹码认出来时，那一帧就按换成的方案算。换着的时候点「换回原来的」（`revert_map_scheme`）换回保存的那套、立即重算、出 `MAP_SCHEME_REVERTED`、不改方案文件；没有三选一时照样出提示；再点一次什么都不做。在方案下拉框里选回保存的那套等于点了「换回原来的」；选另一套记下这个角色用它、这一局不再按地图换；编辑窗口新建、复制和导入后「使用这套方案」同样不再按地图换。本局结束回到保存的方案、不出提示、不记识别记录，新的一局认出地图以后照常再换、再提示。地图交回自动识别时回到保存的方案。② `SchemeMapNoticeTest`：「筹码方案」卡片里方案下拉框下面那一行「本局按地图换用了「龙宫专用」」（级别 info，在 `scheme` 这一块）、按钮「换回原来的」和它的悬停说明（保存的是默认推荐时写「默认推荐」）；点按钮调用 `revert_map_scheme`，这一行收起、出 `MAP_SCHEME_REVERTED`、这一局不再按地图换；对局结束、新的一局收起，新的一局认出地图以后照常再出现；方案名里有尖括号、`&`，或像网页标记（`<b>粗</b>`）时按纯文本显示；一行显示时「筹码方案」卡片随这一行出现，全部展开时卡片收起着、要么这一行出现、要么又换了一套，会自动展开；方案下拉框里标了适用地图的那几项，悬停说明最后一段写「适用地图：……」（按 `Scheme.maps` 的先后、用「、」隔开），通用方案不写，行上的文字照旧只有方案名。
- **检证对象**：`advisor/ui/overlay.py`（`_follow_scheme`、`revert_map_scheme`、`_use_scheme`、`_scheme_chosen`、`_scheme_added`、`_use_requested`，提示行的动作表）、`advisor/ui/overlay_parts/notices.py`（`map_switch` 那一行）、`advisor/ui/overlay_parts/layout.py`（「筹码方案」卡片的提示区、一行显示时出现与否）、`advisor/ui/overlay_parts/scheme_card.py`（`show_schemes` 的悬停说明）、`advisor/ui/overlay_parts/state.py`（`SessionView.map_switched`、`scheme_name`、`saved_scheme_name`、`take_map_switch`）、`advisor/ui/overlay_parts/wording.py`（`MAP_SCHEME_*`、`SCHEME_MAPS_TIP`）；记账那一层（`SessionMapSchemeTest`）见[对局记录](session.md)的同名一节。
- **什么时候要跑**：改了按地图换方案在悬浮窗上的部分（重算与一次性提示、那一行和「换回原来的」、方案下拉框的悬停说明），或 `Session.scheme_choice`、`Session.take_map_switch`、`Session.stop_map_scheme` 以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_scheme_map_switch`（上次约 3 秒）。
- **需要什么**：Qt 离屏平台；方案文件写在测试沙箱的临时文件夹里；不用识别样本截图，不弹窗口。
- **输出怎么看**：同总则。`SchemeMapNoticeTest` 用 `notice_model.notices` 看这一轮排出的提示行，用 `notices.buttons("map_switch")`、`visible_texts` 看按钮和文字。
- **出错时一般是哪里不对**：
  - `test_auto_switch`、`test_switch_in_same_frame_as_chips` 失败 → 认出地图以后的重算、一次性提示 `MAP_SCHEME_SWITCHED` 或识别记录（via 为 map）改了。
  - `test_revert`、`test_revert_without_offer`、`SchemeMapNoticeTest.test_revert_click` 失败 → `revert_map_scheme`（换回、重算、提示、打标记、识别记录）或那一行的按钮接线改了。
  - `test_dropdown_saved_and_same`、`test_dropdown_other_scheme`、`test_editor_and_import_stop_switch` 失败 → 在方案下拉框、编辑窗口、导入后改用时的「这一局不再按地图换」改了。
  - `test_game_over_returns_to_saved`、`SchemeMapNoticeTest.test_hides_after_game_and_new_game` 失败 → 本局结束、新的一局回到保存的方案，或那一行收起/再出现的时机改了。
  - `SchemeMapNoticeTest.test_row_and_button`、`test_saved_default_tip`、`test_special_characters_plain`、`test_markup_name_plain` 失败 → 那一行的文字、按钮、悬停说明，或方案名的转义（提示行没有链接时按纯文本画，`notices.py` 的 `NoticeView.update`）改了。
  - `test_one_line_and_collapsed` 失败 → 一行显示时卡片出现与否（`CARD_WHEN_COLLAPSED` 的 `scheme`）、卡片收起时的自动展开改了。
  - `test_dropdown_applicable_maps` 失败 → 方案下拉列表的「适用地图」一段（`SCHEME_MAPS_TIP`、`show_schemes`）改了。

## test_overlay_shutdown.py：退出时的收尾与后台识别线程的停止、请求

- **测试观点**：从三个测试类检查——① `ShutdownTest`：退出收尾把藏起悬浮窗、停定时器、关掉开着的独立窗口、停下载（等一个上限）、停识别线程（等一个上限）都做一遍；识别线程等满还没停时返回失败、错误记录里写明它当时停在哪里；下载没停下只记一笔、照样往下停识别线程；关独立窗口出错只记一笔、后面几步照做；没有识别线程、没下载过也照样能收尾。② `WatcherStopTest`：`stop` 返回停下了没有；卡在一次取帧里时等不到（返回失败、`current_stack` 看得到停在哪里），取帧回来发现要退出就不再识别这一帧；从没启动过算已停下。③ `HotkeyRequestTest`：取走热键请求的那一刻别的线程又按了一次，这一次不会被随后的清零冲掉。
- **检证对象**：`advisor/ui/overlay.py`（`shutdown`）、`advisor/ui/overlay_parts/watcher.py`（`Watcher.stop`、`current_stack`、`_take_request`、`_requested`）、`advisor/ui/overlay_parts/wording.py`（`NO_DOWNLOAD_ENV`、`POLL_MS`）、`advisor/ui/overlay_parts/app.py`（`OverlaySession`、`UiSettings`）、`advisor/ui/overlay_parts/background.py`（下载）、`advisor/ui/overlay_parts/dialogs.py` 与独立窗口；错误记录写进每个测试自己的临时文件。
- **什么时候要跑**：改了退出收尾的步骤与顺序、识别线程的停止、热键请求的取用，或改了下载停止与各上限常量时。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_shutdown`（上次约 1.6 秒）。
- **需要什么**：Qt 离屏平台；下载、识别流程、取帧对象都是假的，不截屏、不找真实游戏窗口。
- **输出怎么看**：同总则。`ShutdownTest` 把等下载停下的上限放宽（只看停没停、不看快慢）。
- **出错时一般是哪里不对**：
  - `test_stops_everything` 失败 → 退出收尾漏了某一步或顺序改了。
  - `test_stuck_watcher_reported`、`test_never_started_counts_as_stopped` 失败 → 识别线程停止的判定或错误记录文案改了。
  - `test_stuck_grab_then_skips_recognition` 失败 → 取帧卡住时的停止行为改了（应看得到停在 `grab`、取帧回来不再识别这一帧）。
  - `test_download_not_stopping_logged`、`test_window_close_error_does_not_skip_rest` 失败 → 下载或关窗口出错时的兜底改了。
  - `test_trigger_while_taking_not_lost` 失败 → 热键请求的取用与清零有竞态（取的那一刻按下的请求被冲掉）。

## test_overlay_status.py：「识别」卡片的状态胶囊与一行摘要（难度、星币、写法、缩短）

- **测试观点**：从四个测试类检查——① `DifficultyTextTest`：难度胶囊和悬停说明的写法（只看难度信息，不开窗口）——档位为 None 不显示；有难度名写难度名；分不出时写「噩梦及以上」「困难及以上」；依据按来源写；推算出的、房间读数与进度上限矛盾时的写法；不写「None」和分值。② `DifficultyDisplayTest`：本局难度的显示与位置、一行摘要里的位置、悬停说明；新一局清空；普通难度的附注。③ `SummaryLineTest`：一行摘要的写法、放不下时的缩短顺序与末尾省略；三种宽度（平时、标题栏多「补录筹码」、窗口设窄）。④ `CoinsDisplayTest`：星币数的胶囊/摘要写法、知道升星花费时的括注与悬停整句、星数待识别/已 3 星/算花费出错时的处理、末尾省略时括注整段去掉。
- **检证对象**：`advisor/ui/text.py`（`difficulty_bit`、`difficulty_tip`、`coins_bit`、`coins_tip`、`DIFFICULTY_NORMAL_TIP`）、`advisor/ui/overlay.py`（状态胶囊与摘要的组装、`_summary_width`、`shown_bits`）、`advisor/ui/overlay_parts/status.py`（`_fit_summary`/`_elide_segs`、`SUMMARY_STEPS`）、`advisor/session/difficulty.py`（`DifficultyInfo`、`DIFFICULTY_SOURCE_LABEL`）、`advisor/ui/overlay_parts/wording.py`（`MAP_SOURCE_TIP`、`RUNNING_BIT`）；用真资料库。
- **什么时候要跑**：改了难度的识别结果写法、星币与升星花费的写法、状态胶囊或一行摘要的组装与缩短规则时。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_status`（上次约 8.8 秒）。
- **需要什么**：Qt 离屏平台；用真资料库；`SummaryLineTest` 用固定字体（微软雅黑 UI 10pt）量宽度。该组用例按模块说明要在默认缩放和 1.5 倍缩放下各跑一遍。
- **输出怎么看**：同总则。缩短相关的用例失败时，断言消息里会列出每种写法和实际宽度。
- **出错时一般是哪里不对**：
  - `DifficultyTextTest.*` 失败 → 难度胶囊或悬停说明的写法改了（分不出、推算出、矛盾时的句子）。
  - `DifficultyDisplayTest.test_normal_and_hard_plus`、`test_inferred_nightmare_plus`、`test_unsplit_on_same_caps`、`test_room_against_cap` 失败 → 难度胶囊的显示条件、位置或依据文案改了。
  - `test_new_game_clears_difficulty` 失败 → 新一局没清掉难度。
  - `SummaryLineTest.test_writing_and_widths`、`test_shrink_order`、`test_wide_window_writes_everything` 失败 → 一行摘要的写法或缩短顺序改了（有意改的就改断言的宽度预期）。
  - `test_after_first_chip_select`、`test_manual_mode` 失败 → 三选一期间或手动模式的摘要写法改了。
  - `CoinsDisplayTest.test_pill_after_star`、`test_without_cost`、`test_summary` 失败 → 星币胶囊的位置、括注写法或读不到星币时的处理改了。
  - `test_tail_not_split`、`test_brief_segments` 失败 → 末尾省略时括注的分段或各段颜色改了。

## test_overlay_status_expiry.py：一次性提示什么时候收起

- **测试观点**：从五个测试类检查——① `WindowGoneTest`：「游戏窗口已最小化…」「未找到游戏窗口…」这两句在游戏窗口又看得到后收起，两种先后（识别线程抢在悬浮窗看到之前、或在看到之后才送到）都收得起来，仍最小化/仍找不到时照旧显示；悬浮窗自己说的「未找到游戏窗口，请先启动游戏。」（`NO_GAME`）和后台识别发来的（`capture.NOT_FOUND`）一字不差；要用户处理的出错提示不因查看游戏窗口而收起。`NoGameFitTest`：一行显示时这一句整句放得下（不从末尾省略，「请先启动游戏」看得见；默认皮肤、程序的正文字体）。② `RecognitionErrorTest`：识别出错的提示按固定时长（约 15 秒）自动收起、换回等待文字；一直出错时每次重新计时；后台识别线程发来的一帧子识别出错同样收起、错误记进错误记录；收到就马上画出来。③ `OperationResultExpiryTest`：操作结果的一次性提示按固定时长（约 15 秒）自动收起；要用户处理的出错提示照旧留着；到点时只有还是同一条才收起；15 秒内又来新提示时前一条的计时不清新的；同一句再来一次重新计时；移出筹码的那一条（带「撤销」）不走一次性提示，是「已有筹码」卡片里的提示行，按自己的计时（`REMOVE_UNDO_MS`）收起，前一次移出的计时到点不收后一次的。④ `OperationResultRealTimerTest`：按真实计时器（时长换小）走一遍，确认到点真的收起。
- **检证对象**：`advisor/ui/overlay.py`（`set_status`/`_flash`/`_unflash`、`set_recog_error`、`set_flash_status`、`follower.poll`）、`advisor/ui/overlay_parts/wording.py`（`RECOG_FRAME`、`RECOG_ITEM`、`MANUAL_SWITCHED`、`REMOVED_TEXT`、`BLOCKED_HOTKEY`、`NO_GAME`）、`advisor/ui/overlay_parts/watcher.py`（`recog_error` 信号）、`advisor/vision/capture.py`（`MINIMIZED`、`NOT_FOUND`）、`advisor/ui/winapi.py`（`GameProbe`）；前台状态由假的探测函数给。
- **什么时候要跑**：改了一次性提示的收起时长或计时逻辑、「最小化」「未找到窗口」（含按游戏平台找时点明设置的那一句）的收起条件、识别出错提示的计时，或改这几句文案时。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_status_expiry`（上次约 4.7 秒）。
- **需要什么**：Qt 离屏平台；识别线程用假的（不截屏）；前台状态由假探测函数给。
- **输出怎么看**：同总则。用假时钟或把时长常量打桩成极小值，不真等 15 秒；收起走的是计时器到点时调用的那一步。
- **出错时一般是哪里不对**：
  - `WindowGoneTest.*` 失败 → 「最小化」「未找到窗口」（含按游戏平台找时点明设置的那一句）的显示或收起条件改了；`test_no_game_same_as_capture` 失败 → `wording.NO_GAME` 和 `capture.NOT_FOUND` 只改了一边。
  - `NoGameFitTest` 失败 → 「未找到游戏窗口」那句改长了（或悬浮窗变窄了），一行显示时句末被省略。
  - `test_other_messages_untouched` 失败 → 查看游戏窗口时误收了要用户处理的提示。
  - `test_error_collapses_after_a_while`、`test_repeated_error_restarts_timer`、`test_default_is_fifteen_seconds` 失败 → 识别出错提示的计时或默认时长改了。
  - `test_sub_recognition_error_from_watcher_collapses` 失败 → 后台识别线程发来的识别出错提示的计时或记录改了。
  - `OperationResultExpiryTest.test_errors_stay`、`test_new_result_survives_old_timer`、`test_same_text_again_restarts_timer` 失败 → 操作结果的计时，或它与出错提示的区别改了。
  - `test_manual_switch_expires`、`test_map_expires`、`test_add_and_remove_expire`、`test_hotkey_recognition_result_expires`、`test_fix_outdated_result_expires` 失败 → 对应操作结果没挂计时，或收起后没换回等待文字；`test_add_and_remove_expire` 只在移出那一段失败 → `overlay.remove_owned`、`_expire_removal` 的计时或序号改了（移出的提示又走了 `_flash`，或前一次的计时收了后一次的）。
  - `OperationResultRealTimerTest.test_timer_expires` 失败 → 真实计时器那条路径改了。

## test_overlay_watcher.py：后台识别线程（取帧、暂停、统计、出错提示）

- **测试观点**：从六个测试类检查——① `WatcherTest`：按热键要求的那一帧识别完发 `capture_done`（画面没变、Session 不发事件时，界面靠它收起「正在识别…」）。② `WatcherCaptureTest`：后台线程用取帧对象取帧——截取状态在线程开始和每次变了时发 `capture_changed`；暂时没有画面（`NoFrame`）不当出错；取帧出错不退出、下次照常；线程结束关掉取帧对象；找不到窗口/最小化按约定两句原样显示、不算出错、不记错误记录；别的运行期错误按截取出错提示固定的中文、原文只进错误记录；窗口换了下次取帧前请取帧对象再试一次；取帧统计与识别统计按时间记录（识别统计紧跟在取帧统计后面）；等待前先请取帧对象关掉空闲的会话并按它给的秒数设上限。③ `WatcherPauseTest`：暂停时不取帧，按热键时也收起「正在识别…」；线程结束关掉取帧对象。④ `RecogErrorTextTest`：识别出错只发固定中文（不带报错原文）、原文只进错误记录；每一项各自去重（这次运行只提示一次，距这一项上一次出错满 10 分钟再提示）；同一帧几项合成一句；没有界面叫法的几项合成笼统那一句；建取帧对象出错时提示固定中文、错误记录照记。⑤ `WatcherPlatformTest`：取帧对象和快速盯绿框的取帧对象按自己的端去找窗口，不给端时照旧。⑥ `WatcherSwitchPlatformTest`：运行中换端——下一圈开始前关掉现在的取帧对象、按新的端重建，快速盯绿框的也一起换，正在识别的那一帧不受影响，和现在的值相同时不做事；两张悬浮窗共用的牌子（`select_board`）交给建好的识别流程，识别流程重建、线程结束时换下来的那一份在牌子上擦掉（`test_board_handed_to_processor`）。
- **检证对象**：`advisor/ui/overlay_parts/watcher.py`（`Watcher` 的 `run`、`trigger`、`set_paused`、`stop`、`set_platform`，以及 `capture_changed`/`status`/`capture_done`/`recog_error` 信号、`_emit_recog`/`_emit_recog_items`、`_log_grab_stats`/`_log_recog_stats`、`_idle_wait`、`_open_source`）、`advisor/ui/overlay_parts/wording.py`（`RECOG_CAPTURE`/`RECOG_FRAME`/`RECOG_ITEM`/`RECOG_ITEMS`/`RECOG_REPEAT_S`/`RECOG_STARTUP`、`recog_items_text`）、`advisor/ui/screen.py`（`FrameSource`、`NoFrame`、截取状态）、`advisor/vision/pipeline.py`（`FrameProcessor`、`platform`）、`advisor/vision/capture.py`（`WindowGone`、`NOT_FOUND`、`NOT_FOUND_ONE`、`MINIMIZED`）、`advisor/vision/window_capture.py`（`init_thread`）。
- **什么时候要跑**：改了后台识别线程的取帧、暂停、换端、截取状态上报、取帧/识别统计、识别出错提示与去重，或改这几句文案时。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_watcher`（上次约 4.2 秒）。
- **需要什么**：Qt 离屏平台；用假的取帧对象和假的识别流程，不真截图、不跑识别、不建悬浮窗。
- **输出怎么看**：同总则。等待用「处理事件直到条件成立」的方式，不按固定毫秒等。
- **出错时一般是哪里不对**：
  - `test_capture_done_without_events` 失败 → 画面没变时没发 `capture_done`（界面会一直停在「正在识别…」）。
  - `test_state_emitted_and_source_closed`、`test_no_frame_is_not_an_error`、`test_capture_error_keeps_running` 失败 → 取帧状态上报、暂时没画面的处理或取帧出错的兜底改了。
  - `test_window_gone_shown_as_is`、`test_other_runtime_error_is_capture_error` 失败 → 哪几种错原样显示、哪几种按截取出错提示改了。
  - `test_grab_stats_logged`、`test_recog_stats_logged`、`test_recog_stats_when_grab_stats_fails`、`test_recog_stats_after_pause` 失败 → 按时间记录或暂停后重新计时的逻辑改了。
  - `test_idle_wait_limit`、`test_idle_session_closed_while_waiting` 失败 → 等待前关空闲会话、设定等待上限的逻辑改了。
  - `RecogErrorTextTest.*` 失败 → 识别出错提示的写法、去重（每项各算各的、满 10 分钟再提示）或合成规则改了。
  - `test_source_init_error` 失败 → 建取帧对象出错时的提示或错误记录改了。
  - `WatcherPlatformTest`／`WatcherSwitchPlatformTest` 失败 → 取帧对象没按这一张跟的端找窗口，或运行中换端时没关旧的、没按新的重建、快速盯绿框的取帧对象没跟着换（`watcher.py` 的 `_open_source`、`set_platform`）。

## test_lanes.py：两张悬浮窗的接线（`advisor/ui/overlay_parts/lanes.py`）

- **测试观点**：
  - `StartupTest`：不是同时识别时只有一张、识别线程按设置找窗口；设置是「Steam端+TapTap端」时建两张，第一张的识别线程固定 Steam、第二张固定 TapTap；第二张用 `LaneSettings`、不交解锁记录（`records.track` 为假）、提示行只留本局（`notice_model.lane_only`）、Dialogs 和更新是第一张那份；两张的本局状态各一份。启动时两张各起一次跟随计时器，不多起。这里传的 `Services()` 没有推荐日志模块，所以第二张的 `services.logbook` 为 None。两张的识别线程共用同一块「谁的三选一正显示着」的牌子（`Lanes.select_board`，`vision.pipeline.SelectBoard`），运行中加上的第二张也是同一块。文字识别线程数的自检看第一张的识别线程和前台状态，对局看现在各张的 Session，第二张收掉以后只看第一张（`test_ocr_check_sees_every_lane`）。
  - `LaneLogbookTest`：第二张接 TapTap 那条推荐日志线（`Lanes._lane_services`）：`services.logbook` 是 `lane("taptap")` 返回的对象，第一张的还是模块本身，`diagnostics`、`assets`、`version` 两张是同一个对象；没有 `lane`、取线抛异常时第二张为 None，抛异常时记一条错误记录；第二张出三选一记在假线上、第一张的模块没收到，选的不是首推时问「未选择首推的原因」，点原因记在假线的 `record_feedback` 上；两张都在问原因时第二张点「×」只收起第二张的，第一张照旧问、点原因记在第一张的模块上（`test_dismiss_per_window`）；第二张拿不到校准计数（`game_count_since_calibration` 为 None）。
  - `ClientTest`：推荐日志里记的 `client`（对局报告用）每张按自己跟的端取：两开时第一张取 Steam 那一路上一次找到的标题、第二张取 TapTap 那一路的，哪一路最后找过窗口都不串；只开一个客户端时用上一次找到的，还没找到过时不传。
  - `SwitchTest`：运行中加、收第二张——第一张不重建，第二张的识别线程停下，改回别的设置后第一张按设置找窗口；收第二张时把它那四项存进 `lanes[taptap]`。
  - `HotkeyTest`：按热键先叫游戏窗口在前台的那一张，它识别完（`capture_done`）再叫另一张。截取屏幕方式、另一张不在前台时不叫它、在它的悬浮窗上写 `SCREEN_BEHIND`；直接截取时被盖住的另一张照样识别。先叫的那张没开始识别时直接叫另一张；5 秒没识别完也照常叫另一张，超时回调在界面线程跑；上一次按热键留下的超时不再叫。第一张正在换热键时不叫另一张。热键登记按两张的愿望合并：任一张要就登记，第一张还要时第二张不要也不注销。
  - `TrayTest`：第二张的 × 只藏它自己；托盘「显示」把第二张一起显示；第二张 × 的悬停说明是 `LANE_CLOSE_TIP`、长句照样分行；第二张的「设置」「导入/导出」「编辑方案」接到第一张那份窗口（第二张自己那份三个都没建），「编辑方案」用第二张当前的角色。
  - `SettingsTest`：第二张的 `set_manual`、`set_lowend`、`set_opaque`、`set_platform`、`set_ui_lang` 转给第一张并存进共用设置，不丢别的项（界面语言另抄给第二张），第二张的 `restart_app` 也转给第一张、只起一次新进程；第二张不写 `platform_seen`、皮肤一项写的是共用设置里那一项。第一张改了低配、手动模式以后套到第二张的窗口和识别线程，第二张正显示等待提示时按新模式重写。热键文字和能不能用跟着第一张；第二张也按第一张显示「下载挡着识别」。下载挡着时两张一起暂停、下载完两张一起重建识别流程，这期间按热键不叫另一张；下载挡着时加上第二张，它一开始就暂停。
  - `SaveTest`：锁被后台识别占着时 `save_secondary` 限时放弃、记错误记录；收第二张时存设置也有上限，不一直等。
  - `SchemesTest`：一张换了方案，另一张当前角色相同的当场重算；第二张换方案也会让第一张重算；角色不同的不动。编辑窗口新建、复制了第二张当前角色的方案、导入后对第二张的角色点「使用这套方案」，都由第二张当场换用。
  - `HubTest`：第二张的「作业站」按它自己的角色打开。
  - `DiagnosticTest`：有第二张时诊断包的 `state.json` 带 `lanes` 段（`taptap` 下是第二张的悬浮窗状态和对局快照），另交给诊断包的 `lanes` 里只有 `taptap`、带第二张最近的画面和读识别记录的函数；只有一张时 `lanes` 不传、也没有这个键；从第二张点「导出诊断」转给第一张、结果和从第一张点一样。走真的 `Background.export_diagnostics`（诊断模块是假的）：第二张的画面和本局、上一局的识别记录交给诊断包的 `lanes`，导出范围写进 `lanes.taptap.export_scope`，第一张自己的照旧；另一张的统计记录同样交给 `lanes`、范围写进 `lanes.taptap.stats_scope`；第二张还没有画面时 `frame` 为 None、照常导出；取第二张的画面、识别记录时出错时记错误记录、不带 `lanes` 照常导出；只有一张时的调用方式和原来一样（`diagnostics.export` 不认 `lanes` 也照样导出）。另用真的诊断模块写盘：诊断包里有 `taptap/frame.png`、`taptap/events.jsonl`、`taptap/stats.jsonl`，`state.json` 的 `lanes.taptap.export_scope`、`stats_scope` 对。推荐日志的取法：另一张的 `since` 更早时用另一张的，第一张更早时用第一张的，另一张是 None 或读不了时不影响，只有一张时和原来一样（`Background._earliest_scope` 交给 `records_between` 的 start）；只有一张、`since` 读不了时照样原样交给 `records_between`；另一张的 `since` 换不成本地时间（年份超出范围）时不算它、诊断包照常导出；按时刻比、不按字面比，带时区和不带时区的混在一起也能比。取出来以后每条只留它那一张自己的导出范围以内的：另一张开始得早时，这段时间里第一张更早的记录不带进来，和起点同一秒的不漏（`test_logbook_each_lane_keeps_its_own_range`）；`Background._lane_starts` 只有一张时为空表（不筛）、读不了的起点不放进来，`_in_own_range` 对起点不知道的线、时刻读不了、不是字典、`"lane"` 不是文字的记录一律留着（`test_lane_starts_and_own_range`）。
  - `ShutdownTest`：退出时收两张、存第二张；两张都停不下来时总共只等一个期限（不是一张等完再等另一张），各记一条错误记录；第二张先收（第一张还要等下载、更新停下）；先限时叫停文字识别线程数的自检、再停识别线程，限时内没停下算没停下（`test_ocr_check_stopped_before_watchers`）。
  - `DualTestTest`：开测时两张的识别线程都暂停，测完恢复并回调一次；取消立刻恢复；取消后马上重测，旧的一次结束时只清理、不解开新一次的暂停。
  - `GamelogFolderTest`、`RecordFolderTest`：`taptap` 子文件夹第一次用时建出来，不参与顶层识别记录的删旧；子文件夹写不进时记一次错误、照样加上第二张。
  - `LaneTagTest`：只有一张时标题栏的端标签藏着，`_summary_width()` 和没有标签时一样；同时识别时第一张写「Steam」、第二张写「TapTap」，悬停说明写 `LANE_TAG_TIP`；改回只选一个端时第一张的标签去掉、摘要宽度恢复，再改回同时识别时又出现；启动时设置就是同时识别时也有。
  - `TwoLaneLayerTest`：没找到游戏窗口的那张看另一张（`window_plan` 的 `companion`）：第一张的游戏在前台时第二张置顶、识别仍暂停、热键没因它登记，反过来也一样；第一张切到后台或最小化时第二张退回不置顶并放到前台窗口的正下方；第一张的游戏也关了时第二张不置顶也不放到前台窗口下面；从托盘「显示」回来（`reset_layer`）不放；这张自己的游戏最小化时（不是找不到游戏窗口）也不放；只有一张时永远不置顶。`step_behind_foreground` 的几支（前台是普通窗口时放，是拾星助手自己、置顶窗口、`Progman`/`WorkerW`、没有前台窗口时不动）单独核。同时在玩另一个游戏时（这张 `background`、另一张 `game`/`own`）这张照样置顶、被另一个游戏的窗口盖住也不退到后面（截取屏幕时暂停也置顶）；另一张也在后台时照旧连续两次被盖住就退。`_probe` 交给 `probe_game` 的 `ignore` 是另一张的游戏窗口句柄（另一张找不到游戏窗口、没有另一张时为 None）。`restack` 把游戏在前台的那张排到上面并记 `layer_order`，已经排在下面、不是都置顶、两张都不是 `game`、只有一张、`native` 为假时不动；第二张出三选一跳到最前面、从托盘「显示」、切换游戏以后都再排一次。设置窗口这类独立窗口开着时点回游戏，排完以后它们照旧压在悬浮窗上面；另一张在三选一（`front`）时不放回（`test_restack_keeps_open_windows_above`、`test_restack_other_live_leaves_windows`）。玩着 TapTap 那边时从托盘「显示」，独立窗口也压在两张悬浮窗上面（`test_tray_show_puts_open_windows_above_both`）；第二张的托盘和跟随部件接的是共用的那一份独立窗口，第二张调 `relayer` 照第一张的层次设、不看自己的层次（`test_second_relayer_uses_shared_stack`）；第二张的层次变了不打断第一张关窗口时临时置顶的那一轮（`test_second_keeps_primary_settle_round`）；收掉的第二张不再把第一张当成另一张（`test_removed_second_has_no_companion`）。
  - `TwoLanePlacementTest`：两个游戏窗口在同一处时，第二张没拖动过就建在第一张旁边、不相交；第一张被拖到第二张身上，第二张下一次 `poll` 挪开；第二张拖动过就放在拖到的地方，叠着也不动；两个游戏窗口分开摆时第二张和只有一张时算出来的一样；三选一时第二张让开第一张、第一张在第二张拖动过时避开它、第二张没拖动过时第一张不看它；找不到游戏窗口时第二张也不和第一张叠在一起；只有一张时位置不变。
- **检证对象**：`advisor/ui/overlay_parts/lanes.py` 的 `Lanes`（`update_lane_tags`、`_probe`、`_other_game`、`restack`、`diagnostic_lanes`、`diagnostic_sources`）、`Lane`、`LaneHotkey`、`SCREEN_BEHIND`、`LANE_TAG_TIP`、`PENDING_WAIT_S`；`advisor/ui/overlay.py` 的 `set_lane_tag`、`_companion`、`_companion_activity`、`_companion_rect`、`_companion_fixed`、`_restack`；`advisor/ui/overlay_parts/geometry.py` 的 `window_plan`（`companion`）、`WindowPlan`、`live_placement`（`avoid`）、`overlay_position`（`avoid`）、`beside`（含 `prefer`）、`pair_position`、`AVOID_WEIGHT`；`advisor/ui/overlay_parts/follow.py` 的 `WindowFollower.update_plan`、`apply_layer`、`reset_layer`、`target_position`、`place`、`poll`、`companion_activity`、`companion_rect`、`companion_fixed`、`yields`、`restack`；`advisor/ui/winapi.py` 的 `step_behind_foreground`、`DESKTOP_CLASSES`、`probe_game`（`ignore`）、`below`；`advisor/vision/capture.py` 的 `PLATFORM_TAGS`、`PLATFORM_NAMES`；`advisor/ui/overlay.py` 的 `Overlay`（`primary`、`_attach_primary`、`_open_editor_lane`、`_use_scheme`、`_scheme_added`、`_use_requested`、`_schemes_changed`、`start_dual_test`、`cancel_dual_test`、`wait_dual`、`_set_dual_paused`、`show_hotkey_skipped`、`export_diagnostics`、`_assets_blocked`、`_companion_watchers`、`_reset_partner`、`_reset_one`、`shutdown`）；`advisor/ui/overlay_parts/app.py` 的 `main`、`OverlaySession`、`BOTH`、`LANE_KEYS`；`follow.py` 的 `Tray.show_more`、`Tray.fallback`、`Tray.show_from_tray`、`WindowStack`（`relayer`、`open_windows`、`mark_layer`、`cancel_settle`）、`LANE_CLOSE_TIP`；`notices.py` 的 `NoticeModel(lane_only=)`；`records.py` 的 `Eggs(track=)`；`background.py` 的 `Background(also_pause=)`、`_set_blocked`、`_earliest_scope`、`_lane_starts`、`_logbook_extra`、`_in_own_range`、`stats_reader`、`_read_stats`；`advice_card.py` 的 `question_state`；`dialogs.py` 的 `open_editor(hero=)`；`watcher.py` 的 `request_stop`；`advisor/gamelog.py` 的 `GameRecorder`；`advisor/schemes.py` 的 `SchemeStore`。
- **什么时候要跑**：改了 `overlay_parts/lanes.py`、`Overlay` 里的两张接线（转给第一张、方案同步、性能测试暂停、退出）、`main()` 里两张的建法、第二张的关闭/托盘/提示行/解锁/推荐日志处理、诊断包里推荐日志的取法（`background.py` 的 `_earliest_scope`、`_lane_starts`、`_in_own_range`），或识别记录的 `taptap` 子文件夹，都要跑。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_lanes`（上次没有记录）。
- **需要什么**：Qt 离屏平台，不弹窗；识别线程、热键线程、取帧都换成假的；设置文件写在测试沙箱的临时文件夹里。
- **输出怎么看**：同总则；错误记录用 `capture_errors` 收，结束时核对。
- **出错时一般是哪里不对**：
  - `StartupTest`／`SwitchTest` 失败 → 两张的建法、跟的端、第二张的设置类、或增删第二张的处理改坏（`lanes.py` 的 `wire_primary`、`add_secondary`、`remove_secondary`、`set_platform`）。
  - `HotkeyTest` 失败 → 先叫哪张、5 秒保险、几种不叫的情形或热键登记的合并改坏（`on_hotkey`、`_set_pending`、`_fire_pending`、`set_hotkey_enabled`）；`SCREEN_BEHIND` 的提示没出现时看 `Overlay.show_hotkey_skipped`。
  - `TrayTest` 失败 → 第二张 × 的悬停说明或只藏自己、托盘显示两张、三个按钮接到第一张或编辑窗口的角色改坏（`_attach_primary`、`_open_editor_lane`、`LANE_CLOSE_TIP`）。
  - `SettingsTest` 失败 → 设置转给第一张、同步到第二张、热键状态、下载挡着时两张一起暂停/重建改坏（`sync_from_primary`、`sync_hotkey_state`、`Background.also_pause`）。
  - `SaveTest` 失败 → 限时拿锁存第二张的处理改坏（`_save_lane`、`save_secondary`）。收第二张那一条一直不结束 → 收完设端标签时等锁去了（`set_lane_tag`）。
  - `SchemesTest`／`HubTest` 失败 → 方案同步（只叫另一张、同角色才动）或第二张的作业站角色改坏（`scheme_changed`、`lane_for_hero`、`open_hub`）。
  - `DiagnosticTest` 失败 → `lanes` 段的内容、只有一张时没有这个键、`Lanes.diagnostic_sources` 取的第二张画面和识别记录没传给 `Background.export_diagnostics` 的 `lanes`、第二张导出没转给第一张，或 `taptap/` 子文件夹没写出来（`diagnostic_lanes`、`diagnostic_sources`、`export_diagnostics`）；推荐日志带多了、带少了 → 取起点或每条筛的那一步改坏（`Background._earliest_scope`、`_lane_starts`、`_in_own_range`）。
  - `ShutdownTest` 失败 → 两张一起收尾、共用一个期限、第二张先收、限时存设置、先叫停文字识别线程数的自检改坏（`shutdown`、`save_secondary`）。
  - `DualTestTest` 失败 → 性能测试暂停两张、恢复、取消后迟到结果的清理改坏（`start_dual_test`、`cancel_dual_test`、`_set_dual_paused`）。
  - `GamelogFolderTest`／`RecordFolderTest` 失败 → `taptap` 子文件夹的建法、删旧或写不进时的处理改坏（`gamelog.GameRecorder`、`add_secondary`）。
  - `LaneTagTest` 失败 → 增减第二张时没设或没去掉端标签、端名取的不是 `PLATFORM_TAGS`（`update_lane_tags`、`set_lane_tag`）。
  - `TwoLaneLayerTest` 失败 → `window_plan` 的 `nogame` 那一支没看 `companion`、另一张的状态没接上（`_companion_activity`），或退回不置顶时没按条件放到前台窗口下面、`step_behind_foreground` 的判断改坏（`apply_layer`、`step_behind_foreground`）；`background` 那一支没看 `companion`（玩另一个游戏时这张退到了后面）、`_probe` 没把另一张的游戏窗口交给 `ignore`（`_other_game`），或 `restack` 没把前台那张排上去、排完没把独立窗口放回上面（`window_plan`、`_other_game`、`restack`）；托盘「显示」以后独立窗口在第二张下面、第二张调 `relayer` 没设到共用的那一份 → `Overlay` 建 `WindowFollower`、`Tray` 时把 `self.windows` 的方法直接传了进去（要用到时再取，`_attach_primary` 后来才换成第一张的）；第一张关窗口时临时置顶的那一轮被打断 → 第二张的 `cancel_settle` 接到了共用的那一份；收掉的第二张还有另一张 → `_companion` 少了不到两张时为 None 那一条。
  - `TwoLanePlacementTest` 失败 → 第二张没让开第一张、拖动过以后还在挪，或第一张该不该避开第二张的条件改坏（`WindowFollower.target_position` 的 `beside` 和 `avoid`、`place`、`poll`，`Overlay._companion_rect`、`_companion_fixed`）。

## test_overlay_window.py：悬浮窗整体（卡片、补录、地图、热键、设置窗口、一行/展开）

- **测试观点**：从四个测试类检查——① `OverlayWidgetTest`：「已有筹码」卡片（图标格子、超过 10 个时翻页、筹码小卡片、字号、效果文字换行、移出、越往下延伸再滚动）、移出以后的「撤销」（卡片里一条带「撤销」的提示行，点了按原来的位置和来源放回、提示「已撤销移出……」、识别记录记一条；到点或已有筹码有了别的变化时连同按钮收起，点的那一下赶上了变化时不放回、照实说）、热键被占用和写法无效时标题栏下方带「更换热键」「关闭本条提示」的提示行（点「更换热键」打开设置窗口、直接进入等按新热键；热键又能用了收起；关掉以后同一句不再出、别的问题照常出）、窗口位置、补录行与改正栏、状态胶囊的主体写法（刷新次数没读出来时状态胶囊、一行显示的摘要、「识别」卡片收起时的摘要写橙色的「刷新次数 待识别」，悬停说明写按几次估计、不按多出来的刷新算；本次之后预计没有筹码三选一时写剩余刷新可以连续使用）、地图可靠时只显示名称与请选地图、热键的设置（更换过程与结果、占用、长热键、小键盘数字）、按热键识别的提示、所有按钮都有框线、各部分放得下不被截掉、界面文字不含口语字样。② `OverlayModeTest`：平时一行、三选一时显示推荐卡片、关闭后收回一行、全部展开回看上一次推荐；理由标签短句加悬停整句；三选一显示后不显示就画不被截掉；对局中摘要的写法；暂停与直接截取两种情况的摘要和状态胶囊；识别功能暂不可用时按热键不识别。③ `DevLabelTest`：开发版与发行版标题栏的标题不同、一行显示时不加、托盘提示不含。④ `SettingsTest`：「设置」窗口里识别画面一行只在截取屏幕时有（直接截取时没有、截取方式变了当场加上或去掉）、它的状态标签与说明、找到窗口前的预判、窗口里没有「关闭」按钮、旧的「允许截图」设置字段读时忽略并在下次保存时去掉。
- **检证对象**：`advisor/ui/overlay.py`（大量：已有筹码卡片、改正栏、地图选择行、热键相关槽、`set_expanded`/`toggle_expanded`、`set_manual`、`fit_height`/`_layout_height`、`visible_texts`、`diagnostic_state` 等）、`advisor/ui/overlay_parts/*`（`owned_card`、`factory`、`notices`、`layout`、`follow`、`app` 等）、`advisor/ui/settings/dialog.py`（`SettingsDialog`、`SETTINGS_TITLE`、`settings_texts`）、`advisor/ui/settings`（`SETTINGS_METHOD`、各 `SETTINGS_*` 文案）、`advisor/ui/hotkey.py`（`startup_busy_text`、`busy_text`）、`advisor/ui/widgets.py`（`Pill`、`TaggedText`、`ChipGrid`）、`advisor/ui/text.py`（`keep_words`、`tip_html`、`option_views`、`breakdown_rows`）、`advisor/ui/overlay_parts/app.py`（`UiSettings`）、`advisor/ui/overlay_parts/factory.py`（`WrapTaggedText`）；推荐日志、识别线程、热键线程、诊断模块都用假对象。
- **什么时候要跑**：改了悬浮窗的卡片布局、已有筹码卡片、补录/改正、地图选择、热键与设置窗口、一行/全部展开两种显示、开发版标题，或改这几句文案与卡片文字换行处理时。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_window`（上次约 11.1 秒）。
- **需要什么**：Qt 离屏平台；临时文件写进测试沙箱；热键线程、识别线程、诊断模块用假对象（不真登记热键、不真截图）。
- **输出怎么看**：同总则。设置窗口的用例把最大高度打桩放宽以按内容量高度；有的用例用真实临时目录（在沙箱里）。
- **出错时一般是哪里不对**：
  - `test_owned_card_lists_chips`、`test_owned_mini_card_font_sizes`、`test_owned_mini_card_keeps_words` 失败 → 已有筹码卡片的排版、字号或效果文字换行改了。
  - `test_owned_card_more_than_ten` 失败 → 持有超过 10 个筹码时卡片头的个数（只写「12 个」、不写上限）、筹码小卡片或图标格子（第一页画前 10 个、翻到第二页画剩下的）的处理改了。
  - `test_owned_cards_extend_downward_then_scroll` 失败 → 卡片往下延伸、超屏改滚动的高度计算改了。
  - `test_remove_owned_chip`、`test_missed_pick_prompt`、`test_fix_row_closes_when_offer_changes`、`test_fix_confirm_checks_offer_is_unchanged` 失败 → 移出、补录行、改正栏的相关逻辑改了。
  - `test_undo_remove_owned_chip`、`test_undo_remove_expires_or_goes_stale` 失败 → 移出的提示行（`notices.NoticeModel.removed`）、「撤销」（`overlay.undo_remove`、`Session.restore_chip`）或它什么时候收起（`REMOVE_UNDO_MS`、`SessionView.can_restore`）改了。
  - `test_hotkey_change_button` 和热键被占用那几条失败 → 热键不可用的提示行（`notices` 的 hotkey 一行、`hotkeys.problem`）、「更换热键」（`overlay.change_hotkey`）或 `hotkey.startup_busy_text` 的写法改了。
  - `test_status_pills_name_subjects`、`test_advice_line_shows_source`、`test_unreadable_candidates` 失败 → 状态胶囊的主体写法或候选筹码列表改了。`test_unread_rerolls_bit`、`test_unread_rerolls_bit_no_later_picks` 失败 → 刷新次数没读出来时那一项的写法（`advisor/ui/overlay_parts/advice_card.py` 的 `bits`）或它在悬停说明里的那一行（`advisor/ui/text.py` 的 `Bit.tip`、`UNREAD_COST_TIP`，本次之后预计没有筹码三选一时的 `UNREAD_FREE_TIP`，`advisor/ui/overlay_parts/status.py` 的 `status_tip` 的 `live_tips`）改了。
  - `test_reliable_map_shows_name_only`、`test_map_ask_offers_candidate_buttons`、`test_map_unknown_asks_to_open_task_panel` 失败 → 地图可靠时只显示名称、请选地图或打开任务栏的提示改了。
  - `test_hotkey_*` 失败 → 热键的更换流程、说明文案或占用处理改了。
  - `test_all_rows_fit_before_show`、`test_reasons_fit_in_window`、`test_live_layout_not_clipped` 失败 → 某一块被截掉（高度或换行计算改了）。
  - `OverlayModeTest.*` 失败 → 一行/全部展开的切换、回看上一次推荐或摘要写法改了。
  - `DevLabelTest.*` 失败 → 开发版/发行版标题或一行显示时的标题栏改了。
  - `SettingsTest.*` 失败 → 设置窗口里识别画面一行的显示（什么时候有）、文案或旧设置字段的处理改了。

## test_overlay_advice.py：推荐卡片上的理由、「刷新」那一行和同系列上位替代

- **测试观点**：
  - 改正识别有误的候选筹码后，同一个筹码三选一画面（下称三选一）之后的每一帧都按改正后的名字算推荐、记录选择也用改后的名字；三选一关闭或刷新（剩余次数变了）后改正作废（`FixCandidateTest`）。
  - 每个选项（三个候选筹码加「刷新」）的全部理由各做成一个理由标签，加分绿、扣分橙、只作说明的灰；筹码标签与角色职业相符时单列「标签：X ✓」，不占别的理由名额（`ReasonDisplayTest`）。
  - 标签按分值的绝对值从大到小排（扣 40 排在加 20 前面），绝对值相同时按原来的顺序；前瞻和理由标签同一个门槛（绝对值不到门槛就不出标签）（`ScoreFreeDisplayTest`）。
  - 标签上是短句、悬停说明是整句，两处都不写分值、不写总分；「刷新」那一行是「预计优于/不如/与X相当」、概率百分比、代价三段；代价那一段在之后还有三选一、只是很少（代价不到 0.5、刷新没有多出来）时写「之后几乎无需保留刷新」，悬停写之后的三选一「预计不到 1 次」，不写「本次之后预计不会再有」（`ScoreFreeDisplayTest.test_reroll_cost`）；代价不到 0.5 就一定不到 1 次，按资料的刷新代价系数和一局最多的刷新次数核对，另拿 3 星最后能打的一轮、刷新次数没读出来的局面核对（`test_low_cost_under_one_pick`）。
  - 「刷新」的结论（算上消耗 1 次刷新）和概率（不计消耗）方向相反时，或结论「相当」而概率很偏时，不显示概率那一段，只留结论和代价（`RerollConflictTest`）。
  - 本次之后预计没有三选一（连续刷新）时，比较和概率的悬停说明写明「最多连续刷新 N 次」的前提（`RerollChainPillsTest`）。
  - 同系列里被严格上位替代的低一级写暗色的「被X上位替代」、不写推荐度、排在刷新之后；另一张写「比X多……」的对比行（`SeriesDisplayTest`）。
  - 因为队友加分的理由：队友刚需那一条排在最前、不占名额；前几条里没有为正的队友分项时另加其中分值最大的一条；筹码名按钮的悬停明细里写出本局队友（`TeammatePillTest`、`OptionTooltipTeammatesTest`）。

- **检证对象**：`advisor/ui/text.py` 的 `reason_pills`、`option_pills`、`reason_lines`、`part_pill`、`tag_pill`、`lookahead_pill`、`reroll_pills`、`reroll_conflict`、`reroll_chance_hidden`、`reroll_info`、`reroll_reason`、`best_chip`、`breakdown_rows`、`breakdown_html`、`option_views`、`advice_text`、`advice_summary`、`rival_word`；`advisor/ui/overlay_parts/app.py` 的 `OverlaySession`（`fix_candidate`、`_apply_fixes`）；`advisor/ui/overlay_parts/advice_card.py` 的 `AdviceCard.name_tooltip`；`advisor/engine/`（`advise`、`Part`、`ChipEval`、`REROLL`）。

- **什么时候要跑**：改了 `advisor/ui/text.py` 里推荐卡片用的文字（理由标签的短句、悬停明细的整句、推荐度、刷新那一行）或它们的顺序、门槛；改了 `advisor/ui/overlay_parts/app.py` 的 `OverlaySession` 改正逻辑；改了 `advisor/ui/overlay_parts/advice_card.py` 里筹码名按钮的悬停明细；改了 `advisor/engine/` 里理由短句（`advice_text`）或刷新比较的判定以后。

- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_advice`（上次约 10 秒）。

- **需要什么**：没有特别要求。不用识别样本截图：用真的资料库和推荐引擎算，画面数据是构造出来的；个别用例直接构造评估结果。用离屏的 Qt，不会弹出窗口。

- **输出怎么看**：同总则。不用识别样本，没有因样本缺失而跳过的用例。

- **出错时一般是哪里不对**：
  - `ReasonDisplayTest`、`ScoreFreeDisplayTest` 失败 → 多半是理由标签的短句、色调、顺序，或「标签不占名额」「不写分值」被改坏了（去看 `advisor/ui/text.py` 的 `reason_pills`、`part_pill`）。
  - `RerollConflictTest`、`RerollChainPillsTest` 失败 → 多半是「刷新」那一行方向相反时隐藏概率的判断、连续刷新的前提写法改坏了（看 `text.py` 的 `reroll_conflict`、`reroll_chance_hidden`、`reroll_pills`）。
  - `SeriesDisplayTest` 失败 → 多半是同系列上位替代的写法、颜色词的括注或排序改坏了（看 `text.py` 的 `rival_word`、`option_views`）。
  - `TeammatePillTest`、`OptionTooltipTeammatesTest` 失败 → 多半是队友刚需那一条的排法，或悬停明细里队友那一行改坏了（看 `text.py` 的 `option_pills`、`advice_card.py` 的 `name_tooltip`）。
  - `FixCandidateTest` 失败 → 多半是候选改正的生效范围、刷新后作废、重复提示的处理改坏了（看 `overlay_parts/app.py` 的 `OverlaySession`）。
  - `LookaheadSameRateTest` 失败 → 多半是前瞻说明里又出现了取整后前后相同的出现率（「7% → 7%」）。
  - 如果有意改了界面文案（标签短句、悬停整句、被替代的写法），要一并改这里的预期值：断言里写的就是那些文字。

## test_overlay_background.py：后台任务的界面（导出诊断、下载识别图片、托盘、关于、推荐日志与识别记录的转接）

- **测试观点**：
  - 导出诊断：设置窗口里点「导出诊断」，写诊断包期间按钮不可点、写完恢复，结果写在悬浮窗的提示里；没有推荐日志模块时不带额外记录；没有诊断模块时提示不可用（`OverlayServicesTest`）。
  - 导出诊断带的原图取后台识别最近的一帧：直接截取下不另截、不带原图；截取屏幕下现截一张，并记下截取方式、出错原因和步骤。
  - 后台识别留着最近一次三选一有筹码没读出来时的那一帧（悬浮窗的 `unread_source` 现取）时，「导出诊断」把它交给诊断包另存（`select_unreadable`）；还没有、取的时候出错、没接上时不带，照常导出；撤销误判时自动保存的那一份不带（`test_export_carries_unread_frame`）。记下选中项用的那一帧、被临时盖住而没有采用的那一帧、对局中途像一局打完的那一帧（`pick_source`、`covered_source`、`finish_source`，交给诊断包的 `select_picked`、`select_covered`、`finish_look`）同样（`test_export_carries_select_shots`）。
  - 识别记录：有记录器时先写盘再取本局和上一局两段，原样交给诊断包；没有记录器时退回最近的识别事件，玩家名字换成代号。
  - 统计记录：读完识别记录以后另读最近 12 小时的统计（`stats_reader`，没有记录器时交回空表和 `source` 为 `none` 的说明），经 `extra` 写成 `stats.jsonl`，范围写进 `state.json` 的 `stats_scope`；读不出来时记错误记录、不带 `stats.jsonl`、说明里写 error，诊断包照常导出；撤销误判时自动保存的那一份不读、不带（`test_export_stats_reader_records_scope`、`test_export_stats_error_keeps_package`、`test_undo_does_not_carry_stats`）。
  - 另一张悬浮窗的画面、识别记录和统计（`lanes`）：在后台那一步里读识别记录、统计，原样交给诊断包的 `lanes`，导出范围写进 `lanes.<端名>.export_scope`、`stats_scope`，第一张的识别记录、推荐日志和导出范围照旧；读不出来时记错误记录、诊断包照常导出（这一端的识别记录为空、`export_scope`、`stats_scope` 带 error），`read`、`stats` 为 None 时当成空的；不给或空时调用方式和原来一样（`test_export_lanes_read_in_background`、`test_export_lane_stats_read_in_background`、`test_export_lane_read_error_keeps_package`、`test_export_without_lanes_unchanged`）。`events_reader`、`current_frame` 在界面线程里当场取好识别记录器和界面处理过的事件，之后换记录器、再往界面的事件里加都不影响；有画面时返回它，没接上、取的时候出错时返回 None（`test_events_reader_and_current_frame`）。
  - 撤销误判的新一局时自动存一份诊断包（不含截图），记下误判时的状态和识别记录。
  - 首次启动下载识别所需的图片：要下的多（多于 `DOWNLOAD_PAUSE_OVER` 张）时下载期间暂停识别并标出，只补两张时不暂停（个别图片下不了的提示和隔天再试见 `test_download_fail.py`）、失败可重试、下载完成整个重画（筹码图标和开着的编辑方案窗口的行图标一起重做）；下完把这一轮的用时记进识别记录（`download_stats`，带走下载模块填的各项，另加这一轮的模式和是不是点「重试」开始的），没有识别记录器时不写、也不出错（`test_download_logs_stats`）。
  - 手动导出另打压缩包：诊断包文件夹旁边同名的 .zip，提示写压缩包的位置、「打开文件夹」选中它；压缩包没打成时文件夹照旧、提示写文件夹并照实说「压缩包没有打成」、原因进错误记录；撤销误判时自动保存的不打（`test_export_zip`）。
  - 一行显示时「已选」和问原因那一块 20 秒不点就收起、这一次不记；全部展开时照旧显示、能点（`test_question_line_collapses`）。
  - 点问句右边的「×」：一行显示时「已选」和问原因这一块马上收起、卡片回到一行，过了 20 秒再全部展开也不再问（只写「已选：X」，和到点收起的不同），全部展开时问原因这一块不见、「已选：X」照旧；推荐日志没有这一次的 feedback，识别记录里有一条 `dismiss_feedback`；点了以后 `submit_feedback` 也不记，下一次三选一又没选首推时照常再问；已记录原因以后「×」不显示（`test_dismiss_question_line`、`test_dismiss_expanded_keeps_picked`、`test_dismiss_stops_later_feedback_and_new_pick_asks_again`、`test_dismiss_hidden_after_recorded`）。
  - 点第四个原因「想试别的玩法」：记进推荐日志，这一行换成「已记录：想试别的玩法」（`test_feedback_try_other_school`）。界面和推荐日志的原因一字不差（`test_feedback_reasons_match_logbook`）；它的悬停说明写不算推荐不准，其余原因照同一句填原因，「×」写不选原因（`test_reason_tips`）。
  - 关于窗口显示版本和资料出处；托盘：点关闭收到托盘、只提示一次（自己画的小提示框，一次都不调用会放提示音的 `showMessage`；约 4 秒后、点一下都会藏起来；颜色跟皮肤的悬停说明；位置贴着托盘图标、拿不到时放在主屏幕右下角），从托盘能显示回来；托盘菜单的各项和先后（作业站总开关开着时），「设置」「导出诊断」先把收在托盘里的悬浮窗显示出来；悬浮窗打开使用说明（`open_manual`）用系统默认的程序打开、文件不在或打不开时在悬浮窗上照实提示（`test_close_to_tray`、`test_toast_follows_skin_colors`、`test_toast_position`、`test_tray_menu`、`test_open_manual`、`test_manual_path`）。`QuietTest` 另在程序代码里查不调用 `showMessage`、`MessageBeep`。
  - 一次性提示的计时器挂在悬浮窗上：悬浮窗在这之前删掉了就不再收起，也不去碰已删除的部件。
  - 推荐、选择、「未选择首推的原因」、漏记补录、同一组候选再出现，都按正确的编号记进推荐日志；判定新一局、撤销新一局也告诉推荐日志。
  - 没有推荐日志接口时（程序文件不完整时的第一张）不问「未选择首推的原因」：`question_state()` 为 `None`，点了也记不下来（`test_feedback_without_logbook`）。
  - 用户在悬浮窗上的操作（改正、补录、移出、关提示、切模式、切背景、收起问原因）记进识别记录的用户操作一条。

- **检证对象**：`advisor/ui/overlay_parts/background.py` 的 `Background`（`snapshot`、`export_diagnostics`、`_read_lanes`、`events_reader`、`stats_reader`、`_read_events`、`_read_stats`、`current_frame`、`_zip`、`export_done`、`save_undo_record`、`check_assets`、`start_download`、`download_finished`、`_icons_arrived`）；`advisor/ui/tray_toast.py`（`TrayToast`、`toast_position`）；`advisor/ui/overlay_parts/follow.py` 的 `Tray`（`setup_tray`、`menu_reset`、`menu_settings`、`menu_export`、`close_clicked`、`show_toast`）；`advisor/paths.py` 的 `manual_path`；`advisor/ui/overlay_parts/records.py` 的 `OfferLog`；`advisor/ui/widgets.py` 的 `chip_icon`；`advisor/ui/overlay.py` 的 `Overlay`（`export_diagnostics`、`frame_source`、`unread_source`、`pick_source`、`covered_source`、`finish_source`、`check_assets`、`_flash`/`_unflash`、托盘、`show_about`、`open_manual`）。

- **什么时候要跑**：改了导出诊断或识别记录的收集与写法（含另一张悬浮窗的 `lanes`、`events_reader`、`current_frame`）、图片下载、托盘、关于窗口、推荐日志的转接（`background.py`、`records.py`、`overlay.py` 里这几处）以后。

- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_background`（上次约 6 秒）。

- **需要什么**：没有特别要求。推荐日志、诊断、图片下载、版本都是假对象；不用识别样本截图。用离屏的 Qt。个别用例要求 `assets/chips` 里有下载好的筹码图标，没有时跳过。

- **输出怎么看**：跳过时的原因会写成「没有下载好的筹码图标（assets\chips）」；其余同总则。

- **出错时一般是哪里不对**：
  - `test_export_diagnostics`、`test_export_whole_game_from_recorder`、`test_export_uses_session_events` 失败 → 多半是导出时取的记录范围或写法改坏了（看 `overlay_parts/background.py` 的 `export_diagnostics`、`_read_events`）。
  - `test_export_stats_reader_records_scope`、`test_export_stats_error_keeps_package`、`test_undo_does_not_carry_stats` 失败 → 统计记录没在后台那一步里读、没写成 `stats.jsonl`、`stats_scope` 没写、读不出来时没记错误记录或照常导出，或撤销误判时也读了（看 `background.py` 的 `export_diagnostics`、`stats_reader`、`_read_stats`）。
  - `test_export_lanes_read_in_background`、`test_export_lane_stats_read_in_background`、`test_export_lane_read_error_keeps_package`、`test_export_without_lanes_unchanged`、`test_events_reader_and_current_frame` 失败 → 另一张的识别记录、统计没在后台那一步里读、导出范围没写进 `lanes.<端名>.export_scope`、`stats_scope`、读不出来时没记错误记录或没照常导出，或 `events_reader`、`current_frame` 没在界面线程里当场取好（看 `background.py` 的 `export_diagnostics`、`_read_lanes`、`events_reader`、`stats_reader`、`current_frame`）。
  - `test_feedback_without_logbook` 失败 → 没有推荐日志接口时还问了原因，或 `question_state` 没按接口判断（`advice_card.py` 的 `question_state`、`Overlay.services`）。
  - `test_undo_saves_record_without_frame`、`test_undo_record_uses_recorder` 失败 → 多半是撤销误判新一局的自动存盘改坏了（看 `background.py` 的 `save_undo_record`）。
  - `test_download_progress_failure_and_retry`、`test_download_blocks_recognition`、`test_icons_redrawn_after_download` 失败 → 多半是图片下载的进度、暂停、重试或下载完重画改坏了（看 `background.py` 的 `check_assets`、`download_finished`、`_icons_arrived`）。
  - `test_download_logs_stats` 失败 → 下载统计没写进识别记录、字段没带上，或没有记录器时仍写了（看 `background.py` 的 `_log_download_stats`）。
  - `test_offer_pick_and_refresh_logged`、`test_reverted_pick_*`、`test_missed_pick_logged_after_record` 失败 → 多半是推荐编号的转接改坏了（看 `overlay_parts/records.py` 的 `OfferLog`）。
  - `test_user_actions_traced` 失败 → 多半是识别记录里用户操作那一条漏了或字段名改了。
  - `test_flash_timer_tied_to_window` 失败 → 多半是一次性提示的计时器没挂在悬浮窗上，删掉的窗口还会被碰到。
  - `test_export_zip` 失败 → 压缩包没打、打的位置或名字不对，或打不成时的提示和错误记录改了（看 `background.py` 的 `_zip`、`export_done`，`diagnostics.zip_report`，`notices.NoticeModel` 的 export 一行）。
  - `test_question_line_collapses` 失败 → 一行显示时问原因那一块挂多久（`QUESTION_LINE_MS`、`AdviceCard.ask_until`）或 `layout.apply_mode` 里的显示条件改了。
  - `test_dismiss_*`、`test_feedback_try_other_school`、`test_reason_tips` 失败 → 多半是「×」的显示条件、`AdviceCard.dismiss_feedback` 的状态复位，第四个原因的记法，或原因按钮按原因取悬停说明的那一处改了（看 `advice_card.py` 的 `question_state`、`set_pick`、`dismiss_feedback`，`layout.py` 的 `build_advice`、`feedback_dismiss`）。
  - `test_close_to_tray`、`test_toast_*`、`QuietTest` 失败 → 收到托盘的提示又用了系统通知、会出声的接口，或小提示框的时长、位置、颜色改了（看 `advisor/ui/tray_toast.py`、`follow.Tray.close_clicked`）。
  - `test_tray_menu`、`test_open_manual`、`test_manual_path` 失败 → 托盘菜单的项、先后或动作改了，或使用说明的位置（`paths.manual_path`）、找不到时的提示改了。
  - 有意改了导出范围、提示文字或记录字段时，要一并改这里的预期。

## test_overlay_drag.py：在悬浮窗上按住左键拖动

- **测试观点**：
  - 单击（没有移动，或只移动了不到系统拖动距离的几个像素）不算拖动：不记位置、不写进设置文件，三选一期间照旧自动让开（`ClickTest`）。
  - 真的拖动：悬浮窗跟着移动，并记下相对游戏窗口的位置。
  - 拖到屏幕外松手：马上整个挪回屏幕内，存下的相对位置夹在 -1～2 之间，下次启动照这个位置放（`OffScreenTest`）。
  - 没有游戏窗口时拖到屏幕外：同样马上挪回屏幕内。
  - 按在标题栏的端标签上也能拖动悬浮窗（`LaneTagDragTest`）：标签不接文字交互，鼠标事件照常往上传给悬浮窗。

- **检证对象**：`advisor/ui/overlay_parts/follow.py` 的 `WindowFollower.mouse_press`、`mouse_move`、`mouse_release`、`remember_position`；`advisor/ui/overlay.py` 的 `Overlay.mousePressEvent`、`mouseMoveEvent`、`mouseReleaseEvent`；`advisor/ui/overlay_parts/geometry.py` 的 `valid_rel`；设置项 `window_rel`、`window_pos`。

- **什么时候要跑**：改了悬浮窗的拖动处理、相对位置的记录或夹取（`follow.py`、`geometry.py` 的 `valid_rel`）以后。

- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_drag`（上次约 0.3 秒）。

- **需要什么**：没有特别要求。鼠标事件直接交给悬浮窗（带屏幕坐标），不用识别样本截图；离屏的 Qt，不找真实的游戏窗口，前台状态由假的探测函数给。

- **输出怎么看**：同总则。

- **出错时一般是哪里不对**：
  - `test_click_is_not_a_drag`、`test_tiny_move_is_not_a_drag` 失败 → 多半是「算不算拖动」的判定改坏了（看 `follow.py` 的 `mouse_move`、`mouse_release`；判定改坏时一按一放就会记成拖动）。
  - `test_real_drag_moves_and_remembers` 失败 → 多半是相对位置的记录改坏了（看 `follow.py` 的 `remember_position`）。
  - `OffScreenTest` 的两个用例失败 → 多半是挪回屏幕内或相对位置的夹取（-1～2）改坏了（看 `geometry.py` 的 `valid_rel`、`follow.py` 的 `fit_on_screen`）。

## test_overlay_event_branches.py：`Overlay.handle` 里几个分支的行为（picked、pick_reverted、map_read、game_over、胜负）

- **测试观点**：
  - `picked`：选择记在给出推荐的那一次三选一上；没给出推荐（候选没认全）就选了时，只写「已选」、不问原因，推荐日志里没有这一次（`PickedBranchTest`）。
  - `pick_reverted`：同一组候选又出现时，推荐日志里撤销刚记下的选择，这一组沿用原来的编号；之后选了别的记在同一编号上，已有筹码里换成新选的（`PickRevertedBranchTest`）。
  - `map_read`：读到地图以后「打开任务栏」的提示收起，状态里写地图名（`MapReadBranchTest`）。
  - `game_over`：推荐日志记一次对局结束（同一局再送一次也不重复），带星数和已有筹码；只对进行中的对局有意义的提示（星数增加需补录）收起，边框不变色（`GameOverBranchTest`）。
  - 房间画面结束的局，之后的新一局撤销了：结束时收起的「查看筹码」提示、待补录提示、补录行（连同「再次出现、已撤销」的说法）都回来；撤销说明那次结束是误认的，这一局真正结束时推荐日志照常再记一条（以后一条为准），再读到结算画面也不重复记（`UndoAfterRoomEndTest`）。
  - 胜负（`GameResultBranchTest`，对局报告的拼换成记下调用）：没认出通关画面时，结束时推荐日志记一条（胜负 null），报告先等（排一次 `RESULT_WAIT_MS`）；奖励画面星币是 10，定了失败，再记一条（别的项照抄），这时拼一次；再读到奖励画面、等满的计时器晚到都不再记、不再拼；读到奖励画面前后悬浮窗上的文字一样。认出了通关画面：结束时就记通关，奖励画面对得上、不再记，这时拼。打输横幅和胜负判定的记账另在 test_game_outcome 里核对。没等到奖励画面就开了下一局：按上一局的局号拼一次；退出时（`Overlay.shutdown`）拼好才往下走。

- **检证对象**：`advisor/ui/overlay.py` 的 `Overlay.handle`、`report_game_over`、`shutdown` 里拼还在等胜负的报告那一步；`advisor/ui/overlay_parts/dispatch.py` 的 `EventHandlers.on_picked`、`on_pick_reverted`、`on_map_read`、`on_game_over`、`on_status`、`on_newgame`、`log_game_over`、`settle_report`；`advisor/ui/overlay_parts/records.py` 的 `OfferLog`；`advisor/ui/overlay_parts/reporting.py` 的 `Reporting.game_over`、`settle`、`settle_all`；文字在 `advisor/ui/overlay_parts/wording.py`。

- **什么时候要跑**：改了这几个分支的处理、推荐日志的记账（对局结束按局数和胜负去重）、对局报告什么时候拼，或者提示行/待补录提示的收起与恢复（`dispatch.py`、`records.py`、`reporting.py`、`notices.py`）以后。

- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_event_branches`（上次约 4 秒）。

- **需要什么**：没有特别要求。事件由真实 Session 产生、按识别流程的顺序交给悬浮窗，不用识别样本截图；用离屏的 Qt。

- **输出怎么看**：同总则。

- **出错时一般是哪里不对**：
  - `PickedBranchTest.test_pick_without_advice_asks_nothing` 失败 → 多半是「没给推荐就选了」的分支改坏了（看 `dispatch.py` 的 `on_picked`）。
  - `PickRevertedBranchTest` 失败 → 多半是撤销选择、沿用编号的逻辑改坏了（看 `dispatch.py` 的 `on_pick_reverted`、`records.py` 的 `on_reverted`）。
  - `MapReadBranchTest` 失败 → 多半是读到地图后提示的收起或状态写法改坏了（看 `dispatch.py` 的 `on_map_read`）。
  - `GameOverBranchTest`、`UndoAfterRoomEndTest` 失败 → 多半是对局结束的记录、撤销新一局时提示的恢复、或「以后一条为准」的记账改坏了（看 `dispatch.py` 的 `on_game_over`、`on_newgame`、`log_game_over`）。
  - `GameResultBranchTest` 失败 → 胜负定了再记一条、报告等胜负定了才拼的接线改了（看 `dispatch.py` 的 `on_status`、`log_game_over`、`settle_report`，`records.py` 的 `OfferLog.game_over`，`reporting.py` 的 `game_over`、`settle_all`）；收数据的服务按报告编号丢掉后发的同一份，拼早了的那一份发出去以后胜负就补不上了。

## test_overlay_follow.py：跟随游戏窗口、窗口层次、屏幕位置与屏幕外兜底

- **测试观点**：
  - 位置：默认放在游戏画面右侧「刷新」按钮正下方，内容变多向下展开而右上角不动；三选一显示期间让开三选一；关闭后回默认位置；展开超出屏幕底部时整个挪回屏幕内；游戏窗口移动时跟着移动；最小化后留在原处（`OverlayPositionTest`）。
  - 三选一显示期间、没有拖动过时，在几个候选位置里挑挡住候选筹码和「刷新」按钮最少的地方；给了 `avoid`（另一张悬浮窗）时也避开它，不给时结果不变（`LivePlacementTest`）。挨着另一张放（`beside`）依次试左边、右边、下边、上边，都不行时为 None（`BesideTest`）。
  - 不管屏幕怎么排、游戏窗口在哪、保存的相对位置是什么，悬浮窗都完整落在某块屏幕上（用多屏布局和大量相对位置穷举）（`OverlayAlwaysOnScreenTest`）。
  - 独立窗口（设置、关于这类）不总在最前，窗口层次跟着悬浮窗：悬浮窗置顶时置顶、退到盖住它的窗口后面时一起退、三选一时放到它们上面；关掉在前台的独立窗口时先临时置顶悬浮窗、前台交接后再按原来的先后退回；点托盘图标把开着的窗口一起放到最前面、按打开的先后（`SecondaryLayerTest`）。
  - 窗口的截图属性一律不改（`CaptureAffinityTest`）。
  - 六种前台状态 × 两种截取方式 × 自动/手动 × 三选一显示与否 的做法（`window_plan`）：识别是否暂停、窗口层次、热键是否登记、状态胶囊和悬停说明逐格对上（`WindowPlanTest`）。
  - 界面线程定时器晚到多少记一笔 `ui_lag`，用的时钟不含电脑睡眠时间（`PollLagTest`、`PollClockTest`）。
  - 同时识别时找不到游戏窗口的那张看另一张的前台状态（`window_plan` 的 `companion`）：另一张是 `game`、`own` 时也置顶，是 `background`、`minimized`、`nogame` 或没有另一张时照旧不置顶；同时识别、这一张在后台、另一张是 `game`、`own` 时这一张也置顶（三选一时 `front`；截取屏幕时暂停也置顶）；其余状态不管 `companion` 是什么都不变（`WindowPlanTest.test_nogame_with_companion`、`test_background_with_other_game_front`、`test_companion_ignored_elsewhere`）。
  - 「盖住」的判断只看游戏窗口上面的窗口、跳过不算的、相交宽高都至少 8 像素才算（`CoveringTest`）；同时识别时另一张跟的游戏窗口不算盖住这张（`probe_game` 的 `ignore`），它和浏览器都盖住时插在浏览器下面、只有它盖住时不算被盖住（`WinapiFlagsTest.test_probe_ignores_other_game_window`）；悬浮窗按 `window_plan` 调整自己的层次、识别记录和热键跟着变、托盘恢复层次、游戏重启后重试直接截取、点「全部展开/全部折叠」和诊断包里状态的记录（`ForegroundLayerTest`、`ExpandDiagnosticTest`、`WinapiFlagsTest`）。
  - 找不到游戏窗口时显示的那一句用探针带回来的 `notice`（设了游戏平台、开着的是另一个端时点明是设置的原因）；探针没带时为「未找到游戏窗口，请先启动游戏。」；一直是找不到、这一句变了时换成新的并重画一次；显示着别的提示时不盖掉；游戏窗口又看得到时收起（`MissingGameNoticeTest`）。
  - 托盘菜单的「重置悬浮窗」：菜单项的顺序（「显示」「重置悬浮窗」「设置」「导出诊断」，hub.ENABLED 开着时接「打开作业站」，隔一条线「退出」）；一行显示、有卡片单独收起时重置以后全部展开、收起记录清空并存成空表；按假的主显示器可用区域放到正中（外框中心对区域中心，允许差 1 像素），比区域还大时左上角对齐；收在托盘里、一行显示时显示出来以后按展开后的大小居中；有游戏窗口时记下新位置（`rel_custom`、`window_rel`、`window_pos`），之后再按游戏窗口重算仍在正中，没有游戏窗口时也存 `window_pos`；正在显示三选一、没拖动过时点了也放到正中，三选一期间不再让开；收在托盘里时点它显示出来；识别记录记一条 reset（`TrayResetTest`）。两张一起重置的测试在 `test_lanes.py` 的 `TwoLaneResetTest`：两张都收在托盘里、一行显示、各自收起一张卡片时点重置，两张都显示出来、全部展开、收起记录清空并存成空表；第一张外框中心在主显示器可用区域正中，第二张紧挨在它右边、上沿相同；两张都有游戏窗口时都是 `rel_custom`，`lanes.taptap.window_rel`、`window_pos` 是第二张的，顶层的是第一张的；在第二张上调用效果一样（转给第一张）；两张各自的识别记录各有一条 reset；接上托盘的 `show_more`（第一张从托盘显示时把第二张也显示出来）时第二张记的 `hidden` 仍是点之前的状态；点了以后、居中之前第二张被收掉时第一张照常居中、不去摆第二张；`Lanes.restack` 被调用。假屏幕够放两张，改窗口层次的 Win32 调用换成假的。

- **检证对象**：`advisor/ui/overlay_parts/geometry.py`（`overlay_position`、`live_placement`、`beside`（含 `prefer`）、`pair_position`、`AVOID_WEIGHT`、`fit_on_any_screen`、`pick_zones`、`window_plan`、`probe_state`、`WindowPlan`、`valid_rel`、`_window_gone_texts`）；`advisor/ui/overlay_parts/follow.py` 的 `WindowFollower`（`update_activity`、`log_layer`、`target_position`、`place`、`poll`、`companion_rect`、`companion_fixed`、`yields`、`restack`）、`WindowStack`、`Tray`；`advisor/ui/overlay.py` 的 `Overlay`（`reset_overlay`、`_reset_partner`、`_reset_one`、`_center_after_reset`、`_primary_area`）；`advisor/ui/winapi.py`（`GameProbe` 的 `notice`、`covering`、`TopWindow`、`foreground`、`topmost`、`below`、`set_topmost`、`put_below`、`bring_to_front`、`probe_game` 的 `ignore`）；`advisor/vision/capture.py` 的 `missing_notice`；`advisor/perfstats.py` 的 `unbiased_clock`。

- **什么时候要跑**：改了悬浮窗的位置或让开逻辑、跟随与窗口层次、托盘恢复、前台状态判断、找不到游戏窗口时显示的那一句、`winapi.py` 里跟窗口层次有关的调用以后。

- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_follow`（上次约 7 秒）。

- **需要什么**：没有特别要求。不真的找窗口、不截屏：游戏窗口状态用假探测结果，改窗口层次的 Win32 调用换成假的；不用识别样本截图；用离屏的 Qt。

- **输出怎么看**：同总则。

- **出错时一般是哪里不对**：
  - `OverlayPositionTest`、`LivePlacementTest`、`OverlayAlwaysOnScreenTest` 失败 → 多半是位置计算或屏幕外兜底改坏了（看 `geometry.py` 的 `overlay_position`、`live_placement`、`fit_on_any_screen`、`pick_zones`）。
  - `WindowPlanTest` 失败 → 多半是 `window_plan` 里六种前台状态的做法改动了（六种状态各是什么写法要和界面上的胶囊、悬停说明一致）。
  - `SecondaryLayerTest`、`ForegroundLayerTest`、`WinapiFlagsTest` 失败 → 多半是窗口层次调用、关窗口时的临时置顶与退回、或只设自己窗口且不激活的写法改坏了（看 `follow.py` 的 `WindowStack`、`WindowFollower.apply_layer`，`winapi.py` 的层次调用）。
  - `CoveringTest` 失败 → 多半是「盖住」的判定规则改坏了（看 `winapi.py` 的 `covering`）。
  - `PollLagTest`、`PollClockTest` 失败 → 多半是记录界面线程晚到的时钟或门槛改坏了（看 `follow.py`、`perfstats.py`）。
  - `MissingGameNoticeTest` 失败 → 找不到游戏窗口时的提示没按探针的 `notice` 显示、这一句变了没换、盖掉了别的提示，或游戏窗口回来后没收起（看 `follow.py` 的 `update_activity`、`winapi.probe_game` 的 `notice`）。
  - `TrayResetTest` 失败 → 「重置悬浮窗」没全部展开、没清掉卡片的收起记录，居中时用的不是展开以后的大小或不是主显示器的可用区域，或没当作拖动过记下位置（看 `overlay.py` 的 `reset_overlay`、`_center_after_reset`，`follow.py` 的 `Tray.setup_tray`、`WindowFollower.remember_position`）。`TwoLaneResetTest`（test_lanes.py）失败 → 两张没一起做、顺序反了（第一张的 `show_from_tray` 先把第二张显示出来，第二张的 `hidden` 就不是点之前的状态）、第二张没按 `pair_position` 摆在第一张右边，或在第二张上调用没转给第一张（看 `reset_overlay`、`_reset_partner`、`_reset_one`、`_center_after_reset`）。

## test_overlay_guard.py：守卫——悬浮窗的部件不拿着整个悬浮窗

- **测试观点**：
  - `advisor/ui/overlay_parts/` 下每个模块都归了类（外壳、编排、底层模块、部件），归类表里的也都还在（`StaticGuardTest.test_modules_classified`）。
  - 静态扫描源码：外壳以外的模块不导入悬浮窗本体和别的部件模块、不取别的对象的私有成员、不顺着 Qt 的父子关系去找悬浮窗、不经回调的 `__self__`/`__closure__` 找回它属于谁、构造时不收悬浮窗（`StaticGuardTest`）。
  - 悬浮窗和各部件只用 `SessionAPI` 等里列出的 Session 接口，列出的名字真实的 Session 上都有（`StaticGuardTest.test_session_api_listed`、`SessionApiTest`）。
  - 扫描本身认得出几种写坏的源码，也不误报正常写法（`ScannerSelfTest`）。
  - 运行时：建一个离屏悬浮窗，在几种典型状态下逐个看部件对象的属性，没有一个是悬浮窗本身或别的部件（事件分派除外），也不藏在列表或字典里（`PartsHoldTest`）。

- **检证对象**：`advisor/ui/overlay.py`、`advisor/ui/overlay_parts/` 下各模块的源码结构；`advisor/ui/overlay_parts/state.py` 的 `SessionAPI`、`SettingsAPI`、`GameAPI`、`EventLogAPI`。

- **什么时候要跑**：新增或改动 `advisor/ui/overlay_parts/` 下的模块、给部件加了新属性、部件之间新增了引用、或给悬浮窗加了新的显示状态以后。

- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_guard`（上次约 1.5 秒）。

- **需要什么**：没有特别要求。静态部分只读源码，运行时部分建一个离屏悬浮窗（不弹窗口）；不用识别样本截图。

- **输出怎么看**：报错会写明是哪个文件哪一行（运行时是哪个部件的哪个属性）、违反了哪一条、怎么改；其余同总则。

- **出错时一般是哪里不对**：
  - `test_modules_classified` 失败 → 多半是加了新模块却没登记，或删了模块没更新表（改这个测试文件里的归类表）。
  - `test_imports`、`test_parent_chain`、`test_callback_owner`、`test_constructors` 失败 → 多半是部件之间或部件对外壳的引用绕过了约定的回调方式（按报错里的文件名和行号去改）。
  - `test_private_members` 失败 → 多半是部件取了别的对象的私有成员（改成公开的名字，或由外壳接成回调）。
  - `test_session_api_listed`、`SessionApiTest` 失败 → 多半是悬浮窗用了没列入 `SessionAPI` 的接口，或列出了一个 Session 上并不存在的名字（改 `state.py` 的接口表）。
  - `ScannerSelfTest` 失败 → 多半是扫描本身的识别能力被改坏了（这是「守卫的守卫」）。

## test_overlay_hotkeys.py：全局热键的解析、按键转文字、线程里登记和更换

- **测试观点**：
  - 解析热键文字：修饰键（Ctrl、Alt、Shift、Win）加主键（F1～F24、字母、数字、Pause 等），小键盘数字键写「Num5」、和主键盘的「5」是不同的热键；写法无效时报出原因（`HotkeyTest.test_parse`）。
  - 界面上按下的组合键转成热键文字，且能被解析回来；不能用作热键的键返回空（`HotkeyTest.test_key_event_to_text`）。
  - 热键线程里换热键：成功时注销旧的、新的被占用时报告原因并保留旧的，线程退出时注销自己的热键（`HotkeyThreadTest.test_change_and_busy`）。
  - 启动时登记失败（被占用）会报告，线程仍在运行、可以在界面上再换（`HotkeyThreadTest.test_startup_busy_reports_and_can_change`）。
  - 热键只在游戏窗口前台时登记：注销后别的程序能用这个键，再登记时恢复（`HotkeyEnableTest`）。

- **检证对象**：`advisor/ui/hotkey.py` 的 `parse_hotkey`、`same_hotkey`、`hotkey_text`、`busy_text`、`startup_busy_text`、`Hotkey`（`start`、`request`、`set_enabled`、`stop`，信号 `failed`/`changed`/`change_failed`）。

- **什么时候要跑**：改了热键的解析或写法、热键线程的登记与更换以后。

- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_hotkeys`（上次约 0.7 秒）。

- **需要什么**：只在 Windows 上跑，别的系统上两个线程相关的测试类会跳过。热键线程真的向系统登记组合键，用的是 F13～F24 加两三个修饰键（别的程序几乎不用），按进程号错开、用完注销；不建悬浮窗；用离屏的 Qt。

- **输出怎么看**：非 Windows 上会看到两个测试类跳过；其余同总则。

- **出错时一般是哪里不对**：
  - `test_parse` 失败 → 多半是热键解析（修饰键、`F25`、小键盘数字键、主键盘与小键盘的区分）改坏了（看 `hotkey.py` 的 `parse_hotkey`、`same_hotkey`）。
  - `test_key_event_to_text` 失败 → 多半是「按下的组合键 → 热键文字」的转换改坏了（看 `hotkey.py` 的 `hotkey_text`）。
  - `test_change_and_busy`、`test_startup_busy_reports_and_can_change` 失败 → 多半是换热键时旧键没注销、或新的被占用时没保留旧键（看 `hotkey.py` 的 `Hotkey.request`、`_apply_pending`）。
  - `test_enable_disable` 失败 → 多半是「只在游戏前台时登记」的开关（`Hotkey.set_enabled`）改坏了。

## test_overlay_late_events.py：悬浮窗按事件序号只显示最新的推荐

- **测试观点**：
  - 三选一期间识别线程算好一条还没送到时用户换了方案：界面上按新方案重算的推荐留着，晚到的旧推荐不显示、不记推荐日志，之后同一画面也不再发事件（`SchemeSwitchTest`）。
  - 三选一刚出现的第一个推荐还没送到时换方案：显示按新方案算的，排着的那条不显示、不记。
  - 交错到达的两批：换方案前的旧批次里的推荐不显示、不记，新批次照常显示和记；提示类事件两批都照常处理。
  - 没有序号的事件不比先后，已经处理过序号更大的推荐以后照样显示（`test_event_without_seq_always_handled`）。
  - 改正候选时正好算好一条（改正前的名字）：改正后重算的推荐留着，「手动指定」的那一格不被换回去（`FixAndHeroTest`）。
  - 角色待识别、用户先选了角色：晚到的「请选择角色」不显示，推荐不再暂停。
  - 上一局已结束，下一局第一次三选一：晚到的 `newgame` 照常开新的一局、推荐按新方案重画，排着的旧推荐不显示（`LateCloseTest`）。
  - 两个任务接连完成、前一次没看到选择：晚到的「漏记」照常出补录行，但推荐日志里那一次的编号已经给了后来的推荐，补录的不记进推荐日志。

- **检证对象**：`advisor/ui/overlay_parts/dispatch.py` 的 `EventRouter`（`late`、`taking`、`_seq`）；`advisor/ui/overlay.py` 的 `Overlay.handle`；`advisor/session/` 里 `Event.seq`（事件的序号）。

- **什么时候要跑**：改了悬浮窗处理识别线程事件先后的逻辑（`dispatch.py` 的 `EventRouter`）、事件序号，以及换方案/改正候选/选角色时重算推荐的逻辑以后。

- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_late_events`（上次约 7 秒）。

- **需要什么**：没有特别要求。事件由真实 Session 产生，先取出来、之后再交给悬浮窗，相当于排着队等处理的信号；方案文件放在测试沙箱的临时文件夹里；用离屏的 Qt。

- **输出怎么看**：同总则。

- **出错时一般是哪里不对**：
  - `SchemeSwitchTest` 失败 → 多半是按序号丢弃旧推荐的判断、或换方案时重算的逻辑改坏了（看 `dispatch.py` 的 `EventRouter.late`、`overlay.py` 的 `_recompute`）。
  - `test_event_without_seq_always_handled` 失败 → 多半是把没有序号的事件也当旧事件丢掉了。
  - `FixAndHeroTest` 失败 → 多半是改正候选或选角色后留在界面上的状态被晚到事件覆盖了。
  - `LateCloseTest` 失败 → 多半是新一局的判定与紧跟的推荐、或漏记补录的记账改坏了（看 `dispatch.py`、`records.py`）。

## test_overlay_layout.py：悬浮窗的排版（卡片顺序与折叠、悬停说明的样式和宽度、自绘部件）

- **测试观点**：
  - 全部展开时从上到下是标题栏、「识别」「筹码方案」「推荐」「已有筹码」四张卡片和底栏，左右边缘对齐、卡片之间隔 8（`CardLayoutTest.test_cards_aligned`）。
  - 每张卡片的折叠按钮（▾ 展开中 / ▸ 已收起）：收起后只剩卡片头和摘要；收起状态存进设置文件、重启后沿用；设置文件里的值不对时忽略（`test_card_collapse_saved`）。
  - 收起后卡片头的摘要（识别「第 N 轮 · N 星 · 刷新 ×N · 地图」，推荐「首推：X N%」，已有筹码「N 个」）。
  - 「推荐」卡片在出现新的三选一时自动展开；用户在同一次三选一里收起后就保持收起；卡片里出现需要操作的提示时自动展开，用户收起后这条不再让它展开、新的提示才展开；一行显示时卡片只在有提示时出现、只显示那几行提示（`test_advice_card_auto_expands`、`test_prompt_expands_collapsed_card`、`test_one_line_cards_show_prompts_only`）。
  - 一次性提示写在所属的卡片里（补录→已有筹码，地图→识别，改正→推荐，切换模式→标题栏下方），一行显示时写在摘要里不重复；选项块之间隔 14（中间一条淡分隔线）、块内 4；选项块第一行只有推荐度和名称、不写分值（`test_messages_go_to_their_card`、`test_option_spacing_and_separators`、`test_option_blocks_without_scores`）。
  - 同系列的选项块：被替代的写「被X上位替代」和对比行，回看上一次推荐时同样显示、筹码名不是按钮；字号只用四档；固定文字暗色、数值和筹码名亮色（`test_series_blocks`、`test_four_font_sizes`、`test_fixed_text_dim`）。
  - 悬停说明统一用样式表里 `QToolTip` 的样式；带自己样式表的部件上的悬停说明和不带的一样；各部件的样式表都带选择器（`TipStyleTest`）；全部悬停说明按分行规则分行、不超高、不超宽（走各种状态收集）（`TipWidthTest`）。
  - 标题栏只有一个管整个悬浮窗的按钮（一行显示「全部展开」、全部展开「全部折叠」），底栏只有「设置」；卡片头的折叠按钮是小号方形、有底色框线但更淡、箭头随状态变（`FoldButtonsTest`）。
  - 标题栏最左边的端标签（`LaneTagLayoutTest`）：七套皮肤、一行显示和全部展开，标签都在这一行最左边、不和摘要或标题重叠、不比没有标签时高；摘要能用的宽度正好少掉标签的宽加一个间距；标题在正中的皮肤标题仍居中；`HEAD_ALIGN` 为 `bottom` 的皮肤里标签和右边的按钮一样靠下，换皮肤时对齐跟着变；开发版的长标题放得下；换皮肤以后标签的宽和摘要宽度跟着变；一行显示时两个端的「未找到……游戏窗口」加上标签以后都不被省略。
  - 自绘部件：长理由在标签内换行、流式布局会换行、带流派标签的效果说明不另起一行、图标格子按 3-4-3 排十格、地图胶囊滚轮不切地图（`WidgetsTest`）。
  - 图标格子分页（`WidgetsTest`）：超过 10 个时每页 `names()` 只列当前页、`all_names()` 是全部；翻页、这一页没了退到最后一页、不超过 10 个回第 1 页（`test_chip_grid_pages`）；超过 10 个时 `sizeHint` 只多出翻页那一行、宽度不变，不超过 10 个时和以前相同（`test_chip_grid_pager_size`）；点亮着的箭头翻页、第一页的左箭头和最后一页的右箭头点了没反应（`test_chip_grid_arrows`）；第 2 页悬停筹码显示这一页那一格的筹码，悬停箭头写「上一页」「下一页」（`test_chip_grid_tooltip_on_second_page`）；暗着的箭头悬停不写「上一页」「下一页」、光标不是手形，点亮着的箭头翻到头、鼠标没动时光标也跟着恢复（`test_chip_grid_disabled_arrow`）。

- **检证对象**：`advisor/ui/overlay_parts/layout.py` 的 `build`、`build_head`（`lane_tag`）、`apply_mode`；`advisor/ui/overlay.py` 的 `set_lane_tag`、`_summary_width`、`_center_title`、`_align_head`；`advisor/ui/theme/sheets.py` 的 `QLabel#laneTag`；`advisor/vision/capture.py` 的 `PLATFORM_TAGS`；`advisor/ui/widgets.py` 的 `Pill`、`FlowLayout`、`TaggedText`、`ChipGrid`、`MapPill`、`chip_icon`、`Card`、`Separator`、`draw_combo_rows`；`advisor/ui/text.py` 的 `tip_html`、`tip_lines`、`tip_paragraphs_html`、`option_views`、`PERCENT_TIP`；`advisor/ui/theme/` 里的字号和颜色。

- **什么时候要跑**：改了搭界面或摆放卡片（`layout.py`）、自绘部件（`widgets.py`）、悬停说明的分行与宽度（`text.py`）、字号或配色（`theme/`）以后。

- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_layout`（上次约 6 秒）。

- **需要什么**：没有特别要求。推荐日志和识别线程用假对象，不用识别样本截图；用离屏的 Qt，并且要装得上微软雅黑等字体，排版和像素才和别的机器可比。

- **输出怎么看**：如果没装上字体，会在错误输出里看到一行提示（说明界面测试改用别的字体、结果不可比）；其余同总则。

- **出错时一般是哪里不对**：
  - `CardLayoutTest` 失败 → 多半是卡片的顺序、间距、折叠与自动展开的规则改坏了（看 `overlay_parts/layout.py` 的 `build`、`apply_mode`、`CardLayoutTest` 涉及的卡片部件）。
  - `TipStyleTest`、`TipWidthTest` 失败 → 多半是悬停说明的样式、分行或宽度改坏了（看 `advisor/ui/text.py` 的 `tip_html`、`tip_lines`、`tip_paragraphs_html`）。
  - `FoldButtonsTest` 失败 → 多半是标题栏或底栏按钮、卡片折叠按钮的文字或样式改坏了（看 `layout.py` 的 `build_head`、`build_toolbar`，`widgets.py` 的 `Card`）。
  - `WidgetsTest` 失败 → 多半是自绘部件的尺寸、换行或图标画法改坏了（看 `advisor/ui/widgets.py`）；其中 `test_chip_grid_pages`、`test_chip_grid_pager_size`、`test_chip_grid_arrows`、`test_chip_grid_tooltip_on_second_page`、`test_chip_grid_disabled_arrow` 失败 → 图标格子的分页、翻页箭头（含暗着时的悬停说明和光标）、页码或第 2 页的悬停对不上（看 `widgets.py` 的 `ChipGrid`）。
  - 有意改了文案、字号、间距时，要一并改这里的预期。

## test_overlay_layout_table.py：切换显示方式的规则表

- **测试观点**：
  - 规则表 `CARD_WHEN_COLLAPSED` 和测试里逐条判断的参照实现（`reference`）在所有输入组合下对部件做的事完全一样（穷举标题栏和各卡片的输入、五千多种组合；决定 `prompt` 的几项里含一行显示时问原因那一块还挂不挂着（`ask_until`），「识别」一块含进没进对局（`map_entered`：全部展开时打开任务栏那一行要进了对局才显示），「筹码方案」一块含这一局按地图换着方案没有（`scheme`：那一行在时一行显示也出现、收起着自动展开））（`CardTableTest.test_same_as_before_in_every_case`）。
  - 表的键的先后就是卡片的先后（`test_table_rows`）。
  - 比较本身查得出差别：把表里「推荐」一行改得不看 `prompt` 后，要问原因、刚记录完的几种情形就和参照实现不一样了（`test_comparison_notices_a_changed_rule`）。

- **检证对象**：`advisor/ui/overlay_parts/layout.py` 的 `CARD_WHEN_COLLAPSED`、`apply_mode`。

- **什么时候要跑**：改了「一行显示 / 全部展开」时哪些卡片出现、怎么摆放或算摘要的规则（`apply_mode`、`CARD_WHEN_COLLAPSED`）以后。

- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_layout_table`（上次约 0.7 秒）。

- **需要什么**：没有特别要求。不建 QApplication，用假的悬浮窗和假部件（只记下对每个部件做的事）；不用识别样本截图，不弹窗口。

- **输出怎么看**：不通过时会指出是哪一组输入有一处不同，并打印规则表和参照实现两边的记录，照着找规则表里对应的那一行；其余同总则。

- **出错时一般是哪里不对**：
  - `test_same_as_before_in_every_case` 失败 → 多半是规则表或 `apply_mode` 与参照实现（逐条判断）的行为不一致（输出里会指出是哪一组、哪一处不同）。
  - `test_table_rows` 失败 → 多半是表里各行的先后被改动了。
  - `test_comparison_notices_a_changed_rule` 失败 → 多半是比较本身失效了（改了 `apply_mode` 让两边的输入不再被用上），这时要检查比较还查不查得出差别。

## test_overlay_newgame_timeout.py：「带『撤销』的新一局提示」什么时候自动收起

- **测试观点**：
  - 选角界面判定的那一种：显示 `NEWGAME_NOTICE_MS`（默认 30 秒）后自动收起，「撤销」一起去掉，之后不能再撤销这次判定（`SelectNoticeExpiryTest`）。
  - 对局中途判定的那一种：显示 `NEWGAME_MIDGAME_NOTICE_MS`（默认 60 秒）后「识别」卡片和「已有筹码」卡片的两行一起收起，「撤销」一起去掉；不到时长时两行照旧在（`MidgameNoticeExpiryTest`）。
  - 到点时只有还是同一次判定才收起：这期间又判了一次、点了撤销、记下了第一个筹码，前一次的计时到了都什么都不做。
  - 判定依据的种类没写（`cause` 为空）的可撤销新一局，按「选角界面」的约定显示和计时，也按时收起。
  - 按真实计时器走一遍（时长换成很小）：到点真的收起，且到点时核对的是计时器挂上时记下的那一次判定；两种时长各走一遍（`RealTimerTest`）。

- **检证对象**：`advisor/ui/overlay.py` 的 `_schedule_newgame_notice`、`_expire_newgame`、`_newgame_seq`；`advisor/ui/overlay_parts/wording.py` 的 `NEWGAME_NOTICE_MS`、`NEWGAME_MIDGAME_NOTICE_MS`；`advisor/ui/overlay_parts/notices.py` 的 `NoticeModel.midgame`、`newgame_why`。

- **什么时候要跑**：改了带「撤销」的新一局提示的计时或收起条件以后。

- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_newgame_timeout`（上次约 2 秒）。

- **需要什么**：没有特别要求。计时不真等 30 秒、60 秒（直接走计时器到点那一步，或把时长换成很小走真实计时器）；不用识别样本截图；用离屏的 Qt。

- **输出怎么看**：同总则。

- **出错时一般是哪里不对**：
  - `SelectNoticeExpiryTest.test_select_notice_expires_with_undo`、`RealTimerTest.test_timer_expires_select_notice` 失败 → 多半是选角界面判定的提示没有按时收起、或「撤销」没一起去掉（看 `overlay.py` 的 `_schedule_newgame_notice`、`_expire_newgame`）。
  - `MidgameNoticeExpiryTest.test_midgame_notice_expires_with_undo`、`MidgameNoticeExpiryTest.test_midgame_notice_before_timeout`、`RealTimerTest.test_timer_expires_midgame_notice`、`RealTimerTest.test_midgame_notice_not_expired_early` 失败 → 多半是对局中途判定的两行没有按 60 秒收起、两行没有一起收起，或不到时长就收起了（看 `notices.py` 的 `midgame`、`overlay.py` 的 `_schedule_newgame_notice`）。
  - `test_second_judgement_keeps_its_own_timer`、`RealTimerTest.test_first_timer_keeps_second_judgement`、`RealTimerTest.test_select_timer_keeps_later_midgame_notice` 失败 → 多半是「到点时核对是不是同一次判定」的序号判断改坏了（看 `overlay.py` 的 `_newgame_seq`、`_expire_newgame`）。
  - `test_undo_before_timer_does_nothing`、`test_first_chip_before_timer_does_nothing` 失败 → 多半是撤销或记下第一个筹码后没有正确使计时作废。
  - `test_undoable_without_cause_expires` 失败 → 多半是 `cause` 为空时显示和计时的约定不一致（看 `notices.py` 的 `midgame`）。

## test_overlay_notices.py：截取方式提示、第一次启动的「免费软件」声明和认出繁体游戏时的提示

- **测试观点**：
  - 改为截取屏幕时「识别」卡片顶部的提示（⚠ 橙色，带「关闭本条提示」，不改边框颜色，一行显示时也显示）、悬停说明；四种原因各一种写法（`CaptureNoticeTest.test_texts_and_tips`）。
  - 「出错」没有系统错误代码时的悬停说明换成另一句，不写错误代码、也不写出错的内部步骤；发行版里错误记录的位置按用户看到的程序文件夹写（`test_failed_without_code`、`test_error_record_in_release`）。
  - 提示排在对局中途判定的新一局之后（`test_order_with_midgame_newgame`）。
  - 「关闭本条提示」只管这次运行：同一原因不再提示，换了原因照常提示；恢复直接截取后提示收起并一次性提示「已恢复」；启动后第一次收到截取状态、或一直是直接截取时不提示（`test_dismiss_and_restore`、`test_no_restore_notice_at_startup`）。
  - 截取状态的 `detail` 统一成错误代码的写法（`0x` 加 8 位大写），旧写法（代码和步骤写在一起、十进制）也认（`test_detail_parsing`）。
  - 「免费软件」声明：第一次启动显示、一行显示时也在、文字逐字照 `version.FREE_NOTICE` 后接账号风险那一句（`FREE_NOTICE_RISK`），还是一条提示、一个「关闭本条提示」；网址做成可点的链接，后一句里的「关于」也做成链接，点了打开「关于」窗口、停在「免责声明」一节（不交给浏览器）；排在全局提示的最后；点「关闭本条提示」后记进设置文件、以后启动不再显示；开发版和发行版都显示；设置文件里这一项不是 `true` 时按没关过；没有版本模块时不显示；「关于」窗口最上面照旧只写 `FREE_NOTICE`、不接账号风险那一句（每种排法）（`FreeNoticeTest`）。
  - 界面是简体、认出游戏是繁体中文时的提示（把繁体界面的总开关 `lang.HANT_ENABLED` 打开了测；关着时一律不问）：文字和按钮转成繁体（写死转出来的全文）、排在「免费软件」声明前面、〔換〕把界面语言存成繁體并重新打开（起不来时不退出、这一行自己收起）、〔不換〕记进设置文件以后不再问；两个按钮一样宽且不少于三个汉字的宽度；第二张不显示（全局提示）、界面已经是繁体、设置里已选繁體、关过时不显示；对局进行中（找得到游戏窗口）不显示，这一局结束、回到大厅后显示，中途退出以后认不出主菜单、连着一阵子看不到对局读数（`Session.game_quiet`）时也显示；`Session.on_frame` 只在完整识别上数，连着两次繁体才置 `hant_game`，只重查绿框的不算（`HantNoticeTest`）。

- **检证对象**：`advisor/ui/overlay_parts/notices.py` 的 `capture_notice`、`notice_html`、`NoticeView.check_free_notice`、`open_link`、`dismiss_free`、`dismiss_hant`、`_hant_row`、`NoticeModel`、`EQUAL_WIDTH_NOTICES`；`advisor/ui/overlay_parts/wording.py` 的 `FREE_NOTICE_RISK`、`FREE_NOTICE_LINK`、`ABOUT_LINK`、`ABOUT_DISCLAIMER`、`HANT_NOTICE_TEXT`、`HANT_SWITCH`、`HANT_STAY`；`advisor/ui/overlay_parts/factory.py` 的 `_equal_width`；`advisor/ui/overlay.py` 的 `switch_to_hant`、`_playing`；`advisor/ui/overlay_parts/state.py` 的 `take_view`、`SessionView.hant_notice`；`advisor/session/` 的 `Session._note_game_language`、`hant_game`；`advisor/ui/text.py` 的 `capture_detail`、`linkify`、`link_style`；`advisor/version.py` 的 `FREE_NOTICE`、`about_sections`；`advisor/ui/about.py` 的 `AboutDialog`、`LAYOUTS`。

- **什么时候要跑**：改了截取方式的提示文字或悬停说明、错误记录位置的写法、「免费软件」声明、「关于」窗口最上面一段，或改了认出繁体游戏时的提示、`ChipSelect.hant`、`Session.hant_game` 以后。

- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_notices`（上次约 2 秒）。

- **需要什么**：没有特别要求。推荐日志用假对象，设置文件写在临时文件夹里，改文件位置和「关于」窗口排法用替身；不用识别样本截图；用离屏的 Qt。

- **输出怎么看**：同总则。

- **出错时一般是哪里不对**：
  - `CaptureNoticeTest` 的几个用例失败 → 多半是截取方式提示的文字、级别、按钮、悬停说明或收起条件改坏了（看 `overlay_parts/notices.py` 的 `capture_notice`、`NoticeView.update`，`wording.py` 的 `CAPTURE_NOTICE`、`CAPTURE_TIP`）。
  - `test_detail_parsing` 失败 → 多半是截取状态里错误代码的解析改坏了（看 `advisor/ui/text.py` 的 `capture_detail`）。
  - `FreeNoticeTest.test_shown_once_checked`、`test_last_in_global_area` 失败 → 多半是「免费软件」声明的文字或它在全局提示里的位置改坏了（看 `notices.py` 的 `rows`、`version.py` 的 `FREE_NOTICE`）。
  - `test_closed_for_good`、`test_setting_values` 失败 → 多半是「关闭本条提示」写设置项或读取设置项的判断改坏了。
  - `test_about_window_top`、`test_about_every_layout` 失败 → 多半是「关于」窗口最上面那一段的排法改坏了（看 `advisor/ui/about.py`）。
  - `HantNoticeTest` 失败 → 多半是这条提示的文字、转换结果、显示条件（含对局进行中不显示）、两个按钮的接线和宽度，或 `Session.on_frame` 数繁体标题的规矩改坏了。

## test_overlay_owned_card.py：「已有筹码」卡片补录行的说法

- **测试观点**：
  - 补录行的初始说法是「未检测到选择」（`missed_pick`），判定新一局时留下的那一份（撤销新一局时接回去）开始时也是它（`test_initial_state`）。
  - 撤销了记录（`pick_reverted`）以后补录或忽略：说法回到「未检测到选择」，撤销的筹码、首推、候选都清空（`test_pick_missed_resets_to_missed_pick`）。
  - 补录行的结果是操作结果（已补录、已在已有筹码中），交给过一会儿自动收起的提示；点「关闭本条提示」只换回等待提示（`test_results_flash`）。

- **检证对象**：`advisor/ui/overlay_parts/owned_card.py` 的 `OwnedCard.pick_missed`、`missed_kind`、`missed_before`、`missed_offer`、`missed_raw`、`missed_offer_id`；`advisor/ui/overlay_parts/state.py` 的 `EV_MISSED_PICK`、`EV_PICK_REVERTED`。

- **什么时候要跑**：改了补录行的说法、补录或忽略的处理以后。

- **怎么单独跑**：`python -B tools/run_tests.py --only test_overlay_owned_card`（上次不到 1 秒，是最快的几个之一）。

- **需要什么**：没有特别要求。不建窗口：卡片构造时给的控件、锁、Session、回调都是假的；不用识别样本截图。

- **输出怎么看**：同总则。

- **出错时一般是哪里不对**：
  - `test_initial_state` 失败 → 多半是补录行初始的说法或留下那一份的初值改坏了（看 `owned_card.py` 的 `OwnedCard.__init__`）。
  - `test_pick_missed_resets_to_missed_pick` 失败 → 多半是补录/忽略后没有把说法和记下的候选清干净（看 `owned_card.py` 的 `pick_missed`）。
  - `test_results_flash` 失败 → 多半是补录结果该用「过一会儿自动收起的提示」还是「换回等待提示」的判断改坏了。

## test_watcher_pace.py：识别线程一帧识别完以后隔多久再取帧

- **测试观点**
  - `PaceTest`：这一帧有几项子识别出错时，三选一显示着按 `active_ms` 再取、没显示时隔 2 秒，两种情况都把错记进错误记录并给固定中文提示；没出错时照旧按 `active_ms`／`idle_ms`；`errors` 只认真的列表、元组；认三选一出错时隔 2 秒，按热键要求的那一帧不报「不是三选一界面」，手动模式不因为这一帧接着跟随
  - `PendingReadsTest`：识别流程还有排到下一帧的读数时不等、紧接着取下一帧，手动模式也接着取、做完为止；出错时照旧隔 2 秒；识别线程在两帧之间告诉识别流程手动模式开没开、刚从暂停恢复
  - `SelectFailedFlagTest`：认三选一出错的那一帧置上 `select_failed`，下一帧开头清掉，三选一显示状态保持上一帧的值
  - `UnreadShotTest`：识别流程留下的三选一读不全的那一帧（`FrameProcessor.unread_shot`）每帧换到识别线程身上（`Watcher.unread_shot`，导出诊断时取）；识别流程那边没有（刚重建、还没有）时照旧留着上一张，不是元组的不算；记下选中项用的那一帧、被临时盖住而没有采用的那一帧、对局中途像一局打完的那一帧（`pick_shot`、`covered_shot`、`finish_shot`）同样（`test_pick_and_covered_shots`）
  - `PickWatchWiringTest`：快速盯绿框的线程（`vision.pick_watch.PickWatcher`）在识别线程开始时建、启动，识别流程建好以后接到它的交换上，识别线程结束时停下；识别流程重建时接到新的交换、原来的关掉；测试传入假的取帧对象而没给 `band_source` 时不建，平时用 `screen.BandSource`（截取方式跟着识别线程的取帧对象）；识别暂停、手动模式平时（等热键）时告诉识别流程不再盯（`watch_idle`），手动模式跟着三选一时照样盯；每分钟的识别统计带上它这一段的统计（`pick_watch`），取不到时只记错误记录
- **检证对象**：`advisor/ui/overlay_parts/watcher.py` 的 `Watcher`（一帧后的等待、`NO_FRAME_RETRIES`／`NO_FRAME_WAIT_MS`、`set_manual`、`set_paused`、`trigger`、`unread_shot`、`pick_shot`、`covered_shot`、`finish_shot`，快速盯绿框的线程 `_start_pick_watch`、`_attach_pick`、`_watch_idle`、`_stop_pick`、`_log_recog_stats` 的 `pick_watch`）；`advisor/vision/pipeline.py` 的 `FrameProcessor.errors`、`select_failed`、`pending`、`chip_select_visible`、`unread_shot`、`pick_shot`、`covered_shot`、`finish_shot`、`pick_watch`；`advisor/vision/pick_watch.py` 的 `PickWatch`、`PickWatcher`；`advisor/ui/overlay_parts/wording.py` 的 `RECOG_ITEM`、`RECOG_FRAME`、`recog_items_text`；`rules.toml` 的 `[watch] active_ms`／`idle_ms`
- **什么时候要跑**：改了识别线程一帧后的等待时长、子识别出错的处理与提示、排到下一帧的读数、认三选一出错的标记、手动模式的跟随，或快速盯绿框的线程怎么建、接、停，都要跑
- **怎么单独跑**：`python -B tools/run_tests.py --only test_watcher_pace`（上次约 0.3 秒）
- **需要什么**：离屏平台；识别线程真的起一个，取帧对象和识别流程都是假的（不截屏）；要等的时候记下等多久，不真的等
- **输出怎么看**：没有特别的，同总则
- **出错时一般是哪里不对**
  - `PaceTest` 的 `test_error_while_select_visible_keeps_active_pace`／`test_error_while_hidden_waits_two_seconds`／`test_no_error_unchanged` 失败 → 有错没出错时的等待时长改坏（`advisor/ui/overlay_parts/watcher.py`）
  - `PaceTest.test_frame_error_fixed_text`／`test_same_error_logged_each_time_shown_once`／`test_items_in_one_frame_one_sentence` 失败 → 出错提示的固定中文、去重，或错误记录改坏（`advisor/ui/overlay_parts/watcher.py`、`wording.py` 的 `RECOG_ITEM`／`recog_items_text`、`advisor/errors.py`）
  - `PaceTest.test_mock_errors_not_counted`／`test_select_flag_only_true_counts` 失败 → 把不是真的列表／`True` 的值也当成有错（`advisor/ui/overlay_parts/watcher.py`）
  - `PaceTest.test_select_error_waits_two_seconds`／`test_select_error_on_hotkey_frame`／`test_select_error_does_not_keep_manual_follow` 失败 → 认三选一出错时的等待、提示或手动模式跟随改坏（`watcher.py`、`advisor/vision/pipeline.py`）
  - `PendingReadsTest` 失败 → 还有排到下一帧的读数时等了，或没告诉识别流程手动模式／刚从暂停恢复（`watcher.py`、`advisor/vision/pipeline.py` 的 `pending`）
  - `SelectFailedFlagTest.test_flag_follows_select_error` 失败 → `select_failed` 标记的置上／清掉，或三选一显示状态改坏（`advisor/vision/pipeline.py`）
  - `UnreadShotTest` 失败 → 识别线程没照识别流程换那一帧，或识别流程那边没有时把留着的清掉了（`watcher.py` 的 `unread_shot`、`pick_shot`、`covered_shot`、`finish_shot`）
  - `PickWatchWiringTest` 失败 → 快速盯绿框的线程没建、没接到识别流程的交换上、识别线程结束时没停下，暂停或手动模式平时没告诉识别流程不再盯，或统计没带上（`watcher.py` 的 `_start_pick_watch`、`_attach_pick`、`_watch_idle`、`_stop_pick`、`_log_recog_stats`）
  - 有意改了 `[watch]` 的时长或提示文字时，断言用的值／句要跟着改

## test_hub.py：作业站的地址、地图短名、四个入口和总开关

- **测试观点**
  - `UrlTest`：三个地址和 `MAP_SLUGS` 逐字核对（和网站的约定，改了要让测试失败）；`MAP_SLUGS` 的键都在资料库的地图里，没结束的地图都在表里（资料新加地图时会提醒去和网站对短名），每个短名都有 `mechanics/maps/<短名>.md`，三张运动场不在表里；`plans_url` 带角色编号、带地图短名、两个都带、空字符串的短名当没有；`hero_plans_url` 收录的角色、资料库没有的角色（带地图时只有 `map`）、运动场（不带地图）、没有的地图名、`None`。
  - `GlyphBookTest`：「作业站」的书按宽高比占宽度；系统没有图标字体时照样画、照样留出图标那一行；路径左右对称；格子的落点有零头（左右、上下，1、1.25、1.5 倍缩放）时书的样子不变，左上角落在整个设备像素上（`test_whole_device_pixels`）。
  - `OverlayHubTest`（`setUp` 里把 `hub.ENABLED` 打开）：卡片按钮的图标、字、`tool`、悬停说明；新建的会话没认出地图时不带地图；这一局自动认出了地图（卡片按钮和导入/导出窗口的「去作业站找方案」都带短名，记录里的 `map` 是短名）；手动选的地图也带；本局已结束时地图还在但不带（记录的 `map` 为 `None`）；运动场没有短名只带角色；有地图时托盘的「打开作业站」照旧是首页；「发到作业站」由窗口自己发方案，不再经过这里打开网页；换成资料库没有的角色时列出全部方案；换角色后两处地址跟着变；打开失败时显示悬浮窗、提示地址并记一条 `ok` 为假的记录；托盘菜单项的位置和文字；七套皮肤下三个按钮都在、字和 `tool` 对。
  - `ShareHubTest`（`setUp` 里把 `hub.ENABLED` 打开）：七个排法里「去作业站找方案」「发到作业站」都在、字和悬停说明对；点了「去作业站找方案」发 `hub_requested("plans")`，「发到作业站」由窗口自己发方案（没勾方案时不联网、不发 `hub_requested`）；`Dialogs.open_share` 把它们接到 `Overlay.open_hub`；七套皮肤、程序实际字体下，导入/导出窗口按默认大小显示时五个按钮都整个落在看得见的地方（在 `cardsScroll` 里时看 `viewport`），窗口的最小宽度不超过默认宽度（反色排法里两个作业站按钮和导入、清空在同一行：单独一行会把导入、清空、找方案挤出可见区）。
  - `HubDisabledTest`（测试里把 `hub.ENABLED` 关上）：藏着的按钮照样留在原来的布局里、父部件在窗口里（不落成单独的顶层部件），所在的布局拿掉它以后建议大小、最小大小都不变（`assert_no_room`：看不见的部件不占位、前后不加间距）。卡片上的「作业站」按钮这样藏着，另外两个按钮按它们两个取一样的宽度，「导入/导出」的右边贴着卡片内容的右边；七套排法里导入/导出窗口的「发到作业站」「去作业站找方案」、按编号导入那一行（编号输入框和「按编号导入」）、「已登录作业站」那一行（「退出作业站」在这一行里）都这样藏着（单独一行的排法不留空行），提示行、小按钮也不显示；关着时不读本机的登录信息（`hub_api.login_nickname`），也不联网、不碰凭据管理器；托盘菜单没有「打开作业站」。
- **检证对象**：`advisor/hub.py`（`ENABLED`、`BASE`、`HOME`、`PLANS`、`MAP_SLUGS`、`map_slug`、`plans_url`、`hero_plans_url`）；`advisor/ui/overlay.py` 的 `Overlay.open_hub`、`_trace_user`；`advisor/ui/overlay_parts/layout.py` 的「作业站」按钮、`follow.py` 的托盘菜单项、`wording.py` 的 `HUB_TIP`／`HUB_BTN`；`advisor/ui/scheme_share.py` 的 `SchemeShareWindow`（`find_btn`、`post_btn`、`_game_parts`、`_build_stacked`、`hub_own_row`）和 `FIND_ON_HUB_TIP`；`advisor/ui/glyphs.py` 的 `book_path`、`draw_glyph`、`glyph_width`；`tests/support/overlay_cases.py` 的 `choose_hero`、`tip_text`。
- **什么时候要跑**：改了 `advisor/hub.py`（含总开关 `ENABLED`）、作业站的四个入口、`MAP_SLUGS` 或 `data/maps.json`（地图增减、结束）、「筹码方案」卡片的三个按钮、导入/导出窗口的排法和那两个按钮、托盘菜单，或改了这几处的文字时。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_hub`（上次约 12 秒）。
- **需要什么**：Qt 的离屏平台，不弹窗口；打开网址的函数一律换成假的，不会真的打开浏览器；方案文件放在测试沙箱的临时文件夹里。`UrlTest.test_map_slugs_match_data` 读项目里的 `mechanics/maps/`。
- **输出怎么看**：没有特别的，同总则。量按钮是不是整个看得见时，按钮在 `cardsScroll` 滚动区里按 `viewport` 算，有页签的排法先切到按钮所在的页签。
- **出错时一般是哪里不对**
  - `UrlTest.test_urls`／`test_map_slugs` 失败 → 地址或地图短名和网站约定不一致（先确认网站那边的要求，不要直接改预期）。
  - `UrlTest.test_map_slugs_match_data` 失败 → 资料里新加了没结束的地图但没加进 `MAP_SLUGS`，或短名和 `mechanics/maps/` 的文件名对不上（`advisor/hub.py`、`data/maps.json`）。
  - `UrlTest.test_plans_url`／`test_hero_plans_url` 失败 → 拼地址的顺序、空短名、没收录角色时的处理改了（`advisor/hub.py`）。
  - `OverlayHubTest.*` 失败 → `open_hub` 取角色/地图、判断 `game_over`、记录 `map`，或入口的接线改了（`advisor/ui/overlay.py`、`layout.py`、`follow.py`、`dialogs.py`）。
  - `ShareHubTest.test_five_buttons_visible_all_skins` 只在某套皮肤下失败 → 那种排法里按钮被挤出可见区或窗口被撑宽（`advisor/ui/scheme_share.py` 的排法、`hub_own_row`）。
  - `HubDisabledTest` 失败 → 总开关没把作业站的入口都关掉（卡片的按钮、导入/导出窗口的几处、托盘菜单），关掉以后还占着位置、留着空行，关着时读了本机的登录信息或联网，或藏着的部件没放进布局、落成了单独的顶层部件（`advisor/hub.py` 的 `ENABLED`、`layout.py`、`scheme_share.py`、`follow.py`；落成顶层部件时 `test_scheme_wiring` 也会失败）。
  - `GlyphBookTest` 失败 → 自己画的书换形、占的宽度或对称性改了，或落点不再挪到整个设备像素上（`advisor/ui/glyphs.py` 的 `_draw_book`）。
  - **改了预期就该跟着改**：地址、地图短名是和网站的约定，改之前先和网站对好；改了界面文字时悬停说明的断言跟着改（`wording.HUB_TIP`、`scheme_share.FIND_ON_HUB_TIP`）。
