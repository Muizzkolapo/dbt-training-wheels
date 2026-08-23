// Step-state regression checks.
//
// The Python suite cannot reach this logic -- it lives in static/js -- and the project
// has no JS test runner by design. This script needs neither: it uses only node
// builtins (fs, vm, path), so there is no package.json, no npm install, no toolchain.
//
//     node tests/js/check-step-state.mjs
//
// It loads validation.js into a sandbox with stubbed globals and asserts the three
// states the step rail reports. The rule under test is the one the rail used to break:
// a step is blocked, settled or defaulted because of what has been answered -- never
// because of where you are standing in the flow.

import fs from 'fs';
import vm from 'vm';
import path from 'path';
import { fileURLToPath } from 'url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.join(HERE, '..', '..', 'dbt_training_wheels', 'static', 'js', 'validation.js');
let pass=0, fail=0;
function scenario(name, globals, checks){
  const ctx = {
    analysisResults:null, modelConfigurations:{}, stepCompletionState:{},
    currentQuery:null, userDomainName:'', crossProjectRefsState:undefined,
    getAllModels:()=>[], getSavedDescription:()=>'',
    StepRegistry:{getEnabledSteps:()=>[]}, document:{querySelector:()=>null},
    console, ...globals
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(SRC,'utf8'), ctx);
  for(const [label, fn, expected] of checks){
    const got = fn(ctx);
    const ok = JSON.stringify(got)===JSON.stringify(expected);
    console.log(`  ${ok?'PASS':'FAIL'}  ${label}  → ${JSON.stringify(got)}${ok?'':` (expected ${JSON.stringify(expected)})`}`);
    ok?pass++:fail++;
  }
}
const steps = ids => ({getEnabledSteps:()=>ids.map(id=>({id}))});

console.log('\n1. Nothing analysed yet');
scenario('', {StepRegistry:steps(['analyze','layer-staging'])}, [
  ['analyze is blocked', c=>c.getStepState('analyze'), 'blocked'],
  ['layer-staging is blocked (analysis has not run)', c=>c.getStepState('layer-staging'), 'blocked'],
]);

console.log('\n2. Analysis run; staging has 2 undescribed models');
scenario('', {
  analysisResults:{finalTableSqls:[{}], hardcodedTables:[], naming:{stagingModelPrefix:'stg_'},
    layerClassification:{staging:[{name:'a'},{name:'b'}]}},
  StepRegistry:steps(['analyze','layer-staging']),
}, [
  ['analyze settled', c=>c.getStepState('analyze'), 'settled'],
  ['layer-staging blocked', c=>c.getStepState('layer-staging'), 'blocked'],
  ['criterion text counts them', c=>c.validateStepCompletion('layer-staging')[1].text, 'Descriptions written (0 of 2)'],
]);

console.log('\n3. Same, both described');
scenario('', {
  analysisResults:{finalTableSqls:[{}], hardcodedTables:[], naming:{stagingModelPrefix:'stg_'},
    layerClassification:{staging:[{name:'a'},{name:'b'}]}},
  getSavedDescription:n=>['stg_a','stg_b'].includes(n)?'described':'',
  StepRegistry:steps(['layer-staging']),
}, [
  ['layer-staging settled', c=>c.getStepState('layer-staging'), 'settled'],
]);

console.log('\n4. Zero staging models — the legitimate empty case');
scenario('', {
  analysisResults:{finalTableSqls:[{}], hardcodedTables:[], layerClassification:{staging:[]}},
  StepRegistry:steps(['layer-staging']),
}, [
  ['settled, not blocked', c=>c.getStepState('layer-staging'), 'settled'],
  ['and says why', c=>c.validateStepCompletion('layer-staging')[1].text, 'No staging models — nothing to describe'],
]);

console.log('\n5. Materialization/tags untouched → defaulted, never blocked');
scenario('', {
  analysisResults:{finalTableSqls:[{}], hardcodedTables:[]},
  getAllModels:()=>[{name:'m1',layer:'mart'},{name:'m2',layer:'mart'}],
  modelConfigurations:{}, StepRegistry:steps(['materialization','tags']),
}, [
  ['materialization defaulted', c=>c.getStepState('materialization'), 'defaulted'],
  ['tags defaulted', c=>c.getStepState('tags'), 'defaulted'],
]);

console.log('\n6. Cross-project disabled');
scenario('', {crossProjectRefsState:{enabled:false}, StepRegistry:steps(['cross-project-refs'])}, [
  ['settled', c=>c.getStepState('cross-project-refs'), 'settled'],
]);

console.log('\n7. Cross-project enabled, 2 refs, 1 decided');
scenario('', {
  crossProjectRefsState:{enabled:true, loaded:true,
    crossProjectRefs:[{original_reference:'p.d.t1'},{original_reference:'p.d.t2'}],
    decisions:{'p.d.t1':{}}},
  StepRegistry:steps(['cross-project-refs']),
}, [
  ['blocked', c=>c.getStepState('cross-project-refs'), 'blocked'],
  ['counts', c=>c.validateStepCompletion('cross-project-refs')[1].text, 'Decisions recorded (1 of 2)'],
]);

console.log('\n8. Rail counters, and position is irrelevant');
scenario('', {
  analysisResults:{finalTableSqls:[{}], hardcodedTables:[], layerClassification:{staging:[]}},
  getAllModels:()=>[{name:'m1',layer:'mart'}],
  getSavedDescription:()=>'',
  StepRegistry:steps(['analyze','layer-staging','layer-mart','materialization','tags']),
}, [
  ['blocked = mart only (undescribed)', c=>c.getBlockedStepIds(), ['layer-mart']],
  ['settled count 4 of 5', c=>c.getSettledStepCount(), 4],
  ['deploy gate blocked by mart', c=>c.checkAllPreviousStepsComplete(), false],
]);

console.log(`\n${pass} passed, ${fail} failed\n`);
process.exit(fail?1:0);
