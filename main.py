from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, timedelta
import sqlite3
import hashlib
import jwt
from typing import Optional, List
import uvicorn
import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="BJJ PRO GYM API - Professional Edition")

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
class LoginRequest(BaseModel):
    card_code: str
    password: Optional[str] = None

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

# Database setup with better error handling
def get_db_connection():
    try:
        conn = sqlite3.connect('bjj_pro_gym.db', timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.error(f"Database connection error: {str(e)}")
        raise

def init_db():
    try:
        logger.info("Initializing database...")
        conn = get_db_connection()
        cursor = conn.cursor()
       
        # Create gyms table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS gyms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gym_name TEXT NOT NULL,
                owner_name TEXT NOT NULL,
                owner_email TEXT NOT NULL,
                subscription_status TEXT DEFAULT 'active',
                subscription_plan TEXT DEFAULT 'professional',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
       
        # Create gym_admins table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS gym_admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gym_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                card_code TEXT,
                password_hash TEXT,
                role TEXT DEFAULT 'admin',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (gym_id) REFERENCES gyms (id)
            )
        ''')
       
        # Create demo gym if it doesn't exist
        cursor.execute("SELECT COUNT(*) FROM gyms WHERE id = 1")
        if cursor.fetchone()[0] == 0:
            cursor.execute('''
                INSERT INTO gyms (id, gym_name, owner_name, owner_email)
                VALUES (?, ?, ?, ?)
            ''', (1, "Demo BJJ Pro Academy", "Demo Owner", "demo@bjjprogym.com"))
       
        # Create demo admin
        cursor.execute("SELECT COUNT(*) FROM gym_admins WHERE gym_id = 1")
        if cursor.fetchone()[0] == 0:
            admin_password = hashlib.sha256("admin123".encode()).hexdigest()
            cursor.execute('''
                INSERT INTO gym_admins (gym_id, name, card_code, password_hash, role)
                VALUES (?, ?, ?, ?, ?)
            ''', (1, "Admin User", "ADMIN001", admin_password, "owner"))
       
        # Create other tables
        tables = [
            '''CREATE TABLE IF NOT EXISTS classes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gym_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (gym_id) REFERENCES gyms (id),
                UNIQUE(gym_id, name)
            )''',
            '''CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gym_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                email TEXT,
                phone TEXT,
                belt_level TEXT DEFAULT 'White',
                member_id TEXT,
                card_number TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (gym_id) REFERENCES gyms (id)
            )''',
            '''CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gym_id INTEGER NOT NULL,
                class_name TEXT NOT NULL,
                day_of_week INTEGER NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                instructor TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (gym_id) REFERENCES gyms (id)
            )''',
            '''CREATE TABLE IF NOT EXISTS attendance_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gym_id INTEGER NOT NULL,
                student_name TEXT NOT NULL,
                student_id INTEGER,
                member_id TEXT,
                card_number TEXT,
                class_name TEXT NOT NULL,
                check_in_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (gym_id) REFERENCES gyms (id)
            )''',
            '''CREATE TABLE IF NOT EXISTS email_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gym_id INTEGER NOT NULL,
                subject TEXT NOT NULL,
                message TEXT NOT NULL,
                recipient_count INTEGER NOT NULL,
                sent_by TEXT NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notification_type TEXT DEFAULT 'general',
                FOREIGN KEY (gym_id) REFERENCES gyms (id)
            )'''
        ]
       
        for table_sql in tables:
            cursor.execute(table_sql)
       
        # Setup demo data
        setup_demo_data(cursor)
       
        conn.commit()
        conn.close()
        logger.info("✅ Database initialized successfully!")
       
    except Exception as e:
        logger.error(f"Database initialization failed: {str(e)}")
        # Don't raise the error - let the app start anyway

def setup_demo_data(cursor):
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
            ''', (1, name, email, phone, belt, member_id, card_number))
       
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
            ''', (1, class_name, description))
       
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

# SIMPLE HEALTH CHECK - NO DATABASE DEPENDENCY
@app.get("/api/health")
async def health_check():
    """Simple health check that always works"""
    return {
        "status": "healthy",
        "version": "2.0.0-production",
        "timestamp": datetime.now().isoformat()
    }

@app.post("/api/login")
async def login(request: LoginRequest):
    try:
        if not request.password:
            raise HTTPException(status_code=400, detail="Password is required")
       
        conn = get_db_connection()
        cursor = conn.cursor()
       
        password_hash = hashlib.sha256(request.password.encode()).hexdigest()
        cursor.execute('''
            SELECT ga.*, g.gym_name FROM gym_admins ga
            JOIN gyms g ON ga.gym_id = g.id
            WHERE ga.card_code = ? AND ga.password_hash = ?
        ''', (request.card_code, password_hash))
       
        admin = cursor.fetchone()
        conn.close()
       
        if not admin:
            raise HTTPException(status_code=401, detail="Invalid credentials")
       
        token_data = {
            "admin_id": admin["id"],
            "gym_id": admin["gym_id"],
            "name": admin["name"],
            "role": admin["role"],
            "gym_name": admin["gym_name"],
            "exp": datetime.utcnow() + timedelta(hours=24)
        }
       
        token = jwt.encode(token_data, SECRET_KEY, algorithm="HS256")
       
        return {
            "access_token": token,
            "token_type": "bearer",
            "admin_info": {
                "name": admin["name"],
                "role": admin["role"],
                "gym_name": admin["gym_name"],
                "plan": "professional"
            }
        }
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        raise HTTPException(status_code=500, detail="Login failed")

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
       
        # Check for duplicate class name
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
            "class_id": class_id,
            "class": class_data.dict()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating class: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create class")

@app.delete("/api/classes/{class_id}")
async def delete_class(class_id: int, token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]
       
        conn = get_db_connection()
        cursor = conn.cursor()
       
        # Check if class exists
        cursor.execute('SELECT name FROM classes WHERE id = ? AND gym_id = ?', (class_id, gym_id))
        class_result = cursor.fetchone()
       
        if not class_result:
            conn.close()
            raise HTTPException(status_code=404, detail="Class not found")
       
        # Delete the class
        cursor.execute('DELETE FROM classes WHERE id = ? AND gym_id = ?', (class_id, gym_id))
        conn.commit()
        conn.close()
       
        logger.info(f"Class deleted: {class_result[0]} (ID: {class_id})")
        return {"message": f"Class '{class_result[0]}' deleted successfully"}
       
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting class: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete class")

# Current class endpoint for check-ins
@app.get("/api/current-class")
async def get_current_class():
    """Get the currently scheduled class based on day/time"""
    now = datetime.now()
    current_day = now.weekday()  # 0 = Monday
    current_time = now.strftime("%H:%M")
   
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
       
        cursor.execute('''
            SELECT class_name, instructor FROM schedules
            WHERE gym_id = 1 AND day_of_week = ?
            AND start_time <= ? AND end_time >= ?
            ORDER BY start_time LIMIT 1
        ''', (current_day, current_time, current_time))
       
        result = cursor.fetchone()
        conn.close()
       
        if result:
            return {"class_name": result[0], "instructor": result[1]}
        return {"class_name": "Open Mat", "instructor": "Open"}
    except Exception as e:
        logger.error(f"Error getting current class: {str(e)}")
        return {"class_name": "Open Mat", "instructor": "Open"}

# Check-in endpoint
@app.post("/api/checkin")
async def check_in_student(request: CheckInRequest):
    current_class = await get_current_class()
   
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
       
        # Handle check-in by card number or name
        if request.card_number:
            cursor.execute('''
                SELECT id, name, member_id, card_number FROM students
                WHERE gym_id = 1 AND card_number = ?
            ''', (request.card_number,))
            student = cursor.fetchone()
            if not student:
                raise HTTPException(status_code=404, detail="Student not found")
            student_name = student[1]
            student_id = student[0]
            member_id = student[2]
            card_number = student[3]
        else:
            cursor.execute('''
                SELECT id, name, member_id, card_number FROM students
                WHERE gym_id = 1 AND name = ?
            ''', (request.student_name,))
            student = cursor.fetchone()
            if student:
                student_id = student[0]
                member_id = student[2]
                card_number = student[3]
            else:
                student_id = None
                member_id = None
                card_number = None
            student_name = request.student_name
       
        # Record attendance
        cursor.execute('''
            INSERT INTO attendance_logs
            (gym_id, student_name, student_id, member_id, card_number, class_name, check_in_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (1, student_name, student_id, member_id, card_number, current_class["class_name"], datetime.now().isoformat()))
       
        conn.commit()
        conn.close()
       
        return {
            "message": f"Successfully checked in {student_name}",
            "member_id": member_id,
            "card_number": card_number,
            "class_name": current_class["class_name"],
            "instructor": current_class["instructor"],
            "check_in_time": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Check-in error: {str(e)}")
        raise HTTPException(status_code=500, detail="Check-in failed")

# Initialize database on startup (non-blocking)
@app.on_event("startup")
async def startup_event():
    logger.info("🚀 Starting BJJ Pro Gym API...")
    try:
        init_db()
    except Exception as e:
        logger.error(f"Database init failed but continuing: {str(e)}")
    logger.info("✅ BJJ Pro Gym API Started!")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
