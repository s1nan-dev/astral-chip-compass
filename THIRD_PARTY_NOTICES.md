# 第三方声明

拾星助手 1.0.0 是《吉星派对》合作挑战的筹码推荐工具，由玩家自制，**不是官方工具**，与《吉星派对》的开发商、发行商以及下面两个 wiki 均无关联，也未获得它们的认可。

本文件说明本工具使用的资料、图片和第三方软件的来源与授权。本工具和第三方软件的许可证全文在 `LICENSES` 文件夹里。

## 一、游戏内容

《吉星派对》（Astral Party）的名称、角色、筹码、卡牌、地图、界面文字和美术素材的著作权及商标权归游戏的开发商和发行商所有。本工具引用游戏内的名称和文字，只为帮助玩家理解游戏机制、做出选择。

## 二、资料来源

### 1. Astral Party Wiki（英文）

- 地址：<https://astralparty.miraheze.org/>
- 授权：知识共享 署名-相同方式共享 4.0 国际（CC BY-SA 4.0），<https://creativecommons.org/licenses/by-sa/4.0/>
- 署名：Astral Party Wiki 的各位编者（各页面的编辑历史见 wiki）。
- 使用方式：本工具的游戏机制资料（角色、筹码、卡牌、怪物、地图、合作挑战规则、战斗规则）改编自英文 wiki。整理时做了这些改动：只保留游戏机制，翻译为中文，改写为本工具的数据格式，并加入本工具自己的评分规则和筹码适配表。数值、中文名称和效果原文以游戏内和吉星派对 WIKI 为准，英文 wiki 作参考。
- 改编后的资料按 CC BY-SA 4.0 提供，见下面第四节。

### 2. 吉星派对 WIKI（中文，哔哩哔哩 BWIKI）

- 地址：<https://wiki.biligame.com/starengine/>
- 授权：站点没有声明知识共享许可。按 BWIKI 的《WIKI社区规则》（<https://wiki.biligame.com/wiki/WIKI社区规则>），站内多人编辑的内容由编者按贡献共同所有，未经许可不得转载。
- 使用方式：本工具没有转载 BWIKI 的文章，只用它核对事实信息：游戏内的数值、中文名称和效果原文，图片文件名，以及游戏内置的筹码推荐分（这些是游戏本身的数据）。
- 识别用图片：见第三节。

## 三、识别用图片

识别当前角色、星数和已有筹码需要用到游戏的美术素材：角色照（UT_Hero_RolePhoto_*）、Q 版头像（UT_Hero_ProfilePhoto_*）和筹码图标（UT_Relic_*、UT_RelicBase_*）。

- 这些图片的版权归游戏方所有。
- **发行版不附带这些图片。** 首次运行（或图片缺失）时，由用户的电脑通过吉星派对 WIKI 的公开接口直接下载，保存在用户的个人目录 `%LOCALAPPDATA%\AstralChipCompass\assets`（各个版本共用一份；个人目录用不了时改存在程序目录的 `assets` 文件夹），只供本机识别使用。吉星派对 WIKI 上没有、或者和游戏里不一样的个别皮肤角色照，改为下载同一套的卡面原画（UT_Hero_Card_*），在用户的电脑上按裁法（照游戏内核对）裁出角色照，原画用完就删、不留在电脑上（清单中带 `crop` 字段的项）。下载地址、大小和校验值列在 `data/assets_manifest.json`。
- 请勿把下载的图片另行分发。
- 发行版唯一附带的图片是星数识别用的横条模板 `assets/stars/star_bar.png`（游戏界面上三颗星那一小块，wiki 上没有对应的图片），版权归游戏方所有。

## 四、本工具的许可证

1. 拾星助手是免费的开源软件，程序代码按 GNU Affero 通用公共许可证第 3 版（AGPL-3.0）授权，许可证全文见程序目录下的 `LICENSES` 文件夹；源代码：<https://github.com/s1nan-dev/astral-chip-compass> 。
2. 按这一许可证，您可以使用、复制、修改和分发本工具；分发时须附上完整的源代码、保留版权和许可证声明，修改后的版本同样按 AGPL-3.0 授权。具体以许可证全文为准。
3. 随附的第三方组件按各自的许可证授权，本工具的许可证不限制这些许可证赋予您的权利，包括按 LGPL 替换 Qt / PySide6 库，以及为此所需的调试和逆向分析。
4. `data` 文件夹里由本工具整理、编写的资料（角色、筹码、地图数据，规则和筹码适配表）按 CC BY-SA 4.0 提供，署名为「拾星者Sinan，改编自 Astral Party Wiki（CC BY-SA 4.0）」。其中的游戏内文字（名称和效果描述）版权归游戏方所有，不在此授权范围内。

## 五、免责声明

1. **非官方工具**：拾星助手是玩家自制的免费开源工具（AGPL-3.0），与《吉星派对》的开发商、发行商、运营方，以及吉星派对 WIKI、Astral Party Wiki 均无关联，没有得到它们的授权或认可。《吉星派对》的名称、角色、图片等内容的版权归游戏方所有。
2. **工作方式**：本工具只截取游戏窗口的画面，在本机识别后给出推荐。它不读取或修改游戏的内存和文件，不拦截或修改游戏的网络数据，也不向游戏发送键盘或鼠标输入。选哪个筹码始终由您自己决定、自己操作。
3. **账号风险**：游戏运营方的处罚通知把「使用未经授权的第三方程序」列为违反《用户服务协议》的行为，拾星助手属于这类程序。使用本工具可能被认定为违规，受到警告、限制直至封停账号等处罚，本工具无法保证您的账号不受处罚。请先阅读游戏的用户协议，再自行决定是否使用，使用的风险由您自行承担。
4. **推荐仅供参考**：推荐依据公开的游戏资料和本工具的规则估算，可能与游戏实际情况不符。按推荐选择不保证获胜，也不保证取得任何特定结果。
5. **不作担保**：本工具按现状提供，不作任何明示或默示的担保。在适用法律允许的最大范围内，作者不对因使用或无法使用本工具造成的任何损失承担责任，包括但不限于账号受到处罚、游戏内资产损失、数据丢失。
6. **获取渠道**：拾星助手免费发布，官方下载只有两处：下载镜像 <https://dl.astral-chip-compass.com/ACC-latest.zip> ，GitHub 上的发布页面 <https://github.com/s1nan-dev/astral-chip-compass/releases> ；源代码在 <https://github.com/s1nan-dev/astral-chip-compass> 。付费获得的版本、来路不明的修改版都不是官方发布的，可能被植入恶意程序，请只从上面的地址下载。
7. **隐私**：截图只在本机识别，不上传。联网只有两项：从吉星派对 WIKI 下载识别用的图片；检查和下载新版本（连接下载镜像 dl.astral-chip-compass.com 和 GitHub，启动时的自动检查可以在「拾星助手设置」中关掉）。详见使用说明的「隐私」一节。
8. **联系**：游戏方如对本工具有异议，请通过项目主页联系作者。

下载、安装或使用本工具，即表示您已阅读并同意本声明。

## 六、第三方软件

app 文件夹里的 ACC.exe（程序本体）由 [Nuitka](https://nuitka.net/) 编译，C 编译器为 [Zig](https://ziglang.org/)；和 app 文件夹放在一起的拾星助手.exe（启动程序）由 Zig 编译。发行版附带下列第三方组件（版本以发行版为准）：

| 组件 | 版本 | 许可证 | 全文 |
|---|---|---|---|
| Python（含 libffi、zlib、libmpdec、HACL*、mimalloc） | 3.14.6 | PSF-2.0 及各组件的许可证 | `LICENSES/Python.txt` |
| Microsoft Visual C++ 运行库 | 14.44 | 微软可再发行组件条款 | `LICENSES/MSVC-runtime.txt` |
| Nuitka 运行时库 | 4.2.2 | AGPL-3.0 附 Nuitka 运行时库例外 | `LICENSES/Nuitka-runtime.txt` |
| Zig 运行库（compiler-rt、mingw-w64 CRT） | 0.16.0 | MIT / ZPL-2.1 / Apache-2.0 WITH LLVM-exception | `LICENSES/zig-runtime.txt` |
| Qt（内含 FreeType、HarfBuzz、libpng、zlib、PCRE2） | 6.11.2 | LGPL-3.0-only 及内含组件的许可证 | `LICENSES/Qt-PySide6.txt`、`LGPL-3.0.txt`、`GPL-3.0.txt` |
| PySide6 / Shiboken6 | 6.11.2 | LGPL-3.0-only | 同上 |
| OpenCV（opencv-python-headless） | 5.0.0.93 | Apache-2.0 及附带组件（Intel IPP、libjpeg-turbo、libpng、libwebp、zlib、CLapack 等）的许可证 | `LICENSES/OpenCV.txt` |
| ONNX Runtime | 1.30.0 | MIT 及附带组件的许可证（另附官方构建的遥测说明 Privacy.md；本工具在识别前关闭遥测） | `LICENSES/onnxruntime.txt` |
| RapidOCR | 3.9.2 | Apache-2.0（随发行版的是按 3.9.2 修改的精简版 `rapidocr_lite`，改了什么见 `LICENSES/RapidOCR.txt`） | `LICENSES/RapidOCR.txt`、`Apache-2.0.txt` |
| PaddleOCR 模型（PP-OCRv6 检测与识别、方向分类） | 随 RapidOCR 3.9.2 | Apache-2.0 | `LICENSES/PaddleOCR-models.txt`、`Apache-2.0.txt` |
| NumPy（含 LAPACK、libdivide、Highway、x86-simd-sort、掩码数组 numpy.ma 等） | 2.5.3 | BSD-3-Clause 及附带组件的许可证 | `LICENSES/NumPy.txt` |
| python-mss | 10.2.0 | MIT | `LICENSES/mss.txt` |
| pyclipper / Clipper | 1.4.0 | MIT / BSL-1.0 | `LICENSES/pyclipper.txt` |
| 思源黑体 Heavy（按界面用到的字做的子集，字体名改为 ACC Heading） | 2.005 | SIL OFL 1.1 | `LICENSES/SourceHanSans-OFL.txt` |
| 思源黑体 Bold（按界面用到的字做的子集，字体名改为 ACC Title） | 2.005 | SIL OFL 1.1 | `LICENSES/SourceHanSans-OFL.txt` |

### 字体（思源黑体）

- `fonts` 文件夹里的 `ACCHeading-Heavy.otf`（界面标题、页签和按钮用的字体）：取自 Adobe 的思源黑体（Source Han Sans）2.005 简体中文 Heavy，只保留了界面上用到的字（按 SIL Open Font License 1.1 授权，许可证全文和版权声明见 `LICENSES/SourceHanSans-OFL.txt`）。
- `fonts` 文件夹里的 `ACCTitle-Bold.otf`（界面标题用的字体）：取自同一版本思源黑体的简体中文 Bold，同样只保留了界面上用到的字，许可证同上。
- 思源黑体的许可证保留了字体名「Source」，改动过的字体不能再用这个名字，所以这两个子集的字体名分别改为「ACC Heading」「ACC Title」。子集同样按 SIL OFL 1.1 提供，不单独出售。
- 原字体的下载地址：<https://github.com/adobe-fonts/source-han-sans/releases>

### LGPL 组件（Qt、PySide6）

- 这些库未作修改，以独立的动态库文件随附（`Qt6*.dll`、`pyside6.abi3.dll`、`shiboken6.abi3.dll`、`PySide6\*.pyd`、`shiboken6\*.pyd`、`PySide6\qt-plugins\`），可以替换为接口兼容的其他版本。Qt 自带控件（输入框的右键菜单等）的中文翻译 `qtbase_zh_CN.qm` 同样未作修改，随附在 `PySide6\translations\` 下。PySide6 包的少量 Python 引导代码由 Nuitka 编进了 exe。
- 源代码：
  - Qt 6.11.2：<https://download.qt.io/official_releases/qt/6.11/6.11.2/>
  - PySide6 / Shiboken6 6.11.2：<https://download.qt.io/official_releases/QtForPython/>

### 文字识别（rapidocr_lite）

- 发行版里的文字识别组件是 `rapidocr_lite`：根据 RapidAI 的 RapidOCR 3.9.2 修改而来的精简版，按 Apache-2.0 授权（许可证全文见 `LICENSES/Apache-2.0.txt`，出处和改了什么见 `LICENSES/RapidOCR.txt`），编在 ACC.exe 里。按 Apache-2.0 第 4 条（b），源代码里改过的文件开头都写明了出处和改动，逐个文件的说明见源代码中的 `rapidocr_lite/NOTICE.md`（源代码地址见第四节）。
- 识别模型（PP-OCRv6 检测与识别、方向分类）随发行版附在程序目录的 `models` 文件夹里，来自 PaddleOCR，许可证见 `LICENSES/PaddleOCR-models.txt`。识别全靠本机计算，不联网。

### Nuitka

Nuitka 编译器本身按 AGPL-3.0 授权，不在发行版中。编进 exe 的 Nuitka 运行时库适用「Nuitka 运行时库例外」，允许编译产物按作者选择的条款发布，见 `LICENSES/Nuitka-runtime.txt`。
