import {test,expect} from '@playwright/test';
import {artifact,on} from './cairn';

test('project switcher and category links use the selected project',async({page})=>{
  await page.goto('/p/neural-rendering?view=runs');
  await page.getByRole('button',{name:'Choose project',exact:true}).click();
  const rail=page.locator('.rail');
  await expect(rail.getByRole('button',{name:'Project',exact:true})).toContainText('neural-rendering');
  await expect(rail.getByRole('link',{name:'Runs',exact:true})).toHaveAttribute('aria-current','page');
  await rail.getByRole('link',{name:'Artifacts',exact:true}).click();
  await expect(page).toHaveURL(/view=artifacts/);
  await expect(rail.getByRole('link',{name:'Artifacts',exact:true})).toHaveAttribute('aria-current','page');
  await rail.getByRole('button',{name:'Project',exact:true}).click();
  await page.getByRole('option',{name:'All projects',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Projects',exact:true})).toBeVisible();
});


test('a reload asks the server at once, although the cached list bridges it',async({page})=>{
  let name='before';await on(page,'find','Find','Artifacts',()=>({items:[artifact('x',name,1)],total:1}));
  await page.goto('/p/neural-rendering?view=runs');await expect(page.getByText('before',{exact:true})).toBeVisible();
  name='after';await page.reload();await expect(page.getByText('after',{exact:true})).toBeVisible({timeout:3000});
});
