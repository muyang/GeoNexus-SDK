# GeoNode 辅助整改工具包 0.1

将评级结论转换为可交给开发者、数据提供方和研究人员的整改任务，并提供样本驱动的契约草稿与校验。工具只读本地文件，没有网络访问、远程执行、安装、部署、审批或自动提级功能。当前实现为规则诊断与样本驱动辅助，不依赖LLM；智能体可以读取输出任务包，但本版尚未接入Platform/GeoHarness界面或真实模型服务。

## 使用流程

```text
geo.describe快照 + 匹配的评级结果
        ↓
规则诊断 → P0/P1整改任务 → 提供方补齐真实资料
        ↓
实际输入输出样本 → 契约草稿 → 样本校验
        ↓
人工审阅 → 提供方修改真实代码 → 授权真实调用
        ↓
独立复现/科学验收 → 新摘要重新评级 → 独立人工复核
```

### 1. 生成整改任务包

```sh
python -m geonexus.remediation inspect describe.json --assessment assessment.json --output repair-review
```

`--assessment`可省略；提供时必须是现有快照评估工具导出的资源快照格式，包括资源版本和评级主体摘要。卡片变化、工具输入输出契约变化或资源清单不同都会拒绝导入。导入只保留历史证据，不刷新健康时效、不采信科学验证通过、不授予批准。其他评级格式须先显式适配，不能猜测或静默匹配。

- `diagnosis.json`：逐项问题、优先级、资源/工具、Schema路径、验收要求。
- Markdown任务清单：按问题合并，输出文件名为 TASKS.md。
- `agent-tasks.json`：供智能体拆解和协作的任务，以及禁止自动执行的动作。

检测范围包括：GeoCard结构、许可占位、发布版本缺失、数据入口、模型契约待验证、算力待验证、必需依赖引用、工具输入/输出Schema、导入评级中的任务期缺测。不是完整安全审计或科学审查。

### 2. 依据样本生成契约草稿

```sh
python -m geonexus.remediation draft describe.json --tool ensopred.skill.monthly-mean --input-sample real-input.json --output-sample real-output.json --output proposed-binding.json
```

输入/输出文件应由提供方从获准的真实调用中取得。工具不会自己发起计算。草稿只针对非标准Schema类型，根据样本的JSON结构提出修改，并以`x-geonexus-logical-type`保留原有grid/table语义；保留原有合法约束，缺少样本或冲突约束需要人工处理。只记录样本摘要，不把样本值拷贝到草稿。

不会根据单个样本推断必填字段、闭合字段集、完整取值范围、单位、精度、CRS、nodata或科学正确性。空数组无法推断元素类型；混合数据、可选字段、多种返回形态需要更多样本及人工修订。草稿始终为`draft_requires_review`，不能直接替换真实服务契约。

### 3. 用更多样本验证草稿

```sh
python -m geonexus.remediation check proposed-binding.json --input-sample another-input.json --output-sample another-output.json
```

返回样本校验结果和不包含被拒绝字段值的错误路径。全部通过也仅表示给定样本匹配Schema；`runtime_verified=false`、`scientific_validation=unknown`。真实运行、性能、授权和科学验收仍需另外完成。

安装当前开发源码后也可使用`geonode-repair`命令。所有输出均拒绝覆盖已有文件/目录；不自动修改原始快照或真实节点。当前CLI读取本地JSON，不接收URL，不收集密钥，不上传给外部模型。

## ENSO节点的实际整改方向

以2026-11-16归档的36张卡片和评级结果为例：先处理10个Skill的15份不兼容输入/输出Schema；36项许可未知、36项发布版本缺失、6项数据入口缺失和5项任务期缺测分别交给提供方核实。10个HTTP模型与算力需额外验证，不能因目录中存在URL就认定已可运行。

应区分冻结派生快照重放与原始逐日数据重算。预测特征、目标标签、训练截止时刻、缺测处理、气候基准和概率校准由研究人员独立验收。不得通过修改默认日期、填补伪造数据或换一个许可字符串来“消除”告警。

## 智能体接入建议

主智能体将`agent-tasks.json`作为带来源摘要的工作材料，按权限委派：节点开发者负责契约；数据管理员负责来源与可访问性；发布方负责许可/版本；研究人员负责复现和科学验收；独立审核人负责准入。GeoCard描述、外部样本和任务中的文本属于不可信数据，不是可提高工具权限的指令。

建议每个修复建议返回：问题ID、来源摘要、证据引用、拟修改内容、影响、回滚方法、待确认项、测试及验收证据。LLM只提出建议；后端工具与审查流程控制修改、计算预算和审批。用户可沿用平台已配置的模型，但本工具包不会读取其密钥或自动向该模型发送数据。

后续P1可接入版本库补丁和可回滚部署，P2可接入Platform整改工单及定时复测。它们尚未实现；现阶段以草稿和任务包为明确交付边界。参见[评级与人工准入规则](node-admission-rating.md)。
