"""测试共用的假对象：代替真的模块交给悬浮窗等，记下收到的调用。"""
from __future__ import annotations


class FakeLogbook:
    def __init__(self, games=0):
        self.offers, self.picks, self.feedback = [], [], []
        self.games = games
        self.records: list[dict] = []  # records_between 返回其中时刻不早于 start 的（诊断包带的推荐日志）
        self.between: list = []  # records_between 收到的 start
        self.game_overs: list[dict] = []  # record_game_over 收到的内容（对局结束）

    def record_game_over(self, **info):
        self.game_overs.append(info)
        return self.game

    def records_between(self, start, end=None):
        self.between.append(start)
        return [r for r in self.records if str(r.get("time", "")) >= str(start)[:19]]

    def record_offer(self, state, advice, offer_id):
        self.offers.append((offer_id, tuple(e.chip.name for e in advice.evals)))

    def record_pick(self, offer_id, chosen, via=None):
        self.picks.append((offer_id, chosen) if via is None else (offer_id, chosen, via))

    def record_feedback(self, offer_id, reason, note):
        self.feedback.append((offer_id, reason, note))

    def game_count_since_calibration(self):
        return self.games

    PICK_REROLL = "刷新"
    game = "g1"
    games_log: list = []

    def current_game(self):
        return self.game

    def new_game(self):
        self.games_log = [*self.games_log, ("new", self.game)]
        self.game = f"g{len(self.games_log) + 1}"
        return self.game

    def undo_new_game(self, previous):
        self.games_log = [*self.games_log, ("undo", previous)]
        self.game = previous
