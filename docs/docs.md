# TÀI LIỆU THIẾT KẾ VÀ TRIỂN KHAI HỆ THỐNG BI BÁN LẺ

## Thông tin dự án

- **Tên dự án:** VietTech BI Project - Retail BI & RFM
- **Phiên bản tài liệu:** 1.5.1 
- **Ngày cập nhật:** 16/05/2026
- **Đơn vị:** Sinh viên ngành Hệ thống Thông tin - Trường Đại học Công nghệ (UET) - ĐHQGHN

---

## PHẦN 1: TỔNG QUAN DỰ ÁN

### 1.1. Bối cảnh và vấn đề

>Dự án này mô phỏng bài toán xây dựng hệ thống phân tích dữ liệu (BI) cho lĩnh vực bán lẻ. Trong hoạt động thực tế, các luồng dữ liệu nghiệp vụ cốt lõi – bao gồm thông tin khách hàng, chi tiết đơn hàng, danh mục sản phẩm, kênh phân phối, cũng như lịch sử thanh toán và hoàn trả – thường được sinh ra và lưu trữ rải rác tại các cơ sở dữ liệu quan hệ (Relational Database) của hệ thống vận hành (OLTP).

Tuy nhiên, kiến trúc của các database vận hành được thiết kế chủ yếu để xử lý giao dịch hằng ngày một cách nhanh chóng, do đó không tối ưu cho việc truy xuất và tổng hợp báo cáo (OLAP). Nếu người dùng muốn tính toán các chỉ số như doanh thu, lợi nhuận, tỷ lệ hoàn trả, hoặc phân khúc khách hàng theo mô hình RFM, họ thường phải trích xuất thủ công, gom nhóm từ nhiều bảng/file và tự xử lý logic bằng Excel.

Cách làm này dẫn đến một số vấn đề:

- **Tính nhất quán:** KPI dễ bị sai lệch giữa các người dùng hoặc các báo cáo khác nhau.
- **Kiểm soát logic:** Dashboard khó kiểm soát và duy trì logic nếu kết nối trực tiếp dữ liệu thô.
- **Độ phức tạp:** Các bài toán như phân khúc RFM cần nhiều bước xử lý, dễ sai sót nếu làm thủ công.
- **Cấu trúc dữ liệu:** Thiếu một mô hình dữ liệu chuẩn hóa và rõ ràng trước khi đưa vào Power BI.


### 1.2. Giải pháp đề xuất
Để giải quyết các vấn đề trên, dự án xây dựng một luồng xử lý dữ liệu (Data Pipeline) theo hướng chuẩn hóa: dữ liệu nghiệp vụ được cố định dưới dạng các bản chụp tĩnh (Raw CSV Snapshot) để tách biệt khỏi hệ thống vận hành. Từ lớp dữ liệu này, dữ liệu tiếp tục được tải lên vùng đệm Staging và trải qua quá trình chuẩn hóa, biến đổi để tổ chức lại thành mô hình đa chiều (Star Schema) tại tầng Data Mart trên Google BigQuery. Lớp dữ liệu chuẩn hóa này phục vụ trực tiếp cho dashboard Power BI, giúp thống nhất logic và thuận tiện hơn cho việc trình bày báo cáo.


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

### 1.5. Thuật ngữ và khái niệm cốt lõi 

Để thuận tiện cho việc theo dõi kiến trúc và logic phân tích của dự án, dưới đây là các khái niệm cốt lõi được sử dụng xuyên suốt tài liệu:

#### Mô hình Star Schema (Sơ đồ hình sao)
* **Định nghĩa:** Là kiến trúc tổ chức dữ liệu trong kho dữ liệu (Data Warehouse) theo chuẩn của Ralph Kimball. Mô hình bao gồm một bảng sự kiện (Fact Table) đóng vai trò trung tâm và các bảng chiều (Dimension Tables) kết nối trực tiếp xung quanh theo dạng hình sao.
* **Mục đích:** Triệt tiêu các phép JOIN phức tạp và nhiều tầng, tối ưu hóa hiệu năng cho các truy vấn tổng hợp báo cáo (OLAP), đồng thời giúp việc thiết lập các mối quan hệ (Relationships) trên Power BI trở nên trực quan, tường minh.

#### Bảng Fact (Bảng sự kiện) và Bảng Dimension (Bảng chiều)
* **Fact Table:** Lưu trữ các số đo định lượng, các chỉ số có thể tính toán được (Measures/Metrics) sinh ra từ hoạt động nghiệp vụ (như doanh thu, số lượng, chiết khấu) và danh sách các khóa ngoại (Foreign Keys) để liên kết với các bảng chiều.
* **Dimension Table:** Lưu trữ thông tin thuộc tính, mô tả bối cảnh xung quanh sự kiện kinh doanh nhằm phục vụ cho việc lọc và phân nhóm dữ liệu (ví dụ: Khách hàng là ai? Sản phẩm thuộc danh mục nào? Mua qua kênh nào? Vào thời gian nào?).

#### Mô hình RFM (Phân khúc khách hàng hành vi)
* **Recency (R - Độ gần đây):** Số ngày tính từ lần phát sinh giao dịch cuối cùng của khách hàng đến mốc chốt dữ liệu (Snapshot Date). Chỉ số R càng nhỏ chứng tỏ khách hàng có độ gắn kết càng cao với thương hiệu.
* **Frequency (F - Tần suất):** Tổng số lần thực hiện giao dịch thành công của khách hàng trong khoảng thời gian phân tích. Chỉ số F càng cao thể hiện mức độ trung thành và tần suất tương tác lớn.
* **Monetary (M - Giá trị):** Tổng số tiền khách hàng đã chi trả cho các đơn hàng hoàn tất. Chỉ số M càng lớn chứng tỏ khách hàng đóng góp giá trị doanh thu càng nhiều.


## PHẦN 2: KIẾN TRÚC GIẢI PHÁP VÀ LUỒNG XỬ LÝ DỮ LIỆU

Kiến trúc pipeline dữ liệu của dự án được chia thành 5 lớp chính, đi từ nguồn dữ liệu ban đầu đến lớp trực quan hóa trên Power BI.

**![][image1]**

Ý nghĩa từng lớp:

| Lớp | Vai trò |
|---|---|
| Source Database | Nơi phát sinh và lưu trữ dữ liệu nghiệp vụ ban đầu của hệ thống bán lẻ (Hệ thống OLTP). |
| Local Storage (Raw CSV)| Lưu trữ dữ liệu nguồn dưới dạng Snapshot cố định tại thư mục data/raw/. Bước đệm này giúp ngắt kết nối hoàn toàn với DB nguồn, cô lập hoàn toàn tiến trình phân tích để không gây ảnh hưởng đến hệ thống vận hành.|
| BigQuery Staging | Vùng đệm trên Cloud (dataset retailbi_stg). Dữ liệu thô từ CSV được load lên đây và giữ nguyên cấu trúc gốc để phục vụ cho việc đối soát dữ liệu và chuẩn hóa ở bước sau.|
| Data Mart (Star Schema) | Tầng kho dữ liệu thu nhỏ (dataset retailbi_mart). Tại đây, SQL pipeline sẽ thực hiện biến đổi (Transform), tổ chức dữ liệu thành mô hình Star Schema (Fact/Dimension), đồng thời tính toán sẵn các BI Views và phân khúc khách hàng (RFM Snapshot). |
| Analytics (Power BI) | Tầng trình diễn và trực quan hóa (Visualization). Power BI sử dụng dữ liệu từ Data Mart để xây dựng dashboard quản trị, trực quan hóa KPI, phân khúc khách hàng, hiệu quả sản phẩm và ngoại lệ vận hành đơn hàng. |

Trong phạm vi triển khai thực tế, dự án không kết nối trực tiếp đến `Source Database`. Lớp này chỉ đóng vai trò mô tả bối cảnh phát sinh dữ liệu, còn dữ liệu đầu vào dùng trong đồ án bắt đầu từ `Raw CSV Snapshot`.

### 2.1. Luồng xử lý dữ liệu tổng thể

Nếu nhìn dưới góc độ hệ thống BI, dự án đi theo một luồng xử lý điển hình nhưng được thu gọn cho phù hợp với phạm vi đồ án:

1. Dữ liệu nghiệp vụ ban đầu phát sinh ở hệ thống nguồn.
2. Dữ liệu được chụp lại thành raw CSV snapshot để tách rời bước phân tích khỏi hệ thống vận hành.
3. CSV được load lên BigQuery staging để kiểm tra tính đầy đủ và giữ bản sao dữ liệu nguồn trên cloud.
4. Dữ liệu staging được biến đổi thành Star Schema trong Data Mart để phục vụ truy vấn phân tích.
5. Từ Data Mart, hệ thống tiếp tục tạo KPI views và bảng RFM snapshot để Power BI sử dụng làm nguồn trực quan hóa.

Điểm quan trọng ở đây là mỗi lớp dữ liệu có một vai trò riêng: raw để lưu snapshot, staging để đối soát, mart để phân tích, còn Power BI là lớp diễn giải kết quả cho người dùng cuối. Cách tổ chức nhiều lớp như vậy giúp báo cáo có thể giải thích rõ dữ liệu đã được kiểm soát như thế nào trước khi đi vào dashboard.

### 2.2. Vai trò của mô hình RFM trong hệ thống

Trong dự án này, RFM không chỉ là một chỉ số phụ mà là một lớp phân tích khách hàng quan trọng nằm trên Data Mart. Việc đưa RFM vào hệ thống giúp dashboard không dừng ở việc trả lời câu hỏi doanh thu bao nhiêu, mà còn trả lời được câu hỏi khách hàng nào đang có giá trị cao, khách hàng nào đang giảm mức độ gắn bó và nhóm nào cần được chăm sóc lại.

RFM được hiểu là mô hình chấm điểm hành vi khách hàng dựa trên ba thành phần:

| Thành phần | Ý nghĩa | Giá trị diễn giải |
|---|---|---|
| Recency | Khoảng thời gian từ lần mua gần nhất đến ngày chốt dữ liệu | Càng nhỏ càng tốt, thể hiện khách còn mua gần đây |
| Frequency | Số lần mua hàng thành công trong kỳ phân tích | Càng lớn càng tốt, thể hiện khách mua thường xuyên |
| Monetary | Tổng giá trị chi tiêu từ các đơn hoàn tất | Càng lớn càng tốt, thể hiện khách mang lại doanh thu cao |

Trong phạm vi của dự án, RFM được tính từ các đơn hàng có trạng thái `Completed` và được chốt tại một thời điểm cố định là `2025-01-01`. Việc dùng snapshot date cố định phù hợp với đặc điểm dataset tĩnh của đồ án, đồng thời giúp kết quả RFM ổn định và dễ trình bày trước giảng viên.

#### Cách tính điểm RFM trong dự án

Trong dự án này, sau khi tính ba chỉ số gốc là `recency_days`, `frequency_orders` và `monetary_value`, hệ thống tiếp tục quy đổi mỗi chỉ số thành thang điểm từ `1` đến `5`. Việc chấm điểm được thực hiện theo **phân vị tương đối** trên toàn bộ tập khách hàng có đơn `Completed`, tức là khách hàng không được chấm điểm theo một ngưỡng tuyệt đối cố định mà được xếp hạng so với các khách hàng còn lại trong dataset.

- `r_score`: khách hàng mua càng gần thời điểm snapshot thì điểm càng cao.
- `f_score`: khách hàng có số đơn completed càng nhiều thì điểm càng cao.
- `m_score`: khách hàng có tổng chi tiêu càng lớn thì điểm càng cao.

Nói cách khác, hệ thống chia khách hàng thành 5 nhóm điểm cho từng tiêu chí. Nhóm có giá trị tốt nhất sẽ nhận điểm `5`, còn nhóm thấp hơn sẽ nhận các mức điểm giảm dần đến `1`. Sau đó, ba điểm này được ghép lại thành mã `rfm_score`, ví dụ `555`, `443` hoặc `211`. Mã điểm này tiếp tục được dùng để gán khách hàng vào các nhóm hành vi như `Champions`, `Loyal`, `At-risk`, `Churned` và `Regular`.

Việc chấm điểm theo phân vị có ưu điểm là phù hợp với dataset tĩnh của đồ án và giúp phản ánh vị trí tương đối của từng khách hàng trong toàn bộ tập dữ liệu. Tuy nhiên, nó cũng có nghĩa là điểm RFM mang tính so sánh trong nội bộ dataset hiện tại, không phải là một chuẩn tuyệt đối có thể áp dụng nguyên xi cho mọi bộ dữ liệu khác.

### 2.3. Ý nghĩa và ứng dụng của RFM

Từ ba thành phần RFM, hệ thống có thể chia khách hàng thành các nhóm hành vi như `Champions`, `Loyal`, `At-risk`, `Churned` và `Regular`. Điểm mạnh của cách tiếp cận này là doanh nghiệp không còn nhìn khách hàng chỉ bằng một con số doanh thu, mà có thể đánh giá họ đồng thời theo độ gần đây, mức độ thường xuyên và giá trị chi tiêu. Nhờ đó, cùng là hai khách hàng tạo ra doanh thu cao, hệ thống vẫn có thể phân biệt được ai là người mua lặp lại đều đặn và ai chỉ phát sinh một giao dịch lớn nhưng ít khả năng quay lại.

Ở góc độ quản trị, đây là một khác biệt quan trọng. Nếu chỉ dựa trên doanh thu thuần, nhà quản lý có thể đánh giá chưa đúng chất lượng tệp khách hàng. RFM giúp trả lời sâu hơn các câu hỏi như:

- Doanh thu hiện tại đang đến từ nhóm khách hàng nào?
- Nhóm khách hàng giá trị cao có còn duy trì tương tác hay đang giảm mức độ mua hàng?
- Có bao nhiêu khách hàng đang có nguy cơ rời bỏ để cần được giữ chân sớm?
- Doanh nghiệp nên ưu tiên ngân sách chăm sóc cho nhóm nào để tối ưu hiệu quả kinh doanh?

Để thuận tiện cho việc diễn giải trong báo cáo BI, các nhóm RFM của dự án có thể được hiểu ở mức nghiệp vụ như sau:

| Nhóm khách hàng | Đặc điểm hành vi điển hình | Ý nghĩa quản trị |
|---|---|---|
| `Champions` | Mua gần đây, mua nhiều lần và mang lại giá trị chi tiêu cao | Là nhóm khách hàng giá trị nhất; nên ưu tiên duy trì trải nghiệm tốt, loyalty program và các ưu đãi đặc biệt |
| `Loyal` | Mua tương đối đều đặn, tần suất tốt, đóng góp doanh thu ổn định | Là nhóm khách hàng trung thành; phù hợp cho chiến lược upsell, cross-sell và chăm sóc dài hạn |
| `At-risk` | Đã lâu chưa mua lại nhưng vẫn thuộc nhóm có tần suất mua tương đối tốt | Là nhóm cần theo dõi sớm; phù hợp cho chiến dịch win-back hoặc nhắc mua lại |
| `Churned` | Đã lâu không phát sinh giao dịch và tần suất mua thấp | Phản ánh nguy cơ mất khách; có thể dùng để đánh giá mức độ suy giảm của tệp khách hàng và hiệu quả tái kích hoạt |
| `Regular` | Có hành vi mua ở mức trung bình, chưa nổi bật về tần suất hoặc giá trị | Là nhóm có tiềm năng nuôi dưỡng; phù hợp cho các chương trình tăng tần suất mua và nâng giá trị đơn hàng |

Ý nghĩa của RFM trong bài toán bán lẻ nằm ở chỗ nó biến dữ liệu giao dịch thành dữ liệu hành vi. Nói cách khác, từ các bảng đơn hàng và doanh thu, hệ thống không chỉ biết "đã bán được bao nhiêu", mà còn biết "đã bán cho ai", "khách hàng đó có còn gắn bó không" và "nhóm khách nào cần hành động tiếp theo". Đây chính là bước chuyển quan trọng từ báo cáo vận hành thuần túy sang báo cáo BI hỗ trợ quyết định.

Trong hệ thống hiện tại, kết quả RFM được ứng dụng theo ba hướng chính:

1. **Phân tích cấu trúc tệp khách hàng**  
   Dashboard có thể cho thấy tỷ trọng khách hàng theo từng `rfm_segment`, từ đó giúp đánh giá xem doanh nghiệp đang dựa nhiều vào nhóm trung thành hay đang có tỷ lệ lớn khách hàng suy giảm tương tác.

2. **Đánh giá chất lượng doanh thu**  
   Thay vì chỉ quan sát tổng doanh thu, BI có thể phân rã doanh thu theo nhóm khách hàng. Điều này cho phép giảng viên và người đọc báo cáo thấy rõ doanh thu đến từ nhóm `Champions`, `Loyal` hay `At-risk`, từ đó hiểu sâu hơn về độ bền vững của kết quả kinh doanh.

3. **Hỗ trợ hành động quản trị và marketing**  
   Mỗi nhóm RFM có thể gắn với một định hướng hành động khác nhau, ví dụ: giữ chân VIP cho `Champions`, upsell/cross-sell cho `Loyal`, tái kích hoạt cho `At-risk`, hoặc chăm sóc nuôi dưỡng cho `Regular`. Dù phạm vi đồ án chưa triển khai hệ thống campaign thực tế, việc đưa RFM vào BI vẫn thể hiện được giá trị ứng dụng rõ ràng của phân tích dữ liệu trong quản trị khách hàng.

Đối với báo cáo BI, RFM giúp mở rộng phạm vi phân tích từ góc nhìn sản phẩm và đơn hàng sang góc nhìn khách hàng. Đây cũng là lý do dự án tạo riêng bảng `mart_rfm_snapshot` thay vì để Power BI tự tính toàn bộ logic ở lớp trực quan hóa. Cách làm này giúp kết quả phân khúc được cố định, dễ đối soát và nhất quán giữa SQL, dashboard và phần diễn giải trong báo cáo.


## PHẦN 3: NỀN TẢNG LÝ THUYẾT VÀ LÝ DO CHỌN CÔNG NGHỆ

### 3.1. Công nghệ sử dụng

| Công nghệ | Vai trò trong dự án |
|---|---|
| Raw CSV Snapshot | Dữ liệu nguồn cố định, dùng làm đầu vào cho pipeline trong phạm vi đồ án |
| Python 3.10+ | Điều phối bước load CSV lên BigQuery và chạy SQL pipeline |
| Google BigQuery | Lưu staging data, tạo Data Mart, chạy SQL transform và BI views | 
| Power BI Desktop | Trực quan hóa dữ liệu thành dashboard | 

### 3.2. Vì sao dùng BigQuery?

BigQuery được chọn vì phù hợp với đặc điểm của dự án theo nhiều khía cạnh:

- **Serverless, không cần quản lý hạ tầng:** BigQuery không yêu cầu cài đặt hay vận hành cluster. Với phạm vi dự án, điều này giúp tập trung vào logic pipeline thay vì cấu hình server.
- **Hỗ trợ SQL chuẩn và phân tách dataset rõ ràng:** Cho phép tổ chức dữ liệu thành `retailbi_stg` và `retailbi_mart` riêng biệt, tách bạch lớp raw và lớp phân tích.
- **Tích hợp tốt với Power BI:** Power BI có connector trực tiếp với BigQuery, giúp kết nối mart dataset vào dashboard không cần export thêm file trung gian.
- **Phù hợp với dữ liệu dạng bảng lớn:** BigQuery tối ưu cho analytical query trên dữ liệu dạng columnar, khác với relational database vận hành vốn được tối ưu cho transactional workload.

### 3.3. Vì sao dùng Star Schema?

Star Schema được chọn thay vì giữ nguyên cấu trúc normalized của relational database gốc vì một số lý do kỹ thuật cụ thể:

- **Giảm số lần JOIN khi truy vấn:** Normalized schema (3NF) yêu cầu nhiều bảng join lại để ra một chỉ số đơn giản. Star Schema đã pre-join dimension vào dạng denormalized, giúp aggregation query chạy nhanh hơn và dễ viết hơn.
- **Power BI làm việc tốt với mô hình star:** Semantic model trong Power BI được thiết kế để thiết lập relationship theo dạng fact–dimension. Nếu dữ liệu vẫn ở dạng normalized nhiều bảng, việc xây dựng measure và slicer phức tạp hơn đáng kể.
- **Tách biệt rõ "số liệu" và "mô tả":** Fact table chứa các sự kiện đo lường được (doanh thu, số lượng, chiết khấu); dimension table chứa ngữ cảnh để lọc và nhóm (khách hàng, sản phẩm, thời gian). Sự tách biệt này giúp người đọc dashboard hiểu logic dữ liệu dễ hơn.

### 3.4. Vì sao dùng Power BI?
Power BI được chọn làm công cụ trực quan hóa dữ liệu (Visualization) ở tầng cuối cùng của pipeline vì phù hợp với kiến trúc và mục tiêu của đồ án:

- **Tương thích tốt với Star Schema:** Lõi Semantic Model của Power BI được thiết kế để làm việc với mô hình Fact-Dimension, giúp thiết lập các Relationship dễ dàng và rõ ràng.

- **Tích hợp Native Connector với BigQuery:** Kết nối và đọc thẳng dữ liệu từ dataset retailbi_mart mà không cần xuất qua file trung gian.

- **Tối ưu hiệu năng hiển thị:** Nhờ logic tính toán nặng (như RFM) đã được xử lý bằng SQL dưới BigQuery, Power BI được giảm tải các hàm DAX phức tạp. Nó chỉ tập trung xử lý cross-filter, nhờ đó cải thiện khả năng phản hồi khi trình bày dashboard.

---

## PHẦN 4: CÁC GIAI ĐOẠN TRIỂN KHAI

### 4.1. Tổng quan các giai đoạn

| Giai đoạn | Nội dung thực hiện | Kết quả đầu ra |
|---|---|---|
| Giai đoạn 1 | Chuẩn bị raw CSV snapshot | 8 file CSV cố định trong `data/raw/` |
| Giai đoạn 2 | Load CSV lên BigQuery staging bằng Python ETL | Các bảng `stg_*` trong `retailbi_stg` |
| Giai đoạn 3 | Biến đổi dữ liệu staging thành Star Schema | Các bảng `dim_*` và `fact_*` trong `retailbi_mart` |
| Giai đoạn 4 | Tạo lớp phân tích phục vụ BI | `mart_rfm_snapshot`, `vw_monthly_kpi`, `vw_channel_performance`, `vw_return_metrics` |
| Giai đoạn 5 | Xây dựng Power BI Dashboard | 4 dashboard hoàn chỉnh: Business Performance, Product, RFM và Order |

### 4.2. Giai đoạn 1 - Chuẩn bị raw CSV snapshot

Trong phạm vi hiện tại, dữ liệu đầu vào của dự án được cố định dưới dạng raw CSV snapshot trong thư mục `data/raw/`. Đây là lớp dữ liệu nguồn dùng chung cho toàn bộ pipeline ETL/ELT, giúp nhóm tách biệt phần phân tích khỏi hệ thống vận hành và đảm bảo mọi lần chạy pipeline đều dựa trên cùng một bộ dữ liệu.

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

### 4.3. Giai đoạn 2 - Load CSV lên BigQuery Staging

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

### 4.4. Giai đoạn 3 - Tạo Star Schema và load Data Mart

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

Một số quy tắc biến đổi quan trọng của giai đoạn này gồm:

- `dim_payment` được deduplicate theo `payment_method` trước khi tạo `payment_key`.
- `fact_order_items.order_date_key` lấy từ order header để đảm bảo đúng ngữ nghĩa phân tích theo ngày đơn hàng.
- `ship_date_key` dùng ba giá trị đặc biệt `-1`, `-2`, `-3` để phân biệt dữ liệu bất thường, chưa giao và không áp dụng.
- `dim_date` được bổ sung các dòng đặc biệt tương ứng với `ship_date_key` để Power BI có thể xử lý nhất quán.

Kết quả của giai đoạn này là một Data Mart dạng hình sao, đóng vai trò là nguồn dữ liệu chuẩn hóa cho phân tích và trực quan hóa sau này.

### 4.5. Giai đoạn 4 - Tạo RFM Snapshot và BI Views

Sau khi Star Schema hoàn tất, dự án tiếp tục tạo lớp phân tích phục vụ báo cáo. Lớp này gồm hai nhóm thành phần chính.

Thứ nhất là bảng `mart_rfm_snapshot`, được tính từ `fact_orders` với điều kiện chỉ lấy các đơn hàng có trạng thái `Completed`. Snapshot date hiện được cố định tại:

```text
2025-01-01
```

Từ đó, hệ thống tính ba thành phần RFM:

- **Recency:** số ngày kể từ lần mua gần nhất đến ngày snapshot.
- **Frequency:** số lượng đơn hàng completed của khách hàng.
- **Monetary:** tổng giá trị chi tiêu của khách hàng trên các đơn completed.

Thứ hai là các BI views dùng để chuẩn hóa KPI và hỗ trợ đối soát nhanh:

- `vw_monthly_kpi`
- `vw_channel_performance`
- `vw_return_metrics`

Các view này không thay thế fact table trong phân tích chi tiết, nhưng rất hữu ích cho việc kiểm tra định nghĩa KPI, đối chiếu số liệu tổng hợp và làm nguồn tham khảo khi dựng dashboard ở giai đoạn sau.

### 4.6. Giai đoạn 5 - Xây dựng Power BI Dashboard

Trên nền dataset `retailbi_mart`, nhóm đã kết nối Power BI Desktop với BigQuery và xây dựng dashboard theo 4 góc nhìn phân tích: Business Performance, Product, RFM và Order. Ở giai đoạn này, dashboard không lấy dữ liệu trực tiếp từ raw hay staging mà sử dụng fact table, dimension table và một số BI views ở tầng mart để đảm bảo KPI được hiển thị nhất quán với logic đã chuẩn hóa trong SQL.

Kết quả của giai đoạn này là lớp trực quan hóa hoàn chỉnh trong phạm vi đồ án. Người dùng có thể theo dõi hiệu quả kinh doanh tổng quan, phân tích danh mục sản phẩm, quan sát phân khúc khách hàng theo RFM và nhận diện các ngoại lệ trong vòng đời đơn hàng trên cùng một hệ thống BI thống nhất.

---

## PHẦN 5: KẾT QUẢ ETL VÀ ĐỐI SOÁT DỮ LIỆU

### 5.1. Kết quả sau khi load lên BigQuery staging

Sau giai đoạn ETL đầu tiên, toàn bộ dữ liệu nguồn đã được đưa lên dataset `retailbi_stg`. Mỗi file CSV tương ứng với một bảng staging để phục vụ cho kiểm tra dữ liệu và transform sang mart.

Danh sách bảng staging gồm:

- `stg_customers`
- `stg_orders`
- `stg_order_details`
- `stg_order_returns`
- `stg_products`
- `stg_product_categories`
- `stg_product_price_history`
- `stg_sales_channels`

Lớp staging giúp dự án giữ được một bản sao dữ liệu nguồn trên BigQuery, từ đó dễ so sánh row count, kiểm tra trường dữ liệu và truy vết lỗi khi cần đối soát với raw CSV.

### 5.2. Kết quả sau khi transform sang Data Mart

Sau khi chạy SQL pipeline, dataset `retailbi_mart` được hình thành với ba nhóm đối tượng chính:

| Nhóm | Thành phần | Vai trò |
|---|---|---|
| Dimension | `dim_date`, `dim_customer`, `dim_product`, `dim_channel`, `dim_payment` | Lưu thuộc tính mô tả để lọc, nhóm và xây dựng relationship |
| Fact | `fact_orders`, `fact_order_items`, `fact_returns` | Lưu các sự kiện và số đo nghiệp vụ phục vụ phân tích |
| Analytics | `mart_rfm_snapshot`, `vw_monthly_kpi`, `vw_channel_performance`, `vw_return_metrics` | Chuẩn hóa lớp KPI và hỗ trợ phân tích tổng hợp |

So với dữ liệu nguồn dạng quan hệ tác nghiệp, lớp mart đã được tổ chức lại theo đúng tư duy phân tích. Các dimension và fact được tách rõ, mối quan hệ được chuẩn hóa bằng surrogate key, còn các KPI cốt lõi được tổ chức nhất quán thông qua fact table, BI views và lớp dashboard.

### 5.3. Baseline row count của dữ liệu gốc

Để kiểm soát tính đầy đủ của bước load staging, dự án sử dụng baseline row count của bộ dữ liệu cố định như sau:

| File | Số dòng dữ liệu kỳ vọng |
|---|---:|
| `customers.csv` | 5000 |
| `orders.csv` | 21435 |
| `order_details.csv` | 39548 |
| `order_returns.csv` | 1288 |
| `products.csv` | 47 |
| `product_categories.csv` | 14 |
| `product_price_history.csv` | 24 |
| `sales_channels.csv` | 8 |

Các số liệu này được dùng làm mốc đối chiếu với số dòng trong các bảng `stg_*` sau khi chạy ETL.

### 5.4. Các bước đối soát thủ công sau ETL/ELT

Trong phạm vi đồ án, nhóm chưa xây dựng cơ chế kiểm thử dữ liệu tự động mà sử dụng checklist đối soát thủ công. Các nhóm kiểm tra chính gồm:

1. **Kiểm tra row count staging**  
   Số dòng của từng bảng `stg_*` phải khớp với số dòng của file CSV tương ứng.

2. **Kiểm tra tính toàn vẹn của surrogate key**  
   Các trường `customer_key`, `channel_key`, `payment_key`, `product_key`, `order_key` trong fact table không được null một cách bất thường sau khi join từ staging sang dimension/fact.

3. **Kiểm tra `dim_payment`**  
   Bảng `dim_payment` phải chỉ chứa một dòng cho mỗi phương thức thanh toán khác nhau, tránh tình trạng nhân bản khóa do sinh UUID trước khi deduplicate.

4. **Kiểm tra `dim_date` và `ship_date_key`**  
   Bảng `dim_date` phải có đủ các dòng đặc biệt `-1`, `-2`, `-3`; đồng thời phân bố `ship_date_key` trong `fact_orders` phải phản ánh đúng ý nghĩa nghiệp vụ của dữ liệu thiếu ngày giao.

5. **Kiểm tra tính nhất quán KPI**  
   Các tổng số trong `vw_monthly_kpi` phải khớp với dữ liệu gốc từ `fact_orders`, đặc biệt là `total_completed_orders`, `completed_revenue` và `completed_discount`.

6. **Kiểm tra logic RFM**  
   `mart_rfm_snapshot` chỉ sử dụng các đơn `Completed` và phải giữ `customer_key` để có thể nối trực tiếp với `dim_customer` trong Power BI.

Các bước trên được dùng như checklist đối soát thủ công sau mỗi lần nạp dữ liệu hoặc điều chỉnh logic SQL, giúp nhóm kiểm tra lại row count, khóa surrogate và mức độ nhất quán của KPI tổng hợp.

### 5.5. Ý nghĩa của kết quả ETL đối với bước BI

Xét riêng ở góc độ ETL/ELT, kết quả quan trọng nhất của dự án là tạo ra một lớp dữ liệu phân tích đủ rõ logic và đủ ổn định để làm nền cho dashboard Power BI. Cụ thể:

- Dữ liệu nguồn đã được đưa lên cloud và tổ chức lại theo mô hình phù hợp cho BI.
- KPI cốt lõi đã được tổ chức nhất quán giữa fact table, BI views và các measure trong Power BI, hạn chế việc mỗi báo cáo tự diễn giải.
- Mối quan hệ giữa khách hàng, đơn hàng, sản phẩm, kênh bán và hoàn trả đã được chuẩn hóa bằng surrogate key.
- RFM snapshot và các BI views đã sẵn sàng để Power BI sử dụng trực tiếp.

Như vậy, phần ETL/ELT của dự án đã hoàn thành vai trò nền tảng: biến dữ liệu raw thành một Data Mart có cơ sở đối soát và làm nguồn trực tiếp cho lớp dashboard ở phần sau.

---

## PHẦN 6: DASHBOARD POWER BI

### 6.1. Vai trò của dashboard

Dashboard Power BI là lớp trình diễn cuối cùng của toàn bộ hệ thống BI. Sau khi dữ liệu đã được chuẩn hóa ở BigQuery thành Data Mart và các KPI đã được tổ chức nhất quán giữa lớp dữ liệu mart, BI views và Power BI, Power BI đóng vai trò chuyển các kết quả đó thành thông tin quản trị dễ quan sát, dễ lọc và dễ so sánh hơn cho người dùng cuối.

Trong phạm vi đồ án, dashboard được xây dựng để trả lời bốn nhóm câu hỏi chính:

- Doanh nghiệp đang vận hành ra sao ở góc nhìn doanh thu, lợi nhuận, đơn hàng và hoàn trả?
- Sản phẩm và kênh bán nào đang đóng góp tốt hơn vào kết quả kinh doanh?
- Tệp khách hàng đang được phân hóa như thế nào theo hành vi mua hàng?
- Những ngoại lệ nào trong trạng thái đơn hàng, hoàn trả và hủy đơn cần được theo dõi?

### 6.2. Cấu trúc dashboard thực tế

Dashboard hiện tại gồm 4 trang chính, tương ứng với 4 nhóm phân tích của hệ thống BI:

| Trang | Câu hỏi kinh doanh chính | KPI / nội dung chính | Nguồn dữ liệu chính |
|---|---|---|---|
| Business Performance Dashboard | Hiệu quả kinh doanh tổng quan đang như thế nào? | Doanh thu đơn hoàn tất, số đơn hoàn tất, giá trị đơn hàng trung bình, tỷ lệ hoàn trả, xu hướng đơn hàng, doanh thu theo kênh, doanh thu theo sản phẩm, biên lợi nhuận gộp theo thương hiệu | `fact_orders`, `fact_order_items`, `dim_date`, `dim_channel`, `dim_product`, `vw_monthly_kpi`, `vw_channel_performance`, `vw_return_metrics` |
| Product Dashboard | Danh mục sản phẩm nào tạo doanh thu và lợi nhuận tốt hơn? | Doanh thu và lợi nhuận theo danh mục, tốc độ bán sản phẩm, xu hướng doanh thu theo quý, chiết khấu - doanh thu - số lượng theo sản phẩm và thương hiệu | `fact_order_items`, `fact_orders`, `dim_product`, `dim_channel` |
| RFM Dashboard | Khách hàng đang được phân nhóm ra sao theo hành vi mua hàng? | Số khách theo `rfm_segment`, giá trị chi tiêu theo segment, bảng chi tiết khách hàng, độ gần đây, tần suất mua, gợi ý hành động theo segment | `mart_rfm_snapshot`, `dim_customer` |
| Order Dashboard | Những ngoại lệ và đặc điểm vận hành đơn hàng cần theo dõi là gì? | Phương thức thanh toán theo kênh, tổng số đơn theo trạng thái, tỷ lệ hủy theo kênh, tỷ lệ hoàn trả theo ngữ cảnh sản phẩm, chi tiết ngoại lệ đơn hàng | `fact_orders`, `fact_returns`, `fact_order_items`, `dim_channel`, `dim_payment`, `dim_product`, `dim_date` |

Thiết kế này bám sát logic của Data Mart đã xây dựng. Mỗi trang đều có nguồn dữ liệu rõ ràng và bám sát một nhóm câu hỏi kinh doanh cụ thể, tránh việc Power BI phải tự tái hiện lại logic nghiệp vụ từ dữ liệu thô.

### 6.3. Định nghĩa KPI chính trên dashboard

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

### 6.4. Business Performance Dashboard

Trang Business Performance Dashboard là trang tổng quan điều hành, tập trung vào việc cho thấy bức tranh kinh doanh chung của doanh nghiệp trên dataset hiện tại. Trang này sử dụng các KPI card ở phần trên để thể hiện nhanh doanh thu đơn hoàn tất, số đơn hoàn tất, giá trị đơn hàng trung bình và tỷ lệ hoàn trả, sau đó bổ sung các biểu đồ xu hướng và cơ cấu để giải thích sâu hơn kết quả tổng quan.

- KPI card: Doanh thu đơn hoàn tất, số đơn hoàn tất, giá trị đơn hàng trung bình, tỷ lệ hoàn trả.
- Biểu đồ xu hướng số đơn theo tháng.
- Biểu đồ donut doanh thu theo loại kênh bán.
- Biểu đồ doanh thu theo sản phẩm.
- Bảng biên lợi nhuận gộp theo từng thương hiệu.

Trong trang này, chỉ số doanh thu đơn hoàn tất và số đơn hoàn tất đều chỉ tính các đơn hàng có trạng thái `Completed`, nhằm tránh ghi nhận doanh thu hoặc số đơn từ các trạng thái chưa hoàn tất hay đã hủy.

![Business Performance Dashboard](../powerbi/dashboard1.jpg)

*Hình 6.1. Business Performance Dashboard*

### 6.5. Product Dashboard

Trang Product Dashboard đi sâu vào hiệu quả kinh doanh ở cấp sản phẩm và danh mục. So với trang tổng quan, trang này hỗ trợ tốt hơn cho việc so sánh giữa các danh mục, theo dõi mối quan hệ giữa chiết khấu và doanh thu, cũng như xác định các sản phẩm có tốc độ bán cao trong kỳ.

- Biểu đồ doanh thu và lợi nhuận theo danh mục.
- Biểu đồ bubble thể hiện chiết khấu, doanh thu và số lượng bán theo sản phẩm và thương hiệu.
- Biểu đồ tốc độ bán sản phẩm theo tháng ở cấp sản phẩm.
- Biểu đồ xu hướng doanh thu theo quý và danh mục.

![Product Dashboard](../powerbi/dashboard2.jpg)

*Hình 6.2. Product Dashboard*

### 6.6. RFM Dashboard

Trang RFM Dashboard là lớp phân tích khách hàng chuyên biệt của hệ thống. Trang này dùng trực tiếp `mart_rfm_snapshot` để thể hiện phân khúc hành vi khách hàng, thay vì tính RFM động trong Power BI. Nhờ đó, logic phân nhóm được cố định và nhất quán với phần mô hình hóa đã trình bày ở các phần trước.

- Treemap thể hiện tổng số khách hàng theo `rfm_segment`.
- Biểu đồ cột thể hiện giá trị chi tiêu theo từng segment.
- Bảng chi tiết khách hàng gồm thành phố, `rfm_segment`, `monetary_value`, `recency_days`, `frequency_orders` và gợi ý hành động theo segment.

Trang này đặc biệt hữu ích khi trình bày ý nghĩa ứng dụng của RFM trong BI, vì nó cho thấy không chỉ khách hàng được chia thành nhóm nào mà còn gợi mở cách doanh nghiệp có thể hành động với từng nhóm khách hàng cụ thể. Phần gợi ý hành động được thể hiện ở lớp dashboard dựa trên phân khúc RFM, thay vì là một trường nghiệp vụ có sẵn trong bảng mart.

![RFM Dashboard](../powerbi/dashboard3.jpg)

*Hình 6.3. RFM Dashboard*

### 6.7. Order Dashboard

Trang Order Dashboard tập trung vào khía cạnh vận hành đơn hàng và các ngoại lệ nghiệp vụ. Nếu các trang trước thiên về doanh thu, sản phẩm và khách hàng, thì trang này giúp theo dõi trạng thái đơn hàng, kênh có tỷ lệ hủy cao và các trường hợp bất thường cần chú ý trong quá trình xử lý đơn.

- Bảng phân bố phương thức thanh toán theo từng kênh.
- Biểu đồ tổng số đơn theo trạng thái.
- Biểu đồ tỷ lệ hủy theo kênh.
- Bảng tỷ lệ hoàn trả theo ngữ cảnh sản phẩm hoặc nhóm sản phẩm.
- Bảng chi tiết ngoại lệ đơn hàng để xem các đơn hàng có trạng thái cần chú ý.

![Order Dashboard](../powerbi/dashboard4.jpg)

*Hình 6.4. Order Dashboard*

### 6.8. Nhận xét chung về lớp dashboard

Với 4 trang dashboard trên, hệ thống BI của dự án đã có đủ các lớp phân tích từ tổng quan kinh doanh, sản phẩm, khách hàng đến vận hành đơn hàng. Điều quan trọng về mặt học thuật là dashboard này không được xây dựng trực tiếp từ dữ liệu thô, mà dựa trên một lớp Data Mart đã chuẩn hóa sẵn. Nhờ đó, báo cáo có thể chứng minh được mối liên hệ chặt chẽ giữa kiến trúc dữ liệu, logic KPI và kết quả trực quan hóa cuối cùng.

---

## PHẦN 7: ĐÁNH GIÁ KẾT QUẢ ĐẠT ĐƯỢC

Sau khi hoàn thành pipeline ETL/ELT và dashboard BI trong phạm vi hiện tại, dự án đạt được các kết quả chính sau:

| Tiêu chí | Trước khi chuẩn hóa | Sau khi triển khai trong phạm vi dự án | Ý nghĩa |
|---|---|---|---|
| Tổ chức dữ liệu | Dữ liệu raw/nguồn khó dùng trực tiếp cho BI | Dữ liệu được đưa vào BigQuery và tổ chức thành Data Mart | Tạo ra nguồn dữ liệu rõ ràng và dễ đối soát hơn |
| Mô hình phân tích | Dữ liệu chưa tối ưu cho slicer, relationship và measure | Star Schema tách fact và dimension, dùng surrogate key | Thuận lợi cho việc xây dựng semantic model và dashboard Power BI |
| Định nghĩa KPI | KPI dễ bị hiểu khác nhau | KPI được tổ chức nhất quán qua fact table, BI views và dashboard | Giảm rủi ro lệch nghĩa giữa dữ liệu và báo cáo |
| RFM | Phân khúc khách hàng có thể phải tính thủ công | Có `mart_rfm_snapshot` cố định | Tạo sẵn lớp dữ liệu phân tích khách hàng phục vụ BI |
| Dashboard Power BI | Chưa có lớp trực quan hóa hoàn chỉnh | Đã xây dựng 4 dashboard dựa trên `retailbi_mart` | Giúp người dùng quan sát KPI, xu hướng, phân khúc và ngoại lệ nghiệp vụ trực quan hơn |

---

## PHẦN 8: GIỚI HẠN VÀ HƯỚNG MỞ RỘNG

### 8.1. Giới hạn hiện tại

| Giới hạn | Ý nghĩa |
|---|---|
| Dataset cố định | Kết quả ETL hiện phản ánh một snapshot dữ liệu tĩnh, không phải dữ liệu thời gian thực. |
| Chưa có tải dữ liệu tăng dần | Pipeline hiện tại phù hợp để load lại toàn bộ dataset, chưa tối ưu cho dữ liệu tăng dần hằng ngày. |
| Chưa có tự động chạy theo lịch | Việc chạy tự động theo lịch là hướng mở rộng, không phải trọng tâm hiện tại. |
| RFM là một snapshot | Chưa hỗ trợ so sánh RFM qua nhiều thời điểm. |
| Dashboard ở mức phân tích mô tả | Dashboard hiện tập trung vào KPI, xu hướng, phân khúc và ngoại lệ; chưa mở rộng sang dự báo, cảnh báo tự động hoặc phân tích what-if. |

### 8.2. Hướng mở rộng

Dự án có thể được mở rộng theo các hướng sau:

- Kết nối trực tiếp relational database để tự động xuất CSV hoặc load dữ liệu vào staging.
- Bổ sung scheduler để chạy pipeline theo lịch.
- Thêm alerting khi pipeline lỗi.
- Chuyển từ full load sang tải dữ liệu tăng dần.
- Tạo multi-snapshot RFM để theo dõi thay đổi phân khúc khách hàng theo thời gian.
- Bổ sung drill-through, forecasting, alerting và khả năng mở rộng dashboard khi có dữ liệu mới.

---

## PHẦN 9: KẾT LUẬN

Ở giai đoạn hiện tại, dự án VietTech BI đã xây dựng được một luồng BI tương đối đầy đủ trong phạm vi đồ án. Bắt đầu từ raw CSV snapshot, hệ thống đã nạp dữ liệu lên BigQuery staging, biến đổi sang mô hình Star Schema tại tầng Data Mart, tạo lớp RFM snapshot và BI views để hỗ trợ chuẩn hóa KPI, sau đó kết nối Power BI để xây dựng 4 dashboard phân tích phục vụ báo cáo.

Giá trị cốt lõi mà dự án đã đạt được trong giai đoạn này gồm:

- Chuẩn hóa dữ liệu thô thành mô hình phân tích rõ ràng.
- Thống nhất cách hiểu KPI giữa lớp dữ liệu mart, BI views, Power BI và tài liệu.
- Tạo được RFM snapshot phục vụ phân tích khách hàng.
- Thiết lập được checklist đối soát thủ công sau ETL/ELT.
- Hoàn thiện dashboard Power BI 4 trang dựa trên nguồn dữ liệu mart đã chuẩn hóa.

[image1]: <data:image/png;base64,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>
