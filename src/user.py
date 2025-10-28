from aiohttp import ClientSession, ClientTimeout
import asyncio
import sys
import os
import uuid
from loguru import logger
from datetime import datetime, timedelta
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
        
        self.uuids = str(uuid.uuid4())
        self.session = ClientSession(timeout=ClientTimeout(total=5), trust_env=True)
        self.api = BiliApi(self, self.session)

        self.log = logger.bind(user="未知用户")
    
    
    # ---------- 对当日已完成任务进行本地存储，避免当日重复打开后多次执行 ----------
    def _now_beijing(self):
        return datetime.now(pytz.timezone("Asia/Shanghai"))

    def _log_file(self):
        return os.path.join(os.path.dirname(__file__), "task_log.json")

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
            # 点赞和弹幕任务，剔除已完成
            if uid not in logs.get("like", []):
                self.like_list.append(medal)
            if uid not in logs.get("danmaku", []):
                self.danmaku_list.append(medal)
            # 观看任务全部加入，执行时再判断是否完成
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
        
        if self._is_task_done(target_id, "like"):
            self.log.info(f"{name} 点赞任务已完成，跳过。")
            return
        
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
        name = medal["anchor_info"]["nick_name"]
        target_id = medal["medal"]["target_id"]
        success_count = 0
        cd = self.config.get("DANMAKU_CD", 3)  # 弹幕间隔，可在 users.yaml 调整

        if self._is_task_done(target_id, "danmaku"):
            self.log.info(f"{name} 弹幕任务已完成，跳过。")
            return

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
        返回列表中最靠前的可观看房间（观看时长未达到25 min  且  不是轮播直播间）
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
                status = await self.api.getRoomLiveStatus(room_id)
                if status != 2:  # 轮播房间跳过
                    return medal
            except Exception as e:
                self.log.warning(f"{medal['anchor_info']['nick_name']} 获取直播状态失败: {e}")
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
                self.log.error(f"{name} 超过最大尝试 {MAX_ATTEMPTS} 分钟，停止观看")
                return True

            try:
                await self.api.heartbeat(room_id, target_id)
            except Exception as e:
                self.log.warning(f"{name} heartbeat 出错: {e}")
                return False

            attempts += 1
            await asyncio.sleep(60)
        

    async def task_loop(self):
        """按照直播状态与用户类型执行点赞/弹幕/观看任务"""
        current_day = self._now_beijing().date()  # 记录初始日期
        
        while self.like_list or self.danmaku_list or self.watch_list:
            # 每次循环检查是否跨天（北京时间）
            now_day = self._now_beijing().date()
            if now_day != current_day:
                self.log.success(f"检测到北京时间已进入新的一天（{current_day} → {now_day}），正在重新执行任务……")
                # 清理旧任务与日志
                await self.session.close()
                await asyncio.sleep(5)  # 稍等以防止接口频率过快
                if self.api.session and not self.api.session.closed:
                    await self.api.session.close()
                self.api.session = ClientSession(timeout=ClientTimeout(total=5), trust_env=True)
                await self.start()      # 重新启动整个流程
                return                  # 结束旧循环
            
            # 1. 点赞任务（开播才做）
            for medal in self.like_list.copy():
                uid = medal["medal"]["target_id"]
                room_id = medal["room_info"]["room_id"]
                guard = medal["medal"]["guard_level"]
                
                status = await self.api.getRoomLiveStatus(room_id)
                if status != 1:  # 0未开播，1直播，2轮播
                    if guard>0:
                        self.log.info(f"{medal['anchor_info']['nick_name']} 未开播，点赞任务加入重试列表")
                    continue

                times = 10 if guard > 0 else 36
                await self.like_room(room_id, medal, times=times)

                self.like_list.remove(medal)
                self._mark_task_done(uid, "like")
                if guard == 0 and medal in self.danmaku_list:
                    self.danmaku_list.remove(medal)
                    self._mark_task_done(uid, "danmaku")

            # 2. 弹幕任务（未开播才做）
            for medal in self.danmaku_list.copy():
                uid = medal["medal"]["target_id"]
                room_id = medal["room_info"]["room_id"]
                guard = medal["medal"]["guard_level"]
                
                status = await self.api.getRoomLiveStatus(room_id)
                if status == 1:  # 开播状态不发弹幕
                    if guard>0:
                        self.log.info(f"{medal['anchor_info']['nick_name']} 开播中，弹幕任务加入重试列表")
                    continue

                times = 5 if guard > 0 else 10
                await self.send_danmaku(room_id, medal, times=times)

                self.danmaku_list.remove(medal)
                self._mark_task_done(uid, "danmaku")
                if guard == 0 and medal in self.like_list:
                    self.like_list.remove(medal)
                    self._mark_task_done(uid, "like")

            # 3. 观看任务
            medal = await self.get_next_watchable(self.watch_list)

            if medal:
                ok = await self.watch_room(medal)
                if ok:
                    self.watch_list.remove(medal)
            else:
                # 没有可观看房间时，等待一会再重新检查
                await asyncio.sleep(60)

            if not (self.like_list or self.danmaku_list or self.watch_list):
                break
            
            
    # ------------------------- 主流程控制 -------------------------
    async def start(self):
        """启动任务：初始化本地日志记录→登录→获取勋章列表→循环执行点赞/弹幕/观看"""
        self._clean_old_logs()

        # 登录验证
        if not self.api.session or self.api.session.closed:
            self.api.session = ClientSession(timeout=ClientTimeout(total=5), trust_env=True)
        if not await self.loginVerify():
            await self.session.close()
            return

        # 获取勋章列表
        await self.get_medals()
        if not self.medals:
            self.log.info("没有可执行任务的粉丝牌")
            await self.session.close()
            return

        self.log.info(f"开始执行任务：")

        # 循环执行点赞→弹幕→观看
        await self.task_loop()

        self.log.success("所有任务执行完成")
        await self.session.close()
        
        # ---- 等待到下一天后自动重启 ----
        now = self._now_beijing()
        next_day = (now + timedelta(days=1)).replace(hour=0, minute=5, second=5, microsecond=0)
        sleep_seconds = (next_day - now).total_seconds()
        self.log.info(f"等待至北京时间 {next_day.strftime('%Y-%m-%d %H:%M:%S')} 自动开始新任务（约 {sleep_seconds/3600:.2f} 小时）")
        await asyncio.sleep(sleep_seconds)
        if self.api.session and not self.api.session.closed:
            await self.api.session.close()
        self.api.session = ClientSession(timeout=ClientTimeout(total=5), trust_env=True)
        try:
            await self.start()
        except Exception as e:
            self.log.error(f"主任务执行出错：{e}")
            await asyncio.sleep(60)
            await self.start()
