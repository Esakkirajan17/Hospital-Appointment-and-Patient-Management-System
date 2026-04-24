// Main JavaScript file for Hospital Management System

// ===== TIME FORMATTING FUNCTIONS =====

/**
 * Convert 24-hour time to 12-hour format with AM/PM
 * @param {string} time24 - Time in 24-hour format (e.g., "14:30")
 * @returns {string} - Time in 12-hour format with AM/PM (e.g., "02:30 PM")
 */
function convertTo12Hour(time24) {
    if (!time24) return '';
    
    const [hours, minutes] = time24.split(':');
    const hour = parseInt(hours);
    const ampm = hour >= 12 ? 'PM' : 'AM';
    const hour12 = hour % 12 || 12;
    
    return `${hour12.toString().padStart(2, '0')}:${minutes} ${ampm}`;
}

/**
 * Convert 12-hour time with AM/PM to 24-hour format
 * @param {string} time12 - Time in 12-hour format (e.g., "02:30 PM")
 * @returns {string} - Time in 24-hour format (e.g., "14:30")
 */
function convertTo24Hour(time12) {
    if (!time12) return '';
    
    const [time, modifier] = time12.split(' ');
    let [hours, minutes] = time.split(':');
    
    if (modifier === 'PM' && hours !== '12') {
        hours = parseInt(hours) + 12;
    }
    if (modifier === 'AM' && hours === '12') {
        hours = '00';
    }
    
    return `${hours.toString().padStart(2, '0')}:${minutes}`;
}

/**
 * Format all time elements on the page
 */
function formatAllTimes() {
    document.querySelectorAll('.time-12h').forEach(element => {
        const time24 = element.getAttribute('data-time');
        if (time24) {
            element.textContent = convertTo12Hour(time24);
        }
    });
}

/**
 * Check if a time is within working hours (9 AM to 9 PM)
 * @param {string} timeStr - Time in 24-hour format (e.g., "14:30")
 * @returns {boolean} - True if within working hours
 */
function isWithinWorkingHours(timeStr) {
    if (!timeStr) return false;
    
    const [hours, minutes] = timeStr.split(':').map(Number);
    const timeInMinutes = hours * 60 + minutes;
    const startTime = 9 * 60; // 9:00 AM
    const endTime = 21 * 60;   // 9:00 PM
    
    return timeInMinutes >= startTime && timeInMinutes <= endTime;
}

/**
 * Validate selected time slot
 * @param {string} timeStr - Selected time
 * @returns {Object} - Validation result
 */
function validateTimeSlot(timeStr) {
    if (!timeStr) {
        return { valid: false, message: 'Please select a time' };
    }
    
    if (!isWithinWorkingHours(timeStr)) {
        return { valid: false, message: 'Please select a time between 9:00 AM and 9:00 PM' };
    }
    
    return { valid: true, message: 'Valid time slot' };
}

// ===== DATE FORMATTING =====

/**
 * Format date to IST (dd-mm-yyyy)
 * @param {Date} date - Date object
 * @returns {string} - Formatted date
 */
function formatToIST(date) {
    const day = String(date.getDate()).padStart(2, '0');
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const year = date.getFullYear();
    return `${day}-${month}-${year}`;
}

/**
 * Get current IST date and time
 * @returns {Object} - Object containing date and time
 */
function getCurrentIST() {
    const now = new Date();
    const istOffset = 5.5 * 60 * 60 * 1000; // 5.5 hours in milliseconds
    const istTime = new Date(now.getTime() + (istOffset - now.getTimezoneOffset() * 60 * 1000));
    
    return {
        date: formatToIST(istTime),
        time: convertTo12Hour(istTime.getHours() + ':' + istTime.getMinutes()),
        datetime: istTime
    };
}

// ===== DARK MODE =====

/**
 * Initialize dark mode from localStorage
 */
function initDarkMode() {
    const savedTheme = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-bs-theme', savedTheme);
    updateDarkModeIcon(savedTheme);
}

/**
 * Toggle dark mode
 */
function toggleDarkMode() {
    const currentTheme = document.documentElement.getAttribute('data-bs-theme');
    const newTheme = currentTheme === 'light' ? 'dark' : 'light';
    
    document.documentElement.setAttribute('data-bs-theme', newTheme);
    localStorage.setItem('theme', newTheme);
    updateDarkModeIcon(newTheme);
}

/**
 * Update dark mode icon
 * @param {string} theme - Current theme ('light' or 'dark')
 */
function updateDarkModeIcon(theme) {
    const icon = document.getElementById('darkModeIcon');
    if (icon) {
        if (theme === 'dark') {
            icon.classList.remove('fa-moon');
            icon.classList.add('fa-sun');
        } else {
            icon.classList.remove('fa-sun');
            icon.classList.add('fa-moon');
        }
    }
}

// ===== FORM VALIDATION =====

/**
 * Validate email format
 * @param {string} email - Email to validate
 * @returns {boolean} - True if valid
 */
function validateEmail(email) {
    const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return re.test(email);
}

/**
 * Validate phone number (10 digits)
 * @param {string} phone - Phone number to validate
 * @returns {boolean} - True if valid
 */
function validatePhone(phone) {
    const re = /^[0-9]{10}$/;
    return re.test(phone);
}

/**
 * Validate password strength
 * @param {string} password - Password to validate
 * @returns {Object} - Validation result
 */
function validatePassword(password) {
    const result = { valid: true, message: 'Password is strong' };
    
    if (password.length < 8) {
        result.valid = false;
        result.message = 'Password must be at least 8 characters';
    } else if (!/[A-Z]/.test(password)) {
        result.valid = false;
        result.message = 'Password must contain at least one uppercase letter';
    } else if (!/[0-9]/.test(password)) {
        result.valid = false;
        result.message = 'Password must contain at least one number';
    } else if (!/[!@#$%^&*]/.test(password)) {
        result.valid = false;
        result.message = 'Password must contain at least one special character';
    }
    
    return result;
}

// ===== TOAST NOTIFICATIONS =====

/**
 * Show toast notification
 * @param {string} message - Message to display
 * @param {string} type - Type of notification (success, error, warning, info)
 */
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast align-items-center text-white bg-${type} border-0 position-fixed bottom-0 end-0 m-3`;
    toast.setAttribute('role', 'alert');
    toast.setAttribute('aria-live', 'assertive');
    toast.setAttribute('aria-atomic', 'true');
    
    toast.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">
                ${message}
            </div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
        </div>
    `;
    
    document.body.appendChild(toast);
    const bsToast = new bootstrap.Toast(toast);
    bsToast.show();
    
    setTimeout(() => {
        toast.remove();
    }, 5000);
}

// ===== APPOINTMENT BOOKING HELPER =====

/**
 * Populate time slots based on selected date
 * @param {string} date - Selected date
 * @param {number} doctorId - Selected doctor ID
 */
async function loadAvailableTimeSlots(date, doctorId) {
    try {
        const response = await fetch(`/api/available-slots?date=${date}&doctor_id=${doctorId}`);
        const data = await response.json();
        
        const timeSelect = document.getElementById('timeSelect');
        if (!timeSelect) return;
        
        // Clear existing options except the first one
        while (timeSelect.options.length > 1) {
            timeSelect.remove(1);
        }
        
        if (data.available_slots && data.available_slots.length > 0) {
            data.available_slots.forEach(slot => {
                const option = document.createElement('option');
                option.value = slot;
                option.textContent = convertTo12Hour(slot);
                timeSelect.appendChild(option);
            });
        } else {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = 'No slots available';
            option.disabled = true;
            timeSelect.appendChild(option);
        }
    } catch (error) {
        console.error('Error loading time slots:', error);
    }
}

// ===== HEALTH METRICS =====

/**
 * Calculate BMI from weight and height
 * @param {number} weight - Weight in kg
 * @param {number} height - Height in cm
 * @returns {number} - BMI value
 */
function calculateBMI(weight, height) {
    if (!weight || !height) return null;
    const heightM = height / 100;
    return (weight / (heightM * heightM)).toFixed(1);
}

/**
 * Get BMI category
 * @param {number} bmi - BMI value
 * @returns {string} - BMI category
 */
function getBMICategory(bmi) {
    if (!bmi) return 'Unknown';
    if (bmi < 18.5) return 'Underweight';
    if (bmi < 25) return 'Normal';
    if (bmi < 30) return 'Overweight';
    return 'Obese';
}

/**
 * Get blood pressure category
 * @param {number} systolic - Systolic pressure
 * @param {number} diastolic - Diastolic pressure
 * @returns {string} - BP category
 */
function getBPCategory(systolic, diastolic) {
    if (!systolic || !diastolic) return 'Unknown';
    if (systolic < 120 && diastolic < 80) return 'Normal';
    if (systolic < 130 && diastolic < 80) return 'Elevated';
    if (systolic < 140 || diastolic < 90) return 'High BP Stage 1';
    return 'High BP Stage 2';
}

// ===== QR CODE =====

/**
 * Generate QR code for appointment
 * @param {number} appointmentId - Appointment ID
 */
function generateQRCode(appointmentId) {
    window.location.href = `/patient/qr-display/${appointmentId}`;
}

// ===== INITIALIZATION =====
document.addEventListener('DOMContentLoaded', function() {
    // Initialize dark mode
    initDarkMode();
    
    // Format all times on the page
    formatAllTimes();
    
    // Auto-dismiss alerts
    setTimeout(function() {
        document.querySelectorAll('.alert').forEach(function(alert) {
            alert.remove();
        });
    }, 5000);
    
    console.log('✅ Main.js loaded - Time: ' + getCurrentIST().time);
    
    // Initialize time slot loader if on booking page
    const dateInput = document.getElementById('appointmentDate');
    const doctorCards = document.querySelectorAll('.doctor-card');
    
    if (dateInput && doctorCards.length > 0) {
        let selectedDoctor = null;
        
        doctorCards.forEach(card => {
            card.addEventListener('click', function() {
                selectedDoctor = this.dataset.doctorId;
                if (dateInput.value) {
                    loadAvailableTimeSlots(dateInput.value, selectedDoctor);
                }
            });
        });
        
        dateInput.addEventListener('change', function() {
            if (selectedDoctor && this.value) {
                loadAvailableTimeSlots(this.value, selectedDoctor);
            }
        });
    }
    
    // Form validation for registration
    const registerForm = document.querySelector('form[action="{{ url_for("register") }}"]');
    if (registerForm) {
        registerForm.addEventListener('submit', function(e) {
            const phone = document.querySelector('input[name="phone"]').value;
            const password = document.querySelector('input[name="password"]').value;
            
            if (phone && !validatePhone(phone)) {
                e.preventDefault();
                alert('Please enter a valid 10-digit phone number');
                return false;
            }
            
            const passwordValidation = validatePassword(password);
            if (!passwordValidation.valid) {
                e.preventDefault();
                alert(passwordValidation.message);
                return false;
            }
        });
    }
});