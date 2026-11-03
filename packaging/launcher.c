/*
 * 发行版根目录的启动程序（文件名是工具的中文名，如「拾星助手.exe」，见 tools/build_release.py 的 LAUNCHER_EXE；这里不依赖
 * 自己的文件名）：启动同一文件夹里的 app\ACC.exe（真正的程序）。正常双击时等它的悬浮窗出来再退出，它在这之前出错退出了
 * 就弹窗说明；带参数时带起就退出。
 *
 * 发行版解压后，根目录只有这个启动程序和使用说明.html；真正的程序、运行库、数据和程序运行时生成的文件
 * 都在 app 子文件夹里，不用在一堆运行库里找要双击的程序。由 tools/build_release.py 组装时用 zig 编译（GUI 程序，
 * 不开控制台窗口），图标和版本信息与真正的程序相同，内部名称、原始文件名是自己的文件名（packaging/launcher.rc）。
 *
 * - 只用 Windows 自带的接口：不联网，不写注册表，不读写任何文件（只在启动不了时查一下 app\ACC.exe 在不在）。防多开由
 *   真正的程序自己管，这里不碰。
 * - 命令行参数原样转交：自己的命令行去掉第一项（程序名）后，剩下的部分一个字不改接在 app\ACC.exe 后面，参数里的空格、
 *   引号、反斜杠都照原样。第一项在哪里结束按 C 运行库拆参数的规则（引号成对切换，引号外的空格或制表符为止），所以两边的
 *   程序拆出来的参数相同。工作目录设为 app 文件夹。
 * - 把「可以把窗口放到前台」的权限交给真正的程序（用户双击启动的进程才有这个权限）：它显示悬浮窗、已有一个在运行时请
 *   那一个显示悬浮窗，都要用到。
 * - 带起之前从自己的环境里去掉 COMPAT_VAR（__COMPAT_LAYER），哪种启动方式都去掉：给拾星助手.exe 设的兼容性
 *   （例如「替代高 DPI 缩放行为」）是经这个环境变量传给子进程的，不去掉 ACC.exe 也照它运行，悬浮窗发虚、识别错位。直接给
 *   app\ACC.exe 设的不经过这里，由它自己检查。「以管理员身份运行」不靠这个变量传给子进程：CreateProcessW 起的子进程总在
 *   调用者的安全上下文里运行，启动程序是以管理员身份运行的，ACC.exe 去掉这个变量以后照样是管理员。
 * - 正常双击（不带任何参数，也没有 WAIT_ARG；见 no_arguments）时等悬浮窗出来（见 watch_ready、watch）：带起
 *   之前建一个命名事件，名字放进环境变量 READY_VAR 传给 ACC.exe，它显示出悬浮窗约 5 秒以后触发这个事件（和 ACC.exe 约好的
 *   接口；等多久由它定，见 advisor/ui/startup.py 的 READY_DELAY_MS）。最多等 READY_WAIT_MS（15 秒）：事件触发了、到时间了，都安静退出；ACC.exe 先结束时，退出码是 0（例如已有一个在
 *   运行，请那一个显示悬浮窗后退出）或 EXIT_REPORTED（它自己弹过「启动失败」窗口）就安静退出，其余弹窗写「拾星助手启动后
 *   出错退出」、错误代码、一句白话原因，以及崩溃记录 app\.tmp\crash.txt 在哪（见 crashed）。正常启动时看不出变化，只是
 *   启动程序在悬浮窗出来约 5 秒以后才退出。建不了事件时照旧带起就退出，不等。
 * - 带参数时（例如 --selfcheck：自检不通过本来就以 1 退出）带起就退出，不等、不按退出码弹窗；环境里也不留
 *   READY_VAR（万一是从别处继承来的），免得 ACC.exe 去触发别人的事件。
 * - 要等悬浮窗时，带起之前给自己加上 SEM_FAILCRITICALERRORS（子进程继承错误模式）：缺 DLL、程序文件损坏这类 ACC.exe 一启动
 *   就失败的情形，Windows 不再先弹它自己的错误窗口、等用户点掉才让进程结束，而是马上结束，只弹这里的中文说明（缺的是哪个
 *   DLL 不再显示；想看时直接双击 app\ACC.exe）。ACC.exe 整个运行期间都不弹这类「严重错误」窗口，Windows 的接口文档也建议程序启动时都这样设。
 *   不加 SEM_NOGPFAULTERRORBOX：它让 ACC.exe 整个运行期间崩溃时都不经过 Windows 错误报告，事件查看器里「应用程序错误」那条
 *   记录（出错的模块和位置，由 Windows 错误报告写，记录里带报告编号）和错误报告都没有了，换一台电脑出事时查原因要用它；
 *   而默认设置的 Windows 10、11 崩溃时不弹「已停止工作」，错误报告收集完进程就结束，这里照样弹得出说明。只有开着错误报告
 *   窗口的电脑上，用户先看到 Windows 的窗口，点掉以后（不超过 15 秒时）再看到这里的说明。
 * - app\ACC.exe 要管理员权限（有人在它的属性里勾了「以管理员身份运行」，CreateProcessW 报 740）时，和直接双击它时一样，
 *   改用 ShellExecuteExW 以 runas 启动，由 Windows 弹管理员确认框（见 run_as_admin）：点「是」照常运行，
 *   参数、工作目录、前台权限、隐藏参数都同上（等悬浮窗时一般收不到信号，见 run_as_admin）；点「否」不启动、不提示，
 *   退出码 EXIT_START_FAILED。
 * - 启动不了时按原因弹窗，每种原因给一条能照做的操作（怎么分见 classify，ShellExecuteExW 报的错误
 *   也照它分）：app\ACC.exe 不在（只拷走了这一个文件、解压不完整）时提示重新解压；路径太长时提示把文件夹移到路径较短的
 *   位置；文件不是有效的程序时提示重新解压；拒绝访问或报有病毒（多半是杀毒软件拦了）时提示在杀毒软件中设为信任或从隔离区
 *   恢复；被 Windows 的应用控制拦下时说明是什么拦的、在哪里能看到；被组策略禁止时提示联系电脑管理员；程序文件是云盘里只在
 *   云端的占位文件时提示把文件夹挪到不同步的位置；其他原因提示重新解压，空一行接 Windows 对这个错误的说明。除了前两种都
 *   写明 Windows 的错误代码。退出码：不在为 EXIT_NO_APP，其余为 EXIT_START_FAILED。
 * - 隐藏参数 WAIT_ARG（只给打包脚本的试启动用，必须是第一个参数）：这一项不转交，等真正的程序结束，退出码就是它的
 *   退出码；启动不了时不弹窗（试启动不能被弹窗卡住），只用退出码说明。
 * - 编译时定义 ACC_LAUNCHER_TEST（只在测试里）：不弹窗，提示写到标准错误输出（UTF-16LE），测试读它核对；环境变量
 *   TEST_ERROR_VAR 是十进制数时不真的启动，当作 CreateProcessW 报了这个错误代码，测试用它核对各种原因的提示；环境变量
 *   TEST_WAIT_VAR 是十进制数时，等悬浮窗最多等这么多毫秒（代替 READY_WAIT_MS），测试不用真等 15 秒。测试版里没有
 *   ShellExecuteExW 的调用（不会弹管理员确认框），换成 test_shell_execute（见那里）。
 */
#define WIN32_LEAN_AND_MEAN
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <shellapi.h>

#define APP_EXE L"app\\ACC.exe" /* 相对启动程序所在的文件夹 */
#define APP_DIR L"app"
#define WAIT_ARG L"--launcher-wait"
#define EXIT_NO_APP 0xACC1       /* 找不到 app\ACC.exe（tools/build_release.py 的 LAUNCHER_NO_APP） */
#define EXIT_START_FAILED 0xACC2 /* 其他原因启动不了，含在管理员确认框中点「否」（LAUNCHER_START_FAILED） */
#define EXIT_REPORTED 0xACC3     /* app\ACC.exe 自己弹过「启动失败」窗口以后以它退出，这里不再弹窗（LAUNCHER_REPORTED） */
#define BUF 32768                /* 路径、命令行和提示的上限（Windows 的命令行最长 32767 个字） */
/* 要管理员权限时交给 ShellExecuteExW 的选项（为什么是这几个见 run_as_admin） */
#define RUNAS_MASK (SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC | SEE_MASK_FLAG_NO_UI | SEE_MASK_NOZONECHECKS)
/* 「悬浮窗出来了」的信号（和 ACC.exe 约好的接口）：事件名是 READY_PREFIX 接启动程序自己的进程号（十进制），
   经环境变量 READY_VAR 交给 ACC.exe；ACC.exe 第一次显示出悬浮窗以后打开它、触发它，并从自己的环境里去掉这个变量。没有这个
   变量（开发版、直接双击 ACC.exe）时 ACC.exe 什么都不做 */
#define READY_VAR L"ACC_READY_EVENT"
#define READY_PREFIX L"Local\\AstralChipCompass.Ready."
#define READY_WAIT_MS 15000 /* 正常双击时最多等悬浮窗这么久（毫秒） */
#define COMPAT_VAR L"__COMPAT_LAYER" /* 兼容性设置经它传给子进程，带起之前去掉（见文件开头） */
#define CRASH_FILE L"\\.tmp\\crash.txt" /* 崩溃记录：接在 app 文件夹后面，由 ACC.exe 写；错误记录 overlay_err.txt 在同一文件夹 */

/* 弹窗的标题和各种原因的提示。%lu 是十进制的错误代码 */
static const wchar_t TITLE[] = L"拾星助手启动失败";
static const wchar_t MSG_NO_APP[] = L"找不到 app\\ACC.exe，请把压缩包整个重新解压。";
static const wchar_t MSG_LONG_PATH[] = L"无法启动 app\\ACC.exe：程序所在文件夹的路径太长。"
                                       L"请把整个文件夹移到路径较短的位置（例如 D:\\拾星助手）后再打开。";
static const wchar_t MSG_START_FAILED[] = L"无法启动 app\\ACC.exe（错误代码 %lu）。请把压缩包整个重新解压。";
static const wchar_t MSG_BLOCKED[] = L"无法启动 app\\ACC.exe（错误代码 %lu），可能被杀毒软件拦截。"
                                     L"请在杀毒软件中把程序文件夹设为信任，或从隔离区恢复被隔离的文件。";
static const wchar_t MSG_APP_CONTROL[] = L"无法启动 app\\ACC.exe（错误代码 %lu），被 Windows 的应用控制拦截"
                                         L"（拾星助手没有数字签名）。个人电脑上多半是「智能应用控制」，可以在"
                                         L"「Windows 安全中心 → 应用和浏览器控制 → 智能应用控制」中查看；"
                                         L"单位管理的电脑请联系电脑管理员。";
static const wchar_t MSG_POLICY[] = L"无法启动 app\\ACC.exe（错误代码 %lu），这台电脑的组策略禁止运行它。请联系电脑管理员。";
static const wchar_t MSG_CLOUD[] = L"无法启动 app\\ACC.exe（错误代码 %lu）：程序文件在 OneDrive 一类云盘的同步文件夹里，"
                                   L"还没有下载到这台电脑上。请把整个拾星助手文件夹挪到不同步的位置（例如 D:\\拾星助手）"
                                   L"后再打开。";

/* 带起以后、悬浮窗出来之前出错退出（见 crashed）：第一个 %s 是错误代码（CRASH_HEX）或退出码（CRASH_DEC），第二个是白话原因；
   空一行接 MSG_REPORT_HEAD、崩溃记录的完整路径、MSG_REPORT_TAIL */
static const wchar_t MSG_CRASHED[] = L"拾星助手启动后出错退出（%s）：%s。";
static const wchar_t CRASH_HEX[] = L"错误代码 0x%08lX";
static const wchar_t CRASH_DEC[] = L"退出码 %lu";
static const wchar_t MSG_REPORT_HEAD[] = L"反馈问题时请附上崩溃记录 ";
static const wchar_t MSG_REPORT_TAIL[] = L" 和同一文件夹里的错误记录 overlay_err.txt。";
/* 出错退出的白话原因，按 Windows 的异常代码（NTSTATUS）查；名字见各行的注释 */
static const struct {
    DWORD code;
    const wchar_t *text;
} CRASH_REASONS[] = {
    {0xC000001D, L"处理器不支持程序用到的指令"},              /* STATUS_ILLEGAL_INSTRUCTION */
    {0xC0000096, L"处理器不支持程序用到的指令"},              /* STATUS_PRIVILEGED_INSTRUCTION */
    {0xC0000005, L"程序访问内存出错"},                        /* STATUS_ACCESS_VIOLATION */
    {0xC0000135, L"缺少程序要用的文件，请把压缩包整个重新解压"}, /* STATUS_DLL_NOT_FOUND */
    {0xC0000142, L"程序要用的组件初始化失败"},                /* STATUS_DLL_INIT_FAILED */
    {0xC000007B, L"程序文件已损坏，请把压缩包整个重新解压"},    /* STATUS_INVALID_IMAGE_FORMAT */
    {0xC0000409, L"程序检测到严重错误后自行结束"},            /* STATUS_STACK_BUFFER_OVERRUN（abort 也报它） */
    {0xC00000FD, L"程序出错（栈溢出）"},                      /* STATUS_STACK_OVERFLOW */
    {0xC0000017, L"内存不足，请关掉一些程序后再打开"},          /* STATUS_NO_MEMORY */
    {0xC000012D, L"内存不足，请关掉一些程序后再打开"},          /* STATUS_COMMITMENT_LIMIT */
    {0xC0000374, L"程序的内存数据损坏"},                      /* STATUS_HEAP_CORRUPTION */
};
static const wchar_t CRASH_OTHER[] = L"程序异常退出";                        /* 表里没有的异常代码 */
static const wchar_t CRASH_EXIT_CODE[] = L"程序启动时出错，请把压缩包整个重新解压"; /* 不是异常代码的退出码 */

/* 启动不了的原因，决定提示哪一句 */
enum cause {
    CAUSE_MISSING,     /* app\ACC.exe 不在：MSG_NO_APP */
    CAUSE_LONG_PATH,   /* 文件在，路径太长 Windows 不肯启动：MSG_LONG_PATH */
    CAUSE_BROKEN,      /* 不是有效的程序文件：MSG_START_FAILED */
    CAUSE_BLOCKED,     /* 拒绝访问、报有病毒：MSG_BLOCKED */
    CAUSE_APP_CONTROL, /* 被 Windows 的应用控制拦下：MSG_APP_CONTROL */
    CAUSE_POLICY,      /* 被组策略禁止：MSG_POLICY */
    CAUSE_CLOUD,       /* 云盘里取不下来的占位文件：MSG_CLOUD */
    CAUSE_OTHER        /* 其他：MSG_START_FAILED，空一行接 Windows 对这个错误的说明 */
};

/* 找不到文件、找不到路径、文件名太长、目录名无效：可能是 app\ACC.exe 真的不在（没有 app 文件夹时报的是目录名无效：
   工作目录不在），也可能文件在、只是路径太长（app 文件夹的路径超过 258 个字时，CreateProcessW 不接受这么长的工作目录，
   报目录名无效；没开长路径支持的电脑上，程序的路径超过 MAX_PATH 时也可能报找不到路径或文件名太长） */
static const DWORD PATH_ERRORS[] = {ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND, ERROR_FILENAME_EXCED_RANGE,
                                    ERROR_DIRECTORY};
/* 不是有效的程序文件：文件坏了（解压不完整、下载不完整的文件也报这一类） */
static const DWORD BROKEN_ERRORS[] = {ERROR_BAD_FORMAT, ERROR_INVALID_EXE_SIGNATURE, ERROR_EXE_MARKED_INVALID,
                                      ERROR_BAD_EXE_FORMAT, ERROR_EXE_MACHINE_TYPE_MISMATCH, ERROR_FILE_CORRUPT};
/* 拒绝访问、文件有病毒、文件因有病毒已被删除：多半是杀毒软件拦截或隔离了 */
static const DWORD BLOCKED_ERRORS[] = {ERROR_ACCESS_DENIED, ERROR_VIRUS_INFECTED, ERROR_VIRUS_DELETED};
/* 被 Windows 的应用控制拦下：「智能应用控制」或单位的应用控制策略（WDAC）不让没签名的程序运行。winerror.h 里
   ERROR_SYSTEM_INTEGRITY_* 一族中拦下程序时报的几个（策略违规、按信誉拦下的七种）；同族的另外几个是策略本身出错
   （回滚、策略无效、没签名、太多、补充策略未授权）和只管驱动的，不会是启动一个程序报的，不列。另加要求程序有有效签名的
   策略报的「无法验证数字签名」（ERROR_INVALID_IMAGE_HASH）：本工具的程序都没签名，碰上它只能是这类策略 */
static const DWORD APP_CONTROL_ERRORS[] = {ERROR_INVALID_IMAGE_HASH, ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION,
                                           ERROR_SYSTEM_INTEGRITY_REPUTATION_MALICIOUS,
                                           ERROR_SYSTEM_INTEGRITY_REPUTATION_PUA,
                                           ERROR_SYSTEM_INTEGRITY_REPUTATION_DANGEROUS_EXT,
                                           ERROR_SYSTEM_INTEGRITY_REPUTATION_OFFLINE,
                                           ERROR_SYSTEM_INTEGRITY_REPUTATION_UNFRIENDLY_FILE,
                                           ERROR_SYSTEM_INTEGRITY_REPUTATION_UNATTAINABLE,
                                           ERROR_SYSTEM_INTEGRITY_REPUTATION_EXPLICIT_DENY_FILE};
/* 组策略禁止运行：软件限制策略、AppLocker 都报前一个，软件限制策略设成不弹它自己的提示时报后一个 */
static const DWORD POLICY_ERRORS[] = {ERROR_ACCESS_DISABLED_BY_POLICY, ERROR_ACCESS_DISABLED_NO_SAFER_UI_BY_POLICY};
/* 程序文件在 OneDrive 一类云盘的同步文件夹里、只是云端的占位文件，取不下来（云盘程序没在运行、没联网、没登录等）：
   winerror.h 里 ERROR_CLOUD_FILE_* 一族全算，办法都是把文件夹挪到不同步的位置 */
static const DWORD CLOUD_ERRORS[] = {
    ERROR_CLOUD_FILE_SYNC_ROOT_METADATA_CORRUPT, ERROR_CLOUD_FILE_PROVIDER_NOT_RUNNING,
    ERROR_CLOUD_FILE_METADATA_CORRUPT, ERROR_CLOUD_FILE_METADATA_TOO_LARGE, ERROR_CLOUD_FILE_PROPERTY_BLOB_TOO_LARGE,
    ERROR_CLOUD_FILE_PROPERTY_BLOB_CHECKSUM_MISMATCH, ERROR_CLOUD_FILE_TOO_MANY_PROPERTY_BLOBS,
    ERROR_CLOUD_FILE_PROPERTY_VERSION_NOT_SUPPORTED, ERROR_CLOUD_FILE_NOT_IN_SYNC, ERROR_CLOUD_FILE_ALREADY_CONNECTED,
    ERROR_CLOUD_FILE_NOT_SUPPORTED, ERROR_CLOUD_FILE_INVALID_REQUEST, ERROR_CLOUD_FILE_READ_ONLY_VOLUME,
    ERROR_CLOUD_FILE_CONNECTED_PROVIDER_ONLY, ERROR_CLOUD_FILE_VALIDATION_FAILED,
    ERROR_CLOUD_FILE_AUTHENTICATION_FAILED, ERROR_CLOUD_FILE_INSUFFICIENT_RESOURCES,
    ERROR_CLOUD_FILE_NETWORK_UNAVAILABLE, ERROR_CLOUD_FILE_UNSUCCESSFUL,
    ERROR_CLOUD_FILE_NOT_UNDER_SYNC_ROOT, ERROR_CLOUD_FILE_IN_USE, ERROR_CLOUD_FILE_PINNED,
    ERROR_CLOUD_FILE_REQUEST_ABORTED, ERROR_CLOUD_FILE_PROPERTY_CORRUPT, ERROR_CLOUD_FILE_ACCESS_DENIED,
    ERROR_CLOUD_FILE_INCOMPATIBLE_HARDLINKS, ERROR_CLOUD_FILE_PROPERTY_LOCK_CONFLICT, ERROR_CLOUD_FILE_REQUEST_CANCELED,
    ERROR_CLOUD_FILE_PROVIDER_TERMINATED, ERROR_CLOUD_FILE_REQUEST_TIMEOUT, ERROR_CLOUD_FILE_DEHYDRATION_DISALLOWED,
    ERROR_CLOUD_FILE_US_MESSAGE_TIMEOUT};

static wchar_t self_path[BUF];
static wchar_t exe_path[BUF];
static wchar_t dir_path[BUF];
static wchar_t cmd_line[BUF];
static wchar_t probe_path[BUF];
static wchar_t message[BUF];
static wchar_t ready_name[64]; /* READY_PREFIX 接进程号 */
static HANDLE ready_event;     /* 正常双击时等的事件（watch_ready 建）；不等时为 NULL */

/* dst[*pos] 起接上 src；放不下时返回 0（dst 末尾总有结束符） */
static int append(wchar_t *dst, size_t *pos, const wchar_t *src) {
    while (*src) {
        if (*pos + 1 >= BUF) {
            dst[*pos] = L'\0';
            return 0;
        }
        dst[(*pos)++] = *src++;
    }
    dst[*pos] = L'\0';
    return 1;
}

#ifdef ACC_LAUNCHER_TEST
/* 测试版：写到标准错误输出（UTF-16LE），测试读它核对 */
static void write_err(const wchar_t *text) {
    HANDLE err = GetStdHandle(STD_ERROR_HANDLE);
    DWORD written;
    if (err != NULL && err != INVALID_HANDLE_VALUE)
        WriteFile(err, text, (DWORD)(lstrlenW(text) * sizeof(wchar_t)), &written, NULL);
}
#endif

static void show_error(const wchar_t *text) {
#ifdef ACC_LAUNCHER_TEST
    (void)TITLE;
    write_err(text);
#else
    MessageBoxW(NULL, text, TITLE, MB_OK | MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST);
#endif
}

static int listed(DWORD error, const DWORD *list, size_t count) {
    for (size_t i = 0; i < count; ++i)
        if (list[i] == error)
            return 1;
    return 0;
}

/* app\ACC.exe 确实不在：GetFileAttributesW 也报找不到文件或路径。查的是 \\?\ 形式的完整路径，不受 MAX_PATH 的限制——
   没开长路径支持的电脑上，超过 MAX_PATH 的普通路径也报找不到路径，那不说明文件不在。查不了时（拼不下、报别的错误）
   不算不在。 */
static int app_missing(void) {
    size_t p = 0;
    const wchar_t *tail = exe_path;
    int ok = 1;
    if (exe_path[0] == L'\\' && exe_path[1] == L'\\') {
        if (!((exe_path[2] == L'?' || exe_path[2] == L'.') && exe_path[3] == L'\\')) {
            ok = append(probe_path, &p, L"\\\\?\\UNC\\"); /* 网络路径 \\服务器\共享\…… */
            tail = exe_path + 2;
        } /* 已经是 \\?\ 或 \\.\ 开头的：照原样 */
    } else {
        ok = append(probe_path, &p, L"\\\\?\\");
    }
    if (!ok || !append(probe_path, &p, tail))
        return 0;
    if (GetFileAttributesW(probe_path) != INVALID_FILE_ATTRIBUTES)
        return 0;
    DWORD error = GetLastError();
    return error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND;
}

/* 启动 app\ACC.exe 时 Windows 报 error：启动不了的原因 */
static enum cause classify(DWORD error) {
    if (listed(error, PATH_ERRORS, ARRAYSIZE(PATH_ERRORS))) {
        if (app_missing())
            return CAUSE_MISSING;
        /* 文件在：路径真的长才算路径太长。路径不长时报这几个代码是别的原因（例如命令行参数太长时也报文件名太长，
           文件夹名末尾带点时报目录名无效），按其他原因提示，附上 Windows 的说明 */
        return lstrlenW(exe_path) >= MAX_PATH ? CAUSE_LONG_PATH : CAUSE_OTHER;
    }
    if (listed(error, BROKEN_ERRORS, ARRAYSIZE(BROKEN_ERRORS)))
        return CAUSE_BROKEN;
    if (listed(error, BLOCKED_ERRORS, ARRAYSIZE(BLOCKED_ERRORS)))
        return CAUSE_BLOCKED;
    if (listed(error, APP_CONTROL_ERRORS, ARRAYSIZE(APP_CONTROL_ERRORS)))
        return CAUSE_APP_CONTROL;
    if (listed(error, POLICY_ERRORS, ARRAYSIZE(POLICY_ERRORS)))
        return CAUSE_POLICY;
    if (listed(error, CLOUD_ERRORS, ARRAYSIZE(CLOUD_ERRORS)))
        return CAUSE_CLOUD;
    return CAUSE_OTHER;
}

/* 写明错误代码的那几种原因的提示（%lu 是十进制的错误代码）；文件坏了和其他原因用同一句 */
static const wchar_t *coded_message(enum cause cause) {
    switch (cause) {
    case CAUSE_BLOCKED:
        return MSG_BLOCKED;
    case CAUSE_APP_CONTROL:
        return MSG_APP_CONTROL;
    case CAUSE_POLICY:
        return MSG_POLICY;
    case CAUSE_CLOUD:
        return MSG_CLOUD;
    default:
        return MSG_START_FAILED;
    }
}

/* 启动不了：wait 模式只返回退出码；否则按原因弹窗 */
static int fail(enum cause cause, DWORD error, int wait) {
    if (!wait) {
        if (cause == CAUSE_MISSING) {
            show_error(MSG_NO_APP);
        } else if (cause == CAUSE_LONG_PATH) {
            show_error(MSG_LONG_PATH);
        } else {
            int n = wsprintfW(message, coded_message(cause), (unsigned long)error);
            if (cause == CAUSE_OTHER && n > 0) { /* 空一行接 Windows 对这个错误的说明（取不到时只有第一句） */
                wchar_t *detail = message + n + 2;
                DWORD room = (DWORD)(ARRAYSIZE(message) - (size_t)n - 2);
                DWORD len = FormatMessageW(FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS, NULL, error, 0,
                                           detail, room, NULL);
                while (len > 0 && (detail[len - 1] == L'\r' || detail[len - 1] == L'\n' || detail[len - 1] == L' '))
                    detail[--len] = L'\0';
                if (len > 0) {
                    message[n] = L'\n';
                    message[n + 1] = L'\n';
                }
            }
            show_error(message);
        }
    }
    return cause == CAUSE_MISSING ? EXIT_NO_APP : EXIT_START_FAILED;
}

#ifdef ACC_LAUNCHER_TEST
#define TEST_ERROR_VAR L"ACC_LAUNCHER_TEST_ERROR"
#define TEST_RUNAS_VAR L"ACC_LAUNCHER_TEST_RUNAS"
#define TEST_WAIT_VAR L"ACC_LAUNCHER_TEST_WAIT_MS"
/* 环境变量 name 是十进制数时返回它；没有、不是十进制数、太长时返回 0 */
static DWORD test_number(const wchar_t *name) {
    wchar_t buf[16];
    DWORD n = GetEnvironmentVariableW(name, buf, ARRAYSIZE(buf)), value = 0;
    if (n == 0 || n > 9)
        return 0;
    for (DWORD i = 0; i < n; ++i) {
        if (buf[i] < L'0' || buf[i] > L'9')
            return 0;
        value = value * 10 + (DWORD)(buf[i] - L'0');
    }
    return value;
}
#endif

/* 正常双击（rest 是命令行里程序名后面的部分）：不带任何参数，只有空白或什么都没有。按 C 运行库拆参数的规则，这时
   app\ACC.exe 拆出来也没有参数；带了参数的（哪怕是一对空引号）都不算 */
static int no_arguments(const wchar_t *rest) {
    while (*rest == L' ' || *rest == L'\t')
        ++rest;
    return *rest == L'\0';
}

/* 正常双击、带起之前：建「悬浮窗出来了」的事件（手动复位、未触发），名字是 READY_PREFIX 接自己的进程号，放进环境变量
   READY_VAR 让 ACC.exe 继承；再给自己加上 SEM_FAILCRITICALERRORS 让 ACC.exe 继承（为什么见文件开头）。建不了事件、这个
   名字已经有了（照理不会：只有别的进程还拿着以前同一个进程号的启动程序建的事件时才会这样，它可能已经触发过）、设不了
   环境变量时不等：ready_event 留 NULL，环境里也不留这个变量，错误模式不动，照旧带起就退出——这时没有这里的说明，
   Windows 自己的错误窗口得留着 */
static void watch_ready(void) {
    HANDLE event;
    wsprintfW(ready_name, L"%s%lu", READY_PREFIX, (unsigned long)GetCurrentProcessId());
    SetLastError(ERROR_SUCCESS);
    event = CreateEventW(NULL, TRUE, FALSE, ready_name);
    if (event == NULL)
        return;
    if (GetLastError() == ERROR_ALREADY_EXISTS || !SetEnvironmentVariableW(READY_VAR, ready_name)) {
        CloseHandle(event);
        return;
    }
    ready_event = event;
    SetErrorMode(GetErrorMode() | SEM_FAILCRITICALERRORS);
}

/* 等悬浮窗最多等多久（毫秒）：READY_WAIT_MS；测试版里环境变量 TEST_WAIT_VAR 是十进制数（不为 0）时用它 */
static DWORD ready_wait_ms(void) {
#ifdef ACC_LAUNCHER_TEST
    DWORD ms = test_number(TEST_WAIT_VAR);
    if (ms != 0)
        return ms;
#endif
    return READY_WAIT_MS;
}

/* 退出码 code 的白话原因：表里有的照表，其余的异常代码写 CRASH_OTHER */
static const wchar_t *crash_reason(DWORD code) {
    for (size_t i = 0; i < ARRAYSIZE(CRASH_REASONS); ++i)
        if (CRASH_REASONS[i].code == code)
            return CRASH_REASONS[i].text;
    return CRASH_OTHER;
}

/* app\ACC.exe 带起以后、悬浮窗出来之前结束了，退出码 code 不是 0 也不是 EXIT_REPORTED：弹窗说明，不然双击以后什么都看不到。
   0x80000000 起的是 Windows 的异常代码（NTSTATUS：崩溃时进程的退出码就是异常代码；断点 0x80000003 这类警告级的也算，
   写成十进制是一长串看不懂的数），按十六进制写错误代码，原因照 CRASH_REASONS。其余的（例如 1）多半是程序还没来得及弹窗
   就出错了（例如缺少 Python 的组件），写十进制的退出码，提示重新解压。空一行写反馈时附上崩溃记录（app 文件夹的
   .tmp\crash.txt，写完整路径，用户照着就找得到）和同一文件夹里的错误记录。这里不查它们在不在（启动程序不碰文件）：
   程序还没来得及写时，用户去找的时候自然看得到没有。 */
static void crashed(DWORD code) {
    wchar_t head[32];
    size_t p;
    int n;
    if (code >= 0x80000000UL)
        wsprintfW(head, CRASH_HEX, (unsigned long)code);
    else
        wsprintfW(head, CRASH_DEC, (unsigned long)code);
    n = wsprintfW(message, MSG_CRASHED, head, code >= 0x80000000UL ? crash_reason(code) : CRASH_EXIT_CODE);
    p = n > 0 ? (size_t)n : 0;
    if (append(message, &p, L"\n\n") && append(message, &p, MSG_REPORT_HEAD) && append(message, &p, dir_path) &&
        append(message, &p, CRASH_FILE))
        append(message, &p, MSG_REPORT_TAIL);
    show_error(message);
}

/* 正常双击、带起了 app\ACC.exe（process）：同时等悬浮窗出来的事件和它结束，最多 ready_wait_ms 毫秒：等着才接得住启动时的崩溃。
   事件排在前面：悬浮窗出来以后它马上又结束了（两样都已触发）时算悬浮窗出来了，那以后再出什么事不归这里管。
   - 事件触发、到时间、等不了（照理不会）：安静退出，退出码 0。到时间多半是启动得慢、以管理员身份启动收不到信号（见
     run_as_admin），或者它在问用户事情（例如已有一个在运行却没有响应时）。
   - 它先结束：退出码就是它的退出码；是 0（例如已有一个在运行，请那一个显示悬浮窗后退出）或 EXIT_REPORTED（它自己弹过窗）
     时安静退出，其余弹窗说明（crashed）。 */
static DWORD watch(HANDLE process) {
    HANDLE handles[2];
    DWORD code = 0;
    handles[0] = ready_event;
    handles[1] = process;
    if (WaitForMultipleObjects(2, handles, FALSE, ready_wait_ms()) != WAIT_OBJECT_0 + 1)
        return 0;
    if (!GetExitCodeProcess(process, &code))
        return 0;
    if (code != 0 && code != EXIT_REPORTED)
        crashed(code);
    return code;
}

/* 带起了 app\ACC.exe，process 是它的进程句柄：把「可以把窗口放到前台」的权限交给它；wait 模式等它结束，退出码就是它的
   退出码；正常双击（建好了事件）时等悬浮窗出来（watch）；其余带起就退出，退出码 0。process 为 NULL（ShellExecuteExW
   成功了却没给句柄，照理不会）时既交不了也等不了，以 0 退出 */
static int started(HANDLE process, int wait) {
    DWORD code = 0, pid;
    if (process == NULL)
        return 0;
    pid = GetProcessId(process);
    if (pid != 0)
        AllowSetForegroundWindow(pid);
    if (wait) {
        WaitForSingleObject(process, INFINITE);
        if (!GetExitCodeProcess(process, &code))
            code = EXIT_START_FAILED;
    } else if (ready_event != NULL) {
        code = watch(process);
    }
    CloseHandle(process);
    return (int)code;
}

#ifdef ACC_LAUNCHER_TEST
/* 测试版里代替 ShellExecuteExW（不弹管理员确认框）：把交给它的几项写成一行到标准错误输出——
   动词|fMask（十六进制）|nShow|工作目录|程序|参数（没有参数时空着），换行结尾。环境变量 TEST_RUNAS_VAR 是十进制数（不为 0）
   时当作 ShellExecuteExW 报了这个错误代码（1223 即在确认框中点了「否」）；否则当作点了「是」：以普通权限用 CreateProcessW
   启动同一个程序代替（命令行、工作目录同正常启动时），交回它的进程句柄，后面照常交出前台权限、等它结束或等悬浮窗。
   照真的以管理员身份启动那样，子进程拿不到 READY_VAR（见 run_as_admin）：先从环境里去掉它，测试按收不到信号的样子核对 */
static BOOL test_shell_execute(SHELLEXECUTEINFOW *info) {
    wchar_t head[64];
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    DWORD error;
    wsprintfW(head, L"%s|0x%08lX|%d|", info->lpVerb, (unsigned long)info->fMask, info->nShow);
    write_err(head);
    write_err(info->lpDirectory);
    write_err(L"|");
    write_err(info->lpFile);
    write_err(L"|");
    if (info->lpParameters != NULL)
        write_err(info->lpParameters);
    write_err(L"\n");
    error = test_number(TEST_RUNAS_VAR);
    if (error != 0) {
        SetLastError(error);
        return FALSE;
    }
    SetEnvironmentVariableW(READY_VAR, NULL);
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));
    if (!CreateProcessW(exe_path, cmd_line, NULL, NULL, FALSE, 0, NULL, dir_path, &si, &pi))
        return FALSE;
    CloseHandle(pi.hThread);
    info->hProcess = pi.hProcess;
    return TRUE;
}
#define shell_execute test_shell_execute
#else
#define shell_execute ShellExecuteExW
#endif

/* CreateProcessW 报 740：app\ACC.exe 要管理员权限（有人在它的属性里勾了「以管理员身份运行」）。和直接双击它时一样，改用
   ShellExecuteExW 以 runas 启动，由 Windows 弹管理员确认框（用户账户控制）：
   - 点「是」：照常运行，后面同 CreateProcessW 启动成功时（started）。参数原样转交：args 去掉开头的空白就是 lpParameters
     （ShellExecuteExW 拼命令行时自己在程序名后面加空格，C 运行库拆出来的参数不变）；工作目录同样设为 app。
     正常双击时照样等悬浮窗（watch），但一般收不到信号：以管理员身份运行的进程不是启动程序起的，是 Windows 的「应用程序
     信息」服务在用户点「是」以后替它起的，环境变量按用户自己的那一套重新给，启动程序设的 READY_VAR（和去掉的 COMPAT_VAR、
     加上的错误模式）都传不过去，交给 ShellExecuteExW 的参数里也没有地方放环境变量。所以这种情形下等满 15 秒或等它结束，
     结束了照退出码判断，同 CreateProcessW 启动时。
   - 点「否」（ERROR_CANCELLED）：不启动、不提示，退出码 EXIT_START_FAILED。
   - 报别的错误：照 classify 分原因提示（wait 模式只给退出码）。ShellExecuteExW 自己不弹错误窗口（SEE_MASK_FLAG_NO_UI）。
   其余选项：要回进程句柄（SEE_MASK_NOCLOSEPROCESS）；做完再返回（SEE_MASK_NOASYNC：启动程序随后就退出）；不做下载文件的
   安全检查（SEE_MASK_NOZONECHECKS）：CreateProcessW 本来就不做，从网上下载的压缩包解压出来的文件带着来源标记，
   ShellExecuteExW 查它可能在确认框之前多弹一次安全警告。确认框的所有者窗口取当时的前台窗口：双击时就是双击所在的资源管理器
   窗口，和直接双击 app\ACC.exe 时资源管理器给的一样（不给窗口时，Windows 可能把它当作后台程序的请求，只在任务栏上闪烁）。 */
static int run_as_admin(const wchar_t *args, int wait) {
    SHELLEXECUTEINFOW info;
    while (*args == L' ' || *args == L'\t')
        ++args;
    ZeroMemory(&info, sizeof(info));
    info.cbSize = sizeof(info);
    info.fMask = RUNAS_MASK;
    info.hwnd = GetForegroundWindow();
    info.lpVerb = L"runas";
    info.lpFile = exe_path;
    info.lpParameters = *args ? args : NULL;
    info.lpDirectory = dir_path;
    info.nShow = SW_SHOWNORMAL;
    if (!shell_execute(&info)) {
        DWORD error = GetLastError();
        if (error == ERROR_CANCELLED)
            return EXIT_START_FAILED;
        if (error == ERROR_SUCCESS) /* 没给错误代码（照理不会）：按要管理员权限说明 */
            error = ERROR_ELEVATION_REQUIRED;
        return fail(classify(error), error, wait);
    }
    return started(info.hProcess, wait);
}

/* CreateProcessW 报 error（测试版里也可能是注入的）：要管理员权限时改弹确认框启动，其余按原因提示 */
static int not_started(DWORD error, const wchar_t *args, int wait) {
    if (error == ERROR_ELEVATION_REQUIRED)
        return run_as_admin(args, wait);
    return fail(classify(error), error, wait);
}

/* 命令行里第一项（程序名）后面的部分：从第一项后面的空白（或结尾）开始，原样返回 */
static const wchar_t *after_program_name(const wchar_t *cmd) {
    int quoted = 0;
    for (; *cmd; ++cmd) {
        if (*cmd == L'"')
            quoted = !quoted;
        else if (!quoted && (*cmd == L' ' || *cmd == L'\t'))
            break;
    }
    return cmd;
}

/* rest 的第一个参数正好是 WAIT_ARG 时，返回它后面的部分（从后面的空白或结尾开始）；不是时返回 NULL */
static const wchar_t *skip_wait_arg(const wchar_t *rest) {
    const wchar_t *p = rest, *q = WAIT_ARG;
    while (*p == L' ' || *p == L'\t')
        ++p;
    while (*q && *p == *q) {
        ++p;
        ++q;
    }
    if (*q == L'\0' && (*p == L'\0' || *p == L' ' || *p == L'\t'))
        return p;
    return NULL;
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR args, int show) {
    (void)instance;
    (void)previous;
    (void)args;
    (void)show;
    const wchar_t *rest = after_program_name(GetCommandLineW());
    const wchar_t *waited = skip_wait_arg(rest);
    int wait = waited != NULL;
    if (wait)
        rest = waited;
    /* 不论哪种启动方式都不把兼容性设置传给 ACC.exe；READY_VAR 只在正常双击时由 watch_ready 设上（见文件开头） */
    SetEnvironmentVariableW(COMPAT_VAR, NULL);
    SetEnvironmentVariableW(READY_VAR, NULL);

    DWORD n = GetModuleFileNameW(NULL, self_path, BUF);
    if (n == 0)
        return fail(CAUSE_OTHER, GetLastError(), wait);
    if (n >= BUF) /* 自己的路径放不下：路径太长 */
        return fail(CAUSE_LONG_PATH, ERROR_FILENAME_EXCED_RANGE, wait);
    size_t cut = 0; /* 启动程序所在的文件夹（含末尾的反斜杠）的长度 */
    for (size_t i = 0; i < n; ++i)
        if (self_path[i] == L'\\' || self_path[i] == L'/')
            cut = i + 1;
    self_path[cut] = L'\0';
    size_t e = 0, d = 0, c = 0;
    if (!append(exe_path, &e, self_path) || !append(exe_path, &e, APP_EXE) || !append(dir_path, &d, self_path) ||
        !append(dir_path, &d, APP_DIR))
        return fail(CAUSE_LONG_PATH, ERROR_FILENAME_EXCED_RANGE, wait);
    /* 命令行拼不下（参数太长）：和 CreateProcessW 嫌命令行太长时一样，按报了文件名太长分原因 */
    if (!append(cmd_line, &c, L"\"") || !append(cmd_line, &c, exe_path) || !append(cmd_line, &c, L"\"") ||
        !append(cmd_line, &c, rest))
        return fail(classify(ERROR_FILENAME_EXCED_RANGE), ERROR_FILENAME_EXCED_RANGE, wait);
    if (!wait && no_arguments(rest))
        watch_ready(); /* 要管理员权限、改用 ShellExecuteExW 时也用它，所以在下面任何一种启动之前建 */

#ifdef ACC_LAUNCHER_TEST
    DWORD injected = test_number(TEST_ERROR_VAR);
    if (injected != 0)
        return not_started(injected, rest, wait);
#endif
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));
    if (!CreateProcessW(exe_path, cmd_line, NULL, NULL, FALSE, 0, NULL, dir_path, &si, &pi))
        return not_started(GetLastError(), rest, wait); /* 分原因时还要调别的接口，先把错误代码取出来 */
    CloseHandle(pi.hThread);
    return started(pi.hProcess, wait);
}
