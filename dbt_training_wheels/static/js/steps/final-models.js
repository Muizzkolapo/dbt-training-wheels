// ============================================
// STEP 4: FINAL MODELS
// ============================================

function renderFinalModels(container) {
    if (!currentQuery) {
        container.innerHTML = '<div class="dbt-page-card"><p class="dbt-hint">Please select a query first.</p></div>';
        return;
    }

    const finalTables = currentQuery.tables || [];

    // Get naming config from analysis results or use defaults
    const naming = analysisResults?.naming || {};
    const stagingModelPrefix = naming.stagingModelPrefix || 'stg__';
    const martModelPrefix = naming.martModelPrefix || '';
    const finalModelSuffix = naming.finalModelSuffix || '';
    const stagingFolder = naming.stagingFolder || 'staging';
    const martsFolder = naming.martsFolder || 'marts';

    // Use the correct terminology based on config
    const stagingLayerName = stagingFolder === 'prep' ? 'Prep' : 'Staging';
    const martsLayerName = martsFolder === 'final' ? 'Final' : 'Mart';

    if (finalTables.length === 0) {
        container.innerHTML = `
            <div class="dbt-page-card">
                <div class="dbt-page-header" style="display: flex; align-items: center; gap: 0.75rem;">
                    <div class="dbt-icon-box dbt-icon-box-md dbt-icon-box-primary">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                        </svg>
                    </div>
                    <div>
                        <h3 class="dbt-page-title" style="margin-bottom: 0;">${martsLayerName} Models</h3>
                        <p class="dbt-page-subtitle" style="margin-bottom: 0;">No ${martsLayerName.toLowerCase()} models will be created</p>
                    </div>
                </div>
                <div class="dbt-callout dbt-callout-info">
                    <p class="text-sm">This query doesn't create any tables, so no ${martsLayerName.toLowerCase()} models are needed.</p>
                </div>
            </div>
        `;
        return;
    }

    const finalModelsHtml = finalTables.map(table => `
        <div class="dbt-model-card">
            <div class="dbt-flex-start dbt-gap-md">
                <div class="dbt-icon-box dbt-icon-box-sm dbt-icon-box-primary">
                    <svg class="dbt-w-5 dbt-h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                    </svg>
                </div>
                <div class="dbt-flex-1">
                    <h4 class="dbt-model-card-title dbt-mb-2">${martModelPrefix}${table}${finalModelSuffix}.sql</h4>
                    <div class="dbt-code-block">
                        <div class="dbt-code-block-content">
                            <pre><code>{{ config(materialized='table') }}

-- ${martsLayerName} model: ${martModelPrefix}${table}${finalModelSuffix}
-- This model selects from the ${stagingLayerName.toLowerCase()} model

SELECT * FROM {{ ref('${stagingModelPrefix}${table}') }}</code></pre>
                        </div>
                    </div>
                    <div class="dbt-mt-2 dbt-flex-center dbt-gap-sm dbt-hint">
                        <svg class="dbt-w-4 dbt-h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6"></path>
                        </svg>
                        <span>References ${stagingLayerName.toLowerCase()} model: <code class="dbt-code-inline">${stagingModelPrefix}${table}</code></span>
                    </div>
                </div>
            </div>
        </div>
    `).join('');

    container.innerHTML = `
        <div class="dbt-page-card">
            <div class="dbt-page-header dbt-flex-center dbt-gap-md">
                <div class="dbt-icon-box dbt-icon-box-md dbt-icon-box-primary">
                    <svg class="dbt-w-6 dbt-h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                    </svg>
                </div>
                <div>
                    <h3 class="dbt-page-title dbt-mb-0">${martsLayerName} Models</h3>
                    <p class="dbt-page-subtitle dbt-mb-0">These models will be created in the <code class="dbt-code-inline">models/${martsFolder}/</code> folder</p>
                </div>
            </div>

            <!-- Beginner-Friendly Explanation -->
            <div class="dbt-callout dbt-callout-info dbt-mb-6">
                <div class="dbt-flex-start dbt-gap-md">
                    <svg class="dbt-w-5 dbt-h-5 dbt-shrink-0 dbt-mt-1 dbt-text-blue" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                    </svg>
                    <div>
                        <h4 class="dbt-font-semibold dbt-mb-1 dbt-text-blue-dark">Understanding the ${stagingLayerName}-${martsLayerName} Pattern</h4>
                        <p class="dbt-text-sm dbt-text-blue-dark dbt-mb-2">Think of it like cooking: <strong>${stagingLayerName} models</strong> do all the chopping, mixing, and preparation work. <strong>${martsLayerName} models</strong> are just the plated dish — they take the prepped ingredients and serve them up.</p>

                        <div class="dbt-mt-3 dbt-p-3 dbt-bg-white dbt-rounded dbt-border" style="border-color: #cbd5e1;">
                            <div class="dbt-text-xs dbt-space-y-2">
                                <div class="dbt-flex dbt-gap-2">
                                    <span style="color: #3b82f6; font-weight: 600;">${stagingModelPrefix}customers.sql</span>
                                    <span style="color: #64748b;">→ Does all the work: joins, filters, calculations</span>
                                </div>
                                <div class="dbt-flex dbt-gap-2">
                                    <span style="color: #8b5cf6; font-weight: 600;">${martModelPrefix}customers${finalModelSuffix}.sql</span>
                                    <span style="color: #64748b;">→ Just does: SELECT * FROM {{ ref('${stagingModelPrefix}customers') }}</span>
                                </div>
                            </div>
                        </div>

                        <p class="dbt-text-xs dbt-mt-2 dbt-text-blue-dark"><strong>Why separate them?</strong> This makes it easier to test, debug, and reuse logic. Plus, your ${martsLayerName.toLowerCase()} table names stay clean without a "${stagingModelPrefix}" prefix!</p>
                    </div>
                </div>
            </div>

            <div class="dbt-space-y-3">
                ${finalModelsHtml}
            </div>

            ${renderNavFooter({
                middleContent: `<span class="dbt-hint">${finalTables.length} ${martsLayerName.toLowerCase()} model${finalTables.length !== 1 ? 's' : ''} will be created</span>`
            })}
        </div>
    `;
}

// Step 2: Prep Models (Internal Model References)
