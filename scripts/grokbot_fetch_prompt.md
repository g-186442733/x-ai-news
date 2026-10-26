# Grok Bot 每小时采集例行任务（方案 B）

> 本文件是交给 **已有 Grok Bot 运行环境** 的操作提示词，不是 GitHub Actions 会执行的脚本，也不会因为文件存在而自动建立调度。Actions 只读取默认分支中已落盘的 `data/latest.json` 做离线构建，绝不调用 X。

## 角色与执行位置

你是在 Grok Bot 自身运行环境中执行采集的例行任务。请先确认当前运行时真实可用的 X 公开读取工具（例如运行时提供的 `x` 命名空间工具），以实际工具清单为准；不要杜撰 MCP 方法名、端点或参数，不要申请、填写或猜测 Bearer token，也不要读取任何 X 开发者密钥。

- 周期：每小时执行一次。
- 建议时间：`Asia/Shanghai` 每小时 `:05` 开始，由该环境已有调度器登记。
- 单实例：同一时间只允许一个采集任务运行，避免两个任务交错写 `latest.json`。
- 只采集公开信息；不采集私信、私有列表、登录态内容或任何个人敏感数据。

## 采集内容与上限

每轮分别获取以下六类内容：

1. 中文 AI 帖子 → `posts_zh`
2. 英文 AI 帖子 → `posts_en`
3. AI 新闻 → `news`
4. 产业趋势与商业动态 → `industry_news`
5. AI 相关趋势 → `trends_ai`
6. Grok Bot 相关帖子 → `grokbot_posts`

建议范围与上限（上限只约束本轮新采集，不截断已有快照）：

- 普通帖子优先最近 48 小时；Grok Bot 帖子可覆盖最近 30 天，以保持相关性。
- `posts_zh`、`posts_en`、`grokbot_posts` 各最多 20 条。
- `news`、`industry_news`、`trends_ai` 各最多 10 条；`industry_news` 建议每轮 5–10 条（允许确认成功的空数组）。
- 所有条目保留真实原帖时间、原始文本、来源链接和采集时可见的互动数。帖子时间不是采集时间。

### `industry_news` 采集说明

- 主题偏向商业与产业层：SaaS 退潮 / 定制软件 / agentic 平台 / AI 就业 / AI 安全等，不要与偏模型发布、产品 demo 的 `news` 混写。
- 来源优先英文科技媒体（The Verge、TechCrunch、Ars Technica、Wired 等）与中文科技媒体；可用现有 Grok Bot 能力：X `search_news` / `search_posts_all` 按媒体域名或产业关键词检索，必要时对公开页做 WebFetch。
- 每条必须写真实媒体名到 `source`（如 `TechCrunch`、`The Verge`、`36氪`）；`url` 允许媒体站外链（`theverge.com` / `techcrunch.com` 等），不必限于 `x.com`。
- 不编造标题、摘要、来源或链接；没有可靠出处就跳过该条，不要用推测补齐。

## 真实性规则

- 只写实际返回的文本、来源、时间和互动数；不编造新闻事实、标题、摘要、热度、排名或链接。
- 缺失的摘要、主题、互动数保持缺失或空值，不要用推测内容补齐。
- 趋势读取成功但没有明确 AI 词条时，写 `"trends_ai": []`，并在 `trends_note` 写明真实情况；禁止用普通热门词、旧趋势或模拟榜单填空。
- 已批准的 `grokbot_intro.zh` / `grokbot_intro.en` 简介保持原样，不自行改写或生成替代宣传文案。
- `generated_at` 是本轮**完整采集成功**的时间，必须带时区（建议 `+08:00`）。失败轮次不得刷新这个时间。

## 输出契约

候选文件必须是 UTF-8 JSON 对象，根字段完整。最小结构如下（数组内字段按实际返回填写，不要求虚构示例值）：

```json
{
  "generated_at": "YYYY-MM-DDTHH:MM:SS+08:00",
  "timezone": "Asia/Shanghai",
  "source": "Grok Bot built-in X public reads (namespace x)",
  "posts_zh": [
    {
      "author_name": "作者显示名",
      "author_username": "username",
      "text": "原帖正文，保留换行与 emoji",
      "created_at": "YYYY-MM-DDTHH:MM:SS.000Z",
      "like_count": 0,
      "retweet_count": 0,
      "url": "https://x.com/.../status/...",
      "lang": "zh"
    }
  ],
  "posts_en": [],
  "news": [
    {
      "id": "纯数字字符串或已有稳定 ID",
      "name": "新闻标题",
      "summary": "真实摘要；没有就留空字符串",
      "url": "https://x.com/i/trending/...",
      "updated_at": "YYYY-MM-DDTHH:MM:SS.000Z",
      "topics": ["AI"]
    }
  ],
  "industry_news": [
    {
      "id": "稳定 ID 或可从 URL 派生",
      "name": "产业/商业新闻标题",
      "summary": "真实摘要；没有就留空字符串",
      "url": "https://techcrunch.com/...",
      "source": "TechCrunch",
      "updated_at": "YYYY-MM-DDTHH:MM:SS.000Z",
      "topics": ["SaaS", "AI"]
    }
  ],
  "trends_ai": [
    {
      "name": "趋势名称",
      "url": "https://x.com/...",
      "rank": 1,
      "post_count": 0
    }
  ],
  "trends_note": "本轮趋势的真实说明；无 AI 词条时说明已过滤为空",
  "grokbot_posts": [],
  "grokbot_intro": {
    "zh": {"title": "Grok Bot 是什么", "bullets": ["已批准要点"]},
    "en": {"title": "What is Grok Bot", "bullets": ["Approved bullet"]}
  }
}
```

约束说明：

- 六个数组字段必须存在且为数组；显式 `[]` 表示已确认成功的空结果。缺字段、`null` 或类型错误不是空结果。
- `text` 必须非空；`news[].name` 与 `industry_news[].name` 必须非空；`industry_news[].source` 必须为非空媒体名；`trends_ai[].name` 必须非空。
- 互动数写非负整数；无法取得就省略或写 `null`，不要伪造 `0`。
- URL 只写 `http` / `https` 公开地址，不带账号密码，不写 `javascript:` / `data:` 等协议。
- 不要写未知根字段或把上游整段响应塞进快照。

## 失败规则

- 任一必需采集步骤失败或超时：本轮整体失败，**保留上一份 `data/latest.json` 和原来的 `generated_at`**。
- 不要把失败写成“成功空数组”，不要把旧数据混入新快照后标成新时间。
- 只有明确成功读取并确认零结果时，才允许写空数组（尤其是 `trends_ai`）。
- 单条记录字段异常可以让规范化器跳过并给出警告；如果某个非空数组最终没有任何有效条目，`--check` 会失败，本轮不得替换快照。

## 安全写盘（原子替换）

在仓库根目录执行，使用同目录临时文件和单实例锁。以下命令是流程示例；候选 JSON 由你按上面的契约写入 `$candidate`。

```bash
cd /path/to/x-ai-news
mkdir -p data

# 单实例锁（Linux flock）。若运行时没有 flock，可改用 mkdir 原子锁目录。
exec 9>data/.grokbot.lock
flock -n 9 || { echo "another Grok Bot fetch is running"; exit 1; }

candidate="$(mktemp "data/.latest.candidate.XXXXXX")"
trap 'rm -f "$candidate"' EXIT

# 将本轮完整快照写入 $candidate（UTF-8 JSON）。

# 校验候选快照：只做校验和内存规范化，不写任何文件。
# 必须在校验成功后才替换；失败时清理候选文件并保留旧 latest.json。
if python3 scripts/fetch_via_snapshot.py --snapshot "$candidate" --check; then
  python3 - "$candidate" data/latest.json <<'PY'
import os
import sys

source, target = sys.argv[1], sys.argv[2]
os.replace(source, target)
PY
  trap - EXIT
else
  status=$?
  rm -f "$candidate"
  trap - EXIT
  echo "candidate snapshot failed validation; kept previous data/latest.json" >&2
  exit "$status"
fi
```

替换后可选地立即刷新本地派生文件：

```bash
python3 scripts/fetch_via_snapshot.py --snapshot data/latest.json --out-dir data
```

注意：

- 临时文件必须与 `data/latest.json` 位于同一目录，`os.replace` 才是同文件系统原子替换。
- 如果 `--check` 失败，删除临时文件，旧快照和旧派生文件都不动。
- 如果替换成功但随后本地派生刷新失败，以 `latest.json` 为准；下一次 Actions 构建会重新规范化。不要回写一个半成品。
- 不要把 token、环境变量、Authorization 头、私有数据或未知上游响应写进候选 JSON 或日志。

## 交接与报告

报告至少包含：

- 快照路径（`data/latest.json`）与 `generated_at`
- 六个数组各自数量
- 规范化警告数量与所属数组/索引
- 本轮是否为空结果、是否失败

数据进入 GitHub 的交接：

1. 本地写盘成功 **不等于** Pages 已更新。
2. 将 `data/latest.json` 交给调用方既有的仓库发布流程。默认只报告并交接，不自行推送或合并默认分支。
3. 如果该 Grok Bot 环境已获得调用方明确授予的 git 发布权限，按既有流程提交并推送到约定分支；禁止 `git push --force`、禁止改写工作流、禁止用 Actions 回写数据。默认分支收到新的 `data/latest.json` 后，`pages.yml` 才会自动执行构建与部署。
4. 报告不得声称 Actions 已成功、站点已部署或数据已进入默认分支，除非已经实际确认。

## 明确禁止

- 不在 Actions、测试或构建脚本中请求 X、调用 MCP `x`、使用 Bearer 或抓取代理。
- 不新建独立采集工作流，不让 Actions 承担采集或数据回写。
- 不生成模拟趋势、模拟互动数、模拟新闻或替代简介。
- 不因为本地成功而把“每小时构建”描述成“每小时一定有新数据”；新鲜度以 `generated_at` 为准。
