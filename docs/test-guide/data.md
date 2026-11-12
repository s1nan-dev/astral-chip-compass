# 测试指南 · 游戏数据

这是[《测试指南》](../测试指南.md)第八节的一章：每个测试文件一节，每节的结构见那里第八节的开头。


这一章管 data/ 下的资料库：筹码、角色、地图、规则、适配表、同系列表、畸变组件、识别图片清单，载入得对不对、字段自不自洽。改了 data/ 先跑这里的 test_check_data，再看其余。

## test_data.py：随程序发布的 data/ 载入结果、字段自洽和抽查值对不对

- **测试观点**（类 `DataTest`）：
  - 载入的数量、发放筹码、分组和 JSON 一致，且没有因为字段写错被跳过或记警告：`test_counts`。
  - 每张筹码、每个角色的字段自洽（稀有度在名单里、标签非空、职业筹码池属于四个职业、`school` 与 `group` 对应、`maps` 的有无对应「地图专属」、适配集合都在角色表里；角色权重四个职业齐全）：`test_fields`。
  - 抽查若干筹码和角色的数值、标签、适配集合：`test_spot_checks`、`test_heroes`。
  - 资料里的池表只来自筹码的 `pools` 字段、不按标签补全；池表规模固定：`test_class_pools_only_from_table`。
  - 适配表一致、随程序发布的资料没有警告：`test_synergy_is_consistent`。
  - 四档叫法和筹码方案同一套（必选 / 推荐 / 可选 / 不选），资料里不出现旧叫法：`test_tier_names`。
  - 每个特性都写了短句且合规：`test_trait_shorts`。
  - 收入折算引用的筹码名、特性名都存在：`test_income_names_exist`。
  - 阿尔的被动按「每 6 层星光」写（技能说明在 `mechanics/characters.md`）：`test_al_passive_six_layers`。
  - `meta.json` 只写 `data_date` 且是合法日期，「关于」窗口取它：`test_meta`。
  - 地图资料的进度上限、四个难度、按具体难度取哪一列、极限难度的进度上限和筹码任务数（只有星趴·梦想号、御魂庆典、水乡古镇有）、限时开关：`test_map_info`；畸变组件：`test_mutators`。
  - 降级载入（缺角色、引用不存在的筹码、格式错误）：`test_degraded_loading`；`keep_schools`（`true`、流派名的列表、`keep_schools_penalty`，写错时的警告）：`test_keep_schools_flag`；`school_skip`（筹码名的列表，写错时的警告）：`test_school_skip`；名称匹配：`test_name_matching`。
- **检证对象**：`advisor/data.py` 的 `load_db` 和常量（`RARITIES`、`CLASSES`、`SCHOOLS`、`TIERS`、`TIER_LABEL`、`DIFFICULTIES`、`NORMAL`、`SOURCE_GUIDE`），以及 `DB.resolve_chip` / `resolve_hero` / `resolve_map` / `active_maps`、`MapInfo.cap_for`；`advisor/version.py` 的 `data_date`；`data/` 下的 `chips.json`、`heroes.json`、`maps.json`、`mutators.json`、`meta.json`、`synergy.toml`、`rules.toml`。角色技能说明的核对用到 `mechanics/characters.md`。
- **什么时候要跑**：改 `data/` 下资料（尤其 `chips.json` / `heroes.json` 的数值、`maps.json`、`meta.json`、`synergy.toml` 的四档原文和理由、`rules.toml` 的 `[income]`）；改 `advisor/data.py` 的载入或常量；改资料同步日期。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_data`（上次约 0.2 秒）
- **需要什么**：没有特别要求。
- **输出怎么看**：同总则（没有特别的输出）。
- **出错时一般是哪里不对**：
  - `test_counts` / `test_fields` 失败 → 某张筹码或角色缺字段、类型不对被载入跳过了，或分组、发放标记改了。看 `advisor/data.py` 的 `_CHIP_FIELDS`、`_HERO_FIELDS`、`_load_chips`、`_load_heroes`。
  - `test_spot_checks` / `test_heroes` / `test_class_pools_only_from_table` 失败 → 资料里这些筹码的数值、标签、池被改了（数值和名单是资料的一部分，确切改了就把断言的预期一起改）。
  - `test_synergy_is_consistent` 失败 → 随程序发布的资料出了警告或适配表对不上（`synergy.toml` 引用了不存在的筹码、角色等）。
  - `test_tier_names` 失败 → 四档的内部键或显示名改了，或资料里还留着旧叫法、规则理由没以「筹码方案：」开头。看 `advisor/data.py` 的 `TIERS`、`TIER_LABEL`。
  - `test_trait_shorts` 失败 → 某个特性的短句缺失、超长，或带了数值、「」、特性内部名。看 `synergy.toml` 的 `[[trait]]` 和 `advisor/data.py` 的 `trait_shorts`。
  - `test_income_names_exist` 失败 → `rules.toml [income]` 里写了不存在的筹码名或特性名。
  - `test_al_passive_six_layers` 失败 → 阿尔被动的层数（6 层）在 `mechanics/characters.md` 或 `rules.toml` 里被改回 7。
  - `test_meta` 失败 → `meta.json` 多了字段、`data_date` 不合法，或 `advisor/version.py` 的 `data_date` 不再取它。
  - `test_map_info` 失败 → `maps.json` 的进度上限、四个难度、极限难度（`extreme`）、限时日期或开关改了，或 `MapInfo.cap_for`、`tasks_for` 的逻辑改了。
  - `test_mutators` 失败 → `mutators.json` 的组件内容、`scope`、`hint` 改了。
  - `test_degraded_loading` / `test_keep_schools_flag` / `test_name_matching` 失败 → 载入的降级边界、`keep_schools` / `keep_schools_penalty` / `school_skip` 的处理（`advisor/data.py` 的 `_keep_schools`、`_school_skip`），或名称匹配（`_norm`、`resolve_*`、`_resolve`）改了。
  - 有意改的：抽查的数值、名单、状态都是资料里有意的值，改了资料要把断言预期一起改。

## test_data_robust.py：资料写坏时降级成警告、跳过写坏的一项，必需的三份读不出来时抛错

- **测试观点**（按测试类）：
  - `DegradeTest`：写坏的一项记警告、跳过，其余照常载入（规则缺 `score` / `chips`；适配表某段误写成单个表；玩家思路档位、组合 `owned` 误写成字符串；`maps.json` 缺 `tasks`、`extreme` 写坏（按没有极限资料）或只写一项；可选的 `mutators.json` / `meta.json` 读不出；某个筹码缺字段）；推荐离不开的三份（`chips.json`、`heroes.json`、`rules.toml`）整份读不出来时抛 `DataError` 并写明文件名和位置。
  - `RulesCheckTest`：`rules.toml` 载入时核对——引用的名字改错、缺键、会除零的取值、数值写成 `inf`、`nan`（同角色的 `keep_schools_penalty`）都记警告；流派名的列表（`[official] keyword_schools`）写错时点名是哪几项。
  - `SourceCheckTest`：`rules.toml [sources]`（体系来源）写错、适配表规则的 `needs_source` / `without_source` 引用了不存在的来源名时记警告；整节没写时带条件的规则按一直没有来源计算。
  - `NameListTest`：资料里出现名单（稀有度、流派、职业）以外的值记警告、照常载入。
  - `MapListTest`：地图清单取「专属筹码出现过的地图」和 `maps.json` 的并集。
  - `EndlessCycleNameTest`：「循环往复」的筹码名按 `rules.toml [endless_cycle] chip` 认，不写死。
- **检证对象**：`advisor/data.py` 的 `load_db`、`DataError`、`_load_series`、`_load_maps`、`_load_chips`、`_load_heroes`、`_load_needs`、`_load_sources`、`_check_rules` / `_rule_value_problem`、`strict_problems`，以及 `DB.warnings` / `problems` / `maps` / `map_tasks` / `map_task_keys` / `map_info` / `mutators` / `meta` / `trait_rules` / `player_synergy` / `combos` / `sources`；`advisor/engine/reroll.py` 的 `default_rerolls`。
- **什么时候要跑**：改 `advisor/data.py` 的载入、降级或核对（`load_db`、`_check_rules`、各 `_load_*`）；改 `data/` 下资料的结构或字段；改 `rules.toml` 的键名和取值；改 `advisor/engine/reroll.py` 的 `default_rerolls`。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_data_robust`（上次约 1.7 秒）
- **需要什么**：没有特别要求。资料都复制到临时文件夹再改，不碰真的 `data/`。
- **输出怎么看**：用例用 `assertLogs("advisor.data")` 接住同时写日志的警告，再比对 `DB.warnings` 的整句；没有特别的文件输出。
- **出错时一般是哪里不对**：
  - `DegradeTest` 各用例失败 → 对应的降级不再发生（改成抛异常或静默），或跳过后的警告文字改了。看 `advisor/data.py` 里对应的 `_load_*`、`_field_problem`、`_section`。
  - `DegradeTest.test_required_files_unreadable` 失败 → 三份必需资料的 `DataError` 消息或 `file` 字段改了。看 `_required_json`、`_read_text` 和 `rules.toml` 的载入处。
  - `RulesCheckTest` 失败 → `_check_rules` 的必需键表（`_RULES_REQUIRED`）、取值检查（`_rule_value_problem`，如大于 0、正整数、有限的数）或引用名核对改了。
  - `SourceCheckTest` 失败 → `_load_sources` 的核对、`load_db` 里核对规则引用的来源名，或 `_rule_problem` 对 `needs_source` / `without_source` 的检查改了；`rules.toml [sources]` 的写法改了时用例里替换的原文要一起改。
  - `NameListTest` 失败 → `_load_chips` / `_load_heroes` 里名单外取值的警告改了。
  - `MapListTest` 失败 → `load_db` 里地图清单取并集那一步（筹码的 `maps` 加 `map_info`）改了。
  - `EndlessCycleNameTest` 失败 → `default_rerolls` 不再读 `rules.toml [endless_cycle] chip`（写死回「循环往复」）。
  - 有意改的：警告整句、`DataError` 的正则（第几行第几列）与 `advisor/data.py` 里的措辞同步，改了措辞两边一起改。

## test_check_data.py：改完游戏数据后跑它，逐项检查 data/ 写没写坏，并给新筹码追加编号

- **测试观点**：
  - `CheckDataTest.test_current_data_is_clean` / `test_check_table_has_each_check` / `test_main_exit_code_and_last_line` / `test_main_prints_each_problem_once`：随程序发布的 data/ 跑出来一条问题都没有；每一项检查都挂在 `CHECKS` 这张表里；`main` 的退出码（0 / 1）和最后一行（「筹码 N、角色 N、地图 N：没发现问题」/「发现 N 个问题」）；同一个问题只打一次。
  - 审查清单的指纹（`review/` 下两份）：`test_review_current`（清单和现在的数据、代码对得上时不报）、`test_review_goes_stale_when_data_changes` / `test_review_goes_stale_when_code_changes`（改了数据或推荐代码，两份清单都报过期并写明重新生成）、`test_review_missing_file` / `test_review_without_fingerprint_line`（清单不在、开头没有指纹行时照着报）、`test_review_fingerprint_scope`（data/ 下的数据文件（含子文件夹）和 advisor/engine/ 的代码算进指纹，和清单无关的下载图片清单、筹码编号表、斜着摆的角色照表、从卡面现裁角色照的裁法表、不用下载的健全版名单、字节码缓存、临时文件和备份不算；按 `/` 写的相对路径排序）、`test_fingerprint_covers_generator_code`（两个生成脚本用到的项目代码都算进指纹，只管程序目录、读写文件、今天是几号的几个模块除外）、`test_review_list_same_on_any_day` / `test_audit_pins_data_date`（生成时「今天」按资料日期算，换个日子生成的一字不差、出来时环境里的日期照旧）、`test_review_needs_data_date`（资料日期读不到、不是合法日期时退出，不退回运行当天）、`test_review_checked_only_for_program_data`（`--data` 指到别的文件夹时不查清单）、`test_current_review_lists_are_current`（仓库里现在这两份清单和现在的数据、代码对得上）。
  - 每一项检查各造一个错都能报出来：`test_rarity_wrong`、`test_missing_series_pair`、`test_missing_series_reason`、`test_new_chip_without_code`、`test_duplicate_code`、`test_chip_map_not_in_maps_json`、`test_granted_chip_without_map`、`test_bad_date`、`test_extra_meta_key`、`test_map_file_missing`（`file` 指向的机制文档不在，`file` 写成别的类型或空着）、`test_map_tasks_mismatch`（文档里的任务名和 `tasks` 对不上；`tasks` 空着、类型不对时只报那一条，不把文档里的任务全列成「多了」）、`test_map_tasks_tolerant`（表的行首行尾有空白、全角半角、「任务:」半角冒号的整句都认，任务名不同照样报）、`test_map_tasks_unreadable`（表看不懂时报「看不懂这张表」、不抛异常）、`test_code_en_matches_chips`（编号表里现役筹码那一行的 `en` 和 `chips.json` 不一致，已移除的不核对）、`test_photo_tilt`（斜着摆的角色照表 `photo_tilt.json`：文件名不是识别图片清单里的角色照、角度不是 -45～45 之间的数、文件不在、结构不对、读不出来）、`test_photo_tilt_manifest`（文件名照数据文件夹里的识别图片清单核对、只认 `dir` 为 `heroes` 的项，文件夹里没有清单时按程序自己的，清单读不出来时报一行）、`test_photo_tilt_same_as_recognition`（文件名和角度上限同认角色时用的 `advisor/vision/hero.py`）；从卡面现裁角色照的裁法表 `card_crops.json`：`test_card_crops_current`（随程序的表每项的卡面是同一编号、同一皮肤，转了角度的只有阿兰娜泳装的健全版和派德曼「酷暑难耐」、`photo_tilt.json` 里有同样的 17.1°、-42.3°，`card_of` 的换法）、`test_card_crops_format`（卡面不是同一编号同一皮肤、键不是角色照的文件名、`affine` 不是 6 个数、多了别的键、整项不是表、文件不在、结构不对、读不出来，各报一行）、`test_card_crops_matrix`（矩阵压成一条线、翻了过来、宽少写一位数、高多写一位数、拉斜、角色照的中心落在卡面外面）、`test_card_crops_tilt`（转了角度却没在 `photo_tilt.json` 里、角度差超过 1°、摆正的写了角度都报，差不过 1° 的、角度写错由那一项报的、表读不出来的不报）、`test_card_crops_manifest`（清单里没有这一项、没带 `crop`、卡面或裁法不一样、清单里现裁的项表里没有、清单读不出来，写成 0 和 0.0 的算一样；放在备用地址上的看它的 `fallback`，没有 fallback、fallback 里没有裁法的算没有）、`test_card_crops_same_as_tools`（生成清单、取图的工具读的是同一份表，角色照大小同裁图）；首次运行不下载的健全版名单 `sfw_skip.json`：`test_sfw_skip_current`（随程序的名单都是健全版、两个分数在口径里，识别图片清单里一张都没有，名单里现裁、斜着摆的那套裁法和角度照留）、`test_sfw_skip_format`（不是健全版的文件名、分数不是两个 -1～1 的数、既不是现裁的又不在开发目录里、同一套的普通版也不认识，各报一行；开发目录里一张角色照都没有时不核对后一条；文件不在时报在最前面，结构不对、读不出来时报一行）、`test_sfw_skip_dev_from_manifest`（开发目录里只有程序第一次运行时照清单下的图片、名单里的健全版都没下时，随程序的名单不报，普通版也不认识的照样报）、`test_sfw_skip_against_manifest`（名单里的又在清单里时报一行；名单里的现裁健全版可以没有清单项，拿出名单以后斜着摆的、现裁的各报一行）、`test_sfw_skip_broken_no_cascade`（名单文件不在、读不出来、结构不对时整份数据只报根子那一行，斜着摆的、现裁的两项不跟着把名单里的健全版报成「不在清单里」，普通版照常核对）、`test_broken_manifest_file_field`（识别图片清单里 `dir` 为 `heroes` 的项 `file` 写成列表等不是文字时，名单那一项报一行，现裁那一项不拿它当键，都不出错停下）、`test_sfw_skip_same_as_tools`（生成清单、算名单的工具读写同一个文件，不随发行版）。
  - 手改时容易只改一处的地方：`test_fit_names_are_heroes`、`test_duplicate_chip_and_hero`、`test_group_school_maps`、`test_removed_and_current`、`test_hero_weights_and_aliases`、`test_hero_groups`、`test_progress_cap_matches_caps`、`test_progress_caps_keys`、`test_map_dates_and_flags`、`test_map_code_duplicate`、`test_map_code_out_of_range`、`test_map_with_slug_without_code`、`test_arena_with_code`、`test_code_table_rows`。
  - 写坏类型时不抛异常、一处错不报出一长串：`test_wrong_types_do_not_raise`、`test_skipped_item_reported_once`、`test_warnings_name_their_file`、`test_missing_and_broken_files`。
  - 排法（`--format`）不对时报一行、重排后内容不变、换行照原文件、再跑一次结果一样：`test_layout_wrong_reports_and_format_fixes`、`test_format_keeps_line_endings`；命令行带 `--format` 时先重排、打印重排了哪个文件，再照常检查：`test_main_format_flag`；同一个表里有写了不止一次的键（复制一张地图忘了改名等）时报出是哪一项、不提示重排，`--format` 不重排这个文件、文件一个字节不变：`test_duplicate_keys_not_reformatted`；`data/` 下有叫 `*.json` 的文件夹、读不出来的文件时跳过、不抛异常：`test_layout_skips_what_it_cannot_read`。
  - `--add-codes` 只追加、写法照原文件、出错时原文件不动：`test_add_codes_appends_only`、`test_add_codes_keeps_line_endings`、`test_add_codes_write_failure_keeps_file`、`test_add_codes_refuses`。
- **检证对象**：`tools/check_data.py` 的 `collect_problems`、`CHECKS` 和各项检查（`check_chips`、`check_granted`、`check_heroes`、`check_series`、`check_codes`、`check_maps`、`check_map_files`、`check_map_tasks`、`check_mutators`、`check_sfw_skip`、`check_photo_tilt`、`check_card_crops`、`check_meta`、`check_layout`，以及 `chip_problems`、`tilt_problems`、`manifest_photos`、`manifest_items`、`crop_problems`、`affine_problems`、`card_of`、`crop_rotation`、`sfw_skip_problems`、`sfw_skip_names`、`_skipped_or_sfw`、`dev_role_photos`、`hero_problems`、`map_problems`、`map_file_problems`、`map_task_problems`、`_doc_section`、`_doc_tasks`、`codes_problems`、`layout_problems`、`format_files`、`add_codes`、`check_review`、`review_problems`、`main`）；排法由 `tools/json_layout.py` 的 `format_json`、`rendered_bytes` 定；被检查的载入结果来自 `advisor/data.py` 的 `load_db`（`DB.warnings`、`DB.problems`）；机制文档从 `advisor/paths.py` 的 `mechanics_dir()` 下的 `maps/` 取；编号上限来自 `advisor/schemes.py` 的 `CODE_MAX`；审查清单那几条用到 `tools/make_review.py`（`fingerprint`、`fingerprint_files`、`FINGERPRINT_LINE`、`recorded_fingerprint`、`FINGERPRINT_SOURCES`、`REVIEW_NAME`、`AUDIT_NAME`、`REGEN_HINT`、`pinned_today`、`data_date`、`write_review`）、`tools/audit_synergy.py` 和 `advisor/clock.py`（`ENV`、`today`）。
- **什么时候要跑**：改 `tools/check_data.py` 或 `tools/json_layout.py`；改 `data/` 下任何资料（`chips.json`、`heroes.json`、`maps.json`、`mutators.json`、`meta.json`、`chip_codes.json`、`photo_tilt.json`、`card_crops.json`、`sfw_skip.json`、`series.toml`、`rules.toml`、`synergy.toml`）；重新生成 `data/assets_manifest.json` 之后（现裁那几项要和 `card_crops.json` 对得上，`sfw_skip.json` 里的不能在清单里）；改 `mechanics/maps/` 里地图文档的「任务」表或 `maps.json` 的 `file`；改 `advisor/data.py` 里载入时的降级和警告文案；加新筹码、新角色、新地图之后；用 `--format` 重排数据之后；改了推荐代码或 `data/` 之后要重新生成 `review/` 下两份清单（`tools/make_review.py`），忘了生成时指纹那几条会报。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_check_data`（上次约 13 秒）
- **需要什么**：没有特别要求。测试只读真的 `data/`，造的错都写进沙箱临时文件夹里的 data 副本；指纹那几条造一棵小的程序目录树（含 `review/`），`test_current_review_lists_are_current` 读真的 `review/`。健全版名单那一项检查另看开发目录 `assets/heroes` 里有哪些角色照（没下识别图片时这一条不核对）；`test_sfw_skip_format` 把它换成随程序的名单里的那些、`test_sfw_skip_dev_from_manifest` 换成清单里的那些，不看这台电脑上下了没有。
- **输出怎么看**：用例把 `check_data.main` 的标准输出接到内存里逐行比对（最后一行、每条问题一行），并核对没有重复打印。指纹过期时多出的问题形如「review/<清单>：生成依据的指纹和现在的数据、代码对不上」（后面接重新生成的提示），缺清单或没有指纹行时另有一句；排法不对时形如「<文件>：排法和标准写法不一样，运行 python tools/check_data.py --format 重排」，有写了不止一次的键时形如「<文件>：「<哪一项>」写了不止一次，程序只认最后一个……」；地图文档那边有「file……不存在」「「任务」表和 maps.json 的 tasks 对不上」「看不懂这张表」几种。没有别的临时文件。其余同总则。
- **出错时一般是哪里不对**：
  - `test_current_data_is_clean` 失败 → 真的 `data/` 里有一处现在对不上，多半是刚改资料只改了一处。看 `tools/check_data.py` 对应的 `check_*` 和 `advisor/data.py` 的 `load_db`。
  - `test_check_table_has_each_check` 失败 → 往 `CHECKS` 加了/删了检查函数，或改了函数名。
  - `test_main_exit_code_and_last_line` / `test_main_prints_each_problem_once` 失败 → `main` 的收尾文字、退出码，或载入警告的日志去处（`NullHandler`）被改。
  - `test_rarity_wrong`、`test_missing_series_pair`、`test_missing_series_reason`、`test_new_chip_without_code`、`test_duplicate_code`、`test_chip_map_not_in_maps_json`、`test_granted_chip_without_map`、`test_bad_date`、`test_extra_meta_key` 失败 → 对应的 `check_*` 少报或漏报了这一类，去看那个 check 和 `advisor/data.py` 载入时对应的警告。
  - `test_fit_names_are_heroes`、`test_duplicate_chip_and_hero`、`test_group_school_maps`、`test_removed_and_current`、`test_hero_weights_and_aliases`、`test_hero_groups`、`test_progress_cap_matches_caps`、`test_progress_caps_keys`、`test_map_dates_and_flags`、`test_map_code_duplicate`、`test_map_code_out_of_range`、`test_map_with_slug_without_code`、`test_arena_with_code`、`test_code_table_rows` 失败 → `chip_problems` / `hero_problems` / `map_problems` / `codes_problems` 的规则或文案改了。
  - `test_map_file_missing` / `test_map_tasks_mismatch` / `test_map_tasks_tolerant` / `test_map_tasks_unreadable` 失败 → `map_file_problems` / `map_task_problems` / `_doc_section` / `_doc_tasks` 的判定或文案改了（文档里「任务」表的写法也在这里认）。
  - `test_code_en_matches_chips` 失败 → `codes_problems` 的 `en` 核对改了。
  - `test_photo_tilt*` 失败 → `check_photo_tilt` / `tilt_problems` / `manifest_photos` 的判定或文案改了，或文件名、角度上限和 `advisor/vision/hero.py` 的 `TILT_FILE`、`TILT_MAX` 不一致了。
  - `test_card_crops*` 失败 → `check_card_crops` / `crop_problems` / `affine_problems` 的判定或文案改了（合理范围 `CROP_SCALE_MAX`、角度的容差 `CROP_TILT_TOLERANCE`），或改了 `data/card_crops.json`、`photo_tilt.json` 以后没重新生成清单（`tools/make_assets_manifest.py`）；`test_card_crops_current` 失败也可能是表里多了转角度的一套（有意加的，断言里的那份名单一起改）。
  - `test_sfw_skip*` / `test_broken_manifest_file_field` 失败 → `check_sfw_skip` / `sfw_skip_problems` 的判定或文案改了（名单读不出来时不连带报的是 `_skipped_or_sfw`），或改了 `data/sfw_skip.json`（`tools/check_sfw_cover.py --write`）以后没重新生成清单；`test_sfw_skip_current` 失败也可能是名单里的分数不在 `tools/check_sfw_cover.py` 的口径（`OWN_MIN`、`RIVAL_MAX`）里了：重算一遍名单。
  - `test_layout_wrong_reports_and_format_fixes` / `test_format_keeps_line_endings` / `test_main_format_flag` 失败 → `tools/json_layout.py` 的排法、`layout_problems` / `format_files`，或 `main` 里 `--format` 的接法改了。
  - `test_duplicate_keys_not_reformatted` / `test_layout_skips_what_it_cannot_read` 失败 → `_layout_source`（读文件、找写了不止一次的键 `_table_keeping_dups` / `_dup_keys`）或 `layout_problems` / `format_files` 遇到它们时的处理改了。
  - `test_wrong_types_do_not_raise` 失败 → 载入时遇到类型不对不再降级成警告、而是抛异常了（看 `advisor/data.py` 的 `_field_problem`、`_load_chips`、`_load_maps`）。
  - `test_skipped_item_reported_once` / `test_warnings_name_their_file` 失败 → 略去跟着来的「不存在」那套（`check_data.load_warnings`、`_MISSING_REF`、`_SKIPPED_ITEM`）或补文件名的 `_with_file` 改了。
  - `test_add_codes_*` 失败 → `add_codes` 的追加/拒绝逻辑、换行处理或原子写（`advisor/fileio.py` 的 `write_bytes`）改了。
  - `test_review_current` / `test_current_review_lists_are_current` / `test_review_goes_stale_*` / `test_review_missing_file` / `test_review_without_fingerprint_line` 失败 → 改了数据或推荐代码以后忘了重新生成 `review/` 下两份清单（跑 `tools/make_review.py`），或清单开头少了指纹行；指纹算哪些文件的规则改了时，`test_review_fingerprint_scope`、`test_fingerprint_covers_generator_code` 要跟着改（`make_review.FINGERPRINT_SOURCES`）。
  - `test_review_list_same_on_any_day` / `test_audit_pins_data_date` / `test_review_needs_data_date` 失败 → 生成清单时「今天」的取法改了（应按 `data/meta.json` 的资料日期，读不到或不是合法日期时退出）。
  - 有意改的：断言里的提示文字（「请改成角色的 name」这类）是和 `tools/check_data.py` 里同步写的中文，改了提示就两边一起改；这些不是数值预期。

## test_series.py：同系列筹码的严格上位替代、对比行、资料条件、写全检查，以及推荐和命令行

- **测试观点**（按测试类）：
  - `SeriesTableTest`：随程序发布的 `series.toml` 对不对（20 对、15 对算严格上位替代 5 对不算、每对写了理由且说到要点、两张稀有度由低到高且图标相同、算的满足资料条件、要点和悬停说明的写法）。
  - `SeriesLoadingTest`：`series.toml` 写得不对时只记警告、忽略写坏的那一对（文件没有、格式错误、各种写法不对、严格链要写全、字段不认识、`reason` 可以不写、`[[starlight_use]]`）。
  - `StrictRuleTest`：严格上位替代的资料条件 `strict_problems`（标签不计「通用」、流派相同、高一级没有低一级没有的职业分值），以及表和规则的交叉、极端配置（全算 / 全不算 / 互相矛盾）。
  - `SeriesBuildCheckTest`：`tools/check_data.py` 的同系列检查 `series_problems`（图标相同的两张两两都要写一对、每对都要写理由、漏写时一次报全）。
  - `SeriesAdviceTest`：推荐里 `mark_series` 的效果（被替代的推荐度为 0、对比行、刷新比较的对象、只改本次三选一、重复或未收录的输入照常）。
  - `SeriesCliTest`：命令行的渲染（被替代的一行用「—」代替推荐度、对比行、刷新的比较对象、`--json`）；`CompareTextTest`：对比行「比X多……」的空格。
- **检证对象**：`data/series.toml`；`advisor/data.py` 的 `SeriesPair`、`StarlightUse`、`strict_problems`、`_load_series`、`_load_starlight_uses`、`load_db`；`advisor/engine/advice.py` 的 `mark_series`；`advisor/engine/advice_text.py` 的 `compare_text`、`shown_percents`；`advisor/engine` 的 `advise`、`Scorer`、`REROLL`、`LOW_NOTE`、`STARLIGHT_NO_USE`、`tie_key`；`tools/check_data.py` 的 `series_problems`；`advisor/cli.py` 的 `render` / `main`；`advisor/pool.py` 的 `State`、`Teammate`。
- **什么时候要跑**：改 `data/series.toml`；改 `advisor/data.py` 的 `strict_problems` 或同系列载入；改 `advisor/engine/advice.py` 的 `mark_series`；改 `compare_text` / `shown_percents`；改 `tools/check_data.py` 的 `series_problems`；改命令行的渲染。加筹码或改图标时也要跑（同系列表要配对）。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_series`（上次约 10 秒）
- **需要什么**：没有特别要求。
- **输出怎么看**：同总则（没有特别的输出）。造的系列表写进临时文件夹。
- **出错时一般是哪里不对**：
  - `SeriesTableTest` 失败 → `series.toml` 的对、理由或要点改了；改资料时把测试文件里 `STRICT` / `NOT_STRICT` 那份名单和理由一起改（有意改的）。
  - `SeriesLoadingTest` 失败 → `_load_series` / `_load_starlight_uses` 的警告规则或文案改了。
  - `StrictRuleTest` 失败 → `strict_problems` 的三条判定改了。
  - `SeriesBuildCheckTest` 失败 → `series_problems`（写全检查）改了。
  - `SeriesAdviceTest` / `SeriesCliTest` 失败 → `mark_series`、`advise` 的推荐度分派、`compare_text` 或 `cli.render` 改了。
  - `CompareTextTest` 失败 → `compare_text` 的空格规则改了。
  - 有意改的：`ALANA_LAST`、`AL_130853` 这类实测局面的百分比（58% / 29% / 13% 等）是与资料和引擎同步的预期，改了算法或资料要一起改。

## test_assets_manifest.py：识别图片写盘的加固和本地覆盖清单的读写

- **测试观点**（按测试类）：
  - `SaveTest.test_save_flushes_and_renames`：写好的图片先刷盘再改名，内容对、没有留下 `.part`。
  - `SaveTest.test_one_unwritable_image_does_not_stop_the_rest`：一张写不进只算这一张失败、其余照常写进；同一次运行里再下载这一张能写进。
  - `LocalManifestTest.test_load_local_drops_bad_types`：覆盖清单读的时候丢掉类型不对的项，顶层不是对象或不是 JSON 时按空清单（四节都空：`updated`、`gone`、`cropped`、`failed`；现裁记录的读法见 `test_card_crop.py`，个别下载失败的日期见 `test_download_fail.py`）。
  - `LocalManifestTest.test_bad_fields_do_not_break_missing`：更新记录里地址、大小、校验值类型不对的项丢掉后，`missing()` 照常算出缺哪几张、不抛异常。
  - `LocalManifestTest.test_update_local_is_atomic`：写覆盖清单是「临时文件 + 刷盘 + 替换」，读的时候拿锁（可重入，不会自己等自己）。
  - `LocalManifestTest.test_gone_records_forms`：wiki 上已删除的记录，以前的写法（日期的文字）和现在的写法（日期、文件页都是文字的对象）都收，类型不对的丢掉。
  - `LocalManifestTest.test_unknown_sections_kept`：覆盖清单里这一版不认识的顶层节（以后的版本加的）读进来原样留着，记一笔、记个别下载失败、点「重试」去记号整份写回以后还在、内容不变；认得的几节（`LOCAL_SECTIONS`）照旧逐项核对；读的地方（`missing`）不受影响。发行版的这份记录在个人目录里、几个版本共用。
  - `LocalManifestTest.test_busy_local_manifest_retried` / `test_local_manifest_retry_gives_up`：覆盖清单被别的程序一直开着（`fileio` 自己等了约 1 秒仍写不进）时，按 `LOCAL_RETRY_DELAYS` 用不等的写法再试，报 `Busy` 才接着试，写成了照常记下；试满了、只读这类马上就报的、磁盘满了都不再试，不出错。`fileio.write_text` 换成按次序出错的替身，再试之前不真等。
- **检证对象**：`advisor/assets.py` 的 `_save`、`download`、`_get_image`、`_keep`、`load_local`、`_usable_update`、`_usable_gone`、`_write_local`、`LOCAL_RETRY_DELAYS`、`_update_local`、`_note_failed`、`forget_recent`、`missing`、`effective`、`is_present`、`AssetItem`、`Manifest`、`LOCAL_MANIFEST`、`LOCAL_SECTIONS`、`WRITE_FAILED`、`PNG_MAGIC`；`advisor/fileio.py` 的 `write_bytes`、`_sync`；`advisor/paths.py` 的 `assets_dir`、`download_dir`（开发版里下载的识别图片的位置就是 `assets_dir`，测试换掉 `assets_dir` 就换走了；发行版放在个人目录里，见 `test_storage.py` 的 `DownloadDirTest`）。
- **什么时候要跑**：改 `advisor/assets.py`（写盘、覆盖清单、下载流程）；改 `advisor/fileio.py` 的写盘；改 `advisor/paths.py` 的 `assets_dir`、`download_dir`。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_assets_manifest`（上次约 0.08 秒）
- **需要什么**：不联网（下载换成假的取图函数）；用临时文件夹当 assets 目录。
- **输出怎么看**：同总则（没有特别的输出）。
- **出错时一般是哪里不对**：
  - `test_save_flushes_and_renames` 失败 → `_save` 不再先刷盘（`fileio.write_bytes` 的刷盘或临时文件后缀改了），或留下了 `.part`。
  - `test_one_unwritable_image_does_not_stop_the_rest` 失败 → `download` 里单个文件写盘出错的处理（记 `WRITE_FAILED`、不连累其余、重试能写进）改了。
  - `test_load_local_drops_bad_types` / `test_bad_fields_do_not_break_missing` / `test_gone_records_forms` 失败 → `load_local` / `_usable_update` / `_usable_gone` 的过滤规则改了。
  - `test_busy_local_manifest_retried` / `test_local_manifest_retry_gives_up` 失败 → `_write_local` 再试的做法（先照平常的写法、再用不等的写法按 `LOCAL_RETRY_DELAYS` 试，只认 `fileio.Busy`）或 `fileio` 被占用时报的异常改了。
  - `test_update_local_is_atomic` 失败 → `_update_local` 的写盘方式或锁改了。
