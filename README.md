# 美股管理层与大股东增持监测

这个项目可以用 GitHub Actions 或 GitLab CI 在云端每天北京时间 10:00 自动运行，不需要你自己的服务器。

它会：

- 回溯 SEC 最近一个有披露的工作日，处理周末和美国节假日。
- 扫描 SEC EDGAR 的 Form 4，并列出管理层、董事、高管、10% 股东或重要股东集团的主动买入。
- 补充公司市值和中文行业分类。
- 过滤期权、RSU、税款扣缴、计划性出售，以及明显不代表外部净增持的交易。
- 生成中文 Markdown 报告到 `reports/` 目录。

## GitLab 云端运行方式

1. 把本目录里的所有文件推送到 GitLab 仓库根目录。
2. 进入仓库的 `Build -> Pipeline schedules`。
3. 新建 schedule：
   - Description: `Daily US insider buy monitor`
   - Interval Pattern: `0 2 * * *`
   - Cron timezone: `UTC`
   - Target branch: `main`
   - Active: enabled
4. 可选：在 `Settings -> CI/CD -> Variables` 添加 `SEC_USER_AGENT`，值建议写成 `你的名字 你的邮箱`，用于符合 SEC 的访问规范。
5. 运行后在对应 pipeline 的 job artifacts 里下载或查看 `reports/latest.md`。

`0 2 * * *` 是 UTC 02:00，也就是北京时间 10:00。

## GitHub 云端运行方式

1. 在 GitHub 新建一个仓库，可以是 private。
2. 把本目录里的所有文件上传到仓库根目录。
3. 打开仓库的 `Actions` 页面，启用 workflows。
4. 进入 `Settings -> Actions -> General`，确认 `Workflow permissions` 允许 `Read and write permissions`。
5. 可选：在 `Settings -> Secrets and variables -> Actions` 里添加 `SEC_USER_AGENT`，值建议写成 `你的名字 你的邮箱`，用于符合 SEC 的访问规范。

工作流文件是 `.github/workflows/daily-insider-buy.yml`。它每天 UTC 02:00 运行，也就是北京时间 10:00。也可以在 GitHub 的 `Actions` 页面手动点 `Run workflow` 立即运行。

## 本地运行

```bash
python scripts/insider_buy_monitor.py --reports-dir reports
```

脚本只使用 Python 标准库，不需要安装额外依赖。
