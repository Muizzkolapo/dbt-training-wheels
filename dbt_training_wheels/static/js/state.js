/**
 * Centralized State Management for DBT Training Wheels
 *
 * This module provides a single source of truth for application state,
 * with backward compatibility for existing global variable usage.
 *
 * Usage:
 *   appState.get('currentQuery')
 *   appState.set('currentQuery', queryData)
 *   appState.subscribe('currentQuery', (newVal, oldVal) => console.log('Changed!'))
 */

// Keys that should always persist to sessionStorage
const PERSISTENT_STATE_KEYS = [
    'currentStep',
    'analysisResults',
    'modelConfigurations',
    'modelTags',
    'stepCompletionState',
    'queryConfiguration',  // New: Centralized query configuration
    'userMartSelection'
];

class AppState {
    constructor() {
        // Initialize with default values
        this._state = {
            // Core application state
            currentQuery: null,
            currentStep: null,  // Internal step ID (string, e.g., 'analyze', 'sources')
            analysisResults: null,
            generatedFiles: [],
            showingSql: false,
            modelConfigurations: {}, // Per-model config (materialization, schema, tags)

            // Tags-specific state
            modelTags: {},
            availableTags: [],
            allowCustomTags: true,

            // Step completion state tracking
            stepCompletionState: {
                0: { viewed: false },                    // Analyze SQL
                1: { sqlViewed: false },                 // Your Updated SQL
                2: { guidanceViewed: false },            // Internal Model References
                3: { guidanceViewed: false },            // Final Models
                4: { configurationsChanged: false },     // Materialization
                5: { configurationsChanged: false },     // Schema Configuration
                6: { configurationsChanged: false },     // Tags
                7: { sourcesYamlViewed: false },         // Define Sources
                8: { instructionsViewed: false },        // (unused - kept for compatibility)
                9: { lineageViewed: false },             // Review (with lineage)
                10: { deploymentReady: false }           // Deploy
            },

            // UI state
            viewingAllSteps: true,

            // User input state (collected from prerequisite checklist)
            userDomainName: sessionStorage.getItem('dbt_training_wheels_domain_name') || '',
            userDbtProjectPath: sessionStorage.getItem('dbt_training_wheels_dbt_project_path') || '',
            userGitHubBranch: sessionStorage.getItem('dbt_training_wheels_github_branch') || '',

            userMartSelection: (() => {
                const stored = sessionStorage.getItem('dbt_training_wheels_userMartSelection');
                if (!stored) return [];
                try {
                    const parsed = JSON.parse(stored);
                    return Array.isArray(parsed) ? parsed : [];
                } catch (error) {
                    console.warn('Failed to parse stored mart selection:', error);
                    return [];
                }
            })(),

            // GitHub integration state
            githubConfig: {
                enabled: false,
                repository: '',
                branch_prefix: 'dbt_training_wheels/',
                auth_method: null  // 'ssh' when configured
            },

            // Detected dbt projects (populated on startup)
            detectedDbtProjects: [],

            // Centralized query configuration (from backend)
            // This is the single source of truth for naming, model configs, etc.
            queryConfiguration: null,

            // The conversion being worked on: one uploaded folder, with its domain
            // queries in deploy order. The unit of work.
            currentConversion: null
        };

        // Subscribers map: key -> [callbacks]
        this._subscribers = new Map();

        // Debounce timer for backend sync
        this._syncDebounceTimer = null;
    }

    /**
     * Get a state value by key
     * @param {string} key - State key
     * @returns {*} State value
     */
    get(key) {
        return this._state[key];
    }

    /**
     * Set a state value and notify subscribers
     * @param {string} key - State key
     * @param {*} value - New value
     * @param {Object} options - Options: { session: boolean } to persist to sessionStorage
     */
    set(key, value, options = {}) {
        const oldValue = this._state[key];
        this._state[key] = value;

        // Auto-persist critical state keys or explicitly requested
        if (options.session || PERSISTENT_STATE_KEYS.includes(key)) {
            try {
                const serialized = JSON.stringify(value);

                // Check if storage is getting full (warn at 4MB threshold)
                const totalSize = new Blob([serialized]).size;
                if (totalSize > 4 * 1024 * 1024) {
                    console.warn(`Large state object for ${key}: ${(totalSize/1024/1024).toFixed(2)}MB`);
                }

                sessionStorage.setItem(`dbt_training_wheels_${key}`, serialized);
            } catch (e) {
                if (e.name === 'QuotaExceededError') {
                    console.error(`SessionStorage quota exceeded when saving ${key}`);
                    // Try to clear old data and retry
                    this._clearOldestSession();
                    try {
                        sessionStorage.setItem(`dbt_training_wheels_${key}`, JSON.stringify(value));
                    } catch (retryError) {
                        console.error(`Failed to save ${key} even after cleanup`);
                        // Show user-friendly error
                        if (window.errorHandler) {
                            errorHandler.showError({
                                error: {
                                    user_message: 'Storage space full',
                                    beginner_help: 'Your browser\'s storage is full. Your progress is still saved, but new changes may not persist.',
                                    common_fixes: [
                                        'Clear your browser cache and cookies',
                                        'Close other tabs using this application',
                                        'Download your configuration before continuing'
                                    ]
                                }
                            });
                        }
                    }
                } else {
                    console.warn(`Failed to persist ${key} to sessionStorage:`, e);
                }
            }
        }

        // Notify subscribers
        this._notify(key, value, oldValue);
    }

    /**
     * Async version of set with error handling and retry logic
     * Use for critical state updates that need guaranteed persistence
     * @param {string} key - State key
     * @param {*} value - New value
     * @param {Object} options - Options: { session: boolean, retries?: number }
     * @returns {Promise<boolean>} True if successful
     */
    async setAsync(key, value, options = {}) {
        const retries = options.retries || 3;
        let lastError = null;

        for (let attempt = 1; attempt <= retries; attempt++) {
            try {
                // Perform the synchronous state update
                this.set(key, value, options);

                // Verify the value was persisted if session storage was requested
                if (options.session || PERSISTENT_STATE_KEYS.includes(key)) {
                    const stored = sessionStorage.getItem(`dbt_training_wheels_${key}`);
                    if (stored) {
                        const parsed = JSON.parse(stored);
                        // Basic equality check for primitives, reference check otherwise
                        if (typeof value === 'object' && value !== null) {
                            if (JSON.stringify(parsed) !== JSON.stringify(value)) {
                                throw new Error('Stored value mismatch');
                            }
                        } else if (parsed !== value) {
                            throw new Error('Stored value mismatch');
                        }
                    }
                }

                return true;
            } catch (error) {
                lastError = error;
                console.warn(`setAsync attempt ${attempt}/${retries} failed for ${key}:`, error);

                if (attempt < retries) {
                    // Exponential backoff: 100ms, 200ms, 400ms...
                    await new Promise(resolve => setTimeout(resolve, 100 * Math.pow(2, attempt - 1)));
                }
            }
        }

        console.error(`setAsync failed after ${retries} attempts for ${key}:`, lastError);
        return false;
    }

    /**
     * Subscribe to changes on a state key
     * @param {string} key - State key to watch
     * @param {Function} callback - Called with (newValue, oldValue, key)
     * @returns {Function} Unsubscribe function
     */
    subscribe(key, callback) {
        if (!this._subscribers.has(key)) {
            this._subscribers.set(key, []);
        }
        this._subscribers.get(key).push(callback);

        // Return unsubscribe function
        return () => {
            const subs = this._subscribers.get(key);
            const idx = subs.indexOf(callback);
            if (idx > -1) subs.splice(idx, 1);
        };
    }

    /**
     * Notify all subscribers of a key change
     * @private
     */
    _notify(key, newValue, oldValue) {
        const subscribers = this._subscribers.get(key) || [];
        subscribers.forEach(callback => {
            try {
                callback(newValue, oldValue, key);
            } catch (e) {
                console.error(`Error in state subscriber for ${key}:`, e);
            }
        });
    }

    /**
     * Reset state to initial values
     * @param {string[]} keys - Optional array of keys to reset. If empty, resets all.
     */
    reset(keys = []) {
        const keysToReset = keys.length > 0 ? keys : Object.keys(this._state);
        keysToReset.forEach(key => {
            // Reset to initial value based on type
            const current = this._state[key];
            if (Array.isArray(current)) {
                this.set(key, []);
            } else if (typeof current === 'object' && current !== null) {
                this.set(key, {});
            } else if (typeof current === 'boolean') {
                this.set(key, false);
            } else {
                this.set(key, null);
            }
        });
    }

    /**
     * Get all state (for debugging)
     * @returns {Object} Copy of state
     */
    getAll() {
        return { ...this._state };
    }

    /**
     * Restore all persistent state from sessionStorage
     * Call this on page load
     */
    restoreFromSession() {
        console.log('Restoring state from sessionStorage...');
        PERSISTENT_STATE_KEYS.forEach(key => {
            try {
                const stored = sessionStorage.getItem(`dbt_training_wheels_${key}`);
                if (stored) {
                    const value = JSON.parse(stored);
                    this._state[key] = value;
                    console.log(`Restored ${key} from session`);
                    // Notify subscribers
                    this._notify(key, value, undefined);
                }
            } catch (e) {
                console.warn(`Failed to restore ${key} from session:`, e);
                // Clear corrupted data
                sessionStorage.removeItem(`dbt_training_wheels_${key}`);
            }
        });
    }

    /**
     * Validate restored state for consistency
     * @returns {boolean} True if state is valid
     */
    validateRestoredState() {
        // Check if we have a query ID but no query object
        const queryId = this.get('selectedQueryId') || sessionStorage.getItem('selectedQueryId');
        if (queryId && !this.get('currentQuery')) {
            console.warn('Query ID exists but currentQuery is null - state may need re-initialization');
            // Don't clear - let selectQuery handle restoration
            return false;
        }

        // Check if we have analysisResults but no current step
        if (this.get('analysisResults') && !this.get('currentStep')) {
            console.warn('Have analysis results but no current step - resetting to first step');
            // Note: StepRegistry might not be initialized yet, so we'll set a default
            this.set('currentStep', 'analyze');
        }

        return true;
    }

    /**
     * Clear specific keys from session storage
     * @param {string[]} keys - Keys to clear. If empty, clears all persistent keys
     */
    clearSession(keys = []) {
        const toClear = keys.length > 0 ? keys : PERSISTENT_STATE_KEYS;
        console.log('Clearing session storage for:', toClear);
        toClear.forEach(key => {
            try {
                sessionStorage.removeItem(`dbt_training_wheels_${key}`);
                // Reset to default value
                if (Array.isArray(this._state[key])) {
                    this._state[key] = [];
                } else if (typeof this._state[key] === 'object' && this._state[key] !== null) {
                    this._state[key] = {};
                } else {
                    this._state[key] = null;
                }
            } catch (e) {
                console.warn(`Failed to clear ${key}:`, e);
            }
        });
    }

    /**
     * Clear oldest session data to free up space
     * @private
     */
    _clearOldestSession() {
        try {
            // Remove non-critical keys first
            const nonCritical = ['viewingAllSteps', 'showingSql', 'availableTags'];
            nonCritical.forEach(key => {
                sessionStorage.removeItem(`dbt_training_wheels_${key}`);
            });
            console.log('Cleared non-critical session data to free space');
        } catch (e) {
            console.warn('Failed to clear old session data:', e);
        }
    }

    // ============================================
    // QUERY CONFIGURATION SYNC METHODS
    // ============================================

    /**
     * Load QueryConfiguration from backend
     * @param {number} queryId - The query ID
     * @returns {Promise<Object|null>} The QueryConfiguration or null
     */
    async loadQueryConfig(queryId) {
        try {
            const response = await fetch(`/api/query-config/${queryId}`);
            if (response.ok) {
                const config = await response.json();
                this.set('queryConfiguration', config);
                console.log(`Loaded QueryConfiguration for query ${queryId}`);

                // Sync legacy state for backward compatibility
                if (config.analysis_results) {
                    this.set('analysisResults', config.analysis_results);
                }
                if (config.model_configurations) {
                    this._syncModelConfigsFromQueryConfig(config);
                }

                return config;
            } else if (response.status === 404) {
                // Config doesn't exist yet - that's OK
                console.log(`No QueryConfiguration found for query ${queryId}`);
                return null;
            } else {
                console.error(`Failed to load QueryConfiguration: ${response.status}`);
                return null;
            }
        } catch (e) {
            console.error('Failed to load QueryConfiguration:', e);
            return null;
        }
    }

    /**
     * Create or reset QueryConfiguration on backend
     * @param {number} queryId - The query ID
     * @param {string} projectName - Selected project/domain name
     * @returns {Promise<Object|null>} The created QueryConfiguration or null
     */
    async createQueryConfig(queryId, projectName = null) {
        try {
            const response = await fetch(`/api/query-config/${queryId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    project_name: projectName,
                    // The folder the query was uploaded from, not something typed
                    domain_area: domainFromFilename(this.get('currentQuery')?.filename),
                    model_group: conversionNameFromFilename(this.get('currentQuery')?.filename),
                    github_branch: this.get('userGitHubBranch'),
                    dbt_project_path: this.get('userDbtProjectPath')
                })
            });

            if (response.ok) {
                const config = await response.json();
                this.set('queryConfiguration', config);
                console.log(`Created QueryConfiguration for query ${queryId}`);

                // Sync legacy state
                if (config.analysis_results) {
                    this.set('analysisResults', config.analysis_results);
                }
                if (config.model_configurations) {
                    this._syncModelConfigsFromQueryConfig(config);
                }

                return config;
            } else {
                const error = await response.json();
                console.error('Failed to create QueryConfiguration:', error);
                return null;
            }
        } catch (e) {
            console.error('Failed to create QueryConfiguration:', e);
            return null;
        }
    }

    /**
     * Update step-specific configuration on backend (debounced)
     * @param {number} queryId - The query ID
     * @param {string} stepId - Step identifier (e.g., 'materialization', 'schema', 'tags')
     * @param {Object} data - Data to update
     * @returns {Promise<Object|null>} Updated QueryConfiguration or null
     */
    async updateStepConfig(queryId, stepId, data) {
        // Clear any pending sync
        if (this._syncDebounceTimer) {
            clearTimeout(this._syncDebounceTimer);
        }

        // Debounce the sync (300ms)
        return new Promise((resolve) => {
            this._syncDebounceTimer = setTimeout(async () => {
                try {
                    const response = await fetch(`/api/query-config/${queryId}/step/${stepId}`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data)
                    });

                    if (response.ok) {
                        const config = await response.json();
                        this.set('queryConfiguration', config);
                        console.log(`Updated QueryConfiguration step: ${stepId}`);

                        // Sync legacy state
                        if (config.model_configurations) {
                            this._syncModelConfigsFromQueryConfig(config);
                        }

                        resolve(config);
                    } else {
                        const error = await response.json();
                        console.error(`Failed to update step ${stepId}:`, error);
                        resolve(null);
                    }
                } catch (e) {
                    console.error(`Failed to update step ${stepId}:`, e);
                    resolve(null);
                }
            }, 300);
        });
    }

    /**
     * Update step-specific configuration immediately (no debounce)
     * Use this for navigation events where we need immediate persistence
     * @param {number} queryId - The query ID
     * @param {string} stepId - Step identifier
     * @param {Object} data - Data to update
     * @returns {Promise<Object|null>} Updated QueryConfiguration or null
     */
    async updateStepConfigImmediate(queryId, stepId, data) {
        // Clear any pending debounced sync
        if (this._syncDebounceTimer) {
            clearTimeout(this._syncDebounceTimer);
            this._syncDebounceTimer = null;
        }

        try {
            const response = await fetch(`/api/query-config/${queryId}/step/${stepId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });

            if (response.ok) {
                const config = await response.json();
                this.set('queryConfiguration', config);
                console.log(`Updated QueryConfiguration step (immediate): ${stepId}`);

                // Sync legacy state
                if (config.model_configurations) {
                    this._syncModelConfigsFromQueryConfig(config);
                }

                return config;
            } else {
                const error = await response.json();
                console.error(`Failed to update step ${stepId}:`, error);
                return null;
            }
        } catch (e) {
            console.error(`Failed to update step ${stepId}:`, e);
            return null;
        }
    }

    /**
     * Sync modelConfigurations from QueryConfiguration to legacy format
     * @private
     */
    _syncModelConfigsFromQueryConfig(queryConfig) {
        const modelConfigs = {};
        if (queryConfig.model_configurations) {
            queryConfig.model_configurations.forEach((mc, idx) => {
                modelConfigs[idx] = {
                    table: mc.table,
                    type: mc.model_type,
                    materialization: mc.materialization,
                    schema: mc.schema,
                    tags: mc.tags || []
                };
            });
        }
        this.set('modelConfigurations', modelConfigs);
    }

    /**
     * Get naming configuration from QueryConfiguration
     * @returns {Object|null} Naming configuration or null
     */
    getNamingConfig() {
        const queryConfig = this.get('queryConfiguration');
        return queryConfig?.naming || null;
    }

    /**
     * Get a specific naming prefix
     * @param {string} type - Prefix type (mart, intermediate)
     * @returns {string} The prefix or empty string
     */
    getNamingPrefix(type) {
        const naming = this.getNamingConfig();
        if (!naming) {
            // Fall back to global config
            return window.orgConfig?.naming?.[`${type}_model_prefix`] || '';
        }

        switch (type) {
            case 'mart': return naming.mart_model_prefix || '';
            case 'intermediate': return naming.intermediate_model_prefix || '';
            default: return '';
        }
    }
}

// Create singleton instance
const appState = new AppState();

// ============================================
// BACKWARD COMPATIBILITY LAYER
// ============================================
// These property definitions allow existing code that uses
// global variables (e.g., `currentQuery = data`) to continue
// working while actually reading/writing through appState.
//
// This ensures ZERO REGRESSIONS during conversion.

const stateKeys = [
    'currentQuery',
    'currentStep',
    'analysisResults',
    'generatedFiles',
    'showingSql',
    'modelConfigurations',
    'modelTags',
    'availableTags',
    'allowCustomTags',
    'stepCompletionState',
    'viewingAllSteps',
    'userDomainName',
    'userDbtProjectPath',
    'userGitHubBranch',
    'userMartSelection',
    'githubConfig',
    'detectedDbtProjects',
    'queryConfiguration',  // New: Centralized query configuration
    'currentConversion'
];

stateKeys.forEach(key => {
    Object.defineProperty(window, key, {
        get: () => appState.get(key),
        set: (value) => appState.set(key, value),
        configurable: true,
        enumerable: true
    });
});

// Make appState available globally
window.appState = appState;
