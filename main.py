from fastapi import FastAPI, HTTPException, Depends, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, EmailStr, validator
from datetime import datetime, timedelta
import sqlite3
import jwt
from typing import Optional, List
import uvicorn
import os
import logging
import secrets
import string
import re
from passlib.context import CryptContext
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Security logger for audit trail
security_logger = logging.getLogger('security')
security_logger.setLevel(logging.INFO)

# Password hashing context using bcrypt
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Rate limiter
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="BJJ PRO GYM API - Multi-Tenant Professional Edition",
    docs_url=None,  # Disable docs in production
    redoc_url=None,  # Disable redoc in production
    openapi_url=None  # Disable OpenAPI schema in production
)

# Rate limit error handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Trusted Host Middleware (prevents host header attacks)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["*"]  # Configure with your actual domain in production
)

# CORS middleware with stricter settings
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
    max_age=600,
)

# Security headers middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline' unpkg.com cdn.tailwindcss.com; style-src 'self' 'unsafe-inline' fonts.googleapis.com; font-src 'self' fonts.gstatic.com"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    return response

# Security
security = HTTPBearer()
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_urlsafe(32))

# Failed login tracking
failed_login_attempts = {}
ACCOUNT_LOCKOUT_THRESHOLD = 5
LOCKOUT_DURATION = timedelta(minutes=15)

# Security helper functions
def validate_password_strength(password: str) -> tuple[bool, str]:
    """Validate password meets security requirements"""
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter"
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter"
    if not re.search(r"\d", password):
        return False, "Password must contain at least one number"
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
        return False, "Password must contain at least one special character"
    return True, "Password is strong"

def sanitize_input(text: str, max_length: int = 255) -> str:
    """Sanitize user input to prevent XSS and injection attacks"""
    if not text:
        return ""
    # Remove any potentially dangerous characters
    text = text.strip()
    text = re.sub(r'[<>\"\'%;()&+]', '', text)
    return text[:max_length]

def hash_password(password: str) -> str:
    """Hash password using bcrypt"""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against hash"""
    return pwd_context.verify(plain_password, hashed_password)

def log_security_event(event_type: str, details: dict, severity: str = "INFO"):
    """Log security-related events for audit trail"""
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "event_type": event_type,
        "severity": severity,
        **details
    }
    security_logger.info(f"SECURITY_EVENT: {log_entry}")

    # Also store in database
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO security_logs (event_type, details, severity, created_at)
            VALUES (?, ?, ?, ?)
        ''', (event_type, str(details), severity, datetime.now()))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to log security event to database: {e}")

def check_account_lockout(email: str) -> tuple[bool, Optional[datetime]]:
    """Check if account is locked due to failed login attempts"""
    if email in failed_login_attempts:
        attempts, lockout_until = failed_login_attempts[email]
        if lockout_until and datetime.now() < lockout_until:
            return True, lockout_until
        elif datetime.now() >= lockout_until:
            # Lockout expired, reset
            del failed_login_attempts[email]
    return False, None

def record_failed_login(email: str):
    """Record failed login attempt and apply lockout if threshold reached"""
    if email not in failed_login_attempts:
        failed_login_attempts[email] = [1, None]
    else:
        attempts, _ = failed_login_attempts[email]
        attempts += 1
        if attempts >= ACCOUNT_LOCKOUT_THRESHOLD:
            lockout_until = datetime.now() + LOCKOUT_DURATION
            failed_login_attempts[email] = [attempts, lockout_until]
            log_security_event(
                "ACCOUNT_LOCKED",
                {"email": email, "attempts": attempts, "lockout_until": lockout_until.isoformat()},
                "WARNING"
            )
        else:
            failed_login_attempts[email] = [attempts, None]

def clear_failed_logins(email: str):
    """Clear failed login attempts after successful login"""
    if email in failed_login_attempts:
        del failed_login_attempts[email]

# Pydantic models with validation
class GymRegistration(BaseModel):
    gym_name: str
    owner_name: str
    owner_email: EmailStr
    owner_password: str
    access_code: Optional[str] = None

    @validator('gym_name', 'owner_name')
    def sanitize_names(cls, v):
        return sanitize_input(v, 100)

    @validator('owner_password')
    def validate_password(cls, v):
        is_valid, message = validate_password_strength(v)
        if not is_valid:
            raise ValueError(message)
        return v

class LoginRequest(BaseModel):
    email: str
    password: str

class StudentCreate(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    belt_level: str = "White"

class ClassCreate(BaseModel):
    name: str
    description: Optional[str] = None

class ScheduleCreate(BaseModel):
    class_name: str
    day_of_week: int
    start_time: str
    end_time: str
    instructor: Optional[str] = None

class CheckInRequest(BaseModel):
    student_name: Optional[str] = None
    card_number: Optional[str] = None

class EmailRequest(BaseModel):
    subject: str
    message: str
    notification_type: str = "general"
    recipient_type: str = "students"
    recipient_count: int = 0

class PaymentCreate(BaseModel):
    student_name: str
    member_id: Optional[str] = None
    amount: float
    payment_type: str = "Monthly Membership"
    payment_method: str = "Credit Card"

# Database setup with multi-tenant support
def get_db_connection():
    try:
        conn = sqlite3.connect('bjj_pro_gym_production.db', timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.error(f"Database connection error: {str(e)}")
        raise

def generate_gym_code():
    """Generate unique gym code"""
    return ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))

def init_db():
    try:
        logger.info("Initializing production database...")
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Create gyms table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS gyms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gym_code TEXT UNIQUE NOT NULL,
                gym_name TEXT NOT NULL,
                owner_name TEXT NOT NULL,
                owner_email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                subscription_status TEXT DEFAULT 'active',
                subscription_plan TEXT DEFAULT 'professional',
                access_code TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create students table with gym_id
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gym_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                email TEXT,
                phone TEXT,
                belt_level TEXT DEFAULT 'White',
                member_id TEXT,
                card_number TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (gym_id) REFERENCES gyms (id),
                UNIQUE(gym_id, member_id),
                UNIQUE(gym_id, card_number)
            )
        ''')
        
        # Create classes table with gym_id
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS classes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gym_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (gym_id) REFERENCES gyms (id),
                UNIQUE(gym_id, name)
            )
        ''')
        
        # Create schedules table with gym_id
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gym_id INTEGER NOT NULL,
                class_name TEXT NOT NULL,
                day_of_week INTEGER NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                instructor TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (gym_id) REFERENCES gyms (id)
            )
        ''')
        
        # Create attendance_logs table with gym_id
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS attendance_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gym_id INTEGER NOT NULL,
                student_name TEXT NOT NULL,
                student_id INTEGER,
                member_id TEXT,
                card_number TEXT,
                class_name TEXT NOT NULL,
                check_in_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (gym_id) REFERENCES gyms (id)
            )
        ''')
        
        # Create payments table with gym_id
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gym_id INTEGER NOT NULL,
                student_name TEXT NOT NULL,
                member_id TEXT,
                amount REAL NOT NULL,
                payment_type TEXT NOT NULL,
                payment_method TEXT NOT NULL,
                status TEXT DEFAULT 'Completed',
                payment_date DATE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (gym_id) REFERENCES gyms (id)
            )
        ''')
        
        # Create email_notifications table with gym_id
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS email_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gym_id INTEGER NOT NULL,
                subject TEXT NOT NULL,
                message TEXT NOT NULL,
                recipient_count INTEGER NOT NULL,
                sent_by TEXT NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notification_type TEXT DEFAULT 'general',
                FOREIGN KEY (gym_id) REFERENCES gyms (id)
            )
        ''')

        # Create security_logs table for audit trail
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS security_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                details TEXT NOT NULL,
                severity TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create demo gym if it doesn't exist (for backwards compatibility)
        cursor.execute("SELECT COUNT(*) FROM gyms WHERE gym_code = 'DEMO0001'")
        if cursor.fetchone()[0] == 0:
            # Use bcrypt for secure password hashing
            demo_password = hash_password("Admin123!")
            cursor.execute('''
                INSERT INTO gyms (gym_code, gym_name, owner_name, owner_email, password_hash, access_code)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', ('DEMO0001', 'Demo BJJ Pro Academy', 'Demo Owner', 'demo@bjjprogym.com', demo_password, 'ADELYNN14'))

            demo_gym_id = cursor.lastrowid
            setup_demo_data(cursor, demo_gym_id)

            log_security_event(
                "DEMO_ACCOUNT_CREATED",
                {"email": "demo@bjjprogym.com", "gym_code": "DEMO0001"},
                "INFO"
            )
        
        conn.commit()
        conn.close()
        logger.info("✅ Production database initialized successfully!")
        
    except Exception as e:
        logger.error(f"Database initialization failed: {str(e)}")

def setup_demo_data(cursor, gym_id):
    """Setup demo data for a gym"""
    try:
        # Demo students
        demo_students = [
            ("John Smith", "john.smith@email.com", "555-0101", "Blue", "MBR001", "CARD1001"),
            ("Maria Garcia", "maria.garcia@email.com", "555-0102", "White", "MBR002", "CARD1002"),
            ("David Johnson", "david.johnson@email.com", "555-0103", "Purple", "MBR003", "CARD1003"),
            ("Sarah Wilson", "sarah.wilson@email.com", "555-0104", "White", "MBR004", "CARD1004"),
            ("Mike Brown", "mike.brown@email.com", "555-0105", "Blue", "MBR005", "CARD1005")
        ]
        
        for name, email, phone, belt, member_id, card_number in demo_students:
            cursor.execute('''
                INSERT OR IGNORE INTO students (gym_id, name, email, phone, belt_level, member_id, card_number)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (gym_id, name, email, phone, belt, member_id, card_number))
        
        # Demo classes
        demo_classes = [
            ("Fundamentals", "Basic BJJ techniques for beginners"),
            ("Advanced", "Advanced techniques and sparring"),
            ("Competition", "Competition preparation and training"),
            ("No-Gi", "Brazilian Jiu-Jitsu without gi"),
            ("Open Mat", "Free training and practice time")
        ]
        
        for class_name, description in demo_classes:
            cursor.execute('''
                INSERT OR IGNORE INTO classes (gym_id, name, description)
                VALUES (?, ?, ?)
            ''', (gym_id, class_name, description))
        
        # Demo schedules
        demo_schedules = [
            ("Fundamentals", 0, "18:00", "19:00", "Professor Silva"),
            ("Advanced", 0, "19:15", "20:45", "Professor Johnson"),
            ("No-Gi", 1, "19:00", "20:00", "Professor Davis"),
            ("Open Mat", 4, "18:00", "20:00", "Open")
        ]
        
        for class_name, day, start, end, instructor in demo_schedules:
            cursor.execute('''
                INSERT OR IGNORE INTO schedules (gym_id, class_name, day_of_week, start_time, end_time, instructor)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (gym_id, class_name, day, start, end, instructor))
        
    except Exception as e:
        logger.error(f"Demo data setup failed: {str(e)}")

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

# Registration endpoint with security features
@app.post("/api/register")
@limiter.limit("3/hour")  # Rate limit: 3 registrations per hour
async def register_gym(request: Request, registration: GymRegistration):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Check if email already exists
        cursor.execute('SELECT id FROM gyms WHERE owner_email = ?', (registration.owner_email,))
        if cursor.fetchone():
            conn.close()
            log_security_event(
                "REGISTRATION_FAILED",
                {"email": registration.owner_email, "reason": "Email already exists"},
                "WARNING"
            )
            raise HTTPException(status_code=400, detail="Email already registered")

        # Validate access code for special plans
        subscription_plan = "starter"
        if registration.access_code:
            if registration.access_code.upper() == "ADELYNN14":
                subscription_plan = "professional"
            else:
                conn.close()
                log_security_event(
                    "REGISTRATION_FAILED",
                    {"email": registration.owner_email, "reason": "Invalid access code"},
                    "WARNING"
                )
                raise HTTPException(status_code=400, detail="Invalid access code")

        # Generate unique gym code
        gym_code = generate_gym_code()
        while True:
            cursor.execute('SELECT id FROM gyms WHERE gym_code = ?', (gym_code,))
            if not cursor.fetchone():
                break
            gym_code = generate_gym_code()

        # Hash password using bcrypt
        password_hash = hash_password(registration.owner_password)

        # Create gym
        cursor.execute('''
            INSERT INTO gyms (gym_code, gym_name, owner_name, owner_email, password_hash, subscription_plan, access_code)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (gym_code, registration.gym_name, registration.owner_name, registration.owner_email, password_hash, subscription_plan, registration.access_code))

        gym_id = cursor.lastrowid

        # Setup starter data for new gym
        setup_starter_data(cursor, gym_id)

        conn.commit()
        conn.close()

        log_security_event(
            "REGISTRATION_SUCCESS",
            {
                "email": registration.owner_email,
                "gym_code": gym_code,
                "gym_name": registration.gym_name,
                "subscription_plan": subscription_plan
            },
            "INFO"
        )

        return {
            "message": "Gym registered successfully",
            "gym_code": gym_code,
            "subscription_plan": subscription_plan,
            "gym_id": gym_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Registration error: {str(e)}")
        log_security_event(
            "REGISTRATION_ERROR",
            {"email": registration.owner_email, "error": str(e)},
            "ERROR"
        )
        raise HTTPException(status_code=500, detail="Registration failed")

def setup_starter_data(cursor, gym_id):
    """Setup basic starter data for new gyms"""
    try:
        # Basic classes
        starter_classes = [
            ("Fundamentals", "Basic Brazilian Jiu-Jitsu techniques"),
            ("Advanced", "Advanced BJJ training"),
            ("Open Mat", "Free training time")
        ]
        
        for class_name, description in starter_classes:
            cursor.execute('''
                INSERT INTO classes (gym_id, name, description)
                VALUES (?, ?, ?)
            ''', (gym_id, class_name, description))
        
    except Exception as e:
        logger.error(f"Starter data setup failed: {str(e)}")

# Login endpoint with security features
@app.post("/api/login")
@limiter.limit("5/minute")  # Rate limit: 5 attempts per minute
async def login(request: Request, login_request: LoginRequest):
    try:
        # Check if account is locked
        is_locked, lockout_until = check_account_lockout(login_request.email)
        if is_locked:
            log_security_event(
                "LOGIN_ATTEMPT_WHILE_LOCKED",
                {"email": login_request.email, "lockout_until": lockout_until.isoformat()},
                "WARNING"
            )
            raise HTTPException(
                status_code=423,
                detail=f"Account locked due to too many failed attempts. Try again after {lockout_until.strftime('%H:%M')}"
            )

        conn = get_db_connection()
        cursor = conn.cursor()

        # Fetch user by email
        cursor.execute('''
            SELECT id, gym_code, gym_name, owner_name, subscription_plan, access_code, password_hash, owner_email
            FROM gyms
            WHERE owner_email = ?
        ''', (login_request.email,))

        gym = cursor.fetchone()
        conn.close()

        # Verify password using bcrypt
        if not gym or not verify_password(login_request.password, gym["password_hash"]):
            record_failed_login(login_request.email)
            log_security_event(
                "LOGIN_FAILED",
                {"email": login_request.email, "reason": "Invalid credentials"},
                "WARNING"
            )
            raise HTTPException(status_code=401, detail="Invalid credentials")

        # Successful login - clear failed attempts
        clear_failed_logins(login_request.email)

        token_data = {
            "gym_id": gym["id"],
            "gym_code": gym["gym_code"],
            "gym_name": gym["gym_name"],
            "owner_name": gym["owner_name"],
            "subscription_plan": gym["subscription_plan"],
            "access_code": gym["access_code"],
            "exp": datetime.utcnow() + timedelta(hours=24)
        }

        token = jwt.encode(token_data, SECRET_KEY, algorithm="HS256")

        log_security_event(
            "LOGIN_SUCCESS",
            {"email": login_request.email, "gym_code": gym["gym_code"]},
            "INFO"
        )

        return {
            "access_token": token,
            "token_type": "bearer",
            "gym_info": {
                "gym_code": gym["gym_code"],
                "gym_name": gym["gym_name"],
                "owner_name": gym["owner_name"],
                "subscription_plan": gym["subscription_plan"],
                "has_pro_access": gym["access_code"] == "ADELYNN14"
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        log_security_event(
            "LOGIN_ERROR",
            {"email": login_request.email, "error": str(e)},
            "ERROR"
        )
        raise HTTPException(status_code=500, detail="Login failed")

# Updated endpoints with gym isolation
@app.get("/api/students")
async def get_students(token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, name, email, phone, belt_level, member_id, card_number, created_at
            FROM students
            WHERE gym_id = ?
            ORDER BY name
        ''', (gym_id,))
        
        students = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return {"students": students}
    except Exception as e:
        logger.error(f"Error getting students: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve students")

@app.post("/api/students")
async def create_student(student: StudentCreate, token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Auto-generate IDs
        cursor.execute('SELECT COUNT(*) FROM students WHERE gym_id = ?', (gym_id,))
        count = cursor.fetchone()[0]
        member_id = f"MBR{count + 1:03d}"
        card_number = f"CARD{count + 1001:04d}"
        
        cursor.execute('''
            INSERT INTO students (gym_id, name, email, phone, belt_level, member_id, card_number)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (gym_id, student.name, student.email, student.phone, student.belt_level, member_id, card_number))
        
        student_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {
            "message": "Student created successfully",
            "student_id": student_id,
            "member_id": member_id,
            "card_number": card_number
        }
    except Exception as e:
        logger.error(f"Error creating student: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create student")

@app.get("/api/classes")
async def get_classes(token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, name, description, created_at
            FROM classes
            WHERE gym_id = ?
            ORDER BY name
        ''', (gym_id,))
        
        classes = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return {"classes": classes}
    except Exception as e:
        logger.error(f"Error getting classes: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve classes")

@app.post("/api/classes")
async def create_class(class_data: ClassCreate, token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check for duplicate class name within gym
        cursor.execute('''
            SELECT COUNT(*) FROM classes WHERE gym_id = ? AND LOWER(name) = LOWER(?)
        ''', (gym_id, class_data.name))
        
        if cursor.fetchone()[0] > 0:
            conn.close()
            raise HTTPException(status_code=400, detail="Class with this name already exists")
        
        cursor.execute('''
            INSERT INTO classes (gym_id, name, description)
            VALUES (?, ?, ?)
        ''', (gym_id, class_data.name.strip(), class_data.description.strip() if class_data.description else None))
        
        class_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {
            "message": "Class created successfully",
            "class_id": class_id
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating class: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create class")

@app.get("/api/payments")
async def get_payments(token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, student_name, member_id, amount, payment_type, payment_method, status, payment_date
            FROM payments
            WHERE gym_id = ?
            ORDER BY payment_date DESC
        ''', (gym_id,))
        
        payments = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return {"payments": payments}
    except Exception as e:
        logger.error(f"Error getting payments: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve payments")

@app.post("/api/payments")
async def create_payment(payment: PaymentCreate, token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO payments (gym_id, student_name, member_id, amount, payment_type, payment_method, payment_date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (gym_id, payment.student_name, payment.member_id, payment.amount, payment.payment_type, payment.payment_method, datetime.now().date()))

        payment_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return {
            "message": "Payment recorded successfully",
            "payment_id": payment_id
        }
    except Exception as e:
        logger.error(f"Error creating payment: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to record payment")

# Schedule endpoints
@app.get("/api/schedules")
async def get_schedules(token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, class_name, day_of_week, start_time, end_time, instructor
            FROM schedules
            WHERE gym_id = ?
            ORDER BY day_of_week, start_time
        ''', (gym_id,))

        schedules = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return {"schedules": schedules}
    except Exception as e:
        logger.error(f"Error getting schedules: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve schedules")

@app.post("/api/schedules")
async def create_schedule(schedule: ScheduleCreate, token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO schedules (gym_id, class_name, day_of_week, start_time, end_time, instructor)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (gym_id, schedule.class_name, schedule.day_of_week, schedule.start_time, schedule.end_time, schedule.instructor))

        schedule_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return {
            "message": "Schedule created successfully",
            "schedule_id": schedule_id
        }
    except Exception as e:
        logger.error(f"Error creating schedule: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create schedule")

@app.delete("/api/schedules/{schedule_id}")
async def delete_schedule(schedule_id: int, token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('DELETE FROM schedules WHERE id = ? AND gym_id = ?', (schedule_id, gym_id))

        conn.commit()
        conn.close()

        return {"message": "Schedule deleted successfully"}
    except Exception as e:
        logger.error(f"Error deleting schedule: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete schedule")

# Attendance endpoints
@app.get("/api/attendance")
async def get_attendance(token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, student_name, member_id, card_number, class_name, check_in_time
            FROM attendance_logs
            WHERE gym_id = ?
            ORDER BY check_in_time DESC
            LIMIT 500
        ''', (gym_id,))

        attendance = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return {"attendance": attendance}
    except Exception as e:
        logger.error(f"Error getting attendance: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve attendance")

@app.post("/api/attendance/checkin")
async def check_in(checkin: CheckInRequest, token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]

        conn = get_db_connection()
        cursor = conn.cursor()

        # Find student by card number or name
        student = None
        if checkin.card_number:
            cursor.execute('''
                SELECT id, name, member_id, card_number FROM students
                WHERE gym_id = ? AND card_number = ?
            ''', (gym_id, checkin.card_number))
            student = cursor.fetchone()
        elif checkin.student_name:
            cursor.execute('''
                SELECT id, name, member_id, card_number FROM students
                WHERE gym_id = ? AND name = ?
            ''', (gym_id, checkin.student_name))
            student = cursor.fetchone()

        if not student:
            conn.close()
            raise HTTPException(status_code=404, detail="Student not found")

        # Get current class based on time
        now = datetime.now()
        day_of_week = now.weekday()
        current_time = now.strftime("%H:%M")

        cursor.execute('''
            SELECT class_name FROM schedules
            WHERE gym_id = ? AND day_of_week = ? AND start_time <= ? AND end_time >= ?
            ORDER BY start_time DESC LIMIT 1
        ''', (gym_id, day_of_week, current_time, current_time))

        schedule = cursor.fetchone()
        class_name = schedule["class_name"] if schedule else "Open Mat"

        # Log attendance
        cursor.execute('''
            INSERT INTO attendance_logs (gym_id, student_name, student_id, member_id, card_number, class_name, check_in_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (gym_id, student["name"], student["id"], student["member_id"], student["card_number"], class_name, datetime.now()))

        conn.commit()
        conn.close()

        return {
            "message": "Check-in successful",
            "student_name": student["name"],
            "member_id": student["member_id"],
            "class_name": class_name,
            "check_in_time": datetime.now().isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking in: {str(e)}")
        raise HTTPException(status_code=500, detail="Check-in failed")

# Analytics endpoint
@app.get("/api/analytics")
async def get_analytics(token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]

        conn = get_db_connection()
        cursor = conn.cursor()

        # Total students
        cursor.execute('SELECT COUNT(*) as count FROM students WHERE gym_id = ?', (gym_id,))
        total_students = cursor.fetchone()["count"]

        # Total revenue this month
        cursor.execute('''
            SELECT COALESCE(SUM(amount), 0) as total FROM payments
            WHERE gym_id = ? AND strftime('%Y-%m', payment_date) = strftime('%Y-%m', 'now')
        ''', (gym_id,))
        monthly_revenue = cursor.fetchone()["total"]

        # Check-ins today
        cursor.execute('''
            SELECT COUNT(*) as count FROM attendance_logs
            WHERE gym_id = ? AND DATE(check_in_time) = DATE('now')
        ''', (gym_id,))
        checkins_today = cursor.fetchone()["count"]

        # Active students (checked in within last 7 days)
        cursor.execute('''
            SELECT COUNT(DISTINCT student_id) as count FROM attendance_logs
            WHERE gym_id = ? AND check_in_time >= datetime('now', '-7 days')
        ''', (gym_id,))
        active_students = cursor.fetchone()["count"]

        # Belt distribution
        cursor.execute('''
            SELECT belt_level, COUNT(*) as count FROM students
            WHERE gym_id = ? GROUP BY belt_level
        ''', (gym_id,))
        belt_distribution = {row["belt_level"]: row["count"] for row in cursor.fetchall()}

        # Popular classes (last 30 days)
        cursor.execute('''
            SELECT class_name, COUNT(*) as count FROM attendance_logs
            WHERE gym_id = ? AND check_in_time >= datetime('now', '-30 days')
            GROUP BY class_name ORDER BY count DESC LIMIT 5
        ''', (gym_id,))
        popular_classes = [dict(row) for row in cursor.fetchall()]

        # Revenue trend (last 6 months)
        cursor.execute('''
            SELECT strftime('%Y-%m', payment_date) as month, SUM(amount) as total
            FROM payments WHERE gym_id = ?
            GROUP BY month ORDER BY month DESC LIMIT 6
        ''', (gym_id,))
        revenue_trend = [dict(row) for row in cursor.fetchall()]

        # Attendance trend (last 7 days)
        cursor.execute('''
            SELECT DATE(check_in_time) as date, COUNT(*) as count
            FROM attendance_logs WHERE gym_id = ?
            AND check_in_time >= datetime('now', '-7 days')
            GROUP BY date ORDER BY date
        ''', (gym_id,))
        attendance_trend = [dict(row) for row in cursor.fetchall()]

        conn.close()

        return {
            "summary": {
                "total_students": total_students,
                "monthly_revenue": float(monthly_revenue),
                "checkins_today": checkins_today,
                "active_students": active_students
            },
            "belt_distribution": belt_distribution,
            "popular_classes": popular_classes,
            "revenue_trend": revenue_trend,
            "attendance_trend": attendance_trend
        }
    except Exception as e:
        logger.error(f"Error getting analytics: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve analytics")

# Risk analysis endpoint
@app.get("/api/risk-analysis")
async def get_risk_analysis(token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]

        conn = get_db_connection()
        cursor = conn.cursor()

        # Get all students
        cursor.execute('SELECT id, name, member_id, belt_level FROM students WHERE gym_id = ?', (gym_id,))
        students = cursor.fetchall()

        at_risk_students = []

        for student in students:
            # Get last attendance date
            cursor.execute('''
                SELECT MAX(check_in_time) as last_attendance FROM attendance_logs
                WHERE gym_id = ? AND student_id = ?
            ''', (gym_id, student["id"]))

            last_attendance_row = cursor.fetchone()
            last_attendance_str = last_attendance_row["last_attendance"]

            if last_attendance_str:
                last_attendance = datetime.fromisoformat(last_attendance_str)
                days_absent = (datetime.now() - last_attendance).days

                # Flag students who haven't attended in 7+ days
                if days_absent >= 7:
                    risk_level = "High" if days_absent >= 14 else "Medium"
                    at_risk_students.append({
                        "id": student["id"],
                        "name": student["name"],
                        "member_id": student["member_id"],
                        "belt_level": student["belt_level"],
                        "last_attendance": last_attendance_str,
                        "days_absent": days_absent,
                        "risk_level": risk_level
                    })
            else:
                # Never attended
                at_risk_students.append({
                    "id": student["id"],
                    "name": student["name"],
                    "member_id": student["member_id"],
                    "belt_level": student["belt_level"],
                    "last_attendance": None,
                    "days_absent": 999,
                    "risk_level": "High"
                })

        # Sort by days absent (highest first)
        at_risk_students.sort(key=lambda x: x["days_absent"], reverse=True)

        conn.close()

        return {"at_risk_students": at_risk_students}
    except Exception as e:
        logger.error(f"Error getting risk analysis: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve risk analysis")

# Email notification endpoint
@app.post("/api/notifications/email")
async def send_email_notification(email: EmailRequest, token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]

        conn = get_db_connection()
        cursor = conn.cursor()

        # Log the email notification
        cursor.execute('''
            INSERT INTO email_notifications (gym_id, subject, message, recipient_count, sent_by, notification_type)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (gym_id, email.subject, email.message, email.recipient_count, token_data["owner_name"], email.notification_type))

        notification_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return {
            "message": "Email notification logged successfully",
            "notification_id": notification_id,
            "note": "Email sending functionality requires SMTP configuration"
        }
    except Exception as e:
        logger.error(f"Error logging email: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to log email notification")

@app.get("/api/notifications/history")
async def get_notification_history(token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, subject, message, recipient_count, sent_by, sent_at, notification_type
            FROM email_notifications
            WHERE gym_id = ?
            ORDER BY sent_at DESC
            LIMIT 50
        ''', (gym_id,))

        notifications = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return {"notifications": notifications}
    except Exception as e:
        logger.error(f"Error getting notification history: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve notification history")

# Student update and delete endpoints
@app.put("/api/students/{student_id}")
async def update_student(student_id: int, student: StudentCreate, token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE students
            SET name = ?, email = ?, phone = ?, belt_level = ?
            WHERE id = ? AND gym_id = ?
        ''', (student.name, student.email, student.phone, student.belt_level, student_id, gym_id))

        conn.commit()
        conn.close()

        return {"message": "Student updated successfully"}
    except Exception as e:
        logger.error(f"Error updating student: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to update student")

@app.delete("/api/students/{student_id}")
async def delete_student(student_id: int, token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('DELETE FROM students WHERE id = ? AND gym_id = ?', (student_id, gym_id))

        conn.commit()
        conn.close()

        return {"message": "Student deleted successfully"}
    except Exception as e:
        logger.error(f"Error deleting student: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete student")

# Class delete endpoint
@app.delete("/api/classes/{class_id}")
async def delete_class(class_id: int, token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('DELETE FROM classes WHERE id = ? AND gym_id = ?', (class_id, gym_id))

        conn.commit()
        conn.close()

        return {"message": "Class deleted successfully"}
    except Exception as e:
        logger.error(f"Error deleting class: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete class")

# RFID card management endpoints
@app.get("/api/rfid/cards")
async def get_rfid_cards(token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT s.id, s.name as student_name, s.member_id, s.card_number, s.created_at as assigned_date,
                   (SELECT MAX(check_in_time) FROM attendance_logs WHERE gym_id = ? AND student_id = s.id) as last_used
            FROM students s
            WHERE s.gym_id = ? AND s.card_number IS NOT NULL
            ORDER BY s.name
        ''', (gym_id, gym_id))

        cards = []
        for row in cursor.fetchall():
            card = dict(row)
            card["status"] = "Active"
            cards.append(card)

        conn.close()

        return {"cards": cards}
    except Exception as e:
        logger.error(f"Error getting RFID cards: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve RFID cards")

@app.post("/api/rfid/assign")
async def assign_rfid_card(student_id: int, card_number: str, token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE students SET card_number = ?
            WHERE id = ? AND gym_id = ?
        ''', (card_number, student_id, gym_id))

        conn.commit()
        conn.close()

        return {"message": "RFID card assigned successfully"}
    except Exception as e:
        logger.error(f"Error assigning RFID card: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to assign RFID card")

# Health check endpoint
@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "3.0.0-production-multitenant",
        "timestamp": datetime.now().isoformat(),
        "features": ["multi-tenant", "registration", "payments", "analytics"]
    }

# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    logger.info("🚀 Starting BJJ Pro Gym Multi-Tenant API...")
    try:
        init_db()
    except Exception as e:
        logger.error(f"Database init failed but continuing: {str(e)}")
    logger.info("✅ BJJ Pro Gym Multi-Tenant API Started!")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
