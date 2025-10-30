import json
import os
import sys
from loguru import logger
import warnings
import asyncio
import aiohttp
from src import BiliUser

log = logger.bind(user="B站粉丝牌助手")
__VERSION__ = "0.4.0"

warnings.filterwarnings(
    "ignore",
    message="The localize method is no longer necessary, as this time zone supports the fold attribute",
)
os.chdir(os.path.dirname(os.path.abspath(__file__)).split(__file__)[0])

try:
    if os.environ.get("USERS"):
        users = json.loads(os.environ.get("USERS"))
    else:
        import yaml

        with open("users.yaml", "r", encoding="utf-8") as f:
            users = yaml.load(f, Loader=yaml.FullLoader)
    assert users["LIKE_CD"] >= 0, "LIKE_CD参数错误"
    assert users["DANMAKU_CD"] >= 0, "DANMAKU_CD参数错误"
    assert users["WATCH_TARGET"] >= 0, "WATCH_TARGET参数错误"
    assert users["WATCH_MAX_ATTEMPTS"] >= users["WATCH_TARGET"], "WATCH_MAX_ATTEMPTS参数错误，不能小于WATCH_TARGET"
    assert users["WEARMEDAL"] in [0, 1], "WEARMEDAL参数错误"
    config = {
        "LIKE_CD": users["LIKE_CD"],
        "DANMAKU_CD": users["DANMAKU_CD"],
        "WATCH_TARGET": users["WATCH_TARGET"],
        "WATCH_MAX_ATTEMPTS": users["WATCH_MAX_ATTEMPTS"],
        "WEARMEDAL": users["WEARMEDAL"],
        "PROXY": users.get("PROXY"),
    }
except Exception as e:
    log.error(f"读取配置文件失败，请检查格式是否正确: {e}")
    exit(1)


@log.catch
async def main():
    messageList = []
    async with aiohttp.ClientSession(trust_env=True) as session:
#         try:
#             log.warning("当前版本为: " + __VERSION__)
#             resp = await (
#                 await session.get(
#                     "http://version.fansmedalhelper.1961584514352337.cn-hangzhou.fc.devsapp.net/"
#                 )
#             ).json()
#             if resp["version"] != __VERSION__:
#                 log.warning(f"新版本为: {resp['version']}，请更新")
#                 log.warning("更新内容: " + resp["changelog"])
#                 messageList.append(f"当前版本: {__VERSION__}，最新版本: {resp['version']}")
#                 messageList.append(f"更新内容: {resp['changelog']}")
#             if resp["notice"]:
#                 log.warning("公告: " + resp["notice"])
#                 messageList.append(f"公告: {resp['notice']}")
#         except Exception as ex:
#             log.warning(f"检查版本失败: {ex}")
#             messageList.append(f"检查版本失败: {ex}")

        # ------------------------------
        # 创建任务
        # ------------------------------
        startTasks = []
        for user in users["USERS"]:
            if user.get("access_key"):
                biliUser = BiliUser(
                    user["access_key"],
                    user.get("white_uid", ""),
                    user.get("banned_uid", ""),
                    config,
                )
                startTasks.append(biliUser.start())  # ✅ 新逻辑入口

        # ------------------------------
        # 并发执行所有用户任务
        # ------------------------------
        try:
            await asyncio.gather(*startTasks)
        except Exception as e:
            log.exception(e)
            messageList.append(f"任务执行失败: {e}")

        # ------------------------------
        # 消息推送
        # ------------------------------
        if users.get("SENDKEY", ""):
            await push_message(session, users["SENDKEY"], "  \n".join(messageList))

        if users.get("MOREPUSH", ""):
            from onepush import notify
            notifier = users["MOREPUSH"]["notifier"]
            params = users["MOREPUSH"]["params"]
            await notify(
                notifier,
                title=f"【B站粉丝牌助手推送】",
                content="  \n".join(messageList),
                **params,
                proxy=config.get("PROXY"),
            )
            log.info(f"{notifier} 已推送")

    log.info("所有任务执行完成。")


async def push_message(session, sendkey, message):
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    data = {"title": "【B站粉丝牌助手推送】", "desp": message}
    try:
        await session.post(url, data=data)
        log.info("Server酱已推送")
    except Exception as e:
        log.warning(f"Server酱推送失败: {e}")


def run(*args, **kwargs):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())
    log.info("任务结束，等待下一次执行。")


if __name__ == "__main__":
    cron = users.get("CRON", None)

    if cron:
#         from apscheduler.schedulers.blocking import BlockingScheduler
#         from apscheduler.triggers.cron import CronTrigger
# 
#         log.info(f"使用内置定时器 {cron}，开启定时任务。")
#         scheduler = BlockingScheduler()
#         scheduler.add_job(run, CronTrigger.from_crontab(cron), misfire_grace_time=3600)
#         scheduler.start()
        log.info("已配置定时器，开启循环任务。")
        run()
    elif "--auto" in sys.argv:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.interval import IntervalTrigger
        import datetime

        log.info("使用自动守护模式，每隔 24 小时运行一次。")
        scheduler = BlockingScheduler(timezone="Asia/Shanghai")
        scheduler.add_job(
            run,
            IntervalTrigger(hours=24),
            next_run_time=datetime.datetime.now(),
            misfire_grace_time=3600,
        )
        scheduler.start()
    else:
        log.info("未配置定时器，开启单次任务。")
        run()

