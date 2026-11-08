"""繁体界面：各窗口和杂项（about、chrome、hotkey、dual_check、scheme_share（含作业站的几句）、skin_window、theme、skins、
scheme_editor、scheme_list、settings 的几块、stats_window）接上 lang.localize / lang.t 以后的行为；overlay_parts.reporting
的几句只写进错误记录，不接，核对它们照旧简体。

在子进程里先把界面语言定成繁体再导入：同一个进程里重新导入会换掉类、影响别的测试。
核对三件：
- 显示用的常量成了繁体（窗口标题、说明、档位名、流派名等）；
- 拿来比对、当键的常量还是简体（CLASSES、SCHOOLS、TIER_LABEL、PLAYER_TIER_LABEL、皮肤名）；
- 只写进错误记录、异常、设置文件的文字还是简体（ERROR_TITLE、UNPROTECTED、startup 的标题、version 里的声明）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from advisor import version
from advisor.data import CLASSES, SCHOOLS, TIER_LABEL as DATA_TIER_LABEL
from advisor.ui import lang

PROJECT = Path(__file__).resolve().parents[1]

HANT_SCRIPT = r"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from advisor.ui import lang

lang.CURRENT = lang.HANT

from advisor.ui import about, chrome, dual_check, hotkey, screen, scheme_share, skin_window, startup, theme
from advisor.ui import translation, winapi
from advisor.ui.scheme_editor import notes
from advisor.ui.scheme_list import view
from advisor.ui.settings import dual_prompt
from advisor.ui.skins.classic_white import CLASSIC_WHITE_LABEL
from advisor.version import DISCLAIMER_ITEMS, FREE_NOTICE, PRIVACY_SELECT_SHOTS
import importlib

# 各皮肤文件里登记的那个对象、登记表里的、theme 重新导出的是不是同一个（Skin 按对象本身比）
SKIN_FILES = (("default", "DEFAULT"), ("classic_white", "CLASSIC_WHITE"), ("classic_black", "CLASSIC_BLACK"),
              ("classic_inverse", "CLASSIC_INVERSE"), ("neon_sticker", "NEON_STICKER"),
              ("white_seaview", "WHITE_SEAVIEW"), ("carbon_flow", "CARBON_FLOW"))
skin_same = {}
for module_name, attr in SKIN_FILES:
    skin = getattr(importlib.import_module(f"advisor.ui.skins.{module_name}"), attr)
    skin_same[attr] = theme.get_skin(skin.key) is skin and getattr(theme, attr) is skin

# 自己起的方案名：记的是原文，导入结果那一行显示时转
imported = scheme_share._Imported("x", "芬妮", "银行卡流", shown_hero="芬妮")

# 作业站：取回的方案名、作业站给的说明显示时转；昵称照原文
from advisor import hub_api
from advisor.ui import stats_window
from advisor.ui.overlay_parts import reporting

fetched = hub_api.SchemeShare(12, "银行卡流", "x", None, "https://astral-chip-compass.com/builds/12/")
invalid = hub_api.HubError(hub_api.INVALID, fields={"name": "名称里有不允许的词", "note": "说明太长了"})
forbidden = hub_api.HubError(hub_api.FORBIDDEN, reason="邮箱还没有确认")

print(json.dumps({
    "hub_fetch_done": scheme_share.fetch_done_text(fetched, True),
    "hub_fetch_template": scheme_share.FETCH_DONE,
    "hub_invalid": scheme_share.post_error_text(invalid),
    "hub_invalid_template": scheme_share.POST_INVALID,
    "hub_forbidden": scheme_share.post_error_text(forbidden),
    "hub_forbidden_template": scheme_share.POST_FORBIDDEN,
    "hub_nickname": scheme_share.nickname_text("小龙"),
    "hub_logged_in_template": scheme_share.LOGGED_IN,
    "hub_login_denied": scheme_share.LOGIN_FAILED["denied"],
    "hub_post_done": scheme_share.POST_DONE["draft"],
    "hub_logs": [scheme_share.FETCH_LOG, scheme_share.POST_LOG, scheme_share.LOGOUT_LOG, scheme_share.LOGIN_LOG],
    "stats_title": stats_window.WINDOW_TITLE,
    "stats_pending_off": stats_window.PENDING_OFF,
    "stats_pending_on": stats_window.pending_text((True, 3, "app\\logs\\upload")),
    "stats_follow": stats_window.follow_text(stats_window.FOLLOW_DEFAULT, type("R", (), {"total": 0})()),
    "stats_error_title": stats_window.ERROR_TITLE,
    "report_errors": [reporting.REPORT_CLEAR_ERROR, reporting.REPORT_SAVE_ERROR, reporting.REPORT_SEND_ERROR],
}, ensure_ascii=False))

print(json.dumps({
    "share_imported": imported.line(),
    "share_imported_name": imported.name,
    "share_imported_template": scheme_share.IMPORTED,
    "about_title": about.ABOUT_TITLE,
    "about_no_version": about.NO_VERSION,
    "about_section": about.VERSION_SECTION,
    "about_html": about.about_html([("设置", "软件")]),
    "chrome_back_tip": chrome.BACK_TIP,
    "hotkey_place": hotkey.HOTKEY_PLACE,
    "share_title": scheme_share.TITLE,
    "share_note": scheme_share.SHARE_NOTE,
    "share_format": scheme_share.FORMAT_NAME["short"],
    "notes_title": notes.EDITOR_TITLE,
    "notes_classes": list(notes.CLASSES),
    "notes_schools": list(notes.SCHOOLS),
    "notes_tier_label": dict(notes.TIER_LABEL),
    "notes_player_tier_label": dict(notes.PLAYER_TIER_LABEL),
    "notes_block_title": notes.block_title("core", 6, True),
    "view_tier_label": dict(view.TIER_LABEL),
    "dual_title": dual_prompt.PROMPT_TITLE,
    "dual_level": dual_prompt.LEVEL_NAME["easy"],
    "skin_hint": skin_window.HINT,
    "skin_title": skin_window.WINDOW_TITLE,
    "theme_default_label": theme.DEFAULT_LABEL,
    "theme_classic_white_label": theme.CLASSIC_WHITE_LABEL,
    "skin_classic_white_label": CLASSIC_WHITE_LABEL,
    "skin_current_label": theme.current().label,
    "skin_same": skin_same,
    "skin_current_is_default": theme.current() is theme.DEFAULT,
    "skin_labels": [theme.get_skin(key).label for key in theme.SKIN_ORDER],
    "dual_error_title": dual_check.ERROR_TITLE,
    "screen_unprotected": screen.UNPROTECTED,
    "screen_unprotected_detail": screen.UNPROTECTED_DETAIL,
    "startup_crash_title": startup.CRASH_FULL_TITLE,
    "startup_dropped_title": startup.DROPPED_ENV_TITLE,
    "translation_title": translation.TITLE,
    "dpi_invalid": winapi.DPI_NAMES[-1],
    "game_titles": list(winapi.GAME_TITLES),
    "version_free_notice": FREE_NOTICE,
    "version_disclaimer": list(DISCLAIMER_ITEMS),
    "version_privacy": PRIVACY_SELECT_SHOTS,
}, ensure_ascii=False))
"""


def run_hant() -> dict:
    """子进程打印的两行 JSON 合成一份。"""
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.pop(lang.ENV, None)
    result = subprocess.run([sys.executable, "-B", "-c", HANT_SCRIPT], cwd=PROJECT, env=env,
                            capture_output=True, text=True, encoding="utf-8", timeout=180)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    data = {}
    for line in result.stdout.strip().splitlines()[-2:]:
        data.update(json.loads(line))
    return data


class HantWindowsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = run_hant()

    def test_hub_texts(self):
        """作业站：窗口里的句子是繁体；作业站上取回的方案名、作业站给的说明（各字段的说明、账号不能发的原因）显示时转；
        昵称是网站上的人起的名字，照原文。"""
        data = self.data
        self.assertEqual(data["hub_fetch_template"], self.hant("已从作业站取回「{name}」（{n} 号），请核对识别结果后再导入"))
        self.assertEqual(data["hub_fetch_done"], data["hub_fetch_template"].format(name="銀行卡流", n=12))
        self.assertEqual(data["hub_invalid"],
                         data["hub_invalid_template"].format(detail=self.hant("名称里有不允许的词") + "；"
                                                             + self.hant("说明太长了")))
        self.assertIn("名稱裡有不允許的詞", data["hub_invalid"])
        self.assertEqual(data["hub_forbidden"], data["hub_forbidden_template"].format(reason=self.hant("邮箱还没有确认")))
        self.assertEqual(data["hub_nickname"], data["hub_logged_in_template"].format(name="小龙"))
        self.assertEqual(data["hub_logged_in_template"], self.hant("已登录作业站：{name}"))
        self.assertEqual(data["hub_login_denied"], self.hant("网页上点了「不允许」，没有登录；需要时点「重新登录」。"))
        self.assertEqual(data["hub_post_done"], self.hant("已发到作业站，暂未公开；到网页上点「公开」后才会出现在方案列表里"))
        # 出错时错误记录的标题只写进错误记录，照旧简体
        self.assertEqual(data["hub_logs"], ["按编号导入时取方案出错（拾星助手仍在运行）", "发方案到作业站时出错（拾星助手仍在运行）",
                                            "退出作业站时出错（拾星助手仍在运行）", "登录作业站时出错（拾星助手仍在运行）"])

    def test_stats_texts(self):
        """「我的统计」：窗口标题、正文的句子是繁体；文件夹照原样填；写进错误记录的标题还是简体。上报那一边（reporting）
        的文字只写进错误记录，还是简体。"""
        data = self.data
        self.assertEqual(data["stats_title"], "我的統計")
        self.assertEqual(data["stats_pending_off"], "「幫助拾星助手改善推薦準確度」關著：不存也不發對局報告。")
        self.assertEqual(data["stats_pending_on"], "還沒發出的對局報告：3 份，存在 app\\logs\\upload 資料夾。")
        self.assertEqual(data["stats_follow"], "預設推薦：還沒有記錄")
        self.assertEqual(data["stats_error_title"], "统计推荐日志时出错")
        self.assertEqual(data["report_errors"], ["关掉对局报告的开关时没能删掉还没发出的报告（拾星助手仍在运行）",
                                                 "存对局报告时出错（拾星助手仍在运行）",
                                                 "发送对局报告时出错（拾星助手仍在运行）"])

    def hant(self, text: str) -> str:
        return lang.to_hant(text)

    def test_display_texts_are_hant(self):
        """显示用的窗口标题、说明、档位名转成繁体。"""
        data = self.data
        self.assertEqual(data["about_title"], self.hant("关于拾星助手"))
        self.assertEqual(data["about_no_version"], self.hant("版本信息不可用：程序文件不完整，请把压缩包整个重新解压。"))
        self.assertEqual(data["chrome_back_tip"], self.hant("关闭窗口（也可以按 Esc）"))
        self.assertEqual(data["hotkey_place"], self.hant(hotkey_place_simplified()))
        self.assertEqual(data["share_title"], self.hant("导入/导出筹码方案"))
        self.assertEqual(data["share_note"], self.hant(SHARE_NOTE))
        self.assertEqual(data["share_format"], "短字串")
        self.assertEqual(data["notes_title"], self.hant("编辑筹码方案"))
        self.assertIn("必選", data["notes_block_title"])
        self.assertEqual(data["dual_title"], self.hant("同时识别两个客户端"))
        self.assertEqual(data["dual_level"], "餘裕")
        self.assertEqual(data["skin_hint"], self.hant("点击缩略图即可更换：所有窗口立即换成所选的样子，下次启动沿用"))
        self.assertEqual(data["skin_title"], self.hant("拾星助手皮肤"))
        self.assertIn("設定", data["about_html"])
        self.assertIn("軟體", data["about_html"])

    def test_comparison_constants_stay_simplified(self):
        """拿来和资料库、识别结果比对、当键的常量保持简体。"""
        data = self.data
        self.assertEqual(tuple(data["notes_classes"]), CLASSES)
        self.assertEqual(tuple(data["notes_schools"]), SCHOOLS)
        self.assertEqual(data["notes_tier_label"], DATA_TIER_LABEL)
        self.assertEqual(data["notes_player_tier_label"], DATA_TIER_LABEL)
        self.assertEqual(data["view_tier_label"], DATA_TIER_LABEL)
        self.assertEqual(data["about_section"], "版本")

    def test_skin_labels_stay_simplified(self):
        """皮肤名登记在 skin.label 里、会写进错误记录：常量、当前皮肤的名字都还是简体；显示处才 t()。"""
        data = self.data
        self.assertEqual(data["theme_default_label"], "默认")
        self.assertEqual(data["theme_classic_white_label"], "经典主题-白")
        self.assertEqual(data["skin_classic_white_label"], "经典主题-白")
        self.assertEqual(data["skin_current_label"], "默认")

    def test_skin_objects_not_replaced(self):
        """繁体界面导入以后，各皮肤文件登记的对象、登记表里的、theme 重新导出的和当前皮肤还是同一个（换皮肤、比当前
        皮肤都按对象本身比），登记的名字和简体界面一样。"""
        from advisor.ui import theme

        data = self.data
        self.assertEqual(len(data["skin_same"]), 7)
        for attr, same in data["skin_same"].items():
            self.assertTrue(same, attr)
        self.assertTrue(data["skin_current_is_default"])
        self.assertEqual(data["skin_labels"], [theme.get_skin(key).label for key in theme.SKIN_ORDER])

    def test_scheme_name_converted_when_shown(self):
        """自己起的方案名：记的照旧是原文，导入结果那一行显示时按界面语言转（句子本身导入时已转好）。"""
        data = self.data
        self.assertEqual(data["share_imported_name"], "银行卡流")
        self.assertEqual(data["share_imported"],
                         data["share_imported_template"].format(name="銀行卡流", hero="芬妮"))

    def test_record_texts_stay_simplified(self):
        """只写错误记录、异常、设置文件的文字不转；version.py 的声明也不动，由「关于」窗口显示处 t()。"""
        data = self.data
        self.assertEqual(data["dual_error_title"], "同时识别两个客户端时性能测试出错（拾星助手仍在运行）")
        self.assertTrue(data["screen_unprotected"].startswith("直接截取用的显卡设备没能开启多线程保护"))
        self.assertIn("原因：", data["screen_unprotected_detail"])
        self.assertTrue(data["startup_crash_title"].startswith("崩溃记录太大"))
        self.assertTrue(data["startup_dropped_title"].startswith("启动时去掉了别的软件设的 Qt 环境变量"))
        self.assertTrue(data["translation_title"].startswith("载入 Qt 的中文翻译出错"))
        self.assertEqual(data["dpi_invalid"], "无效")
        self.assertIn("吉星派对", data["game_titles"])
        self.assertEqual(data["version_free_notice"], version.FREE_NOTICE)
        self.assertEqual(data["version_disclaimer"], [list(item) for item in version.DISCLAIMER_ITEMS])
        self.assertEqual(data["version_privacy"], version.PRIVACY_SELECT_SHOTS)


SHARE_NOTE = ("短字符串只是把方案内容编码压缩成一串字符，便于复制，不是保密手段："
              "任何装了拾星助手的人都能导入并看到内容。")


def hotkey_place_simplified() -> str:
    """HOTKEY_PLACE 的简体原文（模块里那段会在繁体界面导入时转掉，测试里写一份）。"""
    return "请点击底栏的「设置」按钮（一行显示时先点击标题栏的「全部展开」按钮），在打开的窗口中更换热键"


if __name__ == "__main__":
    unittest.main()
