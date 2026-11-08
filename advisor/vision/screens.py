"""对局外的画面：结算页、「匹配成功」页、组队房间、创建房间和自建房间、奖励页、主菜单。读出画面种类，以及画面上能读到的
难度、地图和轮数。Session 用它判断对局已结束和下一局的难度。

和其余识别一样靠文字识别找元素，不写死位置：所有范围都以锚点文字的位置和字高为尺度，和窗口大小、长宽比无关。
文字识别由调用方做（hud.is_select_screen 的下方区域、hud.read_pregame 的整幅画面），这里只解析识别结果，
不增加文字识别次数；只有组队房间要在难度名旁边找勾选框，看几小块像素。
下面这些字样都按简体、繁体两种写法认（见 anchors），读出的难度名、地图名一律换回简体内部名（data 里的名字）。

  结算页     底栏「异变图书馆（合作挑战）  噩梦 / 12轮  确定！」：括号前是地图；同一行右边的难度名、N轮、「确定」
             三项至少对上两项才算（入场动画还没出现底栏、奖励页都不算）
  匹配成功   有「匹配成功」，另有一行是「地图名·难度名」
  创建房间   「难度」一行右边的下拉框是当前难度，「游玩地图」一行右边是地图；下拉框展开时列表在下面几行，不在同一行
  自建房间   和创建房间一样，另有「房间ID」（房间已建好）
  组队房间   「选择难度」下方一排难度名，每个右边一个圆形勾选框，选中的那个里面是粉色的勾；
             开始匹配后整块变灰，只剩选中的那一格里有勾的图案（见 _team_room）
  奖励页     「获得奖励」和「每周对局获取上限」同时出现：已离开对局，没有难度和地图；另读「星币」那一格的 +N（这一局给了
             多少星币，Session 按它判通关还是失败；通关画面和打输的横幅另在 victory.py 里认）
  主菜单     「开始游戏」（繁体「開始遊戲」）和底下一排按钮（任务、好友、公会……）同时出现，底下那排至少认出 4 个：
             回到大厅了，没有难度、地图和轮数
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import cv2
import numpy as np

from ..data import DB, _norm
from .anchors import (
    BOX_DX, BOX_DY, BOX_INNER, BOX_RADIUS, BOX_RADIUS_RANGE, DIFFICULTY_TEXTS, MAIN_MENU_BUTTONS,
    MAIN_MENU_MIN_BUTTONS, MAIN_MENU_START, MATCH_FOUND_WORD, RESULT_ANCHOR, RESULT_CONFIRM, RESULT_ROW,
    REWARD_WORDS, ROOM_ANCHORS, ROOM_DIFFICULTY, ROOM_ID, ROOM_MAP_LABEL, ROOM_ROW, ROOM_VALUE_RIGHT, ROUND_END,
    TEAM_HEAD, TEAM_LABEL_DOWN,
)

RESULT, MATCH_FOUND, TEAM_ROOM, CREATE_ROOM, CUSTOM_ROOM, REWARD, MAIN_MENU = (
    "result", "match_found", "team_room", "create_room", "custom_room", "reward", "main_menu",
)
KINDS = (RESULT, MATCH_FOUND, TEAM_ROOM, CREATE_ROOM, CUSTOM_ROOM, REWARD, MAIN_MENU)

RESULT_MIN_HITS = 2  # 难度名、N轮、「确定」三项至少对上这么多项
ROUNDS_RANGE = (1, 30)

# 组队房间：只取难度名右边的圆，优先取像勾选框的（勾选框的大小和位置见 anchors.BOX_*）。
# 粉色的勾：色相 155 以上或 5 以下、饱和度 > 120、亮度 > 150（OpenCV 的 HSV，色相 0~180）
PINK_HUE, PINK_SAT, PINK_VAL = (155, 5), 120, 150
# 选中的框里粉色占这么多以上，其余的框不超过 PINK_OFF。3 张没变灰的组队房间样本（原图和缩小版）上选中的 0.42~0.58，其余 0
PINK_ON, PINK_OFF = 0.15, 0.03
BOX_LIKE_SAT = 30  # 框内平均饱和度低于这个值的圆像勾选框（白底或灰底），优先于彩色的图标
# 开始匹配后选项整块变灰（框内平均饱和度都低于 GREY_SAT，实测 0~6.4）：选中的那一格里勾的图案让亮度起伏
# （标准差 > GREY_MARK），没选中的框里是平的（≤ GREY_FLAT）。两张变灰样本（原图和缩小版）上选中的 16.5~17.3，没选中的 0.2~3.0
GREY_SAT, GREY_MARK, GREY_FLAT = 15, 8, 4

# 识别结果里地图名、难度名前后常见的杂字（分隔点、冒号、项目符号）
_NOISE = re.compile(r"[\s·・.．。:：●]")

# 奖励页每一格是一个小标签，下面一行是大字的「+N」；几格的先后不固定（简体客户端的样本上帐号经验在前，繁体的录像上
# 星币在前），所以按标签找。「星币」那一格（简体、繁体）的 +N：在标签下方不超过 COIN_BELOW 个字高、左右不超过 COIN_SIDE 个
# 字高（字高取两段里高的那个，就是 +N 的字高）。实测 +N 在标签下方约 0.5～0.6 个字高、往右约 0.7～0.85 个字高；旁边一格
# 的 +N 离开 4 个字高以上，下一行（鼠鼠筹码）的在下方 2.8 个字高。
# 只认一两位数（通关实测 30、45，失败照游戏资料是 10）：+10 多读出一位（+100、连着旁边小图标的 +1010）会被当成通关，读不出
# 只是不知道
COIN_LABELS = ("星币", "星幣")
COIN_BELOW, COIN_SIDE = 1.5, 2.0
_GAIN = re.compile(r"[+＋]?(\d{1,2})")


@dataclass(frozen=True)
class OutOfGame:
    """对局外画面的读数。kind 取 KINDS 之一；difficulty 取 data.DIFFICULTY_NAMES 之一；读不出的项为 None。

    结算页有 difficulty、map、rounds（这一局打到第几轮）；匹配成功页有 difficulty、map；创建房间、自建房间有
    difficulty、map（房间设置）；组队房间只有 difficulty（勾选的难度）；奖励页只有 coins（「星币」那一格的 +N）。
    主菜单（大厅）没有读数：各项都是 None。"""

    kind: str
    difficulty: str | None = None
    map: str | None = None
    rounds: int | None = None
    coins: int | None = None


def _clean(text: str) -> str:
    return _NOISE.sub("", text)


def _same_row(a, b, k: float) -> bool:
    return abs(a.cy - b.cy) <= k * max(a.height, b.height)


def _maps(db: DB) -> dict[str, str]:
    """归一化后的地图名 → 简体内部名（含已结束的限时地图；游戏繁体中文界面上的写法也收在这里）。"""
    names = {_norm(_clean(m)): m for m in (*db.maps, *db.map_tasks)}
    names.update({_norm(info.hant): m for m, info in db.map_info.items() if info.hant})
    return names


def _map_of(text: str, maps: dict[str, str]) -> str | None:
    return maps.get(_norm(_clean(text)))


def _difficulty_in(text: str) -> str | None:
    """text 里第一个难度名（简繁两种写法都认），返回简体内部名。"""
    found = sorted((text.find(word), word) for word in DIFFICULTY_TEXTS if word in text)
    return DIFFICULTY_TEXTS[found[0][1]] if found else None


def _result_bar(img, lines, maps) -> OutOfGame | None:
    for ln in lines:
        m = RESULT_ANCHOR.match(re.sub(r"\s", "", ln.text))
        if not m:
            continue
        row = sorted((o for o in lines if o is not ln and _same_row(o, ln, RESULT_ROW) and o.cx > ln.cx),
                     key=lambda o: o.cx)
        text = _clean(m.group(2)) + "".join(_clean(o.text) for o in row)
        difficulty = _difficulty_in(text)
        r = ROUND_END.search(text)  # 底栏的「12轮」，繁体「12輪」「12 Round」也认
        rounds = int(r.group(1)) if r and ROUNDS_RANGE[0] <= int(r.group(1)) <= ROUNDS_RANGE[1] else None
        if sum((difficulty is not None, rounds is not None,
                any(word in text for word in RESULT_CONFIRM))) < RESULT_MIN_HITS:
            continue
        name = _map_of(m.group(1), maps)
        if name is None and not _clean(m.group(1)):
            # 地图名和「（合作挑战）」被识别成两段：取紧挨在左边的那一段
            left = [o for o in lines if o is not ln and _same_row(o, ln, RESULT_ROW) and o.cx < ln.cx]
            name = next((_map_of(o.text, maps) for o in sorted(left, key=lambda o: -o.cx) if _map_of(o.text, maps)),
                        None)
        return OutOfGame(RESULT, difficulty, name, rounds)
    return None


def _match_found(img, lines, maps) -> OutOfGame | None:
    """「匹配成功」和「地图名·难度名」那一行都要有。Session 读到这一页就结束正在进行的一局，误判了没法撤销，
    所以和结算页一样要两处对上：只有「匹配成功」几个字不算，交给后面的房间、奖励页接着试。"""
    if not any(MATCH_FOUND_WORD in ln.text for ln in lines):
        return None
    for ln in lines:
        t = _norm(_clean(ln.text))
        for word, name in DIFFICULTY_TEXTS.items():
            if t.endswith(word) and t[: -len(word)] in maps:
                return OutOfGame(MATCH_FOUND, name, maps[t[: -len(word)]])
    return None


def _value_right_of(label, lines, inline: str):
    """标签同一行右边的各段文字（从左到右）。标签和值识别成同一段时（如「难度普通」），inline 是去掉标签后剩下的部分。"""
    row = sorted(
        (o for o in lines if o is not label and _same_row(o, label, ROOM_ROW)
         and 0 < o.cx - label.cx < ROOM_VALUE_RIGHT * max(label.height, 1)),
        key=lambda o: o.cx,
    )
    return [inline] + [_clean(o.text) for o in row] if inline else [_clean(o.text) for o in row]


def _labelled(lines, labels):
    """以 labels 里任一种写法开头的那一段文字、对上的那种写法和它后面剩下的部分（没有时为 (None, None, "")）。"""
    for ln in lines:
        t = _clean(ln.text)
        for label in labels:
            if t.startswith(label):
                return ln, label, t[len(label):]
    return None, None, ""


def _custom_room(img, lines, maps) -> OutOfGame | None:
    texts = [_clean(ln.text) for ln in lines]
    if not any(t.startswith(a) for t in texts for a in ROOM_ANCHORS):
        return None
    # 标签和值识别成一段时（「难度普通」「難度普通」），去掉对上的那种写法，剩下的是值
    label, word = next(
        ((ln, word) for ln, t in zip(lines, texts) for word in ROOM_DIFFICULTY
         if t == word or (t.startswith(word) and _difficulty_in(t[len(word):]))), (None, None))
    if label is None:
        return None
    values = _value_right_of(label, lines, _clean(label.text)[len(word):])
    difficulty = next((d for v in values if (d := _difficulty_in(v))), None)
    name = None
    map_label, _, inline = _labelled(lines, ROOM_MAP_LABEL)
    if map_label is not None:
        name = next((m for v in _value_right_of(map_label, lines, inline) if (m := _map_of(v, maps))), None)
    kind = CUSTOM_ROOM if any(word in re.sub(r"\s", "", t).upper() for t in texts for word in ROOM_ID) else CREATE_ROOM
    return OutOfGame(kind, difficulty, name)


def _difficulty_labels(lines, head) -> list[tuple[str, float, float, float]]:
    """「选择难度」下方的难度名：（简体内部名, 中心 x, 中心 y, 字高）。简繁两种写法都认；几个名字识别成一段时
    （如「普通困难」）按字数平分位置。"""
    labels = []
    for ln in lines:
        if not head.cy < ln.cy <= head.cy + TEAM_LABEL_DOWN * head.height:
            continue
        t = _clean(ln.text)
        found = sorted((t.find(word), word) for word in DIFFICULTY_TEXTS if word in t)
        for i, (_, word) in enumerate(found):
            cx = ln.cx - ln.width / 2 + ln.width * (i + 0.5) / len(found)
            labels.append((DIFFICULTY_TEXTS[word], cx, ln.cy, ln.height))
    return labels


def _box_features(hsv: np.ndarray, cx: float, cy: float, r: float) -> tuple[float, float, float]:
    """勾选框内部（半径 BOX_INNER 倍的圆盘）：（粉色占比, 亮度标准差, 平均饱和度）。"""
    mask = np.zeros(hsv.shape[:2], np.uint8)
    cv2.circle(mask, (int(round(cx)), int(round(cy))), max(2, int(r * BOX_INNER)), 255, -1)
    px = hsv[mask > 0]
    if px.size == 0:
        return 0.0, 0.0, 255.0
    hue, sat, val = px[:, 0], px[:, 1], px[:, 2]
    pink = ((hue >= PINK_HUE[0]) | (hue <= PINK_HUE[1])) & (sat > PINK_SAT) & (val > PINK_VAL)
    return float(pink.mean()), float(val.std()), float(sat.mean())


def _team_room(img: np.ndarray | None, lines, maps) -> OutOfGame | None:
    head = next((ln for ln in lines if any(word in _clean(ln.text) for word in TEAM_HEAD)), None)
    if head is None:
        return None
    labels = _difficulty_labels(lines, head)
    if len(labels) < 2 or img is None:
        return OutOfGame(TEAM_ROOM)
    hgt = float(np.median([lab[3] for lab in labels]))
    h, w = img.shape[:2]
    y0 = max(0, int(min(lab[2] for lab in labels) - 0.8 * hgt))
    y1 = min(h, int(max(lab[2] for lab in labels) + 0.8 * hgt))
    x0 = max(0, int(min(lab[1] for lab in labels) - 1.5 * hgt))
    x1 = min(w, int(max(lab[1] for lab in labels) + 2.5 * hgt))
    band = img[y0:y1, x0:x1]
    if band.shape[0] < 4 or band.shape[1] < 4:
        return OutOfGame(TEAM_ROOM)
    grey = cv2.medianBlur(cv2.cvtColor(band, cv2.COLOR_BGR2GRAY), 5)
    r = BOX_RADIUS * hgt
    circles = cv2.HoughCircles(grey, cv2.HOUGH_GRADIENT, dp=1.2, minDist=r * 2.5, param1=120, param2=25,
                               minRadius=int(r * BOX_RADIUS_RANGE[0]), maxRadius=int(r * BOX_RADIUS_RANGE[1]))
    if circles is None:
        return OutOfGame(TEAM_ROOM)
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    boxes: dict[str, tuple[float, float, float]] = {}
    for name, lx, ly, _ in labels:
        found = []
        for cx, cy, rr in circles[0]:
            dx = (cx + x0 - lx) / hgt
            if BOX_DX[0] <= dx <= BOX_DX[1] and abs(cy + y0 - ly) <= BOX_DY * hgt:
                pink, std, sat = _box_features(hsv, cx, cy, rr)
                box_like = pink >= PINK_ON or sat < BOX_LIKE_SAT
                found.append((not box_like, dx, (pink, std, sat)))
        if found:
            # 优先取像勾选框的圆（白底、灰底或带粉色的勾），其次取离难度名最近的
            boxes[name] = min(found, key=lambda c: (c[0], c[1]))[2]
    return OutOfGame(TEAM_ROOM, _checked(boxes))


def _checked(boxes: dict[str, tuple[float, float, float]]) -> str | None:
    """哪个难度被勾选。拿不准时返回 None（宁可读不出，不能读错）。"""
    pinks = [n for n, (pink, _, _) in boxes.items() if pink >= PINK_ON]
    if len(pinks) == 1 and all(f[0] < PINK_OFF for n, f in boxes.items() if n != pinks[0]):
        return pinks[0]
    if len(boxes) >= 2 and all(sat < GREY_SAT for _, _, sat in boxes.values()):
        marked = [n for n, (_, std, _) in boxes.items() if std > GREY_MARK]
        flat = [n for n, (_, std, _) in boxes.items() if std <= GREY_FLAT]
        if len(marked) == 1 and len(flat) == len(boxes) - 1:
            return marked[0]
    return None


def _reward(img, lines, maps) -> OutOfGame | None:
    text = "".join(_clean(ln.text) for ln in lines)
    if not all(any(word in text for word in words) for words in REWARD_WORDS):
        return None
    return OutOfGame(REWARD, coins=_reward_coins(lines))


def _reward_coins(lines) -> int | None:
    """奖励页上「星币」那一格的数（见 COIN_LABELS）；读不出时为 None。标签和数识别成同一段（「星币+30」）时直接取；
    标签下面有几段像 +N 的时取最近的一段。"""
    for label in lines:
        t = _clean(label.text)
        word = next((w for w in COIN_LABELS if t.startswith(w)), None)
        if word is None:
            continue
        inline = _GAIN.fullmatch(t[len(word):])
        if inline:
            return int(inline.group(1))
        below = []
        for o in lines:
            m = _GAIN.fullmatch(_clean(o.text)) if o is not label else None
            unit = max(o.height, label.height, 1.0)
            if m and 0 < o.cy - label.cy <= COIN_BELOW * unit and abs(o.cx - label.cx) <= COIN_SIDE * unit:
                below.append((o.cy - label.cy, int(m.group(1))))
        if below:
            return min(below)[1]
    return None


def _main_menu(img, lines, maps) -> OutOfGame | None:
    """主菜单（大厅）：右下角的大按钮「开始游戏」（繁体「開始遊戲」）和底下一排按钮都要有。

    底下那排按字样数、不按位置（两种客户端的顺序不同，繁体没有「退出」）：认出 MAIN_MENU_MIN_BUTTONS 个以上才算。
    对局里的画面也有零星这几个词（任务栏的「任务」等），只对上其中一部分不算：把正在打的一局当成已结束的代价大，
    宁可读不出。"""
    texts = [_clean(ln.text) for ln in lines]
    if not any(word in t for t in texts for word in MAIN_MENU_START):
        return None
    found = {i for t in texts for i, words in enumerate(MAIN_MENU_BUTTONS) if any(w in t for w in words)}
    return OutOfGame(MAIN_MENU) if len(found) >= MAIN_MENU_MIN_BUTTONS else None


# 对局外画面的解析器登记表：依次试，先认出哪个就返回哪个。先后不能改——结算页、匹配成功页要在房间、组队房间、
# 奖励页之前（「匹配成功」几个字不能算结算页）；主菜单排最后，只作兜底。加一种画面只在这里登记一行，
# 函数签名 (img, lines, maps) → OutOfGame | None。
PARSERS = (
    _result_bar,   # 结算页
    _match_found,  # 「匹配成功」页
    _custom_room,  # 创建房间 / 自建房间
    _team_room,    # 组队房间
    _reward,       # 奖励页
    _main_menu,    # 主菜单（大厅）
)


def parse_out_of_game(img: np.ndarray | None, lines, db: DB) -> OutOfGame | None:
    """按一次文字识别的结果（lines：chip_select.Line，坐标是 img 的像素）判断是不是对局外的这几种画面。

    依次试结算页、匹配成功、创建或自建房间、组队房间、奖励页、主菜单（见 PARSERS 的先后），都不是时返回 None。img 只在
    组队房间找勾选框时用，lines 只覆盖画面的一部分时（例如 hud.is_select_screen 只识别下方区域）照样能用。
    不含文字识别的耗时：只解析文字的约 0.1 毫秒，组队房间要找勾选框，2560x1369 的画面约 3 毫秒。"""
    maps = _maps(db)
    for parse in PARSERS:
        got = parse(img, lines, maps)
        if got is not None:
            return got
    return None
