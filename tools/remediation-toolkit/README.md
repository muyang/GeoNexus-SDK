# GeoNode辅助整改工具包 0.1（SDK v1.1.2 发布包）

本仓库目录保存发行包模板。完整wheel、案例及验证记录请从GitHub v1.1.2 Release下载工具包ZIP；若在本目录直接运行，请先将构建的1.1.2 wheel放入本目录dist/。

包含规则诊断、输入输出样本驱动的契约草稿、样本校验和智能体任务包。没有远程操作或LLM依赖，尚未接入网站界面。

## 运行

需要Python 3.10+及SDK轻量依赖。建议在独立环境中安装随附wheel，勿覆盖现有平台运行环境：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install dist/geonexus_sdk-1.1.2-py3-none-any.whl
.venv/bin/python run.py inspect examples/describe.json --assessment examples/assessment.json --output my-review
```

已有SDK依赖环境也可直接用其Python运行run.py，无需安装wheel。输出目录必须不存在。

## 草稿功能自测（合成样本，不是节点真实返回）

```sh
.venv/bin/python run.py draft examples/describe.json --tool ensopred.skill.monthly-mean --input-sample examples/synthetic-input.json --output-sample examples/synthetic-output.json --output my-draft.json
.venv/bin/python run.py check my-draft.json --input-sample examples/synthetic-input.json --output-sample examples/synthetic-output.json
```

正式整改前必须用获准真实调用的样本替换合成样本，并检查所有必填、单位、CRS、范围、nodata和科学验收。所有草稿保留review要求，不自动替换节点契约。

## ENSO归档诊断

enso-review目录是2026-11-16归档快照的离线诊断，不代表今天的实时状态。111条逐资源发现归并为9类：许可36、版本36、数据入口6、输入/输出Schema15（涉及10个Skill）、目标期缺测5、正式声明1、算力验证1、HTTP模型验证10、科学复现1。不是111项独立算法问题，也没有实施节点修复或提级。

使用说明.md描述能力边界；AGENT_CONTEXT.md提供智能体任务上下文；verification.json记录构建、演示和测试结果。wheel版本为1.1.2；随GitHub v1.1.2一起发布，不发布PyPI，也不安装覆盖当前Platform/GeoHarness。
