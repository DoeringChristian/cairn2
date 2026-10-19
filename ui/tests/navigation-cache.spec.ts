import {test,expect} from '@playwright/test';

test('project switcher and category links use the selected project',async({page})=>{
  await page.goto('/p/neural-rendering?view=runs');
  const rail=page.locator('.rail');
  await expect(rail.getByRole('button',{name:'Project',exact:true})).toContainText('neural-rendering');
  await expect(rail.getByRole('link',{name:'Runs',exact:true})).toHaveAttribute('aria-current','page');
  await rail.getByRole('link',{name:'Reports',exact:true}).click();
  await expect(page).toHaveURL(/view=reports/);
  await expect(rail.getByRole('link',{name:'Reports',exact:true})).toHaveAttribute('aria-current','page');
  await rail.getByRole('button',{name:'Project',exact:true}).click();
  await page.getByRole('option',{name:'All projects',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Projects',exact:true})).toBeVisible();
});

for (const large of [false,true]) test(large?'large report stays in memory beyond the session-storage budget':'finished report document survives navigation and reload without refetching',async({page,request})=>{
  const {items}=await (await request.get('/v1/artifacts?project=neural-rendering&namespace=reports')).json();
  const report=items.filter((a:any)=>a.name==='report-field-guide').sort((a:any,b:any)=>b.number-a.number)[0];
  if(large)await page.route(/\/(document|preview)\.json$/,async route=>{
    const response=await route.fetch(),document=await response.json();
    await route.fulfill({json:{...document,testPadding:'x'.repeat(1100000)}});
  });
  let documents=0;
  page.on('request',r=>{if(/\/(document|preview)\.json$/.test(r.url()))documents++;});
  await page.goto(`/p/neural-rendering/a/${report.id}`);
  await expect(page.locator('article.native-document')).toBeVisible();
  const initial=documents;
  expect(initial).toBeGreaterThan(0);
  await page.getByRole('tab',{name:'Files',exact:true}).click();
  await page.getByRole('tab',{name:'Report',exact:true}).click();
  await expect(page.locator('article.native-document')).toBeVisible();
  if(!large){await page.reload();await expect(page.locator('article.native-document')).toBeVisible();}
  expect(documents).toBe(initial);
});
