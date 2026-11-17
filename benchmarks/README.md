# 固定评测集

团队培训版增加了 Requests、Starlette 与 FastAPI 官方全栈模板组成的公开语料，提供固定版本、15 道代码与文档检索题、真实 API 记录、并发测试和浏览器截图。[2026-11-17 实测报告](reports/public-onboarding-2026-11-17.md) · [语料清单](public-onboarding.json)

评测包含 Requests、Click、Flask 三个仓库，每个仓库五道入口与流程定位题。提交版本、参考符号和源码片段保存在 cases.json。参考答案由开发时的源码检查整理，独立人工复核待完成。

## 准备代码

在项目根目录执行以下命令。若已下载，只需核对提交版本。

```sh
git clone https://github.com/psf/requests.git benchmarks/checkouts/requests
git -C benchmarks/checkouts/requests checkout 3c7baaa05ab4aa49cc819ce3a819d260de19c157
git clone https://github.com/pallets/click.git benchmarks/checkouts/click
git -C benchmarks/checkouts/click checkout e38188e9e205005d5af5f9e59df51904c984586a
git clone https://github.com/pallets/flask.git benchmarks/checkouts/flask
git -C benchmarks/checkouts/flask checkout 49943b5a94413a530761350f04e896f76e8d620e
```

检出指定版本后，先验证参考符号和片段仍然存在。

```sh
python -m benchmarks.run verify
```

配置模型环境变量后执行真实对照，生成结果写入被 Git 忽略的 benchmarks/results。

```sh
python -m benchmarks.run compare
```

## 对照设计

基础方案与完整方案使用同一模型、入口、初始符号地图和初始源码上下文。基础方案进行一次生成。完整方案可以受限地读取更多源码，并在发现结构性问题时进行一次修订。两者的输出都交给相同的检查器统计。

这个对照衡量整个工作流的效果，额外读取与修订同时发生。进一步归因需要增加消融组，分别关闭工具读取与修订。

记录以下数据。

- 符号覆盖率按五个参考符号是否出现在输出步骤中计算
- 源码定位数同时报告请求步骤数，暴露未知符号和重复步骤
- 静态连接匹配数与未解决连接数分别统计
- 模型返回的 Token 用量、调用次数、耗时和按配置单价估算的费用
- 人工语义评分逐题检查答案正确性，覆盖问题关键事实计 1 分，部分正确计 0.5 分，错误或缺失计 0 分

人工评分由复核者查看完整导览和对应提交源码后填写。机械符号覆盖率只说明步骤涉及这些符号。模型输出、语义分数、性能收益、真实用户节省时间均需在完成相应实验后报告。

