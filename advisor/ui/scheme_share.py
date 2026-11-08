"""「导入/导出筹码方案」窗口：悬浮窗「筹码方案」卡片的「导入/导出」按钮打开。

不分页签，导出在上、导入在下：
- 导出：按角色筛选、勾选我的方案，选择格式（短字符串，或列出全部筹码的可读文字）；预览框里就是要复制的内容（每套
  一行），「复制到剪贴板」把它原样放进剪贴板。「全选」后复制，就是导出当前筛选下的全部方案。默认的角色筛选由 hero
  给出（None 为全部角色）；给了角色时预先勾选该角色正在用的方案。角色下拉框只列有我的方案的角色（筛选中的角色
  没有方案时也列出），下拉列表每一行的画法和悬浮窗相同（widgets.draw_combo_rows）。上次选的格式在本次运行里记住
  （类属性 last_format，各窗口共用；要跨重启记住时由悬浮窗存进设置：format_changed、set_format）。
- 导入：粘贴框里可以一次粘贴多套，前后带别的文字也行；内容变了以后稍等片刻（PARSE_DELAY_MS）自动识别，逐套显示
  结果：能导入的写角色、名称、目标流派（由标记推出来，schemes.target_schools）和各档个数，有问题的写「第 N 套（名称）：」
  和原因（问题的种类由 advisor/schemes.py 的 parse_share 给出，句子在这里拼），不能导入的那一套不导入。
  「导入 N 套为我的方案」只新建、不覆盖已有的方案，同名时名称后加「（2）」；导入后清空粘贴框，在识别结果的位置一套一行写明
  导入了哪几套，并发出 schemes_imported（导入的方案所属的角色名，不重复，按先后）。导入不自动换用：每一行右边一个「使用这套
  方案」按钮，点了发出 use_requested（所属角色, 方案 id），悬浮窗把它设为这个角色用的方案（所属角色正是悬浮窗当前的角色时
  当场换用），再把结果交回来（show_use_result），按钮换成结果那一句。一次导入好几套时每套各自一个按钮，行多了在识别结果区里
  上下滚动，窗口不跟着变高。方案文件只读（来自更新版本的拾星助手）时不能导入，导出照常。
- 作业站：导入这一块右边的「去作业站找方案」打开悬浮窗当前角色的方案列表：只发 hub_requested（plans），窗口自己不打开
  这个网址、不管当前角色和地图（由悬浮窗按当前角色和这一局认出的地图拼地址，见 advisor/hub.py）。
- 发到作业站：导出这一块的「发到作业站」把勾选的一套直接发上去（advisor/hub_api.py 的 publish_scheme）：正好勾了一套才发，
  没勾、勾了两套以上时写一句怎么做，不联网；先在界面线程里生成一次带地图的短字符串（生成不了时写原因，不联网），取好这一套
  的快照交给后台线程（后台线程不碰方案存储和界面）。本机没登录作业站时先开登录对话框（HubLogin：电视式短码，在浏览器里
  授权，程序不经手密码），登录好了接着发。作业站上已有角色和四档筹码一样的一套（409）时在提示行里问要不要更新那一套
  （update_scheme）。发好了写作业站上的状态，另有「在作业站上看」；出错按种类写一句，作业站给的说明照样按纯文本显示、
  太长截短。导出这一块另有「已登录作业站：昵称」和「退出作业站」（只读本机的登录信息，不联网；没登录时不显示）。账号上的
  事（登录、发、更新、退出）同一时间只做一件，和按编号导入互不相干；关窗口时都叫停（退出那一件照样做完，让作业站撤销
  登录信息，只是结果不回到窗口）。发方案不改方案文件：方案文件只读时照样能发。
- 按编号导入：导入这一块的粘贴框上面一行，输入作业站上方案的编号或方案网址，点「按编号导入」（或在框里按回车）才连作业站
  取这一套（advisor/hub_api.py 的 parse_ref、fetch_scheme：只发编号，凭链接分享的方案另带网址里的随机码，和程序版本）；
  窗口打开、输入的时候都不连。认不出的输入不联网，在提示行写怎么输入。在后台线程里取（HubCall），同一时间只取一套，取的
  时候输入框和按钮不能点；取回的分享码（短字符串优先，没有时用可读文字）放进粘贴框：空着（只有空白也算）时直接放，已有
  内容时另起一行接在后面，同一段文字已经在里面时不再接；放进去以后马上识别，不自动导入。正在取、取回了哪一套、出错的原因
  都写在这一行下面的提示行里，不弹窗；作业站上的名称是别人写的，照样按纯文本显示，太长时截短。关窗口时叫停，晚到的结果
  不碰已经关掉的窗口。取方案不改方案文件：方案文件只读时照样能取（导入照旧不能点）。
- 作业站的入口（「发到作业站」和它下面的提示行、「已登录作业站」那一行、「去作业站找方案」、按编号导入那一行）由
  hub.ENABLED 控制，网站没上线时关着、不显示，不联网，也不读凭据管理器。

一次最多识别 schemes.PASTE_MAX_SCHEMES 套、前 schemes.PASTE_MAX_CHARS 个字，超出的部分另起一行说明。parse_share 用的
模糊匹配对象是共用的，不能在两个线程里同时调用：识别只在界面线程里做（2 万字的粘贴约 0.1 秒），按编号导入的后台线程只取
文字，取回以后交回界面线程再识别。

名称、作者、说明和未能识别的筹码名都是别人写的文字，一律按纯文本显示：识别结果是富文本，每一行整体转义后再着色；
方案列表的复选框文字里 & 写成 &&（否则会被当成快捷键的标记吃掉）；预览框和粘贴框是纯文本框。界面是繁体时，方案名称（本机的、
作业站上取回的）和作业站给的说明在显示处按界面语言转（lang.t），作业站上的昵称照原文；预览框、粘贴框里的分享码和发上去的
内容照原文。

窗口和悬浮窗、编辑窗口共用同一个 SchemeStore，自己不缓存方案：显示和重新激活时按 store 的最新内容刷新（refresh），
别处改了方案时也可以直接调 refresh。不碰 Session 和悬浮窗：换用方案经 use_requested 交给悬浮窗做。

样式表和识别结果每一行的颜色在皮肤里（theme.share_style、theme.ShareLook）；换皮肤时（theme.set_skin）当场换（apply_skin）。
怎么排按当前皮肤（theme.Layouts.SHARE，见 LAYOUTS）。
"""
from __future__ import annotations

import copy
import dataclasses
import html
import logging
import math
import threading
import traceback
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QTextCursor
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QPlainTextEdit,
    QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSpacerItem, QStackedWidget, QVBoxLayout, QWidget, QWidgetItem,
)
from shiboken6 import isValid

from .. import hub, hub_api
from .. import schemes as S
from ..data import TIERS, heroes_in_group_order
from ..errors import log_error
from . import chrome, decor, lang, names, theme
from .glyphs import ICON_BOOK
from .text import tip_paragraphs_html
from .theme import SHARE as L
from .widgets import FlowLayout, draw_combo_rows, fill_hero_combo, free_item
from .window_base import SkinnedWindow

log = logging.getLogger(__name__)

# ------------------------------------------------------------ 界面文字

TITLE = "导入/导出筹码方案"  # 窗口标题，也写在窗口顶部
CLOSE = "关闭"
# 左侧页签的排法（side_tabs）：两个页签；页签和卡片的标签写「导出」「导入」，卡片里的说明去掉 EXPORT_HEAD、IMPORT_HEAD
# 开头的「导出：」「导入：」
TAB_EXPORT = "导出"
TAB_IMPORT = "导入"
# 窗口底部的说明
SHARE_NOTE = "短字符串只是把方案内容编码压缩成一串字符，便于复制，不是保密手段：任何装了拾星助手的人都能导入并看到内容。"

# 导出
EXPORT_HEAD = "导出：勾选方案，选择格式后点「复制到剪贴板」。"
HERO_LABEL = "角色"
ALL_HEROES = "全部角色"
SELECT_ALL = "全选"
FORMAT_LABEL = "格式"
# 两种格式的单选按钮（键同 schemes.FORMAT_SHORT、FORMAT_TEXT），和复制结果那句里的叫法
FORMAT_OPTION = {"short": "短字符串（约 20～70 个字符）", "text": "可读文字（列出全部筹码，较长）"}
FORMAT_NAME = {"short": "短字符串", "text": "可读文字"}
ITEM = "{hero} · {name}"  # 方案列表的一行：角色名 · 方案名
# 还没有一套我的方案、导出区的列表空着时，列表里写下一步怎么做（有了方案就收起）
EXPORT_EMPTY = ("还没有可以导出的方案。导出的是自己的方案：点击悬浮窗「筹码方案」卡片的「编辑方案」按钮，新建或复制一套我的方案"
                "以后，这里就会列出来；导入的方案也会列在这里。")
COPY = "复制到剪贴板"
COPIED = "已复制 {n} 套方案的{format}"
COPY_FAILED = "复制失败，请稍后再试。"  # 剪贴板被别的程序占着、内容没写进去时（读回来核对），写在导出这一块的警告里
# 筹码编号表读不出来（data/chip_codes.json 缺失或损坏）时短字符串生成不了，这几套不列入预览
EXPORT_FAILED = "{n} 套方案无法生成短字符串（筹码编号表读取失败），未列入；请改用可读文字"
# 作业站（advisor/hub.py）：导出这一块右边的「发到作业站」把勾选的一套直接发上去（见下面「发到作业站」一段）；导入这一块
# 右边的「去作业站找方案」打开悬浮窗当前角色的方案列表（角色、地图由悬浮窗决定，窗口自己不管）
FIND_ON_HUB = "去作业站找方案"
FIND_ON_HUB_TIP = ("在浏览器里打开作业站上其他玩家分享的当前角色的筹码方案（资料库没收录这个角色时看全部角色的；对局中认出了"
                   "地图或手动选了地图时，只看这张地图能用的）：看中的复制下来，粘贴到上面导入；也可以记下方案的编号，在上面"
                   "按编号导入")
POST_TO_HUB = "发到作业站"
# 悬停说明（三段）：发出去的是什么、发上去以后先不公开、第一次要登录
POST_TO_HUB_TIP = ("把勾选的一套方案直接发到作业站：只发这一套的角色、名称、四档筹码、短说明和适用地图，加上程序版本和资料日期\n"
                   "发上去以后先不公开，到作业站网页上点「公开」后才会出现在方案列表里\n"
                   "第一次发要先登录作业站：在浏览器里核对短码、点「允许」，拾星助手不经手密码")
# 按编号导入（advisor/hub_api.py）：导入这一块粘贴框上面一行，输入作业站上方案的编号或方案网址，点「按编号导入」（或在框里
# 按回车）才连作业站取这一套。不另外弹窗说会联网：按钮和输入框的悬停说明（FETCH_TIP，两段）写明从作业站取、发出去的是什么、
# 不点不连。结果写在这一行下面的提示行里：正在取（FETCH_BUSY）、取回了哪一套（FETCH_DONE 等，名称太长时截短）、出错的原因
# （FETCH_FAILED 等）
FETCH = "按编号导入"
FETCH_PLACEHOLDER = "作业站方案编号或网址"
FETCH_TIP = ("输入作业站上方案的编号或方案页面的网址，点「按编号导入」（或按回车）从作业站取回这一套方案，放进下面的粘贴框；"
             "不自动导入，核对识别结果后再导入\n"
             "只发送方案编号（用链接分享的方案另带网址里的随机码）和程序版本；不点就不连作业站")
FETCH_EMPTY = "请先输入作业站上方案的编号（例如 123），或者方案页面的网址。"
FETCH_BAD_REF = "无法识别这个编号或网址：请输入作业站上方案的编号（例如 123），或者方案页面的网址。"
FETCH_BUSY = "正在从作业站取 {n} 号方案……"
FETCH_DONE = "已从作业站取回「{name}」（{n} 号），请核对识别结果后再导入"
FETCH_DONE_UNNAMED = "已从作业站取回 {n} 号方案，请核对识别结果后再导入"  # 作业站上这一套没有名称时
# 取回的那一段文字已经在粘贴框里（例如同一个编号取了两次）：不再接一遍
FETCH_SAME = "已从作业站取回「{name}」（{n} 号），粘贴框里已有这一套，未重复放入"
FETCH_SAME_UNNAMED = "已从作业站取回 {n} 号方案，粘贴框里已有这一套，未重复放入"
# 取方案出错时提示行写的句子，键是 hub_api.HubError 的 kind（hub_api.NETWORK 等）。取得太频繁（rate_limited）写要等多久：
# 作业站给了秒数时写 FETCH_RATE_LIMITED（FETCH_RATE_MINUTES_FROM 秒以上按分钟写，不足一分钟的算一分钟），没给、给的是 0 时写
# FETCH_RATE_LATER。用户停下的（stopped：关窗口时）不写。fetch_scheme 不会抛的种类、别的异常写 FETCH_ERROR；别的异常另记进
# 错误记录（标题 FETCH_LOG；只记异常的类型和出错的位置，不记异常的说明，免得带出作业站回的内容）
FETCH_FAILED = {
    "network": "无法连接作业站，请检查网络后再试。",
    "not_found": "作业站上没有这一套：编号不对，或已删除、未公开。用链接分享的方案请输入带随机码的完整网址。",
    "server": "作业站暂时出错，请稍后再试。",
    "bad_response": "作业站返回的内容无法识别，请稍后再试；多次出现时请更新拾星助手。",
}
FETCH_RATE_LIMITED = "取方案过于频繁，请 {wait}后再试。"
FETCH_WAIT_SECONDS = "{n} 秒"
FETCH_WAIT_MINUTES = "{n} 分钟"
FETCH_RATE_LATER = "取方案过于频繁，请稍后再试。"
FETCH_ERROR = "取方案时出错，请稍后再试。"
FETCH_LOG = "按编号导入时取方案出错（拾星助手仍在运行）"
# 发到作业站（hub_api.publish_scheme、update_scheme）：正好勾了一套才发；本机没登录时先登录（登录对话框），登录好了接着发。
# 正在发、发好了、出错的原因写在「发到作业站」下面的提示行里；发好了另有「在作业站上看」。作业站上已有一样的一套（409）时
# 提示行里问要不要更新那一套（「更新那一套」「不更新」，「在作业站上看」看已有的那一套）。作业站给的说明（内容不对时各字段的
# 说明、账号不能发的原因）是网站写的，照样按纯文本显示，合起来太长时截短（SITE_TEXT_SHOWN），只显示、不写进错误记录
POST_PICK_ONE = "请只勾选一套方案，再点「发到作业站」。"  # 没勾、勾了两套以上时（不联网）
# 生成不了这一套的短字符串（schemes.to_short 报 ValueError）时不联网
POST_NO_SHORT = "这一套无法生成短字符串（筹码编号表读取失败，或其中的角色、筹码、地图没有编号），发不了；请更新拾星助手后再试。"
POST_BUSY = "正在把「{name}」发到作业站……"
POST_BUSY_UNNAMED = "正在把这一套发到作业站……"  # 这一套没有名称时
UPDATE_BUSY = "正在更新作业站上的 {n} 号方案……"
# 发好了：按作业站回的状态（hub_api.STATUSES）写，新发的（POST_DONE）和更新的（UPDATE_DONE）各一套；从程序发上去的默认
# 不公开（draft），到网页上点「公开」才出现在方案列表里
POST_DONE = {
    "draft": "已发到作业站，暂未公开；到网页上点「公开」后才会出现在方案列表里",
    "public": "已发到作业站，已公开：方案列表里就能看到",
    "unlisted": "已发到作业站：这一套不出现在方案列表里，只有拿到链接的人能看到",
}
UPDATE_DONE = {
    "draft": "已更新作业站上的 {n} 号方案，暂未公开；到网页上点「公开」后才会出现在方案列表里",
    "public": "已更新作业站上的 {n} 号方案：方案列表里显示的就是更新后的内容",
    "unlisted": "已更新作业站上的 {n} 号方案：这一套不出现在方案列表里，只有拿到链接的人能看到",
}
VIEW_ON_HUB = "在作业站上看"  # 提示行下面的按钮：在浏览器里打开作业站上的这一套
# 作业站上已有角色和四档筹码都一样的一套（409）：问要不要用勾选的这一套更新它
POST_DUPLICATE = "作业站上已有一套角色和四档筹码都一样的方案（{n} 号）：是否用勾选的这一套更新它？"
DUP_UPDATE = "更新那一套"
DUP_SKIP = "不更新"
# 新发时回 404：作业站上没有收方案的地方（网站还没开这一项，或者程序比网站旧、地址对不上）。更新时的 404 是那一套不在了，
# 见 POST_FAILED
POST_UNAVAILABLE = "作业站上暂时发不了方案，请稍后再试；多次出现时请更新拾星助手。"
POST_INVALID = "作业站没有收下这一套：{detail}"  # 400：detail 是作业站给的各字段的说明
POST_INVALID_BARE = "作业站没有收下这一套：内容不符合要求，请检查名称和说明后再试。"  # 400 没带说明时
POST_FORBIDDEN = "这个作业站账号现在不能发方案：{reason}"  # 403：reason 是作业站给的原因
POST_FORBIDDEN_BARE = "这个作业站账号现在不能发方案，原因请到作业站网页上查看。"
POST_UNAUTHORIZED = "作业站的登录已失效，请重新登录：再点一次「发到作业站」。"  # 401：本机的登录信息已删掉
# 其余出错（键是 hub_api.HubError 的 kind）：not_found 是更新时作业站上那一套不在了、或不是这个账号发的；太频繁时同按编号
# 导入写要等多久；用户停下的不写；别的种类、别的异常写 POST_ERROR（别的异常另记进错误记录，标题 POST_LOG）
POST_FAILED = {
    "network": "无法连接作业站，请检查网络后再试。",
    "not_found": "作业站上已没有那一套，或那一套不是这个账号发的。",
    "server": "作业站暂时出错，请稍后再试。",
    "bad_response": "作业站返回的内容无法识别，请稍后再试；多次出现时请更新拾星助手。",
}
POST_RATE_LIMITED = "发方案过于频繁，请 {wait}后再试。"
POST_RATE_LATER = "发方案过于频繁，请稍后再试。"
POST_ERROR = "发方案时出错，请稍后再试。"
POST_LOG = "发方案到作业站时出错（拾星助手仍在运行）"
# 登录好了、登录信息却没能存进这台电脑时（hub_api.store_login 返回假）：这一次照样发，提示行里另起一行写这句
LOGIN_NOT_SAVED = "登录信息没能存在这台电脑上，下次发方案还要重新登录。"
SITE_TEXT_SHOWN = 120  # 作业站给的说明合起来最多显示的字数，再长的截短、末尾加省略号
SITE_TEXT_SEP = "；"
# 已登录作业站：导出这一块写「已登录作业站：昵称」和「退出作业站」；昵称是网站上的人起的，照样按纯文本显示，太长时截短
# （NICK_SHOWN）；读出来是空串时写 LOGGED_IN_UNNAMED；没登录时这一行整个不显示
LOGGED_IN = "已登录作业站：{name}"
LOGGED_IN_UNNAMED = "已登录作业站"
LOGOUT = "退出作业站"
LOGOUT_BUSY = "正在退出作业站……"
LOGOUT_DONE = "已退出作业站"
LOGOUT_FAILED = "没能退出作业站，请稍后再试。"  # 退出以后本机还读得出登录信息（没删掉）时
LOGOUT_LOG = "退出作业站时出错（拾星助手仍在运行）"
NICK_SHOWN = 16
# 登录作业站的对话框（电视式短码）：先向作业站要短码，大字显示，自动在浏览器里打开授权页面；用户在网页上核对短码、点
# 「允许」以后拿到登录信息。打不开浏览器时写网址，让用户自己打开。「允许」「不允许」是授权页面上两个按钮的字，照网页写：
# 网页上的按钮改了名字，这里和 LOGIN_FAILED 的 denied 跟着改
LOGIN_TITLE = "登录作业站"
LOGIN_ASKING = "正在向作业站要登录短码……"
LOGIN_STEPS = "已在浏览器里打开作业站的授权页面：请核对页面上的短码和下面的一样，再点「允许」。"
LOGIN_NO_BROWSER = "浏览器没有自动打开：请在浏览器里打开 {url} ，输入下面的短码后点「允许」。"
LOGIN_WAITING = "正在等作业站上的授权……"
LOGIN_PRIVACY = "拾星助手不经手密码：授权以后，这台电脑只存一份登录信息（交给 Windows 加密保管），点「退出作业站」时删掉。"
LOGIN_REOPEN = "再打开一次网页"
LOGIN_CANCEL = "取消"
LOGIN_RETRY = "重新登录"
# 登录没成功（键是 hub_api.HubError 的 kind）：写在对话框里，下面换成「重新登录」「关闭」；用户点了取消（stopped）不写
LOGIN_FAILED = {
    "denied": "网页上点了「不允许」，没有登录；需要时点「重新登录」。",
    "expired": "短码已过期，没有登录；点「重新登录」换一个新的短码。",
    "network": "无法连接作业站，请检查网络后点「重新登录」。",
    "server": "作业站暂时出错，请稍后点「重新登录」再试。",
    "bad_response": "作业站返回的内容无法识别，请稍后再试；多次出现时请更新拾星助手。",
}
LOGIN_RATE_LIMITED = "登录过于频繁，请 {wait}后再试。"
LOGIN_RATE_LATER = "登录过于频繁，请稍后再试。"
LOGIN_ERROR = "登录时出错，请稍后再试。"
LOGIN_LOG = "登录作业站时出错（拾星助手仍在运行）"

# 导入
IMPORT_HEAD = "导入：把方案文字或短字符串粘贴到下面（可以一次粘贴多套，每套一行）"
FOUND = "识别到 {n} 套："
FOUND_NONE = "识别到 0 套"  # 粘贴的内容里没有找到方案时（后面没有逐套的结果，不写冒号）
# 能导入的一套：「✓ 凛 · 标记充能　目标流派 标记、充能；必选 2、推荐 4、不选 3」。没有目标流派、没标筹码的那一项不写，
# 只写标了的档位（个数为 0 的不写）
RESULT_OK = "✓ {hero} · {name}"
RESULT_DETAIL = "　{detail}"
RESULT_SCHOOLS = "目标流派 {schools}"
RESULT_MAPS = "适用地图 {maps}"  # 能导入的一套标了地图时，接在目标流派后面；通用的不写
RESULT_COUNT = "{tier} {n}"
RESULT_SEP = "；"
LIST_SEP = "、"
LIST_MAPS = "适用地图：{maps}"  # 导出列表里标了地图的那一套的悬停说明；通用的不写
# 勾选的方案里有标了地图的：这一版分享出去的内容不带地图（schemes.WRITE_MAPS 为假时写在预览框下面）
MAPS_TEMP_NOTE = "勾选的方案里有标了适用地图的：分享出去的内容暂时不带适用地图，对方导入后算全地图通用。"
# 有问题的一套：每个问题一行，前面写第几套和名称
PROBLEM_LINE = "⚠ 第 {index} 套（{name}）：{text}"
# 问题的句子，键是 schemes.Problem.kind（schemes.PROBLEM_*）；schemes.FATAL_PROBLEMS 里的几种让这一套无法导入。
# codes_unavailable：筹码编号表（data/chip_codes.json）读不出来（程序文件不完整时才会出现），短字符串里的筹码认不出
PROBLEM_TEXT = {
    "bad_code": "字符串不完整或被改动（校验不符），无法导入；请粘贴完整的一串",
    "bad_text": "方案文字格式不正确，无法导入；请粘贴完整的一段",
    "newer_version": "这一套来自更新版本的拾星助手，当前版本无法导入",
    "codes_unavailable": "筹码编号表读取失败，无法导入短字符串；请改为粘贴方案文字",
    "unknown_hero_id": "角色编号 {hero_id} 资料库未收录，已跳过",
    "unknown_hero": "角色「{hero}」资料库未收录，已跳过",
    "unknown_codes": "{count} 个筹码不在当前版本的资料库中（可能来自更新版本的拾星助手），已忽略",
    "removed_chips": "已忽略已移除的筹码：{names}",
    "unknown_names": "未能识别的筹码名：{names}（已忽略）",
    "unknown_map_codes": "{count} 张地图不在当前版本的资料库中（可能来自更新版本的拾星助手），已忽略",
    "unknown_map_names": "未能识别的地图：{names}（已忽略）",
    "name_truncated": "名称超过 12 个字，已截断",
    "note_truncated": "说明超过 60 个字，已截断",
}
# 一次粘贴超出处理上限时（schemes.PASTE_MAX_CHARS、PASTE_MAX_SCHEMES）
TEXT_CUT = "⚠ 粘贴的内容超过 2 万字，只识别了前 2 万字；其余部分请分次粘贴"
SKIPPED = "⚠ 另有 {n} 套超出一次最多 50 套的限制，未识别；请分次粘贴"
IMPORT = "导入 {n} 套为我的方案"
CLEAR = "清空"
IMPORT_NOTE = "导入后新建为我的方案，不覆盖已有的；同名时名称后加「（2）」"
IMPORTED = "已导入筹码方案「{name}」（{hero}）"
# 导入以后每一套「已导入……」那一行右边的按钮：设为它所属角色使用的方案（导入时不自动换用）。点了以后按钮换成结果：
# 所属角色正是悬浮窗当前的角色时悬浮窗当场换用（USED_NOW）；别的角色只记下，下次玩这个角色时用它（USED_LATER）；
# 这套方案已经在别处删掉了时写 USE_GONE
USE = "使用这套方案"
USED_NOW = "悬浮窗已改用这套方案"
USED_LATER = "已设好，下次玩{hero}时用这套方案"
USE_GONE = "这套方案已被删除"
# 方案文件来自更新版本的拾星助手（只读）时，导入按钮下面的提示（和悬浮窗的方案文件提示同一句）
READ_ONLY = "筹码方案文件来自更新版本的拾星助手，当前只能查看，不能修改"

# ------------------------------------------------------------ 尺寸和样式

# 打开时的大小（SIZE，逻辑像素；可以拖大，屏幕放不下时按可用高度缩小）和按屏幕可用高度缩小时给标题栏和边框留的高度
# （SCREEN_MARGIN）在皮肤里（theme.ShareLook），这两个名字也能按属性取（__getattr__）
PARSE_DELAY_MS = 100  # 粘贴框的内容变了以后，停这么久没有再变才识别（连续输入时不每个字都识别一次）
LIST_MIN_H = 56  # 方案列表至少约两行高
RESULTS_MIN_H = 44
# 预览框、粘贴框的（首选高度, 最矮）：平时约三行，窗口放不下时约两行；多出来的高度给方案列表和识别结果区
PREVIEW_H = (64, 44)
PASTE_H = (64, 48)
GAME_LIST_MIN_H = 84  # 经典主题-黑、反色的排法：方案列表至少两行高（每行是带底色的一块，比默认的高）
GAME_PREVIEW_H = (46, 38)  # 经典主题-反色（上下两张卡片）和霓虹贴纸、白色海景房（RGB_KIT）的预览框：约一行半
# 怎么排：排法的名字 → 搭界面的方法名（theme.Layouts.SHARE，不认识的按 default）。default：导出在上、导入在下；
# side_tabs：游戏内风格，左侧「导出」「导入」两个页签；side_by_side：导出、导入两块左右并排（经典主题-黑）；stacked：
# 导出、导入两张卡片上下排，最下面一条横幅写短字符串的说明（经典主题-反色）；banded：导出、导入两块上下排，每块上面一条
# 黑色斜带写「导出」「导入」和说明（霓虹贴纸）；capsules：上下两张白卡片，卡片里左上角一枚渐变胶囊写「导出」「导入」，
# 勾选方案是一排可点的胶囊、格式是分段按钮（白色海景房）
LAYOUTS = {"default": "_build", "side_tabs": "_build_tabs", "side_by_side": "_build_columns", "stacked": "_build_stacked",
           "banded": "_build_banded", "capsules": "_build_capsules"}
# split：导出、导入两块左右并排，中间一条竖线，每块上面是编号的分组标题（碳纤流光）
LAYOUTS["split"] = "_build_split"
ICON_COPY = 0xE8C8  # 「复制到剪贴板」前面的系统图标（复制）
ICON_ADD = 0xE710  # 「导入 N 套为我的方案」前面的系统图标（加号）
ICON_HUB = ICON_BOOK  # 「去作业站找方案」「发到作业站」「按编号导入」前面的图标（自己画的、正面看翻开的书）
# 提示行里作业站上的方案名称最多显示几个字：同本机方案名称的上限（网站上的名称可以更长，导入时照样截成这么长），再长的
# 截短、末尾加省略号
FETCH_NAME_SHOWN = S.NAME_MAX
FETCH_RATE_MINUTES_FROM = 120  # 取得太频繁时作业站要求等这么多秒以上，改按分钟写
ELLIPSIS = "…"

# 识别结果每一行的颜色、样式表在皮肤里（theme.ShareLook 的 TONE_COLOR，theme.share_style）。这两个名字（TONE_COLOR、STYLE）
# 也能按属性取，见 __getattr__


def __getattr__(name: str):
    """颜色常量名和样式表（scheme_share.TONE_COLOR、STYLE 等）：当前皮肤的值，给测试和预览工具用。"""
    if name == "STYLE":
        return theme.share_style()
    return theme.legacy(__name__, name, ("share", "common"))


def button_text(text: str) -> str:
    """按钮、复选框上的文字原样显示：& 写成 &&（单个 & 会被当成快捷键的标记，不显示出来）。"""
    return text.replace("&", "&&")


def result_html(lines: list[tuple[str, str]]) -> str:
    """识别结果 [(色调, 文字)] → 富文本：每一行整体转义（文字里的网页标记原样显示），保留空格，按色调着色（当前皮肤）；
    皮肤给这种色调写了色块（ShareLook.TONE_BOX）时整行写在色块里。"""
    colors = L.TONE_COLOR
    out = []
    for tone, text in lines:
        box = L.TONE_BOX.get(tone)
        if box:
            fill, ink = box
            out.append(f'<table cellspacing="0" cellpadding="5" width="100%" style="background-color:{fill}; '
                       f'margin-bottom:4px"><tr><td style="color:{ink}; white-space:pre-wrap; font-weight:bold">'
                       f'{html.escape(text)}</td></tr></table>')
        else:
            out.append(f'<div style="color:{colors.get(tone, L.TEXT_COLOR)}; white-space:pre-wrap">'
                       f'{html.escape(text)}</div>')
    return "".join(out)


def problem_text(problem: S.Problem) -> str:
    """一个问题的句子（不含「第 N 套（名称）：」）。不认识的种类为空文字。"""
    template = PROBLEM_TEXT.get(problem.kind)
    if template is None:
        return ""
    return template.format(count=problem.count, names=LIST_SEP.join(lang.t(n) for n in problem.names),
                           hero_id=problem.hero_id, hero=lang.t(problem.hero))


def ok_line(scheme: S.Scheme, db) -> str:
    """能导入的一套：「✓ 角色 · 名称　目标流派 X、Y；适用地图 龙宫游乐园、魔法学院；必选 2、推荐 4」。目标流派由标记
    推出来（列进方案的流派里有必选、推荐的，schemes.target_schools；db 是资料库），分享格式里不带；地图只在标了时写。"""
    parts = []
    schools = S.target_schools(db, scheme.tiers)
    if schools:
        parts.append(RESULT_SCHOOLS.format(schools=LIST_SEP.join(lang.t(s) for s in schools)))
    if scheme.maps:
        parts.append(RESULT_MAPS.format(maps=LIST_SEP.join(names.map_in(db, m) for m in scheme.maps)))
    counts = scheme.counts()
    marks = LIST_SEP.join(RESULT_COUNT.format(tier=lang.t(S.TIER_LABEL[t]), n=counts[t]) for t in TIERS if counts[t])
    if marks:
        parts.append(marks)
    # 方案名是原文（存的照旧是原文），显示时按界面语言转一次；RESULT_OK 等常量导入时已转好
    line = RESULT_OK.format(hero=names.hero_in(db, scheme.hero), name=lang.t(scheme.name))
    if parts:
        line += RESULT_DETAIL.format(detail=RESULT_SEP.join(parts))
    return line


def result_lines(result: S.ParseResult, db) -> list[tuple[str, str]]:
    """识别结果的各行 [(色调, 文字)]：「识别到 N 套：」，逐套写能导入的（✓）和每个问题（⚠），最后写超出上限的部分。
    db 是资料库（推导目标流派用）。"""
    n = len(result.items)
    lines = [("dim", FOUND.format(n=n) if n else FOUND_NONE)]
    for item in result.items:
        if item.scheme is not None:
            lines.append(("ok", ok_line(item.scheme, db)))
        for p in item.problems:
            text = problem_text(p)
            if text:
                lines.append(("warn", PROBLEM_LINE.format(index=item.index, name=lang.t(item.name), text=text)))
    if result.text_cut:
        lines.append(("warn", TEXT_CUT))
    if result.skipped:
        lines.append(("warn", SKIPPED.format(n=result.skipped)))
    return lines


def shown_name(name: str) -> str:
    """作业站上的方案名称在提示行里的写法：超过 FETCH_NAME_SHOWN 个字时截短、末尾加省略号。"""
    return name if len(name) <= FETCH_NAME_SHOWN else name[:FETCH_NAME_SHOWN - 1] + ELLIPSIS


def fetch_done_text(share: hub_api.SchemeShare, added: bool) -> str:
    """取回一套以后提示行写的句子：added 为放进了粘贴框，否则是同一段文字已经在里面、没有再接。作业站上的方案名称同本机的
    方案名，显示时按界面语言转（放进粘贴框的分享码照原文）。"""
    name = shown_name(lang.t(share.name or ""))
    if added:
        return FETCH_DONE.format(name=name, n=share.id) if name else FETCH_DONE_UNNAMED.format(n=share.id)
    return FETCH_SAME.format(name=name, n=share.id) if name else FETCH_SAME_UNNAMED.format(n=share.id)


def fetch_error_text(error: BaseException) -> str:
    """取方案出错时提示行写的句子：HubError 按种类（FETCH_FAILED，太频繁时写要等多久），用户停下的（stopped）为空、不写；
    别的种类、别的异常写 FETCH_ERROR。"""
    kind = _kind(error)
    if kind == hub_api.STOPPED:
        return ""
    if kind == hub_api.RATE_LIMITED:
        wait = wait_text(error.retry_after)
        return FETCH_RATE_LIMITED.format(wait=wait) if wait else FETCH_RATE_LATER
    return FETCH_FAILED.get(kind, FETCH_ERROR)


def _kind(error) -> str | None:
    """HubError 的种类；别的异常、None 为 None。"""
    return error.kind if isinstance(error, hub_api.HubError) else None


def wait_text(seconds) -> str | None:
    """作业站要求等多久（429 的 retry_after，秒）的写法：FETCH_RATE_MINUTES_FROM 秒以上按分钟写（不足一分钟的算一分钟）；
    没给、不是正整数时为 None（句子写「稍后」）。"""
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
        return None
    if seconds < FETCH_RATE_MINUTES_FROM:
        return FETCH_WAIT_SECONDS.format(n=seconds)
    return FETCH_WAIT_MINUTES.format(n=math.ceil(seconds / 60))


def site_text(parts) -> str:
    """作业站给的说明（内容不对时各字段的说明、账号不能发的原因）合成一句：去掉空的，各段按界面语言转（网站写的是简体），用
    「；」连起来，超过 SITE_TEXT_SHOWN 个字时截短、末尾加省略号。只用来显示（按纯文本），不写进错误记录；字段名、错误码这类
    拿来比对的不经过这里。"""
    text = SITE_TEXT_SEP.join(lang.t(p.strip()) for p in parts if isinstance(p, str) and p.strip())
    return text if len(text) <= SITE_TEXT_SHOWN else text[:SITE_TEXT_SHOWN - 1] + ELLIPSIS


def post_error_text(error, updating: bool = False) -> str:
    """发方案（updating 为假）、更新方案（updating 为真）出错时提示行写的句子：内容不对（invalid）写作业站给的各字段的说明，
    账号不能发（forbidden）写作业站给的原因，登录失效（unauthorized）请重新登录，太频繁写要等多久，用户停下的（stopped）为空、
    不写；没有（not_found）时新发写 POST_UNAVAILABLE（还没有「那一套」），更新写那一套不在了；作业站上已有一样的一套
    （duplicate）不经过这里（另外问要不要更新）。别的种类、别的异常写 POST_ERROR。"""
    kind = _kind(error)
    if kind == hub_api.STOPPED:
        return ""
    if kind == hub_api.NOT_FOUND and not updating:
        return POST_UNAVAILABLE
    if kind == hub_api.RATE_LIMITED:
        wait = wait_text(error.retry_after)
        return POST_RATE_LIMITED.format(wait=wait) if wait else POST_RATE_LATER
    if kind == hub_api.INVALID:
        fields = error.fields if isinstance(error.fields, dict) else {}
        detail = site_text(fields.values())
        return POST_INVALID.format(detail=detail) if detail else POST_INVALID_BARE
    if kind == hub_api.FORBIDDEN:
        reason = site_text((error.reason,))
        return POST_FORBIDDEN.format(reason=reason) if reason else POST_FORBIDDEN_BARE
    if kind == hub_api.UNAUTHORIZED:
        return POST_UNAUTHORIZED
    return POST_FAILED.get(kind, POST_ERROR)


def post_done_text(published: hub_api.PublishedScheme, updated: bool) -> str:
    """发好了以后提示行写的句子：新发的按 POST_DONE、更新的按 UPDATE_DONE（写作业站上的编号），都按作业站回的状态写；不认识的
    状态按不公开（draft）写。"""
    table = UPDATE_DONE if updated else POST_DONE
    return table.get(published.status, table["draft"]).format(n=published.id)


def login_error_text(error) -> str:
    """登录没成功时对话框里写的句子：按种类（LOGIN_FAILED，太频繁时写要等多久）；用户点了取消（stopped）为空、不写；别的种类、
    别的异常写 LOGIN_ERROR。"""
    kind = _kind(error)
    if kind == hub_api.STOPPED:
        return ""
    if kind == hub_api.RATE_LIMITED:
        wait = wait_text(error.retry_after)
        return LOGIN_RATE_LIMITED.format(wait=wait) if wait else LOGIN_RATE_LATER
    return LOGIN_FAILED.get(kind, LOGIN_ERROR)


def nickname_text(nickname) -> str | None:
    """「已登录作业站」那一行的字（hub_api.login_nickname 的结果）：None（没登录）时为 None，这一行不显示；空串时不写昵称；
    昵称超过 NICK_SHOWN 个字时截短、末尾加省略号。昵称是网站上的人自己起的名字，照原文写，不按界面语言转（同作者名）。"""
    if nickname is None:
        return None
    name = nickname.strip() if isinstance(nickname, str) else ""
    if not name:
        return LOGGED_IN_UNNAMED
    if len(name) > NICK_SHOWN:
        name = name[:NICK_SHOWN - 1] + ELLIPSIS
    return LOGGED_IN.format(name=name)


def hub_url(url) -> bool:
    """url 是作业站的 https 地址（主机和 hub.BASE 相同）：程序只在浏览器里打开这样的地址。"""
    if not isinstance(url, str) or not url:
        return False
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (urllib.parse.urlsplit(hub.BASE).hostname or "").lower()
    return parts.scheme == "https" and parts.netloc.lower() == host


def _open_url(url: str) -> bool:
    """用系统默认的浏览器打开 url（QDesktopServices，不自己起浏览器）；返回系统接了没有。"""
    return bool(QDesktopServices.openUrl(QUrl(url)))


def error_detail(error: BaseException) -> str:
    """意外的异常记进错误记录时写的：异常的类型和出错的位置（调用栈），不写异常的说明（可能带着作业站回的内容）。"""
    stack = "".join(traceback.format_list(traceback.extract_tb(error.__traceback__)))
    return f"{type(error).__module__}.{type(error).__qualname__}\n{stack}"


@dataclass
class _Imported:
    """导入以后的一套（识别结果区里一行）：方案 id、所属角色、名称，点了「使用这套方案」以后的结果（空为还没点；now 悬浮窗
    已改用、later 已设为那个角色用的方案、gone 这套方案已不在）。"""

    scheme_id: str
    hero: str
    name: str
    result: str = ""
    # 界面上显示的角色名（繁体界面为繁体名）；hero 仍是程序用的简体名（换用方案时交给悬浮窗）
    shown_hero: str = ""

    def line(self) -> str:
        """那一行左边的字：「已导入筹码方案「X」（芬妮）」。name 记的是原文，显示时按界面语言转。"""
        return IMPORTED.format(name=lang.t(self.name), hero=self.shown_hero or self.hero)

    def outcome(self) -> tuple[str, str]:
        """点过「使用这套方案」以后按钮换成的那一句和它的样式名（样式表的 useNote 绿色小字、useNoteWarn 橙色小字）；还没点时
        为空。"""
        if self.result == "now":
            return USED_NOW, "useNote"
        if self.result == "later":
            return USED_LATER.format(hero=self.shown_hero or self.hero), "useNote"
        if self.result == "gone":
            return USE_GONE, "useNoteWarn"
        return "", ""


def _label(text: str = "", name: str = "", wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setTextInteractionFlags(Qt.NoTextInteraction)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


class _FitHeight:
    """（和 QLabel 或它的子类一起继承，写在前面）自动换行的字：高度自己按现在的宽度算——这句字折成几行就多高，写成首选
    高度和最小高度，对外不按 heightForWidth 排。

    自动换行的标签，Qt 算最小高度时只算一行；卡片里有这种标签时整张卡片也按 heightForWidth 排，几张卡片抢地方时按首选
    高度削（默认的排法导出、导入上下两块）。窗口矮的时候，同一列的粘贴框、识别结果、提示行就被挤得比它们的最小高度还矮，
    字被裁掉。高度自己算好以后，最小大小就是内容真正要的大小，窗口不够高时 Qt 自己把窗口加高。还没排过版（刚建好）时照
    QLabel 的算法；宽度变了重算；藏起来以后再露出来时先照 QLabel 的算（藏着的时候窗口可能改过宽度），排过版再按新的宽度
    算。"""

    _fit_width = -1

    def hasHeightForWidth(self) -> bool:  # noqa: N802  Qt 的虚函数
        return False

    def _fit(self, hint: QSize) -> QSize:
        if self._fit_width > 0 and self.wordWrap():
            hint.setHeight(self.heightForWidth(self._fit_width))
        return hint

    def sizeHint(self) -> QSize:  # noqa: N802
        return self._fit(super().sizeHint())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self._fit(super().minimumSizeHint())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if event.size().width() != self._fit_width:
            self._fit_width = event.size().width()
            self.updateGeometry()

    def setVisible(self, visible: bool) -> None:  # noqa: N802  hide()、show() 也经过这里
        if visible and self.isHidden():
            self._fit_width = -1
        super().setVisible(visible)


class _FitLabel(_FitHeight, QLabel):
    """自动换行的纯文本标签，高度见 _FitHeight。"""

    def __init__(self, text: str = "", name: str = ""):
        super().__init__(text)
        self.setTextFormat(Qt.PlainText)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.setWordWrap(True)
        if name:
            self.setObjectName(name)


class _FitWarn(_FitHeight, chrome.WarnLabel):
    """皮肤画的警告（chrome.WarnLabel），高度见 _FitHeight。"""


def _button(text: str, primary: bool = False) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.PointingHandCursor)
    if primary:
        btn.setProperty("accent", "primary")
    return btn


def _box() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("box")
    box = QVBoxLayout(frame)
    box.setContentsMargins(10, 8, 10, 10)
    box.setSpacing(6)
    return frame, box


class _TextBox(QPlainTextEdit):
    """纯文本框，首选高度和最矮高度按 heights =（首选, 最矮）定（QPlainTextEdit 默认的首选高度约 190，太高）。"""

    def __init__(self, heights: tuple[int, int], read_only: bool = False):
        super().__init__()
        self._heights = heights
        self.setReadOnly(read_only)

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), self._heights[0])

    def minimumSizeHint(self) -> QSize:
        return QSize(super().minimumSizeHint().width(), self._heights[1])


class _FitArea(QScrollArea):
    """游戏内风格排法的识别结果区：宽度变了（窗口改大小、外面的滚动区出现或去掉滚动条）时调用 on_width，按新的宽度重新
    定它的高（SchemeShareWindow._fit_results）：不然多出来的几行只能在区里滚动着看。prefer_content 为 True 时首选高度就是
    定好的最高（内容的高度）：两张卡片放在外面的滚动区里按首选高度排时（stacked），结果区整个排开，不缩成最矮的一行。"""

    prefer_content = False

    def __init__(self, on_width):
        super().__init__()
        self._on_width = on_width
        self._width = -1

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        if self.prefer_content and self.maximumHeight() < 10000:
            hint.setHeight(max(self.minimumHeight(), self.maximumHeight()))
        return hint

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if event.size().width() != self._width:
            self._width = event.size().width()
            self._on_width()


class _ColumnScroll(QScrollArea):
    """左右并排的排法（side_by_side、split）里导出这一块外面的透明滚动区：发到作业站以后的提示行和它下面的小按钮（skip 里的
    部件，用到时才显示）不撑高窗口——这一列放不下时只有这一列上下滚动。

    最小高度照卡片不算那几样时的最小高度（窗口的最小大小和以前一样，平时不出滚动条）；最小宽度是卡片的最小宽度加滚动条，
    reserve 给了时不比它窄（提示行下面那一排小按钮整排放得下，按钮露出来时窗口不跟着变宽）。卡片的排版变了（提示行露出来、
    收起来）时跟着重算。"""

    def __init__(self, card: QWidget, skip: tuple[QWidget, ...], spacing: int, reserve: Callable[[], int]):
        super().__init__()
        self._skip, self._spacing, self._reserve = skip, spacing, reserve
        self.setObjectName("exportScroll")
        self.setStyleSheet("QScrollArea#exportScroll { background: transparent; border: none; }")
        self.setFocusPolicy(Qt.NoFocus)  # 透明的滚动区不进 Tab 顺序
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(card)
        self.viewport().setAutoFillBackground(False)
        card.setAutoFillBackground(False)  # setWidget 会把它打开：卡片的底由卡片自己画（split 不画底），不用调色板的底色铺满
        card.installEventFilter(self)

    def minimumSizeHint(self) -> QSize:  # noqa: N802  Qt 的虚函数
        card = self.widget()
        hint = card.minimumSizeHint()
        height = hint.height()
        for part in self._skip:
            if part.isVisibleTo(card):
                height -= part.minimumSizeHint().height() + self._spacing
        width = max(hint.width(), self._reserve()) + self.verticalScrollBar().sizeHint().width()
        return QSize(width, height)

    def sizeHint(self) -> QSize:  # noqa: N802
        hint = self.widget().sizeHint()
        return QSize(max(hint.width(), self.minimumSizeHint().width()), hint.height())

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.widget() and event.type() == QEvent.Type.LayoutRequest:
            self.updateGeometry()  # 卡片要的大小变了：外面的布局重新问这一列要多高多宽
        return super().eventFilter(obj, event)


class _FlowList(FlowLayout):
    """勾选方案排成一排可点的胶囊（capsules 排法），放不下时换行。和竖排的方案列表同样用 insertWidget 插在最后一项（一个
    不显示、不占地方的空白）前面，_fill_list 不用分排法。"""

    def __init__(self, parent=None):
        super().__init__(parent, 8, 8)
        self.addItem(QSpacerItem(0, 0))

    def insertWidget(self, index: int, widget: QWidget) -> None:  # noqa: N802  同 QBoxLayout 的方法名
        self.addChildWidget(widget)
        self._items.insert(max(0, index), QWidgetItem(widget))
        self.invalidate()


class _PillTag(QWidget):
    """卡片左上角写「导出」「导入」的渐变小胶囊（capsules 排法；颜色是几种渐变色，字白色、标题字体）。"""

    PX = 13

    def __init__(self, text: str, colors, parent=None):
        super().__init__(parent)
        self._text, self._colors = text, colors
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedSize(self.sizeHint())

    def text(self) -> str:
        return self._text

    def set_colors(self, colors) -> None:
        """换皮肤（排法不变、窗口没重建）时按新皮肤的渐变色重画。"""
        self._colors = colors
        self.update()

    def sizeHint(self) -> QSize:
        from PySide6.QtGui import QFontMetricsF

        return QSize(round(QFontMetricsF(theme.title_font(self.PX)).horizontalAdvance(self._text) + 26), 24)

    def paintEvent(self, event) -> None:
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter

        from . import decor

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.fillPath(decor.capsule(rect), decor.fill_brush(self._colors, rect))
        decor.outlined_text(p, rect, self._text, theme.title_font(self.PX), "#ffffff")
        p.end()


class _RefEdit(QLineEdit):
    """「按编号导入」的输入框。最窄时占位文字也整个看得见（QLineEdit 默认的最小宽度只够几个字）；高度不按自己的字算，跟着
    同一行的按钮（竖向 Ignored：那一行有多高就占多高，上下留白由 _fit_fetch_row 按按钮画出来的那一块定）。"""

    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)

    def _placeholder_width(self) -> int:
        fm = self.fontMetrics()
        frame = super().sizeHint().width() - 17 * fm.horizontalAdvance("x")  # 框线、内边距（Qt 按 17 个 x 加上它们算首选宽度）
        return fm.horizontalAdvance(self.placeholderText()) + max(frame, 0) + 2

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(max(hint.width(), self._placeholder_width()), hint.height())

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(max(hint.width(), self._placeholder_width()), hint.height())


class _Note(QWidget):
    """导入这一块的一行提示（按编号导入的结果；以后登录、发方案也照这一种写）：正在做（busy）、做完了（ok）是正文大小的一行字
    （样式表的 QLabel#hubNote，按 tone 属性分暗色、绿色：一句话较长，不用「已复制」那种标题字体，窄的排法里也不占几行）；
    出错（warn）换成排法里的警告（warn 给的部件，和导出这一块的警告同一个样子）。文字一律按纯文本显示、自动换行；空着时整行
    藏起来、不占位。

    自动换行的字，Qt 算最小高度时只算一行，窗口矮的时候会把同一列的粘贴框、识别结果挤得比它们的最小高度还矮。所以这一行的
    高度自己算：按同一列里一直排着的 width_of（按编号导入那一行）的宽度，算这句字要几行，写成首选高度和最小高度，对外不按
    heightForWidth 排。窗口因此不够高时 Qt 自己把窗口加高，宽度变了就重算。"""

    def __init__(self, warn: QWidget, width_of: QWidget):
        super().__init__()
        self.tone = ""  # 现在显示的种类：busy、ok、warn；空着时为空字符串
        self._width_of = width_of
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)  # 最小高度由 minimumSizeHint 给，布局不另设
        self.info = _label("", "hubNote", wrap=True)
        self.info.setProperty("tone", "busy")
        self.info.hide()
        box.addWidget(self.info)
        self.warn = warn
        self.warn.hide()
        box.addWidget(self.warn)
        self.hide()

    def show_text(self, tone: str, text: str) -> None:
        """显示 text（tone 为 busy、ok 或 warn）；text 为空时整行藏起来。"""
        self.tone = tone if text else ""
        warn = self.tone == "warn"
        if not warn:
            look = "ok" if self.tone == "ok" else "busy"
            if self.info.property("tone") != look:
                self.info.setProperty("tone", look)
                self.info.style().unpolish(self.info)
                self.info.style().polish(self.info)
        self.info.setText("" if warn else text)
        self.warn.setText(text if warn else "")
        self.info.setVisible(bool(text) and not warn)
        self.warn.setVisible(warn)
        self.setVisible(bool(text))
        self.updateGeometry()

    def hasHeightForWidth(self) -> bool:  # noqa: N802  Qt 的虚函数
        return False

    def _height(self) -> int:
        shown = (self.warn if self.tone == "warn" else self.info) if self.tone else None
        if shown is None:
            return 0
        width = self._width_of.width() if self._width_of.isVisible() else self.width()
        height = shown.heightForWidth(width)  # 标签自己算高度时（_FitHeight）也照 QLabel 按这个宽度折行
        return height if height >= 0 else shown.sizeHint().height()

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), self._height())

    def minimumSizeHint(self) -> QSize:
        return QSize(super().minimumSizeHint().width(), self._height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.updateGeometry()  # 宽度变了，字要的行数跟着变

    def text(self) -> str:
        """现在显示的那一句（藏着时为空）。"""
        return (self.warn.text() if self.tone == "warn" else self.info.text()) if self.tone else ""

    def state(self) -> tuple[str, str] | None:
        """（种类, 文字）；藏着时为 None。"""
        return (self.tone, self.text()) if self.tone else None


# ------------------------------------------------------------ 作业站：在后台线程里调用


def _start_thread(fn: Callable[[], None]) -> threading.Thread:
    """在一个守护线程里跑 fn（程序退出时不等它：联网最多等 hub_api.DEFAULT_TIMEOUT 秒）。"""
    thread = threading.Thread(target=fn, name="hub-call", daemon=True)
    thread.start()
    return thread


class _Relay(QObject):
    """把后台线程的结果排队交回界面线程：在界面线程里建、一直留着（不随哪个窗口删掉），信号接在它自己的方法上，后台线程
    发出时排队回到界面线程（同 overlay_parts/updates.py 的写法）。"""

    done = Signal(object)  # 跑完的 HubCall（结果、错误存在它上面）

    def __init__(self):
        super().__init__()
        self.done.connect(self._deliver)

    def _deliver(self, call) -> None:
        call.deliver()


_RELAY: _Relay | None = None


def _relay() -> _Relay:
    """交回结果用的 _Relay（第一次用时在界面线程里建）。"""
    global _RELAY
    if _RELAY is None or not isValid(_RELAY):
        _RELAY = _Relay()
    return _RELAY


class HubCall:
    """在后台线程里调一次作业站（advisor/hub_api.py 的函数会联网、要等），结果交回界面线程。按编号导入用它；以后登录、发方案
    也照它调。

    work(stop) 在后台线程里跑：stop 是 threading.Event，原样交给 hub_api 的 stop 参数；work 只联网、取数据，不碰界面和方案
    文件。跑完以后在界面线程里调 on_done(结果, 错误)：成功时错误为 None；出错时结果为 None，错误是 HubError，或者别的异常
    （不该出现；已经记进错误记录，标题 log_title，只记类型和调用栈）。

    cancel() 置停止事件（关窗口时），之后晚到的结果直接丢掉、不调 on_done；drop() 不叫停、只丢掉结果（联网的那一步照常做完，
    例如退出时让作业站撤销登录信息）；owner（发起调用的窗口）底层已经删掉时同样丢掉：晚到的结果不碰已经关掉的窗口和部件。
    本对象不是 Qt 对象，交回结果靠一直留着的 _Relay，窗口删掉以后后台线程照样能交回（交回以后丢掉）。只在界面线程里建、
    调 start、cancel 和 drop。"""

    def __init__(self, owner: QWidget, work: Callable[[threading.Event], object],
                 on_done: Callable[[object, BaseException | None], None], log_title: str):
        self.owner = owner
        self.stop = threading.Event()
        self._work, self._on_done, self._log_title = work, on_done, log_title
        self._relay = _relay()
        self._dropped = False
        self.result: object = None
        self.error: BaseException | None = None

    def start(self, run: Callable[[Callable[[], None]], object] = _start_thread) -> None:
        """开始：run(函数) 把函数放到后台线程里跑（测试换成当场调用，或者先攒着、过后再调）。"""
        run(self._run)

    def cancel(self) -> None:
        """叫停：联网的那一步看到停止事件就停（hub_api 抛 stopped），晚到的结果丢掉。"""
        self.stop.set()

    def drop(self) -> None:
        """不叫停，只丢掉结果：联网的那一步照常做完，做完以后不调 on_done。"""
        self._dropped = True

    def _run(self) -> None:
        """（后台线程）跑 work，结果或错误存在本对象上，再经 _Relay 把本对象交回界面线程。信号只带本对象：错误是异常对象，
        界面只按它的 kind 写固定的中文（fetch_error_text 这类），不显示它的原文。"""
        try:
            self.result = self._work(self.stop)
        except hub_api.HubError as e:
            self.error = e
        except Exception as e:  # noqa: BLE001  hub_api 出错一律抛 HubError；别的是程序的毛病，记下来，界面照样收尾
            log_error(self._log_title, error_detail(e))
            self.error = e
        try:
            self._relay.done.emit(self)
        except RuntimeError:  # 程序退出时 _Relay 已经删掉：不再交回
            pass

    def deliver(self) -> None:
        """（界面线程）后台线程跑完以后：叫停过、丢掉过（drop）、窗口已经删掉时丢掉，否则调 on_done(结果, 错误)。交回以后放掉
        结果和错误（错误带着后台线程的调用栈，留着会连带留住本对象和窗口）。"""
        result, error = self.result, self.error
        self.result = self.error = None
        if self._dropped or self.stop.is_set() or not isValid(self.owner):
            return
        self._on_done(result, error)


def _body_insets(button: QWidget) -> tuple[int, int]:
    """按钮画出来的那一块离按钮上沿、下沿各多少像素：同一行的输入框照它上下留白，和按钮画出来的那一块一样高、上下对齐。
    胶囊按钮（chrome.PillButton）和圆角块、平行四边形的 GameButton 下沿留 4 像素的厚度或投影；胶囊形的 GameButton 下沿留
    投影的错开量（这种按钮没有投影色时不留）；切角的 GameButton 四周各缩 3 像素，输入框也装上切角的框（同样缩 3 像素，
    _build_split），不另留；样式表画的按钮（QPushButton）整块都画。"""
    if isinstance(button, chrome.PillButton):
        return 0, 4
    if isinstance(button, chrome.GameButton):
        look = theme.CHROME
        if look.BUTTON_SHAPE == "cut":
            return 0, 0
        if look.BUTTON_SHAPE == "pill":
            return 0, round(look.BUTTON_SHADOW[1]) if decor.button_style(look, button.kind)[1] else 0
        return 0, 4
    return 0, 0


# ------------------------------------------------------------ 搭界面用的部件样子


def _frame_box() -> QFrame:
    """默认排法的卡片（QFrame#box，和 _box 同一种）。造的卡片都有 box 属性（卡片里的竖排布局），和 chrome.Card 一样。"""
    frame, box = _box()
    frame.box = box
    return frame


def _no_shrink(card: QWidget) -> None:
    """卡片放在透明滚动区里的排法（stacked、banded、capsules）：卡片竖向不比首选高度矮（策略 MinimumExpanding）。窗口矮的
    时候卡片照首选高度排、在滚动区里上下滚动：识别结果整个排开，粘贴框不被挤矮（两张卡片都按 1 分多出来的地方时，Qt 只照
    最小高度分）。窗口比内容高时多出来的高度照旧两张卡片分。"""
    policy = card.sizePolicy()
    policy.setVerticalPolicy(QSizePolicy.MinimumExpanding)
    card.setSizePolicy(policy)


def _head_label(text: str) -> QWidget:
    return _FitLabel(text, "groupHead")


def _dim_label(text: str) -> QWidget:
    return _label(text, "dim")


def _field_label(text: str) -> QWidget:
    return _label(text, "fieldTitle")


def _plain_warn(text: str = "") -> QWidget:
    return _FitLabel(text, "warnText")


def _skin_warn(text: str = "") -> QWidget:
    return _FitWarn(text, name="warnText")


@dataclass(frozen=True)
class _Kit:
    """导入/导出窗口一种排法搭界面时用的部件样子（同一族的排法共用一套）。default 用 PLAIN，side_tabs 用 PILL，
    经典主题-黑 / 反色 / 碳纤流光（side_by_side、stacked、split）用 GAME，霓虹贴纸 / 白色海景房（banded、capsules）用 RGB。
    建界面只经 _make_export / _make_import 照这个造部件和接线，摆放留在各个 _build_* 里。"""

    export_card: Callable[[], QWidget]
    import_card: Callable[[], QWidget]
    format_card: Callable[[], QWidget] | None  # 「格式」单独一张卡片的排法（side_tabs）
    box_spacing: int | None  # 卡片里内容的间距（None 照卡片自己的）
    custom_head: bool  # 分组说明另做（霓虹贴纸的斜带、白色海景房的胶囊），不在这里写一行小字
    strip_head: bool  # 分组说明去掉「导出：」「导入：」开头
    head: Callable[[str], QWidget]
    field: Callable[[str], QWidget]  # 小标题（角色、格式）
    dim: Callable[[str], QWidget]
    warn: Callable[[str], QWidget]
    copy_button: Callable[[], QWidget]
    import_button: Callable[[], QWidget]
    clear_button: Callable[[], QWidget]
    use_button: Callable[[], QWidget]  # 导入以后每一套那一行右边的「使用这套方案」（比导入、清空矮一号）
    # 提示行下面的小按钮（参数是按钮上的字：「在作业站上看」「更新那一套」「不更新」「退出作业站」）：同「使用这套方案」
    action_button: Callable[[str], QWidget]
    hub_button: Callable[[str], QWidget]  # 作业站的两个按钮（参数是按钮上的字）：和导入、清空同一种次要样式
    hub_own_row: bool  # 作业站的按钮放在那一行的下面、单独一行靠右（经典主题-黑、碳纤流光左右并排、每块较窄，放在同一行
    #                   会把窗口撑宽）；反色上下排，放同一行（单独一行会把按钮挤出可见区）
    hero_spacing: int
    hero_gap: int
    copy_spacing: int
    buttons_spacing: int
    format_own_card: bool  # 格式、预览、复制单独放在 format_card 里
    format_indent: bool  # 格式的单选按钮缩进成一个子布局，标题单独放（经典主题-黑等）
    format_segments: bool  # 格式的标题和单选按钮放在一行里（霓虹贴纸 / 白色海景房）
    format_line_spacing: int
    format_spacing: int
    select_name: str
    paste_name: str
    preview_name: str
    list_min_h: int
    list_margins: tuple
    list_spacing: int
    preview_h: tuple
    game_preview: bool  # 预览框按排法定高矮：左右并排用高的（PREVIEW_H），上下排用矮的（GAME_PREVIEW_H，让出地方给方案列表）
    results_stretch: int
    trailing_stretch: bool  # 导入卡片最下面留出空白（按钮紧跟识别结果）
    # 登录对话框：内容放在一张卡片里（None 为直接放在对话框的底上：默认的排法字就是写在深色底上的）。窗口里的字是照卡片的
    # 底色配的，直接写在皮肤的外框（渐变、花纹）上看不清
    dialog_card: Callable[[], QWidget] | None = None
    # 登录对话框下面一排的按钮：次要样式，大小同这种排法窗口里大的那一号（比提示行下面的小按钮大一号）
    dialog_button: Callable[[str], QWidget] = _button


PLAIN_KIT = _Kit(export_card=_frame_box, import_card=_frame_box, format_card=None, box_spacing=None,
                 custom_head=False, strip_head=False,
                 head=_head_label, field=_dim_label, dim=_dim_label, warn=_plain_warn,
                 copy_button=lambda: _button(COPY, primary=True),
                 import_button=lambda: _button(IMPORT.format(n=0), primary=True),
                 clear_button=lambda: _button(CLEAR),
                 use_button=lambda: _button(USE), action_button=lambda text: _button(text),
                 hub_button=lambda text: _button(text), hub_own_row=False,
                 hero_spacing=6, hero_gap=6, copy_spacing=8, buttons_spacing=8,
                 format_own_card=False, format_indent=False, format_segments=False, format_line_spacing=0, format_spacing=10,
                 select_name="", paste_name="", preview_name="",
                 list_min_h=LIST_MIN_H, list_margins=(8, 6, 8, 6), list_spacing=4,
                 preview_h=PREVIEW_H, game_preview=False, results_stretch=1, trailing_stretch=False)
PILL_KIT = _Kit(export_card=lambda: chrome.Card(TAB_EXPORT, seed=51),
                import_card=lambda: chrome.Card(TAB_IMPORT, seed=53),
                format_card=lambda: chrome.Card(FORMAT_LABEL, seed=52), box_spacing=None,
                custom_head=False, strip_head=True,
                head=_head_label, field=_field_label, dim=_dim_label, warn=_skin_warn,
                copy_button=lambda: chrome.PillButton(COPY, height=46, px=16, seed=6),
                import_button=lambda: chrome.PillButton(IMPORT.format(n=0), height=44, px=15, seed=7),
                clear_button=lambda: chrome.PillButton(CLEAR, kind="light", height=44, px=15),
                use_button=lambda: chrome.PillButton(USE, kind="light", height=34, px=13),
                action_button=lambda text: chrome.PillButton(text, kind="light", height=34, px=13),
                hub_button=lambda text: chrome.PillButton(text, kind="light", height=44, px=15), hub_own_row=False,
                hero_spacing=10, hero_gap=14, copy_spacing=14, buttons_spacing=12,
                format_own_card=True, format_indent=False, format_segments=False, format_line_spacing=0, format_spacing=18,
                select_name="selectAll", paste_name="paste", preview_name="preview",
                list_min_h=LIST_MIN_H, list_margins=(0, 2, 6, 2), list_spacing=6,
                preview_h=PREVIEW_H, game_preview=False, results_stretch=100, trailing_stretch=True,
                dialog_card=lambda: chrome.Card(seed=55, margins=(22, 16, 22, 16)),
                dialog_button=lambda text: chrome.PillButton(text))
GAME_KIT = _Kit(export_card=lambda: chrome.Card(TAB_EXPORT, seed=51, margins=(16, 12, 16, 14)),
                import_card=lambda: chrome.Card(TAB_IMPORT, seed=53, margins=(16, 12, 16, 14)),
                format_card=None, box_spacing=10, custom_head=False, strip_head=True,
                head=_head_label, field=_field_label, dim=_dim_label, warn=_skin_warn,
                copy_button=lambda: chrome.GameButton(COPY, "action", height=42, px=15, icon=ICON_COPY),
                import_button=lambda: chrome.GameButton(IMPORT.format(n=0), "primary", height=42, px=15, icon=ICON_ADD),
                clear_button=lambda: chrome.GameButton(CLEAR, "plain", height=42, px=15),
                use_button=lambda: chrome.GameButton(USE, "secondary", height=32, px=13),
                action_button=lambda text: chrome.GameButton(text, "secondary", height=32, px=13),
                hub_button=lambda text: chrome.GameButton(text, "plain", height=42, px=15, icon=ICON_HUB),
                hub_own_row=True,
                hero_spacing=10, hero_gap=14, copy_spacing=14, buttons_spacing=12,
                format_own_card=False, format_indent=True, format_segments=False, format_line_spacing=14, format_spacing=18,
                select_name="selectAll", paste_name="paste", preview_name="preview",
                list_min_h=GAME_LIST_MIN_H, list_margins=(0, 2, 6, 2), list_spacing=6,
                preview_h=PREVIEW_H, game_preview=True, results_stretch=100, trailing_stretch=True,
                dialog_card=lambda: chrome.Card(seed=55, margins=(16, 12, 16, 14)),
                dialog_button=lambda text: chrome.GameButton(text, "secondary", height=40, px=15))
RGB_KIT = _Kit(export_card=lambda: chrome.Card(seed=51, margins=(14, 12, 14, 12)),
               import_card=lambda: chrome.Card(seed=53, margins=(14, 12, 14, 12)),
               format_card=None, box_spacing=10, custom_head=True, strip_head=True,
               head=_head_label, field=_field_label, dim=_dim_label, warn=_skin_warn,
               copy_button=lambda: chrome.GameButton(COPY, "primary", height=32, px=13, icon=ICON_COPY),
               import_button=lambda: chrome.GameButton(IMPORT.format(n=0), "primary", height=32, px=13, icon=ICON_ADD),
               clear_button=lambda: chrome.GameButton(CLEAR, "plain", height=32, px=13),
               use_button=lambda: chrome.GameButton(USE, "secondary", height=28, px=12),
               action_button=lambda text: chrome.GameButton(text, "secondary", height=28, px=12),
               hub_button=lambda text: chrome.GameButton(text, "plain", height=32, px=13, icon=ICON_HUB), hub_own_row=False,
               hero_spacing=10, hero_gap=12, copy_spacing=12, buttons_spacing=10,
               format_own_card=False, format_indent=False, format_segments=True, format_line_spacing=12, format_spacing=18,
               select_name="selectAll", paste_name="paste", preview_name="preview",
               list_min_h=LIST_MIN_H, list_margins=(10, 6, 10, 6), list_spacing=4,
               preview_h=GAME_PREVIEW_H, game_preview=False, results_stretch=100, trailing_stretch=True,
               dialog_card=lambda: chrome.Card(seed=55, margins=(14, 12, 14, 12)),
               dialog_button=lambda text: chrome.GameButton(text, "secondary", height=32, px=13))


class HubLogin(chrome.HideNotice, QDialog):
    """登录作业站的对话框（电视式短码），由导入/导出窗口（owner）开。

    start：在后台线程里向作业站要短码（hub_api.request_device_code）；拿到以后大字显示短码，自动在浏览器里打开授权页面
    （owner.open_url，只开作业站的 https 地址），另有「再打开一次网页」；然后在后台线程里等授权（hub_api.poll_device_token，
    停止事件交给它）。授权了发 logged_in(hub_api.Login) 并关掉；拒绝、过期、连不上、作业站出错时写原因，按钮换成「重新登录」
    「关闭」。「取消」、×、Esc 叫停正在做的那一步，关掉，发 ended（没登录成功），不写提示；关导入/导出窗口时由它调 cancel，
    对话框跟着关。后台线程只联网，结果经 HubCall 交回界面线程；对话框关掉、删掉以后晚到的结果丢掉。

    样子跟着皮肤：自己画外框的皮肤用 chrome.Frame 当底、chrome.WindowHead 写标题和 ×（同导入/导出窗口的 _game_frame），别的
    皮肤用系统标题栏；样式表用导入/导出窗口的（theme.share_style）；内容放在一张卡片里（kit.dialog_card：窗口里的字照卡片的
    底色配，直接写在皮肤的外框上看不清；默认的排法没有卡片），下面一排按钮比窗口里提示行下面的小按钮大一号（kit.dialog_button）。
    换一步时高度跟着内容（_fit_height）。登录信息不显示、不写进错误记录，交出去以后不留着。"""

    logged_in = Signal(object)  # 拿到的 hub_api.Login
    ended = Signal()  # 关了，没登录成功
    FLAGS = Qt.Dialog | Qt.WindowCloseButtonHint
    WIDTH = 460  # 打开时的宽（逻辑像素）

    def __init__(self, owner: QWidget, kit: _Kit):
        super().__init__(owner)
        self._owner = owner
        self._call: HubCall | None = None
        self._device: hub_api.DeviceCode | None = None
        self._over = False  # 已经交出登录信息，或者已经发过 ended
        self.state_name = ""  # asking 要短码、waiting 等授权、failed 没登录成功
        self.setObjectName("shareWindow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowTitle(LOGIN_TITLE)
        self.setWindowFlags(self.FLAGS)
        self.setWindowModality(Qt.WindowModal)
        self.setStyleSheet(theme.share_style())
        self.back = None
        self.card: QWidget | None = None  # 放内容的卡片（kit.dialog_card 为 None 时没有）
        self._build(kit)
        chrome.frameless(self, self.back is not None, self.FLAGS)
        self.resize(self.WIDTH, self.sizeHint().height())
        # 换一步以后等排版落定再让高度跟着内容（_fit_height；计时器挂在对话框下，对话框删掉时一起没了）
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(self._fit_height)

    def _build(self, kit: _Kit) -> None:
        outer = QVBoxLayout(self)
        if chrome.custom():
            outer.setContentsMargins(0, 0, 0, 0)
            frame = chrome.Frame()
            outer.addWidget(frame)
            root = QVBoxLayout(frame)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(0)
            head = chrome.WindowHead(LOGIN_TITLE)
            self.back = head.closer
            root.addWidget(head)
            body = QVBoxLayout()
            root.addLayout(body, 1)
            margins = (24, 10, 24, 20) if kit.dialog_card is None else (20, 10, 20, 18)
        else:
            body = outer
            margins = (18, 14, 18, 16) if kit.dialog_card is None else (14, 12, 14, 14)
        body.setContentsMargins(*margins)
        if kit.dialog_card is not None:  # 内容放在一张卡片里，同窗口里导出这一块的底（碳纤流光那种不画底的照样不画）
            card = self.card = kit.dialog_card()
            card.plain = bool(getattr(getattr(self._owner, "export_box", None), "plain", False))
            body.addWidget(card, 1)
            body = card.box
        body.setSpacing(10)
        self.status = _FitLabel("", "groupHead")
        body.addWidget(self.status)
        self.code = QLabel("")
        self.code.setObjectName("hubCode")
        self.code.setTextFormat(Qt.PlainText)
        self.code.setAlignment(Qt.AlignCenter)
        self.code.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.code.hide()
        body.addWidget(self.code)
        self.waiting = _FitLabel(LOGIN_WAITING, "dim")
        self.waiting.hide()
        body.addWidget(self.waiting)
        self.error = kit.warn("")
        self.error.hide()
        body.addWidget(self.error)
        body.addWidget(_FitLabel(LOGIN_PRIVACY, "caption"))
        body.addStretch(1)
        row = QHBoxLayout()
        row.setSpacing(kit.buttons_spacing)
        self.reopen_btn = kit.dialog_button(LOGIN_REOPEN)
        self.retry_btn = kit.dialog_button(LOGIN_RETRY)
        self.cancel_btn = kit.dialog_button(LOGIN_CANCEL)
        self.close_btn = kit.dialog_button(CLOSE)
        for btn, slot in ((self.reopen_btn, self.reopen), (self.retry_btn, self.start), (self.cancel_btn, self.cancel),
                          (self.close_btn, self.cancel)):
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(slot)
            btn.hide()
        row.addWidget(self.reopen_btn)
        row.addStretch(1)
        for btn in (self.retry_btn, self.cancel_btn, self.close_btn):
            row.addWidget(btn)
        body.addLayout(row)

    def start(self) -> None:
        """要短码（打开时、点「重新登录」时）。正在做时什么都不做。"""
        if self._call is not None or self._over:
            return
        self._device = None
        self._show("asking")
        self._run(lambda stop: hub_api.request_device_code(stop=stop), self._got_code)

    def _run(self, work, on_done) -> None:
        call = self._call = HubCall(self, work, on_done, LOGIN_LOG)
        call.start(self._owner.run_background)

    def _got_code(self, device, error) -> None:
        """（界面线程）要到了短码：显示短码、打开授权页面，接着在后台线程里等授权；没要到时写原因。"""
        self._call = None
        if error is not None or not isinstance(device, hub_api.DeviceCode):
            self._fail(error)
            return
        self._device = device
        self._show("waiting", opened=self._open(device.verify_url_complete))
        self._run(lambda stop: hub_api.poll_device_token(device, stop=stop), self._got_login)

    def _got_login(self, login, error) -> None:
        """（界面线程）等授权的结果：授权了交出登录信息、关掉；没授权写原因。"""
        self._call = None
        self._device = None
        if error is not None or not isinstance(login, hub_api.Login):
            self._fail(error)
            return
        self._over = True
        self.logged_in.emit(login)
        self.accept()

    def _fail(self, error) -> None:
        text = login_error_text(error) if error is not None else LOGIN_ERROR
        if text:  # 用户点了取消（stopped）：对话框正在关，不写
            self._show("failed", error_text=text)

    def _show(self, state: str, opened: bool = True, error_text: str = "") -> None:
        """按这一步显示：asking 要短码，waiting 显示短码和怎么做（浏览器没打开时写网址），failed 写没登录成功的原因。"""
        self.state_name = state
        device = self._device if state == "waiting" else None
        if state == "asking":
            self.status.setText(LOGIN_ASKING)
        elif device is not None:
            self.status.setText(LOGIN_STEPS if opened else LOGIN_NO_BROWSER.format(url=device.verify_url))
        self.status.setVisible(state != "failed")
        self.code.setText(device.user_code if device is not None else "")
        self.code.setVisible(device is not None)
        self.waiting.setVisible(device is not None)
        self.error.setText(error_text)
        self.error.setVisible(bool(error_text))
        self.reopen_btn.setVisible(device is not None)
        self.retry_btn.setVisible(state == "failed")
        self.cancel_btn.setVisible(state != "failed")
        self.close_btn.setVisible(state == "failed")
        self._fit_timer.start()

    def _fit_height(self) -> None:
        """换了一步（显示的几样变了）以后，高度跟着内容、宽度不变：没登录成功时短码那几行收起，下面不留一大块空白；字多的
        一步照样整段显示（窗口的最小高度由布局管着）。"""
        for _ in range(3):  # 刚露出来的字先按现在的宽度折好行，首选高度才是新的
            QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        self.resize(self.width(), self.sizeHint().height())

    def reopen(self) -> None:
        """「再打开一次网页」：再在浏览器里打开授权页面。"""
        if self._device is not None:
            self._open(self._device.verify_url_complete)

    def _open(self, url: str) -> bool:
        """在浏览器里打开作业站的授权页面（只开作业站的 https 地址）；返回打开了没有。"""
        return hub_url(url) and bool(self._owner.open_url(url))

    def cancel(self) -> None:
        """「取消」「关闭」，关导入/导出窗口时：关掉（done）。"""
        self.reject()

    def done(self, result: int) -> None:  # noqa: N802  Qt 的虚函数
        """关掉：×、Esc、「取消」「关闭」、授权了以后、关导入/导出窗口时都经过这里（Qt 的对话框按 Esc、reject 时不发
        closeEvent）。叫停正在做的那一步（晚到的结果丢掉）；没登录成功时发 ended。"""
        call, self._call = self._call, None
        if call is not None:
            call.cancel()
        self._device = None
        super().done(result)
        if not self._over:
            self._over = True
            self.ended.emit()


class SchemeShareWindow(SkinnedWindow, chrome.EdgeResize, chrome.HideNotice, QWidget):
    """「导入/导出筹码方案」窗口。db 为资料库（角色的排列顺序），store 为悬浮窗共用的 SchemeStore，hero 为默认的
    角色筛选（None 为全部角色）。能改大小：用系统标题栏时拖系统的边框，自己画外框时拖窗口边缘（chrome.EdgeResize）；
    最小为内容需要的大小。"""

    schemes_imported = Signal(list)  # 导入了方案：这些方案所属的角色名（不重复，按先后）
    format_changed = Signal(str)  # 选了另一种格式（"short" / "text"）
    use_requested = Signal(str, str)  # 导入以后点了一套的「使用这套方案」：（所属角色, 方案 id）；结果经 show_use_result 交回
    hub_requested = Signal(str)  # 打开作业站：plans 当前角色的方案列表（「去作业站找方案」；角色、地图由悬浮窗定）
    FLAGS = Qt.Dialog | Qt.WindowCloseButtonHint  # 用系统标题栏时的窗口标志；不总在最前，窗口层次跟着悬浮窗（follow.WindowStack.layer_windows）
    LAYOUT_FIELD = "SHARE"
    LAYOUTS = LAYOUTS  # 排法名 → 建法（模块级的表：split、test-same 这类后加进来的也认）

    last_format = S.FORMAT_SHORT  # 上次选的格式：本次运行里各窗口共用，新开的窗口照它

    def __init__(self, db, store, hero: str | None = None, parent=None):
        super().__init__(parent)
        self.db = db
        self.store = store
        self.catalog = store.catalog
        self._filter: str | None = None  # 角色筛选（None 为全部角色）
        self._checked: set[str] = set()  # 勾选的方案 id（换筛选时记住，导出的只是当前列出来的）
        self._visible: list[S.Scheme] = []
        self._boxes: dict[str, QCheckBox] = {}
        self._list_key: tuple | None = None
        self._hero_items: list[str | None] = []
        self._export_text = ""
        self._export_count = 0
        self._export_failed = 0  # 生成不了短字符串的套数（EXPORT_FAILED）
        self._copy_failed = False  # 上一次复制没写进剪贴板（COPY_FAILED；预览的内容变了以后收起，同「已复制」）
        self._result = S.ParseResult()
        self._done: list[_Imported] = []  # 刚导入的几套（「已导入筹码方案……」和「使用这套方案」），粘贴新内容后不再显示
        self._done_widgets: list[tuple[QPushButton, QLabel]] = []  # 那几行右边的按钮和换成的结果（和 _done 一一对应）
        self._use_button: Callable[[], QWidget] = lambda: _button(USE)  # 照排法造「使用这套方案」（_make_import 换掉）
        self.result_lines: list[tuple[str, str]] = []  # 识别结果区现在显示的各行（色调, 文字）
        self._fetch: HubCall | None = None  # 正在按编号取的那一套（同一时间只取一套）
        self._fetch_focus = False  # 开始取的时候键盘焦点在输入框或按钮上：取完以后焦点回到输入框
        self.run_background: Callable[[Callable[[], None]], object] = _start_thread  # 怎么起后台线程（测试换成当场调用）
        # 账号上的事（登录、发、更新、退出）同一时间只做一件（account_busy）：开着的登录对话框、正在发或更新的那一次、正在
        # 退出的那一次
        self._login: HubLogin | None = None
        self._post: HubCall | None = None
        self._logout_call: HubCall | None = None
        self._pending: S.Scheme | None = None  # 等登录好了接着发的那一套（点「发到作业站」时取好的快照）
        self._dup: tuple | None = None  # 作业站上已有一样的一套、等用户答的：（那一套的编号, 这一套的快照, 登录信息或 None）
        self._view_url = ""  # 「在作业站上看」打开的地址
        self._login_unsaved = False  # 刚登录好、登录信息没能存进本机：这一次发的结果后面另写一句
        self.open_url: Callable[[str], bool] = _open_url  # 怎么在浏览器里打开作业站（测试换成假的，记下网址）
        self.setObjectName("shareWindow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowTitle(TITLE)
        self.setWindowFlags(self.FLAGS)
        self.setStyleSheet(theme.share_style())
        self._parse_timer = QTimer(self)
        self._parse_timer.setSingleShot(True)
        self._parse_timer.setInterval(PARSE_DELAY_MS)
        self._parse_timer.timeout.connect(self.recognize)
        # 取回一套以后等排版落定再滚到导入那一行（_show_import_row；计时器挂在窗口下，窗口删掉时一起没了）
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setSingleShot(True)
        self._reveal_timer.setInterval(0)
        self._reveal_timer.timeout.connect(self._show_import_row)
        # 发到作业站的提示行露出来以后，同样等排版落定再滚到它（_show_post_row）
        self._post_timer = QTimer(self)
        self._post_timer.setSingleShot(True)
        self._post_timer.setInterval(0)
        self._post_timer.timeout.connect(self._show_post_row)
        self.back = None  # 自己画外框时左上角的「返回」（side_tabs 排法里建）
        self.build_layout()
        chrome.frameless(self, self.back is not None, self.FLAGS, resizable=True)
        self.resize(*L.SIZE)
        self.set_format(type(self).last_format)
        self.set_hero(hero)
        self._render_results()
        self.subscribe_skin()

    # ---------------------------------------------------------------- 布局

    def apply_skin(self, skin=None) -> None:
        """换皮肤（theme.set_skin 通知）：重新套样式表，识别结果按新皮肤的颜色重写，几处建窗口时取的皮肤颜色重设。
        排法变了的窗口要重建（悬浮窗做：layout_key 和 layout_name() 不同时关掉重开）。"""
        self.setStyleSheet(theme.share_style())
        self._apply_skin_colors()
        self._fit_fetch_row()
        self._render_results()

    def _pill_colors(self, word: str):
        """「导出」「导入」小胶囊的渐变色（白色海景房），按当前皮肤取。"""
        from .theme import CHROME as K

        if word == TAB_IMPORT:
            return (K.LED_COLORS.get("import") or "#22c3ee", K.LED_COLORS.get("diag") or "#3b82f6")
        return K.ACCENT

    @staticmethod
    def _style_split_line(frame: QFrame) -> None:
        """split 排法里分隔线的颜色，按当前皮肤取（排法不变、窗口没重建时 apply_skin 里重设）。"""
        from .theme import CHROME as K

        frame.setStyleSheet(f"QFrame#{frame.objectName()} {{ background: {chrome.decor.qc(K.CARD_EDGE).name()}; "
                            "border: none; }")

    def _apply_skin_colors(self) -> None:
        """把几处建窗口时取的皮肤颜色按新皮肤重设：胶囊的渐变色、split 排法的两条分隔线（排法不变时窗口不重建）。"""
        for tag in self.findChildren(_PillTag):
            tag.set_colors(self._pill_colors(tag.text()))
        for frame in (getattr(self, "_split_rule", None), getattr(self, "_split_line", None)):
            if frame is not None:
                self._style_split_line(frame)

    # ---------------------------------------------------------------- 造部件和接线
    # 各排法共用：照排法的 _Kit 造部件、接信号，摆放留在各个 _build_* 里。属性名不变（别的地方按名字找部件）。

    def _make_hero_row(self, box: QVBoxLayout, kit: _Kit) -> None:
        """角色筛选、「全选」那一行。"""
        row = QHBoxLayout()
        row.setSpacing(kit.hero_spacing)
        row.addWidget(kit.field(HERO_LABEL))
        self.hero_box = QComboBox()
        self.hero_box.setCursor(Qt.PointingHandCursor)
        self.hero_box.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        draw_combo_rows(self.hero_box)  # 下拉列表每一行和悬浮窗同一种画法
        self.hero_box.currentIndexChanged.connect(self._hero_picked)
        row.addWidget(self.hero_box)
        row.addSpacing(kit.hero_gap)
        self.select_all = QCheckBox(SELECT_ALL)
        if kit.select_name:
            self.select_all.setObjectName(kit.select_name)
        self.select_all.setCursor(Qt.PointingHandCursor)
        self.select_all.clicked.connect(self._select_all_clicked)
        row.addWidget(self.select_all)
        row.addStretch(1)
        box.addLayout(row)

    def _make_scheme_list(self, box: QVBoxLayout, kit: _Kit) -> None:
        """勾选方案的列表（竖向滚动的框；白色海景房的胶囊在 _make_scheme_flow 里另做）。"""
        self.list_area = QScrollArea()
        self.list_area.setObjectName("schemeList")
        self.list_area.setWidgetResizable(True)
        self.list_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_area.setMinimumHeight(kit.list_min_h)
        body = QWidget()
        body.setObjectName("schemeListBody")
        self.list_box = QVBoxLayout(body)
        self.list_box.setContentsMargins(*kit.list_margins)
        self.list_box.setSpacing(kit.list_spacing)
        self.export_empty = _label(EXPORT_EMPTY, "caption", wrap=True)  # 还没有方案时写在空框里（_fill_list）
        self.export_empty.hide()
        self.list_box.addWidget(self.export_empty)
        self.list_box.addStretch(1)
        self.list_area.setWidget(body)
        box.addWidget(self.list_area, 1)

    def _make_scheme_flow(self, box: QVBoxLayout) -> None:
        """勾选方案排成一排可点的胶囊（白色海景房），放不下时换行、随内容变高。"""
        body = QWidget()
        body.setObjectName("schemeListBody")
        self.list_area = body
        self.list_box = _FlowList(body)
        self.list_box.setContentsMargins(0, 2, 0, 2)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        body.setSizePolicy(policy)
        box.addWidget(body)
        # 还没有方案时写下一步怎么做：胶囊排成一排、不是竖着的框，写在胶囊的位置下面（_fill_list）
        self.export_empty = _label(EXPORT_EMPTY, "caption", wrap=True)
        self.export_empty.hide()
        box.addWidget(self.export_empty)

    def _make_fetch(self, kit: _Kit) -> None:
        """「按编号导入」那一行（fetch_row：输入框占满，按钮在右，按钮和导入、清空同一种次要样式）和它下面的提示行
        （fetch_note）。各排法都放在导入这一块粘贴框的上面：先从作业站取，取回的出现在下面的粘贴框里，再看识别结果、导入，
        从上往下顺着走。hub.ENABLED 为假时照样放进布局（落在窗口里，不成单独的顶层部件），只是不显示、不占位。"""
        row = self.fetch_row = QWidget()
        row.setObjectName("fetchRow")
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(kit.buttons_spacing)
        self._ref_holder = QVBoxLayout()  # 输入框上下留白，和按钮画出来的那一块对齐（_fit_fetch_row）
        self._ref_holder.setSpacing(0)
        tip = tip_paragraphs_html(FETCH_TIP)
        self.ref_edit = _RefEdit()
        self.ref_edit.setObjectName("hubRef")
        self.ref_edit.setPlaceholderText(FETCH_PLACEHOLDER)
        self.ref_edit.setToolTip(tip)
        self.ref_edit.returnPressed.connect(self.fetch_by_ref)
        self.ref_edit.textEdited.connect(self._ref_edited)
        self._ref_holder.addWidget(self.ref_edit)
        line.addLayout(self._ref_holder, 1)
        self.fetch_btn = kit.hub_button(FETCH)
        self.fetch_btn.setCursor(Qt.PointingHandCursor)
        self.fetch_btn.setToolTip(tip)
        self.fetch_btn.clicked.connect(self.fetch_by_ref)
        line.addWidget(self.fetch_btn, 0, Qt.AlignVCenter)
        self._fit_fetch_row()
        self.fetch_note = _Note(kit.warn(""), row)
        if not hub.ENABLED:
            row.hide()

    def _fit_fetch_row(self) -> None:
        """输入框上下留白：和同一行的按钮画出来的那一块一样高、上下对齐（_body_insets；按钮的形状跟着皮肤）。"""
        top, bottom = _body_insets(self.fetch_btn)
        self._ref_holder.setContentsMargins(0, top, 0, bottom)

    def _make_paste(self, name: str) -> None:
        """粘贴框（按 Tab 换焦点，不往框里插制表符）。"""
        self.paste = _TextBox(PASTE_H)
        self.paste.setTabChangesFocus(True)
        if name:
            self.paste.setObjectName(name)
        self.paste.textChanged.connect(self._paste_changed)

    def _make_results(self, fit: bool, prefer: bool) -> None:
        """识别结果区：fit 时按内容定高（游戏内风格的排法），prefer 时按首选高度排开。识别结果写在一个富文本标签里；导入以后
        换成一套一行（done_box：「已导入……」和右边的「使用这套方案」，_fill_done 建），行多了在区里上下滚动。"""
        self.results_area = _FitArea(self._fit_results) if fit else QScrollArea()
        if prefer:
            self.results_area.prefer_content = True
        self.results_area.setObjectName("results")
        self.results_area.setWidgetResizable(True)
        self.results_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.results_area.setMinimumHeight(RESULTS_MIN_H)
        body = QWidget()
        body.setObjectName("resultsBody")
        results_box = QVBoxLayout(body)
        results_box.setContentsMargins(0, 0, 0, 0)
        self.results = QLabel("")
        self.results.setTextFormat(Qt.RichText)  # 内容都经过 result_html 转义
        self.results.setTextInteractionFlags(Qt.NoTextInteraction)  # 富文本默认接走鼠标移动；关掉文字交互，自己画外框时按在字上照样能拖动窗口
        self.results.setWordWrap(True)
        self.results.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        results_box.addWidget(self.results)
        self.done_box = QWidget()
        self.done_box.setObjectName("doneBox")
        self.done_rows = QVBoxLayout(self.done_box)
        self.done_rows.setContentsMargins(0, 0, 4, 0)
        self.done_rows.setSpacing(6)
        self.done_box.hide()
        results_box.addWidget(self.done_box)
        results_box.addStretch(1)
        self.results_area.setWidget(body)

    def _make_format(self, box: QVBoxLayout, kit: _Kit, vertical: bool, capsules: bool) -> None:
        """格式的两个单选按钮（白色海景房画成一段一段的分段按钮）。"""
        self.format_group = QButtonGroup(self)
        self.format_buttons: dict[str, QRadioButton] = {}

        def add_radio(layout, key: str) -> None:
            radio = QRadioButton(FORMAT_OPTION[key])
            radio.setCursor(Qt.PointingHandCursor)
            if capsules:
                radio.setObjectName("segment")
            radio.toggled.connect(lambda on, f=key: on and self._format_picked(f))
            self.format_group.addButton(radio)
            self.format_buttons[key] = radio
            layout.addWidget(radio)

        if kit.format_segments:  # 标题和单选按钮放在一行（霓虹贴纸、白色海景房）
            line = QHBoxLayout()
            line.setSpacing(kit.format_line_spacing)
            line.addWidget(kit.field(FORMAT_LABEL), 0, Qt.AlignVCenter)
            formats = QHBoxLayout()
            if capsules:  # 分段按钮：浅灰底的一条，选中的那一段是白色的滑块
                segments = QWidget()
                segments.setObjectName("segments")
                formats.setSpacing(0)
                formats.setContentsMargins(3, 3, 3, 3)
                segments.setLayout(formats)
                for key in (S.FORMAT_SHORT, S.FORMAT_TEXT):
                    add_radio(formats, key)
                line.addWidget(segments, 0, Qt.AlignVCenter)
                line.addStretch(1)
            else:
                formats.setSpacing(kit.format_spacing)
                for key in (S.FORMAT_SHORT, S.FORMAT_TEXT):
                    add_radio(formats, key)
                formats.addStretch(1)
                line.addLayout(formats, 1)
            box.addLayout(line)
            return
        if kit.format_indent:  # 标题单独放，两颗单选按钮缩进成一个子布局（经典主题-黑等）
            line = QHBoxLayout()
            line.setSpacing(kit.format_line_spacing)
            line.addWidget(kit.field(FORMAT_LABEL), 0, Qt.AlignTop)
            formats = QVBoxLayout() if vertical else QHBoxLayout()
            formats.setSpacing(6 if vertical else kit.format_spacing)
            for key in (S.FORMAT_SHORT, S.FORMAT_TEXT):
                add_radio(formats, key)
            if not vertical:
                formats.addStretch(1)
            line.addLayout(formats, 1)
            box.addLayout(line)
            return
        row = QHBoxLayout()
        row.setSpacing(kit.format_spacing)
        if not kit.format_own_card:  # 格式卡片自己写了「格式」，不再写一遍
            row.addWidget(kit.dim(FORMAT_LABEL))
        for key in (S.FORMAT_SHORT, S.FORMAT_TEXT):
            add_radio(row, key)
        row.addStretch(1)
        box.addLayout(row)

    def _make_export(self, kit: _Kit, vertical_formats: bool = False, inner_head: QWidget | None = None,
                     capsules: bool = False) -> tuple[QWidget, QWidget | None]:
        """导出这一块：分组说明、角色筛选和「全选」、勾选方案的列表、格式、预览、复制。（导出卡片, 格式卡片或 None）。
        inner_head 给了时放在卡片最上面（白色海景房的胶囊）；capsules：勾选方案是一排胶囊、格式是分段按钮。"""
        export = self.export_box = kit.export_card()
        if kit.box_spacing is not None:
            export.box.setSpacing(kit.box_spacing)
        if not kit.custom_head:
            text = EXPORT_HEAD.removeprefix(TAB_EXPORT + "：") if kit.strip_head else EXPORT_HEAD
            export.box.addWidget(kit.head(text))
        if inner_head is not None:
            export.box.addWidget(inner_head)
        self._make_hero_row(export.box, kit)
        if capsules:
            self._make_scheme_flow(export.box)
        else:
            self._make_scheme_list(export.box, kit)
        card = kit.format_card() if kit.format_card is not None else None
        tail = card.box if card is not None else export.box
        self._make_format(tail, kit, vertical_formats, capsules)
        self.preview = _TextBox(PREVIEW_H if vertical_formats else GAME_PREVIEW_H, read_only=True) if kit.game_preview \
            else _TextBox(kit.preview_h, read_only=True)
        if kit.preview_name:
            self.preview.setObjectName(kit.preview_name)
        tail.addWidget(self.preview)
        self.maps_warn = _FitLabel("", "dim")  # 同 kit.dim 的小字，自动换行
        self.maps_warn.hide()
        tail.addWidget(self.maps_warn)
        self.export_warn = kit.warn("")
        self.export_warn.hide()
        tail.addWidget(self.export_warn)
        row = QHBoxLayout()
        row.setSpacing(kit.copy_spacing)
        self.copy_btn = kit.copy_button()
        self.copy_btn.clicked.connect(self.copy_to_clipboard)
        row.addWidget(self.copy_btn)
        self.copied = _label("", "okText")
        self.copied.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self.copied, 1)
        self.post_btn = kit.hub_button(POST_TO_HUB)
        self.post_btn.setCursor(Qt.PointingHandCursor)
        self.post_btn.setToolTip(tip_paragraphs_html(POST_TO_HUB_TIP))
        self.post_btn.clicked.connect(self.publish_selected)
        if not hub.ENABLED:
            # 照样放进布局（落在窗口里，不成单独的顶层部件），只是不显示：看不见的部件不占位、前后不加间距，
            # 单独一行的排法也不多出空行
            self.post_btn.hide()
        if kit.hub_own_row:
            tail.addLayout(row)
            tail.addWidget(self.post_btn, 0, Qt.AlignRight)
        else:
            row.addWidget(self.post_btn)
            tail.addLayout(row)
        self._make_account(tail, kit)
        return export, card

    def _make_account(self, box: QVBoxLayout, kit: _Kit) -> None:
        """「发到作业站」下面：提示行（post_note：正在发、发好了、出错的原因，作业站上已有一样的一套时问要不要更新）和它下面的
        小按钮（post_actions：「更新那一套」「不更新」「在作业站上看」，用到哪个显示哪个），再下面「已登录作业站：昵称」和
        「退出作业站」（account_row，没登录时不显示）。都由 hub.ENABLED 管：为假时照样放进布局，只是不显示。kit 另记下来，
        登录对话框照它造按钮。"""
        self._kit = kit
        self.post_note = _Note(kit.warn(""), self.preview)
        box.addWidget(self.post_note)
        actions = self.post_actions = QWidget()
        actions.setObjectName("postActions")
        line = QHBoxLayout(actions)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(kit.buttons_spacing)
        self.dup_update_btn = kit.action_button(DUP_UPDATE)
        self.dup_skip_btn = kit.action_button(DUP_SKIP)
        self.view_btn = kit.action_button(VIEW_ON_HUB)
        for btn, slot in ((self.dup_update_btn, self._dup_update), (self.dup_skip_btn, self._dup_skip),
                          (self.view_btn, self._view_on_hub)):
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(slot)
            btn.hide()
            line.addWidget(btn)
        line.addStretch(1)
        actions.hide()
        box.addWidget(actions)
        account = self.account_row = QWidget()
        account.setObjectName("accountRow")
        line = QHBoxLayout(account)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(kit.buttons_spacing)
        self.account_label = _label("", "dim")
        self.account_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)  # 昵称已截短；不撑宽窗口
        line.addWidget(self.account_label, 1)
        self.logout_btn = kit.action_button(LOGOUT)
        self.logout_btn.setCursor(Qt.PointingHandCursor)
        self.logout_btn.clicked.connect(self.logout)
        line.addWidget(self.logout_btn, 0, Qt.AlignVCenter)
        account.hide()
        box.addWidget(account)

    def _make_import(self, kit: _Kit, fit: bool = False, inner_head: QWidget | None = None,
                     prefer: bool = False) -> QWidget:
        """导入这一块：分组说明、按编号导入那一行和提示行、粘贴框、识别结果（游戏内风格的排法按内容定高）、导入和清空、只读
        提示、说明。"""
        imp = self.import_box = kit.import_card()
        self._use_button = kit.use_button  # 导入以后每一套那一行的「使用这套方案」照这一种排法造（_fill_done）
        if kit.box_spacing is not None:
            imp.box.setSpacing(kit.box_spacing)
        if not kit.custom_head:
            text = IMPORT_HEAD.removeprefix(TAB_IMPORT + "：") if kit.strip_head else IMPORT_HEAD
            imp.box.addWidget(kit.head(text))
        if inner_head is not None:
            imp.box.addWidget(inner_head)
        self._make_fetch(kit)
        imp.box.addWidget(self.fetch_row)
        imp.box.addWidget(self.fetch_note)
        self._make_paste(kit.paste_name)
        imp.box.addWidget(self.paste)
        self._make_results(fit, prefer)
        imp.box.addWidget(self.results_area, kit.results_stretch)
        row = QHBoxLayout()
        row.setSpacing(kit.buttons_spacing)
        self.import_btn = kit.import_button()
        self.import_btn.clicked.connect(self.import_schemes)
        row.addWidget(self.import_btn)
        self.clear_btn = kit.clear_button()
        self.clear_btn.clicked.connect(self.clear_paste)
        row.addWidget(self.clear_btn)
        row.addStretch(1)
        self.find_btn = kit.hub_button(FIND_ON_HUB)
        self.find_btn.setCursor(Qt.PointingHandCursor)
        self.find_btn.setToolTip(tip_paragraphs_html(FIND_ON_HUB_TIP))
        self.find_btn.clicked.connect(lambda: self.hub_requested.emit("plans"))
        if not hub.ENABLED:
            self.find_btn.hide()  # 同「发到作业站」：照样放进布局，只是不显示
        if kit.hub_own_row:
            imp.box.addLayout(row)
            imp.box.addWidget(self.find_btn, 0, Qt.AlignRight)
        else:
            row.addWidget(self.find_btn)
            imp.box.addLayout(row)
        self.read_only_hint = kit.warn(READ_ONLY)
        self.read_only_hint.hide()
        imp.box.addWidget(self.read_only_hint)
        imp.box.addWidget(_FitLabel(IMPORT_NOTE, "caption"))
        if kit.trailing_stretch:  # 识别结果、按钮紧跟在粘贴框下面，多出来的空白在卡片最下面
            imp.box.addStretch(1)
        return imp

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 12)
        outer.setSpacing(8)
        title = _label(TITLE, "section")
        outer.addWidget(title)

        # ---- 导出
        export, _ = self._make_export(PLAIN_KIT)
        outer.addWidget(export, 1)

        # ---- 导入
        imp = self._make_import(PLAIN_KIT)
        outer.addWidget(imp, 1)

        outer.addWidget(_FitLabel(SHARE_NOTE, "caption"))
        row = QHBoxLayout()
        row.addStretch(1)
        self.close_btn = _button(CLOSE)
        self.close_btn.clicked.connect(self.close)
        row.addWidget(self.close_btn)
        outer.addLayout(row)

    def _build_tabs(self) -> None:
        """游戏内风格的排法：左上角「返回」（皮肤自己画外框时）、左侧白色导航栏里「导出」「导入」两个页签，右边深灰面板里
        当前页签的卡片（导出：勾选方案、格式和预览、复制；导入：粘贴框、识别结果、导入），面板下面写短字符串的说明。
        部件的名字和默认的排法相同（别的方法按这些名字找部件）；一个窗口只留一个关闭按钮：自己画外框时没有「关闭」按钮，
        close_btn 就是「返回」。"""
        from .theme import CHROME as K

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = self.frame = chrome.Frame()
        outer.addWidget(frame)
        root = QHBoxLayout(frame)
        root.setContentsMargins(0, 0, 20, 0)
        root.setSpacing(0)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 22)
        left.setSpacing(14)
        if chrome.custom():
            back_row = QHBoxLayout()
            back_row.setContentsMargins(K.BACK_POS[0], K.BACK_POS[1], 0, 0)
            self.back = chrome.BackButton()
            back_row.addWidget(self.back, 0, Qt.AlignLeft)
            left.addLayout(back_row)
        else:
            left.addSpacing(72)
        rail = self.rail = chrome.NavRail()
        left.addWidget(rail, 1)
        root.addLayout(left)
        right = QVBoxLayout()
        right.setContentsMargins(0, 18, 0, 8)
        right.setSpacing(6)
        panel = chrome.Panel(seed=30)
        holder = QVBoxLayout(panel)
        holder.setContentsMargins(24, 24, 24, 24)
        stack = self.pages = QStackedWidget()
        holder.addWidget(stack)
        right.addWidget(panel, 1)
        note = _FitLabel(SHARE_NOTE, "shareNote")
        note.setContentsMargins(24, 0, 12, 0)
        right.addWidget(note)
        root.addLayout(right, 1)

        # ---- 导出：一张卡片放角色、全选、方案列表；一张「格式」卡片放格式、预览、复制
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(18)
        export, fmt = self._make_export(PILL_KIT)
        col.addWidget(export, 1)
        col.addWidget(fmt)
        stack.addWidget(page)

        # ---- 导入：一张卡片放粘贴框、识别结果、导入和清空
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        imp = self._make_import(PILL_KIT)
        col.addWidget(imp, 1)
        stack.addWidget(page)

        for index, name in enumerate((TAB_EXPORT, TAB_IMPORT)):
            tab = rail.add_tab(name)
            tab.toggled.connect(lambda on, i=index: on and stack.setCurrentIndex(i))
        rail.box.addStretch(1)
        rail.tabs()[0].setChecked(True)
        self.tabs = {tab.text(): tab for tab in rail.tabs()}
        if self.back is not None:
            self.close_btn = self.back
        else:  # 用系统标题栏时右下角照旧有「关闭」
            self.close_btn = chrome.PillButton(CLOSE)
            self.close_btn.clicked.connect(self.close)
            right.addWidget(self.close_btn, 0, Qt.AlignRight)

    def _game_parts(self, vertical_formats: bool, hub_own_row: bool = True) -> tuple[QWidget, QWidget]:
        """经典主题-黑、反色、碳纤流光三种排法共用：导出、导入两张卡片（chrome.Card：卡片的样子、标签按皮肤）。导出：角色、
        全选、方案列表、格式和预览、复制；导入：粘贴框、识别结果、导入和清空、只读提示和说明。部件的名字和默认的排法相同。
        vertical_formats：两种格式的单选按钮上下排（左右并排的窗口每块较窄）。hub_own_row：作业站的按钮在那一行下面单独
        一行靠右——经典主题-黑、碳纤流光左右并排、每块较窄，放同一行会把窗口撑宽；反色上下排，传 False 放同一行，不然
        默认大小下「导入 N 套为我的方案」「清空」「去作业站找方案」会被挤出看得见的地方。"""
        kit = GAME_KIT if hub_own_row else dataclasses.replace(GAME_KIT, hub_own_row=False)
        export, _ = self._make_export(kit, vertical_formats=vertical_formats)
        imp = self._make_import(kit, fit=True)
        return export, imp

    def _export_column(self, export: QWidget) -> QWidget:
        """左右并排的排法（side_by_side、split）里导出那一列：每列较窄，发到作业站以后作业站给的说明较长时提示行要折好几行，
        跟着撑高的话窗口会比矮屏幕（768 高）还高。所以导出卡片放进 _ColumnScroll：提示行和小按钮放不下时只有这一列上下滚动
        （_show_post_row 滚到提示行），窗口不变高。hub.ENABLED 为假时提示行不会出现，卡片照旧直接放进那一列。"""
        if not hub.ENABLED:
            return export

        def reserve() -> int:
            # 那一排小按钮三个都露出来时要的宽，加上卡片左右的边距（卡片里竖排布局的边距；卡片外面另有一层布局时连它的）
            buttons = (self.dup_update_btn, self.dup_skip_btn, self.view_btn)
            row = sum(b.sizeHint().width() for b in buttons) + self.post_actions.layout().spacing() * (len(buttons) - 1)
            for layout in {id(x): x for x in (export.box, export.layout()) if x is not None}.values():
                margins = layout.contentsMargins()
                row += margins.left() + margins.right()
            return row

        return _ColumnScroll(export, (self.post_note, self.post_actions), export.box.spacing(), reserve)

    def _game_frame(self) -> tuple[QVBoxLayout, QVBoxLayout]:
        """经典主题-黑、反色两种排法共用：窗口底（chrome.Frame）、顶上一行（标题、右上角 ×，chrome.WindowHead）。返回
        （整个窗口的竖排布局, 放内容的竖排布局）。× 就是关闭窗口（close_btn）；用系统标题栏的皮肤另放「关闭」。"""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = self.frame = chrome.Frame()
        outer.addWidget(frame)
        root = QVBoxLayout(frame)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        head = chrome.WindowHead(TITLE)
        self.back = head.closer
        root.addWidget(head)
        body = QVBoxLayout()
        root.addLayout(body, 1)
        return root, body

    def _closer_or_button(self, row: QHBoxLayout) -> None:
        """一个窗口只留一个关闭按钮：自己画外框时 close_btn 就是右上角的 ×；用系统标题栏时 row 的右端另放「关闭」。"""
        if self.back is not None:
            self.close_btn = self.back
        else:
            self.close_btn = chrome.GameButton(CLOSE, "secondary", height=40, px=15)
            self.close_btn.clicked.connect(self.close)
            row.addWidget(self.close_btn, 0, Qt.AlignRight | Qt.AlignVCenter)

    def _build_columns(self) -> None:
        """经典主题-黑的排法（窗口横着，约 920×600）：顶上正中的标题页签、右上角 ×；导出、导入两块（靛紫网点的底，标题前
        两颗小菱形）左右并排，最下面一行写短字符串的说明。"""
        _, body = self._game_frame()
        body.setContentsMargins(26, 14, 26, 18)
        body.setSpacing(14)
        export, imp = self._game_parts(vertical_formats=True)
        columns = QHBoxLayout()
        columns.setSpacing(20)
        columns.addWidget(self._export_column(export), 1)
        columns.addWidget(imp, 1)
        body.addLayout(columns, 1)
        row = QHBoxLayout()
        row.setSpacing(12)
        note = _FitLabel(SHARE_NOTE, "shareNote")
        row.addWidget(note, 1)
        self._closer_or_button(row)
        body.addLayout(row)

    def _build_stacked(self) -> None:
        """经典主题-反色的排法（窗口竖着，约 620×840）：顶上黑色通栏写标题、右上角 ×；导出、导入两张白卡片上下排（卡片
        左上角压黑色斜角标签），最下面一条洋红横幅写短字符串的说明。作业站的两个按钮和导入、清空放在同一行（hub_own_row
        为 False）：两张卡片放在滚动区里，单独一行会把「导入 N 套为我的方案」「清空」「去作业站找方案」挤出可见区。窗口的
        高度连按编号导入那一行算在内：默认大小下导入、清空、去作业站找方案照样整个看得见。"""
        _, body = self._game_frame()
        body.setContentsMargins(20, 14, 20, 16)
        body.setSpacing(14)
        export, imp = self._game_parts(vertical_formats=False, hub_own_row=False)
        self.results_area.prefer_content = True  # 卡片按首选高度排：识别结果整个排开
        # 两张卡片上下排比较高：放在一个透明的滚动区里，屏幕放不下时窗口照样能缩到可用高度，卡片在窗口里上下滚动
        scroll = QScrollArea()
        scroll.setObjectName("cardsScroll")
        scroll.setFocusPolicy(Qt.NoFocus)  # 透明的滚动区不进 Tab 顺序（焦点停在它上面看不出来）
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        cards = QWidget()
        cards.setObjectName("cardsBody")
        column = QVBoxLayout(cards)
        column.setContentsMargins(0, 10, 0, 0)
        column.setSpacing(22)
        for card in (export, imp):
            _no_shrink(card)
            column.addWidget(card, 1)
        scroll.setWidget(cards)
        # 只上下滚动：窗口最窄为卡片内容需要的宽（再加滚动条），不把卡片挤坏
        scroll.setMinimumWidth(cards.minimumSizeHint().width() + scroll.verticalScrollBar().sizeHint().width() + 4)
        body.addWidget(scroll, 1)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(chrome.Banner(SHARE_NOTE), 1)
        self._closer_or_button(row)
        body.addLayout(row)

    # ---- 霓虹贴纸（banded）、白色海景房（capsules）

    def _rgb_head(self, word: str, head: str, colors) -> QWidget:
        """「导出」「导入」那一行：霓虹贴纸是一条黑色斜带（chrome.Tag，ChromeLook.TAG_KIND 为 band），白色海景房是一枚渐变小胶囊
        （_PillTag，colors 为渐变色）；后面接说明（去掉开头的「导出：」「导入：」）。"""
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(10)
        tag = _PillTag(word, colors) if colors else chrome.Tag(word)
        tag.setFixedSize(tag.sizeHint())
        line.addWidget(tag, 0, Qt.AlignVCenter)
        line.addWidget(_FitLabel(head.removeprefix(word + "："), "groupHead"), 1, Qt.AlignVCenter)
        return row

    def _rgb_parts(self, capsules: bool) -> tuple[QWidget, QWidget, QWidget, QWidget]:
        """霓虹贴纸、白色海景房两种排法共用：（导出那一行, 导出卡片, 导入那一行, 导入卡片）。部件的名字和默认的排法相同。
        capsules：勾选方案排成一排可点的胶囊（_FlowList），格式是分段按钮（单选按钮画成一段一段，样式表的 #segment）；否则
        方案列表竖排在一个框里，格式是两个单选按钮。"""
        k = theme.CHROME
        export_colors = k.ACCENT if capsules else None
        import_colors = ((k.LED_COLORS.get("import") or "#22c3ee", k.LED_COLORS.get("diag") or "#3b82f6")
                         if capsules else None)
        export_head = self._rgb_head(TAB_EXPORT, EXPORT_HEAD, export_colors)
        import_head = self._rgb_head(TAB_IMPORT, IMPORT_HEAD, import_colors)
        export, _ = self._make_export(RGB_KIT, inner_head=export_head if capsules else None, capsules=capsules)
        imp = self._make_import(RGB_KIT, fit=True, inner_head=import_head if capsules else None, prefer=True)
        return export_head, export, import_head, imp

    def _rgb_frame(self, capsules: bool) -> None:
        """霓虹贴纸、白色海景房两种排法：窗口底和顶上一行（标题、右上角 ×，_game_frame）；下面导出、导入两块上下排，放在透明的
        滚动区里（屏幕放不下时窗口照样能缩到可用高度，卡片在窗口里上下滚动）；最下面一行写短字符串的说明。capsules：「导出」
        「导入」的小胶囊写在卡片里面（白色海景房）；否则黑色斜带写在卡片上面（霓虹贴纸）。"""
        _, body = self._game_frame()
        body.setContentsMargins(20, 2, 20, 18)
        body.setSpacing(10)
        export_head, export, import_head, imp = self._rgb_parts(capsules)
        scroll = QScrollArea()
        scroll.setObjectName("cardsScroll")
        scroll.setFocusPolicy(Qt.NoFocus)  # 透明的滚动区不进 Tab 顺序（焦点停在它上面看不出来）
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        cards = QWidget()
        cards.setObjectName("cardsBody")
        column = QVBoxLayout(cards)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8 if not capsules else 12)
        for head, card in ((export_head, export), (import_head, imp)):
            if not capsules:
                column.addWidget(head)
            _no_shrink(card)
            column.addWidget(card, 1)
            if not capsules and card is export:
                column.addSpacing(6)
        scroll.setWidget(cards)
        scroll.setMinimumWidth(cards.minimumSizeHint().width() + scroll.verticalScrollBar().sizeHint().width() + 4)
        body.addWidget(scroll, 1)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(_FitLabel(SHARE_NOTE, "shareNote"), 1)
        self._closer_or_button(row)
        body.addLayout(row)

    def _build_banded(self) -> None:
        """霓虹贴纸的排法（窗口竖着，约 600×700，高度连按编号导入那一行算在内）：顶上一行白字黑描边的标题、右上角 ×；导出、
        导入两块上下排，每块上面一条黑色斜带写「导出」「导入」、后面接说明，下面是粗黑描边的贴纸卡片；最下面一行写短字符串的
        说明。"""
        self._rgb_frame(capsules=False)

    def _build_capsules(self) -> None:
        """白色海景房的排法（窗口竖着，约 600×700，同霓虹贴纸）：顶上一行标题、右上角 ×；导出、导入两张白卡片上下排，卡片左上角
        一枚渐变小胶囊写「导出」「导入」、后面接说明；勾选方案是一排可点的胶囊，格式是分段按钮；最下面一行写短字符串的说明。"""
        self._rgb_frame(capsules=True)

    # ---- 碳纤流光（split）

    def _build_split(self) -> None:
        """碳纤流光的排法（窗口横着，约 900×580，高度连按编号导入那一行和提示行算在内）：顶上一行（四色小方块、斜体粗体的
        标题，右上角 ×）；导出、导入两块左右并排，中间一条竖线，每块上面是编号的分组标题（「01 导出 ──」「02 导入 ──」，
        不画卡片的底和框）；最下面一行写短字符串的说明。两块的内容同经典主题-黑（_game_parts：两种格式上下排）。按钮是
        切角的，主按钮外面一圈光。"""
        _, body = self._game_frame()
        body.setContentsMargins(22, 2, 22, 16)
        body.setSpacing(12)
        export, imp = self._game_parts(vertical_formats=True)
        columns = QHBoxLayout()
        columns.setSpacing(18)
        for number, card in (("01", export), ("02", imp)):
            card.plain = True
            card.set_number(number)
        columns.addWidget(self._export_column(export), 1)
        rule = self._split_rule = QFrame()
        rule.setObjectName("splitRule")
        rule.setFixedWidth(1)
        self._style_split_line(rule)
        columns.addWidget(rule)
        columns.addWidget(imp, 1)
        body.addLayout(columns, 1)
        line = self._split_line = QFrame()
        line.setObjectName("splitLine")
        line.setFixedHeight(1)
        self._style_split_line(line)
        body.addWidget(line)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(_FitLabel(SHARE_NOTE, "shareNote"), 1)
        self._closer_or_button(row)
        body.addLayout(row)
        # 方案列表、预览框、粘贴框、按编号导入的输入框画成切角的框（样式表里它们的底和框透明，四周留出画框线的地方）
        chrome.cut_corners(self.list_area, "well")
        for box in (self.preview, self.paste, self.ref_edit):
            chrome.cut_corners(box, "field")

    # ---------------------------------------------------------------- 导出

    def set_hero(self, hero: str | None) -> None:
        """角色筛选改为 hero（None、资料库里没有的角色为全部角色），并勾选该角色正在用的方案（有的话）。"""
        hero = hero if isinstance(hero, str) and hero in self.db.heroes else None
        self._filter = hero
        if hero is not None:
            current = self.store.current(hero)
            if current is not None:
                self._checked.add(current.id)
        self.refresh()

    def hero_filter(self) -> str | None:
        """现在的角色筛选（None 为全部角色）。"""
        return self._filter

    def refresh(self) -> None:
        """按方案文件的最新内容刷新：角色下拉框、方案列表（保留勾选，删掉的方案不再勾选）、预览、导入按钮和只读提示；
        刚导入的几行里在别处删掉了的那一套写「这套方案已被删除」（只改那一行的字，不重建：可能正在点里面的按钮）。"""
        schemes = self.store.schemes()
        self._checked &= {s.id for s in schemes}
        self._fill_heroes(schemes)
        self._fill_list(schemes)
        self.read_only_hint.setVisible(bool(self.store.read_only))
        self._update_import_button()
        self._update_export()
        self._show_done()

    def _hero_rank(self) -> dict[str, int]:
        """角色的排列顺序：和悬浮窗的角色下拉框相同（按六类分组，组内按资料库的顺序）。"""
        return {h.name: i for i, h in enumerate(heroes_in_group_order(self.db.heroes.values()))}

    def _fill_heroes(self, schemes: list[S.Scheme]) -> None:
        """角色下拉框：「全部角色」，再列有我的方案的角色（筛选中的角色没有方案时也列出），按六类排，每一类前面一行不能选的
        组名（widgets.fill_hero_combo，和悬浮窗、编辑窗口的角色下拉框同一种）；选中项按筛选的角色找（findData）。"""
        hero_names = {s.hero for s in schemes}
        if self._filter is not None:
            hero_names.add(self._filter)
        heroes = [h for h in self.db.heroes.values() if h.name in hero_names]  # 资料库的先后（同一类里照它排）
        items: list[str | None] = [None, *(h.name for h in heroes_in_group_order(heroes))]
        self.hero_box.blockSignals(True)
        if items != self._hero_items:
            self.hero_box.clear()
            self.hero_box.addItem(ALL_HEROES, None)
            fill_hero_combo(self.hero_box, heroes, text=names.hero_name)
            self._hero_items = items
        self.hero_box.setCurrentIndex(0 if self._filter is None else max(self.hero_box.findData(self._filter), 0))
        self.hero_box.blockSignals(False)

    def _fill_list(self, schemes: list[S.Scheme]) -> None:
        """方案列表：当前筛选下的我的方案，按角色排（同一角色里按方案文件的先后），每套一个复选框。一套我的方案都没有时
        列表里写下一步怎么做（EXPORT_EMPTY）。"""
        if self.export_empty.isHidden() == (not schemes):
            self.export_empty.setVisible(not schemes)
        rank = self._hero_rank()
        visible = [s for s in schemes if self._filter is None or s.hero == self._filter]
        visible.sort(key=lambda s: rank.get(s.hero, len(rank)))
        self._visible = visible
        key = tuple((s.id, s.hero, s.name) for s in visible)
        if key != self._list_key:
            for box in self._boxes.values():
                self.list_box.removeWidget(box)
                box.hide()
                box.deleteLater()
            self._boxes = {}
            for s in visible:
                box = QCheckBox(button_text(ITEM.format(hero=names.hero_in(self.db, s.hero), name=lang.t(s.name))))
                box.setCursor(Qt.PointingHandCursor)
                box.toggled.connect(lambda on, sid=s.id: self._item_toggled(sid, on))
                self.list_box.insertWidget(self.list_box.count() - 1, box)
                self._boxes[s.id] = box
            self._list_key = key
            self._order_tabs()
        maps = {s.id: s.maps for s in visible}
        for sid, box in self._boxes.items():
            box.blockSignals(True)
            box.setChecked(sid in self._checked)
            box.blockSignals(False)
            # 标了地图的那一套，悬停说明写上地图（通用的不写）
            shown = [names.map_in(self.db, m) for m in maps.get(sid, ())]
            box.setToolTip(LIST_MAPS.format(maps=LIST_SEP.join(shown)) if shown else "")

    def _order_tabs(self) -> None:
        """方案勾选框每次刷新都会重建，默认的次序把它们排到了最后（落在「关闭」之后）。这里排到「全选」后面、
        列表区、格式之前，和画面上的先后一致。"""
        prev = self.select_all
        for box in self.list_boxes():
            self.setTabOrder(prev, box)
            prev = box
        self.setTabOrder(prev, self.list_area)
        short = self.format_buttons.get(S.FORMAT_SHORT)
        if short is not None:
            self.setTabOrder(self.list_area, short)

    def _hero_picked(self, index: int) -> None:
        item = self.hero_box.model().item(index) if index >= 0 else None
        if item is not None and not item.isEnabled():  # 分组标题选不上；万一选到了，按原来的筛选复原
            self._fill_heroes(self.store.schemes())
            return
        self._filter = self.hero_box.itemData(index)
        self._fill_list(self.store.schemes())
        self._update_export()

    def _item_toggled(self, scheme_id: str, on: bool) -> None:
        if on:
            self._checked.add(scheme_id)
        else:
            self._checked.discard(scheme_id)
        self._update_export()

    def _select_all_clicked(self, on: bool) -> None:
        """「全选」：勾选当前列出的全部方案；已经全勾上时再点则全部取消。"""
        ids = {s.id for s in self._visible}
        if on:
            self._checked |= ids
        else:
            self._checked -= ids
        for sid, box in self._boxes.items():
            box.blockSignals(True)
            box.setChecked(sid in self._checked)
            box.blockSignals(False)
        self._update_export()

    def list_boxes(self) -> list[QCheckBox]:
        """方案列表现在的各行（复选框），按列表的顺序。"""
        return [self._boxes[s.id] for s in self._visible]

    def selected_schemes(self) -> list[S.Scheme]:
        """要导出的方案：当前列出来、勾选了的，按列表的顺序。"""
        return [s for s in self._visible if s.id in self._checked]

    def format(self) -> str:
        """现在选的格式：schemes.FORMAT_SHORT 或 FORMAT_TEXT。"""
        return next((f for f, radio in self.format_buttons.items() if radio.isChecked()), S.FORMAT_SHORT)

    def set_format(self, fmt: str) -> None:
        """选格式（不认识的按短字符串）。"""
        fmt = fmt if fmt in self.format_buttons else S.FORMAT_SHORT
        self.format_buttons[fmt].setChecked(True)
        self._update_export()

    def _format_picked(self, fmt: str) -> None:
        if type(self).last_format != fmt:
            type(self).last_format = fmt
        self._update_export()
        self.format_changed.emit(fmt)

    def export_text(self) -> str:
        """预览框里的内容，也就是「复制到剪贴板」放进剪贴板的内容：选中的方案每套一行。"""
        return self._export_text

    def _update_export(self) -> None:
        fmt = self.format()
        lines: list[str] = []
        failed = 0
        for s in self.selected_schemes():
            if fmt == S.FORMAT_TEXT:
                lines.append(S.to_text(s))
                continue
            try:
                lines.append(S.to_short(s, self.catalog))
            except ValueError as e:  # 筹码编号表读不出来、或缺这个筹码的编号
                failed += 1
                log.warning("方案「%s」（%s）无法生成短字符串：%s", s.name, s.hero, e)
        text = "\n".join(lines)
        if text != self._export_text:
            self._export_text = text
            self.preview.setPlainText(text)
            self.copied.clear()
            self._copy_failed = False
        self._export_count = len(lines)
        self.copy_btn.setEnabled(bool(lines))
        self._export_failed = failed
        self._show_maps_warn()
        self._show_export_warn()
        ids = {s.id for s in self._visible}
        self.select_all.setEnabled(bool(ids))
        self.select_all.setChecked(bool(ids) and ids <= self._checked)

    def _show_maps_warn(self) -> None:
        """勾选的方案里有标了地图的、而这一版分享格式还不带地图时，在预览框下面写一句；写了地图以后（WRITE_MAPS）不写。"""
        shown = any(s.maps for s in self.selected_schemes()) and not getattr(S, "WRITE_MAPS", False)
        self.maps_warn.setText(MAPS_TEMP_NOTE)
        self.maps_warn.setVisible(shown)

    def _show_export_warn(self) -> None:
        """导出这一块的警告（export_warn，和窗口里其他警告同一个样子）：生成不了短字符串的几套、复制失败，各一行；
        都没有时藏起来。"""
        lines = [EXPORT_FAILED.format(n=self._export_failed)] if self._export_failed else []
        if self._copy_failed:
            lines.append(COPY_FAILED)
        self.export_warn.setText("\n".join(lines))
        self.export_warn.setVisible(bool(lines))

    def copy_to_clipboard(self) -> str:
        """把预览的内容放进剪贴板，写明复制了几套、什么格式。没有选中方案时什么都不做。返回复制的内容。

        写进以后读回来核对：剪贴板被别的程序占着、没写进去时不写「已复制」，在导出这一块的警告里写复制失败，返回
        空字符串。"""
        text = self._export_text
        if not text:
            return ""
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(text)
        self._copy_failed = clipboard.text().replace("\r\n", "\n") != text.replace("\r\n", "\n")
        self._show_export_warn()
        if self._copy_failed:
            self.copied.clear()
            return ""
        self.copied.setText(COPIED.format(n=self._export_count, format=FORMAT_NAME[self.format()]))
        return text

    # ---------------------------------------------------------------- 导入

    def _paste_changed(self) -> None:
        self.clear_btn.setEnabled(bool(self.paste.toPlainText() or self._done))
        self._parse_timer.start()

    def recognize(self) -> S.ParseResult:
        """马上识别粘贴框里的内容并显示结果（平时由粘贴框的内容变化触发，稍等 PARSE_DELAY_MS 后调用）。"""
        self._parse_timer.stop()
        text = self.paste.toPlainText()
        if text.strip():
            self._done = []
            self._result = S.parse_share(text, self.catalog)
        else:
            self._result = S.ParseResult()
        self._render_results()
        return self._result

    def _render_results(self) -> None:
        """识别结果区：粘贴框里有内容时写识别结果（一个富文本标签）；空着、刚导入过时一套一行写导入了哪几套，每行右边
        「使用这套方案」（done_box）。"""
        done = not self.paste.toPlainText().strip()
        if done:
            lines = [("ok", item.line()) for item in self._done]
        else:
            lines = result_lines(self._result, self.db)
        self.result_lines = lines
        self.results.setText("" if done else result_html(lines))
        self.results.setVisible(not done)
        self._fill_done(self._done if done else [])
        self.clear_btn.setEnabled(bool(self.paste.toPlainText() or self._done))
        self._update_import_button()
        self._fit_results()

    def _fill_done(self, items: list[_Imported]) -> None:
        """导入以后的那几行（done_box）：左边「已导入筹码方案「X」（芬妮）」（同识别结果「能导入的」那一种颜色，皮肤写了色块时
        也写在色块里），右边「使用这套方案」；点过的那一行按钮换成结果（_show_done）。items 为空时收起。每次按 _done 重建
        （导入、粘贴新内容、清空、换皮肤接回时）；只是结果变了时不重建（_show_done），免得正在点的按钮被换掉。"""
        while self.done_rows.count():
            item = self.done_rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()  # 可能正在处理其中某个按钮的点击，不能立即删
            free_item(item)
        self._done_widgets = []
        for record in items:
            row = QWidget()
            row.setObjectName("doneRow")
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(10)
            text = QLabel(result_html([("ok", record.line())]))
            text.setTextFormat(Qt.RichText)  # 内容经过 result_html 转义：方案名里的网页标记原样显示
            text.setTextInteractionFlags(Qt.NoTextInteraction)
            text.setWordWrap(True)
            text.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            line.addWidget(text, 1)
            button = self._use_button()
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, sid=record.scheme_id: self._use_clicked(sid))
            line.addWidget(button, 0, Qt.AlignVCenter)
            note = _label("", "useNote")  # 一行写完，左边的字放不下时由它折行
            line.addWidget(note, 0, Qt.AlignVCenter)
            self.done_rows.addWidget(row)
            self._done_widgets.append((button, note))
        self.done_box.setVisible(bool(items))
        self._show_done()

    def _show_done(self) -> None:
        """导入以后那几行右边：还没点的是「使用这套方案」（这套方案已经在别处删掉了时写 USE_GONE），点过的换成结果那一句。"""
        for record, (button, note) in zip(self._done, self._done_widgets):
            if not record.result and self.store.get(record.scheme_id) is None:
                record.result = "gone"
            text, name = record.outcome()
            button.setVisible(not text)
            note.setText(text)
            if text and note.objectName() != name:
                note.setObjectName(name)
                note.style().unpolish(note)
                note.style().polish(note)
            note.setVisible(bool(text))

    def _use_clicked(self, scheme_id: str) -> None:
        """「使用这套方案」：发 use_requested（所属角色, 方案 id），由悬浮窗设为这个角色用的方案，结果经 show_use_result 回来。"""
        record = next((r for r in self._done if r.scheme_id == scheme_id), None)
        if record is not None:
            self.use_requested.emit(record.hero, record.scheme_id)

    def show_use_result(self, scheme_id: str, result: str) -> None:
        """悬浮窗交回「使用这套方案」的结果（now 悬浮窗已改用、later 已设为那个角色用的方案、gone 这套方案已不在）：那一行
        的按钮换成结果那一句。不在刚导入的几行里时不管。"""
        for record in self._done:
            if record.scheme_id == scheme_id:
                record.result = result
        self._show_done()
        self._fit_results()

    def imported_rows(self) -> list[tuple[str, str]]:
        """导入以后那几行显示的内容（测试用）：（左边的字, 右边的结果；还没点、按钮还在时为空字符串）。"""
        return [(record.line(), record.outcome()[0]) for record in self._done]

    def _fit_results(self) -> None:
        """游戏内风格的几种排法：识别结果区按内容的高度（不超过卡片里剩下的地方，再多在区里滚动），下面的按钮紧跟着它，
        多出来的空白留在卡片最下面。默认的排法照旧由识别结果区占满剩下的地方。"""
        if self.layout_key == "default":
            return
        width = self.results_area.viewport().width()
        if width < 50:
            width = max(self.import_box.width() - 60, 300)
        lines = 0 if self.results.isHidden() else self.results.heightForWidth(width)
        if not self.done_box.isHidden():
            box = self.done_rows
            lines += box.totalHeightForWidth(width) if box.hasHeightForWidth() else self.done_box.sizeHint().height()
        self.results_area.setMaximumHeight(max(RESULTS_MIN_H, lines + 6))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_results()

    def _update_import_button(self) -> None:
        n = len(self._result.schemes)
        self.import_btn.setText(IMPORT.format(n=n))
        self.import_btn.setEnabled(n > 0 and not self.store.read_only)

    def import_schemes(self) -> list[S.Scheme]:
        """把识别出的、能导入的方案都新建为我的方案（不覆盖已有的，同名时名称后加「（2）」）。导入后清空粘贴框，
        一套一行写明导入了哪几套（每行右边「使用这套方案」；导入不自动换用），刷新方案列表，发出 schemes_imported。
        方案文件只读、没有能导入的方案时什么都不做。返回新建的方案。"""
        if self._parse_timer.isActive():
            self.recognize()
        schemes = self._result.schemes
        if not schemes or self.store.read_only:
            return []
        try:
            added = self.store.import_schemes(schemes)
        except (S.SchemesReadOnly, ValueError) as e:
            log.warning("导入筹码方案失败：%s", e)
            self.refresh()
            return []
        self._done = [_Imported(s.id, s.hero, s.name, shown_hero=names.hero_in(self.db, s.hero)) for s in added]
        self.paste.blockSignals(True)
        self.paste.clear()
        self.paste.blockSignals(False)
        self._result = S.ParseResult()
        self._render_results()
        self.refresh()
        self.schemes_imported.emit(list(dict.fromkeys(s.hero for s in added)))
        return added

    def clear_paste(self) -> None:
        """「清空」：清空粘贴框和识别结果（含刚导入的那几行）。"""
        self._done = []
        self.paste.blockSignals(True)
        self.paste.clear()
        self.paste.blockSignals(False)
        self.recognize()

    # ---------------------------------------------------------------- 按编号导入

    def fetch_by_ref(self) -> bool:
        """「按编号导入」（或在输入框里按回车）：认输入的编号或网址（hub_api.parse_ref），认出来以后在后台线程里从作业站取这一套
        （hub_api.fetch_scheme），取的时候输入框和按钮不能点，提示行写正在取几号；取回以后在界面线程里放进粘贴框、马上识别
        （_fetch_done）。认不出时不联网，在提示行写怎么输入。正在取、hub.ENABLED 为假时什么都不做。返回是否开始取。"""
        if not hub.ENABLED or self._fetch is not None:
            return False
        text = self.ref_edit.text()
        ref = hub_api.parse_ref(text)
        if ref is None:
            self._note("warn", FETCH_BAD_REF if text.strip() else FETCH_EMPTY)
            return False
        self._fetch_focus = self.focusWidget() in (self.ref_edit, self.fetch_btn)
        self._set_fetching(True)
        self._note("busy", FETCH_BUSY.format(n=ref[0]))
        call = self._fetch = HubCall(self, lambda stop: hub_api.fetch_scheme(ref, stop=stop), self._fetch_done,
                                     FETCH_LOG)
        call.start(self.run_background)
        return True

    def fetching(self) -> bool:
        """正在按编号取方案。"""
        return self._fetch is not None

    def _set_fetching(self, on: bool) -> None:
        self.ref_edit.setEnabled(not on)
        self.fetch_btn.setEnabled(not on)

    def _note(self, tone: str, text: str) -> None:
        """提示行写 text（tone：busy、ok、warn；空为收起）。句子换成两三行、窗口不够高时 Qt 自己加高窗口（_Note 按宽度算好
        高度），粘贴框、识别结果不被挤矮。"""
        self.fetch_note.show_text(tone, text)

    def _ref_edited(self, _text: str = "") -> None:
        """改了输入框里的字：上一次「无法识别」「取不到」的提示不再适用，收起（取回了哪一套的那一句照旧留着）。"""
        if self.fetch_note.tone == "warn":
            self.fetch_note.show_text("", "")

    def _fetch_done(self, share, error) -> None:
        """（界面线程）取方案的结果：成功时放进粘贴框（_put_paste）、马上识别，提示行写取回了哪一套；出错时提示行写原因
        （fetch_error_text；用户停下的不写）。不自动导入。"""
        self._fetch = None
        self._set_fetching(False)
        if self._fetch_focus:
            self._fetch_focus = False
            self.ref_edit.setFocus(Qt.OtherFocusReason)
        text = None if error is not None else (getattr(share, "short_text", None) or getattr(share, "text", None))
        if not text:
            self._note("warn", fetch_error_text(error) if error is not None else FETCH_ERROR)
            return
        added = self._put_paste(text)
        self.recognize()
        self._note("ok", fetch_done_text(share, added))
        self._reveal_timer.start()

    def _show_import_row(self) -> None:
        """卡片放在滚动区里的排法：取回一套以后，导入那一行不在看得见的地方时滚到它也露出来（提示行、粘贴框、识别结果都在
        它上面，跟着露出来），不用再往下滚才点得到导入。别的排法不用滚。"""
        scroll = self.findChild(QScrollArea, "cardsScroll")
        if scroll is None or not self.isVisible() or not scroll.isAncestorOf(self.import_btn):
            return
        for _ in range(3):  # 粘贴框、识别结果、提示行刚变：先把排版做完（一层层往外传），滚动区的范围和按钮的位置才是新的
            QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        scroll.ensureWidgetVisible(self.import_btn, 0, 12)

    def _put_paste(self, text: str) -> bool:
        """把取回的分享码放进粘贴框：粘贴框空着（只有空白也算）时整个换成它；已有内容时另起一行接在后面；同一段文字已经在
        里面时不再接。返回放没放进去。用光标插入：可以撤销（Ctrl+Z），粘贴框原来的撤销记录也不清掉。"""
        piece = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        current = self.paste.toPlainText()
        if not piece or piece in current:
            return False
        cursor = QTextCursor(self.paste.document())
        cursor.beginEditBlock()
        if current.strip():
            cursor.movePosition(QTextCursor.End)
            cursor.insertText(piece if current.endswith("\n") else "\n" + piece)
        else:
            cursor.select(QTextCursor.Document)
            cursor.insertText(piece)
        cursor.endEditBlock()
        self.paste.setTextCursor(cursor)
        return True

    def _cancel_fetch(self) -> None:
        """叫停正在取的那一套（关窗口时）：晚到的结果丢掉；输入框和按钮恢复能点，「正在取」那一句收起。"""
        call, self._fetch = self._fetch, None
        if call is None:
            return
        call.cancel()
        self._fetch_focus = False
        self._set_fetching(False)
        if self.fetch_note.tone == "busy":
            self.fetch_note.show_text("", "")

    # ---------------------------------------------------------------- 发到作业站、登录、退出

    def publish_selected(self) -> bool:
        """「发到作业站」：正好勾了一套才发（没勾、勾了两套以上时提示行写怎么做，不联网）。先在界面线程里生成一次这一套的短字符串
        （带地图，同发上去的；生成不了时写原因，不联网），取好这一套的快照；本机没登录（hub_api.login_nickname 为 None）时先开
        登录对话框，登录好了接着发，取消、没登录成功时停在那里。在后台线程里调 hub_api.publish_scheme，只用快照，不碰方案存储和
        界面；方案文件只读时照样能发（发方案不改方案文件）。账号上的事正在做、hub.ENABLED 为假时什么都不做。返回是否开始（登录
        或发）。"""
        if not hub.ENABLED or self.account_busy():
            return False
        chosen = self.selected_schemes()
        self._clear_post()
        if len(chosen) != 1:
            self._post_note("warn", POST_PICK_ONE)
            return False
        scheme = copy.deepcopy(chosen[0])
        try:
            S.to_short(scheme, self.catalog, maps=True)
        except ValueError as e:  # 筹码编号表读不出来、或缺角色、筹码、地图的编号
            log.warning("方案「%s」（%s）无法生成短字符串，没有发到作业站：%s", scheme.name, scheme.hero, e)
            self._post_note("warn", POST_NO_SHORT)
            return False
        if hub_api.login_nickname() is None:
            self._pending = scheme
            self._open_login()
            return True
        self._send(scheme, None)
        return True

    def account_busy(self) -> bool:
        """账号上的事（登录、发、更新、退出）正在做：同一时间只做一件，做的时候「发到作业站」「退出作业站」不能点。"""
        return self._login is not None or self._post is not None or self._logout_call is not None

    def _update_account_buttons(self) -> None:
        busy = self.account_busy()
        for btn in (self.post_btn, self.logout_btn, self.dup_update_btn, self.dup_skip_btn):
            btn.setEnabled(not busy)

    def _send(self, scheme: S.Scheme, token: str | None, number: int | None = None) -> None:
        """在后台线程里把快照 scheme 新发上去（number 为 None），或更新作业站上的 number 号（hub_api.update_scheme）。token 为
        None 时用本机存的登录信息；刚登录好、登录信息没能存进本机时是刚拿到的那一份，只交给这一次（和 409 以后的更新）。"""
        catalog = self.catalog
        if number is None:
            name = shown_name(lang.t(scheme.name))  # 方案名显示时按界面语言转，发上去的照原文
            self._post_note("busy", POST_BUSY.format(name=name) if name else POST_BUSY_UNNAMED)
            work = lambda stop: hub_api.publish_scheme(scheme, catalog, token=token, stop=stop)  # noqa: E731
        else:
            self._post_note("busy", UPDATE_BUSY.format(n=number))
            work = lambda stop: hub_api.update_scheme(number, scheme, catalog, token=token, stop=stop)  # noqa: E731
        call = self._post = HubCall(self, work, lambda result, error: self._post_done(result, error, scheme, token,
                                                                                         number), POST_LOG)
        self._update_account_buttons()
        call.start(self.run_background)

    def _post_done(self, result, error, scheme: S.Scheme, token: str | None, number: int | None) -> None:
        """（界面线程）发、更新的结果：发好了按作业站回的状态写一句、显示「在作业站上看」；作业站上已有一样的一套（duplicate）时
        问要不要用这一套更新它（「更新那一套」「不更新」，「在作业站上看」看已有的那一套）；登录失效（unauthorized：本机的登录
        信息已删掉）时「已登录」那一行跟着改；别的出错写原因（post_error_text）。"""
        self._post = None
        self._update_account_buttons()
        extra = LOGIN_NOT_SAVED if self._login_unsaved else ""
        self._login_unsaved = False
        if error is None and isinstance(result, hub_api.PublishedScheme):
            self._show_post("ok", post_done_text(result, number is not None), extra, view=result.url)
            return
        kind = _kind(error)
        if kind == hub_api.DUPLICATE and isinstance(error.scheme_id, int):
            self._dup = (error.scheme_id, scheme, token)
            self._show_post("ask", POST_DUPLICATE.format(n=error.scheme_id), extra, view=error.url, ask=True)
            return
        if kind == hub_api.UNAUTHORIZED:
            self.refresh_account()
        self._show_post("warn", post_error_text(error, number is not None) if error is not None else POST_ERROR, extra)

    def _show_post(self, tone: str, text: str, extra: str = "", view: str | None = None, ask: bool = False) -> None:
        """「发到作业站」下面的提示行写 text（tone：busy、ok、ask、warn；空为收起），extra 另起一行接在后面；view 是作业站的
        地址时显示「在作业站上看」；ask 时显示「更新那一套」「不更新」。"""
        if extra:
            tone, text = (tone, text + "\n" + extra) if text else ("warn", extra)
        self.post_note.show_text(tone, text)
        self._view_url = view if text and hub_url(view) else ""
        self.view_btn.setVisible(bool(self._view_url))
        self.dup_update_btn.setVisible(ask)
        self.dup_skip_btn.setVisible(ask)
        self.post_actions.setVisible(bool(self._view_url) or ask)
        if text:
            self._post_timer.start()

    def _show_post_row(self) -> None:
        """卡片或导出那一列放在滚动区里的排法（stacked、banded、capsules 的 cardsScroll，side_by_side、split 的
        exportScroll）：提示行露出来以后，它和下面的小按钮不在看得见的地方时滚到它们也露出来。别的排法不用滚。"""
        note = self.post_note
        if not self.isVisible() or not note.isVisibleTo(self):
            return
        for name in ("exportScroll", "cardsScroll"):
            scroll = self.findChild(QScrollArea, name)
            if scroll is not None and scroll.isAncestorOf(note):
                for _ in range(3):  # 提示行刚变：先把排版做完，滚动区的范围和提示行的位置才是新的
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
                if self.post_actions.isVisibleTo(self):
                    scroll.ensureWidgetVisible(self.post_actions, 0, 12)
                scroll.ensureWidgetVisible(note, 0, 12)  # 两样放不下时先顾提示行
                return

    def _post_note(self, tone: str, text: str) -> None:
        self._show_post(tone, text)

    def _clear_post(self) -> None:
        """收起「发到作业站」下面的提示行和小按钮，作业站上已有一样的一套时等着答的那一问作废。"""
        self._dup = None
        self._show_post("", "")

    def _dup_update(self) -> None:
        """「更新那一套」：在后台线程里用这一套的快照更新作业站上已有的那一套（hub_api.update_scheme）。"""
        if self._dup is None or self.account_busy():
            return
        number, scheme, token = self._dup
        self._clear_post()
        self._send(scheme, token, number)

    def _dup_skip(self) -> None:
        """「不更新」：收起那一问，不联网。"""
        self._clear_post()

    def _view_on_hub(self) -> None:
        """「在作业站上看」：在浏览器里打开作业站上的那一套（只开作业站的 https 地址）。"""
        if hub_url(self._view_url):
            self.open_url(self._view_url)

    def _open_login(self) -> None:
        """开登录对话框（HubLogin）：登录好了（logged_in）接着发等着的那一套，没登录成功就关了（ended）时停在那里。"""
        dialog = self._login = HubLogin(self, self._kit)
        dialog.logged_in.connect(self._logged_in)
        dialog.ended.connect(self._login_ended)
        self._update_account_buttons()
        dialog.show()
        dialog.start()

    def _logged_in(self, login) -> None:
        """（界面线程）登录对话框拿到了登录信息：存进本机（hub_api.store_login），「已登录」那一行跟着改，接着发等着的那一套。
        存不进本机时这一次照样用它发（只交给这一次的后台调用，不另外留着），结果后面另写一句下次还要重新登录。"""
        self._login = None
        scheme, self._pending = self._pending, None
        stored = hub_api.store_login(login)
        token = None if stored else login.token
        self._login_unsaved = not stored
        self.refresh_account()
        self._update_account_buttons()
        if scheme is not None:
            self._send(scheme, token)

    def _login_ended(self) -> None:
        """登录对话框关了、没登录成功（取消、出错以后关掉）：不发，提示行不写。"""
        self._login = None
        self._pending = None
        self._update_account_buttons()

    def logout(self) -> bool:
        """「退出作业站」：在后台线程里调 hub_api.logout（让作业站撤销这份登录信息，再删掉本机存的）；做完以后再读一次本机的登录
        状态定「已登录」那一行显示什么，本机还读得出（没删掉）时写没能退出。账号上的事正在做、hub.ENABLED 为假时什么都不做。
        返回是否开始。"""
        if not hub.ENABLED or self.account_busy():
            return False
        self._clear_post()
        self._post_note("busy", LOGOUT_BUSY)
        call = self._logout_call = HubCall(self, lambda stop: hub_api.logout(stop=stop), self._logout_done, LOGOUT_LOG)
        self._update_account_buttons()
        call.start(self.run_background)
        return True

    def _logout_done(self, _result, _error) -> None:
        """（界面线程）退出做完了：不按「退出了」去猜，再读一次本机的登录状态。"""
        self._logout_call = None
        self._update_account_buttons()
        if self.refresh_account():
            self._post_note("warn", LOGOUT_FAILED)
        else:
            self._post_note("ok", LOGOUT_DONE)

    def refresh_account(self) -> bool:
        """按本机存的登录信息（hub_api.login_nickname：只读本机，不联网）显示或藏起「已登录作业站」那一行；返回本机是否登录着。
        hub.ENABLED 为假时不读，这一行不显示。窗口显示、重新激活、登录以后、退出以后、登录失效以后各读一次。"""
        if not hub.ENABLED:
            self.account_row.hide()
            return False
        text = nickname_text(hub_api.login_nickname())
        self.account_label.setText(text or "")
        self.account_row.setVisible(text is not None)
        return text is not None

    def _cancel_account(self) -> None:
        """叫停账号上正在做的事（关窗口时）：登录对话框跟着关；正在发、更新的那一次叫停，晚到的结果丢掉；正在退出的那一次不叫停，
        只丢掉结果（hub_api.logout 照样让作业站撤销这份登录信息、删掉本机存的那份）；等着登录以后发的那一套、作业站上已有
        一样的一套时等着答的那一问作废；按钮恢复能点，「正在……」和那一问收起。"""
        dialog, self._login = self._login, None
        self._pending = None
        if dialog is not None and isValid(dialog):
            dialog.cancel()
        post, logout_call = self._post, self._logout_call
        self._post = self._logout_call = None
        if post is not None:
            post.cancel()
        if logout_call is not None:
            logout_call.drop()
        self._login_unsaved = False
        if self.post_note.tone in ("busy", "ask"):
            self._clear_post()
        self._dup = None
        self._update_account_buttons()

    # ---------------------------------------------------------------- 窗口

    def _available_height(self) -> int | None:
        area = self.available_area()
        return area.height() if area is not None else None

    def fit_screen(self) -> None:
        """屏幕放不下时按可用高度缩小（方案列表和识别结果区随之变短，其余部分不被裁掉）。"""
        avail = self._available_height()
        if avail is None:
            return
        limit = max(self.minimumSizeHint().height(), avail - L.SCREEN_MARGIN)
        if self.height() > limit:
            self.resize(self.width(), limit)

    def showEvent(self, event) -> None:
        self.refresh()
        self.refresh_account()
        self.fit_screen()
        super().showEvent(event)

    def changeEvent(self, event) -> None:
        # 重新激活时（例如在编辑窗口改过方案后切回来）按方案文件的最新内容刷新，「已登录作业站」那一行按本机现在的登录状态
        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self.refresh()
            self.refresh_account()
        super().changeEvent(event)

    def closeEvent(self, event) -> None:
        # 关窗口时叫停正在取的方案和账号上正在做的事（登录对话框跟着关）：晚到的结果丢掉，不碰关掉的窗口（窗口关了只是藏起来，
        # 下次打开还是它）。只在这里叫停，不在藏起来时：换外框时 Qt 也先藏再显示
        self._cancel_fetch()
        self._cancel_account()
        super().closeEvent(event)

    def state(self) -> dict:
        """交出的状态：粘贴框内容、勾选的方案、角色筛选、格式、刚导入的几行、当前页签、按编号导入的输入框和提示行（正在取的
        时候不交出提示行：重开的窗口不接着取），加上窗口的位置。换皮肤重开窗口时靠它保住输入和勾选。账号上的事（登录、发、
        更新、退出）和「发到作业站」下面的提示行不交出：重开的窗口不接着做，「已登录」那一行显示时按本机重读。"""
        s = super().state()
        s.update(paste=self.paste.toPlainText(), checked=sorted(self._checked), filter=self._filter, format=self.format(),
                 done=[dataclasses.asdict(item) for item in self._done],
                 tab=next((name for name, tab in getattr(self, "tabs", {}).items() if tab.isChecked()), None),
                 hub_ref=self.ref_edit.text(), hub_note=None if self._fetch is not None else self.fetch_note.state())
        return s

    def restore(self, state) -> None:
        """接回 state() 交出的状态：换回角色筛选和勾选、粘贴框内容、格式、刚导入的几行、页签、按编号导入的输入框和提示行，
        窗口位置按屏幕挪回来。原来的排法没有页签时，粘贴框有内容或刚导入过就停在「导入」页。勾选的方案在换皮肤期间被删掉时
        不再勾选（refresh 里按现有方案取交集），不报错。"""
        if not state:
            return
        super().restore(state)
        self._filter = state.get("filter")
        self._checked = set(state.get("checked") or ())
        self.refresh()
        fmt = state.get("format")
        if fmt and self.format() != fmt:
            self.set_format(fmt)
        self.paste.blockSignals(True)
        self.paste.setPlainText(state.get("paste") or "")
        self.paste.blockSignals(False)
        self._done = [_Imported(**item) for item in state.get("done") or () if isinstance(item, dict)]
        for item in self._done:
            item.shown_hero = item.shown_hero or names.hero_in(self.db, item.hero)
        ref = state.get("hub_ref")
        self.ref_edit.setText(ref if isinstance(ref, str) else "")
        note = state.get("hub_note")
        # 只接回取完以后的那一句（取回了哪一套、出错的原因）：重开的窗口没有在取，「正在取」不接回
        if (hub.ENABLED and isinstance(note, (tuple, list)) and len(note) == 2 and note[0] in ("ok", "warn")
                and isinstance(note[1], str)):
            self._note(*note)
        self.recognize()
        tabs = getattr(self, "tabs", {})
        tab = state.get("tab")
        if tab is None and (self.paste.toPlainText().strip() or self._done):
            # 从没有页签的排法换过来：粘贴框里有内容或刚导入过，说明正在导入，停在「导入」页（不然开在第一页「导出」）
            tab = TAB_IMPORT
        if tab in tabs:
            tabs[tab].setChecked(True)

    def keep_on_screen(self) -> None:
        """接回窗口位置时用：先按可用高度缩小，再挪进可用区域。"""
        self.fit_screen()
        super().keep_on_screen()


# 界面是繁体时把上面的文字常量转成繁体。作业站出错时错误记录的标题（*_LOG）只写进错误记录，照旧简体
HANT_KEEP = ("FETCH_LOG", "POST_LOG", "LOGOUT_LOG", "LOGIN_LOG")
lang.localize(globals())
