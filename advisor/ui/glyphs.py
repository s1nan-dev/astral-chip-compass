"""系统图标（Windows 自带的 Segoe Fluent Icons，Windows 10 上是 Segoe MDL2 Assets，编号相同）的画法：找字体、画一个图标、
把图标做成按钮前面的小图标。另有一个自己用路径画的图标：「作业站」按钮的书（ICON_BOOK），不靠图标字体。

设置窗口各行的图标、皮肤窗口的说明图标、悬浮窗「筹码方案」卡片的按钮都用这些画法。放这里是为了让底层的部件（chrome）借用
图标画法时不必反过来导入设置窗口；settings/ 也再导出这些名字，别处和测试从 settings 取也一样。测试要替换（patch）
时换这里的名字（例如 glyphs.icon_family）：settings 里的只是指向同一个函数的另一个名字，换掉它，这里的画法照样用这里的。

只依赖 Qt 和底层的装饰模块（色号解析 decor.qc）：不导入设置、悬浮窗这些上层窗口。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPainterPathStroker, QPen,
                           QPixmap, QTransform)

from .decor import qc as color

ICON_FAMILIES = ("Segoe Fluent Icons", "Segoe MDL2 Assets")  # 按顺序找第一个系统里有的
ICON_SETTINGS = 0xE713  # 齿轮：窗口顶部大标题旁
ICON_MODE = 0xE890  # 眼睛：识别模式
ICON_PLATFORM = 0xE7FC  # 游戏手柄：游戏平台（玩哪个客户端）
ICON_HOTKEY = 0xE765  # 键盘：识别热键
ICON_CAPTURE = 0xE7F4  # 显示器：识别画面
ICON_LOWEND = 0xE8BE  # 叶子：低配模式（Windows 任务管理器的「效率模式」也用叶子表示少占处理器）
ICON_BIG_CORES = 0xEC4A  # 速度表（指针在高速）：只用大核
ICON_SKIN = 0xE771  # 画笔：皮肤
ICON_LOOK = 0xE790  # 调色板：悬浮窗背景
ICON_LANG = 0xE8C1  # 「A」和「字」并排（字符）：界面语言
ICON_DIAG = 0xE9D9  # 诊断：诊断包
ICON_UPDATE = 0xE895  # 两个转圈的箭头（Windows 设置里「Windows 更新」也用它）：启动时自动检查更新
ICON_ABOUT = 0xE946  # 信息：关于拾星助手
ICON_CHEVRON = 0xE76C  # 向右的箭头：整行可点的一行右端

# 「作业站」按钮的图标：正面看翻开的书。系统图标字体里没有这个样子，用路径自己画（不靠图标字体：系统里没有图标字体时
# 也画得出来）。坐标按宽 48、高 36 的格子写，左右对称，只写右半边：书页又高又窄，上边微微拱起，靠书脊的上角拐成圆角；
# 书页外侧一道侧边书页、再外面一道封面，显出书的厚度；书页下面一道厚边，再下面是封面，封面在书脊处往下折一点。
# 先画右半边整块的外形，再沿几条中线挖出一样宽的白缝，最后左右镜像合成一个实心路径
ICON_BOOK = "book"  # 自己画的图标用名字、不用编号，免得和图标字体里的编号混
# 书的高度是图标大小 px 的几倍（宽是高的 4/3）：px 为 14 时书正好高 12、宽 16 像素。实心的书比线条的系统图标显得重，
# 画得比图标格子矮一点
BOOK_HEIGHT = 6 / 7
_BOOK_W = 48.0
_BOOK_GAP = 2.25  # 白缝的宽度（书脊缝、各块之间的缝一样宽）：书高 12 像素时约 0.75 像素，再细就和两边糊在一起
_BOOK_PAGE_OUT = 38.7  # 右页外边的 x（右页里边是书脊缝的边）
_BOOK_STRIP = (40.95, 43.35)  # 侧边书页里、外两边的 x
_BOOK_COVER_IN = 45.6  # 封面里边的 x（封面外边是格子的右边）
# 右页上边：书脊一端的高度；拐上来成圆角到最高处、再缓缓落到外上角的两段三次曲线（控制点、控制点、终点）；
# 外上角小圆角的控制点高度、落到外边上的高度
_BOOK_TOP = (4.88, ((25.173, 3.87), (25.793, 0.0), (30.681, 0.0)), ((33.636, 0.0), (36.848, 1.09), (38.338, 1.65)),
             (1.78, 2.78))
_BOOK_STRIP_TOP = (4.3, 7.82)  # 侧边书页上沿：里、外两端的高度（往外斜下去）
_BOOK_COVER_TOP = (5.81, (47.4, 6.28), (6.51, 6.96))  # 封面上沿：里端的高度；外上角圆角起头的点；圆角的控制点高度、终点高度
# 封面下边：外下角；往书脊缓缓抬起的曲线；在书脊处往下折一点的曲线（终点在书脊上）
_BOOK_BOTTOM = ((47.76, 36.0), ((45.4, 35.58), (32.25, 33.07), (28.3, 33.07)),
                ((27.29, 33.07), (25.52, 34.22), (24.0, 34.22)))
# 书页和厚边之间那道缝的中线：外端的高度（外端在书页外侧那道竖缝上）；往里拱起的曲线；到书脊前往下钻的曲线；再伸进书脊缝
_BOOK_SEAM_PAGE = (25.765, ((34.661, 24.055), (32.154, 23.905), (32.129, 23.905)),
                   ((31.901, 23.905), (26.492, 23.065), (24.787, 27.315)), (23.7, 29.707))
# 厚边和封面之间那道缝的中线：外端的高度（外端是封面 U 形的里角，在封面里侧那道竖缝上）；往书脊缓缓抬起的曲线；
# 在书脊前浅浅凹下去、伸进书脊缝的曲线。书脊缝挖到这道缝为止，封面在书脊处连着
_BOOK_SEAM_COVER = (32.345, ((34.856, 29.745), (27.625, 29.325), (27.625, 29.325)),
                    ((26.247, 29.325), (25.377, 30.495), (23.7, 30.495)))
_BOOK_ZOOM = 64.0  # 相减、合并之前把路径放大的倍数：Qt 相减时按固定的坐标容差把曲线切成折线，格子这么小时放大看得出棱角
_book_path: QPainterPath | None = None


def book_path() -> QPainterPath:
    """书的轮廓（格子坐标）：右半边整块的外形挖掉白缝，再左右镜像合成一个路径，第一次用到时算一次。"""
    global _book_path
    if _book_path is None:

        def _book_curve(path: QPainterPath, points) -> None:  # 一段三次曲线：控制点、控制点、终点
            path.cubicTo(*(QPointF(*pt) for pt in points))

        mid = _BOOK_W / 2
        half = _BOOK_GAP / 2
        seam_page = _BOOK_PAGE_OUT + half  # 书页和侧边书页之间那道竖缝的中线
        seam_cover = _BOOK_STRIP[1] + half  # 侧边书页和封面之间那道竖缝的中线
        spine_y, top_in, top_out, (corner_c, corner_y) = _BOOK_TOP
        strip_in, strip_out = _BOOK_STRIP_TOP
        cover_y, cover_corner, (cover_c, cover_end) = _BOOK_COVER_TOP
        bottom_corner, bottom_rise, bottom_fold = _BOOK_BOTTOM
        # 右半边整块的外形：从书脊顶上顺时针一圈；竖缝里那几段随便走，挖缝时会去掉；书脊这边多出一点，镜像以后两半叠上
        outline = QPainterPath(QPointF(mid - 0.2, spine_y))
        outline.lineTo(mid + half, spine_y)
        _book_curve(outline, top_in)
        _book_curve(outline, top_out)
        outline.quadTo(QPointF(_BOOK_PAGE_OUT, corner_c), QPointF(_BOOK_PAGE_OUT, corner_y))
        outline.lineTo(seam_page, corner_y)
        outline.lineTo(seam_page, strip_in)
        outline.lineTo(_BOOK_STRIP[0], strip_in)
        outline.lineTo(_BOOK_STRIP[1], strip_out)
        outline.lineTo(seam_cover, strip_out)
        outline.lineTo(seam_cover, cover_y)
        outline.lineTo(_BOOK_COVER_IN, cover_y)
        outline.lineTo(*cover_corner)
        outline.quadTo(QPointF(_BOOK_W, cover_c), QPointF(_BOOK_W, cover_end))
        outline.lineTo(_BOOK_W, bottom_corner[1] - 0.25)
        outline.quadTo(QPointF(_BOOK_W, bottom_corner[1]), QPointF(*bottom_corner))
        _book_curve(outline, bottom_rise)
        _book_curve(outline, bottom_fold)
        outline.lineTo(mid - 0.2, bottom_fold[2][1])
        outline.closeSubpath()
        # 挖白缝的中线
        page_y, page_rise, page_dive, page_tail = _BOOK_SEAM_PAGE
        cover_seam_y, cover_rise, cover_dip = _BOOK_SEAM_COVER
        joint = QPointF(seam_page, page_y)  # 书页外下角旁：竖缝、斜缝和书页下那道缝交在这里
        corner = QPointF(seam_cover, cover_seam_y)  # 封面 U 形的里角：竖缝、斜缝和封面上那道缝交在这里
        cuts = QPainterPath(QPointF(mid, -2))  # 书脊缝
        cuts.lineTo(mid, cover_dip[2][1])
        cuts.moveTo(seam_page, -2)  # 书页外侧的竖缝，到底拐成斜缝：侧边书页的下端和厚边的外端斜着错开
        cuts.lineTo(joint)
        cuts.lineTo(corner)
        cuts.moveTo(seam_cover, -2)  # 封面里侧的竖缝，到底接着拐进厚边和封面之间那道缝（连成一笔，拐角才是实的）
        cuts.lineTo(corner)
        _book_curve(cuts, cover_rise)
        _book_curve(cuts, cover_dip)
        cuts.moveTo(joint)  # 书页和厚边之间那道缝
        _book_curve(cuts, page_rise)
        _book_curve(cuts, page_dive)
        cuts.lineTo(*page_tail)
        zoom = QTransform.fromScale(_BOOK_ZOOM, _BOOK_ZOOM)
        stroker = QPainterPathStroker(QPen(Qt.black, _BOOK_GAP * _BOOK_ZOOM, Qt.SolidLine, Qt.FlatCap, Qt.MiterJoin))
        stroker.setCurveThreshold(0.02 * _BOOK_ZOOM)  # 缝边切成折线的容差 0.02 格：书高 24 像素时约 0.01 像素，看不出棱角
        right = zoom.map(outline).subtracted(stroker.createStroke(zoom.map(cuts)))
        both = right.united(QTransform(-1, 0, 0, 1, _BOOK_W * _BOOK_ZOOM, 0).map(right))
        _book_path = QTransform.fromScale(1 / _BOOK_ZOOM, 1 / _BOOK_ZOOM).map(both)
    return _book_path


def glyph_width(code: int | str, px: int) -> int:
    """图标占的宽度：图标字体里的图标 px 见方；自己画的书高 px * BOOK_HEIGHT，宽按书的宽高比。"""
    if code != ICON_BOOK:
        return px
    box = book_path().boundingRect()
    return round(px * BOOK_HEIGHT * box.width() / box.height())


def _draw_book(painter: QPainter, rect: QRectF, px: int, fill: QColor) -> None:
    """在 rect 里居中画书：高 px * BOOK_HEIGHT（rect 放不下时按 rect 缩小），宽按书的宽高比。书的左上角挪到最近的整个设备
    像素上：照格子中心摆的话，格子的宽或高是奇数、或者屏幕缩放不是整数倍（125%、150%）时会落在半个像素上，竖边、横边都发软。"""
    path = book_path()
    box = path.boundingRect()
    scale = min(px * BOOK_HEIGHT / box.height(), rect.width() / box.width(), rect.height() / box.height())
    corner = QPointF(rect.center().x() - box.width() * scale / 2, rect.center().y() - box.height() * scale / 2)
    device = painter.deviceTransform()  # 当前坐标 → 设备像素（含屏幕缩放）
    back, invertible = device.inverted()
    if invertible:
        at = device.map(corner)
        corner = back.map(QPointF(round(at.x()), round(at.y())))
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.translate(corner.x() - box.left() * scale, corner.y() - box.top() * scale)
    painter.scale(scale, scale)
    painter.setPen(Qt.NoPen)
    painter.setBrush(fill)
    painter.drawPath(path)
    painter.restore()


_icon_family: str | None = None
_icon_checked = False


def icon_family() -> str | None:
    """系统里的图标字体（ICON_FAMILIES 里第一个有的）；都没有时为 None。第一次用到时查一次（要先有 QApplication）。"""
    global _icon_family, _icon_checked
    if not _icon_checked:
        families = set(QFontDatabase.families())
        _icon_family = next((f for f in ICON_FAMILIES if f in families), None)
        _icon_checked = True
    return _icon_family


def glyph_font(px: int) -> QFont | None:
    """画系统图标用的字体（像素大小 px）；系统里没有图标字体时为 None（调用的地方不画图标、只写字）。不用别的字体顶替：
    图标的编号在别的字体里没有对应的字。"""
    family = icon_family()
    if family is None:
        return None
    font = QFont(family)
    font.setPixelSize(px)
    font.setStyleStrategy(QFont.NoFontMerging)
    return font


def draw_glyph(painter: QPainter, rect: QRectF, code: int | str, px: int, fill: str | QColor) -> bool:
    """在 rect 里居中画图标 code（像素大小 px，颜色 fill）：系统图标字体的编号，或自己画的 ICON_BOOK。没有图标字体时
    系统图标不画（返回 False），自己画的书照样画（不靠图标字体）。"""
    if code == ICON_BOOK:
        _draw_book(painter, rect, px, fill if isinstance(fill, QColor) else color(fill))
        return True
    font = glyph_font(px)
    if font is None:
        return False
    painter.save()
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.setFont(font)
    painter.setPen(fill if isinstance(fill, QColor) else color(fill))
    painter.drawText(rect, Qt.AlignCenter, chr(code))
    painter.restore()
    return True


GLYPH_RATIOS = (1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)  # 做按钮图标时按这几种缩放比各画一张


def glyph_icon(code: int, fill: str | QColor, px: int) -> QIcon | None:
    """系统图标 code 做成按钮前面的小图标（px 逻辑像素见方，颜色 fill）：按几种缩放比各画一张（GLYPH_RATIOS），高分屏上
    按实际的像素取，不发虚。系统里没有图标字体时为 None（按钮只写字）。"""
    if glyph_font(px) is None:
        return None
    icon = QIcon()
    for ratio in GLYPH_RATIOS:
        size = round(px * ratio)
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        draw_glyph(p, QRectF(0, 0, size, size), code, size, fill)
        p.end()
        icon.addPixmap(pix)
    return icon
