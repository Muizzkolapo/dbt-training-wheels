// ============================================
// STEP: CROSS-PROJECT REFERENCES
// ============================================
// Detects tables that belong to other dbt projects
// and allows users to choose between cross-project ref() and source()
// ============================================

// State for cross-project refs
let crossProjectRefsState = {
    enabled: false,
    crossProjectRefs: [],
    sources: [],
    decisions: {},
    loaded: false,
    syncing: false  // Track if we're currently syncing to backend
};

async function renderCrossProjectRefs(container) {
    if (!analysisResults || !currentQuery) {
        container.innerHTML = '<div class="dbt-page-card"><p class="dbt-hint">Please complete the analysis first.</p></div>';
        return;
    }

    // Show loading state
    container.innerHTML = `
        <div class="dbt-page-card">
            <div class="dbt-page-header">
                <h3 class="dbt-page-title">Cross-Project References</h3>
                <p class="dbt-page-subtitle">Checking for references to other dbt projects...</p>
            </div>
            <div class="dbt-empty-state">
                <div class="dbt-spinner"></div>
                <p class="dbt-hint">Analyzing table references...</p>
            </div>
        </div>
    `;

    try {
        // First check if feature is enabled
        const statusResponse = await errorHandler.safeFetch('/api/cross-project-refs/status');
        crossProjectRefsState.enabled = statusResponse.enabled;

        if (!statusResponse.enabled) {
            // Feature disabled - show info and allow skip
            renderCrossProjectRefsDisabled(container);
            return;
        }

        // Feature enabled - detect cross-project refs
        const detectResponse = await errorHandler.safeFetch(`/api/cross-project-refs/${currentQuery.id}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({})
        });

        crossProjectRefsState.crossProjectRefs = detectResponse.cross_project_refs || [];
        crossProjectRefsState.sources = detectResponse.sources || [];
        crossProjectRefsState.loaded = true;

        // Initialize decisions from detected refs (default to using cross-ref)
        crossProjectRefsState.crossProjectRefs.forEach(ref => {
            crossProjectRefsState.decisions[ref.original_reference] = {
                original_reference: ref.original_reference,
                use_cross_ref: ref.use_cross_ref !== false,
                project: ref.project,
                model: ref.model,
                dataset: ref.dataset,
                table: ref.table,
                suggested_source: ref.suggested_source
            };
        });


        // Sync initial decisions to QueryConfiguration (real-time sync)
        await syncCrossProjectDecisionsToBackend();

        // If no cross-project refs found, auto-skip or show info
        if (crossProjectRefsState.crossProjectRefs.length === 0) {
            renderNoCrossProjectRefs(container);
            return;
        }

        // Render the full UI
        renderCrossProjectRefsUI(container);

    } catch (error) {
        console.error('Error detecting cross-project refs:', error);
        container.innerHTML = `
            <div class="dbt-page-card">
                <div class="dbt-page-header">
                    <h3 class="dbt-page-title">Cross-Project References</h3>
                </div>
                <div class="dbt-callout dbt-callout-error">
                    <p>Failed to detect cross-project references. You can skip this step.</p>
                    <p class="dbt-hint mt-2">${error.message || 'Unknown error'}</p>
                </div>
                ${renderNavFooter({ saveBeforeNav: true, nextLabel: 'Skip & Continue' })}
            </div>
        `;
    }
}

function renderCrossProjectRefsDisabled(container) {
    container.innerHTML = `
        <div class="dbt-page-card">
            <div class="dbt-page-header">
                <h3 class="dbt-page-title">Cross-Project References</h3>
                <p class="dbt-page-subtitle">Detect references to models in other dbt projects</p>
            </div>

            <div class="dbt-callout dbt-callout-primary">
                <div style="display: flex; align-items: flex-start; gap: 0.75rem;">
                    <svg class="w-5 h-5" style="flex-shrink: 0; color: var(--brand-blue); margin-top: 2px;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                    </svg>
                    <div>
                        <h4 style="font-weight: 600; margin: 0 0 0.5rem 0;">Feature Not Configured</h4>
                        <p style="margin: 0; font-size: 0.875rem;">
                            Cross-project reference detection is not enabled in your configuration.
                            All table references will be treated as sources.
                        </p>
                        <p class="dbt-hint mt-2">
                            To enable, add <code class="dbt-code-inline">cross_project_refs</code> section to your dbt_training_wheels_config.yaml.
                        </p>
                    </div>
                </div>
            </div>

            ${renderNavFooter({ saveBeforeNav: true })}
        </div>
    `;
}

function renderNoCrossProjectRefs(container) {
    container.innerHTML = `
        <div class="dbt-page-card">
            <div class="dbt-page-header">
                <h3 class="dbt-page-title">Cross-Project References</h3>
                <p class="dbt-page-subtitle">Detect references to models in other dbt projects</p>
            </div>

            <!-- Help Section - Always visible for learning -->
            <div class="dbt-help-section">
                <button onclick="toggleCrossProjectRefsHelp()" class="dbt-help-toggle">
                    <h4 class="dbt-help-title">
                        <svg class="dbt-w-4 dbt-h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                        What are cross-project references?
                    </h4>
                    <svg id="help-chevron-cross-project" class="dbt-chevron" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                    </svg>
                </button>
                <div id="help-content-cross-project" class="dbt-help-content hidden" style="font-size: 0.875rem; line-height: 1.6; color: #374151;">

                    <!-- Definition -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Definition</h5>
                        <p style="margin: 0;">Cross-project references let you reference models from <strong>other dbt projects</strong> in your organization. Instead of treating another team's output as a raw source, you can connect directly to their dbt model — preserving the full data lineage.</p>
                    </div>

                    <!-- Analogy -->
                    <div style="margin-bottom: 1.25rem; padding: 0.75rem; background: #f9fafb; border-radius: 6px;">
                        <p style="margin: 0;"><strong>Think of it like internal APIs.</strong> When another team builds a customer dimension table, you can either treat it as "external data" (like calling a third-party API) or reference it directly as an internal dependency (like importing a shared library). Cross-project refs give you the internal connection.</p>
                    </div>

                    <!-- source() vs ref() -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">source() vs ref() — when to use each</h5>
                        <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem;">
                            <li><strong>Use source()</strong> for raw external tables — data you don't control (BigQuery datasets, data lake files, third-party data)</li>
                            <li><strong>Use ref('project', 'model')</strong> for tables built by another dbt project in your organization</li>
                        </ul>
                    </div>

                    <!-- Syntax -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Syntax</h5>
                        <div style="background: #f9fafb; border-radius: 6px; padding: 0.75rem; font-family: ui-monospace, monospace; font-size: 0.8rem; margin-bottom: 0.5rem;">
                            {{ ref('<strong>project_name</strong>', '<strong>model_name</strong>') }}
                        </div>
                        <p style="margin: 0; font-size: 0.8rem;">Example: <code>{{ ref('analytics_platform', 'dim_customer') }}</code></p>
                    </div>

                    <!-- Why use cross-project refs -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Why use cross-project refs?</h5>
                        <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem;">
                            <li><strong>Complete lineage</strong> — See the full data flow from raw sources through their model to yours</li>
                            <li><strong>No accidental builds</strong> — You can't accidentally run upstream models (saves costs)</li>
                            <li><strong>Stable references</strong> — If they rename schemas, your refs still work</li>
                            <li><strong>Clear contracts</strong> — Only <code>access: public</code> models can be referenced</li>
                        </ul>
                    </div>

                    <!-- Requirements -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Requirements</h5>
                        <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem;">
                            <li>dbt Cloud (Enterprise plan) — not available in local development</li>
                            <li>Upstream model must have <code>access: public</code> in its config</li>
                            <li>Upstream project must have a successful production deployment</li>
                        </ul>
                    </div>

                    <!-- Link to docs -->
                    <div>
                        <p style="margin: 0; font-size: 0.8rem;">
                            <a href="https://docs.getdbt.com/docs/collaborate/govern/project-dependencies" target="_blank" style="color: #2563eb; text-decoration: none;">
                                → Read dbt's cross-project reference guide
                            </a>
                        </p>
                    </div>
                </div>
            </div>

            <!-- Status Message -->
            <div class="dbt-callout dbt-callout-primary dbt-success-card">
                <svg class="dbt-success-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                </svg>
                <h4 class="dbt-success-title">No Cross-Project References Found</h4>
                <p class="dbt-success-text">
                    None of the tables in your query match known dbt projects.
                    All references will be treated as sources.
                </p>
            </div>

            ${crossProjectRefsState.sources.length > 0 ? `
            <div class="dbt-mb-6">
                <h4 class="dbt-section-title-lg">Tables to be treated as sources (${crossProjectRefsState.sources.length})</h4>
                <div class="dbt-callout">
                    ${crossProjectRefsState.sources.map(source => `
                        <div class="dbt-model-card dbt-flex-between" style="margin-bottom: 0.5rem;">
                            <div class="dbt-flex-center dbt-gap-md">
                                <svg class="dbt-w-5 dbt-h-5 dbt-text-green" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4"></path>
                                </svg>
                                <div>
                                    <div class="dbt-model-card-title">${escapeHtml(source.original_reference)}</div>
                                    <div class="dbt-hint">→ ${escapeHtml(source.suggested_source)}</div>
                                </div>
                            </div>
                            <span class="dbt-badge dbt-badge-source">Source</span>
                        </div>
                    `).join('')}
                </div>
            </div>
            ` : ''}

            ${renderNavFooter({ saveBeforeNav: true })}
        </div>
    `;
}

function renderCrossProjectRefsUI(container) {
    const refs = crossProjectRefsState.crossProjectRefs;
    const sources = crossProjectRefsState.sources;

    container.innerHTML = `
        <div class="dbt-page-card">
            <div class="dbt-page-header">
                <h3 class="dbt-page-title">Cross-Project References</h3>
                <p class="dbt-page-subtitle">We detected ${refs.length} table(s) that will use cross-project refs.</p>
            </div>

            <!-- Beginner Help Section -->
            <div class="dbt-help-section">
                <button onclick="toggleCrossProjectRefsHelp()" class="dbt-help-toggle">
                    <h4 class="dbt-help-title">
                        <svg class="dbt-w-4 dbt-h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                        What are cross-project references?
                    </h4>
                    <svg id="help-chevron-cross-project" class="dbt-chevron" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                    </svg>
                </button>
                <div id="help-content-cross-project" class="dbt-help-content hidden" style="font-size: 0.875rem; line-height: 1.6; color: #374151;">

                    <!-- Definition -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Definition</h5>
                        <p style="margin: 0;">Cross-project references let you reference models from <strong>other dbt projects</strong> in your organization. Instead of treating another team's output as a raw source, you can connect directly to their dbt model — preserving the full data lineage.</p>
                    </div>

                    <!-- Analogy -->
                    <div style="margin-bottom: 1.25rem; padding: 0.75rem; background: #f9fafb; border-radius: 6px;">
                        <p style="margin: 0;"><strong>Think of it like internal APIs.</strong> When another team builds a customer dimension table, you can either treat it as "external data" (like calling a third-party API) or reference it directly as an internal dependency (like importing a shared library). Cross-project refs give you the internal connection.</p>
                    </div>

                    <!-- source() vs ref() -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">source() vs ref() — when to use each</h5>
                        <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem;">
                            <li><strong>Use source()</strong> for raw external tables — data you don't control (BigQuery datasets, data lake files, third-party data)</li>
                            <li><strong>Use ref('project', 'model')</strong> for tables built by another dbt project in your organization</li>
                        </ul>
                    </div>

                    <!-- Syntax -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Syntax</h5>
                        <div style="background: #f9fafb; border-radius: 6px; padding: 0.75rem; font-family: ui-monospace, monospace; font-size: 0.8rem; margin-bottom: 0.5rem;">
                            {{ ref('<strong>project_name</strong>', '<strong>model_name</strong>') }}
                        </div>
                        <p style="margin: 0; font-size: 0.8rem;">Example: <code style="background: #f3f4f6; padding: 0.125rem 0.375rem; border-radius: 3px; font-size: 0.75rem;">{{ ref('analytics_platform', 'dim_customer') }}</code></p>
                    </div>

                    <!-- Why use cross-project refs -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Why use cross-project refs?</h5>
                        <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem;">
                            <li><strong>Complete lineage</strong> — See the full data flow from raw sources through their model to yours</li>
                            <li><strong>No accidental builds</strong> — You can't accidentally run upstream models (saves costs)</li>
                            <li><strong>Stable references</strong> — If they rename schemas, your refs still work</li>
                            <li><strong>Clear contracts</strong> — Only <code style="background: #f3f4f6; padding: 0.125rem 0.375rem; border-radius: 3px; font-size: 0.75rem;">access: public</code> models can be referenced</li>
                        </ul>
                    </div>

                    <!-- Requirements -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Requirements</h5>
                        <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem;">
                            <li>dbt Cloud (Enterprise plan) — not available in local development</li>
                            <li>Upstream model must have <code style="background: #f3f4f6; padding: 0.125rem 0.375rem; border-radius: 3px; font-size: 0.75rem;">access: public</code> in its config</li>
                            <li>Upstream project must have a successful production deployment</li>
                        </ul>
                    </div>

                    <!-- Link to docs -->
                    <div>
                        <p style="margin: 0; font-size: 0.8rem;">
                            <a href="https://docs.getdbt.com/docs/collaborate/govern/project-dependencies" target="_blank" style="color: #2563eb; text-decoration: none;">
                                → Read dbt's cross-project reference guide
                            </a>
                        </p>
                    </div>
                </div>
            </div>

            <!-- Access Warning -->
            <div class="dbt-callout dbt-mb-4" style="border-left-color: #d97706; background: #fef3c7;">
                <div style="display: flex; align-items: flex-start; gap: 0.75rem;">
                    <svg class="w-5 h-5" style="flex-shrink: 0; color: #d97706; margin-top: 2px;"
                         fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                              d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
                    </svg>
                    <div>
                        <h4 style="font-weight: 600; color: #b45309; margin: 0 0 0.5rem 0;">
                            Model Access Requirement
                        </h4>
                        <p style="margin: 0; font-size: 0.875rem; color: #374151;">
                            Cross-project refs only work with models marked <code style="background: #fefce8; padding: 0.125rem 0.375rem; border-radius: 0.25rem; font-size: 0.8rem;">access: public</code>
                            in their source project. Cross-project refs only work with models marked access: public in their source project.
                        </p>
                    </div>
                </div>
            </div>

            <!-- Detected Cross-Project References -->
            <div class="dbt-mb-6">
                <h4 class="dbt-section-title-lg">Cross-Project References (${refs.length})</h4>
                <p class="dbt-page-subtitle mb-3">Toggle any refs that should remain as sources.</p>
                <div class="space-y-3">
                    ${refs.map((ref) => renderCrossProjectRefItem(ref)).join('')}
                </div>
            </div>

            ${sources.length > 0 ? `
            <!-- Tables that will remain as sources -->
            <div class="dbt-mb-6">
                <button onclick="toggleSourcesList()" class="dbt-flex-center dbt-gap-sm dbt-hint" style="cursor: pointer;">
                    <svg id="sources-list-chevron" class="dbt-chevron dbt-chevron-sm rotate-90" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"></path>
                    </svg>
                    ${sources.length} table(s) will remain as sources
                </button>
                <div id="sources-list-content" class="hidden dbt-mt-3">
                    <div class="dbt-callout">
                        ${sources.map(source => `
                            <div style="padding: 0.5rem 0; border-bottom: 1px solid var(--brand-gray-light);">
                                <div class="dbt-model-card-title">${escapeHtml(source.original_reference)}</div>
                                <div class="dbt-hint">→ ${escapeHtml(source.suggested_source)}</div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            </div>
            ` : ''}

            <div class="dbt-nav-footer dbt-flex-between">
                <button data-action="goToPrevStep" class="dbt-nav-btn dbt-nav-btn-back" aria-label="Go to previous step">
                    <svg class="dbt-w-4 dbt-h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18"></path>
                    </svg>
                    <span>Back</span>
                </button>
                <button onclick="saveCrossProjectRefsAndContinue()" class="dbt-nav-btn dbt-nav-btn-next" aria-label="Save and continue to next step">
                    <span>Next: ${StepRegistry.getStepById(StepRegistry.getNextStepId('cross-project-refs'))?.title || 'Continue'}</span>
                    <svg class="dbt-w-4 dbt-h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path>
                    </svg>
                </button>
            </div>
        </div>
    `;
}

function renderCrossProjectRefItem(ref) {
    const decision = crossProjectRefsState.decisions[ref.original_reference];
    const useCrossRef = decision?.use_cross_ref !== false;
    const escapedRef = escapeHtml(ref.original_reference).replace(/'/g, "\\'");

    return `
        <div class="dbt-model-card" style="padding: 1rem;">
            <div class="dbt-flex-between">
                <div class="dbt-flex-center dbt-gap-md">
                    <svg class="dbt-w-5 dbt-h-5" style="color: ${useCrossRef ? 'var(--brand-primary)' : 'var(--brand-gray)'};" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="${useCrossRef ? 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z' : 'M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4'}"></path>
                    </svg>
                    <div>
                        <div class="dbt-model-card-title">${escapeHtml(ref.original_reference)}</div>
                        <div class="dbt-hint">→ <code class="dbt-code-inline" style="font-size: 0.8rem;">${useCrossRef ? escapeHtml(ref.suggested_ref) : escapeHtml(ref.suggested_source || `{{ source('${ref.dataset}', '${ref.table}') }}`)}</code></div>
                    </div>
                </div>
                <div class="dbt-flex-center dbt-gap-sm">
                    <label class="relative inline-block w-11 h-5" data-ref-toggle="${escapeHtml(ref.original_reference)}">
                        <input type="checkbox" ${useCrossRef ? 'checked' : ''}
                            onchange="toggleCrossProjectRefDecision('${escapedRef}')"
                            class="peer appearance-none w-11 h-5 bg-slate-100 rounded-full checked:bg-[var(--brand-primary)] cursor-pointer transition-colors duration-300" />
                        <span class="absolute top-0 left-0 w-5 h-5 bg-white rounded-full border border-slate-300 shadow-sm transition-transform duration-300 peer-checked:translate-x-6 peer-checked:border-green-500 cursor-pointer"></span>
                    </label>
                    <span
                        data-ref-badge="${escapeHtml(ref.original_reference)}"
                        class="dbt-badge"
                        style="background: ${useCrossRef ? 'var(--brand-primary-light)' : 'var(--brand-gray-light)'}; color: ${useCrossRef ? 'var(--brand-primary)' : 'var(--brand-gray)'};">
                        ${useCrossRef ? escapeHtml(ref.project) : 'Source'}
                    </span>
                </div>
            </div>
        </div>
    `;
}

async function saveCrossProjectRefsAndContinue() {
    // Capture the next step BEFORE async operations (to avoid race conditions)
    const nextStepId = StepRegistry.getNextStepId(currentStep);
    const currentDisplayNum = StepRegistry.idToDisplayNum(currentStep);
    const nextDisplayNum = StepRegistry.idToDisplayNum(nextStepId);
    const prereqKey = `step${currentDisplayNum}_to_step${nextDisplayNum}`;

    // Sync decisions to QueryConfiguration before navigating
    await syncCrossProjectDecisionsToBackend(true);  // immediate sync

    // Navigate to next step with prerequisite check
    const prereqConfig = window.PREREQUISITE_CONFIG || (typeof PREREQUISITE_CONFIG !== 'undefined' ? PREREQUISITE_CONFIG : null);
    if (prereqConfig && prereqConfig[prereqKey]) {
        showPrerequisiteModal(prereqKey, async function() {
            await setActiveStep(nextStepId);
        });
    } else {
        await setActiveStep(nextStepId);
    }
}

/**
 * Sync cross-project decisions to QueryConfiguration backend
 * @param {boolean} immediate - If true, sync immediately without debounce
 */
async function syncCrossProjectDecisionsToBackend(immediate = false) {
    if (!currentQuery?.id) {
        console.warn('Cannot sync cross-project decisions: no current query');
        return;
    }

    // Get decisions as array
    const decisionsArray = Object.values(crossProjectRefsState.decisions);

    if (decisionsArray.length === 0) {
        console.log('No cross-project decisions to sync');
        return;
    }

    crossProjectRefsState.syncing = true;

    try {
        if (immediate) {
            // Use immediate sync (for navigation events)
            await appState.updateStepConfigImmediate(
                currentQuery.id,
                'cross_project_refs',
                { decisions: decisionsArray }
            );
        } else {
            // Use debounced sync (for real-time field changes)
            await appState.updateStepConfig(
                currentQuery.id,
                'cross_project_refs',
                { decisions: decisionsArray }
            );
        }
        console.log(`Synced ${decisionsArray.length} cross-project decisions to backend`);

        // Update frontend analysisResults.hardcodedTables so review page can read isCrossProjectRef
        updateHardcodedTablesWithCrossProjectFlags(decisionsArray);
    } catch (error) {
        console.error('Failed to sync cross-project decisions:', error);
    } finally {
        crossProjectRefsState.syncing = false;
    }
}

/**
 * Update analysisResults.hardcodedTables with isCrossProjectRef flags
 * based on cross-project decisions. This keeps frontend state in sync
 * so the review page summary counts are correct.
 * @param {Array} decisions - Array of decision objects
 */
function updateHardcodedTablesWithCrossProjectFlags(decisions) {
    if (!analysisResults?.hardcodedTables) return;

    // Build a lookup from original_reference -> decision
    const decisionMap = {};
    for (const d of decisions) {
        if (d.original_reference) {
            decisionMap[d.original_reference] = d;
        }
    }

    for (const table of analysisResults.hardcodedTables) {
        const fullRef = (table.table || '').replace(/`/g, '').replace(/"/g, '');
        const parts = fullRef.split('.');
        // Match using dataset.table (last two parts), same as backend _process_table_metadata
        const lookupKey = parts.length >= 2
            ? `${parts[parts.length - 2]}.${parts[parts.length - 1]}`
            : (parts[parts.length - 1] || '');

        const decision = decisionMap[lookupKey];
        if (decision && decision.use_cross_ref && decision.project && decision.model) {
            table.isCrossProjectRef = true;
            table.crossProjectProject = decision.project;
            table.crossProjectModel = decision.model;
        } else if (decision && !decision.use_cross_ref) {
            // User toggled back to source() - clear the flag
            delete table.isCrossProjectRef;
            delete table.crossProjectProject;
            delete table.crossProjectModel;
        }
    }

    // Persist the updated analysisResults to session state
    appState.set('analysisResults', analysisResults);
}

/**
 * Toggle a cross-project ref decision (use cross-ref vs source)
 * @param {string} originalReference - The original table reference
 */
async function toggleCrossProjectRefDecision(originalReference) {
    const decision = crossProjectRefsState.decisions[originalReference];
    if (!decision) {
        console.warn(`No decision found for ${originalReference}`);
        return;
    }

    // Toggle the decision
    decision.use_cross_ref = !decision.use_cross_ref;

    // Update UI to reflect the change
    updateCrossProjectRefItemUI(originalReference, decision);

    // Sync to backend (debounced for real-time updates)
    await syncCrossProjectDecisionsToBackend();
}

/**
 * Update the UI for a specific cross-project ref item
 * @param {string} originalReference - The original table reference
 * @param {Object} decision - The decision object
 */
function updateCrossProjectRefItemUI(originalReference, decision) {
    // Find the toggle button and update its state
    const toggleBtn = document.querySelector(`[data-ref-toggle="${CSS.escape(originalReference)}"]`);
    // Return slider setting
    if (toggleBtn) {
        const checkbox = toggleBtn.querySelector('input[type="checkbox"]');
        if (checkbox) checkbox.checked = decision.use_cross_ref;
    }

    // Update the badge
    const badge = document.querySelector(`[data-ref-badge="${CSS.escape(originalReference)}"]`);
    if (badge) {
        if (decision.use_cross_ref) {
            badge.style.background = 'var(--brand-primary-light)';
            badge.style.color = 'var(--brand-primary)';
            badge.textContent = decision.project;
        } else {
            badge.style.background = 'var(--brand-gray-light)';
            badge.style.color = 'var(--brand-gray)';
            badge.textContent = 'Source';
        }
    }

    // Update the icon
    const card = toggleBtn?.closest('.dbt-model-card');
    if (card) {
        const icon = card.querySelector('svg');
        if (icon) {
            icon.style.color = decision.use_cross_ref ? 'var(--brand-primary)' : 'var(--brand-gray)';
            const path = icon.querySelector('path');
            if (path) {
                path.setAttribute('d', decision.use_cross_ref
                    ? 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z'
                    : 'M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4'
                );
            }
        }

        // Update the code preview
        const codeEl = card.querySelector('.dbt-code-inline');
        if (codeEl) {
            // Find the original ref data from crossProjectRefsState
            const ref = crossProjectRefsState.crossProjectRefs.find(r => r.original_reference === originalReference);
            if (ref) {
                codeEl.textContent = decision.use_cross_ref
                    ? ref.suggested_ref
                    : (ref.suggested_source || `{{ source('${ref.dataset}', '${ref.table}') }}`);
            }
        }
    }
}

function toggleCrossProjectRefsHelp() {
    toggleHelpSection('help-content-cross-project', 'help-chevron-cross-project');
}

function toggleSourcesList() {
    const content = document.getElementById('sources-list-content');
    const chevron = document.getElementById('sources-list-chevron');
    if (content && chevron) {
        content.classList.toggle('hidden');
        chevron.classList.toggle('rotate-90');
    }
}
