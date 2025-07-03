from fastapi import FastAPI, HTTPException, Depends, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from datetime import datetime, timedelta
import asyncpg
import hashlib
import jwt
import uuid
import stripe
import os
from typing import Optional, List
import uvicorn
import secrets
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="BJJ PRO GYM API - Production SaaS",
    description="Professional Brazilian Jiu-Jitsu Management System",
    version="2.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
SECRET_KEY = os.getenv("SECRET_KEY", "bjj_pro_gym_production_secret_key_2024")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/bjj_pro_gym")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "sk_test_your_stripe_key")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "whsec_your_webhook_secret")

# Initialize Stripe
stripe.api_key = STRIPE_SECRET_KEY

# Security
security = HTTPBearer()

# Access Codes Configuration
MASTER_ACCESS_CODES = {
    "Adelynn14": {
        "plan": "professional",
        "trial_days": 30,
        "features": ["all"],
        "priority": "high",
        "description": "Professional Plan - Full Access"
    },
    "DEMO2024": {
        "plan": "professional",
        "trial_days": 14,
        "features": ["all"],
        "priority": "medium",
        "description": "Demo Access Code"
    },
    "STARTER": {
        "plan": "starter",
        "trial_days": 14,
        "features": ["basic"],
        "priority": "standard",
        "description": "Starter Plan Access"
    }
}

# Stripe Price IDs (configure these in your Stripe dashboard)
STRIPE_PRICES = {
    "starter": "price_starter_97_monthly",
    "professional": "price_professional_197_monthly",
    "enterprise": "price_enterprise_397_monthly"
}

# Pydantic Models
class GymCreate(BaseModel):
    gym_name: str
    owner_name: str
    owner_email: EmailStr
    phone: Optional[str] = None
    address: Optional[str] = None

class AccessCodeRedeem(BaseModel):
    access_code: str
    gym_info: GymCreate

class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    gym_subdomain: Optional[str] = None

class CardScanRequest(BaseModel):
    card_code: str
    gym_id: Optional[str] = None

class StudentCreate(BaseModel):
    name: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    belt_level: str = "White"
    member_id: Optional[str] = None
    card_number: Optional[str] = None

class SubscriptionCreate(BaseModel):
    gym_id: str
    plan_id: str
    payment_method_id: str

class CheckInRequest(BaseModel):
    student_name: Optional[str] = None
    card_number: Optional[str] = None
    gym_id: str

# Database connection pool
async def get_db_pool():
    return await asyncpg.create_pool(DATABASE_URL)

# Database initialization
async def init_db():
    """Initialize database with production schema"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # Create gyms table (multi-tenant)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS gyms (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                subdomain VARCHAR(50) UNIQUE NOT NULL,
                custom_domain VARCHAR(100),
                gym_name VARCHAR(100) NOT NULL,
                owner_name VARCHAR(100) NOT NULL,
                owner_email VARCHAR(100) NOT NULL,
                phone VARCHAR(20),
                address TEXT,
                plan VARCHAR(20) NOT NULL DEFAULT 'starter',
                subscription_status VARCHAR(20) DEFAULT 'trial',
                trial_end_date TIMESTAMP,
                stripe_customer_id VARCHAR(100),
                stripe_subscription_id VARCHAR(100),
                access_code VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create gym_admins table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS gym_admins (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                gym_id UUID NOT NULL REFERENCES gyms(id) ON DELETE CASCADE,
                name VARCHAR(100) NOT NULL,
                email VARCHAR(100) NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                card_code VARCHAR(50),
                role VARCHAR(20) DEFAULT 'admin',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(gym_id, email)
            )
        ''')
        
        # Create students table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS students (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                gym_id UUID NOT NULL REFERENCES gyms(id) ON DELETE CASCADE,
                name VARCHAR(100) NOT NULL,
                email VARCHAR(100),
                phone VARCHAR(20),
                belt_level VARCHAR(20) DEFAULT 'White',
                member_id VARCHAR(50),
                card_number VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(gym_id, member_id),
                UNIQUE(gym_id, card_number)
            )
        ''')
        
        # Create classes table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS classes (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                gym_id UUID NOT NULL REFERENCES gyms(id) ON DELETE CASCADE,
                name VARCHAR(100) NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(gym_id, name)
            )
        ''')
        
        # Create schedules table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS schedules (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                gym_id UUID NOT NULL REFERENCES gyms(id) ON DELETE CASCADE,
                class_name VARCHAR(100) NOT NULL,
                day_of_week INTEGER NOT NULL,
                start_time TIME NOT NULL,
                end_time TIME NOT NULL,
                instructor VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create attendance_logs table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS attendance_logs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                gym_id UUID NOT NULL REFERENCES gyms(id) ON DELETE CASCADE,
                student_id UUID REFERENCES students(id) ON DELETE SET NULL,
                student_name VARCHAR(100) NOT NULL,
                member_id VARCHAR(50),
                card_number VARCHAR(50),
                class_name VARCHAR(100) NOT NULL,
                schedule_id UUID REFERENCES schedules(id) ON DELETE SET NULL,
                check_in_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notes TEXT
            )
        ''')
        
        # Create email_notifications table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS email_notifications (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                gym_id UUID NOT NULL REFERENCES gyms(id) ON DELETE CASCADE,
                subject VARCHAR(255) NOT NULL,
                message TEXT NOT NULL,
                recipient_count INTEGER NOT NULL,
                sent_by VARCHAR(100) NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notification_type VARCHAR(50) DEFAULT 'general'
            )
        ''')
        
        # Create indexes for performance
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_gyms_subdomain ON gyms(subdomain)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_students_gym_id ON students(gym_id)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_attendance_gym_id ON attendance_logs(gym_id)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_attendance_student_id ON attendance_logs(student_id)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_attendance_check_in_time ON attendance_logs(check_in_time)')
    
    await pool.close()
    logger.info("Database initialized successfully")

# Helper functions
def generate_subdomain(gym_name: str) -> str:
    """Generate a unique subdomain from gym name"""
    # Remove special characters and convert to lowercase
    subdomain = ''.join(c.lower() for c in gym_name if c.isalnum() or c == ' ')
    subdomain = subdomain.replace(' ', '-')
    
    # Add random suffix to ensure uniqueness
    suffix = secrets.token_hex(4)
    return f"{subdomain}-{suffix}"

def hash_password(password: str) -> str:
    """Hash password using SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()

def generate_jwt_token(user_data: dict) -> str:
    """Generate JWT token for user authentication"""
    payload = {
        **user_data,
        "exp": datetime.utcnow() + timedelta(hours=24)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def verify_jwt_token(token: str) -> dict:
    """Verify JWT token and return payload"""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Get current authenticated user"""
    return verify_jwt_token(credentials.credentials)

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
    trial_end_date = datetime.utcnow() + timedelta(days=code_info["trial_days"])
    
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Create gym account
            gym_id = await conn.fetchval('''
                INSERT INTO gyms (
                    subdomain, gym_name, owner_name, owner_email, phone, address,
                    plan, subscription_status, trial_end_date, access_code
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING id
            ''', subdomain, gym_info.gym_name, gym_info.owner_name, 
                gym_info.owner_email, gym_info.phone, gym_info.address,
                code_info["plan"], "trial", trial_end_date, access_code)
            
            # Create admin user
            admin_password = secrets.token_urlsafe(12)  # Generate secure password
            password_hash = hash_password(admin_password)
            
            await conn.execute('''
                INSERT INTO gym_admins (gym_id, name, email, password_hash, role)
                VALUES ($1, $2, $3, $4, $5)
            ''', gym_id, gym_info.owner_name, gym_info.owner_email, password_hash, "owner")
            
            # Create demo data for new gym
            await create_demo_data(conn, gym_id)
    
    await pool.close()
    
    # Generate dashboard URL
    dashboard_url = f"https://{subdomain}.bjjprogym.com"
    
    return {
        "success": True,
        "message": "Account created successfully",
        "gym_id": str(gym_id),
        "gym_name": gym_info.gym_name,
        "subdomain": subdomain,
        "dashboard_url": dashboard_url,
        "plan": code_info["plan"],
        "trial_days": code_info["trial_days"],
        "admin_password": admin_password,  # Send via secure email in production
        "access_code_used": access_code
    }

async def create_demo_data(conn, gym_id: str):
    """Create demo data for new gym"""
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
        await conn.execute('''
            INSERT INTO classes (gym_id, name, description)
            VALUES ($1, $2, $3)
        ''', gym_id, class_name, description)
    
    # Demo schedules
    demo_schedules = [
        ("Fundamentals", 0, "06:00", "07:00", "Instructor A"),
        ("Advanced", 0, "18:00", "19:30", "Instructor B"),
        ("No-Gi", 0, "19:45", "21:00", "Instructor C"),
        ("Kids Class", 1, "16:00", "17:00", "Instructor D")
    ]
    
    for class_name, day, start_time, end_time, instructor in demo_schedules:
        await conn.execute('''
            INSERT INTO schedules (gym_id, class_name, day_of_week, start_time, end_time, instructor)
            VALUES ($1, $2, $3, $4, $5, $6)
        ''', gym_id, class_name, day, start_time, end_time, instructor)

@app.post("/api/login")
async def login(request: LoginRequest):
    """User login endpoint"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # Get gym and admin info
        query = '''
            SELECT ga.id, ga.gym_id, ga.name, ga.email, ga.role, ga.password_hash,
                   g.gym_name, g.subdomain, g.plan, g.subscription_status
            FROM gym_admins ga
            JOIN gyms g ON ga.gym_id = g.id
            WHERE ga.email = $1
        '''
        
        if request.gym_subdomain:
            query += ' AND g.subdomain = $2'
            admin = await conn.fetchrow(query, request.email, request.gym_subdomain)
        else:
            admin = await conn.fetchrow(query, request.email)
    
    await pool.close()
    
    if not admin:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    # Verify password
    password_hash = hash_password(request.password)
    if password_hash != admin['password_hash']:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    # Generate JWT token
    token_data = {
        "admin_id": str(admin['id']),
        "gym_id": str(admin['gym_id']),
        "email": admin['email'],
        "name": admin['name'],
        "role": admin['role'],
        "gym_name": admin['gym_name'],
        "subdomain": admin['subdomain'],
        "plan": admin['plan']
    }
    
    token = generate_jwt_token(token_data)
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "admin_info": {
            "name": admin['name'],
            "email": admin['email'],
            "role": admin['role'],
            "gym_name": admin['gym_name'],
            "plan": admin['plan']
        }
    }

@app.post("/api/card-scan")
async def card_scan_check_in(request: CardScanRequest):
    """Handle card scanning for check-in"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # Check if it's an admin card
        admin = await conn.fetchrow('''
            SELECT ga.id, ga.gym_id, ga.name, ga.email, ga.role,
                   g.gym_name, g.subdomain, g.plan
            FROM gym_admins ga
            JOIN gyms g ON ga.gym_id = g.id
            WHERE ga.card_code = $1
        ''', request.card_code)
        
        if admin:
            # Admin card - return login token
            token_data = {
                "admin_id": str(admin['id']),
                "gym_id": str(admin['gym_id']),
                "name": admin['name'],
                "role": admin['role'],
                "gym_name": admin['gym_name'],
                "subdomain": admin['subdomain'],
                "plan": admin['plan']
            }
            
            token = generate_jwt_token(token_data)
            
            return {
                "type": "admin_login",
                "access_token": token,
                "admin_info": {
                    "name": admin['name'],
                    "role": admin['role'],
                    "gym_name": admin['gym_name']
                }
            }
        
        # Check if it's a student card
        student = await conn.fetchrow('''
            SELECT s.id, s.gym_id, s.name, s.member_id, s.card_number,
                   g.gym_name
            FROM students s
            JOIN gyms g ON s.gym_id = g.id
            WHERE s.card_number = $1
        ''', request.card_code)
        
        if student:
            # Student card - process check-in
            # Get current class (simplified for demo)
            current_time = datetime.now().time()
            current_day = datetime.now().weekday()
            
            schedule = await conn.fetchrow('''
                SELECT id, class_name, instructor FROM schedules
                WHERE gym_id = $1 AND day_of_week = $2 
                AND start_time <= $3 AND end_time >= $3
                ORDER BY start_time LIMIT 1
            ''', student['gym_id'], current_day, current_time)
            
            class_name = schedule['class_name'] if schedule else "Open Mat"
            instructor = schedule['instructor'] if schedule else "Open"
            schedule_id = schedule['id'] if schedule else None
            
            # Record attendance
            await conn.execute('''
                INSERT INTO attendance_logs 
                (gym_id, student_id, student_name, member_id, card_number, class_name, schedule_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
            ''', student['gym_id'], student['id'], student['name'], 
                student['member_id'], student['card_number'], class_name, schedule_id)
            
            return {
                "type": "student_checkin",
                "message": f"Welcome back, {student['name']}!",
                "student_name": student['name'],
                "member_id": student['member_id'],
                "class_name": class_name,
                "instructor": instructor,
                "gym_name": student['gym_name']
            }
    
    await pool.close()
    raise HTTPException(status_code=404, detail="Card not recognized")

@app.get("/api/students")
async def get_students(current_user: dict = Depends(get_current_user)):
    """Get students for a gym"""
    gym_id = current_user["gym_id"]
    
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        students = await conn.fetch('''
            SELECT id, name, email, phone, belt_level, member_id, card_number, created_at
            FROM students
            WHERE gym_id = $1
            ORDER BY name
        ''', gym_id)
    
    await pool.close()
    
    return {
        "students": [dict(student) for student in students]
    }

@app.post("/api/students")
async def create_student(student: StudentCreate, current_user: dict = Depends(get_current_user)):
    """Create a new student"""
    gym_id = current_user["gym_id"]
    
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # Auto-generate member ID and card number if not provided
        if not student.member_id:
            count = await conn.fetchval('SELECT COUNT(*) FROM students WHERE gym_id = $1', gym_id)
            student.member_id = f"MBR{count + 1:03d}"
        
        if not student.card_number:
            count = await conn.fetchval('SELECT COUNT(*) FROM students WHERE gym_id = $1', gym_id)
            student.card_number = f"CARD{count + 1001:04d}"
        
        try:
            student_id = await conn.fetchval('''
                INSERT INTO students (gym_id, name, email, phone, belt_level, member_id, card_number)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
            ''', gym_id, student.name, student.email, student.phone, 
                student.belt_level, student.member_id, student.card_number)
            
            return {
                "message": "Student created successfully",
                "student_id": str(student_id),
                "member_id": student.member_id,
                "card_number": student.card_number
            }
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to create student: {str(e)}")
    
    await pool.close()

@app.get("/api/analytics")
async def get_analytics(current_user: dict = Depends(get_current_user)):
    """Get analytics for a gym"""
    gym_id = current_user["gym_id"]
    
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # Belt distribution
        belt_distribution = await conn.fetch('''
            SELECT belt_level, COUNT(*) as count
            FROM students
            WHERE gym_id = $1
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
        ''', gym_id)
        
        # Total students
        total_students = await conn.fetchval('''
            SELECT COUNT(*) FROM students WHERE gym_id = $1
        ''', gym_id)
        
        # Recent attendance (last 7 days)
        recent_attendance = await conn.fetchval('''
            SELECT COUNT(*) FROM attendance_logs
            WHERE gym_id = $1 AND check_in_time >= NOW() - INTERVAL '7 days'
        ''', gym_id)
        
        # Card usage
        card_checkins = await conn.fetchval('''
            SELECT COUNT(*) FROM attendance_logs
            WHERE gym_id = $1 AND card_number IS NOT NULL
        ''', gym_id)
        
        # Classes today
        today = datetime.now().weekday()
        classes_today = await conn.fetchval('''
            SELECT COUNT(*) FROM schedules WHERE gym_id = $1 AND day_of_week = $2
        ''', gym_id, today)
    
    await pool.close()
    
    return {
        "belt_distribution": [dict(row) for row in belt_distribution],
        "total_students": total_students,
        "recent_attendance": recent_attendance,
        "card_checkins": card_checkins,
        "classes_today": classes_today,
        "subscription_plan": current_user["plan"]
    }

@app.post("/api/create-subscription")
async def create_subscription(request: SubscriptionCreate, current_user: dict = Depends(get_current_user)):
    """Create Stripe subscription for gym"""
    gym_id = request.gym_id
    
    # Verify user can modify this gym
    if current_user["gym_id"] != gym_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # Get gym info
        gym = await conn.fetchrow('''
            SELECT owner_email, gym_name, stripe_customer_id
            FROM gyms WHERE id = $1
        ''', gym_id)
        
        if not gym:
            raise HTTPException(status_code=404, detail="Gym not found")
        
        try:
            # Create or get Stripe customer
            if gym['stripe_customer_id']:
                customer = stripe.Customer.retrieve(gym['stripe_customer_id'])
            else:
                customer = stripe.Customer.create(
                    email=gym['owner_email'],
                    name=gym['gym_name'],
                    payment_method=request.payment_method_id,
                    invoice_settings={'default_payment_method': request.payment_method_id}
                )
                
                # Update gym with customer ID
                await conn.execute('''
                    UPDATE gyms SET stripe_customer_id = $1 WHERE id = $2
                ''', customer.id, gym_id)
            
            # Create subscription
            subscription = stripe.Subscription.create(
                customer=customer.id,
                items=[{'price': request.plan_id}],
                payment_behavior='default_incomplete',
                expand=['latest_invoice.payment_intent']
            )
            
            # Update gym with subscription info
            await conn.execute('''
                UPDATE gyms SET 
                    stripe_subscription_id = $1,
                    subscription_status = $2,
                    plan = $3
                WHERE id = $4
            ''', subscription.id, subscription.status, request.plan_id, gym_id)
            
            return {
                "subscription_id": subscription.id,
                "client_secret": subscription.latest_invoice.payment_intent.client_secret,
                "status": subscription.status
            }
            
        except stripe.error.StripeError as e:
            raise HTTPException(status_code=400, detail=str(e))
    
    await pool.close()

@app.post("/api/stripe-webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events"""
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    # Handle subscription events
    if event['type'] == 'invoice.payment_succeeded':
        subscription_id = event['data']['object']['subscription']
        
        # Update gym subscription status
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute('''
                UPDATE gyms SET subscription_status = 'active'
                WHERE stripe_subscription_id = $1
            ''', subscription_id)
        await pool.close()
        
    elif event['type'] == 'invoice.payment_failed':
        subscription_id = event['data']['object']['subscription']
        
        # Update gym subscription status
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute('''
                UPDATE gyms SET subscription_status = 'past_due'
                WHERE stripe_subscription_id = $1
            ''', subscription_id)
        await pool.close()
    
    return {"status": "success"}

@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "version": "2.0.0",
        "timestamp": datetime.utcnow().isoformat()
    }

# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    await init_db()
    logger.info("BJJ Pro Gym API started successfully")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
