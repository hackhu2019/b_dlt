---
name: "b_dlt project rules"
description: "B 站视频音频提取、转写、总结与本地知识库项目规范"
---

# 项目目标

本项目用于处理单个或多个 B 站 UP 主的视频内容，形成可增量更新的本地知识库。

当前阶段只做以下事情：

1. 拉取 UP 主视频清单
2. 下载视频或直接提取音频
3. 优先获取字幕，缺失时执行音频转写
4. 生成结构化文本与大纲总结
5. 建立本地全文检索索引

不在当前阶段做的事情：

1. Web UI
2. 向量数据库
3. 自动部署到生产环境
4. 数据库 schema 迁移

# 沟通与执行

- 默认中文交流
- 结论先行，再给理由
- 遇到模糊需求，先给最合理方案，再等主人决定是否调整
- 不做未请求的功能扩展、重构或格式化清理

# 红线

以下操作必须先得到主人明确确认：

1. 删除文件、目录或执行 git 回滚
2. 修改 `.env`、密钥、token、CI/CD 配置
3. 数据库 schema 变更或数据迁移
4. 安装新的全局依赖或修改系统配置
5. 发布到 GitHub、npm、生产环境或任何外部平台

# 目录规范

目录结构固定如下：

```text
b_dlt/
├── AGENTS.md
├── README.md
├── CONTRIBUTING.md
├── pyproject.toml
├── Makefile
├── .env.example
├── .gitignore
├── .github/
│   └── ISSUE_TEMPLATE/
├── data/
│   ├── manifests/
│   ├── audio/
│   ├── subtitles/
│   ├── transcripts/
│   │   ├── raw/
│   │   └── clean/
│   ├── summaries/
│   │   ├── videos/
│   │   └── creators/
│   └── exports/
├── db/
├── logs/
├── scripts/
└── tests/
```

约定如下：

- `data/manifests/`：UP 主、视频、任务状态等清单文件
- `data/audio/`：转写前保留的音频文件
- `data/subtitles/`：原始字幕与自动字幕
- `data/transcripts/raw/`：逐段原始转写结果
- `data/transcripts/clean/`：清洗后的可读文本
- `data/summaries/videos/`：单视频摘要与大纲
- `data/summaries/creators/`：UP 主维度聚合总结
- `data/exports/`：导出给 Obsidian、Markdown 包或其他外部系统的数据
- `db/`：本地 SQLite 数据库及索引
- `logs/`：运行日志，默认不提交产物
- `scripts/`：可直接执行的脚本，按单一职责拆分
- `tests/`：仅放自动化测试

# 命名规范

- 脚本文件使用 `snake_case.py`
- 数据文件名使用稳定 ID 优先，其次补充可读标题
- Markdown 文件名使用 `YYYYMMDD_slug.md` 或 `video_id_slug.md`
- 不允许中文目录名

建议的数据文件命名：

- manifest：`creator_{mid}.json`
- audio：`{bvid}.m4a`
- subtitle：`{bvid}.json`
- raw transcript：`{bvid}.json`
- clean transcript：`{bvid}.md`
- video summary：`{bvid}.md`

# 实现原则

- 优先复用成熟工具，不重复造轮子
- 字幕优先，ASR 兜底
- 一切流程必须支持增量执行
- 每个脚本只做一件事，输入输出明确
- 先落本地全文检索，不预设向量检索

# 依赖策略

- Python 作为默认实现语言
- 依赖声明集中管理，以 `pyproject.toml` 为准，不允许脚本内隐式要求环境
- 涉及外部协议、平台 API、SDK 的逻辑，先找官方文档或可运行参考实现逐字段对比

# 验证要求

任何编码改动完成后，至少执行对应验证：

1. 语法检查
2. 受影响测试
3. 一次最小可运行验证

在项目脚本建立前，默认验证命令约定如下：

```bash
python -m compileall scripts tests
pytest
```

如已提供仓库级快捷命令，优先使用：

```bash
make check
make test
```

如果当次改动尚未引入 Python 文件或测试文件，需要在交付里明确说明未执行原因。

# 开源约束

- 不提交真实音频、字幕、转写结果、数据库和日志产物
- 不提交 `.env`
- 不提交 cookies、token、会话信息
- README 中必须说明版权、平台条款和使用者责任
- 第三方服务 API Key 只通过环境变量读取

# 文档更新规则

出现以下情况，先改文档再改代码：

1. 新增顶级目录
2. 调整数据流
3. 引入新的核心依赖
4. 更改验证命令
5. 改变开源边界或发布方式
