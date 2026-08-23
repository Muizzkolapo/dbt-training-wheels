// ============================================
// DBT TRAINING WHEELS - MAIN APPLICATION FILE
// ============================================
//
// This file has been refactored into a modular structure:
//
// - utils.js: Shared state variables, ErrorHandler class, helper functions
// - validation.js: Step completion validation and checklist rendering
// - main.js: Step rendering functions, navigation, API calls (this file)
//
// Load order: utils.js → validation.js → main.js
// ============================================

// NOTE: errorHandler is initialized in utils.js and available globally

// Initialize when page loads
document.addEventListener('DOMContentLoaded', function() {
    console.log('DBT Conversion Interface loaded');

    // Initialize step registry with backend-provided steps
    if (typeof conversionSteps !== 'undefined') {
        StepRegistry.init(conversionSteps);
    }

    // Restore persisted state from sessionStorage
    appState.restoreFromSession();

    // Validate restored state
    const stateValid = appState.validateRestoredState();

    // Initialize prerequisite toggle button state
    const toggleBtn = document.getElementById('prerequisite-toggle-btn');
    if (toggleBtn) {
        updatePrerequisiteToggleButton(toggleBtn);
    }

    // Restore sidebar collapse state
    applySidebarState();

    // Render folder tree in sidebar
    renderQueryTree();

    // Restore previously selected query from sessionStorage
    const savedQueryId = sessionStorage.getItem('selectedQueryId');
    if (savedQueryId && scheduledQueries && scheduledQueries.length > 0) {
        const queryId = parseInt(savedQueryId);
        const queryExists = scheduledQueries.find(q => q.id === queryId);
        if (queryExists) {
            if (!stateValid) {
                console.warn('Restoring query with partial state; will attempt to hydrate from session data.');
            }
            console.log('Restoring previously selected query:', queryId);
            selectQuery(queryId, { preserveState: true });

            // If we have restored analysis results and current step, restore that view
            if (analysisResults && currentStep) {
                console.log('Restoring progress - Step:', currentStep);
                viewingAllSteps = false;
                updateStepNavigation();
                renderConversionSteps();
                renderStepContent();
            }
        }
    }

    // Close tooltips when clicking outside
    document.addEventListener('click', function(event) {
        if (!event.target.closest('.sql-help-btn') && !event.target.closest('.sql-tooltip-popup')) {
            document.querySelectorAll('.sql-tooltip-popup').forEach(tooltip => {
                tooltip.classList.add('hidden');
            });
        }
    });

    // Setup drag and drop for file upload
    setupDragAndDrop();
});

// The sidebar lists conversions - one uploaded folder is one entry. Its domains are
// shown underneath as outputs of the conversion, not as separate things to select.
function renderQueryTree() {
    const container = document.getElementById('query-tree');
    if (!container) return;

    const list = Array.isArray(conversions) ? conversions : [];
    container.innerHTML = list.map(renderConversionEntry).join('');
}

function escapeHtml(value) {
    const text = String(value ?? '');
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    };
    return text.replace(/[&<>"']/g, char => map[char]);
}

function renderConversionEntry(conversion) {
    const safeName = escapeHtml(conversion.name || '');
    const domId = safeName.replace(/[^a-zA-Z0-9_-]/g, '_');
    const queries = conversion.queries || [];
    const isSelected = queries.some(q => q.id === currentQuery?.id);

    // A single-domain conversion has nothing to break out - the conversion is the domain
    const groups = conversion.groups?.length
        ? conversion.groups
        : [{ domains: queries.map(q => domainFromFilename(q.filename)) }];

    // One line per group: a chain (base \u2192 features) when it runs in order,
    // a lone chip when it's independent. Arrows say "runs in order" without a label.
    const groupChains = queries.length > 1
        ? `
            <div class="mt-2 space-y-1">
                ${groups.map(group => `
                    <div class="flex items-center flex-wrap gap-y-1 text-xs">
                        ${(group.domains || []).map((domain, index) => `
                            ${index > 0 ? '<svg class="w-3 h-3 mx-0.5 text-[#cccccc] shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5-5 5M6 12h12"/></svg>' : ''}
                            <span class="px-1.5 py-0.5 rounded bg-[#f8fafc] text-[#666666] truncate max-w-[9rem]">${escapeHtml(domain)}</span>
                        `).join('')}
                    </div>
                `).join('')}
            </div>
        `
        : '';

    const summary = queries.length > 1
        ? `${queries.length} domains \u00b7 ${groups.length === 1 ? 'one group' : `${groups.length} independent groups`}`
        : `${queries[0]?.insertCount || 0} INSERT${(queries[0]?.insertCount || 0) > 1 ? 'S' : ''}`;

    // Not a <button>: the row holds its own clickable actions, and interactive
    // content inside a button is invalid and unreachable by keyboard
    return `
        <div
            onclick="selectConversion('${safeName}')"
            onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();selectConversion('${safeName}')}"
            role="button"
            tabindex="0"
            id="conversion-${domId}"
            class="query-item group w-full p-4 text-left border-b border-[#e5e5e5] hover:bg-[#f8fafc] transition-colors cursor-pointer ${isSelected ? 'bg-[#f0fff4] border-l-4 border-l-[#4f46e5]' : ''}"
            title="${safeName}"
        >
            <div class="flex items-start justify-between mb-1">
                <div class="flex items-center gap-2 min-w-0">
                    <svg class="w-4 h-4 text-[#999999] shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"></path>
                    </svg>
                    <span class="font-medium text-[#000000] text-sm truncate sidebar-collapsible">${safeName}</span>
                </div>
                <div class="flex items-center gap-1 shrink-0 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
                    <button
                        onclick="openGroupedDownload(event, '${safeName}')"
                        class="flex items-center justify-center w-6 h-6 rounded hover:bg-[#eef2ff] text-[#999999] hover:text-[#4f46e5]"
                        title="Download grouped SQL (no dbt conversion)"
                        aria-label="Download ${safeName} as grouped SQL"
                    >
                        <svg class="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5 5-5M12 15V3" />
                        </svg>
                    </button>
                    <button
                        onclick="deleteConversion(event, '${safeName}')"
                        class="flex items-center justify-center w-6 h-6 rounded hover:bg-red-100 text-red-500 hover:text-red-700"
                        title="Remove conversion"
                        aria-label="Remove ${safeName}"
                    >
                        <svg class="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M6 6l12 12M6 18L18 6" />
                        </svg>
                    </button>
                </div>
            </div>
            <div class="text-xs text-[#999999] sidebar-collapsible">${summary}</div>
            <div class="sidebar-collapsible">${groupChains}</div>
        </div>
    `;
}

// Open a conversion: its first domain drives the wizard, the rest travel with it
function selectConversion(name) {
    const conversion = (Array.isArray(conversions) ? conversions : []).find(m => m.name === name);
    if (!conversion || !conversion.queries?.length) return;

    appState.set('currentConversion', conversion);
    selectQuery(conversion.primary_query_id);
}

// Grouped download: the folder's SQL, unchanged, sorted into groups that feed off
// each other. This is the whole product for someone who isn't converting to dbt, so it
// gets a real surface: a preview of the groups and the exact zip layout, then the
// download - not a blind save the user only understands after opening the zip.

async function openGroupedDownload(event, name) {
    if (event?.stopPropagation) {
        event.stopPropagation();
        event.preventDefault();
    }

    const conversion = (Array.isArray(conversions) ? conversions : []).find(m => m.name === name);
    if (!conversion?.primary_query_id) return;

    let files;
    try {
        files = await errorHandler.safeFetch(`/api/grouped-source/${conversion.primary_query_id}`);
    } catch (error) {
        console.error('Error loading grouped source:', error);
        return;
    }

    renderGroupedDownloadModal(conversion, files);
}

function renderGroupedDownloadModal(conversion, files) {
    closeGroupedDownload();

    const safeName = escapeHtml(conversion.name || '');
    const groups = conversion.groups?.length
        ? conversion.groups
        : [{ domains: (conversion.queries || []).map(q => domainFromFilename(q.filename)) }];
    const sqlCount = files.filter(f => f.path.endsWith('.sql')).length;

    const groupCards = groups.map((group, index) => {
        const domains = group.domains || [];
        const ordered = domains.length > 1;

        const chain = domains.map((domain, i) => `
            ${i > 0 ? '<svg class="w-4 h-4 text-[#4f46e5] shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5-5 5M6 12h12"/></svg>' : ''}
            <span class="px-2.5 py-1 rounded-md bg-white border border-[#e5e5e5] text-sm font-medium text-[#000000]">${escapeHtml(domain)}</span>
        `).join('');

        return `
            <div class="p-3 rounded-lg border border-[#e5e5e5] bg-[#fafaf7]">
                <div class="flex items-center justify-between mb-2">
                    <span class="text-xs font-semibold uppercase tracking-wide text-[#999999]">
                        ${groups.length > 1 ? `Group ${index + 1}` : 'One group'}
                    </span>
                    <span class="text-xs ${ordered ? 'text-[#b8860b]' : 'text-[#3730a3]'}">
                        ${ordered ? 'runs in this order' : 'independent'}
                    </span>
                </div>
                <div class="flex items-center flex-wrap gap-1.5">${chain}</div>
                ${ordered ? `
                    <p class="text-xs text-[#999999] mt-2">
                        \u201c${escapeHtml(domains[domains.length - 1])}\u201d reads a table \u201c${escapeHtml(domains[0])}\u201d creates.
                    </p>
                ` : ''}
            </div>
        `;
    }).join('');

    const tree = files.map(f => {
        const depth = f.path.split('/').length - 1;
        return `<div class="pl-${depth * 4} text-[#666666]">${escapeHtml(f.path.split('/').pop())}</div>`;
    }).join('');

    const modal = document.createElement('div');
    modal.id = 'grouped-download-modal';
    modal.className = 'fixed inset-0 z-50';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-labelledby', 'grouped-download-title');
    modal.innerHTML = `
        <div class="absolute inset-0 bg-black bg-opacity-40" onclick="closeGroupedDownload()"></div>
        <div class="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-lg bg-white rounded-xl shadow-2xl overflow-hidden">
            <div class="px-6 pt-5 pb-4 border-b border-[#e5e5e5]">
                <div class="flex items-start justify-between">
                    <div>
                        <h3 id="grouped-download-title" class="text-lg font-semibold text-[#000000]">Download grouped SQL</h3>
                        <p class="text-sm text-[#666666] mt-0.5">
                            <code class="bg-[#f8fafc] px-1.5 py-0.5 rounded text-xs">${safeName}</code>
                            \u00b7 your SQL, unchanged \u2014 sorted by what feeds off what
                        </p>
                    </div>
                    <button onclick="closeGroupedDownload()" aria-label="Close" class="p-1 rounded hover:bg-[#f8fafc] text-[#999999]">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
                    </button>
                </div>
            </div>

            <div class="px-6 py-4 space-y-2 max-h-[45vh] overflow-y-auto">
                ${groupCards}
                ${groups.length > 1 ? `
                    <p class="text-xs text-[#999999] pt-1">
                        Groups share no tables \u2014 run them in any order, or in parallel.
                    </p>
                ` : ''}
            </div>

            <div class="px-6 py-3 bg-[#fafaf7] border-t border-[#e5e5e5]">
                <p class="text-xs font-medium text-[#999999] mb-1.5">Inside the zip</p>
                <div class="font-mono text-xs leading-5">${tree}</div>
            </div>

            <div class="px-6 py-4 border-t border-[#e5e5e5] flex items-center justify-between">
                <span class="text-xs text-[#999999]">No dbt conversion \u2014 GROUPS.md explains the split</span>
                <div class="flex items-center gap-2">
                    <button onclick="closeGroupedDownload()" class="px-4 py-2 text-sm text-[#666666] rounded-lg hover:bg-[#f8fafc]">Cancel</button>
                    <button id="grouped-download-confirm" class="px-4 py-2 text-sm font-medium text-white bg-[#4f46e5] rounded-lg hover:bg-[#4338ca]">
                        Download .zip
                    </button>
                </div>
            </div>
        </div>
    `;
    document.body.appendChild(modal);

    document.getElementById('grouped-download-confirm').onclick =
        () => downloadGroupedZip(conversion.name, files, sqlCount, groups.length);

    modal.addEventListener('keydown', e => { if (e.key === 'Escape') closeGroupedDownload(); });
    if (typeof FocusTrap !== 'undefined') FocusTrap.trap(modal);
    document.getElementById('grouped-download-confirm').focus();
}

function closeGroupedDownload() {
    const modal = document.getElementById('grouped-download-modal');
    if (!modal) return;
    if (typeof FocusTrap !== 'undefined') FocusTrap.release();
    modal.remove();
}

async function downloadGroupedZip(name, files, sqlCount, groupCount) {
    const button = document.getElementById('grouped-download-confirm');
    if (button) {
        button.disabled = true;
        button.textContent = 'Zipping\u2026';
    }

    try {
        const zip = new JSZip();
        const folderName = name.toLowerCase().replace(/\s+/g, '_');
        files.forEach(file => zip.file(`${folderName}/${file.path}`, file.content));

        const blob = await zip.generateAsync({ type: 'blob' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `${folderName}_grouped.zip`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);

        closeGroupedDownload();
        Alert.toast(
            `Downloaded ${folderName}_grouped.zip \u2014 ${sqlCount} file${sqlCount === 1 ? '' : 's'}${groupCount > 1 ? ` in ${groupCount} groups` : ''}`,
            'success'
        );
    } catch (error) {
        console.error('Error building the zip:', error);
        if (button) {
            button.disabled = false;
            button.textContent = 'Download .zip';
        }
    }
}

// Delete every query in a conversion
async function deleteConversion(event, name) {
    if (event?.stopPropagation) {
        event.stopPropagation();
        event.preventDefault();
    }

    const conversion = (Array.isArray(conversions) ? conversions : []).find(m => m.name === name);
    if (!conversion) return;

    const count = conversion.queries.length;
    if (!confirm(`Remove the "${name}" conversion${count > 1 ? ` and all ${count} of its domains` : ''}?`)) {
        return;
    }

    for (const query of conversion.queries) {
        const params = new URLSearchParams({ filename: query.filename });
        const res = await fetch(`/api/delete-query/${query.id}?${params}`, { method: 'DELETE' });
        if (!res.ok) {
            console.warn(`[DELETE ${query.filename}] failed`);
        }
    }

    window.location.reload();
}

function applySidebarState() {
    const isCollapsed = localStorage.getItem('dbt_training_wheels_sidebar_collapsed') === 'true';
    document.body.classList.toggle('sidebar-collapsed', isCollapsed);
    updateSidebarToggleButton(isCollapsed);
}

function toggleSidebar() {
    const isCollapsed = !document.body.classList.contains('sidebar-collapsed');
    localStorage.setItem('dbt_training_wheels_sidebar_collapsed', isCollapsed.toString());
    document.body.classList.toggle('sidebar-collapsed', isCollapsed);
    updateSidebarToggleButton(isCollapsed);
}

function updateSidebarToggleButton(isCollapsed) {
    const toggleButton = document.getElementById('sidebar-toggle-btn');
    if (!toggleButton) return;

    const title = isCollapsed ? 'Expand sidebar' : 'Collapse sidebar';
    toggleButton.setAttribute('title', title);
    toggleButton.setAttribute('aria-label', title);
}

// Setup drag and drop functionality
function setupDragAndDrop() {
    // Setup for empty state upload area
    setupDropZone('upload-area');

    // Setup for sidebar upload area (always visible)
    setupDropZone('sidebar-upload-area');
}

// Setup drag and drop for a specific upload zone
function setupDropZone(elementId) {
    const uploadArea = document.getElementById(elementId);
    if (!uploadArea) return;

    const container = uploadArea;

    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        container.addEventListener(eventName, preventDefaults, false);
    });

    function preventDefaults(e) {
        e.preventDefault();
        e.stopPropagation();
    }

    // A semantic state class, not the Tailwind utilities this used to add. Those
    // only ever worked on elements with no competing rule of their own -- any drop
    // zone styled by our own CSS out-specified them, so the highlight silently did
    // nothing. Each zone styles .is-dragover for itself.
    ['dragenter', 'dragover'].forEach(eventName => {
        container.addEventListener(eventName, () => {
            container.classList.add('is-dragover');
        }, false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        container.addEventListener(eventName, () => {
            container.classList.remove('is-dragover');
        }, false);
    });

    container.addEventListener('drop', async (e) => {
        // dataTransfer.files doesn't expose directory contents, so read the entries
        // first and walk any dropped folders
        const entries = await collectDroppedEntries(e.dataTransfer);
        if (entries.length > 0) {
            handleUploadSelection(entries);
        }
    }, false);
}

// Read every .readEntries() page - it returns at most 100 entries per call
function readAllDirectoryEntries(reader) {
    return new Promise((resolve) => {
        const all = [];
        const readBatch = () => {
            reader.readEntries((batch) => {
                if (!batch.length) {
                    resolve(all);
                    return;
                }
                all.push(...batch);
                readBatch();
            }, () => resolve(all));
        };
        readBatch();
    });
}

// Walk a dropped entry, collecting {file, path} with the same relative paths
// webkitRelativePath would have produced for a picked folder
async function walkDroppedEntry(entry, prefix, collected) {
    if (!entry) return;

    if (entry.isFile) {
        const file = await new Promise((resolve) => entry.file(resolve, () => resolve(null)));
        if (file) {
            collected.push({ file, path: prefix ? `${prefix}/${entry.name}` : entry.name });
        }
        return;
    }

    if (entry.isDirectory) {
        const children = await readAllDirectoryEntries(entry.createReader());
        const dirPath = prefix ? `${prefix}/${entry.name}` : entry.name;
        for (const child of children) {
            await walkDroppedEntry(child, dirPath, collected);
        }
    }
}

async function collectDroppedEntries(dataTransfer) {
    const items = Array.from(dataTransfer?.items || []);
    const entries = items
        .filter(item => item.kind === 'file')
        .map(item => (item.webkitGetAsEntry ? item.webkitGetAsEntry() : null))
        .filter(Boolean);

    // Browser doesn't support the entries API - fall back to plain files
    if (entries.length === 0) {
        return Array.from(dataTransfer?.files || []).map(file => ({ file, path: file.name }));
    }

    const collected = [];
    for (const entry of entries) {
        await walkDroppedEntry(entry, '', collected);
    }
    return collected;
}

// Accept either a FileList (from the file/folder inputs) or {file, path} entries
// (from a drop), and normalize to {file, path}
function normalizeUploadEntries(input) {
    return Array.from(input || []).map(item =>
        item && item.file
            ? item
            : { file: item, path: item.webkitRelativePath || item.name }
    );
}

function handleUploadSelection(input) {
    const entries = normalizeUploadEntries(input);
    if (entries.length === 0) return;

    // A lone file with no folder in its path is a single-file upload
    const hasFolderPaths = entries.some(entry => entry.path.includes('/'));
    if (entries.length === 1 && !hasFolderPaths) {
        handleFileUpload(entries[0].file);
        return;
    }

    handleFolderUpload(entries);
}

function setUploadLoading() {
    const uploadProgress = document.getElementById('upload-progress');
    if (uploadProgress) {
        uploadProgress.classList.remove('hidden');
    }

    const sidebarUpload = document.getElementById('sidebar-upload-area');
    if (sidebarUpload) {
        sidebarUpload.innerHTML = `
            <div class="flex items-center justify-center gap-2">
                <svg class="animate-spin h-5 w-5 text-[#4f46e5]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                <span class="text-sm font-medium text-[#666666]">Uploading...</span>
            </div>
        `;
    }
}

function clearUploadLoading() {
    const uploadProgress = document.getElementById('upload-progress');
    if (uploadProgress) {
        uploadProgress.classList.add('hidden');
    }
    restoreSidebarUpload();
}

// POST an upload, returning {ok, data}. Unlike errorHandler.safeFetch this doesn't
// show the error modal, so recoverable conflicts can be handled by the caller.
async function postUpload(url, formData) {
    const response = await fetch(url, { method: 'POST', body: formData });
    let data = null;
    try {
        data = await response.json();
    } catch (_) {
        data = null;
    }
    return { ok: response.ok, data };
}

// A conflict the user can resolve by overwriting - prompt and report their choice
function confirmOverwrite(data) {
    const details = data?.error?.details;
    if (!details?.can_overwrite) return false;

    const conflicts = details.conflicts || [];
    return confirm(
        `${conflicts.length} already exist:\n\n${conflicts.join('\n')}\n\nReplace them with this upload?`
    );
}

async function handleFolderUpload(input, overwrite = false) {
    const entries = normalizeUploadEntries(input);
    if (entries.length === 0) return;

    const sqlEntries = entries.filter(entry => entry.path.toLowerCase().endsWith('.sql'));
    if (sqlEntries.length === 0) {
        errorHandler.showError({
            error: {
                user_message: 'No SQL files found',
                beginner_help: 'Folder uploads only accept .sql files',
                common_fixes: [
                    'Select a folder containing .sql files',
                    'Remove non-SQL files from the selection',
                    'Try uploading a single SQL file instead'
                ]
            }
        });
        resetFileInputs();
        return;
    }

    const formData = new FormData();
    sqlEntries.forEach(entry => {
        formData.append('files', entry.file);
        formData.append('paths', entry.path);
    });

    const uploadUrl = overwrite ? '/api/upload-folder?overwrite=true' : '/api/upload-folder';

    try {
        setUploadLoading();
        const { ok, data } = await postUpload(uploadUrl, formData);
        clearUploadLoading();

        if (!ok) {
            // Offer to replace what's already there rather than dead-ending
            if (!overwrite && confirmOverwrite(data)) {
                return handleFolderUpload(entries, true);
            }
            errorHandler.showError(data);
            resetFileInputs();
            return;
        }

        console.log(data?.message || `Created ${data?.queries_created} query(ies)`);

        setTimeout(() => {
            window.location.reload();
        }, 1500);
    } catch (error) {
        clearUploadLoading();
        errorHandler.showError({
            error: {
                user_message: 'Upload failed',
                beginner_help: 'Could not reach the server to upload your folder.',
                common_fixes: ['Check that the server is running', 'Try uploading again']
            }
        });
    }

    resetFileInputs();
}

// Delete a single uploaded SQL query
async function deleteQuery(event, queryId) {
    if (event?.stopPropagation) {
        event.stopPropagation();
        event.preventDefault();
    }

    // Find query object (for filename/name hints)
    const q = (Array.isArray(scheduledQueries) ? scheduledQueries : []).find(q => q.id === queryId);

    // Derive a filename or name to help the backend when index is missing
    const derivedFilename =
        q?.filename ||
        q?.file ||
        (q?.path ? q.path.split('/').pop() : undefined);

    // Build query params
    const params = new URLSearchParams();
    if (derivedFilename) params.set('filename', derivedFilename);
    if (q?.name)        params.set('name', q.name); // backend will secure_filename(f"{name}.sql")

    const endpoint = params.toString()
        ? `/api/delete-query/${queryId}?${params.toString()}`
        : `/api/delete-query/${queryId}`;

    const res = await fetch(endpoint, { method: 'DELETE' });

    // Treat *any* 2xx as success.
    if (!res.ok) {
        const text = await res.text();
        console.warn(`[DELETE ${endpoint}] Failed:`, text);
        throw new Error();
    }

    // If response body exists, try reading it – but don't throw if it doesn't
    let json = null;
    try { json = await res.json(); } catch (_) {}

    // Now safely update the UI immediately
    if (Array.isArray(scheduledQueries)) {
        const idx = scheduledQueries.findIndex(x => x.id === queryId);
        if (idx !== -1) {
            scheduledQueries.splice(idx, 1);
        }
    }

    if (currentQuery?.id === queryId) {
        currentQuery = null;
        analysisResults = null;
        document.getElementById('main-content')?.classList.add('hidden');
        document.getElementById('empty-state')?.classList.remove('hidden');
    }
    renderQueryTree();
    return;
}

// Handle file upload
async function handleFileUpload(file, overwrite = false) {
    if (!file) return;

    setUploadLoading();

    const formData = new FormData();
    formData.append('file', file);

    // Build URL with overwrite parameter if needed
    const uploadUrl = overwrite ? '/api/upload?overwrite=true' : '/api/upload';

    try {
        // The server reports an existing file as a recoverable conflict, so there's
        // no pre-flight check to race against
        const { ok, data } = await postUpload(uploadUrl, formData);
        clearUploadLoading();

        if (!ok) {
            if (!overwrite && confirmOverwrite(data)) {
                return handleFileUpload(file, true);
            }
            errorHandler.showError(data);
            resetFileInputs();
            return;
        }

        // Reload the page after a short delay to show the new file
        setTimeout(() => {
            // Store tour step if tour is active
            if (isTourActive) {
                let resumeStep = currentTourStep + 1;
                if (typeof TOUR_STEPS !== 'undefined') {
                    const uploadedIndex = TOUR_STEPS.findIndex(step => step.id === 'uploaded-file');
                    if (uploadedIndex !== -1) {
                        resumeStep = Math.max(resumeStep, uploadedIndex);
                    }
                }
                localStorage.setItem('tour_step', resumeStep);
            }
            window.location.reload();
        }, 1500);
    } catch (error) {
        clearUploadLoading();
        errorHandler.showError({
            error: {
                user_message: 'Upload failed',
                beginner_help: 'Could not reach the server to upload your file.',
                common_fixes: ['Check that the server is running', 'Try uploading again']
            }
        });
    }

    resetFileInputs();
}

// Helper to reset file inputs
function resetFileInputs() {
    const fileInput = document.getElementById('file-input');
    if (fileInput) fileInput.value = '';
    const sidebarFileInput = document.getElementById('sidebar-file-input');
    if (sidebarFileInput) sidebarFileInput.value = '';
    const folderInput = document.getElementById('folder-input');
    if (folderInput) folderInput.value = '';
    const sidebarFolderInput = document.getElementById('sidebar-folder-input');
    if (sidebarFolderInput) sidebarFolderInput.value = '';
}

// Helper to restore sidebar upload area
function restoreSidebarUpload() {
    const sidebarUpload = document.getElementById('sidebar-upload-area');
    if (sidebarUpload) {
        sidebarUpload.innerHTML = `
            <div class="flex items-center justify-center gap-2">
                <svg class="w-5 h-5 text-[#999999]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"></path>
                </svg>
                <span class="text-sm font-medium text-[#666666]">Upload SQL File</span>
            </div>
            <p class="text-xs text-[#999999] mt-1">Click or drag & drop</p>
        `;
    }
}

// Select a query
function selectQuery(queryId, options = {}) {
    const preserveState = options.preserveState === true;
    const newQuery = scheduledQueries.find(q => q.id === queryId);

    // Check if we're switching queries (not initial selection)
    const switchingQueries = currentQuery && currentQuery.id !== queryId;

    if (switchingQueries) {
        // Warn user about losing progress if they have any persisted state
        const hasProgress = analysisResults || Object.keys(modelConfigurations).length > 0;
        if (hasProgress) {
            const confirmed = confirm('Switching queries will reset your current progress. Continue?');
            if (!confirmed) {
                return; // User cancelled
            }
        }
        // Clear session storage for old query
        appState.clearSession([
            'analysisResults',
            'modelConfigurations',
            'modelTags',
            'stepCompletionState',
            'currentStep',
            'userMartSelection'
        ]);
    }

    currentQuery = newQuery;
    if (!preserveState) {
        currentStep = StepRegistry.getFirstStepId();  // Use first enabled step (string ID)
        analysisResults = null;
        generatedFiles = [];
        showingSql = false;
        viewingAllSteps = true; // Start with overview
        userMartSelection = [];
    } else if (!currentStep) {
        currentStep = StepRegistry.getFirstStepId();
    }

    // Restore userMartSelection from sessionStorage if available
    const storedMartSelection = sessionStorage.getItem('dbt_training_wheels_userMartSelection');
    if (storedMartSelection) {
        try {
            const parsed = JSON.parse(storedMartSelection);
            if (Array.isArray(parsed)) {
                userMartSelection = parsed;
                console.log('[DEBUG selectQuery] Restored userMartSelection from sessionStorage:', userMartSelection);
            }
        } catch (e) {
            console.warn('[DEBUG selectQuery] Failed to parse stored mart selection:', e);
            userMartSelection = [];
        }
    } else if (!preserveState) {
        // Only clear if we're not preserving state
        userMartSelection = [];
    }

    // Persist selected query ID to sessionStorage
    sessionStorage.setItem('selectedQueryId', queryId);
    appState.set('selectedQueryId', queryId, { session: true });
    console.log('Selected query:', currentQuery);

    // Update UI
    document.querySelectorAll('.query-item').forEach(item => {
        item.classList.remove('selected');
    });
    const selectedQueryItem = document.getElementById(`query-${queryId}`);
    if (selectedQueryItem) {
        selectedQueryItem.classList.add('selected');
    }

    // Hide empty state, show main content
    document.getElementById('empty-state').classList.add('hidden');
    document.getElementById('main-content').classList.remove('hidden');

    // Update header
    document.getElementById('query-name').textContent = currentQuery.name;
    document.getElementById('query-dataset').textContent = currentQuery.dataset;

    // Hide SQL preview
    document.getElementById('sql-preview').classList.add('hidden');
    document.getElementById('toggle-sql-text').textContent = 'Show Original SQL';

    // Render conversion steps
    renderConversionSteps();

    // Update navigation to show overview or restored step
    updateStepNavigation();
}

// Toggle SQL preview
function toggleSqlPreview() {
    showingSql = !showingSql;
    const preview = document.getElementById('sql-preview');
    const toggleText = document.getElementById('toggle-sql-text');

    if (showingSql && currentQuery.sql) {
        preview.classList.remove('hidden');
        toggleText.textContent = 'Hide Original SQL';
        document.getElementById('original-sql').textContent = currentQuery.sql;
    } else {
        preview.classList.add('hidden');
        toggleText.textContent = 'Show Original SQL';
    }
}

// Open full screen SQL modal
function openFullScreenSql() {
    if (!currentQuery || !currentQuery.sql) return;

    const modal = document.getElementById('fullscreen-sql-modal');
    const content = document.getElementById('fullscreen-sql-content');
    const queryName = document.getElementById('fullscreen-query-name');

    content.textContent = currentQuery.sql;
    queryName.textContent = currentQuery.name ? `- ${currentQuery.name}` : '';

    modal.classList.remove('hidden');
    document.body.style.overflow = 'hidden'; // Prevent background scroll

    // Trap focus within modal for accessibility
    if (typeof FocusTrap !== 'undefined') {
        FocusTrap.trap(modal);
    }
}

// Close full screen SQL modal
function closeFullScreenSql() {
    const modal = document.getElementById('fullscreen-sql-modal');
    modal.classList.add('hidden');
    document.body.style.overflow = ''; // Restore scroll

    // Release focus trap and restore previous focus
    if (typeof FocusTrap !== 'undefined') {
        FocusTrap.release();
    }
}

// Handle Escape key to close modal
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        const modal = document.getElementById('fullscreen-sql-modal');
        if (modal && !modal.classList.contains('hidden')) {
            closeFullScreenSql();
        }
    }
});

// Render conversion steps (only enabled steps from registry)
function renderConversionSteps() {
    const container = document.getElementById('conversion-steps');
    container.innerHTML = '';

    // Use StepRegistry to get only enabled steps
    const enabledSteps = StepRegistry.getEnabledSteps();

    enabledSteps.forEach((step, idx) => {
        const displayNum = idx + 1;
        const state = getStepState(step.id);
        const isActive = step.id === currentStep;

        const stepElement = document.createElement('button');
        // Two independent classes: what the step's state is, and whether you are on it.
        stepElement.className =
            `w-full flex items-center gap-4 p-4 rounded-lg border transition-all step-${state}`
            + (isActive ? ' step-active' : '');
        stepElement.onclick = () => setActiveStep(step.id);
        if (isActive) stepElement.setAttribute('aria-current', 'step');
        stepElement.setAttribute('aria-label', `${displayNum}. ${step.title} — ${STEP_STATE_LABEL[state]}`);

        stepElement.innerHTML = `
            <div class="p-2 rounded-lg step-icon-container">
                ${getStepIcon(step.icon)}
            </div>
            <div class="flex-1 text-left">
                <div class="font-medium text-gray-900">${displayNum}. ${step.title}</div>
                <div class="text-sm text-gray-500">${step.description}</div>
            </div>
            <svg class="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"></path>
            </svg>
        `;

        container.appendChild(stepElement);
    });

    renderStepRailSummary(enabledSteps);
}

// What each state means, in the words the rail uses.
const STEP_STATE_LABEL = {
    blocked: 'needs an answer from you',
    settled: "you've answered it",
    defaulted: 'defaults stand — safe to skip'
};

// The summary under the step list: how many are settled, what the dots mean, and a
// way to jump straight to the next thing that actually needs you.
function renderStepRailSummary(enabledSteps) {
    const container = document.getElementById('conversion-steps');
    if (!container) return;

    const total = enabledSteps.length;
    const settled = getSettledStepCount();
    const blocked = getBlockedStepIds();

    const summary = document.createElement('div');
    summary.className = 'step-rail-summary';
    summary.innerHTML = `
        <div class="step-rail-count">
            <strong>${settled} of ${total}</strong> settled
            ${blocked.length > 0
                ? `<button type="button" class="step-rail-jump" onclick="jumpToNextBlockedStep()">
                       Next that needs you →
                   </button>`
                : ''}
        </div>
        <details class="step-rail-legend">
            <summary>What the dots mean</summary>
            <ul>
                <li><span class="step-dot step-dot-blocked" aria-hidden="true"></span>${STEP_STATE_LABEL.blocked}</li>
                <li><span class="step-dot step-dot-settled" aria-hidden="true"></span>${STEP_STATE_LABEL.settled}</li>
                <li><span class="step-dot step-dot-defaulted" aria-hidden="true"></span>${STEP_STATE_LABEL.defaulted}</li>
            </ul>
            <p>Position in the flow never marks a step done. Jump anywhere, in any order.</p>
        </details>
    `;
    container.appendChild(summary);
}

// Jump to the next blocked step after the current one, wrapping around.
function jumpToNextBlockedStep() {
    const blocked = getBlockedStepIds();
    if (blocked.length === 0) return;

    const order = StepRegistry.getEnabledSteps().map(s => s.id);
    const currentIdx = order.indexOf(currentStep);

    const next = blocked.find(id => order.indexOf(id) > currentIdx) || blocked[0];
    setActiveStep(next);
}

// Get step status.
//
// This used to compare display numbers -- a step counted as "completed" because you
// had navigated past it, which is not a fact about the step at all. It now reports
// what validation.js actually knows: blocked / settled / defaulted.
//
// Being the current step is a separate, orthogonal thing (see renderConversionSteps),
// because where you are standing and what you have answered are different questions.
function getStepStatus(stepId) {
    return getStepState(stepId);
}

// Get step icon
function getStepIcon(iconName) {
    const icons = {
        'database': '<svg class="w-5 h-5 step-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"></path></svg>',
        'file-code': '<svg class="w-5 h-5 step-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"></path></svg>',
        'git-branch': '<svg class="w-5 h-5 step-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16V4m0 0L3 8m4-4l4 4m6 0v12m0 0l4-4m-4 4l-4-4"></path></svg>',
        'code': '<svg class="w-5 h-5 step-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"></path></svg>',
        'test-tube': '<svg class="w-5 h-5 step-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"></path></svg>',
        'layers': '<svg class="w-5 h-5 step-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"></path></svg>',
        'check-circle': '<svg class="w-5 h-5 step-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>',
        'settings': '<svg class="w-5 h-5 step-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"></path></svg>',
        'folder': '<svg class="w-5 h-5 step-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"></path></svg>',
        'tag': '<svg class="w-5 h-5 step-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z"></path></svg>',
        'refresh': '<svg class="w-5 h-5 step-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>',
        'upload': '<svg class="w-5 h-5 step-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"></path></svg>'
    };
    return icons[iconName] || icons['database'];
}

// Set active step
async function setActiveStep(stepId) {
    currentStep = stepId;
    viewingAllSteps = false;
    updateStepNavigation();
    renderConversionSteps();
    await renderStepContent();
}

// Show all steps overview
function showAllSteps() {
    viewingAllSteps = true;
    updateStepNavigation();
    renderStepContent();
}

// Update step navigation UI
function updateStepNavigation() {
    const breadcrumb = document.getElementById('step-breadcrumb');
    const overview = document.getElementById('conversion-steps-overview');
    const stepContent = document.getElementById('step-content');

    if (viewingAllSteps) {
        // Show overview, hide breadcrumb
        breadcrumb.classList.add('hidden');
        overview.classList.remove('hidden');
        stepContent.innerHTML = ''; // Clear step content when viewing overview
    } else {
        // Show breadcrumb, hide overview
        breadcrumb.classList.remove('hidden');
        overview.classList.add('hidden');

        // Use StepRegistry for dynamic display number
        const step = StepRegistry.getStepById(currentStep);
        const displayNum = StepRegistry.idToDisplayNum(currentStep);
        const totalSteps = StepRegistry.getTotalSteps();

        if (step && displayNum) {
            document.getElementById('breadcrumb-step-number').textContent = `Step ${displayNum} of ${totalSteps}`;
            document.getElementById('breadcrumb-step-name').textContent = step.title;
        }
    }
}


// Render step content dynamically based on step config
async function renderStepContent() {
    const container = document.getElementById('step-content');

    if (!currentStep) {
        container.innerHTML = '';
        return;
    }

    // Get the render function name from step config
    const renderFnName = StepRegistry.getRenderFn(currentStep);
    if (!renderFnName) {
        console.error(`No render function found for step: ${currentStep}`);
        container.innerHTML = '<div class="dbt-page-card"><p class="dbt-hint">Step not found.</p></div>';
        return;
    }

    // Get the render function from window (global scope)
    const renderFn = window[renderFnName];
    if (typeof renderFn !== 'function') {
        console.error(`Render function ${renderFnName} is not defined`);
        container.innerHTML = '<div class="dbt-page-card"><p class="dbt-hint">Step renderer not found.</p></div>';
        return;
    }

    // Call the render function (may be async)
    await renderFn(container);

    // Setup synchronized scrolling for diff panels on relevant steps
    if (currentStep === 'analyze') {
        setTimeout(() => setupDiffSyncScroll(), 100);
    }
}

// NOTE: viewingAllSteps is declared in utils.js and available globally

// Analyze query
async function analyzeQuery() {
    // Defensive check: ensure currentQuery exists
    if (!currentQuery || !currentQuery.id) {
        console.error('No query selected. currentQuery:', currentQuery);
        errorHandler.showError({
            error: {
                user_message: 'No query selected',
                beginner_help: 'You need to select a query from the left sidebar before analyzing',
                common_fixes: [
                    'Click on a query in the "Scheduled Queries" section on the left',
                    'Make sure you have uploaded a SQL file first'
                ],
                docs_link: '/troubleshooting#general-errors'
            }
        });
        return;
    }

    try {
        console.log('Analyzing query ID:', currentQuery.id);

        // Get selected project name (from prerequisite modal)
        const projectName = userDomainName || sessionStorage.getItem('dbt_training_wheels_domain_name');
        const allowedProjects = window.availableConfigProjects || [];
        if (!projectName || (allowedProjects.length > 0 && !allowedProjects.includes(projectName))) {
            errorHandler.showError({
                error: {
                    user_message: 'Select a valid project before analyzing',
                    beginner_help: 'Naming rules come from dbt_training_wheels_config.yaml, so a project must be selected.',
                    common_fixes: [
                        'Open the checklist and pick a project from the dropdown',
                        'Make sure the project exists in dbt_training_wheels_config.yaml',
                        'Refresh the page if the project list looks stale'
                    ],
                    docs_link: '/troubleshooting#configuration-errors'
                }
            });
            return;
        }
        const martSelection = Array.isArray(userMartSelection) ? userMartSelection : [];
        const params = new URLSearchParams();
        if (projectName) params.append('project_name', projectName);
        if (martSelection.length > 0) params.append('user_mart_selection', martSelection.join(','));
        const url = params.toString()
            ? `/api/analyze/${currentQuery.id}?${params.toString()}`
            : `/api/analyze/${currentQuery.id}`;

        console.log('[DEBUG analyzeCurrentQuery] Using project:', projectName);
        console.log('[DEBUG analyzeCurrentQuery] userDomainName:', userDomainName);
        console.log('[DEBUG analyzeCurrentQuery] sessionStorage value:', sessionStorage.getItem('dbt_training_wheels_domain_name'));
        console.log('[DEBUG analyzeCurrentQuery] API URL:', url);
        console.log('[DEBUG analyzeCurrentQuery] userMartSelection:', martSelection);
        analysisResults = await errorHandler.safeFetch(url);
        console.log('[DEBUG analyzeCurrentQuery] Analysis results received');
        console.log('[DEBUG analyzeCurrentQuery] analysisResults.naming:', analysisResults?.naming);

        // Persist analysis results to session
        appState.set('analysisResults', analysisResults, { session: true });

        renderStepContent();

        // Update completion checklist for Step 0
        setTimeout(() => updateCompletionChecklist(0), 100);
    } catch (error) {
        // Error already displayed by errorHandler
        console.error('Error analyzing query:', error);
    }
}

// Generate all files and trigger download
async function generateAllFiles(evt) {
    let button;
    let originalHTML;

    try {
        // Show loading state
        button = evt ? evt.target.closest('button') : document.querySelector('#step-content button[onclick*="generateAllFiles"]');
        originalHTML = button.innerHTML;
        button.disabled = true;
        button.innerHTML = '<svg class="animate-spin h-5 w-5 text-white inline mr-2" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg> Generating...';

        // Get selected project name and domain area
        const projectName = userDomainName || sessionStorage.getItem('dbt_training_wheels_domain_name');
        const domainArea = domainFromFilename(currentQuery?.filename);
        const modelGroup = conversionNameFromFilename(currentQuery?.filename);


        // Get user's mart selection
        const martSelection = Array.isArray(userMartSelection) ? userMartSelection : [];

        // Build API URL with query parameters
        let apiUrl = `/api/generate-files/${currentQuery.id}`;
        const params = new URLSearchParams();
        if (projectName) params.append('project_name', projectName);
        if (domainArea) params.append('domain_area', domainArea);
        if (modelGroup) params.append('model_group', modelGroup);
        if (martSelection.length > 0) params.append('user_mart_selection', martSelection.join(','));
        if (params.toString()) apiUrl += `?${params.toString()}`;

        console.log('[DEBUG generateAllFiles] API URL:', apiUrl);
        console.log('[DEBUG generateAllFiles] User mart selection:', martSelection);
        generatedFiles = await errorHandler.safeFetch(apiUrl);

        // Create ZIP file using JSZip
        const zip = new JSZip();
        const folderName = `dbt_conversion_${currentQuery.name.toLowerCase().replace(/\s+/g, '_')}`;

        // Add each file to the ZIP
        generatedFiles.forEach(file => {
            zip.file(`${folderName}/${file.path}`, file.content);
        });

        // Generate the ZIP and trigger download
        const content = await zip.generateAsync({ type: 'blob' });
        const url = URL.createObjectURL(content);
        const link = document.createElement('a');
        link.href = url;
        link.download = `${folderName}.zip`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);

        // Reset button with success state
        button.disabled = false;
        button.innerHTML = '<svg class="w-5 h-5 inline mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg> Downloaded!';

        setTimeout(() => {
            button.innerHTML = originalHTML;
        }, 3000);

    } catch (error) {
        console.error('Error generating files:', error);
        // Error already displayed by errorHandler
        if (button) {
            button.disabled = false;
            button.innerHTML = originalHTML;
        }
    }
}

// Write files directly to dbt project
async function writeToDbtProject(evt) {
    let button;
    let originalHTML;

    try {
        // Domain comes from the folder this query was uploaded from - there's no
        // longer a field to type it into
        const domainName = domainFromFilename(currentQuery?.filename);

        // Show loading state
        button = evt ? evt.target.closest('button') : document.querySelector('#step-content button[onclick*="writeToDbtProject"]');
        originalHTML = button.innerHTML;
        button.disabled = true;
        button.innerHTML = '<svg class="animate-spin h-5 w-5 text-white inline mr-2" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg> Writing...';

        // Get selected project name
        const projectName = userDomainName || sessionStorage.getItem('dbt_training_wheels_domain_name');

        const response = await errorHandler.safeFetch(`/api/write-to-dbt-project/${currentQuery.id}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
            domain: domainName,
            model_group: conversionNameFromFilename(currentQuery?.filename),
            project_path: userDbtProjectPath,
            project_name: projectName
        })
        });

        // Show success message with details
        const filesWritten = response.files.length;
        const modelsPath = response.models_path;

        // Reset button with success state
        button.disabled = false;
        button.innerHTML = '<svg class="w-5 h-5 inline mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg> Files Added!';

        // Show detailed success notification
        const notification = document.createElement('div');
        notification.className = 'fixed top-4 right-4 bg-[#4f46e5] text-white px-6 py-4 rounded-lg shadow-2xl z-50 max-w-md';
        notification.innerHTML = `
            <div class="flex items-start gap-3">
                <svg class="w-6 h-6 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                </svg>
                <div class="flex-1">
                    <div class="font-semibold mb-1">Success!</div>
                    <div class="text-sm opacity-90">
                        ${filesWritten} files written to:<br>
                        <code class="bg-[#4338ca] px-2 py-1 rounded text-xs mt-1 inline-block">${modelsPath}</code>
                    </div>
                </div>
                <button onclick="this.parentElement.parentElement.remove()" class="text-white hover:text-gray-200">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
                    </svg>
                </button>
            </div>
        `;
        document.body.appendChild(notification);

        // Auto-remove notification after 5 seconds
        setTimeout(() => {
            notification.remove();
        }, 5000);

        // Reset button after delay
        setTimeout(() => {
            button.innerHTML = originalHTML;
        }, 3000);

    } catch (error) {
        console.error('Error writing to dbt project:', error);
        // Error already displayed by errorHandler
        if (button) {
            button.disabled = false;
            button.innerHTML = originalHTML;
        }
    }
}

// Copy to clipboard
function copyToClipboard(elementId, buttonId) {
    const element = document.getElementById(elementId);
    const text = element.textContent;

    navigator.clipboard.writeText(text).then(() => {
        const button = document.getElementById(buttonId);
        if (button) {
            const originalHTML = button.innerHTML;
            button.innerHTML = '<svg class="w-4 h-4 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>';
            setTimeout(() => {
                button.innerHTML = originalHTML;
            }, 2000);
        }
    }).catch(err => {
        console.error('Failed to copy:', err);
    });
}

// Reset conversion
// Beginner mode is always on - removed toggle functionality

// Model configuration state management functions
// NOTE: getAllModels() is defined in utils.js

function initializeModelConfigurations() {
    const allModels = getAllModels();

    allModels.forEach((model, idx) => {
        if (!modelConfigurations[idx]) {
            modelConfigurations[idx] = {
                table: model.name,
                type: model.type,
                materialization: model.type === 'prep' ? 'table' : 'table',
                schema: '',  // Configure schema in dbt_project.yml
                tags: [...(modelTags[idx] || [])]
            };
        }
    });
}

function getModelConfig(modelIdx) {
    return modelConfigurations[modelIdx] || {
        materialization: 'table',
        schema: '',
        tags: []
    };
}

function updateModelConfig(modelIdx, field, value) {
    if (!modelConfigurations[modelIdx]) {
        modelConfigurations[modelIdx] = {
            table: analysisResults.finalTableSqls[modelIdx].table,
            materialization: 'table',
            schema: 'prep',
            tags: []
        };
    }
    modelConfigurations[modelIdx][field] = value;
}

async function saveModelConfiguration() {
    // Defensive check: ensure currentQuery exists
    if (!currentQuery || !currentQuery.id) {
        console.error('No query selected. currentQuery:', currentQuery);
        errorHandler.showError({
            error: {
                user_message: 'No query selected',
                beginner_help: 'You need to select a query from the left sidebar before saving configuration',
                common_fixes: [
                    'Click on a query in the "Scheduled Queries" section on the left',
                    'Make sure you have uploaded a SQL file first'
                ],
                docs_link: '/troubleshooting#general-errors'
            }
        });
        return false;
    }

    // If we're on a layer step, save descriptions first
    if (currentStep === 'layer-staging' && typeof saveStagingDescriptions === 'function') {
        try {
            await saveStagingDescriptions();
        } catch (error) {
            console.error('Error saving staging descriptions:', error);
        }
    }
    if (currentStep === 'layer-intermediate' && typeof saveIntermediateDescriptions === 'function') {
        try {
            await saveIntermediateDescriptions();
        } catch (error) {
            console.error('Error saving intermediate descriptions:', error);
        }
    }
    if (currentStep === 'layer-mart' && typeof saveMartDescriptions === 'function') {
        try {
            await saveMartDescriptions();
        } catch (error) {
            console.error('Error saving mart descriptions:', error);
            // Continue with model config save even if descriptions fail
        }
    }

    const modelsArray = Object.values(modelConfigurations);
    console.log('Saving model config for query ID:', currentQuery.id, 'Models:', modelsArray);

    try {
        const data = await errorHandler.safeFetch(`/api/save-model-config/${currentQuery.id}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ models: modelsArray })
        });

        console.log('Save response:', data);
        return data.success;
    } catch (error) {
        console.error('Error saving configuration:', error);
        // Error already displayed by errorHandler
        return false;
    }
}

async function saveAndContinue(nextStep, buttonElement = null) {
    console.log('saveAndContinue called with nextStep:', nextStep);

    // Race condition prevention
    if (typeof navigationInProgress !== 'undefined' && navigationInProgress) {
        console.log('Navigation already in progress, ignoring duplicate request');
        return;
    }

    try {
        // Set navigation lock and loading state
        if (typeof navigationInProgress !== 'undefined') {
            navigationInProgress = true;
        }
        if (buttonElement && typeof setNavigationLoading === 'function') {
            setNavigationLoading(buttonElement, true);
        }

        // Validate navigation before proceeding
        if (nextStep && typeof StepRegistry !== 'undefined') {
            const validation = StepRegistry.validateStepNavigation(currentStep, nextStep);
            if (!validation.valid) {
                if (typeof showNavigationError === 'function') {
                    showNavigationError(validation.reason || 'Cannot navigate to this step', 'warning');
                }
                return;
            }
        }

        // Validate that all descriptions are filled in for layer steps
        const layerDescriptionSelectors = {
            'layer-staging': '.staging-description-input',
            'layer-intermediate': '.intermediate-description-input',
            'layer-mart': '.mart-description-input',
        };
        const descSelector = layerDescriptionSelectors[currentStep];
        if (descSelector) {
            const descInputs = document.querySelectorAll(descSelector);
            const emptyInputs = Array.from(descInputs).filter(input => !input.value.trim());
            if (emptyInputs.length > 0) {
                // Highlight empty textareas
                emptyInputs.forEach(input => {
                    input.style.borderColor = '#ef4444';
                    input.addEventListener('input', function handler() {
                        if (this.value.trim()) {
                            this.style.borderColor = '#d1d5db';
                            this.removeEventListener('input', handler);
                        }
                    });
                });
                // Scroll to first empty textarea
                emptyInputs[0].focus();
                emptyInputs[0].scrollIntoView({ behavior: 'smooth', block: 'center' });

                if (typeof showNavigationError === 'function') {
                    showNavigationError(
                        `Please fill in all model descriptions before proceeding (${emptyInputs.length} remaining)`,
                        'warning'
                    );
                }
                return;
            }
        }

        const saved = await saveModelConfiguration();
        console.log('saveModelConfiguration returned:', saved);

        // Build prerequisite key from display numbers
        const currentDisplayNum = StepRegistry.idToDisplayNum(currentStep);
        const nextDisplayNum = StepRegistry.idToDisplayNum(nextStep);
        const prereqKey = `step${currentDisplayNum}_to_step${nextDisplayNum}`;

        const doNavigate = async () => {
            if (saved) {
                console.log('Save successful, navigating to step:', nextStep);
            } else {
                console.warn('Save returned false, navigating anyway to step:', nextStep);
            }

            // Persist step using async method if available
            if (typeof appState !== 'undefined' && appState.setAsync) {
                await appState.setAsync('currentStep', nextStep, { session: true });
            }

            await setActiveStep(nextStep);
        };

        // Check if prerequisite config exists for this transition
        const prereqConfig = window.PREREQUISITE_CONFIG || (typeof PREREQUISITE_CONFIG !== 'undefined' ? PREREQUISITE_CONFIG : null);
        console.log('[saveAndContinue] prereqKey:', prereqKey, 'hasConfig:', prereqConfig && !!prereqConfig[prereqKey]);

        if (prereqConfig && prereqConfig[prereqKey]) {
            showPrerequisiteModal(prereqKey, doNavigate);
        } else {
            await doNavigate();
        }
    } catch (error) {
        console.error('Error in saveAndContinue:', error);
        if (typeof showNavigationError === 'function') {
            showNavigationError('An error occurred. Please try again.', 'error');
        }
        // Even if save fails, try to navigate
        console.warn('Save threw error, attempting navigation to step:', nextStep);
        await setActiveStep(nextStep);
    } finally {
        // Release navigation lock and loading state
        if (typeof navigationInProgress !== 'undefined') {
            navigationInProgress = false;
        }
        if (buttonElement && typeof setNavigationLoading === 'function') {
            setNavigationLoading(buttonElement, false);
        }
    }
}

// Toggle collapsible sections (progressive disclosure)
function toggleSection(sectionId) {
    const content = document.getElementById(sectionId);
    const chevronId = sectionId.replace('-details', '-chevron');
    const chevron = document.getElementById(chevronId);

    if (content) {
        content.classList.toggle('hidden');
    }
    if (chevron) {
        chevron.classList.toggle('rotate-180');
    }
}

// Removed toggleRecommendationDetails - simplified to Stage 1 only

// Toggle SQL block visibility (for custom IDs)
function toggleSqlBlockById(blockId, chevronId) {
    const block = document.getElementById(blockId);
    const chevron = document.getElementById(chevronId);

    if (block) {
        const isHidden = block.classList.contains('hidden');
        block.classList.toggle('hidden');

        if (chevron) {
            if (isHidden) {
                chevron.style.transform = 'rotate(180deg)';
            } else {
                chevron.style.transform = 'rotate(0deg)';
            }
        }
    }
}

// Toggle SQL transformation tooltip
function toggleSqlTooltip(tooltipId) {
    // Close all other tooltips first
    document.querySelectorAll('.sql-tooltip-popup').forEach(tooltip => {
        if (tooltip.id !== tooltipId) {
            tooltip.classList.add('hidden');
        }
    });

    // Toggle the clicked tooltip and position it
    const tooltip = document.getElementById(tooltipId);
    if (tooltip) {
        const isHidden = tooltip.classList.contains('hidden');
        tooltip.classList.toggle('hidden');

        if (isHidden) {
            // Position tooltip near the button
            const btn = document.querySelector(`button[onclick="toggleSqlTooltip('${tooltipId}')"]`);
            if (btn) {
                const btnRect = btn.getBoundingClientRect();
                const container = btn.closest('.relative');
                const containerRect = container.getBoundingClientRect();

                tooltip.style.position = 'absolute';
                tooltip.style.left = `${btnRect.left - containerRect.left}px`;
                tooltip.style.top = `${btnRect.bottom - containerRect.top + 4}px`;
                tooltip.style.zIndex = '1000';
            }
        }
    }
}

// Helper: Escape regex special characters
function escapeRegex(string) {
    return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// Highlight original SQL with red diff markers for hardcoded tables
function highlightDiffOriginal(sql, hardcodedTables) {
    let highlighted = escapeHtml(sql);

    if (!hardcodedTables || hardcodedTables.length === 0) {
        return highlighted;
    }

    hardcodedTables.forEach(table => {
        const fullTableRef = table.table;
        // Highlight the hardcoded table reference with red diff styling
        const regex = new RegExp(`(\`?${escapeRegex(fullTableRef)}\`?)`, 'gi');
        highlighted = highlighted.replace(regex, '<span class="diff-highlight-removed">$1</span>');
    });

    return highlighted;
}

// Highlight transformed SQL with green diff markers for source() and ref() calls
function highlightDiffTransformed(sql, hardcodedTables) {
    let transformed = escapeHtml(sql);

    if (!hardcodedTables || hardcodedTables.length === 0) {
        return transformed;
    }

    hardcodedTables.forEach(table => {
        // Collect all possible replacements for this table to highlight
        const replacements = [];

        // Always check suggestedSource
        if (table.suggestedSource) {
            replacements.push(table.suggestedSource);
        }

        // Also check suggestedRef for self-references
        if (table.isSelfReference && table.suggestedRef) {
            replacements.push(table.suggestedRef);
        }

        // Highlight each found replacement
        replacements.forEach(replacement => {
            // The replacement is already escaped by escapeHtml above, so match the escaped version
            const escapedReplacement = escapeHtml(replacement);

            // Create a regex pattern that matches the escaped source/ref call
            // Need to escape special regex characters in the pattern
            const pattern = escapedReplacement
                .replace(/\\/g, '\\\\')
                .replace(/\(/g, '\\(')
                .replace(/\)/g, '\\)')
                .replace(/\{/g, '\\{')
                .replace(/\}/g, '\\}')
                .replace(/'/g, "\\'")
                .replace(/"/g, '\\"');

            const regex = new RegExp(pattern, 'g');
            transformed = transformed.replace(regex, `<span class="diff-highlight-added">${escapedReplacement}</span>`);
        });
    });

    return transformed;
}

// Setup synchronized scrolling for diff panels
function setupDiffSyncScroll() {
    document.querySelectorAll('.diff-container').forEach(container => {
        const panels = container.querySelectorAll('.diff-panel-content');
        if (panels.length < 2) return;

        let isScrolling = false;

        panels.forEach(panel => {
            panel.addEventListener('scroll', function() {
                if (isScrolling) return;
                isScrolling = true;

                const scrollTop = this.scrollTop;
                const scrollLeft = this.scrollLeft;

                panels.forEach(otherPanel => {
                    if (otherPanel !== this) {
                        otherPanel.scrollTop = scrollTop;
                        otherPanel.scrollLeft = scrollLeft;
                    }
                });

                requestAnimationFrame(() => {
                    isScrolling = false;
                });
            }, { passive: true });
        });
    });
}

// ============================================
// COMPLETION CHECKLIST - MOVED TO validation.js
// ============================================
// All validation functions (validateStepCompletion, validateStep0Completion, etc.)
// and rendering functions (renderCompletionChecklist, updateCompletionChecklist)
// are now in validation.js to avoid duplication and ensure consistent step numbering (1-10)
