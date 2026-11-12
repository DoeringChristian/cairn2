import { useEffect, useRef, useState, type MouseEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { EditorState, Prec, RangeSet, RangeSetBuilder, StateEffect, StateField, type Range } from '@codemirror/state';
import { Decoration, EditorView, ViewPlugin, WidgetType, keymap, type DecorationSet, type ViewUpdate } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { HighlightStyle, syntaxHighlighting, syntaxTree } from '@codemirror/language';
import { markdown, markdownLanguage } from '@codemirror/lang-markdown';
import { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap, type CompletionSource } from '@codemirror/autocomplete';
import { tags } from '@lezer/highlight';
import katex from 'katex';
import DOMPurify from 'dompurify';
import { Code2 } from 'lucide-react';
import { agentLine, blocks, type Block } from './markdown';

type Mounts={add:(dom:HTMLElement,block:Block)=>void;remove:(dom:HTMLElement)=>void};
type Preview={focus:boolean;blocks:Block[];decorations:DecorationSet;agents:DecorationSet};
const focused=StateEffect.define<boolean>();
const style=HighlightStyle.define([
  {tag:tags.heading1,fontSize:'2.2em',fontFamily:'Georgia, serif',fontWeight:'700'},{tag:tags.heading2,fontSize:'1.75em',fontFamily:'Georgia, serif',fontWeight:'700'},{tag:[tags.heading3,tags.heading4],fontSize:'1.3em',fontFamily:'Georgia, serif',fontWeight:'700'},
  {tag:tags.strong,fontWeight:'700'},{tag:tags.emphasis,fontStyle:'italic'},{tag:tags.strikethrough,textDecoration:'line-through'},
  {tag:[tags.processingInstruction,tags.meta,tags.contentSeparator],color:'var(--muted)'},{tag:[tags.link,tags.url],color:'var(--accent)'},{tag:tags.monospace,fontFamily:'ui-monospace, monospace'},
]);
const isAgent=(block:Block)=>block.nodes[0]?.type==='agent';

/** A second $ typed inside an empty $|$ opens display math, $$|$$. */
const display=EditorView.inputHandler.of((view,from,to,text)=>{
  if(text!=='$'||from!==to||view.state.sliceDoc(from-1,from+1)!=='$$')return false;
  view.dispatch({changes:{from,insert:'$$'},selection:{anchor:from+1},userEvent:'input.type'});return true;
});
/** Enter on a line `@agent` or `@agent request` places an agent block there; the page sends the request, or opens its message box. */
const newSlot=()=>`agent-${crypto.getRandomValues(new Uint16Array(1))[0].toString(16).padStart(4,'0')}`;
function mention(onAgent:{current:(slot:string,request:string)=>void}) {
  return keymap.of([{key:'Enter',run:view=>{
    const {state}=view,line=state.doc.lineAt(state.selection.main.head),match=/^@agent(?:\s+(.*))?$/.exec(line.text);
    if(!match||!state.selection.main.empty)return false;
    const slot=newSlot();
    view.dispatch({changes:{from:line.from,to:line.to,insert:`<?cairn agent ${slot}?>`},userEvent:'input.type'});
    onAgent.current(slot,(match[1]||'').trim());
    return true;
  }}]);
}
const agentCount=(state:EditorState)=>{let n=0;for(let i=1;i<=state.doc.lines;i++)if(agentLine.test(state.doc.line(i).text))n++;return n;};
/** Typing, deleting or pasting never removes an agent block by accident; its own control (a change from the page) or undo does. */
const guard=EditorState.transactionFilter.of(tr=>tr.docChanged&&['input','delete','move'].some(event=>tr.isUserEvent(event))&&agentCount(tr.state)<agentCount(tr.startState)?[]:tr);

/** A rendered block; React fills it through a portal so the page's contexts reach it. */
class Rendered extends WidgetType {
  constructor(readonly block:Block,readonly key:string,readonly mounts:Mounts){super();}
  eq(other:Rendered){return other.key===this.key;}
  toDOM(){const dom=document.createElement('div');dom.className='report-block';this.mounts.add(dom,this.block);return dom;}
  destroy(dom:HTMLElement){this.mounts.remove(dom);}
  ignoreEvent(){return true;}
}
/** TeX rendered in place of its source while the cursor is elsewhere. */
class TeX extends WidgetType {
  constructor(readonly tex:string,readonly block:boolean){super();}
  eq(other:TeX){return other.tex===this.tex&&other.block===this.block;}
  toDOM(){const dom=document.createElement(this.block?'div':'span');dom.className=this.block?'md-math-preview':'md-math-rendered';dom.innerHTML=DOMPurify.sanitize(katex.renderToString(this.tex,{displayMode:this.block,throwOnError:false,output:'mathml'}));return dom;}
  ignoreEvent(){return false;}
}

/** The source offset under a point in rendered text, from the offsets its inline text carries. */
function sourceAt(x:number,y:number) {
  const position=document.caretPositionFromPoint?.(x,y),range=position?undefined:document.caretRangeFromPoint?.(x,y);
  const node=position?.offsetNode??range?.startContainer,offset=position?.offset??range?.startOffset??0,text=node?.nodeType===Node.TEXT_NODE;
  const owner=(text?node.parentElement:node as Element|undefined)?.closest<HTMLElement>('[data-offset]');
  return owner?Number(owner.dataset.offset)+(text&&owner.firstChild===node&&owner.tagName!=='DIV'?offset:0):undefined;
}

/** Every block is rendered except the one being edited; an agent block is always rendered and moves as one piece. */
function preview(mounts:Mounts) {
  const parse=(state:EditorState)=>blocks(state.doc.toString(),'body').blocks;
  function decorate(state:EditorState,focus:boolean,parsed:Block[]):Preview {
    const builder=new RangeSetBuilder<Decoration>(),agents=new RangeSetBuilder<Decoration>(),{doc}=state;
    for(const block of parsed){
      const from=doc.line(block.lines[0]+1).from,to=doc.line(Math.min(block.lines[1],doc.lines)).to,agent=isAgent(block);
      const editing=!agent&&focus&&state.selection.ranges.some(range=>range.from<=to&&range.to>=from);
      if(editing){
        // Display math being edited keeps its rendering beneath the source.
        if(block.nodes[0]?.type==='math_block')builder.add(to,to,Decoration.widget({block:true,side:1,widget:new TeX(block.nodes[0].content,true)}));
        continue;
      }
      const decoration=Decoration.replace({block:true,widget:new Rendered(block,`${block.lines[0]}\n${doc.sliceString(from,to)}`,mounts)});
      builder.add(from,to,decoration);
      if(agent)agents.add(from,to,decoration);
    }
    return {focus,blocks:parsed,decorations:builder.finish(),agents:agents.finish()};
  }
  return StateField.define<Preview>({
    create:state=>decorate(state,false,parse(state)),
    update(value,tr){
      const focus=tr.effects.reduce((current,effect)=>effect.is(focused)?effect.value:current,value.focus);
      return tr.docChanged||tr.selection||focus!==value.focus?decorate(tr.state,focus,tr.docChanged?parse(tr.state):value.blocks):value;
    },
    provide:field=>[EditorView.decorations.from(field,value=>value.decorations),EditorView.atomicRanges.of(view=>view.state.field(field).agents)],
  });
}

/** Inside the block being edited, each element shows its Markdown only while the cursor is in it, as in Obsidian:
 * `**`, `*`, `~~`, backticks and link targets hide around text the cursor is not in, and $TeX$ renders. */
const hidden=Decoration.replace({});
class Bullet extends WidgetType {
  eq(){return true;}
  toDOM(){const dom=document.createElement('span');dom.className='md-bullet';dom.textContent='•';return dom;}
}
const bullet=Decoration.replace({widget:new Bullet()});
const elements=new Set(['Emphasis','StrongEmphasis','InlineCode','Strikethrough','Link']);
const marks=new Set(['EmphasisMark','CodeMark','StrikethroughMark','LinkMark','URL']);
function livePreview(view:EditorView):DecorationSet {
  const {state}=view,ranges:Range<Decoration>[]=[];
  if(!view.hasFocus)return RangeSet.empty;
  const touches=(from:number,to:number)=>state.selection.ranges.some(range=>range.from<=to&&range.to>=from);
  // Lines other than the cursor's render: list bullets, heading and quote marks too.
  const editing=(at:number)=>touches(state.doc.lineAt(at).from,state.doc.lineAt(at).to);
  for(const range of state.selection.ranges){
    const start=state.doc.lineAt(range.from),end=state.doc.lineAt(range.to);
    // The block under the cursor: from its first to its last nonblank line around the selection.
    let first=start.number,last=end.number;
    while(first>1&&state.doc.line(first-1).text.trim())first--;
    while(last<state.doc.lines&&state.doc.line(last+1).text.trim())last++;
    const from=state.doc.line(first).from,to=state.doc.line(last).to,code:[number,number][]=[];
    syntaxTree(state).iterate({from,to,enter:node=>{
      if(node.name==='FencedCode'||node.name==='InlineCode')code.push([node.from,node.to]);
      if((node.name==='ListMark'||node.name==='HeaderMark'||node.name==='QuoteMark')&&!editing(node.from)){
        const after=state.doc.sliceString(node.to,node.to+1)===' '?node.to+1:node.to;
        if(node.name!=='ListMark')ranges.push(hidden.range(node.from,after));
        else if(/^[-*+]$/.test(state.doc.sliceString(node.from,node.to)))ranges.push(bullet.range(node.from,node.to));
      }
      if(!elements.has(node.name)||touches(node.from,node.to))return;
      const element=node.node;
      for(let child=element.firstChild;child;child=child.nextSibling)if(marks.has(child.name)&&child.from<child.to)ranges.push(hidden.range(child.from,child.to));
    }});
    const text=state.doc.sliceString(from,to);
    for(const match of text.matchAll(/(?<![\\$])\$(?!\$)([^$\n]*[^$\s\\])\$(?!\$)/g)){
      const at=from+match.index!,end=at+match[0].length;
      if(code.some(([a,b])=>at>=a&&end<=b)||touches(at,end))continue;
      ranges.push(Decoration.replace({widget:new TeX(match[1],false)}).range(at,end));
    }
  }
  return Decoration.set(ranges,true);
}
const inline=ViewPlugin.fromClass(class {
  decorations:DecorationSet;
  constructor(view:EditorView){this.decorations=livePreview(view);}
  update(update:ViewUpdate){if(update.docChanged||update.selectionSet||update.focusChanged||update.viewportChanged)this.decorations=livePreview(update.view);}
},{decorations:plugin=>plugin.decorations});

/** Markdown edited as in Obsidian: blocks render, the block under the cursor shows its source, and within it each element's syntax only while the cursor is in it. */
export function Editor({value,onChange,onRender,onAgent,render,complete,label}: {value:string;onChange:(text:string)=>void;onRender:()=>void;onAgent:(slot:string,request:string)=>void;render:(block:Block)=>ReactNode;complete:CompletionSource;label:string}) {
  const host=useRef<HTMLDivElement>(null),view=useRef<EditorView>(),blur=useRef<ReturnType<typeof setTimeout>>(),pressing=useRef(false),latest=useRef({onChange,complete}),agent=useRef(onAgent);
  agent.current=onAgent;
  const [mounted,setMounted]=useState(new Map<HTMLElement,{block:Block;key:number}>());
  latest.current={onChange,complete};
  useEffect(()=>{
    let count=0;const resize=new ResizeObserver(()=>view.current?.requestMeasure());
    const mounts:Mounts={
      add:(dom,block)=>{resize.observe(dom);setMounted(current=>new Map(current).set(dom,{block,key:count++}));},
      remove:dom=>{resize.unobserve(dom);setMounted(current=>{const next=new Map(current);next.delete(dom);return next;});},
    };
    view.current=new EditorView({parent:host.current!,state:EditorState.create({doc:value,extensions:[
      history(),markdown({base:markdownLanguage}),syntaxHighlighting(style),EditorView.lineWrapping,preview(mounts),inline,guard,
      Prec.high(display),Prec.highest(mention(agent)),closeBrackets(),EditorState.languageData.of(()=>[{closeBrackets:{brackets:['(','[','{','"','`','*','_','$']}}]),
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
    if(isAgent(block)||event.detail>1||!event.currentTarget.contains(target)||!window.getSelection()?.isCollapsed||target.closest('a,button,input,textarea,img,video,audio,iframe,summary,.agent-answer,.comment-reply,.comment-reply-box'))return;
    const line=Number(target.closest('[data-source-path="body"][data-line-start]')?.getAttribute('data-line-start'))||block.lines[0]+1,at=sourceAt(event.clientX,event.clientY);
    // After the click has reached the article, where opening a comment claims it.
    setTimeout(()=>{
      const current=view.current;if(!current||native.defaultPrevented)return;
      clearTimeout(blur.current);pressing.current=false;const {doc}=current.state;
      current.focus();current.dispatch({selection:{anchor:at===undefined?doc.line(Math.min(line,doc.lines)).to:Math.min(at,doc.length)},effects:focused.of(true)});
    });
  }
  /** Shows a block's source from its edit button, for blocks such as viewers whose content keeps every click. */
  function source(block:Block) {
    const current=view.current;if(!current)return;
    current.focus();current.dispatch({selection:{anchor:current.state.doc.line(block.lines[0]+1).from},effects:focused.of(true),scrollIntoView:true});
  }
  return <><div ref={host} className="report-editor"/>{[...mounted].map(([dom,{block,key}])=>createPortal(isAgent(block)?render(block):<div className="report-block-body" onMouseDown={press} onClick={event=>edit(event,block)}>
    <button type="button" className="report-block-edit" aria-label="Edit source" title="Edit source" onMouseDown={event=>{event.preventDefault();event.stopPropagation();}} onClick={()=>source(block)}><Code2 aria-hidden="true"/></button>
    {render(block)}
  </div>,dom,key))}</>;
}
