// ============================================
// GLOSSARY - Searchable dbt & Data Terms
// ============================================

// Glossary terms organized by category
const GLOSSARY_TERMS = [
    // Core dbt Concepts
    {
        term: "dbt (data build tool)",
        category: "Core Concepts",
        definition: "A framework for managing data transformations in a data warehouse, replacing scheduled queries and custom frameworks. Provides version control and structure for data transformations.",
        example: null,
        relatedTerms: ["Model", "Materialization", "ref()"]
    },
    {
        term: "Model",
        category: "Core Concepts",
        definition: "A .sql file in the models/ directory containing a SELECT statement that dbt converts into a table or view. Models should contain only SELECT statements, no DDL like CREATE or INSERT.",
        example: "-- models/prep/prep_customers.sql\nSELECT \n  customer_id,\n  email,\n  created_at\nFROM {{ source('raw', 'customers') }}",
        relatedTerms: ["Materialization", "Prep Models", "Final Models"]
    },
    {
        term: "Materialization",
        category: "Core Concepts",
        definition: "How dbt builds your model in the database. Options include table (physical storage), view (virtual query), incremental (only new/changed rows), or ephemeral (CTE, not materialized).",
        example: "{{ config(materialized='table') }}\n\nSELECT * FROM ...",
        relatedTerms: ["Table", "View", "Incremental", "Ephemeral"]
    },

    // dbt Functions
    {
        term: "ref()",
        category: "dbt Functions",
        definition: "References another dbt model in the same project or across projects. Creates explicit dependencies that dbt uses to build the dependency graph. Syntax: {{ ref('model_name') }} or {{ ref('project_name', 'model_name') }} for cross-project refs.",
        example: "SELECT * FROM {{ ref('prep_customers') }}",
        relatedTerms: ["source()", "Cross-Project Dependencies", "Model"]
    },
    {
        term: "source()",
        category: "dbt Functions",
        definition: "References raw BigQuery tables/views that are NOT dbt models. Environment-aware, automatically uses dev/prod tables based on target. Enables freshness tests and schema tests.",
        example: "SELECT * FROM {{ source('raw_data', 'customers') }}",
        relatedTerms: ["ref()", "Source Definition", "Lineage"]
    },
    {
        term: "config()",
        category: "dbt Functions",
        definition: "Sets model-level configuration like materialization, schema, and tags at the top of a model file.",
        example: "{{ config(\n  materialized='table',\n  tags=['daily']\n) }}",
        relatedTerms: ["Materialization", "Tags"]
    },

    // Materialization Types
    {
        term: "Table",
        category: "Materialization Types",
        definition: "Physical storage of query results in BigQuery. Best for large, complex, or frequently accessed data. Results are stored on disk.",
        example: "{{ config(materialized='table') }}",
        relatedTerms: ["View", "Materialization", "Incremental"]
    },
    {
        term: "View",
        category: "Materialization Types",
        definition: "Virtual query that runs every time it's accessed. Best for simple transformations, when you need fresh data, or to save on storage costs. No physical storage.",
        example: "{{ config(materialized='view') }}",
        relatedTerms: ["Table", "Materialization"]
    },
    {
        term: "Incremental",
        category: "Materialization Types",
        definition: "Only processes new or changed records since the last run, instead of rebuilding the entire table. Best for large append-only datasets like event logs. Requires unique keys and merge logic.",
        example: "{{ config(\n  materialized='incremental',\n  unique_key='id'\n) }}",
        relatedTerms: ["Table", "Materialization", "dbt build"]
    },
    {
        term: "Ephemeral",
        category: "Materialization Types",
        definition: "A model that is compiled into dependent models as a CTE instead of being materialized as a physical object. No table or view is created. Use for reusable logic that doesn't need to be stored.",
        example: "{{ config(materialized='ephemeral') }}\n\n-- This becomes a CTE in models that ref() it",
        relatedTerms: ["CTE", "Materialization"]
    },

    // Model Patterns
    {
        term: "Prep Models",
        category: "Model Patterns",
        definition: "Preparation layer models (prep_*) containing cleaning, standardizing, transformations, and aggregations. The 'kitchen' where all the work happens before serving data.",
        example: "-- models/prep/prep_customers.sql\n-- All joins, filters, calculations here",
        relatedTerms: ["Final Models", "Model", "CTE"]
    },
    {
        term: "Final Models",
        category: "Model Patterns",
        definition: "Analytics-ready tables for business consumption. Simple SELECT * FROM prep models. The 'plated dish' that's ready to serve.",
        example: "-- models/final/customers.sql\nSELECT * FROM {{ ref('prep_customers') }}",
        relatedTerms: ["Prep Models", "Model"]
    },

    // Cross-Project Features
    {
        term: "Cross-Project Dependencies",
        category: "Advanced Features",
        definition: "A dbt Cloud feature that allows models from one project to reference models from another project using ref(), providing complete lineage visibility from raw sources through multiple projects. Only works in dbt Cloud.",
        example: "{{ ref('analytics_platform', 'dim_customer') }}",
        relatedTerms: ["ref()", "dependencies.yml", "access: public"]
    },
    {
        term: "dependencies.yml",
        category: "Advanced Features",
        definition: "A YAML file in the project root that declares which external dbt projects a project depends on. Required for cross-project refs.",
        example: "projects:\n  - name: analytics_platform",
        relatedTerms: ["Cross-Project Dependencies", "dbt Cloud"]
    },
    {
        term: "access: public",
        category: "Advanced Features",
        definition: "A configuration on dbt models that marks them as publicly accessible to other projects for cross-project references. Only models with this setting can be referenced via cross-project refs.",
        example: "# In schema.yml\nmodels:\n  - name: dim_customer\n    access: public",
        relatedTerms: ["Cross-Project Dependencies", "ref()"]
    },

    // Testing & Quality
    {
        term: "Schema Tests",
        category: "Testing & Quality",
        definition: "Data quality tests that validate column values. Common types: not_null (no nulls), unique (no duplicates), relationships (foreign keys), accepted_values (specific values only).",
        example: "columns:\n  - name: customer_id\n    tests:\n      - unique\n      - not_null",
        relatedTerms: ["dbt test", "dbt build", "Source Definition"]
    },
    {
        term: "dbt build",
        category: "dbt Commands",
        definition: "Command that materializes models AND runs their tests in one operation. Ensures models are never deployed without passing tests. Better than running dbt run and dbt test separately.",
        example: "dbt build --select +model_name",
        relatedTerms: ["dbt run", "dbt test", "Schema Tests"]
    },
    {
        term: "dbt run",
        category: "dbt Commands",
        definition: "Command that materializes selected models. Doesn't run tests automatically. Use dbt build instead to include tests.",
        example: "dbt run --select model_name",
        relatedTerms: ["dbt build", "dbt test", "Materialization"]
    },
    {
        term: "dbt test",
        category: "dbt Commands",
        definition: "Command that executes all declared schema and data-quality tests. Must be run separately from dbt run (unless using dbt build).",
        example: "dbt test --select model_name",
        relatedTerms: ["dbt build", "Schema Tests"]
    },
    {
        term: "dbt docs",
        category: "dbt Commands",
        definition: "Automatic documentation generation from model files and YAML. Shows models, lineage (DAG), column descriptions, and test results. Commands: dbt docs generate and dbt docs serve.",
        example: "dbt docs generate\ndbt docs serve",
        relatedTerms: ["DAG", "Lineage", "Doc Blocks"]
    },

    // Configuration & Structure
    {
        term: "Source Definition",
        category: "Configuration",
        definition: "YAML configuration declaring raw input tables with database, schema, and table names. Located in models/source/*.yml. Benefits: centralized, testable, environment-aware, enables lineage.",
        example: "sources:\n  - name: raw_data\n    tables:\n      - name: customers",
        relatedTerms: ["source()", "Schema Tests", "Lineage"]
    },
    {
        term: "Tags",
        category: "Configuration",
        definition: "Labels on models that allow selective execution. Like Gmail labels for your models. Use to run specific groups: dbt run --select tag:daily",
        example: "{{ config(tags=['daily', 'pii']) }}",
        relatedTerms: ["config()", "dbt run"]
    },
    {
        term: "dbt_project.yml",
        category: "Configuration",
        definition: "Main configuration file for a dbt project in the project root. Contains project name, version, model paths, dataset configurations, and profiles.",
        example: "name: 'my_project'\nversion: '1.0.0'\nprofile: 'default'",
        relatedTerms: ["Project Root", "dependencies.yml"]
    },
    {
        term: "Project Root",
        category: "Configuration",
        definition: "Base directory of a dbt project containing dbt_project.yml. Key files: dbt_project.yml, dependencies.yml, models/, macros/, tests/",
        example: null,
        relatedTerms: ["dbt_project.yml", "dependencies.yml"]
    },

    // Advanced Concepts
    {
        term: "Lineage",
        category: "Concepts",
        definition: "The visual representation of how data flows through transformations from sources to final models. Shown as a DAG (graph) in dbt docs. Cross-project refs enable 'complete lineage' while sources 'stop lineage'.",
        example: null,
        relatedTerms: ["DAG", "ref()", "source()", "dbt docs"]
    },
    {
        term: "DAG (Directed Acyclic Graph)",
        category: "Concepts",
        definition: "Visual representation of all models and their dependencies. Shown in dbt docs to understand project structure and lineage. 'Directed' means arrows point from upstream to downstream. 'Acyclic' means no circular dependencies.",
        example: null,
        relatedTerms: ["Lineage", "dbt docs", "ref()"]
    },
    {
        term: "Jinja Templating",
        category: "Concepts",
        definition: "A templating language that generates SQL dynamically. Used in dbt for {{ ref() }}, {{ source() }}, {{ config() }}, {{ doc() }}, and custom macros.",
        example: "{% set my_date = '2024-01-01' %}\nSELECT * FROM table WHERE date = '{{ my_date }}'",
        relatedTerms: ["ref()", "source()", "config()"]
    },
    {
        term: "CTE (Common Table Expression)",
        category: "Concepts",
        definition: "Temporary named result set used within a SQL query (the WITH clause). dbt pattern: Large CTEs should be broken into separate prep models for better organization and testing.",
        example: "WITH customer_orders AS (\n  SELECT customer_id, COUNT(*) as order_count\n  FROM orders\n  GROUP BY customer_id\n)\nSELECT * FROM customer_orders",
        relatedTerms: ["Prep Models", "Ephemeral"]
    },
    {
        term: "DDL (Data Definition Language)",
        category: "Concepts",
        definition: "SQL statements for creating/modifying database objects: CREATE TABLE, INSERT INTO, UPDATE, DROP. dbt rule: Don't write DDL inside model files; use materialization configs instead.",
        example: "-- ❌ Don't do this in dbt:\nCREATE TABLE customers AS SELECT ...\n\n-- ✅ Do this instead:\n{{ config(materialized='table') }}\nSELECT ...",
        relatedTerms: ["Model", "Materialization", "config()"]
    },
    {
        term: "Doc Blocks",
        category: "Documentation",
        definition: "Reusable documentation snippets defined in .md files. Syntax: {% docs block_name %} ... {% enddocs %}. Reference with {{ doc('block_name') }} in YAML. Follows DRY principle - write once, use everywhere.",
        example: "{% docs customer_id %}\nUnique identifier for a customer.\n{% enddocs %}\n\n# In schema.yml:\n{{ doc('customer_id') }}",
        relatedTerms: ["dbt docs", "DRY"]
    },
    {
        term: "DRY (Don't Repeat Yourself)",
        category: "Best Practices",
        definition: "Programming principle of avoiding duplication. In dbt: use doc blocks for repeated descriptions and reusable prep models for common transformations.",
        example: null,
        relatedTerms: ["Doc Blocks", "Prep Models"]
    },

    // Tools & Platforms
    {
        term: "dbt Cloud",
        category: "Tools & Platforms",
        definition: "Managed hosting service for dbt with IDE, job scheduling, and observability features. Only place where cross-project dependencies work. Provides web-based development environment.",
        example: null,
        relatedTerms: ["Cross-Project Dependencies", "dbt Cloud IDE"]
    },
    {
        term: "Compiled SQL",
        category: "Debugging",
        definition: "The final SQL that dbt generates after processing Jinja templates and refs/sources. Located in target/compiled/ directory. Useful for debugging and understanding what SQL dbt actually runs.",
        example: "# Check compiled SQL:\ntarget/compiled/my_project/models/prep/prep_customers.sql",
        relatedTerms: ["Jinja Templating", "ref()", "source()"]
    },
    {
        term: "Feature Branch",
        category: "Version Control",
        definition: "A temporary git branch for developing features. Convention: feature/your-feature-name. Best practice: Never commit directly to main.",
        example: "git checkout -b feature/add-customer-model\ngit add .\ngit commit -m \"Add customer model\"\ngit push",
        relatedTerms: ["Version Control"]
    },
    {
        term: "Version Control",
        category: "Version Control",
        definition: "Tracking changes to code using git. Every transformation change is tracked and reviewable. Enables collaboration, rollbacks, and audit trails.",
        example: null,
        relatedTerms: ["Feature Branch", "dbt"]
    },

    // Legacy Terms
    {
        term: "Scheduled Queries",
        category: "Legacy Concepts",
        definition: "BigQuery queries that run on a schedule (the legacy approach being converted to dbt). Problem: Lack of version control and proper structure for changes.",
        example: null,
        relatedTerms: ["dbt", "Version Control"]
    }
];

// Glossary state
let glossaryOpen = false;
let searchQuery = '';
let selectedCategory = 'All';

// Get all unique categories
function getCategories() {
    const categories = ['All', ...new Set(GLOSSARY_TERMS.map(t => t.category))];
    return categories.sort();
}

// Filter terms based on search and category
function filterTerms(query, category) {
    let filtered = GLOSSARY_TERMS;

    // Filter by category
    if (category && category !== 'All') {
        filtered = filtered.filter(t => t.category === category);
    }

    // Filter by search query
    if (query && query.trim()) {
        const q = query.toLowerCase();
        filtered = filtered.filter(t =>
            t.term.toLowerCase().includes(q) ||
            t.definition.toLowerCase().includes(q) ||
            t.category.toLowerCase().includes(q) ||
            (t.relatedTerms && t.relatedTerms.some(rt => rt.toLowerCase().includes(q)))
        );
    }

    // Sort alphabetically
    return filtered.sort((a, b) => a.term.localeCompare(b.term));
}

// Open glossary modal
function openGlossary() {
    glossaryOpen = true;
    searchQuery = '';
    selectedCategory = 'All';
    renderGlossary();
    document.body.style.overflow = 'hidden';
}

// Close glossary modal
function closeGlossary() {
    glossaryOpen = false;
    const modal = document.getElementById('glossary-modal');
    if (modal) {
        modal.remove();
    }
    document.body.style.overflow = '';
}

// Navigate to a specific term
function navigateToTerm(termName) {
    searchQuery = termName;
    selectedCategory = 'All';
    renderGlossaryContent();

    // Scroll to the term
    setTimeout(() => {
        const termElement = document.querySelector(`[data-term="${termName}"]`);
        if (termElement) {
            termElement.scrollIntoView({ behavior: 'smooth', block: 'start' });
            termElement.style.backgroundColor = '#fef3c7';
            setTimeout(() => {
                termElement.style.backgroundColor = '';
            }, 2000);
        }
    }, 100);
}

// Render glossary modal
function renderGlossary() {
    if (!glossaryOpen) return;

    // Remove existing modal
    const existing = document.getElementById('glossary-modal');
    if (existing) existing.remove();

    const modal = document.createElement('div');
    modal.id = 'glossary-modal';
    modal.className = 'fixed inset-0 z-50 flex items-center justify-center';
    modal.style.backgroundColor = 'rgba(0, 0, 0, 0.5)';

    modal.innerHTML = `
        <div class="bg-white rounded-lg shadow-2xl w-full max-w-5xl h-[90vh] flex flex-col" style="margin: 20px;">
            <!-- Header -->
            <div class="flex items-center justify-between p-6 border-b">
                <div>
                    <h2 class="text-2xl font-bold text-gray-900">DBT Training Wheels Glossary</h2>
                    <p class="text-sm text-gray-500 mt-1">Search dbt and data terms</p>
                </div>
                <button onclick="closeGlossary()" class="text-gray-400 hover:text-gray-600">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
                    </svg>
                </button>
            </div>

            <!-- Search & Filter -->
            <div class="p-6 border-b space-y-3">
                <div class="relative">
                    <input
                        type="text"
                        id="glossary-search"
                        placeholder="Search terms, definitions, or categories..."
                        class="w-full px-4 py-2 pl-10 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                        value="${searchQuery}"
                        oninput="handleGlossarySearch(this.value)"
                    >
                    <svg class="w-5 h-5 absolute left-3 top-2.5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
                    </svg>
                </div>

                <div class="flex gap-2 flex-wrap" id="glossary-categories">
                    ${getCategories().map(cat => `
                        <button
                            onclick="handleCategoryFilter('${cat}')"
                            class="px-3 py-1 text-sm rounded-full transition-colors ${
                                cat === selectedCategory
                                    ? 'bg-blue-100 text-blue-700 font-medium'
                                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                            }"
                        >
                            ${cat}
                        </button>
                    `).join('')}
                </div>
            </div>

            <!-- Content -->
            <div class="flex-1 overflow-y-auto p-6" id="glossary-content">
                <!-- Will be populated by renderGlossaryContent() -->
            </div>
        </div>
    `;

    document.body.appendChild(modal);
    renderGlossaryContent();

    // Focus search input
    setTimeout(() => {
        document.getElementById('glossary-search')?.focus();
    }, 100);
}

// Render glossary content
function renderGlossaryContent() {
    const container = document.getElementById('glossary-content');
    if (!container) return;

    const filtered = filterTerms(searchQuery, selectedCategory);

    if (filtered.length === 0) {
        container.innerHTML = `
            <div class="text-center py-12">
                <svg class="w-16 h-16 mx-auto text-gray-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                </svg>
                <p class="text-gray-500 text-lg">No terms found</p>
                <p class="text-gray-400 text-sm mt-2">Try a different search or category</p>
            </div>
        `;
        return;
    }

    container.innerHTML = `
        <div class="text-sm text-gray-500 mb-4">
            Showing ${filtered.length} term${filtered.length !== 1 ? 's' : ''}
        </div>
        <div class="space-y-6">
            ${filtered.map(term => `
                <div class="border rounded-lg p-5 hover:shadow-md transition-shadow" data-term="${term.term}">
                    <div class="flex items-start justify-between mb-2">
                        <h3 class="text-xl font-bold text-gray-900">${escapeHtml(term.term)}</h3>
                        <span class="px-2 py-1 text-xs font-medium rounded-full bg-blue-50 text-blue-700">
                            ${escapeHtml(term.category)}
                        </span>
                    </div>

                    <p class="text-gray-700 mb-3">${escapeHtml(term.definition)}</p>

                    ${term.example ? `
                        <div class="mb-3">
                            <div class="text-sm font-medium text-gray-600 mb-1">Example:</div>
                            <pre class="text-xs bg-gray-50 p-3 rounded border overflow-x-auto"><code>${escapeHtml(term.example)}</code></pre>
                        </div>
                    ` : ''}

                    ${term.relatedTerms && term.relatedTerms.length > 0 ? `
                        <div class="flex gap-2 flex-wrap items-center">
                            <span class="text-sm text-gray-500">Related:</span>
                            ${term.relatedTerms.map(rt => `
                                <button
                                    onclick="navigateToTerm('${rt}')"
                                    class="text-sm text-blue-600 hover:text-blue-800 hover:underline"
                                >
                                    ${escapeHtml(rt)}
                                </button>
                            `).join(' • ')}
                        </div>
                    ` : ''}
                </div>
            `).join('')}
        </div>
    `;
}

// Handle search input
function handleGlossarySearch(value) {
    searchQuery = value;
    renderGlossaryContent();
}

// Handle category filter
function handleCategoryFilter(category) {
    selectedCategory = category;
    renderGlossary();
}

// Keyboard shortcuts for glossary
document.addEventListener('keydown', (e) => {
    // Escape to close
    if (e.key === 'Escape' && glossaryOpen) {
        closeGlossary();
    }

    // Cmd/Ctrl + K to open glossary
    if ((e.metaKey || e.ctrlKey) && e.key === 'k' && !glossaryOpen) {
        e.preventDefault();
        openGlossary();
    }
});
