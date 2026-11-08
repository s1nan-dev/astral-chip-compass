# 测试指南 · 识别

这是[《测试指南》](../测试指南.md)第八节的一章：每个测试文件一节，每节的结构见那里第八节的开头。


这一章管从截图里读游戏画面的那套识别（advisor/vision）和识别流程本身：轮次、地图、候选筹码、星数、星币、面板、对局外画面，以及什么时候重读。

## test_pipeline.py：识别流程本身怎么走——什么时候重新识别、三选一出来多快给推荐

- **测试观点**：
  - `QuickRecheckTest`：同一个筹码三选一界面上只查绿框、不重复做整页文字识别；只有筹码名变了（重新刷新）、窗口大小变了才重新识别（完整识别，或记下位置以后快读读成，都算认了一次）；读得不完整（概率行读不到、图标没加载）时隔约半秒再读一次；推荐给出以后不再按时间定时整页重认。快速盯绿框只截的那一块（识别流程放上的要盯的那一份）在真截图上查出的选中项和整幅上的相同（`test_band_same_as_full`，其余见 test_pick_watch）。
  - `ProgressInSelectTest`：三选一界面出现期间也读进度条；读到与已采用的不同的格时下一帧马上补读一次，两次一致才采用；每次三选一里只补读一次；离开三选一就清掉补读；一条用真实的进度条识别核对（任务奖励「进度 -1」使进度条当前格由第 11 格退回第 10 格）。
  - `UnchangedScreenTest`：战斗等画面在认定「不是三选一」「不是选角界面」、又读不到轮次之后，画面没变就不再重复识别；用画面指纹判断变没变；淡入到一半的那一帧没认出来时不能记成「不是」；按热键强制重新识别；画面静止时每轮的读轮次、认选角界面也沿用结论，地图变成未知时连任务栏一起再读一次。真的战斗画面（上方有黑带、标题带不在，第二段拦下）不做文字识别也算认过「不是三选一」：一直在动时三选一的文字识别一次也不做（`test_real_battle_never_read`），静止时照样记下、沿用，读不到东西的轮次也不再读，只隔 `unchanged_max_ms` 再读一次（`test_real_battle_remembered_while_still`、`test_round_and_select_screen_not_reread_while_unchanged`、`test_real_battle_round_reread_after_max_time`）；记下以后三选一来了马上认（`test_chip_select_after_real_battle_read_at_once`），标题带回来时不沿用标题带不在时记下的那一条（`test_title_band_back_not_taken_as_unchanged`）。第二段的两种例外：按热键的那一帧上方有黑带就照样认，认出来以后接得下去（`test_hotkey_reads_dark_top_without_band`、`test_hotkey_found_select_kept_when_band_not_where_expected`）；三选一已经认出来时标题带被盖住几帧不算关掉，不结算、不重新给推荐（`test_title_band_covered_*`），真的关了只多完整识别一次（`test_select_closed_into_dark_top_read_once_more`、`test_select_closed_into_light_screen_not_read`）。预筛没过时分情况不重算第一段，同一帧只算一次（`test_dark_band_once_per_frame`）。
  - `BordersAndTaskPanelTest`：去掉画面四周的黑边（先只看靠边一圈的快路，结果必须和整套算法完全相同）；带上上一帧的范围（`keep`）时，黑边里新多出的零星亮点（16:10 屏幕上游戏画在黑边里的小图标）照旧去同样的黑边，不带 `keep` 时和原来一样少去，亮成一片（超过 `BORDER_SPECK`）、`keep` 是整幅或黑边比 `keep` 厚时照新算的；亮像素的占比在 `BORDER_SPECK` 两边时一边照 `keep`、一边照新算的（`test_keep_speck_threshold`）；各边分别照 `keep` 去完剩不到 `BORDER_KEEP` 时照这一帧自己算的，不会去成空图（`test_keep_never_leaves_less_than_half`）；亮点碰到最外一圈（`_edges_lit` 那条捷径）也躲得过；`FrameProcessor` 连着两帧（先干净、再带亮块）偏移不变，换了画面大小就不带 `keep`（`test_keep_*`、`test_processor_keeps_border_across_frames`）；`samples/` 里的截图都没有黑边，故意带上下黑边的三张 2560×1600 实机截图（`LETTERBOXED`：头像挡住轮次的两张、大厅一张）除外，它们去掉的正是上下各一条纯黑、剩下 16:9 的游戏画面（`test_letterboxed_samples`，缺这几张截图时只跳过这一条）；任务栏是否打开的判断（不做文字识别）。
  - `FlowTest`：识别流程接通会话后的周边流程——窗口过小／独占全屏／界面缩放的「不支持」提示、任务栏一打开就读、提示打开「查看筹码」面板并读取面板、读队友和自己那一行、存角色模板、畸变组件、游戏语言提示。
  - `OutOfGameFlowTest`：对局外的画面（结算画面、房间、「匹配成功」画面、奖励画面、主菜单）结束本局、开新的一局；本局已结束时不再读星数；`[map_detect]` 几个开关关掉后的行为。下方区域隔一个 `hud_ms` 两次认出主菜单结束本局、依据「检测到主菜单」，中间回到对局画面的那一次作废（`test_main_menu_ends_game`）；连回同一局（`status` 的 `cause` 为 `rejoined`）时和开新的一局一样马上再认一次角色、不等 `hero_ms`，认回这一局的角色就不再算刚连回来（`test_rejoin_rereads_hero`）。不在对局里时（还没有对局状态、本局已结束）每 `select_ms` 另外看一眼下方区域、这一眼不读星数（`test_glance_outside`），只显示两秒、落在两次读轮次之间的选角界面也认得出（`test_glance_catches_short_select_screen`、`test_glance_after_game_over`），画面停着不动时最多隔 `unchanged_max_ms` 认一次字（`test_glance_static_screen`）；对局中（连着 `quiet_ms` 看不到对局读数以后也一样）只在每 `hud_ms` 读不到轮次时认，看见轮次标记以后第二次起、离那次标记不到 `pick_grace_ms` 的几次先不认（`test_no_glance_in_game`），两开让路时不看（`test_no_glance_while_yielding`）。中途退出以后认不出主菜单（弹窗挡住了等）：读不到轮次、星数连着 `quiet_ms` 以后置 `Session.game_quiet`、记一条 `read:quiet`，这一局的状态不动，又读到轮次马上清掉（`test_quiet_after_quiet_ms`）；只算看着画面的时间，两帧之间最多算 `QUIET_STEP_S`、刚从暂停恢复的第一帧不算（`test_quiet_counts_only_looked_time`）；星数读得到、三选一一直显示着时一直算在对局里，三选一关掉以后从最后一帧三选一起算（`test_list_keeps_in_game`、`test_select_keeps_in_game`）；两开时给另一路让路的帧不算（`test_yielding_not_counted`）。停在主菜单（不在对局里、最近认出的对局外画面是主菜单）时空转、不认字：背景每帧都变、左下角的横幅轮播，底栏和开始按钮那一块（`MENU_REGIONS`）不变时，一分钟里只在第一帧读一次轮次、认一次下方区域，不空转（`menu_idle` 关掉，同时识别两个客户端的性能测试这样用）时几十次（`test_menu_idle_one_minute`）；空转满 `MENU_IDLE_MS` 照常认一次下方区域、还是主菜单就接着空转，低配模式不乘倍数（`test_menu_idle_recheck`）；对局还没结束时认出的主菜单不空转，第二次认出、结束了这一局以后才空转（`test_menu_in_game_not_idle_until_over`）；这两块变了时先认下方区域、接着整幅识别、再读轮次：选角界面开新的一局（`test_menu_idle_then_select_screen`），组队房间交给记账（`test_menu_idle_then_room`），回到对局读到轮次、开新的一局或连回同一局（`test_menu_idle_then_game`、`test_menu_idle_rejoin`）；只有开始按钮那一块变了、整幅缩略图变化不到 `loading_change` 时照样整幅识别（空转前主菜单那张缩略图丢掉），认出「匹配成功」画面（`test_menu_idle_small_change_full_read`）；窗口大小变了、从暂停恢复、按热键、界面上改了对局状态时不再空转、照常认（`test_menu_idle_resize_resume_hotkey`、`test_menu_idle_state_changed`）；两开让路期间不看那一眼，让完先看（`test_menu_idle_yielding`）。固定界面那两块里有一块自己在动时防来回进出（`MENU_FLAPS`）：开始按钮那一块每帧都变时来回 `MENU_FLAPS` 次以后只拿底栏判断，两分钟只认几次字，识别记录判之前的来回照记、最后一条 `why` 为 `bar_only`，之后来回不记（`test_menu_flap_start_bar_only`）；底栏每帧都变时来回满了就 `MENU_FLAP_MS` 以内不空转（`why` 为 `flapping`）、过了再试，每分钟认字比不空转时最多多 `MENU_FLAPS` 次（`test_menu_flap_bar_pause`）；只拿底栏判断时选角界面、组队房间、「匹配成功」画面照样那一帧就认出来，判过以后先没记的那次不再空转在认出别的画面时补记（`test_menu_bar_only_still_leaves`）；认出别的画面、窗口大小变了、从暂停恢复以后回到主菜单，开始按钮那一块一变照旧不再空转（`test_menu_bar_only_until_stay_over`）。对局中看见轮次标记以后第二次起读不到时先不判选角界面（`_pick_grace`）：看见标记以后第一次读不到照判，第二次起离那次标记不到 `pick_grace_s` 时换成读星数、`perfstats.count("pick_grace")` 记一次，过了照判（`test_pick_grace_skips_after_mark`）；每看见一次新的标记，之后第一次读不到又照判（`_grace_mark`，`test_pick_grace_first_miss_each_mark`）；读数和判了「不是」时一样（`test_pick_grace_same_reads_as_not_pick`）；按热键的那一次照判，不算「第一次」（`test_pick_grace_hotkey`）；`pick_grace_ms` 为 0 时每次都判，看见打完时的颜色也不记 `read:finish_look`（`test_pick_grace_zero`）；低配模式乘倍数（`test_pick_grace_lowend`）；刚从暂停恢复、还没读到轮次时照判（`_stale`，`test_pick_grace_after_resume`）；看颜色认出打完时的斜带、通关的红底以后 `pick_grace_s` 以内照判，第一眼没落在结算画面上、结算画面后面才出来时也马上认出，开了新的一局、离看见颜色满时限以后又照常先不判（`_ending_seen_at`，`test_pick_grace_ends_with_banner`）；对局中途看见一次像的颜色、这一局接着打时，过了时限又照常先不判，识别记录 `read:finish_look` 只在第一次看见时记一条（`test_pick_grace_back_after_stray_color`）；第一眼落在战斗等画面上、之后才回到主菜单时，到离那次标记满时限才认出，认出过一次对局外的画面（`_left_game` 清掉看见标记的时刻）以后下一次照判、不再等（`test_pick_grace_not_after_left_game`）。结算画面、对局中途回到主菜单的几条用例里，读不到的第一眼就落在这些画面上，和没有这一条时一样快。
  - `TimedReadsTest`：每一项读数各自计时（性能统计里记 `read:<项>`）。
  - `FastAdviceTest`：三选一出来约 2 秒内先给推荐、下一帧再读一次核对（轮次、进度条、星数够新时）；手动模式、刚从暂停恢复、本局还没读到过轮次、太久没读到轮次时照旧先读完再给；核对读到的不同就重算。紧接着出现的另一次三选一（概率行换了；或概率行一样、三张全换、刷新次数没少）也重新读几帧星数、先给推荐、下一帧核对，并先结算上一次；概率行一样、次数少了 1 或读不出的照旧是同一次里刷新，不读（`test_same_row_select_checked_again`、`test_same_row_refresh_not_checked`）。
  - `IdleOneReadTest`：不在三选一时一帧最多做一项读数，其余到期的排到接下来几帧；先后和「一帧做完」时相同；没到点、不用读的项不占一帧；三选一插进来时没做完的这一轮作废；任务栏刚打开立即读；按热键开始的一轮不沿用「画面没变」的结论；一项出错也占一帧。
  - `SelectRereadTest`：三选一刚出现那一帧读得可疑（刷新次数读成 0、或概率行三项加起来是 100 却不在概率表里）时，过约半秒（低配模式乘倍数，`reread_s`）再把三张卡完整认一次：认出的不同就按新的重算推荐、相同就不动；每次三选一只补认一次；读得正常的不补认。刚出现那一帧没读全（概率行还读不到）时，按第一次读全的那一次判可不可疑；补认和读左上角（核对、再读）同一帧到点时这一帧只读左上角，补认挪到下一帧；给过推荐以后读得不完整（这里是概率行读不到、筹码名都读出来了）时半秒后的再认（`SELECT_RETRY_S`）和核对同一帧到点时同样只核对、再认挪到下一帧，只有再认到点时照旧当帧做（`test_retry_moved_after_check`；还没给过推荐的反过来，见 `FirstRereadTest`）；0 星时补认读对刷新次数以后，记账改判为刚升到 1 星、推荐按 1 星算。补认读到 2 次以后刷新、又读成 0 的仍是同一次（不再补认）；补认仍读成 0 的上一组之后三张全换的一组算紧接着的另一次，照样补认一次（`test_reread_after_zero_new_select`）。
  - `FirstRereadTest`：三选一刚出现那一帧有筹码没读出来、还没给过推荐（记账先不提示无法识别）时，先完整重读三张卡、读左上角（核对等）排到重读之后，两样不在同一帧：再认（`SELECT_RETRY_S`）和核对同一帧到点时这一帧只重读、读全了马上给推荐，下一帧才核对（`test_reread_first_then_check`）；下一帧来得早、再认还没到点时核对也先等着（`test_check_waits_for_reread`）；真读不出来时再认那一帧仍读不全、离刚出现过了半秒以上才提示无法识别，之后照常先核对（`test_unreadable_after_wait_then_check`）；手动模式刚出现那一帧照旧先读完左上角，同样不当场提示（`test_manual_mode`）。读左上角排在后面有上限：提示过无法识别、给了推荐、请选择角色、三选一关掉以后都照常先读左上角，两次三选一紧接着出现、等着时被隐藏又显示、手动模式补读跳变的轮次时也一样（`test_check_not_held_after_close`、`test_another_select_rereads_first`、`test_hero_pending_prompt_then_check`、`test_hidden_while_waiting`、`test_manual_recheck_after_reread`）；核对排在后面、读到的轮次不同时照「先给推荐、下一帧核对」重算，星数照旧读前几帧（`test_check_after_reread_recomputes`）。另核对读不全的那一帧留一张给诊断包（`unread_shot`）：时刻是当时的本地时刻，画面复制一份，之后来的帧、交来识别的那块内存被改都不改它；只查绿框的帧、刷新动画的中间帧不留，刷新后等满了还读不全的那一帧照样留（`test_unread_frame_kept`、`test_refresh_frames_not_kept`）；复制出错时这一次不留、之前的照留，这一帧的读数照常交出，不算识别出错（`test_copy_error_keeps_old_shot`）。
  - `HudRetryTest`：三选一开着时读左上角没成功（核对那一帧、确认变小的轮次那一帧、手动模式先读完那一次出错或读不出轮次）时隔约 1 秒（`retry_s`）再读，每次三选一最多再读 3 次（`HUD_RETRIES`），一直出错也不每帧都读；再读的次数在新出现一次三选一、开新的一局、从暂停恢复、手动模式开关变了时清零。
  - `SelectTopTest`：三选一上读左上角的几处先后——核对那一帧刷新了（筹码名变了、这一帧要完整识别）时这一帧不读左上角，新推荐照常马上给，核对挪到下一帧（`test_refresh_defers_check`），只查绿框一直对不上、每一帧都要完整识别时只挪一帧（`test_full_read_every_frame_check_not_starved`）；只因为读不出轮次而再读时不重读进度条（`test_unreadable_retry_skips_progress`），读进度条出错时再读连同进度条（`test_progress_error_retry_reads_progress`）；先读完再给推荐的那一次不认角色，下一帧按间隔认一次（`test_read_first_hero_next_frame`）；三选一上读星数只走快的那一路、平时那一轮照常全试（`test_select_stars_quick`），手动模式平时不看画面、三选一上也照常全试（`test_select_stars_full_in_manual`）。
  - `OccludedIdleTest`：不在三选一时轮次数字被头像挡住（完整读看见了轮次标记、数字读不出）——不认选角界面、照旧读星数；之后每 0.5 秒（`occluded_s`）只在标记左边那一小块认一次字，隔 6 秒（`occluded_full_s`）才完整读一次；认出手上的轮次或往前一轮时照读到轮次处理（交给记账、接着排这一轮的其余几项，下一次完整读隔 `hud_ms`，识别记录 `read:round_crop`）；认出跳了不止一轮、变小的数，手上还没有轮次，本局已结束时不交给记账（识别记录带 `held`），隔 `hud_ms` 完整读，完整读读到了才走记账连读两次的规矩（`test_peek_jump_waits_for_full_read`、`test_peek_drop_not_delivered`、`test_peek_unknown_round_waits_for_full_read`、`test_peek_after_game_over_waits_for_full_read`）；一直挡着超过 `after_game_ms` 也不开始离开对局后的整幅识别，超过 `quiet_ms` 也不置 `Session.game_quiet`（`test_long_occlusion_not_quiet`）；地图还要靠任务栏认、任务栏开着时照常隔 `hud_ms` 连带读整块、不看那一小块（`test_tasks_open_reads_every_hud_s`）；窗口大小变了照常完整读，那一小块认字出错时只报一次、之后照常完整读（`test_peek_error_reads_normally`）；完整读认出选角界面时离开了对局，几样记下的都清掉。
  - `OccludedSelectTest`：三选一上轮次被挡住——完整读一直看见标记的，过了 `after_game_ms` 也照样先给推荐（对照：没看见标记的先读完）；核对、先读完都只在那一小块认字、连同进度条；认不出时隔 `occluded_s` 再看、不重读进度条、最多再看 `HUD_RETRIES` 次，数字露出来时按读到的轮次重算推荐；那一小块认出跳了不止一轮的数时不交给记账，隔 `retry_s` 完整读（`test_check_jump_rereads_full`、`test_check_jump_stays_wrong_not_adopted`）。
  - `OccludedRealTest`：真的 `read_hud`、`read_round_crop` 走一遍被挡住的读法（带上下黑边的截图，天线挡住数字）：交回的那一小块是去掉黑边后的画面上的像素，隔 `occluded_s` 在同一幅画面上认字、隔 `occluded_full_s` 才再完整读，不认选角界面、不做整幅识别；窗口大小变了照常完整读，那一小块重新定。
  - `MapBlockTest`：地图未知时读轮次连带读画面上方整块（`read_hud` 的 `full`）：任务栏开着时每次都连带读，没开时隔 `block_s`（`[watch] map_block_ms`）才连带读一次；连带读到了任务（按颜色认不出来的任务列表开着）以后读到没有为止每次都连带读（`test_tasks_seen_keeps_reading`）。
  - `SelectBoardTest`、`DualYieldTest`：两个客户端同时识别时两路共用的「谁的三选一正显示着、推荐还没给」（`SelectBoard`：自己的不算、超过 `BOARD_STALE_S` 没更新不算）；另一路的三选一显示着、推荐还没给时自己暂停要做文字识别的读数（`OCR_STEPS`），对局里照旧按 `hud_ms` 认角色、读星数，不设 `pending`，那边给出推荐或关掉马上接着读；自己的三选一照常认、给推荐，两路都显示着时各读各的；记号太久没更新、自己 `watch_idle` 以后不再让路或不再记着。
  - `WatchRulesTest`：`data/rules.toml` 的 `[watch]` 数值（平时看画面的间隔 500 毫秒、低配乘 2 得 1000 毫秒；已删掉的旧「整页重认间隔」不再出现；读星币数的间隔，轮次被挡住时看一眼、完整读一次的间隔，连标记都没看见时完整读一次的间隔 `blank_full_ms`，这一局近多久内读到过轮次才算「最近」`round_retry_gap_ms`（`blank_full_ms` 低配乘倍数、`round_retry_gap_ms` 不乘），任务栏没开时连带读整块的间隔，不在对局里时看一眼下方区域的间隔 `select_ms` 都与代码里的默认值一致，改了照改的读；三选一快读的开关 `select_fast` 默认开、改成 false 照改的读、低配模式不动它；对局中刚看见过轮次标记先不判选角界面的时限 `pick_grace_ms` 默认 10000、与 `PICK_GRACE_MS` 一致，低配乘 2，改了照改的读（`test_pick_grace_written_out`））。
  - `SelectFastTest`：三选一快读接进识别流程（`_select_read`），真的文字识别，同一窗口大小（2560×1057）的两次三选一：完整读认出第一次、记下位置，要认第二次时先快读，读成了不再完整读，识别记录 `read:select` 的 `how` 为 `fast`，读数（名字、原文、稀有度、刷新次数、概率、选中、全队那一行）和完整读一样（`test_next_offer_read_fast`）；快读退回时当场完整读、照它重记位置，识别记录另有 `fast_miss`（`test_fallback_reads_full_and_relearns`）；完整读记不下位置时留着原来那一份（`test_layout_kept_when_not_learnable`）；开关关掉时和原来一样每次完整读（`test_switch_off_reads_full`）；按热键的那一帧、补认那一次、画面大小和记下时不同的走完整读（`test_hotkey_reads_full`、`test_reread_and_other_size_read_full`）。
  - `RoundQuickTest`：读轮次先只认字（`_round_quick`）：完整读读到了轮次、也看见了标记以后，下一次读轮次先在标记左边那一小块只认字，认出手上的轮次或往前一轮就不再完整读（识别记录 `read:hud` 的 `how` 为 `crop`，这一轮其余几项照排）；认出别的数（跳了不止一轮、变小）、认不出时照旧完整读，不把别的数直接交给记账；画面尺寸变了、按热键、到了连带读上方整块的时候照旧完整读；三选一上读左上角也先只认字；离开对局（`_left_game`）时清掉；离最近一次完整读看见标记满 `after_game_s`（30 秒）的那一次完整读，看见了标记从那时重新算（`test_full_read_after_game_s`），没看见（对局外的画面在那一小块上正好认出手上的轮次）时接着认选角界面，之后每 `blank_full_s`（6 秒）完整读一次（连标记都没看见，见 `BlankFullTest`），直到再看见标记（`test_no_mark_keeps_full_reads`）。识别函数换成假的。
  - `BlankFullTest`：这一局正打着、刚看见过轮次时（`_mid_game`，和不重读同一个条件）完整读连标记都没看见（战斗、对决、过场）——离它不到 `blank_full_s`（6 秒）时只认字、不再完整读，当作读了一次、什么都没读到（识别记录 `read:hud` 的 `how` 为 `wait`，照常认选角界面或排读星数，计数 `hud_full_wait`，`test_wait_in_game`）；满 6 秒照旧完整读；新一局还没读到轮次、本局已结束、离上次看见标记满 `round_retry_gap_s` 时不等，照旧每 `hud_ms` 完整读、第一遍读不出照旧重读（`test_new_game_no_wait`、`test_no_wait_after_game_over`、`test_no_wait_after_retry_gap`）；完整读看见了标记或读到了轮次以后恢复每 `hud_ms`，三选一上完整读看见标记也算（`test_select_mark_ends_wait`）；等的这几次先只认字，只认字认不出才数 `hud_full_wait`，回到棋盘时只认字认出手上的轮次就不等、不完整读（`test_crop_reads_round_during_wait`）；按热键、连带读整块（`full`）、三选一上照旧完整读。
  - `HudRetryArgumentTest`：完整读轮次什么时候传 `retry=False`（不再放大、缩小各多认一遍）——`_mid_game` 为真，即手上有轮次、本局没结束、近 `round_retry_gap_s`（60 秒）内看见过标记、不在三选一、不按热键时；真的跳过重读时由 `hud.read_round_info` 数一次 `round_no_retry`（第一遍什么都不像、本来就不会重读的不数）。手上有没轮次、超过一分钟、三选一上、按热键、本局已结束时照旧重读。
  - `ProgressLookTest`：进度条那一块的样子（`hud.progress_look`、`same_progress_look`）够灵敏，用 2560×1369、进度条当前格不同的五张截图（第 1、4、8、11、12 格）：两两比都算变了；把蓝色当前格和右边一格对调（合成「当前格往前挪一格」）也算变了；同一张加高斯噪声、JPEG 重新压缩、缩小再放大都算没变；进度条本体以外（上下两条、两头圆角外露着场景的地方）换成另一张截图的算没变，反过来只把本体换成进度不同的另一张的算变了（`test_scene_around_bar_ignored`）；看不到蓝色当前格时一律不算一样。
  - `ProgressReuseTest`：进度条没变就不认字（`_read_progress`）：真的对局截图（第 8 格），读进度条换成假的。记账连读两次采用以后，那一块和上次认字时一样就不认字、把上次的数原样再交（识别记录 `read:progress` 的 `reused` 为 true，`Session` 的「最近一次读到进度」时刻跟着更新），离上次认字满 `progress_s`（同 `unchanged_max_ms`）照常认一次（`test_unchanged_reused`）；记账在等再读一次（`progress_pending`）、那一块变了、画面尺寸变了、三选一上、没给时刻时照常认字；认字读到了红叉、记账还没采用进度上限时也照常认字，连读两次采用以后才沿用（`test_cap_needs_two_reads`）；`progress_s` 跟着低配模式的倍数。每次读都数一次（`perfstats.count`）：沿用的数 `progress_reused`，照常认字的按没沿用的第一条原因数 `progress_read_select`、`progress_read_no_time`、`progress_read_first`、`progress_read_size`、`progress_read_age`、`progress_read_no_current`、`progress_read_pending`、`progress_read_look`、`progress_read_cap`（`test_counts_*`）。
  - `FullscreenPlatformTest`：查独占全屏时只看这个识别流程跟的端（`FrameProcessor(platform=...)`），`None` 时和原来一样按游戏平台设置查。
- **检证对象**：`advisor/vision/pipeline.py` 的 `FrameProcessor`（`process`、`settle`、`_read_select`、`_no_title_band`、`_record_no_band`、`_no_band_entry`、`_Unchanged`、`trim_borders`、`_edges_lit`、`_fingerprint`、`_same`、`_timed`，以及三选一补认与左上角再读（`_reread_at`、`reread_s`、`_hud_retries`、`_hud_retry_at`、`retry_s`、`HUD_RETRIES`），刚出现时读不全先重读（`_top_due`、`_top_waits`、`_last_hints`）和读不全那一帧的留存（`_note_unread`、`_copy_frame`、`unread_shot`），轮次被挡住时的读法（`_occluded`、`_occluded_crop`、`_peek_round`、`_tasks_wanted`、`_mark_seen_at`、`_left_game`、`OCCLUDED_RECHECK_MS`、`OCCLUDED_FULL_MS`），连标记都没看见时少完整读（`_full_blank_at`、`blank_full_s`、`BLANK_FULL_MS`），完整读不再多认两遍（`round_retry_gap_s`、`ROUND_RETRY_GAP_MS`，计数 `hud_full_wait`；`round_no_retry` 在 `hud.read_round_info` 里数），去黑边记住上一帧（`trim_borders` 的 `keep`、`_sparse_band`、`BORDER_SPECK`、`FrameProcessor._trim`、`_border`），任务栏没开时少读整块（`_block_due`、`_block_tasks`、`MAP_BLOCK_MS`），再读时连不连同进度条（`_retry_progress`）、先读完以后的认角色（`_hero_next`），两开时让路（`SelectBoard`、`board`、`_yielding`、`OCR_STEPS`、`BOARD_STALE_S`），主菜单空转（`_note_menu`、`_menu_idle`、`_menu_leave`、`_menu_prints`、`_menu`、`_menu_glance`、`menu_idle_s`、`menu_idle`、`MENU_REGIONS`、`MENU_IDLE_MS`）和防来回进出（`_menu_trip`、`_menu_stay_over`、`_menu_left`、`_menu_trips`、`_menu_bar_only`、`_menu_hush`、`_menu_unlogged`、`_menu_shape`、`_menu_off_until`、`MENU_TRIP_MS`、`MENU_FLAP_MS`、`MENU_FLAPS`），对局中少找字（三选一快读 `_select_read`、`_select_layout`、`select_fast`，读轮次先只认字 `_round_quick`、`_round_memo`，进度条没变就不认字 `_read_progress` 的 `now`、`_progress_seen`、`progress_s`），对局中先不判选角界面（`_pick_grace`、`_grace_mark`、`_stale`、`_ending_seen_at`、`pick_grace_s`、`PICK_GRACE_MS`，计数用 `advisor/perfstats.py` 的 `count`），对局中途像一局打完的那一帧（`_note_finish_look`、`_finish_pending`、`finish_shot`、`FINISH_SHOT_SIDE`），还有 `platform`、`MIN_UI_SCALE`、`COINS_MS`、`SELECT_RETRY_S` 等常量）；`advisor/vision/hud.py` 的 `Hud.round_mark`、`Hud.round_crop`、`read_round_crop`、`progress_box`、`progress_look`、`same_progress_look`、`PROGRESS_LOOK_*`；`advisor/vision/chip_select.py` 的 `read_chip_select_fast`、`select_layout`（接进识别流程的那一层；快读法本身见 test_chip_select_fast）；`advisor/session/core.py` 的 `WatchHints`（`select_fresh`、`select_unread`、`round_now`）；`advisor/session/` 的 `Session`、`Settings` 与 `on_frame`/`on_progress`/`on_out_of_game`/`build_state`；`advisor/vision/hud.py` 的 `task_panel_visible`；`advisor/vision/screens.py` 的 `OutOfGame`；`advisor/vision/chip_select.py` 的 `difficulty_of`（概率行是不是概率表里的一行）、`looks_like_overlay_screen` 和第一段 `dark_band`（没过预筛时分两种情况）；`data/rules.toml` 的 `[watch]`（含 `select_reread_ms`、`hud_retry_ms`、`occluded_recheck_ms`、`occluded_full_ms`、`blank_full_ms`、`round_retry_gap_ms`、`map_block_ms`、`pick_grace_ms`）、`[map_detect]`。
- **什么时候要跑**：改了 `advisor/vision/pipeline.py`（尤其读取时机、三选一无变化判定、三选一快读、三选一补认与左上角再读、刚出现时读不全的重读、读不全那一帧的留存、轮次被挡住时的读法、读轮次先只认字、进度条没变就不认字、对局中先不判选角界面、两开时让路、主菜单空转、去黑边、计时、`platform`）、`advisor/vision/hud.py` 的进度条那一块（`progress_box`、`progress_look`）、`advisor/session/` 的会话推进，或 `data/rules.toml` 的 `[watch]`（含 `select_reread_ms`、`select_fast`、`hud_retry_ms`、`occluded_recheck_ms`、`occluded_full_ms`、`blank_full_ms`、`round_retry_gap_ms`、`map_block_ms`、`select_ms`、`pick_grace_ms`）、`[map_detect]` 之后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_pipeline`（上次约 1.8 秒；不是慢测试）。
- **需要什么**：一部分用例要识别样本截图——`QuickRecheckTest`、`ProgressInSelectTest`、`UnchangedScreenTest`、`BordersAndTaskPanelTest`、`OccludedRealTest`、`SelectFastTest`、`ProgressLookTest`、`ProgressReuseTest`；其余（`FlowTest`、`OutOfGameFlowTest`、`TimedReadsTest`、`FastAdviceTest`、`IdleOneReadTest`、`SelectRereadTest`、`FirstRereadTest`、`HudRetryTest`、`SelectTopTest`、`OccludedIdleTest`、`OccludedSelectTest`、`MapBlockTest`、`SelectBoardTest`、`DualYieldTest`、`WatchRulesTest`、`FullscreenPlatformTest`、`RoundQuickTest`、`BlankFullTest`、`HudRetryArgumentTest`）识别函数都换成假的，不需要样本。不需要显卡、不弹真窗口。
  - 样本用环境变量 `ACC_SAMPLES_DIR` 指定目录，默认是仓库根目录的 `samples/`。用到的截图（都在 `samples/`）：三选一刷新前后和选中各一张、战斗画面一张、任务三选一图标未加载和已加载各一张、进度条退回一张、同一窗口大小（2560×1057）的两次三选一、进度条当前格不同的五张 2560×1369 截图，另有几张对局画面；`BordersAndTaskPanelTest` 会把 `samples/` 里 2026 开头的每张截图都过一遍。不读任何标注文件。
- **输出怎么看**：缺样本时整类跳过，跳过原因写明缺哪个文件，形如「没有识别样本：缺 samples/<文件名>（准备方法见 docs/测试指南.md「准备识别样本」）」；没有别的特有输出，也不写临时文件。其余同总则。
- **出错时一般是哪里不对**：
  - `QuickRecheckTest.test_reroll_triggers_full_read_not_timer` 失败 → 三选一什么时候重新完整识别的条件改坏了（应当只在筹码名变了／窗口大小变了时重认，不能按时间定时重认）；去看 `advisor/vision/pipeline.py` 的 `_read_select` 和 `FrameProcessor.process`。
  - `QuickRecheckTest.test_selected_frame_found_without_ocr` 失败 → 「只查绿框」或「选中了哪个」的判定（`picked` 事件）改坏了。
  - `QuickRecheckTest.test_incomplete_read_retried`、`test_icons_not_loaded_then_loaded` 失败 → 读得不完整时的重试间隔（`SELECT_RETRY_S`）、稀有度缺失的判定，或出场动画后筹码名位置变了要马上重认的逻辑改坏了。
  - `ProgressInSelectTest` 失败 → 三选一期间进度条的补读规则（`_recheck_progress`、只补一次、两次一致才采用）或 `Session.on_progress` 的「连读两次才采用」改坏了。
  - `UnchangedScreenTest` 失败 → 画面没变的判定（`_Unchanged`、`_fingerprint`/`_same`、沿用上限）改坏了，或按热键强制识别、或「地图未知时连任务栏一起读」的逻辑改了；用真战斗画面、提到标题带的几条失败时看没过预筛那一段（`_no_title_band`、`_record_no_band`）：标题带不在的帧有没有记成「不是」、标题带回来时有没有作废那一条、有缓存时是不是照常只查绿框。
  - `BordersAndTaskPanelTest.test_trim_borders`、`test_edges_first_same_as_full`、`test_random_same_as_full` 失败 → 去黑边规则（黑边亮度门槛、至少占一边的比例、去掉后至少剩多少、快路判据 `_edges_lit`）改坏了。数值是有意改的就同步改断言。
  - `BordersAndTaskPanelTest` 的 `test_keep_*`、`test_processor_keeps_border_across_frames` 失败 → 记住上一帧范围的那一段改了（`trim_borders` 的 `keep`、`_sparse_band`、`BORDER_SPECK`、`_edges_lit` 在给了 `keep` 时不再直接返回、照 `keep` 去完剩不到 `BORDER_KEEP` 时照这一帧自己算的、`FrameProcessor._trim` 和 `_border` 的尺寸比较）。
  - `BordersAndTaskPanelTest.test_samples_have_no_borders` 失败 → 新放进 `samples/` 的某张截图自带黑边（故意带黑边的要加进 `LETTERBOXED`），或快路判据改了。
  - `BordersAndTaskPanelTest.test_letterboxed_samples` 失败 → 去黑边规则改了（去多、去少，或左右也去了），或那几张截图换了；数值是有意改的就同步改 `LETTERBOXED`。
  - `BordersAndTaskPanelTest.test_task_panel_visible` 失败 → 任务栏是否打开的判断或样本清单改了。
  - `FlowTest.test_small_window_and_fullscreen`、`test_small_window_judged_by_ui_scale` 失败 → 「不支持」的判定（客户区宽度门槛、界面缩放、独占全屏）或提示文字改了；提示文字是有意改的就同步改断言。
  - `FastAdviceTest`、`IdleOneReadTest` 失败 → 读取时机（先给推荐、下一帧核对、一帧一项）改坏了，这是这一块最核心的行为。`test_same_row_*` 失败 → 识别流程问的「是不是紧接着出现的另一次」（`advisor/session/selection.py` 的 `starts_another`、`fresh_offer`，经 `WatchHints.another_select`）和记账的结论对不上，或 `_select_reads` 没按它重新读。
  - `SelectRereadTest` 失败 → 三选一读得可疑时的补认（什么时候补、只补一次、认出不同才重算推荐，`_reread_at`/`reread_s`；没读全时等第一次读全再判 `_suspect_due`；和读左上角同一帧到点时挪到下一帧 `_top_due`），或低配模式乘倍数改了；`test_retry_moved_after_check` 失败 → 读得不完整时的再认没和核对错开（`_read_select` 里用缓存的条件）；`test_fixed_rerolls_level_up` 单独失败时多半是记账那边（`Session._open_judged`）没跟着补认重新判；`test_reread_once_per_offer`、`test_reread_after_zero_new_select` 失败也可能是记账「紧接着的另一次还是刷新」的判据（`selection.py` 的 `fresh_offer`）改了。
  - `FirstRereadTest` 失败 → 刚出现时读不全的先后（`_read_select` 定 `_top_waits` 的条件：要读左上角、缓存的读数不完整、上一帧做完时的提示里 `select_fresh` 为真；`_select_reads` 这一帧不读左上角），或记账那边「刚出现时先等一等」（`selection.py` 的 `hold_frame`、`fresh`）改了；`test_unreadable_after_wait_then_check` 失败多半是等满的条件（`HOLD_RETRY_S`、`HOLD_READS`、`HOLD_MAX_S`）或提示过以后不再算刚出现（`_told`）改了。`test_unread_frame_kept`、`test_refresh_frames_not_kept` 失败 → 留不留那一帧（`_note_unread` 看的 `select_unread`，记账的 `SelectTracker.unread`：刷新动画的中间帧不算），或留的不是复制的一份（截取的缓冲被复用时跟着变）；`test_copy_error_keeps_old_shot` 失败 → 复制出错时报成了识别出错或拦住了这一帧的读数（`_note_unread` 要自己接住出错，不经 `_safe`；复制在 `_copy_frame`）。
  - `HudRetryTest` 失败 → 读左上角没成功时的再读（间隔、最多 3 次、次数清零的时机，`_hud_retries`/`_hud_retry_at`/`retry_s`/`HUD_RETRIES`）改了。
  - `SelectTopTest` 失败 → 刷新那一帧没把读左上角挪开、或连着挪了不止一帧（`_read_select` 定 `_top_waits`、`_top_deferred`）、再读时连不连同进度条（`_select_hud` 的 `_retry_progress`）、先读完以后认角色的时机（`_hero_next`、`_top_due`），或三选一上读星数没带 `quick`（`_read_stars`）。
  - `SelectFastTest` 失败 → 三选一快读接进识别流程的那一层改了（`_select_read`：什么时候先快读、退回后完整读、什么时候重记位置，识别记录的 `how`、`fast_miss`）；只有 `test_next_offer_read_fast` 的读数比对失败时，先跑 test_chip_select_fast 看快读法本身（`read_chip_select_fast`、`select_layout`）。
  - `RoundQuickTest` 失败 → 读轮次先只认字改了（`_round_quick` 的条件和接受规矩、`_read_hud` 里记下那一小块的 `_round_memo` 和完整读看见标记的时刻 `_full_mark_at`、`_left_game` 清不清）。
  - `BlankFullTest` 失败 → 对局中连标记都没看见时少完整读改了：`_full_blank_at` 什么时候设成当前时刻、什么时候设回 `-inf`（读到轮次、看见标记、`_round_seen`），`_read_hud` 里等的那一支的条件（`_mid_game` 为真、不连带读整块、只认字没认出、没满 `blank_full_s`）、返回值和识别记录 `how: "wait"`，`_mid_game` 的条件（不在三选一、不按热键、手上有轮次、本局没结束、离 `_mark_seen_at` 不到 `round_retry_gap_s`），或 `set_pace` 没乘 `blank_full_s`。
  - `HudRetryArgumentTest` 失败 → 完整读传不传 `retry=False` 的条件改了（`_mid_game`：`round_now`、`game_over`、`now - _mark_seen_at < round_retry_gap_s`，三选一上、按热键时为假）。
  - `ProgressLookTest` 失败 → 进度条那一块的样子不够灵敏或太灵敏了（`hud.progress_look`、`same_progress_look`、`PROGRESS_LOOK_*` 门槛），或进度条那一块的位置（`progress_box`）、只取本体的范围（`PROGRESS_LOOK_TOP`、`PROGRESS_LOOK_BOTTOM`、`PROGRESS_LOOK_INSET`）、蓝色当前格的找法（`_blue_cells`）改了。
  - `ProgressReuseTest` 失败 → 进度条没变就不认字改了（`_read_progress` 的沿用条件：样子一样、尺寸相同、不到 `progress_s`、读到过当前格、记账没在等再读一次、读到过红叉时记账已经采用了进度上限（`_cap_wanted`）；`_read_step` 给不给 `now`），或每次读数的原因名、时机改了（`test_counts_*` 核对 `perfstats.count` 的 `progress_reused`、`progress_read_*`）。
  - `OccludedIdleTest`、`OccludedSelectTest` 失败 → 轮次被挡住时的读法改了：看见标记以后的排法（`_read_hud` 末尾）、看一眼和完整读的间隔（`_idle_reads`、`occluded_s`、`occluded_full_s`）、看一眼读到的交不交给记账（`_peek_round`：手上的轮次或往前一轮才交，看 `WatchHints.round_now`）、任务栏开着时的读法（`_tasks_wanted`）、在不在对局里（`_mark_seen_at`、`_left_game`、`_check_loading`）、三选一出来够不够新（`_readings_fresh`）、三选一上读左上角（`_select_hud`、`_retry_later`）；也可能是 `hud.Hud` 的 `round_mark`、`round_crop` 改了名字。
  - `MapBlockTest` 失败 → 地图未知时连不连带读整块的条件（`_block_due`、`block_s`、`task_panel_visible`、上一次读到了任务的 `_block_tasks`）改了。
  - `SelectBoardTest`、`DualYieldTest` 失败 → 两开时让路的规矩（`SelectBoard`、`BOARD_STALE_S`、`_yielding`、`OCR_STEPS`、`_idle_reads` 里让路那一支、`watch_idle` 擦掉记号）改了。
  - `WatchRulesTest` 失败 → `data/rules.toml` 的 `[watch]` 数值或 `pipeline.COINS_MS`、`pipeline.SELECT_MS`、`pipeline.PICK_GRACE_MS` 改了；数值是有意改的就同步改断言。
  - `OutOfGameFlowTest` 里 `test_pick_grace_*` 失败 → 对局中先不判选角界面改了：时限从哪一刻算（`_mark_seen_at`，读到轮次、只认字认出、`_left_game` 都改它）、哪几种照判（看见标记以后第一次读不到 `_grace_mark`、`force`、`_stale`、看见打完时的颜色以后 `pick_grace_s` 以内 `_ending_seen_at`、`hints.game_over`、`has_game_state`）、先不判时换成读星数（`_read_hud` 里排的 `_hud_rest`）、计数（`perfstats.count` 的 `pick_grace`，以及照判时按条件数的 `pick_check_force`、`pick_check_stale`、`pick_check_no_mark`、`pick_check_old`、`pick_check_no_game`、`pick_check_first`、`pick_check_ending`，见 `test_pick_grace_counts_each_reason`），或 `set_pace` 没乘倍数；`test_pick_grace_ends_with_banner`、`test_pick_grace_back_after_stray_color` 失败多半是 `_check_victory` 不再记 `_ending_seen_at`、照判的时限不是 `pick_grace_s` 了，或 `read:finish_look` 记得不对（同一段颜色记了不止一条、字段改了名）；`test_finish_stray_color_*` 失败 → 对局中途像打完的那一帧留不留、缩到长边 1280、什么时候转正成 `finish_shot`（读到轮次标记、满 `pick_grace_s`，记账没在等再读一次变小的轮次 `round_drop_pending`）、什么时候丢掉（本局结束、`_left_game`、留下以后判了新的一局 `WatchHints.games`），或复制出错时被当成了识别出错（`_note_finish_look`、`_finish_pending`、`FINISH_SHOT_SIDE`）。结算画面、对局中途回到主菜单那几条（`test_result_*`、`test_main_menu_ends_game` 等）跟着一起失败时，先看是不是第一次读不到不再照判了（`_grace_mark`）。
  - `OutOfGameFlowTest` 里 `test_glance_*`、`test_no_glance_*` 失败 → 不在对局里时看的那一眼改了：什么时候看（`_idle_reads` 的 glance 那一支、`_outside`、`select_s`、`_last_glance`）、看的时候顺不顺带读星数（`_read_pick` 的 `stars`）、静止画面沿用「不是」（`_not_pick`），或让路时的 `OCR_STEPS`。
  - `OutOfGameFlowTest` 里 `test_menu_*` 失败 → 主菜单空转改了：什么时候记下主菜单（`_note_menu`：`_read_pick`、`_check_loading` 认出主菜单、`_outside` 为真时才记）、哪一帧算还在主菜单（`_menu_idle`：`MENU_REGIONS` 两块的指纹、画面尺寸、`menu_idle_s`、按热键、`_outside`）、不再空转以后先看一眼下方区域再整幅识别（`_menu_leave` 置 `_menu_glance`、丢掉 `_loading_thumb`，`_idle_reads` 里 `_menu_glance` 那一支），或空转的帧跳过了不该跳的（`process` 里 `_menu_idle` 那一支）。只有 `test_menu_flap_*`、`test_menu_bar_only_*` 失败时是防来回进出改了：怎么算一次来回、满了怎么判（`_menu_trip`，`MENU_TRIP_MS`、`MENU_FLAP_MS`、`MENU_FLAPS`），只拿底栏判断时比哪一块（`_menu_idle`），什么时候从头数（`_menu_stay_over`：`_note_menu` 认出别的画面或画面尺寸变了、`_read_pick` 认出选角界面、`_note_in_game`、`resumed`），判过以后哪些进出不记、先没记的什么时候补记（`_menu_leave`、`_menu_unlogged`），暂停空转期间不记主菜单（`_note_menu`、`_menu_off_until`）。
  - `FullscreenPlatformTest` 失败 → 查独占全屏时没用这一张自己的端，或 `platform` 的处理改坏（`FrameProcessor._check_window`）。

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
  - `GameEndSequenceTest`：把「一局结束、下一局开始」拼起来回放（第 7 轮→结算画面→奖励画面→组队房间→第 1 轮），核对结算画面第一张（最后一次看见轮次标记以后第一次读不到，对局中先不判选角界面那一条照判）就结束本局恰好一次、房间上勾选的难度留给下一局、连续两次读到第 1 轮时开新的一局且不可撤销。`GameEndBannerSequenceTest`：同一段在第 7 轮后面多一张打完时棋盘上的斜带（打赢时那张真截图，左上角也是第 7 轮），核对斜带上不结束什么、结算画面第一张照样结束本局恰好一次、之后照样开新的一局。
  - `MainMenuSequenceTest`：用真的主菜单截图（简体、繁体各一张）拼三段回放：第 7 轮→主菜单→繁体主菜单→第 1 轮，核对两次认出主菜单（下方区域认得出；第一次是最后一次看见轮次标记以后第一次读不到，照判）结束本局恰好一次、依据「检测到主菜单」、状态保留，结束以后再读到主菜单不出错，读到第 1 轮开新的一局（可以撤销）、不请用户打开「查看筹码」面板；第 7 轮→主菜单→第 7 轮，核对连回同一局（`read:rejoined`），不开新的一局；刚打开时就在主菜单（两张）→第 1 轮，核对不结束、不开新的一局，进对局时从开局看起、不提示。
  - `MainMenuIdleTest`：繁体主菜单那张真截图（上下带黑边），每 0.5 秒一帧，上方和中间每帧换一种亮度（整幅缩略图的变化超过 `loading_change`），左下角的横幅每 5 秒换成画面上方的另一块，底栏和开始按钮那一块不动：第一帧读一次轮次、认一次下方区域，认出主菜单以后空转，识别引擎（耗时统计里的 `ocr`）只在第 0 秒和满一分钟的那一帧被调用，整幅识别一次都没做；第 65 秒开始按钮那一块换成一片灰，不再空转，这一帧先认下方区域、接着整幅识别。
  - `MainMenuRegionsTest`：主菜单空转凭的那两块（`MENU_REGIONS`：底栏那一条、开始按钮那一块），先去黑边再取指纹：同一客户端（繁体）不同时刻的三张主菜单（背景光效、左下角横幅、货币数各不相同）两两之间尺寸一样、两块都算一样（`_same`），左下角横幅那一块确实不同；`labels.json` 里离开主菜单以后会看到的画面（房间、「匹配成功」画面、选角界面、选皮肤、加载画面、结算画面、奖励画面、对局画面）和这三张比，两块里至少有一块对不上。
  - `PreviewSizeTest`：预览图按 1600x855 原样喂（相当于窗口就这么大）和放大回客户端原尺寸，认选角界面、认角色、读面板、读任务栏的结果相同。
- **检证对象**：`advisor/vision/pipeline.py` 的 `FrameProcessor`（真识别）+ `advisor/session/` 的 `Session`（`on_frame`、`on_hud`、`on_progress`、`on_progress_cap`、`on_panel`、`on_out_of_game`、`judge_source` 等）；识别都在 `advisor/vision/chip_select.py`、`hud.py`、`stars.py`、`chip_panel.py`、`screens.py` 里。回放方式：每张截图停两帧，第一帧在拍摄时刻，之后每帧隔 4 秒（测试里的 `replay`；截图回放工具 `tools/replay.py` 是同样的回放，每张停三帧，见软件维护说明「开发工具」）。
- **什么时候要跑**：改了 `advisor/session/` 的新一局判定、难度、进度上限或记账，改了 `advisor/vision/` 里轮次／星数／三选一／面板／对局外画面的识别，或改了真截图的回放方式之后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_pipeline_sequences`（上次约 0.6 秒；慢测试）。
- **需要什么**：识别样本截图和标注。要 `samples/labels.json`、`samples/stars_labels.json`，以及 `samples/` 里同一局（文件名以 20261002_21 开头）的截图不少于 20 张；`GameEndSequenceTest` 另要 6 张跨两局的截图，`GameEndBannerSequenceTest` 再加一张打完时的斜带（在 `pages/` 下），`MainMenuSequenceTest` 另要两张主菜单截图和 `GameEndSequenceTest` 里的第 7 轮、第 1 轮两张，`MainMenuIdleTest` 只要其中繁体主菜单那一张，`MainMenuRegionsTest` 要繁体客户端的三张主菜单（另用 `labels.json` 的 `scene` 挑离开主菜单以后会看到的画面）。用到的标注字段：`labels.json` 每条的 `file`、`scene`、`round`、`rerolls`、`probabilities`、`picked`、`owned`、`team`、`source`；`stars_labels.json` 每条的 `file`、`own`、`own_row`。`panel_labels.json`、`tests/data/coins/labels.json` 这一组用不到。
- **输出怎么看**：缺 `labels.json` 或 `stars_labels.json`，或这一局的截图不足 20 张时，`RealGameReplayTest`、`PreviewSizeTest` 整类跳过；`GameEndSequenceTest` 单独按缺的那 6 张文件名跳过，`GameEndBannerSequenceTest` 按那 6 张加斜带那一张，`MainMenuSequenceTest` 按缺的那 4 张跳过，`MainMenuIdleTest` 缺繁体主菜单那一张时跳过，`MainMenuRegionsTest` 缺三张主菜单里任一张时整类跳过、缺 `labels.json` 时只跳过和别的画面比的那一条。跳过原因写明缺哪个文件。没有别的特有输出。其余同总则。
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
  - `GameEndSequenceTest` 失败 → 「本局已结束」之后开新一局的判定（连续两次读到同一轮次、不可撤销）或房间难度的沿用改了；只是结算画面认得晚了（第二张才结束）时，是对局中先不判选角界面那一条不再在第一次读不到时照判了（`_pick_grace` 的 `_grace_mark`）。`GameEndBannerSequenceTest` 单独失败、斜带那一张上结束了或没读到第 7 轮时，看 `victory.glance`、`_check_victory` 和读轮次；只是结算画面认得晚了时同上。
  - `MainMenuSequenceTest` 失败 → 主菜单的识别（`advisor/vision/screens.py` 的 `_main_menu`，下方区域要盖得住开始按钮和底下一排按钮）、主菜单结束本局（`advisor/session/offgame.py` 的 `SCREEN_RULES`、`confirmed`）、连回同一局（`rejoinable`、`rejoin`），或刚打开时在大厅、进对局时提示不提示面板（`advisor/session/core.py` 的 `_entered_game`、`_from_start`）改了；结束、连回的那一张往后挪了，是对局中先不判选角界面那一条（`_pick_grace`）不再在第一次读不到、刚认出主菜单（`_left_game` 清掉看见标记的时刻）以后照判了。
  - `MainMenuIdleTest` 失败 → 主菜单空转（`advisor/vision/pipeline.py` 的 `_note_menu`、`_menu_idle`、`_menu_leave`、`MENU_REGIONS`、`MENU_IDLE_MS`）改了，或繁体主菜单的下方区域认不出了（`advisor/vision/screens.py` 的 `_main_menu`）；只有 `test_frames_keep_changing` 失败时是测试里模拟的动画、横幅换到了 `MENU_REGIONS` 那两块里。
  - `MainMenuRegionsTest` 失败 → `test_menus_alike`：`MENU_REGIONS` 改到了左下角横幅这类会换的地方，或指纹（`_fingerprint`、`FP_SIZE`、`FP_SAME`）、去黑边（`trim_borders`）改了，停在主菜单时会一直进出空转（三张截图上背景光效的差别按指纹算不大，改到背景上不一定在这里失败，改 `MENU_REGIONS` 时另要看连续画面）；`test_other_screens_differ`：`MENU_REGIONS` 改得太空（只剩纯色、黑边一类），离开主菜单以后可能接着空转、漏认画面，先看失败的是哪一张。
  - `PreviewSizeTest` 失败 → 窗口尺寸换算（预览图放大回原尺寸）或缩放后的识别改坏了。
  - 标注里的数值（第几轮、刷新次数、已有筹码、选中的筹码）是有意改的就同步改断言。

## test_vision_matrix.py：按 `samples/` 全部人工标注逐张核对各识别模块

- **测试观点**：
  - 样本本身：三份标注引用的截图都在、`stars_labels.json`/`panel_labels.json` 里的每张图 `labels.json` 都有、反例画面一种不缺；原图尺寸和标注一致、缩小版确实更小且界面缩放不低于最小窗口。
  - 三选一（`chip_select`）：过预筛；筹码名、稀有度、刷新次数、概率、选中项、全队三选一那一行和标注一致、自检提示和标注的 `expect_problems` 一致（没写的为没有；截图早于游戏更新、资料已照新版改的样本，自检照资料会报的那几条写在标注里，识别评测工具 `tools/eval_vision.py` 同样照它核对）；反例（大厅、选角、选皮肤、载入、结算、卡牌商店、玩家详情、转账、对局、战斗、面板、任务栏）都不能读成三选一。
  - 左上角（`hud`）：标了轮次的读对（压暗或被挡的画面读不出不算错、读错算错）；对局外画面读不到轮次；任务栏开着时按任务文字认地图、其他画面不误认地图；进度上限（红叉格号）读对或读不出（不能读错）；标了进度当前格的样本原图和缩小版都读对。
  - 两条「曾经的缺陷」锁定用例：4:3 小窗口里第 2 轮三选一不得读成 20；4:3 小窗口里第 13 轮面板画面要读得出 13。
  - 选角界面（`hud.is_select_screen`）：只有选角界面判成选角界面。
  - 对局外画面（`screens.parse_out_of_game`）：标了 `screen` 的读数与标注完全相同（变灰的组队房间难度可读不出）；其余样本不能认成结算页、匹配成功页、房间、奖励页或主菜单。下方区域那一次识别同理，主菜单在下方区域也要读得出。
  - 角色（`hero.read_hero`）：看得见立绘的画面认出标注的角色；立绘被挡住（面板、战斗、选角）时认不出；其余画面不能认错。
  - 玩家列表（`stars`）：自己的星数、按头像认出的行和标注一致，被挡住/没有列表/列表里没有自己时返回 `None`；每行亮着几颗星读对、标注之外多出来的行只能是推算的（看不见）；队友角色和是否电脑不读错、自己那一行看得见时要认出是哪一行；转账画面上没有自己的星数。
  - 「查看筹码」面板（`chip_panel`）：筹码、页签个数、选中页签、点选格子与标注一致、没有无法识别的格子和自检提示（截图早于游戏更新、资料已照新版改的样本，筹码、认不出的格数、识别提示按 `panel_labels.json` 的 `expect_read`，识别评测工具 `tools/eval_panel.py` 同样照它核对）；其余样本都不是面板；玩家详情弹窗不是面板。
- **检证对象**：各识别模块——`advisor/vision/chip_select.py`（`read_chip_select`、`looks_like_overlay_screen` 和它的两段 `dark_band`、`title_band_visible`）、`advisor/vision/hud.py`（`read_hud`、`is_select_screen`、`read_pregame`、`read_progress`、`read_progress_cap`、`task_panel_visible`）、`advisor/vision/screens.py`（`parse_out_of_game`）、`advisor/vision/hero.py`（`read_hero`）、`advisor/vision/stars.py`（`diagnose`、`read_players`）、`advisor/vision/chip_panel.py`（`read_chip_panel`）。
- **什么时候要跑**：改了任何一个识别算法、`advisor/vision/anchors.py` 里的几何量、或 `advisor/vision/pipeline.py` 里决定「哪种画面跑哪些识别」的流程；改了标注或换了样本也要跑。是覆盖面最广的一项识别回归。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_vision_matrix`（慢测试：识别慢，单进程约 4~5 分钟；`setUpClass` 里把样本分给几个子进程一次读完，子进程数取逻辑核数的一半、最多 8 个。上次的记录约 0.6 秒，那次多半是没有样本整类跳过了）。
- **需要什么**：`samples/` 下全部样本 + `labels.json`、`stars_labels.json`、`panel_labels.json` 三份标注；还要 `assets/` 里的图片（`assets/heroes/UT_Hero_RolePhoto_*.png`、`assets/profiles/UT_Hero_ProfilePhoto_*.png`、`assets/chips/UT_Relic_50081.png`、`assets/stars/star_bar.png`）。会真的建文字识别引擎（子进程里每个 1 个线程）。不是实机测试，不开真实窗口。
- **输出怎么看**：
  - 缺样本或缺 `assets/` 时整类跳过：前者原因同 test_stars 的 `samples_reason`，后者「没有识别用的图片（assets/）」。
  - 某个模块识别时抛异常时，这一项失败并附上子进程里的调用栈（`识别时出错：…`）。
  - 每条断言用子测试标出是哪张图、哪个变体（原图/缩小）、哪个场景；标了 `SUSPECTED` 的已知项会跳过（当前为空）。
  - 哪些画面跑哪些模块照悬浮窗流程：三选一先过预筛再做文字识别；对局外画面的整幅识别所有样本都跑。过了预筛第一段、被第二段拦下的反例（战斗、玩家详情弹窗、卡牌商店等上方也有黑带的画面）另外照样做一次三选一的文字识别，记在 `select_dark`（悬浮窗不做这一次）。
- **出错时一般是哪里不对**：
  - `test_chip_select_matches_labels` 失败 → 三选一的筹码名匹配、稀有度、刷新次数、概率或选中判断改坏了（看 `chip_select.read_chip_select`、`_match_chip`、`_ring_rarity`、`_count_near`、`_probabilities`、`_selected_column`）。
  - `test_other_screens_are_not_chip_select` 失败 → 预筛 `looks_like_overlay_screen` 或标题匹配放宽了，把别的画面读成三选一。失败在 `select_dark` 这一项（过了预筛第一段、被第二段拦下的反例照样认的那一次）的，是标题匹配放宽了：悬浮窗这时还有预筛挡着，预筛再放宽就会读错。
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
  - `FrameTest`：`as_frame` 收 ndarray 时包一层、收 `Frame` 时原样返回；`image`/`w`/`h` 正确；`ui_scale()` 同一帧只算一次并缓存；`ui_range()` 和 `chip_select.ui_range(w, h)` 相同；`stars.diagnose` 在同一帧同一个角色上只算一次；三选一预筛第一段（`Frame.dark_band`）同一帧只算一次，结果是假的也不重算。
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
  - `MutatorCatalogTest`：畸变组件清单按资料库实际字段取（组件名、效果说明 `effect`、繁体名 `hant`，不是 `en`/`hint`）；只读到一个组件名时靠它旁边的效果说明相近文字认出；繁体名也认、交回简体名，繁体画面上只读到一个组件名时照旧不下结论（效果说明只有简体写法）。
  - `HeroIdHantTest`：按名字找角色编号时（`stars._hero_id`），角色的繁体名、繁体全名也认得出。
  - `ViewChipsHantTest`：存立绘模板前确认左下角的「查看筹码」按钮在：繁体的「查看籌碼」也认，别的文字照旧不存。
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
- **检证对象**：`advisor/vision/hud.py`（`mutator_catalog`、`_find_mutators`）、`advisor/vision/io.py`（`imread`）、`advisor/vision/hero.py`（`save_template`、`user_dir`）、`advisor/vision/stars.py`（`MissingTemplates`、`_bar_template`、`MIN_FRAME`）、`advisor/vision/pipeline.py`（`FrameProcessor.process`/`settle`、`errors`、`MIN_FRAME`）、`advisor/ui/screen.py`（`FrameSource`、`NoFrame`、`_client_changed`）、`advisor/vision/capture.py`（`grab_region`、`GameWindow`、`set_platform`）、`advisor/vision/window_capture.py`（`Support`）、`advisor/ui/hotkey.py`（`parse_hotkey`）、`advisor/ui/overlay_parts/watcher.py`（`Watcher`）、`advisor/ui/overlay_parts/wording.py`（`RECOG_ITEM`）。
- **什么时候要跑**：改了 `advisor/vision/hud.py` 的组件清单、`io.py` 的读图和识别结果转换、`hero.save_template`、`stars.MissingTemplates`/`_bar_template`、`pipeline.FrameProcessor` 的子识别隔离、`ui/screen.py` 的取帧和窗口变化处理、`capture.grab_region`、`capture.set_platform`（只认一个端的游戏窗口，取帧和窗口变化跟着它）、`hotkey.parse_hotkey`、`watcher.py` 的出错上报、或 `pipeline` 里面板模块的导入兜底。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_vision_robust`（上次没有记录）。多数用例把识别引擎换成假的，不需要识别样本；`WatcherReportTest` 用离屏 Qt（`tests/support/qtenv.py` 的 `app()`），不需要真实窗口。
- **需要什么**：没有特别要求（不需要识别样本截图、不需要显卡、不开真实窗口）；`WatcherReportTest` 要离屏 Qt 环境。
- **输出怎么看**：`capture_errors` 把错误记录指到临时文件，用例断言里面的条目（如「识别出错（拾星助手仍在运行）」的标题和调用栈文字）；出错上传的提示是按 `wording.RECOG_ITEM` 格式化的「角色这次没读出来，已自动重试；多次出现请点击「设置」窗口中的「检查更新」按钮，更新到最新版拾星助手。」；没有别的特别输出。
- **出错时一般是哪里不对**：
  - `MutatorCatalogTest` 失败 → `hud.mutator_catalog` 取的字段改了（应是 `data/mutators.json` 的 `effect`、`hant`），或 `_find_mutators` 的单名兜底逻辑改了。
  - `HeroIdHantTest` 失败 → `stars._hero_id` 的名字索引没带上繁体名、繁体全名。
  - `ViewChipsHantTest` 失败 → `anchors.VIEW_CHIPS` 里的写法改了。
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
  - `OcrRangeTest`：`io.ocr` 空画面不调用识别引擎；范围超出画面、整个在画面外、取整后上下颠倒时都夹进画面（不交给引擎空图）；坐标换回原图像素；`det_side`、`pad`（找字一步的放大设置和细长图补不补边）只在这一次识别里改，识别完、出错时都还原，不给时不动；引擎上没有这两个设置时照原样识别。
  - `RerollRereadTest`：三选一右侧「刷新」按钮的剩余次数——按钮上或第一次读到的数字就用；按钮上没数字时放大重读按钮附近，重读什么都没读到时是 `None`（按钮在、数字读不出，不是 0）；重读到的「×N」按放大倍数和裁剪位置换回原图后落在按钮正下方才认。
  - `RoundMarkTest`：左上角的轮次标记——简体的「轮」、繁体界面的「Round」（也认「輪」）都认得出，数字在标记左边或同一段里都读得到；繁体的标记只在数字那一行认，玩家列表第一行的昵称里带 Round 这类字母时不当成标记。标记和头像、数字连成一段（「ARound」「03轮」）时也认（`_round_mark` 简繁都只认数字那一行），按这一段的右端减去标记宽度推出标记的左边缘，裁出的那一块盖住数字；裁的那一块左边贴着菜单按钮时去掉按钮边缘读成的「1」「3.」前缀；数字的位置上没读成轮次的文字要像读错的两位数（带数字，或只有一两个形近的字母、一个形近的汉字）才换大小重读，大厅的通行证名、房间的「返回」「MODE」不重读。
  - `RoundReadTest`：`read_round_info` 的先后——第一遍靠标记读到的数在标记左边那一块只认字核对一次（交回那一块）：读出另一个数时按读不出处理，读不出、两种大小不一致、读出同一个数时照用，标记不在数字那一行时不核对；看见标记、数字没读出来时先裁小块只认字，读不出按读不出处理、不换大小重读；什么都没读到时只读一遍；没看见标记、读成「t0」时放大、缩小重读，重读时才看见标记照样裁小块；`retry=False` 时第一遍没看见标记、读成「M7」也只认一遍、不采用第一遍按位置读到的数，看见标记的那一路照旧（`test_retry_false_reads_only_once`）；只在真的跳过重读时数一次 `round_no_retry`，第一遍什么都不像、`retry` 为真时不数（`test_no_retry_counted_only_when_it_skips`）；`read_hud` 照它填 `round_mark`、`round_crop`，并把 `retry` 原样传下去（`test_hud_carries_mark`）。
  - `TitleLanguageTest`：三选一的标题简体、繁体两种写法都认，繁体标题下三张候选的繁体名交回简体名，`info["foreign"]` 为假，`ChipSelect.hant` 按标题更像哪一种写（繁体为真、简体为假）；其他语言（例如日文）的标题照旧判成其他语言的三选一。
  - `OutOfGameOrderTest`：几种画面的字样同时出现时，按 `screens.PARSERS` 的先后认第一个认得出的（主菜单排最后）；识别结果里文字的先后不影响认哪一种。
  - `TemplateCacheThreadTest`：一个线程重建模板和沿用缓存（`reload`、清空各张表）时，另一个线程正在查、存 `stars` 的三张表（`_photos`、`_faces`、`_still`）不会抛 `KeyError`，也总能拿到自己建的那一份；慢的读图片、缩放模板不在锁里做。新增的两个客户端同时识别时两路读写同一份缓存就靠它。
- **检证对象**：`advisor/vision/io.py`（`result_lines`、`ocr`）、`advisor/vision/chip_select.py`（`read_chip_select`、`_reread_count`、`_title_score`、`Line`、`TITLE_WIDTH_U`、`ocr_scale`）、`advisor/vision/hud.py`（`read_round`、`read_round_info`、`read_round_crop`、`read_hud`、`_is_mark`、`_round_mark`、`_mark_left`、`_mark_crop`、`_crop_digits`、`_round_like`）、`advisor/vision/anchors.py`（`TITLE_TEXTS`、`ROUND_MARK`、`ROUND_TEXT`、`ROUND_MARK_W_HAN`、`ROUND_MARK_W_LATIN`）、`advisor/vision/screens.py`（`parse_out_of_game`、`PARSERS`、`OutOfGame`）、`advisor/vision/templates.py`（`VisionAssets.lock`、`reload`、`reset_hints`）、`advisor/vision/stars.py`（`_hero_photos`、`_face_templates`、`_still_faces` 的锁）。
- **什么时候要跑**：改了 `io.py` 的结果转换或按范围识别、`chip_select` 的标题或刷新次数重读、`hud.py` 读轮次的规则、`screens.py` 的解析器先后 `PARSERS` 或各解析器，或改了 `templates.py`／`stars.py` 的缓存和锁。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_vision_shared`（上次没有记录；识别引擎一律换成假的，不加载模型）。
- **需要什么**：没有特别要求（不需要识别样本截图）。
- **输出怎么看**：同总则（识别引擎换成假的，没有特殊的跳过原因或临时文件）。
- **出错时一般是哪里不对**：
  - `ResultLinesTest` / `OcrRangeTest` 失败 → `io.result_lines` 的坐标换算或 `io.ocr` 的范围夹取改了（夹取后算出空块会交给引擎报错）；`test_det_side_restored` 失败 → `io.ocr` 改了找字设置却没还原（之后所有识别都按读进度条的设置找字，别处读漏）；`test_missing_det_settings` 失败 → 引擎缺这两个设置时 `io.ocr` 报错了。
  - `RerollRereadTest` 失败 → `chip_select` 的刷新次数读取或 `_reread_count` 的放大重读/坐标回换算改了。
  - `RoundMarkTest` 失败 → `hud._is_mark` 认标记的规则或 `anchors.ROUND_MARK`、`ROUND_TEXT` 改了（繁体标记只在数字那一行认，简体的「轮」照旧在整个轮次框里认）；或 `_round_mark` / `_mark_left` / `_mark_crop` 推标记位置、裁数字那一块的算法，`_crop_digits` 去前缀、`_round_like` 判「像读错的两位数」的规则改了。
  - `RoundReadTest` 失败 → `read_round_info` 的先后改了（第一遍靠标记读到的数没核对、核对的规矩改了，看见标记却读不出时又换大小重读了，或该重读时没重读；`retry=False` 时还重读了两遍），或 `read_hud` 没把 `round_mark`、`round_crop` 填上、没把 `retry` 传下去。
  - `TitleLanguageTest` 失败 → `anchors.TITLE_TEXTS` 里的标题写法、`chip_select._title_score` 的取法，或判断简繁的 `_title_hant` 改了。
  - `OutOfGameOrderTest` 失败 → `screens.PARSERS` 里解析器的先后被调换，或某个解析器的匹配条件放宽/收紧（结算页、匹配成功页必须排在房间、组队房间、奖励页之前，主菜单排在最后）。
  - `TemplateCacheThreadTest` 失败 → 缓存的锁没包住查和存、锁里做了慢的读图和缩模板，或存进去以后又回头取表里的（重建缓存时抛 `KeyError`）。

## test_screens.py：核对对局外画面（结算页、「匹配成功」页、房间、奖励页、主菜单）的识别——整幅识别和下方区域识别都和 `samples/labels.json` 的 screen 标注一致，反例画面不误认，解析规则另用构造的文字结果单测。

- **测试观点**（按测试类）：`LabelsTest`（screen 标注本身字段齐全、取值合法、和 scene 对得上、每种画面都有样本，不读图）；`SampleScreensTest`（原图和缩小版都读了；整幅识别和标注完全相同、认出这些画面时不找加载画面的地图名；下方区域结算页、主菜单读全、其余读不出可以、读到不能错；主菜单的原图和缩小版、整幅识别和下方区域都要读成主菜单；组队房间读出勾选的难度；反例两种识别都读不出、选角界面照常判成选角界面；`is_select_screen` 和 `read_pregame` 各只做一次文字识别）；`ParseTest`（构造文字结果核对解析规则：结算页要两项对上、地图名和 «（合作挑战）» 分两段时的取法、「匹配成功」页两处都要有、房间下拉框展开时列表不算当前值、勾选框拿不准时读不出、奖励页要两句话都在、主菜单要开始按钮和底下一排按钮（至少 4 个）都在、`Pregame` 的新字段有默认值；几种画面的繁体字样、繁体地图名、繁体的选角称号、加载画面的繁体地图名、繁体的地图标记也都认得出，交回简体的内部名；繁体中文界面录像里读到的原样（标签后带全角冒号、难度名后带圆点、结算页底栏「07輪」、奖励页数字紧贴着字，以及「07輪」读成两段时）也照样认；奖励页「星币」那一格的 +N（`coins`）按标签找：繁体录像上星币在第一格、简体样本上帐号经验在前都读得对，旁边一格、下一行的 +N 不算，标签和数读成一段、全角加号、没有加号的也认，标签下面没有数、数在上面、离得太远、不是一两位数（`+1O`、多读出一位的 `+100`）时读不出）。标注里没写 `coins` 的不比这一项（别的画面上必须读不出），简体客户端那张奖励页样本另核对读出 45（`test_reward_coins`）。`SelectDetSideTest`：判选角界面那次识别（`is_select_screen`）找字时短边只放大到 `anchors.SELECT_DET_SIDE`（320，引擎默认 736），交给 `io.ocr` 的是这个设置；三张选角界面认作选角界面，主菜单、结算页认不作选角界面、认出的画面种类不变，都和默认设置判的一样（真的文字识别，每张两种设置各读一次）。
- **检证对象**：`advisor/vision/screens.py` 的 `parse_out_of_game`、`OutOfGame`、`KINDS`，以及各解析函数 `_result_bar`、`_match_found`、`_custom_room`、`_team_room`、`_checked`、`_reward`、`_reward_coins`、`_main_menu`；`advisor/vision/hud.py` 的 `read_pregame`（`Pregame`）和 `is_select_screen`；`advisor/vision/anchors.py` 的 `DIFFICULTY_TEXTS`（难度名的简繁写法）、`MAIN_MENU_START`、`MAIN_MENU_BUTTONS`、`MAIN_MENU_MIN_BUTTONS` 和对局外画面的锚点。
- **什么时候要跑**：改了 `advisor/vision/screens.py`，改了 `hud.py` 里 `read_pregame`/`is_select_screen`，改了 `advisor/vision/anchors.py` 里对局外画面的锚点，或改了 `samples/labels.json` 的 screen 标注、画面种类。全部样本的反例扫描在 `tests/test_vision_matrix.py`（慢测试）里。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_screens`（上次约 0.01 秒；是识别模块；它从 `test_vision_matrix` 借 `HAVE_SAMPLES`、`load`、`shrink`、`WORKERS`、`SCREEN_GREYED`）。识别在 `setUpClass` 里分给几个子进程一次读完。
- **需要什么**：`samples/` 和 `samples/labels.json`（标了 screen 的样本，以及 `NEGATIVES` 里列的反例样本）；缺了跳过。
- **输出怎么看**：跳过原因写明缺 `labels.json`；识别在子进程里跑，某项出错时 `self.fail` 会带上子进程的调用栈；`ParseTest` 不读样本、很快。不写盘。
- **出错时一般是哪里不对**：
  - `LabelsTest.test_screen_labels` 失败 → 标注文件本身写错（缺字段、取值不合法、和 scene 对不上、某种画面没有样本）；这是标注的问题，不是程序。
  - `SampleScreensTest.test_full_reading` 失败 → 整幅识别的解析改坏了（`screens.py` 的解析器或锚点阈值），或 `read_pregame` 把地图名和画面种类拼错。
  - `test_bottom_reading` 失败 → `is_select_screen` 的下方区域识别，或结算页、主菜单的解析改坏。
  - `test_main_menu_reading` 失败 → 主菜单的解析（`_main_menu`）或 `anchors.MAIN_MENU_*` 的字样、个数阈值改了，或下方区域没盖住开始按钮和底下一排按钮。
  - `test_team_room_difficulty` 失败 → 组队房间勾选框的识别改坏（`_team_room`、`_checked`，以及 `PINK_*`、`GREY_*` 阈值）。
  - `test_negatives` 失败 → 反例画面被误认成对局外画面（解析太宽），或选角界面没判成选角界面。
  - `test_one_ocr_call` 失败 → `is_select_screen`／`read_pregame` 增加了文字识别次数。
  - `test_reward_coins`、`ParseTest.test_reward_coins_*` 失败 → 奖励页按标签找 +N 的规矩改了（`COIN_LABELS`、`COIN_BELOW`、`COIN_SIDE`、`_GAIN`）；胜负按它定（`advisor/session/offgame.py` 的 `outcome`），读错了会把通关、失败记反。
  - `SelectDetSideTest` 失败 → 判选角界面那次识别的找字设置（`anchors.SELECT_DET_SIDE`、`is_select_screen` 给 `io.ocr` 的 `det_side`）改了，或在新设置下有画面判得和默认设置不同了：先看是哪一张、两种设置各读出什么。
  - `ParseTest.*` 失败 → 解析规则按构造结果核对不上；改了规则时把断言的期望一起改。

## test_victory.py：通关画面「VICTORY」和打输横幅「GAME OVER」的识别（`advisor/vision/victory.py`）和识别流程里接它的那一处

- **测试观点**（按测试类）：
  - `GlanceTest`：每帧的颜色预筛——整屏珊瑚红是底色，中间一带白色多了才算大字可能出来了；白色在那一带以外不算；灰底、只有一小块红、红的偏暗（像压暗的画面）都不算；带透明通道的照常看，灰度图、空图不像；识别流程交的 `Frame` 也认。蓝色斜带要占比够、长边短边够、斜度在范围里才算；横的整屏蓝（斜度 0）、横着的带子、太长太窄的、占比不够的都不算；红底上没有蓝带。
  - `ReadTest`：认字——珊瑚红底上画的 VICTORY 读得到，只有底色、只有别的字时读不到；读出的字只看字母、不分大小写，容许最后的 Y 读错，读不全（「VICTO」）、分成两段、别的词不算。
  - `ReadGameOverTest`：GAME OVER 的认词——只认字母、不分大小写，`0` 当成 `O`，G 滑出画面读成「AME OVER」也算；VICTORY、只有 OVER、空文字不算；灰度图、空图直接为假。
  - `RecordedBandTest`：繁体中文客户端 3840x2160 录像里大字读得全的那一帧，缩到 1920x1080 以后只裁大字那一行（没有玩家名、聊天头像），贴回同样颜色的整屏底上：预筛两项都是真、认字读得到，整幅缩到 1280x720 也读得到。
  - `RecordedBannerTest`：打输那一帧的蓝色斜带形状是真、不是通关的红底，转正以后读得到 GAME OVER，缩到 1280x720、1000x563 也读得到；通关那一帧的斜带形状也是真（颜色和形状几乎一样），但读不出 GAME OVER。
  - `SampleNegativesTest`：结算页（入场、统计）、奖励页、加载画面、战斗画面都不像通关画面（珊瑚红占比远低于 `CORAL_SHARE`），也没有蓝色斜带。
  - `PipelineTest`：识别流程的 `FrameProcessor._check_victory`（预筛、认字换成假的，Session 是真的）：这一局还在进行时才看；像底色时紧接着取下一帧（`pending`），白色多时认字，读到了交给 `Session.on_victory` 记下时刻和一条 `read:victory`、不再急着取下一帧；蓝色斜带形状对了就认字，读到了交给 `Session.on_game_over_banner`、记一条 `read:game_over_banner`，读不到接着取下一帧；同一幅画面（`settle` 接着处理，时刻相同）不再看、不再认字、不再要下一帧，`settle` 在只有底色的通关画面上处理两遍就停（不连着处理 `SETTLE_MAX` 遍）；同一种底色连着超过 `RUN_S` 秒就不再取帧、不再认字，底色消失再出现、从红底换成蓝带时重新算；本局已结束、没有对局状态时不看。
  - `ProcessTest`：整个 `process` 接着这一处——造的通关画面（真的预筛和认字）一帧就记下；造的蓝色斜带配上假的认字也一帧记下。
- **检证对象**：`advisor/vision/victory.py` 的 `glance`、`Glance`、`NOTHING`、`read`、`read_game_over` 和各项阈值（`CORAL_*`、`WHITE_*`、`BAND`、`READ_WIDTH`、`WORD`、`BANNER_*`、`TURN_DEG`、`GAME_OVER_WORD`、`RUN_S`）；`advisor/vision/pipeline.py` 的 `_check_victory`、`_victory_looked`、`_finish_kind`、`_finish_since` 和 `process` 里调它的那一行；`advisor/session/core.py` 的 `on_victory`、`on_game_over_banner`。
- **什么时候要跑**：改了 `advisor/vision/victory.py`、`pipeline.py` 里接通关画面或横幅的那一处，或改了 `Session.on_victory`、`Session.on_game_over_banner`；胜负怎么定另跑 test_game_outcome。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_victory`（上次约 10 秒；会真的建文字识别引擎）。
- **需要什么**：`RecordedBandTest` 要 `samples/pages/20261105_010644_f051294_victory_band_1920x1080.png`（录像裁图，不随仓库发布），`RecordedBannerTest` 要 `samples/pages/20261106_235502_f041328_game_over_banner_1920x1080.png` 和 `samples/pages/20261105_010644_f051195_victory_banner_1920x1080.png`，`SampleNegativesTest` 要 `NEGATIVES` 里列的几张样本截图，缺了各自跳过；其余用造的画面。
- **输出怎么看**：同总则；跳过原因写明缺哪张。不写盘。
- **出错时一般是哪里不对**：
  - `GlanceTest`、`SampleNegativesTest` 失败 → 颜色预筛的阈值改了（`CORAL_HUE`、`CORAL_SAT`、`CORAL_VAL`、`CORAL_SHARE`、`WHITE_*`、`BAND`）；放宽了会让别的画面每帧都去认字（每次约 0.3 秒），收紧了会漏掉通关画面。蓝色那几项（`BANNER_HUE`、`BANNER_SAT`、`BANNER_VAL`、`BANNER_SHARE`、`BANNER_LONG`、`BANNER_SHORT`、`BANNER_SLOPE`）放宽了会让别的蓝色画面也去认字，收紧了会漏掉打输的横幅。
  - `ReadTest`、`RecordedBandTest` 失败 → 认字的范围、放大的宽度或认词的规矩改了（`BAND`、`READ_WIDTH`、`WORD`），或文字识别引擎换了。
  - `ReadGameOverTest`、`RecordedBannerTest` 失败 → 转正的角度（`TURN_DEG`）、认字的范围或认词改了（`GAME_OVER_WORD`、`0` 换 `O` 的规矩），或文字识别引擎换了。
  - `PipelineTest` 失败 → `_check_victory` 的先后改了：什么时候看（对局状态、本局已结束）、什么时候要下一帧、同一幅画面看几遍（`_victory_looked`）、同一种底色连着多久就不再看（`_finish_kind`、`_finish_since`、`RUN_S`）。
  - `ProcessTest` 失败 → `process` 里不再接这一处，或前面的某一步在这种画面上提前返回了。

## test_stars.py：读玩家列表上自己的星数与队友角色，以及用户自存的角色立绘模板

- **测试观点**：
  - `OwnStarsTest`：在三选一画面上按头像读出自己那一行的星数（凛第 4 行 1 星）；同一张图缩到 1280 宽结果一致；自己那一行被三选一的标题带挡住时不猜、返回 `None`；战斗画面没有玩家列表也返回 `None`。
  - `QuickPathTest`：三选一上读星数只走快的那一路（`read_own_stars` 的 `quick`）：头像按平时的几种摇摆认得出时和全试的结果一样（凛、美咲两张三选一）；认不出时（「昏迷」）不取「轮到自己行动」那组伸缩模板、当读不到，全试的照旧按伸缩再试；同一帧上两种结果分开记（`diagnose` 的帧上缓存）。
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

## test_chip_select_fast.py：三选一的快读法——记下位置以后只裁几小块认字，读成的和完整读法逐项相同，对不上就退回

- **测试观点**（按测试类）：
  - `FastReadTest`（合成画面，完整读法读到的字、快读法各块认出的字、圆环颜色都是假的）：认出的字和完整读法一样时结果逐项一样，各块一次交给认字；全队三选一那一行每次都读（没了就跟着变成不是全队）；画面大小变了不认字就退回；标题没认出、把握低、少了几个字的退回，读漏一个字照用，简繁和记下时不同的退回；概率行没读到、只有两项、三项之和不是 100 的退回；名字没认出、把握低、只读到共同部分、是别的字的退回，两头多出来的字去掉以后照用（原文是去掉以后的）；去掉两头才对上的名字，圆环颜色和资料里的稀有度不同就退回（原样就对上的照完整读法给提示）；圆环颜色有看不出的退回；刷新按钮那一块没认出「刷新」、也不是按钮的黄色时次数为 0，有一大片按钮的黄色却没认出「刷新」时退回，认出「刷新」却读不出次数（放大重读也读不出）时退回；记位置的那一次没有刷新按钮也照样记下按钮那两块。
  - `FastNameTest`：名字那一块认出的字怎么对资料里的写法（`_fast_name`，例子是样本截图实际认成的）：原样（归一化以后）就对上的不改；两头不是汉字的去掉几个都行、汉字最多一个；认成「一」的「-」逐处换回去试（「一般」里的「一」不动）；两头各有汉字、中间读错、只读到共同部分的修不成；一样长的有两个筹码、整段最像的是另一个筹码时不认；认的写法和 `DB.match_chip` 认成相似度 1.0 的是同一套；资料里没有一个筹码名是另一个的一部分。
  - `LayoutTest`：各块裁在哪里（名字、概率行照读到的位置，刷新按钮和次数照窗口右边、全队那一行照标题推算，都在画面里）；完整读法没认成三选一、名字匹配度低、圆环颜色有看不出的、概率行没读到、三个名字不在一条线上、名字那一行不在停稳时的位置（卡片还在滑进来）时不记位置。
  - `NarrowLayoutTest`：4:3 窗口里第一栏的名字那一块只裁到左边玩家列表的右边为止，照常读成。
  - `FastSampleTest`（真的文字识别）：`SAME` 里五张（四种窗口大小；第一栏连着角标、没有刷新按钮那一块认出别的字、全队三选一、名字带多余的字和认成「一」的「-」）同一张上快读和完整读逐项相同；`CROSS` 两对同一窗口大小下拿 A 记的位置读 B 和 B 的完整读逐项相同（B 没有刷新按钮、选中了一个）；画面大小不同退回；`OTHER` 里同样大小、不是三选一的两张（对局画面、筹码面板）退回；位置整体偏了几十像素（`SHIFTS`）时退回，或读出和完整读法相同的名字、颜色、次数、概率、选中、全队。
- **检证对象**：`advisor/vision/chip_select.py` 的 `select_layout`、`SelectLayout`、`FastSpot`、`read_chip_select_fast`、`_fast_name`、`_exact_spellings`、`_fast_crop`、`_recognize_crops`、`_yellow_share` 和 `FAST_*` 常数，完整读法和快读法共用的后处理 `_parse_select`（`read_chip_select` 交回的 `info["lines"]`）；`advisor/vision/anchors.py` 的 `REROLL_FROM_RIGHT`、`REROLL_BELOW_TITLE_U`、`REROLL_COUNT_DY_U`、`TEAM_BANNER_ABOVE_U`、`PLAYER_LIST_RIGHT`。
- **什么时候要跑**：改了上面这几处，或三选一的后处理（`_parse_select` 里挑名字、看圆环、读次数、概率行、全队那一行），筹码名的写法和匹配（`advisor/data.py` 的 `_norm`、`DB.match_chip`、`chips.json` 里的名字、繁体写法、旧名），加了三选一样本，或游戏改了三选一界面的排版。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_chip_select_fast`（约 10 秒；`FastSampleTest` 用真的文字识别，是同时最多并行几个的识别模块；其余几类不建识别引擎）。
- **需要什么**：`FastSampleTest` 要 `samples/` 里 `SAME`、`CROSS`、`OTHER` 那 11 张截图，缺了跳过；其余不要样本。
- **输出怎么看**：子测试标出是哪张截图、哪种认出的字或偏了多少；不写盘。其余同总则。
- **出错时一般是哪里不对**：
  - `FastReadTest`、`LayoutTest` 失败 → 快读法的退回条件或各块的位置、大小（`FAST_*`、`anchors.REROLL_*`）改了，或 `_parse_select` 的挑法变了、快读法交给它的字和位置对不上了。
  - `FastNameTest` 失败 → 去掉两头的规矩（`FAST_TRIM_HAN`、`FAST_DASH_HAN`、`_fast_name`）或筹码名的归一化、对照表（`data._norm`、`DB.match_chip`、`_exact_spellings`）改了；`test_no_name_inside_another` 失败是资料里加了一个名字是另一个一部分的筹码，回头看去掉两头的规矩还够不够稳妥。
  - `FastSampleTest` 失败 → 先看子测试是哪张：`test_same_image`、`test_other_shot_same_size` 退回了，看交回的原因（名字那一块裁得不对、刷新按钮的位置变了、识别模型换了认法）；读出来却和完整读法不同是最要紧的，说明快读法会读错，先查那一项在两种读法里各读成什么。

## test_prefilter.py：核对筹码三选一的预筛——第一段（上半段有暗行）的两段式写法和「把整幅缩到 96x54」的写法逐张判定相同；第二段（标题带的位置和上下边）三选一一张不漏，上方也有黑带的反例拦下。

- **测试观点**（按测试类）：
  - `PrefilterTest`（第一段 `dark_band`）：`test_matches_full_resize`（每张样本 × 几种宽度，两段式和整幅缩放的写法判定相同）；`test_near_threshold_recomputed`（NEAR 这张在 1920 宽时取样占比落在阈值附近的余量里，两段式必须按整幅缩放的写法再算一次，判定和它一样是「不像」——这条同时守住「离阈值近一定要按整幅缩放的写法算」；整个预筛也判「不像」）；`test_select_screen_like`（三选一样本两段都判成「像」）；`test_matches_full_resize_on_plain_shapes`（全黑、全白、噪声、下方黑带这些合成画面上也相同；整个预筛不比第一段宽）。
  - `TitleBandSampleTest`（第二段 `title_band_visible`，实机截图）：`test_every_select_sample_passes`（`labels.json` 里每张三选一样本——各种比例、全队三选一、图标还没加载的出场动画、选中、刷新按钮不在的——原图、缩到 1920 宽、缩到一半（不低于最小窗口）都过整个预筛）；`test_dark_tops_blocked`（战斗、玩家详情弹窗、卡牌商店、压暗的对局画面上方也有黑带：原图和 1280 宽都过得了第一段、被第二段拦下）。
  - `TitleBandTightTest`（第二段，分数最靠近门槛的实机帧）：`test_tight_selects_pass`（`TIGHT_SELECTS`：金色筹码的光盖住一段标题带的出场、刷新动画，出场头一帧，上下有黑边的 16:10 窗口，原图和 1280 宽都过整个预筛）；`test_blast_blocked`（`BLAST`：开战转场的黑色爆炸图案，原图和 1280 宽都过得了第一段、被第二段拦下）。
  - `TitleBandSynthTest`（第二段，合成画面，不要样本）：16:9、16:10、21:9、4:3、竖长、4K 窗口里按界面缩放画的标题带都认得出；整条带子错开 20 个界面单位照样认得出；带子挪开 80 个界面单位、只有约 24 个界面单位高的不认；带子上面很暗（七成是纯黑）、下面是很暗的地图（三成多是纯黑）、金色筹码的光盖住三成多的带子时照样认得出；整个画面抬亮 `SELECT_BAND_BLACK` 以内照样认得出，再亮第二段不过、第一段还过；满屏全黑、带子连同上下一大片都是黑的不认；太小的画面不认。
- **检证对象**：`advisor/vision/chip_select.py` 的 `looks_like_overlay_screen`、`dark_band`、`PRE_SAMPLE_MARGIN`、`title_band_visible`、`SELECT_BAND_*`；辅助 `advisor/vision/pipeline.py` 的 `trim_borders`、`MIN_UI_SCALE`。
- **什么时候要跑**：改了预筛的任何一段（暗行范围或阈值、取样步长、`PRE_SAMPLE_MARGIN`；标题带的位置、贴边看的范围、错开的范围、黑的门槛、`SELECT_BAND_CONTRAST`），改了去黑边（`trim_borders`）或界面缩放（`ui_scale`），加了三选一样本，或游戏改了三选一界面的排版。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_prefilter`（上次约 3 秒；不做文字识别）。
- **需要什么**：`PrefilterTest` 要 `samples/` 里 4 张截图（NEAR 是 `20261003_132306_card_give_al_skill_2560x1369.jpg`、SELECT 是三选一、BATTLE 是对局画面、PANEL 是面板）；`TitleBandSampleTest` 要 `labels.json`、其中的三选一样本和 `DARK_TOPS` 那 4 张反例（战斗、玩家详情弹窗、卡牌商店、压暗的对局画面）；`TitleBandTightTest` 要 `samples/pages/` 里 `TIGHT_SELECTS` 那 4 帧和 `BLAST`；缺了跳过。`TitleBandSynthTest` 不要样本。
- **输出怎么看**：跳过时写明缺哪张；子测试标出是哪张图、哪个变体或哪种窗口；不写盘。其余同总则。
- **出错时一般是哪里不对**：
  - `test_matches_full_resize` 失败 → 第一段的两段式和整幅缩放的写法对不上了（取样或余量判断改坏）。
  - `test_near_threshold_recomputed` 失败 → `PRE_SAMPLE_MARGIN` 改小到不覆盖这张样本，或没按整幅缩放的写法重算；这条断言本身也说明该样本仍落在余量里，改了样本就一并改。
  - `test_select_screen_like` 失败 → 暗行范围或阈值、或标题带的位置改坏，把三选一漏掉。
  - `test_matches_full_resize_on_plain_shapes` 失败 → 极端画面（全黑、全白、噪声）上第一段的判定改了，或整个预筛放过了第一段拦下的画面。
  - `test_every_select_sample_passes` 失败 → 第二段把三选一漏掉了，看子测试是哪张图、哪个变体：先看那张图的标题带是不是还在中线以上 400~300 个界面单位（游戏改了排版就重新量，跟着改 `SELECT_BAND_EDGES`）；再看贴边的几行是不是被光、表情盖住太多（`SELECT_BAND_INSIDE`、`SELECT_BAND_CONTRAST`）。
  - `test_dark_tops_blocked` 失败 → 第二段放宽了（门槛、错开的范围、黑的门槛），上方有黑带的画面又会每帧做一次三选一的文字识别；是「第一段」那条断言失败的，说明这张样本已经过不了第一段，不再是这里要的反例，换一张。
  - `TitleBandSynthTest` 失败 → 对应那一条的判定改了：带子的位置、错开的范围、带子外两段取平均、贴边看的范围。

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
  - `PerfStatsTest`：分位数（最近秩法，`p50` 取第 `ceil(0.5n)` 个、`p95` 取第 `ceil(0.95n)` 个）、最大值、总和；带冒号的 `kind` 归到 `reads` 下（名字取冒号后面一段）；没有记录的 `kind` 不出现；`take_period` 取走后清零；`period_s` 是两段之间的时长；样本超过上限 `MAX_SAMPLES` 后只累加次数/总和/最大值、分位数按存下的那些算；处理器占用——第一次 `take_period` 只记基准没有 `cpu`，之后按两次快照的差算（本进程与两个线程折成一个核的百分比、整机 0~100）；某一项 Win32 调用失败时那一项不出现、不是 Windows 时 `cpu` 整个不出现；取时钟、记一笔、取统计全都出错时不往外抛；出错只丢那一段（记一笔出错只丢那一笔，汇总出错只丢这一段、下一段照常）；几个线程同时记不丢不抛。只数次数的 `count`：按名字累加，取走时放进 `counts`（按名字排好），不混进 `reads`，第一次记数也算这一段的开头，这一段没有时不出现、取走后清零（`test_counts`）。
  - `TimedEngineTest`：`chip_select._TimedEngine` 给识别引擎套一层计时，每次调用记一笔 `ocr`；属性读写、`update_params` 转给里面的引擎；识别结果和不套外壳时相同；`recognize_only` 用完照旧还原 `use_det`/`use_cls`/`use_rec`。
  - `UnbiasedClockTest`：`perfstats.unbiased_clock` 在 Windows 上按 `QueryUnbiasedInterruptTime`（100 纳秒为单位）折成秒；没有这个函数或取不到时退回 `time.monotonic`；真实时钟在 Windows 上确实在走、走得和单调时钟一样快。
- **检证对象**：`advisor/perfstats.py`（`record`、`count`、`take_period`、`unbiased_clock`、`_nearest_rank`、`_percent`、`MAX_SAMPLES`、模块级的 `_clock`/`_kernel32`/`_counts`/`_tallies`/`_since`/`_cpu_base`）、`advisor/vision/chip_select.py`（`_TimedEngine`、`recognize_only`、`_engine`）。
- **什么时候要跑**：改了 `advisor/perfstats.py` 的分位数、分组、清零、样本上限、处理器占用计算或时钟；改了 `chip_select._TimedEngine`（计时外壳）或 `recognize_only` 的开关还原。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_perfstats`（上次约 0.1 秒；不真的加载识别模型，引擎换成假的）。
- **需要什么**：没有特别要求（时钟和 Win32 调用都换成假的）；`UnbiasedClockTest.test_real_clock_runs` 只在 Windows 上跑。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `test_quantiles_max_sum` / `test_odd_quantile` / `test_sample_cap` 失败 → 分位数算法（`_nearest_rank`）或样本上限 `MAX_SAMPLES` 处理改了。
  - `test_reads_grouped` / `test_missing_kinds_absent` 失败 → `take_period` 里对带冒号的 `kind` 分组或空 `kind` 的处理改了。
  - `test_take_clears` / `test_period_seconds` 失败 → 取走后清零或 `period_s` 的计算改了。
  - `test_counts` 失败 → `count` 的累加、`take_period` 里 `counts` 的组成或清零改了。
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

## test_ocr_lock.py：两个识别线程共用一个文字识别引擎

- **测试观点**
  - `EngineQueueTest.test_two_readers_queue`：两个线程同时调用 `chip_select.recognize_only`，同一时刻只有一个在引擎里，另一个在 `_ENGINE_LOCK` 上等；两个都读得完，同时在读的最多一个。
  - `EngineQueueTest.test_io_ocr_queue`：每帧读字走的 `io.ocr` 也走这把锁，两路同时读同样一个在引擎里、一个在等。
  - `EngineQueueTest.test_lowend_waits_for_reading`：一路正在读字时另一路调 `use_lowend_threads`，换线程数排在读完以后，不会把正在用的引擎丢掉、不影响这一帧；换完按新线程数丢掉缓存的引擎。
- **检证对象**：`advisor/vision/chip_select.py` 的 `_ENGINE_LOCK`、`recognize_only`、`_ocr_lines`、`_reread_count`、`use_lowend_threads`、`lowend_threads`、`_engine`；`advisor/vision/io.py` 的 `ocr`。
- **什么时候要跑**：改了文字识别引擎的共用方式、`_ENGINE_LOCK` 包住的范围、`use_lowend_threads` 换线程数的时机，或 `io.ocr` 的调用方式，都要跑。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_ocr_lock`（上次约 0.1 秒）。
- **需要什么**：识别引擎换成假的（不建真的、不加载模型）；两个线程用 `Barrier` 同时开始，等锁时最多停 0.05 秒，不真的等很久。
- **输出怎么看**：同总则（没有特别的跳过原因或临时文件）。
- **出错时一般是哪里不对**
  - `test_two_readers_queue`／`test_io_ocr_queue` 失败 → `_ENGINE_LOCK` 没包住调用引擎的那一段，或 `recognize_only`、`io.ocr` 漏了锁（两路会同时用同一个引擎，开关互相覆盖）。
  - `test_lowend_waits_for_reading` 失败 → `use_lowend_threads` 没在锁里换，或换的时候把正在读字用的引擎丢掉了。
  - `test_use_ocr_threads_waits_for_reading` 失败 → `use_ocr_threads` 没在锁里换，或换的时候把正在读字用的引擎丢掉了。

## test_ocr_check.py：文字识别线程数的自检

- **测试观点**
  - `CandidatesTest`：候选线程数按逻辑核心数取（32→(2, 4, 8)，16→(2, 4, 8)，12→(2, 4)，8→(2, 4)，4→(2, 4)，3→(2)，2→(2)，1→()）；默认线程数写成模块常量，不读 `chip_select.OCR_THREADS` 的当前值。
  - `PickTest`：先取最快的，再从少的线程数往上看，第一个不超过「最快 × 1.15」的就是结果；刚好等于 1.15 倍的算差不多。
  - `RunTest.test_rotation`：轮流量，每轮换一个先后（2,4,8 / 4,8,2 / 8,2,4……）。
  - `RunTest.test_warmup_not_timed`：每个引擎先读一次热身，不计时；时钟只用在量的时候。
  - `RunTest.test_lock_held_and_wait_not_counted`：每次读都拿着 `_ENGINE_LOCK`，等锁的时间不算进读数（拿到锁才开始计时）。
  - `RunTest.test_stop_before_build`、`test_stop_before_warmup`、`test_stop_between_reads`：建引擎、热身、量的各阶段叫停都停得下，返回已经量完的整轮数。
  - `RunTest.test_budget_slow`、`test_budget_enough_rounds`：量到 `BUDGET_S` 时，够 `MIN_ROUNDS` 整轮就下结论，不够就报太慢。
  - `RunTest.test_noisy`：同一个线程数最慢和最快差到 `NOISY_RATIO` 倍时算量不准。
  - `RunTest.test_candidates_too_few`、`test_same_image`：候选不足两个什么都不量；各引擎读的是同一张合成图。
  - `RunTest.test_build_error_drops_engines`：建引擎出错时往外抛，已经建好的引擎丢掉、能被回收。
  - `ProgressTest`：量的时候估还要多久：开始时按候选数和份数估（`first_guess`，候选不足时是 0）；`_estimate_left` 按每份花的时间估，过了预算最多是余量加一轮；`run` 的 `progress` 每做完一份交一次，量完时是 0；读得很慢时估的不超过预算；叫停以后不再交。
  - `SampleImageTest`：合成图是 1568×736 的 BGR 图；有模型文件时真引擎读得出 8 段以上（没有模型时跳过）。
  - `RenderCheckTest`：打包自检的「识别合成图」（`diagnostics.ocr_render_check`）读的是这张图，读到的段数不到 `MIN_OCR_LINES` 时报错（打包时要求这一项通过，见 `test_release.py` 的 `test_smoke_problems_ocr_render`）。
  - `StartupTest`：有效记录（核心数相同、线程数在候选里）只换线程数、不再量；核心数变了重量、次数从 0 算；线程数不在候选里当没有；次数用满不再量；候选不足不量。
  - `ReadyTest`：watcher 没了、暂停了、低配模式开着、游戏不在前台、在对局里都不开始；两开时另一张在对局里也不开始，正在量的也停下；对局已结束可以量；中途退出、连着一阵子看不到对局读数（`Session.game_quiet`）的不算在对局里，可以量（`test_quiet_game_can_run`）；正在量时不重复开始。
  - `ImportTest`：导入悬浮窗不带进识别模块和 OpenCV（皮肤预览的子进程也导入悬浮窗，另起一个 Python 进程看）。
  - `ApplyTest`：量出来先换线程数，再写设置（`cores`、`threads`、`ms`、`rounds`）、存盘、记一条 `ocr_check` 识别记录；`STOPPED` 不算次数、不写设置；量完写设置时出错，这次运行开始满 `MAX_RUNS` 次也不再看；`NOISY`、`SLOW`、出错各加一次 `tries` 并写原因；核心数变了次数从 0 算；后台线程不调 `later`。
  - `ShutdownTest`：没在量时马上返回真；正在量时等给的时间，到点返回假。
  - `SettingsTest`：`UiSettings.ocr_check` 好的原样留，不是字典、键不认识、类型不对的清掉，旧设置文件没有这一项时按空。
  - `RecordedGainTest`：自检记录上这台电脑比出来的只用大核的收益（`recorded_gain`，设置窗口「只用大核」一项按它）：核心数相同、有比值时取它；没有记录、换了电脑、没比过、值不是有限的数时为 None；存盘读回照样取得到。
  - `NoticeTest`：提示栏上写什么（`OcrCheck.notice`）：一开始量就写上，定时器每秒刷新一次；剩余时间按估的减去过的时间，估的来回抖时显示的不往上跳，到点是 0；后台线程只记数，不刷新、不排定时器；量完下一次定时器换成结果、不再每秒刷新、到点收起；刷新到一半才量完的，下一次换；按读一次的毫秒分三档（边上的值归快的那一档）；较差、太慢的不自己收起，留到玩家关掉；量不准、出错的自己收起；「下次启动再测」跟着次数；中途停下去掉提示、之后重新写上；上一次的定时器没走到又开始一次时只留一个；退出以后不再刷新；有效记录什么都不写。
  - `RowTest`：提示行（`notices.ocr_check_row`）：量的时候的文字、绿字、没有按钮，还剩 1 秒以内写「马上完成」；各种结果的级别、⚠、「关闭本条提示」；没查到原因时不写原因；秒数不写 0.0；排在图片下载后面、「免费软件」声明前面，不让边框变色；第二张不显示。
  - `ReasonTextTest`：结果差时写的原因（`wording.ocr_reason_text`）：句子和查原因那边对得上（`diagnose` 能给的每一条都写得出，键、数都对得上；每种修法都有「是」的悬停说明、都能记进「不再问」）；只能自己做的写到建议做法、接「下次启动时再测一次」；能改的写到问法为止；两条的连法、「另外」后面的空格、「也可以」、问法写明哪一件；没插电量到限速、分不开游戏占用的写法；都没查到写「可能的原因」；只用大核快多少的写法（百分比、倍数）；哪些带「是」「否」、哪些只有关闭；一般、查到排在小核上时结论照「一般」写、接原因、带「是」「否」；点了「是」以后的三句；忽快忽慢查到原因时的结论；繁体界面的叫法（筆電、省電模式、電源與電池、最佳電源效率、控制台）。
  - `OverlayRowTest`：接到悬浮窗上（离屏）：绿字的样式名 `notice_ok`、悬停说明、一行显示时也看得到；点「关闭本条提示」收起；「是」「否」排在文字下面、一样宽，只有关闭时照旧在文字右边；点「是」真走 `Overlay.set_big_cores`（设上、存进设置、重新量；设不上不存）、打开低配模式；点「否」记进设置、提示收起；对局进行中结果先不显示，量的时候照旧显示；走一遍真的 `OcrCheck` 看到结果；七套皮肤的样式表都有这一条、用自己的 `OK_COLOR`。
- **检证对象**：`advisor/vision/ocr_check.py` 的 `candidates`、`sample_image`、`pick`、`run`、`first_guess`、`_estimate_left`、`Result`、`CANDIDATES`、`ROUNDS`、`MIN_ROUNDS`、`BUDGET_S`、`REST_S`、`CLOSE_ENOUGH`、`NOISY_RATIO`、`WARMUP_SHARES`、`GUESS_SHARE_S`、`STOPPED`、`NOISY`、`SLOW`；`advisor/ui/overlay_parts/ocr_check.py` 的 `OcrCheck`、`OcrNote`、`recorded_gain`；`advisor/ui/overlay_parts/notices.py` 的 `ocr_check_row`、`STACKED_NOTICES`、`EQUAL_WIDTH_NOTICES`；`advisor/ui/overlay_parts/wording.py` 的 `OCR_CHECK_*`、`OCR_REASON_*`、`ocr_reason_text`、`ocr_gain_text`；`advisor/ui/overlay.py` 的 `set_big_cores`；`advisor/ui/overlay_parts/app.py` 的 `UiSettings.ocr_check`、`_clean_ocr_check`；`advisor/diagnostics.py` 的 `ocr_render_check`、`MIN_OCR_LINES`。
- **什么时候要跑**：改了线程数自检的做法、候选数、挑法、时间预算、条件判断、设置里 `ocr_check` 的读写；改了提示栏上的句子、分档、留多久；改了 `chip_select.new_engine` 或 `use_ocr_threads` 也跑；改了合成图、`ocr_render_check` 时另跑 `test_release`。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_ocr_check`（上次约 3 秒，有模型时读合成图那一条另加一点时间）。
- **需要什么**：不用真引擎的部分换成假引擎、假时钟、假 sleep；提示栏接到悬浮窗上的部分离屏建窗口（不接后台识别）；读合成图那一条要识别模型文件，没有时跳过（同 test_rapidocr_lite）。
- **输出怎么看**：同总则；没有模型时 `SampleImageTest.test_real_engine_reads_it` 一条跳过，其余照跑。
- **出错时一般是哪里不对**
  - `test_rotation` 失败 → 每轮的先后没换，或轮换的起点算错。
  - `test_warmup_not_timed` 失败 → 热身也计了时，或计时开始得晚了。
  - `test_lock_held_and_wait_not_counted` 失败 → 读字没拿 `_ENGINE_LOCK`，或计时在等锁之前就开始了。
  - `test_budget_*` 失败 → 预算从建引擎之前算起（热身也算进去），或超过预算时的两种结果接反了。
  - `test_noisy` 失败 → 忽快忽慢的判断用了中位数而不是每一次的读数。
  - `ApplyTest` 失败 → 量出来没先换线程数、设置里的键或类型不对、`STOPPED` 被计了次数。
  - `ProgressTest` 失败 → 每份活的算法和 `run` 的步骤对不上（多加、少加了一步，或者 `WARMUP_SHARES` 改了没有跟上），或 `_estimate_left` 的预算上限算错。
  - `NoticeTest` 失败 → 定时器（`_tick`、`dismiss`）排漏了或多排了，分档的边界（`FAIR_MS`、`POOR_MS`）改了，或者后台线程碰了 `later`、`refresh`（`QTimer` 只能在界面线程用）。
  - `RowTest`、`OverlayRowTest` 失败 → 句子、级别、`NOTICE_AREA`、`NoticeModel.rows` 里的先后，或样式表的 `#notice_ok` 改了；有意改句子时这里的预期、使用说明、术语表一起改。
  - `ReasonTextTest` 失败 → 查原因那边加了原因或修法，句子（`OCR_REASON_*`、`OCR_CHECK_YES_TIP`）或 `DECLINABLE` 没跟上；句子里的占位符和 `Reason.values` 的键对不上；繁体的叫法在 `data/zh_hant_extra.toml`。

## test_ocr_reason.py：结果差时查原因、比只用大核的收益、OcrCheck 点「是」/「否」

- **测试观点**
  - `DiagnoseTest`：表里每一行成立和不成立、读不到的项不算、顺序；被降频同时有电源那几条时不单列 `throttled`，断电那条带上读到的百分比；`unknown` 只在一项都没查到时出现；大小核的比值取一位小数。
  - `ShownTest`：先挑一条只能自己做的，再配一条能替玩家改的；没有能替玩家改的时最多写两条只能自己做的；玩家说过不要的（`declined`）不再问。
  - `SysInfoTest`：大小核按效率等级分组、只取一个处理器组、只有一种等级时为 None；`use_big_cores` 传大核的 Id、关的时候传 `None, 0`，设不上只记一次错误记录；`affinity_counts` 数掩码里的 1、跨处理器组（掩码 0）时为 None；`reset_affinity` 传系统掩码；`reset_priority` 传 NORMAL；`lacks_avx2` 的三种情况（有 AVX2、有 AVX 没 AVX2、两个编号都不认得时按系统版本）。系统调用换成假的，不真的改测试进程的核限制和优先级。
  - `ProbeTest`：假的处理器时间和 PDH：整机、本进程、游戏各占整机的百分比；打不开游戏进程时游戏占用为 None，两开时有一个打不开也一样；没有处理器时间时都为 None；`finish` 重复调用返回同一个结果；加不上计数器、第一次采样失败时查询当场关掉，`finish` 里出错、终点读不到游戏时句柄和查询照样关掉；`read_state`、`readings` 的形状和键。
  - `BigCoreGainTest`：比值对；每轮换先后；中途抛异常、叫停、bind 失败时都放开限制；读得慢时过了预算、又有两轮就收尾；每种不足两次时返回 None。
  - `FindTest`：有大小核时比只用大核的收益，比到了给「排到小核」；没有大小核、忽快忽慢、设置已开着、玩家说过不要、省电、被限核时不比；比的时候出错记错误记录、别的原因照写；什么都没查到时较差、太慢写「可能的原因」，忽快忽慢不写；结果一般（`cores_only`）时只留「排到小核」、别的原因不写、不补「可能的原因」（正好到分界也算），读数照旧整份返回，什么时候不比照较差的。
  - `OcrCheckReasonTest`：结果差时查原因并记下键和读数；原因是一时的记 `retest`，次数跨启动累计（和太慢、量不准交替出现也一共只量 `MAX_TRIES` 次）；这次没量出来时换回默认线程数，中途停下的不换；比的时候玩家打开了只用大核，比完放回去；良好不查，一般在没有大小核的电脑上不查；一般、有大小核时查、带 `cores_only`（较差、太慢不带），比的时候照旧报剩余时间；一般、比出来在小核上时记下原因和读数、问「是」「否」、不自己收起、不记 `retest`，不在小核上时比值照存、照旧收起；再量时没比（良好不查、查了没比）留着这台电脑上一次比出来的比值、标 `gain_kept`，识别记录照记这次的读数，这次比了换成这次的，核心数不同的旧比值不留；忽快忽慢带原因时不自己收起、不带原因时照旧收起；查原因出错、Probe 建不起来、查原因的模块导入不了时结果照常；不管这一次怎么结束读数都收尾。
  - `AcceptTest`：四种修法（只用大核、省电、优先级、能用的核）成、败各一遍；改好后重新量（次数清零、排 `RETEST_DELAY_MS`）；低配模式；点「否」写进记录、下一次查原因收到 `declined`、换了记录不留着；`startup` 的 `retest`（再量、次数满了不再量）。
  - `GamePidsTest`：`Overlay._game_pids` 把各张悬浮窗跟着的窗口换成进程号，窗口没了、还没找到窗口的不要。
  - `SettingsTest`：`UiSettings.big_cores` 的默认、类型不对按关、存盘读回；`_clean_ocr_check` 认得的 `retest`、`reasons`、`declined`、`readings` 留下，类型不对的丢掉；一条结果差的记录存盘读回还是原样。
- **检证对象**：`advisor/vision/ocr_reason.py` 的 `Reason`、`diagnose`、`shown`、`Probe`、`begin`、`read_state`、`readings`、`find`、各分界线；`advisor/sysinfo.py` 的 `big_core_split`、`use_big_cores`、`affinity_counts`、`reset_affinity`、`reset_priority`、`lacks_avx2`；`advisor/vision/ocr_check.py` 的 `big_core_gain`、`GAIN_*`；`advisor/ui/overlay_parts/ocr_check.py` 的 `OcrCheck`、`OcrNote.reasons`、`offer`、`accept`、`decline`；`advisor/ui/overlay.py` 的 `_game_pids`；`advisor/vision/capture.py` 的 `window_pid`；`advisor/ui/overlay_parts/app.py` 的 `UiSettings.big_cores`、`_clean_ocr_check`。
- **什么时候要跑**：改了查原因的判定、分界线（含 `sysinfo.BIG_CORE_GAIN_MIN`、`on_small_cores`）、读的项、Probe 的计数器；改了本进程设置的几个函数；改了 `big_core_gain`；改了结果差、结果一般时的状态流转（什么时候查、点「是」/「否」、重新量、`retest`）；改了自检记录的键。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_ocr_reason`（上次约 1 秒）。
- **需要什么**：系统调用、处理器时间、PDH 都换成假的；不建真引擎、不真的改本进程的核限制和优先级。真实机器上的游戏进程和大小核不在这里跑。
- **输出怎么看**：同总则，没有特别的跳过原因或临时文件。
- **出错时一般是哪里不对**
  - `DiagnoseTest` 失败 → 分界线（`THROTTLED`、`LIMITED`、`OTHERS_BUSY`、`ALL_BUSY`）改了，或降频和电源几条的合并写反了。
  - `ProbeTest` 失败 → 百分比的分母用了整机时间以外的数，或游戏进程读不到时没按 None。
  - `BigCoreGainTest` 失败 → 换了核的限制以后没在 `finally` 里放开，或轮换的先后、`GAIN_SETTLE_S` 用错了。
  - `AcceptTest` 失败 → 改好以后没有重新量（`_finished`、`_runs`），或没改成时也当成了改好。
  - `SysInfoTest` 失败 → 大小核的分组（效率等级高的算大核、按不同的 `(group, core)` 数核心）或掩码的读写不对。
