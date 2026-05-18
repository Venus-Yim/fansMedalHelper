<p align="center">
  <img src="https://s1.ax1x.com/2022/05/24/XPx1tx.png" width="200" height="200" alt="">
</p>
<div align="center">
<h1> 新 B 站粉丝牌助手
</h1>

<p>适配 B 站 2026.5 更新</p>
<p>当前版本：2.0.7</p>

 </div>

### 功能

-   粉丝团+大航海：
    -   [ ] 每日点赞 300 次直播间 （10 亲密度）
    -   [x] 每日在未开播状态下发送 10 条弹幕 （10 亲密度）
    -   [x] 每日观看 150 分钟 （每 15 分钟 1 共 10 亲密度）
    -   实际每日每个粉丝牌可获取的免费亲密度最高为：(10+10+10)*(1或1.5)=30或45
-    超能粉丝节自动签到
-    多账号支持
-    微信推送通知
-    多平台推送通知（可选）


---

### 使用说明

详细文档在这里 👉 [文档](https://Venus-Yim.github.io/fansMedalHelperVersion) 

**请细心阅读**

---

### 问题反馈

-   提 issue
    **提之前请明确问题主题和运行日志**
-   已知问题：
    对于同时使用两个以上账号的用户，超能粉丝节会对部分直播间的签到任务报错[10007:无法签到]，原因推测为代码编写中没有处理好并发导致数据共享，会尽快修复。
    临时解决方案：备份配置文件yaml，删去yaml中其他账号，一次只进行一个账号的打卡。

---

### 鸣谢

以下开源项目为本项目提供了莫大的帮助：

-   感谢 XiaoMiku01 的粉丝牌助手 [XiaoMiku01/fansMedalHelper](https://github.com/XiaoMiku01/fansMedalHelper)
-   感谢 银弹 的 推送库 [y1ndan/onepush](https://github.com/y1ndan/onepush)
-   此脚本的 Go 语言实现版本 [ThreeCatsLoveFish/MedalHelper](https://github.com/ThreeCatsLoveFish/MedalHelper)
-   AW 的 B 站挂机助手 [andywang425/BLTH](https://github.com/andywang425/BLTH)
