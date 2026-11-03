"""从中文 BWIKI 下载筹码图标和稀有度底板到 assets/chips/，供「查看筹码」面板识别（advisor/vision/chip_panel.py）。

下载的文件（原样保存，文件名照 wiki，空格写成下划线）：
  UT_Relic_<图标ID>.png      筹码图案（248x248，透明底）。资料库（data/chips.json）里每个筹码的 icon 字段
                             就是图标 ID；同系列不同档次共用一个图案（例如 Attack = 三档拳击手套）。
  UT_Relic_<数字>.png        wiki 上其余的数字图标也一并下载：资料库尚未收录的新筹码在面板上出现时，
                             可以识别为「未收录」而不是误认成别的筹码。50080 是「时间怀表」触发后的破碎图案。
  UT_RelicBase_<颜色>.png    稀有度底板（Blue 蓝、Purple 紫、Orange 金），用来取三种稀有度的参考颜色。
wiki 上按英文名命名的 300x300 图（UT_Relic_TargetBoard.png 等）是图案叠在底板上的成品图，识别用不到，不下载。

用法：.venv\\Scripts\\python tools\\fetch_chip_icons.py [--check] [--manifest 输出路径]
  默认下载缺少的文件，已有的跳过。
  --check     只列出缺少的文件，不下载。
  --manifest  把需要的文件清单写成 JSON（字段同 advisor/assets.py 的 AssetItem：dir、file、title、url、size、sha1），
              供发布工程生成首次运行的下载清单（data/assets_manifest.json）。

请求走首次运行的下载用的取图器（advisor/assets.py 的 _Fetcher：系统自带的 WinHTTP，和发行版的下载器一样写明工具名称和用途，
不冒充浏览器）。开发时偶尔用，不赶时间：接口请求之间至少隔 API_INTERVAL 秒，下载图片之间至少隔 SLOW_INTERVAL 秒（wiki 前面
的站点防护按电脑的网络地址拦截，请求密了会被拦）。被站点防护拦下（HTTP 567、拿到拦截页面等）或被限流（429、503）时马上停下、
不再发请求，打印原因和几点几分以后再运行，以退出码 2 结束：接着请求只会把封禁拖长，这段时间用浏览器打开 wiki 也会被拦。
停下之前已经下好的照留（--manifest 时不写清单）。别的出错（404、网络出错等）照旧报错退出。
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Callable

_ROOT = Path(__file__).resolve().parents[1]  # 只用来让 advisor 包可以导入；文件位置都走 advisor/paths.py
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from advisor.assets import API, API_INTERVAL, SLOW_INTERVAL, DownloadError, _Fetcher, refusal_note  # noqa: E402
from advisor.paths import assets_dir, data_dir  # noqa: E402
from advisor.vision.chip_panel import BASE_FILES, EXTRA_ICONS, icon_file  # noqa: E402


def list_wiki(prefix: str, ask: Callable[[str], bytes]) -> dict[str, dict]:
    """wiki 上以 prefix 开头的图片：文件名 → {url, size, sha1, descriptionurl}（ask 是接口的取图器）。"""
    found, cont = {}, None
    while True:
        params = {
            "action": "query", "list": "allimages", "aiprefix": prefix, "ailimit": "500",
            "aiprop": "url|size|sha1", "format": "json",
        }
        if cont:
            params["aicontinue"] = cont
        data = json.loads(ask(API + "?" + urllib.parse.urlencode(params)))
        for image in data["query"]["allimages"]:
            found[image["name"]] = image
        cont = data.get("continue", {}).get("aicontinue")
        if not cont:
            return found


def wanted_files(wiki: dict[str, dict]) -> tuple[list[str], list[str]]:
    """（需要的文件名，wiki 上找不到的文件名）。需要的 = 底板 + 资料库用到的图案 + wiki 上其余的数字图标。"""
    chips = json.loads((data_dir() / "chips.json").read_text(encoding="utf-8"))["chips"]
    names = list(BASE_FILES)
    names += sorted({icon_file(c["icon"]) for c in chips})
    names += [icon_file(i) for i in EXTRA_ICONS]
    # 其余数字图标（未收录的新筹码）
    for name in sorted(wiki):
        stem = name[len("UT_Relic_") : -len(".png")] if name.startswith("UT_Relic_") and name.endswith(".png") else ""
        if stem.isdigit():
            names.append(name)
    names = list(dict.fromkeys(names))
    return [n for n in names if n in wiki], [n for n in names if n not in wiki]


def main() -> int:
    """返回退出码：0 照常（--check 时缺文件为 1），2 被 wiki 拒绝、停下了（模块说明）。"""
    sys.stdout.reconfigure(encoding="utf-8")
    # 接口和图片各一条连接，各按自己的间隔（模块说明）
    ask, fetch = _Fetcher(interval=API_INTERVAL), _Fetcher(interval=SLOW_INTERVAL)
    try:
        return run(sys.argv[1:], ask.get, fetch.get)
    except DownloadError as e:
        note = refusal_note(e)
        if note is None:
            raise
        print(note)
        return 2
    finally:
        ask.close()
        fetch.close()


def run(args: list[str], ask: Callable[[str], bytes], fetch: Callable[[str], bytes]) -> int:
    """按命令行参数列出、核对或下载（见模块说明的用法）；ask、fetch 是接口和图片的取图器。"""
    out = assets_dir("chips")
    wiki = list_wiki("UT_Relic", ask)
    names, absent = wanted_files(wiki)
    for n in absent:
        print(f"wiki 上没有 {n}")

    if "--manifest" in args:
        i = args.index("--manifest")
        target = Path(args[i + 1])
        target.parent.mkdir(parents=True, exist_ok=True)
        # 字段和 advisor/assets.py 的 AssetItem 一致：title 是 wiki 文件页名（不带「文件:」前缀）
        items = [
            {
                "dir": "chips",
                "file": n,
                "title": n.replace("_", " "),
                "url": wiki[n]["url"],
                "size": wiki[n]["size"],
                "sha1": wiki[n].get("sha1", ""),
            }
            for n in names
        ]
        target.write_text(json.dumps(items, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"清单 {len(items)} 个文件 → {target}")
        return 0

    missing = [n for n in names if not (out / n).exists()]
    if "--check" in args:
        for n in missing:
            print(f"缺少 {n}")
        print(f"共需 {len(names)} 个文件，缺少 {len(missing)} 个")
        return 1 if missing else 0

    out.mkdir(parents=True, exist_ok=True)
    done = 0
    try:
        for n in missing:
            raw = fetch(wiki[n]["url"])
            sha1 = wiki[n].get("sha1")
            if sha1 and hashlib.sha1(raw).hexdigest() != sha1:
                print(f"{n} 校验失败（SHA-1 不符），未保存")
                continue
            (out / n).write_bytes(raw)
            done += 1
    finally:  # 中途停下（被拦、出错）时也写明已经下好了几个
        print(f"下载 {done} 个，已有 {len(names) - len(missing)} 个 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
