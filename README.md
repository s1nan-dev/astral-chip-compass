# 拾星助手 Astral Chip Compass

《吉星派对》合作挑战的筹码推荐工具。对局中出现筹码三选一时，给出各筹码的推荐度与理由。

- 版本 1.2.0，资料同步日期 2026-11-12。

**拾星助手为无接触式工具。采用纯视觉方案，不会操作游戏，不会影响游戏收发包，不会注入游戏内存。**

**目前仅支持 PC 端，游戏语言为简体中文或繁体中文。支持 Steam 版（国服和国际服）和 TapTap 版。**

## 从这里开始

| 你是 | 看这份 | 里面有什么 |
|---|---|---|
| 玩家 | •&nbsp;**[下载镜像](https://dl.astral-chip-compass.com/ACC-latest.zip)**<br>•&nbsp;**[发布页面](https://github.com/s1nan-dev/astral-chip-compass/releases)**<br>•&nbsp;**[使用说明](https://s1nan-dev.github.io/astral-chip-compass/)** | 下载镜像：总是最新版的压缩包，打不开 GitHub 或下得太慢时用它<br>发布页面：最新版本的压缩包，解压后双击「拾星助手.exe」；以后出了新版本，在拾星助手里就能更新<br>使用说明：系统需求、下载和安装、三步上手、界面导览、常见问题、隐私、怎么反馈问题 |
| 开&#8288;发&#8288;者 | •&nbsp;**[软&#8288;件&#8288;维&#8288;护&#8288;说&#8288;明](docs/软件维护说明.md)** | 从源码运行、每个文件夹和代码文件放什么、游戏更新后改哪里、推荐规则怎么改和审、检查与打包 |

- 使用说明的网页打不开时：[在 GitHub 上看原稿](docs/使用说明.md)，内容一样。
- 反馈问题：[提交 Issue](https://github.com/s1nan-dev/astral-chip-compass/issues)，按模板附上诊断包（导出方法见使用说明的「反馈问题」）。
- 想提交改动：先看[参与改动须知](CONTRIBUTING.md)。

## 声明

- 拾星助手是玩家自制的非官方免费工具，与游戏方无关。它只读取游戏画面，不修改游戏，也不替你操作；但使用第三方程序可能违反《吉星派对》的用户协议，有被处罚乃至封号的风险，请自行决定是否使用，风险自负。推荐结果仅供参考。
- 官方下载只有两处：本页的[发布页面](https://github.com/s1nan-dev/astral-chip-compass/releases)和[下载镜像](https://dl.astral-chip-compass.com/ACC-latest.zip)；源代码在本页。付费获得的版本均非官方发布。
- 非官方渠道下载的拾星助手，可以在[核对页面](https://astral-chip-compass.com/verify/)核对是不是官方发布的原样：把压缩包或解压出来的文件夹拖进去即可。校验值在你的电脑上算，文件不会上传。
- 完整的免责声明见[使用说明的「声明」一节](https://s1nan-dev.github.io/astral-chip-compass/#声明)。

## 许可证与资料来源

- 程序代码按 GNU Affero 通用公共许可证第 3 版（AGPL-3.0）授权，全文见 [LICENSE](LICENSE)（发行版里是 app\LICENSES\AGPL-3.0.txt）。
- `data` 文件夹、`mechanics/` 里整理编写的资料按 CC BY-SA 4.0 提供，改编自 [Astral Party Wiki](https://astralparty.miraheze.org/)（CC BY-SA 4.0）；数值和中文名称照游戏内和[吉星派对 WIKI](https://wiki.biligame.com/starengine/) 核对。其中的游戏内文字和素材版权归游戏方所有。
- 第三方组件按各自的许可证授权，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)（许可证全文在 [LICENSES/](LICENSES/) 文件夹）。文字识别用的是 `rapidocr_lite/`：根据 RapidOCR 3.9.2 修改而来的精简版（Apache-2.0）。
