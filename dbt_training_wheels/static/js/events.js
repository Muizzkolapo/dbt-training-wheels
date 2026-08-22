/**
 * Event Delegation System for DBT Training Wheels
 *
 * This module provides centralized event handling using event delegation,
 * replacing inline onclick handlers with data-action attributes.
 *
 * Usage in HTML:
 *   <button data-action="goToNextStep">Next</button>
 *   <button data-action="toggleStepHelp" data-step-id="5">Help</button>
 *   <input data-action-enter="addCustomTag" data-model-idx="0">
 *   <select data-action-change="updateMaterialization" data-model-idx="0">
 */

const EventManager = {
    // Track cleanup functions for managed listeners
    _cleanupFns: [],

    // Action handlers registry
    _handlers: {},

    /**
     * Initialize the event delegation system
     */
    init() {
        // Click events
        document.addEventListener('click', (e) => this._handleClick(e));

        // Keypress events (for Enter key actions)
        document.addEventListener('keypress', (e) => this._handleKeypress(e));

        // Change events (for select/input changes)
        document.addEventListener('change', (e) => this._handleChange(e));

        // Input events (for real-time validation)
        document.addEventListener('input', (e) => this._handleInput(e));

        console.log('EventManager initialized');
    },

    /**
     * Handle delegated click events
     * @private
     */
    _handleClick(e) {
        const target = e.target.closest('[data-action]');
        if (!target) return;

        const action = target.dataset.action;
        const params = this._extractParams(target.dataset);

        this._execute(action, params, e, target);
    },

    /**
     * Handle delegated keypress events (Enter key)
     * @private
     */
    _handleKeypress(e) {
        if (e.key !== 'Enter') return;

        const target = e.target.closest('[data-action-enter]');
        if (!target) return;

        e.preventDefault();
        const action = target.dataset.actionEnter;
        const params = this._extractParams(target.dataset);

        this._execute(action, params, e, target);
    },

    /**
     * Handle delegated change events
     * @private
     */
    _handleChange(e) {
        const target = e.target.closest('[data-action-change]');
        if (!target) return;

        const action = target.dataset.actionChange;
        const params = this._extractParams(target.dataset);
        params.value = target.value;

        this._execute(action, params, e, target);
    },

    /**
     * Handle delegated input events
     * @private
     */
    _handleInput(e) {
        const target = e.target.closest('[data-action-input]');
        if (!target) return;

        const action = target.dataset.actionInput;
        const params = this._extractParams(target.dataset);
        params.value = target.value;

        this._execute(action, params, e, target);
    },

    /**
     * Extract parameters from dataset, excluding action-related keys
     * @private
     */
    _extractParams(dataset) {
        const params = {};
        for (const [key, value] of Object.entries(dataset)) {
            // Skip action keys
            if (key === 'action' || key === 'actionEnter' || key === 'actionChange' || key === 'actionInput') {
                continue;
            }
            // Convert camelCase to more usable format
            params[key] = value;
        }
        return params;
    },

    /**
     * Execute an action handler
     * @private
     */
    _execute(action, params, event, target) {
        // Built-in action handlers
        const handlers = {
            // Navigation - pass button element for loading state
            'goToNextStep': async () => await goToNextStep(target),
            'goToPrevStep': async () => await goToPrevStep(target),
            'setActiveStep': async () => await setActiveStep(parseInt(params.stepId)),
            'saveAndContinue': async () => await saveAndContinue(getNextStepId(), target),

            // Help toggles
            'toggleStepHelp': () => toggleStepHelp(parseInt(params.stepId)),
            'toggleBeginnerHelp': () => toggleBeginnerHelp(),
            'toggleStep3Help': () => toggleStep3Help(),
            'toggleSourcesHelp': () => toggleSourcesHelp(),
            'toggleDeployHelp': () => toggleDeployHelp(),
            'toggleYamlExplainer': () => toggleYamlExplainer(),
            'toggleAdvancedSources': () => toggleAdvancedSources(),
            'toggleModelConfigSummary': () => toggleModelConfigSummary(),

            // Analysis
            'analyzeQuery': () => analyzeQuery(),

            // SQL actions
            'toggleSqlBlock': () => toggleSqlBlockById(params.blockId, params.chevronId),
            'copyToClipboard': () => copyToClipboard(params.elementId, params.buttonId),
            'copyDagYamlToClipboard': () => copyDagYamlToClipboard(),

            // Tags
            'toggleTag': () => toggleTagStep5(parseInt(params.modelIdx), params.tag),
            'removeTag': () => removeTagStep5(parseInt(params.modelIdx), params.tag),
            'addCustomTag': () => addCustomTagStep5(parseInt(params.modelIdx)),

            // Deploy actions
            'writeToDbtProject': (e) => writeToDbtProject(e),
            'pushToGitHub': (e) => pushToGitHub(e),
            'pushDagToGitHub': (e) => pushDagToGitHub(e),
            'reloadPage': () => location.reload(),

            // Tour actions
            'skipTour': () => skipTour(),
            'nextTourStep': () => nextTourStep(),
            'previousTourStep': () => previousTourStep(),
            'endTour': () => endTour(),

            // UI dismissal
            'dismiss': () => target.closest('[data-dismissible]')?.remove(),
            'removeParent': () => target.parentElement?.remove(),
            'removeGrandparent': () => target.parentElement?.parentElement?.remove(),

            // Modal actions
            'closeFullScreenSql': () => closeFullScreenSql(),
            'toggleSqlTooltip': () => toggleSqlTooltip(params.tooltipId),

            // Prerequisite modal
            'completePrerequisiteModal': () => completePrerequisiteModal(params.modalId),

            // Lineage
            'closeLineagePopup': () => target.parentElement?.remove()
        };

        // Check for registered handler first
        if (this._handlers[action]) {
            try {
                this._handlers[action](params, event, target);
            } catch (err) {
                console.error(`Error executing registered handler '${action}':`, err);
            }
            return;
        }

        // Check built-in handlers
        if (handlers[action]) {
            try {
                handlers[action](event);
            } catch (err) {
                console.error(`Error executing action '${action}':`, err);
            }
        } else {
            console.warn(`Unknown action: ${action}`, params);
        }
    },

    /**
     * Register a custom action handler
     * @param {string} action - Action name
     * @param {Function} handler - Handler function (params, event, target)
     */
    register(action, handler) {
        this._handlers[action] = handler;
    },

    /**
     * Add an event listener with automatic cleanup tracking
     * @param {Element} element - DOM element
     * @param {string} event - Event type
     * @param {Function} handler - Event handler
     * @returns {Function} Unsubscribe function
     */
    on(element, event, handler) {
        element.addEventListener(event, handler);
        const cleanup = () => element.removeEventListener(event, handler);
        this._cleanupFns.push(cleanup);
        return cleanup;
    },

    /**
     * Clean up all managed event listeners
     */
    cleanup() {
        this._cleanupFns.forEach(fn => {
            try {
                fn();
            } catch (e) {
                console.warn('Error during event cleanup:', e);
            }
        });
        this._cleanupFns = [];
    }
};

// Initialize on DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => EventManager.init());

// Cleanup on page unload
window.addEventListener('beforeunload', () => EventManager.cleanup());

// Make available globally
window.EventManager = EventManager;
