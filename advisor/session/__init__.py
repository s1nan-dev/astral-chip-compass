"""一局游戏里的状态记账（Session）：把逐帧的识别结果变成「给推荐」「记下拿了哪个筹码」这样的事件。

不依赖界面库和图像库。传进来的识别结果只要有相应的属性即可：
  三选一  names / raw / rerolls / probabilities / rarity / selected / problems
  对局画面 round / map（以及可选的 map_confidence / map_candidates / tasks_seen）
  角色    hero（有把握时是角色名，否则 None）
  星数    stars / confidence（advisor.vision.stars.OwnStars；读不出时传 None）
  星币数   整数（advisor.vision.coins 读到的；读不出时传 None），见 on_coins
  筹码面板 is_panel / self_tab / owned / unknown_slots / confidence，以及可选的 page（第几页，没有时按只有一页算）
          （advisor.vision.chip_panel.PanelReading）

启动时不沿用 state.json 里的对局状态（已有筹码、轮次、星数、地图、来源全部重新识别）。
已有筹码要靠游戏里的「查看筹码」面板：进入对局后发 panel_prompt，请用户打开面板；读到自己那一页后写入。

新的一局不用手动点，靠这些迹象自动判断（一局之内它们都不会发生）：
  看到选角界面；轮次变小了；自己的星星变少了；认出的角色换了（手动锁定角色时不看这一条）；
  进度条退回了好几格（同时轮次或星数也变小）；离开很久回来后，「查看筹码」面板上少了之前记下的筹码。
  轮次和星数「变小」是和这一局确实到过的值比（见 NewGameJudge.read_round），只是改正之前的读错时不算。
  对局中途的判定要互相印证（NewGameJudge.vetoed）：另一项读数还说「这一局在继续」时按读错处理，进度条也退回了时除外。
  判定前存撤销点：到这一局第一次记录筹码为止，可以撤销（undo_new_game），撤销就是换回判定前的这一局的状态（GameState）。

星数只认左侧玩家列表上读到的，不由三选一的概率行改写。概率行用来判断这次三选一的来源：
升星和筹码商店的概率行由星数决定（0、1 星是同一行），任务和【循环往复】的由任务等级决定。

概率行还用来判定本局难度（settings.difficulty）：每次三选一的概率行和普通难度的概率表某一行完全相同，本局就是普通难度；
和困难及以上的表某一行相同，就是困难及以上；都对不上（文字识别失败等）时沿用本局已判定的难度，本局还没判定时按困难及以上。
困难、噩梦、疯狂、极限的筹码概率相同，概率行分不出。难度和已有筹码、星数一样是对局状态：新的一局时清空，启动时不沿用。
具体难度（噩梦及以上时摩托头盔-高级对所有角色提高）另合并三种来源，见 Session.difficulty_info：
对局外画面上读到的难度（结算画面 > 「匹配成功」画面 > 房间）、按地图和进度条上的进度上限推算、概率行。
房间和「匹配成功」画面上读到的难度和地图属于下一局：先存着（PendingNextGame）。难度在新的一局开始时用上；
地图等到这一局第一次对局内读数时再用（地图算对局状态，选角界面那一帧就用上的话，选角界面的下一帧会再判一次新的一局）。
读到「匹配成功」画面以后又回到了房间：那次匹配没有开始，它上面的难度和地图作废。

对局结束（on_out_of_game，规则表见 offgame.SCREEN_RULES）：读到结算画面，或对局状态还在时隔一会儿连续两次读到房间、
「匹配成功」画面、奖励画面，本局标为已结束（game_over）。这一局的轮次、星数、地图、难度和已有筹码保留到新的一局开始；之后读到的
星数、角色、进度条、面板不再记进这一局；撤销新一局、漏记补录、补录提示、「查看筹码」面板提示和离开后的核对都取消。之后的新一局
在选角界面、连续两次读到同一个轮次、或出现筹码三选一界面时开始（不经过 NewGameJudge.vetoed 的互相印证）：结算画面结束的
不可撤销；房间等画面结束的可以撤销（对局中途误认出这几种画面时，撤销连同「已结束」一起撤回）。

地图分四层认：①加载画面上的地图名 ②任务栏的任务文字 ③地图专属筹码推定 ④任务栏读过、几张地图的匹配度都低时请用户选。
任务栏的匹配度为高或中（hud 的 map_confidence 为 high / medium）、或来自①③、或用户手动指定的，都算可靠，
界面上只显示地图名；只有第④层才请用户选。对局外的「匹配成功」画面和房间设置上读到的地图也算可靠（下一局用）。
第③层和①（以及「匹配成功」画面、房间设置上读到的地图）冲突时，专属筹码拿到了手（已有筹码里的就对不上），或两次不同的
三选一都指向同一张地图，才改（Session._map_from_chips）。

事件：各 on_xxx 方法返回一个主要事件，提示类事件放进待取队列，用 take_events() 取。
调用 Session 的一方每次调完在同一次持锁里取走（识别流程 FrameProcessor._call；悬浮窗在界面线程上调完也当场取走、交给
自己，只取自己这次调用放进来的：events_mark、take_events 的 after），撤销和上一次三选一的结算排在主要事件之前，其余排在
之后（split_extra）；不留给另一个线程下一次来取。

识别流程经 compute_outside 交来它持着的共用锁时，三选一给推荐（engine.advise）的那一段放开锁去算，算完拿回锁核对局面版本，
没变才记下、发出推荐（selection.SelectTracker._advise）；界面线程这段时间照常进锁。识别流程每帧要看的几项（读不读任务栏、
拿哪个角色认自己那一行……）在锁里一次取齐成只读的 WatchHints。
识别记录：挂了 recorder（advisor.gamelog.GameRecorder）时，note()、trace() 记下的每一条同时交给它写盘（events.EventLog），
判定新的一局时换一段，撤销时接回上一段，对局结束时记一个标记。

筹码方案：scheme_for（悬浮窗接上方案存储后设置）按角色名给出该角色正在用的方案，推荐按当前角色的方案计算（scheme_plan）；
方案内容参与「是不是同一个三选一」的判断，切换方案、编辑正在用的方案后下一帧就会重算，recompute() 按最近一次三选一的画面
立即重算（手动模式也一样，不用等下一帧、不用再按热键）。按方案计算时另算一次默认推荐的首推（Advice.default_best，推荐日志记它）；
用的方案变了时记一条识别记录（scheme）。

各模块（只往下导入，不互相循环导入；对外的名字都从本包导入，不依赖模块的划分）：
- core：Session（读数的入口和编排、用户的操作、新的一局和撤销、星数、星币、地图、队友、畸变组件）；
- game_state：这一局的对局状态 GameState、留给下一局的读数 PendingNextGame、撤销点 UndoPoint（撤销就是换回旧对象）；
- selection：三选一界面的状态机 SelectTracker（开关、刷新等待、去重、给推荐，算推荐时可以放开共用锁：compute_outside、
  AdviceMemo）和来源判断 judge_source；
- newgame：轮次、星数的投票和新一局的判定 NewGameJudge，以及判定用的门槛；
- offgame：对局外画面的规则表 SCREEN_RULES，读到这些画面、对局结束时的记账；
- panel：「查看筹码」面板的核对；difficulty：本局难度的合并 resolve_difficulty 和 DifficultyInfo；
- events：事件 Event、事件的种类 KINDS、事件的序号和记录 EventLog（待取的提示类事件、最近事件、识别记录器），
  取走的事件交给悬浮窗的先后（split_extra），以及写进记录时的写法。
各部分的状态直接放在对应对象上（session.game.seen_hero、session.select._hold 等）：新的一局换 game、撤销换回旧的 game 时，
读写的都是当时那一个；名字写到 Session 上会在 __setattr__ 里报出来（见 core._FIELD_HOLDERS）。
设置的读写在 advisor.prefs（Settings 等，这里也导出）。
"""
# 以下都是对外的名字（各模块里定义，这里统一导出）
from ..prefs import (GAME_FIELDS, SETTINGS_INT_LIMIT, SETTINGS_ORPHAN, SETTINGS_RETRY_S, _KINDS, Settings, _field_types,
                     _file_key, _fits, _parse_settings, _write_settings)
from .core import (CHIP_SOURCE_LABEL, CONFIDENCE_LABEL, FOREIGN_CONFIRM, GAIN_GRACE, HERO_SELECT_LOG_S, LEDGER_KEEP,
                   MAP_SCREEN_SOURCES, MAP_SOURCE_LABEL, MATE_CONFIRM, ROUND_OVER_PROGRESS, STAR_COSTS,
                   STAR_MIN_CONFIDENCE, UNSUPPORTED_ORDER, UNSUPPORTED_TEXT, WHOLE_GAME_ROUND, Session, WatchHints)
from .difficulty import DIFFICULTY_SOURCE_LABEL, DifficultyInfo, resolve_difficulty
from .events import FIRST_KINDS, HISTORY, KINDS, Event, EventLog, _event_record, _jsonable, split_extra
from .game_state import GameState, PendingNextGame, UndoPoint
from .newgame import (CONFIRM, CONFIRM_S, NEWGAME_PROGRESS_DROP, NEWGAME_PROGRESS_MIN, NEWGAME_PROGRESS_RESET,
                      NEWGAME_ROUND_MAX, NEWGAME_VETO_S, REGRESS_LABEL, ROUND_RANGE, ROUND_STEP, STAR_STEP,
                      NewGameJudge)
from .offgame import (END_ROUND_CONFIRM_S, OUT_CONFIRM_GAP_S, OUT_CONFIRM_S, OUT_OF_GAME_LABEL, ROOM_KINDS,
                      SCREEN_RULES, ScreenRule, _put_reading)
from .panel import PAGE_SLOTS, PANEL_MIN_CONFIDENCE, RESUME_CHECK_S, RESUME_MISSING_MIN
from .selection import (DEFAULTS_KEEP, GONE_FRAMES, HOLD_MAX_S, HOLD_READS, HOLD_RETRY_S, SOURCE_LABEL, AdviceMemo,
                        SelectTracker, _about_slot, compute_outside)
