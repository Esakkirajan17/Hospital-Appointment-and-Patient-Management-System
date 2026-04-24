"""
🏥 HOSPITAL MANAGEMENT SYSTEM - COMPLETE VERSION
With QR Code Payment & Prescription Access & Doctor Availability Check & Lab Booking System
RUN: python app.py
"""

import os
import socket
import io
import uuid
import urllib.parse
import qrcode
import base64
from datetime import datetime, date, timedelta
from flask import Flask, render_template, redirect, url_for, flash, request, jsonify, send_file, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy.orm import relationship
from sqlalchemy import func, extract

# Import SMS Service
try:
    from sms_service import sms_service
    SMS_AVAILABLE = True
    print("✅ SMS Service loaded successfully")
except ImportError as e:
    SMS_AVAILABLE = False
    print(f"⚠️ SMS Service not available: {e}")

# ========== INITIALIZE APP ==========
app = Flask(__name__)
app.config['SECRET_KEY'] = 'hospital-secret-key-2026-msu-project'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///hospital.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# File upload configuration
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'doc', 'docx'}

# Create upload directories
for folder in ['uploads/lab_reports', 'uploads/health_images', 'uploads/reports', 'temp']:
    os.makedirs(folder, exist_ok=True)

# Remove old database for fresh start (comment out after first run)
if os.path.exists('hospital.db'):
    os.remove('hospital.db')
    print("🗑️ Removed old database - creating fresh one")

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please login to access this page.'

# ========== TEMPLATE FILTERS ==========

@app.template_filter('time_12h')
def convert_to_12h(time_str):
    """Convert 24-hour time to 12-hour format with AM/PM"""
    if not time_str:
        return ''
    try:
        hours, minutes = map(int, time_str.split(':'))
        ampm = 'PM' if hours >= 12 else 'AM'
        hours = hours % 12 or 12
        return f"{hours:02d}:{minutes:02d} {ampm}"
    except:
        return time_str

@app.template_filter('date_ist')
def format_date_ist(date_obj):
    """Format date in IST (dd-mm-yyyy)"""
    if not date_obj:
        return ''
    try:
        return date_obj.strftime('%d-%m-%Y')
    except:
        return str(date_obj)

@app.template_filter('currency')
def currency_format(amount):
    """Format amount in Indian currency format"""
    if amount is None:
        return '₹0.00'
    return f'₹{amount:,.2f}'

@app.template_filter('doctor_name')
def format_doctor_name(full_name):
    """Remove duplicate Dr. prefix from doctor names"""
    if not full_name:
        return ''
    name = full_name.replace('Dr. ', '').replace('Dr ', '')
    return f"Dr. {name}"

# ========== IST TIME UTILITIES ==========

def get_ist_now():
    utc_now = datetime.utcnow()
    ist_now = utc_now + timedelta(hours=5, minutes=30)
    return ist_now

def is_doctor_available_on_day(doctor_id, appointment_date):
    """Check if doctor is available on a specific day based on schedule"""
    day_of_week = appointment_date.weekday()
    
    schedule = DoctorSchedule.query.filter_by(
        doctor_id=doctor_id,
        day_of_week=day_of_week,
        is_available=True
    ).first()
    
    if not schedule:
        return False, "Doctor is not available on this day"
    
    return True, schedule

def is_time_within_working_hours(appointment_time):
    """Check if time is between 9 AM and 9 PM"""
    try:
        hour = int(appointment_time.split(':')[0])
        if hour < 9 or hour > 21:
            return False, "Appointment time must be between 9:00 AM and 9:00 PM"
        return True, "Valid time"
    except:
        return False, "Invalid time format"

def is_time_within_doctor_schedule(doctor_id, appointment_date, appointment_time):
    """Check if time falls within doctor's working hours for that day"""
    day_of_week = appointment_date.weekday()
    
    schedule = DoctorSchedule.query.filter_by(
        doctor_id=doctor_id,
        day_of_week=day_of_week,
        is_available=True
    ).first()
    
    if not schedule:
        return False, "Doctor is not available on this day"
    
    try:
        slot_hour = int(appointment_time.split(':')[0])
        slot_minute = int(appointment_time.split(':')[1])
        slot_minutes = slot_hour * 60 + slot_minute
        
        start_hour, start_minute = map(int, schedule.start_time.split(':'))
        end_hour, end_minute = map(int, schedule.end_time.split(':'))
        start_minutes = start_hour * 60 + start_minute
        end_minutes = end_hour * 60 + end_minute
        
        if slot_minutes < start_minutes or slot_minutes >= end_minutes:
            return False, f"Doctor is only available between {schedule.start_time} and {schedule.end_time} on this day"
        
        return True, schedule
    except:
        return False, "Invalid time format"

def is_doctor_on_leave(doctor_id, appointment_date):
    """Check if doctor is on leave on a specific date"""
    leave = DoctorLeave.query.filter(
        DoctorLeave.doctor_id == doctor_id,
        DoctorLeave.start_date <= appointment_date,
        DoctorLeave.end_date >= appointment_date,
        DoctorLeave.approved == True
    ).first()
    
    if leave:
        return True, f"Doctor is on leave from {leave.start_date.strftime('%d-%m-%Y')} to {leave.end_date.strftime('%d-%m-%Y')}. Reason: {leave.reason}"
    
    return False, None

def is_time_slot_available(doctor_id, appointment_date, appointment_time):
    """Check if a time slot is available (not double booked)"""
    existing = Appointment.query.filter_by(
        doctor_id=doctor_id,
        appointment_date=appointment_date,
        appointment_time=appointment_time,
        status='scheduled'
    ).first()
    
    if existing:
        return False, "This time slot is already booked. Please choose another time."
    
    return True, "Time slot available"

def check_doctor_full_availability(doctor_id, appointment_date, appointment_time):
    """
    Comprehensive doctor availability check
    Returns: (is_available, message, schedule_info)
    """
    time_valid, time_msg = is_time_within_working_hours(appointment_time)
    if not time_valid:
        return False, time_msg, None
    
    on_leave, leave_msg = is_doctor_on_leave(doctor_id, appointment_date)
    if on_leave:
        return False, leave_msg, None
    
    day_available, schedule = is_doctor_available_on_day(doctor_id, appointment_date)
    if not day_available:
        return False, schedule, None
    
    time_in_schedule, schedule_msg = is_time_within_doctor_schedule(doctor_id, appointment_date, appointment_time)
    if not time_in_schedule:
        return False, schedule_msg, None
    
    slot_available, slot_msg = is_time_slot_available(doctor_id, appointment_date, appointment_time)
    if not slot_available:
        return False, slot_msg, None
    
    return True, "Doctor is available", schedule

def get_doctor_available_slots(doctor_id, appointment_date):
    """Get all available time slots for a doctor on a specific date"""
    all_slots = [
        '09:00', '09:30', '10:00', '10:30', '11:00', '11:30',
        '12:00', '12:30', '13:00', '13:30', '14:00', '14:30',
        '15:00', '15:30', '16:00', '16:30', '17:00', '17:30',
        '18:00', '18:30', '19:00', '19:30', '20:00', '20:30', '21:00'
    ]
    
    available_slots = []
    for slot in all_slots:
        is_available, _, _ = check_doctor_full_availability(doctor_id, appointment_date, slot)
        if is_available:
            available_slots.append(slot)
    
    return available_slots

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# ========== DATABASE MODELS ==========

class User(db.Model, UserMixin):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    full_name = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    
    patient = relationship("Patient", back_populates="user", uselist=False)
    doctor = relationship("Doctor", back_populates="user", uselist=False)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    @property
    def is_admin(self):
        return self.role == 'admin'
    
    @property
    def is_doctor(self):
        return self.role == 'doctor'
    
    @property
    def is_patient(self):
        return self.role == 'patient'

class Patient(db.Model):
    __tablename__ = 'patients'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True)
    date_of_birth = db.Column(db.Date)
    blood_group = db.Column(db.String(5))
    height = db.Column(db.Float)
    weight = db.Column(db.Float)
    emergency_contact = db.Column(db.String(20))
    medical_history = db.Column(db.Text)
    allergies = db.Column(db.Text)
    registration_date = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = relationship("User", back_populates="patient")
    appointments = relationship("Appointment", back_populates="patient")
    prescriptions = relationship("Prescription", back_populates="patient")
    payments = relationship("PaymentTransaction", back_populates="patient")
    health_metrics = relationship("HealthMetric", back_populates="patient")
    reports = relationship("PatientReport", back_populates="patient")
    lab_bookings = relationship("LabBooking", back_populates="patient")

class Doctor(db.Model):
    __tablename__ = 'doctors'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True)
    specialization = db.Column(db.String(100))
    department = db.Column(db.String(100))
    qualification = db.Column(db.Text)
    experience = db.Column(db.Integer)
    consultation_fee = db.Column(db.Float, default=0.00)
    available_days = db.Column(db.String(100))
    available_time = db.Column(db.String(100))
    bio = db.Column(db.Text)
    rating = db.Column(db.Float, default=4.5)
    
    user = relationship("User", back_populates="doctor")
    appointments = relationship("Appointment", back_populates="doctor")
    prescriptions = relationship("Prescription", back_populates="doctor")
    schedules = relationship("DoctorSchedule", back_populates="doctor")
    leaves = relationship("DoctorLeave", back_populates="doctor")
    verified_reports = relationship("PatientReport", foreign_keys="PatientReport.verifying_doctor_id")

class Appointment(db.Model):
    __tablename__ = 'appointments'
    
    id = db.Column(db.Integer, primary_key=True)
    appointment_number = db.Column(db.String(20), unique=True, nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'))
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id'))
    appointment_date = db.Column(db.Date, nullable=False)
    appointment_time = db.Column(db.String(10), nullable=False)
    consultation_type = db.Column(db.String(20), nullable=False, default='offline')
    symptoms = db.Column(db.Text)
    recommended_dept = db.Column(db.String(100))
    status = db.Column(db.String(20), default='scheduled')
    consultation_notes = db.Column(db.Text)
    fee_charged = db.Column(db.Float, default=0.00)
    payment_status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reminder_sent = db.Column(db.Boolean, default=False)
    sms_sent = db.Column(db.Boolean, default=False)
    
    patient = relationship("Patient", back_populates="appointments")
    doctor = relationship("Doctor", back_populates="appointments")
    prescription = relationship("Prescription", back_populates="appointment", uselist=False)
    payment = relationship("PaymentTransaction", back_populates="appointment", uselist=False)
    payment_qr = relationship("PaymentQRCode", back_populates="appointment", uselist=False)
    reports = relationship("PatientReport", back_populates="appointment")

class Prescription(db.Model):
    __tablename__ = 'prescriptions'
    
    id = db.Column(db.Integer, primary_key=True)
    prescription_number = db.Column(db.String(20), unique=True, nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'))
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id'))
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointments.id'))
    diagnosis = db.Column(db.Text)
    medication = db.Column(db.Text)
    dosage = db.Column(db.Text)
    duration = db.Column(db.String(50))
    instructions = db.Column(db.Text)
    prescribed_date = db.Column(db.DateTime, default=datetime.utcnow)
    follow_up_date = db.Column(db.Date)
    pdf_path = db.Column(db.String(200))
    payment_status = db.Column(db.String(20), default='pending')
    
    patient = relationship("Patient", back_populates="prescriptions")
    doctor = relationship("Doctor", back_populates="prescriptions")
    appointment = relationship("Appointment", back_populates="prescription")

class PaymentTransaction(db.Model):
    __tablename__ = 'payment_transactions'
    
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.String(50), unique=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointments.id'))
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'))
    amount = db.Column(db.Float)
    payment_method = db.Column(db.String(50))
    payment_status = db.Column(db.String(20), default='pending')
    transaction_date = db.Column(db.DateTime, default=datetime.utcnow)
    qr_scanned = db.Column(db.Boolean, default=False)
    
    patient = relationship("Patient", back_populates="payments")
    appointment = relationship("Appointment", back_populates="payment")

class PaymentQRCode(db.Model):
    __tablename__ = 'payment_qrcodes'
    
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointments.id'))
    qr_code_data = db.Column(db.Text)
    qr_code_text = db.Column(db.String(200))
    expires_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_used = db.Column(db.Boolean, default=False)
    
    appointment = relationship("Appointment", back_populates="payment_qr")

class LabTest(db.Model):
    __tablename__ = 'lab_tests'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    price = db.Column(db.Float, default=0.00)
    preparation = db.Column(db.Text)
    report_time = db.Column(db.String(50))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class LabBooking(db.Model):
    __tablename__ = 'lab_bookings'
    
    id = db.Column(db.Integer, primary_key=True)
    booking_number = db.Column(db.String(20), unique=True, nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'))
    test_id = db.Column(db.Integer, db.ForeignKey('lab_tests.id'))
    booking_date = db.Column(db.Date, nullable=False)
    booking_time = db.Column(db.String(10), nullable=False)
    instructions = db.Column(db.Text)
    status = db.Column(db.String(20), default='pending')
    payment_status = db.Column(db.String(20), default='pending')
    amount = db.Column(db.Float, default=0.00)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    patient = relationship("Patient", back_populates="lab_bookings")
    test = relationship("LabTest", backref="bookings")

class Feedback(db.Model):
    __tablename__ = 'feedbacks'
    
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointments.id'))
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'))
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id'))
    rating = db.Column(db.Integer)
    comment = db.Column(db.Text)
    waiting_time = db.Column(db.Integer)
    recommended = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    appointment = relationship("Appointment")
    patient = relationship("Patient")
    doctor = relationship("Doctor")

class DoctorSchedule(db.Model):
    __tablename__ = 'doctor_schedules'
    
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id'))
    day_of_week = db.Column(db.Integer)
    start_time = db.Column(db.String(10))
    end_time = db.Column(db.String(10))
    slot_duration = db.Column(db.Integer, default=30)
    is_available = db.Column(db.Boolean, default=True)
    
    doctor = relationship("Doctor", back_populates="schedules")

class DoctorLeave(db.Model):
    __tablename__ = 'doctor_leaves'
    
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id'))
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    reason = db.Column(db.String(200))
    approved = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    doctor = relationship("Doctor", back_populates="leaves")

class HealthMetric(db.Model):
    __tablename__ = 'health_metrics'
    
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'))
    recorded_date = db.Column(db.DateTime, default=datetime.utcnow)
    blood_pressure_systolic = db.Column(db.Integer)
    blood_pressure_diastolic = db.Column(db.Integer)
    heart_rate = db.Column(db.Integer)
    blood_sugar = db.Column(db.Float)
    weight = db.Column(db.Float)
    notes = db.Column(db.Text)
    
    patient = relationship("Patient", back_populates="health_metrics")

class PatientReport(db.Model):
    __tablename__ = 'patient_reports'
    
    id = db.Column(db.Integer, primary_key=True)
    report_number = db.Column(db.String(20), unique=True, nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'))
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id'), nullable=True)
    verifying_doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id'), nullable=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointments.id'), nullable=True)
    report_type = db.Column(db.String(50))
    report_name = db.Column(db.String(200))
    file_path = db.Column(db.String(200))
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)
    description = db.Column(db.Text)
    is_verified = db.Column(db.Boolean, default=False)
    
    patient = relationship("Patient", back_populates="reports")
    doctor = relationship("Doctor", foreign_keys=[doctor_id])
    verifying_doctor = relationship("Doctor", foreign_keys=[verifying_doctor_id])
    appointment = relationship("Appointment", back_populates="reports")

# ========== CREATE DATABASE ==========
with app.app_context():
    db.create_all()
    print("✅ Database tables created successfully!")

# ========== HELPER FUNCTIONS ==========

def generate_appointment_number(consultation_type):
    prefix = 'ON' if consultation_type == 'online' else 'OF'
    date_str = datetime.now().strftime('%Y%m%d')
    today = date.today()
    count = Appointment.query.filter(
        Appointment.appointment_date == today,
        Appointment.consultation_type == consultation_type
    ).count() + 1
    return f"{prefix}-{date_str}-{count:03d}"

def generate_prescription_number():
    prefix = 'RX'
    date_str = datetime.now().strftime('%Y%m%d')
    count = Prescription.query.filter(
        db.func.date(Prescription.prescribed_date) == date.today()
    ).count() + 1
    return f"{prefix}-{date_str}-{count:03d}"

def generate_transaction_id():
    prefix = 'TXN'
    date_str = datetime.now().strftime('%Y%m%d%H%M%S')
    random_num = uuid.uuid4().hex[:6].upper()
    return f"{prefix}{date_str}{random_num}"

def generate_report_number():
    prefix = 'RPT'
    date_str = datetime.now().strftime('%Y%m%d')
    today = date.today()
    count = PatientReport.query.filter(
        db.func.date(PatientReport.upload_date) == today
    ).count() + 1
    return f"{prefix}-{date_str}-{count:03d}"

def generate_booking_number():
    """Generate unique lab booking number"""
    prefix = 'LB'
    date_str = datetime.now().strftime('%Y%m%d')
    count = LabBooking.query.filter(
        db.func.date(LabBooking.created_at) == date.today()
    ).count() + 1
    return f"{prefix}-{date_str}-{count:04d}"

def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip_address = s.getsockname()[0]
        s.close()
        return ip_address
    except:
        return "127.0.0.1"

def recommend_department(symptoms):
    symptoms_lower = symptoms.lower()
    triage_rules = [
        {'keywords': ['fever', 'cold', 'cough', 'flu', 'headache', 'body ache', 'throat'], 'department': 'General Medicine', 'icon': '🩺'},
        {'keywords': ['heart', 'chest pain', 'blood pressure', 'palpitation', 'high bp', 'cardiac'], 'department': 'Cardiology', 'icon': '❤️'},
        {'keywords': ['stomach', 'vomit', 'digestion', 'diarrhea', 'constipation', 'acidity', 'indigestion'], 'department': 'Gastroenterology', 'icon': '🧬'},
        {'keywords': ['headache', 'dizziness', 'seizure', 'paralysis', 'migraine', 'vertigo'], 'department': 'Neurology', 'icon': '🧠'},
        {'keywords': ['bone', 'joint', 'fracture', 'sprain', 'arthritis', 'back pain', 'muscle pain'], 'department': 'Orthopedics', 'icon': '🦴'},
        {'keywords': ['skin', 'rash', 'allergy', 'itch', 'acne', 'hives'], 'department': 'Dermatology', 'icon': '🧴'},
        {'keywords': ['child', 'baby', 'pediatric', 'infant', 'kid'], 'department': 'Pediatrics', 'icon': '👶'},
        {'keywords': ['diabetes', 'thyroid', 'blood sugar', 'weight loss', 'hormone'], 'department': 'Endocrinology', 'icon': '🩸'},
        {'keywords': ['emergency', 'severe', 'critical', 'bleeding'], 'department': 'Emergency', 'icon': '🚑'}
    ]
    for rule in triage_rules:
        for keyword in rule['keywords']:
            if keyword in symptoms_lower:
                return {
                    'department': rule['department'],
                    'icon': rule['icon'],
                    'priority': 'Urgent' if keyword in ['chest pain', 'severe', 'critical', 'bleeding'] else 'Routine'
                }
    return {
        'department': 'General Medicine',
        'icon': '🩺',
        'priority': 'Routine'
    }

def calculate_age(birth_date):
    if not birth_date:
        return "N/A"
    today = date.today()
    age = today.year - birth_date.year
    if (today.month, today.day) < (birth_date.month, birth_date.day):
        age -= 1
    return age

def generate_payment_qr(appointment_id):
    """Generate QR code for payment"""
    appointment = Appointment.query.get(appointment_id)
    
    upi_id = "hospital@okhdfcbank"
    amount = appointment.fee_charged
    name = "HMS Hospital"
    note = f"Appointment {appointment.appointment_number}"
    
    upi_url = f"upi://pay?pa={upi_id}&pn={name}&am={amount}&cu=INR&tn={note}"
    
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(upi_url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    img_base64 = base64.b64encode(buffered.getvalue()).decode()
    
    payment_qr = PaymentQRCode.query.filter_by(appointment_id=appointment_id).first()
    if not payment_qr:
        payment_qr = PaymentQRCode(
            appointment_id=appointment_id,
            qr_code_data=img_base64,
            qr_code_text=upi_url,
            expires_at=datetime.utcnow() + timedelta(hours=24),
            is_used=False
        )
        db.session.add(payment_qr)
    else:
        payment_qr.qr_code_data = img_base64
        payment_qr.qr_code_text = upi_url
        payment_qr.expires_at = datetime.utcnow() + timedelta(hours=24)
        payment_qr.is_used = False
    
    db.session.commit()
    return payment_qr

def generate_appointment_qr_base64(appointment):
    """Generate QR code for appointment and return as base64"""
    doctor_name = appointment.doctor.user.full_name
    doctor_name = doctor_name.replace('Dr. ', '').replace('Dr ', '')
    qr_data = f"""
APPOINTMENT DETAILS
-------------------
ID: {appointment.appointment_number}
Patient: {appointment.patient.user.full_name}
Doctor: Dr. {doctor_name}
Specialization: {appointment.doctor.specialization}
Department: {appointment.doctor.department}
Date: {appointment.appointment_date.strftime('%d-%m-%Y')}
Time: {appointment.appointment_time}
Type: {appointment.consultation_type.upper()}
Status: {appointment.status.upper()}
Fee: ₹{appointment.fee_charged}
    """
    
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(qr_data.strip())
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode()

# ========== SMS NOTIFICATION FUNCTIONS ==========

def send_appointment_sms(appointment, sms_type='confirmation'):
    """Send SMS notification for appointment"""
    if not SMS_AVAILABLE:
        print("⚠️ SMS service not available")
        return False
    
    if not appointment or not appointment.patient or not appointment.patient.user.phone:
        print("⚠️ No phone number found for SMS")
        return False
    
    phone = appointment.patient.user.phone
    success = False
    
    try:
        if sms_type == 'confirmation':
            success, msg = sms_service.send_appointment_confirmation(phone, appointment)
            if success:
                print(f"📱 SMS sent: Appointment confirmed for {appointment.appointment_number}")
                appointment.sms_sent = True
                db.session.commit()
            else:
                print(f"⚠️ SMS failed: {msg}")
        elif sms_type == 'reminder':
            success, msg = sms_service.send_appointment_reminder(phone, appointment)
            if success:
                print(f"📱 SMS sent: Reminder for {appointment.appointment_number}")
        elif sms_type == 'cancellation':
            success, msg = sms_service.send_cancellation_notice(phone, appointment)
            if success:
                print(f"📱 SMS sent: Cancellation notice for {appointment.appointment_number}")
        
        return success
    except Exception as e:
        print(f"❌ SMS error: {e}")
        return False

# ========== CREATE DEFAULT DATA ==========
def create_default_data():
    with app.app_context():
        # Create Admin
        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin',
                email='admin@hospital.com',
                role='admin',
                full_name='System Administrator',
                phone='9876543210'
            )
            admin.set_password('admin123')
            db.session.add(admin)
        
        # Create Sample Doctors
        doctors_data = [
            {
                'username': 'dr_smith', 'email': 'dr.smith@hospital.com', 'full_name': 'John Smith',
                'specialization': 'Cardiology', 'department': 'Cardiology', 'qualification': 'MD, DM (Cardiology)',
                'experience': 15, 'fee': 500.00, 'days': 'Mon,Wed,Fri', 'time': '10:00-16:00', 
                'bio': 'Senior Cardiologist with 15+ years of experience',
                'schedule': {0: ('10:00', '16:00'), 2: ('10:00', '16:00'), 4: ('10:00', '16:00')}
            },
            {
                'username': 'dr_williams', 'email': 'dr.williams@hospital.com', 'full_name': 'Sarah Williams',
                'specialization': 'Pediatrics', 'department': 'Pediatrics', 'qualification': 'MD, DCH',
                'experience': 12, 'fee': 400.00, 'days': 'Tue,Thu,Sat', 'time': '09:00-15:00', 
                'bio': 'Pediatric specialist caring for children',
                'schedule': {1: ('09:00', '15:00'), 3: ('09:00', '15:00'), 5: ('09:00', '15:00')}
            },
            {
                'username': 'dr_brown', 'email': 'dr.brown@hospital.com', 'full_name': 'Robert Brown',
                'specialization': 'Orthopedics', 'department': 'Orthopedics', 'qualification': 'MS (Ortho)',
                'experience': 10, 'fee': 600.00, 'days': 'Mon-Fri', 'time': '11:00-18:00', 
                'bio': 'Orthopedic surgeon specializing in joint replacements',
                'schedule': {0: ('11:00', '18:00'), 1: ('11:00', '18:00'), 2: ('11:00', '18:00'), 
                            3: ('11:00', '18:00'), 4: ('11:00', '18:00')}
            }
        ]
        
        for doc_data in doctors_data:
            if not User.query.filter_by(username=doc_data['username']).first():
                doctor_user = User(
                    username=doc_data['username'], email=doc_data['email'], role='doctor',
                    full_name=doc_data['full_name'],
                    phone='9876543210'
                )
                doctor_user.set_password('doctor123')
                db.session.add(doctor_user)
                db.session.flush()
                
                doctor = Doctor(
                    user_id=doctor_user.id, specialization=doc_data['specialization'],
                    department=doc_data['department'], qualification=doc_data['qualification'],
                    experience=doc_data['experience'], consultation_fee=doc_data['fee'],
                    available_days=doc_data['days'], available_time=doc_data['time'], 
                    bio=doc_data['bio']
                )
                db.session.add(doctor)
                db.session.flush()
                
                for day, (start, end) in doc_data['schedule'].items():
                    schedule = DoctorSchedule(
                        doctor_id=doctor.id,
                        day_of_week=day,
                        start_time=start,
                        end_time=end,
                        slot_duration=30,
                        is_available=True
                    )
                    db.session.add(schedule)
        
        # Create sample patients
        patients_data = [
            {
                'username': 'john_doe', 'email': 'john.doe@example.com', 'full_name': 'John Doe',
                'phone': '9876543211', 'blood_group': 'O+', 'dob': '1990-05-15',
                'height': 175, 'weight': 70
            },
            {
                'username': 'jane_smith', 'email': 'jane.smith@example.com', 'full_name': 'Jane Smith',
                'phone': '9876543212', 'blood_group': 'A+', 'dob': '1985-08-22',
                'height': 162, 'weight': 58
            },
            {
                'username': 'sankar', 'email': 'sankar@example.com', 'full_name': 'Sankar R',
                'phone': '9876543213', 'blood_group': 'B+', 'dob': '1995-03-10',
                'height': 170, 'weight': 65
            }
        ]
        
        for pat_data in patients_data:
            if not User.query.filter_by(username=pat_data['username']).first():
                patient_user = User(
                    username=pat_data['username'], email=pat_data['email'], role='patient',
                    full_name=pat_data['full_name'], phone=pat_data['phone']
                )
                patient_user.set_password('patient123')
                db.session.add(patient_user)
                db.session.flush()
                
                patient = Patient(
                    user_id=patient_user.id, blood_group=pat_data['blood_group'],
                    date_of_birth=datetime.strptime(pat_data['dob'], '%Y-%m-%d').date(),
                    height=pat_data['height'], weight=pat_data['weight']
                )
                db.session.add(patient)
        
        # Create sample lab tests
        lab_tests = [
            {
                'name': 'Complete Blood Count (CBC)',
                'description': 'Measures different components of blood including RBC, WBC, platelets, hemoglobin',
                'price': 500,
                'preparation': 'No special preparation required',
                'report_time': '6 hours'
            },
            {
                'name': 'Thyroid Profile (T3, T4, TSH)',
                'description': 'Measures thyroid hormone levels to check thyroid function',
                'price': 800,
                'preparation': 'Fasting for 8-10 hours recommended',
                'report_time': '24 hours'
            },
            {
                'name': 'Lipid Profile',
                'description': 'Measures cholesterol and triglyceride levels',
                'price': 600,
                'preparation': 'Fasting for 9-12 hours required',
                'report_time': '12 hours'
            },
            {
                'name': 'Blood Sugar (Fasting)',
                'description': 'Measures blood glucose levels',
                'price': 200,
                'preparation': 'Fasting for 8 hours required',
                'report_time': '4 hours'
            },
            {
                'name': 'Vitamin D Test',
                'description': 'Measures Vitamin D levels in blood',
                'price': 1200,
                'preparation': 'No special preparation',
                'report_time': '48 hours'
            },
            {
                'name': 'Liver Function Test (LFT)',
                'description': 'Measures liver enzymes and function',
                'price': 700,
                'preparation': 'Fasting for 8-10 hours required',
                'report_time': '24 hours'
            },
            {
                'name': 'Kidney Function Test (KFT)',
                'description': 'Measures kidney function parameters',
                'price': 650,
                'preparation': 'Fasting for 8 hours recommended',
                'report_time': '24 hours'
            },
            {
                'name': 'Urine Routine Test',
                'description': 'Complete urine analysis',
                'price': 250,
                'preparation': 'Morning sample preferred',
                'report_time': '4 hours'
            }
        ]
        
        for test_data in lab_tests:
            if not LabTest.query.filter_by(name=test_data['name']).first():
                test = LabTest(**test_data)
                db.session.add(test)
        
        db.session.commit()
        print("✅ Default data created successfully!")

create_default_data()

# ========== USER LOADER ==========
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ========== ROUTES ==========

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = True if request.form.get('remember') else False
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            if user.is_active:
                login_user(user, remember=remember)
                flash(f'Welcome back, {user.full_name}!', 'success')
                return redirect(url_for('dashboard'))
            else:
                flash('Account is deactivated.', 'danger')
        else:
            flash('Invalid username or password.', 'danger')
    
    return render_template('auth/login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        full_name = request.form.get('full_name')
        phone = request.form.get('phone')
        role = request.form.get('role', 'patient')
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('register'))
        
        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'danger')
            return redirect(url_for('register'))
        
        user = User(
            username=username, email=email, role=role, full_name=full_name, phone=phone
        )
        user.set_password(password)
        
        db.session.add(user)
        db.session.flush()
        
        if role == 'patient':
            patient = Patient(user_id=user.id)
            db.session.add(patient)
        elif role == 'doctor':
            doctor = Doctor(user_id=user.id, consultation_fee=0.00)
            db.session.add(doctor)
        
        db.session.commit()
        
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('login'))
    
    return render_template('auth/register.html')

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.is_admin:
        return redirect(url_for('admin_dashboard'))
    elif current_user.is_doctor:
        return redirect(url_for('doctor_dashboard'))
    elif current_user.is_patient:
        return redirect(url_for('patient_dashboard'))
    return redirect(url_for('index'))

# ========== ADMIN ROUTES ==========

@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    if not current_user.is_admin:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    today = date.today()
    
    total_patients = Patient.query.count() or 0
    total_doctors = Doctor.query.count() or 0
    total_appointments = Appointment.query.count() or 0
    today_appointments = Appointment.query.filter_by(appointment_date=today).count() or 0
    
    online_count = Appointment.query.filter_by(consultation_type='online').count() or 0
    offline_count = Appointment.query.filter_by(consultation_type='offline').count() or 0
    
    total_revenue = db.session.query(db.func.sum(PaymentTransaction.amount)).filter(
        PaymentTransaction.payment_status == 'completed'
    ).scalar() or 0
    
    pending_payments = Appointment.query.filter_by(payment_status='pending', status='completed').count() or 0
    
    total_lab_tests = LabTest.query.count() or 0
    active_lab_tests = LabTest.query.filter_by(is_active=True).count() or 0
    
    pending_reports = PatientReport.query.filter_by(is_verified=False).count() or 0
    
    scheduled_appointments = Appointment.query.filter_by(status='scheduled').count() or 0
    completed_appointments = Appointment.query.filter_by(status='completed').count() or 0
    cancelled_appointments = Appointment.query.filter_by(status='cancelled').count() or 0
    
    dept_appointments = db.session.query(
        Appointment.recommended_dept, 
        func.count(Appointment.id).label('count')
    ).group_by(Appointment.recommended_dept).all()
    
    if not dept_appointments:
        dept_appointments = []
    
    stats = {
        'total_patients': total_patients,
        'total_doctors': total_doctors,
        'total_appointments': total_appointments,
        'today_appointments': today_appointments,
        'total_revenue': total_revenue,
        'pending_payments': pending_payments,
        'online_count': online_count,
        'offline_count': offline_count,
        'total_lab_tests': total_lab_tests,
        'active_lab_tests': active_lab_tests,
        'pending_reports': pending_reports,
        'scheduled_appointments': scheduled_appointments,
        'completed_appointments': completed_appointments,
        'cancelled_appointments': cancelled_appointments,
        'dept_appointments': dept_appointments
    }
    
    recent_appointments = Appointment.query.order_by(Appointment.created_at.desc()).limit(10).all()
    recent_reports = PatientReport.query.order_by(PatientReport.upload_date.desc()).limit(5).all()
    
    return render_template('admin/dashboard.html', 
                         stats=stats, 
                         appointments=recent_appointments, 
                         reports=recent_reports, 
                         now=datetime.now())

@app.route('/admin/users')
@login_required
def admin_users():
    if not current_user.is_admin:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    users = User.query.all()
    return render_template('admin/users.html', users=users)

@app.route('/admin/appointments')
@login_required
def admin_appointments():
    if not current_user.is_admin:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    appointments = Appointment.query.order_by(Appointment.appointment_date.desc()).all()
    return render_template('admin/appointments.html', appointments=appointments)

# ========== DEBUG ROUTE FOR LAB BOOKINGS ==========

@app.route('/debug-lab-bookings')
@login_required
def debug_lab_bookings():
    if not current_user.is_admin:
        return "Access denied - Admin only"
    
    bookings = LabBooking.query.all()
    output = f"<h1>Total Lab Bookings: {len(bookings)}</h1>"
    output += "<a href='/admin/lab-bookings'>← Back to Admin Lab Bookings</a><br><br>"
    
    if len(bookings) == 0:
        output += "<p style='color:red'>No bookings found in database!</p>"
        output += "<p>Try creating a test booking: <a href='/test-create-booking'>Create Test Booking</a></p>"
    else:
        for booking in bookings:
            output += f"""
            <div style="border:1px solid #ccc; margin:10px; padding:10px; border-radius:5px;">
                <strong>Booking ID:</strong> {booking.booking_number}<br>
                <strong>Patient:</strong> {booking.patient.user.full_name}<br>
                <strong>Test:</strong> {booking.test.name}<br>
                <strong>Date:</strong> {booking.booking_date}<br>
                <strong>Time:</strong> {booking.booking_time}<br>
                <strong>Status:</strong> {booking.status}<br>
                <strong>Amount:</strong> ₹{booking.amount}<br>
                <strong>Created:</strong> {booking.created_at}<br>
            </div>
            """
    
    return output

@app.route('/test-create-booking')
@login_required
def test_create_booking():
    if not current_user.is_patient:
        return "Only patients can create test bookings. Please login as a patient."
    
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    test = LabTest.query.first()
    
    if not test:
        return "No lab tests found. Please add lab tests first via Admin → Manage Lab Tests"
    
    booking_number = generate_booking_number()
    booking = LabBooking(
        booking_number=booking_number,
        patient_id=patient.id,
        test_id=test.id,
        booking_date=date.today(),
        booking_time="10:00",
        instructions="Test booking - Please ignore",
        status='pending',
        payment_status='pending',
        amount=test.price
    )
    
    db.session.add(booking)
    db.session.commit()
    
    return f"""
    <h2>✅ Test booking created successfully!</h2>
    <p><strong>Booking ID:</strong> {booking_number}</p>
    <p><strong>Patient:</strong> {patient.user.full_name}</p>
    <p><strong>Test:</strong> {test.name}</p>
    <p><strong>Amount:</strong> ₹{test.price}</p>
    <br>
    <a href='/patient/lab-bookings'>View My Bookings</a> |
    <a href='/admin/lab-bookings'>View Admin Lab Bookings</a> |
    <a href='/debug-lab-bookings'>Debug All Bookings</a>
    """

# ========== LAB BOOKINGS ADMIN ROUTE ==========

@app.route('/admin/lab-bookings')
@login_required
def admin_lab_bookings():
    """Admin view of all lab test bookings"""
    if not current_user.is_admin:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    bookings = LabBooking.query.order_by(LabBooking.created_at.desc()).all()
    
    # Debug print
    print(f"=== Admin Lab Bookings ===")
    print(f"Total bookings in database: {LabBooking.query.count()}")
    for b in bookings:
        print(f"  Booking: {b.booking_number} | Patient: {b.patient.user.full_name} | Test: {b.test.name} | Status: {b.status}")
    
    total_bookings = LabBooking.query.count() or 0
    pending_bookings = LabBooking.query.filter_by(status='pending').count() or 0
    confirmed_bookings = LabBooking.query.filter_by(status='confirmed').count() or 0
    completed_bookings = LabBooking.query.filter_by(status='completed').count() or 0
    cancelled_bookings = LabBooking.query.filter_by(status='cancelled').count() or 0
    
    total_revenue = db.session.query(db.func.sum(LabBooking.amount)).filter(
        LabBooking.payment_status == 'paid'
    ).scalar() or 0
    
    stats = {
        'total': total_bookings,
        'pending': pending_bookings,
        'confirmed': confirmed_bookings,
        'completed': completed_bookings,
        'cancelled': cancelled_bookings,
        'total_revenue': total_revenue
    }
    
    return render_template('admin/lab_bookings.html', bookings=bookings, stats=stats, now=datetime.now())

@app.route('/admin/lab-booking/<int:booking_id>/update', methods=['POST'])
@login_required
def update_lab_booking(booking_id):
    if not current_user.is_admin:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    booking = LabBooking.query.get_or_404(booking_id)
    status = request.form.get('status')
    
    if status in ['pending', 'confirmed', 'completed', 'cancelled']:
        booking.status = status
        db.session.commit()
        flash(f'✅ Booking #{booking.booking_number} status updated to {status}', 'success')
    
    return redirect(url_for('admin_lab_bookings'))

@app.route('/admin/analytics')
@login_required
def admin_analytics():
    if not current_user.is_admin:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    days = request.args.get('days', 30, type=int)
    start_date = date.today() - timedelta(days=days)
    
    daily_appointments = db.session.query(
        func.date(Appointment.appointment_date).label('date'),
        func.count(Appointment.id).label('count'),
        func.sum(Appointment.fee_charged).label('revenue')
    ).filter(
        Appointment.appointment_date >= start_date
    ).group_by('date').order_by('date').all()
    
    dept_stats = db.session.query(
        Appointment.recommended_dept,
        func.count(Appointment.id).label('count'),
        func.sum(Appointment.fee_charged).label('revenue')
    ).group_by(Appointment.recommended_dept).all()
    
    consultation_types = db.session.query(
        Appointment.consultation_type,
        func.count(Appointment.id).label('count')
    ).group_by(Appointment.consultation_type).all()
    
    total_appointments = Appointment.query.count()
    cancelled_appointments = Appointment.query.filter_by(status='cancelled').count()
    cancellation_rate = (cancelled_appointments / total_appointments * 100) if total_appointments > 0 else 0
    
    return render_template('admin/analytics.html',
                         daily=daily_appointments,
                         dept_stats=dept_stats,
                         consultation_types=consultation_types,
                         cancellation_rate=cancellation_rate,
                         days=days,
                         now=datetime.now())

@app.route('/admin/advanced-analytics')
@login_required
def admin_advanced_analytics():
    if not current_user.is_admin:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    monthly_data = []
    monthly_labels = []
    current_year = datetime.now().year
    
    for month in range(1, 13):
        month_start = date(current_year, month, 1)
        if month == 12:
            month_end = date(current_year + 1, 1, 1)
        else:
            month_end = date(current_year, month + 1, 1)
        
        count = Appointment.query.filter(
            Appointment.appointment_date >= month_start,
            Appointment.appointment_date < month_end
        ).count()
        
        monthly_data.append(count)
        monthly_labels.append(datetime(current_year, month, 1).strftime('%b'))
    
    dept_revenue = {}
    appointments = Appointment.query.filter_by(payment_status='paid').all()
    for app in appointments:
        dept = app.recommended_dept or 'General Medicine'
        dept_revenue[dept] = dept_revenue.get(dept, 0) + (app.fee_charged or 0)
    
    dept_labels = list(dept_revenue.keys())
    dept_revenue_values = list(dept_revenue.values())
    
    online_count = Appointment.query.filter_by(consultation_type='online').count()
    offline_count = Appointment.query.filter_by(consultation_type='offline').count()
    
    type_labels = ['Online', 'Offline']
    type_data = [online_count, offline_count]
    
    age_groups = {'0-18': 0, '19-35': 0, '36-50': 0, '51-70': 0, '70+': 0}
    patients = Patient.query.all()
    
    for patient in patients:
        if patient.date_of_birth:
            age = calculate_age(patient.date_of_birth)
            if age <= 18:
                age_groups['0-18'] += 1
            elif age <= 35:
                age_groups['19-35'] += 1
            elif age <= 50:
                age_groups['36-50'] += 1
            elif age <= 70:
                age_groups['51-70'] += 1
            else:
                age_groups['70+'] += 1
    
    age_labels = list(age_groups.keys())
    age_data = list(age_groups.values())
    
    blood_groups = {'A+': 0, 'A-': 0, 'B+': 0, 'B-': 0, 'O+': 0, 'O-': 0, 'AB+': 0, 'AB-': 0}
    for patient in patients:
        if patient.blood_group:
            blood_groups[patient.blood_group] = blood_groups.get(patient.blood_group, 0) + 1
    
    blood_labels = [bg for bg in blood_groups.keys() if blood_groups[bg] > 0]
    blood_data = [blood_groups[bg] for bg in blood_labels]
    
    lab_tests = LabTest.query.all()
    lab_labels = [test.name[:20] for test in lab_tests]
    lab_data = [test.price for test in lab_tests]
    
    doctors = Doctor.query.all()
    doctor_labels = [doc.user.full_name[:15] for doc in doctors]
    doctor_ratings = [doc.rating or 0 for doc in doctors]
    
    avg_bp_systolic = db.session.query(func.avg(HealthMetric.blood_pressure_systolic)).scalar() or 0
    avg_bp_diastolic = db.session.query(func.avg(HealthMetric.blood_pressure_diastolic)).scalar() or 0
    avg_heart_rate = db.session.query(func.avg(HealthMetric.heart_rate)).scalar() or 0
    avg_blood_sugar = db.session.query(func.avg(HealthMetric.blood_sugar)).scalar() or 0
    
    overall_stats = {
        'total_patients': Patient.query.count(),
        'total_doctors': Doctor.query.count(),
        'total_appointments': Appointment.query.count(),
        'total_revenue': db.session.query(db.func.sum(PaymentTransaction.amount)).filter(
            PaymentTransaction.payment_status == 'completed'
        ).scalar() or 0,
        'avg_bp_systolic': avg_bp_systolic,
        'avg_bp_diastolic': avg_bp_diastolic,
        'avg_heart_rate': avg_heart_rate,
        'avg_blood_sugar': avg_blood_sugar,
    }
    
    return render_template('admin/advanced_analytics.html',
                         monthly_labels=monthly_labels,
                         monthly_data=monthly_data,
                         dept_labels=dept_labels,
                         dept_revenue=dept_revenue_values,
                         type_labels=type_labels,
                         type_data=type_data,
                         age_labels=age_labels,
                         age_data=age_data,
                         blood_labels=blood_labels,
                         blood_data=blood_data,
                         lab_labels=lab_labels,
                         lab_data=lab_data,
                         doctor_labels=doctor_labels,
                         doctor_ratings=doctor_ratings,
                         stats=overall_stats,
                         now=datetime.now())

@app.route('/admin/lab-tests')
@login_required
def admin_lab_tests():
    if not current_user.is_admin:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    tests = LabTest.query.all()
    return render_template('admin/lab_tests_admin.html', tests=tests, now=datetime.now())

@app.route('/admin/lab-tests/add', methods=['GET', 'POST'])
@login_required
def add_lab_test():
    if not current_user.is_admin:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        test = LabTest(
            name=request.form.get('name'),
            description=request.form.get('description'),
            price=float(request.form.get('price')),
            preparation=request.form.get('preparation'),
            report_time=request.form.get('report_time')
        )
        db.session.add(test)
        db.session.commit()
        flash(f'✅ Lab test "{test.name}" added successfully!', 'success')
        return redirect(url_for('admin_lab_tests'))
    
    return render_template('admin/add_lab_test.html', now=datetime.now())

@app.route('/admin/lab-tests/edit/<int:test_id>', methods=['GET', 'POST'])
@login_required
def edit_lab_test(test_id):
    if not current_user.is_admin:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    test = LabTest.query.get_or_404(test_id)
    
    if request.method == 'POST':
        test.name = request.form.get('name')
        test.description = request.form.get('description')
        test.price = float(request.form.get('price'))
        test.preparation = request.form.get('preparation')
        test.report_time = request.form.get('report_time')
        test.is_active = 'is_active' in request.form
        db.session.commit()
        flash(f'✅ Lab test "{test.name}" updated!', 'success')
        return redirect(url_for('admin_lab_tests'))
    
    return render_template('admin/edit_lab_test.html', test=test, now=datetime.now())

@app.route('/admin/doctor-schedules')
@login_required
def admin_doctor_schedules():
    if not current_user.is_admin:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    doctors = Doctor.query.all()
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    return render_template('admin/doctor_schedules.html', doctors=doctors, days=days, now=datetime.now())

@app.route('/admin/doctor-schedule/<int:doctor_id>', methods=['GET', 'POST'])
@login_required
def edit_doctor_schedule(doctor_id):
    if not current_user.is_admin:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    doctor = Doctor.query.get_or_404(doctor_id)
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    
    if request.method == 'POST':
        DoctorSchedule.query.filter_by(doctor_id=doctor_id).delete()
        
        for day in range(7):
            start = request.form.get(f'start_{day}')
            end = request.form.get(f'end_{day}')
            if start and end:
                schedule = DoctorSchedule(
                    doctor_id=doctor_id,
                    day_of_week=day,
                    start_time=start,
                    end_time=end,
                    slot_duration=int(request.form.get(f'duration_{day}', 30)),
                    is_available=True
                )
                db.session.add(schedule)
        
        db.session.commit()
        flash(f'✅ Schedule updated for Dr. {doctor.user.full_name}', 'success')
        return redirect(url_for('admin_doctor_schedules'))
    
    schedules = {s.day_of_week: s for s in doctor.schedules}
    return render_template('admin/edit_doctor_schedule.html', 
                         doctor=doctor, days=days, schedules=schedules, now=datetime.now())

@app.route('/admin/doctor-leave/<int:doctor_id>', methods=['GET', 'POST'])
@login_required
def add_doctor_leave(doctor_id):
    if not current_user.is_admin:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    doctor = Doctor.query.get_or_404(doctor_id)
    
    if request.method == 'POST':
        leave = DoctorLeave(
            doctor_id=doctor_id,
            start_date=datetime.strptime(request.form.get('start_date'), '%Y-%m-%d').date(),
            end_date=datetime.strptime(request.form.get('end_date'), '%Y-%m-%d').date(),
            reason=request.form.get('reason'),
            approved=True
        )
        db.session.add(leave)
        db.session.commit()
        flash(f'✅ Leave added for Dr. {doctor.user.full_name}', 'success')
        return redirect(url_for('admin_doctor_schedules'))
    
    return render_template('admin/add_doctor_leave.html', doctor=doctor, now=datetime.now())

@app.route('/admin/export-appointments/<string:format>')
@login_required
def export_appointments(format):
    if not current_user.is_admin:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    import pandas as pd
    import io
    
    appointments = Appointment.query.order_by(Appointment.appointment_date.desc()).all()
    
    data = []
    for app in appointments:
        data.append({
            'Appointment #': app.appointment_number,
            'Patient': app.patient.user.full_name,
            'Doctor': app.doctor.user.full_name,
            'Date': app.appointment_date.strftime('%d-%m-%Y'),
            'Time': app.appointment_time,
            'Type': app.consultation_type.upper(),
            'Symptoms': app.symptoms[:50] if app.symptoms else '',
            'Fee': app.fee_charged,
            'Status': app.status,
            'Payment': app.payment_status
        })
    
    df = pd.DataFrame(data)
    
    if format == 'excel':
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Appointments')
        output.seek(0)
        return send_file(output, as_attachment=True, 
                        download_name=f'appointments_{datetime.now().strftime("%Y%m%d")}.xlsx',
                        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    
    elif format == 'csv':
        output = io.StringIO()
        df.to_csv(output, index=False)
        response = make_response(output.getvalue())
        response.headers["Content-Disposition"] = f"attachment; filename=appointments_{datetime.now().strftime("%Y%m%d")}.csv"
        response.headers["Content-type"] = "text/csv"
        return response

# ========== DOCTOR ROUTES ==========

@app.route('/doctor/dashboard')
@login_required
def doctor_dashboard():
    if not current_user.is_doctor:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    doctor = Doctor.query.filter_by(user_id=current_user.id).first()
    
    if not doctor:
        flash('Doctor profile not found.', 'danger')
        return redirect(url_for('index'))
    
    today = date.today()
    today_appointments = Appointment.query.filter_by(
        doctor_id=doctor.id, appointment_date=today, status='scheduled'
    ).order_by(Appointment.appointment_time).all()
    
    today_online = Appointment.query.filter_by(
        doctor_id=doctor.id, appointment_date=today, consultation_type='online', status='scheduled'
    ).count()
    
    today_offline = Appointment.query.filter_by(
        doctor_id=doctor.id, appointment_date=today, consultation_type='offline', status='scheduled'
    ).count()
    
    upcoming_appointments = Appointment.query.filter_by(
        doctor_id=doctor.id, status='scheduled'
    ).filter(
        Appointment.appointment_date > today
    ).order_by(Appointment.appointment_date).limit(5).all()
    
    completed_count = Appointment.query.filter_by(
        doctor_id=doctor.id, status='completed'
    ).count()
    
    total_revenue = db.session.query(db.func.sum(PaymentTransaction.amount)).filter(
        PaymentTransaction.appointment_id.in_(
            db.session.query(Appointment.id).filter(Appointment.doctor_id == doctor.id)
        ),
        PaymentTransaction.payment_status == 'completed'
    ).scalar() or 0
    
    day_of_week = today.weekday()
    today_schedule = DoctorSchedule.query.filter_by(
        doctor_id=doctor.id,
        day_of_week=day_of_week,
        is_available=True
    ).first()
    
    return render_template('doctor/dashboard.html',
                         doctor=doctor,
                         today_appointments=today_appointments,
                         today_online=today_online,
                         today_offline=today_offline,
                         upcoming_appointments=upcoming_appointments,
                         completed_count=completed_count,
                         total_revenue=total_revenue,
                         today_schedule=today_schedule,
                         now=datetime.now())

@app.route('/doctor/profile', methods=['GET', 'POST'])
@login_required
def doctor_profile():
    if not current_user.is_doctor:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    doctor = Doctor.query.filter_by(user_id=current_user.id).first()
    
    if request.method == 'POST':
        new_fee = request.form.get('consultation_fee')
        if new_fee:
            try:
                fee = float(new_fee)
                if fee >= 0:
                    doctor.consultation_fee = fee
                    flash(f'✅ Consultation fee updated to ₹{fee:.2f}!', 'success')
                else:
                    flash('❌ Fee cannot be negative', 'danger')
            except ValueError:
                flash('❌ Please enter a valid number', 'danger')
        
        doctor.specialization = request.form.get('specialization', doctor.specialization)
        doctor.qualification = request.form.get('qualification', doctor.qualification)
        doctor.experience = request.form.get('experience', doctor.experience)
        doctor.bio = request.form.get('bio', doctor.bio)
        
        db.session.commit()
        flash('✅ Profile updated successfully!', 'success')
        return redirect(url_for('doctor_profile'))
    
    return render_template('doctor/profile.html', doctor=doctor, now=datetime.now())

@app.route('/doctor/queue')
@login_required
def doctor_queue():
    if not current_user.is_doctor:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    doctor = Doctor.query.filter_by(user_id=current_user.id).first()
    consultation_type = request.args.get('type', 'all')
    
    query = Appointment.query.filter_by(
        doctor_id=doctor.id, appointment_date=date.today(), status='scheduled'
    )
    
    if consultation_type != 'all':
        query = query.filter_by(consultation_type=consultation_type)
    
    appointments = query.order_by(Appointment.appointment_time).all()
    
    online_count = Appointment.query.filter_by(
        doctor_id=doctor.id, appointment_date=date.today(), consultation_type='online', status='scheduled'
    ).count()
    
    offline_count = Appointment.query.filter_by(
        doctor_id=doctor.id, appointment_date=date.today(), consultation_type='offline', status='scheduled'
    ).count()
    
    return render_template('doctor/queue.html',
                         doctor=doctor,
                         appointments=appointments,
                         online_count=online_count,
                         offline_count=offline_count,
                         current_filter=consultation_type,
                         now=datetime.now())

@app.route('/doctor/update-record/<int:appointment_id>', methods=['GET', 'POST'])
@login_required
def update_record(appointment_id):
    if not current_user.is_doctor:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    appointment = Appointment.query.get_or_404(appointment_id)
    doctor = Doctor.query.filter_by(user_id=current_user.id).first()
    
    if not doctor or appointment.doctor_id != doctor.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('doctor_dashboard'))
    
    if request.method == 'POST':
        diagnosis = request.form.get('diagnosis', '')
        
        medicine_names = request.form.getlist('medicine_name[]')
        medicine_dosages = request.form.getlist('medicine_dosage[]')
        medicine_durations = request.form.getlist('medicine_duration[]')
        medicine_instructions = request.form.getlist('medicine_instruction[]')
        
        medication_list = []
        for i in range(len(medicine_names)):
            if medicine_names[i] and medicine_names[i].strip():
                med_entry = f"• {medicine_names[i]}"
                if medicine_dosages[i]:
                    med_entry += f" - {medicine_dosages[i]}"
                if medicine_durations[i]:
                    med_entry += f" - {medicine_durations[i]}"
                if medicine_instructions[i]:
                    med_entry += f" - {medicine_instructions[i]}"
                medication_list.append(med_entry)
        
        medication_text = "\n".join(medication_list) if medication_list else request.form.get('medication', '')
        
        if not diagnosis or not medication_text:
            flash('Please add diagnosis and medication.', 'danger')
            return redirect(url_for('update_record', appointment_id=appointment.id))
        
        appointment.consultation_notes = request.form.get('consultation_notes', '')
        
        prescription_number = generate_prescription_number()
        prescription = Prescription(
            prescription_number=prescription_number,
            patient_id=appointment.patient_id,
            doctor_id=doctor.id,
            appointment_id=appointment.id,
            diagnosis=diagnosis,
            medication=medication_text,
            dosage=request.form.get('dosage', ''),
            duration=request.form.get('duration', ''),
            instructions=request.form.get('instructions', ''),
            follow_up_date=datetime.strptime(request.form.get('follow_up_date'), '%Y-%m-%d').date() if request.form.get('follow_up_date') else None,
            payment_status='pending'
        )
        db.session.add(prescription)
        
        appointment.status = 'completed'
        db.session.commit()
        
        generate_payment_qr(appointment.id)
        
        flash('✅ Consultation completed! Payment QR code generated.', 'success')
        return redirect(url_for('payment_qr_page', appointment_id=appointment.id))
    
    return render_template('doctor/update_record.html', 
                         appointment=appointment,
                         now=datetime.now())

@app.route('/doctor/prescriptions')
@login_required
def doctor_prescriptions():
    if not current_user.is_doctor:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    doctor = Doctor.query.filter_by(user_id=current_user.id).first()
    prescriptions = []
    if doctor:
        prescriptions = Prescription.query.filter_by(
            doctor_id=doctor.id
        ).order_by(Prescription.prescribed_date.desc()).all()
    
    return render_template('doctor/view_prescriptions.html', prescriptions=prescriptions, now=datetime.now())

@app.route('/doctor/appointments')
@login_required
def doctor_appointments():
    if not current_user.is_doctor:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    doctor = Doctor.query.filter_by(user_id=current_user.id).first()
    appointments = Appointment.query.filter_by(
        doctor_id=doctor.id
    ).order_by(Appointment.appointment_date.desc(), Appointment.appointment_time).all()
    
    return render_template('doctor/appointments.html', appointments=appointments, now=datetime.now())

@app.route('/doctor/feedbacks')
@login_required
def doctor_feedbacks():
    if not current_user.is_doctor:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    doctor = Doctor.query.filter_by(user_id=current_user.id).first()
    
    feedbacks = Feedback.query.filter_by(
        doctor_id=doctor.id
    ).order_by(Feedback.created_at.desc()).all()
    
    total_feedbacks = len(feedbacks)
    avg_rating = db.session.query(db.func.avg(Feedback.rating)).filter(
        Feedback.doctor_id == doctor.id
    ).scalar() or 0
    
    return render_template('doctor/feedbacks.html',
                         doctor=doctor,
                         feedbacks=feedbacks,
                         total=total_feedbacks,
                         avg_rating=avg_rating,
                         now=datetime.now())

@app.route('/doctor/patient-reports')
@login_required
def doctor_patient_reports():
    if not current_user.is_doctor:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    doctor = Doctor.query.filter_by(user_id=current_user.id).first()
    
    assigned_reports = PatientReport.query.filter_by(
        doctor_id=doctor.id
    ).order_by(PatientReport.upload_date.desc()).all()
    
    unassigned_reports = PatientReport.query.filter(
        PatientReport.doctor_id.is_(None),
        PatientReport.is_verified == False
    ).order_by(PatientReport.upload_date.desc()).all()
    
    return render_template('doctor/patient_reports.html',
                         doctor=doctor,
                         assigned_reports=assigned_reports,
                         unassigned_reports=unassigned_reports,
                         now=datetime.now())

@app.route('/doctor/report/<int:report_id>/view')
@login_required
def doctor_view_report(report_id):
    if not current_user.is_doctor:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    report = PatientReport.query.get_or_404(report_id)
    doctor = Doctor.query.filter_by(user_id=current_user.id).first()
    
    if report.doctor_id and report.doctor_id != doctor.id:
        flash('Access denied. This report is assigned to another doctor.', 'danger')
        return redirect(url_for('doctor_patient_reports'))
    
    return render_template('doctor/view_report.html', report=report, doctor=doctor, now=datetime.now())

@app.route('/doctor/report/<int:report_id>/assign', methods=['POST'])
@login_required
def doctor_assign_report(report_id):
    if not current_user.is_doctor:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    report = PatientReport.query.get_or_404(report_id)
    doctor = Doctor.query.filter_by(user_id=current_user.id).first()
    
    if report.doctor_id:
        flash('This report is already assigned to another doctor.', 'danger')
        return redirect(url_for('doctor_patient_reports'))
    
    report.doctor_id = doctor.id
    db.session.commit()
    
    flash(f'✅ Report #{report.report_number} assigned to you successfully!', 'success')
    return redirect(url_for('doctor_patient_reports'))

@app.route('/doctor/report/<int:report_id>/verify', methods=['POST'])
@login_required
def doctor_verify_report(report_id):
    if not current_user.is_doctor:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    report = PatientReport.query.get_or_404(report_id)
    doctor = Doctor.query.filter_by(user_id=current_user.id).first()
    
    if report.doctor_id != doctor.id:
        flash('Access denied. You are not assigned to this report.', 'danger')
        return redirect(url_for('doctor_patient_reports'))
    
    if report.is_verified:
        flash('This report is already verified.', 'info')
        return redirect(url_for('doctor_patient_reports'))
    
    report.is_verified = True
    report.verifying_doctor_id = doctor.id
    db.session.commit()
    
    flash(f'✅ Report #{report.report_number} verified successfully!', 'success')
    return redirect(url_for('doctor_patient_reports'))

@app.route('/doctor/report/<int:report_id>/download')
@login_required
def doctor_download_report(report_id):
    if not current_user.is_doctor:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    report = PatientReport.query.get_or_404(report_id)
    doctor = Doctor.query.filter_by(user_id=current_user.id).first()
    
    if report.doctor_id and report.doctor_id != doctor.id:
        flash('Access denied. This report is assigned to another doctor.', 'danger')
        return redirect(url_for('doctor_patient_reports'))
    
    if not os.path.exists(report.file_path):
        flash('File not found.', 'danger')
        return redirect(url_for('doctor_patient_reports'))
    
    return send_file(report.file_path, as_attachment=True, download_name=report.report_name)

@app.route('/doctor/schedule')
@login_required
def doctor_schedule():
    if not current_user.is_doctor:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    doctor = Doctor.query.filter_by(user_id=current_user.id).first()
    
    schedules = DoctorSchedule.query.filter_by(
        doctor_id=doctor.id
    ).order_by(DoctorSchedule.day_of_week).all()
    
    leaves = DoctorLeave.query.filter_by(
        doctor_id=doctor.id
    ).order_by(DoctorLeave.start_date.desc()).all()
    
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    
    return render_template('doctor/schedule.html',
                         doctor=doctor,
                         schedules=schedules,
                         leaves=leaves,
                         days=days,
                         now=datetime.now())

@app.route('/doctor/request-leave', methods=['GET', 'POST'])
@login_required
def request_leave():
    if not current_user.is_doctor:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    doctor = Doctor.query.filter_by(user_id=current_user.id).first()
    
    if request.method == 'POST':
        leave = DoctorLeave(
            doctor_id=doctor.id,
            start_date=datetime.strptime(request.form.get('start_date'), '%Y-%m-%d').date(),
            end_date=datetime.strptime(request.form.get('end_date'), '%Y-%m-%d').date(),
            reason=request.form.get('reason'),
            approved=False
        )
        db.session.add(leave)
        db.session.commit()
        
        flash('✅ Leave request submitted. Waiting for admin approval.', 'success')
        return redirect(url_for('doctor_schedule'))
    
    return render_template('doctor/request_leave.html', doctor=doctor, now=datetime.now())

# ========== PATIENT ROUTES ==========

@app.route('/patient/dashboard')
@login_required
def patient_dashboard():
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    
    upcoming_appointments = []
    prescriptions = []
    pending_payments = []
    today = date.today()
    
    if patient:
        upcoming_appointments = Appointment.query.filter_by(
            patient_id=patient.id, status='scheduled'
        ).filter(
            Appointment.appointment_date >= today
        ).order_by(
            Appointment.appointment_date, Appointment.appointment_time
        ).limit(5).all()
        
        prescriptions = Prescription.query.filter_by(
            patient_id=patient.id,
            payment_status='paid'
        ).order_by(Prescription.prescribed_date.desc()).limit(5).all()
        
        pending_payments = Appointment.query.filter_by(
            patient_id=patient.id,
            status='completed',
            payment_status='pending'
        ).all()
    
    return render_template('patient/dashboard.html',
                         patient=patient,
                         appointments=upcoming_appointments,
                         prescriptions=prescriptions,
                         pending_payments=pending_payments,
                         today=today,
                         now=datetime.now())

@app.route('/patient/profile', methods=['GET', 'POST'])
@login_required
def patient_profile():
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    
    if request.method == 'POST':
        current_user.phone = request.form.get('phone')
        current_user.address = request.form.get('address')
        
        dob_str = request.form.get('date_of_birth')
        if dob_str:
            try:
                patient.date_of_birth = datetime.strptime(dob_str, '%Y-%m-%d').date()
            except:
                pass
        
        patient.height = float(request.form.get('height')) if request.form.get('height') else None
        patient.weight = float(request.form.get('weight')) if request.form.get('weight') else None
        patient.blood_group = request.form.get('blood_group')
        patient.emergency_contact = request.form.get('emergency_contact')
        patient.medical_history = request.form.get('medical_history')
        patient.allergies = request.form.get('allergies')
        
        db.session.commit()
        flash('✅ Profile updated successfully!', 'success')
        return redirect(url_for('patient_profile'))
    
    return render_template('patient/profile.html', patient=patient, now=datetime.now())

@app.route('/patient/book-appointment', methods=['GET', 'POST'])
@login_required
def book_appointment():
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        patient = Patient.query.filter_by(user_id=current_user.id).first()
        if not patient:
            flash('Patient profile not found.', 'danger')
            return redirect(url_for('dashboard'))
        
        doctor_id = request.form.get('doctor_id')
        appointment_date = request.form.get('appointment_date')
        appointment_time = request.form.get('appointment_time')
        consultation_type = request.form.get('consultation_type', 'offline')
        symptoms = request.form.get('symptoms', '')
        
        if not all([doctor_id, appointment_date, appointment_time, symptoms]):
            flash('Please fill all fields.', 'danger')
            return redirect(url_for('book_appointment'))
        
        try:
            app_date = datetime.strptime(appointment_date, '%Y-%m-%d').date()
        except:
            flash('Invalid date format.', 'danger')
            return redirect(url_for('book_appointment'))
        
        if app_date < date.today():
            flash('Cannot book appointment for a past date.', 'danger')
            return redirect(url_for('book_appointment'))
        
        doctor = Doctor.query.get(doctor_id)
        if not doctor:
            flash('Doctor not found.', 'danger')
            return redirect(url_for('book_appointment'))
        
        if doctor.consultation_fee <= 0:
            flash('This doctor has not set their consultation fee yet.', 'warning')
            return redirect(url_for('book_appointment'))
        
        is_available, availability_msg, schedule = check_doctor_full_availability(doctor_id, app_date, appointment_time)
        
        if not is_available:
            flash(f'❌ {availability_msg}', 'danger')
            return redirect(url_for('book_appointment'))
        
        recommendation = recommend_department(symptoms)
        appointment_number = generate_appointment_number(consultation_type)
        
        appointment = Appointment(
            appointment_number=appointment_number,
            patient_id=patient.id,
            doctor_id=doctor_id,
            appointment_date=app_date,
            appointment_time=appointment_time,
            consultation_type=consultation_type,
            symptoms=symptoms,
            recommended_dept=recommendation['department'],
            status='scheduled',
            fee_charged=doctor.consultation_fee
        )
        
        db.session.add(appointment)
        db.session.commit()
        
        try:
            send_appointment_sms(appointment, 'confirmation')
            flash(f'✅ Appointment booked successfully! SMS sent to {current_user.phone}', 'success')
        except Exception as e:
            print(f"⚠️ SMS sending failed: {e}")
            flash(f'✅ Appointment booked successfully!', 'success')
        
        return redirect(url_for('patient_dashboard'))
    
    doctors = Doctor.query.filter(Doctor.consultation_fee > 0).all()
    
    available_doctors = []
    for doctor in doctors:
        if doctor.schedules and len(doctor.schedules) > 0:
            available_doctors.append(doctor)
    
    if not available_doctors:
        flash('⚠️ No doctors are currently available for booking. Please check back later.', 'warning')
    
    return render_template('patient/book_appointment.html', 
                         doctors=available_doctors, 
                         today=date.today().strftime('%Y-%m-%d'),
                         now=datetime.now())

@app.route('/patient/cancel-appointment/<int:appointment_id>', methods=['POST'])
@login_required
def cancel_appointment(appointment_id):
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    appointment = Appointment.query.get_or_404(appointment_id)
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    
    if not patient or appointment.patient_id != patient.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('patient_dashboard'))
    
    if appointment.status == 'cancelled':
        flash('Appointment already cancelled.', 'warning')
        return redirect(url_for('patient_appointments'))
    
    appointment.status = 'cancelled'
    db.session.commit()
    
    try:
        send_appointment_sms(appointment, 'cancellation')
    except Exception as e:
        print(f"⚠️ SMS sending failed: {e}")
    
    flash('❌ Appointment cancelled successfully.', 'warning')
    return redirect(url_for('patient_appointments'))

@app.route('/patient/symptoms-checker', methods=['GET', 'POST'])
@login_required
def symptoms_checker():
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    recommendation = None
    if request.method == 'POST':
        symptoms = request.form.get('symptoms', '')
        if symptoms:
            recommendation = recommend_department(symptoms)
            flash('✅ Symptoms analyzed successfully!', 'success')
    
    return render_template('patient/symptoms_checker.html', recommendation=recommendation, now=datetime.now())

@app.route('/patient/appointments')
@login_required
def patient_appointments():
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    appointments = []
    if patient:
        appointments = Appointment.query.filter_by(
            patient_id=patient.id
        ).order_by(Appointment.appointment_date.desc()).all()
    
    return render_template('patient/view_appointments.html', appointments=appointments, now=datetime.now())

@app.route('/patient/prescriptions')
@login_required
def patient_prescriptions():
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    prescriptions = Prescription.query.filter_by(
        patient_id=patient.id,
        payment_status='paid'
    ).order_by(Prescription.prescribed_date.desc()).all()
    
    return render_template('patient/prescriptions.html', prescriptions=prescriptions, now=datetime.now())

@app.route('/patient/payment-qr/<int:appointment_id>')
@login_required
def payment_qr_page(appointment_id):
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    appointment = Appointment.query.get_or_404(appointment_id)
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    
    if appointment.patient_id != patient.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('patient_dashboard'))
    
    if appointment.payment_status == 'paid':
        flash('Payment already completed. You can view your prescription.', 'info')
        return redirect(url_for('view_prescription_after_payment', appointment_id=appointment.id))
    
    payment_qr = generate_payment_qr(appointment.id)
    prescription = Prescription.query.filter_by(appointment_id=appointment.id).first()
    
    return render_template('patient/payment_qr.html',
                         appointment=appointment,
                         payment_qr=payment_qr,
                         prescription=prescription,
                         now=datetime.now())

@app.route('/patient/process-payment/<int:appointment_id>', methods=['POST'])
@login_required
def process_payment(appointment_id):
    if not current_user.is_patient:
        return jsonify({'success': False, 'message': 'Access denied'})
    
    appointment = Appointment.query.get_or_404(appointment_id)
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    
    if appointment.patient_id != patient.id:
        return jsonify({'success': False, 'message': 'Access denied'})
    
    if appointment.payment_status == 'paid':
        return jsonify({'success': False, 'message': 'Payment already completed'})
    
    transaction_id = generate_transaction_id()
    payment_method = request.form.get('payment_method', 'qr')
    
    transaction = PaymentTransaction(
        transaction_id=transaction_id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        amount=appointment.fee_charged,
        payment_method=payment_method,
        payment_status='completed',
        qr_scanned=(payment_method == 'qr')
    )
    db.session.add(transaction)
    
    appointment.payment_status = 'paid'
    
    prescription = Prescription.query.filter_by(appointment_id=appointment.id).first()
    if prescription:
        prescription.payment_status = 'paid'
    
    payment_qr = PaymentQRCode.query.filter_by(appointment_id=appointment.id).first()
    if payment_qr:
        payment_qr.is_used = True
    
    db.session.commit()
    
    flash('✅ Payment successful! You can now view your prescription.', 'success')
    return jsonify({'success': True, 'redirect': url_for('view_prescription_after_payment', appointment_id=appointment.id)})

@app.route('/patient/prescription-after-payment/<int:appointment_id>')
@login_required
def view_prescription_after_payment(appointment_id):
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    appointment = Appointment.query.get_or_404(appointment_id)
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    
    if appointment.patient_id != patient.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('patient_dashboard'))
    
    if appointment.payment_status != 'paid':
        flash('Please complete payment first to view prescription.', 'warning')
        return redirect(url_for('payment_qr_page', appointment_id=appointment.id))
    
    prescription = Prescription.query.filter_by(appointment_id=appointment_id).first()
    
    if not prescription:
        flash('No prescription found for this appointment.', 'warning')
        return redirect(url_for('patient_dashboard'))
    
    payment = PaymentTransaction.query.filter_by(appointment_id=appointment_id, payment_status='completed').first()
    
    return render_template('patient/prescription_after_payment.html',
                         appointment=appointment,
                         prescription=prescription,
                         payment=payment,
                         now=datetime.now())

@app.route('/patient/download-prescription/<int:prescription_id>')
@login_required
def download_prescription(prescription_id):
    prescription = Prescription.query.get_or_404(prescription_id)
    appointment = prescription.appointment
    
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    
    if appointment.patient_id != patient.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('patient_dashboard'))
    
    if appointment.payment_status != 'paid':
        flash('Please complete payment first to download prescription.', 'warning')
        return redirect(url_for('payment_qr_page', appointment_id=appointment.id))
    
    if not prescription.pdf_path or not os.path.exists(prescription.pdf_path):
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4
        
        filename = f"prescription_{prescription.prescription_number}.pdf"
        filepath = os.path.join('temp', filename)
        
        c = canvas.Canvas(filepath, pagesize=A4)
        width, height = A4
        
        c.setFont("Helvetica-Bold", 24)
        c.drawString(50, height - 50, "🏥 HOSPITAL MANAGEMENT SYSTEM")
        c.setFont("Helvetica", 12)
        c.drawString(50, height - 80, "123 Healthcare Avenue, Medical District")
        c.drawString(50, height - 95, "Phone: +91 9876543210 | Email: contact@hospital.com")
        c.line(50, height - 110, width - 50, height - 110)
        
        c.setFont("Helvetica-Bold", 16)
        c.drawString(50, height - 140, "DIGITAL PRESCRIPTION")
        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, height - 170, f"Prescription #: {prescription.prescription_number}")
        
        c.setFillColorRGB(0, 1, 0)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(width - 150, height - 50, "✓ PAID")
        c.setFillColorRGB(0, 0, 0)
        
        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, height - 200, "Patient Details:")
        c.setFont("Helvetica", 11)
        c.drawString(70, height - 220, f"Name: {prescription.patient.user.full_name}")
        c.drawString(70, height - 235, f"Age: {calculate_age(prescription.patient.date_of_birth)}")
        c.drawString(70, height - 250, f"Blood Group: {prescription.patient.blood_group or 'N/A'}")
        
        doctor_name = prescription.doctor.user.full_name
        doctor_name = doctor_name.replace('Dr. ', '').replace('Dr ', '')
        c.setFont("Helvetica-Bold", 12)
        c.drawString(300, height - 200, "Doctor Details:")
        c.setFont("Helvetica", 11)
        c.drawString(320, height - 220, f"Dr. {doctor_name}")
        c.drawString(320, height - 235, f"Specialization: {prescription.doctor.specialization}")
        c.drawString(320, height - 250, f"Date: {prescription.prescribed_date.strftime('%d-%m-%Y %H:%M')}")
        
        c.line(50, height - 280, width - 50, height - 280)
        
        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, height - 310, "Diagnosis:")
        c.setFont("Helvetica", 11)
        y = height - 330
        for line in prescription.diagnosis.split('\n'):
            c.drawString(70, y, line[:100])
            y -= 15
        
        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, y - 20, "Prescribed Medication:")
        y = y - 40
        for med in prescription.medication.split('\n'):
            if med.strip():
                c.setFont("Helvetica", 11)
                c.drawString(70, y, med[:100])
                y -= 15
        
        if prescription.instructions:
            c.setFont("Helvetica-Bold", 12)
            c.drawString(50, y - 15, "Instructions:")
            c.setFont("Helvetica", 11)
            c.drawString(70, y - 35, prescription.instructions[:200])
            y = y - 55
        
        if prescription.follow_up_date:
            c.setFont("Helvetica-Bold", 12)
            c.drawString(50, y - 15, "Follow-up Date:")
            c.setFont("Helvetica", 11)
            c.drawString(200, y - 15, prescription.follow_up_date.strftime('%d-%m-%Y'))
            y = y - 40
        
        payment = PaymentTransaction.query.filter_by(appointment_id=prescription.appointment_id, payment_status='completed').first()
        if payment:
            c.setFont("Helvetica", 10)
            c.drawString(50, 100, f"Payment Transaction ID: {payment.transaction_id}")
            c.drawString(50, 85, f"Payment Date: {payment.transaction_date.strftime('%d-%m-%Y %H:%M')}")
            c.drawString(50, 70, f"Amount Paid: ₹{prescription.appointment.fee_charged:.2f}")
        
        c.save()
        prescription.pdf_path = filepath
        db.session.commit()
    
    return send_file(prescription.pdf_path, as_attachment=True, download_name=f"{prescription.prescription_number}.pdf")

@app.route('/patient/feedback/<int:appointment_id>', methods=['GET', 'POST'])
@login_required
def leave_feedback(appointment_id):
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    appointment = Appointment.query.get_or_404(appointment_id)
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    
    if appointment.patient_id != patient.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('patient_dashboard'))
    
    if appointment.status != 'completed':
        flash('You can only leave feedback for completed appointments.', 'warning')
        return redirect(url_for('patient_appointments'))
    
    existing_feedback = Feedback.query.filter_by(appointment_id=appointment_id).first()
    if existing_feedback:
        flash('You have already submitted feedback for this appointment.', 'info')
        return redirect(url_for('patient_dashboard'))
    
    if request.method == 'POST':
        rating = request.form.get('rating')
        comment = request.form.get('comment')
        waiting_time = request.form.get('waiting_time', 0)
        recommended = request.form.get('recommended') == 'on'
        
        if not rating:
            flash('Please provide a rating.', 'danger')
            return redirect(url_for('leave_feedback', appointment_id=appointment_id))
        
        feedback = Feedback(
            appointment_id=appointment_id,
            patient_id=patient.id,
            doctor_id=appointment.doctor_id,
            rating=int(rating),
            comment=comment,
            waiting_time=int(waiting_time) if waiting_time else 0,
            recommended=recommended
        )
        db.session.add(feedback)
        
        doctor = appointment.doctor
        avg_rating = db.session.query(db.func.avg(Feedback.rating)).filter(
            Feedback.doctor_id == doctor.id
        ).scalar()
        doctor.rating = round(avg_rating, 1) if avg_rating else 4.5
        db.session.commit()
        
        flash('✅ Thank you for your valuable feedback!', 'success')
        return redirect(url_for('patient_dashboard'))
    
    return render_template('patient/feedback.html', appointment=appointment)

@app.route('/patient/calendar-events')
@login_required
def calendar_events():
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    
    appointments = Appointment.query.filter_by(
        patient_id=patient.id,
        status='scheduled'
    ).filter(
        Appointment.appointment_date >= date.today()
    ).order_by(Appointment.appointment_date, Appointment.appointment_time).all()
    
    return render_template('patient/calendar_events.html', 
                         appointments=appointments,
                         now=datetime.now())

@app.route('/patient/add-to-calendar/<int:appointment_id>')
@login_required
def add_to_calendar(appointment_id):
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    appointment = Appointment.query.get_or_404(appointment_id)
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    
    if appointment.patient_id != patient.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('patient_dashboard'))
    
    doctor_name = appointment.doctor.user.full_name
    doctor_name = doctor_name.replace('Dr. ', '').replace('Dr ', '')
    event_title = f"Appointment with Dr. {doctor_name}"
    event_description = f"""
Appointment Details:
- Doctor: Dr. {doctor_name}
- Department: {appointment.doctor.department}
- Type: {appointment.consultation_type.upper()}
- Symptoms: {appointment.symptoms}

Appointment #: {appointment.appointment_number}
Fee: ₹{appointment.fee_charged}
    """
    
    event_date = appointment.appointment_date.strftime('%Y%m%d')
    event_time = appointment.appointment_time.replace(':', '') + '00'
    start_time = f"{event_date}T{event_time}"
    
    end_hour = int(appointment.appointment_time.split(':')[0]) + 1
    end_time_str = f"{end_hour:02d}{appointment.appointment_time.split(':')[1]}00"
    end_time = f"{event_date}T{end_time_str}"
    
    calendar_url = f"https://www.google.com/calendar/render?action=TEMPLATE"
    calendar_url += f"&text={urllib.parse.quote(event_title)}"
    calendar_url += f"&dates={start_time}/{end_time}"
    calendar_url += f"&details={urllib.parse.quote(event_description)}"
    calendar_url += f"&location={urllib.parse.quote('Hospital')}"
    calendar_url += f"&sf=true&output=xml"
    
    return redirect(calendar_url)

@app.route('/patient/qr-display/<int:appointment_id>')
@login_required
def qr_display(appointment_id):
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    appointment = Appointment.query.get_or_404(appointment_id)
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    
    if appointment.patient_id != patient.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('patient_dashboard'))
    
    qr_base64 = generate_appointment_qr_base64(appointment)
    
    return render_template('patient/qrcode_display.html', 
                         appointment=appointment, 
                         qr_code=qr_base64,
                         now=datetime.now())

@app.route('/patient/health-dashboard')
@login_required
def patient_health_dashboard():
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    
    metrics = HealthMetric.query.filter_by(
        patient_id=patient.id
    ).order_by(HealthMetric.recorded_date.desc()).limit(30).all()
    
    metrics_data = []
    for metric in metrics:
        metrics_data.append({
            'id': metric.id,
            'recorded_date': metric.recorded_date.isoformat() if metric.recorded_date else None,
            'blood_pressure_systolic': metric.blood_pressure_systolic,
            'blood_pressure_diastolic': metric.blood_pressure_diastolic,
            'heart_rate': metric.heart_rate,
            'blood_sugar': metric.blood_sugar,
            'weight': metric.weight,
            'notes': metric.notes
        })
    
    health_score = 100
    health_factors = []
    
    if patient.height and patient.weight:
        bmi = patient.weight / ((patient.height/100) ** 2)
        if bmi < 18.5:
            health_score -= 15
            health_factors.append({'issue': 'Underweight', 'impact': -15, 'suggestion': 'Increase calorie intake, consult nutritionist'})
        elif bmi > 25:
            health_score -= 15
            health_factors.append({'issue': 'Overweight', 'impact': -15, 'suggestion': 'Regular exercise and balanced diet'})
        else:
            health_factors.append({'issue': 'Healthy BMI', 'impact': 0, 'suggestion': 'Maintain current lifestyle'})
    
    if metrics and metrics[0].blood_pressure_systolic and metrics[0].blood_pressure_diastolic:
        bp_sys = metrics[0].blood_pressure_systolic
        bp_dia = metrics[0].blood_pressure_diastolic
        if bp_sys > 140 or bp_dia > 90:
            health_score -= 20
            health_factors.append({'issue': 'High Blood Pressure', 'impact': -20, 'suggestion': 'Reduce salt intake, exercise regularly'})
        elif bp_sys < 90 or bp_dia < 60:
            health_score -= 15
            health_factors.append({'issue': 'Low Blood Pressure', 'impact': -15, 'suggestion': 'Increase fluid intake, consult doctor'})
        else:
            health_factors.append({'issue': 'Normal Blood Pressure', 'impact': 0, 'suggestion': 'Keep monitoring'})
    
    if metrics and metrics[0].heart_rate:
        hr = metrics[0].heart_rate
        if hr > 100:
            health_score -= 10
            health_factors.append({'issue': 'High Heart Rate', 'impact': -10, 'suggestion': 'Rest and consult doctor'})
        elif hr < 60:
            health_score -= 5
            health_factors.append({'issue': 'Low Heart Rate', 'impact': -5, 'suggestion': 'Monitor regularly'})
        else:
            health_factors.append({'issue': 'Normal Heart Rate', 'impact': 0, 'suggestion': 'Good heart health'})
    
    if metrics and metrics[0].blood_sugar:
        bs = metrics[0].blood_sugar
        if bs > 140:
            health_score -= 25
            health_factors.append({'issue': 'High Blood Sugar', 'impact': -25, 'suggestion': 'Consult doctor immediately, monitor diet'})
        elif bs < 70:
            health_score -= 15
            health_factors.append({'issue': 'Low Blood Sugar', 'impact': -15, 'suggestion': 'Consume sugar immediately, consult doctor'})
        else:
            health_factors.append({'issue': 'Normal Blood Sugar', 'impact': 0, 'suggestion': 'Maintain healthy diet'})
    
    health_score = max(0, min(100, health_score))
    
    if health_score >= 90:
        grade = "Excellent"
        color = "success"
        icon = "fa-trophy"
    elif health_score >= 75:
        grade = "Good"
        color = "primary"
        icon = "fa-smile"
    elif health_score >= 60:
        grade = "Fair"
        color = "warning"
        icon = "fa-meh"
    else:
        grade = "Needs Attention"
        color = "danger"
        icon = "fa-frown"
    
    bmi = None
    bmi_category = ""
    if patient.height and patient.weight:
        bmi = patient.weight / ((patient.height/100) ** 2)
        if bmi < 18.5:
            bmi_category = "Underweight"
        elif bmi < 25:
            bmi_category = "Normal"
        elif bmi < 30:
            bmi_category = "Overweight"
        else:
            bmi_category = "Obese"
    
    return render_template('patient/health_dashboard.html',
                         patient=patient,
                         metrics=metrics,
                         metrics_data=metrics_data,
                         health_score=health_score,
                         grade=grade,
                         color=color,
                         icon=icon,
                         health_factors=health_factors,
                         bmi=bmi,
                         bmi_category=bmi_category,
                         now=datetime.now())

@app.route('/patient/add-health-metric', methods=['GET', 'POST'])
@login_required
def add_health_metric():
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    
    if request.method == 'POST':
        metric = HealthMetric(
            patient_id=patient.id,
            blood_pressure_systolic=int(request.form.get('bp_systolic')) if request.form.get('bp_systolic') else None,
            blood_pressure_diastolic=int(request.form.get('bp_diastolic')) if request.form.get('bp_diastolic') else None,
            heart_rate=int(request.form.get('heart_rate')) if request.form.get('heart_rate') else None,
            blood_sugar=float(request.form.get('blood_sugar')) if request.form.get('blood_sugar') else None,
            weight=float(request.form.get('weight')) if request.form.get('weight') else None,
            notes=request.form.get('notes')
        )
        db.session.add(metric)
        
        if request.form.get('weight'):
            patient.weight = float(request.form.get('weight'))
        
        db.session.commit()
        flash('✅ Health metric added successfully!', 'success')
        return redirect(url_for('patient_health_dashboard'))
    
    return render_template('patient/add_health_metric.html', patient=patient, now=datetime.now())

@app.route('/patient/health-metric/delete/<int:metric_id>', methods=['POST'])
@login_required
def delete_health_metric(metric_id):
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    metric = HealthMetric.query.get_or_404(metric_id)
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    
    if metric.patient_id != patient.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('patient_dashboard'))
    
    db.session.delete(metric)
    db.session.commit()
    
    flash('✅ Metric deleted.', 'success')
    return redirect(url_for('patient_health_dashboard'))

@app.route('/patient/lab-tests')
@login_required
def patient_lab_tests():
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    tests = LabTest.query.filter_by(is_active=True).all()
    return render_template('patient/lab_tests.html', tests=tests, now=datetime.now())

@app.route('/patient/book-lab-test/<int:test_id>', methods=['GET', 'POST'])
@login_required
def book_lab_test(test_id):
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    test = LabTest.query.get_or_404(test_id)
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    
    print(f"=== Booking Lab Test ===")
    print(f"Patient: {patient.user.full_name} (ID: {patient.id})")
    print(f"Test: {test.name} (ID: {test.id})")
    
    if request.method == 'POST':
        booking_date = request.form.get('booking_date')
        booking_time = request.form.get('booking_time')
        instructions = request.form.get('instructions', '')
        
        print(f"Booking Date: {booking_date}")
        print(f"Booking Time: {booking_time}")
        
        if not booking_date or not booking_time:
            flash('Please select both date and time.', 'danger')
            return redirect(url_for('book_lab_test', test_id=test_id))
        
        try:
            booking_date_obj = datetime.strptime(booking_date, '%Y-%m-%d').date()
            if booking_date_obj < date.today():
                flash('Cannot book for past dates.', 'danger')
                return redirect(url_for('book_lab_test', test_id=test_id))
        except Exception as e:
            print(f"Date error: {e}")
            flash('Invalid date format.', 'danger')
            return redirect(url_for('book_lab_test', test_id=test_id))
        
        booking_number = generate_booking_number()
        print(f"Generated Booking Number: {booking_number}")
        
        booking = LabBooking(
            booking_number=booking_number,
            patient_id=patient.id,
            test_id=test.id,
            booking_date=booking_date_obj,
            booking_time=booking_time,
            instructions=instructions,
            status='pending',
            payment_status='pending',
            amount=test.price
        )
        
        try:
            db.session.add(booking)
            db.session.commit()
            print(f"✅ Booking saved successfully! ID: {booking.id}")
            flash(f'✅ Lab test "{test.name}" booked successfully! Booking ID: {booking_number}', 'success')
            return redirect(url_for('patient_lab_bookings'))
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error saving booking: {e}")
            flash(f'Error booking test: {str(e)}', 'danger')
            return redirect(url_for('book_lab_test', test_id=test_id))
    
    return render_template('patient/book_lab_test.html', test=test, now=datetime.now())

@app.route('/patient/lab-bookings')
@login_required
def patient_lab_bookings():
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    bookings = LabBooking.query.filter_by(
        patient_id=patient.id
    ).order_by(LabBooking.created_at.desc()).all()
    
    print(f"Patient: {patient.user.full_name}")
    print(f"Number of lab bookings found: {len(bookings)}")
    for b in bookings:
        print(f"  - {b.booking_number}: {b.test.name} on {b.booking_date}")
    
    return render_template('patient/lab_bookings.html', bookings=bookings, now=datetime.now())

@app.route('/patient/cancel-lab-booking/<int:booking_id>', methods=['POST'])
@login_required
def cancel_lab_booking(booking_id):
    if not current_user.is_patient:
        return jsonify({'success': False, 'message': 'Access denied'})
    
    booking = LabBooking.query.get_or_404(booking_id)
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    
    if booking.patient_id != patient.id:
        return jsonify({'success': False, 'message': 'Access denied'})
    
    if booking.status != 'pending':
        return jsonify({'success': False, 'message': 'Only pending bookings can be cancelled'})
    
    booking.status = 'cancelled'
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Booking cancelled successfully'})

@app.route('/patient/upload-report', methods=['GET', 'POST'])
@login_required
def upload_report():
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    
    if request.method == 'POST':
        report_type = request.form.get('report_type')
        description = request.form.get('description')
        appointment_id = request.form.get('appointment_id')
        doctor_id = request.form.get('doctor_id')
        file = request.files.get('report_file')
        
        if not file or file.filename == '':
            flash('No file selected.', 'danger')
            return redirect(request.url)
        
        if not allowed_file(file.filename):
            flash('File type not allowed. Please upload images, PDF, or documents.', 'danger')
            return redirect(request.url)
        
        filename = secure_filename(file.filename)
        unique_filename = f"{uuid.uuid4().hex}_{filename}"
        upload_folder = os.path.join(app.config['UPLOAD_FOLDER'], 'reports')
        os.makedirs(upload_folder, exist_ok=True)
        filepath = os.path.join(upload_folder, unique_filename)
        file.save(filepath)
        
        report_number = generate_report_number()
        report = PatientReport(
            report_number=report_number,
            patient_id=patient.id,
            doctor_id=doctor_id if doctor_id else None,
            appointment_id=appointment_id if appointment_id else None,
            report_type=report_type,
            report_name=filename,
            file_path=filepath,
            description=description
        )
        
        db.session.add(report)
        db.session.commit()
        
        flash(f'✅ Report uploaded successfully! Report #: {report_number}', 'success')
        return redirect(url_for('my_reports'))
    
    appointments = Appointment.query.filter_by(
        patient_id=patient.id
    ).order_by(Appointment.appointment_date.desc()).all()
    
    doctors = Doctor.query.all()
    
    return render_template('patient/upload_report.html', 
                         appointments=appointments,
                         doctors=doctors,
                         now=datetime.now())

@app.route('/patient/my-reports')
@login_required
def my_reports():
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    
    reports = PatientReport.query.filter_by(
        patient_id=patient.id
    ).order_by(PatientReport.upload_date.desc()).all()
    
    return render_template('patient/my_reports.html', reports=reports, now=datetime.now())

@app.route('/patient/report/<int:report_id>/view')
@login_required
def view_report(report_id):
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    report = PatientReport.query.get_or_404(report_id)
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    
    if report.patient_id != patient.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('patient_dashboard'))
    
    if not os.path.exists(report.file_path):
        flash('File not found.', 'danger')
        return redirect(url_for('my_reports'))
    
    return send_file(report.file_path)

@app.route('/patient/report/<int:report_id>/download')
@login_required
def download_report(report_id):
    report = PatientReport.query.get_or_404(report_id)
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    
    if report.patient_id != patient.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('patient_dashboard'))
    
    if not os.path.exists(report.file_path):
        flash('File not found.', 'danger')
        return redirect(url_for('my_reports'))
    
    return send_file(report.file_path, as_attachment=True, download_name=report.report_name)

@app.route('/patient/report/<int:report_id>/delete', methods=['POST'])
@login_required
def delete_report(report_id):
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    report = PatientReport.query.get_or_404(report_id)
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    
    if report.patient_id != patient.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('patient_dashboard'))
    
    if os.path.exists(report.file_path):
        os.remove(report.file_path)
    
    db.session.delete(report)
    db.session.commit()
    
    flash('✅ Report deleted successfully!', 'success')
    return redirect(url_for('my_reports'))

@app.route('/patient/payment-history')
@login_required
def payment_history():
    if not current_user.is_patient:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    payments = PaymentTransaction.query.filter_by(
        patient_id=patient.id,
        payment_status='completed'
    ).order_by(PaymentTransaction.transaction_date.desc()).all()
    
    return render_template('patient/payment_history.html', payments=payments, now=datetime.now())

# ========== API ROUTES ==========

@app.route('/api/available-slots')
def api_available_slots():
    doctor_id = request.args.get('doctor_id', type=int)
    date_str = request.args.get('date')
    
    if not doctor_id or not date_str:
        return jsonify({'success': False, 'message': 'Missing parameters', 'available_slots': []})
    
    try:
        appointment_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except:
        return jsonify({'success': False, 'message': 'Invalid date format', 'available_slots': []})
    
    if appointment_date < date.today():
        return jsonify({'success': False, 'message': 'Cannot book for past dates', 'available_slots': []})
    
    available_slots = get_doctor_available_slots(doctor_id, appointment_date)
    
    doctor = Doctor.query.get(doctor_id)
    
    return jsonify({
        'success': True, 
        'available_slots': available_slots,
        'doctor_name': doctor.user.full_name if doctor else None,
        'consultation_fee': doctor.consultation_fee if doctor else 0,
        'date': date_str
    })

@app.route('/api/doctor-availability')
def api_doctor_availability():
    doctor_id = request.args.get('doctor_id', type=int)
    date_str = request.args.get('date')
    time_str = request.args.get('time')
    
    if not doctor_id or not date_str:
        return jsonify({'success': False, 'message': 'Missing parameters'})
    
    try:
        appointment_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except:
        return jsonify({'success': False, 'message': 'Invalid date format'})
    
    if time_str:
        is_available, message, schedule = check_doctor_full_availability(doctor_id, appointment_date, time_str)
        return jsonify({
            'success': True,
            'is_available': is_available,
            'message': message,
            'date': date_str,
            'time': time_str
        })
    else:
        on_leave, leave_msg = is_doctor_on_leave(doctor_id, appointment_date)
        if on_leave:
            return jsonify({
                'success': True,
                'is_available': False,
                'message': leave_msg,
                'date': date_str
            })
        
        day_available, schedule = is_doctor_available_on_day(doctor_id, appointment_date)
        if not day_available:
            return jsonify({
                'success': True,
                'is_available': False,
                'message': schedule,
                'date': date_str
            })
        
        return jsonify({
            'success': True,
            'is_available': True,
            'message': f'Doctor is available on {appointment_date.strftime("%A")}',
            'schedule': {
                'start': schedule.start_time if schedule else None,
                'end': schedule.end_time if schedule else None
            },
            'date': date_str
        })

@app.route('/api/recommend-department', methods=['POST'])
def api_recommend_department():
    if request.is_json:
        symptoms = request.json.get('symptoms', '')
    else:
        symptoms = request.form.get('symptoms', '')
    
    if symptoms:
        result = recommend_department(symptoms)
        return jsonify(result)
    
    return jsonify({'department': 'General Medicine', 'icon': '🩺', 'priority': 'Routine'})

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

# ========== ERROR HANDLERS ==========

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(403)
def forbidden(e):
    return render_template('403.html'), 403

@app.errorhandler(500)
def internal_error(e):
    return render_template('500.html'), 500

# ========== RUN APPLICATION ==========

if __name__ == '__main__':
    network_ip = get_ip_address()
    
    print("\n" + "="*80)
    print("🏥 HOSPITAL MANAGEMENT SYSTEM - COMPLETE VERSION")
    print("="*80)
    print("💳 QR Code Payment System (Demo Mode)")
    print("💰 Amount in Indian Rupees (₹)")
    print("🕐 Appointment Hours: 9:00 AM to 9:00 PM (30-minute slots)")
    print("👨‍⚕️ Doctor Availability: Based on schedule and leaves")
    print("🔬 Lab Test Booking System: Fully Functional (9 AM - 9 PM)")
    print("📱 SMS Notifications: " + ("Enabled" if SMS_AVAILABLE else "Disabled"))
    print("="*80)
    print("🌐 ACCESS URLS:")
    print(f"   • Local: http://localhost:5000")
    print(f"   • Network: http://{network_ip}:5000")
    print("="*80)
    print("👤 TEST ACCOUNTS:")
    print("   • Admin: admin / admin123")
    print("   • Doctors: dr_smith, dr_williams, dr_brown (password: doctor123)")
    print("   • Patients: john_doe, jane_smith, sankar (password: patient123)")
    print("="*80)
    print("💰 Doctor Fees:")
    print("   • Dr. Smith: ₹500 (Cardiology - Mon, Wed, Fri: 10:00-16:00)")
    print("   • Dr. Williams: ₹400 (Pediatrics - Tue, Thu, Sat: 09:00-15:00)")
    print("   • Dr. Brown: ₹600 (Orthopedics - Mon-Fri: 11:00-18:00)")
    print("="*80)
    print("✅ Debug URLs:")
    print("   • Debug Lab Bookings: http://localhost:5000/debug-lab-bookings")
    print("   • Test Create Booking: http://localhost:5000/test-create-booking")
    print("="*80)
    print("✅ Features Included:")
    print("   • Doctor availability checks (schedule, leaves, working hours)")
    print("   • QR Code payment system")
    print("   • Lab test booking system (9 AM - 9 PM slots)")
    print("   • Patient health dashboard with metrics")
    print("   • Doctor feedback with star ratings")
    print("   • Report upload and management")
    print("   • SMS notifications (if configured)")
    print("   • Admin lab bookings management")
    print("="*80 + "\n")
    
    for folder in ['temp', 'uploads/reports', 'uploads/health_images']:
        os.makedirs(folder, exist_ok=True)
    
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)