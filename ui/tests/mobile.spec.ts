import {test, expect, type Page} from '@playwright/test';

async function fits(page: Page, width: number) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1);
}
    for (const viewport of [{width:320,height:568}, {width:390,height:844}, {width:768,height:1024}, {width:844,height:390}]) {
      test(`mobile navigation, lists, reports and lightbox ${viewport.width}`, async ({page, request}) => {
        await page.setViewportSize(viewport);
        const {items} = await (await request.get('/v1/artifacts?project=neural-rendering')).json();
        const report = items.find((item:any) => item.namespace === 'reports' && item.name === 'report-field-guide');
        const run = items.find((item:any) => item.namespace === 'runs');
        await page.goto('/');
        await expect(page.getByRole('heading', {name:'Projects',exact:true})).toBeVisible();
        await fits(page,viewport.width);
        if (viewport.width <= 760) {
          await page.getByRole('button',{name:'Browse',exact:true}).tap();
          await expect(page.getByRole('link',{name:'All projects',exact:true})).toBeVisible();
          await page.locator('.rail').getByRole('link',{name:'neural-rendering',exact:true}).tap();
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
        await page.goto(`/p/neural-rendering/a/${report.id}`);
        await expect(page.locator('article.native-document')).toBeVisible();
        await fits(page,viewport.width);
        if (viewport.width <= 760) {
          const toc = page.getByRole('navigation',{name:'Table of contents'});
          await toc.getByRole('button').tap();
          await toc.getByRole('link').first().tap();
          await expect(toc.getByRole('button')).toHaveAttribute('aria-expanded','false');
        }
        await page.getByRole('button',{name:'Enlarge video',exact:true}).tap();
        const dialog = page.getByRole('dialog');
        await expect(dialog).toBeVisible();
        await dialog.getByRole('button',{name:'Zoom in',exact:true}).tap();
        await expect(dialog.getByLabel('Zoom level')).toHaveText('125%');
        const controls = await dialog.locator('button').evaluateAll(buttons => buttons.filter(b=>b.getBoundingClientRect().width).map(b=>({label:b.getAttribute('aria-label'),r:b.getBoundingClientRect().toJSON()})));
        for (const {label,r} of controls) {
          expect(r.left, label || '').toBeGreaterThanOrEqual(0);
          expect(r.right, label || '').toBeLessThanOrEqual(viewport.width+1);
          expect(r.bottom, label || '').toBeLessThanOrEqual(viewport.height+1);
        }
        await dialog.getByRole('button',{name:'Comment on media',exact:true}).tap();
        await page.setViewportSize({width:viewport.width,height:330});
        const composer = page.getByRole('group',{name:'Add report comment',exact:true});
        await expect(composer).toBeVisible();
        await expect.poll(async () => (await composer.boundingBox())!.y + (await composer.boundingBox())!.height).toBeLessThanOrEqual(330);
        await composer.getByRole('button',{name:'Delete comment',exact:true}).tap();
        await page.setViewportSize(viewport);
        const close = dialog.getByRole('button',{name:'Close media viewer',exact:true});
        expect((await close.boundingBox())!.height).toBeGreaterThanOrEqual(44);
        await close.tap();
        await expect(dialog).toHaveCount(0);
        await page.getByRole('button',{name:'Enlarge Python cell image output',exact:true}).first().tap();
        await expect(dialog).toBeVisible();
        await dialog.getByRole('button',{name:'Zoom in',exact:true}).tap();
        await expect(dialog.getByLabel('Zoom level')).toHaveText('125%');
        await dialog.getByRole('button',{name:'Close media viewer',exact:true}).tap();
      });
    }
