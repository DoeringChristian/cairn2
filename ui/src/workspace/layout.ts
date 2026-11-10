import { newCard } from './cards';
import { uid, type Card, type Layout } from './model';

export function defaultLayout(example = false): Layout {
  const sections = [
    { id: 'curves', title: 'Learning curves', collapsed: false },
    { id: 'images', title: 'Visual progress', collapsed: false },
    { id: 'figures', title: 'Interactive figures', collapsed: false },
  ];
  const scalar = newCard('scalar', 'curves');
  const grouped = {
    ...newCard('scalar', 'curves'),
    title: 'Loss across runs',
    split: [],
    aggregation: { center: 'mean' as const, band: 'minmax' as const, showMembers: false },
  };
  const second = {
    ...newCard('scalar', 'curves'),
    title: 'Quality by camera angle',
    x: 'camera_angle',
    y: 'quality',
    marks: 'markers' as const,
    pointOrder: { field: 'camera_angle', descending: false },
  };
  const image = {
    ...newCard('image', 'images'),
    title: 'Compare reconstructions',
    split: ['@run', 'camera'],
    caption: 'camera',
    width: 6,
    columns: 3,
    height: 520,
  };
  const gallery = {
    ...newCard('image', 'images'),
    title: 'Browse by quality',
    mode: 'gallery' as const,
    pointOrder: { field: 'quality', descending: true },
    caption: 'quality',
    width: 6,
  };
  const sequence = {
    ...newCard('image', 'images'),
    title: 'Follow a camera through time',
    mode: 'sequence' as const,
    split: ['@run', 'camera'],
    axis: 'elapsed',
    caption: 'elapsed',
    width: 6,
  };
  const plotly = { ...newCard('plotly', 'figures'), title: 'Compare fitted curves', columns: 3, width: 6, height: 560 };
  const figures = {
    ...newCard('plotly', 'figures'),
    title: 'Figure gallery',
    mode: 'gallery' as const,
    width: 6,
    columns: 3,
    height: 450,
  };
  return uniformSections({
    version: 1,
    id: uid(),
    name: example ? 'Training cards demo' : 'Overview',
    search: example ? 'workspace-demo/' : '',
    versions: 'latest',
    status: '',
    selected: null,
    sections,
    cards: example ? [scalar, second, grouped, image, gallery, sequence, plotly, figures] : [scalar, image, plotly],
  });
}

export const cardWidths = [3, 4, 6, 12] as const;

export const snapWidth = (width: number) =>
  cardWidths.reduce<number>((best, value) => (Math.abs(value - width) < Math.abs(best - width) ? value : best), 6);

/** Keep the v1 layout shape. The first card determines a section's shared size. */
export function uniformSections(layout: Layout): Layout {
  const sizes = new Map<string, Pick<Card, 'width' | 'height'>>();
  return {
    ...layout,
    cards: layout.cards.map((card) => {
      let size = sizes.get(card.section);
      if (!size) {
        size = { width: snapWidth(card.width), height: card.height };
        sizes.set(card.section, size);
      }
      return card.width === size.width && card.height === size.height ? card : { ...card, ...size };
    }),
  };
}
