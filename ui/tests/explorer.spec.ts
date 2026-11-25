import {test,expect,type Page} from '@playwright/test';
import {artifact,on} from './cairn';

async function open(page:Page,name:string,view='runs'){
  await page.goto(`/p/neural-rendering?view=${view}`);
  await page.getByRole('link',{name,exact:true}).first().click();
  await expect(page.getByRole('heading',{name,exact:true})).toBeVisible();
  return page.url().split('/a/')[1];
}

test('tags are added, filter lists, and are removed',async({page})=>{
  const tag=`shared-${Date.now()}`;
  await open(page,'showcase-baseline');
  await page.getByLabel('New tag',{exact:true}).fill(`${tag}=3`);
  await page.getByRole('button',{name:'Add tag',exact:true}).click();
  await expect(page.getByText(`${tag}=3`,{exact:true})).toBeVisible();
  await page.goto(`/p/neural-rendering?where=${encodeURIComponent(`tag:${tag} > 2`)}`);
  await expect(page.locator('.artifact-list tbody tr')).toHaveCount(1);
  await page.goto(`/p/neural-rendering?where=${encodeURIComponent(`tag:${tag} > 3`)}`);
  await expect(page.getByText('No matching artifacts in this view.')).toBeVisible();
  await page.goto(`/p/neural-rendering?where=${encodeURIComponent(`tag:${tag}`)}`);
  await page.getByRole('button',{name:`Remove tag ${tag}`}).click();
  await expect(page.getByText('No matching artifacts in this view.')).toBeVisible();
});

test('lists filter by logged values',async({page})=>{
  await page.goto('/p/neural-rendering');
  const filter=page.getByRole('searchbox',{name:/^Filter/});
  await filter.fill('psnr>3');await filter.press('Enter');
  await expect(page.getByText('Conditions read like')).toBeVisible();
  await filter.fill('psnr > 30');await filter.press('Enter');
  await expect(page.getByRole('link',{name:'showcase-baseline',exact:true})).toHaveCount(2);
  await filter.fill('_config/seed = 41');await filter.press('Enter');
  await expect(page.getByText('No matching artifacts in this view.')).toBeVisible();
});

test('values show as what they are',async({page})=>{
  const errors:string[]=[];page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
  await open(page,'showcase-baseline');
  for(const name of ['Metric trends','Last values','Configuration','figures/field-start.png','media/wave-evolution.mp4'])await expect(page.getByRole('heading',{name,exact:true})).toBeVisible();
  await expect.poll(()=>page.locator('.image-preview img').first().evaluate((node:HTMLImageElement)=>node.naturalWidth)).toBeGreaterThan(0);
  await expect.poll(()=>page.locator('video').first().evaluate((node:HTMLVideoElement)=>node.duration)).toBeGreaterThan(0);
  const figure=page.locator('.native-plotly');await figure.scrollIntoViewIfNeeded();await expect(figure.locator('.main-svg').first()).toBeVisible();
  await page.getByRole('tab',{name:'Files',exact:true}).click();
  await page.getByRole('searchbox',{name:'Search files'}).fill('START');
  await expect(page.locator('tbody tr')).toHaveCount(1);
  const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('link',{name:'Download'}).click()]);
  expect(download.suggestedFilename()).toBe('field-start.png');
  await open(page,'showcase-samples','artifacts');
  await expect(page.getByRole('cell',{name:'0.03125'}).first()).toBeVisible();
  expect(errors).toEqual([]);
});

test('records are listed by key',async({page})=>{
  await open(page,'showcase-baseline');
  await page.getByRole('tab',{name:'Records',exact:true}).click();
  await page.getByRole('button',{name:'Records with',exact:true}).click();
  await page.getByRole('option',{name:'loss',exact:true}).click();
  await expect(page.locator('tbody tr')).toHaveCount(60);
  await page.getByRole('button',{name:/^Inspect/}).first().click();
  await expect(page.locator('pre').first()).toContainText('"step": 0');
});

test('artifact tab switches and refresh keep one tag editor',async({page})=>{
  await open(page,'showcase-baseline');
  await page.getByLabel('New tag',{exact:true}).fill('draft-kept-across-tabs');
  for(let index=0;index<6;index++){
    for(const tab of ['Records','Overview']){
      await page.getByRole('tab',{name:tab,exact:true}).click();
      await expect(page.getByLabel('New tag',{exact:true})).toHaveCount(1);
      await expect(page.getByLabel('New tag',{exact:true})).toHaveValue('draft-kept-across-tabs');
    }
  }
});

test('lineage controls stay put when explanation changes',async({page})=>{
  const id=await open(page,'showcase-samples','artifacts'),run='f'.repeat(32);
  await page.route('**/ui-api/lineage/**',route=>route.fulfill({json:{nodes:{
    [id]:{name:'result',version:1,kind:'artifact',inputs:[run],consumers:[]},
    [run]:{name:'runs/training',version:1,kind:'run',inputs:[],consumers:[id]}
  },truncated:false}}));
  await page.goto(`/p/neural-rendering/a/${id}?tab=lineage`);
  const toggle=page.getByRole('button',{name:'Data only',exact:true});
  await expect(toggle).toBeVisible();
  const before=await toggle.boundingBox();
  await toggle.click();
  await expect(page.getByText('No data inputs or consumers (only runs, hidden here).',{exact:true})).toBeVisible();
  const after=await toggle.boundingBox();
  expect(after!.x).toBe(before!.x);
  expect(after!.y).toBe(before!.y);
});

test('run list is bounded and slow loads survive auto refresh', async ({page}) => {
  let requests = 0;
  await on(page, 'find', 'Find', 'Artifacts', async request => {
    requests++;
    expect(request.namespaces).toEqual(['runs']);
    expect(request.limit).toBe(50);
    if (requests === 1) await new Promise(resolve => setTimeout(resolve, 6000));
    const items = Array.from({length: request.text ? 1 : 50}, (_, index) => artifact(String(request.offset + index), request.text || `run-${request.offset + index}`, 0));
    return {items, total: request.text ? 1 : 5000};
  });
  await page.goto('/p/large?view=runs');
  await expect(page.getByRole('link', {name:'run-0', exact:true})).toBeVisible({timeout:10000});
  expect(requests).toBe(1);
  await expect(page.getByRole('textbox', {name:'New tag', exact:true})).toHaveCount(50);
  await page.getByRole('button', {name:'Next', exact:true}).click();
  await expect(page.getByRole('link', {name:'run-50', exact:true})).toBeVisible();
  await expect(page.getByRole('link', {name:'run-0', exact:true})).toHaveCount(0);
  await page.getByRole('searchbox', {name:'Search artifacts'}).fill('specific-run');
  await expect(page.getByRole('link', {name:'specific-run', exact:true})).toBeVisible();
});

test('while auto-refresh is paused the list cache survives navigation; reload and refresh bypass it', async ({page}) => {
  const requests: string[] = [];
  await on(page, 'projects', 'Projects', 'ProjectList', () => ({items: [{name: 'cache-demo', artifacts: {}, updated: 1767225600}]}));
  await on(page, 'find', 'Find', 'Artifacts', request => {
    const view = request.namespaces[0] || 'all';
    requests.push(view);
    return {items: [artifact(view, `cached-${view}`, 0)], total: 1};
  });
  const nav = page.getByRole('navigation', {name:'Project navigation'});
  await page.goto('/p/cache-demo?view=runs');
  await expect(page.getByRole('link', {name:'cached-runs', exact:true})).toBeVisible();
  await page.getByRole('button', {name:'Pause auto-refresh', exact:true}).click();
  await nav.getByRole('link', {name:'All', exact:true}).click();
  await expect(page.getByRole('link', {name:'cached-all', exact:true})).toBeVisible();
  await nav.getByRole('link', {name:'Runs', exact:true}).click();
  await expect(page.getByRole('link', {name:'cached-runs', exact:true})).toBeVisible();
  expect(requests).toEqual(['runs','all']);
  await page.reload();
  await expect(page.getByRole('link', {name:'cached-runs', exact:true})).toBeVisible();
  await expect.poll(() => requests).toEqual(['runs','all','runs']);
  await page.getByRole('button', {name:'Pause auto-refresh', exact:true}).click();
  await page.getByRole('button', {name:'Refresh', exact:true}).click();
  await expect.poll(() => requests.length).toBe(4);
  expect(requests.at(-1)).toBe('runs');
  await nav.getByRole('link', {name:'All', exact:true}).click();
  await expect(page.getByRole('link', {name:'cached-all', exact:true})).toBeVisible();
  expect(requests).toHaveLength(4);
});

test('run console shows the output logged by the run',async({page})=>{
  await open(page,'showcase-baseline');
  await page.getByRole('tab',{name:'Console',exact:true}).click();
  await expect(page.locator('.console-output')).toHaveText('step 0: loss 1.02\nstep 20: loss 0.349193\nstep 40: loss 0.128368\n');
});

test('live artifact keeps polling until it finishes',async({page})=>{
  let checks=0;
  await page.clock.install();
  await on(page,'resolve','Resolve','Artifacts',()=>{checks++;return {items:[artifact('x','polled',checks<3?0:1,{project:'neural-rendering',heard_at:4e9})]};});
  await on(page,'keys','Keys','KeyList',()=>({keys:['_outcome']}));
  await on(page,'last','Last','LastValues',()=>({entries:checks<3?[]:[{key:'_outcome',value:'completed'}]}));
  await on(page,'records','Records','RecordPage',()=>({total:0}));
  await page.goto('/p/neural-rendering/a/x');
  await expect(page.getByText('This artifact is live.')).toBeVisible();
  expect(checks).toBe(1);
  // The next check is scheduled only after a response settles; advance until it fires.
  const advanceTo=(count:number)=>expect.poll(async()=>{await page.clock.fastForward(1000);return checks;}).toBe(count);
  await advanceTo(2);
  await advanceTo(3);
  await expect(page.getByText('This artifact is live.')).toHaveCount(0);
  await page.clock.fastForward(16000);
  expect(checks).toBe(3);
});

test('a run whose process is gone shows lost contact and can be marked as stopped',async({page})=>{
  let heard=1767225600,stopped=false;
  await on(page,'resolve','Resolve','Artifacts',()=>({items:[artifact('x','gone',stopped?1:0,{project:'neural-rendering',heard_at:heard})]}));
  await on(page,'keys','Keys','KeyList',()=>({keys:['_outcome']}));
  await on(page,'last','Last','LastValues',()=>({entries:stopped?[{key:'_outcome',value:'incomplete'}]:[]}));
  await on(page,'records','Records','RecordPage',()=>({total:0}));
  await page.route('**/ui-api/stop/x',r=>{stopped=true;return r.fulfill({json:{}});});
  await page.goto('/p/neural-rendering/a/x');await expect(page.getByText('Lost contact:')).toBeVisible();await expect(page.getByText('lost contact',{exact:true})).toBeVisible();
  heard=Date.now()/1000;await page.reload();await expect(page.getByText('This artifact is live.')).toBeVisible();await expect(page.getByRole('button',{name:'Mark as stopped'})).toHaveCount(0);
  heard=1767225600;await page.reload();await page.getByRole('button',{name:'Mark as stopped'}).click();
  await page.getByRole('dialog').getByRole('button',{name:'Mark as stopped',exact:true}).click();
  await expect(page.getByText('incomplete',{exact:true})).toBeVisible();
});

test('unknown pages say so',async({page})=>{
  await page.goto('/p/neural-rendering/r/field-guide');await expect(page.getByRole('heading',{name:'Page not found'})).toBeVisible();
});
