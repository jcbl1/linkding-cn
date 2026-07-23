from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "清除所有已缓存的 favicon 文件（保留目录）。框架会在用户浏览时按需重新获取。"

    def handle(self, *args, **options):
        favicon_folder = Path(settings.LD_FAVICON_FOLDER)
        if not favicon_folder.exists():
            self.stdout.write(self.style.WARNING("Favicon 目录不存在，无需清理"))
            return

        removed = 0
        for f in favicon_folder.iterdir():
            if f.is_file() and f.name not in (".DS_Store",):
                f.unlink()
                removed += 1

        # 注意：不需要清理 FaviconCache DB 记录。
        # favicon_image 视图会检测"DB 说 SUCCESS 但磁盘文件缺失"的不一致状态，
        # 自动触发后台重新获取，当期请求返回默认 favicon.svg，不会报错。
        self.stdout.write(self.style.SUCCESS(f"已清除 {removed} 个 favicon 文件"))
