# 测试指南 · 对局记录

这是[《测试指南》](../测试指南.md)第八节的一章：每个测试文件一节，每节的结构见那里第八节的开头。


这一章管一局的记账（advisor/session）、离开再回来判新一局，以及识别记录和推荐日志的读写。

## test_session.py：记账（Session）的总测试——一局从三选一到结束的判定、读数、存盘

- **测试观点**：从这些角度检查（按测试类分）——
  - `SessionTest`：三选一给推荐与去重（同一画面不重复给、刷新后重给）、记下选中的候选筹码、漏记时请用户补录、星数的读取与推算、三选一来源（升星/筹码商店 vs 任务）的判定、地图识别与手动指定不被覆盖、轮次与星数变小或读错时判不判新的一局、角色识别与手动指定角色、角色锁定、跨对局的设置存取。
  - `StartupPanelTest`：启动时不知道已有筹码 → 进入对局后请用户打开「查看筹码」面板，读到自己那一页后写入已有筹码；有认不出的格子时保留原记录、按格子位置放回、整页 10 格都满就不保留。中途打开、还没读到面板时给的推荐带「已有筹码未知」标记（`owned_unknown`）和当时地图的识别依据（`map_source`）、本局难度的依据（`difficulty_source`），另有和局面里已有筹码一一对应的来路（`owned_sources`）和面板记号（`owned_panel`），从开局看起（`panel_needed` 为假）的不带；看着三选一记下了筹码、一直没读到面板时，下一次推荐照样带这个标记（局面里是记下的那几个），读到面板以后不带（`test_unknown_after_picks_without_panel`）；离开很久后回来、还记着之前的筹码时推荐照记着的算、不带这个标记，记着的是空的才带（`test_resume_with_recorded_chips_not_unknown`）。
  - `LobbyStartupPanelTest`：刚打开时在大厅（还没有对局状态时读到过主菜单、房间、「匹配成功」画面、结算画面、奖励画面），之后进的对局：第一次读到的轮次是第 1、2 轮、没先看到三选一时算从开局看起、不提示；先读到星数时先不提示、等轮次；等的时候先看到三选一、三选一那一帧（`begin_offer` 在轮次之前）、第一次读到第 4 轮、一直读不到轮次等满 `PANEL_WAIT_S` 都照旧提示；没见过这些画面的照旧第一次对局内读数就提示。
  - `PanelFixesPickTest`：思考时间用完时游戏自动拿最左边的筹码，之后读面板与记录不符时以面板为准，并给出改正了哪一次的选择记录。
  - `OwnRowTest`、`HeroNeededTest`、`RevertPickTest`、`UnrecordedGainTest`、`UndoNewGameTest`：自己在玩家列表第几行与本次运行认出的玩家名字；角色待识别时暂停推荐并请用户选择角色、手动指定而立绘认不出时请识别流程存模板；同一组候选在三选一关闭后又出现时撤销刚记下的筹码；星数增加了却没有对应的三选一记录时提示补录；判定新的一局后能撤销，撤销恢复已有筹码、轮次、地图、角色、队友和面板提示。
  - `DirectChipTest`：补录时记为不经三选一获得的筹码：面板核对不改，移出已有筹码时去掉，新的一局清空、撤销恢复，`build_state` 只数其中不是地图直接发放的，诊断里的来源也只把这些写成 direct；待补录的升星走同一条补录但不记。
  - `EndlessPendingTest`：循环往复给的额外那次三选一（`Session.endless_pending` → `State.endless_pending`）：在三选一里看到选中循环往复时记下到期的轮次（升星、筹码商店拿到的再过 2 回合，任务拿到的 3 回合）；到期以后第一次判成任务来源、又不是全队同时三选一的当作给了，这一次本身不再多算；到期那一轮先来的判成升星的不当作它；过了到期那一轮的下一轮不再算；拿到时轮次读不出、用「＋ 补录筹码」手动加的、移出已有筹码时、新的一局都不算，撤销新一局时恢复；同一组候选读对以后改判来源时跟着改。漏记、用户补录时点了循环往复的，按漏记的那次三选一当时的轮次和来源记（那时轮次读不出照旧不记，`test_missed_pick_of_endless`）。额外那次给了以后（`Session.endless_given` → `State.endless_given`）：认出的那一次本身、刷新换了候选以后为 1，那次选中的筹码（漏记补录的也算）还在已有筹码里时为 1，改判来源撤回、筹码移出已有筹码时为 0（`test_given_extra_pick_and_its_chip`、`test_missed_extra_pick_records_chip`）。到期的轮次另交给引擎（`State.endless_due`，只在还没给时带），到期在进度上限以后时引擎不多算（`test_due_after_cap`）。
  - `OwnedSourcesTest`：已有筹码各自的来路（三选一选的、漏记补录、补录、补录时标了不经三选一、玩家手改、面板头一次读到、启动参数给的）和「查看筹码」面板上的记号：进已有筹码时记下，面板读到、玩家改已有筹码时不变，离开时去掉，移出后放回恢复，同一组候选又出现时撤销选择的一起去掉，新的一局清空、撤销恢复；推荐事件上和已有筹码一一对应；诊断快照里也有每张筹码的来路、面板上看到过的和这一局没采用的进度上限。
  - `MapReadAndMutatorsTest`、`UnsupportedTest`、`TeammatesTest`、`HistoryTest`：任务栏认出地图时发 `map_read`、选角前公布的畸变组件留到下一局；不支持的情况（独占全屏、窗口过小、游戏语言不是简体中文或繁体中文）写进状态行提示、每条写明影响和处理方法；队友角色的确认；`recent_events` 和 `snapshot` 能 JSON 序列化。
  - `DifficultyTest`、`DifficultyInfoTest`：本局难度按三选一界面上的概率行判定（普通 / 困难及以上），两张表都对不上时沿用；本局具体难度按结算画面 > 「匹配成功」画面 > 房间 > 进度上限推算 > 概率行的先后取，交给引擎的 `difficulty_level`；进度上限只和极限对得上时推算成极限、不记 `cap_mismatch`（`test_extreme_from_cap`）；房间读数和推算矛盾时按读不准处理（困难及以上），两边都在噩梦及以上时（房间疯狂、御魂庆典 17 格只有极限；房间噩梦、幽魂暗巷 16 格只有疯狂）按噩梦及以上、难度名不写，一边是困难的照旧困难及以上（`test_room_against_cap_both_nightmare_plus`）；进度上限连续两次读到以后先核地图资料，对不上时（水乡古镇 20 格只有普通、概率行却是困难及以上）不采用、同一个数不重复记（`test_cap_mismatch_rejected`），地图后来知道了或概率行改判后对不上时去掉已经采用的（`test_cap_mismatch_after_map_known`），概率行判成普通时普通难度的格数照常采用（`test_cap_with_normal_rows`），地图认错时没采用的数在地图改对以后照常采用（`test_rejected_cap_adopted_after_map_fixed`），已经拒过的数夹在两次读数中间时不打断「连着两次读到另一个数」（17、8、17 里的 8 拒过，第二个 17 照常采用，`test_rejected_cap_between_two_readings_does_not_break_the_run`），地图在 `cap_untrusted` 里、资料不全时判断不了、照旧采用（`test_cap_kept_when_data_cannot_judge`）；一直没读到星数时估出的星数不低于升星、筹码商店三选一估过的最高星（`judge_source` 的 `floor`，读到过星数时不起作用），任务三选一、最左格的稀有度不知道（第 1 个候选没读出、边框颜色也没读到）时估成的升星都不抬高这个下限，开了新的一局清零、撤销时跟着那一局回来（`test_judge_source_estimate_not_below_floor`、`test_star_estimate_floor_through_session`、`test_star_guess_floor_raised_by_levelup_only_and_follows_game`、`test_star_guess_floor_not_raised_when_leftmost_unknown`）。
  - `GameOverTest`、`RecorderTest`、`ReviewRecordTest`、`RecorderErrorTest`：对局结束（结算画面读到一次；房间、「匹配成功」画面、奖励画面要隔一会儿连续两次）；结束信息里的地图：手动指定的照旧用手动的，否则结算画面上读到了就用它（另记 `map_source`、`map_in_game`，房间等画面先结束、之后又读到结算画面时同样重算），都没有时用局里认的，来源是当时的依据；另记本局难度的依据（`difficulty_source`）和这一局没采用的进度上限有几个（`cap_rejected`）。识别记录的转发、换段、撤销、结束标记、`status` 记下变了哪几项；识别记录器出错不影响记账。
  - `LeftmostRarityTest`、`RefreshHoldTest`、`PartialNameTest`、`FirstStarTest`：最左格稀有度判定三选一来源；刷新时保留刷新前的推荐并标「正在刷新」，给过推荐以后（不是刷新）读不全的当场提示；只读到几个筹码名的共同部分时稍后再读；0 星时按刷新次数判断是不是升到 1 星的那一次。
  - `FirstUnreadTest`：三选一刚出现（这一次还没发过推荐、无法识别、请选择角色）时有筹码没读出来：先不提示、不发事件（`holding`，切方案的重算也先不算），读全了照常给推荐；隔半秒以上的第二次完整识别仍读不全、或只有快速复查时等满约 1 秒才提示无法识别，不早于这个时刻，也不一直不提示；提示过无法识别、发过请选择角色以后就不算刚出现，之后读不全的当场提示；紧接着换上来的另一次三选一也算刚出现（上一次照常结算）；等着时界面关了不提示、照常请补录；等着时玩家点了「隐藏」，点「显示」回来时读全了照常给推荐、仍读不全的当场提示（已经等满了，不再另等）；刚出现时没读出来的那一格接着只读到共同部分时还是同一回等待，等满以后按挑出的那一个给推荐、提示匹配度低，之后读全了推荐换成读全的、过时的提示去掉（`test_partial_then_complete_drops_low_match`）；诊断包另带的那一帧看的 `SelectTracker.unread`（新读的一次读不全为真，快速复查、读全、刷新动画的中间帧为假，刷新后等满了还读不全的为真）。
  - `UnadvisedTest`：认出了三选一、程序放弃等待、没给推荐的那几次（`unadvised`）：刚出现时等过仍读不全、刷新后新的三张仍读不全各记一次（原因 `name`、`unknown` 按头一次出现的先后，不重复）；角色还没认出来记 `hero`；给过推荐以后同一组候选里一格被挡住、只读到共同部分以后按挑出的那一个给过推荐的不算；后来读全给了推荐时 `advised` 和 `star`、`source` 改成推荐里的（开了这一项以后的头一个推荐，同一次三选一里之后重算出的推荐不再改它）；关掉时把最后看到选中的那张记进 `pick`；一次三选一只发一次，下一次没给推荐的照旧另记；关掉时先取走这一项再交给记账，记账出错时这一项也不留到下一次三选一。
  - `RereadRejudgeTest`：同一组候选第一次读错、后来读对了（刷新次数从 0 变多、概率行从表外变成表内）时，记账重新判「是不是刚升到 1 星」和这次三选一的来源，撤回第一次留下的记录（最近读到的（星数, 刷新次数）、「每升一星刷新 +1」的推断、非任务三选一的次数和星币拆账那一段的次数）；关了又出现、中间确认过星币数时不再动已经记下的那一段；第一次就读对、刷新以后都不重新判。同一组候选关掉以后又出现（切出游戏又切回等，`test_reopened_keeps_level_up`）：「是不是刚升到 1 星」按这次三选一之前的刷新记录重新判，和第一次一样（例如 0 星时刷新过一次、升星那次读到 2 次判成 1 星，又出现时仍按 1 星）。
  - `HudParseTest`、`LoadingCheckTest`、`SelectFrameTest`：不跑文字识别，直接用构造的文字段检验轮次和地图的判定（含全部地图任务栏两两对比、限时地图降为后备、轮次的各种读错）；离开对局后的整幅识别的节流；三选一界面上也读轮次、角色、自己的星数，读完再交给记账。
- **检证对象**：`advisor/session/core.py` 的 `Session`（`on_frame`、`on_hud`、`on_stars`、`on_hero`、`on_select_screen`、`on_out_of_game`、`on_panel`、`on_progress_cap`、`on_teammates`、`on_mutators`、`on_loading`、`undo_new_game`、`save` 等）、`advisor/session/newgame.py` 的 `NewGameJudge`、`advisor/session/selection.py` 的 `SelectTracker`、`advisor/session/difficulty.py`、`advisor/session/panel.py`、`advisor/session/events.py` 的 `Event`/`EventLog`/`_event_record`、`Settings.load`/`save`；轮次和地图的判定在 `advisor/vision/hud.py` 的 `_parse_round`、`_guess_map`、`_exact_map_name`、`retired_maps`；节流在 `advisor/vision/pipeline.py` 的 `FrameProcessor`。
- **什么时候要跑**：改了 `advisor/session/` 下任何模块、`advisor/vision/hud.py` 的轮次/地图判定、`advisor/vision/pipeline.py` 里交给记账的读取时机、`Settings` 的字段与存盘、`data/maps.json` 的任务文字以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_session`（上次约 33 秒）
- **需要什么**：不需要识别样本截图、不需要显卡；只读 `data/` 资料库和 `data/maps.json`。
- **输出怎么看**：同总则。失败会带子测试名（`map=`、`probabilities=`、`kind=` 等），`HudParseTest` 的地图用例失败时断言里会打印各张地图的匹配分数（`map_scores`）。
- **出错时一般是哪里不对**：
  - `SessionTest.test_advice_once_per_offer` / `test_reroll_gives_new_advice` 失败 → 三选一的去重键坏了（看 `advisor/session/selection.py` 的 `SelectTracker.frame`、`_unchanged`）。
  - `SessionTest.test_new_game_*` / `test_misread_*` / `test_repeated_misread_*` 失败 → 判新的一局与「改正读错」的规则被改坏（`advisor/session/newgame.py` 的 `read_round`、`read_star`、`game_continues`）。
  - `SessionTest.test_row_not_matching_star_means_task` / `test_unknown_star_is_guessed_*` 失败 → 三选一来源与星数的判定（`advisor/session/selection.py` 的 `judge_source`、`table_row`）。
  - `LeftmostRarityTest.*` 失败 → 最左格稀有度的取用（`selection.py` 的 `judge_source.leftmost_fits`；档位资料在 `data/rules.toml` 的稀有度表）。
  - `FirstUnreadTest.*` / `RefreshHoldTest.*` 失败 → 读不全时等不等、等多久（`selection.py` 的 `SelectTracker.hold_frame`、`fresh`（这一次发过推荐、无法识别、请选择角色没有，`_told`）、`HOLD_RETRY_S`/`HOLD_READS`/`HOLD_MAX_S`）；`test_unread_flag` 失败 → 诊断包另带哪一帧的判断（`SelectTracker.unread`，在 `frame` 里按这一帧定）。
  - `RereadRejudgeTest.*` 失败 → 读对以后的重新判（`core.py` 的 `_open_judged`、`_observe_rerolls`、`_ledger_confirm`，`game_state.py` 的 `_open_read`，`selection.py` 的 `first_star`）；`test_reopened_keeps_level_up` 失败 → 同一组候选又出现时没有把刷新记录换回这次三选一之前的（`core.py` 的 `_select_opened`）。
  - `EndlessPendingTest.*` 失败 → 循环往复额外那次的记账（`core.py` 的 `endless_pending`、`endless_given`、`_endless_got`、`_endless_judged`、`_select_closed`、`resolve_missed`、`ENDLESS_GRACE`，`game_state.py` 的 `_endless_due`、`_endless_given`、`_endless_open`、`_endless_chip`、`_missed_at`，等的回合数 `data/rules.toml` 的 `[endless_cycle] delay`、`advisor/engine/parts.py` 的 `endless_wait`）。
  - `StartupPanelTest.*` / `PanelFixesPickTest.*` 失败 → `advisor/session/panel.py` 的 `read_panel`、`panel_order`、`panel_fixes`，或 `Session.on_panel`。
  - `LobbyStartupPanelTest.*` 失败 → 刚打开时在大厅的那一笔和进对局时提示不提示（`core.py` 的 `_entered_game`、`_from_start`、`begin_offer`、`PANEL_WAIT_S`，`offgame.py` 的 `on_out_of_game` 记 `GameState._outside_seen`）。
  - `DifficultyTest.*` / `DifficultyInfoTest.*` 失败 → `advisor/session/difficulty.py` 的 `resolve_difficulty`、`DifficultyInfo.label`，或 `core.py` 的 `_resolve_difficulty`、`_difficulty_changed`。
  - `GameOverTest.*` / `ReviewRecordTest.*` 失败 → `advisor/session/offgame.py` 的 `on_out_of_game`、`end_game`、`round_after_game`，或 `advisor/session/events.py` 的 `EventLog`。
  - `HudParseTest.*` 失败 → `advisor/vision/hud.py` 的 `_parse_round`、`_guess_map`；如果是有意改了 `data/maps.json` 的任务文字，这里关于任务重叠和匹配度的预期要一起改。
  - 断言里写死的事件说明（「检测到组队房间」等）、难度名、「不支持的情况」的两句话、识别提示，都是有意改动时把预期一起改。

## test_session_fuzz.py：随机化性质测试（慢测试）——大量带识别噪声的假读数喂给记账

- **测试观点**：
  - `test_noisy_games`：按真人对局的节奏生成 220 局读数（轮次 1→N、星数升到 3、升星/任务/全队任务/筹码商店三选一、队友、进度条），往里注入真人对局里出现过的识别噪声；检查一局之内不判新的一局、两局之间一定且只判一次、撤销能恢复、新的一局清空已有筹码和进度上限；并核对各种情形确实都走到了（每 2~3 秒读一次轮次和星数、角色认出后 20 秒复查一次）。
  - `test_overlapping_noise`：几项读数的噪声同时出现（星币转账读到 0 星的同时怪物名牌挡住轮次读错一位、昏迷时角色认错等），150 局。
  - `test_round_misread_lower_still_vetoes`、`test_repeated_step_misread_still_vetoes`、`test_step_misread_in_early_rounds_is_corrected`：钉住以前修正过的缺陷——轮次被读错成较小的数（「14」读成「11」）、往前一步的读错连读两次抬高了「确实到过」的轮次；这时轮次仍要否决星数、角色读错带来的新一局迹象。
  - `test_first_hero_misread_is_corrected`、`test_first_round_misread_at_game_start`：标了「预期失败」的两条，钉住软件维护说明「已知局限」写明的局限（对局中途启动时第一次认错角色；开局第一次轮次读错）。局限将来修好了，用例会变成「意外通过」而报错。
  - `test_early_game_single_signal_judges_new_game`、`test_other_reading_older_than_20s_does_not_veto`：前几轮没有可印证的读数时只靠一项照样判；另一项的读数超过 20 秒没有了时也不否决。
  - `test_progress_cap_random_reads`、`test_reroll_count_kept_when_button_hidden`、`test_team_select_stays_task_after_banner_disappears`：进度上限连续两次读到同一个数才采用；刷新按钮被挡住（读成 0 次）时推荐仍按刚才读到的次数；全队任务那一行消失后、刷新以后仍按任务来源算（随机序列里刷新时候选换一组、刷新次数少 1：概率行一样、三张全换而次数没少的算紧接着的另一次三选一）；界面关了以后的下一次不再按任务算：出现之前不到 3 星的是升星，已经 3 星的是筹码商店。
- **检证对象**：`advisor/session/core.py` 的 `Session`、`advisor/session/newgame.py` 的 `NewGameJudge`（含轮次上限和互相印证的 20 秒窗口）、`advisor/session/selection.py` 的 `SelectTracker`。
- **什么时候要跑**：改了判新的一局的规则、星数与轮次的读数处理、三选一的来源与刷新次数以后。改了判新的一局这块，它是最后一道关卡。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_session_fuzz`（上次约 5.6 秒；名单里算慢测试，跑 `--slow` 或 `--all` 才会带上它）
- **需要什么**：不需要识别样本截图、不需要显卡；只用 `data/` 资料库。随机用固定种子，失败可复现。
- **输出怎么看**：同总则。用例失败时抛出的说明里有种子、第几局、当时的真实情况与 `Session` 记下的值，以及最近的读数（每次调用的时刻、参数和返回的事件）。
- **出错时一般是哪里不对**：
  - 失败并说「一局之内判成了新的一局」→ 新一局的判定过于敏感（`advisor/session/newgame.py` 的 `read_round`、`read_star`、`game_continues`）。
  - 失败并说「两局之间没有判定新的一局」→ 判定过于保守，或两局之间的读数（选角界面、轮次变小、星数变少）没有被采用。
  - 断言「各情形最少出现多少次」（转账界面连续读到 0 星、刷新按钮被挡住、全队任务刷新等）失败 → 驱动或噪声注入被改了，测试没走到那些情形；数字是有意调整的就把预期一起改。
  - `test_first_hero_misread_is_corrected` / `test_first_round_misread_at_game_start` 报「意外通过」→ 写明的局限被修好了：去掉「预期失败」这个标记，并更新软件维护说明里的「已知局限」一节。
  - `test_progress_cap_random_reads` 失败 → 进度上限「连续两次才采用」的规则坏了。

## test_session_regressions.py：记账几处判定的回归测试，以及识别流程交给记账的两处

- **测试观点**（按测试类分）：
  - `UndoTwiceTest`：同一局里撤销过一次新一局的判定以后，又撤销了另一种迹象判出的新的一局——前一次撤销留下的「不再当成新一局的迹象」照样在。
  - `JumpTwiceTest`：大幅跳变来的读数不算「确实到过」——从跳变来的值再跳一次时，前一个值照样不算，之后读回真实值只是改正、不是新的一局。
  - `PendingWindowTest`：「连续读到两次」的判定——中间看过画面却没读到、又隔了 20 秒以上的两次同样的读错不算连续；手动模式两次按热键之间没有读过画面的照旧算。
  - `OfferKeyTest`：同一组候选不重复给推荐，但资料库未收录的候选的稀有度变了要重算，已收录的边框颜色读得不稳不重算。
  - `RefreshAgainTest`：刷新后一直读不全、等满约 1 秒提示无法识别以后，同一次三选一里又点了一次刷新，新的中间帧照样标「正在刷新」。
  - `OfferKeyOwnershipTest`：去重键只归三选一管；判定新的一局、撤销时悬浮窗清掉了显示着的三选一，局面没变也重发一次。
  - `ConsecutiveSelectStarTest`：两个任务接连完成、两次三选一之间没有棋盘画面时，第二次三选一上读到的星数算这一次的（不被上一次的结算清掉）；`test_another_select_query` 核对识别流程问的「是不是紧接着出现的另一次」：概率行换了是另一次，概率行一样时三张全换、刷新次数少了 1 是刷新，次数没少是另一次。
  - `SameRowSelectsTest`：两次三选一紧接着出现、概率行一样——三张候选全换了、刷新次数两边都确实读到而且没有减少（次数更多也算）就是另一次：先结算上一次（绿框选中的记进已有筹码、发 `picked`，没看到绿框的 `missed_pick` 请用户补录），推荐事件的 `select_no` 换成新的一次，补录计数（`_selects_since_read`、星数加了 2 时两次都对上、不提示补录）和星币拆账的非任务次数各算两次，补认后重新判的依据（`_open_read`）换成这一次的。拿不准的照旧当刷新（不结算、绿框作废、不多算）：次数少了 1、这一帧读不出、这一帧读成 0 而上一组多于 0、上一组一直读不出、只换了一两个、这一帧或上一组有没认出的、只重查了绿框（沿用上一次完整识别的读数）、原文没变只有名字变了（用户改正了候选）；识别流程问的结论和记账自己的一样。上一组最后一帧刷新按钮被挡住时比的是确实读到的次数；同一组里读数有出入时按多的比（`test_count_dropped_before_names`：点了刷新、新的候选还没换上的那一帧次数已经少了 1，换上来的一组仍算刷新）；刷新过以后紧接着出现另一次时比的是刷新后那一组读到的次数，不带上刷新前那一组的（`test_new_select_after_refresh`）；用户改正候选（原文没变、只有名字变了）不清掉这一组确实读到过的次数，改正那一帧刷新按钮正被挡住时之后的真刷新照旧是刷新（`test_fix_keeps_count_read`）。同一次连着的界面里换上来的一组（刷新，或紧接着的另一次）、上一组确实读到过 2 次以上时，这一组读成 0 按没读到算（`_zero_unread`）：刷新成的一组只认一次、正赶上按钮被挡住，再刷新成三张全换的一组照旧是刷新（`test_zero_after_refresh_unread`）；紧接着的另一次刚出现读成 0 也按没读到算，补认读到的照常记（`test_zero_on_next_select_unread`）；上一组只读到过 1 次的，读成 0 是真的用完，照旧记 0、之后三张全换的是另一次（`test_zero_after_one_is_real`）；界面关掉以后的下一次、换局以后的第一组不拿上一组来比（`test_zero_after_close_counts`）。已知局限：另一次刚出现那一帧读成 0 时当刷新，之后补认读对也不改判；上一组只读到过 0 次时（次数用完了，或界面关掉、换局以后的第一组按钮一直被挡住），三张全换的就算另一次。
  - `RemoveChipTest`：移出一个筹码只影响那一个（补录行、待补录数目、面板提示保留）；面板提示是否还开着。撤销移出（`restore_chip`）：移出再撤销以后，已有筹码和先后、来源、不经三选一的记号、新一局的撤销点、和已有筹码有关的各项都和移出前逐项相同，局面版本加一（`test_restore_puts_back_exactly`，放回第一个、最后一个也一样 `test_restore_first_and_last`；同一组候选还开着时撤销照样放回 `test_restore_while_same_offer_open`）；移出以后又记下、补录、移出了别的，面板核对过，出现了新的三选一，判了新的一局、撤销了新一局时放不回、什么都不改（`test_no_restore_after_other_changes`、`test_only_latest_removal`、`test_no_restore_after_undo_new_game`）。
  - `SaveWhenChangedTest`：只在要存的设置真的变了时才存；存盘函数返回「没写成」时不算存成，内容没变也再试。
  - `RecorderErrorTest`、`EventSeqTest`：识别记录器出错不影响记账、只在第一次出错时记一条警告；每个事件带递增的序号（悬浮窗据此认出晚到的旧推荐）；推荐事件带记账里的第几次三选一（`select_no`），诊断记录里也写上，别的事件没有这一项（`test_select_no_in_record`）。
  - `OutOfGameTwiceTest`：对局状态还在时，房间、「匹配成功」画面、奖励画面要隔一会儿（同一帧上认出两次只算一次）、20 秒内连续两次读到才结束本局；之后开的新一局：结算画面结束的不能撤销、其余的能撤销，撤销时连同「已结束」一起撤回并把收起的提示放回。
  - `MainMenuTest`：对局中途回到主菜单（中途退出、断线）：同房间，隔一会儿连续两次读到才结束本局，依据「检测到主菜单」，只读到一次就回到对局画面的不算；读到主菜单时之前「匹配成功」画面留下的读数作废。之后连续两次读到不比离开时小的轮次（中间没读到别的对局外画面、离开不到 `REJOIN_MAX_S`）：不开新的一局，撤回那次结束接着记（`status` 的 `cause` 为 `rejoined`、识别记录 `read:rejoined`），筹码、星数、进度上限都在；第 1 轮离开又读到第 1 轮也接着记（已知代价，有意如此）；接回以后认出别的角色照常判新的一局、刚接回来时不拿轮次否决，认回这一局的角色以后照旧否决。读到了组队房间（结束以后读到的，或结束本局的两次里先认出的是房间、随后认出主菜单）、轮次比离开时小（从开局看起、不提示面板）、离开太久、后来又读到结算画面的照常开新的一局。
  - `ChipMapConflictTest`：加载画面读到的地图和地图专属筹码推出的地图冲突时，要专属筹码拿到了手、或两次不同的三选一都指向同一张地图才改；同一组候选在画面上停多少帧只算一次。
  - `ConsecutiveSelectFlowTest`、`RoomRecheckFlowTest`：识别流程里交给记账的两处——接连两次三选一时读星数；认出对局外画面以后再认一次（看见轮次标记以后第二次起读不到、离那次标记不到 `pick_grace_ms` 时先不认，时刻按默认 10 秒排）。
- **检证对象**：`advisor/session/core.py` 的 `Session`（`new_game`、`undo_new_game`、`on_frame`、`on_out_of_game`、`_chip_map_confirmed`、`_select_opened`、`_select_closed`、`remove_chip`、`can_restore_chip`、`restore_chip`）、`advisor/session/newgame.py` 的 `NewGameJudge`（`pending_votes`、`note_low`、`regressed`）、`advisor/session/selection.py` 的 `SelectTracker`（`first_star`、`just_refreshed`、`hold_frame`、`starts_another`、`fresh_offer`）、`advisor/session/offgame.py` 的 `on_out_of_game`/`end_game`、`advisor/session/events.py` 的 `Event.seq`、`Event.select_no`、`_event_record`；识别流程侧是 `advisor/vision/pipeline.py` 的 `FrameProcessor`（识别函数全部换成假的）。
- **什么时候要跑**：改了 `advisor/session/` 下判新的一局的规则、撤销、三选一的去重键与刷新保持、对局结束、事件序号，或 `advisor/vision/pipeline.py` 里读星数 / 复查对局外画面的时机以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_session_regressions`（上次约 10 秒）
- **需要什么**：不需要识别样本截图、不需要显卡。
- **输出怎么看**：同总则。多数用例逐句断言事件种类和状态，失败时会指出期望的事件与实际的事件。
- **出错时一般是哪里不对**：
  - `OutOfGameTwiceTest.*` 失败 → `advisor/session/offgame.py` 的对局结束与接下来的新一局判定（`end_game`、`round_after_game`、`confirmed`）。
  - `MainMenuTest.*` 失败 → 主菜单那一行规则（`offgame.py` 的 `SCREEN_RULES`、`ScreenRule.rejoin`）、连回同一局的条件和撤回（`rejoinable`、`rejoin`、`REJOIN_MAX_S`，`core.py` 的 `_revoke_end`），离开以后读到别的画面记的那一笔（`GameState._left`，结束本局的两次里先认出的是别的画面时由 `offgame.end_game` 记），或换角色的否决（`core.py` 的 `on_hero`、`GameState._rejoined`）。
  - `ChipMapConflictTest.*` 失败 → `advisor/session/core.py` 的 `_chip_map_confirmed`、`_map_from_chips`（「两次不同的三选一」那一票）。
  - `OfferKeyTest.*` / `RefreshAgainTest.*` / `ConsecutiveSelectStarTest.*` 失败 → `advisor/session/selection.py` 的 `SelectTracker`（`_unchanged`、`just_refreshed`、`first_star`、`table_row`、`starts_another`）。
  - `SameRowSelectsTest.*` 失败 → 「紧接着的另一次还是刷新」的判据（`selection.py` 的 `starts_another`、`fresh_offer`，上一组确实读到过的最多的刷新次数 `_offer_most`；`test_new_select_after_refresh`、`test_fix_keeps_count_read` 失败多半是 `frame` 里换了一组候选时没清掉它、或改正候选时清掉了它；`test_zero_*` 失败多半是换上来的一组读成 0 时记不记它的条件改了：`frame` 里的 `_zero_unread`，上一组算不算同一次连着的界面、同一局），或认作另一次以后的结算和计数（`frame` 里的 `close`、`core.py` 的 `_select_closed`、`_select_opened`、`_open_judged`，事件的 `select_no`）。`test_unsure_stays_refresh` 的哪一个子用例失败，就是那一种拿不准的情形被认成了另一次。
  - `JumpTwiceTest.*` / `PendingWindowTest.*` 失败 → `advisor/session/newgame.py` 的 `read_round`、`read_star`、`pending_votes`。
  - `EventSeqTest.*` 失败 → `advisor/session/events.py` 的 `EventLog.event`、`push`（序号只增不减），或 `_event_record` 没写上 `seq`、`select_no`。
  - `SaveWhenChangedTest.*` 失败 → `advisor/session/core.py` 的 `save`、`_settings_content`（只在设置变了时才存）。
  - `RemoveChipTest.test_restore_*`、`test_no_restore_*`、`test_only_latest_removal` 失败 → `advisor/session/core.py` 的 `remove_chip`（移出时记下的 `ChipRemoval`）、`_removal_mark`、`can_restore_chip`、`restore_chip`：放回的不是移出前的样子，或者之后已有变化时还放回了；`set_owned` 多改了别的状态时，这里也会先失败。
  - `RecorderErrorTest.*` 失败 → `advisor/session/events.py` 的 `to_recorder`、`core.py` 的 `_to_recorder`（出错只记一条警告）。
  - 用例里写死的说明文字（「检测到组队房间」这类）是有意改的，就一起改预期。

## test_scheme_map_switch.py：对局里按地图自动换筹码方案（记账这一层）

- **测试观点**：从三个测试类检查——① `SessionMapSchemeTest`：这一局实际用哪套每次现算（`Session.scheme_choice`）——保存的是通用方案、这个角色有两套标了这张地图时用方案下拉框里靠前的那一套；保存的标了这张地图就照用；保存的只标了别的地图时到这张地图换成标了它的、改判到谁都没标的用保存的；没有编号、不能标在方案上的地图（运动场）不换；别的角色标了这张地图的方案不算进来，换了角色按那个角色自己的方案算。`stop_map_scheme` 之后这一局都用保存的方案（局面版本加一），还没有这一局的对局状态时不打；新的一局清掉，撤销新的一局时连同告诉过的那一次回来、不重复告诉。本局结束回到保存的方案，新的一局认出地图以后照常再换。地图改判时每换上一套告诉一次（`take_map_switch`），回到保存的不告诉，之后再换过去照样再告诉。保存的是默认推荐时同样换成标了这张地图的方案。推荐按这一局实际用的那套算（`scheme_plan`），三选一开着时认出、改判地图立即重算；地图从三选一里的地图专属筹码认出来的那一帧就按换成的方案算。没有接上方案存储、只接了 `scheme_for`、角色资料库未收录时都是默认推荐、不换。② `OverlayMapSchemeTest`、`SchemeMapNoticeTest`（悬浮窗这一层：重算与一次性提示、「筹码方案」卡片那一行和「换回原来的」、方案下拉框的「适用地图」悬停说明）见[悬浮窗](overlay.md)的同名一节。
- **检证对象**：`advisor/session/core.py`（`SchemeChoice`、`scheme_choice`、`scheme_plan`、`_scheme_map`、`stop_map_scheme`、`take_map_switch`）、`advisor/session/game_state.py`（`map_scheme_off`、`_map_switch_told`）、`advisor/schemes.py`（`Scheme.maps`、`taggable_maps`、`SchemeStore.current`、`SchemeStore.schemes`）；悬浮窗那一层见[悬浮窗](overlay.md)的同名一节。
- **什么时候要跑**：改了 `Session.scheme_choice` 的取舍规则、`stop_map_scheme` 与 `take_map_switch`、`GameState` 里按地图换方案的状态，或方案存储给这一局的方案以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_scheme_map_switch`（上次约 3 秒）。
- **需要什么**：不开窗口的几组只读资料库；悬浮窗那一层用 Qt 离屏平台；方案文件写在测试沙箱的临时文件夹里。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `test_generic_saved_switches_to_first_tagged`、`test_saved_tagged_kept`、`test_saved_tagged_other_map`、`test_default_saved_switches` 失败 → `scheme_choice` 的取舍规则变了（保存的方案、标了这张地图的第一套、方案下拉框的先后）。
  - `test_stop_map_scheme`、`test_undo_keeps_told` 失败 → 「这一局不再按地图换」的打标记、新的一局清掉、撤销时跟着回来改了。
  - `test_game_over_and_new_game`、`test_map_rejudged` 失败 → 本局结束回到保存的方案，或 `take_map_switch` 告诉一次的去重改了。
  - `test_advice_follows_used_scheme` 失败 → 推荐按这一局实际用的那套算、认出或改判地图时重算的时机改了。
  - `test_untaggable_map`、`test_without_store` 失败 → 能标在方案上的地图、没有方案存储时的退路改了。

## test_session_split.py：记账包的结构保证

- **测试观点**（按测试类分）：
  - `FieldGuardTest`：各部分的状态字段各自放在对应的对象上（这一局的状态、留给下一局的读数、三选一状态机、判新的一局、事件记录），名字不重复、`Session` 类上没有被同名的属性挡在前面；把状态字段名写到 `Session` 上会报错（漏改的写当场报出来），写到放它的对象上照常。
  - `GameStateSwapTest`：新的一局换上一整套新对象（除了「查看筹码」面板的两项照旧，各项都是默认值；留给下一局的畸变组件和难度用上、地图留着），撤销换回同一个旧对象、设置里的对局字段和角色也恢复；判新的一局要跨局比较的读数不在「这一局的状态」里，新的一局不清、撤销不退；事件的序号和记录跨局；撤销点里的已有筹码另存一份；深拷贝能分叉再回放。
  - `ScreenRulesTest`：对局外画面的规则表推出的叫法、三种房间、难度依据的叫法和测试里写死的预期逐项相同（连先后）；难度的画面读数按表里的优先级取；加一种画面只要在表里加一行。
  - `EventKindsTest`：记账各模块里建的事件种类就是事件种类表里的那些，不多不少。
  - `AdvisePatchTest`：把推荐引擎换成假的要换在「调用它的模块」上（按名字导入的那处）；换到包的顶层（`advisor.session.advise`）或 `advisor.session.core` 上会直接报错，不会悄悄失效。
  - `UndoAfterReadingsTest`：判了新的一局、新的一局里又读到好几帧再撤销——换回来的就是判定之前的那两个对象，逐项和判定之前相同（只差撤销的规矩调整的几项）；新的一局里的读数都写在新对象上，两局共用了哪个列表、字典、集合这里会报出来。
- **检证对象**：`advisor/session/__init__.py`、`core.py`（字段归属表与 `Session.__setattr__` 的守卫）、`game_state.py`（这一局的状态、留给下一局的读数、撤销点）、`selection.py`、`newgame.py`、`events.py`、`offgame.py` 的规则表。
- **什么时候要跑**：给对局状态加一项新字段、把状态挪到别的对象上、改状态字段的存放位置、改对各部分字段名的守卫、加一种对局外的画面、加一种事件种类、改推荐引擎的导入方式以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_session_split`（上次约 0.8 秒）
- **需要什么**：不需要识别样本截图、不需要显卡。它会读 `tests/test_resume.py` 和 `tests/test_session_fuzz.py` 的源码（找推荐引擎的替换点），所以这两个文件不要挪走或改名。
- **输出怎么看**：同总则。字段守卫的失败会指明是哪一个字段名。
- **出错时一般是哪里不对**：
  - `FieldGuardTest.test_every_field_on_holder` 失败 → 加了状态字段却没在 `core.py` 的字段归属表里登记，或两个部分用了同一个字段名。
  - `FieldGuardTest.test_write_to_session_is_blocked` 失败 → `Session` 的写入守卫被改坏，漏改的写不会当场报错。
  - `GameStateSwapTest.test_new_game_takes_fresh_objects` 失败 → 新的一局没有换上全新对象，或该清空的没清、该留的没留（面板提示、畸变组件、难度、地图）。
  - `GameStateSwapTest.test_undo_takes_back_same_objects` 失败 → 撤销点没存好、或撤销没换回原来的对象、或设置里的对局字段没一起恢复。
  - `ScreenRulesTest.test_derived_tables` 失败 → 规则表改了，推出的叫法跟着变；有意改的话把预期一起改（界面上显示的就是这几个叫法）。
  - `EventKindsTest` 失败 → 新加的事件种类没写进事件种类表，或写的种类拼错了。
  - `AdvisePatchTest.test_old_targets_fail_loudly` 失败 → 记账包的顶层又出现了推荐引擎那个名字，测试里的替换点会悄悄失效。
  - `UndoAfterReadingsTest.*` 失败 → 新的一局和旧的一局共用了同一个列表、字典或集合（新的一局该用新对象里的新容器）。

## test_game_outcome.py：一局的胜负（通关还是失败）在记账里怎么定

- **测试观点**（按测试类分）：
  - `DecisionTableTest`：`offgame.outcome` 的判定表，直接摆这一局的几项依据：结束前 60 秒之内读到通关画面算通关、依据写 `victory`（正好 60 秒也算，早于 60 秒的不算）；只看到打输的横幅算失败、依据写 `game_over`，星币是 0（和电脑打的局输赢都不给星币）、10、20、读不到都一样，星币 ≥ 25 时两边矛盾；通关画面和横幅都看到也算矛盾；没看到这两种时按奖励画面上的星币数，≥ 25 通关、正好 10 失败，依据写 `reward`；别的数都不知道——0、读漏一位的 +3（+30）、+5（+35）、+1（+10）、15、24；通关画面加星币 10 是矛盾，两项都不知道；什么都没有不算失败；还没结束时不知道。阈值本身（25、10、60 秒）和三种依据的叫法另核对一遍。
  - `OutcomeFlowTest`：真实的 Session（打到第 12 轮，假时钟）：通关画面、打输的横幅都不发事件，各记一条 `read:victory`、`read:game_over_banner`；结算画面结束本局时就按看到的画面记通关或失败，`game_over_info` 带 `outcome`、`how`；横幅那一局奖励画面星币是 0 也照样是失败，奖励画面星币 ≥ 25 时改成矛盾；结束以后第一次读到奖励画面算定了（`result_settled`），胜负没变也发一个 `status`、再读到不再发；胜负变了改掉、发 `status`、记一条 `read:game_result`；只有奖励画面时按星币数定（30、25 通关，10 失败，0、3、20、读不出不知道）；星币数取读到过的最大的（+3 之后读到 +30 改成通关，之后读到 10 不再改回去；+1 之后读到 +10 才算失败）；星币数不是非负整数的不算；对局中途认错的一次（结束前一分钟以上，通关画面和横幅都一样）不算；没有对局状态、本局已结束以后读到通关画面、横幅都不记；对局里只读到一次奖励画面不算结束、也不算定了；奖励画面隔一会儿读到两次结束本局时当场用上星币数；房间画面误认的结束、又读到了奖励画面，之后的新一局撤销了，胜负的依据一起作废；新的一局从头算（两种画面的时刻都不带）。
- **检证对象**：`advisor/session/offgame.py` 的 `outcome`、`reward_read`、`refresh_outcome`、`end_info`、`end_game`（记结束的时刻）、`on_out_of_game` 里奖励画面那一段，以及 `REWARD_WIN_COINS`、`REWARD_LOSS_COINS`、`VICTORY_BEFORE_END_S`、`BY_VICTORY`、`BY_GAME_OVER`、`BY_REWARD`；`advisor/session/core.py` 的 `on_victory`、`on_game_over_banner`、`game_over_info`、`result_settled`、`undo_new_game`（胜负的依据一起撤回）；`advisor/session/game_state.py` 的 `_victory_at`、`_game_over_at`、`_ended_at`、`_reward_seen`、`_reward_coins`。
- **什么时候要跑**：改了胜负的判定（阈值、通关画面和横幅的时效、依据的叫法）、奖励画面怎么记、结束和撤销的记账以后；推荐日志和对局报告那一头另跑 test_overlay_event_branches、test_reporting、test_game_report。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_game_outcome`（上次约 2 秒）
- **需要什么**：不需要识别样本截图、不需要显卡；时钟用假的。
- **输出怎么看**：同总则；判定表按情形分 subTest，失败时写明是哪一种。
- **出错时一般是哪里不对**：
  - `DecisionTableTest` 失败 → 判定表改了：阈值、只认正好 10 的失败、矛盾时两项都不知道、通关画面和横幅的时效；有意改的话把表里的期望一起改（读漏一位、和电脑打的局不能记成失败，只看到横幅要记成失败）。
  - `OutcomeFlowTest` 失败 → 胜负什么时候记、什么时候改、什么时候算定了改了；悬浮窗靠第一次读到奖励画面的 `status` 拼报告，这一条丢了报告要等满几分钟才拼。

## test_source_flow.py：三选一是升星还是筹码商店——出现之前的迹象怎么定、关掉以后按星数怎么补准

- **测试观点**（按测试类分，假时钟，自己在玩家列表第 2 行）：
  - `SourceDecideTest`：第一次给推荐时按出现之前收下的迹象定，依据写在推荐事件上（`source_by`）。出现之前不久读到自己的升星横幅（横幅上的名次对得上自己那一行）算升星、`banner`；队友的横幅、还不知道自己在第几行时的横幅不算，拿不准、`guess`，同一次三选一里认出了自己那一行就按横幅重定、局面没变也重发一次推荐，之后照常不重复发（`test_banner_counts_once_own_row_is_known`）。看到购买窗算筹码商店、`shop_window`；横幅和购买窗都有、星数涨了又看过购买窗，都按升星。横幅、购买窗超过 `SOURCE_CUE_S` 的不算；一个迹象只算给它之后的第一次三选一：关掉以后紧接着的下一次、先来了一次全队任务三选一以后的下一次都不算（`test_cue_counts_for_the_next_select_only`、`test_cue_before_a_task_select_is_void`）。出现之前玩家列表上的星数涨了（上一次是任务的三选一、中间没读到过星数时也算，`test_star_gain_after_task_select`；那次任务之前还有一次拿不准、之后没读到过星数的，涨的也可能是那一次的，不算，`test_star_gain_after_task_with_unseen_select_before`）、三选一出现的那一帧在三选一画面上读到多 1 星、后来才读到多 1 星（重发推荐）都算升星、`stars`；上一次三选一关掉以后没读到过星数时，三选一画面上多的 1 星也可能是上一次升的，不认，照旧拿不准（`test_star_up_in_select_not_counted_after_unseen_select`）。出现之前已经是 3 星算筹码商店、`stars`，刚升到 3 星的那一次是升星。0 星时自己的横幅之后出的按 1 星算、购买窗之后出的按 0 星算，刷新次数怎么读都一样，记下的星数不改。概率行认出是任务的照旧（`rows`），横幅不改它，这一局非任务三选一的次数不变、关掉以后也不补。同一组候选关了又出现，仍按出现之前收下的购买窗定，补准时用又出现时的编号。名次不合理（0、9、真假值、空、文字）、本局结束以后读到的横幅和购买窗不记，这一帧购买窗的价格读不出时沿用刚才读到的。诊断记录里推荐写上 `source_by`，补准事件写上 `select_no`、`source`、`source_by`。
  - `SourceSettleTest`：拿不准的、按购买窗定的，关掉以后第一次在对局画面上读到星数时补准（`source_settled`，`source_by` 为 `stars`）：涨了 1 星是升星，没涨是筹码商店，补过的不再补；按购买窗定的、星数涨了也补成升星。三选一刚看不到、还没按关掉处理时读到的星数先不用，关掉以后的下一次再补。两次三选一之间没读到星数：涨的星数和次数相同都是升星、一颗没涨都是筹码商店，只涨了 1 星时分不出、都不补，这一段清掉以后也不再补。拿不准的一次关掉以后自己升星（横幅之后星数先涨、三选一随后才出）：先不对账，等升星那一次关掉以后再对，横幅定的那一次扣掉，之前那一次没涨、是筹码商店（`test_star_up_right_after_own_banner_waits_for_its_select`）；横幅之后过了 `SOURCE_CUE_S` 那次三选一还没出时不补。概率行比记下的星数正好高一星（按刚升星算的那种）、关掉以后星数却没涨的，拿不准的、按购买窗定的都不补成筹码商店，留着拿不准，同一段里概率行对得上当时星数的照常补（`test_row_one_star_up_without_star_up_not_shop`）。本局结束以后读到的星数不补，开了新的一局，上一局没补上的不再补。
- **检证对象**：`advisor/session/core.py` 的 `Session`（`on_levelup_banner`、`on_shop_window`、`_take_cues`、`_source_of`、`_levelup_or_shop`、`_rose_in_select`、`_select_star_seen`、`_own_banner_age`、`_settle_sources`，`_select_opened` 里记下的这一次三选一，`on_stars` 交来的出现之前的星数）和 `SOURCE_CUE_S`；`advisor/session/selection.py` 的 `SOURCE_BY`、`SETTLE_BY`、`SelectTracker.first_star`、`SelectTracker.frame`（去重键里带依据）；`advisor/session/game_state.py` 的 `_banner_cue`、`_shop_cue`、`_select_rec`、`_source_books`；`advisor/session/events.py` 的 `Event.source_by`、`Event.source`、`_event_record`。
- **什么时候要跑**：改了升星还是筹码商店的认法（迹象的时限、先后、依据的取值）、关掉以后怎么补准、`first_star`、三选一出现和关掉时的记账、星数读数的处理以后。识别那一头怎么读横幅和购买窗、什么时候交给记账，另跑识别和识别流程的测试。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_source_flow`（上次约 12 秒）
- **需要什么**：不需要识别样本截图、不需要显卡；只读 `data/` 资料库，时钟用假的。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `SourceDecideTest.*` 失败 → 定来源的先后和条件（`core.py` 的 `_source_of`、`_levelup_or_shop`、`_rose_in_select`、`_take_cues`、`SOURCE_CUE_S`），或横幅名次和自己那一行的对法（名次从 1 数，`own_row` 从 0 数）；`test_zero_stars_*` 失败 → `selection.py` 的 `first_star` 没按定下的来源和依据判；重发推荐的两条（`test_banner_counts_once_own_row_is_known`、`test_star_up_read_later_re_advises`）失败 → `SelectTracker.frame` 的去重键里没有依据。
  - `SourceSettleTest.*` 失败 → `core.py` 的 `_settle_sources`（起点、扣掉已认准的升星、先不对账的几种情形、横幅没等来三选一时不补、概率行高一星的不补成筹码商店）、`_source_of` 记下的 `row_up`、`_select_opened` 记下的出现之前的星数和扣到这一次头上的星数增加（`_gain`），或 `on_stars` 里调用它的条件（只用采用了的读数，本局结束以后不调）。
  - 有意改了规则（时限、先后、补准的条件），把这里的预期一起改；`test_session_tape` 的期望也会跟着变，照那一节重新生成。

## test_session_tape.py：记账的读数磁带回放——一盘真实对局的读数按顺序交给记账，每一步的结果和存下来的期望逐字相同

- **测试观点**（按测试类分）：
  - `SessionTapeTest.test_replay_matches_expected`：把 `tests/data/baseline/session_switch.tape.json` 这盘磁带（一局真人对局的识别读数：「匹配成功」画面之后的开局读数加 39 帧、3 次三选一，按识别流程的顺序交给记账的每一次调用）回放一遍，每一步的返回值、取走的事件、交给识别记录的内容和对外看得到的状态里变了的项，与 `tests/data/baseline/session_switch.expected.jsonl` 逐行逐字比。
  - `SessionTapeTest.test_replay_is_repeatable`：同一份磁带在同一个进程里放两遍结果相同（不依赖上一遍留下的缓存和全局状态，也不改动读进来的磁带）。
  - `TaperTest.test_self_check_catches_missing_input`：录磁带那一层的自查能查出「漏录了只改状态的调用」；驱动直接写的属性录成赋值的一步。
  - `TaperTest.test_other_thread_is_refused`：录的时候别的线程也调用记账 → 报错、不写磁带（各线程的先后每次不同，磁带重现不了）。
  - `TaperTest.test_paths_become_placeholder`：识别记录里的绝对路径写成同一个占位，所以路径不同的两盘回放出来逐字相同；别的带冒号的文字不动。
  - `TaperTest.test_replay_leaves_doc_untouched`：回放用拷贝，磁带里的开局设置不变。
  - `TaperTest.test_without_git`：找不到 git（PATH 里没有、环境变量 `GIT` 也没设）时照样录，磁带里不记提交号和改动状态（`meta.commit`、`meta.dirty` 都是 `None`），自查照过。
  - `SampleCheckTest`：录磁带前先查要录的组里有没有用识别样本的（`SAMPLE_GROUPS`）、样本又不在时给一句中文说明并退出；`BASELINE_QUICK=1` 只用的那一组（`QUICK_GROUPS`，现在只有 `switch`）不用样本，单独录它照常录；要录 replay 一组、截图序列回放工具（`REPLAY_TOOL`）又不在时同样先给一句说明并退出（`test_replay_group_needs_tool`）。
  - `CompareTest`：比对报告写清哪一盘、哪一步、哪一项从什么变成什么；两次用的磁带不同（中间重录过）时这一组只报一条，写明要用现在的磁带重录基准。
- **检证对象**：`advisor/session/` 全部（记账的对外行为）、`advisor/session/events.py` 的 `_event_record`；驱动与回放的实现是 `tools/baseline/session.py`（`tape_doc`、`replay`、`replay_lines`、`encode`、`self_check`、`compare`、路径占位，以及录磁带前查样本的 `_check_samples` 和组名单 `GROUPS`、`SAMPLE_GROUPS`、`QUICK_GROUPS`），磁带的格式和「每一步记什么」也由它定义；找不到 git 时提交号的记法在 `tools/baseline/env.py`（`GIT`）。磁带和期望都是版本库里的文本文件。
- **什么时候要跑**：改了 `advisor/session/` 下任何会影响记账结果的东西（事件、状态字段、判定的时机、撤销）以后。它把一局真人对局的记账在几秒内重跑一遍，是最灵敏的一项。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_session_tape`（上次约 1.5 秒）
- **需要什么**：不需要识别样本截图、不需要显卡；只读 `tests/data/baseline/` 下的磁带与期望文件（`test_without_git`、`SampleCheckTest` 也不用样本）。
- **输出怎么看**：失败时打印的是「哪一步的哪一项和期望不同：期望…… 实际……」，直接看两边的差别。记账规则有意改动以后，用 `python -B -m tools.baseline.session update-test` 重新生成期望，用 `git diff` 看变了哪几步、确认都符合预期再一起提交；改了磁带的录法时再加 `--tape`（连磁带一起重录）。
- **出错时一般是哪里不对**：
  - `test_replay_matches_expected` 失败 → 记账的改动让某一步的返回值、事件或状态变了。先看差异落在哪一步、哪一项，判断是有意改的还是改坏了。
  - 失败信息里整段步号错位 → 磁带与期望被更新成了一对不匹配的（只重录了磁带，或只重新生成了期望）。
  - `test_replay_is_repeatable` 失败 → 记账里留下了跨次运行的状态（模块级变量、缓存），或回放改动了读进来的磁带。
  - `TaperTest.test_self_check_catches_missing_input` 失败 → 录制层的自查不再靠「这一步之后的状态和识别记录」比对（`tools/baseline/session.py` 的 `self_check`）。
  - `TaperTest.test_other_thread_is_refused` 失败 → 录制层不再拒绝别的线程。
  - `TaperTest.test_paths_become_placeholder` 失败 → 路径占位的替换规则或写法变了。
  - `TaperTest.test_without_git` 失败 → 找不到 git 时不再照样录，或把提交号记成了空字符串而不是缺省（`tools/baseline/env.py` 的 `GIT`）。
  - `SampleCheckTest.test_groups_needing_samples` 失败 → 要样本的组名单（`SAMPLE_GROUPS`）、`QUICK_GROUPS` 与 `GROUPS` 的关系，或 `_check_samples` 的提示文字改了。
  - `SampleCheckTest.test_replay_group_needs_tool` 失败 → `_check_samples` 不再先查 replay 一组要的回放工具（`REPLAY_TOOL`），或不录这一组时也拦了。
  - `CompareTest.*` 失败 → 比对报告的措辞或格式变了。

## test_gamelog.py：识别记录（`logs/games` 下的分段文件）的单元测试

- **测试观点**（按测试类分）：
  - `WriteTest`：段头带版本号；不到 60 秒、不到 500 行时不写盘，到时写；记下筹码、新的一局这些要紧的节点马上写；记录的时刻由调用方给；标记马上写且不换段；多个线程同时记不丢。
  - `SegmentTest`：换段产生新文件，导出只取上一段和当前段，按时间有序、各段以段头开始；中途重启时上一段是上次运行留下的最后一段；撤销换段后只剩一个文件（原段 + 撤销那一行 + 新段的记录）；同一秒重名时文件名加 `-2`、`-3` 且始终排在已有的段之后；单段超过体积上限自动换段。
  - `CollectStatsTest`：只留统计类的记录（对局里逐帧的读数、推荐不带）；只留最近 12 小时以内的、`time` 读不出来的不要，系统时间被调回以后比现在还晚的照样留；哪一段有留下的记录就带上那一段的段头（段头早于 12 小时也带），只有逐帧读数、段头又早于 12 小时的那一段整段跳过；窗口层次记录去掉盖住悬浮窗的那个窗口的类名、其余各项照带，导出本局和上一局时照旧带着它；当前段还没写盘的行也在；写盘出过错时内存里的当前段也在；同一个文件不读两遍；读坏的文件跳过、原因记进 `errors`。
  - `PruneTest`：只保留最新 10 段；总量超过上限时从最旧的删；当前段和之前的两段受保护。
  - `MaskTest`：写入前脱敏——玩家列表读数里的名字、任何位置的玩家名字、以及「X正在选择强化筹码」里的 X 都换成代号（同名同码、跨运行不同），电脑玩家的标记保留，不改原记录；已经是代号的不再变。
  - `FailureTest`：文件夹不可写时不抛异常、只报一次错，内存里照常记录、导出不受影响；撤销换段在内存里也成立；程序崩溃时写到一半的行读回时跳过，下一次运行照样能导出上一段；常量（保留段数、写盘间隔、文件名格式、识别记录的位置）。
  - `test_stamp_format`：时刻的写法（毫秒，和 `recent_events` 的 `time` 相同）。
- **检证对象**：`advisor/gamelog.py` 的 `GameRecorder`（`open`、`new_segment`、`undo_segment`、`add`、`mark`、`flush`、`collect`、`collect_stats`、`prune`）、`read_segment`、`mask_record`、`player_code`、`stamp`、`STATS_KINDS` 与各常量；位置来自 `advisor/paths.py` 的 `games_dir`（`logs_dir/games`）。
- **什么时候要跑**：改了 `advisor/gamelog.py`、`advisor/paths.py` 里识别记录的位置、`advisor/fileio.py` 的 `append_lines`（写盘时补换行）、版本号（段头里写着它）以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_gamelog`（上次约 0.4 秒）
- **需要什么**：不需要识别样本截图、不需要显卡；临时文件写在测试沙箱里。
- **输出怎么看**：同总则。用例把文件读回来核对，失败时看断言里的期望与实际行。
- **出错时一般是哪里不对**：
  - `WriteTest.test_header_and_buffered_writes` 失败 → 写盘时机的三个条件或段头字段变了。
  - `SegmentTest.test_collect_previous_and_current` 失败 → 导出的范围（只取上一段和当前段）变了。
  - `SegmentTest.test_undo_segment` / `test_undo_restores_older_previous_segment` 失败 → 撤销换段的写法（去掉新段段头、中间一行撤销记录、删掉新段文件、接回上一段）变了。
  - `SegmentTest.test_size_limit_starts_new_segment` 失败 → 单段体积上限或满时的换段变了。
  - `PruneTest.*` 失败 → 保留段数、总量上限或受保护的段变了。
  - `MaskTest.*` 失败 → 脱敏规则变了（名字代号的写法变了要把断言里的正则一起改）。
  - `FailureTest.test_unwritable_folder` 失败 → 写盘出错的处理（只报一次、本次运行不再写盘、内存里照常记录）变了。
  - `FailureTest.test_constants` 失败 → 常量或识别记录的位置改了；有意改的话把断言一起改（诊断包和界面上的说明也会跟着改）。

## test_gamelog_durability.py：识别记录的写盘加固——断电、半行、坏字符时只丢坏的那一点

- **测试观点**（按测试类分）：
  - `EncodingTest`：一条记录里混进写不成 UTF-8 的字时只丢掉这一条，其余记录照常写盘、读得回来；错误只记一次（不会每帧都记）。
  - `HalfLineTest`：文件末尾是写到一半的半行时，下一条先补一个换行再写（新记录读得回来，半行仍被跳过）；末尾是一串零字节（断电）时同样。
  - `SplitLineTest`：含 U+2028、U+2029、U+0085 的记录不被按换行切开、整条读回来（用 `str.splitlines()` 会被切开丢掉）。
  - `NulTest`：正文里的零字节写成转义，读的时候只去掉行首尾的零字节，不影响它。
  - `UndoAppendTest`：撤销换段时接回的上一段是上次运行留下的、末尾是写到一半的半行——同样先补换行，撤销那一行和移过去的记录都在。
- **检证对象**：`advisor/gamelog.py` 的 `GameRecorder`（坏字符只丢一条、写盘前补换行、撤销换段的追加）、`read_segment` 与它用的切行，以及被它调用的 `advisor/fileio.py` 的 `append_lines`。
- **什么时候要跑**：改了 `advisor/gamelog.py` 的写盘与读回、`advisor/fileio.py` 的 `append_lines` 以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_gamelog_durability`（上次约 0.1 秒）
- **需要什么**：不需要识别样本截图、不需要显卡；时钟用假的，临时文件放在测试沙箱里。
- **输出怎么看**：同总则。用例用真正的读法把文件读回来，失败时断言里是读回来的轮次或其他字段。
- **出错时一般是哪里不对**：
  - `EncodingTest.test_undecodable_record_drops_only_that_line` 失败 → 坏字符的处理变了（把这一行留在待写队列里，之后每次写盘都会出错，本次运行一条都写不进去）。
  - `HalfLineTest.*` / `UndoAppendTest` 失败 → 写盘前补换行的做法（`advisor/fileio.py` 的 `append_lines`）或撤销换段的写法变了。
  - `SplitLineTest.*` / `NulTest.*` 失败 → 读回时的切行方式改了（改成 `str.splitlines()` 就会切开含 U+2028 的记录）。

## test_logbook_durability.py：推荐日志的写盘加固——写不进时不一停到底、坏行不影响整月

- **测试观点**（按测试类分）：
  - `HalfLineTest`：日志末尾是半行时，下一条先补换行再写。
  - `WriteRetryTest`：写不进（文件被别的程序独占、没有权限）以后不一停到底——这一回第一次写不进照常报错（调用方记进错误记录）；之后 60 秒内的记录直接丢掉、不去写、不报错；到时再试一次，还写不进也不再报错；写进了这一回就结束，下一回第一次写不进再报一次。
  - `SplitLineTest`：含 U+2028、U+2029、U+0085 的记录不被按换行切开。
  - `DamagedTailTest`：正文里的零字节、末尾断电留下的一串零字节都不影响读回；最后一条写到一半、断在一个汉字的中间时只丢这半条，这个月的其余记录照样读得出。
- **检证对象**：`advisor/logbook.py` 的 `_write`（重试窗口与「同一回只报一次」）、`_month_file`（按月分文件 `logs/YYYY-MM.jsonl`）、`iter_records`，以及 `advisor/fileio.py` 的 `append_lines`。
- **什么时候要跑**：改了 `advisor/logbook.py` 的写盘与读回、`advisor/fileio.py` 的 `append_lines` 以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_logbook_durability`（上次约 0.1 秒）
- **需要什么**：不需要识别样本截图、不需要显卡；写不进和时钟用替换造，临时文件放在测试沙箱里。
- **输出怎么看**：同总则。用例把记录读回来核对，失败时看断言里的记录。
- **出错时一般是哪里不对**：
  - `WriteRetryTest.test_retries_after_a_pause_and_reports_once` 失败 → 写不进以后的重试节奏、或者「同一回只报一次」的记账变了。
  - `HalfLineTest.*` / `DamagedTailTest.*` 失败 → 写盘前补换行、读回时切行、去零字节、容坏字节的做法变了（`advisor/fileio.py`、`logbook.iter_records`）。
  - `SplitLineTest.*` 失败 → 读回时改成按 `str.splitlines()` 切行了。

## test_logbook_game_over_again.py：推荐日志里同一局有两条对局结束时的处理

- **测试观点**：同一局有两处结束（房间等画面结束的局，之后开的新一局被撤销，那次结束是误认的；这一局真正结束时再记一条），要以后一条为准、不把一局算成两局：
  - `test_resume_follows_later_record`：重启时接局号——真正结束之前接回这一局；记了真正的结束以后不再接。
  - `test_counts_one_game`：局数和校准计数按三选一的记录算（对局结束不计入），两条结束也只是一局；诊断包取推荐日志时两条都在、后一条是真正结束的。
  - `test_rejoined_game`：主菜单结束的局又连回同一局（悬浮窗不换局，不调 `new_game`、`undo_new_game`）：之后的三选一记在同一局，真正结束之前重启接回这一局，两条结束只算一局，记了真正的结束以后不再接。
- **检证对象**：`advisor/logbook.py` 的 `record_game_over`、`new_game`、`undo_new_game`、`resumable`、重启时接局号、`stats`、`game_count_since_calibration`、`records_between`；下游读推荐日志的还有校准工具 `tools/calibrate.py`（汇总开头写「局数 1；」，在 `tests/test_dev_tools.py` 的 `CalibrateGameOverTest` 里核对，借这里的 `setUp`、`play_undone_end`、`later`）。
- **什么时候要跑**：改了 `advisor/logbook.py` 里局号、对局结束、重启接局、局数统计的部分以后；改了这里的 `play_undone_end`、`later` 时连 `test_dev_tools` 一起跑。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_logbook_game_over_again`（上次约 0.07 秒）
- **需要什么**：不需要识别样本截图、不需要显卡；推荐日志写进测试沙箱里的临时文件夹，时钟用假的。
- **输出怎么看**：同总则。失败时看断言里两条结束记录的轮数、局号、局数。
- **出错时一般是哪里不对**：
  - `test_resume_follows_later_record` 失败 → 重启接局号时对「最后一条是不是对局结束」的判断变了。
  - `test_counts_one_game` 失败 → 局数统计把对局结束当成新的一局算了（局数会变成 2）。

## test_logbook_tail.py：推荐日志开局时倒着读最后一段，结果和整份按顺序读逐位相同

- **测试观点**（按测试类分）：
  - `TailEquivalenceTest`：固定种子造 160 个日志文件夹（几个月份文件、各种记录、坏行、写到一半的末行、一串零字节、断在一个字中间的字节、空文件、只有坏行的文件、开头带 BOM、`\r\n` 换行、记录里含 U+2028/U+2029/U+0085、一行比读块还长、目录冒充文件），倒着读的结果和参照实现（测试里另写的一份整份读法）逐位比；块大小临时改小（7、64 字节）再比一遍，专测跨块拼行。另有单独几条：`\r\n` 与末尾无换行、单独的 `\r` 与混合换行、零字节与只有坏行、记录里的分隔字符、目录冒充月份文件（整份跳过）、两条要找的记录落在不同的文件、日志文件夹不在。
  - `TailMonthBoundaryTest`：月份界限（近 31 天）用假的「现在」测——月初、跨年、界限落在 3 月。
  - `TailUnreadableMidFileTest`：读某个文件中途出错时，这个文件里已经读出来的结果也作废、接着读更早的文件；并核对「出错不作废」这种改坏会被抓到。
  - `TailSensitivityTest`：把倒着读故意改坏（按块解码、只认最后一条记录当「最后一条带时刻的记录」）时和参照实现有差异，说明上面那一批随机文件夹确实抓得住这类改坏。
- **检证对象**：`advisor/logbook.py` 的 `_scan_log`、`_scan_file`、`_tail_lines`、切行的辅助、按月界限的取舍、读块大小；结果供重启接局号使用。
- **什么时候要跑**：改了 `advisor/logbook.py` 里倒着读日志的部分，或改了整份读（`iter_records`）的读法口径以后——两者必须一致，这个文件就是钉住这一点。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_logbook_tail`（上次约 22 秒）
- **需要什么**：不需要识别样本截图、不需要显卡；日志文件夹建在测试沙箱里，时钟用假的，随机造数据用固定种子。
- **输出怎么看**：同总则。失败时断言里是「倒着读」和「参照实现」两边的值（一对：最后一条三选一那类的记录、最后一条带时刻的记录的时刻）；子测试名里有文件夹名、假的「现在」和块大小。
- **出错时一般是哪里不对**：
  - `TailEquivalenceTest.*` 失败 → 倒着读的结果和整份读不一致（切行、解码、拼跨块的行、月份界限、读不了的文件，其中之一）。
  - `TailMonthBoundaryTest.*` 失败 → 近 31 天的界限，或「按月整份读」的取舍变了。
  - `TailUnreadableMidFileTest.test_partial_results_from_a_failing_file_are_dropped` 失败 → 读某个文件中途出错时的作废规则变了（不作废会留下这个文件里已经读到的值）。
  - `TailSensitivityTest.test_corpus_distinguishes_broken_variants` 失败 → 这一批随机文件夹不再能抓住「按块解码」「只认最后一条记录」这类改坏；先看清是哪一边变了。
  - 口径是有意改动时，要连参照实现一起改（它是测试里另写的一份整份读法），并在提交说明里写明这是有意的。

## test_logbook_lanes.py：推荐日志分线——一张悬浮窗一条线，各记各的当前这一局

- **测试观点**（按测试类分）：
  - `DefaultLaneTest`：模块级函数（默认的那条线）写出的记录不带 `lane`、三选一编号原样，字段和没有分线时逐项相同；`record_offer` 给了 `client`（`cn`、`int`、`taptap`）才写进三选一记录，没给、给的不认识时不写，分线的记录同样带上；`map_source`、`difficulty_source` 只在白名单里时写；`owned_sources`、`owned_panel` 和 `state.owned` 一样长、每项合格才写（两样各看各的）；`record_unadvised` 写之前清理原因、轮次、星数、布尔值和来源，一项原因都不剩时不记，第二张那条线写出的记录（定局号以前挂着、后补写的也一样）另带 `lane`；`DIFFICULTY_SOURCES`、`OWNED_SOURCES` 和记账里的 `DIFFICULTY_SOURCE_LABEL`、`CHIP_SOURCE_LABEL` 的键一致。
  - `InterleavedLanesTest`：两条线交替记三选一，各是一个局号、日志里不多出 newgame，局数算两局；`_scan_log()` 看不见别的线，`_scan_log("taptap")` 只看得见自己的，末尾一大段别的线的记录时照样往前找到（读块临时改小）。
  - `LaneScanEquivalenceTest`：固定种子造 60 个日志文件夹，几条线的记录随机交错、分在几个月份文件里、有的没有 `time` 键，`_scan_log(线名)` 和整份按顺序读（`iter_records`）再按这条线挑出来逐位比，块大小用默认、7、64 三种；另核对这批数据里按线挑和不按线挑的结果确实常常不同（不是白比）。
  - `LaneRecordTest`：别的线的每条记录带 `lane`、编号带「线名-」前缀，选择和反馈能和它的三选一对上；两条线给的是同一串编号（两张悬浮窗各自从 1 起编号）时，`tools/calibrate.py` 的 `collect()` 照样把两条线的三选一、选择、反馈各配各的、互不串，`stats()` 的三选一次数、选择数也不少算。
  - `LaneIndependenceTest`：一条线开新局、撤销、记对局结束后，另一条的当前局号不变。
  - `RestartTest`：模拟重启（只清内存里的状态、日志留着）后两条线各接各的局号；一条线最后一条是对局结束时不接、另一条照样接；最后一条是新一局时离现在超过 `GAME_GAP` 不接（撤销新一局写的那条同样），不到就照旧接；重启的时刻按各自的最后一条记录算。
  - `HeldUnadvisedTest`：这次运行还没定局号时记的 `unadvised` 先挂着、不写：上次运行打到一半没结束，重启后先来了一次没给推荐的三选一，接着头一次三选一判出是新的一局时，挂着的那项写进新的这一局（时刻照记的时候的、排在那次三选一前面），只进新这一局的报告、不进上次那一局的；定局号以前先来了新一局、对局结束或数据表时写成 `game` 为 null；定了局号以后的照常直接写；最多挂 `MAX_HELD_UNADVISED` 条，多的丢掉。
  - `LaneApiTest`：同一个名字返回同一个对象、空名字报 `ValueError`；线上有第二张悬浮窗用到的常量和方法，没有校准计数那几个；线上的 `records_between` 和模块级的取法相同（按时刻取，两条线的记录都在里面，第二张悬浮窗存诊断包用）；同一秒两条线各开一局局号不同；塞进 `Services` 后 `call`、`has` 照常可用。
- **检证对象**：`advisor/logbook.py` 的 `Lane`、`lane()`、模块级那几个转给默认线的函数、`_scan_log`/`_scan_file` 的按线筛选、`_issued`；下游的 `tools/calibrate.py` 的 `collect()`、`advisor/ui/services.py` 的 `Services`。
- **什么时候要跑**：改了 `advisor/logbook.py` 里线的状态、局号、重启接局、倒着读最后一段的部分，或改了 `tools/calibrate.py` 按 `offer_id` 配对的方式以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_logbook_lanes`（上次约 1 秒）
- **需要什么**：不需要识别样本截图、不需要显卡；推荐日志写进测试沙箱里的临时文件夹，时钟用假的，随机造数据用固定种子。
- **输出怎么看**：同总则。失败时看断言里的记录（`lane`、`offer_id`、局号）和两条线各自的局号。
- **出错时一般是哪里不对**：
  - `DefaultLaneTest.test_default_records_unchanged` 失败 → 默认线的记录多了 `lane`、编号被加了前缀或字段顺序变了（只开一个客户端时，记录都不带 `lane`、编号不加前缀，字段和顺序不变）。
  - `InterleavedLanesTest.*` 失败 → `_scan_log` 没按线筛，或推断新局时用了别的线的记录（一局会被拆成几个局号）。
  - `LaneScanEquivalenceTest.*` 失败 → 倒着读按线筛的结果和整份读不一致（别的线的记录没跳过、最后一条记录的时刻取了别的线的、这条线的记录在更早的文件里时没往前读，其中之一）；子测试名里有文件夹编号、线名和块大小。
  - `LaneRecordTest.test_calibrate_pairs_each_lane` 失败 → 三选一编号的前缀没加，或选择、反馈的编号没跟着加，两条线同一个编号的三选一、选择混在一起。
  - `RestartTest.*` 失败 → 重启接局时读的不是这条线自己的记录（最后一条是不是对局结束、新一局隔了多久、重启的时刻按哪条记录算）。
  - `HeldUnadvisedTest.*` 失败 → 定局号以前的 `unadvised` 没挂着（直接按日志里接上的那一局写了，会算进上次运行没打完的另一局），或补写时用了补写那一刻的时刻、写进了不对的局、没有在新一局、对局结束、数据表之前写成 null。
  - `LaneApiTest.test_lane_has_what_the_second_overlay_uses` 失败 → 线上多了校准计数的函数，或少了第二张悬浮窗要用的方法、常量。
  - `LaneApiTest.test_records_between_reads_both_lanes` 失败 → 线上的 `records_between` 和模块级的取法不同了（只取了这条线的、或时刻的范围不一样），第二张悬浮窗存的诊断包会缺另一条线的推荐日志。

## test_resume.py：离开很久再回来时的新一局判定（假时钟按真实节奏喂读数）

- **测试观点**：
  - `NextGameAfterResumeTest`：上一局几种结束状态 × 下一局第 1~14 轮回来 × 星数高低、进度快慢 × 是否换角色、换位次；判出新的一局（依据轮次／进步条／星数／角色／面板）、回来第一次进对局画面就提示打开面板、判出后清空上一局筹码且地图不沿用、只判一次；主场景最难的情形（同角色、同位次、同轮次同星数、进度没退够）只要求提示打开面板，打开后判出；靠进度条退回判出；回来正在选角界面时判出；面板有认不出的格也能判出。
  - `GameOverResumeTest`：先看到结算画面（或房间、「匹配成功」画面）再切后台，下一局开始后回来——本局已结束，连续两次读到同一个轮次就开新的一局（不可撤销），轮次大于 2 时提示打开面板；结算画面没截到时靠房间结束本局；房间和「匹配成功」画面上的难度、地图留给下一局。
  - `SameGameAfterResumeTest`：同一局离开 2~10 分钟再回来绝不判成新的一局（600 次随机 + 若干固定场景）；离开期间游戏代选的筹码按面板补进来；面板有认不出的格、10 格满；回来后的读数有噪声时照旧按读错处理。
  - `UndoAfterResumeTest`：每一种依据判出后点撤销都能恢复判定前的状态，不能撤两次，同一批读数继续出现也不再判一次。
  - `ResumeCheckRulesTest`：离开不到 120 秒不核对；没有对局状态时不核对；核对只做一次；面板停在队友页签、把握不够的读数不算核对；核对过之后又离开很久会再核对一次。
  - `ManualGapTest`：手动模式没有「暂停恢复」，只是读数之间隔了很久；判得出的靠读数本身，判不出的是已知局限（按现状写断言）。
  - `WatcherResumeTest`：识别流程的暂停管理——几个暂停原因都解除时才算恢复，把暂停了多久告诉会话。
  - `OverlayPauseResumeTest`：悬浮窗按前台状态暂停（直接截取时切出游戏不暂停，只有游戏窗口最小化才暂停；截取屏幕时切出照旧暂停）。
  - `OverlayResumeTest`：悬浮窗上的提示文字（离开后核对的说法与平时的说法）和「对局中途判定为新的一局」的显示、撤销按钮。
- **检证对象**：`advisor/session/` 的 `Session`（`on_resume`、`on_select_screen`、`on_hud`/`on_stars`/`on_progress`/`on_progress_cap`、`on_panel`、`on_out_of_game`、`undo_new_game`、`RESUME_CHECK_S` 等）；`advisor/ui/overlay_parts/watcher.py` 的 `Watcher.set_paused`；`advisor/ui/overlay_parts/app.py` 的 `OverlaySession`/`UiSettings`；`advisor/ui/overlay.py` 的 `Overlay.handle`；`advisor/ui/overlay_parts/wording.py` 的提示文字常量；`advisor/ui/winapi.py` 的 `GameProbe`。
- **什么时候要跑**：改了 `advisor/session/` 的离开后核对或新一局判定、改了悬浮窗的暂停恢复（`watcher.py`、`overlay.py`）、或改了这些提示文字以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_resume`（上次没有记录；不是慢测试，但要初始化界面环境）。
- **需要什么**：不要识别样本（读数全是假的）。要界面环境（离屏运行，测试环境自己起，不弹真窗口）；`OverlayPauseResumeTest`、`OverlayResumeTest` 用真的悬浮窗和暂停管理部件，但窗口是隐藏的、不启动识别线程。
- **输出怎么看**：同总则（没有特有输出）。
- **出错时一般是哪里不对**：
  - `NextGameAfterResumeTest.test_matrix` 失败 → 新一局判定矩阵里某一格坏了（看 `newgame.py` 的判定和 `core.py` 的 `_start_next_game`）；依据文案用固定格式核对，文案有意改的就同步改。
  - `test_same_hero_same_row_late_stage_needs_panel` 失败 → 分不出时「只提示打开面板」的行为改了。
  - `test_classic_progress_case` 失败 → 靠进度条退回判出的依据文案或阈值改了。
  - `test_back_on_select_screen` 失败 → 选角界面判新一局（不可撤销）的规则改了。
  - `GameOverResumeTest` 失败 → 本局已结束后的新一局判定（连续两次读同一轮次、不可撤销、轮次大于 2 时提示打开面板），或房间／「匹配成功」画面的用法改了。
  - `SameGameAfterResumeTest` 失败 → 同一局被误判成新的一局（最要紧的行为），或代选筹码没按面板补进来。
  - `UndoAfterResumeTest` 失败 → 撤销恢复的状态不对，或撤销后又被判了一次。
  - `ResumeCheckRulesTest` 失败 → 核对的时长门槛、核对只做一次、队友页签不算核对等规则改了；门槛数值是有意改的就同步改断言。
  - `ManualGapTest` 失败 → 手动模式下（没有暂停恢复）的判定改了；后两条是已知局限，现状改了就要改断言。
  - `WatcherResumeTest`、`OverlayPauseResumeTest` 失败 → 暂停恢复的调用时机改了（几个暂停原因的合并计时、最小化与直接截取的区分）。
  - `OverlayResumeTest` 失败 → 提示文字或「对局中途判定为新的一局」的显示、撤销按钮改了。

## test_resume_noise.py：给「离开很久再回来」的判定加随机噪声，成批找误判和漏判

- **测试观点**：
  - `SameGameNoiseTest`（同一局离开再回来，400 局）：绝不判成新的一局；原有筹码不丢（面板把一个筹码读错时最多差 1 个）；能由同组最左边候选解释的错选记录按面板改正；随机驱动确实覆盖了要求的情形（防止以后改生成方式时悄悄少覆盖）。
  - `NextGameNoiseTest`（下一局开始后才回来，400 局）：新的一局最多判一次；回来第一次进入对局画面就提示打开面板；判出时上一局的筹码不留进下一局；没判出的只落在可以解释的类别里；覆盖检查。
  - `BoundaryTest`：认不出的格数与少了的个数的边界（少 N 个、认不出 N−1 或 N−2 格）；面板这一页 10 格都满又有认不出的格；候选里有读不出的格；改正过的记录再次离开。
  - `RegressionTest`：这份测试查出、已经修正的问题，各留一个固定场景。
  - `KnownLimitationTest`：软件维护说明「已知局限」写明的行为（标成预期失败）。
- **检证对象**：`advisor/session/` 的 `Session`（`on_resume`、面板核对、新一局判定，以及 `panel.py` 的 `panel_order`/`panel_fixes`/`explained`、`game.chip_sources`、`game._pick_offers`）。推荐引擎换成假的，只测记账。
- **什么时候要跑**：改了 `advisor/session/` 的离开后核对、新一局判定或面板核对（`panel.py`、`newgame.py`、`core.py`）之后必须重跑——种子固定，规则一动会成批失败。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_resume_noise`（上次没有记录；慢测试）。
- **需要什么**：不要识别样本（读数全是假的）。会随 `tests/test_resume.py` 一起初始化界面环境（离屏运行，不弹真窗口、不启动后台线程）。
- **输出怎么看**：设了环境变量 `RESUME_NOISE_REPORT=1` 时在标准错误输出打印统计（各类噪声的次数、漏判分类），不设就安静；失败时报告里会带那几局随机场景的完整描述。其余同总则。
- **出错时一般是哪里不对**：
  - `SameGameNoiseTest.test_never_new_game` 失败 → 同一局回来被误判成新的一局，去看 `newgame.py` 的互相印证和 `panel.py` 的核对。
  - `test_original_chips_kept` 失败 → 面板核对或写入已有筹码的顺序（`panel_order`）把原有筹码丢了。
  - `test_leftmost_picks_fixed` 失败 → 由同组最左边候选解释的错选记录的改正（`panel_fixes`/`explained`）改坏了。
  - `test_coverage` 失败 → 随机驱动少覆盖了某种情形（生成方式改了）；覆盖率门槛是有意改的就同步改断言。
  - `NextGameNoiseTest.test_at_most_one_new_game`、`test_old_chips_not_carried_when_detected` 失败 → 新一局判了多次，或上一局的筹码留进了下一局。
  - `test_prompt_on_return` 失败 → 回来第一次进对局画面没提示打开面板。
  - `test_misses_are_explained` 失败 → 漏判出现了「不可解释」的类别（判定松紧变了）。
  - `BoundaryTest` 失败 → 认不出的格数边界、满页判定，或候选读不出时的改正规则改了。
  - `RegressionTest` 失败 → 以前修好的问题又回来了（看该用例注释里记的场景）。
  - `KnownLimitationTest` 用的是「预期失败」：如果它反而通过了，这条测试会算失败——说明那条已知局限被修好了，应把它从预期失败里移出来。软件维护说明的「已知局限」跟着改。

## test_panel_pages.py：「查看筹码」面板翻页——认第几页、按页对位记账、离开后回来只看第一页

- **测试观点**（按测试类）：
  - `ArrowFramesTest`：实机录像里的三帧（缩到 1920×1080）：第一页（只有右箭头亮，10 个筹码都认对）、第二页（只有左箭头亮，只有一个刚点过的筹码，鼠标指针盖着图案，把握低于门槛；其余 9 格是空格子，不拉低把握，把握只看那一格）、翻页动画（不算面板）；压暗到 0.35 时认出的页不变；在第一页上把箭头改画成只有一页、中间页、最后一页、被挡住（页为空串）。
  - `PageSpanTest`：每一页对应已有筹码的哪一段（`page_span`）：最后一页从记着的最后一页或再后一页起，对不上时挑靠后的，不往更前挑；记着的最后一页那一段对得上的（加上认不出的格数）不到一半时挑再后一页（前面的页上漏记了一个、这一页上的筹码正好是那一段里某次三选一的候选）；中间页按内容滑动着比，从页头开始的优先，错开几位照样对上；对得上的不到一半、对得一样好的几组隔得太远、一个都没认出时不改。
  - `PagedLedgerTest`：已有筹码经三选一一个个记下，面板读数是造的（带 `page`）：11、15、20、23 个筹码每一页看一遍都不变，看到的记为面板确认过，每翻到一页发一次读数；只看第一页只改前 10 个，只看第二页只改第 11 个以后的；两页都看时把漏记的补进它的位置、把记错的改正；第二页只有 1 个；把握不够不用；第一页认错一格（不去掉，挪到这一页之后，看过第二页以后去掉，两页都读对一遍就和实际一样）、认不出一格；第二页认错一格（和只有一页时一样换成认错的那个）、认不出一格；补录得晚的在最后一页上没有时不去掉；一共 21 个、记着 20 个（第一页漏记一个，或第 21 个没记上）时先看到最后一页，记着的一个都不去掉；中间页改正记错的、对不上时不改；箭头看不清、页码不认识时不改；记着的不少于一页（满 10 个也算）时，只有一帧读成只有一页（亮着的箭头被浅色的东西盖住）不改、不发读数，两帧之间隔着别的页、面板关了、把握不够、队友的页签、看不清的不算连着，连着两帧才按只有一页改，记着的不到一页时一帧就改；这一局认出过多于一页的面板以后，记着的不到一页也要两帧，新的一局清掉；没有页码的读数照只有一页算（记着的多于一页时同样要连着两帧）。
  - `ResumePagesTest`：离开后回来先看到第二页（同一局：不判、补上代选的，翻回第一页核对结束；下一局：翻回第一页判出，再看第二页补齐）；翻页动画那几帧、只看到过第二页就关了面板时核对留着，看过第一页、没下结论就关了的照常到此为止（面板看不到 `RESUME_TURN_S` 秒以上才算关了；关了这么久、中间没有别的帧就又打开时同样算关过）；看过第一页、认不出的格子多下不了结论，翻到第二页再翻回来（中间夹着翻页动画那几帧）时核对留着，看清第一页时照常判（下一局判出新的一局，同一局核对结束）；先看到中间页不判；第一页上被挤进来的原谅（最多 3 个；第一页上不在记着的前 10 个里的多于 3 个时一个都不原谅）；第一页对不上判出新的一局，记着的不到一页时也一样；记着的多于一页、面板只有一页时判出新的一局（连着两帧再判：第一帧不判、核对留着）；一帧右箭头被盖住读成只有一页时不判，下一帧照第一页核对；按第一页判出新的一局时，新的一局和撤销后恢复的这一局都记着认出过多于一页的面板；对局中途才启动、第一页没读过时会判成新的一局，「撤销」后恢复原来的记录、面板还开着时照常按第一页核对（不撤销时看完两页也和实际一样）。
  - `PanelGraceTest`：识别流程读面板的节奏：面板开着时每帧读，翻页动画那一帧不算面板，刚看不到的 `PANEL_GRACE_S` 秒以内照样每帧读（翻过去的那一页马上读到），过了这段时间回到按 `hud_ms` 读。识别函数换成假的。
  - `PanelRecomputeTest`：读到面板、按面板改了已有筹码时，三选一还开着（包括收起来去点「查看筹码」的）马上按新的已有筹码重算这一次推荐，重算的 advice 排在 panel_read 后面、局面里的 owned 是面板上的、`owned_unknown` 为假；之前不知道已有筹码、面板上确实一个都没有时已有筹码和局面都没变，只发 panel_read、不重算（推荐卡片上的提醒由悬浮窗收到 panel_read 时收起），之后不再要面板（`test_empty_panel_no_recompute`）；三选一没显示、面板读数和记着的一样、按面板判成新的一局时不重算。推荐引擎换成假的。
- **检证对象**：`advisor/vision/chip_panel.py` 的 `read_chip_panel`（`PanelReading.page`、`arrows`，`_read_arrows`）；`advisor/session/panel.py` 的 `page_span`、`page_merge`、`resume_missing`、`read_panel`（按面板改了已有筹码、又没判成新一局时，经 `SelectTracker.recompute` 重算这一次推荐），经 `Session.on_panel`、`on_resume`；`advisor/vision/pipeline.py` 的 `FrameProcessor._read_panel`。
- **什么时候要跑**：改了 `advisor/session/panel.py`、`Session.on_panel`/`on_resume`、`advisor/vision/chip_panel.py` 的翻页箭头、`advisor/vision/anchors.py` 的 `ARROWS`/`ARROW_HALF`、`FrameProcessor._read_panel` 读面板的节奏以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_panel_pages`（约 2 秒）。
- **需要什么**：记账的几类不要识别样本；`ArrowFramesTest` 要 `samples/pages/` 里的三张录像帧（见测试指南第五节）和 `assets/chips/` 里的筹码图标，缺了跳过。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `ArrowFramesTest` 失败 → 箭头的位置（`anchors.ARROWS`、`ARROW_HALF`）、颜色门槛（`chip_panel.ARROW_*`）或亮度系数改坏了；`test_last_page_single_chip` 失败也可能是置信度把空格子算了进去。
  - `PageSpanTest` 失败 → `page_span` 的候选页或挑法改了。
  - `PagedLedgerTest` 失败 → `page_merge` 对面板上没有的记录的处理（挪到这一页之后、去掉、挪到这一页之前）、去重的键（`GameState._last_panel`）或只有一页要连着几帧（`panel.ONLY_FRAMES`、`GameState._only_frames`、`GameState._paged`）改了。
  - `ResumePagesTest` 失败 → `read_panel` 里离开后核对的那一段、面板关了什么时候结束核对，或 `resume_missing` 的原谅规则改了。
  - `PanelGraceTest` 失败 → `FrameProcessor._read_panel` 的节奏（`PANEL_GRACE_S`、`_panel_seen_at`、`hud_ms`）改了。

## test_resume_frames.py：上一局结束后切到后台、下一局开始后回来，能不能判出新的一局

- **测试观点**：
  - `ResumeAfterGameEndTest`：把一局真人对局回放完（结束时第 10 轮、记下 7 个筹码、进度上限 17），然后暂停识别、时钟往后推 3~10 分钟、再当作「下一局」接着回放同一批样本（同角色、同位次、同地图）；核对各种回来时机都判出且只判一次、依据符合预期（轮次变小／面板核对）；判不出或要靠面板的，回来第一次看到对局画面就提示打开「查看筹码」面板；判出后已有筹码就是下一局的、不含上一局第 9、10 轮才拿到的筹码；判出后的三选一按下一局的已有筹码和角色推荐；靠面板判出的先报新的一局再报面板读数，并保留撤销；第 7 轮回来只看画面判不出来、要靠面板（轮次超过阈值、进度只退 2 格）；第 8 轮回来分不出（轮次超阈值、星数相同、进度只退 1 格），宁可漏判也不在一局之内误判。
  - `ProgressRuleTest`：第 6 轮回来、面板读不出时，靠进度条由第 14 格退回第 10 格（退 4 格）+ 轮次变小判出新的一局（依据是进度条）。
  - `SameGameResumeTest`（对照）：同一局里切到后台再回来，绝不判成新的一局；离开得久同样提示打开面板核对，面板上的筹码只多不少，核对后不判；不打开面板时接着记账。
- **检证对象**：`advisor/session/` 的 `Session.on_resume`、`can_undo_new_game`/`undo_new_game`，以及 `RESUME_CHECK_S`、`RESUME_MISSING_MIN`、`NEWGAME_ROUND_MAX`、`NEWGAME_PROGRESS_DROP`；`advisor/vision/pipeline.py` 的 `FrameProcessor`（真识别，含 `resumed`）和真截图回放方式；`advisor/vision/chip_panel.py` 的面板识别。
- **什么时候要跑**：改了 `advisor/session/` 的「离开后再回来」核对与新一局判定（`core.py`、`newgame.py`、`panel.py`）、改了识别流程的暂停恢复，或改了真截图回放方式之后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_resume_frames`（上次约 0.6 秒；慢测试）。
- **需要什么**：和 `test_pipeline_sequences` 同一批样本（同一局的截图不少于 20 张、`samples/labels.json`、`samples/stars_labels.json`）；缺样本时整个文件跳过。用到的标注字段与 `test_pipeline_sequences` 相同。
- **输出怎么看**：缺样本时整个文件跳过（跳过原因与 `test_pipeline_sequences` 相同，写明缺哪个文件）。没有别的特有输出。其余同总则。
- **出错时一般是哪里不对**：
  - `ResumeAfterGameEndTest.test_new_game_detected_once` 失败 → 判出新一局的时机或依据改了，或判了不止一次（去看 `newgame.py` 的判定、`core.py` 的 `_start_next_game`）。
  - `test_resume_prompt` 失败 → 回来后的面板核对提示（`why="resume"`）或提示时机改了。
  - `test_previous_chips_dropped`、`test_advice_uses_next_game_chips` 失败 → 判出新一局后没清空上一局的筹码，或推荐用了上一局的筹码。
  - `test_panel_check` 失败 → 面板核对判新一局的规则（少几个筹码才算）或撤销的保留改了。
  - `test_round7_needs_panel`、`test_round8_indistinguishable` 失败 → 轮次阈值或进度条退回格数的阈值改了；阈值是有意改的就同步改断言。
  - `ProgressRuleTest.test_progress_and_round_regress` 失败 → 靠进度条退回判新一局的规则或依据文案改了。
  - `SameGameResumeTest.test_never_new_game` 失败 → 同一局回来被误判成新的一局（最要紧的行为），去看判定里的互相印证和面板核对。
  - 预期表里的「第几轮回来、在哪一张判出、依据是什么」是有意改的就同步改。

## test_real_games.py：真人对局里发现的问题，各留一条测试

- **测试观点**：
  - `TeamSelectTest`：标题上方有「……正在选择强化筹码…」那一行时是全队同时三选一，一定按任务来源算；其余玩家选完后那一行消失、刷新后仍是同一次；概率行变了算另一次；两个任务接连完成时两次三选一之间没有棋盘画面；概率行一样时三张全换、刷新次数没少也算另一次（`test_back_to_back_same_row`：2 级任务的三选一选完紧接着升到 2 星又出一次，先记下第一次选的，第二次按刚升星算）；刷新只换候选不算新的一次；读错的概率行不算换了一次；来源判定可以直接核对；一直没读到星数时，估出的星数不低于升星、筹码商店三选一估过的最高星（`judge_source` 的 `floor`、`GameState._star_guess_floor`）。
  - `TeamBannerVisionTest`（要样本）：有那一行的三选一样本读得出，其余三选一样本读不出；那一行必须紧贴在标题上方（名字里碰巧有「强化」但位置不对不算）。
  - `ProbabilityLineTest`：三个百分比之和不是 100 的读数按读不到处理（界面刚出现时把「/」读成数字的情况）；一行读错、一行读对时取读对的那行；样本上概率行照常读出。
  - `OfferRerollsTest`：同一组候选时刷新按钮突然找不到（被表情气泡挡住等），沿用刚才读到的次数。
  - `ProgressCapTest`：进度条上红叉所在的格是进度上限，连续两次读到同一个值才采用、按局记住；开新的一局时重置、撤销时恢复；引擎按它算；地图待识别时的附注写法；样本上读出 18。
  - `ProgressCurrentTest`：进度条的当前格（真人对局里第 9 轮已到第 14 格，剩余轮数按它算）；当前格蓝底碎块只在合起来不超过一格宽时才合并、扁蓝条先去掉；会话连读两次才采用；「读到与已采用的不同的格、还在等再读一次」的状态；引擎按进度算剩余轮数；进度条能否决误判的新一局。
  - `RoundSanityTest`：轮次读数的两道防线（只按位置读到的两位数要两次一致才用；远超进度条当前格的轮次按读错处理）；轮次比进度条同一段时间里走过的格数多出 2 格以上时按读错处理（`ROUND_JUMP_SLACK`，进度条跟上来以后再采用；进度条很久没读到、已经到了红叉前一格、刚读到新的格数还没采用（轮次和进度一起往前跳）时不比；「14」连读成「11」按改正读错采用了以后，读回 14 不比、连读两次就改回来；轮次变小判成新的一局时不带上一局的进度，撤销新的一局时跟着那一局回来）；实机两处修正（怪物名牌下方的血量不当成轮次；数字检测漏掉时在「轮」字左边补读）；只做识别时要还原识别引擎的开关；头像挡住数字的实机截图（繁体兔耳朵和「Round」连成一段时读出 3，简体天线插进数字时读不出可以、不能读错，大厅不当成轮次，都只做一遍带找字的识别）；贴白块挡住数字下沿时先裁小块、不换大小重读而读错；第一遍靠标记读错时（第一遍的文字识别换成读成「10」，挡住「04」下沿一成半时实测读成过），标记左边那一块用真的识别读出 4，两边对不上，按读不出处理；标记左边那一块读成「109」时去掉菜单按钮边缘的前缀，交回的那一块交给 `read_round_crop` 也读得出。
  - `NewGameVetoTest`：对局中途的新一局判定要互相印证——另一项读数在 20 秒内还说这一局在继续，就按读错处理。
  - `NewGameCauseTest`：新一局判定要带上依据的种类（悬浮窗据此区分「对局中途」和「选角界面」）。
  - `RealGameRecognitionTest`：用一场实战局的识别读数复现两次刷新时闪出的「无法识别」「匹配度低」，核对：刷新后没读全时保留推荐、标「正在刷新」；只对得上一个筹码时不提示；只读到共同部分时等读全。
- **检证对象**：`advisor/session/` 的 `Session`（`judge_source`、`on_frame`、`on_stars`、`on_hud`、`on_progress`、`on_progress_cap`、`on_select_screen`、`on_hero`、`undo_new_game`，以及 `newgame.py` 的判定）；`advisor/vision/chip_select.py`（`read_chip_select`、`_team_banner`、`_probabilities`、`_match_chip`、`check_names`、`recognize_only`、`_engine`）；`advisor/vision/hud.py`（`read_progress_cap`、`read_progress`、`read_round`、`read_round_info`、`read_round_crop`、`_parse_round`、`_parse_round_ex`、`_blue_cells`）；`advisor/vision/io.py` 的 `ocr`；`advisor/engine/` 的 `Scorer`、`advise`；`advisor/pool.py` 的 `State`。
- **什么时候要跑**：改了三选一来源判定、进度条读数、轮次读数、新一局互相印证，或引擎里进度上限／剩余轮数的算法之后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_real_games`（上次约 2.9 秒；慢测试）。
- **需要什么**：一部分用例要识别样本和标注。要 `samples/labels.json`，以及它列出的、或按文件名找的若干三选一与对局画面截图（例如标题上方有那一行的任务三选一样本、2026 开头的对局画面和进度条样本、头像挡住轮次数字的两张和大厅一张 2560×1600 截图）。`TeamSelectTest`、`OfferRerollsTest`、`NewGameVetoTest`、`NewGameCauseTest`、`RealGameRecognitionTest` 不需要样本。用到的标注字段：`labels.json` 每条的 `file`、`scene`、`team`。`stars_labels.json`、`panel_labels.json`、`tests/data/coins/labels.json` 这一组用不到。
- **输出怎么看**：没样本时只跳过真用到样本的那几条用例（按文件名找截图的 `sample(pattern)`，`TeamBannerVisionTest.selects` 和用到 `labels.json` 的几条用 `samples.require_samples("labels.json")`），跳过原因写明缺哪个文件；同一类里不用样本的用例照常跑，`TeamSelectTest`、`OfferRerollsTest`、`NewGameVetoTest`、`NewGameCauseTest`、`RealGameRecognitionTest` 整个类都不需要样本。没有别的特有输出。其余同总则。
- **出错时一般是哪里不对**：
  - `TeamSelectTest` 失败 → 全队任务三选一的来源判定（`judge_source`、`chip_select._team_banner`），或「同一次三选一」的辨认（比对概率行，概率行一样时比候选和刷新次数：`selection.py` 的 `starts_another`、`fresh_offer`）改了。
  - `TeamBannerVisionTest` 失败 → 标题上方那一行的识别规则改了，或样本／标注的 `team` 字段改了（是有意改的就同步改断言）。
  - `ProbabilityLineTest.test_sum_must_be_100` 失败 → 概率行求和必须为 100 的校验改了；`test_samples` 失败 → 概率行识别坏了。
  - `OfferRerollsTest` 失败 → 「刷新次数突然读不到时沿用上一次」的规则改了。
  - `ProgressCapTest` 失败 → 进度上限的连读两次才采用、按局记住、开新一局重置／撤销恢复、或地图待识别时的附注写法改了；也可能 `read_progress_cap` 的识别坏了。
  - `ProgressCurrentTest` 失败 → `read_progress` 的当前格／红叉读数、蓝底碎块合并规则、会话的「连读两次才采用」、或引擎按进度算剩余轮数改了。
  - `RoundSanityTest` 失败 → `_parse_round`/`_parse_round_ex`/`read_round` 的轮次修正规则改了，或 `read_round_info` 看见标记却读不出时的做法（裁小块只认字、不换大小重读）、靠标记读到的数用裁小块核对的规矩、`_round_mark`/`_mark_left` 认连成一段的标记、`_crop_digits` 去前缀改了；识别的读数是有意改的就同步改断言。
  - `NewGameVetoTest` 失败 → 新一局判定的互相印证（20 秒窗口、否决的判据）改了。
  - `NewGameCauseTest` 失败 → 新一局事件带上依据种类的写法改了。
  - `RealGameRecognitionTest` 失败 → 「刷新后没读全时保留推荐并标正在刷新」「只对得上一个筹码不提示」「只读到共同部分时等读全」的规则改了。

## test_lock_outside.py：识别线程算推荐时不持共用锁、事件只走一条通道、同一次三选一照搬算过的推荐

- **测试观点**
  - `OutsideLockTest`：算推荐时共用锁是放开的，界面线程马上拿到锁和一份完整快照；把一帧交给 `Session` 的这段时间换线程间隔调短，做完改回；算推荐放开锁和照旧在锁里算，结果逐项相同
  - `SchemeSameResultTest`：按筹码方案计算时同样逐项相同
  - `UiFirstTest`：换线程的间隔按进出次数计——第一个进去的调短、最后一个出来的才改回，交叠、嵌套、出错都一样
  - `InterleaveTest`：算推荐的期间界面线程改了会影响推荐的状态（切方案、改正候选、设地图、选角色、补录筹码、撤销新一局、撤销移出筹码等），旧结果不装进去，最后显示按新状态算的（撤销移出时放回以后的局面和移出前一样，下一帧不重复给推荐，`test_restore_chip_while_computing`）
  - `EventOrderTest`：识别线程这一帧放进队列的事件和界面线程自己放的事件各自只取自己的，先后、序号不乱；取走的提示类事件按先后分成前后两组
  - `HintsTest`：一帧只为读提示进一次锁，每次读到的都和当时在锁里现读的一样；三选一刚出现、前两张没读出来（等着、还没给过推荐）时的 `select_fresh`、`select_unread` 也一样（`test_first_unread_matches`）；给过推荐（`select_told`，判断名字突然变了是不是被盖住用）、记下了选中的另一张（`select_noted`，留下记下它用的那一帧用）两项同样和现读的一样
  - `MemoTest`／`MemoScopeTest`：同一三选一里同样的输入照搬算过的推荐，结果和重算的相同；真算推荐计一次耗时、照搬的不计；界面关掉或深拷贝时清空
  - `OverlayTest`／`UiEventsTest`：接上离屏悬浮窗后，算推荐期间刷新、切方案不用等，界面上的刷新／改正当场生效；界面线程调用 `Session` 放进队列的事件当场交给悬浮窗；两次三选一紧接着出现、概率行一样（三张全换、刷新次数没少）时推荐日志记成两次、编号不同，第一次选的记在第一次的编号上，之后这一次里真刷新照旧记「刷新」（`test_same_row_back_to_back_logged_apart`）
- **检证对象**：`advisor/session/selection.py` 的 `compute_outside`、`AdviceMemo`、`advise`；`advisor/vision/pipeline.py` 的 `_ui_first`、`UI_FIRST_SWITCH_S`、`FrameProcessor.process`／`_hint`；`advisor/session/core.py` 的 `Session.recompute`、`WatchHints`、`on_frame`；`advisor/session/events.py` 的 `split_extra`、`Event`；`advisor/ui/overlay.py` 的 `refresh`、切方案；`advisor/perfstats.py`
- **什么时候要跑**：改了识别线程调 `Session` 的加锁范围、`compute_outside`、换线程间隔、事件的取放与先后、照搬算过的推荐的机制，或界面切方案／改正／刷新的当场重算，都要跑
- **怎么单独跑**：`python -B tools/run_tests.py --only test_lock_outside`（上次约 45 秒，本组里最慢）
- **需要什么**：离屏平台；识别函数换成假的；用慢的假推荐引擎让识别线程停在算推荐里，再在界面线程上做事
- **输出怎么看**：识别线程停在算推荐里最多等一段上限（改坏时照样结束，不会卡死）；每一条失败信息会写明界面线程等了多久；其余同总则
- **出错时一般是哪里不对**
  - `OutsideLockTest.test_lock_free_while_computing` 失败 → 算推荐时还持着共用锁（`advisor/session/selection.py` 的 `compute_outside`）
  - `OutsideLockTest.test_switch_interval_short_while_computing` 失败 → 换线程间隔没调短或没改回（`advisor/vision/pipeline.py` 的 `_ui_first`）
  - `OutsideLockTest.test_same_result_as_inline`／`SchemeSameResultTest` 失败 → 放开锁算的事件、识别记录或最后状态和在锁里算的不同（`advisor/session/`）
  - `UiFirstTest` 失败 → 间隔按进出次数计的逻辑（交叠、嵌套、出错）改坏（`advisor/vision/pipeline.py` 的 `_ui_first`）
  - `InterleaveTest` 失败 → 算推荐期间界面改动后，旧的推荐还装了进去（`advisor/session/core.py` 的 `Session.recompute`、`selection.py`）
  - `EventOrderTest` 失败 → 事件只走一条通道后取放、先后、序号乱了（`advisor/session/events.py` 的 `split_extra`、`core.py` 的 `on_frame`）
  - `HintsTest` 失败 → 一帧多次为读提示进锁，或提示和当时现读的不一样（`advisor/vision/pipeline.py` 的 `_hint`、`advisor/session/core.py` 的 `WatchHints`）
  - `MemoTest`／`MemoScopeTest` 失败 → 照搬算过的推荐的命中、清空或耗时统计改坏（`advisor/session/selection.py` 的 `AdviceMemo`、`advisor/perfstats.py`）
  - `OverlayTest`／`UiEventsTest` 失败 → 界面上刷新／切方案／改正的当场重算，或界面上放的事件没当场交给悬浮窗（`advisor/ui/overlay.py`、`advisor/session/core.py`）；`test_same_row_back_to_back_logged_apart` 失败 → 推荐日志没按记账的结论分开两次（`advisor/ui/overlay_parts/records.py` 的 `OfferLog.on_advice`、事件的 `select_no`），或记账没认出紧接着的另一次（`selection.py` 的 `starts_another`）

## test_result_table_hookup.py：结算数据表的接线——等表、认自己、推荐日志的 table 记录、识别流程什么时候读、报告重拼

- **测试观点**（按测试类分；读表全部用假的函数和假的 `TableReading`，不读截图，读表本身的测试在 `test_result_table`）：
  - `WaitingTest`：开关关着不等；结算画面结束本局才开始等；房间等其他画面结束的不等；认出奖励画面、主菜单、「匹配成功」画面、选角界面、开了新的一局时不再等；从结束起满 `TABLE_WAIT_S` 秒以后不再等，也不再记。
  - `EndedByScreenThenResultTest`：开着对局报告时，一局先被连着两次认出的房间画面结束（两次至少隔 `OUT_CONFIRM_GAP_S`），之后才读到结算画面：也开始等表，读到表能记下；停在结算画面上再读到一次时等表的计时不重来（`_ended_by_screen` 只走一次）；开关关着时不等。
  - `AcceptTest`：`consistent` 为真当场认定、结束等表；为假时先记下，下一次五项数字、BEST、两个星币数完全一样才算读到一份，不一样就换成新的那次再等；读到一份时，这一局还没记过就先记一条、接着等（淡入的几帧读到不完整的一份，`wants_table` 仍为真），记过以后这次读到的数（五项数字和两个星币数里不是 null 的个数，`_table_known_count`）比记过的那一次多才再记一条，一样多或更少时不记、结束等表（`wants_table` 变成假，之后读到的都不再记）；`consistent` 为真记下以后什么都不再记；五项数字一个都没读出来的不算读到（两帧都是空的也不记）。记下的内容照推荐日志 `table` 记录的格式（`advisor/logbook.py` 的模块说明）：有 map、difficulty、rounds、players，没有名字、没有等级（整份记录转成文字查不到假名字），拿不准的项是 null。
  - `SelfTest`：自己那一栏按名字（和玩家列表上认名字的同一套规矩：两边取名字的主干，读到的那个里含着记下的那个、或者相似度不低于 0.8 就是同一个名字）和座位（`own_row` 第 k 行是第 k+1 座）两条线索认；两条矛盾、名字对上不止一栏时全部写 null；名字一栏都没对上、座位那一栏的名字又读出来了且明确不是自己时也写 null，那一栏的名字没读出来、或者还不知道自己的名字时照旧按座位认；「加好友」钮不是灰（`muted is False`）的那一栏、表上写着是电脑（`bot is True`）的那一栏都不认（灰的也不等于就是自己）。座位 k 的角色取玩家列表第 k 行：自己那一行用记账里的角色，别的行用 `game.teammates` 里那一行的角色；电脑以表上名字的写法为准，读不出时用玩家列表上那一行读名字判过的（没判过的写 null）。
  - `LogbookTableTest`：`record_table` 写出的记录字段对、不带 `lane`；第二张的线另带 `lane`；`table` 不在 `_LAST_KINDS` 里，重启后接局不受它影响。
  - `PipelineTableTest`：不在等表时 `looks_like_table`、`read_table` 都不调；等表时每帧先看一眼，像的才读；读到的交给 `Session.on_result_table`，识别记录只记耗时、像不像、读没读到、`consistent`（不带名字）；读表出错时这一帧照常往下走，同一段等表里只报一次。
  - `ReportingTableTest`：`Reporting.table_read` 对已经拼过的一局再拼一次（覆盖同一份），还在等胜负的那一批不动；`ensure`、`set_enabled` 把开关告诉记账。
  - `OverlayTableTest`：`table` 事件交给推荐日志记一条、拿到局号以后叫报告重拼；第二张转给第一张。
- **检证对象**：`advisor/session/core.py` 的 `Session.wants_table`、`on_result_table`、`stop_table_wait`、`_table_self_seat`、`_table_same_name`、`_TABLE_NAME_MATCH`、`_table_record`、`_table_signature`、`_table_known_count`，`advisor/session/game_state.py` 的等表五项（含 `_table_known`），`advisor/session/offgame.py` 的 `final_screen`、`on_out_of_game`、`_revoke_end`；`advisor/logbook.py` 的 `Lane.record_table`；`advisor/vision/pipeline.py` 的 `FrameProcessor._read_table`、`advisor/ui/overlay_parts/watcher.py` 换用 `active_ms`；`advisor/ui/overlay_parts/state.py` 的 `EV_TABLE`、`dispatch.py` 的 `on_table`、`records.py` 的 `OfferLog.log_table`、`reporting.py` 的 `Reporting.table_read`、`lanes.py` 的开关同步。
- **什么时候要跑**：改了等表的起止、认自己那一栏的线索、`table` 记录的字段、识别流程里读表的时机或取帧间隔、悬浮窗对 `table` 事件的处理、报告重拼的条件以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_result_table_hookup`。
- **需要什么**：不读识别样本；悬浮窗那几条用 Qt 的离屏平台；推荐日志写进测试沙箱的临时文件夹。
- **输出怎么看**：同总则。读表用假函数，失败时看断言里的事件种类、记录的字段和 `self` 的取值。
- **出错时一般是哪里不对**：
  - `WaitingTest.*` 失败 → 等表的起止（`Session.wants_table`、`offgame.final_screen`、`on_out_of_game` 里的 `stop_table_wait`，`TABLE_WAIT_S`）改了。
  - `AcceptTest.*` 失败 → `consistent` 的认定、读过一份以后要不要再记（淡入时先记不完整的一份，后来读到数更多的才再记，看 `_table_known_count`、`_table_known`）、两次读数的比较（`_table_signature`）或记录字段（`_table_record`）改了；出现名字或等级说明整理记录时漏删了。
  - `SelfTest.*` 失败 → 认自己那一栏的两条线索、名字比较的相似度门槛（`_TABLE_NAME_MATCH` 要和 `advisor/vision/stars.py` 的 `NAME_MATCH` 一致）、`muted` 和电脑的排除、角色和电脑的取法（`_table_self_seat`、`_table_record`）改了。
  - `PipelineTableTest.*` 失败 → `pipeline._read_table` 在不在等表时的分支、读表用的画面或交给记账的方式改了。
  - `ReportingTableTest.*`、`OverlayTableTest.*` 失败 → `table` 事件的分派、推荐日志的调用、报告重拼的条件或两开时的转交改了。

## test_game_report.py：对局报告——从推荐日志拼、存进推荐日志文件夹下的 upload、启动时补拼、发送和清理

- **测试观点**（按测试类分）：
  - `BuildTest`：同一个 `offer_id` 的 offer、选择、反馈各取最后一条（同一次三选一分在两局时只算在最后一条 offer 的那一局，前一局不带）；旧日志里的「重选」写成「刷新」；`via` 只认 `panel`、`supplement`；反馈只带原因、不带补充说明；`evals` 的筹码只写名字，另带稀有度和流派，整份筹码资料和给人看的整句都不带；白名单外的字段不带；职业倾向照抄 `[职业, 分值]`；`unrecorded` 是整数下标组成的数组（含非空的）；`reroll`、`picks` 的各项只收数和布尔值，`top` 只收整数，写成文字、小数的写 null；按方案推荐的那份方案（推荐日志里记的 `Plan.record`）写回分享码，和拿原来那套方案直接写的一样、同一档里的先后也对得上，写不出来时这一条为 null；`client` 取最后一个非空的；`started` 换成 UTC 只到分钟；`end` 取紧跟在这份报告最后一次三选一后面（不早于它、不超过 `GAME_GAP`）的最后一条 gameover，在它之前、隔得太久、时刻读不出的都不算；`result`（第 2 版报告）和 `end` 出自同一条 gameover：没有 `end` 时为 null，那一条没记胜负（旧日志、胜负还没定）时两项都是 null，胜负结束以后才定、后记的那一条带着的取得到，`outcome`、`how` 只认 `win`/`loss`、`victory`/`reward`/`game_over`（大小写一字不差），别的值（大小写不对、数字、布尔、数组、对象、空文字）那一项写 null；`since` 以前的三选一不带、一条都不剩时为 None；每次三选一的 `state` 里总有 `map_source`（从 offer 记录顶层取，只认 `MAP_SOURCES` 里的值，旧记录、值不对、state 里自己带的都不采用，写 null），总有 `difficulty_source`（只认 `DIFFICULTY_SOURCES` 里的值，否则 null），总有 `owned_sources`、`owned_panel`（和清理过的 `owned` 一一对应：两项数组和 `owned` 一样长、每项合格才写，去掉非文字项、截到 200 项时跟着对齐同一批下标，对不上时两样都 null）；`end` 的 `map_source` 只认 `END_MAP_SOURCES`（局里认的依据加 `result`、`manual`），值不对写 null，`map_in_game` 照文字写，旧记录没有这两项时也不写；`end` 另有 `difficulty_source`、`owned_sources`、`owned_panel`、`cap_rejected`（0～10 亿的整数，否则 null），两样数组和 `end.owned` 对齐。`MAP_SOURCES` 和 `session.core.MAP_SOURCE_LABEL` 的键一致；截断（文字、`evals`、`parts`、队友、数组）和 NaN、Infinity、要数处的布尔值写 null；版本号认不出时用当前的版本。第 3 版起另有结束时的数据表：没有 `table` 记录时 `schema` 是 3、`table` 为 null；同一局几条 `table` 记录取最后一条（按记录先后，不按结束时刻），别的局的不带；只带格式里写了的项，数只收 0～1e9 的整数（布尔值、`3.0`、负数、超过上限、NaN 写 null），`best` 只留五项数字并去重，`badges` 只认 14 个称号、不认得的写 null，`chips` 最多 40、`badges` 最多 8（多出的截掉），不是对象、不是数组的写 null；`players` 缺失、不是数组、空数组、多于 4 项、有非对象、座位重复或超出 1～4 时整张表为 null，两项以上 `self` 为 true 时所有 `self` 都为 null；名字、等级、局号、时刻、线名都不带；清理过的都过 `clean_report`、`explain_table` 为 None；`TABLE_STATS`、`TABLE_BADGES` 和 `vision/result_table.py` 的 `STATS`、`BADGES` 一致。第 3 版外层的 `unadvised`：没有记录时是空数组；取这一局、时刻不早于 `since` 的，按日志先后最多 50 条（多的截掉并记一行）；每项只带六个键，原因只留三种、不重复、一项都不剩的项丢掉（整块照旧是数组），`round`、`star` 只收 0～10 亿的整数，`advised` 是布尔值，`source` 只认 `SOURCES` 里的值，`pick` 是文字或 null，`select_no` 不发；都过 `clean_report`、`explain_unadvised` 为 None。
  - `UnadvisedTest`：`unadvised` 的拼法：一次都没有时 `[]`；只带六个键、按日志先后；清理原因、轮次、星数、布尔值、来源和选择；原因不合格的项丢掉、整块不变成 null；别的局、`since` 以前、时刻读不出的不带；多于 50 条截到 50；过 `clean_report`、`explain_unadvised` 为 None。
  - `StoreTest`：报告紧凑写（没缩进、UTF-8 不带 BOM，`schema` 是 3）、先写临时文件再改名、同一局覆盖同一份且编号不变、关着开关时不写；报告编号是本机另存的随机值、换行、局号拼起来的 MD5（同收数据的服务那一头的算法；本机编号照旧写进报告，但不参与算编号，换了它编号不变），`index.json` 丢了重拼还是同一个编号、同一份文件；同时识别两个客户端时两张各自的一局各拼一份，互不覆盖、不串选择。
  - `PruneClearTest`：删掉 30 天以上的、超过 50 份时删最旧的，不是报告的文件不碰；`clear` 删报告和 `index.json`；待发份数只数报告文件。
  - `BackfillTest`：离现在不到 `GAME_GAP` 的不拼、拼过的不拼、`since` 以前的不拼；有没有 gameover 都拼，记了结束的也等到隔够 `GAME_GAP`（误认的结束以后撤销了新一局、接着打的，不拼这半局）；隔了很久才认到的结束（离最后一次三选一超过 `GAME_GAP`）不带进 `end`，`index.json` 里这一局 `ended` 为 false；同一次三选一的前一条在别的局、后一条在这一局时，只补拼后一局（前一局没有别的三选一就不拼）；索引里记的界线让下次从「界线 − GAME_GAP」起读；`index.json` 丢了再补拼，编号不变、还是同一份。
  - `TargetTest`：设了环境变量就发到它；开发版、复刻的版本只存不发；发行版加官方源代码才发到正式地址。
  - `SendTest`：gzip 压缩、头和两个超时照传；200 删掉；400、413、415 删掉并记一行（带状态码和回话里的 error，不带报告内容）；429、503、411、连不上都留着并停、同一次运行只记一行；一次最多发 `SEND_MAX` 份；中途关掉开关就停。
  - `InstallTest`：本机编号、随机值都是随机的 32 位小写十六进制；报告编号（`report_id_for`）同一随机值、同一局总是同一个，换了随机值或局号就不同。
  - `IntakeTest`：本机起作业站的收数据服务，发一份、核对收下并存在数据文件夹、本机那份删掉。
- **检证对象**：`advisor/game_report.py` 的 `build`、`assemble`、`backfill`、`prune`、`clear`、`pending_count`、`send_pending`、`target`、`new_install`、`new_secret`、`report_id_for`、`folder`、`report_path`、`MAP_SOURCES`/`END_MAP_SOURCES`、`TABLE_STATS`/`TABLE_BADGES`、`_table`、`_unadvised_item`，以及它用到的 `advisor/logbook.py` 的 `records_between`/`MAP_SOURCES`/`UNADVISED_REASONS`、`advisor/fileio.py` 的整份写入、`advisor/winhttp.py` 的 `Session.post`。
- **什么时候要跑**：改了 `advisor/game_report.py`、报告格式（字段、上限、状态码怎么处理），或改了 `advisor/logbook.py` 记 offer 的字段（`client`、`map_source`、`difficulty_source`、`owned_sources`、`owned_panel`）、记 `table` 的字段、记 `unadvised` 的字段，或改了 `advisor/vision/result_table.py` 的 `STATS`、`BADGES`、`advisor/winhttp.py` 的 POST。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_game_report`（上次约 1 秒）。
- **需要什么**：报告格式的检查函数（`report_format.clean_report`）和收数据服务（`intake.py`）在收数据服务那一头，这个仓库里没有：环境变量 `ACC_REPORT_CHECK` 指向放它们的文件夹时，核格式和端到端那几条才跑；没设时自动跳过，其余照跑。发送用假的联网会话，端到端只连本机 `127.0.0.1`。
- **输出怎么看**：同总则；拼出来的报告都过一遍格式检查，失败时 `ReportError.code` 是哪个字段不对（例如 `bad_field:offers[0].state.star`）。
- **出错时一般是哪里不对**：
  - `BuildTest` 失败 → 报告里的字段、类型、上限或拼法（取哪一条、`since` 怎么比、分享码怎么写）改了；先看断言里的字段路径，再看 `REPORT_FORMAT.md`。`test_result_*` 失败 → `result` 取的不是 `end` 那一条，或 `OUTCOMES`、`HOWS` 的认法放宽了（收数据的服务认到别的值会整份回 400，这一份就删了）。
  - `TableTest` 失败 → 取哪一条 `table` 记录、清理的规矩（整数范围、`best`、`badges`、`chips` 的上限、`players` 的结构）改了，或两组常量和 `vision/result_table.py` 对不上。
  - `UnadvisedTest` 失败 → `unadvised` 取哪几条（局号、`since`、先后、上限）、清理的规矩，或 `logbook.record_unadvised` 的写法改了。
  - `StoreTest`、`PruneClearTest`、`BackfillTest` 失败 → 存的位置、`index.json` 的写法、界线或清理的规矩改了；`test_recent_game_over_waits`、`test_going_on_after_false_end` 失败 → 补拼不再对所有局都等 `GAME_GAP`（`backfill`、`_games`）。
  - `SendTest` 失败 → 状态码怎么处理（删、留）、请求头或 gzip 改了；400/413/415 和其余状态码的分法见 `REPORT_FORMAT.md` 的「服务怎么回」。
  - `IntakeTest` 失败 → 报告过不了格式检查（收数据服务回 400），或发送、删除、`index.json` 的先后不对。
