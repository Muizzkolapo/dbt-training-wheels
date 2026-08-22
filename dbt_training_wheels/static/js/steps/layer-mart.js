// ============================================
// LAYER STEP: MART
// ============================================
// Displays the final output tables (the tables being created)
// These are the business-facing models consumed by downstream systems

function renderLayerMart(container) {
    if (!currentQuery) {
        container.innerHTML = '<div class="dbt-page-card"><p class="dbt-hint">Please select a query first.</p></div>';
        return;
    }

    // Ensure model entries exist so descriptions can be saved to them
    initializeModelConfigurations();

    // Get naming config from analysis results or use defaults
    const naming = analysisResults?.naming || {};
    const martModelPrefix = naming.martModelPrefix || '';
    const intermediateModelPrefix = naming.intermediateModelPrefix || 'int__';
    const martsFolder = naming.martsFolder || 'marts';

    // Get all models from centralized getAllModels() function
    const allModels = getAllModels();
    const martComponents = allModels.filter(m => m.layer === 'mart');
    const intermediateComponents = allModels.filter(m => m.layer === 'intermediate');

    // Check if we have upstream models
    const hasIntermediateModels = intermediateComponents.length > 0;

    // If no mart components, show the final select as the mart
    // (there's always at least a final output)
    const finalSelect = analysisResults?.finalSelect || null;
    const hasMartComponents = martComponents.length > 0 || finalSelect;

    if (!hasMartComponents) {
        container.innerHTML = `
            <div class="dbt-page-card">
                <div class="dbt-page-header" style="display: flex; align-items: center; gap: 0.75rem;">
                    <div class="dbt-icon-box dbt-icon-box-md dbt-icon-box-success">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                    </div>
                    <div>
                        <h3 class="dbt-page-title" style="margin-bottom: 0;">Mart Layer</h3>
                        <p class="dbt-page-subtitle" style="margin-bottom: 0;">Final output models</p>
                    </div>
                </div>
                <!-- Collapsible Help Section -->
                <div class="dbt-help-section dbt-mb-6">
                    <button onclick="toggleMartHelp()" class="dbt-help-toggle">
                        <h4 class="dbt-help-title">
                            <svg class="dbt-w-4 dbt-h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                            </svg>
                            What are Mart Models?
                        </h4>
                        <svg id="help-chevron-mart" class="dbt-chevron" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                        </svg>
                    </button>
                    <div id="help-content-mart" class="dbt-help-content hidden" style="font-size: 0.875rem; line-height: 1.6; color: #374151;">

                        <!-- Definition -->
                        <div style="margin-bottom: 1.25rem;">
                            <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Definition</h5>
                            <p style="margin: 0;">Mart models are the <strong>final output layer</strong> — the business-facing tables that analysts, dashboards, and BI tools actually query. This is where all your intermediate transformation work comes together.</p>
                        </div>

                        <!-- Analogy -->
                        <div style="margin-bottom: 1.25rem; padding: 0.75rem; background: #f9fafb; border-radius: 6px;">
                            <p style="margin: 0;"><strong>Think of it like a finished product on a shelf.</strong> Intermediate models are the assembly — marts are the final products customers actually buy. They're ready to use, no further transformation needed.</p>
                        </div>

                        <!-- What belongs here -->
                        <div style="margin-bottom: 1.25rem;">
                            <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">What typically goes in marts?</h5>
                            <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem;">
                                <li><strong>Business entities</strong> — dim_customers, fct_orders, fct_revenue</li>
                                <li><strong>Wide, denormalized tables</strong> — Include all relevant data for easy querying</li>
                                <li><strong>Department-specific views</strong> — Organize by business area (finance, marketing)</li>
                                <li><strong>Materialized as tables</strong> — For better query performance</li>
                            </ul>
                        </div>

                        <!-- Naming convention -->
                        <div style="margin-bottom: 1.25rem;">
                            <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Naming convention</h5>
                            <div style="background: #f9fafb; border-radius: 6px; padding: 0.75rem; font-family: ui-monospace, monospace; font-size: 0.8rem; margin-bottom: 0.5rem;">
                                mart__[project]__[entity].sql
                            </div>
                            <p style="margin: 0; font-size: 0.8rem;">The prefix is set in your <code>dbt_training_wheels_config.yaml</code>. Example: <code>mart__myproject__customer_orders.sql</code></p>
                        </div>

                        <!-- Key principle -->
                        <div style="margin-bottom: 1.25rem;">
                            <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Key principle</h5>
                            <p style="margin: 0; font-size: 0.8rem;"><strong>Entity-grained.</strong> Each row should represent one instance of a concept — one order, one customer, one transaction. This makes data intuitive for business users.</p>
                        </div>

                        <!-- Link to docs -->
                        <div>
                            <p style="margin: 0; font-size: 0.8rem;">
                                <a href="https://docs.getdbt.com/best-practices/how-we-structure/4-marts" target="_blank" style="color: #2563eb; text-decoration: none;">
                                    → Read dbt's marts guide
                                </a>
                            </p>
                        </div>
                    </div>
                </div>

                <div class="dbt-callout dbt-callout-warning">
                    <p class="text-sm">No mart layer output identified. This usually means the query doesn't produce a final result set.</p>
                </div>
                ${renderNavFooter({ stepId: 'layer-mart', saveBeforeNav: true })}
            </div>
        `;
        return;
    }

    // Build mart models HTML - each final table becomes a mart model that SELECT FROM int/stg
    let martModelsHtml = '';

    // For each final table, create a mart model that SELECT FROM the upstream layer
    martModelsHtml += martComponents.map((component, idx) => {
        // component.name already includes the prefix from getAllModels()
        const modelName = component.name;

        // Determine what this mart model should SELECT FROM
        // Use the upstreamCte field (determined from the final SELECT's FROM clause)
        let upstreamRef = '';
        let upstreamLayer = '';

        if (hasIntermediateModels) {
            // Find the intermediate model that matches the upstream CTE
            let relevantInt = null;

            // component.upstreamCte contains the CTE name from the final SELECT
            // For multi-INSERT files, this may include table context: "tablename__ctename"
            if (component.upstreamCte) {
                // Strategy 1: Check intermediate models
                // Match by: table (unique name), name with prefix, or parentTable + originalName
                relevantInt = intermediateComponents.find(m =>
                    m.table === component.upstreamCte ||
                    m.name === `${intermediateModelPrefix}${component.upstreamCte}` ||
                    // Match by parent table context (for multi-INSERT files)
                    (m.parentTable === component.table && m.originalName === component.upstreamCte) ||
                    // Handle case where upstreamCte has table prefix
                    (component.upstreamCte.includes('__') && m.table === component.upstreamCte)
                );

                // Strategy 2: If not found in intermediate, check staging models
                if (!relevantInt) {
                    const stagingComponents = allModels.filter(m => m.layer === 'staging');
                    const stagingModelPrefix = naming.stagingModelPrefix || 'stg__';
                    relevantInt = stagingComponents.find(m =>
                        m.table === component.upstreamCte ||
                        m.name === `${stagingModelPrefix}${component.upstreamCte}` ||
                        (m.parentTable === component.table && m.originalName === component.upstreamCte) ||
                        (component.upstreamCte.includes('__') && m.table === component.upstreamCte)
                    );
                    if (relevantInt) {
                        upstreamLayer = 'staging';
                    }
                }

                // Log for debugging
                console.log(`[Mart ${component.table}] upstreamCte: ${component.upstreamCte}, found: ${relevantInt?.name || 'none'}`);
            }

            // Fall back to first intermediate model if no match found
            if (!relevantInt) {
                // Try to find an intermediate that belongs to this mart's table
                relevantInt = intermediateComponents.find(m => m.parentTable === component.table);
                if (!relevantInt) {
                    relevantInt = intermediateComponents[0];
                }
                console.log(`[Mart ${component.table}] Fallback to: ${relevantInt?.name}`);
            }

            // relevantInt.name already includes the prefix from getAllModels()
            upstreamRef = `{{ ref('${relevantInt.name}') }}`;
            if (!upstreamLayer) {
                upstreamLayer = 'intermediate';
            }
        } else {
            upstreamRef = `{{ source('...', '...') }}`;
            upstreamLayer = 'source';
        }

        return `
            <div class="dbt-model-card dbt-model-card-highlight" data-component-idx="${idx}">
                <div class="dbt-flex-start dbt-gap-md">
                    <div class="dbt-icon-box dbt-icon-box-sm dbt-icon-box-success">
                        <svg class="dbt-w-5 dbt-h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z"></path>
                        </svg>
                    </div>
                    <div class="dbt-flex-1">
                        <div class="dbt-flex-between dbt-mb-2">
                            <h4 class="dbt-model-card-title">${modelName}.sql</h4>
                            <span class="dbt-badge dbt-badge-success">Final Output Table</span>
                        </div>

                        <p class="dbt-text-sm dbt-hint dbt-mb-3">
                            Business-facing model that SELECTs FROM the ${upstreamLayer} layer.
                        </p>

                        <!-- Preview of mart SELECT FROM pattern -->
                        <div class="dbt-code-block">
                            <div class="dbt-code-block-header">
                                <span class="dbt-text-xs dbt-font-medium">Model Pattern</span>
                            </div>
                            <div class="dbt-code-block-content">
                                <pre><code>{{ config(materialized='table') }}

-- Mart model: ${modelName}
-- Final business entity, SELECT FROM ${upstreamLayer}

SELECT *
FROM ${upstreamRef}</code></pre>
                            </div>
                        </div>

                        <div class="dbt-mt-3 dbt-flex-center dbt-gap-sm">
                            <svg class="dbt-w-4 dbt-h-4 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6"></path>
                            </svg>
                            <span class="dbt-text-sm dbt-hint">References ${upstreamLayer} layer via <code class="dbt-code-inline">ref()</code></span>
                        </div>

                        <!-- Mart Description Input -->
                        <div class="dbt-mt-4">
                            <label for="mart-desc-${idx}" class="dbt-text-sm dbt-font-medium" style="display: block; margin-bottom: 0.5rem; color: #374151;">
                                Model Description <span style="color: #ef4444;">*</span>
                                <span class="dbt-hint" style="font-weight: normal; margin-left: 0.25rem;">— Required. Will be included in schema.yml</span>
                            </label>
                            <textarea
                                id="mart-desc-${idx}"
                                data-model-name="${modelName}"
                                class="mart-description-input"
                                rows="3"
                                placeholder="Describe the business purpose, key metrics, or usage notes for this mart model..."
                                style="width: 100%; padding: 0.5rem; border: 1px solid #d1d5db; border-radius: 6px; font-size: 0.875rem; font-family: inherit; resize: vertical;"
                            >${getSavedDescription(modelName)}</textarea>
                            <p class="dbt-text-xs dbt-hint dbt-mt-1">This description will be used in your dbt documentation.</p>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }).join('');

    const totalModels = martComponents.length + (finalSelect ? 1 : 0);

    container.innerHTML = `
        <div class="dbt-page-card">
            <div class="dbt-page-header dbt-flex-center dbt-gap-md">
                <div class="dbt-icon-box dbt-icon-box-md dbt-icon-box-success">
                    <svg class="dbt-w-6 dbt-h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                    </svg>
                </div>
                <div>
                    <h3 class="dbt-page-title dbt-mb-0">Mart Layer</h3>
                    <p class="dbt-page-subtitle dbt-mb-0">Final business entities in <code class="dbt-code-inline">models/${martsFolder}/</code></p>
                </div>
            </div>

            <!-- Collapsible Help Section -->
            <div class="dbt-help-section dbt-mb-6">
                <button onclick="toggleMartHelp()" class="dbt-help-toggle">
                    <h4 class="dbt-help-title">
                        <svg class="dbt-w-4 dbt-h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                        What are Mart Models?
                    </h4>
                    <svg id="help-chevron-mart" class="dbt-chevron" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                    </svg>
                </button>
                <div id="help-content-mart" class="dbt-help-content hidden" style="font-size: 0.875rem; line-height: 1.6; color: #374151;">

                    <!-- Definition -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Definition</h5>
                        <p style="margin: 0;">Mart models are the <strong>final output layer</strong> — the business-facing tables that analysts, dashboards, and BI tools actually query. This is where all your intermediate transformation work comes together.</p>
                    </div>

                    <!-- Analogy -->
                    <div style="margin-bottom: 1.25rem; padding: 0.75rem; background: #f9fafb; border-radius: 6px;">
                        <p style="margin: 0;"><strong>Think of it like a finished product on a shelf.</strong> Intermediate models are the assembly — marts are the final products customers actually buy. They're ready to use, no further transformation needed.</p>
                    </div>

                    <!-- What belongs here -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">What typically goes in marts?</h5>
                        <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem;">
                            <li><strong>Business entities</strong> — dim_customers, fct_orders, fct_revenue</li>
                            <li><strong>Wide, denormalized tables</strong> — Include all relevant data for easy querying</li>
                            <li><strong>Department-specific views</strong> — Organize by business area (finance, marketing)</li>
                            <li><strong>Materialized as tables</strong> — For better query performance</li>
                        </ul>
                    </div>

                    <!-- Naming convention -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Naming convention</h5>
                        <div style="background: #f9fafb; border-radius: 6px; padding: 0.75rem; font-family: ui-monospace, monospace; font-size: 0.8rem; margin-bottom: 0.5rem;">
                            mart__[project]__[entity].sql
                        </div>
                        <p style="margin: 0; font-size: 0.8rem;">The prefix is set in your <code>dbt_training_wheels_config.yaml</code>. Example: <code>mart__myproject__customer_orders.sql</code></p>
                    </div>

                    <!-- Key principle -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Key principle</h5>
                        <p style="margin: 0; font-size: 0.8rem;"><strong>Entity-grained.</strong> Each row should represent one instance of a concept — one order, one customer, one transaction. This makes data intuitive for business users.</p>
                    </div>

                    <!-- Link to docs -->
                    <div>
                        <p style="margin: 0; font-size: 0.8rem;">
                            <a href="https://docs.getdbt.com/best-practices/how-we-structure/4-marts" target="_blank" style="color: #2563eb; text-decoration: none;">
                                → Read dbt's marts guide
                            </a>
                        </p>
                    </div>
                </div>
            </div>

            <!-- Mart Models -->
            <div class="dbt-space-y-3">
                ${martModelsHtml}
            </div>

            ${renderNavFooter({
                stepId: 'layer-mart',
                saveBeforeNav: true,
                middleContent: `<span class="dbt-hint">${totalModels} mart model${totalModels !== 1 ? 's' : ''}</span>`
            })}
        </div>
    `;
}

// Toggle SQL visibility for mart component
function toggleMartComponentSql(idx) {
    const sqlBlock = document.getElementById(`mart-sql-${idx}`);
    const chevron = document.getElementById(`mart-chevron-${idx}`);

    if (sqlBlock && chevron) {
        sqlBlock.classList.toggle('hidden');
        chevron.classList.toggle('rotate-180');
    }
}

// Toggle mart help section visibility
function toggleMartHelp() {
    toggleHelpSection('help-content-mart', 'help-chevron-mart');
}

// Get saved description for a model from modelConfigurations
// Save mart descriptions to backend
async function saveMartDescriptions() {
    if (!currentQuery || !currentQuery.id) {
        console.log('[Mart Descriptions] No current query, skipping save');
        return;
    }

    // Collect all mart descriptions from textareas
    const descriptionInputs = document.querySelectorAll('.mart-description-input');
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
                        type: 'mart',
                        materialization: 'table',
                        schema: '',
                        tags: [],
                        description: description,
                    };
                }
            }
        }
    });

    // Only send to backend if there are non-empty descriptions
    if (!hasDescriptions) {
        console.log('[Mart Descriptions] No non-empty descriptions to save');
        return;
    }

    // Filter out empty descriptions for backend
    const nonEmptyDescriptions = {};
    Object.entries(descriptions).forEach(([key, value]) => {
        if (value) {
            nonEmptyDescriptions[key] = value;
        }
    });

    try {
        console.log('[Mart Descriptions] Saving descriptions:', nonEmptyDescriptions);

        const response = await fetch(`/api/mart-documentation/${currentQuery.id}`, {
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
        console.log('[Mart Descriptions] Save successful:', result);

        // Show success message (optional)
        if (typeof showToast === 'function') {
            showToast('Mart descriptions saved successfully', 'success');
        }
    } catch (error) {
        console.error('[Mart Descriptions] Save failed:', error);
        if (typeof showToast === 'function') {
            showToast('Failed to save mart descriptions', 'error');
        }
    }
}

// Hook into navigation to save descriptions before leaving the step
// This function should be called by the navigation system
window.beforeLeaveMartStep = saveMartDescriptions;
