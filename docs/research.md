# 设计调研与改进依据

调研日期为 2026 年 11 月 14 日。核查范围包括仓库 README、项目元数据、PocketFlow 设计文档、DeepWiki-Open 导览服务部分源码、CodeTour 格式与维护说明、Aider 官方文档及 Spec Kit 工作流。

## 已有项目

### Microsoft CodeTour

在编辑器里将目录、文件和代码行组织成逐步导览。格式支持 ref 绑定 Git 版本、pattern 定位内容，并列出导览过期检测的配套工具。RepoTrail 采用兼容的 .tour 导出格式，并把生成时的版本和待核实部分写入结果。版本绑定与过期检测已经有成熟前例。[仓库与格式说明](https://github.com/microsoft/codetour)

### DeepWiki-Open

当前 README 已列出代码导览。检查的 codemap 服务使用 skeleton 与 enrich 两阶段生成，携带引用和源码上下文，并通过片段查找恢复实际行号。源码引用与自动导览已经属于现有能力。RepoTrail 将其作为功能对照参考，改进价值需要通过固定任务集验证。[仓库](https://github.com/AsyncFuncAI/deepwiki-open) · [检查的服务源码](https://github.com/AsyncFuncAI/deepwiki-open/blob/main/api/services/codemap.py)

### PocketFlow Codebase Knowledge

设计文档给出了清楚的用户故事、输入输出、共享数据与节点分工。代码流程依次获取仓库、识别抽象、分析关系、安排章节、撰写章节与汇总。RepoTrail 参考这种分阶段方式，把符号索引与源码工具放在生成前，把结构性验证与有限修订放在生成后。[需求与设计](https://github.com/The-Pocket/PocketFlow-Tutorial-Codebase-Knowledge/blob/main/docs/design.md) · [工作流源码](https://github.com/The-Pocket/PocketFlow-Tutorial-Codebase-Knowledge/blob/main/flow.py)

### Aider

官方仓库地图说明覆盖符号签名、依赖关系、图排序与 Token 预算。RepoTrail 首版使用 Python AST、入口邻域和受限符号地图，工具可按需读取更多源码。当前实现采用较简单的选择方法，尚未实现 Aider 的完整图排序策略。[官方说明](https://aider.chat/docs/repomap.html) · [仓库](https://github.com/Aider-AI/aider)

### Repomix

提供文件过滤、仓库打包、Token 统计、压缩、CLI 和库接口。RepoTrail 首版的输入范围固定为 Python，使用 Git 文件列表与自有 AST 索引建立快照。后续扩展多语言时，可以重新评估将 Repomix 作为预处理依赖。[仓库](https://github.com/yamadashy/repomix)

## 开发工作流与文档

GitHub Spec Kit 当前提供需求、计划、任务与验证流程。项目借鉴其可追踪的需求和验收组织方式，以 spec.md、architecture.md 和固定评测集记录决策。首版采用轻量文档结构。[Spec Kit](https://github.com/github/spec-kit)

matklad 的架构文档建议强调问题概览、模块地图、架构约束、系统边界与横切关注点。RepoTrail 的模块地图与架构文档按这些问题组织。稳定的架构介绍与绑定源码快照的具体导览各自记录版本信息。[ARCHITECTURE.md](https://matklad.github.io/2021/02/06/ARCHITECTURE.md.html)

## 采用的改进假设

面向第一次接触仓库的人，从可识别入口推荐一条阅读流程。模型解释符号时，程序提供真实源码与能静态解析的关系，检查输出中的未知符号、重复步骤、入口偏移和连接缺口，然后允许一次补查修订。

待验证的假设是，这种工作流能够提高可核对的流程覆盖与解释正确性，并保持可接受的调用成本。固定评测将同时报告正确性、覆盖、未解决连接和成本。若某项指标下降，原始结果同样保留。

## 复用边界

当前代码为独立实现，参考资料影响了工作流、导览格式、上下文选择和文档组织。项目采用 MIT，设计指导的 CC BY 4.0 署名在 THIRD_PARTY.md 中记录。真实模型效果与竞品运行结果尚待实验。

