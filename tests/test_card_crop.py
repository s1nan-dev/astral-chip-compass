"""从卡面原画现裁角色照。

- 裁图（advisor/vision/cardcrop.py）：裁法套到造出来的卡面上，记号落在预期的位置（摆正的、转了角度的；卡面放大一倍、
  裁法不变，照样落在原处）；颜色按透明度乘进去（透明处是黑的，卡面透明处存的白色不带进来），角色照落在卡面外面的部分
  透明；卡面解不开、裁法不对、角色照落在卡面外面、裁出来整张透明时报 CropError。本机有卡面样本时，真的卡面按照游戏内
  核对的裁法裁，和英文 wiki 上同款的游戏角色照（四周各少 8、18 像素）对得上，方向、缩放弄反了就对不上；转了约 -42° 的
  派德曼「酷暑难耐」裁出来再按 photo_tilt.json 的角度转回去，就是卡面本身的样子（反着转对不上）。
- 下载（advisor/assets.py）：下到卡面、核过校验值以后裁成角色照写盘，卡面不落盘；本地覆盖清单记下按哪张卡面、哪份裁法
  裁的，三样都对得上才算在（以前下过的 wiki 角色照、裁法改了、卡面换了、记录丢了、文件不完整的都重下）；硬链接的旧图
  整份换掉，链接到的那一份不动；裁不出、没接上裁图的这一张算失败、不重试（没接上时卡面也不下）；写不进、被拦照原来的
  规矩；卡面在 wiki 上更新过的照样问接口收下；清单在照原样下载和从卡面现裁之间改过时，以前另一张文件的更新记录、已删除的
  记录都不用；清单项没写卡面的校验值时不下、不解码；现裁的排在普通角色照后面、Q 版头像前面；清单项的 crop 读法（旧清单
  没有这一项照常读）；悬浮窗导入下载模块时接上裁图（只登记导入的函数，第一次要裁图时才导入，导入出错按没接上算）。
- 推算裁法的工具（tools/derive_card_crop.py）：造的卡面上推算得回原来的裁法（粗找时没试到的转角也调得出来）；本机有卡面
  样本时，拿英文 wiki 的游戏角色照、截图样的参考图（垫上底色、缩小、存成 JPEG，含转了 17° 的一套）推算，和照游戏内核对的
  裁法差不过 1 个像素。

不联网：下载换成假的取图函数；图片都写在测试沙箱的临时文件夹里，不碰真的 assets\\。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import urllib.error
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from advisor import assets, clock, fileio, paths
from advisor.vision import cardcrop
from tests.support import samples, sandbox
from tools import check_data, derive_card_crop

PW, PH = cardcrop.PHOTO_SIZE
# 英文 wiki 上同款的游戏角色照（224×304，游戏角色照四周各去掉 8、18 像素的样子）放在卡面样本的 enwiki 文件夹里，
# 和照游戏内核对的裁法（data/card_crops.json）比
ENWIKI = ("UT_Hero_RolePhoto_106_02.png", "UT_Hero_RolePhoto_112_03.png")
EN_DIR = "enwiki"
EN_PAD = (8, 18)
TILTED = "UT_Hero_RolePhoto_103_01_sfw.png"  # 阿兰娜泳装的健全版：裁法转了约 17°（data/card_crops.json）


def crop_table() -> dict:
    """随程序的 data/card_crops.json 的 crops：角色照 → {card, affine}。"""
    return json.loads((paths.data_dir() / check_data.CARD_CROPS).read_text(encoding="utf-8"))["crops"]


EN_SAMPLES = [f"{EN_DIR}/{photo}" for photo in ENWIKI] + [check_data.card_of(photo) for photo in ENWIKI]


def textured_card(w: int = 600, h: int = 840, seed: int = 3) -> np.ndarray:
    """造一张卡面（BGRA）：两种粗细的平滑花纹（找位置、比相关度都要有纹理），上边一条透明、透明处的颜色存成白的（同 BWIKI
    的卡面）。"""
    rng = np.random.default_rng(seed)
    tex = np.zeros((h, w, 3), np.float32)
    for cell, weight in ((40, 0.6), (9, 0.4)):
        noise = rng.random((h // cell + 2, w // cell + 2, 3)).astype(np.float32)
        tex += weight * cv2.resize(noise, (w, h), interpolation=cv2.INTER_CUBIC)
    card = np.dstack([(tex * 255).clip(0, 255).astype(np.uint8), np.full((h, w), 255, np.uint8)])
    card[:20] = (255, 255, 255, 0)
    return card


def png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def decode(data: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)


def normalized(m, width: int, height: int) -> list[float]:
    """卡面像素的 2×3 矩阵 → 裁法（第一列乘宽、第二列乘高）。"""
    m = np.array(m, np.float64).reshape(2, 3).copy()
    m[:, 0] *= width
    m[:, 1] *= height
    return [float(v) for v in m.ravel()]


def rotated(angle: float, scale: float, cx: float, cy: float) -> np.ndarray:
    """卡面像素 → 角色照像素：卡面上 (cx, cy) 落在角色照的中心，角色照相对卡面转 angle 度（同 photo_tilt.json）、缩 scale 倍。"""
    t = np.radians(angle)
    rot = scale * np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
    return np.hstack([rot, (np.array([PW / 2, PH / 2]) - rot @ np.array([cx, cy]))[:, None]])


def centroid(img: np.ndarray, x: float, y: float, r: int = 12) -> tuple[float, float]:
    """(x, y) 附近亮点的重心（记号裁出来落在哪里）。"""
    x0, y0 = max(0, int(x) - r), max(0, int(y) - r)
    patch = img[y0:int(y) + r + 1, x0:int(x) + r + 1, 1].astype(np.float64)
    ys, xs = np.nonzero(patch > 40)
    w = patch[ys, xs]
    return float((xs * w).sum() / w.sum() + x0), float((ys * w).sum() / w.sum() + y0)


def gray(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(np.ascontiguousarray(img[:, :, :3]), cv2.COLOR_BGR2GRAY).astype(np.float64)


def masked_corr(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> float:
    return float(np.corrcoef(a[mask], b[mask])[0, 1])


# ---------------------------------------------------------------- 裁图


class CropImageTest(unittest.TestCase):
    MARKS = ((300, 400), (420, 380), (380, 700), (250, 620))  # 卡面上的记号（像素）

    def marked_card(self, scale: int = 1) -> np.ndarray:
        card = np.zeros((1400 * scale, 1000 * scale, 4), np.uint8)
        card[:, :, 3] = 255
        for x, y in self.MARKS:
            cv2.circle(card, (x * scale, y * scale), 6 * scale, (255, 255, 255, 255), -1)
        return card

    def test_marks_land_where_expected(self):
        """摆正的、转了 17° 的两份裁法：卡面上的记号裁出来落在矩阵算出的位置（差不过 0.3 像素）；角色照 240×340、带透明度。"""
        for label, m in (("摆正", [[0.6, 0, -50], [0, 0.6, -110]]), ("转 17°", rotated(17.1, 0.55, 340, 560))):
            with self.subTest(label):
                affine = normalized(m, 1000, 1400)
                photo = cardcrop.crop_image(self.marked_card(), affine)
                self.assertEqual(photo.shape, (PH, PW, 4))
                seen = 0
                for x, y in self.MARKS:
                    ex, ey = np.array(m, np.float64) @ (x, y, 1)
                    if 8 <= ex < PW - 8 and 8 <= ey < PH - 8:
                        gx, gy = centroid(photo, ex, ey)
                        self.assertLess(np.hypot(gx - ex, gy - ey), 0.3, (x, y, ex, ey, gx, gy))
                        seen += 1
                self.assertGreaterEqual(seen, 2)

    def test_same_recipe_on_bigger_card(self):
        """裁法按归一化坐标写：卡面放大一倍（同一张原画），同一份裁法裁出来记号落在同一处。"""
        affine = normalized(rotated(-8.0, 0.5, 330, 540), 1000, 1400)
        small = cardcrop.crop_image(self.marked_card(1), affine)
        big = cardcrop.crop_image(self.marked_card(2), affine)
        for x, y in self.MARKS[:2]:
            ex, ey = rotated(-8.0, 0.5, 330, 540) @ (x, y, 1)
            a, b = centroid(small, ex, ey), centroid(big, ex, ey)
            self.assertLess(np.hypot(a[0] - b[0], a[1] - b[1]), 0.3)

    def test_transparent_becomes_black_and_outside_is_clear(self):
        """颜色按透明度乘进去：全透明处存的白色变成黑的，半透明的按比例变暗；角色照落在卡面外面的部分全透明。"""
        card = np.zeros((400, 300, 4), np.uint8)
        card[:, :] = (10, 20, 30, 255)
        card[:100, :] = (255, 255, 255, 0)  # 全透明，颜色存成白的
        card[100:200, :] = (200, 200, 200, 128)  # 半透明
        # 1:1、整数平移：角色照的像素就是卡面的像素；左上角伸出卡面 20 像素
        photo = cardcrop.crop_image(card, normalized([[1, 0, 20], [0, 1, 20]], 300, 400))
        self.assertTrue((photo[20:120, 20:, :] == 0).all())  # 全透明处：颜色、透明度都是 0
        self.assertTrue((photo[120:220, 20:, :3] == round(200 * 128 / 255)).all())
        self.assertTrue((photo[120:220, 20:, 3] == 128).all())
        self.assertTrue((photo[220:, 20:] == (10, 20, 30, 255)).all())
        self.assertTrue((photo[:20] == 0).all() and (photo[:, :20] == 0).all())  # 卡面外面

    def test_png_formats_and_errors(self):
        """crop_png：不带透明度的、灰度的卡面也裁得出（当整张不透明）；卡面解不开、裁法不对（含数大得离谱、求逆时溢出算不出
        角色照落在哪里的，不变成别的异常）、角色照落在卡面外面、裁出来整张透明时报 CropError（这一张按下载失败算）。"""
        affine = normalized([[0.6, 0, -50], [0, 0.6, -110]], 600, 840)
        card = textured_card()
        bgr = np.ascontiguousarray(card[:, :, :3])
        for label, img in (("BGRA", card), ("BGR", bgr), ("灰度", cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))):
            with self.subTest(label):
                photo = decode(cardcrop.crop_png(png(img), affine))
                self.assertEqual(photo.shape, (PH, PW, 4))
                if label != "BGRA":
                    self.assertTrue((photo[:, :, 3] == 255).all())
        clear = card.copy()
        clear[:, :, 3] = 0
        cases = {
            "不是图片": (b"not a png", affine), "空的": (b"", affine), "截断的": (png(card)[:200], affine),
            "落在卡面外面": (png(card), normalized([[0.6, 0, 2000], [0, 0.6, 0]], 600, 840)),
            "整张透明": (png(clear), affine), "少一个数": (png(card), affine[:5]),
            "不是数": (png(card), ["a", 0, 0, 0, 1, 0]), "缩成一点": (png(card), [0, 0, 5, 0, 0, 5]),
            "无穷大": (png(card), [float("inf"), 0, 0, 0, 1, 0]),
            "数大得离谱": (png(card), [3e302, 4e302, 0, 3e302, 8e302, 0]),
        }
        for label, (data, recipe) in cases.items():
            with self.subTest(label):
                with self.assertRaises(cardcrop.CropError):
                    cardcrop.crop_png(data, recipe)

    def test_crop_error_is_value_error(self):
        # 下载模块按 ValueError 认「裁不出」（advisor/assets.py 的 _keep），不导入识别一侧的异常类
        self.assertTrue(issubclass(cardcrop.CropError, ValueError))
        self.assertEqual(check_data.PHOTO_SIZE, cardcrop.PHOTO_SIZE)  # 核对数据时用的角色照大小同裁图


@unittest.skipUnless(samples.cards_ok(*EN_SAMPLES), samples.cards_reason(*EN_SAMPLES))
class RealCardTest(unittest.TestCase):
    """真的卡面（卡面样本，不随仓库发布）按照游戏内核对的裁法（data/card_crops.json）裁：和英文 wiki 上同款的游戏角色照
    （四周各少 8、18 像素）比，角色本身那部分相关度 0.97 以上；裁法的横竖弄反了（第一、二列除错了宽高）、左右翻了，
    就对不上。"""

    def setUp(self):
        table = crop_table()
        self.pairs = []
        for photo in ENWIKI:
            ref = cardcrop.premultiplied(cardcrop.as_bgra(decode((samples.CARD_SAMPLES_DIR / EN_DIR / photo).read_bytes())))
            raw = decode((samples.CARD_SAMPLES_DIR / table[photo]["card"]).read_bytes())
            self.pairs.append((photo, raw, table[photo]["affine"], ref, EN_PAD))

    def score(self, raw, affine, ref, pad) -> float:
        px, py = pad
        photo = cardcrop.crop_image(raw, affine)[py:PH - py, px:PW - px]
        return masked_corr(gray(photo), gray(ref), ref[:, :, 3] > 200)

    def test_matches_game_photo(self):
        for photo, raw, affine, ref, pad in self.pairs:
            h, w = raw.shape[:2]
            a, b, c, d, e, f = affine
            with self.subTest(photo):
                self.assertGreater(self.score(raw, affine, ref, pad), 0.97)
                swapped = [a * w / h, b * h / w, c, d * w / h, e * h / w, f]  # 第一列除了高、第二列除了宽
                mirrored = [-a, -b, PW - 1 - c, d, e, f]
                for label, wrong in (("横竖弄反", swapped), ("左右翻了", mirrored)):
                    self.assertLess(self.score(raw, wrong, ref, pad), 0.8, label)


PADMAN = "UT_Hero_RolePhoto_105_01.png"  # 派德曼「酷暑难耐」：对局里显示斜着约 42° 的整张卡面，裁法也转了这么多
PADMAN_CARD = check_data.card_of(PADMAN)


def gray8(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(np.ascontiguousarray(img[:, :, :3]), cv2.COLOR_BGR2GRAY)


def face_box() -> tuple[int, int, int, int]:
    """角色照（240×340）里脸部区域的像素范围（同认角色建模板的取法）。"""
    from advisor.vision.anchors import HERO_FACE

    x0, y0, x1, y1 = HERO_FACE
    return round(x0 * PW), round(y0 * PH), round(x1 * PW), round(y1 * PH)


@unittest.skipUnless(samples.cards_ok(PADMAN_CARD), samples.cards_reason(PADMAN_CARD))
class TurnedCardTest(unittest.TestCase):
    """派德曼「酷暑难耐」的卡面（卡面样本，不随仓库发布）按照游戏内核对的裁法裁：卡面就是识别图片清单里那一项的卡面（大小、
    SHA-1 对得上），裁法转了约 -42.3°、和 photo_tilt.json 的角度一样；裁出来的角色照按这个角度转回去（认角色建模板时的转法），
    脸部那一块和卡面上同一块（只缩放、不转，对局里显示的就是这样的整张卡面）对得上，反着转就对不上；样本的 enwiki 文件夹里另有
    英文 wiki 上同款的游戏角色照（四周各少 8、18 像素）时，和它比相关度 0.97 以上，脸部那一块也一样。"""

    def setUp(self):
        self.data = (samples.CARD_SAMPLES_DIR / PADMAN_CARD).read_bytes()
        self.card = decode(self.data)
        self.affine = crop_table()[PADMAN]["affine"]
        self.photo = cardcrop.crop_image(self.card, self.affine)

    def test_card_is_listed(self):
        item = next(i for i in assets.load_manifest().items if i.name == f"heroes/{PADMAN}")
        self.assertEqual((len(self.data), sha1(self.data)), (item.size, item.sha1))
        self.assertEqual(list(item.crop), self.affine)
        tilts = json.loads((paths.data_dir() / check_data.PHOTO_TILT).read_text(encoding="utf-8"))["photos"]
        self.assertAlmostEqual(check_data.crop_rotation(self.affine), -42.25, delta=0.01)
        self.assertEqual(tilts[PADMAN], -42.3)

    def test_turned_back_matches_card(self):
        from advisor.vision import hero

        m = cardcrop.pixel_matrix(self.affine, self.card.shape[1], self.card.shape[0])
        scale = float(np.sqrt(abs(np.linalg.det(m[:, :2]))))  # 角色照像素 / 卡面像素
        x0, y0, x1, y1 = face_box()
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2  # 脸部中心：卡面上同一点挪到这里，只缩放、不转
        ux, uy = cv2.invertAffineTransform(m) @ np.array([cx, cy, 1.0])
        shift = np.array([[scale, 0, cx - scale * ux], [0, scale, cy - scale * uy]])
        view = cv2.warpAffine(gray8(cardcrop.premultiplied(cardcrop.as_bgra(self.card))), shift, (PW, PH),
                              flags=cv2.INTER_LINEAR)[y0:y1, x0:x1].astype(np.float64)
        tilt = hero.tilts()[PADMAN]

        def corr(angle: float) -> float:
            turned = hero.turn_photo(gray8(self.photo), angle)[y0:y1, x0:x1].astype(np.float64)
            return float(np.corrcoef(turned.ravel(), view.ravel())[0, 1])

        self.assertGreater(corr(tilt), 0.95)
        self.assertLess(corr(-tilt), 0.5)

    def test_matches_game_photo(self):
        ref_path = samples.CARD_SAMPLES_DIR / EN_DIR / PADMAN
        if not ref_path.is_file():
            self.skipTest(samples.cards_reason(f"{EN_DIR}/{PADMAN}"))
        ref = cardcrop.premultiplied(cardcrop.as_bgra(decode(ref_path.read_bytes())))
        px, py = EN_PAD
        inner = self.photo[py:PH - py, px:PW - px]
        mask = ref[:, :, 3] > 200
        self.assertGreater(masked_corr(gray(inner), gray(ref), mask), 0.97)
        x0, y0, x1, y1 = face_box()
        face = (slice(y0 - py, y1 - py), slice(x0 - px, x1 - px))
        self.assertGreater(masked_corr(gray(inner)[face], gray(ref)[face], mask[face]), 0.97)


# ---------------------------------------------------------------- 下载


RECIPE = normalized([[0.6, 0, -50], [0, 0.6, -110]], 600, 840)


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class Web:
    """假的取图函数：网址 → 内容（bytes）或异常；记下每个请求。接口（assets.API）的回话另给。"""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.calls.append(url)
        key = url.split("?", 1)[0]
        value = self.routes.get(key)
        if value is None:
            raise urllib.error.HTTPError(url, 404, "not found", {}, None)
        if isinstance(value, BaseException):
            raise value
        return io.BytesIO(value)


class CropDownloadTest(unittest.TestCase):
    def setUp(self):
        self.base = sandbox.temp_dir(self)
        patcher = mock.patch.object(paths, "assets_dir", side_effect=lambda kind="": self.base / kind)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.card = png(textured_card())

    def item(self, card: bytes | None = None, name: str = "UT_Hero_RolePhoto_901_sfw.png", crop=RECIPE,
             url: str = "https://img/card.png") -> assets.AssetItem:
        card = self.card if card is None else card
        return assets.AssetItem("heroes", name, "UT Hero Card 901 sfw.png", url, len(card), sha1(card), crop=tuple(crop))

    def download(self, items, routes, **kw):
        web = Web(routes)
        kw.setdefault("cropper", cardcrop.crop_png)
        result = assets.download(items, opener=web, sleep=lambda s: None, workers=1, **kw)
        return result, web

    def test_downloads_card_and_crops(self):
        """下到卡面、核过校验值以后裁成角色照写盘（和直接裁的一样），不留卡面、不留 .part；覆盖清单记下卡面的 SHA-1、裁法和
        角色照的大小，之后算「在」、不再下载。"""
        item = self.item()
        progress = []
        (ok, failed), web = self.download([item], {item.url: self.card}, progress=lambda *a: progress.append(a))
        self.assertEqual((ok, failed), ([item.name], []))
        self.assertEqual(progress, [(1, 1, item.name)])
        written = item.path.read_bytes()
        self.assertEqual(written, cardcrop.crop_png(self.card, RECIPE))
        self.assertEqual(decode(written).shape, (PH, PW, 4))
        files = sorted(p.relative_to(self.base).as_posix() for p in self.base.rglob("*") if p.is_file())
        self.assertEqual(files, sorted([assets.LOCAL_MANIFEST, f"heroes/{item.file}"]))  # 卡面没落盘
        self.assertNotIn(self.card, [p.read_bytes() for p in self.base.rglob("*") if p.is_file()])
        record = assets.load_local()["cropped"][item.name]
        self.assertEqual(record, {"card": item.sha1, "crop": list(RECIPE), "size": len(written)})
        self.assertNotEqual(len(written), item.size)  # 大小和清单里卡面的对不上，照样算在
        self.assertTrue(assets.is_present(item))
        self.assertEqual(assets.missing(assets.Manifest([item])), [])
        (ok, failed), web = self.download(assets.missing(assets.Manifest([item])), {item.url: self.card})
        self.assertEqual((ok, failed, web.calls), ([], [], []))

    def test_presence_needs_matching_record(self):
        """记录对不上的都不算在：卡面换了（SHA-1 不同）、裁法改了、文件不完整（大小和记着的不同）、记录丢了、覆盖清单
        读不出来；对得上的才算在。普通的图片照旧按大小比。"""
        item = self.item()
        self.download([item], {item.url: self.card})
        good = assets.load_local()
        self.assertTrue(assets.is_present(item, good))
        cases = {
            "卡面换了": lambda rec: rec.update(card="0" * 40),
            "裁法改了": lambda rec: rec.update(crop=[*RECIPE[:5], RECIPE[5] + 1]),
            "大小不同": lambda rec: rec.update(size=rec["size"] + 1),
        }
        for label, change in cases.items():
            with self.subTest(label):
                local = json.loads(json.dumps(good))
                change(local["cropped"][item.name])
                self.assertFalse(assets.is_present(item, local))
        self.assertFalse(assets.is_present(item, {"updated": {}, "gone": {}, "cropped": {}}))  # 没有记录
        self.assertFalse(assets.is_present(item, {"updated": {}, "gone": {}}))  # 旧的覆盖清单：没有这一节
        self.assertFalse(assets.is_present(replace(item, crop=(1, 2, 3, 4, 5, 6)), good))  # 清单里的裁法改了
        (self.base / assets.LOCAL_MANIFEST).write_text("{坏", encoding="utf-8")
        self.assertFalse(assets.is_present(item))  # 读不出来时不能当成在
        self.assertEqual(assets.missing(assets.Manifest([item])), [item])

    def test_old_wiki_photo_replaced(self):
        """以前下过 wiki 上那张旧的角色照（同名、没有现裁的记录）：算缺，重下卡面再裁，整份换掉。旧图是硬链接时只换掉
        这一个名字，链接到的那一份一个字节不动（开发目录里的图片可能是硬链接到别处的）。"""
        item = self.item()
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        original = elsewhere / item.file
        old = png(textured_card(240, 340, seed=9))
        original.write_bytes(old)
        item.path.parent.mkdir(parents=True)
        os.link(original, item.path)
        self.assertEqual(assets.missing(assets.Manifest([item])), [item])
        (ok, failed), _ = self.download([item], {item.url: self.card})
        self.assertEqual((ok, failed), ([item.name], []))
        self.assertEqual(item.path.read_bytes(), cardcrop.crop_png(self.card, RECIPE))
        self.assertEqual(original.read_bytes(), old)

    def test_crop_failure_not_retried(self):
        """卡面裁不出（裁法让角色照落在卡面外面、裁图的函数报 ValueError）：这一张算失败（原因以 CROP_FAILED 开头），只请求
        一次、不问接口，不写图、不记录。"""
        item = self.item(crop=normalized([[0.6, 0, 3000], [0, 0.6, 0]], 600, 840))
        (ok, failed), web = self.download([item], {item.url: self.card})
        self.assertEqual(ok, [])
        self.assertTrue(failed[0][1].startswith(assets.CROP_FAILED), failed)
        self.assertEqual(web.calls, [item.url])
        self.assertFalse(item.path.exists())
        self.assertEqual(assets.load_local()["cropped"], {})

        def broken(data, crop):
            raise ValueError("测试")

        (ok, failed), _ = self.download([self.item()], {item.url: self.card}, cropper=broken)
        self.assertEqual(failed, [(item.name, f"{assets.CROP_FAILED}：测试")])

    def test_unverified_card_not_decoded(self):
        """下到的卡面和清单的大小、校验值对不上，接口给的也还是清单里那张（不是 wiki 上更新过）：这一张算失败，裁图的函数
        一次都不调（只有核过校验值的卡面才交去解码），不写图、不记录。"""
        item = self.item()
        other = png(textured_card(seed=21))
        info = json.dumps({"query": {"pages": [{"title": "File:" + item.title, "imageinfo": [
            {"url": item.url, "size": item.size, "sha1": item.sha1}]}]}}).encode()
        seen = []

        def spy(data, crop):
            seen.append(data)
            return cardcrop.crop_png(data, crop)

        (ok, failed), web = self.download([item], {item.url: other, assets.API: info}, cropper=spy)
        self.assertEqual(ok, [])
        self.assertEqual([name for name, _ in failed], [item.name])
        self.assertTrue(failed[0][1].startswith(("大小不符", "校验值不符")), failed)
        self.assertEqual(seen, [])
        self.assertEqual(web.calls.count(item.url), 2)  # 问过接口、按接口给的再下一次，仍对不上
        self.assertFalse(item.path.exists())
        self.assertEqual(assets.load_local()["cropped"], {})

    def test_card_without_sha1_not_decoded(self):
        """清单项（手改过的）没写卡面的校验值：这一张算失败（NO_CARD_SHA1，原因写明没有校验值），卡面不下、裁图的函数一次都
        不调（核对不了的卡面不交去解码），不写图、不记录。下到卡面以后才发现没有校验值的（问过接口也没给）同样不解码。"""
        item = replace(self.item(), sha1="")
        seen = []

        def spy(data, crop):
            seen.append(data)
            return cardcrop.crop_png(data, crop)

        (ok, failed), web = self.download([item], {item.url: self.card}, cropper=spy)
        self.assertEqual((ok, failed), ([], [(item.name, assets.NO_CARD_SHA1)]))
        self.assertIn("校验值", assets.NO_CARD_SHA1)
        self.assertTrue(assets.is_own_failure(assets.NO_CARD_SHA1))  # 清单的问题，同「清单里没有下载地址」
        self.assertEqual((web.calls, seen), ([], []))
        self.assertFalse(item.path.exists())
        self.assertEqual(assets.load_local()["cropped"], {})
        job = assets._Job.of(0, item, assets.load_local(), spy)
        self.assertEqual(assets._keep(job, self.card, None), assets.NO_CARD_SHA1)
        self.assertEqual(seen, [])

    def test_gone_record_from_other_kind_ignored(self):
        """以前记成 wiki 上已删除的那一项（wiki 上的旧角色照删了），清单里改成从卡面现裁以后：已删除的记录说的是旧角色照，
        和现在下卡面对不上，不用它，启动时照常下卡面再裁，不等 7 天；以前的写法（只有日期）是照原样下载时记的，同样不用。
        现在的写法记着查到删了的文件页，就是现在要问的这张卡面时照旧 7 天内不自动下（「重试」照样下）；反过来卡面删了以后
        这一项改回照原样下载，也照常下。下载时查到删了，记下日期和问的那一页。"""
        item = self.item()
        manifest = assets.Manifest([item])
        today = clock.today().isoformat()
        local = self.base / assets.LOCAL_MANIFEST
        for record in (today, {"date": today, "title": "UT Hero RolePhoto 901 sfw.png"}):
            with self.subTest(record=record):
                local.write_text(json.dumps({"gone": {item.name: record}}), encoding="utf-8")
                self.assertEqual(assets.missing(manifest), [item])
        local.write_text(json.dumps({"gone": {item.name: today}}), encoding="utf-8")
        (ok, failed), _ = self.download(assets.missing(manifest), {item.url: self.card})
        self.assertEqual((ok, failed), ([item.name], []))
        self.assertNotIn(item.name, assets.load_local()["gone"])  # 存好了：旧记录清掉
        local.write_text(json.dumps({"gone": {item.name: {"date": today, "title": item.title}}}), encoding="utf-8")
        item.path.unlink()
        self.assertEqual(assets.missing(manifest), [])  # 这张卡面在 wiki 上删了：7 天内不自动下
        self.assertEqual(assets.missing(manifest, retry_gone=True), [item])
        plain = assets.AssetItem("heroes", item.file, "UT Hero RolePhoto 901 sfw.png", "https://img/photo.png", 10, "0" * 40)
        self.assertEqual(assets.missing(assets.Manifest([plain])), [plain])  # 改回照原样下载：卡面删了不相干
        local.unlink()
        missing_page = json.dumps({"query": {"pages": [{"title": "File:" + item.title, "missing": True}]}}).encode()
        (ok, failed), _ = self.download([item], {item.url: urllib.error.HTTPError(item.url, 404, "gone", {}, None),
                                                 assets.API: missing_page})
        self.assertEqual(failed, [(item.name, assets.GONE)])
        self.assertEqual(assets.load_local()["gone"], {item.name: {"date": today, "title": item.title}})
        self.assertEqual(assets.missing(manifest), [])

    def test_without_cropper(self):
        """没接上裁图（use_cropper 没调过、也没给 cropper）：这一张算失败（NO_CROPPER），卡面不下（下了也裁不成，一张一两兆），
        不会把整张卡面当角色照存下来。"""
        item = self.item()
        with mock.patch.object(assets, "_cropper", None):
            (ok, failed), web = self.download([item], {item.url: self.card}, cropper=None)
        self.assertEqual(failed, [(item.name, assets.NO_CROPPER)])
        self.assertEqual(web.calls, [])
        self.assertFalse(item.path.exists())
        with mock.patch.object(assets, "_cropper", cardcrop.crop_png):  # 接上了：不给 cropper 也按它裁
            (ok, failed), _ = self.download([item], {item.url: self.card}, cropper=None)
        self.assertEqual((ok, failed), ([item.name], []))

    def test_write_failure_and_blocked(self):
        """写不进程序文件夹：原因以 WRITE_FAILED 开头（悬浮窗提示换个位置），不记录；卡面被站点防护拦下（567）：同别的
        图片，这一轮暂停、记下冷却和认出被拦的是这一张。"""
        item = self.item()
        real = fileio.write_bytes

        def flaky(path, data, **kwargs):
            if Path(path).name == item.file:
                raise PermissionError(13, "拒绝访问", str(path))
            return real(path, data, **kwargs)

        with mock.patch.object(fileio, "write_bytes", side_effect=flaky):
            (ok, failed), _ = self.download([item], {item.url: self.card})
        self.assertTrue(assets.is_write_error(failed[0][1]), failed)
        self.assertNotIn(item.name, assets.load_local()["cropped"])
        blocked = urllib.error.HTTPError(item.url, 567, "blocked", {}, io.BytesIO(b""))
        (ok, failed), _ = self.download([item], {item.url: blocked})
        self.assertTrue(assets.is_paused(failed[0][1]), failed)
        self.assertEqual(assets.cooldown().item, item.name)

    def test_stale_update_from_other_kind_ignored(self):
        """清单在两种下法之间改过时，覆盖清单里以前记下的更新记录（另一张文件的地址和校验值）不用：wiki 上更新过的旧角色照
        改成从卡面现裁以后，照清单下卡面再裁（不会去下旧角色照、把它当卡面裁，再记成在）；反过来，卡面在 wiki 上更新过的
        现裁角色照改回照原样下载以后，照清单下角色照（不会把卡面当角色照存下来）。存好以后旧记录清掉。"""
        old_photo = png(textured_card(240, 340, seed=5))
        item = self.item(name="UT_Hero_RolePhoto_901.png")
        item.path.parent.mkdir(parents=True)
        item.path.write_bytes(old_photo)
        stale = {"url": "https://img/old_photo.png", "size": len(old_photo), "sha1": sha1(old_photo)}
        (self.base / assets.LOCAL_MANIFEST).write_text(json.dumps({"updated": {item.name: stale}}), encoding="utf-8")
        self.assertEqual(assets.effective(item), item)
        self.assertEqual(assets.missing(assets.Manifest([item])), [item])
        (ok, failed), web = self.download([item], {item.url: self.card, stale["url"]: old_photo})
        self.assertEqual((ok, failed, web.calls), ([item.name], [], [item.url]))
        self.assertEqual(item.path.read_bytes(), cardcrop.crop_png(self.card, RECIPE))
        local = assets.load_local()
        self.assertNotIn(item.name, local["updated"])
        self.assertEqual(local["cropped"][item.name]["card"], item.sha1)
        self.assertTrue(assets.is_present(item))

        # 反过来：卡面在 wiki 上更新过（更新记录、现裁的记录都在），这一项改回照原样下载角色照
        new_card = png(textured_card(seed=12))
        cut = cardcrop.crop_png(new_card, RECIPE)
        item.path.write_bytes(cut)
        (self.base / assets.LOCAL_MANIFEST).write_text(json.dumps({
            "updated": {item.name: {"url": "https://img/new_card.png", "size": len(new_card), "sha1": sha1(new_card)}},
            "cropped": {item.name: {"card": sha1(new_card), "crop": list(RECIPE), "size": len(cut)}}}), encoding="utf-8")
        right = png(textured_card(240, 340, seed=6))
        plain = assets.AssetItem("heroes", item.file, "UT Hero RolePhoto 901.png", "https://img/right.png", len(right),
                                 sha1(right))
        self.assertEqual(assets.effective(plain), plain)
        self.assertEqual(assets.missing(assets.Manifest([plain])), [plain])
        (ok, failed), web = self.download([plain], {plain.url: right, "https://img/new_card.png": new_card})
        self.assertEqual((ok, failed, web.calls), ([plain.name], [], [plain.url]))
        self.assertEqual(plain.path.read_bytes(), right)
        local = assets.load_local()
        self.assertEqual((local["updated"], local["cropped"]), ({}, {}))
        self.assertTrue(assets.is_present(plain))

    def test_card_updated_on_wiki(self):
        """wiki 上的卡面换过（内容和清单的校验值不符、和接口给的相符）：照样收下、按新卡面裁，覆盖清单记下新的地址和校验值、
        现裁的记录也是新卡面的，下次启动算在。"""
        new = png(textured_card(seed=11))
        item = self.item()  # 清单里是旧卡面
        info = json.dumps({"query": {"pages": [{"title": "File:" + item.title, "imageinfo": [
            {"url": item.url, "size": len(new), "sha1": sha1(new)}]}]}}).encode()
        (ok, failed), web = self.download([item], {item.url: new, assets.API: info})
        self.assertEqual((ok, failed), ([item.name], []))
        self.assertTrue(any(url.startswith(assets.API) for url in web.calls))
        local = assets.load_local()
        self.assertEqual(local["updated"][item.name]["sha1"], sha1(new))
        self.assertEqual(local["cropped"][item.name]["card"], sha1(new))
        self.assertEqual(item.path.read_bytes(), cardcrop.crop_png(new, RECIPE))
        self.assertTrue(assets.is_present(item))

    def test_order(self):
        """还缺的图片：普通的角色照、从卡面现裁的角色照、Q 版头像、筹码图标（同一类里照清单的先后）。"""
        def plain(kind, name):
            return assets.AssetItem(kind, name, name, f"https://img/{name}", 3, "s")

        items = [plain("chips", "c.png"), self.item(name="UT_Hero_RolePhoto_901_sfw.png"), plain("profiles", "p.png"),
                 plain("heroes", "h2.png"), self.item(name="UT_Hero_RolePhoto_902_sfw.png"), plain("heroes", "h1.png")]
        self.assertEqual([i.file for i in assets.missing(assets.Manifest(items))],
                         ["h2.png", "h1.png", "UT_Hero_RolePhoto_901_sfw.png", "UT_Hero_RolePhoto_902_sfw.png", "p.png",
                          "c.png"])

    def test_manifest_crop_field(self):
        """清单项的 crop：6 个数的列表读成元组；没有这一项的（旧清单、别的图片）为 None，照原样下载；写坏了的（不是列表）
        照样当成要现裁的，下载时这一张按裁不出算失败，不会把卡面当角色照存下来。"""
        data = sandbox.temp_dir(self)
        items = [
            {"dir": "heroes", "file": "a.png", "title": "a.png", "url": "https://img/a.png", "size": 3, "sha1": "s"},
            {"dir": "heroes", "file": "b.png", "title": "UT Hero Card b.png", "url": "https://img/card.png",
             "size": len(self.card), "sha1": sha1(self.card), "crop": RECIPE},
            {"dir": "heroes", "file": "c.png", "title": "UT Hero Card c.png", "url": "https://img/card.png",
             "size": len(self.card), "sha1": sha1(self.card), "crop": "坏了"},
        ]
        (data / assets.MANIFEST).write_text(json.dumps({"items": items}), encoding="utf-8")
        with mock.patch.object(paths, "data_dir", return_value=data):
            manifest = assets.load_manifest()
        a, b, c = manifest.items
        self.assertIsNone(a.crop)
        self.assertEqual(b.crop, tuple(RECIPE))
        self.assertEqual(c.crop, ("坏了",))
        hash(b)  # 清单项不可变、能当键用
        (ok, failed), _ = self.download([c], {c.url: self.card})
        self.assertTrue(failed[0][1].startswith(assets.CROP_FAILED), failed)
        self.assertFalse(c.path.exists())

    def test_load_local_drops_bad_crop_records(self):
        """覆盖清单里现裁的记录类型不对的丢掉（手改过、以后改了格式）：card 不是文字、crop 不是 6 个数、size 不是正整数。"""
        good = {"card": "s", "crop": RECIPE, "size": 5}
        bad = {"a": {**good, "card": 1}, "b": {**good, "crop": RECIPE[:5]}, "c": {**good, "crop": [*RECIPE[:5], "1"]},
               "d": {**good, "size": True}, "e": {**good, "size": 0}, "f": "不是对象", "g": {**good, "crop": [True] * 6}}
        (self.base / assets.LOCAL_MANIFEST).write_text(json.dumps({"cropped": {"ok": good, **bad}}), encoding="utf-8")
        self.assertEqual(assets.load_local()["cropped"], {"ok": good})
        (self.base / assets.LOCAL_MANIFEST).write_text(json.dumps({"cropped": []}), encoding="utf-8")
        self.assertEqual(assets.load_local()["cropped"], {})

    def test_note_crop(self):
        """开发用的取图工具裁好以后记一笔（note_crop）：和下载时记的一样，is_present 照它判断；这张别的记录一并清掉。"""
        item = self.item()
        item.path.parent.mkdir(parents=True)
        photo = cardcrop.crop_png(self.card, RECIPE)
        item.path.write_bytes(photo)
        assets._update_local(item.name, gone="2026-01-01")
        assets.note_crop(item.name, item.sha1, RECIPE, len(photo))
        local = assets.load_local()
        self.assertEqual(local["gone"], {})
        self.assertTrue(assets.is_present(item, local))

    def test_services_wire_cropper(self):
        """悬浮窗导入下载模块时接上裁图（advisor/ui/services.py）：下载模块在底层、不导入识别一侧。只登记导入裁图模块的函数，
        这时不导入；下载这一轮有要现裁的图片时才导入、接上（不给 cropper 也按它裁），只导入一次；没有要现裁的不导入。"""
        from advisor.ui import services

        self.addCleanup(assets.use_cropper, assets._cropper, load=assets._cropper_load)
        assets.use_cropper(None)
        loaded = services.Services.load(("assets",))
        self.assertIs(loaded.assets, assets)
        self.assertIsNone(assets._cropper)
        self.assertIs(assets._cropper_load, services._crop_png_loader)
        plain = assets.AssetItem("heroes", "a.png", "a.png", "https://img/a.png", 3, "s")
        self.assertIsNone(assets._current_cropper([plain]))  # 这一轮没有要现裁的：不导入，登记的照留
        self.assertIs(assets._cropper_load, services._crop_png_loader)
        item = self.item()
        (ok, failed), _ = self.download([item], {item.url: self.card}, cropper=None)
        self.assertEqual((ok, failed), ([item.name], []))
        self.assertEqual(item.path.read_bytes(), cardcrop.crop_png(self.card, RECIPE))
        self.assertIs(assets._cropper, cardcrop.crop_png)
        self.assertIsNone(assets._cropper_load)  # 只导入一次

    def test_cropper_import_failure(self):
        """导入裁图模块出错：记进错误记录，要现裁的那几张按没接上算（NO_CROPPER），卡面不下；只试一次，之后的几轮照旧按
        没接上算（同悬浮窗起来时就接不上），不再导入、不再记错误。"""
        from advisor.ui import services
        from tests.support.errors import capture_errors

        errors = capture_errors(self)
        self.addCleanup(assets.use_cropper, assets._cropper, load=assets._cropper_load)
        real_import = __import__

        def no_cardcrop(name, globals=None, locals=None, fromlist=(), level=0):
            if fromlist and "cardcrop" in fromlist:
                raise ImportError("测试：裁图模块导入失败")
            return real_import(name, globals, locals, fromlist, level)

        assets.use_cropper(load=services._crop_png_loader)
        item = self.item()
        with mock.patch("builtins.__import__", side_effect=no_cardcrop):
            (ok, failed), web = self.download([item], {item.url: self.card}, cropper=None)
        self.assertEqual((ok, failed, web.calls), ([], [(item.name, assets.NO_CROPPER)], []))
        titles = [t for t, _ in errors.entries()]
        self.assertEqual(titles, ["从卡面裁角色照的模块 advisor.vision.cardcrop 导入失败，这几张角色照下载不了"])
        (ok, failed), web = self.download([item], {item.url: self.card}, cropper=None)
        self.assertEqual((failed, web.calls), ([(item.name, assets.NO_CROPPER)], []))
        self.assertEqual(len(errors.entries()), 1)
        errors.clear()
        assets.use_cropper(load=lambda: "不是函数")  # 交回的不是函数：同样按没接上算
        self.assertEqual(self.download([item], {item.url: self.card}, cropper=None)[0][1],
                         [(item.name, assets.NO_CROPPER)])

    def test_overlay_start_does_not_import_cropper(self):
        """导入下载模块（Services.load）不带进裁图模块、识别的包和 OpenCV：下载模块本身不依赖识别那一侧，第一次要裁图时
        才导入（services._assets）。另起一个 Python 进程，导入以后看有哪些模块。"""
        code = ("import json, sys; from advisor.ui import services; services.Services.load(('assets',)); "
                "print(json.dumps([m for m in ('advisor.vision', 'advisor.vision.cardcrop', 'cv2') if m in sys.modules]))")
        proc = subprocess.run([sys.executable, "-B", "-c", code], cwd=Path(__file__).resolve().parents[1],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout.strip().splitlines()[-1]), [])


# ---------------------------------------------------------------- 推算裁法


class DeriveTest(unittest.TestCase):
    def test_reference_frame(self):
        """参考图：带透明度的按透明度比（颜色乘进透明度）；截图按四周一圈的底色认出背景、不比；--pad 那一圈不比；
        --mask none 整张都比。"""
        ref = np.zeros((170, 120, 4), np.uint8)
        ref[40:130, 30:90] = (90, 160, 30, 255)
        frame, keep = derive_card_crop.reference_frame(ref)
        self.assertEqual(frame.shape, (PH, PW))
        self.assertAlmostEqual(keep.mean(), (90 * 60) / (170 * 120), delta=0.02)
        shot = np.full((340, 240, 3), (20, 20, 20), np.uint8)
        shot[60:300, 50:200] = textured_card(150, 240)[:, :, :3]
        _, keep = derive_card_crop.reference_frame(shot)
        self.assertFalse(keep[:50].any() or keep[:, :40].any())  # 底色不比
        self.assertGreater(keep[70:290, 60:190].mean(), 0.95)
        _, keep = derive_card_crop.reference_frame(shot, mask="none")
        self.assertTrue(keep.all())
        _, keep = derive_card_crop.reference_frame(shot[:304, :224], pad=EN_PAD, mask="none")
        self.assertFalse(keep[:18].any() or keep[:, :8].any() or keep[-18:].any() or keep[:, -8:].any())
        self.assertTrue(keep[18:-18, 8:-8].all())

    def test_synthetic_card(self):
        """造的卡面：按已知的裁法（转 12°）裁出参考图，粗找时只试 10.5°、13.5°，细调以后照样推算得回原来的裁法（差不过
        0.5 个像素），转角就是 12°。"""
        card = textured_card()
        truth = normalized(rotated(12.0, 0.62, 310, 470), 600, 840)
        ref = cardcrop.crop_image(card, truth)
        result = derive_card_crop.derive(card, ref, angles=[10.5, 13.5])
        self.assertLess(derive_card_crop.max_offset(truth, result.affine, 600, 840), 0.5)
        self.assertAlmostEqual(result.angle, 12.0, delta=0.1)
        self.assertGreater(result.score, 0.99)
        self.assertAlmostEqual(check_data.crop_rotation(result.affine), result.angle)

    def test_main_prints_entry(self):
        """命令行：打印裁法、转角和分数，以及可以直接贴进 card_crops.json 的一项（角色照的文件名由卡面的换来）；转了角度的
        另提示在 photo_tilt.json 加一项。"""
        folder = sandbox.temp_dir(self)
        card = textured_card()
        truth = normalized(rotated(12.0, 0.62, 310, 470), 600, 840)
        (folder / "UT_Hero_Card_901_01.png").write_bytes(png(card))
        (folder / "ref.png").write_bytes(png(cardcrop.crop_image(card, truth)))
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            code = derive_card_crop.main([str(folder / "UT_Hero_Card_901_01.png"), str(folder / "ref.png"),
                                          "--angles", "10.5,13.5,3"])
        self.assertEqual(code, 0)
        text = out.getvalue()
        line = next(x for x in text.splitlines() if x.startswith('"UT_Hero_RolePhoto_901_01.png"'))
        entry = json.loads("{" + line + "}")["UT_Hero_RolePhoto_901_01.png"]
        self.assertEqual(entry["card"], "UT_Hero_Card_901_01.png")
        self.assertLess(derive_card_crop.max_offset(truth, entry["affine"], 600, 840), 0.5)
        self.assertIn('photo_tilt.json 的 photos 里加「"UT_Hero_RolePhoto_901_01.png": 12.0」', text)

    @unittest.skipUnless(samples.cards_ok(*EN_SAMPLES), samples.cards_reason(*EN_SAMPLES))
    def test_enwiki_reference(self):
        """真的卡面、英文 wiki 上的游戏角色照当参考（--pad 8,18）：推算出来的和照游戏内核对的裁法差不过 1 个像素。"""
        photo = ENWIKI[0]
        entry = crop_table()[photo]
        truth = entry["affine"]
        ref = decode((samples.CARD_SAMPLES_DIR / EN_DIR / photo).read_bytes())
        raw = decode((samples.CARD_SAMPLES_DIR / entry["card"]).read_bytes())
        result = derive_card_crop.derive(raw, ref, pad=EN_PAD)
        h, w = raw.shape[:2]
        self.assertLess(derive_card_crop.max_offset(truth, result.affine, w, h), 1.0)
        self.assertGreater(result.score, 0.97)

    @unittest.skipUnless(samples.cards_ok("UT_Hero_Card_103_01_sfw.png"),
                         samples.cards_reason("UT_Hero_Card_103_01_sfw.png"))
    def test_screenshot_rotated(self):
        """真的卡面、截图样的参考图（按 data/card_crops.json 的裁法裁出来，垫上底色、去掉透明度、缩小、存成 JPEG）：转了约
        17° 的一套照样推算得回来，差不过 1 个像素，转角对得上。"""
        crops = crop_table()
        truth = crops[TILTED]["affine"]
        raw = decode((samples.CARD_SAMPLES_DIR / crops[TILTED]["card"]).read_bytes())
        photo = cardcrop.crop_image(raw, truth)
        alpha = photo[:, :, 3:4].astype(np.float32) / 255
        shot = (photo[:, :, :3] + np.array((70, 52, 96), np.float32) * (1 - alpha)).clip(0, 255).astype(np.uint8)
        shot = cv2.resize(shot, (168, 238), interpolation=cv2.INTER_AREA)
        shot = cv2.imdecode(cv2.imencode(".jpg", shot, [cv2.IMWRITE_JPEG_QUALITY, 85])[1], cv2.IMREAD_COLOR)
        result = derive_card_crop.derive(raw, shot)
        h, w = raw.shape[:2]
        self.assertLess(derive_card_crop.max_offset(truth, result.affine, w, h), 1.0)
        self.assertAlmostEqual(result.angle, check_data.crop_rotation(truth), delta=0.3)


if __name__ == "__main__":
    unittest.main()
