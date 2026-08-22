// ============================================
// LAYER STEP: STAGING
// ============================================
// Displays CTEs classified as staging layer
// CTEs that combine multiple external sources

function renderLayerStaging(container) {
    if (!currentQuery) {
        container.innerHTML = '<div class="dbt-page-card"><p class="dbt-hint">Please select a query first.</p></div>';
        return;
    }

    // Ensure model entries exist so descriptions can be saved to them
    initializeModelConfigurations();

    const naming = analysisResults?.naming || {};
    const stagingModelPrefix = naming.stagingModelPrefix || 'stg__';
    const stagingFolder = naming.stagingFolder || 'staging';

    const layerClassification = analysisResults?.layerClassification || {};
    const stagingComponents = layerClassification.staging || [];

    if (stagingComponents.length === 0) {
        container.innerHTML = `
            <div class="dbt-page-card">
                <div class="dbt-page-header" style="display: flex; align-items: center; gap: 0.75rem;">
                    <div class="dbt-icon-box dbt-icon-box-md dbt-icon-box-secondary">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z"></path>
                        </svg>
                    </div>
                    <div>
                        <h3 class="dbt-page-title" style="margin-bottom: 0;">Staging Layer</h3>
                        <p class="dbt-page-subtitle" style="margin-bottom: 0;">No staging models identified</p>
                    </div>
                </div>
                <div class="dbt-help-section dbt-mb-6">
                    <button onclick="toggleStagingHelp()" class="dbt-help-toggle">
                        <h4 class="dbt-help-title">
                            <svg class="dbt-w-4 dbt-h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                            </svg>
                            What are Staging Models?
                        </h4>
                        <svg id="help-chevron-staging" class="dbt-chevron" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                        </svg>
                    </button>
                    <div id="help-content-staging" class="dbt-help-content hidden" style="font-size: 0.875rem; line-height: 1.6; color: #374151;">
                        <div style="margin-bottom: 1.25rem;">
                            <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Definition</h5>
                            <p style="margin: 0;">Staging models clean and standardize raw sources. Here, any CTE that pulls from multiple external sources is staged.</p>
                        </div>
                        <div style="margin-bottom: 1.25rem;">
                            <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Naming convention</h5>
                            <div style="background: #f9fafb; border-radius: 6px; padding: 0.75rem; font-family: ui-monospace, monospace; font-size: 0.8rem; margin-bottom: 0.5rem;">
                                stg__[project]__[cte_name].sql
                            </div>
                            <p style="margin: 0; font-size: 0.8rem;">The prefix is set in your <code>dbt_training_wheels_config.yaml</code>. Example: <code>stg__myproject__raw_orders.sql</code></p>
                        </div>
                    </div>
                </div>

                <div class="dbt-callout dbt-callout-info">
                    <p class="text-sm">No staging models were created from your SQL.</p>
                    <p class="text-sm mt-2">This is normal — your transformations may start in intermediate or mart layers instead.</p>
                </div>
                ${renderNavFooter({ stepId: 'layer-staging', saveBeforeNav: true })}
            </div>
        `;
        return;
    }

    const stagingModelsHtml = stagingComponents.map((component, idx) => {
        const modelName = `${stagingModelPrefix}${component.name}`;
        const refsHtml = buildStagingReferencesHtml(component.dependencies);

        return `
            <div class="dbt-model-card" data-component-idx="${idx}">
                <div class="dbt-flex-start dbt-gap-md">
                    <div class="dbt-icon-box dbt-icon-box-sm dbt-icon-box-secondary">
                        <svg class="dbt-w-5 dbt-h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z"></path>
                        </svg>
                    </div>
                    <div class="dbt-flex-1">
                        <div class="dbt-flex-between dbt-mb-2">
                            <h4 class="dbt-model-card-title">${modelName}.sql</h4>
                        </div>

                        <div class="dbt-collapsible">
                            <button class="dbt-collapsible-trigger dbt-text-sm" onclick="toggleStgComponentSql(${idx})">
                                <svg id="stg-chevron-${idx}" class="dbt-w-4 dbt-h-4 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                                </svg>
                                View SQL
                            </button>
                            <div id="stg-sql-${idx}" class="dbt-collapsible-content hidden">
                                <div class="dbt-code-block dbt-mt-2">
                                    <div class="dbt-code-block-content">
                                        <pre><code>${escapeHtml(component.transformedSql || component.sql || '')}</code></pre>
                                    </div>
                                </div>
                            </div>
                        </div>

                        ${refsHtml}

                        <!-- Staging Description Input -->
                        <div class="dbt-mt-4">
                            <label for="stg-desc-${idx}" class="dbt-text-sm dbt-font-medium" style="display: block; margin-bottom: 0.5rem; color: #374151;">
                                Model Description <span style="color: #ef4444;">*</span>
                                <span class="dbt-hint" style="font-weight: normal; margin-left: 0.25rem;">— Required. Will be included in schema.yml</span>
                            </label>
                            <textarea
                                id="stg-desc-${idx}"
                                data-model-name="${modelName}"
                                class="staging-description-input"
                                rows="3"
                                placeholder="Describe the purpose of this staging model, what sources it combines..."
                                style="width: 100%; padding: 0.5rem; border: 1px solid #d1d5db; border-radius: 6px; font-size: 0.875rem; font-family: inherit; resize: vertical;"
                            >${getSavedDescription(modelName)}</textarea>
                            <p class="dbt-text-xs dbt-hint dbt-mt-1">This description will be used in your dbt documentation.</p>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }).join('');

    container.innerHTML = `
        <div class="dbt-page-card">
            <div class="dbt-page-header dbt-flex-center dbt-gap-md">
                <div class="dbt-icon-box dbt-icon-box-md dbt-icon-box-secondary">
                    <svg class="dbt-w-6 dbt-h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z"></path>
                    </svg>
                </div>
                <div>
                    <h3 class="dbt-page-title dbt-mb-0">Staging Layer</h3>
                    <p class="dbt-page-subtitle dbt-mb-0">Raw combinations in <code class="dbt-code-inline">models/${stagingFolder}/</code></p>
                </div>
            </div>

            <div class="dbt-help-section dbt-mb-6">
                <button onclick="toggleStagingHelp()" class="dbt-help-toggle">
                    <h4 class="dbt-help-title">
                        <svg class="dbt-w-4 dbt-h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                        What are Staging Models?
                    </h4>
                    <svg id="help-chevron-staging" class="dbt-chevron" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                    </svg>
                </button>
                <div id="help-content-staging" class="dbt-help-content hidden" style="font-size: 0.875rem; line-height: 1.6; color: #374151;">
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Definition</h5>
                        <p style="margin: 0;">Staging models clean and standardize raw sources. Here, any CTE that pulls from multiple external sources is staged.</p>
                    </div>
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Naming convention</h5>
                        <div style="background: #f9fafb; border-radius: 6px; padding: 0.75rem; font-family: ui-monospace, monospace; font-size: 0.8rem; margin-bottom: 0.5rem;">
                            stg__[project]__[cte_name].sql
                        </div>
                        <p style="margin: 0; font-size: 0.8rem;">The prefix is set in your <code>dbt_training_wheels_config.yaml</code>. Example: <code>stg__myproject__raw_orders.sql</code></p>
                    </div>
                </div>
            </div>

            <div class="dbt-space-y-3">
                ${stagingModelsHtml}
            </div>

            ${renderNavFooter({
                stepId: 'layer-staging',
                saveBeforeNav: true,
                middleContent: `<span class="dbt-hint">${stagingComponents.length} staging model${stagingComponents.length !== 1 ? 's' : ''}</span>`
            })}
        </div>
    `;
}

function toggleStgComponentSql(idx) {
    const sqlBlock = document.getElementById(`stg-sql-${idx}`);
    const chevron = document.getElementById(`stg-chevron-${idx}`);

    if (sqlBlock && chevron) {
        sqlBlock.classList.toggle('hidden');
        chevron.classList.toggle('rotate-180');
    }
}

function toggleStagingHelp() {
    toggleHelpSection('help-content-staging', 'help-chevron-staging');
}

function buildStagingReferencesHtml(dependencies) {
    if (!dependencies || dependencies.length === 0) return '';

    const depsHtml = dependencies.map(d => {
        const cleaned = String(d).replace(/`/g, '');
        return `<code class="dbt-code-inline">${escapeHtml(cleaned)}</code>`;
    }).join(' ');

    return `
        <div class="dbt-mt-2">
            <div class="dbt-text-xs dbt-hint">References external sources:</div>
            <div class="dbt-flex-wrap dbt-gap-xs dbt-mt-1">${depsHtml}</div>
        </div>
    `;
}

// Save staging descriptions to backend
async function saveStagingDescriptions() {
    if (!currentQuery || !currentQuery.id) {
        console.log('[Staging Descriptions] No current query, skipping save');
        return;
    }

    const descriptionInputs = document.querySelectorAll('.staging-description-input');
    const descriptions = {};
    let hasDescriptions = false;

    descriptionInputs.forEach(input => {
        const modelName = input.getAttribute('data-model-name');
        const description = input.value.trim();

        if (modelName && description) {
            descriptions[modelName] = description;
            hasDescriptions = true;

            // Update local modelConfigurations so description is included in save-model-config payload
            if (typeof modelConfigurations !== 'undefined') {
                const existingKey = Object.keys(modelConfigurations).find(
                    k => modelConfigurations[k].table === modelName
                );
                if (existingKey !== undefined) {
                    modelConfigurations[existingKey].description = description;
                } else {
                    // No existing entry — create one so the description reaches the backend
                    const nextKey = Object.keys(modelConfigurations).length;
                    modelConfigurations[nextKey] = {
                        table: modelName,
                        type: 'staging',
                        materialization: 'table',
                        schema: '',
                        tags: [],
                        description: description,
                    };
                }
            }
        }
    });

    if (!hasDescriptions) {
        console.log('[Staging Descriptions] No non-empty descriptions to save');
        return;
    }

    const nonEmptyDescriptions = {};
    Object.entries(descriptions).forEach(([key, value]) => {
        if (value) {
            nonEmptyDescriptions[key] = value;
        }
    });

    try {
        console.log('[Staging Descriptions] Saving descriptions:', nonEmptyDescriptions);

        const response = await fetch(`/api/model-documentation/${currentQuery.id}/staging`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(nonEmptyDescriptions),
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const result = await response.json();
        console.log('[Staging Descriptions] Save successful:', result);

        if (typeof showToast === 'function') {
            showToast('Staging descriptions saved successfully', 'success');
        }
    } catch (error) {
        console.error('[Staging Descriptions] Save failed:', error);
        if (typeof showToast === 'function') {
            showToast('Failed to save staging descriptions', 'error');
        }
    }
}

window.beforeLeaveStagingStep = saveStagingDescriptions;
