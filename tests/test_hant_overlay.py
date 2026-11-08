"""悬浮窗其余部件的繁体界面（overlay_parts 下除卡片以外的模块）。

在子进程里先定语言再导入（模块常量在导入时转好）：界面是繁体时，显示用的常量、拼显示文字的辅助函数出繁体；
写进错误记录、推荐日志、拿来比对或当键的常量照旧简体；交给显示的那一处再转的几段（方案名、皮肤名、热键线程的原句）
照旧是原文，显示时只转一次。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

SCRIPT = r"""
import json
from types import SimpleNamespace
from advisor.ui import lang
lang.init_from_env()
from advisor import paths, unlock, version
from advisor.ui import hotkey, theme
from advisor.ui.overlay_parts import wording, notices, follow, lanes, records, dispatch, status, layout, state
from advisor.ui.overlay_parts import geometry, hotkeys
from advisor.ui.text import coins_bit
from advisor.vision import capture

# 热键线程交回的原句（简体）：显示的那一处（on_hotkey_change_failed）按界面语言转一次
said, noted = [], []
fake = SimpleNamespace(note="", hotkey="Ctrl+F9", say=said.append, feedback=noted.append, refresh=lambda: None)
busy = hotkey.busy_text("Ctrl+F10")
hotkeys.HotkeyController.on_hotkey_change_failed(fake, "Ctrl+F10", busy, active=False)
coins = status.shown_bit(coins_bit(15, 20))
# 一次性提示交过来时已是显示用的写法：再转一次时「說明了」会变成「說明瞭」
shown_once = "試一下就說明了"

print(json.dumps({
    "hant": lang.is_hant(),
    "title_dev": wording.TITLE_DEV,
    "hant_stay": wording.HANT_STAY,
    "err_title": notices.DOWNLOAD_FALLBACK_TITLE,
    "map_auto": wording.MAP_AUTO,
    "no_game": wording.NO_GAME,
    "feedback": list(wording.FEEDBACK_REASONS),
    "try_reason": lang.t(wording.FEEDBACK_REASONS[-1]),
    "try_tip": wording.FEEDBACK_TRY_TIP,
    "dismiss": wording.FEEDBACK_DISMISS,
    "dismiss_tip": wording.FEEDBACK_DISMISS_TIP,
    "egg_title": wording.EGGS_ERROR,
    "lane_tip": follow.LANE_CLOSE_TIP,
    "lane_behind": lanes.SCREEN_BEHIND,
    "error_title": notices.DOWNLOAD_DIR_TITLE,
    "dispatch_reroll": dispatch.REROLL,
    "scheme_name": state.scheme_name(None),
    "waiting": status.waiting_text(manual=False, hotkey=None, hotkey_ok=False, over=False),
    "capture_restored": wording.CAPTURE_RESTORED,
    "gone": list(geometry._window_gone_texts()),
    "not_found_shown": lang.t(capture.NOT_FOUND),
    "program_folder": wording.PROGRAM_FOLDER,
    "short_path": wording.short_path(str(paths.program_folder() / "导出" / "诊断.zip")),
    "about_disclaimer": wording.ABOUT_DISCLAIMER,
    "about_titles": [title for title, _ in version.about_sections()],
    "busy": busy,
    "startup_busy": hotkey.startup_busy_text("Ctrl+F10"),
    "busy_shown": lang.t(busy),
    "said": said,
    "noted": noted,
    "change_failed": hotkeys.HOTKEY_CHANGE_FAILED,
    "unlock_label": theme.get_skin(unlock.WHITE).label,
    "unlock_text": records.Eggs.unlock_text(None, unlock.WHITE),
    "coins_value": coins.value,
    "coins_brief": coins.brief_plain(),
    "coins_tight": coins.brief_plain(tight=True),
    "kept_path": notices.shown_keeping("诊断包已保存到 D:\\说明\\x.zip。", ("D:\\说明\\x.zip", "", "")),
    "summary_tip": status.summary_tip([], message=shown_once, waiting="", refreshing=False, status_tip=""),
    "live_items": [text for item in status.live_line_items([], {}, shown_once) for text, _ in item],
}, ensure_ascii=False))
"""


def run(language: str) -> dict:
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", lang_env(): language}
    proc = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", SCRIPT], cwd=str(PROJECT), env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr)
    return json.loads(proc.stdout)


def lang_env() -> str:
    from advisor.ui.lang import ENV
    return ENV


class HantOverlayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hant = run("zh-Hant")
        cls.hans = run("zh-Hans")

    def test_display_text_is_traditional(self):
        self.assertTrue(self.hant["hant"])
        self.assertEqual(self.hant["title_dev"], "拾星助手 開發版")
        self.assertEqual(self.hant["hant_stay"], "不換")
        self.assertEqual(self.hant["lane_tip"],
                         "隱藏這張懸浮窗（識別繼續進行；在托盤圖示的選單中選擇「顯示」可以再開啟）")
        self.assertEqual(self.hant["lane_behind"],
                         "當前以擷取螢幕的方式識別，這個遊戲視窗不在前景，這次按熱鍵沒有識別。")
        self.assertEqual(self.hant["no_game"], "未找到遊戲視窗，請先啟動遊戲。")
        self.assertEqual(self.hant["waiting"], "等待籌碼三選一介面…")
        self.assertEqual(self.hant["capture_restored"],
                         "已恢復直接擷取遊戲視窗：遊戲視窗不在前景時識別照常進行。")

    def test_written_and_compared_text_stays_simplified(self):
        for value in (self.hant["err_title"], self.hant["error_title"]):
            self.assertIn("仍在运行", value)
        self.assertEqual(self.hant["map_auto"], "地图：自动识别")
        self.assertEqual(self.hant["feedback"], ["照顾队友", "地图因素", "推荐有误", "想试别的玩法"])
        self.assertEqual(self.hant["dispatch_reroll"], "刷新")
        self.assertEqual(self.hant["egg_title"], "记录对局进度出错（拾星助手仍在运行）")

    def test_feedback_try_and_dismiss_are_traditional(self):
        """第四个原因按显示处转成繁体（按钮上写「想試別的玩法」），它的悬停说明和「×」的悬停说明也是繁体。"""
        self.assertEqual(self.hant["try_reason"], "想試別的玩法")
        self.assertIn("想試別的玩法", self.hant["try_tip"])
        self.assertNotIn("想试别的玩法", self.hant["try_tip"])
        self.assertEqual(self.hant["dismiss"], "×")
        self.assertEqual(self.hant["dismiss_tip"], "不選原因，收起這一問（這一次不記原因）")
        self.assertEqual(self.hans["try_reason"], "想试别的玩法")

    def test_simplified_ui_is_unchanged(self):
        self.assertFalse(self.hans["hant"])
        self.assertEqual(self.hans["title_dev"], "拾星助手 开发版")
        self.assertEqual(self.hans["hant_stay"], "不换")
        self.assertEqual(self.hans["scheme_name"], "默认推荐")
        self.assertEqual(self.hans["said"], [self.hans["change_failed"].format(reason=self.hans["busy"], key="Ctrl+F9")])
        self.assertEqual(self.hans["coins_value"], "15 星币（升星还差 5）")
        self.assertEqual(self.hans["program_folder"], "程序文件夹")

    def test_raw_parts_wait_for_display(self):
        """交给提示行（NoticeView 显示时整句转一次）、拼进一次性提示（说的那一处整句转一次）的几段照旧是原文：方案名、
        皮肤名、热键线程的原句。在这里先转了，显示时就转了两次（再转会改字，例如「說明了」变成「說明瞭」）。"""
        hant, hans = self.hant, self.hans
        self.assertEqual(hant["scheme_name"], "默认推荐")
        self.assertEqual(hant["busy"], "热键 Ctrl+F10 已被其他程序占用，请更换")
        self.assertEqual(hant["startup_busy"], hans["startup_busy"])
        self.assertEqual(hant["unlock_label"], hans["unlock_label"])
        self.assertIn(f"「{hant['unlock_label']}」", hant["unlock_text"])

    def test_converted_once_where_shown(self):
        """显示的那一处转一次：热键线程的原句在悬浮窗上、设置窗口的说明处是繁体，整句只转一次；星币数那一项（text.py
        不转）进状态胶囊以前转好；一次性提示交过来时已是显示用的写法，摘要和悬停说明照原样写；提示行悬停说明里的完整路径
        不转；诊断包位置的写法只有开头的「程序文件夹」跟着界面语言。"""
        hant = self.hant
        self.assertNotEqual(hant["busy_shown"], hant["busy"])
        self.assertEqual(hant["noted"], [hant["busy_shown"]])
        self.assertEqual(hant["said"], [hant["change_failed"].format(reason=hant["busy_shown"], key="Ctrl+F9")])
        self.assertNotIn(hant["busy"], hant["said"][0])
        self.assertEqual(hant["coins_value"], "15 星幣（升星還差 5）")
        self.assertEqual(hant["coins_brief"], "15 星幣（升星差 5）")
        self.assertEqual(hant["coins_tight"], "15 星幣（差 5）")
        self.assertIn("試一下就說明了", hant["summary_tip"])
        self.assertNotIn("說明瞭", hant["summary_tip"])
        self.assertEqual(hant["live_items"], ["試一下就說明了"])
        self.assertTrue(hant["kept_path"].startswith("診斷包"), hant["kept_path"])
        self.assertIn("D:\\说明\\x.zip", hant["kept_path"])
        self.assertEqual(hant["program_folder"], "程式資料夾")
        self.assertEqual(hant["short_path"].replace("\u200b", ""), "程式資料夾\\导出\\诊断.zip")

    def test_window_gone_texts_are_display_forms(self):
        """后台识别取不到画面的几句（跟随游戏窗口的部件按它认出、收起）：一次性提示存的是显示用的写法，列的也只有显示用的
        那一种，各不相同。"""
        gone = self.hant["gone"]
        self.assertEqual(len(gone), 6)
        self.assertEqual(len(set(gone)), 6)
        self.assertEqual(gone[0], self.hant["not_found_shown"])
        self.assertEqual(gone[0], "未找到遊戲視窗，請先啟動遊戲。")
        self.assertEqual(len(self.hans["gone"]), 6)

    def test_about_disclaimer_matches_section_title(self):
        """「关于」里免责声明那一节的名字拿来和 version.about_sections 的标题比（那一份不转）：繁体界面下照旧简体，找得到。"""
        self.assertEqual(self.hant["about_disclaimer"], self.hans["about_disclaimer"])
        self.assertIn(self.hant["about_disclaimer"], self.hant["about_titles"])


class LocalizeCallTest(unittest.TestCase):
    def test_modules_call_localize(self):
        from tools import hant_table

        found = set(hant_table.localized_modules())
        for name in ("wording", "notices", "follow", "lanes", "records", "dispatch"):
            rel = f"advisor/ui/overlay_parts/{name}.py"
            self.assertIn(rel, found, f"{rel} 要在末尾调 lang.localize(globals())")


if __name__ == "__main__":
    unittest.main()
