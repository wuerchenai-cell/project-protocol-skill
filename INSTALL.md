# 安装与维护说明（project-protocol v1.1.0 / 模板 v1.8.0）

> 本包是 **skill**（一页给 agent 的规则说明），不是需要人记得去翻的文档，也不是安装程序。
> 规则主体在 `SKILL.md`，格式总表在 `PROJECT_TEMPLATE.md`，确定性工具在 `progress.py`（可选），
> 可复运行的回归测试在 `regression_tests.py`（原 24 项）、`regression_tests_v103.py`
> （v1.0.3 七项修复新增用例）、`regression_tests_v104.py`（v1.0.4 两处修复 21 项）、
> `regression_tests_v110.py`（v1.1.0 handoff/resume + v1.0.4 兼容 12 项）与
> `independent_acceptance_v104.py`（独立验收 12 项），均可选，宿主可忽略。
> 版本沿革：v1.1.0 在 v1.0.4 基础上增补换 AI 交接（`handoff` / `resume`，见 `SKILL.md` 第 4.1 节），
> v1.0.4 的两处初始化修复全部保留；历史交付包链为
> `project-protocol-skill-v1.0.3.zip` → `project-protocol-skill-v1.0.4.zip` → 本版 `project-protocol-skill-v1.1.0.zip`。

## 一、它凭什么「长期记住」

支持 Agent Skills 的宿主通常在**会话开始时**把已安装 skill 的 `name` + `description` 注入（几百字节），
任务匹配时才加载 `SKILL.md` 全文。因此：

- **长期记住的是「有这么一套协议」**：每次会话都在，不靠人记、不靠某个项目文件。
- **详细规则按需才读**：不常驻占上下文。
- **数据仍落在项目内**（`AGENTS.md` + `06_记忆/`）：纯文本，随 git / 移动盘 / 换机器走。
  项目 `AGENTS.md` 是自包含的，换一个没装本 skill 的 agent 也能照着做。

一句话：**规则跟着人走（skill），状态跟着项目走（纯文本）**，两者不互相依赖。

> 注意：上述「加载行为」是各宿主的一般做法，**不是本包验证过的保证**。是否启用某宿主、
> 安装到哪个宿主，由使用者按自己的授权边界决定；本包不代为安装，也不读写任何宿主私有记忆。

## 二、安装位置

解压交付的 `project-protocol-skill-v1.1.0.zip`，把里面的 `project-protocol/`
整个文件夹复制到下表任一宿主的用户级或项目级 skills 目录。zip 内已含顶层目录，文件名全 ASCII。

| 宿主类型 | 常见安装路径（以所用版本官方文档为准） |
|---|---|
| 通用 / Agent Skills 标准 | `~/.agents/skills/project-protocol/` |
| 支持用户级 skill 的宿主 | 该宿主的用户级 `skills/` 目录 |
| 项目级（可随仓库共享） | `<项目>/.agents/skills/project-protocol/` |

复制示例（Git Bash / macOS / Linux；目标目录不存在时先创建）：

```bash
mkdir -p "$HOME/.agents/skills"
cp -r ./project-protocol "$HOME/.agents/skills/"
```

**安装前注意**：若目标目录已有同名文件夹，先比对版本，不要盲目覆盖；本包不提供自动安装器。
安装后各宿主那份是副本，更新时改源头再逐个同步。

## 三、三种验证必须分开（重要）

| 层次 | 验证什么 | 怎么做 | 不能证明什么 |
|---|---|---|---|
| ① 脚本能运行 | 代码可执行、无语法错误 | `python progress.py --help`；跑 `regression_tests.py` | 不能证明宿主识别了 skill，也不能证明规则被遵守 |
| ② 宿主识别 skill | `SKILL.md` frontmatter 合规、被宿主列出 | 用**该宿主自己的** skill 列表/诊断命令查看；部分宿主需**新开会话**才加载新装 skill | 不能证明 agent 实际会照规则做 |
| ③ 规则能被遵守 | 真实会话中是否按节点记日志、是否查 REJECTED | 在一个真实新项目里跑一两周，观察是否漏记、格式是否走样 | 不能靠 `--help` 或静态检查代替 |

常见误区：`progress.py --help` 打印出六个子命令（init / add / tail / check / handoff / resume），只说明第 ① 层通过。

### 回归测试（可复运行）

在解压出的 `project-protocol/` 目录内执行：

```bash
python regression_tests.py \
  --script ./progress.py \
  --out ./regression-result.json \
  --rounds 10 \
  --workdir ./test-scratch

python regression_tests_v103.py \
  --script ./progress.py \
  --out ./regression-v103-result.json \
  --workdir ./test-scratch-v103

python regression_tests_v104.py \
  --script ./progress.py \
  --out ./regression-v104-result.json \
  --workdir ./test-scratch-v104

python regression_tests_v110.py \
  --script ./progress.py \
  --out ./regression-v110-result.json \
  --workdir ./test-scratch-v110

# 独立验收（环境变量指向隔离目录；不设置时输出到脚本所在目录）
PP_SCRIPT=./progress.py PP_FIXTURES=./acceptance-fixtures \
PP_RESULTS=./independent-v104-results.json \
  python independent_acceptance_v104.py
```

- 纯标准库、离线、只使用 `--workdir` 下新建的隔离临时项目；不安装、不联网、不改任何真实项目。
- `regression_tests.py`：原 24 项，覆盖初始化边界、日志格式、否决检测、参数校验、输出预算、
  确定性锁竞争和 **4 进程 × 20 条 × 10 轮**真实并发追加。
- `regression_tests_v103.py`：v1.0.3 新增用例，覆盖 Unicode 换行、想法 frontmatter 四种写法与
  各类解析失败、UTF-8 BOM 对照、初始化部分写入 / flush / close / 发布阶段失败与完成标识、
  `.git` 范围、init 与日常接手条件分离、`--lock-timeout` 有限正数与持锁进程异常退出。
- `regression_tests_v104.py`：v1.0.4 新增 21 项，覆盖发布前竞争写者、发布原语不可用时不降级、
  暂存文件完整后才发布、无 `os.replace` 回退，以及完成识别六场景（完整新版 / 删标识 /
  末尾规则中途截断 / 更早截断 / 真实完整 v1.0.2 入口 / 残缺旧版）与正文摘要、伪造标识、BOM 等边界。
  `v1.1.0` 新增 `regression_tests_v110.py`：8 项覆盖 `handoff`/`resume`（卡片七节齐全、
  goal/wip/negative 与 L0/L1 绑定、卡片路径不重复且无残留占位符、exclusive 写绝不覆盖竞争卡片、
  冷启动包带 token 量化、无卡 cold-start、输出预算 `--max-lines` 截断、卡片位置说明正确），
  4 项覆盖 v1.1.0 向后兼容（init 发布新版身份、真实 v1.0.4 入口升级后仍可用、v1.0.4 指纹逐字节核对、
  截断 v1.0.4 入口被拒）。
- `independent_acceptance_v104.py`：独立复审原 12 项，A01–A10 与 B01 原样；B02 因 v1.0.4 不再调用
  `os.replace`，注入点改到真实发布原语 `os.link`，并加严断言（原成功条件保留、未删断言）。
- `fixtures/v102_AGENTS.md`、`fixtures/v103_AGENTS.md` 与 `fixtures/v104_AGENTS.md` 是真实旧版生成的完整入口，只读夹具，
  不用于自动改写项目。
- 结果 JSON 含逐用例通过/失败、脚本 SHA-256、平台信息；并发轮次与记录统计见前者。
- **只在运行它的平台上验证过**；其他平台在结果中标注「未验证」，不要据单一平台宣称跨平台通过。
  宿主是否加载 skill、agent 是否真的照规则做，属于上表 ②③ 层，本测试不覆盖。

## 四、换 AI 交接命令（v1.1.0 新增 handoff / resume）

项目采用本协议后，换 agent 接手或在新时段开工时用这两条命令，避免对方重做或猜进度：

```bash
# 交班前：留一张交接快照（卡片只读，绝不覆盖）
python progress.py handoff --by "换班身份" [--tail-lines 30]
# → HANDOFF OK | 06_记忆/HANDOFF-YYYYMMDD-HHMMSS.md | N 字节 | sha256 …

# 接班时：一次读入即冷启动（输出受预算控制，带 token 量化）
python progress.py resume [--lines 30] [--max-lines 40] [--max-bytes 8192]
```

- `handoff` 先把 L0 末尾若干条 + L1 六节 + 最近 WIP 的 NEXT 命令 + 已否决清单压进一张
  `06_记忆/HANDOFF-YYYYMMDD-HHMMSS.md` 快照；卡片写前先落临时文件，再用原子无覆盖发布
- `resume` 打印一次「接手包」：读入清单（行/字节/约略 token）+ 已否决/阻塞行 + 最新快照全文 +
  L1 全文 + L0 末尾，默认只打 40 行 / 8192 字节，可用 `--max-lines` / `--max-bytes` 放宽。
- 卡片**仅供导航，不是事实源**；与 `L1_STATE.md` 冲突时以后者为准。没跑过 `handoff` 的项目也能
  `resume`（只是没有快照卡片），旧项目完全不受影响。
- 完整规则见 `SKILL.md` 第 4.1 节与 `PROJECT_TEMPLATE.md` 第八、第十节。

## 五、关于 hook（闹钟）

部分宿主提供 hook 机制，可在会话结束前提醒「这次留日志了吗」。但：

- **配置路径、事件名、触发时机、是否默认启用都随宿主版本变化，本包未在任何真实宿主上验证**；
- 因此本包**不提供可直接照抄的 hook 配置**，也不声称某事件名可用；
- 若确需启用，请先查所用宿主版本的官方文档确认，再单独配置；配了也只在该宿主生效。

建议顺序：先用纯文本 + 规则跑一段时间；实测确实漏记，再考虑是否加固。

## 六、更新与卸载

- 更新：替换源头目录中的文件，再同步到各宿主副本；用 `--help` 与回归测试确认脚本层，
  用宿主自身命令确认加载层。
- 卸载：删除对应宿主目录下的 `project-protocol/` 文件夹。**项目内已生成的 `AGENTS.md`
  与 `06_记忆/` 不受影响**，照旧可读可写（这是纯文本协议故意做的解耦）。
- 本包不读取、不修改、不删除任何其他 skill 或任何宿主的私有数据。

## 七、边界与未实现

- 不保证 100% 自动记录；提示词规则是软约束。
- v1.1.0 新增换 AI 交接（`handoff` / `resume`，见 `SKILL.md` 第 4.1 节），v1.0.4 只修复
  独立复审提出的两处初始化缺陷（P1/P2）且全部保留；本版不新增数据库、记忆层、L2/L3 生成器、
  自动安装器、hook 或项目管理功能；v1.0.3 的 7 项修复与原有 70 项回归保持不变。
- `--lock-timeout` 必须是有限正数；`nan` / `inf` / `-inf` / `0` / 负数在打开锁文件前即被拒绝。
- `check` 对想法 frontmatter 解析失败一律返回 `2`，不会输出 CLEAN；文本读取兼容首部 UTF-8 BOM
  且不改写历史正文。
- `init` 发布 `AGENTS.md` 前会先把完整内容写入临时文件并 flush/fsync/正常关闭，再用原子无覆盖硬链接
  发布；目标在预检后被其他写者创建时明确失败并完整保留其字节，不覆盖、不删除、不列为本轮产物。
  文件系统不支持该原子操作时明确报错，不降级为 `os.replace`。中途失败会准确列出已创建 /
  已完整写入 / 临时文件与恢复方式；残缺入口不会被 `add/tail/check` 当作初始化完成。脚本不删除任何已有内容。
- 完成判定按明确版本身份选择：当前版本完成标识绑定正文 sha256，缺标识 / 截断 / 改写一律返回 2，
  不回落旧版启发式；v1.0.4 旧入口同为 sha256 绑定标记、按其专属身份走同一校验，
  完整 v1.0.2 / v1.0.3 入口靠字节指纹 + 完整结构判定，残缺旧版返回 2。
- 原子无覆盖发布依赖文件系统支持硬链接（本机 NTFS 已实测）；网络文件系统、非 NTFS 的 Windows
  文件系统、Linux / macOS 分支与宿主加载行为均**未验证**。
- Windows 多进程并发时，打开锁载体文件偶发瞬时共享冲突；脚本只对「打开」做有界重试
  （60 次 × 20 ms），写入仍只执行一次，持续失败明确报错。这不是放宽并发标准，也不跳过竞争者。
- 不引入数据库 / 向量库 / MCP / 外部记忆服务 / 外部 LLM API；全部离线、Python 标准库。
- `L2_SCENE.md`、`L3_PROFILE.md` 的生成脚本**尚未实现**，没生成时留空，不影响 L0/L1 两个事实源。
- L0 月末封存（改名移入 `99_归档/`）**需手工做**，脚本没有 `archive` 子命令；执行前须按
  `00_定义/COLLAB.md` 暂停并发写者。
- `add` 的写锁只保护当前 L0 的追加；手工编辑 L1、日志轮转、绕过脚本直接写 L0 **不受保护**。
- 并发锁使用 `msvcrt.locking`（Windows）或 `fcntl.flock`（POSIX）。POSIX 分支未在本次交付的
  测试平台上实跑，标注为未验证。
- 不对现有项目做改造：只对用户明确要求新建并采用本协议的项目生效，旧项目一个字不动。
