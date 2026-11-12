# 测试指南 · 系统

这是[《测试指南》](../测试指南.md)第八节的一章：每个测试文件一节，每节的结构见那里第八节的开头。


这一章管和 Windows 打交道的那一层：写盘工具、下载、屏幕与窗口截取、本机配置、诊断包。

## test_fileio.py：统一的写盘工具

- **测试观点**（都是模拟底层写盘操作，看每一步的顺序和结果）：
  - `WriteTest`：不在的文件夹会建；先写临时文件、刷盘、再替换，不留临时文件；覆盖已有文件；`durable=False` 不刷盘；`if_changed=True` 时内容一样就不动文件、返回 False；`suffix` 换临时文件后缀；`backup=True` 保留上一版、先换目标后写备份、两步都先刷盘；旧内容是全零或空的就不拿它盖掉还能用的备份；旧内容读不了时照常写；上次剩的临时文件被覆盖；目标是文件夹时马上报错；`suffix` 是空的或 `.bak` 时报错且什么都不碰。
  - `BackupCheckTest`：`backup_check` 判断旧内容能不能留作备份——不能用时不盖掉备份、只记一条警告；能用时照常留；它报别的异常时什么都不写、照原样抛出。
  - `WriteTextTest`：`newline` 默认和 `open()` 一样（Windows 上是 `\r\n`）；写不成 UTF-8 时在动文件前就报错；各种选项照传。
  - `WriteFaultTest`：整份写入的每一步出错、或程序在任何一步被结束时，目标文件和备份至少有一份是完整的；接下来再写一次照常。
  - `SyncErrorTest`：刷盘报错不算写不进——照常替换、每个文件只记一条警告。
  - `RetryTest`：文件被别的程序占着时稍等再试，试满还不行才报错；目标是只读的、或文件夹写不进时马上报（不等）。
  - `ReadTest`：读坏时的兜底——有能用的备份就先给坏文件留一份、再用备份恢复，任何一步被结束文件名都一直在、下次照样能读到；不支持硬链接的盘上改成另写一份；另写的那份写到一半出错就删掉、算没留下；备份也读坏时同样另存；另存的名字同一秒不覆盖已有的；解析函数报程序错误时不当读坏；文件读不了时报错、不动文件。
  - `MoveAsideTest`：另存成「原名.标记-年月日-时分秒」，同一秒已有时接 -2、-3，不覆盖。
  - `AppendTest`：追加时上个半行/零字节先补一个换行；写不成 UTF-8 时不开文件、不动文件；`durable` 时刷盘；被占用重试；文件夹写不进马上报。
  - `StagedDirTest`：文件夹先写到 `.partial` 再改名；中途出错留 `.partial`；以前剩的 `.partial` 不碰、换 `-2`；正式文件夹已在时报错；改名被占用时重试。
  - `ModuleTest`：这个模块只用标准库、不导 Qt 也不导 advisor 其他模块；模块开头的提纲写到了每个对外函数。
- **检证对象**：`advisor/fileio.py` 的全部对外函数与常量——`write_bytes`、`write_text`、`write_backup`、`read_with_backup`、`move_aside`、`append_lines`、`staged_dir`、`backup_path`、`Loaded`、`RETRY_DELAYS`、`Busy`。
- **什么时候要跑**：改 `advisor/fileio.py` 任何一处；以及任何改用它的地方（设置、筹码方案、下载的识别图片、推荐日志、识别记录、诊断包）改了写盘方式时——这些地方的保证都由这里集中实现。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_fileio`（上次约 0.6 秒）。
- **需要什么**：没有特别要求，文件都写在测试沙箱的临时文件夹里；出错、被占用、程序被结束都用替身造。
- **输出怎么看**：这里的「磁盘」是模拟的——换掉模块里的 `open` 和 `os.fsync`、`os.replace`、`os.rename`、`os.link`、`os.unlink`，断言里看的是它记下的步骤顺序、是否刷盘、有没有留下临时文件；不写真的用户文件。
- **出错时一般是哪里不对**：
  - `WriteTest` 失败 → 写临时文件/刷盘/替换的步骤或顺序改了（`fileio.write_bytes`）。
  - `WriteTest.test_bad_suffix` 失败 → 后缀校验（空的、`.bak` 报 ValueError）改了。
  - `BackupCheckTest` 失败 → 旧内容能不能当备份的判断改了（`fileio._fit_for_backup`）。
  - `WriteFaultTest` 失败 → 出错/中断时「至少一份完整」的保证破了（`write_bytes` 和 `_keep_backup` 的次序）。
  - `SyncErrorTest` 失败 → 刷盘报错被当成写不进了，或警告记了不止一次（`_sync`）。
  - `RetryTest` 失败 → 重试次数/间隔改了，或「只读/文件夹马上报」的判断改了（`RETRY_DELAYS`、`_retry`、`_hopeless`）。
  - `ReadTest` 失败 → 读坏后的兜底改了：另存名字、用备份恢复、恢复被中断的保护（`read_with_backup`、`_keep_copy`、`_aside`）。
  - `AppendTest` 失败 → 补换行或编码先行的做法改了（`append_lines`）。
  - `StagedDirTest` 失败 → `.partial` 命名或改名时机改了（`staged_dir`、`_new_partial`）。
  - `ModuleTest.test_only_standard_library` 失败 → 这个模块新导入了 Qt 或 advisor 的其他模块；`test_outline_lists_every_public_function` 失败 → 加了对外函数但模块开头提纲没写。
  - 若某条断言的预期数字（例如重试间隔、分段长度）是有意改的，把断言的期望一起改。

## test_winhttp.py：下载用的 WinHTTP 客户端

- **测试观点**：
  - `BasicTest`：声明长度的正文、分块正文、请求头只发一个 User-Agent、404/500 报 HTTP 错误、跟随 301/302、不收也不带 cookie、重定向最多跟 10 次。
  - `RedirectTest`：重定向没跟、回话是 3xx 时按 HTTP 错误报（错误码就是那个 3xx），不把 3xx 的正文当图片交回。
  - `FailureTest`：声明长度没读够报「读了一半」、等回话超时和读正文超时都按超时报、发请求出错时的中文原因。
  - `PostTest`：`Session.post` 的正文真的发出去、请求头照传；任何状态码都交回、不抛 HTTP 错误；3xx 原样交回、不跟重定向；回话正文最多读 64 KiB；连接和发送、接收的超时分开设；置了停止标志抛「停止」。
  - `ResendTest`：复用的长连接被服务器关掉时换一条连接重发一次（正常关和 RST 都认），POST 重发的那一次照样带着整份正文；每条连接都失败只重发一次；置了停止标志就不重发。
  - `StopTest`：正文读到一半置停止标志抛「停止」；下载层把它转成自己的「已取消」；经 `assets.download` 时很快收手、不留文件。
  - `HandleTest`：成功、HTTP 错误、没读够、重定向失败、停止这几条路径都把连接句柄和请求句柄关掉。
  - `ParallelTest`：4 个线程各一个会话同时下载互不干扰。
  - `ProxyTest`：用哪个代理（环境变量优先、小写也认、只有 http_proxy 时 https 直连、都没有时直连、否则用注册表里的 https 项）；代理地址的各种写法化成 WinHTTP 认的「主机:端口」；用户名密码的拆法；错误码的中文；不给 agent 时会话名是 `AstralChipCompass`。
  - `ProxySessionTest`：常见的代理写法都建得起会话。
  - `BypassTest`：例外主机直连（假代理一个请求都收不到），不例外才走代理。
  - `ProxyAuthTest`：代理地址带用户名密码时发成 HTTP Basic；代理一律回 407 时按 HTTP 错误报。
  - `ProxyAuthRedirectTest`：跳转到别的主机时那一跳也带认证；代理回 407 就在同一个请求上重设凭据重发一次、不死循环；代理不认时也只重发一次；没跳转不重发；重发时附加请求头不重复；取地址失败照原来报 407；例外主机不设凭据也不重发；判例外时拿到的是带端口的主机名。
  - `ProxySetupFailureTest`：给请求设直连、设凭据失败时按网络错误报，原因里没有密码，句柄照样都关、请求没发出去。
  - `DllLoadTest`：winhttp.dll 只从 System32 加载。
  - `LiveProxyTest`：环境变量里的代理真的用上（http 地址收到完整地址的 GET、https 地址收到 CONNECT）。
  - `ReleaseExclusionTest`：打包时排掉 OpenSSL（`ssl`、`_ssl`、`_hashlib` 和那两个 DLL），标准库这两个模块只在 try 里导入 `ssl`。
- **检证对象**：`advisor/winhttp.py`（`Session`、`_once`、`_proxy_server`、`_proxy_credentials`、`_reason`、重定向策略与 `RESEND`／`MAX_REDIRECTS`）；`advisor/assets.py` 的 `_KeepAlive`、`download`；`tools/build_release.py` 的排除表。测试里的假服务器和假代理不是被测代码。
- **什么时候要跑**：改 `advisor/winhttp.py` 任何一处；改 `advisor/assets.py` 里 `_KeepAlive` 或 `download` 的网络部分；改了打包脚本里对 OpenSSL 的排除。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_winhttp`（上次约 36 秒，这一组里最慢的）。
- **需要什么**：不需要联网（只连本机回环地址上自己起的假服务），要 Windows（WinHTTP）。HTTPS 本机测不了，只在说明里注明要联网实测。
- **输出怎么看**：没有特别的连日输出；代理那几条按请求行、请求头里的 `Proxy-Authorization` 核对。`ReleaseExclusionTest` 依赖这台电脑的 Python 的 DLL 文件夹里有 OpenSSL 的两个 DLL，没有会失败。
- **出错时一般是哪里不对**：
  - `BasicTest` / `HandleTest` 失败 → 发请求、读回话、关句柄的流程改了（`winhttp.Session._once`）。
  - `RedirectTest` 失败 → 3xx 的报法改了（`_once` 里 `status >= 300` 那段）。
  - `FailureTest` / `ResendTest` 失败 → 读正文或重发的判断改了（`_read_body`、`_send_error`、`RESEND`）。
  - `StopTest` 失败 → 停止标志的检查点或「停止」到「已取消」的转换改了（`_read_body`、`assets._KeepAlive`）。
  - `ProxyTest` / `ProxySessionTest` / `BypassTest` / `ProxyAuthTest` / `ProxyAuthRedirectTest` 失败 → 代理的取用、例外判断或凭据处理改了（`_proxy_server`、`_proxy_credentials`、`_configure_proxy`、`_once` 里 407 重发）。
  - `ProxySetupFailureTest` 失败 → 设直连/设凭据失败时的报错或句柄释放改了。
  - `DllLoadTest` 失败 → winhttp.dll 的加载方式改了（`WinDLL(..., winmode=0x800)`）。
  - `ReleaseExclusionTest` 失败 → 打包脚本的排除表改了，或标准库版本变了。

## test_download_stop.py：退出程序时识别图片下载很快停下、停得干净

- **测试观点**：
  - `DownloadStopTest.test_stop_while_waiting_for_reply` / `test_stop_while_reading_body`：请求发出去对方一直不回、或正文读到一半不再发（网络卡住）时置停止标志，`download` 很快返回、没下完的算「已取消」、不留下任何文件（连半截的临时文件都没有）；下载线程是守护线程（退出程序时不等它们）；卡住的请求后来断开了也不重连重试。
  - `test_stop_between_chunks`：正文一直在慢慢地来（没卡住，只是慢）时按块读，每块之间看一次停止标志，下载线程自己很快收手，不留半截文件；同一条连接也不再开新的。
  - `test_keep_alive_reads_whole_body`：平时照常——同一条连接接着下三张、正文比一块大也按块读完、校验对得上就存好、不留临时文件。
  - `test_chunked_body`：分块传输（没有声明长度）的正文照样读完存好。
  - `test_connection_cut_mid_body`：读到一半连接断了，按网络错误重试，最后算失败、不存半截文件；这一张记进本地覆盖清单（个别图片下载失败、隔天再试，见 `test_download_fail.py`），识别图片的文件夹里只多这一个清单文件。
  - `test_connection_setup_error`：下载线程建不了会话时，每张按出错的原因算失败（以 `NO_SESSION` 开头、后接出错的原文），不当成「已取消」，不发请求、不留文件（不是哪一张图的问题，不记进本地覆盖清单）。
  - `ExitTest.test_process_exits_promptly`：在子进程里照退出程序的做法走一遍（后台线程下载、置停止标志、随即退出），进程很快结束。
- **检证对象**：`advisor/assets.py` 的 `download`、`_get_image`、`_Book`、`_Fetcher`、`_KeepAlive`、`AssetItem`、`CANCELLED`、`RETRIES`、`TIMEOUT`；`advisor/winhttp.py` 的 `Session`（停止处理）。
- **什么时候要跑**：改 `advisor/assets.py` 的 `download`／`_get_image`／`_Book`／`_Fetcher`／`_KeepAlive`（尤其是取消、守护线程、收尾时限）；改 `advisor/winhttp.py` 的 `Session` 停止处理。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_download_stop`（上次约 6.6 秒）。
- **需要什么**：不需要联网（连本机回环地址上自己起的假图片服务，走 WinHTTP 明文 http），要 Windows。
- **输出怎么看**：快慢用较宽的上限核对（置停止标志后 `download` 最晚多久返回、子进程最晚多久退出），不是精确毫秒；假服务记下收到几个请求、发出多少正文。
- **出错时一般是哪里不对**：
  - `test_stop_while_waiting_for_reply` / `test_stop_while_reading_body` 失败 → 取消时 `download` 被卡住的请求拖住、或留下了文件（`assets.download` 的收尾）。
  - 这两条里「下载线程应当还卡在请求里」失败 → 线程不是守护线程了，或提前收手（`assets.download` 里建线程那段）。
  - 「卡住的请求后来断开了又重连重试」失败 → 重试前没再检查停止标志（`assets._get_image`）。
  - `test_stop_between_chunks` 失败 → 按块读时每块之间没看停止标志，或没及时收手（`assets._Fetcher`／`winhttp._read_body`）。
  - `ExitTest` 失败 → 退出时还等下载线程（`assets.download` 的收尾时限）。
  - `test_keep_alive_reads_whole_body` / `test_chunked_body` 失败 → 正常下载或分块正文读坏了。
  - `test_connection_cut_mid_body` 失败 → 读到一半断开时的重试或失败原因改了。
  - `test_connection_setup_error` 失败 → 建会话失败被误当成「已取消」，或没按原因报。

## test_download_stats.py：每轮下载识别图片的用时和最慢的几张（download_stats）

- **测试观点**：
  - 给了 `stats` 时填对：要下的张数、成功和失败张数、总秒数、被拦和提前停下、连接数和是不是慢速；最慢的 5 张按秒数从慢到快，不足 5 张时有几张写几张；同一张下了两遍（旧地址 404，问过接口按新地址再下）的两遍用时加在一起；问接口那一步直接收下的（内容和清单对不上、正是 wiki 上更新过的图片）也有字节数；取消、被站点防护拦下时也填，没在下载的（没有字节数、用时不到 0.1 秒）不算最慢的那几张；每张都有了结果以后才置上停止标志的不算提前停下。
  - 不给 `stats` 时返回值和以前一样。
- **检证对象**：`advisor/assets.py` 的 `download`（`stats`）、`_Timings`（每张的用时、同一张两遍的累加、最慢几张的排序）。
- **什么时候要跑**：改了 `download` 的计时口径或 `stats` 的字段、`_Timings`，或改了下图的流程（`_get_image`、`_Book.run`）以后。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_download_stats`（上次约 0.7 秒）。
- **需要什么**：不需要联网（下载换成假的 opener），钟换成假的（每张的用时由假回话拨钟决定）；识别图片的文件夹指到测试沙箱的临时文件夹。
- **输出怎么看**：`slowest` 的名字、秒数、字节数直接核对；总秒数取一位小数，按假钟拨过的秒数之和核对。
- **出错时一般是哪里不对**：
  - `test_fills_counts_seconds_and_slowest_five` / `test_slowest_when_fewer_than_five` 失败 → 计时口径、排序或个数改了（`_Timings.add`、`_Timings.slowest`）。
  - `test_two_passes_added_together` 失败 → 同一张两遍的用时没加在一起（`_Timings` 按 `_Job.index` 累加，三段用的要是同一个 `_Job`），或问接口的那一下被算到了某一张上。
  - `test_cancel_still_fills` / `test_blocked_still_fills` / `test_stop_after_all_handled_not_paused` 失败 → `download` 末尾的 `finally` 没填，或 `paused`、`blocked` 的判定改了（`_Book.cut_short`），或最慢几张不再滤掉没在下载的（`_Timings.slowest`）。
  - `test_accepted_after_refresh_has_bytes` 失败 → 问接口那一步直接收下的图片没补记字节数（`download` 里包着 `_ask` 的 `ask`、`_Timings.saved`）。
  - `test_slow_mode_reported` 失败 → 慢速时的连接数或 `slow` 没记对（`download` 里挑速度那段）。
  - `test_without_stats_same_result` 失败 → 不给 `stats` 时下载本身坏了。

## test_download_block.py：识别图片的下载被站点防护拦下时整体停下、冷却以后接着下

- **测试观点**：
  - `BlockedTest`：一条连接拿到 HTTP 567，另一条连接手上那张收手、不写文件，之后谁都不再发请求，没下的都算暂停（`is_paused`）、记下冷却 35 分钟；拿到网页（拦截页，状态码 200 或带页面的 403）也按被拦算、不去问接口；不带页面的 403 单独一次只算那一张失败，连着两次才按被拦算；限流（429、503）先按 Retry-After 等（没写时 60 秒），所有连接一起等，连着两次、要求等得比 300 秒还久时按被拦算；同一阵被拒只算一次：6 条连接上的请求都发出去以后才一起回 429 的，按单独一次限流一起等过再下、不记冷却，一起回不带页面的 403 的各算那一张失败（`test_one_wave_of_rejections_counts_once`），等过以后新发出去的又被拒才按被拦算（`test_rejected_again_after_wave_blocks`），同一阵里先回来的是限流、后回来的是 567 或拦截页面的照旧一次就停（`test_block_in_same_wave_still_stops`）；Retry-After 写成秒数和 HTTP 日期都认，乱写的（含「²」这类不是 ASCII 的数字）不认、也不妨碍认出 567；被拦之前下好的照常留着。
  - `CooldownTest`：冷却记录写进 `assets/download-cooldown.json`（整份替换，不留临时文件），每次都从文件读（重启以后照样认）；再被拦翻倍 35、70、140、180 分钟，上一次被拦一天以前的从 35 分钟算；Retry-After 更长按它；格式不对的不认，时刻离谱（比现在晚一天以上）的从第一次读到起按冷却分钟数算、写回文件（之后再读不再往后挪）；过了点按慢速下（1 条连接、每次隔 1 秒），下全了清掉记录，没下全时留着。
  - `ProbeTest`：冷却期间再下载（悬浮窗上点「重试」）只发一个请求：仍被拒（567、限流、403、拦截页）或没得到回话时冷却记录一字不改；通了按慢速接着下完、清掉记录；通了以后接着下又被拦算新的一次（翻倍）；通了、没下完就退出时到点的时刻提前到现在。
  - `LastBlockedTest`：冷却记录记着认出被拦的是哪一张（`item`，dir/file）；接着下时它排到最后，其余照原来的先后；冷却期间的试探请求不用它（只缺它一张时才用），仍被拒时记录照旧；6 张里一张一直回网页、另一个 wiki 上的那张一直被拦时，几轮下来别的图都下到，冷却照旧翻倍；记录里没有这一项的照原来的先后。问接口那一批被拦时不记是哪一张。
  - `BatchTest`：内容对不上、404 的攒起来，所有图片下过一遍以后按所在的 wiki 50 张一批问 imageinfo 接口，接口请求之间隔 2 秒，接口限流单独一次时等过 Retry-After 再问；对得上新校验值的不再下载；换了地址的再下一次，接口里没有的记作已删除；接口被拦（567、拦截页）一样整体停下；三段（先下一遍、问接口、按新地址再下）之间也隔开各自的间隔；取图器的疏密（`test_fetcher_interval`）：给了 `interval` 的（接口用的 `API_INTERVAL`、开发用的取图工具下图片用的 `SLOW_INTERVAL`）只按这条连接上的固定间隔，和每秒的上限无关，什么都不给的照样每秒最多 `RATE` 个。
  - `PaceTest`（真的等，假的 opener 每个请求故意等一会儿）：平时整个一轮每秒最多开始 `RATE` 个请求（各条连接、各段合在一起算）：每个图片请求都按上限领时刻，领到的时刻两两相隔不小于 1/`RATE`，每个请求都不早于领到的时刻开始；同时在路上的不超过 `WORKERS` 个、6 条都用上（`test_rate_and_connections`）；每张都要等 0.8 秒时，照平时的速度（每秒 5 个、6 条连接）8 张领到的时刻一个紧接一个（第 8 个离第 1 个正好 7/`RATE` 秒），同时在路上的有 4 个以上，约 2.2 秒下完，接近「张数 ÷ 每秒上限」，比「张数 × 等待 ÷ 2」快（只开 2 条连接、每条连接上一张接一张地等的话达不到，`test_waits_overlap`）；先下一遍、问接口、按新地址再下三段共用每秒的上限，接口请求不领时刻、放行时记进共用的计时，按新地址再下的第一个请求领到的时刻离它至少 1/`RATE`（`test_phases_share_pace`）；等请求时刻的时候取消，正等着的线程马上收手、`download` 按时返回、之后不再有请求（`test_stop_while_waiting_for_turn`）；限流时已经领了时刻、还在等的请求也不发，等过 Retry-After 再重新领：发出去的请求领到的时刻要么在限流算进去之前、要么在 Retry-After 到了以后（`test_throttle_holds_waiting_turns`）；6 条连接里一条认出被拦，已经在路上的回话到了也不写文件、不重试，领到的时刻在认出被拦以后的一个都不发（`test_block_with_six_connections`）；被拦过以后给了 6 条连接也只用 1 条、不领时刻、照旧隔 `SLOW_INTERVAL`，冷却期间只试一个请求（`test_slow_after_block_unchanged`）。
  - `CancelTest`：限流、正在按 Retry-After 等的时候取消，照旧约 `STOP_POLL`＋`STOP_GRACE` 秒内返回，算「已取消」，不记冷却。
  - `OrderTest`：还缺的图片按角色照、Q 版头像、筹码图标排。
  - `BlockedNoticeTest`：悬浮窗下载那一行被拦时的写法（几点几分接着下、⚠ 橙色、边框不变色、不说检查网络，「重试」的悬停说明）、识别不暂停、情形写进错误记录；启动时还在冷却不下载；到点自动接着下（假的钟和计时器，早到的计时器只更新提示、再排一次）；冷却期间点「重试」交给下载模块，点之前排的「到点接着下」作废（后来才到也不下载、不再排），离上一次试不到 `RETRY_GAP_S`（10 秒）再点不算、到点以后点不受它限制；「其余 N 张」只数到点要下的（下载模块的 `missing`：这一轮下好的、wiki 上已删除的不算，网络出错的算，`missing` 不算的今天记了日期的也不算；交不出 `missing` 时按没下好的去掉已删除的）；拒绝过以后接着下时识别不暂停；冷却期间退出不用等，退出以后才到的计时器也不再开始下载；下载模块没记下冷却时照 35 分钟算、进到整分；几点几分的写法（今天、明天、更晚）。这几条比的是平时（要下的多、下载期间识别暂停）的下载，`DOWNLOAD_PAUSE_OVER` 打补丁改成 2，假的 3 张就算多（只补少量图片、识别照常的情形在 `test_download_fail.py`）。
  - `RetryBlockedTest`：在下载失败那一行点「重试」、这一下正好被站点防护拦下（用真的下载模块，只换取图、钟和计时器）：今天个别下载失败（记了 `failed`）、wiki 上已删除（记了 `gone`）的那一张，点「重试」时去掉记号，下载那一行写「其余 1 张」，到点真的接着下，下好了这一行消失，还下不了回到下载失败那一行、重新记号；中途重启（新的悬浮窗，覆盖清单从文件读）、当天冷却结束后照样接着下；覆盖清单写不进时不出错，这次运行里照样接着下，文件里的记号原样留着；没点「重试」的（启动时、冷却到点）照旧不下记了号的，冷却期间点「重试」照旧只试一个请求、不去记号。
- **检证对象**：`advisor/assets.py` 的 `download`、`_Gate`（含 `holds`，`rejected` 按 `sent` 分同一阵）、`_Book`、`_Pace`（`claim`、`started`）、`_Fetcher`（`_turn`、`sent`）、`RATE`、`WORKERS`、`_get_image`、`_ask`、`_refreshed`、`_batches`、`_settle`、`cooldown`、`_note_block`、`_write_cooldown`、`_retry_after`、`_is_page`、`_http_error`、`missing`、`forget_recent`、`Blocked`、`Cooldown`、`PAUSED`、`is_paused`；`advisor/ui/overlay_parts/background.py` 的 `check_assets`、`start_download`（`RETRY_GAP_S`，点「重试」时去记号）、`download_finished`、`blocked_round`、`left_after_block`、`log_block`、`_show_cooldown`、`_resume_due`；`notices.py` 的下载那一行和悬停说明；`wording.py` 的 `DOWNLOAD_BLOCKED`、`DOWNLOAD_BLOCKED_RETRY`、`ASSETS_TIP*`、`resume_time`。
- **什么时候要跑**：改 `advisor/assets.py` 的下载流程、认被拦、冷却、限流、接口合批；改悬浮窗下载那一行的提示、「重试」、到点接着下。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_download_block`（上次约 16 秒，其中 `PaceTest` 真的等约 8 秒，`BlockedTest` 里同一阵被拒的几条约 2 秒）。
- **需要什么**：不需要联网：下载换成假的 opener，识别图片的文件夹指到测试沙箱的临时文件夹；悬浮窗离屏，下载模块换成假的（`RetryBlockedTest` 用真的下载模块，取图换成假的 opener）。
- **输出怎么看**：等待大多换成记下来、不真等的 sleep，钟换成假的（钟不走，平时每秒的上限领到的时刻依次往后排，第 k 个请求记下的等待是 k/`RATE` 秒上下；要看这个的几条把上限调成每 10 秒一个，等待和真的耗时分得开）；量「所有连接一起等」「接口请求隔 2 秒」、同一阵被拒的几条和 `PaceTest` 真的等（间隔常量、每秒的上限 `RATE` 打补丁改小或调大，让几秒内跑完）。疏密按领到的时刻核对（`Claims` 记下 `_Pace.claim` 领出去的时刻、`_Pace.started` 记进去的时刻）：领到的时刻在锁里算，两两相隔不小于 1/`RATE`，不留余量；请求实际开始的时刻只核对不早于领到的、约好的时刻（留 `EARLY`，0.005 秒，计时的舍入），不核对相邻两个请求实际相隔多久：带时限的等待按系统计时器的精度醒，常晚十几毫秒，8 个测试进程一起跑、机器忙时晚得更多，只会让请求更稀，按实际相隔核对会误报。假的 opener 记下每次请求的网址和发请求的线程；同一阵的回话（`Wave`）等前几个请求都到了才一起回。
- **出错时一般是哪里不对**：
  - `BlockedTest` 失败 → 认被拦的口径（`_http_error`、`_is_page`、`_Gate.rejected`）或全部停下（`_Gate.block`、`_Book.run` 的收尾）改了；同一阵被拒的三条失败 → 连着被拒的数法改了（`_Gate.rejected` 按请求放行的时刻 `_Fetcher.sent` 和最近算进去的那一次 `_since` 分同一阵，`_Fetcher._turn` 记 `sent` 要在看 `holds` 之前）。
  - `CooldownTest` 失败 → 冷却记录的读写、翻倍和上限、慢速改了（`cooldown`、`_note_block`、`_settle`、`download` 里挑速度那段）。
  - `ProbeTest` 失败 → 冷却期间只试一个请求的做法改了（`_Gate` 的 `probing`、`answered`、`unanswered`，`_settle`）。
  - `LastBlockedTest` 失败 → 记下被拦的是哪一张、接着下时排到最后的做法改了（`_Gate.block` 的 `item`、`_note_block`、`cooldown`、`download` 开头排先后那一句）。
  - `BatchTest` 失败 → 接口合批、间隔或刷新后的处理改了（`_batches`、`_ask`、`_refreshed`、`query_imageinfo`），或三段之间的间隔（`_Pace`）；`test_fetcher_interval` 失败 → 取图器两种疏密的分法改了（`_Fetcher` 给了 `interval` 时按固定间隔、不给时按 `_Pace` 领时刻，开发用的三个取图工具靠前一种）。
  - `PaceTest` 失败 → 平时的疏密改了：`test_rate_and_connections`、`test_waits_overlap` 是每秒的上限或连接数（`RATE`、`WORKERS`、`_Pace.claim`、`download` 里挑速度那段；有意改这两个数时软件维护说明、使用说明里的时间一起改）；`test_phases_share_pace` 是各段没有共用一个 `_Pace`，或按固定间隔下的请求没记进去（`_Pace.started`）；`test_stop_while_waiting_for_turn` 是等时刻的时候不看停止标志（`_Fetcher._turn` 的 `_wait`）；`test_throttle_holds_waiting_turns` 是等时刻的时候被限流了没重新等（`_Fetcher._turn` 看 `_Gate.holds`、`_Gate.rejected` 加一）；`test_block_with_six_connections` 是被拦以后在路上的照样写了文件、重试，或等着时刻的照样发了（`_keep`、`_get_image` 看停止标志，`_Gate.block`）；这几条报「领到的时刻相隔不够」「请求早于领到的时刻开始」时是 `_Pace.claim` 或 `_Fetcher._turn` 的等待改了，不是机器忙；`test_slow_after_block_unchanged` 是被拦过以后的慢速、只试一个请求改了（同 `CooldownTest`、`ProbeTest`）。
  - `BlockedNoticeTest` 失败 → 悬浮窗那边的接线改了（`background.py` 的 `blocked_round`、`_show_cooldown`、`_resume_due`、`start_download`，`notices.py` 的 `rows`、`tips`）；有意改了提示的写法时，断言和使用说明里照抄的那几处一起改。
  - `RetryBlockedTest` 失败 → 点「重试」时去记号的做法改了（`background.py` 的 `start_download` 点「重试」那一支，`assets.py` 的 `forget_recent`、`missing` 认不认内存里记着的、`_note_failed`、`_update_local`），或「其余 N 张」、到点接着下没照 `missing` 算（`left_after_block`、`start_download` 冷却那一支）。

## test_download_fail.py：个别识别图片一直下不了时隔天再试、只补少量图片时识别照常

- **测试观点**：
  - `RecordTest`：一轮下完、属于个别图片下不了时，这一张自己的问题（网络出错、5xx、单独一次的 403、问过接口仍对不上、卡面裁不出）的那几张记进本地覆盖清单的 `failed`（今天的日期），当天 `missing` 不算它们、`retry_items`（点「重试」）照样算、第二天又算；一轮失败超过 `FAILED_MANY` 张（按总张数，不分哪一类：像断网，或者几百张写不进混着一张网络出错）时一张都不记，正好 `FAILED_MANY` 张的照记；这一轮被拦下（567）或取消了时，同一轮里先出了网络错误的那张也不记；下载线程建不了下载会话（`NO_SESSION`，建会话的那一步换成出错的替身、不发请求）时一张都不记；写不进、没接上裁图、wiki 上已删除（另记 `gone`）的不记，一张图试满次数都是限流、最后报成「HTTP 429」的（界线 `REJECT_LIMIT` 打补丁调大来造）也不记；下好了清掉这笔，记下失败时这张别的记录（wiki 上更新过的地址）照留；覆盖清单写不进时不出错、下次照旧再下；诊断包 version.txt 的「识别图片：缺 N 张」照旧把记了日期的几张算进去。
  - `SkipTest`：日期不是文字的丢掉，写得不对（不是 YYYY-MM-DD、没有这一天）、比今天还晚的当没记，这一节或整个文件写坏了照常下载；以前的覆盖清单没有这一节时当没记；记下的当天不算、第二天起算，wiki 上已删除的照旧 7 天，两样互不影响；wiki 上已删除的日期同样，比今天还晚、写得不对的当没记（现在的写法、以前只有日期的写法都一样）。
  - `ForgetTest`：点「重试」时去记号（`forget_recent`）只去给的那几张的 `failed`、`gone`，这几张的更新记录、现裁记录和别的图片的记号照留，去了以后 `missing` 照常算它们；没有要去的不写盘（没有覆盖清单时也不建）；`missing`、`retry_items` 调用以后覆盖清单一个字节都不变；写不进时不出错、文件原样，这次运行里 `missing` 当它们没有记号（重启以后照文件），又失败重新记号以后、存好以后从内存里拿掉。
  - `ClassifyTest`：`is_own_failure` 认哪些原因（被拦暂停、取消、写不进、没接上裁图、已删除、建不了下载会话、最后报成限流 429、503、原因不明的不算）；`is_crop_failure` 只认卡面裁不出（`CROP_FAILED` 开头）；`isolated`、`deferred` 的各种组合（只缺一张、那一张也没下好照样算个别；混着写不进的只把网络出错的那几张隔天再试；原因不明、被拦、取消、建不了下载会话、失败超过 `FAILED_MANY` 张都不算，张数按总数算：写不进、已删除的也算进去，正好 `FAILED_MANY` 张的照算个别）。
  - `PauseTest`：要下的不多于 `DOWNLOAD_PAUSE_OVER` 张时下载期间识别不暂停（热键照常请识别、状态胶囊不写「识别 暂不可用」、悬停说明写识别照常），多一张就照旧暂停（热键提示「识别功能暂不可用」）。
  - `FailedNoticeTest`：一张下不了时下载那一行就是选定的那一句（逐字核对），多张写「有 N 张」「用到这些图片的」；混着写不进的、只有 wiki 上已删除的，「明天会自动再试」改为「之后会自动再试」，悬停说明按原因写各自什么时候再试；带「重试」和「关闭本条提示」，⚠ 橙色、边框不变色；悬停说明写今天不再自动下载、明天再试；错误记录写明这张（这几张、其中几张）明天再自动下载；全部或大部分没下下来（失败超过 `FAILED_MANY` 张，按总张数：几百张写不进混着一张网络出错、四十张 wiki 上已删除的也算；20 张以内混着写不进的照旧是「之后」那一句）、建不了下载会话时照旧说检查网络、只有「重试」，悬停说明写下次启动时重新下载，里面有 wiki 上已删除的（不论混着写不进还是网络出错，种类 `gone`）换成写明已删除的 7 天后再试的那一句；失败的全是卡面裁不出时（种类 `crop`）换成下载后处理失败的那一句（写明错误记录的位置，不说检查网络，只有「重试」，悬停说明同大部分没下下来），混着一张网络出错的、下载模块没有 `is_crop_failure` 的照旧说检查网络，不超过 `FAILED_MANY` 张的照旧是个别图片下不了；下载模块没有 `isolated`、原因不明时照旧说检查网络；关掉以后刷新也不再显示、关掉不会下载，再下载再失败时又出现，「重试」照样下（`retry_items`）；点「重试」开始下载之前先叫下载模块去掉这一轮要下的几张的记号（`forget_recent`），启动时不去，下载模块交不出这个接口、调用出错时照旧下载（出错的写进错误记录）；当天记下失败的几张（真的 `missing`）启动时不下载、不提示，第二天照常下载。
  - `NoticeModelTest`：提示行的排法（个别的两种写法都是两个按钮、其余的只有「重试」，没写种类的旧写法照旧说检查网络，卡面裁不出的那一句写明错误记录的位置）和下载那一行各种状态的悬停说明；悬停说明（`ASSETS_TIP_LATER`、`ASSETS_TIP_GONE`）和使用说明里写死的「7 天」和 `GONE_RETRY_DAYS` 一致。
- **检证对象**：`advisor/assets.py` 的 `load_local`、`_update_local`、`_note_failed`、`forget_recent`、`_write_local` 写没写进、`_within`、`_recently_failed`、`_recently_gone`、`GONE_RETRY_DAYS`、`_THROTTLED_REASONS`、`missing`、`retry_items`、`is_own_failure`、`is_crop_failure`、`_no_session`、`isolated`、`deferred`、`download` 的收尾、`_Book.run` 里建不了会话的那一步（`NO_SESSION`）、`_keep`、`FAILED_RETRY_DAYS`、`FAILED_MANY`；`advisor/ui/overlay_parts/background.py` 的 `start_download`（`DOWNLOAD_PAUSE_OVER`、`_dl_mode`，点「重试」时去记号）、`download_finished`、`failure_kind`、`crop_only`、`gone_later`、`deferred_count`、`dismiss_failed`、`log_failures`；`notices.py` 的 `few_failed_text`、`rows`、`tips`；`wording.py` 的 `DOWNLOAD_FEW`、`DOWNLOAD_FEW_LATER`、`DOWNLOAD_CROP_FAILED`、`ASSETS_TIP_QUIET`、`ASSETS_TIP_FEW`、`ASSETS_TIP_LATER`、`ASSETS_TIP_GONE`、`ASSETS_TIP_FAILED`；`advisor/ui/overlay.py` 接上「关闭本条提示」那一句（`dismiss_download`）；`advisor/diagnostics.py` 的 `environment_text`（「识别图片：缺 N 张」）。
- **什么时候要跑**：改 `advisor/assets.py` 里本地覆盖清单的读写、`missing`、`download` 的收尾、失败原因的写法；改悬浮窗下载期间暂不暂停识别、下载失败那一行的写法和按钮、悬停说明。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_download_fail`（上次约 2 秒）。
- **需要什么**：不需要联网：下载换成假的 opener，识别图片的文件夹指到测试沙箱的临时文件夹，「今天」用 `ACC_TODAY` 钉死、第二天另设；悬浮窗离屏，下载模块换成假的（分「个别」「大部分」照用下载模块自己的 `isolated`、`deferred`）。
- **输出怎么看**：等待换成不真等的 sleep；取消那一条用一直被限流（等 60 秒）的第二张，第一张处理完时置停止标志，真等的只有收尾的一小会儿。
- **出错时一般是哪里不对**：
  - `RecordTest` / `SkipTest` 失败 → 记失败日期或跳过的规矩改了（`download` 末尾的 `_note_failed(deferred(...))`、`_recently_failed`、`missing` 的 `retry_failed`、`_keep` 清记录的条件、`_update_local` 清的几节）。
  - `ForgetTest` 失败 → 点「重试」时去记号的规矩改了（`forget_recent` 去哪几节、哪几张，什么时候写盘，写不进时在内存里记着的 `_forgotten` 和 `missing` 认不认它，`_note_failed`、`_update_local` 从内存里拿掉），或查询函数（`missing`、`retry_items`）动了记号。
  - `ClassifyTest` 失败 → 哪些原因算这一张自己的问题、界线 `FAILED_MANY`、「没下完不记」或「建不了下载会话不记」改了（`is_own_failure`、`_no_session`、`isolated`）。
  - `PauseTest` 失败 → 下载期间暂停识别的界线或接线改了（`background.py` 的 `start_download`、`DOWNLOAD_PAUSE_OVER`、`_set_blocked`）。
  - `FailedNoticeTest` / `NoticeModelTest` 失败 → 下载失败那一行的分法、写法、按钮或悬停说明改了（`background.py` 的 `failure_kind`、`crop_only`、`gone_later`、`dismiss_failed`、`log_failures`，`notices.py` 的 `rows`、`tips`、`few_failed_text`，`overlay.py` 的 `actions`），或点「重试」时去记号的接线改了（`background.py` 的 `start_download`）；有意改了提示的写法时，断言和使用说明里照抄的那几处一起改。

## test_assets_mirror.py：wiki 上没有、或和游戏里不一样，放在备用地址上的识别图片（清单里的 mirror 项）

- **测试观点**：
  - `MirrorDownloadTest`：备用地址上的图片下到了、校验值对上，照常存下，不写 wiki 上更新过的记录、不留 `.part`；备用地址回 404、内容对不上（大小、SHA-1）时只算这一张失败，不问 wiki 的接口，不记成 wiki 上已删除；回网页只算这一张失败，不按站点防护拦截算（不冷却）；回 403、429 不算进 wiki 的被拒（`_Gate.rejected` 不调），同一轮里 wiki 的图片照常下完、不冷却；403、404 只请求一次；503、没得到回话按 `BACKOFF` 等过再试，试满 `RETRIES` 次算失败，不算进 wiki 的被拒和试探；个别下载失败照常记下今天，当天 `missing` 不算、点「重试」（`retry_failed`）照常重下，最后报成限流的不记；覆盖清单里这一项以前从 wiki 下载时的记录（更新过的地址、已删除）不用，照清单从备用地址下，存好以后清掉。
  - `MirrorProbeTest`：wiki 被拦以后的冷却期间再下载（只试一个请求）：备用地址上的那张下到了，wiki 上的试探请求仍被拒时照试探被拒算、冷却记录一字不改；备用地址上的那张没得到回话，不按试探没得到回话算，wiki 上的试探照常通了、下好。
  - `LoadManifestTest`：官方仓库的版本（`update.USE_MIRROR` 为真）读进来带 `mirror` 的为 True、不带的为 False，`missing` 照常算它；复刻的改版（`USE_MIRROR` 为假）读清单时去掉 `mirror` 项，`missing`、`retry_items` 都没有它，wiki 上的照常；带 `fallback` 的（`test_fork_uses_fallback`）官方的版本照旧从备用地址下、不管 fallback，复刻的改版换成 fallback 里的下法（从卡面现裁、或 wiki 上那张，本地文件不变），fallback 里写了 `mirror` 也不认；旧版本从卡面现裁、这一版改放备用地址的角色照（`test_upgrade_from_cropped`），覆盖清单里记着现裁记录的，复刻的改版照 fallback 认它、不重下，官方的版本按大小对不上只从备用地址下这一张（不下卡面、不问 wiki 的接口），存好以后清掉现裁的记录、再启动不再算缺。
  - `MakeManifestMirrorTest`：`tools/make_assets_manifest.py` 把 `MIRRORED` 里的图片写成 mirror 项（没有 `title`，地址是 `MIRROR_BASE` 加 `assets/<dir>/<SHA-1>/<file>`，大小、SHA-1 照本地文件算），算进 `counts`、`download_bytes`；本地少一张、Q 版头像识别不会读（编号不是 `data/heroes.json` 里的角色）时打印出来、退出码 1、不写清单；wiki 上已经有了、和备用地址上一样时打印一行提醒，照样写成备用地址上的，fallback 是 wiki 上那张；wiki 上的和游戏里不一样时不提醒，只写进 fallback；`card_crops.json` 里有裁法的角色照（`test_crop_fallback`）一起问卡面，fallback 是卡面加裁法，不另列一项现裁的，卡面在 wiki 上找不到时报出来、退出码 1、不写清单；`MIRRORED` 里的健全版角色照列在 `sfw_skip.json` 里的（`test_sfw_skip_not_listed`）和别的不用下载的健全版一样不进清单、不问它的文件页；开发目录里另有照 wiki 列的图片时，两边的文件页在同一次里问，备用地址上的那几张只列成备用地址上的一项。
- **检证对象**：`advisor/assets.py` 的 `AssetItem.mirror`、`load_manifest`（看 `update.USE_MIRROR`）、`_get_mirror_image`、`effective`、`_recently_gone`、`_keep`、`missing`、`retry_items`；`tools/make_assets_manifest.py` 的 `MIRRORED`、`main`。
- **什么时候要跑**：改 `advisor/assets.py` 里备用地址上的图片那几处（下载、出错怎么算、读清单）；改 `advisor/update.py` 的 `USE_MIRROR`、`MIRROR_BASE`；改 `tools/make_assets_manifest.py` 的 `MIRRORED` 或写 mirror 项的那一段；改了 `data/assets_manifest.json` 里的 mirror 项（另跑 test_release、test_project_consistency）。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_assets_mirror`。
- **需要什么**：不联网：下载换成假的 opener，wiki 接口换成假的函数，识别图片的文件夹、清单所在的文件夹指到测试沙箱的临时文件夹；冷却那两条用假的钟。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `MirrorDownloadTest` 失败 → 备用地址上的图片出错时的算法改了（`_get_mirror_image` 哪些重试、哪些只算这一张失败，`effective`、`_recently_gone` 不用 wiki 的记录，`_keep` 清记录），或备用地址的回话算进了 wiki 的 `_Gate`。
  - `MirrorProbeTest` 失败 → 冷却期间的试探把备用地址上的请求当成了试探（`_Gate` 的 `probing`、`answered`、`unanswered`）。
  - `LoadManifestTest` 失败 → 读清单时 `mirror` 字段没读进来，或复刻的改版没去掉 mirror 项、没换成 fallback（`load_manifest` 要在每次读时看 `update.USE_MIRROR`）。
  - `MakeManifestMirrorTest` 失败 → 生成清单时 mirror 项和 fallback 的写法、本地少图或识别不会读时的拦法、备用地址上的健全版照不照 `sfw_skip.json` 挑掉改了（`make_assets_manifest.py` 的 `main`）。

## test_card_crop.py：从卡面原画现裁角色照（裁图、下载、推算裁法的工具）

- **测试观点**：
  - `CropImageTest`：裁法套到造出来的卡面上，记号落在矩阵算出的位置（摆正的、转了 17° 的，差不过 0.3 像素；卡面放大一倍、裁法不变，记号照样落在原处）；颜色按透明度乘进去（全透明处存的白色变成黑的，半透明的按比例变暗），角色照落在卡面外面的部分全透明；不带透明度的、灰度的卡面也裁得出；卡面解不开（不是图片、空的、截断的）、裁法不对（少一个数、不是数、无穷大、缩成一点、数大得离谱以致算不出角色照落在哪里）、角色照整个落在卡面外面、裁出来整张透明时报 `CropError`；`CropError` 是 `ValueError`（下载模块按它认），核对数据用的角色照大小同裁图。
  - `RealCardTest`（要卡面样本）：真的卡面按 `data/card_crops.json` 的裁法裁，和英文 wiki 上同款的游戏角色照（四周各少 8、18 像素）比，角色本身那部分相关度 0.97 以上；裁法的横竖弄反（第一、二列除错了宽高）、左右翻了时 0.8 以下。
  - `TurnedCardTest`（要派德曼「酷暑难耐」的卡面样本）：卡面就是识别图片清单里那一项的卡面（大小、SHA-1、裁法都对得上；这张放在备用地址上，看的是复刻的版本照着现裁的 fallback），裁法转了约 -42.25°、`photo_tilt.json` 里写 -42.3；裁出来的角色照按这个角度转回去（认角色建模板时的转法），脸部那一块和卡面上同一块（只缩放、不转）相关度 0.95 以上，反着转 0.5 以下；样本里另有英文 wiki 上同款的游戏角色照时，和它比整张、脸部那一块都在 0.97 以上（没有时这一条跳过）。
  - `CropDownloadTest`：下到卡面、核过校验值以后裁成角色照写盘，和直接裁的一样，卡面不落盘、不留 `.part`；覆盖清单记下卡面的 SHA-1、裁法和角色照的大小，之后算「在」、不再下载；记录对不上的（卡面换了、裁法改了、大小不同、没有记录、旧的覆盖清单没有这一节、读不出来）都不算在；以前下过的 wiki 角色照（同名、没有记录）重下、整份换掉，旧图是硬链接时链接到的那一份不动；裁不出时只请求一次、不问接口、不写图、不记录（原因以「卡面裁不出角色照」开头）；下到的卡面核不过大小、校验值（接口给的也还是清单里那张）时这一张失败，裁图的函数一次都不调；没接上裁图时这一张失败、卡面不下（不会把卡面当角色照存下来），接上了不给 `cropper` 也按它裁；写不进时原因以「写不进文件」开头、不记录；卡面被拦（567）时同别的图片暂停、冷却记录记下是这一张；卡面在 wiki 上换过时问接口收下、按新卡面裁、记新的校验值；清单在照原样下载和从卡面现裁之间改过时（`test_stale_update_from_other_kind_ignored`），以前另一张文件（旧角色照、卡面）的更新记录不用，照清单下、存好以后清掉；已删除的记录也一样（`test_gone_record_from_other_kind_ignored`）：记着的文件页不是现在要问的那一页、以前只有日期的写法对现裁的项，都不用，照常下载，下载时查到删了记下日期和问的那一页；清单项没写卡面的校验值时这一张失败（`NO_CARD_SHA1`），卡面不下、不交去解码；还缺的图片按普通角色照、现裁的角色照、Q 版头像、筹码图标排；清单项的 `crop` 读成元组、没有的为 `None`、写坏了的照样当成要现裁的；覆盖清单里类型不对的现裁记录丢掉；`note_crop` 记的和下载时一样；悬浮窗导入下载模块时只登记导入裁图模块的函数，这一轮有要现裁的图片时才导入、只导入一次，导入出错记进错误记录、按没接上算（卡面不下）；另起一个进程核对导入下载模块不带进裁图模块、识别的包和 OpenCV。
  - `DeriveTest`：参考图带透明度的按透明度比，截图按四周的底色认出背景，`--pad` 那一圈、`--mask none` 的写法；造的卡面上按已知的裁法（转 12°）裁出参考图，粗找时只试 10.5°、13.5°，细调以后差不过 0.5 像素、转角 12°；命令行打印可以贴进 `card_crops.json` 的一项和 `photo_tilt.json` 的提示；要卡面样本的两条：英文 wiki 上的游戏角色照当参考（`--pad 8,18`）、截图样的参考图（垫底色、缩小、存成 JPEG，转了约 17° 的那一套）推算，和照游戏内核对的裁法差不过 1 个像素。
- **检证对象**：`advisor/vision/cardcrop.py` 的 `crop_image`、`crop_png`、`pixel_matrix`、`as_bgra`、`premultiplied`、`CropError`、`PHOTO_SIZE`；`advisor/assets.py` 的 `download`（`cropper`）、`_get_image`、`_keep`、`is_present`、`effective`、`_same_kind`、`_same_crop`、`load_local`、`_usable_crop`、`_update_local`、`note_crop`、`use_cropper`、`_current_cropper`、`_recently_gone`、`load_manifest`、`_crop_field`、`_order`、`missing`、`CROP_FAILED`、`NO_CROPPER`、`NO_CARD_SHA1`；`advisor/ui/services.py` 的 `_assets`、`_crop_png_loader`；`tools/derive_card_crop.py` 的 `derive`、`reference_frame`、`max_offset`、`main`；`tools/check_data.py` 的 `card_of`、`crop_rotation`；`data/card_crops.json`。
- **什么时候要跑**：改 `advisor/vision/cardcrop.py`（新用了 `cv2` 的名字时另跑 `test_build_cv` 和 `test_dev_tools`：比对脚本的探针要跟着补，由 `test_dev_tools` 的 `ProbeCoverageTest` 核对）、`advisor/assets.py` 里现裁那几处（`_keep`、`is_present`、`effective`、覆盖清单的 `cropped`、`_order`）、`tools/derive_card_crop.py`；改 `data/card_crops.json`；换了卡面样本。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_card_crop`（上次约 11 秒，多半花在推算裁法的那几条上）。
- **需要什么**：不联网（下载换成假的取图函数，图片写在测试沙箱的临时文件夹里）。`RealCardTest` 和 `DeriveTest` 里的两条要卡面样本：`tests/data/cards/` 下的 `UT_Hero_Card_106_02.png`、`UT_Hero_Card_112_03.png`、`UT_Hero_Card_103_01_sfw.png`（BWIKI 上的原图），和 `tests/data/cards/enwiki/` 下英文 wiki 上同款的游戏角色照 `UT_Hero_RolePhoto_106_02.png`、`UT_Hero_RolePhoto_112_03.png`（224×304）；`TurnedCardTest` 要 `UT_Hero_Card_105_01.png`，和它比游戏角色照的那一条另要 `enwiki/UT_Hero_RolePhoto_105_01.png`；没有时这几条跳过（见第五节，位置可用 `ACC_CARD_SAMPLES_DIR` 改）。
- **输出怎么看**：同总则。
- **出错时一般是哪里不对**：
  - `CropImageTest` 失败 → `cardcrop.py` 的矩阵换算（第一列除宽、第二列除高）、只取盖住的那一块时的平移、透明度乘进去那一步，或取样方式改了。
  - `RealCardTest` 失败 → 裁法的含义或取样方式改了，或 `data/card_crops.json` 里这几套的数字改了；相关度掉到 0.97 以下又不是有意改的，先看是不是方向、缩放弄反了。
  - `TurnedCardTest` 失败 → 派德曼那一套的裁法、`photo_tilt.json` 里的角度、清单里的卡面（BWIKI 上换过卡面）改了，或转角的约定（`check_data.crop_rotation` 和 `hero.turn_photo` 的正负）对不上了：转回去以后反着转的那一面反而像，就是正负号弄反了。
  - `CropDownloadTest` 失败 → `assets.py` 里现裁的部分（`_keep` 的裁、存、记的先后，`is_present` 的三样核对，`effective` 认不认更新记录（`_same_kind`）、`_recently_gone` 认不认已删除的记录，`_get_image` 没接上裁图、没有卡面校验值时不下，`_update_local` 的 `cropped`，`_order`，`load_manifest` 的 `crop`）或接上裁图那一段（`services.py` 的 `_assets`、`_crop_png_loader`，`assets.py` 的 `use_cropper`、`_current_cropper`）改了。
  - `DeriveTest` 失败 → `derive_card_crop.py` 的粗找（缩放范围、转角、脸部模板）、细调（坐标下降的步长和停的条件）或认背景的办法改了；有意放宽、收紧时，断言里的像素数和角度一起改。

## test_window_capture.py：直接截取游戏窗口那套，以及取帧入口按出错种类挑截取方式

- **测试观点**：
  - `ClassifyTest`：错误代码归成哪一类（拒绝、不支持、窗口已关、显卡驱动重置、其他），有符号/无符号都认；`CaptureError` 的字段和文字；`_check` 对成功/失败码的处理；空指针不崩溃。
  - `SupportTest`：按 Windows 版本号判断能不能直接截取（19041 起）、能不能请求不显示黄框（20348 起）；这台电脑的版本号。
  - `ImageTest`：从截到的整幅画面里裁出游戏客户区的位置，以及客户区里的一块（快速盯绿框只截筹码那一带：按客户区左上角挪，不全在客户区里时这一帧丢弃）；BGRA→BGR 和「取前三个通道」逐像素相同（含每行有补齐的视图、倒序、隔列、空图、4K）；分段长度不超上限也不浪费。
  - `CopyWaitTest`：等显卡做完拷贝用信号加事件、超时或出错时改由映射自己等；建信号和放掉。
  - `MultithreadTest`：给显卡设备开多线程保护——先向即时上下文要、要不到向设备要、都要不到照常建好；真建设备时的先后次序。
  - `GrabStepsTest`：没用上的旧帧计数；分步计时的报告次序；步骤名集合；只要一块（`region`）时交给读回那一步，不要时照旧按整幅读。
  - `ExclusiveFullscreenTest`：系统报有程序独占全屏时，只在前台窗口属于游戏时才算（切出游戏时别的程序独占全屏不算）。
  - `WindowGoneErrorTest`：找不到窗口、窗口最小化给玩家看的固定中文（找不到时不写窗口标题，三个标题都找过才算找不到）；设了游戏平台、开着的是另一个端时给玩家看的那一句（填上那个端的名字，同 `capture.NOT_FOUND_PLATFORM`）；两个客户端同时识别、这一次明确指定了端时是「未找到「{name}」的游戏窗口。」（`NOT_FOUND_ONE`），另一个端开着也是这一句；`missing_notice` 用眼下设的平台判断同一套情形，悬浮窗定时查看游戏窗口（`probe_game`）时把它放进 `notice`，找到窗口时为空；都是 `RuntimeError` 的一种，悬浮窗认得出这几句。
  - `PlatformTest`：`set_platform` 认 `steam`、`taptap`、`both`（两个客户端同时识别），别的值按自动识别、不抛错；指定了端时只找这个端，不看设置；设置是 `both`、又没给端时和自动识别一样（三个标题都找，两个都开着跟后启动的那个），找到后记下实际的那个端；`both` 时还没区分端的调用处找到的是后启动的那个，找不到时是 `NOT_FOUND`，不点设置的问题；找到的标题按端记，`found_title` 给了端时取那个端上一次找到的（两开时两路识别不互相覆盖），不给时照旧是最后一次找到的，`client_of` 把标题换成对局报告的客户端（`cn`、`int`、`taptap`）；`probe_game` 按端找。
  - `GameTitlesTest`：Steam 版国服（`AstralParty_CN`）、国际服（`AstralParty_INT`）和 TapTap 版（`吉星派对`）的游戏窗口都认，先找国服、再找国际服、最后 TapTap 版，找到哪个用哪个；两个端同时开着时自动识别按进程启动时间取晚的那个；只传一个标题时只找那一个；截图、前台判断和悬浮窗查看游戏窗口（`winapi.probe_game`）认的是同一个窗口；三处的标题（`capture.WINDOW_TITLES`、`winapi.GAME_TITLES`、打包脚本的 `GAME_TITLES`）相同；打包脚本查游戏开着没有时 TapTap 版也认。
  - `ModuleRulesTest`：这个模块只导系统组件和 numpy、cv2，不导界面库、不写界面中文句子。
  - `FrameSourceTest`：会话复用、游戏窗口换了先关旧的、空闲满 30 秒关掉、最小化和找不到窗口、没有新画面时沿用最近一帧、「出错按种类处理」（不支持/拒绝立即改、显卡驱动重置累计到 3 次才改、其他错误重试一次）、改为截取屏幕后再试一次直接截取、截屏方式要求游戏或悬浮窗在前台、窗口正好关了、意外的错误、一直没画面按出错处理，以及状态、统计、快照。
  - `BlankCheckTest`：游戏在前台且连续 10 秒画面几乎全黑时才去屏幕上核对一小块，那块不黑才判为全黑并改方式；读条时屏幕上也是黑的不误判；不在前台不核对；来了一帧亮的就重新计时；少量取样和整套取样结果一致；核对的位置要避开本进程窗口和被别的窗口盖住的地方。
  - `BandSourceTest`：快速盯绿框的取帧对象（`screen.BandSource`）截取方式跟着后台识别的取帧对象：直接截取时自己建一个会话（照后台识别的系统支持请求不显示黄框）、一直复用、只要那一块、最多等 `BAND_WAIT_S`（几十毫秒以内）要新画面；游戏窗口换了先关旧的；客户区大小不对、最小化、找不到游戏窗口、还没有截取方式时不截；会话出错或建不了时关掉、`BACKOFF_S` 内不重建，错误记录只记一次；后台识别改为截取屏幕时截屏幕上的那一块、关掉自己的会话，游戏和拾星助手自己的窗口都不在前台时不截。
  - `LastStateTest`：记录最近一个取帧对象的截取方式。
  - `SystemSmokeTest`、`ReadbackSmokeTest`、`ConvertThreadTest`（子进程、真组件、Win）：真的建显卡设备、读回纹理、量颜色转换只用当前线程。
- **检证对象**：`advisor/vision/window_capture.py`（`classify`、`CaptureError`、`_check`、`system_support`、`client_box`、`region_box`、`strip_rows`、`to_bgr`、`_Device` 的 `_protect`／`_open_fence`／`_wait_copy`／`read_bgr`、`WindowCapture` 的 `_latest`／`grab`（含 `region`）／`_read`、`stats`、`component_check`）；`advisor/ui/screen.py` 的 `FrameSource`、`BandSource`、`initial_state`、`expected_method`、`last_state`；`advisor/vision/capture.py` 的 `grab_region`、`find_window`、`find_game`、`game_in_front`、`client_rect`、`WindowGone`、`NOT_FOUND`、`NOT_FOUND_ONE`、`WINDOW_TITLES`、`set_platform`、`platform`、`found_platform`、`missing_notice`、`PLATFORM_TITLES`、`PLATFORM_NAMES`、`NOT_FOUND_PLATFORM`、`BOTH`、`BOTH_NAME`、`exclusive_fullscreen`；`advisor/ui/winapi.py` 的 `GAME_TITLES`、`find_game_hwnd`、`probe_game`（含 `GameProbe.notice`）；`tools/build_release.py` 的 `GAME_TITLES`。
- **什么时候要跑**：改 `advisor/vision/window_capture.py` 任何一处；改 `advisor/ui/screen.py` 的 `FrameSource`、`BandSource`（选方式、出错处理、贴补之外的取帧逻辑）；改 `advisor/vision/capture.py` 的裁屏、找窗口、游戏平台（`set_platform`、`BOTH`、`PLATFORM_TITLES`、`PLATFORM_NAMES`、`NOT_FOUND_PLATFORM`、`NOT_FOUND_ONE`）、独占全屏、窗口位置；改游戏窗口的标题（三处一起改）。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_window_capture`（上次约 6 秒）。真正开测试窗口的实机版是另一个模块 `test_window_capture_live`。
- **需要什么**：大部分不需要；`SystemSmokeTest`、`ReadbackSmokeTest`、`ConvertThreadTest` 要 Windows + 显卡（在子进程里跑真组件），非 Windows 跳过。
- **输出怎么看**：非 Windows 时那三个真组件的用例跳过；`ReadbackSmokeTest` 里如果这台电脑的显卡驱动用不了信号等拷贝，会跳过并说明「照常由映射自己等」；子进程崩溃算失败。
- **出错时一般是哪里不对**：
  - `ClassifyTest` 失败 → 错误码和种类的对应改了（`window_capture.py` 的 `KINDS`／`classify`）。
  - `SupportTest.test_build_thresholds` 失败 → 版本号门槛改了（`WINDOW_BUILD`／`BORDERLESS_BUILD`）。
  - `ImageTest.test_bgr_*` 失败 → 颜色转换结果和「取前三个通道」不一致（`to_bgr`），或分段长度算错（`strip_rows`）。
  - `ImageTest.test_client_box` 失败 → 裁客户区的位置或「画面尺寸和窗口不一致就丢弃」的判断改了（`client_box`）。
  - `CopyWaitTest` 失败 → 等显卡拷贝的信号/事件流程改了（`_Device._wait_copy`／`_open_fence`）。
  - `MultithreadTest` 失败 → 多线程保护的开启顺序或失败处理改了（`_Device._protect`）。
  - `FrameSourceTest` 失败 → 取帧对象选方式、会话复用、出错分类、状态/统计的某一步改了（`advisor/ui/screen.py` 的 `FrameSource`）。
  - `BandSourceTest` 失败 → 快速盯绿框的取帧对象没跟着后台识别的截取方式、会话没复用或出错后没等就重建、截屏幕时位置没按客户区挪（`advisor/ui/screen.py` 的 `BandSource`）；`ImageTest.test_region_box`、`GrabStepsTest.test_region_passed_to_read` 失败 → 只截一块时的位置换算或交给读回那一步改了（`window_capture.py` 的 `region_box`、`grab`）。
  - `BlankCheckTest` 失败 → 全黑核对的时机或位置改了（`FrameSource._is_blank`／`_screen_differs`／`_probe_block`、`screen._near_black`）。
  - `ExclusiveFullscreenTest` 失败 → 独占全屏只认前台属于游戏的判断改了（`capture.exclusive_fullscreen`）。
  - `GameTitlesTest` 失败 → 找游戏窗口的标题、先后或游戏平台设置改了（`capture._find`、`capture.set_platform`、`winapi.find_game_hwnd`），或三处的标题不一致了。
  - `WindowGoneErrorTest`／`PlatformTest` 失败 → 找不到窗口的几句、`both` 的认法（按自动识别找、两路指定端各找各的）、`missing_notice` 按端写的那一句，或 `found_platform`／`found_title` 的记法改了（`capture._chosen`、`_locate`、`_gone`、`missing_notice`）。
  - `ReadbackSmokeTest` 失败 → 读回纹理那套流程改了（`_Device.read_bgr`）。
  - `ConvertThreadTest` 失败 → 颜色转换又变回多线程了（`to_bgr`）。
  - `ModuleRulesTest` 失败 → 这个模块新导入了界面库或写了界面中文句子。

## test_window_capture_live.py：直接截取的实机测试，截取、端到端识别、4K 性能合成跑一次

- **测试观点**
  - `test_runner_finished` / `test_never_on_screen_or_foreground`：子进程正常跑完并给出结果；测试窗口的矩形从不和屏幕相交、前台窗口从来不是测试窗口。
  - `test_capture_steps`：不被挡、被同进程的窗口完全盖住、移动、改大小、改回、画面静止时取到的画面四色块吻合（≥ 99%）；改大小那一步最多丢 1 帧；最小化时报「已最小化」且没有新画面；关闭后报找不到窗口、重开（新句柄）后重建会话；对句柄 0 建截取对象得到错误而不崩溃。
  - `test_end_to_end`：3 张三选一截图的候选筹码和标注一致、换成三选一后 2 次识别以内给出推荐、截到的画面和原图逐像素一致（≥ 99%）。
  - `test_performance_4k`：每秒 5 次取帧的耗时（95% ≤ 15 毫秒）和 CPU（≤ 单核 6%，按 CPU 周期数算）；建会话/取帧增加的内存、空闲关闭后释放的内存、连续 300 帧的句柄数和内存增长。
- **检证对象**：`advisor/ui/screen.py`（`FrameSource`、`CaptureState`）、`advisor/vision/capture.py`、`advisor/vision/window_capture.py`（`WindowCapture`、`CaptureError`）、`advisor/vision/pipeline.py`（`FrameProcessor`）、`advisor/session/`（`Session`、`Settings`）；执行脚本 `tests/live_capture_run.py` 和 `tests/live_window_host.py`。
- **什么时候要跑**：改了截取相关代码（`advisor/ui/screen.py`、`advisor/vision/window_capture.py`、`advisor/vision/capture.py`）、识别流程（`advisor/vision/pipeline.py`）或 `Session` 的接入，或改了和 Windows 截取有关的底层代码时。平时（快测、慢测试）都不跑。
- **怎么单独跑**：先设环境变量再跑，只有这一个文件：
  ```
  $env:CHIP_ADVISOR_LIVE_CAPTURE = "1"
  python -B tools/run_tests.py --only test_window_capture_live
  ```
  这是实机测试（`tools/run_tests.py` 的 `LIVE` 名单，`--all` 也不含），上次没有记录，约 1.5～3 分钟（其中 4K 性能约 1 分钟）。
- **需要什么**：Windows；环境变量 `CHIP_ADVISOR_LIVE_CAPTURE=1`（不设时整个类跳过，原因是「实机测试（测试窗口都在屏幕范围以外）：设环境变量 CHIP_ADVISOR_LIVE_CAPTURE=1 时运行」）。端到端那部分要识别样本——`tests/live_capture_run.py` 读 `samples/labels.json` 和 4 张截图（一张对局画面 `20261002_213905_hud_round7_real.jpg`，三张三选一 `20261002_212245_…`、`20261002_213757_…`、`20261002_215027_…`），取标注里的 `file`、`hero`、`offer`；这些按仓库根目录的 `samples/` 直接读（这部分不走 `ACC_SAMPLES_DIR`），缺了会报错而不是跳过。测试窗口全程在所有屏幕范围以外（`x = -20000`），不进任务栏、不抢焦点、不出声，不截屏幕、不碰别的程序的窗口；用到「请求不显示黄框」时系统会在隐私记录里记一条。
- **输出怎么看**：子进程执行 `tests/live_capture_run.py`，它最后打印一行 `RESULT {json}`，并把结果另存到 `.tmp/live-capture/result-<时间>.json`；跑了性能还会打印一张分步表。失败时用例把子进程 stderr 的末尾贴进断言说明（`self.proc.stderr[-3000:]`），据此判断是哪一步没过。
- **出错时一般是哪里不对**
  - `test_runner_finished` 失败 → 子进程没跑完或没给出结果（看 stderr 末尾的异常）。
  - `test_never_on_screen_or_foreground` 失败 → 测试窗口建到了屏幕里，或抢了前台（去看 `tests/live_window_host.py` 的位置/样式）。
  - `test_capture_steps` 失败 → 截取逻辑改了：某一步四色块对不上、改大小丢帧、最小化/关闭/重开的处理不对（去看 `advisor/vision/window_capture.py`、`advisor/ui/screen.py`）。
  - `test_end_to_end` 失败 → 识别没在 2 次内出推荐、候选和标注不一致，或截到的画面和原图对不上（去看 `advisor/vision/pipeline.py`、`advisor/vision/chip_select.py`）。
  - `test_performance_4k` 失败 → 取帧变慢、CPU 升高，或内存/句柄泄漏（去看 `advisor/vision/window_capture.py`、`advisor/ui/screen.py`）。
  - **改了预期就该跟着改**：通过标准里的阈值（99%、15 毫秒、6%、内存/句柄上限）是有意定的；确实放宽/收紧时，把本文件里的数字一起改。

## test_window_layer_live.py：实机测悬浮窗的窗口层次

- **测试观点**（每个 `test_` 方法一步）：
  - `test_runner_finished`：子进程跑完、给出结果、用的是 Qt 的 windows 平台。
  - `test_never_on_screen_or_foreground`：全程悬浮窗和测试窗口的矩形都不和屏幕相交、前台窗口从不是它们（会在 20 次以上核对中反复确认）。
  - `test_start_topmost`：一开始悬浮窗排在游戏窗口上面、置顶，识别照常进行。
  - `test_cover_threshold`：和别的窗口相交 7 像素不算盖住；相交 8 像素第一次不退，连续第二次才退到它后面（层次是「盖住的窗口 → 悬浮窗 → 游戏」，悬浮窗不再置顶）。
  - `test_front_during_chip_select`：出现筹码三选一时悬浮窗回到最上层（置顶并排在最前）；三选一关闭后再退回原来后面。
  - `test_uncovered_restores_topmost`：不再被盖住时马上恢复置顶。
  - `test_skips_transparent_hidden_minimized`：鼠标可穿透的透明覆盖层、隐藏的窗口、最小化的窗口都不算盖住。
  - `test_topmost_cover`：设成「总在最前」的窗口盖住时，悬浮窗排在它正下方且仍置顶；三选一时盖过它；关闭后排回它正下方；它关掉后照常置顶。
  - `test_minimized_game`：游戏窗口最小化时暂停识别、不再置顶；还原后恢复。
  - `test_screen_capture_like_0_12_0`：改成截取屏幕方式时，切出游戏就暂停、不再置顶；恢复直接截取后照常置顶。
- **检证对象**：`advisor/ui/overlay.py` 的 `Overlay`、`update_activity`、窗口层次计划（`window_plan`）；`advisor/ui/winapi.py` 的 `probe_game`、`windows_above`、`covering`、`topmost`、`below`、`put_below`、`bring_to_front`。执行脚本是 `tests/live_layer_run.py`（另起测试窗口进程 `tests/live_window_host.py`），它们不是被测代码。
- **什么时候要跑**：改了 `advisor/ui/overlay.py` 里判断前台状态、被盖住、暂停、调整窗口层次的部分；改了 `advisor/ui/winapi.py` 窗口层次那组函数；或改了「什么时候退到别的窗口后面」的规则。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_window_layer_live`（默认跳过；是实机测试，实际约 15～40 秒，要设环境变量 `CHIP_ADVISOR_LIVE_CAPTURE=1`——和直接截取的实机测试共用这个开关）。
- **需要什么**：Windows 桌面、能开多个窗口。会在所有屏幕范围以外（x 约 -19000）开 1 个悬浮窗和 5 个测试窗口，不进任务栏、不抢焦点、不出声、不截屏。
- **输出怎么看**：子进程打印一行 `RESULT {json}`，测试把它拆成每一步来核对。结果里有平台名、每一步的窗口层次顺序、悬浮窗上面紧挨的是谁、是否置顶、识别状态、状态胶囊、`on_screen`（悬浮窗或测试窗口上过屏的记录，应为空）、`foreground`（前台是测试窗口的记录，应为空）、核对次数。子进程崩溃、超时、没打印结果都算失败。
- **出错时一般是哪里不对**：
  - `test_start_topmost` / `test_uncovered_restores_topmost` 失败 → 恢复置顶的逻辑坏了（`overlay.py` 调整层次那段）。
  - `test_cover_threshold` 失败 → 「盖住」的像素门槛或「连续两次才退」的判断改了（`winapi.COVER_MIN`／`covering`，和 `overlay.py` 的调用）。
  - `test_front_during_chip_select` 失败 → 三选一时「排到最前」改了（`winapi.bring_to_front`）。
  - `test_skips_transparent_hidden_minimized` 失败 → 「透明、隐藏、最小化不算盖住」的过滤改了（`winapi.covering`）。
  - `test_topmost_cover` 失败 → 遇到「总在最前」的窗口时排它正下方的逻辑改了（`winapi.put_below`）。
  - `test_minimized_game` / `test_screen_capture_like_0_12_0` 失败 → 暂停识别、取消置顶的时机改了（`overlay.py` 的前台状态/暂停判断）。
  - `test_never_on_screen_or_foreground` 失败 → 测试窗口的位置算法动了（属于测试脚本问题，不是被测代码）。

## test_screen_capture.py：截取屏幕时把悬浮窗从识别画面里抹掉（贴补）

- **测试观点**：
  - `CoveredBlocksTest`：窗口矩形和游戏客户区（游戏画面那块矩形）求交集——相交才算、裁掉外面的部分、正好贴边不算、副屏的负坐标也要对。
  - `GrabFrameTest`：旧的调用方传 `allow_capture=False` 时直接整幅截图不贴补；悬浮窗不在客户区就不动任何窗口的截图属性；压在客户区上就临时让这几个窗口不出现在截图里、只重截相交的那几小块、贴回原图，图里其余部分逐像素不变；一个或多个窗口都贴；重截小块出错时退回「排除后整幅重截」；截图期间窗口被移动或隐藏，原位置和新位置都补；读「允不允许截图」的判断要在锁里、后台等锁时界面线程改了开关要按新值走。
  - `FrameSourceScreenTest`：取帧对象改用截取屏幕方式（系统不支持直接截取）时一律贴补，不建直接截取的会话。
  - `GrabBlockTest`：快速盯绿框截屏幕上的一块（`grab_block`）同样贴补：截图前后各查一次窗口，不相交时不动任何窗口的截图属性、也不拿 `AFFINITY_LOCK`（后台识别截整幅时不用等它），压住这一块时拿锁、再查一次窗口、只重截交集贴回，重截出错时排除后整块重截（截的还是这一块）。
  - `PolicyLockTest`：界面线程切窗口的截图属性时等一下后台截图，等不到也不卡界面。
  - `ExcludedTest`：让窗口暂时不出现在截图里的那套调用——只改当前能被截到的窗口，结束时（含中途出错）都改回。
  - `WinApiSmokeTest`：真实 Win32 调用（枚举本进程窗口、对无效句柄查询和设置截图属性）不出错。
  - `LiveCaptureTest`（实机）：屏幕上真弹一个品红小窗，识别用的截图里没有它、是它下面的画面，截完窗口又能被截到。
- **检证对象**：`advisor/ui/screen.py` 的 `grab_frame`、`grab_block`、`_patch_own`、`covered_blocks`、`policy_lock`、`FrameSource`（截取屏幕这条路）；`advisor/ui/winapi.py` 的 `own_windows`、`excluded`、`capture_affinity`、`set_capture_allowed`；`advisor/vision/capture.py` 的 `grab_client`、`grab_region`、`grab`。
- **什么时候要跑**：改了 `advisor/ui/screen.py` 里贴补、`covered_blocks`、`AFFINITY_LOCK`、`policy_lock` 或 `FrameSource` 用哪种方式；改了 `advisor/ui/winapi.py` 的截图属性那一组（`excluded`、`capture_affinity`、`set_capture_allowed`、`own_windows`）；改了 `advisor/vision/capture.py` 的 `grab_client`、`grab_region`、`grab`。
- **怎么单独跑**：`python -B tools/run_tests.py --only test_screen_capture`（上次约 0.2 秒；`LiveCaptureTest` 是实机测试，要设 `CHIP_ADVISOR_LIVE_CAPTURE=screen` 才跑）。
- **需要什么**：平时不需要；`LiveCaptureTest` 要 Windows 桌面，会在屏幕左下角显示一个 160×90 的小窗约 1 秒（不抢焦点、不出声）。
- **输出怎么看**：跳过时原因写在用例名后面（非 Windows、或没设那个环境变量）；贴补出错改成整幅重截时会给错误记录写一条，这个文件是用替身核对「写没写」的，不写真的记录。
- **出错时一般是哪里不对**：
  - `CoveredBlocksTest` 失败 → 交集范围算错（`advisor/ui/screen.py` 的 `covered_blocks`）。
  - `GrabFrameTest.test_overlay_patched` 失败 → 贴补的坐标或重截小块的范围算错（同一函数 + `grab_frame`）。
  - `GrabFrameTest.test_patch_error_falls_back` 失败 → 贴补出错时没退回整幅重截，或没把窗口属性改回（`grab_frame`）。
  - `GrabFrameTest.test_window_moved_during_capture` / `test_window_hidden_during_capture` 失败 → 截图前后两次查窗口、合并两次结果的做法坏了（`grab_frame`）。
  - `GrabFrameTest.test_allow_read_inside_lock` / `test_switched_on_while_waiting` 失败 → 读开关的时机或加锁顺序坏了（`grab_frame` 的 `AFFINITY_LOCK`）。
  - `GrabBlockTest` 失败 → 截一块时的贴补（`grab_block`、和 `grab_frame` 共用的 `_patch_own`）改坏了；`GrabFrameTest` 同时失败时多半是 `_patch_own` 的先后改了。
  - `ExcludedTest` 失败 → `winapi.excluded` 只改能截到的窗口、或出错时没改回。
  - `WinApiSmokeTest` 失败 → `winapi` 的 Win32 声明/参数类型写错了。
  - `LiveCaptureTest` 失败 → 真机上贴补没生效、坐标和屏幕缩放对不上、或截完没把属性改回。

## test_sysinfo.py：测向 Windows 声明本进程要高性能、读回本进程的运行状况，以及读这台电脑的配置（处理器、内存、电源、显示器、显卡）

- **测试观点**：
  - 声明高性能时传进去的结构和信息类（Version 1、ControlMask 1、StateMask 0、信息类 4、大小 12 字节）；设不上或调用出错只记一次、不抛出（`DeclareTest`）。
  - 读回的省电设置判成 high/eco/auto、优先级类别换成名字、别的值写 `0x…`（`PerformanceStateTest`、`PriorityClassTest`）。
  - CPU Set 缓冲区的解析：大小核分组、处理器编号区间写法、按每项的 Size 往跳、跳过别的 Type、缓冲区截断、多个处理器组（`ParseCpuSetsTest`）。
  - 设置窗口「只用大核」一项用的处理器名称：去掉 (R)、(TM)（不分大小写）、®、™，紧挨着的两个词中间留一个空格，连续的空白并成一个；读不到、去掉以后什么都不剩时为 `None`；测试里一律按读不到（`CpuLabelTest`）。性能自检比出来的收益到了 `BIG_CORE_GAIN_MIN`（1.3，正好到也算）才算排在小核上，比过、不到为假，没比过、不是有限的数时说不准（`OnSmallCoresTest`）。
  - 把配置写成中文几行：大小核、只有一种核心、多于两组、用电池加节电、电源计划只写名字不写原始 GUID、读不到的各项；有刷新率时括号里接在最后（`MachineLinesTest`、`PowerInfoTest`）。
  - 读屏幕时本线程临时切到按每块屏幕的缩放、读完切回（出错也切回）；切不成时标明宽高可能是换算后的；每块屏幕的刷新率（`EnumDisplaySettingsW` 的 `dmDisplayFrequency`）读得到，系统报 0、1、没有这个函数、调用抛出时为 `None`（`DisplaysTest`）。
  - 在这台电脑上真调一次（只影响跑测试的这个进程）确认功能可用、类型对、够快，有屏幕时刷新率是 `None` 或正常的整数（`RealCallTest`、`RealMachineInfoTest`、`RealDisplaysTest`）；不是 Windows 时模块照样导入、各项为 `None`（`NotWindowsImportTest`）。

- **检证对象**：`advisor/sysinfo.py` 的 `declare_high_performance`、`performance_state`、`priority_class`、`cpu_info`、`_parse_cpu_sets`、`_classes_from`、`_range_text`、`cpu_label`、`on_small_cores`、`BIG_CORE_GAIN_MIN`、`memory_info`、`power_info`、`_power_mode`、`_power_plan`、`displays`、`_per_monitor_dpi`、`gpus`、`machine_info`、`machine_lines` 及各 `_…_line`。测试把模块级的 `_kernel32`、`_user32`、`_shcore`、`_powrprof` 换成假的。

- **什么时候要跑**：改 `advisor/sysinfo.py` 的任何部分；改 `machine_lines` 写进诊断包 `version.txt` 的那几行中文；改优先级、电源计划、电源模式的名字表。

- **怎么单独跑**：`python -B tools/run_tests.py --only test_sysinfo`（上次约 0.2 秒）。

- **需要什么**：大部分用假的系统调用；`RealCallTest`、`RealMachineInfoTest`、`RealDisplaysTest` 只在 Windows 上真调一次。不需要显卡，也不开窗口。

- **输出怎么看**：真调的几条会声明本测试进程要高性能、读一次本机配置；不在 Windows 上时跳过。错误记录写进测试自己的临时文件，不写真实的错误记录。

- **出错时一般是哪里不对**：
  - `DeclareTest` 失败 → 传给 `SetProcessInformation` 的结构、信息类或几位掩码改了（这几个值由 Windows 定死，改了多半是错的）。
  - `PerformanceStateTest`、`PriorityClassTest` 失败 → 判定或名字表改了；有意改名字就一起改断言。
  - `ParseCpuSetsTest` 失败 → `_parse_cpu_sets`/`_classes_from`/`_range_text` 的解析改坏了（大小核分组、区间写法）。
  - `MachineLinesTest`、`PowerInfoTest` 失败 → `machine_lines`/`power_info` 的中文或写法改了；有意改就一起改断言（诊断包 `version.txt` 会跟着变）。
  - `DisplaysTest` 失败 → `displays`/`_per_monitor_dpi` 的临时切换或「virtualized」标记改了。
  - `RealMachineInfoTest.test_types_and_fast` 失败 → `machine_info` 在真机上变慢（超过 1 秒），或某项类型不对（例如又用上会卡的 WMI）。
  - `NotWindowsImportTest` 失败 → 模块在非 Windows 上导入不了，或没按 `None` 兜底。

## test_usage.py：测处理器占用的采样（每分钟一条、存盘、导出时现量各线程）

- **测试观点**：
  - 每分钟一条：第一次只记基准、第二次按这一段的时长出一条（字段、`state` 合进去）、不到 1 秒不出记录也不换基准、读不到、抛出时不往外抛、只留最近 `KEEP` 条（`TickTest`）。
  - 电源状况：取到就原样放进 `power`；读不到、出错时不带这一项，这一条照记（`PowerTest`）。
  - 存盘：没 attach 时一个文件都不写；attach 读回 12 小时以内的，丢掉更早的和坏行并重写文件，文件不存在时不新建；攒满 5 条才追加、flush 立刻写；文件超过 2 * KEEP 行时重写成最近 KEEP 条；写盘出错以后不再写、不抛异常，内存照记，只记一次错误记录；reset 清掉存盘的状态（`PersistTest`，文件都在测试自己的临时文件夹里）。
  - 现量：真起一个空转的 Python 线程跑一小段，它在 `threads` 里、`core` 够大、`name` 对、`stacks` 里有那个函数名；每层调用栈只有文件名（不带路径分隔符和盘符）；结果里没有 `module`、`start` 这类键；调用线程自己没有调用栈；`sys._current_frames()` 抛异常时有 `stacks_error`、占用照常；不是 Windows 时为 `None`（`ProbeTest`、`SecondsDefaultTest`）。
  - 空转的线程用 `time.sleep(0)` 走圈：一直占着处理器，同时让出 GIL，主线程才跑得动。

- **检证对象**：`advisor/usage.py` 的 `tick`、`attach`、`flush`、`records`、`reset`、`_parse`、`_append`、`_rewrite`、`_fail`、`_read_power`、`probe`、`_thread_ids`、`_thread_times`、`_frames_of`、`_collect_stacks`、各常量。

- **什么时候要跑**：改 `advisor/usage.py` 的任何部分；改 `advisor/perfstats.py` 的 `usage_snapshot`/`usage_percent`；改导出诊断里现量、每分钟记录的做法。

- **怎么单独跑**：`python -B tools/run_tests.py --only test_usage`（上次约 2 秒）。

- **需要什么**：只在 Windows 上真量线程；不需要显卡，也不开窗口。

- **出错时一般是哪里不对**：
  - `TickTest` 失败 → `tick` 的基准、时长门槛、字段或留存的条数改了。
  - `PowerTest` 失败 → `power` 的取法或读不到时的处理改了。
  - `PersistTest` 失败 → `attach` 读回/重写、攒多少条写一次、`flush`、文件长度上限、写盘出错后的处理、`reset` 改了；写盘出错那条要看 `append_lines` 是不是只调了一次、错误记录是不是只记了一条。
  - `ProbeTest` 失败 → 线程枚举、线程时间、调用栈的取法或结果里的键改了；空转线程没占到处理器时看 `time.sleep(0)` 那个循环。

## test_diagnostics_reports.py：测导出诊断包的写盘加固和脱敏、自动保存包的清理、另带的三选一那几帧和对局中途像打完的那一帧、同时识别时另一张悬浮窗的子文件夹、错误记录和崩溃记录、每分钟的占用记录、自检的必备数据文件、「系统」一行不查 WMI、把电脑配置写进诊断包

- **测试观点**：
  - 先在「名字 `.partial`」里写、写完再改名成正式名字，不留 `.partial`；写一半出错留 `.partial` 并抛 `ExportError`；自动保存的标记只在该有时才有；另带的文件名不能冒充那个标记（大小写、末尾带点或空格）（`ExportStagingTest`）。
  - 手动导出另打的压缩包（`zip_report`）：在诊断包文件夹旁边、同名加 `.zip`，解开是同名的文件夹、文件一个不少（中文文件名照旧）、按 deflate 压缩，文件夹照旧留着，不留 `.zip.partial`；压缩包不算诊断包（`report_dirs` 只认文件夹）；已经有同名的压缩包时不覆盖；打到一半写不进、改名一直被占用（稍等再试，用完了照原样报错）时不留下 `.zip` 和 `.partial`，文件夹照旧（`ZipReportTest`）。
  - 另带最近一次三选一有筹码没读出来时的那一帧（`select_unreadable`）：另写一张 `select-unreadable-<时分秒>.png`，内容逐像素就是交来的那一帧，`state.json` 的 `select_unreadable` 记下时刻和文件名，当时的画面 `frame.png` 照旧；没给时不多写文件、`state.json` 里也没有这一项；编码不了时原因记进 `select_unreadable_error`、不记文件名，其余内容照常写好；另带的记录不能用这个文件名开头（`SelectUnreadableTest`）。记下三选一选中项用的那一帧（`select_picked`）、被临时盖住而没有采用的那一帧（`select_covered`）同样另写 `select-picked-<时分秒>.png`、`select-covered-<时分秒>.png`，`state.json` 各记时刻、那一次的情况（第几张、哪张筹码，或读到的文字、稀有度）和文件名，那一次的情况里有同名的 `time`、`file` 时盖不掉时刻和文件名（没写成时也不留下一个文件名）；一张编码不了只记这一张的原因；另带的记录不能用这两个文件名开头（`SelectShotsTest`）。对局中途像一局打完的那一帧（`finish_look`）另写 `finish-look-<时分秒>.png`，`state.json` 的 `finish_look` 记时刻、那一次的情况和文件名（`test_finish_look_written`）；另带的记录也不能用 `finish-look-` 开头。
  - 清理自动保存的包：只删最旧的、只留最近若干份，没到份数不删（`PruneTest`）；只删带标记、名字是时间、不是链接的文件夹，被别的程序占着的那份整份留着，特意传进来「不要删」的那份不删、也算在保留份数里，同一秒的几份按序号排（`PruneSafetyTest`）。
  - 路径脱敏：个人目录、应用数据目录、账户名换成 `%USERPROFILE%`/`%APPDATA%`/`%LOCALAPPDATA%`/`%USERNAME%`；账户名只作完整路径段才换（很短、是常见词时不误伤正文）；大小写、正反斜杠、`\\?\` 前缀、个人目录被重定向到别的盘或网络位置时都对；换过的不再换第二次；JSON 文字里的转义（`\n` 等）不当成路径分隔符；崩溃记录里非 ASCII 字符写成 `\uXXXX` 的用户目录、账户名也换；给了环境变量时按那一份换（`ScrubTest`、`ScrubEdgeTest`）。
  - 自检的必备数据文件清单包含 `assets_manifest.json`、`meta.json`、`mutators.json`（`RequiredDataTest`）。
  - 每分钟的占用记录经 `extra` 写成 `usage.jsonl`，最近 12 小时的统计同样经 `extra` 写成 `stats.jsonl`，这两个名字都不占 `RESERVED`；`crash.txt`、`crash.old.txt` 占 `RESERVED`（`ExportStagingTest`）。
  - 手动导出带上错误记录、它的旧记录和两个崩溃记录：有才带、空的不带；账户名和崩溃记录里 `\uXXXX` 的写法都换掉；不是 UTF-8 的字节不抛错、照样换；超大的只带末尾并写明，切开处那半行不要（半行里的路径不全，认不出账户名）；自动保存的不带；文件名和 `startup` 的两个相同；自检「占用采样」一项不算关键项（`LogsTest`）。
  - 自检「占用采样」：只有一个 Python 线程时也看得出取不取得到调用栈（另起的陪量线程算进 `stack_threads`，量完结束）；枚举不到线程、量不了时抛错；取调用栈出错时把原因带进结果（`UsageCheckTest`）。
  - 「系统」一行写法同 Python 的 platform 模块、但不查 WMI（WMI 卡住时导出照常几秒内做完）（`SystemLineTest`）。
  - 导出时把这台电脑的配置和运行状况写进 `version.txt` 和 `state.json`（只取一次）；读不到时只写一行、导出照常成功（`MachineInfoExportTest`）。
  - `version.txt` 的「作业站登录」一行只写有没有、不写昵称，只查一次凭据管理器（`wincred.exists`）；作业站的入口关着（`hub.ENABLED`）时不写这一行，也不查凭据管理器（`HubLoginLineTest`）。
  - 同时识别两个客户端时另带另一张悬浮窗的子文件夹（`lanes`）：`<端名>/frame.png` 就是交来的那一帧、`<端名>/events.jsonl` 就是交来的记录、给了 `stats` 时另写 `<端名>/stats.jsonl`（路径照样脱敏），`state.json` 的 `lanes.<端名>` 里调用方给的状态照旧、另记没写成画面时的 `frame_error`，顶层的还是第一张的；画面为 `None`、编码不了时记的原因，端名查不过（含末尾带换行、`con`、`nul`、`com1` 这类 Windows 的设备名）时抛 `ValueError` 且不建诊断包；`snapshot` 里 `lanes` 或这一端不是字典时原来的值放进 `state` 一项、不丢；压缩包里有子文件夹；不给时没有子文件夹（`LaneReportTest`）。

- **检证对象**：`advisor/diagnostics.py` 的 `export`、`zip_report`、`ZIP_SUFFIX`、`ZIP_PARTIAL_SUFFIX`、`USAGE`、`STATS`、`CRASH_LOGS`、`_copy_logs`、`usage_check`、`_write_unreadable`、`_write_shot`、`_lane_state`、`LANE_NAME`、`LANE_DEVICES`、`NO_FRAME`、`SELECT_UNREADABLE`、`SELECT_UNREADABLE_PREFIX`、`SELECT_PICKED`、`SELECT_COVERED`、`FINISH_LOOK`、`FINISH_LOOK_PREFIX`、`SHOT_PREFIXES`、`ExportError`、`REPORT_NAME`、`AUTO_MARK`、`report_dirs`、`prune_auto_reports`、`_remove_report`、`REQUIRED_DATA`、`system_text`、`_machine_info`、`_machine_lines`；`advisor/fileio.py` 的 `staged_dir`、`PARTIAL_SUFFIX`；`advisor/paths.py` 的 `reports_dir`、`state_path`、`schemes_path`、`temp_dir`、`scrub_paths`、`_replace_path`、`_ascii_escaped`（`advisor/diagnostics.py` 照旧导出 `scrub_paths`）；`advisor/sysinfo.py` 的 `machine_info`、`machine_lines`。

- **什么时候要跑**：改了 `advisor/diagnostics.py` 的导出/清理/另带的那一帧/另一张悬浮窗的子文件夹（`lanes`）/错误记录和崩溃记录（`_copy_logs`）/自检的「占用采样」一项/「系统」一行/电脑配置；改了 `advisor/paths.py` 的脱敏（`scrub_paths`，错误记录、崩溃记录也用它，同时跑 `test_errors`、`test_startup_guard`）；改了 `advisor/fileio.py` 的 `staged_dir`；改了诊断包文件夹的命名或自动保存标记；改了必备数据文件清单。

- **怎么单独跑**：`python -B tools/run_tests.py --only test_diagnostics_reports`（上次约 6 秒）。

- **需要什么**：目录联接相关的几条、以及「开着的文件删不掉」那条，只在 Windows 上跑；「系统版本」那条也只在 Windows 上跑。临时文件在 `.tmp/tests-diagnostics-reports/` 下。

- **输出怎么看**：跳过时写明原因（「目录联接只在 Windows 上有」「Windows 上开着的文件删不掉」「系统版本只在 Windows 上有」）。不碰真实的 `reports` 目录（都换成临时文件夹）；`SystemLineTest` 把界面库的缓存清掉、错误记录换成假的，并起一个后台线程验证导出不等 WMI。

- **出错时一般是哪里不对**：
  - `ExportStagingTest.test_writes_then_renames_to_a_final_name` 失败 → 导出没有先写 `.partial` 再改名，或残留了 `.partial`。
  - `test_partial_is_left_when_writing_fails` 失败 → 写一半出错时没留住 `.partial`、或没抛 `ExportError`。
  - `test_extra_name_cannot_smuggle_the_marker` 失败 → 另带文件名的检查（不能用保留名、末尾不能是点或空格、大小写）松了。
  - `ZipReportTest` 失败 → `zip_report` 打的位置、名字、里面的路径或压缩方式改了，覆盖了已有的压缩包，或出错时留下了 `.zip`、`.partial`（先写 `.zip.partial`、写完刷盘再改名，改名被占用时按 `PARTIAL_DELAYS` 稍等再试）。
  - `SelectUnreadableTest` 失败 → 另带的那一帧的写法（文件名 `SELECT_UNREADABLE` 和里面的时刻、`state.json` 的 `select_unreadable` 两项）、出错时的记法（`select_unreadable_error`，同 `frame_error`），或没给时多写了东西（`_write_unreadable`、`export`）。
  - `PruneTest`、`PruneSafetyTest` 失败 → `prune_auto_reports` 删了不该删的（手动导出的、链接、`.partial`），或没保住特意传进来的那份、或同一秒的排序错了。
  - `ScrubTest`、`ScrubEdgeTest` 失败 → `scrub_paths` 误伤正文（账户名很短、是常见词、或正好是 JSON 的转义字母）或脱敏不全；有意改脱敏规则时一起改断言。
  - `RequiredDataTest` 失败 → 必备数据文件清单少了（有意增删文件时改断言）。
  - `SystemLineTest.test_same_wording_as_platform` 失败 → `system_text` 的版本名对照表改了，或又回去调 `platform.platform()`。
  - `test_export_does_not_wait_for_wmi` 失败 → 导出里又走到了会查 WMI 的那条路（会一直卡住）。
  - `MachineInfoExportTest` 失败 → 导出时取电脑配置的次数（应只一次）或读不到时的写法改了。
  - `LaneReportTest` 失败 → `export` 的 `lanes` 子文件夹（`_lane_state` 的兜底、`frame_error` 的两种写法、端名的检查）改了；端名的检查松了会写到别的文件夹或盖掉顶层文件，查不过时必须一个诊断包都不建。
