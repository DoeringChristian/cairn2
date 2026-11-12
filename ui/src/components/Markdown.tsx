import { createElement, Fragment, type ReactNode } from 'react';
import DOMPurify from 'dompurify';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import { SyntaxCode } from './SyntaxCode';
export type Source={path:string;line_start:number;line_end:number};
export type Node={source?:Source;type:string;tag:string;nesting:number;content:string;attributes?:Record<string,string>;children?:Node[];info?:string;meta?:Record<string,number>|null;hidden?:boolean;offset?:number};
export type Render={image:(src:string,alt:string,attrs:Record<string,string>,key:number)=>ReactNode;container?:(attrs:Record<string,string>,content:ReactNode[],key:number)=>ReactNode|undefined;fence?:(info:string,content:string,key:number,source?:Source)=>ReactNode|undefined;agent?:(attrs:Record<string,string>,key:number,source?:Source)=>ReactNode};
const tags=new Set(['p','h1','h2','h3','h4','h5','h6','table','thead','tbody','tr','th','td','ul','ol','li','blockquote','strong','em','s','code','pre','a','figure','figcaption','span','div','aside','sup','sub','dl','dt','dd']);
const callout=/^callout-(note|tip|warning|important|caution)$/;
/** Sanitize HTML before placing it in the document flow. */
export function SafeHtml({html,inline=false}: {html:string;inline?:boolean}) {
  const clean=DOMPurify.sanitize(html,{USE_PROFILES:{html:true,mathMl:true},FORBID_TAGS:['style','form','input','button','iframe','object','embed','audio','video'],FORBID_ATTR:['style','id','name','srcset']});
  return createElement(inline?'span':'div',{className:'native-rich',dangerouslySetInnerHTML:{__html:clean}});
}
export function MathValue({tex,block=false}: {tex:string;block?:boolean}) {
  return <span data-latex={tex}><SafeHtml inline={!block} html={katex.renderToString(tex,{displayMode:block,throwOnError:false,trust:false,output:'mathml'})}/></span>;
}
export const classes=(value?:string)=>value&&/^[\w -]+$/.test(value)?value:undefined;
export const length=(value?:string)=>value&&/^\d+(\.\d+)?(px|%|em|rem)?$/.test(value)?(/\d$/.test(value)?value+'px':value):undefined;
/** Source attributes that comments and selection copy anchor to. */
const sourced=(node:Node)=>node.source?{'data-source-path':node.source.path,'data-line-start':node.source.line_start,'data-line-end':node.source.line_end}:{};
function safeHref(value:string) {return /^(https?:|mailto:|#)/i.test(value)?value:undefined;}
/** Markdown nodes as React; images, fenced divs and fenced blocks the caller knows are rendered by the caller. */
export function nodesToReact(nodes:Node[],render:Render):ReactNode[] {
  let index=0;
  function children():ReactNode[] {
    const out:ReactNode[]=[];
    while(index<nodes.length){const node=nodes[index++],key=index,attrs=node.attributes||{};if(node.nesting===-1)break;
      if(node.type==='inline'){out.push(<Fragment key={key}>{nodesToReact(node.children||[],render)}</Fragment>);continue;}
      if(node.type==='text'){out.push(node.offset===undefined?<Fragment key={key}>{node.content}</Fragment>:<span key={key} data-offset={node.offset}>{node.content}</span>);continue;}
      if(node.type==='code_inline'){out.push(<code key={key} data-offset={node.offset}>{node.content}</code>);continue;}
      if(node.type==='softbreak'||node.type==='hardbreak'){out.push(node.type==='hardbreak'?<br key={key}/>:'\n');continue;}
      if(node.type==='agent'){out.push(render.agent?.(attrs,key,node.source));continue;}
      if(node.type==='image'){out.push(render.image(attrs.src||'',node.content,attrs,key));continue;}
      if(node.type.startsWith('math_')){out.push(<div key={key} {...sourced(node)} data-offset={node.offset} className={node.type.startsWith('math_inline')?'native-inline-math':'native-math'}><MathValue tex={node.content} block={!node.type.startsWith('math_inline')}/>{node.type==='math_block_eqno'&&<small>({node.info})</small>}</div>);continue;}
      if(node.type==='fence'){const custom=render.fence?.((node.info||'').trim(),node.content,key,node.source);if(custom!==undefined){out.push(custom);continue;}}
      if(node.type==='fence'||node.type==='code_block'){out.push(<div key={key} {...sourced(node)}><SyntaxCode code={node.content} language={node.info}/></div>);continue;}
      if(node.type==='hr'){out.push(<hr key={key}/>);continue;}
      if(node.type==='footnote_ref'){const n=(node.meta?.id||0)+1;out.push(<sup key={key}><a id={`fnref-${n}`} href={`#fn-${n}`}>{n}</a></sup>);continue;}
      if(node.type==='footnote_anchor'){const n=(node.meta?.id||0)+1;out.push(<a key={key} href={`#fnref-${n}`} aria-label="Back to footnote reference">↩</a>);continue;}
      const content=node.nesting===1?children():node.content;
      if(node.hidden){out.push(<Fragment key={key}>{content}</Fragment>);continue;}
      if(node.type.startsWith('container_')){const custom=render.container?.(attrs,content as ReactNode[],key);if(custom!==undefined){out.push(custom);continue;}
        const kind=(attrs.class||'').split(' ').map(name=>callout.exec(name)?.[1]).find(Boolean);out.push(kind?<aside key={key} className={`admonition ${kind}`}><p className="admonition-title">{attrs.title||kind[0].toUpperCase()+kind.slice(1)}</p>{content}</aside>:<div key={key} className={classes(attrs.class)}>{content}</div>);continue;}
      if(node.type==='footnote_block_open'){out.push(<ol key={key} className="native-footnotes">{content}</ol>);continue;}
      if(node.type==='footnote_open'){out.push(<li key={key} id={`fn-${(node.meta?.id||0)+1}`}>{content}</li>);continue;}
      if(tags.has(node.tag)){const props:Record<string,unknown>={key,...sourced(node)};if(attrs.href)props.href=safeHref(attrs.href);if(attrs.title)props.title=attrs.title;if(classes(attrs.class))props.className=classes(attrs.class);if(node.tag==='ol'&&attrs.start)props.start=Number(attrs.start);out.push(createElement(node.tag,props,content));}
      else if(content)out.push(<Fragment key={key}>{content}</Fragment>);
    }return out;
  }return children();
}
