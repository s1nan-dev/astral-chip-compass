"""这一局的对局状态（GameState）、对局外读到留给下一局的读数（PendingNextGame），和判定新一局前存下的撤销点（UndoPoint）。

一局的状态都在 GameState 里：新的一局换上一个新对象（各项取默认值），判定新的一局之前把旧对象连同 PendingNextGame、设置里的
对局字段（prefs.GAME_FIELDS）和角色存成撤销点，撤销就是换回旧对象。旧对象换下来以后没有人再改它，所以不用深拷贝；新加一项
对局状态只要写进 GameState：新的一局自动清空，撤销自动恢复，不用另外登记。读到这一局的状态直接写 session.game.x（测试和
别处一样），名字写到 Session 上会在 __setattr__ 里报出来。

不在这里的：判过几次新的一局（Session.games，撤销也不回退）、撤销点本身（Session._undo）、三选一界面的状态（界面不随新的
一局关掉）、最近一次读到的轮次、星数、进度条和读不到的时刻（判新的一局时要跨局比较）、星币拆账已经记下的各段
（Session.ledger，各段带着局数）、自己的名字、不支持的提示。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

from ..prefs import GAME_FIELDS
from .newgame import CONFIRM, REGRESS_LABEL


def _no_low() -> dict:
    """轮次、星数、进度条都还没有读到比这一局到过的低（见 GameState._low）。"""
    return {k: (0, 0.0, None) for k in REGRESS_LABEL}


@dataclass(slots=True, eq=False)
class GameState:
    """这一局的对局状态：新的一局取各项的默认值，启动时按命令行给的开局状态（from_settings）。按对象认同一局，不按内容比较。

    「查看筹码」面板的两项（panel_needed、_panel_prompted）新的一局照旧沿用，由开新一局的地方按怎么开始的再设
    （见 Session.new_game）；其余各项新的一局一律回到默认值。"""

    # ---- 角色
    seen_hero: str | None = None  # 这一局里靠立绘认出的角色
    # 用户手动指定了角色：不再被自动识别覆盖，「角色换了」也不再当成新的一局。只在本次运行中有效，
    # 直到新的一局、交回自动识别或关掉程序；不存进 state.json，启动时一律重新识别
    hero_locked: bool = False
    _hero_votes: tuple[str, int] | None = None  # 认出的和这一局之前不同的角色（角色, 连续认出的次数）

    # ---- 轮次、星数（读法见 NewGameJudge.read_round、read_star）
    # 还没采用的轮次读数：（读数, 连续读到的次数, 最近一次的时刻），见 NewGameJudge.pending_votes
    _round_pending: tuple[int, int, float] | None = None
    _round_seen: int = 0  # 当前轮次连续读到了几次；启动参数里给了的算已确认
    _round_jumped: bool = False  # 当前轮次是大幅跳变后靠连续读到采用的：读到再多次也不算确实到过
    _round_floor: int = 0  # 这一局确实到过的轮次，变小到它以下才是新的一局（见 NewGameJudge.read_round）
    # 往前走一步之前、已确认的轮次：读回它算改正（静止画面上「05」连读成「06」时，floor 被一步抬上去）
    _round_before_step: int | None = None
    # 采用当前轮次时的（轮次, 进度条当前格）：轮次往前跳时拿进度条这段时间走过的格数核对（见 Session.on_hud）
    _round_at_progress: tuple[int, int] | None = None
    _star_pending: tuple[int, int, float] | None = None  # 同上，星数
    _star_seen: int = 0
    _star_jumped: bool = False
    _star_floor: int = 0
    # 这一局一直没读到星数时，升星、筹码商店三选一（最左格的稀有度知道的）估过的最高星（见 Session.judge_source）：
    # 估星数时不低于它。
    # 和 _star_floor（这一局确实读到过的星数）不同，估出来的不算读到过
    _star_guess_floor: int = 0
    # 这次三选一界面上读到的自己的星数。升星的三选一出现时列表上已经亮了新的星，
    # 所以读到了说明星数是最新的；没读到（自己那一行被标题带挡住）时记下的可能还是升星前的
    _select_star: int | None = None

    # ---- 星币、星币拆账
    # 自己的星币数（玩家列表上自己那一行横条左边的数字）：连续两次读到同一个数才采用，读不到时不改、不清零（on_coins）；
    # 这一局还没采用过时为 None。不存盘
    coins: int | None = None
    _coins_pending: int | None = None  # 最近一次读到的星币数（和它相同的下一次读数才采用）
    # 星币拆账（皮肤解锁「白色海景房」，advisor/unlock.py 的 transfers_in）：玩家列表上认不出自己那一行的一段（on_list）
    # 前后两次确认的星币数之差，连同这一段里的非任务三选一次数、前后的星数等记成一段，放进 Session.ledger 等悬浮窗取走。
    # _ledger_base 是最近一次确认的星币数，_ledger_gone 是那以后认不出过自己那一行，_ledger_star 是那时的星数，
    # _ledger_opens 是那以后的非任务三选一次数；_game_opens 是这一局的。只记、不判断
    _ledger_base: int | None = None
    _ledger_gone: bool = False
    _ledger_star: int | None = None
    _ledger_opens: int = 0
    _game_opens: int = 0
    # 这次三选一（刷新不算新的一次）第一次给推荐时判断的来源；同一组候选后来读对了时按读对的改（见 _open_read）
    _open_source: str | None = None
    # 这次三选一第一次给推荐时那一组候选的读数和那之前的几项（见 Session._open_judged）：候选 names、这一组最近
    # 读到的刷新次数 rerolls（读不到的不算）、最近一次的概率行是不是概率表里的行 in_table、那之前的 _rerolls_seen 和
    # per_star_rerolls（before）、这次三选一还在 _ledger_opens 这一段里 ledger（确认星币数以后为假）。同一组候选后来读对了
    # （刷新次数从 0 变多、概率行从表外变成表内）时据此撤回第一次留下的记录、重新判。刷新（候选换了）以后为 None
    _open_read: dict | None = None
    # 这一局是从开局看起的：从选角界面看起，上一局结束后轮次还小时开始（_start_next_game 的 panel 为假），或这一局第一次
    # 采用的轮次不超过 WHOLE_GAME_ROUND（启动时正在加载等）。启动时在对局中途、对局中途判定的新一局（第 3 轮以后）都不算。
    # 星币拆账（这一局之前在筹码商店买过几次）要用
    whole_game: bool = False
    result_read: bool = False  # 这一局认出了结算画面（不论是不是它结束了这一局）

    # ---- 已有筹码、补录、地图
    missed_offer: list[str | None] | None = None  # 最近一次漏记的三选一，等用户补录
    map_candidates: list[str] = field(default_factory=list)  # 可能的地图，最可能的在前
    map_ask: bool = False  # 第④层：任务栏读过了，几张地图的匹配度仍都低，需要用户从 map_candidates 里选
    _panel_open: bool = False  # 上一次读对局画面时任务栏是不是开着
    # 已有筹码各是怎么记下的（见 CHIP_SOURCE_LABEL）
    chip_sources: dict[str, str] = field(default_factory=dict)
    # 已有筹码次第进来的来路（见 CHIP_SOURCE_LABEL）：进「已有筹码」那一刻记下，面板读到、玩家改已有筹码时不变，
    # 离开「已有筹码」时去掉。和 chip_sources 分开记：面板核对会把 chip_sources 都改成「查看筹码」面板
    owned_sources: dict[str, str | None] = field(default_factory=dict)
    # 这一局在「查看筹码」面板上看到过的筹码：读到面板的那一页以后仍在「已有筹码」里的记进来，离开时去掉
    _panel_seen: set[str] = field(default_factory=set)
    # 补录时记为不经三选一获得的筹码（飞羽手环定期给的、本局有「神秘宝藏」时宝箱给的等）：剩余三选一次数不把这些
    # 算成拿过一次三选一。面板核对不改它，筹码移出已有筹码时跟着去掉；不靠 chip_sources 记：面板核对会把来源都改成
    # 「查看筹码」面板
    direct: set[str] = field(default_factory=set)
    # 三选一记下的筹码 → 当时的三个候选：「查看筹码」面板上没有它、却有同一组里的另一个时，按面板改正
    # （思考时间用完时游戏自动拿最左边的筹码，绿框选中的不一定是实际拿到的）
    _pick_offers: dict[str, tuple] = field(default_factory=dict)
    # 本局确认过的自己在玩家列表第几行（0 起；也是「查看筹码」面板上自己的页签序号）。玩家顺序开局就定下、整局不变，
    # 所以头像认不出（「昏迷」等）时沿用。按头像或名字认出时记下
    own_row: int | None = None

    # ---- 进度条
    # 本局进度条上读到的进度上限（红叉所在的格）：连续两次读到同一个数才采用
    progress_cap: int | None = None
    _cap_pending: int | None = None
    # 这一局没采用过的进度上限（和地图资料对不上，见 Session.on_progress_cap）：同一个数只记一次
    _cap_rejected: set[int] = field(default_factory=set)
    # 进度条上当前所在的格（蓝色高亮）：连续两次读到同一个数才采用。剩余轮数按它算
    progress: int | None = None
    _progress_pending: int | None = None
    _progress_floor: int = 0  # 这一局采用过的最大的当前格（进度回到开头时用来认出新的一局）
    # 轮次、星数、进度条各自连续几次读到比这一局到过的低（读到的次数, 最近一次的时刻, 那一次的读数），见 REGRESS_LABEL
    _low: dict[str, tuple[int, float, int | None]] = field(default_factory=_no_low)

    # ---- 「查看筹码」面板、离开后的核对
    # 识别暂停很久后恢复：下一次读到自己的「查看筹码」面板时核对已有筹码（见 RESUME_CHECK_S）
    _resume_check: bool = False
    # 核对第一次读到面板时的（已有筹码, 各自来源, 三选一候选, 面板开着期间只有一页、第一页上认出过的筹码（dict 当有序集合））；
    # 下不了结论时后续几帧接着用（只看只有一页、第一页；只看到过后面的页时面板关了也留着，见 session.panel.read_panel）
    _resume_base: tuple | None = None
    # 核对看过只有一页、第一页以后，面板从什么时候起看不到（Session 的时钟；看到面板时清掉）：看不到不足
    # session.panel.RESUME_TURN_S 秒的当翻页动画那几帧，核对留着，见 session.panel.read_panel
    _resume_gone: float | None = None
    # 已有筹码未知（启动时不沿用）：进入对局后请用户打开「查看筹码」面板。启动参数里给了已有筹码时不用。
    # 这两项新的一局照旧沿用（见类的说明）
    panel_needed: bool = False
    _panel_prompted: bool = False
    # 还没有对局状态时读到过对局外的画面（刚打开时在主菜单、房间里等，见 offgame.on_out_of_game）：进对局时还在开局的算从开局
    # 看起，不请用户打开面板；先读到不带轮次的读数时从什么时候起等轮次（Session 的时钟）。见 Session._entered_game
    _outside_seen: bool = False
    _panel_wait: float | None = None
    _last_panel: tuple | None = None  # 上一次发 panel_read 时的（已有筹码, 认不出的格数, 第几页, 这一页认出的筹码）
    # 连着几帧读成「只有一页」（数到 panel.ONLY_FRAMES 为止）：记着的不少于一页、或者 _paged 时要连着这么多帧才按只有一页
    # 改，见 session.panel.read_panel；读到别的页、看不清、把握不够、队友的页签、面板关了都从 0 数起
    _only_frames: int = 0
    # 这一局认出过第一页、中间页或最后一页（筹码多于一页）：同一局里筹码只增不减，之后读成只有一页的都要连着几帧才用
    _paged: bool = False
    _last_panel_tab: tuple[int, int] | None = None  # 上一次提示「请点击自己的页签」时的（选中的, 自己的）

    # ---- 队友、畸变组件
    # 队友（玩家列表上除自己以外的各行）：[{hero: 角色名或 None, bot: 是否电脑, row: 第几行}]
    teammates: list[dict] = field(default_factory=list)
    _mate_votes: dict[int, dict[str, int]] = field(default_factory=dict)
    _mate_bots: dict[int, bool] = field(default_factory=dict)
    _mate_name_votes: dict[int, tuple[int, int]] = field(default_factory=dict)  # 行 →（名字像电脑的次数, 像真人的次数）
    _mate_rows: set[int] = field(default_factory=set)
    mutators: list[str] = field(default_factory=list)  # 本局的畸变组件（对局外读到、留给下一局的见 PendingNextGame）

    # ---- 三选一的记账
    # 最近一次关闭的三选一（候选的标准名, 记下的筹码或 None），同一组候选紧接着又出现时撤销
    _last_closed: tuple[tuple, str | None] | None = None
    # 上一次在对局画面上读到当前星数以来出现过几次三选一；星数增加后还差几次三选一没看到
    _selects_since_read: int = 0
    _gain: dict | None = None
    unrecorded: int = 0  # 待补录的筹码数（星数增加却没有对应的三选一记录）
    # 角色合作挑战等级 Lv6 起每升一星刷新次数 +1。不读等级，从画面上的刷新次数推断：
    # 升星后第一次三选一的「刷新 ×N」比升星前最后读到的多 1 → 有这项加成（True），相同 → 没有（False），还没判断过为 None。
    # 只影响读不出刷新次数时的默认值（Session._default_rerolls）；_rerolls_seen 是最近一次读到的（星数, 刷新次数）
    per_star_rerolls: bool | None = None
    _rerolls_seen: tuple[int, int] | None = None
    # 循环往复给的额外那次三选一（State.endless_pending、endless_given、endless_due，规则见 Session.endless_pending、
    # endless_given）：在三选一里看到选中循环往复时（漏记、用户补录时点了它的，按漏记的那次三选一）记下到期的轮次（那时的轮次 + rules.toml
    # [endless_cycle] delay，按那次三选一的来源）；那时轮次读不出、或循环往复是面板核对、手动加进来的时不记。
    # _endless_given 是到期以后当作「额外那次」的那一次三选一的候选，同一组候选后来改判了来源时撤回；_endless_open 是
    # 正开着的这一次三选一就是它（刷新换了候选也算，新的一次三选一出现时清掉）；_endless_chip 是那一次选中的筹码（漏记、
    # 补录的也算）
    _endless_due: int | None = None
    _endless_given: tuple | None = None
    _endless_open: bool = False
    _endless_chip: str | None = None
    # 最近一次漏记的三选一（missed_offer）当时的（轮次, 来源, 是不是循环往复给的额外那次）：用户补录时按它记循环往复
    # （Session.resolve_missed）
    _missed_at: tuple | None = None
    # 认三选一是升星还是筹码商店（规则见 Session._source_of）。画面上的两样迹象各记最后一次看到的（Session 的时钟, 读数）：
    # 升星横幅上的名次（从 1 数，Session.on_levelup_banner）、筹码商店的购买窗上的价格（读不出为 None，on_shop_window）。
    # 一次三选一出现时收进那一次的记录、清掉（Session._take_cues）：一个迹象只算给它之后的第一次三选一
    _banner_cue: tuple[float, int] | None = None
    _shop_cue: tuple[float, int | None] | None = None
    # 最近一次出现的三选一（刷新不算新的一次）认来源要的几项（dict，各项见 Session._select_opened）：出现之前的星数、收下的
    # 横幅和购买窗、定的来源和依据、关掉以后补准的来源等。同一组候选关了又出现时接着用（编号换成新的）
    _select_rec: dict | None = None
    # 上一次对账（Session._settle_sources：关掉以后在对局画面上读到星数时补准来源）以来出现的各次三选一的记录，先后照出现的
    # 先后（和 _select_rec 是同一些 dict）
    _source_books: list[dict] = field(default_factory=list)

    # ---- 撤销后的记忆、立绘模板
    _ignore_hero: str | None = None  # 撤销「角色换了」的新一局后，不再把这个角色当成新一局的迹象
    _select_guard: bool = False  # 撤销「选角界面」的新一局后，回到对局画面之前不再看选角界面
    # 手动指定的角色和立绘识别结果不同（或立绘无法识别）时，把这一局的立绘存成这个角色的模板（资料库里没有的新皮肤下次就认得出）
    template_wanted: str | None = None

    # ---- 本局难度
    # 本局难度的画面读数：依据（OUT_OF_GAME_LABEL 里除奖励画面以外的几种）→ 难度名，后读到的排在后面
    # （三种房间取最近读到的一种）。不算对局状态（has_game_state）：房间里读到的难度要留到选角界面之后，
    # 算进去的话选角界面会把它当成上一局的状态清掉。合并规则见 Session.difficulty_info
    difficulty_evidence: dict[str, str] = field(default_factory=dict)
    # 本局还没有对局内的读数（轮次、星数、三选一）：第一次读到时用掉对局外读到的难度和地图（见 Session._entered_game）
    _in_game_seen: bool = False
    # 最近一次记下的（difficulty_info, 冲突说明）：变了才记识别读数，胶囊上的写法变了才发 status
    _difficulty_key: tuple | None = None

    # ---- 对局结束
    # 本局已结束（结算画面等，见 Session.on_out_of_game）：状态保留到新的一局，之后的读数不再记进这一局。只由新的一局
    # 清掉（撤销房间等结束以后开的新一局时一起撤回，见 Session.undo_new_game）
    game_over: bool = False
    # 结束时的 {why, map, difficulty, level, rounds, cap, difficulty_source, cap_rejected, outcome, how, map_source,
    # map_in_game}，见 Session.game_over_info
    _ended: dict | None = None
    # 胜负的依据（见 offgame.outcome）：读到通关画面「VICTORY」的时刻、读到打输横幅「GAME OVER」的时刻、结束的时刻（时钟秒），
    # 结束后读到过奖励画面没有、奖励画面上「星币」那一格的数（读不出为 None）
    _victory_at: float | None = None
    _game_over_at: float | None = None
    _ended_at: float | None = None
    _reward_seen: bool = False
    _reward_coins: int | None = None
    _end_votes: tuple[int, int, float] | None = None  # 结束后读到的轮次（读数, 连续次数, 时刻）
    # 对局状态还在时第一次读到的房间、「匹配成功」画面、奖励画面或主菜单（种类, 时刻, 读到之前留给下一局的难度, 地图）：
    # 再读到一次才结束本局，见 offgame.confirmed；回到对局画面就作废，留给下一局的难度和地图退回读到之前
    # （Session._entered_game）
    _out_pending: tuple[str, float, dict, tuple | None] | None = None
    # 本局是房间、「匹配成功」画面、奖励画面或主菜单结束的（不是结算画面）：之后开的新一局可以撤销，见 Session._start_next_game
    _ended_by_screen: bool = False
    # 房间等画面结束本局时清掉的几项（漏记补录、待补录、「查看筹码」面板提示、刚关掉的那组候选）和结束前留给下一局的
    # 难度、地图（{属性名: 值}）：撤销之后开的新一局、断线以后连回这一局时放回，见 Session._revoke_end。结算画面结束的、
    # 还没结束时为 None
    _end_cleared: dict | None = None
    # 本局是哪种画面结束的（offgame.SCREEN_RULES 的键；后来又读到结算画面时改成结算画面），结束以后又读到了主菜单以外的
    # 对局外画面没有（确实离开了这一局；结束本局的两次里先认出的那一次是这些画面的也算，见 offgame.end_game），以及刚当成
    # 断线以后连回了这一局、还没再认出这一局的角色：见 offgame.round_after_game
    _end_screen: str | None = None
    # 结算画面结束本局以后等识别数据表（Session.on_result_table）：只在开着对局报告时等（Session.wants_table），
    # 认定了一份表（_table_done：读到 consistent 为真的，或记过一份以后又读稳了一份、数不比它多）、认出别的对局外画面、
    # 开了新的一局或满 TABLE_WAIT_S 秒时结束。
    # _table_pending 是 consistent 为假时上一次读到的各项（见 _table_signature），再读到完全一样的才算读到一份。
    # _table_known 是这一局最近一次按 consistent 为假记下的表读到了几个数（见 _table_known_count）：淡入时先记下
    # 不完整的，之后读到数更多的才再记一条；0 表示这一局还没按 consistent 为假记过表。consistent 为真记下的那一条
    # 不更新它（_table_done 已经挡住之后的读数）
    _table_wait: bool = False
    _table_done: bool = False
    _table_at: float | None = None
    _table_pending: tuple | None = None
    _table_known: int = 0
    _left: bool = False
    _rejoined: bool = False
    # 地图专属筹码推出的地图和加载画面等读到的冲突、还没改时：（推出的地图或几张地图, 那一次的专属筹码和所在的格子），
    # 见 Session._chip_map_confirmed
    _chip_vote: tuple | None = None

    # ---- 筹码方案（这一局实际用哪套每次现算，见 Session.scheme_choice；这里只记两样）
    # 这一局不再按地图自动换方案（Session.stop_map_scheme：点了「换回原来的」，或在悬浮窗上手动换了方案）：之后这一局都用
    # 保存的方案。新的一局换上新对象就清掉，撤销新的一局时跟着旧的那一局回来
    map_scheme_off: bool = False
    # 这一局最近一次告诉过用户的按地图换方案：（角色, 换成的方案 id），见 Session.take_map_switch。同一局里接着换着同一套
    # 不再提示；撤销新的一局时跟着回来，换回那一局时不重复提示。没有按地图换着时为 None
    _map_switch_told: tuple[str, str] | None = None

    @classmethod
    def from_settings(cls, settings) -> GameState:
        """启动时的对局状态：命令行给了轮次、星数、地图、已有筹码时按它们（轮次、星数算已确认），其余同新的一局；
        已有筹码未知时请用户打开「查看筹码」面板。"""
        return cls(
            _round_seen=CONFIRM if settings.round is not None else 0, _round_floor=settings.round or 0,
            _star_seen=CONFIRM if settings.star_read else 0, _star_floor=settings.star if settings.star_read else 0,
            map_candidates=[settings.map] if settings.map else [],
            chip_sources={n: "given" for n in settings.owned},
            owned_sources={n: "given" for n in settings.owned}, panel_needed=not settings.owned,
        )


@dataclass(slots=True, eq=False)
class PendingNextGame:
    """对局外读到、留给下一局的读数，后读到的覆盖先读到的。新的一局开始时（Session.new_game）难度和畸变组件就用掉；
    地图等到这一局第一次对局内读数时再用（Session._apply_pending），地图算对局状态，选角界面那一帧就用上的话，
    下一帧会把它当成上一局的状态、再判一次新的一局。判定新的一局前和 GameState 一起存进撤销点。"""

    # 房间、「匹配成功」画面上读到的难度（依据 → 难度名，写法同 GameState.difficulty_evidence）
    _next_difficulty: dict[str, str] = field(default_factory=dict)
    # 加载画面（本局已结束后）、「匹配成功」画面、房间设置上读到的地图（地图名, map_source）
    _next_map: tuple[str, str] | None = None
    _next_mutators: list[str] = field(default_factory=list)  # 在对局外读到的畸变组件


class UndoPoint(NamedTuple):
    """判定新的一局之前的样子（撤销新一局时换回去）：这一局的状态和留给下一局的读数（两个对象原样留着：新的一局换上的是
    新对象，没有人再改旧的），以及设置里的对局字段和角色（Settings 是同一个对象，各项的值另存一份）。"""

    game: GameState
    pending: PendingNextGame
    settings: dict

    @classmethod
    def take(cls, game: GameState, pending: PendingNextGame, settings) -> UndoPoint:
        values = {k: getattr(settings, k) for k in (*GAME_FIELDS, "hero")}
        values["owned"] = list(values["owned"])  # 已有筹码的列表会被原地改（三选一记下、补录）：存一份
        return cls(game, pending, values)
