import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const project = 'neural-rendering';
const address = (name: string) => `/ui-api/report?${new URLSearchParams({ project, name })}`;
async function report(request: APIRequestContext, text: string) {
  const name = `test-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  expect((await request.put(address(name), { data: { text } })).ok()).toBe(true);
  return name;
}
const stored = async (request: APIRequestContext, name: string) => (await (await request.get(address(name))).json()).text as string;
const content = (page: Page) => page.locator('.report-editor .cm-content');
async function open(page: Page, name: string) {
  await page.goto(`/p/${project}/r/${name}`);
  await expect(content(page)).toBeVisible();
}
/** The visible text of a line, without what is hidden. */
const line = (page: Page, text: string) => page.locator('.cm-line').filter({ hasText: text });
test.describe.configure({ mode: 'parallel' });

test('a report is created, written, and deleted', async ({ page, request }) => {
  const name = `made-${Date.now()}`;
  await page.goto(`/p/${project}/reports`);
  await page.getByLabel('New report').fill(name);
  await page.getByRole('button', { name: 'Create', exact: true }).click();
  await expect(content(page)).toBeVisible();
  await content(page).click();
  await page.keyboard.type('# Title\nSome **bold** text\n\n- one\ntwo\n\n> [!note] Mind\nthis');
  await expect(page.locator('.md-h1')).toHaveText('Title');
  await expect(page.locator('.md-strong')).toHaveText('bold');
  await expect(page.locator('.md-marker')).toHaveCount(2);
  await expect(page.locator('.md-callout')).toHaveCount(2);
  await expect.poll(() => stored(request, name)).toBe('# Title\nSome **bold** text\n\n- one\n- two\n> [!note] Mind\n> this');
  await page.getByRole('button', { name: 'Delete report' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Reports', exact: true }).first()).toBeVisible();
  expect((await request.get(address(name))).status()).toBe(404);
});

test('marks show only where the cursor is', async ({ page, request }) => {
  const name = await report(request, '# Head\n\nplain **bold** and *soft* with $x^2$ here\n\nlast');
  await open(page, name);
  await expect(line(page, 'plain')).toHaveText(/^plain bold and soft with/);
  await expect(page.locator('.md-math math')).toHaveCount(1);
  await page.locator('.md-strong').click();
  await expect(line(page, 'plain')).toContainText('**bold** and soft');
  await page.locator('.md-em').click();
  await expect(line(page, 'plain')).toContainText('bold and *soft*');
  await expect(page.locator('.md-strong')).toHaveText('bold');
  await page.locator('.md-h1').click();
  await expect(page.locator('.md-h1')).toHaveText('# Head');
  await page.locator('.md-math').click();
  await expect(line(page, 'plain')).toContainText('$x^2$');
  await page.getByText('last', { exact: true }).click();
  await page.keyboard.press('ControlOrMeta+a');
  await page.keyboard.press('ArrowRight');
  await page.keyboard.type(' bold');
  await page.keyboard.press('Shift+Alt+ArrowLeft');
  await page.keyboard.press('ControlOrMeta+b');
  await expect.poll(() => stored(request, name)).toContain('last **bold**');
  await page.keyboard.press('ControlOrMeta+b');
  await expect.poll(() => stored(request, name)).toContain('last bold');
});

test('figures of runs are drawn, stay drawn while the cursor passes, and show their source when asked', async ({ page, request }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const name = await report(request, `before\n\n![A field](cairn:runs/showcase-baseline@v1#figures/field-start.png)\n\nthe psnr is ![psnr](cairn:runs/showcase-baseline@v1#psnr) dB, see [the run](cairn:runs/showcase-baseline@v1)\n\n![Clip](cairn:runs/showcase-baseline@v1#media/wave-evolution.mp4)\n\n![Curve](cairn:runs/showcase-baseline@v1#curve)\n\n![Rows](cairn:datasets/showcase-samples@v1#samples)\n\n![Gone](cairn:runs/showcase-baseline@v1#nothing.png)\n\n![Nobody](cairn:runs/nobody@v1#a.png)\n\nend`);
  await open(page, name);
  const picture = page.locator('.md-element img').first();
  await expect.poll(() => picture.evaluate((node: HTMLImageElement) => node.naturalWidth)).toBeGreaterThan(0);
  await expect(page.getByText('A field', { exact: true })).toBeVisible();
  await expect(page.locator('.md-value')).toHaveText('34.2');
  await page.getByText('before', { exact: true }).click();
  await page.keyboard.press('End');
  for (let i = 0; i < 4; i++) await page.keyboard.press('ArrowRight');
  await expect(picture).toBeVisible();
  await page.locator('.md-element').first().hover();
  await page.locator('.md-element').first().getByRole('button', { name: 'Edit source' }).click();
  await expect(line(page, '![A field]')).toContainText('(cairn:runs/showcase-baseline@v1#figures/field-start.png)');
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('ArrowDown');
  await expect(line(page, '![A field]')).toHaveCount(0);
  for (const [text, check] of [['Clip', 'video'], ['Curve', '.native-plotly .main-svg'], ['Rows', 'td']] as const) {
    await page.getByText(text, { exact: true }).scrollIntoViewIfNeeded().catch(() => page.mouse.wheel(0, 600));
    await expect(page.locator(`.md-element ${check}`).first()).toBeVisible();
  }
  await page.mouse.move(700, 500);
  await page.mouse.wheel(0, 3000);
  await expect(page.getByText('has no nothing.png')).toBeVisible();
  await expect(page.getByText('Nothing is called runs/nobody@v1')).toBeVisible();
  await page.evaluate(() => scrollTo(0, 0));
  await page.getByText('before', { exact: true }).click();
  await page.locator('.md-link').click();
  await expect(page).toHaveURL(/\/a\/[0-9a-f]{32}/);
  expect(errors).toEqual([]);
});

test('cards and viewers of runs are drawn from blocks', async ({ page, request }) => {
  const name = await report(request, '```card\nkind: scalar\nruns: runs/showcase-*\nx: step\ny: loss\ncaption: The loss\n```\n\n```view\nviewer: cairn:viewers/field@v1#index.html\n```\n\n```card\nkind: scalar\nrun: a\n```\n');
  await open(page, name);
  await expect(page.locator('.md-card .uplot')).toBeVisible();
  await expect(page.locator('.md-card .workspace-legend button')).toHaveCount(1);
  await expect(page.locator('iframe.native-html-viewer')).toBeVisible();
  await page.mouse.move(700, 500);
  await page.mouse.wheel(0, 2000);
  await expect(page.getByText('A card needs runs')).toBeVisible();
});

test('nothing moves while figures load', async ({ page, request }) => {
  const v = 'cairn:runs/showcase-baseline@v1#';
  const name = await report(request, `top\n\n![a](${v}figures/field-start.png)\n\nbetween\n\n![b](${v}media/wave-evolution.mp4)\n\nafter\n\n![c](${v}figures/field-end.png)\n\nend`);
  await page.setViewportSize({ width: 1200, height: 2600 });
  let release = () => {};
  const held = new Promise<void>((done) => (release = done));
  await page.route(/ui-api\/content\//, async (route) => { await held; await route.continue(); });
  await open(page, name);
  const places = () => page.evaluate(() => ['between', 'after', 'end'].map((text) => Math.round([...document.querySelectorAll('.cm-line')].find((node) => node.textContent === text)!.getBoundingClientRect().top)));
  await expect(page.locator('.md-blank')).toHaveCount(2);
  await page.waitForTimeout(500);
  const before = await places();
  release();
  await expect.poll(() => page.locator('.md-element img').evaluateAll((nodes) => nodes.filter((node) => (node as HTMLImageElement).naturalWidth > 0).length)).toBe(2);
  await expect.poll(() => page.locator('video').evaluate((node: HTMLVideoElement) => node.readyState)).toBeGreaterThan(1);
  await page.waitForTimeout(500);
  expect(await places()).toEqual(before);
});

test('tables are edited in their cells', async ({ page, request }) => {
  const name = await report(request, 'top\n\n| run | loss |\n|-----|-----:|\n| a | **0.1** |\n\nend');
  await open(page, name);
  const cell = (row: number, column: number) => page.locator('.md-table tr').nth(row).locator('th, td').nth(column);
  await expect(cell(1, 1).locator('strong')).toHaveText('0.1');
  await cell(1, 1).click();
  await expect(cell(1, 1)).toHaveText('**0.1**');
  await page.keyboard.press('End');
  await page.keyboard.type(' a|b');
  await expect.poll(() => stored(request, name)).toContain('| a | **0.1** a\\|b |');
  await page.keyboard.press('Tab');
  await page.keyboard.type('new');
  await page.keyboard.press('Tab');
  await page.keyboard.type('2');
  await expect.poll(() => stored(request, name)).toContain('| new | 2 |');
  await page.getByRole('button', { name: 'Add a column after' }).click();
  await expect(page.locator('.md-table tr').first().locator('th')).toHaveCount(3);
  await page.getByRole('button', { name: 'Remove this column' }).click();
  await expect(page.locator('.md-table tr').first().locator('th')).toHaveCount(2);
  await cell(2, 0).click();
  await page.getByRole('button', { name: 'Remove this row' }).click();
  await expect(page.locator('.md-table tr')).toHaveCount(2);
  await cell(1, 0).click();
  await page.keyboard.press('Escape');
  await page.keyboard.type('X');
  await expect.poll(() => stored(request, name)).toContain('\nX\nend');
});

test('two people and an agent write at once', async ({ page, browser, request, baseURL }) => {
  const name = await report(request, 'head\n\none\n\ntwo\n');
  await open(page, name);
  const other = await (await browser.newContext({ baseURL })).newPage();
  await open(other, name);
  await line(page, 'one').click();
  await page.keyboard.press('End');
  await line(other, 'two').click();
  await other.keyboard.press('End');
  await Promise.all([page.keyboard.type(' by anna', { delay: 10 }), other.keyboard.type(' by ben', { delay: 10 }), request.post(address(name), { data: { old: 'head', new: 'HEAD' } })]);
  for (const person of [page, other]) {
    await expect(line(person, 'by anna')).toContainText('one by anna');
    await expect(line(person, 'by ben')).toContainText('two by ben');
  }
  await expect(page.locator('.cm-ySelectionCaret')).toHaveCount(1);
  await expect.poll(() => stored(request, name)).toBe('HEAD\n\none by anna\n\ntwo by ben\n');
  // Undo takes back one's own writing only.
  await page.keyboard.press('ControlOrMeta+z');
  await expect(line(other, 'by anna')).toHaveCount(0);
  await expect(line(other, 'two')).toContainText('two by ben');
  await other.context().close();
});

test('references, files and blocks complete', async ({ page, request }) => {
  const name = await report(request, '');
  await open(page, name);
  await content(page).click();
  await page.keyboard.type('![x](cairn:runs/showc');
  await page.locator('.cm-tooltip-autocomplete li', { hasText: 'runs/showcase-baseline@v' }).first().click();
  await page.keyboard.type('#fig');
  await page.locator('.cm-tooltip-autocomplete li', { hasText: 'figures/field-end.png' }).first().click();
  await expect.poll(() => stored(request, name)).toMatch(/^!\[x\]\(cairn:runs\/showcase-baseline@v\d+#figures\/field-end\.png\)/);
});

test('the table of contents follows the text', async ({ page, request }) => {
  const name = await report(request, `# One\n\n${'text\n\n'.repeat(80)}## Two\n\nend`);
  await open(page, name);
  await page.getByRole('button', { name: 'Show table of contents' }).click();
  const toc = page.getByRole('navigation', { name: 'Table of contents' });
  await expect(toc.getByRole('link')).toHaveText(['One', 'Two']);
  await toc.getByRole('link', { name: 'Two' }).click();
  await expect(page.locator('.md-h2')).toBeInViewport();
  await request.post(address(name), { data: { old: '# One', new: '# First' } });
  await expect(toc.getByRole('link')).toHaveText(['First', 'Two']);
});
