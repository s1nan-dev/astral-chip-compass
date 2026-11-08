# 测试指南 · 我的统计

这是[《测试指南》](../测试指南.md)第八节的一章：每个测试文件一节，每节的结构见那里第八节的开头。


这一章管「我的统计」窗口和窗口里的数字（`advisor/play_stats.py`、`advisor/ui/stats_window.py`）：局数、三选一次数、跟着首推选的比例、常用角色和筹码，以及没有推荐日志、统计出错时窗口写的那几句和最下面讲对局报告的那一句。

## test_play_stats.py：从推荐日志算数字——口径同 tools/calibrate.py

- **测试观点**（按测试类分）：
  - `FollowRateTest`：百分比四舍五入到整数；一次都没有时为 `None`（界面上写「还没有记录」）。
  - `ComputeTest`：同一个 `offer_id` 取最后一条三选一、实际选择也取最后一条；最后一条是按「查看筹码」面板改正的（`via == "panel"`）不算进一致率，之后又有正常的选择时重新算；补录的（`via == "supplement"`）照旧算；没记选择的不算；旧日志的「重选」按「刷新」算，选「刷新」算跟着首推、不算筹码；按筹码方案推荐的另算一行，不进默认推荐的局数、次数和一致率；最近 30 天按传入的 `now` 算（正好 30 天前那一刻算在最近 30 天里）；角色按局数排前 5 个（同一局里的几次三选一只算一局，同数按名字排）；每个角色选得最多的 3 个筹码（刷新、没记选择的不算，同数按名字排），只写前 5 个角色；坏记录（不是字典、缺 `offer_id`、局号写成空串或布尔值、时刻和 `state`/`advice` 写坏）跳过、不抛异常。
  - `CalibrateAgreementTest`：同一份日志（同一个三选一更新了几次、面板改正、补录、旧的「重选」、没记选择、跟首推不一样、按方案推荐这几种都有）写进测试沙箱的临时文件夹，交给 `tools/calibrate.py` 的汇总和 `compute`：局数、三选一次数、跟着首推选的次数两边一样，按方案推荐的那一次两边都另算。
- **检证对象**：`advisor/play_stats.py` 的 `compute`、`PlayStats`、`FollowRate`；`CalibrateAgreementTest` 另用到 `tools/calibrate.py` 的 `collect`、`report` 和 `advisor/logbook.py` 的 `iter_records`。
- **什么时候要跑**：改了 `advisor/play_stats.py`、`advisor/logbook.py` 里记录的样子或 `by_scheme`、`tools/calibrate.py` 的口径以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_play_stats`（上次约 1 秒）
- **需要什么**：不需要 Qt、不需要识别样本；记录都在测试里造，`CalibrateAgreementTest` 的日志写在测试沙箱的临时文件夹里。
- **输出怎么看**：同总则。失败时看断言里的次数和比率，多半是去重或哪一类记录算没算进去。
- **出错时一般是哪里不对**：
  - 一致率差一个 → 最后一个 `pick` 的 `via` 或「重选」的归一（`play_stats._follow`、`_option`）。
  - 局数、次数多出来 → `offer_id`、局号有没有按 `calibrate` 的 `if rec.get(...)` 排除空串和布尔值（`_usable`）。
  - 角色、筹码的先后不对 → 排序的次序（同数按名字）。
  - `CalibrateAgreementTest` 失败 → 「我的统计」和校准工具的口径有一边改了、另一边没跟上（`advisor/play_stats.py`、`tools/calibrate.py`）；只是 `calibrate.report` 的措辞改了时，改这条测试里认数字的那几个正则。

## test_stats_window.py：「我的统计」窗口——七套皮肤、几句特殊的话、后台线程

- **测试观点**（按用例）：
  - `test_all_skins_show_text`：七套皮肤各打开一次，开头一句、四项数字和最下面一句都在；排好版的正文不比显示的地方宽（字不被横向截掉）；正文第一行的字和后面的底色对比度不低于 4.5，按窗口画出来的样子量（「关于」把正文写在浅色卡片上的皮肤，正文要垫上卡片的底色）；窗口里没有按钮（只读）。
  - `test_pending_lines`：最下面一句的三种情况：开关关着；开着、0 份；开着、3 份（带文件夹）。
  - `test_empty_log`：一局都没有时四项换成一句。
  - `test_error`：统计出错时窗口只写一句，报错原文进错误记录。
  - `test_bad_pending`：待发报告的份数给得不对（不是数）时同统计出错，不停在「正在统计…」。
  - `test_loading_then_filled`：打开时先写「正在统计…」，算完换掉。
  - `test_close_before_done`：算完以前关掉窗口，算完不再碰控件、不出错。
  - `test_deleted_before_done`：算完以前窗口已经删掉（底层的 Qt 对象没了），晚到的结果没人接，界面线程、后台线程都不出错，也不记错误记录。
  - `test_hant_names`：繁体界面下筹码名、角色名用游戏繁体名（`advisor/ui/names.py`），统计照旧按简体名算。
  - `test_show_stats_reuses_window`：已经开着时再打开，返回同一个窗口。
  - `test_show_stats_replaces_closed_window`：关掉以后再打开，新开一个、关掉的那个删掉（给了父窗口时也不在父窗口下面越积越多）。
- **检证对象**：`advisor/ui/stats_window.py` 的 `StatsWindow`、`stats_html`、`follow_text`、`pending_text`、`window_style`、`show_stats`，以及 `advisor/play_stats.py` 的数字接到窗口上的那一段。
- **什么时候要跑**：改了 `advisor/ui/stats_window.py`、「我的统计」窗口里的文字、`theme.about_style`、`AboutLook` 或各套皮肤「关于」的排法（窗口跟着皮肤换样子、正文垫不垫卡片的底色）以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_stats_window`（上次约 2 秒）
- **需要什么**：Qt 的 offscreen 平台（`tests/support/qtenv`），不弹窗口；只读 `data/` 资料库。
- **输出怎么看**：同总则。七套皮肤的用例带 `skin=` 子测试名。
- **出错时一般是哪里不对**：
  - 某个皮肤说找不到某一行 → 正文排法或这一套皮肤的颜色、字体没跟着改（`_line`、`stats_html`）。
  - 某个皮肤的对比度不够 → 正文没垫上卡片的底色，或那一套皮肤「关于」的排法、卡片的颜色改了（`window_style`、`CARD_LAYOUTS`）。
  - `test_close_before_done`、`test_deleted_before_done` 报错 → 后台线程算完以后还在碰已经关掉、删掉的窗口（`_filled`、`_failed`）。
  - `test_bad_pending` 报「统计没有算完」→ 待发报告那一步出的错没有交给窗口（`_Worker._run`）。
  - `test_hant_names` 失败 → 名字没有经 `advisor/ui/names.py`，或统计跟着界面语言变了（统计只该用简体名）。
  - `test_show_stats_replaces_closed_window` 失败 → 关掉的旧窗口没删（`show_stats`）。
