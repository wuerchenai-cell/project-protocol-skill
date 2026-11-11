# 第三方内容与依赖声明

本文件按 MIT License 的授权精神，如实声明本 skill 包内的第三方内容情况。

## 第第三方代码：无

`progress.py`、全部回归测试脚本与文档均为本项目原创或由本项目维护者编写（含 AI 助手辅助起草后经人工审核定稿的内容）。本包不含任何来自第三方仓库的代码、模板或大段文字再分发。本包整体按 MIT License 授权，版权持有人：清尘（Copyright (c) 2026 清尘）。使用/复制/修改请保留上述版权与许可声明。

## 第三方设计/协议借鉴

- 目录契约与「L0 流水账 + L1 权威态」两层状态思想，参考了开源社区中通用的项目状态管理惯例（只借鉴思想，未复制任何具体实现代码）。If you believe any material in this package infringes your copyright, open an issue and it will be removed.

## 运行时依赖

`progress.py` **只依赖 Python 标准库**（argparse / errno / hashlib / math / os / re / sys / time / datetime / pathlib），无第三方 pip 依赖，无运行时网络访问，无外部 API 调用。回归测试同样只用标准库 `unittest` 风格自写断言，不依赖 pytest。

## 开发/发布工具（不计入运行时）

| 工具 | 用途 | 许可 |
|------|------|------|
| Python 3.x（解释器本身） | 运行 progress.py | PSF License |
| Git / GitHub Actions | 版本管理与 CI | Git GPLv2，GHA 服务条款 |

## 形态声明

本 skill 以纯文件包形式分发（skill package），不附带任何二进制、模型权重或生成物。所有变更可经版本历史完整追溯。
