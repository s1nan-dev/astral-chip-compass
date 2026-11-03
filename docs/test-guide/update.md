# 测试指南 · 软件内更新

这是[《测试指南》](../测试指南.md)第八节的一章：每个测试文件一节，每节的结构见那里第八节的开头。


这一章管软件内更新：查有没有新版本、下载并备好（有文件清单时只下变了的文件，走不通改下整包）、换版本程序、悬浮窗和设置窗口里更新的那几处，以及发版时往下载镜像传发行包的上传脚本。

七个文件各管一段：

- 查更新、下整包、解压备好（`advisor/update.py` 的前一半）→ `test_update.py`；
- 下载并备好（`advisor/update.py` 的 `obtain`：先只下变了的文件，走不通改下整包）→ `test_update_obtain.py`；
- 文件清单的生成和核对、按字节段从整包里取出变了的文件拼成新版本（`advisor/update_files.py`）→ `test_update_files.py`；
- 换版本程序（`advisor/update_apply.py`）→ `test_update_apply.py`；
- 界面这一边：控制模块、提示行、设置窗口里那一行、启动时交给换版本程序（`advisor/ui/overlay_parts/updates.py` 等）→ `test_update_ui.py`；
- 发版时的上传脚本（`tools/upload_r2.py`）→ `test_upload_r2.py`；
- 从打包、准备上传一直走到程序里下好，两边都用真的代码 → `test_update_flow.py`。

七个文件都不联网：要联网的地方用本机回环地址上自己起的假服务器（不经代理）或假的联网会话；上传脚本的令牌一律是测试里造的假值。更新怎么工作、新旧版本之间要固定下来的约定见软件维护说明「软件内更新」。

## test_update.py：测查更新和下载的前一半（advisor/update.py）——版本号比较、版本文件和 GitHub 回话的核对、两边怎么认、问哪几边、下整包、解压备好、下载前的检查

- **测试观点**（按测试类）：
  - 版本号（`VersionTest`）：按数字逐段比（`1.0.10` 比 `1.0.9` 新），段数不同时短的补 0；带 `v`、带后缀、全角数字、空的这类认不出的不算新；同一个版本的判法；转成程序文件版本号的四段（第四段是 0）。
  - 下载镜像的版本文件（`MirrorFormatTest`）：`latest.json` 必填的几项缺了、类型不对、格式号不对、大小超出上下限（1 MiB～1 GiB，和上传脚本同一对数）、SHA-256 写法不对、压缩包路径和版本号对不上、发布页不在源代码仓库的发布页下面（含 `..`、转义的点段、反斜杠这类会落到别处的写法）时整份不认；改动说明（`notes`）、资料同步日期（`data_date`）不对时当没有，别的照认；文件清单的三项有一项不对时这一版当没有清单、别的照认，并写明哪里不对。
  - GitHub 的回话（`GitHubFormatTest`）：照 GitHub 文档里 `releases/latest` 的格式造的 1.0.0 样本（`GITHUB_SAMPLE`，日期、编号、校验值都是编的）认得出；tag 不是 `v<三段版本号>`、发布页和附件地址不在这个仓库下面、附件名和版本号对不上、大小不对时整份不认；附件的 `digest` 认不出时当没有校验值；清单附件 `ACC-<版本号>.files.json` 用不上（没有 digest、大小不对、地址不对）时这一版当没有清单、压缩包照认。
  - 两边怎么认（`CheckTest`、`CheckManifestTest`）：两边一致时下载地址先镜像后 GitHub；版本号、大小、SHA-256 有一样对不上时只认 GitHub 那一版、写一条错误记录；只拿到一边时认那一边（连不上、404 不记错误记录，内容不对的记一条）；两边都没拿到时，两边的原因按镜像在前、GitHub 在后写成一整句；GitHub 的附件没有校验值时，只在镜像的版本号和大小都一样时借用镜像的 SHA-256，否则这一版不认（`NO_DIGEST`）；比当前版本新、又不是跳过的版本才算有新版本；请求只带 User-Agent（工具名、版本号、源代码地址），GitHub 另带它要的两个头；停止时抛 `winhttp.Stopped`，不当成没拿到。清单照「认清单」那一套：能拿到 GitHub 的那一版时以它的清单附件为准，镜像上的一样时清单地址先镜像后 GitHub，GitHub 没拿到时用镜像的；压缩包怎么认不受清单影响。
  - 问哪几边（`SourcesTest`、`ForkCheckTest`）：由源代码地址推出（`sources`）。只有和 `MIRROR_SOURCE` 一字不差时才问下载镜像；GitHub 只认 `https://github.com/<owner>/<repo>` 这一种写法，大小写照原样；不在 GitHub 上的、末尾多了「/」或「.git」的、拼进接口地址会跑到别处的，不问 GitHub，也不用镜像时两边都不问、不建联网会话（`NO_SOURCE`）。复刻的版本只问 GitHub，下载镜像一次都不请求，认下的版本里没有镜像上的地址，没拿到时原因只写 GitHub。
  - 真的联网会话（`CheckLiveTest`）：用真的 `winhttp.Session` 问本机的假服务器，请求头真的发出去了，回话按块收齐，版本文件超过上限（`LATEST_LIMIT`）不收。
  - 下整包（`DownloadTest`）：按认下的地址先后下到 `update\ACC-X.Y.Z.zip`，边下边写 `.part`、边算 SHA-256；第一个地址回 404 或内容不对时换下一个，都不行时写明每个地址为什么不行、`update` 里不留东西；跟重定向；进度；中途、开始以前、收齐的那一刻停止都作废、不换地址；上次下好的同一个包直接用，在却不对的重新下，上次留下的 `.part` 覆盖；`.part` 写不进时不换地址、不发请求，下到一半磁盘满了说空间不够；不给会话时自己建、用完关掉；错误里的来源名（下载镜像、GitHub、主机名）。
  - 解压备好（`PrepareTest`）：核对、解到 `update\new.part`、改名成 `update\new`、删压缩包，最后才写 `ready.json`；名字不对（`..`、绝对路径、盘符、`:`、Windows 上不能当文件名的、不分大小写重名、文件和文件夹同名）、缺 `app\ACC.exe` 或 `拾星助手.exe`、项数和解开后的大小超上限（`MAX_FILES`、`MAX_UNPACKED`）、加密的和不认的压缩方式、坏包、`ACC.exe` 的文件版本号和要换的版本不一样时都不备好；出错时收拾干净，之前备好的那一份作废；`update` 以外的压缩包不删。
  - 下载前的检查（`PreflightTest`）：程序文件夹写不进、空闲空间不到 `FREE_SPACE`（800 MB）时给出原因；`free_space` 在真的文件夹上取得到数。
  - 程序文件的版本号（`ExeVersionTest`）：读本机 Python 的 `python.exe` 的版本资源试真的；没有版本资源、文件不在时为空；`version.dll` 只从 System32 加载。
  - 导入和对外的名字（`ImportTest`）：导入它不带进界面库和识别用的库（在子进程里核对）；它自己不看是不是发行版（开发版不查、不下、不换由调用的一方先看）；对外的名字、几个地址常量、`Release` 和 `CheckResult` 的字段和先后。
- **检证对象**：`advisor/update.py` 的 `is_newer`、`sources`、`check`、`preflight`、`free_space`、`download`、`prepare`、`exe_version`，以及它们用到的 `_mirror_release`、`_github_release`、`_decide`、`_files`、`_fetch_json`、`_fetch_to`、`_members`、`_fine_name`、`_unpack`、`_check_version`、`_write_ready`；常量 `MIRROR_BASE`、`MIRROR_SOURCE`、`LATEST_JSON`、`GITHUB_API`、`USE_MIRROR`、`PAGE_PREFIX`、`DOWNLOAD_PREFIX`、`USER_AGENT`、`MIN_SIZE`、`MAX_SIZE`、`LATEST_LIMIT`、`MAX_FILES`、`MAX_UNPACKED`、`FREE_SPACE`、`NO_DIGEST`、`NO_SOURCE`；下载用的 `advisor/winhttp.py` 的 `Session`。
- **什么时候要跑**：改了 `advisor/update.py` 的查、认、下整包、备好、下载前的检查，改了 `advisor/winhttp.py` 的会话，改了源代码地址（`advisor/version.py` 的 `SOURCE_URL`）。改了 `obtain`（只下变了的文件那条路）另跑 `test_update_obtain`；改了 `latest.json` 的写法另跑 `test_upload_r2` 和 `test_update_flow`，改了 `ready.json` 的写法、`update\new` 的位置另跑 `test_update_apply` 和 `test_update_flow`。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_update`（上次约 3 秒）。
- **需要什么**：要在 Windows 上跑：`CheckLiveTest`、`DownloadTest` 用真的 WinHTTP 问 127.0.0.1 上自己起的 http.server（不经代理），`ExeVersionTest` 读本机 Python 的 `python.exe`。别的查和认用假的会话（`FakeSession`：按地址给回话或抛错）；备好时换掉 `exe_version`（造的压缩包里的 `ACC.exe` 不是真的程序）。不联网。
- **输出怎么看**：同总则；错误记录指到每个用例自己的临时文件，用例里核对写了几条、标题是什么。
- **出错时一般是哪里不对**：
  - `VersionTest` 失败 → `is_newer`、`_same_version`、`_four` 的比法改了。
  - `MirrorFormatTest`、`GitHubFormatTest` 失败 → 认版本文件（`_mirror_release`）、认 GitHub 回话（`_github_release`）的规矩改了。`latest.json` 的写法是新旧版本之间要固定下来的约定（软件维护说明「软件内更新」的「新旧版本之间的约定」），要改时和上传脚本写它的地方（`tools/upload_r2.py` 的 `latest_manifest`）一起改；GitHub 改了回话的格式时，先照它的文档改样本 `GITHUB_SAMPLE`。
  - `CheckTest`、`CheckManifestTest` 失败 → `check` 认哪一版（`_decide`）、认清单（`_files`）的规矩改了，或错误记录的标题、`problem` 那一整句改了（界面照原样套进「检查更新失败：……」显示）。
  - `SourcesTest`、`ForkCheckTest` 失败 → `sources` 推出问哪几边的规矩改了，或源代码地址、`MIRROR_SOURCE` 改了；复刻以后改源代码地址时要跟着改的测试见软件维护说明「复刻以后发布自己的版本」。
  - `CheckLiveTest`、`DownloadTest` 失败 → `_fetch_json`、`download`、`_fetch_to` 或 `advisor/winhttp.py` 的会话（请求头、按块收、读的上限、重定向、停止）改了。
  - `PrepareTest` 失败 → `prepare` 的核对（`_members`、`_fine_name`、上限）或先后改了；`ready.json` 的写法和 `update\new` 的位置是换版本程序认的约定，要和 `advisor/update_apply.py` 一起改。
  - `PreflightTest` 失败 → `preflight`、`free_space`、`FREE_SPACE` 改了。
  - `ExeVersionTest` 失败 → `exe_version` 读版本资源的做法改了，或加载 `version.dll` 时不再只找 System32。
  - `ImportTest` 失败 → `advisor/update.py` 新导入了界面库或识别用的库、开始自己看 `paths.is_frozen`，或者对外的名字、地址常量、`Release` 和 `CheckResult` 的字段改了（`test_update_ui` 的替身照这些字段写，要一起改）。

## test_update_obtain.py：测下载并备好（advisor/update.py 的 obtain）——有文件清单时先只下变了的文件，走不通改下整包

- **测试观点**（按测试类）：
  - 走通只下变了的文件（`DeltaTest`）：对照本机，没变的复制、变了的按字节段从整包里取，不下整包；本机全都用得上时一个字节都不取；写 `ready.json` 之前先把拼好的文件刷进硬盘；第一个地址不认 Range、清单下不到或对不上时换下一个地址；取的时候按收到的字节报进度；地址跳转以后照样按段取；替换之前备好的那一份；不给会话时自己建、用完关掉。做完以后和 `download` + `prepare` 一样：`update\new` 和整包解开的一字不差，最后写 `ready.json`。
  - 走不通改下整包（`FallbackTest`）：服务器不认 Range、Content-Range 写错、回得比要的长、按段取到的内容不对、清单拿不到、要取的超过整包的六成（`DELTA_RATIO`，不值得）、最后核对没通过、出了没想到的错：删掉 `new.part`，写一条错误记录（原因里去掉用户文件夹的路径），改下整包；界面先后收到 phase `"delta"`（走到了的话）和 `"full"`。
  - 没有文件清单（`NoManifestTest`）：直接下整包，出错的说法同 `download`、`prepare`。
  - 停止（`StopTest`）：中途、对照本机文件时、开始以前停止都抛 `winhttp.Stopped`，不留 `new.part` 和 `.part`，不改下整包，不写错误记录。
  - 进度（`MeterTest`）：只下变了的文件这条路第一次报进度时先报 phase `"delta"`；中途按收到的字节报，交出去的只增不减、不超过总数；要取的是 0 时也报一次 phase 和进度。
  - 接口（`InterfaceTest`）：`obtain` 的参数名、先后和「后四个只能按名字传、默认为空」不变，界面照这个接口调用。
- **检证对象**：`advisor/update.py` 的 `obtain` 和它用到的 `_delta`、`_fetch_manifest`、`_range`、`_flush_tree`、`_DeltaMeter`、`DELTA_RATIO`（整包那条路照样走 `download`、`prepare`）；拼新版本用的 `advisor/update_files.py` 的 `assemble`。
- **什么时候要跑**：改了 `obtain` 或只下变了的文件那条路（取清单、按段取、改下整包的条件、进度和 phase），改了 `advisor/update_files.py` 的 `assemble`、`parse_manifest`，改了 `advisor/winhttp.py` 的会话。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_update_obtain`（上次约 5 秒）。
- **需要什么**：要在 Windows 上跑：用真的 WinHTTP 会话问 127.0.0.1 上自己起的假服务器（不经代理）。标准库的 http.server 不认 Range，假服务器自己处理「bytes=起-止」，还能按路径故意不认 Range、Content-Range 写错、回得比要的长、按段取时给改过的包、跳转。造的发行包照发行版的样子（顶层 `AstralChipCompass/`、时间戳写死、名字有中文、有 deflate 也有存储方式、带一个空目录和一个空文件），清单由 `update_files.build_manifest` 生成；「本机」是临时文件夹里按旧版本放的文件（另有新版本里没有的旧文件和用户数据）。`exe_version` 换成假的。不联网。
- **输出怎么看**：同总则；假服务器记下每个请求的路径和 Range 头，用例按它核对取了哪些、取了几次。
- **出错时一般是哪里不对**：
  - `DeltaTest` 失败 → 先跑 `test_update_files`：它也失败时是 `assemble` 本身的问题；它通过时看 `_delta` 怎么取清单、换地址、刷盘、交给 `_finish`。
  - `FallbackTest` 失败 → 走不通的认法（`_range` 在读正文之前核对状态码、Content-Range 和长度）、`DELTA_RATIO`，或改下整包之前的收拾（删 `new.part`、写错误记录、phase 的先后）改了。
  - `NoManifestTest` 失败 → 没有清单时 `obtain` 不再和 `download` + `prepare` 一样了。
  - `StopTest` 失败 → 停止以后多做了事（留下文件、改下整包、写了错误记录）。
  - `MeterTest` 失败 → `_DeltaMeter` 报进度的口径改了；界面按 phase 和进度选那一句，要和 `test_update_ui` 一起看。
  - `InterfaceTest` 失败 → `obtain` 的参数改了：界面（`advisor/ui/overlay_parts/updates.py`）和 `test_update_ui` 的替身 `FakeNet.obtain` 照这个接口写，要一起改。

## test_update_files.py：测按文件更新（advisor/update_files.py）——文件清单的生成和核对、对照本机只取变了的文件、按字节段从整包里取出来拼成新版本

- **测试观点**（按测试类）：
  - 生成清单（`BuildManifestTest`）：中文名、存储和压缩两种方式、空文件、空目录都记对；写法固定（UTF-8、中文不转义、键排序、LF 结尾），同一个包生成两次字节一样；下面有文件的目录项不记，只记空目录；每个文件后面跟数据描述符的包照样生成；不在顶层文件夹下面、缺主程序、bzip2 压缩、不分大小写重名、版本号写法不对时不生成。
  - 读清单（`ParseManifestTest`）：认下的 SHA-256 写成大写也认（清单里的必须是小写），不认识的字段不看；格式号、缺字段、类型不对、路径不对（`..`、绝对路径、盘符、冒号、反斜杠、空段、Windows 上不能当文件名的）、不分大小写重名、超上限（文件数、空目录数、解压后合计、清单本身的大小，正好等于上限的认）、缺主程序、版本号和整包的文件名、大小、SHA-256 和认下的那一版不一样、根本不是清单时都不认。
  - 拼新版本（`AssembleTest`）：本机全都一样时全部复制、一个字节都不取、中央目录也不读；部分不一样（大小变了、大小一样内容变了、本机没有）时只取这几个，请求次数、取的字节在范围内；要取的超过整包的六成时不值得，返回 False、不建 `dest`；放开比例时全部从整包里取，和整包解压的一字不差；`dest` 是空文件夹可以，不是空的不拼；清单对象不是 `parse_manifest` 造的、路径不对时一个文件都不写。
  - 几种包的写法（`DataDescriptorTest`、`EntryCommentTest`、`ArchiveCommentTest`、`BigFileTest`）：每个文件后面跟数据描述符（本地文件头里的大小是 0）、中央目录里每个文件带注释（比按清单估的大，补取一次）、整包末尾带注释（尾部记录不在最后，补取一次）都拼得出；大文件分段取，每次请求不超过一段，不整个读进内存。
  - 停止（`StopTest`）：取到一半、开始以前、对照本机文件时停止都抛 `Stopped`，之后不再取。
  - 走不通（`FailTest`）：服务器不认 Range、回得比要的长、连不上、回的字节被改过（存储方式和 deflate 的文件、本地文件头）、解压出来比清单写的大（超过就停，写出去的不超过清单的大小）、清单上有中央目录里没有、对照完到复制之前本机文件被改了或变长了，都抛 `DeltaError`；`dest` 以外什么都没多，本机文件一个没动。
  - 不可信的包（`UntrustedZipTest`）：整包的中央目录、本地文件头都当不可信的数据：手写的包造出各种毛病（条目互相重叠、扩展字段越界、本地文件头和中央目录说的不一样、本地文件头的位置不在中央目录前面、同名两次、大小和清单不一样、压缩数据流后面还有多的或提前断了、中央目录超上限、要取的字节不在整包里、拼的时候 `dest` 里多出了别的文件、`dest` 里的文件夹成了指向别处的目录链接），都抛 `DeltaError`，原因对得上，`dest` 以外什么都没写。
- **检证对象**：`advisor/update_files.py` 的 `build_manifest`、`parse_manifest`、`assemble`（`_Assembly`、`_ZipTail`、`_Sink`）、`DeltaError`、`FileEntry`、`Manifest`、`zip_name`、`manifest_name`，常量 `MANIFEST_FORMAT`、`MAX_FILES`、`MAX_TOTAL`、`MAX_MANIFEST`、`SEGMENT`、`MAX_CENTRAL`。
- **什么时候要跑**：改了 `advisor/update_files.py`。清单的写法是新旧版本之间要固定下来的约定（打包时生成、发版时传上去、程序照它只下变了的文件），改了写法另跑 `test_release`（打包时生成、核对清单）和 `test_update_flow`；改了 `assemble` 的接口另跑 `test_update_obtain`。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_update_files`（上次约 7 秒）。
- **需要什么**：本机 127.0.0.1 上起一个认 Range 的小 http 服务器提供造出来的发行包（能故意不认 Range、回得比要的长，也能随时把包换成改过的）。测试用的取法 `RangeFetch` 用 urllib 带 Range 头去取，核对回 206、Content-Range 的起止和总长，同时记下每次请求的起止；它只是测试替身，写法就是调用方的取法要满足的那几条，模块本身不联网。目录链接那一条只在 Windows 上跑。不联网。
- **输出怎么看**：同总则；「本机」是临时文件夹里按旧版本放的文件，`dest` 照实际的位置放在它的 `update\new.part`。
- **出错时一般是哪里不对**：
  - `BuildManifestTest`、`ParseManifestTest` 失败 → 清单的写法、字段或上限改了。打包脚本写的（`tools/build_release.py` 的 `make_files_manifest`）和程序认的要一起改，也要和软件维护说明「新旧版本之间的约定」对得上。
  - `AssembleTest` 失败 → 对照本机的口径（大小、SHA-256）、不值得的比例，或复制和取的分法改了。
  - `DataDescriptorTest`、`EntryCommentTest`、`ArchiveCommentTest`、`BigFileTest` 失败 → 读中央目录的做法（`_ZipTail`：把整包的末尾当成只读文件交给 zipfile，读到更前面时补取差的那一截）、按中央目录定每个文件的范围，或分段（`SEGMENT`）改了。
  - `FailTest`、`UntrustedZipTest` 失败 → 少核对了一项（范围、名字、压缩方式、大小、SHA-256），或写出去的超出了 `dest`。这两类守着「从网上取来的字节一律不信」，有意放宽某一项之前先想清楚坏包会怎么利用它。

## test_update_apply.py：测换版本（advisor/update_apply.py）——下好的新版本把旧版本换掉，起不来就退回

- **测试观点**（按测试类）：
  - 换版本程序（`ApplyTest`，`apply_main`）：
    - 换好了的几种：悬浮窗出来了（事件触发）、等满时间新版本还在运行、新版本以退出码 0 结束而防多开的锁在别的拾星助手手里；结果写 `ok`。
    - 什么都不改的几种：换版本程序不在 `<根>\update\new\app` 里、命令行参数不对、`ready.json` 不在或不对、版本不是自己的、旧版本的程序不在（以 `EXIT_REFUSED` 退出），另一个换版本程序在换（`EXIT_BUSY`）；旧进程一直不退出时结果 `not_applied`、别的不改、不起程序。
    - 换不上就退回原样（`not_applied`，起旧版本）：复制出错、两次改名改不成（一时被占着时隔一会儿再试）、改名报的不是被占着；之前留下的有用户数据的 `app.old` 改不了名。
    - 起不来就退回（`rolled_back`，起旧版本）：新版本在悬浮窗出来之前就退出、起不来、换过来以后出了没想到的错；用户数据（连同新版本运行时写下的）搬回 `app`，`app.failed` 里只留新版本的程序，根目录两个文件换回旧的，原因按怎么结束的写。
    - 退不回时：新版本的文件夹改不了名就留着新版本（用户数据都在里面），`app.old` 改不回就把新版本改回 `app`；两个都改不回 `app` 时什么都不删，结果写明两个版本在哪、不起程序；最后一道再试时改得回的照样收拾好（旧版本回到 `app`，或者 `app.old` 怎么也改不回时把复制出来的新版本改成 `app`）。
    - 别的：一开始先写一份「没做完」的结果（中途被结束时它就是结果）；最后起程序经根目录的启动程序，它不在、起不来时直接起 `app\ACC.exe`；`update\new\app` 里的用户数据不复制；之前留下的 `app.next`、`app.old`、根目录的旧文件怎么处理；没换上的次数怎么记；当前目录在 `app` 里时先换到自己的文件夹。
  - 要不要换（`PendingTest`，`pending`）：备好了、比当前版本新、新版本的程序在时交出 `ready.json` 的内容；没换上的结果还没读走、次数到了上限（没换上以后又下好了一遍的除外）、换版本程序正在换时不交；查的时候什么都不删、不改。
  - 起换版本程序（`LaunchTest`，`launch`）：起的是 `update\new\app\ACC.exe --apply-update <旧进程号>`，它回话（接手了）才为 True；核对没通过、出错退出、一直不回话、起不来记一次没换上，另一个在换时不记；没换上以后读走结果才再起，到 2 次（`MAX_FAILED`）不再起，重新下好一遍以后又能起。
  - 读结果（`TakeResultTest`，`take_result`）：同一个结果只交一次；写它的换版本程序还在运行时那一份「没做完」不交；换的就是正在运行的这个版本的「没做完」不交（由收尾补完）；标记不上时这次不交、下次再交。
  - 收尾（`AfterStartTest`，`after_start`）：换好了以后删根目录的 `.old`、`.new` 和 `app.next`、`app.old`，`app.failed` 只留最近的一份，换版本的记录接到 `app\logs\update.log`（有上限），最后删 `update`；换版本程序还没退出、结果还没读过、`app.old` 里还有用户数据、`update` 里有别的下载时不删那一部分；没换上、还能再换时留着 `update`；根目录的正式文件不在时把 `.old` 改回去；两次改名以后中途退出的补完（搬用户数据、不覆盖新版本已经写下的，结果改成 `ok`）；删不掉的留着结果下次再收；只读的去掉只读再删；链接只删链接本身、不跟进去；别的名字一概不碰。
  - 开发版（`DevTest`）：对外的几个函数什么都不做、不起任何程序。
  - 删除范围（`SafetyTest`）：能删、能另起名的只有 `app.next`、`app.old`、`app.failed` 和它们带时间的另起名；不在根下这一层、名字不对的一律报错，什么都不删；「还有没有用户数据」的判法。
  - 真进程（`RealProcessTest`）：当新版本、旧版本、换版本程序的是跑一小段脚本的 Python，核对命名事件、等进程、起进程那一套真的通：换好了、触发事件之前退出时退回、`launch` 的回话、写结果的换版本程序在不在、读程序文件的版本资源。
  - 名字（`LayoutTest`、`NamesTest`）：搭的用户数据覆盖 `paths.USER_DATA_NAMES` 的每一项；和防多开、启动程序（`packaging/launcher.c`）、打包脚本约好的名字相同，退出码不和启动程序、打包脚本认的那几个撞；导入它、跑入口的换版本参数都不带进界面库和识别库（在子进程里核对）。
- **检证对象**：`advisor/update_apply.py` 的 `apply_main`（`_Apply`）、`pending`、`launch`、`take_result`、`after_start`，删、另起名之前的核对（`_slot_check`、`_slots`、`_data_left`），和 Windows 打交道的 `_Win32`、`_start`、`_wait_ready`、`_file_version`，常量 `APPLY_ARG`、`MAX_FAILED`、`EXIT_REFUSED`、`EXIT_BUSY`、`READY_ENV`、`INSTANCE_MUTEX`、`ROOT_FILES`；`advisor/paths.py` 的 `USER_DATA_NAMES`；入口 `packaging/chip_advisor.py` 接换版本参数的那一段。
- **什么时候要跑**：改了 `advisor/update_apply.py`；改了 `paths.USER_DATA_NAMES`（换版本照它搬用户数据）；改了防多开的锁、启动程序或打包脚本里和它约好的名字、退出码、环境变量。改了换版本程序对外的约定（命令行、`result.json`、`apply.log`、「已经接手」的事件）另跑 `test_release_rehearse`（打包时拿刚打好的包演练一次换版本）；改了 `pending`、`launch`、`take_result` 的接口另跑 `test_update_ui`。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_update_apply`（上次约 16 秒，是这一章最慢的：`RealProcessTest` 真的起进程）。
- **需要什么**：在测试沙箱的临时文件夹里搭发行版文件夹的样子（`Layout`：根目录的启动程序和使用说明、`app` 里的旧版本和每一种用户数据、`update\new` 里的新版本、`ready.json`），程序文件的内容写成「old program」「new program」，按内容认是哪个版本；起进程、等事件、等旧进程这几个小函数换成替身（`Fake`），等待的时长调小。`RealProcessTest` 和目录链接那一条只在 Windows 上跑。不联网、不碰真的发行版文件夹。
- **输出怎么看**：同总则；临时文件夹里另放一个 `outside` 文件夹，每个用例结束时核对它没被碰（前后的文件快照一样）。
- **出错时一般是哪里不对**：
  - `ApplyTest` 某项失败 → `_Apply` 的步骤（等旧进程、复制成 `app.next`、两次改名、搬用户数据、换根目录的两个文件、起新版本、退回、最后一道）或结果的写法改了；有意改步骤时，软件维护说明「软件内更新」的「全过程」一起改。
  - `PendingTest`、`LaunchTest`、`TakeResultTest` 失败 → 什么时候交出备好的版本、什么时候起换版本程序、没换上的次数怎么记（`MAX_FAILED`）改了；界面按这几个函数的结果提示，另跑 `test_update_ui`。
  - `AfterStartTest` 失败 → 收尾删哪些、什么时候不删、记录的上限（`KEPT_LOG_MAX`）改了。
  - `SafetyTest` 失败 → 能删、能另起名的名字范围变宽了：先确认不会删到用户自己放在发行版文件夹里的东西。
  - `RealProcessTest` 失败 → `_Win32` 里调 Windows 的那几处（命名事件、等进程、起进程、读版本资源）改了，或这台电脑上 Python 起子进程受限。
  - `LayoutTest.test_user_files_cover_every_name` 失败 → `paths.USER_DATA_NAMES` 加了新的一项：这里搭的用户数据跟着加上。
  - `NamesTest` 失败 → 和别处约好的名字、退出码改了一边没改另一边：防多开的锁（`advisor/ui/single_instance.py`）、等悬浮窗的环境变量（`advisor/ui/startup.py`）、启动程序（`packaging/launcher.c`）、打包脚本里的程序名和退出码（`tools/build_release.py`）要和 `advisor/update_apply.py` 一起改。

## test_update_ui.py：测软件内更新在界面这一边——控制模块、悬浮窗上的提示行、设置窗口里「启动时自动检查更新」那一行、启动时交给换版本程序

- **测试观点**（按测试类）：
  - 设置文件里的三项（`SettingsFieldsTest`）：自动检查的开关、上次检查的日期、跳过的版本；默认开着自动检查、没查过、没跳过；旧设置文件没有这三项时照默认、不记警告；存了再读一样；三项都跨对局沿用；类型不对的按默认。
  - 文字（`WordingTest`）：出错的那一句按种类选、原因去掉末尾的句号、没有原因时写「原因不明」；大小按整数 MB 写（至少 1），有文件清单时写成上限；下载中那一句按下到哪一步选（比对本机的文件不写大小，只下变了的部分带 x / y MB，要下载的是 0 时写用本机的文件拼出新版本、不写「0 / 0 MB」）。
  - 提示行（`RowsTest`，`update_rows`）：各种状态排出的提示行的文字、级别、按钮和动作：有新版本（四个按钮，「更新」是主按钮；只能手动下载时换成「手动下载」）、下载中的几步（都带「停止」）、解压核对中、下好了、已经是最新、出错（⚠ 橙色）、刚换上新版本；都不让边框变色。
  - 控制模块（`DevTest`、`AutoCheckTest`、`ManualCheckTest`、`DownloadTest`、`RestartTest`、`ResultTest`、`OpenTest`，`UpdateControl`）：
    - 开发版不查、不下、不换，「检查更新」只说一句开发版不检查更新。
    - 自动检查：一天最多一次（日期往回调了也算没查过）、关了不查、带上跳过的版本、连不上和程序出错都不出声（程序出错照样记进错误记录）、发行版里缺了查和下载的模块时手动检查说一句。
    - 手动检查：跳过的版本照样提示，已经是最新、两边都连不上、源代码地址推不出查更新的地址（`NO_SOURCE`）、一版都认不下（`NO_DIGEST`）、程序出错各说一句；检查中、下载中、下好了时不再查；「跳过这个版本」「以后再说」；对局中先攒着、下载在后台照常进行；查到的这一版已经备好时直接提示「重启更新」。
    - 下载（只调 `update.obtain`）：先看程序文件夹写不写得进、空间够不够；比对本机的文件、只下变了的部分、下整包、只下变了的部分没走通改下整包（进度从 0 重新数）、解压核对；进度每多 1 MB 交回一次；停止；下载和核对出错按 `obtain` 最后告诉的那一步算下载失败还是解压核对失败；等启动时的收尾做完再下载，等的上限比收尾等换版本程序的长。
    - 「重启更新」：先起换版本程序，起来了才退出；起不来时提示原因、带「手动下载」，按读到的结果说下次启动会不会再试，次数到了上限时记进跳过。
    - 启动时读上一次的结果：换上了说一句，退回的、没换上次数到了上限的记进跳过，收尾放到后台。
    - 打开的地址：只打开发布页面和下载镜像下的地址（判法和 `advisor/update.py` 的一样）；「手动下载」按认下的这一版有没有下载镜像上的地址开压缩包或发布页面；不用下载镜像的版本（复刻的改版）一律开发布页面。
  - 启动时交出去（`HandOverTest`，`hand_over_update`、`run`）：发行版里有下好的新版本时，查过防多开以后、起悬浮窗以前交给换版本程序，交出去了就直接返回、不起悬浮窗；没有、起不来、出错时照常启动；开发版不查，已有一个在运行时不查。
  - 悬浮窗的接线（`OverlayTest`）：按钮接到控制模块上；按钮多的排在文字下面；对局中不显示；放在标题栏下方、排在「免费软件」声明前面；设置窗口的开关和「检查更新」；退出时叫停下载、没停下记一笔；`main()` 里悬浮窗出来、启动时的几项检查做完以后才读结果、排自动检查。
  - 七套皮肤的设置窗口（`SkinRowTest`）：每套都有这一行（分页的在「关于」页），开关和「检查更新」在这一行里、看得见、不超出窗口；检查的结果代替说明显示、放得下；忙的时候「检查更新」不能点；点开关、点按钮按名字调用悬浮窗的方法。
- **检证对象**：`advisor/ui/overlay_parts/updates.py` 的 `UpdateControl`、`UpdateNotice`、`has_manifest`、`_url_under`；`advisor/ui/overlay_parts/notices.py` 的 `update_rows`；`advisor/ui/overlay_parts/wording.py` 里 `UPDATE_` 开头的文字和 `update_` 开头的几个函数；`advisor/ui/overlay_parts/app.py` 的 `UiSettings`（`update_auto`、`update_checked`、`update_skipped`）和 `main`；`advisor/ui/settings/dialog.py` 里「启动时自动检查更新」那一行（`UPDATE_TITLE`、`UPDATE_DESC`、`UPDATE_CHECK_ACTION`、`SettingsValues.update_note`）和各套皮肤的设置窗口；`advisor/ui/overlay.py` 的接线（`set_update_auto`、`check_updates`、退出）；`advisor/ui/__init__.py` 的 `hand_over_update`、`run`。
- **什么时候要跑**：改了上面这些地方；改了 `advisor/update.py`、`advisor/update_apply.py` 对外的名字、参数或结果（这里的替身照它们写）。改了更新那几句文字另跑 `test_ui_no_raw_errors`（界面文字里不出现原始的报错）；改了设置窗口那一行的文字另跑 `test_settings_window`（设置窗口各项的文字逐字核对），按钮名还写在「设置」按钮的悬停说明里，另跑 `test_project_consistency`；在 `advisor/ui/settings` 包里增删这一行用到的名字另跑 `test_settings_package`；改了某套皮肤的设置窗口排版另跑那套皮肤的测试（见「皮肤和界面控件」一章）。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_update_ui`（上次约 4 秒）。
- **需要什么**：Qt 的 offscreen 平台（不弹窗口）；查和下载（`advisor/update.py`）、换版本（`advisor/update_apply.py`）一律换成假的（`FakeNet`、`FakeApply`：对外的名字和参数照真的，记下调用、按测试给的结果回），不联网、不起进程、不碰程序文件夹；设置文件写在测试沙箱的临时文件夹里。整个模块先装好随软件带的字体；`SkinRowTest` 结束时换回默认皮肤和原来的字体。
- **输出怎么看**：同总则；错误记录指到每个用例自己的文件，用例结束时还有没核对过的就算失败。
- **出错时一般是哪里不对**：
  - `SettingsFieldsTest` 失败 → `UiSettings` 那三项的名字、默认值或校验改了（设置文件里的名字改了时旧设置文件就读不到了）。
  - `WordingTest`、`RowsTest` 失败 → 更新那几句文字、`update_rows` 排出的按钮和动作名改了；有意改文字时，这里的预期、使用说明里引用的文字一起改。
  - `AutoCheckTest`、`ManualCheckTest`、`DownloadTest`、`RestartTest`、`ResultTest` 失败 → `UpdateControl` 的状态切换改了：什么时候查、提示什么、下载的几步、出错算哪一步、「重启更新」的先后、读上一次的结果。
  - `OpenTest` 失败 → 打开地址前的核对（`updates._url_under`）和 `advisor/update.py` 的 `_url_under` 判得不一样了，或「手动下载」开哪个地址的规矩改了。这两份核对要一起改。
  - `HandOverTest` 失败 → `hand_over_update` 或 `run()` 里交给换版本程序的位置改了（要在防多开之后、起悬浮窗之前）。
  - `OverlayTest` 失败 → 悬浮窗上提示行的位置、排法、按钮的接线（`overlay.py` 里动作名到 `UpdateControl` 方法的那张表）或 `main()` 里的先后改了。
  - `SkinRowTest` 失败 → 某一套皮肤的设置窗口排版放不下这一行、少了这一行，或按钮调用的方法名（`UPDATE_CHECK_ACTION`、`set_update_auto`）改了。

## test_upload_r2.py：测发版上传脚本（tools/upload_r2.py）——签名、令牌文件、上传的顺序和中途停下

- **测试观点**（按测试类）：
  - 签名（`SigV4Test`）：照 AWS 文档里公开的 S3 签名示例（GET Object、PUT Object、带查询串的两个）逐字核对规范请求、待签字符串和签名；查询串的排序和编码、路径编码；上传请求签了哪些头（Host、`x-amz-date`、`x-amz-content-sha256`、`Content-Type`、`Cache-Control`），签的就是发出去的这几个头，区域 `auto`、服务 `s3`。
  - 令牌文件（`TokenFileTest`）：容忍 CRLF、空行、首尾空白、BOM、`#` 说明行；缺项、写法不对时只报名字和行号、不带任何值；打印令牌对象时不显示值；`endpoint` 只认 R2 的 https 地址（本机地址除外）。
  - 不泄露令牌（`RedactTest`）：令牌的值（连同 `endpoint` 的主机名、账号那一段）在输出里换成占位，不分大小写。
  - 重定向（`RedirectTest`）：不从 https 降级成明文 http；签了名的上传（PUT）遇到哪种重定向都不跟，签名不会被带到别的主机。
  - 整个上传走一遍（`UploadFlowTest`，经 `main()`）：顺序是核对 GitHub → 传发行包 → 从公开地址下回来核对 → 传文件清单 → 读回核对 → 传 `ACC-latest.zip` → 传改动说明 → 传 `latest.json` → 读回核对 → 按段取发行包的前 1 KB；每个文件的请求头、缓存设置和签名；`latest.json` 的内容（含文件清单的三项）。GitHub 对不上（压缩包、文件清单两个附件）、公开地址下回来不对、R2 回错、R2 回重定向时停在哪一步、写明哪些传上去了、哪些还没传；最后按段取时回 200、Content-Range 或长度不对只打警告、不算失败；`--dry-run` 不发上传请求；本地的东西不对、压缩包大小超出上下限（1 MiB～1 GiB，和程序认的一样）、令牌不对时一个请求都不发；按 Ctrl+C 中断时照样写 R2 的状态；任何输出（标准输出、标准错误、异常的说明）里都没有令牌的值。
  - 地址（`AddressTest`）：写死在脚本里的公开地址和 GitHub 接口，和程序查更新用的一样（`advisor/update.py` 的 `MIRROR_BASE`、`GITHUB_API`）。
- **检证对象**：`tools/upload_r2.py` 的 `sign`、`signing_key`、`canonical_query`、`canonical_headers`、`uri_encode`、`put_request`、`read_token`、`Token`、`Redactor`、`_NoDowngrade`、`Upload`（`prepare`、`check_github`、`transfer`、`verify_public`、`probe_range`、`readback`、`summary`）、`latest_manifest`、`zip_problem`、`main`，常量 `PUBLIC_BASE`、`GITHUB_API`、`STEPS`、`CACHE_RELEASE`、`CACHE_SHORT`、`CACHE_NONE`、`MIN_SIZE`、`MAX_SIZE`、`TOKEN_ENV`。
- **什么时候要跑**：改了 `tools/upload_r2.py`；改了 `latest.json` 的写法、下载镜像的地址、源代码地址（`advisor/update.py`、`advisor/version.py`）；改了打包脚本生成的压缩包和文件清单的名字。改了 `latest.json` 的写法另跑 `test_update`（程序认它）和 `test_update_flow`。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_upload_r2`（上次约 5 秒）。
- **需要什么**：不联网，不读真的令牌文件：令牌一律是测试里造的假值，环境变量 `ACC_R2_TOKEN_FILE` 在每个用例里先清掉。本机的假服务器（`FakeCloud`）同时当 R2 的上传接口（127.0.0.2，照收到的请求头重算签名来核对）、公开地址和 GitHub 的接口（127.0.0.1）；脚本发往本机以外的请求在测试里直接算失败（`UploadFlowTest.local_only`），不会真的发出去。
- **输出怎么看**：同总则；`UploadFlowTest` 经 `main()` 跑脚本，标准输出和标准错误由测试收下来核对（每一步、警告、摘要，以及里面没有令牌的值），假服务器记下收到的每个请求。
- **出错时一般是哪里不对**：
  - `SigV4Test` 失败 → 签名的算法、编码或签了哪些头改了。照 AWS 文档的示例逐字对，示例的值不要改。
  - `TokenFileTest`、`RedactTest` 失败 → 令牌文件的读法、报错的写法或占位的规矩改了；报错和输出里绝不能带令牌的值。
  - `RedirectTest`、`UploadFlowTest.test_put_redirect_not_followed` 失败 → 跟不跟重定向的规矩改了（`_NoDowngrade`）。
  - `UploadFlowTest` 别的失败 → 上传的顺序（`STEPS`）、缓存设置、`latest.json` 的内容（`latest_manifest`）、核对 GitHub 和读回的规矩、停在哪一步的写法改了。顺序是有意的：先传发行包和文件清单、核对过了，最后才换 `latest.json`，程序才查得到新版本；有意改顺序时，软件维护说明「软件内更新」的「发版时放什么」一起改。
  - `AddressTest` 失败 → 脚本里的地址和 `advisor/update.py` 里的对不上了，两边一起改。

## test_update_flow.py：测软件内更新从发版到下好走一遍，两边都用真的代码（不用替身、不联网）

- **测试观点**（按测试类）：
  - 只下变了的文件（`DeltaFlowTest`）：查到新版本（清单的三项都认出来，「更新」写成最多约多少）→ 点「更新」→ 比对本机的文件 → 只按段取变了的那几个 → 最后核对 → 下好；没点「重启更新」、下次启动再查到这一版时直接是「重启更新」，不再下载。镜像不认 Range 时换 GitHub 的地址（跳到文件服务器，Range 头照样带着）按段取；两边都不认 Range 时比对以后改下整包（记一条错误记录，不提示），照样下好；按段取的时候点「停止」：回到有新版本那一条，什么都不留下，不改下整包。
  - 没有清单（`FullFlowTest`）：加清单以前的 `latest.json`、GitHub 上也没有清单附件：下整包 → 解压核对 → 下好。
  - 复刻的版本（`ForkFlowTest`）：镜像上照样放着这一版，却一次都不请求；查、下清单、按段取都只走 GitHub，照样下好。
  - 出错的分法（`FailureFlowTest`）：`advisor/update.py` 抛的错按出错时做到哪一步算下载失败、解压核对失败，原因照它给的写；只下变了的文件那条路走不通只记错误记录，不交给界面。
  - 每一遍都核对：界面线程收到的 phase 和进度，提示行和设置窗口那一行的文字和先后，`update\new` 和整包解开的一字不差，换版本的模块认这份 `ready.json`（`update_apply.pending`），`update` 以外的没被碰，以及每台服务器收到了哪些请求。
- **检证对象**：发版这一边：`tools/build_release.py` 的 `make_zip`、`make_files_manifest`、`files_manifest_problems`（打包的自检照跑），`tools/upload_r2.py` 的 `Upload.prepare`（核对本地的发行包和清单，算好要传的几样，`latest.json` 也在里面）。程序这一边：`advisor/ui/overlay_parts/updates.py` 的 `UpdateControl`（下载线程是真的线程，结果经信号排队回到界面线程），`advisor/update.py` 的 `check`、`obtain`，`advisor/update_files.py`，`advisor/update_apply.py` 的 `pending`。
- **什么时候要跑**：改了这一章里任何一个模块之间接得上的地方：打包脚本生成的压缩包和清单、上传脚本放上去的名字和 `latest.json`、程序认的版本文件和清单、下载线程和界面之间的信号、`ready.json`。只改了一个模块的内部，先跑那个模块自己的测试。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_update_flow`（上次约 4 秒）。
- **需要什么**：装了 deflate 包（打包脚本压发行包用的 libdeflate 绑定，见 `requirements-build.txt`），没装时打包那一步报错、写明怎么装；Qt（离屏平台，等下载线程时跑界面线程的事件循环）；要在 Windows 上跑（真的 WinHTTP）。发行包整个模块只打一次，放在测试沙箱里。三台假服务器都在 127.0.0.1 上：下载镜像（`latest.json`、发行包、文件清单、改动说明）、GitHub（接口回发布的这一版，附件的地址跳到文件服务器）、文件服务器；发行包和清单都认 Range。`advisor/update.py` 里指向下载镜像、GitHub 的几个地址换成本机的（发布页照真的写）；上传脚本的令牌文件是这里造的假值，不连 R2，也不核对 GitHub。造的 `ACC.exe` 不是真的程序，`exe_version` 换成假的。
- **输出怎么看**：同总则；打包的自检没过时报「打包的自检没过」和原因；界面那一边记下每次刷新时界面上的样子和界面线程收到的 phase、进度，每台假服务器记下收到的每个请求的路径和 Range 头，用例按这些核对。
- **出错时一般是哪里不对**：
  - 打包的自检没过 → `make_zip`、`make_files_manifest` 或程序认清单的 `parse_manifest` 改了一边；先跑 `test_update_files`、`test_release`。
  - 查不到新版本、清单的三项没认出来 → 上传脚本写的 `latest.json`（`latest_manifest`）和程序认的（`_mirror_release`）对不上了；先跑 `test_upload_r2`、`test_update`。
  - 按段取、改下整包的先后不对 → `obtain` 换地址、改下整包的规矩改了；先跑 `test_update_obtain`。
  - 提示行、设置窗口那一行的先后不对 → `UpdateControl` 收 phase 和进度的做法改了；先跑 `test_update_ui`。
  - 等不到下载线程做完 → 这里等界面线程用的是 `pump_until`（QEventLoop，等的时候放开 GIL），不要换成 `tests.support.waits.wait_until`：它用的 `QTest.qWait` 等的时候不放开 GIL，下载线程跑不动。
  - `update_apply.pending` 不认 `ready.json` → `prepare`、`_finish` 写的和换版本程序认的对不上了；先跑 `test_update_apply`。
