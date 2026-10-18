import { useCopyText } from './useCopyText';
import { Check, Copy } from 'lucide-react';
import { useMemo } from 'react';
import hljs from 'highlight.js/lib/core';
import python from 'highlight.js/lib/languages/python';
import javascript from 'highlight.js/lib/languages/javascript';
import typescript from 'highlight.js/lib/languages/typescript';
import json from 'highlight.js/lib/languages/json';
import bash from 'highlight.js/lib/languages/bash';
import yaml from 'highlight.js/lib/languages/yaml';
import css from 'highlight.js/lib/languages/css';
import xml from 'highlight.js/lib/languages/xml';
import sql from 'highlight.js/lib/languages/sql';
import cpp from 'highlight.js/lib/languages/cpp';
import rust from 'highlight.js/lib/languages/rust';
import markdown from 'highlight.js/lib/languages/markdown';
for(const [name,grammar] of Object.entries({python,javascript,typescript,json,bash,yaml,css,xml,sql,cpp,rust,markdown}))hljs.registerLanguage(name,grammar);
const aliases:Record<string,string>={py:'python',js:'javascript',jsx:'javascript',ts:'typescript',tsx:'typescript',sh:'bash',shell:'bash',zsh:'bash',yml:'yaml',html:'xml',c:'cpp',md:'markdown'};
/** Display-only code; unknown/plain fences remain escaped text, never executed. */
export function SyntaxCode({code,language=''}: {code:string;language?:string}) {
  const {copy,copied,error}=useCopyText(code);
  const raw=language.trim().split(/\s/)[0].replace(/[{}]/g,'').toLowerCase(),name=aliases[raw]||raw;
  const highlighted=useMemo(()=>hljs.getLanguage(name)?hljs.highlight(code,{language:name,ignoreIllegals:true}).value:undefined,[code,name]);
  return <div className="syntax-code"><button type="button" className="image-copy code-copy" aria-label="Copy code" title="Copy code" onClick={copy}>{copied?<Check aria-hidden="true"/>:<Copy aria-hidden="true"/>}</button><span className="sr-only" role="status">{copied?'Copied':error?'Unable to copy code':''}</span>{raw&&<span className="syntax-language" aria-hidden="true">{raw}</span>}<pre tabIndex={0} aria-label={raw?`${raw} code`:'Code'}>{highlighted?<code className={`hljs language-${name}`} dangerouslySetInnerHTML={{__html:highlighted}}/>:<code>{code}</code>}</pre></div>;
}
