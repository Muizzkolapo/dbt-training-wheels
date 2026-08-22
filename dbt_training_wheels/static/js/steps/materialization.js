// ============================================
// STEP 5: MATERIALIZATION
// ============================================

function renderMaterialization(container) {
    if (!analysisResults) {
        container.innerHTML = '<div class="dbt-page-card"><p class="dbt-hint">Please complete the analysis first.</p></div>';
        return;
    }

    initializeModelConfigurations();

    const materializationHints = {
        table: 'Creates a physical table. Best for frequently queried data.',
        view: 'Creates a database view. No storage cost but slower queries.',
        incremental: 'Only processes new/changed rows. Best for large fact tables.',
        ephemeral: 'Inlined as CTE, no database object. Best for intermediate logic.'
    };

    container.innerHTML = `
        <div class="dbt-page-card">
            <div class="dbt-page-header">
                <h3 class="dbt-page-title">Step ${getCurrentDisplayNum()}: Materialization</h3>
                <p class="dbt-page-subtitle">Choose how dbt builds each model</p>
            </div>

            <!-- Help Section -->
            <div class="dbt-help-section">
                <button onclick="toggleStepHelp(3)" class="dbt-help-toggle">
                    <h4 class="dbt-help-title">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                        What is materialization?
                    </h4>
                    <svg id="help-chevron-step3" class="dbt-chevron" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                    </svg>
                </button>
                <div id="help-content-step3" class="dbt-help-content hidden" style="font-size: 0.875rem; line-height: 1.6; color: #374151;">

                    <!-- Definition -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Definition</h5>
                        <p style="margin: 0;">Materialization tells dbt <strong>how</strong> to build your model in the database — as a physical table, a view, or something else. Each option has different trade-offs for speed, storage, and freshness.</p>
                    </div>

                    <!-- Analogy -->
                    <div style="margin-bottom: 1.25rem; padding: 0.75rem; background: #f9fafb; border-radius: 6px;">
                        <p style="margin: 0;"><strong>Think of it like choosing building materials.</strong> A brick house (table) is sturdy and fast to live in, but takes time to rebuild. A tent (view) is quick to set up but slower to use daily. Choose based on how the model will be used.</p>
                    </div>

                    <!-- The four options -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">The four materialization types</h5>
                        <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem;">
                            <li><strong>table</strong> — Creates a physical table. Fast queries, uses storage, full rebuild each run. <em>Best for: final outputs, dashboards</em></li>
                            <li><strong>view</strong> — Creates a database view. No storage, runs SQL on each query. <em>Best for: rarely-used models</em></li>
                            <li><strong>incremental</strong> — Only processes new/changed rows. Very efficient for large datasets. <em>Best for: event logs, fact tables</em></li>
                            <li><strong>ephemeral</strong> — No database object created; SQL is inlined as a CTE. <em>Best for: reusable intermediate logic</em></li>
                        </ul>
                    </div>

                    <!-- Quick tip -->
                    <div style="margin-bottom: 1.25rem; padding: 0.75rem; background: #f0fdf4; border-radius: 6px;">
                        <p style="margin: 0; font-size: 0.8rem;"><strong>Quick tip:</strong> Start with <code style="background: #dcfce7; padding: 0.125rem 0.375rem; border-radius: 3px; font-size: 0.75rem;">table</code> for final outputs and <code style="background: #dcfce7; padding: 0.125rem 0.375rem; border-radius: 3px; font-size: 0.75rem;">view</code> for intermediate work. You can always change it later!</p>
                    </div>

                    <!-- Link to docs -->
                    <div>
                        <p style="margin: 0; font-size: 0.8rem;">
                            <a href="https://docs.getdbt.com/docs/build/materializations" target="_blank" style="color: #2563eb; text-decoration: none;">
                                → Read dbt's materializations guide
                            </a>
                        </p>
                    </div>
                </div>
            </div>

            <!-- Model Configuration -->
            <div class="dbt-space-y-4">
                ${getAllModels().length > 0 ?
                    getAllModels().map((model, idx) => {
                        const config = getModelConfig(idx);
                        // Badge based on layer type
                        let badgeClass, badgeText;
                        switch (model.layer || model.type) {
                            case 'staging':
                                badgeClass = 'dbt-badge-primary';
                                badgeText = 'STAGING';
                                break;
                            case 'intermediate':
                                badgeClass = 'dbt-badge-secondary';
                                badgeText = 'INT';
                                break;
                            case 'mart':
                                badgeClass = 'dbt-badge-success';
                                badgeText = 'MART';
                                break;
                            default:
                                badgeClass = 'dbt-badge';
                                badgeText = model.type?.toUpperCase() || 'MODEL';
                        }
                        return `
                            <div class="dbt-model-card dbt-model-card-bg">
                                <div class="dbt-model-card-header">
                                    <svg class="dbt-w-5 dbt-h-5 dbt-text-gray" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                                    </svg>
                                    <span class="dbt-model-card-title">${model.displayName}</span>
                                    <span class="dbt-badge ${badgeClass} dbt-ml-auto">${badgeText}</span>
                                </div>

                                <div>
                                    <label class="dbt-label">
                                        Materialization Strategy
                                    </label>
                                    <select id="mat-step3-${idx}" class="dbt-select">
                                        <option value="table" ${config.materialization === 'table' ? 'selected' : ''}>table</option>
                                        <option value="view" ${config.materialization === 'view' ? 'selected' : ''}>view</option>
                                        <option value="incremental" ${config.materialization === 'incremental' ? 'selected' : ''}>incremental</option>
                                        <option value="ephemeral" ${config.materialization === 'ephemeral' ? 'selected' : ''}>ephemeral</option>
                                    </select>
                                    <div class="dbt-hint dbt-mt-2" id="mat-hint-step3-${idx}">
                                        ${materializationHints[config.materialization]}
                                    </div>
                                </div>

                                <!-- Config Preview -->
                                <div class="dbt-code-block dbt-mt-3">
                                    <div class="dbt-code-block-content">
                                        <div class="dbt-code-block-title dbt-mb-1">Config block (top of your model file):</div>
                                        <pre><code>{{
  config(
    materialized='<span id="preview-mat-step3-${idx}">${config.materialization}</span>'
  )
}}</code></pre>
                                    </div>
                                </div>
                            </div>
                        `;
                    }).join('')
                    : '<p class="dbt-hint">No models to configure</p>'
                }
            </div>

            ${renderNavFooter({ saveBeforeNav: true })}
        </div>
    `;

    // Setup event listeners for all models
    const allModels = getAllModels();
    allModels.forEach((model, idx) => {
        const select = document.getElementById(`mat-step3-${idx}`);
        const hintEl = document.getElementById(`mat-hint-step3-${idx}`);

        if (select) {
            select.addEventListener('change', () => {
                updateModelConfig(idx, 'materialization', select.value);
                if (hintEl) {
                    hintEl.textContent = materializationHints[select.value] || '';
                }
                // Update preview
                const previewEl = document.getElementById(`preview-mat-step3-${idx}`);
                if (previewEl) {
                    previewEl.textContent = select.value;
                }
            });
        }
    });
}

// NEW: Step 4 - Schema (focused on one concept)
