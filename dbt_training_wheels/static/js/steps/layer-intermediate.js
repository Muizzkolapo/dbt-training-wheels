// ============================================
// LAYER STEP: INTERMEDIATE
// ============================================
// Displays CTEs classified as intermediate layer
// Transformations and aggregations between staging and marts

function renderLayerIntermediate(container) {
    if (!currentQuery) {
        container.innerHTML = '<div class="dbt-page-card"><p class="dbt-hint">Please select a query first.</p></div>';
        return;
    }

    // Ensure model entries exist so descriptions can be saved to them
    initializeModelConfigurations();

    // Get naming config from analysis results or use defaults
    const naming = analysisResults?.naming || {};
    const intermediateModelPrefix = naming.intermediateModelPrefix || 'int__';
    const stagingModelPrefix = naming.stagingModelPrefix || 'stg__';
    const stagingFolder = naming.stagingFolder || 'staging';

    // Get intermediate models from centralized getAllModels() function
    const intermediateComponents = getAllModels().filter(m => m.layer === 'intermediate');

    // If no intermediate components, show informational message
    if (intermediateComponents.length === 0) {
        container.innerHTML = `
            <div class="dbt-page-card">
                <div class="dbt-page-header" style="display: flex; align-items: center; gap: 0.75rem;">
                    <div class="dbt-icon-box dbt-icon-box-md dbt-icon-box-secondary">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z"></path>
                        </svg>
                    </div>
                    <div>
                        <h3 class="dbt-page-title" style="margin-bottom: 0;">Intermediate Layer</h3>
                        <p class="dbt-page-subtitle" style="margin-bottom: 0;">No intermediate models identified</p>
                    </div>
                </div>
                <!-- Collapsible Help Section -->
                <div class="dbt-help-section dbt-mb-6">
                    <button onclick="toggleIntermediateHelp()" class="dbt-help-toggle">
                        <h4 class="dbt-help-title">
                            <svg class="dbt-w-4 dbt-h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                            </svg>
                            What are Intermediate Models?
                        </h4>
                        <svg id="help-chevron-intermediate" class="dbt-chevron" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                        </svg>
                    </button>
                    <div id="help-content-intermediate" class="dbt-help-content hidden" style="font-size: 0.875rem; line-height: 1.6; color: #374151;">

                        <!-- Definition -->
                        <div style="margin-bottom: 1.25rem;">
                            <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Definition</h5>
                            <p style="margin: 0;">Intermediate models are the <strong>middle transformation layer</strong> — they sit between staging and marts. This is where you combine, reshape, and prepare data before it becomes a final business entity.</p>
                        </div>

                        <!-- Analogy -->
                        <div style="margin-bottom: 1.25rem; padding: 0.75rem; background: #f9fafb; border-radius: 6px;">
                            <p style="margin: 0;"><strong>Think of it like assembling sub-components.</strong> Before building a car, you don't go straight from raw parts to finished vehicle. You first assemble the engine, the chassis, the electrical system — then combine them. Intermediate models are those sub-assemblies.</p>
                        </div>

                        <!-- What belongs here -->
                        <div style="margin-bottom: 1.25rem;">
                            <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">What typically goes in intermediate?</h5>
                            <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem;">
                                <li><strong>Joining staging models</strong> — Combine cleaned data from multiple sources</li>
                                <li><strong>Aggregations</strong> — GROUP BY to change the grain of data</li>
                                <li><strong>Pivoting</strong> — Reshape rows into columns or vice versa</li>
                                <li><strong>Business logic</strong> — Apply rules before the final mart</li>
                            </ul>
                        </div>

                        <!-- Naming convention -->
                        <div style="margin-bottom: 1.25rem;">
                            <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Naming convention</h5>
                            <div style="background: #f9fafb; border-radius: 6px; padding: 0.75rem; font-family: ui-monospace, monospace; font-size: 0.8rem; margin-bottom: 0.5rem;">
                                int__[project]__[cte_name].sql
                            </div>
                            <p style="margin: 0; font-size: 0.8rem;">The prefix is set in your <code>dbt_training_wheels_config.yaml</code>. Example: <code>int__myproject__customer_orders.sql</code></p>
                        </div>

                        <!-- Key principle -->
                        <div style="margin-bottom: 1.25rem;">
                            <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Key principle</h5>
                            <p style="margin: 0; font-size: 0.8rem;"><strong>Many inputs, one output.</strong> Intermediate models can pull from multiple staging models, but should typically feed into just one mart. This keeps your DAG clean and traceable.</p>
                        </div>

                        <!-- Link to docs -->
                        <div>
                            <p style="margin: 0; font-size: 0.8rem;">
                                <a href="https://docs.getdbt.com/best-practices/how-we-structure/3-intermediate" target="_blank" style="color: #2563eb; text-decoration: none;">
                                    → Read dbt's intermediate guide
                                </a>
                            </p>
                        </div>
                    </div>
                </div>

                <div class="dbt-callout dbt-callout-info">
                    <p class="text-sm">No intermediate models were created from your SQL.</p>
                    <p class="text-sm mt-2">This is normal — your transformations may appear in staging or mart layers instead.</p>
                </div>
                ${renderNavFooter({ stepId: 'layer-intermediate', saveBeforeNav: true })}
            </div>
        `;
        return;
    }

    // Build intermediate models HTML
    const intermediateModelsHtml = intermediateComponents.map((component, idx) => {
        // component.name already includes the prefix from getAllModels()
        const modelName = component.name;

        // Determine what this component references (staging or source tables)
        const refsHtml = buildReferencesHtml(component.dependencies, stagingModelPrefix);

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

                        <!-- SQL Preview (collapsible) -->
                        <div class="dbt-collapsible">
                            <button class="dbt-collapsible-trigger dbt-text-sm" onclick="toggleIntComponentSql(${idx})">
                                <svg id="int-chevron-${idx}" class="dbt-w-4 dbt-h-4 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                                </svg>
                                View SQL
                            </button>
                            <div id="int-sql-${idx}" class="dbt-collapsible-content hidden">
                                <div class="dbt-code-block dbt-mt-2">
                                    <div class="dbt-code-block-content">
                                        <pre><code>${escapeHtml(component.transformedSql || component.sql || '')}</code></pre>
                                    </div>
                                </div>
                            </div>
                        </div>

                        ${refsHtml}

                        <!-- Intermediate Description Input -->
                        <div class="dbt-mt-4">
                            <label for="int-desc-${idx}" class="dbt-text-sm dbt-font-medium" style="display: block; margin-bottom: 0.5rem; color: #374151;">
                                Model Description <span style="color: #ef4444;">*</span>
                                <span class="dbt-hint" style="font-weight: normal; margin-left: 0.25rem;">— Required. Will be included in schema.yml</span>
                            </label>
                            <textarea
                                id="int-desc-${idx}"
                                data-model-name="${modelName}"
                                class="intermediate-description-input"
                                rows="3"
                                placeholder="Describe the transformations, joins, or business logic in this intermediate model..."
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
                    <h3 class="dbt-page-title dbt-mb-0">Intermediate Layer</h3>
                    <p class="dbt-page-subtitle dbt-mb-0">Purpose-built transforms in <code class="dbt-code-inline">models/intermediate/</code></p>
                </div>
            </div>

            <!-- Collapsible Help Section -->
            <div class="dbt-help-section dbt-mb-6">
                <button onclick="toggleIntermediateHelp()" class="dbt-help-toggle">
                    <h4 class="dbt-help-title">
                        <svg class="dbt-w-4 dbt-h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                        What are Intermediate Models?
                    </h4>
                    <svg id="help-chevron-intermediate" class="dbt-chevron" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                    </svg>
                </button>
                <div id="help-content-intermediate" class="dbt-help-content hidden" style="font-size: 0.875rem; line-height: 1.6; color: #374151;">

                    <!-- Definition -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Definition</h5>
                        <p style="margin: 0;">Intermediate models are the <strong>middle transformation layer</strong> — they sit between staging and marts. This is where you combine, reshape, and prepare data before it becomes a final business entity.</p>
                    </div>

                    <!-- Analogy -->
                    <div style="margin-bottom: 1.25rem; padding: 0.75rem; background: #f9fafb; border-radius: 6px;">
                        <p style="margin: 0;"><strong>Think of it like assembling sub-components.</strong> Before building a car, you don't go straight from raw parts to finished vehicle. You first assemble the engine, the chassis, the electrical system — then combine them. Intermediate models are those sub-assemblies.</p>
                    </div>

                    <!-- What belongs here -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">What typically goes in intermediate?</h5>
                        <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem;">
                            <li><strong>Joining staging models</strong> — Combine cleaned data from multiple sources</li>
                            <li><strong>Aggregations</strong> — GROUP BY to change the grain of data</li>
                            <li><strong>Pivoting</strong> — Reshape rows into columns or vice versa</li>
                            <li><strong>Business logic</strong> — Apply rules before the final mart</li>
                        </ul>
                    </div>

                    <!-- Naming convention -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Naming convention</h5>
                        <div style="background: #f9fafb; border-radius: 6px; padding: 0.75rem; font-family: ui-monospace, monospace; font-size: 0.8rem; margin-bottom: 0.5rem;">
                            int__[project]__[cte_name].sql
                        </div>
                        <p style="margin: 0; font-size: 0.8rem;">The prefix is set in your <code>dbt_training_wheels_config.yaml</code>. Example: <code>int__myproject__customer_orders.sql</code></p>
                    </div>

                    <!-- Key principle -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Key principle</h5>
                        <p style="margin: 0; font-size: 0.8rem;"><strong>Many inputs, one output.</strong> Intermediate models can pull from multiple staging models, but should typically feed into just one mart. This keeps your DAG clean and traceable.</p>
                    </div>

                    <!-- Link to docs -->
                    <div>
                        <p style="margin: 0; font-size: 0.8rem;">
                            <a href="https://docs.getdbt.com/best-practices/how-we-structure/3-intermediate" target="_blank" style="color: #2563eb; text-decoration: none;">
                                → Read dbt's intermediate guide
                            </a>
                        </p>
                    </div>
                </div>
            </div>

            <!-- Intermediate Models -->
            <div class="dbt-space-y-3">
                ${intermediateModelsHtml}
            </div>

            ${renderNavFooter({
                stepId: 'layer-intermediate',
                saveBeforeNav: true,
                middleContent: `<span class="dbt-hint">${intermediateComponents.length} intermediate model${intermediateComponents.length !== 1 ? 's' : ''}</span>`
            })}
        </div>
    `;
}

// Toggle SQL visibility for intermediate component
function toggleIntComponentSql(idx) {
    const sqlBlock = document.getElementById(`int-sql-${idx}`);
    const chevron = document.getElementById(`int-chevron-${idx}`);

    if (sqlBlock && chevron) {
        sqlBlock.classList.toggle('hidden');
        chevron.classList.toggle('rotate-180');
    }
}

// Build references HTML showing dependencies
function buildReferencesHtml(dependencies, stagingPrefix) {
    if (!dependencies || dependencies.length === 0) return '';

    const refs = dependencies.map(d => {
        // Check if this references a staging model
        const isStaging = d.startsWith(stagingPrefix.replace('__', ''));
        const refStyle = isStaging ? 'dbt-code-inline dbt-ref-staging' : 'dbt-code-inline';
        return `<code class="${refStyle}">${d}</code>`;
    }).join(', ');

    return `
        <div class="dbt-mt-2 dbt-hint dbt-text-xs">
            <strong>References:</strong> ${refs}
        </div>
    `;
}

// Toggle intermediate help section visibility
function toggleIntermediateHelp() {
    toggleHelpSection('help-content-intermediate', 'help-chevron-intermediate');
}

// Save intermediate descriptions to backend
async function saveIntermediateDescriptions() {
    if (!currentQuery || !currentQuery.id) {
        console.log('[Intermediate Descriptions] No current query, skipping save');
        return;
    }

    const descriptionInputs = document.querySelectorAll('.intermediate-description-input');
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
                        type: 'intermediate',
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
        console.log('[Intermediate Descriptions] No non-empty descriptions to save');
        return;
    }

    const nonEmptyDescriptions = {};
    Object.entries(descriptions).forEach(([key, value]) => {
        if (value) {
            nonEmptyDescriptions[key] = value;
        }
    });

    try {
        console.log('[Intermediate Descriptions] Saving descriptions:', nonEmptyDescriptions);

        const response = await fetch(`/api/model-documentation/${currentQuery.id}/intermediate`, {
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
        console.log('[Intermediate Descriptions] Save successful:', result);

        if (typeof showToast === 'function') {
            showToast('Intermediate descriptions saved successfully', 'success');
        }
    } catch (error) {
        console.error('[Intermediate Descriptions] Save failed:', error);
        if (typeof showToast === 'function') {
            showToast('Failed to save intermediate descriptions', 'error');
        }
    }
}

window.beforeLeaveIntermediateStep = saveIntermediateDescriptions;
