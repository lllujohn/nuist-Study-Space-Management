-- 南信大自习空间管理子系统 v2.0 - 完整数据库建库脚本
-- =========================================================

-- 开启事件调度器（用于定时任务）
SET GLOBAL event_scheduler = ON;

-- 如果数据库已存在，先删除重建
DROP DATABASE IF EXISTS lingxi_db;

CREATE DATABASE lingxi_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE lingxi_db;

-- =========================================================
-- 1. 表结构创建
-- =========================================================

-- 1.1 学生表 (students)
CREATE TABLE students (
    student_id   VARCHAR(20) PRIMARY KEY,
    name         VARCHAR(50) NOT NULL,
    email        VARCHAR(100) UNIQUE,
    phone        VARCHAR(20) UNIQUE,
    password_hash VARCHAR(255) NOT NULL DEFAULT '', -- bcrypt 哈希密码
    credit_score INT NOT NULL DEFAULT 100,
    study_points INT NOT NULL DEFAULT 0,            -- 学习积分（用于商城兑换）
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 1.2 阅览室表 (rooms)
CREATE TABLE rooms (
    room_id INT AUTO_INCREMENT PRIMARY KEY,
    room_name VARCHAR(100) NOT NULL UNIQUE,
    location VARCHAR(200),
    open_time TIME NOT NULL DEFAULT '08:00:00',
    close_time TIME NOT NULL DEFAULT '22:00:00',
    status TINYINT NOT NULL DEFAULT 1, -- 1: 开放, 0: 关闭
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 1.3 座位表 (seats)
CREATE TABLE seats (
    seat_id INT AUTO_INCREMENT PRIMARY KEY,
    room_id INT NOT NULL,
    seat_no VARCHAR(20) NOT NULL,
    has_power TINYINT(1) DEFAULT 0,
    status ENUM(
        'available',
        'reserved',
        'occupied',
        'maintenance'
    ) DEFAULT 'available',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (room_id) REFERENCES rooms (room_id) ON DELETE CASCADE,
    UNIQUE KEY uk_room_seat (room_id, seat_no)
);

-- 1.4 预约记录表 (reservations)
CREATE TABLE reservations (
    reservation_id INT AUTO_INCREMENT PRIMARY KEY,
    student_id VARCHAR(20) NOT NULL,
    seat_id INT NOT NULL,
    reserve_date DATE NOT NULL,
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    status ENUM(
        'pending',
        'active',
        'away',
        'completed',
        'cancelled',
        'violated'
    ) DEFAULT 'pending',
    checkin_time DATETIME,
    checkout_time DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES students (student_id) ON DELETE CASCADE ON UPDATE CASCADE,
    FOREIGN KEY (seat_id) REFERENCES seats (seat_id) ON DELETE CASCADE
);

-- 1.5 违约记录表 (violation_logs)
CREATE TABLE violation_logs (
    log_id INT AUTO_INCREMENT PRIMARY KEY,
    student_id VARCHAR(20) NOT NULL,
    reservation_id INT,
    violation_type ENUM(
        'no_show',
        'late',
        'leave_early',
        'away_overtime',
        'noise',
        'other'
    ) NOT NULL,
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES students (student_id) ON DELETE CASCADE ON UPDATE CASCADE,
    FOREIGN KEY (reservation_id) REFERENCES reservations (reservation_id) ON DELETE SET NULL
);

-- 1.6 信用变动流水表 (credit_logs)
CREATE TABLE credit_logs (
    log_id INT AUTO_INCREMENT PRIMARY KEY,
    student_id VARCHAR(20) NOT NULL,
    score_change INT NOT NULL, -- 正数增加，负数扣除
    reason VARCHAR(255) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES students (student_id) ON DELETE CASCADE ON UPDATE CASCADE
);

-- 1.7 黑名单表 (blacklist)
CREATE TABLE blacklist (
    blacklist_id INT AUTO_INCREMENT PRIMARY KEY,
    student_id VARCHAR(20) NOT NULL,
    reason VARCHAR(255),
    start_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    end_time DATETIME,
    is_active TINYINT(1) DEFAULT 1,
    FOREIGN KEY (student_id) REFERENCES students (student_id) ON DELETE CASCADE ON UPDATE CASCADE
);

-- 1.8 积分商城商品表 (products) - NEW
CREATE TABLE products (
    product_id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    points_required INT NOT NULL,
    stock INT NOT NULL DEFAULT 0,
    version INT NOT NULL DEFAULT 0, -- 乐观锁版本号：每次库存变动 +1，用于并发冲突检测
    image_url VARCHAR(255),
    status TINYINT NOT NULL DEFAULT 1, -- 1: 上架, 0: 下架
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 1.9 积分兑换订单表 (exchange_orders) - NEW
CREATE TABLE exchange_orders (
    order_id INT AUTO_INCREMENT PRIMARY KEY,
    student_id VARCHAR(20) NOT NULL,
    product_id INT NOT NULL,
    points_deducted INT NOT NULL,
    status ENUM(
        'pending',
        'completed',
        'cancelled'
    ) DEFAULT 'completed',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES students (student_id) ON DELETE CASCADE ON UPDATE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products (product_id) ON DELETE CASCADE
);

-- 1.10 设备报修工单表 (repair_tickets) - NEW
CREATE TABLE repair_tickets (
    ticket_id INT AUTO_INCREMENT PRIMARY KEY,
    student_id VARCHAR(20) NOT NULL,
    room_id INT NOT NULL,
    seat_id INT,
    description TEXT NOT NULL,
    status ENUM(
        'pending',
        'processing',
        'resolved',
        'closed'
    ) DEFAULT 'pending',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES students (student_id) ON DELETE CASCADE ON UPDATE CASCADE,
    FOREIGN KEY (room_id) REFERENCES rooms (room_id) ON DELETE CASCADE,
    FOREIGN KEY (seat_id) REFERENCES seats (seat_id) ON DELETE SET NULL
);

-- 1.11 每日自习统计表 (daily_study_stats) - NEW
CREATE TABLE daily_study_stats (
    stat_date DATE PRIMARY KEY,
    total_reservations INT DEFAULT 0,
    completed_reservations INT DEFAULT 0,
    total_violations INT DEFAULT 0,
    total_study_hours DECIMAL(10, 2) DEFAULT 0.00,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 1.12 管理员表 (admins) - NEW
CREATE TABLE admins (
    admin_id      INT AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    real_name     VARCHAR(50),
    role          ENUM('super', 'staff') DEFAULT 'staff',
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 1.13 系统公告表 (announcements) - NEW
CREATE TABLE announcements (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    title      VARCHAR(200) NOT NULL,
    content    TEXT NOT NULL,
    admin_id   INT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (admin_id) REFERENCES admins (admin_id) ON DELETE SET NULL
);

-- 1.14 操作审计日志表 (system_logs) - NEW
CREATE TABLE system_logs (
    log_id     INT AUTO_INCREMENT PRIMARY KEY,
    log_type   ENUM('credit_change', 'exchange', 'blacklist', 'admin_op', 'login') NOT NULL,
    student_id VARCHAR(20),
    operator   VARCHAR(50),   -- 操作者（学号或 admin 用户名）
    content    TEXT NOT NULL, -- 日志内容描述
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_log_type (log_type),
    INDEX idx_log_student (student_id)
);

-- =========================================================
-- 2. 视图 (Views)
-- =========================================================

-- 2.1 原有视图：实时座位统计
CREATE VIEW v_room_status AS
SELECT
    r.room_id,
    r.room_name,
    r.location,
    r.open_time,
    r.close_time,
    r.status AS room_status,
    COUNT(s.seat_id) AS total_seats,
    SUM(
        CASE
            WHEN s.status = 'available' THEN 1
            ELSE 0
        END
    ) AS available_seats,
    SUM(
        CASE
            WHEN s.status = 'reserved' THEN 1
            ELSE 0
        END
    ) AS reserved_seats,
    SUM(
        CASE
            WHEN s.status = 'occupied' THEN 1
            ELSE 0
        END
    ) AS occupied_seats,
    SUM(
        CASE
            WHEN s.status = 'maintenance' THEN 1
            ELSE 0
        END
    ) AS maintenance_seats
FROM rooms r
    LEFT JOIN seats s ON r.room_id = s.room_id
GROUP BY
    r.room_id;

-- 2.2 新增视图：Dashboard 综合统计
CREATE VIEW v_dashboard_stats AS
SELECT
    r.room_name,
    COUNT(s.seat_id) AS total_seats,
    SUM(
        CASE
            WHEN s.status IN ('occupied', 'reserved') THEN 1
            ELSE 0
        END
    ) AS in_use_seats,
    ROUND(
        SUM(
            CASE
                WHEN s.status IN ('occupied', 'reserved') THEN 1
                ELSE 0
            END
        ) / NULLIF(COUNT(s.seat_id), 0) * 100,
        2
    ) AS occupancy_rate
FROM rooms r
    LEFT JOIN seats s ON r.room_id = s.room_id
GROUP BY
    r.room_id;

-- =========================================================
-- 3. 存储过程与触发器
-- =========================================================
DELIMITER $$

-- 3.1 预约座位存储过程 (带学号校验与时间冲突校验)
CREATE PROCEDURE sp_reserve_seat (
    IN  p_student_id  VARCHAR(20),
    IN  p_seat_id     INT,
    IN  p_date        DATE,
    IN  p_start_time  TIME,
    IN  p_end_time    TIME,
    OUT p_code        INT,
    OUT p_message     VARCHAR(255)
)
sp_reserve_seat_label: BEGIN
    DECLARE v_stu_count   INT DEFAULT 0;
    DECLARE v_bl_count    INT DEFAULT 0;
    DECLARE v_seat_status VARCHAR(20);
    DECLARE v_conflict    INT DEFAULT 0;
    DECLARE v_stu_conflict INT DEFAULT 0;
    DECLARE v_room_open   TINYINT DEFAULT 1;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        SET p_code = 99;
        SET p_message = '系统内部错误，预约事务已回滚';
    END;

    START TRANSACTION;

    SELECT COUNT(*) INTO v_stu_count FROM students WHERE student_id = p_student_id;
    IF v_stu_count = 0 THEN
        ROLLBACK;
        SET p_code = 5;
        SET p_message = CONCAT('预约失败：学号 [', p_student_id, '] 不存在，请确认后重试');
        LEAVE sp_reserve_seat_label;
    END IF;

    SELECT COUNT(*) INTO v_bl_count FROM blacklist WHERE student_id = p_student_id AND is_active = 1;
    IF v_bl_count > 0 THEN
        ROLLBACK;
        SET p_code = 1;
        SET p_message = '预约失败：账号已被列入黑名单';
        LEAVE sp_reserve_seat_label;
    END IF;

    -- 检查学生自身在此时段是否已有其他预约，防止刷单占座
    SELECT COUNT(*) INTO v_stu_conflict FROM reservations
    WHERE student_id = p_student_id AND reserve_date = p_date
      AND status NOT IN ('cancelled', 'completed', 'violated')
      AND (p_start_time < end_time AND p_end_time > start_time);

    IF v_stu_conflict > 0 THEN
        ROLLBACK;
        SET p_code = 6;
        SET p_message = '预约失败：您在该时段已有其他预约，不能重复预约';
        LEAVE sp_reserve_seat_label;
    END IF;

    SELECT s.status, r.status INTO v_seat_status, v_room_open
    FROM seats s JOIN rooms r ON s.room_id = r.room_id
    WHERE s.seat_id = p_seat_id FOR UPDATE;

    IF v_room_open = 0 THEN
        ROLLBACK;
        SET p_code = 4;
        SET p_message = '预约失败：该阅览室当前未开放';
        LEAVE sp_reserve_seat_label;
    END IF;

    IF v_seat_status != 'available' THEN
        ROLLBACK;
        SET p_code = 2;
        SET p_message = CONCAT('预约失败：座位状态为 [', v_seat_status, ']');
        LEAVE sp_reserve_seat_label;
    END IF;

    -- 检查座位在此时段是否已被别人预约
    SELECT COUNT(*) INTO v_conflict FROM reservations
    WHERE seat_id = p_seat_id AND reserve_date = p_date
      AND status NOT IN ('cancelled', 'completed', 'violated')
      AND (p_start_time < end_time AND p_end_time > start_time);

    IF v_conflict > 0 THEN
        ROLLBACK;
        SET p_code = 3;
        SET p_message = '预约失败：该座位在此时段已被预约';
        LEAVE sp_reserve_seat_label;
    END IF;

    UPDATE seats SET status = 'reserved' WHERE seat_id = p_seat_id;
    INSERT INTO reservations (student_id, seat_id, reserve_date, start_time, end_time, status)
    VALUES (p_student_id, p_seat_id, p_date, p_start_time, p_end_time, 'pending');

    COMMIT;
    SET p_code = 0;
    SET p_message = CONCAT('预约成功，编号：', LAST_INSERT_ID());
END$$

-- 3.2 积分商城兑换存储过程 (乐观锁版本，高并发防超卖)
-- 架构说明：
--   商品表引入 version 字段作为乐观锁版本号。
--   学生积分扣减仍用 FOR UPDATE（学生行数少，悲观锁代价低）。
--   商品库存改用 CAS (Compare-And-Swap) 模式：
--     UPDATE products SET stock=stock-1, version=version+1
--     WHERE product_id=X AND version=current_version
--   若 ROW_COUNT()=0 则说明有并发冲突，返回 409 让调用方重试。
CREATE PROCEDURE sp_exchange_product(
    IN  p_student_id VARCHAR(20),
    IN  p_product_id INT,
    OUT p_code       INT,
    OUT p_msg        VARCHAR(255)
)
sp_exchange_label: BEGIN
    DECLARE v_stu_pts   INT;
    DECLARE v_prod_stock INT;
    DECLARE v_req_pts   INT;
    DECLARE v_status    TINYINT;
    DECLARE v_version   INT;   -- 读取到的当前版本号
    DECLARE v_affected  INT;   -- CAS 更新影响行数

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        SET p_code = 500;
        SET p_msg = '内部错误，兑换已回滚';
    END;

    START TRANSACTION;

    -- Step 1：锁定学生积分行（学生行少，悲观锁代价可接受）
    SELECT study_points INTO v_stu_pts
    FROM students WHERE student_id = p_student_id FOR UPDATE;
    IF v_stu_pts IS NULL THEN
        ROLLBACK; SET p_code = 404; SET p_msg = '学号不存在'; LEAVE sp_exchange_label;
    END IF;

    -- Step 2：读取商品信息（普通快照读，不加锁）
    SELECT stock, points_required, status, version
    INTO   v_prod_stock, v_req_pts, v_status, v_version
    FROM   products WHERE product_id = p_product_id;

    IF v_status = 0 THEN
        ROLLBACK; SET p_code = 400; SET p_msg = '商品已下架'; LEAVE sp_exchange_label;
    END IF;
    IF v_prod_stock <= 0 THEN
        ROLLBACK; SET p_code = 400; SET p_msg = '商品库存不足'; LEAVE sp_exchange_label;
    END IF;
    IF v_stu_pts < v_req_pts THEN
        ROLLBACK; SET p_code = 400; SET p_msg = '学习积分不足'; LEAVE sp_exchange_label;
    END IF;

    -- Step 3：CAS 原子扣减库存（乐观锁核心）
    -- 只有 version 未被其他事务修改时才能更新成功
    UPDATE products
    SET    stock   = stock - 1,
           version = version + 1
    WHERE  product_id = p_product_id
      AND  version    = v_version;  -- 版本匹配才执行

    SET v_affected = ROW_COUNT();

    -- Step 4：检测并发冲突（CAS 失败 → 409 冲突）
    IF v_affected = 0 THEN
        ROLLBACK;
        SET p_code = 409;
        SET p_msg = '并发冲突，请重试';
        LEAVE sp_exchange_label;
    END IF;

    -- Step 5：扣减积分 & 写入兑换订单
    UPDATE students
    SET    study_points = study_points - v_req_pts
    WHERE  student_id = p_student_id;

    INSERT INTO exchange_orders (student_id, product_id, points_deducted, status)
    VALUES (p_student_id, p_product_id, v_req_pts, 'completed');

    COMMIT;
    SET p_code = 200;
    SET p_msg = '兑换成功';
END$$

-- 3.3 触发器：扣除信用分自动加入黑名单
CREATE TRIGGER trg_credit_deduct
AFTER INSERT ON credit_logs
FOR EACH ROW
BEGIN
    DECLARE current_score INT;
    SELECT credit_score INTO current_score FROM students WHERE student_id = NEW.student_id;
    IF current_score < 0 THEN
        IF NOT EXISTS (SELECT 1 FROM blacklist WHERE student_id = NEW.student_id AND is_active = 1) THEN
            INSERT INTO blacklist (student_id, reason, is_active)
            VALUES (NEW.student_id, CONCAT('信用分降至 ', current_score, '，系统自动拉黑'), 1);
        END IF;
    END IF;
END$$

-- 3.4 存储过程：清理超时暂离 (供 Event 调度) - NEW
CREATE PROCEDURE sp_cleanup_away_overtime()
BEGIN
    -- 将暂离超过 2 小时的状态改为违约
    UPDATE reservations 
    SET status = 'violated' 
    WHERE status = 'away' AND updated_at < DATE_SUB(NOW(), INTERVAL 2 HOUR);
    
    -- 座位状态释放逻辑可在此扩展...
END$$

-- 3.5 触发器：信用分变动自动写入审计日志 - NEW
CREATE TRIGGER trg_audit_credit_change
AFTER UPDATE ON students
FOR EACH ROW
BEGIN
    IF NEW.credit_score <> OLD.credit_score THEN
        INSERT INTO system_logs (log_type, student_id, operator, content)
        VALUES (
            'credit_change',
            NEW.student_id,
            'system',
            CONCAT('学生[', NEW.student_id, ']信用分变化：', OLD.credit_score, ' → ', NEW.credit_score,
                   '（变动 ', (NEW.credit_score - OLD.credit_score), ' 分）')
        );
    END IF;
END$$

-- 3.6 触发器：积分兑换自动写入审计日志 - NEW
CREATE TRIGGER trg_audit_exchange
AFTER INSERT ON exchange_orders
FOR EACH ROW
BEGIN
    DECLARE v_prod_name VARCHAR(100);
    SELECT name INTO v_prod_name FROM products WHERE product_id = NEW.product_id;
    INSERT INTO system_logs (log_type, student_id, operator, content)
    VALUES (
        'exchange',
        NEW.student_id,
        NEW.student_id,
        CONCAT('学生[', NEW.student_id, ']兑换商品「', v_prod_name, '」，消耗积分 ', NEW.points_deducted, ' 分')
    );
END$$

DELIMITER ;

-- =========================================================
-- 4. 定时事件 (Event Scheduler) - NEW
-- =========================================================
-- 每天凌晨 2 点执行超时暂离清理
CREATE EVENT ev_cleanup_away_overtime
ON SCHEDULE EVERY 1 DAY STARTS CONCAT(CURDATE() + INTERVAL 1 DAY, ' 02:00:00')
DO CALL sp_cleanup_away_overtime();

-- =========================================================
-- 5. 初始化测试数据
-- =========================================================

-- 5.1 阅览室
INSERT INTO
    rooms (
        room_id,
        room_name,
        location,
        open_time,
        close_time,
        status
    )
VALUES (
        1,
        '图书馆1楼自习室',
        '图书馆一楼',
        '08:00:00',
        '22:00:00',
        1
    ),
    (
        2,
        '明德楼自习室',
        '明德楼3楼',
        '08:00:00',
        '22:00:00',
        1
    ),
    (
        3,
        '临江楼自习室',
        '临江楼2楼',
        '08:00:00',
        '22:00:00',
        1
    ),
    (
        4,
        '图书馆6楼自习室',
        '图书馆六楼',
        '08:00:00',
        '22:00:00',
        1
    ),
    (
        5,
        '文德楼自习室',
        '文德楼4楼',
        '08:30:00',
        '21:30:00',
        1
    ),
    (
        6,
        '阅江楼自习室',
        '阅江楼2楼',
        '08:00:00',
        '22:00:00',
        1
    );

-- 5.2 批量生成座位数据
DELIMITER $$

CREATE PROCEDURE tmp_gen_seats(IN rid INT, IN total INT)
BEGIN
    DECLARE i INT DEFAULT 1;
    DECLARE row_letter CHAR(1);
    DECLARE col_num INT;
    DECLARE seat_label VARCHAR(10);
    DECLARE has_pwr TINYINT;
    WHILE i <= total DO
        SET row_letter = ELT(FLOOR((i-1)/10) + 1, 'A','B','C','D','E','F','G','H','I','J','K','L','M','N','O','P','Q','R','S','T');
        SET col_num = ((i-1) MOD 10) + 1;
        SET seat_label = CONCAT(row_letter, '-', LPAD(col_num, 2, '0'));
        SET has_pwr = IF(col_num <= 5, 1, 0);
        INSERT INTO seats (room_id, seat_no, has_power, status)
        VALUES (rid, seat_label, has_pwr, 'available');
        SET i = i + 1;
    END WHILE;
END$$

DELIMITER ;

CALL tmp_gen_seats (1, 100);

CALL tmp_gen_seats (2, 120);

CALL tmp_gen_seats (3, 80);

CALL tmp_gen_seats (4, 150);

CALL tmp_gen_seats (5, 120);

CALL tmp_gen_seats (6, 100);

DROP PROCEDURE tmp_gen_seats;

-- 5.3 学生数据 (包含积分和密码哈希，默认密码均为 123456)
-- bcrypt hash of '123456': $2b$10$/ij32ZpmSjj6tAukH5uzLOrSGSQKpK6GMCLsrFfv.VQDjI3p1lSkm
INSERT INTO
    students (
        student_id,
        name,
        email,
        phone,
        password_hash,
        credit_score,
        study_points
    )
VALUES (
        '202213020001',
        '张明远',
        '202213020001@nuist.edu.cn',
        '13901000001',
        '$2b$10$/ij32ZpmSjj6tAukH5uzLOrSGSQKpK6GMCLsrFfv.VQDjI3p1lSkm',
        100,
        500
    ),
    (
        '202313020001',
        '陈晓燕',
        '202313020001@nuist.edu.cn',
        '13901000011',
        '$2b$10$/ij32ZpmSjj6tAukH5uzLOrSGSQKpK6GMCLsrFfv.VQDjI3p1lSkm',
        95,
        200
    ),
    (
        '202413020060',
        '吴思远',
        '202413020060@nuist.edu.cn',
        '13901000021',
        '$2b$10$/ij32ZpmSjj6tAukH5uzLOrSGSQKpK6GMCLsrFfv.VQDjI3p1lSkm',
        100,
        1500
    ),
    (
        '202413020061',
        '孙雨桐',
        '202413020061@nuist.edu.cn',
        '13901000022',
        '$2b$10$/ij32ZpmSjj6tAukH5uzLOrSGSQKpK6GMCLsrFfv.VQDjI3p1lSkm',
        80,
        50
    ),
    (
        '202411030001',
        '蒋宇航',
        '202411030001@nuist.edu.cn',
        '13901000041',
        '$2b$10$/ij32ZpmSjj6tAukH5uzLOrSGSQKpK6GMCLsrFfv.VQDjI3p1lSkm',
        -5,
        0
    );

-- 5.3.1 管理员账号 (admin / Admin@123)
-- bcrypt hash of 'Admin@123': $2b$10$ya4fQrL5V0HCdNndVuRj7O9WLVJUHsH0ucem6plvvytGLfQbVhm/m
INSERT INTO admins (username, password_hash, real_name, role)
VALUES
    ('admin', '$2b$10$ya4fQrL5V0HCdNndVuRj7O9WLVJUHsH0ucem6plvvytGLfQbVhm/m', '超级管理员', 'super'),
    ('staff01', '$2b$10$ya4fQrL5V0HCdNndVuRj7O9WLVJUHsH0ucem6plvvytGLfQbVhm/m', '图书馆管理员', 'staff');

-- 5.3.2 初始系统公告
INSERT INTO announcements (title, content, admin_id) VALUES
    ('欢迎使用南信大自习空间管理系统', '本系统支持在线座位预约、积分兑换、设备报修等功能。请遵守自习室规定，维护良好的学习环境！', 1),
    ('考试周通宵自习室开放通知', '2026年6月17日至6月28日考试周期间，图书馆6楼自习室将延长开放至次日凌晨2点，欢迎同学们合理利用。', 1),
    ('关于爽约扣分规则的温馨提示', '预约后请务必在开始时间后20分钟内完成签到，否则系统将自动记为爽约并扣除10信用分，座位将同时释放供他人使用。', 1);

-- 5.4 积分商城商品
INSERT INTO
    products (
        name,
        description,
        points_required,
        stock,
        image_url
    )
VALUES (
        '塔斯汀套餐',
        '可在校内和气象谷门店使用，包含指定汉堡与饮品。',
        1500,
        20,
        '/images/tastien.jpeg'
    ),
    (
        '瑞幸咖啡',
        '可在校内和气象谷门店使用，兑换任意常规咖啡一杯。',
        800,
        50,
        '/images/luckin.jpeg'
    ),
    (
        '蜜雪冰城饮品',
        '可在校内和气象谷门店使用，兑换指定饮品一杯。',
        500,
        100,
        '/images/mixue.jpeg'
    ),
    (
        'nuist稿纸',
        '南信大定制版稿纸一本，记录你的学习点滴。',
        300,
        200,
        '/images/paper.png'
    );

-- 5.5 统计图表假数据 (每日自习统计，折线图用)
INSERT INTO
    daily_study_stats (
        stat_date,
        total_reservations,
        completed_reservations,
        total_violations,
        total_study_hours
    )
VALUES (
        DATE_SUB(CURDATE(), INTERVAL 6 DAY),
        450,
        400,
        12,
        1200.5
    ),
    (
        DATE_SUB(CURDATE(), INTERVAL 5 DAY),
        520,
        480,
        8,
        1420.0
    ),
    (
        DATE_SUB(CURDATE(), INTERVAL 4 DAY),
        480,
        450,
        15,
        1310.2
    ),
    (
        DATE_SUB(CURDATE(), INTERVAL 3 DAY),
        600,
        550,
        5,
        1600.0
    ),
    (
        DATE_SUB(CURDATE(), INTERVAL 2 DAY),
        610,
        580,
        10,
        1650.5
    ),
    (
        DATE_SUB(CURDATE(), INTERVAL 1 DAY),
        590,
        560,
        7,
        1580.0
    ),
    (CURDATE(), 120, 30, 2, 210.0);

-- 制造真实的预约数据，使系统大屏和管理员界面能正常显示 (模拟占座率)
-- 正在使用中的座位
UPDATE seats SET status = 'occupied' WHERE seat_id IN (1, 2, 3, 101, 102, 221, 222);
INSERT INTO reservations (student_id, seat_id, reserve_date, start_time, end_time, status) VALUES
('202213020001', 1, CURDATE(), '08:00:00', '12:00:00', 'active'),
('202313020001', 2, CURDATE(), '09:00:00', '14:00:00', 'active'),
('202213020001', 3, CURDATE(), '10:00:00', '15:00:00', 'active'),
('202213020001', 101, CURDATE(), '08:30:00', '11:30:00', 'active'),
('202313020001', 102, CURDATE(), '09:30:00', '22:00:00', 'active'),
('202213020001', 221, CURDATE(), '08:00:00', '11:00:00', 'active'),
('202213020001', 222, CURDATE(), '10:00:00', '18:00:00', 'active');

-- 已被预约但还未签到的座位
UPDATE seats SET status = 'reserved' WHERE seat_id IN (5, 6, 7, 103, 104);
INSERT INTO reservations (student_id, seat_id, reserve_date, start_time, end_time, status) VALUES
('202213020001', 5, CURDATE(), '14:00:00', '18:00:00', 'pending'),
('202313020001', 6, CURDATE(), '16:00:00', '20:00:00', 'pending'),
('202213020001', 7, CURDATE(), '18:00:00', '21:00:00', 'pending'),
('202313020001', 103, CURDATE(), '19:00:00', '22:00:00', 'pending'),
('202213020001', 104, CURDATE(), '20:00:00', '22:00:00', 'pending');

-- =========================================================
-- 6. 性能优化与自动化维护
-- =========================================================

-- 6.1 索引优化 (Index)
-- 优化预约冲突查询与日常大屏查询性能
CREATE INDEX idx_reservations_date_seat ON reservations(reserve_date, seat_id);
CREATE INDEX idx_reservations_student ON reservations(student_id);
CREATE INDEX idx_logs_type ON system_logs(log_type);

-- 6.2 自动化维护定时任务 (Event Scheduler)
-- 自动清理已经过了结束时间但状态还是 active 的预约（视为正常结束自习，但未点击签退）
DELIMITER $$
CREATE EVENT evt_auto_complete_reservations
ON SCHEDULE EVERY 10 MINUTE
DO
BEGIN
    UPDATE reservations 
    SET status = 'completed' 
    WHERE status = 'active' 
      AND reserve_date <= CURDATE() 
      AND end_time <= CURTIME();
END$$
DELIMITER ;