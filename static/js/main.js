function checkRegistrationModal(registrationRequired) {
    if (registrationRequired) {
        const modal = document.getElementById("registerModal");
        modal.style.display = "block";

        // Optionally, pause video stream or show message
        const statusText = document.querySelector(".status");
        if (statusText) {
            statusText.innerText = "New user detected! Please register.";
        }
    }
}

// Optional: close modal after form submit
const registerForm = document.querySelector("#registerModal form");
if (registerForm) {
    registerForm.addEventListener("submit", () => {
        const modal = document.getElementById("registerModal");
        modal.style.display = "none";
    });
}

// Poll for registration status every 2 seconds
setInterval(() => {
    fetch('/check_registration')
        .then(response => response.json())
        .then(data => {
            if (data.pending) {
                checkRegistrationModal(true);
            }
        })
        .catch(error => console.error('Error checking registration:', error));
}, 2000);
