import {test,expect} from '@playwright/test';

test('project switcher and category links use the selected project',async({page})=>{
  await page.goto('/p/neural-rendering?view=runs');
  await page.getByRole('button',{name:'Choose project',exact:true}).click();
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

