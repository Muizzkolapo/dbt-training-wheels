// ============================================
// STEP 7: TAGS
// ============================================

function renderTags(container) {
    if (!analysisResults) {
        container.innerHTML = '<div class="dbt-page-card"><p class="dbt-hint">Please complete the analysis first.</p></div>';
        return;
    }

    initializeModelConfigurations();

    loadTagsConfig().then(defaultTags => {
        // Initialize modelTags with defaults if not set
        const allModels = getAllModels();
        allModels.forEach((model, idx) => {
            if (!modelTags[idx]) {
                modelTags[idx] = [...defaultTags];
            }
            // Sync to modelConfigurations
            const config = getModelConfig(idx);
            if (!config.tags || config.tags.length === 0) {
                updateModelConfig(idx, 'tags', [...defaultTags]);
            }
        });

        container.innerHTML = `
            <div class="dbt-page-card">
                <div class="dbt-page-header">
                    <h3 class="dbt-page-title">Step ${getCurrentDisplayNum()}: Tags</h3>
                    <p class="dbt-page-subtitle">Add tags for selective model execution</p>
                </div>

                <!-- Help Section -->
                <div class="dbt-help-section">
                    <button onclick="toggleStepHelp(5)" class="dbt-help-toggle">
                        <h4 class="dbt-help-title">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                            </svg>
                            What are tags?
                        </h4>
                        <svg id="help-chevron-step5" class="dbt-chevron" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                        </svg>
                    </button>
                    <div id="help-content-step5" class="dbt-help-content hidden" style="font-size: 0.875rem; line-height: 1.6; color: #374151;">

                        <!-- Definition -->
                        <div style="margin-bottom: 1.25rem;">
                            <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Definition</h5>
                            <p style="margin: 0;">Tags are labels you attach to models so you can run specific groups together. Instead of running all models, you can run only the ones tagged <code style="background: #f3f4f6; padding: 0.125rem 0.375rem; border-radius: 3px; font-size: 0.8rem;">daily</code> or <code style="background: #f3f4f6; padding: 0.125rem 0.375rem; border-radius: 3px; font-size: 0.8rem;">finance</code>.</p>
                        </div>

                        <!-- Analogy -->
                        <div style="margin-bottom: 1.25rem; padding: 0.75rem; background: #f9fafb; border-radius: 6px;">
                            <p style="margin: 0;"><strong>Think of it like labels in Gmail.</strong> You tag emails as "Work" or "Personal" so you can find them later. Tags in dbt let you group models by schedule, team, data sensitivity, or any category you choose.</p>
                        </div>

                        <!-- Common use cases -->
                        <div style="margin-bottom: 1.25rem;">
                            <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Common tag categories</h5>
                            <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem;">
                                <li><strong>Scheduling</strong> — <code style="background: #f3f4f6; padding: 0.125rem 0.25rem; border-radius: 3px; font-size: 0.75rem;">hourly</code>, <code style="background: #f3f4f6; padding: 0.125rem 0.25rem; border-radius: 3px; font-size: 0.75rem;">daily</code>, <code style="background: #f3f4f6; padding: 0.125rem 0.25rem; border-radius: 3px; font-size: 0.75rem;">weekly</code></li>
                                <li><strong>Data governance</strong> — <code style="background: #f3f4f6; padding: 0.125rem 0.25rem; border-radius: 3px; font-size: 0.75rem;">pii</code>, <code style="background: #f3f4f6; padding: 0.125rem 0.25rem; border-radius: 3px; font-size: 0.75rem;">finance</code>, <code style="background: #f3f4f6; padding: 0.125rem 0.25rem; border-radius: 3px; font-size: 0.75rem;">sensitive</code></li>
                                <li><strong>Domain/team</strong> — <code style="background: #f3f4f6; padding: 0.125rem 0.25rem; border-radius: 3px; font-size: 0.75rem;">marketing</code>, <code style="background: #f3f4f6; padding: 0.125rem 0.25rem; border-radius: 3px; font-size: 0.75rem;">product</code>, <code style="background: #f3f4f6; padding: 0.125rem 0.25rem; border-radius: 3px; font-size: 0.75rem;">sales</code></li>
                            </ul>
                        </div>

                        <!-- How to use -->
                        <div style="margin-bottom: 1.25rem;">
                            <h5 style="font-size: 0.8rem; font-weight: 600; color: #111827; margin: 0 0 0.5rem 0;">Running models by tag</h5>
                            <div style="background: #f9fafb; border-radius: 6px; padding: 0.75rem; font-family: ui-monospace, monospace; font-size: 0.75rem;">
                                dbt run --select tag:daily<br>
                                dbt test --select tag:pii<br>
                                dbt run --select tag:finance+
                            </div>
                            <p style="margin: 0.5rem 0 0 0; font-size: 0.75rem; color: #6b7280;">The <code style="font-size: 0.7rem;">+</code> runs the model plus all downstream models.</p>
                        </div>

                        <!-- Link to docs -->
                        <div>
                            <p style="margin: 0; font-size: 0.8rem;">
                                <a href="https://docs.getdbt.com/reference/resource-configs/tags" target="_blank" style="color: #2563eb; text-decoration: none;">
                                    → Read dbt's tags documentation
                                </a>
                            </p>
                        </div>
                    </div>
                </div>

                <!-- Tags Configuration -->
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

                                    <div id="tag-selector-step5-${idx}" class="tag-selector">
                                        ${availableTags.map(tag => `
                                            <button type="button" class="tag-chip ${config.tags?.includes(tag) ? 'selected' : ''}" data-tag="${tag}" onclick="toggleTagStep5(${idx}, '${tag}')">
                                                <span class="tag-chip-icon">${config.tags?.includes(tag) ? '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>' : ''}</span>
                                                ${tag}
                                            </button>
                                        `).join('')}
                                        ${allowCustomTags ? `
                                            <div class="tag-custom-input">
                                                <input type="text" id="custom-tag-step5-${idx}" placeholder="+ Custom tag" onkeypress="if(event.key==='Enter'){addCustomTagStep5(${idx});event.preventDefault();}">
                                            </div>
                                        ` : ''}
                                    </div>
                                    <div id="selected-tags-step5-${idx}" class="selected-tags-preview ${config.tags?.length ? '' : 'hidden'}">
                                        ${(config.tags || []).map(tag => `
                                            <span class="selected-tag-badge">
                                                ${tag}
                                                <span class="remove-tag" onclick="removeTagStep5(${idx}, '${tag}')">&times;</span>
                                            </span>
                                        `).join('')}
                                    </div>

                                    <!-- Config Preview -->
                                    <div class="dbt-code-block dbt-mt-3">
                                        <div class="dbt-code-block-content">
                                            <div class="dbt-code-block-title dbt-mb-1">Config block (top of your model file):</div>
                                            <pre><code>{{
  config(
    materialized='<span id="preview-mat-step5-${idx}">${config.materialization}</span>'<span id="preview-schema-step5-${idx}">${config.schema ? `,\n    schema='${config.schema}'` : ''}</span><span id="preview-tags-step5-${idx}">${config.tags?.length ? `,\n    tags=[${config.tags.map(t => `'${t}'`).join(', ')}]` : ''}</span>
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

        // Mark tags step as completed for deploy checklist
        stepCompletionState['tags'] = { tagsConfigured: true };
    });
}

// Tag helper functions for Step 5
function toggleTagStep5(modelIdx, tag) {
    const config = getModelConfig(modelIdx);
    const tags = config.tags || [];
    const tagIndex = tags.indexOf(tag);

    if (tagIndex > -1) {
        tags.splice(tagIndex, 1);
    } else {
        tags.push(tag);
    }

    updateModelConfig(modelIdx, 'tags', tags);

    // Persist modelTags separately for backward compatibility
    modelTags[modelIdx] = tags;
    appState.set('modelTags', modelTags, { session: true });

    updateTagUIStep5(modelIdx);
}

function addCustomTagStep5(modelIdx) {
    const input = document.getElementById(`custom-tag-step5-${modelIdx}`);
    const tag = input.value.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '_');

    if (tag) {
        const config = getModelConfig(modelIdx);
        const tags = config.tags || [];
        if (!tags.includes(tag)) {
            tags.push(tag);
            updateModelConfig(modelIdx, 'tags', tags);

            // Persist modelTags separately for backward compatibility
            modelTags[modelIdx] = tags;
            appState.set('modelTags', modelTags, { session: true });

            updateTagUIStep5(modelIdx);
        }
    }
    input.value = '';
}

function removeTagStep5(modelIdx, tag) {
    const config = getModelConfig(modelIdx);
    const tags = config.tags || [];
    const tagIndex = tags.indexOf(tag);

    if (tagIndex > -1) {
        tags.splice(tagIndex, 1);
        updateModelConfig(modelIdx, 'tags', tags);

        // Persist modelTags separately for backward compatibility
        modelTags[modelIdx] = tags;
        appState.set('modelTags', modelTags, { session: true });

        updateTagUIStep5(modelIdx);
    }
}

function updateTagUIStep5(modelIdx) {
    const config = getModelConfig(modelIdx);
    const selectedTags = config.tags || [];

    // Update chip selection states
    document.querySelectorAll(`#tag-selector-step5-${modelIdx} .tag-chip`).forEach(chip => {
        const tag = chip.dataset.tag;
        if (selectedTags.includes(tag)) {
            chip.classList.add('selected');
            chip.querySelector('.tag-chip-icon').innerHTML = `
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
                </svg>`;
        } else {
            chip.classList.remove('selected');
            chip.querySelector('.tag-chip-icon').innerHTML = '';
        }
    });

    // Update selected tags preview
    const previewContainer = document.getElementById(`selected-tags-step5-${modelIdx}`);
    if (previewContainer) {
        if (selectedTags.length > 0) {
            previewContainer.innerHTML = selectedTags.map(tag => `
                <span class="selected-tag-badge">
                    ${tag}
                    <span class="remove-tag" onclick="removeTagStep5(${modelIdx}, '${tag}')">&times;</span>
                </span>
            `).join('');
            previewContainer.classList.remove('hidden');
        } else {
            previewContainer.innerHTML = '';
            previewContainer.classList.add('hidden');
        }
    }

    // Update config preview
    const previewTagsEl = document.getElementById(`preview-tags-step5-${modelIdx}`);
    if (previewTagsEl) {
        if (selectedTags.length > 0) {
            previewTagsEl.textContent = `,\n    tags=[${selectedTags.map(t => `'${t}'`).join(', ')}]`;
        } else {
            previewTagsEl.textContent = '';
        }
    }
}

// Step 7: Define Sources (renamed from renderStep4)
