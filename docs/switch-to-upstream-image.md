# 切换到上游镜像

适用于从本仓库镜像 `jcbl1/linkding-cn` 切换到上游镜像 `woohoodai/linkding-cn`。

## 步骤

1. **网页端导出备份**

   打开「设置 → 导入与导出 → 导出书签」，保存 Netscape HTML 备份。

   > 该备份只包含书签、标签和笔记，不含快照、高亮、皮肤与配置，仍需执行第 3 步。

2. **停止服务**

   ```bash
   docker compose stop
   ```

3. **（推荐）备份数据目录**

   ```bash
   tar czf linkding-data-backup-$(date +%F).tgz data/
   ```

   数据目录包含数据库、后台任务队列、密钥、图标、预览图、快照和已导入的皮肤。

4. **切换镜像**

   编辑 `docker-compose.yml`：

   ```diff
   -    image: jcbl1/linkding-cn:latest
   +    image: woohoodai/linkding-cn:latest
   ```

   如果原来使用 `-base` 或 `-base-alpine` 变体，请换成上游同名变体。`.env` 和数据卷映射保持不变。

5. **拉取并启动**

   ```bash
   docker compose pull
   docker compose up -d
   docker compose logs -f
   ```

   确认日志中的数据库迁移和健康检查正常。

## 注意

- 建议把镜像固定到 `v1.3.4` 或更高版本，不要切换到更低版本。
- 从 v1.3.3 升级时，建议先在设置中把主题改为 Auto / Light / Dark；上游不再提供 Nord / Wireframe。
- 回滚请使用第 3 步的完整数据备份，或回到本仓库 `v1.3.4` 镜像。
