"""从 wiki 下载角色图片（含各皮肤），用于识别当前角色和玩家列表里的头像。

文件名规则：UT_Hero_<类型>_<角色编号>[_<皮肤>][_sfw].png（皮肤：_01、_02…按 BWIKI 角色页「皮肤名N」的顺序，_Max 是羁绊皮肤；
_sfw 是游戏里另一版原画）
  RolePhoto     角色照，和对局里左下角那张立绘出自同一张原画，advisor/vision/hero.py 拿它比对。原样保存到 assets/heroes。
  Bust          半身立绘，姿势和局内那张对不上，识别用不到。原图很大，缩到宽 512 再保存到 assets/heroes。
  ProfilePhoto  Q 版头像，玩家列表里用的。原样保存到 assets/profiles。

图片都取自中文 BWIKI（吉星派对 WIKI）。角色照在开发目录里下全（健全版也下：tools/check_sfw_cover.py 要拿全部角色照算，
不用下载的健全版由 tools/make_assets_manifest.py 挑掉）；Q 版头像和清单一个口径，只下识别会读的（文件名认得出角色编号、
是 data/heroes.json 里的角色的，同 make_assets_manifest.read_by_recognition），编号四位数的那些（账号头像一类）识别从来
不读，不下。

请求走首次运行的下载用的取图器（advisor/assets.py 的 _Fetcher：系统自带的 WinHTTP，User-Agent 写明本工具的名称和用途，
不冒充浏览器）。开发时偶尔用，不赶时间：接口请求之间至少隔 API_INTERVAL 秒，下载图片之间至少隔 SLOW_INTERVAL 秒（wiki 前面
的站点防护按电脑的网络地址拦截，请求密了会被拦）。被站点防护拦下（HTTP 567、拿到拦截页面等）或被限流（429、503）时马上停下、
不再发请求，打印原因和几点几分以后再运行，以退出码 2 结束：接着请求只会把封禁拖长，这段时间用浏览器打开 wiki 也会被拦。
停下之前已经下好、写进文件的照留。别的出错（404、网络出错等）照旧报错退出。

从卡面现裁的角色照：BWIKI 上只有卡面原画（UT_Hero_Card_*）、没有角色照的皮肤，和 BWIKI 的角色照跟游戏里
不一样的皮肤，裁法记在 data/card_crops.json。加 --crop 时只做这一件：按它把卡面裁成角色照存进 assets/heroes（advisor/vision/cardcrop.py，和首次
运行时下载以后裁的是同一个函数），并在 assets/manifest-local.json 记下按哪张卡面（SHA-1）、哪份裁法裁的（assets.note_crop：
开发目录也和首次运行时一样按它判断在不在，tools/make_assets_manifest.py 照它核对）。卡面只在内存里，不落盘。
  --cards 文件夹  卡面从这个文件夹读（文件名同 BWIKI）。按文件夹里的 imageinfo.json（BWIKI imageinfo 接口给的
                  {卡面文件名: {url, size, sha1}}）核对 SHA-1，里面没有这张时按清单（data/assets_manifest.json）里这张角色照
                  的那一项核对；两样都没有、对不上的不裁。不连网。
  不给 --cards    向 BWIKI 的接口要卡面的地址和 SHA-1，下载卡面（间隔、被拦就停同上），按接口给的核对。
  已经按同一张卡面、同一份裁法裁好的跳过；旧的（以前下的 wiki 角色照、裁法改了、卡面换了）重裁。写的时候先写临时文件、
  再整份换上（advisor/fileio.py），不在原来的文件上改：开发目录里的图片可能是指向别处的硬链接，就地写会改到那一份。
  某一张裁不出（卡面解不开、裁法离谱到算不出来）时报出这一张、其余照裁；有张数没裁成时以退出码 1 结束。
  放在备用地址上的角色照（tools/make_assets_manifest.py 的 MIRRORED）不裁：开发目录里那张是游戏内的图，裁法照留在表里，
  只给不用官方备用地址的复刻版本首次运行时现裁。
普通的下载（RolePhoto）跳过 card_crops.json 里列的角色照：它们按卡面现裁（或放在备用地址上），BWIKI 上同名的角色照（有的话）
和游戏里的不一样。

用法：.venv\\Scripts\\python tools\\fetch_assets.py [类型 ...] [--hero 编号 ...]
      类型默认只有 RolePhoto；已下载的文件会跳过。新角色上线后加 --hero 编号 补下它的各皮肤。
      .venv\\Scripts\\python tools\\fetch_assets.py --crop [--cards 文件夹] [--hero 编号 ...]
      按 data/card_crops.json 现裁角色照（见上）。
      下载、替换、现裁了图片后，运行 tools\\make_assets_manifest.py 更新发行版的下载清单。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # 让 advisor 包可以导入
    sys.path.insert(0, str(ROOT))

from advisor import assets, fileio, paths  # noqa: E402
from advisor.assets import (API, API_INTERVAL, SLOW_INTERVAL, DownloadError, _Fetcher,  # noqa: E402
                            query_imageinfo, refusal_note)
from advisor.version import SOURCE_URL  # noqa: E402
from advisor.vision import cardcrop  # noqa: E402
from tools import make_assets_manifest  # noqa: E402
from tools.make_assets_manifest import HEROES, hero_ids, read_by_recognition  # noqa: E402

OUT = {"RolePhoto": ROOT / "assets" / "heroes", "Bust": ROOT / "assets" / "heroes",
       "ProfilePhoto": ROOT / "assets" / "profiles"}
# 写明工具名称、源代码的地址和用途，不冒充浏览器（和 advisor/assets.py 的下载器一致）
USER_AGENT = f"AstralChipCompass-maintenance (+{SOURCE_URL}; Astral Party co-op chip helper; asset preparation)"
BUST_WIDTH = 512
BACKDROP = (40, 34, 34)  # 立绘是透明底，垫上和游戏里相近的深色
CARD_CROPS = "card_crops.json"  # data/ 下：从卡面现裁的角色照和裁法（同 tools/check_data.py 的 CARD_CROPS）
CARD_INFO = "imageinfo.json"  # --cards 文件夹里：BWIKI imageinfo 接口给的卡面地址、大小和 SHA-1


def list_images(prefix: str, ask: Callable[[str], bytes]) -> list[dict]:
    """BWIKI 上以 prefix 开头的图片（allimages，每页 500 张；ask 是接口的取图器）。"""
    images, cont = [], None
    while True:
        params = {
            "action": "query", "list": "allimages", "aiprefix": prefix,
            "ailimit": "500", "aiprop": "url|size", "format": "json",
        }
        if cont:
            params["aicontinue"] = cont
        data = json.loads(ask(API + "?" + urllib.parse.urlencode(params)))
        images += data["query"]["allimages"]
        cont = data.get("continue", {}).get("aicontinue")
        if not cont:
            return images


def shrink_bust(raw: bytes) -> bytes:
    img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if img.ndim == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3:4].astype(np.float32) / 255
        backdrop = np.empty_like(img[:, :, :3])
        backdrop[:] = BACKDROP
        img = (img[:, :, :3] * alpha + backdrop * (1 - alpha)).astype(np.uint8)
    h, w = img.shape[:2]
    img = cv2.resize(img, (BUST_WIDTH, round(h * BUST_WIDTH / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes()


def fetch_kind(kind: str, heroes: set[str], ask: Callable[[str], bytes], fetch: Callable[[str], bytes],
               cropped: frozenset[str] = frozenset(), keep: Callable[[str], bool] | None = None) -> None:
    """下载一类图片（RolePhoto、Bust、ProfilePhoto）里本地还没有的；heroes 不空时只下这几个角色的。ask、fetch 是接口和图片的
    取图器。cropped 是按卡面现裁的角色照（card_crops.json 里列的），不在这里下（见模块说明）。keep 不为 None 时只下它说要
    的（Q 版头像：识别会读的，见模块说明），别的不下、记个数。中途停下（被拦、出错）时照样打印已经下了几张。"""
    out = OUT[kind]
    out.mkdir(parents=True, exist_ok=True)
    done = skipped = unread = 0
    try:
        for image in list_images(f"UT_Hero_{kind}_", ask):
            name = image["name"].replace(" ", "_")
            m = re.match(rf"UT_Hero_{kind}_(\d+)", name)
            if not m or (heroes and m.group(1) not in heroes) or name in cropped:
                continue
            if keep is not None and not keep(name):
                unread += 1
                continue
            target = out / name
            if target.exists():
                skipped += 1
                continue
            raw = fetch(image["url"])
            target.write_bytes(shrink_bust(raw) if kind == "Bust" else raw)
            done += 1
    finally:
        left_out = f"，识别不读、没下 {unread} 张" if unread else ""
        print(f"{kind}：下载 {done} 张，已有 {skipped} 张{left_out} → {out}")


def read_crops() -> dict[str, tuple[str, list]]:
    """data/card_crops.json 的 crops：{角色照的文件名: (卡面的文件名, 裁法的 6 个数)}。没有这个文件时为空表；读不出来、
    写得不对时抛 ValueError（细的核对在 tools/check_data.py）。"""
    path = paths.data_dir() / CARD_CROPS
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.get("crops") if isinstance(raw, dict) else None
    if not isinstance(rows, dict):
        raise ValueError("最外层应是有 crops 的表")
    out = {}
    for photo, entry in rows.items():
        card = entry.get("card") if isinstance(entry, dict) else None
        affine = entry.get("affine") if isinstance(entry, dict) else None
        if not (isinstance(card, str) and isinstance(affine, list) and len(affine) == 6):
            raise ValueError(f"「{photo}」应写成卡面的文件名和 6 个数")
        out[photo] = (card, affine)
    return out


def _manifest_cards() -> dict[str, dict]:
    """识别图片清单里从卡面现裁的各项：{角色照的文件名: 清单项}（--cards 文件夹里没有 imageinfo 时按它核对卡面）。"""
    try:
        raw = json.loads((paths.data_dir() / assets.MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = raw.get("items") if isinstance(raw, dict) else None
    return {i["file"]: i for i in items or () if isinstance(i, dict) and i.get("dir") == "heroes" and "crop" in i
            and isinstance(i.get("file"), str)}


def crop_photos(crops: dict[str, tuple[str, list]], heroes: set[str], cards: Path | None,
                ask: Callable[[str], bytes] | None = None, fetch: Callable[[str], bytes] | None = None) -> int:
    """按 card_crops.json 现裁角色照存进 assets/heroes（模块说明「从卡面现裁的角色照」），返回没裁成的张数。heroes 不空时只
    裁这几个角色的。cards 给了时卡面从这个文件夹读、不连网；没给时 ask、fetch 是接口和图片的取图器（被拦、限流时抛
    DownloadError，由 main 停下）。中途停下时已经裁好的照留。"""
    out = paths.assets_dir("heroes")
    out.mkdir(parents=True, exist_ok=True)

    def hero_of(photo: str) -> str | None:
        m = re.match(r"UT_Hero_RolePhoto_(\d+)", photo)
        return m.group(1) if m else None

    # 放在备用地址上的不裁（模块说明）：开发目录里那张是游戏内的图，裁了会把它换掉，生成清单时备用地址上的就成了裁的
    mirrored = {name for kind, name in make_assets_manifest.MIRRORED if kind == "heroes"}
    todo = {photo: crop for photo, crop in sorted(crops.items())
            if (not heroes or hero_of(photo) in heroes) and photo not in mirrored}
    if mirrored & set(crops):
        print(f"放在备用地址上、不裁的 {len(mirrored & set(crops))} 张（开发目录里是游戏内的图）")
    if cards is None:
        asked = query_imageinfo(sorted({card.replace("_", " ") for card, _ in todo.values()}), ask)

        def reference(photo: str, card: str) -> dict | None:
            return asked.get(card.replace("_", " "))
        where = "BWIKI 上没有这张卡面"
    else:
        try:
            given = json.loads((cards / CARD_INFO).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            given = {}
        listed = _manifest_cards()

        def reference(photo: str, card: str) -> dict | None:
            info = given.get(card) if isinstance(given, dict) else None
            if isinstance(info, dict) and isinstance(info.get("sha1"), str):
                return info
            item = listed.get(photo)
            return item if item is not None and item.get("title") == card.replace("_", " ") else None
        where = f"{cards} 里的 {CARD_INFO} 和识别图片清单里都没有这张卡面的校验值"
    local = assets.load_local()
    done = skipped = failed = 0
    try:
        for photo, (card, affine) in todo.items():
            ref = reference(photo, card)
            if ref is None:
                print(f"核对不了 {card}（{where}），{photo} 没裁")
                failed += 1
                continue
            item = assets.AssetItem("heroes", photo, card.replace("_", " "), str(ref.get("url") or ""),
                                    int(ref.get("size") or 0), ref["sha1"], crop=tuple(affine))
            if assets.is_present(item, local):
                skipped += 1
                continue
            if cards is None:
                data = fetch(item.url)
            else:
                try:
                    data = (cards / card).read_bytes()
                except OSError as e:
                    print(f"读不到 {cards / card}（{type(e).__name__}），{photo} 没裁")
                    failed += 1
                    continue
            if hashlib.sha1(data).hexdigest() != item.sha1 or (item.size and len(data) != item.size):
                print(f"{card} 的大小或 SHA-1 和{'接口' if cards is None else '记下'}的对不上（wiki 上可能换过这张卡面），"
                      f"{photo} 没裁")
                failed += 1
                continue
            try:
                photo_bytes = cardcrop.crop_png(data, affine)
            except (ValueError, cv2.error) as e:  # CropError 之外，离谱的裁法算到半路也会报（NaN 转不成整数等）：只算这一张
                print(f"{card} 裁不出角色照（{e}），{photo} 没裁")
                failed += 1
                continue
            # 先写临时文件再整份换上：不在原来的文件上改（它可能是硬链接），写到一半出错时原来的照旧
            fileio.write_bytes(out / photo, photo_bytes, suffix=".part")
            assets.note_crop(f"heroes/{photo}", item.sha1, affine, len(photo_bytes))
            done += 1
    finally:
        print(f"从卡面现裁角色照 {done} 张，已是最新 {skipped} 张，没裁成 {failed} 张 → {out}")
    return failed


def main() -> int:
    """下载各类图片，或按 card_crops.json 现裁角色照。返回退出码：0 照常，1 有角色照没裁成（--crop）、表读不出来
    （card_crops.json；要下 Q 版头像时的 heroes.json），2 被 wiki 拒绝、停下了（模块说明）。"""
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="从 wiki 下载角色图片；加 --crop 时按 data/card_crops.json 从卡面现裁角色照。")
    ap.add_argument("kinds", nargs="*", metavar="类型", help="RolePhoto（默认）、Bust、ProfilePhoto")
    ap.add_argument("--hero", nargs="+", default=[], metavar="编号", help="只下（只裁）这几个角色的")
    ap.add_argument("--crop", action="store_true", help="按 data/card_crops.json 从卡面现裁角色照（只做这一件）")
    ap.add_argument("--cards", metavar="文件夹", help="和 --crop 一起用：卡面从这个文件夹读，不连网")
    args = ap.parse_args()
    unknown = [k for k in args.kinds if k not in OUT]
    if unknown:
        ap.error(f"不认识的类型：{'、'.join(unknown)}（可选 {'、'.join(OUT)}）")
    if args.crop and args.kinds:
        ap.error("--crop 只做现裁，不和类型一起用")
    if args.cards and not args.crop:
        ap.error("--cards 要和 --crop 一起用")
    try:
        crops = read_crops()
    except (OSError, ValueError) as e:
        print(f"data/{CARD_CROPS} 读不出来或写得不对（{e}），先运行 tools/check_data.py 看哪里不对。")
        return 1
    heroes = set(args.hero)
    if args.crop and args.cards:
        return 1 if crop_photos(crops, heroes, Path(args.cards)) else 0
    kinds = args.kinds or ["RolePhoto"]
    ids = None
    if "ProfilePhoto" in kinds:  # Q 版头像只下识别会读的（模块说明）：先认得出哪些编号是角色，再连网
        ids = hero_ids()
        if ids is None:
            print(f"data/{HEROES} 读不出来（认不出哪些 Q 版头像是角色的），没下载：先运行 tools/check_data.py 看哪里不对。")
            return 1

    # 接口和图片各一条连接，各按自己的间隔（模块说明）
    ask = _Fetcher(interval=API_INTERVAL, agent=USER_AGENT)
    fetch = _Fetcher(interval=SLOW_INTERVAL, agent=USER_AGENT)
    try:
        if args.crop:
            return 1 if crop_photos(crops, heroes, None, ask.get, fetch.get) else 0
        for kind in kinds:
            keep = (lambda name: read_by_recognition(name, ids)) if kind == "ProfilePhoto" else None
            fetch_kind(kind, heroes, ask.get, fetch.get, frozenset(crops) if kind == "RolePhoto" else frozenset(), keep)
    except DownloadError as e:
        note = refusal_note(e)
        if note is None:
            raise
        print(note)
        return 2
    finally:
        ask.close()
        fetch.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
