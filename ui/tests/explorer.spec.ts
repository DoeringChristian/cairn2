import {test,expect} from '@playwright/test';

test('all-versions tags reach every version of a name',async({page,request})=>{
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=runs')).json();
  const runs=items.filter((a:any)=>a.name==='showcase-baseline'),tag=`shared-${Date.now()}`;
  expect(runs.length).toBeGreaterThan(1);
  try{
    await page.goto(`/p/neural-rendering/a/${runs[0].id}`);
    await page.getByRole('button',{name:'All versions',exact:true}).click();
    await page.getByLabel('New tag',{exact:true}).fill(tag);
    await page.getByRole('button',{name:'Add tag',exact:true}).click();
    await expect(page.getByRole('button',{name:`Remove tag ${tag}`})).toBeVisible();
    await expect.poll(async()=>(await (await request.get(`/v1/artifacts/${runs[1].id}/annotations`)).json()).annotations.tags||[]).toContain(tag);
  }finally{
    const current=await (await request.get(`/v1/artifacts/${runs[0].id}/annotations`)).json();
    await request.patch(`/ui-api/artifacts/${runs[0].id}/tags`,{data:{tags:(current.name.tags||[]).filter((t:string)=>t!==tag),scope:'name'}});
  }
});

test('artifact tab switches and refresh keep one tag editor',async({page,request})=>{
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering')).json();
  const artifact=items.find((item:any)=>item.namespace!=='reports');
  const errors:string[]=[];
  page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
  await page.goto(`/p/neural-rendering/a/${artifact.id}`);
  await page.getByLabel('New tag',{exact:true}).fill('draft-kept-across-tabs');
  for(let index=0;index<6;index++){
    for(const tab of ['Records','Overview']){
      await page.getByRole('tab',{name:tab,exact:true}).click();
      await expect(page.getByLabel('New tag',{exact:true})).toHaveCount(1);
      await expect(page.getByLabel('New tag',{exact:true})).toHaveValue('draft-kept-across-tabs');
    }
  }
  await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await expect(page.getByLabel('New tag',{exact:true})).toHaveCount(1);
  expect(errors.filter(message=>/same key/.test(message))).toEqual([]);
});

test('lineage controls stay put when explanation changes',async({page,request})=>{
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering')).json();
  const artifact=items.find((a:any)=>a.namespace!=='reports'),run='f'.repeat(32);
  await page.route('**/ui-api/lineage/**',route=>route.fulfill({json:{nodes:{
    [artifact.id]:{name:'result',version:1,kind:'artifact',inputs:[run],consumers:[]},
    [run]:{name:'runs/training',version:1,kind:'run',inputs:[],consumers:[artifact.id]}
  },truncated:false}}));
  await page.goto(`/p/neural-rendering/a/${artifact.id}?tab=lineage`);
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
  const forbidden: string[] = [];
  page.on('request', request => {
    if (/\/v1\/(artifacts|resolutions)/.test(request.url())) forbidden.push(request.url());
  });
  await page.route('**/ui-api/artifacts?*', async route => {
    requests++;
    const query = new URL(route.request().url()).searchParams;
    expect(query.get('category')).toBe('runs');
    expect(query.get('limit')).toBe('50');
    if (requests === 1) await new Promise(resolve => setTimeout(resolve, 6000));
    const offset = Number(query.get('offset'));
    const search = query.get('search');
    const items = Array.from({length: search ? 1 : 50}, (_, index) => ({
      id: String(offset+index), project: 'large', namespace: 'runs', name: search || `run-${offset+index}`,
      state: 'live', number: null, created_at: '2026-10-18T12:00:00Z', finished_at: null,
      annotations: {tags: []}, annotations_etag: 'test',
    }));
    await route.fulfill({json: {items, total: search ? 1 : 5000}});
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
  expect(forbidden).toEqual([]);
});

test('while auto-refresh is paused the list cache survives navigation; reload and refresh bypass it', async ({page}) => {
  const requests: string[] = [];
  await page.route('**/ui-api/artifacts?*', async route => {
    const query = new URL(route.request().url()).searchParams;
    const category = query.get('category')!;
    requests.push(category);
    await route.fulfill({json: {items: [{id: category, project:'cache-demo', namespace:category,
      name:`cached-${category}`, state:'live', number:null, created_at:'2026-10-18T12:00:00Z',
      annotations:{tags:[]}, annotations_etag:'test'}], total:1}});
  });
  const nav = page.getByRole('navigation', {name:'Project navigation'});
  await page.goto('/p/cache-demo?view=runs');
  await expect(page.getByRole('link', {name:'cached-runs', exact:true})).toBeVisible();
  await page.getByRole('button', {name:'Pause auto-refresh', exact:true}).click();
  await nav.getByRole('link', {name:'Artifacts', exact:true}).click();
  await expect(page.getByRole('link', {name:'cached-artifacts', exact:true})).toBeVisible();
  await nav.getByRole('link', {name:'Runs', exact:true}).click();
  await expect(page.getByRole('link', {name:'cached-runs', exact:true})).toBeVisible();
  expect(requests).toEqual(['runs','artifacts']);
  await page.reload();
  await expect(page.getByRole('link', {name:'cached-runs', exact:true})).toBeVisible();
  await expect.poll(() => requests).toEqual(['runs','artifacts','runs']);
  await page.getByRole('button', {name:'Pause auto-refresh', exact:true}).click();
  await page.getByRole('button', {name:'Refresh', exact:true}).click();
  await expect.poll(() => requests.length).toBe(4);
  expect(requests.at(-1)).toBe('runs');
  await nav.getByRole('link', {name:'Artifacts', exact:true}).click();
  await expect(page.getByRole('link', {name:'cached-artifacts', exact:true})).toBeVisible();
  expect(requests).toHaveLength(4);
});

test('run console shows the output stored by the run',async({page,request})=>{
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=runs')).json();
  const run=items.find((a:any)=>a.name==='showcase-baseline'&&a.state==='finished');
  await page.goto(`/p/neural-rendering/a/${run.id}`);
  await page.getByRole('tab',{name:'Console',exact:true}).click();
  await expect(page.locator('.console-output')).toHaveText('step 0: loss 1.02\nstep 20: loss 0.349193\nstep 40: loss 0.128368\n');
  await page.getByRole('tab',{name:'Overview',exact:true}).click();
  await expect(page.getByRole('heading',{name:/^console\//})).toHaveCount(0);
});

test('live artifact keeps polling until it finishes', async ({page,request}) => {
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=datasets')).json();
  const artifact=items[0];
  let checks=0;
  await page.clock.install();
  await page.route('**/v1/resolutions', route => {checks++;return route.fulfill({json:[{...artifact,state:checks<3?'live':'finished'}]});});
  await page.goto(`/p/neural-rendering/a/${artifact.id}`);
  await expect(page.getByRole('heading',{name:'Value',exact:true})).toBeVisible();
  expect(checks).toBe(1);
  // The next check is scheduled only after a response settles; advance until it fires.
  const advanceTo=(count:number)=>expect.poll(async()=>{await page.clock.fastForward(1000);return checks;}).toBe(count);
  await advanceTo(2);
  await advanceTo(3);
  await expect(page.getByText('This artifact is live. Its ID stays the same when it finishes.',{exact:true})).toHaveCount(0);
  await page.clock.fastForward(16000);
  expect(checks).toBe(3);
});

test('a run whose process is gone shows lost contact and can be marked as stopped',async({page,request})=>{
  const name=`abandoned-${Date.now()}`,{artifact}=await (await request.post('/v1/artifacts',{data:{project:'neural-rendering',namespace:'runs',name}})).json();
  try{
    let lost=true;await page.route('**/v1/liveness?*',r=>r.fulfill({json:[{artifact_id:artifact.id,last_seen:artifact.created_at,heartbeat:true,lost}]}));
    await page.goto(`/p/neural-rendering/a/${artifact.id}`);await expect(page.getByText('Lost contact:')).toBeVisible();await expect(page.getByText('lost contact',{exact:true})).toBeVisible();
    lost=false;await page.reload();await expect(page.getByText('This artifact is live.')).toBeVisible();await expect(page.getByRole('button',{name:'Mark as stopped'})).toHaveCount(0);
    await page.unroute('**/v1/liveness?*');await page.reload();await page.getByRole('button',{name:'Mark as stopped'}).click();
    await page.getByRole('dialog').getByRole('button',{name:'Mark as stopped',exact:true}).click();
    await expect(page.getByText('incomplete',{exact:true})).toBeVisible();
    expect((await (await request.get(`/v1/artifacts?project=neural-rendering&namespace=runs&state=finished`)).json()).items.find((a:any)=>a.id===artifact.id).manifest.outcome).toBe('incomplete');
  }finally{await request.delete(`/ui-api/artifacts/${artifact.id}`);}
});

test('the interface explores runs and artifacts and has no reports',async({page,request})=>{
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=runs')).json(),run=items.find((a:any)=>a.name==='showcase-baseline');
  await page.goto('/p/neural-rendering');await expect(page.getByRole('link',{name:'showcase-baseline'}).first()).toBeVisible();await expect(page.getByRole('link',{name:'Reports',exact:true})).toHaveCount(0);
  await page.goto(`/p/neural-rendering/a/${run.id}`);await expect(page.getByRole('heading',{name:'Metric trends',exact:true})).toBeVisible();
  await page.goto('/p/neural-rendering/r/field-guide');await expect(page.getByRole('heading',{name:'Page not found'})).toBeVisible();
});
