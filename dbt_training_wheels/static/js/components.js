// ============================================
// REUSABLE UI COMPONENTS
// ============================================
// Components that can be easily used across any step or page
// Usage: Just call the render function with your data

// ============================================
// CHECKLIST COMPONENT
// ============================================
// A flexible, interactive checklist component
//
// Usage Examples:
//
// 1. Simple static checklist:
//    Checklist.render('my-container', {
//        title: 'Requirements',
//        items: [
//            { id: 'item1', text: 'First item', completed: true },
//            { id: 'item2', text: 'Second item', completed: false }
//        ]
//    });
//
// 2. Interactive checklist with callbacks:
//    Checklist.render('my-container', {
//        title: 'Setup Tasks',
//        items: [...],
//        interactive: true,
//        onChange: (itemId, isChecked, allItems) => {
//            console.log(`${itemId} is now ${isChecked}`);
//        },
//        onComplete: (allItems) => {
//            console.log('All items completed!');
//        }
//    });
//
// 3. Minimal checklist (no title, compact):
//    Checklist.render('my-container', {
//        items: [...],
//        compact: true
//    });

const Checklist = {
    // Store state for each checklist instance
    _instances: {},

    /**
     * Render a checklist into a container
     * @param {string} containerId - ID of the container element (without #)
     * @param {Object} options - Configuration options
     * @param {string} [options.title] - Title for the checklist
     * @param {string} [options.subtitle] - Subtitle/description
     * @param {Array} options.items - Array of {id, text, completed, description?}
     * @param {boolean} [options.interactive=false] - Allow clicking to toggle items
     * @param {boolean} [options.compact=false] - Use compact styling
     * @param {boolean} [options.showProgress=true] - Show "X of Y" progress
     * @param {Function} [options.onChange] - Callback when item toggled: (itemId, isChecked, allItems)
     * @param {Function} [options.onComplete] - Callback when all items completed
     * @param {string} [options.emptyMessage] - Message when no items
     * @returns {Object} Instance with update methods
     */
    render(containerId, options) {
        const container = document.getElementById(containerId);
        if (!container) {
            console.warn(`Checklist: Container #${containerId} not found`);
            return null;
        }

        // Store instance state
        const instanceId = containerId;
        this._instances[instanceId] = {
            options: { ...options },
            items: [...(options.items || [])]
        };

        // Render the checklist
        container.innerHTML = this._generateHTML(instanceId);

        // Attach event listeners if interactive
        if (options.interactive) {
            this._attachEventListeners(instanceId, container);
        }

        // Return instance methods
        return {
            update: (newItems) => this.updateItems(instanceId, newItems),
            getItems: () => this.getItems(instanceId),
            isComplete: () => this.isComplete(instanceId),
            destroy: () => this.destroy(instanceId)
        };
    },

    /**
     * Generate HTML for the checklist
     */
    _generateHTML(instanceId) {
        const instance = this._instances[instanceId];
        if (!instance) return '';

        const { options, items } = instance;
        const {
            title,
            subtitle,
            interactive = false,
            compact = false,
            showProgress = true,
            emptyMessage = 'No items'
        } = options;

        const completedCount = items.filter(i => i.completed).length;
        const totalCount = items.length;
        const isAllComplete = completedCount === totalCount && totalCount > 0;

        if (items.length === 0) {
            return `
                <div class="dbt-checklist dbt-checklist-empty">
                    <p class="text-sm text-[#666666]">${emptyMessage}</p>
                </div>
            `;
        }

        const containerClass = compact ? 'dbt-checklist dbt-checklist-compact' : 'dbt-checklist';
        const cursorClass = interactive ? 'cursor-pointer hover:bg-[#f9f9f9]' : '';

        return `
            <div class="${containerClass}" data-checklist-id="${instanceId}">
                ${title ? `
                    <div class="dbt-checklist-header">
                        <div class="flex items-center gap-2">
                            <svg class="w-4 h-4 ${isAllComplete ? 'text-[#4f46e5]' : 'text-[#1f2937]'}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"></path>
                            </svg>
                            <h4 class="dbt-checklist-title">${title}</h4>
                        </div>
                        ${showProgress ? `
                            <span class="dbt-checklist-progress ${isAllComplete ? 'complete' : ''}">
                                ${completedCount} of ${totalCount}
                            </span>
                        ` : ''}
                    </div>
                ` : ''}
                ${subtitle ? `<p class="dbt-checklist-subtitle">${subtitle}</p>` : ''}
                <ul class="dbt-checklist-items">
                    ${items.map((item, idx) => `
                        <li class="dbt-checklist-item ${item.completed ? 'completed' : ''} ${cursorClass}"
                            data-item-id="${item.id}"
                            data-item-idx="${idx}"
                            ${interactive ? `onclick="Checklist._handleClick('${instanceId}', '${item.id}')"` : ''}>
                            <div class="dbt-checklist-checkbox">
                                ${item.completed ?
                                    `<svg class="w-5 h-5 text-[#4f46e5]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                                    </svg>`
                                    :
                                    `<svg class="w-5 h-5 text-[#cccccc]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <circle cx="12" cy="12" r="10" stroke-width="2"></circle>
                                    </svg>`
                                }
                            </div>
                            <div class="dbt-checklist-content">
                                <span class="dbt-checklist-text">${item.text}</span>
                                ${item.description ? `<p class="dbt-checklist-description">${item.description}</p>` : ''}
                            </div>
                        </li>
                    `).join('')}
                </ul>
                ${isAllComplete ? `
                    <div class="dbt-checklist-complete-banner">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
                        </svg>
                        All items completed!
                    </div>
                ` : ''}
            </div>
        `;
    },

    /**
     * Handle click on interactive checklist item
     */
    _handleClick(instanceId, itemId) {
        const instance = this._instances[instanceId];
        if (!instance) return;

        // Toggle the item
        const item = instance.items.find(i => i.id === itemId);
        if (item) {
            item.completed = !item.completed;
        }

        // Re-render
        const container = document.getElementById(instanceId);
        if (container) {
            container.innerHTML = this._generateHTML(instanceId);
        }

        // Call onChange callback
        if (instance.options.onChange) {
            instance.options.onChange(itemId, item?.completed, [...instance.items]);
        }

        // Check if all complete and call onComplete
        if (this.isComplete(instanceId) && instance.options.onComplete) {
            instance.options.onComplete([...instance.items]);
        }
    },

    /**
     * Attach event listeners (for future extensibility)
     */
    _attachEventListeners(instanceId, container) {
        // Event listeners are attached inline via onclick for simplicity
        // This method is a hook for future keyboard navigation, etc.
    },

    /**
     * Update items in an existing checklist
     */
    updateItems(instanceId, newItems) {
        const instance = this._instances[instanceId];
        if (!instance) return;

        instance.items = [...newItems];

        const container = document.getElementById(instanceId);
        if (container) {
            container.innerHTML = this._generateHTML(instanceId);
        }
    },

    /**
     * Get current items state
     */
    getItems(instanceId) {
        const instance = this._instances[instanceId];
        return instance ? [...instance.items] : [];
    },

    /**
     * Check if all items are complete
     */
    isComplete(instanceId) {
        const instance = this._instances[instanceId];
        if (!instance || instance.items.length === 0) return false;
        return instance.items.every(i => i.completed);
    },

    /**
     * Destroy a checklist instance
     */
    destroy(instanceId) {
        delete this._instances[instanceId];
        const container = document.getElementById(instanceId);
        if (container) {
            container.innerHTML = '';
        }
    },

    /**
     * Create a simple inline checklist (returns HTML string)
     * Use when you just need the HTML without instance management
     */
    createHTML(items, options = {}) {
        const tempId = `temp-${Date.now()}`;
        this._instances[tempId] = {
            options: { showProgress: true, ...options },
            items: [...items]
        };
        const html = this._generateHTML(tempId);
        delete this._instances[tempId];
        return html;
    }
};


// ============================================
// PROGRESS INDICATOR COMPONENT
// ============================================
// Shows progress through a series of steps
//
// Usage:
//    ProgressIndicator.render('container-id', {
//        steps: ['Step 1', 'Step 2', 'Step 3'],
//        currentStep: 1,  // 0-indexed
//        completedSteps: [0]  // Array of completed step indices
//    });

const ProgressIndicator = {
    render(containerId, options) {
        const container = document.getElementById(containerId);
        if (!container) return;

        const { steps = [], currentStep = 0, completedSteps = [] } = options;

        container.innerHTML = `
            <div class="dbt-progress-indicator">
                ${steps.map((step, idx) => {
                    const isCompleted = completedSteps.includes(idx);
                    const isCurrent = idx === currentStep;
                    const isPending = !isCompleted && !isCurrent;

                    return `
                        <div class="dbt-progress-step ${isCompleted ? 'completed' : ''} ${isCurrent ? 'current' : ''} ${isPending ? 'pending' : ''}">
                            <div class="dbt-progress-dot">
                                ${isCompleted ?
                                    `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
                                    </svg>` :
                                    `<span>${idx + 1}</span>`
                                }
                            </div>
                            <span class="dbt-progress-label">${step}</span>
                            ${idx < steps.length - 1 ? '<div class="dbt-progress-line"></div>' : ''}
                        </div>
                    `;
                }).join('')}
            </div>
        `;
    }
};


// ============================================
// ALERT/BANNER COMPONENT
// ============================================
// Display info, warning, success, or error banners
//
// Usage:
//    Alert.render('container-id', {
//        type: 'success',  // 'info', 'warning', 'success', 'error'
//        title: 'Success!',
//        message: 'Your changes have been saved.',
//        dismissible: true
//    });

const Alert = {
    render(containerId, options) {
        const container = document.getElementById(containerId);
        if (!container) return;

        const {
            type = 'info',
            title,
            message,
            dismissible = false
        } = options;

        const typeConfig = {
            info: { bg: 'bg-blue-50', border: 'border-blue-200', text: 'text-blue-800', icon: 'M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z' },
            success: { bg: 'bg-green-50', border: 'border-green-200', text: 'text-green-800', icon: 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z' },
            warning: { bg: 'bg-yellow-50', border: 'border-yellow-200', text: 'text-yellow-800', icon: 'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z' },
            error: { bg: 'bg-red-50', border: 'border-red-200', text: 'text-red-800', icon: 'M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z' }
        };

        const config = typeConfig[type] || typeConfig.info;
        const alertId = `alert-${Date.now()}`;

        container.innerHTML = `
            <div id="${alertId}" class="dbt-alert ${config.bg} ${config.border} ${config.text}">
                <div class="flex items-start gap-3">
                    <svg class="w-5 h-5 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="${config.icon}"></path>
                    </svg>
                    <div class="flex-1">
                        ${title ? `<h4 class="font-medium">${title}</h4>` : ''}
                        ${message ? `<p class="text-sm ${title ? 'mt-1' : ''}">${message}</p>` : ''}
                    </div>
                    ${dismissible ? `
                        <button onclick="document.getElementById('${alertId}').remove()" class="text-current opacity-70 hover:opacity-100">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
                            </svg>
                        </button>
                    ` : ''}
                </div>
            </div>
        `;
    },

    // Show a temporary toast-style alert
    toast(message, type = 'info', duration = 3000) {
        const toastId = `toast-${Date.now()}`;
        const toast = document.createElement('div');
        toast.id = toastId;
        toast.className = 'dbt-toast fixed bottom-4 right-4 z-50';
        document.body.appendChild(toast);

        this.render(toastId, { type, message, dismissible: true });

        if (duration > 0) {
            setTimeout(() => {
                const el = document.getElementById(toastId);
                if (el) el.remove();
            }, duration);
        }
    }
};
