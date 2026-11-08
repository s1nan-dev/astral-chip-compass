# 测试指南 · 筹码方案

这是[《测试指南》](../测试指南.md)第八节的一章：每个测试文件一节，每节的结构见那里第八节的开头。


这一章管筹码方案的数据结构和方案文件（`advisor/schemes.py`）、编辑窗口、导入/导出窗口和它们的列表控件。

## test_schemes.py：筹码方案的数据结构、三样推导、分享格式、方案文件和筹码编号表

- **测试观点**（按测试类）：
  - `CodeTableTest`：编号表覆盖每张现役筹码、编号不重复不超上限、编号只增不改（编号 1～89 永远对应最初的筹码）、读坏的表拒收、表不在时返回空表。
  - `MapCodeTest`：地图编号（`data/maps.json` 的 `code`）只增不改（9 张地图的编号固定在测试里）、能标的地图按编号排、结束的地图留着编号、资料里编号重复时一个编号只对应一张地图。
  - `TiersTest`：四档和顺位（改档位、改顺位、移动到某位、上下移；相等要每一档的先后也相同；可哈希、可 pickle）。
  - `SchemeTest`：方案的清理和校验、相等与哈希、目标流派不能手选（由标记推出来）、复制默认推荐、唯一名。
  - `SchoolsTest`：列进方案的流派、被锁成不选的流派、目标流派三样推导；上锁的门槛取 `rules.toml [pool] max_schools`。
  - `CodecTest`：短字符串的编码和解码（第 2、3 版的固定样本、先后就是顺位、小编号穷举、保留位代表更新版本、非规范的内容拒收、第 3 版没标地图或最大编号那一位没标时拒收、校验连版本一起算、逐字符损坏和每个前缀都拒收、越界拒收、编号不认识的地图忽略并记问题）；短字符串的开头（`test_short_prefixes`：第 3 版写 ACC3-、第 2 版写 CA2-，导入时 ACC、CA 两种开头哪个版本都认，不分大小写、全角半角，别的字母不认；固定样本是 CA3- 开头，换成 ACC3- 解出来一样）。
  - `RoundTripTest`：两种分享格式往返（复制默认推荐、随机 200 套、说明和作者里含分隔符、极端组合、可读文字的写法）；带地图的方案打开 `maps` 开关往返（含地图）；`WRITE_MAPS` 开着（默认）或参数打开时标了地图的写第 3 版、没标的照写第 2 版；开关或参数关掉时一律写第 2 版。
  - `ParseTest`：宽容解析和安全（第 2、3 版固定样本、前后带别的文字、手改的文字、坏文字与角色、旧版本的字符串、第 3 版不写「地图」算通用、运动场和认不出的地图名忽略并记问题、乱填不崩、名称和说明截断、前后缀不崩、大段粘贴限时、不是分享文字）。
  - `DataUpdateTest`：模拟资料更新后旧的字符串和方案文件怎么读（改名、移除、更新版本的新筹码、编号表读不出）。
  - `StoreTest`：方案文件（默认位置、没有文件、增删改和上次使用、文件格式、顺位存取、行序定顺位、没变不写、备份是上一版、坏文件和旧格式、写到一半中断、来自更新版本时只读、不认识的字段原样保留、筹码行里不认识的字段跟着筹码、地图行里不认识的字段和认不出的行原样保留、先认编号再认名称、同步盘的冲突副本不动、导入、极端组合、写错不抛、写不进改存 fallback、读不了不覆盖、短暂被占用会重试、边读边删不出错）。
- **检证对象**：`advisor/schemes.py` 的 `Tiers`、`Mark`、`Scheme`、`SchemeStore`、`ChipCodes`、`Catalog`、`get_catalog`、`load_codes`、`read_codes`、`map_codes` / `taggable_maps` / `normalize_maps`、`encode_payload` / `decode_payload`、`ShortPayload`、`parse_share`、`to_text`、`to_short`、`scheme_from_default`、`default_marks`、`default_schools`、`listed_schools`、`locked_schools`、`target_schools`、`school_of`、`clean_text` / `clean_name` / `unique_name`、`backup_path`、`CODE_MAX`、`MAP_CODE_MAX`、`SHARE_VERSION`、`SHARE_VERSION_MAPS`、`WRITE_MAPS`、`FILE_VERSION`、各 `PROBLEM_*`、`LoadNotice`、`SchemesReadOnly`；`advisor/fileio.py`；`advisor/paths.py` 的 `schemes_path`；`tools/check_data.py` 的 `codes_problems`、`map_problems`、`CODE_MAX`；`tests/support/sandbox.py` 的 `real_paths` / `temp_dir`；`tests/test_fileio.py` 的 `Disk`。
- **什么时候要跑**：改 `advisor/schemes.py` 的任何部分（档位、分享格式、解析、方案文件）；改 `data/chip_codes.json`；改 `tools/check_data.py` 的编号检查；改 `advisor/fileio.py` 的原子写；改 `advisor/paths.py` 的方案文件位置。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_schemes`（上次约 3.9 秒）
- **需要什么**：没有特别要求。方案文件写在沙箱 `.tmp` 下的临时文件夹里，不碰真的 `schemes.json`。
- **输出怎么看**：同总则。测试文件里的分享格式固定样本（短字符串和可读文字）是与格式绑定的预期，改了格式两边一起改。
- **出错时一般是哪里不对**：
  - `CodeTableTest` 失败 → 编号表的覆盖/去重/上限，或 `read_codes` / `load_codes` 的拒收改了；也可能是 `chips.json` 加了筹码没配编号。
  - `TiersTest` / `SchemeTest` 失败 → `Tiers` / `Scheme` 的相等、顺位、清理、复制默认推荐改了。
  - `SchoolsTest` 失败 → `listed_schools` / `locked_schools` / `target_schools` 或 `max_schools` 门槛改了。
  - `CodecTest` 失败 → 短字符串的位布局、校验或拒收规则改了（改动要出新版本，固定样本跟着改）。
  - `RoundTripTest` / `ParseTest` 失败 → `parse_share`、`to_text` / `to_short`、模糊匹配或截断改了。
  - `DataUpdateTest` 失败 → 载入时对改名、移除、更新版本的识别改了（看 `Catalog` 的筹码解析）。
  - `StoreTest` 失败 → `SchemeStore` 的读写、备份、坏文件处理、只读、未知字段保留或 fallback 改了。
  - `MapStoreTest` 失败 → 方案文件里 `maps` 的写法（按编号、同时记编号和名称）、读时先认编号再认名称、类型不对按通用、认不出的行原样保留改了。
  - 有意改的：短字符串最大长度（115）、固定样本（含第 3 版带地图的固定样本）、编号 1～89 的名称表都是与格式绑定的预期，改了格式或资料要一起改。

## test_schemes_file.py：方案文件（`schemes.json`）的读写和各种坏情况，由方案存储 `SchemeStore` 负责

- **测试观点**：
  - `OrphanTest`：方案文件不在、只剩备份时——不读备份、第一次保存前把备份另存为 `.bak.orphan-时间`（名字被占时接 `-2`）、另存不成时照常写方案文件但不碰备份、备份在保存前已被删时照常保存、只剩备份又没有内容要存时不建文件、写不进改存 fallback 时也先另存只剩的备份。
  - `SavedTest`：改动即存——每次改动 `save` 返回真、`saved` 为真；写不进去（改存也不成）时 `save` 返回假、`saved` 为假、`save_error` 有值、内存里的改动照旧有效；来自更新版本的文件只读；写的时候刷进硬盘（`os.fsync`）。
  - `BrokenTest`：读坏了——坏文件没能另存又没有备份时这次运行不写方案文件（`blocked`）；坏文件没能留一份、备份能用时改读备份；用备份恢复没成时 `save_error` 有值、不是 `blocked`、下次保存照常写；备份没能用上时按只剩备份处理；运行中文件被外面改坏时下次保存照常写、坏内容不盖掉备份。
  - `MapStoreTest`：适用地图的存取——带地图存了再读回来一致（按编号排、同时记编号和名称，读时先认编号再认名称）；没有 `maps` 键时算通用；`maps` 类型不对时这一套算通用、文件不当坏文件、别的方案照常；认不出的地图行不参与计算、改了这套方案再存时原样留在后面；没改过的方案原样写回。
- **检证对象**：`advisor/schemes.py` 的 `SchemeStore`（`_load`、`save`、`_keep_orphan`、`_save_fallback`、`saved`/`blocked`、`_atomic_write`、`backup_path`）；`advisor/fileio.py` 的 `write_bytes`、`read_with_backup`、`move_aside`。
- **什么时候要跑**：改了 `advisor/schemes.py` 里方案文件的读写（`SchemeStore._load`、`save`、`_keep_orphan`、`_parse`），或改了 `advisor/fileio.py` 的整份写入 / 备份 / 另存 / 读并兜底，或改了 `.bak`／`.broken-时间`／`.bak.orphan-时间` 的命名与时机以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_schemes_file`（上次约 0.3 秒）。
- **需要什么**：没有特别要求（不建窗口、不用 Qt）。
- **输出怎么看**：用 `assertLogs` 核对记下的警告（`advisor.schemes`、`advisor.fileio`）；文件都写在测试沙箱的临时文件夹里。其余同总则。
- **出错时一般是哪里不对**：
  - `OrphanTest.test_backup_kept_before_first_save` 失败 → 只剩备份时第一次保存前另存的时机，或该不该读备份的判断改坏了（看 `advisor/schemes.py` 的 `_keep_orphan`、`_load`）。
  - `OrphanTest.test_name_taken` 失败 → 另存重名时接 `-2` 的逻辑改坏了（`advisor/fileio.py` 的 `_aside`）。
  - `OrphanTest.test_set_aside_fails` 失败 → 另存不成时"照常写方案文件、但不再碰备份"的分支改坏了（`_keep_orphan`）。
  - `OrphanTest.test_set_aside_before_fallback` 失败 → 改存 fallback 时是否仍先另存只剩的备份、`path` 是否换成 fallback（`_save_fallback`）。
  - `SavedTest.test_write_fails` 失败 → `save` 的返回值、`saved`／`save_error`，或改存的判定改坏了。
  - `SavedTest.test_read_only` 失败 → 版本比本程序新的文件只读判定（`save` 应返回假、`saved` 假、`blocked` 假）。
  - `SavedTest.test_flushed_to_disk` 失败 → 写入是否还调 `os.fsync`（`advisor/fileio.py` 的 `_sync`）。
  - `BrokenTest.test_not_set_aside_blocks_writing` 失败 → 坏文件没能另存时 `blocked` 的判定、坏文件是否原样留着（`_load`、`blocked`）。
  - `BrokenTest.test_restore_failed_keeps_good_backup` 失败 → 用备份恢复没成时 `save_error`／`saved` 的处理，或"坏内容不盖掉备份"的检查（`advisor/fileio.py` 的 `read_with_backup`、`_atomic_write` 的 `backup_check`）。
  - `BrokenTest.test_externally_broken_file_does_not_replace_backup` 失败 → 运行中改坏时的备份保护，或断电后的零字节不记警告的处理。
  - 这里多处断言的是文件名、字节数和返回值，属于有意核对的行为；改了这些约定要连同预期一起改。

## test_scheme_editor.py：编辑筹码方案窗口本身（分块分组、软锁、默认推荐、目标流派、规则提示、改动即存……）

- **测试观点**（按测试类）：
  - `TextsTest`：界面文字和测试里的对照句子逐字相同（窗口标题、按钮、档位叫法、软锁文字、筹码池提醒文字），去掉的旧文字不再出现，规则常数（`HARD_TO_SEE` 等）。
  - `LayoutTest`：`list_layout` 把标了档位的按档位归块、块里按顺位、未标记按流派分组、被锁的流派进「其他流派」；各种标法下每个能标档位的筹码都正好出现一次。
  - `HintsTest`：橙色软锁提示（`lock_notice`，列进方案的流派里只标了可选的另起一行点名，`second_only_notes`）、灰色规则提示（`rule_hints`：三个以上流派竞争、必选较多、同系列低一级标得比高一级高）、目标流派和说明、复制来的方案说明、行尾的默认档位和筹码池提醒（`row_end`）。
  - `WindowTest`：窗口框架（标题、大小、不总在最前、不是对话框）、角色下拉框用悬浮窗同一种画法、角色下拉框每组前面一行不能选的组名（按角色找下标、选中项都对，`test_hero_box_groups`）、去掉的控件不再有、默认按悬浮窗当前角色和它正在用的方案打开、方案列表行尾的「默认」「我的」「使用中」、换角色不影响悬浮窗。
  - `UsedAndAddedTest`：「使用中」跟着方案存储里这个角色正在用的那套走（悬浮窗换了以后 `show_used` 重标、不重建列表；每个角色各标各的）；新建空方案、复制、复制为我的方案以后发 `scheme_added`（所属角色、新方案 id），只读时不发。
  - `BlocksAndGroupsTest`：新建方案只在「未标记」一块、各流派分组、地图专属默认收起、标了档位的排进块里、空组不显示、搜索和「只看已标记」、被锁的行在只看已标记里不显示但能搜到。
  - `TierButtonsTest`：点档位按钮选中／取消／改档位、拖动改顺位、改别的筹码的档位不影响其余顺位。
  - `SoftLockTest`：两个流派列进后其余被锁、问话的显示与取消／确认、被锁行的手标不选、离开方案后重新被锁、四个流派、只读时的被锁行、问话被别的动作收起、问话滚进画面。
  - `TargetSchoolsTest`：目标流派随标记即时刷新（通用不算、可选不算）。
  - `NameTest`：新建时的名称编号、名称限长（按字数、表情算一个字）、只改一个框不动别的、复制时名称截短、重名接「（2）」、空名成「未命名方案」。
  - `DefaultViewTest`：默认推荐只读、只显示四档的块、行尾写默认档位、有固定档位时「复制」「删除」换成「复制为我的方案」、复制默认推荐照原样、没有固定档位的角色写一句说明；没有固定档位时不显示「复制为我的方案」、只有「新建空方案」（这时它的悬停写明不是副本，选中我的方案时不带悬停），点了建空白方案、不记「复制自默认推荐」，只读的说明跟着换（`test_new_empty_without_fixed_tiers`）；这时空着的筹码列表里写下一步怎么做（`DEFAULT_EMPTY_GUIDE`：盖在列表的可见区域里、不接鼠标、不撑大窗口，选中我的方案或换成有固定档位的角色时收起，`test_empty_guide`），七套皮肤、两种屏幕高度下整段放得下（`test_empty_guide_all_skins`）。
  - `DeleteTest`：删除在窗口内确认、不另弹窗口、删正在用的方案改回默认推荐、切方案取消确认。
  - `SaveTest`：改动即存（停止输入后存、切方案时存没存的文字、文字和档位一起存、每种改动都发 `schemes_changed`、外部导入后刷新、方案被别处删了再点档位不出错）。
  - `ReadOnlyTest`：来自更新版本的方案文件只读（按钮、输入框、档位都不能改，文件不变）。
  - `MapsTest`：适用地图一栏（`MapField`，一行「适用地图」和一个下拉多选 `MapCombo`）按 `taggable_maps` 列出每张地图一项；点了立即存进方案（`normalize_maps` 按地图编号排）、发 `schemes_changed`，再点一下取消；下拉框上写标了哪几张、一张都没标写「全地图通用」，悬停说明写明全标了哪几张；点开以后列着每张能标的地图，勾一项不收起（`test_popup_lists_maps_and_stays_open`；按下和松开分开、中间事件循环照常跑的真点法，下拉列表里按回车勾选，也都不收起，按下以后拖到外面松开以后 Esc、点在外面照常收起：`test_popup_stays_open_with_real_clicks`）、空格／回车点开、上下键移动、空格勾选（`test_keyboard_opens_and_checks`）、滚轮不换项（`test_wheel_does_not_change_maps`）；方案列表那一行的悬停说明写「适用地图：……」（通用的不写）；默认推荐不显示这一栏、只读时下拉框不能点；新建空方案、复制为我的方案是通用的，复制带着原来的地图；七种排法都建得出这一项；矮屏幕（1280×600、560）下窗口缩得到、这一项在窗口里（`test_short_screen_*`；经典主题-黑的表单在滚动区里，先滚到底）。方案文件里存不存地图不在这里查（另一处实现），只查 `SchemeStore` 内存里的结果。
  - `StaleAndHintsTest`：资料更新后对不上的筹码列出并可移除、灰色规则提示随标记即时出现消失、提示很多时在框里滚动。
  - `ExtremeTest`：全必选、全不选、只标通用和地图专属、标了又全撤掉。
  - `ScrollTest`：屏幕矮时只有筹码列表变短、其余不动；内容放得下时不显示滚动条。
  - `PoolNotesTest`／`PoolNotesDisplayTest`：筹码池提醒用真实资料核对（几名牌角色的「较难出现」名单、悬停文字、判据），且只出现在标了必选／推荐／可选的行的行尾、随角色变。
  - `EscapeTest`：用户写的名称／作者／说明按纯文本显示，不渲染网页标记。
- **检证对象**：`advisor/ui/scheme_editor/` 的 `SchemeEditor`（`window.py`）、界面文字和规则函数（`notes.py` 的 `list_layout`、`rule_hints`、`series_hints`、`lock_notice`、`second_only_notes`、`target_note`、`pool_notes`、`row_end`、`copied_note`、`default_ends`、`block_title`、`copy_name`、`new_name` 等）、小部件（`widgets.py`）；`advisor/ui/scheme_list/`（`SchemeList`、`BlockSpec`/`GroupSpec`/`RowSpec`）；`advisor/schemes.py`（`Scheme`、`Tiers`、`default_marks`、`listed_schools`、`locked_schools`、`target_schools`）；`advisor/pool.py`。
- **什么时候要跑**：改了 `advisor/ui/scheme_editor/`、`advisor/ui/scheme_list/`、`advisor/schemes.py`、`advisor/pool.py` 里任何一处，尤其是界面文字、分块分组的规则、软锁、目标流派、默认推荐、筹码池提醒、改动即存。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_scheme_editor`（上次约 10.8 秒）。
- **需要什么**：Qt（离屏平台，不会弹窗口）；不需要识别样本、显卡；方案文件写在测试沙箱的临时文件夹里。
- **输出怎么看**：没有特别的打印；界面文字、档位叫法、筹码池提醒的文字和数字都在断言里逐字比，资料库变了（如「未标记 · 85 个」、某角色「较难出现」名单）这些数字会跟着要改。其余同总则。
- **出错时一般是哪里不对**：
  - `TextsTest` 失败 → 界面文字改了却没同步（`notes.py` 的文字常量）；这一类的预期是有意写的，改了文字要一起改。
  - `LayoutTest` 失败 → 分块分组的规则改坏了（`notes.py` 的 `list_layout`、`advisor/schemes.py` 的 `listed_schools`／`locked_schools`）。
  - `HintsTest` 失败 → 软锁提示、规则提示、目标流派说明或行尾措辞的逻辑改坏了（`lock_notice`、`rule_hints`、`series_hints`、`row_end`）。
  - `WindowTest` 失败 → 窗口初始状态、角色下拉框画法和组名行或换角色的处理改坏了（`window.py` 的 `set_hero`、`_make_hero_box`，`advisor/ui/widgets.py` 的 `fill_hero_combo`、`first_data`）。
  - `UsedAndAddedTest` 失败 → 「使用中」的标法（`window.py` 的 `used_id`、`_mark_used`、`show_used`，`notes.py` 的 `USED_TAG`）或新建、复制以后发的 `scheme_added`（`_add`）改了。
  - `BlocksAndGroupsTest` 失败 → 标记后行进哪一块、组的标题和收起、筛选（搜索／只看已标记）改坏了。
  - `TierButtonsTest` 失败 → 点档位按钮／拖动改顺位后存进方案的内容改坏了（`_tier_clicked`、`_row_moved`）。
  - `SoftLockTest` 失败 → 软锁的判定、问话的显示／确认／取消、被锁行手标不选的处理改坏了（`_ask_lock`、`_lock_answered`、`locked_schools`）。
  - `TargetSchoolsTest` 失败 → 目标流派的推导改了（`advisor/schemes.py` 的 `target_schools`）。
  - `NameTest` 失败 → 名称限长、重名加后缀、空名成「未命名方案」的逻辑改了（`copy_name`、`new_name`、`SchemeStore.unique_name`）。
  - `DefaultViewTest` 失败 → 默认推荐只读、复制照原样、选中默认推荐时显示哪几个按钮、「新建空方案」的悬停（`window.py` 的 `_show_selected`、`_show_copy_default`、`_copy_default`，`notes.py` 的 `NEW_BUTTON`、`NEW_EMPTY_TIP` 等）、行尾默认档位或条件规则的说明改了；`test_empty_guide*` 失败 → 空列表里那一段（`empty_guide`、`notes.DEFAULT_EMPTY_GUIDE`）的显示条件，或 `scheme_list` 的 `set_note`、`_place_note` 摆放改了。
  - `SaveTest` 失败 → 改动即存（`_commit`、`_flush`、`schemes_changed`）或外部导入后刷新的处理改坏了。
  - `ReadOnlyTest` 失败 → 只读文件下按钮／输入框／档位的处理改了。
  - `MapsTest` 失败 → 适用地图下拉多选的勾选、存进方案的内容或方案列表的悬停说明改了（`MapField`、`MapCombo`、`_map_toggled`、`_maps_tip`、`normalize_maps`），或默认推荐、只读时这一栏的显示改了；`test_short_screen_*` 失败 → 这一项把窗口撑得超过了屏幕，或把别的地方挤了出去。
  - `PoolNotesTest`／`PoolNotesDisplayTest` 失败 → 筹码池提醒的判据、悬停文字或出现在哪些行上改了（`pool_notes`、`advisor/pool.py`）；里面的名单和数字是有意的预期。

## test_scheme_editor_package.py：`scheme_editor` 包对外的名字、共用的画法、基类上的焦点框

- **测试观点**：
  - 能从 `advisor.ui.scheme_editor` 导入的名字（界面文字、规则常数、函数和数据类、七种画法、部件；清单在测试里）都在（`test_reexports`）。
  - 旧的颜色常量名和样式表（`STYLE`、`DIM_COLOR` 等）能按属性取（`test_legacy_names`）。
  - 是一个包、五个子模块都在（`test_is_package_with_modules`）。
  - 画法类还是 `scheme_cards` 里那几个再导出的同一份，排法表还是那七个建法（`test_delegates_are_the_shared_ones`）。
  - 基类有 `paint_focus`、默认不画也不报错；六种有焦点框的画法各写自己的 `paint_focus`；普通列表那一行用基类的（`test_base_has_paint_focus`、`test_row_delegates_draw_their_own_focus`、`test_plain_list_uses_base_focus`）。
- **检证对象**：`advisor/ui/scheme_editor/` 包（`__init__.py` 的再导出和 `__getattr__`，及 `notes.py`、`widgets.py`、`layouts.py`、`window.py` 五个子模块）；`advisor/ui/scheme_cards.py` 的基类和画法。
- **什么时候要跑**：改了 `scheme_editor` 包的拆分（增删子模块、改再导出的名字），或改了 `scheme_cards` 的基类／画法的 `paint_focus` 以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_scheme_editor_package`（上次约 0.002 秒）。
- **需要什么**：Qt（离屏）；不建窗口。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `test_reexports` 失败 → 某个名字移了地方、改名或没再导出（清单在测试里，名字很多）。
  - `test_legacy_names` 失败 → `__getattr__` 取旧色名／样式表的方式改了。
  - `test_is_package_with_modules` 失败 → `scheme_editor` 不是包了，或子模块改名／删除。
  - `test_delegates_are_the_shared_ones` 失败 → 画法不再从 `scheme_cards` 取同一份，或排法表变了。
  - `test_row_delegates_draw_their_own_focus`／`test_plain_list_uses_base_focus` 失败 → 焦点框该在哪个类上实现的划分改了（六种自己画、普通列表不重写）。

## test_scheme_list.py：编辑窗口的筹码列表（摆放、拖动改顺位、改档位时的飞与提示、收起展开、筛选、动画）

- **测试观点**（按测试类）：
  - `LayoutTest`：块、组、行的先后和排列；增删行；空列表和只有一行；行高一致、块标题和分隔线；块标题为空；重复和奇怪的输入；行尾的几段；点组标题收起展开；收起展开状态交出、接回；筛选；各种样子都画得出来。
  - `ClickTest`：点档位按钮发信号、不能点的行不发、列表自己不改档位；按下后移到别处松开不算点；悬停说明；按钮的位置在行里。
  - `DragTest`：同一块里上下拖改顺位、按筹码名也能拖、拖不出本块、Esc 取消、松手事件没送到的取消、失去前台取消、回到起点算没动、小移动不算拖、按钮上和不能拖的行不拖、筛选时只在显示的行间拖、窗口存不进去时回原位、内容变了取消拖动、拖到边缘自动滚动、块整块露出来时不滚。
  - `TransitionTest`：改档位后到新块最后并闪一下；要去的地方在画面外时出提示并补回滚动位置；在不在画面的判据；取消标记回到收起着的组；所属流派被锁时去「其他流派」；连着标几个画面不跑；上面一块变长变短时看着的组不跳；最上面看着时下面改档位画面不跑；一次要飞的行太多时直接到位；上锁解锁整组；上锁时点的那一行也飞；关动画时直接到位。
  - `AnimationTest`（假时钟推进）：飞到画面里的块、飞出画面淡出、收起展开的影子和动画、拖动时的让位、动画没做完又改、回到收起的组、上锁的动画。
  - `InlineAndQueryTest`：把控件（问话）放进列表、控件变高跟着重排、收起时不显示、拿掉后交还；问话滚进画面；放了控件的块不拖；查询（`row_rect`、`scroll_to`、`content`）。
  - `KeyboardTest`／`EditorKeyboardTest`：Tab 与 Shift+Tab 的先后、空格／回车的点击、不能点的行跳过、被锁的行能走到、只读时直接穿过、筛选时走到、焦点框、鼠标点了以后键盘接着走、键盘连着标几个时焦点落在补上来的行；真的编辑窗口的 Tab 先后和软锁问话的键盘答法。
- **检证对象**：`advisor/ui/scheme_list/`（`SchemeList`、`_RowItem`／`_HeaderItem`／`_InlineHost`／`_EdgeHint`，`specs.py` 的 `RowSpec`／`GroupSpec`／`BlockSpec` 和尺寸，`view.py` 的常量与算法）；`advisor/ui/theme/` 的 `ListLook`。
- **什么时候要跑**：改了 `advisor/ui/scheme_list/` 里任何一处（摆放、拖动、动画、画法、尺寸、提示），改了 `theme` 的 `ListLook`，或改了编辑窗口交给它的内容格式（`BlockSpec`／`GroupSpec`／`RowSpec`）以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_scheme_list`（上次约 6.4 秒）。
- **需要什么**：Qt（离屏，不会弹窗口）；动画另换假时钟一帧一帧推进，不靠真实时间；有一条用例在没下载筹码图标时会跳过（`test_icons_refreshed_after_download`）。
- **输出怎么看**：那条换图标的用例需要 `assets/chips` 里已下载的筹码图标，没有时打印跳过原因；其余没有特别输出。其余同总则。
- **出错时一般是哪里不对**：
  - `LayoutTest` 的排列、增删、筛选失败 → 列表摆放、分块或去重（`specs.py` 的 `_clean_blocks`／`_structure`）改坏了。
  - `DragTest` 失败 → 拖动改顺位、拖出边界、取消（Esc／失去前台／内容变）、自动滚动的处理改坏了（`view.py`）。
  - `TransitionTest` 的提示和补回滚动位置失败 → 改档位时出提示的判据（`_side`）、补回滚动位置、飞太多直接到位的处理改坏了。
  - `AnimationTest` 失败 → 动画的时间、让位、影子、落定闪一下改坏了（`specs.py` 的 `_Motion`、`view.py` 的 `_tick`）。
  - `ClickTest`／`KeyboardTest` 的焦点框和 Tab 先后失败 → 焦点框画法或能用 Tab 走到的地方（`_stops`／`_go`）改坏了。
  - `InlineAndQueryTest` 失败 → 把问话放进列表、重排或查询接口改坏了。

## test_scheme_list_package.py：筹码列表包对外的名字、是同一份对象、旧外观常量名按皮肤取

- **测试观点**：
  - 测试和窗口用到的模块级名字（含私有）都能从包上取到（`test_all_names_importable`）。
  - 三个子模块 `specs`／`items`／`view` 都在（`test_submodules_present`）。
  - 包上的名字和它所在子模块里的是同一个对象（再导出，不是抄的一份）（`test_same_objects_as_submodules`）。
  - 旧的外观常量名按属性取当前皮肤的值，换一套皮肤后跟着变（`test_legacy_skin_names`）。
  - 默认动画开关按 `specs` 里那一份读，改过时不留旧值（`test_default_animated_reads_live_value`）。
- **检证对象**：`advisor/ui/scheme_list/` 包（`__init__.py` 的再导出和 `__getattr__`，及 `specs.py`／`items.py`／`view.py`）；`advisor/ui/theme/`。
- **什么时候要跑**：改了筹码列表包的拆分（增删子模块、改再导出的名字），或改了旧外观常量名、动画开关的存放以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_scheme_list_package`（上次约 0.001 秒）。
- **需要什么**：Qt（离屏）；不建列表窗口。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `test_all_names_importable` 失败 → 某个名字移了地方或没再导出（清单在测试里）。
  - `test_same_objects_as_submodules` 失败 → 包上的名字是抄的一份，不是子模块里那一个。
  - `test_legacy_skin_names` 失败 → `__getattr__` 取旧外观常量名、或换皮肤后跟着变的行为改了。
  - `test_default_animated_reads_live_value` 失败 → `set_default_animated` 没改到 `specs` 里那一份，或包上读的是旧值。

## test_scheme_cards.py：方案列表的画法类的结构和排法表

- **测试观点**：
  - 七种画法（`_SchemeListDelegate` 等）都在 `advisor/ui/scheme_cards.py` 里，都继承同一个基类 `_SchemeRowDelegate`（`test_delegates_share_base`）。
  - 皮肤测试按名字从 `advisor.ui.scheme_editor` 取 `_SchemeStickerDelegate`、`_SchemeWellDelegate`：在 `scheme_editor` 上也再导出一份（`test_editor_reexports`）。
  - 编辑窗口七种排法、导入/导出窗口七种排法各有一个建界面的方法，名字集合固定（`test_layouts`）。
- **检证对象**：`advisor/ui/scheme_cards.py`（基类和七个子类）；`advisor/ui/scheme_editor/__init__.py` 的 `LAYOUTS`；`advisor/ui/scheme_share.py` 的 `LAYOUTS`。
- **什么时候要跑**：改了 `advisor/ui/scheme_cards.py`（加减画法、改基类、搬家），或改了编辑窗口／导入导出窗口的排法表（`LAYOUTS`）以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_scheme_cards`（上次约 1.2 秒）。
- **需要什么**：Qt（离屏）；不建窗口。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `test_delegates_share_base` 失败 → 某个画法改名、搬家或不再继承 `_SchemeRowDelegate`。
  - `test_editor_reexports` 失败 → `scheme_editor/__init__.py` 不再再导出那两个画法（皮肤测试会取不到）。
  - `test_layouts` 失败 → 加减了排法、改了建界面的方法名，或导入/导出窗口的排法表变了。

## test_scheme_cards_focus.py：方案列表各画法画焦点框（一套方案的行有、分组标题没有）

- **测试观点**：
  - 每套皮肤按它编辑窗口排法所用的那种画法，在离屏的图上把同一行带焦点、不带焦点各画一遍，逐像素比：一套方案的行（默认推荐、我的方案）带焦点时和不带焦点不同（选中、悬停、禁用时都这样）；分组标题带不带焦点画得一样（`test_rows_draw_focus_headers_do_not`）。
  - `paint_focus` 本身只在带焦点时画：不带焦点时调它，图上什么都不多（`test_paint_focus_only_with_focus`）。
  - 悬浮窗正在用的那一套（行尾「使用中」）：七种画法（默认的一行加六种）都画得出来，和同一行没在用时画得不一样，选中、悬停、禁用时也一样（`test_used_row_marked`）。
  - 皮肤用到的排法都在测试列出的六种里；加了新排法要先把它用的画法补进测试的表（`skins()`）。
- **检证对象**：`advisor/ui/scheme_cards.py` 的 `_SchemeRowDelegate.paint_focus` 和六个子类的 `paint_focus`、「使用中」的画法（`_USED_ROLE`、`_scheme_tag`）；`advisor/ui/theme/` 的 `skins`／`set_skin`。
- **什么时候要跑**：改了 `scheme_cards.py` 里任何画法的 `paint_focus`（焦点框）、改了方案列表一行的画法（含「使用中」），或加了新排法／新皮肤以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_scheme_cards_focus`（上次约 0.2 秒）。
- **需要什么**：Qt（离屏）和微软雅黑字体（逐像素比，字体不对结果不可比；测试环境会装）。不需要识别样本。
- **输出怎么看**：逐像素比；失败时说的是一行（皮肤 + 画法 + 哪一行 + 哪种状态）画出的图和不带焦点时相同或不同。不写临时文件。其余同总则。
- **出错时一般是哪里不对**：
  - `test_rows_draw_focus_headers_do_not` 失败 → 某个画法一套方案那一行的焦点框没画或画错，或分组标题被画上了焦点框（看 `scheme_cards.py` 对应画法的 `paint_focus`／`paint`）。
  - `test_paint_focus_only_with_focus` 失败 → `paint_focus` 忘了先看有没有焦点（`option.state` 的 `State_HasFocus`）。
  - `test_used_row_marked` 失败 → 某个画法没按 `_USED_ROLE` 画「使用中」（看对应画法的 `paint_row`，以及皮肤 `SCHEME_CARD` 里的 `tag_used` 这几项）。
  - `skins()` 里的断言失败 → 新加的排法／皮肤没在测试的排法→画法表里登记。

## test_scheme_save_notice.py：写不进方案文件时，编辑窗口右下角换成警告色的写不进说明

- **测试观点**：
  - 现在的内容不在方案文件里时（`store.saved` 为假），右下角出现警告色的写不进说明（文本、`objectName` 为 `warn`）；之后某次保存写进去了换回「已保存」（`objectName` 为 `saved`）（`test_write_failure_shows_warning_then_recovers`）。
  - 在输入框里打字时先藏起来，存完（写不进）再显示那句（`test_typing_hides_then_saves`）。
  - 写不进时那句话可能折成两行；换到别的方案时这一行换回一行的「已保存」再藏起来，底部不留两行高的空白（`test_switching_scheme_resets_to_one_line`）。
  - 方案文件来自更新版本（只读）时这一行都不显示（`test_read_only_never_shows`）。
  - 七套皮肤（三种搭界面的代码）下那句话都排得下：不截断、删除按钮不被挤掉（`test_warning_fits_in_every_skin`）。
- **检证对象**：`advisor/ui/scheme_editor/window.py` 的 `SchemeEditor._show_saved`／`_set_saved`；`advisor/schemes.py` 的 `SchemeStore.saved`（写没写成）。写不进用替掉 `advisor.fileio.write_bytes` 的方式造（不真的去占用文件）。
- **什么时候要跑**：改了右下角「已保存」／写不进提示的写法，改了「现在的内容在不在方案文件里」（`SchemeStore.saved`）的判定，或改了编辑窗口底部这一行的排版以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_scheme_save_notice`（上次约 5.8 秒）。
- **需要什么**：Qt（离屏）；方案文件写在测试沙箱的临时文件夹里。不需要识别样本。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `test_write_failure_shows_warning_then_recovers` 失败 → `saved` 的判定或"写不进时显示哪一句"的逻辑改坏了（`window.py` 的 `_show_saved`）。
  - `test_typing_hides_then_saves` 失败 → 在输入框打字时先藏起来、存完再显示的处理改了。
  - `test_switching_scheme_resets_to_one_line` 失败 → 换方案时换回一行「已保存」再藏起来，或藏起来时仍占位的处理改了。
  - `test_read_only_never_shows` 失败 → 只读时不该显示这一行的判断改了。
  - `test_warning_fits_in_every_skin` 失败 → 警告那句话的排版、皮肤里的字号／行高，或底部一行的布局改了（不是写盘逻辑）。

## test_scheme_text_fields.py：名称／作者／说明三个文字框的限长，和可读文字里的转义

- **测试观点**：
  - `LimitTest`：名称满了以后在中间打字、粘贴、输入法上屏都进不去，原有的字一个不挤掉，光标停在刚输入的字后面；离满还差几个字时只进去放得下的（末尾去掉），光标停在进去的字后面；表情占两个 UTF-16 码元、光标位置按码元算；说明里表情和汉字混着、离满还差 2 个字时只进去前 2 个，后面原有的字都在；改完存进文件再读回来仍是那份。
  - `EscapeTest`：说明、作者里本来就有 `〖〗丨＼` 等近似字或转义符时，导出加转义、导入照原样还原，不换成 `【】｜`（`test_lookalikes_in_original_kept`、`test_hand_written_backslash`）；没有这几个字时原样（`test_plain_text_unchanged`）；随机拼的说明、作者导出再导入都完整还原（`test_random_round_trip`）。
- **检证对象**：`advisor/ui/scheme_editor/widgets.py` 的 `_CharLimit`（限长）；编辑窗口的三个输入框（`name_edit`／`author_edit`／`note_edit`）；`advisor/schemes.py` 的 `clean_text`／`clean_name`、`to_text`、`parse_share`、`_escape_field`／`_unescape_field`。
- **什么时候要跑**：改了输入框的限长算法，或改了 `advisor/schemes.py` 里方案名称／作者／说明的清理和分享格式的转义（`to_text`、`parse_share`）以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_scheme_text_fields`（上次约 1.1 秒）。
- **需要什么**：Qt（离屏，建真的编辑窗口）；中文用 `insert()` 和输入法事件输入。不需要识别样本。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `LimitTest.test_full_name_keeps_text` 失败 → 限长按字数算、满了不挤掉原有的字、"光标停在刚输入的字后面"的处理改坏了（`_CharLimit`）。
  - `test_insert_cut_to_room`／`test_mixed_text_cut_keeps_tail` 失败 → "只进去放得下的那几个、末尾去掉、后面原有的字都在"的处理改了。
  - `test_emoji_cursor_counts_code_units` 失败 → 光标位置按 UTF-16 码元算（表情占两个）的处理改了。
  - `EscapeTest` 系列失败 → `advisor/schemes.py` 的转义／还原（`_escape_field`／`_unescape_field`、`to_text`／`parse_share`）改了；这些是分享格式的约定，改了要连预期一起改。

## test_scheme_wiring.py：编辑方案、导入/导出两个窗口在七套皮肤（七种排法）下的信号都只接一次、接到该接的方法上

- **测试观点**：
  - 建窗口之前把窗口类上接信号的方法换成只记名字的替身，建好以后逐个发信号，看叫到了哪些方法、各几次。
  - 编辑窗口：角色下拉框、方案列表、新建空方案／复制／复制为我的方案／删除、删除确认、关闭、对不上的筹码的移除、搜索和只看已标记、筹码列表的 `tier_clicked`／`row_moved`、软锁问话、三个输入框的 `textEdited`／`editingFinished`，各只接一次、接到该接的方法（`test_editor`）。
  - 导入/导出窗口：角色下拉框、全选、复制、粘贴、导入、清空、关闭、格式按钮，各只接一次（`test_share`）。
  - 建窗口时多出来的、没有父部件的顶层部件为空；窗口上记着的部件全在这个窗口里（重复接线会让保存、删除、导入执行两次；部件落到窗口外面则藏起来）。
- **检证对象**：`advisor/ui/scheme_editor/` 的 `SchemeEditor`（`_make_*` 里的接线）；`advisor/ui/scheme_share.py` 的 `SchemeShareWindow`；`advisor/ui/theme/` 的 `skins`。
- **什么时候要跑**：改了编辑窗口或导入/导出窗口的接线（`_make_*` 里的 `connect`）、加了排法，或调了造部件的先后以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_scheme_wiring`（上次约 0.8 秒）。
- **需要什么**：Qt（离屏）；方案文件写在测试沙箱里。
- **输出怎么看**：失败时说的是哪个信号、叫到了哪些方法（期望只有一个）；「不正常的顶层部件」会列出类名和 `objectName`。其余同总则。
- **出错时一般是哪里不对**：
  - `test_editor` 失败 → 编辑窗口某处 `connect` 加了一回或去了一回、接到了别的方法，或有部件没放进窗口（`window.py` 的 `_make_*`、`layouts.py`）。
  - `test_share` 失败 → 导入/导出窗口同样的接线问题（`advisor/ui/scheme_share.py`）。
  - `strays`／`outside` 断言失败 → 某个部件建出来忘了加进窗口，落成了单独的顶层部件。

## test_scheme_share.py：「导入/导出筹码方案」窗口的勾选、预览、导入结果、转义和只读

- **测试观点**
  - `ExportTest`：窗口布局；按角色筛选（只列有方案的角色，角色前面有不能选的组名、只列有角色的组）、全选只管列出来的、换筛选记住勾选；默认按当前角色的方案勾选；两种格式（短字符串、可读文字）的预览和复制到剪贴板的内容完全一致；记住上次格式；别处改名/删除后刷新；剪贴板被别的程序占时写复制失败；勾选框的 Tab 次序；筹码编号表读不出时短字符串不可用；屏幕放不下时缩小；一套我的方案都没有时列表里写下一步怎么做（`EXPORT_EMPTY`，导入一套后收起，只是筛选到没有方案的角色时不写，`test_empty_guide`），七套皮肤下整段看得到、窗口和有一套方案时一样大（`test_empty_guide_all_skins`）。
  - `ImportTest`：识别结果照对照表（「识别到 N 套：」、能导入的「✓ …」）；每种问题各写一句原因、前面写「第 N 套（名称）：」；编号表读不出时标了档位的短字符串不导入；导入只新建不覆盖（同名加「（2）」）；导出→粘贴→导入后内容相同；粘贴框空着时按钮不可用、改动后自动识别；只读时导入不可用、导出照常；一次最多 50 套 / 2 万字、超出的另起一行说明（并量本进程 CPU 时间）；导入以后每套一行加「使用这套方案」按钮，点了发 `use_requested`、按悬浮窗交回的结果换成那一句（`test_use_buttons_after_import`），方案已在别处删掉时写「这套方案已被删除」、换皮肤后接回照旧（`test_use_gone_and_restore`），导入很多套时在识别结果区里滚动、窗口不变高（`test_many_imported_rows_scroll`）。
  - `MapsTest`：能导入的一套标了地图时，识别结果在目标流派后面写「适用地图 龙宫游乐园、魔法学院」（通用的不写，直接构造带 `maps` 的 `Scheme` 和 `ParseResult`，不依赖分享格式解出地图）；导出列表里标了地图的一套悬停写「适用地图：……」；`WRITE_MAPS` 为真（默认）时勾了标地图的也不写提示；把它关掉时，勾选的方案里有标了地图的才在预览框下面写提示，取消勾选、只勾通用的都不写。
  - `EscapeTest`：名称、作者、说明、未能识别的筹码名里的网页标记和 `&` 都原样显示文字（不渲染）。
  - `WordingTest`：界面文字照对照表逐字写、都在模块顶层常量里；书面语，没有语气词、不用「我」「你」指代工具（「我的方案」是功能名，不算，`test_written_language`）。
- **检证对象**：`advisor/ui/scheme_share.py`（`SchemeShareWindow`、`ok_line`、`button_text`、各文字常量、预览框下面带地图的提示）；`advisor/schemes.py`（`Scheme`、`Scheme.maps`、`SchemeStore`、`Catalog`、`ChipCodes`、`parse_share`、`to_short`、`to_text`、`encode_payload`、`target_schools`、格式键 `FORMAT_SHORT`/`FORMAT_TEXT`、`WRITE_MAPS`）。
- **什么时候要跑**：改了 `advisor/ui/scheme_share.py`、`advisor/schemes.py`（分享格式、导入问题种类、上限），或改了这两个文件里的界面文字时。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_scheme_share`（上次约 2.7 秒）。
- **需要什么**：Qt 的离屏平台——不是 offscreen 平台时 `setUpModule` 整个文件跳过（不弹窗口、不碰系统剪贴板）。不需要识别样本。
- **输出怎么看**：跳过时原因是「只在 Qt 的 offscreen 平台上跑（不弹窗口、不碰系统的剪贴板）」。`test_large_pastes` 会把三次量到的 CPU 时间放进断言说明里。方案文件都放在测试沙箱的临时文件夹里，不碰真实文件。
- **出错时一般是哪里不对**
  - `ExportTest.test_preview_and_clipboard_both_formats` / `test_default_hero` 失败 → 预览、剪贴板或默认勾选逻辑改了（去看 `SchemeShareWindow.export_text`/`select_all`/`set_hero`）。
  - `ExportTest.test_empty_guide*` 失败 → 没有方案时那一段的显示条件（`_fill_list`）、放的位置（`_make_scheme_list`、`_make_scheme_flow`）或文字改了；只在某套皮肤下失败 → 那种排法里这一段被裁掉或把窗口撑大了。
  - `ImportTest.test_results_like_table` / `test_every_problem_sentence` 失败 → 识别结果的拼法或某种问题的文字改了（去看 `advisor/ui/scheme_share.py` 的 `PROBLEM_TEXT`、`advisor/schemes.py` 的问题种类）。
  - `ImportTest.test_import_creates_new_without_overwrite` / `test_round_trip_default_copies` 失败 → 导入覆盖了已有方案，或两种格式往返后内容不一致（去看 `SchemeStore.add`、`to_short`/`to_text`/`parse_share`）。
  - `ImportTest.test_large_pastes` 失败 → 一次粘贴的限制、超出说明，或识别/排版明显变慢（去看 `parse_share`、上限常量）。
  - `ImportTest.test_use_*`、`test_many_imported_rows_scroll` 失败 → 导入以后那几行（`_fill_done`、`_show_done`、`show_use_result`、`USE`／`USED_NOW`／`USED_LATER`／`USE_GONE`）或它们在识别结果区里的排法（`_fit_results`）改了。
  - `EscapeTest.test_markup_shown_as_text` 失败 → 某处的文字没做转义，网页标记被渲染了（去看 `SchemeShareWindow` 里拼富文本的地方、`button_text`）。
  - `WordingTest.test_table_texts` / `test_keys_and_limits_match_schemes` 失败 → 界面文字和对照表不一致，或句子里写的上限和 `advisor/schemes.py` 的常量不一致（后者要一起改）。
  - `WordingTest.test_written_language` 失败 → 导入/导出窗口的界面文字里用了语气词或「我」「你」。
  - **改了预期就该跟着改**：对照表 `TABLE`、`TABLE_FORMATS` 等是界面文字的标准写法；有意改文字时把常量、对照表和本节例子一起改。
