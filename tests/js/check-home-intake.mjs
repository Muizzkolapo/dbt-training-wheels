// Home intake regression checks.
//
//     node tests/js/check-home-intake.mjs
//
// Node builtins only -- see check-step-state.mjs for why there is no test runner.
//
// The name a pasted query is given becomes a filename, a dbt model identifier and a
// YAML key, so slugifyQueryName is the one piece of home.js worth pinning down: a
// name that slips through wrong produces models nobody can reference.

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.join(HERE, '..', '..', 'dbt_training_wheels', 'static', 'js', 'home.js');

// home.js registers a DOMContentLoaded listener at load; stub just enough to get past it.
globalThis.document = { addEventListener() {}, getElementById() { return null; } };

const src = fs.readFileSync(SRC, 'utf8');
const { slugifyQueryName, HOME_INTAKES } = new Function(
    src + '; return { slugifyQueryName, HOME_INTAKES };'
)();

let pass = 0, fail = 0;
function check(label, got, expected) {
    const ok = JSON.stringify(got) === JSON.stringify(expected);
    console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label} → ${JSON.stringify(got)}`
        + (ok ? '' : ` (expected ${JSON.stringify(expected)})`));
    ok ? pass++ : fail++;
}

console.log('\nslugifyQueryName');
[
    ['spaces become underscores',      'Daily Orders',      'daily_orders'],
    ['an existing .sql is not doubled', 'daily_orders.sql',  'daily_orders'],
    ['punctuation collapses',           '  Weird--Name!! ',  'weird_name'],
    ['case is normalised',              'ALL_CAPS',          'all_caps'],
    ['leading digits survive',          '2024 report',       '2024_report'],
    ['runs collapse to one underscore', 'a  b',              'a_b'],
    ['unicode is stripped',             'café ☕ report',    'caf_report'],
    ['a path separator cannot escape',  '../../etc/passwd',  'etc_passwd'],
    // These three return empty, which is what makes submitPastedQuery refuse:
    // a name that slugs to nothing must not become a silently-defaulted filename.
    ['underscores alone are rejected',  '___',               ''],
    ['empty stays empty',               '',                  ''],
    ['punctuation alone is rejected',   '....sql',           ''],
].forEach(([label, input, expected]) => check(label, slugifyQueryName(input), expected));

console.log('\nintake tabs');
check('three routes, in order', HOME_INTAKES, ['folder', 'file', 'paste']);

console.log(`\n${pass} passed, ${fail} failed\n`);
process.exit(fail ? 1 : 0);
