# Hệ thống Business Intelligence (BI) hỗ trợ ra quyết định trong kinh doanh

## Thông tin dự án

- **Tên dự án:** VietTech BI Project - Retail BI & RFM
- **Đơn vị:** Nhóm 9 - Lớp INT 3202E 2
   1. Nguyễn Khánh Kỳ - 24022675
   2. Nguyễn Quốc Khánh - 24022671
   3. Nguyễn Tuấn Dũng - 24022635
   4. Nguyễn Tiến Huy - 24022663
   5. Mai Tuấn Minh - 24022691
   6. Bùi Minh Quân - 24022711

---

## PHẦN 1: TỔNG QUAN DỰ ÁN

### 1.1. Bối cảnh và vấn đề

> Dự án này mô phỏng bài toán xây dựng hệ thống phân tích dữ liệu (BI) cho lĩnh vực bán lẻ. Trong hoạt động thực tế, các luồng dữ liệu nghiệp vụ cốt lõi – bao gồm thông tin khách hàng, chi tiết đơn hàng, danh mục sản phẩm, kênh phân phối, cũng như lịch sử thanh toán và hoàn trả – thường được sinh ra và lưu trữ rải rác tại các cơ sở dữ liệu quan hệ (Relational Database) của hệ thống vận hành (OLTP).

Tuy nhiên, kiến trúc của các database vận hành được thiết kế chủ yếu để xử lý giao dịch hằng ngày một cách nhanh chóng, do đó không tối ưu cho việc truy xuất và tổng hợp báo cáo (OLAP). Nếu người dùng muốn tính toán các chỉ số như doanh thu, lợi nhuận, tỷ lệ hoàn trả, hoặc phân khúc khách hàng theo mô hình RFM, họ thường phải trích xuất thủ công, gom nhóm từ nhiều bảng/file và tự xử lý logic bằng Excel.

Cách làm này dẫn đến một số vấn đề:

- **Tính nhất quán:** KPI dễ bị sai lệch giữa các người dùng hoặc các báo cáo khác nhau.
- **Kiểm soát logic:** Dashboard khó kiểm soát và duy trì logic nếu kết nối trực tiếp dữ liệu thô.
- **Độ phức tạp:** Các bài toán như phân khúc RFM cần nhiều bước xử lý, dễ sai sót nếu làm thủ công.
- **Cấu trúc dữ liệu:** Thiếu một mô hình dữ liệu chuẩn hóa và rõ ràng trước khi đưa vào Power BI.


### 1.2. Giải pháp đề xuất
Để giải quyết các vấn đề trên, dự án xây dựng một luồng xử lý dữ liệu (Data Pipeline) theo hướng chuẩn hóa: dữ liệu nghiệp vụ được cố định dưới dạng các bản chụp tĩnh (Raw CSV Snapshot) để tách biệt khỏi hệ thống vận hành. Từ lớp dữ liệu này, dữ liệu tiếp tục được tải lên vùng đệm Staging và trải qua quá trình chuẩn hóa, biến đổi để tổ chức lại thành mô hình đa chiều (Star Schema) tại tầng Data Mart trên Google BigQuery. Lớp dữ liệu chuẩn hóa này phục vụ cho dashboard Power BI, giúp thống nhất logic và thuận tiện hơn cho việc trình bày báo cáo.


### 1.3. Phạm vi dự án
Trong phạm vi đồ án, dự án không hướng tới các tính năng thường gặp ở môi trường production như xử lý dữ liệu thời gian thực, tải dữ liệu tăng dần hay tự động hóa theo lịch. Thay vào đó, trọng tâm chính là xây dựng một pipeline dữ liệu rõ ràng, nhất quán về logic nghiệp vụ và thuận tiện cho việc đối soát.

### 1.4. Mục tiêu dự án

| Mục tiêu | Ý nghĩa |
|---|---|
| Tách dữ liệu phân tích khỏi database vận hành | Dữ liệu nghiệp vụ ban đầu được xuất thành raw CSV snapshot, giúp quá trình phân tích không truy vấn trực tiếp vào database gốc. |
| Chuẩn hóa dữ liệu phân tích | Đưa raw CSV vào BigQuery staging và chuyển đổi thành Data Mart dạng Star Schema. |
| Xây dựng mô hình BI rõ ràng | Tách fact và dimension để Power BI dễ tạo relationship, slicer và measure. |
| Thống nhất định nghĩa KPI | Đảm bảo doanh thu, lợi nhuận, chiết khấu, số đơn và các chỉ số hoàn trả được hiểu nhất quán giữa SQL và Power BI |
| Phân tích khách hàng bằng RFM | Tạo `mart_rfm_snapshot` cố định tại ngày `2025-01-01` để phân khúc khách hàng theo Recency, Frequency và Monetary. |
| Hoàn thiện dashboard Power BI | Xây dựng dashboard 4 trang: Business Performance, Product, RFM và Order. |


## PHẦN 2: NỀN TẢNG LÝ THUYẾT VÀ LÝ DO CHỌN CÔNG NGHỆ

### 2.1. Thuật ngữ và khái niệm cốt lõi

Để thuận tiện cho việc theo dõi kiến trúc và logic phân tích của dự án, dưới đây là các khái niệm cốt lõi về kỹ thuật và nghiệp vụ được sử dụng xuyên suốt tài liệu:

#### 2.1.1. Khái niệm về Mô hình Phân tích Khách hàng (RFM)

Nếu Star Schema giúp tổ chức dữ liệu theo hướng thuận tiện cho phân tích, thì mô hình RFM là khung lý thuyết nghiệp vụ cốt lõi nằm trên Data Mart để giải quyết bài toán phân khúc khách hàng. Thay vì chỉ đánh giá người mua qua một con số doanh thu đơn thuần, RFM chấm điểm hành vi khách hàng dựa trên ba thành phần:

| Thành phần | Ý nghĩa | Giá trị diễn giải |
| :--- | :--- | :--- |
| **Recency (R)** | Khoảng thời gian từ lần mua gần nhất đến ngày chốt dữ liệu. | Càng nhỏ càng tốt, thể hiện khách vẫn đang tương tác gần đây. |
| **Frequency (F)** | Số lần mua hàng thành công trong kỳ phân tích. | Càng lớn càng tốt, thể hiện khách mua thường xuyên, độ trung thành cao. |
| **Monetary (M)** | Tổng giá trị chi tiêu (đây là tổng đơn hàng của khách hàng) từ các đơn hoàn tất. | Càng lớn càng tốt, thể hiện khách mang lại giá trị doanh thu cao. |

**Phương pháp tính toán và phân hạng trong dự án:**

Phù hợp với đặc thù dữ liệu tĩnh của dự án, RFM được tính từ các đơn hàng có trạng thái `Completed` và chốt tại một thời điểm snapshot cố định (`2025-01-01`). Việc này giúp đảm bảo tính ổn định của số liệu khi trình bày và đối soát. 

Hệ thống sử dụng phương pháp **phân vị tương đối** để chấm điểm từ `1` đến `5` cho mỗi tiêu chí (5 là tốt nhất, 1 là thấp nhất). Khách hàng được xếp hạng dựa trên sự so sánh với toàn bộ tập dữ liệu nội bộ thay vì một ngưỡng tuyệt đối. Ba điểm số này ghép lại thành mã `rfm_score` (ví dụ: `555`, `412`) và là cơ sở để phân tập khách hàng thành 5 nhóm hành vi quản trị điển hình:

| Nhóm khách hàng | Đặc điểm hành vi điển hình | Ý nghĩa & Ứng dụng quản trị trên BI Dashboard |
| :--- | :--- | :--- |
| **Champions** | Mua gần đây, tần suất cao, tổng chi tiêu lớn (Điểm R, F, M đều cao). | Nhóm giá trị nhất. Đánh giá độ bền vững của doanh thu; áp dụng các ưu đãi đặc quyền để duy trì trải nghiệm. |
| **Loyal** | Mua tương đối đều đặn, đóng góp doanh thu ổn định. | Nhóm trung thành cốt lõi. Theo dõi tỷ trọng để định hướng chiến lược upsell và cross-sell. |
| **At-risk** | Đã lâu chưa mua lại nhưng trước đó có tần suất/giá trị tốt. | Nhóm có nguy cơ rời bỏ. Dashboard giúp phát hiện sớm để triển khai các chiến dịch win-back (nhắc mua lại). |
| **Churned** | Đã lâu không phát sinh giao dịch, tần suất và giá trị thấp. | Phản ánh mức độ suy giảm của tệp khách hàng, hỗ trợ phân tích nguyên nhân để cải thiện vận hành. |
| **Regular** | Hành vi mua ở mức trung bình, chưa nổi bật. | Nhóm tiềm năng. Phân tích để tìm cơ hội chuyển đổi họ thành nhóm Loyal thông qua các chương trình nuôi dưỡng. |

**Ý nghĩa của RFM đối với hệ thống BI:**

Ở góc độ quản trị, đây là một khác biệt quan trọng. Nếu chỉ dựa trên doanh thu thuần, nhà quản lý có thể đánh giá chưa đúng chất lượng tệp khách hàng. RFM giúp trả lời sâu hơn các câu hỏi chiến lược như:

- Doanh thu hiện tại đang đến từ nhóm khách hàng nào?
- Nhóm khách hàng giá trị cao có còn duy trì tương tác hay đang giảm mức độ mua hàng?
- Có bao nhiêu khách hàng đang có nguy cơ rời bỏ để cần được giữ chân sớm?
- Doanh nghiệp nên ưu tiên ngân sách chăm sóc cho nhóm nào để tối ưu hiệu quả kinh doanh?

Để hiện thực hóa các mục tiêu quản trị trên thành những chỉ số đo lường được trên báo cáo, dự án đã gắn kết chặt chẽ nghiệp vụ RFM vào kiến trúc hệ thống dữ liệu. Thay vì để công cụ BI tự xử lý phân tập, toàn bộ logic chấm điểm và phân hạng được tính toán tập trung và đóng gói cố định tại bảng `mart_rfm_snapshot` ở tầng Data Mart. Sự liên kết này giúp Power BI kế thừa dữ liệu phân tích đã được chuẩn hóa, từ đó giúp dashboard hiển thị nhất quán hơn và giảm rủi ro sai lệch khi trình bày phân khúc khách hàng.

---

#### 2.1.2. Khái niệm về Kiến trúc Dữ liệu

- **Mô hình Star Schema (Sơ đồ hình sao)**
    - **Định nghĩa:** Là kiến trúc tổ chức dữ liệu trong kho dữ liệu (Data Warehouse) theo chuẩn của Ralph Kimball. Mô hình bao gồm một bảng sự kiện (Fact Table) đóng vai trò trung tâm và các bảng chiều (Dimension Tables) kết nối trực tiếp xung quanh theo dạng hình sao.
    - **Mục đích:** Giảm số lượng và độ phức tạp của các phép JOIN nhiều tầng trong truy vấn tổng hợp báo cáo (OLAP), đồng thời giúp việc thiết lập các mối quan hệ (Relationships) trên Power BI trở nên trực quan, tường minh.

- **Bảng Fact (Bảng sự kiện) và Bảng Dimension (Bảng chiều)**
    - **Fact Table:** Lưu trữ các số đo định lượng, các chỉ số có thể tính toán được (Measures/Metrics) sinh ra từ hoạt động nghiệp vụ (như doanh thu, số lượng, chiết khấu) và danh sách các khóa ngoại (Foreign Keys) để liên kết với các bảng chiều.
    - **Dimension Table:** Lưu trữ thông tin thuộc tính, mô tả bối cảnh xung quanh sự kiện kinh doanh nhằm phục vụ cho việc lọc và phân nhóm dữ liệu (ví dụ: Khách hàng là ai? Sản phẩm thuộc danh mục nào? Mua qua kênh nào? Vào thời gian nào?).

**Lý do chọn Star Schema trong dự án:**

- **Giảm số lần JOIN khi truy vấn:** Normalized schema (3NF) yêu cầu nhiều bảng join lại để ra một chỉ số đơn giản. Star Schema tổ chức dữ liệu theo fact và các dimension được thiết kế cho mục đích phân tích, giúp truy vấn tổng hợp dễ viết và dễ kiểm soát hơn.
- **Power BI làm việc tốt với mô hình star:** Semantic model trong Power BI được thiết kế để thiết lập relationship theo dạng fact–dimension. Nếu dữ liệu vẫn ở dạng normalized nhiều bảng, việc xây dựng measure và slicer phức tạp hơn đáng kể.
- **Tách biệt rõ "số liệu" và "mô tả":** Fact table chứa các sự kiện đo lường được (doanh thu, số lượng, chiết khấu); dimension table chứa ngữ cảnh để lọc và nhóm (khách hàng, sản phẩm, thời gian). Sự tách biệt này giúp người đọc dashboard hiểu logic dữ liệu dễ hơn.

### 2.2. Công nghệ sử dụng

| Công nghệ | Vai trò trong dự án |
|---|---|
| Python 3.10+ | Điều phối bước load CSV lên BigQuery và chạy SQL pipeline |
| Google BigQuery | Lưu staging data, tạo Data Mart, chạy SQL transform và BI views | 
| Power BI Desktop | Trực quan hóa dữ liệu thành dashboard | 

### 2.3. Vì sao dùng BigQuery?

BigQuery được chọn vì phù hợp với đặc điểm của dự án theo nhiều khía cạnh:

- **Serverless, không cần quản lý hạ tầng:** BigQuery không yêu cầu cài đặt hay vận hành cluster. Điều này giúp tập trung vào logic pipeline thay vì cấu hình server.
- **Hỗ trợ SQL chuẩn và phân tách dataset rõ ràng:** Cho phép tổ chức dữ liệu thành `retailbi_stg` và `retailbi_mart` riêng biệt, tách bạch lớp raw và lớp phân tích.
- **Giảm xử lý phức tạp ở tầng dashboard:** Các phép tổng hợp và biến đổi được đẩy xuống BigQuery, giúp Power BI tập trung vào trực quan hóa thay vì tính toán nặng.
- **Tối ưu truy vấn phân tích:** BigQuery được thiết kế cho truy vấn phân tích kiểu OLAP trên dữ liệu dạng columnar, phù hợp với nhu cầu tổng hợp KPI.

### 2.4. Vì sao dùng Power BI?
Power BI được chọn làm công cụ trực quan hóa dữ liệu (Visualization) ở tầng cuối cùng của pipeline vì phù hợp với kiến trúc và mục tiêu của đồ án:

- **Tương thích tốt với Star Schema:** Lõi Semantic Model của Power BI được thiết kế để làm việc với mô hình Fact-Dimension, giúp thiết lập các relationship dễ dàng và rõ ràng.

- **Tích hợp Native Connector với BigQuery:** Kết nối và đọc thẳng dữ liệu từ dataset retailbi_mart mà không cần xuất qua file trung gian.

- **Tối ưu hiệu năng hiển thị:** Nhờ logic tính toán nặng (như RFM) đã được xử lý bằng SQL dưới BigQuery, Power BI giảm bớt nhu cầu xử lý nhiều DAX phức tạp ở tầng dashboard. Điều này giúp mô hình báo cáo gọn hơn và thuận tiện hơn khi trình bày.

---

## PHẦN 3: KIẾN TRÚC GIẢI PHÁP VÀ LUỒNG XỬ LÝ DỮ LIỆU

Kiến trúc pipeline dữ liệu của dự án được chia thành 5 lớp chính, đi từ nguồn dữ liệu ban đầu đến lớp trực quan hóa trên Power BI.

![Kiến trúc pipeline dữ liệu](stage.jpg)

Ý nghĩa từng lớp:

| Lớp | Vai trò |
|---|---|
| Source Database | Nơi phát sinh và lưu trữ dữ liệu nghiệp vụ ban đầu của hệ thống bán lẻ (Hệ thống OLTP). |
| Local Storage (Raw CSV)| Lưu trữ dữ liệu nguồn dưới dạng Snapshot cố định tại thư mục data/raw/. Bước đệm này giúp ngắt kết nối hoàn toàn với DB nguồn, cô lập hoàn toàn tiến trình phân tích để không gây ảnh hưởng đến hệ thống vận hành.|
| BigQuery Staging | Vùng đệm trên Cloud (dataset retailbi_stg). Dữ liệu thô từ CSV được load lên đây và giữ nguyên cấu trúc gốc để phục vụ cho việc đối soát dữ liệu và chuẩn hóa ở bước sau.|
| Data Mart (Star Schema) | Tầng kho dữ liệu thu nhỏ (dataset retailbi_mart). Tại đây, SQL pipeline sẽ thực hiện biến đổi (Transform), tổ chức dữ liệu thành mô hình Star Schema (Fact/Dimension), đồng thời tính toán sẵn các BI Views và phân khúc khách hàng (RFM Snapshot). |
| Analytics (Power BI) | Tầng trình diễn và trực quan hóa (Visualization). Power BI sử dụng dữ liệu từ Data Mart để xây dựng dashboard quản trị, trực quan hóa KPI, phân khúc khách hàng, hiệu quả sản phẩm và ngoại lệ vận hành đơn hàng. |


### 3.1. Luồng xử lý dữ liệu tổng thể

Nếu nhìn dưới góc độ hệ thống BI, dự án đi theo một luồng xử lý điển hình nhưng được thu gọn cho phù hợp:

1. Dữ liệu nghiệp vụ ban đầu phát sinh ở hệ thống nguồn.
2. Dữ liệu được chụp lại thành raw CSV snapshot để tách rời bước phân tích khỏi hệ thống vận hành.
3. CSV được load lên BigQuery staging để kiểm tra tính đầy đủ và giữ bản sao dữ liệu nguồn trên cloud.
4. Dữ liệu staging được biến đổi thành Star Schema trong Data Mart để phục vụ truy vấn phân tích.
5. Từ Data Mart, hệ thống tiếp tục tạo KPI views và bảng RFM snapshot để Power BI sử dụng làm nguồn trực quan hóa.

Điểm quan trọng ở đây là mỗi lớp dữ liệu có một vai trò riêng: raw để lưu snapshot, staging để đối soát, mart để phân tích, còn Power BI là lớp diễn giải kết quả cho người dùng cuối. Cách tổ chức nhiều lớp như vậy giúp báo cáo có thể giải thích rõ dữ liệu đã được kiểm soát như thế nào trước khi đi vào dashboard.


## PHẦN 4: CÁC GIAI ĐOẠN TRIỂN KHAI

### 4.1. Tổng quan các giai đoạn

| Giai đoạn | Nội dung thực hiện | Kết quả đầu ra |
|---|---|---|
| Giai đoạn 1 | Source Database – Hệ thống OLTP phát sinh dữ liệu nghiệp vụ | Schema `retailbi_oltp` với 8 bảng quan hệ, 21.435 đơn hàng, 5.000 khách hàng |
| Giai đoạn 2 | Chuẩn bị raw CSV snapshot (export từ OLTP) | 8 file CSV cố định trong `data/raw/` |
| Giai đoạn 3 | Load CSV lên BigQuery staging bằng Python ETL | Các bảng `stg_*` trong `retailbi_stg` |
| Giai đoạn 4 | Biến đổi dữ liệu staging thành Star Schema | Các bảng `dim_*` và `fact_*` trong `retailbi_mart` |
| Giai đoạn 5 | Tạo lớp phân tích phục vụ BI | `mart_rfm_snapshot`, `vw_monthly_kpi`, `vw_channel_performance` |
| Giai đoạn 6 | Xây dựng Power BI Dashboard | 4 dashboard hoàn chỉnh: Business Performance, Product, RFM và Order |

### 4.2. Giai đoạn 1 - Source Database (Hệ thống OLTP)

Trước khi dữ liệu được xuất ra CSV và đưa vào pipeline phân tích, toàn bộ nghiệp vụ bán lẻ của VietTech được mô phỏng trong một hệ thống cơ sở dữ liệu quan hệ (RDBMS) theo mô hình OLTP — schema `retailbi_oltp`. Đây là lớp nguồn của dữ liệu nghiệp vụ, nơi các giao dịch như tạo đơn hàng, cập nhật sản phẩm, đăng ký khách hàng và ghi nhận hoàn trả sẽ phát sinh trong bối cảnh vận hành thực tế.

#### Kiến trúc tổng thể của OLTP Schema

Schema `retailbi_oltp` được triển khai trên MySQL với character set `utf8mb4`, bao gồm **8 bảng** tổ chức theo quan hệ chuẩn hóa (3NF) phục vụ cho hoạt động giao dịch:

![ERD OLTP - retailbi_oltp](erd_oltp.png)

*Hình 4.1. Entity Relationship Diagram (ERD) của schema retailbi_oltp*

#### Mô tả chi tiết các bảng

**Nhóm bảng danh mục (Reference/Master Data)**

| Bảng | Vai trò | Trường chính |
|---|---|---|
| `sales_channels` | Lưu thông tin các kênh bán hàng (cửa hàng vật lý, web, app) | `channel_code`, `channel_name`, `channel_type` (Physical/Web/App) |
| `product_categories` | Phân loại sản phẩm theo danh mục và danh mục con | `category`, `sub_category` |
| `products` | Danh mục sản phẩm, bao gồm giá vốn, giá niêm yết, tồn kho | `product_code`, `cost_price`, `msrp`, `quantity_in_stock` |
| `customers` | Thông tin khách hàng: nhân khẩu học, địa lý, phân khúc | `customer_code`, `segment` (Consumer/Corporate/Home Office), `city`, `registered_date` |

**Nhóm bảng giao dịch (Transactional Data)**

| Bảng | Vai trò | Trường chính |
|---|---|---|
| `orders` | Đơn hàng tổng hợp: trạng thái, phương thức thanh toán, kênh, tổng tiền | `order_code`, `status`, `payment_method`, `total_amount`, `net_amount`, `total_cost` |
| `order_details` | Chi tiết từng dòng sản phẩm trong đơn hàng | `quantity`, `unit_price`, `unit_cost`, `discount_rate`, `line_profit` |
| `order_returns` | Lịch sử hoàn trả đơn hàng, lý do, thời điểm | `reason`, `returned_at` |
| `product_price_history` | Lịch sử thay đổi giá MSRP và giá vốn theo thời gian | `price_type`, `old_price`, `new_price`, `changed_at` |

#### Mối quan hệ giữa các bảng

Các bảng trong schema được kết nối thông qua khóa ngoại (Foreign Key) theo mô hình quan hệ chuẩn:

- `orders` → `customers` (N:1): mỗi đơn hàng thuộc một khách hàng
- `orders` → `sales_channels` (N:1): mỗi đơn hàng đến từ một kênh bán
- `order_details` → `orders` (N:1, CASCADE DELETE): chi tiết đơn thuộc đơn hàng
- `order_details` → `products` (N:1): mỗi dòng chi tiết tham chiếu một sản phẩm
- `order_returns` → `orders` (1:1): mỗi đơn hàng tối đa một lần hoàn trả
- `products` → `product_categories` (N:1): mỗi sản phẩm thuộc một danh mục
- `product_price_history` → `products` (N:1): lịch sử giá theo từng sản phẩm

#### Dữ liệu mô phỏng và kịch bản nghiệp vụ

Do không kết nối trực tiếp vào một hệ thống OLTP production thực tế, toàn bộ dữ liệu trong schema `retailbi_oltp` được tạo ra bằng script Python (`generate_data.py`) với các đặc trưng sát thực tế:

| Thực thể | Quy mô |
|---|---|
| Khách hàng (`customers`) | ~5.000 khách hàng |
| Sản phẩm (`products`) | Đa dạng danh mục: Smartphones, Laptops, Tablets, Accessories, Networking, Storage |
| Kênh bán (`sales_channels`) | 8 kênh: 6 cửa hàng vật lý (Hà Nội, Hải Phòng, Huế, Đà Nẵng, HCM, Cần Thơ) + Web + App |
| Đơn hàng (`orders`) | ~21.000 đơn hàng trong giai đoạn 2023–2024 |
| Giao dịch (`order_details`) | Nhiều dòng chi tiết theo từng đơn hàng |

Dữ liệu được tạo với SEED cố định (`SEED = 42`) để đảm bảo tính tái lập (reproducibility), phù hợp với đặc thù snapshot tĩnh của đồ án. Các trạng thái đơn hàng bao gồm: `Pending`, `Processing`, `Shipped`, `Completed`, `Cancelled`, `Returned` — phản ánh các trạng thái chính trong vòng đời đơn hàng của bộ dữ liệu mô phỏng.

#### Vai trò của Source Database trong pipeline

Trong kiến trúc tổng thể của dự án (Phần 3), lớp Source Database đóng vai trò **điểm xuất phát** của toàn bộ luồng dữ liệu. Từ schema `retailbi_oltp`, dữ liệu được export thành 8 file CSV thô tương ứng với 8 bảng nghiệp vụ, sau đó được đưa vào pipeline ELT ở các giai đoạn tiếp theo.


### 4.3. Giai đoạn 2 - Chuẩn bị raw CSV snapshot

Như đã đề cập, toàn bộ dữ liệu đầu vào được thu thập thông qua quá trình kết xuất (export) từ hệ thống cơ sở dữ liệu vận hành. Dữ liệu này sau đó được đóng băng (snapshot) và lưu trữ dưới dạng file CSV thô trong thư mục `data/raw/`. Đây là lớp dữ liệu nguồn dùng chung cho toàn bộ pipeline ELT, giúp nhóm tách biệt phần phân tích khỏi hệ thống vận hành và đảm bảo mọi lần chạy pipeline đều dựa trên cùng một bộ dữ liệu.

Các file nguồn chính gồm:

- `customers.csv`
- `orders.csv`
- `order_details.csv`
- `order_returns.csv`
- `products.csv`
- `product_categories.csv`
- `product_price_history.csv`
- `sales_channels.csv`

Ở lớp này, dữ liệu được giữ gần với cấu trúc gốc nhất có thể. Nhóm không áp dụng biến đổi nghiệp vụ trực tiếp trên file CSV mà dành phần chuẩn hóa và mô hình hóa cho các bước xử lý phía sau trên BigQuery.

### 4.4. Giai đoạn 3 - Load CSV lên BigQuery Staging

Sau khi chuẩn bị xong raw snapshot, dự án sử dụng script `scripts/etl_csv_to_bq.py` để load toàn bộ CSV lên BigQuery staging. Script này đảm nhận các công việc chính sau:

1. Đọc cấu hình từ `config/.env`, bao gồm `PROJECT_ID`, `BQ_LOCATION`, `BQ_DATASET_STG`, `RAW_DATA_DIR` và `CSV_DELIMITER`.
2. Kiểm tra sự tồn tại của thư mục raw data và xác nhận đủ các file CSV bắt buộc trước khi chạy.
3. Tạo dataset staging nếu chưa tồn tại.
4. Lần lượt nạp từng file CSV vào BigQuery với tên bảng theo mẫu `stg_<table_name>`.
5. Ghi đè dữ liệu bằng `WRITE_TRUNCATE` để phù hợp với đặc điểm dataset cố định và cách chạy full load của đồ án.

Việc tách lớp staging có hai ý nghĩa quan trọng. Thứ nhất, đây là vùng đệm giúp kiểm tra row count và đối soát dữ liệu trước khi transform. Thứ hai, dữ liệu staging vẫn giữ cấu trúc gần với nguồn CSV nên thuận tiện cho đối soát khi phát hiện sai lệch ở tầng mart.

Kết quả của giai đoạn này là dataset `retailbi_stg` với các bảng:

- `stg_customers`
- `stg_orders`
- `stg_order_details`
- `stg_order_returns`
- `stg_products`
- `stg_product_categories`
- `stg_product_price_history`
- `stg_sales_channels`

### 4.5. Giai đoạn 4 - Tạo Star Schema và load Data Mart

Sau khi dữ liệu đã có mặt trong staging, dự án sử dụng script `scripts/run_sql_pipeline.py` để chạy tuần tự các file SQL trong thư mục `sql/`. Thứ tự thực thi được cố định như sau:

1. `01_create_staging_dataset.sql`
2. `02_create_star_schema.sql`
3. `03_load_star_schema.sql`
4. `04_rfm_snapshot.sql`
5. `05_bi_views.sql`

Trọng tâm của giai đoạn này là chuyển dữ liệu từ cấu trúc bảng nguồn sang mô hình Star Schema trong dataset `retailbi_mart`.

Các bảng dimension được tạo gồm:

- `dim_date`
- `dim_customer`
- `dim_product`
- `dim_channel`
- `dim_payment`

Các bảng fact được tạo gồm:

- `fact_orders`
- `fact_order_items`
- `fact_returns`

Trong mô hình này, các fact table không còn join trực tiếp bằng khóa tự nhiên của dữ liệu nguồn mà dùng surrogate key để kết nối với dimension. Riêng `fact_order_items` và `fact_returns` vẫn giữ `order_id` cho mục đích đối soát, nhưng đồng thời bổ sung `order_key` để liên kết nhất quán với `fact_orders` ở tầng BI.

#### Một số xử lý kỹ thuật trong quá trình load Star Schema

Trong quá trình nạp dữ liệu từ staging sang Data Mart, nhóm cũng phải xử lý một số tình huống kỹ thuật cụ thể để mô hình Star Schema phản ánh đúng ngữ nghĩa nghiệp vụ và giữ được tính nhất quán khi phân tích.

**Tạo và chuẩn hóa `dim_date`:** Nhóm không để các bảng fact phân tích trực tiếp trên trường ngày gốc, mà chuyển các ngày quan trọng sang `date_key` dạng `YYYYMMDD` để liên kết với bảng `dim_date`. Bảng này tách sẵn các thuộc tính như năm, quý, tháng, tuần, ngày cuối tuần, giai đoạn Tết và cuối năm, giúp Power BI phân tích theo thời gian thuận tiện hơn.

```sql
CAST(FORMAT_DATE('%Y%m%d', d) AS INT64) AS date_key,
EXTRACT(YEAR FROM d) AS year,
EXTRACT(QUARTER FROM d) AS quarter,
EXTRACT(MONTH FROM d) AS month
```

**Chuẩn hóa định dạng dữ liệu khi load mart:** Dữ liệu ở staging được giữ gần với nguồn CSV nên có thể chưa phù hợp trực tiếp cho phân tích. Khi load sang mart, nhóm ép kiểu các trường định danh về `INT64`, các trường tiền tệ về `NUMERIC`, và chuẩn hóa các trường boolean hoặc text để dữ liệu nhất quán hơn trong mô hình phân tích.

```sql
CAST(id AS INT64) AS customer_id,
CAST(p.msrp AS NUMERIC) AS msrp,
LOWER(TRIM(CAST(is_active AS STRING))) IN ('1', 'true') AS is_active
```

**Phân tách đa ngữ nghĩa của `ship_date` rỗng:** Trường `ship_date` có nhiều giá trị `NULL`, nhưng các giá trị này không mang cùng một ý nghĩa. Nhóm không gộp tất cả về một trạng thái chung, mà tách thành ba trường hợp: `-1` cho dữ liệu bất thường hoặc không xác định, `-2` cho các đơn `Pending` hoặc `Processing` chưa giao, và `-3` cho các đơn `Cancelled` không áp dụng ngày giao. Cách làm này giúp các báo cáo vận hành không hiểu sai cùng một giá trị `NULL` theo nhiều nghĩa khác nhau.

```sql
CASE
  WHEN o.ship_date IS NOT NULL THEN CAST(FORMAT_DATE('%Y%m%d', DATE(o.ship_date)) AS INT64)
  WHEN o.ship_date IS NULL AND o.status IN ('Pending', 'Processing') THEN -2
  WHEN o.ship_date IS NULL AND o.status = 'Cancelled' THEN -3
  ELSE -1
END AS ship_date_key
```

**Chuẩn hóa dimension thanh toán trước khi sinh khóa:** Khi tạo `dim_payment`, nếu sinh khóa trực tiếp trên dữ liệu giao dịch thì rất dễ làm bảng dimension bị nhân bản theo từng dòng đơn hàng. Vì vậy, nhóm thực hiện loại bỏ trùng lặp `payment_method` trước, sau đó mới cấp `payment_key`, để bảng dimension giữ đúng vai trò là lớp ngữ cảnh nhỏ gọn và ổn định.

```sql
WITH distinct_payments AS (
  SELECT DISTINCT TRIM(payment_method) AS payment_method
  FROM `your-project-id.retailbi_stg.stg_orders`
)
```

**Lookup surrogate key từ dimension vào fact:** Các bảng fact không giữ quan hệ phân tích bằng khóa tự nhiên của dữ liệu nguồn, mà thực hiện đối chiếu sang các dimension để lấy các trường `*_key` trước khi nạp vào mart. Nhờ đó, các mối liên kết fact-dimension trên Power BI rõ ràng hơn và phù hợp hơn với cách tổ chức semantic model của Star Schema.

```sql
LEFT JOIN `your-project-id.retailbi_mart.dim_customer` dc
  ON CAST(o.customer_id AS INT64) = dc.customer_id
LEFT JOIN `your-project-id.retailbi_mart.dim_channel` dch
  ON CAST(o.channel_id AS INT64) = dch.channel_id
```

Kết quả của giai đoạn này là một Data Mart dạng hình sao, đóng vai trò là nguồn dữ liệu chuẩn hóa cho phân tích và trực quan hóa sau này.

![Mô hình Data Mart dạng Star Schema](mart.png)

*Hình 4.2. Mô hình Data Mart dạng Star Schema*

### 4.6. Giai đoạn 5 - Tạo RFM Snapshot và BI Views

Sau khi Star Schema hoàn tất, dự án tiếp tục tạo lớp phân tích phục vụ báo cáo. Lớp này gồm hai nhóm thành phần chính.

Thứ nhất là bảng `mart_rfm_snapshot`, được tính từ `fact_orders` với điều kiện chỉ lấy các đơn hàng có trạng thái `Completed`. Snapshot date hiện được cố định tại: 2025-01-01


Từ đó, hệ thống tính ba thành phần RFM:

- **Recency:** số ngày kể từ lần mua gần nhất đến ngày snapshot.
- **Frequency:** số lượng đơn hàng completed của khách hàng.
- **Monetary:** tổng giá trị chi tiêu của khách hàng trên các đơn completed.

Sau khi tính ba giá trị gốc này, hệ thống tiếp tục chấm điểm từng tiêu chí bằng phương pháp phân vị tương đối. Cụ thể, mỗi tiêu chí R, F, M được chia thành 5 mức điểm bằng hàm `NTILE(5)`, trong đó điểm cao hơn thể hiện hành vi có giá trị hơn trong phạm vi dataset hiện tại. Với Recency, khách hàng có lần mua gần hơn sẽ được điểm cao hơn; với Frequency và Monetary, khách hàng mua nhiều hơn hoặc chi tiêu cao hơn sẽ được điểm cao hơn.

```sql
NTILE(5) OVER (ORDER BY recency_days DESC) AS r_score,
NTILE(5) OVER (ORDER BY frequency_orders) AS f_score,
NTILE(5) OVER (ORDER BY monetary_value) AS m_score
```

Ba điểm số này sau đó được ghép lại thành `rfm_score`, ví dụ `555` thể hiện nhóm khách hàng có điểm cao ở cả ba tiêu chí. Dựa trên tổ hợp điểm R, F, M, hệ thống gán khách hàng vào các nhóm hành vi như `Champions`, `Loyal`, `At-risk`, `Churned` và `Regular`. Việc phân nhóm được thực hiện sẵn ở tầng mart thay vì tính động trong Power BI để đảm bảo kết quả ổn định và nhất quán khi trình bày dashboard.

```sql
CASE
  WHEN s.r_score >= 4 AND s.f_score >= 4 AND s.m_score >= 4 THEN 'Champions'
  WHEN s.r_score >= 3 AND s.f_score >= 3 THEN 'Loyal'
  WHEN s.r_score <= 2 AND s.f_score >= 3 THEN 'At-risk'
  WHEN s.r_score <= 2 AND s.f_score <= 2 THEN 'Churned'
  ELSE 'Regular'
END AS rfm_segment
```

Thứ hai là các BI views dùng để chuẩn hóa KPI và hỗ trợ đối soát nhanh:

- `vw_monthly_kpi`: tổng hợp KPI theo tháng như tổng số đơn, số đơn hoàn tất, doanh thu đơn hoàn tất, lợi nhuận gộp, biên lợi nhuận gộp, AOV và chiết khấu.
- `vw_channel_performance`: tổng hợp hiệu quả theo kênh bán, gồm số đơn, số đơn hoàn tất và doanh thu đơn hoàn tất theo từng kênh.

Các view này không thay thế fact table trong phân tích chi tiết, nhưng rất hữu ích cho việc kiểm tra định nghĩa KPI, đối chiếu số liệu tổng hợp và làm nguồn tham khảo khi dựng dashboard ở giai đoạn sau.

### 4.7. Tổng kết luồng ELT và ý nghĩa đối với hệ thống BI

Trải qua các giai đoạn từ raw CSV snapshot đến Data Mart, RFM snapshot và BI views, pipeline đã chuyển dữ liệu nguồn thành một lớp dữ liệu phân tích có cấu trúc rõ ràng hơn cho Power BI. Giá trị chính của luồng ELT trong dự án không nằm ở việc xử lý dữ liệu theo thời gian thực, mà ở việc tách từng bước xử lý thành các lớp có thể đối soát và giải thích được.

Cụ thể, pipeline mang lại một số kết quả chính:

- **Tổ chức lại dữ liệu cho phân tích:** Dữ liệu từ các bảng nguồn được đưa lên BigQuery và tái cấu trúc thành Star Schema, giúp Power BI dễ thiết lập relationship, slicer và measure hơn.
- **Chuẩn hóa liên kết giữa các thực thể:** Các bảng fact sử dụng surrogate key để liên kết với dimension, giúp mô hình phân tích rõ ràng hơn so với việc phụ thuộc trực tiếp vào khóa tự nhiên từ nguồn.
- **Thống nhất một phần logic KPI và RFM:** Các định nghĩa quan trọng như completed revenue, completed orders và phân khúc RFM được tổ chức ở tầng mart/BI views, giúp giảm rủi ro mỗi dashboard diễn giải chỉ số theo một cách khác nhau.
- **Giảm bớt xử lý ở tầng trực quan hóa:** Power BI sử dụng dữ liệu đã được mô hình hóa sẵn từ mart, nên tập trung nhiều hơn vào trình bày, lọc và so sánh dữ liệu thay vì phải tái tạo toàn bộ logic transform từ dữ liệu thô.

Như vậy, quá trình ELT đã tạo ra một nền dữ liệu đủ rõ ràng và nhất quán trong phạm vi để phục vụ bước xây dựng dashboard quản trị.

### 4.8. Giai đoạn 6 - Xây dựng Power BI Dashboard

Trên nền dataset `retailbi_mart`, nhóm đã kết nối Power BI Desktop với BigQuery và xây dựng dashboard theo 4 góc nhìn phân tích: Business Performance, Product, RFM và Order. Ở giai đoạn này, dashboard không lấy dữ liệu trực tiếp từ raw hay staging mà sử dụng fact table, dimension table và một số BI views ở tầng mart để đảm bảo KPI được hiển thị nhất quán với logic đã chuẩn hóa trong SQL.

Kết quả của giai đoạn này là lớp trực quan hóa hoàn chỉnh trong phạm vi đồ án. Người dùng có thể theo dõi hiệu quả kinh doanh tổng quan, phân tích danh mục sản phẩm, quan sát phân khúc khách hàng theo RFM và nhận diện các ngoại lệ trong vòng đời đơn hàng trên cùng một hệ thống BI thống nhất.

---

## PHẦN 5: DASHBOARD POWER BI

### 5.1. Vai trò của dashboard

Dashboard Power BI là lớp trình diễn cuối cùng của toàn bộ hệ thống BI. Sau khi dữ liệu đã được chuẩn hóa ở BigQuery thành Data Mart và các KPI đã được tổ chức nhất quán giữa lớp dữ liệu mart, BI views và Power BI, Power BI đóng vai trò chuyển các kết quả đó thành thông tin quản trị dễ quan sát, dễ lọc và dễ so sánh hơn cho người dùng cuối.

Dashboard được xây dựng để trả lời bốn nhóm câu hỏi chính:

- Doanh nghiệp đang vận hành ra sao ở góc nhìn doanh thu, lợi nhuận, đơn hàng và hoàn trả?
- Sản phẩm và kênh bán nào đang đóng góp tốt hơn vào kết quả kinh doanh?
- Tệp khách hàng đang được phân hóa như thế nào theo hành vi mua hàng?
- Những ngoại lệ nào trong trạng thái đơn hàng, hoàn trả và hủy đơn cần được theo dõi?

### 5.2. Cấu trúc dashboard thực tế

Dashboard hiện tại gồm 4 trang chính, tương ứng với 4 nhóm phân tích của hệ thống BI:

| Trang | Câu hỏi kinh doanh chính | KPI / nội dung chính | Nguồn dữ liệu chính |
|---|---|---|---|
| Business Performance Dashboard | Hiệu quả kinh doanh tổng quan đang như thế nào? | Doanh thu đơn hoàn tất, số đơn hoàn tất, giá trị đơn hàng trung bình, tỷ lệ hoàn trả, xu hướng đơn hàng, doanh thu theo kênh, doanh thu theo sản phẩm, biên lợi nhuận gộp theo thương hiệu | `fact_orders`, `fact_order_items`, `dim_date`, `dim_channel`, `dim_product`, `vw_monthly_kpi`, `vw_channel_performance` |
| Product Dashboard | Danh mục sản phẩm nào tạo doanh thu và lợi nhuận tốt hơn? | Doanh thu và lợi nhuận theo danh mục, tốc độ bán sản phẩm, xu hướng doanh thu theo quý, chiết khấu - doanh thu - số lượng theo sản phẩm và thương hiệu | `fact_order_items`, `fact_orders`, `dim_product`, `dim_channel` |
| RFM Dashboard | Khách hàng đang được phân nhóm ra sao theo hành vi mua hàng? | Số khách theo `rfm_segment`, giá trị chi tiêu theo segment, bảng chi tiết khách hàng, độ gần đây, tần suất mua, gợi ý hành động theo segment | `mart_rfm_snapshot`, `dim_customer` |
| Order Dashboard | Những ngoại lệ và đặc điểm vận hành đơn hàng cần theo dõi là gì? | Phương thức thanh toán theo kênh, tổng số đơn theo trạng thái, tỷ lệ hủy theo kênh, tỷ lệ hoàn trả theo ngữ cảnh sản phẩm, chi tiết ngoại lệ đơn hàng | `fact_orders`, `fact_returns`, `fact_order_items`, `dim_channel`, `dim_payment`, `dim_product`, `dim_date` |

Thiết kế này bám sát logic của Data Mart đã xây dựng. Mỗi trang đều có nguồn dữ liệu rõ ràng và bám sát một nhóm câu hỏi kinh doanh cụ thể, tránh việc Power BI phải tự tái hiện lại logic nghiệp vụ từ dữ liệu thô.

### 5.3. Định nghĩa KPI chính trên dashboard

Để tránh hiểu sai ý nghĩa của các KPI trên dashboard, dự án diễn giải trực tiếp các chỉ số cốt lõi như sau:

| KPI | Ý nghĩa | Vai trò trong phân tích |
|---|---|---|
| Doanh thu đơn hoàn tất | Doanh thu từ các đơn hàng đã hoàn tất | Phản ánh kết quả bán hàng thực sự đã được ghi nhận |
| Số đơn hoàn tất | Số lượng đơn hàng đã hoàn tất | Cho biết quy mô giao dịch thành công trong kỳ phân tích |
| Giá trị đơn hàng trung bình (AOV) | Giá trị trung bình của một đơn hàng hoàn tất | Giúp đánh giá mức chi tiêu bình quân của khách hàng trên mỗi đơn |
| Tỷ lệ hoàn trả | Tỷ lệ đơn hàng phát sinh hoàn trả trên tổng số đơn hàng | Giúp đánh giá mức độ hoàn trả và theo dõi rủi ro về chất lượng sản phẩm, trải nghiệm khách hàng hoặc quy trình vận hành |
| Biên lợi nhuận gộp | Tỷ lệ lợi nhuận gộp trên doanh thu | Giúp đánh giá hiệu quả sinh lời tương đối giữa các thương hiệu, nhóm sản phẩm hoặc kênh bán |
| Lợi nhuận | Phần giá trị còn lại sau khi trừ chi phí khỏi doanh thu | Cho biết đóng góp lợi nhuận tuyệt đối của từng nhóm phân tích |

Ở lớp tổng quan của dashboard, các chỉ số doanh thu, số đơn, giá trị đơn hàng trung bình, lợi nhuận và biên lợi nhuận gộp được ưu tiên hiểu theo các đơn hàng ở trạng thái `Completed`. Riêng tỷ lệ hoàn trả được diễn giải theo ngữ cảnh lọc của dashboard.

### 5.4. Business Performance Dashboard

Trang Business Performance Dashboard là trang tổng quan điều hành, tập trung vào việc cho thấy bức tranh kinh doanh chung của doanh nghiệp trên dataset hiện tại. Trang này sử dụng các KPI card ở phần trên để thể hiện nhanh doanh thu đơn hoàn tất, số đơn hoàn tất, giá trị đơn hàng trung bình và tỷ lệ hoàn trả, sau đó bổ sung các biểu đồ xu hướng và cơ cấu để giải thích sâu hơn kết quả tổng quan.

- KPI card: Doanh thu đơn hoàn tất, số đơn hoàn tất, giá trị đơn hàng trung bình, tỷ lệ hoàn trả.
- Biểu đồ xu hướng số đơn theo tháng.
- Biểu đồ donut doanh thu theo loại kênh bán.
- Biểu đồ doanh thu theo sản phẩm.
- Bảng biên lợi nhuận gộp theo từng thương hiệu.

Trong trang này, chỉ số doanh thu đơn hoàn tất và số đơn hoàn tất đều chỉ tính các đơn hàng có trạng thái `Completed`, nhằm tránh ghi nhận doanh thu hoặc số đơn từ các trạng thái chưa hoàn tất hay đã hủy.

![Business Performance Dashboard](../powerbi/dashboard1.jpg)

*Hình 5.1. Business Performance Dashboard*

### 5.5. Product Dashboard

Trang Product Dashboard đi sâu vào hiệu quả kinh doanh ở cấp sản phẩm và danh mục. So với trang tổng quan, trang này hỗ trợ tốt hơn cho việc so sánh giữa các danh mục, theo dõi mối quan hệ giữa chiết khấu và doanh thu, cũng như xác định các sản phẩm có tốc độ bán cao trong kỳ.

- Biểu đồ doanh thu và lợi nhuận theo danh mục.
- Biểu đồ bubble thể hiện chiết khấu, doanh thu và số lượng bán theo sản phẩm và thương hiệu.
- Biểu đồ tốc độ bán sản phẩm theo tháng ở cấp sản phẩm.
- Biểu đồ xu hướng doanh thu theo quý và danh mục.

![Product Dashboard](../powerbi/dashboard2.jpg)

*Hình 5.2. Product Dashboard*

### 5.6. RFM Dashboard

Trang RFM Dashboard là lớp phân tích khách hàng chuyên biệt của hệ thống. Trang này dùng trực tiếp `mart_rfm_snapshot` để thể hiện phân khúc hành vi khách hàng, thay vì tính RFM động trong Power BI. Nhờ đó, logic phân nhóm được cố định và nhất quán với phần mô hình hóa đã trình bày ở các phần trước.

- Treemap thể hiện tổng số khách hàng theo `rfm_segment`.
- Biểu đồ cột thể hiện giá trị chi tiêu theo từng segment.
- Bảng chi tiết khách hàng gồm thành phố, `rfm_segment`, `monetary_value`, `recency_days`, `frequency_orders` và gợi ý hành động theo segment.

Trang này đặc biệt hữu ích khi trình bày ý nghĩa ứng dụng của RFM trong BI, vì nó cho thấy không chỉ khách hàng được chia thành nhóm nào mà còn gợi mở cách doanh nghiệp có thể hành động với từng nhóm khách hàng cụ thể. Phần gợi ý hành động được thể hiện ở lớp dashboard dựa trên phân khúc RFM, thay vì là một trường nghiệp vụ có sẵn trong bảng mart.

![RFM Dashboard](../powerbi/dashboard3.jpg)

*Hình 5.3. RFM Dashboard*

### 5.7. Order Dashboard

Trang Order Dashboard tập trung vào khía cạnh vận hành đơn hàng và các ngoại lệ nghiệp vụ. Nếu các trang trước thiên về doanh thu, sản phẩm và khách hàng, thì trang này giúp theo dõi trạng thái đơn hàng, kênh có tỷ lệ hủy cao và các trường hợp bất thường cần chú ý trong quá trình xử lý đơn.

- Bảng phân bố phương thức thanh toán theo từng kênh.
- Biểu đồ tổng số đơn theo trạng thái.
- Biểu đồ tỷ lệ hủy theo kênh.
- Bảng tỷ lệ hoàn trả theo ngữ cảnh sản phẩm hoặc nhóm sản phẩm.
- Bảng chi tiết ngoại lệ đơn hàng để xem các đơn hàng có trạng thái cần chú ý.

![Order Dashboard](../powerbi/dashboard4.jpg)

*Hình 5.4. Order Dashboard*

### 5.8. Nhận xét chung về lớp dashboard

Với 4 trang dashboard trên, hệ thống BI của dự án đã bao phủ các lớp phân tích chính từ tổng quan kinh doanh, sản phẩm, khách hàng đến vận hành đơn hàng. Điều quan trọng về mặt học thuật là dashboard này không được xây dựng trực tiếp từ dữ liệu thô, mà dựa trên một lớp Data Mart đã chuẩn hóa sẵn. Nhờ đó, báo cáo có thể chứng minh được mối liên hệ chặt chẽ giữa kiến trúc dữ liệu, logic KPI và kết quả trực quan hóa cuối cùng.

---

## PHẦN 6: ĐÁNH GIÁ KẾT QUẢ ĐẠT ĐƯỢC

Sau khi hoàn thành pipeline ELT và dashboard BI trong phạm vi hiện tại, dự án đạt được các kết quả chính sau:

| Tiêu chí | Trước khi chuẩn hóa | Sau khi triển khai trong phạm vi dự án | Ý nghĩa |
|---|---|---|---|
| Tổ chức dữ liệu | Dữ liệu raw/nguồn khó dùng trực tiếp cho BI | Dữ liệu được đưa vào BigQuery và tổ chức thành Data Mart | Tạo ra nguồn dữ liệu rõ ràng và dễ đối soát hơn |
| Mô hình phân tích | Dữ liệu chưa tối ưu cho slicer, relationship và measure | Star Schema tách fact và dimension, dùng surrogate key | Thuận lợi cho việc xây dựng semantic model và dashboard Power BI |
| Định nghĩa KPI | KPI dễ bị hiểu khác nhau | KPI được tổ chức nhất quán qua fact table, BI views và dashboard | Giảm rủi ro lệch nghĩa giữa dữ liệu và báo cáo |
| RFM | Phân khúc khách hàng có thể phải tính thủ công | Có `mart_rfm_snapshot` cố định | Tạo sẵn lớp dữ liệu phân tích khách hàng phục vụ BI |
| Dashboard Power BI | Chưa có lớp trực quan hóa hoàn chỉnh | Đã xây dựng 4 dashboard dựa trên `retailbi_mart` | Giúp người dùng quan sát KPI, xu hướng, phân khúc và ngoại lệ nghiệp vụ trực quan hơn |

---

## PHẦN 7: KẾT LUẬN

Ở giai đoạn hiện tại, dự án VietTech BI đã xây dựng được một luồng BI tương đối đầy đủ. Bắt đầu từ raw CSV snapshot, hệ thống đã nạp dữ liệu lên BigQuery staging, biến đổi sang mô hình Star Schema tại tầng Data Mart, tạo lớp RFM snapshot và BI views để hỗ trợ chuẩn hóa KPI, sau đó kết nối Power BI để xây dựng 4 dashboard phân tích phục vụ báo cáo.

Giá trị cốt lõi mà dự án đã đạt được trong giai đoạn này gồm:

- Chuẩn hóa dữ liệu thô thành mô hình phân tích rõ ràng.
- Thống nhất cách hiểu KPI giữa lớp dữ liệu mart, BI views, Power BI và tài liệu.
- Tạo được RFM snapshot phục vụ phân tích khách hàng.
- Hoàn thiện dashboard Power BI 4 trang dựa trên nguồn dữ liệu mart đã chuẩn hóa.
