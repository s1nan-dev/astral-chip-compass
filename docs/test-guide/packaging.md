# 测试指南 · 打包和发行版

这是[《测试指南》](../测试指南.md)第八节的一章：每个测试文件一节，每节的结构见那里第八节的开头。


这一章管打包发行版（tools/build_release.py）、自编 OpenCV/numpy、使用说明网页、模块分层，以及「程序用到了发行版排掉的模块」这道检查。

## test_release.py：发布工程的整体检查——版本与「关于」窗口、文件位置、识别图片清单与下载、推荐日志、诊断包、发行版检查、发行版根目录的启动程序

- **测试观点**（按测试类）：
  - 版本与「关于」窗口（`VersionTest`）：版本号格式、资料日期、`about_sections()`/`about_text()` 的分节和文字（免费软件声明、许可证、第三方许可证、隐私说明）、`LICENSE` 是不是未改动的 AGPL-3.0 原文、英文名与个人目录/文件名、`version.py` 不导入第三方库。
  - 文件位置（`PathsTest`）：开发版各文件的位置（下载的识别图片也在项目的 `assets`）、备份文件夹的位置规则、新模块都经 `paths.py` 定位、发行版里「程序目录」的写法（前面多一层 `app\`），「关于」窗口里下载的识别图片和立绘模板只写发行版的个人目录。
  - 识别图片的清单与下载（`ManifestTest`、`AssetsTest`）：`data/assets_manifest.json` 的条目是否完整、是否和 `assets/` 里的图片对得上（都取自 BWIKI，不带 `api`；从卡面现裁的角色照：清单项是同一编号、同一皮肤的卡面、裁法 6 个数，开发目录里那张按覆盖清单里「按哪张卡面、哪份裁法裁的」记录核对；识别用不到的——编号不是 `data/heroes.json` 里的角色的 Q 版头像、`data/sfw_skip.json` 里列的健全版——开发目录里照留、不在清单里）；下载照样支持图片在另一个 wiki 上的清单项（带 `api`，问那个 wiki 的接口）；下载的重试、被限流时按 Retry-After 等（被站点防护拦下时整体停下、冷却以后接着下，见 `test_download_block.py`）、失败放弃、wiki 上更新/移动/删除的图片、非 PNG 拒绝（拿到网页按被拦算）、中途取消、缺失判定（清单里已经没有、以前下过的图片不算缺、不下、不删）、用系统自带的 WinHTTP。
  - 推荐日志与诊断包（`LogbookTest`、`DiagnosticsTest`）：日志的记录格式、怎么判新的一局、重启后怎么接局、月度文件、对局结束；诊断包导出哪些文件、内容与脱敏（用户主目录换成 `%USERPROFILE%`）、文件名检查、设置/方案文件随包、`version.txt` 的截取方式与设置目录两行。
  - 发行版检查（`BuildReleaseCheckTest`、`LocalPathScanTest`、`StateKeyTest`、`VcRuntimeTest`）：必备文件、根目录只放两样、不该发布的文件（源码、开发文档和样本的文件夹、日志、设置和筹码方案文件；`.md` 只许有 `app` 里的 `THIRD_PARTY_NOTICES.md`）、`data/*.toml` 里的开发路径和停用的「重选」、许可证、数据白名单、识别图片清单、编译与链接参数、通用 x86-64 以外的指令、打包电脑的路径、试启动结果核对（设置目录、识别图片的文件夹要在试启动指定的个人目录里，`test_smoke_problems_images_dir` 等；自检读不到打包信息算失败；自检「识别合成图」没通过算失败，`test_smoke_problems_ocr_render`）、压缩包可复现；打包信息 `app\build.json`（组装时写在 `app` 里，发行版检查要它在、程序读得出，`test_build_info_in_dist`；摘要的 `build`，屏幕上组装以后、结论之前各一行，有未提交的改动、项目文件夹是 git 仓库却没记下提交号时摘要的 `warnings` 记一条、屏幕上另起一行提醒，`test_summary_and_output_carry_build_info`）；打包电脑的路径扫描认哪些写法；打包密钥的编译步骤；VC 运行库整套替换与版本核对。`BuildReleaseCheckTest.test_launcher_source_matches_script`：`packaging/launcher.c` 和打包脚本、程序约好的名字、退出码、事件和环境变量对得上，弹窗的标题和各句提示逐字照这里的表，七句提示、出错退出的各个原因和各类错误代码照抄在软件维护说明里。
  - 启动程序（`LauncherTest`）：`packaging/launcher.c` 的参数转交、工作目录、等悬浮窗、按退出码分原因提示、要管理员权限时改用 `ShellExecuteExW`、超长路径（用打包环境里的 zig 真的编出来运行）。
- **检证对象**：`advisor/version.py`、`advisor/paths.py`、`advisor/assets.py`、`advisor/winhttp.py`、`advisor/logbook.py`、`advisor/diagnostics.py`、`advisor/unlock.py`；`tools/build_release.py`（`check_dist`、`local_path_problems`、`smoke_problems`、`make_zip`、`stash_user_data`（`USER_DATA`、`ASSET_RECORDS`：下载记在识别图片文件夹里的覆盖清单和冷却记录也暂存、放回）、`state_key_*`、`vc_runtime_problems`、`launch_once`、`assemble` 写打包信息、`build_info_problems`、`build_info_lines`、`build_info_warnings` 等）、`packaging/launcher.c`、仓库根的 `LICENSE`、`LICENSES/`、`THIRD_PARTY_NOTICES.md`、`data/assets_manifest.json`、`docs/使用说明.md`、`docs/软件维护说明.md`。辅助：`tests/support/sandbox.py`。
- **什么时候要跑**：改了对应那段就至少要跑一遍——
  - `advisor/version.py`、仓库根 `LICENSE`、`LICENSES/`、`THIRD_PARTY_NOTICES.md`（版本号、关于窗口、署名、许可证）→ 先看 `VersionTest`。
  - `advisor/paths.py`（文件位置、个人目录、备份文件夹、发行版里路径的写法）→ `PathsTest`。
  - `advisor/assets.py`、`advisor/winhttp.py`（识别图片下载）→ `AssetsTest`。
  - 重新生成 `data/assets_manifest.json` 或增删/替换 `assets/` 里的图片 → `ManifestTest`。
  - `advisor/logbook.py`（推荐日志）→ `LogbookTest`。
  - `advisor/diagnostics.py`（诊断包）→ `DiagnosticsTest`。
  - `tools/build_release.py`（发行版检查、试启动、密钥、VC 运行库、压缩包）→ `BuildReleaseCheckTest`、`LocalPathScanTest`、`StateKeyTest`、`VcRuntimeTest`。
  - `packaging/launcher.c`、`packaging/launcher.rc`、启动程序的提示文字（软件维护说明里照抄的那些）→ `LauncherTest`、`BuildReleaseCheckTest.test_launcher_source_matches_script`。
  - 打包流程的约定常量（例如 `advisor/ui/startup.py` 的 `READY_DELAY_MS`、`advisor/ui/skin_preview.py` 的 `CHILD_TAG`）→ 相关用例会一起核对。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_release`（上次约 190 秒；不在慢测试、实机测试名单里，但会编 C 程序、跑真实子进程，是耗时较长的模块之一）。
- **需要什么**：不用识别样本截图、不用显卡、不开可见窗口（界面部分离屏运行）。但 `LauncherTest` 要用打包环境里的 zig 真的编出启动程序并运行，这台电脑没有打包环境（`.tmp/build-venv`，用打包脚本的 `--setup` 建）时这一类整体跳过；这台电脑建不了超长路径时个别用例跳过。`ManifestTest.test_matches_dev_assets` 要开发目录里下载过识别图片（第一次运行开发版时下载，或运行 `tools/fetch_assets.py`），一张都没有时跳过；`StateKeyTest.test_local_key_file_readable` 只在本机有打包密钥文件时核对它读得出来，没有时跳过（这时打包加 `--public-key`）。编译、打包的几条一律用临时文件夹里的假密钥，不读本机的密钥文件。
- **输出怎么看**：临时文件都在程序临时目录下的 `tests-release/`：`LauncherTest` 在那里编出启动程序和假的 `app\ACC.exe` 并运行。跳过时会写明原因（例如「这台电脑没有打包环境里的 zig……」「这台电脑建不了超过 260 个字符的路径……」）。其余同总则。
- **出错时一般是哪里不对**：
  - `VersionTest.test_version_format` 失败 → `version.py` 的 `VERSION` / `file_version()` 改了而没同步（改版本号时，这里的预期一起改）。
  - `VersionTest.test_about_sections` / `test_license_section` / `test_free_notice` 失败 → 「关于」窗口、许可证条款、隐私说明的文字改了；有意改文字时，断言的预期文字要一起改。
  - `VersionTest.test_license_file` 失败 → 仓库根的 `LICENSE` 不是 agpl-3.0.txt 原文（被改过，或换行被转成 CRLF）。这是有意不能改的文件。
  - `PathsTest.test_layout` / `test_release_paths_shown_under_app` 失败 → `paths.py` 的位置算法或「程序目录」前加 `app\` 的写法改了（去看 `paths.py` 的 `program_folder`、`shown`）。
  - `AssetsTest` 某项失败 → `assets.py` 的下载、校验值比对、重试/限流等待、取消、清单判定改了（去看 `assets.py` 的 `download`、`_get_image`、`_Gate`、`missing`）。
  - `ManifestTest.test_matches_dev_assets` 失败 → `assets/` 里加了或改了图片，但没重新生成 `data/assets_manifest.json`（去跑 `tools/make_assets_manifest.py`）；报「不是按清单里的卡面、裁法裁的」时，开发目录里那张现裁的角色照要重裁（`tools/fetch_assets.py --crop`）；报「识别用不到，不该在清单里」、`test_left_out_not_listed` 失败时，改了 `data/sfw_skip.json` 或 `data/heroes.json` 以后没重新生成清单。
  - `LogbookTest` 某项失败 → `logbook.py` 的记局边界、重启接局、月度文件、对局结束规则改了。
  - `DiagnosticsTest` 某项失败 → `diagnostics.py` 的导出文件清单、脱敏、文件名检查、设置/方案文件随包规则改了。
  - `BuildReleaseCheckTest.test_launcher_source_matches_script` 失败 → `packaging/launcher.c` 的名字、退出码、提示文字或错误代码表改了而打包脚本、`test_release.py` 里的表（`LAUNCHER_*`）或软件维护说明没跟上（反过来也一样）；三处要一起改。
  - `BuildReleaseCheckTest` / `VcRuntimeTest` 某项失败 → `build_release.py` 的各项清单（必备文件、许可证表、数据白名单、VC 运行库）改了，或发行版里真的缺/多了文件；`test_data_whitelist_covers_data_folder` 失败 → `data/` 下新加了 json、toml，要决定发不发布：发布的加进 `DATA_FILES`，只给开发用的工具读的（如 `card_crops.json`）加进 `DEV_ONLY_DATA`。
  - `BuildReleaseCheckTest.test_build_info_in_dist`、`test_summary_and_output_carry_build_info` 失败 → 打包信息写在哪、发行版检查怎么查它（`check_dist`、`build_info_problems`），或摘要的 `build`、`warnings`、屏幕上那几行的写法（`build_info_lines`、`build_info_warnings`，`main` 里组装以后、结论之前两处）改了；打包信息本身的写法、读法见 `test_build_info.py`。
  - `LocalPathScanTest` 某项失败 → `local_path_problems` 认得/不认的打包电脑的路径写法改了（报错里不能出现用户文件夹名原文）。
  - `StateKeyTest` 某项失败 → 打包密钥的编译步骤（生成临时模块、编完删掉、`--public-key`）改了。
  - `LauncherTest` 某项失败 → `packaging/launcher.c` 的行为、退出码或某句提示改了（有意改提示时，`launcher.c` 和这里的预期文字、软件维护说明要一起改）。

## test_release_rehearse.py：测打包时试启动之后的换版本演练（拿刚打好的压缩包真换一次版本）

- **测试观点**（按测试类）：
  - 整个演练走一遍（`RehearseTest`）：换版本程序是假的进程，照换版本程序对外的约定改文件夹、写 `update\result.json` 和 `update\apply.log`。核对起它之前清掉了上一次留下的、解好了「旧版本」、备好了 `update\new` 和 `ready.json`；命令行、工作目录、环境（同试启动，不下载识别图片，带「已经接手」的事件名）；换好了时逐项核对都通过，换上的新版本收了尾、删了 `update` 也认得；退出码不是 0、退回了、没换、一直不结束、用户数据没搬过去、没触发事件、记录里少了那一句、`app` 还是原来那一份、根目录少了文件、新版本没在运行、弹了失败的窗口，各报一条。游戏开着、拾星助手正在运行、演练文件夹不在 `.tmp` 下或者本身是链接时不演练、不删；演练起的程序结束不了、查不了进程表时算没通过、演练文件夹留着。每一条都核对别处的拾星助手（发行版文件夹里的、试启动的、文件夹名字只差几个字的）一个都没被结束，演练文件夹以外的文件一个都没动，句柄都关了。
  - 结束演练起的程序（`EndProcessesTest`）：只认程序文件在演练文件夹里的（不分大小写），先结束换版本程序、再结束根目录的启动程序、最后是 `app` 里的，刚被带起来的接着结束；动手前再核对一遍程序文件，进程号换了主人时不碰；结束不了的到时间报出来。
  - 和 Windows、换版本程序的约定（`WindowsTest`、`ContractTest`）：交给换版本程序的旧进程号在它看来已经退出；换版本程序触发得了演练建的事件；记录里要有正好是那一句的一行；退出码的说法；摘要里的一项和打包流程里的那一步。
- **检证对象**：`tools/build_release.py` 的 `rehearse`、`rehearse_problems`、`apply_rehearsal`、`rehearsal_step`、`end_rehearsal_processes`、`remove_rehearsal` 和它们用到的小函数；换版本程序对外的约定（`advisor/update_apply.py` 的命令行、结果、记录、事件）。
- **什么时候要跑**：改了 `tools/build_release.py` 的换版本演练，或者改了 `advisor/update_apply.py` 对外的约定（命令行、`result.json`、`apply.log` 的位置和那两句记录、「已经接手」的事件）。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_release_rehearse`（约 2 秒）。
- **需要什么**：不起真的程序、不编译：换版本程序、进程表、窗口标题都是替身，时钟和等待由参数注入。`WindowsTest` 和链接那一条只在 Windows 上跑，只起一个马上就退出的 Python 进程。

## test_build_info.py：测打包信息（发行版 `app\build.json`：打包时间、提交号、打包时有没有未提交的改动）怎么写、怎么读、写在哪

- **测试观点**（按测试类）：
  - 打包脚本怎么写（`GitStateTest`）：提交号和有没有未提交的改动照 git 的口径（没加进版本库的新文件不算改动，改了已跟踪的文件、暂存了也算）；还没有提交的仓库、仓库里的子文件夹（源代码压缩包解在别的仓库里面时就是这样）、找不到 git、git 报错或超时、提交号不是十六进制时没有提交号，照样给出打包时间；打包时间换成 UTC、精确到秒；写成的 build.json 正好三项、不含本机路径和用户名，程序读回来和写进去的一样；屏幕上的打包信息那一行，有未提交的改动、项目文件夹是 git 仓库（根目录下有 `.git` 文件夹或文件）却没记下提交号时另起一行醒目的提醒，写明是没找到 git 还是 git 出错（`test_screen_lines`、`test_no_commit_warning`）。
  - 程序怎么读（`ParseBuildInfoTest`）：三项都对时认下（大写的十六进制也认，多出来的项不看）；坏 JSON、不是对象、不是 UTF-8、字段缺、类型不对、时间认不出、提交号不是十六进制、没有提交号却写了有没有改动，都当作没有打包信息、不抛错；超过上限的文件不读；只有发行版读，开发版从源码运行时程序目录里有这个文件也不读。
  - 「关于」窗口「版本」一节的那一行（`AboutLineTest`）：打包时间换成本地时间、精确到分，提交号和未提交的改动写在括号里；本地时间一律传固定的时区换算（跨日、秒数截掉不进位、换算不了时不写这一行），不看跑测试的电脑在哪个时区；开发版、读不到打包信息时没有这一行。
  - 诊断包（`DiagnosticsBuildInfoTest`）：`version.txt` 里资料同步日期下面一行「打包信息」（时间写 UTC 原样；没有提交号、开发环境、发行版读不到各一句），`state.json` 和自检结果里的 `build` 一项照 build.json 的写法、开发环境为 null，读打包信息出了意外时记下原因、导出照常。
- **检证对象**：`tools/build_release.py` 的 `git_state`、`collect_build_info`、`write_build_info`、`is_repo_root`、`build_info_warnings`、`build_info_lines`；`advisor/version.py` 的 `BuildInfo`、`parse_build_info`、`read_build_info`、`build_info`、`build_line`、`about_sections`；`advisor/diagnostics.py` 的 `build_text`、`build_state`、`export`。
- **什么时候要跑**：改了打包信息的写法、读法（三个文件里上面那几处），「关于」窗口「版本」一节的行，诊断包的 `version.txt`、`state.json`、自检结果。改了「关于」窗口的版本卡另跑 `test_skin_neon_seaview`，打包流程里怎么用它另跑 `test_release`。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_build_info`（上次约 2 秒）。
- **需要什么**：`GitStateTest` 里几条在 `.tmp` 下的临时仓库里跑真的 git（找 git 先看环境变量 `GIT`，再看 PATH；都找不到时这几条跳过，跳过的原因写明）；跑之前去掉会让 git 去用别处的仓库或设置的环境变量，不读本机的全局、系统设置。git 报错、超时用替身。不打包、不联网。
- **输出怎么看**：同总则；临时仓库、临时文件在 `.tmp/tests-build-info/` 下，跑完删掉。
- **出错时一般是哪里不对**：
  - `GitStateTest.test_clean_then_dirty`、`test_not_its_own_repository`、`test_git_errors` 失败 → `git_state` 跑的 git 命令或认提交号、认改动的口径改了。
  - `test_screen_lines`、`test_no_commit_warning` 失败 → 屏幕上打包信息那一行或两条醒目提醒（`DIRTY_WARNING`、`NO_COMMIT_WARNING`）的写法、什么时候提醒改了；软件维护说明「打包发行版」的「打包信息」一条跟着改。
  - `ParseBuildInfoTest` 失败 → `parse_build_info` 认哪些写法、`BUILD_INFO_MAX` 改了；打包脚本写的和程序认的要一起改。
  - `AboutLineTest` 失败 → 「版本」一节里那一行的写法或位置改了（`build_line`、`about_sections`）。
  - `DiagnosticsBuildInfoTest` 失败 → 诊断包里「打包信息」一行的写法、位置，或 `build` 一项改了。

## test_release_zip.py：测用 libdeflate 编码器压出的发行版压缩包结构和内容都对、排除表没排错东西

- **测试观点**
  - `ZipWriterTest`：把一个小文件夹压成包，核对文件先后（按名字排序）、每个文件的时刻、目录结构、中文名的 UTF-8 标记、压缩方法号是 8（Deflate）、内容逐字节一致；可压缩的数据用 libdeflate 压出来不比 zlib 的 9 级大；把 zipfile 的 ZIP64 门槛调小，走大文件那条路也解得开；再用 PowerShell 的 `Expand-Archive`（和 Python 是两套独立实现）解一遍核对。
  - `LibdeflateHookTest`：`make_zip` 换掉的是标准库私有的 `zipfile._get_compressor`，钩上、用完恢复；升级 Python 改了这个私有函数的签名时能报出来；缺 deflate 包时报错并写清怎么装；压缩器 flush 出来的码流就是一次压完的标准 Deflate 流。
  - `ExclusionListTest`：排除表里该有的名字都在（几个用不到的 DLL，numpy 的测试、随机数等子包，几个标准库模块）；不能排除的（中文编码、warnings 的实现、识别要用的 numpy 子包、识别时按需加载的 onnxruntime 库）一个都不在，numpy 的核心扩展也不能被上层包名连带排掉。
- **检证对象**：`tools/build_release.py` 的 `make_zip`、`libdeflate_zip`、`require_deflate`、`_LibdeflateCompressor`，以及常量 `ZIP_TIMESTAMP`、`ZIP_DEFLATE_LEVEL`、`NOFOLLOW`、`NOFOLLOW_STDLIB`、`NOFOLLOW_NUMPY`、`NOFOLLOW_OTHER`、`NOINCLUDE_DLLS`；还有标准库 `zipfile` 的私有接口 `_get_compressor`。
- **什么时候要跑**：改 `tools/build_release.py` 的打包/压缩相关部分（`make_zip`、`libdeflate_zip`、`require_deflate`、`_LibdeflateCompressor` 或 `ZIP_*` 常量）；改动 `NOFOLLOW*`、`NOINCLUDE_DLLS` 排除表；改 `requirements-build.txt` 里 deflate 的版本；升级 Python（可能动了 `zipfile._get_compressor`）。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_release_zip`（上次约 1.6 秒；不是慢测试，也不是实机测试）
- **需要什么**：装了 deflate 包（`requirements-build.txt` 里的 libdeflate 绑定），没装时这一组会报错——错误信息里写了怎么装；`Expand-Archive` 那条要 Windows 上的 PowerShell，找不到就跳过。
- **输出怎么看**：没有特别输出，`Expand-Archive` 那条在非 Windows 上会以 skipped 出现并写出原因；跑的过程会在 `.tmp/tests-release` 下建临时文件夹，跑完自动删。
- **出错时一般是哪里不对**
  - `ZipWriterTest.test_structure_content_and_method` 失败 → `make_zip` 里写文件先后/时刻/属性/方法号的写法被改了（去看 `tools/build_release.py` 的 `make_zip`）。
  - `ZipWriterTest.test_content_is_smaller_than_zlib_9` 失败 → `ZIP_DEFLATE_LEVEL` 被调低，或 `_LibdeflateCompressor`/`libdeflate_zip` 没真正用上 libdeflate。
  - `ZipWriterTest.test_zip64_path_used_for_large_files` 失败 → `make_zip` 里给 `info.file_size` 那一步被删了，ZIP64 的分支走不到。
  - `ZipWriterTest.test_expand_archive_unpacks_identically` 失败 → 压出来的包别的解压实现读不了（码流不是标准 Deflate）。
  - `LibdeflateHookTest.test_zipfile_private_hook_unchanged` 失败 → 多半是 Python 升级改了 `zipfile._get_compressor` 的参数，要复核 `libdeflate_zip` 的写法。
  - `LibdeflateHookTest.test_swaps_and_restores` 失败 → 钩子用完没恢复，或只换 Deflate、别的压缩方法没照标准库走。
  - `LibdeflateHookTest.test_missing_deflate_raises_clear_error` 失败 → `require_deflate` 不再报错（比如悄悄退回 zlib），或提示文字里少了包名/文件名。
  - `LibdeflateHookTest.test_compressor_matches_deflate_directly` 失败 → `_LibdeflateCompressor` 的分块/一次压完逻辑被改坏。
  - `ExclusionListTest.test_dll_exclusions`、`test_module_exclusions` 失败 → 排除表里少了某个名字，或不该排的又排了。
  - `ExclusionListTest.test_required_modules_not_excluded` 失败 → 把编码、warnings 实现或 numpy 必用子包排进了发行版。有意增删排除表时，断言里的名单要一起改。

## test_build_tools.py：打包和数据检查这几个工具脚本的加固（Nuitka 环境的 PATH 与变量、图片清单的写盘规矩、取图工具被拦就停、数据检查不用 assert）

- **测试观点**（按测试类）：
  - `BuildEnvPathTest`：给 Nuitka 的 PATH 去掉了开发环境（项目根的 `.venv`）和任何 `site-packages` 下的条目，系统目录和其余条目照原顺序留下，打包环境里的 zig 仍放最前。
  - `CompileEnvTest`：链接参数固定成通用的 x86-64（`LINK_CPU_FLAGS`，防止按打包电脑的处理器编运行库）；打包电脑上残留的编译参数（`CFLAGS` 等）去掉；名字里带 `TOKEN`、`PASSWORD`、`_KEY` 一类字样的环境变量不传给子进程，编译要用的系统变量照传。
  - `MakeManifestTest`：有图片在 wiki 上找不到、或本地文件和 wiki 上的对不上时，默认打印出来、退出码 1、不写清单；加了 `--force` 才照写。接口请求之间照 `assets.API_INTERVAL` 隔开，用完关掉连接；被站点防护拦下、被限流（429、503）时马上停下，打印原因，退出码 2，不写清单。`data/card_crops.json` 里列的角色照问卡面：清单项的 `title`、`url`、`size`、`sha1` 是卡面的，另带 `crop`；开发目录里那张不是按这张卡面、这份裁法裁的（没有记录、卡面换了、本地没有）时同「不一致」处理；`card_crops.json`、`sfw_skip.json` 读不出来、写得不对，`heroes.json` 读不出来、没有角色编号时不写清单、不问接口；清单项都问 BWIKI 的接口、不带 `api`。识别用不到的不列（`test_profiles_only_read_by_recognition`、`test_sfw_skip_left_out`）：Q 版头像只收识别会读的（和认头像同一个判断 `stars.profile_photo_hero`，编号在 `heroes.json` 里），编号四位数的、不是角色的、编号前面补 0 的都不列、不问接口；`sfw_skip.json` 里列的健全版不列，照原样下载的、从卡面现裁的都一样（现裁的连卡面也不问），不在名单里的照常列，没有名单时都列；开发目录里的图照留；打印不列的张数。名单里写了健全版以外的文件名（普通版、卡面等）时报出写错的那几个、退出码 1、不写清单、不问接口，认健全版的写法同 `check_data`（`test_sfw_skip_only_sfw_names`）；打印的「需下载约 N MB」按十进制算（`test_megabytes_decimal`）。wiki 接口用替身，不联网。
  - `FetchCropTest`：`fetch_assets.py --crop` 按 `card_crops.json` 现裁角色照、在覆盖清单里记下（和首次运行时裁的一样），再跑一遍都跳过、文件不动；`--cards` 时卡面从文件夹读、不建取图器，按文件夹里的 `imageinfo.json`、没有时按清单核 SHA-1，对不上、读不到、核对不了的不裁（退出码 1），`--hero` 只裁这几个角色的；不给 `--cards` 时向接口要地址、下载卡面，接口和图片各一条连接，被拦就停（退出码 2，裁好的照留）；旧图是硬链接时整份换掉、链接到的那一份不动；某一张裁图时报的不是 `CropError`（离谱的裁法算出 NaN、OpenCV 报错）也只算这一张没裁成，其余照裁、最后退出码 1（`test_one_bad_crop_does_not_stop_the_rest`）；放在备用地址上的（`MIRRORED`）不裁，开发目录里那张一个字节不动（`test_mirrored_not_cut`）；普通的角色照下载跳过这些角色照；参数写错时报错退出。
  - `FetchAssetsTest`、`FetchChipIconsTest`：两个取图工具走 `assets._Fetcher`，接口和图片各一条连接（间隔分别是 `assets.API_INTERVAL`、`assets.SLOW_INTERVAL`；给了间隔的取图器只按这条连接上的固定间隔，不按首次运行下载每秒的上限，这一点在 `test_download_block.py` 的 `test_fetcher_interval` 里测），用完都关掉；本地已有的不下；下到一半被站点防护拦下（567、拦截页面）或被限流时马上停下、之后不再发请求，退出码 2，已经下好的照留，`--manifest` 时不写清单；别的出错（404）照旧报错。`fetch_assets.py` 的 Q 版头像和清单一个口径，只下识别会读的（`read_by_recognition`），打印没下几张，`heroes.json` 读不出来时不连网、退出码 1；角色照照旧下全（`test_profiles_only_read_by_recognition`）。取图器换成替身，不联网；图片写到临时文件夹。
  - `SfwCoverTest`：`tools/check_sfw_cover.py` 只把只用普通版的角色照认得出的健全版算成不用下载（本人的最高分 ≥ 0.93、别的角色的 ≤ 0.83，边上的值算不用下；一模一样的脸部相关度是 1，0.4、0.6 两种缩放取低的那个（和先后无关），整片一个颜色的模板、缩小以后太小的比不了）；健全版换了一张画的、没有普通版的要下；斜着摆的一套两张都先转过来再比（脸部取法同认角色建模板）；默认只打印每张的分数、要不要下和与名单的差别，不写文件；`--write` 新建时写 CRLF、说明用工具里的那段，内容没变不写，现有的名单不一样时打印多了、少了、分数变了哪几张，说明和换行照原文件；有角色照读不出来、普通版或健全版一张都没有、名单读不出来时退出码 1、不写。用造出来的小图，不碰真的 `assets/`、`data/`。
  - `RefusalNoteTest`：被拒时打印的那一句（`assets.refusal_note`）写明原因和几点几分以后再运行（35 分钟以后进到整分，Retry-After 更长按它，不是今天的另写月日）；别的出错不算被拒。
  - `CheckDataNoAssertTest`：`tools/check_data.py` 里没有 `assert`（`python -O` 会跳过 assert，坏数据会被悄悄放过）。
- **检证对象**：`tools/build_release.py` 的 `path_entries_without_dev_env`、`build_env`、`compile_env`、`is_secret_env_name`（及 `LINK_CPU_FLAGS`、`COMPILE_ENV_DROP`）；`tools/make_assets_manifest.py` 的 `main`、`read_crops`、`read_sfw_skip`、`hero_ids`、`read_by_recognition`、`SFW_PHOTO`、`MB`；`tools/fetch_assets.py` 的 `main`、`fetch_kind`、`crop_photos`、`read_crops`；`tools/fetch_chip_icons.py` 的 `main`、`run`；`tools/check_sfw_cover.py` 的 `main`、`load_faces`、`similarity`、`covers`、`Cover`、`write_skip`（脸部取法来自 `advisor/vision/hero.py` 的 `_load_templates`、`turn_photo`）；`advisor/vision/stars.py` 的 `profile_photo_hero`；`advisor/assets.py` 的 `refusal_note`；`tools/check_data.py`。辅助：`tests/support/sandbox.py`、`advisor/paths.py`。
- **什么时候要跑**：改了 `tools/build_release.py` 里给 Nuitka 拼环境变量的部分（PATH 过滤、编译/链接参数、去掉名字里像密钥的环境变量）、改了 `tools/make_assets_manifest.py` 的写清单规则（含哪些图不列）、改了两个取图工具（`tools/fetch_assets.py`、`tools/fetch_chip_icons.py`）的请求和被拦时的处理、改了 `tools/check_sfw_cover.py`（口径、写名单）或 `advisor/vision/stars.py` 的 `profile_photo_hero`、改了 `tools/check_data.py`（尤其是把某处改成 `assert`），或者改了依赖清单/打包环境布局而影响到 PATH 时。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_build_tools`（上次约 0.06 秒）。
- **需要什么**：没有特别要求。不联网（wiki 接口用替身）、不真的编译，清单也只写到临时文件夹，不碰真的 `data/`。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `BuildEnvPathTest` 失败 → `build_release.py` 的 PATH 过滤规则改了（大小写、正反斜杠、`site-packages` 判定），或 zig 没放最前（去看 `path_entries_without_dev_env`、`build_env`）。
  - `CompileEnvTest.test_ldflags_target_generic_cpu` 失败 → `LINK_CPU_FLAGS` 或 `compile_env` 对 `LDFLAGS` 的处理改了。
  - `CompileEnvTest.test_inherited_compile_flags_dropped` 失败 → `COMPILE_ENV_DROP` 名单改了。
  - `CompileEnvTest.test_secret_names_dropped` 失败 → `is_secret_env_name`（`SECRET_ENV_WORDS`/`SECRET_ENV_SUFFIXES`）改了，或把该传给子进程的系统变量也误删了。
  - `MakeManifestTest` 某项失败 → `make_assets_manifest.py` 在有找不到/不一致时仍写了清单，或 `--force` 该写时不写、退出码/打印文字变了（有意改提示时，断言的预期一起改）；`test_blocked_stops_without_writing` 失败 → 被拦、被限流时没马上停下，或照写了清单。
  - `FetchAssetsTest` / `FetchChipIconsTest` 某项失败 → 取图工具被拦时没马上停下、停下以后还发了请求、退出码不是 2，或停下时没留住已经下好的；也可能是改了建取图器时给的间隔（去看两个工具的 `main`）。取图器拿到间隔以后是不是真的照它等，看 `test_download_block.py` 的 `test_fetcher_interval`。
  - `FetchCropTest` 某项失败 → `fetch_assets.py` 的 `crop_photos`（核对卡面、跳过裁好的、整份换掉、记录）、`read_crops` 或 `main` 的参数改了；也可能是裁图本身（`advisor/vision/cardcrop.py`）或记录的写法（`assets.note_crop`）改了，先看 `test_card_crop`。
  - `MakeManifestTest.test_profiles_only_read_by_recognition` / `test_sfw_skip_left_out` 失败 → `make_assets_manifest.py` 挑掉识别用不到的那一步（`read_by_recognition`、`read_sfw_skip`）改了，或 `stars.profile_photo_hero` 认编号的写法改了（先看 `test_stars` 的 `ProfilePhotoNameTest`）。
  - `SfwCoverTest` 某项失败 → `check_sfw_cover.py` 的口径（`OWN_MIN`、`RIVAL_MAX`、`SCALES`、`PAD`）、比法（`similarity`）、取脸部（`load_faces`，用的是 `hero._load_templates`）或写名单（`write_skip`）改了；`test_thresholds` 失败也可能是认角色的门槛（`hero.MIN_SCORE`、`MIN_MARGIN`）改了，口径要跟着重新定。
  - `RefusalNoteTest` 失败 → `assets.refusal_note` 的写法或几点几分的算法（`assets._resume_at`）改了。
  - `CheckDataNoAssertTest` 失败 → `tools/check_data.py` 里又写了 `assert`。

## test_build_cv.py：自编 OpenCV / 不带 OpenBLAS 的 numpy，核对编译、打包脚本里不需要真编译的部分

- **测试观点**
  - `CompilerEnvTest`：编译环境按目录直接拼（`INCLUDE`、`LIB`、`PATH` 都指到便携 MSVC/SDK），丢掉打包机上残留的编译参数（`CL`、`_CL_` 等），只认常量定的 MSVC/SDK 版本，缺工具集/编译器时报错。
  - `OpencvPatchTest` / `OpencvCmakePatchTest`：`setup.py` 和 OpenCV 自己的 CMake 脚本的补丁打得对、重复跑不会重复打、对不上时报出来，写回时 CRLF/LF 照原样；补丁里的 CMake 函数用真的 `cmake -P` 跑一遍。
  - `LocalPathPrefixTest` / `BuildCvArgsTest`：去掉打包电脑的路径的前缀表（长的在前、不分大小写）和编译参数（只在 `OPENCV_EXTRA_*` 里加 `/d1trimfile`，不整个覆盖 `CMAKE_C_FLAGS` 等）。
  - `SourceAndBuildTest` / `NumpyScrubTest` / `DownloadChecksumTest`：取源码核对标签、编 wheel 的两条命令照官方 CI、改写 numpy wheel 去掉打包电脑的路径、下载的东西先核对 SHA-256 再用。
  - `UsedCv2NamesTest`：项目代码和 `rapidocr_lite` 里用到的 `cv2` 名字扫得全（打包检查「自编版里都有」按它查；官方版和自编版逐位比对的工具 `tools/compare_cv.py` 的探针也要覆盖这些名字，由 `tests/test_dev_tools.py` 的 `ProbeCoverageTest` 核对）。
  - `BuildReleaseSelfbuildTest` / `BuildReleaseCvNamesTest` / `BuildReleaseSelfbuildEnvTest`：打包脚本把 requirements 里的 OpenCV/numpy 换成自编 wheel、检查打包环境的 `cv2` 名字齐全、编译之前核对打包环境装的确实是自编版。
- **检证对象**：`tools/build_cv.py`、`tools/build_release.py`。编译命令、`git`、下载都换成记录的假函数；不联网、不下载、不真编译；打包脚本的密钥文件换成假的。
- **什么时候要跑**：改了 `tools/build_cv.py`、`tools/build_release.py`，或改了 OpenCV/numpy 版本常量、补丁、编译参数、打包环境的核对逻辑时（改了 `used_cv2_names`、项目里新用了 `cv2` 的名字时另跑 `test_dev_tools`）。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_build_cv`（上次没有记录；跑一次以后会记进 `.tmp/test-times.json`）。
- **需要什么**：没有特别要求（不需要样本、不联网）。`OpencvCmakePatchTest.test_cmake_function` 需要自编环境里装了 `cmake`（`tools/build_cv.py --setup` 装的那个），没有时这一条跳过（原因「没有 cvbuild-venv 里的 cmake（tools/build_cv.py --setup 装）」）；临时文件都建在 `.tmp` 下。
- **输出怎么看**：只有那一条会跳过；没有别的跳过。测试全程用假文件/记录，不真的下载或编译。
- **出错时一般是哪里不对**
  - `CompilerEnvTest.*` 失败 → 编译环境的拼法或丢掉的变量清单改了（去看 `tools/build_cv.py` 的 `compiler_env`、`COMPILER_ENV_DROP`）。
  - `OpencvPatchTest.*` / `OpencvCmakePatchTest.*` 失败 → 补丁的原文/新文或 patch 辅助函数改了，也需要跟着更新 `OPENCV_SETUP_PATCHES`、`OPENCV_CMAKE_PATCHES`。
  - `LocalPathPrefixTest.*` / `BuildCvArgsTest.*` 失败 → 前缀表或去打包电脑的路径的编译参数改了（去看 `local_path_prefixes`、`opencv_cmake_args`、`trimfile_flags`）。
  - `SourceAndBuildTest.*` / `NumpyScrubTest.*` / `DownloadChecksumTest.*` 失败 → 取源码、编 wheel、改写 numpy wheel 或 SHA-256 核对的逻辑改了。
  - `UsedCv2NamesTest.*` 失败 → 扫描 `cv2` 名字的逻辑或清单改坏了。
  - `BuildRelease*Test` 失败 → 打包脚本用自编 wheel、`cv2` 名字检查或打包环境核对改了。
  - **改了预期就该跟着改**：版本常量、补丁原文/新文、`OFFICIAL_CPU_DISPATCH` 等是有意的常量，改了版本或 OpenCV 写法时要把本文件的预期一起改。

## test_manual_html.py：测 docs/使用说明.md 怎么转成发行版里的「使用说明.html」、截图对不对得上

- **测试观点**
  - `SlugTest`：指锚点的算法和 GitHub 一致（括号、斜杠、下划线、大小写、空格转横线等）。
  - `ConvertTest`：转换支持的写法（标题级别、段落里中文不加空格、嵌套列表、行内的粗体/代码/链接、图片嵌成 data URI 并按一半显示、图为注、同输入同字节）；不支持的写法要报错并写明使用说明的行号；第一个标题之前的块当导语放在目录前面；两种排法的目录（宽屏用的侧边目录和窄屏、手机、打印用的书本式目录）是同一份项、同样的链接、都能跳到存在的锚点，项的文字以「开头时带 `lb`；页头标题前有标志（`BADGE`，画在网页里），它的 `id` 不和标题的锚点重名；版式样式里侧边目录在宽屏常驻（`sticky`）、开头目录和「↑ 目录」在宽屏不显示、手机上一栏排小节、打印时侧边目录不印。
  - `ReadmeManualTest`：由真实的 `docs/使用说明.md` 生成的整页——开头照首页写的纯视觉声明（`docs/使用说明.md` 里不重复）、各节先后（`##` 一级、`###` 二级，比首页低一级的层级）、一个文件就能看（不引用外部文件、图片都是嵌进去的 PNG）、锚点能跳且和 GitHub 一致、不残留 Markdown 记号、不写皮肤、截图和 `docs/manual` 一一对应且按两倍像素画、每张图后面紧跟从 1 起的编号图注、系统需求照商店页的样子写、三步上手/界面导览/一局中的操作/常见问题各节的篇幅、路径按发行版结构写；常见问题里双击启动程序以后弹窗的四条，问题里的提示逐字照 `packaging/launcher.c` 的弹窗、回答照弹窗上说的操作写（`test_launcher_answers`）；首页、`CONTRIBUTING.md` 和 `.github` 下的表单里指向网页版的链接，网址是 `SOURCE_URL` 那个仓库的 GitHub Pages、带的锚点是这一页里的标题（`test_online_links`）。
  - `TrayMenuTest`：截图里那张托盘菜单的项和悬浮窗真实的托盘菜单是同样几项。
  - `MakeManualImagesTest`：画图脚本跑得通——在子进程里离屏只画一张小图，输出是两倍像素的 PNG，临时文件不留在输出文件夹里。
  - `CommandLineTest`：命令行入口（`python tools/manual_html.py --out 路径`）只用标准库的 Python（`-I -S`，没有 PySide6、numpy 等）也能生成整页；写出的文件和 `build_release.manual_html()` 逐字节相同，是 UTF-8、不带 BOM。
  - `WorkflowTest`：`.github/workflows/manual-pages.yml` 运行的就是这个入口（命令和 `--out` 的参数对得上），上传的 `_site` 正是生成网页所在的文件夹；触发条件里有影响网页内容的文件，权限齐全；只在 `SOURCE_URL` 那个仓库公开以后发布（复刻的仓库、其他副本和仓库还是私有的时候 build 跳过、deploy 跟着跳过）。
- **检证对象**：`tools/manual_html.py` 的 `slug`、`render`、`STYLE`、`BADGE`、`Parser`、`Renderer`（`toc`、`side_toc`）、`MarkdownError`、`build_page`、`manual_section`、`front_note`、`write_page`、`main`；`tools/build_release.py` 的 `manual_html`、`manual_section`、`front_note`、`MANUAL_IMAGE_SCALE`、`LAUNCHER_EXE`、`EXE`；`.github/workflows/manual-pages.yml`；`tools/make_manual_images.py` 的 `SCALE`、`SHOTS`、`TRAY_MENU`、`main`；内容侧还有 `docs/使用说明.md`、首页 `README.md` 开头的粗体声明、首页和 `CONTRIBUTING.md`、`.github` 下的表单里指向网页版的链接、`advisor/version.py` 的 `SOURCE_URL`、`docs/manual/*.png`、`packaging/launcher.c`、`advisor/ui/overlay_parts/follow.py` 的 `Tray.setup_tray`、`advisor/vision/window_capture.py` 的 `WINDOW_BUILD`、`advisor/ui/settings/dialog.py` 的 `LOWEND_TITLE`、`advisor/ui/theme` 的皮肤名单、`advisor/version.py`。
- **什么时候要跑**：改 `docs/使用说明.md`（增删标题、改文字、换截图、改路径写法）；改首页 `README.md` 开头的粗体声明，或首页、`CONTRIBUTING.md`、问题表单里指向网页版的链接；改 `tools/manual_html.py`（转换、生成整页、命令行入口）或 `tools/make_manual_images.py`；动 `docs/manual` 下的截图；改 `.github/workflows/manual-pages.yml`；改 `packaging/launcher.c` 的弹窗文字；改系统需求里引用的版本号（`WINDOW_BUILD`）或设置窗口里的开关名（`LOWEND_TITLE`）；改皮肤名单或版本号、资料同步日期。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_manual_html`（上次约 2.5 秒；不是慢测试、实机测试）
- **需要什么**：Qt（离屏画图那一组，不弹窗口、不截屏）；Pillow（画图脚本存 PNG 用）；已有的 `docs/manual` 截图；`docs/使用说明.md`、`packaging/launcher.c`；Windows 的微软雅黑字体（缺了只是提示，不中止）。
- **输出怎么看**：`ConvertTest` 报错时异常里带着「使用说明.md 第 N 行」，指出是哪一行写法不认；画图那条在子进程里跑，失败时把它的错误输出附在断言里；临时文件夹建在 `.tmp/tests-manual` 下，跑完自动删。
- **出错时一般是哪里不对**
  - `SlugTest.test_github_rules` 失败 → 锚点算法（`tools/manual_html.py` 的 `slug`）和 GitHub 对不上。
  - `ConvertTest.test_blocks`、`test_lead_before_toc`、`test_english_lines_join_with_space_and_text_is_escaped`、`test_ordered_start_and_loose_list`、`test_image_and_legend` 失败 → 转换的某类写法（标题/段落拼接/列表/图片与图注/行内标记）改坏了（去看 `tools/manual_html.py` 的 `Parser`、`Renderer`）。
  - `ConvertTest.test_unsupported_is_reported_with_readme_line` 失败 → 不支持写法没报错，或行号算错（`MarkdownError` 的 `line`）。
  - `ConvertTest.test_two_tocs`、`test_badge_in_header`、`test_layout_style` 失败 → 两种排法的目录（`Renderer.toc`、`side_toc`：项、链接、`lb`、位置）、页头标志（`BADGE`）或版式样式（`STYLE` 里宽屏、手机、打印那几块）改坏了。
  - `ConvertTest.test_same_input_same_bytes` 失败 → 转换结果不再稳定（顺序、时间戳之类被带进去了）。
  - `ReadmeManualTest.test_page`、`test_badge`、`test_sections_in_order`、`test_anchors`、`test_no_markdown_left_and_no_maintenance` 失败 → 使用说明的结构/标题变了，或生成的网页不满足这些要求（两个目录的项和链接、页头标志）；有意改节次时，`SECTIONS` 之类的预期要一起改。
  - `ReadmeManualTest.test_pure_vision_first` 失败 → 首页 `README.md` 开头（第一个「## 」标题前面）没有那两行粗体的声明（纯视觉、支持的平台和游戏语言）；或者纯视觉声明在 `docs/使用说明.md` 里又写了一遍；或者生成的网页开头（目录前面）没照首页写（去看 `tools/manual_html.py` 的 `front_note`、`build_page`）。
  - `ReadmeManualTest.test_self_contained`、`test_images`、`test_legends_follow_images` 失败 → 引用了外部文件、图片没嵌进去、截图和使用说明引用对不上、或图注编号不连续。
  - `ReadmeManualTest.test_no_skins` 失败 → 使用说明里出现了皮肤或各套皮肤的名字。
  - `ReadmeManualTest.test_system_requirements`、`test_first_steps`、`test_tour`、`test_short_answers` 失败 → 这几节的文字/篇幅/截图变了；有意改时把断言的预期一起改（例如系统需求里的版本号、开关名）。
  - `ReadmeManualTest.test_release_layout` 失败 → 路径写法或双击的程序名没跟 `packaging/launcher.c` 一致。
  - `ReadmeManualTest.test_launcher_answers` 失败 → 常见问题里引用的弹窗提示和 `packaging/launcher.c` 的原文对不上（改了弹窗文字没改使用说明，或反过来），或回答里少了弹窗上说的操作。
  - `ReadmeManualTest.test_online_links` 失败 → 指向网页版的链接锚点不是这一页里的标题（改了标题没改首页、问题表单里的链接），网址和 `SOURCE_URL` 那个仓库的 GitHub Pages 对不上，或首页、问题反馈表单里的网页版链接少了。
  - `TrayMenuTest.test_same_items` 失败 → 悬浮窗的托盘菜单项和画图脚本里的菜单项不一致了。
  - `MakeManualImagesTest.test_draws_one_image` 失败 → 画图脚本起不来、画不出、输出不是两倍像素的 PNG，或把临时文件留在了输出文件夹里。
  - `CommandLineTest.test_writes_same_bytes_without_site_packages` 失败 → 命令行入口在干净环境里跑不起来（导入了第三方包，或 `-I` 下没把项目根目录加进 `sys.path`）、写出的字节和 `build_release.manual_html()` 不同，或带了 BOM（去看 `tools/manual_html.py` 的 `build_page`、`write_page`、`main`）。
  - `WorkflowTest.*` 失败 → `.github/workflows/manual-pages.yml` 里运行的命令、`--out` 的路径、上传的文件夹、触发条件或权限改得和入口对不上，或 build 不再只在 `SOURCE_URL` 那个仓库里跑（`test_only_the_public_repo`）。

## test_excluded_imports.py：测「程序用到了发行版排掉的模块」这道钩子，以及测试运行器收记录、据此判失败

- **测试观点**
  - `ExcludedImportsTest`：用临时文件夹里造的假项目、假测试包、放在项目文件夹里的假虚拟环境（`.venv`）和假的被排除模块，逐条核对钩子的判定——程序直接导入就记一条、测试/工具自己导入不算、子模块也算命中、不在排除表的不记、库装在项目文件夹里照样算库、程序目录里本身不随发行版的模块（例如命令行入口）不算、只 `find_spec` 查询不算导入、有退路的那一组的记法与边界、排除表读不出时不打断导入只记一条错、环境变量没设就不装。
  - 换到「运行器」这一侧：收齐各进程的记录并按处去重、拼出单列文字；收到了记录或钩子报的错时整次算没通过（退出码 1、JSON 里 `ok` 为假、不写「全部通过」、跑完删记录文件夹、`--no-excluded-imports` 时不建文件夹也不传下去）；只有「有退路」的一组时另起一行但算通过。
  - `NumpyMaImportTest`：在干净的子进程里把排除表只设成 `numpy.ma`，真调一次 `np.median`，核对那一条被记下来、记录已经是写进文件的了（子进程用 `os._exit` 退出，等于进程结束时不会补写）。
- **检证对象**：`tests/support/excluded_imports.py` 的 `install`、`Recorder`（`find_spec`/`_record`/`_kind`/`_write`）、`load_tables`、`load_excluded`、`matches`；`tools/run_tests.py` 的 `collect_excluded`、`collect_excluded_fallbacks`、`collect_excluded_errors`、`excluded_lines`、`fallback_line`、`main`、`EXCLUDED_ENV`；以及 `tools/build_release.py` 从源码里读出来的 `NOFOLLOW`、`NOFOLLOW_WITH_FALLBACK`。
- **什么时候要跑**：改 `tests/support/excluded_imports.py`；改 `tools/run_tests.py` 里收记录、判失败、拼文字那几段；改 `tools/build_release.py` 的 `NOFOLLOW`、`NOFOLLOW_WITH_FALLBACK`（加/减被排除的模块或「有退路」的登记）；改了运行器往子进程传环境变量的方式。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_excluded_imports`（上次约 1.0 秒；不是慢测试、实机测试）
- **需要什么**：装了 numpy（最后一条要在子进程里真调 `np.median`）；其余用例用自己造的假项目和假模块，不依赖真的被排除库。
- **输出怎么看**：`NumpyMaImportTest` 会在子进程里打印一行 `RECORDS ...`，只有失败时才会在日志里看到；临时文件夹建在系统临时目录下（`excluded-imports-*`），跑完自动删；其余同总则。
- **出错时一般是哪里不对**
  - `ExcludedImportsTest.test_program_import_is_recorded` 失败 → 钩子没记下来，或「记下时就写文件」坏了（去看 `tests/support/excluded_imports.py` 的 `Recorder.find_spec`/`_write`）。
  - `test_test_dir_import_is_ignored`、`test_excluded_program_module_does_not_count` 失败 → 判「算不算程序」的分类逻辑（`Recorder._kind`）把测试/工具或程序目录里不随发行版的模块算成程序了。
  - `test_submodule_is_recorded`、`test_module_outside_table_is_ignored` 失败 → 命中的规则（`matches`）改宽或改窄了。
  - `test_library_inside_project_folder` 失败 → 项目文件夹里的虚拟环境被当成了「项目里别的地方」，没算成库。
  - `test_find_spec_lookup_is_not_an_import` 失败 → 只查询不导入的判定（`_lookup_only`）失效。
  - `test_fallback_via_registered_library_is_listed`、`test_fallback_needs_registered_library` 失败 → 「有退路」的一组判定（`Recorder._importers` 与 `via`）记错了或不记了。
  - `test_fallback_group_registered_in_nofollow` 失败 → `NOFOLLOW_WITH_FALLBACK` 里的名字不在 `NOFOLLOW` 里、登记的那个库本身被排掉或没装、或 `load_tables` 读不出来。
  - `test_unreadable_table_does_not_break_imports` 失败 → 排除表读不出时的容错（`Recorder._table`）没兜住。
  - `test_env_var_installs_and_absent_skips` 失败 → `install` 的环境变量开关（`ENV_VAR`）不认或没设也装了。
  - `test_run_tests_collects_and_dedups`、`test_run_tests_fails_on_records` 失败 → 运行器收记录、去重、拼文字、判失败、删文件夹、JSON 字段或 `--no-excluded-imports` 的行为改了（去看 `tools/run_tests.py`）。
  - `test_load_excluded_matches_build_release` 失败 → 从源码里解析排除表的写法跟不上 `build_release.py` 的新写法，或有意把 `numpy.random`（不该在表里）之类加进去了。
  - `NumpyMaImportTest.test_numpy_median_records_numpy_ma` 失败 → numpy 版本变了使 `np.median` 不再按需导入 `numpy.ma`，或钩子的装法/退出前写文件的行为坏了。

## test_selfcheck_vision.py：核对打包自检里「识别读数」这一整套——`readout.read_frame` 在一张画面上把各项识别都读出来、某项出错只记那一项；源码和发行版的读数比对；打包自检选的样本覆盖每一项读数；试启动时那一轮的流程；自检入口带不带样本清单的分派。

- **测试观点**（按测试类）：`ReadFrameTest`（一张对局画面上各项都读出、能写 JSON、玩家列表只记角色和是不是电脑、不记名字；某个识别函数抛异常时只记这一项）；`CompareVisionTest`（两边相同无问题；有一项不同、一边出错一边没出错、少一张图都报出；问题条数有上限）；`SmokeProblemsVisionTest`（不一致原样进打包问题表；这台电脑没有样本时这一项没跑、不算问题）；`VisionSmokeTest`（试启动那一轮：识别图片在启动发行版之前复制进试启动用的临时个人目录（`LOCALAPPDATA` 指到的 `.tmp` 下，发行版从那里读）、经启动程序带清单自检、读数写到打包临时文件夹；读数不同、两边库版本不同、发行版没写出、写坏了、退出码非 0、超时、比对本身出错、本机没样本各自的表现；识别图片只往那个临时个人目录复制，目标是发行版文件夹、试启动的程序文件夹、打包电脑上真的个人目录时拒绝，发行版文件夹里不多文件）；`VisionCoverageReportTest`（覆盖表里本机缺的那张写明没覆盖到）；`SelfCheckEntryTest`（`ACC.exe --selfcheck` 带不带 `--vision-samples` 的分派）；`SelfCheckVisionItemTest`（带清单的自检多一项「识别读数」、读数写到程序目录的 `.tmp`、不带清单时没有这一项、也不改文字识别的线程数）；`CoverageTest`（打包自检选的每个读数都有一张图读出真东西，含轮次被挡住时看的那一小块、奖励页的星币数、通关画面和打输横幅的颜色和认字）。
- **检证对象**：`advisor/vision/readout.py` 的 `read_frame`/`read_items`/`_Reader` 和它记的每一项读数；`tools/build_release.py` 的 `compare_vision`、`smoke_problems`、`vision_smoke`、`vision_coverage`、`copy_vision_assets`、`smoke_images_dir`、`VISION_SAMPLES`/`VISION_COVERAGE`/`VISION_PROBLEM_MAX`/`VISION_READINGS`/`VISION_MANIFEST`/`VISION_ASSET_DIRS`；`advisor/diagnostics.py` 的 `self_check`/`self_check_main`/`vision_samples`/`VISION_SAMPLES_CHECK`/`VISION_SAMPLES_FILE`/`LIBRARIES_CHECK`；`packaging/chip_advisor.py` 的 `main`/`VISION_SAMPLES_ARG`。
- **什么时候要跑**：改了 `readout.py`（增删读数项、改编码）、改了 `tools/build_release.py` 的比对或试启动流程、样本清单或覆盖表、改了自检或带样本自检（`diagnostics.self_check`）、改了入口的参数处理（`packaging/chip_advisor.py`）。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_selfcheck_vision`（上次约 0.43 秒；是识别模块；`ReadFrameTest`、`CoverageTest` 要读样本，慢一些）。
- **需要什么**：`samples/`（`ReadFrameTest`、`CoverageTest` 用，`CoverageTest` 另要 `samples/pages/` 里 `VISION_COVERAGE` 写的两帧录像；缺了跳过）和 `assets/` 里的角色照、Q 版头像、筹码图标、星数横条（缺了跳过）；其余几条用假对象，不读样本、不启动程序。
- **输出怎么看**：跳过原因写明缺 `labels.json` 还是缺 assets；`CoverageTest` 失败时会指出哪张图的哪一项没读出（子测试名带着项名和文件名）；试启动那几条在沙箱里用假的 `subprocess.run` 和假的读数，不启动程序、不写真实文件。
- **出错时一般是哪里不对**：
  - `ReadFrameTest.test_reads_every_module_and_writes_json` 失败 → `readout.read_frame` 漏了一项或某项读不出；看 `READOUT_KEYS` 和各项的编码。
  - `test_one_module_failing_only_records_that_one` 失败 → 某项出错没被单独接住（`_Reader.run`）。
  - `CompareVisionTest.*` 失败 → `compare_vision` 的比对逻辑改坏（哪些算不同、条数上限、末尾提示里的明细路径）。
  - `VisionSmokeTest.*` 失败 → 试启动那一轮的流程改坏（复制哪些图片、清单、读取数文件、超时或写坏时的处理、退出码）。
  - `VisionCoverageReportTest`／`CoverageTest` 失败 → `VISION_COVERAGE` 和实际样本对不上，或某项在一张图上读不出；改了 `readout` 或换了样本时要把 `VISION_COVERAGE`/`VISION_SAMPLES` 一起改（这是有意选的样本）。
  - `SelfCheckEntryTest.test_dispatch` 失败 → `chip_advisor.main` 对 `--vision-samples` 的分派改坏，或 `VISION_SAMPLES_ARG` 和 `build_release.VISION_ARG` 不一致。
  - `SelfCheckVisionItemTest.*` 失败 → 带清单自检多出的这一项、写文件的位置、或读样本时把文字识别线程数改成 1 这几处改坏（`diagnostics.self_check`）。

## test_selfcheck_key.py：核对自检里打包密钥那一项（`diagnostics.STATE_KEY_CHECK`；发行版要导入得到打包时编进程序的密钥模块，且拿到的不是公开密钥）和打包对它的要求（没加 `--public-key` 打包时这一项不通过算打包失败），并守住密钥不泄漏。

- **测试观点**（按测试类）：`StateKeyCheckTest`（发行版：密钥模块导入得到、密钥是 32 字节而且不是公开密钥才通过；导入不到、内容不对、正好是公开密钥都不通过，且说明里没有密钥的任何写法；开发版：不带打包密钥是正常的、写一句、不算失败；自检里有这一项、且不算关键项；发行版加载上了密钥时，内存里的结果、写到 `.tmp/selfcheck.json` 的和打到标准输出的都不含密钥；发行版没加载上密钥时这一项不通过、但不算关键项，整个自检不因它失败）；`SmokeStateKeyTest`（打包试启动：编译时放进了密钥的，这一项没通过算打包失败；加了 `--public-key`、或自检结果里没有这一项时不查；试启动结果里记下这一包要不要求这项通过，且结果和日志里都没有密钥）。
- **检证对象**：`advisor/diagnostics.py` 的 `state_key_check`、`STATE_KEY_CHECK`/`STATE_KEY_LOADED`/`STATE_KEY_DEV`、`is_frozen`、`self_check`/`self_check_main`；`advisor/unlock.py` 的 `KEY_MODULE`/`build_key`/`PUBLIC_KEY`（本文件只核对密钥加载）；`tools/build_release.py` 的 `smoke`/`smoke_problems`、`STATE_KEY_FILE`。
- **什么时候要跑**：改了 `diagnostics.state_key_check`/`self_check`，改了 `advisor/unlock.py` 里加载密钥的做法，改了 `tools/build_release.py` 里 `smoke`/`smoke_problems` 对密钥那一项的判断，或改了打包时生成密钥模块的做法。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_selfcheck_key`（`.tmp/test-times.json` 里还没有它的记录；自检里只有「识别合成图」一项建一次识别引擎；不是慢测试、也不是实机测试）。
- **需要什么**：没有特别要求——发行版用 `diagnostics.is_frozen` 换成的假值模拟，密钥模块用 `sys.modules` 里的假模块模拟，文字识别那一项换成出错的、窗口截取组件换成假的；不编译、不打包、不启动程序。
- **输出怎么看**：跳过和失败信息里都不会出现密钥；`test_self_check_item_has_no_key` 会检查内存里的结果、写到 `.tmp/selfcheck.json`（沙箱里）的、以及标准输出的三处，都不含密钥的任何写法（十六进制大小写、Base64、`bytes` 的 repr、按 latin-1 当文字）。`.tmp/selfcheck.json` 写在测试沙箱里，跑完删掉。
- **出错时一般是哪里不对**：
  - `StateKeyCheckTest.test_frozen` 失败 → `state_key_check` 的判定或说明改坏（该通过或该不过的情形对不上），或说明里带出了密钥。
  - `test_dev` 失败 → 开发版的分支（不打包时写「开发版不带打包密钥」）改坏。
  - `test_self_check_item_has_no_key`／`test_self_check_item_not_critical` 失败 → 自检里这一项的名字、是否关键项、是否写密钥改坏（`diagnostics.self_check`），或写文件、打输出时带了密钥。
  - `SmokeStateKeyTest.test_required_when_key_compiled_in` 失败 → `smoke_problems` 对「密钥这一项」的判断改坏（该报或不该报、以及 `state_key_expected`）。
  - `test_smoke_records_whether_key_compiled_in` 失败 → `smoke` 记 `state_key_expected` 的依据（按有没有加 `--public-key`）改坏，或结果、日志里带出了密钥。
  - 有意改动：`PUBLIC_KEY` 是公开常量，测试用它构造「正好是公开密钥」的情形；改了密钥机制时要同步改这里的期望。

## test_import_layers.py：测 advisor 的分层没被导入关系打破（底层的模块不反过来依赖上层的界面模块）

- **测试观点**
  - `ScannerTest`：扫描器本身认不认得各种写法的向上导入——写在函数或方法里的延迟导入、相对导入、绝对导入、`from 包 import 子模块`，几种都要认得出来；只导入底层模块的源码不报；Qt 的导入也要认得出来，`ctypes` 之类不算。
  - `ImportLayersTest`：界面基础设施一组（主题、装饰、字形、控件、窗口基类、Win32 声明、错误记录）不导入各窗口、悬浮窗、缩略图子进程入口和筹码方案的那几个窗口；`advisor/win32.py` 不导入 Qt。
  - `LayerChainTest`：整份 advisor 按「资料/路径/写盘/错误记录 → 推荐/识别 → 记账 → 界面」分层，下层（包括函数里的延迟导入）不导入上层；`advisor` 下每个源码文件都登记了层，新加的模块没登记就报出来；还单独核对 `chain_level` 对几个模块名算出来的层数。
- **检证对象**：`advisor/` 下全部源码的导入语句（用 `ast` 解析，不导入这些模块）；扫描与判层用的是本文件自己的 `imported_modules`、`chain_level`、`module_of`，以及 `LAYERS`、`FORBIDDEN`、`CHAIN` 三张表。
- **什么时候要跑**：在 `advisor/` 下加新模块（要登记进 `CHAIN`，否则 `test_every_module_placed` 报出来）；改任意 `advisor` 模块之间的导入关系，尤其是写在函数里的延迟导入；动底层模块（主题、控件、窗口基类、错误记录、`win32.py`）；把某个窗口模块改名或拆成包（`FORBIDDEN`、`FORBIDDEN_PREFIX` 要跟着改）。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_import_layers`（上次约 1.2 秒；不是慢测试、实机测试）
- **需要什么**：没有特别要求（只读源码、几秒内跑完）。
- **输出怎么看**：失败时断言里会直接列出「哪个文件（第几层）导入了哪个上层模块（第几层）」，或列出没登记的模块路径，一眼能看出是哪一处接错了；没有别的输出、不写临时文件。
- **出错时一般是哪里不对**
  - `ScannerTest.test_upward_imports_found`、`test_low_imports_pass`、`test_qt_found` 失败 → 扫描函数（`imported_modules`）漏认或误认某种写法，先把扫描器修好，否则后面的守卫会悄悄放过。
  - `ImportLayersTest.test_low_layers_do_not_import_windows` 失败 → 底层的某个模块（含函数里的延迟导入）导入了窗口/悬浮窗/方案窗口，把导入环接回来了。
  - `ImportLayersTest.test_win32_does_not_import_qt` 失败 → `advisor/win32.py` 里引入了 Qt，界面库没起来时就不好用了。
  - `LayerChainTest.test_levels` 失败 → `CHAIN` 表或判层函数改了，和预期对不上。
  - `LayerChainTest.test_every_module_placed` 失败 → `advisor/` 下新加的模块没登记进 `CHAIN`。
  - `LayerChainTest.test_lower_layers_do_not_import_upper` 失败 → 下层模块导入了上层（多半是新写的延迟导入），要么换个写法、要么把这层关系写清楚。
