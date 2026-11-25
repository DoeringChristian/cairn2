/** How tall things were when they were last shown here, so that their space is there before them. */
const key = 'cairn:heights:v1';
const limit = 2000;
let heights = new Map<string, number>();
try {
  heights = new Map(JSON.parse(localStorage.getItem(key) || '[]'));
} catch {
  // Without storage, they are known until the page is left.
}
let saving: ReturnType<typeof setTimeout> | undefined;
export const heightOf = (name: string) => heights.get(name);
export function keepHeight(name: string, height: number) {
  if (height < 1 || Math.abs((heights.get(name) ?? 0) - height) < 1) return;
  heights.delete(name);
  heights.set(name, Math.round(height));
  clearTimeout(saving);
  saving = setTimeout(() => {
    while (heights.size > limit) heights.delete(heights.keys().next().value!);
    try {
      localStorage.setItem(key, JSON.stringify([...heights]));
    } catch {
      // As above.
    }
  }, 500);
}
