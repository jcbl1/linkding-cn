# Git 分支管理策略

## 分支说明

| 分支 | 用途 | 包含内容 |
|------|------|----------|
| `main` | 主分支，用于部署 | 功能代码 + fork 特定配置（用户名、镜像源等） |
| `for-upstream` | 向上游 PR | 仅功能代码，不含 fork 特定配置 |
| `feat-*` | 功能分支 | 特定功能的开发代码 |

## 标准工作流程

```
1. 从 main 创建功能分支
   git checkout -b feat/my-feature main

2. 在功能分支上开发
   git add .
   git commit -m "feat: my feature"

3. 合并到 for-upstream（用于向上游 PR）
   git checkout for-upstream
   git merge feat/my-feature

4. 合并到 main（用于部署）
   git checkout main
   git merge feat/my-feature
```

## 分支关系图

```
upstream/main
      │
      ▼
origin/main ─────────────────────────────────────
      │                                    ▲
      ├── feat/feature-1 ──────────────────┤
      │                                    │
      ├── feat/feature-2 ──────────────────┤
      │                                    │
      ▼                                    │
origin/for-upstream ───────────────────────┘
      │
      └── 仅包含功能修改，可向上游 PR
```

## Fork 特定配置

以下文件包含 fork 特定的配置，**不应**包含在 `for-upstream` 分支中：

- `.github/workflows/build.yaml` - Docker 镜像名称
- `docker-compose.yml` - 默认镜像名称
- `scripts/build-docker.sh` - 本地构建脚本
- `bookmarks/views/settings.py` - 版本检查 API URL
- `bookmarks/templates/settings/general.html` - GitHub 链接（部分）
- `package.json` - 仓库信息

## 向上游 PR 的步骤

```bash
# 1. 确保 for-upstream 是最新的
git checkout for-upstream
git fetch upstream
git merge upstream/main

# 2. 创建 PR 分支
git checkout -b pr/my-feature

# 3. 合并功能分支
git merge feat/my-feature

# 4. 向上游发起 PR
# 使用 GitHub 界面从 pr/my-feature 到 upstream/main
```

## 注意事项

1. **功能分支**应只包含功能修改，不包含 fork 特定配置
2. **合并到 main 时**可以添加 fork 特定配置
3. **for-upstream 分支**应始终保持干净，可随时向上游 PR
4. 使用 `git add -p` 可以精确选择要暂存的修改
