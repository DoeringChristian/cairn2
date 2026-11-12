import {test,expect} from '@playwright/test';
import {fieldGuide,reportPage} from './fixtures';

const title='Without grids, 8DNA does not profit more from width: it flattens earlier and falls behind the grid model at large widths';
const status='Status: supported (2026-11-01), 5 assets × 5 widths × 3 lighting setups, one seed per cell. With random Fourier features, the grid-free model gains about as much from width as the grid model up to W = 256 and less beyond it.';

for(const width of [1440,900,390]){
  test(`plain findings use the reading column at ${width}px`,async({page,request})=>{
    await page.setViewportSize({width,height:900});
    const report=await fieldGuide(request);
    await page.route(`**/ui-api/reports/${report.id}/comments`,route=>route.fulfill({json:[]}));
    await page.route(`**/v1/reports/${report.id}`,async route=>{
      const response=await route.fetch(),fixture=await response.json(),finding=`::: {.finding .plain-fixture}\n**${title}**\n\n${status}\n:::\n`;

      fixture.parts.finding={...fixture.parts.body,source:finding,author:'agent'};
      await route.fulfill({json:fixture});
    });
    const finding=`::: {.finding .plain-fixture}\n**${title}**\n\n${status}\n:::\n`;
    await page.route(`**/ui-api/reports/${report.id}/markdown`,route=>route.fulfill({json:{markdown:`# Finding layout regression\n\n${finding}\n<?cairn agent finding?>\n`,revision:1}}));
    await page.goto(reportPage);
    const findings=page.locator('.plain-fixture');
    await expect(findings).toHaveCount(2);
    for(const finding of await findings.all()){
      const box=await finding.boundingBox();
      for(const paragraph of await finding.locator('p').all()){
        const text=await paragraph.boundingBox();
        expect(text!.width).toBeGreaterThanOrEqual(box!.width-2);
        expect(text!.x).toBeCloseTo(box!.x,0);
      }
    }
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await findings.first().screenshot({path:test.info().outputPath('finding.png')});
  });
}
