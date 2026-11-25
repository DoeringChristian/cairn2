import schema from './schema';

/** Cairn's protocol, as cairn.proto defines it; tools/ui_schema.py writes the schema. */
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
/** What a value has besides JSON: content, references and bytes. */
export class Content {
  constructor(public digest: string, public size: number, public media_type: string) {}
}
export class Ref {
  constructor(public artifact: string) {}
}
export class Bytes {
  constructor(public data: Uint8Array) {}
  toJSON() { return `${this.data.length} bytes`; }
}
type Field = [number: number, name: string, kind: string, message: string, count: 'one' | 'many' | 'map'];
const messages = new Map(Object.entries(schema as unknown as Record<string, Field[]>).map(([name, fields]) => [name, new Map(fields.map(field => [field[0], field]))]));
const utf8 = new TextDecoder(), encoder = new TextEncoder();
const hex = (bytes: Uint8Array) => Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');

class Reader {
  private view: DataView;
  constructor(private data: Uint8Array, public at = 0, public end = data.length) { this.view = new DataView(data.buffer, data.byteOffset); }
  /** Low and high 32 bits; numbers beyond 2^53 lose their last digits. */
  varint(): [number, number] {
    let low = 0, high = 0, shift = 0, byte;
    do {
      byte = this.data[this.at++];
      if (shift < 28) low |= (byte & 127) << shift;
      else if (shift === 28) { low |= (byte & 15) << 28; high |= (byte & 127) >> 4; }
      else high |= (byte & 127) << (shift - 32);
      shift += 7;
    } while (byte & 128);
    return [low >>> 0, high >>> 0];
  }
  unsigned() {
    const byte = this.data[this.at];
    if (byte < 128) { this.at++; return byte; }
    const [low, high] = this.varint();
    return high * 4294967296 + low;
  }
  signed() {
    const value = this.unsigned();
    return value % 2 ? -(value + 1) / 2 : value / 2;
  }
  double() { const value = this.view.getFloat64(this.at, true); this.at += 8; return value; }
  bytes() { const size = this.unsigned(), start = this.at; this.at += size; return this.data.subarray(start, this.at); }
  text() {
    const size = this.unsigned(), start = this.at, end = this.at += size;
    if (size > 24) return utf8.decode(this.data.subarray(start, end));
    let out = '';
    for (let i = start; i < end; i++) {
      const byte = this.data[i];
      if (byte > 127) return utf8.decode(this.data.subarray(start, end));
      out += String.fromCharCode(byte);
    }
    return out;
  }
  inner() { const size = this.unsigned(), start = this.at; this.at += size; return new Reader(this.data, start, this.at); }
  skip(wire: number) {
    if (wire === 0) this.varint();
    else if (wire === 1) this.at += 8;
    else if (wire === 2) this.at += this.unsigned();
    else if (wire === 5) this.at += 4;
    else throw new Error('Not a message of this protocol.');
  }
}

function value(r: Reader): unknown {
  let found: unknown = null;
  while (r.at < r.end) {
    switch (r.unsigned()) {
      case 8: r.unsigned(); found = null; break;
      case 16: found = r.unsigned() === 1; break;
      case 24: found = r.signed(); break;
      case 33: found = r.double(); break;
      case 42: found = r.text(); break;
      case 50: found = new Bytes(r.bytes().slice()); break;
      case 58: { const list: unknown[] = [], inner = r.inner(); while (inner.at < inner.end) { inner.unsigned(); list.push(value(inner.inner())); } found = list; break; }
      case 66: found = map(r.inner()); break;
      case 74: found = message('Blob', r.inner()); break;
      case 82: found = new Ref(message('Ref', r.inner()).artifact); break;
      default: throw new Error('Not a value of this protocol.');
    }
  }
  return found;
}
function map(r: Reader) {
  const found: Record<string, unknown> = {};
  while (r.at < r.end) {
    r.unsigned();
    const entry = r.inner();
    let key = '', held: unknown = null;
    while (entry.at < entry.end) if (entry.unsigned() === 10) key = entry.text(); else held = value(entry.inner());
    found[key] = held;
  }
  return found;
}
const scalar = (kind: string, r: Reader): unknown =>
  kind === 'string' ? r.text() : kind === 'bytes' ? r.bytes() : kind === 'double' ? r.double() : kind === 'bool' ? r.unsigned() === 1 : kind === 'sint64' ? r.signed() : r.unsigned();
function message(name: string, r: Reader): any {
  if (name === 'Value') return value(r);
  if (name === 'Map') return map(r);
  const fields = messages.get(name)!, found: Record<string, any> = {};
  for (const [, field, kind, inner, count] of fields.values())
    found[field] = count === 'many' ? [] : count === 'map' ? {} : kind === 'message' ? (inner === 'Map' ? {} : null) : kind === 'string' ? '' : kind === 'bytes' ? new Uint8Array() : kind === 'bool' ? false : 0;
  while (r.at < r.end) {
    const tag = r.unsigned(), field = fields.get(tag >>> 3), wire = tag & 7;
    if (!field) { r.skip(wire); continue; }
    const [, key, kind, inner, count] = field;
    if (count === 'many' && wire === 2 && !['message', 'string', 'bytes'].includes(kind)) { const packed = r.inner(); while (packed.at < packed.end) found[key].push(scalar(kind, packed)); continue; }
    const item = kind === 'message' ? message(inner, r.inner()) : scalar(kind, r);
    if (count === 'map') found[key][item.key] = item.value;
    else if (count === 'many') found[key].push(item);
    else found[key] = item;
  }
  return name === 'Blob' ? new Content(hex(found.digest), found.size, found.media_type) : found;
}
export const decode = <T,>(name: string, data: Uint8Array): T => message(name, new Reader(data));
export const decodeRecord = (data: Uint8Array) => map(new Reader(data)) as Record<string, Json>;

class Writer {
  private parts: number[] = [];
  unsigned(value: number) { while (value > 127) { this.parts.push((value % 128) | 128); value = Math.floor(value / 128); } this.parts.push(value); }
  bytes(data: Uint8Array | number[]) { this.unsigned(data.length); for (const byte of data) this.parts.push(byte); }
  double(value: number) { this.parts.push(...new Uint8Array(new Float64Array([value]).buffer)); }
  done() { return this.parts; }
}
function write(name: string, given: any): number[] {
  const w = new Writer();
  if (name === 'Value') {
    if (given === null) { w.unsigned(8); w.unsigned(1); }
    else if (typeof given === 'boolean') { w.unsigned(16); w.unsigned(given ? 1 : 0); }
    else if (Number.isSafeInteger(given) && Math.abs(given) < 2 ** 51) { w.unsigned(24); w.unsigned(given < 0 ? -2 * given - 1 : 2 * given); }
    else if (typeof given === 'number') { w.unsigned(33); w.double(given); }
    else if (Array.isArray(given)) { w.unsigned(58); w.bytes(write('List', { items: given })); }
    else if (given instanceof Content) { w.unsigned(74); w.bytes(write('Blob', { ...given, digest: given.digest.match(/../g)!.map(byte => parseInt(byte, 16)) })); }
    else if (typeof given === 'object') { w.unsigned(66); w.bytes(write('Map', given)); }
    else { w.unsigned(42); w.bytes(encoder.encode(String(given))); }
    return w.done();
  }
  if (name === 'Map') return write('Map.', { entries: Object.entries(given).map(([key, value]) => ({ key, value })) });
  for (const [number, key, kind, inner, count] of messages.get(name.replace(/\.$/, ''))!.values()) {
    if (given[key] === undefined) continue;
    for (const item of count === 'many' ? given[key] : [given[key]]) {
      if (kind === 'message') { w.unsigned(number * 8 + 2); w.bytes(write(inner, item)); }
      else if (kind === 'string' || kind === 'bytes') { w.unsigned(number * 8 + 2); w.bytes(kind === 'string' ? encoder.encode(item) : item); }
      else if (kind === 'double') { w.unsigned(number * 8 + 1); w.double(item); }
      else { w.unsigned(number * 8); w.unsigned(kind === 'bool' ? Number(item) : item); }
    }
  }
  return w.done();
}
export const encode = (name: string, given: unknown) => new Uint8Array(write(name, given));
