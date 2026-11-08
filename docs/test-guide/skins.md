# 测试指南 · 皮肤和界面控件

这是[《测试指南》](../测试指南.md)第八节的一章：每个测试文件一节，每节的结构见那里第八节的开头。


这一章管皮肤机制和每一套皮肤、自绘部件与装饰、界面控件与文字交互，以及界面文字的守卫。

## test_theme.py：皮肤机制本身（登记、切换、字体、排法入口）

- **测试观点**：
  - 皮肤数据（`SkinDataTest`）：默认皮肤的配色/字号/样式表；外观参数由共用颜色推出来；参数名写错时报错；`skins()` 的先后；共用的齿轮图标和「已有筹码」个数（`0 个`）的颜色（霓虹贴纸电光青、碳纤流光电光青、白色海景房紫，其余照正文和暗色）；不认识的皮肤名按默认；只读表格；换皮肤时依次通知登记的窗口、出错的只记进错误记录、退订的不再通知。
  - 字体文件（`FontFilesTest`）：文件夹不在、文件坏了、文件能用、换到字体文件不在的皮肤时各自的表现（不报错，退回默认正文字体）。
  - 悬浮窗（`OverlaySkinTest`）：换到测试皮肤以后颜色/字体/尺寸都是新皮肤的、当场换的和新建逐像素相同、换回默认相同、收在托盘里换皮肤、状态保留、透出游戏画面的透明度。
  - 透出游戏画面（`SeeThroughTest`）：默认皮肤做在面板底色里（窗口本身不透明），其余六套改为整个窗口两档透明（平时/识别暂停），打开开关时一律不透明。
  - 四个窗口（`WindowsSkinTest`）：开着的窗口当场换、和新建逐像素相同、换回默认相同。
  - 编辑方案动起来时换皮肤（`EditorMotionTest`）：拖动/动画中换皮肤不出错、放回原位、动画直接到位、只换颜色时接着拖/接着动画。
  - 编辑方案换皮肤（`EditorLiveSwitchTest`）：只改留白/正文字号/程序字号时当场重排，和新建相同。
  - 记住所选（`RememberSkinTest`）：设置文件里的皮肤重新 build 后照旧、不认识的名字按默认；设置窗口「皮肤」一行只在注册两套以上时出现、点了马上换并存进设置；等待按键时换皮肤照旧。
  - 排法入口（`LayoutEntryTest`）：设置窗口按皮肤取排法；不认识的排法按默认；编辑方案/导入导出/关于的排法变了时悬浮窗关掉重开。
- **检证对象**：`advisor/ui/theme/`（`Skin`、`make_skin`、`register`/`unregister`/`skins`/`get_skin`/`has_skin`/`set_skin`/`current`/`drawing`、`subscribe`/`unsubscribe`、`load_fonts`/`unload_fonts`/`family`/`available`/`apply_fonts`、`ink`/`ink_html`、`legacy`、`validate_skin`）；`advisor/ui/skins/`；`chrome.py`、`decor.py`、`overlay.py`、`settings/`、`scheme_editor/`、`scheme_share.py`、`about.py`、`scheme_list/`。
- **什么时候要跑**：改 `advisor/ui/theme/` 的任何部分；改皮肤的参数或新增参数；改字体加载/替代字体的规则；改各窗口取排法的地方。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_theme`（上次约 9 秒）。
- **需要什么**：没有特别要求；离屏 Qt；`FontFilesTest.test_good_file` 在系统里没有 `arial.ttf` 时会跳过；用到系统的 `SimSun`/`Arial`/`Consolas` 时如果这台电脑没有就不核对字体那几条。
- **输出怎么看**：同总则；测试皮肤只在这个文件里登记，不出现在设置窗口里；字体文件、设置文件、方案文件写在 `.tmp` 下的临时文件夹里。
- **出错时一般是哪里不对**：
  - `SkinDataTest.test_default_values` 失败 → 默认皮肤的配色/字号/样式表被改了（数值是有意的就跟着改断言的预期）。
  - `test_derived_from_common`、`test_bad_names`、`test_skins_order`、`test_shared_extras` 失败 → `make_skin`/`register`/皮肤序的规则。
  - `test_listeners`、`test_unsubscribed_during_notify_is_skipped` 失败 → `set_skin` 的通知逻辑。
  - `FontFilesTest` 失败 → `load_fonts` 对缺失/损坏字体文件的处理。
  - `OverlaySkinTest`、`SeeThroughTest`、`WindowsSkinTest` 失败 → 换皮肤没重画干净或透明度逻辑（看 `theme` 的 `SEE_THROUGH`、`PANEL_ALPHA` 和 `overlay.py`）。
  - `EditorMotionTest`、`EditorLiveSwitchTest` 失败 → 换皮肤时编辑方案的拖动/动画/重排。
  - `RememberSkinTest` 失败 → 设置文件读写或「皮肤」一行的显示条件。
  - `LayoutEntryTest` 失败 → 各窗口按皮肤取排法的入口，或排法变了没重开窗口。

## test_theme_package.py：`theme` 包对外的名字都取得到

- **测试观点**（`ThemePackageTest`）：
  - 能按 `theme.名字` 取到的名字（含测试和工具在用的私有名字；清单在测试里）一个都不少。
  - 外观参数数据类定义在 `looks` 子模块、样式表函数定义在 `sheets` 子模块，本包只是重新导出；`Skin`、`make_skin` 定义在本包。
  - 当前皮肤这类运行时会改的状态只在本包；子模块拼样式表时从这个包现查。
  - 换皮肤以后不带参数再拼一次样式表，跟着换成新皮肤的（不是导入时抄下的一份）。
  - 测试里会打补丁的那几个名字都在本包里定义。
- **检证对象**：`advisor/ui/theme/__init__.py`、`advisor/ui/theme/looks.py`、`advisor/ui/theme/sheets.py`。
- **什么时候要跑**：改 `advisor/ui/theme/` 包的结构、把名字在子模块之间挪动、改重新导出的名字。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_theme_package`（上次约 0.1 秒）。
- **需要什么**：没有特别要求，不建窗口。
- **输出怎么看**：同总则；失败信息会列出「theme 缺少这些名字：……」。
- **出错时一般是哪里不对**：
  - `test_names_still_importable` 失败 → 漏了重新导出某个名字（按失败信息里列的名字补回 `__init__.py`）。
  - `test_submodules_hold_the_split_out_parts` 失败 → 数据类/函数挪到了别的子模块，或本包又自己定义了一份。
  - `test_current_skin_state_still_on_package`、`test_sheets_follow_skin_switch` 失败 → 子模块把当前皮肤抄了一份，而不是现查。
  - `test_patch_targets_live_in_package` 失败 → 测试要打补丁的名字被挪出了本包。

## test_skin_carbon.py：碳纤流光（carbon_flow）这一套皮肤的登记、外观参数、字体子集和各个窗口的表现

- **测试观点**：
  - 登记和外观参数（`CarbonDataTest`）：显示名、四个窗口各用的排法、标题/数字字体、七色配色、警告色；这些新参数默认皮肤和别的几套不用。
  - 字体子集（`FontTest`）：粗体子集装得上、字体名是 `ACC Title`、界面用到的字都在子集里、子集外的字退回正文字体不出方块。
  - 悬浮窗（`OverlaySwitchTest`）：当场换皮肤和新建逐像素相同、换回默认和换之前相同；推荐、展开/收起、位置等状态保留；记分板样的推荐区、「已有筹码」个数（`0 个`）写电光青、底栏齿轮图标。
  - 四个窗口（`WindowsTest`、`EditorShareTest`）：随皮肤重排/重开、设置窗口两列格子控件照常、关于左边目录跳转、编辑方案页签和两列表单、搜索放大镜颜色、导入/导出左右并排、皮肤窗口的「使用中」标记。
  - 关闭和拖动（`CloseAndMoveTest`）：× 和 Esc 关窗、一个窗口只有一个关闭按钮、按住拖动、编辑方案和导入/导出拖边缘改大小（设置、关于不能）、系统圆角。
  - 窗口高度（`HeightTest`）：不超过屏幕可用高度（含 768 和 480 高的屏幕）。
  - 装饰（`DecorUnderTextTest`）：灯带、灯线、标题后面的线不压在字下面。
  - 记住所选（`RememberTest`、`ListenerTest`）：皮肤存进设置文件、换皮肤的通知名单不随开关窗口增长。
- **检证对象**：`advisor/ui/skins/carbon_flow.py`（碳纤流光的参数）；`advisor/ui/theme/`（登记、外观参数、字体开关、取值）；`fonts/ACCTitle-Bold.otf` 和 `tools/make_font_subset.py`（收字规则）；`advisor/ui/chrome.py`（`CloseButton`、`EdgeResize`、`WindowHead`、`Panel`、`edge_at`、`round_corners`）；`advisor/ui/decor.py`（画法）；`advisor/ui/overlay.py` 的 `set_skin` 和 `advisor/ui/overlay_parts/option_widgets.py` 的 `_ScorePlate`、`_ScoreRow`、`_ScoreName`；`advisor/ui/scheme_editor/`、`scheme_share.py`、`settings/`、`about.py`、`skin_window.py`。
- **什么时候要跑**：改 `advisor/ui/skins/carbon_flow.py`；改 `advisor/ui/theme/` 的公共部分、`chrome.py`、`decor.py`、`widgets.py` 的画法；改悬浮窗、编辑方案、导入/导出、设置、关于、皮肤窗口的布局或文字；改 `tools/make_font_subset.py` 的收字规则或换字体子集文件；改了界面文字、简繁转换的规则或补充词组（子集覆盖检查会报）。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_skin_carbon`（上次约 15 秒）。
- **需要什么**：没有特别要求；离屏 Qt，不弹窗口。要这台电脑装了微软雅黑（离屏环境会装上），否则排版会变。
- **输出怎么看**：同总则；设置文件、方案文件写在项目 `.tmp` 下的临时文件夹里。`test_search_icon_cyan` 在系统里没有图标字体时会跳过（写「系统里没有图标字体」）。
- **出错时一般是哪里不对**：
  - `CarbonDataTest.test_registered` 失败 → 碳纤流光的排法/字体/配色没登记或登记错了（看 `advisor/ui/skins/carbon_flow.py`）。
  - `CarbonDataTest.test_other_skins_untouched` 失败 → 新加的外观参数漏进了别的皮肤（默认皮肤和其余几套应留空）。
  - `FontTest.test_bold_subset_covers_ui_text` 失败 → 改了界面文字没重做字体子集（跑 `tools/make_font_subset.py`）。
  - `FontTest.test_outside_subset_falls_back` 失败 → 子集外的字没退回正文字体（`theme` 的替代字体没设）。
  - `OverlaySwitchTest` 的逐像素断言失败 → 悬浮窗换皮肤没重画干净，或换回默认没还原（看 `advisor/ui/overlay.py` 的 `set_skin`）。
  - `test_owned_count_and_settings_glyph` 失败 → 「已有筹码」个数（`0 个`）的颜色或底栏齿轮图标。
  - `WindowsTest`、`EditorShareTest` 失败 → 窗口没按排法重开/重排，或换回默认没还原系统标题栏、控件位置不对。
  - `CloseAndMoveTest` 失败 → 关闭按钮、拖动、改大小、圆角（看 `advisor/ui/chrome.py`）。
  - `HeightTest` 失败 → 窗口高度没按屏幕可用高度缩（编辑方案应只有筹码列表变短）。
  - `DecorUnderTextTest` 失败 → 灯带/线画到了字下面。

## test_skin_classic.py：经典主题-白（classic_white）这一套皮肤的登记、字体子集、窗口切换和窗口行为

- **测试观点**：
  - 登记和外观参数（`ClassicDataTest`）：显示名、排法（设置/编辑方案/导入导出左侧页签、关于深灰面板、外框「返回」）、字体、警告色（黑底黄字圆标）；`ink`/`ink_html` 把深色面板上的字换浅色；样式表里的标题字体。
  - 字体子集（`FontTest`）：子集装得上、收字规则覆盖这套皮肤用到的界面文字、子集外的字退回、替代字体优先正文字体。
  - 换过去再换回来（`SwitchTest`）：悬浮窗逐像素相同、几处样子（浅灰卡片、警告圆标、稀有度胶囊筹码名、首推底）；四个窗口随皮肤重排/重开。
  - 设置窗口（`SettingsPagesTest`）：「识别」「外观」「关于」三页切页、每项在它那一页；「关于」页四个按钮打开关于窗口对应的一节；等待按键时换皮肤照常；警告圆标不压字。
  - 关闭和拖动（`CloseAndMoveTest`、`SingleCloseTest`）：「返回」和 Esc 关窗、任务栏按钮照旧、拖动移动窗口、只有一个关闭按钮；默认皮肤照旧用系统标题栏；编辑方案「已保存」写在原「关闭」的位置。
  - 装饰（`DecorUnderTextTest`）：斜带、导航栏竖条、几何小图案不压字。
  - 边缘和圆角（`EdgeAtTest`、`ResizeTest`、`CornerTest`）：边缘命中、编辑方案和导入导出拖边缘改大小、设置和关于固定大小、系统圆角失败时安静。
  - 窗口高度（`HeightTest`）：矮屏幕不压扁输入框、目标流派多时窗口不变宽、其它窗口适配、默认皮肤尺寸不变。
  - 记住所选（`RememberTest`、`ListenerTest`）。
- **检证对象**：`advisor/ui/skins/classic_white.py`；`advisor/ui/theme/`；`fonts/` 里的四个标题字体子集（简体、繁体各两个字重）和 `tools/make_font_subset.py`；`advisor/ui/chrome.py`（`BackButton`、`EdgeResize`、`edge_at`、`round_corners`、`_native_windows`）；`advisor/ui/decor.py`（`panel_stripes` 等）；`advisor/ui/scheme_editor/`、`scheme_share.py`、`settings/`、`about.py`、`overlay.py`。
- **什么时候要跑**：改 `advisor/ui/skins/classic_white.py`；改 `theme`、`chrome.py`、`decor.py`、`widgets.py` 的公共画法；改编辑方案/导入导出/设置/关于窗口的布局或文字；改字体子集或收字规则（`collect_text`、`collect_text_hant`，繁体的收字范围是 `tools/hant_table.py` 的 `all_texts`）；改了界面文字、简繁转换的规则或补充词组（`advisor/ui/lang.py`、`data/zh_hant_extra.toml`：转出来的繁体字要在繁体子集里）。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_skin_classic`（上次约 14 秒）。
- **需要什么**：没有特别要求；离屏 Qt。`FontTest.test_fallback_prefers_body_font` 在没有可用的中文字体时会跳过。
- **输出怎么看**：同总则；设置、方案文件写在 `.tmp` 下。若干用例先把随软件带的字体装上、跑完撤掉（换皮肤前后字体库一样）。
- **出错时一般是哪里不对**：
  - `ClassicDataTest.test_registered` / `test_ink` / `test_title_font_placeholders` 失败 → 经典主题-白的排法、字体或深色面板换浅色的规则改了（看 `advisor/ui/skins/classic_white.py`、`theme` 的 `ink`）。
  - `FontTest` 失败 → 字体子集没装/没覆盖界面文字（重做子集）。
  - `SwitchTest` 失败 → 悬浮窗换皮肤没重画干净或窗口没按排法重开。
  - `SettingsPagesTest` 失败 → 设置三页的项或「关于」页四个按钮的跳转。
  - `CloseAndMoveTest`、`SingleCloseTest` 失败 → 关闭按钮/「已保存」位置/任务栏标志。
  - `DecorUnderTextTest` 失败 → 斜带或竖条压在字下。
  - `EdgeAtTest`、`ResizeTest`、`CornerTest` 失败 → 边缘命中或改大小/圆角（看 `advisor/ui/chrome.py`）。
  - `HeightTest` 失败 → 矮屏幕压扁了输入框或截掉了折行说明。
  - `RememberTest`、`ListenerTest` 失败 → 皮肤没存住或通知名单增长。

## test_skin_game.py：经典主题-黑（classic_black）和经典主题-反色（classic_inverse）两套皮肤各跑一遍

- **测试观点**：
  - 登记和外观参数（`GameDataTest`）：两套的显示名、外框右上角 ×、四个窗口各用的排法、字体；两套警告色各不同（黑黄底/黑斜条），默认和经典主题-白不受影响。
  - 悬浮窗（`OverlaySwitchTest`）：逐像素相同、状态保留；经典主题-黑的圆形筹码图标、首推光圈和星星、推荐区底、标题在正中；经典主题-反色的稀有度短条、首推斜角标、位次在名字前；筹码名点击改正；图标没下载好时画稀有度色圈。
  - 四个窗口（`WindowsTest`）：随皮肤重排/重开、设置三页和「关于」页四个按钮、控件照常、等待按键时换皮肤照旧。
  - 关闭和拖动（`CloseAndMoveTest`）：× 和 Esc、一个关闭按钮、拖动、改大小、圆角。
  - 窗口高度（`HeightTest`）：矮屏幕不压扁输入框、经典主题-黑的表单放滚动区、正常尺寸。
  - 编辑方案和导入导出（`EditorShareTest`）：方案卡片横排/竖排、档位按钮分段、多目标流派、导入导出并排/上下排、识别结果整个排开。
  - 装饰（`DecorUnderTextTest`、`DecorTest`）：分组标题的横线不压字、反色横幅文字在横幅底色上；平行四边形和缎带的形状。
- **检证对象**：`advisor/ui/skins/classic_black.py`、`advisor/ui/skins/classic_inverse.py`；`advisor/ui/theme/`；`advisor/ui/decor.py`（`slant`、`ribbon`、`ridge`、`board_window/panel` 等）；`advisor/ui/overlay_parts/option_widgets.py`（`_AdviceBox`、`_ChipIcon`、`_GameName`、`_BestBadge`）；`chrome.py`、`scheme_editor/`、`scheme_share.py`、`settings/`、`about.py`。
- **什么时候要跑**：改这两套皮肤的文件；改 `decor.py`、`chrome.py`、`option_widgets.py` 的画法或 `theme`；改编辑方案/导入导出/设置/关于窗口的布局或文字。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_skin_game`（上次约 27 秒）。
- **需要什么**：没有特别要求；离屏 Qt；要这台电脑装了微软雅黑。`test_icons_before_download` 会把 assets 位置换到临时文件夹，其余用例读这台电脑的 `assets` 里已下载的筹码图。
- **输出怎么看**：同总则；设置、方案文件写在 `.tmp` 下。
- **出错时一般是哪里不对**：
  - `GameDataTest` 失败 → 两套的排法/字体/警告色登记错了。
  - `OverlaySwitchTest` 的逐像素断言失败 → 悬浮窗换皮肤没重画干净。
  - `test_black_looks`、`test_inverse_looks` 失败 → 圆形图标、稀有度短条、首推斜角标等画法（看 `option_widgets.py`、`decor.py`）。
  - `test_icons_before_download` 失败 → 图片没下载好时的稀有度色圈。
  - `WindowsTest`、`EditorShareTest` 失败 → 窗口排法或控件位置。
  - `HeightTest` 失败 → 矮屏幕压扁输入框或经典主题-黑的表单滚动区。
  - `DecorUnderTextTest` 失败 → 横线/横幅压字；`DecorTest` 失败 → 平行四边形/缎带形状。

## test_skin_neon_seaview.py：霓虹贴纸（neon_sticker）和白色海景房（white_seaview）两套皮肤各跑一遍

- **测试观点**：
  - 登记和外观参数（`DataTest`）：显示名、排法、字体（霓虹贴纸 Heavy 子集、白色海景房 Bold 子集）、警告色、白色海景房组标题前小灯点的颜色；不用这些参数的皮肤不受影响。
  - 悬浮窗（`OverlaySwitchTest`）：逐像素相同、状态保留；霓虹贴纸首推黄框、稀有度胶囊筹码名、标题黑色斜带、「已有筹码」个数（`0 个`）写电光青；白色海景房每条推荐一张小卡片、左边彩色竖条（首推紫粉渐变）、组标题前小灯点、「已有筹码」个数写紫；筹码名点击改正；一行显示。
  - 四个窗口（`WindowsTest`）：随皮肤重排/重开、设置窗口单页、控件照常；霓虹贴纸「关于」跳到某一节、白色海景房「关于」的展开/收起/跳转；白色海景房「关于」顶上的版本卡，发行版里是版本、资料同步日期、打包时间、运行环境四行，这一节最后一行的说明写在卡片下面，每一行都完整、放得下（`test_seaview_version_card_with_build_line`）。
  - 关闭和拖动（`CloseAndMoveTest`）：× 和 Esc、一个关闭按钮、拖动、改大小、圆角；皮肤窗口也固定大小。
  - 窗口高度（`HeightTest`）：不超过可用高度（768、640、560、480 高的屏幕）、正常尺寸。
  - 编辑方案和导入导出（`EditorShareTest`）：方案卡片竖排在左栏、按钮不出栏、档位分段、导入导出的两种排法、白色海景房勾选胶囊按宽度换行、识别结果整个排开、共用排法时颜色重取。
  - 皮肤窗口（`SkinWindowTest`）：格子、说明跟在标题后、正在用的那一格标「使用中」、缩略图画得出来、点格子当场换。
  - 装饰（`DecorUnderTextTest`）：霓虹贴纸网点不压字、白色海景房竖条不压字、标题前小装饰不压字。
- **检证对象**：`advisor/ui/skins/neon_sticker.py`、`advisor/ui/skins/white_seaview.py`；`advisor/ui/decor.py`（`halftone`、`band_tag`、`led_tag`、`logo_title`、`sticker_card`、`well_card`）；`advisor/ui/overlay_parts/option_widgets.py`（`_EntryCard`、`_GameName`）；`advisor/ui/scheme_editor/`（`_SchemeStickerDelegate`、`_SchemeWellDelegate`）；`advisor/ui/scheme_share.py`（`_PillTag`、`_FlowList`）；`advisor/ui/about.py`（`_BandBrowser`、`_VersionCard`、`_Fold`）；`skin_window.py`、`skin_preview.py`、`chrome.py`、`theme`。
- **什么时候要跑**：改这两套皮肤的文件；改 `decor.py`、`chrome.py`、`option_widgets.py`、`skin_window.py`、`skin_preview.py`；改编辑方案/导入导出/设置/关于窗口的布局或文字。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_skin_neon_seaview`（上次约 30 秒）。
- **需要什么**：没有特别要求；离屏 Qt；要这台电脑装了微软雅黑；`SkinWindowTest.test_thumbnails` 会真画缩略图（在本进程里画）。
- **输出怎么看**：同总则；设置、方案、预览图写在 `.tmp` 下，不碰程序目录的 `assets/skins`。同 `test_skin_game.py`：一开始装好两套随软件带的字体，跑完撤掉。
- **出错时一般是哪里不对**：
  - `DataTest` 失败 → 两套的排法/字体/警告色/灯点颜色登记错了。
  - `OverlaySwitchTest` 的逐像素断言失败 → 悬浮窗换皮肤没重画干净。
  - `test_neon_looks`、`test_seaview_looks`、`test_one_line` 失败 → 黄框、竖条、胶囊、灯点等画法。
  - `test_neon_owned_count_color`、`test_seaview_owned_count_color` 失败 → 「已有筹码」个数（`0 个`）的颜色或这套皮肤的 `OWNED_COUNT_COLORS` 改了。
  - `test_name_click_opens_fix` 失败 → 筹码名点击改正。
  - `WindowsTest` 失败 → 窗口排法；关于窗口的滚动/手风琴；`test_seaview_version_card_with_build_line` 失败 → 版本卡放几行（`about.py` 的 `_build_accordion`）或「版本」一节的行（`version.about_sections`）改了。
  - `EditorShareTest` 失败 → 方案卡片/档位/导入导出排法。
  - `SkinWindowTest` 失败 → 皮肤窗口的格子、标记或缩略图。
  - `DecorUnderTextTest` 失败 → 网点/竖条/小装饰压字。

## test_skin_package.py：随程序发布的皮肤包能按任意导入顺序导入而不报循环导入

- **测试观点**：
  - `SkinPackageImportTest.test_any_import_order`：先导入 `theme`、`skins` 包、某一个皮肤文件、某一个皮肤的常量、`_shared` 五种顺序，各起一个新进程核对；每种顺序下七套都登记、`skins()` 的先后等于 `SKIN_ORDER`、`theme` 重新导出的常量就是那一套、`theme.skins` 还是函数（别名没盖住它）。
- **检证对象**：`advisor/ui/theme/__init__.py`（末尾导入皮肤包并重新导出）；`advisor/ui/skins/__init__.py`、`advisor/ui/skins/_shared.py` 和各皮肤文件（`default.py`、`classic_white.py`、`classic_black.py`、`classic_inverse.py`、`neon_sticker.py`、`white_seaview.py`、`carbon_flow.py`）。
- **什么时候要跑**：改 `advisor/ui/theme/` 或 `advisor/ui/skins/` 里模块之间的导入关系、重新导出的名字、`SKIN_ORDER`；新加一套皮肤。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_skin_package`（上次约 19 秒；或者说每组导入顺序各起一个新进程，比别的皮肤测试慢）。
- **需要什么**：没有特别要求，不建窗口；会起子进程（用同一个 Python 解释器）。
- **输出怎么看**：同总则；子进程的报错（循环导入、名字对不上）直接打在失败信息里（截取错误输出的末尾）。
- **出错时一般是哪里不对**：
  - `test_any_import_order` 失败 → `theme` 和皮肤包互相导入时漏了一处（例如皮肤文件在导入时直接用了只在 `theme` 末尾才有的名字），或重新导出的名字/位置变了、`SKIN_ORDER` 变了。

## test_skin_preview_job.py：画皮肤缩略图的子进程被放进作业对象（Windows），保证主程序结束时不留下孤儿进程

- **测试观点**（`ChildJobTest`）：
  - 子进程（以及开发版里启动器再起的真正解释器）都在作业里；作业的句柄被系统收回时它们都跟着结束（模拟主程序被强制结束）。
  - `stop`（程序正常退出）结束整棵进程树。
  - 建不了作业、放不进作业时照常运行、照样回话，`stop` 照样结束它。
  - 挂起着起以后恢复不了时结束它、照常另起一个。
- **检证对象**：`advisor/ui/skin_preview.py` 的 `_start_child`、`PreviewStore._spawn`、`_kill`、`stop`、`_new_job`、`_join_job`、`_resume`、`child_command`、`CREATE_SUSPENDED`；用 Windows 的作业对象（`kernel32`）。
- **什么时候要跑**：改 `advisor/ui/skin_preview.py` 里起子进程、放作业、结束进程的部分；改子进程命令行（`child_command`）。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_skin_preview_job`（上次约 2 秒；要起子进程）。
- **需要什么**：Windows（不是 Windows 时整个文件跳过，理由「作业对象只在 Windows 上有」）；会起子进程，子进程是个很小的假程序（不导入界面库）。
- **输出怎么看**：同总则；用例里把子进程的进程号写进临时文件来认出真正在画的那个进程；失败时收尾只结束核对过在作业里的进程，收尾核对错误记录为空。
- **出错时一般是哪里不对**：
  - `test_closing_job_ends_child_and_its_children` 失败 → 子进程没放进作业，或作业句柄关掉后进程没跟着结束（看 `skin_preview.py` 的 `_start_child`、`_join_job`）。
  - `test_stop_ends_the_whole_tree` 失败 → `stop` 没结束整棵树。
  - `test_runs_normally_when_not_in_job` 失败 → 建不了/放不进作业时没照常运行。
  - `test_resume_failure_starts_again` 失败 → 恢复失败时没另起一个（看 `_resume`）。

## test_skin_window.py：「皮肤」窗口和皮肤缩略图（预览图）

- **测试观点**：
  - 窗口（`WindowTest`）：格子按皮肤先后排成 4＋3 两行、空出的格子写说明；点格子当场换、存进设置文件、「使用中」跟着走；关掉再开、重启都记得；换皮肤时窗口自己换样子、左上角不动、太大时挪回屏幕里；高度不超过屏幕可用高度（放不下时格子滚动）；默认皮肤只有系统标题栏的 ×，自己画外框的皮肤只有一个关闭按钮；「使用中」的标法来自皮肤参数；格子能用 Tab 走到；没有悬浮窗时点了不换。
  - 预览图（`PreviewTest`）：第一次下载识别图片的同时画（预览要用的图排在下载最前面，处理完就画）；图标下载不成时先画色圈、齐了再补画；三选一显示着时先不画；再启动不重画，版本变了/文件坏了/大小不对/皮肤变了或多了/图标后来才齐时重画；画好之前格子里是这套的主色；画的时候不改当前皮肤、不动用户的悬浮窗；文件夹写不进时留在内存；打包试启动（不下载）时不画；`main()` 接上的是在子进程里画的那种。
  - 在子进程里画（`IsolatedPreviewTest`）：子进程画、主程序存；某一套让子进程崩溃/卡住时主程序照常运行、那一套记为画不出来；子进程起不来/没说话就退出时这次显示主色、只记一次错误、下次再试；三选一出现时结束子进程、结束后另起；`stop` 结束子进程；子进程命令行和入口；子进程不写个人目录（下载的识别图片只读，不建、不试写 `%LOCALAPPDATA%` 下的个人目录）；起子进程时环境变量照抄，另带上这次运行决定好的识别图片位置（`paths.child_environ`，`test_child_gets_download_dir`）。
  - 设置窗口「皮肤」一行（`SettingsRowTest`）：显示当前这套的小缩略图和名称、「更换」打开「皮肤」窗口、缩略图画好后换上；只登记一套时这一行不显示。
  - 文件名（`FileNameTest`）：只有字母数字下划线的皮肤名照写，换过字的另接一小段指纹。
  - 每一套画得出来（`RegisteredSkinsTest`）：另起一个进程把随程序发布的每一套都画一遍。
- **检证对象**：`advisor/ui/skin_window.py`（`SkinWindow`、`SkinTile`、`NoteCell`、`HintText`）；`advisor/ui/skin_preview.py`（`PreviewStore`、`main_colors`、`file_name`、`fingerprint`、`scene_icons`、`icons_present`、`child_command`、`MANIFEST`、`VERSION`、`FORMAT`）；`advisor/ui/preview_child.py`（`render`、`child_main`、`trim`）；`advisor/ui/settings/widgets.py`（`SkinThumb`、`SkinRow`）和设置窗口的「皮肤」一行；`advisor/ui/overlay_parts/background.py`（`_preview_first`、`_preview_arrived`）；`advisor/ui/overlay_parts/app.py` 的 `main`；`advisor/paths.py` 的 `use_program_dir`；`theme` 的 `drawing`。
- **什么时候要跑**：改 `advisor/ui/skin_window.py`、`skin_preview.py`、`preview_child.py`；改设置窗口的「皮肤」一行；改 `advisor/ui/overlay_parts/background.py` 里预览图的排队/补画；改 `advisor/paths.py` 的程序目录位置。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_skin_window`（上次约 460 秒，是全套快测里最慢的一个；机器忙时会更久，因为它要起子进程逐像素对照）。
- **需要什么**：没有特别要求，但会起子进程（`IsolatedPreviewTest` 用子进程真画缩略图）；离屏 Qt；要这台电脑装了微软雅黑。测试期间登记的皮肤先换成固定的一组（默认、经典主题-白加几套测试皮肤），跑完按原来的先后登记回去。
- **输出怎么看**：同总则；预览图和设置文件写在 `.tmp` 下，不碰程序目录的 `assets/skins`。子进程崩溃/卡住的用例会把子进程的错误输出写进错误记录（用例自己核对后再清掉）。
- **出错时一般是哪里不对**：
  - `WindowTest` 失败 → 格子排版、「使用中」标记、换皮肤后左上角/大小、高度限制（看 `advisor/ui/skin_window.py`）。
  - `PreviewTest` 失败 → 预览图的排队、补画、重画判断或「画的时候不动用户悬浮窗」（看 `advisor/ui/skin_preview.py`、`overlay_parts/background.py`）。
  - `IsolatedPreviewTest` 失败 → 子进程的起法、崩溃/卡住/沉默的处理、命令行或入口（看 `skin_preview.py`、`preview_child.py`）。
  - `SettingsRowTest` 失败 → 设置窗口「皮肤」一行的缩略图/名称/按钮。
  - `FileNameTest` 失败 → 预览图文件名（非 ASCII 皮肤名的指纹）。
  - `RegisteredSkinsTest` 失败 → 某一套皮肤画不出来（失败信息里写出是哪一套）。

## test_decor_registry.py：卡片和标签的画法登记表

- **测试观点**：
  - 表里没有的种类（`UnknownKindTest`）：`ChromeLook` 里写了登记表里没有的卡片/标签种类时，按浅灰卡片、橙色圆角标签画（尺寸、摆法也一样）。
  - 编号标题的字体（`IndexTagFontTest`）：不给 px 时编号和标题都用传进来的字体；给了 px 时编号用数字字体的窄体、标题用斜体粗体，尺寸也照这个算法算。
- **检证对象**：`advisor/ui/decor.py` 的 `CARD_KINDS`、`TAG_KINDS`、`TAG_PLACE`、`card`、`card_metrics`、`tag`、`tag_size`、`tag_place`、`index_title`、`index_font`、`italic_title_font`；`advisor/ui/theme/` 的 `ChromeLook` 和 `drawing`。
- **什么时候要跑**：改 `advisor/ui/decor.py` 的画法登记表、编号标题的画法；改 `theme` 里 `CARD_KIND`/`TAG_KIND` 的取值。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_decor_registry`（上次约 0.05 秒）。
- **需要什么**：没有特别要求；离屏 Qt，不弹窗口。
- **输出怎么看**：同总则；对照是逐像素比较同一块画两次。
- **出错时一般是哪里不对**：
  - `UnknownKindTest.test_card` 失败 → 不认识的卡片种类没退回浅灰卡片（看 `decor.card`/`card_metrics`）。
  - `UnknownKindTest.test_tag` 失败 → 不认识的标签种类没退回橙色圆角标签（看 `decor.tag`/`tag_size`/`tag_place`）。
  - `IndexTagFontTest.test_without_px_uses_given_font`、`test_with_px_uses_index_fonts` 失败 → 编号标题在有无 px 两种情形下用的字体或尺寸（看 `decor.index_title`、`index_font`、`italic_title_font`、`tag_size`）。

## test_ui_widgets.py：界面部件的回归（条目释放、图标缓存、装饰兜底、颜色写法、文字兜底、皮肤参数校验）

- **测试观点**：
  - 自动换行布局（`FlowLayoutReleaseTest`、`OverlayClearTest`、`OverlayRebuildTest`）：`FlowLayout` 清空、父部件删掉、整块删掉三条路都把条目释放；悬浮窗反复重搭推荐区不再只涨不降；空白的条目（间距、弹簧）也释放；部件数不随轮数增长。
  - 筹码图标缓存（`ChipIconCacheTest`）：图片齐全时缓存键不含皮肤颜色（各套皮肤共用一张）；读不出来时用皮肤颜色，换皮肤以后不能拿上一套画的。
  - 装饰兜底（`DecorGuardTest`）：步长、范围写成 0 时不除零、不死循环。
  - 颜色写法（`ColorParseTest`）：`#rrggbb`、`#rgb`、`rgba(...)`、元组、`QColor` 都认；`widgets.qcolor`、`settings.color` 是它的别名。
  - 文字兜底（`TextHelpersTest`）：没有候选筹码时「刷新」的理由标签、不认识的稀有度取色、超长说明的分行。
  - 悬停说明里的路径（`TipPathTest`）：短路径照旧分行，长路径在「\」后换行、宽度不超过 600 像素。
  - 皮肤参数校验（`SkinParamsTest`）：随程序发布的每套皮肤参数都合法；写错参数时报出来；只读表改不动。
- **检证对象**：`advisor/ui/widgets.py`（`FlowLayout`、`free_item`、`_release_items`、`chip_icon`、`_icon_cache`、`RARITY_BASE`、`qcolor`）；`advisor/ui/overlay_parts/factory.py`（`_clear_grid`、`_clear_layout`）；`advisor/ui/decor.py`（`halftone`、`checker`、`wide_stripes`、`qc`、`rounded`）；`advisor/ui/text.py`（`reroll_pills`、`candidate_views`、`tip_lines`、`tip_names`、`set_tip_names`、`tip_paragraphs_html`）；`advisor/ui/theme/` 的 `validate_skin`、`COMMON.RARITY_COLOR`；`advisor/ui/settings/` 的 `color`。
- **什么时候要跑**：改 `advisor/ui/widgets.py` 的布局/图标/标签；改悬浮窗重搭推荐区的写法；改 `decor.py` 的画法参数处理；改 `text.py` 的兜底；给皮肤加参数（校验会拦）。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_ui_widgets`（上次约 1.7 秒）。
- **需要什么**：没有特别要求；离屏 Qt，不弹窗口。
- **输出怎么看**：同总则；`ChipIconCacheTest` 把筹码图放在临时文件夹里，和这台电脑上没下载图片无关。
- **出错时一般是哪里不对**：
  - 条目释放相关的用例失败 → `FlowLayout.clear`、`factory._clear_grid`/`_clear_layout` 没释放取出的条目（部件数只涨不降）。
  - `ChipIconCacheTest` 失败 → 图标缓存键里混进了皮肤颜色，或读不出的图没按皮肤重画（看 `widgets.chip_icon`）。
  - `DecorGuardTest` 失败 → `decor.halftone`/`checker`/`wide_stripes` 在步长/范围 0 时除零或死循环。
  - `ColorParseTest` 失败 → `decor.qc` 少了某种写法或别名对不上。
  - `TextHelpersTest` 失败 → `text.reroll_pills`/`candidate_views`/`tip_lines` 的兜底。
  - `TipPathTest` 失败 → 悬停说明里长路径的换行或宽度（看 `text.tip_lines`）。
  - `SkinParamsTest.test_all_shipped_skins_valid` 失败 → 某一套皮肤的参数写错了（失败信息里有参数名）。

## test_ui_no_raw_errors.py：守卫，界面上不出现报错原文

- **测试观点**（`NoRawErrorsTest`）：
  - 用语法树扫 `advisor/ui` 下（含子文件夹）把文字交给界面的调用，参数里用到接住的异常、`str(e)`、`type(e).__name__`、`traceback` 等的，报出来（报错原文只该进错误记录和诊断包）。
  - 扫描认得那几种写法，也不误报装在别的函数里的（本该由那个函数写中文）和退出码这类。
  - 允许名单里的每一条，扫描在那个文件里照样报得出来（不再需要时把这一条删掉）。
  - 软件内更新写给玩家看的原因（`advisor/update.py` 的 `UpdateError` 的文字、`CheckResult` 的 `problem`，界面原样显示）里不带报错原文：用同一个扫描器扫 `advisor/update.py`，出错的类型和错误代码、WinHTTP 的原因要经固定的写法，库的报错原文只进错误记录（`test_update_texts_have_no_raw_errors`）。
- **检证对象**：`advisor/ui` 下所有源码（语法树扫描）；`SINKS` 里登记的界面调用；`advisor/update.py`（`UPDATE_TEXT_SINKS`：`UpdateError`、`CheckResult`）；允许名单（现在列了三处：`advisor/ui/hotkey.py` 热键写法无效的中文原因 `f"{e}。"`、`str(e)`，`advisor/ui/overlay_parts/updates.py` 下载线程交给界面的 `UpdateError` 的文字 `(stage[0], str(e))`）；`advisor/errors.py` 的错误记录。
- **什么时候要跑**：改 `advisor/ui` 下任何往界面写文字的地方；改 `advisor/update.py` 里写给玩家看的原因；新增把异常文字交给界面的写法；改错误记录/启动失败提示的写法。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_ui_no_raw_errors`（上次约 0.8 秒）。
- **需要什么**：没有特别要求；不建窗口、不跑程序。
- **输出怎么看**：同总则；失败时列出「文件:行号：把 …… 交给了界面」，把报错原文改写成固定中文、原文走错误记录即可。
- **出错时一般是哪里不对**：
  - `test_ui_does_not_show_raw_errors` 失败 → 某处把异常文字/模块名/报错原文直接写上了界面，改成固定中文（原文用 `log_error` 记）。
  - `test_scanner_recognizes_raw_errors` 失败 → 扫描器本身的识别规则被改坏了（认不出那几种写法或误报了安全写法）。
  - `test_allowlist_still_needed` 失败 → 允许名单里某条现在扫描已经报不出来，删掉那条（或写法变了）。
  - `test_update_texts_have_no_raw_errors` 失败 → `advisor/update.py` 某处把报错原文拼进了 `UpdateError` 或 `CheckResult` 的原因，改成固定的写法（原文用错误记录记）。

## test_label_interaction.py：标签的文字交互（按在文字上也能拖动悬浮窗）

- **测试观点**：
  - `NoGlobalFilterTest`：程序上不挂「第一次显示时把 `QLabel` 设成不可交互」的全局过滤器——建出悬浮窗以后，另建的可选中的普通标签交互设置不被改掉。
  - `OverlayLabelTest`：悬浮窗（一行显示、全部展开、三选一推荐、带链接的提示行、补录行、补录栏）里除了自己处理链接的标签，都是 `NoTextInteraction`；带链接的提示行能点。「自己处理链接的」指交给 Qt 打开链接的（`openExternalLinks`），或文字里有链接、点了由程序接的（`linkActivated`：提示行的链接都这样接，「关于」那个链接打开「关于」窗口、不交给浏览器）。
  - `OtherWindowsLabelTest`：设置、编辑方案、导入/导出、皮肤窗口里该不交互的标签都是 `NoTextInteraction`。
- **检证对象**：各处直接设文字交互的地方——`advisor/ui/overlay_parts/factory.py`、`layout.py`、`owned_card.py`、`option_widgets.py`、`notices.py`；`advisor/ui/widgets.py`；`advisor/ui/scheme_editor/widgets.py`；`advisor/ui/scheme_share.py`、`about.py`、`settings/`；悬浮窗 `advisor/ui/overlay.py`。
- **什么时候要跑**：在建标签的地方改动文字交互设置（新增/删除 `setTextInteractionFlags`）；改悬浮窗或各窗口里标签的建法；有人想加全局事件过滤器时。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_label_interaction`（上次约 1.6 秒）。
- **需要什么**：没有特别要求；离屏 Qt；设置、方案文件写在测试沙箱里。
- **输出怎么看**：同总则；`OverlayLabelTest.test_labels_in_states` 会先造出「免费软件」声明那条带链接的提示行来核对链接标签。
- **出错时一般是哪里不对**：
  - `NoGlobalFilterTest.test_other_label_keeps_its_flags` 失败 → 装了全局事件过滤器（应在建标签的地方直接设）。
  - `OverlayLabelTest` 失败 → 悬浮窗某个标签没设成 `NoTextInteraction`（按在字上不能拖动），或链接标签不能点（看 `notices.py`）。
  - `OtherWindowsLabelTest` 失败 → 某个窗口里该不交互的标签能选中文字了。

## test_qt_translation.py：Qt 自带控件的中文翻译，以及它在自检和打包里的核对

- **测试观点**：
  - 安装（`QtTranslationTest`）：装上以后输入框（`QLineEdit`、`QPlainTextEdit`）的右键菜单是中文（以翻译文件里的实际内容为准）；翻译器留在模块里不被回收；卸下以后恢复英文；候选位置先 Qt 报告的翻译目录、再 PySide6 包目录下的 `translations`；找不到翻译文件或找的时候出错时不抛异常、只记一条错误记录；悬浮窗的 `build()` 在建好 `QApplication` 以后、建窗口以前装上。
  - 自检（`TranslationSelfCheckTest`）：开发环境里载入得了；找不到、载入的文件没把「复制」译成中文时不通过；不算关键项；找不到时 `translation_check` 抛 `FileNotFoundError`；打包试启动时这一项没通过算打包失败（结果里没有这一项时不算）。
  - 许可证（`QtLicenseNoteTest`）：随发行版的 `LICENSES/Qt-PySide6.txt` 开头和 `build_release.QT_NOTE` 一字不差、写着随附了这个翻译文件；打包脚本里没有被当成转义的反斜杠（不被 `SyntaxWarning` 拦下）。
- **检证对象**：`advisor/ui/translation.py`（`install`、`load`、`candidate_files`、`TRANSLATION_FILE`、`TITLE`、`PROBE`、`_reset_for_tests`、`_translator`）；`advisor/diagnostics.py`（`translation_check`、`TRANSLATION_CHECK`、`self_check`、`_render_text`、`window_capture_check`）；`tools/build_release.py`（`QT_NOTE`、`QT_TRANSLATION_DEST`、`QT_TRANSLATION_SOURCE`、`smoke_problems`）；`LICENSES/Qt-PySide6.txt`；`advisor/ui/overlay_parts/app.py` 的 `build`。
- **什么时候要跑**：改 `advisor/ui/translation.py`；改 Qt/PySide6 版本或翻译文件的路径/文件名；改 `advisor/diagnostics.py` 的自检项；改 `tools/build_release.py` 的 `QT_NOTE`、翻译文件打包路径或许可证收集；改 `LICENSES/Qt-PySide6.txt`。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_qt_translation`（上次约 2 秒）。
- **需要什么**：没有特别要求；用离屏 `QApplication`；不编译、不打包、不启动程序。`test_candidate_files` 会核对 PySide6 包里确实有那个翻译文件。
- **输出怎么看**：同总则；这一组会往 `QApplication` 上装/卸翻译器，跑完会卸下，不串到别的测试。
- **出错时一般是哪里不对**：
  - `test_installs_and_translates` 失败 → 翻译装上以后菜单不是中文，或翻译器被回收了（看 `translation.install`）。
  - `test_candidate_files` 失败 → 候选位置少了一处或打包目标路径变了（看 `translation.candidate_files`、`build_release.QT_TRANSLATION_DEST`）。
  - `test_missing_file_logs_once`、`test_lookup_error_does_not_raise` 失败 → 找不到/出错时不该抛异常，只记一条错误记录。
  - `test_build_installs_before_windows` 失败 → `build()` 里装翻译的位置挪了（应在建 `QApplication` 之后、读设置和建窗口之前）。
  - `TranslationSelfCheckTest` 失败 → 自检「界面翻译」一项的通过条件或它在打包里的要求（看 `diagnostics.translation_check`、`build_release.smoke_problems`）。
  - `QtLicenseNoteTest.test_shipped_note_matches` 失败 → `QT_NOTE` 和随发行版的文件对不上（改了说明只改一处）。
  - `test_no_stray_escapes` 失败 → 打包脚本里写了单反斜杠（`\t` 会变成制表符之类）。

## test_window_state.py：换皮肤以后各个独立窗口保持原样

- **测试观点**
  - `test_editor_keeps_object_and_inputs`／`test_editor_scroll_restored`／`test_editor_keeps_folds`：编辑窗口换来换去仍停在原来的角色、方案、搜索文字、只看已标记、滚动位置和列表各组的收起展开
  - `test_editor_missing_scheme_falls_back`／`test_editor_deleted_scheme_falls_back_to_current`：换皮肤期间选中的方案被删掉时退回初始做法，不报错
  - `test_editor_offscreen_position_moved_back`／`test_share_and_about_offscreen_moved_back`：窗口位置在屏幕外时按「按屏幕缩小」的规矩挪回可用区域
  - `test_editor_keeps_other_hero_and_scheme`：编辑的不是悬浮窗当前角色、选的也不是那个角色在用的方案时，也一直停在原来的角色和方案
  - `test_editor_unsaved_text_kept`：名称框改了、还没到存的时候就换皮肤，关旧窗口时照常存盘，重开的窗口是改过的名称
  - `test_share_keeps_inputs`／`test_share_keeps_filter_and_imported_lines`／`test_share_tab_restored`／`test_share_opens_import_tab_when_importing`：导入/导出窗口的粘贴内容、勾选、格式、页签都在，从没有页签的排法换过来时正在导入就停在「导入」页
  - `test_about_section_kept`：关于窗口停在原来那一节
  - `test_settings_position_kept`／`test_settings_tab_kept`：设置、皮肤窗口在原窗口里重建，位置和页签不动
  - `test_restore_error_does_not_stop_other_windows`／`test_state_error_reopens_as_new`：某个窗口交出、接回出错时只记错误，别的窗口照常重开
  - `test_esc_follows_window_frame`：Esc 按窗口实际有没有系统标题栏决定关不关
- **检证对象**：`advisor/ui/window_base.py` 的 `SkinnedWindow`（`state`／`restore`／`layout_name`）；`advisor/ui/theme/`；`advisor/ui/scheme_editor/` 的 `SchemeEditor.state`／`restore` 与筹码列表的折叠、滚动；`advisor/ui/scheme_share.py` 的 `SchemeShareWindow`；`advisor/ui/about.py` 的 `AboutDialog`；`advisor/ui/settings/dialog.py` 的 `SettingsDialog`；`advisor/ui/overlay.py` 的对话框管理
- **什么时候要跑**：改了窗口的「交出／接回状态」、换皮肤重建窗口的逻辑，改了编辑窗口、导入/导出窗口、关于窗口、设置窗口里的状态（页签、滚动、折叠、窗口位置），或改了 Esc 关窗的规矩，都要跑
- **怎么单独跑**：`python -B tools/run_tests.py --only test_window_state`（上次约 10.8 秒）
- **需要什么**：离屏平台，不弹窗；设置文件、方案文件写在测试沙箱里；运行期间换一张空的换皮肤通知名单，跑完换回默认皮肤
- **输出怎么看**：窗口和方案文件的临时文件夹在沙箱里；出错（错误记录）会被接住并在结束时核对；其余同总则
- **出错时一般是哪里不对**
  - 编辑窗口那几条失败 → 交出／接回状态漏了角色、方案、搜索文字、只看已标记、滚动位置或分组折叠（`advisor/ui/scheme_editor/`）
  - `test_editor_missing_scheme_falls_back`／`test_editor_deleted_scheme_falls_back_to_current` 失败 → 换皮肤期间方案被删时没退回初始做法或报错（`advisor/ui/scheme_editor/`）
  - 屏幕外位置那两条失败 → 接回以后没按「按屏幕缩小」挪回可用区域（`advisor/ui/window_base.py`）
  - `test_editor_unsaved_text_kept`／`test_share_*` 失败 → 关旧窗口时该存盘的没存、或接回时多写了方案文件／多发了信号（`advisor/ui/scheme_editor/`、`advisor/ui/scheme_share.py`）
  - `test_about_section_kept` 失败 → 关于窗口没停在原来那一节（`advisor/ui/about.py`）
  - `test_settings_position_kept`／`test_settings_tab_kept` 失败 → 设置窗口被重开或页签没接回（`advisor/ui/settings/dialog.py`、`advisor/ui/window_base.py`）
  - `test_restore_error_does_not_stop_other_windows`／`test_state_error_reopens_as_new` 失败 → 某个窗口出错时把别的窗口也拖住了，或错误记录条数不对
  - `test_esc_follows_window_frame` 失败 → Esc 关窗看的是皮肤招牌而不是窗口实际的系统标题栏（`advisor/ui/window_base.py`）

## test_icon.py：程序图标（`packaging/app.ico`）的图案

- **测试观点**
  - `test_shape_and_colors`：深色圆盘、金色圆环、正中一颗金色四角星；每个尺寸的四角都透明、正中是星星的颜色、圆环中线上（上、左、右三处）是圆环的颜色、45° 方向上星星和圆环之间是圆盘的颜色
  - `test_letters_only_from_96`：96 像素以上外圈整圈都有字（有画满、颜色和外圈的字一致的像素），更小的尺寸一个字都没有
  - `test_app_icon_has_every_size`：程序图标里含各个尺寸
  - `test_packaged_ico_matches_drawing`：仓库里的 `packaging/app.ico` 和现在 `render_icon` 的画法逐点一致
- **检证对象**：`advisor/ui/icon.py` 的 `render_icon`、`app_icon`、`DISC`、`RING`、`STAR`、`LETTERS`、`CANVAS`、`DISC_RADIUS`、`LETTERS_MIN`、`LETTERS_RADIUS`、`ICON_SIZES`、`_params`、`_star_path`、`_letters_path`、`_outer_font`；`packaging/app.ico`
- **什么时候要跑**：改了图标的画法、圆盘／圆环／星星／外圈字的颜色或尺寸表，或改了 `packaging/app.ico`，都要跑
- **怎么单独跑**：`python -B tools/run_tests.py --only test_icon`（上次约 0.9 秒）
- **需要什么**：离屏平台；外圈的字要有 Bahnschrift 才画得出来（Windows 10 起自带），读 `packaging/app.ico` 那一条要用到图像库（Pillow）
- **输出怎么看**：`test_packaged_ico_matches_drawing` 失败时会写明「删掉 `packaging/app.ico` 再运行 `ensure_icon()`（或重新打包）」；其余同总则
- **出错时一般是哪里不对**
  - `test_shape_and_colors` 失败 → 圆盘／圆环／星星的颜色或尺寸表改了，或四角没留透明（`advisor/ui/icon.py` 的 `DISC`／`RING`／`STAR`／`DISC_RADIUS`／`_params`／`render_icon`）
  - `test_letters_only_from_96` 失败 → 外圈字从哪个尺寸开始写、基线半径或颜色改了（`LETTERS_MIN`／`LETTERS_RADIUS`／`LETTERS`／`_letters_path`）
  - `test_app_icon_has_every_size` 失败 → 尺寸表改了（`advisor/ui/icon.py` 的 `ICON_SIZES`）
  - `test_packaged_ico_matches_drawing` 失败 → 画法改了但仓库里的 `packaging/app.ico` 没重画；删掉它再运行 `ensure_icon()` 或重新打包
  - 有意改了画法时，这几条会一起失败，按上面的做法重新生成图标即可
