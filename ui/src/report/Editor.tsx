import { useEffect, useRef, useState, type MouseEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { EditorState, Prec, RangeSetBuilder, StateEffect, StateField } from '@codemirror/state';
import { Decoration, EditorView, WidgetType, keymap, type DecorationSet } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language';
import { markdown } from '@codemirror/lang-markdown';
import { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap, type CompletionSource } from '@codemirror/autocomplete';
import { tags } from '@lezer/highlight';
import { blocks, type Block } from './markdown';

type Mounts={add:(dom:HTMLElement,block:Block)=>void;remove:(dom:HTMLElement)=>void};
type Preview={focus:boolean;blocks:Block[];decorations:DecorationSet};
const focused=StateEffect.define<boolean>();
const style=HighlightStyle.define([
  {tag:tags.heading1,fontSize:'2.2em',fontFamily:'Georgia, serif',fontWeight:'700'},{tag:tags.heading2,fontSize:'1.75em',fontFamily:'Georgia, serif',fontWeight:'700'},{tag:[tags.heading3,tags.heading4],fontSize:'1.3em',fontFamily:'Georgia, serif',fontWeight:'700'},
  {tag:tags.strong,fontWeight:'700'},{tag:tags.emphasis,fontStyle:'italic'},{tag:tags.strikethrough,textDecoration:'line-through'},
  {tag:[tags.processingInstruction,tags.meta,tags.contentSeparator],color:'var(--muted)'},{tag:[tags.link,tags.url],color:'var(--accent)'},{tag:tags.monospace,fontFamily:'ui-monospace, monospace'},
]);

/** A second $ typed inside an empty $|$ opens display math, $$|$$. */
const display=EditorView.inputHandler.of((view,from,to,text)=>{
  if(text!=='$'||from!==to||view.state.sliceDoc(from-1,from+1)!=='$$')return false;
  view.dispatch({changes:{from,insert:'$$'},selection:{anchor:from+1},userEvent:'input.type'});return true;
});

/** A rendered block; React fills it through a portal so the page's contexts reach it. */
class Rendered extends WidgetType {
  constructor(readonly block:Block,readonly key:string,readonly mounts:Mounts){super();}
  eq(other:Rendered){return other.key===this.key;}
  toDOM(){const dom=document.createElement('div');dom.className='report-block';this.mounts.add(dom,this.block);return dom;}
  destroy(dom:HTMLElement){this.mounts.remove(dom);}
  ignoreEvent(){return true;}
}

/** The source offset under a point in rendered text, from the offsets its inline text carries. */
function sourceAt(x:number,y:number) {
  const position=document.caretPositionFromPoint?.(x,y),range=position?undefined:document.caretRangeFromPoint?.(x,y);
  const node=position?.offsetNode??range?.startContainer,offset=position?.offset??range?.startOffset??0,text=node?.nodeType===Node.TEXT_NODE;
  const owner=(text?node.parentElement:node as Element|undefined)?.closest<HTMLElement>('[data-offset]');
  return owner?Number(owner.dataset.offset)+(text&&owner.firstChild===node&&owner.tagName!=='DIV'?offset:0):undefined;
}

/** Every block is rendered except those the selection touches while the editor has focus. */
function preview(mounts:Mounts) {
  const parse=(state:EditorState)=>blocks(state.doc.toString(),'body').blocks;
  function decorate(state:EditorState,focus:boolean,parsed:Block[]):Preview {
    const builder=new RangeSetBuilder<Decoration>(),{doc}=state;
    for(const block of parsed){
      const from=doc.line(block.lines[0]+1).from,to=doc.line(Math.min(block.lines[1],doc.lines)).to;
      if(focus&&state.selection.ranges.some(range=>range.from<=to&&range.to>=from))continue;
      builder.add(from,to,Decoration.replace({block:true,widget:new Rendered(block,`${block.lines[0]}\n${doc.sliceString(from,to)}`,mounts)}));
    }
    return {focus,blocks:parsed,decorations:builder.finish()};
  }
  return StateField.define<Preview>({
    create:state=>decorate(state,false,parse(state)),
    update(value,tr){
      const focus=tr.effects.reduce((current,effect)=>effect.is(focused)?effect.value:current,value.focus);
      return tr.docChanged||tr.selection||focus!==value.focus?decorate(tr.state,focus,tr.docChanged?parse(tr.state):value.blocks):value;
    },
    provide:field=>EditorView.decorations.from(field,value=>value.decorations),
  });
}

/** Markdown that stays rendered while it is edited: the block under the cursor shows its source. */
export function Editor({value,onChange,onRender,render,complete,label}: {value:string;onChange:(text:string)=>void;onRender:()=>void;render:(block:Block)=>ReactNode;complete:CompletionSource;label:string}) {
  const host=useRef<HTMLDivElement>(null),view=useRef<EditorView>(),blur=useRef<ReturnType<typeof setTimeout>>(),pressing=useRef(false),latest=useRef({onChange,complete});
  const [mounted,setMounted]=useState(new Map<HTMLElement,{block:Block;key:number}>());
  latest.current={onChange,complete};
  useEffect(()=>{
    let count=0;const resize=new ResizeObserver(()=>view.current?.requestMeasure());
    const mounts:Mounts={
      add:(dom,block)=>{resize.observe(dom);setMounted(current=>new Map(current).set(dom,{block,key:count++}));},
      remove:dom=>{resize.unobserve(dom);setMounted(current=>{const next=new Map(current);next.delete(dom);return next;});},
    };
    view.current=new EditorView({parent:host.current!,state:EditorState.create({doc:value,extensions:[
      history(),markdown(),syntaxHighlighting(style),EditorView.lineWrapping,preview(mounts),
      Prec.high(display),closeBrackets(),EditorState.languageData.of(()=>[{closeBrackets:{brackets:['(','[','{','"','`','*','_','$']}}]),
      autocompletion({override:[context=>latest.current.complete(context)]}),keymap.of([...closeBracketsKeymap,...completionKeymap,...defaultKeymap,...historyKeymap]),
      EditorView.focusChangeEffect.of((_state,focusing)=>focusing&&pressing.current?null:focused.of(focusing)),
      EditorView.contentAttributes.of({'aria-label':label}),
      EditorView.updateListener.of(update=>{if(update.docChanged)latest.current.onChange(update.state.doc.toString());}),
    ]})});
    // Print mode lays out the whole report, so headings, anchors and comments exist off screen too.
    (view.current as unknown as {viewState:{printing:boolean}}).viewState.printing=true;view.current.requestMeasure();
    return()=>{view.current?.destroy();resize.disconnect();};
  },[]);
  useEffect(onRender,[mounted]);
  useEffect(()=>{const current=view.current;if(current&&value!==current.state.doc.toString())current.dispatch({changes:{from:0,to:current.state.doc.length,insert:value}});},[value]);
  /** A press on rendered text gives up focus, which would reset a selection made there; a click then shows the source where it landed. */
  function press() {pressing.current=true;blur.current=setTimeout(()=>{view.current?.contentDOM.blur();pressing.current=false;});}
  function edit(event:MouseEvent,block:Block) {
    const target=event.target as Element,native=event.nativeEvent;
    if(event.detail>1||!event.currentTarget.contains(target)||!window.getSelection()?.isCollapsed||target.closest('a,button,input,textarea,img,video,audio,iframe,summary,.agent-answer,.comment-reply,.comment-reply-box'))return;
    const line=Number(target.closest('[data-source-path="body"][data-line-start]')?.getAttribute('data-line-start'))||block.lines[0]+1,at=sourceAt(event.clientX,event.clientY);
    // After the click has reached the article, where opening a comment claims it.
    setTimeout(()=>{
      const current=view.current;if(!current||native.defaultPrevented)return;
      clearTimeout(blur.current);pressing.current=false;const {doc}=current.state;
      current.focus();current.dispatch({selection:{anchor:at===undefined?doc.line(Math.min(line,doc.lines)).to:Math.min(at,doc.length)},effects:focused.of(true)});
    });
  }
  return <><div ref={host} className="report-editor"/>{[...mounted].map(([dom,{block,key}])=>createPortal(<div onMouseDown={press} onClick={event=>edit(event,block)}>{render(block)}</div>,dom,key))}</>;
}
