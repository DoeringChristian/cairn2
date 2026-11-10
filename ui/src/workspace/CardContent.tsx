import { useMemo } from 'react';
import { cardKinds } from './cards';
import { filtered, type Card, type Observation } from './model';

export function CardContent({ card, rows }: { card: Card; rows: Observation[] }) {
  const { Content } = cardKinds[card.kind];
  const records = useMemo(() => filtered(rows, card), [rows, card]);
  return <Content card={card} rows={records} />;
}
