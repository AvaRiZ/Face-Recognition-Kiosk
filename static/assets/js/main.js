window.addEventListener('load', function () {
    "use strict";

    const select = (selector, all = false) => {
        const trimmed = selector.trim();
        return all ? [...document.querySelectorAll(trimmed)] : document.querySelector(trimmed);
    };

    const on = (type, selector, listener, all = false) => {
        const targets = select(selector, all);
        if (all) {
            targets.forEach((target) => target.addEventListener(type, listener));
            return;
        }
        if (targets) {
            targets.addEventListener(type, listener);
        }
    };

    const applyTheme = (theme) => {
        const isDark = theme === 'dark';
        document.body.classList.toggle('dark', isDark);
        document.documentElement.setAttribute('data-bs-theme', theme);
        document.body.setAttribute('data-bs-theme', theme);

        const toggleBtn = select('#profileThemeToggle');
        if (!toggleBtn) {
            return;
        }

        const icon = toggleBtn.querySelector('i');
        const label = toggleBtn.querySelector('span');

        if (icon) {
            icon.className = isDark ? 'bi bi-sun me-2' : 'bi bi-moon me-2';
        }

        if (label) {
            label.textContent = isDark ? 'Light Mode' : 'Dark Mode';
        }
    };

    const savedTheme = localStorage.getItem('theme') || 'light';
    applyTheme(savedTheme);

    const themeToggleBtn = select('#profileThemeToggle');
    if (themeToggleBtn) {
        themeToggleBtn.addEventListener('click', function () {
            const isDark = document.body.classList.contains('dark');
            const nextTheme = isDark ? 'light' : 'dark';
            applyTheme(nextTheme);
            localStorage.setItem('theme', nextTheme);
        });
    }

    if (select('.search-bar-toggle')) {
        on('click', '.search-bar-toggle', function () {
            const searchBar = select('.search-bar');
            if (searchBar) {
                searchBar.classList.toggle('search-bar-show');
            }
        });
    }

    if (select('.toggle-sidebar-btn')) {
        on('click', '.toggle-sidebar-btn', function (event) {
            event.preventDefault();
            document.body.classList.toggle('toggle-sidebar');
        });
    }

    const header = select('#header');
    if (header) {
        const updateHeaderState = () => {
            header.classList.toggle('header-scrolled', window.scrollY > 100);
        };
        updateHeaderState();
        document.addEventListener('scroll', updateHeaderState);
    }

    const backToTop = select('.back-to-top');
    if (backToTop) {
        const updateBackToTopState = () => {
            backToTop.classList.toggle('active', window.scrollY > 100);
        };
        updateBackToTopState();
        document.addEventListener('scroll', updateBackToTopState);
    }

    if (typeof bootstrap !== 'undefined') {
        const tooltipTriggers = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
        tooltipTriggers.forEach((tooltipTrigger) => {
            new bootstrap.Tooltip(tooltipTrigger);
        });
    }

    const forms = document.querySelectorAll('.needs-validation');
    Array.prototype.slice.call(forms).forEach(function (form) {
        form.addEventListener('submit', function (event) {
            if (!form.checkValidity()) {
                event.preventDefault();
                event.stopPropagation();
            }

            form.classList.add('was-validated');
        }, false);
    });
});
