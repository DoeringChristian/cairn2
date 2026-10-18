import type { ButtonHTMLAttributes } from 'react';
import { Check, MessageSquare, MessageSquarePlus } from 'lucide-react';
import './CommentButton.css';

type Props = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> & {
  existing?: boolean;
  saved?: boolean;
};

/** One visual control for text, media, selection tools, and lightboxes. */
export function CommentButton({existing = false, saved = false, className = '', ...props}: Props) {
  const Icon = saved ? Check : existing ? MessageSquare : MessageSquarePlus;
  return <button type="button" aria-label={existing ? 'Open comment' : 'Add comment'} {...props} className={`comment-button ${className}`}><Icon aria-hidden="true"/></button>;
}
