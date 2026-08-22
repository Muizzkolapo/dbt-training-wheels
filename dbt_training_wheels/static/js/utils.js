// ============================================
// GLOBAL STATE MANAGEMENT
// ============================================
// NOTE: Global state variables are now defined in state.js
// They are proxied to window.* for backward compatibility.
// Use appState.get('key') / appState.set('key', value) for new code.

// ============================================
// STEP REGISTRY - DYNAMIC STEP MANAGEMENT
// ============================================
// This registry manages enabled/disabled steps and provides
// consistent numbering for navigation breadcrumbs.

const StepRegistry = {
    // Populated from conversionSteps after page load
    _allSteps: [],
    _enabledSteps: [],
    _idToDisplayNum: {},
    _displayNumToId: {},

    /**
     * Initialize the registry from conversionSteps array
     * Call this after conversionSteps is available (from backend)
     */
    init(steps) {
        this._allSteps = steps || [];
        this._enabledSteps = this._allSteps.filter(s => s.enabled !== false);

        // Build lookup maps
        this._idToDisplayNum = {};
        this._displayNumToId = {};
        this._enabledSteps.forEach((step, idx) => {
            const displayNum = idx + 1;
            this._idToDisplayNum[step.id] = displayNum;
            this._displayNumToId[displayNum] = step.id;
        });

        console.log(`StepRegistry initialized: ${this._enabledSteps.length} enabled steps`);
    },

    /**
     * Get all enabled steps
     */
    getEnabledSteps() {
        return this._enabledSteps;
    },

    /**
     * Get total count of enabled steps
     */
    getTotalSteps() {
        return this._enabledSteps.length;
    },

    /**
     * Get step config by internal ID
     */
    getStepById(stepId) {
        return this._allSteps.find(s => s.id === stepId);
    },

    /**
     * Get step config by display number (1-based)
     */
    getStepByDisplayNum(displayNum) {
        const stepId = this._displayNumToId[displayNum];
        return stepId ? this.getStepById(stepId) : null;
    },

    /**
     * Convert internal step ID to display number
     * Returns null if step is disabled
     */
    idToDisplayNum(stepId) {
        return this._idToDisplayNum[stepId] || null;
    },

    /**
     * Convert display number to internal step ID
     */
    displayNumToId(displayNum) {
        return this._displayNumToId[displayNum] || null;
    },

    /**
     * Check if a step is enabled
     */
    isEnabled(stepId) {
        return this._idToDisplayNum[stepId] !== undefined;
    },

    /**
     * Get the next enabled step ID after the given step
     * Returns null if at the end
     */
    getNextStepId(currentStepId) {
        const currentDisplayNum = this._idToDisplayNum[currentStepId];
        if (!currentDisplayNum) return null;

        const nextDisplayNum = currentDisplayNum + 1;
        return this._displayNumToId[nextDisplayNum] || null;
    },

    /**
     * Get the previous enabled step ID before the given step
     * Returns null if at the beginning
     */
    getPrevStepId(currentStepId) {
        const currentDisplayNum = this._idToDisplayNum[currentStepId];
        if (!currentDisplayNum || currentDisplayNum <= 1) return null;

        const prevDisplayNum = currentDisplayNum - 1;
        return this._displayNumToId[prevDisplayNum] || null;
    },

    /**
     * Get the first enabled step ID
     */
    getFirstStepId() {
        return this._enabledSteps.length > 0 ? this._enabledSteps[0].id : null;
    },

    /**
     * Get the last enabled step ID
     */
    getLastStepId() {
        return this._enabledSteps.length > 0
            ? this._enabledSteps[this._enabledSteps.length - 1].id
            : null;
    },

    /**
     * Get render function name for a step
     */
    getRenderFn(stepId) {
        const step = this.getStepById(stepId);
        return step ? step.renderFn : null;
    },

    /**
     * Validate if navigation to a target step is allowed
     * Checks prerequisites and step accessibility
     * @param {string} fromStepId - Current step ID
     * @param {string} toStepId - Target step ID
     * @returns {Object} { valid: boolean, reason?: string }
     */
    validateStepNavigation(fromStepId, toStepId) {
        // Check if target step exists
        const targetStep = this.getStepById(toStepId);
        if (!targetStep) {
            return { valid: false, reason: `Step "${toStepId}" does not exist` };
        }

        // Check if step is enabled
        if (!this._enabledSteps.find(s => s.id === toStepId)) {
            return { valid: false, reason: `Step "${toStepId}" is not enabled` };
        }

        // Check if navigation direction is valid (can't skip forward)
        const fromDisplayNum = this._idToDisplayNum[fromStepId];
        const toDisplayNum = this._idToDisplayNum[toStepId];

        if (fromDisplayNum && toDisplayNum) {
            // Allow backwards navigation freely
            if (toDisplayNum < fromDisplayNum) {
                return { valid: true };
            }

            // For forward navigation, only allow next step (no skipping)
            if (toDisplayNum > fromDisplayNum + 1) {
                return { valid: false, reason: 'Cannot skip steps forward' };
            }
        }

        return { valid: true };
    },

    /**
     * Get next valid step ID with validation
     * Returns null with reason if navigation not possible
     * @param {string} currentStepId - Current step ID
     * @returns {Object} { stepId: string|null, valid: boolean, reason?: string }
     */
    getNextValidStepId(currentStepId) {
        const nextId = this.getNextStepId(currentStepId);

        if (!nextId) {
            return { stepId: null, valid: false, reason: 'Already at last step' };
        }

        const validation = this.validateStepNavigation(currentStepId, nextId);
        return {
            stepId: validation.valid ? nextId : null,
            valid: validation.valid,
            reason: validation.reason
        };
    },

    /**
     * Get previous valid step ID with validation
     * @param {string} currentStepId - Current step ID
     * @returns {Object} { stepId: string|null, valid: boolean, reason?: string }
     */
    getPrevValidStepId(currentStepId) {
        const prevId = this.getPrevStepId(currentStepId);

        if (!prevId) {
            return { stepId: null, valid: false, reason: 'Already at first step' };
        }

        const validation = this.validateStepNavigation(currentStepId, prevId);
        return {
            stepId: validation.valid ? prevId : null,
            valid: validation.valid,
            reason: validation.reason
        };
    }
};

// ============================================
// UTILITY FUNCTIONS
// ============================================

/**
 * Focus trap utility for modal accessibility
 * Traps keyboard focus within an element (required for WCAG compliance)
 */
const FocusTrap = {
    _trapped: null,
    _previousFocus: null,
    _handler: null,

    /**
     * Trap focus within an element
     * @param {Element} element - Element to trap focus within
     */
    trap(element) {
        this._previousFocus = document.activeElement;
        this._trapped = element;

        const focusable = element.querySelectorAll(
            'button, input, select, textarea, [tabindex]:not([tabindex="-1"]), a[href]'
        );

        if (focusable.length === 0) return;

        const first = focusable[0];
        const last = focusable[focusable.length - 1];

        // Focus first element
        first.focus();

        // Handle Tab key to cycle within element
        this._handler = (e) => {
            if (e.key !== 'Tab') return;

            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        };

        element.addEventListener('keydown', this._handler);
    },

    /**
     * Release focus trap and restore previous focus
     */
    release() {
        if (this._trapped && this._handler) {
            this._trapped.removeEventListener('keydown', this._handler);
        }
        if (this._previousFocus) {
            this._previousFocus.focus();
        }
        this._trapped = null;
        this._handler = null;
        this._previousFocus = null;
    }
};
window.FocusTrap = FocusTrap;

// ============================================
// STATE INITIALIZATION
// ============================================
// NOTE: State variables are defined in state.js with backward-compatible
// window.* proxies. The code below initializes state from config/session.

// Load config and initialize state on startup
(async function loadConfigOnStartup() {
    try {
        // Load from config first
        const response = await fetch('/api/config');
        if (response.ok) {
            const config = await response.json();

            // Set dbt project path from config
            if (config.dbt_project && config.dbt_project.project_path) {
                userDbtProjectPath = config.dbt_project.project_path;
                sessionStorage.setItem('dbt_training_wheels_dbt_project_path', userDbtProjectPath);
            }

            // Set default domain name from config (first project)
            // Only if not already set in sessionStorage
            const defaultProject = config.default_project || (Array.isArray(config.projects) ? config.projects[0] : '');
            if (!sessionStorage.getItem('dbt_training_wheels_domain_name') && defaultProject) {
                userDomainName = defaultProject;
                sessionStorage.setItem('dbt_training_wheels_domain_name', userDomainName);
                console.log('Default project from config:', userDomainName);
            }

            // Store available projects for UI
            if (config.projects && config.projects.length > 0) {
                window.availableConfigProjects = config.projects;
                console.log('[DEBUG loadConfigOnStartup] Available projects from config:', config.projects);
                console.log('[DEBUG loadConfigOnStartup] config.default_project:', config.default_project);
            } else {
                console.warn('[DEBUG loadConfigOnStartup] No projects found in config!');
            }

            // Load GitHub config
            if (config.github) {
                githubConfig = {
                    enabled: config.github.enabled || false,
                    repository: config.github.repository || '',
                    branch_prefix: config.github.branch_prefix || 'dbt_training_wheels/',
                    auth_method: config.github.auth_method || null,  // 'ssh' when configured
                    base_path: config.github.base_path || ''
                };
                console.log('GitHub integration:', githubConfig.enabled ? 'enabled' : 'disabled');
                console.log('GitHub auth method:', githubConfig.auth_method || 'none');
            }
        }

        // Auto-detect dbt projects (only relevant when not using GitHub integration)
        const detectResponse = await fetch('/api/detect-dbt-projects');
        if (detectResponse.ok) {
            const detectData = await detectResponse.json();
            detectedDbtProjects = detectData.projects || [];
            console.log(`Auto-detected ${detectedDbtProjects.length} dbt projects`);
        }
    } catch (e) {
        console.warn('Could not load config:', e);
    }
})();

// ============================================
// ERROR HANDLER CLASS
// ============================================

class ErrorHandler {
    constructor() {
        this.initializeErrorContainer();
    }

    initializeErrorContainer() {
        // Create error modal container if it doesn't exist
        if (!document.getElementById('error-modal')) {
            const modalHTML = `
                <div id="error-modal" class="error-modal hidden">
                    <div class="error-backdrop"></div>
                    <div class="error-content">
                        <div class="error-header">
                            <svg class="error-icon" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                                <circle cx="12" cy="12" r="10" stroke-width="2"/>
                                <line x1="12" y1="8" x2="12" y2="12" stroke-width="2" stroke-linecap="round"/>
                                <line x1="12" y1="16" x2="12.01" y2="16" stroke-width="2" stroke-linecap="round"/>
                            </svg>
                            <h3 class="error-title">Something went wrong</h3>
                        </div>
                        <div class="error-body">
                            <p class="error-message"></p>
                            <div class="error-help-box">
                                <div class="error-help-header">
                                    <svg class="info-icon" width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
                                        <path d="M8 0C3.58 0 0 3.58 0 8s3.58 8 8 8 8-3.58 8-8-3.58-8-8-8zm.75 12H7.25v-1.5h1.5V12zm0-3H7.25V4h1.5v5z"/>
                                    </svg>
                                    <span>What this means</span>
                                </div>
                                <p class="error-help-text"></p>
                            </div>
                            <div class="error-fixes-box">
                                <div class="error-fixes-header">
                                    <svg class="check-icon" width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
                                        <path d="M13.78 4.22a.75.75 0 010 1.06l-7.25 7.25a.75.75 0 01-1.06 0L2.22 9.28a.75.75 0 011.06-1.06L6 10.94l6.72-6.72a.75.75 0 011.06 0z"/>
                                    </svg>
                                    <span>Try these solutions</span>
                                </div>
                                <ul class="error-fixes-list"></ul>
                            </div>
                            <div class="error-technical-details">
                                <button class="error-technical-toggle" type="button">
                                    <svg class="chevron-icon" width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
                                        <path d="M4.5 3L7.5 6L4.5 9" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
                                    </svg>
                                    <span>Show technical details</span>
                                </button>
                                <div class="error-technical-content hidden">
                                    <div class="technical-detail">
                                        <span class="technical-label">Error Type:</span>
                                        <span class="technical-value error-type"></span>
                                    </div>
                                    <div class="technical-detail">
                                        <span class="technical-label">Error Code:</span>
                                        <span class="technical-value error-code"></span>
                                    </div>
                                    <div class="technical-detail">
                                        <span class="technical-label">Trace ID:</span>
                                        <span class="technical-value error-trace-id"></span>
                                    </div>
                                    <div class="technical-detail">
                                        <span class="technical-label">Details:</span>
                                        <pre class="technical-value error-details"></pre>
                                    </div>
                                </div>
                            </div>
                            <div class="error-docs-link-container">
                                <a href="#" class="error-docs-link" target="_blank">
                                    <svg class="book-icon" width="14" height="14" viewBox="0 0 14 14" fill="currentColor">
                                        <path d="M2 0a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V2a2 2 0 00-2-2H2zm3.5 4.5h3a.5.5 0 010 1h-3a.5.5 0 010-1zm0 2h3a.5.5 0 010 1h-3a.5.5 0 010-1zm0 2h5a.5.5 0 010 1h-5a.5.5 0 010-1z"/>
                                    </svg>
                                    View troubleshooting guide
                                </a>
                            </div>
                        </div>
                        <div class="error-footer">
                            <button class="error-close-btn" type="button">Close</button>
                        </div>
                    </div>
                </div>
            `;
            document.body.insertAdjacentHTML('beforeend', modalHTML);

            // Setup event listeners
            const modal = document.getElementById('error-modal');
            const backdrop = modal.querySelector('.error-backdrop');
            const closeBtn = modal.querySelector('.error-close-btn');
            const techToggle = modal.querySelector('.error-technical-toggle');

            backdrop?.addEventListener('click', () => this.hideError());
            closeBtn?.addEventListener('click', () => this.hideError());
            techToggle?.addEventListener('click', (e) => {
                const content = modal.querySelector('.error-technical-content');
                const chevron = techToggle.querySelector('.chevron-icon');
                content?.classList.toggle('hidden');
                chevron?.classList.toggle('rotated');
            });
        }
    }

    showError(error) {
        const modal = document.getElementById('error-modal');
        if (!modal) return;

        // Populate error details
        modal.querySelector('.error-message').textContent =
            error?.error?.user_message || error?.message || 'An unexpected error occurred';

        modal.querySelector('.error-help-text').textContent =
            error?.error?.beginner_help || 'We encountered an issue while processing your request.';

        // Populate fixes list
        const fixesList = modal.querySelector('.error-fixes-list');
        fixesList.innerHTML = '';
        const fixes = error?.error?.common_fixes || [];
        fixes.forEach(fix => {
            const li = document.createElement('li');
            li.textContent = fix;
            fixesList.appendChild(li);
        });

        // Populate technical details
        modal.querySelector('.error-type').textContent = error?.error?.error_type || 'Unknown';
        modal.querySelector('.error-code').textContent = error?.status || 'N/A';
        modal.querySelector('.error-trace-id').textContent = error?.error?.trace_id || 'N/A';
        modal.querySelector('.error-details').textContent =
            JSON.stringify(error?.error?.details || error, null, 2);

        // Set docs link
        const docsLink = modal.querySelector('.error-docs-link');
        docsLink.href = error?.error?.docs_link || '/troubleshooting';

        // Show modal
        modal.classList.remove('hidden');
    }

    hideError() {
        const modal = document.getElementById('error-modal');
        modal?.classList.add('hidden');
    }

    async safeFetch(url, options = {}) {
        try {
            const response = await fetch(url, options);
            const contentType = response.headers.get('content-type') || '';
            let data = null;
            if (contentType.includes('application/json')) {
                data = await response.json();
            } else {
                const text = await response.text();
                data = {
                    error: {
                        user_message: `Request failed (${response.status})`,
                        beginner_help: 'The server did not return JSON for this request.',
                        common_fixes: [
                            'Try the request again',
                            'Check if the server is running',
                            'Contact support if the problem persists'
                        ],
                        technical_details: {
                            message: text || 'No response body returned'
                        }
                    }
                };
            }

            if (!response.ok) {
                this.showError(data);
                throw new Error(data?.error?.user_message || 'Request failed');
            }

            return data;
        } catch (error) {
            if (error.message !== 'Request failed') {
                this.showError({
                    error: {
                        user_message: 'Network error',
                        beginner_help: 'Could not connect to the server. Please check your connection.',
                        common_fixes: [
                            'Check if the server is running',
                            'Verify your network connection',
                            'Try refreshing the page'
                        ]
                    }
                });
            }
            throw error;
        }
    }
}

// Global error handler instance
const errorHandler = new ErrorHandler();

// ============================================
// HELPER FUNCTIONS - MODEL MANAGEMENT
// ============================================

// The domain a query's models belong to: the folder it was uploaded from.
// 'demo/sample1.sql' -> 'sample1'. Mirrors domain_from_filename() in
// services/domain_resolver.py, which decides where the backend writes the files.
function domainFromFilename(filename) {
    if (!filename) return '';
    const stem = filename.replace(/\\/g, '/').split('/').pop();
    return stem.toLowerCase().endsWith('.sql') ? stem.slice(0, -4) : stem;
}

// The conversion a query belongs to: the folder it was uploaded from.
// 'demo/sample1.sql' -> 'demo'; a root-level 'lone.sql' is its own conversion.
// Mirrors conversion_name_for() in services/query_service.py.
function conversionNameFromFilename(filename) {
    if (!filename) return '';
    const normalized = filename.replace(/\\/g, '/');
    if (normalized.includes('/')) return normalized.split('/')[0];
    return domainFromFilename(normalized);
}

// Get ALL models (staging + mart) in the correct order
function getAllModels() {
    const models = [];

    if (!analysisResults) return models;

    // Get naming prefixes from analysis results or config
    const naming = analysisResults.naming || {};
    const stagingPrefix = naming.stagingModelPrefix ||
                         appState.getNamingPrefix('staging') ||
                         window.orgConfig?.naming?.staging_model_prefix || 'stg__';
    const stagingFolder = naming.stagingFolder ||
                         window.orgConfig?.naming?.staging_folder || 'staging';
    const intermediatePrefix = naming.intermediateModelPrefix ||
                              appState.getNamingPrefix('intermediate') ||
                              window.orgConfig?.naming?.intermediate_model_prefix || 'int__';
    const martPrefix = naming.martModelPrefix ||
                      appState.getNamingPrefix('mart') ||
                      window.orgConfig?.naming?.mart_model_prefix || '';

    // Get layer classification from analysis results
    const layerClassification = analysisResults.layerClassification || {};

    const stagingModelNames = new Set();

    // Add staging models from layer classification only
    // Staging = tables being CREATED that are simple (no CTEs, SCS < 3)
    // NOTE: External sources (hardcodedTables) are NOT staging models - they become source() calls
    const stagingComponents = layerClassification.staging || [];
    stagingComponents.forEach(component => {
        const name = `${stagingPrefix}${component.name}`;
        if (stagingModelNames.has(name)) return;
        stagingModelNames.add(name);
        models.push({
            name,
            displayName: `${name}.sql`,
            type: 'staging',
            table: component.name,
            originalName: component.originalName || component.name,  // Original CTE name before unique naming
            parentTable: component.parentTable,  // Which table this CTE belongs to
            sql: component.transformedSql || component.sql,
            scs: component.scs,
            layer: 'staging'
        });
    });

    // All staging models come from layerClassification only
    // External sources (hardcodedTables) are referenced via source() calls, not separate staging models

    const intermediateModelNames = new Set();

    // Add intermediate models (lighter CTEs)
    const intermediateComponents = layerClassification.intermediate || [];
    intermediateComponents.forEach(component => {
        const name = `${intermediatePrefix}${component.name}`;
        if (intermediateModelNames.has(name)) return;
        intermediateModelNames.add(name);
        models.push({
            name,
            displayName: `${name}.sql`,
            type: 'intermediate',
            table: component.name,
            originalName: component.originalName || component.name,  // Original CTE name before unique naming
            parentTable: component.parentTable,  // Which table this CTE belongs to
            sql: component.transformedSql || component.sql,
            scs: component.scs,
            layer: 'intermediate'
        });
    });

    // All models come from layerClassification (staging/intermediate/mart)
    // No fallback to monolithic intermediate - we always extract CTEs per table

    // Add mart models (final output tables)
    // Mart is a ROLE, not structural - the actual SQL is in the structural layer
    // Mart models are thin wrappers: SELECT * FROM ref('int__tablename')
    const martComponents = layerClassification.mart || [];
    martComponents.forEach(component => {
        models.push({
            name: `${martPrefix}${component.name}`,
            displayName: `${martPrefix}${component.name}.sql`,
            type: 'mart',
            table: component.name,
            sql: component.sql,  // May be null - use buildMartSelectSql() to generate
            scs: component.scs,
            layer: 'mart',
            upstreamCte: component.upstreamCte,  // CTE name the final SELECT references
            structuralLayer: component.structuralLayer  // Which layer has the actual SQL (staging/intermediate)
        });
    });

    return models;
}

function initializeModelConfigurations() {
    const allModels = getAllModels();

    allModels.forEach((model, idx) => {
        if (!modelConfigurations[idx]) {
            modelConfigurations[idx] = {
                table: model.name,
                type: model.type,
                materialization: model.type === 'prep' ? 'table' : 'table',
                schema: '',  // Configure schema in dbt_project.yml
                tags: [...(modelTags[idx] || [])]
            };
        }
    });
}

function getModelConfig(modelIdx) {
    return modelConfigurations[modelIdx] || {
        materialization: 'table',
        schema: '',
        tags: []
    };
}

function getSavedDescription(modelName) {
    if (!modelName || typeof modelConfigurations === 'undefined') {
        return '';
    }

    // Try direct key lookup first, then search by table name
    // (configs may be keyed by numeric index from initializeModelConfigurations)
    const config = modelConfigurations[modelName]
        || Object.values(modelConfigurations).find(c => c.table === modelName);
    return config && config.description ? config.description : '';
}

function updateModelConfig(modelIdx, field, value) {
    if (!modelConfigurations[modelIdx]) {
        const allModels = getAllModels();
        modelConfigurations[modelIdx] = {
            table: allModels[modelIdx]?.name || '',
            type: allModels[modelIdx]?.type || 'final',
            materialization: 'table',
            schema: 'prep',
            tags: []
        };
    }
    modelConfigurations[modelIdx][field] = value;

    // Persist the entire modelConfigurations object locally
    appState.set('modelConfigurations', modelConfigurations, { session: true });

    // Sync to backend (debounced) if we have a current query
    if (currentQuery?.id) {
        const stepId = field === 'materialization' ? 'materialization' :
                      field === 'schema' ? 'schema' :
                      field === 'tags' ? 'tags' : null;

        if (stepId) {
            // Build the models array for the update
            const models = Object.values(modelConfigurations).map(mc => ({
                table: mc.table,
                [field]: mc[field]
            }));

            // Use debounced update (300ms delay)
            appState.updateStepConfig(currentQuery.id, stepId, { models });
        }
    }
}

// Load tags configuration (returns default tags)
async function loadTagsConfig() {
    // Set default available tags if not already set
    if (availableTags.length === 0) {
        availableTags = ['daily', 'weekly', 'hourly', 'critical', 'pii', 'finance'];
    }
    // Return empty - tags are assigned per model in the Tags step
    return [];
}

// ============================================
// HELPER FUNCTIONS - UI TOGGLES
// ============================================

/**
 * Generic helper to toggle visibility of a section with optional chevron rotation.
 * @param {string} contentId - ID of the content element to show/hide
 * @param {string} chevronId - ID of the chevron element to rotate (optional)
 * @param {string} rotateClass - CSS class for rotation (default: 'rotate-180')
 */
function toggleHelpSection(contentId, chevronId, rotateClass = 'rotate-180') {
    const content = document.getElementById(contentId);
    const chevron = chevronId ? document.getElementById(chevronId) : null;
    if (content) {
        content.classList.toggle('hidden');
        if (chevron) {
            chevron.classList.toggle(rotateClass);
        }
    }
}

function toggleStepHelp(stepId) {
    toggleHelpSection(`help-content-step${stepId}`, `help-chevron-step${stepId}`);
}

// Backward compatibility wrappers
function toggleBeginnerHelp() { toggleStepHelp(0); }
function toggleStep3Help() { toggleStepHelp(2); }

function toggleAdvancedSources() {
    toggleHelpSection('advanced-sources-content', 'advanced-sources-chevron', 'rotate-90');
}

// ============================================
// HELPER FUNCTIONS - SYNCHRONIZED SCROLLING
// ============================================

function setupDiffSyncScroll() {
    const leftPanel = document.getElementById('original-sql-panel');
    const rightPanel = document.getElementById('transformed-sql-panel');

    if (!leftPanel || !rightPanel) return;

    let isLeftScrolling = false;
    let isRightScrolling = false;

    leftPanel.addEventListener('scroll', () => {
        if (!isRightScrolling) {
            isLeftScrolling = true;
            rightPanel.scrollTop = leftPanel.scrollTop;
            setTimeout(() => { isLeftScrolling = false; }, 100);
        }
    }, { passive: true });

    rightPanel.addEventListener('scroll', () => {
        if (!isLeftScrolling) {
            isRightScrolling = true;
            leftPanel.scrollTop = rightPanel.scrollTop;
            setTimeout(() => { isRightScrolling = false; }, 100);
        }
    }, { passive: true });
}

// ============================================
// PREREQUISITE CHECKLIST MODAL SYSTEM
// ============================================

// Global setting to enable/disable prerequisite modals
let prerequisitesEnabled = localStorage.getItem('dbt_training_wheels_prerequisites_enabled') !== 'false'; // Default: enabled

// Toggle prerequisite modals on/off
function togglePrerequisites() {
    prerequisitesEnabled = !prerequisitesEnabled;
    localStorage.setItem('dbt_training_wheels_prerequisites_enabled', prerequisitesEnabled.toString());

    // Update button UI
    const toggleBtn = document.getElementById('prerequisite-toggle-btn');
    if (toggleBtn) {
        updatePrerequisiteToggleButton(toggleBtn);
    }

    return prerequisitesEnabled;
}

// Update toggle button appearance
function updatePrerequisiteToggleButton(btn) {
    if (prerequisitesEnabled) {
        btn.innerHTML = `
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path>
            </svg>
            <span>Checklists: ON</span>
        `;
        btn.classList.remove('bg-gray-100', 'text-gray-600');
        btn.classList.add('bg-green-50', 'text-green-700', 'border-green-200');
    } else {
        btn.innerHTML = `
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
            </svg>
            <span>Checklists: OFF</span>
        `;
        btn.classList.remove('bg-green-50', 'text-green-700', 'border-green-200');
        btn.classList.add('bg-gray-100', 'text-gray-600', 'border-gray-300');
    }
}

// Configuration for prerequisite questions for each step transition
// Steps are numbered 1-11 based on enabled steps
// Exported to window for access from main.js
const PREREQUISITE_CONFIG = window.PREREQUISITE_CONFIG = {
    // Questions shown when first loading step 1
    // Note: This is dynamically modified based on GitHub config - see getPrerequisiteConfig()
    'step1_load': {
        title: 'Prerequisite Checklist',
        subtitle: 'Before you begin, please ensure you\'ve completed these essential setup steps',
        alertType: 'warning', // 'warning', 'info', 'success'
        alertTitle: 'Important Notice',
        alertMessage: 'Complete all prerequisites before proceeding. This ensures a smooth dbt transformation process.',
        questions: [
            {
                title: 'Select your project',
                description: '<strong>What:</strong> Choose the project/domain for organizing your models<br><strong>Why:</strong> Models will be saved under this project folder<br><strong>How:</strong> Select from configured projects below',
                type: 'dropdown',
                inputId: 'prereq-project-select',
                placeholder: 'Select a project...'
            },
            {
                id: 'git-branch-question',
                title: 'I have created a new git branch',
                description: '<strong>What:</strong> Create a feature branch in your dbt project<br><strong>Why:</strong> Protects your main branch and enables code review<br><strong>How:</strong> Run <code class="bg-gray-100 px-2 py-0.5 rounded text-xs">git checkout -b feature/convert-[query-name]</code>'
            },
            {
                id: 'mart-selection',
                type: 'mart_selection',
                title: 'I understand my scheduled query structure',
                description: '<strong>What:</strong> Review your scheduled query\'s table outputs<br><strong>Why:</strong> Select the final business tables to treat as marts<br><strong>How:</strong> Choose your final outputs below'
            }
        ],
        continueText: 'Continue to Analysis'
    },
    // ============================================
    // LAYER STEPS (Steps 1-4)
    // ============================================
    // Step 1 (Analyze) -> Step 2 (Staging Layer)
    // Checklist focuses on what user learned in Step 1 (Analyze)
    'step1_to_step2': {
        title: 'Completed Source Analysis?',
        subtitle: 'Confirm what you learned in the Analysis step',
        alertType: 'info',
        alertTitle: 'Analysis Complete',
        alertMessage: 'You have identified the source tables your query depends on.',
        questions: [
            {
                title: 'I have reviewed the source tables identified',
                description: 'Saw which external tables (datasets) my SQL query reads from'
            },
            {
                title: 'I understand which tables will become {{ source() }} calls',
                description: 'These are external tables that dbt will reference using source definitions'
            },
            {
                title: 'I am ready to proceed',
                description: 'Ready to see how my SQL is decomposed into dbt model layers'
            }
        ],
        continueText: 'Continue to Staging'
    },
    // Step 2 (Staging Layer) -> Step 3 (Intermediate Layer)
    // Checklist focuses on what user learned in Step 2 (Staging)
    'step2_to_step3': {
        title: 'Completed Staging Review?',
        subtitle: 'Confirm what you learned in the Staging Layer step',
        alertType: 'info',
        alertTitle: 'Staging Layer Complete',
        alertMessage: 'You have reviewed the staging models that clean and standardize raw data.',
        questions: [
            {
                title: 'I have reviewed the staging models',
                description: 'Saw the models prefixed with stg__ that clean raw source data'
            },
            {
                title: 'I understand what staging models do',
                description: 'They standardize column names, cast data types, and apply basic cleaning'
            },
            {
                title: 'I am ready to proceed',
                description: 'Ready to see the intermediate layer models'
            }
        ],
        continueText: 'Continue to Intermediate'
    },
    // Step 3 (Intermediate Layer) -> Step 4 (Mart Layer)
    // Checklist focuses on what user learned in Step 3 (Intermediate)
    'step3_to_step4': {
        title: 'Completed Intermediate Review?',
        subtitle: 'Confirm what you learned in the Intermediate Layer step',
        alertType: 'info',
        alertTitle: 'Intermediate Layer Complete',
        alertMessage: 'You have reviewed the intermediate models that transform and join data.',
        questions: [
            {
                title: 'I have reviewed the intermediate models',
                description: 'Saw the models prefixed with int__ that join and transform data'
            },
            {
                title: 'I understand what intermediate models do',
                description: 'They combine staging models with business logic like joins and aggregations'
            },
            {
                title: 'I am ready to proceed',
                description: 'Ready to see the final mart layer models'
            }
        ],
        continueText: 'Continue to Mart'
    },
    // ============================================
    // CROSS-PROJECT & SQL REVIEW (Steps 4-7)
    // ============================================
    // Step 4 (Mart Layer) -> Step 5 (Cross-Project Refs)
    // Checklist focuses on what user learned in Step 4 (Mart)
    'step4_to_step5': {
        title: 'Completed Mart Review?',
        subtitle: 'Confirm what you learned in the Mart Layer step',
        alertType: 'info',
        alertTitle: 'Mart Layer Complete',
        alertMessage: 'You have reviewed the mart models - your final business-facing outputs.',
        questions: [
            {
                title: 'I have reviewed the mart models',
                description: 'Saw the models prefixed with mart__ that produce final outputs'
            },
            {
                title: 'I understand what mart models do',
                description: 'They are the final tables/views that analysts and dashboards query'
            },
            {
                title: 'I am ready to proceed',
                description: 'Ready to check for cross-project dependencies'
            }
        ],
        continueText: 'Continue to Cross-Project'
    },
    // Step 5 (Cross-Project Refs) -> Step 6 (Materialization)
    // Checklist focuses on what user learned in Step 5 (Cross-Project)
    'step5_to_step6': {
        title: 'Completed Cross-Project Review?',
        subtitle: 'Confirm what you learned in the Cross-Project References step',
        alertType: 'info',
        alertTitle: 'Cross-Project Check Complete',
        alertMessage: 'You have reviewed any dependencies on other dbt projects.',
        questions: [
            {
                title: 'I have reviewed cross-project references',
                description: 'Checked if my query uses tables from other dbt projects'
            },
            {
                title: 'I understand {{ ref() }} vs {{ source() }}',
                description: 'ref() is for other dbt models, source() is for external tables'
            },
            {
                title: 'I am ready to proceed',
                description: 'Ready to configure materialization for each model'
            }
        ],
        continueText: 'Continue to Materialization'
    },
    // ============================================
    // CONFIGURATION STEPS (Steps 6-8)
    // ============================================
    // Step 6 (Materialization) -> Step 7 (Tags)
    // Checklist focuses on what user learned in Step 6 (Materialization)
    'step6_to_step7': {
        title: 'Completed Materialization?',
        subtitle: 'Confirm your materialization choices',
        alertType: 'info',
        alertTitle: 'Materialization Complete',
        alertMessage: 'You have configured how dbt builds each model.',
        questions: [
            {
                title: 'I have selected materialization types for all models',
                description: 'Chosen whether each model should be a table, view, or incremental build'
            },
            {
                title: 'I understand how materialization affects performance',
                description: 'Tables are faster to query but slower to build; views are opposite; incremental is for large datasets'
            },
            {
                title: 'I am ready to add tags for model selection',
                description: 'Tags allow you to run subsets of models (e.g., dbt run --select tag:daily)'
            }
        ],
        continueText: 'Continue to Tags'
    },
    // Step 7 (Tags) -> Step 8 (Sources)
    // Checklist focuses on what user learned in Step 7 (Tags)
    'step7_to_step8': {
        title: 'Completed Tagging?',
        subtitle: 'Confirm your tagging strategy',
        alertType: 'info',
        alertTitle: 'Tags Complete',
        alertMessage: 'You have added tags to organize your models.',
        questions: [
            {
                title: 'I have added tags to relevant models',
                description: 'Tagged models appropriately for selection and organization (daily, weekly, hourly, etc.)'
            },
            {
                title: 'I understand how tags are used in dbt',
                description: 'Tags allow selective execution like dbt run --select tag:daily'
            },
            {
                title: 'I am ready to define my source tables',
                description: 'sources.yml declares external tables used by your models'
            }
        ],
        continueText: 'Continue to Sources'
    },
    // Step 8 (Sources) -> Step 9 (Review)
    // Checklist focuses on what user learned in Step 8 (Sources)
    'step8_to_step9': {
        title: 'Ready to Review?',
        subtitle: 'Confirm your source definitions before reviewing the lineage',
        alertType: 'info',
        alertTitle: 'Sources Complete',
        alertMessage: 'You have reviewed the sources.yml file for your external tables.',
        questions: [
            {
                title: 'I have reviewed the generated sources.yml file',
                description: 'Verified that all source tables are correctly defined with proper schema and table names'
            },
            {
                title: 'I understand how {{ source() }} references work',
                description: 'Know that {{ source(\'schema\', \'table\') }} calls reference the tables defined in sources.yml'
            },
            {
                title: 'I am ready to review the model lineage',
                description: 'Understand the data flow from sources through prep models to final models'
            }
        ],
        continueText: 'Continue to Review'
    },
    // ============================================
    // REVIEW & DEPLOY STEPS (Steps 9-11)
    // ============================================
    // Step 9 (Review) -> Step 10 (Deploy)
    'step9_to_step10': {
        title: 'Ready to Deploy?',
        subtitle: 'Confirm you have reviewed the model configurations',
        alertType: 'warning',
        alertTitle: 'Deployment',
        alertMessage: 'You will add the generated dbt models directly to your dbt project.',
        questions: [
            {
                title: 'I have reviewed the sources and models summary',
                description: 'Verified the count of sources and models that will be created'
            },
            {
                title: 'I have confirmed all model configurations',
                description: 'Checked schema, materialization, and tags for each model'
            },
            {
                title: 'I am ready to add models to my dbt project',
                description: 'Files will be written directly to your dbt project folder'
            }
        ],
        continueText: 'Continue to Deploy'
    }
};

/**
 * Get prerequisite config with dynamic modifications based on GitHub settings
 * @param {string} configKey - Key from PREREQUISITE_CONFIG
 * @returns {object} Modified config object
 */
function getPrerequisiteConfig(configKey) {
    const config = JSON.parse(JSON.stringify(PREREQUISITE_CONFIG[configKey])); // Deep clone

    // For step1_load, modify based on GitHub config (SSH auth)
    if (configKey === 'step1_load' && githubConfig.enabled && githubConfig.auth_method === 'ssh') {
        // Remove the dbt project path question - not needed when pushing to GitHub
        config.questions = config.questions.filter(q => q.inputId !== 'prereq-dbt-project-path');

        // Find and replace the git branch checkbox with a branch name input
        config.questions = config.questions.map(q => {
            if (q.id === 'git-branch-question') {
                return {
                    title: 'Enter your GitHub branch name',
                    description: `<strong>What:</strong> Branch name for pushing your dbt models<br><strong>Why:</strong> DBT Training Wheels will push files directly to this branch on GitHub<br><strong>How:</strong> We'll create this branch in <code class="bg-gray-100 px-2 py-0.5 rounded text-xs">${githubConfig.repository}</code>`,
                    type: 'text',
                    inputId: 'prereq-github-branch',
                    placeholder: `e.g., ${githubConfig.branch_prefix}marketing-models`
                };
            }
            return q;
        });

        // Update continue button text
        config.continueText = 'Continue to Analysis';
    }

    return config;
}

/**
 * Show a prerequisite modal for a step transition
 * @param {string} configKey - Key from PREREQUISITE_CONFIG (e.g., 'step1_to_step2')
 * @param {function} onComplete - Callback function to execute when prerequisites are completed
 */
function showPrerequisiteModal(configKey, onComplete) {
    // The first prerequisite (step1_load) is ALWAYS required - it captures project path and domain
    const isFirstPrerequisite = configKey === 'step1_load';

    // If prerequisites are disabled AND this is NOT the first prerequisite, skip
    if (!prerequisitesEnabled && !isFirstPrerequisite) {
        if (onComplete && typeof onComplete === 'function') {
            onComplete();
        }
        return;
    }

    const config = getPrerequisiteConfig(configKey);
    if (!config) {
        console.error(`No prerequisite config found for: ${configKey}`);
        if (onComplete) onComplete();
        return;
    }

    const modalId = `prereq-modal-${configKey}`;

    // Determine alert type class
    const alertClass = `prereq-alert-${config.alertType || 'info'}`;

    // Build questions HTML (supports checkbox, text input, and dropdown types)
    const questionsHtml = config.questions.map((q, idx) => {
        // Dropdown type - for project selection from config
        if (q.type === 'dropdown' && q.inputId === 'prereq-project-select') {
            const projects = window.availableConfigProjects || [];
            const currentProject = userDomainName || '';

            if (projects.length === 0) {
                return `
                    <div class="prereq-checklist-item">
                        <div style="background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 12px;">
                            <h4 class="prereq-item-title" style="color: #dc2626;">No projects configured</h4>
                            <p class="prereq-item-description" style="color: #7f1d1d;">
                                Please add projects to your <code style="background: #fee2e2; padding: 2px 6px; border-radius: 4px;">dbt_training_wheels_config.yaml</code> file.
                            </p>
                        </div>
                    </div>
                `;
            }

            console.log('[DEBUG prereq-project-select] Building options, projects:', projects);
            const options = projects.map(p => {
                const selected = p === currentProject ? 'selected' : '';
                const displayName = p.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
                console.log('[DEBUG prereq-project-select] option value:', p, 'displayName:', displayName);
                return `<option value="${p}" ${selected}>${displayName}</option>`;
            }).join('');

            return `
                <div class="prereq-checklist-item">
                    <div>
                        <h4 class="prereq-item-title">${q.title}</h4>
                        <p class="prereq-item-description dbt-mb-3">${q.description}</p>
                        <select
                            id="${q.inputId}"
                            data-question-idx="${idx}"
                            onchange="handleProjectSelection(this); validatePrerequisiteModal('${modalId}', ${config.questions.length})"
                            style="width: 100%; padding: 10px 12px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; background: white;"
                        >
                            <option value="">${q.placeholder || 'Select a project...'}</option>
                            ${options}
                        </select>
                    </div>
                </div>
            `;
        }

        if (q.type === 'mart_selection') {
            const storedSelection = Array.isArray(userMartSelection) ? userMartSelection : [];
            const isChecked = storedSelection.length > 0 ? 'checked' : '';
            const panelClass = storedSelection.length > 0 ? '' : 'hidden';

            return `
                <div class="prereq-checklist-item" id="${modalId}-mart-selection-container" data-min-mart-tables="1" data-loaded="false">
                    <label>
                        <input
                            type="checkbox"
                            id="${modalId}-q${idx}"
                            data-question-idx="${idx}"
                            data-mart-selection-toggle="true"
                            onchange="handleMartSelectionToggle(this, '${modalId}', ${config.questions.length})"
                            ${isChecked}
                        >
                        <div>
                            <h4 class="prereq-item-title">${q.title}</h4>
                            <p class="prereq-item-description">${q.description}</p>
                        </div>
                    </label>
                    <div id="${modalId}-mart-selection-panel" class="prereq-mart-selection-panel ${panelClass}">
                        <div class="prereq-mart-selection-header">
                            <div onclick="toggleMartSelectionList('${modalId}')" style="flex: 1; display: flex; align-items: center; gap: 0.5rem; cursor: pointer;">
                                <p class="prereq-mart-selection-title">Which tables are your FINAL outputs that you want to use?</p>
                                <button type="button" class="prereq-mart-selection-toggle">
                                    <svg id="${modalId}-mart-chevron" class="dbt-chevron" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                                    </svg>
                                </button>
                            </div>
                            <button type="button" class="prereq-mart-fullscreen-btn" onclick="openMartSelectionFullscreen('${modalId}', ${config.questions.length})" title="Expand to full screen">
                                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" style="width: 1.25rem; height: 1.25rem;">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4"></path>
                                </svg>
                            </button>
                        </div>
                        <div id="${modalId}-mart-selection-list" class="prereq-mart-selection-list hidden">
                            <div class="prereq-mart-selection-placeholder">Check the box to load detected tables.</div>
                        </div>
                        <div class="prereq-mart-selection-footer">
                            <span id="${modalId}-mart-selection-count">0 selected (min 1)</span>
                        </div>
                    </div>
                </div>
            `;
        }

        if (q.type === 'text') {
            // Regular text input
            // Determine initial value based on input type
            let initialValue = '';
            if (q.inputId === 'prereq-github-branch') {
                initialValue = userGitHubBranch;
            }

            return `
                <div class="prereq-checklist-item">
                    <div>
                        <h4 class="prereq-item-title">${q.title}</h4>
                        <p class="prereq-item-description dbt-mb-3">${q.description}</p>
                        <input
                            type="text"
                            id="${q.inputId || modalId + '-text-' + idx}"
                            data-question-idx="${idx}"
                            placeholder="${q.placeholder || ''}"
                            oninput="validatePrerequisiteModal('${modalId}', ${config.questions.length})"
                            value="${initialValue}"
                        >
                    </div>
                </div>
            `;
        } else {
            // Checkbox question (default)
            return `
                <div class="prereq-checklist-item">
                    <label>
                        <input
                            type="checkbox"
                            id="${modalId}-q${idx}"
                            data-question-idx="${idx}"
                            onchange="validatePrerequisiteModal('${modalId}', ${config.questions.length})"
                        >
                        <div>
                            <h4 class="prereq-item-title">${q.title}</h4>
                            <p class="prereq-item-description">${q.description}</p>
                        </div>
                    </label>
                </div>
            `;
        }
    }).join('');

    const alertIconPath = config.alertType === 'warning'
        ? 'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z'
        : 'M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z';

    const modalHtml = `
        <div id="${modalId}" class="prereq-modal-overlay">
            <div class="prereq-modal-container">
                <!-- Header -->
                <div class="prereq-modal-header">
                    <div class="prereq-modal-header-title">
                        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                        <h2>${config.title}</h2>
                    </div>
                    <p>${config.subtitle}</p>
                </div>

                <!-- Content -->
                <div class="prereq-modal-content">
                    <div class="prereq-alert ${alertClass}">
                        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="${alertIconPath}"></path>
                        </svg>
                        <div>
                            <h3 class="prereq-alert-title">${config.alertTitle}</h3>
                            <p class="prereq-alert-message">${config.alertMessage}</p>
                        </div>
                    </div>

                    <!-- Checklist Items -->
                    <div class="prereq-checklist">
                        ${questionsHtml}
                    </div>

                    <!-- Footer with action button -->
                    <div class="prereq-modal-footer">
                        <div class="prereq-modal-counter">
                            <span id="${modalId}-counter">0/${config.questions.length}</span> checks completed
                        </div>
                        <button
                            id="${modalId}-btn"
                            onclick="completePrerequisiteModal('${modalId}')"
                            disabled
                            class="prereq-modal-btn"
                        >
                            ${config.continueText}
                            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path>
                            </svg>
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `;

    // Remove existing modal if present
    const existingModal = document.getElementById(modalId);
    if (existingModal) {
        existingModal.remove();
    }

    // Add modal to body
    const modalContainer = document.createElement('div');
    modalContainer.innerHTML = modalHtml;
    document.body.appendChild(modalContainer.firstElementChild);

    // Auto-load mart selection if pre-checked
    const martToggle = document.querySelector(`#${modalId} input[data-mart-selection-toggle="true"]`);
    if (martToggle && martToggle.checked) {
        loadMartSelectionOptions(modalId, config.questions.length);
    }

    // Store callback
    window[`prereqCallback_${modalId}`] = onComplete;

    // Prevent scrolling on body when modal is open
    document.body.style.overflow = 'hidden';
}

/**
 * Handle toggling of the mart selection section inside the prerequisite modal.
 * @param {HTMLInputElement} checkbox - The toggle checkbox element
 * @param {string} modalId - The modal ID
 * @param {number} totalQuestions - Total number of prerequisite questions
 */
/**
 * Toggle the visibility of the mart selection table list.
 * @param {string} modalId - The modal ID
 */
function toggleMartSelectionList(modalId) {
    const listEl = document.getElementById(`${modalId}-mart-selection-list`);
    const chevron = document.getElementById(`${modalId}-mart-chevron`);

    if (listEl && chevron) {
        listEl.classList.toggle('hidden');
        chevron.classList.toggle('rotate-180');
    }
}

/**
 * Open mart selection in fullscreen overlay for better visibility.
 * @param {string} modalId - The modal ID
 * @param {number} totalQuestions - Total number of prerequisite questions
 */
async function openMartSelectionFullscreen(modalId, totalQuestions) {
    const container = document.getElementById(`${modalId}-mart-selection-container`);
    if (!container) return;

    // Load tables if not already loaded
    if (container.dataset.loaded !== 'true') {
        await loadMartSelectionOptions(modalId, totalQuestions);
    }

    // Get current selections
    const currentSelections = Array.from(
        container.querySelectorAll('input[data-mart-table]:checked')
    ).map(input => input.getAttribute('data-mart-table'));

    // Get all tables data
    const allTables = [];
    container.querySelectorAll('input[data-mart-table]').forEach(input => {
        const label = input.closest('label');
        if (label) {
            const tableName = input.getAttribute('data-mart-table');
            const nameEl = label.querySelector('.prereq-mart-selection-name');
            const metaEl = label.querySelector('.prereq-mart-selection-meta');
            const reasonEl = label.querySelector('.prereq-mart-selection-reason');

            allTables.push({
                name: tableName,
                displayName: nameEl ? nameEl.textContent : tableName,
                meta: metaEl ? metaEl.textContent : '',
                reason: reasonEl ? reasonEl.textContent : '',
                selected: currentSelections.includes(tableName)
            });
        }
    });

    // Create fullscreen overlay
    const overlay = document.createElement('div');
    overlay.id = 'mart-fullscreen-overlay';
    overlay.className = 'mart-fullscreen-overlay';

    const escape = typeof escapeHtml === 'function' ? escapeHtml : (text) => text;

    overlay.innerHTML = `
        <div class="mart-fullscreen-content">
            <div class="mart-fullscreen-header">
                <h3 style="margin: 0; font-size: 1.25rem; font-weight: 600; color: var(--brand-black);">
                    Select Final Output Tables
                </h3>
                <button type="button" onclick="closeMartSelectionFullscreen('${modalId}', ${totalQuestions})" class="mart-fullscreen-close" title="Close">
                    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" style="width: 1.5rem; height: 1.5rem;">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
                    </svg>
                </button>
            </div>
            <div class="mart-fullscreen-body">
                <p style="font-size: 0.875rem; color: var(--brand-gray-dark); margin-bottom: 1rem;">
                    Check the boxes next to the tables you want to use as final mart outputs. These will be the business-facing tables that analysts and dashboards query.
                </p>
                <div class="mart-fullscreen-list">
                    ${allTables.map(table => `
                        <label class="mart-fullscreen-option">
                            <input
                                type="checkbox"
                                data-mart-table-fs="${escape(table.name)}"
                                ${table.selected ? 'checked' : ''}
                            >
                            <div style="flex: 1;">
                                <div style="font-weight: 600; font-size: 0.95rem; color: var(--brand-black); margin-bottom: 0.25rem;">
                                    ${escape(table.displayName)}
                                </div>
                                <div style="font-size: 0.8rem; color: var(--brand-gray-dark); margin-bottom: 0.15rem;">
                                    ${escape(table.meta)}
                                </div>
                                <div style="font-size: 0.8rem; color: var(--brand-gray-dark);">
                                    ${escape(table.reason)}
                                </div>
                            </div>
                        </label>
                    `).join('')}
                </div>
            </div>
            <div class="mart-fullscreen-footer">
                <span id="mart-fullscreen-count" style="font-size: 0.875rem; color: var(--brand-gray-dark);">
                    ${currentSelections.length} selected (min ${container.dataset.minMartTables || 1})
                </span>
                <button type="button" onclick="closeMartSelectionFullscreen('${modalId}', ${totalQuestions})" class="dbt-btn dbt-btn-primary">
                    Done
                </button>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);

    // Attach change listeners to update count
    overlay.querySelectorAll('input[data-mart-table-fs]').forEach(checkbox => {
        checkbox.addEventListener('change', () => {
            const count = overlay.querySelectorAll('input[data-mart-table-fs]:checked').length;
            const countEl = document.getElementById('mart-fullscreen-count');
            if (countEl) {
                countEl.textContent = `${count} selected (min ${container.dataset.minMartTables || 1})`;
            }
        });
    });

    // Fade in
    setTimeout(() => overlay.classList.add('visible'), 10);
}

/**
 * Close fullscreen mart selection and apply selections.
 * @param {string} modalId - The modal ID
 * @param {number} totalQuestions - Total number of prerequisite questions
 */
function closeMartSelectionFullscreen(modalId, totalQuestions) {
    const overlay = document.getElementById('mart-fullscreen-overlay');
    if (!overlay) return;

    // Get selections from fullscreen
    const selectedTables = Array.from(
        overlay.querySelectorAll('input[data-mart-table-fs]:checked')
    ).map(input => input.getAttribute('data-mart-table-fs'));

    // Apply to original checkboxes
    const container = document.getElementById(`${modalId}-mart-selection-container`);
    if (container) {
        container.querySelectorAll('input[data-mart-table]').forEach(input => {
            const tableName = input.getAttribute('data-mart-table');
            input.checked = selectedTables.includes(tableName);
        });

        // Update state and validation
        handleMartSelectionChange(modalId, totalQuestions);
    }

    // Fade out and remove
    overlay.classList.remove('visible');
    setTimeout(() => overlay.remove(), 300);
}

async function handleMartSelectionToggle(checkbox, modalId, totalQuestions) {
    const panel = document.getElementById(`${modalId}-mart-selection-panel`);
    if (!panel) return;

    if (checkbox.checked) {
        panel.classList.remove('hidden');
        await loadMartSelectionOptions(modalId, totalQuestions);
    } else {
        panel.classList.add('hidden');
    }

    validatePrerequisiteModal(modalId, totalQuestions);
}

/**
 * Load detected tables and render mart selection options.
 * @param {string} modalId - The modal ID
 * @param {number} totalQuestions - Total number of prerequisite questions
 */
async function loadMartSelectionOptions(modalId, totalQuestions) {
    const container = document.getElementById(`${modalId}-mart-selection-container`);
    const listEl = document.getElementById(`${modalId}-mart-selection-list`);

    if (!container || !listEl) return;

    if (container.dataset.loaded === 'true') {
        handleMartSelectionChange(modalId, totalQuestions);
        return;
    }

    if (!currentQuery || !currentQuery.id) {
        listEl.innerHTML = '<div class="prereq-mart-selection-placeholder">No query selected yet.</div>';
        return;
    }

    listEl.innerHTML = '<div class="prereq-mart-selection-placeholder">Detecting tables...</div>';

    try {
        const projectName = userDomainName || sessionStorage.getItem('dbt_training_wheels_domain_name');
        const params = new URLSearchParams();
        if (projectName) params.append('project_name', projectName);
        const url = params.toString()
            ? `/api/queries/${currentQuery.id}/detect-tables?${params.toString()}`
            : `/api/queries/${currentQuery.id}/detect-tables`;

        const response = await errorHandler.safeFetch(url);
        const detectedTables = response?.detectedTables || [];
        const recommended = response?.recommendations?.mart || [];
        const minMartTables = response?.minMartTables || 1;

        container.dataset.minMartTables = String(minMartTables);
        container.dataset.loaded = 'true';

        if (detectedTables.length === 0) {
            listEl.innerHTML = '<div class="prereq-mart-selection-placeholder">No output tables detected.</div>';
            handleMartSelectionChange(modalId, totalQuestions);
            return;
        }

        const storedSelection = Array.isArray(userMartSelection) ? userMartSelection : [];
        const defaultSelection = storedSelection.length > 0 ? storedSelection : recommended;

        const grouped = detectedTables.reduce((acc, table) => {
            const groupKey = table.dataset || 'unknown';
            if (!acc[groupKey]) acc[groupKey] = [];
            acc[groupKey].push(table);
            return acc;
        }, {});

        const groupKeys = Object.keys(grouped).sort();
        const escape = typeof escapeHtml === 'function' ? escapeHtml : (text) => text;

        listEl.innerHTML = groupKeys.map(groupKey => `
            <div class="prereq-mart-selection-group">
                <div class="prereq-mart-selection-group-title">${escape(groupKey)}</div>
                ${grouped[groupKey].map(table => {
                    const isChecked = defaultSelection.includes(table.name) ? 'checked' : '';
                    const displayName = table.fullName || table.name;
                    return `
                        <label class="prereq-mart-selection-option">
                            <input
                                type="checkbox"
                                data-mart-table="${escape(table.name)}"
                                onchange="handleMartSelectionChange('${modalId}', ${totalQuestions})"
                                ${isChecked}
                            >
                            <div>
                                <div class="prereq-mart-selection-name">${escape(displayName)}</div>
                                <div class="prereq-mart-selection-meta">SCS: ${table.scs} · ${escape(table.complexity || 'unknown')}</div>
                                <div class="prereq-mart-selection-reason">${escape(table.reason || '')}</div>
                            </div>
                        </label>
                    `;
                }).join('')}
            </div>
        `).join('');

        // Auto-expand the list to show the tables
        listEl.classList.remove('hidden');
        const chevron = document.getElementById(`${modalId}-mart-chevron`);
        if (chevron) {
            chevron.classList.add('rotate-180');
        }

        handleMartSelectionChange(modalId, totalQuestions);
    } catch (error) {
        console.error('Failed to detect mart tables:', error);
        listEl.innerHTML = '<div class="prereq-mart-selection-placeholder">Unable to detect tables. Please try again.</div>';
    }
}

/**
 * Update stored mart selection and refresh prerequisite validation.
 * @param {string} modalId - The modal ID
 * @param {number} totalQuestions - Total number of prerequisite questions
 */
function handleMartSelectionChange(modalId, totalQuestions) {
    const container = document.getElementById(`${modalId}-mart-selection-container`);
    if (!container) return;

    const selectedTables = Array.from(
        container.querySelectorAll('input[data-mart-table]:checked')
    ).map(input => input.getAttribute('data-mart-table')).filter(Boolean);

    userMartSelection = selectedTables;
    appState.set('userMartSelection', selectedTables, { session: true });

    const minMartTables = parseInt(container.dataset.minMartTables || '1', 10);
    const counter = document.getElementById(`${modalId}-mart-selection-count`);
    if (counter) {
        const status = selectedTables.length >= minMartTables ? 'selected' : 'selected (needs more)';
        counter.textContent = `${selectedTables.length} ${status} (min ${minMartTables})`;
    }

    validatePrerequisiteModal(modalId, totalQuestions);
}

/**
 * Validate prerequisite modal (checkboxes, text inputs, and selects)
 * @param {string} modalId - The modal ID
 * @param {number} totalQuestions - Total number of questions
 */
function validatePrerequisiteModal(modalId, totalQuestions) {
    const modal = document.getElementById(modalId);
    if (!modal) return;

    let completedCount = 0;

    // Check all checkboxes
    const checkboxes = modal.querySelectorAll('input[type="checkbox"][data-question-idx]');
    checkboxes.forEach(checkbox => {
        if (checkbox.dataset.martSelectionToggle === 'true') {
            const container = checkbox.closest('.prereq-checklist-item');
            const minMartTables = container ? parseInt(container.dataset.minMartTables || '1', 10) : 1;
            const selectedCount = container
                ? container.querySelectorAll('input[data-mart-table]:checked').length
                : 0;
            if (checkbox.checked && selectedCount >= minMartTables) {
                completedCount++;
            }
            return;
        }

        if (checkbox.checked) {
            completedCount++;
        }
    });

    // Check all text inputs (must have non-empty value)
    const textInputs = modal.querySelectorAll('input[type="text"][data-question-idx]');
    textInputs.forEach(input => {
        if (input.value.trim().length > 0) {
            completedCount++;
            // Store domain name if it's the domain input
            // Store dbt project path if it's the path input
            if (input.id === 'prereq-dbt-project-path') {
                userDbtProjectPath = input.value.trim();
                sessionStorage.setItem('dbt_training_wheels_dbt_project_path', userDbtProjectPath);
            }
            // Model group isn't asked for - it's the folder the query was uploaded from

            // Store GitHub branch name if it's the branch input
            if (input.id === 'prereq-github-branch') {
                userGitHubBranch = input.value.trim();
                sessionStorage.setItem('dbt_training_wheels_github_branch', userGitHubBranch);
            }
            // Domain isn't asked for - it's the folder the query was uploaded from
        }
    });

    // Check all select elements (must have a selected value)
    const selects = modal.querySelectorAll('select[data-question-idx]');
    selects.forEach(select => {
        if (select.value.trim().length > 0) {
            completedCount++;
            // Store project selection as domain name
            if (select.id === 'prereq-project-select') {
                console.log('[DEBUG validatePrerequisiteModal] prereq-project-select value:', select.value);
                userDomainName = select.value.trim();
                sessionStorage.setItem('dbt_training_wheels_domain_name', userDomainName);
                console.log('[DEBUG validatePrerequisiteModal] userDomainName set to:', userDomainName);
            }
        }
    });

    const allCompleted = completedCount === totalQuestions;

    // Update counter
    const counter = document.getElementById(`${modalId}-counter`);
    if (counter) {
        counter.textContent = `${completedCount}/${totalQuestions}`;
    }

    // Enable/disable continue button
    const continueBtn = document.getElementById(`${modalId}-btn`);
    if (continueBtn) {
        continueBtn.disabled = !allCompleted;
    }
}

/**
 * Handle project selection from dropdown
 * Sets the domain name based on selected project
 * @param {HTMLSelectElement} selectElement - The select element
 */
function handleProjectSelection(selectElement) {
    const projectName = selectElement.value;
    console.log('[DEBUG handleProjectSelection] selectElement.value:', selectElement.value);
    console.log('[DEBUG handleProjectSelection] projectName:', projectName);
    if (projectName) {
        userDomainName = projectName;
        sessionStorage.setItem('dbt_training_wheels_domain_name', userDomainName);
        console.log('[DEBUG handleProjectSelection] userDomainName set to:', userDomainName);
        console.log('[DEBUG handleProjectSelection] sessionStorage now:', sessionStorage.getItem('dbt_training_wheels_domain_name'));
    }
}

/**
 * Complete prerequisite modal and execute callback
 * @param {string} modalId - The modal ID
 */
async function completePrerequisiteModal(modalId) {
    console.log('completePrerequisiteModal called with modalId:', modalId);
    console.log('[DEBUG completePrerequisiteModal] userDomainName at start:', userDomainName);
    console.log('[DEBUG completePrerequisiteModal] sessionStorage dbt_training_wheels_domain_name:', sessionStorage.getItem('dbt_training_wheels_domain_name'));

    // Project path is stored in sessionStorage only (detected from cwd, no need to persist to config)
    if (userDbtProjectPath) {
        console.log('Using dbt project path:', userDbtProjectPath);
    }

    // Hide and remove the modal
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.remove();
        document.body.style.overflow = 'auto';
        console.log('Modal removed');
    }
    // Create QueryConfiguration on backend with domain_area and model_group
    if (window.currentQuery?.id) {
        const projectName = sessionStorage.getItem('dbt_training_wheels_domain_name') || '';
        await appState.createQueryConfig(window.currentQuery.id, projectName);
    }

    // Execute callback if exists
    const callbackKey = `prereqCallback_${modalId}`;
    const callback = window[callbackKey];
    console.log('Looking for callback:', callbackKey, 'Found:', typeof callback);

    if (callback && typeof callback === 'function') {
        try {
            console.log('Executing callback...');
            await callback();
            console.log('Callback executed successfully');
        } catch (error) {
            console.error('Error executing prerequisite callback:', error);
        } finally {
            delete window[callbackKey];
        }
    } else {
        console.warn('No callback found for modal:', modalId);
    }
}

/**
 * Hide/close prerequisite modal without completing
 * @param {string} modalId - The modal ID
 */
function hidePrerequisiteModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.remove();
        document.body.style.overflow = 'auto';
    }

    // Clean up callback
    if (window[`prereqCallback_${modalId}`]) {
        delete window[`prereqCallback_${modalId}`];
    }
}

// ============================================
// HELPER FUNCTIONS FOR STEP NAVIGATION WITH PREREQUISITES
// ============================================

function navigateWithPrerequisites(configKey, nextStep) {
    console.log('navigateWithPrerequisites called:', configKey, nextStep);
    showPrerequisiteModal(configKey, async function() {
        console.log('navigateWithPrerequisites callback executing, calling saveAndContinue:', nextStep);
        await saveAndContinue(nextStep);
    });
}

function navigateDirectWithPrerequisites(configKey, nextStep) {
    console.log('navigateDirectWithPrerequisites called:', configKey, nextStep);
    showPrerequisiteModal(configKey, async function() {
        console.log('navigateDirectWithPrerequisites callback executing, calling setActiveStep:', nextStep);
        await setActiveStep(nextStep);
    });
}

// ============================================
// NAVIGATION ERROR HANDLING & RACE PREVENTION
// ============================================

/**
 * Race condition prevention lock for navigation
 * Prevents double-clicking from triggering multiple navigations
 */
let navigationInProgress = false;

/**
 * Show a toast notification for navigation errors
 * Uses non-blocking toast notifications for better UX
 * @param {string} message - Error message to display
 * @param {string} type - 'error', 'warning', 'info' (default: 'error')
 * @param {number} duration - Auto-dismiss duration in ms (default: 4000)
 */
function showNavigationError(message, type = 'error', duration = 4000) {
    // Remove any existing navigation toasts
    const existingToasts = document.querySelectorAll('.nav-toast');
    existingToasts.forEach(toast => toast.remove());

    // Create toast container if it doesn't exist
    let toastContainer = document.getElementById('nav-toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.id = 'nav-toast-container';
        toastContainer.className = 'nav-toast-container';
        document.body.appendChild(toastContainer);
    }

    // Determine toast styling based on type
    const typeStyles = {
        error: { bg: 'var(--brand-error)', icon: 'M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z' },
        warning: { bg: '#d97706', icon: 'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z' },
        info: { bg: 'var(--brand-primary-dark)', icon: 'M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z' }
    };
    const style = typeStyles[type] || typeStyles.error;

    // Create toast element
    const toast = document.createElement('div');
    toast.className = 'nav-toast dbt-toast';
    toast.setAttribute('role', 'alert');
    toast.setAttribute('aria-live', 'polite');
    toast.innerHTML = `
        <div class="nav-toast-content" style="background: ${style.bg}; color: white; padding: 12px 16px; border-radius: 8px; display: flex; align-items: center; gap: 10px;">
            <svg class="dbt-w-5 dbt-h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="${style.icon}"></path>
            </svg>
            <span>${message}</span>
            <button class="nav-toast-dismiss" onclick="this.closest('.nav-toast').remove()" style="background: none; border: none; color: white; cursor: pointer; padding: 4px; margin-left: auto;">
                <svg class="dbt-w-4 dbt-h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
                </svg>
            </button>
        </div>
    `;

    toastContainer.appendChild(toast);

    // Auto-dismiss after duration
    if (duration > 0) {
        setTimeout(() => {
            if (toast.parentNode) {
                toast.style.animation = 'slideOutDown 0.3s ease';
                setTimeout(() => toast.remove(), 300);
            }
        }, duration);
    }
}

/**
 * Set navigation button loading state
 * @param {HTMLElement} button - Button element
 * @param {boolean} loading - Whether to show loading state
 */
function setNavigationLoading(button, loading) {
    if (!button) return;

    if (loading) {
        button.classList.add('nav-loading');
        button.disabled = true;
        // Store original content
        button.dataset.originalContent = button.innerHTML;
        // Add spinner
        const spinnerHtml = `
            <svg class="dbt-w-4 dbt-h-4 spinner" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>
            </svg>
            <span>Loading...</span>
        `;
        button.innerHTML = spinnerHtml;
    } else {
        button.classList.remove('nav-loading');
        button.disabled = false;
        // Restore original content
        if (button.dataset.originalContent) {
            button.innerHTML = button.dataset.originalContent;
            delete button.dataset.originalContent;
        }
    }
}

// ============================================
// STEP NAVIGATION HELPERS (using StepRegistry)
// ============================================
// These functions provide easy navigation that automatically
// skips disabled steps. Use these in step files for prev/next buttons.

/**
 * Navigate to the next enabled step with prerequisite check
 * Async version with validation and race condition prevention
 * @param {HTMLElement} buttonElement - Optional button element for loading state
 * @returns {Promise<string|null>} The next step ID, or null if at end or navigation failed
 */
async function goToNextStep(buttonElement = null) {
    // Race condition prevention
    if (navigationInProgress) {
        console.log('Navigation already in progress, ignoring duplicate request');
        return null;
    }

    try {
        navigationInProgress = true;

        // Set loading state on button
        if (buttonElement) {
            setNavigationLoading(buttonElement, true);
        }

        // Use validated step navigation
        const validation = StepRegistry.getNextValidStepId(currentStep);
        if (!validation.valid) {
            showNavigationError(validation.reason || 'Cannot navigate to next step', 'warning');
            return null;
        }

        const nextId = validation.stepId;
        if (!nextId) return null;

        // Persist current step before navigation using async method
        const persisted = await appState.setAsync('currentStep', nextId, { session: true });
        if (!persisted) {
            showNavigationError('Failed to save navigation state. Please try again.', 'error');
            return null;
        }

        // Get current display number to build prerequisite key
        const currentDisplayNum = StepRegistry.idToDisplayNum(currentStep);
        const nextDisplayNum = StepRegistry.idToDisplayNum(nextId);
        const prereqKey = `step${currentDisplayNum}_to_step${nextDisplayNum}`;

        console.log('[goToNextStep] Debug:', {
            currentStep,
            nextId,
            currentDisplayNum,
            nextDisplayNum,
            prereqKey,
            hasConfig: !!(window.PREREQUISITE_CONFIG && window.PREREQUISITE_CONFIG[prereqKey]),
            prerequisitesEnabled,
            allKeys: window.PREREQUISITE_CONFIG ? Object.keys(window.PREREQUISITE_CONFIG) : 'PREREQUISITE_CONFIG not found'
        });

        // Check if prerequisite config exists for this transition
        const prereqConfig = window.PREREQUISITE_CONFIG || PREREQUISITE_CONFIG;
        if (prereqConfig && prereqConfig[prereqKey]) {
            console.log('[goToNextStep] Showing prerequisite modal for:', prereqKey);
            showPrerequisiteModal(prereqKey, async function() {
                await setActiveStep(nextId);
            });
        } else {
            // No prerequisite defined, navigate directly
            console.log('[goToNextStep] No prerequisite config, navigating directly');
            await setActiveStep(nextId);
        }

        return nextId;
    } catch (error) {
        console.error('Navigation error:', error);
        showNavigationError('An error occurred during navigation. Please try again.', 'error');
        return null;
    } finally {
        navigationInProgress = false;
        if (buttonElement) {
            setNavigationLoading(buttonElement, false);
        }
    }
}

/**
 * Navigate to the previous enabled step
 * Async version with validation and race condition prevention
 * @param {HTMLElement} buttonElement - Optional button element for loading state
 * @returns {Promise<string|null>} The previous step ID, or null if at beginning or navigation failed
 */
async function goToPrevStep(buttonElement = null) {
    // Race condition prevention
    if (navigationInProgress) {
        console.log('Navigation already in progress, ignoring duplicate request');
        return null;
    }

    try {
        navigationInProgress = true;

        // Set loading state on button
        if (buttonElement) {
            setNavigationLoading(buttonElement, true);
        }

        // Use validated step navigation
        const validation = StepRegistry.getPrevValidStepId(currentStep);
        if (!validation.valid) {
            showNavigationError(validation.reason || 'Cannot navigate to previous step', 'warning');
            return null;
        }

        const prevId = validation.stepId;
        if (!prevId) return null;

        // Persist current step before navigation using async method
        const persisted = await appState.setAsync('currentStep', prevId, { session: true });
        if (!persisted) {
            showNavigationError('Failed to save navigation state. Please try again.', 'error');
            return null;
        }

        await setActiveStep(prevId);
        return prevId;
    } catch (error) {
        console.error('Navigation error:', error);
        showNavigationError('An error occurred during navigation. Please try again.', 'error');
        return null;
    } finally {
        navigationInProgress = false;
        if (buttonElement) {
            setNavigationLoading(buttonElement, false);
        }
    }
}

/**
 * Get the next enabled step ID (without navigating)
 * @returns {number|null}
 */
function getNextStepId() {
    return StepRegistry.getNextStepId(currentStep);
}

/**
 * Get the previous enabled step ID (without navigating)
 * @returns {number|null}
 */
function getPrevStepId() {
    return StepRegistry.getPrevStepId(currentStep);
}

/**
 * Check if there's a next step available
 * @returns {boolean}
 */
function hasNextStep() {
    return StepRegistry.getNextStepId(currentStep) !== null;
}

/**
 * Check if there's a previous step available
 * @returns {boolean}
 */
function hasPrevStep() {
    return StepRegistry.getPrevStepId(currentStep) !== null;
}

/**
 * Get the current step's display number (1-based, counting only enabled steps)
 * @returns {number|null}
 */
function getCurrentDisplayNum() {
    return StepRegistry.idToDisplayNum(currentStep);
}

/**
 * Get total number of enabled steps
 * @returns {number}
 */
function getTotalEnabledSteps() {
    return StepRegistry.getTotalSteps();
}

/**
 * Generate navigation footer HTML with dynamic step names.
 * Uses StepRegistry to determine next/previous step titles automatically.
 *
 * @param {Object} options - Configuration options
 * @param {string} options.stepId - Current step ID (defaults to currentStep global)
 * @param {string} options.middleContent - Optional HTML content for middle section (e.g., model count)
 * @param {boolean} options.showPrev - Whether to show previous button (default: true)
 * @param {boolean} options.showNext - Whether to show next button (default: true)
 * @param {string} options.nextAction - Custom action for next button (default: goToNextStep)
 * @param {string} options.prevAction - Custom action for prev button (default: goToPrevStep)
 * @param {boolean} options.saveBeforeNav - If true, uses saveAndContinue instead of goToNextStep (default: false)
 * @param {string} options.nextLabel - Custom label for next button (default: "Next: [Step Title]")
 * @returns {string} HTML string for navigation footer
 *
 * @example
 * // Basic usage - auto-detects next step name
 * container.innerHTML += renderNavFooter();
 *
 * @example
 * // With middle content showing count
 * container.innerHTML += renderNavFooter({
 *     middleContent: '<span class="dbt-hint">3 models</span>'
 * });
 *
 * @example
 * // For steps that need to save before navigating
 * container.innerHTML += renderNavFooter({ saveBeforeNav: true });
 */
function renderNavFooter(options = {}) {
    const stepId = options.stepId || currentStep;
    const showPrev = options.showPrev !== false;
    const showNext = options.showNext !== false;
    const saveBeforeNav = options.saveBeforeNav === true;
    const middleContent = options.middleContent || '';

    // Determine next action based on saveBeforeNav option
    const nextAction = options.nextAction || (saveBeforeNav ? 'saveAndContinue' : 'goToNextStep');
    const prevAction = options.prevAction || 'goToPrevStep';

    // Get next and previous step info
    const nextStepId = StepRegistry.getNextStepId(stepId);
    const prevStepId = StepRegistry.getPrevStepId(stepId);
    const nextStep = nextStepId ? StepRegistry.getStepById(nextStepId) : null;
    const prevStep = prevStepId ? StepRegistry.getStepById(prevStepId) : null;

    // Build previous button HTML with accessibility attributes
    const prevButtonHtml = showPrev && prevStep ? `
        <button data-action="${prevAction}" class="dbt-nav-btn dbt-nav-btn-back" aria-label="Go to previous step: ${prevStep.title}">
            <svg class="dbt-w-4 dbt-h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18"></path>
            </svg>
            <span>Back</span>
        </button>
    ` : '<div></div>'; // Empty div for flexbox spacing

    // Build next button HTML with accessibility attributes
    let nextButtonHtml = '';
    if (showNext && nextStep) {
        // Use custom label if provided, otherwise default to "Next: [Step Title]"
        const nextLabel = options.nextLabel || `Next: ${nextStep.title}`;
        nextButtonHtml = `
            <button data-action="${nextAction}" class="dbt-nav-btn dbt-nav-btn-next" aria-label="${nextLabel}">
                <span>${nextLabel}</span>
                <svg class="dbt-w-4 dbt-h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path>
                </svg>
            </button>
        `;
    } else if (showNext) {
        // Last step - no next
        nextButtonHtml = '<div></div>';
    }

    // Combine with optional middle content
    const rightSection = middleContent ? `
        <div class="dbt-flex-center dbt-gap-md">
            ${middleContent}
            ${nextButtonHtml}
        </div>
    ` : nextButtonHtml;

    return `
        <div class="dbt-nav-footer dbt-flex-between">
            ${prevButtonHtml}
            ${rightSection}
        </div>
    `;
}

// ============================================
// VIRTUAL LIST FOR LARGE LISTS (PERF-002)
// ============================================

/**
 * VirtualList - Renders only visible items for performance with large lists.
 * Use when rendering 50+ items to avoid DOM bloat and improve scroll performance.
 *
 * @example
 * const list = new VirtualList(container, {
 *     itemHeight: 60,
 *     renderItem: (item, index) => `<div class="item">${item.name}</div>`
 * });
 * list.setItems(myLargeArray);
 */
class VirtualList {
    constructor(container, options = {}) {
        this.container = container;
        this.itemHeight = options.itemHeight || 60;
        this.renderItem = options.renderItem || ((item) => `<div>${item}</div>`);
        this.buffer = options.buffer || 2; // Extra items above/below viewport
        this.items = [];
        this.visibleStart = 0;
        this.visibleEnd = 0;

        this.container.style.overflow = 'auto';
        this.container.style.position = 'relative';

        // Create inner container for absolute positioning
        this.innerContainer = document.createElement('div');
        this.innerContainer.style.position = 'relative';
        this.container.appendChild(this.innerContainer);

        this.container.addEventListener('scroll', () => this.onScroll(), { passive: true });
    }

    setItems(items) {
        this.items = items;
        this.innerContainer.style.height = `${items.length * this.itemHeight}px`;
        this.render();
    }

    onScroll() {
        requestAnimationFrame(() => this.render());
    }

    render() {
        const scrollTop = this.container.scrollTop;
        const viewHeight = this.container.clientHeight;

        const start = Math.max(0, Math.floor(scrollTop / this.itemHeight) - this.buffer);
        const end = Math.min(
            start + Math.ceil(viewHeight / this.itemHeight) + (this.buffer * 2),
            this.items.length
        );

        // Skip if visible range unchanged
        if (start === this.visibleStart && end === this.visibleEnd) return;

        this.visibleStart = start;
        this.visibleEnd = end;

        // Render only visible items
        const fragment = document.createDocumentFragment();
        for (let i = start; i < end; i++) {
            const itemEl = document.createElement('div');
            itemEl.style.position = 'absolute';
            itemEl.style.top = `${i * this.itemHeight}px`;
            itemEl.style.width = '100%';
            itemEl.innerHTML = this.renderItem(this.items[i], i);
            fragment.appendChild(itemEl);
        }

        this.innerContainer.innerHTML = '';
        this.innerContainer.appendChild(fragment);
    }

    refresh() {
        this.visibleStart = -1;
        this.visibleEnd = -1;
        this.render();
    }

    destroy() {
        this.container.removeEventListener('scroll', this.onScroll);
        this.innerContainer.remove();
    }
}

// Export for use in step modules
window.VirtualList = VirtualList;
window.errorHandler = errorHandler;
window.StepRegistry = StepRegistry;
