# Comfy Database Schema

Use this reference before writing SQL for the Comfy S&OP database.

Connection defaults:

- Host: `43.139.154.78`
- Port: `3306`
- Database: `ai_agent_test`
- User: `ai_agent_user`
- Password: read from `SOP_DB_PASSWORD` or ask the user; do not store it in files.

## Tables

### `comfy_sku_info`

SKU master data. Use as the product dimension.

Important fields: `sku_no`, `sku_name`, `spu_no`, `brand_name`, `supplier_name`, `category_name`, `sku_type`, `purchase_price`, `currency`, `stock_grade_json`, `safe_stock`, `delete_flag`.

Indexed fields: `id`, `sku_no`.

### `comfy_store_info`

Marketplace, country, and store master data.

Important fields: `marketplace`, `country`, `store_name`, `store_status`, `bu_group`, `currency`.

Indexed fields: `id`, `store_name`.

### `comfy_marketplace_sku_info`

Marketplace SKU and listing mapping.

Important fields: `marketplace_sku_no`, `spu_no`, `asin_no`, `listing_url`.

Indexed fields: `id`, `marketplace_sku_no`.

Do not assume `marketplace_sku_no` equals internal `sku_no` unless verified for the requested analysis.

### `comfy_sales_info`

Sales order facts.

Important fields: `platform_order_no`, `sales_date`, `sku_no`, `marketplace`, `store_name`, `country`, `currency`, `sales_qty`, `sales_amount`.

Indexed fields: `id`, `platform_order_no`, `sku_no`.

Use for demand history and sales velocity.

### `comfy_profit_info`

Order and SKU profit facts.

Important fields: `platform_order_no`, `marketplace`, `country`, `sku_no`, `sales_income_gmv`, `net_sales_amount`, `purchase_cost_price`, `gross_profit_amount`, `gross_profit_rate`, `marketplace_profit`, `marketplace_profit_rate`, fulfillment, warehouse, commission, VAT, advertising, and other cost fields.

Indexed fields: `id`, `platform_order_no`, `marketplace`, `country`.

Use for profit-aware replenishment. Join to sales by `platform_order_no` when order-level alignment is required; use `sku_no`, `marketplace`, and `country` for aggregate planning.

### `comfy_sku_inventory_info`

Current SKU inventory by physical warehouse.

Important fields: `sku_no`, `physical_warehouse_name`, `inventory_qty`, `saleable_qty`, `unsellable_qty`, `in_transit_qty`, `po_qty`, `inventory_amount`, `turn_stock_days`, `expected_sale_days`, `safe_stock_days`, `target_stock_days`, `max_stock_days`.

Indexed fields: `id`, `sku_no`, `physical_warehouse_name`.

Use `saleable_qty + in_transit_qty + po_qty` as the default stock position. Do not count `unsellable_qty` as available supply.

### `comfy_inventory_aging_snapshot_info`

Inventory aging snapshots by SKU and physical warehouse.

Important fields: `snapshot_date`, `sku_no`, `physical_warehouse_name`, `inventory_qty`, `up_90_qty`, `up_120_qty`, `up_365_qty`, and aging bucket fields.

Indexed fields: `id`, `snapshot_date`, `sku_no`, `physical_warehouse_name`.

Use the latest snapshot per SKU unless the user asks for a historical aging trend.

### `comfy_shipping_order_info`

Shipping and inbound logistics facts.

Important fields: `shipping_order_no`, `sku_no`, `shipping_qty`, `shipping_date`, `eat_date`, `shipping_status`, `customs_clearance_status`, `shipping_method`, `warehouse_date`, `delay_days`.

Indexed fields: `id`, `shipping_order_no`, `sku_no`.

Use for inbound risk and delay analysis. `eat_date` is stored as text, so validate before date arithmetic.

### `comfy_purchase_order_info`

Purchase and supplier facts.

Important fields: `po_order`, `sku_no`, `purchase_qty`, `purchase_amount`, `po_status`, `supplier_name`, `supplier_delivery_date`, `production_cycle`, `production_capacity`, `supplier_moq`, `expected_delivery_date`, `actual_delivery_date`.

Indexed fields: `id`, `sku_no`.

Use for lead time, MOQ, supplier context, and purchase pipeline. Prefer `comfy_sku_inventory_info.po_qty` for currently open purchase quantity when building the standard replenishment calculation.

### `comfy_sku_promotion_info`

SKU promotion plans.

Important fields: `sku_no`, `promotion_start_date`, `promotion_end_date`, `plan_discount_amount`, `plan_discount_count`, `estimated_increment`.

Indexed fields: `id`, `sku_no`.

Use active or near-future promotions to raise demand requirements.

## Logical Joins

- SKU dimension: join most fact tables on `sku_no`.
- Sales to profit: join on `platform_order_no` for order-level analysis; aggregate by `sku_no`, `marketplace`, and `country` for planning.
- Store dimension: join sales to stores on `store_name`; validate with `marketplace` and `country`.
- Inventory and aging: join on `sku_no` and optionally `physical_warehouse_name`.
- Purchase and logistics: join on `sku_no`; join by shipping or PO number only when a verified mapping exists.

No explicit foreign keys are present. Treat relationships as business keys and validate cardinality for new analyses.

## Table Sizes Observed

- `comfy_sales_info`: about 1.1M rows.
- `comfy_profit_info`: about 1.06M rows.
- `comfy_inventory_aging_snapshot_info`: about 334k rows.
- `comfy_shipping_order_info`: about 189k rows.
- `comfy_marketplace_sku_info`: about 33k rows.
- `comfy_sku_inventory_info`: about 9.5k rows.
- `comfy_sku_info`: about 8k rows.
- `comfy_purchase_order_info`: about 1k rows.
- `comfy_store_info`: about 131 rows.
- `comfy_sku_promotion_info`: about 15 rows.

Use indexed filters for large tables: `sku_no`, `platform_order_no`, `snapshot_date`, `marketplace`, `country`, and `physical_warehouse_name`.
