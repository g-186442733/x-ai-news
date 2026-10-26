# X AI 新闻站交付说明（方案 B · DN-B）

## 链接

| 项 | URL |
|---|---|
| 仓 | https://github.com/axureboutique/x-ai-news |
| PR（未合并） | https://github.com/axureboutique/x-ai-news/pull/1 |
| Pages | https://axureboutique.github.io/x-ai-news/ |
| Actions | 未启用（见卡点） |

## 刷新路径（方案 B）

1. Grok Bot／本机按 `scripts/grokbot_fetch_prompt.md` 写 `data/latest.json`（及拆分文件）
2. `git push` 到功能分支／main
3. GitHub Actions 仅 `build_site.py` + 部署 Pages（**不**调用 X MCP）

当前因 OAuth 缺 `workflow` scope，Pages 临时用 legacy `gh-pages` 分支部署已构建的 `site/`；workflow 原稿在 `docs/workflows/`。

## DN-B

| 角色 | 工具 | 模型／配置 | 结果 |
|---|---|---|---|
| Navigator | `codex exec -p max --skip-git-repo-check` | gpt-6-astra（max） | `docs/NAVIGATOR_PLAN.md` |
| Driver | `dsh --profile headless` | deepseek-flash + `~/.dsh/cordis.patch.yml` effort max | 实现脚本／资源／测试 |
| 审查 | `codex exec -p max -m gpt-6-astra … review --base main` | gpt-6-astra max | P1–P3 已修并推送 |

本地：`python3 -m unittest discover -s tests` → **38 OK**。

## 卡点

1. **OAuth 无 `workflow` scope**（当前 scopes: gist, read:org, repo）→ 无法推送 `.github/workflows/*`；Actions 无 run、无法绿。处理：`gh auth refresh -h github.com -s repo,read:org,gist,workflow` 后按 `docs/workflows/README.md` 拷回并推送。
2. PR **请勿合并**（按任务要求）；合并后仍须先有 workflow 文件，Actions 才会跑。
3. Driver 截图曾挂起（已杀进程）；站用占位图，不影响构建。
4. Pages 刚 force-push 后 status=`building`；raw `gh-pages` 已含审查修复（UTC `data-generated-at`），CDN 随后刷新。

## 数据

`data/latest.json`：`generated_at=2026-10-26T02:05:34+08:00`；posts_zh=7, posts_en=6, news=7, trends_ai=0（显示「暂无」）, grokbot_posts=6。
