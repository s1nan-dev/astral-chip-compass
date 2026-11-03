"""截图识别。需要 opencv、onnxruntime 和自带的精简文字识别库 rapidocr_lite（模型随发行版，开发时取自原版 rapidocr 包）。"""
from .chip_select import ChipSelect, read_chip_select

try:  # 「查看筹码」面板识别：缺了不影响其余识别（见 pipeline.py）
    from .chip_panel import PanelReading, read_chip_panel
except Exception:  # noqa: BLE001
    PanelReading = read_chip_panel = None

__all__ = ["ChipSelect", "PanelReading", "read_chip_panel", "read_chip_select", "reset_caches", "reset_hints"]


def reset_hints() -> None:
    """清掉识别各模块里沿用前几帧结果的缓存，让每张图的读数只取决于这张图，和先读过哪些图无关。

    只给评测脚本、基准和测试逐张读样本时用；悬浮窗运行时不调用（实际使用时窗口和画面连续，沿用是对的）。清这几处
    （都在 templates.assets 上）：
      _last_u        上次搜到的界面缩放：画面尺寸相同时先试它，配得上就不再粗搜，搜到的缩放可能和从头搜的不同
      _last_skin     上次认出自己时用的那套皮肤：同一个角色先只试这一套，认出的行和把握可能和全试时不同
      _last_fit      上次的面板拟合：画面尺寸相同时先验证它，验证过了就不再找圆，格子位置可能和从头找的不同
      _slot_cache    认过的格子：按缩小的格子图做指纹，指纹相同就沿用那次的图案比对结果；指纹是粗的，
                     别的画面上同一个筹码也会对上，沿用的相关度和认出的筹码来自先读的那张
    识别用的图片缓存（角色照、头像、横条、筹码图标及其缩放后的模板）不清：内容只由图片和缩放决定，和先读过哪些图无关；
    要重新读取图片时用 reset_caches。
    """
    from . import templates

    templates.assets.reset_hints()


def reset_caches() -> None:
    """识别用的图片有增减（首次运行下载完、重试下载后）时调用：各模块下次识别时重新读取图片。

    角色照（hero）、Q 版头像和星数横条（stars）、筹码图标（chip_panel）都缓存在 templates.assets 里；
    不清掉的话，下载前读到的空清单会一直沿用到程序重启。
    """
    from . import templates

    templates.assets.reload()
