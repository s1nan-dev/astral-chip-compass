"""「导入/导出筹码方案」窗口（advisor/ui/scheme_share.py）：勾选、全选、按角色筛选；两种格式的预览和剪贴板内容一致；
导入的识别结果和问题提示；导入只新建、不覆盖；导出 → 粘贴 → 导入后内容相同；转义；一次粘贴 50 套、2 万字以上；只读。

用 Qt 的 offscreen 平台，不会弹出窗口；剪贴板是 offscreen 平台在本进程里的剪贴板，不碰系统的剪贴板（不是 offscreen
平台时整个文件跳过）。测试用的方案文件都放在程序目录的 .tmp 下的临时文件夹里。
"""
import ast
import json
import random
import re
import shutil
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QEvent, QEventLoop, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QTextDocument  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QLabel, QScrollArea  # noqa: E402
from shiboken6 import isValid  # noqa: E402

from advisor import hub, hub_api, paths, wincred, winhttp  # noqa: E402
from advisor import schemes as S  # noqa: E402
from advisor.data import heroes_in_group_order, load_db  # noqa: E402
from advisor.ui import lang  # noqa: E402
from advisor.ui import scheme_share as W  # noqa: E402
from tests.support import qtenv  # noqa: E402
from tests.support.widgets import paint_messages, qt_messages, settle  # noqa: E402

APP = qtenv.app()
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "advisor" / "ui" / "scheme_share.py"


def setUpModule():
    if QGuiApplication.platformName() != "offscreen":
        raise unittest.SkipTest("只在 Qt 的 offscreen 平台上跑（不弹窗口、不碰系统的剪贴板）")


# ------------------------------------------------------------ 界面文字对照表（逐字）

TABLE = {
    "TITLE": "导入/导出筹码方案",
    "EXPORT_HEAD": "导出：勾选方案，选择格式后点「复制到剪贴板」。",
    "HERO_LABEL": "角色",
    "ALL_HEROES": "全部角色",
    "SELECT_ALL": "全选",
    "FORMAT_LABEL": "格式",
    "COPY": "复制到剪贴板",
    "IMPORT_HEAD": "导入：把方案文字或短字符串粘贴到下面（可以一次粘贴多套，每套一行）",
    "CLEAR": "清空",
    "FIND_ON_HUB": "去作业站找方案",
    "FIND_ON_HUB_TIP": ("在浏览器里打开作业站上其他玩家分享的当前角色的筹码方案（资料库没收录这个角色时看全部角色的；"
                        "对局中认出了地图或手动选了地图时，只看这张地图能用的）：看中的复制下来，粘贴到上面导入；"
                        "也可以记下方案的编号，在上面按编号导入"),
    "POST_TO_HUB": "发到作业站",
    "POST_TO_HUB_TIP": ("把勾选的一套方案直接发到作业站：只发这一套的角色、名称、四档筹码、短说明和适用地图，加上程序版本和"
                        "资料日期\n发上去以后先不公开，到作业站网页上点「公开」后才会出现在方案列表里\n"
                        "第一次发要先登录作业站：在浏览器里核对短码、点「允许」，拾星助手不经手密码"),
    "IMPORT_NOTE": "导入后新建为我的方案，不覆盖已有的；同名时名称后加「（2）」",
    "SHARE_NOTE": "短字符串只是把方案内容编码压缩成一串字符，便于复制，不是保密手段：任何装了拾星助手的人都能导入并看到内容。",
    "CLOSE": "关闭",
}
# 短字符串带着各档的顺位（19 套默认推荐副本 26～66 个字符），说明写「约 20～70 个字符」
TABLE_FORMATS = {"short": "短字符串（约 20～70 个字符）", "text": "可读文字（列出全部筹码，较长）"}
TABLE_COPIED = "已复制 1 套方案的短字符串"
TABLE_FOUND = "识别到 2 套："
TABLE_OK = "✓ 凛 · 标记充能　目标流派 标记、充能；必选 2、推荐 4、不选 3"
TABLE_IMPORT = "导入 N 套为我的方案"
TABLE_IMPORTED = "已导入筹码方案「X」（凛）"
# 导入时的问题（每条前面写「第 N 套（名称）：」）；N、X 是占位
TABLE_PROBLEMS = {
    "字符串不完整或被改动（校验不符），无法导入；请粘贴完整的一串",
    "方案文字格式不正确，无法导入；请粘贴完整的一段",
    "这一套来自更新版本的拾星助手，当前版本无法导入",
    "N 个筹码不在当前版本的资料库中（可能来自更新版本的拾星助手），已忽略",
    "已忽略已移除的筹码：X",
    "未能识别的筹码名：X（已忽略）",
    "N 张地图不在当前版本的资料库中（可能来自更新版本的拾星助手），已忽略",
    "未能识别的地图：X（已忽略）",
    "角色编号 N 资料库未收录，已跳过",
    "角色「X」资料库未收录，已跳过",
    "名称超过 12 个字，已截断",
    "说明超过 60 个字，已截断",
}
# 方案文件来自更新版本时的句子（悬浮窗的方案文件提示里的那一句；只读时导入按钮下面也用它）
TABLE_READ_ONLY = "筹码方案文件来自更新版本的拾星助手，当前只能查看，不能修改"
# 对照表以外的问题句子：筹码编号表读不出来时，标了档位的短字符串不导入
NO_CODES = "筹码编号表读取失败，无法导入短字符串；请改为粘贴方案文字"
# 按编号导入（作业站）：按钮、输入框的占位文字、两者共用的悬停说明（两段），提示行的各句（编号填 1234，X 是作业站上的
# 名称）
TABLE_FETCH = {
    "FETCH": "按编号导入",
    "FETCH_PLACEHOLDER": "作业站方案编号或网址",
    "FETCH_TIP": ("输入作业站上方案的编号或方案页面的网址，点「按编号导入」（或按回车）从作业站取回这一套方案，放进下面的"
                  "粘贴框；不自动导入，核对识别结果后再导入\n只发送方案编号（用链接分享的方案另带网址里的随机码）和程序版本；"
                  "不点就不连作业站"),
    "FETCH_EMPTY": "请先输入作业站上方案的编号（例如 123），或者方案页面的网址。",
    "FETCH_BAD_REF": "无法识别这个编号或网址：请输入作业站上方案的编号（例如 123），或者方案页面的网址。",
}
TABLE_FETCH_BUSY = "正在从作业站取 1234 号方案……"
TABLE_FETCH_DONE = "已从作业站取回「X」（1234 号），请核对识别结果后再导入"
TABLE_FETCH_DONE_UNNAMED = "已从作业站取回 1234 号方案，请核对识别结果后再导入"
TABLE_FETCH_SAME = "已从作业站取回「X」（1234 号），粘贴框里已有这一套，未重复放入"
TABLE_FETCH_SAME_UNNAMED = "已从作业站取回 1234 号方案，粘贴框里已有这一套，未重复放入"
# 出错时提示行的句子，键是 hub_api.HubError 的种类；取得太频繁按要等几秒写（键是 retry_after），用户停下的不写，别的种类、
# 别的异常写 TABLE_FETCH_ERROR
TABLE_FETCH_FAILED = {
    "network": "无法连接作业站，请检查网络后再试。",
    "not_found": "作业站上没有这一套：编号不对，或已删除、未公开。用链接分享的方案请输入带随机码的完整网址。",
    "server": "作业站暂时出错，请稍后再试。",
    "bad_response": "作业站返回的内容无法识别，请稍后再试；多次出现时请更新拾星助手。",
}
TABLE_FETCH_RATE = {
    30: "取方案过于频繁，请 30 秒后再试。",
    119: "取方案过于频繁，请 119 秒后再试。",
    120: "取方案过于频繁，请 2 分钟后再试。",
    121: "取方案过于频繁，请 3 分钟后再试。",
    0: "取方案过于频繁，请稍后再试。",
    None: "取方案过于频繁，请稍后再试。",
}
TABLE_FETCH_ERROR = "取方案时出错，请稍后再试。"
# 发到作业站、登录、退出的各句：占位 N 是作业站上的编号，X 是作业站给的说明，U 是网址
TABLE_POST = {
    "POST_PICK_ONE": "请只勾选一套方案，再点「发到作业站」。",
    "POST_NO_SHORT": ("这一套无法生成短字符串（筹码编号表读取失败，或其中的角色、筹码、地图没有编号），发不了；"
                      "请更新拾星助手后再试。"),
    "POST_BUSY": "正在把「X」发到作业站……",
    "POST_BUSY_UNNAMED": "正在把这一套发到作业站……",
    "UPDATE_BUSY": "正在更新作业站上的 N 号方案……",
    "POST_DONE": {
        "draft": "已发到作业站，暂未公开；到网页上点「公开」后才会出现在方案列表里",
        "public": "已发到作业站，已公开：方案列表里就能看到",
        "unlisted": "已发到作业站：这一套不出现在方案列表里，只有拿到链接的人能看到",
    },
    "UPDATE_DONE": {
        "draft": "已更新作业站上的 N 号方案，暂未公开；到网页上点「公开」后才会出现在方案列表里",
        "public": "已更新作业站上的 N 号方案：方案列表里显示的就是更新后的内容",
        "unlisted": "已更新作业站上的 N 号方案：这一套不出现在方案列表里，只有拿到链接的人能看到",
    },
    "VIEW_ON_HUB": "在作业站上看",
    "POST_DUPLICATE": "作业站上已有一套角色和四档筹码都一样的方案（N 号）：是否用勾选的这一套更新它？",
    "DUP_UPDATE": "更新那一套",
    "DUP_SKIP": "不更新",
    "POST_INVALID": "作业站没有收下这一套：X",
    "POST_INVALID_BARE": "作业站没有收下这一套：内容不符合要求，请检查名称和说明后再试。",
    "POST_FORBIDDEN": "这个作业站账号现在不能发方案：X",
    "POST_FORBIDDEN_BARE": "这个作业站账号现在不能发方案，原因请到作业站网页上查看。",
    "POST_UNAUTHORIZED": "作业站的登录已失效，请重新登录：再点一次「发到作业站」。",
    "POST_FAILED": {
        "network": "无法连接作业站，请检查网络后再试。",
        "not_found": "作业站上已没有那一套，或那一套不是这个账号发的。",
        "server": "作业站暂时出错，请稍后再试。",
        "bad_response": "作业站返回的内容无法识别，请稍后再试；多次出现时请更新拾星助手。",
    },
    "POST_UNAVAILABLE": "作业站上暂时发不了方案，请稍后再试；多次出现时请更新拾星助手。",
    "POST_RATE_LIMITED": "发方案过于频繁，请 X后再试。",
    "POST_RATE_LATER": "发方案过于频繁，请稍后再试。",
    "POST_ERROR": "发方案时出错，请稍后再试。",
    "LOGIN_NOT_SAVED": "登录信息没能存在这台电脑上，下次发方案还要重新登录。",
    "LOGGED_IN": "已登录作业站：X",
    "LOGGED_IN_UNNAMED": "已登录作业站",
    "LOGOUT": "退出作业站",
    "LOGOUT_BUSY": "正在退出作业站……",
    "LOGOUT_DONE": "已退出作业站",
    "LOGOUT_FAILED": "没能退出作业站，请稍后再试。",
    "LOGIN_TITLE": "登录作业站",
    "LOGIN_ASKING": "正在向作业站要登录短码……",
    "LOGIN_STEPS": "已在浏览器里打开作业站的授权页面：请核对页面上的短码和下面的一样，再点「允许」。",
    "LOGIN_NO_BROWSER": "浏览器没有自动打开：请在浏览器里打开 U ，输入下面的短码后点「允许」。",
    "LOGIN_WAITING": "正在等作业站上的授权……",
    "LOGIN_PRIVACY": ("拾星助手不经手密码：授权以后，这台电脑只存一份登录信息（交给 Windows 加密保管），"
                      "点「退出作业站」时删掉。"),
    "LOGIN_REOPEN": "再打开一次网页",
    "LOGIN_CANCEL": "取消",
    "LOGIN_RETRY": "重新登录",
    "LOGIN_FAILED": {
        "denied": "网页上点了「不允许」，没有登录；需要时点「重新登录」。",
        "expired": "短码已过期，没有登录；点「重新登录」换一个新的短码。",
        "network": "无法连接作业站，请检查网络后点「重新登录」。",
        "server": "作业站暂时出错，请稍后点「重新登录」再试。",
        "bad_response": "作业站返回的内容无法识别，请稍后再试；多次出现时请更新拾星助手。",
    },
    "LOGIN_RATE_LIMITED": "登录过于频繁，请 X后再试。",
    "LOGIN_RATE_LATER": "登录过于频繁，请稍后再试。",
    "LOGIN_ERROR": "登录时出错，请稍后再试。",
}
# 占位换回常量里的写法（{n}、{name} 这类）
TABLE_POST_SLOTS = {"POST_BUSY": ("X", "{name}"), "UPDATE_BUSY": ("N", "{n}"), "UPDATE_DONE": ("N", "{n}"),
                    "POST_DUPLICATE": ("N", "{n}"), "POST_INVALID": ("X", "{detail}"),
                    "POST_FORBIDDEN": ("X", "{reason}"), "POST_RATE_LIMITED": ("X", "{wait}"),
                    "LOGGED_IN": ("X", "{name}"), "LOGIN_NO_BROWSER": ("U", "{url}"),
                    "LOGIN_RATE_LIMITED": ("X", "{wait}")}


def table_problem(start: str, **values) -> str:
    """对照表里以 start 开头的问题句子，占位 N、X 换成实际的值。以 start 开头的要正好一句：对照表是集合，有两句时
    取到哪句随每次运行的哈希而变。"""
    found = [p for p in TABLE_PROBLEMS if p.startswith(start)]
    if len(found) != 1:
        raise AssertionError(f"对照表里以「{start}」开头的句子应正好一句：{found}")
    sentence = found[0]
    for key, value in values.items():
        sentence = sentence.replace(key, value)
    return sentence


def shown(text: str) -> str:
    """按钮、复选框上实际显示的文字：&& 显示为 &，单个 & 是快捷键的标记、不显示。"""
    return re.sub(r"&(&?)", lambda m: m.group(1), text)


def rendered(label) -> list[str]:
    """富文本标签实际显示出来的各行文字。"""
    doc = QTextDocument()
    doc.setHtml(label.text())
    text = doc.toPlainText()
    return text.split("\n") if text else []


def done_rows(win) -> list[tuple[list[str], object, object]]:
    """导入以后识别结果区里的各行：（左边显示出来的字, 「使用这套方案」按钮, 换成的结果那一句）。"""
    out = []
    for i in range(win.done_rows.count()):
        line = win.done_rows.itemAt(i).widget().layout()
        out.append((rendered(line.itemAt(0).widget()), line.itemAt(1).widget(), line.itemAt(2).widget()))
    return out


def content(s: S.Scheme, full: bool = True) -> tuple:
    """分享格式带得过去的内容：角色、名称、每个筹码的档位和顺位（按方案的先后），可读文字另有说明和作者。"""
    base = (s.hero, s.name, tuple(s.tiers.items()), tuple(s.tiers.rank(c) for c in s.tiers))
    return base + (s.note, s.author) if full else base


def hero_order(db) -> list[str]:
    """悬浮窗角色下拉框里角色的顺序：按六类分组，组内按资料库的顺序。"""
    return [h.name for h in heroes_in_group_order(db.heroes.values())]


def guard_hub(test) -> None:
    """打开 hub.ENABLED 测作业站：不联网、不碰真的凭据管理器、不开浏览器。本机的登录状态（hub_api.login_nickname）换成假的，
    返回 test.nickname（None 为没登录）；winhttp.Session、wincred 的读、写、删和导入/导出窗口默认的打开浏览器（这之后建的
    窗口用它）换成一调就报错的，每条测试最后核对都没调过。"""
    test.nickname = None
    for target, name, value in ((hub, "ENABLED", True), (hub_api, "login_nickname", lambda: test.nickname)):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        test.addCleanup(patcher.stop)
    fakes = []
    for target, name in ((winhttp, "Session"), (wincred, "load"), (wincred, "load_user"), (wincred, "save"),
                         (wincred, "delete"), (W, "_open_url")):
        patcher = mock.patch.object(target, name, side_effect=AssertionError("测试里不该联网、不该碰凭据管理器、不该开浏览器"))
        fakes.append(patcher.start())
        test.addCleanup(patcher.stop)
    test.session = fakes[0]
    test.addCleanup(lambda: [fake.assert_not_called() for fake in fakes])
    hub_open_sizes(test)


# 作业站的入口打开时导入/导出窗口打开的大小（皮肤里写的是入口关着时的大小）：高度连「已登录作业站」那一行、发到作业站以后
# 两行以内的提示行和小按钮算在内，露出来时窗口不跟着变高、按钮照样看得见。没列的皮肤开着、关着一样大
HUB_OPEN_SIZES = {"default": (600, 680), "neon_sticker": (600, 720), "carbon_flow": (900, 660),
                  "classic_white": (860, 680), "classic_black": (920, 690), "classic_inverse": (620, 880)}


class _HubOpenLook:
    """导入/导出窗口取皮肤数值的替身（换掉 scheme_share.L）：SIZE 照 HUB_OPEN_SIZES，其余照当前皮肤。"""

    def __getattr__(self, name: str):
        from advisor.ui import theme

        if name == "SIZE":
            return HUB_OPEN_SIZES.get(theme.current_key(), theme.SHARE.SIZE)
        return getattr(theme.SHARE, name)


def hub_open_sizes(test) -> None:
    """测作业站入口打开时的排法：这之后建的导入/导出窗口按入口打开时的大小（HUB_OPEN_SIZES）开，测试结束换回。"""
    patcher = mock.patch.object(W, "L", _HubOpenLook())
    patcher.start()
    test.addCleanup(patcher.stop)


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.cat = S.get_catalog(cls.db)
        cls.order = hero_order(cls.db)

    def setUp(self):
        saved = W.SchemeShareWindow.last_format
        self.addCleanup(setattr, W.SchemeShareWindow, "last_format", saved)

    def tmp(self) -> Path:
        paths.temp_dir().mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=paths.temp_dir()))
        self.addCleanup(shutil.rmtree, tmp, True)
        return tmp

    def store(self, catalog=None) -> S.SchemeStore:
        return S.SchemeStore(self.tmp() / "schemes.json", catalog or self.cat)

    def window(self, store, hero=None) -> W.SchemeShareWindow:
        win = W.SchemeShareWindow(self.db, store, hero)
        self.addCleanup(win.deleteLater)
        self.addCleanup(win.close)
        return win

    def sorted_heroes(self, names) -> list[str]:
        return sorted(set(names), key=self.order.index)

    @staticmethod
    def scheme(hero="芬妮", name="治愈坦克", **kw) -> S.Scheme:
        """一套方案（目标流派由标记推出来：缓冲盾牌、医疗箱-完备治疗、大碗炖肉都是治愈）。"""
        kw.setdefault("tiers", {"缓冲盾牌": "core", "医疗箱-完备治疗": "core", "大碗炖肉": "first"})
        return S.Scheme(hero=hero, name=name, **kw)

    @staticmethod
    def rows(win) -> list[tuple[str, bool]]:
        return [(shown(box.text()), box.isChecked()) for box in win.list_boxes()]

    @staticmethod
    def heroes(win) -> list[str]:
        """角色下拉框里能选的各项（「全部角色」和各个角色；不含不能选的组名）。"""
        model = win.hero_box.model()
        return [win.hero_box.itemText(i) for i in range(win.hero_box.count()) if model.item(i).isEnabled()]

    @staticmethod
    def headers(win) -> list[str]:
        """角色下拉框里不能选的组名（「── 法术C ──」）。"""
        model = win.hero_box.model()
        return [win.hero_box.itemText(i) for i in range(win.hero_box.count()) if not model.item(i).isEnabled()]


# ------------------------------------------------------------ 导出


class ExportTest(_Base):
    def setUp(self):
        super().setUp()
        self.st = self.store()
        self.a = self.st.add(self.scheme(name="治愈坦克"))
        self.b = self.st.add(self.scheme(hero="凛", name="标记充能", tiers={"魔法箭袋": "core", "标记喷罐": "first"}))
        self.c = self.st.add(self.scheme(name="星光速攻", tiers={"金色飞星": "core"}))
        # 方案列表按角色排（悬浮窗角色下拉框的顺序），同一角色里按方案文件的先后
        self.listed = sorted((self.a, self.b, self.c), key=lambda s: self.order.index(s.hero))
        self.assertEqual(self.sorted_heroes(["芬妮", "凛"]), ["凛", "芬妮"])  # 前提：凛排在芬妮前面

    def test_layout(self):
        """约 600 × 640；不分页签，导出在上、导入在下；窗口标题。角色下拉框的下拉列表每一行和悬浮窗同一种画法
        （widgets.ComboRowDelegate：鼠标所在的行亮灰圆角框，当前的值左边一根短条）。"""
        from advisor.ui.widgets import ComboRowDelegate

        win = self.window(self.st)
        self.assertEqual(W.SIZE, (600, 640))
        self.assertEqual((win.width(), win.height()), W.SIZE)
        self.assertEqual(win.windowTitle(), TABLE["TITLE"])
        self.assertIsInstance(win.hero_box.itemDelegate(), ComboRowDelegate)
        win.show()  # offscreen 平台：不会出现在桌面上
        APP.processEvents()
        self.assertLess(win.export_box.geometry().bottom(), win.import_box.geometry().top())

    def test_empty_guide(self):
        """一套我的方案都没有时，导出区的列表里写下一步怎么做；窗口大小不变。导入一套以后收起，列出那一套；
        只是按角色筛选到没有方案的角色时不写（别的角色有方案）。"""
        win = self.window(self.store())
        win.show()  # offscreen 平台：不会出现在桌面上
        APP.processEvents()
        self.assertTrue(win.export_empty.isVisible())
        self.assertEqual(win.export_empty.text(), W.EXPORT_EMPTY)
        self.assertEqual(W.EXPORT_EMPTY, "还没有可以导出的方案。导出的是自己的方案：点击悬浮窗「筹码方案」卡片的「编辑方案」按钮，"
                                         "新建或复制一套我的方案以后，这里就会列出来；导入的方案也会列在这里。")
        self.assertEqual((win.width(), win.height()), W.SIZE)
        self.assertEqual(self.rows(win), [])
        win.paste.setPlainText(S.to_short(self.a, self.cat))
        win.recognize()
        self.assertEqual(len(win.import_schemes()), 1)
        APP.processEvents()
        self.assertFalse(win.export_empty.isVisible())
        self.assertEqual(self.rows(win), [("芬妮 · 治愈坦克", False)])
        # 别的角色有方案、筛选的角色没有：列表空着，不写这一段
        win = self.window(self.st, "小町")
        win.show()
        APP.processEvents()
        self.assertEqual(self.rows(win), [])
        self.assertFalse(win.export_empty.isVisible())

    def test_empty_guide_all_skins(self):
        """七套皮肤（七种排法）：说明整段看得到（不被列表框、窗口裁掉），窗口不因它变大（和有一套方案时一样大）。"""
        from PySide6.QtCore import QPoint, QRect

        from advisor.ui import theme
        self.addCleanup(theme.set_skin, theme.DEFAULT_KEY)
        for skin in theme.skins():
            theme.set_skin(skin.key)
            with self.subTest(skin=skin.key, layout=W.SchemeShareWindow.layout_name()):
                one = self.store()
                one.add(self.scheme())
                ref = self.window(one)
                ref.show()
                win = self.window(self.store())
                win.show()
                for _ in range(4):
                    APP.processEvents()
                guide = win.export_empty
                self.assertTrue(guide.isVisible())
                self.assertFalse(ref.export_empty.isVisible())
                self.assertEqual(win.size(), ref.size())
                self.assertGreaterEqual(guide.height(), guide.heightForWidth(guide.width()))
                self.assertEqual(guide.visibleRegion().boundingRect(), guide.rect())
                self.assertTrue(win.rect().contains(QRect(guide.mapTo(win, QPoint(0, 0)), guide.size())))

    def test_filter_check_and_select_all(self):
        """按角色筛选（只列有方案的角色）；全选只管列出来的；换筛选时记住勾选，导出的只是列出来的。"""
        win = self.window(self.st)
        self.assertIsNone(win.hero_filter())
        self.assertEqual(self.heroes(win), ["全部角色", "凛", "芬妮"])
        # 列出来的角色按六类排，每一类前面一行不能选的组名（只有列出来的角色所在的几类）
        self.assertEqual([win.hero_box.itemText(i) for i in range(win.hero_box.count())],
                         ["全部角色", "── 法术C ──", "凛", "── 钱辅 ──", "芬妮"])
        self.assertEqual(self.headers(win), ["── 法术C ──", "── 钱辅 ──"])
        self.assertEqual(self.rows(win), [("凛 · 标记充能", False), ("芬妮 · 治愈坦克", False), ("芬妮 · 星光速攻", False)])
        self.assertFalse(win.copy_btn.isEnabled())
        self.assertEqual(win.export_text(), "")
        win.hero_box.setCurrentIndex(win.hero_box.findText("芬妮"))
        self.assertEqual(win.hero_filter(), "芬妮")
        self.assertEqual([r[0] for r in self.rows(win)], ["芬妮 · 治愈坦克", "芬妮 · 星光速攻"])
        win.select_all.click()
        self.assertEqual(self.rows(win), [("芬妮 · 治愈坦克", True), ("芬妮 · 星光速攻", True)])
        self.assertTrue(win.select_all.isChecked())
        self.assertEqual([s.id for s in win.selected_schemes()], [self.a.id, self.c.id])
        self.assertEqual(len(win.export_text().splitlines()), 2)
        self.assertTrue(win.copy_btn.isEnabled())
        win.hero_box.setCurrentIndex(win.hero_box.findText("凛"))  # 芬妮的勾选不导出
        self.assertEqual(self.rows(win), [("凛 · 标记充能", False)])
        self.assertFalse(win.select_all.isChecked())
        self.assertEqual((win.selected_schemes(), win.export_text()), ([], ""))
        self.assertFalse(win.copy_btn.isEnabled())
        win.list_boxes()[0].click()
        self.assertTrue(win.select_all.isChecked())
        win.hero_box.setCurrentIndex(0)  # 全部角色：勾选都还在
        self.assertEqual(win.hero_box.currentText(), "全部角色")
        self.assertEqual([r[1] for r in self.rows(win)], [True, True, True])
        self.assertTrue(win.select_all.isChecked())
        win.list_boxes()[1].click()  # 取消一套：全选跟着取消
        self.assertFalse(win.select_all.isChecked())
        win.select_all.click()  # 没全勾上时点全选：全部勾上
        self.assertEqual([r[1] for r in self.rows(win)], [True, True, True])
        win.select_all.click()  # 全勾上时再点：全部取消
        self.assertEqual([r[1] for r in self.rows(win)], [False, False, False])
        self.assertEqual(win.export_text(), "")

    def test_default_hero(self):
        """默认的角色筛选是悬浮窗当前的角色，并勾选它正在用的方案；没有方案的角色也照样筛选；None 和资料库里没有的
        角色为全部角色；复用窗口时可以换角色。"""
        self.st.set_current("芬妮", self.c.id)
        win = self.window(self.st, "芬妮")
        self.assertEqual((win.hero_filter(), win.hero_box.currentText()), ("芬妮", "芬妮"))
        self.assertEqual(self.rows(win), [("芬妮 · 治愈坦克", False), ("芬妮 · 星光速攻", True)])
        self.assertEqual(win.export_text(), S.to_short(self.c, self.cat))
        win = self.window(self.st, "小町")
        self.assertEqual((win.hero_filter(), self.rows(win)), ("小町", []))
        self.assertEqual(self.heroes(win), ["全部角色", *self.sorted_heroes(["芬妮", "凛", "小町"])])
        self.assertFalse(win.select_all.isEnabled())
        self.assertFalse(win.copy_btn.isEnabled())
        for hero in (None, "不存在的角色"):
            win = self.window(self.st, hero)
            self.assertEqual((win.hero_filter(), win.hero_box.currentText()), (None, "全部角色"))
            self.assertEqual([r[1] for r in self.rows(win)], [False, False, False])  # 没有默认的角色：不预先勾选
        win.set_hero("凛")
        self.assertEqual((win.hero_filter(), self.rows(win)), ("凛", [("凛 · 标记充能", False)]))

    def test_preview_and_clipboard_both_formats(self):
        """两种格式：预览是选中的方案每套一行，复制到剪贴板的内容和预览完全一致；复制结果那句写明套数和格式。"""
        win = self.window(self.st)
        win.select_all.click()
        expected = {
            S.FORMAT_SHORT: "\n".join(S.to_short(s, self.cat) for s in self.listed),
            S.FORMAT_TEXT: "\n".join(S.to_text(s) for s in self.listed),
        }
        self.assertEqual(win.format(), S.FORMAT_SHORT)  # 默认短字符串
        self.assertTrue(win.format_buttons[S.FORMAT_SHORT].isChecked())
        self.assertEqual(win.format_buttons[S.FORMAT_SHORT].text(), TABLE_FORMATS["short"])
        self.assertEqual(win.format_buttons[S.FORMAT_TEXT].text(), TABLE_FORMATS["text"])
        for fmt in (S.FORMAT_SHORT, S.FORMAT_TEXT):
            with self.subTest(fmt=fmt):
                win.format_buttons[fmt].click()
                self.assertEqual(win.format(), fmt)
                self.assertEqual(win.preview.toPlainText(), expected[fmt])
                self.assertEqual(win.export_text(), expected[fmt])
                self.assertEqual(win.copied.text(), "")  # 换了格式：上一次复制结果那句不再显示
                self.assertEqual(win.copy_to_clipboard(), expected[fmt])
                self.assertEqual(QGuiApplication.clipboard().text(), win.preview.toPlainText())
                self.assertEqual(win.copied.text(), W.COPIED.format(n=3, format=W.FORMAT_NAME[fmt]))
        self.assertEqual(win.copied.text(), "已复制 3 套方案的可读文字")
        win.list_boxes()[0].click()  # 改了勾选：复制结果那句不再显示
        self.assertEqual(win.copied.text(), "")
        win.format_buttons[S.FORMAT_SHORT].click()
        win.select_all.click()  # 全部勾上
        win.select_all.click()  # 全部取消
        win.list_boxes()[0].click()
        win.copy_btn.click()
        self.assertEqual(win.copied.text(), TABLE_COPIED)
        self.assertEqual(QGuiApplication.clipboard().text(), S.to_short(self.listed[0], self.cat))
        self.assertTrue(win.export_warn.isHidden())

    def test_format_remembered(self):
        """记住上次选的格式：新开的窗口照它；选另一种时发出 format_changed。"""
        self.assertEqual(self.window(self.st).format(), S.FORMAT_SHORT)
        win = self.window(self.st)
        got = []
        win.format_changed.connect(got.append)
        win.format_buttons[S.FORMAT_TEXT].click()
        self.assertEqual(got, [S.FORMAT_TEXT])
        self.assertEqual(W.SchemeShareWindow.last_format, S.FORMAT_TEXT)
        again = self.window(self.st)
        self.assertEqual(again.format(), S.FORMAT_TEXT)
        self.assertTrue(again.format_buttons[S.FORMAT_TEXT].isChecked())
        again.set_format("不认识的格式")
        self.assertEqual(again.format(), S.FORMAT_SHORT)

    def test_refresh_after_changes_elsewhere(self):
        """别处（编辑窗口）改名、删除方案后刷新：列表、勾选和预览跟着变。"""
        win = self.window(self.st, "芬妮")
        win.select_all.click()
        self.st.update(replace(self.a, name="改过的名字", tiers={"缓冲盾牌": "first"}))
        self.st.delete(self.c.id)
        win.refresh()
        self.assertEqual(self.rows(win), [("芬妮 · 改过的名字", True)])
        self.assertEqual(win.export_text(), S.to_short(self.st.get(self.a.id), self.cat))
        self.st.delete(self.a.id)
        win.refresh()
        self.assertEqual((self.rows(win), win.export_text()), ([], ""))
        self.assertEqual(self.heroes(win), ["全部角色", "凛", "芬妮"])  # 筛选中的角色没有方案了也留在下拉框里

    def test_copy_fails_when_clipboard_rejects(self):
        """剪贴板被别的程序占着、没写进去（读回来不是刚写的）：不写「已复制」，在导出这一块的警告（和窗口里其他警告同一个
        样子）里写复制失败，返回空；再复制成功时警告收起、照旧写「已复制」（绿色）；改了勾选、预览的内容变了也收起。"""
        win = self.window(self.st)
        win.select_all.click()
        fake = mock.Mock()
        fake.text = mock.Mock(return_value="别人的内容")
        with mock.patch.object(W.QGuiApplication, "clipboard", return_value=fake):
            self.assertEqual(win.copy_to_clipboard(), "")
        self.assertEqual(win.copied.text(), "")
        self.assertEqual((win.export_warn.text(), win.export_warn.objectName()), (W.COPY_FAILED, "warnText"))
        self.assertFalse(win.export_warn.isHidden())
        self.assertEqual(win.copy_to_clipboard(), win.export_text())
        self.assertTrue(win.export_warn.isHidden())
        self.assertEqual((win.copied.text(), win.copied.objectName()), ("已复制 3 套方案的短字符串", "okText"))
        with mock.patch.object(W.QGuiApplication, "clipboard", return_value=fake):
            win.copy_to_clipboard()
        self.assertFalse(win.export_warn.isHidden())
        win.list_boxes()[0].click()
        self.assertTrue(win.export_warn.isHidden())
        self.assertEqual(win.copied.text(), "")

    def test_escape_does_not_close_default_skin(self):
        """默认皮肤（系统标题栏）：按 Esc 不关，和其他窗口的规矩一样。"""
        win = self.window(self.st)
        win.show()
        APP.processEvents()
        QTest.keyClick(win, Qt.Key_Escape)
        APP.processEvents()
        self.assertTrue(win.isVisible())

    def test_tab_order_after_refresh(self):
        """方案勾选框每次刷新重建后仍排在「全选」后面、列表区、格式之前（默认次序会落到「关闭」之后）；粘贴框按 Tab 换焦点。"""
        win = self.window(self.st)
        boxes = win.list_boxes()
        self.assertEqual(len(boxes), 3)
        self.assertIs(win.select_all.nextInFocusChain(), boxes[0])  # 「全选」后面紧跟着第一套
        self.assertIs(boxes[0].nextInFocusChain(), boxes[1])
        self.assertIs(boxes[1].nextInFocusChain(), boxes[-1])
        self.assertIs(boxes[-1].nextInFocusChain(), win.list_area)  # 勾选框之后回到列表区、格式，不再落到「关闭」后面
        self.assertTrue(win.paste.tabChangesFocus())

    def test_short_string_unavailable(self):
        """筹码编号表读不出来时短字符串生成不了：这几套不列入预览并写明，可读文字照常。"""
        st = self.store(S.Catalog(self.db, S.ChipCodes()))
        st.add(self.scheme())
        win = self.window(st)
        with self.assertLogs(W.log, "WARNING"):
            win.select_all.click()
        self.assertEqual(win.export_text(), "")
        self.assertFalse(win.copy_btn.isEnabled())
        self.assertFalse(win.export_warn.isHidden())
        self.assertEqual(win.export_warn.text(), "1 套方案无法生成短字符串（筹码编号表读取失败），未列入；请改用可读文字")
        win.format_buttons[S.FORMAT_TEXT].click()
        self.assertTrue(win.export_text().startswith("【治愈坦克】筹码方案 v2｜芬妮"))
        self.assertTrue(win.export_warn.isHidden())
        self.assertTrue(win.copy_btn.isEnabled())

    def test_fit_screen(self):
        """屏幕放不下时按可用高度缩小，不小于布局需要的最小高度；放得下时不变。"""
        win = self.window(self.st)
        win._available_height = lambda: 560
        win.fit_screen()
        self.assertLess(win.height(), W.SIZE[1])
        self.assertEqual(win.height(), max(win.minimumSizeHint().height(), 560 - W.SCREEN_MARGIN))
        roomy = self.window(self.st)
        roomy._available_height = lambda: 1000
        roomy.fit_screen()
        self.assertEqual(roomy.height(), W.SIZE[1])


# ------------------------------------------------------------ 导入


class ImportTest(_Base):
    def test_results_like_table(self):
        """识别结果照对照表：「识别到 2 套：」；能导入的「✓ 凛 · 标记充能　目标流派 标记、充能；必选 2、推荐 4、不选 3」；
        校验不符的那套写原因、不导入；前后带别的字也认。"""
        text = ("【标记充能】筹码方案 v2｜凛｜必选：魔法箭袋、忍术飞镖｜推荐：标记喷罐、电流剑、"
                "额外电池、手持风扇-大｜不选：银行卡-余额少、银行卡-余额多、银行卡-用不完")
        bad = "【速攻】CA1-7Hq3k"
        self.assertEqual([p.kind for p in S.parse_share(bad, self.cat).items[0].problems], [S.PROBLEM_BAD_CODE])
        win = self.window(self.store())
        win.paste.setPlainText("凛的方案，充能流也不错：" + text + "\n" + bad + "\n欢迎交流")
        win.recognize()
        self.assertEqual(win.result_lines, [
            ("dim", TABLE_FOUND),
            ("ok", TABLE_OK),
            ("warn", "⚠ 第 2 套（速攻）：" + table_problem("字符串不完整")),
        ])
        self.assertEqual(rendered(win.results), [line for _, line in win.result_lines])
        self.assertEqual(win.import_btn.text(), TABLE_IMPORT.replace("N", "1"))
        self.assertTrue(win.import_btn.isEnabled())

    def test_every_problem_sentence(self):
        """每种问题按对照表写原因，前面写「第 N 套（名称）：」；不能导入的不导入，其余的照常导入。"""
        good = S.to_short(S.Scheme(hero="凛", name="好的", tiers={"魔法箭袋": "core"}), self.cat)
        broken = good[:-1] + ("2" if good[-1] != "2" else "3")
        fanny = self.db.heroes["芬妮"].id
        newer_codes = S.encode_payload(S.ShortPayload(fanny, ((34, "core"), (200, "first"), (201, "first"))))
        no_hero = S.encode_payload(S.ShortPayload(999, ((34, "core"),)))
        lines = [
            good,
            broken,
            "【缺角色】筹码方案 v2｜必选：缓冲盾牌",
            "【新版本】筹码方案 v4｜芬妮｜必选：缓冲盾牌",
            "【新筹码】CA2-" + newer_codes,
            "【旧筹码】筹码方案 v2｜芬妮｜必选：缓冲盾牌｜不选：手电筒-一般",
            "【错字】筹码方案 v2｜芬妮｜推荐：完全不存在的筹码、另一个也不存在",
            "【未收录】CA2-" + no_hero,
            "【新角色】筹码方案 v2｜不存在的角色｜必选：缓冲盾牌",
            "【一二三四五六七八九十甲乙丙】筹码方案 v2｜芬妮｜必选：缓冲盾牌",
            "【长说明】筹码方案 v2｜芬妮｜必选：缓冲盾牌｜说明：" + "长" * 61,
        ]
        kinds = [[p.kind for p in item.problems] for item in S.parse_share("\n".join(lines), self.cat).items]
        self.assertEqual(kinds, [[], [S.PROBLEM_BAD_CODE], [S.PROBLEM_BAD_TEXT], [S.PROBLEM_NEWER],
                                 [S.PROBLEM_UNKNOWN_CODES], [S.PROBLEM_REMOVED], [S.PROBLEM_UNKNOWN_NAMES],
                                 [S.PROBLEM_HERO_ID], [S.PROBLEM_HERO_NAME], [S.PROBLEM_NAME_CUT],
                                 [S.PROBLEM_NOTE_CUT]])  # 前提：每一行正好是一种问题
        win = self.window(self.store())
        win.paste.setPlainText("\n".join(lines))
        result = win.recognize()
        self.assertEqual(len(result.schemes), 6)
        long_name = "一二三四五六七八九十甲乙"
        # 分享格式不带流派：目标流派由必选、推荐的筹码推出来（魔法箭袋是标记，缓冲盾牌是治愈）
        self.assertEqual(win.result_lines, [
            ("dim", "识别到 11 套："),
            ("ok", "✓ 凛 · 好的　目标流派 标记；必选 1"),
            ("warn", "⚠ 第 2 套（好的）：" + table_problem("字符串不完整")),
            ("warn", "⚠ 第 3 套（缺角色）：" + table_problem("方案文字格式")),
            ("warn", "⚠ 第 4 套（新版本）：" + table_problem("这一套来自")),
            ("ok", "✓ 芬妮 · 新筹码　目标流派 治愈；必选 1"),
            ("warn", "⚠ 第 5 套（新筹码）：" + table_problem("N 个筹码", N="2")),
            ("ok", "✓ 芬妮 · 旧筹码　目标流派 治愈；必选 1"),
            ("warn", "⚠ 第 6 套（旧筹码）：" + table_problem("已忽略已移除", X="手电筒-一般")),
            ("ok", "✓ 芬妮 · 错字"),
            ("warn", "⚠ 第 7 套（错字）：" + table_problem("未能识别的筹码名", X="完全不存在的筹码、另一个也不存在")),
            ("warn", "⚠ 第 8 套（未收录）：" + table_problem("角色编号", N="999")),
            ("warn", "⚠ 第 9 套（新角色）：" + table_problem("角色「", X="不存在的角色")),
            ("ok", "✓ 芬妮 · " + long_name + "　目标流派 治愈；必选 1"),
            ("warn", "⚠ 第 10 套（" + long_name + "）：" + table_problem("名称超过")),
            ("ok", "✓ 芬妮 · 长说明　目标流派 治愈；必选 1"),
            ("warn", "⚠ 第 11 套（长说明）：" + table_problem("说明超过")),
        ])
        self.assertEqual(win.import_btn.text(), "导入 6 套为我的方案")
        added = win.import_schemes()
        self.assertEqual([s.name for s in added], ["好的", "新筹码", "旧筹码", "错字", long_name, "长说明"])
        self.assertEqual(len(win.store.schemes()), 6)

    def test_code_table_unreadable(self):
        """筹码编号表读不出来（程序文件不完整）：标了档位的短字符串那一套写明原因、不导入（不导入成一套空方案）；
        可读文字照常导入。"""
        win = self.window(self.store(S.Catalog(self.db, S.ChipCodes())))
        win.paste.setPlainText(S.to_short(self.scheme(), self.cat) + "\n" + S.to_text(self.scheme(name="文字的")))
        win.recognize()
        self.assertEqual(win.result_lines, [
            ("dim", "识别到 2 套："),
            ("warn", "⚠ 第 1 套（治愈坦克）：" + NO_CODES),
            ("ok", "✓ 芬妮 · 文字的　目标流派 治愈；必选 2、推荐 1"),
        ])
        self.assertEqual(win.import_btn.text(), "导入 1 套为我的方案")
        self.assertEqual([s.name for s in win.import_schemes()], ["文字的"])

    def test_ok_line_parts(self):
        """能导入的一套：没有目标流派时不写这一项，只写标了的档位；什么都没标时只写角色和名称。分享格式不带流派，目标流派
        由标记推出来（schemes.target_schools：列进方案的流派里有必选、推荐的，可以超过两个，按星光、治愈、标记、充能的顺序）。"""
        rin = S.Scheme(hero="照", name="不选为主", tiers={"汉娜人偶": "second", "星币锤": "avoid", "银行卡-余额少": "avoid"})
        self.assertEqual(W.ok_line(rin, self.db), "✓ 照 · 不选为主　可选 1、不选 2")
        # 推荐了充能的电流剑
        self.assertEqual(W.ok_line(S.Scheme(hero="凛", name="充能流", tiers={"电流剑": "first"}), self.db),
                         "✓ 凛 · 充能流　目标流派 充能；推荐 1")
        self.assertEqual(W.ok_line(S.Scheme(hero="凛", name="空"), self.db), "✓ 凛 · 空")
        # 只标了可选的流派不算目标流派；通用筹码不属于流派
        self.assertEqual(W.ok_line(S.Scheme(hero="凛", name="可选和通用", tiers={"标靶": "second", "大背包": "core"}),
                                   self.db), "✓ 凛 · 可选和通用　必选 1、可选 1")
        # 必选、推荐跨三个流派：三个都写
        three = S.Scheme(hero="凛", name="三个流派", tiers={"紫色飞星": "core", "缓冲盾牌": "first", "标记喷罐": "first"})
        self.assertEqual(W.ok_line(three, self.db), "✓ 凛 · 三个流派　目标流派 星光、治愈、标记；必选 1、推荐 2")
        self.assertEqual(S.target_schools(self.db, three.tiers), ("星光", "治愈", "标记"))

    def test_import_creates_new_without_overwrite(self):
        """导入只新建、不覆盖：同名加「（2）」，原来那套不变；导入后清空粘贴框、写明导入了哪几套、刷新方案列表、发出信号。"""
        st = self.store()
        old = st.add(self.scheme(note="原来的说明", author="甲"))
        win = self.window(st, "芬妮")
        got = []
        win.schemes_imported.connect(got.append)
        win.format_buttons[S.FORMAT_TEXT].click()
        win.select_all.click()
        shared = win.copy_to_clipboard()
        rin = S.to_short(S.Scheme(hero="凛", name="标记充能", tiers={"魔法箭袋": "core"}), self.cat)
        win.paste.setPlainText("看看这两套：\n" + shared + "\n" + rin + " 欢迎交流")
        win.recognize()
        self.assertEqual(win.import_btn.text(), "导入 2 套为我的方案")
        before = st.path.read_bytes()
        added = win.import_schemes()
        self.assertEqual([(s.hero, s.name) for s in added], [("芬妮", "治愈坦克（2）"), ("凛", "标记充能")])
        self.assertEqual(st.get(old.id), old)  # 原来那套不变
        self.assertEqual(content(added[0])[2:], content(old)[2:])  # 内容相同，只有名称加了后缀
        self.assertEqual(got, [["芬妮", "凛"]])
        self.assertEqual(win.result_lines, [("ok", "已导入筹码方案「治愈坦克（2）」（芬妮）"),
                                            ("ok", "已导入筹码方案「标记充能」（凛）")])
        self.assertEqual(win.result_lines[1][1], TABLE_IMPORTED.replace("X", "标记充能"))
        self.assertEqual(win.paste.toPlainText(), "")
        self.assertEqual((win.import_btn.text(), win.import_btn.isEnabled()), ("导入 0 套为我的方案", False))
        self.assertEqual(self.rows(win), [("芬妮 · 治愈坦克", True), ("芬妮 · 治愈坦克（2）", False)])
        self.assertEqual(self.heroes(win), ["全部角色", *self.sorted_heroes(["芬妮", "凛"])])
        # 已存进方案文件
        self.assertNotEqual(st.path.read_bytes(), before)
        again = S.SchemeStore(st.path, self.cat)
        self.assertEqual([(s.hero, s.name) for s in again.schemes()],
                         [("芬妮", "治愈坦克"), ("芬妮", "治愈坦克（2）"), ("凛", "标记充能")])
        # 粘贴新的内容后，「已导入」那几行换成新的识别结果；清空后什么都不显示
        win.paste.setPlainText("随便写的文字")
        win.recognize()
        self.assertEqual(win.result_lines, [("dim", "识别到 0 套")])
        self.assertTrue(win.clear_btn.isEnabled())
        win.clear_btn.click()
        self.assertEqual((win.paste.toPlainText(), win.result_lines), ("", []))
        self.assertFalse(win.clear_btn.isEnabled())

    def test_use_buttons_after_import(self):
        """导入以后一套一行，每行右边「使用这套方案」（导入不自动换用：方案文件里谁用哪套不变）；点了发 use_requested
        （所属角色, 方案 id），结果交回来以后那一行的按钮换成结果那一句（悬浮窗当场换用的、设给别的角色的各一种写法），
        别的行照旧有按钮；粘贴新的内容、清空以后这几行不再显示。"""
        st = self.store()
        win = self.window(st, "芬妮")
        win.show()
        APP.processEvents()
        asked = []
        win.use_requested.connect(lambda hero, sid: asked.append((hero, sid)))
        texts = [S.to_short(self.scheme(name="甲"), self.cat), S.to_short(self.scheme(hero="凛", name="乙"), self.cat),
                 S.to_short(self.scheme(hero="米米", name="丙"), self.cat)]
        win.paste.setPlainText("\n".join(texts))
        win.recognize()
        added = win.import_schemes()
        self.assertEqual([st.current(h) for h in ("芬妮", "凛", "米米")], [None, None, None])
        rows = done_rows(win)
        self.assertEqual([lines for lines, _, _ in rows], [[W.IMPORTED.format(name=a.name, hero=a.hero)] for a in added])
        self.assertTrue(all(btn.isVisibleTo(win) and btn.text() == "使用这套方案" for _, btn, _ in rows))
        self.assertTrue(all(note.isHidden() for _, _, note in rows))
        self.assertEqual(win.imported_rows(), [(W.IMPORTED.format(name=a.name, hero=a.hero), "") for a in added])
        rows[1][1].click()
        self.assertEqual(asked, [("凛", added[1].id)])
        win.show_use_result(added[1].id, "later")
        win.show_use_result(added[0].id, "now")
        rows = done_rows(win)
        self.assertEqual([(btn.isHidden(), note.isHidden(), note.text()) for _, btn, note in rows],
                         [(True, False, "悬浮窗已改用这套方案"), (True, False, "已设好，下次玩凛时用这套方案"),
                          (False, True, "")])
        self.assertEqual(rows[0][2].objectName(), "useNote")
        win.paste.setPlainText("随便写的文字")
        win.recognize()
        self.assertTrue(win.done_box.isHidden())
        self.assertEqual(win.imported_rows(), [])

    def test_use_gone_and_restore(self):
        """导入的一套在别处删掉了：那一行写「这套方案已被删除」（刷新时，不重建这几行）；悬浮窗交回 gone 也一样。换皮肤重开
        窗口时（state / restore）这几行和各自的结果照旧。"""
        st = self.store()
        win = self.window(st)
        win.paste.setPlainText(S.to_short(self.scheme(name="甲"), self.cat) + "\n"
                               + S.to_short(self.scheme(name="乙"), self.cat))
        win.recognize()
        added = win.import_schemes()
        buttons = [btn for _, btn, _ in done_rows(win)]
        win.show_use_result(added[0].id, "now")
        st.delete(added[1].id)
        win.refresh()
        rows = done_rows(win)
        self.assertEqual([btn for _, btn, _ in rows], buttons)  # 同一批部件：没有重建
        self.assertEqual([(note.text(), note.objectName()) for _, _, note in rows],
                         [("悬浮窗已改用这套方案", "useNote"), ("这套方案已被删除", "useNoteWarn")])
        again = self.window(st)
        again.restore(win.state())
        self.assertEqual(again.imported_rows(), win.imported_rows())
        self.assertEqual([note.text() for _, _, note in done_rows(again)], ["悬浮窗已改用这套方案", "这套方案已被删除"])

    def test_many_imported_rows_scroll(self):
        """一次导入很多套（50 套）：每套一行、各自一个按钮，行多了在识别结果区里上下滚动，窗口最矮的高度不跟着变高。"""
        from tests.support.player_layer import with_player_layer
        db = with_player_layer(self.db)
        heroes = list(db.player_synergy)
        st = self.store()
        win = self.window(st)
        win.show()
        APP.processEvents()
        before = win.minimumSizeHint().height()
        win.paste.setPlainText("\n".join(S.to_short(S.scheme_from_default(db, heroes[i % len(heroes)]), self.cat)
                                         for i in range(S.PASTE_MAX_SCHEMES)))
        win.recognize()
        added = win.import_schemes()
        APP.processEvents()
        self.assertEqual(len(added), S.PASTE_MAX_SCHEMES)
        self.assertEqual(len(done_rows(win)), S.PASTE_MAX_SCHEMES)
        self.assertEqual(win.minimumSizeHint().height(), before)
        self.assertLessEqual(win.height(), max(W.SIZE[1], before))

    def test_round_trip_default_copies(self):
        """导出 → 粘贴 → 导入后内容相同：每个有玩家思路的角色一套「复制默认推荐」（角色数从资料库读，不写死），两种格式，
        经过剪贴板；再导入回原来的方案文件时不覆盖，同名加「（2）」。玩家思路用测试用的默认推荐档位（复制出来的方案有内容）。"""
        from tests.support.player_layer import with_player_layer
        db = with_player_layer(self.db)
        heroes = list(db.player_synergy)
        self.assertGreater(len(heroes), 0)
        for fmt in (S.FORMAT_SHORT, S.FORMAT_TEXT):
            with self.subTest(fmt=fmt):
                src = self.store()
                originals = {s.hero: s for s in (src.add(S.scheme_from_default(db, h)) for h in heroes)}
                win = self.window(src)  # 全部角色
                win.set_format(fmt)
                win.select_all.click()  # 全选 → 复制就是导出全部方案
                self.assertEqual(len(win.selected_schemes()), len(heroes))
                win.copy_btn.click()
                clip = QGuiApplication.clipboard().text()
                self.assertEqual(clip, win.preview.toPlainText())
                self.assertEqual(len(clip.splitlines()), len(heroes))
                self.assertEqual(win.copied.text(), W.COPIED.format(n=len(heroes), format=W.FORMAT_NAME[fmt]))
                dst = self.store()
                other = self.window(dst)
                other.paste.setPlainText(clip)
                result = other.recognize()
                self.assertEqual(len(result.schemes), len(heroes))
                self.assertEqual([line for tone, line in other.result_lines if tone == "warn"], [])
                self.assertEqual(other.import_btn.text(), TABLE_IMPORT.replace("N", str(len(heroes))))
                other.import_btn.click()
                full = fmt == S.FORMAT_TEXT
                imported = {s.hero: s for s in dst.schemes()}
                self.assertEqual(set(imported), set(originals))
                for hero, s in originals.items():
                    self.assertEqual(content(imported[hero], full), content(s, full), hero)
                # 导入回原来的方案文件：不覆盖，同名加「（2）」
                win.paste.setPlainText(clip)
                self.assertEqual(len(win.import_schemes()), len(heroes))
                self.assertEqual(sorted(s.name for s in src.schemes()),
                                 sorted(["默认推荐（副本）"] * len(heroes) + ["默认推荐（副本）（2）"] * len(heroes)))
                for s in originals.values():
                    self.assertEqual(src.get(s.id), s)

    def test_buttons_follow_paste(self):
        """粘贴框空着时导入、清空都不可用；改动后稍等自动识别（不用点按钮）；只有空白时不显示识别结果。"""
        win = self.window(self.store())
        self.assertEqual((win.import_btn.text(), win.import_btn.isEnabled()), ("导入 0 套为我的方案", False))
        self.assertFalse(win.clear_btn.isEnabled())
        self.assertEqual(win.result_lines, [])
        win.paste.setPlainText(S.to_short(self.scheme(), self.cat))
        self.assertTrue(win.clear_btn.isEnabled())
        deadline = time.perf_counter() + 3
        while not win.result_lines and time.perf_counter() < deadline:
            APP.processEvents(QEventLoop.AllEvents, 10)
        self.assertEqual(win.result_lines[0], ("dim", "识别到 1 套："))
        self.assertTrue(win.import_btn.isEnabled())
        win.paste.setPlainText("   \n  ")
        win.recognize()
        self.assertEqual(win.result_lines, [])
        self.assertFalse(win.import_btn.isEnabled())

    def test_read_only(self):
        """方案文件来自更新版本（只读）：导入按钮不可用、写明原因，方案文件不变；导出照常。"""
        path = self.tmp() / "schemes.json"
        S.SchemeStore(path, self.cat).add(self.scheme())
        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = S.FILE_VERSION + 1
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        before = path.read_bytes()
        st = S.SchemeStore(path, self.cat)
        self.assertTrue(st.read_only)
        win = self.window(st, "芬妮")
        self.assertFalse(win.read_only_hint.isHidden())
        self.assertEqual(win.read_only_hint.text(), TABLE_READ_ONLY)
        win.paste.setPlainText(S.to_short(self.scheme(name="另一套"), self.cat))
        win.recognize()
        self.assertEqual(win.result_lines, [("dim", "识别到 1 套："), ("ok", "✓ 芬妮 · 另一套　目标流派 治愈；必选 2、推荐 1")])
        self.assertEqual((win.import_btn.text(), win.import_btn.isEnabled()), ("导入 1 套为我的方案", False))
        self.assertEqual(win.import_schemes(), [])
        self.assertEqual(path.read_bytes(), before)
        win.select_all.click()
        self.assertTrue(win.copy_btn.isEnabled())
        self.assertEqual(win.copy_to_clipboard(), S.to_short(self.scheme(), self.cat))
        self.assertEqual(QGuiApplication.clipboard().text(), win.export_text())
        self.assertTrue(self.window(self.store()).read_only_hint.isHidden())

    def test_large_pastes(self):
        """一次最多 50 套、2 万字，超出部分另起一行说明；粘贴后给出结果（含识别前稍等的那一下和排版）花的本进程 CPU 时间
        不超过 1 秒。

        量 CPU 时间（time.process_time）不量墙钟：几个测试进程同时跑、机器很忙时，本进程分不到 CPU 的那段不算进来。单元测试
        只防明显变慢（例如不小心写成平方级）；「粘贴后 0.5 秒内出结果」的体感要求留给性能测量，按改前、改后交替测的办法核对。
        识别和排版都在界面线程里做（识别前稍等用的是界面线程的定时器），下面等结果时一直在处理事件、不睡，CPU 时间量得到它的
        全部耗时。复制默认推荐用测试用的默认推荐档位（复制出来的方案有内容）。"""
        from tests.support.player_layer import with_player_layer
        db = with_player_layer(self.db)
        heroes = list(db.player_synergy)
        rows = []
        for i in range(60):
            s = S.scheme_from_default(db, heroes[i % len(heroes)])
            rows.append(S.to_short(s, self.cat) if i % 2 else S.to_text(s))
        many = "\n".join(rows)
        self.assertLess(len(many), S.PASTE_MAX_CHARS)
        noise = "\n" + "噪声" * 10000
        pool = "".join(sorted({ch for n in self.db.chips for ch in n}))
        rng = random.Random(7)
        garbage = "\n".join(
            "【乱{}】筹码方案 v2｜芬妮｜推荐：".format(i)
            + "、".join("".join(rng.choice(pool) for _ in range(rng.randint(2, 5))) for _ in range(150))
            for i in range(80))
        cases = {"超过 50 套": many, "超过 2 万字": "\n".join(rows[:10]) + noise, "两样都超": many + noise,
                 "乱码": garbage}
        for label, text in cases.items():
            with self.subTest(label=label):
                expected = S.parse_share(text, self.cat)
                timings = []
                for _ in range(3):  # 取三次里最省的（某一次赶上垃圾回收等一次性的开销）；每次用新的 Catalog（模糊匹配没有缓存）
                    win = self.window(self.store(S.Catalog(self.db, self.cat.codes)))
                    t0, cpu0 = time.perf_counter(), time.process_time()
                    win.paste.setPlainText(text)
                    while not win.result_lines and time.perf_counter() - t0 < 30:  # 只防卡死：机器忙时墙钟会长很多
                        APP.processEvents(QEventLoop.AllEvents, 5)
                    win.results.heightForWidth(540)  # 排版也算在内
                    timings.append(time.process_time() - cpu0)
                self.assertLess(min(timings), 1.0, timings)
                lines = win.result_lines
                n = len(expected.items)
                self.assertEqual(lines[0], ("dim", "识别到 {} 套：".format(n)))
                self.assertEqual(sum(1 for tone, _ in lines if tone == "ok"), len(expected.schemes))
                self.assertEqual(("warn", W.TEXT_CUT) in lines, expected.text_cut)
                self.assertEqual(("warn", W.SKIPPED.format(n=expected.skipped)) in lines, expected.skipped > 0)
                self.assertEqual(win.import_btn.text(), "导入 {} 套为我的方案".format(len(expected.schemes)))
                if label == "超过 50 套":
                    self.assertEqual((n, expected.skipped, expected.text_cut), (50, 10, False))
                    self.assertEqual(lines[-1], ("warn", "⚠ 另有 10 套超出一次最多 50 套的限制，未识别；请分次粘贴"))
                elif label == "超过 2 万字":
                    self.assertEqual((n, expected.skipped, expected.text_cut), (10, 0, True))
                    self.assertEqual(lines[-1], ("warn", "⚠ 粘贴的内容超过 2 万字，只识别了前 2 万字；其余部分请分次粘贴"))
                elif label == "两样都超":
                    self.assertTrue(expected.text_cut and expected.skipped)
                    self.assertEqual(lines[-2:], [("warn", W.TEXT_CUT), ("warn", W.SKIPPED.format(n=expected.skipped))])


# ------------------------------------------------------------ 适用地图


class MapsTest(_Base):
    """适用地图：能导入的一套标了地图时写在目标流派后面；导出列表里标了地图的一套用悬停说明写出来；勾选的方案里有标了
    地图的、而分享格式不带地图时（WRITE_MAPS 关掉），预览框下面写一句提示；开着（默认）时不写。"""

    def test_import_result_writes_maps(self):
        s = self.scheme(name="带地图的", maps=("龙宫游乐园", "魔法学院"))
        line = W.ok_line(s, self.db)
        self.assertEqual(line, "✓ 芬妮 · 带地图的　目标流派 治愈；适用地图 龙宫游乐园、魔法学院；"
                               "必选 2、推荐 1")
        result = S.ParseResult(items=(S.ParsedScheme(index=1, name=s.name, kind=S.FORMAT_TEXT, scheme=s),))
        self.assertEqual(W.result_lines(result, self.db), [("dim", "识别到 1 套："), ("ok", line)])

    def test_import_result_omits_generic(self):
        line = W.ok_line(self.scheme(), self.db)
        self.assertNotIn("适用地图", line)

    def test_export_list_tooltip(self):
        st = self.store()
        st.add(self.scheme(name="带地图的", maps=("龙宫游乐园",)))
        st.add(self.scheme(name="通用的"))
        win = self.window(st)
        tips = {shown(box.text()): box.toolTip() for box in win.list_boxes()}
        self.assertEqual(tips["芬妮 · 带地图的"], "适用地图：龙宫游乐园")
        self.assertEqual(tips["芬妮 · 通用的"], "")

    def test_export_maps_note(self):
        st = self.store()
        st.add(self.scheme(name="带地图的", maps=("龙宫游乐园",)))
        st.add(self.scheme(name="通用的"))
        win = self.window(st)
        win.show()  # offscreen 平台：不会出现在桌面上
        APP.processEvents()
        # 分享格式带地图（WRITE_MAPS 开着，默认）：勾了标地图的那一套也不写
        self.assertTrue(S.WRITE_MAPS)
        win.list_boxes()[0].setChecked(True)
        APP.processEvents()
        self.assertTrue(win.list_boxes()[0].isChecked())
        self.assertFalse(win.maps_warn.isVisible())
        win.list_boxes()[0].setChecked(False)
        APP.processEvents()
        # 开关关掉（分享出去的内容不带地图）时才写
        with mock.patch.object(S, "WRITE_MAPS", False):
            self.assertFalse(win.maps_warn.isVisible())  # 没勾选：不写
            win.list_boxes()[0].setChecked(True)
            APP.processEvents()
            self.assertTrue(win.maps_warn.isVisible())
            self.assertEqual(win.maps_warn.text(), W.MAPS_TEMP_NOTE)
            self.assertEqual(W.MAPS_TEMP_NOTE, "勾选的方案里有标了适用地图的：分享出去的内容暂时不带适用地图，"
                                              "对方导入后算全地图通用。")
            win.list_boxes()[0].setChecked(False)
            APP.processEvents()
            self.assertFalse(win.maps_warn.isVisible())
            # 勾了通用的那一套：不写
            win.list_boxes()[1].setChecked(True)
            APP.processEvents()
            self.assertFalse(win.maps_warn.isVisible())


# ------------------------------------------------------------ 按编号导入（作业站）


class FetchTest(_Base):
    """按编号导入：hub.ENABLED 为假时不显示；认不出的输入不联网；编号、网址按 parse_ref 的结果去取；取回的放进粘贴框（空的、
    已有内容、同一段已在里面三种）并马上识别，不自动导入；各种出错的句子；取的时候不能再点；关窗口以后晚到的结果丢掉。

    不联网：hub_api.fetch_scheme 换成假的（返回 self.result，是异常时抛出），winhttp.Session 换成一建就报错的，每条测试
    最后核对没有建过。后台线程换成当场调用，或者先攒着（defer）、测试里再调；另有一条用真的线程。"""

    def setUp(self):
        super().setUp()
        guard_hub(self)
        patcher = mock.patch.object(hub_api, "fetch_scheme", self.fake_fetch)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.rin = self.scheme(hero="凛", name="标记充能", tiers={"魔法箭袋": "core", "标记喷罐": "first"})
        self.short = S.to_short(self.rin, self.cat)
        self.result = None
        self.calls: list[tuple[object, object]] = []  # fetch_scheme 收到的（ref, stop）
        self.pending: list = []  # 攒着、还没跑的后台任务

    def fake_fetch(self, ref, *, stop=None, **kw):
        self.calls.append((ref, stop))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    def share(self, **kw) -> hub_api.SchemeShare:
        values = dict(id=1234, name="标记充能", short_text=self.short, text=S.to_text(self.rin),
                      url="https://astral-chip-compass.com/builds/1234/")
        values.update(kw)
        return hub_api.SchemeShare(**values)

    def fetch_window(self, store=None, defer: bool = False) -> W.SchemeShareWindow:
        win = self.window(store or self.store())
        win.run_background = self.pending.append if defer else (lambda fn: fn())
        return win

    def run_pending(self) -> None:
        while self.pending:
            self.pending.pop(0)()

    def wait_until(self, condition, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                self.fail("等后台线程交回结果超时")
            APP.processEvents(QEventLoop.AllEvents, 20)

    @staticmethod
    def cut_labels(win) -> list[str]:
        """窗口里看得见、自动换行、高度不够整段显示的标签（识别结果区、方案列表里的在区里滚动着看，不算）。"""
        out = []
        for label in win.findChildren(QLabel):
            if not (label.isVisible() and label.wordWrap() and label.text()):
                continue
            if win.results_area.isAncestorOf(label) or win.list_area.isAncestorOf(label):
                continue
            if label.height() < label.heightForWidth(label.width()):
                out.append(label.text()[:16])
        return out

    def test_hidden_when_hub_off(self):
        """hub.ENABLED 为假：七种排法里输入框、按钮、提示行都不显示（照样放在窗口里），按钮、回车都不取。"""
        from advisor.ui import theme
        self.addCleanup(theme.set_skin, theme.DEFAULT_KEY)
        with mock.patch.object(hub, "ENABLED", False):
            for skin in theme.skins():
                theme.set_skin(skin.key)
                with self.subTest(skin=skin.key, layout=W.SchemeShareWindow.layout_name()):
                    win = self.fetch_window()
                    win.show()
                    settle()
                    for part in (win.fetch_row, win.ref_edit, win.fetch_btn, win.fetch_note):
                        self.assertFalse(part.isVisible())
                    self.assertTrue(win.isAncestorOf(win.fetch_row))
                    self.assertTrue(win.isAncestorOf(win.fetch_note))
                    win.ref_edit.setText("1234")
                    self.assertFalse(win.fetch_by_ref())
                    win.fetch_btn.clicked.emit()
                    win.ref_edit.returnPressed.emit()
                    self.assertEqual(self.calls, [])
                    self.assertIsNone(win.fetch_note.state())

    def test_bad_input_not_fetched(self):
        """认不出的输入不联网：提示行写怎么输入（空着时写先输入），输入框和按钮照旧能点；改了输入框的字收起这一句。"""
        win = self.fetch_window()
        win.show()
        cases = {"": TABLE_FETCH["FETCH_EMPTY"], "   ": TABLE_FETCH["FETCH_EMPTY"],
                 "abc": TABLE_FETCH["FETCH_BAD_REF"], "0": TABLE_FETCH["FETCH_BAD_REF"],
                 "http://astral-chip-compass.com/builds/12/": TABLE_FETCH["FETCH_BAD_REF"],
                 "https://example.com/builds/12/": TABLE_FETCH["FETCH_BAD_REF"],
                 "astral-chip-compass.com/builds/": TABLE_FETCH["FETCH_BAD_REF"]}
        for text, sentence in cases.items():
            with self.subTest(text=text):
                self.assertIsNone(hub_api.parse_ref(text))
                win.ref_edit.setText(text)
                self.assertFalse(win.fetch_by_ref())
                self.assertEqual(win.fetch_note.state(), ("warn", sentence))
                self.assertTrue(win.ref_edit.isEnabled() and win.fetch_btn.isEnabled())
                self.assertFalse(win.fetching())
        self.assertEqual(self.calls, [])
        win.ref_edit.setFocus()
        QTest.keyClicks(win.ref_edit, "1")
        self.assertIsNone(win.fetch_note.state())

    def test_number_and_url_by_parse_ref(self):
        """编号（全角、前后空白也认）和网址都按 parse_ref 的结果取：交给 fetch_scheme 的是（编号, 随机码），带着停止事件；
        取的时候提示行写正在取几号。"""
        self.result = self.share()
        win = self.fetch_window(defer=True)
        cases = (("1234", (1234, "")), ("　１２３４ ", (1234, "")),
                 ("astral-chip-compass.com/builds/77/?key=Ab_9-x&utm=1", (77, "Ab_9-x")),
                 ("https://astral-chip-compass.com/builds/88/", (88, "")))
        for text, ref in cases:
            with self.subTest(text=text):
                self.assertEqual(hub_api.parse_ref(text), ref)
                win.ref_edit.setText(text)
                count = len(self.calls)
                self.assertTrue(win.fetch_by_ref())
                self.assertEqual(win.fetch_note.state(), ("busy", TABLE_FETCH_BUSY.replace("1234", str(ref[0]))))
                self.assertEqual((len(self.pending), len(self.calls)), (1, count))  # 联网的那一步只在后台线程里调
                self.run_pending()
                seen, stop = self.calls[-1]
                self.assertEqual(seen, ref)
                self.assertIsInstance(stop, threading.Event)
                self.assertFalse(stop.is_set())
                self.assertFalse(win.fetching())

    def test_enter_and_button(self):
        """在输入框里按回车、点「按编号导入」都会取。"""
        self.result = self.share()
        win = self.fetch_window()
        win.show()
        settle()
        win.ref_edit.setText("1234")
        win.ref_edit.setFocus()
        QTest.keyClick(win.ref_edit, Qt.Key_Return)
        self.assertEqual([ref for ref, _ in self.calls], [(1234, "")])
        win.fetch_btn.click()
        self.assertEqual([ref for ref, _ in self.calls], [(1234, ""), (1234, "")])

    def test_paste_cases(self):
        """取回的分享码放进粘贴框：空的（只有空白也算）整个换掉；已有内容时另起一行接在后面（末尾已经换行时不多空一行）；
        同一段已在里面时不再接。放进去以后马上识别（不等识别前稍等的那一下），不自动导入；放进去的那一下可以撤销。没有短字符串
        时用可读文字。"""
        self.result = self.share()
        st = self.store()
        win = self.fetch_window(st)
        win.ref_edit.setText("1234")
        win.paste.setPlainText("  \n ")
        win.recognize()
        self.assertTrue(win.fetch_by_ref())
        self.assertEqual(win.paste.toPlainText(), self.short)
        self.assertFalse(win._parse_timer.isActive())
        self.assertEqual(win.result_lines, [("dim", "识别到 1 套："), ("ok", "✓ 凛 · 标记充能　目标流派 标记；必选 1、推荐 1")])
        self.assertEqual(win.fetch_note.state(), ("ok", TABLE_FETCH_DONE.replace("X", "标记充能")))
        self.assertEqual((win.import_btn.text(), win.import_btn.isEnabled()), ("导入 1 套为我的方案", True))
        self.assertEqual(st.schemes(), [])  # 不自动导入
        # 同一段已经在里面：不再接
        self.assertTrue(win.fetch_by_ref())
        self.assertEqual(win.paste.toPlainText(), self.short)
        self.assertEqual(win.fetch_note.state(), ("ok", TABLE_FETCH_SAME.replace("X", "标记充能")))
        # 已有别的内容：另起一行接在后面，两套一起识别；撤销回到放进去以前
        other = S.to_short(self.scheme(), self.cat)
        win.paste.setPlainText(other)
        win.fetch_by_ref()
        self.assertEqual(win.paste.toPlainText(), other + "\n" + self.short)
        self.assertEqual(win.result_lines[0], ("dim", "识别到 2 套："))
        win.paste.undo()
        self.assertEqual(win.paste.toPlainText(), other)
        win.paste.setPlainText(other + "\n")
        win.fetch_by_ref()
        self.assertEqual(win.paste.toPlainText(), other + "\n" + self.short)
        # 没有短字符串时用可读文字
        self.result = self.share(short_text=None)
        win.clear_paste()
        win.fetch_by_ref()
        self.assertEqual(win.paste.toPlainText(), S.to_text(self.rin))
        self.assertEqual(win.result_lines[0], ("dim", "识别到 1 套："))
        self.assertEqual(st.schemes(), [])

    def test_name_plain_and_shortened(self):
        """作业站上的名称照纯文本显示（网页标记、& 原样）；超过 12 个字的截短、末尾加省略号；没有名称时只写编号。"""
        win = self.fetch_window()
        win.ref_edit.setText("1234")
        for name, shown_name in (("<b>粗</b>&x", "<b>粗</b>&x"), ("一二三四五六七八九十甲乙", "一二三四五六七八九十甲乙"),
                                 ("一二三四五六七八九十甲乙丙", "一二三四五六七八九十甲…")):
            with self.subTest(name=name):
                self.result = self.share(name=name)
                win.clear_paste()
                win.fetch_by_ref()
                self.assertEqual(win.fetch_note.text(), TABLE_FETCH_DONE.replace("X", shown_name))
        self.assertEqual(win.fetch_note.info.textFormat(), Qt.PlainText)
        self.result = self.share(name="")
        win.clear_paste()
        win.fetch_by_ref()
        self.assertEqual(win.fetch_note.text(), TABLE_FETCH_DONE_UNNAMED)
        win.fetch_by_ref()
        self.assertEqual(win.fetch_note.text(), TABLE_FETCH_SAME_UNNAMED)

    def test_error_sentences(self):
        """出错时按 HubError 的种类写一句（不弹窗），粘贴框不变，输入框和按钮恢复能点；取得太频繁时写要等多久；用户停下的
        不写；别的种类写取方案时出错。"""
        win = self.fetch_window()
        win.paste.setPlainText("原来的内容")
        win.ref_edit.setText("1234")
        cases = [(hub_api.HubError(kind), text) for kind, text in TABLE_FETCH_FAILED.items()]
        cases += [(hub_api.HubError(hub_api.RATE_LIMITED, retry_after=s), text) for s, text in TABLE_FETCH_RATE.items()]
        cases.append((hub_api.HubError(hub_api.UNAUTHORIZED), TABLE_FETCH_ERROR))
        for error, text in cases:
            with self.subTest(kind=error.kind, retry_after=error.retry_after):
                self.result = error
                self.assertTrue(win.fetch_by_ref())
                self.assertEqual(win.fetch_note.state(), ("warn", text))
                self.assertFalse(win.fetching())
                self.assertTrue(win.ref_edit.isEnabled() and win.fetch_btn.isEnabled())
                self.assertEqual(win.paste.toPlainText(), "原来的内容")
        self.result = hub_api.HubError(hub_api.STOPPED)
        win.fetch_by_ref()
        self.assertIsNone(win.fetch_note.state())
        self.assertFalse(win.fetch_note.isVisibleTo(win))

    def test_unexpected_error_logged(self):
        """后台线程里抛出 HubError 以外的异常：程序不出错，记一条错误记录（异常的类型和出错的位置，不带异常的说明），
        提示行写取方案时出错。交回以后不再留着这个异常（它带着后台线程的调用栈）。"""
        self.result = RuntimeError("回话里的内容 secret-body")
        win = self.fetch_window(defer=True)
        win.ref_edit.setText("1234")
        with mock.patch.object(W, "log_error") as logged:
            self.assertTrue(win.fetch_by_ref())
            call = win._fetch
            self.run_pending()
        logged.assert_called_once()
        title, detail = logged.call_args.args
        self.assertEqual(title, W.FETCH_LOG)
        self.assertIn("RuntimeError", detail)
        self.assertNotIn("secret-body", detail)
        self.assertEqual(win.fetch_note.state(), ("warn", TABLE_FETCH_ERROR))
        self.assertFalse(win.fetching())
        self.assertIsNone(call.error)
        self.assertIsNone(call.result)

    def test_busy_blocks_another(self):
        """取的时候输入框和按钮不能点，再点、再按回车都不另起一次；取完恢复，焦点回到输入框。"""
        self.result = self.share()
        win = self.fetch_window(defer=True)
        win.show()
        settle()
        win.ref_edit.setText("1234")
        win.ref_edit.setFocus()
        self.assertTrue(win.fetch_by_ref())
        self.assertTrue(win.fetching())
        self.assertFalse(win.ref_edit.isEnabled())
        self.assertFalse(win.fetch_btn.isEnabled())
        self.assertFalse(win.fetch_by_ref())
        win.fetch_btn.click()
        QTest.keyClick(win.ref_edit, Qt.Key_Return)
        self.assertEqual(len(self.pending), 1)
        self.assertIsNone(win.state()["hub_note"])  # 正在取：不交出提示行
        self.run_pending()
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(win.fetching())
        self.assertTrue(win.ref_edit.isEnabled() and win.fetch_btn.isEnabled())
        self.assertIs(win.focusWidget(), win.ref_edit)
        self.assertEqual(win.paste.toPlainText(), self.short)

    def test_busy_repaints_all_skins(self):
        """七种排法：在输入框里按回车开始取以后（输入框和按钮不能点，键盘焦点挪到下一个部件）照常重画，取完焦点回到输入框、
        取回的放进粘贴框。Qt 不报画不上去：碳纤流光的粘贴框装着切角的框，焦点挪进粘贴框以后重画框时不能往里面的视口上画
        （往上画时要画光标，程序崩溃）。"""
        from advisor.ui import theme
        self.addCleanup(theme.set_skin, theme.DEFAULT_KEY)
        messages = qt_messages(self)
        self.result = self.share()
        for skin in theme.skins():
            theme.set_skin(skin.key)
            with self.subTest(skin=skin.key, layout=W.SchemeShareWindow.layout_name()):
                win = self.fetch_window(defer=True)
                win.show()
                settle()
                tabs = getattr(win, "tabs", {})
                if W.TAB_IMPORT in tabs:
                    tabs[W.TAB_IMPORT].setChecked(True)
                    settle()
                win.activateWindow()
                win.ref_edit.setFocus()
                win.ref_edit.setText("1234")
                QTest.keyClick(win.ref_edit, Qt.Key_Return)
                self.assertTrue(win.fetching())
                win.repaint()
                settle()
                self.run_pending()
                settle()
                self.assertFalse(win.fetching())
                self.assertIs(win.focusWidget(), win.ref_edit)
                self.assertEqual(win.paste.toPlainText(), self.short)
                win.close()
        self.assertEqual(paint_messages(messages), [])

    def test_close_drops_late_result(self):
        """关窗口时置停止事件（交给 fetch_scheme 的就是它）：输入框和按钮恢复，「正在取」那一句收起；晚到的结果丢掉，不碰
        窗口、不出错。窗口删掉以后才交回的结果同样丢掉。"""
        self.result = self.share()
        win = self.fetch_window(defer=True)
        win.show()
        settle()
        win.ref_edit.setText("1234")
        win.fetch_by_ref()
        call = win._fetch
        win.close()
        self.assertTrue(call.stop.is_set())
        self.assertFalse(win.fetching())
        self.assertTrue(win.ref_edit.isEnabled() and win.fetch_btn.isEnabled())
        self.assertIsNone(win.fetch_note.state())
        self.run_pending()
        self.assertIs(self.calls[-1][1], call.stop)
        self.assertEqual(win.paste.toPlainText(), "")
        self.assertIsNone(win.fetch_note.state())
        self.assertIsNone(call.result)  # 丢掉的结果同样不留着
        # 窗口删掉以后才交回
        gone = W.SchemeShareWindow(self.db, self.store(), None)
        gone.run_background = self.pending.append
        gone.ref_edit.setText("1234")
        gone.fetch_by_ref()
        gone.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.run_pending()
        self.assertEqual(len(self.calls), 2)

    def test_real_thread(self):
        """真的在后台线程里取：结果排队交回界面线程（在界面线程里放进粘贴框、写提示行）；出错时同样。"""
        win = self.window(self.store())  # run_background 照旧起线程
        threads = []
        done = win._fetch_done

        def record(share, error):
            threads.append(threading.current_thread() is threading.main_thread())
            done(share, error)

        win._fetch_done = record
        self.result = self.share()
        win.ref_edit.setText("1234")
        self.assertTrue(win.fetch_by_ref())
        self.wait_until(lambda: not win.fetching())
        self.assertEqual(threads, [True])
        self.assertEqual(win.paste.toPlainText(), self.short)
        self.result = hub_api.HubError(hub_api.NETWORK)
        self.assertTrue(win.fetch_by_ref())
        self.wait_until(lambda: not win.fetching())
        self.assertEqual(threads, [True, True])
        self.assertEqual(win.fetch_note.state(), ("warn", TABLE_FETCH_FAILED["network"]))

    def test_read_only_store_can_fetch(self):
        """方案文件只读（来自更新版本）：照样能取、放进粘贴框、识别，导入照旧不能点，方案文件不变。"""
        path = self.tmp() / "schemes.json"
        S.SchemeStore(path, self.cat).add(self.scheme())
        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = S.FILE_VERSION + 1
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        before = path.read_bytes()
        st = S.SchemeStore(path, self.cat)
        self.assertTrue(st.read_only)
        self.result = self.share()
        win = self.fetch_window(st)
        win.ref_edit.setText("1234")
        self.assertTrue(win.fetch_by_ref())
        self.assertEqual(win.paste.toPlainText(), self.short)
        self.assertEqual((win.import_btn.text(), win.import_btn.isEnabled()), ("导入 1 套为我的方案", False))
        self.assertEqual(win.import_schemes(), [])
        self.assertEqual(path.read_bytes(), before)

    def test_state_restore(self):
        """换皮肤重开窗口时接回输入框和提示行（取完以后的那一句）；「正在取」不接回；作业站入口关着时不接回提示行。"""
        self.result = self.share()
        win = self.fetch_window()
        win.ref_edit.setText("1234")
        win.fetch_by_ref()
        state = win.state()
        self.assertEqual((state["hub_ref"], state["hub_note"]), ("1234", ("ok", TABLE_FETCH_DONE.replace("X", "标记充能"))))
        again = self.fetch_window()
        again.restore(json.loads(json.dumps(state)))  # 存成 JSON 再读回来也接得上（二元组变成列表）
        self.assertEqual(again.ref_edit.text(), "1234")
        self.assertEqual(again.fetch_note.state(), state["hub_note"])
        self.assertEqual(again.paste.toPlainText(), self.short)
        busy = self.fetch_window()
        busy.restore(dict(state, hub_note=("busy", TABLE_FETCH_BUSY)))
        self.assertIsNone(busy.fetch_note.state())
        with mock.patch.object(hub, "ENABLED", False):
            off = self.window(self.store())
            off.restore(state)
            self.assertIsNone(off.fetch_note.state())

    def test_layout_all_skins(self):
        """七种排法：按编号导入那一行在导入这一块里、粘贴框上面；输入框和按钮在同一行，输入框和按钮画出来的那一块一样高、
        上下对齐。默认大小、最小大小下取回一套以后：提示行和别的自动换行的字整段显示，粘贴框、识别结果区不比最矮还矮，按编号
        导入那一行不被挤扁，导入那一行看得见（卡片在滚动区里的排法自动滚过去）。"""
        from PySide6.QtCore import QPoint, QRect

        from advisor.ui import theme
        self.addCleanup(theme.set_skin, theme.DEFAULT_KEY)
        self.result = self.share(name="标记充能·速攻（第二版，适合新手）")
        for skin in theme.skins():
            theme.set_skin(skin.key)
            for size in ("default", "min"):
                with self.subTest(skin=skin.key, layout=W.SchemeShareWindow.layout_name(), size=size):
                    win = self.fetch_window()
                    win.show()
                    settle()
                    tabs = getattr(win, "tabs", {})
                    if W.TAB_IMPORT in tabs:
                        tabs[W.TAB_IMPORT].setChecked(True)
                    if size == "min":
                        win.resize(win.minimumSizeHint())
                    settle()
                    edit, btn = win.ref_edit, win.fetch_btn
                    self.assertTrue(win.import_box.isAncestorOf(win.fetch_row))

                    def top(widget):
                        return widget.mapTo(win, QPoint(0, 0)).y()

                    self.assertLessEqual(top(win.fetch_row) + win.fetch_row.height(), top(win.paste))
                    inset_top, inset_bottom = W._body_insets(btn)
                    body = btn.height() - inset_top - inset_bottom
                    self.assertEqual((top(edit), edit.height()), (top(btn) + inset_top, body))
                    self.assertGreaterEqual(edit.width(), edit.minimumSizeHint().width())
                    win.ref_edit.setText("1234")
                    self.assertTrue(win.fetch_by_ref())
                    settle()
                    note = win.fetch_note
                    self.assertTrue(note.isVisible())
                    self.assertGreaterEqual(note.height(), note.info.heightForWidth(note.width()))
                    self.assertEqual(self.cut_labels(win), [])
                    self.assertGreaterEqual(win.paste.height(), win.paste.minimumSizeHint().height())
                    self.assertGreaterEqual(win.results_area.height(), W.RESULTS_MIN_H)
                    self.assertEqual((top(edit), edit.height()), (top(btn) + inset_top, body))
                    scroll = win.findChild(QScrollArea, "cardsScroll")
                    area = scroll.viewport() if scroll is not None else win
                    shown_rect = QRect(win.import_btn.mapTo(area, QPoint(0, 0)), win.import_btn.size())
                    self.assertTrue(area.rect().contains(shown_rect), shown_rect)
                    if scroll is not None:  # 卡片在滚动区里：照首选高度排（粘贴框约三行，识别结果整个排开），不挤
                        self.assertEqual(win.paste.height(), W.PASTE_H[0])
                        self.assertGreaterEqual(win.results_area.height(), win.results_area.sizeHint().height())


# ------------------------------------------------------------ 转义


class AccountTest(_Base):
    """发到作业站、登录对话框、「已登录作业站」那一行和「退出作业站」。

    不联网、不碰凭据管理器（guard_hub）：hub_api 的 login_nickname、request_device_code、poll_device_token、store_login、
    publish_scheme、update_scheme、logout 都换成假的，记下收到的参数；打开浏览器换成记下网址。后台线程换成当场调用，或者先
    攒着（defer）、测试里再调。"""

    URL = "https://astral-chip-compass.com/builds/88/"

    def setUp(self):
        super().setUp()
        guard_hub(self)
        self.calls: list[tuple] = []  # 假的 hub_api 收到的（函数名, 参数…）
        self.device = hub_api.DeviceCode("dev-secret", "BCDF-GHJK", "https://astral-chip-compass.com/app/device",
                                         "https://astral-chip-compass.com/app/device?code=BCDF-GHJK", 5.0, 600.0)
        self.login = hub_api.Login("tok-secret", "星海旅人")
        self.code_result: object = self.device
        self.poll_result: object = self.login
        self.stored = True
        self.publish_result: object = hub_api.PublishedScheme(88, self.URL, "draft")
        self.update_result: object = hub_api.PublishedScheme(77, "https://astral-chip-compass.com/builds/77/", "public")
        self.after_logout = None  # 退出以后本机读出来的昵称（None 为删掉了）
        fakes = {"request_device_code": self.fake_code, "poll_device_token": self.fake_poll,
                 "store_login": self.fake_store, "publish_scheme": self.fake_publish, "update_scheme": self.fake_update,
                 "logout": self.fake_logout}
        for name, value in fakes.items():
            patcher = mock.patch.object(hub_api, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.opened: list[str] = []
        self.pending: list = []
        self.fen = self.scheme()

    @staticmethod
    def _give(value):
        if isinstance(value, BaseException):
            raise value
        return value

    def fake_code(self, *, stop=None, **kw):
        self.calls.append(("code", stop))
        return self._give(self.code_result)

    def fake_poll(self, device, *, stop=None, **kw):
        self.calls.append(("poll", device, stop))
        return self._give(self.poll_result)

    def fake_store(self, login):
        self.calls.append(("store", login))
        if self.stored:
            self.nickname = login.nickname
        return self.stored

    def fake_publish(self, scheme, catalog, *, token=None, stop=None, **kw):
        self.calls.append(("publish", scheme, token, stop))
        return self._give(self.publish_result)

    def fake_update(self, number, scheme, catalog, *, token=None, stop=None, **kw):
        self.calls.append(("update", number, scheme, token, stop))
        return self._give(self.update_result)

    def fake_logout(self, *, stop=None, **kw):
        self.calls.append(("logout", stop))
        self.nickname = self.after_logout
        return True

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]

    def make(self, store=None, defer: bool = False, check: int = 1) -> W.SchemeShareWindow:
        """导入/导出窗口：store 里有芬妮的两套方案，勾上前 check 套；后台线程当场跑（defer 时先攒着）。"""
        if store is None:
            store = self.store()
            store.add(self.fen)
            store.add(self.scheme(name="第二套"))
        win = self.window(store)
        win.run_background = self.pending.append if defer else (lambda fn: fn())
        win.open_url = lambda url: self.opened.append(url) or True
        win.show()
        settle()
        for box in win.list_boxes()[:check]:
            box.click()
        return win

    def run_pending(self) -> None:
        while self.pending:
            self.pending.pop(0)()

    def published(self) -> list[tuple]:
        return [c for c in self.calls if c[0] in ("publish", "update")]

    def test_hidden_when_hub_off(self):
        """hub.ENABLED 为假：「发到作业站」、提示行、「已登录」那一行七种排法里都不显示，点了也不发、不开登录对话框，不读本机的
        登录状态。"""
        from advisor.ui import theme
        self.addCleanup(theme.set_skin, theme.DEFAULT_KEY)
        self.nickname = "星海旅人"
        with mock.patch.object(hub, "ENABLED", False), \
                mock.patch.object(hub_api, "login_nickname", side_effect=AssertionError("不该读")) as read:
            for skin in theme.skins():
                theme.set_skin(skin.key)
                with self.subTest(skin=skin.key):
                    win = self.make()
                    for widget in (win.post_btn, win.post_note, win.post_actions, win.account_row):
                        self.assertFalse(widget.isVisibleTo(win), widget.objectName())
                    self.assertFalse(win.publish_selected())
                    self.assertFalse(win.logout())
                    self.assertIsNone(win._login)
            read.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_pick_exactly_one(self):
        """没勾、勾了两套：提示行写怎么做，不联网、不开登录对话框；按钮平时照旧能点。"""
        for check in (0, 2):
            with self.subTest(check=check):
                win = self.make(check=check)
                self.assertTrue(win.post_btn.isEnabled())
                win.post_btn.click()
                self.assertEqual(win.post_note.state(), ("warn", TABLE_POST["POST_PICK_ONE"]))
                self.assertIsNone(win._login)
        self.assertEqual(self.calls, [])

    def test_short_text_fails(self):
        """生成不了这一套的短字符串：写原因，不联网。"""
        self.nickname = "星海旅人"
        win = self.make()
        with mock.patch.object(S, "to_short", side_effect=ValueError("x")):
            self.assertFalse(win.publish_selected())
        self.assertEqual(win.post_note.state(), ("warn", TABLE_POST["POST_NO_SHORT"]))
        self.assertEqual(self.calls, [])

    def test_logged_in_publishes_snapshot(self):
        """已登录：在后台把勾选的那一套的快照发上去（带地图写分享码由 hub_api 做），用本机存的登录信息（token 为 None）；
        发好了写不公开的那一句，「在作业站上看」打开作业站上的那一套。方案文件只读时照样能发。"""
        self.nickname = "星海旅人"
        store = self.store()
        store.add(self.fen)
        store.read_only = True
        win = self.make(store)
        self.assertTrue(win.publish_selected())
        (_, scheme, token, stop), = self.published()
        self.assertIsNot(scheme, store.schemes()[0])  # 快照：不是方案存储里的那个对象
        self.assertEqual(content(scheme), content(store.schemes()[0]))
        self.assertIsNone(token)
        self.assertIsInstance(stop, threading.Event)
        self.assertEqual(win.post_note.state(), ("ok", TABLE_POST["POST_DONE"]["draft"]))
        self.assertTrue(win.view_btn.isVisibleTo(win))
        self.assertFalse(win.dup_update_btn.isVisibleTo(win))
        win.view_btn.click()
        self.assertEqual(self.opened, [self.URL])
        self.assertNotIn("code", self.names())

    def test_busy_name_follows_ui_language(self):
        """界面是繁体时，正在发的那一句里的方案名按界面语言转；发上去的快照照原文（方案存储里记的也是原文）。窗口在简体下
        建好，只在点「发到作业站」时换成繁体：句子本身是简体常量，转的只有方案名。"""
        self.nickname = "星海旅人"
        name = self.fen.name
        self.assertNotEqual(lang.to_hant(name), name)  # 前提：简繁写法不同
        win = self.make(defer=True)
        with mock.patch.object(lang, "CURRENT", lang.HANT):
            self.assertTrue(win.publish_selected())
        self.assertEqual(win.post_note.state(), ("busy", W.POST_BUSY.format(name=lang.to_hant(name))))
        self.run_pending()
        (_, scheme, _, _), = self.published()
        self.assertEqual(scheme.name, name)

    def test_login_then_publish(self):
        """没登录：先开登录对话框，要短码、大字显示、自动打开授权页面，等到授权以后存进本机、「已登录」那一行显示昵称，接着发；
        发的时候用本机存的登录信息。"""
        win = self.make(defer=True)
        self.assertFalse(win.account_row.isVisibleTo(win))
        self.assertTrue(win.publish_selected())
        dialog = win._login
        self.assertIsInstance(dialog, W.HubLogin)
        self.assertFalse(win.post_btn.isEnabled())
        self.assertEqual(dialog.status.text(), TABLE_POST["LOGIN_ASKING"])
        self.pending.pop(0)()  # 要到短码
        self.assertEqual(dialog.code.text(), "BCDF-GHJK")
        self.assertTrue(dialog.code.isVisibleTo(dialog))
        self.assertEqual(dialog.status.text(), TABLE_POST["LOGIN_STEPS"])
        self.assertEqual(self.opened, [self.device.verify_url_complete])
        dialog.reopen_btn.click()
        self.assertEqual(self.opened, [self.device.verify_url_complete] * 2)
        self.pending.pop(0)()  # 授权了
        self.assertEqual(self.names()[:3], ["code", "poll", "store"])
        self.assertIs(self.calls[1][1], self.device)
        self.assertIsInstance(self.calls[1][2], threading.Event)
        self.assertIsNone(win._login)
        self.assertTrue(win.account_row.isVisibleTo(win))
        self.assertEqual(win.account_label.text(), "已登录作业站：星海旅人")
        self.run_pending()
        (_, scheme, token, _), = self.published()
        self.assertEqual(scheme.name, "治愈坦克")
        self.assertIsNone(token)
        self.assertEqual(win.post_note.state(), ("ok", TABLE_POST["POST_DONE"]["draft"]))
        self.assertTrue(win.post_btn.isEnabled())
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertFalse(isValid(dialog))  # 关掉以后删掉

    def test_cancel_login(self):
        """取消登录：叫停等授权的那一步，不发，提示行不写；晚到的授权丢掉。×（关掉对话框）同样。"""
        for how in ("cancel", "close"):
            with self.subTest(how=how):
                self.calls.clear()
                win = self.make(defer=True)
                win.publish_selected()
                dialog = win._login
                self.pending.pop(0)()
                waiting = dialog._call  # 等授权的那一步（还没跑）
                (dialog.cancel_btn.click if how == "cancel" else dialog.close)()
                self.assertTrue(waiting.stop.is_set())
                self.assertIsNone(win._login)
                self.assertTrue(win.post_btn.isEnabled())
                self.run_pending()  # 晚到的授权：丢掉
                self.assertEqual(self.names(), ["code", "poll"])
                self.assertIs(self.calls[-1][2], waiting.stop)  # 交给 poll_device_token 的就是这个停止事件
                self.assertIsNone(win.post_note.state())
                self.assertFalse(win.account_row.isVisibleTo(win))

    def test_login_failures(self):
        """拒绝、过期、连不上、作业站出错：对话框里写一句，按钮换成「重新登录」「关闭」，不发；「重新登录」重新要短码。"""
        cases = {"denied": hub_api.DENIED, "expired": hub_api.EXPIRED, "network": hub_api.NETWORK,
                 "server": hub_api.SERVER}
        for key, kind in cases.items():
            with self.subTest(kind=key):
                self.calls.clear()
                self.nickname = None  # 上一轮登录好了：换回没登录
                self.poll_result = hub_api.HubError(kind)
                win = self.make()
                win.publish_selected()
                dialog = win._login
                self.assertEqual(dialog.state_name, "failed")
                self.assertEqual(dialog.error.text(), TABLE_POST["LOGIN_FAILED"][key])
                self.assertTrue(dialog.retry_btn.isVisibleTo(dialog) and dialog.close_btn.isVisibleTo(dialog))
                self.assertFalse(dialog.cancel_btn.isVisibleTo(dialog))
                self.assertEqual(self.published(), [])
                self.poll_result = self.login
                dialog.retry_btn.click()  # 重新登录：这次授权了，接着发
                self.assertEqual(self.names(), ["code", "poll", "code", "poll", "store", "publish"])
                win.close()
        self.code_result = hub_api.HubError(hub_api.NETWORK)  # 要短码就连不上
        self.nickname = None
        win = self.make()
        win.publish_selected()
        self.assertEqual(win._login.error.text(), TABLE_POST["LOGIN_FAILED"]["network"])
        win._login.close_btn.click()
        self.assertIsNone(win._login)
        self.assertIsNone(win.post_note.state())

    def test_store_fails_still_publishes(self):
        """登录信息存不进本机：这一次照样用刚拿到的登录信息发（token），结果后面另写一句下次还要重新登录；「已登录」那一行不显示
        （本机读不出）。"""
        self.stored = False
        win = self.make()
        win.publish_selected()
        (_, _, token, _), = self.published()
        self.assertEqual(token, "tok-secret")
        self.assertEqual(win.post_note.state(),
                         ("ok", TABLE_POST["POST_DONE"]["draft"] + "\n" + TABLE_POST["LOGIN_NOT_SAVED"]))
        self.assertFalse(win.account_row.isVisibleTo(win))
        self.assertNotIn("tok-secret", repr(win.state()))

    def test_duplicate_update_or_skip(self):
        """409：提示行问要不要更新作业站上已有的那一套，「在作业站上看」看那一套；「更新那一套」在后台调 update_scheme（编号是
        已有的那一套，内容是这一套的快照），「不更新」收起、不联网。"""
        self.nickname = "星海旅人"
        other = "https://astral-chip-compass.com/builds/77/"
        for choice in ("update", "skip"):
            with self.subTest(choice=choice):
                self.calls.clear()
                self.publish_result = hub_api.HubError(hub_api.DUPLICATE, scheme_id=77, url=other)
                win = self.make()
                win.publish_selected()
                self.assertEqual(win.post_note.state(), ("ask", TABLE_POST["POST_DUPLICATE"].replace("N", "77")))
                self.assertTrue(win.dup_update_btn.isVisibleTo(win) and win.dup_skip_btn.isVisibleTo(win))
                self.assertTrue(win.view_btn.isVisibleTo(win))
                win.view_btn.click()
                self.assertEqual(self.opened[-1], other)
                (win.dup_update_btn if choice == "update" else win.dup_skip_btn).click()
                if choice == "update":
                    (_, number, scheme, token, _), = [c for c in self.calls if c[0] == "update"]
                    self.assertEqual((number, scheme.name, token), (77, "治愈坦克", None))
                    self.assertEqual(win.post_note.state(), ("ok", TABLE_POST["UPDATE_DONE"]["public"].replace("N", "77")))
                else:
                    self.assertNotIn("update", self.names())
                    self.assertIsNone(win.post_note.state())
                    self.assertFalse(win.post_actions.isVisibleTo(win))
                win.close()

    def test_done_texts_by_status(self):
        """发好了按作业站回的状态写：新发的、更新的各三种。"""
        for status in hub_api.STATUSES:
            with self.subTest(status=status):
                published = hub_api.PublishedScheme(77, self.URL, status)
                self.assertEqual(W.post_done_text(published, False), TABLE_POST["POST_DONE"][status])
                self.assertEqual(W.post_done_text(published, True), TABLE_POST["UPDATE_DONE"][status].replace("N", "77"))

    def test_site_texts_plain_and_cut(self):
        """400、403：作业站给的说明照纯文本显示（带 <b> 也照原样），几项合起来、太长时截短；没带说明时写一句通用的。"""
        self.nickname = "星海旅人"
        cases = [
            (hub_api.HubError(hub_api.INVALID, fields={"name": "<b>名称</b>太长", "note": "说明里有不能用的字"}),
             TABLE_POST["POST_INVALID"].replace("X", "<b>名称</b>太长；说明里有不能用的字")),
            (hub_api.HubError(hub_api.FORBIDDEN, reason="<i>邮箱</i>还没确认"),
             TABLE_POST["POST_FORBIDDEN"].replace("X", "<i>邮箱</i>还没确认")),
            (hub_api.HubError(hub_api.INVALID, fields={}), TABLE_POST["POST_INVALID_BARE"]),
            (hub_api.HubError(hub_api.FORBIDDEN), TABLE_POST["POST_FORBIDDEN_BARE"]),
        ]
        for error, text in cases:
            with self.subTest(text=text):
                self.publish_result = error
                win = self.make()
                win.publish_selected()
                self.assertEqual(win.post_note.state(), ("warn", text))
                self.assertEqual(win.post_note.warn.textFormat(), Qt.PlainText)
                win.close()
        long = "长" * 300
        cut = W.post_error_text(hub_api.HubError(hub_api.FORBIDDEN, reason=long))
        self.assertEqual(cut, TABLE_POST["POST_FORBIDDEN"].replace("X", "长" * (W.SITE_TEXT_SHOWN - 1) + "…"))

    def test_other_errors(self):
        """401：写登录已失效，「已登录」那一行跟着改成没登录（hub_api 已删掉本机的登录信息）；其余种类各写一句，用户停下的不写。
        404 分新发、更新两种写：新发时是作业站上没有收方案的地方（网站还没开这一项，或程序和网站对不上），更新时是那一套不在了。"""
        self.nickname = "星海旅人"
        win = self.make()
        self.assertTrue(win.account_row.isVisibleTo(win))

        def unauthorized(*args, **kw):
            self.nickname = None
            raise hub_api.HubError(hub_api.UNAUTHORIZED)

        with mock.patch.object(hub_api, "publish_scheme", unauthorized):
            win.publish_selected()
        self.assertEqual(win.post_note.state(), ("warn", TABLE_POST["POST_UNAUTHORIZED"]))
        self.assertFalse(win.account_row.isVisibleTo(win))
        for kind, text in TABLE_POST["POST_FAILED"].items():
            self.assertEqual(W.post_error_text(hub_api.HubError(kind), updating=True), text)
            if kind != hub_api.NOT_FOUND:
                self.assertEqual(W.post_error_text(hub_api.HubError(kind)), text)
        self.assertEqual(W.post_error_text(hub_api.HubError(hub_api.NOT_FOUND)), TABLE_POST["POST_UNAVAILABLE"])
        win.close()
        # 窗口上：新发回 404、409 以后更新那一套回 404
        self.nickname = "星海旅人"
        self.publish_result = hub_api.HubError(hub_api.NOT_FOUND)
        win = self.make()
        win.publish_selected()
        self.assertEqual(win.post_note.state(), ("warn", TABLE_POST["POST_UNAVAILABLE"]))
        win.close()
        self.publish_result = hub_api.HubError(hub_api.DUPLICATE, scheme_id=77,
                                               url="https://astral-chip-compass.com/builds/77/")
        self.update_result = hub_api.HubError(hub_api.NOT_FOUND)
        win = self.make()
        win.publish_selected()
        win.dup_update_btn.click()
        self.assertEqual(win.post_note.state(), ("warn", TABLE_POST["POST_FAILED"]["not_found"]))
        self.assertEqual(W.post_error_text(hub_api.HubError(hub_api.RATE_LIMITED, retry_after=30)),
                         "发方案过于频繁，请 30 秒后再试。")
        self.assertEqual(W.post_error_text(hub_api.HubError(hub_api.STOPPED)), "")
        self.assertEqual(W.post_error_text(ValueError("x")), TABLE_POST["POST_ERROR"])

    def test_account_row_and_logout(self):
        """「已登录作业站：昵称」：昵称照纯文本、太长截短，空串时只写「已登录作业站」；「退出作业站」在后台退出，做完再读一次本机：
        读不出时那一行藏起来，还读得出时写没能退出。"""
        for nickname, text in (("<b>星</b>", "已登录作业站：<b>星</b>"), ("", "已登录作业站"),
                               ("一二三四五六七八九十一二三四五六七八", "已登录作业站：一二三四五六七八九十一二三四五…")):
            with self.subTest(nickname=nickname):
                self.nickname = nickname
                win = self.make()
                self.assertEqual(win.account_label.text(), text)
                self.assertEqual(win.account_label.textFormat(), Qt.PlainText)
                win.close()
        self.nickname = "星海旅人"
        win = self.make()
        self.assertTrue(win.logout())
        self.assertEqual(self.names(), ["logout"])
        self.assertIsInstance(self.calls[0][1], threading.Event)
        self.assertFalse(win.account_row.isVisibleTo(win))
        self.assertEqual(win.post_note.state(), ("ok", TABLE_POST["LOGOUT_DONE"]))
        self.nickname = "星海旅人"
        self.after_logout = "星海旅人"  # 本机没删掉
        win.refresh_account()
        win.logout()
        self.assertTrue(win.account_row.isVisibleTo(win))
        self.assertEqual(win.post_note.state(), ("warn", TABLE_POST["LOGOUT_FAILED"]))

    def test_busy_blocks_account_not_fetch(self):
        """账号上的事同一时间只做一件：发的时候「发到作业站」「退出作业站」不能点，再点不另起；按编号导入另算一路，照样能点。"""
        self.nickname = "星海旅人"
        win = self.make(defer=True)
        self.assertTrue(win.publish_selected())
        self.assertFalse(win.post_btn.isEnabled())
        self.assertFalse(win.logout_btn.isEnabled())
        self.assertFalse(win.publish_selected())
        self.assertFalse(win.logout())
        self.assertTrue(win.fetch_btn.isEnabled() and win.ref_edit.isEnabled())
        self.assertEqual(len(self.pending), 1)
        self.run_pending()
        self.assertTrue(win.post_btn.isEnabled() and win.logout_btn.isEnabled())
        self.assertEqual(len(self.published()), 1)

    def test_close_cancels_everything(self):
        """关窗口：正在发的那一次叫停，晚到的结果丢掉、不出错；登录对话框跟着关；state() 不交出账号上的事。"""
        self.nickname = "星海旅人"
        win = self.make(defer=True)
        win.publish_selected()
        win.close()
        self.run_pending()
        (_, _, _, stop), = self.published()
        self.assertTrue(stop.is_set())
        self.assertIsNone(win.post_note.state())
        self.assertTrue(win.post_btn.isEnabled())
        self.nickname = None
        win = self.make(defer=True)
        win.publish_selected()
        dialog = win._login
        self.pending.pop(0)()
        state = win.state()
        win.close()
        self.assertFalse(dialog.isVisible())
        self.assertIsNone(win._login)
        self.run_pending()
        self.assertEqual(len(self.published()), 1)  # 只有上面那一次
        self.assertFalse({"post", "login", "account"} & {k.split("_")[0] for k in state})
        fresh = self.make(defer=True)
        fresh.restore(state)
        self.assertIsNone(fresh.post_note.state())
        self.assertIsNone(fresh._login)

    def test_close_lets_logout_finish(self):
        """关窗口时正在退出：退出不叫停、照样做完（让作业站撤销这份登录信息，本机存的删掉），结果不回到关掉的窗口；按钮恢复能点，
        「正在退出」收起。"""
        self.nickname = "星海旅人"
        win = self.make(defer=True)
        self.assertTrue(win.logout())
        self.assertEqual(win.post_note.state(), ("busy", TABLE_POST["LOGOUT_BUSY"]))
        win.close()
        self.assertIsNone(win.post_note.state())
        self.assertTrue(win.post_btn.isEnabled() and win.logout_btn.isEnabled())
        self.run_pending()
        (name, stop), = self.calls
        self.assertEqual(name, "logout")
        self.assertFalse(stop.is_set())
        self.assertIsNone(self.nickname)  # 假的退出照样跑完：本机的登录信息删掉了
        self.assertIsNone(win.post_note.state())  # 没写「已退出」「没能退出」

    def test_browser_only_hub_https(self):
        """授权页面不是作业站的 https 地址时不打开，写网址让用户自己打开。"""
        self.code_result = replace(self.device, verify_url_complete="http://astral-chip-compass.com/app/device")
        win = self.make(defer=True)
        win.publish_selected()
        self.pending.pop(0)()  # 要到短码（等授权的那一步先不跑）
        self.assertEqual(self.opened, [])
        self.assertEqual(win._login.status.text(),
                         TABLE_POST["LOGIN_NO_BROWSER"].replace("U", self.device.verify_url))
        self.assertFalse(W.hub_url("https://evil.example/builds/1/"))
        self.assertTrue(W.hub_url(self.URL))

    @staticmethod
    def cut_labels(root) -> list[str]:
        """root 里看得见、自动换行、高度不够整段显示的标签（识别结果区、方案列表里的在区里滚动着看，不算）。"""
        skip = [getattr(root, name) for name in ("results_area", "list_area") if hasattr(root, name)]
        return [label.text()[:16] for label in root.findChildren(QLabel)
                if label.isVisible() and label.wordWrap() and label.text() and not any(s.isAncestorOf(label) for s in skip)
                and label.height() < label.heightForWidth(label.width())]

    @staticmethod
    def shown(win, widget) -> bool:
        """widget 整个落在看得见的地方：在滚动区（导出那一列的 exportScroll、卡片的 cardsScroll）里时看滚动区的 viewport，
        滚动区本身也整个在窗口里。"""
        from PySide6.QtCore import QPoint, QRect

        area = win
        for name in ("exportScroll", "cardsScroll"):
            scroll = win.findChild(QScrollArea, name)
            if scroll is not None and scroll.isAncestorOf(widget):
                area = scroll.viewport()
                break
        inside = QRect(widget.mapTo(area, QPoint(0, 0)), widget.size())
        outer = QRect(area.mapTo(win, QPoint(0, 0)), area.size())
        return widget.isVisible() and area.rect().contains(inside) and win.rect().contains(outer)

    def test_dialog_all_skins(self):
        """七套皮肤、程序实际用的字体下的登录对话框：自己画外框的皮肤没有系统标题栏（外框、标题、× 同窗口）；字照卡片底色配的
        排法内容放在卡片里；等授权和没登录成功两步，看得见的两个按钮都整个在对话框里、不比首选的窄、不比最小的矮，大字短码不比
        首选高度矮，自动换行的字整段显示；没登录成功时高度跟着内容缩回来（比等授权时矮，下面不留一大块空白）。"""
        from PySide6.QtCore import QPoint, QRect

        from advisor.ui import chrome, theme
        self.addCleanup(theme.set_skin, theme.DEFAULT_KEY)
        self.addCleanup(APP.setFont, APP.font())
        self.poll_result = hub_api.HubError(hub_api.EXPIRED)
        for skin in theme.skins():
            theme.set_skin(skin.key)
            APP.setFont(theme.app_font())
            with self.subTest(skin=skin.key):
                self.calls.clear()
                win = self.make(defer=True)
                win.publish_selected()
                dialog = win._login
                self.pending.pop(0)()  # 要短码
                settle()
                self.assertTrue(dialog.isVisible())
                self.assertEqual(dialog.code.text(), "BCDF-GHJK")
                self.assertEqual(bool(dialog.windowFlags() & Qt.FramelessWindowHint), chrome.custom())
                self.assertEqual(dialog.card is not None, win._kit.dialog_card is not None)
                heights = {}
                for state in ("waiting", "failed"):
                    if state == "failed":
                        self.pending.pop(0)()  # 等授权：短码过期
                        settle()
                    self.assertEqual(dialog.state_name, state)
                    buttons = [b for b in (dialog.reopen_btn, dialog.retry_btn, dialog.cancel_btn, dialog.close_btn)
                               if b.isVisible()]
                    self.assertEqual(len(buttons), 2, state)
                    for btn in buttons:
                        rect = QRect(btn.mapTo(dialog, QPoint(0, 0)), btn.size())
                        self.assertTrue(dialog.rect().contains(rect), (state, btn.text(), rect))
                        self.assertGreaterEqual(btn.width(), btn.sizeHint().width(), (state, btn.text()))
                        self.assertGreaterEqual(btn.height(), btn.minimumSizeHint().height(), (state, btn.text()))
                    if state == "waiting":
                        self.assertGreaterEqual(dialog.code.height(), dialog.code.sizeHint().height())
                    self.assertEqual(self.cut_labels(dialog), [], state)
                    heights[state] = dialog.height()
                self.assertLess(heights["failed"], heights["waiting"])
                win.close()
                self.pending.clear()

    def test_account_layout_all_skins(self):
        """七种排法、程序实际用的字体，默认大小和最小大小：发到作业站以后的几行和「复制到剪贴板」「发到作业站」在同一块里，
        「发到作业站」下面依次是提示行、那一排小按钮、「已登录作业站」那一行，左边和「复制到剪贴板」对齐，上下间距同预览框和复制
        那一行之间的。登录着、作业站上已有一样的一套时：默认大小下窗口不被撑大，「发到作业站」、提示行、三个小按钮、「退出作业站」
        都整个看得见；自动换行的字整段显示，方案列表、预览框不比最矮还矮。作业站给的说明很长（截到 120 字）时，默认大小、最小大小
        下窗口都不因此变得比 768 高的屏幕的可用高度（728）高（左右并排的排法只有导出那一列滚动），字照样整段显示。"""
        from PySide6.QtCore import QPoint

        from advisor.ui import theme
        self.addCleanup(theme.set_skin, theme.DEFAULT_KEY)
        self.addCleanup(APP.setFont, APP.font())
        long_reason = "限" * (W.SITE_TEXT_SHOWN + 30)
        for skin in theme.skins():
            theme.set_skin(skin.key)
            APP.setFont(theme.app_font())
            for size in ("default", "min"):
                with self.subTest(skin=skin.key, layout=W.SchemeShareWindow.layout_name(), size=size):
                    self.nickname = "星海旅人"
                    self.publish_result = hub_api.HubError(hub_api.DUPLICATE, scheme_id=77, url=self.URL)
                    win = self.make()
                    if size == "min":
                        win.resize(win.minimumSizeHint())
                        settle()
                    win.post_btn.click()
                    settle()
                    self.assertEqual(win.post_note.tone, "ask")
                    card = win.copy_btn.parentWidget()
                    parts = (win.post_note, win.post_actions, win.account_row)
                    for part in parts:
                        self.assertIs(part.parentWidget(), card)

                    def top(w) -> int:
                        return w.mapTo(card, QPoint(0, 0)).y()

                    def bottom(w) -> int:
                        return top(w) + w.height()

                    def left(w) -> int:
                        return w.mapTo(card, QPoint(0, 0)).x()

                    spacing = min(top(win.copy_btn), top(win.post_btn)) - bottom(win.preview)
                    if top(win.post_btn) > bottom(win.copy_btn):  # 「发到作业站」单独一行（左右并排的排法）
                        spacing = top(win.copy_btn) - bottom(win.preview)
                        above = bottom(win.post_btn)
                    else:
                        above = max(bottom(win.copy_btn), bottom(win.post_btn))
                    for part in parts:
                        self.assertEqual(top(part) - above, spacing, part.objectName() or "postNote")
                        self.assertEqual(left(part), left(win.copy_btn))
                        above = bottom(part)
                    if size == "default":
                        self.assertEqual((win.width(), win.height()), tuple(W.L.SIZE))  # 入口打开时的大小
                        for widget in (win.post_btn, win.post_note, win.dup_update_btn, win.dup_skip_btn, win.view_btn,
                                       win.logout_btn):
                            self.assertTrue(self.shown(win, widget), widget.objectName() or type(widget).__name__)
                    self.assertEqual(self.cut_labels(win), [])
                    self.assertGreaterEqual(win.list_area.height(), win.list_area.minimumSizeHint().height())
                    self.assertGreaterEqual(win.preview.height(), win.preview.minimumSizeHint().height())
                    # 作业站给的说明很长：照样整段显示；窗口不因此变得比 768 高的屏幕的可用高度高（默认就比它高的几种排法
                    # 卡片在滚动区里，不变高；到了矮屏幕上按可用高度缩小另见各皮肤的测试）
                    before = win.height()
                    self.publish_result = hub_api.HubError(hub_api.FORBIDDEN, reason=long_reason)
                    win.post_btn.click()
                    settle()
                    self.assertTrue(win.post_note.text().endswith(W.ELLIPSIS))
                    self.assertLessEqual(win.height(), max(728, before))
                    self.assertEqual(self.cut_labels(win), [])
                    win.close()


class EscapeTest(_Base):
    def test_markup_shown_as_text(self):
        """名称、作者、说明、未能识别的筹码名里的网页标记和 &：方案列表、预览、识别结果、导入结果里都原样显示文字。"""
        name = "<b>粗</b>&x"
        st = self.store()
        s = st.add(self.scheme(name=name, note="<i>说明</i>&amp;", author="<i>某</i>"))
        self.assertEqual(s.name, name)
        win = self.window(st, "芬妮")
        self.assertEqual(self.rows(win), [("芬妮 · <b>粗</b>&x", False)])
        self.assertEqual(win.list_boxes()[0].text(), "芬妮 · <b>粗</b>&&x")
        win.format_buttons[S.FORMAT_TEXT].click()
        win.select_all.click()
        text = win.preview.toPlainText()
        self.assertIn("【<b>粗</b>&x】筹码方案 v2", text)
        self.assertIn("｜说明：<i>说明</i>&amp;｜作者：<i>某</i>", text)
        win.paste.setPlainText(text + "\n【<i>坏</i>】CA2-1111\n【乱】筹码方案 v2｜芬妮｜推荐：<b>不存在</b>")
        win.recognize()
        expected = [
            "识别到 3 套：",
            "✓ 芬妮 · <b>粗</b>&x　目标流派 治愈；必选 2、推荐 1",
            "⚠ 第 2 套（<i>坏</i>）：" + table_problem("字符串不完整"),
            "✓ 芬妮 · 乱",
            "⚠ 第 3 套（乱）：" + table_problem("未能识别的筹码名", X="<b>不存在</b>"),
        ]
        self.assertEqual([line for _, line in win.result_lines], expected)
        self.assertEqual(rendered(win.results), expected)  # 显示出来的就是这些文字，标记没有被渲染
        self.assertNotIn("<b>", win.results.text())
        self.assertNotIn("<i>", win.results.text())
        added = win.import_schemes()
        self.assertEqual(len(added), 2)
        self.assertIn("<b>", added[0].name)
        # 导入以后一套一行（右边「使用这套方案」）：每一行显示出来的就是这些文字
        self.assertEqual([line for lines, _, _ in done_rows(win) for line in lines],
                         [W.IMPORTED.format(name=a.name, hero=a.hero) for a in added])
        self.assertEqual(rendered(win.results), [])
        self.assertIn(("芬妮 · " + added[0].name, False), self.rows(win))
        self.assertEqual(W.button_text("A&B&&C"), "A&&B&&&&C")
        self.assertEqual(shown(W.button_text("A&B&&C")), "A&B&&C")


# ------------------------------------------------------------ 界面文字


class WordingTest(unittest.TestCase):
    """界面文字照对照表逐字写，写成模块顶层的字面量常量（一致性测试不加载 Qt 也读得到）；书面语。"""

    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        cls.consts = {}
        for node in cls.tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                try:
                    cls.consts[node.targets[0].id] = ast.literal_eval(node.value)
                except (ValueError, TypeError, SyntaxError):
                    pass

    def test_table_texts(self):
        c = self.consts
        for key, text in TABLE.items():
            self.assertEqual(c[key], text, key)
        self.assertEqual(c["FORMAT_OPTION"], TABLE_FORMATS)
        self.assertEqual(c["COPIED"].format(n=1, format=c["FORMAT_NAME"]["short"]), TABLE_COPIED)
        self.assertEqual(c["FOUND"].format(n=2), TABLE_FOUND)
        self.assertEqual(c["IMPORT"].format(n="N"), TABLE_IMPORT)
        self.assertEqual(c["IMPORTED"].format(name="X", hero="凛"), TABLE_IMPORTED)
        self.assertEqual(c["READ_ONLY"], TABLE_READ_ONLY)
        # 导入以后每一行的「使用这套方案」和点了以后的结果
        self.assertEqual((c["USE"], c["USED_NOW"], c["USE_GONE"]), ("使用这套方案", "悬浮窗已改用这套方案", "这套方案已被删除"))
        self.assertEqual(c["USED_LATER"].format(hero="凛"), "已设好，下次玩凛时用这套方案")
        placeholders = {"{count}": "N", "{hero_id}": "N", "{names}": "X", "{hero}": "X"}
        sentences = set()
        for template in c["PROBLEM_TEXT"].values():
            for key, value in placeholders.items():
                template = template.replace(key, value)
            sentences.add(template)
        self.assertEqual(sentences, TABLE_PROBLEMS | {NO_CODES})
        self.assertEqual(c["PROBLEM_TEXT"][S.PROBLEM_NO_CODES], NO_CODES)
        self.assertEqual(c["PROBLEM_LINE"].format(index=2, name="速攻", text="……"), "⚠ 第 2 套（速攻）：……")

    def test_fetch_texts(self):
        """按编号导入的按钮、占位文字、悬停说明和提示行的各句照对照表逐字写；出错的句子按 hub_api 的种类各一句（用户停下的
        不写，取得太频繁的另按要等多久写）。"""
        c = self.consts
        for key, text in TABLE_FETCH.items():
            self.assertEqual(c[key], text, key)
        self.assertEqual(c["FETCH_BUSY"].format(n=1234), TABLE_FETCH_BUSY)
        self.assertEqual(c["FETCH_DONE"].format(name="X", n=1234), TABLE_FETCH_DONE)
        self.assertEqual(c["FETCH_DONE_UNNAMED"].format(n=1234), TABLE_FETCH_DONE_UNNAMED)
        self.assertEqual(c["FETCH_SAME"].format(name="X", n=1234), TABLE_FETCH_SAME)
        self.assertEqual(c["FETCH_SAME_UNNAMED"].format(n=1234), TABLE_FETCH_SAME_UNNAMED)
        self.assertEqual(c["FETCH_FAILED"], TABLE_FETCH_FAILED)
        self.assertEqual(c["FETCH_ERROR"], TABLE_FETCH_ERROR)
        from advisor import hub_api
        self.assertEqual(set(c["FETCH_FAILED"]) | {hub_api.RATE_LIMITED, hub_api.STOPPED},
                         {hub_api.NETWORK, hub_api.NOT_FOUND, hub_api.SERVER, hub_api.BAD_RESPONSE,
                          hub_api.RATE_LIMITED, hub_api.STOPPED})
        limited = hub_api.RATE_LIMITED
        for seconds, text in TABLE_FETCH_RATE.items():
            self.assertEqual(W.fetch_error_text(hub_api.HubError(limited, retry_after=seconds)), text, seconds)
        self.assertEqual(W.fetch_error_text(hub_api.HubError(limited, retry_after=True)), TABLE_FETCH_RATE[None])
        self.assertEqual(W.fetch_error_text(hub_api.HubError(hub_api.STOPPED)), "")
        self.assertEqual(W.fetch_error_text(hub_api.HubError(hub_api.UNAUTHORIZED)), TABLE_FETCH_ERROR)
        self.assertEqual(W.fetch_error_text(ValueError("x")), TABLE_FETCH_ERROR)
        # 作业站上的名称在提示行里最多显示的字数同本机方案名称的上限
        self.assertEqual(W.FETCH_NAME_SHOWN, S.NAME_MAX)

    def test_post_texts(self):
        """发到作业站、登录、退出的各句照对照表逐字写（都是字面量常量）；出错、发好了的几张表的键和 hub_api 的种类、状态一致；
        书面语里不用「发布」两个字。"""
        c = self.consts
        for key, text in TABLE_POST.items():
            with self.subTest(key=key):
                self.assertIn(key, c)
                slot = TABLE_POST_SLOTS.get(key)
                if isinstance(text, dict):
                    expected = {k: v.replace(*slot) if slot else v for k, v in text.items()}
                else:
                    expected = text.replace(*slot) if slot else text
                self.assertEqual(c[key], expected)
        from advisor import hub_api
        self.assertEqual(set(c["POST_DONE"]), set(hub_api.STATUSES))
        self.assertEqual(set(c["UPDATE_DONE"]), set(hub_api.STATUSES))
        self.assertLessEqual(set(c["POST_FAILED"]), hub_api.KINDS)
        self.assertLessEqual(set(c["LOGIN_FAILED"]), hub_api.KINDS)
        self.assertEqual(c["CLOSE"], "关闭")  # 登录没成功以后的「关闭」
        flat = [v for key in TABLE_POST for v in (c[key].values() if isinstance(c[key], dict) else [c[key]])]
        self.assertFalse([t for t in flat + [c["POST_TO_HUB_TIP"]] if "发布" in t])

    def test_keys_and_limits_match_schemes(self):
        """问题种类、格式的键和 advisor/schemes.py 一致；句子里写的上限和 schemes 的上限一致。"""
        c = self.consts
        self.assertEqual(set(c["PROBLEM_TEXT"]), set(S.PROBLEM_ORDER))
        self.assertEqual(set(c["FORMAT_OPTION"]), {S.FORMAT_SHORT, S.FORMAT_TEXT})
        self.assertEqual(set(c["FORMAT_NAME"]), {S.FORMAT_SHORT, S.FORMAT_TEXT})
        self.assertIn("超过 {} 个字".format(S.NAME_MAX), c["PROBLEM_TEXT"][S.PROBLEM_NAME_CUT])
        self.assertIn("超过 {} 个字".format(S.NOTE_MAX), c["PROBLEM_TEXT"][S.PROBLEM_NOTE_CUT])
        self.assertEqual(S.PASTE_MAX_CHARS, 20000)
        self.assertIn("2 万字", c["TEXT_CUT"])
        self.assertIn("最多 {} 套".format(S.PASTE_MAX_SCHEMES), c["SKIPPED"])

    def _chinese_literals(self):
        """含汉字的字符串字面量（不算文档字符串）：(节点, 是否在模块顶层的常量里, 是否是写日志的参数)。"""
        docstrings, top, logged = set(), set(), set()
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    docstrings.add(id(body[0].value))
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "log"):
                logged.update(id(n) for n in ast.walk(node))
        for node in self.tree.body:
            if isinstance(node, ast.Assign):
                top.update(id(n) for n in ast.walk(node.value))
        for node in ast.walk(self.tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
                    and re.search(r"[一-鿿]", node.value)):
                yield node, id(node) in top, id(node) in logged

    def test_written_language(self):
        """书面语：没有语气词，不用「我」「你」指代工具。「我的方案」是功能名，不算指代。"""
        particles = re.compile(r"[吧呢啦哦呀嘛喔咯噢哟呗啊哇嘿嗯呐咧嘞哒呦]")
        pronouns = re.compile(r"[我你俺咱]")
        bad = []
        for node, _, _ in self._chinese_literals():
            text = node.value
            if particles.search(text) or pronouns.search(text.replace("我的方案", "")):
                bad.append((node.lineno, text))
        self.assertEqual(bad, [])

    def test_ui_text_only_in_constants(self):
        """界面上的文字都在模块顶层的常量里（函数里只有写日志用的字面量），不用 f-string 拼界面文字。"""
        stray = [(n.lineno, n.value) for n, in_top, logged in self._chinese_literals() if not in_top and not logged]
        self.assertEqual(stray, [])
        fstrings = [n.lineno for n in ast.walk(self.tree) if isinstance(n, ast.JoinedStr)
                    and any(isinstance(v, ast.Constant) and re.search(r"[一-鿿]", v.value) for v in n.values)]
        self.assertEqual(fstrings, [])


if __name__ == "__main__":
    unittest.main()
