# 订阅源开发指南

## 目录

1. [概述与定位](#1-概述与定位)
2. [目录结构与文件约定](#2-目录结构与文件约定)
3. [适配器注册：config.jsonc](#3-适配器注册configjsonc)
4. [域名配置完整字段参考](#4-域名配置完整字段参考)
5. [配置合并规则](#5-配置合并规则)
6. [元数据提取（metadata）](#6-元数据提取metadata)
7. [快照生成（snapshot）](#7-快照生成snapshot)
8. [阅读模式（reader）](#8-阅读模式reader)
9. [认证体系（auth）](#9-认证体系auth)
10. [自定义脚本开发](#10-自定义脚本开发)
11. [URL 处理](#11-url-处理)
12. [测试与调试验证](#12-测试与调试验证)
13. [订阅源分发](#13-订阅源分发)
14. [从 UserScript 迁移](#14-从-userscript-迁移)
15. [完整示例：从零创建适配器](#15-完整示例从零创建适配器)
16. [附录](#16-附录)

---

## 1. 概述与定位

### 1.1 site-adapters 是什么

site-adapters 是 linkding 的网站适配系统，让 linkding 能够针对不同网站采用不同的策略来提取元数据、生成页面快照、以及提供优化的阅读模式。简而言之，它是"每个网站的个性化规则集"。

### 1.2 它解决什么问题

当你保存书签时，linkding 需要做三件事：

- **元数据提取** —— 获取标题、描述、预览图片
- **快照生成** —— 保存页面 HTML 副本（离线可读）
- **阅读模式** —— 提取正文内容，提供清爽阅读体验

不同网站的结构天差地别。通用规则能覆盖大部分场景，但对特定网站（如知乎、微博、即刻等）需要定制化的提取规则才能获得理想效果。site-adapters 就是这些定制规则的载体。

### 1.3 架构全景

```
用户保存书签
    │
    ▼
load_domain_config(url)
    │
    ├─ 读取 config.jsonc 中的 _adapters 列表
    ├─ 按优先级加载各适配器的 adapters.jsonc
    ├─ URL 域名匹配（精确匹配 + 通配符 *.domain.com）
    ├─ 别名链解析
    ├─ 跨适配器按优先级合并（靠前覆盖靠后）→ defaults 适配器 global_defaults 全局覆盖
    │
    ▼
get_metadata_config / get_snapshot_config / get_reader_config(url)
    │
    ├─ default + section 合并
    ├─ auth 合并（顶级 + default.auth + section.auth）
    ├─ URL 重写（request_url / rewrite_url）
    ├─ 用户 preference（toggles）应用
    │
    ▼
执行引擎
    ├─ 有 script → run_script(script_path, url, config, ...)
    │   ├─ .py → 线程内执行 extract()
    │   └─ .js → 子进程 + stdin JSON
    └─ 无 script → 内置引擎
        ├─ metadata: CSS selector 匹配
        ├─ snapshot: SingleFile + DOM 清理
        └─ reader: defuddle 内容提取
```

---

## 2. 目录结构与文件约定

所有 site-adapters 数据位于 `data/site_adapters/` 目录（由 `LD_SITE_ADAPTERS_DIR` 配置项指定）：

```
data/site_adapters/
├── adapters/                        # 适配器集
│   ├── config.jsonc                 # 适配器注册表（_adapters 列表）
│   ├── defaults/                    # 内置 defaults 适配器（id=name=defaults，目录名不重复）
│   │   ├── adapters.jsonc           #   域名规则 + 全局 defaults
│   │   └── scripts/                 #   自定义脚本
│   ├── <id>.<name>/                 # 其他适配器目录（id≠name时）
│   │   ├── adapters.jsonc           #   域名规则文件（核心）
│   │   └── scripts/                 #   自定义脚本
│   │       ├── extract.py
│   │       ├── snapshot.js
│   │       └── ...
│   └── ...
├── etc/
│   └── templates/                   # 脚本模板
│       ├── metadata_py.py
│       ├── metadata_js.js
│       ├── snapshot_py.py
│       ├── snapshot_js.js
│       ├── reader_py.py
│       └── reader_js.js
└── logs/
    └── execution-YYYY-MM-DD.jsonl   # 执行日志（按天轮转，默认保留30天）
```

### 关键文件说明

| 文件 | 作用 |
|------|------|
| `config.jsonc` | 适配器注册表，定义加载顺序、优先级、订阅源 URL |
| `adapters.jsonc` | 单个适配器的域名规则集，包含 `_meta`、`defaults`（适配器内默认）、`global_defaults`（仅 defaults 适配器，全局覆盖）、`domains` |
| `scripts/*.js` / `scripts/*.py` | 自定义脚本，JS 通过子进程执行，Python 通过线程执行 |

---

## 3. 适配器注册：config.jsonc

### 3.1 管理入口

适配器的添加、编辑、删除、启停、排序均通过管理界面操作：**`/admin/site-adapters`**。

也可以直接编辑 `data/site_adapters/adapters/config.jsonc` 文件。

### 3.2 格式

```jsonc
{
  "_adapters": [
    {
      "name": "MyAdapter",
      "id": "my-publisher-id",
      "source": "https://example.com/my-subscription.jsonc",
      "update_interval": 86400,
      "enabled": true
    },
    {
      "name": "MyLocal",
      "id": "local",
      "source": "/absolute/path/to/adapters.jsonc",
      "enabled": false
    }
  ]
}
```

### 3.3 _adapters 条目字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | 是 | 发布者唯一标识。适配器目录名为 `{id}.{name}` |
| `name` | string | 是 | 适配器名称（UI 显示 + 目录名的一部分） |
| `source` | string | 是 | 文件来源。HTTPS URL（远程订阅）、相对路径或绝对路径（本地适配器） |
| `update_interval` | int | 否 | 远程订阅源的更新间隔（秒），默认 86400（24小时） |
| `enabled` | bool | 否 | 是否启用，默认 true |
| `exclude` | [string] | 否 | `fnmatch` 模式数组，匹配到的域名不会从该适配器加载 |

### 3.4 优先级规则

`_adapters` 数组的顺序**就是优先级顺序**——靠前的适配器覆盖靠后的同名字段。

```
_adapters: [A, B, C]    （A 优先级最高）
合并方向：C → B → A
结果：A 的同名字段覆盖 B 和 C
```

### 3.5 source 字段详解

| source 值 | 含义 | 加载路径 |
|-----------|------|----------|
| `https://...` | 远程订阅源 | 先下载缓存到 `adapters/{id}.{name}/adapters.jsonc`，再从此处加载 |
| `./relative/path` | 本地相对路径 | 相对于 `adapters/` 目录解析 |
| `/absolute/path` | 本地绝对路径 | 直接使用该路径 |

### 3.6 去重规则

同一个 `id+name` 组合只能出现一次，重复的条目会被跳过（保留最先出现的）。

---

## 4. 域名配置完整字段参考

每个域名配置是一个 JSON 对象，支持以下顶级字段。此外适配器文件顶层还有两个特殊键：

| 键 | 作用域 | 说明 |
|----|--------|------|
| `defaults` | 当前适配器 | 该适配器内所有域名的默认模板，被各域名配置覆盖 |
| `global_defaults` | 全局（仅 defaults 适配器） | 覆盖所有适配器所有域名的全局默认值 |

以下为域名配置内的字段：

```jsonc
{
  "example.com": {
    // --- 别名 ---
    "type": "alias",        // 设为 "alias" 后，target 指向另一域名
    "target": "other.com",  // 复用 other.com 的完整配置

    // --- 认证（可放在顶级、default、或各 section 内）---
    "auth": {
      "cookie": { /* Cookie 配置 */ },
      "headers": { /* 自定义 HTTP Header */ },
      "token": { /* OAuth2 Token 配置 */ }
    },

    // --- 默认设置（被 metadata/snapshot/reader 继承）---
    "default": {
      "timeout": 60,
      "proxy": "http://proxy:8080",
      "http": {
        "User-Agent": "...",
        "Referer": "..."
      },
      "request_url": ["pattern", "replacement"],
      "rewrite_url": ["pattern", "replacement"],
      "auth": { /* section 级 auth 覆盖 */ }
    },

    // --- 元数据提取 ---
    "metadata": {
      "select_title": ["h1", "meta[property='og:title']"],
      "select_description": ["meta[name='description']"],
      "select_image": ["meta[property='og:image']"],
      "script": "./scripts/extract.js",
      "timeout": 30,
      "proxy": "...",
      "http": { "Cookie": "..." },
      "request_url": ["pattern", "replacement"],
      "rewrite_url": ["pattern", "replacement"],
      "auth": { /* section 级 auth 覆盖 */ }
    },

    // --- 快照生成 ---
    "snapshot": {
      "keep_elements": [".article-content"],
      "remove_elements": [".ads", ".nav"],
      "process_lazy_images": ["data-src", "data-original"],
      "remove_classes": { ".content": ["hidden", "collapsed"] },
      "set_styles": { ".content": { "maxHeight": "none" } },
      "script": "./scripts/snapshot.js",
      "singlefile_args": {
        "--remove-hidden-elements": true,
        "--browser-wait-delay": 2000
      },
      "toggles": {
        "comments": {
          "selector": ".comments-section",
          "default": true,
          "label": "评论区"
        }
      },
      "timeout": 60,
      "proxy": "...",
      "http": { "User-Agent": "..." },
      "request_url": ["pattern", "replacement"],
      "rewrite_url": ["pattern", "replacement"],
      "auth": { /* section 级 auth 覆盖 */ }
    },

    // --- 阅读模式 ---
    "reader": {
      "defuddle_args": {
        "contentSelector": [".article-body"],
        "removeExactSelectors": [".ad"]
      },
      "timeout": 30,
      "proxy": "...",
      "http": { "User-Agent": "..." },
      "auth": { /* section 级 auth 覆盖 */ }
    }
  }
}
```

### 4.1 通用字段（default / metadata / snapshot / reader 共用）

| 字段 | 类型 | 说明 |
|------|------|------|
| `timeout` | int | 请求超时（秒） |
| `proxy` | string | HTTP 代理地址 |
| `http` | `{header: value}` | 自定义 HTTP 请求头，key 是 Header 名称（如 `User-Agent`、`Cookie`），value 是字符串 |
| `request_url` | `[pattern, replacement]` 或 `[[p1, r1], [p2, r2]]` | 正则替换——用 replacement URL 发起实际请求 |
| `rewrite_url` | 同上 | 正则替换——在存储/展示时改写 URL |
| `auth` | auth 配置对象 | section 级别的认证覆盖，与顶级 `auth` 合并 |

> **注意**：`auth.cookie` 和 `http.Cookie` 互斥——当 `auth.cookie` 存在时，`http` 中的 `Cookie` 头会被忽略。

### 4.2 metadata 专属字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `select_title` | `[CSS Selector]` | 标题选择器列表（按顺序尝试，取第一个匹配结果） |
| `select_description` | `[CSS Selector]` | 描述选择器列表 |
| `select_image` | `[CSS Selector]` | 预览图选择器列表 |
| `script` | string | 自定义提取脚本路径（相对或绝对，`.js` 或 `.py`） |

当存在 `script` 时，内置的 CSS selector 提取逻辑被跳过，完全由脚本接管。

### 4.3 snapshot 专属字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `keep_elements` | `[CSS Selector]` | 保留的元素列表（其余元素被移除） |
| `remove_elements` | `[CSS Selector]` | 需要移除的元素列表 |
| `process_lazy_images` | `[属性名]` | 懒加载图片的实际 src 属性名（如 `data-src`、`data-original`） |
| `remove_classes` | `{selector: [class]}` | 从匹配元素上移除指定 CSS class |
| `set_styles` | `{selector: {prop: value}}` | 给匹配元素设置内联样式 |
| `singlefile_args` | `{arg: value}` | 传递给 SingleFile 引擎的 CLI 参数 |
| `script` | string | 自定义快照脚本路径（接管整个快照流程） |
| `toggles` | `{id: {selector, default, label}}` | 用户可切换的元素组 |

> **互斥关系**：`script` 与 `keep_elements` / `remove_elements` / `remove_classes` / `set_styles` / `singlefile_args` 互斥——当存在 `script` 时，声明式元素控制被跳过，完全由脚本接管。

### 4.4 reader 专属字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `defuddle_args` | `{param: value}` | 传递给 defuddle 内容提取引擎的参数 |

> reader 不支持自定义 `script` 字段，所有阅读模式配置通过 `defuddle_args` 完成。

### 4.5 别名（alias）

多个域名共享同一配置时使用别名：

```jsonc
{
  "www.example.com": { "type": "alias", "target": "example.com" },
  "*.example.com": { "type": "alias", "target": "example.com" },
  "example.com": {
    "metadata": { "select_title": ["h1"] }
  }
}
```

- 别名链最大深度 10 层，超出会记录警告并丢弃配置
- 别名环会被检测并中断

---

## 5. 配置合并规则

### 5.1 合并流程

配置合并分为两个阶段：

**阶段一：跨适配器合并**

`_adapters` 数组顺序即优先级（靠前覆盖靠后），从后往前合并。defaults 适配器（id="defaults"）强制排在第一位，所以它始终是最高优先级：

```
_adapters: [defaults, A, B]    （defaults 强制第一，A 第二，B 第三）
合并方向：B → A → defaults
结果：defaults 的同名字段覆盖 A 和 B
```

在每个适配器内部，`defaults` 作为该适配器所有域名的默认模板——适配器内每个域名配置会合并 `defaults` 作为基准，再叠加自身的字段。

在同一适配器内，`domains` 下的域名配置以 `defaults` 为基准，域名配置覆盖 `defaults`：

```jsonc
{
  "defaults": { "timeout": 30 },
  "domains": {
    "example.com": { "timeout": 60 }   // 覆盖 defaults 中的 30
  }
}
```

**阶段二：defaults 适配器 `global_defaults` 全局覆盖**

阶段一完成后，系统额外将 **defaults 适配器（id="defaults"）的 `global_defaults`** 作为全局覆盖层，应用到**所有**域名上——无论该域名定义在哪个适配器中。

> `global_defaults` 是 `defaults` 适配器的专属字段。其他适配器即使写了也会被忽略，只有 defaults 适配器的 `global_defaults` 才能覆盖全部适配器。

```
假设 _adapters = [defaults, B]：

defaults: { "defaults": {}, "global_defaults": { "timeout": 30 }, "domains": { "a.com": { "metadata": {...} } } }
B:        { "defaults": { "timeout": 60 }, "domains": { "a.com": {...}, "b.com": {...} } }

对于 b.com：
  阶段一 → b.com 从 B 获得配置（B.defaults.timeout=60 作为基准；defaults 适配器无 b.com 域名，不参与覆盖）
  阶段二 → global_defaults.timeout=30 再覆盖 b.com，最终 timeout=30
```

只有 defaults 适配器的 `global_defaults` 有全局覆盖能力。B 的 `defaults`（timeout=60）只在阶段一中作为 B 内部域名配置的基准，不会渗透到其他适配器。

### 5.2 使用建议

defaults 适配器的 `global_defaults` 是全局默认值的唯一入口，建议仅配置通用字段。`defaults` 只影响 defaults 适配器自身的域名：

```jsonc
{
  "defaults": {},
  "global_defaults": {
    "default": {
      "http": {
        "User-Agent": "Mozilla/5.0 (compatible; MyBot/1.0)"
      },
      "timeout": 60
    }
  },
  "domains": {}
}
```

> **警告**：不建议在 `global_defaults` 中配置 `select_title`、`keep_elements`、`remove_elements` 等网站特定字段，因为它们会覆盖所有第三方适配器的专业配置，导致意料之外的结果。`defaults` 仅影响 defaults 适配器自身的域名，不会渗透到其他适配器。

### 5.3 deep_merge 语义

合并使用 `deep_merge` 算法：

- 嵌套对象递归合并
- **`null` 值表示删除**：在覆盖层中将某个字段设为 `null`，会在合并结果中移除该字段
- 数组直接替换（不合并）

```jsonc
// base
{ "default": { "http": { "User-Agent": "ua1", "Accept": "text/html" } } }

// override
{ "default": { "http": { "Accept": null, "Cookie": "c1" } } }

// result
{ "default": { "http": { "User-Agent": "ua1", "Cookie": "c1" } } }
```

### 5.4 auth 合并规则

认证配置采用三层合并（后层覆盖前层）：

```
顶级 auth  →  default.auth  →  section.auth
```

- **cookie**：深合并（`merge_cookie`）
- **headers**：浅合并（后覆盖前）
- **token**：直接替换

### 5.5 通配符域名匹配

域名匹配支持两种方式：

1. **精确匹配**：`example.com` 仅匹配 `example.com`
2. **通配符匹配**：`*.example.com` 匹配 `a.example.com`、`b.c.example.com` 等

通配符按域名层级数降序排列后逐一尝试，层级多的优先匹配。

---

## 6. 元数据提取（metadata）

### 6.1 内置引擎：CSS Selector

不指定 `script` 时，使用内置的 CSS 选择器提取：

```jsonc
{
  "metadata": {
    "select_title": [
      "h1.article-title",                 // 优先尝试
      "meta[property='og:title']",        // 其次
      "title"                              // 兜底
    ],
    "select_description": [
      "meta[name='description']",
      "meta[property='og:description']"
    ],
    "select_image": [
      "meta[property='og:image']"
    ]
  }
}
```

选择器按数组顺序依次尝试，取第一个匹配到非空内容的结果。对于 `select_title` 和 `select_description`，如果选择器匹配的是 `<meta>` 标签，则取其 `content` 属性；否则取其 `textContent`。

### 6.2 自定义脚本引擎

```jsonc
{
  "metadata": {
    "script": "./scripts/my_extract.py"
  }
}
```

存在 `script` 字段时，完全跳过内置的 CSS selector 逻辑。

**Python 脚本接口**：

```python
def extract(url, config, html_content=None):
    """
    url: str           — 页面 URL
    config: dict       — 合并后的配置（已去除 _ 前缀的内部字段）
    html_content: str  — 已获取的页面 HTML

    返回：{"title": str|None, "description": str|None, "image": str|None}
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_content, "html.parser")
    return {
        "title": soup.select_one("h1").text if soup.select_one("h1") else None,
        "description": None,
        "image": None,
    }
```

**JavaScript 脚本接口**：

```javascript
/**
 * stdin JSON: { url, config, html_content }
 * stdout JSON: { title, description, image }
 */
const input = JSON.parse(require('fs').readFileSync('/dev/stdin', 'utf8'));
const { url, config, html_content } = input;

// your logic here...

console.log(JSON.stringify({ title: "...", description: null, image: null }));
```

脚本模板文件位于 `data/site_adapters/etc/templates/metadata_py.py` 和 `metadata_js.js`。

---

## 7. 快照生成（snapshot）

### 7.1 内置引擎：SingleFile + DOM 清理

不指定 `script` 时，系统使用 SingleFile 引擎生成快照，并在生成前后应用 DOM 清理规则：

```jsonc
{
  "snapshot": {
    "keep_elements": [".article-body", ".main-content"],
    "remove_elements": [".ad", ".nav", ".sidebar", ".footer"],
    "process_lazy_images": ["data-src", "data-original", "data-lazy-src"],
    "remove_classes": {
      ".content": ["hidden", "collapsed"],
      ".modal": ["visible"]
    },
    "set_styles": {
      ".content": { "maxHeight": "none", "overflow": "visible" }
    },
    "singlefile_args": {
      "--remove-hidden-elements": true,
      "--browser-wait-delay": 2000,
      "--load-deferred-images": true
    }
  }
}
```

**处理顺序**：
1. SingleFile 生成快照 HTML
2. 应用 `remove_classes` — 移除指定 CSS class
3. 应用 `set_styles` — 设置内联样式
4. 应用 `keep_elements` / `remove_elements` — 保留/移除元素
5. 应用 `process_lazy_images` — 将懒加载属性的值设置到 `src`

### 7.2 自定义快照脚本

```jsonc
{
  "snapshot": {
    "script": "./scripts/snapshot.js"
  }
}
```

**Python 接口**：

```python
def extract(url, config, output_path=None):
    """
    url: str            — 页面 URL
    config: dict        — 合并后的配置
    output_path: str    — 快照 HTML 输出路径

    不需要返回值（结果写入 output_path）
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url)
        html = page.content()
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)
        browser.close()
```

**JavaScript 接口**：

```javascript
/**
 * stdin JSON: { url, config, output_path }
 * config.storage: { key: value } — 预注入到 localStorage 的 token
 */
const { url, config, output_path } = JSON.parse(require('fs').readFileSync(0, 'utf8'));

(async () => {
  const browser = await launchBrowser(config); // 使用平台内置的 browser_provider
  const page = await browser.newPage();
  await page.goto(url, { waitUntil: 'networkidle' });
  const html = await page.content();
  require('fs').writeFileSync(output_path, html);
  await browser.close();
})();
```

脚本模板位于 `data/site_adapters/etc/templates/snapshot_py.py` 和 `snapshot_js.js`。

### 7.3 互斥关系

`script` 与声明式字段（`keep_elements`、`remove_elements`、`remove_classes`、`set_styles`、`singlefile_args`）互斥。当存在 `script` 时，这些声明式配置不生效。

### 7.4 Toggles（用户开关）

`toggles` 允许用户选择性地保留或移除某些元素：

```jsonc
{
  "snapshot": {
    "toggles": {
      "comments": {
        "selector": ".comments-section",
        "default": true,            // 默认保留？true=默认移除
        "label": "评论区"
      },
      "sidebar": {
        "selector": ".sidebar",
        "default": false,
        "label": "侧边栏"
      }
    }
  }
}
```

- `default: true` → 默认**移除**该元素
- `default: false` → 默认**保留**该元素
- 用户可在设置中覆盖每一项，覆盖值会持久化

toggle 最终会影响 `remove_elements` 和 `keep_elements`：
- 若最终判定为移除：selector 加入 `remove_elements`，从 `keep_elements` 移除
- 若最终判定为保留：selector 加入 `keep_elements`，从 `remove_elements` 移除

---

## 8. 阅读模式（reader）

阅读模式使用 defuddle 引擎从页面中提取正文内容，所有配置通过 `defuddle_args` 完成。reader 区块不支持自定义脚本。

### 8.1 defuddle_args 配置

```jsonc
{
  "reader": {
    "defuddle_args": {
      "contentSelector": [".article-body", "article"],
      "removeExactSelectors": [".ad", ".social-share"],
      "removePartialSelectors": ["promo"],
      "removeHiddenElements": true,
      "removeLowScoring": true,
      "standardize": true
    }
  }
}
```

可用参数详见 [附录 C](#c-defuddle_args-参数列表)。

---

## 9. 认证体系（auth）

针对需要登录或特殊认证的网站，site-adapters 提供了三种认证方式。

### 9.1 配置位置

auth 配置可以放在三个层级：

```jsonc
{
  "example.com": {
    "auth": { /* 顶级 — 对 metadata、snapshot、reader 均生效 */ },
    "default": {
      "auth": { /* default 级 — 被各 section 继承 */ }
    },
    "metadata": {
      "auth": { /* section 级 — 仅对该 section 生效，覆盖上级 */ }
    }
  }
}
```

合并顺序：**顶级 auth → default.auth → section.auth**（后覆盖前）。

### 9.2 Cookie 认证

```jsonc
{
  "auth": {
    "cookie": {
      "type": "login",              // "anon" | "login"

      // 有效性验证
      "verify": {
        "check": ["已登录"],        // 页面文本中必须出现的关键词
        "invalid_patterns": ["登录", "安全验证"],  // 出现即失效
        "valid_selector": ".user-avatar"  // 必须存在的 DOM 元素
      },

      // 自动续期
      "refresh": {
        "url": "https://example.com/refresh",
        "wait_cookie": "token",     // 等待此 cookie 出现
        "timeout": 30
      },
      "refresh_interval": 86400     // 刷新间隔（秒）
    }
  }
}
```

**Cookie 类型**：

| 类型 | 说明 |
|------|------|
| `anon` | 匿名 Cookie（系统可自动获取） |
| `login` | 登录 Cookie（需要用户手动提供） |

**验证流程**：
1. 获取页面内容
2. 检查 `valid_selector` 是否存在（若配置）
3. 检查 `invalid_patterns` 是否出现（任一匹配即失效）
4. 检查 `check` 关键词是否出现（全部匹配才算有效）
5. 失效时自动触发 `refresh` 刷新流程

**用户 Cookie**：`type: "login"` 时，用户可通过 UI 提供自己的登录 Cookie，系统将其保存并优先使用。

### 9.3 HTTP Headers 认证

```jsonc
{
  "auth": {
    "headers": {
      "X-API-Key": {},           // 固定值（用户在 UI 填写）
      "Authorization": {},       // 同上
      "X-Custom-Header": {}      // 同上
    }
  }
}
```

- 每个 header 的 value 由用户通过 UI 提供
- 这些 header 会被注入到对应 section 的所有 HTTP 请求中
- 用户级别的 header 值优先级最高：用户值 > 配置静态值

### 9.4 Token 认证（OAuth2）

```jsonc
{
  "auth": {
    "token": {
      "type": "login",                  // "anon" | "login"
      "endpoint": "https://api.example.com/oauth/token",
      "client_id": "my-client-id",
      "client_secret": "my-secret",
      "grant_type": "client_credentials",
      "format": "json",                 // 请求体格式
      "access_path": "access_token",    // 响应中 access token 的 JSON 路径
      "refresh_path": "refresh_token",  // 响应中 refresh token 的 JSON 路径
      "expires_path": "expires_in",     // 响应中过期时间的 JSON 路径
      "header": "Authorization",        // 注入到哪个 HTTP Header
      "header_format": "Bearer {token}",// Header 格式，{token} 替换为实际 token
      "extra_params": {                 // 额外的请求参数
        "scope": "read"
      },
      "verify": {                       // Token 有效性验证（同 cookie.verify）
        "check": ["\"status\":\"ok\""],
        "invalid_patterns": ["unauthorized"]
      }
    }
  }
}
```

- Token 由系统自动获取和刷新
- 有效期内复用缓存，过期后自动用 refresh_token 换取新 token
- 获取成功后自动注入到配置的 HTTP Header 中

---

## 10. 自定义脚本开发

### 10.1 脚本语言选择

| 语言 | 执行方式 | 超时机制 | 适用场景 |
|------|----------|----------|----------|
| Python (`.py`) | 线程内 import + `extract()` | 软超时（daemon 线程继续运行） | 简单提取、无外部依赖 |
| JavaScript (`.js`) | 子进程 `node script.js` | 硬超时（进程被 SIGTERM） | 浏览器自动化、需要外部库 |

### 10.2 Python 脚本规范

**统一入口函数**：

```python
def extract(url, config, html_content=None, output_path=None):
    """
    metadata 脚本：html_content 有值，output_path 为 None，返回 dict
    snapshot 脚本：output_path 有值，html_content 可为 None，返回 None

    config 中的内部字段（以 _ 开头）已被过滤，只包含用户配置字段。
    """
    pass
```

**兼容旧接口**（按优先级查找）：
1. `extract(url, config, ...)` — 推荐
2. `_load_website_metadata(url, config)` — 元数据回退
3. `_parse_html(html_content, url, config)` — HTML 解析回退
4. `_parse_url(url, config)` — URL 解析回退
5. `_create_snapshot(url, output_path, config)` — 快照回退

**安全约束**：
- 脚本必须在 `data/site_adapters/` 目录树内（`is_allowed_script_path` 校验）
- 超时默认 30 秒
- 超时后线程标记为 daemon（不会强制杀死，进程退出时自动清理）

### 10.3 JavaScript 脚本规范

**stdin 输入格式**：

```json
{
  "url": "https://example.com/article/123",
  "config": { "keep_elements": [".main"], "storage": {} },
  "html_content": "<html>...",    // metadata 有，snapshot 无
  "html_path": "/tmp/xxx.html",   // html_content 的临时文件路径
  "output_path": "/tmp/snapshot.html"
}
```

**stdout 输出格式**：

```json
{"title": "...", "description": null, "image": null}
```

**浏览器 API**：

快照脚本中，可通过 `config` 获取以下浏览器相关配置：

| config 字段 | 说明 |
|-------------|------|
| `chromium_path` | Chromium 可执行文件路径 |
| `license_key` | CloakBrowser license key（若使用） |
| `storage` | 预注入到 `localStorage` 的键值对 |
| `timeout` | 请求超时（毫秒） |

推荐内置浏览器引擎的导入方式：

```javascript
// 尝试 CloakBrowser，回退到 Playwright+Chromium
let browser;
try {
  const cb = await import("cloakbrowser");
  browser = await cb.launch({ headless: true });
} catch {
  const pw = require("playwright-core");
  browser = await pw.chromium.launch({
    headless: true,
    executablePath: config.chromium_path || "/usr/bin/chromium",
    args: ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
  });
}
```

### 10.4 脚本路径配置

在适配器配置中引用脚本：

```jsonc
{
  "metadata": { "script": "./scripts/extract.js" },      // 相对路径
  "snapshot": { "script": "scripts/jike/snapshot.js" },   // 相对（自动补 scripts/）
  "metadata": { "script": "/abs/path/to/script.py" }      // 绝对路径（需在 site_adapters 目录内）
}
```

- 相对路径以适配器文件所在目录为基准
- 脚本路径会被安全校验，不允许跳出 `data/site_adapters/` 目录树
- 越界的绝对路径会被警告并跳过

### 10.5 脚本超时

| 脚本类型 | 超时默认值 | 超时行为 |
|----------|-----------|----------|
| Python | 30 秒 | 线程 join(timeout)后标记 daemon，继续后台运行 |
| JS | 30 秒 | subprocess.run(timeout=30)，过期抛 TimeoutExpired |

JS 脚本的超时可精确控制，Python 脚本的"超时"仅意味着不再等待结果，脚本仍可能在后台运行。

---

## 11. URL 处理

### 11.1 request_url

`request_url` 用于在发起 HTTP 请求前重写 URL。典型场景：

- 移动端 URL → API URL：`https://web.okjike.com/u/xxx/post/123` → `https://m.okjike.com/originalPosts/123`
- 短链接 → 原始链接

```jsonc
{
  "metadata": {
    "request_url": [
      "https://web\\.example\\.com/page/(\\d+)",
      "https://api.example.com/v1/pages/\\1"
    ]
  }
}
```

支持多组规则：

```jsonc
{
  "request_url": [
    ["pattern1", "replacement1"],
    ["pattern2", "replacement2"]
  ]
}
```

### 11.2 rewrite_url

`rewrite_url` 在存储/展示时改写 URL（不影响实际请求）：

```jsonc
{
  "rewrite_url": [
    "https://m\\.example\\.com/(.+)",
    "https://www.example.com/\\1"
  ]
}
```

### 11.3 正则语法

使用 Python `re.sub` 标准正则语法，支持捕获组 `( )` 和反向引用 `\1` `\2` 等。

---

## 12. 测试与调试验证

### 12.1 优先使用管理界面

日常管理操作（添加、编辑、删除、启用/禁用、排序适配器）建议通过 **`/admin/site-adapters`** 页面完成。

该页面提供：
- 适配器列表管理（增删改查、拖拽排序）
- 订阅源更新（远程适配器一键刷新）
- 域名配置的查看、搜索、启用/禁用
- 域名规则的在线编辑

### 12.2 命令行工具

命令行适合开发调试场景：

```bash
# 验证整个适配器集的正确性
python manage.py site_adapter validate

# 查看某个 URL 的完整合并后配置
python manage.py site_adapter show-config https://example.com/article/123

# 测试元数据提取（查看提取结果 + 所用配置）
python manage.py site_adapter metadata https://example.com/article/123

# 端到端流水线测试（metadata + snapshot + reader）
python manage.py site_adapter pipeline https://example.com/article/123

# 生成快照并保存到文件
python manage.py site_adapter pipeline https://example.com/article/123 --output ./snapshot.html

# 仅测试 metadata + reader（跳过快照）
python manage.py site_adapter pipeline https://example.com/article/123 --skip-snapshot

# 验证单个适配源文件
python manage.py site_adapter validate-subscription ./my-adapter/adapters.jsonc

# 验证远程适配源
python manage.py site_adapter validate-subscription https://example.com/subscription.jsonc

# 测试 Cookie 状态
python manage.py site_adapter cookie https://example.com/article/123
python manage.py site_adapter cookie https://example.com/article/123 --section snapshot

# 从 Tampermonkey UserScript 生成配置骨架
python manage.py site_adapter from-userscript ./myscript.user.js
```

### 12.3 validate 命令

验证内容：
- `config.jsonc` 格式校验
- `_adapters` 条目合法性（name 安全、source HTTPS 要求、interval 正整数）
- 各适配器的 `adapters.jsonc` 格式校验
- 域名配置字段合法性（已知字段 vs 未知字段警告）
- 脚本路径存在性和安全性
- `singlefile_args` 参数名有效性
- `defuddle_args` 参数名有效性
- `auth.cookie` / `auth.token` 子字段完整性
- 互斥字段检查

### 12.4 show-config 命令

输出格式：

```json
{
  "url": "https://example.com/article/123",
  "domain": "example.com",
  "domain_key": "example.com",
  "defaults": { /* defaults 全局覆盖配置 */ },
  "raw_config": { /* 域名原始配置 */ },
  "merged": { /* 合并后的最终配置 */ }
}
```

这个命令对于理解"为什么我的配置没生效"非常有用——可以直接看到合并后的完整配置。

### 12.5 执行日志

所有脚本执行、Cookie 验证/刷新都会被记录到日志文件：

```
data/site_adapters/logs/execution-YYYY-MM-DD.jsonl
```

每条日志格式：

```json
{
  "timestamp": "2026-08-03T10:30:00+00:00",
  "url": "https://example.com/article/123",
  "domain_key": "example.com",
  "step": "snapshot",
  "returncode": 0,
  "duration_ms": 1234,
  "cmd": ["node", "/path/to/script.js"],
  "stdout": "...",
  "stderr": ""
}
```

- 按天轮转，默认保留 30 天（通过 `LD_SITE_ADAPTERS_LOG_RETENTION_DAYS` 配置）
- 敏感字段（`authorization`、`cookie`、`set-cookie`）会被自动脱敏

### 12.6 程序内收集日志

```python
from site_adapters.services.execution_log import collect_executions

with collect_executions() as entries:
    # 此范围内的 log_execution() 调用会被收集到 entries 列表
    do_something()

# entries 现在包含该范围内的所有日志条目
```

---

## 13. 订阅源分发

### 13.1 订阅源文件格式

远程订阅源是一个 HTTPS 托管的 JSONC 文件，结构如下：

```jsonc
{
  "_meta": {
    "id": "my-publisher-id",
    "name": "我的订阅源",
    "version": 1,
    "description": "为中文网站优化的适配规则"
  },
  "defaults": {
    "timeout": 30
  },
  "domains": {
    "example.com": {
      "metadata": { "select_title": ["h1"] },
      "snapshot": { "remove_elements": [".ad"] }
    }
  }
}
```

### 13.2 用户订阅

用户通过 linkding 的 **`/admin/site-adapters`** 管理界面，添加 HTTPS URL 即可订阅：

1. 系统自动下载适配源文件
2. 缓存到 `adapters/{id}.{name}/adapters.jsonc`
3. 同步 `scripts/` 目录中的脚本文件
4. 根据 `update_interval` 定期检查更新

### 13.3 订阅源 _meta 字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | 是 | 发布者唯一标识（目录名的一部分） |
| `name` | string | 是 | 订阅源名称（UI 显示 + 目录名的一部分） |
| `version` | int | 推荐 | 版本号（配合 `checkUpdateUrl` 做增量更新） |
| `description` | string | 否 | 订阅源描述 |

### 13.4 条件更新（减少带宽）

系统支持三种机制避免重复下载：

**1. ETag / Last-Modified**（HTTP 标准）

服务器返回 `ETag` 和 `Last-Modified` 头，下次请求时带上 `If-None-Match` 和 `If-Modified-Since`。若内容未变，服务器返回 304，跳过下载。

**2. checkUpdateUrl**（版本检查）

```jsonc
{
  "_meta": { "version": 1 }
}
```

发布方提供一个 `version.json`：

```jsonc
{
  "version": 2,
  "updateUrl": "https://example.com/subscription-v2.jsonc"
}
```

用户侧在 `config.jsonc` 中为该订阅源配置 `check_update_url` 指向该文件。若远程版本号 ≤ 本地版本号，跳过下载；若 `updateUrl` 存在且版本更新，改用新 URL 下载。

**3. update_interval**（时间间隔）

默认 86400 秒（24小时），在此间隔内不重复下载。可在管理界面点击"立即更新"强制刷新。

### 13.5 _includes 模块化

大型订阅源可以拆分到多个文件：

```jsonc
{
  "_includes": [
    "https://cdn.example.com/common-rules.jsonc",
    "https://cdn.example.com/chinese-sites.jsonc"
  ],
  "domains": {
    "mysite.com": { /* ... */ }
  }
}
```

- `_includes` 中的文件递归合并（先加载的优先级低）
- 主文件中的 `domains` 覆盖 `_includes` 中的同名域名
- 最大递归深度 10 层
- 循环引用会被检测并中断
- 所有 include URL 必须是 HTTPS

### 13.6 脚本分发

订阅源中的脚本通过配置中的相对路径引用：

```jsonc
{
  "domains": {
    "example.com": {
      "snapshot": { "script": "./scripts/snapshot.js" }
    }
  }
}
```

系统在下载订阅源时会：

1. 扫描所有 `script` 和 `*_script` 字段
2. 解析脚本 URL（相对于订阅源 URL）
3. 下载并保存到 `scripts/` 目录
4. 清理订阅源中不再引用的旧脚本

**安全措施**：
- 脚本 URL 必须是 HTTPS
- 脚本文件名必须通过安全校验（无路径遍历）
- 下载后的脚本只能在 `data/site_adapters/` 目录树内执行

### 13.7 发布检查清单

分发订阅源前，建议：

```bash
# 1. 验证格式
python manage.py site_adapter validate-subscription ./your-adapter/

# 2. 测试几个典型 URL
python manage.py site_adapter show-config https://target-site.com/page
python manage.py site_adapter metadata https://target-site.com/page
python manage.py site_adapter pipeline https://target-site.com/page

# 3. 确保文件可通过 HTTPS 访问，Content-Type 建议为 application/json
# 4. 版本号建议使用递增整数
```

---

## 14. 从 UserScript 迁移

如果你已有 Tampermonkey / Greasemonkey UserScript，可以用以下命令快速生成适配器配置骨架：

```bash
python manage.py site_adapter from-userscript ./myscript.user.js
```

### 14.1 自动推断

工具会从 UserScript 的元数据块中解析：

| UserScript 指令 | 推断结果 |
|-----------------|---------|
| `@match` | 提取域名作为 `domain_key` |
| `@name` | 作为适配器的 name |
| `@grant GM_xmlhttpRequest` | 推断需要 `auth`（`cookie: { type: "login" }`） |
| `querySelector('...')` 等 DOM API | 列出检测到的选择器作为参考 |

### 14.2 输出示例

```
Generated config for zhihu.com (from 知乎增强):
{
  "zhihu.com": {
    "auth": {
      "cookie": { "type": "login" }
    },
    "metadata": {},
    "snapshot": {},
    "reader": {}
  }
}

Detected selectors in script (may help configure metadata/snapshot):
  - .RichContent-inner
  - .QuestionHeader-title
  - .ContentItem-expandButton
```

生成配置后，继续手动补全具体的选择器和脚本引用。

---

## 15. 完整示例：从零创建适配器

假设我们要为 `dev.to` 创建一个本地适配器。

### 步骤 1：创建适配器目录和文件

创建本地适配器目录（id="local"、name="my-dev-to"）：

```bash
mkdir -p data/site_adapters/adapters/local.my-dev-to
```

创建 `data/site_adapters/adapters/local.my-dev-to/adapters.jsonc`：

```jsonc
{
  "_meta": {
    "id": "local",
    "name": "my-dev-to",
    "version": 1
  },
  "domains": {
    "dev.to": {
      "metadata": {
        "select_title": ["h1.crayons-article__header__meta"],
        "select_description": ["meta[property='og:description']"],
        "select_image": ["meta[property='og:image']"]
      },
      "snapshot": {
        "remove_elements": [
          "#top-bar",
          ".crayons-article__aside",
          ".crayons-article-actions",
          ".spec-feedback"
        ],
        "keep_elements": [
          ".crayons-article__main"
        ],
        "process_lazy_images": ["data-src"],
        "singlefile_args": {
          "--browser-wait-delay": 1000
        }
      },
      "reader": {
        "defuddle_args": {
          "contentSelector": [".crayons-article__body"]
        }
      }
    },
    "www.dev.to": {
      "type": "alias",
      "target": "dev.to"
    }
  }
}
```

### 步骤 2：注册适配器

在 **`/admin/site-adapters`** 管理界面添加该适配器：

1. 点击"添加适配器"
2. 填入 `id: "local"`、`name: "my-dev-to"`
3. `source` 填入本地路径：`./local.my-dev-to/adapters.jsonc`
4. 保存并确保启用

也可直接编辑 `data/site_adapters/adapters/config.jsonc`：

```jsonc
{
  "_adapters": [
    { "id": "local", "name": "my-dev-to", "source": "./local.my-dev-to/adapters.jsonc", "enabled": true }
  ]
}
```

### 步骤 3：验证

在 **`/admin/site-adapters`** 页面确认适配器已加载，然后使用命令行测试：

```bash
# 查看合并后配置
python manage.py site_adapter show-config https://dev.to/some-article

# 测试元数据提取
python manage.py site_adapter metadata https://dev.to/some-article

# 端到端测试
python manage.py site_adapter pipeline https://dev.to/some-article
```

### 步骤 4：迭代

如果 CSS Selector 不够精确，可以添加自定义脚本：

```bash
cp data/site_adapters/etc/templates/metadata_py.py data/site_adapters/adapters/local.my-dev-to/scripts/extract.py
```

编辑脚本逻辑，然后在配置中引用：

```jsonc
{
  "metadata": {
    "script": "./scripts/extract.py"
  }
}
```

再次运行测试命令验证效果。

---

## 16. 附录

### A. 合法字段速查表

以下列出各 section 的合法字段。使用不在列表中的字段会导致"unknown field"警告并在运行时被忽略。

#### default 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `timeout` | int | 请求超时（秒） |
| `proxy` | string | HTTP 代理 |
| `http` | `{header: value}` | HTTP 请求头 |
| `request_url` | 正则规则 | 请求 URL 重写 |
| `rewrite_url` | 正则规则 | 显示 URL 重写 |
| `auth` | auth 对象 | 认证配置（继承到 section） |

#### metadata 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `select_title` | `[CSS Selector]` | 标题选择器 |
| `select_description` | `[CSS Selector]` | 描述选择器 |
| `select_image` | `[CSS Selector]` | 预览图选择器 |
| `script` | string | 自定义提取脚本 |
| `timeout` | int | 请求超时 |
| `proxy` | string | HTTP 代理 |
| `http` | `{header: value}` | HTTP 请求头 |
| `request_url` | 正则规则 | 请求 URL 重写 |
| `rewrite_url` | 正则规则 | 显示 URL 重写 |
| `auth` | auth 对象 | section 级认证覆盖 |

#### snapshot 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `keep_elements` | `[CSS Selector]` | 保留的元素 |
| `remove_elements` | `[CSS Selector]` | 移除的元素 |
| `process_lazy_images` | `[属性名]` | 懒加载图片属性名 |
| `remove_classes` | `{selector: [class]}` | 移除 CSS class |
| `set_styles` | `{selector: {prop: value}}` | 设置内联样式 |
| `singlefile_args` | `{arg: value}` | SingleFile CLI 参数 |
| `toggles` | `{id: toggle对象}` | 用户可切换元素 |
| `script` | string | 自定义快照脚本 |
| `timeout` | int | 请求超时 |
| `proxy` | string | HTTP 代理 |
| `http` | `{header: value}` | HTTP 请求头 |
| `request_url` | 正则规则 | 请求 URL 重写 |
| `rewrite_url` | 正则规则 | 显示 URL 重写 |
| `auth` | auth 对象 | section 级认证覆盖 |

> 互斥组：`script` 与 `keep_elements` / `remove_elements` / `remove_classes` / `set_styles` / `singlefile_args` 互斥。

#### reader 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `defuddle_args` | `{param: value}` | defuddle 引擎参数 |
| `timeout` | int | 请求超时 |
| `proxy` | string | HTTP 代理 |
| `http` | `{header: value}` | HTTP 请求头 |
| `auth` | auth 对象 | section 级认证覆盖 |

> reader 不支持 `script` 字段。

#### auth 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `cookie` | cookie 对象 | Cookie 认证配置 |
| `headers` | `{name: {}}` | 用户自定义 Header |
| `token` | token 对象 | OAuth2 Token 认证配置 |

#### cookie 字段

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `type` | `"anon"` / `"login"` | 是 | Cookie 类型 |
| `verify` | verify 对象 | 否 | 有效性验证配置 |
| `refresh` | refresh 对象 | 否 | 自动续期配置 |
| `refresh_interval` | int | 否 | 刷新间隔（秒） |

**verify 对象**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `check` | `[string]` | 页面文本中必须出现的关键词（全部匹配） |
| `invalid_patterns` | `[string]` | 页面文本中出现任一个即认为失效 |
| `valid_selector` | string | 页面中必须存在的 CSS 选择器 |

**refresh 对象**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `url` | string | Cookie 刷新页面 URL |
| `wait_cookie` | string | 等待此名称的 cookie 被设置 |
| `timeout` | int | 等待超时（秒） |

#### token 字段

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `type` | `"anon"` / `"login"` | 是 | Token 类型 |
| `endpoint` | string | 是 | OAuth2 token endpoint URL |
| `client_id` | string | 否 | OAuth2 client_id |
| `client_secret` | string | 否 | OAuth2 client_secret |
| `grant_type` | string | 否 | grant_type（默认 `client_credentials`） |
| `format` | string | 否 | 请求格式（默认 `json`） |
| `access_path` | string | 否 | 响应 JSON 中 access_token 的路径（默认 `access_token`） |
| `refresh_path` | string | 否 | 响应 JSON 中 refresh_token 的路径 |
| `expires_path` | string | 否 | 响应 JSON 中过期时间的路径（默认 `expires_in`） |
| `header` | string | 否 | 注入的 HTTP Header 名（默认 `Authorization`） |
| `header_format` | string | 否 | Header 值模板，`{token}` 替换为实际值（默认 `Bearer {token}`） |
| `extra_params` | `{string: string}` | 否 | 额外的 OAuth2 请求参数 |
| `verify` | verify 对象 | 否 | Token 有效性验证（同 cookie.verify） |

### B. singlefile_args 常用参数

以下是项目中实际使用的 singlefile_args 参数：

| 参数 | 说明 |
|------|------|
| `--remove-hidden-elements` | 移除隐藏元素 |
| `--browser-wait-delay` | 浏览器等待延迟（ms） |
| `--load-deferred-images` | 加载延迟渲染的图片 |

> 完整的 SingleFile CLI 参数列表及其详细说明，请运行 `single-file --help` 查看。

### C. defuddle_args 参数列表

以下参数从 defuddle 引擎的源代码中提取，保存在 `site_adapters/services/engine/references/defuddle_params.txt`。

| 参数 | 说明 |
|------|------|
| `contentSelector` | 内容区域选择器 |
| `debug` | 调试模式 |
| `fetch` | 抓取配置 |
| `includeReplies` | 包含回复内容 |
| `language` | 内容语言 |
| `markdown` | 输出 Markdown 格式 |
| `profile` | 性能分析开关 |
| `removeContentPatterns` | 移除匹配正则的内容 |
| `removeExactSelectors` | 精确移除的选择器 |
| `removeHiddenElements` | 移除隐藏元素 |
| `removeImages` | 移除所有图片 |
| `removeLowScoring` | 移除低分内容块 |
| `removePartialSelectors` | 部分匹配移除的选择器 |
| `removeSmallImages` | 移除小尺寸图片 |
| `separateMarkdown` | 分离 Markdown 输出 |
| `standardize` | 标准化处理 |
| `url` | 文章原始 URL |
| `useAsync` | 异步处理模式 |

> 这些参数来源于 defuddle 项目源码（[fivefilters/defuddle](https://github.com/fivefilters/defuddle)），项目中 `site_adapters/services/engine/references/defuddle_params.txt` 保存了完整列表。

### D. HTTP Header 参考

HTTP Header 相关的权威来源：
- [MDN HTTP Headers](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers)
- [IANA Message Headers Registry](https://www.iana.org/assignments/message-headers/message-headers.xhtml)

项目中 `site_adapters/services/engine/references/http_headers.txt`（若存在）可作为编辑器自动补全的参考，但不保证完整性。

### E. 常见问题 FAQ

**Q: 我的配置为什么没生效？**

先用 `python manage.py site_adapter show-config <url>` 查看合并后的完整配置。通常原因是：

1. 域名没有精确匹配——注意 `www.example.com` 和 `example.com` 是不同的 key
2. 被更高优先级的适配器覆盖了——检查 `_adapters` 顺序
3. 被 defaults 适配器（id="defaults"）的 `global_defaults` 全局覆盖了——这是最高优先级的兜底，会覆盖所有域名的同名字段
4. 配置中有 typo——用 `python manage.py site_adapter validate` 检查
5. 适配器的 `enabled` 为 `false`

**Q: 如何让多个域名共享同一配置？**

使用别名：

```jsonc
{
  "www.example.com": { "type": "alias", "target": "example.com" },
  "*.example.com": { "type": "alias", "target": "example.com" },
  "example.com": { /* 实际配置 */ }
}
```

**Q: select_title 不生效，总是提取不到标题？**

常见原因：
1. 页面是 JS 渲染的，HTML 中标题还未生成——需要改用快照脚本
2. 选择器不够精确——用浏览器 DevTools 确认实际的 DOM 结构
3. 选择器匹配了但没有内容——检查是否正确处理了 `<meta>` 的 `content` 属性

**Q: 快照脚本太慢怎么办？**

1. 优先使用声明式配置（`keep_elements` / `remove_elements` / `singlefile_args`），避免脚本
2. 如果必须用脚本，减少 `waitUntil` 等待时间
3. 使用 `page.waitForSelector` 精确等待关键元素而非固定延时

**Q: 我的 Cookie 总是失效？**

检查 `verify` 配置：
- `invalid_patterns` 是否过于严格（误匹配正常页面内容）
- `check` 关键词是否在登录态下确实存在
- `valid_selector` 选择器是否正确
- 查看执行日志 `logs/execution-*.jsonl` 了解具体的验证结果

**Q: 远程订阅源更新不生效？**

1. 等待 `update_interval` 到期（默认 24 小时）
2. 在 `/admin/site-adapters` 管理界面点击"立即更新"
3. 检查 `_meta.version` 是否已递增
4. 如果配置了 `check_update_url`，确认 `version.json` 可访问且版本号更大

**Q: 如何贡献适配器规则？**

你可以通过以下方式贡献：
1. 将你的适配器规则托管到 HTTPS 服务器，在社区分享 URL
2. 将 UserScript 发布为独立的适配源

**Q: 我不小心删除了 defaults 适配器，怎么恢复？**

刷新 site-adapters 页面即可。系统检测到 defaults 适配器不存在时会自动创建。

**Q: Python 脚本超时后还在运行？**

是的，Python 脚本的"超时"是软超时——主线程不再等待结果，但脚本线程以 daemon 模式继续运行。这是 Python 线程模型的固有限制。如果脚本可能长时间运行，建议改用 JavaScript 脚本（硬超时，进程会被终止）。
