# Workflows staging

当前 `gh` OAuth token（scopes: gist, read:org, repo）缺少 `workflow`，无法把 `.github/workflows/*` 推到 GitHub。

维护方任选其一：
1. `gh auth refresh -h github.com -s repo,read:org,gist,workflow` 后，把本目录两个 yml 拷回 `.github/workflows/` 并推送；或
2. 在 GitHub UI 新建同名 workflow，粘贴本目录文件内容。

拷贝命令：
```bash
mkdir -p .github/workflows
cp docs/workflows/ci.yml .github/workflows/ci.yml
cp docs/workflows/pages.yml .github/workflows/pages.yml
git add .github/workflows && git commit -m "ci: add Actions workflows" && git push
```
