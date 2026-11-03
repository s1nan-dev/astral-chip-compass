"""提示行里的两种：直接截取用不了、改为截取屏幕时的截取方式提示（悬停说明、关掉以后、恢复直接截取以后），第一次启动时的
「免费软件」声明（链接、关掉以后不再显示、「关于」窗口最上面的同一句）。推荐日志用假对象代替，个别用例换掉文件位置
（advisor.paths）的函数、诊断模块和「关于」窗口的排法；设置文件写在测试沙箱的临时文件夹里。

用 Qt 的 offscreen 平台，不会弹出窗口。"""
import json
import re
import threading
import unittest
from types import SimpleNamespace
from unittest import mock
from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtWidgets import QLabel  # noqa: E402
from advisor import paths as app_paths  # noqa: E402
from advisor import version as app_version  # noqa: E402
from advisor.data import load_db  # noqa: E402
from advisor.session import GAME_FIELDS  # noqa: E402
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.wording import CAPTURE_SCREEN_TIP, CAPTURE_RESTORED, CAPTURE_NOTICE, CAPTURE_TIP_NO_CODE, CAPTURE_TIP
from advisor.ui.overlay_parts.wording import ABOUT_LINK, FREE_NOTICE_RISK
from advisor.ui.overlay_parts.app import UiSettings, OverlaySession, load_settings
from advisor.ui.services import Services  # noqa: E402
from advisor.ui.settings import settings_texts  # noqa: E402
from advisor.ui.text import capture_detail  # noqa: E402
from tests.support.fakes import FakeLogbook  # noqa: E402
from tests.support.overlay_cases import APP, ERR_SHOWN_DEV, PROJECT, capture_state, expire_flash, plain_html, shown, temp_dir, tip_text


class CaptureNoticeTest(unittest.TestCase):
    """直接截取用不了、改为截取屏幕时「识别」卡片顶部的提示（⚠ 橙色，带「关闭本条提示」，不改边框颜色；一行显示时也显示），
    悬停说明；关掉只管这次运行；恢复直接截取后一次性提示。截取方式写进识别记录。"""

    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.session.game.seen_hero = "凛"
        self.records = []
        self.session.log_state = lambda kind, **data: self.records.append((kind, data))
        self.window = Overlay(self.db, self.session, threading.Lock(), services=Services(logbook=FakeLogbook()))

    def tearDown(self):
        self.window.hide()

    def test_texts_and_tips(self):
        """四种原因的提示和悬停说明。截取状态的约定：detail 只有错误代码，出错的步骤在 step；系统不支持、全黑没有
        错误代码，Windows 拒绝没有错误代码时按 0x80070005 写。出错时的内部步骤名不写到悬停说明里。"""
        w = self.window
        cases = {
            "unsupported": ("", "", CAPTURE_SCREEN_TIP),
            "denied": ("", "", CAPTURE_TIP["denied"].format(code="0x80070005") + "\n" + CAPTURE_SCREEN_TIP),
            "failed": ("0x887A0005", "CreateForWindow",
                       CAPTURE_TIP["failed"].format(code="0x887A0005", err_file=ERR_SHOWN_DEV)
                       + "\n" + CAPTURE_SCREEN_TIP),
            "blank": ("", "", CAPTURE_TIP["blank"] + "\n" + CAPTURE_SCREEN_TIP),
        }
        for problem, (detail, step, tip) in cases.items():
            with self.subTest(problem=problem):
                w.on_capture_changed(capture_state("screen", problem, detail, border_requested=False, step=step))
                self.assertEqual(w.notice_model.texts().get("capture"), CAPTURE_NOTICE[problem])
                self.assertTrue(CAPTURE_NOTICE[problem].startswith("⚠ "))
                self.assertTrue(CAPTURE_NOTICE[problem].endswith("已改为截取屏幕：游戏窗口不在前台时暂停识别。"))
                self.assertNotIn("截图工具截不到悬浮窗", CAPTURE_NOTICE[problem])
                label = next(lb for lb in w.recog_top.findChildren(QLabel) if lb.text() == CAPTURE_NOTICE[problem])
                self.assertEqual(label.objectName(), "notice_warn")
                self.assertEqual(tip_text(label.toolTip()), tip)
                self.assertEqual([b.text() for b in w.notices.buttons("capture")], ["关闭本条提示"])
                # 一行显示时「识别」卡片也出现，只显示这一行；不是「需要您操作」，边框不变色
                self.assertFalse(w.expanded)
                self.assertTrue(w.card_open("recog"))
                self.assertFalse(w.needs_attention())
        self.assertEqual(self.records[-1], ("capture", {"method": "screen", "problem": "blank", "detail": "", "step": "",
                                                         "build": 26200, "border_requested": False}))
        failed = next(d for k, d in self.records if k == "capture" and d["problem"] == "failed")
        self.assertEqual((failed["detail"], failed["step"]), ("0x887A0005", "CreateForWindow"))
        self.assertIn("错误代码 0x887A0005。", cases["failed"][2])
        self.assertNotIn("CreateForWindow", cases["failed"][2])  # 内部步骤名不写到界面上
        self.assertIn("详细信息已写入错误记录（.tmp\\overlay_err.txt）。", cases["failed"][2])  # 开发版的写法不变

    def test_failed_without_code(self):
        """「出错」没有系统错误代码（建好的会话 10 秒一帧都没有时步骤为 TryGetNextFrame；程序内部出错时步骤是错误的
        类名）：悬停说明开头换成 CAPTURE_TIP_NO_CODE，不写错误代码，也不写出错的步骤。提示本身不变，
        设置窗口的原因不带错误代码。"""
        w = self.window

        def notice_label() -> QLabel:
            # 同一条提示换了悬停说明时整行重建，换下来的那一行隐藏后等待删除：只取显示着的
            found = [lb for lb in w.recog_top.findChildren(QLabel)
                     if lb.text() == CAPTURE_NOTICE["failed"] and lb.isVisibleTo(w.recog_top)]
            self.assertEqual(len(found), 1)
            return found[0]

        for step in ("TryGetNextFrame", "AttributeError", ""):
            with self.subTest(step=step):
                state = capture_state("screen", "failed", "", border_requested=False, step=step)
                w.on_capture_changed(state)
                self.assertEqual(w.notice_model.texts()["capture"], CAPTURE_NOTICE["failed"])
                tip = tip_text(notice_label().toolTip())
                self.assertEqual(tip, CAPTURE_TIP_NO_CODE.format(err_file=ERR_SHOWN_DEV) + "\n" + CAPTURE_SCREEN_TIP)
                self.assertTrue(tip.startswith("详细信息已写入错误记录"), tip)
                self.assertNotIn("错误代码", tip)
                if step:
                    self.assertNotIn(step, tip)  # 内部步骤名不写到界面上
                body = settings_texts(state)[1]
                self.assertTrue(body.startswith("原因：直接截取游戏窗口出错。"), body)
                self.assertNotIn("错误代码", body)
                capture = [d for k, d in self.records if k == "capture"][-1]
                self.assertEqual((capture["problem"], capture["detail"], capture["step"]), ("failed", "", step))
        # 有错误代码时照「出错」那一句的开头写，不写步骤
        w.on_capture_changed(capture_state("screen", "failed", "0x80004005", border_requested=False))
        self.assertEqual(tip_text(notice_label().toolTip()),
                         CAPTURE_TIP["failed"].format(code="0x80004005", err_file=ERR_SHOWN_DEV)
                         + "\n" + CAPTURE_SCREEN_TIP)

    def test_error_record_in_release(self):
        """错误记录的位置按用户看到的程序文件夹写（paths.shown）：发行版是「app\\.tmp\\overlay_err.txt」——截取方式提示的悬停
        说明（有、没有错误代码两种）和导出诊断失败的提示；开发版照旧「.tmp\\overlay_err.txt」（上面几条）。模拟发行版只换
        paths.shown，不打包。"""
        w = self.window
        release = "app\\.tmp\\overlay_err.txt"

        def notice_tip() -> str:
            found = [lb for lb in w.recog_top.findChildren(QLabel)
                     if lb.text() == CAPTURE_NOTICE["failed"] and lb.isVisibleTo(w.recog_top)]
            self.assertEqual(len(found), 1)
            return tip_text(found[0].toolTip())

        with mock.patch.object(app_paths, "shown", lambda rel: f"app\\{rel}"):
            w.on_capture_changed(capture_state("screen", "failed", "0x887A0005", border_requested=False,
                                               step="CreateForWindow"))
            self.assertEqual(notice_tip(), CAPTURE_TIP["failed"].format(code="0x887A0005",
                                                                       err_file=release) + "\n" + CAPTURE_SCREEN_TIP)
            w.on_capture_changed(capture_state("screen", "failed", "", border_requested=False, step="TryGetNextFrame"))
            self.assertEqual(notice_tip(), CAPTURE_TIP_NO_CODE.format(err_file=release)
                             + "\n" + CAPTURE_SCREEN_TIP)
            w._export_done(None, "")
            self.assertEqual(w.message.text, f"诊断包导出失败，详细信息已写入错误记录（{release}）")
        w._export_done(None, "")
        self.assertEqual(w.message.text, "诊断包导出失败，详细信息已写入错误记录（.tmp\\overlay_err.txt）")

    def test_order_with_midgame_newgame(self):
        """排在对局中途判定的新一局之后（都在「识别」卡片最上面）。"""
        w, s = self.window, self.session
        s.set_owned(["书签"])
        w.handle(s._auto_new_game("round", 1, "轮次由第 8 轮变为第 1 轮"))
        w.on_capture_changed(capture_state("screen", "failed", "0x80004005"))
        rows = [w.recog_top_box.itemAt(i).widget().objectName() for i in range(w.recog_top_box.count())]
        self.assertEqual(rows, ["notice-newgame", "notice-capture"])

    def test_dismiss_and_restore(self):
        """「关闭本条提示」只管这次运行：同一原因不再提示，换了原因照常提示。恢复直接截取后提示收起，并一次性提示
        CAPTURE_RESTORED（「识别」卡片，约 15 秒后收起）；之后再改为截取屏幕时照常提示。"""
        w = self.window
        w.on_capture_changed(capture_state("screen", "denied"))
        w.notices.buttons("capture")[0].click()
        self.assertNotIn("capture", w.notice_model.texts())
        w.on_capture_changed(capture_state("screen", "denied", "0x80070005"))  # 细节变了、原因相同
        self.assertNotIn("capture", w.notice_model.texts())
        w.on_capture_changed(capture_state("screen", "failed", "0x80004005"))
        self.assertEqual(w.notice_model.texts()["capture"], CAPTURE_NOTICE["failed"])
        w.on_capture_changed(capture_state("window"))
        self.assertNotIn("capture", w.notice_model.texts())
        self.assertEqual((w.message.text, w.message.area), (CAPTURE_RESTORED, "recog"))
        self.assertTrue(w.status.summary_plain.startswith("已恢复直接截取游戏窗口"))
        expire_flash(w)
        self.assertNotEqual(w.message.text, CAPTURE_RESTORED)
        w.on_capture_changed(capture_state("screen", "denied"))
        self.assertEqual(w.notice_model.texts()["capture"], CAPTURE_NOTICE["denied"])  # 关掉的那次只管当时

    def test_no_restore_notice_at_startup(self):
        """启动后第一次收到截取状态、或一直是直接截取：不提示「已恢复」；系统不支持时一开始就提示。"""
        w = self.window
        w.on_capture_changed(capture_state("window"))
        self.assertNotEqual(w.message.text, CAPTURE_RESTORED)
        self.assertNotIn("capture", w.notice_model.texts())
        other = Overlay(self.db, self.session, threading.Lock())
        try:
            other.set_expected_capture("screen")  # 按系统版本预判：不支持直接截取
            self.assertEqual(other.capture_method, "screen")
            other.on_capture_changed(capture_state("screen", "unsupported", build=18363))
            self.assertEqual(other.notice_model.texts()["capture"], CAPTURE_NOTICE["unsupported"])
            self.assertIn("需要 Windows 10 版本 2004 或更高版本", CAPTURE_NOTICE["unsupported"])
            self.assertNotEqual(other.message.text, CAPTURE_RESTORED)
        finally:
            other.hide()

    def test_detail_parsing(self):
        """截取状态的 detail 统一成错误代码的写法（0x 加 8 位大写）。按约定 detail 只有错误代码；旧写法（错误代码和
        步骤写在一起、十进制）也认，步骤拆出来备用（界面上以 CaptureState.step 为准）。"""
        self.assertEqual(capture_detail("0x80070005"), ("0x80070005", ""))
        self.assertEqual(capture_detail("0x887a0005"), ("0x887A0005", ""))
        self.assertEqual(capture_detail("CreateForWindow 0x80070005"), ("0x80070005", "CreateForWindow"))
        self.assertEqual(capture_detail("0x887a0005（步骤：GetBuffer）"), ("0x887A0005", "GetBuffer"))
        self.assertEqual(capture_detail("hr=-2147024891 step=CreateForWindow"), ("0x80070005", "CreateForWindow"))
        self.assertEqual(capture_detail(""), ("", ""))
        self.assertEqual(capture_detail(None), ("", ""))


def unpadded(text: str) -> str:
    """网址链接两边的空白（text.linkify 在网址前后各加一个不换行空格；转成纯文本时变成普通空格）去掉以后的文字：和
    version.FREE_NOTICE 逐字比较用。"""
    return re.sub(r" (https?://[^\s]+) ", r"\1", text.replace("\xa0", " "))


class FreeNoticeTest(unittest.TestCase):
    """第一次启动时的「免费软件」声明（version.FREE_NOTICE）后面接一句账号风险的提醒（wording.FREE_NOTICE_RISK）：main()
    启动时调用 check_free_notice，设置里还没关过就显示在标题栏下方（全局提示，排在全局提示的最后），一行显示、全部展开都显示；
    还是一条提示、一个「关闭本条提示」；是说明，边框不变色；网址做成能点的链接（交给系统默认浏览器打开），「关于」也是链接
    （打开「关于」窗口、停在免责声明那一节）；点「关闭本条提示」后记进设置文件，以后启动不再显示，删掉设置文件会再显示一次；
    开发版、发行版都显示；测试、预览不调用 check_free_notice，不显示。「关于」窗口最上面照旧只写声明那一句（每种排法），
    逐字相同。设置文件写在项目 .tmp 下的临时文件夹里。"""

    URL = "https://github.com/s1nan-dev/astral-chip-compass"
    TEXT = app_version.FREE_NOTICE + FREE_NOTICE_RISK  # 悬浮窗上那一条

    def setUp(self):
        self.db = load_db()
        self.path = temp_dir(self) / "state.json"
        self.services = Services(logbook=FakeLogbook(), version=app_version)

    def make(self, settings=None, services=None, dev_label=None) -> Overlay:
        session = OverlaySession(self.db, settings if settings is not None else UiSettings(hero="凛"), self.path)
        w = Overlay(self.db, session, threading.Lock(), services=services or self.services, dev_label=dev_label)
        w.run_background = lambda fn: fn()
        self.addCleanup(w.hide)
        return w

    @staticmethod
    def global_keys(w) -> list[str]:
        """标题栏下方全局提示的各行（键），按显示的先后。"""
        box = w.global_notice_box
        return [box.itemAt(i).widget().objectName().removeprefix("notice-") for i in range(box.count())]

    @staticmethod
    def notice_label(w, key: str = "free") -> QLabel:
        box = w.global_notice_box
        row = next(box.itemAt(i).widget() for i in range(box.count())
                   if box.itemAt(i).widget().objectName() == f"notice-{key}")
        return next(x for x in row.findChildren(QLabel) if x.objectName().startswith("notice_"))

    def test_shown_once_checked(self):
        """check_free_notice 以后显示（一行显示时也在），文字逐字照 FREE_NOTICE、后面接账号风险那一句；不调用时（测试、预览、
        画皮肤缩略图的子进程）不显示。只作说明：级别 info、边框不变色，不让「需要您操作」。"""
        self.assertEqual(app_version.FREE_NOTICE,
                         "拾星助手是免费的开源软件（AGPL-3.0）。下载镜像：https://dl.astral-chip-compass.com/ACC-latest.zip ；"
                         f"源代码和发布页面：{self.URL} 。付费获得的版本均非官方发布。")
        self.assertEqual(FREE_NOTICE_RISK, "使用第三方工具可能违反游戏的用户协议、导致账号被处罚，详见「关于」窗口里的免责声明。")
        w = self.make()
        self.assertNotIn("free", w.notice_model.texts())
        w.notices.check_free_notice()
        self.assertEqual(w.notice_model.texts()["free"], self.TEXT)
        self.assertEqual(self.global_keys(w), ["free"])
        self.assertEqual([b.text() for b in w.notices.buttons("free")], ["关闭本条提示"])
        self.assertEqual(tip_text(w.notices.buttons("free")[0].toolTip()), "关闭本条提示")
        w.show()
        APP.processEvents()
        self.assertFalse(w._expanded)  # 一行显示：全局提示照样在
        self.assertTrue(w.global_notices.isVisibleTo(w.panel))
        self.assertIn(self.TEXT, unpadded(shown(w, w.global_area)))
        self.assertFalse(w.needs_attention())
        self.assertFalse(bool(w.panel.property("attention")))
        self.assertEqual(self.notice_label(w).objectName(), "notice_info")
        w.set_expanded(True)
        self.assertIn(self.TEXT, unpadded(shown(w, w.global_area)))
        w.refresh()
        self.assertEqual(w.notice_model.texts()["free"], self.TEXT)  # 不关就一直在
        w.notices.check_free_notice()  # 再查一次也只有一条
        self.assertEqual(self.global_keys(w), ["free"])
        self.assertEqual(unpadded(shown(w)).count(app_version.FREE_NOTICE), 1)

    def test_link_clickable(self):
        """网址做成链接（带底色，同「关于」窗口），点了交给系统默认浏览器；账号风险那一句里的「关于」也做成同样的链接，点了
        打开「关于」窗口、停在免责声明那一节（不交给浏览器）。显示以后照旧能点（悬浮窗按在文字上拖动的处理不动自己处理链接的
        标签）。其余没有链接的提示行照旧是纯文字、不接鼠标。"""
        from PySide6.QtGui import QDesktopServices

        from advisor.ui.text import link_style, linkify

        w = self.make()
        w.notices.check_free_notice()
        fallback = SimpleNamespace(kind="unwritable", detail="PermissionError [WinError 5]")
        with mock.patch.object(app_paths, "storage_fallback", return_value=fallback, create=True):
            w.refresh()
            w.show()
            APP.processEvents()
            label = self.notice_label(w)
            self.assertEqual(label.textFormat(), Qt.RichText)
            about = f'<a href="{ABOUT_LINK}" style="{link_style()}">「关于」</a>'
            self.assertEqual(label.text(), linkify(self.TEXT).replace("「关于」", about))  # 由同一句转成，字一个不改
            self.assertIn(f'<a href="{self.URL}" style="{link_style()}">', label.text())
            self.assertFalse(label.openExternalLinks())  # 链接由提示行自己接（open_link）
            self.assertTrue(label.textInteractionFlags() & Qt.LinksAccessibleByMouse)
            self.assertEqual(unpadded(plain_html(label.text())), self.TEXT)
            other = self.notice_label(w, "storage")
            self.assertFalse(other.openExternalLinks())
            self.assertEqual(other.textInteractionFlags(), Qt.NoTextInteraction)
            with mock.patch.object(QDesktopServices, "openUrl", return_value=True) as open_url:
                label.linkActivated.emit(self.URL)
                self.assertEqual([c.args[0].toString() for c in open_url.call_args_list], [self.URL])
                label.linkActivated.emit(ABOUT_LINK)
                open_url.assert_called_once()  # 「关于」不交给浏览器
        box = w.dialogs.about
        self.addCleanup(box.close)
        self.assertTrue(box.isVisible())
        self.assertEqual(box._section, "免责声明")
        w.hide()

    def test_last_in_global_area(self):
        """排在全局提示的最后：存不进个人目录、方案文件读坏、图片下载、校准、诊断包位置都在它前面。"""
        w = self.make()
        w.notices.check_free_notice()
        fallback = SimpleNamespace(kind="unwritable", detail="PermissionError [WinError 5]")
        with mock.patch.object(app_paths, "storage_fallback", return_value=fallback, create=True):
            w.background.assets_state = ("downloading", 3, 10, "a.png")
            w.notice_model.calibrate_n = 25
            w.notice_model.export_path = str(PROJECT / "reports" / "20261008-120000")
            w.refresh()
            self.assertEqual(self.global_keys(w), ["storage", "assets", "calibrate", "export", "free"])
            w.notices.buttons("storage")[0].click()
            self.assertEqual(self.global_keys(w), ["assets", "calibrate", "export", "free"])

    def test_closed_for_good(self):
        """点「关闭本条提示」：马上收起，记进设置文件（free_notice_closed），识别记录记一条用户操作；之后刷新、下次启动都不再
        显示。删掉设置文件后启动（新用户）会再显示一次。"""
        w = self.make()
        w.notices.check_free_notice()
        w.notices.buttons("free")[0].click()
        self.assertNotIn("free", w.notice_model.texts())
        self.assertEqual(self.global_keys(w), [])
        self.assertIs(w.session.settings.free_notice_closed, True)
        self.assertIs(json.loads(self.path.read_text(encoding="utf-8"))["free_notice_closed"], True)
        records = [e for e in w.session.recent_events(600) if e.get("kind") == "read:user"]
        self.assertEqual([e.get("action") for e in records], ["dismiss_free"])
        w.refresh()
        self.assertNotIn("free", w.notice_model.texts())
        again = self.make(settings=load_settings(self.path))  # 下次启动
        again.notices.check_free_notice()
        self.assertNotIn("free", again.notice_model.texts())
        self.path.unlink()  # 删掉设置文件
        fresh = self.make(settings=load_settings(self.path))
        fresh.notices.check_free_notice()
        self.assertEqual(fresh.notice_model.texts()["free"], self.TEXT)

    def test_dev_and_release(self):
        """开发版、发行版都显示（不按 is_frozen 区分）；设置文件里别的项不影响。"""
        for frozen in (False, True, None):
            with self.subTest(frozen=frozen):
                diagnostics = SimpleNamespace() if frozen is None else SimpleNamespace(is_frozen=lambda f=frozen: f)
                w = self.make(services=Services(logbook=FakeLogbook(), diagnostics=diagnostics, version=app_version))
                self.assertEqual(w.dev_label, frozen is False)
                w.notices.check_free_notice()
                self.assertEqual(w.notice_model.texts()["free"], self.TEXT)
        w = self.make(settings=UiSettings(hero="芬妮", manual=True, opaque=True, free_notice_closed=False))
        w.notices.check_free_notice()
        self.assertIn("free", w.notice_model.texts())

    def test_setting_values(self):
        """设置文件里的 free_notice_closed：只有 true 算关过；不是 true / false 的值（文字、数字、null）按没关过，不报错；
        没有这一项（新用户、旧的设置文件）按没关过。"""
        for raw, want in (({"free_notice_closed": True}, True), ({"free_notice_closed": False}, False),
                          ({"free_notice_closed": "true"}, False), ({"free_notice_closed": 1}, False),
                          ({"free_notice_closed": None}, False), ({"hero": "芬妮"}, False)):
            with self.subTest(raw=raw):
                self.path.write_text(json.dumps(raw), encoding="utf-8")
                settings = load_settings(self.path)
                self.assertIs(settings.free_notice_closed, want)
                w = self.make(settings=settings)
                w.notices.check_free_notice()
                self.assertEqual("free" in w.notice_model.texts(), not want)
        self.assertNotIn("free_notice_closed", GAME_FIELDS)  # 跨对局、跨启动的设置，不是对局状态

    def test_without_version_module(self):
        """没有版本模块（导入出错）时不显示，也不出错。"""
        w = self.make(services=Services(logbook=FakeLogbook()))
        w.notices.check_free_notice()
        self.assertNotIn("free", w.notice_model.texts())

    def test_about_window_top(self):
        """从悬浮窗打开「关于」窗口（默认皮肤）：最上面一段就是声明那一句（链接可点，不接账号风险那一句：免责声明一节里有），
        下面才是「版本」一节。"""
        w = self.make()
        w.notices.check_free_notice()
        w.show_about()
        box = w.dialogs.about
        self.addCleanup(box.close)
        block = box.browser.document().begin()
        self.assertTrue(w.notice_model.texts()["free"].startswith(unpadded(block.text())))
        self.assertEqual(unpadded(block.text()), app_version.FREE_NOTICE)
        self.assertNotIn(FREE_NOTICE_RISK, unpadded(box.text()))
        self.assertGreater(block.next().blockFormat().headingLevel(), 0)
        self.assertEqual(block.next().text().strip(), "版本")
        self.assertTrue(unpadded(box.text()).startswith(app_version.FREE_NOTICE))
        self.assertIn(f'href="{self.URL}"', box.browser.toHtml())
        self.assertTrue(box.browser.openExternalLinks())

    def test_about_every_layout(self):
        """「关于」窗口的每种排法（各套皮肤用的排法不同）都把这一句放在最上面，逐字相同、链接可点：正文在文字框里的排法是正文
        第一段（紧接着是「版本」的小标题）；accordion 写在版本卡上面（不进折叠的各节）。排法按名字逐个建（AboutDialog.layout_name
        换成这一种），不换皮肤：换皮肤要装皮肤的字体，会让这个文件里后面量文字宽度的测试量出不同的结果。不显示窗口。"""
        from advisor.ui import about, theme
        from PySide6.QtWidgets import QLayout

        self.assertEqual({skin.layouts.ABOUT for skin in theme.skins()}, set(about.LAYOUTS))  # 七套皮肤用到全部七种排法
        sections = app_version.about_sections()
        seen = set()
        for key in about.LAYOUTS:
            with self.subTest(layout=key):
                with mock.patch.object(about.AboutDialog, "layout_name", staticmethod(lambda k=key: k)):
                    box = about.AboutDialog(sections)
                try:
                    seen.add(box.layout_key)
                    self.assertTrue(unpadded(box.text()).startswith(app_version.FREE_NOTICE), box.layout_key)
                    self.assertEqual(unpadded(box.text()).count(app_version.FREE_NOTICE), 1)  # 只写一次
                    if box.layout_key == "accordion":
                        self.assertEqual(box._lead_texts, [app_version.FREE_NOTICE])
                        lead = box.findChild(QLabel, "aboutLead")
                        self.assertEqual(unpadded(plain_html(lead.text())), app_version.FREE_NOTICE)
                        self.assertIn(f'href="{self.URL}"', lead.text())
                        self.assertTrue(lead.openExternalLinks())
                        self.assertNotIn("", box.folds)
                        holder = next(lay for lay in box.findChildren(QLayout)
                                      if lay.indexOf(lead) >= 0 and lay.indexOf(box.version_card) >= 0)
                        self.assertLess(holder.indexOf(lead), holder.indexOf(box.version_card))
                    else:
                        block = box.browser.document().begin()
                        self.assertEqual(unpadded(block.text()), app_version.FREE_NOTICE)
                        self.assertGreater(block.next().blockFormat().headingLevel(), 0)
                        self.assertIn(f'href="{self.URL}"', box.browser.toHtml())
                        self.assertTrue(box.browser.openExternalLinks())
                    if box.layout_key == "toc":
                        self.assertEqual(list(box.toc), [t for t, _ in sections if t])  # 目录里没有这一段
                finally:
                    box.close()
                    box.deleteLater()
                    APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(seen, set(about.LAYOUTS))  # 七种排法都查到了


if __name__ == "__main__":
    unittest.main()
