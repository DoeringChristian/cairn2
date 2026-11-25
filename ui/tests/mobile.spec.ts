import {test, expect, type Page} from '@playwright/test';

async function fits(page: Page, width: number) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1);
}
    for (const viewport of [{width:320,height:568}, {width:390,height:844}, {width:768,height:1024}, {width:844,height:390}]) {
      test(`mobile navigation, lists and runs ${viewport.width}`, async ({page, request}) => {
        await page.setViewportSize(viewport);
        const {items} = await (await request.get('/v1/artifacts?project=neural-rendering')).json();
        const run = items.find((item:any) => item.namespace === 'runs');
        await page.goto('/');
        await expect(page.getByRole('heading', {name:'Projects',exact:true})).toBeVisible();
        await fits(page,viewport.width);
        if (viewport.width <= 760) {
          await page.getByRole('button',{name:'Browse',exact:true}).tap();
          await page.locator('.rail').getByRole('button',{name:'Project',exact:true}).tap();
          await page.getByRole('option',{name:/neural-rendering/}).tap();
          await expect(page.getByRole('button',{name:'Browse',exact:true})).toHaveAttribute('aria-expanded','false');
        }
        await page.goto('/p/neural-rendering?view=runs');
        await expect(page.locator('.artifact-list tr').nth(1)).toBeVisible();
        await fits(page,viewport.width);
        if (viewport.width <= 600) await expect(page.locator('.artifact-list tbody tr').first()).toHaveCSS('display','grid');
        await page.goto(`/p/neural-rendering/a/${run.id}`);
        await expect(page.getByRole('heading',{name:'Metric trends',exact:true})).toBeVisible();
        await fits(page,viewport.width);
        for (const tab of ['Records','Files','Lineage','Metadata']) {
          await page.getByRole('tab',{name:tab,exact:true}).tap();
          await fits(page,viewport.width);
        }
      });
    }
