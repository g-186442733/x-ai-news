# X AI 新闻站

把本地 X 公开信息快照离线规范化，再生成可独立部署的静态站。首页突出 Grok Bot，提供中文 / English 双语 Tab、AI 帖子、AI 新闻、产业趋势与商业动态、AI 趋势和真实的快照更新时间。

- 预期 Pages 地址：<https://axureboutique.github.io/x-ai-news/>
- 仓库：<https://github.com/axureboutique/x-ai-news>
- 技术栈：Python 3.12 标准库 + 原生 HTML/CSS/JavaScript，无第三方 Python 包、无 Node 构建、无后端。

## 本地构建

在仓库根目录执行：

```bash
python3 -m unittest discover -s tests -v
python3 scripts/fetch_via_snapshot.py --snapshot data/latest.json --check
python3 scripts/fetch_via_snapshot.py --snapshot data/latest.json --out-dir data
```

根路径本地预览（`--base-path` 必须与服务路径一致）：

```bash
python3 scripts/build_site.py --data-dir data --out-dir site --base-path / --stale-after-hours 2
python3 -m http.server 8000 --directory site
# 打开 http://localhost:8000/
```

项目子路径预览（模拟 GitHub Pages `/x-ai-news/`）：

```bash
python3 scripts/build_site.py --data-dir data --out-dir site --base-path /x-ai-news/ --stale-after-hours 2
preview_root="$(mktemp -d)"
cp -R site "$preview_root/x-ai-news"
python3 -m http.server 8000 --directory "$preview_root"
# 打开 http://localhost:8000/x-ai-news/
```

确认 CSS/JS 资源和 Tab 正常。`site/` 是生成物，不提交。

## 数据流与规范化契约

```text
data/latest.json（唯一原始快照，只读）
        │
        ├─ fetch_via_snapshot.py ──> posts_zh.json / posts_en.json / news.json
        │                            industry_news.json / trends_ai.json /
        │                            grokbot_posts.json / meta.json
        │
        └─ build_site.py ──────────> site/index.html + site/assets/site.{css,js}
```

`fetch_via_snapshot.py` 是本地文件适配器，只接受本地路径，不联网、不读取 X 凭证：

- 校验根结构、六个数组字段、带时区的 `generated_at`、`trends_note` 和 `grokbot_intro`。
- 帖子稳定 ID 优先取 X 状态 URL 中的 `/status/<数字>`；取不到时按作者、正文、时间计算 SHA-256，不伪造原帖链接。
- 帖子 / 新闻在各自数组内去重，按真实时间倒序；无效时间保留 `null` 并排在末尾；所有有效时间统一输出 UTC ISO 8601。
- 互动数缺失或非法为 `null`，真实的 `0` 保持 `0`。
- URL 只保留带主机的 `http(s)` 地址；拒绝脚本协议、凭证信息和控制字符。
- `trends_ai: []` 是合法成功结果；缺失字段、`null`、损坏 JSON 或“非空数组最终没有有效条目”会失败。
- 输出固定键顺序、缩进和末尾换行；同一快照重复运行字节一致。
- `meta.json` 写入 `schema_version: 1`、`summary`、`snapshot_sha256` 和六个数组文件的 `files_sha256`。
- 七个输出先写临时文件再替换；中途失败会回滚已替换文件。`--check` 只校验和打印摘要，不创建目录、不写任何文件。
- 输入 `data/latest.json` 永远不会被修改。

## 静态构建

`build_site.py` 只读取七个规范化文件：

- 要求 `schema_version == 1`，并核验六个数组的 `files_sha256`、模块计数和字段类型；摘要不符时拒绝构建，不使用原始快照或历史文件兜底。
- 全部内容在构建时写入 HTML，浏览器不请求 JSON；CSS/JS 从 `assets/` 复制到 `site/assets/`。
- 所有上游文本经 `html.escape(..., quote=True)` 转义；URL 再次做协议校验后才生成链接；外链带 `rel="noopener noreferrer"`。
- 趋势为空时中文显示 **“暂无”**，英文显示 “No AI trends at the moment”，并展示经转义的 `trends_note`。
- 时间统一按 `Asia/Shanghai` 展示；快照超过 `--stale-after-hours` 显示过期提示；快照时间超前超过 5 分钟显示“数据时间异常”。
- 中文 / English 两个完整语言面板预渲染；无 JS 时两组内容同时可读，并提供锚点导航。JS 只控制可见性、焦点和 Tab 语义，不插入不可信 HTML。
- 输出目录只允许 `index.html`、`assets/site.css`、`assets/site.js`；包含其它文件会明确失败。构建先写临时目录，成功后替换；输出路径不得指向仓库根、数据目录、源码目录或这些目录的祖先。

## 本地测试

```bash
python3 -m unittest discover -s tests -v
```

测试使用标准库 `unittest` 与临时目录，不访问网络、不修改生产快照。覆盖：

- `tests/test_fetch_via_snapshot.py`：契约校验、长 ID、稳定去重与排序、空趋势、缺失计数与真实 0、错误不覆盖旧文件、`--check` 无写入、输入摘要不变、确定性输出、退出码。
- `tests/test_build_site.py`：Grok Bot 模块、双语与空状态、来源时间保留、固定时钟下的新鲜/过期/未来时间、摘要不符拒绝、恶意文本与 URL 转义、Tab 关联与 ID 唯一、根路径/子路径资源、输出目录保护、产物文件边界。

## GitHub Actions 与 Pages

唯一工作流：`.github/workflows/pages.yml`。

- 触发：每小时 `:17`（cron 按 UTC 解释，上海同样是 `:17`）、`workflow_dispatch`、以及默认分支上 `data/latest.json` / `scripts/**` / `assets/**` / `tests/**` / 工作流自身的 push。
- `build` job 仅允许默认分支：`github.ref == format('refs/heads/{0}', github.event.repository.default_branch)`。
- `build`：checkout → Python 3.12 → `unittest` → 规范化 → 构建 → 配置 Pages → 上传 `site/` artifact。
- `deploy`：`needs: build`，用 `actions/deploy-pages` 部署 `github-pages` 环境，不使用 `always()`。
- 权限最小化：顶层 `contents: read`；`build` 为 `contents: read` + `pages: read`；`deploy` 仅 `pages: write` + `id-token: write`。
- 任一步失败即停止，不上传半成品；已在线的 Pages 版本继续保留。
- Actions 不调用 X / MCP `x`，不包含 Bearer 或采集凭证，不执行 `git commit` / `git push`，不回写仓库数据。

首次启用需要仓库维护方在 **Settings → Pages → Build and deployment → Source** 选择 **GitHub Actions**，并允许 `github-pages` 环境部署。默认使用 `github.io` 域名，不维护 `CNAME` 或 `gh-pages` 分支。首次部署后请以环境实际 URL 为准检查首页、资源、Tab 和趋势空状态。

GitHub 的 schedule 可能延迟或丢弃繁忙时段的运行；公共仓库长期无活动时工作流也可能被自动禁用。保留手动触发入口，并在需要时重新启用 Actions。

## 数据刷新（方案 B）

```text
Grok Bot（真实内置 X 公开读，每小时 :05）
    │ 写候选 JSON → fetch_via_snapshot.py --check → 原子替换 data/latest.json
    ▼
调用方既有仓库发布流程（审核 / 提交 / 推送到默认分支）
    ▼
Actions 每小时 :17 构建 → Pages 部署
```

- 采集在 **Grok Bot 自身运行环境** 完成，Actions 只做校验、规范化、构建和部署。
- Grok Bot 的例行任务说明见 [`scripts/grokbot_fetch_prompt.md`](scripts/grokbot_fetch_prompt.md)：包含采集范围、真实性规则、失败保留旧快照、原子写盘、单实例锁和交接报告。
- 本地写盘不会自动进入 GitHub。必须由调用方既有发布流程把新的 `data/latest.json` 带入默认分支，Actions 才能构建新数据。
- 数据新鲜度只由 `latest.json` 的 `generated_at` 决定；Actions 运行时间不会替换它，也不会被描述成“刚刚更新”。
- 如果同步链路尚未建立，站点仍可上线，但会按小时重建同一份旧快照，并在超过 2 小时后显示过期提示。
- 默认不授权 Grok Bot 直接推送或合并主分支；是否推送取决于调用方是否明确授予发布权限，且必须遵守既有流程，禁止强制推送。

外部运行前置条件（未完成时自动数据更新闭环尚未建立）：

1. 维护方启用 Pages / GitHub Actions，并完成首轮部署。
2. 在 Grok Bot 环境登记每小时任务。
3. 落实快照进入默认分支的既有发布流程。

## 架构约束

- 不新增后端、数据库、登录、评论、全文搜索、自动翻译或客户端 X 嵌入组件。
- 不引入 CDN 字体、第三方脚本、远程头像或远程 JSON；浏览页面不会自动请求 X。
- 不生成假趋势、假新闻、假互动数或替代简介；没有数据时显示明确的空状态。
- `site/` 不包含原始快照、脚本、测试、认证文件或整个仓库副本。
- 不修改 `auto-image-pipeline` 等其它项目；不新增独立采集工作流。

## 主要文件

```text
scripts/fetch_via_snapshot.py   # 本地快照规范化 + 校验
scripts/build_site.py           # 静态站生成 + 输出保护
scripts/grokbot_fetch_prompt.md # Grok Bot 每小时例行任务提示词
assets/site.css                 # 布局、主题、响应式、焦点样式
assets/site.js                  # 语言 Tab、键盘操作、过期提示
data/latest.json                # 唯一原始快照（只读输入）
data/*.json                     # 规范化派生数据，由脚本重建
tests/                          # 标准库 unittest
.github/workflows/pages.yml     # 每小时构建与 Pages 部署
```
