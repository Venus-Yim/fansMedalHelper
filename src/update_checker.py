import requests
from loguru import logger

CURRENT_VERSION = "2.0.5"
VERSION_URL = "https://raw.githubusercontent.com/Venus-Yim/fansMedalHelper/master/version.txt"

log = logger.bind(user="更新检查", module="update_checker")

def check_update():
    try:
        lines = [i.strip() for i in requests.get(VERSION_URL, timeout=2).text.splitlines()]

        latest = lines[0] if len(lines) > 0 else ""
        info = lines[1] if len(lines) > 1 else ""
        if latest != CURRENT_VERSION:
            log.warning(f"发现新版本：{latest}（当前版本：{CURRENT_VERSION}），更新内容为：{info}")
        else:
            log.success(f"当前已是最新版本：{CURRENT_VERSION}")
    except Exception as e:

        log.warning(f"检查版本失败：{e}")
