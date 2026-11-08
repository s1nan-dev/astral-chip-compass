"""把一帧游戏画面交给各个识别模块，再交给 Session 记账。悬浮窗和回放工具共用这一套。

三选一画面出来要尽快给推荐（见 FrameProcessor.process）：手上的轮次、进度条够新时先给推荐、下一帧再读一次
核对，推荐给出以后只盯绿框（刚出现时读得可疑的补认一次、读左上角没成功时隔一会儿再读几次，见 FrameProcessor._select_reads）；
刚出现时读得不完整、还没给过推荐的，先完整重读三张卡，读左上角排到重读之后（见 FrameProcessor._read_select）。
不在三选一时一帧最多做一项读数，其余到期的排到接下来的几帧，每一帧开头先看是不是三选一。
左上角的轮次数字被玩家列表第一行的头像挡住（读轮次时在数字那一行看见了轮次标记、数字却读不出）时，当作还在对局里：不判断
选角界面，隔一会儿只在标记左边那一小块便宜地认一次字，隔久一点才完整读一次；三选一刚出现时手上的轮次只是因为被挡住才旧的，
照样先给推荐（见 FrameProcessor._idle_reads、_peek_round、_readings_fresh）。两个客户端同时识别时，一路的三选一显示着，另一路
暂停平时要做文字识别的读数，把识别引擎让给它（见 SelectBoard）。
三选一有筹码没读出来的那一帧（刷新动画的中间帧除外）在内存里留最近的一张，导出诊断时另存（FrameProcessor.unread_shot）。
三选一显示着时另一个线程快速盯绿框（vision/pick_watch.py）：玩家从点选到点「确定」常不到 1 秒，读左上角、做文字识别的一帧
可能比这还长。识别流程每帧放上要盯的、取走它看到的选中项，按截到画面的先后排在这一帧的读数之前或之后、在同一把锁里交给
记账（FrameProcessor._watch_frame）。给过推荐以后同一次三选一里，完整识别读到的名字突然变了一两张（至少一张没变）、变了的都
读不准而且稀有度读不出（像是被弹出的小窗、动画临时盖住）时，这一帧不采用（FrameProcessor._covered）。记下选中了哪张用的
那一帧、不采用的那一帧各在内存里留最近一张（pick_shot、covered_shot），导出诊断时另存。对局中途看见像一局打完时的
颜色、这一局却接着打下去时，那一帧转正以后也留一张（finish_shot），导出诊断时另存。
回放截图的工具用 FrameProcessor.settle（一张截图上到期的读数一次做完）。

每帧先去掉画面四周的纯黑边（游戏按固定比例渲染、窗口比例不同时会有上下或左右黑边），各识别模块只看中间的游戏画面。
窗口过小、独占全屏、游戏语言这几种不支持的情况记进 Session.unsupported，悬浮窗显示在状态行。
读不到轮次时认对局外的画面（结算画面、「匹配成功」画面、房间、奖励画面、主菜单，见 screens.py）：选角界面判断那次的下方区域
认得出结算画面的底栏、主菜单和 16:9 附近的组队房间，其余靠离开对局后的整幅识别（_check_loading）。读数交给
Session.on_out_of_game：结束这一局，房间和「匹配成功」画面上的难度、地图留给下一局（[map_detect] out_of_game 关掉时不认这几种
画面）。不在对局里时（还没有对局状态、本局已结束）另外约每秒看一眼下方区域（[watch] select_ms，见 FrameProcessor._idle_reads）：
选角界面只显示几秒，每 hud_ms 才顺带看一次的话常常落在两次之间。
停在主菜单时（不在对局里、最近认出的对局外画面是主菜单）不做文字识别，只看底栏和开始按钮那两块固定的界面变没变（主菜单空转，
见 FrameProcessor._menu_idle）：主菜单的背景动画、左下角轮播的横幅不算；这两块一变就先看一眼下方区域、接着整幅识别，再照常
识别，空转满 MENU_IDLE_MS 也照常认一次。两块里有一块自己会变、空转来回进出时（见 MENU_FLAPS），只是开始按钮那一块的这次
停在主菜单期间只看底栏，底栏也在变的暂停空转一阵：最坏和不空转时一样。
对局中少找字（读出来的数不变）：三选一完整读认出以后记下位置，之后同样大小的画面上先只裁那几小块认字（快读，对不上就当场
完整读，见 FrameProcessor._select_read）；读轮次先在上次完整读记下的标记左边那一小块只认字，认出手上的轮次或往前一轮才直接用
（_round_quick）；不在三选一时进度条那一块和上次认字时一样就沿用上次的读数（_read_progress）。
本局难度的合并规则见 advisor/session/__init__.py 开头的说明。
"""
from __future__ import annotations

import copy
import dataclasses
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime

import cv2
import numpy as np

from .. import perfstats
from ..data import DB
from ..errors import log_error
from ..pool import difficulty_of
from ..session import ROUND_STEP, Event, Session, WatchHints, compute_outside, split_extra
from .anchors import OCR_TOP, SELECT_REGION, VIEW_CHIPS, VIEW_CHIPS_BOX
from . import templates
from .chip_select import (hidden_ui_visible, looks_like_overlay_screen, name_strip, read_chip_select,
                          read_chip_select_fast, reread_selected, select_layout, ui_scale)
from .coins import read_own_coins
from .frame import Frame, as_frame
from .hero import read_hero, save_template
from .hud import (Hud, is_select_screen, mutator_catalog, progress_look, read_hud, read_pregame, read_progress,
                  read_round_crop, same_progress_look, task_panel_visible)
from .pick_watch import PickWatch
from .screens import MAIN_MENU
from .stars import MissingTemplates, name_key, read_own_stars, read_own_stars_by_name, read_players
from . import victory

try:  # 「查看筹码」面板识别（另一个模块，缺了不影响其余识别）
    from .chip_panel import read_chip_panel
except Exception:  # noqa: BLE001
    # 导入失败（发行版缺了文件等）：面板识别不可用，其余照常。原因写进错误记录，不然这一项无声无息地没了；模块只导入一次，
    # 所以只记这一次
    read_chip_panel = None
    log_error("「查看筹码」面板的识别模块导入失败，读不了面板上的已有筹码", traceback.format_exc(), append=True)

SELECT_STAR_TRIES = 3  # 每次三选一最多读几帧自己的星数（自己那一行被挡住时读不到，不必一直试）
# 多久读一次自己的星币数（[watch] coins_ms 可改）。和读星数同一次读（读星数按 hud_ms，所以默认实际约 6 秒一次）；
# 每次墙钟约 14 毫秒、CPU 约 44 毫秒（只识别数字那一小块，不做文字检测）；三选一期间不读。
# 头像认不出自己那一行时，也是在这一拍按本局记住的名字找那一行（_own_by_name，多一次名字的文字识别）
COINS_MS = 5000
# 三选一读得不完整（见 _incomplete）时，隔这么久就再完整识别一次。读得完整时一直沿用，三个筹码名那一条变了才再认。
# 界面刚出现时概率行可能读错、图标还没加载出来，半秒后多半已显示完整。要读左上角（核对等）的时候：这次三选一刚出现、还没
# 给过推荐（WatchHints.select_fresh）时先完整重读三张卡，读左上角排到重读之后（还没给出推荐，没有要核对的；先把三张卡读对，
# 推荐能早出来）；给过推荐以后照旧先读左上角，再认挪到下一帧，和补认一样。两样都不在同一帧做（见 FrameProcessor._read_select、
# _top_due）
SELECT_RETRY_S = 0.5
# 补认（见 FrameProcessor._suspicious）：三选一刚出现时读得可疑（刷新次数读成 0、概率行不是概率表里的行；这两样
# 不算读得不完整，不会按 SELECT_RETRY_S 再认）时，隔这么久再完整识别一次三张卡（[watch] select_reread_ms 可改，低配模式乘倍数）。
# 刷新按钮被表情气泡挡住时多半半秒内就露出来。刚出现那一帧读全了就按它判，没读全（出场动画里概率行还读不到等）时按这次三选一
# 第一次读全的那一次判；每次三选一只判这一次、最多补认一次，读得正常的不多认。到点的那一帧还要读左上角（核对等）时挪到下一帧，
# 一帧里不做两样大的识别（见 FrameProcessor._top_due）
SELECT_REREAD_MS = 500
# 三选一开着时读左上角（轮次连同进度条：先读完再给推荐、核对、补读跳变的轮次、确认变小的轮次）出错、读不出轮次时，隔这么久再读
# 一次（见 FrameProcessor._select_hud；[watch] hud_retry_ms 可改，低配模式乘倍数），每次三选一最多再读
# HUD_RETRIES 次：左上角只是被挡了一下的，三选一开着时就能核对、确认新的一局；一直出错也只多读这几次，不会每一帧都读
HUD_RETRY_MS = 1000
HUD_RETRIES = 3
# 轮次被挡住（读轮次时在数字那一行看见了轮次标记、数字却读不出，见 FrameProcessor._occluded）时，隔这么久在上次标记左边那
# 一小块再认一次字（hud.read_round_crop：只认字、不找字，几十毫秒；[watch] occluded_recheck_ms 可改，低配模式乘倍数）。挡住
# 数字的是玩家列表第一行的头像，头像一直在晃，数字时隐时现：看得勤一点，露出来的那一下就读到了。平时的取帧间隔（idle_ms）也是
# 半秒，所以大约每一帧看一眼；这一眼不设 pending，不会让识别线程不等就接着取帧。三选一上读不出轮次时的再读也按这个间隔
OCCLUDED_RECHECK_MS = 500
# 轮次被挡住期间，隔这么久才完整读一次轮次（重新找标记、看是不是离开了对局，[watch] occluded_full_ms 可改，低配模式乘倍数）。
# 完整读一次要找字（慢电脑上约 0.5 秒），被挡住时多半照样读不出。离开了对局（标记不见了）最晚这么久以后才发现：结算画面一般
# 停得比这久，照常认得出
OCCLUDED_FULL_MS = 6000
# 这一局正打着、刚看见过轮次（FrameProcessor._mid_game）时完整读轮次、连标记都没看见（战斗、对决、过场这类画面上连着读不到），
# 隔这么久才再完整读一次（[watch] blank_full_ms 可改，低配模式乘倍数，见 FrameProcessor._full_blank_at）。这类画面上每 hud_ms
# 完整读一次（慢电脑上约 0.4 秒）多半白读。只认字照旧每 hud_ms 一次（_round_quick）：这一局完整读看见过标记、离那次不到
# after_game_ms 时，回到棋盘只认字就认出；只认字用不上时，回到棋盘最晚晚这么久才完整读到（原来最晚晚 hud_ms）。新一局还没
# 读到轮次、对局外、离上次看见标记已满 round_retry_gap_ms 时不等，照旧每 hud_ms 完整读。
BLANK_FULL_MS = 6000
# 这一局近这么久（毫秒）内看见过轮次标记（读到轮次、只看见标记、只认字那一小块认出都算）、手上有轮次、本局没结束时，不在
# 三选一上的完整读第一遍读不出就不再放大、缩小各多认一遍，连标记都没看见以后也隔 BLANK_FULL_MS 才再完整读（[watch]
# round_retry_gap_ms 可改，两样共用这一个条件，见 FrameProcessor._mid_game）：对决、过场画面的左上角落进别的白色大数字时
# 三遍都白读。只是「多久算最近」，不是读的间隔，低配模式不乘倍数。
ROUND_RETRY_GAP_MS = 60000
# 地图未知、任务栏没开（hud.task_panel_visible）时，读轮次隔这么久才连带读一次画面上方整块（[watch] map_block_ms 可改，低配
# 模式乘倍数）。任务栏开着时照旧每次都连带读。任务栏没开时这一块能读到的：地图标记（「线索」这类，只凭它下不了结论，见
# hud._guess_map），按颜色认不出来的任务列表（展开在画面右边的那种，底色和任务栏不同，task_panel_visible 认不出），任务完成时
# 画面中间停几秒的横幅（后两种各读得到一条任务，加上地图标记就能下结论）。后两种最晚晚这么久才读到，停的时间比这个间隔短的
# （横幅）可能错过；读到过一次任务以后，读到没有为止每次都连带读（见 _block_due）。改成和 hud_ms 一样就是每次都连带读
MAP_BLOCK_MS = 6000
# 两个客户端同时识别时，另一路「三选一正显示着」的记号多久没更新就不再算数（见 SelectBoard）：另一路的识别线程停了、卡在一次
# 很慢的取帧里时，自己不会一直让路。三选一显示着时那一路每一帧都更新（慢电脑上最长的一帧约 2～3 秒）
BOARD_STALE_S = 5.0
# 连着多久看不到对局里的读数（[map_detect] quiet_ms，见 FrameProcessor._count_quiet）只算看着画面的时间：相邻两帧之间最多算
# 这么多秒。暂停（游戏切到后台、最小化）、手动模式平时不看画面、截不到画面时没有帧，之后的第一帧只多算这么多；平时两帧之间
# 隔 idle_ms（低配模式乘倍数）再加一帧识别的耗时（慢电脑上 2～3 秒），都在这以内
QUIET_STEP_S = 5.0
# 不在对局里时（还没有对局状态、本局已结束）多久看一眼下方区域，认选角界面和对局外的画面（[watch] select_ms 可改，低配模式乘
# 倍数，见 FrameProcessor._idle_reads）。选角界面只显示三四秒，平时只在每 hud_ms 读不到轮次时顺带看，常常落在两次之间，
# 没看到它，这一局就不知道是从开局看起的。画面和上次认定「不是」时一样就不认字（_not_pick 的指纹，最多隔 unchanged_max_ms
# 重认一次）：大厅停着不动时认字的次数和原来差不多，下方区域一直在变时最多约每秒认一次
SELECT_MS = 1000
# 本局进行中、读不到轮次、标记也没有（战斗、弹窗、换了画面）时，看见一次轮次标记（读到轮次、或数字被挡住只看见标记，
# FrameProcessor._mark_seen_at）以后第一次读不到照判是不是选角界面，第二次起离那次标记不到这么久就先不判（不认画面下方，
# 照旧排读星数；[watch] pick_grace_ms 可改，低配模式乘倍数，见 FrameProcessor._pick_grace）。按热键开始的那一轮、刚从暂停
# 恢复还没读到轮次时照判；看见像通关画面、打输横幅的颜色以后这么久以内照判（结算画面紧跟着出来）。一局实机记录的对局中两分多钟
# 里，读不到轮次的几段多是 10～13 秒（战斗），另有一段 19 秒、一段 38 秒以上：每 hud_ms 读不到一次就认一次下方，占对局中
# 文字识别的三成多；这样先不判，判了的 26 次里省掉 9 次（约三分之一；15 秒 11 次、20 秒 14 次，6 秒一次也省不掉）。打完一局、
# 中途退回主菜单和房间时，读不到的第一眼多半就落在结算画面、主菜单、房间上，认得和原来一样快。代价：第一眼落在别的画面上
# （例如按 Esc 停在菜单上几秒再退出）时最多晚这么久才认出、结束这一局；这时退回去马上又开一局、这么久以内就到了选角界面
# （只显示三四秒）可能漏看，进对局以后要请玩家打开「查看筹码」面板
PICK_GRACE_MS = 10000
# 停在主菜单时（主菜单空转，见 FrameProcessor._menu_idle）凭这几块固定的界面（x0, y0, x1, y1，占窗口的比例）认「还在主菜单上」：
# 底下那一条（头像、货币、任务到设定那一排按钮）和右下角「开始游戏」那一块。整块下方区域（SELECT_REGION）不行：主菜单的背景
# （立绘上的光效、粒子）一直在变，左下角的活动横幅会轮播。同一客户端不同时刻的三张主菜单截图（去掉黑边以后）两两比，整块下方
# 区域的指纹差 0.020～0.023，其中左下那一块（横幅和背景）差 0.041～0.049，已经超过 FP_SAME；底下那一条只差 0～0.0023（货币数
# 变了也在内），开始按钮那一块 0.005～0.010。samples 里别的画面（房间、匹配中、「匹配成功」画面、选角界面、加载画面、对局、
# 战斗、三选一、「查看筹码」面板、结算画面、奖励画面等）和这三张主菜单比，底下那一条至少差 0.17、开始按钮那一块至少差 0.13，
# 两块里差得多的那块至少差 0.19：两块都按 FP_SAME 比，两边都留足了余量。先底栏、后开始按钮（FrameProcessor._menu_idle 按这个
# 次序认哪一块变了）
MENU_REGIONS = ((0.0, 0.90, 1.0, 1.0), (0.70, 0.62, 1.0, 0.90))
# 主菜单空转满这么久，照常认一次下方区域（同不在对局里时看的那一眼），还是主菜单就接着空转：防的是指纹意外对上、画面其实已经
# 离开了主菜单。低配模式不乘倍数：一分钟认一次本来就很少，乘了只是让意外对上时晚一倍才发现
MENU_IDLE_MS = 60000
# 主菜单空转防「来回进出」（见 FrameProcessor._menu_trip）：固定界面那两块里有一块自己会变（开始按钮附近有动画等）时，空转一开始
# 就因为它变了不再空转，看一眼下方区域、整幅识别又认出主菜单，下一帧对上了又空转，一来一回比不空转时认的字还多。因为这两块变了
# 不再空转、之后没认出别的画面、MENU_TRIP_MS 以内又认回主菜单重新空转，算一次来回；MENU_FLAP_MS 以内满 MENU_FLAPS 次时：每次
# 都只是开始按钮那一块变了的，这次停在主菜单期间（认出别的画面、窗口大小变了、从暂停恢复为止）只拿底栏判断；底栏也变过的，
# MENU_FLAP_MS 以内不再空转（和不空转时一样读），过了再试，最坏和不空转时一样。单靠底栏分得开：底栏在主菜单之间只差 0～0.0023，
# 和离开主菜单以后的画面（匹配中、「匹配成功」画面、房间、选角界面等）至少差 0.17；开始匹配、进房间都是整屏换画面，底栏照样会变。
# MENU_TRIP_MS：从不再空转到重新空转，平时是紧接着的一两帧（不到 1 秒）；慢电脑开着低配模式时要先看一眼、再整幅识别，再隔一个
# 取帧间隔才对上，所以留宽。宽了的代价只是别的进出（弹窗开了又关等）也可能算成来回，判过以后照样认得出离开主菜单
MENU_TRIP_MS = 10000
MENU_FLAP_MS = 60000
MENU_FLAPS = 3
# 两开时另一路三选一显示着时让路的几项（都要做文字识别，和三选一那一路排队用同一个识别引擎）：读轮次和便宜的那一眼、选角界面
# 判断（连同不在对局里时看的那一眼）、读队友、存立绘模板、读进度条、离开对局后的整幅识别。认角色、读星数是模板比对，照旧做
# （读星数那一项不连带读星币、不按名字找自己）
OCR_STEPS = frozenset({"round", "peek", "pick", "glance", "teammates", "template", "progress", "loading"})
# settle 用同一幅画面最多接着处理几帧（一轮读数最多七项，再加离开对局后的整幅识别；改坏了也不会一直转下去）
SETTLE_MAX = 20

# 画面没变就不重复做文字识别（见 _Unchanged）。指纹：判定区域缩成这么大的灰度图
FP_SIZE = (32, 12)
# 两个指纹的平均灰度差（0~1）不超过这个值算画面没变。用 samples 里的 4K 截图量：
#   战斗画面和三选一相差 0.18，下方区域和选角界面相差 0.24；同一个三选一重选前后 0.025。
#   模拟画面里的动作：战斗画面中间一块（角色）左右平移画面宽度的 3% 相差 0.02、8% 相差 0.037，
#   整个画面上下平移 1% 相差 0.024。
FP_SAME = 0.04
# 画面一直没变时，最多隔这么久重新识别一次，以防万一（[watch] unchanged_max_ms 可改）
UNCHANGED_MAX_MS = 10000

MATES_EAGER = 6  # 每局前几次读队友按 hud_ms，之后按 hero_ms
# 「查看筹码」面板刚看不到的这么多秒以内照样每帧都看（平时按 hud_ms 看）：翻页动画那几帧格子错位、不算面板，翻过去的那一页
# 不用再等一个 hud_ms 才读到。面板关了以后多看的这几帧，不是面板时每帧约 10 毫秒
PANEL_GRACE_S = 2.0
MIN_CLIENT_WIDTH = 1000  # 16:9 窗口的客户区宽度低于这个值时提示「窗口过小」
MIN_FRAME = 16  # 画面任一边小于这么多像素时只报「窗口过小」，其余识别对着空画面没有意义（游戏窗口不会这么小）
MIN_UI_SCALE = MIN_CLIENT_WIDTH / 1920  # 换成界面缩放比例（宽 / 1920、高 / 1080 中较小的）：比 16:9 宽的窗口按高度算
FULLSCREEN_CHECK_S = 5.0  # 多久查一次独占全屏
BORDER_DARK = 12  # 四周黑边：整行（列）的最大亮度不超过这个值
# 上一帧的黑边范围（trim_borders 的 keep）里新多出来的那一条，比 BORDER_DARK 亮的像素占不到这么多时，这一边照上一帧去：
# 16:10 屏幕上游戏在黑边里断续画了一面粉色小旗，一个亮点不该让整条黑边不去。实机截图缩到 1280 宽时，小旗那一条约
# 0.0097～0.0142，最宽的一行约 0.017（小旗按界面缩放画，占画面宽的比例不随窗口大小变）。真内容占得多：samples 里的截图
# 去掉黑边以后，靠上、下、左、右边、占这一边 1% 以上的一条最少约 0.12（只有 0.5% 宽的几行最少约 0.022，差几行不碍事）。
# 取两者之间的 0.04：小旗再宽一倍也照旧去；黑边里真画满了东西（整屏过场等）占得多，照新算的。
BORDER_SPECK = 0.04
BORDER_MIN = 0.01  # 黑边至少占这一边的这么多才去掉
BORDER_KEEP = 0.5  # 去掉黑边后至少还剩这么多，否则不去（整幅画面都暗时不当成黑边）
# OpenCV 用几个线程（cv2.setNumThreads，全进程的设置；悬浮窗在识别线程开头调用 limit_cv_threads 设一次）。
# OpenCV 默认按全部逻辑核分线程（Windows 版用系统的并行库）：缩小、转灰度这类整幅的活会分线程，多线程墙钟只快几毫秒
# （最多的是文字识别前把 4K 画面上方缩到 1920 宽那一次：16 线程约 1.4 毫秒、1 线程约 7.8 毫秒），所有线程合计的 CPU
# 却是单线程的好几倍，做完还空转一阵；模板比对（matchTemplate）、预筛把整幅缩到 96x54 那一次本来就不分线程。
# 识别的大头是文字识别，它的线程数另由 chip_select.OCR_THREADS 管，不受这一项影响；截取画面的颜色转换按段转
# （window_capture.to_bgr），本来就不分线程。
CV_THREADS = 1
# 识别线程把一帧交给 Session.on_frame 的这段时间（_call_frame：算推荐在锁外，一次几十到几百毫秒的纯 Python 计算）里，解释器
# 换线程的间隔（sys.setswitchinterval）临时调到这么短（秒）。界面线程每调一次 Qt 都会放开解释器锁、调完再拿回来，刷新一次
# 要拿回几十次；识别线程正在算推荐时，界面线程每次要先等一个换线程的间隔才轮得到，Windows 上这个等待按系统计时器的粒度走
# （常见约 15.6 毫秒，比默认的 5 毫秒还长），算推荐的那几百毫秒里一次刷新要等上一百多毫秒。不到 1 毫秒时按 0 毫秒等：界面线程
# 一要就让给它。只在这一段调短：其余时候照默认，两个线程都在算的时候不来回切
UI_FIRST_SWITCH_S = 0.0005
# 换线程的间隔是整个进程的设置，调短、改回按进出次数计（_ui_first）：几个线程交叠着进出时，要是各自记下进去时的值、出来时
# 各自改回，后出来的会把先进去的那个调短以后的值当成原值改回去，之后一直是调短的
_UI_FIRST_LOCK = threading.Lock()
_ui_first_count = 0  # 现在有几个在 _ui_first 里（同一个线程嵌套的也算）
# 第一个进去时原来的间隔：最后一个出来时改回它。原来就不比 UI_FIRST_SWITCH_S 长（没有调）时为 None，出来也不用改回
_ui_first_before: float | None = None


@contextmanager
def _ui_first():
    """这个 with 期间把换线程的间隔调到 UI_FIRST_SWITCH_S（已经更短时不动），出来时改回原来的，出错出来也改回。只有识别线程调
    （_call_frame）。第一个进去的记下原来的间隔并调短，最后一个出来的才改回，进出都在 _UI_FIRST_LOCK 里：几个线程交叠着
    进出、同一个线程嵌套都改得回原值。"""
    global _ui_first_count, _ui_first_before
    with _UI_FIRST_LOCK:
        if _ui_first_count == 0:
            before = sys.getswitchinterval()
            _ui_first_before = before if before > UI_FIRST_SWITCH_S else None
            if _ui_first_before is not None:
                sys.setswitchinterval(UI_FIRST_SWITCH_S)
        _ui_first_count += 1
    try:
        yield
    finally:
        with _UI_FIRST_LOCK:
            _ui_first_count -= 1
            if _ui_first_count == 0 and _ui_first_before is not None:
                # 解释器按微秒存间隔、写的时候截掉小数：读到的小数原样写回可能少 1 微秒（每帧改回一次就越来越短），先取整到微秒
                sys.setswitchinterval((round(_ui_first_before * 1e6) + 0.5) / 1e6)
                _ui_first_before = None


def limit_cv_threads() -> None:
    """把 OpenCV 的线程数设成 CV_THREADS（全进程的设置）。悬浮窗的识别线程开头调用一次。
    设不了时照旧按 OpenCV 默认的线程数，只是 CPU 占用高一些，识别结果不受影响。"""
    try:
        cv2.setNumThreads(CV_THREADS)
    except Exception:  # noqa: BLE001  省 CPU 的设置，出错不能让识别线程退出
        pass


def _wall() -> datetime:
    """现在的本地时刻：留下三选一读不全的那一帧时记下（FrameProcessor.unread_shot，诊断包里那一张的文件名和 state.json 按它
    写）。各项识别多久做一次按调用方给的单调时钟，不用它。"""
    return datetime.now()


def _copy_frame(img: np.ndarray) -> np.ndarray:
    """留给诊断包的那一帧复制一份（FrameProcessor._note_unread）：截取的缓冲之后可能被别的帧复用。"""
    return np.array(img, copy=True)


# 对局中途像打完的那一帧缩到长边不超过这么多像素（FrameProcessor._note_finish_look）：只要看得出是什么画面，不必是原图；
# 1280x720 的一张三通道约 2.7 MB
FINISH_SHOT_SIDE = 1280


def _timed(name: str, fn, /, *args, **kwargs):
    """调用一项识别，记一笔它的耗时（毫秒，记成 read:名字）。

    按模块级的名字取函数（调用处传进来的就是模块全局名），测试在 pipeline 模块上替换这些名字时照旧生效。
    出错时也记一笔，再把异常原样抛给调用方（由 _safe 接住）。name、fn 只按位置传：被计时的函数有同名的关键字参数时
    照样原样交给它。
    """
    begin = time.monotonic()
    try:
        return fn(*args, **kwargs)
    finally:
        perfstats.record(f"read:{name}", (time.monotonic() - begin) * 1000.0)


def _edges_lit(img: np.ndarray, step: int) -> bool:
    """粗看的那一层（每隔 step 个像素取一个点，即 trim_borders 里的 small）靠四条边的一圈上，四条边各有亮点
    （有一个通道超过 BORDER_DARK）。一圈的宽度是离这条边不到 BORDER_MIN 的那几行、几列（3840x2054 是上下各 3 行、
    左右各 6 列，3840x2160 是上 3 行、下 2 行、左 5 列、右 4 列）。

    为 True 时 trim_borders 整套算下来必定原样返回：有亮点的第一行（列）不会比这一圈里有亮点的那一行（列）离边更远，
    按原分辨率找准的边界也不会更远，离边都不到 BORDER_MIN，全部归零。图不是三维的（不是 BGR）或是空的时为 False，
    交给整套算法照旧处理。"""
    if img.ndim != 3:
        return False
    h, w = img.shape[:2]
    ys, xs = np.arange(0, h, step), np.arange(0, w, step)  # small 的各行、各列在原图上的位置
    top, bottom = ys[ys < BORDER_MIN * h], ys[h - ys - 1 < BORDER_MIN * h]
    left, right = xs[xs < BORDER_MIN * w], xs[w - xs - 1 < BORDER_MIN * w]
    if not (top.size and bottom.size and left.size and right.size):
        return False
    return bool(
        img[: top[-1] + 1 : step, ::step].max() > BORDER_DARK
        and img[bottom[0] :: step, ::step].max() > BORDER_DARK
        and img[::step, : left[-1] + 1 : step].max() > BORDER_DARK
        and img[::step, right[0] :: step].max() > BORDER_DARK
    )


def _sparse_band(img: np.ndarray, axis: int, lo: int, hi: int) -> bool:
    """[lo, hi) 这一条里比 BORDER_DARK 亮的像素占不到 BORDER_SPECK 时为真。axis 为 0 时是整幅宽的几行，为 1 时是整幅高的几列。"""
    if hi <= lo:
        return True
    band = img[lo:hi] if axis == 0 else img[:, lo:hi]
    return float((band.max(axis=2) > BORDER_DARK).mean()) < BORDER_SPECK


def trim_borders(img: np.ndarray, keep: tuple[int, int, int, int] | None = None) -> tuple[np.ndarray, tuple[int, int]]:
    """去掉画面四周的纯黑边，返回（中间的游戏画面, 它在原图上的左上角）。没有黑边时原样返回。

    先粗看一层（每隔 step 个像素取一个点）找出有亮点的第一行、最后一行、第一列、最后一列，再在它们附近按原分辨率找准。
    每帧都要做，所以先只看粗看那一层靠四条边的一圈（_edges_lit）：四条边上都有亮点时结果必定是原样返回，不用再看整幅；
    结果和整套算完全相同。没有黑边的平常画面在这一步就判完（4K 约 0.03 毫秒，整套约 2.2 毫秒；2560x1057 约 0.05 对 3.4），
    整幅都暗、真有黑边的画面照旧整套算（多花约 0.02 毫秒）。

    keep 是上一帧最后用的范围（x0, y0, x1, y1，同样大小的图上的像素；调用方保证）：先照原算法算出这一帧的范围（没有黑边时
    就是整幅），哪一边算出来的黑边比 keep 那一边薄，就看多出来的那一条里比 BORDER_DARK 亮的像素占多少，不到 BORDER_SPECK
    （像 16:10 屏幕的黑边里断续画着的小旗）时这一边照 keep 去；黑边比 keep 厚的那一边照新算的。这样黑边里零星冒出的小图标
    不会让这一帧少去黑边、整个界面偏几十像素。照 keep 去完剩下的不到 BORDER_KEEP 时（几帧各自暗在一边，拼起来剩得太少，
    极端时什么都不剩），这一帧照自己算的去，和不给 keep 时一样。不给 keep 时和原来一字不差；给了而且 keep 不是整幅时，
    _edges_lit 那条捷径也要多看上面那几条（小旗碰到最外一圈时四条边都有亮点，直接返回就会漏掉），其余照旧只在黑边变薄的
    几帧多看一条。"""
    h, w = img.shape[:2]
    full = (0, 0, w, h)
    step = max(1, min(h, w) // 270)
    shortcut = _edges_lit(img, step)
    if shortcut and (keep is None or keep == full):
        return img, (0, 0)
    if shortcut:
        cand = full  # 四条边都有亮点：整套算下来必定是整幅
    else:
        small = img[::step, ::step].max(axis=2)
        rows = np.flatnonzero(small.max(axis=1) > BORDER_DARK)
        cols = np.flatnonzero(small.max(axis=0) > BORDER_DARK)
        if rows.size == 0 or cols.size == 0:
            cand = full
        else:
            def edge(lo: int, hi: int, axis: int) -> tuple[int, int]:
                """在粗估的边界附近按原分辨率找准。"""
                n = h if axis == 0 else w
                a0, a1 = max(0, lo * step - step), min(n, lo * step + step)
                b0, b1 = max(0, hi * step - step), min(n, (hi + 1) * step + step)
                if axis == 0:
                    band_a, band_b = img[a0:a1].max(axis=(1, 2)), img[b0:b1].max(axis=(1, 2))
                else:
                    band_a, band_b = img[:, a0:a1].max(axis=(0, 2)), img[:, b0:b1].max(axis=(0, 2))
                first = np.flatnonzero(band_a > BORDER_DARK)
                last = np.flatnonzero(band_b > BORDER_DARK)
                start = a0 + int(first[0]) if first.size else lo * step
                end = b0 + int(last[-1]) + 1 if last.size else min(n, (hi + 1) * step)
                return start, end

            y0, y1 = edge(int(rows[0]), int(rows[-1]), 0)
            x0, x1 = edge(int(cols[0]), int(cols[-1]), 1)
            if y0 < BORDER_MIN * h:
                y0 = 0
            if h - y1 < BORDER_MIN * h:
                y1 = h
            if x0 < BORDER_MIN * w:
                x0 = 0
            if w - x1 < BORDER_MIN * w:
                x1 = w
            small_enough = (y1 - y0) < BORDER_KEEP * h or (x1 - x0) < BORDER_KEEP * w
            cand = full if small_enough or (x0, y0, x1, y1) == full else (x0, y0, x1, y1)
    if keep is not None:
        x0, y0, x1, y1 = own = cand
        kx0, ky0, kx1, ky1 = keep
        # 上边新算的黑边薄（y0 更小）：多出来的 [y0, ky0) 这几行里只有零星亮点就照 keep 去
        if y0 < ky0 and _sparse_band(img, 0, y0, ky0):
            y0 = ky0
        if y1 > ky1 and _sparse_band(img, 0, ky1, y1):
            y1 = ky1
        if x0 < kx0 and _sparse_band(img, 1, x0, kx0):
            x0 = kx0
        if x1 > kx1 and _sparse_band(img, 1, kx1, x1):
            x1 = kx1
        # 各边分别照 keep 去，拼起来可能剩不到一半（keep 和这一帧各暗在一边）：照这一帧自己算的，不去掉大半幅画面
        too_small = (y1 - y0) < BORDER_KEEP * h or (x1 - x0) < BORDER_KEEP * w
        cand = own if too_small else (x0, y0, x1, y1)
    if cand == full:
        return img, (0, 0)
    x0, y0, x1, y1 = cand
    return img[y0:y1, x0:x1], (x0, y0)


def _thumb(img) -> np.ndarray:
    """很小的灰度缩略图，用来便宜地判断画面有没有变化。收 Frame 或 ndarray。"""
    img = as_frame(img).image
    return cv2.cvtColor(cv2.resize(img, (64, 36), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)


def _fingerprint(img, region) -> np.ndarray:
    """判定区域（x0, y0, x1, y1，占窗口的比例）的指纹。收 Frame 或 ndarray。

    隔几个像素取一个点，再缩成 FP_SIZE 的灰度图：4K 画面约 0.5 毫秒（整幅缩放要 10 毫秒）。
    """
    img = as_frame(img).image
    h, w = img.shape[:2]
    x0, y0, x1, y1 = region
    top, left = round(y0 * h), round(x0 * w)
    crop = img[top : max(round(y1 * h), top + 1), left : max(round(x1 * w), left + 1)]
    step = max(1, crop.shape[1] // (FP_SIZE[0] * 16))
    small = cv2.resize(crop[::step, ::step], FP_SIZE, interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.int16)


def _same(a: np.ndarray | None, b: np.ndarray | None) -> bool:
    return a is not None and b is not None and a.shape == b.shape and float(np.abs(a - b).mean()) / 255 <= FP_SAME


def _incomplete(seen) -> bool:
    """三选一读得不完整：概率行读不到（或三项之和不对）、有筹码名认不出、有筹码名只读到几个筹码名的共同部分
    （ChipSelect.partial，「飞星」）、或三格的稀有度都读不出（出场动画里筹码图标还没加载，只有空圈）。"""
    rarity = list(getattr(seen, "rarity", None) or ())
    return (
        not getattr(seen, "probabilities", None)
        or not all(seen.names)
        or any(getattr(seen, "partial", None) or ())
        or (bool(rarity) and not any(rarity))
    )


def _weak_slot(seen, i: int) -> bool:
    """三选一读数的第 i 张（从 0 数）读得不准：读不出（名字为 None）、只读到几个筹码名的共同部分（partial），或者识别提示说它
    匹配度低（chip_select.check_names 的写法「第 N 个候选筹码匹配度低……」「……无法识别……」）。"""
    names = list(getattr(seen, "names", None) or ())
    if i >= len(names) or names[i] is None:
        return True
    partial = list(getattr(seen, "partial", None) or ())
    if i < len(partial) and partial[i]:
        return True
    prefix = f"第 {i + 1} 个候选筹码"
    return any(p.startswith(prefix) and ("匹配度低" in p or "无法识别" in p) for p in getattr(seen, "problems", None) or ())


def _reread_copy(seen, selected: int | None, watch: bool = False):
    """沿用一次完整识别的结果（seen），只换选中项的一份，同 chip_select.reread_selected 交回的那样标 reread（不是新读的一次）；
    watch 为真时另标是快速盯绿框看到的（vision/pick_watch.py）。seen 不是 dataclass 时（测试里的假读数）复制后改属性。"""
    fields = {"names": list(seen.names), "raw": list(getattr(seen, "raw", None) or ()), "selected": selected,
              "problems": list(getattr(seen, "problems", None) or ()), "reread": True}
    if watch:
        fields["watch"] = True
    if dataclasses.is_dataclass(seen) and not isinstance(seen, type):
        return dataclasses.replace(seen, **fields)
    out = copy.copy(seen)
    for key, value in fields.items():
        setattr(out, key, value)
    return out


def _same_offer(old, new) -> bool:
    """快速盯绿框的两份要盯的（pick_watch.Target）是同一组候选：三个筹码名都一样。同一组又完整识别了一次（补认、读得不完整时
    再认、筹码名那一条一时对不上又认回来）时识别流程换一份要盯的，画面上还是这三张（见 FrameProcessor._watch_frame）。"""
    return new is not None and list(old.seen.names) == list(new.seen.names)


class _Unchanged:
    """画面没变就沿用「这一帧不是某个界面」的结论，不再重复做文字识别。

    文字识别的结论是「不是」、而且画面是静止的（和上一次看时的指纹差不多）时，记下这时的指纹。
    之后看到的画面和它差不多、又没过 max_s 秒，就直接当作「不是」；画面一变、或过了 max_s，照常识别。
    只在静止时记：正在淡入、还没显示完整的界面可能识别不出来，不能记成「不是」，下一帧显示完整了要马上再认。
    不用文字识别就知道「不是」的画面（三选一的标题带不在，见 FrameProcessor._no_title_band）也照这样记。
    """

    def __init__(self, region, max_s: float):
        self.region, self.max_s = region, max_s
        self.entry: tuple[np.ndarray, float] | None = None  # 结论为「不是」时的（指纹, 识别的时刻）
        self.matched: tuple[np.ndarray, float] | None = None  # 这一帧对上的 entry（沿用的，或这一帧刚记下的）
        self._last: np.ndarray | None = None  # 最近一次看的指纹
        self._settled = False  # 最近一次看的画面和再上一次差不多

    def same(self, img: Frame | np.ndarray, now: float, force: bool = False) -> bool:
        """看一眼这一帧：和记下的「不是」画面一样、可以不再识别时返回 True。force 时一律返回 False。"""
        fp = _fingerprint(img, self.region)
        self._settled = _same(fp, self._last)
        self._last = fp
        entry = self.entry
        if not force and entry is not None and now - entry[1] < self.max_s and _same(fp, entry[0]):
            self.matched = entry
            return True
        self.matched = None
        return False

    def record(self, found: bool, now: float) -> None:
        """same() 返回 False 后认过了（做了文字识别，或不用识别就知道不是），found 是认出了这个界面没有。"""
        if found:
            self.entry = None
        elif self._settled:
            self.entry = self.matched = (self._last, now)

    def interrupt(self) -> None:
        """这一帧没有看（例如没过预筛）：下一帧不算静止，这一帧也不算对上了。"""
        self._last = self.matched = None


class SelectBoard:
    """两个客户端同时识别时，两路识别流程共用的一块牌子：哪一路的三选一正显示着。

    文字识别引擎全进程只有一个，两路都要排队用（chip_select 里的引擎锁）。一路的三选一显示着时，它的每一次文字识别（认三张卡、
    先读完的轮次和进度条、核对）都可能要等另一路正在做的那一次（慢电脑上最长约 1 秒），推荐跟着晚出来。所以那一路显示着三选一
    时，另一路暂停平时要做文字识别的读数（FrameProcessor._idle_reads 的 OCR_STEPS），模板比对、看自己有没有三选一照旧做；
    那一路的推荐给过以后、或者关掉以后接着做：推荐给过以后那一路只盯绿框，基本不用引擎，玩家一直不选时另一路的读数不该跟着
    一直变旧。两路都显示着三选一时各读各的，谁都不让。
    每一路每帧认完三选一以后在这里记一笔（note）：显示着、推荐还没给就记下这一帧的时刻，否则（没显示、被隐藏着也算）擦掉；暂停、
    不跟着三选一时也擦掉（FrameProcessor.watch_idle）。记下的时刻超过 BOARD_STALE_S 没更新就不再算数：那一路的识别线程停了、
    重建了、卡住了的时候，另一路不会一直让路。时刻用两路共同的单调时钟（悬浮窗的识别线程都传 time.monotonic）。
    哪个线程都可以调用（自带锁）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._showing: dict[int, float] = {}  # id(识别流程) → 最近一次看见自己的三选一的时刻

    def note(self, owner, showing: bool, now: float) -> None:
        """owner（一路的识别流程）这一帧有没有显示着还没给过推荐的三选一。"""
        with self._lock:
            if showing:
                self._showing[id(owner)] = now
            else:
                self._showing.pop(id(owner), None)

    def others(self, owner, now: float) -> bool:
        """除了 owner 自己，还有没有哪一路的三选一正显示着、推荐还没给（BOARD_STALE_S 以内记过）。"""
        with self._lock:
            return any(key != id(owner) and now - at < BOARD_STALE_S for key, at in self._showing.items())


class FrameProcessor:
    def __init__(self, db: DB, session: Session, lock: threading.Lock | None = None, platform=None):
        self.db, self.session = db, session
        self.lock = lock or threading.Lock()
        # 这个识别流程跟的是哪个端（capture.STEAM、capture.TAPTAP）：查独占全屏时只看这个端（_check_window）；None 时
        # 按游戏平台设置查。两个客户端同时识别时每路各跟一个端，运行中换端由悬浮窗的识别线程改这一项（Watcher.set_platform）
        self.platform = platform
        # 识别的图片模板和沿用缓存收在一个对象里（templates.VisionAssets）；这里是共享的那一个实例，
        # 所以 vision.reset_caches() / reset_hints() 作用到的就是本处理器用的缓存。
        self.assets = templates.assets
        watch = db.rules.get("watch", {})
        self.hud_s = watch.get("hud_ms", 3000) / 1000
        self.hero_s = watch.get("hero_ms", 20000) / 1000
        self.coins_s = watch.get("coins_ms", COINS_MS) / 1000
        self.reread_s = watch.get("select_reread_ms", SELECT_REREAD_MS) / 1000  # 补认隔多久（见 SELECT_REREAD_MS）
        # 三选一快读（[watch] select_fast，见 _read_select）开着没有；快读要裁的位置（chip_select.SelectLayout，最近一次完整读
        # 认出三选一、位置记得下来时记的，只留最新的一份；没记过时为 None）。两个客户端各有自己的识别流程，各记各的
        self.select_fast = bool(watch.get("select_fast", True))
        self._select_layout = None
        self.retry_s = watch.get("hud_retry_ms", HUD_RETRY_MS) / 1000  # 三选一上读左上角没成功时隔多久再读（见 HUD_RETRY_MS）
        # 轮次被挡住时隔多久便宜地看一眼、隔多久完整读一次（见 OCCLUDED_RECHECK_MS、OCCLUDED_FULL_MS）
        self.occluded_s = watch.get("occluded_recheck_ms", OCCLUDED_RECHECK_MS) / 1000
        self.occluded_full_s = watch.get("occluded_full_ms", OCCLUDED_FULL_MS) / 1000
        # 连标记都没看见时多久完整读一次轮次（见 BLANK_FULL_MS）；是读的间隔，低配模式乘倍数
        self.blank_full_s = watch.get("blank_full_ms", BLANK_FULL_MS) / 1000
        # 这一局近这么久（秒）内读到过轮次时，完整读第一遍读不出就不再换大小多认两遍（见 _read_hud）。只是「多久算最近」，
        # 不是读的间隔，低配模式不乘倍数
        self.round_retry_gap_s = watch.get("round_retry_gap_ms", ROUND_RETRY_GAP_MS) / 1000
        self.block_s = watch.get("map_block_ms", MAP_BLOCK_MS) / 1000  # 任务栏没开时隔多久连带读一次整块（见 MAP_BLOCK_MS）
        # 不在对局里时多久看一眼下方区域（选角界面、对局外的画面，见 SELECT_MS）；最近一次看的时刻（_read_pick，不论是读不到
        # 轮次时顺带看的，还是这一眼）
        self.select_s = watch.get("select_ms", SELECT_MS) / 1000
        # 本局进行中读不到轮次、标记也没有时，看见轮次标记以后第二次起、离那次标记不到这么久先不判选角界面（见 PICK_GRACE_MS、
        # _pick_grace）。
        # _ending_seen_at：最近一次看见像通关画面、打输横幅的颜色（_check_victory）的时刻，离它不到 pick_grace_s 时不先不判
        self.pick_grace_s = watch.get("pick_grace_ms", PICK_GRACE_MS) / 1000
        self._ending_seen_at = float("-inf")
        # 看见哪一次标记（_mark_seen_at 的值）以后读不到时照判过一次：和 _mark_seen_at 不一样，就是看见这次标记以后第一次
        # 读不到，照判、记下；一样时才先不判（_pick_grace）
        self._grace_mark: float | None = None
        self._last_glance = float("-inf")
        self._last_coins = float("-inf")  # 最近一次读星币数的时刻
        detect = db.rules.get("map_detect", {})
        self.loading_on = detect.get("loading", True)
        self.loading_s = detect.get("loading_ms", 2000) / 1000
        self.loading_width = detect.get("loading_width", 1280)
        self.loading_change = detect.get("loading_change", 0.03)
        self.after_game_s = detect.get("after_game_ms", 30000) / 1000
        # 连着这么久（只算看着画面的时间）看不到对局里的读数，就当作不在对局里了（Session.game_quiet，见 _count_quiet）
        self.quiet_s = detect.get("quiet_ms", 60000) / 1000
        self.mutators_on = detect.get("mutators", True)
        self.mutators_s = detect.get("mutators_ms", 6000) / 1000
        # 认对局外的画面（结算画面、「匹配成功」画面、房间、奖励画面、主菜单）：选角界面判断那次的下方区域和离开对局后的整幅识别
        self.screens_on = detect.get("out_of_game", True)
        self.tasks_now = detect.get("task_panel_immediate", True)
        self._last_hud = float("-inf")
        self._last_hero = float("-inf")
        # 最近一次在对局画面上的时刻：读到轮次、或者读轮次时在数字那一行看见了轮次标记（数字被挡住）时更新，认出选角界面、
        # 对局外的画面时清掉（_left_game）。选角界面、结算画面、房间、奖励画面、主菜单上都没有这个标记，所以看见它就是在对局里。
        # 三选一刚出现时手上的轮次够不够新（_readings_fresh）、离开对局后的整幅识别从什么时候开始找（_check_loading）、让路期间
        # 要不要认角色和读星数（_idle_reads）都按它算：数字被挡住期间不当成离开了对局
        self._mark_seen_at = float("-inf")
        # 最近一次对局里的读数以后看了多久画面（秒，只算看着画面的时间，见 _count_quiet），和上一次算进去的那一帧的时刻
        # （None：还没算过、刚从暂停恢复，下一帧从那时接着算）
        self._quiet_for = 0.0
        self._quiet_at: float | None = None
        # 轮次被挡住：最近一次完整读轮次在数字那一行看见了标记、数字却读不出，交回了标记左边那一小块的位置。为（那一小块,
        # 读的时候去掉黑边后的画面尺寸）；读到了轮次、没看见标记、画面尺寸变了时为 None（见 _occluded_crop）
        self._occluded: tuple | None = None
        # 读轮次先只认字（见 _round_quick）：最近一次完整读读到了轮次、也看见了标记时标记左边那一小块。为（那一小块, 读的时候
        # 去掉黑边后的画面尺寸）；没记过、离开了对局（_left_game）、画面尺寸变了时为 None
        self._round_memo: tuple | None = None
        # 最近一次完整读轮次看见了标记（读没读到数字都算）的时刻：只认字看不见标记，离它满 after_game_s 时不先只认字、直接完整读
        # （见 _round_quick）
        self._full_mark_at = float("-inf")
        # 最近一次「不在三选一时的完整读、什么都没读到」（轮次和标记都没有）的时刻：这一局正打着、刚看见过轮次（_mid_game）时，
        # 离它不到 blank_full_s 只认字、不再完整读（见 _read_hud）。读到了轮次或看见了标记（三选一上完整读看见的也算）、只认字
        # 读到轮次时设回 -inf
        self._full_blank_at = float("-inf")
        self._last_peek = float("-inf")  # 最近一次便宜地看一眼（_peek_round）的时刻
        self._last_block = float("-inf")  # 最近一次读轮次时连带读整块（地图未知时，见 MAP_BLOCK_MS）的时刻
        # 最近一次连带读整块读到了任务（tasks_seen 非空：任务栏、或按颜色认不出来的任务列表开着）：读到没有为止每次都连带读
        # （见 _block_due）
        self._block_tasks = False
        self._last_loading = float("-inf")  # 最近一次离开对局后的整幅识别（_check_loading）的时刻
        self._last_mates = float("-inf")
        self._mates_tries = 0  # 这一局读了几次队友
        self._mates_game = -1  # 上面的次数是第几局的（Session.games）
        self._last_panel = float("-inf")
        self._panel_seen_at = float("-inf")  # 最近一次读到「查看筹码」面板的时刻（PANEL_GRACE_S）
        self._last_fullscreen = float("-inf")
        self._loading_thumb: np.ndarray | None = None
        self._hero_misses = 0
        self._select_star_tries = 0  # 这次三选一已经读了几帧星数
        self._select_hero_at = float("-inf")  # 三选一期间、角色待识别时最近一次认角色的时刻
        self._recheck_round = False  # 三选一界面上读到了往前大幅跳变的轮次：下一帧马上再读一次，不等 hud_ms
        self._recheck_progress = False  # 三选一界面上读到的进度条当前格和已采用的不同：下一帧马上再读一次
        # 三选一界面上读到了变小的轮次（可能开了新的一局，要连读两次）：到这个时刻（隔 hud_ms）再读一次；None 为不用
        self._drop_check_at: float | None = None
        # 这次三选一先给了推荐（手上的轮次、进度条够新，见 _readings_fresh）：下一帧再读一次轮次和进度条核对
        self._check_due = False
        # 这次三选一里确认变小的轮次的那一次已经读过（之后读不出、出错时只按 retry_s 再试，不再从头隔 hud_ms 排一次确认）
        self._drop_checking = False
        # 三选一上读左上角没成功（出错、读不出轮次，见 _select_hud）：到这个时刻再读一次；None 为不用。_hud_retries 是这次三选一
        # 已经再读了几次（最多 HUD_RETRIES 次），_retry_owner 是数这几次时的（局数, 角色）：三选一开着时开了新的一局、换了角色，
        # 重新数（见 _select_reads）
        self._hud_retry_at: float | None = None
        self._hud_retries = 0
        self._retry_owner: tuple | None = None
        # 再读左上角时连同进度条：上一次要连同进度条却没读成（读进度条出错，或读轮次出错、没走到读进度条那一步）才连同；只因为
        # 读不出轮次而再读时不重读进度条（那一次已经读到了，进度条也不会被头像挡住）
        self._retry_progress = False
        self._hud_round: int | None = None  # 最近一次读轮次读到的（读不到为 None）：三选一上据此判断要不要再读（_select_hud）
        # 先读完再给推荐的那一次没有认角色（推荐不等它）：给出推荐以后的下一帧按间隔认一次（_select_reads），换了角色的新一局
        # 晚一帧改过来
        self._hero_next = False
        # 补认：这次三选一刚出现时读得可疑（_suspicious），到这个时刻再完整识别一次三张卡（_read_select）；None 为不用。
        # _suspect_due：这次三选一还没判过可不可疑（刚出现那一帧没读全时，等第一次读全的那一次判）。每次三选一只判一次，
        # 最多补认一次
        self._reread_at: float | None = None
        self._suspect_due = False
        self._manual = False  # 手动模式（悬浮窗的识别线程经 set_manual 告诉它）：平时不看画面，三选一出现时先读完再给推荐
        self._stale = False  # 刚从暂停恢复（resumed）、之后还没读到过轮次：三选一出现时先读完再给推荐
        # 刚从暂停恢复、还没认过一帧三选一：暂停前显示着的三选一，恢复后的第一帧也当作刚出现（见 resumed）
        self._after_pause = False
        # 不在三选一时这一轮读数还没做的几项（按先后，见 _idle_reads；None 为没有在做的一轮），以及这一轮是不是按热键的那一帧
        # 开始的（认选角界面、轮次沿用照它）
        self._hud_rest: list[str] | None = None
        self._hud_force = False
        # 这一帧做了一项读数以后还有到期的没做、排到了下一帧：悬浮窗的识别线程不等 idle_ms，紧接着取下一帧
        self.pending = False
        self._tasks_open = False  # 上一帧任务栏是不是开着（刚打开时立即读）
        # 最近一次完整识别的三选一：（结果, 筹码名那一条的指纹, 画面尺寸, 识别的时刻, 沿用多久）。
        # 沿用多久：读得完整时一直沿用（三个筹码名那一条变了、画面尺寸变了才再认），不完整时为 SELECT_RETRY_S
        self._select_cache: tuple | None = None
        # 战斗等画面读不到轮次时还要判断是不是选角界面：每次文字识别约 0.25 秒、CPU 0.7 秒。认定「不是三选一」（标题带不在，或
        # 过了预筛、文字识别否定）「不是选角界面」以后，判定区域的画面没变就沿用结论，不再识别
        unchanged_s = watch.get("unchanged_max_ms", UNCHANGED_MAX_MS) / 1000
        self._not_select = _Unchanged((0.0, 0.0, 1.0, OCR_TOP), unchanged_s)
        self._not_pick = _Unchanged(SELECT_REGION, unchanged_s)
        # 进度条没变就不认字（见 _read_progress）：最近一次不在三选一上认字读进度条时那一块的（样子 hud.ProgressLook, 画面尺寸,
        # 认字的时刻, 读数 hud.Progress）；没有时为 None。沿用最多 progress_s（同 unchanged_max_ms，低配模式乘倍数）
        self._progress_seen: tuple | None = None
        self.progress_s = unchanged_s
        self._no_band_entry: tuple | None = None  # _not_select 里最近一条因为标题带不在才记下的（见 _no_title_band）
        # 主菜单空转（见 _menu_idle）：记下的主菜单，为（固定界面那几块的指纹（MENU_REGIONS）, 画面尺寸, 认出的时刻, 记下以后
        # 有没有哪一帧对上过，即开始空转了没有）；没记着时为 None。_menu_glance：刚不再空转，下一次排读数时先看一眼下方区域
        # （见 _menu_leave）
        self._menu: tuple | None = None
        self._menu_glance = False
        # 防来回进出（见 _menu_trip），这次停在主菜单期间的记录（_menu_stay_over 清掉）：_menu_left 最近一次因为固定界面变了不再
        # 空转的（时刻, 哪一块变了：bar 底栏、start 开始按钮、both 两块都变了, 画面尺寸）；_menu_trips 最近 MENU_FLAP_MS 以内的
        # 来回（时刻, 哪一块）；_menu_bar_only 只拿底栏判断；_menu_hush 判过来回了，来回的那几次进出不再记识别记录；
        # _menu_unlogged 判过以后因为固定界面变了不再空转、还没记的那一次是哪一块变了（不是来回时补记，见 _menu_leave）；
        # _menu_shape 上次记下的主菜单的画面尺寸。_menu_off_until：底栏也在来回，这之前不空转（停留结束也不清，过了再试）
        self._menu_left: tuple | None = None
        self._menu_trips: list[tuple[float, str]] = []
        self._menu_bar_only = False
        self._menu_hush = False
        self._menu_unlogged: str | None = None
        self._menu_shape: tuple | None = None
        self._menu_off_until = float("-inf")
        self.menu_idle_s = MENU_IDLE_MS / 1000  # 空转满这么久照常认一次（低配模式不乘倍数，见 MENU_IDLE_MS）
        # 停在主菜单时空转不空转。同时识别两个客户端的性能测试（ui/dual_check.py）关掉：它让两个游戏停在大厅量负担，要照不空转
        # 时的读法量，不然量出来的几乎只剩取帧，进了对局才发现吃不消
        self.menu_idle = True
        # 上面几个间隔在 [watch] 里读到的原值：低配模式按倍数改节奏时都从它乘（set_pace），不累乘
        self._base_pace = (self.hud_s, self.hero_s, self.coins_s, unchanged_s, self.reread_s, self.retry_s,
                           self.occluded_s, self.occluded_full_s, self.blank_full_s, self.block_s, self.select_s,
                           self.pick_grace_s)
        # 两个客户端同时识别时两路共用的「谁的三选一正显示着」（SelectBoard，悬浮窗的识别线程接上）；None 时不让路（只有一路、
        # 回放、测试）
        self.board: SelectBoard | None = None
        self._last_yield = float("-inf")  # 让路期间最近一次按 hud_ms 认角色、读星数的时刻（见 _idle_reads）
        # 最近一次轮次、任务栏什么都没读到：（当时对上的「不是三选一」画面, 有没有连任务栏一起读）
        self._blank_hud: tuple | None = None
        self.stars_on = True  # 缺星数识别用的模板时关掉，其余照常
        self.chip_select_visible = False
        self.panel_visible = False  # 上一帧是不是「查看筹码」面板（自己那一页）
        self.offset = (0, 0)  # 上一帧去掉黑边后游戏画面在客户区里的左上角
        # 上一帧最后用的画面大小和黑边范围（(shape[:2], (x0, y0, x1, y1))）：大小没变时交给 trim_borders（keep），
        # 黑边里零星冒出的小亮点（16:10 屏幕的游戏在黑边里画的小图标）不会让这一帧少去黑边；大小变了重新找
        self._border: tuple | None = None
        self.last_frame: np.ndarray | None = None  # 最近一次交来识别的画面（客户区整幅）
        # 最近一次三选一读不全时的（本地时刻, 那一帧的整幅画面）：交给记账的这一帧是新读的、有筹码没读出来、又不在刷新后的
        # 等待里（WatchHints.select_unread）时留下，新的来了就整个换掉（_note_unread）。只放在内存里（一张客户区整幅截图，
        # 2560x1600 约 12 MB、3840x2160 约 25 MB），导出诊断时另存一张（diagnostics.export 的 select_unreadable）。画面复制
        # 一份（截取的缓冲之后可能被别的帧复用），这里不编码：三选一期间不多花编码的几十毫秒，导出时再编码。别的线程读它时
        # 读到的是整个元组，时刻和画面一定是同一次的
        self.unread_shot: tuple[datetime, np.ndarray] | None = None
        # 最近一次「记账记下了选中的是哪张」用的那一帧：（本地时刻, 画面, {slot 第几张（从 0 数）, chip 哪张筹码, 快速盯绿框
        # 看到的另有 watch、region（截的那一块在客户区里的左上宽高）}）。识别流程交给记账的读数（完整识别、快速复查）记下的
        # 留整幅画面，快速盯绿框看到的留它截的那一块（_note_picked）。不采用的那一帧：（本地时刻, 画面, {读到的 raw、names、
        # rarity、selected，变了的几张 slots}）（_note_covered，见 _covered）。都同 unread_shot：只放在内存里（各最多一张整幅
        # 截图），新的来了整个换掉，导出诊断时另存（diagnostics.export 的 select_picked、select_covered），复制出错时不留
        self.pick_shot: tuple | None = None
        self.covered_shot: tuple | None = None
        # 对局中途看见像一局打完时的颜色、这一局却接着打下去时的那一帧：（本地时刻, 缩小的画面, {look、coral、white、round、
        # size}）。_check_victory 先按条件把它留成待定的那张，之后这一局又读到轮次、满了 pick_grace_s 就转正到这里；导出诊断时
        # 另存（diagnostics.export 的 finish_look）。同上面几张：只放在内存里（缩到长边 1280，最多约 2.7 MB）；本局结束不清，
        # 玩家多半在打完以后才导出
        self.finish_shot: tuple | None = None
        # 待转正的那一张：（本地时刻, 缩小的画面, 那一次的情况, 留下它时的单调时钟, 留下它时判过几次新的一局 WatchHints.games）。
        # 后两项只用来判断转不转正（和 _mark_seen_at 比、认出是不是还是这一局），不写进诊断包
        self._finish_pending: tuple | None = None
        # 和快速盯绿框的线程之间的交换（vision/pick_watch.py）：每帧放上要盯的、取走看到的（_watch_frame）。悬浮窗的识别线程把
        # 盯绿框的线程接到它上面（PickWatcher.attach）；没有那个线程时（回放、测试）没人看到，什么都不多交
        self.pick_watch = PickWatch()
        self._watch_from: tuple | None = None  # 现在要盯的那一份是照哪个（缓存, 去掉的黑边, 客户区宽高）建的
        self._watch_carry: list = []  # 不在帧里放下要盯的时（watch_idle）取走的、还没交给记账的那几次（下一帧照先后交）
        # 这一帧接住的子识别错误：（这一项的名字, 异常, 调用栈的文字），process 每帧开头清空。由调用 process 的一方照样报出
        # （悬浮窗的后台识别线程把调用栈写进错误记录，按名字提示哪一项没读出来），见 _failed
        self.errors: list[tuple[str, Exception, str]] = []
        self.select_failed = False  # 这一帧认三选一出错：不知道是不是三选一（chip_select_visible 还是上一帧的），每帧开头清掉
        # 这一帧没有三选一，但认出了「您当前正处于隐藏UI状态」（三选一被玩家隐藏，当它还开着，见 _select_hidden），每帧开头清掉。
        # 悬浮窗手动模式跟随三选一时，隐藏着也接着看（等三选一回来或关掉）；按热键的那一帧是隐藏着的时候不报「当前画面不是
        # 筹码三选一界面」
        self.select_hidden = False
        # Session 不知道的那一次三选一正被隐藏着（按热键的那一帧认出了那行字，见 _select_hidden）：之后每帧照样看一眼那行字，
        # 那行字没了、三选一回来了就不再看
        self._hidden_unseen = False
        # 这一帧接下来读什么要看的几项（session.WatchHints）：每帧开头清掉，第一次要用时在锁里取一份（_hint），之后每次在锁里
        # 调完 Session 都顺手换一份新的（_renew），读的地方不再各自进锁。悬浮窗的识别线程在一帧做完以后也看它（holding）
        self.hints: WatchHints | None = None
        # 上一帧做完时的那一份（每帧开头从 hints 挪过来）：认三选一那一步还没进过锁，看这次三选一是不是刚出现
        # （select_fresh，见 _read_select）用它，不为它多进一次锁。界面线程在这之间给出的推荐（改正候选、选了角色）要到下一帧
        # 才看得到，最多先多重读一次三张卡
        self._last_hints: WatchHints | None = None
        self._seen = None  # 这一帧的三选一读数（WatchHints.another_select 按它算；不是三选一时为 None）
        # 这一帧认三选一时，这次三选一刚出现、读得不完整，读左上角排到完整重读三张卡之后；或者这一帧要完整识别（筹码名那一条
        # 变了等）、读左上角挪到下一帧（_read_select 定，_select_reads 这一帧不读左上角）；每帧开头清掉
        self._top_waits = False
        # 上一帧因为要完整识别把读左上角挪到了下一帧（_read_select）：连着两帧都要完整识别时这一帧不再挪
        self._top_deferred = False

    def set_pace(self, factor: float) -> None:
        """各项识别多久做一次改成 [watch] 原值的 factor 倍（低配模式；factor 为 1 时回到原值）：读轮次和
        任务栏、认角色、读队友、读星币，「画面没变就沿用」的上限（进度条没变就沿用读数的上限 progress_s 也是），三选一上补认、
        再读左上角之前等多久，轮次被挡住时多久看一眼、
        多久完整读一次、连标记都没看见时多久完整读一次，任务栏没开时多久连带读一次整块，不在对局里时多久看一眼选角界面，
        以及对局中刚看见过轮次标记多久以内
        先不判选角界面（pick_grace_s：读轮次的间隔跟着乘，乘了以后一段读不到轮次的时间里先不判的次数和原来一样）。按构造时读到的
        原值乘，不累乘。已经记下的时刻和沿用的结论（上次读轮次的时刻、这一局读了几次队友、三选一的沿用结果、「画面没变就不再认」
        的结论、已经排好的补认和再读……）不动：切换一次开关不会让这些从头来、多读一轮。看到的东西不变，只是看得晚一点。主菜单
        空转多久照常认一次（menu_idle_s）不乘（见 MENU_IDLE_MS）。在调用 process 的线程里、两帧之间调用。"""
        hud, hero, coins, unchanged, reread, retry, occluded, occluded_full, blank_full, block, select, grace = \
            self._base_pace
        self.hud_s, self.hero_s, self.coins_s = hud * factor, hero * factor, coins * factor
        self._not_select.max_s = self._not_pick.max_s = self.progress_s = unchanged * factor
        self.reread_s, self.retry_s = reread * factor, retry * factor
        self.occluded_s, self.occluded_full_s = occluded * factor, occluded_full * factor
        self.blank_full_s, self.block_s = blank_full * factor, block * factor
        self.select_s = select * factor
        self.pick_grace_s = grace * factor

    def set_manual(self, on: bool) -> None:
        """手动模式开或关（悬浮窗的识别线程在两帧之间告诉它）：手动模式平时不看画面，手上的轮次、进度条不算新，三选一出现时
        照旧先读完再给推荐（见 _readings_fresh）。开关变了时补认、再读左上角的次数和排着的时刻清掉
        （_clear_select_retries）：手动模式不跟随三选一时两次按热键之间看不到界面关掉，不能把这一次的带到下一次。"""
        on = bool(on)
        if on != self._manual:
            self._clear_select_retries()
        self._manual = on

    def resumed(self) -> None:
        """识别刚从暂停恢复（游戏刚切回前台等；悬浮窗的识别线程在恢复后的第一帧之前调用）：暂停期间轮次、进度条可能变了，
        之后第一次读到轮次以前出现的三选一照旧先读完再给推荐（见 _readings_fresh）。
        暂停前显示着的三选一，恢复后的第一帧也当作刚出现：暂停期间可能已经换成了另一次（概率行相同、刷新次数又分不出不是刷新
        时，Session 当作同一次里刷新了），不能沿用暂停前的读数、也不能因为「推荐给出以后只盯绿框」就一直不再读。暂停前排着的
        补认、再读左上角和已经再读的次数一并清掉（_clear_select_retries），恢复后从头算。
        暂停期间没看画面：连着多久看不到对局里的读数从恢复后的第一帧接着算（_count_quiet），暂停的时间不算进去。
        暂停前停在主菜单空转的，不再空转（_menu_leave）：暂停期间画面可能早就离开过主菜单，恢复后的第一帧照常认（先看一眼
        下方区域），还是主菜单再接着空转；防来回进出的记录（只拿底栏判断等）也作废，从头数（_menu_stay_over）。"""
        self._stale = True
        self._after_pause = True
        self._quiet_at = None
        self._clear_select_retries()
        self._menu_stay_over()
        self._menu_leave("resumed")
        self.watch_idle()

    def watch_idle(self) -> None:
        """不再盯绿框（vision/pick_watch.py）：悬浮窗的识别线程暂停、手动模式不跟着三选一、要长时间不取帧之前调用（从暂停恢复时
        也调一次）。要盯的放下（盯绿框的线程正在查的那一次先查完），已经看到、还没交给记账的留着，下一帧照先后交（_watch_frame）。
        下一帧看到三选一时照常放上。在调用 process 的线程里、两帧之间调用。
        两开时自己也不再算「三选一正显示着」（SelectBoard）：暂停、不跟着三选一的时候自己不看画面，另一路不用再让路。"""
        got = self.pick_watch.publish(None)
        self._watch_from = None
        if got is not None:
            self._watch_carry.append(got)
        if self.board is not None:
            self.board.note(self, False, 0.0)

    def _clear_select_retries(self) -> None:
        """清掉这次三选一的补认和再读左上角：排着的时刻、还没判的可疑、已经再读的次数、确认变小的轮次读过没有、再读时要不要
        连同进度条、推荐以后要不要认角色。新出现一次三选一、三选一关掉、从暂停恢复、手动模式开关变了时调用，不带到下一次三选一。"""
        self._reread_at = self._hud_retry_at = None
        self._suspect_due = False
        self._hud_retries = 0
        self._retry_owner = None
        self._drop_checking = False
        self._retry_progress = self._hero_next = False

    def settle(self, img: np.ndarray, now: float, force: bool = False) -> list[Event]:
        """把这一幅画面上到期的读数都做完：照常识别一帧（process），还有排到下一帧的读数（pending）时用同一幅画面接着识别，
        直到做完，相当于紧接着又截到一模一样的画面。回放截图的工具和测试用：一张截图代表画面在那一刻的样子，各项读数、先后
        和一帧做完时一样。悬浮窗的识别线程每次都截新的画面，不用它。返回这几帧的全部事件；这几帧接住的错误都留在 errors 里。
        后面几帧不再按热键的那一帧处理：force 只管看是不是三选一那一下，这一轮读数照开始那一帧的（_hud_force）。"""
        events = self.process(img, now, force)
        errors = list(self.errors)
        for _ in range(SETTLE_MAX):
            if not self.pending:
                break
            events += self.process(img, now)
            errors += self.errors
        self.errors = errors
        return events

    # ------------------------------------------------------------ 和 Session 打交道

    def _call(self, events: list[Event], fn, *args):
        """在锁里调用 Session 的方法，主要事件和待取的提示类事件都放进 events。返回主要事件。

        pick_reverted（同一组候选又出现，撤销了刚记下的筹码）发生在这一组候选的推荐之前，放在主要事件前面：
        悬浮窗先把撤销记到上一次的推荐日志编号上，再给这一组候选沿用同一个编号。上一次三选一的结算（紧接着出现另一次
        三选一时由 Session 补发的 picked / missed_pick）同理放在前面（session.split_extra）。
        同一次持锁里顺手换一份新的提示（_renew）：调完以后 Session 的状态可能变了，这一帧后面读的照新的。
        """
        with self.lock:
            try:
                ev = fn(*args)
                extra = self.session.take_events()
            finally:
                self._renew()
        before, after = split_extra(extra)
        events.extend(before)
        if ev is not None:
            events.append(ev)
        events.extend(after)
        return ev

    def _call_frame(self, events: list[Event], seen):
        """把这一帧的三选一读数交给 Session.on_frame（经 _call）。要给推荐时，算推荐的那一段放开锁去算（session.compute_outside，
        见 SelectTracker._advise）：一次几十到几百毫秒，界面线程这段时间不用等它，照常进锁刷新、响应点击；换线程的间隔这段
        时间调短（_ui_first），界面线程拿回解释器锁也不用等。"""
        with compute_outside(self.lock), _ui_first():
            return self._call(events, self.session.on_frame, seen)

    def _hint(self) -> WatchHints:
        """这一帧要看的提示（session.WatchHints）。这一帧还没在锁里调过 Session、还没有一份时，在锁里取一份：一帧最多为它
        进这一次锁。之后每次调完 Session 都换新的（_renew）：这一帧前面各步改的都看得到；界面线程在两次调用之间改的要到下一次
        调完才看得到（见 session.WatchHints）。"""
        hints = self.hints
        if hints is None:
            with self.lock:
                hints = self.hints = WatchHints.of(self.session, self._seen)
        return hints

    def _renew(self) -> None:
        """（持锁时调用）刚调完 Session：换一份新的提示（_hint）。取不到时清掉，下一次读的地方自己再取，出错就报在那一项上。"""
        try:
            self.hints = WatchHints.of(self.session, self._seen)
        except Exception:  # noqa: BLE001  见上
            self.hints = None

    def _trace(self, kind: str, **data) -> None:
        with self.lock:
            self.session.trace(kind, **data)

    def _safe(self, name: str, fn, *args, **kwargs):
        """调用一项子识别：出错时这一项这一帧作废、返回 None，其余照常（见 process 的说明），错误交给 _failed。

        各子识别串在 process 里：一项抛异常就作废整帧的话，已从 Session 取走的事件会丢失，三选一那一帧也因为读轮次、
        认角色、读星数排在 on_frame 之前而不给推荐。不能在持锁时调用：记识别记录要拿同一把锁。
        """
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001  一项子识别出错不能作废整帧
            self._failed(name, e)
            return None

    def _failed(self, name: str, error: Exception) -> None:
        """一项子识别出错（在接住它的 except 里调用）：识别记录里记一条 read:failed，错误连同调用栈留在 errors 里，由调用
        process 的一方照样报出。errors 里的每一项是（这一项的名字 name、异常、调用栈的文字）：调用方按 name 组界面上的提示
        （wording.recog_item_text），调用栈只进错误记录。一个错只由接住它的那一层记一次（接住以后不再往外抛），同一帧里
        不会报两次。"""
        self.errors.append((name, error, traceback.format_exc()))
        self._trace("failed", item=name, reason=f"{type(error).__name__}: {error}")

    # ------------------------------------------------------------ 三选一

    def _read_select(self, img: Frame | np.ndarray, now: float, events: list[Event], force: bool = False):
        """识别三选一。同一个三选一界面上不每帧都做文字识别：

        完整识别过、画面尺寸没变、三个筹码名那一条也没变时，只重查选中的绿框（几毫秒）：推荐给出以后三选一期间每一帧都只做
        这一下，看得勤，点得再快也不容易漏掉「选中」那一下。刷新、换了一批，筹码名那一条变了，马上完整识别；
        不再定时整页重认（剩余刷新次数只在刷新时变，刷新时筹码名也变了）。
        上次读得不完整（_incomplete：概率行读错、图标还没加载等）时只沿用 SELECT_RETRY_S，之后马上完整识别。
        补认（_reread_at，刚出现时读得可疑时排的，见 _suspicious）到点时这一帧也完整识别一次。这一帧还要读左上角（核对等，
        见 _top_due）时，补认和读得不完整的再认（SELECT_RETRY_S 到了）都留到下一帧，这一帧照旧只查绿框
        （慢电脑上完整识别、读左上角各要近 1 秒，挤在同一帧就有约 2 秒看不到绿框）；筹码名那一条变了的照旧马上完整识别，
        这时反过来把读左上角挪到下一帧（_top_waits）：刷新以后的新推荐不等读完轮次、进度条。连着两帧都要完整识别（筹码名那一条
        一直在变、被什么盖着，只查绿框对不上）时第二帧不再挪（_top_deferred），左上角不会一直读不到。
        例外是这次三选一刚出现、读得不完整、还没给过推荐（上一帧做完时的提示里的 select_fresh，见 _last_hints；记账那边
        这时先不提示无法识别，见 session.SelectTracker.hold_frame）：先后换过来，读左上角排到完整重读三张卡之后
        （_top_waits，这一帧 _select_reads 不读左上角）。再认到点的那一帧完整识别，还没到点的照旧只查绿框；筹码名那一条
        变了、当场完整识别的那一帧也不读左上角。重读完了（读全了就给出推荐）以后照常先读左上角：读全以后的补认、给过推荐以后
        读得不完整的再认，和读左上角同一帧到点时照旧挪到下一帧。还没给过推荐就没有要核对的推荐，先把三张卡读对推荐能早出来；
        等记账提示了无法识别（最多等 session.selection.HOLD_MAX_S）、或给了推荐，就不再算刚出现，读左上角不会一直排在后面。
        任何一次完整识别都算补认过了（筹码名变了等别的原因先认了也算），排着的清掉；这一次出错也不再补：认三张卡出错不在下一帧
        接着认。
        完整识别读到的像是被临时盖住了一部分（_covered：给过推荐以后、同一次三选一里，有一两张的名字变了、至少一张没变，变了的
        都读不准而且稀有度读不出）时这一帧不采用：缓存照旧用之前读全的那一组，选中项按之前的位置重新判断（_covered_frame）。
        过了预筛却不是三选一的画面（转场等）也不每帧都识别：画面和上次认定「不是」时一样，就沿用（见 _Unchanged）。没过预筛时
        见 _no_title_band。
        收 Frame 或 ndarray（Frame 上按帧缓存的只用到预筛第一段，其余统一取图）。
        """
        frame = as_frame(img)
        img = frame.image
        deferred, self._top_deferred = self._top_deferred, False
        band = looks_like_overlay_screen(frame)
        if band:
            if self._not_select.entry is self._no_band_entry:
                # 那一条是标题带不在的画面上记下的，这一帧标题带在了，就不算同一个画面：不看指纹，照常识别（标题带只占判定区域的
                # 一小块，带子外的画面差不多时指纹可能还算「没变」）
                self._not_select.entry = None
        elif self._no_title_band(frame, now, force):
            return None
        cache = self._select_cache
        top = self._top_due(now)  # 这一帧要读左上角：补认、读得不完整的再认都挪到下一帧（刚出现、还没给过推荐的除外）
        last = self._last_hints
        # 这次三选一刚出现、读得不完整、还没给过推荐：读左上角排到完整重读三张卡之后
        self._top_waits = (top and cache is not None and _incomplete(cache[0])
                           and last is not None and last.select_fresh)
        due = cache is not None and now - cache[3] >= cache[4]  # 读得不完整、到了再认的时刻
        reread = self._reread_at is not None and now >= self._reread_at and not top
        if (cache is not None and not reread and cache[2] == img.shape[:2]
                and (not due or (top and not self._top_waits))):
            seen = reread_selected(img, cache[0], cache[1])
            if seen is not None:
                self._not_select.interrupt()
                return seen
        if self._not_select.same(img, now, force):
            return None
        self._reread_at = None
        if top and not self._top_waits and not deferred:
            # 这一帧要完整识别（筹码名那一条变了：刷新、换了一批）：读左上角挪到下一帧，刷新以后的新推荐不等读完轮次、进度条；
            # 到点的几样留着，下一帧照常读。上一帧已经这样挪过一次的（连着两帧都要完整识别：筹码名那一条一直在变、被什么盖着）
            # 这一帧不再挪、照常读，左上角不会一直读不到
            self._top_waits = self._top_deferred = True
        info: dict = {}
        seen, how = self._select_read(img, force, reread, info)
        if band:
            self._not_select.record(seen is not None, now)
        else:
            self._record_no_band(seen is not None, now)
        changed = None if seen is None else self._covered(cache, seen, img.shape[:2])
        if changed is not None:
            seen = self._covered_frame(img, cache, seen, changed)
        else:
            self._select_cache = None if seen is None else (
                seen, name_strip(img, seen), img.shape[:2], now, SELECT_RETRY_S if _incomplete(seen) else float("inf"),
            )
            if seen is not None:
                # rarity（图标边框颜色）、partial（只读到共同部分的格子，有才记）不一定每条记录都有，读记录的脚本按可选字段处理；
                # 快读读成的另有 how 为 fast，快读退回、改用完整读的另有 fast_miss（退回的原因），见 _select_read
                extra = {"partial": list(seen.partial)} if any(getattr(seen, "partial", None) or ()) else {}
                extra.update(how)
                self._trace(
                    "select", names=list(seen.names), raw=list(seen.raw), rerolls=getattr(seen, "rerolls", None),
                    probabilities=list(getattr(seen, "probabilities", None) or ()),
                    selected=getattr(seen, "selected", None), problems=list(getattr(seen, "problems", ())),
                    rarity=list(getattr(seen, "rarity", None) or ()), **extra,
                )
        # 快读读成时标题已经和记下位置时的简繁写法对上了，等同完整读认出了简体或繁体的标题
        self._call(events, self.session.on_language, False if how.get("how") == "fast" else info.get("foreign"))
        return seen

    def _select_read(self, img: np.ndarray, force: bool, reread: bool, info: dict) -> tuple:
        """认一次三张卡（_read_select 要完整识别的那一处），交回（结果, 识别记录里另记的几项）。

        开关开着（select_fast）、记着快读要裁的位置、位置是同样大小的画面上记的、不是按热键要求的那一帧（force，手动核对走完整读）、
        也不是补认到点的那一次（reread：本来就是对刚出现时读得可疑的那一次再要一个意见）时，先快读（chip_select.read_chip_select_fast：
        只裁几小块认字、不找字，耗时约是完整读的 15～21%）：读成了就用它，另记 how 为 fast。
        快读退回（标题、名字、概率行、刷新次数、圆环颜色有一样对不上：卡片还在滑进来、刷新时名字在淡入淡出、点选以后、关掉等）、
        没记着位置、开关关着时照旧完整读（chip_select.read_chip_select）；快读退回的另记 fast_miss（退回的原因）。动画那几帧不另外
        等：退回就当场完整读，现在每次都完整读，退回不会比现在慢；刷新时快读读成还没换掉的旧名字的，筹码名那一条接着变，照旧会
        再认，停稳那一帧的读数为准。
        完整读认出了三选一时，照这一次重记位置（chip_select.select_layout）；记不下来（读得不完整、卡片还在滑进来等）时留着原来
        那一份：动画那几帧完整读也读不全，停稳以后原来的位置多半还对。认字都在识别引擎的锁里排队（两开时两路共用一个引擎）。"""
        layout = self._select_layout
        why = ""
        if (self.select_fast and layout is not None and not force and not reread
                and tuple(layout.size) == (img.shape[1], img.shape[0])):
            try:
                seen, why = _timed("select_fast", read_chip_select_fast, img, self.db, layout)
            except Exception as e:  # noqa: BLE001  快读出错不能让三选一认不出：这一次改用完整读，最坏和不快读时一样
                # 那一份位置丢掉（下一次完整读重记），出错写进错误记录，识别记录的 fast_miss 写明出错
                self._select_layout = None
                seen, why = None, f"出错：{type(e).__name__}"
                log_error("三选一快读出错，这一次改用完整读", traceback.format_exc())
            if seen is not None:
                return seen, {"how": "fast"}
        seen = _timed("select", read_chip_select, img, self.db, info=info)
        if seen is not None:
            found = select_layout(img.shape, seen, info.get("lines"))
            if found is not None:
                self._select_layout = found
        return seen, ({"fast_miss": why} if why else {})

    def _no_title_band(self, frame: Frame, now: float, force: bool) -> bool:
        """这一帧没过预筛：是不是就此当作不是三选一（为真时 _read_select 这一帧到此为止）。

        上半段连暗行都没有（第一段 dark_band 没过）：不是三选一，这一帧也不算看过（_Unchanged.interrupt）。
        有暗行、标题带不在该在的地方（第二段没过：战斗、玩家详情弹窗、卡牌商店等）：不做文字识别，当作认过「不是三选一」，静止时
        记下、沿用，_read_hud 照旧不再读这种画面上读不到东西的轮次。
        这样有暗行的帧有两种例外，照常走只查绿框和完整识别：
          按热键的那一帧（force）：第二段只靠游戏的排版，排版万一变了，手动还认得出；
          三选一已经认出来了（_select_cache）：第二段只管三选一还没认出来的时候。标题带一时被盖住不算界面关了，不然连着两帧
          （session.selection.GONE_FRAMES）就按关掉结算，盖住的东西一走又算新的一次、重新给推荐；排版万一变了，按热键认出来以后
          也才接得下去。筹码名那一条没变就照旧只查绿框；界面真的关了，完整识别一次否定、缓存清掉，只多这一次。"""
        dark = frame.dark_band()
        if dark and (force or self._select_cache is not None):
            return False
        self._select_cache = None
        if not dark:
            self._not_select.interrupt()
        elif not self._not_select.same(frame, now):
            self._record_no_band(False, now)
        return True

    def _record_no_band(self, found: bool, now: float) -> None:
        """标题带不在的一帧认过了（_Unchanged.record）；这一帧记下了「不是」的话另外记着是哪一条（_no_band_entry，见 _read_select）。"""
        self._not_select.record(found, now)
        if self._not_select.matched is not None:
            self._no_band_entry = self._not_select.matched

    def _covered(self, cache, seen, shape) -> list[int] | None:
        """这一次完整识别（seen）像不像被什么临时盖住了一部分（游戏弹出的小窗、动画）：像的话返回名字变了的那几张（从 0 数），
        不像时为 None（照常采用）。要同时满足：
          和之前读全的那一组（cache：上一次完整识别的缓存，读得完整、画面大小没变）相比，有一张或两张的名字变了、至少一张没变，
            变了的每一张都读不准（读不出、只读到几个筹码名的共同部分、或匹配度低，见 _weak_slot），而且这一张的稀有度读不出
            （rarity 里是 None；没有 rarity 这一项的读数不算）；
          刷新次数没少（读不出的不算少；之前读到 2 次以上、这一次读成 0 的按刷新按钮也被挡住算，一次刷新到不了 0）；
          记账那边这次三选一已经给过推荐（WatchHints.select_told）、这一帧不是紧接着的另一次三选一（another_select）、不在等候选
            读全（holding：刚出现、刷新后等着的时候读不全是意料之中的）。
        这几样有一样不满足就照常采用：三张全变了的（刷新、紧接着的另一次，包括刷新动画里三张都读不准、次数还没变的几帧）、刷新以后
        的新一组（刷新次数少一）、紧接着的另一次、刚出现还没给过推荐时的读不全、读全以后又读对了的（变了的那张读得准）、名字没变的
        补认，都和原来一样。刷新、换成另一次都会把三张全换掉，三张全变了的交给记账按现有的规矩分（拿不准的当刷新）：刷新动画里
        次数可能晚一点才变，三张都读不准的那几帧也可能是刷新，不当成被盖住，记账才知道候选换过了（刷新前点过的选中作废）。所以
        给过推荐以后只有一部分名字变了、变了的都读不准、连稀有度也读不出，才多半是画面被临时盖住了。
        要问记账的那几样在最后问：这一帧为读提示进的那一次锁（_hint，读到的提示后面照用）。"""
        if cache is None or cache[2] != shape:
            return None
        prev = cache[0]
        if _incomplete(prev):
            return None
        names, old = list(getattr(seen, "names", None) or ()), list(getattr(prev, "names", None) or ())
        rarity = getattr(seen, "rarity", None)
        if not names or len(names) != len(old) or not isinstance(rarity, (list, tuple)) or len(rarity) != len(names):
            return None
        changed = [i for i, (a, b) in enumerate(zip(names, old)) if a != b]
        if (not changed or len(changed) == len(names) or any(rarity[i] is not None for i in changed)
                or not all(_weak_slot(seen, i) for i in changed)):
            return None
        n, before = getattr(seen, "rerolls", None), getattr(prev, "rerolls", None)
        if n is not None and before is not None and n < before and not (n == 0 and before >= 2):
            return None
        self._seen, self.hints = seen, None  # 提示里的「是不是紧接着的另一次」按这一帧的读数算
        try:
            hints = self._hint()
        except Exception:  # noqa: BLE001  取不到提示时照常采用（之后读提示的地方自己再取，出错报在那一项上）
            return None
        if not hints.select_told or hints.another_select or hints.holding:
            return None
        return changed

    def _covered_frame(self, img: np.ndarray, cache, seen, changed: list[int]):
        """不采用被临时盖住的这一帧（见 _covered）：缓存照旧用之前读全的那一组（位置、筹码名那一条）；这一帧的选中项按之前的
        位置重新判断（同 reread_selected，筹码名那一条对不上就当这一帧没看到选中）。交给记账的是之前那一组、标着沿用
        （reread），记账照旧是之前那一组：同一个局面不重算推荐，也不添这一帧新冒出的「匹配度低」提示。识别记录另记一条
        select_covered（读到的、为什么不采用、按之前的位置看到的选中项），这一帧的画面留一张（covered_shot）。返回交给记账的那一份。"""
        prev, strip = cache[0], cache[1]
        again = reread_selected(img, prev, strip)
        kept = again if again is not None else _reread_copy(prev, None)
        self._seen = kept
        slots = "、".join(f"第 {i + 1} 张" for i in changed)
        self._trace(
            "select_covered", names=list(seen.names), raw=list(seen.raw), rerolls=getattr(seen, "rerolls", None),
            probabilities=list(getattr(seen, "probabilities", None) or ()), selected=getattr(seen, "selected", None),
            problems=list(getattr(seen, "problems", None) or ()), rarity=list(seen.rarity), slots=list(changed),
            kept=list(prev.names), reselected=getattr(kept, "selected", None),
            reason=f"给过推荐以后同一次三选一里{slots}的名字变了，读得不准、稀有度读不出，像是被临时遮挡，这一帧不采用",
        )
        self._note_covered(seen, changed)
        return kept

    def _note_covered(self, seen, changed: list[int]) -> None:
        """留下不采用的这一帧（_covered_frame）：交来识别的整幅画面复制一份，连同本地时刻和读到的整个换掉 covered_shot。只给
        诊断用：复制出错时这一次不留、之前留的照旧，不算识别出错（同 _note_unread）。"""
        try:
            details = {"slots": list(changed), "raw": list(seen.raw), "names": list(seen.names),
                       "rarity": list(seen.rarity), "selected": getattr(seen, "selected", None)}
            shot = (_wall(), _copy_frame(self.last_frame), details)
        except Exception:  # noqa: BLE001  留不下就不留
            return
        self.covered_shot = shot

    def _select_hidden(self, img: Frame | np.ndarray, force: bool = False) -> bool:
        """这一帧没有三选一：三选一正要按关掉处理（WatchHints.select_closing，界面消失后的第 GONE_FRAMES 帧起）时，看一眼
        画面上有没有「您当前正处于隐藏UI状态」（chip_select.hidden_ui_visible，只识别已知位置的一条，约 10 毫秒），平时不看。
        认出了就是玩家点了三选一界面上的「隐藏」：交给 Session.on_select_hidden，当三选一还开着。
        隐藏期间每一帧都是这样的帧（不算看不到三选一，一直正要按关掉处理），每帧看一次；隐藏着的时候识别按平时的间隔取帧。
        按热键要求的那一帧（force）也看一眼：手动模式下三选一出来以后没按过热键就被隐藏了，Session 不知道这一次三选一，
        不会正要按关掉处理。认出了就之后每帧照样看（_hidden_unseen），悬浮窗手动模式据此跟随，点「显示」、三选一回来时照常
        认出、给推荐；那行字没了就不再看。这一次 Session 那边没开着，交给它不起作用（SelectTracker.hidden）。"""
        closing = self._hint().select_closing
        was, self._hidden_unseen = self._hidden_unseen, False  # 看那行字出错时不再接着看
        if not (closing or force or was):
            return False
        hidden = bool(_timed("select_hidden", hidden_ui_visible, img))
        self._hidden_unseen = hidden and not closing
        return hidden

    def _note_unread(self, img: np.ndarray) -> None:
        """这一帧的三选一交给记账以后（process，在 _call_frame 之后）：记账说这一帧新读的三选一有筹码没读出来、又不在刷新后
        等新的候选读全的那段时间里（这一次调完在锁里取的提示 select_unread，见 session.SelectTracker.unread），就留下这一帧的
        整幅画面（img：交来识别的原图，没去黑边），连同现在的本地时刻整个换掉之前留的（unread_shot）。复制一份，不编码。
        这一张只给诊断用，不影响识别：复制出错（内存不够等）时这一次不留，之前留的照旧留着，也不算这一帧识别出错（不经 _safe、
        不进 errors，悬浮窗不提示「识别出错」），这一帧的读数照常交出。"""
        try:
            hints = self.hints
            if hints is None or not hints.select_unread:
                return
            shot = (_wall(), _copy_frame(img))
        except Exception:  # noqa: BLE001  留不下就不留，见上
            return
        self.unread_shot = shot

    def _note_picked(self, img: np.ndarray, sighting=None) -> None:
        """刚交给记账的读数让记账记下了选中的是哪张（这一次调完在锁里取的提示 select_noted：选中项变了、记下了一张筹码，见
        session.SelectTracker.noted）：留下记下它用的那一帧，连同本地时刻、第几张、哪张筹码整个换掉之前留的（pick_shot）。
        img 是识别流程交来识别的整幅画面（没去黑边），快速盯绿框看到的（sighting）是它截的那一块，另记 watch 和那一块在客户区
        里的位置。同 _note_unread：复制一份、不编码；复制出错时这一次不留、之前留的照旧，不算识别出错。"""
        try:
            hints = self.hints
            noted = None if hints is None else hints.select_noted
            if not noted:
                return
            details = {"slot": noted[0], "chip": noted[1]}
            if sighting is not None:
                details["watch"] = True
                details["region"] = list(sighting.target.region)
            shot = (_wall(), _copy_frame(img), details)
        except Exception:  # noqa: BLE001  留不下就不留，见上
            return
        self.pick_shot = shot

    # ------------------------------------------------------------ 快速盯绿框（vision/pick_watch.py）

    def _watch_target(self, seen):
        """这一帧以后要盯的那一份（pick_watch.Target）：三选一显示着、有完整识别的缓存（_select_cache）时照它建，缓存、去掉的
        黑边、客户区大小都没变时沿用原来那一份（被临时盖住、不采用的那一帧缓存照旧，也沿用）；否则 None（不盯）。缓存里的读数
        没有位置信息时（测试里的假读数）建不成，也是 None。"""
        cache, client_img = self._select_cache, self.last_frame
        if seen is None or cache is None or client_img is None:
            self._watch_from = None
            return None
        client = (int(client_img.shape[1]), int(client_img.shape[0]))
        current, made = self.pick_watch.target, self._watch_from
        if current is not None and made is not None and made[0] is cache and made[1:] == (self.offset, client):
            return current
        self._watch_from = (cache, self.offset, client)
        return self.pick_watch.make_target(cache[0], cache[1], cache[2], self.offset, client)

    def _watch_frame(self, seen, now: float) -> tuple[list, list]:
        """认完这一帧的三选一以后（process）：换上这一帧以后要盯的（_watch_target；不在三选一、被隐藏时为 None，盯绿框的线程
        停下），取走盯绿框的线程看到的选中项，按截到画面的时刻分成两份返回：早于这一帧画面（now）的，在这一帧的读数之前交给
        记账；晚于的，在这一帧的读数之后交（还是同一组时；这一帧看不到三选一时也交，那时三选一其实还开着）。换了一组以后才
        看到的上一组不交（画面上已经是新的一组了）。这一帧把同一组又完整识别了一次、换了一份要盯的（_same_offer）不算换了一组：
        补认、读得不完整时的再认也可能是很慢的一帧，这一帧做的时候看到的照样交，按这一帧的读数交（截的那一块的位置照旧）。
        PickWatch.publish 先等盯绿框的线程把正在查的那一次查完再取，先后不乱：界面关掉之前看到的选中项，一定在记账处理看不到
        三选一的那一帧（按关掉处理之前）交进去。"""
        target = self._watch_target(seen)
        got = self.pick_watch.publish(target)
        pending = [s for s in (*self._watch_carry, got) if s is not None]
        self._watch_carry = []
        before, after = [], []
        for s in sorted(pending, key=lambda s: s.at):
            if s.at < now:
                before.append(s)
            elif s.target is target or seen is None:
                after.append(s)
            elif _same_offer(s.target, target):
                after.append(dataclasses.replace(s, target=dataclasses.replace(s.target, seen=target.seen)))
        return before, after

    def _watch_later(self) -> list:
        """这一帧交给记账以后，盯绿框的线程在这一帧做的时候看到的（还是现在要盯的那一组的）：在这一帧的读数之后交。"""
        got = self.pick_watch.take()
        return [got] if got is not None and got.target is self.pick_watch.target else []

    def _deliver_watch(self, events: list[Event], sightings) -> None:
        """把快速盯绿框看到的选中项交给记账：和每帧开头的快速复查交回的读数一样走 Session.on_frame（_call_frame，同一把锁），
        沿用那一组的完整识别、只换选中项，另标 watch（识别记录 read:selected 带 watch: true）。记账因此记下了选中的是哪张时
        留下截的那一块（_note_picked）。"""
        for s in sightings:
            self._safe("frame", self._call_frame, events, _reread_copy(s.target.seen, s.slot, watch=True))
            self._note_picked(s.image, s)

    def _trim(self, img: np.ndarray) -> tuple[np.ndarray, tuple[int, int]]:
        """去掉画面四周的黑边（trim_borders）：画面大小和上一帧一样时带上上一帧最后用的范围（_border），
        黑边里零星冒出的小亮点不会让这一帧少去黑边；大小变了重新找。算完记下这一帧最后用的范围。"""
        h, w = img.shape[:2]
        keep = self._border[1] if self._border is not None and self._border[0] == (h, w) else None
        out, offset = trim_borders(img, keep)
        x0, y0 = offset
        self._border = ((h, w), (x0, y0, x0 + out.shape[1], y0 + out.shape[0]))
        return out, offset

    def process(self, img: np.ndarray, now: float, force: bool = False) -> list[Event]:
        """now 是单调时钟的秒数，用来控制各项识别多久做一次。

        force：这一帧是用户按热键要求的，不沿用「画面没变、上次认定不是」的结论，一律重新识别。
        三选一的帧：手上的轮次、进度条够新时先给推荐、下一帧再核对，推荐给出以后只盯绿框（_select_reads）；例外只有刚出现时
        读得可疑的补认一次、读左上角没成功时隔 retry_s 再读（不设 pending，到点以后照三选一期间的节奏取到的那一帧做）。刚出现
        时读得不完整、还没给过推荐的，先完整重读三张卡、读左上角排到后面（_read_select）。三选一交给记账以后，记账说这一帧
        有筹码没读出来（刷新动画的中间帧除外）时留下这一帧的画面（_note_unread、unread_shot），记下了选中的是哪张时也留一张
        （_note_picked、pick_shot）。快速盯绿框的线程看到的选中项（vision/pick_watch.py）按截到画面的先后排在这一帧的读数之前
        或之后交给记账（_watch_frame）：这一帧看不到三选一时，之前看到的先交、再按看不到处理。不在
        三选一时一帧最多做一项读数，其余到期的排到接下来的几帧（_idle_reads；还有排着的时 pending 为真）：每一帧开头照旧先看
        是不是三选一，三选一画面出来时不用等别的读数做完。停在主菜单、底栏和开始按钮那两块没变时（主菜单空转，_menu_idle）
        不看「查看筹码」面板和任务栏、一项读数都不排，三选一照旧每帧看。
        每项子识别各自接住异常（_safe），一项出错不影响整帧：出错的那一项这一帧作废，其余照常，三选一那一帧照样给推荐；
        已经从 Session 取走的事件照常返回、照常记进识别记录（finally 里的 _finish）。接住的错误留在 errors 里，由调用方
        照样报出（悬浮窗：写进错误记录、提示「识别出错」）。认三选一出错时这一帧到此为止：之后各项都按是不是三选一分路。
        """
        events: list[Event] = []
        self.errors = []
        self.select_failed = self.select_hidden = False
        self.pending = self._top_waits = False
        self._last_hints, self.hints, self._seen = self.hints, None, None
        self.last_frame = img  # 「导出诊断」存的原图（客户区整幅，未去黑边）
        try:
            self._safe("window", self._check_window, img, now, events)
            if min(img.shape[:2]) < MIN_FRAME:
                # 比「窗口过小」的门槛还小得多：只报「窗口过小」（_check_window 已报），其余识别对着空画面会出错
                return events
            trimmed = self._safe("trim", self._trim, img)
            img, self.offset = trimmed if trimmed is not None else (img, (0, 0))
            # 这一帧的统一载体：各识别模块从它取图，也把「同一帧只算一次」的中间结果记在它上面（见 frame.Frame）
            frame = Frame(img, self.offset)
            # 上一帧是不是三选一（这一帧的三选一是不是刚出现）。刚从暂停恢复时不算：暂停期间看不到画面（见 resumed）
            was_visible = self.chip_select_visible and not self._after_pause
            try:
                seen = self._read_select(frame, now, events, force)
            except Exception as e:  # noqa: BLE001
                # 不知道这一帧是不是三选一：当成「不是」交给 Session 会算作界面关了一帧（连着两帧就结算这一次三选一），
                # 其余各项也按它分路，所以这一帧到此为止
                self.select_failed = True
                self._failed("select", e)
                return events
            self._after_pause = False
            self.chip_select_visible = seen is not None
            if self.board is not None:
                # 两开时另一路据此让路（被隐藏着的不算显示着）；这次的推荐给过以后不再要它让，见 SelectBoard
                self.board.note(self, seen is not None and not self._hint().select_told, now)
            # 快速盯绿框：换上这一帧以后要盯的，取走看到的选中项（排在这一帧的读数之前、之后交给记账的两份）。早于这一帧画面的
            # 先交：这一帧的其余读数（读左上角、开新的一局等）都在它们之后
            before, after = self._safe("watch", self._watch_frame, seen, now) or ((), ())
            self._deliver_watch(events, before)
            if seen is not None:
                self._hidden_unseen = False  # 三选一回来了（或又出来一次）：照常认，不再单独看那行字
                # 「是不是紧接着出现的另一次三选一」（WatchHints.another_select）要按这一帧的读数算：认三选一时取的那一份
                # 不算（_covered 按这一帧的读数取的照用），下一次读时重取
                if self._seen is not seen:
                    self._seen, self.hints = seen, None
                self._select_reads(frame, seen, now, events, was_visible)
            else:
                self._select_star_tries = 0
                self._select_hero_at = float("-inf")
                self._recheck_round = self._recheck_progress = self._check_due = False
                self._drop_check_at = None
                self._clear_select_retries()
                self.select_hidden = bool(self._safe("select_hidden", self._select_hidden, frame, force))
            if seen is not None or self.select_hidden:
                self._safe("quiet", self._note_in_game, now)  # 三选一显示着（被玩家隐藏着也算）：在对局里
            if self.select_hidden:
                # 三选一被玩家隐藏了：当它还开着，这一帧不算看不到它，不记下选中的、不撤销，推荐留着
                self._safe("frame", self._call, events, self.session.on_select_hidden)
            else:
                self._safe("frame", self._call_frame, events, seen)
                if seen is not None:
                    self._note_unread(self.last_frame)  # 自己接住出错，不报识别出错（见 _note_unread）
                    self._note_picked(self.last_frame)  # 同上
            self._deliver_watch(events, after)
            if seen is not None:
                self._deliver_watch(events, self._watch_later())
            if seen is None:
                if self._safe("menu", self._menu_idle, frame, now, force):
                    # 主菜单空转的一帧（_menu_idle）：「查看筹码」面板、任务栏只在对局里有，不看（这一帧也不是面板）；没做完的
                    # 这一轮作废，读数一项都不排。连着多久看不到对局里的读数照常算（这一帧没有对局读数）
                    self.panel_visible = False
                    self._hud_rest = None
                else:
                    self._safe("panel", self._read_panel, frame, now, events)
                    if self._safe("task_panel", self._task_panel_opened, frame):
                        # 任务栏刚打开：这一帧就读轮次连同任务栏，不等 hud_ms，也不等正在做的一轮做完（任务栏可能很快又关上）。
                        # 没做完的那一轮作废，从读轮次起重新排（没到点的项不占帧）
                        self._last_hud = float("-inf")
                        self._hud_rest = None
                    self._idle_reads(frame, now, events, force)
                self._safe("quiet", self._count_quiet, now)
            else:
                self.panel_visible = False
                if self._menu is not None:
                    self._safe("menu", self._menu_leave, "select")  # 三选一显示着：在对局里，不再空转
            if seen is None:
                self._safe("victory", self._check_victory, frame, now, events)  # 通关画面：先看颜色，像了再认字
        finally:
            self._safe("note", self._finish, events)
        return events

    def _select_reads(self, frame: Frame, seen, now: float, events: list[Event], was_visible: bool) -> None:
        """三选一的帧在交给 Session 之前读的几项。三选一界面上也看得到左上角的轮次、进度条，左下角的立绘和
        左侧的玩家列表；纯手动模式下几乎只在三选一时截图，不在这里读的话轮次和角色一直是旧的。

        新出现的一次三选一（上一帧不是三选一，或紧接着出现的另一次）：手上的轮次、进度条够新（_readings_fresh）时不读，直接
        用它们给推荐，这一帧到此为止，推荐马上交给界面；下一帧（三选一期间按 active_ms）再读一次核对（_check_due），读到的和
        用来算推荐的不同时，Session 按新的数重算推荐（局面变了才重发，同一次三选一推荐日志以后一条为准）。不够新时照旧先读完
        （轮次、进度条）再给推荐；先读完的那一次不认角色（推荐不等它），给出推荐以后的下一帧按间隔认一次（_hero_next）。
        推荐给出以后只盯绿框：核对只做一次，之后到三选一关掉都不再读轮次、进度条，不再定时认角色；连读两次才采用的规矩照样适用：
        往前大幅跳变的轮次、和已采用的不同的进度条下一帧补读一次，变小的轮次（可能开了新的一局，宁可确认得慢一点）隔 hud_ms
        补读一次。同一次三选一里刷新了（筹码名变了，完整识别）照常马上给推荐，
        不另外核对；那一帧正好要读左上角的，挪到下一帧（_read_select 定 _top_waits）。
        只盯绿框的两个例外：刚出现时读得可疑（_suspicious；刚出现那一帧没读全时按第一次读全的那一次判）时排一次
        补认，reread_s 后完整识别一次三张卡（_read_select；到点的那一帧要读左上角时挪到下一帧），认出的交给 Session 照常处理：
        和原来不同就按新的重算推荐（同一次三选一，推荐日志以后一条为准），相同不重发；读左上角（先读完、核对、补读、确认）出错
        或读不出轮次时隔 retry_s 再读，每次三选一最多再读 HUD_RETRIES 次（_select_hud）；轮次被挡住时（_occluded）读左上角
        只在标记左边那一小块认字，再读按 occluded_s 的间隔，只因为读不出轮次而再读时不重读进度条。三选一开着时开了新的一局、
        换了角色，再读的次数重新数。
        刚出现那一帧读得不完整、还没给过推荐（记账先不提示无法识别）：核对、补读、确认、再读都排到完整重读三张卡之后
        （_read_select 定 _top_waits），重读以后的下一帧照常读。
        角色待识别时不给推荐（hero_needed）：照旧一出现就认一次角色，之后按 hud_ms 再试。星数照旧读前几帧（读到为止），只走
        快的那一路（_read_stars 的 in_select）。"""
        # 不在三选一时没做完的这一轮读数作废：三选一有自己的读法，关掉以后各项照各自的间隔再读
        self._hud_rest = None
        self._safe("begin_offer", self._begin_offer, events)
        another = self._safe("another_select", self._another_select, seen)
        if another:
            self._select_star_tries = 0  # 紧接着出现的另一次三选一（两次之间没有棋盘画面）：这一次也读几帧星数
        if not was_visible or another:
            # 上一次三选一的补读、补认、再读不带到这一次：这一次的核对、补读各自从头算
            self._recheck_round = self._recheck_progress = False
            self._drop_check_at = None
            self._clear_select_retries()
            self._suspect_due = True  # 这一次读得可不可疑：读全了就判（见下），没读全时等第一次读全的那一次
            self._check_due = self._readings_fresh(now)
            if not self._check_due:
                self._select_hud(frame, now, events)
                # 角色复查不排在推荐之前（模板比对，慢电脑上约 0.5 秒）：读到了轮次（在对局里、画面没被压暗）就下一帧按间隔认
                self._hero_next = self._hud_round is not None
        else:
            hints = self._hint()
            if (hints.games, hints.stars_hero) != self._retry_owner:
                self._hud_retries = 0  # 开了新的一局、换了角色（这一帧之前）：再读左上角的次数重新数
            drop_due, retry_due = self._hud_due(now)
            if self._top_waits:
                # 这次三选一刚出现、读得不完整、还没给过推荐：读左上角排到完整重读三张卡之后（_read_select），这一帧不读；
                # 到点的几样（核对、补读、确认、再读）都留着，重读以后的下一帧照常读
                pass
            elif self._check_due or self._recheck_round or drop_due or retry_due:
                # 只是再读（核对、补读、确认都不在这一帧）、上一次进度条也读到了：只读轮次，不重读进度条
                progress = self._check_due or self._recheck_round or drop_due or self._retry_progress or not retry_due
                self._check_due = False
                if drop_due:
                    # 确认变小的轮次：这一次读了就算确认过，之后读不出、出错时只按 retry_s 再读（见 _round_follow_up）
                    self._drop_check_at, self._drop_checking = None, True
                if retry_due:
                    self._hud_retry_at = None
                    self._hud_retries += 1
                self._select_hud(frame, now, events, progress=progress)
            elif self._recheck_progress:
                start = len(self.errors)
                self._safe("progress", self._read_progress, frame, events, in_select=True)
                if len(self.errors) > start:
                    # 补读进度条出错：_read_progress 没走到清掉它的那一步，这里清掉，不在下一帧接着读；隔 retry_s 连同轮次再读
                    self._recheck_progress = False
                    self._retry_progress = True
                    self._retry_later(now)
            elif self._hero_next:
                self._hero_next = False
                self._safe("hero", self._read_hero_due, frame, now, events)
        if self._suspect_due and not _incomplete(seen):
            # 这次三选一第一次读全（刚出现那一帧，或之后第一次读全的那一次）：判一次可不可疑，可疑的过 reread_s 补认。没读全的
            # 照旧按 SELECT_RETRY_S 再完整识别（_read_select），读全以前不判：出场动画里没读全、读全时概率行却读错的也补得到
            self._suspect_due = False
            if self._suspicious(seen):
                self._reread_at = now + self.reread_s
        # 角色待识别时不给推荐（hero_needed）：三选一一出现就立即认一次角色，之后按 hud_ms 再试
        pending = self._hint().hero_pending
        if pending and now - self._select_hero_at >= self.hud_s:
            self._select_hero_at = now
            if self._last_hero != now:  # 这一帧读轮次时已经认过了就不再认
                self._safe("hero", self._read_hero, frame, now, events)
        if self._select_star_tries < SELECT_STAR_TRIES:
            got = self._safe("stars", self._read_stars, frame, events, in_select=True)
            self._select_star_tries = SELECT_STAR_TRIES if got else self._select_star_tries + 1
        hints = self._hint()
        self._retry_owner = (hints.games, hints.stars_hero)  # 这一帧做完时是哪一局、哪个角色（下一帧比，见上）

    def _suspicious(self, seen) -> bool:
        """三选一刚出现时的读数可疑、要补认（见 SELECT_REREAD_MS）：刷新次数读成 0（刷新按钮可能正好被
        表情气泡挡住；刷新次数用完时按钮本来就不在，也读成 0，分不出来，照样补认一次），或者概率行三项加起来是 100（识别模块
        已挡掉加起来不是 100 的）、却不是概率表里的任何一行（pool.difficulty_of，Session 认是不是同一次三选一也按它）。
        读不到概率行、筹码名没读全这类读得不完整的，照旧按 SELECT_RETRY_S 再认（_incomplete），不在这里算：判的是这次三选一
        第一次读全的那一次（刚出现那一帧读全了就是那一帧，见 _select_reads）。"""
        probs = getattr(seen, "probabilities", None)
        return getattr(seen, "rerolls", None) == 0 or (bool(probs) and difficulty_of(self.db, probs) is None)

    def _hud_due(self, now: float) -> tuple[bool, bool]:
        """三选一显示着时，（确认变小的轮次到点了没有, 再读左上角到点了没有）（见 _select_reads、_top_due）。"""
        return (self._drop_check_at is not None and now >= self._drop_check_at,
                self._hud_retry_at is not None and now >= self._hud_retry_at)

    def _top_due(self, now: float) -> bool:
        """三选一已经显示着（不是刚出现那一帧、不是刚从暂停恢复）时，这一帧 _select_reads 要不要读左上角：核对先给的推荐、补读
        跳变的轮次、确认变小的轮次、再读，或只补读进度条；先读完再给推荐以后要认一次角色（_hero_next）也算在内（模板比对，
        慢电脑上也近 0.5 秒）。同一帧补认、或读得不完整时的再认（SELECT_RETRY_S）也到点时，这一帧只读
        左上角，那一次完整识别挪到下一帧（_read_select）：慢电脑上一次完整识别、一次读左上角各要近 1 秒，挤在
        同一帧就有约 2 秒看不到绿框。读左上角的这几样各有上限（核对、补读、确认、认角色各一次，再读最多 HUD_RETRIES 次），完整识别
        最多往后挪这几帧；挪的时候不设 pending，照三选一期间的节奏取下一帧。
        例外：这次三选一刚出现、读得不完整、还没给过推荐时反过来，读左上角排到完整重读三张卡之后；筹码名那一条变了、这一帧要
        完整识别时也是（_read_select 定 _top_waits），到点的这几样留着、下一帧照常读；同样不设 pending。"""
        if not self.chip_select_visible or self._after_pause:
            return False
        return (self._check_due or self._recheck_round or self._recheck_progress or self._hero_next
                or any(self._hud_due(now)))

    def _select_hud(self, frame: Frame, now: float, events: list[Event], progress: bool = True) -> None:
        """三选一的帧上读一次左上角：轮次，progress 为真时连同进度条（_read_hud）。轮次被挡住时（_occluded_crop）不再完整读，
        只在标记左边那一小块认字（_peek_round），进度条单独读。读轮次或读进度条出错、没读到轮次时这一次不算数：再读一次
        （_retry_later：被挡住时隔 occluded_s，否则隔 retry_s），每次三选一最多再读 HUD_RETRIES 次。只因为读不出轮次而再读时
        不重读进度条（这一次已经读到了，进度条也不会被头像挡住）；要连同进度条却没读成的（读进度条出错，或读轮次出错、没走到
        读进度条那一步）再读时连同（_retry_progress）。
        出错的那一项不在下一帧接着补读（_recheck_round、_recheck_progress 清掉，交给再读），一直出错也不会每一帧都读；左上角只是
        被挡了一下的，再读时读到了，核对、确认新的一局照常（读到轮次以后补读、确认的规矩见 _round_follow_up）。"""
        start = len(self.errors)
        self._hud_round = None
        occluded = self._occluded_crop(frame) is not None
        if occluded:
            self._safe("hud", self._peek_round, frame, now, events, in_select=True)
            if progress:
                self._safe("progress", self._read_progress, frame, events, in_select=True)
        else:
            self._safe("hud", self._read_hud, frame, now, events, in_select=True, progress=progress)
        failed = {name for name, _, _ in self.errors[start:]} & {"hud", "progress"}
        if "hud" in failed:
            self._recheck_round = False  # 读轮次（连同交给 Session）出错：_read_hud 没走到排补读那一步
        if "progress" in failed:
            self._recheck_progress = False  # 读进度条出错：同上
        if progress:
            self._retry_progress = "progress" in failed or (not occluded and "hud" in failed)
        if failed or self._hud_round is None:
            self._retry_later(now)

    def _retry_later(self, now: float) -> None:
        """三选一上读左上角没成功：这次三选一还没再读满 HUD_RETRIES 次时，隔一会儿再读一次（_select_reads 到点时读）；
        已经排着的不往后推。轮次被挡住时（读的时候在数字那一行看见了标记）按 occluded_s 再看一眼，和平时一样；否则按 retry_s。"""
        if self._hud_retry_at is None and self._hud_retries < HUD_RETRIES:
            self._hud_retry_at = now + (self.occluded_s if self._occluded is not None else self.retry_s)

    def _readings_fresh(self, now: float) -> bool:
        """三选一刚出现时，手上的轮次、进度条够不够新，能不能先给推荐、下一帧再读一次核对。
        这几种照旧先读完再给推荐：手动模式（平时不看画面，没有新读数）；刚从暂停恢复（游戏刚切回前台等）、之后还没读到过轮次；
        本局还没读到过轮次（新的一局刚开始等）；最近一次看见轮次标记（读到轮次，或数字被挡住、只看见标记）已经过了
        after_game_ms（默认 30 秒）。
        最后一条按 after_game_ms 算，不按 hud_ms 的几倍：读不到轮次多半是战斗、面板这类对局里的画面挡住了左上角，常常一挡就
        十几秒，一局里的轮次这段时间通常不变（进度条可能因为击倒推进，下一帧核对时就改过来），按 hud_ms 算的话战斗之后出现的
        三选一都要先读完，慢电脑上就超过 2 秒；过了 after_game_ms 识别流程本来就当作可能离开了对局（开始找加载画面），
        手上的轮次不再算数。按看见标记算：数字被玩家列表的头像挡住时轮次读不出、标记还在，一局里头像可能一直晃着挡住数字，
        手上的轮次只是因为被挡住才旧的，照样先给推荐、下一帧核对。"""
        if self._manual or self._stale:
            return False
        return self._hint().round_known and now - self._mark_seen_at < self.after_game_s

    def _begin_offer(self, events: list[Event]) -> None:
        """这一帧是三选一界面，而上一局已结束（结算画面等之后，游戏切到后台期间开了下一局时会这样）：先开新的一局
        （Session.begin_offer），这一帧立即读轮次、角色和星数，这次推荐就用上。不先开的话，这些读数在 on_frame 之前
        按「已结束」处理掉（轮次只算一票、星数不起作用），这一次三选一的读星数次数也白白用掉。
        已有筹码还要靠「查看筹码」面板时（WatchHints.panel_needed）也在读轮次之前告诉 Session：这一局先看到的是三选一，
        这一帧随后读到的轮次再小也不算从开局看起（见 Session._entered_game）。"""
        hints = self._hint()
        if not (hints.game_over or hints.panel_needed):
            return
        ev = self._call(events, self.session.begin_offer)
        if ev is not None and ev.kind == "newgame":
            self._last_hud = self._last_hero = self._select_hero_at = float("-inf")
            self._select_star_tries = 0
            self._hero_misses = 0

    def _another_select(self, seen) -> bool:
        """这一帧的三选一（seen）是不是紧接着出现的另一次（Session.starts_another_select，取在这一帧的提示里：
        WatchHints.another_select）：是的话这一次也读几帧星数。由 process 经 _safe 调用（_safe 出错时记识别记录要拿锁，
        不能在锁里调 _safe）。"""
        return self._hint().another_select

    def _finish(self, events: list[Event]) -> None:
        if events:
            with self.lock:
                self.session.note(events)

    # ------------------------------------------------------------ 不支持的情况

    def _check_window(self, img: np.ndarray, now: float, events: list[Event]) -> None:
        """窗口过小每帧查；独占全屏隔 FULLSCREEN_CHECK_S 查一次。

        界面按宽、高中较小的比例缩放（chip_select.ui_scale 的口径），所以按缩放比例判断：比 16:9 更宽的窗口（例如 21:9）
        宽度够了，高度不够时界面照样很小。
        """
        h, w = img.shape[:2]
        changed = False
        with self.lock:
            small = min(w / 1920, h / 1080) < MIN_UI_SCALE
            changed |= self.session.set_unsupported("small_window", small, size=f"{w}×{h}")
        if now - self._last_fullscreen >= FULLSCREEN_CHECK_S:
            self._last_fullscreen = now
            try:
                from .capture import exclusive_fullscreen

                full = exclusive_fullscreen(platform=self.platform)
            except Exception:  # noqa: BLE001  查询失败时不提示
                full = False
            with self.lock:
                changed |= self.session.set_unsupported("fullscreen", full)
        if changed:
            events.append(Event("status"))

    # ------------------------------------------------------------ 对局画面

    def _task_panel_opened(self, img: Frame | np.ndarray) -> bool:
        """任务栏刚打开（上一帧还没开）、而且地图还要靠它识别。"""
        if not self.tasks_now:
            return False
        want = self._hint().wants_map
        open_now = want and task_panel_visible(img)
        opened = open_now and not self._tasks_open
        self._tasks_open = open_now
        return opened

    def _idle_reads(self, frame: Frame, now: float, events: list[Event], force: bool) -> None:
        """不在三选一时这一帧的读数：一帧最多做一项，其余到期的排到接下来的几帧。每一帧开头照旧先看是不是
        三选一（process），任何两项读数之间都先看一眼，三选一画面出来时不用等别的读数做完。

        这一轮没做完的接着做；没有在做的一轮、到了 hud_ms 就开新的一轮（先读轮次，见 _read_hud）；这一轮做完了、这一帧还没读过
        才看离开对局后的整幅识别（_check_loading）。没到点、不用读的项不占这一帧。做了一项以后还有排着的（或离开对局后的整幅
        识别可能到点了）时 pending 为真：悬浮窗的识别线程不等 idle_ms，紧接着取下一帧接着做。
        一轮里各项的先后、各自多久读一次、读到的怎么采用都和一帧做完时一样，只是分到相邻几帧；几项必须同一帧读的（轮次和
        任务栏、星数和星币、选角界面和对局外画面、存立绘模板）各自就是一项，见 _read_step。
        轮次被挡住时（_occluded_crop）不按 hud_ms 开新的一轮：隔 occluded_full_s 才完整读一次轮次（重新找标记、看是不是离开了
        对局），其余时候隔 occluded_s 只便宜地看一眼（peek，见 _peek_round）。这一眼是一帧里的一项，读不到时不排别的；读到手上的
        轮次或往前一轮时照读到轮次处理，接着排认角色、读星数等这一轮的其余几项；读到别的数时不再算被挡住，照常隔 hud_ms 完整读。
        例外是地图还要靠任务栏认、任务栏开着时（_tasks_wanted）：照常隔 hud_ms 连带读整块，不看那一眼。
        两开时另一路的三选一显示着（_yielding）：要做文字识别的几项（OCR_STEPS）让路，留到那边关掉以后接着做，这期间不设
        pending（等的是另一路，不用紧接着取帧）；在对局里时照旧按 hud_ms 认角色、读星数（模板比对）。
        不在对局里时（还没有对局状态、本局已结束，见 _outside）：两次读轮次之间另外隔 select_ms 看一眼下方区域（glance：认选角
        界面和对局外的画面，同 _read_pick，不顺带排读星数）。选角界面只显示三四秒，只在每 hud_ms 读不到轮次时顺带看的话常常
        落在两次之间。下方区域和上次认定「不是」时一样就不认字（_not_pick）。对局中（连着 quiet_ms 读不到对局读数时也算）
        不看这一眼，照旧只在读不到轮次时顺带看（看见轮次标记以后第二次起读不到、离那次标记 pick_grace_s 以内也先不顺带看，
        见 _pick_grace）。
        刚不再主菜单空转时（_menu_leave 置 _menu_glance）先看这一眼、不沿用「画面没变」的结论，下一帧接着做离开对局后的整幅
        识别，读轮次等照常排在后面（空转期间没读轮次，这时读轮次多半也到点了）：离开主菜单以后多半是选角界面（有时只显示一两秒）、
        「匹配成功」画面（三四秒）或房间，前一个靠这一眼认，后两个靠整幅识别认；先花一次读轮次、再认一次下方区域才轮到整幅识别的话，
        慢电脑上「匹配成功」画面可能已经过去了。连回了对局要连着两次读到轮次才算，晚一帧读不碍事。让路期间照旧不看，留到让完。"""
        yielding = self._yielding(now)
        if self._hud_rest is None:
            if yielding:
                if now - self._last_yield >= self.hud_s and now - self._mark_seen_at < self.after_game_s:
                    self._last_yield = now
                    self._hud_rest = ["hero", "stars"]
            elif self._menu_glance:
                self._menu_glance = False
                self._hud_rest, self._hud_force = ["glance", "loading"], True
            elif self._occluded_crop(frame) is not None and not self._tasks_wanted():
                if now - self._last_hud >= self.occluded_full_s:
                    self._hud_rest, self._hud_force = ["round"], force
                elif now - self._last_peek >= self.occluded_s:
                    self._hud_rest, self._hud_force = ["peek"], force
            elif now - self._last_hud >= self.hud_s:
                self._hud_rest, self._hud_force = ["round"], force
            elif now - self._last_glance >= self.select_s and self._outside():
                self._hud_rest, self._hud_force = ["glance"], force
        read = False
        while self._hud_rest:
            if yielding and self._hud_rest[0] in OCR_STEPS:
                return  # 留到另一路的三选一关掉以后
            if read:
                self.pending = True
                return
            read = self._read_step(self._hud_rest.pop(0), frame, now, events, ocr=not yielding)
        self._hud_rest = None
        if yielding:
            return
        if not read:
            self._read_step("loading", frame, now, events)
        elif now - self._mark_seen_at >= self.after_game_s:
            self.pending = True  # 离开对局后的整幅识别可能到点了：下一帧再看

    def _yielding(self, now: float) -> bool:
        """两开时另一路的三选一正显示着（SelectBoard）：自己平时要做文字识别的读数让路（_idle_reads）。"""
        board = self.board
        return board is not None and board.others(self, now)

    def _outside(self) -> bool:
        """不在对局里：还没有这一局的对局状态（刚打开时在大厅等、选角界面开了新的一局还没进对局），或本局已结束。
        这时另外隔 select_ms 看一眼下方区域（_idle_reads）。"""
        hints = self._hint()
        return not hints.has_game_state or hints.game_over

    def _tasks_wanted(self) -> bool:
        """地图还要靠任务栏认、任务栏开着（按颜色认得出的 _tasks_open，或按颜色认不出的 _block_tasks）：轮次被挡住时也照常隔
        hud_ms 连带读整块（任务栏开着的时候不长，刚打开那一帧可能还在动画里）。这时也不看那一眼：只交轮次的读数没有任务，
        记账会当成任务栏关了（GameState._panel_open），匹配度中的地图就不再读任务栏了。"""
        return self._tasks_open or (self._block_tasks and self._hint().wants_map)

    def _occluded_crop(self, img: Frame | np.ndarray):
        """轮次被挡住时上次标记左边那一小块（hud.Hud.round_crop，这个大小的画面上的像素）；没被挡住，或者画面尺寸和看见它时
        不同了（窗口大小变了，那一小块对不上了）时为 None，后一种顺带清掉：照常完整读。"""
        occluded = self._occluded
        if occluded is None:
            return None
        if occluded[1] != as_frame(img).image.shape[:2]:
            self._occluded = None
            return None
        return occluded[0]

    # 一轮里各项出错时报的名字（认选角界面、对局外画面（连同不在对局里时看的那一眼）、便宜地看一眼算在读轮次里，报同一个名字）
    _STEP_NAMES = {"round": "hud", "pick": "hud", "glance": "hud", "peek": "hud"}

    def _read_step(self, step: str, frame: Frame, now: float, events: list[Event], ocr: bool = True) -> bool:
        """做这一轮里的一项（_idle_reads），返回做没做读数（没到点、不用读时为假，不占这一帧）。

        各项：round 读轮次（地图未知时连任务栏，见 _read_hud）；peek 轮次被挡住时便宜地看一眼（_peek_round）；pick 读不到
        轮次时认选角界面和对局外画面（_read_pick）；glance 不在对局里时隔 select_ms 看一眼，同 pick 但不顺带排读星数；
        hero 按间隔认角色；stars 读星数（读星币的那一拍连同星币、头像认不出时
        按名字找自己；ocr 为假时这两样不做，见 _idle_reads 的让路）；teammates 读队友；template 存立绘模板；progress 读进度条；
        loading 离开对局后的整幅识别。
        出错时照样报出（_failed），也算读过（花了时间）：读轮次、认选角界面出错时后面几项不排，这一轮作废；别的项出错，
        下一帧接着做下一项。"""
        try:
            if step == "round":
                return self._read_hud(frame, now, events, force=self._hud_force)
            if step == "peek":
                self._peek_round(frame, now, events)
                return True
            if step == "pick":
                return self._read_pick(frame, now, events)
            if step == "glance":
                return self._read_pick(frame, now, events, stars=False)
            if step == "hero":
                return self._read_hero_due(frame, now, events)
            if step == "stars":
                return self._read_stars(frame, events, now=now, ocr=ocr) is not None
            if step == "teammates":
                return self._read_teammates(frame, now, events)
            if step == "template":
                return self._save_template(frame, events)
            if step == "progress":
                self._read_progress(frame, events, now=now)
                return True
            return self._check_loading(frame, now, events)
        except Exception as e:  # noqa: BLE001  一项出错不能作废整帧（同 _safe）
            self._failed(self._STEP_NAMES.get(step, step), e)
            return True

    def _read_hud(self, img: Frame | np.ndarray, now: float, events: list[Event], in_select: bool = False,
                  force: bool = False, progress: bool = True) -> bool:
        """读一次轮次（地图还不知道时顺带读任务栏）交给 Session。返回做没做文字识别（同一个静止画面上次什么都没读到时不读）。

        in_select：这是三选一界面（任务栏这时看不到）：一次读完，progress 为真时接着读进度条（只因为读不出轮次而再读时
        不读，见 _select_hud）；不认角色（先读完再给推荐时角色挪到下一帧，见 _select_reads）；星数由 process() 另外读。
        交给 Session 以后要不要补读见 _round_follow_up，出错、读不出轮次时隔多久再读见 _select_hud（调用方）。
        不在三选一时只读轮次：读到轮次时接着认角色、读星数、读队友、存立绘模板、读进度条，读不到时认选角界面和对局外的画面
        （本局进行中、看见轮次标记以后第二次起读不到、离那次标记 pick_grace_s 以内先不认、只读星数，见 _pick_grace），这几项
        排进这一轮（_hud_rest），一帧做一项（_idle_reads）。
        在数字那一行看见了轮次标记、数字却读不出（被玩家列表的头像挡住，Hud.round_mark）时，当作还在对局里：记下看见标记的
        时刻（_mark_seen_at）和标记左边那一小块（_occluded，之后隔 occluded_s 只在那里认字），不认选角界面，照旧排读星数。
        地图未知时，任务栏开着（或上一次连带读到了任务）才每次连带读整块，没开时隔 block_s 才连带读一次（_block_due）。
        这一局正打着、刚看见过轮次时（_mid_game：不在三选一、不是按热键、手上有轮次、本局没结束、离最近一次看见标记不到
        round_retry_gap_s）少读两样，两样共用这一个条件：
        - 完整读第一遍读不出时不再换大小多认两遍（read_hud 的 retry 为假；对决、过场画面的左上角落进了别的白色大数字时三遍都
          白读），真的跳过重读时由 hud 数一次 round_no_retry。连带读整块时也一样。
        - 离上一次完整读什么都读不到（轮次和标记都没有，_full_blank_at）不到 blank_full_s 时，这一帧只认字、不再完整读
          （BLANK_FULL_MS）：当作读了一次、什么都没读到，照常交给 Session、照常认选角界面或排读星数，识别记录 how 为 wait，
          数一次 hud_full_wait。先只认字（_round_quick）能用时回到棋盘马上认出，用不上时最晚晚 blank_full_s 才完整读到。
          连带读整块时照旧完整读。
        新一局还没读到轮次、对局外（本局已结束）、离上次看见标记已满 round_retry_gap_s、三选一上、按热键时都照原来的读法。
        读轮次（连同交给 Session）出错时，这一次读 HUD 作废（由调用方接住）：不把出错当成「什么都没读到」交给 Session，
        这一轮后面几项也不排。
        """
        self._last_hud = now
        full = not in_select and self._hint().wants_map and self._block_due(img, now)
        mid_game = self._mid_game(now, in_select, force)
        # 这一帧和上次读时是同一个「不是三选一」的静止画面（战斗等），上次轮次和任务栏什么都没读到：不再读
        screen = None if in_select else self._not_select.matched
        blank_screen, blank_full = self._blank_hud or (None, False)
        if not force and screen is not None and blank_screen is screen and (blank_full or not full):
            hud, read = Hud(), False
        elif not force and not full and (quick := self._round_quick(img, now)) is not None:
            # 先只认字读到了手上的轮次或往前一轮：当作这一次读到了轮次，不再完整读（见 _round_quick）
            hud, read = Hud(round=quick), True
            self._blank_hud = None
            self._trace("hud", round=quick, map=None, confidence=None, candidates=[], tasks=[], how="crop")
        elif mid_game and not full and now - self._full_blank_at < self.blank_full_s:
            # 对局中上一次完整读连标记都没看见（BLANK_FULL_MS）：不到 blank_full_s 不完整读，当作读了一次、什么都没读到
            hud, read = Hud(), True
            perfstats.count("hud_full_wait")
            self._trace("hud", round=None, map=None, confidence=None, candidates=[], tasks=[], how="wait")
        else:
            if mid_game:
                # 对局中第一遍读不出就不再换大小多认两遍（见 read_round_info 的 retry）
                hud = _timed("hud", read_hud, img, self.db, full=full, retry=False)
            else:
                hud = _timed("hud", read_hud, img, self.db, full=full)
            read = True
            # 读到了轮次或看见了标记（三选一上看见的也算）就不再等；不在三选一时什么都没读到，从这一次起等 blank_full_s
            if hud.round is not None or getattr(hud, "round_mark", False):
                self._full_blank_at = float("-inf")
            elif not in_select:
                self._full_blank_at = now
            if full:
                self._last_block = now
                self._block_tasks = bool(getattr(hud, "tasks_seen", None))
            empty = hud.round is None and not getattr(hud, "round_mark", False) and not any(
                getattr(hud, k, None) for k in ("map", "tasks_seen", "map_candidates", "map_scores")
            )
            self._blank_hud = (screen, full) if screen is not None and empty else None
            mark = {"mark": True} if hud.round is None and getattr(hud, "round_mark", False) else {}
            self._trace(
                "hud", round=hud.round, map=getattr(hud, "map", None), confidence=getattr(hud, "map_confidence", None),
                candidates=list(getattr(hud, "map_candidates", ())), tasks=list(getattr(hud, "tasks_seen", ())), **mark,
            )
            memo = getattr(hud, "round_crop", None)
            if getattr(hud, "round_mark", False):
                self._full_mark_at = now
            if hud.round is not None and getattr(hud, "round_mark", False) and memo is not None:
                self._round_memo = (memo, as_frame(img).image.shape[:2])
        self._hud_round = hud.round
        ev = self._call(events, self.session.on_hud, hud)
        if in_select:
            self._round_follow_up(hud.round is not None, now)
        else:
            self._recheck_round = False
        if ev is not None and (ev.kind == "newgame" or getattr(ev, "cause", "") == "rejoined"):
            # 开了新的一局，或者当成断线以后连回了主菜单结束的那一局（offgame.rejoin）：马上再认一次角色。连回来的其实可能是
            # 新的一局（房间和选角界面都没截到），认出别的角色时 Session.on_hero 照常判新的一局，不用等 hero_ms
            self._last_hero = float("-inf")
        if in_select and progress:
            # 任务奖励「进度 -1」常在三选一出现之前生效（实机第 10 轮），三选一画面上就看得到退回后的当前格：
            # 这次推荐要用上，所以三选一上也读进度条（左上角压暗读不到轮次时也读）
            self._safe("progress", self._read_progress, img, events, in_select=True)

        if hud.round is None:
            crop = getattr(hud, "round_crop", None)
            if getattr(hud, "round_mark", False):
                # 数字被挡住、标记还在：在对局里（选角界面、结算画面、房间、奖励画面、主菜单上都没有这个标记），不认选角界面
                self._mark_seen_at = now
                self._note_in_game(now)
                self._not_pick.interrupt()
                self._occluded = (crop, as_frame(img).image.shape[:2]) if crop is not None else None
                if not in_select:
                    self._hud_rest = ["stars"]
            else:
                self._occluded = None
                if not in_select:
                    # 看见轮次标记以后第二次起读不到、离那次标记不久（战斗等挡住了左上角）先不判选角界面，照旧排读星数
                    # （同 _read_pick 判「不是」时）
                    self._hud_rest = ["stars"] if self._pick_grace(now, force) else ["pick"]
            return read
        self._round_seen(now)
        if not in_select:
            # 在对局里：先认角色再读星数（认出角色后才能在玩家列表里找到自己那一行），再读队友、存立绘模板、读进度条
            self._hud_rest = ["hero", "stars", "teammates", "template", "progress"]
        return read

    def _mid_game(self, now: float, in_select: bool = False, force: bool = False) -> bool:
        """这一局正打着、刚看见过轮次：不在三选一上、不是按热键的那一次，手上有轮次（WatchHints.round_now）、本局没结束，
        离最近一次看见轮次标记（_mark_seen_at：读到轮次、只看见标记、只认字那一小块认出都算）不到 round_retry_gap_s。
        这时读轮次少读两样（_read_hud）：完整读第一遍读不出不再换大小重读（read_round_info 的 retry），完整读连标记都没
        看见以后隔 blank_full_s 才再完整读。两样只认这一个条件，改的时候一起改：新一局还没读到轮次、对局外（本局已结束）、
        离上次看见标记已满 round_retry_gap_s 时都照原来的读法，每 hud_s 完整读、第一遍读不出就换大小重读。"""
        if in_select or force:
            return False
        hints = self._hint()
        return (hints.round_now is not None and not hints.game_over
                and now - self._mark_seen_at < self.round_retry_gap_s)

    def _pick_grace(self, now: float, force: bool) -> bool:
        """读不到轮次、标记也没有（_read_hud，不在三选一时）：这一次先不判选角界面（不排 pick，照旧排读星数）。本局进行中、
        看见一次轮次标记（_mark_seen_at：读到轮次，或数字被挡住、只看见标记）以后第二次起读不到、离那次标记不到 pick_grace_s
        时才先不判：战斗、弹窗这类对局里的画面挡住左上角，常常一挡就十几秒，原来这段时间每 hud_s 认一次下方区域，占对局中
        文字识别的三成多（见 PICK_GRACE_MS）。
        看见标记以后第一次读不到时照判（_grace_mark 记下照判过的是哪一次标记）：打完一局时结算画面紧跟在棋盘之后出来（两局实机
        记录里最后一次读到轮次之后约 4 秒，其中一局只停了约 8 秒就换成了房间），中途退回主菜单、房间也是一下子换过去，这一眼
        多半就落在这些画面上，认得和原来一样快；落在过场、战斗这类画面上时，之后到离那次标记 pick_grace_s 为止先不判，最多晚
        这么久才认出。
        按热键开始的这一轮（force）、刚从暂停恢复还没读到轮次（_stale：暂停期间可能早就离开了对局）时照判；按热键的那一次不算
        「第一次」，之后第一次自动读不到时还会照判一次。还没有对局状态、本局已结束时照判：不在对局里时另有隔 select_s 看的
        那一眼（_idle_reads），这里判不判都省不了几次。认出选角界面、对局外的画面时 _mark_seen_at 清掉（_left_game），之后
        照判、不会接着跳过：本局进行中认出主菜单、房间等要连着认出两次才结束这一局，第二次照常隔 hud_s 就判。
        看见像通关画面、打输横幅的颜色以后 pick_grace_s 以内（_ending_seen_at，_check_victory 记下）也照判：第一眼落在横幅、
        红底或过场上、结算画面在后面几眼才出来时，先不判就可能错过结算画面，这一局改由房间等结束、结算画面上的轮次和难度记不上。
        打赢时先有约 2 秒斜着的横幅、再约 2 秒整屏红底，打输时有约 2 秒蓝色斜带（见 vision.victory），每帧都看颜色，颜色像了
        就紧接着取下一帧，一般看得到；结算画面在这之后几秒以内出来。只管这么久、不管这一局剩下的时间：对局中途偶尔也有颜色
        像的画面（一局实机记录里，对局中途像过一次以后，这一局剩下的时间一次都没有先不判）。
        每调用一次数一次（perfstats，每分钟随识别统计记进识别记录）：先不判的仍然数 pick_grace；照判的按命中的那一条数
        pick_check_force、pick_check_stale、pick_check_no_mark、pick_check_old、pick_check_no_game、pick_check_first、
        pick_check_ending（见下面各处的先后）。不另记识别记录（不挤掉对局内容）。"""
        if force:
            perfstats.count("pick_check_force")
            return False
        if self._stale:
            perfstats.count("pick_check_stale")
            return False
        if now - self._mark_seen_at >= self.pick_grace_s:
            perfstats.count("pick_check_no_mark" if self._mark_seen_at == float("-inf") else "pick_check_old")
            return False
        hints = self._hint()
        if not hints.has_game_state or hints.game_over:
            perfstats.count("pick_check_no_game")
            return False
        if self._grace_mark != self._mark_seen_at:
            self._grace_mark = self._mark_seen_at  # 看见这次标记以后第一次读不到：照判
            perfstats.count("pick_check_first")
            return False
        if now - self._ending_seen_at < self.pick_grace_s:
            perfstats.count("pick_check_ending")
            return False  # 刚看见像打完时的颜色：结算画面马上要出来
        perfstats.count("pick_grace")
        return True

    def _round_quick(self, img: Frame | np.ndarray, now: float) -> int | None:
        """读轮次先只认字：在最近一次完整读记下的标记左边那一小块（_round_memo）只认字、不找字（hud.read_round_crop，约十几
        毫秒；完整读要找字，约一百多毫秒），交回可以直接当作读到了的轮次；不能用时为 None，调用方（_read_hud）照旧完整读。
        只有记账那边手上有轮次、本局没结束，认出的是手上的轮次或往前一轮（同 _peek_round 的规矩）才交：认出别的数（跳了不止
        一轮、变小）不交，改走完整读和记账连读两次的规矩，理由见 _peek_round（画面没变时连读两次很容易满足，读错的数会绕过核对）。
        没记着那一小块、画面尺寸和记下时不同（顺带清掉）时不认。轮次一律两位，从 9 到 10 标记不挪，那一小块照用；换了界面语言
        （简繁、英文的标记宽度不同）那一小块对不上，认不出或认成别的数，自然退回完整读、重记。认字出错时清掉那一小块
        （之后照常完整读、重记），出错交给调用方报出。
        离最近一次完整读看见标记（_full_mark_at）满 after_game_s 时也不认、这一次完整读：只认字只说明那一小块里是这几个数字，
        看不见标记；对局外的画面万一在那一小块上正好认出手上的轮次或往前一轮，一直当作读到了轮次的话，认选角界面、对局外的
        画面和离开对局后的整幅识别（都要读不到轮次才走）就一直不做。完整读看见了标记从那时重新算，没看见（离开了对局）以后每次
        都完整读，直到再看见标记；代价是对局里每 after_game_s 多一次完整读。"""
        memo = self._round_memo
        if memo is None:
            return None
        image = as_frame(img).image
        if memo[1] != image.shape[:2]:
            self._round_memo = None
            return None
        if now - self._full_mark_at >= self.after_game_s:
            return None
        hints = self._hint()
        if hints.game_over or hints.round_now is None:
            return None
        try:
            value = _timed("hud_crop", read_round_crop, image, memo[0])
        except Exception:
            self._round_memo = None
            raise
        if value is None or not hints.round_now <= value <= hints.round_now + ROUND_STEP:
            return None
        return value

    def _round_seen(self, now: float) -> None:
        """读到了轮次（完整读、便宜地看一眼都算）：在对局里，记下时刻；不再算被挡住。"""
        self._not_pick.interrupt()
        self._mark_seen_at = now
        self._occluded = None
        self._stale = False
        self._full_blank_at = float("-inf")
        self._note_in_game(now)

    def _block_due(self, img: Frame | np.ndarray, now: float) -> bool:
        """地图未知、要读轮次时，这一次连不连带读画面上方整块（任务栏和地图标记）：任务栏开着（task_panel_visible，约 2 毫秒）
        时每次都连带读；没开时隔 block_s 才读一次。上一次连带读整块读到了任务（_block_tasks：按颜色认不出来的任务列表开着）时
        也每次都连带读，读到没有为止：开着的这段时间和任务栏开着时一样每次读（Session 按每次读到的任务判断任务栏开没开），
        只是刚打开时最晚晚 block_s 才发现。整块的文字识别比只读轮次还贵（慢电脑上约 0.6 秒），任务栏没开时大多读不到东西；
        读的也只是少读几次，读到的照常交给 Session，不沿用上一次的结果（沿用会改识别结果，见 MAP_BLOCK_MS）。"""
        return self._block_tasks or now - self._last_block >= self.block_s or bool(task_panel_visible(img))

    def _peek_round(self, img: Frame | np.ndarray, now: float, events: list[Event], in_select: bool = False) -> None:
        """轮次被挡住时便宜地看一眼：在上次标记左边那一小块只认字（hud.read_round_crop，不找字，几十毫秒）。

        读不到时什么都不交（不算看了一次左上角、没读到轮次：这一眼不找标记，分不出是还挡着还是离开了对局，那由隔
        occluded_full_s 的完整读来看）。读到的是手上的轮次或往前一轮时照读到轮次处理：交给 Session，记下时刻、不再算被挡住；
        不在三选一时接着排认角色、读星数等这一轮的其余几项（和完整读到轮次时一样），下一次完整读照常隔 hud_s。
        读到别的数（跳了不止一轮、变小，手上还没有轮次，本局已结束）不交给 Session（识别记录 held 为 true）：不再只看那一小块，
        照常隔 hud_s 完整读，读到了再走 Session 连读两次的规矩（三选一上按 _retry_later 再读）。这一块和第一遍找字读到的数
        对不上时，完整读（hud.read_round_info）按读不出处理；要是这一眼直接交，画面没变时连读两次很容易满足，读错的数就绕过了
        核对（在样本上模拟头像挡住，第 11 轮这一块读成 17，进度条当前格是 13：Session 只丢比当前格大超过 4 的，17 丢不掉）。
        变小的轮次还可能判成新的一局、清掉这一局的记录，只认一小块不够把握。
        三选一上读到以后要不要补读见 _round_follow_up。
        认字出错时不再算被挡住（之后照常完整读），出错交给调用方报出。"""
        self._last_peek = now
        crop = self._occluded_crop(img)
        if crop is None:
            return
        try:
            value = _timed("round_crop", read_round_crop, as_frame(img).image, crop)
        except Exception:
            # 那一小块认不了（位置对不上这幅画面等）：不再只看那一小块，之后照常完整读；出错照样报出（由调用方接住），不会
            # 隔 occluded_s 就报一次
            self._occluded = None
            raise
        hints = self._hint()
        near = (value is not None and not hints.game_over and hints.round_now is not None
                and hints.round_now <= value <= hints.round_now + ROUND_STEP)
        if value is not None and not near:
            self._trace("round_crop", round=value, held=True)
            self._occluded = None
        if not near:
            if in_select:
                self._hud_round = None
                self._round_follow_up(False, now)
            return
        if in_select:
            self._hud_round = value
        self._trace("round_crop", round=value)
        ev = self._call(events, self.session.on_hud, Hud(round=value))
        if ev is not None and ev.kind == "newgame":
            self._last_hero = float("-inf")
        self._round_seen(now)
        if in_select:
            self._round_follow_up(True, now)
            return
        self._last_hud = now
        self._recheck_round = False
        self._hud_rest = ["hero", "stars", "teammates", "template", "progress"]

    def _round_follow_up(self, got: bool, now: float) -> None:
        """三选一界面上读了一次轮次、交给 Session 以后，要不要补读（连读两次才采用的规矩；got：这一次读到了轮次）。
        往前大幅跳变：下一帧马上补读一次；补读的结果还对不上，三选一期间不再读（关掉以后照常隔 hud_ms 再读）。
        变小的轮次（可能开了新的一局）：第一次读到时隔 hud_ms 补读一次确认（宁可确认得慢一点）；确认的那一次读过以后
        （_drop_checking）不再从头排，那一次读不出、出错时只按 retry_s 再读（_select_hud）；不再待确认
        （确认下来、改正了读错）时排着的清掉。
        这一次没读到轮次时两样都不排（补读的那一次没读到也算补读过了）：由 _select_hud 隔 retry_s 再读，读到了再照上面排。"""
        if not got:
            self._recheck_round = False
            return
        hints = self._hint()
        self._recheck_round = not self._recheck_round and hints.round_jump_pending
        if not hints.round_drop_pending:
            self._drop_check_at = None
        elif self._drop_check_at is None and not self._drop_checking:
            self._drop_check_at = now + self.hud_s

    def _read_hero_due(self, img: Frame | np.ndarray, now: float, events: list[Event]) -> bool:
        """在对局里：还没认出角色时每次都试，认出后隔一段时间复查一次（角色变了说明是新的一局）。返回这一次认没认。"""
        hero_known = self._hint().seen_hero is not None
        # 认不出的皮肤不要一直试：连续失败 10 次后也降到慢速
        eager = not hero_known and self._hero_misses < 10
        if now - self._last_hero < (self.hud_s if eager else self.hero_s):
            return False
        self._read_hero(img, now, events)
        return True

    def _read_pick(self, img: Frame | np.ndarray, now: float, events: list[Event], stars: bool = True) -> bool:
        """读不到轮次（这一轮的下一项）：可能不在对局里。看看是不是选角界面（新的一局要开始了），同一次识别顺带认对局外的画面
        （结算画面的底栏、主菜单、16:9 附近窗口里组队房间的「选择难度」一栏）。下方区域和上次认定「不是」时一样（战斗等），
        就不再判断（按热键开始的这一轮照旧不沿用）。不是选角界面、也不是对局外的画面时，玩家列表可能还在：读星数排进这一轮
        （stars 为假时不排：不在对局里时隔 select_ms 看的那一眼，见 _idle_reads）。记下看的时刻（_last_glance）。
        认出主菜单、交给记账以后不在对局里了的，记下来，之后的帧可以空转（_note_menu）；认出别的画面、选角界面时离开了主菜单
        （防来回进出的记录清掉，见 _menu_stay_over）。
        返回做没做文字识别。认选角界面和对局外画面（连同交给 Session）出错时这一轮作废（由调用方接住），不记成「不是选角界面」。"""
        self._last_glance = now
        picking, read = False, False
        info: dict = {}
        if not self._not_pick.same(img, now, self._hud_force):
            picking, read = _timed("pick", is_select_screen, img, self.db, info=info), True
            # 本局还没结束时认出的房间、奖励画面等（结算画面除外）：静止画面也不记成「不是」，hud_ms 后再认一次
            # （Session 要连续两次读到才结束本局）
            hints = self._hint()
            running = hints.has_game_state and not hints.game_over
            kind = getattr(info.get("screen"), "kind", None) if self.screens_on and running else None
            self._not_pick.record(bool(picking) or kind not in (None, "result"), now)
        screen = info.get("screen") if self.screens_on else None
        if screen is not None:
            # 离开了对局：不用再等 after_game_ms，马上开始找加载画面和其余对局外的画面（奖励画面、房间等）
            self._left_game()
            self._out_of_game(events, screen, full=False)
            self._note_menu(img, screen, now)
            return read
        if picking:
            self._left_game()  # 离开了对局，可以开始找加载画面了
            self._menu_stay_over()  # 也离开了主菜单
            self._last_loading = float("-inf")  # 选角界面上可能公布畸变组件：马上看一次
            ev = self._call(events, self.session.on_select_screen)
            if ev is not None:
                self._last_hero = float("-inf")
            return read
        # 不是选角界面（战斗、筹码面板等）：玩家列表可能还在，照样读星数（本局已结束时不读，见 _read_stars）
        if stars:
            self._hud_rest = [*(self._hud_rest or ()), "stars"]
        return read

    def _left_game(self) -> None:
        """认出了选角界面、对局外的画面：离开了对局。最近读到轮次、看见标记的时刻一起清掉（离开对局后的整幅识别马上开始找），
        被挡住的那一小块也不再算数（不再只看那一小块），读轮次先只认字的那一小块（_round_memo）、进度条沿用的那一次
        （_progress_seen）、待转正的那一帧（_finish_pending）一起清掉。"""
        self._mark_seen_at = float("-inf")
        self._occluded = None
        self._round_memo = None
        self._progress_seen = None
        self._finish_pending = None  # 待转正的那一帧：这一局不会接着打下去，不必留

    def _note_in_game(self, now: float) -> None:
        """这一帧有对局里的读数（读到轮次或看见轮次标记、三选一显示着或被玩家隐藏着、读到玩家列表上的星数、「查看筹码」
        面板）：连着多久看不到对局读数从头算（_count_quiet）；之前已经当作不在对局里了的（Session.game_quiet），改回在对局里。
        也不在主菜单上了：防来回进出的记录清掉（_menu_stay_over）。"""
        self._quiet_for, self._quiet_at = 0.0, now
        self._menu_stay_over()
        if self._hint().game_quiet:
            with self.lock:
                self.session.game_quiet = False
                self._renew()
            self._trace("quiet", on=False)

    def _count_quiet(self, now: float) -> None:
        """不是三选一的一帧做完读数以后（process）：这一帧没有对局里的读数时（有的话 _note_in_game 已经从头算），把离上一帧的
        时间算进「连着多久看不到对局读数」，最多算 QUIET_STEP_S（中间暂停、手动模式不看画面、截不到画面的时间不算）；满了
        quiet_s 就当作不在对局里了（Session.game_quiet）。
        为的是中途退出一局时认不出主菜单（弹窗挡住了开始按钮和底下那排按钮等），或者停在主菜单以外认不出的画面上：没有画面
        结束这一局（主菜单认得出时由它结束，见 session.offgame 的 SCREEN_RULES），这一局的状态一直留着，悬浮窗一直当这局还在打。
        悬浮窗上对局中先不显示的提示、性能自检能不能开始照它按不在对局里算；这一局记的状态不动，结束一局、开新的一局照旧
        由主菜单、选角界面、结算画面等判断。两开时给另一路让路的几帧（_yielding）不算：那几帧不读轮次，不是看不到。"""
        last, self._quiet_at = self._quiet_at, now
        if last is None or now <= last or self._yielding(now):
            return
        self._quiet_for += min(now - last, QUIET_STEP_S)
        if self._quiet_for < self.quiet_s or self._hint().game_quiet:
            return
        with self.lock:
            self.session.game_quiet = True
            self._renew()
        self._trace("quiet", on=True, seconds=round(self._quiet_for))

    def _read_progress(self, img: Frame | np.ndarray, events: list[Event], in_select: bool = False,
                       now: float | None = None) -> None:
        """读进度条：当前所在的格（剩余轮数按它算）和红叉所在的格（进度上限，本局定下后不再改）。
        读不出的项为 None，不影响已记下的值。

        当前格要连续两次读到同一个数才采用（Session.on_progress）。在三选一界面上（in_select）读到和已采用的不同时，
        下一帧马上再读一次（process()），两次一致就采用，这一次三选一的推荐随即按新的当前格计算，不用等 hud_ms。

        进度条没变就不认字：不在三选一上、给了 now（这一轮读数里的那一项，见 _read_step）时，先看进度条那一块的样子
        （hud.progress_look，不做文字识别）。和上一次认字时（_progress_seen）一样（hud.same_progress_look：蓝色当前格挪一格、
        编号换了都算变了）、画面尺寸相同、离那次认字不到 progress_s、那次读到了当前格、记账那边没有在等再读一次的进度
        （progress_pending：读到了和已采用的不同的格，要再认一次字来定）时，不认字，把那次读到的数原样再交给记账（「最近一次
        读到进度」的时刻跟着更新，判新的一局时拿它互相印证），识别记录另有 reused 为 true。只在已采用的数上沿用：读到了别的数
        时记账在等再读一次，照常认字，不会因为沿用凑成「连读两次」。红叉（进度上限）也是连读两次才采用：那次读到了红叉、记账
        还没采用本局的进度上限（Session.wants_progress_cap）时同样不沿用、照常认字，采用以后再沿用。三选一上照旧每次都认字：
        推荐要用这一刻的进度（任务奖励「进度 -1」常在三选一出现之前生效），三选一压暗了画面，样子也和平时不同。
        每调用一次数一次（perfstats，每分钟随识别统计记进识别记录）：沿用的数 progress_reused；照常认字的按没沿用的第一条
        原因数 progress_read_select、progress_read_no_time、progress_read_first、progress_read_size、progress_read_age、
        progress_read_no_current、progress_read_pending、progress_read_look、progress_read_cap（先后见下面的判断）。
        """
        look = progress_look(img) if not in_select and now is not None else None
        if look is None:
            # 三选一上、调用方没给 now 时不算那一块的样子，照常认字
            perfstats.count("progress_read_select" if in_select else "progress_read_no_time")
        else:
            last, shape = self._progress_seen, as_frame(img).image.shape[:2]
            if last is None:
                reason = "progress_read_first"
            elif last[1] != shape:
                reason = "progress_read_size"
            elif not (now - last[2] < self.progress_s):
                reason = "progress_read_age"
            elif last[3].current is None:
                reason = "progress_read_no_current"
            elif self._hint().progress_pending:
                reason = "progress_read_pending"
            elif not same_progress_look(last[0], look):
                reason = "progress_read_look"
            elif last[3].cap is not None and self._cap_wanted():
                reason = "progress_read_cap"
            else:
                reason = None  # 照旧沿用上次的读数
            if reason is None:
                got = last[3]
                perfstats.count("progress_reused")
                self._trace("progress", current=got.current, cap=got.cap, in_select=False, reused=True)
                self._call(events, self.session.on_progress, got.current, got.cap)
                self._recheck_progress = False
                return
            perfstats.count(reason)
        got = _timed("progress", read_progress, img)
        if look is not None:
            self._progress_seen = (look, as_frame(img).image.shape[:2], now, got)
        self._trace("progress", current=got.current, cap=got.cap, in_select=in_select)
        self._call(events, self.session.on_progress, got.current, got.cap)
        pending = self._hint().progress_pending
        # 每次只马上补读一次；补读的结果还对不上，就照常隔 hud_ms 再读
        self._recheck_progress = in_select and pending and not self._recheck_progress

    def _cap_wanted(self) -> bool:
        """记账还没采用本局的进度上限（Session.wants_progress_cap，在锁里读）：进度条沿用时读到过红叉的那一次不沿用（见
        _read_progress）。只在其余几条都满足、要沿用的那次读到了红叉时才问，平时不多进锁。"""
        with self.lock:
            return bool(getattr(self.session, "wants_progress_cap", True))

    def _read_hero(self, img: Frame | np.ndarray, now: float, events: list[Event]) -> None:
        self._last_hero = now
        guess = _timed("hero", read_hero, img, self.db)
        self._hero_misses = 0 if guess is not None and guess.hero else self._hero_misses + 1
        if guess is not None:
            self._trace("hero", hero=getattr(guess, "hero", None), best=getattr(guess, "best", None),
                        score=round(float(getattr(guess, "score", 0.0)), 3),
                        runner_up=round(float(getattr(guess, "runner_up", 0.0)), 3), image=getattr(guess, "image", ""))
        ev = self._call(events, self.session.on_hero, guess)
        if ev is not None and ev.kind == "newgame":
            self._hero_misses = 0

    def _read_stars(self, img: Frame | np.ndarray, events: list[Event], in_select: bool = False,
                    now: float | None = None, ocr: bool = True) -> bool | None:
        """读左侧玩家列表上自己的星数，交给 Session。返回读到了没有（读不出、画面上没有列表时为 False）；没读（缺横条模板
        以后、本局已结束）时为 None。

        本局已结束时不读：读到的星数不记进这一局（Session.on_stars），新的一局由选角界面、轮次或三选一开始。
        例如静止的结算画面：认出以后，同样的画面沿用「不是选角界面」的结论、不再认画面，这一轮会走到读星数这一步。
        给了 now（对局画面上按 hud_ms 读的那几次，不是三选一）时，同一次顺带读星币数（_read_coins）：星币读的是这一次认出的
        自己那一行，所以和读星数是同一帧的一项（一帧一项时也不拆开）。
        头像认不出自己那一行时（「昏迷」字样盖住头像等；按「全员相同」读出星数时也不知道是哪一行），在读星币的那一拍
        （隔 coins_ms；三选一期间不找）按本局记住的名字找那一行（_own_by_name），找到了照样读这一行的星数和星币数。
        ocr 为假时（两开时另一路的三选一显示着，见 _idle_reads）这两样都要做文字识别，这一拍不做，留到下一拍。
        三选一上（in_select）只走快的那一路（stars.read_own_stars 的 quick：头像按平时的几种摇摆认得出自己那一行时，几十毫秒）：
        认不出时不再逐个皮肤、逐个伸缩去试（慢电脑上 0.25～0.6 秒，排在推荐之前），这一帧当读不到，下一帧再试（每次三选一
        最多 SELECT_STAR_TRIES 帧），三选一关掉以后平时那一轮照常全试。手动模式照旧全试：平时不看画面，没有「平时那一轮」，
        换了皮肤以后快的那一路（只试上次认出时的那套）一直认不出，星数就一直读不到。
        """
        if not self.stars_on:
            return None
        hints = self._hint()
        hero, over = hints.stars_hero, hints.game_over
        if over:
            return None
        try:
            # 先按头像认自己那一行。本局记住的行不用在这里：选转账对象等画面上玩家列表会去掉自己那一行、整体上移，
            # 按记住的行会读到队友的星数（只用于「查看筹码」面板的页签判断，见 _read_panel）
            quick = {"quick": True} if in_select and not self._manual else {}
            own = _timed("stars", read_own_stars, img, hero, **quick)
        except MissingTemplates:
            # 缺横条模板（assets/stars/）：以后不再读星数，这一次把错误报出去，让界面上看得到（只报这一次）。
            # 别的文件读不到（读头像素材的一瞬间文件被换掉等）不算缺模板：照常报错，下次照样读
            self.stars_on = False
            raise
        # 按名字找只在读星币的那一拍、角色已认出（按头像认过、没认出来）时做：角色待识别时 stars_hero 为 None，
        # 头像根本没有比，不算「头像认不出」。按「全员相同」读出的星数不知道是哪一行（row 为 None），读不了星币数，这时也按名字找
        coins_due = now is not None and not in_select and ocr and now - self._last_coins >= self.coins_s
        if hero is not None and coins_due and getattr(own, "row", None) is None:
            own = self._own_by_name(img) or own
        if own is not None:
            self._trace("stars", stars=getattr(own, "stars", None), confidence=getattr(own, "confidence", None),
                        row=getattr(own, "row", None), how=getattr(own, "how", ""), in_select=in_select)
            if getattr(own, "row", None) is not None and getattr(own, "how", "") == "头像":
                self._note_own(own.row)
        ev = self._call(events, self.session.on_stars, own, in_select)
        if ev is not None and ev.kind == "newgame":
            self._last_hero = float("-inf")
            self._hero_misses = 0
        if own is not None and now is not None:
            self._note_in_game(now)  # 看得见玩家列表：在对局里（三选一上读的那几次不给 now，三选一本身已经算过）
        if now is not None and not in_select:
            # 对局画面上的这一拍认没认出自己那一行（商店等把玩家列表挡住时认不出）：Session 据此把前后两次确认的星币数记成
            # 一段（星币拆账）
            self._call(events, self.session.on_list, own is not None)
            if ocr:
                self._read_coins(img, own, now, events)
        return own is not None

    def _own_by_name(self, img: Frame | np.ndarray):
        """头像认不出自己那一行时，按本局记住的自己的名字找那一行（stars.read_own_stars_by_name：玩家列表四行都看得见、
        只有一行读到了完整的名字、其余各行的名字都读得出也不像没读全的自己的名字才算）。返回这一行的星数结果（OwnStars，
        how 为「名字」），找不到时返回 None。

        还没记住名字（Session.player_name：按头像认出自己那一行时读到的名字）时不找。本局已经认出过自己在第几行时
        （session.game.own_row），按名字找到的必须是同一行：玩家顺序整局不变，对不上说明认错了（例如自己那一行的名字有字读错、
        队友的名字里正好含着记住的名字），不读。每次找的结果（行号或读不到的原因，不写名字）记进识别记录，画面上没有
        玩家列表时不记；出错时只记原因，不影响这一帧其余的识别。"""
        hints = self._hint()
        name, known = hints.player_name, hints.own_row
        if not name:
            return None
        try:
            report = _timed("own_by_name", read_own_stars_by_name, img, name)
        except Exception as e:  # noqa: BLE001  按名字找只是补充，出错不能打断这一帧其余的识别
            self._trace("own_by_name", row=None, reason=f"按名字找自己那一行出错：{type(e).__name__}: {e}")
            return None
        own, reason = report.result, report.reason
        if own is not None and known is not None and own.row != known:
            reason = f"按名字找到的是第 {own.row + 1} 行，本局认出的是第 {known + 1} 行，不读"
            own = None
        if report.rows:
            self._trace("own_by_name", row=None if own is None else own.row, reason=reason)
        return own

    def _read_coins(self, img: Frame | np.ndarray, own, now: float, events: list[Event]) -> None:
        """隔 coins_ms 读一次自己的星币数（玩家列表上自己那一行横条左边的数字，advisor.vision.coins），交给 Session。

        和读星数同一帧读，用读星数时认出的自己那一行（own：按头像认出的，或头像认不出时按名字找到的），不另找；
        没认出自己那一行（玩家列表不在画面上、被挡住等）时不做文字识别。三选一期间不读（只在对局画面上读星数时调用）。
        每次的读数或读不到的原因记进识别记录；读星币出错时只记下原因，不影响其余识别。"""
        if now - self._last_coins < self.coins_s:
            return
        self._last_coins = now
        try:
            got = _timed("coins", read_own_coins, img, own)
        except Exception as e:  # noqa: BLE001  星币数只用于显示，出错不能打断这一帧其余的识别
            self._trace("coins", coins=None, reason=f"读星币数出错：{type(e).__name__}: {e}")
            return
        self._trace("coins", coins=got.value, reason=got.reason, text=got.text, score=got.score)
        self._call(events, self.session.on_coins, got.value)

    def _read_teammates(self, img: Frame | np.ndarray, now: float, events: list[Event]) -> bool:
        """队友还没认全时读玩家列表上其余各行的角色和名字（认全以后不再读）。返回这一次读没读。

        每次约 0.5 秒（含文字识别）：每局前 MATES_EAGER 次按 hud_ms，之后（有的行一直认不出，例如资料库里没有的皮肤）按 hero_ms。
        """
        hints = self._hint()
        hero, want, games = hints.stars_hero, hints.wants_teammates, hints.games
        if games != self._mates_game:
            self._mates_game, self._mates_tries = games, 0
        interval = self.hud_s if self._mates_tries < MATES_EAGER else self.hero_s
        if not self.stars_on or hero is None or not want or now - self._last_mates < interval:
            return False
        self._last_mates = now
        self._mates_tries += 1
        players = _timed("players", read_players, img, hero)
        if players is None or players.own_row is None:
            return True
        # 按头像认出的自己那一行的名字：头像认不出时按名字找自己（「查看筹码」面板）
        mine = players.rows[players.own_row] if players.own_row < len(players.rows) else None
        if mine is not None and mine.visible and mine.name and not mine.bot:
            self._note_own(players.own_row, name_key(mine.name))
        by_id = {h.id: h.name for h in self.db.heroes.values()}
        rows = [{"row": p.row, "hero": by_id.get(p.hero_id), "bot": p.bot} for p in players.teammates]
        self._trace("players", own_row=players.own_row, rows=[
            {"row": p.row, "hero": by_id.get(p.hero_id), "best": by_id.get(p.best_id), "score": p.score,
             "margin": p.margin, "name": p.name, "bot": p.bot} for p in players.rows
        ])
        self._call(events, self.session.on_teammates, rows)
        return True

    def _save_template(self, img: Frame | np.ndarray, events: list[Event]) -> bool:
        """手动指定了角色、立绘却没认出来（或认成别人）：这一帧左下角看得见立绘时，存成这个角色的模板。看左下角的按钮和存的
        立绘是同一帧。返回这一次做没做文字识别（不用存时为假）。"""
        hero = self._hint().template_wanted
        if hero is None or self.panel_visible:
            return False
        from . import io

        img = as_frame(img).image
        h, w = img.shape[:2]
        unit = ui_scale(w, h)
        x0, up0, x1, up1 = VIEW_CHIPS_BOX
        lines = _timed("template", io.ocr, img, (x0 * unit, h - up0 * unit, x1 * unit, h - up1 * unit))
        if not any(word in ln.text.replace(" ", "") for ln in lines for word in VIEW_CHIPS):
            return True  # 左下角被挡住了（筹码面板等），这一帧不存
        path = save_template(img, hero)
        if path:
            self._trace("template", hero=hero, path=path)
            with self.lock:
                self.session.template_saved()
                self._renew()
        return True

    def _read_panel(self, img: Frame | np.ndarray, now: float, events: list[Event]) -> None:
        """「查看筹码」面板：已有筹码未知时每帧看，平时按 hud_ms 看（面板打开时随时同步已有筹码）；面板刚看不到的
        PANEL_GRACE_S 秒以内也每帧看（翻页动画那几帧不算面板，翻过去的那一页马上读到）。"""
        if read_chip_panel is None:
            return
        hints = self._hint()
        needed, hero, known, name = hints.panel_needed, hints.stars_hero, hints.own_row, hints.player_name
        if (not needed and now - self._last_panel < self.hud_s and not self.panel_visible
                and now - self._panel_seen_at >= PANEL_GRACE_S):
            return
        self._last_panel = now
        try:
            # 判断选中的页签是不是自己：玩家列表里自己的头像；认不出时用本局已确认的行，再不行按名字找
            reading = _timed("panel", read_chip_panel, img, self.db, hero_key=hero, self_row=known, own_name=name)
        except FileNotFoundError:
            return  # 缺筹码图标模板：首次运行时下载（advisor/assets.py），下载前不读面板
        self.panel_visible = bool(reading is not None and getattr(reading, "is_panel", False))
        if self.panel_visible:
            self._panel_seen_at = now
            self._note_in_game(now)  # 「查看筹码」面板只在对局里有
        if reading is not None and getattr(reading, "is_panel", False):
            # page：第几页（翻页箭头，见 chip_panel.PanelReading.page），arrows 是两端箭头的样子
            self._trace("panel", self_tab=getattr(reading, "self_tab", None), owned=list(getattr(reading, "owned", ())),
                        unknown=getattr(reading, "unknown_slots", None), confidence=getattr(reading, "confidence", None),
                        self_index=getattr(reading, "self_index", None), how=getattr(reading, "self_how", ""),
                        selected=getattr(reading, "selected_slot", None), page=getattr(reading, "page", None),
                        arrows=list(getattr(reading, "arrows", None) or ()))
            mine = getattr(reading, "self_index", None)
            if mine is not None and known is None:
                self._note_own(mine)  # 头像或名字认出了自己的页签：本局记住
        self._call(events, self.session.on_panel, reading)

    def _note_own(self, row: int, name: str = "") -> None:
        """本局自己在玩家列表第几行（和那一行的名字）交给 Session 记住。"""
        with self.lock:
            self.session.note_own(row, name)
            self._renew()

    # ------------------------------------------------------------ 对局外：结算画面等、加载画面的地图名、畸变组件

    def _out_of_game(self, events: list[Event], screen, full: bool) -> None:
        """对局外的画面读数（screens.OutOfGame）交给 Session：结算画面结束这一局，对局状态还在时房间、「匹配成功」画面、
        奖励画面、主菜单也结束这一局；房间和「匹配成功」画面上的难度、地图留给下一局。
        full：整幅识别（read_pregame）读到的；否则是选角界面判断那次的下方区域读到的。"""
        self._trace("screen", screen=getattr(screen, "kind", None), difficulty=getattr(screen, "difficulty", None),
                    map=getattr(screen, "map", None), rounds=getattr(screen, "rounds", None), full=full)
        self._call(events, self.session.on_out_of_game, screen)

    def _check_loading(self, img: Frame | np.ndarray, now: float, events: list[Event]) -> bool:
        """不在对局里时，隔一会儿做一次整幅画面的文字识别（read_pregame），同一次识别找三样：对局外的画面（结算画面、
        「匹配成功」画面、房间、奖励画面、主菜单，交给 Session.on_out_of_game）、完整的地图名（第①层，加载画面）和本局的
        畸变组件。
        返回这一次做没做（没到点、画面没变等时为假）。

        整幅画面的文字识别比较贵，所以只在画面和上次看时明显不同的时候做：要找地图名（Session.wants_loading：地图未知，
        或本局已结束、两局之间）时最快 loading_ms 一次；对局状态还在、地图已知时最快 mutators_ms 一次。后一种不论有没有
        畸变组件清单都要看：结算画面没截到时，房间、「匹配成功」画面、奖励画面和主菜单也能结束这一局。
        例外：本局还没结束时认出了房间、「匹配成功」画面、奖励画面或主菜单（Session 要隔一会儿连续两次读到才结束本局），画面停着
        不动也按上面的间隔再认一次；本局结束以后不再这样，静止画面照旧不重复识别。
        关掉第①层（[map_detect] loading）时一律按 mutators_ms。三样各有开关（loading、mutators、out_of_game），
        都关掉、或开着的几样都不用找（地图已知、没有畸变组件清单、不认对局外的画面）时不做整幅识别。
        对局里的战斗等画面也读不到轮次，所以最后一次看见轮次标记（读到轮次，或数字被挡住、只看见标记）之后过一阵子才算离开了
        对局（认出选角界面、结算画面等时立即算离开）：数字被头像挡住期间不当成离开了对局。
        认出对局外的画面时 read_pregame 不给地图名：房间设置上的地图、结算画面上这一局的地图都不是加载画面上的地图名。
        认出主菜单、交给记账以后不在对局里了的，记下来，之后的帧可以空转（_note_menu）；空转期间不做整幅识别，不再空转时上次
        比的缩略图（_loading_thumb，主菜单那张）丢掉，离开主菜单以后画面变化不大也照常认（_menu_leave）。
        """
        if now - self._mark_seen_at < self.after_game_s:
            return False
        want_map = self.loading_on and self._hint().wants_loading
        catalog = mutator_catalog(self.db) if self.mutators_on else ()
        if not (want_map or catalog or self.screens_on):
            return False
        if now - self._last_loading < (self.loading_s if want_map else self.mutators_s):
            return False
        thumb = _thumb(img)
        if self._loading_thumb is not None:
            change = float(np.abs(thumb.astype(np.int16) - self._loading_thumb).mean()) / 255
            if change < self.loading_change:
                return False
        self._last_loading = now
        self._loading_thumb = thumb
        found = _timed("pregame", read_pregame, img, self.db, self.loading_width, catalog)
        if found.screen is not None and self.screens_on:
            self._out_of_game(events, found.screen, full=True)
            hints = self._hint()
            running = hints.has_game_state and not hints.game_over
            if running and getattr(found.screen, "kind", None) != "result":
                self._loading_thumb = None  # 本局还没结束（只读到一次）：画面不变也再认一次
            self._note_menu(img, found.screen, now)
        if found.mutators:
            self._trace("mutators", names=list(found.mutators))
            self._call(events, self.session.on_mutators, found.mutators, True)
        if found.map is not None and want_map:
            self._call(events, self.session.on_loading, found.map)
        return True

    # ------------------------------------------------------------ 主菜单空转

    def _note_menu(self, img: Frame | np.ndarray, screen, now: float) -> None:
        """认出了对局外的画面、交给记账以后（_read_pick 的下方区域、_check_loading 的整幅识别都算）：认出的是主菜单、这时不在
        对局里（_outside）就记下这一帧固定界面那几块的指纹（MENU_REGIONS）、画面尺寸和时刻（_menu），之后的帧对得上就空转
        （_menu_idle）。本局还在进行时认出的主菜单不记：记账要隔一会儿连续两次认出才结束本局（offgame.confirmed），这时照现在的
        规矩再认；第二次认出、结束了本局以后才记。刚记下的还不算开始空转（见 _menu_idle）。menu_idle 关掉时不记。
        认出的是别的画面时离开了主菜单，画面尺寸和上次记下的主菜单不同时窗口大小变了：这次停在主菜单期间防来回进出的记录都
        作废（_menu_stay_over）。底栏也在来回、暂停空转的这段时间（_menu_off_until，见 _menu_trip）不记。"""
        if getattr(screen, "kind", None) != MAIN_MENU:
            self._menu_stay_over()
            return
        if not self.menu_idle or not self._outside():
            return
        img = as_frame(img).image
        shape = img.shape[:2]
        if shape != self._menu_shape:
            self._menu_stay_over()
            self._menu_shape = shape
        if now < self._menu_off_until:
            return
        self._menu = (self._menu_prints(img), shape, now, False)

    @staticmethod
    def _menu_prints(img: np.ndarray) -> tuple:
        """固定界面那几块（MENU_REGIONS）各自的指纹（_fingerprint），4K 画面一共不到 1 毫秒。"""
        return tuple(_fingerprint(img, region) for region in MENU_REGIONS)

    def _menu_idle(self, frame: Frame, now: float, force: bool) -> bool:
        """这一帧是不是主菜单空转的一帧（不是三选一的帧上，process 在看「查看筹码」面板、排读数之前问）。

        记着主菜单（_note_menu）、这一帧固定界面那几块的指纹都和记下时一样（_same 的容差，见 MENU_REGIONS）、画面尺寸也一样，
        就是还停在主菜单上：这一帧一次文字识别都不做，读轮次、认选角界面、整幅识别、认角色、读星数、读队友、读进度条一项都不排，
        「查看筹码」面板、任务栏也不看（都只在对局里有）；主菜单上没有轮次、地图、难度要读，等的只是玩家离开主菜单。只看这两块：
        主菜单的背景动画、左下角轮播的横幅一直在变，整块下方区域、整幅缩略图都会跟着变。三选一照旧每帧看（process），一帧约多
        算两块指纹。
        记下以后第一次有一帧对上，才算开始空转（识别记录记一条 read:menu_idle，on 为 true）；在那之前就没对上的（认出时画面还在
        淡入、固定界面那几块也在动）悄悄丢掉，照常识别，等下一次认出主菜单再记：固定界面一直在动的电脑上照旧识别，不会每一帧都
        认一次字。
        空转中有下面几种情形就不再空转（_menu_leave；这一帧照常识别：先看一眼下方区域，下一帧整幅识别）：这几块变了（开始匹配、
        进房间、选角界面、弹窗盖住了下方等），画面尺寸变了（窗口大小变了），按热键要求的一帧，不在对局外了（界面上改了对局状态
        等），空转满 menu_idle_s（照常认一次，还是主菜单就接着空转，防的是指纹意外对上）。三选一显示着（process）、从暂停恢复
        （resumed）时也不再空转。连着多久看不到对局里的读数照常算（_count_quiet：空转的帧没有对局读数）；两开时让路照旧
        （空转本来不认字，不再空转以后要认字的几项照旧等让完）。
        防来回进出（见 MENU_FLAPS）：因为这几块变了不再空转时连同哪一块变了记下来（_menu_left），记下以后第一次对上、要开始空转
        时看是不是一次来回、满了没有（_menu_trip）：只是开始按钮那一块来回的，这次停在主菜单期间只拿底栏判断（_menu_bar_only，
        开始按钮那一块变了不算），这一帧照常开始空转，识别记录那一条的 why 为 bar_only；底栏也在来回的，这一帧不开始空转、
        MENU_FLAP_MS 以内不再记主菜单，识别记录记一条 on 为 false、why 为 flapping。判过以后这次停在主菜单期间，来回的那几次
        进出不再记识别记录（_menu_hush；不再空转那一条先不记，重新空转时是来回就两条都不记，不是来回、这次停留结束了才补记，
        见 _menu_leave、_menu_stay_over），别的进出（满一分钟照常认一次等）照记。窗口大小变了时这些记录作废
        （_menu_stay_over）。"""
        menu = self._menu
        if menu is None:
            return False
        prints, shape, since, started = menu
        img = frame.image
        why, left = None, None
        if force:
            why = "hotkey"
        elif not self._outside():
            why = "in_game"
        elif img.shape[:2] != shape:
            why = "resized"
            self._menu_stay_over()
        elif now - since >= self.menu_idle_s:
            why = "recheck"
        else:
            bar, start = (_same(a, b) for a, b in zip(self._menu_prints(img), prints))
            if not bar or not (start or self._menu_bar_only):
                why, left = "changed", (now, "start" if bar else "bar" if start else "both", shape)
        if why is not None:
            self._menu_leave(why, left)
            return False
        if not started:
            judged = self._menu_trip(now, shape)
            unlogged, self._menu_unlogged = self._menu_unlogged, None
            if judged == "flapping":
                self._menu = None
                self._trace("menu_idle", on=False, why=judged)
                return False
            self._menu = (prints, shape, since, True)
            if judged == "bar_only":
                self._trace("menu_idle", on=True, why=judged)
            elif judged is None or not self._menu_hush:
                if unlogged is not None:  # 判过以后先没记的那一次不再空转，不是来回：补记
                    self._trace("menu_idle", on=False, why="changed", part=unlogged)
                self._trace("menu_idle", on=True)
        return True

    def _menu_trip(self, now: float, shape: tuple) -> str | None:
        """记下以后第一次对上、要开始空转的这一帧（_menu_idle）：是不是一次来回，满了没有（见 MENU_FLAPS）。
        上一次不再空转是因为固定界面变了（_menu_left）、到现在不到 MENU_TRIP_MS、画面尺寸一样，就算一次来回（这之间认出了别的
        画面的话，_menu_stay_over 已经清掉了 _menu_left），连同那一次哪一块变了记进 _menu_trips（只留 MENU_FLAP_MS 以内的）。
        底栏也变了的（bar、both）满 MENU_FLAPS 次：MENU_FLAP_MS 以内不再空转（_menu_off_until，_note_menu 不记），返回
        "flapping"；只是开始按钮那一块变了的满 MENU_FLAPS 次：这次停在主菜单期间只拿底栏判断（_menu_bar_only），返回
        "bar_only"。判过以后从头数，这次停在主菜单期间来回的那几次进出不再记识别记录（_menu_hush）。算来回、没满时返回
        "trip"，不算来回时返回 None。只拿底栏判断以后，开始按钮那一块变了不会让空转停下，之后的来回只会是底栏的。"""
        left, self._menu_left = self._menu_left, None
        if left is None or now - left[0] > MENU_TRIP_MS / 1000 or left[2] != shape:
            return None
        trips = [trip for trip in self._menu_trips if now - trip[0] < MENU_FLAP_MS / 1000]
        trips.append((now, left[1]))
        bar = sum(part != "start" for _, part in trips)
        if bar >= MENU_FLAPS:
            self._menu_trips, self._menu_hush = [], True
            self._menu_off_until = now + MENU_FLAP_MS / 1000
            return "flapping"
        if len(trips) - bar >= MENU_FLAPS:
            self._menu_trips, self._menu_hush, self._menu_bar_only = [], True, True
            return "bar_only"
        self._menu_trips = trips
        return "trip"

    def _menu_stay_over(self) -> None:
        """这次停在主菜单结束了（认出别的画面、选角界面，有对局里的读数，窗口大小变了，从暂停恢复）：防来回进出的记录清掉
        （见 _menu_trip），只拿底栏判断、来回的进出不记识别记录都不再算数，下一次停在主菜单从头数。判过来回以后最后那一次不再
        空转先没记的（_menu_unlogged，多半就是离开主菜单的那一次），这时补记。底栏来回以后暂停空转的时限（_menu_off_until）
        不清：最多 MENU_FLAP_MS 照不空转时读。"""
        if self._menu_unlogged is not None:
            self._trace("menu_idle", on=False, why="changed", part=self._menu_unlogged)
        self._menu_left = self._menu_unlogged = None
        self._menu_trips = []
        self._menu_bar_only = self._menu_hush = False

    def _menu_leave(self, why: str, left: tuple | None = None) -> None:
        """不再空转（见 _menu_idle；why 是原因：changed 固定界面变了、resized 画面尺寸变了、hotkey 按热键、in_game 不在对局外了、
        recheck 空转满 menu_idle_s、select 三选一显示着、resumed 从暂停恢复）：记着的主菜单丢掉。已经开始空转的，下一次排读数时
        先看一眼下方区域、下一帧接着做整幅识别（_menu_glance，见 _idle_reads；这一眼同不在对局里时看的那一眼，不沿用「画面没变」
        的结论），读轮次等照常排在后面：选角界面有时只显示一两秒，不能先花一次读轮次再看。三选一显示着时不用看（在对局里了）。
        离开对局后的整幅识别上次比的缩略图（_loading_thumb）是空转之前主菜单那张，丢掉：离开主菜单以后画面变化不大（弹窗只盖住
        一角等）也照常认。left：因为固定界面变了不再空转时为（时刻, 哪一块变了, 画面尺寸），记下来看之后重新空转是不是一次来回
        （_menu_left，见 _menu_trip），别的原因不再空转时清掉。识别记录记一条 read:menu_idle（on 为 false，带 why；changed 另带
        part：bar、start、both）；判过来回以后（_menu_hush）因为固定界面变了的先不记（_menu_unlogged）：重新空转时是来回就不记，
        不是来回、或这次停在主菜单结束了才补记（_menu_idle、_menu_stay_over）。还没开始空转的（记下以后一帧都没对上过）
        悄悄丢掉，照常识别，上一次不再空转的记录（_menu_left、_menu_unlogged）留着。"""
        menu, self._menu = self._menu, None
        if menu is None or not menu[3]:
            return
        self._menu_left = left
        self._menu_glance = why != "select"
        self._loading_thumb = None
        if left is not None and self._menu_hush:
            self._menu_unlogged = left[1]
        else:
            self._trace("menu_idle", on=False, why=why, **({"part": left[1]} if left else {}))

    # ------------------------------------------------------------ 通关画面、打输的横幅

    # 上一次看通关画面是在哪一帧（process 的 now）：settle 拿同一幅画面接着处理时（now 相同）不再看、不再认字，也不再
    # 要下一帧（画面一模一样，看了也是同样的结果）
    _victory_looked: float | None = None
    # 现在这一片底色是哪种（"coral" 通关的红底、"banner" 打输的蓝带）和它从哪一帧起连着出现：别的画面大面积是这两种颜色时
    # （蓝色的过场、切换画面），不能一直紧接着取帧、每帧都认一次字，连着超过 victory.RUN_S 秒就不再算这两段画面
    _finish_kind: str | None = None
    _finish_since: float = 0.0

    def _note_finish_look(self, frame: Frame, kind: str, seen, now: float, round_now: int | None, games: int) -> None:
        """留下对局中途像一局打完时的这一帧（_check_victory）：整个换掉待转正的那一张（_finish_pending）。画面是 glance 看的
        那一幅，缩到长边不超过 FINISH_SHOT_SIDE（本来就不超过的照原样复制一份：截取的缓冲之后可能被别的帧复用）；连同本地
        时刻、留下它时的单调时钟 now、那一刻的读数（look、coral、white、round）和原图尺寸，以及那时判过几次新的一局（games，
        之后判了新的一局就不转正）。复制、缩小出错时这一次不留，之前留的照旧，不算识别出错（同 _note_unread：不经 _safe、
        不进 errors）。"""
        try:
            image = as_frame(frame).image
            h, w = image.shape[:2]
            side = max(h, w)
            if side > FINISH_SHOT_SIDE:
                scale = FINISH_SHOT_SIDE / side
                small = cv2.resize(image, (max(1, round(w * scale)), max(1, round(h * scale))),
                                   interpolation=cv2.INTER_AREA)
            else:
                small = _copy_frame(image)
            details = {"look": kind, "coral": round(seen.coral, 3), "white": round(seen.white, 3),
                       "round": round_now, "size": [w, h]}
            shot = (_wall(), small, details, now, games)
        except Exception:  # noqa: BLE001  留不下就不留，见上
            return
        self._finish_pending = shot

    def _check_victory(self, frame: Frame, now: float, events: list[Event]) -> None:
        """不是三选一的帧上看一眼是不是通关画面或打输的横幅（vision/victory.py），只在这一局还在进行时看。先看颜色（4K 一帧不到
        1 毫秒）：像通关画面的珊瑚红底、或像打输时那条蓝色斜带时，紧接着取下一帧（pending，不等平时的间隔：大字读得全的
        时间很短）。红底上中间一带白色多时认一次字，读到 VICTORY 交给 Session.on_victory；蓝带形状对了就转正认一次字，
        读到 GAME OVER 交给 Session.on_game_over_banner。读到了就不再急着取下一帧。
        同一种底色连着超过 RUN_S 秒就不再算（_finish_kind、_finish_since）：那多半是别的画面，不再认字、不再紧接着取帧；底色消失、
        换成另一种时重新算。
        对局中途像了一次、之后这一局却接着打下去（离留下那一帧满了 pick_grace_s、又读到过轮次标记）时，把它转正成
        finish_shot，导出诊断时另存；本局已结束或没有对局状态时丢掉待转正的那一张（真打完时颜色后面紧跟着结算画面）。
        留下以后判了新的一局（WatchHints.games 变了）时也丢掉：新的一局不一定先经过选角界面、对局外的画面（_left_game）或
        本局结束，游戏切到后台、窗口最小化期间打完一局又开了一局时，回来先读到的是新一局变小的轮次、连读两次才判新的一局。
        所以读到变小的轮次、记账还在等再读一次（round_drop_pending）时也先不转正，等它判完：判了新的一局就丢掉，是读错就照常转正。"""
        hints = self._hint()
        if not hints.has_game_state or hints.game_over:
            self._finish_kind = None
            self._finish_pending = None
            return
        pending = self._finish_pending
        if pending is not None and pending[4] != hints.games:
            self._finish_pending = pending = None  # 留下以后判了新的一局：那一帧是上一局的，见上
        if (pending is not None and not hints.round_drop_pending and now - pending[3] >= self.pick_grace_s
                and self._mark_seen_at > pending[3]):
            self.finish_shot = (pending[0], pending[1], pending[2])
            self._finish_pending = None
        if now == self._victory_looked:
            return
        self._victory_looked = now
        seen = victory.glance(frame)
        kind = "coral" if seen.background else "banner" if seen.banner else None
        if kind != self._finish_kind:
            self._finish_kind, self._finish_since = kind, now
        if kind is None or now - self._finish_since > victory.RUN_S:
            return
        # 像通关画面或打输横幅（还没认字也算）：结算画面马上要出来了，之后 pick_grace_s 以内读不到轮次时照常判选角界面
        # （_pick_grace）。离上次看见隔了 pick_grace_s 以上才记一条识别记录（finish_look：哪种、占比），并把这一帧留成
        # 待转正的那一张：对局中途被别的画面引起时，事后对得上是哪一幅画面。pick_grace_s 为 0（先不判关着）时不记也不留：
        # 「隔了 0 秒以上」每帧都满足，颜色像的每一帧都会记一条、留一张
        if self.pick_grace_s > 0 and now - self._ending_seen_at >= self.pick_grace_s:
            self._trace("finish_look", look=kind, coral=round(seen.coral, 3), white=round(seen.white, 3))
            self._note_finish_look(frame, kind, seen, now, hints.round_now, hints.games)
        self._ending_seen_at = now
        if kind == "coral":
            if seen.text and _timed("victory", victory.read, frame):
                self._call(events, self.session.on_victory)
                return
        elif _timed("game_over", victory.read_game_over, frame):
            self._call(events, self.session.on_game_over_banner)
            return
        self.pending = True
