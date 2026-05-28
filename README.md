# b_dlt

`b_dlt` 是一个面向 B 站内容沉淀的离线优先工作流项目，用于把 UP 主视频整理为本地可检索的文字知识库。

当前目标：

1. 批量获取单个 UP 主的视频清单
2. 下载或提取音频
3. 优先获取字幕，缺失时执行音频转写
4. 生成结构化文本和大纲总结
5. 建立本地全文检索索引

## 项目状态

当前仓库五个核心阶段都已有首版实现。

现在也提供了一个总控脚本 `run_creator_pipeline`，用于一条命令串起完整流程。

## 适用场景

- 批量整理单个 UP 主的视频为本地 Markdown + SQLite 知识库
- 优先复用字幕，缺失时再调用 ASR，尽量减少转写成本
- 需要本地可追溯产物，而不是黑盒 SaaS

不适合的场景：

- 想直接要 Web UI 或在线协作平台
- 想一开始就上向量数据库 / RAG / 生产部署
- 需要跨平台全自动登录态管理

## 依赖前提

运行前至少要有：

- Python `3.9+`
- `yt-dlp`
- `ffmpeg`

按需可选：

- `OPENAI_API_KEY`
  仅当视频没有可用字幕、需要 ASR 兜底时才需要
- B 站 cookies
  仅当匿名访问不稳定、需要登录态拉取或下载时使用

注意：

- 本仓库不会自动帮你安装 `yt-dlp` 和 `ffmpeg`
- `fetch_manifest` 不支持直接读取浏览器 cookies，稳定方案是先导出 `cookies_file`
- 任何 cookies、token、API key 都不应该进仓库

## 开发启动

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

最小检查：

```bash
make verify
```

## 快速开始

1. 复制环境变量模板

```bash
cp .env.example .env
```

2. 按需填写 `.env`

- 如果你准备依赖字幕优先，不一定马上需要 `OPENAI_API_KEY`
- 如果你要用 ASR 兜底，需要配置 `OPENAI_API_KEY`
- 如果你长期处理 B 站内容，建议配置 `BILIBILI_COOKIES_FILE`

3. 先导出一份可复用 cookies 文件

```bash
python scripts/export_bilibili_cookies.py \
  --browser chrome \
  --output-file /absolute/path/to/bili.cookies.txt
```

4. 一条命令跑完整流程

```bash
python scripts/run_creator_pipeline.py \
  --creator-mid 1339268 \
  --auth-mode cookies_file \
  --cookies-file /absolute/path/to/bili.cookies.txt \
  --query 海鸥
```

5. 结果默认写入：

- `data/manifests/`
- `data/audio/`
- `data/subtitles/`
- `data/transcripts/raw/`
- `data/transcripts/clean/`
- `data/summaries/videos/`
- `data/summaries/creators/`
- `db/knowledge.db`

## 目录概览

```text
data/         原始数据、中间产物、摘要导出
db/           SQLite 数据库与索引
logs/         运行日志
scripts/      工作流脚本
tests/        自动化测试
```

完整规范见 [AGENTS.md](./AGENTS.md)。

## 仓库入口

- `pyproject.toml`：项目元数据与开发依赖
- `Makefile`：本地检查与测试快捷命令
- `CHANGELOG.md`：版本变更记录
- `SECURITY.md`：安全与敏感信息处理规则
- `CODE_OF_CONDUCT.md`：社区协作行为准则

## 计划中的工作流

1. `fetch_manifest`
   拉取 UP 主视频列表并写入本地 manifest
2. `download_audio`
   下载视频或直接提取音频，支持增量跳过
3. `transcribe`
   优先使用现成字幕，缺失时调用 ASR
4. `summarize`
   生成单视频大纲与 UP 主维度聚合总结
5. `build_index`
   将文本写入本地 SQLite FTS 索引
6. `run_creator_pipeline`
   顺序执行 manifest、audio、subtitle、transcribe、summarize、index

当前进度：

- `fetch_manifest`：已实现首版
- `download_audio`：已实现首版
- `transcribe`：已实现首版
- `summarize`：已实现首版
- `build_index`：已实现首版

## 推荐工作流

推荐优先使用总控脚本，不要手工记六段命令。

默认路径下的一次完整运行顺序：

1. 拉取 `creator_{mid}.json`
2. 下载音频和 `info.json`
3. 下载字幕
4. 优先字幕转写，缺失时走 ASR
5. 生成 clean transcript / video summary / creator summary
6. 刷新 `db/knowledge.db`

只有在你要单独调试某一阶段时，再使用下面这些分步脚本。

`fetch_manifest` 示例：

```bash
python scripts/fetch_manifest.py --creator-mid 123456
```

使用 cookies 文件拉取清单：

```bash
python scripts/fetch_manifest.py \
  --creator-mid 123456 \
  --auth-mode cookies_file \
  --cookies-file /absolute/path/to/bili.cookies.txt
```

`download_audio` 示例：

```bash
python scripts/download_audio.py --manifest data/manifests/creator_123456.json
```

使用浏览器登录态下载音频：

```bash
python scripts/download_audio.py \
  --manifest data/manifests/creator_123456.json \
  --auth-mode browser \
  --browser chrome
```

导出浏览器里的 B 站 cookies 到可复用文件：

```bash
python scripts/export_bilibili_cookies.py \
  --browser chrome \
  --output-file /absolute/path/to/bili.cookies.txt
```

`transcribe` 示例：

```bash
python scripts/transcribe.py --audio-dir data/audio --subtitle-dir data/subtitles
```

`summarize` 示例：

```bash
python scripts/summarize.py --raw-dir data/transcripts/raw
```

`build_index` 示例：

```bash
python scripts/build_index.py --query 知识管理
```

`run_creator_pipeline` 一条命令跑完整流程：

```bash
python scripts/run_creator_pipeline.py \
  --creator-mid 1339268 \
  --auth-mode cookies_file \
  --cookies-file /absolute/path/to/bili.cookies.txt \
  --query 海鸥
```

如果希望脚本先从浏览器导出 cookies，再复用这份 cookies 跑完整流程：

```bash
python scripts/run_creator_pipeline.py \
  --creator-mid 1339268 \
  --export-cookies \
  --browser chrome \
  --query 海鸥
```

默认输出位置：

- `data/manifests/creator_{mid}.json`
- `data/audio/`
- `data/subtitles/`
- `data/transcripts/raw/`
- `data/transcripts/clean/`
- `data/summaries/videos/`
- `data/summaries/creators/`
- `db/knowledge.db`

如果希望把产物放到独立工作目录：

```bash
python scripts/run_creator_pipeline.py \
  --creator-mid 1339268 \
  --workspace-root /absolute/path/to/workspace \
  --db-path /absolute/path/to/workspace/knowledge.db
```

## 开发原则

- 字幕优先，ASR 兜底
- 增量同步优先，不重复处理
- 文件产物可读，索引结构可查
- 先做本地检索，不预设复杂 RAG

## 环境变量

复制 `.env.example` 后按需填充，不要提交真实 `.env`。

项目内的 B 站鉴权不是单独设计成“token”，而是统一抽象为 `auth/cookies` 配置。

支持四种模式：

- `none`：匿名请求
- `browser`：通过 `yt-dlp --cookies-from-browser` 读取本机浏览器登录态
- `cookies_file`：读取 Netscape 格式 cookies 文件
- `cookie_header`：直接传原始 `Cookie` 请求头

可用环境变量：

```bash
BILIBILI_AUTH_MODE=none
BILIBILI_BROWSER=chrome
BILIBILI_COOKIES_FILE=/absolute/path/to/bili.cookies.txt
BILIBILI_COOKIE_HEADER=SESSDATA=...; bili_jct=...
```

优先级规则：

1. CLI 参数优先于环境变量
2. 未显式传参时，脚本会读取 `.env`
3. `download_audio` 支持四种模式
4. `fetch_manifest` 只支持 `none`、`cookies_file`、`cookie_header`

注意：

- `browser` 模式只适合 `download_audio`，因为它依赖 `yt-dlp` 读取浏览器 cookies
- `run_creator_pipeline --export-cookies` 会先导出一份 cookies 文件，再用这份文件跑完整流程
- 如果你把全局环境设成 `BILIBILI_AUTH_MODE=browser`，`fetch_manifest` 会自动忽略这个不兼容模式，并退回到匿名或其他兼容配置
- `cookies_file` 建议优先使用，这样同一份配置可以同时覆盖 `fetch_manifest` 和 `download_audio`
- 推荐先运行 `export_bilibili_cookies.py` 导出一份本地 cookies 文件，再把 `BILIBILI_COOKIES_FILE` 指向这份文件

## 云端运行建议

可以云端运行，但不要把“浏览器登录态读取”也放到云端。

建议拆分成两段：

1. 本地执行 `export_bilibili_cookies.py`
   从你自己的浏览器导出 cookies 文件
2. 云端执行 `run_creator_pipeline.py`
   把 cookies 文件作为挂载文件或 secret 注入

原因很直接：

- 云服务器通常没有你真实登录过的浏览器环境
- 浏览器 cookies 解密在 macOS / Windows 上常依赖本机钥匙串或系统凭据
- 云端更适合跑 `manifest -> audio -> subtitle -> transcribe -> summarize -> index`

如果你明确不需要登录态，也可以全程匿名运行，但稳定性会差一些。

## 输出结构

最终你会得到两类核心资产：

1. Markdown
   适合本地阅读、Obsidian、二次整理
2. SQLite FTS 索引
   适合命令行检索和后续接自己的应用

典型结构：

```text
data/
├── manifests/
├── audio/
├── subtitles/
├── transcripts/
│   ├── raw/
│   └── clean/
└── summaries/
    ├── videos/
    └── creators/
db/
└── knowledge.db
```

这套结构默认就是本地知识库，不需要额外引入向量数据库。

## 常见问题

`run_creator_pipeline` 跑到转写时报 `OPENAI_API_KEY` 缺失：

- 说明当前视频没有可用字幕，已经进入 ASR 兜底路径
- 配置 `OPENAI_API_KEY` 后重跑即可

`fetch_manifest` 无法使用 `browser` 模式：

- 这是设计限制，不是 bug
- 先导出 `cookies_file`，再给 `fetch_manifest` 和总控脚本复用

macOS 导出浏览器 cookies 失败：

- 通常不是脚本逻辑问题，而是系统钥匙串权限未放行
- 允许对应进程访问浏览器凭据后重试

`build_index` 报 `No markdown documents found to index`：

- 说明前面的 `summarize` 没有产出文件
- 先检查 `data/transcripts/raw/` 和 `data/summaries/`

## 验证

在脚本和测试落地后，默认执行：

```bash
make verify
```

## 开源说明

本仓库不包含任何真实音视频、字幕、cookies、token 或转写数据。

使用者需要自行确保：

1. 符合 B 站平台条款
2. 尊重内容版权与作者权益
3. 合法使用下载、转写和整理后的内容

另外需要明确：

- 本项目不承诺绕过平台限制
- 不为任何侵权、批量搬运、未授权分发负责
- 提交 issue 或 PR 时，不要附带真实 cookies、完整请求头、API key 或个人数据

## License

本项目使用 [Apache License 2.0](./LICENSE)。
