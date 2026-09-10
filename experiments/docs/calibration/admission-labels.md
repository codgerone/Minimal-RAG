# 人工标签格式与维护入口

唯一人工数据在 [人工标签](../../data/calibration/admission-labels.md)，派生图在 [校准图](../../output/calibration/admission-coverage-chart.html)。

目录迁移已完成；本说明保留在 docs/calibration，CLI 默认参数、数据内导航与本页链接已同步。人工数据与可重建输出分开管理，不复制两份人工真值。

标签暂保留现有 Markdown 格式。人工 admit/reject、样本身份及人工说明是维护依据；candidate/slot 覆盖率等为可复算派生列。校准命令只更新派生数据，不修改人工判断。目录迁移不同时改变标签格式或准入算法。

人工数据应纳入版本管理，不能作为 output 清理；生成图可以重建。方法和结论见 [阈值报告](admission-thresholds.md)。
