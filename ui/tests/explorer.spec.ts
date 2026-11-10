import {fieldGuide,reportPage} from './fixtures';
import {test,expect, type Page} from '@playwright/test';
async function openTextComment(page:Page) {
  await page.evaluate(()=>window.getSelection()?.removeAllRanges());
  const paragraph=page.locator('.native-document p').filter({hasText:'This is a working tour'}).first();
  await paragraph.scrollIntoViewIfNeeded();
  await expect.poll(()=>page.evaluate(()=>CSS.highlights.get('cairn-comments')?.size||0)).toBeGreaterThan(0);
  const point=await paragraph.evaluate(node=>{const range=document.createRange();range.selectNodeContents(node);const rect=range.getClientRects()[0];return {x:rect.x+4,y:rect.y+rect.height/2};});
  await page.mouse.click(point.x,point.y);
}


test('plain artifact identities, reports, media zoom and lineage',async({page,request})=>{
  const errors:string[]=[];
  page.on('pageerror',e=>errors.push(e.message));
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=runs')).json();
  const run=items.find((a:any)=>a.name==='showcase-baseline');
  expect(run.id).toMatch(/^[0-9a-f]{32}$/);
  await page.goto(reportPage);
  await expect(page.locator('article.native-document')).toBeVisible();
  await expect(page.locator('iframe')).toHaveCount(0);
  await page.getByRole('button',{name:'Enlarge Field at the start'}).first().click();
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
  await page.getByRole('button',{name:'Show table of contents',exact:true}).click();
  const toc=page.getByRole('navigation',{name:'Table of contents'});
  await expect(toc).toBeVisible();
  if(await toc.getByRole('button').getAttribute('aria-expanded')==='false')await toc.getByRole('button').click();
  const anchor=toc.getByRole('link').nth(1), target=await anchor.getAttribute('href');
  await anchor.click();
  await expect(page).toHaveURL(new RegExp(target!+'$'));
  await expect(page.locator(target!)).toBeVisible();
  await page.goto(`/p/neural-rendering/a/${run.id}?tab=lineage`);
  await expect(page.getByRole('img',{name:'Lineage graph'})).toBeVisible();
  await expect(page.getByText('Lineage unavailable:')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('media gestures change actual geometry and copy feedback resets',async({page,context,request})=>{
  await context.grantPermissions(['clipboard-read','clipboard-write']);
  await page.goto(reportPage);
  for(const video of [false,true]){
    await page.getByRole('button',{name:video?'Enlarge video':'Enlarge Field at the start',exact:true}).first().click();
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
  await page.goto(reportPage);
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

test('comments autosave, highlight, reopen beside text, and delete after reload',async({page,context,request})=>{
  await context.grantPermissions(['clipboard-read','clipboard-write']);
  const report=await fieldGuide(request);
  await page.goto(reportPage);
  const paragraph=page.locator('.native-document p').filter({hasText:'This is a working tour'}).first();
  await paragraph.scrollIntoViewIfNeeded();
  await paragraph.evaluate(node=>{const range=document.createRange();range.selectNodeContents(node);const selection=window.getSelection()!;selection.removeAllRanges();selection.addRange(range);});
  await page.getByRole('button',{name:'Comment on selected text',exact:true}).click();
  const editor=page.getByRole('group',{name:'Add report comment'}),body=`Browser comment test ${Date.now()}`;
  await editor.getByLabel('Comment',{exact:true}).fill(body);
  await expect(editor.getByRole('button',{name:/save/i})).toHaveCount(0);
  await expect(editor.getByLabel('Saved',{exact:true})).toBeVisible({timeout:5000});
  await editor.getByLabel('Comment',{exact:true}).fill(body+' updated');
  await expect.poll(async()=>{const rows=await (await request.get(`/ui-api/reports/${report.id}/comments`)).json();return rows.filter((c:any)=>c.body===body+' updated').length;}).toBe(1);
  await expect(editor.getByRole('button',{name:/close/i})).toHaveCount(0);
  await page.getByRole('heading',{name:'Report',exact:true}).click();
  await expect(editor).toHaveCount(0);
  await expect.poll(()=>page.evaluate(()=>CSS.highlights.get('cairn-comments')?.size||0)).toBeGreaterThan(0);
  await expect(page.locator('.report-comments')).toHaveCount(0);
  await expect(page.locator('.report-comment-marker')).toHaveCount(0);
  await openTextComment(page);
  await expect(editor.getByLabel('Comment',{exact:true})).toHaveValue(body+' updated');
  await editor.getByLabel('Comment',{exact:true}).fill(body+' final');
  await page.getByRole('heading',{name:'Report',exact:true}).click();
  await expect(editor).toHaveCount(0);
  await expect.poll(async()=>{const rows=await (await request.get(`/ui-api/reports/${report.id}/comments`)).json();return rows.some((c:any)=>c.body===body+' final');}).toBe(true);
  await page.reload();
  await page.locator('.native-document p').filter({hasText:'This is a working tour'}).first().scrollIntoViewIfNeeded();
  await expect.poll(()=>page.evaluate(()=>CSS.highlights.get('cairn-comments')?.size||0)).toBeGreaterThan(0);
  await openTextComment(page);
  await expect(editor.getByLabel('Comment',{exact:true})).toHaveValue(body+' final');
  await editor.getByRole('button',{name:'Delete comment'}).click();await expect(editor).toHaveCount(0);
  await expect.poll(async()=>{const rows=await (await request.get(`/ui-api/reports/${report.id}/comments`)).json();return rows.some((c:any)=>c.body===body+' final');}).toBe(false);
});

test('Enter sends immutable messages; leaving saves root and reply drafts',async({page,request})=>{
  const report=await fieldGuide(request);
  const rows=async()=>(await (await request.get(`/ui-api/reports/${report.id}/comments`)).json()) as any[];
  await page.goto(reportPage);
  const paragraph=page.locator('.native-document p').filter({hasText:'This is a working tour'}).first();
  await paragraph.scrollIntoViewIfNeeded();
  await paragraph.evaluate(node=>{const range=document.createRange();range.selectNodeContents(node);const selection=window.getSelection()!;selection.removeAllRanges();selection.addRange(range);});
  await page.getByRole('button',{name:'Comment on selected text',exact:true}).click();
  const editor=page.getByRole('group',{name:'Add report comment'}),field=editor.getByLabel('Comment',{exact:true}),body=`Agent comment test ${Date.now()}`;
  let rootId:string|undefined;
  try {
    await field.fill(body);
    // Click away immediately, before the autosave timer.
    await page.getByRole('heading',{name:'Report',exact:true}).click();
    await expect(editor).toHaveCount(0);
    const root=(await rows()).find(c=>c.body===body);rootId=root.id;
    expect(root.sent_at).toBeNull();
    await openTextComment(page);
    await expect(field).toHaveValue(body);
    await field.evaluate((node:HTMLTextAreaElement)=>node.setSelectionRange(node.value.length,node.value.length));await field.press('Shift+Enter');await field.pressSequentially('Second line');
    await field.press('Enter');
    await expect(editor.getByRole('log')).toContainText(body+'\nSecond line');
    await expect(field).toHaveValue('');
    await expect.poll(async()=>(await rows()).find(c=>c.id===rootId)?.sent_at).toBeTruthy();
    await expect(editor.locator('textarea')).toHaveCount(1);
    await field.fill('A saved follow-up');
    await page.getByRole('heading',{name:'Report',exact:true}).click();
    await expect(editor).toHaveCount(0);
    const reply=(await rows()).find(c=>c.reply_to===rootId);
    expect(reply.body).toBe('A saved follow-up');expect(reply.sent_at).toBeNull();
    await openTextComment(page);
    await expect(field).toHaveValue('A saved follow-up');
    await field.fill('An adjusted follow-up');await field.press('Enter');
    await expect(editor.getByRole('log')).toContainText('An adjusted follow-up');
    await expect(field).toHaveValue('');
    const after=await rows();
    expect(after.filter(c=>c.reply_to===rootId)).toHaveLength(1);
    expect(after.find(c=>c.id===reply.id).sent_at).toBeTruthy();
    expect(after.find(c=>c.id===rootId).body).toBe(body+'\nSecond line');
    await editor.getByRole('button',{name:'Delete comment'}).click();await expect(editor).toHaveCount(0);
    await expect.poll(async()=>(await rows()).some(c=>c.id===rootId||c.reply_to===rootId)).toBe(false);
  } finally {if(rootId)await request.delete(`/ui-api/reports/${report.id}/comments/${rootId}`);}
});

test('comments resolve, hide, show on request and reopen',async({page,request})=>{
  const report=await fieldGuide(request);
  const body=`Resolve check ${Date.now()}`;
  const comment=await (await request.post(`/ui-api/reports/${report.id}/comments`,{data:{anchor:{quote:'This is a working tour'},body,author:'reviewer'}})).json();
  const state=async()=>(await (await request.get(`/ui-api/reports/${report.id}/comments`)).json()).find((c:any)=>c.id===comment.id)?.resolved_at??null;
  try{
    await page.goto(reportPage);
    const editor=page.getByRole('group',{name:'Add report comment'});
    await page.locator('.native-document p').filter({hasText:'This is a working tour'}).first().scrollIntoViewIfNeeded();
    await openTextComment(page);
    await expect(editor).toContainText('reviewer');
    await editor.getByRole('button',{name:'Resolve comment'}).click();
    await expect(editor).toHaveCount(0);await expect(page.locator('.report-comment-marker')).toHaveCount(0);
    await expect.poll(state).not.toBeNull();
    await page.getByRole('button',{name:'Show resolved comments'}).click();
    await expect.poll(()=>page.evaluate(()=>CSS.highlights.get('cairn-comments')?.size||0)).toBeGreaterThan(0);
    await openTextComment(page);await expect(editor).toContainText('Resolved');
    await editor.getByRole('button',{name:'Reopen comment'}).click();
    await expect.poll(state).toBeNull();
  }finally{await request.delete(`/ui-api/reports/${report.id}/comments/${comment.id}`);}
});

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

test('media comments keep region and time and empty edits delete',async({page,request})=>{
  const report=await fieldGuide(request);
  await page.goto(reportPage);
  for(const video of [false,true]){
    await page.getByRole('button',{name:video?'Enlarge video':'Enlarge Field at the start',exact:true}).first().click();
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
    const shell=page.locator('[data-comment-media]').filter({has:page.getByRole('button',{name:video?'Enlarge video':'Enlarge Field at the start',exact:true})}).first();
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

test('ordinary code fences are highlighted and remain display-only',async({page})=>{
  await page.goto(reportPage);
  const python=page.getByLabel('python code',{exact:true}).filter({hasText:'def summarize'});
  await expect(python.locator('.hljs-keyword').first()).toBeVisible();
  await expect(page.getByRole('button',{name:'Enlarge video',exact:true})).toHaveText('');
});

test('copy controls preserve full code, media comment controls sit right, tags filter runs',async({page,request,context})=>{
  await context.grantPermissions(['clipboard-read','clipboard-write']);
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering')).json();
  await page.goto(reportPage);
  const code=page.locator('.syntax-code').filter({hasText:'def summarize'}).first();
  const original=await code.locator('code').innerText();
  await code.getByRole('button',{name:'Copy code',exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(original);
  await expect(code.getByRole('status')).toHaveText('Copied');
  await expect(code.getByRole('status')).toHaveText('');
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
    await page.getByRole('heading',{name:'Report',exact:true}).click();
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
  await page.getByRole('button', {name:'Pause auto-refresh', exact:true}).click();
  await nav.getByRole('link', {name:'Artifacts', exact:true}).click();
  await expect(page.getByRole('link', {name:'cached-artifacts', exact:true})).toBeVisible();
  await nav.getByRole('link', {name:'Runs', exact:true}).click();
  await expect(page.getByRole('link', {name:'cached-runs', exact:true})).toBeVisible();
  expect(requests).toEqual(['runs','artifacts']);
  await page.reload();
  await expect(page.getByRole('link', {name:'cached-runs', exact:true})).toBeVisible();
  expect(requests).toEqual(['runs','artifacts']);
  await page.getByRole('button', {name:'Pause auto-refresh', exact:true}).click();
  await page.getByRole('button', {name:'Refresh', exact:true}).click();
  await expect.poll(() => requests.length).toBe(3);
  expect(requests.at(-1)).toBe('runs');
  await nav.getByRole('link', {name:'Artifacts', exact:true}).click();
  await expect(page.getByRole('link', {name:'cached-artifacts', exact:true})).toBeVisible();
  expect(requests).toHaveLength(3);
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

test('full paragraph selection keeps actions beside selected text',async({page,request})=>{
  await page.goto(reportPage);
  const paragraph=page.locator('.native-document p').filter({hasText:'This is a working tour'}).first();
  await paragraph.scrollIntoViewIfNeeded();
  // Include the next block boundary, as a browser's full-line selection can do.
  await paragraph.evaluate(node=>{const range=document.createRange();range.selectNodeContents(node);if(node.nextSibling)range.setEnd(node.nextSibling,0);const selection=window.getSelection()!;selection.removeAllRanges();selection.addRange(range);});
  const actions=page.locator('.selection-actions');
  await expect(actions).toBeVisible();
  const p=(await paragraph.boundingBox())!,a=(await actions.boundingBox())!;
  expect(a.y).toBeGreaterThanOrEqual(p.y);expect(a.y).toBeLessThanOrEqual(p.y+p.height+12);
  expect(a.x).toBeGreaterThanOrEqual(p.x);expect(a.x).toBeLessThan(p.x+p.width);
  await actions.getByRole('button',{name:'Comment on selected text'}).click();
  const chat=page.locator('.comment-popover');
  const before=(await chat.boundingBox())!;
  await page.evaluate(()=>scrollBy(0,80));
  await expect.poll(async()=>Math.abs((await chat.boundingBox())!.y-before.y)).toBeGreaterThan(20);
  await chat.getByRole('button',{name:'Delete comment'}).click();
});

test('code annotations preserve syntax foreground colors and a dark background',async({page,request})=>{
  await page.goto(reportPage);
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
