"""文件管理器后端(原 web-pack 的 fm/backend,改造为包)。

改造点只有两处,功能一行未动:
- 原来的扁平 `import config` / `import pathutil` 依赖 sys.path 注入,与 mafagent
  根目录的 config.py 抢 sys.modules["config"];现在是包内相对导入。
- 原 config.py 改名 settings.py(同上,避免模块名冲突)。

根目录来自项目根 appconfig(读 config.json 的 fm_root),settings.py 在 import 时
直接加载,不再经环境变量。
"""
