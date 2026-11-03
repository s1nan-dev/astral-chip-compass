"""「已有筹码」卡片部件（advisor/ui/overlay_parts/owned_card.py）里补录行的说法（missed_kind）：平时是「未检测到选择」
（missed_pick），只有同一组候选再次出现、撤销了记录时才是 pick_reverted；补录或忽略以后回到 missed_pick。判定新一局时
留的那一份（missed_before，撤销新一局时接回去）开始时也是 missed_pick。补录的结果是操作结果，交给过一会儿自动收起的
提示（flash），忽略只换回等待提示（say）。

不建窗口：卡片构造时只收控件、锁、Session、显示状态和几个回调，这里给假的，只有补录（pick_missed）用到的几样。
"""
import threading
import unittest
from types import SimpleNamespace

from advisor.ui.overlay_parts.dispatch import EV_MISSED_PICK, EV_PICK_REVERTED
from advisor.ui.overlay_parts.owned_card import OwnedCard


class FakeParts:
    """补录用到的几样：锁、Session 的补录、记进推荐日志、问未选择首推的原因、一次性提示和刷新。added 为 Session 记不记进去。"""

    def __init__(self, added: bool):
        self.lock = threading.Lock()
        self.session = SimpleNamespace(resolve_missed=lambda name: added)
        self.supplemented, self.picks, self.said, self.flashed = [], [], [], []
        self.live = SimpleNamespace(on=False)

    def supplement(self, chip, offer_id):
        self.supplemented.append((chip, offer_id))

    def set_pick(self, chip, offer_id, best):
        self.picks.append((chip, offer_id, best))

    def say(self, text, area="global"):
        self.said.append((text, area))

    def flash(self, text, area="global"):
        self.flashed.append((text, area))

    def trace_user(self, action, **data):
        pass

    def waiting_text(self):
        return "等待筹码三选一界面…"

    def refresh(self):
        pass


def owned_card(parts: FakeParts) -> OwnedCard:
    """按卡片构造时收的几样建「已有筹码」卡片；补录不碰控件、资料库和量宽度，那几样给 None。"""
    return OwnedCard(owned_count=None, chip_grid=None, mini_box=None, missed_label=None, missed_buttons=None,
                     db=None, session=parts.session, lock=parts.lock, live=parts.live, say=parts.say, flash=parts.flash,
                     waiting_text=parts.waiting_text, trace_user=parts.trace_user, refresh=parts.refresh,
                     card_width=None, remove_owned=None, set_pick=parts.set_pick, supplement=parts.supplement)


class MissedKindTest(unittest.TestCase):
    def test_initial_state(self):
        card = owned_card(FakeParts(added=False))
        self.assertEqual(card.missed_kind, EV_MISSED_PICK)
        self.assertEqual(card.missed_before, (EV_MISSED_PICK, "", None, None, []))

    def test_pick_missed_resets_to_missed_pick(self):
        """撤销了记录（pick_reverted）以后补录、忽略：说法回到「未检测到选择」，撤销的筹码、首推、候选都清空。"""
        for name, added in (("标靶", True), (None, False)):
            with self.subTest(name=name):
                ov = FakeParts(added)
                card = owned_card(ov)
                card.missed_kind, card.missed_chip, card.missed_best = EV_PICK_REVERTED, "标靶", ("标靶", "标靶", "75%")
                card.missed_offer, card.missed_raw = ["标靶", "书签", None], ["标靶", "书签", "？"]
                card.missed_offer_id = "20261011-120000-1"
                card.pick_missed(name)
                self.assertEqual((card.missed_kind, card.missed_chip, card.missed_best), (EV_MISSED_PICK, "", None))
                self.assertEqual((card.missed_offer, card.missed_raw), ([], []))
                self.assertEqual(ov.supplemented, [("标靶", "20261011-120000-1")] if added else [])

    def test_results_flash(self):
        """补录行的结果（已补录、已在已有筹码中）是操作结果，交给 flash（FLASH_MS 后自动收起）；点「关闭本条
        提示」只换回等待提示（say）。"""
        cases = (
            ("标靶", True, [("已补录：标靶。等待下一次筹码三选一", "owned")], []),
            ("标靶", False, [("「标靶」已在已有筹码中，无需补录", "owned")], []),
            (None, False, [], [("等待筹码三选一界面…", "advice")]),
        )
        for name, added, flashed, said in cases:
            with self.subTest(name=name, added=added):
                parts = FakeParts(added)
                owned_card(parts).pick_missed(name)
                self.assertEqual((parts.flashed, parts.said), (flashed, said))


if __name__ == "__main__":
    unittest.main()
