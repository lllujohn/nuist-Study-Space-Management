# 灵犀（Lingxi）高校智能自习室管理与预约系统

> 数据库系统原理课程设计 | MySQL 9.x + FastAPI + Vue 3

---

## 🗂 项目结构

```
数据库课程设计/
├── init.sql          # 数据库初始化脚本（建表、视图、触发器、存储过程、测试数据）
├── main.py           # FastAPI 后端核心代码
├── requirements.txt  # Python 依赖包列表
├── .env              # 数据库连接配置（⚠️ 修改密码后再运行）
├── index.html        # 前端交互界面（Vue 3 单文件）
└── README.md         # 本文件
```

---

## 🚀 三步快速启动

### 第 1 步：初始化数据库

打开 MySQL 命令行或 MySQL Workbench，执行初始化脚本：

```bash
mysql -u root -p < init.sql
```

> **提示**：脚本会自动创建 `lingxi_db` 数据库，无需手动建库。

---

### 第 2 步：配置并启动后端

**2a. 修改数据库密码**

编辑 `.env` 文件，将 `DB_PASSWORD` 改为你本机 MySQL 的实际密码：

```env
DB_PASSWORD=your_actual_password
```

**2b. 安装 Python 依赖**

```bash
cd /path/to/数据库课程设计
pip install -r requirements.txt
```

**2c. 启动 FastAPI 服务**

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

启动成功后，访问 http://127.0.0.1:8000/docs 可查看交互式 API 文档（Swagger UI）。

---

### 第 3 步：打开前端页面

直接用浏览器打开 `index.html` 文件即可（无需额外服务器）：

```bash
open index.html   # macOS
# 或双击文件夹中的 index.html
```

---

## 📋 核心功能说明

### 数据库高级对象

| 对象 | 名称 | 功能 |
|------|------|------|
| 视图 | `v_room_status` | 实时统计每个阅览室的总座位、空闲、已约、使用中数量 |
| 触发器 | `trg_credit_deduct` | 插入违约记录时自动扣信用分，降至 ≤0 自动拉黑 |
| 存储过程 | `sp_reserve_seat` | 事务保证：黑名单检查 → 行锁 → 冲突检测 → 原子写入 |

### API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/api/rooms` | 查询 v_room_status 视图 |
| POST | `/api/reserve` | 调用 sp_reserve_seat 存储过程 |
| GET  | `/api/students/{id}` | 查询学生信用分与黑名单状态 |
| GET  | `/api/seats/{room_id}` | 查询指定阅览室座位列表 |
| POST | `/api/checkin` | 签到 / 暂离 / 签退 状态流转 |

### 测试账号

| 学号 | 姓名 | 信用分 | 状态 |
|------|------|--------|------|
| 2024001001 | 林夏 | 100 | 正常 |
| 2024001002 | 陈浩然 | 85 | 正常 |
| 2024001003 | 苏瑶 | 100 | 正常 |
| 2024001004 | 宋宇 | -5 | ⛔ 黑名单（触发器自动拉黑）|
| 2024001005 | 韩冰 | -10 | ⛔ 黑名单 |

---

## 🔑 高并发防超卖原理

存储过程 `sp_reserve_seat` 使用 **`SELECT ... FOR UPDATE`** 对目标座位行加排它锁，
配合 MySQL InnoDB 的事务机制，确保在高并发场景下同一座位不会被重复预约：

```sql
-- 锁定座位行，阻塞其他并发事务
SELECT s.status FROM seats s ... WHERE s.seat_id = p_seat_id FOR UPDATE;
-- 在锁保护下检查状态并更新
UPDATE seats SET status = 'reserved' WHERE seat_id = p_seat_id;
INSERT INTO reservations ...;
COMMIT; -- 提交后自动释放锁
```

---

## ⚠️ 常见问题

**Q：启动后端报错 `Access denied for user`？**
> 检查 `.env` 中的 `DB_USER` 和 `DB_PASSWORD` 是否正确。

**Q：前端提示"无法连接后端服务"？**
> 确认 uvicorn 已在 8000 端口运行，且未被防火墙拦截。

**Q：执行 `init.sql` 报 `ERROR 1064`？**
> 确认 MySQL 版本 ≥ 8.0，且执行时已切换到具有建库权限的账号。
