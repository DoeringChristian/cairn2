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
  await page.goto(`/p/${project}`);
  await page.getByLabel('New report').fill(name);
  await page.getByRole('button', { name: 'Create', exact: true }).click();
  await expect(content(page)).toBeVisible();
  await content(page).click();
  await page.keyboard.type('# Title\nSome **bold** text\n\n- one\ntwo\n\n> [!note] Mind\nthis');
  await expect(page.locator('.md-h1')).toHaveText('Title');
  await expect(page.locator('.md-strong')).toHaveText('bold');
  await expect(page.locator('.md-bullet')).toHaveCount(2);
  await expect(page.locator('.md-callout')).toHaveCount(2);
  await expect.poll(() => stored(request, name)).toBe('# Title\nSome **bold** text\n\n- one\n- two\n> [!note] Mind\n> this');
  await page.getByRole('button', { name: 'Delete report' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Your reports' })).toBeVisible();
  expect((await request.get(address(name))).status()).toBe(404);
});

test('reports are listed as cards, and start from another', async ({ page, request }) => {
  const first = await report(request, '# Shared beginning');
  await page.goto('/');
  await page.getByRole('link', { name: new RegExp(`^\\d+ reports in ${project}$`) }).click();
  await expect(page.locator('.report-card').filter({ hasText: first })).toBeVisible();
  await page.getByLabel('New report').fill(`${first}-copy`);
  await page.getByRole('button', { name: 'Start from', exact: true }).click();
  await page.getByRole('option', { name: `Copy of ${first}`, exact: true }).click();
  await page.getByRole('button', { name: 'Create', exact: true }).click();
  await expect(page.locator('.md-h1')).toHaveText('Shared beginning');
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
  await page.locator('.md-link[data-href]').click();
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

test('tables are edited in their cells, and by their handles', async ({ page, request }) => {
  const name = await report(request, 'top\n\n| run | loss |\n|-----|-----:|\n| a | **0.1** |\n| b | 0.05 |\n\nend');
  await open(page, name);
  const cell = (row: number, column: number) => page.locator(`.pm-table-cell[data-row="${row}"][data-col="${column}"]`);
  const handle = (kind: string, index: number) => page.locator(`.pm-table-${kind}-handle[data-index="${index}"]`);
  const rows = async () => (await stored(request, name)).split('\n').filter((text) => text.startsWith('|')).map((text) => text.split(/(?<!\\)\|/).slice(1, -1).map((part) => part.trim()));
  await expect(cell(1, 1).locator('strong')).toHaveText('0.1');
  await cell(1, 1).click();
  await expect(cell(1, 1)).toHaveText('**0.1**');
  await page.keyboard.press('ControlOrMeta+a');
  await page.keyboard.type('0.2 a|b');
  await expect.poll(async () => (await rows())[2]).toEqual(['a', '0.2 a\\|b']);
  await page.keyboard.press('ControlOrMeta+a');
  await page.keyboard.type('0.2');
  await cell(2, 1).click();
  await page.keyboard.press('Tab');
  await expect(cell(3, 0)).toBeFocused();
  await page.keyboard.type('c');
  await page.keyboard.press('Tab');
  await page.keyboard.type('0.1');
  await expect.poll(rows).toEqual([['run', 'loss'], ['----', '---:'].map(() => expect.stringMatching(/^-+:?$/)), ['a', '0.2'], ['b', '0.05'], ['c', '0.1']] as any);
  await cell(0, 1).hover();
  await handle('col', 1).click();
  await page.getByRole('menuitem', { name: 'Sort ascending' }).click();
  await expect.poll(async () => (await rows()).slice(2).map((row) => row[0])).toEqual(['b', 'c', 'a']);
  await cell(1, 0).hover();
  const from = (await handle('row', 1).boundingBox())!;
  const to = (await cell(3, 0).boundingBox())!;
  await page.mouse.move(from.x + 7, from.y + 11);
  await page.mouse.down();
  await page.mouse.move(from.x + 7, to.y + to.height / 2, { steps: 5 });
  await page.mouse.up();
  await expect.poll(async () => (await rows()).slice(2).map((row) => row[0])).toEqual(['c', 'a', 'b']);
  await page.getByRole('button', { name: 'Add column' }).click();
  await expect(cell(0, 2)).toBeFocused();
  await page.keyboard.type('note');
  await expect.poll(async () => (await rows())[0]).toEqual(['run', 'loss', 'note']);
  await cell(1, 0).click({ button: 'right' });
  await page.getByRole('menuitem', { name: 'Delete row' }).click();
  await expect.poll(async () => (await rows()).slice(2).map((row) => row[0])).toEqual(['a', 'b']);
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

test('what is selected is copied as a reference an agent can use', async ({ page, context, request }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const name = await report(request, '# Head\n\nThe **loss** falls\nand stays.\n\n![A field](cairn:runs/showcase-baseline@v1#figures/field-start.png)\n\n| run | loss |\n|-----|------|\n| a | 0.1 |\n\nend');
  const where = `[cairn:reports/${name}]`;
  const copied = () => page.evaluate(() => navigator.clipboard.readText());
  await open(page, name);
  await line(page, 'The').dblclick({ position: { x: 45, y: 10 } });
  await page.locator('.md-reference button').click();
  await expect(page.locator('.md-reference button')).toHaveAttribute('data-copied');
  expect(await copied()).toBe(`${name}.md:3 "loss" ${where}`);
  await line(page, 'The').click({ position: { x: 3, y: 10 } });
  await page.keyboard.press('Shift+ArrowDown');
  await page.keyboard.press('Shift+End');
  await page.locator('.md-reference button').click();
  await expect.poll(copied).toBe(`${name}.md:3 "The **loss** falls\nand stays." ${where}`);
  // It names the passage exactly as `report edit` needs it.
  const passage = /"([^]*)" \[/.exec(await copied())![1];
  expect((await request.post(address(name), { data: { old: passage, new: 'It falls.' } })).ok()).toBe(true);
  await page.locator('.md-element').hover();
  await page.locator('.md-element').getByRole('button', { name: 'Copy reference' }).click();
  await expect.poll(copied).toBe(`${name}.md:5 "![A field](cairn:runs/showcase-baseline@v1#figures/field-start.png)" ${where}`);
  await page.locator('.pm-table-cell[data-row="1"][data-col="1"]').click({ button: 'right' });
  await page.getByRole('menuitem', { name: 'Copy reference' }).click();
  await expect.poll(copied).toBe(`${name}.md:9 "0.1" ${where}`);
  await line(page, 'It falls').click();
  await page.keyboard.press('ControlOrMeta+Alt+c');
  await expect.poll(copied).toBe(`${name}.md:3 "It falls." ${where}`);
  await page.getByRole('button', { name: 'Enlarge A field' }).click();
  await page.getByRole('button', { name: 'Copy source reference' }).click();
  await expect.poll(copied).toContain('"artifact_ref": "runs/showcase-baseline@v1#figures/field-start.png"');
  expect(await copied()).toContain(`"file": "${name}.md"`);
});

test('a click shows the marks at once, and selects nothing', async ({ page, request }) => {
  const name = await report(request, '# Head\n\nThe **bold** word\n\n```python\nprint(1)\n```\n');
  await open(page, name);
  await page.locator('.md-h1').click();
  await expect(page.locator('.md-h1')).toHaveText('# Head', { timeout: 100 });
  for (const x of [2, 12, 25, 34]) {
    await page.locator('.md-strong').click({ position: { x, y: 10 } });
    await expect(line(page, 'The')).toContainText('**bold**');
    await expect(page.locator('.md-reference')).toHaveCount(0);
    await page.locator('.md-h1').click();
  }
  await page.locator('.md-fence').first().hover();
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.getByRole('button', { name: 'Copy code' }).click();
  await expect(page.getByRole('button', { name: 'Copy code' })).toHaveAttribute('data-copied');
});

test('a value within a line has no buttons, and a click shows its source', async ({ page, request }) => {
  const name = await report(request, 'above\n\nthe psnr is ![psnr](cairn:runs/showcase-baseline@v1#psnr) dB\n\nend');
  await open(page, name);
  await page.locator('.md-value').hover();
  await expect(page.locator('.md-element button')).toHaveCount(0);
  await page.locator('.md-value').click();
  await expect(line(page, 'the psnr')).toHaveText('the psnr is ![psnr](cairn:runs/showcase-baseline@v1#psnr) dB');
  await page.getByText('end', { exact: true }).click();
  await expect(page.locator('.md-value')).toHaveText('34.2');
});

const picture = 'cairn:runs/showcase-baseline@v1#figures/field-start.png';
const other = 'cairn:runs/showcase-baseline@v1#figures/field-end.png';
const images = (page: Page) => page.locator('.md-embed img:not(.cm-widgetBuffer)');
async function drawn(page: Page, count: number) {
  await expect(images(page)).toHaveCount(count);
  await expect.poll(() => images(page).evaluateAll((nodes) => nodes.every((node) => (node as HTMLImageElement).naturalWidth > 0 && node.getBoundingClientRect().width > 20))).toBe(true);
}

test('a figure stays while the lines around it are deleted and written', async ({ page, request }) => {
  const name = await report(request, `one\n\n![A](${picture})\n\ntwo\n`);
  await open(page, name);
  await drawn(page, 1);
  // The empty line above goes, and then the text above, letter by letter.
  await page.getByText('one', { exact: true }).click();
  await page.keyboard.press('End');
  await page.keyboard.press('Delete');
  await expect.poll(() => stored(request, name)).toBe(`one\n![A](${picture})\n\ntwo\n`);
  await drawn(page, 1);
  // Delete at the end of the text does not pull the figure into it.
  await page.keyboard.press('Delete');
  for (let i = 0; i < 2; i++) await page.keyboard.press('ArrowUp');
  await page.keyboard.press('End');
  for (let i = 0; i < 3; i++) await page.keyboard.press('Backspace');
  await expect.poll(() => stored(request, name)).toBe(`\n![A](${picture})\n\ntwo\n`);
  await drawn(page, 1);
  await page.keyboard.press('Delete');
  await expect.poll(() => stored(request, name)).toBe(`![A](${picture})\n\ntwo\n`);
  await drawn(page, 1);
  // From below: the empty line goes first, and the text under it is not joined to it.
  await page.getByText('two', { exact: true }).click();
  await page.keyboard.press('ArrowUp');
  await page.keyboard.press('Backspace');
  await expect.poll(() => stored(request, name)).toBe(`![A](${picture})\ntwo\n`);
  await drawn(page, 1);
  await page.getByText('two', { exact: true }).click();
  await page.keyboard.press('ControlOrMeta+ArrowLeft');
  await page.keyboard.press('Home');
  await page.keyboard.press('Backspace');
  await expect.poll(() => stored(request, name)).toBe(`![A](${picture})\ntwo\n`);
  await drawn(page, 1);
  // The cursor is behind it now; only the next one removes it, and undo brings it back.
  await expect(page.locator('.md-media-caret')).toHaveCount(1);
  await page.keyboard.press('Backspace');
  await expect.poll(() => stored(request, name)).toBe('\ntwo\n');
  await page.keyboard.press('ControlOrMeta+z');
  await drawn(page, 1);
});

test('the cursor steps over figures, and they are selected with the text', async ({ page, request }) => {
  const name = await report(request, `one\n\n![A caption](${picture})\n\ntwo\n`);
  await open(page, name);
  await drawn(page, 1);
  await page.getByText('one', { exact: true }).click();
  await page.keyboard.press('End');
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('.md-media-caret')).toHaveCount(1);
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('.md-media-caret')).toHaveCount(1);
  // Down goes through the caption, and on.
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('textbox', { name: 'Caption' })).toBeFocused();
  await page.keyboard.type(', longer');
  await page.keyboard.press('ArrowDown');
  await expect.poll(() => stored(request, name)).toBe(`one\n\n![A caption, longer](${picture})\n\ntwo\n`);
  await page.keyboard.type('between');
  await expect.poll(() => stored(request, name)).toBe(`one\n\n![A caption, longer](${picture})\nbetween\ntwo\n`);
  await drawn(page, 1);
  await page.keyboard.press('ControlOrMeta+a');
  await expect(page.locator('.md-embed.is-selected')).toHaveCount(1);
});

async function drag(page: Page, from: ReturnType<Page['locator']>, to: ReturnType<Page['locator']>, x: number, y: number) {
  const [a, b] = [(await from.boundingBox())!, (await to.boundingBox())!];
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(a.x + a.width / 2 + 10, a.y + a.height / 2 + 10, { steps: 3 });
  await page.mouse.move(b.x + b.width * x, b.y + b.height * y, { steps: 6 });
  await page.mouse.move(b.x + b.width * x + 1, b.y + b.height * y);
  await page.mouse.up();
}
test('figures are dragged, set beside each other, and resized', async ({ page, request }) => {
  await page.setViewportSize({ width: 1280, height: 1600 });
  const name = await report(request, `one\n\n![A](${picture})\n\n![B](${other})\n\ntwo\n`);
  await open(page, name);
  await drawn(page, 2);
  const embed = (i: number) => page.locator('.md-embed').nth(i);
  await drag(page, images(page).nth(1), line(page, 'one'), 0.1, 0.2);
  await expect.poll(() => stored(request, name)).toBe(`![B](${other})\n\none\n\n![A](${picture})\n\ntwo\n`);
  await drag(page, images(page).nth(1), embed(0), 0.9, 0.5);
  await expect.poll(() => stored(request, name)).toBe(`![B](${other}) ![A](${picture})\n\none\n\ntwo\n`);
  await drawn(page, 2);
  const [left, right] = [(await embed(0).boundingBox())!, (await embed(1).boundingBox())!];
  expect(right.x).toBeGreaterThan(left.x + left.width - 1);
  expect(Math.abs(left.y - right.y)).toBeLessThan(3);
  const pull = async (i: number, dx: number) => {
    await embed(i).hover();
    const handle = (await embed(i).locator('.md-resize').boundingBox())!;
    await page.mouse.move(handle.x + 9, handle.y + 9);
    await page.mouse.down();
    await page.mouse.move(handle.x + 9 + dx, handle.y + 9, { steps: 6 });
    await page.mouse.up();
  };
  await pull(0, -120);
  await expect.poll(() => stored(request, name)).toMatch(/^!\[B\]\([^)]*\)\{width=(\d+)%\} !\[A\]\([^)]*\)\{width=(\d+)%\}/);
  const sizes = [...(await stored(request, name)).matchAll(/width=(\d+)/g)].map((match) => Number(match[1]));
  expect(sizes[0] + sizes[1]).toBe(100);
  expect(sizes[0]).toBeLessThan(45);
  await drawn(page, 2);
  await drag(page, images(page).nth(1), line(page, 'two'), 0.1, 0.8);
  await expect.poll(() => stored(request, name)).toContain(`two\n\n![A](${picture}){width=`);
  await images(page).nth(1).click({ button: 'right' });
  await page.getByRole('menuitem', { name: 'Full width' }).click();
  await expect.poll(() => stored(request, name)).toContain(`two\n\n![A](${picture})\n`);
  await images(page).nth(1).click({ button: 'right' });
  await page.getByRole('menuitem', { name: 'Half width' }).click();
  await expect.poll(() => stored(request, name)).toContain(`![A](${picture}){width=50%}`);
  const half = (await embed(1).locator('.md-sized').boundingBox())!.width / (await embed(1).boundingBox())!.width;
  expect(half).toBeCloseTo(0.5, 1);
  await images(page).nth(1).click({ button: 'right' });
  await page.getByRole('menuitem', { name: 'Remove' }).click();
  await drawn(page, 1);
});

test('captions are written where they stand', async ({ page, request }) => {
  const name = await report(request, `top\n\n![](${picture})\n\n\`\`\`card\nruns: runs/showcase-*\ny: loss\n\`\`\`\n\nend\n`);
  await open(page, name);
  await drawn(page, 1);
  await page.locator('.md-embed').hover();
  await page.locator('.md-embed .md-caption').click();
  await page.keyboard.type('New [one]');
  await page.keyboard.press('Enter');
  await expect.poll(() => stored(request, name)).toContain(`![New [one\\]](${picture})`);
  await expect(page.locator('.md-embed .md-caption')).toHaveText('New [one]');
  await page.locator('.md-block').hover();
  await page.locator('.md-block .md-caption').click();
  await page.keyboard.type('The loss');
  await page.getByText('end', { exact: true }).click();
  await expect.poll(() => stored(request, name)).toContain('caption: "The loss"\n```');
  await drawn(page, 1);
});

test('a figure right after a table is a figure', async ({ page, request }) => {
  const table = '| run | loss |\n|-----|-----:|\n| a | 0.1 |';
  const name = await report(request, `top\n\n${table}\n\n![A|B](${picture})\n\nend`);
  const rows = page.locator('.pm-table tr');
  await open(page, name);
  await drawn(page, 1);
  // Before the figure: the empty line between them goes, and then there is nothing to delete.
  await page.locator('.pm-table-cell[data-row="1"][data-col="1"]').click();
  await page.keyboard.press('Escape');
  await page.keyboard.press('Backspace');
  await expect(page.locator('.pm-table-cell[data-row="1"][data-col="1"]')).toBeFocused();
  await expect.poll(() => stored(request, name)).toMatch(/\| a +\| +0\.1 \|\n!\[A\|B\]/);
  await drawn(page, 1);
  await expect(rows).toHaveCount(2);
  await page.keyboard.press('Escape');
  await expect(page.locator('.md-media-caret')).toHaveCount(1);
  for (let i = 0; i < 3; i++) {
    await page.keyboard.press('Backspace');
    await expect(page.locator('.pm-table-cell[data-row="1"][data-col="1"]')).toBeFocused();
    await page.keyboard.press('Escape');
    await drawn(page, 1);
    await expect(rows).toHaveCount(2);
  }
  // Text above a table is not joined to it either.
  await page.getByText('top', { exact: true }).click();
  await page.keyboard.press('End');
  await page.keyboard.press('Delete');
  await page.keyboard.press('Delete');
  await expect(page.locator('.pm-table-cell[data-row="0"][data-col="0"]')).toBeFocused();
  await expect.poll(() => stored(request, name)).toMatch(/^top\n\| run/);
  await drawn(page, 1);
  await expect(rows).toHaveCount(2);
  for (const [text, count] of [[`${table}\n![A](${picture})`, 2], [`![A](${picture})\n${table}`, 2], [`> q\n![A](${picture})`, 0], [`- a\n![A](${picture})`, 0], [`# H\n![A](${picture})\n## I`, 0], ['```py\nx\n```\n' + `![A](${picture})`, 0], [`$$\nx\n$$\n![A](${picture})`, 0], [`![A](${picture})\n---\nx`, 0]] as const) {
    await request.put(address(name), { data: { text } });
    await drawn(page, 1);
    await expect(rows).toHaveCount(count);
  }
});
