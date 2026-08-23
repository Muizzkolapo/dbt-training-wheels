// ============================================
// COMPLETION VALIDATION FUNCTIONS
// ============================================
// Validators use string step IDs matching config.py.
//
// Each validator returns a list of criteria: { id, text, completed, optional? }.
//
// `optional: true` means "a real default already applies here". An unmet optional
// criterion does NOT block -- it is the difference between the rail saying "this
// needs an answer from you" and "defaults stand, safe to skip". Materialization and
// tags are the two genuine cases: initializeModelConfigurations() gives every model
// a materialization, and a model with no tags is valid dbt.
//
// Everything else is required. A required criterion that is unmet blocks the step.

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

// Step: Staging Layer
//
// Mirrors renderLayerStaging()'s own source of truth -- layerClassification.staging,
// with the prefix applied -- rather than inventing a second one. A validator that
// disagrees with the renderer is worse than no validator.
//
// Zero staging models is a legitimate outcome, not an incomplete step: a script that
// reads raw tables and goes straight to joining them has nothing to stage.
function validateLayerStagingCompletion() {
    const components = analysisResults?.layerClassification?.staging || [];
    const prefix = analysisResults?.naming?.stagingModelPrefix || 'stg__';
    const described = components.filter(c => getSavedDescription(`${prefix}${c.name}`).length > 0).length;

    return [
        {
            id: 'staging-analysis-run',
            text: 'Analysis has run',
            completed: analysisResults !== null
        },
        {
            id: 'staging-described',
            text: components.length === 0
                ? 'No staging models — nothing to describe'
                : `Descriptions written (${described} of ${components.length})`,
            completed: described === components.length
        }
    ];
}

// Step: Intermediate Layer
// renderLayerIntermediate() reads getAllModels() rather than layerClassification,
// so this does too.
function validateLayerIntermediateCompletion() {
    const components = (typeof getAllModels === 'function' ? getAllModels() : [])
        .filter(m => m.layer === 'intermediate');
    const described = components.filter(m => getSavedDescription(m.name).length > 0).length;

    return [
        {
            id: 'intermediate-analysis-run',
            text: 'Analysis has run',
            completed: analysisResults !== null
        },
        {
            id: 'intermediate-described',
            text: components.length === 0
                ? 'No intermediate models — nothing to describe'
                : `Descriptions written (${described} of ${components.length})`,
            completed: described === components.length
        }
    ];
}

// Step: Mart Layer
// renderLayerMart() renders description inputs for martComponents only -- the
// finalSelect fallback governs the empty state, not the input list -- so only
// martComponents are validated here.
function validateLayerMartCompletion() {
    const components = (typeof getAllModels === 'function' ? getAllModels() : [])
        .filter(m => m.layer === 'mart');
    const described = components.filter(m => getSavedDescription(m.name).length > 0).length;

    return [
        {
            id: 'mart-models-exist',
            text: `Mart models identified (${components.length})`,
            completed: components.length > 0
        },
        {
            id: 'mart-described',
            text: components.length === 0
                ? 'No mart models identified yet'
                : `Descriptions written (${described} of ${components.length})`,
            completed: components.length > 0 && described === components.length
        }
    ];
}

// Step: Cross-Project References
//
// crossProjectRefsState is a top-level binding in steps/cross-project-refs.js, which
// is only loaded when the step is enabled -- hence the typeof guard.
//
// Note that decisions are pre-populated when refs are detected (each ref defaults to
// using the cross-project ref), so "decided" means a decision exists and will be
// applied, not that the user personally chose it.
function validateCrossProjectRefsCompletion() {
    const state = typeof crossProjectRefsState !== 'undefined' ? crossProjectRefsState : null;

    if (!state || !state.enabled) {
        return [
            {
                id: 'cross-project-not-enabled',
                text: 'Cross-project references are not enabled',
                completed: true
            }
        ];
    }

    const refs = state.crossProjectRefs || [];
    const decisions = state.decisions || {};
    const decided = refs.filter(r => decisions[r.original_reference]).length;

    return [
        {
            id: 'cross-project-scanned',
            text: 'Other projects scanned',
            completed: state.loaded === true
        },
        {
            id: 'cross-project-decided',
            text: refs.length === 0
                ? 'No cross-project references found'
                : `Decisions recorded (${decided} of ${refs.length})`,
            completed: decided === refs.length
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
            // Optional: initializeModelConfigurations() already gives every model a
            // materialization, so leaving this untouched is a real answer, not a gap.
            id: 'materialization-selected',
            text: 'Materialization chosen for all models',
            completed: allConfigured,
            optional: true
        },
        {
            id: 'configs-saved',
            text: 'Configurations saved',
            completed: configuredCount > 0,
            optional: true
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
            // Optional throughout: a model with no tags is valid dbt. Tags exist for
            // selective runs, so an untagged conversion is a choice, not an omission.
            id: 'tags-assigned',
            text: 'At least one tag assigned to each model',
            completed: allHaveTags && Object.keys(modelConfigurations).length === modelCount,
            optional: true
        },
        {
            id: 'tags-saved',
            text: 'Tag configurations saved',
            completed: Object.keys(modelConfigurations).length > 0,
            optional: true
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
    'layer-staging': validateLayerStagingCompletion,
    'layer-intermediate': validateLayerIntermediateCompletion,
    'layer-mart': validateLayerMartCompletion,
    'cross-project-refs': validateCrossProjectRefsCompletion,
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

// The three states the step rail reports. Position in the flow is NOT one of them --
// walking past a step tells you nothing about whether it has been answered.
//
//   blocked   -- a required criterion is unmet; this needs an answer from you
//   settled   -- every criterion is met
//   defaulted -- all required criteria are met, but some optional ones are not.
//                Defaults stand, so the step is safe to skip.
function getStepState(stepId) {
    const criteria = validateStepCompletion(stepId);

    // No validator means nothing to answer here.
    if (criteria.length === 0) return 'defaulted';

    if (criteria.some(c => !c.optional && !c.completed)) return 'blocked';

    return criteria.every(c => c.completed) ? 'settled' : 'defaulted';
}

// Count of steps still needing an answer, for the rail's blocker jump.
function getBlockedStepIds() {
    return StepRegistry.getEnabledSteps()
        .map(s => s.id)
        .filter(id => getStepState(id) === 'blocked');
}

// Steps that are done as far as the user is concerned -- answered, or safely
// running on defaults. This is what "N of 10 settled" counts.
function getSettledStepCount() {
    return StepRegistry.getEnabledSteps()
        .filter(s => getStepState(s.id) !== 'blocked')
        .length;
}

// Check if all previous steps are complete (before current step)
//
// Uses required criteria only. Deploy should not be held up because nobody added
// tags -- an unmet optional criterion means a default applies, not that work is
// outstanding. (This also removes an inconsistency: steps with no validator used to
// pass here via [].every() while isStepComplete() reported them incomplete.)
function checkAllPreviousStepsComplete() {
    const enabledSteps = StepRegistry.getEnabledSteps();

    for (const step of enabledSteps) {
        if (step.id === 'deploy') continue;
        if (getStepState(step.id) === 'blocked') {
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
