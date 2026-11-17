# GeoNexus-SDK v1.1.2

发布日期：2026-11-17。包含节点声明、广播、评级、人工准入及辅助整改工具包0.1。SDK包版本为1.1.2；工具包自身功能版本仍为0.1，二者不是两个互斥发布号。

## 功能

- 节点与资源分别声明、评级、审核；独立观测绑定具体摘要且有有效期。
- 官方与第三方资源走相同的权限、预算、主权、任务契约和执行节点路由条件。
- GeoCard保持静态语义；健康、容量观测、评级、审批独立保存。
- 离线整改工具支持inspect、draft、check三个命令，并输出智能体任务包。
- 提供真实ENSO节点归档评级、17页PDF、36项资源明细及整改诊断。报告中的节点地址是该次被评估对象的局域网地址，不保证读者可连接。

## 使用

```sh
pip install geonexus_sdk-1.1.2-py3-none-any.whl
geonode-repair --help
python -m geonexus.remediation inspect describe.json --assessment assessment.json --output review
```

治理HTTP演示需要server/http可选依赖；轻量核心不新增GIS、LLM或数据库服务依赖。辅助整改工具包ZIP内含1.1.2 wheel、归档快照、任务清单、使用说明、智能体上下文和明确标记的合成样本。建议独立虚拟环境验证，不覆盖现有平台运行环境。

## 验证与限制

功能回归在Python3.12完成942项测试；新工具与准入模块39项定向测试通过。发布前另核对版本、源码构建、wheel入口及打包后的归档诊断。样本演示不是节点真实输出，不记为科学复现。

本次触及的17个Python文件通过仓库指定Ruff 0.16.7的lint和格式检查。全仓仍有7项既有lint问题、47个文件需格式整理，原有开发快照另记录类型检查债务；本次不将其描述为全部通过，也不关闭CI检查。CI与发布资产状态以本次发布记录为准。

已知评级规则局限：许可占位文本和本地snapshot版本仍会获得基础完整性分；节点库存包含未绑定的目录项。暂评分数不能当作许可、真实发布版本、可执行率或科学正确率。

本次没有改动远程节点、审批或当前Platform/GeoHarness部署，也不发布到PyPI。治理生产接入、LLM整改建议、代码补丁、部署及后台工单尚需后续实现。

参见[节点准入与评级](node-admission-rating.md)、[辅助整改工具](node-remediation-toolkit.md)及[真实节点报告](reports/2026-11-16-enso-node-rating/README.md)。
