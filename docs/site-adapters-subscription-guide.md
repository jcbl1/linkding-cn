# 订阅源开发指南

## 快速开始

> **数据存储**：所有 site-adapters 数据存储在 `data/site_adapters/` 目录中。该目录是一个独立的数据集，可通过 URL 下载获取，不在 linkding 主仓库中版本控制。

### 1. 创建订阅源文件

创建一个 `.jsonc` 文件：

```jsonc
{
  "_meta": {
    "name": "我的订阅源",
    "version": 1
  },
  "domains": {
    "example.com": {
      "metadata": {
        "select_title": ["h1.article-title"]
      },
      "snapshot": {
        "remove_elements": [".ads", ".sidebar"]
      }
    }
  }
}
```

### 2. 本地测试

```bash
# 验证订阅源格式
python manage.py site_adapter validate-subscription ./my-subscription.jsonc

# 或通过 HTTPS URL 验证
python manage.py site_adapter validate-subscription https://example.com/my-subscription.jsonc
```

### 3. 托管

将文件托管到任何 HTTPS 静态托管服务（GitHub、GitLab Pages 等）。

### 4. 分发

用户在 linkding-cn 的 Site Adapters 管理页面中添加你的订阅源 URL 即可。

## 格式规范

### 顶层结构

```jsonc
{
  "_meta": { ... },    // 必需：元数据
  "domains": { ... }   // 必需：域名配置
}
```

### _meta 字段

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `name` | string | 否 | 订阅源名称 |
| `version` | integer | **是** | 单调递增版本号 |
| `checkUpdateUrl` | string | 否 | 轻量版本检测 URL |

### checkUpdateUrl

指向一个返回以下格式的小文件：

```json
{"id": 1721894400000, "version": 3}
```

客户端先检查此文件，版本有变化才下载完整订阅。节省流量。

### domains 字段

key 为域名，value 为配置对象或别名字符串：

```jsonc
{
  "domains": {
    "zhihu.com": { ... },           // 精确匹配
    "*.zhihu.com": { ... },         // 通配符匹配
    "alias.example.com": "target.example.com"  // 别名
  }
}
```

### 域名配置

```jsonc
{
  "metadata": {
    "select_title": ["h1.title"],        // 标题选择器（数组，按优先级）
    "select_description": ["meta[name=description]"],
    "select_image": [".article-image img"]
  },
  "snapshot": {
    "remove_elements": [".ads", ".modal"],  // 去除的元素
    "keep_elements": ["article"],           // 只保留的元素
    "process_lazy_images": true,            // 修复懒加载图片
    "script": "./scripts/mysite.js",        // 自定义脚本（可选）
    "toggles": {                            // 用户可切换的选项
      "sidebar": {
        "selector": ".Sidebar",
        "label": "侧边栏",
        "default": true
      }
    }
  },
  "reader": {
    "defuddle_args": {
      "contentSelector": ".article-content"
    }
  },
  "auth": {
    "cookie": { "type": "login" }         // 需要用户登录
  }
}
```

## 自定义脚本

### 适用场景

声明式配置（`remove_elements`、`keep_elements`）无法满足需求时使用脚本：

- 展开折叠内容（移除 `.is-collapsed` 类）
- 修复链接跳转（重写 URL）
- 特定站点的复杂 DOM 操作

### 脚本引用

```jsonc
{
  "snapshot": {
    "script": "./scripts/custom.js"     // 相对路径（相对于订阅文件）
  }
}
```

或使用 URL：

```jsonc
{
  "snapshot": {
    "script": "https://example.com/scripts/custom.js"
  }
}
```

### 执行环境

脚本在 **SingleFile 浏览器上下文**中执行：

1. SingleFile 先执行声明式配置（`remove_elements` 等）
2. 然后触发 `single-file-on-before-capture-request` 事件
3. 脚本在此事件中执行，可使用原生 `document` API
4. 最后 SingleFile 捕获处理后的 HTML

### 脚本示例

```javascript
// 后处理
(function() {
  // 展开折叠内容
  document.querySelectorAll('.MainContent.is-collapsed').forEach(function(el) {
    el.classList.remove('is-collapsed');
  });
  
  // 去除展开按钮
  document.querySelectorAll('.ContentItem-expandButton').forEach(function(el) {
    el.remove();
  });

})();
```

### 注意事项

- 脚本在浏览器中运行，可使用 `document`、`querySelector` 等原生 API
- 懒加载图片修复已由声明式 `process_lazy_images` 处理，脚本无需重复
- 简单的元素移除优先用 `remove_elements`，复杂逻辑才用脚本
- Reader 模式不支持自定义脚本

## 目录结构

如果订阅源包含脚本，目录结构如下：

```
my-subscription/
├── subscription.jsonc    # 订阅配置
└── scripts/              # 脚本目录
    ├── x.js
    └── reddit.js
```

## 版本管理

- 每次修改规则后，递增 `_meta.version`
- 客户端只在远程版本 > 本地版本时更新
- 建议使用 `checkUpdateUrl` 减少不必要的完整下载

## 示例

### 基础示例：优化快照

```jsonc
{
  "_meta": { "name": "快照优化", "version": 1 },
  "domains": {
    "github.com": {
      "snapshot": {
        "remove_elements": [".Header", ".footer", ".js-repo-nav"]
      }
    }
  }
}
```

### 完整示例：需要认证 + 自定义脚本

```jsonc
{
  "_meta": { "name": "需认证站点", "version": 1 },
  "domains": {
    "zhihu.com": {
      "metadata": {
        "select_title": ["h1.QuestionHeader-title", "meta[property=\"og:title\"]"],
        "select_description": [".RichContent-inner", "meta[property=\"og:description\"]"]
      },
      "snapshot": {
        "remove_elements": ["header", "footer", "nav", ".Sticky", ".Modal-wrapper"],
        "process_lazy_images": ["data-actualsrc", "data-original"],
        "script": "./scripts/zhihu.js",
        "toggles": {
          "comments": { "selector": ".Comments-container", "label": "评论区", "default": false }
        }
      },
      "auth": { "cookie": { "type": "login" } }
    }
  }
}
```
