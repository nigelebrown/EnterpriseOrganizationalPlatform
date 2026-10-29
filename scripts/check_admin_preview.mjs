// Validate only the client-side synthetic preview; no API credentials or live data.
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import assert from 'node:assert/strict';
const js = readFileSync('jcf_platform/admin.js','utf8');
const start = js.indexOf('function sampleSnapshot(){');
const end = js.indexOf('async function loadPaged(', start);
assert(start >= 0 && end > start,'Synthetic preview function must exist');
const data = runInNewContext(js.slice(start,end)+'\nsampleSnapshot()');
assert.equal(data.preview,true);
assert.equal(data.locations.length,12);
assert.equal(data.sections.length,9);
assert.equal(data.mappings.length,16);
assert.equal(data.buildings.length,2);
assert.equal(data.occupancies.length,3);
assert.equal(data.reporting.length,10);
for(const name of ['CIB','PIU','General Office','Armoury','Operations Office','DIU']) {
  assert(data.sections.some(x=>x.name==='[TEST] '+name),'Missing Section/Unit '+name);
}
const sac = data.locations.find(x=>x.code==='DEMO-SAC');
assert(sac);
assert.equal(data.mappings.filter(x=>x.locationId===sac.locationId).length,3);
assert(data.occupancies.every(x=>data.locations.some(l=>l.locationId===x.locationId)));
assert(data.reporting.every(x=>x.sourceName && x.authorityName));
console.log('JCF-EMP sample directory preview schema and relationships verified.');
