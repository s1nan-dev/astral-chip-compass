"""认当前角色：把画面左下角的立绘和 wiki 上各角色各皮肤的「角色照」做比对。

角色照在下载的识别图片的 heroes/UT_Hero_RolePhoto_<角色编号>[_<皮肤>].png（首次运行时从 wiki 下载，见 advisor/assets.py；
文件夹按 paths.download_dir，发行版在个人目录里），和对局里左下角那张立绘出自同一张原画。取角色照中间的脸部，在左下角
区域里按多个缩放找最像的位置。
不同角色的显示比例不一样，所以缩放是搜出来的，不写死。

窗口长宽比不固定：区域和缩放都以「界面单位」（1080p 窗口里的 1 像素，见 chip_select.ui_scale）计，
不按窗口宽度的比例划。立绘贴着画面左下角。

斜着摆的皮肤：个别皮肤在对局里显示的是斜着摆的整张原画，wiki 的角色照却是摆正的，直接比相关度上不去。这几张角色照
记在 data/photo_tilt.json（文件名 → 角度，照游戏内核对），建模板时先把整张角色照绕脸部区域（HERO_FACE）的中心转这么多度
（正数逆时针，同 cv2.getRotationMatrix2D），再取脸部；没列出的角色照照原样取，以后再有斜着摆的皮肤只在表里加一项。

资料库里没有的皮肤（或角色）：用户手动指定角色、而立绘无法识别时，悬浮窗把这一帧左下角的立绘存成新模板
（save_template，存到下载的识别图片的 heroes/user/，同角色照放在一起：发行版在个人目录里，换新版本照样沿用），之后一并比对。
用户模板存的就是对局里的样子，不转。
"""
from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass

import cv2
import numpy as np

from .. import fileio, paths
from ..data import DB
from . import io
from . import templates
from .anchors import HERO_FACE, HERO_REGION_RIGHT, HERO_REGION_UP, HERO_WORK_UNIT, PHOTO_UNITS, USER_BOX
from .chip_select import ui_scale
from .frame import as_frame

COARSE_STEP = 1.09  # 粗筛时相邻两个缩放的比
FINE = (0.955, 0.97, 0.985, 1.0, 1.015, 1.03, 1.045)  # 细调：粗筛结果乘这些倍数
MIN_SCORE = 0.86
MIN_MARGIN = 0.10  # 第一名要比第二名的角色高出这么多

USER_KEEP = 3  # 每个角色最多留几张用户模板（多了删最早的）
USER_PREFIX = "UT_Hero_UserPhoto_"

# 对局里斜着摆的角色照（data/ 下，随发行版）：{"photos": {文件名: 角度}}。放在数据里不写死在这里：以后再有斜着摆的
# 皮肤只改数据，tools/check_data.py 照样核对。角度超过 TILT_MAX（度）的多半是写错了（例如 17 写成 170），这一项不转，
# 由 tools/check_data.py 报出来
TILT_FILE = "photo_tilt.json"
TILT_MAX = 45


@dataclass(frozen=True)
class _Template:
    hero_id: int
    name: str  # 文件名
    face: np.ndarray  # 灰度
    photo_width: int | None  # wiki 角色照的整张宽度；用户模板为 None（已按界面单位归一）


def heroes_dir():
    """角色照所在的文件夹（下载的识别图片，paths.download_dir）。用到时才取：发行版启动时才决定放在哪。"""
    return paths.download_dir("heroes")


def user_dir():
    """手动指定角色时存的立绘模板所在的文件夹：角色照文件夹里的 user（发行版在个人目录里，所有版本共用）。"""
    return paths.download_dir("heroes") / "user"


def tilts() -> dict[str, float]:
    """对局里斜着摆的角色照（data/photo_tilt.json 的 photos）：文件名 → 角度。文件读不出来时为空（都照原样比对）；
    角度不是数、超出 ±TILT_MAX 的那一项不转。"""
    try:
        raw = json.loads((paths.data_dir() / TILT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    photos = raw.get("photos") if isinstance(raw, dict) else None
    if not isinstance(photos, dict):
        return {}
    return {name: float(angle) for name, angle in photos.items()
            if isinstance(angle, (int, float)) and not isinstance(angle, bool) and -TILT_MAX <= angle <= TILT_MAX}


def turn_photo(photo: np.ndarray, angle: float) -> np.ndarray:
    """整张角色照绕脸部区域（HERO_FACE）的中心转 angle 度（正数逆时针，同 cv2.getRotationMatrix2D），大小不变。转出来的
    空角按边上的像素补（BORDER_REPLICATE），不补黑：角度大时空角会落进脸部区域，黑块是原画里没有的，会拉低相关度。"""
    h, w = photo.shape[:2]
    center = ((HERO_FACE[0] + HERO_FACE[2]) / 2 * w, (HERO_FACE[1] + HERO_FACE[3]) / 2 * h)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(photo, matrix, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _load_templates() -> list[_Template]:
    got = templates.assets._templates
    if got is None:
        got = []
        folder = heroes_dir()
        if folder.is_dir():
            tilted = tilts()
            for f in sorted(folder.glob("UT_Hero_RolePhoto_*.png")):
                m = re.match(r"UT_Hero_RolePhoto_(\d+)", f.name)
                photo = io.imread(f, cv2.IMREAD_GRAYSCALE)
                if m is None or photo is None:
                    continue
                if tilted.get(f.name):  # 斜着摆的皮肤：先转成对局里的样子再取脸部（角度为 0 的照原样）
                    photo = turn_photo(photo, tilted[f.name])
                h, w = photo.shape
                face = photo[
                    round(HERO_FACE[1] * h) : round(HERO_FACE[3] * h), round(HERO_FACE[0] * w) : round(HERO_FACE[2] * w)
                ]
                got.append(_Template(int(m.group(1)), f.name, face, w))
        folder = user_dir()
        if folder.is_dir():
            for f in sorted(folder.glob(f"{USER_PREFIX}*.png")):
                m = re.match(rf"{USER_PREFIX}(\d+)_", f.name)
                face = io.imread(f, cv2.IMREAD_GRAYSCALE)
                if m is None or face is None or min(face.shape) < 20:
                    continue
                got.append(_Template(int(m.group(1)), f"user/{f.name}", face, None))
        templates.assets._templates = got
    return got


def reload_templates() -> None:
    """模板有增减（下载了角色照、存了用户模板）后调用，下次识别时重新读取。"""
    templates.assets._templates = None
    templates.assets._coarse_cache.clear()


def _scales(t: _Template, r_lo: float, r_hi: float) -> tuple[float, float]:
    """这个模板在工作图上的缩放倍数范围（模板像素 → 工作图像素）。r 是界面缩放相对估计值的倍数。"""
    if t.photo_width is None:
        return HERO_WORK_UNIT * r_lo * 0.95, HERO_WORK_UNIT * r_hi * 1.05
    return PHOTO_UNITS[0] * HERO_WORK_UNIT * r_lo / t.photo_width, PHOTO_UNITS[1] * HERO_WORK_UNIT * r_hi / t.photo_width


def _steps(lo: float, hi: float) -> list[float]:
    out, s = [], lo
    while s <= hi * 1.0001:
        out.append(s)
        s *= COARSE_STEP
    return out


def _score(work: np.ndarray, face: np.ndarray, s: float) -> float:
    patch = cv2.resize(face, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    if patch.shape[0] < 10 or patch.shape[0] >= work.shape[0] or patch.shape[1] >= work.shape[1]:
        return -1.0
    return float(cv2.matchTemplate(work, patch, cv2.TM_CCOEFF_NORMED).max())


@dataclass
class HeroGuess:
    hero: str | None  # 有把握时是角色名，否则 None
    best: str  # 最像的角色名
    score: float  # 相关度 0~1
    runner_up: float  # 第二像的角色的相关度
    image: str  # 匹配上的角色照文件（能看出是哪套皮肤；用户模板以「user/」开头）


def _region(frame) -> tuple[np.ndarray, float, float, float]:
    """左下角立绘所在的那块，以及（估计的界面缩放, 缩放下限 / 估计, 上限 / 估计）。（收 Frame）"""
    img = frame.image
    h, w = img.shape[:2]
    est = frame.ui_scale()
    lo, hi = frame.ui_range()
    # 宽屏按高度缩放、估计值可靠，范围按估计值划；较窄的窗口可能按高度缩放，范围按上限划
    reach = est if hi <= 1.1 * est else hi
    x1 = min(w, round(HERO_REGION_RIGHT * reach))
    y0 = max(0, round(h - HERO_REGION_UP * reach))
    return img[y0:h, 0:x1], est, lo / est, hi / est


def read_hero(img: np.ndarray, db: DB) -> HeroGuess | None:
    """img 是游戏窗口客户区的 BGR 图（或 Frame）。没有角色照模板时返回 None。"""
    frame = as_frame(img)
    tpls = _load_templates()
    if not tpls:
        return None
    crop, est, r_lo, r_hi = _region(frame)
    if crop.size == 0:
        return None
    f = HERO_WORK_UNIT / est
    work = cv2.cvtColor(
        cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_CUBIC),
        cv2.COLOR_BGR2GRAY,
    )

    # 粗筛在一半分辨率上做（快 4 倍以上），缩好的模板缓存起来（换了几种窗口比例后缓存会变大，超过上限就清掉）
    half = cv2.resize(work, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    coarse_cache = templates.assets._coarse_cache
    if len(coarse_cache) > 12000:
        coarse_cache.clear()
    coarse = []
    for index, t in enumerate(tpls):
        lo, hi = _scales(t, r_lo, r_hi)
        best_score, best_s = -1.0, lo
        for s in _steps(lo, hi):
            key = (index, round(s, 5))
            patch = coarse_cache.get(key)
            if patch is None:
                patch = coarse_cache[key] = cv2.resize(t.face, None, fx=s * 0.5, fy=s * 0.5, interpolation=cv2.INTER_AREA)
            if patch.shape[0] < 6 or patch.shape[0] >= half.shape[0] or patch.shape[1] >= half.shape[1]:
                continue
            score = float(cv2.matchTemplate(half, patch, cv2.TM_CCOEFF_NORMED).max())
            if score > best_score:
                best_score, best_s = score, s
        coarse.append((best_score, best_s, t))
    coarse.sort(key=lambda c: -c[0])

    # 对粗筛靠前的几张细调缩放
    refined = []
    for _, s, t in coarse[:10]:
        score = max(_score(work, t.face, s * k) for k in FINE)
        refined.append((score, t.hero_id, t.name))
    floor = min(s for s, _, _ in refined)
    # 没细调的那些不会超过细调过的最低分太多，按粗筛分数记，但不让它们排到前面去
    refined += [(min(score, floor), t.hero_id, t.name) for score, _, t in coarse[10:]]

    per_hero: dict[int, tuple[float, str]] = {}
    for score, hero_id, name in refined:
        if score > per_hero.get(hero_id, (-2.0, ""))[0]:
            per_hero[hero_id] = (score, name)
    ranked = sorted(per_hero.items(), key=lambda kv: -kv[1][0])
    best_id, (best_score, best_file) = ranked[0]
    second = ranked[1][1][0] if len(ranked) > 1 else -1.0
    by_id = {hero.id: hero.name for hero in db.heroes.values()}
    best_name = by_id.get(best_id, str(best_id))
    sure = best_score >= MIN_SCORE and best_score - second >= MIN_MARGIN and best_id in by_id
    return HeroGuess(best_name if sure else None, best_name, best_score, second, best_file)


def user_patch(img: np.ndarray) -> np.ndarray | None:
    """这一帧左下角立绘里脸部常在的那一块，按界面单位归一（1 像素 = 1 个界面单位）。画面太小时返回 None。"""
    h, w = img.shape[:2]
    est = ui_scale(w, h)
    x0, up0, x1, up1 = USER_BOX
    box = (round(x0 * est), round(h - up1 * est), round(x1 * est), round(h - up0 * est))
    if box[0] < 0 or box[1] < 0 or box[2] > w or box[3] > h or box[2] - box[0] < 40:
        return None
    crop = img[box[1] : box[3], box[0] : box[2]]
    size = (x1 - x0, up1 - up0)
    return cv2.resize(crop, size, interpolation=cv2.INTER_AREA if est > 1 else cv2.INTER_CUBIC)


def save_template(frame_bgr: np.ndarray, hero_key) -> str | None:
    """把这一帧左下角的立绘存成 hero_key（角色编号、角色名或别名）的用户模板，返回存下的文件路径。

    只在用户手动指定了角色、而立绘无法识别时调用（例如资料库里还没有的新皮肤）。调用方负责确认这一帧是对局画面、
    立绘没有被「查看筹码」面板等挡住。每个角色最多留 USER_KEEP 张，多了删最早的。
    整份写入（fileio.write_bytes：先写「原名.tmp」再换上去），中途出错、断电不会留下半截的模板；写不进时照旧抛 OSError。
    """
    from .stars import _hero_id

    hero_id = _hero_id(hero_key)
    patch = user_patch(as_frame(frame_bgr).image)
    if hero_id is None or patch is None:
        return None
    folder = user_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = folder / f"{USER_PREFIX}{hero_id}_{stamp}.png"
    ok, data = cv2.imencode(".png", patch)
    if not ok:
        return None
    fileio.write_bytes(path, data.tobytes())
    old = sorted(folder.glob(f"{USER_PREFIX}{hero_id}_*.png"))
    for f in old[:-USER_KEEP]:
        try:
            f.unlink()
        except OSError:
            pass
    reload_templates()
    return str(path)
