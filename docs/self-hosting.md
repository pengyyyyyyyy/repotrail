# 团队版部署与运维

## 本地启动

安装 Python 3.11 或更新版本与 Git，在项目虚拟环境中执行以下命令。

```sh
python -m pip install -e '.[dev,embeddings]'
python -m repotrail.cli admin create admin --name 管理员
python -m repotrail.cli seed --username admin --source /path/to/repotrail
python -m repotrail.cli serve --team --port 8766
```

Windows 使用 `.venv\Scripts\python.exe`，macOS 和 Linux 使用 `.venv/bin/python`。管理员命令通过交互输入设置密码。打开 http://127.0.0.1:8766 登录。

本地快速启动使用 `.repotrail/team.db` SQLite 数据库和内嵌 worker，适合开发、示例与功能验证。共享部署使用下方的 PostgreSQL 配置。

`seed` 创建 RepoTrail 示例项目，源码由 `--source` 指定。示例包含明确标注的模拟企业文档。索引任务自动排队，完成后可在页面中生成课程。Python 源码的默认分析上限为 300 个文件、单文件 200 KB、总计 4 MB。

首次使用本地嵌入会下载多语言模型，缓存位于数据目录的 `embeddings` 子目录。预置该缓存可用于具备对应模型运行依赖的离线部署。

## Docker Compose

依赖 Docker Engine 与 Compose v2。

1. 将 `deploy/team.env.example` 复制为项目根目录的 `.env`。
2. 在 `.env` 中填写独立的 `REPOTRAIL_DB_PASSWORD`。
3. 执行以下命令。

```sh
docker compose up --build -d
docker compose run --rm web repotrail admin create admin --name 管理员
docker compose exec web repotrail seed --username admin --source /app
```

默认访问地址为 http://127.0.0.1:8766。Compose 启动 PostgreSQL 17 与 pgvector、Web 和独立 worker。Web 与 worker 共享运行数据卷，数据库使用独立数据卷。应用进程以专用非 root 用户运行。

`REPOTRAIL_REPOSITORY_ROOT` 指定的宿主机目录以只读方式挂载到容器的 `/repositories`。在项目源码位置填写容器内路径，例如 `/repositories/example-project`。HTTPS 仓库地址与访问令牌在项目创建表单中分别填写。

通过反向代理提供 HTTPS 时，将 `REPOTRAIL_PUBLIC_URL` 设置为完整公开地址，将对应域名加入 `REPOTRAIL_ALLOWED_HOSTS`，并设置 `REPOTRAIL_COOKIE_SECURE=1`。反向代理为事件流保留长连接并关闭响应缓冲，问答连接需要至少 210 秒的读取超时。

## 首次配置与培训流程

1. 管理员登录，在“模型与用量”配置兼容 Chat Completions 的服务，获取模型并测试连接。
2. 知识检索默认使用本地多语言模型，也可配置独立的兼容 Embeddings API。
3. 创建项目，填写本地目录或 HTTPS Git 地址，并建立源码索引。
4. 上传 Markdown 或文本型 PDF，补充业务背景、架构决策和团队规范。
5. 创建成员账号，并加入相应项目。
6. 生成课程草稿，逐章修改和审核后发布。
7. 成员学习、追问并提交总结，管理员通过“学习验收”给出意见或确认完成。

模型配置更新后用于后续任务。更换嵌入模型后，在设置页重建项目检索索引。关键词模式可用于检查检索流程和构造评测对照。

模型单价按每百万输入与输出 Token 配置，可同时设置计价币种。调用记录保存服务实际返回的 Token 数和当时使用的单价。估算结果随项目与成员归属，便于复核。

### 生成预算

| 阶段 | 单次请求 max_tokens |
| --- | --- |
| 课程大纲 | 6,500 |
| 每章正文 | 8,500 |
| 追问检索规划 | 2,400 |
| 追问回答 | 6,000 |
| 阅读起点推荐 | 4,500 |
| 会话摘要 | 1,800 |

课程按 4–12 章分别生成，大纲、章节与阅读起点结构各允许一次修订。追问先执行最多两个受限检索，再流式生成回答；引用补查最多执行一次。DeepSeek 官方接口使用文档支持的 low 推理强度，输出额度同时覆盖推理与正文。

实际输出规模和时延由资料、问题及模型决定。兼容客户端还限制上下文大小、请求次数与超时。[DeepSeek 思考模式说明](https://api-docs.deepseek.com/guides/thinking_mode)

## 数据与凭据

数据库保存账号、授权关系、源码快照、文档版本、知识片段、课程、消息、分享、学习总结、审核记录和任务用量。

团队版使用 Argon2 密码哈希、带有效期的服务端登录会话和 CSRF 校验。项目访问在服务端检查。个人会话按所有权授权，主动分享后由相应项目成员查看。成员移出项目后，原有登录会话中的项目访问也会重新检查。

模型与 Git 凭据加密保存到数据库。默认主密钥位于数据目录的 `team.key`，也可通过 `REPOTRAIL_SECRET_KEY` 提供 Fernet 主密钥。凭据恢复依赖数据库和对应主密钥。模型连接元数据位于 `provider.json`，嵌入配置位于 `embedding.json`。

已有桌面版模型连接可通过原有系统凭据库迁移到团队凭据存储。CLI 读取团队配置时使用相同的加密凭据后端。

## 环境变量

| 变量 | 用途 |
| --- | --- |
| REPOTRAIL_DATA_DIR | 数据目录，默认工作目录下的 .repotrail |
| REPOTRAIL_DATABASE_URL | PostgreSQL 连接地址，支持 postgresql+psycopg 协议 |
| REPOTRAIL_SECRET_KEY | 可选的部署主密钥，默认自动生成到数据卷 |
| REPOTRAIL_ALLOWED_HOSTS | 允许的 Host，逗号分隔 |
| REPOTRAIL_PUBLIC_URL | 浏览器访问的完整服务地址，用于来源校验 |
| REPOTRAIL_COOKIE_SECURE | HTTPS 部署设置为 1 |
| REPOTRAIL_EMBEDDED_WORKER | 本地默认 1，独立 worker 部署设置为 0 |
| REPOTRAIL_EMBEDDINGS | local、api 或 off |
| REPOTRAIL_EMBEDDING_MODEL | 独立嵌入模型 ID |
| REPOTRAIL_EMBEDDING_URL | 兼容嵌入 API 根地址 |
| REPOTRAIL_EMBEDDING_KEY | 嵌入 API 密钥 |
| REPOTRAIL_API_KEY | 生成模型 API 密钥的环境配置 |
| REPOTRAIL_BASE_URL | 生成模型 API 根地址 |
| REPOTRAIL_MODEL | 生成模型 ID |
| REPOTRAIL_INPUT_PRICE | 每百万输入 Token 的单价 |
| REPOTRAIL_OUTPUT_PRICE | 每百万输出 Token 的单价 |
| REPOTRAIL_CURRENCY | 费用估算币种 |

嵌入相关环境变量覆盖对应页面设置。生成模型配置沿用现有 ProviderStore 的页面配置与环境初始值行为。

## 任务恢复与版本

worker 为任务领取租约并定期续期。失去租约的任务重新排队，达到自动恢复次数后提供手动重试。索引、文档与课程数据使用事务提交，生成实体与任务关联，以保持重试的一致性。

每个会话同时处理一个回答，客户端请求 ID 支持问题提交去重。长会话保留原始消息，生成时使用有限历史窗口与会话摘要。停止或失败后保留消息状态，可以在会话或任务页重试。

源码与文档更新后产生新的资料版本。课程保留自己的资料清单。已发布课程内容保留原版本，管理员通过新草稿发布后续课程。删除文档会清理其知识片段，已有生成文本和学习记录保留，可据此复核并发布更新课程。

## 备份与恢复

先停止写入服务和 worker，然后备份数据库及凭据恢复材料。

```sh
docker compose stop web worker
docker compose exec db pg_dump -U repotrail -Fc -f /tmp/repotrail.dump repotrail
docker compose cp db:/tmp/repotrail.dump .repotrail-backup.dump
docker compose cp web:/var/lib/repotrail/team.key .repotrail-backup.key
docker compose cp web:/var/lib/repotrail/provider.json .repotrail-backup-provider.json
docker compose cp web:/var/lib/repotrail/embedding.json .repotrail-backup-embedding.json
docker compose start web worker
```

按实际已创建的配置文件执行对应复制命令。使用环境主密钥时，将该密钥纳入企业密钥备份流程。备份包含资料和凭据恢复材料，应按企业备份权限管理。

在空数据库与对应数据卷上恢复。

```sh
docker compose stop web worker
docker compose cp .repotrail-backup.dump db:/tmp/repotrail.dump
docker compose exec db pg_restore -U repotrail -d repotrail --clean --if-exists /tmp/repotrail.dump
docker compose cp .repotrail-backup.key web:/var/lib/repotrail/team.key
docker compose cp .repotrail-backup-provider.json web:/var/lib/repotrail/provider.json
docker compose cp .repotrail-backup-embedding.json web:/var/lib/repotrail/embedding.json
docker compose run --rm --user root web chown -R 10001:10001 /var/lib/repotrail
docker compose start web worker
```

本地 SQLite 模式在服务停止后备份数据目录。嵌入模型缓存与远程 checkout 缓存可重新构建。源码历史、上传文档和学习记录保存在数据库中。

## 升级与验证

升级前执行备份。固定应用版本后重建镜像，执行 `repotrail migrate`，再启动 Web 与 worker。当前团队数据库模式版本为 1，程序会拒绝不兼容的模式版本。

```sh
python -m pytest -q
node --check repotrail/static/team.js
python -m benchmarks.run verify
```

回归覆盖账号权限、跨项目访问、个人会话、分享撤回、课程发布、学习验收、历史恢复、任务重试和凭据脱敏。性能与真实模型评测应记录硬件、资料规模、模型、成功率、耗时和实际返回的用量。

```sh
python -m benchmarks.team retrieval
python -m benchmarks.team load --username admin --concurrency 5
```

检索评测使用随项目提供的固定问题，分别执行关键词与混合检索。真实问答评测通过交互密码登录，创建独立会话、记录实际耗时与用量，结束后清理本次创建的会话。结果默认保存到 Git 忽略的 `benchmarks/results`。`--password-file` 可用于本地受控验证，`--keep` 保留本次评测会话。

设置 `REPOTRAIL_TEST_DATABASE_URL` 后，团队回归同时运行 SQLite 与 PostgreSQL 后端。PostgreSQL 测试为每个用例创建专用测试数据库，并在完成后清理，需要具备创建测试数据库的权限。
