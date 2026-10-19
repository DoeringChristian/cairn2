import {test,expect} from '@playwright/test';

test('plain artifact identities, native reports, media zoom and lineage',async({page,request})=>{
  const errors:string[]=[];
  page.on('pageerror',e=>errors.push(e.message));
  const response=await request.get('/v1/artifacts?project=neural-rendering&namespace=reports');
  const {items}=await response.json();
  const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
  expect(report.id).toMatch(/^[0-9a-f]{32}$/);
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  await expect(page.locator('article.native-document')).toBeVisible();
  await expect(page.locator('iframe:not(.native-html-viewer)')).toHaveCount(0);
  await page.getByRole('button',{name:'Enlarge Python cell image output'}).first().click();
  const dialog=page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect.poll(()=>dialog.locator('img').evaluate((img:HTMLImageElement)=>img.naturalWidth)).toBeGreaterThan(0);
  await dialog.getByRole('button',{name:'Zoom in',exact:true}).click();
  await expect(dialog.getByLabel('Zoom level')).toHaveText('125%');
  await dialog.getByRole('button',{name:'Fit / reset'}).click();
  await expect(dialog.getByLabel('Zoom level')).toHaveText('100%');
  await dialog.getByRole('button',{name:'Close media viewer'}).click();
  await expect(page.locator('audio')).toHaveCount(1);
  await expect(page.locator('video')).toHaveCount(1);
  await page.getByRole('button',{name:'Enlarge video',exact:true}).click();
  await dialog.getByRole('button',{name:'Play video',exact:true}).click();
  await expect.poll(()=>dialog.locator('video').evaluate((v:HTMLVideoElement)=>v.currentTime)).toBeGreaterThan(0);
  await dialog.getByRole('button',{name:'Pause video',exact:true}).click();
  await expect(dialog.getByRole('button',{name:'Play video',exact:true}).locator('svg')).toHaveCount(1);
  await dialog.getByRole('button',{name:'Close media viewer'}).click();
  const toc=page.getByRole('navigation',{name:'Table of contents'});
  await expect(toc).toBeVisible();
  if(await toc.getByRole('button').getAttribute('aria-expanded')==='false')await toc.getByRole('button').click();
  const anchor=toc.getByRole('link').nth(1), target=await anchor.getAttribute('href');
  await anchor.click();
  await expect(page).toHaveURL(new RegExp(target!+'$'));
  await expect(page.locator(target!)).toBeVisible();
  await page.goto(`/p/neural-rendering/a/${report.id}?tab=lineage`);
  await expect(page.getByRole('img',{name:'Lineage graph'})).toBeVisible();
  await expect(page.getByText('Lineage unavailable:')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('media gestures change actual geometry and copy feedback resets',async({page,context,request})=>{
  await context.grantPermissions(['clipboard-read','clipboard-write']);
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  for(const video of [false,true]){
    await page.getByRole('button',{name:video?'Enlarge video':'Enlarge Python cell image output',exact:true}).first().click();
    const dialog=page.getByRole('dialog'),media=dialog.locator(video?'video':'img');
    await expect.poll(()=>media.evaluate((node:HTMLImageElement|HTMLVideoElement)=>node instanceof HTMLVideoElement?node.videoWidth:node.naturalWidth)).toBeGreaterThan(0);
    const width=await media.evaluate(node=>node.getBoundingClientRect().width);
    await dialog.getByRole('button',{name:'Zoom in',exact:true}).click();
    await expect.poll(()=>media.evaluate(node=>node.getBoundingClientRect().width)).toBeGreaterThan(width*1.2);
    await dialog.getByRole('button',{name:'Fit / reset',exact:true}).click();
    await expect(dialog.getByLabel('Zoom level')).toHaveText('100%');
    const viewport=dialog.getByRole('region',{name:video?'Zoomable video viewport':'Zoomable image viewport'});
    await viewport.hover();await page.mouse.wheel(0,-300);
    await expect.poll(()=>media.evaluate(node=>node.getBoundingClientRect().width)).toBeGreaterThan(width*1.2);
    const copyLabel=video?'Copy time reference':'Copy source reference';
    await dialog.getByRole('button',{name:copyLabel,exact:true}).click();
    await expect(dialog.getByRole('button',{name:'Copied',exact:true})).toBeVisible();
    await expect(dialog.getByRole('button',{name:copyLabel,exact:true})).toBeVisible({timeout:4000});
    await expect(dialog.getByRole('button',{name:copyLabel,exact:true}).locator('svg')).toHaveCount(1);
    expect(await page.evaluate(()=>navigator.clipboard.readText())).toContain('file');
    await dialog.getByRole('button',{name:'Close media viewer'}).click();
  }
});

test('video references combine temporal and spatial selections',async({page,context,request})=>{
  await context.grantPermissions(['clipboard-read','clipboard-write']);
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  await page.getByRole('button',{name:'Enlarge video',exact:true}).click();
  const dialog=page.getByRole('dialog');
  await expect.poll(()=>dialog.locator('video').evaluate((v:HTMLVideoElement)=>v.duration)).toBeGreaterThan(0);
  await dialog.getByRole('button',{name:'Select time range',exact:true}).click();
  const timeline=(await dialog.getByLabel('Video timeline',{exact:true}).boundingBox())!;
  await page.mouse.move(timeline.x+timeline.width*.2,timeline.y+18);await page.mouse.down();await page.mouse.move(timeline.x+timeline.width*.7,timeline.y+18,{steps:5});await page.mouse.up();
  await expect(dialog.getByRole('slider',{name:'Clip start',exact:true})).toBeVisible();
  await dialog.getByRole('button',{name:'Select spatial region',exact:true}).click();
  const video=(await dialog.locator('video').boundingBox())!;
  await page.mouse.move(video.x+video.width*.2,video.y+video.height*.2);await page.mouse.down();await page.mouse.move(video.x+video.width*.6,video.y+video.height*.6,{steps:5});await page.mouse.up();
  await dialog.getByRole('button',{name:'Copy clip reference',exact:true}).click();
  await expect(dialog.getByRole('button',{name:'Copied',exact:true})).toBeVisible();
  const reference=JSON.parse(await page.evaluate(()=>navigator.clipboard.readText()));
  expect(reference.clip.start).toBeCloseTo(.8,1);expect(reference.clip.end).toBeCloseTo(2.8,1);
  expect(reference.region.width).toBeGreaterThan(100);expect(reference.region.height).toBeGreaterThan(50);
  await dialog.getByRole('button',{name:'Clear reference selection'}).click();
  await expect(dialog.getByRole('slider',{name:'Clip start',exact:true})).toHaveCount(0);
});

test('showcase renders themed interactive Plotly output',async({page,request})=>{
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  const plot=page.getByRole('img',{name:'Interactive Plotly figure'}).first();
  await plot.scrollIntoViewIfNeeded();
  await expect(plot.locator('.scatterlayer .trace')).toHaveCount(2,{timeout:20000});
  await expect(plot.locator('.modebar')).toBeVisible();
  const background=await plot.locator('.main-svg').first().evaluate(el=>getComputedStyle(el).backgroundColor);
  expect(background).toBe('rgb(255, 254, 249)');
  await plot.locator('.legendtoggle').first().click();
  await expect(plot.locator('.scatterlayer .trace')).toHaveCount(1);
  expect(errors).toEqual([]);
});

test('comments autosave, highlight, reopen beside text, and delete across versions',async({page,context,request})=>{
  await context.grantPermissions(['clipboard-read','clipboard-write']);
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const reports=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number);
  await page.goto(`/p/neural-rendering/a/${reports[0].id}`);
  const paragraph=page.locator('.native-document p').filter({hasText:'This is a working tour'}).first();
  await paragraph.scrollIntoViewIfNeeded();
  await paragraph.evaluate(node=>{const range=document.createRange();range.selectNodeContents(node);const selection=window.getSelection()!;selection.removeAllRanges();selection.addRange(range);});
  await page.getByRole('button',{name:'Comment on selected text',exact:true}).click();
  const editor=page.getByRole('group',{name:'Add report comment'}),body=`Browser comment test ${Date.now()}`;
  await editor.getByLabel('Comment',{exact:true}).fill(body);
  await expect(editor.getByRole('button',{name:/save/i})).toHaveCount(0);
  await expect(editor.getByLabel('Saved',{exact:true})).toBeVisible({timeout:5000});
  await editor.getByLabel('Comment',{exact:true}).fill(body+' updated');
  await expect.poll(async()=>{const rows=await (await request.get(`/ui-api/reports/${reports[0].id}/comments`)).json();return rows.filter((c:any)=>c.body===body+' updated').length;}).toBe(1);
  await expect(editor.getByRole('button',{name:/close/i})).toHaveCount(0);
  await page.getByRole('heading',{name:'Published report',exact:true}).click();
  await expect(editor).toHaveCount(0);
  await expect.poll(()=>page.evaluate(()=>CSS.highlights.get('cairn-comments')?.size||0)).toBeGreaterThan(0);
  await expect(page.locator('.report-comments')).toHaveCount(0);
  const marker=page.getByTitle(body+' updated',{exact:true});
  await expect(marker).toBeVisible();
  const appearance=(node:Element)=>{const css=getComputedStyle(node);return [css.width,css.height,css.color,css.backgroundColor,css.border,css.borderRadius,css.boxShadow];};
  expect(await page.locator('.output-comment-button').first().evaluate(appearance)).toEqual(await marker.evaluate(appearance));
  const shell=page.locator('.native-output-shell').first();
  await shell.evaluate(node=>(node as HTMLElement).style.width='180px');
  const mediaMarker=shell.locator(':scope > .output-comment-button');
  await expect.poll(async()=>Math.abs((await mediaMarker.boundingBox())!.x-(await marker.boundingBox())!.x)).toBeLessThan(1);
  await shell.evaluate(node=>(node as HTMLElement).style.removeProperty('width'));
  await marker.click();
  await expect(editor.getByLabel('Comment',{exact:true})).toHaveValue(body+' updated');
  await editor.getByLabel('Comment',{exact:true}).fill(body+' final');
  await page.getByRole('heading',{name:'Published report',exact:true}).click();
  await expect(editor).toHaveCount(0);
  await expect.poll(async()=>{const rows=await (await request.get(`/ui-api/reports/${reports[0].id}/comments`)).json();return rows.some((c:any)=>c.body===body+' final');}).toBe(true);
  await page.goto(`/p/neural-rendering/a/${reports[1].id}`);
  await page.locator('.native-document p').filter({hasText:'This is a working tour'}).first().scrollIntoViewIfNeeded();
  await expect.poll(()=>page.evaluate(()=>CSS.highlights.get('cairn-comments')?.size||0)).toBeGreaterThan(0);
  await page.getByTitle(body+' final',{exact:true}).click();
  await expect(editor.getByLabel('Comment',{exact:true})).toHaveValue(body+' final');
  await editor.getByRole('button',{name:'Delete comment'}).click();await expect(editor).toHaveCount(0);
  await expect.poll(async()=>{const rows=await (await request.get(`/ui-api/reports/${reports[0].id}/comments`)).json();return rows.some((c:any)=>c.body===body+' final');}).toBe(false);
});

test('report overview groups versions and opens latest publication',async({page,request})=>{
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const reports=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number);
  expect(reports.length).toBeGreaterThan(1);
  await page.goto('/p/neural-rendering');
  await expect(page.locator('.report-card')).toHaveCount(new Set(items.map((a:any)=>a.name)).size);
  const card=page.locator('.report-card').filter({hasText:'The report field guide'});
  await expect(card).toHaveCount(1);
  await expect(card).toHaveAttribute('href',`/p/neural-rendering/a/${reports[0].id}`);
  await card.click();
  await expect(page.getByRole('button',{name:'Artifact version'})).toContainText(`v${reports[0].number}`);
});

test('media comments keep region and time and empty edits delete',async({page,request})=>{
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  for(const video of [false,true]){
    await page.getByRole('button',{name:video?'Enlarge video':'Enlarge Python cell image output',exact:true}).first().click();
    const dialog=page.getByRole('dialog');
    await dialog.getByRole('button',{name:'Comment on media'}).click();
    const editor=page.getByRole('group',{name:'Add report comment'}),body=`Browser media test ${Date.now()}`;
    await editor.getByLabel('Comment',{exact:true}).fill(body);
    await expect(editor.getByLabel('Saved',{exact:true})).toBeVisible();
    const rows=await (await request.get(`/ui-api/reports/${report.id}/comments`)).json(),comment=rows.find((c:any)=>c.body===body);
    expect(comment.anchor.media_key).toBeTruthy();if(video)expect(comment.anchor.media.time).toBeGreaterThanOrEqual(0);
    await dialog.getByRole('button',{name:'Zoom in',exact:true}).click();
    await expect(editor).toHaveCount(0);
    await expect(dialog.getByRole('button',{name:'Comment on media',exact:true})).toHaveCount(0);
    await dialog.getByRole('button',{name:'Open comment',exact:true}).click();
    await expect(editor.getByLabel('Comment',{exact:true})).toHaveValue(body);
    await editor.getByLabel('Comment',{exact:true}).fill(body+' edited');
    await expect.poll(async()=>{const rows=await (await request.get(`/ui-api/reports/${report.id}/comments`)).json();return rows.filter((c:any)=>c.anchor.media_key===comment.anchor.media_key).map((c:any)=>({id:c.id,body:c.body}));}).toEqual([{id:comment.id,body:body+' edited'}]);
    await dialog.getByRole('button',{name:'Zoom in',exact:true}).click();
    await dialog.getByRole('button',{name:'Close media viewer'}).click();
    const shell=page.locator('[data-comment-media]').filter({has:page.getByRole('button',{name:video?'Enlarge video':'Enlarge Python cell image output',exact:true})}).first();
    await expect(shell.locator(':scope > .output-comment-button')).toHaveCount(1);
    await shell.getByRole('button',{name:'Open comment',exact:true}).click();
    await expect(editor.getByLabel('Comment',{exact:true})).toHaveValue(body+' edited');
    await expect(page.locator('.report-comment-marker').and(page.getByTitle(body+' edited',{exact:true}))).toHaveCount(0);
    await editor.getByLabel('Comment',{exact:true}).fill('');
    await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await expect(editor).toHaveCount(0);
    await expect.poll(async()=>{const rows=await (await request.get(`/ui-api/reports/${report.id}/comments`)).json();return rows.some((c:any)=>c.id===comment.id);}).toBe(false);
    await expect(shell.locator(':scope > .output-comment-button')).toHaveAttribute('aria-label',/^Comment on /);
  }
});

test('custom HTML executes in isolation, resizes and offers WebGPU',async({page,request})=>{
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  const htmlOutput=page.locator('[data-output-mime="text/html"]');
  await expect(page.locator('article.native-document')).toBeVisible();
  if(await htmlOutput.count())await htmlOutput.last().scrollIntoViewIfNeeded();
  const frame=page.frameLocator('iframe.native-html-viewer');
  await expect(frame.locator('#renderer')).toHaveText(/WebGPU renderer active|Canvas fallback/,{timeout:20000});
  await expect(page.locator('iframe.native-html-viewer')).toHaveAttribute('sandbox','allow-scripts');
  const isolation=await frame.locator('body').evaluate(()=>{try{return !!parent.document.body;}catch{return false;}});expect(isolation).toBe(false);
  await expect.poll(()=>page.locator('iframe.native-html-viewer').evaluate(el=>el.getBoundingClientRect().height)).toBeGreaterThan(360);
  const before=await page.locator('iframe.native-html-viewer').evaluate(el=>el.getBoundingClientRect().height);
  await frame.getByText('About this viewer',{exact:true}).click();
  await expect.poll(()=>page.locator('iframe.native-html-viewer').evaluate(el=>el.getBoundingClientRect().height)).toBeGreaterThan(before);
  await frame.getByRole('slider').fill('3');
  const surface=page.getByRole('img',{name:'Interactive Plotly figure'}).nth(1);
  await surface.scrollIntoViewIfNeeded();
  await expect(surface.locator('canvas').first()).toBeVisible({timeout:20000});
});

test('ordinary code fences are highlighted and remain display-only',async({page,request})=>{
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  const python=page.getByLabel('python code',{exact:true}).filter({hasText:'def summarize'});
  await expect(python.locator('.hljs-keyword').first()).toBeVisible();
  await expect(page.getByLabel('javascript code',{exact:true}).locator('.hljs-keyword').first()).toBeVisible();
  await expect(page.getByLabel('yaml code',{exact:true}).locator('.hljs-attr').first()).toBeVisible();
  await expect(page.locator('.native-output').filter({hasText:'This code fence is not executed'})).toHaveCount(0);
  await expect(page.getByLabel('text code',{exact:true})).toContainText('<script>this is displayed, not executed</script>');
  await expect(page.getByRole('button',{name:'Enlarge video',exact:true})).toHaveText('');
});

test('copy controls preserve full code and IDs, media comment controls sit right, tags filter runs',async({page,request,context})=>{
  await context.grantPermissions(['clipboard-read','clipboard-write']);
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering')).json();
  const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  const code=page.locator('.syntax-code').filter({hasText:'def summarize'}).first();
  const original=await code.locator('code').innerText();
  await code.getByRole('button',{name:'Copy code',exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(original);
  await expect(code.getByRole('status')).toHaveText('Copied');
  await expect(code.getByRole('status')).toHaveText('');
  await page.getByRole('button',{name:`Copy ID ${report.id}`,exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(report.id);
  for(const selector of ['.preview-open','.prism-video-preview','audio']){
    const media=page.locator(selector).first();
    if(!await media.count())continue;
    const shell=media.locator('xpath=..');
    const bug=shell.locator(':scope > .output-comment-button');
    await expect(bug).toHaveCount(1);
    const boxes=await Promise.all([bug.boundingBox(),media.boundingBox()]);
    expect(boxes[0]!.x).toBeGreaterThanOrEqual(boxes[1]!.x+boxes[1]!.width);
    await bug.click();
    await expect(page.getByRole('group',{name:'Add report comment'})).toBeVisible();
    await page.getByLabel('New tag').click();
  }
  const run=items.find((a:any)=>a.namespace==='runs'),tag=`ui-check-${Date.now()}`;
  await page.goto(`/p/neural-rendering/a/${run.id}`);
  try{
    await page.getByLabel('New tag').fill(tag);
    await page.getByRole('button',{name:'Add tag',exact:true}).click();
    await expect(page.getByRole('button',{name:`Remove tag ${tag}`})).toBeVisible();
    await page.goto('/p/neural-rendering?view=runs');
    await page.getByLabel('Filter by tags').getByRole('button',{name:tag,exact:true}).click();
    await expect(page.locator('tbody tr')).toHaveCount(1);
    await expect(page.locator('tbody tr')).toContainText(run.name);
  }finally{
    const current=(await (await request.post('/v1/resolutions',{data:{refs:[run.id]}})).json())[0];
    await request.patch(`/ui-api/artifacts/${run.id}/tags`,{data:{tags:(current.annotations.tags||[]).filter((t:string)=>t!==tag),expected_etag:current.annotations_etag}});
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

test('list cache survives category navigation and reload, refresh bypasses it', async ({page}) => {
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
  await page.getByRole('button', {name:'Auto-refresh · 5s', exact:true}).click();
  await nav.getByRole('link', {name:'Reports', exact:true}).click();
  await expect(page.getByText('cached-reports', {exact:true})).toBeVisible();
  await nav.getByRole('link', {name:'Artifacts', exact:true}).click();
  await expect(page.getByRole('link', {name:'cached-artifacts', exact:true})).toBeVisible();
  await nav.getByRole('link', {name:'Runs', exact:true}).click();
  await expect(page.getByRole('link', {name:'cached-runs', exact:true})).toBeVisible();
  expect(requests).toEqual(['runs','reports','artifacts']);
  await page.reload();
  await expect(page.getByRole('link', {name:'cached-runs', exact:true})).toBeVisible();
  expect(requests).toEqual(['runs','reports','artifacts']);
  await page.getByRole('button', {name:'Auto-refresh · 5s', exact:true}).click();
  await page.getByRole('button', {name:'Refresh', exact:true}).click();
  await expect.poll(() => requests.length).toBe(4);
  expect(requests.at(-1)).toBe('runs');
  await nav.getByRole('link', {name:'Reports', exact:true}).click();
  await expect(page.getByText('cached-reports', {exact:true})).toBeVisible();
  expect(requests).toHaveLength(4);
});

test('report text does not wait for history or media and survives a slow reload', async ({page,request}) => {
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
  let release!:()=>void;
  const pending = new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/ui-api/artifacts/*/history', async route=>{await pending;await route.continue().catch(()=>{});});
  await page.route('**/ui-api/reports/*/output/**', route=>route.abort());
  try {
    await page.goto(`/p/neural-rendering/a/${report.id}`);
    await expect(page.locator('article.native-document')).toBeVisible({timeout:2000});
    await expect(page.locator('article.native-document')).toContainText('report');
    // The saved preview/descriptor render even when their revalidation cannot complete.
    await page.route('**/v1/resolutions', route=>route.abort());
    await page.route(/\/ui-api\/reports\/[^/]+\/(preview|document)\.json$/, route=>route.abort());
    await page.reload();
    await expect(page.locator('article.native-document')).toBeVisible({timeout:2000});
  } finally {release();}
});

test('finished report polling stops and metadata refresh does not re-render prose', async ({page,request}) => {
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
  let checks=0, description='Original description';
  await page.clock.install();
  await page.addInitScript(() => {
    (window as any).reportHtmlParses=0;
    const parse=DOMParser.prototype.parseFromString;
    DOMParser.prototype.parseFromString=function(...args:Parameters<typeof parse>){(window as any).reportHtmlParses++;return parse.apply(this,args);};
  });
  await page.route('**/v1/resolutions', route => {checks++;return route.fulfill({json:[{...report,annotations:{...report.annotations,description}}]});});
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  await expect(page.locator('article.native-document')).toBeVisible();
  const parses=await page.evaluate(()=>(window as any).reportHtmlParses);
  expect(parses).toBeGreaterThan(0);
  const initial=checks;
  await page.clock.fastForward(16000);
  expect(checks).toBe(initial);
  description='Refreshed description';
  await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await expect(page.locator('.hero')).toContainText(description);
  expect(checks).toBe(initial+1);
  expect(await page.evaluate(()=>(window as any).reportHtmlParses)).toBe(parses);
});

test('live artifact keeps polling until it finishes', async ({page,request}) => {
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
  let checks=0;
  await page.clock.install();
  await page.route('**/v1/resolutions', route => {checks++;return route.fulfill({json:[{...report,state:checks<3?'live':'finished'}]});});
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  await expect(page.locator('article.native-document')).toBeVisible();
  expect(checks).toBe(1);
  await page.clock.fastForward(5100);
  await expect.poll(()=>checks).toBe(2);
  await page.clock.fastForward(5100);
  await expect.poll(()=>checks).toBe(3);
  await expect(page.getByText('This artifact is live. Its ID stays the same when it finishes.',{exact:true})).toHaveCount(0);
  await page.clock.fastForward(16000);
  expect(checks).toBe(3);
});

test('v2 reports load document and output assets directly without a preview conversion', async ({page,request}) => {
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const report=items.find((a:any)=>a.manifest?.value?.data?.document_version===2);
  expect(report).toBeTruthy();
  const paths:string[]=[];
  page.on('request',r=>{paths.push(new URL(r.url()).pathname);});
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  await expect(page.locator('article.native-document')).toBeVisible();
  const image=page.getByRole('button',{name:'Enlarge Python cell image output',exact:true}).first().locator('img');
  await image.scrollIntoViewIfNeeded();
  await expect.poll(()=>image.evaluate((img:HTMLImageElement)=>img.naturalWidth)).toBeGreaterThan(0);
  await expect(image).toHaveAttribute('src',new RegExp(`/ui-api/reports/${report.id}/assets/`));
  expect(paths).toContain(`/ui-api/reports/${report.id}/document.json`);
  expect(paths.some(p=>p.endsWith('/preview.json')||p.endsWith('/executed.ipynb'))).toBe(false);
});

test('legacy v1 inline report output remains readable', async ({page,request}) => {
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const report=items[0],value=report.manifest.value;
  const source={path:'legacy.qmd',line_start:1,line_end:1};
  await page.route('**/v1/resolutions',route=>route.fulfill({json:[{...report,manifest:{...report.manifest,value:{...value,data:{...value.data,document_version:1}}}}]}));
  await page.route('**/preview.json',route=>route.fulfill({json:{format:'cairn/document',version:1,title:'Legacy report',labels:{},blocks:[{id:'legacy',kind:'code',source,options:{echo:false},outputs:[{output_type:'stream',text:'Legacy printed output'},{output_type:'display_data',data:{'image/png':'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='}}]}]}}));
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  await expect(page.locator('article.native-document')).toContainText('Legacy printed output');
  const image=page.getByRole('button',{name:'Enlarge Python cell image output',exact:true}).locator('img');
  await image.scrollIntoViewIfNeeded();
  await expect.poll(()=>image.evaluate((img:HTMLImageElement)=>img.naturalWidth)).toBe(1);
});

test('full paragraph selection keeps actions beside selected text in the comment column',async({page,request})=>{
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  const paragraph=page.locator('.native-document p').filter({hasText:'This is a working tour'}).first();
  await paragraph.scrollIntoViewIfNeeded();
  // Include the next block boundary, as a browser's full-line selection can do.
  await paragraph.evaluate(node=>{const range=document.createRange();range.selectNodeContents(node);if(node.nextSibling)range.setEnd(node.nextSibling,0);const selection=window.getSelection()!;selection.removeAllRanges();selection.addRange(range);});
  const actions=page.locator('.selection-actions');
  await expect(actions).toBeVisible();
  const p=(await paragraph.boundingBox())!,a=(await actions.boundingBox())!;
  expect(a.y).toBeGreaterThanOrEqual(p.y-2);expect(a.y).toBeLessThanOrEqual(p.y+p.height+2);
  const x=await page.locator('article.native-document').evaluate(node=>Math.min(innerWidth-52,node.getBoundingClientRect().right+8));
  expect(Math.abs((await actions.getByRole('button',{name:'Comment on selected text'}).boundingBox())!.x-x)).toBeLessThan(1);
});

test('Plotly comments do not highlight chart text',async({page,request})=>{
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
  let comments:any[]=[];
  await page.route(new RegExp(`/ui-api/reports/${report.id}/comments(?:/[^/]+)?$`),async route=>{
    const method=route.request().method();
    if(method==='POST') {const comment={...route.request().postDataJSON(),id:'plotly-highlight-test',artifact_id:report.id};comments=[comment];return route.fulfill({json:comment});}
    if(method==='DELETE'){comments=[];return route.fulfill({json:{}});}
    return route.fulfill({json:comments});
  });
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  const shell=page.locator('.native-output-shell').filter({has:page.locator('.native-plotly-card')}).first();
  await shell.scrollIntoViewIfNeeded();
  await shell.getByRole('button',{name:'Comment on Plotly figure',exact:true}).click();
  const editor=page.getByRole('group',{name:'Add report comment'});
  await editor.getByLabel('Comment',{exact:true}).fill('Chart annotation only');
  await expect(editor.getByLabel('Saved',{exact:true})).toBeVisible();
  expect(await page.evaluate(()=>CSS.highlights.get('cairn-comments')?.size||0)).toBe(0);
  await editor.getByRole('button',{name:'Delete comment'}).click();
  await expect(editor).toHaveCount(0);
});

test('code annotations preserve syntax foreground colors and a dark background',async({page,request})=>{
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  const code=page.getByLabel('python code',{exact:true}).filter({hasText:'def summarize'}).locator('code');
  await code.scrollIntoViewIfNeeded();
  await code.evaluate(node=>{const range=document.createRange();range.selectNodeContents(node);const selection=window.getSelection()!;selection.removeAllRanges();selection.addRange(range);});
  await page.getByRole('button',{name:'Comment on selected text',exact:true}).click();
  const editor=page.getByRole('group',{name:'Add report comment'});
  await editor.getByLabel('Comment',{exact:true}).fill('Code palette check');
  await expect(editor.getByLabel('Saved',{exact:true})).toBeVisible();
  const colors=await code.locator('.hljs-keyword').first().evaluate(node=>({normal:getComputedStyle(node).color,highlight:getComputedStyle(node,'::highlight(cairn-comments)').color,background:getComputedStyle(node,'::highlight(cairn-comments)').backgroundColor}));
  expect(colors.highlight).toBe(colors.normal);
  expect(colors.background).toBe('rgb(41, 71, 57)');
  await editor.getByRole('button',{name:'Delete comment'}).click();
  await expect(editor).toHaveCount(0);
});
