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
  test(`image controls and editor reserve space at ${viewport.width}`,async({page,request})=>{
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
    expect(image!.y+image!.height).toBeLessThanOrEqual(box!.y+1);
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
    await editor.getByLabel('Send to agent').uncheck();
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
