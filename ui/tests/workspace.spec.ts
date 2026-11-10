import { readFileSync } from 'node:fs';
import { test, expect, type Page, type Locator } from '@playwright/test';

const project='workspace-test';
const artifact=(id:string,name:string,number:number,created_at:string)=>({id,project,namespace:'runs',entity:'artifact',name,number,state:'finished',created_at,finished_at:created_at,metadata:{config:{seed:number}},annotations:{},manifest:{outcome:'completed'},annotations_etag:'x'});
const runs=[artifact('old','workspace-demo/a',1,'2026-01-01'),artifact('a','workspace-demo/a',2,'2026-01-02'),artifact('b','workspace-demo/b',1,'2026-01-02')];
const image={$cairn:1,format:'image',version:1,data:{name:'test.png'},blobs:[{digest:'test-png',media_type:'image/png',size:1}],refs:[]};
const video={$cairn:1,format:'file',version:1,data:{name:'clip.mp4',media_type:'video/mp4'},blobs:[{digest:'test-mp4',media_type:'video/mp4',size:1}],refs:[]};
const figure={$cairn:1,format:'figure/plotly',version:1,data:{data:[{x:[0,1],y:[0,1],type:'scatter'}],layout:{}},blobs:[],refs:[]};
async function fixture(page:Page,projects=[project]){
  await page.route('**/ui-api/session',r=>r.fulfill({json:{authenticated:true,no_auth:true,repository_id:'workspace-tests'}}));
  await page.route('**/ui-api/projects',r=>r.fulfill({json:projects.map(name=>({name,runs:name===project?3:0,reports:0,artifacts:name===project?3:0}))}));
  await page.route('**/ui-api/artifacts?*',r=>{const offset=Number(new URL(r.request().url()).searchParams.get('offset'));return r.fulfill({json:{items:runs.slice(offset,offset+2),total:runs.length}});});
  await page.route('**/ui-api/artifacts/*/history',r=>r.fulfill({json:runs.filter(a=>a.name===runs.find(item=>r.request().url().includes(`/artifacts/${item.id}/`))?.name)}));
  await page.route('**/v1/resolutions',r=>r.fulfill({json:runs.filter(a=>r.request().postDataJSON().refs.includes(a.id))}));
  await page.route('**/v1/records/query',r=>{const body=r.request().postDataJSON(),id=body.ref;const values=body.cursor?[{step:2,loss:1,quality:10,elapsed:20,camera:'front',camera_angle:0,image,video,figure}]:[{step:0,loss:3,quality:30,elapsed:0,camera:'front',camera_angle:0,image,video,figure},...(id==='b'?[]:[{step:1,loss:2,quality:20,elapsed:10,camera:'front',camera_angle:0,image,video,figure}])];return r.fulfill({json:{artifact_id:id,cutoff:3,next_cursor:body.cursor?null:'page2',items:values.map((data,i)=>({artifact_id:id,ordinal:body.cursor?3:i+1,sequence:i,writer_id:'w',kind:'record',data,created_at:'2026-01-01'}))}});});
  await page.route('**/v1/blobs/test-png',r=>r.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64')}));
  await page.route('**/v1/blobs/test-mp4',r=>r.fulfill({contentType:'application/octet-stream',body:readFileSync(new URL('../../examples/report-showcase/assets/wave-evolution.mp4',import.meta.url))}));
  await page.goto(`/p/${project}/workspace?example=cards`);
  await expect(page.locator('.workspace-data-status')).toContainText('2 runs · 5 records');
}
const card=(page:Page,title:string)=>page.getByRole('article',{name:title,exact:true});

async function chooseField(dialog:Locator,label:string,value:string){await dialog.getByRole('button',{name:label,exact:true}).click();await dialog.page().getByRole('listbox',{name:label,exact:true}).getByText(value,{exact:true}).click();}

test('complete streams, independent scalar axes and ordering, explicit media matching',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await fixture(page);
  const scalar=card(page,'Loss over training');await expect(scalar.locator('.uplot')).toBeVisible();
  await expect.poll(()=>scalar.locator('.workspace-scalar-plot').evaluate((node:any)=>node.uplot?.data.slice(1).map((t:any)=>t[0]))).toEqual([[0,1,2],[0,2]]);
  await page.getByRole('button',{name:'Settings for Loss over training'}).click();const dialog=page.getByRole('dialog');
  await dialog.getByRole('tab',{name:'Mapping'}).click();await chooseField(dialog,'X field','elapsed');await chooseField(dialog,'Point order','loss');await dialog.getByRole('button',{name:'Done'}).click();
  await expect.poll(()=>scalar.locator('.workspace-scalar-plot').evaluate((node:any)=>node.uplot?.data[1][0])).toEqual([20,10,0]);
  const comparison=card(page,'Compare reconstructions');await comparison.getByRole('slider').fill('1');
  await expect(comparison.getByText('No observation at 1',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Settings for Compare reconstructions'}).click();await dialog.getByRole('tab',{name:'Mapping'}).click();await chooseField(dialog,'Match observations','Previous available');await dialog.getByRole('button',{name:'Done'}).click();await comparison.getByRole('slider').fill('1');await expect(comparison.getByText('No observation at 1')).toHaveCount(0);await expect(comparison.locator('.workspace-tile-caption').filter({hasText:'step: 0'})).toHaveCount(1);
  const gallery=card(page,'Browse by quality');await expect(gallery.locator('.workspace-tile-caption').first()).toContainText('quality: 30');
  await expect(gallery.locator('.workspace-tile-caption').last()).toContainText('quality: 10');
  const figures=card(page,'Compare fitted curves');await figures.scrollIntoViewIfNeeded();await figures.locator('.workspace-media-tile').nth(1).scrollIntoViewIfNeeded();await expect.poll(()=>figures.locator('.js-plotly-plot').count()).toBe(2);
  expect(await figures.locator('.workspace-media-tile').evaluateAll(nodes=>nodes.every(n=>n.scrollHeight<=n.clientHeight+1))).toBe(true);
  await figures.getByRole('button',{name:'Expand figure',exact:false}).first().click();await expect(page.getByRole('dialog').locator('.js-plotly-plot')).toBeVisible();await page.getByRole('dialog').getByRole('button',{name:'Close dialog'}).click();
  expect(errors).toEqual([]);
});

test('create, cancel, resize, sections, save and reload preserve the view without data copies',async({page})=>{
  await fixture(page);await page.getByRole('button',{name:'Add card',exact:true}).click();let dialog=page.getByRole('dialog');
  await dialog.getByRole('button',{name:'Image',exact:true}).click();await dialog.getByLabel('Card title').fill('My images');await dialog.getByRole('button',{name:'Add card',exact:true}).click();await expect(card(page,'My images')).toBeVisible();
  await page.getByRole('button',{name:'Settings for My images'}).click();await dialog.getByLabel('Card title').fill('Live title');await expect(page.locator('main article[aria-label="Live title"]')).toBeVisible();await dialog.getByRole('button',{name:'Done',exact:true}).click();await page.getByRole('button',{name:'Undo layout change',exact:true}).click();await expect(card(page,'My images')).toBeVisible();
  const handle=page.getByRole('button',{name:'Resize My images'});await handle.focus();await handle.press('ArrowDown');await handle.press('ArrowLeft');await expect(card(page,'My images')).toHaveCSS('height','380px');
  await page.getByRole('button',{name:'Add section',exact:true}).click();await dialog.getByLabel('Name',{exact:true}).fill('Details');await dialog.getByRole('button',{name:'Apply',exact:true}).click();
  const source=card(page,'My images').locator('.workspace-drag-handle');await source.scrollIntoViewIfNeeded();const start=await source.boundingBox();await page.mouse.move(start!.x+8,start!.y+8);await page.mouse.down();await page.mouse.move(start!.x+18,start!.y+8,{steps:4});
  const target=page.locator('.workspace-section').filter({has:page.getByRole('heading',{name:'Details',exact:true})}).locator('.workspace-empty-section');await target.scrollIntoViewIfNeeded();const end=await target.boundingBox();await page.mouse.move(end!.x+40,end!.y+20,{steps:5});await page.mouse.move(end!.x+45,end!.y+25);await page.mouse.up();
  await expect(target).toHaveCount(0);
  await page.getByRole('button',{name:'Save as…',exact:true}).click();await dialog.getByLabel('Name',{exact:true}).fill('Review');await dialog.getByRole('button',{name:'Save layout',exact:true}).click();
  await page.goto(`/p/${project}/workspace`);await expect(page.getByRole('button',{name:'Saved layout'})).toHaveText('Review');
  await expect(card(page,'My images')).toHaveCSS('height','380px');await expect(page.locator('.workspace-section').filter({has:page.getByRole('heading',{name:'Details',exact:true})})).toContainText('My images');
  const saved=await page.evaluate(()=>JSON.parse(localStorage.getItem('cairn:workspace:v1:workspace-tests:workspace-test')!));const review=saved.layouts.find((l:any)=>l.name==='Review');expect(review).toBeTruthy();expect(review.cards.find((c:any)=>c.title==='My images').width).toBe(4);expect(JSON.stringify(saved)).not.toContain('test-png');
  await chooseField(page.locator('main'),'Versions','All versions');await expect(page.locator('.workspace-data-status')).toContainText('3 runs · 8 records');
});

test('mobile cards and modal controls stay inside the viewport',async({page})=>{
  await page.setViewportSize({width:390,height:844});await fixture(page);
  const overflow=()=>page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);
  expect(await overflow()).toBe(false);const comparison=card(page,'Compare reconstructions');await comparison.scrollIntoViewIfNeeded();await comparison.getByRole('button',{name:'Enlarge image'}).first().click();await expect(page.getByRole('dialog')).toBeVisible();await page.getByRole('button',{name:'Close media viewer'}).click();
  await page.getByRole('button',{name:'Settings for Compare reconstructions'}).click();await page.getByRole('dialog').getByRole('tab',{name:'Mapping'}).click();expect(await overflow()).toBe(false);await page.getByRole('dialog').getByRole('button',{name:'Done'}).click();await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('workspace SVG blobs decode in thumbnails and lightboxes despite an untyped HTTP response',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await fixture(page);
  const svg={$cairn:1,format:'figure/svg',version:1,data:{},blobs:[{digest:'test-svg',media_type:'image/svg+xml',size:150,name:'figure.svg'}],refs:[]};
  await page.route('**/v1/blobs/test-svg',r=>r.fulfill({contentType:'application/octet-stream',body:'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80"><rect width="120" height="80" fill="teal"/></svg>'}));
  await page.route('**/v1/records/query',r=>{const body=r.request().postDataJSON();return r.fulfill({json:{artifact_id:body.ref,cutoff:1,next_cursor:null,items:[{artifact_id:body.ref,ordinal:1,sequence:1,writer_id:'w',kind:'record',data:{step:0,image:svg},created_at:'2026-01-01'}]}});});
  await page.getByRole('button',{name:'Refresh workspace',exact:true}).click();
  const comparison=card(page,'Compare reconstructions');
  await expect.poll(()=>comparison.locator('img').evaluateAll(nodes=>nodes.map(n=>(n as HTMLImageElement).naturalWidth))).toEqual([120,120]);
  await comparison.getByRole('button',{name:'Enlarge image',exact:true}).first().click();
  const dialog=page.getByRole('dialog');await expect(dialog.getByText('120 × 80',{exact:true})).toBeVisible();
  await expect(dialog.getByText('This image format could not be decoded by the browser.')).toHaveCount(0);
  expect(await dialog.locator('img').evaluate((n:HTMLImageElement)=>n.naturalWidth)).toBe(120);
  await dialog.getByRole('button',{name:'Close media viewer'}).click();
  await comparison.getByRole('button',{name:'Enlarge image',exact:true}).first().click();await expect(page.getByRole('dialog').getByText('120 × 80',{exact:true})).toBeVisible();
  expect(errors).toEqual([]);
});

test('record filters support nested configuration and splitting never aggregates',async({page})=>{
  await fixture(page);await page.getByRole('button',{name:'Settings for Loss over training'}).click();const dialog=page.getByRole('dialog');
  await dialog.getByRole('tab',{name:'Mapping'}).click();await dialog.getByRole('button',{name:'Remove grouping @run',exact:true}).click();await dialog.getByRole('button',{name:'Done'}).click();
  const plot=card(page,'Loss over training').locator('.workspace-scalar-plot');await expect.poll(()=>plot.evaluate((node:any)=>node.uplot?.data.slice(1).map((t:any)=>t[0]))).toEqual([[0,0,1,2,2]]);
  await page.getByRole('button',{name:'Settings for Loss over training'}).click();await dialog.getByRole('button',{name:'Add filter',exact:true}).click();await chooseField(dialog,'Filter field 1','@config/seed');await dialog.getByLabel('Value',{exact:true}).fill('2');await dialog.getByRole('button',{name:'Done'}).click();
  await expect.poll(()=>plot.evaluate((node:any)=>node.uplot?.data.slice(1).map((t:any)=>t[0]))).toEqual([[0,1,2]]);
});

test('a failed later record page never leaves a partial plot',async({page})=>{
  await fixture(page);
  await page.route('**/v1/records/query',async r=>{if(r.request().postDataJSON().cursor)return r.fulfill({status:503,json:{error:{message:'Record stream temporarily unavailable'}}});await r.fallback();});
  await page.getByRole('button',{name:'Refresh workspace',exact:true}).click();await expect(page.getByRole('alert')).toContainText('Record stream temporarily unavailable');await expect(card(page,'Loss over training').locator('.workspace-scalar-plot')).toHaveCount(0);
  await expect(card(page,'Loss over training')).toContainText('Data could not be loaded');
});

for(const scenario of [{name:'50,000 records',perRun:26001,padding:0},{name:'32 MiB',perRun:2000,padding:9000}])test(`complete selections above ${scenario.name} remain inspectable`,async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await fixture(page);
  await page.getByRole('button',{name:'Save',exact:true}).click();
  // Keep one scalar card so the assertion measures complete data, not duplicate renderers.
  await page.evaluate(()=>{const key='cairn:workspace:v1:workspace-tests:workspace-test',saved=JSON.parse(localStorage.getItem(key)!);saved.layouts[0].cards=saved.layouts[0].cards.slice(0,1);localStorage.setItem(key,JSON.stringify(saved));});
  let pages=0,bytes=0;
  await page.route('**/v1/records/query',async route=>{
    const body=route.request().postDataJSON(),start=Number(body.cursor||0),end=Math.min(start+1000,scenario.perRun);pages++;
    const items=Array.from({length:end-start},(_,i)=>{const step=start+i;return {artifact_id:body.ref,ordinal:step+1,sequence:step,writer_id:'w',kind:'record',created_at:'2026-01-01',data:{step,loss:scenario.perRun-step,...(scenario.padding?{detail:'x'.repeat(scenario.padding)}:{})}};});
    const response=JSON.stringify({artifact_id:body.ref,cutoff:scenario.perRun,next_cursor:end<scenario.perRun?String(end):null,items});bytes+=Buffer.byteLength(response);
    await route.fulfill({contentType:'application/json',body:response});
  });
  await page.reload();
  await expect(page.locator('.workspace-data-status')).toContainText(`${(2*scenario.perRun).toLocaleString()} records`,{timeout:20000});
  const plot=card(page,'Loss over training').locator('.workspace-scalar-plot');
  await expect.poll(()=>plot.evaluate((n:any)=>n.uplot?.data.slice(1).map((series:any)=>({count:series[0].length,first:series[0][0],last:series[0].at(-1),lastY:series[1].at(-1)})))).toEqual(Array(2).fill({count:scenario.perRun,first:0,last:scenario.perRun-1,lastY:1}));
  expect(pages).toBe(2*Math.ceil(scenario.perRun/1000));if(scenario.padding)expect(bytes).toBeGreaterThan(32*1024*1024);
  await expect(page.getByRole('alert')).toHaveCount(0);expect(errors).toEqual([]);
});

test('pointer resize and reordering remain undoable; saved demo reloads',async({page})=>{
  await page.setViewportSize({width:1440,height:1000});await fixture(page);
  const loss=card(page,'Loss over training'),start=await loss.boundingBox(),handle=await page.getByRole('button',{name:'Resize Loss over training',exact:true}).boundingBox();
  await page.mouse.move(handle!.x+10,handle!.y+10);await page.mouse.down();await page.mouse.move(handle!.x-120,handle!.y+70,{steps:6});await page.mouse.up();expect((await loss.boundingBox())!.width).toBeLessThan(start!.width);await expect(loss).toHaveCSS('height','420px');await expect(card(page,'Quality by camera angle')).toHaveCSS('height','420px');expect((await card(page,'Quality by camera angle').boundingBox())!.width).toBeCloseTo((await loss.boundingBox())!.width,0);
  await page.getByRole('button',{name:'Undo layout change',exact:true}).click();await expect(loss).toHaveCSS('height','360px');
  await card(page,'Quality by camera angle').locator('.workspace-drag-handle').dragTo(loss);await expect(page.locator('.workspace-section').first().locator('article').first()).toHaveAttribute('aria-label','Quality by camera angle');
  await page.getByRole('button',{name:'Save',exact:true}).click();await expect(page).toHaveURL(new RegExp(`/p/${project}/workspace$`));await page.reload();await expect(page.locator('.workspace-save-status')).toHaveText('Saved in this browser');
});

test('run sidebar, section sizes and cross-section moves stay consistent',async({page})=>{
  await page.setViewportSize({width:1440,height:1000});await fixture(page);
  await expect(page.getByRole('button',{name:'Arrange',exact:true})).toHaveCount(0);
  const sidebar=await page.getByRole('complementary',{name:'Run selection'}).boundingBox();
  const loss=card(page,'Loss over training'),quality=card(page,'Quality by camera angle');
  expect(sidebar!.x+sidebar!.width).toBeLessThan((await loss.boundingBox())!.x);
  await page.getByRole('button',{name:'Settings for Loss over training'}).click();const dialog=page.getByRole('dialog');
  await dialog.getByRole('tab',{name:'Appearance'}).click();await expect(dialog.getByRole('button',{name:'Section card width',exact:true})).toHaveCount(0);await expect(dialog.getByLabel('Section card height')).toHaveCount(0);await dialog.getByRole('button',{name:'Done'}).click();
  const handle=loss.getByRole('button',{name:'Resize Loss over training'});await handle.press('ArrowLeft');await handle.press('ArrowLeft');for(let i=0;i<9;i++)await handle.press('ArrowDown');
  await expect(loss).toHaveCSS('height','540px');await expect(quality).toHaveCSS('height','540px');
  expect(await loss.evaluate(n=>n.style.getPropertyValue('--card-span'))).toBe('3');expect(await quality.evaluate(n=>n.style.getPropertyValue('--card-span'))).toBe('3');
  const target=card(page,'Compare reconstructions');await quality.locator('.workspace-drag-handle').dragTo(target,{targetPosition:{x:40,y:20}});
  await expect(quality).toHaveCSS('height','520px');expect(await quality.evaluate(n=>n.style.getPropertyValue('--card-span'))).toBe('6');
  await page.getByRole('button',{name:'Undo layout change'}).click();await expect(quality).toHaveCSS('height','540px');
  await page.setViewportSize({width:390,height:844});await expect(page.getByRole('complementary',{name:'Run selection'})).toBeHidden();await page.getByRole('button',{name:'2 runs',exact:true}).click();await expect(page.getByRole('complementary',{name:'Run selection'})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('full-width sections resize by pointer and keyboard and survive saving',async({page})=>{
  await page.setViewportSize({width:1440,height:1000});await fixture(page);
  const loss=card(page,'Loss over training'),section=page.locator('.workspace-section').first(),grid=section.locator('.workspace-grid');
  const handle=loss.getByRole('button',{name:'Resize Loss over training'});
  const grip=(await handle.boundingBox())!,bounds=(await grid.boundingBox())!;
  await page.mouse.move(grip.x+8,grip.y+8);await page.mouse.down();await page.mouse.move(bounds.x+bounds.width-4,grip.y+8,{steps:8});await page.mouse.up();
  for(const item of await section.locator('article').all())expect((await item.boundingBox())!.width).toBeCloseTo(bounds.width,0);
  await handle.press('ArrowDown');await expect(loss).toHaveCSS('height','380px');
  expect((await loss.boundingBox())!.width).toBeCloseTo(bounds.width,0);
  for(const span of [6,4,3,3]){await handle.press('ArrowLeft');expect(await loss.evaluate(n=>n.style.getPropertyValue('--card-span'))).toBe(String(span));}
  for(const span of [4,6,12,12]){await handle.press('ArrowRight');expect(await loss.evaluate(n=>n.style.getPropertyValue('--card-span'))).toBe(String(span));}
  await page.getByRole('button',{name:'Save',exact:true}).click();await page.reload();
  await expect(loss).toHaveCSS('height','380px');
  for(const item of await section.locator('article').all())expect((await item.boundingBox())!.width).toBeCloseTo((await grid.boundingBox())!.width,0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('legacy mixed card sizes load without rewriting the saved layout',async({page})=>{
  await fixture(page);await page.getByRole('button',{name:'Save',exact:true}).click();
  const original=await page.evaluate(()=>{const key='cairn:workspace:v1:workspace-tests:workspace-test',saved=JSON.parse(localStorage.getItem(key)!);saved.layouts[0].cards[0].width=8;saved.layouts[0].cards[1].width=3;saved.layouts[0].cards[1].height=800;const raw=JSON.stringify(saved);localStorage.setItem(key,raw);return raw;});
  await page.reload();await expect(card(page,'Loss over training')).toHaveCSS('height','360px');await expect(card(page,'Quality by camera angle')).toHaveCSS('height','360px');
  expect(await card(page,'Quality by camera angle').evaluate(n=>n.style.getPropertyValue('--card-span'))).toBe('6');
  expect(await page.evaluate(()=>localStorage.getItem('cairn:workspace:v1:workspace-tests:workspace-test'))).toBe(original);
});

test('saved workspaces stay separate across projects and repositories',async({page})=>{
  const other='another-project';await fixture(page,[project,other]);
  await page.route('**/ui-api/artifacts?*',async r=>{if(new URL(r.request().url()).searchParams.get('project')===other)return r.fulfill({json:{items:[],total:0}});await r.fallback();});
  const rename=async(from:string,to:string)=>{await page.getByRole('button',{name:`Settings for ${from}`,exact:true}).click();const dialog=page.getByRole('dialog');await dialog.getByLabel('Card title').fill(to);await dialog.getByRole('button',{name:'Done',exact:true}).click();};
  await rename('Loss over training','Project A loss');
  const keyA=`cairn:workspace:v1:workspace-tests:${project}`,keyB=`cairn:workspace:v1:workspace-tests:${other}`;
  const savedA=await page.evaluate(key=>localStorage.getItem(key),keyA);
  await page.getByRole('button',{name:'Choose project',exact:true}).click();
  const switchProject=async(name:string)=>{await page.getByRole('button',{name:'Project',exact:true}).click();await page.getByRole('listbox',{name:'Project',exact:true}).getByText(name,{exact:true}).click();await expect(page).toHaveURL(new RegExp(`/p/${name}/workspace$`));};
  await switchProject(other);await expect(card(page,'Project A loss')).toHaveCount(0);await expect(page.locator('.workspace-data-status')).toContainText('0 runs');
  await rename('Loss over training','Project B loss');
  expect(await page.evaluate(key=>localStorage.getItem(key),keyA)).toBe(savedA);
  const savedB=await page.evaluate(key=>localStorage.getItem(key),keyB);
  await switchProject(project);await expect(card(page,'Project A loss')).toBeVisible();await expect(card(page,'Project B loss')).toHaveCount(0);
  await switchProject(other);await expect(card(page,'Project B loss')).toBeVisible();
  // A different repository with the same project name has its own layout.
  await page.route('**/ui-api/session',r=>r.fulfill({json:{authenticated:true,no_auth:true,repository_id:'another-repository'}}));
  await page.reload();await expect(card(page,'Project B loss')).toHaveCount(0);await rename('Loss over training','Other repository loss');
  expect(await page.evaluate(key=>localStorage.getItem(key),keyB)).toBe(savedB);
  await page.route('**/ui-api/session',r=>r.fulfill({json:{authenticated:true,no_auth:true,repository_id:'workspace-tests'}}));
  await page.reload();await expect(card(page,'Project B loss')).toBeVisible();await expect(card(page,'Other repository loss')).toHaveCount(0);
});

test('one eye control drives run visibility without reshuffling colors',async({page})=>{
  await fixture(page);const loss=card(page,'Loss over training'),plot=loss.locator('.workspace-scalar-plot');
  await expect.poll(()=>plot.evaluate((n:any)=>n.uplot?.series.length-1)).toBe(2);
  const colors=await plot.evaluate((n:any)=>Object.fromEntries((n.uplot?.series.slice(1)||[]).map((t:any,i:number)=>[t.label,typeof t.stroke==='function'?t.stroke(n.uplot,i+1):t.stroke])));
  expect(new Set(Object.values(colors)).size).toBe(2);
  const rows=page.locator('.workspace-run-row');expect(await rows.evaluateAll(nodes=>nodes.every(n=>n.getBoundingClientRect().height<=32))).toBe(true);
  await page.getByRole('button',{name:'Hide workspace-demo/b · v1',exact:true}).click();
  await expect.poll(()=>plot.evaluate((n:any)=>n.uplot?.series.length-1)).toBe(1);await expect(page.locator('.workspace-run-list').getByRole('checkbox')).toHaveCount(0);await expect(page.locator('.workspace-data-status')).toContainText('1 run · 3 records · 1 hidden');
  expect(await plot.evaluate((n:any)=>n.uplot.series[1].stroke(n.uplot,1))).toBe(colors['workspace-demo/a · v2']);
  await page.getByRole('button',{name:'Show workspace-demo/b · v1',exact:true}).click();await expect.poll(()=>plot.evaluate((n:any)=>Object.fromEntries((n.uplot?.series.slice(1)||[]).map((t:any,i:number)=>[t.label,typeof t.stroke==='function'?t.stroke(n.uplot,i+1):t.stroke])))).toEqual(colors);
  await page.getByRole('button',{name:'Hide workspace-demo/a · v2',exact:true}).click();await expect.poll(()=>plot.evaluate((n:any)=>n.uplot?.series.length-1)).toBe(1);expect(await plot.evaluate((n:any)=>n.uplot.series[1].stroke(n.uplot,1))).toBe(colors['workspace-demo/b · v1']);
  await page.getByRole('button',{name:'Show all runs',exact:true}).click();await expect.poll(()=>plot.evaluate((n:any)=>Object.fromEntries((n.uplot?.series.slice(1)||[]).map((t:any,i:number)=>[t.label,typeof t.stroke==='function'?t.stroke(n.uplot,i+1):t.stroke])))).toEqual(colors);
  await page.reload();await expect.poll(()=>plot.evaluate((n:any)=>Object.fromEntries((n.uplot?.series.slice(1)||[]).map((t:any,i:number)=>[t.label,typeof t.stroke==='function'?t.stroke(n.uplot,i+1):t.stroke])))).toEqual(colors);
});

test('icon navigation expands, collapses, and leaves project switching accessible',async({page})=>{
  await fixture(page);const rail=page.getByRole('complementary',{name:'Main navigation'});
  expect((await rail.boundingBox())!.width).toBe(72);await expect(rail.getByRole('link',{name:'Workspace',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Choose project',exact:true}).click();expect((await rail.boundingBox())!.width).toBe(240);await page.getByRole('button',{name:'Project',exact:true}).click();await expect(page.getByRole('listbox',{name:'Project'})).toBeVisible();await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'Collapse navigation',exact:true}).click();expect((await rail.boundingBox())!.width).toBe(72);await page.getByRole('button',{name:'Expand navigation',exact:true}).click();await expect(rail.getByText('Workspace',{exact:true})).toBeVisible();
});

test('field dropdowns list available values and grouping creates distinct lines',async({page})=>{
  await fixture(page);await page.getByRole('button',{name:'Settings for Loss over training'}).click();const dialog=page.getByRole('dialog');
  await expect(dialog.getByLabel('Place card in section')).toHaveCount(0);await expect(dialog.locator('select')).toHaveCount(0);await expect(dialog.getByLabel('Combine filter conditions')).toHaveCount(0);
  await dialog.getByRole('tab',{name:'Mapping'}).click();await chooseField(dialog,'Y field','quality');await chooseField(dialog,'Add grouping field','step');await dialog.getByRole('button',{name:'Done'}).click();
  await expect.poll(()=>card(page,'Loss over training').locator('.workspace-scalar-plot').evaluate((n:any)=>n.uplot?.series.length-1)).toBe(5);
  await page.getByRole('button',{name:'Settings for Compare reconstructions'}).click();await dialog.getByRole('button',{name:'Image field',exact:true}).click();await expect(dialog.getByRole('listbox',{name:'Image field'}).getByText('image',{exact:true})).toHaveCount(2);await page.keyboard.press('Escape');await dialog.getByRole('button',{name:'Done',exact:true}).click();
});

test('run links open Overview; Workspace is an embedded run tab with the other inspection tabs available',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await fixture(page);await page.getByRole('button',{name:'Hide workspace-demo/b · v1',exact:true}).click();
  await page.getByRole('button',{name:'Settings for Loss over training'}).click();await page.getByRole('dialog').getByLabel('Card title').fill('My project curve');await page.getByRole('dialog').getByRole('button',{name:'Done'}).click();
  await expect(page.getByRole('button',{name:'Save',exact:true})).toBeDisabled();
  await page.getByRole('link',{name:'Open run workspace-demo/b · v1',exact:true}).click();
  await expect(page).toHaveURL(/\/a\/b$/);await expect(page.getByRole('tab',{name:'Overview',exact:true})).toHaveAttribute('aria-selected','true');await expect(page.getByRole('heading',{name:'Metric trends'})).toBeVisible();await expect(page.locator('.workspace-page')).toHaveCount(0);
  await page.getByRole('tab',{name:'Workspace',exact:true}).click();
  await expect(page).toHaveURL(/\/a\/b\?tab=workspace$/);await expect(page.getByRole('complementary',{name:'Run selection'})).toHaveCount(0);await expect(page.locator('.workspace-data-status')).toContainText('1 run · 2 records');await expect(page.getByRole('heading',{name:'workspace-demo/b',exact:true})).toBeVisible();
  await expect(page.getByRole('main')).toHaveCount(1);await expect(page.getByRole('button',{name:'Add card',exact:true})).toBeVisible();
  await expect.poll(()=>card(page,'My project curve').locator('.workspace-scalar-plot').evaluate((n:any)=>n.uplot?.series.slice(1).map((t:any)=>t.label))).toEqual(['workspace-demo/b · v1']);
  for(const name of ['Overview','Records','Files','Lineage','History','Metadata'])await expect(page.getByRole('tab',{name:new RegExp(`^${name}`)})).toBeVisible();
  await page.getByRole('tab',{name:'Records',exact:true}).click();await expect(page.getByRole('heading',{name:'Record explorer'})).toBeVisible();
  await page.getByRole('tab',{name:'Files',exact:true}).click();await expect(page.getByRole('heading',{name:'Files',exact:true})).toBeVisible();
  await page.getByRole('tab',{name:'Workspace',exact:true}).click();await expect(page.locator('.workspace-data-status')).toContainText('1 run · 2 records');
  await page.getByRole('link',{name:'Project workspace',exact:true}).click();await expect(page.locator('.workspace-data-status')).toContainText('1 run · 3 records · 1 hidden');await expect(page.getByRole('button',{name:'Show workspace-demo/b · v1',exact:true})).toBeVisible();await expect(card(page,'My project curve')).toBeVisible();
  await page.goto(`/p/${project}?view=runs`);await page.getByRole('link',{name:'workspace-demo/a',exact:true}).first().click();await expect(page).toHaveURL(/\/a\/old$/);await expect(page.getByRole('tab',{name:'Overview',exact:true})).toHaveAttribute('aria-selected','true');
  expect(errors).toEqual([]);
});

test('saved visibility survives reload; direct run views ignore filters and latest-only selection',async({page})=>{
  await fixture(page);await page.getByRole('button',{name:'Hide all runs',exact:true}).click();await expect(page.locator('.workspace-data-status')).toContainText('0 runs · 0 records');await page.getByRole('button',{name:'Save',exact:true}).click();await page.reload();await expect(page.locator('.workspace-data-status')).toContainText('0 runs · 0 records');
  const queries:string[]=[];let listRequests=0;page.on('request',r=>{if(r.url().includes('/v1/records/query'))queries.push(r.postDataJSON().ref);if(r.url().includes('/ui-api/artifacts?'))listRequests++;});
  await page.goto(`/p/${project}/workspace/old`);await expect(page).toHaveURL(new RegExp(`/a/old\\?tab=workspace$`));await expect(page.getByRole('tab',{name:'Workspace',exact:true})).toHaveAttribute('aria-selected','true');await expect(page.locator('.workspace-data-status')).toContainText('1 run · 3 records');await expect.poll(()=>card(page,'Loss over training').locator('.workspace-scalar-plot').evaluate((n:any)=>n.uplot?.series[1].label)).toBe('workspace-demo/a · v1');expect(new Set(queries)).toEqual(new Set(['old']));expect(listRequests).toBe(0);
  await page.getByRole('link',{name:'Project workspace',exact:true}).click();await expect(page.locator('.workspace-data-status')).toContainText('0 runs · 0 records');await page.getByRole('button',{name:'Show all runs',exact:true}).click();await expect(page.locator('.workspace-data-status')).toContainText('2 runs · 5 records');
});

test('invalid and cross-project run links never fall back to project records',async({page})=>{
  await fixture(page);await page.route('**/v1/resolutions',r=>{const id=r.request().postDataJSON().refs[0];return r.fulfill({json:id==='foreign'?[{...runs[0],id,project:'another-project'}]:[]});});
  const queries:string[]=[];page.on('request',r=>{if(r.url().includes('/v1/records/query'))queries.push(r.postDataJSON().ref);});
  for(const id of ['missing','foreign']){await page.goto(`/p/${project}/workspace/${id}`);await expect(page.getByRole('alert')).toContainText('Artifact not found in this project');await expect(page.locator('.workspace-scalar-plot')).toHaveCount(0);await expect(page.getByRole('complementary',{name:'Run selection'})).toHaveCount(0);}
  expect(queries).toEqual([]);
});

test('settings apply while open, aggregate bands persist, and one undo restores the edit session',async({page})=>{
  await fixture(page);await page.getByRole('button',{name:'Settings for Loss over training'}).click();const dialog=page.getByRole('dialog');
  await expect(dialog.getByRole('button',{name:'Apply changes'})).toHaveCount(0);await expect(dialog.locator('select')).toHaveCount(0);
  await dialog.getByLabel('Card title').fill('Grouped loss');
  await expect(page.locator('main article[aria-label="Grouped loss"]')).toBeVisible();
  await dialog.getByRole('tab',{name:'Mapping'}).click();await dialog.getByRole('button',{name:'Remove grouping @run'}).click();await chooseField(dialog,'Group center','Mean');await chooseField(dialog,'Group band','± standard deviation');await dialog.getByLabel('Show member runs').check();
  const plot=page.locator('main article[aria-label="Grouped loss"] .workspace-scalar-plot');
  await expect.poll(()=>plot.evaluate((n:any)=>n.uplot?.series.length)).toBe(4);
  await dialog.getByRole('button',{name:'Done',exact:true}).click();await expect(page.getByRole('button',{name:'Save',exact:true})).toBeDisabled();await page.reload();
  await page.getByRole('button',{name:'Settings for Grouped loss'}).click();await dialog.getByRole('tab',{name:'Mapping'}).click();await expect(dialog.getByRole('button',{name:'Group center',exact:true})).toHaveText('Mean');await expect(dialog.getByRole('button',{name:'Group band',exact:true})).toHaveText('± standard deviation');
  await chooseField(dialog,'Group center','Median');await chooseField(dialog,'Group band','Min – max');await dialog.getByRole('button',{name:'Close dialog'}).click();await page.getByRole('button',{name:'Undo layout change'}).click();
  await page.getByRole('button',{name:'Settings for Grouped loss'}).click();await dialog.getByRole('tab',{name:'Mapping'}).click();await expect(dialog.getByRole('button',{name:'Group center',exact:true})).toHaveText('Mean');await expect(dialog.getByRole('button',{name:'Group band',exact:true})).toHaveText('± standard deviation');
  await dialog.getByRole('tab',{name:'Data'}).click();await dialog.getByLabel('Card title').fill('');await dialog.getByRole('button',{name:'Done',exact:true}).click();await expect(card(page,'Grouped loss')).toBeVisible();
});

test('uPlot supports point inspection, drag zoom, reset, legend visibility and resized cards',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await fixture(page);
  const loss=card(page,'Loss over training'),plot=loss.locator('.workspace-scalar-plot');
  const point=await plot.evaluate((n:any)=>{const u=n.uplot,r=u.over.getBoundingClientRect();return {x:r.left+u.valToPos(1,'x'),y:r.top+u.valToPos(2,'y')};});
  await page.mouse.move(point.x,point.y);await expect(loss.locator('.workspace-scalar-tooltip')).toContainText('step: 1');await page.mouse.click(point.x,point.y);await expect(page.getByRole('dialog',{name:'Record 2',exact:true})).toBeVisible();await page.getByRole('button',{name:'Close dialog'}).click();
  const bounds=await loss.locator('.u-over').boundingBox();const before=await plot.evaluate((n:any)=>[n.uplot.scales.x.min,n.uplot.scales.x.max]);
  await page.mouse.move(bounds!.x+bounds!.width*.2,bounds!.y+bounds!.height*.2);await page.mouse.down();await page.mouse.move(bounds!.x+bounds!.width*.8,bounds!.y+bounds!.height*.8,{steps:8});await page.mouse.up();
  await expect.poll(()=>plot.evaluate((n:any)=>n.uplot.scales.x.max-n.uplot.scales.x.min)).toBeLessThan(before[1]-before[0]);await loss.getByRole('button',{name:'Reset plot zoom'}).click();await expect.poll(()=>plot.evaluate((n:any)=>[n.uplot.scales.x.min,n.uplot.scales.x.max])).toEqual(before);
  for(const name of ['workspace-demo/a · v2','workspace-demo/b · v1'])await loss.getByRole('button',{name,exact:true}).click();await expect(loss.locator('canvas')).toBeVisible();await loss.getByRole('button',{name:'workspace-demo/a · v2',exact:true}).click();
  await loss.getByRole('button',{name:'Resize Loss over training'}).press('ArrowDown');await expect.poll(()=>plot.evaluate((n:any)=>n.uplot.height===n.clientHeight)).toBe(true);
  expect(errors).toEqual([]);
});

test('video fields become video cards that play inline and enlarge',async({page})=>{
  await fixture(page);await page.getByRole('button',{name:'Add card',exact:true}).click();const dialog=page.getByRole('dialog');
  await dialog.getByRole('button',{name:'Video',exact:true}).click();await expect(dialog.getByRole('button',{name:'Video field',exact:true})).toHaveText('video');
  await dialog.getByRole('button',{name:'Add card',exact:true}).click();const videos=card(page,'Videos');await videos.scrollIntoViewIfNeeded();
  await expect.poll(()=>videos.locator('video').first().evaluate((v:HTMLVideoElement)=>v.readyState)).toBeGreaterThan(0);
  await videos.getByRole('button',{name:'Enlarge video',exact:true}).first().click();await expect(page.getByRole('dialog').locator('video')).toBeVisible();
});

test('palette menus work in media cards and no workspace selector uses native controls',async({page})=>{
  await fixture(page);await expect(page.locator('main select')).toHaveCount(0);
  const sequence=card(page,'Follow a camera through time');await sequence.scrollIntoViewIfNeeded();await sequence.getByRole('button',{name:'Sequence',exact:true}).click();
  const menu=page.getByRole('listbox',{name:'Sequence',exact:true});await expect(menu).toBeVisible();const rect=await menu.boundingBox(),viewport=page.viewportSize()!;expect(rect!.x).toBeGreaterThanOrEqual(0);expect(rect!.x+rect!.width).toBeLessThanOrEqual(viewport.width);expect(rect!.y+rect!.height).toBeLessThanOrEqual(viewport.height);
  await menu.getByRole('option').last().click();await expect(sequence.getByRole('button',{name:'Sequence',exact:true})).toContainText('workspace-demo/b');
});

test('new cards group per run and the compact picker applies selections without Add',async({page})=>{
  await fixture(page);await page.getByRole('button',{name:'Add card',exact:true}).click();const dialog=page.getByRole('dialog');
  for(const kind of ['Scalar','Image','Plotly']){
    await dialog.getByRole('button',{name:kind,exact:true}).click();await dialog.getByRole('tab',{name:'Mapping'}).click();await expect(dialog.getByRole('button',{name:'Remove grouping @run',exact:true})).toHaveText('Run ×');
  }
  await expect(dialog.getByRole('button',{name:'Add',exact:true})).toHaveCount(0);
  await chooseField(dialog,'Add grouping field','camera');await expect(dialog.getByRole('button',{name:'Remove grouping camera',exact:true})).toBeVisible();
  await dialog.getByRole('button',{name:'Add grouping field'}).click();await expect(dialog.getByRole('listbox',{name:'Add grouping field'}).getByRole('option',{name:'camera',exact:true})).toHaveCount(0);await page.keyboard.press('Escape');
  await dialog.getByRole('button',{name:'Remove grouping camera'}).click();await dialog.getByRole('button',{name:'Cancel',exact:true}).click();
});

test('settings lock background scrolling, preserve position on close and release on history navigation',async({page})=>{
  await page.setViewportSize({width:1280,height:720});await fixture(page);
  const button=page.getByRole('button',{name:'Settings for Loss across runs'});await button.scrollIntoViewIfNeeded();
  const start=await page.evaluate(()=>scrollY);await button.click();const dialog=page.getByRole('dialog');
  await expect.poll(()=>page.evaluate(()=>getComputedStyle(document.documentElement).overflow)).toBe('hidden');
  await page.mouse.move(5,350);await page.mouse.wheel(0,600);await page.waitForTimeout(150);expect(await page.evaluate(()=>scrollY)).toBe(start);
  await dialog.getByRole('tab',{name:'Mapping'}).click();const controls=dialog.locator('.workspace-editor-controls');await controls.hover();await page.mouse.wheel(0,500);await expect.poll(()=>controls.evaluate(e=>e.scrollTop)).toBeGreaterThan(0);expect(await page.evaluate(()=>scrollY)).toBe(start);
  await dialog.getByRole('button',{name:'Done',exact:true}).click();await expect.poll(()=>page.evaluate(()=>getComputedStyle(document.documentElement).overflow)).not.toBe('hidden');expect(await page.evaluate(()=>scrollY)).toBe(start);
  await page.mouse.move(800,600);await page.mouse.wheel(0,300);await expect.poll(()=>page.evaluate(()=>scrollY)).toBeGreaterThan(start);
  await page.getByRole('link',{name:'Open run workspace-demo/b · v1'}).click();await page.getByRole('tab',{name:'Workspace',exact:true}).click();await page.getByRole('button',{name:'Settings for Loss over training'}).click();
  await page.goBack();await expect(page.getByRole('tab',{name:'Overview',exact:true})).toHaveAttribute('aria-selected','true');await expect(page.getByRole('dialog')).toHaveCount(0);await expect.poll(()=>page.evaluate(()=>getComputedStyle(document.documentElement).overflow)).not.toBe('hidden');
  await page.goForward();await expect(page.getByRole('tab',{name:'Workspace',exact:true})).toHaveAttribute('aria-selected','true');await expect(page.getByRole('dialog')).toHaveCount(0);await page.getByRole('button',{name:'Settings for Loss over training'}).click();await page.keyboard.press('Escape');await expect.poll(()=>page.evaluate(()=>getComputedStyle(document.documentElement).overflow)).not.toBe('hidden');
});

test('closing a nested record inspector keeps the settings scroll lock',async({page})=>{
  await fixture(page);await page.getByRole('button',{name:'Settings for Loss over training'}).click();const settings=page.getByRole('dialog',{name:'Card settings',exact:true});
  const point=await settings.locator('.workspace-scalar-plot').evaluate((n:any)=>{const u=n.uplot,r=u.over.getBoundingClientRect();return {x:r.left+u.valToPos(1,'x'),y:r.top+u.valToPos(2,'y')};});
  await page.mouse.move(point.x,point.y);await expect(settings.locator('.workspace-scalar-tooltip')).toBeVisible();await page.mouse.click(point.x,point.y);
  const inspector=page.getByRole('dialog',{name:'Record 2',exact:true});await expect(inspector).toBeVisible();await inspector.getByRole('button',{name:'Close dialog'}).click();await expect(settings).toBeVisible();expect(await page.evaluate(()=>getComputedStyle(document.documentElement).overflow)).toBe('hidden');
  await settings.getByRole('button',{name:'Close dialog'}).click();expect(await page.evaluate(()=>getComputedStyle(document.documentElement).overflow)).not.toBe('hidden');
});

test('directional drags zoom only the selected axis and reset restores padded home ranges',async({page})=>{
  await fixture(page);const loss=card(page,'Loss over training'),plot=loss.locator('.workspace-scalar-plot');
  const ranges=()=>plot.evaluate((n:any)=>({x:[n.uplot.scales.x.min,n.uplot.scales.x.max],y:[n.uplot.scales.y.min,n.uplot.scales.y.max]}));
  const home=await ranges();expect(home.x[0]).toBeLessThan(0);expect(home.x[1]).toBeGreaterThan(2);expect(home.y[0]).toBeLessThan(1);expect(home.y[1]).toBeGreaterThan(3);
  const drag=async(x1:number,y1:number,x2:number,y2:number,axis?:'x'|'y')=>{
    const r=(await loss.locator('.u-over').boundingBox())!;await page.mouse.move(r.x+x1*r.width,r.y+y1*r.height);await page.mouse.down();await page.mouse.move(r.x+x2*r.width,r.y+y2*r.height,{steps:8});
    if(axis)expect(await plot.evaluate((n:any,axis)=>n.uplot.select[axis==='x'?'height':'width'],axis)).toBeCloseTo(axis==='x'?r.height:r.width,0);
    await page.mouse.up();
  };
  await drag(.2,.5,.8,.52,'x');let view=await ranges();expect(view.x[1]-view.x[0]).toBeLessThan(home.x[1]-home.x[0]);expect(view.y).toEqual(home.y);
  const horizontal=view.x;await drag(.5,.8,.52,.2,'y');view=await ranges();expect(view.x).toEqual(horizontal);expect(view.y[1]-view.y[0]).toBeLessThan(home.y[1]-home.y[0]);
  const vertical=view.y;await drag(.8,.5,.2,.51,'x');view=await ranges();expect(view.y).toEqual(vertical);expect(view.x[1]-view.x[0]).toBeLessThan(horizontal[1]-horizontal[0]);
  await loss.getByRole('button',{name:'Reset plot zoom'}).click();await expect.poll(ranges).toEqual(home);
  await drag(.2,.2,.8,.8);view=await ranges();expect(view.x[1]-view.x[0]).toBeLessThan(home.x[1]-home.x[0]);expect(view.y[1]-view.y[0]).toBeLessThan(home.y[1]-home.y[0]);
  await loss.getByRole('button',{name:'Reset plot zoom'}).click();await expect.poll(ranges).toEqual(home);
});

test('log axes render filtered tick labels, retain positive padded bounds and survive reload',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await fixture(page);
  await page.route('**/v1/records/query',r=>{const id=r.request().postDataJSON().ref;return r.fulfill({json:{artifact_id:id,cutoff:5,next_cursor:null,items:[[-1,-1],[0,0],[1,.01],[10,.1],[100,1]].map(([step,loss],i)=>({artifact_id:id,ordinal:i+1,sequence:i,writer_id:'w',kind:'record',data:{step,loss},created_at:'2026-01-01'}))}});});
  await page.getByRole('button',{name:'Refresh workspace'}).click();await expect(page.locator('.workspace-data-status')).toContainText('10 records');
  const loss=card(page,'Loss over training'),plot=loss.locator('.workspace-scalar-plot');await loss.getByRole('button',{name:'Settings for Loss over training'}).click();const dialog=page.getByRole('dialog');await dialog.getByRole('tab',{name:'Appearance'}).click();
  await dialog.getByLabel('Logarithmic X').check();await dialog.getByLabel('Logarithmic Y').check();await dialog.getByRole('button',{name:'Done'}).click();
  const logarithmic=()=>plot.evaluate((n:any)=>{const u=n.uplot;return {x:u.scales.x.distr,y:u.scales.y.distr,xRange:[u.scales.x.min,u.scales.x.max],yRange:[u.scales.y.min,u.scales.y.max],dx:[u.valToPos(10,'x')-u.valToPos(1,'x'),u.valToPos(100,'x')-u.valToPos(10,'x')],dy:[u.valToPos(.01,'y')-u.valToPos(.1,'y'),u.valToPos(.1,'y')-u.valToPos(1,'y')],valid:u.data.slice(1).every((s:any)=>s[0].every((x:number|null,i:number)=>x===null||s[1][i]===null||x>0&&s[1][i]>0))};});
  let log=await logarithmic();expect(log.x).toBe(3);expect(log.y).toBe(3);expect(log.xRange[0]).toBeGreaterThan(0);expect(log.xRange[0]).toBeLessThan(1);expect(log.xRange[1]).toBeGreaterThan(100);expect(log.yRange[0]).toBeGreaterThan(0);expect(log.yRange[0]).toBeLessThan(.01);expect(log.yRange[1]).toBeGreaterThan(1);expect(log.dx[0]).toBeCloseTo(log.dx[1]);expect(log.dy[0]).toBeCloseTo(log.dy[1]);expect(log.valid).toBe(true);
  await page.reload();await expect(plot.locator('canvas')).toBeVisible();log=await logarithmic();expect(log.x).toBe(3);expect(log.y).toBe(3);
  await loss.getByRole('button',{name:'Settings for Loss over training'}).click();await dialog.getByRole('tab',{name:'Appearance'}).click();await dialog.getByLabel('Logarithmic X').uncheck();await dialog.getByLabel('Logarithmic Y').uncheck();await dialog.getByRole('button',{name:'Done'}).click();await expect.poll(()=>plot.evaluate((n:any)=>[n.uplot.scales.x.distr,n.uplot.scales.y.distr])).toEqual([1,1]);expect(errors).toEqual([]);
});
