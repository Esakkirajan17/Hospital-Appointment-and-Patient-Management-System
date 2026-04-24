// Simple Notification System - Minimal Version

// Auto-dismiss alerts after 5 seconds
setTimeout(function() {
    document.querySelectorAll('.alert').forEach(function(alert) {
        alert.remove();
    });
}, 5000);

// Check for notifications (can be expanded later)
function checkNotifications() {
    // This function can be expanded later
    console.log('Notifications checked');
}

// Export for use in other files
window.notifications = {
    check: checkNotifications
};