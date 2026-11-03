# 给 AI 编程工具的说明

这是《星引擎Party/吉星派对》合作挑战的筹码推荐悬浮窗：Windows 桌面程序，Python，纯视觉识别，不开游戏也能开发（识别用的样本另有准备）。

## 文件在哪

- `advisor/`：程序本体（识别、记账、推荐、悬浮窗）；`advisor/data.py` 载入 `data/` 下的游戏数据和推荐规则。
- `data/`：游戏数据（`chips.json`、`heroes.json`、`maps.json`、`mutators.json`、`meta.json`、`chip_codes.json`、`photo_tilt.json`、`card_crops.json`、`sfw_skip.json`）和推荐规则（`synergy.toml`、`rules.toml`、`series.toml`），游戏更新时改的就是这里。
- `mechanics/`：游戏机制说明。改推荐规则前先读对应的机制，不要只凭效果原文推断。
- `review/`：推荐规则的审查清单。`docs/`：各份说明和文档。`tools/`：检查、生成、下载、打包脚本和开发工具（识别评测、截图回放、界面预览等，用法见 `docs/软件维护说明.md` 的「开发工具」）。`tests/`：单元测试。

每个文件夹、每个代码文件放什么，见 `docs/软件维护说明.md` 的「目录结构」；游戏更新后改哪里、推荐规则怎么改和审、数据结构，见同一份说明。

## 改完跑什么

- 改了 `data/` 里的数据或规则：跑 `tools/check_data.py`（检查改得对不对；新筹码先跑 `tools/check_data.py --add-codes` 补编号）。
- 改了 `data/` 里的推荐规则（`synergy.toml`、`rules.toml`、`series.toml`）或推荐代码：重新生成 `review/` 下的两份审查清单（`tools/make_review.py`），并在提交说明里写清哪些角色的排名变了；量「首推变了几个」用 `tools/baseline`（见 `docs/测试指南.md` 第六节）。
- 改了代码：跑相关测试，见 `docs/测试指南.md` 第七节「改了哪里跑哪些测试」。

## 生成的、不要手改的文件

- `review/synergy-review.md`、`review/synergy-audit.md`：由 `tools/make_review.py` / `tools/audit_synergy.py` 生成。
- `data/assets_manifest.json`：由 `tools/make_assets_manifest.py` 生成。
- 发行版里的「使用说明.html」：由 `docs/使用说明.md` 经 `tools/manual_html.py` 生成（`packaging/` 下不留副本）。
- `data/chips.json` 等数据文件的编号表 `data/chip_codes.json`：编号只增不改（已有编号一个都不能变）。

## 注释和文案的规矩

- 注释和界面文字用中文；代码注释只写「为什么这么做」（设计理由、依据、容易看错的地方），不写「这一行在做什么」。
- 界面文字用书面语：不用口语和语气词，不用「我」「你」指代工具；提示用户操作时写「请……」，写明点哪里；数字和汉字之间加半角空格。
- 界面上的用词：先定下要用的说法，再改代码里的文字，`docs/使用说明.md` 里描述界面的部分同步改。
- 代码里的文字常量改过之后，`tests/test_project_consistency.py` 会核对代码和 `docs/使用说明.md` 里的引用是否一致。

## 不要提交的东西

- 识别样本截图和自己的标注（`samples/`、`tests/data/` 里只在本机的数据）。
- 自己的设置、筹码方案、推荐日志、诊断包和临时文件（`.tmp/`、`logs/`、`reports/`、`state.json`、`schemes.json` 等）。
