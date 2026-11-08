"""作业站：玩家分享拾星助手筹码方案的网站。

拾星助手用系统浏览器打开作业站的几个固定地址（首页、某个角色的方案列表），网址里不带方案内容。按编号导入时
程序自己连作业站取这一套方案，只发编号（只带方案网址里的随机码 key）和程序版本（User-Agent），不发送任何个人信息；平时不
连，也没有开关。

登录用电视式短码：程序向作业站要一个短码，在系统浏览器里打开授权页面，用户在网页上点「允许」以后程序才拿到令牌；程序
不碰密码。令牌存在 Windows 凭据管理器里（CRED_TARGET，advisor/wincred.py），「退出作业站」时删掉。发方案时程序把这一套
方案的分享码、名称、短说明，连同程序版本和资料日期发给作业站；不发本机编号、推荐日志、电脑信息。令牌不写进错误记录、
推荐日志和设置文件，诊断包里只记有没有登录。

地址和网站约定好，以后不改；网站改名时只改界面上显示的名字（wording.HUB_BTN 等），这里不改。

某个角色的方案列表用 heroes.json 里的 id（advisor/data.py 的 Hero.id）拼在地址后面；网站认不出编号、或者不带编号时列出
全部方案。网址里还可以带地图短名（map 参数），网站只列这张地图能用的方案：标了这张地图的排在前面，一张地图都没标的通用方案
排在后面，只标了别的地图的不列；网站认不出的短名当没给。复刻的改版改了地址时只改这个模块。
"""
from __future__ import annotations

# 总开关：为 False 时「筹码方案」卡片的「作业站」按钮、导入/导出窗口的「发到作业站」「去作业站找方案」「按编号导入」和
# 「已登录作业站」那一行、托盘菜单的「打开作业站」都不显示，程序也不连作业站（作业站的接口用不了、要先藏起入口时设成
# False）。为 True 时照常显示。
ENABLED = False

# 登录令牌在 Windows 凭据管理器里的名字（普通凭据）。前缀是程序名，用户在控制面板里看得到、删得掉。
CRED_TARGET = "AstralChipCompass/hub"

BASE = "https://astral-chip-compass.com/app/"
HOME = BASE
PLANS = BASE + "plans"
API_BASE = "https://astral-chip-compass.com/api/v1/"
# 按编号导入、发方案、修改方案：在 API 后面接方案编号
API = API_BASE + "schemes/"
# 登录：要短码、用短码换令牌；退出作业站也发到 DEVICE_TOKEN（DELETE）
DEVICE_CODE = API_BASE + "device/code"
DEVICE_TOKEN = API_BASE + "device/token"
ME = API_BASE + "me"
# 方案网址的前缀：BUILDS 后面接方案编号和「/」，后面可能带查询串（凭链接的方案带随机码 key）
BUILDS = "https://astral-chip-compass.com/builds/"

# 地图名（data/maps.json 的键）→ 作业站认的短名。这是和网站约定好的，以后不改；短名照 mechanics/maps/ 的文件名去掉 .md。
# 三张运动场（海选赛运动场、淘汰赛运动场、决赛大赛场）已经结束、共用一份资料，没有短名，认出来也不带地图。资料里新加了
# 地图时，先和网站对好短名再加进来（tests/test_hub.py 会提醒）。
MAP_SLUGS = {
    "龙宫游乐园": "dragon-palace-amusement-park",
    "星趴·梦想号": "dreama",
    "园林中庭": "garden-courtyard",
    "幽魂暗巷": "ghost-alley",
    "魔法学院": "magic-academy",
    "御魂庆典": "soul-celebration",
    "水乡古镇": "water-town",
    "森秋幻境": "autumnwood-mirage",
    "异变图书馆": "anomalous-library",
}


def map_slug(name: str | None) -> str | None:
    """地图名的短名；不是地图名（None、空串、资料里没有的名字）时返回 None，网址里就不带地图。"""
    return MAP_SLUGS.get(name) if isinstance(name, str) else None


def plans_url(hero_id: int | None = None, slug: str | None = None) -> str:
    """方案列表的地址：hero_id 为角色编号，slug 为地图短名；给哪个带哪个，两个都不给时列出全部方案。空字符串的短名当没有。"""
    query = []
    if hero_id is not None:
        query.append(f"hero={hero_id}")
    if slug:
        query.append(f"map={slug}")
    return f"{PLANS}?{'&'.join(query)}" if query else PLANS


def hero_plans_url(db, hero: str | None, map_name: str | None = None) -> str:
    """某个角色的方案列表：hero 是角色名（db.heroes 的键）；资料库没收录这个角色、或 hero 为空时列出全部方案。
    map_name 是当前地图名，查得到短名时只列这张地图能用的方案，查不到时不带地图。"""
    record = db.heroes.get(hero) if isinstance(hero, str) else None
    return plans_url(record.id if record is not None else None, map_slug(map_name))
