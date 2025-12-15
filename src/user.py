from aiohttp import ClientSession, ClientTimeout
import asyncio
import sys
import os
import uuid
from loguru import logger
from datetime import datetime, timedelta
from croniter import croniter
import time
from collections import defaultdict
import pytz
import json

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger.remove()
logger.add(
    sys.stdout,
    colorize=True,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> <blue>{extra[user]}</blue> <level>{message}</level>",
    backtrace=False,
    diagnose=False,
)


class BiliUser:
    """
    按直播间状态与大航海身份执行点赞、弹幕、观看任务
    在2025.9更新后，大航海房间每日  点赞五次+弹幕五条  实际上仍能获得(5+5)*1.5(大航海系数加成)=15亲密度
    非大航海房间通过点赞或弹幕来维持灯牌点亮
    所有房间均能通过25 min有效观时来获得30基础亲密度
    """
    def __init__(self, access_token: str, whiteUIDs: str = '', bannedUIDs: str = '', config: dict = {}):
        from .api import BiliApi
        def _parse_uid_input(uids):
            """
            将多种可能的输入规范化为 int 列表。
            支持：
              - None -> []
              - list/tuple -> 逐项尝试 int()
              - str: "1,2,3" 或 "1, 2, 3" 或 "['1','2']" -> 按逗号切分再 int()
            会忽略无法转换为 int 的项（并不会抛异常）。
            """
            if not uids:
                return []
            # 如果已经是 list/tuple：直接尝试转换每一项
            if isinstance(uids, (list, tuple)):
                out = []
                for x in uids:
                    try:
                        out.append(int(x))
                    except Exception:
                        # 忽略不可转项
                        continue
                return out

            # 如果是字符串，按逗号切分并提取数字
            if isinstance(uids, str):
                # 先去掉常见的方括号、引号等，防止像 "[1,2]" 导致单项无法转 int
                s = uids.strip()
                # 去掉方括号和单/双引号（如果是像 "[1,2]"）
                s = s.strip("[]\"'")
                parts = [p.strip() for p in s.split(",") if p.strip()]
                out = []
                for p in parts:
                    try:
                        out.append(int(p))
                    except Exception:
                        # 尝试从字符串中提取连续数字（比如 "id: 1234"）
                        import re
                        m = re.search(r"(\d+)", p)
                        if m:
                            out.append(int(m.group(1)))
                        # 否则忽略
                return out

            # 其他类型（如单个 int）
            try:
                return [int(uids)]
            except Exception:
                return []

        self.access_key = access_token
        self.whiteList = _parse_uid_input(whiteUIDs)
        self.bannedList = _parse_uid_input(bannedUIDs)
        self.config = config

        self.mid, self.name = 0, ""
        self.medals = []
        self.message = []
        self.errmsg = []
        self.is_awake = True
        
        self.uuids = str(uuid.uuid4())
        self.session = ClientSession(timeout=ClientTimeout(total=5), trust_env=True)
        self.api = BiliApi(self, self.session)
        self._current_watch_task = None
        self._retry_info_like = {}
        self._retry_info_danmaku = {}

        self.log = logger.bind(user=self.name or "未知用户", uid=self.uuids)
        self.log_file = f"logs/{self.uuids}.log"
        self.sink_id = logger.add(
            self.log_file,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
            filter=lambda record: record["extra"].get("uid") == self.uuids,
            encoding="utf-8"
        )
    
    
    # ---------- 对当日已完成任务进行本地存储，避免当日重复打开后多次执行 ----------
    def _now_beijing(self):
        return datetime.now(pytz.timezone("Asia/Shanghai"))

    def _log_file(self):
        return os.path.join(os.path.dirname(__file__), f"task_log_{self.access_key}.json")

    def _load_log(self):
        try:
            with open(self._log_file(), "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}

    def _save_log(self, data):
        with open(self._log_file(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _clean_old_logs(self):
        logs = self._load_log()
        today = self._now_beijing().strftime("%Y-%m-%d")
        # 删除旧日期
        for date in list(logs.keys()):
            if date != today:
                del logs[date]
        self._save_log(logs)

    def _is_task_done(self, uid, task_type):
        logs = self._load_log()
        today = self._now_beijing().strftime("%Y-%m-%d")
        return uid in logs.get(today, {}).get(task_type, [])

    def _mark_task_done(self, uid, task_type):
        logs = self._load_log()
        today = self._now_beijing().strftime("%Y-%m-%d")
        logs.setdefault(today, {}).setdefault(task_type, []).append(uid)
        self._save_log(logs)
    
    
    # ------------------------- 登录与初始化 -------------------------
    async def loginVerify(self):
        info = await self.api.loginVerift()
        if info["mid"] == 0:
            self.log.error("登录失败，access_key 可能已过期")
            return False
        self.mid, self.name = info["mid"], info["name"]
        self.log = logger.bind(user=self.name)
        self.log.success(f"{self.name} 登录成功 (UID: {self.mid})")
        return True

    async def get_medals(self):
        """根据白名单/黑名单生成粉丝牌任务列表，保持白名单顺序"""
        self.medals.clear()
        all_medals = {}
        like_cd=self.config.get("LIKE_CD",0.3)
        danmaku_cd=self.config.get("DANMAKU_CD",3)
        watch_cd=self.config.get("WATCH_TARGET",25)
        
        self.log.info(f"开始获取任务列表，粉丝牌顺序为（排名先后即为执行任务先后）：")
        
        # 先获取全部勋章，用于白名单查找
        async for medal in self.api.getFansMedalandRoomID():
            all_medals[medal["medal"]["target_id"]] = medal

        if self.whiteList:
            for uid in self.whiteList:
                medal = all_medals.get(uid)
                anchor_info = (medal.get("anchor_info") if medal else None)
                if anchor_info:
                    name = anchor_info.get("nick_name", "未知主播")
                    if medal:
                        self.medals.append(medal)
                        self.log.info(f"{name}(uid：{uid})")
                    else:
                        self.log.error(f"白名单 {name}(uid：{uid}) 的粉丝牌 未拥有或被删除，已跳过")
                else:
                    self.log.error(f"白名单 uid：{uid} 对应的主播 不存在，已跳过")
        else:
            # 不使用白名单，添加所有勋章，剔除黑名单
            for uid, medal in all_medals.items():
                anchor_info = medal.get("anchor_info")
                if anchor_info:
                    name = anchor_info.get("nick_name", "未知主播")
                    if uid not in self.bannedList:
                        self.medals.append(medal)
                        self.log.info(f"{name}(uid：{uid})")
                    else:
                        self.log.warning(f"{name}(uid：{uid}) 在黑名单中，已跳过")
                else:
                    self.log.error(f"勋章列表 uid：{uid} 对应的主播 不存在，已跳过")
    
        # 生成待执行任务列表
        self.like_list = []
        self.danmaku_list = []
        self.watch_list = []

        today = self._now_beijing().strftime("%Y-%m-%d")
        logs = self._load_log().get(today, {})
        WATCH_TARGET = self.config.get("WATCH_TARGET", 25)

        for medal in self.medals:
            uid = medal["medal"]["target_id"]
            if like_cd and (medal['medal']['is_lighted']==0 or (not self._is_task_done(uid, "like") and medal["medal"]["guard_level"]>0)):
                self.like_list.append(medal)
            if danmaku_cd and (medal['medal']['is_lighted']==0 or (not self._is_task_done(uid, "danmaku") and medal["medal"]["guard_level"]>0)):
                self.danmaku_list.append(medal)
            if watch_cd:
                try:
                    watched = await self.api.getWatchLiveProgress(uid) * 5
                    if watched < WATCH_TARGET:
                        self.watch_list.append(medal)
                except Exception as e:
                    self.log.warning(f"{medal['anchor_info']['nick_name']} 获取直播状态失败: {e}")
            
        self.log.success(f"任务列表共 {len(self.medals)} 个粉丝牌(待点赞: {len(self.like_list)}, 待弹幕: {len(self.danmaku_list)}, 待观看: {len(self.watch_list)})\n")

    # ------------------------- 点赞任务 -------------------------
    async def like_room(self, room_id, medal, times=5):
        name = medal["anchor_info"]["nick_name"]
        success_count = 0
        target_id = medal["medal"]["target_id"]
        
        for i in range(times):
            fail_count = 0
            while fail_count < 3:
                try:
                    await self.api.likeInteractV3(room_id, target_id, self.mid)
                    success_count += 1
                    await asyncio.sleep(self.config.get("LIKE_CD", 0.3))
                    break  # 成功后退出重试循环
                except Exception as e:
                    fail_count += 1
                    self.log.warning(f"{name} 第 {i+1}/{times} 次点赞失败: {e}， 进行重试 (第{fail_count}/3次)")
                    
                    if fail_count < 3:
                        await asyncio.sleep(1)  # 等待1秒后重试
                    else:
                        self.log.error(f"{name} 第 {i+1}/{times} 次点赞连续失败3次，放弃此条。")
                        break

        self.log.success(f"{name} 点赞任务完成 ({success_count}/{times} 次成功)")



    # ------------------------- 弹幕任务 -------------------------
    async def send_danmaku(self, room_id, medal, times=10):
        if room_id == 10451956:
            return
        name = medal["anchor_info"]["nick_name"]
        target_id = medal["medal"]["target_id"]
        success_count = 0
        cd = self.config.get("DANMAKU_CD", 3)  # 弹幕间隔，可在 users.yaml 调整

        for i in range(times):
            fail_count = 0

            while fail_count < 3:
                try:
                    await self.api.sendDanmaku(room_id, msg=(f"机器人自动打卡，共{times}条~" if i == 0 else None))
                    success_count += 1
                    await asyncio.sleep(cd)  # 使用配置中的间隔
                    break  # 成功后跳出重试循环
                except Exception as e:
                    fail_count += 1
                    self.log.warning(f"{name} 第 {i+1}/{times} 条弹幕失败: {e}，进行重试 (第{fail_count}/3次)")
                        
                    if fail_count < 3:
                        await asyncio.sleep(5)  # 等待5秒后重试
                    else:
                        self.log.error(f"{name} 第 {i+1}/{times} 条弹幕连续失败3次，放弃此条。")
                        break

        self.log.success(f"{name} 弹幕任务完成 ({success_count}/{times} 条成功)")
        
    
    # ------------------------- 观看任务 -------------------------
    async def get_next_watchable(self, watch_list):
        """
        返回列表中最靠前的可观看房间（观看时长未达到25 min）
        """
        WATCH_TARGET = self.config.get("WATCH_TARGET", 25)
        for medal in watch_list.copy():
            uid = medal["medal"]["target_id"]
            room_id = medal["room_info"]["room_id"]

            try:
                watched = await self.api.getWatchLiveProgress(uid) * 5
                if watched >= WATCH_TARGET:
                    watch_list.remove(medal)
                    continue
                if await self.api.get_medal_light_status(uid)==0:
                    status = await self.api.getRoomLiveStatus(room_id)
                    if status == 1:
                        await self.like_room(room_id, medal, times=36)
                    else:
                        await self.send_danmaku(room_id, medal, times=10)
                    if await self.api.get_medal_light_status(uid)==0:
                        self.log.error(f"{medal['anchor_info']['nick_name']} 灯牌点亮失败，已将灯牌放至列表最后")
                        watch_list.remove(medal)
                        watch_list.append(medal)
                        await asyncio.sleep(0)
                        continue
                        
                return medal
                    
            except Exception as e:
                self.log.warning(f"{medal['anchor_info']['nick_name']} 判定是否可观看失败: {e}")
                continue
        return None  # 没有可观看房间
    
    
    async def watch_room(self, medal):
        """
        对单个房间进行观看直到完成或达到最大尝试
        """
        room_id = medal["room_info"]["room_id"]
        name = medal["anchor_info"]["nick_name"]
        target_id = medal["medal"]["target_id"]

        WATCH_TARGET = self.config.get("WATCH_TARGET", 25)
        MAX_ATTEMPTS = self.config.get("WATCH_MAX_ATTEMPTS", 50)
        attempts = 0
        
        try:
            watched = await self.api.getWatchLiveProgress(target_id) * 5
        except Exception as e:
            self.log.warning(f"{name} 获取观看进度失败: {e}")
            return False
        self.log.info(f"{name} 开始执行观看任务，还需{WATCH_TARGET-watched}分钟有效观看时长")
        
        while True:
            try:
                watched = await self.api.getWatchLiveProgress(target_id) * 5
            except Exception as e:
                self.log.warning(f"{name} 获取观看进度失败: {e}")
                return False

            if watched >= WATCH_TARGET:
                self.log.success(f"{name} 已观看 {watched} 分钟，任务完成")
                return True

            if attempts >= MAX_ATTEMPTS:
                self.log.error(f"{name} 超过最大尝试 {MAX_ATTEMPTS} 分钟，停止观看。该灯牌被放至观看队列最后。")
                self.watch_list.remove(medal)
                self.watch_list.append(medal)
                return False

            try:
                await self.api.heartbeat(room_id, target_id)
            except Exception as e:
                self.log.warning(f"{name} heartbeat 出错: {e}")
                return False

            attempts += 1
            await asyncio.sleep(60)
    
    async def _watch_task_wrapper(self, medal):
        """ 在后台运行单个 watch_room，并在结束后根据返回值从 watch_list 中移除 medal。 保证：不论成功/失败/异常，都会将 self._current_watch_task 置为 None。 """
        name = medal["anchor_info"]["nick_name"]
        try:
            ok = await self.watch_room(medal)
            if ok:
                # 如果 watch_room 成功，则把 medal 从 watch_list 中移除（若仍在列表中）
                try:
                    self.watch_list.remove(medal)
                except ValueError: # 已经被移除则忽略
                    pass
            else:
                # watch_room 返回 False 的情况下，watch_room 本身已经把 medal 放到队尾或记录了日志
                pass
        except asyncio.CancelledError:
            self.log.info(f"{name} 的后台观看任务被取消")
            raise
        except Exception as e:
            self.log.warning(f"{name} 的后台观看任务出现异常: {e}")
        finally:
            self._current_watch_task = None
            self.log.info(f"{name} 后台观看任务结束，_current_watch_task 清空。")
    
    async def task_loop(self):
        """按直播状态与用户类型执行点赞/弹幕任务，观看任务作为独立后台任务运行。
        - 重试/重复日志以每 30 分钟为周期节流（由上层 retry_info 控制）
        - 使用独立 day_change_watcher 通过事件通知实现跨天重启
        """
        # 确保 retry state 已存在
        if not hasattr(self, "_retry_info"):
            self._retry_info = {}

        LOG_INTERVAL = 1800  # 重复日志间隔：30 分钟

        # day change event：由 watcher 设置，start() 会根据这个事件决定是否重启
        self._day_changed_event = asyncio.Event()

        # ---------- 跨天监测子任务 ----------
        async def day_change_watcher():
            current_day = self._now_beijing().date()
            while True:
                await asyncio.sleep(5)
                now_day = self._now_beijing().date()
                if now_day != current_day:
                    self.log.success(f"检测到北京时间已进入新的一天（{current_day} → {now_day}），准备重新执行任务……")
                    # 标记跨天事件，由上层 start() 处理重启流程
                    self._day_changed_event.set()
                    return

        # ---------- 点赞/弹幕子循环 ----------
        async def like_danmaku_loop():
            # 将点赞和弹幕的 retry state 分开保存，避免相互覆盖和冲突
            if not hasattr(self, "_retry_info_like"):
                self._retry_info_like = {}
            if not hasattr(self, "_retry_info_danmaku"):
                self._retry_info_danmaku = {}

            while self.like_list or self.danmaku_list:
                
                now = time.time()

                def _key_for(medal):
                    return f"{medal['medal']['target_id']}:{medal['room_info']['room_id']}"

                def _ensure_state(key, kind: str):
                    """
                    kind: 'like' 或 'danmaku'
                    """
                    if kind == "like":
                        st = self._retry_info_like.get(key)
                        if st is None:
                            st = {"next_check": 0.0, "last_log": 0.0, "fail_count": 0}
                            self._retry_info_like[key] = st
                        return st
                    else:
                        st = self._retry_info_danmaku.get(key)
                        if st is None:
                            st = {"next_check": 0.0, "last_log": 0.0, "fail_count": 0}
                            self._retry_info_danmaku[key] = st
                        return st
                
                # 点赞
                for medal in self.like_list.copy():
                    key = _key_for(medal)
                    st = _ensure_state(key, "like")

                    # 跳过还未到下次检查时间的 medal
                    if now < st["next_check"]:
                        continue

                    uid = medal["medal"]["target_id"]
                    room_id = medal["room_info"]["room_id"]
                    guard = medal["medal"]["guard_level"]

                    try:
                        status = await self.api.getRoomLiveStatus(room_id)
                    except Exception as e:
                        # 网络或 API 错误：指数退避，日志每 LOG_INTERVAL 打一次
                        st["fail_count"] += 1
                        backoff = min(LOG_INTERVAL, 2 ** min(st["fail_count"], 10))
                        st["next_check"] = now + backoff
                        if now - st["last_log"] > LOG_INTERVAL:
                            st["last_log"] = now
                            self.log.warning(f"{medal['anchor_info']['nick_name']} 获取房间开播状态失败: {e} （后续 {int(backoff)}s 内不再重试）")
                        continue

                    # 非直播则不点赞：短退避，日志按 LOG_INTERVAL 节流
                    if status != 1:
                        st["fail_count"] += 1
                        st["next_check"] = now + 60  # 状态不符合时短退避
                        if st["fail_count"] == 1 or (now - st["last_log"] > LOG_INTERVAL):
                            st["last_log"] = now
                            if guard > 0:
                                self.log.info(f"{medal['anchor_info']['nick_name']} 未开播，点赞任务加入重试列表")
                        continue

                    # 真正执行点赞 —— 成功后移除 retry 状态并清理列表
                    try:
                        times = 10 if guard > 0 else 38
                        await self.like_room(room_id, medal, times=times)
                    except Exception as e:
                        # 如果点赞内部失败，也按指数退避处理并节流日志
                        st["fail_count"] += 1
                        backoff = min(LOG_INTERVAL, 2 ** min(st["fail_count"], 10))
                        st["next_check"] = now + backoff
                        if now - st["last_log"] > LOG_INTERVAL:
                            st["last_log"] = now
                            self.log.warning(f"{medal['anchor_info']['nick_name']} 点赞失败: {e} （后续 {int(backoff)}s 内不再重试）")
                        continue

                    # 点赞成功：移除 medal，标记完成，清理 like 的 retry state
                    try:
                        self.like_list.remove(medal)
                    except ValueError:
                        pass
                    self._mark_task_done(uid, "like")
                    if key in self._retry_info_like:
                        del self._retry_info_like[key]

                    # 如果是非大航海，并且也在弹幕列表中，则移除弹幕任务
                    if guard == 0 and medal in self.danmaku_list:
                        try:
                            self.danmaku_list.remove(medal)
                        except ValueError:
                            pass
                        self._mark_task_done(uid, "danmaku")
                        # 清理对应 danmaku retry state
                        if key in self._retry_info_danmaku:
                            del self._retry_info_danmaku[key]
                
                # 弹幕
                for medal in self.danmaku_list.copy():
                    key = _key_for(medal)
                    st = _ensure_state(key, "danmaku")

                    if now < st["next_check"]:
                        continue
                    
                    uid = medal["medal"]["target_id"]
                    room_id = medal["room_info"]["room_id"]
                    guard = medal["medal"]["guard_level"]

                    try:
                        status = await self.api.getRoomLiveStatus(room_id)
                    except Exception as e:
                        st["fail_count"] += 1
                        backoff = min(LOG_INTERVAL, 2 ** min(st["fail_count"], 10))
                        st["next_check"] = now + backoff
                        if now - st["last_log"] > LOG_INTERVAL:
                            st["last_log"] = now
                            self.log.warning(f"{medal['anchor_info']['nick_name']} 获取房间开播状态失败: {e} （后续 {int(backoff)}s 内不再重试）")
                        continue
                    
                    # 如果正在直播则不发弹幕，短退避并按 LOG_INTERVAL 节流日志
                    if status == 1:
                        st["fail_count"] += 1
                        st["next_check"] = now + 60
                        if st["fail_count"] == 1 or (now - st["last_log"] > LOG_INTERVAL):
                            st["last_log"] = now
                            if guard > 0:
                                self.log.info(f"{medal['anchor_info']['nick_name']} 开播中，弹幕任务加入重试列表")
                        continue
                    
                    # 真正执行弹幕
                    try:
                        times = 5 if guard > 0 else 10
                        await self.send_danmaku(room_id, medal, times=times)
                    except Exception as e:
                        st["fail_count"] += 1
                        backoff = min(LOG_INTERVAL, 2 ** min(st["fail_count"], 10))
                        st["next_check"] = now + backoff
                        if now - st["last_log"] > LOG_INTERVAL:
                            st["last_log"] = now
                            self.log.warning(f"{medal['anchor_info']['nick_name']} 发送弹幕失败: {e} （后续 {int(backoff)}s 内不再重试）")
                        continue
                    
                    # 弹幕成功：移除 medal，标记完成，清理 danmaku 的 retry state
                    try:
                        self.danmaku_list.remove(medal)
                    except ValueError:
                        pass
                    self._mark_task_done(uid, "danmaku")
                    if key in self._retry_info_danmaku:
                        del self._retry_info_danmaku[key]

                    if guard == 0 and medal in self.like_list:
                        try:
                            self.like_list.remove(medal)
                        except ValueError:
                            pass
                        self._mark_task_done(uid, "like")
                        # 清理对应 like retry state
                        if key in self._retry_info_like:
                            del self._retry_info_like[key]

                # Per-medal 控制已经大幅减少重复查询与日志，因此短 sleep 足够
                await asyncio.sleep(5)


        # ---------- 观看管理子循环 ----------
        async def watch_manager_loop():
            while self.watch_list or self._current_watch_task:
                if self._current_watch_task is None and self.watch_list:
                    try:
                        watch_medal = await self.get_next_watchable(self.watch_list)
                    except Exception as e:
                        self.log.warning(f"选择可观看房间时出错: {e}")
                        watch_medal = None

                    if watch_medal:
                        self.log.info(f"启动后台观看任务: {watch_medal['anchor_info']['nick_name']} (room: {watch_medal['room_info']['room_id']})")
                        self._current_watch_task = asyncio.create_task(self._watch_task_wrapper(watch_medal))

                await asyncio.sleep(10)

        # ---------- 启动并管理子任务 ----------
        # 启动 day watcher
        if not hasattr(self, "_day_watch_task") or self._day_watch_task.done():
            self._day_watch_task = asyncio.create_task(day_change_watcher())

        # 循环检查子任务与退出条件（当 day change 触发或任务全部完成时退出）
        try:
            while True:
                # 若跨天事件触发，立即中止循环以便上层 start() 进行重启
                if getattr(self, "_day_changed_event", None) and self._day_changed_event.is_set():
                    break

                # 全部任务空闲且无后台观看，退出
                if not (self.like_list or self.danmaku_list or self.watch_list or self._current_watch_task):
                    break

                # 启动点赞/弹幕与 watch 管理子任务（如果尚未启动或已结束）
                if not hasattr(self, "_like_task") or self._like_task.done():
                    self._like_task = asyncio.create_task(like_danmaku_loop())
                if not hasattr(self, "_watch_manager_task") or self._watch_manager_task.done():
                    self._watch_manager_task = asyncio.create_task(watch_manager_loop())

                # 主循环短睡以便周期性检查（如跨天），并不影响后台 watch task
                await asyncio.sleep(5)
        finally:
            # 退出前尝试取消仍在运行的子任务（若有）
            for tname in ("_like_task", "_watch_manager_task", "_day_watch_task"):
                task = getattr(self, tname, None)
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        self.log.info("task_loop 退出。")
        return

    async def start(self):
        """启动任务：初始化本地日志记录→登录→获取勋章列表→循环执行点赞/弹幕/观看
        start 会在跨天触发（任务未全部执行完成）时立即重新开始（即时重启到新的一天）；若任务全部执行完成，则交由main根据 CRON 配置情况进行跨天重启。
        """
        from aiohttp import ClientSession, ClientTimeout
        from croniter import croniter
        from datetime import datetime

        # 清理旧日志
        self._clean_old_logs()

        # 循环直到不需要继续（由跨天/CRON 决定）
        while True:
            # 建立 session（若无）
            if not getattr(self.api, "session", None) or self.api.session.closed:
                self.api.session = ClientSession(timeout=ClientTimeout(total=5), trust_env=True)

            # 登录验证
            if not await self.loginVerify():
                try:
                    if getattr(self, "session", None) and not self.session.closed:
                        await self.session.close()
                except Exception:
                    pass
                return

            # 获取勋章列表
            await self.get_medals()
            if not self.medals:
                self.log.info("没有可执行任务的粉丝牌")
                try:
                    if getattr(self, "session", None) and not self.session.closed:
                        await self.session.close()
                except Exception:
                    pass
                return

            # 初始化 retry info（若尚未）
            if not hasattr(self, "_retry_info"):
                self._retry_info = {}

            self.log.info("开始执行任务：")

            # 调用主循环（阻塞直到任务完成或跨天事件触发）
            await self.task_loop()

            # 如果是跨天触发，立即重新开始（无需等待 CRON）
            if getattr(self, "_day_changed_event", None) and self._day_changed_event.is_set():
                # 清理旧 session 并立即重启新一天的任务流程
                try:
                    if getattr(self.api, "session", None) and not self.api.session.closed:
                        await self.api.session.close()
                except Exception:
                    pass
                
                self.log.info("检测到跨天，已退出以等待外部调度器/下一次 run() 触发新任务。")
                return

            # 否则，任务为“正常完成”——关闭 session 并根据 CRON 决定是否等待重启
            self.log.success("所有任务执行完成")
            try:
                if getattr(self.api, "session", None) and not self.api.session.closed:
                    await self.api.session.close()
            except Exception:
                pass

            return

