# Pull Request

## 改了什么

<!-- 一两句话说明；关联 issue 请写 "Closes #123" -->

## 自查清单

- [ ] `python tests/run_all.py --strict` 全部通过（本地 + 没有跳过）
- [ ] 没有引入第三方 Python 依赖（`requirements.txt` 仍为空）
- [ ] 新增/修改的文本文件是 UTF-8 无 BOM（`.ps1` 保持纯 ASCII）
- [ ] 没有把 `web/config.json`、`saves/`、`models/`、`logs/` 之类的运行时数据提交进来
- [ ] 改动涉及 API / 配置 / 脚本时，同步更新了 `docs/` 与 `CHANGELOG.md`
- [ ] 安全相关改动（鉴权、Host/Origin 校验、CSP、文件写入）已说明影响

## 测试方式

<!-- 贴关键输出：python tests/run_all.py 的 SUMMARY 行 -->
