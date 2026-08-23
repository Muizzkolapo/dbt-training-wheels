// ============================================
// DBT Training Wheels Interactive Tour
// ============================================

// Tour state
const TOUR_STORAGE_KEY = 'dbt_training_wheels_tour_completed';
let isTourActive = false;
let currentTourStep = 0;

// Tour step definitions
const TOUR_STEPS = [
    {
        id: 'welcome',
        title: 'Welcome to DBT Training Wheels',
        description: 'DBT Training Wheels helps you convert BigQuery scheduled queries into production-ready dbt models. This quick tour will show you the essentials.',
        tip: 'Use arrow keys to navigate, or press Escape to skip.',
        target: null,
        position: 'center',
        highlight: false
    },
    {
        id: 'sidebar',
        title: 'Scheduled Queries',
        description: 'Your BigQuery scheduled queries appear here. You can also upload SQL files directly using the upload area above.',
        tip: 'Click any query to start the conversion process.',
        target: '#scheduled-queries-section',
        position: 'center',
        highlight: true
    },
    {
        id: 'file-upload',
        title: 'File Upload',
        description: 'Bring in some SQL to continue the tour. Pick a folder, choose a single file, or paste a query — or drag and drop a .sql file onto the highlighted area.',
        tip: 'You can also drag and drop a file onto the upload box in the sidebar.',
        target: '#upload-area',
        position: 'top',
        highlight: true
    },
    {
        id: 'uploaded-file',
        title: 'Your Uploaded File',
        description: 'Your SQL file is now loaded and ready to convert. Click next to see the conversion workflow.',
        tip: 'Each file shows its name and a summary of what it contains.',
        target: '#query-tree',
        position: 'right',
        highlight: true
    },
    {
        id: 'header-controls',
        title: 'Control Buttons',
        description: 'Quick access to key features: view original SQL, toggle prerequisite checklists, replay this tour, and export your files.',
        tip: 'The Export button appears after completing all steps.',
        target: '#header-controls',
        position: 'bottom',
        highlight: true
    },
    {
        id: 'conversion-steps',
        title: 'Conversion Workflow',
        description: 'Your conversion journey has 10 steps. Each step transforms your SQL into proper dbt models. Steps turn green as you complete them.',
        tip: 'Click any step to jump directly to it.',
        target: '#conversion-steps-overview',
        position: 'right',
        highlight: true
    }
];

// ============================================
// Tour State Management
// ============================================

function hasSeenTour() {
    return localStorage.getItem(TOUR_STORAGE_KEY) === 'true';
}

function markTourComplete() {
    localStorage.setItem(TOUR_STORAGE_KEY, 'true');
}

// ============================================
// Tour Core Functions
// ============================================

function startTour(stepNumber = 0) {
    // Close any open modals first
    closeAllModals();

    isTourActive = true;
    currentTourStep = stepNumber;
    createTourOverlay();
    showTourStep(stepNumber);
    document.body.style.overflow = 'hidden';

    // Add keyboard listeners
    document.addEventListener('keydown', handleTourKeyboard);
}

// Close any open modals before starting tour
function closeAllModals() {
    // Close prerequisite modal if open
    const prereqModal = document.getElementById('prereq-modal');
    if (prereqModal) {
        prereqModal.remove();
    }

    // Close any backdrop
    const backdrop = document.querySelector('.modal-backdrop');
    if (backdrop) {
        backdrop.remove();
    }

    // Remove any modal-open class from body
    document.body.classList.remove('modal-open');
}

function endTour() {
    isTourActive = false;
    markTourComplete();
    removeTourOverlay();
    document.body.style.overflow = 'auto';

    // Remove keyboard listeners
    document.removeEventListener('keydown', handleTourKeyboard);
}

function skipTour() {
    endTour();
}

function nextTourStep() {
    if (currentTourStep < TOUR_STEPS.length - 1) {
        currentTourStep++;
        showTourStep(currentTourStep);
    } else {
        endTour();
    }
}

function previousTourStep() {
    if (currentTourStep > 0) {
        currentTourStep--;
        showTourStep(currentTourStep);
    }
}

function handleTourKeyboard(e) {
    if (!isTourActive) return;

    switch (e.key) {
        case 'Escape':
            skipTour();
            break;
        case 'ArrowRight':
        case 'Enter':
            if (TOUR_STEPS[currentTourStep].id !== 'file-upload') {
                nextTourStep();
            }
            break;

        case 'Enter':
            nextTourStep();
            break;
        case 'ArrowLeft':
            previousTourStep();
            break;
    }
}

// ============================================
// Tour UI Creation
// ============================================

function createTourOverlay() {
    // Remove existing overlay if present
    removeTourOverlay();

    // Create backdrop as direct child of body
    const backdrop = document.createElement('div');
    backdrop.id = 'tour-backdrop';
    backdrop.className = 'tour-backdrop';
    backdrop.onclick = skipTour;
    document.body.appendChild(backdrop);

    // Create spotlight as direct child of body
    const spotlight = document.createElement('div');
    spotlight.id = 'tour-spotlight';
    spotlight.className = 'tour-spotlight';
    spotlight.style.display = 'none';
    document.body.appendChild(spotlight);

    // Create popover as direct child of body (not nested!)
    const popover = document.createElement('div');
    popover.id = 'tour-popover';
    popover.className = 'tour-popover';
    document.body.appendChild(popover);
}

function removeTourOverlay() {
    // Remove backdrop
    const backdrop = document.getElementById('tour-backdrop');
    if (backdrop) {
        backdrop.remove();
    }

    // Remove spotlight
    const spotlight = document.getElementById('tour-spotlight');
    if (spotlight) {
        spotlight.remove();
    }

    // Remove popover
    const popover = document.getElementById('tour-popover');
    if (popover) {
        popover.remove();
    }

    // Remove any highlight classes
    document.querySelectorAll('.tour-highlight').forEach(el => {
        el.classList.remove('tour-highlight');
    });
}

function showTourStep(stepIndex) {
    const step = TOUR_STEPS[stepIndex];
    const popover = document.getElementById('tour-popover');
    const spotlight = document.getElementById('tour-spotlight');

    if (!popover || !spotlight) return;

    // Remove previous highlights
    document.querySelectorAll('.tour-highlight').forEach(el => {
        el.classList.remove('tour-highlight');
    });

    // Generate and set popover content
    popover.innerHTML = generatePopoverContent(step, stepIndex);

    // Handle spotlight for targeted steps
    if (step.target) {
        const targetEl = document.querySelector(step.target);

        if (targetEl && isElementVisible(targetEl)) {
            if (step.highlight) {
                targetEl.classList.add('tour-highlight');
            }
            positionSpotlight(targetEl);
            positionPopover(targetEl);
            spotlight.style.display = 'block';
        } else {
            spotlight.style.display = 'none';
        }
    } else {
        spotlight.style.display = 'none';
    }
}

// Check if an element is visible in the viewport
function isElementVisible(el) {
    if (!el) return false;

    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);

    // Check if element has dimensions and is not hidden
    return (
        rect.width > 0 &&
        rect.height > 0 &&
        style.display !== 'none' &&
        style.visibility !== 'hidden' &&
        style.opacity !== '0'
    );
}

function generatePopoverContent(step, stepIndex) {
    const isFirst = stepIndex === 0;
    const isLast = stepIndex === TOUR_STEPS.length - 1;
    const isFileUpload = step.id === 'file-upload';
    const isWelcome = step.id === 'welcome' || step.id === 'complete';

    let welcomeIcon = '';
    if (isWelcome) {
        welcomeIcon = `
            <div class="tour-welcome-icon">
                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                          d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z">
                    </path>
                </svg>
            </div>
        `;
    }

    return `
        <div style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px;background:#1f2937;color:white;border-radius:12px 12px 0 0;">
            <span style="font-size:13px;font-weight:600;">Step ${stepIndex + 1} of ${TOUR_STEPS.length}</span>
            <button onclick="skipTour()" style="background:rgba(255,255,255,0.2);border:none;color:white;width:24px;height:24px;border-radius:4px;cursor:pointer;font-size:18px;">&times;</button>
        </div>
        <div style="padding:20px;background:white;">
            ${welcomeIcon}
            <h3 style="font-size:18px;font-weight:700;color:#000;margin:0 0 8px 0;">${step.title}</h3>
            <p style="font-size:14px;color:#555;line-height:1.6;margin:0;">${step.description}</p>
            ${step.tip ? `
                <div style="display:flex;align-items:flex-start;gap:8px;margin-top:16px;padding:12px;background:#f5f5f5;border-radius:8px;font-size:13px;color:#666;">
                    <svg style="width:16px;height:16px;color:#1f2937;flex-shrink:0;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                    </svg>
                    <span>${step.tip}</span>
                </div>
            ` : ''}
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;padding:12px 20px;background:#fafafa;border-top:1px solid #eee;">
            <button onclick="skipTour()" style="background:none;border:none;color:#888;font-size:13px;cursor:pointer;padding:8px 12px;">Skip Tour</button>
            <div style="display:flex;gap:8px;">
                <button onclick="previousTourStep()" ${isFirst ? 'disabled' : ''} style="display:flex;align-items:center;gap:6px;padding:8px 16px;border-radius:6px;font-size:14px;font-weight:600;cursor:pointer;background:white;border:1px solid #ddd;color:#333;">
                    <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"></path></svg>
                    Back
                </button>
                <button onclick="${isLast ? 'endTour()' : isFileUpload ? '' : 'nextTourStep()'}" style="display:flex;align-items:center;gap:6px;padding:8px 16px;border-radius:6px;font-size:14px;font-weight:600;cursor:pointer;background:#1f2937;border:none;color:white;">
                    ${isLast ? 'Finish' : isFileUpload ? 'Upload to continue' : 'Next'}
                    <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"></path></svg>
                </button>
            </div>
        </div>
        <div style="display:flex;justify-content:center;gap:6px;padding:12px 20px;background:white;border-top:1px solid #eee;">
            ${TOUR_STEPS.map((_, i) => `
                <div style="width:8px;height:8px;border-radius:50%;background:${i === stepIndex ? '#1f2937' : (i < stepIndex ? 'rgba(19,78,74,0.4)' : '#ddd')};${i === stepIndex ? 'transform:scale(1.2);' : ''}"></div>
            `).join('')}
        </div>
        <div style="display:flex;justify-content:center;gap:16px;padding:10px 20px;background:#f8f8f8;border-radius:0 0 12px 12px;font-size:12px;color:#888;">
            <span style="display:flex;align-items:center;gap:4px;"><kbd style="padding:2px 6px;background:white;border:1px solid #ddd;border-radius:4px;font-size:11px;">←</kbd> <kbd style="padding:2px 6px;background:white;border:1px solid #ddd;border-radius:4px;font-size:11px;">→</kbd> Navigate</span>
            <span style="display:flex;align-items:center;gap:4px;"><kbd style="padding:2px 6px;background:white;border:1px solid #ddd;border-radius:4px;font-size:11px;">Esc</kbd> Skip</span>
        </div>
    `;
}

// ============================================
// Positioning Functions
// ============================================

function positionSpotlight(targetEl) {
    const spotlight = document.getElementById('tour-spotlight');
    if (!spotlight || !targetEl) return;

    const rect = targetEl.getBoundingClientRect();
    const padding = 8;

    // Use fixed positioning (viewport coordinates)
    spotlight.style.top = `${rect.top - padding}px`;
    spotlight.style.left = `${rect.left - padding}px`;
    spotlight.style.width = `${rect.width + padding * 2}px`;
    spotlight.style.height = `${rect.height + padding * 2}px`;
}

function positionPopover(targetEl) {
    const popover = document.getElementById('tour-popover');
    if (!popover || !targetEl) return;
    
    const rect = targetEl.getBoundingClientRect();
    const inTopHalf = (rect.top + rect.height / 2) < window.innerHeight / 2;

    
    popover.style.top = inTopHalf ? 'auto' : '20px';
    popover.style.bottom = inTopHalf ? '150px' : 'auto';

    console.log(inTopHalf, rect.top)
}


// ============================================
// Auto-start for first-time users
// ============================================

function initTourAutoStart() {
    // Check if user has seen the tour
    if (!hasSeenTour()) {
        // Wait a bit for the page to fully load
        setTimeout(() => {
            // Only auto-start if on the main page (not in a specific step)
            if (document.getElementById('empty-state') || document.getElementById('conversion-steps-overview')) {
                // Resume from saved step if page was reloaded during tour
                const savedStep = localStorage.getItem('tour_step');
                localStorage.removeItem('tour_step');
                startTour(savedStep ? parseInt(savedStep) : 0);
            }
        }, 1000);
    }
}

// Initialize auto-start when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initTourAutoStart);
} else {
    // DOM already loaded
    setTimeout(initTourAutoStart, 500);
}
