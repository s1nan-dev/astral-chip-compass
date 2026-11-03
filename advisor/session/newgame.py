"""新的一局怎么判：轮次、星数的读数要连续读到几次才采用（投票），对局中途判定新的一局之前和另一项读数互相印证
（NewGameJudge），以及这些门槛常量。

NewGameJudge 记着最近一次读到的轮次、星数、进度条和读不到的时刻：判定新的一局时要跨局比较，所以新的一局不清、撤销也不退回。
这一局的投票状态（还没采用的读数、连续读到的次数、确实到过的下限、往回退的计数）在 GameState 里，跟着新的一局清空、撤销恢复。
判出来以后开新的一局、存撤销点在 Session（_auto_new_game、_start_next_game）。
"""
from __future__ import annotations

CONFIRM = 2  # 轮次或星数变小、大幅跳变，角色变了，要连续看到几次才算数，防止偶尔认错
# 轮次、星数「连续读到」的两次：中间读不到的帧（战斗等）不打断连续，但隔了这么多秒以上、中间又看过画面却没读到的
# 不算（自动识别时隔几分钟的两次同样的读错）；中间根本没有读过的（手动模式两次按热键之间）照旧算。见 NewGameJudge.pending_votes
CONFIRM_S = 20.0
ROUND_STEP = 1  # 轮次一次往前走不超过这么多时直接采用；跳得更多多半是读错，要连续读到同一个数才采用
STAR_STEP = 1  # 星数同理：一次多一颗直接采用（升星），多两颗以上要连续读到
ROUND_RANGE = (1, 20)  # 合理的轮次：第 1 轮起，不超过各地图进度上限的最大值；范围外的读数一律忽略
# 轮次变小判为新的一局时，新值不超过这么多（新的一局从第 1 轮开始，每 3 秒读一次轮次，一般在第 1、2 轮就读到）。
# 读错一位数字（「14」读成「11」）造成的「变小」因此不会清空这一局的记录。
NEWGAME_ROUND_MAX = 5
# 对局中途判定新的一局要互相印证：轮次、星数、角色里有一项说「开了新的一局」，而另一项在这么多秒内的最近一次读数
# 还说「这一局在继续」（轮次读数和这一局确实到过的都已超过 NEWGAME_ROUND_MAX，读错一位数字的较小轮次照样算；
# 星数不小于确实到过的、且至少 1 星；进度条见 NEWGAME_PROGRESS_MIN），
# 就按读错处理，不清空。真的开了新的一局时轮次回到第 1 轮、星数回到 0 星，另一项很快也会跟着变，不会一直挡住。
NEWGAME_VETO_S = 20.0
NEWGAME_PROGRESS_MIN = 4  # 进度条当前格不小于这个数时，一定不是刚开始的新一局（新的一局从第 1 格开始）
NEWGAME_PROGRESS_RESET = 2  # 进度条连续两次读到不超过这一格、而这一局到过第 5 格以后：进度回到了开头，是新的一局
# 进度条比这一局到过的最大格退回这么多格以上：任务奖励「进度-1」一次只退 1 格（实测最多两个任务接连完成），
# 退这么多只能是开了新的一局（游戏切到后台期间上一局结束、下一局开始时会出现）
NEWGAME_PROGRESS_DROP = 3
# 进度条退回 NEWGAME_PROGRESS_DROP 格以上，同时轮次比这一局到过的小或星数比到过的少（各自连续读到两次）：
# 就是新的一局，不再按「超过第 5 轮的变小是读错」处理，也推翻另一项「这一局在继续」的否决。
# 只认进度条作这个证据（最可靠）：读不到进度条时照旧，宁可漏判，由离开后的面板核对兜底
REGRESS_LABEL = {"round": "轮次变小", "star": "星数变少", "progress": "进度条退回"}


class NewGameJudge:
    """轮次、星数的投票和新一局的判定（见模块说明）。各项状态直接写（session.judge._last_round_read 等）。
    读写这一局的状态（session.game）和设置里的轮次、星数；判定的依据记识别读数（trace）。"""

    FIELDS = ("_round_blank_at", "_star_blank_at", "_last_round_read", "_last_star_read", "_last_progress_read")
    __slots__ = ("session", *FIELDS)

    def __init__(self, session):
        self.session = session  # 记账（Session）：这一局的状态、设置、时钟和识别读数都经它
        self._round_blank_at: float | None = None  # 最近一次看了画面、没读到轮次的时刻（pending_votes 用）
        self._star_blank_at: float | None = None  # 同上，星数
        # 最近一次读到的轮次、星数、进度条当前格（读数, 时刻）：判定新的一局时互相印证（见 NEWGAME_VETO_S）
        self._last_round_read: tuple[int, float] | None = None
        self._last_star_read: tuple[int, float] | None = None
        self._last_progress_read: tuple[int, float] | None = None

    # ------------------------------------------------------------ 投票

    def read_round(self, rnd: int) -> str | None:
        """处理一次读到的轮次，返回 "changed"（采用了新值）、"newgame" 或 None（没变化或还在等确认）。

        往前走一小步（ROUND_STEP 以内）直接采用；大幅跳变或变小要连续读到同一个数才采用（怎样算连续见 pending_votes）。
        变小到这一局「确实到过」的轮次（_round_floor）以下、且新值不超过 NEWGAME_ROUND_MAX 才是新的一局；
        没到它以下（或新值较大，多半是读错了一位数字），按改正之前的读错处理。
        确实到过的轮次：
          一步一步走到（或这一局第一次读到）、又读到了两次的；
          大幅跳变来的不算，读到再多次也不算：同一个静止画面，读错一次往往会原样再错好几次；
          从确实到过的轮次往前跳、跳到的数也连续读到了两次，跳之前的那个算（游戏确实走过了它）；从跳变来的轮次再跳一次时
          不算（它本身就没算过）；
          变小后连续读到两次、改正过来的算。
        """
        s, g = self.session.settings, self.session.game
        if s.round is None or s.round < rnd <= s.round + ROUND_STEP:
            g._round_pending = None
            confirmed = s.round is not None and g._round_seen >= CONFIRM and not g._round_jumped
            g._round_before_step = s.round if confirmed else None
            s.round, g._round_seen, g._round_jumped = rnd, 1, False
            return "changed"
        if rnd == s.round:
            g._round_pending = None
            g._round_seen += 1
            if g._round_seen >= CONFIRM and not g._round_jumped:
                g._round_floor = max(g._round_floor, rnd)
            return None
        votes = self.pending_votes(g._round_pending, rnd, self._round_blank_at)
        g._round_pending = (rnd, votes, self.session._clock())
        if votes < CONFIRM:
            return None
        g._round_pending = None
        if rnd == g._round_before_step and s.round == rnd + ROUND_STEP and g._round_floor <= s.round:
            # 读回往前一步之前确认过的轮次：那一步是静止画面上连读错的（「05」读成「06」），改正，「确实到过」也退回去
            s.round, g._round_seen, g._round_jumped = rnd, votes, False
            g._round_floor = rnd
            g._round_before_step = None
            return "changed"
        if rnd < g._round_floor and rnd <= NEWGAME_ROUND_MAX:
            return "newgame"
        if rnd < g._round_floor and self.regression_evidence("round") is not None:
            # 超过第 NEWGAME_ROUND_MAX 轮的变小本来按改正读错处理；进度条（或星数）也一起往回退了，
            # 是游戏切到后台期间开了新的一局、回来时已经过了前几轮
            return "newgame"
        jumped = rnd > s.round
        if jumped and g._round_seen >= CONFIRM and not g._round_jumped:
            g._round_floor = max(g._round_floor, s.round)
        s.round, g._round_seen, g._round_jumped = rnd, votes, jumped
        if not jumped:
            g._round_floor = max(g._round_floor, rnd)
        return "changed"

    def read_star(self, n: int) -> str | None:
        """处理一次读到的星数，返回 "changed"（采用了新值）、"newgame" 或 None（没变化或还在等确认）。

        和轮次一样（见 read_round）：多一颗（升星）直接采用；多两颗以上或变少，要连续读到同一个数才采用（见
        pending_votes）；少于这一局确实到过的星数（_star_floor）才是新的一局，否则是改正之前的读错。跳变来的星数不算确实
        到过，从它再跳一次时也不算。
        三选一界面上读到的和对局画面上读到的同样算数（队友不能和自己是同一个角色，三选一界面上不会把队友那一行认成自己）。
        """
        s, g = self.session.settings, self.session.game
        if not s.star_read or s.star < n <= s.star + STAR_STEP:
            g._star_pending = None
            s.star, s.star_read, g._star_seen, g._star_jumped = n, True, 1, False
            return "changed"
        if n == s.star:
            g._star_pending = None
            g._star_seen += 1
            if g._star_seen >= CONFIRM and not g._star_jumped:
                g._star_floor = max(g._star_floor, n)
            return None
        votes = self.pending_votes(g._star_pending, n, self._star_blank_at)
        g._star_pending = (n, votes, self.session._clock())
        if votes < CONFIRM:
            return None
        g._star_pending = None
        if n < g._star_floor:
            return "newgame"
        jumped = n > s.star
        if jumped and g._star_seen >= CONFIRM and not g._star_jumped:
            g._star_floor = max(g._star_floor, s.star)
        s.star, g._star_seen, g._star_jumped = n, votes, jumped
        if not jumped:
            g._star_floor = max(g._star_floor, n)
        return "changed"

    def pending_votes(self, pending: tuple | None, value: int, blank_at: float | None) -> int:
        """轮次、星数还没采用的读数（_round_pending、_star_pending：读数, 连续读到的次数, 最近一次的时刻）加上这一次读到
        value 以后，连续读到了几次。读不到的帧不打断「连续」；但离上一次超过 CONFIRM_S 秒、中间又看过画面却没读到
        （blank_at：最近一次读不到的时刻，在上一次之后）的，重新数：自动识别时隔几分钟的两次同样的读错不算连续两次。
        中间根本没有读过的（手动模式两次按热键之间）照旧算连续。"""
        if pending is None or pending[0] != value:
            return 1
        if self.session._clock() - pending[2] > CONFIRM_S and blank_at is not None and blank_at > pending[2]:
            return 1
        return pending[1] + 1

    @property
    def round_jump_pending(self) -> bool:
        """读到了往前大幅跳变的轮次，还在等再读到一次（变小的不算：可能是新的一局，宁可确认得慢一点）。"""
        pending, current = self.session.game._round_pending, self.session.settings.round
        return pending is not None and current is not None and pending[0] > current

    @property
    def round_drop_pending(self) -> bool:
        """读到了变小的轮次，还在等再读到一次（可能开了新的一局，也可能是读错）。"""
        pending, current = self.session.game._round_pending, self.session.settings.round
        return pending is not None and current is not None and pending[0] < current

    # ------------------------------------------------------------ 互相印证

    def note_low(self, name: str, value: int, low: bool) -> None:
        """记下 name（round / star / progress）这次的读数，以及它是否比这一局到过的低：连续低的次数和时刻。"""
        lows = self.session.game._low
        count = lows[name][0] + 1 if low else 0
        lows[name] = (count, self.session._clock(), value)

    def regressed(self, name: str) -> bool:
        """name 这一项最近 NEWGAME_VETO_S 秒内连续两次以上读到比这一局到过的低。"""
        count, at, _ = self.session.game._low[name]
        return count >= CONFIRM and self.session._clock() - at <= NEWGAME_VETO_S

    def regression_evidence(self, by: str) -> str | None:
        """by（round / star / hero / progress）说开了新的一局时，另一项读数是不是也往回退了（见 REGRESS_LABEL）。
        是的话返回那一项的说明，用来推翻「另一项还说这一局在继续」的否决。

        只认进度条：它最可靠，退回 NEWGAME_PROGRESS_DROP 格以上只能是新的一局。进度条读不到时不推翻
        （轮次、星数、选角界面可能同时读错，宁可漏判，由离开后的面板核对兜底）。
        by 是进度条本身时，要轮次或星数也一起往回退。选角界面不参与：真回到选角界面时，上一局的读数早就超过
        NEWGAME_VETO_S 秒，本来就不会被否决。"""
        if by == "select":
            return None
        if by == "progress":
            return next((REGRESS_LABEL[n] for n in ("round", "star") if self.regressed(n)), None)
        last = self._last_progress_read
        recent = last is not None and self.session._clock() - last[1] <= NEWGAME_VETO_S
        return REGRESS_LABEL["progress"] if recent and self.regressed("progress") else None

    def game_continues(self, by: str) -> str | None:
        """by（round / star / hero / select）说开了新的一局时，另一项的最近读数是否还说这一局在继续（见 NEWGAME_VETO_S）。
        是的话返回那一项读数的说明，调用方按读错处理；否则返回 None。"""
        now = self.session._clock()
        g = self.session.game
        # 进度条确实回到了开头（连续两次读到第 1、2 格，之前到过第 5 格以后）：这是新一局的证据，其他读数不再否决
        if g.progress is not None and g.progress <= NEWGAME_PROGRESS_RESET and g._progress_floor >= 5:
            return None
        if by != "round" and self._last_round_read is not None:
            rnd, at = self._last_round_read
            # 新的一局开头 20 秒内轮次不会超过第 NEWGAME_ROUND_MAX 轮；read_round 也不把超过它的变小当成新一局
            if now - at <= NEWGAME_VETO_S and rnd > NEWGAME_ROUND_MAX and g._round_floor > NEWGAME_ROUND_MAX:
                return f"轮次仍为第 {rnd} 轮"
        if by != "star" and self._last_star_read is not None:
            n, at = self._last_star_read
            if now - at <= NEWGAME_VETO_S and n >= g._star_floor >= 1:
                return f"星数仍为 {n} 星"
        if self._last_progress_read is not None and g.progress is not None:
            # 进度条：新的一局从第 1 格开始；读到的格还在这一局记下的附近（任务奖励「进度-1」接连两次会退 2 格；
            # 退 NEWGAME_PROGRESS_DROP 格以上才算新一局的证据，两者之间不留空档）
            n, at = self._last_progress_read
            if now - at <= NEWGAME_VETO_S and n >= NEWGAME_PROGRESS_MIN and n > g.progress - NEWGAME_PROGRESS_DROP:
                return f"进度仍在第 {n} 格"
        return None

    def vetoed(self, by: str, why: str) -> bool:
        """判定新的一局前互相印证：被另一项读数否决时记一条识别读数（诊断用），返回 True。

        另一项读数也往回退了（regression_evidence）时不否决：两项一起退回，是开了新的一局。"""
        evidence = self.regression_evidence(by)
        if evidence is not None:
            self.session.trace("newgame_corroborated", by=by, why=why, evidence=evidence)
            return False
        against = self.game_continues(by)
        if against is None:
            return False
        self.session.trace("newgame_vetoed", by=by, why=why, against=against)
        return True

    def with_evidence(self, by: str, why: str) -> str:
        """新一局的依据里补上另一项也往回退了的说明（有的话）。"""
        evidence = self.regression_evidence(by)
        return f"{why}，{evidence}" if evidence else why
