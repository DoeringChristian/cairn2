import type { Page, Route } from '@playwright/test';
import { Content, decode, encode } from '../src/protocol';

/** Cairn's answers, for pages tested without a server. */
export { Content };
export const artifact = (id: string, name: string, version: number, more: object = {}) => ({
  id, project: 'workspace-test', namespace: 'runs', name, version, finished: version > 0, completed: version > 0, records: 10,
  created_at: 1767225600, finished_at: version > 0 ? 1767225600 : 0, heard_at: Date.now() / 1000, tags: {}, aliases: [], values: {_outcome: version > 0 ? 'completed' : null}, ...more,
});
export const digest = (letter: string) => letter.repeat(64);
export const content = (letter: string, media: string) => new Content(digest(letter), 1, media);
export const asked = <T = any,>(route: Route, message: string) => decode<T>(message, new Uint8Array(route.request().postDataBuffer() || []));
export const answer = (route: Route, message: string, value: object) => route.fulfill({contentType: 'application/x-protobuf', body: Buffer.from(encode(message, value))});
export const on = (page: Page, name: string, request: string, reply: string, handler: (request: any, route: Route) => object | undefined | Promise<object | undefined>) =>
  page.route(`**/ui-api/do/${name}`, async route => {
    const value = await handler(asked(route, request), route);
    return value ? answer(route, reply, value) : undefined;
  });
const frame = (kind: number, data: Uint8Array) => { const head = Buffer.alloc(5); head[0] = kind; head.writeUInt32BE(data.length, 1); return Buffer.concat([head, data]); };
export const stream = (parts: {run: object; after: number; records: any[]; config?: any}[], end = true) => Buffer.concat([
  ...parts.flatMap(({run, after, records, config}) => {
    const all = records.map((data, i) => ({_time: 1767225600 + i, ...(i === 0 && config ? {_config: config} : {}), ...data})).slice(after);
    const pages = Array.from({length: Math.ceil(all.length / 1000)}, (_, i) => all.slice(i * 1000, (i + 1) * 1000));
    return [frame(0, encode('Artifact', run)), ...pages.map((page, i) => frame(1, encode('RecordPage', {ordinals: page.map((_, j) => after + i * 1000 + j + 1), records: page.map(record => encode('Map', record)), total: records.length})))];
  }), ...(end ? [frame(2, new Uint8Array())] : [])]);
