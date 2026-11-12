"""不开游戏，用一张样本截图走完「识别 → 记录 → 悬浮窗」并把悬浮窗画成图片，用来检查界面。

用法：.venv\\Scripts\\python tools\\preview_overlay.py 截图 角色 [已有筹码...] [选项]
  --out 路径            输出图片，默认 .tmp\\overlay_preview.png（检查改动时建议写到 .tmp 下）
  --all 目录            把下面列出的各种状态一次画完，存进这个目录（截图、角色参数照写，用作默认值）
  --shot 截图            改用这张截图（--all 的各状态里用）
  --pipeline            和悬浮窗一样把截图交给 FrameProcessor：角色、轮次、星数都从画面上读（角色参数只是起始值）
  --idle                不识别截图，画出还没出现筹码三选一时的样子（截图参数照写，不使用）
  --before 截图          配合 --pipeline：先喂这几张对局画面（例如打开任务栏的截图，用来识别地图），可以写多次
  --star N / --round N  读到的星数、轮次（默认星数、轮次都是待识别）
  --map 地图[:依据[:匹配度]] 当前地图。依据 loading / tasks / chips / manual（默认 tasks），匹配度 high / medium（默认 high）
  --map-ask 地图,地图    模拟任务栏读过了、几张地图的匹配度仍都低，请用户从这几张里选
  --auto-hero           角色算作自动识别出来的（默认是「待识别」）
  --manual-hero [角色]   角色算作手动指定的；写了角色时，自动识别结果是这个角色（和手动指定的不同）
  --probs 蓝,紫,金       改掉画面上读到的概率，例如 40,45,15 配 --star 1 看任务来源的三选一
  --unreadable N        假装第 N 个候选筹码（从 1 数）无法识别，看这时的界面
  --unrecorded N        假装第 N 个候选筹码是资料库未收录的新筹码（按平均水平计算）
  --panel-tab N         「查看筹码」面板开着，选中的是队友的页签（自己是从左数第 N 个）
  --fix N=筹码名         模拟在推荐卡片里把第 N 个候选筹码改正成某个筹码（可以写多次）
  --open-fix N          画出点了第 N 个筹码名按钮之后展开的改正栏
  --missed              模拟三选一关了却没检测到选择了哪个，画出补录行
  --manual              打开手动模式
  --hotkey 热键          当前热键（例如 Ctrl+Shift+F9；配 --settings 看设置窗口「识别热键」一行的键帽放不放得下）
  --hotkey-failed       模拟启动时热键被其他程序占用
  --hotkey-capture      点了设置窗口「识别热键」的「更改」、等待按下新热键（悬浮窗上看不出，配 --settings 看设置窗口）
  --hotkey-busy 热键     模拟在设置窗口里换成这个热键时被其他程序占用（设置窗口在前台：回到等待按键；配 --settings 看）
 卡片、提示行和另存的图：
  --expanded            点了标题栏的「全部展开」：全部显示
  --closed              三选一给出推荐后又关闭了（选择了第 1 个候选筹码）：收回一行（全部展开时可回看上一次推荐）
  --picked 筹码          同 --closed，但选择的是这个筹码（不是首推时「推荐」卡片问原因）
  --feedback-done 原因   配合 --picked：点了「未选择首推的原因」里的这个原因
  --paused              游戏窗口最小化：识别已暂停
  --hero-needed         三选一出现了，但角色待识别：推荐暂停，请选角色
  --hero 角色 / --owned 筹码,筹码  另写角色、已有筹码（--all 的各状态里用）
  --newgame-undo 依据    判定为新的一局，可撤销
  --panel-prompt        请打开「查看筹码」面板
  --owned-unknown       对局中途打开、还没读到「查看筹码」面板：已有筹码未知，推荐按没有筹码算（「推荐」卡片最上面的提示）
  --panel-read N        识别到 N 个已有筹码
  --map-read 地图        识别到地图
  --gain                星数增加却没有三选一记录：请补录
  --reverted 筹码        同一组候选筹码再次出现，撤销了刚记录的筹码
  --open-add            打开补录栏（输入筹码名搜索）
  --export              已导出诊断包
  --downloading x/y     正在下载识别所需的图片
  --download-failed N   图片有 N 张下载失败
  --download-few N      个别图片下不了：N 张下载失败，其余识别照常、明天会自动再试（带「重试」和「关闭本条提示」）
  --download-few-later N  同 --download-few，失败的里面混着 wiki 上已删除、写不进等别的原因：写「之后会自动再试」
  --calibrate           推荐日志已满 20 局
  --ocr-check 种类       性能自检那一条：running 正在量、good 良好、fair 一般、poor 较差、slow 太慢量不完、noisy 量不准、error 出错；
                        带查到的原因的 poor_small（能替玩家改，「是」「否」）、fair_small（一般、排在小核上，同样问）、
                        poor_ac（只能玩家自己做）、poor_two（两条）、slow_unknown（都没查到）、noisy_busy；
                        点了以后的 fixed、lowend_on、fix_failed
  --unsupported 宽度     不支持的情况：游戏窗口过小（「识别」卡片里的警告行）
  --teammates           队友（悬停说明里）
  --collapse 卡片,卡片   收起这几张卡片（recog 识别 / advice 推荐 / owned 已有筹码），等于点了卡片头的折叠按钮（▾）
  --screen-height N     按这个屏幕可用高度排版（看已有筹码多时「已有筹码」卡片在卡片内滚动）
  --hover N             另存第 N 个候选筹码（从 1 数）的悬停明细（文件名加 _hover）
  --hover-reason 文字    另存悬停在理由标签上的样子：第一个含这段文字的理由标签（写数字时为第 N 个，从 1 数），
                        下方画出弹出的整句（文件名加 _hoverreason）
  --hover-grid N        另存图标格子第 N 格（从 1 数）的悬停说明（文件名加 _hovergrid）
  --about               另存「关于」窗口（文件名加 _about）
  --in-game 截图         另存悬浮窗放在这张游戏截图上默认位置的样子（文件名加 _ingame），检查有没有挡住按钮
  --scale N             配合 --in-game：Windows 的显示缩放（默认 1.5，即 150%）；截图按它缩成逻辑像素，
                        例如 3840x2054 的截图配 --scale 2.0 相当于 1920 宽、100% 缩放的窗口
  --screen-width N      配合 --in-game：屏幕的逻辑宽度。默认按全屏；大于游戏画面时按窗口化（游戏窗口靠左，右侧是桌面）
 同系列、队友、本局难度、对局结束、设置窗口：
  --names 筹码,筹码,筹码  把识别出的三个候选筹码换成这几个（写「*」的格子不换；稀有度按资料库），看换了候选的局面
  --mates 角色,角色,角色  队友（玩家列表从第 2 位起）；写成「角色:电脑」为电脑队友（--teammates 是固定的一组）
  --difficulty-read 种类:难度  对局外的画面上读到的难度。种类：result 结算画面、match_found「匹配成功」画面、
                        team_room 组队房间、create_room 创建房间界面、custom_room 自建房间，例如 team_room:噩梦
  --rows normal / hard  概率行判定出的难度（--idle 时用；识别截图时按截图上的概率行判定）
  --cap N / --progress N  进度条上读到的进度上限（红叉所在的格）、当前所在的格
  --game-over 种类[:难度[:地图[:轮数]]]  最后读到一张对局外的画面，本局已结束（种类同 --difficulty-read，另有 reward
                        奖励画面），例如 result:噩梦:异变图书馆:12
  --hover-status 胶囊    另存悬停在「识别」卡片某个状态胶囊上的样子（写胶囊的 key：difficulty 难度、game_over 对局、
                        round 轮次、star 星数、map 地图、class 职业），下方画出弹出的悬停说明（文件名加 _hoverstatus）
  --hover-text 文字      另存悬停在第一个文字为这段（没有完全相同的时取含这段的）、带悬停说明的标签、文字或按钮上的样子，
                        例如「被金色上位替代」、对比行「比紫色多」、推荐度「%」（文件名加 _hovertext，写多次时加序号）
  --settings            另存「拾星助手设置」窗口（识别模式、识别热键、低配模式、悬浮窗背景、诊断与说明；按直接截取画，没有
                        「识别画面」一项，截取屏幕时才有；文件名加 _settings）
  --big-cores           配 --settings：按有大小核的电脑画（多「只用大核」那一项；处理器名称写 SAMPLE_CPU，按没有自检记录写）
  --hover-live          另存悬停在「推荐」卡片三选一内容空白处（首推那一行筹码名按钮的右边）的样子，悬停说明是整块内容的
                        说明（wording.LIVE_TIP；文件名加 _hoverlive）
  这几种悬停说明在 offscreen 平台上用 Qt 自己的悬停说明控件画（宽度、换行和实际一样）；其他平台上为了不弹出窗口，
  照悬浮窗样式表里的 QToolTip 用标签模拟。
画在哪里：默认用 Qt 的 offscreen 平台画（tests/support/qtenv 的离屏环境：屏幕 1920×1080、缩放 1、装好微软雅黑，和界面测试、
外观基准同一套），不会出现在屏幕上，同样的参数每次画出来逐像素相同。画的是发行版的样子：标题栏不写「开发版」。
  --skin 皮肤名          按这套皮肤画（default、neon_sticker、carbon_flow、white_seaview、classic_white、classic_black、
                        classic_inverse；默认 default）。开发版里各套都已解锁
  --native              改用 Windows 自己的平台画（窗口不显示，直接画进图片；字形和实际显示的一样，但随本机的字体和缩放变）
  --show                真的把窗口显示出来再画（Windows 自己的平台）
  --offscreen           也接受（默认就是 offscreen）
要截图：默认的截图和 --all 的各状态用识别样本 samples 里的截图（按文件名取，见下面的 SELECT、HUD、MISAKI_*、AL_*；识别样本
不随仓库发布，准备方法见 docs/测试指南.md「准备识别样本」），缺了先说明缺哪几张，以退出码 2 结束。--idle 不识别截图、
不用样本；看自己的截图时把它写成第一个参数。没下载过识别图片时，悬浮窗上的筹码图标画成稀有度色圈。

作为库（tools/baseline/ui.py 用来录外观基准）：start_qt 建好 QApplication；scenario_args 给出 SCENARIOS 各状态的参数；
build 按参数搭出局面（read 可以换成冻结好的识别结果，不现读截图），shots 把局面画成一组图（Shot：主图和各种另存的图，
连同图上的窗口），都不写文件；render 是命令行用的「搭、画、存」。用到的私有成员都收在「私有成员」一节。
"""
from __future__ import annotations

import argparse
import os
import shlex
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel, QToolTip, QWidget  # noqa: E402

from advisor import sysinfo  # noqa: E402
from advisor.data import HARD, NORMAL, load_db  # noqa: E402
from advisor.paths import temp_dir  # noqa: E402
from advisor.session import Event, split_extra  # noqa: E402
from advisor.ui.hotkey import startup_busy_text  # noqa: E402
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.hotkey import busy_text
from advisor.ui.overlay_parts.geometry import fit_rect, live_placement, rel_point
from advisor.ui.overlay_parts.ocr_check import OcrNote
from advisor.ui.services import Services  # noqa: E402
from advisor.ui.text import chip_tip  # noqa: E402
from advisor.ui.widgets import Pill  # noqa: E402
from advisor.vision import read_chip_select  # noqa: E402
from advisor.vision.ocr_reason import Reason  # noqa: E402

PIPELINE_STEP = 4.0  # --pipeline：帧与帧之间按几秒算（比读轮次的间隔长，每帧都读一次轮次）
PIPELINE_HOLD = 2  # --pipeline：每张截图喂几帧（轮次大幅跳变要连续读到两次才采用）
QT_WORK = ROOT / ".tmp" / "preview_overlay"  # 离屏平台的配置文件放在这里（tests/support/qtenv.configure）
# 假的诊断包位置（--export：只显示在提示里，不创建）。外观基准换成和项目位置无关的写法
FAKE_REPORT = ROOT / "reports" / "20261002-213045"
SAMPLES = ROOT / "samples"
SAMPLES_GUIDE = "docs/测试指南.md「准备识别样本」"  # 识别样本不随仓库发布，准备方法写在这里
SELECT = str(SAMPLES / "20261002_013522_chipselect_levelup_star1_badge_on.png")  # 凛，4K
HUD = str(SAMPLES / "20261002_012918_hud_round1.png")
# 10-02 实机一局：美咲，星趴·梦想号，困难难度；截图是 2560x1057
MISAKI_R5 = str(SAMPLES / "20261002_144845_chipselect_levelup_star1_2560x1057.jpg")  # 第 5 轮，1 星
MISAKI_R9 = str(SAMPLES / "20261002_145427_chipselect_levelup_star2_2560x1057.jpg")  # 第 9 轮，2 星，刷新 ×4
MISAKI_R10 = str(SAMPLES / "20261002_145915_chipselect_task_tier3_no_reroll_button_2560x1057.jpg")  # 第 10 轮，任务
GAME = "--hero 美咲 --auto-hero --map 星趴·梦想号"
R5 = f"--shot {MISAKI_R5} --round 5 --star 1 --owned 普通瞄具"
R9 = f"--shot {MISAKI_R9} --round 9 --star 2 --owned 普通瞄具,速度轮滑-中级"
R10 = f"--shot {MISAKI_R10} --round 10 --star 2 --owned 普通瞄具,速度轮滑-中级,标靶"
OWNED6 = "普通瞄具,速度轮滑-中级,标靶,大背包,速度轮滑-高级,美工刀-锋利"  # 这一局后来的已有筹码
OWNED10 = OWNED6 + ",医疗箱-紧急治疗,忍术飞镖,安全气囊,磨刀石"
# 10-03 实机一局：阿尔，异变图书馆，噩梦（开局前在组队房间读到），三名真人队友帕露南、茉莉、邦妮（玩家列表第 2～4 位）。
# 文件名带 _real 的截图是 3840x2054（--in-game 用默认的 --scale 1.5），
# 带 2560x1369 的是按逻辑像素存的（配 --scale 1.0）。局面（轮次、星数、已有筹码）照 tests/test_engine.py 的 AlLiveReviewTest
AL_R2 = str(SAMPLES / "20261003_124946_chipselect_levelup_star1_al_real.jpg")  # 第 2 轮：“8面”骰子 / 循环往复 / 摩托头盔-一般
AL_R5 = str(SAMPLES / "20261003_130112_chipselect_team_task_tier2_star1_al_real.jpg")  # 第 5 轮：额外电池 / 摩托头盔-高级 / 速度轮滑-中级
AL_R7 = str(SAMPLES / "20261003_130854_chipselect_task_tier2_after_reroll_bankcards_real.jpg")  # 第 7 轮：银行卡-用不完 / 银行卡-余额多 / 小猪存钱罐
AL_R10 = str(SAMPLES / "20261003_131934_chipselect_team_task_tier3_star3_left_blue_real.jpg")  # 第 10 轮：医疗箱-紧急治疗 / 维生素药丸 / 银行卡-余额多
AL_R11 = str(SAMPLES / "20261003_131952_chipselect_shop_star3_al_real.jpg")  # 第 11 轮：探天卫星 / 摩托头盔-中级 / 书签
AL_HUD9 = str(SAMPLES / "20261003_131150_hud_round9_star2_hp1_dimmed_2560x1369.jpg")
AL_RESULT = str(SAMPLES / "20261003_132518_result_portraits_2560x1369.jpg")  # 结算画面
AL_GOT = ("“8面”骰子", "ATM机", "摩托头盔-高级", "友情徽章", "银行卡-用不完", "星币锤", "银行卡-余额多", "金色飞星", "缓冲盾牌")


def al_owned(n: int) -> str:
    """10-03 那局按获得顺序的前 n 个已有筹码（--owned 的写法）。"""
    return ",".join(AL_GOT[:n])


AL_BASE = "--hero 阿尔 --auto-hero --mates 帕露南,茉莉,邦妮"
AL = f"{AL_BASE} --map 异变图书馆"
AL_ROOM = "--difficulty-read team_room:噩梦"
AL_END = f"--round 12 --star 3 --progress 12 --cap 17 --owned {al_owned(9)} --game-over result:噩梦:异变图书馆:12"

# --all 画的各种状态：（文件名, 参数）。截图、角色不写时用命令行给的
SCENARIOS = [
    ("01_idle_one_line", "--idle --hero 美咲 --auto-hero"),
    ("02_in_game_open_tasks", "--idle --hero 美咲 --auto-hero --round 3 --star 1"),
    ("03_live_advice", f"{GAME} {R9}"),
    ("03b_live_advice_chip_best", f"{GAME} {R5}"),
    ("04_live_advice_expanded", f"{GAME} {R9} --expanded"),
    ("05_closed_summary", f"{GAME} {R10} --picked 电击手套"),
    ("06_closed_expanded", f"{GAME} {R10} --picked 电击手套 --expanded"),
    ("07_paused", f"{GAME} {R9} --paused"),
    ("08_paused_one_line", f"{GAME} {R10} --picked 电击手套 --paused"),
    ("09_hero_needed", f"--hero 美咲 --map 星趴·梦想号 {R9} --hero-needed"),
    ("10_newgame_undo", "--idle --hero 美咲 --newgame-undo 检测到选角界面"),
    ("11_panel_prompt", "--idle --hero 美咲 --auto-hero --round 1 --panel-prompt"),
    ("12_panel_read", "--idle --hero 美咲 --auto-hero --round 1 --owned 普通瞄具,速度轮滑-中级,标靶 --panel-read 3"),
    ("13_map_read", "--idle --hero 美咲 --auto-hero --round 2 --map-read 星趴·梦想号"),
    ("14_map_ask", "--idle --hero 美咲 --auto-hero --round 2 --map-ask 魔法学院,海选赛运动场"),
    ("15_gain", f"--idle {GAME} --round 6 --star 2 --owned 普通瞄具,速度轮滑-中级 --gain"),
    ("16_add_search", f"--idle {GAME} --round 6 --owned 普通瞄具,速度轮滑-中级 --gain --open-add"),
    ("17_reverted", f"{GAME} {R9} --reverted 手电筒-强光"),
    ("17b_reverted_missed", f"{GAME} {R9} --reverted 手电筒-强光 --missed"),
    ("18_missed", f"{GAME} {R9} --missed"),
    ("19_dragon_palace", "--idle --hero 美咲 --auto-hero --round 4 --map 龙宫游乐园:manual"),
    # 持有飞羽手环：标题栏多一个黄色的「补录筹码」（本局有畸变组件「神秘宝藏」时同样如此）
    ("20_quick_add_bracelet", "--idle --auto-hero --round 4 --map 园林中庭 --hero 帕露南 --owned 飞羽手环"),
    ("21_feedback", f"{GAME} {R10} --picked 大背包"),
    ("22_feedback_done", f"{GAME} {R10} --picked 大背包 --feedback-done 照顾队友"),
    ("23_export", f"--idle {GAME} --round 9 --star 2 --owned 普通瞄具,速度轮滑-中级 --expanded --export"),
    ("24_downloading", "--idle --downloading 120/444"),
    ("25_download_failed", "--idle --download-failed 4 --expanded"),
    ("26_calibrate", "--idle --hero 美咲 --auto-hero --calibrate"),
    ("27_unsupported", "--idle --unsupported 900"),
    # 职业未达到门槛时写倾向（凛：「职业 无 · 倾向 卡牌 80」）；队友写在状态胶囊的悬停说明里
    ("28_tendency_expanded", "--idle --auto-hero --round 3 --star 1 --map 异变图书馆 --expanded --teammates"),
    ("29_unreadable", f"{GAME} {R9} --unreadable 2"),
    ("30_hover", f"{GAME} {R10} --hover 3"),
    # 理由标签上只写要点和分值（整句太长，一排标签挤在一起不好看），悬停显示整句：前瞻、收入折算。
    # 收入折算用美咲画（小猪存钱罐「收入剩余 14 轮」）：
    # 凛的小猪存钱罐按默认推荐的条件规则「凛不缺星币」计算，没有收入折算的标签
    ("30b_hover_reason", f"{GAME} {R9} --hover-reason 前瞻"),
    ("30c_hover_reason_income", "--hero 美咲 --auto-hero --round 3 --map 异变图书馆 --hover-reason 收入剩余"),
    ("31_about", "--idle --about"),
    ("32_ingame_live", f"{GAME} {R9} --in-game {MISAKI_R9} --scale 1.0"),
    ("33_ingame_one_line", "--idle --auto-hero --round 3 --map 异变图书馆 --in-game {hud}"),
    ("34_ingame_live_100", "--auto-hero --round 3 --map 异变图书馆 --in-game {select} --scale 1.0"),
    # 1920 宽、1280 宽的窗口（100% 缩放）：全屏时放右下角，窗口化且右侧有空间时放到游戏窗口外侧
    ("37_ingame_live_1920", "--auto-hero --round 3 --map 异变图书馆 --in-game {select} --scale 2.0"),
    ("38_ingame_live_1280", "--auto-hero --round 3 --map 异变图书馆 --in-game {select} --scale 3.0"),
    ("39_ingame_live_1280_windowed", "--auto-hero --round 3 --map 异变图书馆 --in-game {select} --scale 3.0 --screen-width 1920"),
    ("35_unrecorded", f"{GAME} {R9} --unrecorded 3 --hover 3"),
    ("36_panel_tab", "--idle --hero 美咲 --auto-hero --round 1 --panel-prompt --panel-tab 3"),
    # 卡片的排版：已有筹码 0 / 6 / 10 个、各卡片折叠、没选首推时的原因提问、长理由换行
    ("40_owned_0_expanded", f"--idle {GAME} --round 1 --expanded"),
    ("41_owned_6_expanded", f"--idle {GAME} --round 12 --star 2 --owned {OWNED6} --expanded --hover-grid 6"),
    ("42_owned_10_expanded", f"--idle {GAME} --round 16 --star 3 --owned {OWNED10} --expanded"),
    ("43_owned_10_live_scroll", f"{GAME} {R9} --owned {OWNED10} --expanded --screen-height 1040"),
    ("44_collapsed_recog", f"--idle {GAME} --round 12 --star 2 --owned {OWNED6} --expanded --collapse recog"),
    ("45_collapsed_advice_live", f"{GAME} {R5} --collapse advice"),
    ("46_collapsed_owned", f"{GAME} {R5} --expanded --collapse owned"),
    ("47_collapsed_all", f"--idle {GAME} --round 12 --star 2 --owned {OWNED6} --expanded --collapse recog,advice,owned"),
    ("48_question_expanded", f"{GAME} {R10} --picked 大背包 --expanded"),
    ("49_long_reason", f"{GAME} {R10}"),
    # 角色和地图都手动指定、改正了首推的候选筹码：「恢复自动识别」按钮、地图胶囊的「（手动指定）」、筹码名后的「（手动指定）」
    ("50_manual_and_fix_expanded",
     f"--hero 美咲 --manual-hero 邦妮 --map 星趴·梦想号:manual {R5} --fix 1=医疗箱-完备治疗 --expanded"),
    # 同系列、队友、本局难度、对局结束这几种状态，用 10-03 那局（阿尔）的截图和局面：
    # 噩梦及以上提高摩托头盔-高级（第 5 轮金头盔那组）；悬停：筹码名明细、「难度：噩梦及以上」标签、推荐度、
    # 「推荐」卡片的空白处（LIVE_TIP）
    ("51_al_helmet_nightmare_expanded",
     f"{AL} {AL_ROOM} --shot {AL_R5} --round 5 --star 1 --progress 5 --owned {al_owned(2)} --expanded"
     f" --hover 2 --hover-reason 噩梦 --hover-text % --hover-live --in-game {AL_R5}"),
    # 同系列：第 7 轮刷新后的两张银行卡（不算上位替代，各写一行对比）；换成三张银行卡（余额少被紫色上位替代）；
    # 第 5 轮那组的速度轮滑-中级换成摩托头盔-中级（被金色上位替代）
    ("52_al_bankcards_compare_expanded",
     f"{AL} {AL_ROOM} --shot {AL_R7} --round 7 --star 2 --progress 7 --owned {al_owned(4)} --expanded"
     f" --hover-text 比紫色多 --in-game {AL_R7}"),
    ("52b_al_three_bankcards_expanded",
     f"{AL} {AL_ROOM} --shot {AL_R7} --names *,*,银行卡-余额少 --round 7 --star 2 --progress 7 --owned {al_owned(4)}"
     " --expanded --hover-text 上位替代"),
    ("52c_al_helmets_superseded_expanded",
     f"{AL} {AL_ROOM} --shot {AL_R5} --names *,*,摩托头盔-中级 --round 5 --star 1 --progress 5 --owned {al_owned(2)}"
     " --expanded --hover-text 上位替代"),
    # 队友刚需（阿尔本身不会触发，这里换成凛、队友照旧）：手持风扇-大被提到第二位，标记喷罐另有「配合队友：邦妮」
    ("53_rin_teammate_need_expanded",
     f"--hero 凛 --auto-hero --map 异变图书馆 --mates 帕露南,茉莉,邦妮 {AL_ROOM} --shot {AL_R11}"
     " --names *,手持风扇-大,标记喷罐 --round 11 --star 3 --progress 11 --cap 17 --owned 普通瞄具 --expanded"
     " --hover-reason 刚需 --hover-text 配合队友：邦妮"),
    # 本局难度的三种写法：房间读数「噩梦」、按进度上限推算「噩梦及以上」、只有概率行「困难及以上」
    ("54a_status_room_nightmare",
     f"--idle {AL} {AL_ROOM} --rows hard --round 9 --star 2 --progress 9 --owned {al_owned(5)} --expanded"
     f" --hover-status difficulty --in-game {AL_HUD9} --scale 1.0"),
    ("54b_status_cap_nightmare_plus",
     f"--idle {AL} --rows hard --round 11 --star 3 --progress 11 --cap 17 --owned {al_owned(7)} --expanded"
     " --hover-status difficulty"),
    ("54c_status_hard_plus",
     f"--idle {AL} --rows hard --round 8 --star 2 --progress 8 --owned {al_owned(5)} --expanded --hover-status difficulty"),
    # 对局结束（读到结算画面）：一行显示（悬停在标题栏的摘要上）、全部展开（没有上一次推荐时「推荐」卡片写等待下一局）
    ("55a_game_over_one_line", f"--idle {AL} {AL_ROOM} {AL_END} --hover-text 对局 --in-game {AL_RESULT} --scale 1.0"),
    ("55b_game_over_expanded", f"--idle {AL} {AL_ROOM} {AL_END} --expanded --hover-status game_over"),
    ("56_settings", "--idle --hero 阿尔 --auto-hero --settings"),
    # 长悬停说明的断行：「星光」流派的前瞻（第 2 轮）；已持有肾上腺素-高效时美工刀的冲突（第 10 轮那组换一格，
    # 已有筹码的第 6 个星币锤换成肾上腺素-高效）
    ("57a_long_tip_school", f"{AL_BASE} {AL_ROOM} --shot {AL_R2} --round 2 --star 1 --progress 2 --hover-reason 前瞻"),
    ("57b_long_tip_adrenaline",
     f"{AL} {AL_ROOM} --shot {AL_R10} --names *,美工刀-初级,* --round 10 --star 3 --progress 11 --cap 17"
     f" --owned {al_owned(5)},肾上腺素-高效 --hover-reason 冲突"),
]
UNRECORDED_NAME = "星辰回响徽记"  # --unrecorded：模拟游戏更新后资料库还没收录的新筹码
SAMPLE_CPU = "13th Gen Intel Core i9-13980HX"  # --big-cores：「只用大核」一项写的处理器名称（举例，8 大核 16 小核）
# --ocr-check：性能自检那一条的几种样子（秒数、毫秒是举例）
OCR_NOTES = {
    "running": OcrNote("running", left=8), "good": OcrNote("good", threads=4, ms=222),
    "fair": OcrNote("fair", threads=4, ms=620), "poor": OcrNote("poor", threads=4, ms=1240),
    "slow": OcrNote("slow", threads=4, retry=True), "noisy": OcrNote("noisy", threads=4, retry=True),
    "error": OcrNote("error", threads=4, retry=True),
    "poor_small": OcrNote("poor", threads=4, ms=1240,
                          reasons=(Reason("small_core", "big_cores", {"big": 8, "small": 16, "ratio": 2.0}),)),
    "fair_small": OcrNote("fair", threads=8, ms=400,
                          reasons=(Reason("small_core", "big_cores", {"big": 8, "small": 16, "ratio": 2.0}),)),
    "poor_ac": OcrNote("poor", threads=4, ms=1240, retry=True, reasons=(Reason("no_ac", values={"perf": 45}),)),
    "poor_two": OcrNote("poor", threads=4, ms=1240,
                        reasons=(Reason("others_busy", values={"percent": 70, "with_game": True}),
                                 Reason("small_core", "big_cores", {"big": 8, "small": 16, "ratio": 2.0}))),
    "slow_unknown": OcrNote("slow", threads=4, reasons=(Reason("unknown", "lowend"),)),
    "noisy_busy": OcrNote("noisy", threads=4, retry=True,
                          reasons=(Reason("others_busy", values={"percent": 70, "with_game": False}),)),
    "fixed": OcrNote("fixed"), "lowend_on": OcrNote("lowend_on"), "fix_failed": OcrNote("fix_failed"),
}

ABOUT = [
    ("版本", "拾星助手 0.0.0（预览）"),
    ("资料日期", "资料同步日期：2026-10-17"),
    ("资料来源与授权", "游戏机制资料改编自 Astral Party Wiki（英文）https://astralparty.miraheze.org/ ，数值和中文名称照游戏内和吉星派对 WIKI 核对。"),
    ("免责声明", "本工具只截取游戏画面并在本机计算，不修改游戏、不读取内存、不模拟输入。"),
]


def load(path: str) -> np.ndarray:
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def read_shot(path: str, db):
    """截图 → 筹码三选一的识别结果（vision.read_chip_select；不是三选一时为 None）。build 默认用它现读。"""
    return read_chip_select(load(path), db)


def settle() -> None:
    """每次画图之前调用：处理完积压的事件。外观基准把它换成自己的（另外等 Qt 内部的短计时器到时、动画直接到终点），
    画出来的样子不随画得快慢变。"""
    QApplication.processEvents()


# ------------------------------------------------------------ 私有成员
# 预览要搭出一些平时只能由识别、计时或用户操作走到的局面，下面这几个函数直接读写了 Session、Overlay 的私有成员（都收在这一节，
# render 里不直接碰）：Session._set_auto_map、_difficulty_changed、_auto_new_game、_clock；Overlay._screen_height、
# _fix_confirm、_activity、_toggle_card、_shown_bits、_settings、_settings_view、_about、_rel、
# _rel_custom。拆分悬浮窗、Session 时这些名字最先变：换成公开入口时只改这一节。


def _past_wait(session) -> None:
    """三选一刚出现时有筹码没读出来，记账先等一等（selection.HOLD_MAX_S 内不提示，SelectTracker.hold_frame）：把它的时钟往后
    拨过等的时间，再交一次同样的读数就是等满以后提示「无法识别」的样子（--unreadable）。"""
    from advisor.session.selection import HOLD_MAX_S

    clock = session._clock
    session._clock = lambda: clock() + HOLD_MAX_S


def _set_auto_map(session, map_obj, source: str, confidence: str) -> None:
    session._set_auto_map(map_obj, source, confidence)


def _difficulty_changed(session) -> None:
    session._difficulty_changed(push=False)


def _auto_new_game(session, why: str):
    return session._auto_new_game("select", None, why)


def _pin_screen_height(window: Overlay, height: int) -> None:
    window._screen_height = lambda: height


def _confirm_fix(window: Overlay) -> None:
    window.advice.fix_confirm()


def _set_assets_state(window: Overlay, state: tuple, blocked: bool = False) -> None:
    window.background.assets_state = state
    if blocked:
        window.background.blocked = True


def _set_ocr_note(window: Overlay, note: OcrNote) -> None:
    window.ocr_check.notice = lambda: note


def _set_activity(window: Overlay, activity: str) -> None:
    window.follower.activity = activity


def _toggle_card(window: Overlay, key: str) -> None:
    window._toggle_card(key)


def _status_bits(window: Overlay) -> list:
    return window.status.shown_bits


def _settings_dialog(window: Overlay):
    return window.dialogs.settings


def _settings_view(window: Overlay):
    return window.dialogs.settings_view()


def _about_dialog(window: Overlay):
    return window.dialogs.about


def _placement(window: Overlay) -> tuple:
    """（相对游戏窗口的位置, 用户是否拖动过）。"""
    return window.follower.rel, window.follower.rel_custom


def run_pipeline(db, session, window, lock, paths: list[str]):
    """像悬浮窗的后台线程一样逐帧识别、记录，事件交给悬浮窗。返回最后一个事件。每帧到期的读数都做完（FrameProcessor.settle：
    悬浮窗平时一帧只读一项、紧接着取下一帧接着读，一张截图就是接着用这一张）。"""
    from advisor.vision.pipeline import FrameProcessor

    processor = FrameProcessor(db, session, lock)
    now, last = 0.0, None
    for path in paths:
        img = load(path)
        for _ in range(PIPELINE_HOLD):
            now += PIPELINE_STEP
            for ev in processor.settle(img, now):
                window.handle(ev)
                last = ev
    return last


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", nargs="?", default=SELECT)
    ap.add_argument("hero", nargs="?", default="凛")
    ap.add_argument("owned", nargs="*")
    ap.add_argument("--out", default=str(temp_dir() / "overlay_preview.png"))
    ap.add_argument("--all")
    ap.add_argument("--shot")
    ap.add_argument("--hero", dest="hero_opt")
    ap.add_argument("--owned", dest="owned_opt", default="")
    ap.add_argument("--pipeline", action="store_true")
    ap.add_argument("--idle", action="store_true")
    ap.add_argument("--before", action="append", default=[])
    ap.add_argument("--star", type=int)
    ap.add_argument("--round", type=int)
    ap.add_argument("--map")
    ap.add_argument("--map-ask")
    ap.add_argument("--auto-hero", action="store_true")
    ap.add_argument("--manual-hero", nargs="?", const="", default=None)
    ap.add_argument("--probs")
    ap.add_argument("--unreadable", type=int, action="append", default=[])
    ap.add_argument("--unrecorded", type=int, action="append", default=[])
    ap.add_argument("--panel-tab", type=int)
    ap.add_argument("--fix", action="append", default=[])
    ap.add_argument("--open-fix", type=int)
    ap.add_argument("--missed", action="store_true")
    ap.add_argument("--manual", action="store_true")
    ap.add_argument("--hotkey")
    ap.add_argument("--hotkey-failed", action="store_true")
    ap.add_argument("--hotkey-capture", action="store_true")
    ap.add_argument("--hotkey-busy")
    ap.add_argument("--expanded", action="store_true")
    ap.add_argument("--closed", action="store_true")
    ap.add_argument("--picked")
    ap.add_argument("--feedback-done")
    ap.add_argument("--paused", action="store_true")
    ap.add_argument("--hero-needed", action="store_true")
    ap.add_argument("--newgame-undo")
    ap.add_argument("--panel-prompt", action="store_true")
    ap.add_argument("--owned-unknown", action="store_true")
    ap.add_argument("--panel-read", type=int)
    ap.add_argument("--map-read")
    ap.add_argument("--gain", action="store_true")
    ap.add_argument("--reverted")
    ap.add_argument("--open-add", action="store_true")
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--downloading")
    ap.add_argument("--download-failed", type=int)
    ap.add_argument("--download-few", type=int)
    ap.add_argument("--download-few-later", type=int)
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--ocr-check", choices=sorted(OCR_NOTES))
    ap.add_argument("--unsupported")
    ap.add_argument("--teammates", action="store_true")
    ap.add_argument("--collapse", default="")
    ap.add_argument("--screen-height", type=int)
    ap.add_argument("--hover", type=int)
    ap.add_argument("--hover-reason")
    ap.add_argument("--hover-grid", type=int)
    ap.add_argument("--about", action="store_true")
    ap.add_argument("--in-game")
    ap.add_argument("--scale", type=float, default=1.5)
    ap.add_argument("--screen-width", type=int, default=0)
    ap.add_argument("--names")
    ap.add_argument("--mates")
    ap.add_argument("--difficulty-read", action="append", default=[])
    ap.add_argument("--rows", choices=("normal", "hard"))
    ap.add_argument("--cap", type=int)
    ap.add_argument("--progress", type=int)
    ap.add_argument("--game-over")
    ap.add_argument("--hover-status")
    ap.add_argument("--hover-text", action="append", default=[])
    ap.add_argument("--settings", action="store_true")
    ap.add_argument("--big-cores", action="store_true")
    ap.add_argument("--hover-live", action="store_true")
    ap.add_argument("--offscreen", action="store_true")
    ap.add_argument("--native", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--skin")
    return ap


def scenario_args(ap: argparse.ArgumentParser | None = None, image: str = SELECT, hero: str = "凛",
                  owned=()) -> list[tuple[str, argparse.Namespace]]:
    """SCENARIOS 各状态的参数：[(名字, 解析好的参数)]。截图、角色、已有筹码不写时用 image、hero、owned（命令行 --all 时是
    命令行给的）。"""
    ap = ap or parser()
    base = [image, hero, *owned]
    return [(name, ap.parse_args([*base, *shlex.split(extra.format(select=SELECT, hud=HUD), posix=False)]))
            for name, extra in SCENARIOS]


def fake_services(args) -> Services:
    """预览用的假日志、诊断、下载、版本模块：只记在内存里，不写文件、不联网。"""
    logbook = SimpleNamespace(
        record_offer=lambda *a, **kw: None, record_pick=lambda *a: None, record_feedback=lambda *a: None,
        game_count_since_calibration=lambda: 20 if args.calibrate else 0,
    )
    # 悬浮窗导出诊断时另传 extra=（本局和上一局的识别记录、推荐日志），假的 export 照收不用
    diagnostics = SimpleNamespace(export=lambda *a, **kw: str(FAKE_REPORT))
    # 「关于」用真的 advisor/version.py（只读文字，不写文件）；没有时用示例文字
    version = Services.load(("version",)).version or SimpleNamespace(VERSION="0.0.0", about_sections=lambda: ABOUT)
    return Services(logbook=logbook, diagnostics=diagnostics, version=version)


def tooltip_pixmap(html: str, width: int = 500) -> QPixmap:
    """悬停说明画成图片（样式和悬浮窗样式表里的 QToolTip 一致）。"""
    label = QLabel(html)
    label.setTextFormat(Qt.RichText)
    label.setWordWrap(True)
    label.setStyleSheet("QLabel { background:#1c2030; color:#e8eaf0; border:1px solid #6a7085; padding:4px; }")
    label.setFixedWidth(width)
    label.adjustSize()
    return label.grab()


def reason_hover_image(window: Overlay, pixmap: QPixmap, query: str) -> QPixmap:
    """悬停在理由标签上的样子：悬浮窗的图，被悬停的标签描一圈黄线，下方画出弹出的悬停说明（整句）。

    悬停说明的内容和悬浮窗设置的完全相同（Pill.toolTip()），样式同悬浮窗样式表里的 QToolTip。
    query 为数字时取第 N 个理由标签（从 1 数，按显示顺序），否则取第一个文字里含 query 的。
    """
    pills = [p for p in window.findChildren(Pill) if p.toolTip() and p.isVisibleTo(window)]
    pills.sort(key=lambda p: (p.mapTo(window, QPoint(0, 0)).y(), p.mapTo(window, QPoint(0, 0)).x()))
    if query.isdigit():
        pill = pills[int(query) - 1] if 0 < int(query) <= len(pills) else None
    else:
        pill = next((p for p in pills if query in p.text()), None)
    if pill is None:
        raise SystemExit(f"--hover-reason {query}：没有对应的理由标签（共 {len(pills)} 个：{'、'.join(p.text() for p in pills)}）")
    html = pill.toolTip()
    tip = QLabel(html)
    tip.setWordWrap(html.startswith("<"))  # 长句是固定宽度的富文本（text.tip_html），短句照写一行
    tip.setStyleSheet("QLabel { background:#1c2030; color:#e8eaf0; border:1px solid #6a7085; padding:4px; }")
    tip.adjustSize()
    tip_pix = tip.grab()
    top_left = pill.mapTo(window, QPoint(0, 0))
    at = QPoint(top_left.x() + pill.width() // 3, top_left.y() + pill.height() + 8)  # 悬停说明出现在鼠标下方
    w = max(window.width(), at.x() + tip.width() + 2)
    h = max(window.height(), at.y() + tip.height() + 2)
    dpr = pixmap.devicePixelRatio()
    canvas = QPixmap(round(w * dpr), round(h * dpr))
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.drawPixmap(0, 0, pixmap)
    painter.setPen(QColor("#ffd76a"))
    painter.drawRoundedRect(QRect(top_left, pill.size()).adjusted(-1, -1, 0, 0), 10, 10)
    painter.drawPixmap(at, tip_pix)
    painter.end()
    return canvas


def tip_text(widget: QWidget) -> str:
    """悬停在 widget 上时显示的悬停说明：它自己没有时用最近一层父部件的（和 Qt 一样往上找）。"""
    while widget is not None:
        if widget.toolTip():
            return widget.toolTip()
        widget = widget.parentWidget()
    return ""


def tip_pixmap(text: str, widget: QWidget, at: QPoint) -> QPixmap:
    """悬停说明画成图片。offscreen 平台上用 Qt 自己的悬停说明控件（QToolTip，样式取 widget 所在窗口的样式表；
    宽度、换行和实际显示的一样）；其他平台上真的弹出会出现在桌面上，改用标签模拟（样式同悬浮窗样式表里的 QToolTip，
    富文本自动换行、纯文本照原样分行）。at 是鼠标在 widget 里的位置。"""
    if QGuiApplication.platformName() == "offscreen":
        QToolTip.showText(widget.mapToGlobal(at), text, widget)
        settle()
        label = next((w for w in QApplication.topLevelWidgets()
                      if w.objectName() == "qtooltip_label" and w.isVisible()), None)
        if label is not None:
            pix = label.grab()
            QToolTip.hideText()
            # 当场关掉、删掉：hideText 要等 Qt 自己的计时器才真的收起，那之前再显示悬停说明会接着用这一个（是新建还是
            # 接着用随画得快慢变），这里让下一个一律新建
            label.close()
            label.deleteLater()
            QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            QApplication.processEvents()
            return pix
    label = QLabel(text)
    label.setWordWrap(text.lstrip().startswith("<"))
    label.setStyleSheet("QLabel { background:#1c2030; color:#e8eaf0; border:1px solid #6a7085; padding:4px; }")
    label.adjustSize()
    return label.grab()


def hover_compose(base: QPixmap, target: QRect, tip: QPixmap, flip: bool = True) -> QPixmap:
    """悬停的样子：base 上被悬停的部件（target，base 里的逻辑像素）描一圈黄线，悬停说明 tip 画在鼠标的右下方
    （鼠标在部件左起三分之一、下沿处）。flip：右边放不下（超出 base 的宽度）时改放到鼠标左边，和悬浮窗靠在屏幕右边时
    Qt 的放法一样。画布按需要往外扩，扩出的部分透明。"""
    dpr = base.devicePixelRatio()
    bw, bh = base.width() / dpr, base.height() / dpr
    tw, th = tip.width() / tip.devicePixelRatio(), tip.height() / tip.devicePixelRatio()
    cx, cy = target.x() + target.width() // 3, target.y() + target.height()
    x, y = cx + 2, cy + 8
    if flip and x + tw > bw:
        x = cx - 4 - tw
    left, right = min(0.0, x), max(bw, x + tw + 2)
    height = max(bh, y + th + 2)
    canvas = QPixmap(round((right - left) * dpr), round(height * dpr))
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.translate(-left, 0)
    painter.drawPixmap(0, 0, base)
    painter.setPen(QColor("#ffd76a"))
    painter.drawRoundedRect(target.adjusted(-1, -1, 0, 0), 10, 10)
    painter.drawPixmap(round(x), round(y), tip)
    painter.end()
    return canvas


def status_hover_image(window: Overlay, pixmap: QPixmap, key: str) -> QPixmap:
    """悬停在「识别」卡片的状态胶囊（key 为 Bit.key）上的样子。胶囊自己不带悬停说明，显示的是整排胶囊的
    （_status_tip；地图胶囊是地图下拉框，有自己的）。"""
    keys = [b.key for b in _status_bits(window)]
    if key not in keys:
        raise SystemExit(f"--hover-status {key}：没有这个状态胶囊（现有：{'、'.join(map(str, keys))}）")
    widget = window.status_flow.itemAt(keys.index(key)).widget()
    if widget is None or not widget.isVisibleTo(window):
        raise SystemExit(f"--hover-status {key}：状态胶囊没有显示（「识别」卡片收起时请加 --expanded）")
    top_left = widget.mapTo(window, QPoint(0, 0))
    tip = tip_pixmap(tip_text(widget), widget, QPoint(widget.width() // 3, widget.height()))
    return hover_compose(pixmap, QRect(top_left, widget.size()), tip)


def text_hover_image(window: Overlay, pixmap: QPixmap, query: str) -> QPixmap:
    """悬停在第一个文字为 query（没有完全相同的时取含 query 的）、自己带悬停说明的标签、文字或按钮上的样子（按显示顺序）。"""
    found = []
    for w in window.findChildren(QWidget):
        if not isinstance(w, (Pill, QLabel, QAbstractButton)) or not w.toolTip() or not w.isVisibleTo(window):
            continue
        pos = w.mapTo(window, QPoint(0, 0))
        found.append((pos.y(), pos.x(), w.text(), w))
    found.sort(key=lambda f: (f[0], f[1]))
    widget = next((w for *_, text, w in found if text == query), None)
    widget = widget or next((w for *_, text, w in found if query in text), None)
    if widget is None:
        raise SystemExit(f"--hover-text {query}：没有带悬停说明、文字含这段的部件")
    top_left = widget.mapTo(window, QPoint(0, 0))
    tip = tip_pixmap(widget.toolTip(), widget, QPoint(widget.width() // 3, widget.height()))
    return hover_compose(pixmap, QRect(top_left, widget.size()), tip)


def live_hover_image(window: Overlay, pixmap: QPixmap) -> QPixmap:
    """悬停在「推荐」卡片三选一内容空白处的样子：首推那一行（第一个选项块的第一行）筹码名按钮右边的空白，
    那里没有自己的悬停说明，显示的是整块内容的（Overlay.body 的 LIVE_TIP，和 Qt 一样往上找）。
    几套皮肤的选项块换了画法（经典主题-黑的圆形图标、碳纤流光的记分板等），第一行不是默认的那种部件时，在第一个选项块
    上面一截里从右往左找一处这样的空白（_blank_spot）。"""
    body = window.body
    if not body.isVisibleTo(window) or body.layout().count() == 0:
        raise SystemExit("--hover-live：「推荐」卡片里没有三选一的内容（要有推荐；一行显示时请加 --expanded）")
    block = body.layout().itemAt(0).widget().layout().itemAt(0).widget()
    head = block.layout().itemAt(0).widget() if block is not None and block.layout() is not None else None
    parts = [c for c in head.children() if isinstance(c, QWidget) and c.isVisibleTo(head)] if head is not None else []
    if not parts:
        hit, at = _blank_spot(window, body, block or body)
        if hit is None:
            raise SystemExit("--hover-live：首推那一块里没有空白")
        spot = hit.mapTo(window, at)
        tip = tip_pixmap(tip_text(hit), hit, at)
        return hover_compose(pixmap, QRect(spot.x() - 24, spot.y() - 12, 48, 24), tip)
    left = max(c.geometry().right() for c in parts) + 6
    at = QPoint(left + (head.width() - left) // 3, head.height() - 1)
    if head.childAt(at) is not None or not tip_text(head):
        raise SystemExit("--hover-live：首推那一行的右边没有空白")
    target = QRect(head.mapTo(window, QPoint(left, 0)), head.size())
    target.setWidth(head.width() - left)
    tip = tip_pixmap(tip_text(head), head, at)
    return hover_compose(pixmap, target, tip)


def _blank_spot(window: Overlay, body: QWidget, block: QWidget):
    """block（推荐区第一个选项块）上面一截里从右往左找一处空白：鼠标停在那里时显示的是整块内容的悬停说明（body 的，
    没有更近的部件带自己的说明）。返回（那里的部件, 部件里的位置）；找不到时为（None, None）。"""
    live = tip_text(body)
    origin = block.mapTo(window, QPoint(0, 0))
    for dy in range(6, min(block.height(), 60), 4):
        for x in range(block.width() - 8, 8, -4):
            spot = QPoint(origin.x() + x, origin.y() + dy)
            hit = window.childAt(spot)
            if hit is not None and (hit is body or body.isAncestorOf(hit)) and tip_text(hit) == live:
                return hit, hit.mapFrom(window, spot)
    return None, None


def settings_shot(window: Overlay) -> "Shot":
    """「拾星助手设置」窗口（底栏的「设置」按钮打开的那个）。
    offscreen 平台上照悬浮窗的做法打开（show_settings）；其他平台上不显示，直接画（免得弹出窗口）。
    画完关掉窗口（关掉的窗口对象还在，Shot 里照样能查它的部件）。"""
    from advisor.ui.settings import SettingsDialog

    if QGuiApplication.platformName() == "offscreen":
        window.dialogs.show_settings()
        dialog = _settings_dialog(window)
    else:
        dialog = SettingsDialog(_settings_view(window))
        dialog.adjustSize()
    settle()
    shot = Shot("_settings", dialog.grab(), dialog, "settings")
    dialog.close()
    return shot


def out_of_game(spec: str, db) -> SimpleNamespace:
    """--game-over 的「种类[:难度[:地图[:轮数]]]」→ 对局外画面的读数（advisor.vision.screens.OutOfGame 的字段）。"""
    kind, name, map_name, rounds = (spec.split(":") + [None, None, None])[:4]
    return SimpleNamespace(kind=kind, difficulty=name or None, map=db.resolve_map(map_name) if map_name else None,
                           rounds=int(rounds) if rounds else None)


@dataclass
class Shot:
    """画出来的一张图：suffix 为另存时文件名加的后缀（主图为空），image 为图（QPixmap 或 QImage），widget 为图上的窗口
    （主图是悬浮窗，_about、_settings 是那个窗口；悬停说明、贴在游戏截图上的合成图为 None），kind 为种类（overlay 悬浮窗、
    hover 各种悬停说明、about、settings、ingame 贴在游戏截图上），meta 为另外要记的（ingame：悬浮窗在截图上的位置）。"""

    suffix: str
    image: object
    widget: QWidget | None = None
    kind: str = "overlay"
    meta: dict = field(default_factory=dict)


@dataclass
class Built:
    """build 搭好的局面：悬浮窗、Session 和最后一个事件（没有时为 None）。"""

    window: Overlay
    session: OverlaySession
    event: object = None


def build(args, db, read=None, show: bool | None = None) -> Built:
    """按参数搭出一个悬浮窗（还没画）。read 为截图 → 三选一的识别结果（默认 read_shot 现读；外观基准换成冻结好的读数，
    每次给一份新的，这里会改它）；show 为是否显示悬浮窗（默认 offscreen 平台上显示，其他平台按 --show、--offscreen）。"""
    read = read or read_shot
    image = args.shot or args.image
    hero = db.resolve_hero(args.hero_opt or args.hero).name
    settings = UiSettings(
        hero=hero, star=args.star or 0, star_read=args.star is not None, round=args.round, manual=args.manual,
        hotkey=args.hotkey,
    )
    session = OverlaySession(db, settings)  # 不传路径：不写 state.json
    session.set_owned([*args.owned, *(n for n in args.owned_opt.replace("，", ",").split(",") if n)])
    if args.auto_hero:
        session.game.seen_hero = hero
    if args.manual_hero is not None:
        session.set_hero(hero)
        session.game.seen_hero = db.resolve_hero(args.manual_hero).name if args.manual_hero else None
    if args.map:
        parts = args.map.split(":")
        name, source, confidence = (parts + ["tasks", "high"][len(parts) - 1:])[:3]
        if source == "manual":
            session.set_map(name)
        else:
            _set_auto_map(session, db.resolve_map(name), source, confidence)
    if args.map_ask:
        session.game.map_candidates = [db.resolve_map(m) for m in args.map_ask.replace("，", ",").split(",") if m]
        session.game.map_ask = True
    if args.teammates:
        session.game.teammates = [{"hero": "邦妮", "bot": False}, {"hero": "远野汉娜", "bot": True}, {"hero": None, "bot": False}]
    if args.mates:
        # 玩家列表第 2 位起（自己在第 1 位），写法和 Session.on_teammates 认定后的一样
        session.game.teammates = [
            {"row": i + 1, "hero": db.resolve_hero(name.split(":")[0]).name, "bot": name.endswith(":电脑")}
            for i, name in enumerate(n.strip() for n in args.mates.replace("，", ",").split(",") if n.strip())
        ]
    # 本局难度的依据：对局外画面的读数、概率行、进度条（Session.difficulty_info 合并）
    for item in args.difficulty_read:
        kind, name = item.split(":", 1)
        session.game.difficulty_evidence[kind] = name
    if args.rows:
        session.settings.difficulty = NORMAL if args.rows == "normal" else HARD
    if args.cap is not None:
        session.game.progress_cap = args.cap
    if args.progress is not None:
        session.game.progress = args.progress
    if args.difficulty_read or args.rows or args.cap is not None:
        _difficulty_changed(session)
    if args.unsupported:
        width = int(args.unsupported)
        session.set_unsupported("small_window", True, size=f"{width}×{round(width * 9 / 16)}")
    lock = threading.Lock()
    # 按发行版的样子画：标题栏不写「开发版」
    window = Overlay(db, session, lock, services=fake_services(args), dev_label=False)
    window.run_background = lambda fn: fn()
    if args.screen_height:
        _pin_screen_height(window, args.screen_height)
    if args.hotkey_failed:
        window.on_hotkey_failed(startup_busy_text(window.hotkeys.hotkey))  # 和启动时热键线程发的同一句

    event = None
    if args.pipeline:
        event = run_pipeline(db, session, window, lock, [*args.before, image])
    elif not args.idle:
        seen = read(image, db)
        if args.names:
            # 换候选筹码：名称和稀有度按资料库（最左格的稀有度参与判断来源，换成同稀有度的筹码时来源不变）
            for slot, name in enumerate(args.names.replace("，", ",").split(",")[:3]):
                if name.strip() and name.strip() != "*":
                    chip = db.resolve_chip(name.strip())
                    seen.names[slot] = seen.raw[slot] = chip.name
                    seen.rarity[slot] = chip.rarity
                    seen.name_scores[slot] = 1.0
        for n in args.unreadable:
            seen.names[n - 1] = None
            seen.raw[n - 1] = "某某某"
            seen.problems.append(f"第 {n} 个候选筹码无法识别（识别文字：「某某某」）")
        for n in args.unrecorded:
            # 资料库未收录的新筹码：识别模块写原文、标 unrecorded（稀有度沿用画面上读到的图标边框颜色）
            seen.names[n - 1] = seen.raw[n - 1] = UNRECORDED_NAME
            seen.unrecorded[n - 1] = True
        if args.probs:
            seen.probabilities = tuple(int(p) for p in args.probs.split(","))
        if args.hero_needed:
            session.game.seen_hero = None  # 角色待识别：Session 发 hero_needed，不给推荐
        if args.owned_unknown:
            session.game.panel_needed = True  # 还没读到面板（上面 set_owned 记为已确认，这里改回来）：推荐按没有筹码算
        event = session.on_frame(seen)
        if event is None and session.holding:
            # 三选一刚出现时有筹码没读出来（--unreadable）：记账先等一等、不发事件。画等满以后提示「无法识别」的样子
            _past_wait(session)
            event = session.on_frame(seen)
        window.handle(event)
    for item in args.fix:
        slot, name = item.split("=", 1)
        window.advice.open_fix(int(slot) - 1)
        window.fix_box.setEditText(name)
        _confirm_fix(window)
    if args.open_fix:
        window.advice.open_fix(args.open_fix - 1)
    if args.reverted:
        # 同一组候选：记下 --reverted 的筹码、关闭，再出现一次
        seen = session.raw_seen
        seen.selected = list(seen.names).index(args.reverted) if args.reverted in seen.names else 0
        session.on_frame(seen)
        for _ in range(3):
            ev = session.on_frame(None)
            if ev is not None:
                window.handle(ev)
        seen.selected = None
        window.handle(session.on_frame(seen) or Event("status"))
        for ev in session.take_events():
            window.handle(ev)
            event = ev
    if args.missed or args.closed or args.picked:
        # 三选一关了：--missed 一直没看到绿框（请用户补录）；--closed / --picked 记下了选择
        if (args.closed or args.picked) and session.last_seen is not None:
            seen = session.raw_seen
            names = list(session.last_seen.names)
            seen.selected = names.index(args.picked) if args.picked in names else 0
            session.on_frame(seen)
        for _ in range(5):
            ev = session.on_frame(None)
            if ev is not None:
                window.handle(ev)
                event = ev
                break
        if args.closed or args.picked:
            window.handle(Event("idle"))
    if args.feedback_done:
        window.advice.submit_feedback(args.feedback_done)
    if args.game_over:
        # 读到对局外的画面（结算画面等）：Session 判定本局已结束，事件照 FrameProcessor 的顺序交给悬浮窗
        ev = session.on_out_of_game(out_of_game(args.game_over, db))
        extras = session.take_events()
        before, after = split_extra(extras)
        for item in (*before, *([ev] if ev is not None else []), *after):
            window.handle(item)
        event = ev or event
    if args.newgame_undo:
        session.set_owned(["普通瞄具", "速度轮滑-中级"])
        session.settings.round = 6
        event = _auto_new_game(session, args.newgame_undo)
        window.handle(event)
    if args.panel_prompt:
        session.game.panel_needed = True  # 已有筹码未知（上面 set_owned 记为已确认，这里改回来）
        window.handle(Event("panel_prompt"))
    if args.panel_read is not None:
        window.handle(Event("panel_prompt"))
        event = Event("panel_read")
        event.owned = list(session.settings.owned)[: args.panel_read]
        window.handle(event)
    if args.panel_tab:
        # 面板开着，选中的是队友的页签（自己是从左数第 N 个）
        reading = SimpleNamespace(is_panel=True, self_tab=False, self_index=args.panel_tab - 1, selected_tab=0,
                                  owned=[], unknown_slots=0, confidence=0.9, page_full=False, problems=[])
        event = session.on_panel(reading)
        window.handle(event)
    if args.map_read:
        _set_auto_map(session, db.resolve_map(args.map_read), "tasks", "high")
        event = Event("map_read")
        event.map = session.settings.map
        window.handle(event)
    if args.gain:
        session.game.unrecorded = 1
        window.handle(Event("unrecorded_gain", why="星数由 1 星增加为 2 星，未检测到对应的筹码三选一界面", count=1))
    if args.open_add:
        window.open_add()
        window.add_box.setEditText("鹰眼")
    if args.export:
        window.export_diagnostics()
    if args.downloading:
        done, total = (int(x) for x in args.downloading.split("/"))
        _set_assets_state(window, ("downloading", done, total, ""), blocked=True)
        window.refresh()
    if args.download_failed:
        _set_assets_state(window, ("failed", args.download_failed, 440))
        window.refresh()
    if args.download_few or args.download_few_later:
        # 个别图片下不了（Background.failure_kind 的 few、few_later）
        few = ("few", args.download_few) if args.download_few else ("few_later", args.download_few_later)
        _set_assets_state(window, ("failed", few[1], 440, few[0]))
        window.refresh()
    if args.calibrate:
        window.notices.check_calibration()
        window.refresh()
    if args.ocr_check:
        _set_ocr_note(window, OCR_NOTES[args.ocr_check])
        window.refresh()
    if args.expanded:
        window.set_expanded(True)
    if args.paused:
        _set_activity(window, "minimized")  # 游戏窗口最小化：识别已暂停（直接截取时切出游戏也不暂停）
        window.refresh()
    if args.hotkey_capture:
        window.start_capture()
    if args.hotkey_busy:
        window.start_capture()
        window.on_hotkey_change_failed(args.hotkey_busy, busy_text(args.hotkey_busy), active=True)
    for key in (k for k in args.collapse.replace("，", ",").split(",") if k):
        if window.cards[key].is_open:
            _toggle_card(window, key)  # 等于点了卡片头的折叠按钮（▾）
    if show is None:
        show = args.show or args.offscreen or QGuiApplication.platformName() == "offscreen"
    if show:
        window.show()  # offscreen 平台的「显示」也不会出现在屏幕上
    app = QApplication.instance()
    app.processEvents()
    window.fit_height()
    app.processEvents()
    return Built(window, session, event)


def shots(built: Built, args, db) -> list[Shot]:
    """把 build 搭好的局面画成图：主图（悬浮窗），再按参数依次画各种另存的图（悬停说明、「关于」、设置窗口、贴在游戏截图上
    的样子）。都在内存里，不写文件。"""
    window, session = built.window, built.session
    settle()
    pixmap = window.grab()
    out = [Shot("", pixmap, window, "overlay")]
    if args.hover:
        out.append(Shot("_hover", tooltip_pixmap(window.advice.name_tooltip(args.hover - 1)), kind="hover"))
    if args.hover_reason:
        out.append(Shot("_hoverreason", reason_hover_image(window, pixmap, args.hover_reason), kind="hover"))
    if args.hover_grid:
        owned = list(session.settings.owned)
        if args.hover_grid <= len(owned):
            out.append(Shot("_hovergrid", tooltip_pixmap(chip_tip(db.chips[owned[args.hover_grid - 1]]), 360),
                            kind="hover"))
    if args.about:
        window.show_about()
        dialog = _about_dialog(window)
        settle()
        out.append(Shot("_about", dialog.grab(), dialog, "about"))
        dialog.close()
    if args.hover_status:
        out.append(Shot("_hoverstatus", status_hover_image(window, pixmap, args.hover_status), kind="hover"))
    for i, query in enumerate(args.hover_text, 1):
        suffix = "_hovertext" if len(args.hover_text) == 1 else f"_hovertext{i}"
        out.append(Shot(suffix, text_hover_image(window, pixmap, query), kind="hover"))
    if args.settings:
        actual, actual_label = sysinfo.has_big_cores, sysinfo.cpu_label
        if args.big_cores:  # 跑这个工具的电脑不一定有大小核：处理器名称也换成有大小核的，不写这台电脑的
            sysinfo.has_big_cores = lambda: True
            sysinfo.cpu_label = lambda: SAMPLE_CPU
        try:
            out.append(settings_shot(window))
        finally:
            sysinfo.has_big_cores, sysinfo.cpu_label = actual, actual_label
    if args.hover_live:
        out.append(Shot("_hoverlive", live_hover_image(window, pixmap), kind="hover"))
    if args.in_game:
        image, rect = in_game(window, pixmap, args.in_game, args.scale, args.screen_width)
        out.append(Shot("_ingame", image, kind="ingame",
                        meta={"rect": [rect.x(), rect.y(), rect.width(), rect.height()],
                              "canvas": [image.width(), image.height()]}))
    return out


def render(args, db, out: Path) -> str:
    """按参数搭出一个悬浮窗并画成图片（主图存成 out，另存的图在 out 的文件名后面加后缀），返回一行说明。"""
    built = build(args, db)
    window, event = built.window, built.event
    out.parent.mkdir(parents=True, exist_ok=True)
    extra = []
    for shot in shots(built, args, db):
        path = out.with_name(out.stem + shot.suffix + ".png") if shot.suffix else out
        shot.image.save(str(path))
        if shot.suffix:
            extra.append(path.name)
    cards = "、".join(f"{k}:{'开' if window.card_open(k) else ('收起' if not window.cards[k].isHidden() else '不显示')}"
                     for k in window.cards)
    return (
        f"{event.kind if event else '没有事件'}  →  {out.name}{'（' + '、'.join(extra) + '）' if extra else ''}"
        f"  高 {window.height()}\n"
        f"  摘要：{window.status.summary_plain.replace(chr(10), ' ／ ')}\n  状态：{window.status.status_text()}\n  卡片：{cards}"
    )


def in_game(window: Overlay, pixmap: QPixmap, image: str, scale: float, screen_w: int = 0) -> tuple[QImage, QRect]:
    """把悬浮窗按默认位置画在游戏截图上：截图按 Windows 显示缩放换算成逻辑像素，悬浮窗按逻辑像素原样贴上。返回（合成的图,
    悬浮窗在图上的位置）。

    位置和悬浮窗实际用的算法相同（没有拖动过时，三选一显示着就挑挡住三选一最少的位置，见 overlay_parts/geometry.py 的 live_placement）。
    screen_w 为 0 时按全屏（屏幕就是游戏画面）；大于游戏画面宽度时按窗口化（游戏窗口靠左，右侧是空白的桌面）。
    """
    img = load(image)
    h, w = img.shape[:2]
    lw, lh = round(w / scale), round(h / scale)
    small = cv2.resize(img, (lw, lh), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    game_img = QImage(rgb.data, lw, lh, 3 * lw, QImage.Format_RGB888).copy()
    sw = max(lw, screen_w)
    canvas = QImage(sw, lh, QImage.Format_RGB888)
    canvas.fill(QColor(40, 44, 52))
    game = QRect(0, 0, lw, lh)
    screen = QRect(0, 0, sw, lh)
    pw, ph = window.width(), window.height()  # 逻辑像素（pixmap 本身按屏幕缩放比例画，像素更多）
    rel, custom = _placement(window)
    if window.live_state.on and not custom:
        pos = live_placement(game, (pw, ph), rel, [screen])
    else:
        pos = rel_point(game, rel, (pw, ph))
        pos = fit_rect(pos, (pw, ph), [screen]) or pos
    painter = QPainter(canvas)
    painter.drawImage(0, 0, game_img)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.drawPixmap(QRect(pos.x(), pos.y(), pw, ph), pixmap)
    painter.end()
    return canvas, QRect(pos.x(), pos.y(), pw, ph)


def use_skin(key: str | None) -> None:
    """换上皮肤 key（None 为默认皮肤），程序的默认字体按皮肤（同 overlay_app.build）。不认识的名字报错退出。"""
    from advisor.ui import theme

    keys = [s.key for s in theme.skins()]
    if key is not None and key not in keys:
        raise SystemExit(f"没有这套皮肤：{key}（可选：{'、'.join(keys)}）")
    theme.set_skin(key or theme.DEFAULT_KEY)
    QApplication.instance().setFont(theme.app_font())


def start_qt(skin: str | None = None, native: bool = False) -> QApplication:
    """建 QApplication 并换上皮肤。默认走离屏环境（tests/support/qtenv.start_app：屏幕 1920×1080、缩放 1、装好微软雅黑），
    要在当前目录是项目根目录时调用（配置文件写成相对路径）；native 时用 Windows 自己的平台（窗口默认不显示）。本进程已有
    QApplication 时沿用。"""
    app = QApplication.instance()
    if app is None:
        if native:
            app = QApplication(sys.argv)
        else:
            from tests.support import qtenv

            app = qtenv.start_app(QT_WORK)
    use_skin(skin)
    return app


def _absolute(args) -> None:
    """命令行里的相对路径按启动时的当前目录换成绝对路径（之后当前目录要换到项目根目录）。"""
    for name in ("image", "shot", "out", "all", "in_game"):
        value = getattr(args, name, None)
        if value:
            setattr(args, name, str(Path(value).resolve()))
    args.before = [str(Path(p).resolve()) for p in args.before]


def missing_shots(runs: list[argparse.Namespace]) -> list[str]:
    """这几组参数要读、本机却没有的截图（命令行先查一遍，不在画到一半时出错）。项目里的写相对项目根目录的路径，
    项目外的照原样写。"""
    need = []
    for args in runs:
        if not args.idle:
            need.append(args.shot or args.image)
        need += [*args.before, *([args.in_game] if args.in_game else [])]
    gone = []
    for path in dict.fromkeys(need):
        if not Path(path).is_file():
            try:
                gone.append(str(Path(path).resolve().relative_to(ROOT)))
            except ValueError:
                gone.append(str(path))
    return gone


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = parser()
    args = ap.parse_args(argv)
    if args.pipeline and (args.unreadable or args.probs or args.names):
        ap.error("--pipeline 时画面怎么读就怎么用，不能再配 --unreadable、--probs、--names")
    if args.idle and (args.pipeline or args.unreadable or args.probs or args.fix or args.open_fix or args.missed
                      or args.names):
        ap.error("--idle 不识别截图，不能再配 --pipeline、--unreadable、--probs、--fix、--open-fix、--missed、--names")

    native = args.native or args.show
    _absolute(args)
    gone = missing_shots([sub for _, sub in scenario_args(ap, args.image, args.hero, args.owned)] if args.all else [args])
    if gone:
        more = f" 等 {len(gone)} 张" if len(gone) > 3 else ""
        print(f"缺截图：{'、'.join(gone[:3])}{more}（默认读识别样本 samples 里的截图，识别样本不随仓库发布，准备方法见 "
              f"{SAMPLES_GUIDE}；不识别截图时加 --idle，看自己的截图时把它写成第一个参数）", file=sys.stderr)
        raise SystemExit(2)
    if not native:
        os.chdir(ROOT)  # 离屏平台的配置文件按相对项目根目录的路径给（平台参数里不能带盘符）
    start_qt(args.skin, native=native)
    db = load_db()
    if args.all:
        outdir = Path(args.all)
        for name, sub in scenario_args(ap, args.image, args.hero, args.owned):
            print(render(sub, db, outdir / f"{name}.png"))
        return
    print(render(args, db, Path(args.out)))


if __name__ == "__main__":
    main()
