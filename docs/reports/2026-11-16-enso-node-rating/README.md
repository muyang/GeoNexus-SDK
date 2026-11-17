# ENSO / NINO3.4 节点只读评级快照

观测日期：2026-11-16。节点：`http://192.168.100.182:9841`，`node-enso-nino34`。局域网地址只标识本次对象，不保证读者可访问。

- [17页图表化PDF：模型、实测及完整报告](rating-model-and-report.pdf)
- [完整中文报告](report.zh-CN.md)
- [36项资源清单](resources.csv)
- [完整机器评级](assessment.json)
- [本地复核记录](verification.json)
- [评级模块设计及接入说明](../../node-admission-rating.md)

节点暂评B / 70.60；8项C、18项D、10项not_ready。节点评级来自旧式目录的本地映射，不是提供方正式广播、机构认证、人工批准或科学正确性认证。未进行预测、回测、硬件基准或审批变更。10个Model与10个Skill实际对应同一批算子。

许可占位文本、本地snapshot版本和无执行绑定的目录项是现行评分规则的已知局限。8个NOAA源能读取，不代表均满足默认2026-08任务；5项缺少该期有效值。未核验的证据保持unknown。

本目录只发布评估结果，不发布凭据、运行数据库、环境配置或原始数据下载。PDF内嵌JSON、CSV、原报告、验证记录及评级设计文档；均为历史记录，不使观测证据续期。报告中的重放命令记录当时工作区，不是本仓库安装路径。原始HTTP响应及完整复核工作区保留在本地审核包。

在SDK根目录、安装server/http可选依赖后运行通用示例：

```sh
python examples/governed_node_demo.py --output /tmp/geonexus-admission-demo
python examples/rate_node_snapshot.py /path/to/geo-describe.json --output /tmp/ratings.json
```

结果JSON不代表远程科学计算已经重现。新治理API仍需Platform/RuoYi适配，当前网站未因本次源码发布而自动上线评级。
