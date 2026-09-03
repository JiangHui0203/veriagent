"""Shared constants and small helpers for the Olist V0 data foundation."""

from __future__ import annotations

from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_DIR / "datasets" / "olistbr"
DATABASE_NAME = "ecommerce.duckdb"


TABLE_DEFINITIONS: dict[str, dict[str, object]] = {
    "customers": {
        "csv": "olist_customers_dataset.csv",
        "columns": [
            ("customer_id", "VARCHAR"),
            ("customer_unique_id", "VARCHAR"),
            ("customer_zip_code_prefix", "VARCHAR"),
            ("customer_city", "VARCHAR"),
            ("customer_state", "VARCHAR"),
        ],
    },
    "orders": {
        "csv": "olist_orders_dataset.csv",
        "columns": [
            ("order_id", "VARCHAR"),
            ("customer_id", "VARCHAR"),
            ("order_status", "VARCHAR"),
            ("order_purchase_timestamp", "TIMESTAMP"),
            ("order_approved_at", "TIMESTAMP"),
            ("order_delivered_carrier_date", "TIMESTAMP"),
            ("order_delivered_customer_date", "TIMESTAMP"),
            ("order_estimated_delivery_date", "TIMESTAMP"),
        ],
    },
    "order_items": {
        "csv": "olist_order_items_dataset.csv",
        "columns": [
            ("order_id", "VARCHAR"),
            ("order_item_id", "INTEGER"),
            ("product_id", "VARCHAR"),
            ("seller_id", "VARCHAR"),
            ("shipping_limit_date", "TIMESTAMP"),
            ("price", "DECIMAL(18, 2)"),
            ("freight_value", "DECIMAL(18, 2)"),
        ],
    },
    "order_payments": {
        "csv": "olist_order_payments_dataset.csv",
        "columns": [
            ("order_id", "VARCHAR"),
            ("payment_sequential", "INTEGER"),
            ("payment_type", "VARCHAR"),
            ("payment_installments", "INTEGER"),
            ("payment_value", "DECIMAL(18, 2)"),
        ],
    },
    "products": {
        "csv": "olist_products_dataset.csv",
        "columns": [
            ("product_id", "VARCHAR"),
            ("product_category_name", "VARCHAR"),
            ("product_name_lenght", "INTEGER"),
            ("product_description_lenght", "INTEGER"),
            ("product_photos_qty", "INTEGER"),
            ("product_weight_g", "INTEGER"),
            ("product_length_cm", "INTEGER"),
            ("product_height_cm", "INTEGER"),
            ("product_width_cm", "INTEGER"),
        ],
    },
    "category_translation": {
        "csv": "product_category_name_translation.csv",
        "columns": [
            ("product_category_name", "VARCHAR"),
            ("product_category_name_english", "VARCHAR"),
        ],
    },
}


EXPECTED_ROW_COUNTS = {
    "customers": 99_441,
    "orders": 99_441,
    "order_items": 112_650,
    "order_payments": 103_886,
    "products": 32_951,
    # The source has 71 parsed data records; its final row has no trailing newline.
    "category_translation": 71,
}


def validate_source_files(data_dir: Path) -> None:
    missing = [
        str(data_dir / definition["csv"])
        for definition in TABLE_DEFINITIONS.values()
        if not (data_dir / str(definition["csv"])).is_file()
    ]
    if missing:
        raise FileNotFoundError("Missing required Olist CSV files: " + ", ".join(missing))


def sql_path(path: Path) -> str:
    """Return a path safely quoted for a DuckDB SQL string literal."""

    return str(path.resolve()).replace("'", "''")
