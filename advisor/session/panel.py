"""「查看筹码」面板核对：面板上读到的筹码和原来记着的已有筹码怎么合起来排先后（panel_order），哪些三选一的选择记录要按
面板改正（panel_fixes），哪些记错能由面板上的同组候选解释（explained：离开后核对新的一局时不算「少了」）。

面板一页 PAGE_SLOTS 格，筹码多于一页时按拿到的先后分页，用两端的箭头翻页（识别出的是第几页见
advisor.vision.chip_panel.PanelReading.page）。每一页只核对已有筹码里对应的那一段，没看到的那几页照记着的不动：
  only   只有一页：面板上的就是全部已有筹码。记着的不少于一页（PAGE_SLOTS 个），或者这一局认出过第一页、中间页、最后一页
          （同一局里筹码只增不减，面板不会再变回只有一页）时，要连着 ONLY_FRAMES 帧都读成只有一页才按它改：亮着的箭头被
          浅色的东西盖住时会读成不亮，按只有一页改就会去掉一页以外的全部，只有一帧的当看不清；
  first   第一页（后面还有）：对应最早拿的 PAGE_SLOTS 个；
  last    最后一页（前面还有）：对应第 PAGE_SLOTS + 1 个以后的；多于两页时是第几页按内容挑（page_span）；
  middle  中间页（前后都有，多于两页才有）：第几页光看箭头分不出，按内容和已有筹码一组 PAGE_SLOTS 个滑动着比，对得最好、
          又对得上一半以上的那一组；对不上就不改；
  空串    箭头看不清：不知道对应哪一段，不改。
已有筹码的先后：三选一记下的、面板上读到的就是拿到的先后（三选一关掉时接在末尾，面板读到后按格子排），和面板一致；补录、
手动加的接在加的时候的末尾，可能比实际拿到的晚几位，读到面板以后按面板排（page_merge 对这几种另有照顾）。

panel_order、panel_fixes、explained、page_span、page_merge、resume_missing 只做计算，参数都显式传入（原来记着的已有筹码、
各自的来源、三选一记下的筹码当时的三个候选）。read_panel 是读到面板以后的记账（Session.on_panel）：按面板写入已有筹码、
改正选择记录，离开很久后回来的核对也在这里；它接收记账（Session），读写这一局的状态（Session.game），事件经记账发。"""
from __future__ import annotations

# 识别暂停（游戏切到后台、找不到游戏窗口等）这么多秒以上，回来后请用户打开「查看筹码」面板核对已有筹码：
# 期间可能开了新的一局（同一个角色、轮次和星数又差不多时，只看画面分不出），也可能思考时间用完、游戏代选了筹码
RESUME_CHECK_S = 120
RESUME_MISSING_MIN = 2  # 离开后核对时，面板上少了这么多个之前记下的筹码（同一局里筹码只增不减），判为新的一局
PANEL_MIN_CONFIDENCE = 0.5  # 筹码面板读数的把握低于这个值时不用
PAGE_SLOTS = 10  # 「查看筹码」面板一页的格数（3-4-3 排成三行）；多于这么多个筹码时翻页
PAGES = ("only", "first", "middle", "last")  # 认得出的页（见模块说明）
# 记着的不少于一页（PAGE_SLOTS 个）、或者这一局认出过多于一页的面板时，连着这么多帧都读成只有一页才按只有一页改（见模块
# 说明）；中间隔着别的页、面板关了、把握不够、队友的页签、看不清的都不算连着。两样都不是时一帧就改
ONLY_FRAMES = 2
PAGED = ("first", "middle", "last")  # 认出这几页说明这一局的筹码多于一页（GameState._paged）
# 离开后的核对看过只有一页、第一页、还没下结论时，面板看不到不足这么多秒的当翻页动画那几帧（格子错位、不算面板；翻过去、
# 翻回来照常接着核对），看不到这么久以后再读到不是面板的画面，才算面板关了、核对到此为止。取识别流程在面板刚看不到时照样
# 每帧都看的那段时间（vision.pipeline 的 PANEL_GRACE_S）：翻页动画那几帧都在这段时间里
RESUME_TURN_S = 2.0
# 离开后看第一页核对时，记着的前 PAGE_SLOTS 个里「被挤进来的」（漏记、补录得晚的把后面的挤出了这一段）最多原谅这么多个；
# 第一页上不在这一段里的筹码多于这么多个时一个都不原谅：不是漏记了几个，更像换了一局（见 resume_missing）
RESUME_SHIFT_MAX = 3
# 中间页：面板上认出的筹码至少这么多（比例）在记着的那一组里，才算对上；最后一页：记着的最后一页那一段至少这么多（比例）
# 对得上，才算是那一页（见 page_span）
MIDDLE_MATCH = 0.5
# 在已有筹码里的位置就是拿到的先后的来源：三选一当场记下的、面板上读到的。补录、手动加的位置不一定（见 page_merge）
PLACED = ("pick", "panel")


def panel_order(reading, owned: list[str], keep: list[str], old: list[str]) -> list[str]:
    """面板上认出的筹码 owned 和保留的原记录 keep 合起来，按面板上的位置排（格子里的筹码按获得顺序排列）。

    有每一格的读数时（PanelReading.slots）按格子走一遍：认出的格子放认出的筹码；认不出的格子（被遮挡、无法识别、
    和前面的格子重复、资料库里没有）按顺序放保留的筹码，保留的放完后不再放。
    没有每一格的读数时，保留的筹码插在原记录里排在它前面、已经放进结果的最近一个筹码之后（没有就放在最前）。
    old 是原来记着的已有筹码（按页核对时是对上的那一段）。
    """
    slots = sorted(getattr(reading, "slots", None) or (), key=lambda slot: slot.index)
    if slots:
        out: list[str] = []
        queue = list(keep)
        for slot in slots:
            if slot.state == "empty":
                continue
            name = slot.chip if slot.state == "chip" else None
            if name in owned and name not in out:
                out.append(name)
            elif queue:
                out.append(queue.pop(0))
        # 格子读数和 owned 对不上时（不应发生），没放进去的接在末尾
        return out + [n for n in owned if n not in out] + [n for n in queue if n not in out]
    out = list(owned)
    for name in keep:
        before = old[: old.index(name)]
        prev = next((p for p in reversed(before) if p in out), None)
        out.insert(out.index(prev) + 1 if prev is not None else 0, name)
    return out


def panel_fixes(old: list[str], seen: list[str], new: list[str], sources: dict,
                offers: dict) -> list[tuple[str, str]]:
    """面板读数和三选一的记录对不上时，哪些选择记录要改：[(原来记下的, 面板上实际的)]。

    原来按三选一（或补录）记下的筹码面板上没有，而同一组候选里正好有一个面板上有、之前又没记着，
    就是当时实际拿到的那个（例如思考时间用完、游戏自动拿了最左边的筹码，而绿框选中的是别的）。
    同一组里对得上的不止一个时不改。old 是原来记着的已有筹码，seen 是面板上认出的筹码，new 是按面板更新后的已有筹码；
    sources、offers 是已有筹码各自的来源和三选一记下的筹码当时的三个候选。
    """
    fixes = []
    for wrong in old:
        if wrong in new or sources.get(wrong) not in ("pick", "missed"):
            continue
        offer = offers.get(wrong) or ()
        right = [n for n in offer if n and n != wrong and n in seen and n not in old]
        if len(right) == 1 and all(right[0] != r for _, r in fixes):
            fixes.append((wrong, right[0]))
    return fixes


def explained(seen, old: list[str], sources: dict, offers: dict, leftmost_only: bool = False) -> set[str]:
    """按三选一记下（old 里来源是 pick / missed）、面板上没认出的筹码中，能由面板上的同组候选解释的三选一记错：
    ① 同组最左边的那个在面板上、之前没记着：思考时间用完，游戏拿了最左边的，绿框选中的是别的；
    ② 同组另外两个候选里正好一个在面板上、之前没记着：选中后又改选，或绿框读错；
    ③ 同组最左边那格当时没读出名字：面板上多出来的、不是这一组读得出的候选的筹码可以抵它。
    面板上的每个筹码只抵一条记录。离开后核对时这些不算「少了」（同一局不误判优先）；leftmost_only 时只按 ①。
    记录改成哪个仍按 panel_fixes（同一组里对得上的不止一个时不改）。"""
    records = [w for w in old if w not in seen and sources.get(w) in ("pick", "missed")]
    extra = [n for n in seen if n not in old]
    out: set[str] = set()
    used: set[str] = set()
    for w in records:  # ①
        left = (tuple(offers.get(w) or ()) + (None,))[0]
        if left and left != w and left in extra and left not in used:
            out.add(w)
            used.add(left)
    if leftmost_only:
        return out
    for w in records:  # ②
        if w in out:
            continue
        right = [n for n in (offers.get(w) or ()) if n and n != w and n in extra and n not in used]
        if len(right) == 1:
            out.add(w)
            used.add(right[0])
    for w in records:  # ③
        offer = tuple(offers.get(w) or ())
        if w in out or offer[:1] != (None,):
            continue
        n = next((n for n in extra if n not in used and n not in offer), None)
        if n is not None:
            out.add(w)
            used.add(n)
    return out


def _matched(seen: list[str], seg: list[str], old: list[str], sources: dict, offers: dict) -> int:
    """面板这一页认出的筹码（seen）对得上已有筹码里 seg 这一段的有几个：就在这一段里的，加上能解释这一段里三选一记错的
    （同组候选、已有筹码里哪儿都没有，见 explained）。old 是全部已有筹码。"""
    local = [n for n in seen if n in seg or n not in old]
    return sum(1 for n in seen if n in seg) + len(explained(local, seg, sources, offers))


def page_span(page: str, old: list[str], seen: list[str], sources: dict, offers: dict,
              unknown: int = 0) -> tuple[int, int] | None:
    """面板这一页对应已有筹码里的哪一段：（起, 止），old[起:止]。对不上时返回 None（不改）。unknown 是这一页认不出的格数。

    only 是全部；first 是最早拿的 PAGE_SLOTS 个。
    last：最后一页是第几页光看箭头分不出（只知道前面还有），按记着的个数，是记着的最后一页（这一段是那一页起的全部），或者
      再后面一页（记着的正好满页、或者漏记了几个；这一段是空的，这一页上的接在末尾，什么都不去掉）。记着的最后一页那一段
      对得上的（加上认不出的格数）至少一半（MIDDLE_MATCH）才挑它：挑了它，那一段里这一页上没有的三选一记下的、面板读到的
      都要去掉；大半对不上时这一页多半是再后面一页（例如前面的页上漏记了一个，记着的最后一页那一段其实大半在前一页上；
      或者这一页上的筹码正好是那一段里某次三选一的候选，能「解释」一条记错），挑了就会去掉近一整页。漏记比多记常见，
      拿不准时挑靠后的，挑错了少去掉几个。更靠前的页不挑：那样这一段比一页还长，一个认错的格子就可能把整页去掉。
    middle：一组 PAGE_SLOTS 个滑动着比（中间页是第几页分不出，之前漏记的会让这一组错开几位），挑对得上最多的；一样多时挑
      正好从页头开始的、再挑靠前的。对得上的不到面板上认出的一半（MIDDLE_MATCH），或者对得一样好的几组隔得超过一页
      （分不出是哪一页），返回 None。"""
    n, size = len(old), PAGE_SLOTS
    if page == "only":
        return 0, n
    if page == "first":
        return 0, min(size, n)
    if page == "last":
        k = -(-n // size) - 1  # 记着的最后一页是第几页（0 起）；记着的不超过一页时，最后一页只能是再后面一页
        if k >= 1:
            seg = old[k * size:]
            hit = _matched(seen, seg, old, sources, offers)
            if hit and hit + max(0, unknown) >= MIDDLE_MATCH * len(seg):
                return k * size, n
        return n, n
    if page != "middle" or not seen:
        return None
    scores = [(_matched(seen, old[i : i + size], old, sources, offers), i) for i in range(1, n)]
    if not scores:
        return None
    top = max(score for score, _ in scores)
    if top < MIDDLE_MATCH * len(seen):
        return None
    tied = [i for score, i in scores if score == top]
    if max(tied) - min(tied) > size:
        return None
    start = min(tied, key=lambda i: (i % size != 0, i))
    return start, min(n, start + size)


def page_merge(reading, old: list[str], seen: list[str], unknown: int, span: tuple[int, int], page: str,
               sources: dict, offers: dict) -> tuple[list[str], list[tuple[str, str]]]:
    """按面板这一页改已有筹码里对上的那一段 old[起:止]（span，见 page_span），返回（改好的已有筹码, 改正的选择记录
    [(原来记下的, 面板上实际的)]，见 panel_fixes）。seen 是这一页认出的筹码，unknown 是这一页认不出的格数。

    这一段以外的照旧，只是这一页上认出的筹码如果记在别处，挪到这一页（面板上它就在这一页，同一个筹码只能拿一次）。
    这一段里：认不出的格子最多保留同样多个原记录（能由同组最左边解释的三选一记错不保留，见 explained），和认出的一起按
    格子排（panel_order）；能由面板上的同组候选解释的三选一记错改成面板上的那个（panel_fixes）。其余面板上没有的：
      only    去掉（面板上的就是全部）；
      first、middle  挪到这一页之后：可能在没看到的下一页（之前漏记了这一页上的筹码，后面的就被挤了进来），不去掉；
      last    位置可靠的（三选一当场记下的、面板上读到的，PLACED）去掉：它后面再没有别的页了；补录、手动加的挪到这一页
              之前：它们是加的时候接在末尾的，可能其实在前面的页上，不去掉。"""
    start, end = span
    seg = old[start:end]
    local = [n for n in seen if n in seg or n not in old]
    excused = explained(local, seg, sources, offers, leftmost_only=True)
    keep = [n for n in seg if n not in seen and n not in excused][:unknown]
    block = panel_order(reading, seen, keep, seg)
    before = [n for n in old[:start] if n not in block]
    after = [n for n in old[end:] if n not in block]
    fixes = panel_fixes(old, seen, before + block + after, sources, offers)
    gone = {w for w, _ in fixes} | excused
    left = [n for n in seg if n not in block and n not in gone]
    if page in ("first", "middle"):
        return before + block + left + after, fixes
    if page == "last":
        return before + [n for n in left if sources.get(n) not in PLACED] + block + after, fixes
    return before + block + after, fixes


def resume_missing(page: str, old: list[str], seen: list[str], sources: dict, offers: dict) -> tuple[list[str], int]:
    """离开后核对新的一局：面板上少了离开前记着的哪些筹码（同一局里筹码只增不减），以及其中原谅几个。old、sources、offers
    是第一次读到面板时记着的（Session.game._resume_base），seen 是这几帧面板上认出的筹码（只有一页、第一页的读数才拿来核对，
    见 read_panel）。能由面板上的同组候选解释的三选一记错不算少了（explained）。

    only：面板上的是全部，比全部记着的，不原谅。
    first：第一页是最早拿的 PAGE_SLOTS 个，只比记着的前 PAGE_SLOTS 个。之前漏记了第一页上的筹码（或补录得晚、排到了后面）时，
      之后拿的会被挤进记着的前 PAGE_SLOTS 个，它们其实在下一页，不算少了：这样的只会排在这一段的末尾（后面再没有面板上看得到
      的），个数也不超过面板上有、这一段里没有的筹码（挤它们出去的就是这些）。按这两条原谅。面板上有、这一段里没有的多于
      RESUME_SHIFT_MAX 个时一个都不原谅：记着的大多不在第一页上，不是漏记了几个，更像换了一局（记着的不到一页、下一局的第一页
      又和它们对不上时也是这样，照只有一页时的规矩全算少了）。"""
    if page != "first":
        excused = explained(seen, old, sources, offers)
        return [n for n in old if n not in seen and n not in excused], 0
    window = old[:PAGE_SLOTS]
    local = [n for n in seen if n in window or n not in old]
    excused = explained(local, window, sources, offers)
    missing = [n for n in window if n not in seen and n not in excused]
    last = max((i for i, n in enumerate(window) if n in seen), default=-1)
    trailing = sum(1 for n in window[last + 1 :] if n in missing)
    pushed = sum(1 for n in seen if n not in window) - len(excused)
    if pushed > RESUME_SHIFT_MAX:
        return missing, 0
    return missing, max(0, min(trailing, pushed))


def read_panel(s, reading):
    """喂入一次「查看筹码」面板的识别结果（advisor.vision.chip_panel.PanelReading；不是面板时传 None），s 是记账
    （Session.on_panel）。

    只认自己那一页，按页核对（见模块说明）：只有一页时面板上的就是全部已有筹码，按它写入（来源记为面板，头一次就是在面板上
    读到的筹码来路也记成面板）；第一页、中间页、
    最后一页只改已有筹码里对上的那一段（page_span、page_merge），没看到的照旧。有没认出的格子时，原来记着、面板上没认出的
    筹码保留（最多保留没认出的格数；能由面板上的同组候选解释的三选一记错不保留，见 explained），保留的筹码放回原来的位置
    （见 panel_order）。认不出是第几页（箭头看不清）、中间页对不上时不改。没有页码的读数按只有一页算。记着的不少于一页、
    或者这一局认出过多于一页的面板（GameState._paged）时，读成只有一页要连着 ONLY_FRAMES 帧（GameState._only_frames 数着）
    才按它改，之前的几帧和看不清一样：不改、不发读数、离开后的核对也不拿它判、不结束。
    离开很久后回来（Session.on_resume）的核对也在这里，只看只有一页或第一页（最早拿的 PAGE_SLOTS 个）：面板上少了
    RESUME_MISSING_MIN 个以上之前记下的筹码就判为新的一局（见 resume_missing）；回来时先看到的是后面的页，不拿它判，
    照常按页核对，等看到第一页再判（翻页、面板关了都留着等）。看过第一页、下不了结论的，翻页动画那几帧（不算面板）不打断，
    面板看不到 RESUME_TURN_S 秒以上才算关了、到此为止。
    读数有变化、或这是启动后第一次读到时发 panel_read。按面板改了已有筹码、这次又没判成新的一局时，三选一界面还开着
    （包括收起来去点面板的）就按记下的三选一画面马上重算一次推荐（SelectTracker.recompute），重算的事件排在 panel_read
    后面放进待取队列：玩家把三选一收起来去点「查看筹码」时，读到面板的这一帧推荐就改对了。已有筹码没变（例如之前不知道、
    面板上确实一个都没有）时推荐也不变，不重算；推荐卡片上「还不知道已有筹码」那行提醒由悬浮窗收到 panel_read 时收起。
    本局已结束后读到的不起作用（不记进这一局）。
    """
    if s.game.game_over:
        return None
    # 提示过「请点击自己的页签」以后，面板关了或切到了自己的页签：发 count 为 0 的 panel_tab，悬浮窗收起这条提示
    tab_cleared = s._event("panel_tab", count=0) if s.game._last_panel_tab is not None else None
    if reading is None or not getattr(reading, "is_panel", False):
        s.game._last_panel_tab = None
        s.game._only_frames = 0  # 面板关了（或翻页动画那几帧）：读成只有一页的不再算连着
        base = s.game._resume_base
        if base is not None and base[3]:
            # 离开后的核对读过只有一页、第一页的面板，没下结论就看不到了：翻页动画那几帧也是这样（不算面板），看不到不足
            # RESUME_TURN_S 秒时留着，翻到别的页、翻回第一页照常接着核对；看不到这么久才算面板关了，到此为止。只看到过后面的页
            # 时一直留着，等看到第一页再判：同一局里第一页上的筹码不会变，晚一点看也不会误判
            now = s._clock()
            if s.game._resume_gone is None:
                s.game._resume_gone = now
            elif now - s.game._resume_gone >= RESUME_TURN_S:
                s.game._resume_check, s.game._resume_base, s.game._resume_gone = False, None, None
        return tab_cleared
    gone, s.game._resume_gone = s.game._resume_gone, None  # 面板又看得到了（队友的页签、把握不够的也算开着）
    if gone is not None and s._clock() - gone >= RESUME_TURN_S and s.game._resume_base is not None:
        # 看过第一页、没下结论以后面板看不到了这么久才又看到（这期间没有别的帧来结束核对，例如两帧隔得久）：不是翻页动画，
        # 算面板关过，核对到此为止，这一帧照常按面板写入。不足这么久的是翻页动画那几帧，接着核对
        s.game._resume_check, s.game._resume_base = False, None
    if not getattr(reading, "self_tab", False):
        # 面板开着，选中的却是队友的页签：请用户点自己的页签（每换一次选中的页签提示一次）
        s.game._only_frames = 0
        mine, selected = getattr(reading, "self_index", None), getattr(reading, "selected_tab", None)
        if mine is not None and selected is not None and mine != selected:
            key = (int(selected), int(mine))
            if key != s.game._last_panel_tab:
                s.game._last_panel_tab = key
                return s._event("panel_tab", count=int(mine) + 1)
        return None
    s.game._last_panel_tab = None
    if tab_cleared is not None:
        s._push(tab_cleared)
    if float(getattr(reading, "confidence", 1.0) or 0.0) < PANEL_MIN_CONFIDENCE:
        s.game._only_frames = 0
        return None
    s.game._panel_prompted = True  # 用户已经打开了面板，不用再请他打开
    page = getattr(reading, "page", "only")
    if page in PAGED:
        s.game._paged = True  # 下面离开后的核对按这一页判了新的一局、之后又撤销时，换回来的这一局也要记着
    s.game._only_frames = min(s.game._only_frames + 1, ONLY_FRAMES) if page == "only" else 0
    if page not in PAGES:
        return None  # 认不出是第几页（翻页箭头被挡住等）：不知道对应已有筹码的哪一段，这一帧不改
    if (page == "only" and (len(s.settings.owned) >= PAGE_SLOTS or s.game._paged)
            and s.game._only_frames < ONLY_FRAMES):
        # 记着的已经满一页、或者这一局认出过多于一页的面板，却读成只有一页（两端箭头都不亮）：可能是亮着的箭头被浅色的东西
        # 盖住了（记着的满一页时，之后拿的可能漏记了，面板其实已经翻页）。按只有一页改会去掉一页以外的全部，所以先和看不清
        # 一样不改，连着 ONLY_FRAMES 帧都这样再改（真的只有一页时面板开着每帧都是这样，晚一帧而已）
        return None
    owned: list[str] = []
    raw = list(getattr(reading, "owned", ()) or ())
    for n in raw:
        if n in s.db.chips and n not in owned:
            owned.append(n)
    # 读数里重复的、资料库里没有的格子也算认不出：那一格里真正的筹码说不准，照样可以留一个原来记着的
    unknown = max(0, int(getattr(reading, "unknown_slots", 0) or 0)) + len(raw) - len(owned)
    newgame = None
    if s.game._resume_check:
        # 离开很久后回来的核对：同一局里筹码只增不减，面板上少了之前记下的好几个，是开了新的一局。
        # 能由面板上的同组候选解释的三选一记错不算少了；认不出的格子里每格最多藏着一个之前记下的筹码，扣掉格数再比。
        # 只看只有一页、第一页（最早拿的那些，后面的页离开期间可能多了好几个）；先看到后面的页时照常按页核对，等翻回第一页。
        # 下不了结论（认不出的格子多）时，面板开着的后续几帧接着核对：
        # 和第一次读到面板时记着的比，认出的筹码取这几帧的并集（面板开着时筹码不会变少）；面板关了就到此为止（看不到
        # RESUME_TURN_S 秒以上才算关了，翻页动画那几帧不算；只看到过后面的页时不算，见上）
        if s.game._resume_base is None:
            s.game._resume_base = (list(s.settings.owned), dict(s.game.chip_sources), dict(s.game._pick_offers), {})
        old, sources, offers, seen = s.game._resume_base
        if page in ("only", "first"):
            seen.update(dict.fromkeys(owned))  # 按认出的先后排，结果不随集合顺序变
            missing, forgiven = resume_missing(page, old, list(seen), sources, offers)
            if len(missing) - forgiven - unknown >= RESUME_MISSING_MIN:
                s.game._resume_check, s.game._resume_base = False, None
                shown = "、".join(missing[:3]) + ("等" if len(missing) > 3 else "")
                why = f"离开后打开的「查看筹码」面板上没有之前记下的 {len(missing)} 个筹码（{shown}）"
                if unknown:
                    why += f"，另有 {unknown} 格未能识别"
                newgame = s._auto_new_game("panel", len(missing), why)
                s.game._panel_prompted = True  # 面板正开着，不用再请用户打开
            elif len(missing) - forgiven < RESUME_MISSING_MIN:
                s.game._resume_check, s.game._resume_base = False, None  # 对得上：仍是同一局
    g, settings = s.game, s.settings  # 下面按面板更新这一局（刚按面板判了新的一局时，就是新的这一局）
    if page in PAGED:
        g._paged = True  # 刚按这一页判了新的一局时，新的这一局也认出过多于一页的面板
    old = list(settings.owned)
    span = page_span(page, old, owned, g.chip_sources, g._pick_offers, unknown)
    if span is None:
        return newgame  # 中间页和记着的哪一组都对不上：不改
    new, fixes = page_merge(reading, old, owned, unknown, span, page, g.chip_sources, g._pick_offers)
    first = g.panel_needed
    g.panel_needed = False
    # 同一页的读数没变（面板开着的每一帧）不再处理，刚记下一个筹码、面板上还没换过来的那几帧也就不会把它去掉。翻到另一页时
    # 已有筹码常常不变，但那一页上的筹码要记为面板确认过的：键里带上第几页和这一页认出的筹码
    key = (tuple(new), unknown, page, tuple(owned))
    if key == g._last_panel and not first:
        return None
    g._last_panel = key
    for wrong, right in fixes:
        g._pick_offers[right] = g._pick_offers.pop(wrong)
    # 面板上看到的都算确认过（来源记为面板，同一组候选又出现时也不撤销）
    g.chip_sources = {n: ("panel" if n in owned else g.chip_sources.get(n, "panel")) for n in new}
    g.direct &= set(new)  # 「不经三选一获得」的记号不受面板核对影响，只去掉面板上没有了的
    # 来路：头一次就是在面板上读到的记「查看筹码」面板，一直在的照旧；离开已有筹码的连同面板记号去掉。
    # 面板记号：读到的这一页上有的记上；已经不在已有筹码里的去掉（new 是按这一页更新后的已有筹码，不在里面的就是没有了）。
    # 还在已有筹码里、只是这一页上没有的照旧留着
    g.owned_sources = {n: g.owned_sources.get(n, "panel" if n in owned else None) for n in new}
    g._panel_seen &= set(new)
    g._panel_seen.update(n for n in owned if n in new)
    changed = new != settings.owned
    if changed:
        settings.owned = new
        if new and newgame is None:  # 刚按面板判了新一局时保留「撤销」（万一面板读错了）
            s._recorded()
    if g.missed_offer and any(n in owned for n in g.missed_offer if n):
        g.missed_offer = None  # 漏记的那个已经在面板上了
    if page == "only":
        g.unrecorded = 0  # 面板上的就是全部已有筹码
    else:
        g.unrecorded = max(0, g.unrecorded - max(0, len(new) - len(old)))  # 这一页上多出来的抵掉同样多个待补录
    s.save()
    # problems：面板识别的提示（例如「大铜锣」可能是队友触发后出现的无效图标），悬浮窗照原文显示
    read = s._event("panel_read", owned=list(new), count=unknown, problems=list(getattr(reading, "problems", ()) or ()),
                    fixes=fixes)
    if newgame is None and changed:
        # 按面板改了已有筹码、这一次又没判成新的一局：三选一界面还开着时（包括收起来去点「查看筹码」的）马上按记下的
        # 三选一画面重算，读到面板的这一帧就把推荐改对，不等玩家回到三选一画面。recompute 返回 None（三选一没显示、
        # 候选还没读全等）时什么都不做，之后的画面照常按新的已有筹码算。重算的事件放进待取队列，排在这次返回的 panel_read
        # 后面（识别流程、悬浮窗都按 split_extra 排）。已有筹码没变时推荐不变，不重发（推荐日志里不多出一样的一条，
        # 请选择角色、无法识别的提示也不重来）；那行「还不知道已有筹码」的提醒由悬浮窗收到 panel_read 时收起
        ev = s.select.recompute()
        if ev is not None:
            s._push(ev)
    if newgame is not None:
        s._push(read)  # 先发新一局（悬浮窗清空），再发这一局的面板读数
        return newgame
    return read
