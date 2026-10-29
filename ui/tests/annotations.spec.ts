import {test,expect} from '@playwright/test';

test('same-label figures keep separate comments and only one editor stays visible',async({page,request})=>{
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
  // Fixture comments exercise the reader without writing into the repository.
  let comments:any[]=[];
  await page.route(`**/ui-api/reports/${report.id}/comments`,route=>route.fulfill({json:comments}));
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  const figures=page.locator('[data-comment-media="report.qmd:Plotly figure"]');
  await expect(figures).toHaveCount(2);
  const lines=await figures.evaluateAll(nodes=>nodes.map(n=>Number((n as HTMLElement).dataset.commentLine)));
  expect(lines[0]).not.toBe(lines[1]);
  comments=lines.map((line,index)=>({id:`fixture-${index}`,artifact_id:report.id,number:report.number,revision:1,body:`Figure ${index+1}`,anchor:{media_key:'report.qmd:Plotly figure',path:'report.qmd',line_start:line},for_agent:false}));
  comments.push(...Array.from({length:5},(_,i)=>({id:`reply-${i}`,reply_to:'fixture-0',artifact_id:report.id,number:report.number,revision:1,body:'Long reply\n'.repeat(12),anchor:{},for_agent:false})));
  await page.getByRole('button',{name:'Refresh',exact:true}).click();
  for(let index=0;index<2;index++){
    await figures.nth(index).scrollIntoViewIfNeeded();
    await figures.nth(index).getByRole('button',{name:'Open comment',exact:true}).click();
    const editor=page.locator('.comment-popover:visible');
    await expect(editor).toHaveCount(1);
    await expect(editor.getByLabel('Comment',{exact:true})).toHaveValue(`Figure ${index+1}`);
    const bounds=await editor.boundingBox();
    expect(bounds!.y).toBeGreaterThanOrEqual(0);
    expect(bounds!.y+bounds!.height).toBeLessThanOrEqual(page.viewportSize()!.height);
  }
  await expect(page.locator('.report-comment-marker')).toHaveCount(0);
  comments.push({id:'unplaced',artifact_id:report.id,number:report.number,revision:1,body:'Comment on a removed passage',anchor:{quote:'A passage no longer present in this report'},for_agent:false});
  await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await page.locator('.unplaced-comments summary').click();
  await page.getByRole('button',{name:'Comment on a removed passage',exact:true}).click();
  await expect(page.locator('.comment-popover:visible').getByLabel('Comment',{exact:true})).toHaveValue('Comment on a removed passage');
});

for(const viewport of [{width:1280,height:800},{width:320,height:568},{width:844,height:390}]){
  test(`image controls stay outside media and floating chat fits at ${viewport.width}`,async({page,request})=>{
    await page.setViewportSize(viewport);
    const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
    const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
    await page.route(`**/ui-api/reports/${report.id}/comments`,route=>route.fulfill({json:[]}));
    await page.goto(`/p/neural-rendering/a/${report.id}`);
    await page.getByRole('button',{name:'Enlarge Python cell image output',exact:true}).first().click();
    const dialog=page.getByRole('dialog'),media=dialog.locator('img');
    await expect.poll(()=>media.evaluate((n:HTMLImageElement)=>n.naturalWidth)).toBeGreaterThan(0);
    const assertSeparate=async()=>{
      await expect.poll(async()=>{const image=await media.boundingBox(),tools=await dialog.locator('.prism-lightbox-tools').boundingBox();return image!.y+image!.height-tools!.y;}).toBeLessThanOrEqual(1);
      for(const button of await dialog.locator('button:visible').all()){
        const box=await button.boundingBox();
        expect(box!.x).toBeGreaterThanOrEqual(0);
        expect(box!.x+box!.width).toBeLessThanOrEqual(viewport.width+1);
      }
    };
    await assertSeparate();
    await dialog.getByRole('button',{name:'Comment on media',exact:true}).click();
    const editor=dialog.getByRole('group',{name:'Add report comment'});
    await expect(editor).toBeVisible();
    await assertSeparate();
    const image=await media.boundingBox(),box=await editor.boundingBox();
    expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.y+box!.height).toBeLessThanOrEqual(viewport.height);
    await expect(editor).toHaveCSS('position','fixed');
    await dialog.getByRole('button',{name:'Zoom in',exact:true}).click();
    await expect(editor).toHaveCount(0);
    await expect(dialog.getByLabel('Zoom level')).toHaveText('125%');
  });
}

test('two images from one cell persist distinct figure anchors and reopen in lightboxes',async({page,request})=>{
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
  let comments:any[]=[];
  await page.route(`**/ui-api/reports/${report.id}/comments`,async route=>{
    if(route.request().method()==='GET')return route.fulfill({json:comments});
    const payload=route.request().postDataJSON();
    const comment={...payload,id:`fixture-${comments.length}`,artifact_id:report.id,number:report.number,revision:1};
    comments.push(comment);return route.fulfill({json:comment});
  });
  await page.route(`**/ui-api/reports/${report.id}/{document,preview}.json`,async route=>{
    const response=await route.fetch(),document=await response.json();
    const block=document.blocks.find((b:any)=>b.outputs?.some((o:any)=>Object.keys(o.data||{}).some(m=>m.startsWith('image/'))));
    const output=block.outputs.find((o:any)=>Object.keys(o.data||{}).some(m=>m.startsWith('image/')));
    block.outputs=[output,structuredClone(output)];
    await route.fulfill({json:document});
  });
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  const images=page.getByRole('button',{name:'Enlarge Python cell image output',exact:true});
  await expect(images).toHaveCount(2);
  for(let i=0;i<2;i++){
    await images.nth(i).click();
    const dialog=page.getByRole('dialog');
    await dialog.getByRole('button',{name:'Comment on media',exact:true}).click();
    const editor=dialog.getByRole('group',{name:'Add report comment'});
    await editor.getByLabel('Comment',{exact:true}).fill(`Output ${i}`);
    await expect.poll(()=>comments.length).toBe(i+1);
    await dialog.getByRole('button',{name:'Zoom in',exact:true}).click();
    await expect(editor).toHaveCount(0);
    await dialog.getByRole('button',{name:'Close media viewer'}).click();
  }
  expect(comments[0].anchor.media_key).toBe(comments[1].anchor.media_key);
  expect(comments[0].anchor.line_start).toBe(comments[1].anchor.line_start);
  expect(comments[0].anchor.media_id).toBeTruthy();
  expect(comments[0].anchor.media_id).not.toBe(comments[1].anchor.media_id);
  for(let i=0;i<2;i++){
    await images.nth(i).click();
    const dialog=page.getByRole('dialog');
    await dialog.getByRole('button',{name:'Open comment',exact:true}).click();
    await expect(dialog.getByLabel('Comment',{exact:true})).toHaveValue(`Output ${i}`);
    await dialog.getByRole('button',{name:'Zoom in',exact:true}).click();
    await dialog.getByRole('button',{name:'Close media viewer'}).click();
  }
});

test('failed sends preserve the draft and retry without duplicate messages',async({page,request})=>{
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
  const comments:any[]=[];let sends=0,creates=0,updates=0;
  await page.route(`**/ui-api/reports/${report.id}/comments**`,async route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(req.method()==='GET')return route.fulfill({json:comments});
    if(path.endsWith('/sending')){
      sends++;
      if(sends===1)return route.fulfill({status:503,json:{error:{message:'Agent queue unavailable'}}});
      comments[0].sent_at=new Date().toISOString();
      return route.fulfill({json:comments[0]});
    }
    if(req.method()==='PUT'){updates++;Object.assign(comments[0],req.postDataJSON());return route.fulfill({json:comments[0]});}
    creates++;comments.push({...req.postDataJSON(),id:'draft',artifact_id:report.id,number:report.number,revision:1,created_at:new Date().toISOString()});
    await new Promise(resolve=>setTimeout(resolve,150));
    return route.fulfill({json:comments[0]});
  });
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  const paragraph=page.locator('.native-document p').filter({hasText:'This is a working tour'}).first();
  await paragraph.scrollIntoViewIfNeeded();
  await paragraph.evaluate(node=>{const range=document.createRange();range.selectNodeContents(node);window.getSelection()!.removeAllRanges();window.getSelection()!.addRange(range);});
  await page.getByRole('button',{name:'Comment on selected text',exact:true}).click();
  const editor=page.locator('.comment-popover'),field=editor.getByLabel('Comment',{exact:true});
  await field.fill('Keep this draft');
  await field.dispatchEvent('keydown',{key:'Enter',isComposing:true});expect(sends).toBe(0);
  await field.press('Enter');
  await expect(editor.getByRole('alert')).toContainText('Agent queue unavailable');
  await expect(field).toHaveValue('Keep this draft');
  expect(comments[0].sent_at).toBeUndefined();
  await field.fill('Keep this adjusted draft');await field.press('Enter');
  await expect(editor.getByRole('log')).toContainText('Keep this adjusted draft');
  await expect(field).toHaveValue('');
  expect(creates).toBe(1);expect(updates).toBe(1);expect(sends).toBe(2);
  await page.screenshot({path:'/tmp/cairn-comment-chat.png'});
});

test('floating media chat restores and avoids a saved region',async({page,request})=>{
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
  let comments:any[]=[];
  await page.route(`**/ui-api/reports/${report.id}/comments`,route=>route.fulfill({json:comments}));
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  const image=page.getByRole('button',{name:'Enlarge Python cell image output',exact:true}).first();
  const source=await image.locator('xpath=ancestor::*[@data-comment-media][1]').evaluate(n=>({... (n as HTMLElement).dataset}));
  comments=[{id:'region-thread',artifact_id:report.id,number:report.number,revision:1,body:'Check this detail',created_at:new Date().toISOString(),sent_at:new Date().toISOString(),for_agent:true,anchor:{media_key:source.commentMedia,media_id:source.commentId,line_start:Number(source.commentLine),media:{region:{x:200,y:150,width:100,height:100,imageWidth:1000,imageHeight:800}}}}];
  await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await image.click();
  const dialog=page.getByRole('dialog');
  await dialog.getByRole('button',{name:'Open comment',exact:true}).click();
  const region=dialog.locator('.prism-region-box'),chat=dialog.locator('.comment-popover');
  await expect(region).toBeVisible();await expect(chat.getByRole('log')).toContainText('Check this detail');
  await expect.poll(async()=>{
    const a=(await region.boundingBox())!,b=(await chat.boundingBox())!;
    return Math.max(0,Math.min(a.x+a.width,b.x+b.width)-Math.max(a.x,b.x))*Math.max(0,Math.min(a.y+a.height,b.y+b.height)-Math.max(a.y,b.y));
  }).toBe(0);
  const before=(await chat.boundingBox())!;
  await page.setViewportSize({width:1100,height:700});
  await expect.poll(async()=>Math.abs((await chat.boundingBox())!.x-before.x)).toBeGreaterThan(10);
});

test('triple-click paragraph selection retains copy and comment actions',async({page,request,context})=>{
  await context.grantPermissions(['clipboard-read','clipboard-write']);
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
  await page.route(`**/ui-api/reports/${report.id}/comments`,route=>route.fulfill({json:[]}));
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  const paragraph=page.locator('.native-document p').filter({hasText:'This is a working tour'}).first();
  await paragraph.scrollIntoViewIfNeeded();await paragraph.click({clickCount:3});
  const actions=page.locator('.selection-actions');
  await expect(actions.getByRole('button',{name:'Comment on selected text'})).toBeVisible();
  await actions.getByRole('button',{name:'Copy selected text and source'}).click();
  expect(await page.evaluate(()=>navigator.clipboard.readText())).toContain('This is a working tour');
  await expect(actions).toBeVisible();
  // Browser selections can use parent-element offsets rather than text offsets.
  await paragraph.evaluate(node=>{const range=document.createRange();range.setStart(node.parentNode!,Array.from(node.parentNode!.childNodes).indexOf(node));range.setEnd(node.nextSibling!,0);const s=window.getSelection()!;s.removeAllRanges();s.addRange(range);});
  await expect(actions.getByRole('button',{name:'Comment on selected text'})).toBeVisible();
  await actions.getByRole('button',{name:'Comment on selected text'}).click();
  await expect(page.locator('.comment-popover')).toBeVisible();
});

test('closing a saving draft does not clear a newer triple-click selection',async({page,request})=>{
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
  let comments:any[]=[];
  await page.route(`**/ui-api/reports/${report.id}/comments`,async route=>{
    if(route.request().method()==='GET')return route.fulfill({json:comments});
    await new Promise(resolve=>setTimeout(resolve,500));
    comments=[{...route.request().postDataJSON(),id:'slow-draft',number:report.number,revision:1,created_at:new Date().toISOString()}];
    return route.fulfill({json:comments[0]});
  });
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  const paragraph=page.locator('.native-document p').filter({hasText:'This is a working tour'}).first();
  await paragraph.scrollIntoViewIfNeeded();await paragraph.click({clickCount:3});
  await page.getByRole('button',{name:'Comment on selected text'}).click();
  await page.getByLabel('Comment',{exact:true}).fill('A pending draft');
  await paragraph.click({clickCount:3});
  await expect(page.locator('.comment-popover')).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Comment on selected text'})).toBeVisible();
  expect(await page.evaluate(()=>window.getSelection()?.toString())).toContain('This is a working tour');
  // Selecting an already highlighted paragraph must not focus a chat mid-click.
  await page.evaluate(()=>window.getSelection()?.removeAllRanges());
  await paragraph.click({clickCount:3});
  await expect(page.getByRole('button',{name:'Comment on selected text'})).toBeVisible();
  await expect(page.locator('.comment-popover')).toHaveCount(0);
});

test('highlight clicks open immediately, stay open on repeat clicks, and allow triple-click selection',async({page,request})=>{
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
  await page.route(`**/ui-api/reports/${report.id}/comments`,route=>route.fulfill({json:[{id:'highlight-thread',artifact_id:report.id,number:report.number,revision:1,body:'Existing conversation',created_at:new Date().toISOString(),sent_at:new Date().toISOString(),for_agent:true,anchor:{quote:'This is a working tour',path:'report.qmd'}}]}));
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  const paragraph=page.locator('.native-document p').filter({hasText:'This is a working tour'}).first();
  await paragraph.scrollIntoViewIfNeeded();
  await expect.poll(()=>page.evaluate(()=>CSS.highlights.get('cairn-comments')?.size||0)).toBe(1);
  // Inspect after paint, without waiting out a delayed open timer.
  const openOnClick=()=>paragraph.evaluate(async node=>{
    window.getSelection()?.removeAllRanges();
    const range=document.createRange();range.setStart(node.firstChild!,0);range.setEnd(node.firstChild!,4);
    const rect=range.getBoundingClientRect();
    node.dispatchEvent(new MouseEvent('click',{bubbles:true,detail:1,clientX:rect.left+2,clientY:rect.top+2}));
    await new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve())));
    return !!document.querySelector('.comment-popover');
  });
  expect(await openOnClick()).toBe(true);
  await expect(page.locator('.comment-popover').getByRole('log')).toContainText('Existing conversation');
  await expect(page.getByLabel('Comment',{exact:true})).not.toBeFocused();
  expect(await openOnClick()).toBe(true);
  await page.getByRole('heading',{name:'Published report',exact:true}).click();
  await expect(page.locator('.comment-popover')).toHaveCount(0);
  // Pauses between clicks let React render, exercising focus interference.
  await paragraph.click({position:{x:12,y:12},clickCount:3,delay:75});
  await expect(page.getByRole('button',{name:'Comment on selected text'})).toBeVisible();
  await expect(page.locator('.comment-popover')).toHaveCount(0);
  expect(await page.evaluate(()=>window.getSelection()?.toString())).toContain('This is a working tour');
});
