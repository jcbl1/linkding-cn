import logging
import mimetypes
import os
import os.path
import re
from pathlib import Path

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# register mime type for .ico files, which is not included in the default
# mimetypes of the Docker image
mimetypes.add_type("image/x-icon", ".ico")


def _ensure_favicon_folder():
    Path(settings.LD_FAVICON_FOLDER).mkdir(parents=True, exist_ok=True)


def domain_to_filename(domain: str) -> str:
    """将 hostname 转为安全的文件名基础部分（不含扩展名）。

    例: "example.com" -> "example_com"
    """
    return re.sub(r"\W+", "_", domain)


def _get_favicon_path(favicon_file: str) -> Path:
    return Path(os.path.join(settings.LD_FAVICON_FOLDER, favicon_file))


def _find_cached_favicon_file(domain: str) -> str | None:
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
        path = _get_favicon_path(filename)
        if path.exists():
            # 校验文件内容是否为有效图片（防止残留损坏文件）
            try:
                with open(path, "rb") as f:
                    header = f.read(16)
                if not _is_valid_image(header):
                    logger.warning(f"Removing corrupted favicon file: {filename}")
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
            _get_favicon_path(legacy_file).unlink(missing_ok=True)
        new_candidates.sort(key=lambda c: c[0])
        return new_candidates[0][1]

    if legacy_candidates:
        # 迁移：将最佳旧文件重命名为新命名
        legacy_candidates.sort(key=lambda c: c[0])
        best_legacy = legacy_candidates[0][1]
        _, ext = os.path.splitext(best_legacy)
        new_filename = f"{name}{ext}"
        new_path = _get_favicon_path(new_filename)
        legacy_path = _get_favicon_path(best_legacy)
        try:
            legacy_path.rename(new_path)
            logger.info(f"Migrated favicon: {best_legacy} -> {new_filename}")
            # 清理其余旧文件
            for _, lf in legacy_candidates:
                if lf != best_legacy:
                    _get_favicon_path(lf).unlink(missing_ok=True)
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
            path = _get_favicon_path(filename)
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


def _try_fetch_from_providers(domain: str, scheme: str = "https", timeout: int = 10) -> tuple[str, bytes] | None:
    """依次尝试所有配置的 provider，返回第一个成功的结果 (content_type, body)。

    全部失败时返回 None。
    """
    url_parameters = {
        "url": f"{scheme}://{domain}",
        "domain": domain,
    }

    for provider_url in settings.LD_FAVICON_PROVIDERS:
        favicon_url = provider_url.format(**url_parameters)
        try:
            logger.debug(f"Trying favicon provider: {favicon_url}")
            with requests.get(favicon_url, timeout=timeout) as response:
                response.raise_for_status()
                body = response.content
                if _is_data_uri(body):
                    logger.debug(f"Provider returned data URI, trying next: {favicon_url}")
                    continue
                content_type = response.headers.get("Content-Type", "image/png")
                if _is_svg_placeholder(body, content_type):
                    logger.debug(f"Provider returned SVG placeholder, trying next: {favicon_url}")
                    continue
                return content_type, body
        except requests.exceptions.RequestException as e:
            logger.warning(f"Favicon provider failed: {favicon_url}: {e}")
            continue
    return None


def _is_valid_image(data: bytes) -> bool:
    """Check if data starts with known image file magic bytes."""
    if len(data) < 8:
        return False
    # PNG
    if data[:4] == bytes([0x89, 0x50, 0x4E, 0x47]):
        return True
    # JPEG
    if data[:3] == bytes([0xFF, 0xD8, 0xFF]):
        return True
    # GIF
    if data[:4] == b"GIF8":
        return True
    # ICO
    if data[:4] == bytes([0x00, 0x00, 0x01, 0x00]):
        return True
    # SVG: must contain <svg tag (not just any < character like HTML)
    if b"<svg" in data[:256] or (data[:5] == b"<?xml" and b"<svg" in data[:512]):
        return True
    # WebP
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return False


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
        logger.warning(f"Favicon provider returned invalid image data for {domain} (content_type={content_type}, size={len(body)})")
        return ""

    file_extension = mimetypes.guess_extension(content_type) or ".png"
    name = domain_to_filename(domain)
    favicon_file = f"{name}{file_extension}"
    favicon_path = _get_favicon_path(favicon_file)

    with open(favicon_path, "wb") as f:
        f.write(body)

    _remove_existing_variants(domain, keep_filename=favicon_file)
    logger.info(f"Saved favicon: {domain} -> {favicon_file}")
    return favicon_file
