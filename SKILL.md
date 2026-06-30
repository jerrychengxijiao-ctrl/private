---
name: s-op
description: "基于 Comfy MySQL 数据库生成 S&OP 智能补货和销售预测建议。仅当用户要求进行补货计划、S&OP 计划、销售小组+SKU 维度补货测算、销售预测、未来 6 个月月度预测/补货建议、库存覆盖/缺货风险/安全库存评估，或要求输出中文字段的 .xlsx 预测/补货建议表时使用。使用历史销量、当前库存、当前动销、补货周期、安全库存、可售库存、在途、采购未入库、生产周期、MOQ、促销计划和库龄风险等数据。每次生成销售预测时，必须把最终 Excel 的预测明细追加保存到 DuckDB 数据库。不要用于普通数据库表结构介绍、临时 SQL 查询、单纯利润分析、BI 可视化、非补货类经营分析、写入生产数据库、修改生产数据库、创建前端应用或不需要 Comfy ai_agent_test 数据库的任务。"
---

# S&OP 智能补货

## 能力概述

使用这个 skill，把 Comfy 数据库中的销售、库存、库龄、采购、物流、促销和利润数据，转化为可供 S&OP 会议评审的智能补货建议。

默认只进行只读查询和报表生成。不要写入生产数据库表，不要把数据库密码保存到 skill 文件、参考资料或输出报告中。

## 触发条件

当用户的需求符合以下任一场景时，使用这个 skill：

- 用户明确提到 `S&OP`、`智能补货`、`补货建议`、`补货计划`、`采购建议`、`库存覆盖`、`缺货风险`、`安全库存`、`未来 6 个月补货`。
- 用户要求基于数据库生成“销售小组 + SKU”维度的补货建议。
- 用户要求按月输出未来补货数量、月度补货计划或未来 6 个月建议补货量。
- 用户要求生成销售预测、月度销售预测、备货等级产品预测，或要求保存预测明细历史。
- 用户要求结合历史销量、当前动销、当前库存、可售库存、在途库存、采购未入库、安全库存、补货周期、生产周期、MOQ、促销计划或库龄风险进行补货判断。
- 用户要求生成 `.xlsx` 文件作为最终成果，并且字段需要使用中文。
- 用户没有明确说 `S&OP`，但问题本质是在问“哪些 SKU 要补、补多少、何时补、为什么补”。

## 禁止触发条件

当用户的需求属于以下场景时，不要使用这个 skill，除非用户明确要求把结果用于补货计划：

- 只是介绍数据库表结构、字段含义、表关系或索引。
- 只是执行一次临时 SQL 查询、数据抽取、数据清洗或排查连接问题。
- 只是分析销售额、利润、毛利率、广告费、订单数、店铺表现或平台表现，没有补货决策目标。
- 只是做 BI 看板、前端页面、图表、自动化脚本或数据库维护。
- 需要写入、更新、删除、建表、改表或修改数据库权限。
- 用户要求输出 PDF、PPT、网页、接口服务或非补货类文档。
- 用户使用的不是 Comfy `ai_agent_test` 数据库，且没有提供可映射到本 skill 表结构的数据。
- 用户只问通用供应链理论、S&OP 概念解释或算法讨论，不需要实际数据库测算。

如果触发条件和禁止触发条件同时出现，优先确认用户是否要生成“销售小组 + SKU 的补货建议文件”。确认前不要执行数据库分析。

## 目标成果

正式交付时，优先生成 `.xlsx` 文件。输出粒度为“销售小组 + SKU + 月份”，时间范围默认覆盖未来 6 个月，字段名使用中文。

如果先运行脚本进行试算，可以输出中间 CSV 或 Markdown 摘要；但面向用户的最终成果应转换为中文字段的 Excel 文件。

每次生成销售预测后，必须把 Excel 中的 `预测明细` 工作表追加写入 DuckDB：

- 默认数据库：`outputs/sales_forecast.duckdb`
- 明细表：`sales_forecast_detail`
- 批次表：`sales_forecast_batch`
- 最新批次视图：`latest_sales_forecast_detail`

使用 `scripts/store_forecast_detail_duckdb.py` 执行入库。不要把预测明细只保存在 Excel 中。

## 快速使用

1. 写 SQL 或调整分析口径前，先阅读 `references/database-schema.md`，确认表结构、字段含义和关联方式。
2. 解释或修改补货公式前，先阅读 `references/replenishment-method.md`，确认算法假设。
3. 需要生成标准补货建议时，优先使用 `scripts/replenishment_report.py`。

Linux/macOS 示例：

```bash
export SOP_DB_PASSWORD="..."
python ~/.codex/skills/s-op/scripts/replenishment_report.py --output-dir ./outputs/sop
```

Windows PowerShell 示例：

```powershell
$env:SOP_DB_PASSWORD = "..."
python C:\Users\JERRY\.codex\skills\s-op\scripts\replenishment_report.py --output-dir .\outputs\sop
```

脚本默认连接配置如下，除非命令行参数或环境变量覆盖：

- 主机：`43.139.154.78`
- 端口：`3306`
- 用户：`ai_agent_user`
- 数据库：`ai_agent_test`
- 密码环境变量：优先读取 `SOP_DB_PASSWORD`，其次读取 `MYSQL_PWD`

## 标准工作流

1. 明确计划问题：SKU 范围、平台或国家筛选、计划周期，以及是否包含暂时健康的 SKU。
2. 如果用户关心最新性，先检查销售、库存、库龄等数据的最新日期。
3. 运行标准脚本，生成第一版补货建议。
4. 重点复核异常项：近期无销量、库存为负或缺失、库龄过高、MOQ 过大、交期缺失、毛利偏低或为负。
5. 用业务语言总结动作：立即补货、继续观察、因库龄暂停补货、或因数据缺失需要进一步核查。

## 标准脚本

当用户需要补货清单、S&OP 建议、SKU 库存覆盖分析或采购建议时，使用：

```text
scripts/replenishment_report.py
```

常用参数：

```bash
python ~/.codex/skills/s-op/scripts/replenishment_report.py \
  --lookback-days 60 \
  --target-days 45 \
  --safety-days 14 \
  --lead-time-days 30 \
  --top 30
```

筛选参数：

- `--sku SKU123`：只分析指定 SKU，可重复传入多个 SKU。
- `--marketplace Amazon`：按平台筛选销售和利润信号。
- `--country US`：按国家/站点筛选销售和利润信号。
- `--include-healthy`：输出没有补货建议的健康 SKU。

输出文件：

- `sop_replenishment.csv`：逐 SKU 补货明细。
- `sop_replenishment.md`：管理层摘要和重点行动清单。

## 销售预测明细入库

当生成销售预测 Excel 后，执行：

```powershell
python C:\Users\JERRY\.codex\skills\s-op\scripts\store_forecast_detail_duckdb.py `
  --xlsx .\outputs\销售预测.xlsx `
  --duckdb .\outputs\sales_forecast.duckdb `
  --forecast-type "销售预测"
```

入库规则：

- 只读取 Excel 的 `预测明细` 工作表。
- 每次运行生成一个新的 `预测批次ID`，追加写入 `sales_forecast_detail`。
- 同时在 `sales_forecast_batch` 记录来源文件、入库时间、预测类型和明细行数。
- 如需覆盖同一批次，显式传入 `--batch-id` 和 `--replace-batch`。
- 后续查询最新一次预测，可读取 `latest_sales_forecast_detail` 视图。

## 核心补货判断

把脚本结果当作 S&OP 初稿，而不是自动采购单。

默认判断逻辑：

- 当“交期需求 + 目标库存 + 安全库存”对应的需求量大于当前库存位置时，产生补货建议。
- 库存位置默认等于 `可售库存 + 在途库存 + 采购未入库数量`。
- 当库存覆盖天数低于交期天数，优先级较高。
- 当库存覆盖天数低于 `交期天数 + 安全天数`，视为安全库存不足。
- 当库龄库存较高且覆盖天数超过最大库存天数时，降低补货优先级或建议暂停。
- 近期无销量的 SKU 不自动补货，应视为需求信号不足，需要人工复核。
- MOQ 只在原始建议补货量大于 0 时进行向上取整。
- 当供应受限时，优先考虑毛利、平台利润、缺货风险和促销影响。

## 输出解读

重点关注这些字段：

- `priority`：补货优先级，如 `stockout`、`critical_before_lead_time`、`below_safety`、`replenish`、`overstock_aging`。
- `daily_sales`：近 N 天日均销量。
- `stock_position_qty`：库存位置，包含可售、在途和采购未入库。
- `coverage_days`：库存可覆盖天数。
- `recommended_qty`：建议补货数量。
- `supplier_moq`：供应商 MOQ。
- `aged_90_qty`、`aged_120_qty`、`aged_365_qty`：库龄风险。
- `marketplace_profit`、`avg_marketplace_profit_rate`：利润参考。

## 人工复核建议

生成建议后，至少复核以下问题：

- 数据是否足够新，尤其是库存和最近销售日期。
- 是否存在大额在途或采购未入库，但还没有体现在可售库存中。
- 是否存在 90 天、120 天或 365 天以上库龄库存。
- 建议补货量是否主要由 MOQ 放大。
- 促销计划是否真实有效，`plan_discount_count` 是否可作为计划销量增量。
- 低毛利或负利润 SKU 是否仍有战略补货必要。

## 参考资料

- `references/database-schema.md`：数据库表结构、核心字段、索引和逻辑关联。
- `references/replenishment-method.md`：补货公式、优先级标签、假设和解释口径。

## 后续优化方向

后续优化这个 skill 时，优先考虑以下方向：

- 增加平台、国家、仓库维度的差异化补货策略。
- 增加季节性、促销、断货损失和异常订单的需求修正。
- 增加供应商交期波动、物流延误和采购 MOQ 的更精细约束。
- 增加利润优先级，在供应或现金受限时输出排序建议。
- 增加输出模板，例如 S&OP 周会版、采购执行版、库存风险版。
