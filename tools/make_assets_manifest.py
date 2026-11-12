"""生成 data/assets_manifest.json：发行版首次运行时要从吉星派对 WIKI（BWIKI）下载的识别图片清单。

按开发目录 assets/ 下现有的图片列清单（heroes 角色照、profiles Q 版头像、chips 筹码图标、achieve 结算数据表上的称号图标），
每张向 BWIKI 的 imageinfo 接口要真实下载地址、大小和 SHA-1，并和本地文件核对。
本地文件名就是 BWIKI 上的文件名（下划线等同空格）。
assets/<目录>/user/ 下是用户自己存的模板（手动指定角色时保存的立绘），不进清单。
BUNDLED 里的图片 wiki 上没有，标成 bundled，随发行版附带，不下载。
MIRRORED 里的图片 wiki 上没有、或和游戏里不一样，照游戏内的图放在备用地址上，标成 mirror：url 是备用地址（地址里带
内容的 SHA-1，图换了地址也跟着换），不带 title。复刻的改版不用官方的备用地址（advisor/assets.py），这几项另带 fallback，写它
不用备用地址时怎么下（字段同普通的项）：data/card_crops.json 里有裁法的角色照照旧从卡面现裁（fallback 是卡面的 title、url、size、
sha1 加 crop），wiki 上有这张（和游戏里不一样）的照 wiki 上那张；两样都没有的不带，复刻的改版缺这一张。wiki 上有了、和备用地址上
那张一样以后，把这些文件名从 MIRRORED 去掉，重新生成清单，就改回从 wiki 下。MIRRORED 里的健全版角色照列在 data/sfw_skip.json
里的，和别的不用下载的健全版一样不进清单（见下）。

识别用不到、或有别的图顶替得了的不进清单（首次运行少下几十兆、少发一百多个请求），开发目录里照留：
- Q 版头像只收识别会读的：文件名按 advisor/vision/stars.py 的 profile_photo_hero（认头像时用的同一个判断）认出角色编号、
  编号是 data/heroes.json 里的角色的。BWIKI 上编号四位数的那些是账号头像一类，不是角色头像，识别从来不读。
- data/sfw_skip.json 里列的健全版角色照（直播模式下显示的另一版原画）：只用普通版的角色照也认得出（tools/check_sfw_cover.py
  照识别的比对方法算的）。不在名单里的健全版照常列进来，以后出的新健全版默认下载。名单里只认健全版的文件名（SFW_PHOTO，
  同 tools/check_data.py）：写进了别的（例如普通版）时报出来、不写清单，普通版都要下载，不能被名单挑掉。

BWIKI 上只有卡面原画、没有角色照的皮肤，和 BWIKI 的角色照跟游戏里不一样的皮肤：data/card_crops.json 里
列的角色照不照本地文件列，改问卡面（UT_Hero_Card_*）的地址、大小和 SHA-1，清单项的 title、url、size、sha1 是卡面的，另带
crop 字段（裁法的 6 个数，照抄 card_crops.json），首次运行时下载卡面、在本机裁出角色照（advisor/assets.py）。这些角色照
不和卡面比校验值，改核对开发目录里那张是不是按 wiki 上现在这张卡面、这份裁法裁的（assets/manifest-local.json 里
tools/fetch_assets.py --crop 记下的那笔，和首次运行时判断在不在是同一个办法，见 assets.is_present）：不是的、本地没有的，
同「本地文件和 wiki 上的不一致」处理。也在 MIRRORED 里的角色照照备用地址上的列（开发目录里那张是游戏内的图，不是裁的），裁法
只写进它的 fallback。card_crops.json、sfw_skip.json、heroes.json 读不出来、写得不对时不写清单（先运行
tools/check_data.py 看哪里不对）。

用法：.venv\\Scripts\\python tools\\make_assets_manifest.py [--force]
  有图片在 wiki 上找不到、或本地文件和 wiki 上的不一致时，打印出来并以退出码 1 结束，不写清单（免得发行版下载的
  图片和开发时测过的不一样、或少了图片）。核对无误时才写 data\\assets_manifest.json。
  --force  确实要照写时用（例如 wiki 上删了一张图）：打印的内容和平时一样，照写清单
  接口请求之间至少隔 assets.API_INTERVAL 秒（同发行版下载时的规矩）。被站点防护拦下（assets.Blocked：HTTP 567、拿到的
  是拦截页面等）或被限流（429、503）时马上停下、不再发请求，打印原因和几点几分以后再运行（assets.refusal_note），以退出码
  2 结束，不写清单：接着请求只会把封禁拖长，这段时间浏览器打开 wiki 也会被拦。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from advisor import paths  # noqa: E402
from advisor.assets import (API, API_INTERVAL, MANIFEST, WIKI_HOME, AssetItem, DownloadError, _Fetcher,  # noqa: E402
                            is_present, load_local, query_imageinfo, refusal_note)
from advisor.update import MIRROR_BASE  # noqa: E402
from advisor.vision.stars import profile_photo_hero  # noqa: E402
from tools import json_layout  # noqa: E402

KINDS = ("heroes", "profiles", "chips", "achieve")
BUNDLED = (("stars", "star_bar.png"),)  # 取自对局截图的星数横条模板，wiki 上没有
# BWIKI 上没有、或和游戏里不一样的识别图片（照游戏内核对查出来的），照游戏内的图放在备用地址上；wiki 上有了、和这里的一样
# 以后从这里去掉，改回从 wiki 下。角色照里的这几张 BWIKI 上没有角色照，原来从卡面现裁（裁法照留在 card_crops.json，复刻的改版用）。
# 其中列在 sfw_skip.json 里的健全版不下载、不放备用地址，开发目录里照样放游戏内的图：算名单（tools/check_sfw_cover.py）拿游戏里
# 显示的那张比，取图工具现裁时也不换掉它
MIRRORED = (*(("profiles", f"UT_Hero_ProfilePhoto_{skin}.png")
              for skin in ("104_03", "113_04", "114_04", "117_04", "119_03", "123_03", "124_03", "130", "130_Max")),
            *(("heroes", f"UT_Hero_RolePhoto_{skin}.png")
              for skin in ("104_03", "105_01", "106_02", "106_02_sfw", "107_03_sfw", "108", "108_02_sfw", "108_sfw",
                           "112_02_sfw", "112_03", "113_04", "114_01_sfw", "114_sfw", "117_04", "117_04_sfw", "119_03",
                           "121_01_sfw", "121_03", "121_03_sfw", "123_02", "123_03", "123_03_sfw", "123_sfw", "124_03",
                           "128_Max_sfw", "130", "130_Max", "130_Max_sfw", "130_sfw")),
            # 森秋幻境的三张专属筹码
            *(("chips", f"UT_Relic_{icon}.png") for icon in ("50094", "50095", "50096")),
            # 结算数据表上的 14 个称号图标：BWIKI 上的和游戏里的不一样（照游戏内核对），都照游戏内的图放备用地址
            *(("achieve", f"UT_Achieve_{1000 + i}_Settlement.png") for i in range(14)))
IMAGE_SUFFIXES = (".png",)
CARD_CROPS = "card_crops.json"  # data/ 下：从卡面现裁的角色照和裁法（同 tools/check_data.py 的 CARD_CROPS）
# data/ 下：不用下载的健全版角色照（同 tools/check_data.py 的 SFW_SKIP，tools/check_sfw_cover.py 算、写）
SFW_SKIP = "sfw_skip.json"
# 健全版角色照的文件名：名单里只认这样的（同 tools/check_data.py 的 _SFW_PHOTO，测试核对）
SFW_PHOTO = re.compile(r"UT_Hero_RolePhoto_\d+(?:_[0-9A-Za-z]+)*_sfw\.png")
HEROES = "heroes.json"  # data/ 下：角色（Q 版头像只收这里的角色编号的）
MB = 1_000_000  # 打印的「MB」按十进制算，同使用说明里写的首次下载多少兆


def local_files(kind: str) -> list[tuple[str, str]]:
    """[(本地文件名, wiki 上的文件页名)]：文件页名就是文件名，下划线换成空格。"""
    folder = paths.assets_dir(kind)
    if not folder.is_dir():
        return []
    return [(f.name, f.name.replace("_", " ")) for f in sorted(folder.iterdir())
            if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES]


def read_sfw_skip() -> set[str] | None:
    """data/sfw_skip.json 的 photos：不用下载的健全版角色照的文件名。没有这个文件时为空（健全版都下）；读不出来、不是有
    photos 表的表时为 None（细的核对在 tools/check_data.py）。"""
    path = paths.data_dir() / SFW_SKIP
    if not path.exists():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    photos = raw.get("photos") if isinstance(raw, dict) else None
    return set(photos) if isinstance(photos, dict) else None


def hero_ids() -> set[int] | None:
    """data/heroes.json 里的角色编号（Q 版头像只收这些编号的）；读不出来、一个编号都没有时为 None。"""
    try:
        heroes = json.loads((paths.data_dir() / HEROES).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(heroes, list):
        return None
    ids = {h["id"] for h in heroes
           if isinstance(h, dict) and isinstance(h.get("id"), int) and not isinstance(h["id"], bool)}
    return ids or None


def read_by_recognition(name: str, heroes: set[int]) -> bool:
    """这张 Q 版头像识别会不会读：文件名认得出角色编号（和认头像同一个判断，advisor/vision/stars.py 的 profile_photo_hero），
    而且是 heroes 里的角色。"""
    return profile_photo_hero(name) in heroes


def read_crops() -> dict[str, tuple[str, list]] | None:
    """data/card_crops.json → {角色照的文件名: (卡面在 wiki 上的文件名（下划线换成空格）, 裁法的 6 个数)}。没有这个文件时
    为空表；读不出来、结构不对、哪一项不是「卡面的文件名 + 6 个数」时为 None（细的核对在 tools/check_data.py）。"""
    path = paths.data_dir() / CARD_CROPS
    if not path.exists():
        return {}
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("crops")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(rows, dict):
        return None
    out = {}
    for photo, entry in rows.items():
        card = entry.get("card") if isinstance(entry, dict) else None
        affine = entry.get("affine") if isinstance(entry, dict) else None
        if not (isinstance(card, str) and card and isinstance(affine, list) and len(affine) == 6
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in affine)):
            return None
        out[photo] = (card.replace("_", " "), affine)
    return out


def sha1_of(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true",
                    help="有对不上的也照写清单（平时不写：先查清为什么对不上）")
    args = ap.parse_args()

    crops, skip, heroes = read_crops(), read_sfw_skip(), hero_ids()
    for value, name, why in ((crops, CARD_CROPS, "读不出来或写得不对"), (skip, SFW_SKIP, "读不出来或写得不对"),
                             (heroes, HEROES, "读不出来（认不出哪些 Q 版头像是角色的）")):
        if value is None:
            print(f"data/{name} {why}，没写清单：先运行 tools/check_data.py 看哪里不对。")
            return 1
    not_sfw = sorted(name for name in skip if not SFW_PHOTO.fullmatch(name))
    if not_sfw:  # 名单只列健全版：写进了普通版等别的，照它挑会把要下载的图挑掉（模块说明）
        print(f"data/{SFW_SKIP} 里有不是健全版角色照的文件名（应照 UT_Hero_RolePhoto_<编号>[_<皮肤>]_sfw.png 写）："
              + "、".join(not_sfw) + "。名单只列不用下载的健全版，普通版都要下载；没写清单：先改好名单，"
                                     "tools/check_data.py 也会报出来。")
        return 1
    files = {kind: local_files(kind) for kind in KINDS}
    mirrored = set(MIRRORED)
    # 备用地址上的角色照里列在 sfw_skip.json 的健全版同样不进清单（模块说明：名单里的不下载，tools/check_data.py 也这样核）
    listed_mirror = [(kind, name) for kind, name in MIRRORED if not (kind == "heroes" and name in skip)]
    # 备用地址上的图片本地必须有（照游戏内的图放进来）；Q 版头像照样过「识别会读的」那道筛
    mirrored_bad = [f"{kind}/{name}" for kind, name in listed_mirror
                    if kind == "profiles" and not read_by_recognition(name, heroes)]
    mirrored_missing, mirrored_meta = [], {}
    for kind, name in listed_mirror:
        path = paths.assets_dir(kind) / name
        if path.is_file() and path.stat().st_size > 0:
            mirrored_meta[(kind, name)] = (path.stat().st_size, sha1_of(path))
        else:
            mirrored_missing.append(f"{kind}/{name}")
    if mirrored_bad or mirrored_missing:
        if mirrored_bad:
            print("MIRRORED 里的 Q 版头像识别不会读（文件名认不出角色编号，或编号不是 data/heroes.json 里的角色）："
                  + "、".join(mirrored_bad) + "。")
        if mirrored_missing:
            print("MIRRORED 里的图片本地没有（应放在 assets/<dir>/ 下，照游戏内的图）：" + "、".join(mirrored_missing) + "。")
        print("没写清单。")
        return 1
    # 识别用不到、或有别的图顶替得了的不列（模块说明）：开发目录里照留
    dropped_sfw = sorted(skip & ({local for local, _ in files["heroes"]} | set(crops)))
    dropped_profiles = [local for local, _ in files["profiles"] if not read_by_recognition(local, heroes)]
    # 从卡面现裁的角色照问卡面（不论本地有没有），其余照本地文件列；备用地址上的几张另按 MIRRORED 列
    wanted = [(kind, local, title) for kind in KINDS for local, title in files[kind]
              if (kind, local) not in mirrored
              and not (kind == "heroes" and (local in crops or local in skip))
              and not (kind == "profiles" and local in dropped_profiles)]
    wanted += [("heroes", photo, card) for photo, (card, _) in sorted(crops.items())
               if photo not in skip and ("heroes", photo) not in mirrored]
    # 备用地址上的角色照里原来从卡面现裁的：复刻的改版照旧裁（fallback），也要问卡面
    mirrored_cards = {name: crops[name] for kind, name in listed_mirror if kind == "heroes" and name in crops}
    fetcher = _Fetcher(interval=API_INTERVAL)
    try:
        # 备用地址上的几张也一起问（同一批请求）：wiki 上有了、和备用地址上一样的要提醒去掉，不一样的给复刻的改版照旧下 wiki 上那张
        titles = ({title for _, _, title in wanted} | {name.replace("_", " ") for _, name in listed_mirror}
                  | {card for card, _ in mirrored_cards.values()})
        info = query_imageinfo(sorted(titles), fetcher.get, API)
    except DownloadError as e:
        note = refusal_note(e)
        if note is None:
            raise
        print(note)
        print("没写清单。")
        return 2
    finally:
        fetcher.close()

    items, not_found, differs, uncut = [], [], [], []
    records = load_local()  # 开发目录里现裁的角色照是按哪张卡面、哪份裁法裁的（tools/fetch_assets.py --crop 记下）
    for kind, local, title in wanted:
        meta = info.get(title)
        if not meta:
            not_found.append(f"{kind}/{local}" + (f"（卡面 {title}）" if kind == "heroes" and local in crops else ""))
            continue
        item = {
            "dir": kind, "file": local, "title": title, "url": meta["url"],
            "size": int(meta["size"]), "sha1": meta["sha1"],
        }
        if kind == "heroes" and local in crops:
            item["crop"] = crops[local][1]
            asset = AssetItem(kind, local, title, item["url"], item["size"], item["sha1"], crop=tuple(item["crop"]))
            if not is_present(asset, records):
                uncut.append(f"{kind}/{local}")
        elif sha1_of(paths.assets_dir(kind) / local) != meta.get("sha1"):
            differs.append(f"{kind}/{local}")
        items.append(item)
    for kind, name in listed_mirror:
        size, digest = mirrored_meta[(kind, name)]
        item = {"dir": kind, "file": name, "mirror": True,
                "url": f"{MIRROR_BASE}assets/{kind}/{digest}/{name}", "size": size, "sha1": digest}
        own = info.get(name.replace("_", " "))  # wiki 上的这张
        if own and own.get("sha1") == digest:
            print(f"wiki 上已经有了 {name}，和备用地址上的一样：可以从 MIRRORED 去掉，改从 wiki 下")
        if name in mirrored_cards:  # 复刻的改版照旧从卡面现裁
            card, affine = mirrored_cards[name]
            meta = info.get(card)
            if meta:
                item["fallback"] = {"title": card, "url": meta["url"], "size": int(meta["size"]), "sha1": meta["sha1"],
                                    "crop": affine}
            else:
                not_found.append(f"{kind}/{name}（复刻的改版现裁用的卡面 {card}）")
        elif own:  # 复刻的改版照旧下 wiki 上那张
            item["fallback"] = {"title": name.replace("_", " "), "url": own["url"], "size": int(own["size"]),
                                "sha1": own["sha1"]}
        items.append(item)
    for kind, local in BUNDLED:
        path = paths.assets_dir(kind) / local
        if path.exists():
            items.append({"dir": kind, "file": local, "bundled": True, "size": path.stat().st_size,
                          "sha1": sha1_of(path)})
        else:
            not_found.append(f"{kind}/{local}（随附图片缺失）")

    counts = {kind: sum(1 for i in items if i["dir"] == kind and not i.get("bundled")) for kind in KINDS}
    total_bytes = sum(i["size"] for i in items if not i.get("bundled"))
    cards = [i for i in items if "crop" in i]
    on_mirror = [i for i in items if i.get("mirror")]
    print(f"清单 {len(items)} 项：" + "、".join(f"{k} {v}" for k, v in counts.items())
          + f"（其中从卡面现裁 {len(cards)}，备用地址上 {len(on_mirror)}：复刻的改版不用备用地址，其中 "
          + f"{sum(1 for i in on_mirror if 'fallback' in i)} 张照旧从卡面现裁或下 wiki 上的，其余没有别的下法），"
          + f"随附 {sum(1 for i in items if i.get('bundled'))}；"
          + f"需下载约 {total_bytes / MB:.1f} MB（其中卡面约 {sum(i['size'] for i in cards) / MB:.1f} MB）")
    print(f"识别用不到、或有别的图顶替得了，不进清单：健全版 {len(dropped_sfw)} 张（data/{SFW_SKIP} 里列的）、"
          f"Q 版头像 {len(dropped_profiles)} 张（不是 data/{HEROES} 里的角色的）")
    if not_found:
        print(f"wiki 上找不到（不进清单）{len(not_found)} 张：" + "、".join(not_found[:20]))
    if differs:
        print(f"本地文件和 wiki 上的不一致（照写时按 wiki 写）{len(differs)} 张：" + "、".join(differs[:20]))
    if uncut:
        print(f"开发目录里的角色照不是按 wiki 上现在的卡面、data/{CARD_CROPS} 里的裁法裁的（或还没有），"
              f"先运行 tools/fetch_assets.py --crop（照写时按 wiki 的卡面写）{len(uncut)} 张：" + "、".join(uncut[:20]))
    if (not_found or differs or uncut) and not args.force:
        print("没写清单：确实要照写时加 --force")
        return 1

    manifest = {
        "version": 1,
        "generated": date.today().isoformat(),
        "source": WIKI_HOME,
        "api": API,
        "note": "识别用图片：程序首次运行时下载到 assets/<dir>/<file>，一般取自 source（mirror 项除外，见下）。"
                "带 crop 字段的项下载的是对应的卡面原画（title、url、size、sha1 是卡面的），在本机按 crop 的裁法（照游戏内"
                "核对）裁出角色照，卡面不留。"
                "bundled 项随发行版附带，不下载。"
                "mirror 项是 WIKI 上没有、或和游戏里不一样的图片，从备用地址下载（url 里带内容的 SHA-1）；"
                "不用官方备用地址的复刻版本改照 fallback 下（字段同普通的项），没有 fallback 的不下。",
        "counts": counts,
        "download_bytes": total_bytes,
        "items": sorted(items, key=lambda i: (i["dir"], i["file"])),
    }
    out = paths.data_dir() / MANIFEST
    out.write_text(json_layout.format_json(manifest), encoding="utf-8")
    print(f"已写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
