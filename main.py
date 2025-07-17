from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from datetime import datetime, timedelta
import sqlite3
import hashlib
import jwt
from typing import Optional, List
import uvicorn
import os
import logging
import secrets
import string

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="BJJ PRO GYM API - Multi-Tenant Professional Edition")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security
security = HTTPBearer()
SECRET_KEY = os.getenv("SECRET_KEY", "bjj_pro_gym_secret_key_2024_professional")

# Pydantic models
class GymRegistration(BaseModel):
    gym_name: str
    owner_name: str
    owner_email: EmailStr
    owner_password: str
    access_code: Optional[str] = None

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
        
        # Create demo gym if it doesn't exist (for backwards compatibility)
        cursor.execute("SELECT COUNT(*) FROM gyms WHERE gym_code = 'DEMO0001'")
        if cursor.fetchone()[0] == 0:
            demo_password = hashlib.sha256("admin123".encode()).hexdigest()
            cursor.execute('''
                INSERT INTO gyms (gym_code, gym_name, owner_name, owner_email, password_hash, access_code)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', ('DEMO0001', 'Demo BJJ Pro Academy', 'Demo Owner', 'demo@bjjprogym.com', demo_password, 'ADELYNN14'))
            
            demo_gym_id = cursor.lastrowid
            setup_demo_data(cursor, demo_gym_id)
        
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

# Registration endpoint
@app.post("/api/register")
async def register_gym(registration: GymRegistration):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if email already exists
        cursor.execute('SELECT id FROM gyms WHERE owner_email = ?', (registration.owner_email,))
        if cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail="Email already registered")
        
        # Validate access code for special plans
        subscription_plan = "starter"
        if registration.access_code:
            if registration.access_code.upper() == "ADELYNN14":
                subscription_plan = "professional"
            else:
                conn.close()
                raise HTTPException(status_code=400, detail="Invalid access code")
        
        # Generate unique gym code
        gym_code = generate_gym_code()
        while True:
            cursor.execute('SELECT id FROM gyms WHERE gym_code = ?', (gym_code,))
            if not cursor.fetchone():
                break
            gym_code = generate_gym_code()
        
        # Hash password
        password_hash = hashlib.sha256(registration.owner_password.encode()).hexdigest()
        
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
        
        return {
            "message": "Gym registered successfully",
            "gym_code": gym_code,
            "subscription_plan": subscription_plan,
            "gym_id": gym_id
        }
        
    except Exception as e:
        logger.error(f"Registration error: {str(e)}")
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

# Login endpoint (updated)
@app.post("/api/login")
async def login(request: LoginRequest):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        password_hash = hashlib.sha256(request.password.encode()).hexdigest()
        cursor.execute('''
            SELECT id, gym_code, gym_name, owner_name, subscription_plan, access_code
            FROM gyms
            WHERE owner_email = ? AND password_hash = ?
        ''', (request.email, password_hash))
        
        gym = cursor.fetchone()
        conn.close()
        
        if not gym:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        
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
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
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
