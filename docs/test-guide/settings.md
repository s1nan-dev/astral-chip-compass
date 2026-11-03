# 测试指南 · 设置和启动退出

这是[《测试指南》](../测试指南.md)第八节的一章：每个测试文件一节，每节的结构见那里第八节的开头。


这一章管设置文件的读写与存放位置、设置窗口、启动检查与防多开、退出收尾和错误记录。

## test_settings_file.py：设置文件 `state.json` 的读写、备份与坏文件处理

- **测试观点**
  - `ReadTest`：程序写出的设置照读、原样再存不重写；某一项类型不对只这一项按默认并记警告；每一项（对局状态以外）写成对象时都按默认；缺项按默认；浮点数照读（NaN、无穷大、超出 32 位整数按默认）；看不懂的值照读；整份读坏时另存成 `.broken-时间` 并改读备份恢复；没有备份时按默认设置但仍算原来有设置文件；文件在却读不了时这次运行不写它
  - `OrphanBackupTest`：读坏了、备份没能用上时第一次保存前把备份另存为 `.bak.orphan-时间`，之后不覆盖它；另存的名字重了接 `-2`；另存不成时照常写设置文件但不碰 `.bak`
  - `SaveTest`：第一次存不建 `.bak`，之后每次真写时原来的内容留作 `.bak`；写时刷进硬盘；同样的文字只换行不重写；外面改坏的文件不拿去盖掉 `.bak`；返回值是存进的文件或 `False`
  - `ForceSaveTest`：`force=True` 跳过退避等待、读不了的文件强制也不写；`Session.save` 透传 `force`
  - `ExitSaveTest`：`main()` 退出时强制再存一次；后台识别一直占锁时跳过并记错误记录
  - `HotkeyNumberTest`：热键写成数字时读时按默认、设置窗口照常打开，万一数字到了窗口按未设置显示
  - `UnlockRecordTest`：设置文件里加密存的那一项（`aux`）解不开、这次又读不到机器标识时不盖掉它，皮肤一项也照原样写回；读得到机器标识还解不开的照旧整串换掉
- **检证对象**：`advisor/prefs.py` 的 `Settings.read`／`Settings.save`／`_write_settings`；`advisor/ui/overlay_parts/app.py` 的 `UiSettings`、`load_settings`、`main`、`_save_on_exit`；`advisor/session/` 的 `Session.save`、`GAME_FIELDS`；`advisor/ui/settings/dialog.py` 的键帽；`advisor/unlock.py` 的 `load`／`seal`／`unseal`／`machine_id`；`advisor/ui/theme/` 的皮肤
- **什么时候要跑**：改了 `advisor/prefs.py` 的读、写、备份、坏文件处理与另存，`advisor/fileio.py` 的写盘，`advisor/ui/overlay_parts/app.py` 的 `load_settings`／`UiSettings`／退出保存，设置窗口热键键帽，`advisor/unlock.py` 的存取（`seal`／`unseal`／`load`），都要跑
- **怎么单独跑**：`python -B tools/run_tests.py --only test_settings_file`（上次约 1.7 秒）
- **需要什么**：界面那几条要离屏平台，不弹窗；设置文件写在测试沙箱里；读不了、另存不成、恢复不成用替身造，不真的占用文件
- **输出怎么看**：记警告的用例用 `assertLogs` 收；没有别的特有输出，同总则
- **出错时一般是哪里不对**
  - `ReadTest.test_good_file_read_and_not_rewritten` 失败 → 原样再存时重写了文件或建了 `.bak`（`advisor/prefs.py` 的 `Settings.save`）
  - `ReadTest` 的 `test_bad_field_only_that_field`／`test_every_field_checked`／`test_missing_fields`／`test_float_numbers_kept` 失败 → 各项的类型检查（`_fits`）改坏：类型不对的没按默认、缺项没按默认、数值范围没拦住
  - `ReadTest.test_lenient_values_kept` 失败 → 不该拦的宽松值被当成错拦下了
  - `ReadTest` 的 `test_broken_file_uses_backup`／`test_broken_file_without_backup`／`test_broken_file_and_backup`／`test_new_user_only_without_file` 失败 → 坏文件另存、用备份恢复、或「新用户」的判断改坏（`advisor/prefs.py` 的 `Settings.read`）
  - `ReadTest` 的 `test_unreadable_file_not_written`／`test_broken_file_not_set_aside_is_not_written`／`test_broken_file_not_kept_uses_backup_values`／`test_restore_failed_keeps_backup` 失败 → 读不了的文件照写、坏文件没留一份就被盖
  - `OrphanBackupTest` 失败 → 不能用的备份没被另存为 `.bak.orphan-时间`，或每存一次都去试另存（`advisor/prefs.py` 的 `SETTINGS_ORPHAN`）
  - `SaveTest` 失败 → `.bak` 不是上一版、没刷硬盘、坏内容盖掉了 `.bak`、返回值不对（`advisor/fileio.py`、`advisor/prefs.py`）
  - `ForceSaveTest` 失败 → `force` 没跳过退避等待，或对读不了的文件也照写（`advisor/prefs.py`、`advisor/session/`）
  - `ExitSaveTest` 失败 → 退出时没强制再存，或占锁时没跳过并记错误（`advisor/ui/overlay_parts/app.py` 的 `main`）
  - `UnlockRecordTest` 失败 → 该保住 `aux` 那一项与皮肤一项时盖掉了，或该换掉时没换（`advisor/unlock.py`）；皮肤只按「可不可用」判断
  - 有意改了设置文件的格式、默认值或备份命名时，这里的预期要跟着改

## test_settings_background.py：设置文件写不进时交给后台写、退出时补写的测试

- **测试观点**
  - `LockedFileTest`：设置文件被别的程序独占打开时，`save` 很快返回、不等磁盘，交给后台写盘；放开以后后台写进最新一份；退避期间改了几次只写最后一份；当场写成了就不写排着的旧内容；后台正在等文件时又交来新内容照样很快返回
  - `SessionTest`：交给后台的改动又改回上一次存成的样子就不写了；识别线程持着共用锁存设置时，界面线程拿锁不用跟着等磁盘
  - `BackupLockedTest`：只有 `.bak` 被占用时不卡，`.bak` 由后台补；读坏了、备份没能用上时第一次保存前把备份另存成 `.bak.orphan-时间`
  - `FallbackInBackgroundTest`：发行版个人目录里的设置写不进时，后台改存到程序文件夹，之后的保存直接存在那里、`Session` 换过去
  - `ExitFlushTest`：退出时不管退避把排着的限时写完；一直被占用时到点放弃并记错误记录；退出前就开始、试的不是最新内容的那次不算试过
  - `WriterThreadTest`：后台写的线程是守护线程、排着的写完就自己退出、没人用的那份设置不再替它写、程序出错记进错误记录
  - `NoWaitTest`：写盘工具 `wait=False` 时被占用马上报 `Busy`、只读的照原样报、只有 `.bak` 被占用时交给 `backup_later`
- **检证对象**：`advisor/prefs.py` 的 `SettingsWriter`、`Settings.save`、`_write_settings`、`SETTINGS_RETRY_S`、`SETTINGS_ORPHAN`、`WRITER_THREAD`；`advisor/fileio.py` 的 `write_text`／`write_bytes`（`wait=False`）、`Busy`、`write_backup`、`move_aside`、`RETRY_DELAYS`、`_read`；`advisor/ui/overlay_parts/app.py` 的 `_save_on_exit`、`EXIT_FLUSH_S`；`advisor/session/` 的 `Session.save`；`advisor/paths.py` 的 `storage_fallback`、`STATE_FALLBACK_NAME`、`FALLBACK_STATE_FAILED`
- **什么时候要跑**：改了 `advisor/prefs.py` 的写盘队列、`advisor/fileio.py` 的写盘与重试、`advisor/ui/overlay_parts/app.py` 的退出收尾、`advisor/session/` 的 `save`、`advisor/paths.py` 的「个人目录写不进时改存程序文件夹」，都要跑
- **怎么单独跑**：`python -B tools/run_tests.py --only test_settings_background`（上次约 3.8 秒）
- **需要什么**：Windows（用 `CreateFileW` 独占打开来模拟别的程序占着文件，非 Windows 上整个模块跳过）；离屏平台、不起进程、不开窗口；临时文件写在测试沙箱里
- **输出怎么看**：非 Windows 时整模块的用例报跳过。后台写不进时的警告默认被压住，要核对的用例才用 `assertLogs` 收。其余同总则
- **出错时一般是哪里不对**
  - `LockedFileTest.test_returns_fast_and_written_when_free` 失败 → `save` 在调用线程里等了磁盘，或放开以后后台没写进、`.bak` 不是换下来的那一版（去看 `advisor/prefs.py` 的 `SettingsWriter`、`advisor/fileio.py` 的 `write_text`）
  - `LockedFileTest` 的 `test_latest_written_after_backoff`／`test_written_now_drops_queued`／`test_late_background_write_skipped`／`test_writing_in_background_then_newer` 失败 → 后台写盘的排队或「按序号判断旧的不再写」改坏了（`advisor/prefs.py` 的 `SettingsWriter`）
  - `SessionTest.test_reverted_change_not_written_later` 失败 → 改回原样仍写进文件（`advisor/session/` 的 `Session.save`）
  - `SessionTest.test_lock_holder_does_not_wait` 失败 → 持共用锁存设置的线程等磁盘、界面线程跟着等（`advisor/fileio.py` 里 `wait=False` 的分支）
  - `BackupLockedTest` 失败 → `.bak` 那一步在调用线程里等，或 `.bak` 没补上、`.bak.orphan-时间` 没另存（`advisor/fileio.py` 的 `write_backup`／`backup_later`，`advisor/prefs.py` 的 `_keep_orphan`）
  - `FallbackInBackgroundTest` 失败 → 发行版改存到程序文件夹没做或没告诉 `paths`（`advisor/paths.py` 的 `storage_fallback`、`advisor/prefs.py`）
  - `ExitFlushTest` 失败 → 退出时没把排着的写完，或一直被占用时没到点放弃、没记错误记录（`advisor/ui/overlay_parts/app.py` 的 `_save_on_exit`）
  - `WriterThreadTest` 失败 → 后台线程不是守护线程、出错没记进错误记录、或没人用的设置还替它写（`advisor/prefs.py` 的 `SettingsWriter`）
  - `NoWaitTest` 失败 → `wait=False` 该马上报 `Busy` 却等了，或只读文件报成了 `Busy`（`advisor/fileio.py`）
  - 改了重试间隔、退避时长这类常量的默认值时要连带看这里的假值，不必改断言

## test_settings_window.py：设置窗口（「拾星助手设置」）的项、排法与联动

- **测试观点**
  - `WordingTest`：悬浮窗与设置窗口上的界面文字逐字核对；「关于拾星助手」那行的说明照「关于」窗口的各节列出
  - `LayoutTest`：有哪些项（`ITEMS`）和怎么排；默认皮肤下的分组卡片、行图标、各段文字不被截掉、窗口高度等于内容高度；系统没有图标字体时只写字；屏幕矮时窗口内滚动；可点的都是手形光标
  - `ModeTest`：识别模式两块方块（点选切换、存盘、提示一次、按当前热键写说明、点已选中的那块不做事）
  - `HotkeyRowTest`：识别热键那一行的键帽和说明（平时、等待按键、按了不能用的键、被占用、写法无效、未设置），以及没有悬浮窗时显示默认值
  - `OpaqueTest`：「悬浮窗背景不透明」开关即时改底色、存盘、重启后照旧、读到非布尔值按关
  - `NewUserTest`：新用户「已有筹码」卡片默认收起，已有设置文件的照文件的；「全部展开」不改卡片自己的折叠状态
  - `TitleAndToolbarTest`：标题栏只剩标题、「全部折叠」、×，底栏只剩「设置」；「关于拾星助手」整行可点；「导出诊断」调用悬浮窗的方法
  - `SchemeButtonsTest`：「筹码方案」卡片的「编辑方案」「导入/导出」两个图标按钮的样子、打开的窗口、两个下拉框变窄后最长的角色名仍完整显示
- **检证对象**：`advisor/ui/settings/dialog.py`（`SettingsDialog`、`ITEMS`、`SettingsValues`、`hotkey_texts`、`manual_desc`、`shown_items`、`set_values`、`fit_size`）、`advisor/ui/settings/layouts.py`、`advisor/ui/settings/widgets.py`、`advisor/ui/glyphs.py`；`advisor/ui/overlay_parts/wording.py` 的界面文字常量；`advisor/ui/overlay.py` 的 `set_manual`／`set_lowend`／`show_about`／`export_diagnostics`、卡片折叠；`advisor/ui/overlay_parts/factory.py` 的 `IconTextButton`；`advisor/ui/theme/`
- **什么时候要跑**：改了 `advisor/ui/settings/` 下的项表、排法、文字、控件，改了 `advisor/ui/overlay_parts/wording.py` 里这些界面文字，改了悬浮窗的标题栏／底栏／卡片按钮，或改了角色下拉框宽度，都要跑
- **怎么单独跑**：`python -B tools/run_tests.py --only test_settings_window`（上次约 2.4 秒）
- **需要什么**：离屏平台，不弹窗；设置文件、方案文件写在测试沙箱里；`test_icons` 与涉及系统图标字体的断言在系统没有图标字体时跳过
- **输出怎么看**：窗口和方案文件的临时文件夹在测试沙箱里；出错（错误记录）会被 `capture_errors` 接住并在结束时核对为空；其余同总则
- **出错时一般是哪里不对**
  - `WordingTest` 失败 → 界面文字被改动，或「关于」窗口的各节改了而说明没跟着改（`advisor/ui/overlay_parts/wording.py`、`advisor/ui/settings/dialog.py`、`advisor/version.py` 的 `about_sections`）
  - `LayoutTest.test_items` 失败 → `ITEMS` 的项、组、类型、动作，或排法表里漏了／多了一项（`advisor/ui/settings/dialog.py`、`layouts.py`）
  - `LayoutTest.test_layout`／`test_scroll_when_screen_short` 失败 → 各项的先后、控件位置、文字被截掉、窗口高度或滚动改坏（`advisor/ui/settings/`）
  - `LayoutTest.test_icons`／`test_without_icon_font` 失败 → 行图标或「没有图标字体时只写字」改坏（`advisor/ui/glyphs.py`、`advisor/ui/settings/`）
  - `ModeTest` 失败 → 识别模式切换、存盘、提示文字、或手动模式说明按热键写改坏（`advisor/ui/overlay.py` 的 `set_manual`、`advisor/ui/settings/dialog.py` 的 `manual_desc`）
  - `HotkeyRowTest` 失败 → 键帽／说明的各种状态改坏（`advisor/ui/settings/dialog.py` 的 `hotkey_texts`）
  - `OpaqueTest` 失败 → 开关的即时改底色、存盘或取值判断改坏（`advisor/ui/overlay.py`、`advisor/ui/settings/dialog.py`）
  - `NewUserTest` 失败 → 新用户卡片折叠或「全部展开」的行为改坏（`advisor/ui/overlay_parts/wording.py` 的 `NEW_USER_COLLAPSED`、`advisor/ui/overlay.py`）
  - `SchemeButtonsTest` 失败 → 两个图标按钮或角色下拉框宽度改坏（`advisor/ui/overlay_parts/factory.py`、`advisor/ui/overlay.py`）
  - 有意改了这些界面文字时，`WordingTest` 的逐字预期要跟着改

## test_settings_package.py：设置窗口包的导入面

- **测试观点**
  - `test_exported_names`：能从 `advisor.ui.settings` 取到的名字（清单在测试里）都在
  - `test_same_objects`：包上再导出的名字和子模块里的是同一个对象，不是抄的一份
  - `test_no_writes_on_package`：测试和工具不在包本身上替换、改写名字（要打在定义它的子模块上）
  - `test_scanner`：扫描「有没有写在包上」的规则本身认得对
  - `test_any_import_order`：各子模块按任意顺序先导入都不报循环导入，包、子模块、窗口类三处的排法表和行部件是同一个对象
- **检证对象**：`advisor/ui/settings/__init__.py`（再导出）与 `dialog.py`／`layouts.py`／`widgets.py`／`palette.py`；`tools/`、`tests/` 里对包的打桩写法
- **什么时候要跑**：改了 `advisor/ui/settings/` 的模块拆分、`__init__.py` 的再导出、子模块之间的导入关系，或在测试／工具里新增／改动对 `advisor.ui.settings` 的打桩，都要跑
- **怎么单独跑**：`python -B tools/run_tests.py --only test_settings_package`（上次约 16.6 秒，主要是起子进程核对导入顺序）
- **需要什么**：起子进程（用离屏平台跑导入核对）；不改文件；扫 `tests/`、`tools/` 的源码
- **输出怎么看**：子进程出错时把它的标准错误末尾截下来报出来；核对结果本身是子进程最后一行 JSON。其余同总则
- **出错时一般是哪里不对**
  - `test_exported_names`／`test_same_objects` 失败 → 包里少再导出了某个名字，或再导出的是另一份（`advisor/ui/settings/__init__.py`）
  - `test_no_writes_on_package` 失败 → 测试或工具在包上打了桩／改了名（要打在 `advisor/ui/settings/<子模块>.py` 上）
  - `test_scanner` 失败 → 扫描器认「写在包上」的规则改坏（本测试文件里的 `package_writes`）
  - `test_any_import_order` 失败 → 子模块之间出现循环导入，或三处的排法表／行部件不是同一个对象
  - 新增、改名设置窗口的子模块时，这里的再导出清单要跟着改

## test_lowend.py：低配模式（拉长节奏、减识别线程）与启动时声明高性能

- **测试观点**
  - `RulesTest`：`rules.toml` 写明的倍数和代码里的默认值一致
  - `PaceTest`：识别流程的节奏按构造时的原值乘、不累乘，已记下的时刻和沿用的结论不动，改回 1 倍时逐位是原值；三选一补认、再读左上角之前等多久（`[watch]` 的 `select_reread_ms`、`hud_retry_ms`）也跟着乘倍数
  - `WatcherPaceTest`：识别线程在两帧之间换节奏和文字识别线程数，开关变了才换，下载完图片重建识别流程时照开关重新套上；三选一显示着时和出错后的等待不变
  - `OcrThreadsFunctionTest`／`OcrThreadsTest`：文字识别按低配的线程数建、缓存的引擎线程数不同时丢掉重建、正在识别的那一帧照旧、核心少算下来一样时不重建、开关没开过时不碰
  - `SettingsTest`：`UiSettings.lowend` 默认关、旧文件缺这项按关、读到非布尔值按关
  - `OverlayTest`：`Overlay.set_lowend` 存盘、记一条用户操作、经信号告诉识别线程、和当前相同则不做事
  - `MainTest`：`main()` 在识别线程开始前按设置套上、接好信号，启动时先声明高性能再建界面；`build()` 不声明
  - `OpenExternalTest`：`winapi.open_folder` 按普通优先级另起资源管理器、只认已有文件夹、起不了时退回并只记一次错误
  - `ListenerStartTest`：等「显示」请求的线程在开始等之前调一次 `on_start`，出错只记错误照样等
- **检证对象**：`advisor/vision/pipeline.py` 的 `FrameProcessor.set_pace`（含三选一补认与再读的等待 `reread_s`、`retry_s`）；`advisor/ui/overlay_parts/watcher.py` 的 `Watcher.set_lowend`、`LOWEND_FACTOR`、`NO_FRAME_WAIT_MS`、`NO_FRAME_RETRIES`；`advisor/vision/chip_select.py` 的 `ocr_threads`、`use_lowend_threads`、`LOWEND_OCR_THREADS`、`_engine`；`data/` 里 `rules.toml` 的 `[watch]`（含 `select_reread_ms`、`hud_retry_ms`）；`advisor/ui/overlay_parts/app.py` 的 `UiSettings.lowend`、`build`、`main`、`declare_high_performance`；`advisor/ui/overlay.py` 的 `set_lowend`；`advisor/ui/winapi.py` 的 `open_folder`；`advisor/ui/single_instance.py` 的 `listen`
- **什么时候要跑**：改了低配模式的节奏倍数、文字识别线程数、识别线程的换挡逻辑、`main()` 的启动顺序与高性能声明、`open_folder`、或等「显示」请求线程的启动回调，都要跑
- **怎么单独跑**：`python -B tools/run_tests.py --only test_lowend`（上次约 2.6 秒）
- **需要什么**：离屏平台、不开窗口；识别线程真的起一个，但取帧对象和识别流程都是假的、不截屏；文字识别引擎换成假的；起进程的调用换成假的
- **输出怎么看**：没有特别的，同总则
- **出错时一般是哪里不对**
  - `RulesTest.test_factor_written_out` 失败 → `rules.toml` 的倍数和代码默认值不一致（`advisor/ui/overlay_parts/watcher.py` 的 `LOWEND_FACTOR`）
  - `PaceTest` 失败 → 节奏是累乘的、改了不该动的状态，或三选一补认与再读的等待没跟着乘倍数（`advisor/vision/pipeline.py` 的 `set_pace`）
  - `WatcherPaceTest` 失败 → 换挡时机、按 `[watch]` 取倍数、或三选一／出错后的等待改坏（`advisor/ui/overlay_parts/watcher.py`）
  - `OcrThreadsFunctionTest`／`OcrThreadsTest` 失败 → 文字识别线程数的取舍或缓存引擎的丢弃时机改坏（`advisor/vision/chip_select.py` 的 `ocr_threads`／`use_lowend_threads`）
  - `SettingsTest` 失败 → `UiSettings.lowend` 的默认或取值判断改坏（`advisor/ui/overlay_parts/app.py`）
  - `OverlayTest` 失败 → `set_lowend` 没存盘／没发信号／重复设也做事（`advisor/ui/overlay.py`）
  - `MainTest.test_wiring_and_high_performance` 失败 → 高性能声明与建界面的先后、线程优先级、或被设成带 `on_start`（`advisor/ui/overlay_parts/app.py` 的 `main`）
  - `OpenExternalTest` 失败 → `open_folder` 的优先级、命令行、只认文件夹或退回处理改坏（`advisor/ui/winapi.py`）
  - `ListenerStartTest` 失败 → 等「显示」请求线程没在开始等之前调 `on_start`（`advisor/ui/single_instance.py`）

## test_lowend_settings.py：设置窗口里的「低配模式」开关

- **测试观点**
  - `ItemTest`：低配模式紧接「识别画面」后面、是「识别」一组的最后一项，是开关、改了调用悬浮窗的 `set_lowend`、默认关；行图标是叶子、两种图标字体里都有这个字、和别的行图标不重；各排法表里都有这一项
  - `SkinLayoutTest`：七套皮肤的设置窗口里都有这一行，截取屏幕时位置在「识别画面」下面；按项名配色的皮肤给它配了颜色，不和挨着的项撞色；换皮肤重建窗口后开关状态照旧
  - `SwitchTest`：点开关调用悬浮窗的 `set_lowend`、存盘、告诉识别线程、显示一致；悬浮窗那边改了窗口即时更新；`set_values` 改开关状态，和不透明开关互不相干
  - `TipTest`：开关的悬停说明分条、分行，写的秒数照 `rules.toml [watch]`、核心数照文字识别的线程数
  - `PagedFitTest`：分页的三套窗口高度写死，「识别」页在各种截取状态下都放得下，屏幕矮时在窗口内滚动
  - `ManualImageTest`：使用说明的截图给每一行标了编号，`docs/使用说明.md` 的图注照同样的先后
- **检证对象**：`advisor/ui/settings/dialog.py` 的 `ITEMS`、`SettingsDialog`、`LOWEND_TITLE`／`LOWEND_DESC`／`LOWEND_TIP`、`SettingsValues`；`advisor/ui/settings/layouts.py` 的各排法表；`advisor/ui/theme/` 的皮肤配色；`advisor/ui/glyphs.py` 的 `ICON_LOWEND`；`advisor/ui/overlay.py` 的 `set_lowend`；`tools/make_manual_images.py` 与 `docs/使用说明.md`
- **什么时候要跑**：改了设置窗口的项表、各皮肤排法或配色、低配模式那一行的文字与说明、设置窗口的高度与滚动，或改了使用说明的截图脚本／图注，都要跑
- **怎么单独跑**：`python -B tools/run_tests.py --only test_lowend_settings`（上次约 5.0 秒）
- **需要什么**：离屏平台，不弹窗；设置文件写在测试沙箱里；系统没有图标字体时 `ItemTest.test_icon` 跳过
- **输出怎么看**：运行期间换一张空的换皮肤通知名单（别的测试模块建的窗口不陪着重画），跑完换回默认皮肤；其余同总则
- **出错时一般是哪里不对**
  - `ItemTest.test_item`／`test_layout_tables` 失败 → 项的位置、类型、动作，或某张排法表漏了／多了低配模式（`advisor/ui/settings/dialog.py`、`layouts.py`）
  - `ItemTest.test_icon` 失败 → 行图标改了或和别的图标重了（`advisor/ui/glyphs.py`）
  - `SkinLayoutTest` 失败 → 某套皮肤没有这一行，或按项名的配色和邻居撞色（`advisor/ui/theme/`、`advisor/ui/settings/`）
  - `SwitchTest` 失败 → 开关没调对 `set_lowend`、或和不透明开关互相影响（`advisor/ui/settings/dialog.py`、`advisor/ui/overlay.py`）
  - `TipTest.test_tip_numbers` 失败 → 说明里写的秒数／核心数和设置不一致（`advisor/ui/settings/dialog.py` 的 `LOWEND_TIP`、`advisor/vision/chip_select.py`）
  - `PagedFitTest` 失败 → 分页皮肤的「识别」页放不下，或屏幕矮时不滚动（`advisor/ui/settings/dialog.py`、`layouts.py`）
  - `ManualImageTest` 失败 → 使用说明的截图编号或图注没跟着项表改（`tools/make_manual_images.py`、`docs/使用说明.md`）

## test_storage.py：测设置和筹码方案、下载的识别图片存在哪里——开发版在程序目录、发行版在个人目录，写不进时退回程序目录，以及诊断包里的「设置目录」和识别图片的位置

- **测试观点**：
  - 开发版：设置和方案在程序目录、不看 `APPDATA`；设置文件写不进时不抛出、记警告、返回没写成；文件坏了另存成 `state.json.broken-时间` 后按默认；个人目录名只写在 `paths.py` 一处（`DevLayoutTest`）。
  - 发行版：各文件的位置；第一次启动；换到新版本文件夹仍沿用设置和方案；个人目录写不进、建不了、没有 `APPDATA` 时退回程序目录另起名的文件并给出原因；下次启动把退回期间存的较新那份写回个人目录、原内容留 `.bak`、退回的那份标记为用过；只标记不覆盖的几种情形；从旧版本的设置不读不删（`ReleaseLayoutTest`）。
  - 把开发目录里现有的设置文件（项目根目录的 `state.json`，没有时跳过）读写一遍，原有各项不变、新加的项按默认补齐（`ReleaseLayoutTest`、`DevSettingsRoundTripTest`）。
  - 运行中方案文件、设置文件在个人目录里存不进时改存到程序目录的另起名文件，原因保持第一次的（`SchemesFallbackTest`、`SettingsFallbackTest`）。
  - 诊断包：`version.txt` 的「设置目录」一行（在「运行方式」下面）、带上设置和方案文件、退回时的写法、自检结果里的设置目录和原因，以及自检结果里程序目录的打包信息（`build`：没有 build.json 时为空，有时照它的写法）（`DiagnosticsInReleaseTest`）。
  - 发行版下载的识别图片：图片、两份下载记录、立绘模板在 `%LOCALAPPDATA%\AstralChipCompass\assets`（第一次用到时建好、只决定一次），星数横条模板、皮肤缩略图在程序目录；没有 `LOCALAPPDATA`、不是绝对路径、建不了、写不进时退回程序目录的 `assets`、记下原因（不含路径）、只记一条警告；开发版照旧在项目的 `assets`（`DownloadDirTest`）。
  - 下载、还缺几张、齐不齐、覆盖清单、冷却记录在个人目录里照常；换新版本（新的程序文件夹、旧的删掉）照样沿用，只下新出的和改过的，立绘模板照样在；程序目录 `assets` 里以前下的图片和记录不读、不搬、不删；随发行版带的只在程序目录里找（`DownloadInPersonalDirTest`）。
  - 导入时就算好的路径不定在决定之前的位置：识别（星数的头像、角色照、筹码面板）、画筹码图标、选项块的图标、皮肤缩略图的局面都按发行版的位置找（`ImportTimeTest`）。
  - 画缩略图的子进程：识别图片只读，不建、不试写个人目录；只读主程序经 `paths.DOWNLOADS_ENV` 交来的位置（`paths.child_environ`），个人目录在、却写不进、主程序退回了 `app\assets` 时也读 `app\assets`，缩略图记的「图标齐不齐」和主程序一致，`PreviewStore.needs` 不再每次判要重画；交来的位置为空、不是绝对路径、没交来时照 `_peek_downloads` 判断（个人目录还没有时照缺图处理）；开发版的子进程不看它（`PreviewChildTest`）。
  - 诊断包 `state.json` 的 `images`（写成 `%LOCALAPPDATA%\…`、退回的原因）、自检的「识别图片」按新位置数缺几张、自检结果里的识别图片目录和原因（`DownloadDiagnosticsTest`）。
  - 测试沙箱把 `LOCALAPPDATA` 也指进沙箱，真实的 `%LOCALAPPDATA%\AstralChipCompass` 记下指纹、进程结束时核对；模拟发行版又没换 `LOCALAPPDATA` 时也只落在沙箱里（`SandboxLocalAppDataTest`）。

- **检证对象**：`advisor/paths.py` 的 `is_frozen`、`user_dir`、`local_dir`、`personal_shown`、`settings_dir`、`state_path`、`schemes_path`、`storage_fallback`、`state_fallback`、`note_state_fallback`、`schemes_fallback`、`note_fallback`、`assets_dir`、`download_dir`、`download_fallback`、`program_dirs`、`use_program_dir`、`child_environ`、`DOWNLOADS_ENV`、`_decide`、`_decide_downloads`、`_peek_downloads`、`_passed_downloads`、`_child_downloads`、`_probe`、`_write_error`、`_adopt`、`_take_back`、`_mark_used`、`FOLDER_NAME`；`advisor/assets.py` 的 `AssetItem.path`、`_local_path`、`_cooldown_path`、`missing`、`missing_bundled`、`download`；`advisor/vision/stars.py` 的 `photo_dirs`；`advisor/vision/hero.py` 的 `heroes_dir`、`user_dir`；`advisor/vision/chip_panel.py` 的 `missing_files`；`advisor/ui/widgets.py` 的 `chip_icon`；`advisor/ui/skin_preview.py` 的 `default_folder`、`scene_icons`、`icons_present`、`PreviewStore` 的 `needs`；`advisor/prefs.py` 的 `Settings`；`advisor/schemes.py` 的 `SchemeStore`、`_atomic_write`、`backup_path`、`get_catalog`；`advisor/diagnostics.py` 的 `SETTINGS_DIR_LINE`、`SETTINGS_DIR_FALLBACK`、`FALLBACK_REASONS`、`DOWNLOAD_FALLBACK_REASONS`、`download_fallback_reason`、`download_state`、`export`、`self_check`、`BUILD_STATE`；`advisor/version.py` 的 `EN_NAME`、`SHORT_NAME`、`BUILD_INFO_NAME`；`advisor/ui/overlay_parts/app.py` 的 `UiSettings`、`load_settings`；`advisor/session` 的 `GAME_FIELDS`、`Session`；`tests/support/sandbox.py` 的 `REAL_LOCALAPPDATA_ENV`、`WATCH`。

- **什么时候要跑**：改了 `advisor/paths.py`（位置、退回、写回、标记、下载的识别图片的位置）、`advisor/prefs.py` 的保存/改存、`advisor/schemes.py` 的存储/改存、`advisor/assets.py` 的文件位置、读识别图片的模块（`stars`、`hero`、`chip_panel`、`widgets`、`skin_preview`）取文件夹的写法、`advisor/diagnostics.py` 的设置目录行和识别图片一项、`advisor/version.py` 的英文名/简写、`advisor/ui/overlay_parts/app.py` 的 `UiSettings`/`load_settings`、`GAME_FIELDS`，或 `tests/support/sandbox.py` 的环境变量。

- **怎么单独跑**：`python -B tools/run_tests.py --only test_storage`（上次约 1.5 秒）。

- **需要什么**：无特别要求。模拟发行版用替换（把「是否发行版」当作真、程序目录、`APPDATA` 和 `LOCALAPPDATA` 都指到 `.tmp` 下的临时文件夹），不碰真实的个人目录；下载换成假的回话，不联网；项目里的 `state.json` 和 `dist/` 下旧版本的设置只读、复制副本用（文件不在时读现在设置的几条跳过）。

- **输出怎么看**：发行版的位置、退回、改存都用替换模拟；会写 `.tmp/tests-storage/` 下的临时文件夹。退回时会往 `advisor.paths` 的日志记警告，测试里把这条日志屏蔽掉，不打进测试输出。

- **出错时一般是哪里不对**：
  - `DevLayoutTest.test_dev_keeps_settings_in_program_dir`、`ReleaseLayoutTest.test_files_in_their_places` 失败 → `paths` 的各位置算法改了。
  - `ReleaseLayoutTest.test_unwritable_falls_back_to_program_dir`、`test_cannot_create_personal_dir`、`test_no_appdata` 失败 → `_decide`/`_probe` 或退回原因（几种 `FALLBACK_*`）改了。
  - `test_next_start_takes_back_fallback`、`test_newer_fallback_written_back`、`test_personal_newer_only_marks`、`test_same_content_only_marks`、`SchemesFallbackTest` 失败 → `_take_back`/`_mark_used` 的新旧比较和「标记为用过」的逻辑改坏了。
  - `test_upgrade_from_0_12_0` 失败 → 又去读程序目录里旧版本留下的 `state.json` 了。
  - `DevLayoutTest.test_folder_name_written_once` 失败 → 个人目录名被写字面量到别的文件里了（只应写在 `advisor/paths.py` 一处）；改了名字要连 `version.EN_NAME` 一起改。
  - `test_current_settings_round_trip`（两处）失败 → 设置项读写不一致（少了、改了、多出不是默认值的）；有意加/改设置项时跟着改这个比较的期望。
  - `SettingsFallbackTest`、`SchemesFallbackTest` 失败 → 运行中改存、原因保持第一次、「都写不进时」的处理改了。
  - `DiagnosticsInReleaseTest` 失败 → 诊断包「设置目录」一行的文字或位置、退回原因的写法改了；有意改就一起改断言。
  - `DownloadDirTest`、`DownloadInPersonalDirTest` 失败 → `paths.download_dir` 的位置、退回、只决定一次，或 `assets` 里取文件位置的地方改了（有的又用回了 `assets_dir`）。
  - `ImportTimeTest` 失败 → 有模块在导入时就把下载的识别图片的位置算好了（应在用到时才取 `download_dir`）。
  - `PreviewChildTest` 失败 → 画缩略图的子进程试写或建了个人目录，或者没读主程序交来的位置（`paths.use_program_dir`、`child_environ`、`_passed_downloads`、`_peek_downloads`）。
  - `DownloadDiagnosticsTest` 失败 → 诊断包的 `images` 一项、自检的识别图片目录或退回原因的写法改了。
  - `SandboxLocalAppDataTest` 失败 → 测试沙箱没把 `LOCALAPPDATA` 指进沙箱，或没盯真实的 `%LOCALAPPDATA%\AstralChipCompass`（`tests/support/sandbox.py`）。

## test_paths_durability.py：测退回期间存的文件写回个人目录、以及退回时复制个人目录文件时的写盘保证

- **测试观点**：
  - 退回的那份较新：写回个人目录时先刷盘、替换、把原来的内容留作 `.bak`，退回的那份改名为 `.used-`（不删）（`TakeBackTest`）。
  - 个人目录里的更新、或内容相同：不覆盖、不留 `.bak`，只把退回的那份标记为用过（`TakeBackTest`）。
  - 退回文件不在时什么都不做；个人目录里的那份写坏了（不是完整 JSON）时不拿它盖掉还能用的 `.bak`（`TakeBackTest`）。
  - 退回程序目录时把个人目录里的那份复制过来：先刷盘再换上、不留临时文件、已经有的不覆盖（`AdoptTest`）。

- **检证对象**：`advisor/paths.py` 的 `_take_back`、`_adopt`、`USED_MARK`、`STATE_NAME`、`STATE_FALLBACK_NAME`；`advisor/fileio.py` 的 `_sync` 和整份写入（先刷盘再替换、`.bak`）。

- **什么时候要跑**：改了 `advisor/paths.py` 的 `_take_back`/`_adopt`，或改了 `advisor/fileio.py` 的写盘保证（刷盘、留 `.bak`、另存）时。

- **怎么单独跑**：`python -B tools/run_tests.py --only test_paths_durability`（上次约 0.04 秒）。

- **需要什么**：无特别要求（临时文件在 `.tmp` 下）。

- **输出怎么看**：同总则。会写 `.tmp/tests-paths-durability/` 下的临时文件夹。

- **出错时一般是哪里不对**：
  - `TakeBackTest.test_newer_fallback_is_written_back` 失败 → `_take_back` 没先刷盘、没留 `.bak`，或没把退回的那份标记为用过。
  - `test_older_fallback_is_only_marked` 失败 → 新旧比较或「只标记」的分支改了。
  - `test_broken_personal_file_does_not_replace_good_bak` 失败 → 传给整份写入的「原来的内容能不能留作备份」这一步丢了（坏内容盖掉了 `.bak`），去看 `_take_back` 传给 `fileio.write_bytes` 的参数。
  - `AdoptTest.test_copy_into_program_dir_is_durable` 失败 → `_adopt` 没先刷盘，或覆盖了已有的文件。

## test_startup_guard.py：测启动时的检查——崩溃记录、发行版入口、`run()` 的接线、预加载识别要用的库、启动失败弹窗按原因写的中文、悬浮窗出来的信号

- **测试观点**：
  - 各约定：自己弹过「启动失败」窗口后的退出码、入口和 `advisor` 里各常量的两份写法（弹窗的字、文件名、环境变量名）相等，以及和 `packaging/launcher.c` 源码里的约定（`ContractTest`）。
  - 崩溃记录（`crash.txt`）的位置（开发目录、发行版）、打开时的做法（上一次不是空的先另存 `crash.old.txt`、被占用时接在后面写、文件夹写不进时不打开、`faulthandler` 已开着时不动；另存的 `crash.old.txt` 里用户目录、账户名换成占位，`\uXXXX` 写法的也换，发行版在交给 `adopt_crash_log` 时换）和大小上限（`CrashPathTest`、`CrashLogOpenTest`、`CrashLimitTest`）。
  - 在子进程里真的崩溃、真的「假崩溃」（别的库自己接住的异常）、超过上限停用、自检走完清空（`CrashLogProcessTest`）。
  - 发行版入口 `main()`：从源码运行时不碰崩溃记录和环境变量；发行版先开崩溃记录、去掉别的软件设的 Qt 环境变量、缩略图子进程不开记录、自检清空记录、导入失败时写调用栈并弹窗（`EntryTest`）。
  - 启动失败弹窗按原因写中文（DLL 加载不了、处理器太老、其他），报错原文只进错误记录（`SummaryTest`）。
  - `run()` 的步骤顺序（防多开 → 开崩溃记录 → 记 Qt 环境变量 → 预加载 → 起悬浮窗 → 清空崩溃记录）、退出码、取走 `ACC_READY_EVENT`（`RunTest`）。
  - 预加载按名单顺序导入、出错抛出、覆盖识别要用的全部第三方库（`PreloadTest`）。
  - 「悬浮窗出来了」的信号：只触发一次、没有事件不报、打不开时记一笔；`main()` 里排定时器的位置（`ReadySignalTest`）。

- **检证对象**：`advisor/ui/startup.py` 的 `open_crash_log`、`adopt_crash_log`、`clear_crash_log`、`check_crash_log`、`watch_crash_log`、`_first_exception_line`、`PRELOAD`、`preload`、`take_ready_event`、`signal_ready`、`signal_ready_later`、`READY_ENV`；`advisor/ui/__init__.py` 的 `run`、`startup_summary`、`load_failed`、`report_startup_error`、`EXIT_REPORTED`、`LOAD_BLOCKED`、`OLD_CPU`、`BROKEN_FILES`；`packaging/chip_advisor.py` 的 `main`、`_open_crash_log`、`_clear_crash`、`_drop_qt_env`、`_load_failed`、`QT_ENV_DROP`、`FROZEN`；`packaging/launcher.c`；`tools/build_release.py` 的 `LAUNCHER_REPORTED`；`advisor/paths.py` 的 `app_dir`、`temp_dir`。

- **什么时候要跑**：改了 `advisor/ui/startup.py`、`advisor/ui/__init__.py` 的 `run`/`startup_summary`/`load_failed`、`packaging/chip_advisor.py`、`packaging/launcher.c`、`tools/build_release.py` 里和启动程序相关的常量、`advisor/paths.py` 的程序目录；改了依赖库名单（`PRELOAD`）或启动失败弹窗的文字。

- **怎么单独跑**：`python -B tools/run_tests.py --only test_startup_guard`（上次约 17 秒）。

- **需要什么**：Windows（`faulthandler` 的崩溃、命名事件）；要起子进程；不弹真窗口（弹窗换成假的）；`ReadySignalTest`、`CrashLimitTest` 用离屏 Qt 跑定时器。

- **输出怎么看**：真的崩溃、假崩溃、超过上限停用都在子进程里做（`faulthandler` 是全进程的，不能在这个进程里改）；子进程没有测试沙箱，错误记录改写到同一个临时文件夹。会写 `.tmp/` 下的临时文件夹。不在 Windows 上时崩溃、命名事件的用例跳过。

- **出错时一般是哪里不对**：
  - `ContractTest` 失败 → 入口和 `advisor` 里两份写死的常量不同步了（退出码、崩溃记录文件名、弹窗文字、环境变量名），或者 `launcher.c`/`tools/build_release.py` 里那份没跟着改。
  - `CrashPathTest`、`EntryTest` 的位置断言失败 → `paths.app_dir`/`program_folder` 或发行版文件夹布局改了。
  - `CrashLogOpenTest`、`CrashLimitTest`、`CrashLogProcessTest` 失败 → 崩溃记录的另存、上限、清空做法改了（入口那份和 `startup.py` 那份要一起改）。
  - `SummaryTest`、`RunTest.test_real_dialog_text`、`EntryTest` 的导入失败用例失败 → 启动失败弹窗的中文（`ui.LOAD_BLOCKED`、`OLD_CPU`、`BROKEN_FILES`）改了；有意改就一起改断言。
  - `RunTest.test_normal_order` 失败 → `run()` 的步骤顺序改了。
  - `PreloadTest` 失败 → `PRELOAD` 名单改了，或新加的依赖没覆盖到（识别线程和文字识别要用的库都应在预加载里）。
  - `ReadySignalTest` 失败 → `signal_ready_later`/`signal_ready` 的触发时机，或 `main()` 里排定时器的位置改了。

## test_exit_flow.py：悬浮窗退出时把这些东西停干净

- **测试观点**
  - `MainShutdownTest.test_main_calls_shutdown_and_closes_recorder`：`main()` 退出时先调悬浮窗的 `shutdown()`（停识别线程、下载），再关识别记录
  - `MainShutdownTest.test_stuck_watcher_ends_process_after_recorder_closed`：识别线程没停下时，关好识别记录以后直接结束进程（退出码照旧）
  - `MainShutdownTest.test_nonzero_code_is_not_startup_failure`／`test_startup_exit_code_still_reported`：事件循环带着非 0 退出码结束时不按启动失败弹窗，启动时就退出的照旧弹窗
  - `MainShutdownTest.test_exit_step_error_logged`：收尾的某一步出错时记进错误记录，后面几步照做、照常退出
  - `ExitSubprocessTest.test_main_exits_cleanly_in_subprocess`：子进程里真的走一遍 `main()`，显示后马上退出，退出码 0、没出错、三样收尾都做了
  - `ExitSubprocessTest.test_stuck_watcher_exits_normally`：识别线程卡在很慢的调用里时，等满上限照常结束，错误记录写明没停下、当时停在哪里
  - `ExitSubprocessTest.test_terminate_process_passes_code`：`terminate_process` 的退出码照传，不跑收尾，调用之后的代码不再执行
- **检证对象**：`advisor/ui/overlay_parts/app.py` 的 `main`、`_save_on_exit`、`terminate_process`；`advisor/ui/overlay.py` 的 `Overlay.shutdown`；`advisor/ui/single_instance.py` 的 `listen`／`Listener.stop`；`advisor/gamelog.py` 的 `GameRecorder.close`
- **什么时候要跑**：改了悬浮窗退出的顺序、识别线程／下载／识别记录的收尾、退出码的处理，或 `terminate_process`，都要跑
- **怎么单独跑**：`python -B tools/run_tests.py --only test_exit_flow`（上次约 6.4 秒）
- **需要什么**：进程内的几条把界面、识别、热键、等待线程全换成假的；子进程的几条要能起 Python 子进程，离屏真的建出悬浮窗，把截屏、找窗口、改窗口层次、全局热键、托盘、下载、试写程序文件夹、设置和识别记录的文件都换成假的或指到临时文件夹
- **输出怎么看**：子进程断言失败时会把它的标准输出／标准错误原样打出来；子进程每做一样收尾就写一行到临时文件夹的 `calls.txt`，找不到这个文件说明收尾没做到；其余同总则
- **出错时一般是哪里不对**
  - `test_main_calls_shutdown_and_closes_recorder` 失败 → 退出顺序改了，或少了 `shutdown()`／关识别记录（`advisor/ui/overlay_parts/app.py` 的 `main`、`advisor/ui/overlay.py` 的 `shutdown`）
  - `test_stuck_watcher_ends_process_after_recorder_closed` 失败 → 识别线程没停下时没先关识别记录就结束进程（同上）
  - `test_nonzero_code_is_not_startup_failure`／`test_startup_exit_code_still_reported` 失败 → 把普通退出当成了启动失败、或反过来（`advisor/ui/__init__.py` 的 `run`、`app.py` 的 `main`）
  - `test_exit_step_error_logged` 失败 → 收尾某一步出错时没记错误记录、或后续步骤没做（`app.py` 的 `main`）
  - `test_main_exits_cleanly_in_subprocess` 失败 → 子进程里退出码不是 0、有没接住的错误、或三样收尾没做全（`app.py` 的 `main`、`overlay.py` 的 `shutdown`）
  - `test_stuck_watcher_exits_normally` 失败 → 识别线程卡住时没到点放弃，或没写「没停下」这条错误记录（`app.py` 的 `shutdown` 收尾）
  - `test_terminate_process_passes_code` 失败 → `terminate_process` 没照传退出码，或跑了收尾、之后还继续执行（`app.py` 的 `terminate_process`）

## test_single_instance.py：测「同时只运行一个拾星助手」，以及已在运行的那个没有响应时怎么办（问用户还是安静退出、代弹的说明窗）

- **测试观点**：
  - 一组内核对象的名字（锁互斥体、「显示」事件、「已显示」事件、记着拿锁是谁的共享内存）的组成和几个时间常量（再次启动最多等 2 秒、卡住满 15 秒才算、弹窗显示期间每 0.2 秒试一次拿锁）固定不随便改（`NamesTest`）。
  - 拿锁、放锁、只查询不建（`LockTest`）；子进程结束以后锁和那条记录都随之消失，不留残留（`ProcessLockTest`）。
  - 已在运行的那个不回应时：刚开不满 15 秒安静退出、满了才问用户、点「结束它并重新打开」只结束对得上的那个、点「取消」就不动它、进程号被别的进程重用时不误伤（`StuckProcessTest`、`StuckDecisionTest`）。
  - 再次启动的进程把「请显示」发给已在运行的那个，收到回应后马上退出；请求来得早、等的时候锁释放、旧回应不算、两个同时启动都不抢锁、等待线程按时停、回调出错照常等（`ShowRequestTest`）。
  - 查不了、不拦时要照常启动；以管理员身份运行的那个（打不开）算在运行、弹说明窗（`FallbackTest`、`ElevatedOwnerTest`）。
  - 弹窗的文字、按钮、默认按钮、图标和「总在最前」（`StuckPromptTest`）；`docs/使用说明.md`「常见问题」引用的弹窗文字和按钮名是弹窗上实际显示的（`test_docs_quote_dialog`）。
  - `run()`、悬浮窗 `main()`、发行版入口里的接线：已有一个在运行时不起悬浮窗、不弹窗、不写错误记录（`RunWiringTest`、`OverlayMainWiringTest`、`SelfCheckEntryTest`），以及真的再起一个进程走一遍（`RelaunchProcessTest`、`StuckRelaunchProcessTest`、`RelaunchOverlayTest`）。

- **检证对象**：`advisor/ui/single_instance.py` 的 `Names`、`NAMES`、`acquire`、`Instance`、`Listener`、`start`、`_unanswered`、`_find_owner`、`_end`、`_Released`、`claim`、`listen`、`acknowledge`、`running`；`advisor/ui/stuck_prompt.py` 的 `question`、`ask_end`、`notice`、`tell`、`duration`、`_application`；弹窗文字还和 `docs/使用说明.md`「常见问题」对；接线看 `advisor/ui/__init__.py` 的 `run`、`advisor/ui/overlay_parts/app.py` 的 `main`、`advisor/ui/overlay.py` 的 `Overlay.on_relaunch`/`relaunch_requested`/`relaunch_ack`、`advisor/ui/overlay_parts/follow.py` 的 `on_relaunch`、`packaging/chip_advisor.py` 的 `main`（`--selfcheck` 分支）。

- **什么时候要跑**：改了 `advisor/ui/single_instance.py`、`advisor/ui/stuck_prompt.py`；改了启动/退出接线（`advisor/ui/__init__.py` 的 `run`、`advisor/ui/overlay_parts/app.py` 的 `main`、`advisor/ui/overlay.py`、`advisor/ui/overlay_parts/follow.py`）；改了 `packaging/chip_advisor.py` 里 `--selfcheck` 的分支；改了程序名、个人目录名（`paths.FOLDER_NAME`）。凡动到「启动、退出、防多开、弹窗」的改动都要跑。

- **怎么单独跑**：`python -B tools/run_tests.py --only test_single_instance`（上次约 35 秒）。

- **需要什么**：Windows（命名互斥体、事件、共享内存、目录联接）；要起子进程；弹窗相关的用例用离屏 Qt 平台（不在桌面上出窗口）；测试自己把放开前台权限、弹窗、时钟都换成假的，不抢焦点、不碰正式的名字。

- **输出怎么看**：不在 Windows 上时大量用例跳过（原因「命名互斥体和事件只在 Windows 上有」）；本进程以管理员身份运行时 `ElevatedOwnerTest` 跳过；真弹窗的用例只在离屏平台上跑。测试会写 `.tmp/tests-single-instance/` 下的临时文件夹，起 `HOLDER`、`RELAUNCH`、`STUCK_RUN` 几个小脚本的子进程，用完都把测试自己起的子进程结束掉。

- **出错时一般是哪里不对**：
  - `NamesTest.test_official_names` 失败 → 名字的组成、时间常量或共享内存格式（标记 `ACC2`）被改了；这些是各版本之间约好的，改了要么改回，要么连断言一起改。
  - `LockTest`、`ProcessLockTest` 失败 → `acquire`/`running` 的拿放锁，或 `_record_owner` 写共享内存（进程号、创建时间、拿到锁的时刻）改坏了。
  - `StuckDecisionTest`、`StuckProcessTest` 失败 → `_unanswered`/`_find_owner`/`_end`/`_Released` 的分支（什么时候问、什么时候安静退出、只结束对得上的那个）改坏了。
  - `ShowRequestTest`、`RelaunchOverlayTest` 失败 → `_ask`/`_answered`/`Listener`/`acknowledge` 或跨线程信号接线改坏了。
  - `StuckPromptTest.test_question`/`test_notices`/`test_docs_quote_dialog` 失败 → 弹窗文字、按钮改了；有意改的话，连 `docs/使用说明.md`「常见问题」里引用的写法一起改。
  - `RunWiringTest`、`OverlayMainWiringTest`、`SelfCheckEntryTest` 失败 → `run()`/`main()` 的先后顺序或提前返回改坏了。
  - `FallbackTest.test_access_denied_counts_as_running` 失败 → 「拒绝访问也算在运行」的处理丢了。

## test_errors.py：错误记录（`overlay_err.txt`）的写法

- **测试观点**
  - `MovedTest`／`SandboxTest`：`advisor.ui` 也能导入 `log_error`、`ERR_FILE`（再导出 `advisor/errors.py` 的那一份）；没被换掉时错误记录在这个测试进程的沙箱里
  - `AppendTest`：一律追加（启动失败也不清掉之前的记录）；几个线程同时写不串行、一条不少；`log_error` 返回「写在哪儿」；写不进去时返回的中文不含英文原文；标题和详细信息里的用户目录、账户名写进去之前换成占位（`test_user_dirs_written_as_placeholders`）
  - `RepeatTest`：同一个错只整条记第一次，之后定期记一行「又出现 N 次」，退出时补上没记的次数；文件被删掉以后照常整条记
  - `RotateTest`：到了上限把旧的改名另存成 `overlay_err.old.txt`（只留一份），新文件从整条记起；换文件时还没记的次数补在旧文件末尾；改不了名时照旧追加
  - `StartupSummaryTest`：启动失败的弹窗按原因写中文，英文类名和报错原文只进错误记录
- **检证对象**：`advisor/errors.py` 的 `log_error`、`flush`、`old_path`、`ERR_FILE`、`REPEAT_S`、`KEEP`、`MAX_BYTES`、`clock`；`advisor/ui/__init__.py` 里再导出的 `log_error`／`ERR_FILE` 与 `startup_summary`、`run`、`report_startup_error`
- **什么时候要跑**：改了错误记录的写入、去重计数、轮换、或启动失败弹窗的文案，或挪动 `log_error` 所在的模块，都要跑
- **怎么单独跑**：`python -B tools/run_tests.py --only test_errors`（上次约 1.1 秒）
- **需要什么**：没有特别要求；每个测试的错误记录写进自己的临时文件；「又出现 N 次」的间隔用假时钟拨，不等真实时间
- **输出怎么看**：核对用 `capture_errors` 把错误记录指到临时文件，结束时看 `entries()`；其余同总则
- **出错时一般是哪里不对**
  - `MovedTest.test_reexported_from_ui` 失败 → `advisor.ui` 没再导出 `log_error`／`ERR_FILE`，或两处的 `ERR_FILE` 不是同一个（`advisor/ui/__init__.py`）
  - `AppendTest.test_startup_error_appends`／`test_append_flag_ignored` 失败 → 变成覆盖写了，把之前的记录清掉（`advisor/errors.py` 的 `log_error`）
  - `AppendTest.test_threads_do_not_mix` 失败 → 并发写记录时串行或丢条（`advisor/errors.py`）
  - `AppendTest.test_unwritable_reports_reason` 失败 → 写不进时返回的中文里带了英文原文或路径（`advisor/errors.py`）
  - `AppendTest.test_user_dirs_written_as_placeholders` 失败 → 写进错误记录的标题、详细信息没经 `paths.scrub_paths`，带出了用户目录或账户名（`advisor/errors.py` 的 `log_error`）
  - `RepeatTest` 失败 → 同一个错的整条／计数／「又出现 N 次」的时机，或不同详细信息算同一个错（`advisor/errors.py`）
  - `RotateTest` 失败 → 到上限轮换、换文件时补记、或改不了名时的退路改坏（`advisor/errors.py` 的 `old_path`、轮换那一段）
  - `StartupSummaryTest` 失败 → 启动失败的文案或「原文只进错误记录」改坏（`advisor/ui/__init__.py` 的 `startup_summary`／`run`）
  - 有意改了「又出现 N 次」的间隔或上限常量时，断言用的值要跟着改

## test_runtime_notices.py：一直留着的三条提示（识别已停止、程序文件夹写不进、高 DPI 被改过）

- **测试观点**
  - `WordingTest`：三条提示的文字逐字核对，都放在标题栏下方（全局提示）
  - `WatcherCrashTest`：识别线程出错或没报错就结束时，悬浮窗常驻「识别已停止」、错误记录写明原文；退出时叫停的、会自动重试的不算；错误记录本身写不进时照样提示
  - `AfterStopTest`：停下以后状态胶囊、一行显示的摘要、卡片收起时的摘要都以「识别 已停止」开头，不再写「识别 照常进行」「识别中」；等待提示（「推荐」卡片的空闲文字、摘要里「识别 已停止」后面那一项）不再写「等待筹码三选一界面…」，换成「请重新打开拾星助手」；按热键不请识别也不留「正在识别…」
  - `PersistentRowsTest`：三条都排在最前、先后固定、橙色警告、没有按钮、不是「需要您操作」；一次性提示来了又收起、切换显示方式都不收起
  - `FolderProbeTest`：启动时对几处程序文件夹和下载的识别图片实际要写的那一处试写，程序文件夹里写不进的常驻提示并记错误记录（按用户看到的程序文件夹写相对路径，个人目录里的写成 `%LOCALAPPDATA%\…`）；只有个人目录里那一处写不进时不算程序文件夹写不进；错误记录本身写不进时照样提示；发行版的识别图片退回程序文件夹时不另出提示、只记一条错误记录（写明原因和这次放在哪）
  - `DownloadReasonTest`／`DownloadNoticeTest`：下载识别图片时写盘出错和网络出错分开；失败全是写盘出错时提示换成写不进的那一句（识别图片在个人目录里时是存不进个人目录的那一句，程序文件夹写不进的那一行照常显示），混着几张网络出错时按个别图片下不了写（其余识别照常、之后自动再试，见 `test_download_fail.py`），失败超过 `FAILED_MANY` 张时（按总张数，写不进的也算进去）照旧说检查网络；识别图片文件夹（在程序文件夹里时）写不进时不下载
  - `DpiApiTest`／`DpiCheckTest`：查到「替代高 DPI 缩放行为」不是按每块屏幕的缩放时常驻提示并记错误记录；系统没有查询函数、或 Qt 用的不是 Windows 窗口平台时不查
  - `MainWiringTest`：`main()` 显示以后接上识别停下的两个信号，试写程序文件夹、查 DPI 在显示以后、下载识别图片之前
- **检证对象**：`advisor/ui/overlay_parts/watcher.py`（`recog_stopped`、`crashed`、`recog_error`）；`advisor/ui/overlay.py` 的 `on_recog_stopped`；`advisor/ui/overlay_parts/notices.py` 的 `NoticeModel`、`check_folders`、`note_download_fallback`、`folder_text`、`images_shown`、`check_dpi`、`dpi_program`；`advisor/ui/overlay_parts/status.py` 的 `capsule_bit`、`activity_tip`、`waiting_text`；`advisor/ui/overlay_parts/wording.py` 的 `RECOG_STOPPED`、`RECOG_STOPPED_WAITING`、`FOLDER_UNWRITABLE`、`DOWNLOAD_UNWRITABLE`、`ASSETS_TIP`、`DPI_CHANGED`、`CAPSULE_KEYS`、`NOTICE_AREA`；`advisor/paths.py` 的 `program_dirs`、`unwritable_dirs`、`download_dir`、`download_fallback`、`personal_shown`；`advisor/diagnostics.py` 的 `download_fallback_reason`；`advisor/assets.py` 的 `download`、`is_write_error`、`WRITE_FAILED`；`advisor/ui/overlay_parts/background.py` 的 `failure_reasons`、`check_assets`；`advisor/ui/winapi.py` 的 `dpi_awareness`、`_dpi_api`；`advisor/ui/overlay_parts/app.py` 的 `main`
- **什么时候要跑**：改了识别线程出错／结束的处理，这三条提示的文字、位置、先后，程序文件夹试写，下载失败的原因分类，DPI 查询与提示，或 `main()` 的接线顺序，都要跑
- **怎么单独跑**：`python -B tools/run_tests.py --only test_runtime_notices`（上次约 3.0 秒）
- **需要什么**：离屏、不截屏、不找真实窗口；识别线程用假的取帧对象和识别流程；下载换成假的（不联网）；DPI 系统调用打桩；试写只写测试沙箱里的文件夹
- **输出怎么看**：`DpiApiTest.test_real_call` 在这台电脑没有查询函数时跳过（只读本进程的设置）；错误记录写进每个测试自己的临时文件；其余同总则
- **出错时一般是哪里不对**
  - `WordingTest` 失败 → 三条提示的文字或所在区域改了（`advisor/ui/overlay_parts/wording.py`）
  - `WatcherCrashTest` 失败 → 识别线程出错没发信号、把退出时叫停或会重试的也当成停止、或记了两笔错误（`advisor/ui/overlay_parts/watcher.py`、`advisor/ui/overlay.py` 的 `on_recog_stopped`）
  - `AfterStopTest` 失败 → 停下以后状态胶囊／摘要没改成「识别 已停止」、等待提示没换（`status.py` 的 `waiting_text`、`overlay.py` 的 `_waiting_text`），或按热键还去请识别（`advisor/ui/overlay_parts/status.py`、`advisor/ui/overlay.py`）
  - `PersistentRowsTest` 失败 → 三条的先后、级别、按钮或「是否一直留着」改坏（`advisor/ui/overlay_parts/notices.py` 的 `NoticeModel.rows`）
  - `FolderProbeTest` 失败 → 试写的几处、提示文字或错误记录里的相对路径改坏（`advisor/paths.py` 的 `program_dirs`／`unwritable_dirs`、`notices.py` 的 `check_folders`）
  - `DownloadReasonTest`／`DownloadNoticeTest` 失败 → 写盘出错和网络出错没分开、提示没换成写不进的句子、个别和大部分没下下来没分开、或识别图片文件夹写不进时还下载（`advisor/assets.py`、`notices.py`、`advisor/ui/overlay_parts/background.py` 的 `failure_reasons`、`failure_kind`）
  - `DpiApiTest`／`DpiCheckTest` 失败 → DPI 查询、判「不是按每块屏幕缩放」、或提示里的程序文件名改坏（`advisor/ui/winapi.py`、`notices.py` 的 `check_dpi`／`dpi_program`）
  - `MainWiringTest` 失败 → `main()` 接信号或试写／查 DPI／下载的先后改坏（`advisor/ui/overlay_parts/app.py` 的 `main`）
  - 有意改了这几句界面文字或提示先后时，断言预期要跟着改

## test_services_input.py：测悬浮窗调外部模块时整理返回值的两个函数，缺字段、类型不对时不出错、不报离谱的数

- **测试观点**：
  - 数量的整理（`CountTest`）：数字、清单照常；负数、算不出长度的对象按 0；小数取整；不是有限的数按 0；一段文字按一项（不按字数算）、空文字按 0、非空 bytes 按 1。
  - 「关于」各节的整理（`SectionsTest`）：真实的各节照原样；接受字典、`(标题, 正文)` 的列表、带 `title`/`text` 的对象、生成器；一整段文字算一节（没有标题）；不能逐项取的（数字这类）按没有；缺的标题、正文按空；只有一个值、是一段文字的当正文。

- **检证对象**：`advisor/ui/services.py` 的 `count`、`sections`、`_text`；`advisor/version.py` 的 `about_sections`。

- **什么时候要跑**：改了 `advisor/ui/services.py` 的 `count`/`sections`，或改了 `advisor/version.py` 的 `about_sections` 的返回形状（「关于」窗口的内容）。

- **怎么单独跑**：`python -B tools/run_tests.py --only test_services_input`（上次约 0.001 秒）。

- **需要什么**：无特别要求（不导入 Qt、不开窗口）。

- **输出怎么看**：同总则。

- **出错时一般是哪里不对**：
  - `CountTest` 失败 → `count` 的兜底逻辑改了；接口层（下载结果等）换了返回类型时，改的是 `count`。
  - `SectionsTest.test_real_about_sections_unchanged` 失败 → `version.about_sections` 返回了不是 `(标题, 正文)` 的东西。
  - `test_accepted_shapes`、`test_odd_shapes` 失败 → `sections` 对缺字段、类型不对的处理改了。
  - 注意：`str.title` 是方法而不是标题，`sections` 判断「一项是不是带标题的对象」时用的是「`title` 不是可调用的」，改这里别把「一整段文字」误当成带标题的对象。
