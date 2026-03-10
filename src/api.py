import asyncio
from hashlib import md5
import hashlib
import os
import random
import sys
import time
import json
import re
import traceback
from typing import Union
from loguru import logger
from urllib.parse import urlencode, urlparse


from aiohttp import ClientSession

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


MIXIN_KEY_ENC_TAB = [
46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,
27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,
37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,
22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52
]

def _get_mixin_key(img_key, sub_key):
    s = img_key + sub_key
    return ''.join([s[i] for i in MIXIN_KEY_ENC_TAB])[:32]


class Crypto:
    APPKEY = "4409e2ce8ffd12b8"
    APPSECRET = "59b43e04ad6965f34319062b478f83dd"

    @staticmethod
    def md5(data: Union[str, bytes]) -> str:
        """generates md5 hex dump of `str` or `bytes`"""
        if type(data) == str:
            return md5(data.encode()).hexdigest()
        return md5(data).hexdigest()

    @staticmethod
    def sign(data: Union[str, dict]) -> str:
        """salted sign funtion for `dict`(converts to qs then parse) & `str`"""
        if isinstance(data, dict):
            _str = urlencode(data)
        elif type(data) != str:
            raise TypeError
        return Crypto.md5(_str + Crypto.APPSECRET)


class SingableDict(dict):
    @property
    def sorted(self):
        """returns a alphabetically sorted version of `self`"""
        return dict(sorted(self.items()))

    @property
    def signed(self):
        """returns our sorted self with calculated `sign` as a new key-value pair at the end"""
        _sorted = self.sorted
        return {**_sorted, "sign": Crypto.sign(_sorted)}


# def retry(tries=60, interval=1):
#     def decorate(func):
#         async def wrapper(*args, **kwargs):
#             count = 0
#             func.isRetryable = False
#             log = logger.bind(user=f"{args[0].u.name}")
#             while True:
#                 try:
#                     result = await func(*args, **kwargs)
#                 except Exception as e:
#                     count += 1
#                     if type(e) == BiliApiError:
#                         if e.code == 1011040:
#                             raise e
#                         elif e.code == 10030:
#                             await asyncio.sleep(10)
#                         elif e.code == -504:
#                             pass
#                         else:
#                             raise e
#                     if count > tries:
#                         log.error(f"API {urlparse(args[1]).path} 调用出现异常: {str(e)}")
#                         raise e
#                     else:
#                         # log.error(f"API {urlparse(args[1]).path} 调用出现异常: {str(e)}，重试中，第{count}次重试")
#                         await asyncio.sleep(interval)
#                     func.isRetryable = True
#                 else:
#                     if func.isRetryable:
#                         pass
#                         # log.success(f"重试成功")
#                     return result
# 
#         return wrapper
# 
#     return decorate

def retry(tries=60, interval=1):
    def decorate(func):
        async def wrapper(*args, **kwargs):
            import traceback
            count = 0
            func.isRetryable = False
            # 尽量安全绑定用户标识，避免在异常路径再次访问 args 导致新的异常
            try:
                user_name = getattr(args[0].u, "name", "unknown")
            except Exception:
                user_name = "unknown"
            log = logger.bind(user=f"{user_name}")
            while True:
                try:
                    result = await func(*args, **kwargs)
                except Exception as e:
                    count += 1
                    tb = traceback.format_exc()
                    # 打印到控制台
                    #print(f"[RETRY] exception in {func.__name__}: {e}\nTraceback:\n{tb}", flush=True)
                    # 也用 loguru 记录
                    #log.error(f"Exception in {func.__name__}: {e}\n{tb}")
                    # 特殊处理 BiliApiError 
                    if type(e) == BiliApiError:
                        if e.code == 1011040:
                            raise e
                        elif e.code == 10030:
                            await asyncio.sleep(10)
                        elif e.code == -504:
                            pass
                        elif e.code == -352:
                            print(f"因b站接口更新，点赞模块暂不可用，建议在配置文件中临时关闭点赞功能，并关注本项目后续更新。受影响功能有：1.大航海每日开播自动点赞；2.非大航海开播期间自动点亮。")
                            log.error(f"因b站接口更新，点赞模块暂不可用，建议在配置文件中临时关闭点赞功能，并关注本项目后续更新。受影响功能有：1.大航海每日开播自动点赞；2.非大航海开播期间自动点亮。")
                        else:
                            raise e
                    if count > tries:
                        # 如果超过重试限额，同时输出上下文并抛出
                        try:
                            u_path = args[1] if len(args) > 1 else "unknown"
                            log.error(f"API {u_path} 调用出现异常: {str(e)}")
                        except Exception:
                            log.error(f"API call出现异常且无法取得路径: {e}")
                        raise e
                    else:
                        await asyncio.sleep(interval)
                    func.isRetryable = True
                else:
                    if func.isRetryable:
                        pass
                    return result

        return wrapper

    return decorate


def client_sign(data: dict):
    _str = json.dumps(data, separators=(",", ":"))
    for n in ["sha512", "sha3_512", "sha384", "sha3_384", "blake2b"]:
        _str = hashlib.new(n, _str.encode("utf-8")).hexdigest()
    return _str


def randomString(length: int = 16) -> str:
    return "".join(
        random.sample("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", length)
    )


class BiliApiError(Exception):
    def __init__(self, code: int, msg: str):
        self.code = code
        self.msg = msg

    def __str__(self):
        return f"错误代码({self.code}):{self.msg}"


class BiliApi:
    headers = {
        "User-Agent": "Mozilla/5.0 BiliDroid/6.73.1 (bbcallen@gmail.com) os/android model/Mi 10 Pro mobi_app/android build/6731100 channel/xiaomi innerVer/6731110 osVer/12 network/2",
    }
    from .user import BiliUser

    def __init__(self, u: BiliUser, s: ClientSession):
        self.u = u
        self.session = s
        self._wbi_cache = None
        # create per-instance headers copy to avoid cross-user mutation
        # NOTE: BiliApi.headers 原为类属性；在实例化时复制一份到 self.headers
        self.headers = dict(self.__class__.headers)

    def __check_response(self, resp: dict) -> dict:
        if resp["code"] != 0 or ("mode_info" in resp["data"] and resp["message"] != ""):
            raise BiliApiError(resp["code"], resp["message"])
        return resp["data"]

    @retry()
    async def __get(self, *args, **kwargs):
        async with self.session.get(*args, **kwargs) as resp:
            return self.__check_response(await resp.json())

    @retry()
    async def __post(self, *args, **kwargs):
        async with self.session.post(*args, **kwargs) as resp:
            return self.__check_response(await resp.json())
        
    async def _get_wbi_key(self):
        """
        获取 WBI mixin_key，并缓存
        """
        now = int(time.time())

        if hasattr(self, "_wbi_cache") and self._wbi_cache is not None:
            key, ts = self._wbi_cache
            if now - ts < 3600:
                return key

        url = "https://api.bilibili.com/x/web-interface/nav"

        async with self.session.get(url) as resp:
            data = await resp.json()

        img_url = data["data"]["wbi_img"]["img_url"]
        sub_url = data["data"]["wbi_img"]["sub_url"]

        img_key = img_url.split("/")[-1].split(".")[0]
        sub_key = sub_url.split("/")[-1].split(".")[0]

        mixin_key = _get_mixin_key(img_key, sub_key)

        self._wbi_cache = (mixin_key, now)

        return mixin_key

    @retry()
    async def loginVerift(self):
        """
        登录验证
        """
        url = "https://app.bilibili.com/x/v2/account/mine"
        params = {
            "access_key": self.u.access_key,
            "actionKey": "appkey",
            "appkey": Crypto.APPKEY,
            "ts": int(time.time()),
        }
        return await self.__get(url, params=SingableDict(params).signed, headers=self.headers)

    async def getFansMedalandRoomID(self) -> dict:
        """
        获取用户粉丝勋章和直播间ID
        """
        url = "https://api.live.bilibili.com/xlive/app-ucenter/v1/fansMedal/panel"
        params = {
            "access_key": self.u.access_key,
            "actionKey": "appkey",
            "appkey": Crypto.APPKEY,
            "ts": int(time.time()),
            "page": 1,
            "page_size": 50,
        }
        first_flag = True
        while True:
            data = await self.__get(url, params=SingableDict(params).signed, headers=self.headers)
            if first_flag and data["special_list"]:
                for item in data["special_list"]:
                    yield item
                self.u.wearedMedal = data["special_list"][0]
                first_flag = False
            for item in data["list"]:
                yield item
            if not data["list"]:
                break
            params["page"] += 1
    
    @retry()
    async def get_medal_light_status(self, target_id: int) -> int | None:
        """
        根据 target_id 获取该粉丝牌的点亮状态
        返回：
            1 表示点亮，0 表示未点亮，None 表示没找到该勋章
        """
        async for medal in self.getFansMedalandRoomID():
            # medal结构内的目标ID字段名为 'medal' -> 'target_id'
            if medal.get('medal', {}).get('target_id') == target_id:
                return medal['medal'].get('is_lighted')
        return None

    
    @retry()
    async def getRoomLiveStatus(self, room_id: int) -> int:
        '''获取直播间当前开播状态'''
        url = "https://api.live.bilibili.com/room/v1/Room/get_info"
        params = {"room_id": room_id}
        async with self.session.get(url, params=params, headers=self.headers) as resp:
            data = await resp.json()
            if data["code"] != 0:
                self.user.log.warning(f"获取直播状态失败: {data['message']}")
                return 0  # 未开播
            return data["data"]["live_status"]  # 0=未开播, 1=直播, 2=轮播

    @retry()
    async def getWatchLiveProgress(self, target_id: int) -> int:
        """
        获取观看直播任务的完成进度
        :param target_id: 粉丝牌目标用户 UID
        :return: int, 完成次数 (0-5)
        """
        url = "https://api.live.bilibili.com/xlive/app-ucenter/v1/fansMedal/GetActivatedMedalInfo"
        params = {
            "access_key": self.u.access_key,
            "actionKey": "appkey",
            "appkey": Crypto.APPKEY,
            "target_id": target_id,
            "web_location": "444.260",
        }
        resp = await self.__get(url, params=params, headers=self.headers)

        task_info = resp.get("task_info", [])
        for task in task_info:
            if task.get("jump_type") == "watchLive":
                sub = task.get("sub_title", "")
                # 用正则提取两个数字
                nums = re.findall(r"(\d+)", sub)
                if len(nums) >= 2:
                    current, total = map(int, nums[:2])
                    return min(total, current)
                elif len(nums) == 1:
                    return int(nums[0])
                else:
                    return 0
        return 0

    @retry()
    async def likeInteractV3(self, room_id: int, up_id: int, self_uid: int):
        url = "https://api.live.bilibili.com/xlive/app-ucenter/v1/like_info_v3/like/likeReportV3"

        print(f"[API ENTRY] likeInteractV3 room={room_id} up_id={up_id} self_uid={self_uid}", flush=True)

        try:
            # 基本 params，先用 str 包裹所有 value，防止 int 泄漏
            params = {
                "click_time": str(random.randint(2,10)),
                "room_id": str(room_id),
                "uid": str(self_uid),
                "anchor_id": str(up_id),
                "web_location": "444.8",
                "wts": str(int(time.time()))
            }

            # 读取 csrf
            bili_jct = None
            try:
                jar = getattr(self.session, "cookie_jar", None)
                if jar:
                    # 用 host 而非整个 url 保证 filter_cookies 正常
                    cookies = jar.filter_cookies("https://api.live.bilibili.com")
                    if "bili_jct" in cookies:
                        bili_jct = cookies["bili_jct"].value
            except Exception as ex:
                print(f"[API] cookie read failure: {ex}", flush=True)

            if not bili_jct:
                # 退回到 headers 中查找
                cookie_hdr = self.headers.get("Cookie", "") if hasattr(self, "headers") else ""
                m = re.search(r"bili_jct=([^;]+)", cookie_hdr)
                if m:
                    bili_jct = m.group(1)

            if bili_jct:
                params["csrf"] = str(bili_jct)

            # 获取动态 mixin_key（会缓存）
            mixin_key = await self._get_wbi_key()

            # 构造 query 并签名
            query = urlencode(sorted(params.items()))
            query = re.sub(r"[!'()*]", "", query)
            w_rid = hashlib.md5((query + mixin_key).encode()).hexdigest()
            params["w_rid"] = w_rid

            params = {k: str(v) for k, v in params.items()}

            headers = {
                "Origin": "https://live.bilibili.com",
                "Referer": f"https://live.bilibili.com/{room_id}",
                "User-Agent": str(self.headers.get("User-Agent", "Mozilla/5.0")) if hasattr(self, "headers") else "Mozilla/5.0",
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "x-client-sign": str(w_rid)
            }

            print("[API DEBUG] prepared likeReportV3 call", flush=True)
            print(" params:", params, flush=True)
            print(" param types:", {k: type(v).__name__ for k,v in params.items()}, flush=True)
            print(" headers:", headers, flush=True)
            try:
                jar2 = self.session.cookie_jar.filter_cookies("https://api.live.bilibili.com")
                cookie_vals = {k: v.value for k, v in jar2.items()}
            except Exception as ex:
                cookie_vals = f"<cookie read failed: {ex}>"
            print(" cookies:", cookie_vals, flush=True)

            # 发送请求
            async with self.session.post(url, params=params, headers=headers, data=b"") as resp:
                text = await resp.text()
                try:
                    data = await resp.json()
                except Exception as ex:
                    #tb = traceback.format_exc()
                    #print(f"[API ERROR] JSON decode failed: {ex}\nresp_text={text}\ntraceback:\n{tb}", flush=True)
                    try:
                        self.u.log.error(f"likeReportV3 JSON decode failed: {ex}")
                    except Exception:
                        pass
                    raise

        except Exception as e:
            # 捕获所有异常并把完整 traceback 打印到 stdout
            #tb = traceback.format_exc()
            #print(f"[API EXCEPTION] likeInteractV3 failed: {e}\nTraceback:\n{tb}", flush=True)
            try:
                self.u.log.error(f"[API EXCEPTION] likeInteractV3 failed: {e}")
            except Exception:
                pass
            raise

        print(f"[API] likeReportV3 response: {data}", flush=True)
        try:
            self.u.log.info(f"[API] likeReportV3 response: {data}")
        except Exception:
            pass

        return self.__check_response(data)

    async def sendDanmaku(self, room_id: int, msg: str = None) -> str:
        """
        发送弹幕（单次请求失败就抛异常，由上层处理重试）
        """
        url = "https://api.live.bilibili.com/xlive/app-room/v1/dM/sendmsg"
        danmakus = [
            "(⌒▽⌒).", "（￣▽￣）.", "(=・ω・=).", "(｀・ω・´).", "(〜￣△￣)〜.",
            "(･∀･).", "(°∀°)ﾉ.", "(￣3￣).", "╮(￣▽￣)╭.", "_(:3」∠)_",
            "(^・ω・^ ).", "(●￣(ｴ)￣●).", "ε=ε=(ノ≧∇≦)ノ.", "⁄(⁄ ⁄•⁄ω⁄•⁄ ⁄)⁄.", "←◡←.",
        ]
        params = {
            "access_key": self.u.access_key,
            "actionKey": "appkey",
            "appkey": Crypto.APPKEY,
            "ts": int(time.time()),
        }
        data = {
            "cid": room_id,
            "msg": msg if msg else random.choice(danmakus),
            "rnd": int(time.time()),
            "color": "16777215",
            "fontsize": "25",
        }
        self.headers.update({"Content-Type": "application/x-www-form-urlencoded"})

        # 仅尝试一次，如果失败则直接抛异常
        resp = await self.__post(
            url, params=SingableDict(params).signed, data=data, headers=self.headers
        )
        return json.loads(resp["mode_info"]["extra"])["content"]
    
    @retry()
    async def heartbeat(self, room_id: int, up_id: int):
        url = "https://live-trace.bilibili.com/xlive/data-interface/v1/heartbeat/mobileHeartBeat"
        data = {
            "platform": "android",
            "uuid": self.u.uuids[0],
            "buvid": randomString(37).upper(),
            "seq_id": "1",
            "room_id": f"{room_id}",
            "parent_id": "6",
            "area_id": "283",
            "timestamp": f"{int(time.time())-60}",
            "secret_key": "axoaadsffcazxksectbbb",
            "watch_time": "60",
            "up_id": f"{up_id}",
            "up_level": "40",
            "jump_from": "30000",
            "gu_id": randomString(43).lower(),
            "play_type": "0",
            "play_url": "",
            "s_time": "0",
            "data_behavior_id": "",
            "data_source_id": "",
            "up_session": f"l:one:live:record:{room_id}:{int(time.time())-88888}",
            "visit_id": randomString(32).lower(),
            "watch_status": "%7B%22pk_id%22%3A0%2C%22screen_status%22%3A1%7D",
            "click_id": self.u.uuids[1],
            "session_id": "",
            "player_type": "0",
            "client_ts": f"{int(time.time())}",
        }
        data.update(
            {
                "client_sign": client_sign(data),
                "access_key": self.u.access_key,
                "actionKey": "appkey",
                "appkey": Crypto.APPKEY,
                "ts": int(time.time()),
            }
        )
        self.headers.update(
            {
                "Content-Type": "application/x-www-form-urlencoded",
            }
        ),
        return await self.__post(url, data=SingableDict(data).signed, headers=self.headers)
    
    @retry()
    async def signIn(self, ruid: int, room_id: int, activity_id: int = 109745):

        url = "https://api.live.bilibili.com/xlive/custom-activity-interface/baseActivity/DoSignIn"

        # 读取 csrf
        bili_jct = None
        try:
            jar = getattr(self.session, "cookie_jar", None)
            if jar:
                # 用 host 而非整个 url 保证 filter_cookies 正常
                cookies = jar.filter_cookies("https://api.live.bilibili.com")
                if "bili_jct" in cookies:
                    bili_jct = cookies["bili_jct"].value
        except Exception as ex:
            print(f"[API] cookie read failure: {ex}", flush=True)

        if not bili_jct:
            # 退回到 headers 中查找
            cookie_hdr = self.headers.get("Cookie", "") if hasattr(self, "headers") else ""
            m = re.search(r"bili_jct=([^;]+)", cookie_hdr)
            if m:
                bili_jct = m.group(1)
        csrf = str(bili_jct)
        
        params = {
            "activity_id": activity_id,
            "ruid": ruid,
            "platform": "web",
            "csrf": csrf
        }
        
        headers = {
            "Origin": "https://live.bilibili.com",
            "Referer": f"https://live.bilibili.com/{room_id}",
            "User-Agent": self.headers["User-Agent"],
            "Content-Type": "application/x-www-form-urlencoded",
        }

        async with self.session.post(
            url,
            params=params,
            headers=headers,
            data=""
        ) as resp:
            data = await resp.json()

        return self.__check_response(data)

    # async def entryRoom(self, room_id: int, up_id: int):
    #     data = {
    #         "access_key": self.u.access_key,
    #         "actionKey": "appkey",
    #         "appkey": Crypto.APPKEY,
    #         "ts": int(time.time()),
    #         'platform': 'android',
    #         'uuid': self.u.uuids[0],
    #         'buvid': randomString(37).upper(),
    #         'seq_id': '1',
    #         'room_id': f'{room_id}',
    #         'parent_id': '6',
    #         'area_id': '283',
    #         'timestamp': f'{int(time.time())-60}',
    #         'secret_key': 'axoaadsffcazxksectbbb',
    #         'watch_time': '60',
    #         'up_id': f'{up_id}',
    #         'up_level': '40',
    #         'jump_from': '30000',
    #         'gu_id': randomString(43).lower(),
    #         'visit_id': randomString(32).lower(),
    #         'click_id': self.u.uuids[1],
    #         'heart_beat': '[]',
    #         'client_ts': f'{int(time.time())}'
    #     }
    #     url = "http://live-trace.bilibili.com/xlive/data-interface/v1/heartbeat/mobileEntry"
    #     return await self.__post(url, data=SingableDict(data).signed, headers=self.headers.update({
    #         "Content-Type": "application/x-www-form-urlencoded",
    #     }))    

    async def wearMedal(self, medal_id: int):
        """
        佩戴粉丝牌
        """
        url = "https://api.live.bilibili.com/xlive/app-ucenter/v1/fansMedal/wear"
        data = {
            "access_key": self.u.access_key,
            "actionKey": "appkey",
            "appkey": Crypto.APPKEY,
            "ts": int(time.time()),
            "medal_id": medal_id,
            "platform": "android",
            "type": "1",
            "version": "0",
        }
        self.headers.update(
            {
                "Content-Type": "application/x-www-form-urlencoded",
            }
        ),
        return await self.__post(url, data=SingableDict(data).signed, headers=self.headers)
    
    

#     async def shareRoom(self, room_id: int):
#         """
#         分享直播间
#         """
#         url = "https://api.live.bilibili.com/xlive/app-room/v1/index/TrigerInteract"
#         data = {
#             "access_key": self.u.access_key,
#             "actionKey": "appkey",
#             "appkey": Crypto.APPKEY,
#             "ts": int(time.time()),
#             "interact_type": 3,
#             "roomid": room_id,
#         }
#         self.headers.update(
#             {
#                 "Content-Type": "application/x-www-form-urlencoded",
#             }
#         ),
#         # for _ in range(5):
#         await self.__post(url, data=SingableDict(data).signed, headers=self.headers)
#         # await asyncio.sleep(self.u.config['SHARE_CD'] if not self.u.config['ASYNC'] else 5)

#     async def likeInteract(self, room_id: int):
#         """
#         点赞直播间
#         """
#         url = "https://api.live.bilibili.com/xlive/web-ucenter/v1/interact/likeInteract"
#         data = {
#             "access_key": self.u.access_key,
#             "actionKey": "appkey",
#             "appkey": Crypto.APPKEY,
#             "click_time": 1,
#             "roomid": room_id,
#         }
#         self.headers.update(
#             {
#                 "Content-Type": "application/x-www-form-urlencoded",
#             }
#         ),
#         # for _ in range(3):
#         await self.__post(url, data=SingableDict(data).signed, headers=self.headers)
#         # await asyncio.sleep(self.u.config['LIKE_CD'] if not self.u.config['ASYNC'] else 2)
    
#     async def getGroups(self):
#         url = "https://api.vc.bilibili.com/link_group/v1/member/my_groups?build=0&mobi_app=web"
#         params = {
#             "access_key": self.u.access_key,
#             "actionKey": "appkey",
#             "appkey": Crypto.APPKEY,
#             "ts": int(time.time()),
#         }
#         res = await self.__get(url, params=SingableDict(params).signed, headers=self.headers)
#         list = res["list"] if "list" in res else []
#         for group in list:
#             yield group

#     async def signInGroups(self, group_id: int, owner_id: int):
#         url = "https://api.vc.bilibili.com/link_setting/v1/link_setting/sign_in"
#         params = {
#             "access_key": self.u.access_key,
#             "actionKey": "appkey",
#             "appkey": Crypto.APPKEY,
#             "ts": int(time.time()),
#             "group_id": group_id,
#             "owner_id": owner_id,
#         }
#         return await self.__get(url, params=SingableDict(params).signed, headers=self.headers)

#     async def doSign(self):
#         """
#         直播区签到
#         """
#         url = "https://api.live.bilibili.com/rc/v1/Sign/doSign"
#         params = {
#             "access_key": self.u.access_key,
#             "actionKey": "appkey",
#             "appkey": Crypto.APPKEY,
#             "ts": int(time.time()),
#         }
#         return await self.__get(url, params=SingableDict(params).signed, headers=self.headers)

#     async def getUserInfo(self):
#         """
#         用户直播等级
#         """
#         url = "https://api.live.bilibili.com/xlive/app-ucenter/v1/user/get_user_info"
#         params = {
#             "access_key": self.u.access_key,
#             "actionKey": "appkey",
#             "appkey": Crypto.APPKEY,
#             "ts": int(time.time()),
#         }
#         return await self.__get(url, params=SingableDict(params).signed, headers=self.headers)

#     async def getOneBattery(self):
#         url = "https://api.live.bilibili.com/xlive/app-ucenter/v1/userTask/UserTaskReceiveRewards"
#         data = {
#             "access_key": self.u.access_key,
#             "actionKey": "appkey",
#             "appkey": Crypto.APPKEY,
#             "ts": int(time.time()),
#         }
#         return await self.__post(url, data=SingableDict(data).signed, headers=self.headers)