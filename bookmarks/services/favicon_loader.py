import logging
import mimetypes
import os
import os.path
import re
import struct
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import monotonic
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from django.conf import settings

logger = logging.getLogger(__name__)

# register mime type for .ico files, which is not included in the default
# mimetypes of the Docker image
mimetypes.add_type("image/x-icon", ".ico")



# ---------------------------------------------------------------------------
# Provider 健康检查（启动探测 + 定时刷新）
# ---------------------------------------------------------------------------

_HEALTH_CHECK_TIMEOUT = 3       # 单 provider 探测超时（秒）
_RECHECK_INTERVAL = 6 * 3600    # 不可用 provider 重新探测间隔（6 小时）


class _ProviderHealthChecker:
    """管理 favicon provider 的健康状态。

    - 启动时（首次调用时）并发探测所有 provider
    - 不可用的 provider 从尝试列表中排除
    - 定时重新探测不可用 provider，恢复后自动加入
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._unhealthy_set: set[str] = set()  # 不可达 provider（快速查找）
        self._last_probe: float = 0            # 上次探测时间
        self._probing = False                  # 是否正在探测中
        self._initialized = False              # 是否完成过首次探测

    def reset(self):
        """重置健康状态（用于测试）。"""
        with self._lock:
            self._unhealthy_set = set()
            self._last_probe = 0
            self._probing = False
            self._initialized = False

    def get_active_providers(self) -> list[str]:
        """返回当前可用的 provider 列表（排除已知不可达的）。

        首次调用时触发后台探测，同时返回全部 provider（避免阻塞）。
        探测完成后排除不可达 provider。
        始终以 settings.LD_FAVICON_PROVIDERS 为基准，支持运行时变更。
        """
        with self._lock:
            if not self._initialized:
                self._initialized = True
                self._start_probe()
                return list(settings.LD_FAVICON_PROVIDERS)

            if self._unhealthy_set and monotonic() - self._last_probe >= _RECHECK_INTERVAL:
                self._start_probe()

        # 基于当前 settings 过滤（不在锁内，settings 读取无竞争）
        current = list(settings.LD_FAVICON_PROVIDERS)
        if not self._unhealthy_set:
            return current
        return [p for p in current if p not in self._unhealthy_set]

    def _start_probe(self):
        """在后台线程中并发探测所有 provider。"""
        if self._probing:
            return
        self._probing = True
        thread = threading.Thread(target=self._probe_all, daemon=True)
        thread.start()

    def _probe_all(self):
        """并发探测所有 provider 的可达性。"""
        providers = list(settings.LD_FAVICON_PROVIDERS)
        if not providers:
            with self._lock:
                self._unhealthy_set = set()
                self._last_probe = monotonic()
                self._probing = False
            return

        results = {}
        probe_url_params = {"domain": "google.com", "url": "https://google.com"}

        def _probe_one(provider_template):
            url = provider_template.format(**probe_url_params)
            try:
                resp = requests.get(
                    url, timeout=_HEALTH_CHECK_TIMEOUT,
                    allow_redirects=True, stream=True,
                )
                resp.close()
                return resp.status_code < 500
            except Exception:
                return False

        with ThreadPoolExecutor(max_workers=len(providers)) as executor:
            futures = {executor.submit(_probe_one, p): p for p in providers}
            for future in as_completed(futures):
                provider = futures[future]
                try:
                    results[provider] = future.result()
                except Exception:
                    results[provider] = False

        unhealthy = {p for p, ok in results.items() if not ok}
        healthy = [p for p in providers if p not in unhealthy]

        with self._lock:
            self._unhealthy_set = unhealthy
            self._last_probe = monotonic()
            self._probing = False

        for p in healthy:
            logger.info("Favicon provider healthy: %s", p)
        for p in unhealthy:
            logger.info("Favicon provider unhealthy (excluded): %s", p)


# 全局单例
_provider_health = _ProviderHealthChecker()


def _ensure_favicon_folder():
    Path(settings.LD_FAVICON_FOLDER).mkdir(parents=True, exist_ok=True)


def domain_to_filename(domain: str) -> str:
    """将 hostname 转为安全的文件名基础部分（不含扩展名）。

    例: "example.com" -> "example_com"
    """
    return re.sub(r"\W+", "_", domain)


def get_favicon_path(favicon_file: str) -> Path:
    return Path(os.path.join(settings.LD_FAVICON_FOLDER, favicon_file))


def find_cached_favicon_file(domain: str) -> str | None:
    """在磁盘上查找指定域名的 favicon 文件，返回文件名或 None。

    兼容两种命名约定：
    - 新约定：domain_to_filename(domain)（如 example_com）
    - 旧约定：https_{name} / http_{name}（如 https_example_com）

    优先返回 SVG > PNG > JPG > ICO，确保确定性。
    """
    favicon_folder = Path(settings.LD_FAVICON_FOLDER)
    if not favicon_folder.exists():
        return None

    name = domain_to_filename(domain)
    # 兼容旧命名：带 scheme 前缀
    legacy_names = {f"https_{name}", f"http_{name}"}

    ext_priority = {".svg": 0, ".png": 1, ".jpg": 2, ".jpeg": 3, ".ico": 4, ".gif": 5}
    new_candidates = []  # 新命名（不带 scheme 前缀）
    legacy_candidates = []  # 旧命名（带 scheme 前缀）

    for filename in os.listdir(settings.LD_FAVICON_FOLDER):
        base, ext = os.path.splitext(filename)
        if base != name and base not in legacy_names:
            continue
        path = get_favicon_path(filename)
        if path.exists():
            # 校验文件内容是否为有效图片（防止残留损坏文件）
            try:
                with open(path, "rb") as f:
                    header = f.read(16)
                if not _is_valid_image(header):
                    logger.warning("Removing corrupted favicon file: %s", filename)
                    path.unlink()
                    continue
            except OSError:
                continue
            entry = (ext_priority.get(ext.lower(), 99), filename)
            if base == name:
                new_candidates.append(entry)
            else:
                legacy_candidates.append(entry)

    # 新命名优先；找到新命名文件时清理旧命名文件
    if new_candidates:
        for _, legacy_file in legacy_candidates:
            get_favicon_path(legacy_file).unlink(missing_ok=True)
        new_candidates.sort(key=lambda c: c[0])
        return new_candidates[0][1]

    if legacy_candidates:
        # 迁移：将最佳旧文件重命名为新命名
        legacy_candidates.sort(key=lambda c: c[0])
        best_legacy = legacy_candidates[0][1]
        _, ext = os.path.splitext(best_legacy)
        new_filename = f"{name}{ext}"
        new_path = get_favicon_path(new_filename)
        legacy_path = get_favicon_path(best_legacy)
        try:
            legacy_path.rename(new_path)
            logger.info("Migrated favicon: %s -> %s", best_legacy, new_filename)
            # 清理其余旧文件
            for _, lf in legacy_candidates:
                if lf != best_legacy:
                    get_favicon_path(lf).unlink(missing_ok=True)
            return new_filename
        except OSError:
            return best_legacy

    return None


def _remove_existing_variants(domain: str, keep_filename: str | None = None):
    """删除指定域名的所有旧扩展名变体（保留 keep_filename），包括旧 scheme 前缀命名。

    直接尝试已知文件名，避免 os.listdir 扫描整个目录。
    """
    name = domain_to_filename(domain)
    all_bases = [name, f"https_{name}", f"http_{name}"]
    all_exts = [".svg", ".png", ".jpg", ".jpeg", ".ico", ".gif"]
    for base in all_bases:
        for ext in all_exts:
            filename = f"{base}{ext}"
            if filename == keep_filename:
                continue
            path = get_favicon_path(filename)
            if path.exists():
                path.unlink()


def _is_data_uri(data: bytes) -> bool:
    """Favicon provider 返回 data URI 表示无真实图标。"""
    return data.startswith(b"data:")


def _is_svg_placeholder(data: bytes, content_type: str) -> bool:
    """检测 SVG 内容是否为占位符而非真实图标。

    占位符特征：极小体积、无绘图元素、或仅含简单几何图形。
    """
    if "svg" not in content_type.lower() and not data.lstrip().startswith(b"<"):
        return False
    # 真实 SVG 图标通常 > 200 bytes 且包含绘图元素
    if len(data) < 200:
        return True
    # 检查是否包含常见 SVG 绘图元素
    lower = data[:2048].lower()
    drawing_tags = [b"<path", b"<circle", b"<rect", b"<polygon", b"<ellipse", b"<line", b"<polyline", b"<text"]
    if not any(tag in lower for tag in drawing_tags):
        return True
    return False



# ---------------------------------------------------------------------------
# 自建 favicon 解析器（兜底方案）
# ---------------------------------------------------------------------------

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)

_HONEST_UA = (
    "Mozilla/5.0 (compatible; LinkdingFaviconBot/1.0; "
    "+https://github.com/sissbruecker/linkding)"
)

_MAX_HTML_BYTES = 32 * 1024  # 只读前 32KB

def _resolve_url(href: str, base_url: str) -> str:
    """将相对 URL 解析为绝对 URL。"""
    if not href:
        return ""
    if href.startswith("data:"):
        return href
    if href.startswith(("http://", "https://")):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        parsed = urlparse(base_url)
        return f"{parsed.scheme}://{parsed.netloc}{href}"
    if base_url.endswith("/"):
        return base_url + href
    return base_url.rsplit("/", 1)[0] + "/" + href


def _calculate_favicon_score(size, fmt: str, rel: str) -> int:
    """为 favicon 候选打分，分数越高越优先。

    优先选择 32×32 的图标（覆盖 16px Retina 显示）。
    SVG 矢量图标始终最优（缩放无损）。
    """
    # SVG 矢量图标始终最优
    if "svg" in fmt:
        return 200

    score = 100

    # 距离 32px 越近分越高
    if size:
        distance = abs(size - 32)
        if distance == 0:
            score += 80   # 32px 最佳
        elif distance <= 16:
            score += 60 + (1 if size > 32 else 0)  # 同距离时偏好更大尺寸（48px > 16px）
        elif distance <= 32:
            score += 45   # 64px
        elif distance <= 64:
            score += 25   # 96px
        else:
            score += 5    # >96px，最后兜底
    else:
        score += 20  # 无尺寸信息，中等优先级

    # 格式偏好
    if "png" in fmt:
        score += 15
    elif "webp" in fmt:
        score += 10

    # apple-touch-icon 通常是大图，降低优先级
    if "apple-touch-icon" in rel:
        score -= 30

    return score


def _parse_html_for_favicons(html: str, base_url: str) -> list:
    """从 HTML 中解析 favicon 链接，返回 [(url, score), ...] 按分数降序排列。"""
    candidates = []
    try:
        soup = BeautifulSoup(html[:_MAX_HTML_BYTES], "html.parser")
    except Exception:
        return []

    for link in soup.find_all("link", rel=True, href=True):
        rel = " ".join(link.get("rel", []))
        if "icon" not in rel:
            continue
        href = link["href"]
        if href.startswith("data:"):
            continue
        sizes = link.get("sizes", "")
        size = None
        if sizes:
            match = re.match(r"(\d+)x\d+", sizes)
            if match:
                size = int(match.group(1))
        fmt = link.get("type", "") or ""
        if not fmt and "." in href.split("/")[-1]:
            fmt = href.rsplit(".", 1)[-1].split("?")[0]
        url = _resolve_url(href, base_url)
        score = _calculate_favicon_score(size, fmt, rel)
        candidates.append((url, score))

    # og:image 作为最后的 HTML 候选
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        url = _resolve_url(og["content"], base_url)
        candidates.append((url, 30))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates


def _download_favicon_candidate(url: str, timeout: int, use_browser_ua: bool = False) -> tuple | None:
    """下载单个 favicon 候选，验证后返回 (content_type, body) 或 None。"""
    if url.startswith("data:"):
        return None
    ua = _BROWSER_UA if use_browser_ua else _HONEST_UA
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": ua}, allow_redirects=True)
        if not resp.ok:
            return None
        body = resp.content
        if not _is_valid_image(body):
            if body.lstrip().startswith(b"<"):
                return "image/svg+xml", body
            return None
        ct = resp.headers.get("Content-Type", "")
        if not ct or "text/html" in ct:
            ct = _guess_content_type(url, body)
        return ct, body
    except Exception:
        return None


def _guess_content_type(url: str, body: bytes) -> str:
    """从 URL 扩展名和文件头猜测 content_type。"""
    ext = ""
    if "." in url.split("/")[-1]:
        ext = url.rsplit(".", 1)[-1].split("?")[0].lower()
    if ext in ("png", "jpg", "jpeg", "gif", "webp", "svg", "ico"):
        ct = mimetypes.guess_type(f"x.{ext}")[0]
        if ct:
            return ct
    if body[:4] == b"\x89PNG":
        return "image/png"
    if body[:3] == b"GIF":
        return "image/gif"
    if body[:4] == b"RIFF":
        return "image/webp"
    if body[:2] == b"\x00\x00":
        return "image/x-icon"
    if body[:2] == b"\xff\xd8":
        return "image/jpeg"
    return "image/png"


def _fetch_favicon_from_site(domain: str, scheme: str = "https", timeout: int = 6) -> tuple | None:
    """直接从目标网站解析 favicon（自建兜底方案）。

    发现策略：
    1. 请求目标网页 HTML，解析 <link rel="icon"> 等标签
    2. 请求 /manifest.json 中的 icons
    3. fallback: /apple-touch-icon.png
    4. fallback: /favicon.ico

    使用双 UA 策略：先用诚实 UA，被拦截后切换浏览器 UA。
    """

    base_url = f"{scheme}://{domain}"
    html_timeout = min(timeout, 6)
    resource_timeout = min(timeout, 4)

    # 双 UA 策略获取 HTML
    html = None
    for ua in (_HONEST_UA, _BROWSER_UA):
        try:
            resp = requests.get(
                base_url, timeout=html_timeout,
                headers={"User-Agent": ua, "Accept": "text/html,application/xhtml+xml,*/*"},
                allow_redirects=True,
            )
            if resp.ok and "text/html" in resp.headers.get("Content-Type", ""):
                final = urlparse(resp.url)
                base_url = f"{final.scheme}://{final.netloc}"
                html = resp.text
                break
        except Exception:
            continue

    # 收集所有候选
    candidates = []

    if html:
        candidates.extend(_parse_html_for_favicons(html, base_url))

    # manifest.json
    try:
        manifest_resp = requests.get(
            f"{base_url}/manifest.json", timeout=resource_timeout,
            headers={"User-Agent": _HONEST_UA},
        )
        if manifest_resp.ok:
            manifest = manifest_resp.json()
            for icon in manifest.get("icons", []):
                if icon.get("src"):
                    url = _resolve_url(icon["src"], base_url)
                    sizes = icon.get("sizes", "")
                    size = None
                    if sizes:
                        match = re.match(r"(\d+)x\d+", sizes)
                        if match:
                            size = int(match.group(1))
                    fmt = icon.get("type", "")
                    score = _calculate_favicon_score(size, fmt, "icon")
                    candidates.append((url, score))
    except Exception:
        pass

    # 固定路径 fallback
    candidates.append((f"{base_url}/apple-touch-icon.png", 30))
    candidates.append((f"{base_url}/favicon.ico", 10))

    # 按分数降序去重
    seen = set()
    unique_candidates = []
    for url, score in candidates:
        if url not in seen:
            seen.add(url)
            unique_candidates.append((url, score))
    unique_candidates.sort(key=lambda x: x[1], reverse=True)

    # 依次尝试下载
    for url, _score in unique_candidates:
        result = _download_favicon_candidate(url, resource_timeout)
        if result:
            logger.debug("Self-resolved favicon for %s: %s", domain, url)
            return result

    return None



def _try_fetch_from_providers(domain: str, scheme: str = "https", timeout: int = 10) -> tuple[str, bytes] | None:
    """依次尝试所有配置的 provider，返回第一个成功的结果 (content_type, body)。

    全部失败时返回 None（不抛异常）。
    provider 使用指定 scheme；自建解析器自动尝试 https 和 http。
    """
    url_parameters = {
        "url": f"{scheme}://{domain}",
        "domain": domain,
    }

    for provider_url in _provider_health.get_active_providers():
        favicon_url = provider_url.format(**url_parameters)
        try:
            logger.debug("Trying favicon provider: %s", favicon_url)
            with requests.get(favicon_url, timeout=timeout) as response:
                response.raise_for_status()
                body = response.content
                if _is_data_uri(body):
                    logger.debug("Provider returned data URI, trying next: %s", favicon_url)
                    continue
                content_type = response.headers.get("Content-Type", "image/png")
                if _is_svg_placeholder(body, content_type):
                    logger.debug("Provider returned SVG placeholder, trying next: %s", favicon_url)
                    continue
                return content_type, body
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else 0
            logger.debug("Favicon provider returned %s, skipping: %s", status_code, favicon_url)
        except requests.exceptions.RequestException as e:
            logger.warning("Favicon provider failed: %s: %s", favicon_url, e)

    # 所有第三方 provider 均失败 → 尝试自建解析（兜底，自动尝试 https 和 http）
    for try_scheme in (scheme, "http" if scheme == "https" else scheme):
        try:
            result = _fetch_favicon_from_site(domain, scheme=try_scheme, timeout=timeout)
            if result:
                return result
        except Exception as e:
            logger.warning("Self-built favicon resolver failed for %s (%s): %s", domain, try_scheme, e)

    return None


def _is_valid_image(data: bytes) -> bool:
    """Check if data starts with known image file magic bytes."""
    if len(data) < 8:
        return False
    # PNG
    if data[:4] == b"\x89PNG":
        return True
    # JPEG
    if data[:3] == b"\xff\xd8\xff":
        return True
    # GIF
    if data[:4] == b"GIF8":
        return True
    # ICO
    if data[:4] == b"\x00\x00\x01\x00":
        return True
    # SVG: must contain <svg tag (not just any < character like HTML)
    if b"<svg" in data[:256] or (data[:5] == b"<?xml" and b"<svg" in data[:512]):
        return True
    # WebP
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return False



# ---------------------------------------------------------------------------
# ICO 单帧提取（纯 Python，无需 Pillow）
# ---------------------------------------------------------------------------

def _extract_best_ico_frame(data: bytes, target_size: int = 32) -> bytes | None:
    """从 ICO 文件中提取最接近 target_size 的 PNG 帧。

    ICO 格式：6 字节头 + N×16 字节目录 + 图像数据。
    现代 ICO 通常在 256×256 entry 中嵌入 PNG，较小尺寸为 BMP。
    本函数只提取 PNG 帧（直接可用），不处理 BMP 帧（需要 Pillow 转换）。

    返回提取的 PNG 字节，无可用 PNG 帧时返回 None。
    """
    if len(data) < 6 or data[:4] != b"\x00\x00\x01\x00":
        return None

    _, _, count = struct.unpack("<HHH", data[:6])
    if count == 0 or len(data) < 6 + count * 16:
        return None

    best_entry = None
    best_distance = float("inf")

    for i in range(count):
        off = 6 + i * 16
        entry = data[off : off + 16]
        w = entry[0] or 256  # 0 表示 256
        h = entry[1] or 256
        size = struct.unpack("<I", entry[8:12])[0]
        offset = struct.unpack("<I", entry[12:16])[0]

        # 检查是否为 PNG 帧
        if offset + 4 > len(data):
            continue
        if data[offset : offset + 4] != b"\x89PNG":
            continue

        distance = abs(w - target_size)
        if distance < best_distance:
            best_distance = distance
            best_entry = {"offset": offset, "size": size}

    if best_entry is None:
        return None

    offset = best_entry["offset"]
    size = best_entry["size"]
    frame = data[offset : offset + size]
    if len(frame) < 8:
        return None

    return frame


def fetch_and_save_favicon(domain: str, scheme: str = "https", timeout: int = 10) -> str:
    """为指定域名获取 favicon 并保存到磁盘，返回文件名。

    1. 依次尝试所有 provider
    2. 成功后保存文件，清理旧扩展名变体
    3. 全部失败返回空字符串
    """
    _ensure_favicon_folder()

    result = _try_fetch_from_providers(domain, scheme=scheme, timeout=timeout)
    if not result:
        return ""

    content_type, body = result

    # 校验下载内容是否为有效图片
    if not _is_valid_image(body):
        logger.warning("Favicon provider returned invalid image data for %s (content_type=%s, size=%s)", domain, content_type, len(body))
        return ""

    # ICO 文件：提取最接近 32×32 的 PNG 帧，避免存储多分辨率 bundle
    if content_type in ("image/x-icon", "image/vnd.microsoft.icon") or body[:4] == b"\x00\x00\x01\x00":
        extracted = _extract_best_ico_frame(body)
        if extracted:
            body = extracted
            content_type = "image/png"

    file_extension = mimetypes.guess_extension(content_type) or ".png"
    name = domain_to_filename(domain)
    favicon_file = f"{name}{file_extension}"
    favicon_path = get_favicon_path(favicon_file)

    # 原子写入：先写临时文件再 rename，防止并发写入导致文件损坏
    tmp_path = favicon_path.with_suffix(".tmp")
    with open(tmp_path, "wb") as f:
        f.write(body)
    os.rename(str(tmp_path), str(favicon_path))

    _remove_existing_variants(domain, keep_filename=favicon_file)
    logger.info("Saved favicon: %s -> %s", domain, favicon_file)
    return favicon_file
