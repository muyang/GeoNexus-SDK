# GeoNode 实测评级：El Niño / NINO3.4 预测节点

评估对象：<http://192.168.100.182:9841>
节点 ID：`node-enso-nino34`；节点软件自报版本：`1.0.0`。
评估时间：2026-11-16 17:12:03 UTC+08:00。评级策略：`readiness-v1`，策略摘要 `80d544c02b0900981e218ce8e51c755a919e44f69036db8f91fdb01c5564003d`。

**结论：节点工程准备度暂评 B，70.60/100；生产准入建议为“整改后复核”。**

这是用新 SDK 对现有旧式节点元数据进行的本地试评。节点连接、资源目录与8个公开数据文件已实际读取；没有提交注册、修改审批、安装资源、调用模型或运行预测/回测。节点 B 级不表示36项产品都可以执行，也不代表科学可靠性认证。

## 1. 分级结果与适用范围

| 层次 | 结果 | 判断依据 |
|---|---|---|
| 节点工程准备度 | **B / 70.60，暂评** | 健康接口、目录实际可达；部分声明字段来自旧卡片映射 |
| 声明算力类别 | **small_cpu，未核验声明** | Compute 卡自报8核、12 GiB、无GPU；未进行硬件探测或基准测试 |
| 科学正确性、回测质量 | **unknown** | 未运行预测/回测、核验训练切分、检查独立科学验证报告 |
| 人工准入 | **建议整改后复核** | 许可、正式声明、执行契约和复现证据存在缺口；本报告不授予审批权限 |
| 生产平台状态 | **本轮未修改** | 未将试评写入当前 Platform/GeoHarness，也未部署SDK新治理接口 |

节点原生报告分项为：

| 分项 | 分项得分 | 权重 | 加权分 | 证据边界 |
|---|---:|---:|---:|---|
| 声明完整度 | 70 | 20% | 14.00 | 名称、节点ID来自接口；机构仅从算力卡映射，节点管辖域未知 |
| 算力证据 | 40 | 25% | 10.00 | 只有8核/12 GiB的自报声明，独立算力核验未知 |
| 当前可达性 | 100 | 25% | 25.00 | 实际HTTP健康响应；不是长期在线率或SLA |
| 资源契约比例 | 72 | 30% | 21.60 | 26/36未发现已绑定Schema错误，SDK取整为72；其中包含未绑定的目录项，不是可执行率 |
| 合计 | | | **70.60** | |

资源级结果：

| 资源类别 | 数量 | SDK工程准备度 |
|---|---:|---|
| 数据 | 14 | D × 6；C × 8 |
| 模型 | 10 | D × 10 |
| Skill | 10 | not_ready × 10 |
| 算力 | 1 | D × 1 |
| Workflow | 1 | D × 1 |

36张卡片的外层GeoCard结构均通过规范校验。10张模型卡与10张Skill卡实际对应**同一批10个算子**，不是20项独立算法。

8个NOAA数据均为 C / 53.00；6个其余数据为 D / 16.25–20.00；10个模型为 D / 26.25；10个Skill标为 `not_ready`；算力 D / 18.75；Workflow D / 26.25。低分表示当前证据不足，不能据此判定算法本身无价值。

## 2. 真实接口证据

- `GET /health`：200，节点自报 `node-enso-nino34`、`geomcp`、软件版本 `1.0.0`。
- `GET /capabilities`：200，10个 `skills`、36个GeoCard ID、1个十步案例配方；`tools`为空，不能因此忽略另列的 `skills`。
- `POST /geomcp`，方法 `geo.describe`：200，返回36张完整卡片及其输入输出声明。该POST是目录读取，没有执行计算。
- `GET /`：200，说明发现与规划开放、`geo.execute`/`geo.invoke`需要 API key，HTTP模型调用另有开放入口。
- `GET /models`：405。此结果只说明该URL不支持GET，**不能断言模型POST执行服务故障**。
- `GET /openapi.json`：500。当前无法从该常见文档入口读取HTTP模型的标准接口说明；未测试模型POST。

GeoMCP目录发现已证实可用；本轮没有验证官方 MCP 的 `initialize`、`tools/list` 等方法，不能据此宣称官方MCP全面兼容。执行鉴权只记录节点声明，未通过实际调用验证安全行为。

## 3. 数据：可读取与满足任务分别判断

8个外部入口均完成HTTPS实际下载，检查了文本时间序列的数字布局、有效记录及缺测标记，并记录完整文件SHA256。未把HTTP 200页面、空间足迹或字段存在当作有效分析数据。

| 数据ID | 最后有效期的列索引 | 默认目标2026-08是否有值 | 有效记录/值数 |
|---|---|---|---:|
| `noaa.cpc.oni` | 2026-09 | 有 | 921 |
| `noaa.psl.dmi` | 2026-05 | **缺少** | 1877 |
| `noaa.psl.meiv2` | 2026-10 | 有 | 574 |
| `noaa.psl.pacwarm` | 2026-08 | 有 | 944 |
| `noaa.psl.pdo` | 2025-08 | **缺少** | 932 |
| `noaa.psl.qbo` | 2026-02 | **缺少** | 938 |
| `noaa.psl.soi` | 2026-07 | **缺少** | 907 |
| `noaa.psl.tni` | 2026-07 | **缺少** | 943 |

注意：ONI为三个月季节序列，MEIv2为两个月季节序列；表中月份是中心月/列索引，**不能直接视作相同定义的逐月区域指数**。所有数据的单位、气候基准期、预处理和时间可用性仍需任务级匹配。相关检查只证明可读和基本文本结构，不证明科学正确。

当前源文件对默认 `2026-08` 缺少 DMI、PDO、QBO、SOI、TNI 的有效值。这是**本轮下载的公开源状态**，不证明节点所存 `features.csv` 同样缺测；节点的派生快照未提供可访问文件入口，目前无法核验其填补、截断、历史可得性和来源版本。

其余6个数据产品为气候态场、月平均场、区域指数、特征表、回测sigma和NSOAS逐日SST，卡片未提供数据读取入口。部分含文件清单和SHA256，这是有价值的供应方声明，但没有实际文件就不能认定已完成哈希比对。NSOAS逐日源明确注明不在当前节点。

**优点**：月平均、区域指数明确区分“提供团队已保存结果”与“本节点重算”；距平描述也说明它不等同官方指数，减少误用。应保留这些语义，并进一步结构化为 `served_snapshot` / `recomputed`、数据来源版本、时间基准和单位。

## 4. 工具与模型契约

标准JSON Schema的 `type` 不包含 `grid`、`table`。它们可以保留在GeoCard业务语义中，但可调用接口必须使用 `object`、`array`、`number` 等标准类型，并定义具体结构或制品引用。参见 [JSON Schema官方类型说明](https://json-schema.org/understanding-json-schema/reference/type)。

| Skill | 输入Schema | 输出Schema |
|---|---|---|
| `ensopred.skill.anomaly` | 不通过 | 不通过 |
| `ensopred.skill.build-indices` | 通过 | 不通过 |
| `ensopred.skill.ensemble-forecast` | 不通过 | 不通过 |
| `ensopred.skill.equatorial-section` | 通过 | 不通过 |
| `ensopred.skill.front` | 不通过 | 不通过 |
| `ensopred.skill.monthly-change` | 不通过 | 不通过 |
| `ensopred.skill.monthly-mean` | 通过 | 不通过 |
| `ensopred.skill.region-boxes` | 通过 | 不通过 |
| `ensopred.skill.report` | 不通过 | 通过 |
| `ensopred.skill.walk-forward` | 不通过 | 不通过 |

共6个输入Schema、9个输出Schema存在问题，合计阻断10个Skill。具体JSON路径见 `assessment.json` 的 `schema_diagnostics`。

例如 `ensopred.skill.monthly-mean` 的输出 `properties.mean_field.type = "grid"` 应改为真实返回形态的标准Schema：如果返回内联数组，就明确维度、数值与缺测；如果返回文件，就明确URI、媒体类型、CRS、范围、单位、校验和。**不能仅将类型字符串替换成object而不核对真实返回结构**。

10个Model声明了各自HTTP `.../invoke` 入口，但没有在本次目录中提供可验证的独立HTTP调用绑定/标准Schema；它们暂列待验证目录项。GeoCard内部 `inputs`/`outputs` 的业务类型不是JSON Schema，不能直接把这些语义声明本身判作非法。HTTP模型是否实际成功需下一轮授权调用验证，本轮未执行。

## 5. 案例与科学验收

`ensopred.case.nino34-forecast` 引用了14个数据和10个Skill，已有十步配方、参数默认值及部分边界说明，具备研究案例的组织基础。但完整复现尚未核验：

1. 10个必需Skill当前契约阻断；修复并分别验证输入和输出后才能作为自动编排步骤。
2. 必需组件声明含不在本节点、也未提供读取入口的原始NSOAS数据。应区分“用冻结派生快照重放演示”与“从原始逐日数据重新计算”，分别列出可达依赖与验收要求。
3. 默认目标期与外部实时源覆盖范围不一致；应锁定实际训练输入快照，显式说明缺测处理、对齐规则和各期数据在起报时的可得性。
4. 特征表说明包含未来1–6个月目标值，应独立验证标签列与输入列隔离、滚动训练截止时刻和归一化拟合边界，防止信息泄漏。这里只提出检验要求，**没有证据判定已经发生泄漏**。
5. ONI、团队月指数、预测目标的定义不同，需明确气候基准、滑动季节窗口、去趋势、单位及比较方法。
6. 卡片空间经度有0–360形式且声明EPSG:4326；需显式说明经度域，统一地图转换和跨日期变更线范围，不能直接将300°经度当作地图常用的−180–180范围。
7. 可选GPU/A100只在参数/描述中出现；本节点Compute卡为CPU。若实际委派另一算力节点，应独立声明该节点、权限、预算、路由与执行证据，不能据此把当前节点评为GPU算力。

配方中供应方自报的 `status: approved` 不是Platform的人工批准。完整复现、预测精度、概率校准、独立回测与科学结论均保持 `unknown`。

## 6. 治理与评级规则的边界

本次检查的根目录、健康、能力和发现响应未广播新的正式 `NodeAnnouncement`。为了运行现有SDK，构造了明确标注 `NOT-SUBMITTED` 的**本地评估映射**：节点ID和名称来自真实接口、机构名称从Compute提供方字段映射、容量来自Compute卡；节点管辖域未擅自从各资源的CN声明推导。revision=1和有效期是评估器本地字段，不是提供方发布和续租证据。因此节点B只是暂评，不具备正式机构认证、广播租约和生产准入含义。

还发现当前 `readiness-v1` 的三项规则局限，必须随分数一起展示：

- 35张卡将许可填为“未声明，请联系团队”，1张未填许可；当前基础评分器按“非空文本”给元数据完整性分。**36项许可实际均未明确**，不能因分数计入就认为已获授权。
- 所有资源缺少提供方资源版本，采用本地 `snapshot-<hash>` 区分快照；这保证本地评级对象可定位，却不代表上游有正式发布版本。基础规则会给这一非空版本计元数据分。
- 节点库存指标为“未发现绑定Schema错误的比例”，会包含尚无执行绑定的目录模型与Workflow，不能用于宣称“72%资源可执行”。

因此，本报告同时保存SDK原生分数和严格的审计发现。正式评级接入前应收紧这些规则、补充正式声明与独立证据，再重评；不应把此次暂评直接用作生产批准。读取NOAA官方域名的文件也不自动等于核验整条派生血缘、使用许可或科学真实性；这些证据维度仍为未知。

## 7. 整改清单与复核标准

| 优先级 | 工作 | 完成标准 |
|---|---|---|
| P0 | 修复10个Skill标准输入/输出Schema | 20份Schema均通过Draft2020-12检查；再用真实执行结果逐项通过输入与输出校验 |
| P0 | 补齐正式节点声明与发布资料 | 机构与联系人、节点管辖域、递增revision、真实版本/摘要、声明及续租有效期；SDK声明校验通过 |
| P0 | 明确许可和执行鉴权 | 每项给出可核验许可/授权条件；分别验证无凭据拒绝、授权调用允许、撤销后拒绝；不依赖目录字符串判断 |
| P0 | 冻结可演示案例的数据快照 | 原始或派生文件可按权限读取并核对哈希；2026-08数据依赖逐项对齐，缺测策略和可得时间可追溯 |
| P1 | 接通10个HTTP模型契约 | 标准请求/响应、认证、超时、错误码、幂等、预算和远程路由；修复OpenAPI 500或提供明确替代文档 |
| P1 | 构建真实重放与科学验收 | 两种复现模式清楚；训练/验证时间隔离；基线与滚动回测、误差、校准、结果单位和制品均有独立证据 |
| P1 | 独立容量核验 | 核对CPU/内存、并发、队列和限制；若使用A100则单独登记远程算力，实时空闲量另行观测 |
| P2 | 稳定性与持续更新 | 有时效的周期健康证据、性能/失败率记录、版本变化后重评与复审、依赖健康告警 |

以上为整改建议，本轮没有修改远程节点，也没有自动批准任何资源。后续通过人工审核且任务权限、预算、主权与契约均满足时，这些第三方资源可按与官方资源相同的条件参与编排。

## 8. 证据、复核与工具

- `assessment.json`：节点与36项产品的完整分数、政策摘要、检查结果和审计发现。
- `resources.csv`：36项资源逐项清单，便于后台审核。
- `describe.json` / `capabilities.json` / `health.json` / `root.json`：实际公开响应。
- `*-http.json` / `*.body`：HTTP状态、观测时间、文件哈希及实际内容；8个源文件均保留。
- `locally-mapped-announcement.NOT-SUBMITTED.json`：仅本地映射，**不得作为提供方签发声明或直接提交**。
- `observations.LOCAL-ONLY.json`：本地探测记录，**不是已由Platform认证接收的生产证据**。可达性证据有效期15分钟，数据读取证据1小时；历史报告保持观测时刻的评级，不能作为以后持续在线的证明。
- `assess.py`：调用实际SDK评级器，按保存的评估时刻离线重放，保证同一证据同一策略得到相同结果。
- `collect.py`：仅只读HTTP采集；不调用execute/invoke，不扫描其他局域网设备。

离线复核命令（在项目工作区根目录执行，不安装或替换正在运行的SDK）：

```sh
PYTHONPATH=work/sdk-node-admission-20261116/sdk-repo/src work/mgearth-integration/venv/bin/python work/geonode-rating-9841-20261116/assess.py
python3 work/geonode-rating-9841-20261116/make_report.py
```

本地复算核验分数、等级、阻断状态、完整Schema路径诊断和数据覆盖结论一致，不计入节点产品的独立科学复现得分。底层JSON Schema库返回的首条错误文本顺序可能变化，不将这一文本顺序当作评级差异。

## 附录：36项资源明细

| ID | 类型 | 等级 | 分数 | SDK角色识别 |
|---|---|---|---:|---|
| `ensopred.compute.182-pool` | compute | D | 18.75 | catalog_only |
| `ensopred.data.climatology-1991-2020` | data | D | 20.00 | catalog_only |
| `ensopred.data.features` | data | D | 17.50 | catalog_only |
| `ensopred.data.monthly-mean-fields` | data | D | 20.00 | catalog_only |
| `ensopred.data.regional-indices` | data | D | 20.00 | catalog_only |
| `ensopred.data.walk-forward-sigmas` | data | D | 16.25 | catalog_only |
| `noaa.cpc.oni` | data | C | 53.00 | input |
| `noaa.psl.dmi` | data | C | 53.00 | input |
| `noaa.psl.meiv2` | data | C | 53.00 | input |
| `noaa.psl.pacwarm` | data | C | 53.00 | input |
| `noaa.psl.pdo` | data | C | 53.00 | input |
| `noaa.psl.qbo` | data | C | 53.00 | input |
| `noaa.psl.soi` | data | C | 53.00 | input |
| `noaa.psl.tni` | data | C | 53.00 | input |
| `nsoas.data.sst-daily` | data | D | 20.00 | catalog_only |
| `ensopred.model.anomaly` | model | D | 26.25 | catalog_only |
| `ensopred.model.build-indices` | model | D | 26.25 | catalog_only |
| `ensopred.model.ensemble-forecast` | model | D | 26.25 | catalog_only |
| `ensopred.model.equatorial-section` | model | D | 26.25 | catalog_only |
| `ensopred.model.front` | model | D | 26.25 | catalog_only |
| `ensopred.model.monthly-change` | model | D | 26.25 | catalog_only |
| `ensopred.model.monthly-mean` | model | D | 26.25 | catalog_only |
| `ensopred.model.region-boxes` | model | D | 26.25 | catalog_only |
| `ensopred.model.report` | model | D | 26.25 | catalog_only |
| `ensopred.model.walk-forward` | model | D | 26.25 | catalog_only |
| `ensopred.skill.anomaly` | skill | not_ready | 20.00 | unsupported_schema |
| `ensopred.skill.build-indices` | skill | not_ready | 20.00 | unsupported_schema |
| `ensopred.skill.ensemble-forecast` | skill | not_ready | 20.00 | unsupported_schema |
| `ensopred.skill.equatorial-section` | skill | not_ready | 20.00 | unsupported_schema |
| `ensopred.skill.front` | skill | not_ready | 20.00 | unsupported_schema |
| `ensopred.skill.monthly-change` | skill | not_ready | 20.00 | unsupported_schema |
| `ensopred.skill.monthly-mean` | skill | not_ready | 20.00 | unsupported_schema |
| `ensopred.skill.region-boxes` | skill | not_ready | 20.00 | unsupported_schema |
| `ensopred.skill.report` | skill | not_ready | 20.00 | unsupported_schema |
| `ensopred.skill.walk-forward` | skill | not_ready | 20.00 | unsupported_schema |
| `ensopred.case.nino34-forecast` | workflow | D | 26.25 | catalog_only |
