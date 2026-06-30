#!/usr/bin/env python3
"""Store a sales forecast detail worksheet into a DuckDB database."""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path
import re
import sys
from typing import Optional

import duckdb
import pandas as pd


DEFAULT_SHEET = "预测明细"
DEFAULT_TABLE = "sales_forecast_detail"
DEFAULT_BATCH_TABLE = "sales_forecast_batch"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Append a forecast detail sheet from an .xlsx workbook to a DuckDB database."
    )
    parser.add_argument("--xlsx", required=True, help="Forecast workbook path.")
    parser.add_argument(
        "--duckdb",
        default="outputs/sales_forecast.duckdb",
        help="DuckDB database path. Defaults to outputs/sales_forecast.duckdb.",
    )
    parser.add_argument("--sheet", default=DEFAULT_SHEET, help="Worksheet name to import.")
    parser.add_argument(
        "--table",
        default=DEFAULT_TABLE,
        help=f"Detail table name. Defaults to {DEFAULT_TABLE}.",
    )
    parser.add_argument(
        "--batch-table",
        default=DEFAULT_BATCH_TABLE,
        help=f"Batch metadata table name. Defaults to {DEFAULT_BATCH_TABLE}.",
    )
    parser.add_argument(
        "--forecast-type",
        default="销售预测",
        help="Forecast type label stored with this batch.",
    )
    parser.add_argument(
        "--batch-id",
        help="Optional batch id. Defaults to <forecast type>_<timestamp>.",
    )
    parser.add_argument(
        "--replace-batch",
        action="store_true",
        help="Delete existing rows for the same batch id before inserting.",
    )
    return parser.parse_args()


def slug(value: str) -> str:
    cleaned = re.sub(r"\s+", "_", value.strip())
    cleaned = re.sub(r"[^\w\-\u4e00-\u9fff]+", "_", cleaned)
    cleaned = cleaned.strip("_")
    return cleaned or "forecast"


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def normalize_cell(value):
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value


def read_detail(xlsx_path: Path, sheet: str) -> pd.DataFrame:
    if not xlsx_path.exists():
        raise FileNotFoundError(f"Workbook not found: {xlsx_path}")
    df = pd.read_excel(xlsx_path, sheet_name=sheet, engine="openpyxl")
    df = df.dropna(how="all")
    if df.empty:
        raise ValueError(f"Sheet {sheet!r} has no detail rows.")
    df.columns = [str(col).strip() for col in df.columns]
    df = df.apply(lambda column: column.map(normalize_cell))
    return df


def create_batch_id(forecast_type: str, now: dt.datetime) -> str:
    return f"{slug(forecast_type)}_{now.strftime('%Y%m%d_%H%M%S')}"


def table_exists(con: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    result = con.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = ?
        """,
        [table_name],
    ).fetchone()
    return bool(result and result[0])


def existing_columns(con: duckdb.DuckDBPyConnection, table_name: str) -> list[str]:
    rows = con.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'main' AND table_name = ?
        ORDER BY ordinal_position
        """,
        [table_name],
    ).fetchall()
    return [row[0] for row in rows]


def ensure_detail_table(
    con: duckdb.DuckDBPyConnection,
    table_name: str,
    batch_table: str,
    view_name: str,
    detail_view: str,
    df_columns: list[str],
) -> None:
    if not table_exists(con, table_name):
        con.execute(
            f"CREATE TABLE {quote_ident(table_name)} AS SELECT * FROM {quote_ident(detail_view)} WHERE 1=0"
        )
    columns = existing_columns(con, table_name)
    missing = [column for column in df_columns if column not in columns]
    for column in missing:
        con.execute(f"ALTER TABLE {quote_ident(table_name)} ADD COLUMN {quote_ident(column)} VARCHAR")
    con.execute(
        f"""
        CREATE OR REPLACE VIEW {quote_ident(view_name)} AS
        SELECT *
        FROM {quote_ident(table_name)}
        WHERE "预测批次ID" = (
          SELECT "预测批次ID"
          FROM {quote_ident(batch_table)}
          ORDER BY "入库时间" DESC
          LIMIT 1
        )
        """
    )


def ensure_batch_table(con: duckdb.DuckDBPyConnection, batch_table: str) -> None:
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {quote_ident(batch_table)} (
          "预测批次ID" VARCHAR PRIMARY KEY,
          "入库时间" TIMESTAMP,
          "预测类型" VARCHAR,
          "来源文件" VARCHAR,
          "来源工作表" VARCHAR,
          "明细行数" BIGINT,
          "备注" VARCHAR
        )
        """
    )


def main() -> int:
    args = parse_args()
    xlsx_path = Path(args.xlsx).resolve()
    db_path = Path(args.duckdb).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    now = dt.datetime.now().replace(microsecond=0)
    batch_id = args.batch_id or create_batch_id(args.forecast_type, now)
    df = read_detail(xlsx_path, args.sheet)

    metadata = {
        "预测批次ID": batch_id,
        "入库时间": now,
        "预测类型": args.forecast_type,
        "来源文件": str(xlsx_path),
        "来源工作表": args.sheet,
    }
    for column, value in reversed(metadata.items()):
        df.insert(0, column, value)

    con = duckdb.connect(str(db_path))
    detail_view = "__forecast_detail_df"
    batch_view = "__forecast_batch_df"
    latest_view = "latest_sales_forecast_detail"
    try:
        ensure_batch_table(con, args.batch_table)
        con.register(detail_view, df)
        ensure_detail_table(con, args.table, args.batch_table, latest_view, detail_view, list(df.columns))

        if args.replace_batch:
            con.execute(
                f"DELETE FROM {quote_ident(args.table)} WHERE \"预测批次ID\" = ?",
                [batch_id],
            )
            con.execute(
                f"DELETE FROM {quote_ident(args.batch_table)} WHERE \"预测批次ID\" = ?",
                [batch_id],
            )

        existing = con.execute(
            f"SELECT COUNT(*) FROM {quote_ident(args.batch_table)} WHERE \"预测批次ID\" = ?",
            [batch_id],
        ).fetchone()[0]
        if existing:
            raise ValueError(
                f"Batch id {batch_id!r} already exists. Pass --replace-batch to overwrite it."
            )

        target_columns = existing_columns(con, args.table)
        select_expr = ", ".join(
            quote_ident(column) if column in df.columns else f"NULL AS {quote_ident(column)}"
            for column in target_columns
        )
        con.execute(
            f"INSERT INTO {quote_ident(args.table)} ({', '.join(quote_ident(c) for c in target_columns)}) "
            f"SELECT {select_expr} FROM {quote_ident(detail_view)}"
        )

        batch_df = pd.DataFrame(
            [
                {
                    "预测批次ID": batch_id,
                    "入库时间": now,
                    "预测类型": args.forecast_type,
                    "来源文件": str(xlsx_path),
                    "来源工作表": args.sheet,
                    "明细行数": len(df),
                    "备注": "由 store_forecast_detail_duckdb.py 自动写入",
                }
            ]
        )
        con.register(batch_view, batch_df)
        con.execute(
            f"INSERT INTO {quote_ident(args.batch_table)} SELECT * FROM {quote_ident(batch_view)}"
        )

        detail_count = con.execute(f"SELECT COUNT(*) FROM {quote_ident(args.table)}").fetchone()[0]
        batch_count = con.execute(f"SELECT COUNT(*) FROM {quote_ident(args.batch_table)}").fetchone()[0]
    finally:
        con.close()

    print(f"DuckDB: {db_path}")
    print(f"Detail table: {args.table}")
    print(f"Batch table: {args.batch_table}")
    print(f"Batch id: {batch_id}")
    print(f"Inserted rows: {len(df)}")
    print(f"Total detail rows: {detail_count}")
    print(f"Total batches: {batch_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
