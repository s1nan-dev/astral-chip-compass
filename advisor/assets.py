"""识别用图片的首次运行下载。

角色照（heroes）、Q 版头像（profiles）、筹码图标（chips）和结算数据表上的称号图标（achieve）是游戏美术素材，发行版不附带，
由用户电脑在首次运行时从中文 BWIKI 下载到 paths.download_dir() 下（发行版是个人目录
%LOCALAPPDATA%\\AstralChipCompass\\assets，
所有版本共用一份，换新版本只下新出的、改过的；开发版是项目的 assets/），只供本机识别使用。随发行版附带的（清单项带
bundled）在程序目录的 assets/ 下（paths.assets_dir）。下面说的 assets/ 都指 download_dir() 这一层。
清单项可以带 api 字段，指向图片所在的另一个 wiki 的接口（现在的清单里没有这样的项）。
要下载哪些文件、从哪里下、大小和 SHA-1 校验值，都写在 data/assets_manifest.json

BWIKI 上只有整张卡面原画、没有角色照的皮肤，和 BWIKI 的角色照跟游戏里不一样（裁的位置、画面不同）的皮肤：
这些清单项带 crop 字段（裁法的 6 个数，照游戏内核对），title、url、size、sha1 是卡面的。下到卡面、核过校验值以后按裁法裁出角色照（advisor/vision/cardcrop.py），照常写成 .part、
刷盘、改名；卡面只在内存里，不落盘。清单项没写卡面的校验值时不下、不裁（下到的卡面核对不了，不交去解码）。裁图要解 PNG、
用 OpenCV，在识别那一侧；本模块在底层、不导入它，由上层用 use_cropper 接上（悬浮窗在 advisor/ui/services.py 里接：只登记
一个导入裁图模块的函数，这一轮下载有要现裁的图片时才导入）。裁出来的角色照和清单里卡面的大小对不上，齐不齐另按本地覆盖
清单里的一笔记录判断：这张是哪张卡面（SHA-1）按哪份裁法裁的（见 is_present）。

  missing()                      → 还缺的图片（清单项的列表，识别先用到的排在前面，见 ORDER）；个别下载失败、记了日期的
                                   当天不算（见下载规矩「个别图片一直下不了」）
  retry_items()                  → 用户点「重试」时要下载的图片（近期不自动重试的也算）
  forget_recent(names)           → 用户点「重试」时先去掉这几张近期不再自动下载的记号（这一下被拦时到点也接着下它们）
  download(items, progress, stop, stats=) → (成功的文件名列表, [(失败的文件名, 原因)])；给了 stats 时另填这一轮的
                                    用时和最慢的几张（见 download 的说明）
  isolated(failed)、deferred(failed) → 一轮下载的失败是不是「个别图片下不了」、其中隔天再自动下载的那几张
  cooldown()                     → 被站点防护拦下以后的冷却记录（Cooldown）；没被拦过、或已经下全时为 None
  refusal_note(error)            → 开发用的取图工具被 wiki 拒绝时打印的一句（几点几分以后再运行）；别的出错为 None
  use_cropper(crop, load=)       → 接上从卡面现裁角色照的函数（crop(卡面的字节, 裁法) → 角色照的字节；或 load() → 这个函数，
                                   第一次要裁图时才调用）
  note_crop(name, card, crop, size) → 记下现裁的角色照是哪张卡面按哪份裁法裁的（开发用的取图工具裁好以后调用）
  fetch_remote_manifest(stop)    → 取识别图片的最新清单、核对、更新的才存下（REMOTE_SAVED 等结果，见「最新清单」）
  load_manifest()                → 要用的清单：自带的（load_bundled），官方仓库的版本再和存着的最新清单合起来
  new_heroes()                   → 存着的最新清单里的新角色（{编号: NewHero}）
  use_hero_ids(ids, load=)       → 登记这台程序认识的角色编号：最新清单多出来的角色照、Q 版头像只下认识的

最新清单：游戏出了新造型、新角色时，随程序带的清单（data/assets_manifest.json）里没有这几张图，不更新程序就认不出。备用
地址上另放一份完整的最新清单（update.ASSETS_LATEST），官方仓库的版本取回来存在个人目录（REMOTE_FILE，几个版本共用），
和自带的合起来用（_merge），多出来的图照 missing()、download() 下。这份清单决定程序去哪下东西、把什么文件写进识别图片的
文件夹：核对很严（parse_remote：只许那几类、合各类命名的 .png 文件名、只许 BWIKI 的图床和备用地址上的地址），哪一条不过
整份不用、记一条错误记录，照自带的清单走，别的功能不受影响；读存着的那份时再核一遍。

下载规矩：
- 带写明本工具名称、源代码的地址和用途的 User-Agent（不冒充浏览器，也不用别的办法绕过站点防护）；系统或环境变量设了代理时
  经代理下载。
- 不给站点添压力：整个这一轮下载每秒最多开始 RATE 个请求（各条连接、各段合在一起算，相邻两个请求的开始至少隔 1/RATE 秒），
  同时最多 WORKERS 条连接（复用连接，不每张都重新握手）。请求有多密只由 RATE 管；连接多开几条是为了把等图床的时间叠起来：
  图床节点上没有缓存时每张要回源站取，一张常要零点几秒，一条连接上一张接一张地等，请求再稀也下得慢（见 RATE 的说明）。
  向接口（imageinfo）要地址和校验值时 API_BATCH 张一批，接口请求之间至少隔 API_INTERVAL 秒（接口比图床更容易被拦）。
  一轮分几段下（见 download），各段共用这个每秒的上限；每段新建的连接，第一个请求也和上一段的请求隔开（_Pace）。
- 被拦就全部停下（Blocked）：BWIKI 前面的站点防护按电脑的网络地址拦截，拦下以后还接着请求只会把封禁拖长，这段时间
  用户用浏览器也打不开 BWIKI。认作被拦的：HTTP 567（请求被站点的安全策略拦截）；拿到的是网页而不是图片或接口的数据
  （拦截页、验证页，状态码可能是 200，也可能是带页面的 403）；限流（429、503）或不带页面的 403 连着 REJECT_LIMIT 次
  （同一阵被拒只算一次，见下一条）；限流要求等得比 RETRY_AFTER_MAX 还久；冷却期间的试探请求被拒。任何一条连接认出被拦，
  所有下载线程马上停下：不开始新的（正等着时刻的也不再发），也不重试；别的连接上已经发出去的，回话到了也不写文件。这一轮
  没下的不算失败，原因记作 PAUSED（暂停），已经下好的照常用。
- 限流单独一次（第一次 429、503）：所有下载线程按 Retry-After 一起等（没给时等 RETRY_AFTER 秒），再接着下；已经领了时刻、
  还在等的请求也不发，一起等过以后重新领（_Fetcher._turn）。同一阵被拒只算一次（_Gate.rejected）：算进「连着被拒」的那一次
  被拒（限流、不带页面的 403）回来的那一刻已经发出去、还在路上的请求，回来也被拒的，不再往上加（限流的照样按它的
  Retry-After 一起等）；那之后才发出去的请求又被拒，才算连着又一次。几条连接同时在下，站点开始拒绝的那一刻路上常有好几个
  请求，它们回来多半一起被拒：每个都算的话，连接一多，单独一次限流也会被当成连着被拒、冷却半个多小时。567、拦截页面这类
  一次就按被拦算，不受这条影响；中间有照常回话的，照旧从头数。
- 冷却（Cooldown，记在 assets/download-cooldown.json，程序重启也认）：被拦的时刻加 COOLDOWN_MINUTES 分钟以后再试，
  Retry-After 更长就按它；再被拦冷却时间翻倍，最多 COOLDOWN_MAX_MINUTES 分钟。冷却期间悬浮窗不发起下载，到点（没开着
  的，下次启动时过了点）再下；被拦过以后接着下时用更慢的速度（SLOW_WORKERS 条连接，每次隔 SLOW_INTERVAL 秒）。冷却期间
  调用 download（用户点了「重试」）只试一个请求：通了就按慢速接着下；仍被拒（或这个请求没得到回话）时照旧等到原来的
  时刻，冷却时间不重算、不翻倍（用户自己点的一个请求不会加重封禁）。下全了清掉这条记录。
  记录里另记认出被拦的是哪一张（Cooldown.item）；接着下时（到点、点「重试」、启动时冷却已过）把它排到最后，冷却期间的
  试探请求也就不用它（还缺的不止一张时）：认出被拦只看这一张的回话，也可能是这一张自己的问题（例如图床对它一直回网页、
  整站其实没拦），每一轮都从它开始的话，排在它后面的图永远下不到。问接口的那一批被拦时不记（不是哪一张图的问题）。
- 每个文件最多试 RETRIES 次（网络出错、5xx），等待期间取消（退出程序）立即返回；取消时下载的各层见到停止标志就收手：
  连接、发请求、重试之前都看，正文按块读、每块之间也看（见 download）；下载到 .part 临时文件，校验通过后才改名，
  半截文件不会被当成好的。
- 下载好了、写不进识别图片的文件夹的（个人目录的磁盘满了、被杀毒软件拦了；退回程序文件夹时程序文件夹放在受保护的文件夹、
  只读的位置）不再重试，原因以 WRITE_FAILED 开头，和网络出错分开（is_write_error）：失败的全是写盘出错时，悬浮窗换成写不进
  的提示（按图片在个人目录还是程序文件夹里写），不再说检查网络。

  不记的：被站点防护拦下暂停（冷却另管）、取消、写不进（换个位置才写得进，悬浮窗另有提示）、没接上裁图（程序的问题）、
  wiki 上已删除（另记日期）、最后报成限流（429、503）的：几条下载线程交替收到正常回话、凑不到连着 REJECT_LIMIT 次被拒时，
  一张图试满次数也可能一直是限流，这说明的是站点那边忙，不是这一张的问题，下次启动照常再下。这一轮被拦下、取消了时一张
  都不记：没下完，分不清是个别图片下不了还是整个连不上。
  下载线程建不了下载会话（读不了系统的证书、代理设置不对等，原因以 NO_SESSION 开头）时也一张都不记：这不是哪一张图的
  问题，对这一轮的每一张都一样，照全部或大部分没下下来算（悬浮窗照旧提示检查网络），下次启动照常再下。
  这一轮失败的总张数超过 FAILED_MANY 张时也不算个别图片下不了，一张都不记：按缺了几张算，不分哪一类失败（这一张自己的
  问题、写不进、wiki 上已删除、没接上裁图都算进去）。多半是这台电脑整个连不上（断网、第一次运行时没联网）或大批写不进
  （磁盘满了），下次启动要照常全下，不能让几百张都等一天，悬浮窗也不能说「其余识别照常」。不按「这一轮一张都没下好」
  来分：只缺一张时那一张失败了也是一张都没下好，那正是要隔天再试的情形。
- 卡面和别的图片一视同仁：被拦、限流、冷却、慢速、取消、写不进文件夹照上面的规矩；卡面在 wiki 上更新过的，同样问接口、
  记进覆盖清单。卡面解不开、裁出来是空的，这一张算失败（原因以 CROP_FAILED 开头），不重试：再下一次也一样。
- 本地覆盖清单写不进（被杀毒软件、同步软件正开着它）时稍等再试（_write_local），仍写不进就算了：记不下的只是下次启动时
  再下一遍（现裁的角色照没有记录就不算在，要重下卡面再裁）。点「重试」时去不掉的记号，这次运行里记在内存里
  （forget_recent）。
- 发行版的本地覆盖清单在个人目录里，几个版本共用：这一版不认识的顶层节（以后的版本加的）原样留着、照原样写回
  （load_local），开回旧一些的版本时不把新版本记的写丢。
"""
from __future__ import annotations

import dataclasses
import hashlib
import http.client
import json
import math
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timezone
from typing import Callable, Iterable

from . import clock, errors, fileio, paths, update, winhttp
from .version import SOURCE_URL, VERSION

MANIFEST = "assets_manifest.json"
# assets/ 下：wiki 上更新过、删除了的图片，现裁的角色照，个别下载失败的图片（见模块说明）
LOCAL_MANIFEST = "manifest-local.json"
# 覆盖清单里这一版认得的几节（load_local 逐项核对类型）；不认识的顶层节原样留着、照原样写回（见 load_local）
LOCAL_SECTIONS = ("updated", "gone", "cropped", "failed")
# 本地覆盖清单被别的程序开着、写不进时（杀毒软件、同步软件正读着它）：fileio 自己已经稍等再试了约 1 秒，还开着的再隔这么久
# （秒）各试一次（_write_local）。记不下现裁的记录，下次启动就要把那几张卡面重下一遍
LOCAL_RETRY_DELAYS = (0.0, 0.3, 0.6)
COOLDOWN_FILE = "download-cooldown.json"  # assets/ 下：被站点防护拦下以后的冷却记录（见模块说明「冷却」）
GONE_RETRY_DAYS = 7
# 个别图片下载失败（见模块说明「个别图片一直下不了」）：记下的日期起这么多天以内启动时不再自动下载，1 是隔天再试
FAILED_RETRY_DAYS = 1
# 一轮下载失败的图片超过这么多张时（总张数，不分哪一类：这一张自己的问题、写不进、wiki 上已删除、没接上裁图都算），不算
# 个别图片下不了，一张都不记：个别下不了的通常只有一两张（连不上某个图床、wiki 上某张图坏了）；几十上百张一起失败的多半是
# 整个连不上或大批写不进，下次启动要照常全下，悬浮窗照旧提示检查网络
FAILED_MANY = 20
API = "https://wiki.biligame.com/starengine/api.php"
WIKI_HOME = "https://wiki.biligame.com/starengine/"  # 图片来源（清单的 source）
# 写明工具名称、版本、源代码的地址和用途，便于站点运营方识别、找到出处（MediaWiki 建议使用带联系方式的描述性 User-Agent）。
# BWIKI 的图片和接口不需要浏览器的 UA
USER_AGENT = (f"AstralChipCompass/{VERSION} (+{SOURCE_URL}; Astral Party co-op chip helper; "
              "downloads recognition images on first run)")
TIMEOUT = 30  # 秒
RETRIES = 3
# 平时的速度（没有冷却记录时）：整个这一轮下载每秒最多开始 RATE 个请求，各条连接、各段（先下一遍、问接口、按新地址再下）
# 合在一起，相邻两个请求的开始至少隔 1/RATE 秒（_Pace）；同时最多 WORKERS 条连接，同一条连接上不另隔。请求有多密只由 RATE
# 管，不随连接数、网速、图床节点上有没有缓存变：站点每秒最多收到 RATE 个请求，免得招站点防护。
# 连接数只是为了把等图床的时间叠起来：图床前面是 CDN，节点上没有这些图的缓存时每张要回源站取，一张常要零点几秒（一两兆的
# 卡面近一秒），有缓存时一张零点一秒上下；一条连接上一张接一张地等，慢就慢在这段等待。连接多了，一张回源站慢的时候别的连接
# 照样在下，各自的等待叠在一起。6 条和浏览器打开网页时对同一个网站同时开的连接数一样。首次下载 360 张上下的图，有没有缓存都
# 约一分多钟：按每秒 5 个算约 72 秒，另加最后几张本身的下载时间
RATE = 5
WORKERS = 6
# 被拦过以后接着下（冷却记录还在）：1 条连接，每次隔 1 秒（上一个请求结束到下一个请求开始）
SLOW_WORKERS = 1
SLOW_INTERVAL = 1.0
API_INTERVAL = 2.0  # 接口请求之间至少隔这么久（秒）
API_BATCH = 50  # imageinfo 一次问几张（MediaWiki 给一般用户的上限）
BACKOFF = (2.0, 6.0)  # 网络出错、5xx 时第 2、3 次尝试前等多久（秒）
THROTTLED = frozenset({429, 503})  # 限流：按 Retry-After 等
BLOCKED_STATUS = 567  # 站点防护的拦截（回话的页面上写着请求已被站点的安全策略拦截）
# 被拒（限流、不带页面的 403）连着这么多次按被拦算。同一阵只算一次：算进去的那一次被拒回来的那一刻已经在路上的请求，回来
# 也被拒的不另算，那之后才发出去的又被拒才算下一次（模块说明「限流单独一次」、_Gate.rejected）
REJECT_LIMIT = 2
RETRY_AFTER = 60.0  # 限流的回话没写 Retry-After 时等多久（秒）
# 限流要求等得比这还久（秒）时不在下载里干等（下载那一行的进度一直不动），按被拦算：冷却时间取它和 COOLDOWN_MINUTES
# 里长的，悬浮窗上写明几点几分接着下
RETRY_AFTER_MAX = 300.0
COOLDOWN_MINUTES = 35  # 被拦以后过多久再试（分钟）：站点防护按网络地址的封禁约半小时
COOLDOWN_MAX_MINUTES = 180  # 再被拦时冷却时间翻倍（35、70、140 分钟），最多这么久
COOLDOWN_LONGEST = 24 * 3600.0  # 冷却最长多久（秒）：Retry-After 再长也按这么久；记录里的时刻比现在晚这么多以上的不认
COOLDOWN_FRESH = 24 * 3600.0  # 上一次被拦已过去这么久（秒）时再被拦，从 COOLDOWN_MINUTES 算起，不接着翻倍
CHUNK = 64 * 1024  # 正文按块读，每读一块看一次停止标志（每块最多等一次网络，有多少收多少）
STOP_POLL = 0.2  # 秒：download() 隔多久看一次停止标志
STOP_GRACE = 0.3  # 秒：取消时给下载线程收尾的时间（正在写的文件写完、读完一块的见到停止标志）；卡在网络上的不等
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
CANCELLED = "已取消"
# 被站点防护拦下时这一轮没下的图片的原因（不算失败，冷却以后接着下）；认出被拦的那一张后面另带括注写明怎么认出的
PAUSED = "站点防护暂时拒绝下载，已暂停"
# 下载结果里写盘出错的原因以它开头，后接出错的类型和原文（is_write_error 按它认）
WRITE_FAILED = "写不进文件"
# 从卡面现裁角色照时卡面解不开、裁出来是空的：原因以它开头，后接哪里不对
CROP_FAILED = "卡面裁不出角色照"
NO_CROPPER = "没有接上从卡面裁角色照的功能"  # 带 crop 的清单项、却没有 use_cropper（也没给 cropper）时的原因
# 带 crop 的清单项没写卡面的校验值（手改过的清单）时的原因：下到的卡面核对不了，不交去解码（解码的是 wiki 上的图片，只解
# 核对过的），也就不下载（_get_image）；问过接口也没给校验值、已经下到的（_keep）同样不裁
NO_CARD_SHA1 = "清单里没有这张卡面的校验值（sha1），核对不了卡面，不裁"
# 下载线程建不了下载会话（winhttp.Session：读不了系统的证书、代理设置不对等）：这一件的每一张的原因以它开头，后接出错的
# 类型和原文。不是哪一张图的问题（is_own_failure 不算），这一轮照全部或大部分没下下来算（isolated 为假，一张都不记）
NO_SESSION = "建不了下载会话"
# 下载的先后（missing 按它排，同一类里照清单的先后）：识别先用到的先下，被拦下、只下了一部分时先下好的先能用上。进入对局
# 就要比左下角的立绘认角色（认不出时推荐暂停，要手动选角色）；读星数、找自己那一行、认队友每一帧都比 Q 版头像；筹码图标
# 只在打开「查看筹码」面板认已有筹码时用（开局时多半还没有筹码），悬浮窗上缺图标时画成稀有度色圈。
# 角色照里从卡面现裁的那些（带 crop）排在普通角色照后面、Q 版头像前面：卡面一张一两兆（普通角色照几十 K），四十几张加起来
# 是别的图片全部加起来的两倍，排在中间的话，大多数玩家用的普通角色照要等它们下完，被拦下、只下了一部分时认得出的角色也少；
# 仍在 Q 版头像前面，是因为用这些皮肤的玩家一进对局同样要认角色。
# 称号图标排在最后：只在一局结束、读结算数据表认各栏的称号时用（开着对局报告时才读），缺图标时称号的种类写空
ORDER = ("heroes", "profiles", "chips", "achieve")


@dataclass(frozen=True)
class AssetItem:
    """清单里的一张图片。"""

    dir: str  # 子文件夹：heroes / profiles / chips（下载的，在 paths.download_dir 下）/ stars（bundled，在 paths.assets_dir 下）
    file: str  # 本地文件名
    title: str = ""  # wiki 上的文件页名（不带「文件:」前缀），如 UT Hero RolePhoto 101.png
    url: str = ""  # 生成清单时的真实下载地址
    size: int = 0
    sha1: str = ""
    bundled: bool = False  # True：随发行版附带（wiki 上没有），不下载
    api: str = ""  # 文件页所在 wiki 的接口；空表示 BWIKI（API）
    # 从卡面现裁的角色照：裁法的 6 个数（见 advisor/vision/cardcrop.py），title、url、size、sha1 是卡面的；None 表示照原样下载
    crop: tuple | None = None
    # True：BWIKI 上缺失的图片，url 指向备用下载地址，下载出错不问 wiki 的接口，也不算进 wiki 的站点防护和限流
    mirror: bool = False

    @property
    def name(self) -> str:
        return f"{self.dir}/{self.file}"

    @property
    def path(self):
        """本地文件：随发行版附带的（bundled）在程序目录的 assets 下，下载的在 paths.download_dir 下（发行版是个人目录）。"""
        return (paths.assets_dir if self.bundled else paths.download_dir)(self.dir) / self.file


@dataclass
class Manifest:
    items: list[AssetItem] = field(default_factory=list)
    generated: str = ""
    source: str = ""


def load_manifest() -> Manifest:
    """要用的清单：随程序带的 data/assets_manifest.json（load_bundled），官方仓库的版本（update.USE_MIRROR 为真，每次读时
    看）再和个人目录里存着的最新清单（fetch_remote_manifest 取回、存下的）合起来（_merge：同名的项换成最新清单里的，自带的
    项一张不少，最新清单多出来的项排在后面）。存着的最新清单每次读都重读、再核一遍（不缓存：取到新的以后下一次 missing()
    就看得到）；没有、读不出、核对不过、是以后的格式时只用自带的。复刻的改版不读最新清单（它在官方的备用地址上，见
    fetch_remote_manifest）。"""
    manifest = load_bundled()
    if not update.USE_MIRROR:
        return manifest
    remote = _stored_remote()
    return manifest if remote is None else _merge(manifest, remote)


def load_bundled() -> Manifest:
    """读 data/assets_manifest.json；没有清单时返回空清单（开发目录里图片本来就齐）。不是官方仓库的版本
    （update.USE_MIRROR 为假，每次读时看）换掉带 mirror 的项：复刻的改版不碰官方的备用地址，带 fallback 的照 fallback 下
    （同一个本地文件，字段换成 fallback 里的），不带的去掉（见模块说明）。"""
    try:
        raw = json.loads((paths.data_dir() / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Manifest()
    use_mirror = update.USE_MIRROR
    known = set(AssetItem.__dataclass_fields__)
    items = []
    for item in raw.get("items", []):
        if item.get("mirror") and not use_mirror:
            fallback = item.get("fallback")
            if not isinstance(fallback, dict):
                continue
            # 本地文件照原来那一项；fallback 里写了 mirror 也不认：复刻的改版一概不从备用地址下
            item = {**fallback, "dir": item.get("dir"), "file": item.get("file"), "mirror": False}
        fields = {k: v for k, v in item.items() if k in known}
        if "crop" in fields:
            fields["crop"] = _crop_field(fields["crop"])
        items.append(AssetItem(**fields))
    return Manifest(items, raw.get("generated", ""), raw.get("source", ""))


def _crop_field(value) -> tuple:
    """清单项的 crop（JSON 里是 6 个数的列表）换成元组（清单项不可变）。写坏了（不是列表）的照样当成要现裁的一项，裁的
    时候按裁法不对算这一张失败：当成普通的图片的话，会把整张卡面当角色照存下来。"""
    return tuple(value) if isinstance(value, (list, tuple)) else (value,)


# ---------------------------------------------------------------- 联网的最新清单

# 个人目录（paths.download_dir() 这一层，几个版本共用）里存着的最新清单：取回的原样字节，核对过才存
REMOTE_FILE = "manifest-remote.json"
# 同一层：存着的最新清单是哪个 ETag（下次请求带 If-None-Match，没变时省下正文），连同那份清单的 SHA-256：对不上（清单被
# 别的版本换过、改过）时不带，整份重取
REMOTE_ETAG_FILE = "manifest-remote-etag.json"
REMOTE_FORMAT = 1  # 这一版认得的最新清单的格式；比它大的是以后的格式：不用，也不算错
REMOTE_LIMIT = 2 * 1024 * 1024  # 最新清单整份最多多少字节（现在一百多 KB）
REMOTE_MAX_ITEMS = 3000  # items 最多多少项（现在四百项上下）
REMOTE_MAX_HEROES = 1000  # heroes 最多多少项
REMOTE_MAX_SIZE = 8 * 1024 * 1024  # 一张图片（或卡面）最大多少字节（现在最大的卡面不到 1 MB）
# 最新清单里只许这几类（stars 随发行版附带，bundled 的项不许有）
REMOTE_DIRS = ("heroes", "profiles", "chips", "achieve")
# 各类的文件名：识别按文件名在文件夹里找（vision/hero.py、stars.py、chip_panel.py、result_table.py），只许这样的名字，只许
# .png，没有路径分隔符、「..」、盘符。角色编号不许以 0 开头：「0101」和「101」会被识别当成同一个角色
_REMOTE_FILES = {
    "heroes": re.compile(r"UT_Hero_RolePhoto_[1-9][0-9]{0,5}(?:_[A-Za-z0-9]{1,16}){0,3}\.png"),
    "profiles": re.compile(r"UT_Hero_ProfilePhoto_[1-9][0-9]{0,5}(?:_[A-Za-z0-9]{1,16}){0,3}\.png"),
    "chips": re.compile(r"UT_Relic(?:Base)?_[A-Za-z0-9]{1,32}\.png"),
    "achieve": re.compile(r"UT_Achieve_[1-9][0-9]{0,5}_Settlement\.png"),
}
_HERO_ID = re.compile(r"UT_Hero_(?:RolePhoto|ProfilePhoto)_([0-9]+)")  # 角色照、Q 版头像文件名里的角色编号
_SHA1_HEX = re.compile(r"[0-9a-f]{40}")
# wiki 上的文件页名（问接口时用，见 query_imageinfo）：字母数字开头，只有字母数字、空格、「_」「.」「-」，.png 结尾；不许
# 「|」（接口里分隔几个文件页）这类有特殊意思的字符
_TITLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,195}\.png")
# 地址前缀后面的部分：几段，每段字母数字和「_」「~」「-」开头、只有这几样和「.」，.png 结尾。没有「%」（百分号编码的
# 「/」「\」「.」）、「?」「#」「@」「:」，也没有空段、「.」「..」这样的段
_URL_REST = re.compile(r"(?:[A-Za-z0-9_~-][A-Za-z0-9._~-]*/)*[A-Za-z0-9_~-][A-Za-z0-9._~-]*\.png")
WIKI_IMAGES = "https://patchwiki.biligame.com/images/starengine/"  # BWIKI 的图床：最新清单里 wiki 上的图片只许在它下面
_VERSION3 = re.compile(r"[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}")  # 新角色的 since：数字.数字.数字
NEW_HERO_NAME_MAX = 12  # 新角色的名字最多几个字

# fetch_remote_manifest 的结果（给调用方记录、决定要不要接着查缺图）
REMOTE_SAVED = "saved"  # 取到了更新的一份，存下了：接着查缺图（missing）、下载
REMOTE_UNCHANGED = "unchanged"  # 和存着的一样（或服务器回「没变」）
REMOTE_SKIPPED = "skipped"  # 不是官方仓库的版本（update.USE_MIRROR 为假）：不取
REMOTE_FAILED = "failed"  # 没取到（连不上、HTTP 出错）或存不进：照旧用存着的、自带的，下次检查更新时再取
REMOTE_CANCELLED = "cancelled"  # 取的时候停止标志置位了：没存
REMOTE_REJECTED = "rejected"  # 取到了、核对不过（或序号比存着的小、序号一样内容不同）：不用、不存，记了一条错误记录
REMOTE_FUTURE = "future"  # 以后的格式（format 比 REMOTE_FORMAT 大；或存着的是以后的版本写的）：不用、不动，不算错

# 读写最新清单和它的 ETag 记录时拿着：取（后台线程）、读（load_manifest，别的线程）、几个线程同时取时，比序号、写盘不交错；
# Windows 上替换文件时别的线程正开着它会失败。只包读写文件、比对，联网不在锁里
_remote_lock = threading.Lock()


@dataclass(frozen=True)
class NewHero:
    """最新清单里的新角色（这一版的资料里可能还没有）：编号、名字、哪一版起有。"""

    id: int
    name: str
    since: str


@dataclass(frozen=True)
class RemoteManifest:
    """核对过的最新清单。raw 是解析出来的整份 JSON（比内容一不一样用：序号一样、内容不同的不存）。"""

    serial: int
    generated: str
    items: tuple[AssetItem, ...]
    heroes: dict[int, NewHero]
    raw: object = field(compare=False, repr=False)


class _RemoteBad(ValueError):
    """最新清单核对不过：str(e) 是哪里不对（写进错误记录）。"""


class _RemoteLater(Exception):
    """最新清单是以后的格式（format 比 REMOTE_FORMAT 大）：这一版不用，不算错。"""


def _int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _shown(value) -> str:
    """错误记录里写的值：repr，太长的截断。"""
    text = repr(value)
    return text if len(text) <= 80 else text[:77] + "..."


def _no_constant(name: str):
    raise ValueError(f"JSON 里不许有 {name}")


def _url_ok(value, prefix: str) -> bool:
    """value 是 prefix 下面的一张 .png 的地址：只用 ASCII 的可打印字符，没有空白、反斜杠；以 prefix 一字不差开头（大小写不同、
    主机名后面带「@」或端口、换成相像的主机名的都不算）；后面的部分照 _URL_REST（没有「%」「?」「#」「:」，没有空段、
    「.」「..」这样的段：取图时这样的段会被折算掉，「前缀/../别处」就跑出了前缀）；拆开以后协议、主机名和 prefix 的一样，
    没有用户名、端口、查询串和片段。"""
    if not (isinstance(value, str) and len(value) <= 2048 and value.isascii() and value.isprintable()
            and not any(ch.isspace() or ch == "\\" for ch in value) and value.startswith(prefix)):
        return False
    rest = value[len(prefix):]
    if not _URL_REST.fullmatch(rest) or any(part in (".", "..") for part in rest.split("/")):
        return False
    try:
        parts, want = urllib.parse.urlsplit(value), urllib.parse.urlsplit(prefix)
        port = parts.port
    except ValueError:
        return False
    return (parts.scheme == want.scheme == "https" and parts.netloc == want.netloc and parts.username is None
            and parts.password is None and port is None and not parts.query and not parts.fragment)


def _mirror_images() -> str:
    """最新清单里备用地址上的图片只许在它下面（用到时才取 update.MIRROR_BASE：测试可以换）。"""
    return update.MIRROR_BASE + "assets/"


def _check_crop(value, kind: str) -> tuple:
    """最新清单项（或它的 fallback）的 crop：只许角色照（从卡面现裁的是角色照），6 个有限的数（同 _usable_crop）。比
    _crop_field 严：自带清单里写坏的照样当成要现裁的一项、裁的时候算失败；最新清单里写坏的整份不用。"""
    if kind != "heroes":
        raise _RemoteBad("只有角色照能带 crop")
    if not (isinstance(value, list) and len(value) == 6 and all(_finite(v) for v in value)):
        raise _RemoteBad(f"crop 不是 6 个数：{_shown(value)}")
    return tuple(value)


def _finite(value) -> bool:
    """_number，另把大得换不成小数的整数（JSON 里写几百位的数）算作不合：math.isfinite 遇到它抛 OverflowError。"""
    try:
        return _number(value)
    except OverflowError:
        return False


def _check_common(entry: dict, kind: str, prefix: str) -> dict:
    """清单项和它的 fallback 都有的几样：title（可以没有）、url（在 prefix 下面）、size、sha1、crop（可以没有）；api 不许有。
    返回 AssetItem 用的这几个字段。"""
    if "api" in entry:
        raise _RemoteBad("不许带 api（只从 BWIKI 问接口）")
    title = entry.get("title", "")
    if title != "" and not (isinstance(title, str) and _TITLE.fullmatch(title)):
        raise _RemoteBad(f"title 不对：{_shown(title)}")
    url = entry.get("url")
    if not _url_ok(url, prefix):
        raise _RemoteBad(f"地址不在 {prefix} 下面，或写法不对：{_shown(url)}")
    size = entry.get("size")
    if not (_int(size) and 1 <= size <= REMOTE_MAX_SIZE):
        raise _RemoteBad(f"size 不在 1～{REMOTE_MAX_SIZE} 字节：{_shown(size)}")
    sha1 = entry.get("sha1")
    if not (isinstance(sha1, str) and _SHA1_HEX.fullmatch(sha1)):
        raise _RemoteBad(f"sha1 不是 40 位小写十六进制：{_shown(sha1)}")
    crop = _check_crop(entry["crop"], kind) if "crop" in entry else None
    return {"title": title, "url": url, "size": size, "sha1": sha1, "crop": crop}


def _remote_item(entry) -> AssetItem:
    """最新清单里的一项 → AssetItem；哪里不对抛 _RemoteBad（整份不用）。

    规矩：dir 只许 REMOTE_DIRS；file 照这一类的命名（_REMOTE_FILES）；bundled、api 不许有；mirror 是真假（可以不写），带
    mirror 的地址在备用地址（_mirror_images）下面，不带的在 BWIKI 的图床（WIKI_IMAGES）下面，两样对不上的不认（mirror 决定
    下载出错时问不问 wiki 的接口、算不算进 wiki 的限流）；fallback（复刻的改版用，可以没有）是对象，地址只许在 BWIKI 的
    图床下面，其余同清单项。
    不认识的键不管（AssetItem 里没有的字段读进来也用不上，以后的清单加了说明性的键，这一版照常用）；改了意思的会换 format。"""
    if not isinstance(entry, dict):
        raise _RemoteBad("不是对象")
    kind = entry.get("dir")
    if kind not in REMOTE_DIRS:
        raise _RemoteBad(f"dir 不对：{_shown(kind)}")
    name = entry.get("file")
    if not (isinstance(name, str) and _REMOTE_FILES[kind].fullmatch(name)):
        raise _RemoteBad(f"file 不合 {kind} 的命名：{_shown(name)}")
    if "bundled" in entry:
        raise _RemoteBad("不许带 bundled")
    mirror = entry.get("mirror", False)
    if not isinstance(mirror, bool):
        raise _RemoteBad(f"mirror 不是真假：{_shown(mirror)}")
    fields = _check_common(entry, kind, _mirror_images() if mirror else WIKI_IMAGES)
    if "fallback" in entry:
        fallback = entry["fallback"]
        if not isinstance(fallback, dict):
            raise _RemoteBad("fallback 不是对象")
        try:
            _check_common(fallback, kind, WIKI_IMAGES)
        except _RemoteBad as e:
            raise _RemoteBad(f"fallback 的 {e}") from None
    return AssetItem(kind, name, mirror=mirror, **fields)


def _new_hero(entry) -> NewHero | None:
    """最新清单 heroes 里的一项；不合的（id 不是正整数、name 不是 1～NEW_HERO_NAME_MAX 个字、since 不是数字.数字.数字）为
    None，只丢这一项。"""
    if not isinstance(entry, dict):
        return None
    hero_id, name, since = entry.get("id"), entry.get("name"), entry.get("since")
    if not (_int(hero_id) and 1 <= hero_id <= 999_999):
        return None
    if not (isinstance(name, str) and 1 <= len(name) <= NEW_HERO_NAME_MAX and name.isprintable()
            and name.strip() == name):
        return None
    if not (isinstance(since, str) and _VERSION3.fullmatch(since)):
        return None
    return NewHero(hero_id, name, since)


def _key(item: AssetItem) -> tuple[str, str]:
    """合并、查重时的名字：文件名不分大小写（Windows 上只差大小写的是同一个文件，两项写同一个文件会来回重下）。"""
    return item.dir, item.file.casefold()


def parse_remote(data: bytes) -> RemoteManifest:
    """核对最新清单的字节，交回 RemoteManifest。哪一条不过抛 _RemoteBad（str 是哪里不对），整份不用；format 比
    REMOTE_FORMAT 大时抛 _RemoteLater。

    整份不超过 REMOTE_LIMIT 字节；是 UTF-8 的 JSON（NaN、Infinity 不认，开头不许有 BOM）；顶层是对象；format 是 1；serial 是
    正整数；generated 是文字（可以没有）；items 是列表、不超过 REMOTE_MAX_ITEMS 项，每一项照 _remote_item，文件名不分大小写
    不许重复；heroes（可以没有）是列表、不超过 REMOTE_MAX_HEROES 项，不合的项丢掉（_new_hero），编号重复的留第一个。
    顶层不认识的键不管。

    核对时没料到的出错（类型、算术、嵌套太深）同样按核对不过算：这份清单来自网络、个人目录里的文件，读坏了只是不用它，
    不能让取清单的线程、读清单的 missing() 出错。"""
    try:
        return _parse_remote(data)
    except (_RemoteBad, _RemoteLater):
        raise
    except (TypeError, ArithmeticError, RecursionError) as e:
        raise _RemoteBad(f"核对时出错（{type(e).__name__}）") from None


def _parse_remote(data: bytes) -> RemoteManifest:
    """parse_remote 的核对本身（见它的说明）。"""
    if len(data) > REMOTE_LIMIT:
        raise _RemoteBad(f"超过 {REMOTE_LIMIT} 字节")
    try:
        raw = json.loads(data.decode("utf-8"), parse_constant=_no_constant)
    except UnicodeDecodeError:
        raise _RemoteBad("不是 UTF-8 的文字") from None
    except (ValueError, RecursionError) as e:  # 不是 JSON、开头带 BOM、嵌套太深
        raise _RemoteBad(f"不是 JSON（{type(e).__name__}）") from None
    if not isinstance(raw, dict):
        raise _RemoteBad("顶层不是对象")
    fmt = raw.get("format")
    if not (_int(fmt) and fmt >= 1):
        raise _RemoteBad(f"format 不对：{_shown(fmt)}")
    if fmt > REMOTE_FORMAT:
        raise _RemoteLater(fmt)
    serial = raw.get("serial")
    if not (_int(serial) and serial >= 1):
        raise _RemoteBad(f"serial 不是正整数：{_shown(serial)}")
    generated = raw.get("generated", "")
    if not (isinstance(generated, str) and len(generated) <= 40):
        raise _RemoteBad(f"generated 不对：{_shown(generated)}")
    entries = raw.get("items")
    if not isinstance(entries, list):
        raise _RemoteBad("items 不是列表")
    if len(entries) > REMOTE_MAX_ITEMS:
        raise _RemoteBad(f"items 超过 {REMOTE_MAX_ITEMS} 项（{len(entries)} 项）")
    items: list[AssetItem] = []
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(entries, 1):
        try:
            item = _remote_item(entry)
        except _RemoteBad as e:
            label = entry.get("file") if isinstance(entry, dict) else None
            raise _RemoteBad(f"items 第 {index} 项（{_shown(label)}）：{e}") from None
        if _key(item) in seen:
            raise _RemoteBad(f"items 第 {index} 项（{item.name}）和前面的重名")
        seen.add(_key(item))
        items.append(item)
    heroes: dict[int, NewHero] = {}
    if "heroes" in raw:
        listed = raw["heroes"]
        if not isinstance(listed, list) or len(listed) > REMOTE_MAX_HEROES:
            raise _RemoteBad(f"heroes 不是列表，或超过 {REMOTE_MAX_HEROES} 项")
        for entry in listed:
            hero = _new_hero(entry)
            if hero is not None:
                heroes.setdefault(hero.id, hero)
    return RemoteManifest(serial, generated, tuple(items), heroes, raw)


def _remote_path():
    return paths.download_dir() / REMOTE_FILE


def _etag_path():
    return paths.download_dir() / REMOTE_ETAG_FILE


def _read_remote() -> tuple[bytes | None, RemoteManifest | None, bool]:
    """存着的最新清单：（原样的字节, 核对过的 RemoteManifest, 是不是以后的格式）。没有、读不出时字节为 None；读得出、核对不过
    （别的版本写的、被改过）时 RemoteManifest 为 None，当没有。在 _remote_lock 里调用。"""
    try:
        data = _remote_path().read_bytes()
    except OSError:
        return None, None, False
    try:
        return data, parse_remote(data), False
    except _RemoteLater:
        return data, None, True
    except _RemoteBad:
        return data, None, False


def _stored_remote() -> RemoteManifest | None:
    """存着的、核对过的最新清单；没有、核对不过、是以后的格式时为 None。"""
    with _remote_lock:
        return _read_remote()[1]


def new_heroes() -> dict[int, NewHero]:
    """存着的最新清单里的新角色：{编号: NewHero}（名字、哪一版起有）。没有最新清单、复刻的改版（不读最新清单）时为空。"""
    if not update.USE_MIRROR:
        return {}
    remote = _stored_remote()
    return dict(remote.heroes) if remote is not None else {}


_ETAG = re.compile(r'(?:W/)?"[\x21\x23-\x7e]{1,200}"')  # ETag：带引号的可见 ASCII（不许换行：要原样写进请求头）


def _stored_etag(data: bytes | None) -> str | None:
    """下次请求带的 If-None-Match：ETag 记录里记的那份清单（SHA-256）就是现在存着的 data 时才用，记录读不出、对不上时为
    None（整份重取）。在 _remote_lock 里调用。"""
    if data is None:
        return None
    try:
        record = json.loads(_etag_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    etag = record.get("etag")
    if not (isinstance(etag, str) and _ETAG.fullmatch(etag)):
        return None
    return etag if record.get("sha256") == hashlib.sha256(data).hexdigest() else None


def _write_etag(data: bytes, etag: str | None) -> None:
    """记下存着的清单 data 的 ETag（没有、写法不对时删掉记录）。写不进、删不掉时就算了：下次整份重取（对不上 SHA-256 的
    记录不用）。在 _remote_lock 里调用。"""
    try:
        if etag is None or not _ETAG.fullmatch(etag):
            _etag_path().unlink(missing_ok=True)
        else:
            record = {"etag": etag, "sha256": hashlib.sha256(data).hexdigest()}
            fileio.write_text(_etag_path(), json.dumps(record), suffix=".part")
    except OSError:
        pass


def _save_remote(data: bytes, etag: str | None) -> bool:
    """存下核对过的最新清单（原样的字节）：临时文件、刷盘、整份替换（fileio.write_bytes），断电、写一半时旧的要么完整、要么是
    新的；再记下它的 ETag。返回存进去了没有：写不进不算错，下次再取。在 _remote_lock 里调用。"""
    try:
        fileio.write_bytes(_remote_path(), data, suffix=".part")
    except OSError:
        return False
    _write_etag(data, etag)
    return True


def _reject_remote(title: str, detail: str) -> str:
    """最新清单不用：记一条错误记录，交回 REMOTE_REJECTED。"""
    errors.log_error(title, f"{update.ASSETS_LATEST}\n{detail}")
    return REMOTE_REJECTED


class _TooBig(Exception):
    """收到的正文超过 REMOTE_LIMIT。"""


def fetch_remote_manifest(stop=None, *, session=None) -> str:
    """从备用地址取识别图片的最新清单（update.ASSETS_LATEST），核对、和存着的比序号，更新的才存进个人目录（REMOTE_FILE）。
    交回 REMOTE_SAVED、REMOTE_UNCHANGED、REMOTE_SKIPPED、REMOTE_FAILED、REMOTE_CANCELLED、REMOTE_REJECTED、REMOTE_FUTURE
    之一（见各自的说明）；是 REMOTE_SAVED 时调用方接着查缺图（missing() 已经照合好的清单算）、下载。不抛异常。

    - 只在官方仓库的版本（update.USE_MIRROR 为真）取。
    - 请求只发一个：用 _KeepAlive（同下载图片：系统的证书和代理，带 USER_AGENT，超时 TIMEOUT，停止标志置位时收手）；
      不跟重定向（只认备用地址本身的回话）；正文最多收 REMOTE_LIMIT 字节，多了按核对不过算。取不到就算了，不重试：下次
      检查更新时再取。备用地址不是 wiki，不受 wiki 的被拦、冷却那一套管。
    - 存着的清单记了 ETag 的带 If-None-Match，回 304 算没变。
    - 核对见 parse_remote；不过的整份不用、不存，记一条错误记录。以后的格式不用、不记。
    - 存着一份时：取到的序号比它小的不存，序号一样、内容不一样的也不存（各记一条错误记录）；序号一样、内容一样的不重写。
      存着的是以后的格式（更新的版本写的）时不动它。
    - 停止标志置位以后不再存。

    session：只给测试用（有 stream(req, sink, timeout, stop, head=, limit=, redirects=) 和 close() 的对象，同
    winhttp.Session）；不给时新建一个、用完关掉。在后台线程里调用；和 load_manifest（别的线程）同时调用不打架
    （_remote_lock）。"""
    if not update.USE_MIRROR:
        return REMOTE_SKIPPED
    with _remote_lock:
        stored_data, stored, _ = _read_remote()
        etag = _stored_etag(stored_data) if stored is not None else None
    url = update.ASSETS_LATEST
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    parts: list[bytes] = []
    got = {"size": 0, "etag": None}

    def head(status, reply) -> None:
        got["etag"] = _header(reply, "ETag").strip() or None

    def sink(chunk: bytes) -> None:
        got["size"] += len(chunk)
        if got["size"] > REMOTE_LIMIT:
            raise _TooBig
        parts.append(chunk)

    own = session is None
    try:
        if own:
            session = _KeepAlive()
        try:
            session.stream(urllib.request.Request(url, headers=headers), sink, TIMEOUT, stop, head=head,
                           limit=REMOTE_LIMIT, redirects=False)
        finally:
            if own:
                session.close()
    except (_TooBig, winhttp.TooLong):
        return _reject_remote("识别图片的最新清单不对，没有用它（拾星助手仍在运行）", f"超过 {REMOTE_LIMIT} 字节")
    except (winhttp.Stopped, _Stopped):
        return REMOTE_CANCELLED
    except urllib.error.HTTPError as e:
        e.close()
        return REMOTE_UNCHANGED if e.code == 304 and etag else REMOTE_FAILED
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        return REMOTE_FAILED
    if _stopped(stop):
        return REMOTE_CANCELLED
    data = b"".join(parts)
    try:
        remote = parse_remote(data)
    except _RemoteLater:
        return REMOTE_FUTURE
    except _RemoteBad as e:
        return _reject_remote("识别图片的最新清单不对，没有用它（拾星助手仍在运行）", str(e))
    with _remote_lock:
        stored_data, stored, later = _read_remote()  # 再读一次：别的线程可能刚存了一份
        if later:
            return REMOTE_FUTURE
        if stored is not None:
            if remote.serial < stored.serial:
                return _reject_remote("识别图片的最新清单比已经存着的旧，没有用它（拾星助手仍在运行）",
                                      f"取到的序号 {remote.serial}，存着的序号 {stored.serial}")
            if remote.serial == stored.serial:
                if remote.raw != stored.raw:
                    return _reject_remote("识别图片的最新清单和已经存着的序号一样、内容不一样，没有用它（拾星助手仍在运行）",
                                          f"序号 {remote.serial}")
                if got["etag"] != _stored_etag(stored_data):
                    _write_etag(stored_data, got["etag"])  # 内容没变、ETag 换了：记下新的，下次省正文
                return REMOTE_UNCHANGED
        return REMOTE_SAVED if _save_remote(data, got["etag"]) else REMOTE_FAILED


# 联网的清单带来的角色照、Q 版头像，角色编号这台程序认不认识（见 use_hero_ids）
_hero_ids: frozenset[int] | None = None
_hero_ids_load: Callable[[], Iterable[int] | None] | None = None
_hero_ids_lock = threading.Lock()


def use_hero_ids(ids: Iterable[int] | None = None, *, load: Callable[[], Iterable[int] | None] | None = None) -> None:
    """登记这台程序认识的角色编号（资料里有的）：最新清单多出来的角色照、Q 版头像，编号既不在这里、也不在最新清单的
    heroes 里的不下（认出来也对不上角色）。本模块在底层、不导入资料，由上层登记（同 use_cropper）；load 是交回编号的函数，
    第一次合并清单时才调用、只调用一次，出错、交回的不是编号的集合时按没登记算。没登记时都下。ids、load 都不给时取消登记。"""
    global _hero_ids, _hero_ids_load
    with _hero_ids_lock:
        _hero_ids = None if ids is None else frozenset(ids)
        _hero_ids_load = load


def _known_hero_ids() -> frozenset[int] | None:
    """登记的角色编号；没登记、load 出错时为 None（都下）。"""
    global _hero_ids, _hero_ids_load
    with _hero_ids_lock:
        if _hero_ids_load is not None:
            load, _hero_ids_load = _hero_ids_load, None
            try:
                found = load()
                _hero_ids = None if found is None else frozenset(int(v) for v in found)
            except Exception:  # noqa: BLE001  登记不上只是新角色的图照下，不让读清单出错
                _hero_ids = None
        return _hero_ids


def _hero_of(item: AssetItem) -> int | None:
    """角色照、Q 版头像文件名里的角色编号；别的类为 None。"""
    if item.dir not in ("heroes", "profiles"):
        return None
    m = _HERO_ID.match(item.file)
    return int(m.group(1)) if m else None


def _merge(bundled: Manifest, remote: RemoteManifest) -> Manifest:
    """自带的清单和最新清单合起来：按（dir, 文件名，不分大小写）对，同名的项换成最新清单里的（在原来的位置），自带清单里有、
    最新清单里没有的照旧留着（最新清单不能让程序少下图）；最新清单多出来的项照它的先后排在后面，其中角色照、Q 版头像的角色
    编号这台程序不认识的（登记了编号，又不在最新清单的 heroes 里，见 use_hero_ids）不要。随发行版附带的（bundled）不换。"""
    by_key = {_key(item): item for item in remote.items}
    used: set[tuple[str, str]] = set()
    items = []
    for item in bundled.items:
        key = _key(item)
        if not item.bundled and key in by_key:
            items.append(by_key[key])
            used.add(key)
        else:
            items.append(item)
    known = _known_hero_ids()
    if known is not None:
        known = known | set(remote.heroes)
    for item in remote.items:
        if _key(item) in used:
            continue
        hero = _hero_of(item)
        if known is not None and hero is not None and hero not in known:
            continue
        items.append(item)
    return Manifest(items, bundled.generated, bundled.source)


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


# ---------------------------------------------------------------- 本地覆盖清单

# 可重入：写覆盖清单（_update_local）时也在锁里读一次（load_local），同一个线程不能再等自己。冷却记录的读写也用它
_local_lock = threading.RLock()
# 用户点「重试」时要去掉记号、覆盖清单却写不进的图片（dir/file，见 forget_recent）：这次运行里 missing 当它们没有记号。以后给
# 它们重新记号或存好时（_note_failed、_update_local）拿掉，之后照覆盖清单。在 _local_lock 里读写
_forgotten: set[str] = set()


def _local_path():
    return paths.download_dir() / LOCAL_MANIFEST


def load_local() -> dict:
    """assets/manifest-local.json：{"updated": {dir/file: {url, size, sha1}}, "gone": {dir/file: {date, title}},
    "cropped": {dir/file: {card, crop, size}}, "failed": {dir/file: "YYYY-MM-DD"}}（gone：wiki 上已删除的图片是哪天、哪个
    文件页（title）查到删了的，以前的记录只有日期（"YYYY-MM-DD"），见 _recently_gone；cropped：现裁的角色照是哪张卡面
    （SHA-1）按哪份裁法裁的、裁出来多少字节，见 is_present；failed：个别下载失败的图片是哪天失败的，见模块说明「个别图片
    一直下不了」）。

    读的时候拿锁：下载线程正写这个文件时，Windows 上替换会失败（别的线程开着它）。类型不对的项丢掉（手改过或以后改了
    格式）：updated 只收值是对象、地址和校验值是文字、大小是整数的（都可以不写），gone 只收值是文字的（以前的写法）或日期、
    文件页都是文字的对象，failed 只收值是文字的（日期写得不对的用到时当没记，见 _within），cropped 只收 _usable_crop 的；
    不然 effective()、is_present() 换算时抛异常，启动时按「不缺图」处理、一直不下载。丢掉的那张按清单比对，对不上就重新
    下载、重新记下（现裁的角色照没有记录就不算在，重新下卡面再裁）。
    这一版不认识的顶层节（以后的版本加的）原样留在返回的字典里，排在认得的这几节（LOCAL_SECTIONS）后面；写回时
    （_update_local、_note_failed、forget_recent 都是读进来、改、整份写回）照原样写回。发行版的这份记录放在个人目录里，几个
    版本共用：用户开回旧一些的版本时，不把新版本记的东西写丢。认得的几节里认不出的条目照上面的规矩丢掉。
    """
    with _local_lock:
        try:
            raw = json.loads(_local_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        updated, gone, cropped, failed = raw.get("updated"), raw.get("gone"), raw.get("cropped"), raw.get("failed")
        known = {
            "updated": {k: v for k, v in updated.items() if _usable_update(v)} if isinstance(updated, dict) else {},
            "gone": {k: v for k, v in gone.items() if _usable_gone(v)} if isinstance(gone, dict) else {},
            "cropped": {k: v for k, v in cropped.items() if _usable_crop(v)} if isinstance(cropped, dict) else {},
            "failed": {k: v for k, v in failed.items() if isinstance(v, str)} if isinstance(failed, dict) else {},
        }
        return {**known, **{k: v for k, v in raw.items() if k not in known}}


def _usable_update(info) -> bool:
    """覆盖清单里一张图片的更新记录能不能用：对象，url、sha1 是文字或不写，size 是整数或不写（同 _update_local 写的）。"""
    if not isinstance(info, dict):
        return False
    size = info.get("size")
    return (all(isinstance(info.get(k), (str, type(None))) for k in ("url", "sha1"))
            and (size is None or (isinstance(size, int) and not isinstance(size, bool))))


def _usable_crop(info) -> bool:
    """覆盖清单里一张现裁的角色照的记录能不能用：对象，card（卡面的 SHA-1）是文字，crop 是 6 个数，size 是正整数（同
    _keep、note_crop 写的）。"""
    if not isinstance(info, dict):
        return False
    crop, size = info.get("crop"), info.get("size")
    return (isinstance(info.get("card"), str) and isinstance(crop, list) and len(crop) == 6
            and all(_number(v) for v in crop) and isinstance(size, int) and not isinstance(size, bool) and size > 0)


def _usable_gone(info) -> bool:
    """覆盖清单里一张 wiki 上已删除的图片的记录能不能用：以前的写法是日期的文字；现在是对象，date（日期）、title（查到删了的
    文件页）都是文字（同 _refreshed 写的）。日期写得不对的用到时当没记（_within）。"""
    if isinstance(info, str):
        return True
    return isinstance(info, dict) and isinstance(info.get("date"), str) and isinstance(info.get("title"), str)


def _write_local(data: dict) -> bool:
    """把本地覆盖清单整份写下（data 是 load_local 读进来、改过的那一份，连同它留着的不认识的节）：临时文件、刷盘、再整份替换
    （fileio.write_text），断电、写一半时旧清单要么完整、要么是新的。在 _local_lock 里调用。返回写进去了没有。

    被别的程序开着、写不进时（杀毒软件、同步软件正读着它），fileio 自己已经稍等再试了约 1 秒；还开着的再按 LOCAL_RETRY_DELAYS
    隔一会儿试几次，用不等的写法（wait=False）：fileio 报 Busy（还被开着）才接着试，只读、文件夹写不进这类马上就报的不再试，
    多等也没用。仍写不进就算了：记不下来只是下次启动时再下一遍（现裁的角色照没有记录就不算在，要重下卡面再裁）。"""
    text = json.dumps(data, ensure_ascii=False, indent=1)
    try:
        fileio.write_text(_local_path(), text, suffix=".part")
        return True
    except PermissionError:
        pass  # 一直被开着，或只读、写不进：下面不等的写法分得开
    except OSError:
        return False
    for delay in LOCAL_RETRY_DELAYS:
        if delay > 0:
            time.sleep(delay)
        try:
            fileio.write_text(_local_path(), text, suffix=".part", wait=False)
            return True
        except fileio.Busy:
            continue
        except OSError:
            return False
    return False


def _update_local(name: str, *, updated: dict | None = None, gone: dict | str | None = None,
                  cropped: dict | None = None) -> None:
    """记下一张图片在 wiki 上的新地址和校验值（updated）、已删除的日期和文件页（gone）、现裁的角色照是按哪张卡面哪份裁法
    裁的（cropped）。这张原来的记录都换成给的这几样，一样都没给时清掉这张的记录；个别下载失败的日期（failed）也一起清掉：
    存好了、记成已删除了，都不再是「下载失败、隔天再试」。内存里记着的去记号（_forgotten）也拿掉：这张的记录以这一次为准。
    写法见 _write_local。
    """
    with _local_lock:
        _forgotten.discard(name)
        data = load_local()
        for section in LOCAL_SECTIONS:
            data[section].pop(name, None)
        if updated:
            data["updated"][name] = updated
        if gone:
            data["gone"][name] = gone
        if cropped:
            data["cropped"][name] = cropped
        _write_local(data)


def _note_failed(names: list[str]) -> None:
    """记下这几张图片（dir/file）今天个别下载失败（覆盖清单的 failed，见模块说明「个别图片一直下不了」）：一轮记一次，写法
    同 _update_local。这几张别的记录照旧留着：wiki 上更新过的地址和校验值、现裁的记录都还有用，失败的只是这一次下载。
    内存里记着的去记号（_forgotten）拿掉：又失败了，照记号算。写不进时就算了：下次启动照旧再下。"""
    if not names:
        return
    today = clock.today().isoformat()
    with _local_lock:
        _forgotten.difference_update(names)
        data = load_local()
        for name in names:
            data["failed"][name] = today
        _write_local(data)


def forget_recent(names: Iterable[str]) -> bool:
    """用户在下载失败那一行点「重试」时（悬浮窗，这一轮开始下载之前）调用：去掉这几张图片（dir/file）「近期不再自动下载」
    的记号，即覆盖清单里个别下载失败的日期（failed）、wiki 上已删除的记录（gone）。这几张别的记录（wiki 上更新过的地址和
    校验值、现裁的记录）照留，别的图片的记号不动。

    点「重试」就是要现在下这几张：去掉以后 missing 照常算它们，这一轮正好被站点防护拦下时，它们和别的没下好的图片一样算进
    下载那一行的「其余 N 张」、冷却到点接着下（中途退出、当天再打开也是）。这一轮没被拦的照旧：存好了 _keep 清记录，又下不了
    的按原因重新记号（_note_failed 记今天、_refreshed 记已删除）。只在点「重试」时去：missing、retry_items 这类查询不动记号。

    没有要去的记号时不写盘；有的一次写下，写法同 _note_failed。写不进时不出错：这次运行里在内存里记着这几张（_forgotten），
    missing 照样当它们没有记号；重启以后照覆盖清单里的记号，同没点「重试」。返回记号是不是已从覆盖清单里去掉（本来就没有
    要去的也算）。"""
    names = set(names)
    with _local_lock:
        data = load_local()
        marked = {name for name in names if name in data["failed"] or name in data["gone"]}
        if not marked:
            return True
        for name in marked:
            data["failed"].pop(name, None)
            data["gone"].pop(name, None)
        if _write_local(data):
            _forgotten.difference_update(marked)
            return True
        _forgotten.update(marked)
        return False


def note_crop(name: str, card_sha1: str, crop, size: int) -> None:
    """记下现裁的角色照（name 是 dir/file）是哪张卡面（card_sha1）按哪份裁法（crop，6 个数）裁的、裁出来多少字节（size），
    is_present 按它判断在不在。开发用的取图工具（tools/fetch_assets.py --crop）裁好开发目录里的角色照以后调用；首次运行的
    下载在 _keep 里记。这张别的记录（wiki 上更新过、已删除、个别下载失败）一并清掉：记的就是这张卡面现在的样子。"""
    _update_local(name, cropped={"card": card_sha1, "crop": [float(v) for v in crop], "size": int(size)})


def effective(item: AssetItem, local: dict | None = None) -> AssetItem:
    """按本地覆盖清单换上 wiki 上当前的地址、大小和校验值。

    更新记录要和这一项现在的下法对得上才用（_same_kind）：清单在「照原样下载」和「从卡面现裁」之间改过的（例如 wiki 上
    旧的角色照改成从卡面现裁，或者以后又换回角色照），以前记下的是另一张文件（旧的角色照、卡面）的地址和校验值，照它下的话，
    会把旧角色照当卡面裁、或者把卡面当角色照存下来，校验值、大小又对得上，一直当成在。这样的旧记录不用，照清单下，存好以后
    _keep 把它清掉。

    备用地址上的图片一律照清单：它没有 wiki 上的更新可记（_keep 不记），覆盖清单里这一项的更新记录是它以前还从
    wiki 下载时记的，地址是 wiki 上的，照它下会一直下不到。存好以后 _keep 把它清掉。"""
    if item.mirror:
        return item
    local = local if local is not None else load_local()
    info = local["updated"].get(item.name)
    if not info or not _same_kind(item, info, local):
        return item
    return dataclasses.replace(item, url=info.get("url") or item.url, size=int(info.get("size") or item.size),
                               sha1=info.get("sha1") or item.sha1)


def _same_kind(item: AssetItem, info: dict, local: dict) -> bool:
    """覆盖清单里这一项的更新记录（info）是不是按这一项现在的下法记下的。从卡面现裁的，更新记录和现裁的记录在 _keep 里一起写，
    现裁的记录里的卡面 SHA-1 就是更新记录的校验值；照原样下载的，存好时清掉这一项的现裁记录，两样不会同时在。"""
    cropped = local.get("cropped", {}).get(item.name)
    if item.crop is not None:
        return cropped is not None and cropped.get("card") == info.get("sha1")
    return cropped is None


def is_present(item: AssetItem, local: dict | None = None) -> bool:
    """文件在、而且大小对得上（大小不对多半是下载中断留下的）。wiki 上更新过的图片按本地覆盖清单的大小比。

    现裁的角色照（带 crop）的大小和清单里卡面的对不上，改按覆盖清单里这张的记录（cropped）：记着的卡面 SHA-1 和这一项的
    （wiki 上更新过的按覆盖清单）一样、裁法一样、文件大小和记着的一样，才算在。没有这笔记录（以前下过 wiki 上的旧角色照、
    记录丢了、读不出来）、裁法改了、卡面在 wiki 上更新了的都不算在：重新下卡面再裁。"""
    local = local if local is not None else load_local()
    item = effective(item, local)
    try:
        size = item.path.stat().st_size
    except OSError:
        return False
    if size <= 0:
        return False
    if item.crop is not None:
        record = local.get("cropped", {}).get(item.name)
        return (record is not None and record["card"] == item.sha1 and record["size"] == size
                and _same_crop(record["crop"], item.crop))
    return not item.size or size == item.size


def _same_crop(a, b) -> bool:
    """两份裁法（6 个数）一样：按数比（JSON 里写成 0 和 0.0 的一样）；写坏了、比不了的算不一样。"""
    try:
        return len(a) == len(b) and all(float(x) == float(y) for x, y in zip(a, b))
    except (TypeError, ValueError):
        return False


_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")  # 覆盖清单里记的日期只认 YYYY-MM-DD（同 clock 的 ACC_TODAY）


def _within(when, days: int, today: date | None = None) -> bool:
    """覆盖清单里记的日期（when）起还没过 days 天。日期写得不对（不是 YYYY-MM-DD 的文字、没有这一天）、比今天还晚（系统
    时钟往回拨过、手改过）的都当没记，照常下载：不然可能一直等下去（比今天晚的要等到那天以后再过 days 天）。"""
    if not isinstance(when, str) or not _DAY.fullmatch(when):
        return False
    try:
        day = date.fromisoformat(when)
    except ValueError:
        return False
    return 0 <= ((today or clock.today()) - day).days < days


def _recently_gone(item: AssetItem, local: dict, today: date | None = None) -> bool:
    """这张图片记成 wiki 上已删除（覆盖清单的 gone），从那天起还没过 GONE_RETRY_DAYS 天：启动时先不自动下载。

    记录要和这一项现在的下法对得上才算（同 effective 不用另一种下法时记下的更新记录）：记着的文件页（title）就是现在要问的
    那一页（_wiki_title）；以前的记录只有日期，那时的清单项都是照原样下载的，只对照原样下载的项（不带 crop）算。清单里这一项
    后来改成从卡面现裁（问的是卡面）、换了文件页的，记录说的是另一张图，不用，照常下载。日期的规矩见 _within。local 是旧的
    覆盖清单、没有这一节时当没记。备用地址上的图片不问 wiki，这样的记录是它以前还从 wiki 下载时记的，不用（同
    effective）。"""
    if item.mirror:
        return False
    record = (local.get("gone") or {}).get(item.name)
    if isinstance(record, dict):
        if record.get("title") != _wiki_title(item):
            return False
        record = record.get("date")
    elif item.crop is not None:
        return False
    return _within(record, GONE_RETRY_DAYS, today)


def _recently_failed(item: AssetItem, local: dict, today: date | None = None) -> bool:
    """这张图片个别下载失败、记了日期（覆盖清单的 failed），从那天起还没过 FAILED_RETRY_DAYS 天：启动时先不自动下载。
    日期的规矩见 _within。local 是旧的覆盖清单、没有这一节时当没记。"""
    return _within((local.get("failed") or {}).get(item.name), FAILED_RETRY_DAYS, today)


def _order(item: AssetItem) -> tuple[int, bool]:
    """下载的先后：先按 ORDER 分类；同一类里从卡面现裁的排在后面（见 ORDER 的说明）。"""
    return (ORDER.index(item.dir) if item.dir in ORDER else len(ORDER)), item.crop is not None


def missing(manifest: Manifest | None = None, *, retry_gone: bool = False,
            retry_failed: bool = False) -> list[AssetItem]:
    """还缺的、需要下载的图片，识别先用到的排在前面（ORDER，同一类里照清单的先后）。随发行版附带的（bundled）不算；
    wiki 上已删除的图片 GONE_RETRY_DAYS 天内不算（记录要和这一项现在的下法对得上，见 _recently_gone；retry_gone=True 时
    也算）；个别下载失败、记了日期的图片 FAILED_RETRY_DAYS 天以内不算（retry_failed=True 时也算；见模块说明「个别图片一直
    下不了」）。悬浮窗的「重试」两样都算（retry_items）。用户点过「重试」、记号去不掉的（覆盖清单写不进，见 forget_recent）
    这次运行里当没记号算。只读，不动记号。"""
    manifest = manifest or load_manifest()
    with _local_lock:
        local = load_local()
        forgotten = set(_forgotten)
    todo = [
        item for item in manifest.items
        if not item.bundled and not is_present(item, local)
        and (retry_gone or item.name in forgotten or not _recently_gone(item, local))
        and (retry_failed or item.name in forgotten or not _recently_failed(item, local))
    ]
    return sorted(todo, key=_order)


def retry_items() -> list[AssetItem]:
    """用户点「重试」时要下载的图片：包括 wiki 上已删除、个别下载失败而近期不再自动重试的。只读，不动记号：点「重试」时去
    记号是 forget_recent 的事。"""
    return missing(retry_gone=True, retry_failed=True)


def missing_bundled(manifest: Manifest | None = None) -> list[AssetItem]:
    """随发行版附带、却不在的图片（发行版不完整，下载也补不回来）。"""
    manifest = manifest or load_manifest()
    return [item for item in manifest.items if item.bundled and not item.path.exists()]


# ---------------------------------------------------------------- 从卡面现裁


_cropper: Callable[[bytes, tuple], bytes] | None = None  # 从卡面现裁角色照的函数，见 use_cropper
_cropper_load: Callable[[], Callable | None] | None = None  # 第一次要裁图时才调用、交回 _cropper 的函数，见 use_cropper
_cropper_lock = threading.Lock()


def use_cropper(crop: Callable[[bytes, tuple], bytes] | None = None, *,
                load: Callable[[], Callable | None] | None = None) -> None:
    """接上从卡面现裁角色照的函数：crop(卡面 PNG 的字节, 裁法) → 角色照 PNG 的字节，裁不出时抛 ValueError（advisor/vision/
    cardcrop.py 的 crop_png）。裁图要解 PNG、用 OpenCV，放在识别那一侧；本模块在底层，不导入识别一侧的模块（连函数里的延迟
    导入也不行，见 tests/test_import_layers.py），由用到下载的上层接上：悬浮窗在 advisor/ui/services.py 导入本模块时接。

    load：不直接给函数，给一个交回它的函数（里面导入裁图模块：要零点一秒上下，还会带进识别的整个包）。下载这一轮有要现裁的
    图片时才调用（_current_cropper，在调用 download 的线程里），只调用一次、结果记下；交回的不是函数或出错时按没接上算。
    悬浮窗这样接：出来之前不导入。

    没接上（也没给 download 的 cropper）时，带 crop 的清单项按下载失败算（NO_CROPPER），卡面也不下，不会把整张卡面当角色照
    存下来。"""
    global _cropper, _cropper_load
    with _cropper_lock:
        _cropper, _cropper_load = crop, load


def _current_cropper(items: list[AssetItem]) -> Callable[[bytes, tuple], bytes] | None:
    """这一轮下载用的裁图函数（use_cropper 接上的）。接上的是 load 时，这一轮有要现裁的图片（带 crop）才调用它，只调用一次：
    交回的函数记下以后一直用；交回的不是函数、出错时以后都按没接上算（同导入时就接不上：下次启动再试）。这一轮没有要现裁的
    图片时不调用，不为它导入裁图模块。"""
    global _cropper, _cropper_load
    if not any(item.crop is not None for item in items):
        return _cropper
    with _cropper_lock:  # 几轮下载同时要时，后来的等先来的导入完，用同一个结果
        if _cropper_load is not None:
            load, _cropper_load = _cropper_load, None
            try:
                found = load()
            except Exception:  # noqa: BLE001  接不上只是要现裁的那几张下载不了，不让整轮下载出错
                found = None
            _cropper = found if callable(found) else None
        return _cropper


# ---------------------------------------------------------------- 冷却记录


@dataclass(frozen=True)
class Cooldown:
    """被站点防护拦下以后的冷却记录（见模块说明「冷却」）。"""

    until: float  # 这个时刻（time.time()）以后再下载
    minutes: int  # 这一次冷却了多少分钟：再被拦时翻倍
    at: float  # 被拦的时刻
    reason: str = ""  # 怎么认出被拦的（悬浮窗写进错误记录）
    item: str = ""  # 认出被拦的是哪一张（清单项的 dir/file，同 AssetItem.name）：接着下时排到最后；问接口时被拦为空

    def active(self, now: float | None = None) -> bool:
        """还在冷却：没到 until。"""
        return (time.time() if now is None else now) < self.until


def _cooldown_path():
    return paths.download_dir() / COOLDOWN_FILE


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def cooldown(now: float | None = None) -> Cooldown | None:
    """冷却记录：被拦过、还没下全时有；没有、读不出、格式不对时为 None（照没被拦过算：按平时的速度下载）。
    until 比现在晚 COOLDOWN_LONGEST 以上的（系统时钟往回拨过、手改过）按从现在起冷却 minutes 分钟算，并把这样算出的记录
    写回去：不写回的话每次读都从读的那一刻重新算，到点的时刻跟着往后挪，一直到不了点；被拦的时刻也不晚于现在（再被拦时
    按它判断翻不翻倍）。"""
    now = time.time() if now is None else now
    with _local_lock:
        try:
            raw = json.loads(_cooldown_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict) or not _number(raw.get("until")):
            return None
        minutes = raw.get("minutes")
        if not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= COOLDOWN_MAX_MINUTES:
            minutes = COOLDOWN_MINUTES
        until = float(raw["until"])
        at = float(raw["at"]) if _number(raw.get("at")) else None
        reason = raw.get("reason") if isinstance(raw.get("reason"), str) else ""
        item = raw.get("item") if isinstance(raw.get("item"), str) else ""  # 没记这一项的：照原来的先后下
        if until - now > COOLDOWN_LONGEST:
            record = Cooldown(now + minutes * 60, minutes, now if at is None else min(at, now), reason, item)
            _write_cooldown(record)
            return record
    return Cooldown(until, minutes, until - minutes * 60 if at is None else at, reason, item)


def _write_cooldown(record: Cooldown | None) -> None:
    """写冷却记录（同 _update_local：临时文件、刷盘、整份替换）；None 时删掉。写不进时就算了：这次运行里悬浮窗照样按
    记着的时刻接着下，只是重启以后不认；删不掉时，下次下载照被拦过算（慢速），到点的时刻早已过去，不再等。"""
    with _local_lock:
        try:
            if record is None:
                _cooldown_path().unlink(missing_ok=True)
            else:
                text = json.dumps(dataclasses.asdict(record), ensure_ascii=False, indent=1)
                fileio.write_text(_cooldown_path(), text, suffix=".part")
        except OSError:
            pass


def _resume_at(now: float, minutes: int, error: DownloadError) -> float:
    """被拒以后到什么时候再请求（同 time.time()）：minutes 分钟以后，Retry-After 更长时按它（最多 COOLDOWN_LONGEST）。进到
    整分：写几点几分时，写的那一分就是到点的时刻。"""
    wait = max(minutes * 60.0, min(error.retry_after or 0.0, COOLDOWN_LONGEST))
    return math.ceil((now + wait) / 60) * 60.0


def _note_block(before: Cooldown | None, error: Blocked, now: float, item: str = "") -> Cooldown:
    """被拦了：记下冷却到什么时候、认出被拦的是哪一张（item）。第一次（或上一次被拦已过去 COOLDOWN_FRESH）冷却
    COOLDOWN_MINUTES 分钟，再被拦翻倍、最多 COOLDOWN_MAX_MINUTES；Retry-After 更长时按它（_resume_at）。"""
    minutes = COOLDOWN_MINUTES
    if before is not None and now - before.at < COOLDOWN_FRESH:
        minutes = min(before.minutes * 2, COOLDOWN_MAX_MINUTES)
    record = Cooldown(_resume_at(now, minutes, error), minutes, now, str(error), item)
    _write_cooldown(record)
    return record


# ---------------------------------------------------------------- 取图


class DownloadError(Exception):
    def __init__(self, reason: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(reason)
        self.status = status
        self.retry_after = retry_after  # 站点要求等多久（秒，回话里的 Retry-After）；没写时为 None


class Blocked(DownloadError):
    """站点防护拦下了这台电脑的请求（见模块说明「被拦就全部停下」）：所有下载线程停下，冷却以后再接着下。"""


class _Stopped(Exception):
    """停止标志置位了（取消下载）：下载的各层见到就收手，不再连接、读、重试；这张图片的结果是 CANCELLED。"""


def _read_body(resp, stop=None) -> bytes:
    """按块读完响应的正文（每块最多等一次网络），每块之前看停止标志，置位就抛 _Stopped，不等读完。声明了长度、没读够
    对方就断开时同 read() 一样抛 IncompleteRead。"""
    read = getattr(resp, "read1", None) or resp.read
    parts = []
    while True:
        if _stopped(stop):
            raise _Stopped
        chunk = read(CHUNK)
        if not chunk:
            break
        parts.append(chunk)
    data = b"".join(parts)
    left = getattr(resp, "length", None)  # http.client 的响应：声明的长度里还没读到的
    if isinstance(left, int) and not isinstance(left, bool) and left > 0:
        raise http.client.IncompleteRead(data, left)
    return data


def _header(headers, name: str) -> str:
    """回话里的一个头（不分大小写）：headers 是 email.message.Message（WinHTTP、urllib 给的）或普通的字典；没有时为空。"""
    try:
        value = headers.get(name)
        if value is None and isinstance(headers, dict):
            value = next((v for k, v in headers.items() if str(k).lower() == name.lower()), None)
    except AttributeError:  # 没有头（None）
        return ""
    return "" if value is None else str(value)


def _retry_after(headers, now: float | None = None) -> float | None:
    """回话里的 Retry-After（秒数，或 HTTP 日期）→ 还要等多少秒；没写、认不出时为 None。"""
    text = _header(headers, "Retry-After").strip()
    if not text:
        return None
    # 只认 ASCII 数字：「²」这类字符 isdigit() 也为真，float() 却报错，567 就认不出是被拦、下载照样接着请求
    if text.isascii() and text.isdigit():
        return float(text)
    from email.utils import parsedate_to_datetime

    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, when.timestamp() - (time.time() if now is None else now))


# 网页的开头（站点防护的拦截页、验证页）：图片以 PNG 的文件头开头，接口回的是 JSON，都不会这样开头
_PAGE_HEADS = (b"<!doctype html", b"<html", b"<head", b"<body", b"<script", b"<title", b"<meta")


def _is_page(data: bytes) -> bool:
    """拿到的是不是网页（站点防护的拦截页、验证页），而不是图片或接口的数据。"""
    if data.startswith(PNG_MAGIC):
        return False
    head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return head.startswith(_PAGE_HEADS) or b"<html" in head


def _status_text(code: int) -> str:
    """没被拦的 HTTP 错误在下载结果里的原因：「HTTP 404」这样写（_http_error）。"""
    return f"HTTP {code}"


# 最后报成限流（429、503）的原因：一张图试满 RETRIES 次都是限流（几条下载线程交替收到正常回话，凑不到连着 REJECT_LIMIT 次
# 被拒、不按被拦算）。说明的是站点那边忙，不是这一张的问题，is_own_failure 不算
_THROTTLED_REASONS = frozenset(_status_text(code) for code in THROTTLED)


def _http_error(e: urllib.error.HTTPError) -> DownloadError:
    """HTTP 错误 → DownloadError；站点防护拦下的（567、带拦截页面的 403）是 Blocked。限流（429、503）带上 Retry-After。"""
    headers = getattr(e, "headers", None)
    if e.code == BLOCKED_STATUS:
        return Blocked(f"HTTP {e.code}（请求被站点的安全策略拦截）", e.code, _retry_after(headers))
    if e.code == 403 and "text/html" in _header(headers, "Content-Type").lower():
        return Blocked(f"HTTP {e.code}（拿到的是拦截页面）", e.code)
    return DownloadError(_status_text(e.code), e.code, _retry_after(headers) if e.code in THROTTLED else None)


class _KeepAlive(winhttp.Session):
    """用系统自带的 WinHTTP（advisor/winhttp.py）取图，不必再带 OpenSSL。用法同 urllib.request.urlopen，另可给 stop
    （停止标志，见 _read_body）：__call__(req, timeout, stop) 返回正文已读完、有 read、能用 with 的对象，close() 关会话。

    同一主机的连接由 WinHTTP 在会话里自己复用（几百张小图，每张都重新握手会慢好几倍）。重定向、代理、证书校验都交给
    WinHTTP：重定向自动跟随（只禁止从 https 跳到 http）；代理照 urllib.request.getproxies()（环境变量优先，一个都没有时
    按注册表的手动代理；自动配置脚本不认），例外主机每个请求另判（proxy_bypass）。停止标志置位时 winhttp.Session 抛
    winhttp.Stopped，这里转成 _Stopped，下载的各层照旧收手。

    一个下载线程一个（含 close），只在用它的线程里建、用、关：从别的线程关会话打断不了正在进行的请求。
    """

    def __call__(self, req: urllib.request.Request, timeout: float = TIMEOUT, stop=None):
        try:
            return super().__call__(req, timeout, stop)
        except winhttp.Stopped:
            raise _Stopped from None


def _spacing() -> float:
    """平时相邻两个请求的开始至少隔多久（秒）：1/RATE（用到时才取，测试可以换）。"""
    return 1.0 / RATE


class _Pace:
    """一轮下载里各段（先下一遍、问接口、按新地址再下）、各条连接共用的计时（time.monotonic），见 RATE 的说明。

    - 每秒最多开始 RATE 个请求：平时下图片的取图器（不给 interval 的）每个请求开始之前在锁里领一个时刻（claim）：现在、
      上一个领到的时刻加 1/RATE，两者里晚的那个；领完出锁，再等到这个时刻。按固定间隔下的（问接口、被拦过以后的慢速：
      各段只有一条连接，间隔都比 1/RATE 长）不领，请求开始时记下时刻（started），之后领到的照样从它隔开，新一段的第一个
      请求也不和上一段的挨着。领了时刻却没发出去的（停下、被拦、等的时候被限流了）不退回：后面的最多多等 1/RATE 秒
      （几条线程同时作废时各领各的，前后加起来最多约 1.2 秒）。
      线程醒得晚的（带时限的等待按系统计时器的精度醒，常晚十几毫秒，机器忙时晚得更多），这个请求和下一个实际开始的时刻
      相隔会比 1/RATE 少这么多；领到的时刻照旧一个隔一个 1/RATE，不会越积越密，平均下来每秒仍不超过 RATE 个。
    - last：最后一个请求结束的时刻。各段的取图器是新建的，按固定间隔下的第一个请求也从它隔开这段的间隔，不和上一段的
      最后一个请求挨着。"""

    def __init__(self):
        self._lock = threading.Lock()  # 几个下载线程同时领、同时记
        self.last = 0.0
        self._next = 0.0  # 下一个请求最早什么时候开始

    def claim(self) -> float:
        """领一个请求开始的时刻（见类的说明）。"""
        with self._lock:
            at = max(time.monotonic(), self._next)
            self._next = at + _spacing()
            return at

    def started(self, when: float) -> None:
        """按固定间隔下的请求在 when 开始了：之后领到的时刻离它至少 1/RATE。"""
        with self._lock:
            self._next = max(self._next, when + _spacing())

    def note(self, when: float) -> None:
        """一个请求在 when 结束了。"""
        with self._lock:
            self.last = max(self.last, when)


class _Fetcher:
    """发请求、管请求的疏密。一个下载线程一个，只在这个线程里用（含 close）。agent：请求带的 User-Agent，不给时按
    USER_AGENT（开发用的取图工具另写自己的名称和用途）。站点防护拦下的（HTTP 567、带拦截页面的 403、拿到的是网页）抛
    Blocked，其余 HTTP 错误、网络出错抛 DownloadError。

    疏密分两种：
    - 给了 interval：这条连接上一个请求结束到下一个请求开始至少隔 interval 秒（问接口、被拦过以后的慢速、开发用的取图
      工具）。给了 pace（一轮下载各段共用的 _Pace）时，第一个请求也从它记着的最后一个请求隔开 interval，每个请求开始的
      时刻记进去。
    - 不给 interval：每个请求开始之前按 pace 领时刻，每秒最多开始 RATE 个请求，几条连接共用一个 pace 时合在一起算（平时
      下图片）；同一条连接上不另隔。没给 pace 时自己建一个：只有这一条连接，照样每秒最多 RATE 个。
    每个请求结束时记进 pace（有的话）。gate：一轮下载各下载线程共用的 _Gate，给了时每个请求之前先等限流（见 _turn）。
    sent：最近一个请求放行的时刻（time.monotonic）；回来被拒时 _Gate.rejected 按它分是不是同一阵。"""

    def __init__(self, opener: Callable | None = None, sleep: Callable[[float], None] = time.sleep,
                 interval: float | None = None, agent: str = USER_AGENT, pace: _Pace | None = None,
                 gate: _Gate | None = None):
        self._keep_alive = None if opener else _KeepAlive()
        self._open = opener or self._keep_alive
        self._sleep = sleep
        self._interval = interval
        self._agent = agent
        self._pace = pace if pace is not None or interval is not None else _Pace()
        self._gate = gate
        self._last = pace.last if pace is not None else 0.0
        self.sent: float | None = None

    def close(self) -> None:
        if self._keep_alive is not None:
            self._keep_alive.close()

    def _turn(self, stop) -> None:
        """等到这个请求能开始：先等限流（gate.hold，所有下载线程一起等），再等这条连接的间隔（给了 interval）或领到的
        时刻。等的时候别的连接又被限流了（gate.holds 变了）：照样等过限流、重新领时刻，领到的那个作废（不发请求，后面的
        最多多等一会儿）。停止标志置位时抛 _Stopped。放行的时刻（sent）在最后看 gate.holds 之前记：看的时候还没加上的那一次
        限流，记下的回来时刻一定比 sent 晚（_Gate.rejected 先加 holds、后记时刻），这个请求回来也被拒时和它算同一阵。"""
        gate = self._gate
        while True:
            holds = gate.holds if gate is not None else 0
            if gate is not None and gate.hold(self._sleep, stop):
                raise _Stopped
            at = self._last + self._interval if self._interval is not None else self._pace.claim()
            wait = at - time.monotonic()
            if wait > 0 and _wait(wait, self._sleep, stop):
                raise _Stopped
            self.sent = time.monotonic()
            if gate is None or gate.holds == holds:
                break
        if _stopped(stop):
            raise _Stopped
        if self._interval is not None and self._pace is not None:
            self._pace.started(time.monotonic())

    def get(self, url: str, stop=None) -> bytes:
        """url 的内容。stop（停止标志）置位时抛 _Stopped：等限流、等间隔或领到的时刻的时候，发请求之前，读正文的每一块之前
        都看。"""
        self._turn(stop)
        req = urllib.request.Request(url, headers={"User-Agent": self._agent})
        extra = {"stop": stop} if self._keep_alive is not None else {}  # 测试换上的 opener 只收 (req, timeout)
        try:
            with self._open(req, timeout=TIMEOUT, **extra) as resp:
                data = _read_body(resp, stop)
        except urllib.error.HTTPError as e:
            e.close()  # 释放错误响应占着的连接
            raise _http_error(e) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise DownloadError(f"网络错误：{getattr(e, 'reason', e)}") from e
        finally:
            self._last = time.monotonic()
            if self._pace is not None:
                self._pace.note(self._last)
        if _is_page(data):
            raise Blocked("拿到的是网页（拦截页面），不是图片或接口的数据（HTTP 200）", 200)
        return data


def query_imageinfo(titles: Iterable[str], fetch: Callable[[str], bytes], api: str = API) -> dict[str, dict]:
    """向 wiki（默认 BWIKI）的 imageinfo 接口要当前的下载地址、大小和 SHA-1，API_BATCH 张一个请求（请求之间的间隔由
    fetch 管，见 _Fetcher 的 interval）。返回 {文件页名（不带前缀）: info}；接口里没有的（wiki 上已删除）不在里面。"""
    titles = list(titles)
    found: dict[str, dict] = {}
    for start in range(0, len(titles), API_BATCH):
        batch = titles[start : start + API_BATCH]
        query = urllib.parse.urlencode({
            "action": "query", "prop": "imageinfo", "iiprop": "url|size|sha1|mime", "format": "json",
            "formatversion": "2", "titles": "|".join("File:" + t for t in batch),
        })
        data = json.loads(fetch(f"{api}?{query}"))
        q = data.get("query", {})
        back = {}  # 规范化后的页名 → 请求里给的页名
        for norm in q.get("normalized", []):
            back[norm["to"]] = norm["from"]
        for page in q.get("pages", []):
            info = (page.get("imageinfo") or [None])[0]
            asked = back.get(page.get("title", ""), page.get("title", ""))
            key = _strip_prefix(asked)
            if info:
                found[key] = info
    return found


def _strip_prefix(title: str) -> str:
    for prefix in ("File:", "文件:", "Image:", "图像:"):
        if title.startswith(prefix):
            return title[len(prefix) :]
    return title


def _wiki_title(item: AssetItem) -> str:
    return item.title or item.file.replace("_", " ")


def _stopped(stop) -> bool:
    if stop is None:
        return False
    if hasattr(stop, "is_set"):
        return bool(stop.is_set())
    return bool(stop() if callable(stop) else stop)


def _check(item: AssetItem, data: bytes, sha1: str, size: int) -> str | None:
    """校验下载内容，不通过时返回原因。"""
    if not data.startswith(PNG_MAGIC):
        return "不是 PNG 图片"
    if size and len(data) != size:
        return f"大小不符（{len(data)} ≠ {size}）"
    if sha1 and _sha1(data) != sha1:
        return "校验值不符"
    return None


def _save(item: AssetItem, data: bytes) -> None:
    """把下载好的图片整份写进文件：先写「.part」、刷盘、再改名（fileio.write_bytes）。先刷盘是关键：NTFS 只给文件名记
    日志，内容没落盘就改名的话，断电后会留下大小对、内容全是零的图，`is_present` 又只比大小，那张图永远认不出。

    写不进时报 OSError，只算这一张失败（_keep 把原因写成 WRITE_FAILED 开头，记进 download 的结果），其余照常写：
    下载在后台线程里，文件被占用时 fileio 自己的重试最多等约 1 秒，不卡界面；一张写不进（例如正被杀毒软件扫着）不连累
    其余的，悬浮窗上的「重试」也能再写。
    """
    fileio.write_bytes(item.path, data, suffix=".part")


def is_write_error(reason) -> bool:
    """download 结果里的一条失败原因是不是写盘出错（WRITE_FAILED 开头：图片下载好了、写不进识别图片的文件夹 paths.download_dir，
    发行版平时是个人目录里的，退回时、开发版是程序目录的 assets）。网络出错、校验不符、wiki 上已删除、取消了、被站点防护
    拦下暂停的都不算。"""
    return isinstance(reason, str) and reason.startswith(WRITE_FAILED)


def is_paused(reason) -> bool:
    """download 结果里的一条原因是不是被站点防护拦下、这一轮暂停了（PAUSED 开头）：不算失败，冷却以后接着下。"""
    return isinstance(reason, str) and reason.startswith(PAUSED)


def is_crop_failure(reason) -> bool:
    """download 结果里的一条失败原因是不是卡面裁不出角色照（CROP_FAILED 开头：卡面下到了、核过校验值，解不开或裁出来是
    空的）。这不是网络的问题，失败的全是这一种时悬浮窗不说检查网络。"""
    return isinstance(reason, str) and reason.startswith(CROP_FAILED)


def _no_session(reason) -> bool:
    """download 结果里的一条原因是不是下载线程建不了下载会话（NO_SESSION 开头）。"""
    return isinstance(reason, str) and reason.startswith(NO_SESSION)


def is_own_failure(reason) -> bool:
    """download 结果里的一条失败原因是不是这一张图片自己的问题：网络出错、5xx 试满次数、单独一次的 403、问过接口仍 404
    或内容对不上、卡面裁不出（CROP_FAILED）、清单里没有地址或卡面的校验值等。个别几张这样下不了时隔天再自动下载（见模块
    说明「个别图片一直下不了」）。被站点防护拦下暂停（is_paused）、取消（CANCELLED）、写不进（is_write_error）、没接上裁图
    （NO_CROPPER）、wiki 上已删除（GONE）、建不了下载会话（NO_SESSION：对每一张都一样）、最后报成限流（429、503：站点那边
    忙，见 _THROTTLED_REASONS）的不算，原因不明的（不是文字）也不算。"""
    return (isinstance(reason, str) and reason not in (CANCELLED, NO_CROPPER, GONE) and reason not in _THROTTLED_REASONS
            and not is_paused(reason) and not is_write_error(reason) and not _no_session(reason))


def _reasons(failed) -> list | None:
    """download 结果的第二项 → [(dir/file, 原因)]；有一项不是（文件名, 文字原因）的（原因不明，例如下载整个出错时悬浮窗原样
    交回的图片清单）为 None。"""
    if not isinstance(failed, (list, tuple)):
        return None
    out = []
    for entry in failed:
        if not (isinstance(entry, (list, tuple)) and len(entry) == 2 and isinstance(entry[1], str)):
            return None
        out.append((str(entry[0]), entry[1]))
    return out


def isolated(failed) -> bool:
    """一轮下载的失败（download 结果的第二项）是不是「个别图片下不了」（见模块说明「个别图片一直下不了」）：有失败，原因都
    知道，这一轮下完了（没有被拦下暂停、没有取消：没下完的分不清是哪一种），没有建不了下载会话的（NO_SESSION：不是哪一张图
    的问题，照全部或大部分没下下来算），而且失败的总张数不超过 FAILED_MANY 张：按缺了几张算，这一张自己的问题、写不进、
    wiki 上已删除、没接上裁图都算进去（几百张写不进时不能说「其余识别照常」）。悬浮窗按它换提示的写法（其余识别照常、之后
    自动再试、能关掉），download 按它记下失败的日期（deferred）。"""
    reasons = _reasons(failed)
    if not reasons or any(why == CANCELLED or is_paused(why) or _no_session(why) for _, why in reasons):
        return False
    return len(reasons) <= FAILED_MANY


def deferred(failed) -> list[str]:
    """一轮下载的失败里隔天再自动下载的那几张（dir/file）：这一轮是个别图片下不了（isolated）时，其中这一张自己的问题
    （is_own_failure）的那几张（写不进、已删除、没接上裁图、最后报成限流的照旧不记）；不是时为空表，失败的总张数超过
    FAILED_MANY 张时一张都不记。download 下完一轮按它记进覆盖清单（_note_failed），悬浮窗按它写错误记录。"""
    if not isolated(failed):
        return []
    return [name for name, why in _reasons(failed) if is_own_failure(why)]


def refusal_note(error: BaseException, now: float | None = None) -> str | None:
    """开发用的取图、生成清单的工具（tools/fetch_assets.py、fetch_chip_icons.py、make_assets_manifest.py）出错时用：error 是
    wiki 拒绝了这台电脑的请求（站点防护拦下的 Blocked，或限流 429、503）时，返回打印给维护者看的一句：怎么被拒的、几点几分
    以后再运行（COOLDOWN_MINUTES 分钟以后，Retry-After 更长时按它，见 _resume_at）；别的出错为 None，照常报错。
    这几个工具被拒就马上停下，不像首次运行的下载那样先等一次限流：开发时偶尔跑，不赶时间，接着请求只会把封禁拖长。"""
    if not isinstance(error, DownloadError) or not (isinstance(error, Blocked) or error.status in THROTTLED):
        return None
    now = time.time() if now is None else now
    at, today = time.localtime(_resume_at(now, COOLDOWN_MINUTES, error)), time.localtime(now)
    when = f"{at.tm_hour:02d}:{at.tm_min:02d}"
    if (at.tm_year, at.tm_yday) != (today.tm_year, today.tm_yday):
        when = f"{at.tm_mon} 月 {at.tm_mday} 日 {when}"
    return (f"wiki 拒绝了这台电脑的请求（{error}），已停下、不再发请求。{when} 以后再运行；"
            "在此之前用浏览器打开 wiki 也可能被拒绝。")


GONE = "wiki 上已没有这张图片"


def _wait(seconds: float, sleep: Callable[[float], None], stop) -> bool:
    """等 seconds 秒；等待期间 stop 置位就提前结束。返回是否已取消。"""
    if sleep is time.sleep and stop is not None:
        if hasattr(stop, "wait"):
            stop.wait(seconds)
        else:
            end = time.monotonic() + seconds
            while not _stopped(stop) and time.monotonic() < end:
                time.sleep(min(0.2, max(0.0, end - time.monotonic())))
    else:
        sleep(seconds)
    return _stopped(stop)


# ---------------------------------------------------------------- 一轮下载


class _Gate:
    """一轮下载里各下载线程共用的状态。halt 是下载线程看的停止标志：认出被拦的线程当场置位（block），别的线程不开始
    新的请求、也不重试，不等调用 download 的线程下一次查看。"""

    def __init__(self, halt: threading.Event, probing: bool = False):
        self.halt = halt
        self.blocked: Blocked | None = None
        self.blocked_probe = False  # 被拦的是冷却期间的试探请求：冷却记录不改
        self.blocked_item = ""  # 认出被拦的是哪一张（dir/file，记进冷却记录）；问接口的那一批被拦时为空
        self.probing = probing  # 冷却期间：这一轮的第一个请求是试探
        self.passed = False  # 试探通了
        # 要求一起等过几次（每次限流加一）：取图器领了时刻、等的时候这个数变了，就照样等过限流、重新领（_Fetcher._turn）
        self.holds = 0
        self._lock = threading.Lock()
        self._not_before = 0.0  # 限流时所有下载线程都等到这个时刻（time.monotonic）再发请求
        self._streak = 0  # 连着被拒了几次（同一阵只算一次，见 rejected）；站点照常回话时清零
        self._since = 0.0  # 最近算进 _streak 的那一次被拒回来的时刻（time.monotonic）：在这之前放行的请求和它算同一阵

    def block(self, error: Blocked, item: str = "") -> None:
        """认出被拦（item 是在下的那一张）：记下最先认出的那一次，当场让所有下载线程停下。"""
        with self._lock:
            if self.blocked is None:
                self.blocked, self.blocked_probe, self.blocked_item = error, self.probing, item
        self.halt.set()

    def hold(self, sleep: Callable[[float], None], stop) -> bool:
        """限流时等到约好的时刻再发请求（每个请求之前，在取图器里：_Fetcher._turn）；返回等待期间是否停下了（取消、被拦）。"""
        with self._lock:
            wait = self._not_before - time.monotonic()
        return wait > 0 and _wait(wait, sleep, stop)

    def answered(self) -> None:
        """站点照常回了话（图片、接口的数据、404、5xx 等）：被拒的次数清零；冷却期间的试探通了。"""
        with self._lock:
            self._streak = 0
            if self.probing:
                self.probing, self.passed = False, True

    def unanswered(self, error: DownloadError) -> None:
        """请求没得到回话（网络出错），平时照常重试。冷却期间的试探请求没得到回话时说不准站点还拦不拦：照旧停下（只发这
        一个请求），按仍被拒算，抛 Blocked（冷却记录不改）。"""
        with self._lock:
            probing = self.probing
        if probing:
            raise Blocked(f"{error}（冷却期间的试探请求没有得到回话）", error.status)

    def rejected(self, error: DownloadError, sent: float | None = None) -> None:
        """被拒了一次（限流 429、503，或不带页面的 403）；sent 是这个请求放行的时刻（_Fetcher.sent）。冷却期间的试探请求
        被拒、连着第 REJECT_LIMIT 次被拒、限流要求等得比 RETRY_AFTER_MAX 还久时按被拦算，抛 Blocked。其余的限流：所有下载
        线程按 Retry-After（没写时 RETRY_AFTER 秒）一起等（hold）再接着下。403 只算那一张失败：单独一次多半是那一个文件
        自己的问题（没有权限、已删除），为它停下全部下载、冷却半个多小时不值；连着两次（中间没有照常回话的）才像是站点在
        拒绝这台电脑。

        同一阵被拒只算一次（模块说明「限流单独一次」）：sent 早于最近算进去的那一次被拒回来的时刻的（那时已经在路上），回来
        也被拒，不再往「连着被拒」里加，限流的照样按它的 Retry-After 一起等；那之后才放行的又被拒，才算连着又一次。不知道
        什么时候放行的（sent 为 None）每次都算。同一阵里算进去的那一次已经凑够 REJECT_LIMIT、按被拦算了的（那个线程正让所有
        下载线程停下），后回来的这个照取消收手（抛 _Stopped）：被拦记在算够的那一张上，不在这一张上另记一次。"""
        wait = (RETRY_AFTER if error.retry_after is None else error.retry_after) if error.status in THROTTLED else 0.0
        with self._lock:
            same = self._streak > 0 and sent is not None and sent < self._since
            if not same:
                self._streak += 1
            streak, probing = self._streak, self.probing
            hold = not probing and streak < REJECT_LIMIT and 0 < wait <= RETRY_AFTER_MAX
            if hold:
                self.holds += 1  # 先加、后记时刻：取图器先记 sent、后看 holds（_Fetcher._turn），没看到这一次的 sent 都更早
            now = time.monotonic()
            if not same:
                self._since = now
            if hold:
                self._not_before = max(self._not_before, now + wait)
        if probing:
            raise Blocked(f"{error}（冷却期间的试探请求仍被拒绝）", error.status, error.retry_after)
        if streak >= REJECT_LIMIT:
            if same:
                raise _Stopped
            raise Blocked(f"{error}（连着 {streak} 次被拒）", error.status, error.retry_after)
        if wait > RETRY_AFTER_MAX:
            raise Blocked(f"{error}（要求等待 {wait:g} 秒）", error.status, wait)


@dataclass(eq=False)
class _Job:
    """一张要下载的图片和它这一轮的进展：按哪个地址、校验值下（本地覆盖清单记过 wiki 上更新的，按它），问过接口没有。"""

    index: int
    item: AssetItem
    url: str
    sha1: str
    size: int
    overridden: bool = False  # 本地覆盖清单里记着这张（又和清单对得上时清掉）
    refreshed: bool = False  # 问过接口了：再对不上、再 404 就算失败
    data: bytes | None = None  # 内容和清单对不上时下到的内容：问过接口，对得上新的校验值就直接收下，不再下载
    why: str = ""  # 要问接口的原因（「HTTP 404」或内容对不上的原因）：接口帮不上时就是这张的失败原因
    cut: Callable[[bytes, tuple], bytes] | None = None  # 从卡面现裁角色照的函数（带 crop 的清单项用，见 use_cropper）

    @classmethod
    def of(cls, index: int, item: AssetItem, local: dict, cut: Callable | None = None) -> _Job:
        current = effective(item, local)
        return cls(index, item, current.url, current.sha1, current.size, overridden=current is not item, cut=cut)


REFRESH = object()  # _get_image 的结果：要向接口刷新地址和校验值（攒起来一批问）
AGAIN = object()  # _ask 的结果：按接口给的地址、校验值再下一次


def _keep(job: _Job, data: bytes, stop) -> str | None:
    """存下校验通过的图片；地址、大小、校验值和清单不同时记进本地覆盖清单，又和清单一致时清掉覆盖记录。取消以后不再写
    文件（download 已经按「已取消」报了，不再等这个线程）。

    带 crop 的（从卡面现裁）：data 是卡面，按裁法裁出角色照再存（卡面不落盘），并在覆盖清单里记下按哪张卡面、哪份裁法裁的
    （is_present 按它判断在不在）。先存图、后记录：中途断电只是下次重下一遍，不会把没裁好的当成在。"""
    if _stopped(stop):
        return CANCELLED
    item = job.item
    cropped = None
    if item.crop is not None:
        if job.cut is None:
            return NO_CROPPER
        if not job.sha1:  # 没有校验值、核对不了的卡面不交去解码（_get_image 一开始就不下；问过接口也没给时到这里）
            return NO_CARD_SHA1
        try:
            data = job.cut(data, item.crop)
        except ValueError as e:  # 卡面解不开、裁出来是空的（cardcrop.CropError）：再下一次也一样，不重试
            return f"{CROP_FAILED}：{e}"
        if _stopped(stop):
            return CANCELLED
        cropped = {"card": job.sha1, "crop": list(item.crop), "size": len(data)}
    try:
        _save(item, data)
    except OSError as e:  # 写不进：再下载一次也一样，不重试（文件被占用时 fileio 已经等过）；和网络出错分开记
        return f"{WRITE_FAILED}：{type(e).__name__}: {e}"
    updated = None
    # 备用地址上的图片不记「wiki 上更新过」：地址里带内容的 SHA-1，图换了地址也跟着换，没有 wiki 上的更新记录可记
    if not item.mirror and (job.url, job.sha1, job.size) != (item.url, item.sha1, item.size):
        updated = {"url": job.url, "size": job.size, "sha1": job.sha1}
    local = load_local()
    # 照原样下载的这一项还留着现裁的记录（清单以前把它列成从卡面现裁）：一并清掉，连同 effective 没用的旧更新记录；以前个别
    # 下载失败记的日期（failed）也清掉。备用地址上的图片还留着以前从 wiki 下载时的更新记录的（effective 不用它）也清掉
    if (updated or cropped or job.overridden or item.name in local["gone"] or item.name in local["cropped"]
            or item.name in local["failed"] or (item.mirror and item.name in local["updated"])):
        _update_local(item.name, updated=updated, cropped=cropped)
    return None


def _get_mirror_image(job: _Job, fetcher: _Fetcher, sleep: Callable[[float], None], stop):
    """下载一张放在备用地址上的图片（清单项带 mirror）：404、内容对不上、403 只算这一张失败，不问 wiki 的接口；
    限流（429、503）、网络出错同普通图片，按 BACKOFF 等过再试，最多 RETRIES 次。备用地址的回话不算进 gate（wiki 的站点防护
    和限流）：备用地址那边出错不能让同一轮里 wiki 的下载停下，也不能让悬浮窗说成 wiki 拒绝了这台电脑。"""
    if not job.url:
        return "清单里没有下载地址"
    reason = "未尝试"
    backoff = False
    for attempt in range(RETRIES):
        if _stopped(stop):
            return CANCELLED
        if backoff and _wait(BACKOFF[min(attempt - 1, len(BACKOFF) - 1)], sleep, stop):
            return CANCELLED
        try:
            data = fetcher.get(job.url, stop)
        except Blocked as e:  # 403 带拦截页面、拿到的是网页：这一张失败，不按 wiki 被拦算
            return str(e)
        except DownloadError as e:
            reason = str(e)
            if e.status in (403, 404):  # 这一张自己的问题：不问接口，也不再试（同一个地址再要一次多半还是一样）
                return reason
            backoff = True
            continue
        problem = _check(job.item, data, job.sha1, job.size)
        if problem is None:
            return _keep(job, data, stop)
        return problem  # 地址里带内容的 SHA-1，内容对不上是这一张坏了，没有 wiki 接口可问
    return reason


def _get_image(job: _Job, fetcher: _Fetcher, gate: _Gate, sleep: Callable[[float], None], stop):
    """下载一张图片（第一遍，或问过接口以后再下一次）。返回 None（存好了）、这一张的失败原因（含 CANCELLED），或 REFRESH
    （旧地址 404、内容和清单对不上，还没问过接口的）。被拦时抛 Blocked；被拒、限流、试探请求怎么算见 _Gate。"""
    if job.item.mirror:
        return _get_mirror_image(job, fetcher, sleep, stop)
    if not job.url:
        return "清单里没有下载地址"
    if job.item.crop is not None and job.cut is None:
        return NO_CROPPER  # 卡面下了也裁不成角色照：不白下（一张一两兆，每次启动都会再来一遍），也不多给站点添请求
    if job.item.crop is not None and not job.sha1:
        return NO_CARD_SHA1  # 下了也核对不了、不交去解码：同样不白下
    reason = "未尝试"
    # 上一次是网络出错、5xx：下一次之前按 BACKOFF 等。限流时所有下载线程一起等（gate.hold）、按疏密等到时刻，都在取图器里
    # （_Fetcher._turn），等的时候取消了抛 _Stopped，这一张同样算「已取消」
    backoff = False
    for attempt in range(RETRIES):
        if _stopped(stop):
            return CANCELLED
        if backoff and _wait(BACKOFF[min(attempt - 1, len(BACKOFF) - 1)], sleep, stop):
            return CANCELLED
        try:
            data = fetcher.get(job.url, stop)
        except Blocked:
            raise
        except DownloadError as e:
            reason = str(e)
            if e.status in THROTTLED or e.status == 403:
                gate.rejected(e, fetcher.sent)  # 连着被拒（同一阵只算一次）、试探请求被拒时抛 Blocked
                if e.status == 403:
                    return reason  # 不带页面的 403 单独一次：只算这一张失败，同一个文件再要一次多半还是 403
                backoff = False
                continue
            if e.status is None:
                gate.unanswered(e)  # 试探请求没得到回话时抛 Blocked
            else:
                gate.answered()
            if e.status == 404:  # 图片在 wiki 上换过地址、删了
                if job.refreshed:
                    return reason
                job.why = reason
                return REFRESH
            backoff = True
            continue
        gate.answered()
        problem = _check(job.item, data, job.sha1, job.size)
        if problem is None:
            return _keep(job, data, stop)
        if job.refreshed:
            return problem
        job.data, job.why = data, problem  # 可能是 wiki 上的图片更新了：问过接口再比一次
        return REFRESH
    return reason


def _refreshed(job: _Job, info: dict | None, stop):
    """接口给的这一张的当前地址、大小和校验值（info；接口里没有时为 None）→ 这一张的结果：内容对不上、下到的正是 wiki 上
    更新过的图片时直接收下（None 或写不进的原因）；旧地址 404、接口里也没有的是 wiki 上已删除（GONE，记下日期和问的是哪个
    文件页，见 _recently_gone）；其余按接口给的再下一次（AGAIN）。"""
    item = job.item
    if not info:
        if job.data is None:
            _update_local(item.name, gone={"date": clock.today().isoformat(), "title": _wiki_title(item)})
            return GONE
        return job.why  # 内容对不上、接口里又查不到：照原来的原因算失败
    url, sha1 = info.get("url") or job.url, info.get("sha1") or job.sha1
    size = int(info.get("size") or job.size)
    data = job.data
    if data is not None and (sha1, size) != (job.sha1, job.size) and _check(item, data, sha1, size) is None:
        job.url, job.sha1, job.size, job.data = url, sha1, size, None
        return _keep(job, data, stop)
    job.url, job.sha1, job.size, job.refreshed, job.data = url, sha1, size, True, None
    return AGAIN


def _ask(batch: list[_Job], fetcher: _Fetcher, gate: _Gate, stop) -> list:
    """同一个 wiki 的一批（最多 API_BATCH 张）向 imageinfo 接口要当前的地址和校验值，一个请求：[(图片, 结果)]，结果同
    _refreshed。接口被拦时抛 Blocked；限流单独一次时同下载图片，按 Retry-After 等过（取图器里的 gate.hold）再问一次（不再
    问的话，这一批会按原来的原因算失败，悬浮窗说成下载失败）；等的时候取消了抛 _Stopped，这一批算「已取消」。接口本身出错
    （网络、别的 HTTP 错误、回的不是想要的 JSON）时这一批按原来的原因算失败，不再为它们多发请求。"""
    api = batch[0].item.api or API
    titles = [_wiki_title(job.item) for job in batch]
    for _ in range(RETRIES):
        try:
            found = query_imageinfo(titles, lambda u: fetcher.get(u, stop), api)
        except Blocked:
            raise
        except DownloadError as e:
            if e.status in THROTTLED or e.status == 403:
                gate.rejected(e, fetcher.sent)  # 连着被拒（同一阵只算一次）时抛 Blocked
                if e.status in THROTTLED:
                    continue  # 限流单独一次：等过再问
            elif e.status is None:
                gate.unanswered(e)
            else:
                gate.answered()
            return [(job, job.why) for job in batch]
        except (ValueError, TypeError, AttributeError, KeyError):  # 回的不是想要的 JSON
            gate.answered()
            return [(job, job.why) for job in batch]
        gate.answered()
        return [(job, _refreshed(job, found.get(_wiki_title(job.item)), stop)) for job in batch]
    return [(job, job.why) for job in batch]


def _batches(jobs: list[_Job]) -> list[list[_Job]]:
    """要问接口的图片按所在的 wiki 分开，每 API_BATCH 张一批。"""
    by_api: dict[str, list[_Job]] = {}
    for job in jobs:
        by_api.setdefault(job.item.api or API, []).append(job)
    return [group[i : i + API_BATCH] for group in by_api.values() for i in range(0, len(group), API_BATCH)]


class _Timings:
    """一轮下载里每张图片的用时，几个下载线程同时记（照 _Book 的办法加锁）。

    一张的一次尝试从开始请求（fetch 之前，等限速、等领到的时刻也算在内）算到写完文件或认出失败；同一张下了两遍的
    （旧地址 404、内容对不上，问过接口再下一次）加在一起，就是这一张这一轮一共花了多久。用时按 download 的 clock_now
    取，这个钟被拨回去时记 0，不记负数。"""

    SLOWEST = 5  # 记最慢的几张

    def __init__(self, clock: Callable[[], float]):
        self._clock = clock
        self._lock = threading.Lock()
        self._seconds: dict[int, float] = {}  # index（todo 里的下标）→ 这一张这一轮的总秒数
        self._names: dict[int, str] = {}
        self._sizes: dict[int, int] = {}  # 写好的文件有多少字节（没写好、取不到时没有这一项）

    def add(self, job: _Job, started: float, saved: bool) -> None:
        """记一张的一次尝试：从 started（开始请求的时刻）到现在。saved：这一次写好了文件（不够时字节数取文件的）。"""
        elapsed = max(0.0, self._clock() - started)
        size = None
        if saved:
            try:
                size = job.item.path.stat().st_size
            except OSError:  # 取不到字节数就不记这一项
                size = None
        with self._lock:
            self._seconds[job.index] = self._seconds.get(job.index, 0.0) + elapsed
            self._names[job.index] = job.item.name
            if size is not None:
                self._sizes[job.index] = size

    def saved(self, job: _Job) -> None:
        """问接口那一步直接收下的图片：补上写好的文件的字节数（用时已经记过）。取不到时不记这一项。"""
        try:
            size = job.item.path.stat().st_size
        except OSError:
            return
        with self._lock:
            self._sizes[job.index] = size

    def slowest(self, count: int = SLOWEST) -> list[dict]:
        """最慢的几张，按秒数从慢到快：[{"name": dir/file, "s": 秒（两位小数）, "bytes": 字节数（写好的才有）}]。
        刚开始就被取消、被拦下的几张（没有字节数、用时不到 0.1 秒）不算：它们没在下载，列进最慢的几张没有意义。
        耗时相同的按名字排，先后固定（诊断包前后两份好对）。"""
        with self._lock:
            rows = sorted((index for index in self._seconds
                           if index in self._sizes or self._seconds[index] >= 0.1),
                          key=lambda index: (-self._seconds[index], self._names[index]))
            out = []
            for index in rows[:count]:
                entry = {"name": self._names[index], "s": round(self._seconds[index], 2)}
                if index in self._sizes:
                    entry["bytes"] = self._sizes[index]
                out.append(entry)
            return out


class _Book:
    """一轮下载的结果：每张图片最后的结果（None 是存好了，其余是原因），按处理完的先后记；每处理完一张调一次 progress。"""

    def __init__(self, todo: list[AssetItem], progress):
        self.todo, self.progress = todo, progress
        self.done: dict[int, str | None] = {}

    def _note(self, job: _Job, outcome, later: list) -> None:
        if outcome is REFRESH or outcome is AGAIN:
            later.append(job)
            return
        if job.index in self.done:
            return
        self.done[job.index] = outcome
        if self.progress and outcome != CANCELLED and not is_paused(outcome):
            handled = sum(1 for v in self.done.values() if v != CANCELLED and not is_paused(v))
            self.progress(handled, len(self.todo), job.item.name)

    @staticmethod
    def going(stop, halt: threading.Event) -> bool:
        return not _stopped(stop) and not halt.is_set()

    def run(self, tasks: list, threads: int, make_fetcher: Callable[[], _Fetcher], work, stop, halt: threading.Event,
            gate: _Gate, name: str) -> list:
        """在 threads 个下载线程里做 tasks（work(task, fetcher) → [(图片, 结果)]；task 是一张图片，或问接口的一批），
        在调用 download 的线程里收结果，要接着做的（REFRESH、AGAIN）收起来返回。每 STOP_POLL 看一次停止标志和被拦；
        取消了、被拦了时只给下载线程 STOP_GRACE 秒收尾，卡在网络上的不等（见 download），这期间交回的结果照收。"""
        later: list = []
        if not tasks:
            return later
        jobs: queue.SimpleQueue = queue.SimpleQueue()
        for task in tasks:
            jobs.put(task)
        results: queue.SimpleQueue = queue.SimpleQueue()  # (图片, 结果)：下载线程交回，在这里汇总
        expected = sum(len(task) if isinstance(task, list) else 1 for task in tasks)

        def worker() -> None:
            # 开始做第一件时才建：建不了（例如读不了系统的证书、代理设置不对）时这一件的每一张按 NO_SESSION 算失败（不是哪
            # 一张图的问题，见 isolated），下一件再建
            fetcher = None
            try:
                while not halt.is_set():
                    try:
                        task = jobs.get_nowait()
                    except queue.Empty:
                        return
                    batch = task if isinstance(task, list) else [task]
                    if fetcher is None:
                        try:
                            fetcher = make_fetcher()
                        except Exception as e:  # noqa: BLE001  同下载出错，只是原因另带 NO_SESSION 开头
                            for job in batch:
                                results.put((job, f"{NO_SESSION}：{type(e).__name__}: {e}"))
                            continue
                    try:
                        out = work(task, fetcher)
                    except _Stopped:
                        out = [(job, CANCELLED) for job in batch]
                    except Blocked as e:  # 被拦：当场让所有下载线程停下；下的是一张图时记下是哪一张（接着下时排到最后）
                        gate.block(e, "" if isinstance(task, list) else task.item.name)
                        out = [(job, f"{PAUSED}（{e}）") for job in batch]
                    except Exception as e:  # noqa: BLE001  单个文件出错不影响其余
                        out = [(job, f"{type(e).__name__}: {e}") for job in batch]
                    for pair in out:
                        results.put(pair)
            finally:
                if fetcher is not None:
                    fetcher.close()

        pool = [threading.Thread(target=worker, name=f"{name}_{i}", daemon=True)
                for i in range(min(max(1, threads), len(tasks)))]
        for thread in pool:
            thread.start()
        got = 0
        try:
            while got < expected and not _stopped(stop) and gate.blocked is None:
                try:
                    job, outcome = results.get(timeout=STOP_POLL)
                except queue.Empty:
                    if not any(t.is_alive() for t in pool) and results.empty():
                        break  # 下载线程都意外结束了：剩下的算没下完，不一直等
                    continue
                got += 1
                self._note(job, outcome, later)
        finally:
            if got < expected:
                halt.set()
        if got < expected:  # 取消了、被拦了：没开始的不再开始，正在下的给一小会儿收尾，卡在网络上的不等（见 download）
            deadline = time.monotonic() + STOP_GRACE
            for thread in pool:
                thread.join(max(0.0, deadline - time.monotonic()))
            while True:
                try:
                    job, outcome = results.get_nowait()
                except queue.Empty:
                    break
                self._note(job, outcome, later)
        else:
            for thread in pool:
                thread.join()  # 都处理完了：各线程只剩关掉自己的连接
        return later

    def complete(self) -> bool:
        """这一轮的图片都处理完了：都存好了，或是 wiki 上已删除的（下不了，GONE_RETRY_DAYS 天内不再自动重试）。"""
        return len(self.done) == len(self.todo) and all(v is None or v == GONE for v in self.done.values())

    def cut_short(self) -> bool:
        """这一轮有没做完的：还没处理到的，或做到一半取消了的（CANCELLED）。每张都有了结果（含这一张自己的失败）时为假。"""
        return len(self.done) < len(self.todo) or CANCELLED in self.done.values()

    def result(self, blocked: bool) -> tuple[list[str], list[tuple[str, str]]]:
        """(成功的 dir/file 列表, [(失败的 dir/file, 原因)])。没处理完的：被拦了是 PAUSED，否则是 CANCELLED。"""
        ok: list[str] = []
        failed: list[tuple[str, str]] = []
        for index, outcome in self.done.items():
            name = self.todo[index].name
            if outcome is None:
                ok.append(name)
            elif blocked and outcome == CANCELLED:
                failed.append((name, PAUSED))
            else:
                failed.append((name, outcome))
        rest = PAUSED if blocked else CANCELLED
        failed += [(item.name, rest) for index, item in enumerate(self.todo) if index not in self.done]
        return ok, failed


def _settle(before: Cooldown | None, gate: _Gate, book: _Book, now: float) -> None:
    """一轮下完以后的冷却记录：被拦了记下冷却到什么时候、认出被拦的是哪一张（冷却期间的试探请求仍被拒时不改）；下全了
    清掉；冷却期间的试探通了、又没下全（取消了、有别的失败）时把到点的时刻提前到现在：之后照旧按慢速下，重启以后也不用
    再等。"""
    if gate.blocked is not None:
        if not gate.blocked_probe:
            _note_block(before, gate.blocked, now, gate.blocked_item)
    elif book.complete():
        if before is not None:
            _write_cooldown(None)
    elif gate.passed and before is not None and before.until > now:
        _write_cooldown(dataclasses.replace(before, until=now))


def download(
    items: Iterable[AssetItem] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    stop=None,
    *,
    opener: Callable | None = None,
    sleep: Callable[[float], None] = time.sleep,
    workers: int | None = None,
    clock_now: Callable[[], float] = time.time,
    cropper: Callable[[bytes, tuple], bytes] | None = None,
    stats: dict | None = None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """下载图片。items 默认是 missing() 的结果。

    progress(done, total, name)：每处理完一个文件调用一次（name 是 dir/file），在调用 download 的线程里调用。
    stop：threading.Event 或返回 bool 的函数。置位后不再开始新的文件，正在下的见到就收手（等限流、等请求的时刻的时候，连接、
    发请求、重试之前，读正文的每一块之前），下到一半的不写文件；没下完的算失败，原因「已取消」。置位以后最多约
    STOP_POLL＋STOP_GRACE 秒就返回。
    workers：同时开几条连接（每个下载线程一条），不给时按 WORKERS；不管几条，整个这一轮每秒最多开始 RATE 个请求（模块说明
    「不给站点添压力」）。被拦过（有冷却记录）时一律按 SLOW_WORKERS 条连接、每次隔 SLOW_INTERVAL 秒。还在冷却时
    （cooldown().active()）只试一个请求，见模块说明「冷却」。
    冷却记录里记着上一次认出被拦的是哪一张时，这一张排到最后，其余照 items 的先后。
    返回 (成功的 dir/file 列表, [(失败的 dir/file, 原因)])；被拦时这一轮没下的原因以 PAUSED 开头（is_paused），冷却到
    什么时候见 cooldown()。这一轮是个别图片下不了（isolated）时，这一张自己的问题的那几张记下今天的日期（deferred、
    _note_failed），隔天再由 missing 算进来；items 是调用方给的就照给的下，不看这笔记录。
    cropper：从卡面现裁角色照的函数（带 crop 的清单项用），不给时按 use_cropper 接上的（_current_cropper）。
    opener、sleep、clock_now（现在的时刻，同 time.time）只给测试用。
    stats：给了时在这一轮结束时填好（正常下完、取消、被拦、出错都填，出错时能填多少填多少），记这一轮的用时：
    seconds（总秒数，墙上时间，含先下一遍、问接口、按新地址再下三段）、total（开始时要下的张数）、ok、failed（成功的、
    失败的张数）、blocked（被站点防护拦下）、paused（这一轮提前停下：取消或被拦）、threads（同时几条连接）、slow（是不是
    被拦过以后的慢速）、slowest（最慢的 5 张，按秒数从慢到快；每张 name 是 dir/file、s 是秒数、bytes 是写好的文件的字节数）。
    一张的用时从开始请求算到写完文件或认出失败，含等限速、等领到的时刻和重试前的退避；同一张下了两遍的加在一起。
    不给 stats 时不记这些，下载和返回值都不变。

    分三步：先把每张图片下一遍；旧地址 404、内容和清单对不上的攒起来，按所在的 wiki 每 API_BATCH 张一批问接口；地址、校验值
    换了的再下一次。哪一步被拦都整体停下（模块说明「被拦就全部停下」）。清单项带 api 字段、来自别的站点时，也跟着一起停、
    一起冷却：分开各停各的要另记冷却、另出提示，用户看到的说法也更绕。

    下载线程是守护线程，连接由各线程自己关；取消时只给它们 STOP_GRACE 秒收尾，不等卡在网络上的：卡住的请求（对方不回话、
    正文读到一半不再来）打断不了，从别的线程关连接也没用，等的话要等到超时（TIMEOUT），退出程序时进程
    跟着被拖住。不等就照常退出，系统收回连接；它们后来醒过来见到停止标志就收手，不重试、不写文件。
    """
    started = clock_now()
    timing = _Timings(clock_now)
    todo = [item for item in (missing() if items is None else items) if not item.bundled]
    now = clock_now()
    before = cooldown(now)
    if before is not None and before.item:
        # 上一次认出被拦的那一张排到最后（模块说明「冷却」）：试探请求、接着下的头一个请求都不用它；排序是稳定的
        todo.sort(key=lambda item: item.name == before.item)
    slow = before is not None
    # 下载线程看的停止标志：调用方的 stop 由 _Book.run 转过去（stop 可能只是个函数，不能等它），被拦时由认出的线程当场置位，
    # download 返回（含出错）时也置位，之后下载线程不再开始新的请求
    halt = threading.Event()
    gate = _Gate(halt, probing=slow and before.active(now))
    threads = SLOW_WORKERS if slow else max(1, WORKERS if workers is None else workers)
    gap = SLOW_INTERVAL if slow else None
    book = _Book(todo, progress)
    local = load_local()
    cut = cropper if cropper is not None else _current_cropper(todo)
    jobs = [_Job.of(index, item, local, cut) for index, item in enumerate(todo)]
    # 三段共用：每秒最多 RATE 个请求合在一起算，新一段的第一个请求也和上一段的隔开（_Pace）
    pace = _Pace()

    def pages() -> _Fetcher:
        return _Fetcher(opener, sleep, gap, pace=pace, gate=gate)

    def image(job: _Job, fetcher: _Fetcher) -> list:
        began = clock_now()
        try:
            outcome = _get_image(job, fetcher, gate, sleep, halt)
        except BaseException:  # 被拦、取消：这一张没下完，用时照样记（填统计时也要算上）
            timing.add(job, began, saved=False)
            raise
        timing.add(job, began, saved=outcome is None)
        return [(job, outcome)]

    def ask(batch: list[_Job], fetcher: _Fetcher) -> list:
        out = _ask(batch, fetcher, gate, halt)
        for job, outcome in out:
            if outcome is None:  # 内容对得上、直接收下：补记字节数
                timing.saved(job)
        return out

    ok: list[str] = []
    failed: list[tuple[str, str]] = []
    try:
        try:
            later = book.run(jobs, threads, pages, image, stop, halt, gate, "assets")
            if later and book.going(stop, halt):
                later = book.run(_batches(later), 1, lambda: _Fetcher(opener, sleep, API_INTERVAL, pace=pace, gate=gate),
                                 ask, stop, halt, gate, "assets_api")
                if later and book.going(stop, halt):
                    book.run(later, threads, pages, image, stop, halt, gate, "assets")
        finally:
            halt.set()
        _settle(before, gate, book, clock_now())
        ok, failed = book.result(gate.blocked is not None)
        _note_failed(deferred(failed))
        return ok, failed
    finally:
        _note_run(todo, ok, failed)
        if stats is not None:
            try:
                stats.update({
                    "seconds": round(max(0.0, clock_now() - started), 1),
                    "total": len(todo),
                    "ok": len(ok),
                    "failed": len(failed),
                    "blocked": gate.blocked is not None,
                    # 停止标志是每张都有了结果以后才置上的（例如刚下完就退出）不算提前停下
                    "paused": gate.blocked is not None or (_stopped(stop) and book.cut_short()),
                    "threads": threads,
                    "slow": slow,
                    "slowest": timing.slowest(),
                })
            except Exception:  # noqa: BLE001  统计不能影响下载
                pass


# ---------------------------------------------------------------- 给对局报告的识别图片情况

# 这次运行里下载成了几张（从 wiki、从备用地址）、失败了几张（被拦暂停、取消的不算失败：没试完）
_run_lock = threading.Lock()
_run = {"wiki": 0, "mirror": 0, "failed": 0}
# report_status 的结果留这么久（秒）：每次三选一都要一份，不每次都把几百张图片的文件大小查一遍
STATUS_KEEP_S = 120.0
STATUS_MISSING_MAX = 30  # 缺的图片最多列这么多张的文件名
_status_cache: tuple[float, dict] | None = None


def _note_run(todo: list[AssetItem], ok: list[str], failed: list[tuple[str, str]]) -> None:
    """一轮下载结束：按清单项的 mirror 数成功的从哪下的，失败的去掉暂停、取消的（没试完）；记进这次运行的计数，
    并作废 report_status 留着的结果（缺的少了）。出错时只少这一轮的计数。"""
    global _status_cache
    try:
        mirror = {item.name for item in todo if item.mirror}
        bad = sum(1 for _name, reason in failed
                  if not (is_paused(reason) or (isinstance(reason, str) and reason.startswith(CANCELLED))))
        with _run_lock:
            for name in ok:
                _run["mirror" if name in mirror else "wiki"] += 1
            _run["failed"] += bad
            _status_cache = None
    except Exception:  # noqa: BLE001  计数不能影响下载
        pass


def report_status(now: float | None = None) -> dict:
    """对局报告里识别图片的情况（不带路径）：manifest 随程序带的清单生成的日期、serial 存着的最新清单的序号（没有时 None）、
    expected 清单里要下载的图片张数（随发行版附带的不算）、present 本机有几张（文件在、大小对得上，见 is_present）、
    missing 缺的那几张的文件名（最多 STATUS_MISSING_MAX 张，按清单的先后）、wiki、mirror、failed 这次运行里从 wiki、
    从备用地址下载成的张数和失败的张数。结果留 STATUS_KEEP_S 秒（下载结束时作废）；三项计数每次现取。"""
    global _status_cache
    now = time.monotonic() if now is None else now
    with _run_lock:
        cached = _status_cache
        run = dict(_run)
    if cached is None or not 0 <= now - cached[0] < STATUS_KEEP_S:
        manifest = load_manifest()
        with _local_lock:
            local = load_local()
        items = [item for item in manifest.items if not item.bundled]
        absent = [item for item in items if not is_present(item, local)]
        remote = _stored_remote() if update.USE_MIRROR else None
        cached = (now, {
            "manifest": manifest.generated if isinstance(manifest.generated, str) else None,
            "serial": remote.serial if remote is not None else None,
            "expected": len(items),
            "present": len(items) - len(absent),
            "missing": [item.file for item in absent[:STATUS_MISSING_MAX]],
        })
        with _run_lock:
            _status_cache = cached
    return {**cached[1], "missing": list(cached[1]["missing"]), **run}
