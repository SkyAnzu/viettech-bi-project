-- ==============================================================================
-- KHỞI TẠO SCHEMA
-- ==============================================================================
SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0;
SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0;
SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';

CREATE SCHEMA IF NOT EXISTS `retailbi_oltp` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci ;
USE `retailbi_oltp` ;

-- 1. Bảng sales_channels (Kênh bán hàng)
CREATE TABLE IF NOT EXISTS `sales_channels` (
  `id` SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `channel_code` VARCHAR(30) NOT NULL,
  `channel_name` VARCHAR(100) NOT NULL,
  `channel_type` ENUM('Physical', 'Web', 'App') NOT NULL,
  `is_active` TINYINT(1) NOT NULL DEFAULT '1',
  `opened_date` DATE NULL,
  PRIMARY KEY (`id`),
  UNIQUE INDEX `uq_channel_code` (`channel_code` ASC) VISIBLE
) ENGINE = InnoDB;

-- 2. Bảng product_categories (Phân loại sản phẩm)
CREATE TABLE IF NOT EXISTS `product_categories` (
  `id` SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `category` VARCHAR(100) NOT NULL,
  `sub_category` VARCHAR(100) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE INDEX `uq_category_sub` (`category` ASC, `sub_category` ASC) VISIBLE
) ENGINE = InnoDB;

-- 3. Bảng products (Sản phẩm)
CREATE TABLE IF NOT EXISTS `products` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `product_code` VARCHAR(50) NOT NULL,
  `product_name` VARCHAR(255) NOT NULL,
  `category_id` SMALLINT UNSIGNED NOT NULL,
  `brand` VARCHAR(100) NULL,
  `cost_price` DECIMAL(15,2) NOT NULL,
  `msrp` DECIMAL(15,2) NOT NULL,
  `quantity_in_stock` INT NOT NULL DEFAULT '0',
  `is_active` TINYINT(1) NOT NULL DEFAULT '1',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE INDEX `uq_product_code` (`product_code` ASC) VISIBLE,
  INDEX `idx_updated_at` (`updated_at` ASC) VISIBLE, -- [THÊM MỚI] Phục vụ ETL Extract
  CONSTRAINT `fk_products_category` FOREIGN KEY (`category_id`) REFERENCES `product_categories` (`id`)
) ENGINE = InnoDB;

-- 4. Bảng customers (Khách hàng)
CREATE TABLE IF NOT EXISTS `customers` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `customer_code` VARCHAR(50) NOT NULL,
  `first_name` VARCHAR(50) NOT NULL,
  `last_name` VARCHAR(50) NOT NULL,
  `email` VARCHAR(150) NULL,
  `phone` VARCHAR(20) NULL,
  `gender` ENUM('Male', 'Female', 'Other') NULL,
  `date_of_birth` DATE NULL,
  `city` VARCHAR(100) NOT NULL,
  `country` VARCHAR(100) NOT NULL DEFAULT 'Vietnam',
  `segment` ENUM('Consumer', 'Corporate', 'Home Office') NOT NULL,
  `registered_date` DATE NOT NULL,
  `is_active` TINYINT(1) NOT NULL DEFAULT '1',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE INDEX `uq_customer_code` (`customer_code` ASC) VISIBLE,
  UNIQUE INDEX `uq_customer_email` (`email` ASC) VISIBLE,
  INDEX `idx_updated_at` (`updated_at` ASC) VISIBLE -- [THÊM MỚI] Phục vụ ETL Extract
) ENGINE = InnoDB;

-- 5. Bảng orders (Đơn hàng)
CREATE TABLE IF NOT EXISTS `orders` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `order_code` VARCHAR(50) NOT NULL,
  `customer_id` INT UNSIGNED NOT NULL,
  `channel_id` SMALLINT UNSIGNED NOT NULL,
  `order_date` DATETIME NOT NULL,
  `ship_date` DATE NULL,
  `status` ENUM('Pending', 'Processing', 'Shipped', 'Completed', 'Cancelled', 'Returned') NOT NULL DEFAULT 'Pending',
  `payment_method` ENUM('Cash', 'Credit Card', 'Bank Transfer', 'E-Wallet') NOT NULL DEFAULT 'Cash',
  `total_amount` DECIMAL(15,2) NOT NULL,
  `total_discount` DECIMAL(15,2) NOT NULL,
  `net_amount` DECIMAL(15,2) NOT NULL,
  `total_cost` DECIMAL(15,2) NOT NULL,
  `note` TEXT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE INDEX `uq_order_code` (`order_code` ASC) VISIBLE,
  INDEX `idx_updated_at` (`updated_at` ASC) VISIBLE, -- [THÊM MỚI] Phục vụ ETL Extract
  INDEX `idx_rfm_query` (`customer_id` ASC, `status` ASC, `order_date` ASC) VISIBLE, -- [THÊM MỚI] Phục vụ ETL Transform (RFM)
  CONSTRAINT `fk_orders_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`),
  CONSTRAINT `fk_orders_channel` FOREIGN KEY (`channel_id`) REFERENCES `sales_channels` (`id`)
) ENGINE = InnoDB;

-- 6. Bảng order_details (Chi tiết đơn hàng)
CREATE TABLE IF NOT EXISTS `order_details` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `order_id` INT UNSIGNED NOT NULL,
  `product_id` INT UNSIGNED NOT NULL,
  `quantity` INT NOT NULL,
  `unit_price` DECIMAL(15,2) NOT NULL,
  `unit_cost` DECIMAL(15,2) NOT NULL,
  `discount_rate` DECIMAL(5,4) NOT NULL,
  `discount_amount` DECIMAL(15,2) NOT NULL,
  `line_total` DECIMAL(15,2) NOT NULL,
  `line_cost` DECIMAL(15,2) NOT NULL,
  `line_profit` DECIMAL(15,2) NOT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE INDEX `uq_order_product` (`order_id` ASC, `product_id` ASC) VISIBLE,
  CONSTRAINT `fk_details_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_details_product` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`)
) ENGINE = InnoDB;

-- 7. Bảng order_returns (Lịch sử hoàn trả)
CREATE TABLE IF NOT EXISTS `order_returns` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `order_id` INT UNSIGNED NOT NULL,
  `reason` VARCHAR(255) NULL,
  `returned_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE INDEX `uq_return_order` (`order_id` ASC) VISIBLE,
  CONSTRAINT `fk_returns_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`)
) ENGINE = InnoDB;

-- 8. Bảng product_price_history (Lịch sử thay đổi giá)
CREATE TABLE IF NOT EXISTS `product_price_history` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `product_id` INT UNSIGNED NOT NULL,
  `price_type` ENUM('MSRP', 'COST') NOT NULL,
  `old_price` DECIMAL(15,2) NULL,
  `new_price` DECIMAL(15,2) NOT NULL,
  `changed_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  CONSTRAINT `fk_price_hist_prod` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`)
) ENGINE = InnoDB;

SET SQL_MODE=@OLD_SQL_MODE;
SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS;
SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS;
