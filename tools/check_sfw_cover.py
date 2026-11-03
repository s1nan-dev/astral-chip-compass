"""算哪些健全版角色照首次运行不用下载：只用普通版的角色照也认得出。名单记在 data/sfw_skip.json。

健全版（文件名带 _sfw）是直播模式（游戏设置里的开关）下显示的另一版原画。认角色只比对角色照的脸部区域，不少健全版的脸部
和同一套的普通版一样或很像，不下也认得出；省下的多是从卡面现裁的那些（一张卡面一两兆）。口径照认角色的比对方法
（advisor/vision/hero.py）：

- 模板：开发目录 assets/heroes 里全部普通版角色照（不带 _sfw）的脸部区域，取法同认角色时建模板（hero._load_templates：
  data/photo_tilt.json 里斜着摆的先转过来）。
- 每张健全版同样取脸部区域，缩到 SCALES 里的每一种大小（对局画面里立绘常见的缩放），四周按边上的像素补 PAD 像素（乘缩放，
  至少 2 像素）的余量，和缩成同样大小的每个模板比（cv2.TM_CCOEFF_NORMED，取最大值）；两种缩放里取低的那个分数。
- 本人（同一角色编号）的普通版里最高的分数不低于 OWN_MIN，而且别的角色里最高的不高于 RIVAL_MAX，才算认得出、不用下载。
  OWN_MIN 是认角色的门槛（hero.MIN_SCORE）加 HEADROOM 的余量，RIVAL_MAX 再减去第一名要领先第二名的差（hero.MIN_MARGIN）。

名单里是「不用下载」的（photos：角色照的文件名 → [本人的最高分, 别的角色的最高分]，保留三位小数）：不在名单里的健全版照常
下载，以后出的新健全版默认也下载。生成识别图片清单（tools/make_assets_manifest.py）时跳过名单里的；从卡面现裁的那些，
裁法照留在 data/card_crops.json，以后要下时从名单里拿掉、重新生成清单就行。开发目录里的健全版都留着（tools/fetch_assets.py
照常下、照常裁），本工具才算得了。

用法：.venv\\Scripts\\python tools\\check_sfw_cover.py [--write]
  打印每张健全版的两个分数（各是和哪一张比出来的）、要不要下，再打印和 data/sfw_skip.json 现有名单的差别；默认只打印。
  --write  照算出来的写进 data/sfw_skip.json（说明照留，换行照原文件；内容没变时不写）。有角色照读不出来时不写。
  开发目录里加了普通版或健全版角色照（出了新皮肤）以后都重算一遍：新的普通版可能让别的角色的分数变高。写完运行
  tools/make_assets_manifest.py 重新生成清单，再运行 tools/check_data.py 核对。
以退出码 1 结束：开发目录里没有角色照（普通版或健全版一张都没有）、有角色照读不出来、data/sfw_skip.json 读不出来。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # 让 advisor 包可以导入
    sys.path.insert(0, str(ROOT))

from advisor import fileio, paths  # noqa: E402
from advisor.vision import hero  # noqa: E402
from tools import json_layout  # noqa: E402

SFW_SKIP = "sfw_skip.json"  # data/ 下：不用下载的健全版（同 tools/check_data.py、make_assets_manifest.py 的 SFW_SKIP）
SFW_MARK = "_sfw"  # 健全版的文件名带它（UT_Hero_RolePhoto_<编号>[_<皮肤>]_sfw.png）
SCALES = (0.4, 0.6)  # 脸部区域缩到原来的这么大再比：对局画面里立绘常见的缩放
PAD = 4  # 健全版四周补的余量（像素，乘缩放、至少 2）：模板可以错开几个像素再比
HEADROOM = 0.07  # 本人的分数在认角色的门槛之上要留的余量
OWN_MIN = round(hero.MIN_SCORE + HEADROOM, 2)  # 0.93
RIVAL_MAX = round(OWN_MIN - hero.MIN_MARGIN, 2)  # 0.83
DIGITS = 3  # 名单里的分数保留几位小数
CRLF = b"\r\n"  # data/ 下的 JSON 是 CRLF：新建这个文件时也这样写
# 新建名单时写的说明。和 data/ 下别的表一样不写源码路径（tests/test_project_consistency.py 的 DevNoteTest），工具和步骤
# 指到软件维护说明
NOTE = ("首次运行不下载的健全版角色照（直播模式下显示的另一版原画，文件名带 _sfw）：只用普通版的角色照也认得出，照识别的比对方法"
        "核过。photos 的键是角色照的文件名（开发目录 assets/heroes 里的；从卡面现裁的那些，裁法在 card_crops.json），值是核对时的"
        "两个分数 [本人, 别的角色]：脸部区域在 0.4、0.6 两种缩放下和全部普通版的角色照比，取低的那个；本人的普通版里最像的一张"
        "不低于 0.93（认角色的门槛 0.86 加余量）、别的角色里最像的一张不高于 0.83（第一名要领先 0.10）才列进来。不在这里的健全版"
        "照常下载，以后出的新健全版默认也下载。生成识别图片清单时跳过这里列的；从卡面现裁的那几张，裁法照留在 card_crops.json，"
        "以后要下时从这里拿掉、重新生成清单。开发目录里加了普通版或健全版的角色照以后要重算，怎么算、改完跑什么见软件维护说明"
        "「出了新的健全版」。只给开发用的工具读，不随发行版。")


@dataclass(frozen=True)
class Cover:
    """一张健全版角色照只用普通版比出来的结果。"""

    name: str  # 健全版角色照的文件名
    own: float  # 本人（同一角色编号）的普通版里最高的分数；本人一张普通版都没有时为 -1
    own_by: str  # 是哪一张普通版比出来的
    rival: float  # 别的角色的普通版里最高的分数
    rival_by: str

    @property
    def covered(self) -> bool:
        """认得出、不用下载：本人的分数不低于 OWN_MIN，别的角色的不高于 RIVAL_MAX。"""
        return self.own >= OWN_MIN and self.rival <= RIVAL_MAX

    def scores(self) -> list[float]:
        """名单里记的两个分数。"""
        return [round(self.own, DIGITS), round(self.rival, DIGITS)]


def is_sfw(name: str) -> bool:
    return Path(name).stem.endswith(SFW_MARK)


def load_faces() -> tuple[dict[str, tuple[int, np.ndarray]], list[str]]:
    """开发目录 assets/heroes 里的角色照 → {文件名: (角色编号, 脸部的灰度图)}，取法同认角色时建模板（hero._load_templates，
    斜着摆的先转过来；用户存的模板不算）；另返回读不出来的文件名。读之前、读之后都清掉认角色的模板缓存，不和识别混用。"""
    hero.reload_templates()
    try:
        loaded = {t.name: t for t in hero._load_templates() if t.photo_width is not None}
    finally:
        hero.reload_templates()
    folder = hero.heroes_dir()
    names = sorted(p.name for p in folder.glob("UT_Hero_RolePhoto_*.png")) if folder.is_dir() else []
    return {n: (t.hero_id, t.face) for n, t in loaded.items()}, [n for n in names if n not in loaded]


def similarity(query: np.ndarray, template: np.ndarray, scales=SCALES) -> float:
    """健全版的脸部（query）和一个模板的脸部比：每种缩放下把 query 缩小、四周补余量，模板缩成和 query 一样大，取模板在
    余量里错开的各个位置里最高的相关度；几种缩放里取低的那个。比不了时为 -1：缩小以后不到 2 像素；模板整片一个颜色
    （OpenCV 这时给的相关度是 1，不能算像，不然这一张会被当成认得出、或者挡住别的健全版）；相关度算不出来。"""
    low = 1.0
    for s in scales:
        q = cv2.resize(query, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        if min(q.shape[:2]) < 2:
            return -1.0
        t = cv2.resize(template, (q.shape[1], q.shape[0]), interpolation=cv2.INTER_AREA)
        if int(t.max()) == int(t.min()):
            return -1.0
        pad = max(2, round(PAD * s))
        wide = cv2.copyMakeBorder(q, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
        found = np.nan_to_num(cv2.matchTemplate(wide, t, cv2.TM_CCOEFF_NORMED), nan=-1.0, posinf=-1.0, neginf=-1.0)
        low = min(low, float(found.max()))
    return low


def covers(faces: dict[str, tuple[int, np.ndarray]]) -> list[Cover]:
    """每张健全版只用普通版比出来的结果（按文件名排）。"""
    normal = {n: v for n, v in faces.items() if not is_sfw(n)}
    out = []
    for name in sorted(n for n in faces if is_sfw(n)):
        hero_id, face = faces[name]
        own = rival = (-1.0, "")
        for other, (other_id, template) in sorted(normal.items()):
            score = (similarity(face, template), other)
            if other_id == hero_id:
                own = max(own, score)
            else:
                rival = max(rival, score)
        out.append(Cover(name, own[0], own[1], rival[0], rival[1]))
    return out


def skip_path() -> Path:
    return paths.data_dir() / SFW_SKIP


def read_skip() -> dict | None:
    """data/sfw_skip.json 的内容（note、photos）；没有这个文件时为空表；读不出来、不是有 photos 表的表时为 None。"""
    path = skip_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) and isinstance(raw.get("photos"), dict) else None


def differences(old: dict, new: dict) -> list[str]:
    """名单前后的差别（给人看的几行）：多了、少了哪几张，分数变了的几张。"""
    lines = []
    added, removed = sorted(set(new) - set(old)), sorted(set(old) - set(new))
    if added:
        lines.append(f"新列进名单（不用下了）{len(added)} 张：" + "、".join(added))
    if removed:
        lines.append(f"从名单里拿掉（要下了）{len(removed)} 张：" + "、".join(removed))
    changed = [n for n in sorted(set(old) & set(new)) if old[n] != new[n]]
    if changed:
        lines.append(f"分数变了 {len(changed)} 张：" + "、".join(f"{n} {old[n]} → {new[n]}" for n in changed))
    return lines


def write_skip(table: dict, photos: dict) -> bool:
    """照 photos 写 data/sfw_skip.json：说明照留（没有时用 NOTE），换行照原文件（新建时 CRLF），先写临时文件再整份换上。
    内容没变时不写，返回 False。"""
    path = skip_path()
    raw = path.read_bytes() if path.exists() else b""
    newline = (b"\r\n" if b"\r\n" in raw else b"\n") if raw else CRLF
    value = {"note": table.get("note") or NOTE, "photos": photos}
    text = json_layout.rendered_bytes(value, newline)
    if text == raw:
        return False
    fileio.write_bytes(path, text)
    return True


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="算哪些健全版角色照首次运行不用下载（只用普通版的角色照也认得出）。")
    ap.add_argument("--write", action="store_true", help=f"照算出来的写进 data/{SFW_SKIP}（先打印和现有名单的差别）")
    args = ap.parse_args(argv)
    faces, unread = load_faces()
    if unread:
        print(f"读不出来的角色照 {len(unread)} 张（算的时候没用上）：" + "、".join(unread))
    if not any(is_sfw(n) for n in faces) or all(is_sfw(n) for n in faces):
        print(f"开发目录 {hero.heroes_dir()} 里普通版或健全版的角色照一张都没有：先运行 tools/fetch_assets.py 下好、"
              f"tools/fetch_assets.py --crop 裁好。")
        return 1
    found = covers(faces)
    for c in found:
        verdict = "不用下" if c.covered else "要下"
        print(f"{c.name}  本人 {c.own:.3f}（{c.own_by or '没有普通版'}）  别的角色 {c.rival:.3f}（{c.rival_by}）  {verdict}")
    skip = {c.name: c.scores() for c in found if c.covered}
    print(f"健全版 {len(found)} 张：不用下 {len(skip)} 张、要下 {len(found) - len(skip)} 张"
          f"（本人 ≥ {OWN_MIN}、别的角色 ≤ {RIVAL_MAX} 才不用下）")
    table = read_skip()
    if table is None:
        print(f"data/{SFW_SKIP} 读不出来或写得不对（应是有 photos 表的表），没法比：先改好或删掉它（删掉以后 --write 照新建）。")
        return 1
    old = table.get("photos", {})
    diff = differences(old, skip)
    print(f"和 data/{SFW_SKIP} 现有的名单比：" + ("一样" if not diff else ""))
    for line in diff:
        print("  " + line)
    if unread:
        if args.write:
            print("有角色照读不出来，没写名单：先把它们重新下好、裁好。")
        return 1
    if args.write:
        if write_skip(table, skip):
            print(f"已写入 {skip_path()}；接着运行 tools/make_assets_manifest.py 重新生成清单、tools/check_data.py 核对。")
        else:
            print("内容没变，没写。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
