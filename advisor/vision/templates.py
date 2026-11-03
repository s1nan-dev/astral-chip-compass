"""识别用的图片模板和沿用前几帧结果的缓存，收在一个对象里（VisionAssets）。

这些缓存都在识别线程里读写，只有一个实例（templates.assets，由 FrameProcessor 持有），所以不加锁；
`vision.reset_caches()` / `vision.reset_hints()` 作用在它上面。

两类缓存：
  图片缓存（reload）：读进内存的模板图和图标清单。首次运行下载完图片、重试下载后调用 reload() 重新读取；
    内容只由图片和缩放决定，和先读过哪些图无关。
  沿用缓存（reset_hints）：画面尺寸相同时沿用上次搜到的界面缩放、上次认出自己时用的那套皮肤、上次的面板拟合和认过的
    格子。评测脚本、基准和测试逐张读样本时清掉，让读数只取决于这张图。

各模块经模块名取（templates.assets._bar），不按名字导入实例：测试替换 templates.assets 上的一个名字就能换掉所有调用处。
"""
from __future__ import annotations


class VisionAssets:
    def __init__(self) -> None:
        # ---------------------------------------------------------- 图片缓存
        # stars：横条模板（灰度, 掩码）
        self._bar = None
        # stars：角色编号 → 各皮肤的 Q 版头像（BGRA）
        self._photos: dict = {}
        # stars：(角色编号, 缩放, 是否伸缩) → 每套皮肤各角度各压缩的脸部模板
        self._faces: dict = {}
        # stars：缩放 → 全部角色不转不压的脸部模板（队友用）
        self._still: dict = {}
        # stars：归一化后的角色名 → 角色编号
        self._hero_index = None
        # stars：全部角色编号
        self._hero_ids = None
        # hero：角色照模板
        self._templates = None
        # hero：粗筛缩放 → 缩放后的脸部模板
        self._coarse_cache: dict = {}
        # chip_panel：图标 ID → (灰度, 掩码)，原尺寸
        self._icons = None
        # chip_panel：(图标 ID, 尺寸) → (灰度, 掩码)
        self._icon_cache: dict = {}
        # chip_panel：稀有度 → 参考色相
        self._base_hues = None
        # ---------------------------------------------------------- 沿用缓存
        # stars：(画面宽高, 界面缩放 u)，画面尺寸相同时先试它
        self._last_u = None
        # stars：角色编号 → 上次认出自己时用的是第几套皮肤
        self._last_skin: dict = {}
        # chip_panel：上次的面板拟合（画面尺寸, ox, oy, a）
        self._last_fit = None
        # chip_panel：格子指纹 → SlotReading
        self._slot_cache: dict = {}

    def reload(self) -> None:
        """图片有增减（例如首次运行下载完 Q 版头像、筹码图标）后调用：下次识别时重新读取图片。

        内容只由图片和缩放决定，和先读过哪些图无关；不重新读取时，下载前读到的空清单会一直沿用到程序重启。
        """
        self._bar = self._hero_index = self._hero_ids = None
        self._photos.clear()
        self._faces.clear()
        self._still.clear()
        self._templates = None
        self._coarse_cache.clear()
        self._icons = self._base_hues = None
        self._icon_cache.clear()
        self.reset_hints()

    def reset_hints(self) -> None:
        """清掉沿用前几帧结果的缓存（见 vision.reset_hints）。"""
        self._last_u = None
        self._last_skin.clear()
        self._last_fit = None
        self._slot_cache.clear()


assets = VisionAssets()
