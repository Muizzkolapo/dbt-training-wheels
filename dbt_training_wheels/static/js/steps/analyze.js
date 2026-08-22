// ============================================
// STEP 1: ANALYZE SQL - SOURCE TABLES
// ============================================
// Identifies external source tables and maps them to {{ source() }} calls.
// Internal tables (created within script) are handled in layer steps.
// ============================================

function renderAnalyze(container) {
    if (!analysisResults) {
        container.innerHTML = `
            <div class="dbt-page-card">
                <div class="dbt-page-header" style="display: flex; align-items: center; justify-content: space-between;">
                    <h3 class="dbt-page-title" style="margin-bottom: 0;">Step ${getCurrentDisplayNum()}: Identify Source Tables</h3>
                </div>

                <!-- Collapsible Help Section -->
                <div class="dbt-help-section">
                    <button onclick="toggleSourceTablesHelp()" class="dbt-help-toggle">
                        <h4 class="dbt-help-title">
                            <svg class="dbt-w-4 dbt-h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                            </svg>
                            What are Source Tables?
                        </h4>
                        <svg id="help-chevron-source-tables" class="dbt-chevron" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                        </svg>
                    </button>
                    <div id="help-content-source-tables" class="dbt-help-content hidden" style="font-size: 0.875rem; line-height: 1.6; color: #374151;">

                        <!-- Definition -->
                        <div style="margin-bottom: 1.25rem;">
                            <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Definition</h5>
                            <p style="margin: 0;">The <code style="background: #f3f4f6; padding: 0.125rem 0.375rem; border-radius: 3px; font-size: 0.8rem;">source()</code> function tells dbt where your raw data lives. Instead of writing the full table path in your SQL, you give it a friendly name.</p>
                        </div>

                        <!-- Simple Analogy -->
                        <div style="margin-bottom: 1.25rem; padding: 0.75rem; background: #f9fafb; border-radius: 6px;">
                            <p style="margin: 0;"><strong>Think of it like contacts on your phone.</strong> Instead of dialing 555-123-4567 every time, you just tap "Mom". If the number changes, you update it once — not in every text thread.</p>
                        </div>

                        <!-- How it works -->
                        <div style="margin-bottom: 1.25rem;">
                            <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">How your SQL changes</h5>
                            <div style="background: #f9fafb; border-radius: 6px; padding: 0.75rem; font-family: ui-monospace, monospace; font-size: 0.8rem;">
                                <div style="margin-bottom: 0.5rem;">
                                    <span style="color: #6b7280;">Before:</span><br>
                                    <span style="color: #374151;">FROM \`project.dataset.customers\`</span>
                                </div>
                                <div>
                                    <span style="color: #6b7280;">After:</span><br>
                                    <span style="color: #374151;">FROM {{ source('dataset', 'customers') }}</span>
                                </div>
                            </div>
                        </div>

                        <!-- Syntax breakdown -->
                        <div style="margin-bottom: 1.25rem;">
                            <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Syntax</h5>
                            <div style="background: #f9fafb; border-radius: 6px; padding: 0.75rem; font-family: ui-monospace, monospace; font-size: 0.8rem; text-align: center; margin-bottom: 0.5rem;">
                                {{ source('<strong>source_name</strong>', '<strong>table_name</strong>') }}
                            </div>
                            <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem;">
                                <li><strong>source_name</strong> — The dataset or schema (defined in sources.yml)</li>
                                <li><strong>table_name</strong> — The specific table within that source</li>
                            </ul>
                        </div>

                        <!-- Why use it -->
                        <div>
                            <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Why use sources?</h5>
                            <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem;">
                                <li><strong>Environment flexibility</strong> — Switch between dev, staging, and prod without changing SQL</li>
                                <li><strong>Data lineage</strong> — dbt can trace where your data comes from</li>
                                <li><strong>Freshness monitoring</strong> — Get alerts when source data hasn't updated</li>
                                <li><strong>Documentation</strong> — Automatically document what external data you depend on</li>
                            </ul>
                        </div>
                    </div>
                </div>

                <div class="dbt-empty-state">
                    <svg class="dbt-empty-state-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"></path>
                    </svg>
                    <p class="dbt-empty-state-text mb-4">Scan your SQL to find external source tables</p>
                    <button
                        id="analyze-query-btn"
                        data-action="analyzeQuery"
                        class="dbt-btn dbt-btn-primary dbt-btn-lg"
                        style="opacity: 0.5; cursor: not-allowed;"
                        disabled
                    >
                        Run Analysis
                    </button>
                    <p class="dbt-hint mt-2" style="color: #ea580c;">Complete the prerequisite checklist to continue</p>
                </div>
            </div>
        `;

        // Show step1_load prerequisite modal
        setTimeout(() => {
            showPrerequisiteModal('step1_load', function() {
                const analyzeBtn = document.getElementById('analyze-query-btn');
                if (analyzeBtn) {
                    analyzeBtn.disabled = false;
                    analyzeBtn.style.opacity = '1';
                    analyzeBtn.style.cursor = 'pointer';
                }
                const warningMsg = document.querySelector('[style*="color: #ea580c"]');
                if (warningMsg) {
                    warningMsg.remove();
                }
            });
        }, 100);
    } else {
        // Filter to only external source tables (exclude self-references)
        const externalSources = (analysisResults.hardcodedTables || [])
            .filter(t => !t.isSelfReference);

        const selfReferences = (analysisResults.hardcodedTables || [])
            .filter(t => t.isSelfReference);

        container.innerHTML = `
            <div class="dbt-page-card">
                <div class="dbt-page-header dbt-flex-center dbt-gap-md">
                    <div class="dbt-icon-box dbt-icon-box-md dbt-icon-box-primary">
                        <svg class="dbt-w-6 dbt-h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4"></path>
                        </svg>
                    </div>
                    <div>
                        <h3 class="dbt-page-title dbt-mb-0">Source Tables</h3>
                        <p class="dbt-page-subtitle dbt-mb-0">External tables mapped to <code class="dbt-code-inline">{{ source() }}</code></p>
                    </div>
                </div>

                <!-- Collapsible Help Section -->
                <div class="dbt-help-section dbt-mb-6">
                    <button onclick="toggleSourceTablesHelp()" class="dbt-help-toggle">
                        <h4 class="dbt-help-title">
                            <svg class="dbt-w-4 dbt-h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                            </svg>
                            What are Source Tables?
                        </h4>
                        <svg id="help-chevron-source-tables" class="dbt-chevron" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                        </svg>
                    </button>
                    <div id="help-content-source-tables" class="dbt-help-content hidden" style="font-size: 0.875rem; line-height: 1.6; color: #374151;">

                        <!-- Definition -->
                        <div style="margin-bottom: 1.25rem;">
                            <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Definition</h5>
                            <p style="margin: 0;">The <code style="background: #f3f4f6; padding: 0.125rem 0.375rem; border-radius: 3px; font-size: 0.8rem;">source()</code> function tells dbt where your raw data lives. Instead of writing the full table path in your SQL, you give it a friendly name.</p>
                        </div>

                        <!-- Simple Analogy -->
                        <div style="margin-bottom: 1.25rem; padding: 0.75rem; background: #f9fafb; border-radius: 6px;">
                            <p style="margin: 0;"><strong>Think of it like contacts on your phone.</strong> Instead of dialing 555-123-4567 every time, you just tap "Mom". If the number changes, you update it once — not in every text thread.</p>
                        </div>

                        <!-- How it works -->
                        <div style="margin-bottom: 1.25rem;">
                            <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">How your SQL changes</h5>
                            <div style="background: #f9fafb; border-radius: 6px; padding: 0.75rem; font-family: ui-monospace, monospace; font-size: 0.8rem;">
                                <div style="margin-bottom: 0.5rem;">
                                    <span style="color: #6b7280;">Before:</span><br>
                                    <span style="color: #374151;">FROM \`project.dataset.customers\`</span>
                                </div>
                                <div>
                                    <span style="color: #6b7280;">After:</span><br>
                                    <span style="color: #374151;">FROM {{ source('dataset', 'customers') }}</span>
                                </div>
                            </div>
                        </div>

                        <!-- Syntax breakdown -->
                        <div style="margin-bottom: 1.25rem;">
                            <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Syntax</h5>
                            <div style="background: #f9fafb; border-radius: 6px; padding: 0.75rem; font-family: ui-monospace, monospace; font-size: 0.8rem; text-align: center; margin-bottom: 0.5rem;">
                                {{ source('<strong>source_name</strong>', '<strong>table_name</strong>') }}
                            </div>
                            <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem;">
                                <li><strong>source_name</strong> — The dataset or schema (defined in sources.yml)</li>
                                <li><strong>table_name</strong> — The specific table within that source</li>
                            </ul>
                        </div>

                        <!-- Why use it -->
                        <div>
                            <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Why use sources?</h5>
                            <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem;">
                                <li><strong>Environment flexibility</strong> — Switch between dev, staging, and prod without changing SQL</li>
                                <li><strong>Data lineage</strong> — dbt can trace where your data comes from</li>
                                <li><strong>Freshness monitoring</strong> — Get alerts when source data hasn't updated</li>
                                <li><strong>Documentation</strong> — Automatically document what external data you depend on</li>
                            </ul>
                        </div>
                    </div>
                </div>

                <div class="space-y-6">
                    ${externalSources.length > 0 ? `
                    <!-- External Source Tables -->
                    <div>
                        <h4 class="dbt-section-title dbt-mb-3">
                            <svg class="dbt-w-4 dbt-h-4 inline-block mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
                            </svg>
                            ${externalSources.length} External Source Table${externalSources.length !== 1 ? 's' : ''} Found
                        </h4>
                        <div class="dbt-space-y-3">
                            ${externalSources.map((table, idx) => `
                                <div class="dbt-model-card">
                                    <div class="dbt-flex-start dbt-gap-md">
                                        <div class="dbt-icon-box dbt-icon-box-sm dbt-icon-box-primary">
                                            <svg class="dbt-w-4 dbt-h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4"></path>
                                            </svg>
                                        </div>
                                        <div class="dbt-flex-1">
                                            <div class="dbt-mb-2">
                                                <span class="dbt-text-sm dbt-hint">Original:</span>
                                                <code class="dbt-code-inline" style="color: #dc2626; background: rgba(220, 38, 38, 0.1);">\`${escapeHtml(table.table)}\`</code>
                                            </div>
                                            <div class="dbt-flex-center dbt-gap-sm dbt-mb-2">
                                                <svg class="dbt-w-4 dbt-h-4 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6"></path>
                                                </svg>
                                                <span class="dbt-text-sm dbt-hint">Becomes:</span>
                                                <code class="dbt-code-inline" style="color: #16a34a; background: rgba(22, 163, 74, 0.1);">${escapeHtml(table.suggestedSource)}</code>
                                            </div>
                                            <div class="dbt-text-xs dbt-hint">
                                                Source: <strong>${escapeHtml(table.sourceSchema)}</strong> / Table: <strong>${escapeHtml(table.sourceTable)}</strong>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                    ` : `
                    <div class="dbt-callout dbt-callout-success">
                        <p class="text-sm">No external source tables found. Your SQL may only reference tables created within the script.</p>
                    </div>
                    `}

                    ${selfReferences.length > 0 ? `
                    <!-- Internal References Info -->
                    <div class="dbt-callout" style="background: var(--brand-gray-bg); border-color: var(--brand-gray-light);">
                        <div class="dbt-flex-start dbt-gap-md">
                            <svg class="dbt-w-5 dbt-h-5 dbt-shrink-0 dbt-mt-1" style="color: var(--brand-gray-dark);" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                            </svg>
                            <div>
                                <h4 class="dbt-font-semibold dbt-mb-1" style="color: var(--brand-gray-dark);">
                                    ${selfReferences.length} Internal Table${selfReferences.length !== 1 ? 's' : ''} Detected
                                </h4>
                                <p class="dbt-text-sm" style="color: var(--brand-gray-dark);">
                                    These tables are created within your script and will become <code class="dbt-code-inline">{{ ref() }}</code>
                                    calls to staging/intermediate models. They'll be shown in the layer steps.
                                </p>
                                <div class="dbt-mt-2 dbt-text-xs" style="color: var(--brand-gray);">
                                    ${selfReferences.map(t => `<code class="dbt-code-inline">${escapeHtml(t.sourceTable)}</code>`).join(', ')}
                                </div>
                            </div>
                        </div>
                    </div>
                    ` : ''}

                    <!-- DECLARE Variables Warning -->
                    ${analysisResults.declareVariables && analysisResults.declareVariables.length > 0 ? `
                    <div class="dbt-callout dbt-callout-warning">
                        <div style="display: flex; align-items: flex-start; gap: 0.75rem;">
                            <svg class="w-5 h-5 flex-shrink-0" style="color: #d97706; margin-top: 0.125rem;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
                            </svg>
                            <div style="flex: 1;">
                                <h4 class="font-semibold mb-1" style="color: #92400e;">Note: DECLARE Variables Found</h4>
                                <p class="text-sm mb-2" style="color: #92400e;">
                                    Your SQL contains <strong>DECLARE</strong> statements which dbt doesn't support. You'll need to handle these manually.
                                </p>
                                <div class="dbt-bg-white dbt-rounded dbt-p-3 dbt-border" style="border-color: #fcd34d;">
                                    <ul style="margin: 0; padding-left: 1.25rem; font-family: monospace; font-size: 0.8rem; color: #78350f;">
                                        ${analysisResults.declareVariables.map(v => `
                                            <li><strong>${escapeHtml(v.variable)}</strong> (${escapeHtml(v.type)}) = ${escapeHtml(v.defaultValue)}</li>
                                        `).join('')}
                                    </ul>
                                </div>
                            </div>
                        </div>
                    </div>
                    ` : ''}

                    <!-- Current Analysis Info -->
                    <div class="dbt-callout dbt-mt-4" style="background: #f8fafc; border-color: #e2e8f0;">
                        <div class="dbt-flex-between dbt-gap-md">
                            <div>
                                <span class="dbt-text-xs dbt-hint">Project:</span>
                                <code class="dbt-code-inline">${analysisResults.naming?.projectName || userDomainName || 'default'}</code>
                                <span class="dbt-text-xs dbt-hint dbt-ml-3">Staging prefix:</span>
                                <code class="dbt-code-inline">${analysisResults.naming?.stagingModelPrefix || window.orgConfig?.naming?.staging_model_prefix || 'stg__'}</code>
                                <span class="dbt-text-xs dbt-hint dbt-ml-3">Mart prefix:</span>
                                <code class="dbt-code-inline">${analysisResults.naming?.martModelPrefix || window.orgConfig?.naming?.mart_model_prefix || ''}</code>
                            </div>
                            <button
                                data-action="reanalyzeQuery"
                                class="dbt-btn dbt-btn-secondary dbt-btn-sm"
                                onclick="clearAnalysisAndRerun()"
                            >
                                Re-analyze
                            </button>
                        </div>
                    </div>

                    ${renderNavFooter({
                        stepId: 'analyze',
                        showPrev: false,
                        saveBeforeNav: true,
                        middleContent: `<span class="dbt-hint">${externalSources.length} source${externalSources.length !== 1 ? 's' : ''} identified</span>`
                    })}
                </div>
            </div>
        `;
    }
}

/**
 * Clear analysis results and re-run analysis with current project selection.
 * This allows users to re-analyze if they changed projects.
 */
function clearAnalysisAndRerun() {
    console.log('[DEBUG clearAnalysisAndRerun] Clearing analysis results');
    console.log('[DEBUG clearAnalysisAndRerun] Current userDomainName:', userDomainName);
    console.log('[DEBUG clearAnalysisAndRerun] Current sessionStorage:', sessionStorage.getItem('dbt_training_wheels_domain_name'));

    // Clear cached analysis results
    analysisResults = null;
    appState.clearSession(['analysisResults', 'modelConfigurations', 'modelTags']);

    // Re-render the step to show the prerequisite modal
    renderStepContent();
}

/**
 * Toggle the source tables help section visibility
 */
function toggleSourceTablesHelp() {
    toggleHelpSection('help-content-source-tables', 'help-chevron-source-tables');
}
