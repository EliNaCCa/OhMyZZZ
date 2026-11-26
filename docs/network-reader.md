# 网络读取

在一条龙的 **OhMyZZZ → 设置 → 运行设置** 选择「网络读取」。

1. 在玩游戏的桌面点「打开读取器」。首次使用会下载经过 SHA-256 校验的 Windows 版，启动时由 Windows 请求管理员权限。
2. 在读取器里打开 **Live export**，保留角色、驱动盘、音擎和穿戴信息，点 **Start capture**，再登录对应的游戏账号。已经在线时需要重新登录一次。
3. 回到 OhMyZZZ 的「仓库」，点「读取仓库」。刷盘时保持读取器打开，新掉落和强化会同步到下一次读取。切换游戏账号时重新打开读取器。

只计算留盘和配装时，网络读取可以省去翻包。启用自动标记或换装时，插件仍会用当前游戏画面重新扫描和计算，再操作对应的盘。网络导出的属性值由品级、等级和强化次数换算。预览和游戏扫描分别保存。

**每日和每周次数**按一条龙当前账号的刷新日计算。每次任务列表走到 OhMyZZZ 最多运行一轮，成功才计数。比如「每天 1 次、每周 3 次」表示一周最多整理三天。手动读取和手动换装单独运行。

## 它怎么读，有什么风险？

登录时，服务器会把本账号的库存发给游戏客户端。Windows 读取器用 WinDivert 的只收、复制模式旁听这段通信，将已有的驱动盘数据整理成 ZOD。OhMyZZZ 向同一台电脑的读取器取一份清单，在本地计算。读取器需要管理员权限，使用期间驱动会在系统里运行。[所选版本源码](https://github.com/pingwinekZ/zzz_packet_capture_0delay/blob/cbf8efabbc77d187b6fc16c499b9c39b8e018acd/rust/crates/zzz-capture/src/windivert.rs)

游戏规则和词条倍率是公开资料，仓库明细属于登录账号。官方声明允许对影响公平的第三方工具采取封禁等措施，具体检测规则和单个读取器的封号概率没有公开。OCR 同样使用第三方自动化来翻包。[绝区零公平运营声明](https://www.hoyolab.com/article/30585630)

本插件适配了支持实时导出的 **0delay 3.3.1**。作者标明支持游戏 3.3 的国际服 Windows 客户端（OSPRODWin），国服客户端还需实机验证。读取器源码保存在自己的 fork，首次安装下载上游发布包。[读取器 fork](https://github.com/EliNaCCa/OhMyZZZ-Scanner) · [上游发布包](https://github.com/pingwinekZ/zzz_packet_capture_0delay/releases/tag/v3.3.1)
