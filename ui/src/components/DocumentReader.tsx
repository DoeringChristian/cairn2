import { OutputComment } from "./OutputComment";
import { SyntaxCode } from "./SyntaxCode";
import { HtmlOutput } from "./HtmlOutput";
import { ReportArtifactContext } from "./ReportContext";
import { ReportComments } from "./ReportComments";
import { PlotlyFigure } from "./PlotlyFigure";
import { ReportToc } from "./ReportToc";
import { ZoomImage } from './ZoomImage';
import { MediaPlayback } from './MediaPlayback';
import { createElement, Fragment, memo, useEffect, useState, useMemo, useRef, type ReactNode } from 'react';
import MarkdownIt from 'markdown-it';
import footnote from 'markdown-it-footnote';
import DOMPurify from 'dompurify';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import { useResource, type BlobRef } from '../api';
import { SelectionCopy } from './SelectionCopy';
import { ErrorNote, Hint, Loading } from './States';
type Source={path:string;line_start:number;line_end:number};
type Node={source?:Source;type:string;tag:string;nesting:number;content:string;attributes?:Record<string,string>;attrs?:Record<string,string>|[string,string][]|null;children?:Node[];info?:string;meta?:Record<string,number>;hidden?:boolean};
type Asset={asset:string;mime:string;encoding:"binary"|"utf-8"|"json"};
type Output={ui_payload?:{path:string;mime:string};output_type:string;name?:string;text?:string|string[]|Asset;ename?:string;evalue?:string;data?:Record<string,unknown>};
type Block={id:string;kind:string;source:Source;text?:string;nodes?:Node[];code?:string;label?:string;options?:{echo?:boolean;output?:boolean;'fig-cap'?:string;'tbl-cap'?:string};outputs?:Output[]};
type Document={format:string;version:number;title:string;blocks:Block[];labels:Record<string,{text:string}>};
const md=new MarkdownIt({html:false,linkify:false}).use(footnote);
const tags=new Set(['p','h1','h2','h3','h4','h5','h6','table','thead','tbody','tr','th','td','ul','ol','li','blockquote','strong','em','s','code','pre','a','figure','figcaption','span','div','aside','sup']);
const join=(value:unknown):string=>Array.isArray(value)?value.join(''):String(value??'');
/** Sanitize rich notebook HTML before placing it in the native document flow. */
function SafeHtml({html,inline=false}: {html:string;inline?:boolean}) {
  const clean=DOMPurify.sanitize(html,{USE_PROFILES:{html:true,mathMl:true},FORBID_TAGS:['style','form','input','button','iframe','object','embed','audio','video'],FORBID_ATTR:['style','id','name','srcset']});
  return createElement(inline?'span':'div',{className:'native-rich',dangerouslySetInnerHTML:{__html:clean}});
}
function MathValue({tex,block=false}: {tex:string;block?:boolean}) {
  return <span data-latex={tex}><SafeHtml inline={!block} html={katex.renderToString(tex,{displayMode:block,throwOnError:false,trust:false,output:'mathml'})}/></span>;
}
function safeHref(value:string) {return /^(https?:|mailto:|#)/i.test(value)?value:undefined;}
function resource(base:string,raw:string,source:Source,blobs:BlobRef[]) {
  if(/^data:image\/(png|jpeg|gif|webp);base64,/i.test(raw)) return raw;
  if(/^[a-z]+:|^\/|\\/i.test(raw))return undefined;
  let path:string; try{path=decodeURIComponent(raw.split(/[?#]/)[0]);}catch{return undefined;}
  const parent=source.path.split('/').slice(0,-1).join('/');
  const options=[path,`sources/${parent?parent+'/':''}${path}`];
  const name=options.find(name=>!name.split('/').includes('..')&&blobs.some(b=>b.name===name));
  return name?base+name.split('/').map(encodeURIComponent).join('/'):undefined;
}
function inlineText(text:string,labels:Document['labels']):ReactNode[] {
  return text.split(/(@(?:fig|tbl|sec|eq)-[\w.-]+|\$[^$\n]+\$)/g).map((part,i)=>part.startsWith('$')&&part.endsWith('$')?<MathValue key={i} tex={part.slice(1,-1)}/>:part.startsWith('@')&&labels[part.slice(1)]?<a key={i} href={`#${part.slice(1)}`}>{labels[part.slice(1)].text}</a>:part);
}
function nodesToReact(nodes:Node[],base:string,source:Source,blobs:BlobRef[],labels:Document['labels']):ReactNode[] {
  let index=0;
  function children():ReactNode[] {
    const out:ReactNode[]=[];
    while(index<nodes.length){const node=nodes[index++],key=index,attrs=node.attributes||(Array.isArray(node.attrs)?Object.fromEntries(node.attrs):node.attrs)||{};if(node.nesting===-1)break;
      if(node.type==='inline') {
        const parsed=node.children?.length?node.children:md.parseInline(node.content.replace(/\s*\{#(?:sec|fig|tbl)-[\w.-]+\}\s*$/,''),{})[0]?.children || [];
        out.push(<Fragment key={key}>{nodesToReact(parsed as Node[],base,source,blobs,labels)}</Fragment>);continue;
      }
      if(node.type==='text'){out.push(<Fragment key={key}>{inlineText(node.content,labels)}</Fragment>);continue;}
      if(node.type==='code_inline'){out.push(<code key={key}>{node.content}</code>);continue;}
      if(node.type==='softbreak'||node.type==='hardbreak'){out.push(node.type==='hardbreak'?<br key={key}/>:'\n');continue;}
      if(node.type==='image'){const src=resource(base,attrs.src||'',source,blobs);out.push(src?<ZoomImage key={key} src={src} alt={node.content} source={{file:source.path,line:source.line_start}}/>:<span key={key}>[Image unavailable: {node.content}]</span>);continue;}
      if(node.type.startsWith('math_')){out.push(<div key={key} id={node.type==='math_block_label'?node.info:undefined} className={node.type==='math_inline'?'native-inline-math':'native-math'}><MathValue tex={node.content} block={node.type!=='math_inline'}/>{node.info&&labels[node.info]&&<small>{labels[node.info].text}</small>}</div>);continue;}
      if(node.type==='html_inline'||node.type==='html_block'){out.push(<SafeHtml key={key} html={node.content} inline={node.type==='html_inline'}/>);continue;}
      if(node.type==='fence'||node.type==='code_block'){out.push(<SyntaxCode key={key} code={node.content} language={node.info}/>);continue;}
      if(node.type==='hr'){out.push(<hr key={key}/>);continue;}
      if(node.type==='footnote_ref'){const n=(node.meta?.id||0)+1;out.push(<sup key={key}><a id={`fnref-${n}`} href={`#fn-${n}`}>{n}</a></sup>);continue;}
      if(node.type==='footnote_anchor'){const n=(node.meta?.id||0)+1;out.push(<a key={key} href={`#fnref-${n}`} aria-label="Back to footnote reference">↩</a>);continue;}
      const content=node.nesting===1?children():node.content;
      if(node.hidden){out.push(<Fragment key={key}>{content}</Fragment>);continue;}
      if(node.type==='container_callout_open'){const kind=/callout-(\w+)/.exec(node.info||'')?.[1]||'note';const title=/title="([^"]+)"/.exec(node.info||'')?.[1]||kind;out.push(<aside key={key} className={`admonition ${kind}`}><p className="admonition-title">{title}</p>{content}</aside>);continue;}
      if(node.type==='footnote_block_open'){out.push(<ol key={key} className="native-footnotes">{content}</ol>);continue;}
      if(node.type==='footnote_open'){out.push(<li key={key} id={`fn-${(node.meta?.id||0)+1}`}>{content}</li>);continue;}
      if(tags.has(node.tag)){const props:Record<string,unknown>={key};if(node.source){props['data-source-path']=node.source.path;props['data-line-start']=node.source.line_start;props['data-line-end']=node.source.line_end;}if(attrs.id&&/^(sec|fig|tbl|eq)-[\w.-]+$/.test(attrs.id))props.id=attrs.id;if(attrs.href)props.href=safeHref(attrs.href);if(attrs.title)props.title=attrs.title;if(node.tag==='ol'&&attrs.start)props.start=Number(attrs.start);out.push(createElement(node.tag,props,content));}
      else if(content)out.push(<Fragment key={key}>{content}</Fragment>);
    }return out;
  }return children();
}
function AssetOutput({asset,output,mime,...props}: {asset:Asset;output:Output;mime?:string;base:string;source:Source;blobs:BlobRef[];labels:Document['labels'];htmlUrl:string}) {
  const host=useRef<HTMLDivElement>(null),[payload,setPayload]=useState<unknown>(),[error,setError]=useState<Error>();
  const src=resource(props.base,asset.asset,props.source,props.blobs);
  useEffect(()=>{
    const controller=new AbortController();
    if(!src){setError(new Error('Report output asset is missing'));return;}
    const observer=new IntersectionObserver(entries=>{if(!entries.some(entry=>entry.isIntersecting))return;observer.disconnect();
      fetch(src,{signal:controller.signal,credentials:'same-origin'}).then(async response=>{if(!response.ok)throw new Error(`Unable to load report output (${response.status})`);return asset.encoding==='json'?response.json():response.text();}).then(data=>{if(!controller.signal.aborted)setPayload(data);}).catch(e=>{if(!controller.signal.aborted)setError(e);});
    },{rootMargin:'300px'});observer.observe(host.current!);
    return()=>{controller.abort();observer.disconnect();};
  },[src,asset.encoding]);
  return <div ref={host} className="native-asset-output" data-output-mime={mime||"text/plain"}>{error?<ErrorNote error={error}/>:payload===undefined?<Loading what="Loading output"/>:<OutputView {...props} output={mime?{...output,data:{[mime]:payload}}:{...output,text:String(payload)}}/>}</div>;
}
function OutputView({output,base,source,blobs,labels,htmlUrl,formatVersion=1}: {formatVersion?:number;output:Output;base:string;source:Source;blobs:BlobRef[];labels:Document['labels'];htmlUrl:string}) {
  if(formatVersion===2) {
    const data=output.data||{};
    const mime=['application/vnd.plotly.v1+json','video/mp4','video/webm','audio/wav','audio/mpeg','audio/ogg','image/svg+xml','image/png','image/jpeg','text/html','text/markdown','application/json','text/plain'].find(mime=>data[mime]);
    const asset=(output.output_type==='stream'?output.text:mime?data[mime]:undefined) as Asset|undefined;
    if(asset&&typeof asset==='object'&&typeof asset.asset==='string') {
      const src=resource(base,asset.asset,source,blobs);
      if(!src)return <Hint>Report output asset is missing.</Hint>;
      if(mime==='application/vnd.plotly.v1+json')return <OutputComment source={{file:source.path,line:source.line_start}} label="Plotly figure"><PlotlyFigure src={src}/></OutputComment>;
      if(mime?.startsWith('image/'))return <ZoomImage className="native-output-image" src={src} alt="Python cell image output" source={{file:source.path,line:source.line_start}}/>;
      if(mime?.startsWith('video/')||mime?.startsWith('audio/'))return <MediaPlayback src={src} mime={mime} label={mime.startsWith('audio/')?'Report audio':'Report video'} source={{file:source.path,line:source.line_start}}/>;
      return <AssetOutput asset={asset} output={output} mime={mime} base={base} source={source} blobs={blobs} labels={labels} htmlUrl={htmlUrl}/>;
    }
  }
  if(output.output_type==='stream')return <pre className={`native-output ${output.name||''}`}>{join(output.text)}</pre>;
  if(output.output_type==='error')return <pre className="native-output error">{output.ename}: {output.evalue}</pre>;
  if (output.ui_payload) {
    const {path,mime}=output.ui_payload,src=`${base}output/${path}`;
    if(mime==='application/vnd.plotly.v1+json')return <OutputComment source={{file:source.path,line:source.line_start}} label="Plotly figure"><PlotlyFigure src={src}/></OutputComment>;
    if(mime.startsWith('image/'))return <ZoomImage className="native-output-image" src={src} alt="Python cell image output" source={{file:source.path,line:source.line_start}}/>;
    return <MediaPlayback src={src} mime={mime} label={mime.startsWith('audio/')?'Report audio':'Report video'} source={{file:source.path,line:source.line_start}}/>;
  }
  const data=output.data||{};
  if(data['application/vnd.plotly.v1+json'])return <OutputComment source={{file:source.path,line:source.line_start}} label="Plotly figure"><PlotlyFigure spec={data['application/vnd.plotly.v1+json']}/></OutputComment>;
  for(const mime of ['video/mp4','video/webm','audio/wav','audio/mpeg','audio/ogg']) if(data[mime]) {
    const src=`data:${mime};base64,${join(data[mime]).replace(/\s/g,'')}`;
    return <MediaPlayback src={src} mime={mime} label={mime.startsWith('audio/')?'Report audio':'Report video'} source={{file:source.path,line:source.line_start}}/>;
  }
  for(const type of ['image/svg+xml','image/png','image/jpeg'])if(data[type]){
    const raw=join(data[type]);const src=type==='image/svg+xml'?`data:image/svg+xml;charset=utf-8,${encodeURIComponent(raw)}`:`data:${type};base64,${raw.replace(/\s/g,'')}`;
    return <ZoomImage className="native-output-image" src={src} alt="Python cell image output" source={{file:source.path,line:source.line_start}}/>;
  }
  if(data['text/html'])return /<(script|style|canvas|html)\b/i.test(join(data['text/html']))?<OutputComment source={{file:source.path,line:source.line_start}} label="HTML viewer"><HtmlOutput src={htmlUrl}/></OutputComment>:<SafeHtml html={join(data['text/html'])}/>;
  if(data['text/markdown'])return <>{nodesToReact(md.parse(join(data['text/markdown']),{}) as unknown as Node[],base,source,blobs,labels)}</>;
  return <pre className="native-output">{data['application/json']?JSON.stringify(data['application/json'],null,2):join(data['text/plain'])}</pre>;
}
export const DocumentReader = memo(function DocumentReader({version,blobs,project,documentVersion=1}: {version:string;blobs:BlobRef[];project:string;documentVersion?:number}) {
  const container=useRef<HTMLElement>(null);
  const base=`/ui-api/reports/${encodeURIComponent(version)}/`;
  const {data,error,loading}=useResource<Document>(base+(documentVersion===2?'document.json':'preview.json'),0,true);
  const renderedBlocks=useMemo(()=>data?.format==='cairn/document'&&[1,2].includes(data.version)?data.blocks.map((block,blockIndex)=>{
    const caption=block.options?.['fig-cap']||block.options?.['tbl-cap'];
    return <div className="native-block" key={block.id} id={block.label||block.id} data-source-path={block.source.path} data-line-start={block.source.line_start} data-line-end={block.source.line_end}>
      {block.kind==='markdown'?nodesToReact(block.nodes||[],base,block.source,blobs,data.labels):<>
        {block.options?.echo!==false&&<details className="native-code"><summary>Code · {block.source.path}:{block.source.line_start}</summary><SyntaxCode code={block.code||''} language="python"/></details>}
        {block.options?.output!==false&&block.outputs?.map((output,i)=><OutputView key={i} formatVersion={data.version} htmlUrl={`${base}html/${blockIndex}/${i}`} output={output} base={base} source={block.source} blobs={blobs} labels={data.labels}/>)}
        {caption&&<p className="native-caption">{block.label&&data.labels[block.label]?`${data.labels[block.label].text}: `:''}{inlineText(caption,data.labels)}</p>}
      </>}
    </div>;
  }):null,[data,base,blobs]);
  if(loading&&!data)return <Loading what="Loading report"/>;if(error&&!data)return <ErrorNote error={error}/>;
  if(!data||data.format!=='cairn/document'||![1,2].includes(data.version))return <Hint>This document format is not supported by this reader.</Hint>;
  return <ReportArtifactContext.Provider value={version}><div className="report-reader-layout"><ReportToc container={container} documentKey={data}/><article ref={container} className="native-document report-prose" aria-label={data.title}>{renderedBlocks}<SelectionCopy container={container} version={version}/></article><ReportComments container={container} artifact={version} project={project}/></div></ReportArtifactContext.Provider>;
}, (previous,next)=>previous.version===next.version&&previous.documentVersion===next.documentVersion&&previous.project===next.project&&
  (previous.blobs===next.blobs||JSON.stringify(previous.blobs)===JSON.stringify(next.blobs)));
