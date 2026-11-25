# 一条龙驱动盘管理

当前版本提供按账号保存的库存预览。在一条龙插件管理页导入 Release 中的 ZIP，点「驱动盘管理」的设置。安装仓库扫描器后可导入当前账号的 ZOD 扫描，或在配置中启用 run_scanner 让应用生成新扫描。扫描器需开启驱动盘识别，背包总数须与导出数一致。

- 在「规则订阅」点「更新来源」获取角色攻略和套装主词条规则。有本地覆盖的字段可在同一页处理三方冲突。自动同步时沿用最近成功的整版快照。
- 在「仓库预览」选中一张盘可调整同类额外备用数或手动保护。按用途区分暴击、暴伤时，在配置的 reserve_rules 中填写 purpose、stats 和 count。count 为 0 表示零张，null 表示不限。
- 在「配队与配置」填写配装器窗口的本机地址 optimizer_url 和所需方案。每项直接使用配装器的请求参数，插件沿用该配队的实战暴击率和预期输出。当前适配 +15 S 盘和已有明确主属性数值的 S 盘。按 profiles 数组依次分配，同一 simultaneous 分组按顺序独占，其他分组可共用盘。任何启用方案缺少结果时其余候选待复核。

示例配置片段，角色参数应来自自己的配装器方案。

~~~json
{
  "reserve": 2,
  "reserve_rules": [{"set":"WoodpeckerElectro","slot":"4","main":"crit_dmg_","purpose":"偏暴击","stats":["crit_"],"count":3}],
  "profiles": [{"name":"常用队 · 主C","agentKey":"Ellen","simultaneous":"日常","tolerancePct":0.5,"request":{"characterName":"艾莲","roleSystem":"ATTACK","characterElement":"ICE","baseATK":1500,"baseCritRate":5,"baseCritDmg":50}}]
}
~~~

两张表订阅源分别是 [角色攻略](https://docs.google.com/spreadsheets/d/e/2PACX-1vTj2PaPq6Py_1B5fsOPj_Moc-tN_7mut7fICczI6lz1njyEIAInTnfB7lAraX4pYCRGNbaHGlIbFZ90/pubhtml) 与 [主词条统计](https://docs.google.com/spreadsheets/d/1ZIZa4zg0zkkPIP2TrHQaMw9XzjJLvO2iOpUAyREDFwY/edit?gid=133245023)。攻略中以图片表示的套装和含条件的词条可在 roster 中通过 sets 和 mainMap 建立个人映射。市场版本更新和游戏内换装、标记、拆解仍待接入。

数据在一条龙安装目录 plugin_data/drive_disc_manager 下按账号分开，版本替换时保留。开发仓库保留 idk500 清理插件 v1.0.6 的基线和 GPL-3.0 许可。配装结果来自 [本地多角色配装器](https://github.com/xliuforge/ZZZ_Multi_Agent_Drive_Optimizer)。验证仅运行 python scripts/smoke.py。使用 python scripts/package_manager.py 生成 ZIP 和 SHA-256。
