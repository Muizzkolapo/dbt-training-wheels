// ============================================
// STEP 8: DEFINE SOURCES
// ============================================

async function renderSources(container) {
    if (!analysisResults) {
        container.innerHTML = '<div class="dbt-page-card"><p class="dbt-hint">Please complete the analysis first.</p></div>';
        return;
    }

    // Show loading state
    container.innerHTML = '<div class="dbt-page-card"><p class="dbt-hint">Loading sources preview...</p></div>';

    // Fetch filtered sources.yml from backend
    let sourcesYaml;
    try {
        const projectParam = userDomainName ? `?project=${encodeURIComponent(userDomainName)}` : '';
        const response = await errorHandler.safeFetch(`/api/preview-sources/${currentQuery.id}${projectParam}`);
        sourcesYaml = response.sources_yml;
    } catch (error) {
        console.error('Error fetching sources preview:', error);
        // Fall back to client-side generation if fetch fails
        const sourcesByDataset = {};
        if (analysisResults.hardcodedTables) {
            analysisResults.hardcodedTables.forEach(table => {
                // Skip self-references and cross-project refs
                if (!table.isSelfReference && !table.isCrossProjectRef) {
                    const parts = table.table.split('.');
                    const dataset = parts.length >= 2 ? parts[parts.length - 2] : 'default';
                    const tableName = parts[parts.length - 1];

                    if (!sourcesByDataset[dataset]) {
                        sourcesByDataset[dataset] = [];
                    }
                    if (!sourcesByDataset[dataset].includes(tableName)) {
                        sourcesByDataset[dataset].push(tableName);
                    }
                }
            });
        }

        sourcesYaml = 'version: 2\n\nsources:';
        Object.keys(sourcesByDataset).forEach(dataset => {
            sourcesYaml += `\n  - name: ${dataset}`;
            sourcesYaml += `\n    # Optional: Add description for this source`;
            sourcesYaml += `\n    # description: "Raw data from ${dataset}"`;
            sourcesYaml += `\n    tables:`;
            sourcesByDataset[dataset].forEach(tableName => {
                sourcesYaml += `\n      - name: ${tableName}`;
            });
        });
    }

    // Filter out self-references and cross-project refs (only show true external sources)
    const externalTables = analysisResults.hardcodedTables ?
        analysisResults.hardcodedTables.filter(t => !t.isSelfReference && !t.isCrossProjectRef) : [];

    container.innerHTML = `
        <div class="dbt-page-card">
            <div class="dbt-page-header">
                <h3 class="dbt-page-title">Define Sources</h3>
                <p class="dbt-page-subtitle">Create a sources.yml file to register your external BigQuery tables with dbt</p>
            </div>

            <!-- Beginner Help Section -->
            <div class="dbt-help-section">
                <button onclick="toggleSourcesHelp()" class="dbt-help-toggle">
                    <h4 class="dbt-help-title">
                        <svg class="dbt-w-4 dbt-h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                        What is sources.yml and why do I need it?
                    </h4>
                    <svg id="help-chevron-step6" class="dbt-chevron" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                    </svg>
                </button>
                <div id="help-content-step6" class="dbt-help-content hidden" style="font-size: 0.875rem; line-height: 1.6; color: #374151;">

                    <!-- Definition -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Definition</h5>
                        <p style="margin: 0;">The <code style="background: #f3f4f6; padding: 0.125rem 0.375rem; border-radius: 3px; font-size: 0.8rem;">sources.yml</code> file tells dbt about the external tables your models depend on. It's the configuration that makes your <code style="background: #f3f4f6; padding: 0.125rem 0.375rem; border-radius: 3px; font-size: 0.8rem;">{{ source() }}</code> calls work.</p>
                    </div>

                    <!-- Analogy -->
                    <div style="margin-bottom: 1.25rem; padding: 0.75rem; background: #f9fafb; border-radius: 6px;">
                        <p style="margin: 0;"><strong>Think of it like a contact book.</strong> Instead of memorizing phone numbers (table names), you save them under friendly names. When you need to call someone, you look them up by name. If they change their number, you update it in one place.</p>
                    </div>

                    <!-- What sources.yml contains -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">What goes in sources.yml?</h5>
                        <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem;">
                            <li><strong>Source name</strong> — A friendly name for a group of tables (usually the dataset name)</li>
                            <li><strong>Table definitions</strong> — Each raw table you're pulling from</li>
                            <li><strong>Optional: Descriptions</strong> — Documentation that appears in dbt docs</li>
                            <li><strong>Optional: Freshness checks</strong> — Alerts when data is stale</li>
                        </ul>
                    </div>

                    <!-- Why use sources.yml -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Why create a sources.yml file?</h5>
                        <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem;">
                            <li><strong>Lineage tracking</strong> — dbt shows where your data comes from</li>
                            <li><strong>Freshness monitoring</strong> — Get alerts when source data is late</li>
                            <li><strong>Documentation</strong> — Describe what each source table contains</li>
                            <li><strong>Single source of truth</strong> — Change table names in one place</li>
                        </ul>
                    </div>

                    <!-- Where to place it -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Where does this file go?</h5>
                        <p style="margin: 0; font-size: 0.8rem;">Place it in your <code style="background: #f3f4f6; padding: 0.125rem 0.375rem; border-radius: 3px; font-size: 0.75rem;">models/</code> directory, typically alongside the models that use these sources.</p>
                    </div>

                    <!-- Link to docs -->
                    <div>
                        <p style="margin: 0; font-size: 0.8rem;">
                            <a href="https://docs.getdbt.com/docs/build/sources" target="_blank" style="color: #2563eb; text-decoration: none;">
                                → Read dbt's sources documentation
                            </a>
                        </p>
                    </div>
                </div>
            </div>

            <!-- Tables to Register -->
            ${externalTables.length > 0 ? `
            <div class="dbt-mb-6">
                <h4 class="dbt-section-title-lg">External Tables Found (${externalTables.length})</h4>
                <div class="dbt-callout">
                    <div class="dbt-space-y-2">
                        ${externalTables.map(table => `
                            <div class="dbt-model-card dbt-flex-between">
                                <div class="dbt-flex-center dbt-gap-md">
                                    <svg class="dbt-w-5 dbt-h-5 dbt-text-green" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4"></path>
                                    </svg>
                                    <div>
                                        <div class="dbt-model-card-title">${table.table}</div>
                                        <div class="dbt-hint">→ ${table.suggestedSource}</div>
                                    </div>
                                </div>
                                <span class="dbt-badge dbt-badge-source">External Source</span>
                            </div>
                        `).join('')}
                    </div>
                </div>
            </div>

            <!-- Generated sources.yml -->
            <div class="dbt-mb-6">
                <div class="dbt-flex-between dbt-mb-3">
                    <h4 class="dbt-section-title-lg dbt-mb-0">Your sources.yml File</h4>
                    <button onclick="copyToClipboard('sources-yaml-content', 'copy-sources-btn')" id="copy-sources-btn" class="dbt-btn dbt-btn-primary dbt-btn-sm">
                        <svg class="dbt-w-4 dbt-h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path>
                        </svg>
                        Copy YAML
                    </button>
                </div>
                <div class="dbt-code-block">
                    <div class="dbt-code-block-header">
                        <span>sources.yml</span>
                        <span>YAML</span>
                    </div>
                    <div class="dbt-code-block-content">
                        <pre id="sources-yaml-content"><code>${escapeHtml(sourcesYaml)}</code></pre>
                    </div>
                </div>
            </div>

            <!-- Optional: Advanced Configuration -->
            <div class="dbt-mb-6">
                <button onclick="toggleAdvancedSources()" class="dbt-flex-center dbt-gap-sm dbt-hint">
                    <svg id="advanced-sources-chevron" class="dbt-chevron dbt-chevron-sm rotate-90" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"></path>
                    </svg>
                    Show advanced configuration options
                </button>
                <div id="advanced-sources-content" class="hidden dbt-mt-4">
                    <div class="dbt-callout">
                        <h5 class="dbt-section-title">Optional Enhancements</h5>
                        <div class="dbt-space-y-3 dbt-text-sm">
                            <div>
                                <div class="dbt-font-medium">Add Freshness Checks:</div>
                                <pre class="dbt-code-inline dbt-code-block-pre dbt-mt-1">freshness:
  warn_after: {count: 12, period: hour}
  error_after: {count: 24, period: hour}</pre>
                            </div>
                            <div>
                                <div class="dbt-font-medium">Add Column Descriptions:</div>
                                <pre class="dbt-code-inline dbt-code-block-pre dbt-mt-1">columns:
  - name: id
    description: "Primary key"
  - name: created_at
    description: "Timestamp when record was created"</pre>
                            </div>
                            <div>
                                <div class="dbt-font-medium">Add Tests:</div>
                                <pre class="dbt-code-inline dbt-code-block-pre dbt-mt-1">columns:
  - name: id
    tests:
      - unique
      - not_null</pre>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            ` : `
            <!-- No External Tables -->
            <div class="dbt-callout dbt-callout-primary dbt-success-card">
                <svg class="dbt-success-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                </svg>
                <h4 class="dbt-success-title">No External Sources Needed!</h4>
                <p class="dbt-success-text">This query doesn't reference any external tables that need to be defined as sources.</p>
            </div>
            `}

            ${renderNavFooter({ saveBeforeNav: true })}
        </div>
    `;

    // Mark sources step as completed for deploy checklist
    stepCompletionState['sources'] = { sourcesViewed: true };
}

// Toggle sources help section
function toggleSourcesHelp() {
    toggleHelpSection('help-content-step6', 'help-chevron-step6');
}

// Toggle advanced sources configuration
function toggleAdvancedSources() {
    const content = document.getElementById('advanced-sources-content');
    const chevron = document.getElementById('advanced-sources-chevron');
    if (content && chevron) {
        content.classList.toggle('hidden');
        chevron.classList.toggle('rotate-90');
    }
}

// Step 8: Review & Deploy (renamed from renderStep5)
