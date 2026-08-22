// ============================================
// STEP 10: DEPLOY
// ============================================

async function renderDeploy(container) {
    if (!analysisResults) {
        container.innerHTML = '<div class="bg-white rounded-lg border border-gray-200 p-6"><p class="text-gray-600">Please complete the analysis first.</p></div>';
        return;
    }

    // Calculate counts from getAllModels() for consistency with file preview
    const allModels = getAllModels();
    const intermediateCount = allModels.filter(m => m.layer === 'intermediate').length;
    const martCount = allModels.filter(m => m.layer === 'mart').length;
    const totalModels = allModels.length;

    // The domain is the folder this query was uploaded from - 'demo/sample1.sql' is
    // domain 'sample1' - matching how the backend lays the files out
    const domainValue = domainFromFilename(currentQuery?.filename) || currentQuery?.name || '';


    container.innerHTML = `
        <div class="dbt-page-card">
            <div class="dbt-page-header">
                <h3 class="dbt-page-title">Deploy Models</h3>
                <p class="dbt-page-subtitle">Add your ${totalModels} dbt models to your project</p>
            </div>

            <!-- Deployment Readiness Checklist -->
            <div id="deploy-checklist" class="mb-6"></div>

            <!-- Configuration Summary (from earlier steps) -->
            <div class="mb-6 p-4 bg-gray-50 rounded-lg border border-gray-200">
                <h4 class="text-sm font-medium text-gray-700 mb-3">Deployment Target</h4>
                <div class="space-y-2 text-sm">
                    ${githubConfig.enabled && githubConfig.auth_method === 'ssh' ? `
                    <div class="flex">
                        <span class="text-gray-500 w-24">Mode:</span>
                        <span class="font-mono text-gray-900 flex items-center gap-1">
                            <svg class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor">
                                <path fill-rule="evenodd" clip-rule="evenodd" d="M12 2C6.477 2 2 6.477 2 12c0 4.42 2.87 8.17 6.84 9.5.5.08.66-.23.66-.5v-1.69c-2.77.6-3.36-1.34-3.36-1.34-.46-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.6.07-.6 1 .07 1.53 1.03 1.53 1.03.87 1.52 2.34 1.07 2.91.83.09-.65.35-1.09.63-1.34-2.22-.25-4.55-1.11-4.55-4.92 0-1.11.38-2 1.03-2.71-.1-.25-.45-1.29.1-2.64 0 0 .84-.27 2.75 1.02.79-.22 1.65-.33 2.5-.33.85 0 1.71.11 2.5.33 1.91-1.29 2.75-1.02 2.75-1.02.55 1.35.2 2.39.1 2.64.65.71 1.03 1.6 1.03 2.71 0 3.82-2.34 4.66-4.57 4.91.36.31.69.92.69 1.85V21c0 .27.16.59.67.5C19.14 20.16 22 16.42 22 12A10 10 0 0012 2z"/>
                            </svg>
                            GitHub via SSH (${githubConfig.repository})
                        </span>
                    </div>
                    <div class="flex">
                        <span class="text-gray-500 w-24">Branch:</span>
                        <span class="font-mono text-gray-900">${userGitHubBranch || 'Not configured'}</span>
                    </div>
                    ` : `
                    <div class="flex">
                        <span class="text-gray-500 w-24">Project:</span>
                        <span class="font-mono text-gray-900">${userDbtProjectPath || 'Not configured'}</span>
                    </div>
                    `}
                </div>
            </div>

            <!-- Where models will be saved. The domain comes from the folder this query
                 was uploaded from, so there's nothing to type. -->
            <div class="mb-6">
                <h4 class="block text-sm font-medium text-gray-700 mb-2">Destination</h4>
                <p class="text-sm text-gray-500">
                    Files will be saved to: <code class="text-xs bg-gray-100 px-2 py-1 rounded font-mono">models/<span id="domain-display">${escapeHtml(domainValue)}</span>/</code>
                </p>
                <p class="text-xs text-gray-400 mt-1">
                    <code>${escapeHtml(domainValue)}</code> comes from the folder you uploaded.
                    Upload subfolders to split models across domains.
                </p>
            </div>

            <!-- Help Section -->
            <div class="dbt-help-section">
                <button onclick="toggleDeployHelp()" class="dbt-help-toggle">
                    <h4 class="dbt-help-title">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                        What happens when I deploy?
                    </h4>
                    <svg id="deploy-help-chevron" class="dbt-chevron" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                    </svg>
                </button>
                <div id="deploy-help-content" class="dbt-help-content hidden" style="font-size: 0.875rem; line-height: 1.6; color: #374151;">

                    <!-- Definition -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Definition</h5>
                        <p style="margin: 0;">Deployment creates the actual SQL files in your dbt project folder. These files contain the transformed SQL with all your configurations (materializations, schemas, tags) baked in.</p>
                    </div>

                    <!-- Analogy -->
                    <div style="margin-bottom: 1.25rem; padding: 0.75rem; background: #f9fafb; border-radius: 6px;">
                        <p style="margin: 0;"><strong>Think of it like publishing a document.</strong> You've drafted and edited your SQL transformations. Now you're saving the final version to the project folder where dbt can find and run them.</p>
                    </div>

                    <!-- What gets created -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">What gets created?</h5>
                        <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem;">
                            <li><strong>models/{domain}/intermediate/*.sql</strong> — Transformation logic (joins, filters, calculations)</li>
                            <li><strong>models/{domain}/marts/*.sql</strong> — Final outputs that SELECT from intermediate</li>
                            <li><strong>models/sources.yml</strong> — Definitions for external tables, shared by every domain</li>
                        </ul>
                    </div>

                    <!-- Next steps -->
                    <div style="margin-bottom: 1.25rem;">
                        <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">After deployment</h5>
                        <ol style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem;">
                            <li><strong>Verify syntax:</strong> Run <code style="background: #f3f4f6; padding: 0.125rem 0.375rem; border-radius: 3px; font-size: 0.75rem;">dbt compile</code> to check for errors</li>
                            <li><strong>Test one model:</strong> Run <code style="background: #f3f4f6; padding: 0.125rem 0.375rem; border-radius: 3px; font-size: 0.75rem;">dbt run --select model_name</code></li>
                            <li><strong>Review compiled SQL:</strong> Check <code style="background: #f3f4f6; padding: 0.125rem 0.375rem; border-radius: 3px; font-size: 0.75rem;">target/compiled/</code> to see final queries</li>
                            <li><strong>Run all models:</strong> Run <code style="background: #f3f4f6; padding: 0.125rem 0.375rem; border-radius: 3px; font-size: 0.75rem;">dbt run --select tag:your_tag</code></li>
                        </ol>
                    </div>

                    <!-- Link to docs -->
                    <div>
                        <p style="margin: 0; font-size: 0.8rem;">
                            <a href="https://docs.getdbt.com/docs/running-a-dbt-project/run-your-dbt-projects" target="_blank" style="color: #2563eb; text-decoration: none;">
                                → Read dbt's guide to running projects
                            </a>
                        </p>
                    </div>
                </div>
            </div>

            <!-- DECLARE Variables Reminder -->
            ${analysisResults.declareVariables && analysisResults.declareVariables.length > 0 ? `
            <div class="dbt-callout mt-6" style="border-left: 4px solid #f59e0b; background: #fffbeb;">
                <div style="display: flex; align-items: flex-start; gap: 0.75rem;">
                    <svg class="w-5 h-5" style="flex-shrink: 0; color: #f59e0b; margin-top: 2px;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
                    </svg>
                    <div style="flex: 1;">
                        <h4 style="font-weight: 600; color: #b45309; margin: 0 0 0.5rem 0;">⚠️ Action Required: DECLARE Variables</h4>
                        <p style="margin: 0 0 0.5rem 0; color: #374151; font-size: 0.875rem;">
                            Your SQL uses <strong>DECLARE</strong> statements. dbt doesn't support these, so you'll need to convert them. Here are your options:
                        </p>
                        <div style="background: #fef3c7; border-radius: 0.375rem; padding: 0.5rem 0.75rem; margin-bottom: 0.75rem;">
                            <p style="font-weight: 600; margin: 0 0 0.5rem 0; font-size: 0.75rem; color: #92400e;">Variables found in your SQL:</p>
                            <ul style="margin: 0; padding-left: 1.25rem; font-family: monospace; font-size: 0.75rem;">
                                ${analysisResults.declareVariables.map(v => `
                                    <li><strong>${escapeHtml(v.variable)}</strong> (${escapeHtml(v.type)}) = ${escapeHtml(v.defaultValue)}</li>
                                `).join('')}
                            </ul>
                        </div>

                        <div style="background: white; border-radius: 0.375rem; padding: 0.75rem; border: 1px solid #fcd34d;">
                            <p style="font-weight: 600; margin: 0 0 0.5rem 0; font-size: 0.75rem; color: #92400e;">How to fix:</p>
                            <div style="font-size: 0.75rem; color: #374151; space-y: 0.5rem;">
                                <p style="margin: 0.25rem 0;"><strong>Option 1:</strong> Move to <code style="background: #fef3c7; padding: 0.125rem 0.375rem; border-radius: 0.25rem;">dbt_project.yml</code> vars section:</p>
                                <pre style="background: #f3f4f6; padding: 0.5rem; border-radius: 0.25rem; margin: 0.25rem 0; font-size: 0.7rem; overflow-x: auto;">vars:
  my_date: '2024-01-01'
  threshold: 100</pre>
                                <p style="margin: 0.5rem 0 0.25rem 0;"><strong>Option 2:</strong> Use Jinja at top of model:</p>
                                <pre style="background: #f3f4f6; padding: 0.5rem; border-radius: 0.25rem; margin: 0.25rem 0; font-size: 0.7rem; overflow-x: auto;">{% set my_date = '2024-01-01' %}
{% set threshold = 100 %}</pre>
                                <p style="margin: 0.5rem 0 0.25rem 0;"><strong>Option 3:</strong> Replace with literal values if they're constants</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            ` : ''}

            <!-- Deploy Actions -->
            <div class="mt-6 space-y-4">
                <!-- GitHub Push -->
                <div id="github-push-section">
                    <div class="p-4 bg-gray-50 rounded-lg border border-gray-200">
                        <h4 class="text-sm font-medium text-gray-700 mb-3 flex items-center gap-2">
                            <svg class="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
                                <path fill-rule="evenodd" clip-rule="evenodd" d="M12 2C6.477 2 2 6.477 2 12c0 4.42 2.87 8.17 6.84 9.5.5.08.66-.23.66-.5v-1.69c-2.77.6-3.36-1.34-3.36-1.34-.46-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.6.07-.6 1 .07 1.53 1.03 1.53 1.03.87 1.52 2.34 1.07 2.91.83.09-.65.35-1.09.63-1.34-2.22-.25-4.55-1.11-4.55-4.92 0-1.11.38-2 1.03-2.71-.1-.25-.45-1.29.1-2.64 0 0 .84-.27 2.75 1.02.79-.22 1.65-.33 2.5-.33.85 0 1.71.11 2.5.33 1.91-1.29 2.75-1.02 2.75-1.02.55 1.35.2 2.39.1 2.64.65.71 1.03 1.6 1.03 2.71 0 3.82-2.34 4.66-4.57 4.91.36.31.69.92.69 1.85V21c0 .27.16.59.67.5C19.14 20.16 22 16.42 22 12A10 10 0 0012 2z"/>
                            </svg>
                            Push to GitHub
                        </h4>
                        <div class="space-y-3">
                            <div class="p-3 bg-white rounded border border-gray-200">
                                <div class="flex items-center justify-between">
                                    <span class="text-sm text-gray-600">Branch:</span>
                                    <span id="github-branch-display" class="font-mono text-sm font-medium text-gray-900">${userGitHubBranch || 'Not set'}</span>
                                </div>
                            </div>
                            <input type="hidden" id="github-branch-name" value="${userGitHubBranch || ''}">
                            <div id="sibling-stack-option"></div>
                            <div>
                                <label for="github-base-branch" class="block text-sm text-gray-600 mb-1">
                                    Base branch
                                    <span class="text-gray-400 font-normal">(optional)</span>
                                </label>
                                <input
                                    type="text"
                                    id="github-base-branch"
                                    class="w-full px-3 py-2 border border-gray-300 rounded-md text-sm font-mono"
                                    placeholder="${githubConfig.default_branch || 'main'}"
                                />
                                <p class="text-xs text-gray-500 mt-1">
                                    Leave empty to branch off <code>${githubConfig.default_branch || 'main'}</code>.
                                    Set it to stack onto an existing branch instead.
                                </p>
                            </div>
                            <div class="flex items-center gap-2">
                                <input type="checkbox" id="github-create-pr" class="rounded border-gray-300">
                                <label for="github-create-pr" class="text-sm text-gray-600">Create Pull Request</label>
                            </div>
                            <div id="pr-capability"></div>
                            <button onclick="pushToGitHub(event)" class="dbt-deploy-btn w-full" style="background-color: #24292e; color: white;">
                                <svg class="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
                                    <path fill-rule="evenodd" clip-rule="evenodd" d="M12 2C6.477 2 2 6.477 2 12c0 4.42 2.87 8.17 6.84 9.5.5.08.66-.23.66-.5v-1.69c-2.77.6-3.36-1.34-3.36-1.34-.46-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.6.07-.6 1 .07 1.53 1.03 1.53 1.03.87 1.52 2.34 1.07 2.91.83.09-.65.35-1.09.63-1.34-2.22-.25-4.55-1.11-4.55-4.92 0-1.11.38-2 1.03-2.71-.1-.25-.45-1.29.1-2.64 0 0 .84-.27 2.75 1.02.79-.22 1.65-.33 2.5-.33.85 0 1.71.11 2.5.33 1.91-1.29 2.75-1.02 2.75-1.02.55 1.35.2 2.39.1 2.64.65.71 1.03 1.6 1.03 2.71 0 3.82-2.34 4.66-4.57 4.91.36.31.69.92.69 1.85V21c0 .27.16.59.67.5C19.14 20.16 22 16.42 22 12A10 10 0 0012 2z"/>
                                </svg>
                                Push to GitHub Branch
                            </button>
                        </div>
                    </div>
                </div>

            </div>

            <!-- Navigation -->
            ${renderNavFooter()}
        </div>
    `;

    // Mark deployment page as viewed for completion state
    stepCompletionState['deploy'] = { deploymentReady: true };

    // Render the deployment readiness checklist
    // This demonstrates the Checklist component usage for non-technical users
    Checklist.render('deploy-checklist', {
        title: 'Deployment Readiness',
        subtitle: 'Review these items before deploying',
        items: [
            {
                id: 'domain',
                text: 'Domain/folder name specified',
                completed: !!userDomainName,
                description: userDomainName ? `Using: ${userDomainName}` : 'Set in Step 1'
            },
            {
                id: 'models',
                text: 'Models configured',
                completed: totalModels > 0,
                description: `${totalModels} model${totalModels !== 1 ? 's' : ''} ready`
            },
            {
                id: 'tags',
                text: 'Tags assigned',
                completed: !!(stepCompletionState['tags']?.tagsConfigured),
                description: 'Model tags set in Step 6'
            },
            {
                id: 'sources',
                text: 'Sources reviewed',
                completed: !!(stepCompletionState['sources']?.sourcesViewed),
                description: 'Source tables verified'
            }
        ],
        interactive: false,  // Read-only status display
        showProgress: true
    });

    // Check if GitHub is configured and show the push section
    initGitHubSection();
}



// Toggle deploy help visibility
function toggleDeployHelp() {
    toggleHelpSection('deploy-help-content', 'deploy-help-chevron');
}

// Check if GitHub is configured and show/hide the push section
async function initGitHubSection() {
    const section = document.getElementById('github-push-section');
    if (!section) return;

    try {
        const response = await fetch('/api/github/status');
        const data = await response.json();

        if (data.enabled && data.auth_method === 'ssh') {
            section.classList.remove('hidden');
            // Set default branch name
            const branchInput = document.getElementById('github-branch-name');
            if (branchInput && !branchInput.value) {
                const queryName = currentQuery?.name?.toLowerCase().replace(/[^a-z0-9]/g, '-') || 'models';
                branchInput.placeholder = `${data.branch_prefix}${queryName}`;
            }
            renderPullRequestCapability(data.pull_requests);
            await initSiblingStackOption();
        }
    } catch (err) {
        console.log('GitHub integration not available:', err);
    }
}

// Say up front whether pull requests can be opened. Pushing branches needs only the
// SSH key; opening PRs needs the GitHub CLI authenticated, which often it isn't.
function renderPullRequestCapability(capability) {
    const container = document.getElementById('pr-capability');
    if (!container || !capability) return;

    container.innerHTML = capability.available
        ? `
            <p class="text-xs text-green-700">
                Pull requests will be created and linked as a stack automatically
                <span class="text-green-600">(gh ${escapeHtml(capability.gh_version)})</span>.
            </p>
        `
        : `
            <p class="text-xs text-gray-500">
                Branches will be pushed, but pull requests won't be opened automatically &mdash;
                ${escapeHtml(capability.reason || 'the GitHub CLI is unavailable')}.
                You'll get a pre-filled link per branch instead.
            </p>
        `;
}

// Show what this deploy will push. There's no choice to make - one uploaded folder
// is one deploy - so this is a plan, not a control.
async function initSiblingStackOption() {
    const container = document.getElementById('sibling-stack-option');
    if (!container || !currentQuery) return;

    const conversion = appState.get('currentConversion');
    const queries = conversion?.queries || [];

    if (queries.length < 2) {
        container.innerHTML = '';
        return;
    }

    // Domains that feed off each other stack; domains that share nothing don't.
    // The backend decides this from the SQL, so show its answer rather than recomputing.
    const groups = conversion?.groups?.length
        ? conversion.groups
        : [{ domains: queries.map(q => domainFromFilename(q.filename)) }];

    // Same visual language as the sidebar and the download preview: a chain of chips
    // joined by arrows is "merges in this order"; a lone chip is its own pull request.
    const groupBlocks = groups.map((group, groupIndex) => {
        const domains = group.domains || [];
        const stacked = domains.length > 1;

        const chain = domains.map((domain, index) => `
            ${index > 0 ? '<svg class="w-3.5 h-3.5 text-[#4f46e5] shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5-5 5M6 12h12"/></svg>' : ''}
            <span class="px-2 py-0.5 rounded bg-white border border-[#e5e5e5] text-xs font-medium text-[#000000]">${escapeHtml(domain)}</span>
        `).join('');

        return `
            <div class="mt-2 first:mt-0 p-2.5 bg-[#fafaf7] border border-[#e5e5e5] rounded">
                <div class="flex items-center justify-between mb-1.5">
                    <span class="text-[10px] font-semibold uppercase tracking-wide text-[#999999]">
                        ${groups.length > 1 ? `Group ${groupIndex + 1}` : 'One group'}
                    </span>
                    <span class="text-[10px] ${stacked ? 'text-[#b8860b]' : 'text-[#3730a3]'}">
                        ${stacked ? `${domains.length} stacked pull requests` : 'own pull request'}
                    </span>
                </div>
                <div class="flex items-center flex-wrap gap-1.5">${chain}</div>
                ${stacked ? `
                    <p class="text-[11px] text-[#999999] mt-1.5">
                        \u201c${escapeHtml(domains[domains.length - 1])}\u201d reads a table
                        \u201c${escapeHtml(domains[0])}\u201d creates, so it merges after it.
                    </p>
                ` : ''}
            </div>
        `;
    }).join('');

    const summary = groups.length > 1
        ? `${queries.length} domains in ${groups.length} independent groups \u2014 groups don't wait
           on each other, each starts from your base branch:`
        : `${queries.length} domains, merging in dependency order:`;

    container.innerHTML = `
        <div class="p-3 bg-[#f8fafc] border border-[#e5e5e5] rounded-lg">
            <p class="text-sm font-medium text-[#000000]">
                This deploys the whole <code class="bg-white px-1 rounded">${escapeHtml(conversion.name)}</code> conversion
            </p>
            <p class="text-xs text-[#666666] mt-1">${summary}</p>
            <div class="mt-2">${groupBlocks}</div>
        </div>
    `;
}

// Push files to GitHub
async function pushToGitHub(event, forcePush = false) {
    event.preventDefault();

    const branchInput = document.getElementById('github-branch-name');
    const createPrCheckbox = document.getElementById('github-create-pr');

    // Use the branch from input, or fall back to the saved userGitHubBranch
    const branchName = branchInput?.value?.trim() || userGitHubBranch || '';
    const createPr = createPrCheckbox?.checked || false;

    // Domain comes from the uploaded folder, not from user input. Sent so the backend
    // can label the dbt_project.yml block; it derives the same value if omitted.
    const domainArea = domainFromFilename(currentQuery?.filename);

    // Get model gruop from input (user can edit it in deploy step)
    const modelGroup = conversionNameFromFilename(currentQuery?.filename);

    // Get project name for config lookup (the project selected in the dropdown)
    const projectName = sessionStorage.getItem('dbt_training_wheels_domain_name') || '';

    if (!branchName) {
        showNotification('No branch name configured. Please set it in Step 1.', 'error');
        return;
    }

    // Show loading state
    const btn = event.target.closest('button');
    const originalContent = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `
        <svg class="animate-spin w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        Pushing to GitHub...
    `;

    try {
        console.log('[GitHub] Starting push request...');

        // Get user's mart selection from appState (which tables user selected for mart layer)
        const martSelection = appState.get('userMartSelection') || [];
        console.log('[GitHub] User mart selection:', martSelection);

        const response = await fetch(`/api/push-to-github/${currentQuery.id}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                branch_name: branchName,
                domain: domainArea,  // Folder path for models/{domain}/
                model_group: modelGroup, // Names the docs file, not a folder
                project: projectName,  // Project name for config/naming lookup
                create_pr: createPr,
                commit_message: `Add dbt models: ${currentQuery.name}`,
                user_mart_selection: martSelection,  // Pass user's table selection to backend
                // Replace branches left by a previous deploy of this conversion
                force_push: forcePush,
                // Empty means the repo's default branch
                base_branch: document.getElementById('github-base-branch')?.value?.trim() || ''
            })
        });

        console.log('[GitHub] Response status:', response.status);
        const result = await response.json();
        console.log('[GitHub] Response data:', result);

        if (!response.ok) {
            // Branches from a previous deploy of this conversion: offer to replace them,
            // which updates any open PRs in place instead of dead-ending
            if (!forcePush && result.error?.details?.can_overwrite) {
                btn.disabled = false;
                btn.innerHTML = originalContent;
                if (confirmOverwrite(result)) {
                    return pushToGitHub(event, true);
                }
                return;
            }
            throw new Error(result.error?.user_message || result.message || 'Push failed');
        }

        console.log('[GitHub] Success! Updating UI...');

        // Find the github push section and replace content
        const githubSection = document.getElementById('github-push-section');
        if (githubSection) {
            const pushContainer = githubSection.querySelector('.space-y-3');
            if (pushContainer) {
                pushContainer.innerHTML = renderPushSuccess(result);
            }
        }
        console.log('[GitHub] UI updated successfully');

    } catch (err) {
        console.error('GitHub push error:', err);

        // Show error in UI
        const githubSection = document.getElementById('github-push-section');
        if (githubSection) {
            const pushContainer = githubSection.querySelector('.space-y-3');
            if (pushContainer) {
                pushContainer.innerHTML = `
                    <div class="p-4 bg-red-50 border border-red-200 rounded-lg">
                        <div class="flex items-center gap-2 text-red-700 font-medium mb-2">
                            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                            </svg>
                            Push Failed
                        </div>
                        <p class="text-sm text-red-600">${err.message || 'Failed to push to GitHub'}</p>
                        <button onclick="location.reload()" class="mt-3 px-4 py-2 bg-red-100 text-red-700 rounded hover:bg-red-200 text-sm">
                            Try Again
                        </button>
                    </div>
                `;
            }
        }
    }
}

const EXTERNAL_LINK_ICON = `
    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path>
    </svg>
`;

const SUCCESS_HEADER_ICON = `
    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
    </svg>
`;

// Render the result of a single-branch push
function renderSinglePushSuccess(result) {
    const pr = result.pull_request;

    return `
        <div class="p-4 bg-green-50 border border-green-200 rounded-lg">
            <div class="flex items-center gap-2 text-green-700 font-medium mb-3">
                ${SUCCESS_HEADER_ICON}
                Pushed Successfully!
            </div>
            <div class="space-y-2">
                <a href="${result.branch_url}" target="_blank" class="flex items-center justify-center gap-2 px-4 py-3 bg-white text-gray-800 rounded-lg hover:bg-gray-50 border border-gray-200 transition-colors">
                    ${EXTERNAL_LINK_ICON}
                    View Branch on GitHub
                </a>
                ${pr ? `
                <a href="${pr.url}" target="_blank" class="flex items-center justify-center gap-2 px-4 py-3 bg-[#4f46e5] text-white rounded-lg hover:bg-[#4338ca] transition-colors">
                    ${EXTERNAL_LINK_ICON}
                    View Pull Request
                </a>
                ` : ''}
                ${!pr && result.pull_request_note ? `
                <p class="text-xs text-green-600">${escapeHtml(result.pull_request_note)}</p>
                ` : ''}
            </div>
            <p class="text-sm text-green-600 mt-3">${result.files_pushed} files pushed to <code class="bg-green-100 px-1 rounded">${escapeHtml(result.branch)}</code></p>
        </div>
    `;
}

// Show whether the pull requests were created and linked as a stack on GitHub,
// or why we fell back to the compare links above
function renderStackPrStatus(result) {
    const prs = result.pull_requests || [];

    if (prs.length) {
        const links = prs.map((url, index) => `
            <a href="${url}" target="_blank" class="text-[#4f46e5] hover:underline">#${index + 1}</a>
        `).join(' ');

        return `
            <p class="text-xs text-green-700 mt-3">
                ${prs.length} pull request(s) created and linked as a stack on GitHub: ${links}
            </p>
        `;
    }

    const reason = result.pr_linking && result.pr_linking.reason;
    if (!reason) return '';

    // Correct base branches don't make a stack - GitHub's stack is an object created
    // through the API, so PRs opened from the compare links won't carry the 1/2 badge
    const branches = (result.stack || []).map(entry => entry.branch);

    return `
        <div class="mt-3 space-y-2">
            <p class="text-xs text-gray-500">
                Pull requests weren't created automatically &mdash; ${escapeHtml(reason)}.
                Use the "Open PR" links above; each is pre-filled with the right base branch.
            </p>
            <p class="text-xs text-gray-500">
                They'll build on each other correctly, but GitHub won't show them as a linked
                stack until they're registered as one.
            </p>
            ${branches.length > 1 ? `
            <button
                onclick='linkStackOnGitHub(${JSON.stringify(branches)}, ${JSON.stringify(result.base_branch || "main")})'
                class="text-xs px-3 py-2 bg-white border border-gray-200 rounded-lg hover:bg-gray-50">
                Link them as a stack
            </button>
            <p class="text-xs text-gray-400">
                Works once the pull requests exist, and adopts them rather than opening new ones.
            </p>
            ` : ''}
        </div>
    `;
}

// Register already-pushed branches as a stack on GitHub, adopting any open PRs
async function linkStackOnGitHub(branches, baseBranch) {
    const response = await fetch('/api/link-stack', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ branches, base_branch: baseBranch })
    });

    const data = await response.json().catch(() => null);

    if (!response.ok) {
        errorHandler.showError(data);
        return;
    }

    const prs = data.pull_requests || [];
    showNavigationError(
        prs.length
            ? `Linked ${prs.length} pull request(s) into a stack`
            : 'Linked the branches into a stack',
        'success'
    );
}

// One deploy can produce several independent results - one per group of domains that
// feed off each other. Each renders as whatever it is: a stack, or a lone pull request.
function renderPushSuccess(result) {
    if (!result.is_grouped) {
        return result.is_stack ? renderStackPushSuccess(result) : renderSinglePushSuccess(result);
    }

    const groups = result.groups || [];
    const blocks = groups.map((group, index) => `
        <div class="mt-3 first:mt-0">
            <p class="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">
                Group ${index + 1} &mdash; ${escapeHtml((group.domains || []).join(', '))}
            </p>
            ${group.is_stack ? renderStackPushSuccess(group) : renderSinglePushSuccess(group)}
        </div>
    `).join('');

    return `
        <div class="p-3 bg-green-50 border border-green-200 rounded-lg">
            <div class="flex items-center gap-2 text-green-700 font-medium mb-1">
                ${SUCCESS_HEADER_ICON}
                Pushed ${groups.length} independent groups
            </div>
            <p class="text-sm text-green-700 mb-2">
                These groups share no tables, so none of them waits on another &mdash; each
                starts from <code class="bg-green-100 px-1 rounded">${escapeHtml(result.base_branch || 'the base branch')}</code>
                and can be reviewed and merged on its own.
            </p>
            ${blocks}
        </div>
    `;
}

// Render the result of a cross-domain stacked push, in merge order
function renderStackPushSuccess(result) {
    const stack = result.stack || [];

    const items = stack.map((entry, index) => `
        <li class="bg-white border border-gray-200 rounded-lg p-3">
            <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                    <div class="text-sm font-medium text-gray-800">
                        <span class="text-gray-400">${index + 1}.</span> ${escapeHtml(entry.name)}
                    </div>
                    <div class="text-xs text-gray-500 mt-0.5 truncate">
                        <code>${escapeHtml(entry.branch)}</code>
                        <span class="text-gray-400">onto</span>
                        <code>${escapeHtml(entry.base)}</code>
                    </div>
                    <div class="text-xs text-gray-500 mt-0.5">${entry.files_pushed} file(s)</div>
                </div>
                <div class="flex items-center gap-2 shrink-0">
                    <a href="${entry.compare_url}" target="_blank" class="flex items-center gap-1 px-3 py-2 bg-[#4f46e5] text-white text-sm rounded-lg hover:bg-[#4338ca] transition-colors">
                        ${EXTERNAL_LINK_ICON}
                        Open PR
                    </a>
                    <a href="${entry.branch_url}" target="_blank" class="flex items-center gap-1 px-3 py-2 bg-white text-gray-700 text-sm rounded-lg hover:bg-gray-50 border border-gray-200 transition-colors">
                        Branch
                    </a>
                </div>
            </div>
        </li>
    `).join('');

    return `
        <div class="p-4 bg-green-50 border border-green-200 rounded-lg">
            <div class="flex items-center gap-2 text-green-700 font-medium mb-1">
                ${SUCCESS_HEADER_ICON}
                Pushed a stack of ${stack.length} branches
            </div>
            <p class="text-sm text-green-700 mb-3">
                Your models span ${stack.length} domains, so each one got its own branch.
                Open the pull requests in this order &mdash; each targets the branch before it,
                so its diff shows only that domain's models.
            </p>
            <ol class="space-y-2">${items}</ol>
            ${renderStackPrStatus(result)}
            <p class="text-xs text-green-600 mt-3">
                ${result.files_pushed} file(s) pushed onto <code class="bg-green-100 px-1 rounded">${escapeHtml(result.base_branch)}</code>.
                Merge them in the same order: as each one merges, GitHub retargets the next onto
                <code class="bg-green-100 px-1 rounded">${escapeHtml(result.base_branch)}</code>.
            </p>
        </div>
    `;
}
