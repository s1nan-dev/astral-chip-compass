# 测试指南 · 一致性和其他

这是[《测试指南》](../测试指南.md)第八节的一章：每个测试文件一节，每节的结构见那里第八节的开头。


这一章管跨文件的核对（版本号、首页和两份说明里的路径、样本标注、使用说明里引用的界面文字、界面文字的语体、识别记录和诊断包的说明）、测试和工具的私有名字守卫、推荐对比工具几个领域的台账比对、开发工具（校准、对着游戏窗口调试、自编 OpenCV 的逐位比对、缺识别样本时的说明），以及几处跨模块接通的测试。

## test_project_consistency.py：只读地核对项目文件之间的一致性（版本号、首页和两份说明里的路径、样本标注、使用说明里引用的界面文字、界面文字的语体、识别记录和诊断包的说明）

- **测试观点**
  - `VersionTest`：版本号只在 `advisor/version.py` 定义，首页（`README.md`）第一节、`THIRD_PARTY_NOTICES.md` 跟着它；署名（`AUTHOR`）也得一字不差；README 第一节写的资料同步日期 = `data/meta.json`（关于窗口显示的那个）。
  - `DevNoteTest`：随发行版发布的文字里没有开发路径（`docs`、`tools`、`tests` 下的文件，`.py` 源码）和停用的「重选」（口径是打包脚本的 `DEV_NOTE`）：`data/*.json` 的说明文字（`data/*.toml` 由 `test_release` 查），以及 `docs/使用说明.md`（生成「使用说明.html」的那份，图片只查说明文字）和 `THIRD_PARTY_NOTICES.md`。
  - `SamplesTest`：`samples/` 的截图和三份标注互相对得上，标注里的名称都在资料库里，标注里不写玩家昵称（缺样本时整个类跳过）。
  - `TerminologyTest`：`docs/使用说明.md` 里引用的提示原文是悬浮窗上实际显示的文字（面板、任务栏、下载、未选首推的原因、角色、地图、页签、补录、星数增加、新一局、难度胶囊、对局结束、设置窗口和热键、热键被占用、这次运行里一直留着的三条提示、下载被拦和个别图片下不了、一行显示的摘要、开发版的标题）；软件维护说明里的校准提醒照 `rules.toml` 的局数写；几处写法确实在源码里（新一局和对局结束的依据由 Session 写，撤销、补录、面板、地图依据的句子在悬浮窗里，难度推算和矛盾的说明在资料和记账里）；截取方式「原因」的四种写法在设置窗口、诊断包、提示三处逐字相同；切换模式后的提示和设置窗口里的说明是同一句；底栏「设置」按钮的悬停说明按窗口里的各项写；下载那几句不说检查网络、不用「到点」；「允许截图」已取消；「导出诊断」的悬停说明写明诊断包含本局和上一局。
  - `RecommendCardTest`：「推荐」卡片上「刷新」的标签是悬浮窗上实际显示的文字（使用说明「推荐的写法和规则」一节不写总分和分值）；标签和悬停明细不写分值，按分值的绝对值排、按色调着色，概率写百分数不写「几成」，很偏时写「不到 1%」「超过 99%」；推荐流派、队友、同系列、未收录、循环往复、畸变组件、职业倾向、前瞻几处的写法（不写计分用语），使用说明照抄的标签；队友刚需名单。
  - `SchemeCardWordingTest`：「筹码方案」卡片相关的界面文字和测试里的对照表逐字相同，悬浮窗的界面文字里没有和现有布局对不上的说法（例如「左侧下拉框」）；星币的状态胶囊、摘要、悬停的写法（`text.py` 的顶层字面量）和使用说明；使用说明里「识别」卡片、底栏、「筹码方案」卡片三个按钮、角色待识别的写法。
  - `SchemeWordingTest`：使用说明「筹码方案的规则」「编辑筹码方案的操作」「导入和导出的规则」几节引用的写法（卡片、位次、提醒和刷新那一行、档位前瞻那一条的短句和悬停整句、编辑窗口、导入/导出窗口、方案文件的提示）是界面上实际显示的文字；能导入的一套的写法；方案文件来自更新版本时几处只读提示是同一句；旧档名「必拿」「不推荐」和旧前缀「玩家思路：」不再出现，四档的叫法几处一致。
  - `ReadmePathsTest`：首页（`README.md`）、`docs/使用说明.md`、`docs/软件维护说明.md` 里反引号、代码块、链接和图片提到的项目文件都存在（`.tmp`、`.venv`、`dist`、`logs` 等运行时目录除外）；链接和图片（不算锚点和网址）从所在文件的文件夹算找得到，和 GitHub 上点开时一样（docs 下的两份说明写 `manual/…`、`使用说明.md`，写成 `docs/…` 在网页上点不开）；「目录结构」表在 `docs/软件维护说明.md` 里。不随仓库发布的识别样本、本机数据和运行时下载的识别图片（`NOT_SHIPPED`：`samples`、`tests/data/coins`、`tests/data/private.json`、`assets/heroes`、`assets/profiles`、`assets/chips`）只在本机整个没有时才跳过核对，不当作写错。
  - `DiagnosticsDocsTest` / `AssetsManifestTest`：`docs/使用说明.md` 的「隐私」「反馈问题」「文件夹说明」和「关于」窗口的隐私说明一致（含筹码三选一有候选筹码没识别出来时那一帧只留在内存里、导出诊断时另带一张的两处写法，以及记下选中项用的那一帧、被临时遮挡而没有采用的那一帧那一句 `version.PRIVACY_SELECT_SHOTS`、对局中途像一局打完时的那一帧那一句 `version.PRIVACY_FINISH_SHOT`；两处都写全识别记录里会记的游戏窗口标题（`advisor/ui/winapi.py` 的 `GAME_TITLES`），并写明不读、不记别的窗口的标题；两处都写到诊断包另带识别记录里最近 12 小时的每分钟统计；每分钟的处理器占用记录存在哪那一句 `version.PRIVACY_USAGE` 两处逐字相同、文件名和 `advisor/usage.py` 的 `FILE_NAME` 一致，「文件夹说明」也写到它（`test_privacy_usage_sentence`）；「反馈问题」写到另带的 select-picked、select-covered、finish-look 几张，诊断包大小按通常带的几张整幅截图说）、`data/assets_manifest.json` 的条目和汇总数字和 `docs/使用说明.md` 对得上（都取自 BWIKI、不带 `api`，BWIKI 的项文件页名就是文件名，从卡面现裁的项文件页是同一编号、同一皮肤的卡面）。
  - `UiWordingTest`：界面文字用书面语，没有口语语气词，不用「我」「你」指代工具（「我的方案」除外）；几处偏口语的说法（「更常出」「到账」「快速处理」）在界面文字和资料里都不出现。
  - `LicenseTextTest`：首页「许可证与资料来源」一节写明程序代码和资料的许可证、放在末节，「从这里开始」链到使用说明的网页版（GitHub Pages，另留 `docs/使用说明.md` 原稿的链接）和 `docs/软件维护说明.md`；「免费软件」声明照 `version.FREE_NOTICE` 写在使用说明的「声明」里；限制使用的说法（不准商用、不提供源码一类）和「使用条款」这个标题都不出现在首页、两份说明、第三方声明和「关于」窗口里。
- **检证对象**：只读项目文件——`README.md`、`docs/使用说明.md`、`docs/软件维护说明.md`、`THIRD_PARTY_NOTICES.md`、`data/*.json`（含 `assets_manifest.json`）、`tools/build_release.py`；以及源码 `advisor/version.py`、`advisor/paths.py`、`advisor/assets.py`、`advisor/gamelog.py`、`advisor/schemes.py`、`advisor/data.py`、`advisor/engine/`、`advisor/session/`、`advisor/prefs.py`、`advisor/ui/`（含 `text.py`、`overlay.py`、`overlay_parts/`、`settings/dialog.py`）、`advisor/diagnostics.py`。界面文字用 `ast` 从源码里取，不导入 PySide6、不跑识别。
- **什么时候要跑**：改了版本号、`README.md`、`docs/使用说明.md`、`docs/软件维护说明.md`、`THIRD_PARTY_NOTICES.md`、`data/*.json`、`tools/build_release.py`，或改了任何界面的文字常量（`advisor/ui/`、`advisor/engine/`、`advisor/session/`、`advisor/prefs.py`）、改了 `samples/` 的截图或标注时。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_project_consistency`（上次约 2 秒）。
- **需要什么**：`SamplesTest` 要 `samples/labels.json`、`samples/stars_labels.json`、`samples/panel_labels.json` 以及它们列出的截图，缺任何一个时整个类跳过（`@unittest.skipUnless(samples.samples_ok(...), samples.samples_reason(...))`，原因形如「没有识别样本：缺 samples\stars_labels.json（准备方法见 docs/测试指南.md「准备识别样本」）」）；样本目录用 `ACC_SAMPLES_DIR` 指（没设时是 `samples/`）。其余各条只读项目文件，不需要样本；`ReadmePathsTest` 里不随仓库的样本和本机数据只在本机没有时跳过。
  - 用到的标注字段：`labels.json`——`file`、`size`、`offer`、`owned`、`badge`、`badge_unexplained`、`picked`、`selected`、`hero`、`map`、`rarity`、`probabilities`、`source`、`team`、`scene`、`tasks`、`star`，以及整份的 `_说明`；`stars_labels.json`——`file`、`own_row`、`rows`、`own`、`actual`、`hero`、`players`、`bots`；`panel_labels.json`——`file`、`selected_tab`、`tab_count`、`self_index`、`is_panel`、`owned`、`hero`。
- **输出怎么看**：`SamplesTest` 缺样本时整类跳过，原因同上一节。别的条失败时 `subTest` 会写清是哪个文件、哪一处不一致（`ReadmePathsTest` 失败写出「哪个文件：哪个写法」）。没有特别的临时文件。
- **出错时一般是哪里不对**
  - `VersionTest.*` 失败 → 版本号、署名、资料同步日期或某处文档漏改（去看 `advisor/version.py`、`README.md`、`docs/软件维护说明.md`、`THIRD_PARTY_NOTICES.md`）。
  - `DevNoteTest.*` 失败 → 使用说明、`THIRD_PARTY_NOTICES.md` 或 `data/*.json` 的说明文字里写了开发路径或「重选」（失败信息写明哪个文件、第几行或哪个键）。
  - `SamplesTest.*` 失败 → 新增了没在 `labels.json` 里登记的截图，或标注里的名称/尺寸/候选数不对（去看 `samples/` 与三份标注）。
  - `TerminologyTest` / `RecommendCardTest` / `SchemeCardWordingTest` / `SchemeWordingTest` 失败 → 悬浮窗上的提示、「刷新」标签、理由标签、方案卡片、编辑和导入/导出窗口或位次的文字改过而使用说明没同步，或常量和测试里的对照文字不一致（去看 `advisor/ui/overlay_parts/wording.py`、`advisor/ui/text.py`、`advisor/ui/settings/dialog.py`、`advisor/ui/scheme_editor/notes.py`、`advisor/ui/scheme_share.py`、`advisor/engine/`、`advisor/session/`、`advisor/schemes.py`）；报「源码里没有这一句」（`assertIn(..., self.session_templates)` 一类）时，是 Session、悬浮窗或资料里的句子改了写法，测试里的句子要跟着改。
  - `ReadmePathsTest` 失败 → 首页、使用说明或软件维护说明里写了仓库里不存在的文件，或链接/图片没从所在文件夹算（docs 下写成 `docs/manual/…` 在网页上点不开）——去看对应那份文件；不随仓库的样本、本机数据和下载的识别图片只在本机没有时跳过，不算失败。
  - `DiagnosticsDocsTest` 失败 → 使用说明的「隐私」「反馈问题」和「关于」窗口的隐私说明不一致（去看 `advisor/gamelog.py`、`advisor/version.py`）。
  - `AssetsManifestTest` 失败 → `data/assets_manifest.json` 的字段、汇总数字或与 `tools/build_release.py` 的 `FALLBACK_BUNDLED`、`docs/使用说明.md` 里的数字不一致。
  - `UiWordingTest.*` 失败 → 界面文字里出现了语气词、用「我」「你」指代工具，或用了上面那几处口语说法（去看对应文字常量、`data/synergy.toml`）。
  - `LicenseTextTest` 失败 → 首页「许可证与资料来源」一节的说法、链接或位置改了，「免费软件」声明和使用说明对不上，或限制使用的说法、「使用条款」这个标题出现了。
  - **改了预期就该跟着改**：这些多是「两处文字必须一字不差」的核对；有意改文字时，把代码常量、首页和两份说明以及本文件里的例子一起改。

## test_private_names.py：守卫测试，核对测试和工具里用到的私有名字还都在，并扫出没登记的私有用法

- **测试观点**
  - `PrivateModuleNamesTest`：三个模块的**模块级**私有名字（`advisor.ui.overlay._alive`、`advisor.session._event_record`、`advisor.prefs._write_settings`）都还取得到；要 `mock.patch` 的写清单还一致；`module_patch_problem` 自己的样例判断正确。
  - `PrivateInstanceNamesTest`：`Overlay`、`Session`、`Scorer` 实例上的私有属性（清单 `CLASS_NAMES`）都还取得到；登记在 `WRITTEN`/`CLASS_WRITTEN` 的名字写得进、读得回（写完还原）；`write_problem` 自己的样例判断正确。
  - `PrivateUsageScanTest`：用 `ast` 扫 `tests/`、`tools/` 下的 py，报出对这三个类和三个模块取的、清单里没登记的私有名字；扫描器自己的样例被正确识别。
- **检证对象**：`advisor/ui/overlay.py` 的 `Overlay`、`advisor/session/`（`Session` 与模块）、`advisor/engine/`（`Scorer` 与模块）、`advisor/prefs.py`（模块）——核对它们身上若干私有名字还在、还能写。
- **什么时候要跑**：拆分、搬动 `advisor/ui/overlay.py`、`advisor/session/`、`advisor/engine/`、`advisor/prefs.py` 里的私有方法/属性或模块级名字时；在测试或 `tools/` 里新用了私有名字时（新增的私有用法要在本文件的清单里登记）。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_private_names`（上次没有记录；跑一次以后会记进 `.tmp/test-times.json`）。
- **需要什么**：没有特别要求（用 Qt 的离屏平台建实例）。
- **输出怎么看**：`test_no_unregistered_private_use` 失败时会打出「清单里没有的私有用法」逐行清单（`文件:行号 归属.名字（写入）`），照着它登记或改走公开探针。没有特别的临时文件。
- **出错时一般是哪里不对**
  - `PrivateModuleNamesTest.test_module_names` / `PrivateInstanceNamesTest.test_overlay_names`（或 `test_session_names`/`test_scorer_names`）失败 → 私有名字被改名或搬走，测试还按老名字取；去看对应模块，并决定是改测试还是（既然搬走了）从清单删掉。
  - `test_module_patch_targets` 失败 → `mock.patch` 的目标只是重新导出的一份，patch 会打空（去看 `advisor/prefs.py`、`advisor/session/` 的定义位置）。
  - `test_written_names` 失败 → 原来只读的转发属性变成写不进，或写入落成了实例上新加、没人读的属性（去看对应类的属性实现）。
  - `test_no_unregistered_private_use` 失败 → 测试或 `tools/` 里新用了没登记的私有名字；按输出的清单登记到 `CLASS_NAMES`/`WRITTEN`/`CLASS_ATTRS`/`CLASS_WRITTEN`/`MODULE_NAMES`/`MODULE_WRITTEN`。
  - `test_scan_recognizes_sample` / `test_write_problem_sample` / `test_module_patch_sample` 失败 → 扫描器或判断函数自己的识别逻辑改坏了（不是被守卫的代码出问题）。
  - **改了预期就该跟着改**：清单本身是有意维护的台账；搬动/新增私有名字时，把清单一起改。

## test_baseline_storage.py：基准工具读设置文件时，「这次运行不写它」也要录下来；分享格式的输入里有「复制为我的方案」

- **测试观点**：两条用例。一是设置文件在、但这次运行读不了（读的时候出错，于是存盘时不写它、返回「没写成」）时，基准记录里的存盘位置记 `false`（不当成写成了）、写盘后的文件列表和读完时相同、警告照记（日志里有一行写「读取失败」）。二是分享格式的输入里有复制默认推荐得到的方案：测试用的默认推荐档位（`tests/support/player_layer.toml`）里写了档位的角色各一套（带档位和顺位），另有一套资料里的默认推荐（现在不带标记），复制的日期都是固定的那一天。
- **检证对象**：`tools/baseline/storage.py` 里录设置文件那一段的记录方式和分享格式的输入（`_share_inputs`）；被测的行为本身在 `advisor/schemes.py` 的 `Settings.load`/`save`（读不了的文件这次运行不写回）。「读不了」用替换 `advisor/fileio.py` 的读函数造出。
- **什么时候要跑**：改了基准工具里录设置文件的那一段、分享格式的输入，或改了 `Settings.load`/`save` 的「读不了就不写回」、测试用的默认推荐档位以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_baseline_storage`（上次约 0.01 秒）
- **需要什么**：不需要识别样本截图、不需要显卡；文件写在测试沙箱的临时文件夹里。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `test_not_written_recorded` 失败 → 录制那一层不再区分「存进了哪个文件」和「这次没写」（存盘位置可能写成了文件名）；或 `Settings.save` 在读不了时不再返回「没写成」。
  - 日志里那句「读取失败」没有了 → `Settings.load` 读不了时的警告被去掉或换了措辞（换了就把断言一起改）。

## test_baseline_docs.py：推荐对比工具的「生成文档」领域，比对两份审查清单时去掉开头那行指纹

- **测试观点**：只有一类用例（`DocsCompareTest`）——推荐对比工具的「生成文档」领域（`tools/baseline/docs.py`）比对 `review/` 下两份审查清单（`synergy-review.md`、`synergy-audit.md`）时，不以开头那行「生成依据的指纹」为差异（它随数据和推荐代码变，改了推荐代码时一定不同，不算文档内容的变化）：两次指纹不同、正文相同 → 没有差异；录于加指纹以前、没有这一行的旧基准和带指纹的那份照样逐行对得上；正文真有不同照样报出，行号按 B 那一份文件算（把去掉的指纹行算进去）；只差在换行符（`\r\n` 与 `\n`）时报「文字相同，字节不同」。
- **检证对象**：`tools/baseline/docs.py` 的 `record`、`compare`、`_body`、`_lines`、`DOCS`、`SHOWN`；指纹行的识别用 `tools/make_review.py` 的 `FINGERPRINT_LINE`、`recorded_fingerprint`。
- **什么时候要跑**：改了 `tools/baseline/docs.py` 的比对规则，或改了审查清单开头的指纹行写法（`tools/make_review.py` 的 `FINGERPRINT_LINE`）、清单的文件名（`REVIEW_NAME`、`AUDIT_NAME`）以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_baseline_docs`（上次没有记录；跑一次以后会记进 `.tmp/test-times.json`）。
- **需要什么**：不需要识别样本截图、不需要显卡；两份清单都写在测试沙箱的临时文件夹里，不碰真的 `review/`。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `test_fingerprint_line_not_compared`／`test_old_baseline_without_fingerprint_line` 失败 → 去掉指纹行这一步失效（两次指纹不同时仍报了差异，或没有指纹行的旧基准对不上）。
  - `test_real_difference_line_number_in_b` 失败 → 正文不同时没报出，或报的行号没把去掉的那行算进去。
  - `test_line_endings_only` 失败 → 只差换行符时的判定改了。
  - 改了清单文件名或指纹行写法时，`DOCS` 和指纹行的识别（`make_review.recorded_fingerprint`）要跟着改。

## test_dev_tools.py：开发工具——校准、对着游戏窗口调试、自编 OpenCV 的逐位比对，以及缺识别样本时先说明

开发工具不随发行版发布，只从源码运行，用法见[软件维护说明](../软件维护说明.md)的「开发工具」。

- **测试观点**
  - `CalibrateReportTest`：校准工具按推荐日志汇总推荐与实际选择、「不认同」的反馈；按「查看筹码」面板改正的选择记在同一编号上，单列一句、不计入一致率；记了「已校准」以后重新计数。
  - `CalibrateTryOtherSchoolTest`：没选首推、原因点的是「想试别的玩法」的三选一单列一句，不计入一致率，也不进「推荐 → 实际」的对照，原因统计照旧列出；同一次有几条原因时以最后一条为准（先点别的、后点它的不计入，先点它、后点别的照常计入）；最后记下的选择就是首推的照常计入；本机推荐日志和校准导出文件两条路都查。
  - `CalibrateGameOverTest`：推荐日志里同一局有两条对局结束（误认的结束、新一局撤销、真正的结束）时，汇总只算一局（开头写「局数 1；」）；局面借 `tests/test_logbook_game_over_again.py` 的 `GameOverAgainTest`。
  - `CalibrateSchemeRecordsTest`：按筹码方案推荐的三选一不计入任何统计（同一个三选一中途换成方案的以最后一条为准），开头写明跳过了几次，它们的「不认同」也不列；推荐日志照 `tests/test_scheme_scoring.py` 的 `LogbookTest.write_mixed_records` 写。
  - `CalibrateExportTest`：读校准导出文件默认只算正式版、`--group formal,beta` 两组都算；按方案推荐的不计入、坏行跳过并在开头写明，没有分组、分组认不出的行不算进任何一组、也在开头写明有几行；导出里为 null 的星数、轮次、角色、首推、筹码名照样汇总（明细里 null 的筹码名写「?」）；`--file` 与 `--mark` 冲突、不带 `--file` 给 `--group`、认不出的分组（含空的）都报错退出，文件不存在时退出码非 0。
  - `LiveToolTest`：对着游戏窗口调试的命令行工具 `tools/live.py` 和悬浮窗一样经记账算推荐：手动指定的星数不被概率行改写（和星数对不上的概率行按任务来源算，任务等级按概率行或 `--tier`）。
  - `ProbeCoverageTest`：比对脚本 `tools/compare_cv.py` 报「全部覆盖」的 `cv2` 名字，探针里真的调到（`IMPLICIT_NAMES` 写明的几个除外）；项目代码用到的每个 `cv2` 名字都在覆盖清单里；按灰度解码的读法也在探针里。
  - `BuildInfoTest`：从 `cv2.getBuildInformation()` 的文字里取 CPU 基线、分派、并行框架、IPP 版本和 JPEG、PNG 的 SIMD 两行；基线和分派下面各一行 `requested` 分开记；模块清单、媒体格式不同不报。
  - `SamplesNoteTest` / `NoSamplesTest` / `PreviewShotsTest`：要读识别样本的工具缺样本时先说明缺哪个文件、准备方法在哪里，以退出码 2 结束：识别评测、回放、耗时共用的 `tools/eval_common.need_samples`（写法和测试缺样本时的跳过原因一样）；两个比对工具一张截图都没有时不建识别引擎、不跑探针，不当成「没有差异」；界面预览在命令行上先查要读的截图（`--idle` 不读截图，`--before`、`--in-game` 的也查）。
- **检证对象**：`tools/calibrate.py` 的 `collect`、`report`、`read_export`、`export_header`（读 `advisor/logbook.py` 记的推荐日志或校准导出文件，`mark_calibrated`）；`tools/live.py` 的 `new_session`、`recommend`；`tools/compare_cv.py` 的 `PROBE`、`COVERED_NAMES`、`IMPLICIT_NAMES`、`build_info_facts`、`build_info_differences` 和 `main` 开头查样本的一段，以及 `tools/build_cv.py` 的 `used_cv2_names`；`tools/eval_common.py` 的 `samples_missing`、`need_samples`；`tools/compare_ocr_lite.py` 的 `main` 开头查样本的一段；`tools/preview_overlay.py` 的 `missing_shots`。
- **什么时候要跑**：改了这几个工具；改了 `advisor/logbook.py` 的记录格式、对局结束和局数统计；改了 `advisor/session/` 里星数、三选一来源的判断（`tools/live.py` 走同一个记账）；改了 `tests/test_logbook_game_over_again.py` 的 `play_undone_end`、`later` 或 `tests/test_scheme_scoring.py` 的 `LogbookTest.write_mixed_records`（这里借用）；项目代码里新用了 `cv2` 的名字时。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_dev_tools`（上次约 2.4 秒）。
- **需要什么**：不需要识别样本截图、不开游戏、不开窗口：推荐日志写进测试沙箱里的临时文件夹；`LiveToolTest` 的识别换成假的读数；比对脚本只核对文字（探针的源码、构建信息的样例），不起别的 Python 环境；缺样本的几条用测试沙箱里的空文件夹。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**
  - `CalibrateReportTest` / `CalibrateTryOtherSchoolTest` / `CalibrateGameOverTest` / `CalibrateSchemeRecordsTest` / `CalibrateExportTest` 失败 → 汇总的口径（面板改正的不计入一致率、「想试别的玩法」的不计入、按方案推荐的不计入、一局两条结束只算一局）、读校准导出文件（分组、坏行、null 字段、开头几行）、「不认同」的处理或汇总的文字改了（`tools/calibrate.py`、`advisor/logbook.py`）；有意改文字时把断言一起改。
  - `LiveToolTest` 失败 → `tools/live.py` 的推荐入口，或「手动指定的星数不被概率行改写」改坏了（`Session.judge_source`、`Settings.star_read`）。
  - `ProbeCoverageTest.test_used_names_covered` 失败 → 项目里新用了一个 `cv2` 名字而探针没补：给 `compare_cv.PROBE` 照程序里的用法补一个调用，写进 `COVERED_NAMES`。`test_covered_names_are_called` 失败 → 覆盖清单里写了探针没调到的名字。
  - `BuildInfoTest` 失败 → 比对脚本从构建信息里取的几项或取法改了（`BUILD_INFO_SCALARS`、`BUILD_INFO_SIMD_ANCHORS`）。
  - `SamplesNoteTest` / `NoSamplesTest` / `PreviewShotsTest` 失败 → 缺样本时的说明、退出码或先查的时机改了（`tools/eval_common.py`、`compare_cv.py`、`compare_ocr_lite.py`、`preview_overlay.py`）。

## test_integration.py：跨模块接通的几处（未收录筹码、面板页签、缓存重置）

- **测试观点**
  - `UnrecordedRecognitionTest`：用图标边框颜色把关，判出资料库未收录的候选筹码；只读到一个汉字或读不到颜色仍算无法识别
  - `LowMatchTest`：候选筹码「匹配度低」分两种——文字有错但只对得上一个筹码就不提示；只读到共同部分算没读完，按边框颜色挑并照旧带提示；识别流程对没读完的格子半秒后再读一次
  - `UnrecordedSessionTest`：资料库未收录的候选筹码照样给推荐（按识别到的稀有度算平均），推荐里写清「资料库未收录」，不记进已有筹码；识别模块没标成未收录的陌生名字仍请用户改正（三选一刚出现时先等一等，再读一次还是它才提示无法识别）
  - `PanelTabTest`：队友页签选错时提示一次，换到另一个队友再提示一次；选对自己的页签时读已有筹码
  - `OverlayIntegrationTest`：未收录的推荐能画出来、候选列表里标「（资料库未收录）」但不标橙色；面板页签提示需要用户操作、边框变色；无效图标提示上界面
  - `ResetCachesTest`：重置识别模块缓存的函数把几个缓存都清掉
- **检证对象**：`advisor/vision/chip_select.py` 的 `_unrecorded_slots`、`check_names`、`_match_chip`、`reread_selected`、`name_strip`、`ChipSelect`；`advisor/session/core.py` 的 `on_frame`、`on_panel`；`advisor/ui/overlay.py`；`advisor/ui/text.py` 的 `option_views`、`candidate_views`、`unreadable_slots`、`breakdown_html`；`advisor/vision/pipeline.py` 的 `_incomplete`；`advisor/vision/__init__.py` 的 `reset_caches`
- **什么时候要跑**：改了候选筹码的未收录／匹配度低判定、记账里对未收录候选的处理、悬浮窗画未收录筹码、面板页签提示、缓存重置，都要跑
- **怎么单独跑**：`python -B tools/run_tests.py --only test_integration`（上次约 1.9 秒）
- **需要什么**：离屏平台，不弹窗
- **输出怎么看**：同总则
- **出错时一般是哪里不对**
  - `UnrecordedRecognitionTest` 失败 → 判资料库未收录的把关（颜色、汉字数、相似度）改坏（`advisor/vision/chip_select.py` 的 `_unrecorded_slots`）
  - `LowMatchTest` 失败 → 「匹配度低」两种情形、按边框颜色挑、或只读到共同部分算没读完改坏（`advisor/vision/chip_select.py` 的 `check_names`、`advisor/vision/pipeline.py` 的 `_incomplete`）
  - `UnrecordedSessionTest` 失败 → 未收录候选没给推荐、稀有度没交给引擎，或误记进已有筹码（`advisor/session/core.py` 的 `on_frame`）
  - `PanelTabTest` 失败 → 队友页签提示的次数或条件、或读自己页签改坏（`advisor/session/core.py` 的 `on_panel`）
  - `OverlayIntegrationTest` 失败 → 未收录筹码的渲染、候选列表标记、面板提示或无效图标提示改坏（`advisor/ui/text.py`、`advisor/ui/overlay.py`、`advisor/ui/overlay_parts/wording.py` 的 `PANEL_TAB`）
  - `ResetCachesTest` 失败 → 某个识别模块的缓存没清（`advisor/vision/__init__.py` 的 `reset_caches`）

## test_undo_auto_report.py：撤销误判时自动保存的诊断包

- **测试观点**：
  - `test_undo_export_auto_and_prune_spares_it`：走一遍撤销误判（判一个新的一局，再点「撤销」），诊断模块收到的导出请求带自动保存标记，随后按份数清理并把刚存的那一份作为「不删」传进去；状态行提示里写明保存在哪。
  - `test_undo_export_failure_no_prune_no_notice`：导出失败时不清理、也不另加提示（撤销本身照常完成）。
  - `test_manual_export_has_no_auto_and_no_prune`：手动的「导出诊断」照旧不带自动保存标记，也不按份数清理。
  - `test_unread_frame_only_in_manual_export`：后台识别留着三选一读不全时的那一帧时，自动保存的那一份不含截图，当时的画面和这一帧都不带；手动导出两样都带。
  - `test_select_shots_only_in_manual_export`：记下选中项用的那一帧、被临时盖住而没有采用的那一帧同样只在手动导出时带上。
  - `test_rejoin_saves_nothing`：主菜单结束的局又连回同一局（记账自动撤回那次结束，不是用户撤销误判）：不存诊断包、不清理。
- **检证对象**：`advisor/ui/overlay_parts/background.py` 的 `save_undo_record`、`export_diagnostics`、`export_done`（撤销时自动存）、`advisor/diagnostics.py` 的导出与按份数清理（含「不删的那一份」这个参数）、以及撤销那条路径（`advisor/ui/overlay.py` 里撤销新一局的判定）。
- **什么时候要跑**：改了撤销误判时自动存诊断包的路径、诊断模块导出的参数或清理的行为、撤销后那句提示的文字以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_undo_auto_report`（上次约 0.2 秒）
- **需要什么**：不需要识别样本截图、不需要显卡；要能建界面（用离屏平台，不弹真实窗口）；诊断模块用假对象，不写文件。
- **输出怎么看**：同总则。诊断模块是假对象，失败时断言里是它收到的「自动保存标记」和清理时收到的「不删的那一份」。
- **出错时一般是哪里不对**：
  - `test_undo_export_auto_and_prune_spares_it` 失败 → 撤销时存诊断包不再带自动保存标记，或存好以后不按份数清理、没把刚存的那一份传进去（系统时间被调回过时它名字最旧，不传会被当成最旧的删掉）。
  - `test_undo_export_failure_no_prune_no_notice` 失败 → 「失败时不清理、不另加提示」这一条被改坏了，或失败时反而多了提示。
  - `test_manual_export_has_no_auto_and_no_prune` 失败 → 手动导出的路径也带上了自动保存标记，或也做了按份数清理。
  - `test_unread_frame_only_in_manual_export` 失败 → 自动保存的那一份带上了三选一读不全时的那一帧（不含截图的说法就不对了），或手动导出没带上它（`background.py` 的 `export_diagnostics` 取 `unread_source`）。`test_select_shots_only_in_manual_export` 失败同理（取 `pick_source`、`covered_source`、`finish_source`）。
  - 提示文字的断言失败 → 撤销后那句提示改了；有意改的话把预期一起改。
