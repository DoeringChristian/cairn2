import { baseCard, type Card } from '../model';
import { image } from './image';
import { plotly } from './plotly';
import { scalar } from './scalar';
import type { CardKind } from './types';
import { video } from './video';
import { viewer } from './viewer';

/** Every card kind, in the order of the type picker. A new kind needs only an entry here. */
export const cardKinds = { scalar, image, video, plotly, viewer } satisfies Record<string, CardKind>;

export type Kind = keyof typeof cardKinds;

export const isKind = (kind: unknown): kind is Kind => typeof kind === 'string' && Object.hasOwn(cardKinds, kind);

export const newCard = (kind: Kind, section: string): Card => ({
  ...baseCard(kind, section),
  ...cardKinds[kind].defaults,
});
