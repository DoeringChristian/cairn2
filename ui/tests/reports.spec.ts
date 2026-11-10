import {test,expect,type APIRequestContext,type Page} from '@playwright/test';
import {reportPage} from './fixtures';

const scratch=async(request:APIRequestContext,body='# Scratch\n\nThis is a scratch paragraph.\n')=>(await request.post('/ui-api/reports',{data:{project:'neural-rendering',name:`spec-${Date.now()}-${Math.random().toString(36).slice(2,7)}`,body}})).json();
const revision=async(request:APIRequestContext,id:string)=>(await (await request.get(`/v1/reports/${id}`)).json()).parts.body.revision;

test('the project lists reports and creates one',async({page,request})=>{
  await page.goto('/p/neural-rendering');
  await expect(page.locator('.report-card').filter({hasText:'field-guide'})).toHaveAttribute('href',reportPage);
  const name=`spec-created-${Date.now()}`;
  await page.getByLabel('New report').fill(name);
  await page.getByRole('button',{name:'Create',exact:true}).click();
  await expect(page).toHaveURL(new RegExp(`/r/${name}$`));
  await expect(page.locator('article.native-document h1')).toHaveText(name);
  const created=(await (await request.get('/v1/reports?project=neural-rendering')).json()).find((r:any)=>r.name===name);
  await request.delete(`/ui-api/reports/${created.id}`);
});

test('the showcase renders artifact media and agent slots',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(reportPage);
  const image=page.getByRole('button',{name:'Enlarge Field at the start',exact:true}).locator('img');
  await expect.poll(()=>image.evaluate((img:HTMLImageElement)=>img.naturalWidth)).toBeGreaterThan(0);
  const baseline=page.getByRole('region',{name:'Agent: baseline'});
  await expect(baseline).toContainText('Did the baseline converge?');
  await expect(baseline.locator('.agent-answer')).toContainText('Converged');
  await expect(baseline.getByRole('button',{name:'Enlarge Final field',exact:true})).toBeVisible();
  await expect(page.getByRole('region',{name:'Agent: ablation'}).locator('.agent-answer')).toContainText('Drop the boundary term');
  await expect(page.locator('aside.admonition.note')).toContainText('Seeds are fixed');
  await expect(page.locator('.native-math')).toHaveCount(1);
  await expect(page.locator('.native-footnotes li')).toHaveCount(1);
  await expect(page.locator('audio')).toHaveCount(1);
  expect(errors).toEqual([]);
});

const replace=async(page:Page,text:string)=>{await page.getByLabel('Report text').focus();await page.keyboard.press('ControlOrMeta+a');await page.keyboard.insertText(text);};

test('editing autosaves the body and agent slots fill in beside it',async({page,request})=>{
  const report=await scratch(request);
  try{
    await page.goto(`/p/neural-rendering/r/${report.name}`);
    await replace(page,'# Scratch\n\nTry three seeds.\n\n::: {.agent #seeds}\nHow many seeds?\n:::\n');
    await expect.poll(()=>revision(request,report.id)).toBe(2);
    await page.getByLabel('Report text').blur();
    const slot=page.getByRole('region',{name:'Agent: seeds'});
    await expect(slot).toContainText('How many seeds?');
    await expect(slot).toContainText('Waiting for the agent.');
    await request.put(`/v1/reports/${report.id}/parts/seeds`,{data:{source:'Five, the variance is high.',author:'agent'}});
    await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await expect(slot.locator('.agent-answer')).toContainText('Five, the variance is high.');
  }finally{await request.delete(`/ui-api/reports/${report.id}`);}
});

test('the block under the cursor shows its source and completes references',async({page,request})=>{
  const report=await scratch(request);
  try{
    await page.goto(`/p/neural-rendering/r/${report.name}`);
    await page.locator('.native-document p').filter({hasText:'This is a scratch paragraph'}).click();
    await expect(page.locator('.cm-line').filter({hasText:'This is a scratch paragraph.'})).toBeVisible();
    await expect(page.locator('article.native-document h1')).toHaveText('Scratch');
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
    await expect.poll(async()=>(await (await request.get(`/v1/reports/${report.id}`)).json()).parts.body.source).toContain('![Start](cairn:runs/showcase-baseline@latest#figures/field-start.png)');
  }finally{await request.delete(`/ui-api/reports/${report.id}`);}
});

test('a click edits where it lands and delimiters close themselves',async({page,request})=>{
  const report=await scratch(request,'# Scratch\n\nThis is a **scratch** paragraph.\n');
  try{
    await page.goto(`/p/neural-rendering/r/${report.name}`);
    const point=await page.locator('.native-document strong').evaluate(node=>{const range=document.createRange(),text=node.querySelector('[data-offset]')!.firstChild!;range.setStart(text,3);range.setEnd(text,4);const rect=range.getBoundingClientRect();return {x:rect.left+1,y:rect.top+rect.height/2};});
    await page.mouse.click(point.x,point.y);
    await expect(page.locator('.cm-line').filter({hasText:'This is a **scratch** paragraph.'})).toBeVisible();
    await page.keyboard.type('X');
    await page.keyboard.press('End');await page.keyboard.type(' ( $$');
    await expect.poll(async()=>(await (await request.get(`/v1/reports/${report.id}`)).json()).parts.body.source).toBe('# Scratch\n\nThis is a **scrXatch** paragraph. ( $$$$)\n');
  }finally{await request.delete(`/ui-api/reports/${report.id}`);}
});

test('a body changed elsewhere is never overwritten',async({page,request})=>{
  const report=await scratch(request);
  try{
    await page.goto(`/p/neural-rendering/r/${report.name}`);
    await replace(page,'# Mine\n');
    await request.put(`/ui-api/reports/${report.id}/body`,{data:{source:'# Elsewhere\n',expected_revision:1}});
    await expect(page.getByText('Report part changed since it was read')).toBeVisible();
    expect((await (await request.get(`/v1/reports/${report.id}`)).json()).parts.body.source).toBe('# Elsewhere\n');
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
