import { useEffect, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { createPortal } from 'react-dom';
import { ActionIcon } from '../components/Button';
export function WorkspaceDialog({title,children,onClose,wide=false}: {title:string;children:ReactNode;onClose:()=>void;wide?:boolean}) {
  const ref=useRef<HTMLDialogElement>(null),close=useRef(onClose);close.current=onClose;
  useEffect(()=>{
    const previous=document.activeElement as HTMLElement|null,dialog=ref.current;
    const leave=()=>close.current();
    dialog?.showModal();
    // Same-page history navigation may keep the workspace mounted.
    window.addEventListener('popstate',leave);
    return()=>{window.removeEventListener('popstate',leave);dialog?.close();if(previous?.isConnected)previous.focus({preventScroll:true});};
  },[]);
  return createPortal(<dialog aria-label={title} ref={ref} className={`workspace-dialog ${wide?'wide':''}`} onCancel={e=>{e.preventDefault();onClose();}}><header><h2>{title}</h2><ActionIcon label="Close dialog" onClick={onClose}><X size={18}/></ActionIcon></header>{children}</dialog>,document.body);
}
