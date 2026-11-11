# 为 project-protocol 贡献修改

欢迎以 PR 形式提交改进。本 skill 是纯标准库、离线、确定性的协议工具，贡献修改时请先读本文件，避免破坏既有契约。

## 提交前自检（缺一即打回）

1. **跑全部回归**（4 套 + 独立验收），必须全绿：

   ```bash
   python regression_tests.py --script progress.py --out regression-base-result.json --workdir <临时目录>
   python regression_tests_v103.py --script progress.py --out regression-v103-result.json --workdir <临时目录>
   python regression_tests_v104.py
   python regression_tests_v110.py
   python independent_acceptance_v104.py
   ```

2. **跑 CI 字数门禁口径核对**（whitespace token ≤ 2050），并核对 `.github/workflows/ci.yml` 里的 SKILL.md budget gate 步骤。
3. **不做破坏性改动**：本 skill 的契约是「只追加、不覆盖、原子发布」。修改 `progress.py` 时不得引入第三方依赖、不得引入网络访问、不得引入外部 LLM API、不得把临时工作目录写进包内（回归测试的 `--workdir` 一律指到包外临时目录，绝不在仓库里跑默认工作目录）。

## 文档与代码的一致性

「CLI 事实」由 v103 的 V30 一致性用例守护，覆盖的不是固定某四处文档，而是下列互不重叠的表面：
- `progress.py` 的 argparse `--help` 文本（main / init / add / tail / check 五个帮助面；`handoff` / `resume` 不在 V30 断言内，改这两条命令的帮助文本请顺手跑 v110）
- `INSTALL.md` 的版本史关口（`v1.0.3` / 交付包名 / 各回归脚本名 / `--lock-timeout` / 「未验证」）
- `SKILL.md`、`PROJECT_TEMPLATE.md` 与「内嵌生成 AGENTS.md 模板」中的版本史、frontmatter 64 行上限、UTF-8 BOM、完成标识四处不变式

另请注意：`CAPABILITIES.md` 与 `SKILL.md` 第 7 节命令清单、`progress.py --help` 之间由**人工约定**保持一致（行为细节面广，换命令加参数时请顺手核对），`regression_tests_v103.py` 的 V30 门禁不会自动检查 CAPABILITIES.md。

## 分支与提交

- 从 `main` 切分支；提交信息用中文，一句话说清改了什么（沿用仓库既有风格）。
- 机器可读的回归结果（`*-result.json`）随包提交，历史结果保留不覆盖。

## License

参与即接受本仓库以 MIT License 分发（见 `LICENSE`，版权持有人：清尘）。你提交的内容同样按 MIT 授权。
