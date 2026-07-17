# GPT分析器 项目约定

以后修改这个应用时，直接在本目录工作：

`/Users/lwh/Downloads/GPT成本计算器Project`

不要再改临时工作区，也不要再新建重复项目。打包安装后只保留一个应用：

`/Applications/GPT分析器.app`

## 每次改完必须检查

- 应用展示名、菜单名、打包产物是 `GPT分析器`。
- 账号池分析 Tab 能识别 `PLUS共享号池` 和 `K12共享号池` 截图。
- PLUS 和 K12 历史独立保存、独立对比，账号池分析页用上下两个表分别展示，不能混在一起算增减。
- 历史表能看到每个时间点的总账号、5h/7d 可调度剩余、并发可用、限流、额度保护、错误、禁用，并在关键列显示较上次的趋势变化；账号池表格数字显示整数。
- 原成本计算功能仍可用，成本计算数据和账号池历史不要混用。
- OCR 图片只从剪贴板读取，不保存原图。
- 账号池历史和成本历史都不限制条数。
- Command+Q 必须能退出应用。
- 打包后清理旧安装，避免 Launchpad/搜索出现多个重复 app。

## 推荐验证流程

1. 在本目录运行 `./build.sh`。
2. 退出旧应用。
3. 删除 `/Applications/GPT成本计算器.app` 和旧的 `/Applications/GPT分析器.app`，只复制新的 `GPT分析器.app` 过去。
4. 删除本目录 `build`。
5. 用 `find /Applications /Users/lwh/Applications /Users/lwh/Downloads -name 'GPT成本计算器.app' -o -name 'GPT分析器.app' -print` 确认只剩 `/Applications/GPT分析器.app`。
6. 打开新版应用，截图检查 UI。
