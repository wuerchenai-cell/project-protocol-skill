# project-protocol skill

AI-agnostic 的项目交接协议 skill（**v1.1.0，模板 v1.8.0**）。给 AI 编码助手（Codex / WorkBuddy 等）提供统一的项目结构契约与状态记忆，让"换一个新的 AI 会话接手项目"成为确定性操作，而不是靠对话历史碰运气。

## 这套协议解决什么问题

一个项目开新会话时，AI 对"项目进行到哪一步、哪些方案试过且失败、下一步是什么"完全没有信息。project-protocol 用一套固定的目录骨架 + 两层状态文件解决这个问题：

- **固定目录契约**：`00_定义 / 01_任务 / 02_调研 / 03_过程 / 04_成果 / 05_证据 / 06_记忆 / 07_素材 / 08_工具 / 99_归档` + 根 `AGENTS.md` 入口
- **L0_LOG.md**：只追加的流水账（DONE/DECIDED/REJECTED/BLOCKED/WIP 五态），失败决策永久可查、不重复尝试
- **L1_STATE.md**：当前状态唯一权威，冷启动先读它全文 + L0 末尾 30 行
- **v1.1.0 新增交接能力**：`handoff` 生成一页交接快照 + `resume` 冷启动，让同一项目可被不同 agent 接手而不重复劳动

## 核心文件

| 文件 | 作用 |
|---|---|
| `SKILL.md` | 协议本体（规则条文，跨宿主分发的入口） |
| `PROJECT_TEMPLATE.md` | 目录结构总表与各文件格式规范 |
| `INSTALL.md` | 安装方法（Codex / WorkBuddy 双宿主） |
| `progress.py` | 命令行工具：`init / add / tail / check / handoff / resume`，纯 Python 标准库，无第三方依赖 |
| `fixtures/` | 各版本 AGENTS.md 快照（回归测试基准） |
| `regression_tests*.py` | 回归测试（base 24 + v103 29 + v104 22 + v110 12） |
| `independent_acceptance_v104.py` | 独立验收脚本（12 项） |
| `*-result.json` | 各轮测试/验收的机器可读结果 |

## 快速上手

```bash
# 新项目开工（仅限空目录，非空老项目一律拒绝、零写入）
python progress.py init ./my-project

# 每完成一个可验证节点
python progress.py add --root ./my-project \
  --status DONE --tid T01 \
  --text "完成基础架构" \
  --path "04_成果/T01/交付" \
  --next "开始接口测试"

# 冷启动/接手
python progress.py tail --root ./my-project --lines 30
python progress.py check --root ./my-project "基础架构" --strict

# v1.1.0 换 AI 交接
python progress.py handoff --root ./my-project --by <身份>   # 交班前留快照
python progress.py resume --root ./my-project --lines 30     # 接班时一次性冷启动
```

## 版本与状态

- **v1.1.0（模板 v1.8.0）**：在 v1.0.4 基础上增补 `handoff` / `resume` 交接能力。
- 回归测试全过：base 24 + v103 29 + v104 22 + v110 12 = **87 项**，另加独立验收 12 项。
- 进度工具 sha256：`C1645F9BF1401D47383F32F40B7374C2279CAA8EAF22BB81C2B1B63A3A870317`。
- 已知边界：`init` 仅限空目录（对非空老项目拒绝，属设计约束而非缺陷）；老项目需走"手工模式"（照目录契约手工建骨架，脚本层禁用属预期）。

## License

**MIT License** — 版权持有人：清尘（Copyright (c) 2026 清尘）。

详见仓库根目录 `LICENSE`。第三方内容与依赖声明见 `THIRD_PARTY_NOTICES.md`；贡献修改流程见 `CONTRIBUTING.md`。复制、分发或修改本包内容时，请保留上述版权与许可声明。
