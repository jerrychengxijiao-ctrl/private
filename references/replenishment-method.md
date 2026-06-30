# Replenishment Method

This is the default S&OP calculation used by `scripts/replenishment_report.py`.

## Inputs

- Demand history: `comfy_sales_info.sales_qty` over the lookback window.
- Current supply: `comfy_sku_inventory_info.saleable_qty`, `in_transit_qty`, and `po_qty`.
- Policy days: `target_stock_days`, `safe_stock_days`, and `max_stock_days` from inventory when available; otherwise CLI defaults.
- Lead time and MOQ: `production_cycle` and `supplier_moq` from purchase orders when available; otherwise CLI defaults.
- Promotion uplift: active or near-future `comfy_sku_promotion_info.plan_discount_count`.
- Aging risk: latest `up_90_qty`, `up_120_qty`, and `up_365_qty` from inventory aging snapshots.
- Profit context: aggregate profit fields from `comfy_profit_info`.

## Formula

Daily sales:

```text
daily_sales = sales_qty_in_lookback / lookback_days
```

Stock position:

```text
stock_position = saleable_qty + in_transit_qty + po_qty
```

Demand horizon:

```text
demand_days = lead_time_days + target_stock_days + safety_stock_days
```

Target stock:

```text
target_stock_qty = daily_sales * demand_days + promo_planned_qty
```

Raw replenishment:

```text
raw_replenishment_qty = max(0, target_stock_qty - stock_position)
```

MOQ rounding:

```text
recommended_qty = ceil(raw_replenishment_qty / supplier_moq) * supplier_moq
```

If MOQ is missing or zero, round raw replenishment up to the next whole unit.

Coverage:

```text
coverage_days = stock_position / daily_sales
```

When `daily_sales` is zero, coverage is undefined and the SKU should be reviewed as a demand-signal issue rather than automatically replenished.

## Priority Labels

- `stockout`: saleable stock is zero while recent demand exists.
- `critical_before_lead_time`: stock position does not cover the estimated lead time.
- `below_safety`: stock position covers lead time but not lead time plus safety stock.
- `replenish`: projected target stock is above stock position.
- `overstock_aging`: aged stock exists and coverage is above maximum stock days.
- `no_recent_sales`: inventory exists but the SKU has no demand signal in the lookback window.
- `no_signal`: no recent sales and no meaningful stock signal.
- `healthy`: no immediate replenishment action from the default formula.

## Interpretation

Use `recommended_qty` as a starting proposal, not an automatic purchase order.

Raise priority when:

- The SKU has strong margin and low coverage.
- A promotion is active or starts inside the planning horizon.
- Logistics delays are high for the SKU or supplier.
- The SKU is strategically important or belongs to a protected launch group.

Lower or pause priority when:

- Aged stock is material.
- Margin is weak or negative.
- Recent demand is missing or appears one-off.
- The recommended quantity exists only because of a large MOQ.

## Common Checks

- Compare recommended orders against supplier capacity and MOQ.
- Review SKUs with `daily_sales = 0` separately.
- Review SKUs with `aged_365_qty > 0` before replenishment.
- Confirm that inventory data is current enough for purchase decisions.
- Verify marketplace and country filters when the business question is not global.
