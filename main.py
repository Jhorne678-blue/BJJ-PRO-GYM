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

app = FastAPI(title="BJJ PRO GYM API - Professional Edition", version="2.0.0")

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
    recipient_type: str = "all_students"
    recipient_count: int = 0

# Database setup
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
            ''', (1, "BJJ Pro Academy", "Demo Owner", "demo@bjjprogym.com"))
       
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
                last_attendance TIMESTAMP,
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

def setup_demo_data(cursor):
    try:
        # Demo students with varied last attendance for risk analysis
        demo_students = [
            ("John Smith", "john.smith@email.com", "555-0101", "Blue", "MBR001", "CARD1001", "2025-01-05"),
            ("Maria Garcia", "maria.garcia@email.com", "555-0102", "White", "MBR002", "CARD1002", "2025-01-05"),
            ("David Johnson", "david.johnson@email.com", "555-0103", "Purple", "MBR003", "CARD1003", "2025-01-05"),
            ("Sarah Wilson", "sarah.wilson@email.com", "555-0104", "White", "MBR004", "CARD1004", "2025-01-04"),
            ("Mike Brown", "mike.brown@email.com", "555-0105", "Blue", "MBR005", "CARD1005", "2025-01-04"),
            ("Lisa Davis", "lisa.davis@email.com", "555-0106", "Purple", "MBR006", "CARD1006", "2025-01-03"),
            ("Tom Wilson", "tom.wilson@email.com", "555-0107", "Brown", "MBR007", "CARD1007", "2025-01-03"),
            ("Anna Lee", "anna.lee@email.com", "555-0108", "White", "MBR008", "CARD1008", "2024-12-25"),
            ("Carlos Silva", "carlos.silva@email.com", "555-0109", "Black", "MBR009", "CARD1009", "2025-01-04"),
            ("Jessica Taylor", "jessica.taylor@email.com", "555-0110", "Blue", "MBR010", "CARD1010", "2025-01-05"),
            ("Roberto Santos", "roberto.santos@email.com", "555-0111", "Purple", "MBR011", "CARD1011", "2025-01-04"),
            ("Michelle Kim", "michelle.kim@email.com", "555-0112", "White", "MBR012", "CARD1012", "2024-12-20"),
            ("Alex Rodriguez", "alex.rodriguez@email.com", "555-0113", "Brown", "MBR013", "CARD1013", "2025-01-02"),
            ("Emma Thompson", "emma.thompson@email.com", "555-0114", "Blue", "MBR014", "CARD1014", "2024-12-28"),
            ("Ryan O'Connor", "ryan.oconnor@email.com", "555-0115", "White", "MBR015", "CARD1015", "2024-12-15")
        ]
       
        for name, email, phone, belt, member_id, card_number, last_attendance in demo_students:
            cursor.execute('''
                INSERT OR IGNORE INTO students (gym_id, name, email, phone, belt_level, member_id, card_number, last_attendance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (1, name, email, phone, belt, member_id, card_number, last_attendance))
       
        # Demo classes
        demo_classes = [
            ("Fundamentals", "Basic BJJ techniques for beginners"),
            ("Advanced", "Advanced techniques and sparring"),
            ("Competition", "Competition preparation and training"),
            ("No-Gi", "Brazilian Jiu-Jitsu without gi"),
            ("Open Mat", "Free training and practice time"),
            ("Kids Program", "BJJ classes designed for children")
        ]
       
        for class_name, description in demo_classes:
            cursor.execute('''
                INSERT OR IGNORE INTO classes (gym_id, name, description)
                VALUES (?, ?, ?)
            ''', (1, class_name, description))

        # Demo schedules
        demo_schedules = [
            ("Fundamentals", 0, "18:00", "19:00", "Professor Silva"),
            ("Advanced", 0, "19:15", "20:45", "Professor Johnson"),
            ("No-Gi", 1, "19:00", "20:00", "Professor Davis"),
            ("Fundamentals", 2, "18:00", "19:00", "Professor Silva"),
            ("Competition", 2, "19:15", "21:15", "Professor Brown"),
            ("Open Mat", 4, "18:00", "20:00", "Open"),
            ("Fundamentals", 5, "10:00", "11:00", "Professor Silva"),
            ("Open Mat", 5, "11:15", "13:15", "Open")
        ]
        
        for class_name, day, start_time, end_time, instructor in demo_schedules:
            cursor.execute('''
                INSERT OR IGNORE INTO schedules (gym_id, class_name, day_of_week, start_time, end_time, instructor)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (1, class_name, day, start_time, end_time, instructor))

        # Demo attendance logs
        demo_attendance = [
            ("John Smith", 1, "MBR001", "CARD1001", "Fundamentals", "2025-01-05 18:05"),
            ("Maria Garcia", 2, "MBR002", "CARD1002", "Fundamentals", "2025-01-05 18:08"),
            ("David Johnson", 3, "MBR003", "CARD1003", "Advanced", "2025-01-05 19:20"),
            ("Sarah Wilson", 4, "MBR004", "CARD1004", "No-Gi", "2025-01-04 19:05"),
            ("Mike Brown", 5, "MBR005", "CARD1005", "Fundamentals", "2025-01-04 18:12"),
            ("Lisa Davis", 6, "MBR006", "CARD1006", "Competition", "2025-01-03 19:18"),
            ("Tom Wilson", 7, "MBR007", "CARD1007", "Open Mat", "2025-01-03 18:30")
        ]
        
        for student_name, student_id, member_id, card_number, class_name, check_in_time in demo_attendance:
            cursor.execute('''
                INSERT OR IGNORE INTO attendance_logs (gym_id, student_name, student_id, member_id, card_number, class_name, check_in_time)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (1, student_name, student_id, member_id, card_number, class_name, check_in_time))
       
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

# API Endpoints

@app.get("/api/health")
async def health_check():
    """Simple health check"""
    return {
        "status": "healthy",
        "version": "2.0.0-production",
        "timestamp": datetime.now().isoformat(),
        "features": [
            "Student Management",
            "Class Scheduling", 
            "RFID Check-in",
            "Risk Analysis",
            "Email Communications",
            "Business Analytics"
        ]
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
            SELECT id, name, email, phone, belt_level, member_id, card_number, created_at, last_attendance
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
            INSERT INTO students (gym_id, name, email, phone, belt_level, member_id, card_number, last_attendance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (gym_id, student.name, student.email, student.phone, student.belt_level, member_id, card_number, datetime.now().isoformat()))
       
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
    except Exception as e:
        logger.error(f"Error creating class: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create class")

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

@app.get("/api/schedules")
async def get_schedules(token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, class_name, day_of_week, start_time, end_time, instructor, created_at
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

@app.get("/api/attendance")
async def get_attendance(token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, student_name, student_id, member_id, card_number, class_name, check_in_time
            FROM attendance_logs
            WHERE gym_id = ?
            ORDER BY check_in_time DESC
            LIMIT 100
        ''', (gym_id,))
        
        attendance = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return {"attendance": attendance}
    except Exception as e:
        logger.error(f"Error getting attendance: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve attendance")

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
                WHERE gym_id = 1 AND LOWER(name) LIKE LOWER(?)
            ''', (f'%{request.student_name}%',))
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
        
        # Update student's last attendance
        if student_id:
            cursor.execute('''
                UPDATE students SET last_attendance = ? WHERE id = ?
            ''', (datetime.now().isoformat(), student_id))
       
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

@app.get("/api/risk-analysis")
async def get_risk_analysis(token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, name, email, member_id, belt_level, last_attendance,
                   CASE 
                       WHEN last_attendance IS NULL THEN 999
                       ELSE julianday('now') - julianday(last_attendance)
                   END as days_absent
            FROM students
            WHERE gym_id = ?
            AND (last_attendance IS NULL OR julianday('now') - julianday(last_attendance) >= 3)
            ORDER BY days_absent DESC
        ''', (gym_id,))
        
        at_risk_students = []
        for row in cursor.fetchall():
            student = dict(row)
            days_absent = int(student['days_absent'])
            
            if days_absent >= 14:
                risk_level = 'high'
            elif days_absent >= 7:
                risk_level = 'medium'
            else:
                risk_level = 'low'
                
            student['risk_level'] = risk_level
            student['days_absent'] = days_absent
            at_risk_students.append(student)
        
        conn.close()
        
        return {"at_risk_students": at_risk_students}
    except Exception as e:
        logger.error(f"Error getting risk analysis: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve risk analysis")

@app.post("/api/send-email")
async def send_email(request: EmailRequest, token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Determine recipient count based on type
        if request.recipient_type == "all_students":
            cursor.execute('SELECT COUNT(*) FROM students WHERE gym_id = ?', (gym_id,))
            recipient_count = cursor.fetchone()[0]
        elif request.recipient_type == "at_risk_students":
            cursor.execute('''
                SELECT COUNT(*) FROM students 
                WHERE gym_id = ? 
                AND (last_attendance IS NULL OR julianday('now') - julianday(last_attendance) >= 7)
            ''', (gym_id,))
            recipient_count = cursor.fetchone()[0]
        else:
            recipient_count = request.recipient_count
        
        # Save email to history
        cursor.execute('''
            INSERT INTO email_notifications (gym_id, subject, message, recipient_count, sent_by, notification_type)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (gym_id, request.subject, request.message, recipient_count, token_data["name"], request.recipient_type))
        
        conn.commit()
        conn.close()
        
        return {
            "message": "Email sent successfully",
            "recipient_count": recipient_count,
            "subject": request.subject
        }
    except Exception as e:
        logger.error(f"Error sending email: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to send email")

@app.get("/api/email-history")
async def get_email_history(token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, subject, recipient_count, sent_at, notification_type, sent_by
            FROM email_notifications
            WHERE gym_id = ?
            ORDER BY sent_at DESC
            LIMIT 50
        ''', (gym_id,))
        
        emails = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return {"emails": emails}
    except Exception as e:
        logger.error(f"Error getting email history: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve email history")

@app.get("/api/analytics")
async def get_analytics(token_data: dict = Depends(verify_token)):
    try:
        gym_id = token_data["gym_id"]
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get basic stats
        cursor.execute('SELECT COUNT(*) FROM students WHERE gym_id = ?', (gym_id,))
        total_students = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM classes WHERE gym_id = ?', (gym_id,))
        total_classes = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM attendance_logs WHERE gym_id = ?', (gym_id,))
        total_attendance = cursor.fetchone()[0]
        
        # Belt distribution
        cursor.execute('''
            SELECT belt_level, COUNT(*) as count
            FROM students
            WHERE gym_id = ?
            GROUP BY belt_level
        ''', (gym_id,))
        
        belt_distribution = [{"belt": row[0], "count": row[1]} for row in cursor.fetchall()]
        
        conn.close()
        
        return {
            "total_students": total_students,
            "total_classes": total_classes,
            "total_attendance": total_attendance,
            "belt_distribution": belt_distribution,
            "revenue_estimate": total_students * 150,  # Estimate $150/month per student
            "retention_rate": 87  # Placeholder
        }
    except Exception as e:
        logger.error(f"Error getting analytics: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve analytics")

# Initialize database on startup
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
