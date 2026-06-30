#!/usr/bin/env python3
"""Generate a read-only S&OP replenishment report from the Comfy MySQL database."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import getpass
import math
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Dict, Iterable, List, Optional


DEFAULT_HOST = "43.139.154.78"
DEFAULT_PORT = 3306
DEFAULT_USER = "ai_agent_user"
DEFAULT_DB = "ai_agent_test"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create SKU replenishment recommendations for S&OP review."
    )
    parser.add_argument("--host", default=os.getenv("SOP_DB_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.getenv("SOP_DB_PORT", DEFAULT_PORT)))
    parser.add_argument("--user", default=os.getenv("SOP_DB_USER", DEFAULT_USER))
    parser.add_argument("--database", default=os.getenv("SOP_DB_NAME", DEFAULT_DB))
    parser.add_argument(
        "--password-env",
        default="SOP_DB_PASSWORD",
        help="Environment variable containing the MySQL password. Falls back to MYSQL_PWD.",
    )
    parser.add_argument(
        "--password-prompt",
        action="store_true",
        help="Prompt for a password instead of reading an environment variable.",
    )
    parser.add_argument("--as-of-date", help="Planning date in YYYY-MM-DD. Defaults to latest sales date.")
    parser.add_argument("--lookback-days", type=positive_int, default=60)
    parser.add_argument("--target-days", type=nonnegative_int, default=45)
    parser.add_argument("--safety-days", type=nonnegative_int, default=14)
    parser.add_argument("--lead-time-days", type=nonnegative_int, default=30)
    parser.add_argument("--top", type=positive_int, default=30, help="Rows to show in the Markdown action list.")
    parser.add_argument("--limit", type=nonnegative_int, default=0, help="Limit CSV rows; 0 means all rows.")
    parser.add_argument("--sku", action="append", help="Limit to one SKU. Can be passed multiple times.")
    parser.add_argument("--marketplace", help="Filter sales and profit signals by marketplace.")
    parser.add_argument("--country", help="Filter sales and profit signals by country.")
    parser.add_argument(
        "--include-healthy",
        action="store_true",
        help="Include SKUs without a replenishment recommendation in the CSV.",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/sop_replenishment",
        help="Directory for sop_replenishment.csv and sop_replenishment.md.",
    )
    return parser.parse_args()


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def validate_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise SystemExit(f"Invalid --as-of-date {value!r}; expected YYYY-MM-DD.") from exc


def password_from_args(args: argparse.Namespace) -> Optional[str]:
    if args.password_prompt:
        return getpass.getpass("MySQL password: ")
    return os.getenv(args.password_env) or os.getenv("MYSQL_PWD")


def mysql_query(args: argparse.Namespace, sql: str) -> List[Dict[str, str]]:
    password = password_from_args(args)
    if not password:
        raise SystemExit(
            f"Missing password. Set {args.password_env} or MYSQL_PWD, or pass --password-prompt."
        )

    cmd = [
        "mysql",
        "--batch",
        "--raw",
        "--default-character-set=utf8mb4",
        "--protocol=TCP",
        "-h",
        args.host,
        "-P",
        str(args.port),
        "-u",
        args.user,
        "-D",
        args.database,
        "-e",
        sql,
    ]
    env = os.environ.copy()
    env["MYSQL_PWD"] = password
    result = subprocess.run(
        cmd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "mysql command failed")
    if not result.stdout.strip():
        return []
    reader = csv.DictReader(result.stdout.splitlines(), delimiter="\t")
    return list(reader)


def build_sql(args: argparse.Namespace) -> str:
    as_of = validate_date(args.as_of_date)
    as_of_expr = f"DATE({sql_string(as_of)})" if as_of else "NULL"
    lookback_offset = args.lookback_days - 1
    planning_horizon = args.lead_time_days + args.target_days + args.safety_days

    sku_filter = ""
    if args.sku:
        sku_values = ", ".join(sql_string(sku) for sku in args.sku)
        sku_filter = f" AND b.sku_no IN ({sku_values})"

    marketplace_sales_filter = ""
    marketplace_profit_filter = ""
    if args.marketplace:
        marketplace_sales_filter = f" AND s.marketplace = {sql_string(args.marketplace)}"
        marketplace_profit_filter = f" AND pft.marketplace = {sql_string(args.marketplace)}"

    country_sales_filter = ""
    country_profit_filter = ""
    if args.country:
        country_sales_filter = f" AND s.country = {sql_string(args.country)}"
        country_profit_filter = f" AND pft.country = {sql_string(args.country)}"

    action_filter = "" if args.include_healthy else "WHERE recommended_qty > 0 OR priority <> 'healthy'"
    limit_clause = f"LIMIT {args.limit}" if args.limit else ""

    return f"""
WITH
params AS (
  SELECT COALESCE({as_of_expr}, (SELECT MAX(sales_date) FROM comfy_sales_info), CURDATE()) AS as_of_date
),
base AS (
  SELECT sku_no FROM comfy_sku_info WHERE sku_no IS NOT NULL
  UNION SELECT sku_no FROM comfy_sku_inventory_info WHERE sku_no IS NOT NULL
  UNION SELECT sku_no FROM comfy_sales_info WHERE sku_no IS NOT NULL
),
sales_window AS (
  SELECT
    s.sku_no,
    SUM(COALESCE(s.sales_qty, 0)) AS sales_qty,
    SUM(COALESCE(s.sales_amount, 0)) AS sales_amount,
    COUNT(DISTINCT s.platform_order_no) AS order_count,
    MIN(s.sales_date) AS first_sales_date,
    MAX(s.sales_date) AS last_sales_date
  FROM comfy_sales_info s
  CROSS JOIN params prm
  WHERE s.sales_date BETWEEN DATE_SUB(prm.as_of_date, INTERVAL {lookback_offset} DAY) AND prm.as_of_date
    {marketplace_sales_filter}
    {country_sales_filter}
  GROUP BY s.sku_no
),
sales AS (
  SELECT
    sku_no,
    sales_qty,
    sales_amount,
    order_count,
    first_sales_date,
    last_sales_date,
    sales_qty / {args.lookback_days} AS daily_sales
  FROM sales_window
),
inventory AS (
  SELECT
    sku_no,
    SUM(COALESCE(inventory_qty, 0)) AS inventory_qty,
    SUM(COALESCE(saleable_qty, 0)) AS saleable_qty,
    SUM(COALESCE(unsellable_qty, 0)) AS unsellable_qty,
    SUM(COALESCE(in_transit_qty, 0)) AS in_transit_qty,
    SUM(COALESCE(po_qty, 0)) AS po_qty,
    SUM(COALESCE(inventory_amount, 0)) AS inventory_amount,
    MAX(NULLIF(target_stock_days, 0)) AS target_stock_days,
    MAX(NULLIF(safe_stock_days, 0)) AS safe_stock_days,
    MAX(NULLIF(max_stock_days, 0)) AS max_stock_days,
    COUNT(DISTINCT physical_warehouse_name) AS warehouse_count,
    GROUP_CONCAT(DISTINCT physical_warehouse_name ORDER BY physical_warehouse_name SEPARATOR '; ') AS warehouses
  FROM comfy_sku_inventory_info
  GROUP BY sku_no
),
purchase AS (
  SELECT
    sku_no,
    MAX(supplier_name) AS supplier_name,
    MAX(NULLIF(supplier_moq, 0)) AS supplier_moq,
    MAX(NULLIF(production_cycle, 0)) AS production_cycle,
    MAX(expected_delivery_date) AS latest_expected_delivery_date,
    SUM(COALESCE(purchase_qty, 0)) AS historical_purchase_qty,
    SUM(COALESCE(purchase_amount, 0)) AS historical_purchase_amount
  FROM comfy_purchase_order_info
  GROUP BY sku_no
),
promotion AS (
  SELECT
    prm2.sku_no,
    SUM(COALESCE(prm2.plan_discount_count, 0)) AS promo_planned_qty,
    SUM(COALESCE(prm2.plan_discount_amount, 0)) AS promo_discount_amount,
    SUM(COALESCE(prm2.estimated_increment, 0)) AS promo_estimated_increment,
    MIN(prm2.promotion_start_date) AS next_promotion_start_date,
    MAX(prm2.promotion_end_date) AS next_promotion_end_date
  FROM comfy_sku_promotion_info prm2
  CROSS JOIN params p
  WHERE prm2.promotion_start_date <= DATE_ADD(p.as_of_date, INTERVAL {planning_horizon} DAY)
    AND prm2.promotion_end_date >= p.as_of_date
  GROUP BY prm2.sku_no
),
latest_aging AS (
  SELECT sku_no, MAX(snapshot_date) AS snapshot_date
  FROM comfy_inventory_aging_snapshot_info
  GROUP BY sku_no
),
aging AS (
  SELECT
    a.sku_no,
    MAX(a.snapshot_date) AS aging_snapshot_date,
    SUM(COALESCE(a.up_90_qty, 0)) AS aged_90_qty,
    SUM(COALESCE(a.up_120_qty, 0)) AS aged_120_qty,
    SUM(COALESCE(a.up_365_qty, 0)) AS aged_365_qty
  FROM comfy_inventory_aging_snapshot_info a
  JOIN latest_aging la
    ON la.sku_no = a.sku_no AND la.snapshot_date = a.snapshot_date
  GROUP BY a.sku_no
),
profit_window AS (
  SELECT
    pft.sku_no,
    SUM(COALESCE(pft.net_sales_amount, 0)) AS net_sales_amount,
    SUM(COALESCE(pft.gross_profit_amount, 0)) AS gross_profit_amount,
    SUM(COALESCE(pft.marketplace_profit, 0)) AS marketplace_profit,
    AVG(NULLIF(pft.gross_profit_rate, 0)) AS avg_gross_profit_rate,
    AVG(NULLIF(pft.marketplace_profit_rate, 0)) AS avg_marketplace_profit_rate
  FROM comfy_profit_info pft
  JOIN comfy_sales_info s2
    ON s2.platform_order_no = pft.platform_order_no
   AND s2.sku_no = pft.sku_no
  CROSS JOIN params prm
  WHERE s2.sales_date BETWEEN DATE_SUB(prm.as_of_date, INTERVAL {lookback_offset} DAY) AND prm.as_of_date
    {marketplace_profit_filter}
    {country_profit_filter}
  GROUP BY pft.sku_no
),
calc AS (
  SELECT
    (SELECT as_of_date FROM params) AS as_of_date,
    b.sku_no,
    sku.sku_name,
    sku.spu_no,
    sku.brand_name,
    COALESCE(sku.supplier_name, pur.supplier_name) AS supplier_name,
    sku.category_name,
    COALESCE(s.sales_qty, 0) AS sales_qty,
    COALESCE(s.sales_amount, 0) AS sales_amount,
    COALESCE(s.order_count, 0) AS order_count,
    s.first_sales_date,
    s.last_sales_date,
    COALESCE(s.daily_sales, 0) AS daily_sales,
    COALESCE(inv.inventory_qty, 0) AS inventory_qty,
    COALESCE(inv.saleable_qty, 0) AS saleable_qty,
    COALESCE(inv.unsellable_qty, 0) AS unsellable_qty,
    COALESCE(inv.in_transit_qty, 0) AS in_transit_qty,
    COALESCE(inv.po_qty, 0) AS po_qty,
    COALESCE(inv.saleable_qty, 0) + COALESCE(inv.in_transit_qty, 0) + COALESCE(inv.po_qty, 0) AS stock_position_qty,
    COALESCE(inv.inventory_amount, 0) AS inventory_amount,
    COALESCE(inv.target_stock_days, {args.target_days}) AS target_days,
    COALESCE(inv.safe_stock_days, {args.safety_days}) AS safety_days,
    COALESCE(inv.max_stock_days, {args.target_days + args.safety_days + args.lead_time_days}) AS max_stock_days,
    COALESCE(pur.production_cycle, {args.lead_time_days}) AS lead_time_days,
    COALESCE(pur.supplier_moq, 0) AS supplier_moq,
    pur.latest_expected_delivery_date,
    COALESCE(promo.promo_planned_qty, 0) AS promo_planned_qty,
    COALESCE(promo.promo_discount_amount, 0) AS promo_discount_amount,
    COALESCE(promo.promo_estimated_increment, 0) AS promo_estimated_increment,
    promo.next_promotion_start_date,
    promo.next_promotion_end_date,
    aging.aging_snapshot_date,
    COALESCE(aging.aged_90_qty, 0) AS aged_90_qty,
    COALESCE(aging.aged_120_qty, 0) AS aged_120_qty,
    COALESCE(aging.aged_365_qty, 0) AS aged_365_qty,
    COALESCE(profit.net_sales_amount, 0) AS net_sales_amount,
    COALESCE(profit.gross_profit_amount, 0) AS gross_profit_amount,
    COALESCE(profit.marketplace_profit, 0) AS marketplace_profit,
    profit.avg_gross_profit_rate,
    profit.avg_marketplace_profit_rate,
    inv.warehouse_count,
    inv.warehouses
  FROM base b
  LEFT JOIN comfy_sku_info sku ON sku.sku_no = b.sku_no
  LEFT JOIN sales s ON s.sku_no = b.sku_no
  LEFT JOIN inventory inv ON inv.sku_no = b.sku_no
  LEFT JOIN purchase pur ON pur.sku_no = b.sku_no
  LEFT JOIN promotion promo ON promo.sku_no = b.sku_no
  LEFT JOIN aging ON aging.sku_no = b.sku_no
  LEFT JOIN profit_window profit ON profit.sku_no = b.sku_no
  WHERE b.sku_no IS NOT NULL
    {sku_filter}
),
rec AS (
  SELECT
    calc.*,
    CASE WHEN daily_sales > 0 THEN stock_position_qty / daily_sales ELSE NULL END AS coverage_days,
    lead_time_days + target_days + safety_days AS demand_days,
    daily_sales * (lead_time_days + target_days + safety_days) + promo_planned_qty AS target_stock_qty,
    GREATEST(0, CEIL(daily_sales * (lead_time_days + target_days + safety_days) + promo_planned_qty - stock_position_qty)) AS raw_replenishment_qty
  FROM calc
),
prioritized AS (
  SELECT
    rec.*,
    CASE
      WHEN daily_sales > 0 AND saleable_qty <= 0 THEN 'stockout'
      WHEN daily_sales > 0 AND coverage_days < lead_time_days THEN 'critical_before_lead_time'
      WHEN daily_sales > 0 AND coverage_days < lead_time_days + safety_days THEN 'below_safety'
      WHEN daily_sales > 0 AND aged_365_qty > 0 AND coverage_days > max_stock_days THEN 'overstock_aging'
      WHEN raw_replenishment_qty > 0 THEN 'replenish'
      WHEN daily_sales = 0 AND inventory_qty > 0 THEN 'no_recent_sales'
      WHEN daily_sales = 0 THEN 'no_signal'
      ELSE 'healthy'
    END AS priority
  FROM rec
),
final_rows AS (
  SELECT
    prioritized.*,
    CASE
      WHEN raw_replenishment_qty <= 0 THEN 0
      WHEN supplier_moq > 0 THEN CEIL(raw_replenishment_qty / supplier_moq) * supplier_moq
      ELSE raw_replenishment_qty
    END AS recommended_qty
  FROM prioritized
)
SELECT
  as_of_date,
  sku_no,
  sku_name,
  spu_no,
  brand_name,
  supplier_name,
  category_name,
  priority,
  ROUND(daily_sales, 4) AS daily_sales,
  sales_qty,
  sales_amount,
  order_count,
  first_sales_date,
  last_sales_date,
  inventory_qty,
  saleable_qty,
  unsellable_qty,
  in_transit_qty,
  po_qty,
  stock_position_qty,
  ROUND(coverage_days, 1) AS coverage_days,
  lead_time_days,
  target_days,
  safety_days,
  max_stock_days,
  demand_days,
  ROUND(target_stock_qty, 0) AS target_stock_qty,
  raw_replenishment_qty,
  supplier_moq,
  recommended_qty,
  promo_planned_qty,
  next_promotion_start_date,
  next_promotion_end_date,
  aged_90_qty,
  aged_120_qty,
  aged_365_qty,
  aging_snapshot_date,
  net_sales_amount,
  gross_profit_amount,
  marketplace_profit,
  ROUND(avg_gross_profit_rate, 2) AS avg_gross_profit_rate,
  ROUND(avg_marketplace_profit_rate, 2) AS avg_marketplace_profit_rate,
  warehouse_count,
  warehouses
FROM final_rows
{action_filter}
ORDER BY
  FIELD(priority, 'stockout', 'critical_before_lead_time', 'below_safety', 'replenish', 'overstock_aging', 'no_recent_sales', 'no_signal', 'healthy'),
  recommended_qty DESC,
  daily_sales DESC,
  sku_no
{limit_clause};
"""


def numeric(row: Dict[str, str], key: str, default: float = 0.0) -> float:
    value = row.get(key)
    if value in (None, "", "NULL"):
        return default
    try:
        return float(value)
    except ValueError:
        return default


def write_csv(rows: List[Dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def priority_counts(rows: Iterable[Dict[str, str]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        priority = row.get("priority") or "unknown"
        counts[priority] = counts.get(priority, 0) + 1
    return counts


def fmt_number(value: float) -> str:
    if math.isclose(value, round(value)):
        return f"{int(round(value)):,}"
    return f"{value:,.1f}"


def write_markdown(rows: List[Dict[str, str]], path: Path, args: argparse.Namespace) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    counts = priority_counts(rows)
    total_recommended = sum(numeric(row, "recommended_qty") for row in rows)
    total_sales_qty = sum(numeric(row, "sales_qty") for row in rows)
    action_rows = [row for row in rows if numeric(row, "recommended_qty") > 0]
    top_rows = action_rows[: args.top]
    as_of = rows[0].get("as_of_date") if rows else args.as_of_date or "latest sales date"

    lines = [
        "# S&OP Replenishment Recommendation",
        "",
        f"- As of date: `{as_of}`",
        f"- Lookback days: `{args.lookback_days}`",
        f"- Default lead/target/safety days: `{args.lead_time_days}/{args.target_days}/{args.safety_days}`",
        f"- Rows returned: `{len(rows)}`",
        f"- SKUs with positive recommended quantity: `{len(action_rows)}`",
        f"- Total recommended quantity: `{fmt_number(total_recommended)}`",
        f"- Sales quantity in lookback among returned rows: `{fmt_number(total_sales_qty)}`",
    ]
    if args.marketplace:
        lines.append(f"- Marketplace filter: `{args.marketplace}`")
    if args.country:
        lines.append(f"- Country filter: `{args.country}`")
    if args.sku:
        lines.append(f"- SKU filter: `{', '.join(args.sku)}`")

    lines.extend(["", "## Priority Mix", ""])
    if counts:
        for priority, count in sorted(counts.items(), key=lambda item: item[0]):
            lines.append(f"- `{priority}`: {count}")
    else:
        lines.append("- No rows returned.")

    lines.extend(["", f"## Top {min(args.top, len(top_rows))} Actions", ""])
    if not top_rows:
        lines.append("No positive replenishment recommendations were returned.")
    else:
        lines.append(
            "| Priority | SKU | Name | Daily Sales | Coverage Days | Recommended Qty | MOQ | Aged 365+ | Supplier |"
        )
        lines.append("|---|---|---|---:|---:|---:|---:|---:|---|")
        for row in top_rows:
            name = (row.get("sku_name") or "").replace("|", "/")[:48]
            supplier = (row.get("supplier_name") or "").replace("|", "/")[:32]
            lines.append(
                "| {priority} | `{sku}` | {name} | {daily} | {coverage} | {qty} | {moq} | {aged} | {supplier} |".format(
                    priority=row.get("priority") or "",
                    sku=row.get("sku_no") or "",
                    name=name,
                    daily=fmt_number(numeric(row, "daily_sales")),
                    coverage=row.get("coverage_days") or "",
                    qty=fmt_number(numeric(row, "recommended_qty")),
                    moq=fmt_number(numeric(row, "supplier_moq")),
                    aged=fmt_number(numeric(row, "aged_365_qty")),
                    supplier=supplier,
                )
            )

    lines.extend(
        [
            "",
            "## Review Notes",
            "",
            "- Confirm inventory freshness before converting recommendations into purchase orders.",
            "- Review `overstock_aging`, `no_recent_sales`, and large MOQ-driven recommendations manually.",
            "- Treat promotion quantities as planned unit uplift unless the business owner confirms another interpretation.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def main() -> int:
    args = parse_args()
    sql = build_sql(args)
    rows = mysql_query(args, sql)

    output_dir = Path(args.output_dir)
    csv_path = output_dir / "sop_replenishment.csv"
    md_path = output_dir / "sop_replenishment.md"
    write_csv(rows, csv_path)
    write_markdown(rows, md_path, args)

    print(f"Wrote {csv_path}")
    print(f"Wrote {md_path}")
    print(f"Rows: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
