# 测试指南 · 识别

这是[《测试指南》](../测试指南.md)第八节的一章：每个测试文件一节，每节的结构见那里第八节的开头。


这一章管从截图里读游戏画面的那套识别（advisor/vision）和识别流程本身：轮次、地图、候选筹码、星数、星币、面板、对局外画面，以及什么时候重读。

## test_pipeline.py：识别流程本身怎么走——什么时候重新识别、三选一出来多快给推荐

- **测试观点**：
  - `QuickRecheckTest`：同一个筹码三选一界面上只查绿框、不重复做整页文字识别；只有筹码名变了（重新刷新）、窗口大小变了才重新完整识别；读得不完整（概率行读不到、图标没加载）时隔约半秒再读一次；推荐给出以后不再按时间定时整页重认。快速盯绿框只截的那一块（识别流程放上的要盯的那一份）在真截图上查出的选中项和整幅上的相同（`test_band_same_as_full`，其余见 test_pick_watch）。
  - `ProgressInSelectTest`：三选一界面出现期间也读进度条；读到与已采用的不同的格时下一帧马上补读一次，两次一致才采用；每次三选一里只补读一次；离开三选一就清掉补读；一条用真实的进度条识别核对（任务奖励「进度 -1」使进度条当前格由第 11 格退回第 10 格）。
  - `UnchangedScreenTest`：战斗等画面在认定「不是三选一」「不是选角界面」、又读不到轮次之后，画面没变就不再重复识别；用画面指纹判断变没变；淡入到一半的那一帧没认出来时不能记成「不是」；按热键强制重新识别；画面静止时每轮的读轮次、认选角界面也沿用结论，地图变成未知时连任务栏一起再读一次。
  - `BordersAndTaskPanelTest`：去掉画面四周的黑边（先只看靠边一圈的快路，结果必须和整套算法完全相同）；任务栏是否打开的判断（不做文字识别）。
  - `FlowTest`：识别流程接通会话后的周边流程——窗口过小／独占全屏／界面缩放的「不支持」提示、任务栏一打开就读、提示打开「查看筹码」面板并读取面板、读队友和自己那一行、存角色模板、畸变组件、游戏语言提示。
  - `OutOfGameFlowTest`：对局外的画面（结算画面、房间、「匹配成功」画面、奖励画面）结束本局、开新的一局；本局已结束时不再读星数；`[map_detect]` 几个开关关掉后的行为。
  - `TimedReadsTest`：每一项读数各自计时（性能统计里记 `read:<项>`）。
  - `FastAdviceTest`：三选一出来约 2 秒内先给推荐、下一帧再读一次核对（轮次、进度条、星数够新时）；手动模式、刚从暂停恢复、本局还没读到过轮次、太久没读到轮次时照旧先读完再给；核对读到的不同就重算。紧接着出现的另一次三选一（概率行换了；或概率行一样、三张全换、刷新次数没少）也重新读几帧星数、先给推荐、下一帧核对，并先结算上一次；概率行一样、次数少了 1 或读不出的照旧是同一次里刷新，不读（`test_same_row_select_checked_again`、`test_same_row_refresh_not_checked`）。
  - `IdleOneReadTest`：不在三选一时一帧最多做一项读数，其余到期的排到接下来几帧；先后和「一帧做完」时相同；没到点、不用读的项不占一帧；三选一插进来时没做完的这一轮作废；任务栏刚打开立即读；按热键开始的一轮不沿用「画面没变」的结论；一项出错也占一帧。
  - `SelectRereadTest`：三选一刚出现那一帧读得可疑（刷新次数读成 0、或概率行三项加起来是 100 却不在概率表里）时，过约半秒（低配模式乘倍数，`reread_s`）再把三张卡完整认一次：认出的不同就按新的重算推荐、相同就不动；每次三选一只补认一次；读得正常的不补认。刚出现那一帧没读全（概率行还读不到）时，按第一次读全的那一次判可不可疑；补认和读左上角（核对、再读）同一帧到点时这一帧只读左上角，补认挪到下一帧；给过推荐以后读得不完整（这里是概率行读不到、筹码名都读出来了）时半秒后的再认（`SELECT_RETRY_S`）和核对同一帧到点时同样只核对、再认挪到下一帧，只有再认到点时照旧当帧做（`test_retry_moved_after_check`；还没给过推荐的反过来，见 `FirstRereadTest`）；0 星时补认读对刷新次数以后，记账改判为刚升到 1 星、推荐按 1 星算。补认读到 2 次以后刷新、又读成 0 的仍是同一次（不再补认）；补认仍读成 0 的上一组之后三张全换的一组算紧接着的另一次，照样补认一次（`test_reread_after_zero_new_select`）。
  - `FirstRereadTest`：三选一刚出现那一帧有筹码没读出来、还没给过推荐（记账先不提示无法识别）时，先完整重读三张卡、读左上角（核对等）排到重读之后，两样不在同一帧：再认（`SELECT_RETRY_S`）和核对同一帧到点时这一帧只重读、读全了马上给推荐，下一帧才核对（`test_reread_first_then_check`）；下一帧来得早、再认还没到点时核对也先等着（`test_check_waits_for_reread`）；真读不出来时再认那一帧仍读不全、离刚出现过了半秒以上才提示无法识别，之后照常先核对（`test_unreadable_after_wait_then_check`）；手动模式刚出现那一帧照旧先读完左上角，同样不当场提示（`test_manual_mode`）。读左上角排在后面有上限：提示过无法识别、给了推荐、请选择角色、三选一关掉以后都照常先读左上角，两次三选一紧接着出现、等着时被隐藏又显示、手动模式补读跳变的轮次时也一样（`test_check_not_held_after_close`、`test_another_select_rereads_first`、`test_hero_pending_prompt_then_check`、`test_hidden_while_waiting`、`test_manual_recheck_after_reread`）；核对排在后面、读到的轮次不同时照「先给推荐、下一帧核对」重算，星数照旧读前几帧（`test_check_after_reread_recomputes`）。另核对读不全的那一帧留一张给诊断包（`unread_shot`）：时刻是当时的本地时刻，画面复制一份，之后来的帧、交来识别的那块内存被改都不改它；只查绿框的帧、刷新动画的中间帧不留，刷新后等满了还读不全的那一帧照样留（`test_unread_frame_kept`、`test_refresh_frames_not_kept`）；复制出错时这一次不留、之前的照留，这一帧的读数照常交出，不算识别出错（`test_copy_error_keeps_old_shot`）。
  - `HudRetryTest`：三选一开着时读左上角没成功（核对那一帧、确认变小的轮次那一帧、手动模式先读完那一次出错或读不出轮次）时隔约 1 秒（`retry_s`）再读，每次三选一最多再读 3 次（`HUD_RETRIES`），一直出错也不每帧都读；再读的次数在新出现一次三选一、开新的一局、从暂停恢复、手动模式开关变了时清零。
  - `WatchRulesTest`：`data/rules.toml` 的 `[watch]` 数值（平时看画面的间隔 500 毫秒、低配乘 2 得 1000 毫秒；已删掉的旧「整页重认间隔」不再出现；读星币数的间隔与代码里的默认值一致）。
- **检证对象**：`advisor/vision/pipeline.py` 的 `FrameProcessor`（`process`、`settle`、`_read_select`、`_Unchanged`、`trim_borders`、`_edges_lit`、`_fingerprint`、`_same`、`_timed`，以及三选一补认与左上角再读（`_reread_at`、`reread_s`、`_hud_retries`、`_hud_retry_at`、`retry_s`、`HUD_RETRIES`），刚出现时读不全先重读（`_top_due`、`_top_waits`、`_last_hints`）和读不全那一帧的留存（`_note_unread`、`_copy_frame`、`unread_shot`），还有 `MIN_UI_SCALE`、`COINS_MS`、`SELECT_RETRY_S` 等常量）；`advisor/session/core.py` 的 `WatchHints`（`select_fresh`、`select_unread`）；`advisor/session/` 的 `Session`、`Settings` 与 `on_frame`/`on_progress`/`on_out_of_game`/`build_state`；`advisor/vision/hud.py` 的 `task_panel_visible`；`advisor/vision/screens.py` 的 `OutOfGame`；`advisor/vision/chip_select.py` 的 `difficulty_of`（概率行是不是概率表里的一行）；`data/rules.toml` 的 `[watch]`（含 `select_reread_ms`、`hud_retry_ms`）、`[map_detect]`。
- **什么时候要跑**：改了 `advisor/vision/pipeline.py`（尤其读取时机、三选一无变化判定、三选一补认与左上角再读、刚出现时读不全的重读、读不全那一帧的留存、去黑边、计时）、`advisor/session/` 的会话推进，或 `data/rules.toml` 的 `[watch]`（含 `select_reread_ms`、`hud_retry_ms`）、`[map_detect]` 之后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_pipeline`（上次约 1.8 秒；不是慢测试）。
- **需要什么**：一部分用例要识别样本截图——`QuickRecheckTest`、`ProgressInSelectTest`、`UnchangedScreenTest`、`BordersAndTaskPanelTest`；其余（`FlowTest`、`OutOfGameFlowTest`、`TimedReadsTest`、`FastAdviceTest`、`IdleOneReadTest`、`SelectRereadTest`、`FirstRereadTest`、`HudRetryTest`、`WatchRulesTest`）识别函数都换成假的，不需要样本。不需要显卡、不弹真窗口。
  - 样本用环境变量 `ACC_SAMPLES_DIR` 指定目录，默认是仓库根目录的 `samples/`。用到的截图（都在 `samples/`）：三选一刷新前后和选中各一张、战斗画面一张、任务三选一图标未加载和已加载各一张、进度条退回一张，另有几张对局画面；`BordersAndTaskPanelTest` 会把 `samples/` 里 2026 开头的每张截图都过一遍。不读任何标注文件。
- **输出怎么看**：缺样本时整类跳过，跳过原因写明缺哪个文件，形如「没有识别样本：缺 samples/<文件名>（准备方法见 docs/测试指南.md「准备识别样本」）」；没有别的特有输出，也不写临时文件。其余同总则。
- **出错时一般是哪里不对**：
  - `QuickRecheckTest.test_reroll_triggers_full_read_not_timer` 失败 → 三选一什么时候重新完整识别的条件改坏了（应当只在筹码名变了／窗口大小变了时重认，不能按时间定时重认）；去看 `advisor/vision/pipeline.py` 的 `_read_select` 和 `FrameProcessor.process`。
  - `QuickRecheckTest.test_selected_frame_found_without_ocr` 失败 → 「只查绿框」或「选中了哪个」的判定（`picked` 事件）改坏了。
  - `QuickRecheckTest.test_incomplete_read_retried`、`test_icons_not_loaded_then_loaded` 失败 → 读得不完整时的重试间隔（`SELECT_RETRY_S`）、稀有度缺失的判定，或出场动画后筹码名位置变了要马上重认的逻辑改坏了。
  - `ProgressInSelectTest` 失败 → 三选一期间进度条的补读规则（`_recheck_progress`、只补一次、两次一致才采用）或 `Session.on_progress` 的「连读两次才采用」改坏了。
  - `UnchangedScreenTest` 失败 → 画面没变的判定（`_Unchanged`、`_fingerprint`/`_same`、沿用上限）改坏了，或按热键强制识别、或「地图未知时连任务栏一起读」的逻辑改了。
  - `BordersAndTaskPanelTest.test_trim_borders`、`test_edges_first_same_as_full`、`test_random_same_as_full` 失败 → 去黑边规则（黑边亮度门槛、至少占一边的比例、去掉后至少剩多少、快路判据 `_edges_lit`）改坏了。数值是有意改的就同步改断言。
  - `BordersAndTaskPanelTest.test_samples_have_no_borders` 失败 → 新放进 `samples/` 的某张截图自带黑边，或快路判据改了。
  - `BordersAndTaskPanelTest.test_task_panel_visible` 失败 → 任务栏是否打开的判断或样本清单改了。
  - `FlowTest.test_small_window_and_fullscreen`、`test_small_window_judged_by_ui_scale` 失败 → 「不支持」的判定（客户区宽度门槛、界面缩放、独占全屏）或提示文字改了；提示文字是有意改的就同步改断言。
  - `FastAdviceTest`、`IdleOneReadTest` 失败 → 读取时机（先给推荐、下一帧核对、一帧一项）改坏了，这是这一块最核心的行为。`test_same_row_*` 失败 → 识别流程问的「是不是紧接着出现的另一次」（`advisor/session/selection.py` 的 `starts_another`、`fresh_offer`，经 `WatchHints.another_select`）和记账的结论对不上，或 `_select_reads` 没按它重新读。
  - `SelectRereadTest` 失败 → 三选一读得可疑时的补认（什么时候补、只补一次、认出不同才重算推荐，`_reread_at`/`reread_s`；没读全时等第一次读全再判 `_suspect_due`；和读左上角同一帧到点时挪到下一帧 `_top_due`），或低配模式乘倍数改了；`test_retry_moved_after_check` 失败 → 读得不完整时的再认没和核对错开（`_read_select` 里用缓存的条件）；`test_fixed_rerolls_level_up` 单独失败时多半是记账那边（`Session._open_judged`）没跟着补认重新判；`test_reread_once_per_offer`、`test_reread_after_zero_new_select` 失败也可能是记账「紧接着的另一次还是刷新」的判据（`selection.py` 的 `fresh_offer`）改了。
  - `FirstRereadTest` 失败 → 刚出现时读不全的先后（`_read_select` 定 `_top_waits` 的条件：要读左上角、缓存的读数不完整、上一帧做完时的提示里 `select_fresh` 为真；`_select_reads` 这一帧不读左上角），或记账那边「刚出现时先等一等」（`selection.py` 的 `hold_frame`、`fresh`）改了；`test_unreadable_after_wait_then_check` 失败多半是等满的条件（`HOLD_RETRY_S`、`HOLD_READS`、`HOLD_MAX_S`）或提示过以后不再算刚出现（`_told`）改了。`test_unread_frame_kept`、`test_refresh_frames_not_kept` 失败 → 留不留那一帧（`_note_unread` 看的 `select_unread`，记账的 `SelectTracker.unread`：刷新动画的中间帧不算），或留的不是复制的一份（截取的缓冲被复用时跟着变）；`test_copy_error_keeps_old_shot` 失败 → 复制出错时报成了识别出错或拦住了这一帧的读数（`_note_unread` 要自己接住出错，不经 `_safe`；复制在 `_copy_frame`）。
  - `HudRetryTest` 失败 → 读左上角没成功时的再读（间隔、最多 3 次、次数清零的时机，`_hud_retries`/`_hud_retry_at`/`retry_s`/`HUD_RETRIES`）改了。
  - `WatchRulesTest` 失败 → `data/rules.toml` 的 `[watch]` 数值或 `pipeline.COINS_MS` 改了；数值是有意改的就同步改断言。

## test_pick_watch.py：三选一显示着时另外快速盯绿框、名字突然被盖住的一帧不采用、留下记选中用的那一帧

- **测试观点**：
  - `BandTest`：快速盯绿框只截 `select_band` 那一块做快速复查，结果和在整幅画面上做的逐位相同（随机画面、靠着画面边上要裁掉一部分的位置、单位长度取整到 .5 的也有）；名字那一条被盖住时对不上；截到的那一块没盖住要看的范围时当作对不上，不会用负数下标绕到另一头取像素。用真截图核对的一条在 test_pipeline 的 `QuickRecheckTest.test_band_same_as_full`。
  - `ExchangeTest`：识别流程和盯绿框的线程之间的交换（`PickWatch`）：按完整识别建要盯的（没有位置信息时不盯）；只留最近一次看到选中的；识别流程换要盯的、取走看到的之前先等正在查的那一次查完（有上限；换之前本来就没有要盯的时不等，那时查完的也收不进来）；换下来的那一份查完看到的不收；关掉以后不再给要盯的。
  - `WatcherTest`：盯绿框的线程（`PickWatcher`）：查一次的结果（看到选中、没有绿框、名字那一条对不上）和统计；按截到画面的时刻记（落后的那一点减掉）；客户区大小不对时不查；暂停时不截；出错只记一次错误记录、关掉取帧对象、隔一会儿再查，也清掉「正在查」；没有要盯的满 30 秒关掉截取；识别流程重建时换上新的交换；真起线程能看到选中、能停下。
  - `QuickPickTest`：读左上角的那一帧要 1 秒、绿框只出现 0.3 秒，界面关掉时记成那一张（`read:selected` 带 `watch`）；对照：没有盯绿框时请补录；补认那一帧完整识别要 1 秒时同样记上（同一组又认了一次、换了一份要盯的，这一帧里看到的照样交，按这一帧的读数交），这一帧读到的是另一组时上一组看到的不交；两帧之间看到的在看不到三选一的那一帧之前交给记账；这一帧画面上选中的和读左上角那 1 秒里改选的，按截到画面的先后记成后改选的；换了一组换成新的一份、暂停（`watch_idle`）、被隐藏、关掉时不再盯，又出现时照常；换下来的上一组查完看到的不交；暂停之前看到的恢复后照先后交；识别流程重建时接到新的交换上；真起盯绿框的线程时它不调用记账（交给记账的都在识别流程的线程里、持着锁）。
  - `CoveredTest`：实际对局里记错过的读数序列（第 3 张读成「标记」、稀有度读不出、选中认成第 3 张）那一帧不采用，记账照旧、不发新推荐，`read:select_covered` 记下读到的和为什么不采用，之后按之前的位置看到第 2 张的绿框，界面关掉记成标靶；对照：照常采用时记成标记喷罐。名字那一条对得上的那一帧（补认）按之前的位置重新判断选中项。算被盖住的（只变了一两张、至少一张没变）：之前读到 2 次、这一帧读成 0 的；读不出、只读到共同部分的；变了两张（读成另一个筹码、读不出）、一张没变的。照常采用的（不误伤）：三张全变了的——刷新动画里三张都读不出、次数还没变的那一帧（下一帧新的一组读清楚了、次数还是之前的，照旧按刷新处理，不结算上一组、不弹补录、不另起一次；刷新前点过的那张作废，界面关掉时不记成它），紧接着的另一次中间夹着这样的过渡帧时同样按刷新处理（有意的取舍：丢掉上一次选的，这一次关掉时请补录）；刷新以后的新一组（刷新动画里读不全的也算）、之前只读到 1 次又读成 0 的、紧接着的另一次、刚出现还没给过推荐时的读不全、变了的那一张读得准、稀有度读得出（只读到共同部分的照旧先等一等）、名字没变的补认、记账正在等候选读全、玩家改正了候选以后同样读错的帧。
  - `ShotTest`：记下选中项时留一张（识别流程的读数留整幅画面，盯绿框看到的留它截的那一块、另记 `watch` 和那一块的位置），同一张一直选着不换、改选了换；不采用的那一帧留一张（连同读到的）；复制出错时这一次不留、之前的照旧，不算识别出错。
- **检证对象**：`advisor/vision/pick_watch.py`（`PickWatch`、`PickWatcher`、`Target`、`Sighting`）；`advisor/vision/pipeline.py` 的 `FrameProcessor`（`_watch_frame`、`_watch_target`、`_watch_later`、`_deliver_watch`、`watch_idle`、`resumed`、`_covered`、`_covered_frame`、`_note_covered`、`_note_picked`、`pick_shot`、`covered_shot`）；`advisor/vision/chip_select.py` 的 `select_band`、`reread_selected`、`name_strip`、`_selected_column`（`origin`、`shape`）；`advisor/session/selection.py` 的 `SelectTracker.told`、`noted`、`note_selected`（`watch`）；`advisor/session/core.py` 的 `WatchHints`（`select_told`、`select_noted`）；`advisor/ui/overlay_parts/app.py` 的 `OverlaySession`（改正候选）。
- **什么时候要跑**：改了 `advisor/vision/pick_watch.py`；改了 `advisor/vision/pipeline.py` 的三选一（`_read_select`、`process` 里交给记账的先后）；改了 `chip_select.py` 的快速复查、名字那一条、绿框；改了 `advisor/session/selection.py` 的选中项记录或三选一的状态。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_pick_watch`（约 3 秒；不是慢测试）。
- **需要什么**：不需要识别样本：画面是造的（三张卡的名字那一条各画一块按筹码名定的大色块，选中的那一栏画一圈亮绿框，「盖住」是在名字那一条上盖一块浅灰），完整识别和读左上角等换成假的，时钟由测试拨。真起线程的三条（`WatcherTest.test_thread_runs_and_stops`、`QuickPickTest.test_no_session_calls_outside_lock`）用真时钟，各要零点几秒。
- **输出怎么看**：没有特有输出，也不写临时文件。其余同总则。
- **出错时一般是哪里不对**：
  - `BandTest` 失败 → 只截一块时的换算（`_crop`、`_green_boxes`、`_strip_boxes`、`select_band`）和整幅上的算法不一致了：改了 `_selected_column`、`name_strip` 的范围时两边要一起改。
  - `ExchangeTest`、`WatcherTest` 失败 → 交换的先后（`publish` 先等正在查的那一次、`end` 只收现在要盯的那一份）或线程的节奏、出错处理、空闲关掉截取改了。
  - `QuickPickTest` 失败 → 看到的选中项交给记账的时机或先后（`_watch_frame` 按截到画面的时刻分前后、`_watch_later`、看不到三选一的那一帧先交），或停下的时机（`_watch_target` 返回 None 的几种）改坏了；`test_no_session_calls_outside_lock` 失败说明有在别的线程里调用记账的地方。
  - `CoveredTest` 失败 → 不采用的条件（`_covered`：之前读全的那一组、只变了一两张（至少一张没变）、变了的都读不准而且稀有度读不出、刷新次数没少、给过推荐、不是另一次、不在等候选读全）改了；只有某一条「照常采用」的失败时，多半是那一种情形被误判成了被盖住（三张全变了的几条失败，多半是「至少一张没变」那一条丢了）。
  - `ShotTest` 失败 → 留不留那一帧（记账的 `SelectTracker.noted`，`WatchHints.select_noted`）、留的不是复制的一份，或复制出错时报成了识别出错。

## test_pipeline_sequences.py：把一局真人对局的截图按时间回放，逐步核对整局的识别和记账

- **测试观点**：
  - `RealGameReplayTest`（整局回放，把 1600x855 的预览图放大回客户端原尺寸）：整局不能被判成新的一局；选角界面出现在对局开始前不算新的一局；地图从「匹配成功」画面留给这一局；组队房间和结算画面上不结束、结算画面恰好结束一次并保留状态；难度按概率行和画面读数；进度上限从进度条上的红叉读出并记住；起始角色故意设错后能自动改对；轮到自己行动（头像在做动画）时也按头像认出自己那一行、读到星数；全队任务三选一按任务来源算；升星三选一按升星算；第 10 轮金色飞星优先于星币锤；「查看筹码」面板读到的已有筹码与标注一致；选转账对象的画面不把队友的星数当成自己的；每次三选一传进推荐的已有筹码、轮次、刷新次数与标注一致；默认推荐里的「必选」筹码是首推。
  - `GameEndSequenceTest`：把「一局结束、下一局开始」拼起来回放（结算画面→奖励画面→组队房间→第 1 轮），核对结算画面结束本局恰好一次、房间上勾选的难度留给下一局、连续两次读到第 1 轮时开新的一局且不可撤销。
  - `PreviewSizeTest`：预览图按 1600x855 原样喂（相当于窗口就这么大）和放大回客户端原尺寸，认选角界面、认角色、读面板、读任务栏的结果相同。
- **检证对象**：`advisor/vision/pipeline.py` 的 `FrameProcessor`（真识别）+ `advisor/session/` 的 `Session`（`on_frame`、`on_hud`、`on_progress`、`on_progress_cap`、`on_panel`、`on_out_of_game`、`judge_source` 等）；识别都在 `advisor/vision/chip_select.py`、`hud.py`、`stars.py`、`chip_panel.py`、`screens.py` 里。回放方式：每张截图停两帧，第一帧在拍摄时刻，之后每帧隔 4 秒（测试里的 `replay`；截图回放工具 `tools/replay.py` 是同样的回放，每张停三帧，见软件维护说明「开发工具」）。
- **什么时候要跑**：改了 `advisor/session/` 的新一局判定、难度、进度上限或记账，改了 `advisor/vision/` 里轮次／星数／三选一／面板／对局外画面的识别，或改了真截图的回放方式之后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_pipeline_sequences`（上次约 0.6 秒；慢测试）。
- **需要什么**：识别样本截图和标注。要 `samples/labels.json`、`samples/stars_labels.json`，以及 `samples/` 里同一局（文件名以 20261002_21 开头）的截图不少于 20 张；`GameEndSequenceTest` 另要 6 张跨两局的截图。用到的标注字段：`labels.json` 每条的 `file`、`scene`、`round`、`rerolls`、`probabilities`、`picked`、`owned`、`team`、`source`；`stars_labels.json` 每条的 `file`、`own`、`own_row`。`panel_labels.json`、`tests/data/coins/labels.json` 这一组用不到。
- **输出怎么看**：缺 `labels.json` 或 `stars_labels.json`，或这一局的截图不足 20 张时，`RealGameReplayTest`、`PreviewSizeTest` 整类跳过；`GameEndSequenceTest` 单独按缺的那 6 张文件名跳过。跳过原因写明缺哪个文件。没有别的特有输出。其余同总则。
- **出错时一般是哪里不对**：
  - `test_no_new_game` 失败 → 整局里被误判成新的一局（去看 `advisor/session/newgame.py` 的判定和 `on_hud`/`on_stars` 的互相印证），或窗口／语言的「不支持」判定改了。
  - `test_map` 失败 → 地图从「匹配成功」画面留给本局的逻辑（`advisor/session/core.py` 的 `_apply_pending`、`_entered_game`）改坏了。
  - `test_lobby_and_result_screens` 失败 → 结算画面结束本局的判定，或难度依据（结算画面／「匹配成功」画面）改了。
  - `test_hero` 失败 → 角色立绘识别，或「起始角色设错后能自动改对」改了。
  - `test_own_turn_stars` 失败 → 按头像在自己那一行读星数（`advisor/vision/stars.py`）改坏了。
  - `test_team_task_selects_use_task_source`、`test_levelup_selects_use_levelup_source` 失败 → 三选一来源判定（`Session.judge_source`、`chip_select._team_banner`）改了。
  - `test_round10_gold_star_over_coin_hammer`、`test_must_pick_chips_first`、`test_owned_passed_to_advice`、`test_advice_round_and_rerolls` 失败 → 推荐引擎或记账（已有筹码／轮次／刷新次数）改坏了。
  - `test_chips_panel` 失败 → 「查看筹码」面板的识别（`advisor/vision/chip_panel.py`）或写入已有筹码改了。
  - `test_coin_transfer_keeps_stars` 失败 → 选转账对象的画面上的星数判定改坏了。
  - `GameEndSequenceTest` 失败 → 「本局已结束」之后开新一局的判定（连续两次读到同一轮次、不可撤销）或房间难度的沿用改了。
  - `PreviewSizeTest` 失败 → 窗口尺寸换算（预览图放大回原尺寸）或缩放后的识别改坏了。
  - 标注里的数值（第几轮、刷新次数、已有筹码、选中的筹码）是有意改的就同步改断言。

## test_vision_matrix.py：按 `samples/` 全部人工标注逐张核对各识别模块

- **测试观点**：
  - 样本本身：三份标注引用的截图都在、`stars_labels.json`/`panel_labels.json` 里的每张图 `labels.json` 都有、反例画面一种不缺；原图尺寸和标注一致、缩小版确实更小且界面缩放不低于最小窗口。
  - 三选一（`chip_select`）：过预筛；筹码名、稀有度、刷新次数、概率、选中项、全队三选一那一行和标注一致、自检提示和标注的 `expect_problems` 一致（没写的为没有；截图早于游戏更新、资料已照新版改的样本，自检照资料会报的那几条写在标注里，识别评测工具 `tools/eval_vision.py` 同样照它核对）；反例（大厅、选角、选皮肤、载入、结算、卡牌商店、玩家详情、转账、对局、战斗、面板、任务栏）都不能读成三选一。
  - 左上角（`hud`）：标了轮次的读对（压暗或被挡的画面读不出不算错、读错算错）；对局外画面读不到轮次；任务栏开着时按任务文字认地图、其他画面不误认地图；进度上限（红叉格号）读对或读不出（不能读错）；标了进度当前格的样本原图和缩小版都读对。
  - 两条「曾经的缺陷」锁定用例：4:3 小窗口里第 2 轮三选一不得读成 20；4:3 小窗口里第 13 轮面板画面要读得出 13。
  - 选角界面（`hud.is_select_screen`）：只有选角界面判成选角界面。
  - 对局外画面（`screens.parse_out_of_game`）：标了 `screen` 的读数与标注完全相同（变灰的组队房间难度可读不出）；其余样本不能认成结算页、匹配成功页、房间或奖励页。下方区域那一次识别同理。
  - 角色（`hero.read_hero`）：看得见立绘的画面认出标注的角色；立绘被挡住（面板、战斗、选角）时认不出；其余画面不能认错。
  - 玩家列表（`stars`）：自己的星数、按头像认出的行和标注一致，被挡住/没有列表/列表里没有自己时返回 `None`；每行亮着几颗星读对、标注之外多出来的行只能是推算的（看不见）；队友角色和是否电脑不读错、自己那一行看得见时要认出是哪一行；转账画面上没有自己的星数。
  - 「查看筹码」面板（`chip_panel`）：筹码、页签个数、选中页签、点选格子与标注一致、没有无法识别的格子和自检提示（截图早于游戏更新、资料已照新版改的样本，筹码、认不出的格数、识别提示按 `panel_labels.json` 的 `expect_read`，识别评测工具 `tools/eval_panel.py` 同样照它核对）；其余样本都不是面板；玩家详情弹窗不是面板。
- **检证对象**：各识别模块——`advisor/vision/chip_select.py`（`read_chip_select`、`looks_like_overlay_screen`）、`advisor/vision/hud.py`（`read_hud`、`is_select_screen`、`read_pregame`、`read_progress`、`read_progress_cap`、`task_panel_visible`）、`advisor/vision/screens.py`（`parse_out_of_game`）、`advisor/vision/hero.py`（`read_hero`）、`advisor/vision/stars.py`（`diagnose`、`read_players`）、`advisor/vision/chip_panel.py`（`read_chip_panel`）。
- **什么时候要跑**：改了任何一个识别算法、`advisor/vision/anchors.py` 里的几何量、或 `advisor/vision/pipeline.py` 里决定「哪种画面跑哪些识别」的流程；改了标注或换了样本也要跑。是覆盖面最广的一项识别回归。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_vision_matrix`（慢测试：识别慢，单进程约 4~5 分钟；`setUpClass` 里把样本分给几个子进程一次读完，子进程数取逻辑核数的一半、最多 8 个。上次的记录约 0.6 秒，那次多半是没有样本整类跳过了）。
- **需要什么**：`samples/` 下全部样本 + `labels.json`、`stars_labels.json`、`panel_labels.json` 三份标注；还要 `assets/` 里的图片（`assets/heroes/UT_Hero_RolePhoto_*.png`、`assets/profiles/UT_Hero_ProfilePhoto_*.png`、`assets/chips/UT_Relic_50081.png`、`assets/stars/star_bar.png`）。会真的建文字识别引擎（子进程里每个 1 个线程）。不是实机测试，不开真实窗口。
- **输出怎么看**：
  - 缺样本或缺 `assets/` 时整类跳过：前者原因同 test_stars 的 `samples_reason`，后者「没有识别用的图片（assets/）」。
  - 某个模块识别时抛异常时，这一项失败并附上子进程里的调用栈（`识别时出错：…`）。
  - 每条断言用子测试标出是哪张图、哪个变体（原图/缩小）、哪个场景；标了 `SUSPECTED` 的已知项会跳过（当前为空）。
  - 哪些画面跑哪些模块照悬浮窗流程：三选一先过预筛再做文字识别；对局外画面的整幅识别所有样本都跑。
- **出错时一般是哪里不对**：
  - `test_chip_select_matches_labels` 失败 → 三选一的筹码名匹配、稀有度、刷新次数、概率或选中判断改坏了（看 `chip_select.read_chip_select`、`_match_chip`、`_ring_rarity`、`_count_near`、`_probabilities`、`_selected_column`）。
  - `test_other_screens_are_not_chip_select` 失败 → 预筛 `looks_like_overlay_screen` 或标题匹配放宽了，把别的画面读成三选一。
  - `test_round` / `test_no_round_out_of_game` 失败 → `hud.read_round`/`_parse_round_ex` 的两位数、菜单按钮边缘前缀处理改了；或选角界面/对局外判断改了。
  - `test_map` 失败 → `_guess_map` 的任务匹配阈值、`task_key` 或地图标记改了。
  - `test_progress_cap` / `test_progress_current` 失败 → `read_progress`/`_current_cell`/`_cap_cell` 的红叉、蓝色当前格或编号格识别改了。
  - `test_select_screen` 失败 → `is_select_screen` 的掠取范围或 `SELECT_MIN_TITLES` 门槛改了。
  - `test_out_of_game_screens` / `test_out_of_game_screens_bottom` 失败 → `screens.parse_out_of_game` 的解析器先后 `PARSERS` 或各解析器改了。
  - `test_hero` 失败 → `hero.read_hero` 的缩放搜索、`MIN_SCORE`/`MIN_MARGIN` 或 `HERO_HIDDEN`/`HERO_SHOWN` 口径改了。
  - `test_own_stars` / `test_star_rows` / `test_players` / `test_transfer_screen_has_no_own_stars` 失败 → `stars` 的星数、行数或队友识别改了（同 test_stars 的定位）。
  - `test_panel` / `test_other_screens_are_not_panel` / `test_player_detail_is_not_panel` 失败 → `chip_panel` 的格子拟合、图案比对、页签或面板判定改了；`_list_owner` 未完整识别时的处理改了。
  - `test_round_not_misread_in_small_4_3_window` / `test_round_read_in_small_4_3_window` 失败 → `read_round` 里「按位置读到的两位数要两次一致」「去掉菜单按钮边缘的前缀」这两条防护被回退了，是明显的回归。
  - 标注里写成有意改动的数值（例如某张图标注的行数、星数、稀有度）时，是**标注**该同步改，而不是代码错——先确认是改了识别还是改了标注。

## test_vision_frame.py：同一帧里沿用的识别结果和每次都从头算一样

- **测试观点**：
  - `SameFrameDiagnoseTest`：同一帧里第一次 `diagnose` 认出自己后记下皮肤，第二次按新记下的皮肤重算，结果要和新包一个 `Frame` 从头算的逐位相同（不能沿用第一次的结果）；同一帧里皮肤没再变时直接返回记下的同一个对象；角色名、别名、编号指同一个角色时共用同一份结果；认不得的角色按原文分开记（原因里写这个名字）。
  - `PanelFrameTest`：`read_chip_panel` 收到带透明通道的 BGRA 图（透明通道为 0）时，交给玩家列表识别的是去掉透明通道的 BGR 图（否则文字识别会变成一片白）；收 `Frame` 时把同一个 `Frame` 交下去，玩家列表在这一帧里不重找。
  - `LayoutOnceTest`：同一帧里先读星数再读队友、或面板找自己那一页签接着读星数时，真正干活的 `stars._find_layout` 只跑一次。
  - `FrameTest`：`as_frame` 收 ndarray 时包一层、收 `Frame` 时原样返回；`image`/`w`/`h` 正确；`ui_scale()` 同一帧只算一次并缓存；`ui_range()` 和 `chip_select.ui_range(w, h)` 相同；`stars.diagnose` 在同一帧同一个角色上只算一次。
- **检证对象**：`advisor/vision/frame.py`（`Frame`、`as_frame`）、`advisor/vision/stars.py`（`diagnose`、`_find_layout`、`read_own_stars`、`read_players`）、`advisor/vision/chip_panel.py`（`read_chip_panel` 收 BGRA/Frame）、`advisor/vision/chip_select.py`（`ui_range`）、`advisor/vision/__init__.py`（`reset_hints`）。
- **什么时候要跑**：改了 `advisor/vision/frame.py`；或改了 `stars.diagnose` 的按帧缓存、`chip_panel` 收图的处理、`chip_select.ui_range`、面板在 `Frame` 上复用玩家列表的写法。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_vision_frame`（上次约 0.03 秒。「按名字找自己那一行」那一步在测试里换成了假函数，不真的做文字识别）。
- **需要什么**：识别样本截图：对局 `20261002_213811_hud_round7_own_turn_real.jpg`、筹码面板 `20261002_013927_chips_panel_one_chip.png`、「昏迷」的筹码面板 `20261002_193857_chips_panel_knocked_out_2397x1798.jpg`（`FrameTest` 两条全黑画面不需要样本）。位置见 `tests/support/samples.py`，可用 `ACC_SAMPLES_DIR` 改。
- **输出怎么看**：缺截图时按 `samples.samples_reason` 跳过（同 test_stars 的写法）；没有别的特别输出，同总则。
- **出错时一般是哪里不对**：
  - `test_second_call_follows_learned_skin` 失败 → `frame.py` 的按帧缓存（`_stars_diagnose`）或 `stars.diagnose` 里按皮肤重算的逻辑改坏了（`_last_skin` 的用法）。
  - `PanelFrameTest.test_bgra_gives_bgr_to_player_list` 失败 → `chip_panel.read_chip_panel` 对 BGRA 的去透明通道处理改了。
  - `LayoutOnceTest` 失败 → `stars._layout` / `stars._find_layout` 的「同一帧只找一次」缓存被绕过。
  - `FrameTest.test_as_frame_and_caches` 失败 → `frame.py` 的 `as_frame`、`ui_scale`/`ui_range` 缓存或与 `chip_select.ui_range` 的口径改了。

## test_vision_robust.py：识别流程的健壮性（坏素材、极小画面、窗口变大、一项子识别出错）

- **测试观点**：
  - `MutatorCatalogTest`：畸变组件清单按资料库实际字段取（组件名 → 效果说明 `effect`，不是 `en`/`hint`）；只读到一个组件名时靠它旁边的效果说明相近文字认出。
  - `ReadAssetImageTest`：0 字节、损坏、读不到的图片经 `io.imread` 返回 `None`、不抛异常；读横条模板、角色照、筹码图标都经 `io.imread`。
  - `UserTemplateWriteTest`：用户模板整份写入（先写临时文件再换上去），写好的是完整图片、不留临时文件；换不上去时抛 `OSError`、不留半截模板也不留临时文件。
  - `MissingTemplateTest`：缺横条模板用的是专门的 `stars.MissingTemplates`，是 `FileNotFoundError` 的子类（接 `FileNotFoundError` 的地方照样接得住）。
  - `TinyFrameTest`：1×1 的画面各识别函数和 `FrameProcessor` 都不抛异常（裁出空块、缩放到识别模型会报错）；窗口过小时只报「窗口过小」；`pipeline.MIN_FRAME` 与 `stars.MIN_FRAME` 都是 16。
  - `LaterWindowDropTest`：等一帧的几十毫秒里窗口变大时，这一帧裁的是「新窗口左上角、旧窗口大小」，要丢掉（连续两个 `grab` 之间查一次窗口）；大小没变、只挪了位置、窗口没了/最小化了照常交出。
  - `GrabRegionMssTest`：`capture.grab_region` 用 `mss.MSS`（`mss.mss` 已弃用）。
  - `ParseHotkeyNoneTest`：`hotkey.parse_hotkey(None)`、`""` 返回 `None`，不抛 `AttributeError`。
  - `SubstepsIsolationTest` / `ErrorCountedOnceTest`：一项子识别抛异常时不作废整帧——已经从 `Session` 取走的事件照常返回、其余各项照常做；认三选一出错时这一帧不往下做（不把「不是三选一」交给 `Session`）；读轮次、认选角界面出错时不当成「什么都没读到」；同一个错只由接住它的那一层记一次。
  - `WatcherReportTest`：后台识别线程把一帧的事件照常交给界面，每个错写进错误记录、提示「识别出错」；没有错时不提示。
  - `ChipPanelImportTest`：面板识别模块导入失败（发行版缺文件）时面板识别不可用、其余照常，原因写进错误记录（模块只导入一次，只记这一次）。
- **检证对象**：`advisor/vision/hud.py`（`mutator_catalog`、`_find_mutators`）、`advisor/vision/io.py`（`imread`）、`advisor/vision/hero.py`（`save_template`、`user_dir`）、`advisor/vision/stars.py`（`MissingTemplates`、`_bar_template`、`MIN_FRAME`）、`advisor/vision/pipeline.py`（`FrameProcessor.process`/`settle`、`errors`、`MIN_FRAME`）、`advisor/ui/screen.py`（`FrameSource`、`NoFrame`、`_client_changed`）、`advisor/vision/capture.py`（`grab_region`、`GameWindow`）、`advisor/vision/window_capture.py`（`Support`）、`advisor/ui/hotkey.py`（`parse_hotkey`）、`advisor/ui/overlay_parts/watcher.py`（`Watcher`）、`advisor/ui/overlay_parts/wording.py`（`RECOG_ITEM`）。
- **什么时候要跑**：改了 `advisor/vision/hud.py` 的组件清单、`io.py` 的读图和识别结果转换、`hero.save_template`、`stars.MissingTemplates`/`_bar_template`、`pipeline.FrameProcessor` 的子识别隔离、`ui/screen.py` 的取帧和窗口变化处理、`capture.grab_region`、`hotkey.parse_hotkey`、`watcher.py` 的出错上报、或 `pipeline` 里面板模块的导入兜底。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_vision_robust`（上次没有记录）。多数用例把识别引擎换成假的，不需要识别样本；`WatcherReportTest` 用离屏 Qt（`tests/support/qtenv.py` 的 `app()`），不需要真实窗口。
- **需要什么**：没有特别要求（不需要识别样本截图、不需要显卡、不开真实窗口）；`WatcherReportTest` 要离屏 Qt 环境。
- **输出怎么看**：`capture_errors` 把错误记录指到临时文件，用例断言里面的条目（如「识别出错（拾星助手仍在运行）」的标题和调用栈文字）；出错上传的提示是按 `wording.RECOG_ITEM` 格式化的「角色这次没读出来，已自动重试；多次出现请点击「设置」窗口中的「检查更新」按钮，更新到最新版拾星助手。」；没有别的特别输出。
- **出错时一般是哪里不对**：
  - `MutatorCatalogTest` 失败 → `hud.mutator_catalog` 取的字段改了（应是 `data/mutators.json` 的 `effect`），或 `_find_mutators` 的单名兜底逻辑改了。
  - `ReadAssetImageTest` 失败 → `io.imread` 的损坏/缺失防护被去掉，或某个模块改成不经过 `io.imread` 读图了。
  - `UserTemplateWriteTest.test_write_failure_raises` 失败 → `hero.save_template` 的整份写入（`fileio.write_bytes`）改成了直接写，换不上去时会留半截。
  - `MissingTemplateTest` 失败 → `stars.MissingTemplates` 不再是 `FileNotFoundError` 子类，或 `_bar_template` 缺文件时改抛别的异常。
  - `TinyFrameTest` 失败 → 某个识别函数不再对过小画面返回 `None`/空块，会在 OpenCV 缩放时报错；`MIN_FRAME` 数值被改。
  - `LaterWindowDropTest.test_window_grew_frame_dropped` 失败 → `screen.FrameSource._client_changed` 的「取帧后再查一次窗口」被去掉或放宽了。
  - `GrabRegionMssTest` 失败 → `capture.grab_region` 又用回了 `mss.mss`。
  - `ParseHotkeyNoneTest` 失败 → `hotkey.parse_hotkey` 对 `None`/空串不再先返回 `None`。
  - `SubstepsIsolationTest` / `ErrorCountedOnceTest` 失败 → `pipeline.FrameProcessor` 里某一项子识别的接错、三选一失败即停、或错误的单次记账改了。
  - `WatcherReportTest` 失败 → `Watcher` 把 `process` 返回的事件或 `errors` 上报给界面的先后/内容改了。
  - `ChipPanelImportTest` 失败 → `pipeline.py` 对 `advisor.vision.chip_panel` 导入失败的兜底或错误记录改了。

## test_vision_shared.py：识别模块共用的小工具和对局外画面的解析顺序

- **测试观点**：
  - `ResultLinesTest`：`io.result_lines` 没有识别框、缺字段时返回空表；坐标按缩放倍数和裁剪左上角换回原图像素。
  - `OcrRangeTest`：`io.ocr` 空画面不调用识别引擎；范围超出画面、整个在画面外、取整后上下颠倒时都夹进画面（不交给引擎空图）；坐标换回原图像素。
  - `RerollRereadTest`：三选一右侧「刷新」按钮的剩余次数——按钮上或第一次读到的数字就用；按钮上没数字时放大重读按钮附近，重读什么都没读到时是 `None`（按钮在、数字读不出，不是 0）；重读到的「×N」按放大倍数和裁剪位置换回原图后落在按钮正下方才认。
  - `OutOfGameOrderTest`：几种画面的字样同时出现时，按 `screens.PARSERS` 的先后认第一个认得出的；识别结果里文字的先后不影响认哪一种。
- **检证对象**：`advisor/vision/io.py`（`result_lines`、`ocr`）、`advisor/vision/chip_select.py`（`read_chip_select`、`_reread_count`、`Line`、`TITLE`、`TITLE_WIDTH_U`、`ocr_scale`）、`advisor/vision/screens.py`（`parse_out_of_game`、`PARSERS`、`OutOfGame`）。
- **什么时候要跑**：改了 `io.py` 的结果转换或按范围识别、`chip_select` 的刷新次数重读、`screens.py` 的解析器先后 `PARSERS` 或各解析器。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_vision_shared`（上次没有记录；识别引擎一律换成假的，不加载模型）。
- **需要什么**：没有特别要求（不需要识别样本截图）。
- **输出怎么看**：同总则（识别引擎换成假的，没有特殊的跳过原因或临时文件）。
- **出错时一般是哪里不对**：
  - `ResultLinesTest` / `OcrRangeTest` 失败 → `io.result_lines` 的坐标换算或 `io.ocr` 的范围夹取改了（夹取后算出空块会交给引擎报错）。
  - `RerollRereadTest` 失败 → `chip_select` 的刷新次数读取或 `_reread_count` 的放大重读/坐标回换算改了。
  - `OutOfGameOrderTest` 失败 → `screens.PARSERS` 里解析器的先后被调换，或某个解析器的匹配条件放宽/收紧（结算页、匹配成功页必须排在房间、组队房间、奖励页之前）。

## test_screens.py：核对对局外画面（结算页、「匹配成功」页、房间、奖励页）的识别——整幅识别和下方区域识别都和 `samples/labels.json` 的 screen 标注一致，反例画面不误认，解析规则另用构造的文字结果单测。

- **测试观点**（按测试类）：`LabelsTest`（screen 标注本身字段齐全、取值合法、和 scene 对得上、每种画面都有样本，不读图）；`SampleScreensTest`（原图和缩小版都读了；整幅识别和标注完全相同、认出这些画面时不找加载画面的地图名；下方区域结算页读全、其余读不出可以、读到不能错；组队房间读出勾选的难度；反例两种识别都读不出、选角界面照常判成选角界面；`is_select_screen` 和 `read_pregame` 各只做一次文字识别）；`ParseTest`（构造文字结果核对解析规则：结算页要两项对上、地图名和 «（合作挑战）» 分两段时的取法、「匹配成功」页两处都要有、房间下拉框展开时列表不算当前值、勾选框拿不准时读不出、奖励页要两句话都在、`Pregame` 的新字段有默认值）。
- **检证对象**：`advisor/vision/screens.py` 的 `parse_out_of_game`、`OutOfGame`、`KINDS`，以及各解析函数 `_result_bar`、`_match_found`、`_custom_room`、`_team_room`、`_checked`、`_reward`；`advisor/vision/hud.py` 的 `read_pregame`（`Pregame`）和 `is_select_screen`；`advisor/data.py` 的 `DIFFICULTY_NAMES`。
- **什么时候要跑**：改了 `advisor/vision/screens.py`，改了 `hud.py` 里 `read_pregame`/`is_select_screen`，改了 `advisor/vision/anchors.py` 里对局外画面的锚点，或改了 `samples/labels.json` 的 screen 标注、画面种类。全部样本的反例扫描在 `tests/test_vision_matrix.py`（慢测试）里。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_screens`（上次约 0.01 秒；是识别模块；它从 `test_vision_matrix` 借 `HAVE_SAMPLES`、`load`、`shrink`、`WORKERS`、`SCREEN_GREYED`）。识别在 `setUpClass` 里分给几个子进程一次读完。
- **需要什么**：`samples/` 和 `samples/labels.json`（标了 screen 的样本，以及 `NEGATIVES` 里列的反例样本）；缺了跳过。
- **输出怎么看**：跳过原因写明缺 `labels.json`；识别在子进程里跑，某项出错时 `self.fail` 会带上子进程的调用栈；`ParseTest` 不读样本、很快。不写盘。
- **出错时一般是哪里不对**：
  - `LabelsTest.test_screen_labels` 失败 → 标注文件本身写错（缺字段、取值不合法、和 scene 对不上、某种画面没有样本）；这是标注的问题，不是程序。
  - `SampleScreensTest.test_full_reading` 失败 → 整幅识别的解析改坏了（`screens.py` 的解析器或锚点阈值），或 `read_pregame` 把地图名和画面种类拼错。
  - `test_bottom_reading` 失败 → `is_select_screen` 的下方区域识别或结算页解析改坏。
  - `test_team_room_difficulty` 失败 → 组队房间勾选框的识别改坏（`_team_room`、`_checked`，以及 `PINK_*`、`GREY_*` 阈值）。
  - `test_negatives` 失败 → 反例画面被误认成对局外画面（解析太宽），或选角界面没判成选角界面。
  - `test_one_ocr_call` 失败 → `is_select_screen`／`read_pregame` 增加了文字识别次数。
  - `ParseTest.*` 失败 → 解析规则按构造结果核对不上；改了规则时把断言的期望一起改。

## test_stars.py：读玩家列表上自己的星数与队友角色，以及用户自存的角色立绘模板

- **测试观点**：
  - `OwnStarsTest`：在三选一画面上按头像读出自己那一行的星数（凛第 4 行 1 星）；同一张图缩到 1280 宽结果一致；自己那一行被三选一的标题带挡住时不猜、返回 `None`；战斗画面没有玩家列表也返回 `None`。
  - `OwnRowFallbackTest`：头像认不出自己那一行时（三选一压暗、头像上叠着「昏迷」字样）`read_own_stars` 按头像读不出、不按记住的行硬猜；「查看筹码」面板用的 `own_row_by_name` 按名字或「其余三行都是电脑、只有一行不是」找自己那一行；`name_key` 把文字识别连带读进来的首尾数字、空白、标点从名字里去掉。
  - `OwnStarsByNameTest`：`read_own_stars_by_name`——头像认不出时按本局记住的名字找那一行读星数。只有四行都看得见、只有一行读到的名字里含着整个记住的名字、其余各行读到的名字都看得出不是没读全的自己，才算；两行以上对得上、一行都对不上、名字没读全都不得结论；不用「其余都是电脑」那一条（比按名字找面板页签更严）。
  - `TeammatesTest`：`read_players` 认其余各行的角色和是否电脑、标出自己那一行；界面比按窗口估计的大 5%（最下面一行横条可能找不到）时不许凭空往上补一行、把行号整体错开；`_covered_by_band` 判断一行横条是不是被黑色标题带盖住。
  - `UserTemplateTest`：资料库里没有的立绘，手动指定角色后 `save_template` 存成用户模板（写在程序目录 `assets` 下的 `heroes/user`，运行时生成），之后其他画面（变暗、缩小）也认得出；别的角色不会误认成存过的角色；每个角色最多留 `USER_KEEP` 张；角色不存在时不存。
  - `ProfilePhotoNameTest`：Q 版头像的文件名认角色编号（`profile_photo_hero`）：编号后面只能跟 `_<皮肤>` 或 `.png`，编号前面补了 0 的、别的写法的数字不算；认某个角色的头像只读编号对得上的（105 读 105、105_01，不读 1050_01、1051、0105）。生成识别图片清单用的是同一个判断。
  - `TiltedPhotoTest`：斜着摆的皮肤（`data/photo_tilt.json`：角色照文件名 → 角度）。表读不出来时都不转、写错的项（不是数、超出 ±45°）不转；转的中心是脸部区域（`HERO_FACE`）的中心、正数逆时针；建模板时只转表里的角色照，别的角色照和用户模板取出的脸部逐字节不变；从卡面现裁的健全版（`UT_Hero_RolePhoto_103_01_sfw.png`，表里 17.1°）建模板时照样先转过来（`test_cropped_sfw_turned`）；从卡面现裁的派德曼「酷暑难耐」（`UT_Hero_RolePhoto_105_01.png`，表里 -42.3°）同样先转过来再取脸部、大小不变（`test_cropped_padman_turned`）；录像帧上的阿兰娜泳装（对局里约逆时针斜 17°）转了才认得出（相关度不低于 `MIN_SCORE`、比第二名高出 `MIN_MARGIN` 以上，配上的是泳装这一套的原版或健全版：脸部区域两版一样，现裁的健全版配得更准），不转时认不出。
- **检证对象**：`advisor/vision/stars.py`（`read_own_stars`、`read_players`、`diagnose`、`read_own_stars_by_name`、`own_row_by_name`、`name_key`、`_covered_by_band`、`reset_cache`、`profile_photo_hero`、`_hero_photos`、`photo_dirs`）、`advisor/vision/hero.py`（`read_hero`、`save_template`、`reload_templates`、`USER_KEEP`、`USER_PREFIX`、`tilts`、`turn_photo`、`TILT_FILE`、`TILT_MAX`）、`advisor/vision/templates.py` 的 `assets`（图片与沿用缓存）、`advisor/data.py` 的 `load_db`；`data/photo_tilt.json`。
- **什么时候要跑**：改了 `advisor/vision/stars.py`、`advisor/vision/hero.py`、`advisor/vision/anchors.py`（星数、头像、横条用到的几何量）、`advisor/vision/templates.py`；或换了 `data/heroes.json`、`data/photo_tilt.json`、`assets/stars/star_bar.png`、`assets/profiles/`、`assets/heroes/` 里的图片。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_stars`（上次约 0.5 秒；会真的建文字识别引擎，属于「用到文字识别的模块」）。
- **需要什么**：识别样本截图（`samples/` 下若干张，见下）；`tests/data/private.json` 里的 `own_name`（只有「按记住的名字找自己那一行」那一步用）；`TiltedPhotoTest.test_tilted_skin_recognised` 要 `tests/data/hero/` 里阿兰娜泳装的那张裁图（录像帧 3840×2160 里认角色看的左下角那一块）和下载好的角色照 `assets/heroes/UT_Hero_RolePhoto_103_01.png`，`test_cropped_sfw_turned` 要这张和现裁的健全版 `UT_Hero_RolePhoto_103_01_sfw.png`，`test_cropped_padman_turned` 要现裁的 `UT_Hero_RolePhoto_105_01.png`，缺了跳过。
- **输出怎么看**：
  - 缺截图时整类或用例跳过，原因形如「没有识别样本：缺 samples\20261002_015454_chipshop_prompt.png（准备方法见 docs/测试指南.md「准备识别样本」）」；用 `ACC_SAMPLES_DIR` 指到别处时按那里的文件判断。
  - 缺 `tests/data/private.json` 时，`OwnRowFallbackTest.own_name` 那一步跳过，原因「没有本机的测试数据文件 tests/data/private.json（不进版本库）：截图上玩家自己的名字只写在这个文件里，按名字找自己那一行的这一步跳过」；其余断言照常跑。`ACC_PRIVATE_DATA` 可指向另一份或不存在的文件。
  - `UserTemplateTest` 在测试沙箱里建临时文件夹，不碰真实的用户模板目录。
- **出错时一般是哪里不对**：
  - `OwnStarsTest.test_reads_own_row_in_chip_select` 失败 → 三选一画面上自己那行的头像比对或星数判断改坏了（去看 `stars.py` 的 `diagnose`、`_pick_self`、`_count_stars`、`SELF_MIN`、`SELF_MARGIN`）；把握阈值被调低也可能。
  - `test_blocked_row_gives_none` / `OwnRowFallbackTest.test_knocked_out_gives_none` 失败 → 「被挡住/认不出时返回 None、不硬猜」这条改了（`_find_layout` 里往上补行的 `_covered_by_band` 判断，或 `diagnose` 里被挡住的处理）。
  - `OwnStarsByNameTest` 整类失败 → `read_own_stars_by_name` 的取舍规矩改了（`_named_rows`、`_maybe_own_name`、四行都要看得见、不用「其余都是电脑」）；`test_needs_all_four_rows` 失败 → 少行时该不该按名字找改了。
  - `test_name_key` 失败 → `name_key` 去数字/标点的规则改了；这是有意改的话，把断言里的预期一起改。
  - `TeammatesTest.test_ui_scale_off_by_five_percent_keeps_rows` 失败 → `_find_layout` 往上补虚行的条件放宽了，会把各行行号错开。
  - `UserTemplateTest` 失败 → `hero.save_template` / `read_hero` / 模板缓存 `reload_templates` 的写法改了；模板张数上限（`USER_KEEP`）改了也要同步断言。
  - `TiltedPhotoTest` 失败 → 斜着摆的皮肤那一套改了：表的读法（`hero.tilts`）、转法（`hero.turn_photo`：中心、方向、补边）、建模板时转了别的角色照或用户模板，或 `data/photo_tilt.json` 里阿兰娜泳装（原版、健全版）、派德曼「酷暑难耐」那几项的角度改了；`test_cropped_sfw_turned`、`test_cropped_padman_turned` 失败也可能是现裁的裁法改了（`data/card_crops.json`，转的角度要和 `photo_tilt.json` 一致）。
  - `ProfilePhotoNameTest` 失败 → `profile_photo_hero` 认编号的写法改了；它也决定识别图片清单收哪些 Q 版头像（`tools/make_assets_manifest.py`），改了要跑 `test_build_tools`、重新生成清单。

## test_coins.py：核对玩家列表上自己那一行横条左边的星币数怎么读（`advisor/vision/coins.py`）、记账里星币数和下一次升星的花费怎么记（`Session.coins`、`next_star_cost`），以及识别流程里读星币数、头像认不出时按记住的名字找自己那一行这两步。

- **测试观点**（按测试类）：
  - `SampleTest`：样本上按角色（或按标注里记住的名字）读出自己那一行的星币数、列表上每一行的星币数；选转账对象的画面上不把自己当队友；小窗口（1280、1024 宽）读出的和原图一样；三位数（用两位数样本合成）不少读一位；数字被挡住一部分或第三位伸出裁剪框时宁可读不出、不能读成别的数；单独一个「0」的几种宽度；只识别数字那一小块、不做整幅文字检测。
  - `ByNameSampleTest`：头像认不出（「昏迷」等）时按本局记住的名字找自己那一行，行号、星数、把握和标注一致；每张样本按名字找到的行和标注一致，头像认得出的几张两条路一致；记住的名字和画面上谁都对不上时不读；列表只剩三行（转账、标题带挡住）时不按名字找。
  - `DigitLikeTest`：识别原文里像数字的字符（字母「O」、全角数字等）换成半角数字，其余字符不动；不用样本。
  - `ReadOwnCoinsInputTest`：输入不完整（没认出自己那一行、被挡住、把握不够、横条位置没有或出了画面）时返回原因、不出错。
  - `SessionCoinsTest`：星币数连续两次读到同一个数才采用、读不到时不清零也不打断「连续」、非法值忽略、新的一局清空、自动判定的新一局清空且撤销能恢复、本局已结束后读到的不采用、对局状态不存盘。
  - `NextStarCostTest`：升星要的星币（0→1 星 15、1→2 星 20、2→3 星 25）、星数待识别或已 3 星时为 None、新的一局后回到待识别、启动参数给的星数不算读到。
  - `PipelineCoinsTest`：识别流程里读星币数那一步（和读星数同帧、隔 coins_ms、三选一期间不读、读不到时把原因写进识别记录、读星币出错不影响这一帧其余识别、coins_ms 可改、本局已结束后不读、新的一局要重新连读两次）。
  - `PipelineByNameTest`：识别流程按名字找自己那一行（只在读星币那一拍、角色已认出且记住了名字才找；三选一期间不找；和本局认出的行对不上时不读；出错不影响其余识别；按名字找到的行不记成「本局认出的行」；识别记录里不写名字）。
  - `RealFrameFlowTest`：真实画面经识别流程走一遍（阿尔 2 星、连读两次 21 星币、下一次升星要 25；「昏迷」的画面按名字读到 3 星、13 星币）。
- **检证对象**：`advisor/vision/coins.py` 的 `read_own_coins`/`read_coins_at`/`_white_digits`/`_recognize`/`DIGIT_LIKE`/`MIN_ROW_CONFIDENCE`；`advisor/vision/stars.py` 的 `read_own_stars`/`read_own_stars_by_name`/`name_key`/`Row`；`advisor/session/core.py` 的 `Session.on_coins`/`next_star_cost`/`new_game`/`on_hud`/`on_select_screen`/`undo_new_game`/`on_out_of_game`/`note`/`recent_events`/`snapshot`/`note_own`（`STAR_COSTS`、`Event` 在 `advisor/session` 里导出）；`advisor/vision/pipeline.py` 的 `FrameProcessor._read_coins`/`_read_stars`/`_own_by_name`/`settle`（间隔 `hud_s`、`coins_s`）；辅助 `advisor/vision/pipeline.py` 的 `trim_borders`、`advisor/vision/hud.py` 的 `Hud`/`Progress`、`advisor/vision/screens.py` 的 `OutOfGame`。
- **什么时候要跑**：改了 `advisor/vision/coins.py`（挑数字、识别、核对、阈值）、`advisor/vision/stars.py` 里认自己那一行或按名字找（`read_own_stars`、`read_own_stars_by_name`、`name_key`）、`advisor/session/core.py` 里星币数和升星花费、`advisor/vision/pipeline.py` 里读星币数那一步或按名字找；改了 `[watch]` 的 `coins_ms` 也要跑 `PipelineCoinsTest`。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_coins`（上次约 0.5 秒；是识别模块）。
- **需要什么**：`tests/data/coins/` 里的星币数截图和同文件夹的 `labels.json`（截图从 `samples/` 原样复制，「昏迷」那张来自诊断包）。缺截图或标注时 `SampleTest`、`RealFrameFlowTest` 跳过；标注里没有记住的名字时 `ByNameSampleTest` 和 `RealFrameFlowTest` 里按名字的用例跳过。
- **输出怎么看**：跳过原因写明缺 `labels.json` 的哪个文件或哪张图；不打印对照表，靠断言和 `Session.recent_events` 里的识别记录（`read:coins`、`read:own_by_name`、`read:stars`）。自己的名字只写在 `labels.json` 的 `name` 字段里，测试代码里不写，识别记录里也不写（有专门断言守着）。不写临时文件。
- **出错时一般是哪里不对**：
  - `SampleTest.test_own_coins`／`test_every_row` 失败 → 挑数字、识别或核对改坏了（`_white_digits`、`read_coins_at` 里的阈值）。
  - `test_transfer_screen_never_reads_teammate` 失败 → 不知道自己是哪一行时仍去读了（`read_own_coins` 该返回 None 加原因），会把队友的星币数当成自己的。
  - `test_three_digits`／`test_lone_zero_common_widths` 失败 → 位数核对或像数字字符的替换改坏了（`read_coins_at` 的 `\d{1,3}` 核对、`DIGIT_LIKE`）。
  - `test_cut_or_covered_digits_not_misread`／`test_partly_covered_digit_not_misread` 失败 → 数字被挡住时的防线松了（`DIGIT_GAP`、`DIGIT_HEIGHT_SPREAD`、`LEFTOVER_AREA`/`LEFTOVER_HEIGHT`、`CHAR_WIDTH`），会读成别的数。
  - `test_digit_like_characters`／`DigitLikeTest` 失败 → `DIGIT_LIKE` 的替换表或 `_recognize` 的写法改坏。
  - `test_only_recognition_no_detection` 失败 → 星币数那一块改成了整幅或整块的文字检测（`coins.recognize_only` 的用法）。
  - `SessionCoinsTest.*` 失败 → 「连读两次才采用」「读不到不清零」「非法值忽略」「新一局清空」这类规则改坏了（`Session.on_coins`、`new_game`）。
  - `NextStarCostTest.*` 失败 → 升星花费表或「星数待识别」的判断改坏（`STAR_COSTS`、`Session.next_star_cost`）。这几处的数值是有意的，改了表就把断言的期望一起改。
  - `PipelineCoinsTest.*` 失败 → 间隔、三选一期间是否读、出错时的处理改坏了（`FrameProcessor._read_coins`、`coins_s`）。
  - `PipelineByNameTest.*` 失败 → 「按名字找」的触发条件或记录改坏了（`_own_by_name`、`stars.read_own_stars_by_name`）；注意「识别记录里不写名字」是有意的。
  - `RealFrameFlowTest.*` 失败 → 读星数、读星币、记账整条链在真实画面上对不上。
- **识别样本和环境变量**：样本位置由 `tests/support/samples.py` 定：`ACC_SAMPLES_DIR` 设了就用它、否则是仓库根目录的 `samples`；`ACC_COIN_SAMPLES_DIR` 设了就用它、否则是 `tests/data/coins`。缺哪个文件由 `samples_ok`/`coins_ok` 判断，跳过原因由 `samples_reason`/`coins_reason` 拼出（写明缺 `samples\某文件` 或 `tests\data\coins\某文件`，以及「准备方法见 docs/测试指南.md「准备识别样本」」）。本文件要的标注是 `tests/data/coins/labels.json`，用到的字段：`file`（截图名）、`hero`（自己的角色）、`own_row`（自己是第几行）、`own`（应读出的星币数，读不到为 null）、`why`（读不到的原因）、`how`（缺省按头像，`名字` 表示头像认不出、按名字找）、`name`（本局记住的自己的名字）、`name_row`（按名字找到的行）、`rows`（每一行的星币数，读不到为 null）。另外 `tests/private_data.py` 用 `ACC_PRIVATE_DATA`（设了就指到别处，否则是本机的 `tests/data/private.json`），它放的是和真截图比对用的自己的名字等；**本组这 8 个文件都不读它**，只提到它的用法。

## test_chip_panel.py：把「查看筹码」面板的识别（读出自己页签上有哪些筹码、判断选中的是不是自己、标出点选的格子）拿真实面板截图和筹码图标逐张核对，另用合成画面守住共用图案和认不出的情况。

- **测试观点**（按测试类）：
  - `FilesTest`：首次运行要下载的文件清单全不全（每个筹码的图标都在、三张稀有度底板都在、清单不重复），共用图案在不同稀有度上要靠底色区分。
  - `InputTest`：画面太空、太小、没有面板时不崩，判为「不是面板」。
  - `SampleTest`：真实面板截图上读出的筹码、页签个数、选中的页签、点选的格子、置信度、是否满页，和标注一致，两端的翻页箭头都不亮（只有一页；多于一页的见 test_panel_pages）；点选过的格子（外圈黄绿色）标为点选、画面被筹码三选一压暗时照读；判断选中的页签是不是自己（按玩家列表头像、按本局已确认的行、按名字找）；画面底部的像素小人不用来判断自己；反例画面（战斗、三选一、对局画面、任务栏）都不是面板；同一尺寸连续读沿用上次的位置；没有图标文件时仍认出面板但筹码算无法识别。
  - `SyntheticTest`：把图标按稀有度底色画进空面板，检验同系列图案（拳击手套、医疗箱、美工刀、飞星、瞄具）、时间怀表触发后的破碎图标、「大铜锣」列为 doubtful、图案不在库里或图案和底色对不上时算无法识别、合成的黄绿色外圈判为点选、只有一两个筹码时空格子不拉低置信度。
  - `PanelOrderTest`：真实面板上有格子读不出（外圈被挡住）时，凭原来的记录把筹码放回那一格的位置，已有筹码的顺序和面板一致。
- **检证对象**：`advisor/vision/chip_panel.py` 的 `read_chip_panel`（以及 `icon_files`/`icon_file`、`_locate`、`_read_slot`、`_read_tabs`、`_list_owner`、`_name_owner`、`PanelReading`/`SlotReading`）；辅助 `advisor/session/panel.py` 的 `panel_order`，经 `advisor/session/core.py` 的 `Session.on_panel`/`set_owned`；合成画面用 `tools/panel_synth.py` 的 `swap_selected_tab`、`draw_chips`、`rarity_bgr`（识别评测工具 `tools/eval_panel.py` 合成面板也用它们）。
- **什么时候要跑**：改了 `advisor/vision/chip_panel.py`（找格子、认筹码、页签、点选判断、图标文件名），改了 `advisor/vision/anchors.py` 里面板几何，改了 `advisor/session/panel.py` 或 `Session.on_panel`/`set_owned`，改了 `tools/panel_synth.py`（合成画面的函数），或改了资料库里筹码的图标 ID、稀有度（`data/chips.json`）。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_chip_panel`（上次约 0.02 秒；要建文字识别引擎，是同时最多并行几个的识别模块；不是慢测试、也不是实机测试）。没有识别样本和筹码图标时几乎全部跳过，所以很快。
- **需要什么**：`samples/` 里的面板截图、`samples/panel_labels.json` 标注、`assets/chips/` 里的筹码图标（`tools/fetch_chip_icons.py` 下载）。缺截图或标注的类、缺图标的用例会跳过。
- **输出怎么看**：跳过时写明缺哪个文件、准备方法（缺样本的说明形如「没有识别样本：缺 samples\某文件（准备方法见 docs/测试指南.md「准备识别样本」）」，缺图标写「没有下载筹码图标（tools/fetch_chip_icons.py 下载）」）。它不打印对照表，靠断言；`PanelOrderTest` 用 `Session.recent_events` 看事件种类 `panel_read`。不写临时文件（`read_chip_panel` 只读盘）。
- **出错时一般是哪里不对**：
  - `FilesTest.test_icon_files_cover_every_chip` 失败 → 资料库新增了筹码、图标 ID 没加进 `icon_files`，或 `ICON_ALIASES` 没跟上（看 `chip_panel.icon_files`、`ICON_ALIASES`）。
  - `FilesTest.test_shared_art_differs_by_rarity` 失败 → 资料库里出现两个图标、稀有度都相同的筹码（面板靠底色分不开，看 `data/chips.json`）。
  - `SampleTest.test_reads_owned_chips` 失败 → 找格子、认筹码或读页签读错了（看 `_locate`、`_read_slot`、`_read_tabs`），或面板几何（`advisor/vision/anchors.py`）被改坏。
  - `test_selected_slot`／`test_selected_flag_not_cached`／`SyntheticTest.test_selected_rim_synthetic` 失败 → 点选外圈（黄绿色）的色相、饱和度阈值改坏了（`SELECTED_HUE`、`SELECTED_MIN_SAT`、`_selected_rim`），或点选标记被沿用缓存带走（`_read_slot` 的 `_slot_cache` 逻辑）。这几处的数值是有意调的，改了阈值就跟着改断言里的期望（`SELECTED_RIM_HSV` 等）。
  - `test_self_tab_from_player_list`／`test_avatar_wins_over_remembered_row`／`test_knocked_out_self_tab`／`test_bottom_sprite_not_used` 失败 → 判断选中的页签是不是自己（`_list_owner`、`_name_owner`、`read_chip_panel` 里几种依据的先后）改坏了。
  - `SyntheticTest.test_unknown_art_and_wrong_rarity` 失败 → 「认不出就不猜」的分支被改坏（`_read_slot` 里 `ICON_MIN`/`ICON_MARGIN`、图案和稀有度对不上时的说明）。
  - `PanelOrderTest.test_recorded_chip_keeps_its_slot` 失败 → 面板读数合进已有筹码时的保留和放回（`panel_order`、`panel.read_panel`）改坏了。
  - `InputTest.test_blank_frame_is_not_panel` 失败 → 不是面板时的返回（`is_panel=False`、`owned=[]`）改坏了。

## test_select_hidden.py：三选一界面上点了「隐藏」——认出那行字时当三选一还开着

- **测试观点**（按测试类）：
  - `HiddenTextTest`：实机录像里的四帧（缩到 960×540）：隐藏着的两张（一张左下角被轮盘挡了一部分）认出「您当前正处于隐藏UI状态」，隐藏之前、点显示以后的三选一画面上没有；上下加黑边成 4:3（三选一界面居中、贴着下边）、左右加黑边成 21:9 时一样；隐藏着的画面过不了三选一的预筛。
  - `HiddenTextLogicTest`：文字识别换成假的：那一条里白色的字不够多（空画面）时不调文字识别，画面太小不看；读漏一两个字、中间多读出空格的算，把握低、是别的字的不算。
  - `HiddenLedgerTest`：记账：隐藏后点显示（不撤销、不重发推荐，确定后只记一次；改选了的照常认）；隐藏着时间到了（照常按绿框记，打开面板后按面板改正）；那一帧没认出字（先记后撤，结果仍然对）；隐藏两次；三选一没显示时不起作用；识别记录在开始隐藏时记一条。
  - `HiddenFlowTest`：识别流程：只在三选一正要按关掉处理的那一帧看那行字（平时、显示着、消失后的第 1 帧都不看），隐藏着时每帧看一次，交给 `Session.on_select_hidden`、标出 `select_hidden`；点显示后不撤销、不重发推荐；字没了照常记下绿框；没认出时先记后撤；看那行字出错时这一项报错、照常按关掉处理。按热键要求的那一帧也看一眼：刚隐藏的第 1 帧照样当三选一还开着；手动模式下没按过热键就隐藏了的那一次（Session 不知道）认出后每帧接着看、点显示后照常给推荐、确定后照常记下，那行字没了、没有那行字、看那行字出错时不再接着看。
  - `HiddenFollowTest`：悬浮窗的识别线程：手动模式跟随三选一时，隐藏着也接着看（按平时的间隔），隐藏结束后照常再看 `FOLLOW_FRAMES` 帧就停；标记不是真的 True 不算。按热键的那一帧是隐藏着的时候不报「当前画面不是筹码三选一界面」，照常告诉界面这一帧识别完了，接着看到三选一回来。
- **检证对象**：`advisor/vision/chip_select.py` 的 `hidden_ui_visible`（`anchors.HIDDEN_UI*`）；`advisor/session/selection.py` 的 `SelectTracker.closing`、`hidden`，`Session.on_select_hidden`、`WatchHints.select_closing`；`advisor/vision/pipeline.py` 的 `FrameProcessor._select_hidden`、`process`、`select_hidden`；`advisor/ui/overlay_parts/watcher.py` 的跟随和按热键那一帧的提示。
- **什么时候要跑**：改了上面这几处，或三选一关掉的判定（`GONE_FRAMES`、`SelectTracker.frame`/`close`）、识别线程的跟随以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_select_hidden`（约 1 秒；`HiddenTextTest` 用真的文字识别，是同时最多并行几个的识别模块）。
- **需要什么**：`HiddenTextTest` 要 `samples/pages/` 里的四张录像帧（见测试指南第五节），缺了跳过；其余不要样本。会初始化界面环境（离屏运行；识别线程用假的取帧对象和识别流程）。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `HiddenTextTest`、`HiddenTextLogicTest` 失败 → 那行字的位置（`anchors.HIDDEN_UI_*`）、白色预筛（`chip_select.HIDDEN_WHITE*`）、把握门槛（`HIDDEN_REC_MIN`）或正则（`anchors.HIDDEN_UI`）改了。
  - `HiddenLedgerTest` 失败 → `SelectTracker.hidden` 没把看不到的帧数压在关掉之前，或 `frame`、`close` 没清 `_hidden`。
  - `HiddenFlowTest` 失败 → `FrameProcessor.process` 里看那行字的时机（`WatchHints.select_closing`）或交给 Session 的分支改了。
  - `HiddenFollowTest` 失败 → `Watcher._loop` 里跟随三选一的那几行改了。

## test_prefilter.py：核对筹码三选一预筛的两段式写法——和「把整幅缩到 96x54」的整幅缩放写法逐张判定相同。

- **测试观点**：`test_matches_full_resize`（每张样本 × 几种宽度，两段式和整幅缩放的写法判定相同）；`test_near_threshold_recomputed`（NEAR 这张在 1920 宽时取样占比落在阈值附近的余量里，两段式必须按整幅缩放的写法再算一次，判定和它一样是「不像」——这条同时守住「离阈值近一定要按整幅缩放的写法算」）；`test_select_screen_like`（三选一样本判成「像」）；`test_matches_full_resize_on_plain_shapes`（全黑、全白、噪声、下方黑带这些合成画面上也相同）。
- **检证对象**：`advisor/vision/chip_select.py` 的 `looks_like_overlay_screen`、`PRE_SAMPLE_MARGIN`；辅助 `advisor/vision/pipeline.py` 的 `trim_borders`。
- **什么时候要跑**：改了 `looks_like_overlay_screen` 的判定、取样步长或暗行阈值，改了 `PRE_SAMPLE_MARGIN`，或改了去黑边（`trim_borders`）。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_prefilter`（上次约 0.5 秒；不做文字识别）。
- **需要什么**：`samples/` 里 4 张截图（NEAR 是 `20261003_132306_card_give_al_skill_2560x1369.jpg`、SELECT 是三选一、BATTLE 是对局画面、PANEL 是面板）；缺了跳过。
- **输出怎么看**：跳过时写明缺哪张；不写盘。其余同总则。
- **出错时一般是哪里不对**：
  - `test_matches_full_resize` 失败 → 两段式和整幅缩放的写法对不上了（取样或余量判断改坏）。
  - `test_near_threshold_recomputed` 失败 → `PRE_SAMPLE_MARGIN` 改小到不覆盖这张样本，或没按整幅缩放的写法重算；这条断言本身也说明该样本仍落在余量里，改了样本就一并改。
  - `test_select_screen_like` 失败 → 暗行范围或阈值改坏，把三选一漏掉。
  - `test_matches_full_resize_on_plain_shapes` 失败 → 极端画面（全黑、全白、噪声）上的判定改了。

## test_cv_threads.py：检查「识别只用一个 OpenCV 线程」这件事——`pipeline.limit_cv_threads` 设得到、设不了也不抛、悬浮窗的后台识别线程开头设一次，而且只用 1 个线程和用多个线程时的识别结果逐项相同。

- **测试观点**：`LimitCvThreadsTest`（把 OpenCV 线程数设成 `CV_THREADS`=1；设不了时只记着、不抛异常）；`WatcherLimitsThreadsTest`（悬浮窗后台识别线程一开始就在识别线程里设一次，不是界面线程）；`OneThreadSameResultsTest`（原尺寸样本上，1 个线程和多个线程的预筛、三选一识别、筹码名那一条指纹、画面指纹和缩略图、认角色、读星数逐项相同）。
- **检证对象**：`advisor/vision/pipeline.py` 的 `limit_cv_threads`、`CV_THREADS`（以及被用来比结果的 `_fingerprint`、`_thumb`、`name_strip` 等）；`advisor/ui/overlay_parts/watcher.py` 的 `Watcher.run`（调用 `limit_cv_threads` 的位置）。
- **什么时候要跑**：改了 `pipeline.limit_cv_threads`/`CV_THREADS`，或改了识别线程的启动流程；升级 OpenCV 后也要跑（某个函数按线程数改了算法时最后一条会报出来）。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_cv_threads`（上次约 0.2 秒；是识别模块）。
- **需要什么**：`samples/` 里的 `20261002_013617_chipselect_selected_state.png` 和 `20261002_012918_hud_round1.png`（`OneThreadSameResultsTest` 用；缺了跳过）。`WatcherLimitsThreadsTest` 用离屏 Qt（`tests/support/qtenv.py`）。
- **输出怎么看**：跳过时写明缺的样本；每条测试结束把 OpenCV 线程数恢复成原来的，不影响同一进程里别处的测试；不写盘。其余同总则。
- **出错时一般是哪里不对**：
  - `LimitCvThreadsTest.test_sets_threads` 失败 → `CV_THREADS` 或 `limit_cv_threads` 被改坏。
  - `test_error_does_not_stop_recognition` 失败 → 设线程数失败时没接住异常（`limit_cv_threads` 里的 try/except）。
  - `WatcherLimitsThreadsTest.test_called_once_in_recognition_thread` 失败 → 识别线程没设、设了不止一次，或在界面线程里设（`Watcher.run`）。
  - `OneThreadSameResultsTest.test_same_results` 失败 → 某个识别函数在 1 线程和多线程下结果不同（多半是升级 OpenCV 或某处改了算法）；哪一项不同看子测试名。

## test_rapidocr_lite.py：核对精简版文字识别库 `rapidocr_lite` 和原版 RapidOCR 在同一批样本上结果完全相同、会话选项相同，纯 Python 的外扩框几何和原版用的 Shapely 逐位相同；另测「只识别不检测」、非法参数报错、找模型的几种情况。

- **测试观点**（按测试类）：`TestSameAsOriginal`（整图识别结果、只识别不检测的结果、检测/方向分类/识别三个会话的选项都和原版逐项相同）；`TestLiteEngine`（只识别后 `update_params` 能还原、按字分割报错且不留副作用、模型目录不存在时报 FileNotFoundError）；`TestLiteConfig`（「段.子段.键」覆盖、非法键报错、枚举字段要传枚举、构造时开按字分割报错、`allow_spinning` 生成的两条会话选项、找不到模型时的报错、改过的文件保留版权头、找模型时不导入原版）；`TestUnclipGeometry`（外扩框面积、周长的期望值，退化多边形，和 Shapely 逐位比，和原版 `unclip` 比）。
- **检证对象**：`rapidocr_lite/main.py`（`RapidOCR`）、`rapidocr_lite/config.py`（`build_config`、`installed_models_dir`、`DET_MODEL_FILE`/`CLS_MODEL_FILE`/`REC_MODEL_FILE`）、`rapidocr_lite/inference_engine/onnxruntime/main.py`（`OrtInferSession._init_sess_opts`）、`rapidocr_lite/ch_ppocr_det/utils.py`（`_polygon_area`、`_polygon_length`、`DBPostProcess.unclip`）；对照的原版是装好的 `rapidocr` 包。
- **什么时候要跑**：改了 `rapidocr_lite/` 下的任何文件、升级原版 RapidOCR 或 onnxruntime、或改了 `advisor/vision/chip_select.py` 里建引擎的参数（线程数、`allow_spinning`）。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_rapidocr_lite`（上次约 0.5 秒；是识别模块）。
- **需要什么**：装好的原版 `rapidocr`（只用于比对那几条，没有就跳过）、Shapely（逐位比那两条用，没有就跳过）、识别模型（`rapidocr` 包里的 models，缺了跳过要建引擎的几条）、3 张小样本（`20261002_211623_skin_select_matchmaking_1600x855.jpg`、`20261002_211713_hud_round1_emoji_panel_1600x855.jpg`、`20261002_213305_chips_panel_two_chips_1600x855.jpg`）。不用显卡。
- **输出怎么看**：跳过原因写明（「没装原版 rapidocr 或找不到模型；…」或「找不到识别模型，跳过要建引擎的几条；…」，后接缺样本的说明）。`test_notices_kept` 检查每个文件开头两行的出处和改动注释、以及原版对应的版权头还在；`test_finding_models_does_not_import_original` 起一个子进程，检查 `installed_models_dir()` 不会连带导入 `rapidocr`/`omegaconf`/`yaml`/`shapely`/`PIL`/`requests`。不写盘。
- **出错时一般是哪里不对**：
  - `TestSameAsOriginal.test_full_image_identical`／`test_rec_only_identical` 失败 → 精简版和原版的识别结果不同了（升级了原版，或改了精简版的前后处理）。
  - `test_session_options_identical` 失败 → 建会话的选项和原版不同（`allow_spinning`、线程数、执行 provider）。
  - `TestLiteEngine.test_word_box_rejected_without_side_effects` 失败 → 报错前先改了开关（`main.py` 里 `return_word_box` 的检查顺序）。
  - `TestLiteConfig.test_bad_key_raises`／`test_dotted_params_apply` 失败 → 键名校验或点号覆盖改坏（`config.py`）。
  - `TestUnclipGeometry.test_bitwise_same_as_shapely`／`test_unclip_same_as_original` 失败 → 面积、周长算法改了（`ch_ppocr_det/utils.py` 的 `_polygon_area`/`_polygon_length`），或退化多边形的处理不同。`EDGE_BOXES`、`DEGENERATE` 里的期望值是有意记下来的（对照 Shapely 2.1.2 算的），改了算法就一并改。
  - `test_notices_kept` 失败 → 新加或改过的文件没写「本文件来自 RapidOCR 3.9.2」和改动说明，或删了原版版权头。
  - `test_missing_model_dir_raises`／`test_no_installed_models_raises_clearly` 失败 → 找不到模型时的报错改了。

## test_ocr_lite_switch.py：程序用自带精简版 rapidocr_lite 的守卫

- **测试观点**：
  - `ModelsDirTest`：开发时模型取自装好的原版 rapidocr 包里的 `models`；发行版里是程序目录下的 `models`，缺文件时抛 `FileNotFoundError` 并在说明里写明缺哪几个、在哪个目录；发行版里模型目录整个不存在时也只报缺模型。
  - `EngineTest`：`_engine` 用自带精简版建引擎，全程不导入原版 `rapidocr`（也不连带 `omegaconf`、`PyYAML`、`shapely`、`PIL` 等）；空转由精简版配置里的 `allow_spinning` 关掉，没有给原版包一层的 `_no_spinning`。
  - `DiagnosticsTest`：自检 `advisor/diagnostics.py` 不导入原版 `rapidocr`，走同一个 `_engine`。
  - `BuildModelsTest`：打包必备清单里是 `app\models` 下三个模型（不是原版包里的路径）；`INCLUDE_PACKAGES` 带上精简版、不带原版和它带进来的依赖；多进程相关的几项也不跟进；`copy_models` 把三个模型拷进 `app\models`、`model_problems` 核对 SHA-256；开发环境取不到模型时组装在编启动程序、动发行版文件夹之前停下，上一次组装的文件夹原样不动。
  - `DevModelsHintTest`：开发环境取不到模型时，报错写明怎么补（含 `--no-deps`、`requirements.txt`、`--no-deps --force-reinstall`）；发行版的报错不写开发用的装库命令，结尾是「请重新解压下载的压缩包。」。
  - `ProgramEngineTest`：程序实际用的引擎（`chip_select._engine`）和照同样设置用原版 RapidOCR 建的引擎比，三个会话实际生效的选项、用的模型、推理参数都逐项相同；发行版里没有原版包时 `_engine` 照样建得起来，用的是 `ocr_models_dir()` 给的目录。
  - `LicenseFilesTest`：`LICENSES/` 里的文件和 `advisor/version.py` 的 `THIRD_PARTY` 表登记的正好一样；发行版不带的组件（Pillow、Shapely、GEOS、tqdm、certifi 等）在「关于」窗口的第三方列表、`THIRD_PARTY_NOTICES.md` 和 `LICENSES/MSVC-runtime.txt` 里都不提；写明附带组件的几项（Python、OpenCV、NumPy）的组件名单和通知文件里同一行逐字相同；打包脚本写死的那几份说明（`RAPIDOCR_NOTE`、`PADDLE_NOTE`、`MSVC_NOTE`）和 `LICENSES/` 里的一字不差。
  - `LicenseTrimTest`：打包脚本把许可证全文按「发行版里真带着的组件」裁剪——Python 的 `LICENSE.txt`（`python_license_txt`）、自带文档的「Incorporated Software」（`python_incorporated`）、numpy 的组件许可证（`numpy_component_licenses`）、OpenCV 的第三方（`opencv_third_party`）各按名单取段落，原文和名单对不上、或末尾多出没登记的段落时抛 `RuntimeError` 停下（不悄悄漏收新组件）；最后一条核对 `LICENSES/` 里重新生成的 Python.txt、NumPy.txt、OpenCV.txt。
- **检证对象**：`advisor/paths.py`（`ocr_models_dir`、`MODELS_SUBDIR`、`is_frozen`、`app_dir`、`importlib.util.find_spec`）、`advisor/vision/chip_select.py`（`_engine`、`_TimedEngine`、`recognize_only`）、`advisor/diagnostics.py`、`tools/build_release.py`（`REQUIRED_ENGINE_FILES`、`OCR_MODEL_FILES`、`INCLUDE_PACKAGES`、`NOFOLLOW_OCR_DEPS`、`NOFOLLOW_CONCURRENCY`、`NOFOLLOW`、`copy_models`、`model_problems`、`assemble`、`compile_launcher`、`python_license_txt`、`python_incorporated`、`numpy_component_licenses`、`opencv_third_party`、`RAPIDOCR_NOTE`、`PADDLE_NOTE`、`MSVC_NOTE`、`_join`）、`advisor/version.py`（`THIRD_PARTY`），以及 `LICENSES/`、`THIRD_PARTY_NOTICES.md`。
- **什么时候要跑**：改了识别引擎的构建（`chip_select._engine`）、模型目录 `paths.ocr_models_dir`、`advisor/diagnostics.py`、`tools/build_release.py` 的打包清单/许可证收集、`advisor/version.py` 的第三方表、`LICENSES/` 里的文件或 `THIRD_PARTY_NOTICES.md`。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_ocr_lite_switch`（上次没有记录；属于「用到文字识别的模块」，会真的建引擎）。
- **需要什么**：开发环境里装好的原版 `rapidocr` 包（模型的来源）和里面的三个模型文件。没装原版时，`EngineTest.test_engine_uses_lite_without_importing_original`、`ProgramEngineTest`、以及 `ModelsDirTest`/`BuildModelsTest` 里需要模型的几条跳过，原因「没装原版 rapidocr 或找不到模型」；只查 source/清单/许可证的用例（`test_frozen_uses_program_dir_and_reports_missing`、`test_bad_frozen_dir_still_reports`、`test_no_spinning_helper_removed`、`test_no_original_import`、`test_required_lists_models`、`test_include_and_nofollow`、开发提示、许可证各条）照常跑。
- **输出怎么看**：跳过时的原因同上（两条文案）；`EngineTest` 会起一个子进程跑 `_engine`，子进程打印导入到的模块清单和 `True`，断言里正是比对这一行。
- **出错时一般是哪里不对**：
  - `test_engine_uses_lite_without_importing_original` 失败 → `_engine` 又导入了原版 `rapidocr` 或它带进来的依赖。
  - `test_no_spinning_helper_removed` 失败 → 又加回了 `_no_spinning`，或少了 `allow_spinning`/`Global.model_root_dir`。
  - `test_no_original_import` 失败 → `advisor/diagnostics.py` 又 `from rapidocr import …`。
  - `test_required_lists_models` / `test_include_and_nofollow` 失败 → 打包清单、`INCLUDE_PACKAGES` 或 `NOFOLLOW_*` 改了。
  - `test_copy_and_verify` 失败 → `copy_models`/`model_problems`（SHA-256 核对）改了。
  - `test_assemble_stops_before_touching_release_without_models` 失败 → `assemble` 取不到模型时不再在动发行版文件夹之前停下。
  - `DevModelsHintTest` 失败 → `paths.ocr_models_dir` 的缺模型报错文案改了（`--no-deps`、`--force-reinstall`、发行版结尾句）——这是有意改的话要同步断言。
  - `LicenseFilesTest` 失败 → `THIRD_PARTY` 表或 `LICENSES/`、通知文件、打包脚本里写死的说明三处对不上（改了一处忘了另一处）。
  - `LicenseTrimTest` 失败 → 打包脚本的许可证裁剪（或列表常量 `PYTHON_INCORPORATED_SECTIONS`、`PYTHON_SECTIONS_DROPPED`、`NUMPY_COMPONENT_LICENSES`、`OPENCV_THIRD_PARTY_LIBS`）改了，或 `LICENSES/` 里生成的全文和名单不一致——升级依赖库后应先重跑打包脚本的 `--licenses`。

## test_perfstats.py：识别各步耗时和处理器占用的统计

- **测试观点**：
  - `PerfStatsTest`：分位数（最近秩法，`p50` 取第 `ceil(0.5n)` 个、`p95` 取第 `ceil(0.95n)` 个）、最大值、总和；带冒号的 `kind` 归到 `reads` 下（名字取冒号后面一段）；没有记录的 `kind` 不出现；`take_period` 取走后清零；`period_s` 是两段之间的时长；样本超过上限 `MAX_SAMPLES` 后只累加次数/总和/最大值、分位数按存下的那些算；处理器占用——第一次 `take_period` 只记基准没有 `cpu`，之后按两次快照的差算（本进程与两个线程折成一个核的百分比、整机 0~100）；某一项 Win32 调用失败时那一项不出现、不是 Windows 时 `cpu` 整个不出现；取时钟、记一笔、取统计全都出错时不往外抛；出错只丢那一段（记一笔出错只丢那一笔，汇总出错只丢这一段、下一段照常）；几个线程同时记不丢不抛。
  - `TimedEngineTest`：`chip_select._TimedEngine` 给识别引擎套一层计时，每次调用记一笔 `ocr`；属性读写、`update_params` 转给里面的引擎；识别结果和不套外壳时相同；`recognize_only` 用完照旧还原 `use_det`/`use_cls`/`use_rec`。
  - `UnbiasedClockTest`：`perfstats.unbiased_clock` 在 Windows 上按 `QueryUnbiasedInterruptTime`（100 纳秒为单位）折成秒；没有这个函数或取不到时退回 `time.monotonic`；真实时钟在 Windows 上确实在走、走得和单调时钟一样快。
- **检证对象**：`advisor/perfstats.py`（`record`、`take_period`、`unbiased_clock`、`_nearest_rank`、`_percent`、`MAX_SAMPLES`、模块级的 `_clock`/`_kernel32`/`_counts`/`_since`/`_cpu_base`）、`advisor/vision/chip_select.py`（`_TimedEngine`、`recognize_only`、`_engine`）。
- **什么时候要跑**：改了 `advisor/perfstats.py` 的分位数、分组、清零、样本上限、处理器占用计算或时钟；改了 `chip_select._TimedEngine`（计时外壳）或 `recognize_only` 的开关还原。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_perfstats`（上次约 0.1 秒；不真的加载识别模型，引擎换成假的）。
- **需要什么**：没有特别要求（时钟和 Win32 调用都换成假的）；`UnbiasedClockTest.test_real_clock_runs` 只在 Windows 上跑。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `test_quantiles_max_sum` / `test_odd_quantile` / `test_sample_cap` 失败 → 分位数算法（`_nearest_rank`）或样本上限 `MAX_SAMPLES` 处理改了。
  - `test_reads_grouped` / `test_missing_kinds_absent` 失败 → `take_period` 里对带冒号的 `kind` 分组或空 `kind` 的处理改了。
  - `test_take_clears` / `test_period_seconds` 失败 → 取走后清零或 `period_s` 的计算改了。
  - `test_cpu_after_first_period` / `test_cpu_missing_on_failure` 失败 → 处理器占用的折算法（`_percent`）或第一次只记基准、失败项缺省的逻辑改了。
  - `test_never_raises` / `test_recovers_after_failure` 失败 → `record`/`take_period` 的「绝不外抛、出错只丢一段」防护被去掉。
  - `test_threads_record_concurrently` 失败 → `record` 里的锁或计数改了（会丢样本）。
  - `TimedEngineTest` 失败 → `_TimedEngine` 的转发或 `recognize_only` 的开关还原改了（识别结果会跟着变）。
  - `UnbiasedClockTest` 失败 → `unbiased_clock` 的换算或退回条件改了。

## test_out_of_game_static.py：离开对局后的整幅识别，和「连续两次才结束本局」

- **测试观点**
  - `test_still_screen_ends_game`：只有整幅识别才认得出的画面停着不动时，第一次认出以后隔一段再认一次，第二次读到就结束本局，前后不超过上限；结束以后同样画面不再重复整幅识别
  - `test_result_screen_read_once`：结算画面读到一次就结束本局，静止画面照旧只认一次
  - `test_unrecognized_still_screen_read_once`：认不出对局外画面的静止画面只认一次，不因为这一项多做整幅识别
  - `test_no_game_state_read_once`：没有对局状态时认出房间，不用确认，静止画面照旧只认一次
- **检证对象**：`advisor/vision/pipeline.py` 的 `FrameProcessor._check_loading`、`settle`、`after_game_s`；`advisor/session/offgame.py` 的 `OUT_CONFIRM_GAP_S`／`OUT_CONFIRM_S` 与结束本局的判断；`advisor/vision/screens.py` 的 `OutOfGame`、`CREATE_ROOM`／`CUSTOM_ROOM`／`MATCH_FOUND`／`RESULT`／`REWARD`／`TEAM_ROOM`；`advisor/vision/hud.py` 的 `Pregame`；`rules.toml` 的 `[map_detect] loading_ms`／`mutators_ms`
- **什么时候要跑**：改了离开对局后的整幅识别、结束本局的确认间隔、`[map_detect]` 的间隔，或改了「哪几种对局外画面只靠整幅识别认得出」，都要跑
- **怎么单独跑**：`python -B tools/run_tests.py --only test_out_of_game_static`（上次约 3.5 秒）
- **需要什么**：识别函数全部换成假的，不截屏、不找真实窗口；离屏
- **输出怎么看**：没有特别的，同总则
- **出错时一般是哪里不对**
  - `test_still_screen_ends_game` 失败 → 整幅识别的间隔、两次之间的时间、结束本局的时刻或「结束以后不再认」改坏（`advisor/vision/pipeline.py` 的 `_check_loading`、`advisor/session/offgame.py`）
  - `test_result_screen_read_once` 失败 → 结算画面该一次就结束却又要确认（`advisor/session/offgame.py`）
  - `test_unrecognized_still_screen_read_once` 失败 → 认不出时还反复做整幅识别（`advisor/vision/pipeline.py`）
  - `test_no_game_state_read_once` 失败 → 没有对局状态时也去确认（`advisor/session/offgame.py`）
  - 有意改了 `[map_detect]` 的间隔或确认上限时，这里的读数会跟着变，按新的值核定即可
