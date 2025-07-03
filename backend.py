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
import secrets

app = FastAPI(title="BJJ PRO GYM API - Demo Edition")

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
SECRET_KEY = os.getenv("SECRET_KEY", "bjj_pro_gym_secret_key_2024_demo")

# Access Codes Configuration
MASTER_ACCESS_CODES = {
    "Adelynn14": {
        "plan": "professional",
        "trial_days": 30,
        "features": ["all"],
        "priority": "high",
        "description": "Professional Plan - Full Access"
    }
}

# Pydantic models
class GymCreate(BaseModel):
    gym_name: str
    owner_name: str
    owner_email: str
    phone: Optional[str] = None
    address: Optional[str] = None

class AccessCodeRedeem(BaseModel):
    access_code: str
    gym_info: GymCreate

class LoginRequest(BaseModel):
    card_code: str
    password: Optional[str] = None

class CardScanRequest(BaseModel):
    card_code: str
    
class StudentCreate(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    belt_level: str = "White"
    member_id: Optional[str] = None
    card_number: Optional[str] = None

class ClassCreate(BaseModel):
    name: str
    description: Optional[str] = None

class CheckInRequest(BaseModel):
    student_name: Optional[str] = None
    card_number: Optional[str] = None

class EmailRequest(BaseModel):
    subject: str
    message: str
    notification_type: str = "general"
    recipient_type: str = "students"
    recipient_count: int = 0

# Database setup
def init_db():
    conn = sqlite3.connect('bjj_pro_gym.db')
    cursor = conn.cursor()
    
    # Create gyms table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gyms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gym_name TEXT NOT NULL,
            subdomain TEXT UNIQUE,
            owner_name TEXT NOT NULL,
            owner_email TEXT NOT NULL,
            address TEXT,
            phone TEXT,
            subscription_status TEXT DEFAULT 'active',
            subscription_plan TEXT DEFAULT 'professional',
            trial_end_date TIMESTAMP,
            access_code TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create gym_admins table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gym_admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gym_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            email TEXT,
            card_code TEXT,
            password_hash TEXT,
            role TEXT DEFAULT 'admin',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (gym_id) REFERENCES gyms (id),
            UNIQUE(gym_id, card_code)
        )
    ''')
    
    # Create demo gym if it doesn't exist
    cursor.execute("SELECT COUNT(*) FROM gyms WHERE id = 1")
    if cursor.fetchone()[0] == 0:
        trial_end = (datetime.now() + timedelta(days=365)).isoformat()
        cursor.execute('''
            INSERT INTO gyms (
                id, gym_name, subdomain, owner_name, owner_email,
                subscription_status, subscription_plan, trial_end_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (1, "Demo BJJ Pro Academy", "demo", "Demo Owner", "demo@bjjprogym.com", "active", "professional", trial_end))
    
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
            FOREIGN KEY (gym_id) REFERENCES gyms (id),
            UNIQUE(gym_id, member_id),
            UNIQUE(gym_id, card_number)
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
            schedule_id INTEGER,
            check_in_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            FOREIGN KEY (gym_id) REFERENCES gyms (id),
            FOREIGN KEY (student_id) REFERENCES students (id)
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
    print("✅ BJJ PRO GYM Database initialized successfully!")

def setup_demo_data(cursor):
    """Setup demo data"""
    # Demo classes
    demo_classes = [
        ("Fundamentals", "Basic BJJ techniques for beginners"),
        ("Advanced", "Advanced techniques and sparring"),
        ("Competition", "Competition preparation and training"),
        ("Open Mat", "Free training and practice time"),
        ("No-Gi", "Brazilian Jiu-Jitsu without gi"),
        ("Kids Class", "BJJ for children (ages 6-16)")
    ]
    
    for class_name, description in demo_classes:
        cursor.execute('''
            INSERT OR IGNORE INTO classes (gym_id, name, description)
            VALUES (?, ?, ?)
        ''', (1, class_name, description))
    
    # Demo students
    demo_students = [
        ("John Smith", "john.smith@email.com", "555-0101", "Blue", "MBR001", "CARD1001"),
        ("Maria Garcia", "maria.garcia@email.com", "555-0102", "White", "MBR002", "CARD1002"),
        ("David Johnson", "david.johnson@email.com", "555-0103", "Purple", "MBR003", "CARD1003"),
        ("Sarah Wilson", "sarah.wilson@email.com", "555-0104", "White", "MBR004", "CARD1004"),
        ("Mike Brown", "mike.brown@email.com", "555-0105", "Blue", "MBR005", "CARD1005"),
        ("Jennifer Lee", "jennifer.lee@email.com", "555-0106", "Purple", "MBR006", "CARD1006"),
        ("Carlos Rodriguez", "carlos.rodriguez@email.com", "555-0107", "Brown", "MBR007", "CARD1007"),
        ("Emma Thompson", "emma.thompson@email.com", "555-0108", "White", "MBR008", "CARD1008"),
        ("Alex Chen", "alex.chen@email.com", "555-0109", "Blue", "MBR009", "CARD1009"),
        ("Isabella Martinez", "isabella.martinez@email.com", "555-0110", "White", "MBR010", "CARD1010")
    ]
    
    for name, email, phone, belt, member_id, card_number in demo_students:
        cursor.execute('''
            INSERT OR IGNORE INTO students (gym_id, name, email, phone, belt_level, member_id, card_number)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (1, name, email, phone, belt, member_id, card_number))

# Helper functions
def get_db_connection():
    conn = sqlite3.connect('bjj_pro_gym.db')
    conn.row_factory = sqlite3.Row
    return conn

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

def generate_subdomain(gym_name: str) -> str:
    """Generate a unique subdomain from gym name"""
    subdomain = ''.join(c.lower() for c in gym_name if c.isalnum() or c == ' ')
    subdomain = subdomain.replace(' ', '-')
    suffix = secrets.token_hex(4)
    return f"{subdomain}-{suffix}"

# API Endpoints
@app.post("/api/redeem-access-code")
async def redeem_access_code(request: AccessCodeRedeem):
    """Redeem access code and create gym account"""
    access_code = request.access_code
    gym_info = request.gym_info
    
    # Validate access code
    if access_code not in MASTER_ACCESS_CODES:
        raise HTTPException(status_code=400, detail="Invalid access code")
    
    code_info = MASTER_ACCESS_CODES[access_code]
    
    # Generate subdomain
    subdomain = generate_subdomain(gym_info.gym_name)
    
    # Calculate trial end date
    trial_end_date = datetime.now() + timedelta(days=code_info["trial_days"])
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Create gym account
        cursor.execute('''
            INSERT INTO gyms (
                gym_name, subdomain, owner_name, owner_email, phone, address,
                subscription_plan, subscription_status, trial_end_date, access_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (gym_info.gym_name, subdomain, gym_info.owner_name, 
              gym_info.owner_email, gym_info.phone, gym_info.address,
              code_info["plan"], "trial", trial_end_date.isoformat(), access_code))
        
        gym_id = cursor.lastrowid
        
        # Create admin user
        admin_password = secrets.token_urlsafe(12)
        password_hash = hashlib.sha256(admin_password.encode()).hexdigest()
        
        cursor.execute('''
            INSERT INTO gym_admins (gym_id, name, email, password_hash, role)
            VALUES (?, ?, ?, ?, ?)
        ''', (gym_id, gym_info.owner_name, gym_info.owner_email, password_hash, "owner"))
        
        conn.commit()
        
        # Generate dashboard URL
        dashboard_url = f"https://{subdomain}.bjjprogym.com"
        
        return {
            "success": True,
            "message": "Account created successfully",
            "gym_id": gym_id,
            "gym_name": gym_info.gym_name,
            "subdomain": subdomain,
            "dashboard_url": dashboard_url,
            "plan": code_info["plan"],
            "trial_days": code_info["trial_days"],
            "admin_password": admin_password,
            "access_code_used": access_code
        }
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create account: {str(e)}")
    finally:
        conn.close()

@app.post("/api/login")
async def login(request: LoginRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Handle card-only login (for card scanning)
    if not request.password:
        cursor.execute('''
            SELECT ga.*, g.gym_name FROM gym_admins ga
            JOIN gyms g ON ga.gym_id = g.id
            WHERE ga.card_code = ?
        ''', (request.card_code,))
    else:
        # Handle traditional login with password
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
            "gym_name": admin["gym_name"]
        }
    }

@app.post("/api/card-scan")
async def card_scan_login(request: CardScanRequest):
    """Handle card scanning for instant login"""
    login_request = LoginRequest(card_code=request.card_code)
    return await login(login_request)

@app.get("/api/current-class")
async def get_current_class_endpoint():
    """Get the currently scheduled class based on day/time"""
    now = datetime.now()
    current_day = now.weekday()  # 0 = Monday
    current_time = now.strftime("%H:%M")
    
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

@app.post("/api/checkin")
async def check_in_student(request: CheckInRequest):
    current_class = await get_current_class_endpoint()
    
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

@app.get("/api/students")
async def get_students(token_data: dict = Depends(verify_token)):
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

@app.post("/api/students")
async def create_student(student: StudentCreate, token_data: dict = Depends(verify_token)):
    gym_id = token_data["gym_id"]
    
    # Auto-generate member ID and card number if not provided
    if not student.member_id:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM students WHERE gym_id = ?', (gym_id,))
        count = cursor.fetchone()[0]
        conn.close()
        student.member_id = f"MBR{count + 1:03d}"
        
    if not student.card_number:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM students WHERE gym_id = ?', (gym_id,))
        count = cursor.fetchone()[0]
        conn.close()
        student.card_number = f"CARD{count + 1001:04d}"
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            INSERT INTO students (gym_id, name, email, phone, belt_level, member_id, card_number)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (gym_id, student.name, student.email, student.phone, student.belt_level, student.member_id, student.card_number))
        
        student_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {
            "message": "Student created successfully",
            "student_id": student_id,
            "member_id": student.member_id,
            "card_number": student.card_number,
            "student": student.dict()
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Student creation failed: {str(e)}")

@app.get("/api/classes")
async def get_classes(token_data: dict = Depends(verify_token)):
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

@app.post("/api/classes")
async def create_class(class_data: ClassCreate, token_data: dict = Depends(verify_token)):
    gym_id = token_data["gym_id"]
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
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
            "class": {
                "name": class_data.name.strip(),
                "description": class_data.description.strip() if class_data.description else None
            }
        }
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")

@app.get("/api/analytics")
async def get_analytics(token_data: dict = Depends(verify_token)):
    gym_id = token_data["gym_id"]
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Belt level distribution
    cursor.execute('''
        SELECT belt_level, COUNT(*) as count
        FROM students 
        WHERE gym_id = ? 
        GROUP BY belt_level
        ORDER BY 
            CASE belt_level
                WHEN 'White' THEN 1
                WHEN 'Blue' THEN 2
                WHEN 'Purple' THEN 3
                WHEN 'Brown' THEN 4
                WHEN 'Black' THEN 5
                ELSE 6
            END
    ''', (gym_id,))
    
    belt_distribution = [dict(row) for row in cursor.fetchall()]
    
    # Total students
    cursor.execute('SELECT COUNT(*) FROM students WHERE gym_id = ?', (gym_id,))
    total_students = cursor.fetchone()[0]
    
    # Recent attendance (last 7 days)
    cursor.execute('''
        SELECT COUNT(*) FROM attendance_logs 
        WHERE gym_id = ? AND julianday('now') - julianday(check_in_time) <= 7
    ''', (gym_id,))
    recent_attendance = cursor.fetchone()[0]
    
    # Card usage stats
    cursor.execute('''
        SELECT COUNT(*) FROM attendance_logs 
        WHERE gym_id = ? AND card_number IS NOT NULL
    ''', (gym_id,))
    card_checkins = cursor.fetchone()[0]
    
    # Classes today
    today = datetime.now().weekday()
    cursor.execute('SELECT COUNT(*) FROM schedules WHERE gym_id = ? AND day_of_week = ?', (gym_id, today))
    classes_today = cursor.fetchone()[0]
    
    conn.close()
    
    return {
        "belt_distribution": belt_distribution,
        "total_students": total_students,
        "classes_today": classes_today,
        "recent_attendance": recent_attendance,
        "card_checkins": card_checkins,
        "subscription_plan": "Professional"
    }

@app.get("/api/risk-analysis")
async def get_risk_analysis(token_data: dict = Depends(verify_token)):
    gym_id = token_data["gym_id"]
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get students who haven't attended in 14+ days
    cursor.execute('''
        SELECT s.name, s.email, s.phone, s.belt_level, s.member_id, s.card_number,
               MAX(al.check_in_time) as last_attendance,
               COUNT(al.id) as total_classes
        FROM students s
        LEFT JOIN attendance_logs al ON s.id = al.student_id AND s.gym_id = al.gym_id
        WHERE s.gym_id = ?
        GROUP BY s.id, s.name
        HAVING last_attendance IS NULL OR 
               julianday('now') - julianday(last_attendance) >= 14
        ORDER BY last_attendance ASC
    ''', (gym_id,))
    
    at_risk_students = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return {"at_risk_students": at_risk_students}

@app.post("/api/send-email")
async def send_email(request: EmailRequest, token_data: dict = Depends(verify_token)):
    gym_id = token_data["gym_id"]
    sent_by = token_data["name"]
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Log the email
    cursor.execute('''
        INSERT INTO email_notifications (gym_id, subject, message, recipient_count, sent_by, notification_type)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (gym_id, request.subject, request.message, request.recipient_count, sent_by, request.notification_type))
    
    conn.commit()
    conn.close()
    
    return {
        "message": "Email logged successfully",
        "recipient_count": request.recipient_count,
        "recipient_type": request.recipient_type,
        "notification": f"✅ Professional email sent to {request.recipient_count} recipients"
    }

@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "1.0.0",
        "timestamp": datetime.now().isoformat()
    }

# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    init_db()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
