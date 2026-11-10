import type { ComponentType } from 'react';
import type { LucideIcon } from 'lucide-react';
import type { Card, FieldInfo, Observation } from '../model';

export type ContentProps = {
  card: Card;
  /** Records of the visible runs that pass the card's filters. */
  rows: Observation[];
};

export type SettingsProps = {
  card: Card;
  /** Fields discovered in the visible runs' records. */
  fields: FieldInfo[];
  patch: (value: Partial<Card>) => void;
};

/** One kind of workspace card. Register it in `cards/index.ts`. */
export type CardKind = {
  label: string;
  icon: LucideIcon;
  /** Settings a new card starts with, on top of `baseCard`. */
  defaults: Partial<Card>;
  /** False when the card shows no run records, so it never waits for them. */
  usesRecords: boolean;
  /** Whether the kind's own settings are complete; title and filters are checked for every card. */
  isValid: (card: Card) => boolean;
  Content: ComponentType<ContentProps>;
  Settings: ComponentType<SettingsProps>;
};
