import type { LucideIcon } from 'lucide-react';
import { Button, type ButtonProps } from '../Button';

/** Shared compact media action; the label remains available to keyboard/screen-reader users. */
export function MediaIconButton({label,icon:Icon,...props}: Omit<ButtonProps,'children'> & {label:string;icon:LucideIcon}) {
  return <Button {...props} small className="media-icon-button" aria-label={label} title={label}><Icon size={18} strokeWidth={1.8} aria-hidden="true"/></Button>;
}
