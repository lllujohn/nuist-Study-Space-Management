import csv
import io
import logging
import os
import re
import time
import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Any, DefaultDict, Dict, Generator, List, Optional, Tuple

import bcrypt
import jwt

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()

JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError(
        "❌ 环境变量 JWT_SECRET 未配置！请在 .env 文件中添加：JWT_SECRET=<强随机字符串>"
    )
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24


def create_token(payload: Dict[str, Any]) -> str:
    data = payload.copy()
    data["exp"] = datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS)
    return jwt.encode(data, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> Dict[str, Any]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    except Exception:
        raise HTTPException(status_code=401, detail="身份验证失败")


def get_current_user(
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None)
) -> Dict[str, Any]:
    if token:
        return verify_token(token)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录，请先登录")
    token_str = authorization.split(" ", 1)[1]
    return verify_token(token_str)


def require_admin(
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None)
) -> Dict[str, Any]:
    user = get_current_user(authorization=authorization, token=token)
    if user.get("role") not in ("admin_super", "admin_staff"):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("lingxi_api")

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME", "lingxi_db")

DATABASE_URL = (
    f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    "?charset=utf8mb4"
)
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    echo=False,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("✅ 数据库连接成功 [%s]", DB_NAME)
    except Exception as e:
        logger.error("❌ 数据库连接失败: %s", e)
    yield
    engine.dispose()
    logger.info("🔒 数据库连接池已关闭")


app = FastAPI(
    title="南信大自习空间管理系统 API",
    description="NUIST 自习室座位预约、积分商城、设备报修系统",
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

_images_dir = os.path.join(os.path.dirname(__file__), "images")
if os.path.isdir(_images_dir):
    app.mount("/images", StaticFiles(directory=_images_dir), name="images")


_rate_limit_store: DefaultDict[str, List[float]] = defaultdict(list)
RATE_LIMITED_PATHS: Tuple[str, ...] = ("/api/reserve", "/api/exchange")
RATE_LIMIT_MAX    = 3
RATE_LIMIT_WINDOW = 5.0


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.method != "OPTIONS" and any(request.url.path.startswith(p) for p in RATE_LIMITED_PATHS):
        client_ip = request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.client.host if request.client else "unknown")).split(",")[0].strip()
        now = time.monotonic()

        timestamps = _rate_limit_store[client_ip]
        valid_timestamps = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]

        if len(valid_timestamps) >= RATE_LIMIT_MAX:
            _rate_limit_store[client_ip] = valid_timestamps
            logger.warning("限流触发 [IP=%s PATH=%s]", client_ip, request.url.path)
            return JSONResponse(
                status_code=429,
                content={"code": 429, "msg": "请求过于频繁，请稍后再试"},
            )

        valid_timestamps.append(now)
        _rate_limit_store[client_ip] = valid_timestamps

        import random
        if random.random() < 0.01:
            dead = [ip for ip, ts in _rate_limit_store.items()
                    if not any(now - t < RATE_LIMIT_WINDOW for t in ts)]
            for ip in dead:
                del _rate_limit_store[ip]

    return await call_next(request)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("未捕获异常 [%s %s]", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"code": 500, "msg": "服务器内部错误，请稍后重试", "detail": str(exc)},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "msg": exc.detail},
    )


STUDENT_ID_RE = re.compile(r"^202\d{9}$")


def api_ok(data: Any = None, msg: str = "success") -> Dict[str, Any]:
    resp: Dict[str, Any] = {"code": 200, "msg": msg}
    if data is not None:
        resp["data"] = data
    return resp


def api_err(msg: str, code: int = 400) -> Dict[str, Any]:
    return {"code": code, "msg": msg}


def validate_student_id(student_id: str) -> str:
    sid = student_id.strip()
    if not STUDENT_ID_RE.match(sid):
        raise HTTPException(
            status_code=400,
            detail="学号必须为 12 位数字，且以 202x 开头",
        )
    return sid


class ReserveRequest(BaseModel):
    student_id: str
    seat_id: int
    reserve_date: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    is_looking_for_buddy: bool = False
    buddy_tags: Optional[str] = None

    @field_validator("student_id")
    @classmethod
    def check_student_id(cls, v: str) -> str:
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
    delta: int    # 正数增加，负数扣减
    reason: str


class AdminBlacklistRequest(BaseModel):
    action: str   # ban | unban
    reason: Optional[str] = None


class AdminTicketRequest(BaseModel):
    action: str   # processing | resolved | closed


class AdminSeatRequest(BaseModel):
    status: str   # available | maintenance


class AdminRoomRequest(BaseModel):
    room_name: str
    location: str
    open_time: str = "08:00:00"
    close_time: str = "22:00:00"

class MessageRequest(BaseModel):
    room_id: int
    content: str
    is_anonymous: bool = False

class CreditTaskRequest(BaseModel):
    task_type: str
    description: str
    proof_url: Optional[str] = None

class AdminCreditTaskAction(BaseModel):
    action: str # approved | rejected


@app.get("/", include_in_schema=False)
def serve_index() -> FileResponse:
    response = FileResponse(
        os.path.join(os.path.dirname(__file__), "index.html"),
        media_type="text/html",
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ---- 数据大屏 ----
@app.get("/api/dashboard/stats", summary="大屏数据：各阅览室占座率 + 近 7 日违约趋势")
def get_dashboard_stats(db: Session = Depends(get_db)) -> Dict[str, Any]:
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


# ---- 阅览室与座位 ----
@app.get("/api/rooms", summary="所有阅览室实时状态")
def get_rooms(db: Session = Depends(get_db)) -> Dict[str, Any]:
    rows = db.execute(text("SELECT * FROM v_room_status")).mappings().all()
    return api_ok([dict(r) for r in rows])


@app.get("/api/seats/{room_id}", summary="某阅览室的座位列表")
def get_seats(room_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    返回该阅览室所有座位及其当前状态。
    座位状态由当前时间内的活跃预约动态决定，不依赖 seats.status 列（该列仅用于 maintenance）。
    """
    rows = db.execute(
        text(
            "SELECT s.seat_id, s.seat_no, s.has_power, s.tags, "
            "CASE "
            "  WHEN s.status = 'maintenance' THEN 'maintenance' "
            "  WHEN res.status IN ('active', 'away') THEN 'occupied' "
            "  WHEN res.status = 'pending' THEN 'reserved' "
            "  ELSE 'available' "
            "END AS status, "
            "r.student_id, u.name as student_name, r.reserve_date, r.start_time, r.end_time, r.is_looking_for_buddy, r.buddy_tags "
            "FROM seats s "
            "LEFT JOIN reservations r ON s.seat_id = r.seat_id AND r.status IN ('pending', 'active', 'away') AND r.reserve_date IN (CURDATE(), DATE_ADD(CURDATE(), INTERVAL 1 DAY)) "
            "LEFT JOIN reservations res ON s.seat_id = res.seat_id AND res.status IN ('pending', 'active', 'away') AND NOW() BETWEEN CONCAT(res.reserve_date, ' ', res.start_time) AND CONCAT(res.reserve_date, ' ', res.end_time) "
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
                "tags": r["tags"],
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
                "end_time": r["end_time"],
                "is_looking_for_buddy": r["is_looking_for_buddy"],
                "buddy_tags": r["buddy_tags"]
            })
            
    return api_ok(list(seats_dict.values()))


@app.post("/api/reserve", summary="提交座位预约")
def reserve_seat(req: ReserveRequest, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    reserve_date = req.reserve_date or date.today().isoformat()
    start_time   = req.start_time   or "08:00:00"
    end_time     = req.end_time     or "10:00:00"

    if user.get("role") == "student" and user.get("sub") != req.student_id:
        raise HTTPException(status_code=403, detail="越权操作：不能替他人预约")

    try:
        fmt = "%Y-%m-%d %H:%M:%S" if start_time.count(':') == 2 else "%Y-%m-%d %H:%M"
        req_dt = datetime.strptime(f"{reserve_date} {start_time}", fmt)
        if req_dt < datetime.now():
            return {"code": 400, "msg": "预约失败：不能预约已经过去的时间段"}
    except ValueError as e:
        logger.error("时间解析失败: %s", e)

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
        
        code, msg = out[0], out[1]
        if code == 0 and (req.is_looking_for_buddy or req.buddy_tags):
            cursor.execute(
                "UPDATE reservations SET is_looking_for_buddy = %s, buddy_tags = %s "
                "WHERE student_id = %s AND seat_id = %s AND reserve_date = %s AND start_time = %s "
                "ORDER BY reservation_id DESC LIMIT 1",
                (1 if req.is_looking_for_buddy else 0, req.buddy_tags, req.student_id, req.seat_id, reserve_date, start_time)
            )
            
        raw_conn.commit()
        return {"code": 200 if code == 0 else 400, "msg": msg}
    except Exception:
        raw_conn.rollback()
        logger.exception("预约存储过程异常")
        raise
    finally:
        cursor.close()
        raw_conn.close()


@app.get("/api/reservations/{student_id}", summary="查询学生预约记录")
def get_reservations(student_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
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

class FocusRewardRequest(BaseModel):
    reservation_id: Optional[int] = None

@app.post("/api/focus/reward", summary="专注倒计时完成奖励")
def reward_focus(req: FocusRewardRequest, db: Session = Depends(get_db), user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    student_id = user["sub"]
    
    if not req.reservation_id:
        return api_ok(msg="普通专注完成，不计入积分！再接再厉！")

    res_data = db.execute(text("SELECT status, checkin_time, last_focus_time FROM reservations WHERE reservation_id = :rid AND student_id = :sid FOR UPDATE"), {"rid": req.reservation_id, "sid": student_id}).fetchone()
    if not res_data:
        return {"code": 400, "msg": "预约记录不存在"}
    
    if res_data[0] != 'active':
        return {"code": 400, "msg": "只有在自习中的状态才能获得积分奖励哦！"}

    checkin_time = res_data[1]
    last_focus_time = res_data[2]
    
    reference_time = last_focus_time if last_focus_time else checkin_time
    if not reference_time:
        return {"code": 400, "msg": "未找到有效的签到时间！"}
        
    if datetime.now() < reference_time + timedelta(minutes=25):
        db.rollback()
        return {"code": 400, "msg": "太快啦！每次番茄钟必须至少专注 25 分钟才能领奖！"}

    points = 1

    db.execute(text("UPDATE reservations SET last_focus_time = NOW() WHERE reservation_id = :rid"), {"rid": req.reservation_id})
    res = db.execute(text("UPDATE students SET study_points = study_points + :pts WHERE student_id = :sid"), {"pts": points, "sid": student_id})
    db.execute(
        text("INSERT INTO study_points_logs (student_id, points_change, reason) VALUES (:sid, :pts, :reason)"),
        {"sid": student_id, "pts": points, "reason": "番茄钟专注完成奖励"}
    )
    db.commit()
    
    if res.rowcount > 0:
        return api_ok(msg=f"专注完成，获得 {points} 积分！")
    return {"code": 400, "msg": "发放积分失败"}


@app.post(
    "/api/reservations/{reservation_id}/action",
    summary="预约状态流转（签到 / 暂离 / 结束 / 取消）",
)
def handle_reservation_action(
    reservation_id: int,
    req: ReservationActionRequest,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user)
) -> Dict[str, Any]:
    if user.get("role") == "student" and user.get("sub") != req.student_id:
        raise HTTPException(status_code=403, detail="越权操作：无权修改他人的预约状态")

    sid = validate_student_id(req.student_id)

    valid_actions = {
        "checkin":  "active",
        "away":     "away",
        "checkout": "completed",
        "cancel":   "cancelled",
    }
    if req.action not in valid_actions:
        raise HTTPException(status_code=400, detail="无效的操作类型")

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
    now            = datetime.now()

    if current_status == "pending":
        start_dt = datetime.strptime(res["start_dt"], "%Y-%m-%d %H:%M:%S")
        if now > start_dt + timedelta(minutes=20):
            db.execute(text("UPDATE reservations SET status = 'violated' WHERE reservation_id = :rid"), {"rid": reservation_id})
            db.execute(
                text("INSERT INTO credit_logs (student_id, score_change, reason) VALUES (:sid, -10, :reason)"),
                {"sid": sid, "reason": f"爽约：预约 #{reservation_id} 逾期20分钟未签到"},
            )
            db.commit()
            raise HTTPException(status_code=400, detail="该预约已逾期20分钟未签到，系统已自动记为爽约并扣除10信用分。")


    if req.action == "checkin" and current_status not in ("pending", "away"):
        raise HTTPException(status_code=400, detail="当前状态无法执行签到")
    if req.action == "checkin" and current_status == "pending":
        start_dt = datetime.strptime(res["start_dt"], "%Y-%m-%d %H:%M:%S")
        if now < start_dt - timedelta(minutes=15):
            raise HTTPException(status_code=400, detail="只能在预约时间前 15 分钟内签到")
        if now < start_dt:
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
                raise HTTPException(status_code=400, detail="上一位同学还未结束自习，请稍后再签到")
    if req.action == "away" and current_status != "active":
        raise HTTPException(status_code=400, detail="仅自习中状态可暂离")
    if req.action == "cancel" and current_status != "pending":
        raise HTTPException(status_code=400, detail="只能取消待签到的预约")
    if req.action == "checkout" and current_status not in ("active", "away"):
        raise HTTPException(status_code=400, detail="当前状态无法结束自习")

    updates = ["status = :status"]
    params: Dict[str, Any] = {"status": new_status, "rid": reservation_id}

    if req.action == "checkin" and current_status == "pending":
        updates.append("checkin_time = NOW()")
    if req.action == "checkout":
        updates.append("checkout_time = NOW()")
        checkin_time = res["checkin_time"]
        if checkin_time:
            pts_row = db.execute(
                text("SELECT GREATEST(0, ROUND(TIMESTAMPDIFF(MINUTE, :checkin, NOW()) / 60.0 * 5))"),
                {"checkin": checkin_time}
            ).fetchone()
            pts = int(pts_row[0]) if pts_row else 0
            if pts > 0:
                db.execute(
                    text("UPDATE students SET study_points = study_points + :pts WHERE student_id = :sid"),
                    {"pts": pts, "sid": sid}
                )
                db.execute(
                    text("INSERT INTO study_points_logs (student_id, points_change, reason) VALUES (:sid, :pts, :reason)"),
                    {"sid": sid, "pts": pts, "reason": "正常签退：系统结算时长奖励"}
                )
    db.execute(
        text(f"UPDATE reservations SET {', '.join(updates)} WHERE reservation_id = :rid"),
        params,
    )
    db.commit()

    return api_ok(msg="操作成功")


# ---- 数据导出 ----
@app.get("/api/export/stats", summary="导出每日自习统计 CSV")
def export_stats_csv(db: Session = Depends(get_db)) -> StreamingResponse:
    rows = db.execute(
        text(
            "SELECT stat_date, total_reservations, completed_reservations, "
            "total_violations, total_study_hours "
            "FROM daily_study_stats ORDER BY stat_date ASC"
        )
    ).mappings().all()

    fieldnames = ["stat_date", "total_reservations", "completed_reservations",
                  "total_violations", "total_study_hours"]

    def _csv_generator():
        yield "日期,总预约数,完成数,违约次数,自习总时数(h)\n"
        for row in rows:
            buf = io.StringIO()
            csv.DictWriter(buf, fieldnames=fieldnames).writerow(dict(row))
            yield buf.getvalue()

    filename = f"study_stats_{date.today().isoformat()}.csv"
    return StreamingResponse(
        _csv_generator(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---- 学生信息 ----

@app.get("/api/students/me/points_logs", summary="获取当前登录学生的积分明细")
def get_my_points_logs(db: Session = Depends(get_db), user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    if user["role"] != "student":
        raise HTTPException(status_code=403, detail="仅学生可查看积分明细")
    sid = user["sub"]
    rows = db.execute(
        text("SELECT log_id, points_change, reason, created_at FROM study_points_logs WHERE student_id = :sid ORDER BY created_at DESC"),
        {"sid": sid}
    ).mappings().fetchall()
    return {"code": 200, "data": [dict(r) for r in rows]}

@app.get("/api/students/{student_id}", summary="查询学生信息（含信用分与违约记录）")
def get_student(student_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
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

    bl = db.execute(
        text(
            "SELECT reason FROM blacklist "
            "WHERE student_id = :sid AND is_active = 1"
        ),
        {"sid": sid},
    ).mappings().first()

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


# ---- 积分商城 ----
@app.get("/api/products", summary="积分商城商品列表")
def get_products(db: Session = Depends(get_db)) -> Dict[str, Any]:
    rows = db.execute(
        text("SELECT * FROM products WHERE status = 1 ORDER BY points_required ASC")
    ).mappings().all()
    return api_ok([dict(r) for r in rows])


@app.post("/api/exchange", summary="积分兑换商品")
def exchange_product(req: ExchangeRequest, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    if user.get("role") == "student" and user.get("sub") != req.student_id:
        raise HTTPException(status_code=403, detail="越权操作：不能扣减他人的积分")

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


# ---- 设备报修 ----
@app.post("/api/repairs", summary="提交设备报修工单")
def submit_repair(req: RepairRequest, db: Session = Depends(get_db), user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    if user.get("role") == "student" and user.get("sub") != req.student_id:
        raise HTTPException(status_code=403, detail="越权操作：不能替他人报修")

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


@app.get("/api/repairs", summary="所有报修工单")
def get_repairs(db: Session = Depends(get_db)) -> Dict[str, Any]:
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


# ---- 认证 ----
@app.post("/api/auth/register", summary="学生注册")
def student_register(req: RegisterRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    sid = req.student_id.strip()
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


@app.post("/api/auth/reset-password", summary="重置密码")
def reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
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


# ---- 公告 ----
@app.get("/api/announcements", summary="公告列表")
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


# ---- 管理员后台 ----
@app.get("/api/admin/students", summary="所有学生列表")
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


@app.patch("/api/admin/students/{student_id}/credit", summary="调整学生信用分")
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


@app.patch("/api/admin/students/{student_id}/blacklist", summary="拉黑或解封学生")
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


@app.get("/api/admin/rooms", summary="阅览室列表（管理员）")
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


@app.delete("/api/admin/rooms/{room_id}", summary="删除阅览室")
def admin_delete_room(
    room_id: int,
    db: Session = Depends(get_db),
    admin: Dict = Depends(require_admin)
) -> Dict[str, Any]:
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


# ---- 审计日志 ----
@app.get("/api/admin/logs", summary="系统操作日志")
def admin_get_logs(
    log_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    admin: Dict = Depends(require_admin)
) -> Dict[str, Any]:
    where = "WHERE log_type = :lt" if log_type else ""
    params: Dict[str, Any] = {"limit": limit, "skip": skip}
    if log_type:
        params["lt"] = log_type
    rows = db.execute(
        text(f"SELECT log_id, log_type, student_id, operator, content, "
             f"DATE_FORMAT(created_at, '%Y-%m-%d %H:%i:%s') AS created_at "
             f"FROM system_logs {where} ORDER BY created_at DESC LIMIT :limit OFFSET :skip"),
        params
    ).mappings().all()
    return api_ok([dict(r) for r in rows])


# ---- Room Guestbook ----
@app.get("/api/rooms/{room_id}/messages", summary="获取自习室留言")
def get_room_messages(room_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    rows = db.execute(text(
        "SELECT m.msg_id, m.content, m.is_anonymous, m.created_at, m.student_id, "
        "IF(m.is_anonymous, '匿名同学', s.name) as author_name "
        "FROM room_messages m "
        "JOIN students s ON m.student_id = s.student_id "
        "WHERE m.room_id = :rid AND m.status = 1 "
        "ORDER BY m.created_at DESC"
    ), {"rid": room_id}).mappings().all()
    
    res = []
    for r in rows:
        d = dict(r)
        d["created_at"] = str(d["created_at"])
        res.append(d)
    return api_ok(res)

@app.post("/api/rooms/{room_id}/messages", summary="发布留言")
def post_room_message(room_id: int, req: MessageRequest, db: Session = Depends(get_db), user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    student_id = user["sub"]
    db.execute(text(
        "INSERT INTO room_messages (room_id, student_id, content, is_anonymous) "
        "VALUES (:rid, :sid, :content, :anon)"
    ), {"rid": room_id, "sid": student_id, "content": req.content, "anon": 1 if req.is_anonymous else 0})
    db.commit()
    return api_ok(msg="留言发布成功")

@app.delete("/api/messages/{msg_id}", summary="删除留言")
def delete_room_message(msg_id: int, db: Session = Depends(get_db), user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    student_id = user["sub"]
    if user.get("role") == "admin":
        res = db.execute(text("UPDATE room_messages SET status = 0 WHERE msg_id = :mid"), {"mid": msg_id})
    else:
        res = db.execute(text("UPDATE room_messages SET status = 0 WHERE msg_id = :mid AND student_id = :sid"), {"mid": msg_id, "sid": student_id})
    
    db.commit()
    if res.rowcount > 0:
        return api_ok(msg="留言已删除")
    return {"code": 400, "msg": "删除失败，可能无权限或留言不存在"}

# ---- Credit Recovery Tasks ----
@app.post("/api/credit-tasks", summary="申请信用分恢复")
def apply_credit_task(req: CreditTaskRequest, db: Session = Depends(get_db), user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    student_id = user["sub"]
    db.execute(text(
        "INSERT INTO credit_tasks (student_id, task_type, description, proof_url) "
        "VALUES (:sid, :ttype, :desc, :proof)"
    ), {"sid": student_id, "ttype": req.task_type, "desc": req.description, "proof": req.proof_url})
    db.commit()
    return api_ok(msg="申请已提交，等待管理员审核")

@app.get("/api/credit-tasks", summary="查看我的申请/管理员查看所有申请")
def get_credit_tasks(db: Session = Depends(get_db), user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    is_admin = user.get("role") in ("admin_super", "admin_staff")
    if is_admin:
        rows = db.execute(text(
            "SELECT t.*, s.name as student_name FROM credit_tasks t "
            "JOIN students s ON t.student_id = s.student_id ORDER BY t.created_at DESC"
        )).mappings().all()
    else:
        rows = db.execute(text(
            "SELECT * FROM credit_tasks WHERE student_id = :sid ORDER BY created_at DESC"
        ), {"sid": user["sub"]}).mappings().all()
        
    res = []
    for r in rows:
        d = dict(r)
        d["created_at"] = str(d["created_at"])
        d["updated_at"] = str(d["updated_at"])
        res.append(d)
    return api_ok(res)

@app.post("/api/admin/credit-tasks/{task_id}/action", summary="管理员审核志愿任务")
def admin_audit_credit_task(task_id: int, req: AdminCreditTaskAction, db: Session = Depends(get_db), admin: Dict = Depends(require_admin)) -> Dict[str, Any]:
    if req.action not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="Invalid action")
        
    task = db.execute(text("SELECT * FROM credit_tasks WHERE task_id = :tid FOR UPDATE"), {"tid": task_id}).mappings().first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["status"] != "pending":
        raise HTTPException(status_code=400, detail="该任务已处理过")
        
    db.execute(text("UPDATE credit_tasks SET status = :s WHERE task_id = :tid"), 
               {"s": req.action, "tid": task_id})
               
    if req.action == "approved":
        db.execute(text(
            "INSERT INTO credit_logs (student_id, score_change, reason) VALUES (:sid, :pts, :reason)"
        ), {"sid": task["student_id"], "pts": task["points_reward"], "reason": f"完成信用分恢复任务：{task['task_type']}"})
        
    db.commit()
    return api_ok(msg="审核完成")

# ---- SSE Notifications ----
@app.get("/api/stream/notifications", summary="SSE 消息通知订阅")
async def stream_notifications(request: Request, db: Session = Depends(get_db), user: Dict[str, Any] = Depends(get_current_user)):
    student_id = user.get("sub")
    if not student_id or user.get("role") != "student":
        raise HTTPException(status_code=403, detail="Not a student")
        
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
                
            with engine.connect() as conn:
                res = conn.execute(text(
                    "SELECT reservation_id, room_name, seat_no, start_time "
                    "FROM reservations res JOIN seats s ON res.seat_id = s.seat_id JOIN rooms r ON s.room_id = r.room_id "
                    "WHERE res.student_id = :sid AND res.status = 'pending' "
                    "AND CONCAT(res.reserve_date, ' ', res.start_time) BETWEEN NOW() AND DATE_ADD(NOW(), INTERVAL 15 MINUTE) "
                    "LIMIT 1"
                ), {"sid": student_id}).mappings().first()
                
            if res:
                yield f"data: {{\"type\": \"reminder\", \"msg\": \"你的自习预约即将开始，请准备签到！({res['room_name']} - {res['seat_no']})\"}}\n\n"
                
            await asyncio.sleep(10)
            yield f"data: {{\"type\": \"ping\"}}\n\n"
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")
