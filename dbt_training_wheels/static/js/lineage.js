// ============================================
// LINEAGE VISUALIZATION WITH CYTOSCAPE.JS
// ============================================

let cyInstance = null;

// Main entry point - called from step8-review.js
function initializeLineageDiagram() {
    const container = document.getElementById('lineage-container');
    if (!container) {
        console.warn('Lineage container not found');
        return;
    }

    // Check if Cytoscape is loaded
    if (typeof cytoscape === 'undefined') {
        container.innerHTML = `
            <div class="flex items-center justify-center h-full text-gray-500">
                <div class="text-center">
                    <svg class="w-12 h-12 mx-auto mb-2 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
                    </svg>
                    <p>Lineage visualization library not loaded</p>
                </div>
            </div>
        `;
        return;
    }

    // Build lineage data from analysis results
    const { nodes, edges } = buildLineageData();

    if (nodes.length === 0) {
        container.innerHTML = `
            <div class="flex items-center justify-center h-full text-gray-500">
                <div class="text-center">
                    <svg class="w-12 h-12 mx-auto mb-2 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4"></path>
                    </svg>
                    <p>No models found to visualize</p>
                </div>
            </div>
        `;
        return;
    }

    // Destroy existing instance if any
    if (cyInstance) {
        cyInstance.destroy();
    }

    // Initialize Cytoscape
    cyInstance = cytoscape({
        container: container,
        elements: [...nodes, ...edges],
        style: getLineageStyles(),
        layout: getLayoutConfig(),
        minZoom: 0.3,
        maxZoom: 2,
        wheelSensitivity: 0.3,
    });

    // Add click handler for nodes
    cyInstance.on('tap', 'node', function(evt) {
        const node = evt.target;
        showNodeDetails(node);
    });

    // Add hover effects
    cyInstance.on('mouseover', 'node', function(evt) {
        const node = evt.target;
        node.addClass('hover');
        container.style.cursor = 'pointer';
    });

    cyInstance.on('mouseout', 'node', function(evt) {
        const node = evt.target;
        node.removeClass('hover');
        container.style.cursor = 'default';
    });

    // Fit to container with padding
    cyInstance.fit(50);
}

// Build nodes and edges from analysis results
function buildLineageData() {
    const nodes = [];
    const edges = [];
    const nodeIds = new Set();
    const edgeIds = new Set();

    // 1. Add source nodes (external tables) and cross-project ref nodes
    const sources = analysisResults?.hardcodedTables?.filter(t => !t.isSelfReference) || [];
    const sourceMap = {}; // Map sourceTable name to node ID for edge creation
    const crossProjectMap = {}; // Map cross-project model names to node IDs

    sources.forEach((source, idx) => {
        const parts = source.table.split('.');
        const tableName = parts[parts.length - 1];
        const nodeId = `source_${idx}`;

        if (!nodeIds.has(nodeId)) {
            nodeIds.add(nodeId);

            // Determine node type and styling based on whether it's a cross-project ref
            const isCrossProject = source.isCrossProjectRef === true;
            const nodeType = isCrossProject ? 'cross-project' : 'source';

            // Store mapping for edge creation
            if (isCrossProject) {
                // For cross-project refs, map by the model name
                const modelName = source.crossProjectModel || tableName;
                crossProjectMap[modelName.toLowerCase()] = nodeId;
                // Also map with project prefix
                if (source.crossProjectProject) {
                    crossProjectMap[`${source.crossProjectProject}.${modelName}`.toLowerCase()] = nodeId;
                }
            } else {
                sourceMap[tableName.toLowerCase()] = nodeId;
                if (source.sourceTable) {
                    sourceMap[source.sourceTable.toLowerCase()] = nodeId;
                }
            }

            nodes.push({
                data: {
                    id: nodeId,
                    label: isCrossProject ? `${source.crossProjectProject}.${source.crossProjectModel}` : tableName,
                    fullName: source.table,
                    type: nodeType,
                    suggestedSource: source.suggestedSource,
                    sourceTable: source.sourceTable,
                    crossProjectProject: source.crossProjectProject,
                    crossProjectModel: source.crossProjectModel,
                }
            });
        }
    });

    // 2. Add staging model nodes (transformation logic)
    // currentQuery.tables is an array of strings (table names), not objects
    const stagingModels = currentQuery?.tables || [];
    const stagingMap = {}; // Map staging model name to node ID
    const stagingPrefix = window.orgConfig?.naming?.staging_model_prefix || '';

    stagingModels.forEach((tableName, idx) => {
        const modelName = typeof tableName === 'string' ? tableName : (tableName.name || tableName.targetTable || `staging_model_${idx + 1}`);
        const nodeId = `staging_${idx}`;

        if (!nodeIds.has(nodeId)) {
            nodeIds.add(nodeId);
            stagingMap[modelName.toLowerCase()] = nodeId;
            stagingMap[`${stagingPrefix}${modelName}`.toLowerCase()] = nodeId;

            const config = getModelConfig(idx);
            nodes.push({
                data: {
                    id: nodeId,
                    label: `${stagingPrefix}${modelName}`,
                    type: 'staging',
                    schema: config.schema || 'default',
                    materialization: config.materialization || 'table',
                    tags: config.tags || [],
                    baseName: modelName,
                }
            });
        }
    });

    // 3. Add final model nodes
    const finalModels = analysisResults?.finalTableSqls || [];
    const finalMap = {}; // Map final model name to node ID

    finalModels.forEach((model, idx) => {
        const baseName = model.targetTable || model.table || `model_${idx + 1}`;
        const nodeId = `final_${idx}`;

        if (!nodeIds.has(nodeId)) {
            nodeIds.add(nodeId);
            finalMap[baseName.toLowerCase()] = nodeId;
            finalMap[`final__${baseName}`.toLowerCase()] = nodeId;

            const configIdx = prepModels.length + idx;
            const config = getModelConfig(configIdx);
            nodes.push({
                data: {
                    id: nodeId,
                    label: `final__${baseName}`,
                    type: 'final',
                    schema: config.schema || 'default',
                    materialization: config.materialization || 'table',
                    tags: config.tags || [],
                    baseName: baseName,
                    sql: model.sql,
                }
            });
        }
    });

    // 4. Helper to add edge if not duplicate
    const addEdge = (sourceId, targetId) => {
        const edgeId = `edge_${sourceId}_${targetId}`;
        if (!edgeIds.has(edgeId)) {
            edgeIds.add(edgeId);
            edges.push({
                data: {
                    id: edgeId,
                    source: sourceId,
                    target: targetId,
                }
            });
        }
    };

    // 5. Build lineage: Sources → Prep → Final
    // The flow is: Sources feed into Prep models, Prep models feed into Final models

    // Step A: Connect Sources → Prep models
    // Parse the SQL in finalTableSqls to find which sources each prep model uses
    let foundSourceToPrep = false;

    prepModels.forEach((tableName, prepIdx) => {
        const modelName = typeof tableName === 'string' ? tableName : tableName.name;
        const prepNodeId = `prep_${prepIdx}`;

        // Find the corresponding SQL for this prep model
        const matchingModel = finalModels.find(f =>
            (f.table && f.table.toLowerCase() === modelName?.toLowerCase()) ||
            (f.targetTable && f.targetTable.toLowerCase() === modelName?.toLowerCase())
        );

        if (matchingModel && matchingModel.sql) {
            const sql = matchingModel.sql;

            // Try multiple patterns for source() and ref() calls
            // Pattern 1: {{ source('schema', 'table') }}
            // Pattern 2: source('schema', 'table')
            // Pattern 3: {{ ref('project', 'model') }} - cross-project ref
            // Pattern 4: ref('project', 'model') - cross-project ref without curly braces
            const sourcePatterns = [
                /\{\{\s*source\s*\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)\s*\}\}/gi,
                /source\s*\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)/gi
            ];

            const crossProjectRefPatterns = [
                /\{\{\s*ref\s*\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)\s*\}\}/gi,
                /ref\s*\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)/gi
            ];

            // Check for source() calls
            for (const pattern of sourcePatterns) {
                let match;
                while ((match = pattern.exec(sql)) !== null) {
                    const sourceTable = match[2].toLowerCase();
                    if (sourceMap[sourceTable]) {
                        addEdge(sourceMap[sourceTable], prepNodeId);
                        foundSourceToPrep = true;
                    }
                }
            }

            // Check for cross-project ref() calls
            for (const pattern of crossProjectRefPatterns) {
                let match;
                while ((match = pattern.exec(sql)) !== null) {
                    const project = match[1].toLowerCase();
                    const model = match[2].toLowerCase();
                    const refKey = model;
                    const refKeyWithProject = `${project}.${model}`;

                    // Try to find matching cross-project node
                    if (crossProjectMap[refKey] || crossProjectMap[refKeyWithProject]) {
                        const nodeId = crossProjectMap[refKey] || crossProjectMap[refKeyWithProject];
                        addEdge(nodeId, prepNodeId);
                        foundSourceToPrep = true;
                    }
                }
            }
        }
    });

    // Fallback: If no source→prep edges found, connect based on hardcodedTables
    // Each source in hardcodedTables was found in the SQL, so connect to prep models
    if (!foundSourceToPrep && prepModels.length > 0 && sources.length > 0) {
        // Connect all sources to all prep models as fallback
        sources.forEach((_, sourceIdx) => {
            prepModels.forEach((_, prepIdx) => {
                addEdge(`source_${sourceIdx}`, `prep_${prepIdx}`);
            });
        });
    }

    // Step B: Connect Prep → Final models
    // Each prep model feeds into its corresponding final model (by name matching)
    prepModels.forEach((tableName, prepIdx) => {
        const modelName = typeof tableName === 'string' ? tableName : tableName.name;
        const prepNodeId = `prep_${prepIdx}`;

        // Find the matching final model by base name
        finalModels.forEach((finalModel, finalIdx) => {
            const finalBaseName = finalModel.targetTable || finalModel.table || '';

            // Connect prep to final if they share the same base name
            if (finalBaseName.toLowerCase() === modelName?.toLowerCase()) {
                addEdge(prepNodeId, `final_${finalIdx}`);
            }
        });
    });

    // 6. Fallback: If no prep→final edges, maybe the architecture is different
    // Check if final models have ref() calls to prep models
    const hasPrep2FinalEdges = edges.some(e =>
        e.data.source.startsWith('prep_') && e.data.target.startsWith('final_')
    );

    if (!hasPrep2FinalEdges && prepModels.length > 0 && finalModels.length > 0) {
        // Try parsing ref() calls in final model SQL
        finalModels.forEach((finalModel, finalIdx) => {
            const sql = finalModel.sql || '';

            // Pattern for two-arg ref() - cross-project refs (check this FIRST)
            const twoArgRefPattern = /ref\s*\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)/gi;
            // Pattern for single-arg ref() - local refs (check AFTER two-arg to avoid false matches)
            const singleArgRefPattern = /ref\s*\(\s*['"]([^'"]+)['"]\s*\)(?!\s*,)/gi;

            // Check two-arg refs FIRST (cross-project refs)
            let match;
            while ((match = twoArgRefPattern.exec(sql)) !== null) {
                const project = match[1].toLowerCase();
                const model = match[2].toLowerCase();
                const refKey = model;
                const refKeyWithProject = `${project}.${model}`;

                // Try to find matching cross-project node
                if (crossProjectMap[refKey] || crossProjectMap[refKeyWithProject]) {
                    const nodeId = crossProjectMap[refKey] || crossProjectMap[refKeyWithProject];
                    addEdge(nodeId, `final_${finalIdx}`);
                }
            }

            // Then check single-arg refs (local staging models)
            while ((match = singleArgRefPattern.exec(sql)) !== null) {
                const refModel = match[1].toLowerCase();
                // Check if this ref matches any staging model
                stagingModels.forEach((tableName, stagingIdx) => {
                    const modelName = typeof tableName === 'string' ? tableName : tableName.name;
                    if (refModel === modelName?.toLowerCase() ||
                        refModel === `${stagingPrefix}${modelName}`.toLowerCase()) {
                        addEdge(`staging_${stagingIdx}`, `final_${finalIdx}`);
                    }
                });
            }
        });
    }

    // 7. Final fallback: If still no edges, create simple linear connections
    if (edges.length === 0) {
        // Connect sources to preps, preps to finals
        if (prepModels.length > 0) {
            sources.forEach((_, sourceIdx) => {
                prepModels.forEach((_, prepIdx) => {
                    addEdge(`source_${sourceIdx}`, `prep_${prepIdx}`);
                });
            });
            prepModels.forEach((_, prepIdx) => {
                finalModels.forEach((_, finalIdx) => {
                    addEdge(`prep_${prepIdx}`, `final_${finalIdx}`);
                });
            });
        } else {
            // No prep models, connect sources directly to finals
            sources.forEach((_, sourceIdx) => {
                finalModels.forEach((_, finalIdx) => {
                    addEdge(`source_${sourceIdx}`, `final_${finalIdx}`);
                });
            });
        }
    }

    return { nodes, edges };
}

// Cytoscape styles for nodes and edges
function getLineageStyles() {
    return [
        // Base node style
        {
            selector: 'node',
            style: {
                'label': 'data(label)',
                'text-valign': 'center',
                'text-halign': 'center',
                'font-size': '11px',
                'font-family': 'ui-monospace, SFMono-Regular, monospace',
                'text-wrap': 'wrap',
                'text-max-width': '120px',
                'width': '140px',
                'height': '50px',
                'shape': 'roundrectangle',
                'border-width': 2,
                'padding': '10px',
            }
        },
        // Source nodes (purple)
        {
            selector: 'node[type="source"]',
            style: {
                'background-color': '#f3e8ff',
                'border-color': '#9333ea',
                'color': '#581c87',
            }
        },
        // Cross-project ref nodes (orange - from another dbt project)
        {
            selector: 'node[type="cross-project"]',
            style: {
                'background-color': '#ffedd5',
                'border-color': '#ea580c',
                'color': '#7c2d12',
                'border-width': 3,
                'border-style': 'dashed',
            }
        },
        // Prep model nodes (blue)
        {
            selector: 'node[type="prep"]',
            style: {
                'background-color': '#dbeafe',
                'border-color': '#2563eb',
                'color': '#1e3a8a',
            }
        },
        // Final model nodes (green)
        {
            selector: 'node[type="final"]',
            style: {
                'background-color': '#dcfce7',
                'border-color': '#16a34a',
                'color': '#14532d',
            }
        },
        // Hover state
        {
            selector: 'node.hover',
            style: {
                'border-width': 3,
                'shadow-blur': 10,
                'shadow-color': '#00000030',
                'shadow-offset-x': 0,
                'shadow-offset-y': 2,
                'shadow-opacity': 1,
            }
        },
        // Selected state
        {
            selector: 'node:selected',
            style: {
                'border-width': 3,
                'border-color': '#4f46e5',
            }
        },
        // Edge style
        {
            selector: 'edge',
            style: {
                'width': 2,
                'line-color': '#94a3b8',
                'target-arrow-color': '#94a3b8',
                'target-arrow-shape': 'triangle',
                'curve-style': 'bezier',
                'arrow-scale': 1.2,
            }
        },
        // Edge hover
        {
            selector: 'edge.hover',
            style: {
                'line-color': '#4f46e5',
                'target-arrow-color': '#4f46e5',
                'width': 3,
            }
        }
    ];
}

// Layout configuration for dagre (hierarchical left-to-right)
function getLayoutConfig() {
    // Check if dagre layout is available
    if (typeof cytoscape !== 'undefined' && cytoscape('layout', 'dagre')) {
        return {
            name: 'dagre',
            rankDir: 'LR', // Left to Right
            nodeSep: 60,
            rankSep: 100,
            padding: 30,
            animate: true,
            animationDuration: 500,
        };
    }

    // Fallback to breadthfirst if dagre not available
    return {
        name: 'breadthfirst',
        directed: true,
        padding: 30,
        spacingFactor: 1.5,
        animate: true,
        animationDuration: 500,
    };
}

// Show node details in a popup
function showNodeDetails(node) {
    const data = node.data();

    // Remove any existing popup
    const existingPopup = document.getElementById('lineage-node-popup');
    if (existingPopup) {
        existingPopup.remove();
    }

    // Build popup content based on node type
    let detailsHTML = '';

    if (data.type === 'source') {
        detailsHTML = `
            <div class="font-semibold text-purple-700 mb-2 flex items-center gap-2">
                <div class="w-3 h-3 rounded bg-purple-100 border-2 border-purple-600"></div>
                Source
            </div>
            <div class="space-y-1 text-sm">
                <div><span class="text-gray-500">Table:</span> <code class="bg-gray-100 px-1 rounded">${data.fullName || data.label}</code></div>
                <div><span class="text-gray-500">dbt ref:</span> <code class="bg-gray-100 px-1 rounded">${data.suggestedSource || 'source(...)'}</code></div>
            </div>
        `;
    } else if (data.type === 'cross-project') {
        detailsHTML = `
            <div class="font-semibold text-orange-700 mb-2 flex items-center gap-2">
                <div class="w-3 h-3 rounded bg-orange-100 border-2 border-orange-600" style="border-style: dashed;"></div>
                Cross-Project Reference
            </div>
            <div class="space-y-1 text-sm">
                <div><span class="text-gray-500">Project:</span> <code class="bg-gray-100 px-1 rounded">${data.crossProjectProject || 'unknown'}</code></div>
                <div><span class="text-gray-500">Model:</span> <code class="bg-gray-100 px-1 rounded">${data.crossProjectModel || data.label}</code></div>
                <div><span class="text-gray-500">dbt ref:</span> <code class="bg-gray-100 px-1 rounded">${data.suggestedSource || `ref('${data.crossProjectProject}', '${data.crossProjectModel}')`}</code></div>
                <div class="text-xs text-gray-500 mt-2">This model exists in another dbt project</div>
            </div>
        `;
    } else if (data.type === 'prep') {
        detailsHTML = `
            <div class="font-semibold text-blue-700 mb-2 flex items-center gap-2">
                <div class="w-3 h-3 rounded bg-blue-100 border-2 border-blue-600"></div>
                Prep Model
            </div>
            <div class="space-y-1 text-sm">
                <div><span class="text-gray-500">Model:</span> <code class="bg-gray-100 px-1 rounded">${data.label}</code></div>
                <div><span class="text-gray-500">Schema:</span> <code class="bg-gray-100 px-1 rounded">${data.schema}</code></div>
                <div><span class="text-gray-500">Materialization:</span> <code class="bg-gray-100 px-1 rounded">${data.materialization}</code></div>
                <div><span class="text-gray-500">Tags:</span> ${data.tags?.length > 0 ? data.tags.map(t => `<span class="inline-block bg-gray-100 px-1 rounded text-xs">${t}</span>`).join(' ') : '<span class="text-gray-400">none</span>'}</div>
            </div>
        `;
    } else if (data.type === 'final') {
        detailsHTML = `
            <div class="font-semibold text-green-700 mb-2 flex items-center gap-2">
                <div class="w-3 h-3 rounded bg-green-100 border-2 border-green-600"></div>
                Final Model
            </div>
            <div class="space-y-1 text-sm">
                <div><span class="text-gray-500">Model:</span> <code class="bg-gray-100 px-1 rounded">${data.label}</code></div>
                <div><span class="text-gray-500">Schema:</span> <code class="bg-gray-100 px-1 rounded">${data.schema}</code></div>
                <div><span class="text-gray-500">Materialization:</span> <code class="bg-gray-100 px-1 rounded">${data.materialization}</code></div>
                <div><span class="text-gray-500">Tags:</span> ${data.tags?.length > 0 ? data.tags.map(t => `<span class="inline-block bg-gray-100 px-1 rounded text-xs">${t}</span>`).join(' ') : '<span class="text-gray-400">none</span>'}</div>
            </div>
        `;
    }

    // Create popup element
    const popup = document.createElement('div');
    popup.id = 'lineage-node-popup';
    popup.className = 'absolute bg-white border border-gray-200 rounded-lg shadow-lg p-4 z-50 max-w-xs';

    // Position popup near the node
    const container = document.getElementById('lineage-container');
    const containerRect = container.getBoundingClientRect();
    const nodePosition = node.renderedPosition();

    // Calculate popup position (offset from node)
    let left = nodePosition.x + 80;
    let top = nodePosition.y - 50;

    // Ensure popup stays within container bounds
    if (left + 250 > containerRect.width) {
        left = nodePosition.x - 260;
    }
    if (top < 10) {
        top = 10;
    }

    popup.style.left = `${left}px`;
    popup.style.top = `${top}px`;

    popup.innerHTML = `
        <button onclick="this.parentElement.remove()" class="absolute top-2 right-2 text-gray-400 hover:text-gray-600">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
            </svg>
        </button>
        ${detailsHTML}
    `;

    container.appendChild(popup);

    // Close popup when clicking elsewhere
    const closeHandler = (e) => {
        if (!popup.contains(e.target) && e.target !== node.renderedDomElement()) {
            popup.remove();
            document.removeEventListener('click', closeHandler);
        }
    };
    setTimeout(() => document.addEventListener('click', closeHandler), 100);
}

// Refresh lineage diagram (can be called when data changes)
function refreshLineageDiagram() {
    if (cyInstance) {
        const { nodes, edges } = buildLineageData();
        cyInstance.elements().remove();
        cyInstance.add([...nodes, ...edges]);
        cyInstance.layout(getLayoutConfig()).run();
        cyInstance.fit(50);
    }
}

// Destroy lineage diagram (cleanup)
function destroyLineageDiagram() {
    if (cyInstance) {
        cyInstance.destroy();
        cyInstance = null;
    }
}
