// ============================================
// STEP 9: REVIEW
// ============================================

async function renderReview(container) {
    if (!analysisResults) {
        container.innerHTML = '<div class="bg-white rounded-lg border border-gray-200 p-6"><p class="text-gray-600">Please complete the analysis first.</p></div>';
        return;
    }

    // CONSOLIDATE what was shown in Steps 2-4
    // Step 2 (Staging): uses layerClassification.staging directly
    // Step 3 (Intermediate): uses getAllModels().filter(m => m.layer === 'intermediate')
    // Step 4 (Mart): uses getAllModels().filter(m => m.layer === 'mart')
    // Review consolidates these same data sources
    const layerClassification = analysisResults.layerClassification || {};
    const stagingCount = (layerClassification.staging || []).length; // Same as Step 2

    const allModels = getAllModels();
    const intermediateCount = allModels.filter(m => m.layer === 'intermediate').length; // Same as Step 3
    const martCount = allModels.filter(m => m.layer === 'mart').length; // Same as Step 4

    // Deduplicate counts — hardcodedTables can have multiple entries for the same
    // table (e.g. referenced in both FROM and JOIN), so count unique references only
    const seenSources = new Set();
    const seenCrossRefs = new Set();
    (analysisResults.hardcodedTables || []).forEach(t => {
        if (t.isSelfReference) return;
        const parts = (t.table || '').replace(/`/g, '').replace(/"/g, '').split('.');
        const key = parts.length >= 2
            ? `${parts[parts.length - 2]}.${parts[parts.length - 1]}`
            : (parts[parts.length - 1] || '');
        if (t.isCrossProjectRef) {
            seenCrossRefs.add(key);
        } else {
            seenSources.add(key);
        }
    });
    const sourcesCount = seenSources.size;
    const crossProjectRefsCount = seenCrossRefs.size;
    const totalModelsCount = stagingCount + intermediateCount + martCount;

    container.innerHTML = `
        <div class="dbt-page-card">
            <div class="dbt-page-header">
                <h3 class="dbt-page-title">Review Configuration</h3>
                <p class="dbt-page-subtitle">Review your model configuration before deployment</p>
            </div>

            <!-- Summary Stats -->
            <div class="dbt-stats-grid">
                <div class="dbt-stat-card">
                    <div class="dbt-stat-value text-purple-600">${sourcesCount}</div>
                    <div class="dbt-stat-label">Sources</div>
                </div>
                <div class="dbt-stat-card">
                    <div class="dbt-stat-value text-orange-600">${crossProjectRefsCount}</div>
                    <div class="dbt-stat-label">Cross-Project Refs</div>
                </div>
                <div class="dbt-stat-card">
                    <div class="dbt-stat-value text-green-600">${totalModelsCount}</div>
                    <div class="dbt-stat-label">Models</div>
                    <div class="text-xs text-gray-500 mt-1">${stagingCount} Staging · ${intermediateCount} Int · ${martCount} Mart</div>
                </div>
            </div>

            <!-- Model Configuration Summary -->
            <div class="mb-6">
                <h4 class="dbt-section-title-lg">
                    <svg class="w-5 h-5 text-[#4f46e5]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                    </svg>
                    Model Configurations (${stagingCount + intermediateCount + martCount} models)
                </h4>
                <div class="dbt-callout" style="max-height: none; overflow-y: auto;">
                    ${renderModelConfigSummary()}
                </div>
            </div>

            <!-- Navigation -->
            ${renderNavFooter({ saveBeforeNav: true })}
        </div>
    `;

    // Mark review as viewed for completion checklist
    stepCompletionState['review'] = { configReviewed: true };
}

// Helper function to render model configuration summary
function renderModelConfigSummary() {
    const allModels = getAllModels();
    if (allModels.length === 0) {
        return '<p class="dbt-hint">No models configured yet.</p>';
    }

    return allModels.map((model, idx) => {
        const config = getModelConfig(idx);
        const badgeClass = model.type === 'prep' ? 'dbt-badge-prep' : 'dbt-badge-final';
        return `
            <div class="dbt-model-card" style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.5rem;">
                <div style="display: flex; align-items: center; gap: 0.75rem;">
                    <span class="dbt-badge ${badgeClass}">
                        ${model.type.toUpperCase()}
                    </span>
                    <span class="dbt-model-card-title">${model.name}</span>
                </div>
                <div style="display: flex; align-items: center; gap: 1rem; font-size: 0.75rem; color: var(--brand-gray-dark);">
                    <span>Schema: <code class="dbt-code-inline">${config.schema || 'default'}</code></span>
                    <span>Mat: <code class="dbt-code-inline">${config.materialization || 'table'}</code></span>
                    <span>Tags: ${(config.tags || []).length > 0 ? config.tags.join(', ') : 'none'}</span>
                </div>
            </div>
        `;
    }).join('');
}
