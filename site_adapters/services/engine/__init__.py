"""
Script execution engine.

Public API:
    from site_adapters.services.engine import run_script
    from site_adapters.services.engine.browser_provider import launch_browser, get_browser_config
"""

from site_adapters.services.engine.script_runner import run_script

__all__ = ["run_script"]
