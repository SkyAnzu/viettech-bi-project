# VietTech Retail BI Project

## Tổng quan

Đây là project Business Intelligence (BI) mô phỏng quy trình phân tích dữ liệu bán lẻ từ dữ liệu nguồn dạng CSV đến lớp dashboard trên Power BI. Mục tiêu của project là xây dựng một pipeline rõ ràng, có thể chạy lại được, đồng thời tạo ra một Data Mart phù hợp cho việc theo dõi KPI, phân tích sản phẩm, phân khúc khách hàng và vận hành đơn hàng.

Pipeline tổng thể của project:

`Raw CSV -> Python Loader -> BigQuery Staging -> SQL Transform -> Star Schema -> RFM + BI Views -> Power BI`

## Project làm được gì

- Load dữ liệu CSV lên BigQuery staging.
- Tạo Data Mart theo mô hình Star Schema.
- Xây dựng các bảng dimension và fact phục vụ phân tích.
- Tạo bảng `mart_rfm_snapshot` để phân khúc khách hàng theo mô hình RFM.
- Tạo các BI views phục vụ KPI và đối soát nhanh.
- Làm nguồn dữ liệu cho 4 trang dashboard Power BI.

## Các dashboard chính

Project hiện tổ chức dashboard theo 4 góc nhìn:

- Business Performance
- Product
- RFM
- Order

## Cấu trúc thư mục

- `config/`: file cấu hình mẫu cho môi trường chạy.
- `data/raw/`: dữ liệu nguồn dạng CSV, kèm script/schema SQL dùng để dựng dữ liệu OLTP mô phỏng.
- `docs/`: báo cáo project và tài liệu KPI.
- `scripts/`: Python scripts để load dữ liệu và chạy SQL pipeline.
- `sql/`: các file SQL dùng để tạo mart, RFM và BI views.
- `powerbi/`: ảnh dashboard dùng trong báo cáo.

## Cách chạy pipeline

Chạy các lệnh từ thư mục gốc của project.

1. Tạo file `.env` từ `config/.env.example`.
2. Cài dependencies:
   `pip install -r scripts/requirements.txt`
3. Load dữ liệu CSV lên BigQuery staging:
   `python scripts/etl_csv_to_bq.py`
4. Chạy SQL pipeline để tạo Star Schema, RFM và BI views:
   `python scripts/run_sql_pipeline.py`
5. Kết nối Power BI tới dataset `BQ_DATASET_MART`.

## Điều kiện để chạy

- Python 3.10+
- Google Cloud project đã bật BigQuery
- Service account có quyền làm việc với BigQuery

## Kết quả đầu ra

Sau khi chạy pipeline, project tạo ra:

- Staging dataset: `BQ_DATASET_STG`
- Mart dataset: `BQ_DATASET_MART`

Các bảng chính trong mart:

- `dim_date`, `dim_customer`, `dim_product`, `dim_channel`, `dim_payment`
- `fact_orders`, `fact_order_items`, `fact_returns`
- `mart_rfm_snapshot`

Các view chính:

- `vw_monthly_kpi`
- `vw_channel_performance`
- `vw_return_metrics`

## Power BI

Khi dựng dashboard trên Power BI, project sử dụng lớp dữ liệu từ mart thay vì query trực tiếp staging data.

- Dùng relationship theo surrogate key trong mart model.
- Dùng `vw_monthly_kpi` cho KPI tổng quan và xu hướng theo tháng.
- Dùng `vw_return_metrics` cùng `vw_monthly_kpi` cho các chỉ số liên quan đến hoàn trả.
- Dùng `mart_rfm_snapshot` cho trang phân tích khách hàng theo RFM.

## Thứ tự chạy SQL

1. `sql/01_create_staging_dataset.sql`
2. `sql/02_create_star_schema.sql`
3. `sql/03_load_star_schema.sql`
4. `sql/04_rfm_snapshot.sql`
5. `sql/05_bi_views.sql`

## Cấu hình chính

- `PROJECT_ID`: Google Cloud project ID
- `BQ_LOCATION`: BigQuery location, mặc định `asia-southeast1`
- `BQ_DATASET_STG`: tên staging dataset, mặc định `retailbi_stg`
- `BQ_DATASET_MART`: tên mart dataset, mặc định `retailbi_mart`
- `RAW_DATA_DIR`: thư mục chứa raw CSV, mặc định `data/raw`
- `CSV_DELIMITER`: dấu phân cách CSV, mặc định `,`
- `GOOGLE_APPLICATION_CREDENTIALS`: đường dẫn đến service account JSON

## Tài liệu liên quan

- Báo cáo chính: `docs/docs.md`
- KPI reference: `docs/KPI_GLOSSARY.md`
