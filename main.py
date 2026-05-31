"""
南信大自习空间管理子系统 - FastAPI 后端 v4.0
================================================
重构要点：
  1. 依赖注入 (Depends + yield) 统一管理数据库 Session
  2. 全局异常处理器 - 生产级友好 JSON 错误响应
  3. Pydantic Response Models + 完整类型注解
  4. 标准化日志格式
  5. JWT 认证 + bcrypt 密码安全模块
  6. 管理员后台接口 + 审计日志
"""
import csv
import io
import logging
import os
import re
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Any, DefaultDict, Dict, Generator, List, Optional, Tuple

import bcrypt
import jwt

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

# =========================================================
# JWT 配置
# =========================================================
JWT_SECRET = "lingxi_secret_key_2026"
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24


def create_token(payload: Dict[str, Any]) -> str:
    """Create a JWT token that expires in JWT_EXPIRE_HOURS hours."""
    data = payload.copy()
    data["exp"] = datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS)
    return jwt.encode(data, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> Dict[str, Any]:
    """Verify and decode a JWT token. Raises HTTPException on failure."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    except Exception:
        raise HTTPException(status_code=401, detail="身份验证失败")


def get_current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    """Dependency: extract and validate JWT from Authorization header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录，请先登录")
    token = authorization.split(" ", 1)[1]
    return verify_token(token)


def require_admin(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    """Dependency: ensure caller is an admin."""
    user = get_current_user(authorization)
    if user.get("role") not in ("admin_super", "admin_staff"):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user

# =========================================================
# 日志配置 (Logging Configuration)
# =========================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("lingxi_api")

# =========================================================
# 数据库配置 (Database Configuration)
# =========================================================
load_dotenv()
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "88888888")
DB_NAME = os.getenv("DB_NAME", "lingxi_db")

DATABASE_URL = (
    f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    "?charset=utf8mb4"
)
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,  # 自动检测断开的连接
    echo=False,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# =========================================================
# 依赖注入：数据库 Session 工厂 (FastAPI Best Practice)
# =========================================================
def get_db() -> Generator[Session, None, None]:
    """
    FastAPI 依赖项：提供数据库 Session。
    使用 yield 确保每次请求结束后 Session 自动关闭，
    即使发生异常也能正确释放连接回连接池。
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =========================================================
# 应用生命周期 (Lifespan)
# =========================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动时验证数据库连接，关闭时释放连接池。"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("✅ 数据库连接成功 [%s]", DB_NAME)
    except Exception as e:
        logger.error("❌ 数据库连接失败: %s", e)
    yield
    engine.dispose()
    logger.info("🔒 数据库连接池已关闭")


# =========================================================
# FastAPI 应用实例
# =========================================================
app = FastAPI(
    title="南信大自习空间管理子系统 API",
    description="企业级 SaaS 座位预约、积分商城、设备报修综合系统",
    version="3.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载本地图片目录，使浏览器可通过 /images/xxx.jpeg 访问
_images_dir = os.path.join(os.path.dirname(__file__), "images")
if os.path.isdir(_images_dir):
    app.mount("/images", StaticFiles(directory=_images_dir), name="images")


# =========================================================
# 限流中间件 (Rate Limiting Middleware)
# 规则：/api/reserve 和 /api/exchange 这两个高并发写接口
#          同一 IP 地址每 5 秒内最多请求 3 次。
# 实现：基于内存的滑动窗口计数（单进程足够）
# =========================================================

# 存储: {ip -> [(timestamp1), (timestamp2), ...]}
_rate_limit_store: DefaultDict[str, List[float]] = defaultdict(list)

# 高并发写接口列表与限流配置
RATE_LIMITED_PATHS: Tuple[str, ...] = ("/api/reserve", "/api/exchange")
RATE_LIMIT_MAX     = 3    # 最大请求次数
RATE_LIMIT_WINDOW  = 5.0  # 时间窗口（秒）


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """
    全局限流中间件。

    针对 /api/reserve 和 /api/exchange 进行 IP 级别限流：
    同一 IP 在 5 秒内请求超过 3 次，返回 HTTP 429。
    """
    if any(request.url.path.startswith(p) for p in RATE_LIMITED_PATHS):
        client_ip = request.client.host if request.client else "unknown"
        now       = time.monotonic()

        # 清除过期的记录
        timestamps = _rate_limit_store[client_ip]
        _rate_limit_store[client_ip] = [
            t for t in timestamps if now - t < RATE_LIMIT_WINDOW
        ]

        if len(_rate_limit_store[client_ip]) >= RATE_LIMIT_MAX:
            logger.warning("限流触发 [IP=%s PATH=%s]", client_ip, request.url.path)
            return JSONResponse(
                status_code=429,
                content={"code": 429, "msg": "请求过于频繁，请稍后再试"},
            )

        _rate_limit_store[client_ip].append(now)

    return await call_next(request)


# =========================================================
# 全局异常处理器 (Global Exception Handlers)
# =========================================================
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    捕获所有未处理的服务器异常，返回标准化的 JSON 错误响应，
    避免将原始堆栈信息暴露给前端客户端。
    """
    logger.exception("未捕获的服务器异常 [%s %s]", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"code": 500, "msg": "服务器内部错误，请稍后重试", "detail": str(exc)},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """标准化 HTTPException 的响应格式，与业务 JSON 风格保持一致。"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "msg": exc.detail},
    )


# =========================================================
# 工具函数 (Utility Functions)
# =========================================================
STUDENT_ID_RE = re.compile(r"^202\d{9}$")


def api_ok(data: Any = None, msg: str = "success") -> Dict[str, Any]:
    """标准成功响应封装。"""
    resp: Dict[str, Any] = {"code": 200, "msg": msg}
    if data is not None:
        resp["data"] = data
    return resp


def api_err(msg: str, code: int = 400) -> Dict[str, Any]:
    """标准错误响应封装。"""
    return {"code": code, "msg": msg}


def validate_student_id(student_id: str) -> str:
    """
    验证学号格式：12 位数字，且以 202x 开头。

    :param student_id: 待验证的学号字符串
    :returns: 去除首尾空格后的合法学号
    :raises HTTPException: 格式不符时抛出 400
    """
    sid = student_id.strip()
    if not STUDENT_ID_RE.match(sid):
        raise HTTPException(
            status_code=400,
            detail="学号必须为 12 位数字，且以 202x 开头",
        )
    return sid


# =========================================================
# Pydantic 请求模型 (Request Models)
# =========================================================
class ReserveRequest(BaseModel):
    student_id: str
    seat_id: int
    reserve_date: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None

    @field_validator("student_id")
    @classmethod
    def check_student_id(cls, v: str) -> str:
        """Pydantic 层面的学号格式校验。"""
        if not STUDENT_ID_RE.match(v.strip()):
            raise ValueError("学号必须为 12 位数字，且以 202x 开头")
        return v.strip()


class ExchangeRequest(BaseModel):
    student_id: str
    product_id: int


class ReservationActionRequest(BaseModel):
    student_id: str
    action: str  # checkin | away | checkout | cancel


class RepairRequest(BaseModel):
    student_id: str
    room_id: int
    seat_id: Optional[int] = None
    description: str


class RegisterRequest(BaseModel):
    student_id: str
    name: str
    phone: str
    email: Optional[str] = None
    password: str

    @field_validator("student_id")
    @classmethod
    def check_sid(cls, v: str) -> str:
        if not STUDENT_ID_RE.match(v.strip()):
            raise ValueError("学号必须为 12 位数字，且以 202x 开头")
        return v.strip()


class LoginRequest(BaseModel):
    student_id: str
    password: str


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class ResetPasswordRequest(BaseModel):
    student_id: str
    phone: str
    new_password: str


class AnnouncementRequest(BaseModel):
    title: str
    content: str


class AdminCreditRequest(BaseModel):
    delta: int    # 正数增加，负数扣除
    reason: str


class AdminBlacklistRequest(BaseModel):
    action: str   # "ban" or "unban"
    reason: Optional[str] = None


class AdminTicketRequest(BaseModel):
    action: str   # "processing" | "resolved" | "closed"


class AdminSeatRequest(BaseModel):
    status: str   # "available" | "maintenance"


class AdminRoomRequest(BaseModel):
    room_name: str
    location: str
    open_time: str = "08:00:00"
    close_time: str = "22:00:00"


# =========================================================
# 前端页面入口 (Serve Frontend)
# =========================================================
@app.get("/", include_in_schema=False)
def serve_index() -> FileResponse:
    """
    直接托管 index.html，使用户可通过 http://localhost:8000/ 访问系统，
    所有图片、API 路径均基于同一域名，无需跨域或绝对路径处理。
    """
    response = FileResponse(
        os.path.join(os.path.dirname(__file__), "index.html"),
        media_type="text/html",
    )
    # 禁用前端 HTML 缓存，确保每次刷新都拿到最新代码
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# =========================================================
# 数据大屏接口 (Dashboard APIs)
# =========================================================
@app.get("/api/dashboard/stats", summary="获取 ECharts 大屏数据源")
def get_dashboard_stats(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    返回大屏所需数据：
    - 各阅览室实时占座率（饼/仪表盘图）
    - 近 7 日全校违约趋势（折线图）
    """
    occupancy = db.execute(
        text("SELECT room_name, occupancy_rate FROM v_dashboard_stats")
    ).mappings().all()

    trend = db.execute(
        text(
            "SELECT DATE_FORMAT(stat_date, '%m-%d') AS date, total_violations "
            "FROM daily_study_stats ORDER BY stat_date ASC LIMIT 7"
        )
    ).mappings().all()

    return api_ok({
        "occupancy": [dict(r) for r in occupancy],
        "trend": [dict(r) for r in trend],
    })


# =========================================================
# 阅览室与座位接口 (Rooms & Seats)
# =========================================================
@app.get("/api/rooms", summary="获取所有阅览室实时状态")
def get_rooms(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """查询所有阅览室的实时状态，包括空闲座位数。"""
    rows = db.execute(text("SELECT * FROM v_room_status")).mappings().all()
    return api_ok([dict(r) for r in rows])


@app.get("/api/seats/{room_id}", summary="获取指定阅览室的座位列表")
def get_seats(room_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    获取某一阅览室下所有座位的编号、电源情况与当前状态。

    :param room_id: 阅览室 ID
    """
    # 每次获取座位列表前，执行一次懒加载清理：将超过预约开始时间 20 分钟仍未签到的预约标记为爽约，并释放座位
    try:
        # 1. 爽约（逾期20分钟未签到）：-10分
        db.execute(text(
            "UPDATE reservations r "
            "INNER JOIN seats s ON r.seat_id = s.seat_id "
            "INNER JOIN students u ON r.student_id = u.student_id "
            "SET r.status = 'violated', s.status = 'available', u.credit_score = u.credit_score - 10 "
            "WHERE r.status = 'pending' "
            "AND CONCAT(r.reserve_date, ' ', r.start_time) < DATE_SUB(NOW(), INTERVAL 20 MINUTE)"
        ))
        # 2. 提前离开未结束自习（时间到了却没有手动签退）：-5分
        db.execute(text(
            "UPDATE reservations r "
            "INNER JOIN seats s ON r.seat_id = s.seat_id "
            "INNER JOIN students u ON r.student_id = u.student_id "
            "SET r.status = 'violated', s.status = 'available', u.credit_score = u.credit_score - 5 "
            "WHERE r.status = 'active' "
            "AND CONCAT(r.reserve_date, ' ', r.end_time) < NOW()"
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"清理逾期预约失败: {e}")

    rows = db.execute(
        text(
            "SELECT s.seat_id, s.seat_no, s.has_power, s.status, "
            "r.student_id, u.name as student_name, r.reserve_date, r.start_time, r.end_time "
            "FROM seats s "
            "LEFT JOIN reservations r ON s.seat_id = r.seat_id AND r.status IN ('pending', 'active', 'away') AND r.reserve_date IN (CURDATE(), DATE_ADD(CURDATE(), INTERVAL 1 DAY)) "
            "LEFT JOIN students u ON r.student_id = u.student_id "
            "WHERE s.room_id = :rid"
        ),
        {"rid": room_id},
    ).mappings().all()
    
    seats_dict = {}
    for r in rows:
        sid = r["seat_id"]
        if sid not in seats_dict:
            seats_dict[sid] = {
                "seat_id": sid,
                "seat_no": r["seat_no"],
                "has_power": r["has_power"],
                "status": r["status"],
                "room_id": room_id,
                "reservations": []
            }
        if r["student_id"]:
            seats_dict[sid]["reservations"].append({
                "student_id": r["student_id"],
                "student_name": r["student_name"],
                "reserve_date": r["reserve_date"],
                "start_time": r["start_time"],
                "end_time": r["end_time"]
            })
            
    return api_ok(list(seats_dict.values()))


@app.post("/api/reserve", summary="提交座位预约（调用存储过程）")
def reserve_seat(req: ReserveRequest) -> Dict[str, Any]:
    """
    调用存储过程 sp_reserve_seat 完成原子性预约：
    - 学号存在检查
    - 黑名单检查
    - 座位状态 + 时间冲突检查
    - 自动写入 reservations 并更新 seats.status

    注：存储过程使用事务，需通过 raw_connection 调用。
    """
    reserve_date = req.reserve_date or date.today().isoformat()
    start_time   = req.start_time   or "08:00:00"
    end_time     = req.end_time     or "10:00:00"

    from datetime import datetime
    try:
        fmt = "%Y-%m-%d %H:%M:%S" if start_time.count(':') == 2 else "%Y-%m-%d %H:%M"
        req_dt = datetime.strptime(f"{reserve_date} {start_time}", fmt)
        if req_dt < datetime.now():
            return {"code": 400, "msg": "预约失败：不能预约已经过去的时间段"}
    except Exception as e:
        logger.error(f"时间解析错误: {e}")

    raw_conn = engine.raw_connection()
    try:
        cursor = raw_conn.cursor()
        cursor.execute("SET @p_code = 0, @p_message = ''")
        cursor.execute(
            "CALL sp_reserve_seat(%s, %s, %s, %s, %s, @p_code, @p_message)",
            (req.student_id, req.seat_id, reserve_date, start_time, end_time),
        )
        cursor.execute("SELECT @p_code AS code, @p_message AS message")
        out = cursor.fetchone()
        raw_conn.commit()

        code, msg = out[0], out[1]
        return {"code": 200 if code == 0 else 400, "msg": msg}
    except Exception:
        raw_conn.rollback()
        logger.exception("预约座位存储过程异常")
        raise
    finally:
        cursor.close()
        raw_conn.close()


@app.get("/api/reservations/{student_id}", summary="查询学生的预约记录")
def get_reservations(student_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    查询某学生的全部预约历史，按创建时间倒序。

    :param student_id: 12 位学号
    """
    sid = validate_student_id(student_id)
    rows = db.execute(
        text(
            """
            SELECT
                res.reservation_id,
                res.reserve_date,
                res.start_time,
                res.end_time,
                res.status,
                r.room_name,
                s.seat_no
            FROM reservations res
            JOIN seats s ON res.seat_id = s.seat_id
            JOIN rooms  r ON s.room_id  = r.room_id
            WHERE res.student_id = :sid
            ORDER BY res.created_at DESC
            """
        ),
        {"sid": sid},
    ).mappings().all()

    data: List[Dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        d["reserve_date"] = str(d["reserve_date"])
        d["start_time"]   = str(d["start_time"])
        d["end_time"]     = str(d["end_time"])
        data.append(d)

    return api_ok(data)


@app.post(
    "/api/reservations/{reservation_id}/action",
    summary="操作预约状态（签到 / 暂离 / 结束 / 取消）",
)
def handle_reservation_action(
    reservation_id: int,
    req: ReservationActionRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    预约状态机流转：
    - pending  → checkin → active
    - active   → away    → away
    - away     → checkin → active
    - active / away → checkout → completed
    - pending  → cancel  → cancelled

    :param reservation_id: 预约记录 ID
    :param req: 包含 student_id 和 action 的请求体
    """
    sid = validate_student_id(req.student_id)

    valid_actions = {
        "checkin":  "active",
        "away":     "away",
        "checkout": "completed",
        "cancel":   "cancelled",
    }
    if req.action not in valid_actions:
        raise HTTPException(status_code=400, detail="无效的操作类型")

    # 查询当前预约（FOR UPDATE 加行锁防止并发冲突）
    res = db.execute(
        text(
            "SELECT status, seat_id, checkin_time, "
            "CONCAT(reserve_date, ' ', start_time) AS start_dt, "
            "CONCAT(reserve_date, ' ', end_time) AS end_dt "
            "FROM reservations "
            "WHERE reservation_id = :rid AND student_id = :sid "
            "FOR UPDATE"
        ),
        {"rid": reservation_id, "sid": sid},
    ).mappings().first()

    if not res:
        raise HTTPException(status_code=404, detail="找不到该预约或无权限")

    current_status = res["status"]
    new_status     = valid_actions[req.action]

    from datetime import datetime
    now = datetime.now()

    # 懒处理：如果仍在 pending 状态，但时间已经完全超过了预约开始时间 20 分钟，则直接记为爽约
    if current_status == "pending":
        from datetime import timedelta
        start_dt = datetime.strptime(res["start_dt"], "%Y-%m-%d %H:%M:%S")
        if now > start_dt + timedelta(minutes=20):
            db.execute(text("UPDATE reservations SET status = 'violated' WHERE reservation_id = :rid"), {"rid": reservation_id})
            db.execute(text("UPDATE students SET credit_score = credit_score - 10 WHERE student_id = :sid"), {"sid": sid})
            db.execute(text("UPDATE seats SET status = 'available' WHERE seat_id = :seat_id"), {"seat_id": res["seat_id"]})
            db.commit()
            raise HTTPException(status_code=400, detail="该预约已逾期20分钟未签到，系统已自动记为【爽约】并扣除10信用分。座位已释放。")

    # 状态机合法性校验
    if req.action == "checkin"  and current_status not in ("pending", "away"):
        raise HTTPException(status_code=400, detail="当前状态无法执行签到")
    if req.action == "checkin" and current_status == "pending":
        from datetime import timedelta
        start_dt = datetime.strptime(res["start_dt"], "%Y-%m-%d %H:%M:%S")
        if now < start_dt - timedelta(minutes=15):
            raise HTTPException(status_code=400, detail="太早了！只能在预约时间前 15 分钟内签到。")
        if now < start_dt:
            # 提前签到：检查座位是否还有上一位同学（未到期且未离开）
            overlap = db.execute(
                text(
                    "SELECT 1 FROM reservations "
                    "WHERE seat_id = :seat_id "
                    "AND status IN ('active', 'away') "
                    "AND reservation_id != :rid "
                    "AND CONCAT(reserve_date, ' ', end_time) > NOW() "
                    "LIMIT 1"
                ),
                {"seat_id": res["seat_id"], "rid": reservation_id}
            ).fetchone()
            if overlap:
                raise HTTPException(status_code=400, detail="上一位同学还未结束自习，请等对方离开后再提前签到。")
    if req.action == "away"     and current_status != "active":
        raise HTTPException(status_code=400, detail="仅自习中状态可暂离")
    if req.action == "cancel"   and current_status != "pending":
        raise HTTPException(status_code=400, detail="只能取消待签到的预约")
    if req.action == "checkout" and current_status not in ("active", "away"):
        raise HTTPException(status_code=400, detail="当前状态无法结束自习")

    # 构建动态 SET 子句
    updates = ["status = :status"]
    params: Dict[str, Any] = {"status": new_status, "rid": reservation_id}

    if req.action == "checkin" and current_status == "pending":
        updates.append("checkin_time = NOW()")
    if req.action == "checkout":
        updates.append("checkout_time = NOW()")
        # 计算有效学习积分：每小时 5 积分
        checkin_time = res["checkin_time"]
        if checkin_time:
            db.execute(
                text(
                    "UPDATE students "
                    "SET study_points = study_points + GREATEST(0, ROUND(TIMESTAMPDIFF(MINUTE, :checkin, NOW()) / 60.0 * 5)) "
                    "WHERE student_id = :sid"
                ),
                {"checkin": checkin_time, "sid": sid},
            )

    db.execute(
        text(f"UPDATE reservations SET {', '.join(updates)} WHERE reservation_id = :rid"),
        params,
    )


    # 同步更新座位状态
    seat_status: str
    if new_status in ("active", "away"):
        seat_status = "occupied"
    else:
        seat_status = "available"

    db.execute(
        text("UPDATE seats SET status = :ss WHERE seat_id = :seat_id"),
        {"ss": seat_status, "seat_id": res["seat_id"]},
    )
    db.commit()

    return api_ok(msg="操作成功")


# =========================================================
# 数据报表导出接口 (Data Export)
# =========================================================
@app.get("/api/export/stats", summary="导出每日自习统计为 CSV 文件")
def export_stats_csv(db: Session = Depends(get_db)) -> StreamingResponse:
    """
    将 daily_study_stats 表的数据转换为 CSV 格式流式下载。

    - 设置 Content-Disposition: attachment 头，浏览器直接展示下载对话框。
    - 使用 StreamingResponse 避免大数据集时内存渢出。
    """
    rows = db.execute(
        text(
            "SELECT stat_date, total_reservations, completed_reservations, "
            "total_violations, total_study_hours "
            "FROM daily_study_stats ORDER BY stat_date ASC"
        )
    ).mappings().all()

    def _csv_generator():
        """CSV 行生成器，每行完成后即将数据 flush 到网络。"""
        buf = io.StringIO()
        writer = csv.DictWriter(
            buf,
            fieldnames=["stat_date", "total_reservations", "completed_reservations",
                        "total_violations", "total_study_hours"],
        )
        # 写入中文表头
        buf.write("日期,总预约数,完成数,违约次数,自习总时数(h)\n")
        yield buf.getvalue()

        for row in rows:
            buf = io.StringIO()
            writer = csv.DictWriter(
                buf,
                fieldnames=["stat_date", "total_reservations", "completed_reservations",
                            "total_violations", "total_study_hours"],
            )
            writer.writerow(dict(row))
            yield buf.getvalue()

    filename = f"study_stats_{date.today().isoformat()}.csv"
    return StreamingResponse(
        _csv_generator(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# =========================================================
# 学生、信用、积分接口 (Students)
# =========================================================
@app.get("/api/students/{student_id}", summary="查询学生信息（含信用分、积分与违约历史）")
def get_student(student_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    综合查询学生档案：
    - 基本信息（学号、姓名、邮箱）
    - 信用分与学习积分
    - 是否在黑名单及原因
    - 最近 5 条违约记录

    :param student_id: 12 位学号
    """
    sid = validate_student_id(student_id)

    stu = db.execute(
        text(
            "SELECT student_id, name, email, credit_score, study_points "
            "FROM students WHERE student_id = :sid"
        ),
        {"sid": sid},
    ).mappings().first()

    if not stu:
        raise HTTPException(status_code=404, detail="未找到该学生信息")

    # 黑名单状态
    bl = db.execute(
        text(
            "SELECT reason FROM blacklist "
            "WHERE student_id = :sid AND is_active = 1"
        ),
        {"sid": sid},
    ).mappings().first()

    # 近期违约记录（最多 5 条）
    v_logs = db.execute(
        text(
            "SELECT violation_type, created_at "
            "FROM violation_logs "
            "WHERE student_id = :sid "
            "ORDER BY created_at DESC LIMIT 5"
        ),
        {"sid": sid},
    ).mappings().all()

    data = dict(stu)
    data["is_blacklisted"]   = bl is not None
    data["blacklist_reason"] = bl["reason"] if bl else None
    data["violation_logs"]   = [dict(v) for v in v_logs]

    return api_ok(data)


# =========================================================
# 商城系统 (Products & Exchange)
# =========================================================
@app.get("/api/products", summary="获取积分商城商品列表")
def get_products(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """查询所有上架商品（status = 1），按所需积分升序排列。"""
    rows = db.execute(
        text("SELECT * FROM products WHERE status = 1 ORDER BY points_required ASC")
    ).mappings().all()
    return api_ok([dict(r) for r in rows])


@app.post("/api/exchange", summary="积分兑换商品（悲观锁，防超卖）")
def exchange_product(req: ExchangeRequest) -> Dict[str, Any]:
    """
    调用存储过程 sp_exchange_product 完成原子性积分兑换：
    - 学号存在检查
    - 库存检查（FOR UPDATE 悲观锁）
    - 积分余额检查
    - 扣减积分 + 扣减库存 + 写入兑换订单

    :param req: 包含 student_id 和 product_id 的请求体
    """
    validate_student_id(req.student_id)

    raw_conn = engine.raw_connection()
    try:
        cursor = raw_conn.cursor()
        cursor.execute("SET @p_code = 0, @p_msg = ''")
        cursor.execute(
            "CALL sp_exchange_product(%s, %s, @p_code, @p_msg)",
            (req.student_id, req.product_id),
        )
        cursor.execute("SELECT @p_code, @p_msg")
        out = cursor.fetchone()
        raw_conn.commit()

        code, msg = out[0], out[1]
        return {"code": code, "msg": msg}
    except Exception:
        raw_conn.rollback()
        logger.exception("积分兑换存储过程异常")
        raise
    finally:
        cursor.close()
        raw_conn.close()


# =========================================================
# 设备报修系统 (Repairs)
# =========================================================
@app.post("/api/repairs", summary="提交设备报修工单")
def submit_repair(req: RepairRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    提交报修工单。触发器 trg_repair_seat_maintenance 会自动
    将关联座位的状态更新为 'maintenance'，无需在此手动操作。

    :param req: 报修请求体（学号、阅览室、座位、故障描述）
    """
    validate_student_id(req.student_id)
    db.execute(
        text(
            "INSERT INTO repair_tickets (student_id, room_id, seat_id, description) "
            "VALUES (:sid, :rid, :seat, :desc)"
        ),
        {
            "sid":  req.student_id,
            "rid":  req.room_id,
            "seat": req.seat_id,
            "desc": req.description,
        },
    )
    db.commit()
    logger.info("报修工单已提交 [student=%s, room=%s]", req.student_id, req.room_id)
    return api_ok(msg="报修工单已提交，座位已临时锁定为维修状态")


@app.get("/api/repairs", summary="获取所有报修工单列表")
def get_repairs(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """查询全部报修工单，含阅览室名称与座位编号，按时间倒序。"""
    rows = db.execute(
        text(
            """
            SELECT
                t.ticket_id,
                t.description,
                t.status,
                DATE_FORMAT(t.created_at, '%Y-%m-%d %H:%i') AS created_at,
                r.room_name,
                s.seat_no
            FROM repair_tickets t
            JOIN  rooms r ON t.room_id  = r.room_id
            LEFT JOIN seats s ON t.seat_id  = s.seat_id
            ORDER BY t.created_at DESC
            """
        )
    ).mappings().all()
    return api_ok([dict(r) for r in rows])


# =========================================================
# 认证接口 (Auth APIs)
# =========================================================
@app.post("/api/auth/register", summary="学生注册")
def student_register(req: RegisterRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """学生注册：学号、姓名、手机号、密码（bcrypt 加密存储）。"""
    sid = req.student_id.strip()
    # 检查学号是否已存在
    exists = db.execute(text("SELECT 1 FROM students WHERE student_id = :sid"), {"sid": sid}).fetchone()
    if exists:
        raise HTTPException(status_code=400, detail="该学号已注册，请直接登录")
    phone_exists = db.execute(text("SELECT 1 FROM students WHERE phone = :p"), {"p": req.phone}).fetchone()
    if phone_exists:
        raise HTTPException(status_code=400, detail="该手机号已被注册")

    pw_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()
    email = req.email or f"{sid}@nuist.edu.cn"
    db.execute(text(
        "INSERT INTO students (student_id, name, email, phone, password_hash) "
        "VALUES (:sid, :name, :email, :phone, :pw)"
    ), {"sid": sid, "name": req.name, "email": email, "phone": req.phone, "pw": pw_hash})
    db.commit()
    return api_ok(msg="注册成功！请登录")


@app.post("/api/auth/login", summary="学生登录")
def student_login(req: LoginRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """学生登录：验证学号和密码，返回 JWT token。"""
    row = db.execute(
        text("SELECT student_id, name, password_hash, credit_score FROM students WHERE student_id = :sid"),
        {"sid": req.student_id.strip()}
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=401, detail="学号不存在")
    stored_hash = row["password_hash"]
    if not stored_hash or not bcrypt.checkpw(req.password.encode(), stored_hash.encode()):
        raise HTTPException(status_code=401, detail="密码错误")

    token = create_token({"sub": row["student_id"], "name": row["name"], "role": "student"})
    # 写入登录日志
    db.execute(text(
        "INSERT INTO system_logs (log_type, student_id, operator, content) VALUES ('login', :sid, :sid, :c)"
    ), {"sid": row["student_id"], "c": f"学生[{row['student_id']}]登录系统"})
    db.commit()
    return api_ok({
        "token": token,
        "role": "student",
        "student_id": row["student_id"],
        "name": row["name"],
        "credit_score": row["credit_score"],
    })


@app.post("/api/auth/admin/login", summary="管理员登录")
def admin_login(req: AdminLoginRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """管理员登录：验证用户名和密码，返回 JWT token（role=admin_super/admin_staff）。"""
    row = db.execute(
        text("SELECT admin_id, username, real_name, password_hash, role FROM admins WHERE username = :u"),
        {"u": req.username}
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=401, detail="管理员账号不存在")
    if not bcrypt.checkpw(req.password.encode(), row["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="密码错误")

    role_key = f"admin_{row['role']}"  # admin_super or admin_staff
    token = create_token({"sub": row["username"], "name": row["real_name"], "role": role_key, "admin_id": row["admin_id"]})
    db.execute(text(
        "INSERT INTO system_logs (log_type, operator, content) VALUES ('login', :u, :c)"
    ), {"u": row["username"], "c": f"管理员[{row['username']}]登录系统"})
    db.commit()
    return api_ok({
        "token": token,
        "role": role_key,
        "username": row["username"],
        "real_name": row["real_name"],
    })


@app.post("/api/auth/reset-password", summary="找回密码（学号+手机号验证）")
def reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """通过学号+预留手机号验证身份，重置密码。不需要验证码，符合课程设计规范。"""
    row = db.execute(
        text("SELECT student_id FROM students WHERE student_id = :sid AND phone = :phone"),
        {"sid": req.student_id.strip(), "phone": req.phone.strip()}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="账号或手机号不匹配，验证失败")
    pw_hash = bcrypt.hashpw(req.new_password.encode(), bcrypt.gensalt()).decode()
    db.execute(text("UPDATE students SET password_hash = :pw WHERE student_id = :sid"),
               {"pw": pw_hash, "sid": req.student_id.strip()})
    db.commit()
    return api_ok(msg="密码已重置，请重新登录")


# =========================================================
# 公告接口 (Announcements)
# =========================================================
@app.get("/api/announcements", summary="获取最新公告列表（学生端）")
def get_announcements(db: Session = Depends(get_db)) -> Dict[str, Any]:
    rows = db.execute(text(
        "SELECT id, title, content, DATE_FORMAT(created_at, '%Y-%m-%d') AS created_at "
        "FROM announcements ORDER BY created_at DESC LIMIT 10"
    )).mappings().all()
    return api_ok([dict(r) for r in rows])


@app.post("/api/admin/announcements", summary="管理员发布公告")
def create_announcement(
    req: AnnouncementRequest,
    db: Session = Depends(get_db),
    admin: Dict = Depends(require_admin)
) -> Dict[str, Any]:
    db.execute(text(
        "INSERT INTO announcements (title, content, admin_id) VALUES (:title, :content, :aid)"
    ), {"title": req.title, "content": req.content, "aid": admin.get("admin_id")})
    db.execute(text(
        "INSERT INTO system_logs (log_type, operator, content) VALUES ('admin_op', :u, :c)"
    ), {"u": admin["sub"], "c": f"管理员发布公告：{req.title}"})
    db.commit()
    return api_ok(msg="公告发布成功")


@app.delete("/api/admin/announcements/{ann_id}", summary="管理员删除公告")
def delete_announcement(
    ann_id: int,
    db: Session = Depends(get_db),
    admin: Dict = Depends(require_admin)
) -> Dict[str, Any]:
    db.execute(text("DELETE FROM announcements WHERE id = :id"), {"id": ann_id})
    db.commit()
    return api_ok(msg="公告已删除")


# =========================================================
# 管理员接口 (Admin APIs)
# =========================================================
@app.get("/api/admin/students", summary="获取所有学生列表")
def admin_get_students(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    admin: Dict = Depends(require_admin)
) -> Dict[str, Any]:
    rows = db.execute(text(
        "SELECT student_id, name, email, phone, credit_score, study_points, "
        "DATE_FORMAT(created_at, '%Y-%m-%d') AS created_at FROM students ORDER BY created_at DESC "
        "LIMIT :limit OFFSET :skip"
    ), {"limit": limit, "skip": skip}).mappings().all()
    return api_ok([dict(r) for r in rows])


@app.patch("/api/admin/students/{student_id}/credit", summary="管理员调整学生信用分")
def admin_adjust_credit(
    student_id: str,
    req: AdminCreditRequest,
    db: Session = Depends(get_db),
    admin: Dict = Depends(require_admin)
) -> Dict[str, Any]:
    row = db.execute(text("SELECT credit_score FROM students WHERE student_id = :sid"), {"sid": student_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="学生不存在")
    new_score = max(0, row[0] + req.delta)
    db.execute(text("UPDATE students SET credit_score = :s WHERE student_id = :sid"), {"s": new_score, "sid": student_id})
    db.execute(text(
        "INSERT INTO system_logs (log_type, student_id, operator, content) VALUES ('credit_change', :sid, :op, :c)"
    ), {"sid": student_id, "op": admin["sub"], "c": f"管理员[{admin['sub']}]{req.reason}，信用分变动 {req.delta:+d}"})
    db.commit()
    return api_ok({"new_credit_score": new_score}, msg="信用分已调整")


@app.patch("/api/admin/students/{student_id}/blacklist", summary="拉黑/解封学生")
def admin_blacklist(
    student_id: str,
    req: AdminBlacklistRequest,
    db: Session = Depends(get_db),
    admin: Dict = Depends(require_admin)
) -> Dict[str, Any]:
    if req.action == "ban":
        db.execute(text(
            "INSERT INTO blacklist (student_id, reason, is_active) VALUES (:sid, :r, 1) "
            "ON DUPLICATE KEY UPDATE is_active=1, reason=:r"
        ), {"sid": student_id, "r": req.reason or "管理员手动拉黑"})
        db.execute(text(
            "INSERT INTO system_logs (log_type, student_id, operator, content) VALUES ('blacklist', :sid, :op, :c)"
        ), {"sid": student_id, "op": admin["sub"], "c": f"管理员拉黑学生[{student_id}]：{req.reason}"})
    elif req.action == "unban":
        db.execute(text("UPDATE blacklist SET is_active=0 WHERE student_id=:sid"), {"sid": student_id})
        db.execute(text(
            "INSERT INTO system_logs (log_type, student_id, operator, content) VALUES ('blacklist', :sid, :op, :c)"
        ), {"sid": student_id, "op": admin["sub"], "c": f"管理员解封学生[{student_id}]"})
    else:
        raise HTTPException(status_code=400, detail="action 必须为 ban 或 unban")
    db.commit()
    return api_ok(msg="操作成功")


@app.get("/api/admin/tickets", summary="管理员查看所有报修工单")
def admin_get_tickets(
    db: Session = Depends(get_db),
    admin: Dict = Depends(require_admin)
) -> Dict[str, Any]:
    rows = db.execute(text(
        "SELECT t.ticket_id, t.student_id, t.description, t.status, "
        "DATE_FORMAT(t.created_at, '%Y-%m-%d %H:%i') AS created_at, "
        "r.room_name, s.seat_no "
        "FROM repair_tickets t "
        "JOIN rooms r ON t.room_id = r.room_id "
        "LEFT JOIN seats s ON t.seat_id = s.seat_id "
        "ORDER BY t.created_at DESC"
    )).mappings().all()
    return api_ok([dict(r) for r in rows])


@app.patch("/api/admin/tickets/{ticket_id}", summary="管理员处理报修工单")
def admin_update_ticket(
    ticket_id: int,
    req: AdminTicketRequest,
    db: Session = Depends(get_db),
    admin: Dict = Depends(require_admin)
) -> Dict[str, Any]:
    valid = ("processing", "resolved", "closed")
    if req.action not in valid:
        raise HTTPException(status_code=400, detail=f"action 必须为 {valid}")
    # 若工单变为 resolved，将对应座位恢复可用
    if req.action == "resolved":
        ticket = db.execute(text(
            "SELECT seat_id FROM repair_tickets WHERE ticket_id = :tid"
        ), {"tid": ticket_id}).fetchone()
        if ticket and ticket[0]:
            db.execute(text("UPDATE seats SET status = 'available' WHERE seat_id = :sid"), {"sid": ticket[0]})
    db.execute(text("UPDATE repair_tickets SET status = :s WHERE ticket_id = :tid"),
               {"s": req.action, "tid": ticket_id})
    db.execute(text(
        "INSERT INTO system_logs (log_type, operator, content) VALUES ('admin_op', :op, :c)"
    ), {"op": admin["sub"], "c": f"管理员[{admin['sub']}]将工单#{ticket_id}状态改为{req.action}"})
    db.commit()
    return api_ok(msg="工单已更新")


@app.get("/api/admin/rooms", summary="管理员获取阅览室列表")
def admin_get_rooms(db: Session = Depends(get_db), admin: Dict = Depends(require_admin)) -> Dict[str, Any]:
    rows = db.execute(text("SELECT * FROM v_room_status")).mappings().all()
    return api_ok([dict(r) for r in rows])


@app.post("/api/admin/rooms", summary="管理员新增阅览室")
def admin_add_room(
    req: AdminRoomRequest,
    db: Session = Depends(get_db),
    admin: Dict = Depends(require_admin)
) -> Dict[str, Any]:
    db.execute(text(
        "INSERT INTO rooms (room_name, location, open_time, close_time) VALUES (:n, :l, :o, :c)"
    ), {"n": req.room_name, "l": req.location, "o": req.open_time, "c": req.close_time})
    db.execute(text(
        "INSERT INTO system_logs (log_type, operator, content) VALUES ('admin_op', :op, :c)"
    ), {"op": admin["sub"], "c": f"管理员新增阅览室：{req.room_name}"})
    db.commit()
    return api_ok(msg="阅览室添加成功")


@app.delete("/api/admin/rooms/{room_id}", summary="管理员删除阅览室")
def admin_delete_room(
    room_id: int,
    db: Session = Depends(get_db),
    admin: Dict = Depends(require_admin)
) -> Dict[str, Any]:
    # CASCADE setting in foreign keys handles deletion of related seats and reservations
    db.execute(text("DELETE FROM rooms WHERE room_id = :rid"), {"rid": room_id})
    db.execute(text(
        "INSERT INTO system_logs (log_type, operator, content) VALUES ('admin_op', :op, :c)"
    ), {"op": admin["sub"], "c": f"管理员删除了阅览室#{room_id}"})
    db.commit()
    return api_ok(msg="阅览室删除成功")


@app.patch("/api/admin/seats/{seat_id}", summary="管理员修改座位状态")
def admin_update_seat(
    seat_id: int,
    req: AdminSeatRequest,
    db: Session = Depends(get_db),
    admin: Dict = Depends(require_admin)
) -> Dict[str, Any]:
    valid = ("available", "maintenance")
    if req.status not in valid:
        raise HTTPException(status_code=400, detail=f"status 必须为 {valid}")
    db.execute(text("UPDATE seats SET status = :s WHERE seat_id = :sid"), {"s": req.status, "sid": seat_id})
    db.execute(text(
        "INSERT INTO system_logs (log_type, operator, content) VALUES ('admin_op', :op, :c)"
    ), {"op": admin["sub"], "c": f"管理员将座位#{seat_id}状态改为{req.status}"})
    db.commit()
    return api_ok(msg="座位状态已更新")


# =========================================================
# 操作审计日志接口 (Audit Logs)
# =========================================================
@app.get("/api/admin/logs", summary="管理员查看系统操作日志")
def admin_get_logs(
    log_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    admin: Dict = Depends(require_admin)
) -> Dict[str, Any]:
    where = "WHERE log_type = :lt" if log_type else ""
    params = {"limit": limit, "skip": skip}
    if log_type:
        params["lt"] = log_type
    rows = db.execute(
        text(f"SELECT log_id, log_type, student_id, operator, content, "
             f"DATE_FORMAT(created_at, '%Y-%m-%d %H:%i:%s') AS created_at "
             f"FROM system_logs {where} ORDER BY created_at DESC LIMIT :limit OFFSET :skip"),
        params
    ).mappings().all()
    return api_ok([dict(r) for r in rows])

