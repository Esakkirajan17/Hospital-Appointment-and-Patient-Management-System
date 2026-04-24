"""
SMS Notification Service - Appointment Only
Sends SMS only for appointment-related notifications using Twilio
"""

import os
import logging
from datetime import datetime
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SMSService:
    """SMS service for appointment-only notifications"""
    
    def __init__(self):
        self.provider = 'twilio'
        
        # Get Twilio credentials
        self.account_sid = os.getenv('TWILIO_ACCOUNT_SID')
        self.auth_token = os.getenv('TWILIO_AUTH_TOKEN')
        self.twilio_phone = os.getenv('TWILIO_PHONE_NUMBER')
        
        # Trial mode settings
        self.trial_mode = os.getenv('TWILIO_TRIAL_MODE', 'true').lower() == 'true'
        verified = os.getenv('VERIFIED_PHONE_NUMBERS', '')
        self.verified_numbers = [num.strip() for num in verified.split(',') if num.strip()]
        
        if not all([self.account_sid, self.auth_token, self.twilio_phone]):
            logger.error("❌ Twilio credentials missing!")
            self.client = None
        else:
            self.client = Client(self.account_sid, self.auth_token)
            logger.info(f"✅ Twilio SMS Service initialized")
            logger.info(f"📱 Phone: {self.twilio_phone}")
            if self.trial_mode:
                logger.info(f"📱 Verified numbers: {len(self.verified_numbers)}")
    
    def _format_phone(self, phone):
        """Format phone number for Twilio (E.164 format)"""
        if not phone:
            return None
            
        phone = ''.join(c for c in phone if c.isdigit())
        
        if len(phone) == 10:
            return f"+91{phone}"
        elif len(phone) == 12 and phone.startswith('91'):
            return f"+{phone}"
        elif len(phone) == 13 and phone.startswith('+'):
            return phone
        elif len(phone) > 10:
            return f"+91{phone[-10:]}"
        
        return None
    
    def _is_verified(self, phone):
        """Check if number is verified for trial account"""
        if not self.trial_mode:
            return True
            
        formatted = self._format_phone(phone)
        if not formatted:
            return False
            
        for verified in self.verified_numbers:
            if verified in formatted or formatted in verified:
                return True
            digits = ''.join(c for c in formatted if c.isdigit())[-10:]
            verified_digits = ''.join(c for c in verified if c.isdigit())[-10:]
            if digits == verified_digits:
                return True
        return False
    
    def send_sms(self, to_phone, message):
        """Send SMS only for appointment purposes"""
        if not self.client:
            return False, "Twilio not configured"
        
        if not to_phone:
            return False, "No phone number"
        
        to_phone = self._format_phone(to_phone)
        if not to_phone:
            return False, "Invalid phone number"
        
        if self.trial_mode and not self._is_verified(to_phone):
            return False, f"Number {to_phone} not verified for trial"
        
        try:
            twilio_msg = self.client.messages.create(
                body=message,
                from_=self.twilio_phone,
                to=to_phone
            )
            
            logger.info(f"✅ Appointment SMS sent to {to_phone}")
            return True, "Appointment notification sent"
            
        except TwilioRestException as e:
            logger.error(f"❌ Twilio error: {e}")
            return False, f"Failed to send: {e}"
    
    # ========== APPOINTMENT-ONLY SMS METHODS ==========
    
    def send_appointment_confirmation(self, phone, appointment):
        """Send SMS when patient books an appointment"""
        if not appointment:
            return False, "No appointment data"
        
        doctor_name = appointment.doctor.user.full_name if appointment.doctor else "Doctor"
        date_str = appointment.appointment_date.strftime('%d-%m-%Y')
        
        message = f"""🏥 HMS: Appointment Confirmed!
📋 {appointment.appointment_number}
👨‍⚕️ Dr. {doctor_name}
📅 {date_str}
⏰ {appointment.appointment_time}
📍 {appointment.consultation_type.upper()}

Please arrive 15 mins early."""
        
        return self.send_sms(phone, message)
    
    def send_appointment_reminder(self, phone, appointment):
        """Send SMS 24 hours before appointment"""
        if not appointment:
            return False, "No appointment data"
        
        doctor_name = appointment.doctor.user.full_name if appointment.doctor else "Doctor"
        date_str = appointment.appointment_date.strftime('%d-%m-%Y')
        
        message = f"""⏰ REMINDER: Appointment Tomorrow!
📋 {appointment.appointment_number}
👨‍⚕️ Dr. {doctor_name}
📅 {date_str}
⏰ {appointment.appointment_time}

Please carry ID proof."""
        
        return self.send_sms(phone, message)
    
    def send_cancellation_notice(self, phone, appointment):
        """Send SMS when appointment is cancelled"""
        if not appointment:
            return False, "No appointment data"
        
        doctor_name = appointment.doctor.user.full_name if appointment.doctor else "Doctor"
        date_str = appointment.appointment_date.strftime('%d-%m-%Y')
        
        message = f"""❌ Appointment Cancelled
📋 {appointment.appointment_number}
👨‍⚕️ Dr. {doctor_name}
📅 {date_str}
⏰ {appointment.appointment_time}

Please book a new appointment."""
        
        return self.send_sms(phone, message)

# Global SMS service instance
sms_service = SMSService()
