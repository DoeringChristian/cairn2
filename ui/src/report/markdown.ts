import MarkdownIt from 'markdown-it';
import type Token from 'markdown-it/lib/token.mjs';
import container from 'markdown-it-container';
import footnote from 'markdown-it-footnote';
import texmath from 'markdown-it-texmath';
import katex from 'katex';
import type { Node } from '../components/Markdown';

const md=new MarkdownIt('commonmark',{html:false}).enable(['table','strikethrough']).use(footnote).use(texmath,{engine:katex,delimiters:'dollars'})
  .use(container,'div',{validate:(info:string)=>/^\s*\{/.test(info)});
const attribute=/([#.])([^\s{}"'=]+)|([\w-]+)=(?:"([^"]*)"|'([^']*)'|([^\s{}"']+))/g;

/** Pandoc attributes of a fenced div, e.g. {.agent #baseline}: id, space-separated class and key/value pairs. */
export function attributes(info:string) {
  const result:Record<string,string>={},classes:string[]=[];
  for(const match of info.matchAll(attribute)){
    if(match[1]==='#')result.id=match[2];else if(match[1]==='.')classes.push(match[2]);else result[match[3]]=match[4]??match[5]??match[6];
  }
  if(classes.length)result.class=classes.join(' ');
  return result;
}
function node(token:Token,path:string):Node {
  return {type:token.type,tag:token.tag,nesting:token.nesting,content:token.content,info:token.info,meta:token.meta,hidden:token.hidden,
    attributes:token.type.startsWith('container_')&&token.nesting===1?attributes(token.info):Object.fromEntries(token.attrs||[]),
    source:token.map?{path,line_start:token.map[0]+1,line_end:token.map[1]}:undefined,children:token.children?.map(child=>node(child,path))};
}
/** Markdown as nodes that remember their part and lines, so comments anchor to the source. */
export const parse=(text:string,path:string)=>md.parse(text,{}).map(token=>node(token,path));
const literal=new Set(['text','code_inline','math_inline','math_inline_double']);
/** Where each piece of inline text starts in the source, so a click can place the cursor there. */
function locate(nodes:Node[],text:string,starts:number[]) {
  let line=0;
  for(const inline of nodes){
    if(inline.source)line=inline.source.line_start-1;
    if(inline.type!=='inline')continue;
    let cursor=starts[line];
    for(const child of inline.children||[])if(literal.has(child.type)&&child.content){const at=text.indexOf(child.content,cursor);if(at>=0){child.offset=at;cursor=at+child.content.length;}}
  }
}
export type Block={lines:[number,number];nodes:Node[]};
/** Top-level blocks with their source lines, a fenced div's closing fence included; nodes without lines, such as footnotes, come last. */
export function blocks(text:string,path:string) {
  const tokens=md.parse(text,{}),lines=text.split('\n'),result:Block[]=[],rest:Node[]=[],starts=[0];
  for(const line of lines)starts.push(starts[starts.length-1]+line.length+1);
  for(let i=0,j=0;i<tokens.length;i=j+1){
    j=i;if(tokens[i].nesting===1)while(tokens[j].nesting!==-1||tokens[j].level!==tokens[i].level)j++;
    const nodes=tokens.slice(i,j+1).map(token=>node(token,path)),map=tokens[i].map;
    if(!map){rest.push(...nodes);continue;}
    locate(nodes,text,starts);
    result.push({lines:[map[0],tokens[i].type.startsWith('container_')&&/^\s*:{3,}\s*$/.test(lines[map[1]]||'')?map[1]+1:map[1]],nodes});
  }
  return {blocks:result,rest};
}
