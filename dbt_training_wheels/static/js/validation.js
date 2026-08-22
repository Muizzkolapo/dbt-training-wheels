// ============================================
// COMPLETION VALIDATION FUNCTIONS
// ============================================
// Validators use string step IDs matching config.py

// Step: Analyze SQL
function validateAnalyzeCompletion() {
    return [
        {
            id: 'analysis-run',
            text: 'Analysis completed successfully',
            completed: analysisResults !== null
        },
        {
            id: 'tables-detected',
            text: 'Final tables detected',
            completed: analysisResults?.finalTableSqls?.length > 0
        },
        {
            id: 'sources-identified',
            text: 'External data sources identified',
            completed: analysisResults?.hardcodedTables !== undefined
        }
    ];
}

// Step: Final Models
function validateFinalModelsCompletion() {
    const finalModelCount = currentQuery?.tables?.length || 0;
    return [
        {
            id: 'final-models-identified',
            text: `Final models identified (${finalModelCount})`,
            completed: finalModelCount > 0
        },
        {
            id: 'pattern-reviewed',
            text: 'Pattern reviewed',
            completed: stepCompletionState['final-models']?.guidanceViewed === true
        }
    ];
}

// Step: Materialization
function validateMaterializationCompletion() {
    const allModels = getAllModels();
    const modelCount = allModels.length;
    const configuredCount = Object.keys(modelConfigurations).length;
    const allConfigured = modelCount > 0 && configuredCount === modelCount;

    return [
        {
            id: 'models-exist',
            text: `Models identified (${modelCount} total: prep + final)`,
            completed: modelCount > 0
        },
        {
            id: 'materialization-selected',
            text: 'Materialization strategy selected for all models',
            completed: allConfigured
        },
        {
            id: 'configs-saved',
            text: 'Configurations saved',
            completed: configuredCount > 0
        }
    ];
}

// Step: Tags
function validateTagsCompletion() {
    const allModels = getAllModels();
    const modelCount = allModels.length;
    const allHaveTags = Object.values(modelConfigurations).every(config =>
        config.tags && config.tags.length > 0
    );

    return [
        {
            id: 'tags-assigned',
            text: 'At least one tag assigned to each model',
            completed: allHaveTags && Object.keys(modelConfigurations).length === modelCount
        },
        {
            id: 'tags-saved',
            text: 'Tag configurations saved',
            completed: Object.keys(modelConfigurations).length > 0
        }
    ];
}

// Step: Define Sources
function validateSourcesCompletion() {
    const hasExternalTables = analysisResults?.hardcodedTables?.some(t => !t.isSelfReference);

    return [
        {
            id: 'sources-detected',
            text: 'External sources detected',
            completed: hasExternalTables || analysisResults?.hardcodedTables !== undefined
        },
        {
            id: 'yaml-generated',
            text: 'Sources YAML content generated',
            completed: hasExternalTables === true || hasExternalTables === false
        },
        {
            id: 'yaml-reviewed',
            text: 'Sources configuration reviewed',
            completed: stepCompletionState['sources']?.sourcesYamlViewed === true || !hasExternalTables
        }
    ];
}

// Step: Review (with lineage)
function validateReviewCompletion() {
    return [
        {
            id: 'all-steps-done',
            text: 'All previous steps completed',
            completed: checkAllPreviousStepsComplete()
        },
        {
            id: 'configs-finalized',
            text: 'Model configurations finalized',
            completed: Object.keys(modelConfigurations).length > 0
        },
        {
            id: 'lineage-reviewed',
            text: 'Lineage diagram reviewed',
            completed: stepCompletionState['review']?.lineageViewed === true
        }
    ];
}

// Step: Deploy
function validateDeployCompletion() {
    return [
        {
            id: 'domain-specified',
            text: 'Domain name specified',
            completed: userDomainName && userDomainName.trim().length > 0
        },
        {
            id: 'ready-to-deploy',
            text: 'Ready for deployment',
            completed: analysisResults?.finalTableSqls?.length > 0
        }
    ];
}

// ============================================
// COMPLETION CHECKLIST - HELPER FUNCTIONS
// ============================================

// Validator mapping using string step IDs
const stepValidators = {
    'analyze': validateAnalyzeCompletion,
    'final-models': validateFinalModelsCompletion,
    'materialization': validateMaterializationCompletion,
    'tags': validateTagsCompletion,
    'sources': validateSourcesCompletion,
    'review': validateReviewCompletion,
    'deploy': validateDeployCompletion
};

// Master validation function (accepts string step ID)
function validateStepCompletion(stepId) {
    const validator = stepValidators[stepId];
    return validator ? validator() : [];
}

// Check if current step is complete
function isStepComplete(stepId) {
    const criteria = validateStepCompletion(stepId);
    return criteria.length > 0 && criteria.every(c => c.completed);
}

// Check if all previous steps are complete (before current step)
function checkAllPreviousStepsComplete() {
    const enabledSteps = StepRegistry.getEnabledSteps();
    const deployStep = enabledSteps.find(s => s.id === 'deploy');

    // Check all enabled steps except deploy
    for (const step of enabledSteps) {
        if (step.id === 'deploy') continue;
        const criteria = validateStepCompletion(step.id);
        if (!criteria.every(c => c.completed)) {
            return false;
        }
    }
    return true;
}

// ============================================
// COMPLETION CHECKLIST - RENDERING FUNCTIONS
// ============================================

// Render checklist for current step (accepts string step ID)
function renderCompletionChecklist(stepId) {
    const criteria = validateStepCompletion(stepId);
    const completedCount = criteria.filter(c => c.completed).length;
    const totalCount = criteria.length;
    const isComplete = completedCount === totalCount;

    return `
        <div class="completion-checklist bg-white border border-[#e5e5e5] rounded-lg p-4 mb-6">
            <div class="flex items-center justify-between mb-3">
                <h4 class="font-medium text-[#000000] flex items-center gap-2">
                    <svg class="w-4 h-4 text-[#4f46e5]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"></path>
                    </svg>
                    Completion Checklist
                </h4>
                <span class="text-xs font-medium ${isComplete ? 'text-[#4f46e5]' : 'text-[#666666]'}">
                    ${completedCount} of ${totalCount}
                </span>
            </div>
            <ul class="space-y-2">
                ${criteria.map(item => `
                    <li class="flex items-start gap-3 transition-all duration-200 ${item.completed ? 'opacity-100' : 'opacity-70'}">
                        <div class="flex-shrink-0 mt-0.5">
                            ${item.completed ?
                                '<svg class="w-5 h-5 text-[#4f46e5]" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>'
                                :
                                '<svg class="w-5 h-5 text-[#999999]" fill="none" stroke="currentColor" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-width="2"></circle></svg>'
                            }
                        </div>
                        <span class="text-sm ${item.completed ? 'text-[#000000] font-medium' : 'text-[#666666]'}">
                            ${item.text}
                        </span>
                    </li>
                `).join('')}
            </ul>
        </div>
    `;
}

// Update checklist in real-time
function updateCompletionChecklist(stepId) {
    const container = document.querySelector('.completion-checklist');
    if (!container) return;

    const checklistHTML = renderCompletionChecklist(stepId);
    const tempDiv = document.createElement('div');
    tempDiv.innerHTML = checklistHTML;
    container.replaceWith(tempDiv.firstElementChild);
}
