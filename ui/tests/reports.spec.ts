import {test,expect,type APIRequestContext,type Page} from '@playwright/test';
import {reportPage} from './fixtures';

const toDocument=async(request:APIRequestContext,markdown:string)=>JSON.stringify(await (await request.post('/ui-api/markdown',{data:{markdown}})).json());
const scratch=async(request:APIRequestContext,markdown='# Scratch\n\nThis is a scratch paragraph.\n')=>(await request.post('/ui-api/reports',{data:{project:'neural-rendering',name:`spec-${Date.now()}-${Math.random().toString(36).slice(2,7)}`,body:await toDocument(request,markdown)}})).json();
const saved=async(request:APIRequestContext,id:string)=>(await (await request.get(`/ui-api/reports/${id}/markdown`)).json()).markdown;
const replace=async(page:Page,text:string)=>{await page.getByLabel('Report text').focus();await page.keyboard.press('ControlOrMeta+a');await page.keyboard.insertText(text);};
const line=(page:Page,text:string|RegExp)=>page.locator('.cm-line').filter({hasText:text});
const toEnd=async(page:Page)=>{await page.getByLabel('Report text').focus();await page.keyboard.press('ControlOrMeta+End');};

test('the project lists reports and creates one from a template',async({page,request})=>{
  await page.goto('/p/neural-rendering');
  await expect(page.locator('.report-card').filter({hasText:'field-guide'})).toHaveAttribute('href',reportPage);
  const name=`spec-created-${Date.now()}`;
  await page.getByLabel('New report').fill(name);
  await page.getByRole('button',{name:'Start from',exact:true}).click();
  await page.getByRole('listbox',{name:'Start from',exact:true}).getByText('Research direction',{exact:true}).click();
  await page.getByRole('button',{name:'Create',exact:true}).click();
  try{
    await expect(page).toHaveURL(new RegExp(`/r/${name}$`));
    await expect(page.locator('article.native-document h2').first()).toHaveText('Abstract');
    await expect(page.getByLabel('Message the agent about baseline')).toHaveAttribute('placeholder',/Train the baseline/);
  }finally{
    const created=(await (await request.get('/v1/reports?project=neural-rendering')).json()).find((r:any)=>r.name===name);
    if(created)await request.delete(`/ui-api/reports/${created.id}`);
  }
});

test('the showcase renders artifact media and agent blocks',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(reportPage);
  const image=page.getByRole('button',{name:'Enlarge Field at the start',exact:true}).locator('img');
  await expect.poll(()=>image.evaluate((img:HTMLImageElement)=>img.naturalWidth)).toBeGreaterThan(0);
  const baseline=page.getByRole('region',{name:'Agent: baseline'});
  await expect(baseline.locator('.agent-request')).toContainText('Did the baseline converge?');
  await expect(baseline.locator('.agent-answer')).toContainText('Converged');
  await expect(baseline.getByRole('button',{name:'Enlarge Final field',exact:true})).toBeVisible();
  await expect(page.getByRole('region',{name:'Agent: ablation'}).locator('.agent-answer')).toContainText('Drop the boundary term');
  await expect(page.locator('aside.admonition.note').filter({hasText:'Seeds are fixed'})).toBeVisible();
  await expect(page.locator('.native-math')).toHaveCount(1);
  await expect(page.locator('audio')).toHaveCount(1);
  expect(errors).toEqual([]);
});

test('@agent completes, takes a request on its line, and Enter places the block',async({page,request})=>{
  const report=await scratch(request);
  try{
    await page.goto(`/p/neural-rendering/r/${report.name}`);
    await toEnd(page);
    await page.keyboard.press('Enter');await page.keyboard.press('Enter');
    await page.keyboard.type('@ag');
    await page.getByRole('option',{name:/@agent/}).click();
    await page.keyboard.type('How many seeds?');
    await page.keyboard.press('Enter');
    await expect.poll(async()=>(await (await request.get(`/v1/reports/${report.id}/comments`)).json()).map((c:any)=>[c.body,!!c.sent_at])).toEqual([['How many seeds?',true]]);
    const slot=(await (await request.get(`/v1/reports/${report.id}/comments`)).json())[0].anchor.path;
    const block=page.getByRole('region',{name:`Agent: ${slot}`});
    await expect(block.locator('.agent-request')).toContainText('How many seeds?');
    await expect(block).toContainText('Waiting for the agent');
    await expect.poll(()=>saved(request,report.id)).toContain(`<?cairn agent ${slot}?>`);
    await request.put(`/v1/reports/${report.id}/parts/${slot}`,{data:{source:'Five, the variance is high.',author:'agent'}});
    await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await expect(block.locator('.agent-answer')).toContainText('Five, the variance is high.');
    // Discussing puts the cursor in the message box.
    await block.getByText('Discuss').click();
    await expect(block.getByLabel(`Message the agent about ${slot}`)).toBeFocused();
    // Without a request, Enter opens the new block's message box.
    await toEnd(page);
    await page.keyboard.press('Enter');await page.keyboard.press('Enter');
    await page.keyboard.type('@agent');
    await page.keyboard.press('Enter');
    await expect(page.getByLabel(/^Message the agent about agent-/).and(page.locator(':focus'))).toBeVisible();
  }finally{await request.delete(`/ui-api/reports/${report.id}`);}
});

test('the line being edited shows its Markdown, and within it only the element under the cursor',async({page,request})=>{
  const report=await scratch(request,'## Plan\n\nEnergy $E=mc^2$ and **bold** here.\n\nOther.\n');
  try{
    await page.goto(`/p/neural-rendering/r/${report.name}`);
    await expect(page.locator('article.native-document h2')).toHaveText('Plan');
    await expect(line(page,'## Plan')).toHaveCount(0);
    await page.locator('article.native-document h2').click();
    await expect(line(page,'## Plan')).toBeVisible();
    await page.locator('.native-document p').filter({hasText:'Energy'}).click();
    const energy=line(page,'Energy');
    await expect(energy.locator('.md-math-rendered')).toHaveCount(1);
    await expect(energy).toHaveText(/^Energy .* and bold here\.$/);
    await page.keyboard.press('Home');
    for(let i=0;i<7;i++)await page.keyboard.press('ArrowRight');
    await expect(energy).toContainText('$E=mc^2$');
    for(let i=0;i<8;i++)await page.keyboard.press('ArrowRight');
    await page.keyboard.press('Backspace');
    await expect(energy).toContainText('Energy $E=mc^2 and');
    await page.keyboard.type('$');
    await expect.poll(()=>saved(request,report.id)).toContain('Energy $E=mc^2$ and **bold** here.');
    await page.keyboard.press('End');
    await expect(energy).toContainText('and bold here.');
    for(let i=0;i<6;i++)await page.keyboard.press('ArrowLeft');
    await expect(energy).toContainText('and **bold** here.');
  }finally{await request.delete(`/ui-api/reports/${report.id}`);}
});

test('in a block being edited, only the cursor line shows its Markdown',async({page,request})=>{
  const report=await scratch(request,'- Train the **baseline** for 60 steps.\n- Compare the field at the start and the end.\n  1. Look for ringing near the boundary.\n  2. Check the energy $E = \\frac{1}{2} \\int |\\nabla u|^2 \\, dx$.\n');
  try{
    await page.goto(`/p/neural-rendering/r/${report.name}`);
    await page.locator('.native-document li').filter({hasText:'Check the energy'}).last().click();
    const last=line(page,'Check the energy');
    await expect(last).toBeVisible();
    await expect(last).toContainText('2. Check the energy');
    const train=line(page,'Train the');
    await expect(train).toHaveText('• Train the baseline for 60 steps.');
    await expect(line(page,'Compare the field')).toHaveText('• Compare the field at the start and the end.');
    await page.keyboard.press('ArrowUp');
    await expect(last.locator('.md-math-rendered')).toHaveCount(1);
    await expect(line(page,'Look for ringing')).toContainText('1. Look for ringing');
    await expect(train).toHaveText('• Train the baseline for 60 steps.');
    // A rendered bullet or formula leaves each line one line tall: no blank space opens around it.
    const height=async(text:string)=>(await line(page,text).boundingBox())!.height;
    expect(await height('Train the')).toBeLessThan(40);
    expect(await height('Check the energy')).toBeLessThan(60);
    await page.screenshot({path:test.info().outputPath('lines.png')});
  }finally{await request.delete(`/ui-api/reports/${report.id}`);}
});

test('a click edits where it lands and delimiters close themselves',async({page,request})=>{
  const report=await scratch(request,'# Scratch\n\nThis is a **scratch** paragraph.\n');
  try{
    await page.goto(`/p/neural-rendering/r/${report.name}`);
    const point=await page.locator('.native-document strong').evaluate(node=>{const range=document.createRange(),text=node.querySelector('[data-offset]')!.firstChild!;range.setStart(text,3);range.setEnd(text,4);const rect=range.getBoundingClientRect();return {x:rect.left+1,y:rect.top+rect.height/2};});
    await page.mouse.click(point.x,point.y);
    await expect(line(page,'This is a **scratch** paragraph.')).toBeVisible();
    await page.keyboard.type('X');
    await page.keyboard.press('End');await page.keyboard.type(' ( $$');
    await expect(line(page,'This is a scrXatch paragraph. ( $$$$)')).toBeVisible();
    await expect.poll(()=>saved(request,report.id)).toContain('This is a **scrXatch** paragraph.');
  }finally{await request.delete(`/ui-api/reports/${report.id}`);}
});

test('the block under the cursor completes references into figures',async({page,request})=>{
  const report=await scratch(request);
  try{
    await page.goto(`/p/neural-rendering/r/${report.name}`);
    await toEnd(page);
    await page.keyboard.press('Enter');await page.keyboard.press('Enter');
    await page.keyboard.type('![');
    await page.getByRole('option',{name:/cairn:/}).click();
    await page.keyboard.type('Start');await page.keyboard.press('Tab');
    await page.keyboard.type('showcase-base');
    await page.getByRole('option',{name:'runs/showcase-baseline'}).click();
    await page.keyboard.type('#figures/field-s');
    await page.getByRole('option',{name:'figures/field-start.png'}).click();
    await page.getByLabel('Report text').blur();
    await expect(page.getByRole('button',{name:'Enlarge Start',exact:true})).toBeVisible();
    await expect.poll(()=>saved(request,report.id),{timeout:10000}).toContain('![Start](cairn:runs/showcase-baseline@latest#figures/field-start.png)');
  }finally{await request.delete(`/ui-api/reports/${report.id}`);}
});

test('typing over an agent block keeps it; its own control removes it',async({page,request})=>{
  const report=await scratch(request,'# Scratch\n\n<?cairn agent kept?>\n\nAfter.\n');
  try{
    await page.goto(`/p/neural-rendering/r/${report.name}`);
    await replace(page,'gone');
    await expect(page.getByRole('region',{name:'Agent: kept'})).toBeVisible();
    await page.getByRole('region',{name:'Agent: kept'}).getByRole('button',{name:'Remove agent block',exact:true}).click();
    await expect(page.getByRole('region',{name:'Agent: kept'})).toHaveCount(0);
    await expect.poll(()=>saved(request,report.id)).not.toContain('cairn agent');
  }finally{await request.delete(`/ui-api/reports/${report.id}`);}
});

test('a body changed elsewhere is never overwritten',async({page,request})=>{
  const report=await scratch(request);
  try{
    await page.goto(`/p/neural-rendering/r/${report.name}`);
    await page.getByLabel('Report text').focus();
    await request.put(`/ui-api/reports/${report.id}/body`,{data:{source:'# Elsewhere\n',expected_revision:1}});
    await replace(page,'# Mine\n');
    await expect(page.getByText('Report part changed since it was read')).toBeVisible();
    expect(await saved(request,report.id)).toBe('# Elsewhere\n');
  }finally{await request.delete(`/ui-api/reports/${report.id}`);}
});

test('a comment on selected text is sent to the agent',async({page,request})=>{
  const report=await scratch(request);
  try{
    await page.goto(`/p/neural-rendering/r/${report.name}`);
    const paragraph=page.locator('.native-document p').filter({hasText:'This is a scratch paragraph'});
    await paragraph.evaluate(node=>{const range=document.createRange();range.selectNodeContents(node);window.getSelection()!.removeAllRanges();window.getSelection()!.addRange(range);});
    await page.getByRole('button',{name:'Comment on selected text',exact:true}).click();
    const field=page.getByRole('group',{name:'Add report comment'}).getByLabel('Comment',{exact:true});
    await field.fill('Why only one paragraph?');await field.press('Enter');
    await expect.poll(async()=>(await (await request.get(`/v1/reports/${report.id}/comments`)).json()).map((c:any)=>[c.body,!!c.sent_at,c.anchor.path])).toEqual([['Why only one paragraph?',true,'body']]);
  }finally{await request.delete(`/ui-api/reports/${report.id}`);}
});

const VIEW='The field at the start and the end, in a custom viewer';
test('a view block runs its viewer with the settings and links to the files it names',async({page})=>{
  await page.goto(reportPage);
  const viewer=page.frameLocator(`iframe[title="${VIEW}"]`);
  await expect(viewer.locator('h3')).toHaveText('Field over training');
  await expect(viewer.locator('img')).toHaveCount(2);
  await expect.poll(()=>viewer.locator('img').evaluateAll(images=>images.every(image=>(image as HTMLImageElement).naturalWidth>0))).toBe(true);
  await expect(page.locator('figcaption').filter({hasText:VIEW})).toBeVisible();
});

test('a block that keeps its clicks, such as a viewer, opens its source from its edit button',async({page,request})=>{
  const report=await scratch(request,'Before.\n\n```view\nviewer: cairn:viewers/field@latest#index.html\ntitle: Field\n```\n');
  try{
    await page.goto(`/p/neural-rendering/r/${report.name}`);
    const block=page.locator('.report-block-body').filter({has:page.locator('iframe')});
    await expect(block).toBeVisible();
    await block.getByRole('button',{name:'Edit source'}).click();
    await expect(line(page,'viewer: cairn:viewers/field@latest#index.html')).toBeVisible();
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('End');
    await page.keyboard.type(' over training');
    await expect(line(page,'title: Field over training')).toBeVisible();
  }finally{await request.delete(`/ui-api/reports/${report.id}`);}
});

test('the same view works as a workspace card',async({page})=>{
  await page.goto('/p/neural-rendering/workspace');
  await page.getByRole('button',{name:/^Add card to /}).first().click();
  const dialog=page.getByRole('dialog');
  await dialog.getByRole('button',{name:'Viewer',exact:true}).click();
  await dialog.getByRole('tab',{name:'YAML',exact:true}).click();
  await dialog.getByLabel('View YAML',{exact:true}).fill('viewer: cairn:viewers/field@latest#index.html\ntitle: Field in a card\nimages:\n  - cairn:runs/showcase-baseline@latest#figures/field-end.png\n');
  await expect(dialog.frameLocator('iframe').locator('h3')).toHaveText('Field in a card');
  await dialog.getByRole('button',{name:'Add card',exact:true}).click();
  await expect(page.getByRole('article',{name:'Viewer',exact:true}).frameLocator('iframe').locator('img')).toHaveCount(1);
});
