// ============================================
// HOME
// ============================================
// The landing view's own behaviour: switching between the three ways of bringing
// SQL in, and turning a pasted query into an upload.
//
// Paste deliberately does not get its own endpoint. /api/upload already validates
// the extension, guards the size, handles the name collision and reports a
// recoverable conflict -- so a pasted query is packaged as a File and handed to
// handleFileUpload() like any other. One code path, one set of error messages.

const HOME_INTAKES = ['folder', 'file', 'paste'];

function selectHomeIntake(which) {
    if (!HOME_INTAKES.includes(which)) return;

    HOME_INTAKES.forEach(name => {
        const tab = document.getElementById(`home-tab-${name}`);
        const panel = document.getElementById(`home-panel-${name}`);
        const isSelected = name === which;

        if (tab) {
            tab.classList.toggle('is-active', isSelected);
            tab.setAttribute('aria-selected', String(isSelected));
        }
        if (panel) panel.hidden = !isSelected;
    });

    if (which === 'paste') {
        document.getElementById('home-paste-sql')?.focus();
    }
}

// Restores the note under the paste box after an inline complaint.
const HOME_PASTE_DEFAULT_NOTE = '<code>.sql</code> is added for you.';

function setHomePasteNote(html, isProblem) {
    const note = document.getElementById('home-paste-note');
    if (!note) return;
    note.innerHTML = html;
    note.style.color = isProblem ? 'var(--brand-error)' : '';
}

// A model name has to survive being a filename, a dbt model identifier and a YAML
// key, so anything outside [a-z0-9_] becomes an underscore.
function slugifyQueryName(raw) {
    return raw
        .toLowerCase()
        .replace(/\.sql$/, '')
        .replace(/[^a-z0-9]+/g, '_')
        .replace(/^_+|_+$/g, '')
        .replace(/_{2,}/g, '_');
}

function submitPastedQuery() {
    const sqlField = document.getElementById('home-paste-sql');
    const nameField = document.getElementById('home-paste-name');
    if (!sqlField || !nameField) return;

    const sql = sqlField.value.trim();
    if (!sql) {
        setHomePasteNote('Paste some SQL first.', true);
        sqlField.focus();
        return;
    }

    // The name is required rather than defaulted: it becomes the basis for every
    // model name downstream, so "pasted_query" would quietly poison the output.
    const name = slugifyQueryName(nameField.value.trim());
    if (!name) {
        setHomePasteNote('Give it a name — it becomes the model name.', true);
        nameField.focus();
        return;
    }

    setHomePasteNote(HOME_PASTE_DEFAULT_NOTE, false);

    const file = new File([sql], `${name}.sql`, { type: 'text/plain' });
    handleFileUpload(file);
}

document.addEventListener('DOMContentLoaded', () => {
    const nameField = document.getElementById('home-paste-name');
    if (!nameField) return;

    nameField.addEventListener('keydown', event => {
        if (event.key === 'Enter') {
            event.preventDefault();
            submitPastedQuery();
        }
    });
});
