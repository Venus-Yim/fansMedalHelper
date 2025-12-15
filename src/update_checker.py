import requests
from loguru import logger

CURRENT_VERSION = "2.0.4"
VERSION_URL = "https://raw.githubusercontent.com/Venus-Yim/fansMedalHelper/master/version.txt"

log = logger.bind(user="更新检查", module="update_checker")

def check_update():
    try:
        latest = requests.get(VERSION_URL, timeout=2).text.strip()
        if latest != CURRENT_VERSION:
            log.warning(f"发现新版本：{latest}（当前版本：{CURRENT_VERSION}）")
        else:
            log.success(f"当前已是最新版本：{CURRENT_VERSION}")
    except Exception as e:
        log.warning(f"检查版本失败：{e}")