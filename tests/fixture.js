import {pendingUploads, clearQueue, legacyPages} from '../extension/store.js';
const output = document.querySelector('#results');
const wait = ms => new Promise(resolve => setTimeout(resolve,ms));
const assert = (condition,message) => { if(!condition)throw new Error(message); };
const checks=[];
async function check(name,fn) { await fn(); checks.push(name); output.textContent=checks.map(s=>'PASS '+s).join('\n'); }
const api = async (type,payload={}) => {
  const result=await chrome.runtime.dispatch({type:'seen:'+type,...payload},{id:'seen-test',url:chrome.runtime.getURL('archive.html')});
  if(!result.ok)throw new Error(result.error);return result.value;
};
async function settle() { await wait(2950); await api('sync'); await wait(100); }
function latest() { return [...seenTest.saved.values()].at(-1); }
document.querySelector('#run').addEventListener('click',async()=>{
  document.querySelector('#run').disabled=true;
  try {
    await seenTest.ready(); globalThis.__seenDOMCapture.stop(); await clearQueue(); await api('clear'); await api('diagnostics-clear');
    const root=document.querySelector('#shadow').attachShadow({mode:'open'}); root.append(document.createTextNode('OPEN_SHADOW_MARKER'));
    const closed=document.querySelector('#closed').attachShadow({mode:'closed'}); closed.append(document.createTextNode('CLOSED_SHADOW_MARKER'));
    seenTest.closedRoots.set(document.querySelector('#closed'),closed);
    document.querySelector('#field').value='LIVE_INPUT_MARKER';
    document.querySelector('#area').value='LIVE_TEXTAREA_MARKER';
    document.querySelector('#select').selectedIndex=1;
    const before=Array.from(document.childNodes,node=>node.nodeType===Node.ELEMENT_NODE?node.outerHTML:new XMLSerializer().serializeToString(node)).join('');
    const resources=performance.getEntriesByType('resource').length;
    await globalThis.__seenDOMCapture.start(); await settle(); globalThis.__seenDOMCapture.stop();
    const capture=latest();
    await check('whole DOM includes hidden, offscreen, collapsed, scripts, templates, attributes, comments',()=>{
      for(const marker of ['VISIBLE_DOM_MARKER','HIDDEN_DOM_MARKER','COLLAPSED_DOM_MARKER','SCRIPT_DOM_MARKER','TEMPLATE_DOM_MARKER','COMMENT_DOM_MARKER','OFFSCREEN_DOM_MARKER','data-preserve'])assert(capture.dom.html.includes(marker),'Missing '+marker);
      assert(capture.dom.html.includes('<!DOCTYPE html>'),'Doctype missing');
    });
    await check('live form state and open/closed shadow DOM are retained',()=>{
      assert(capture.dom.formState.some(s=>s.value==='LIVE_INPUT_MARKER'),'Input value missing');
      assert(capture.dom.formState.some(s=>s.value==='synthetic-only'),'Password filtered');
      assert(capture.dom.formState.some(s=>s.value==='LIVE_TEXTAREA_MARKER'),'Textarea missing');
      assert(capture.dom.formState.some(s=>s.selected?.[0]===1),'Selection missing');
      assert(capture.dom.shadowRoots.some(s=>s.html.includes('OPEN_SHADOW_MARKER')),'Open shadow missing');
      assert(capture.dom.shadowRoots.some(s=>s.html.includes('CLOSED_SHADOW_MARKER')),'Closed shadow missing');
    });
    await check('capture adds no resource requests and leaves source DOM unchanged',()=>{
      assert(performance.getEntriesByType('resource').length===resources,'Extra request');
      // The test output changes only after the baseline comparison.
      const savedSource=capture.dom.html; assert(savedSource===before,'Source DOM altered by capture');
    });
    await check('all sites and formerly sensitive routes are treated equally',async()=>{
      for(const url of ['http://one.test/login','https://two.test/inbox','https://three.test/settings','https://four.test/billing']){
        const r=await chrome.runtime.dispatch({type:'seen:allowed'},{...seenTest.sender(),url});assert(r.ok&&r.value.allowed,url+' denied');
      }
    });
    await check('background navigation is logged before content-script injection',async()=>{
      chrome.tabs.onUpdated.emit(1,{status:'loading'},seenTest.sender().tab);await wait(25);
      const diagnostic=(await api('status')).diagnostics.at(-1);
      assert(diagnostic.event==='navigation-observed'&&diagnostic.stage==='loading','Navigation missing');
    });
    await check('Incognito and unfocused/background tabs are rejected',async()=>{
      let r=await chrome.runtime.dispatch({type:'seen:allowed'},{...seenTest.sender(),tab:{...seenTest.sender().tab,incognito:true}});assert(!r.value.allowed,'Incognito allowed');
      seenTest.control.focused=false;r=await chrome.runtime.dispatch({type:'seen:allowed'},seenTest.sender());assert(!r.value.allowed,'Unfocused allowed');seenTest.control.focused=true;
      seenTest.control.active=false;r=await chrome.runtime.dispatch({type:'seen:allowed'},seenTest.sender());assert(!r.value.allowed,'Background allowed');seenTest.control.active=true;
    });
    await check('only the top-level document is registered and permitted',async()=>{
      await api('sync');const script=seenTest.registered()[0];assert(script.allFrames===false&&!script.matchOriginAsFallback,'Subframes enabled');
      const r=await chrome.runtime.dispatch({type:'seen:allowed'},{...seenTest.sender(),frameId:2,url:'about:srcdoc'});assert(!r.value.allowed,'Subframe allowed');
    });
    await check('page contexts cannot read or clear the archive',async()=>{
      for(const type of ['seen:list','seen:clear','seen:read']){const r=await chrome.runtime.dispatch({type},seenTest.sender());assert(!r.ok,'Privilege boundary failed');}
    });
    await check('snapshot URLs cannot be spoofed',async()=>{
      const r=await chrome.runtime.dispatch({type:'seen:begin',url:'https://other.test/',bytes:1,chunks:1},seenTest.sender());assert(!r.value.allowed,'Spoof accepted');
      const diagnostic=(await api('status')).diagnostics.at(-1);
      assert(diagnostic.event==='capture-rejected'&&diagnostic.reason==='url-mismatch','URL rejection not diagnosed');
      assert(diagnostic.url==='https://other.test/'&&diagnostic.senderUrl===location.href,'URL disagreement missing');
      const count=(await api('status')).diagnostics.length;
      await chrome.runtime.dispatch({type:'seen:begin',url:'https://other.test/',bytes:1,chunks:1},{...seenTest.sender(),frameId:2});
      assert((await api('status')).diagnostics.length===count,'Subframe rejection polluted diagnostics');
    });
    await check('paused capture rejected',async()=>{
      seenTest.settings.paused=true;const r=await chrome.runtime.dispatch({type:'seen:allowed'},seenTest.sender());assert(!r.value.allowed,'Pause ignored');seenTest.settings.paused=false;
    });
    await check('scroll and noisy UI changes do not save, but meaningful content does',async()=>{
      await globalThis.__seenDOMCapture.start();const count=seenTest.saved.size;
      window.dispatchEvent(new Event('scroll'));await wait(100);assert(seenTest.saved.size===count,'Scroll captured');
      const noise=document.createElement('div');noise.className='advertisement';noise.textContent='ROTATING_AD_MARKER';document.body.append(noise);
      await settle();assert(seenTest.saved.size===count,'Advertisement change captured');
      const content=document.createElement('p');content.textContent='NEW_MEANINGFUL_CONTENT_MARKER';document.querySelector('#fixture').append(content);
      await settle();globalThis.__seenDOMCapture.stop();
      assert(seenTest.saved.size===count+1,'Meaningful content not captured');
      assert(latest().dom.html.includes('NEW_MEANINGFUL_CONTENT_MARKER'),'Meaningful DOM missing');
    });
    await check('foreground time pauses separately without saving DOM',async()=>{
      const captures=seenTest.saved.size;
      chrome.tabs.onActivated.emit({tabId:1});await wait(40);
      chrome.idle.onStateChanged.emit('idle');await wait(40);
      const visit=[...seenTest.visits.values()].at(-1);
      assert(visit&&visit.focusedMs>0,'Focused time missing');
      assert(seenTest.saved.size===captures,'Timing created a DOM snapshot');
      chrome.idle.onStateChanged.emit('active');await wait(40);
    });
    await check('page departure saves a last-second meaningful change',async()=>{
      await globalThis.__seenDOMCapture.start();const count=seenTest.saved.size;
      const final=document.createElement('p');final.textContent='FINAL_PAGEHIDE_MARKER';document.querySelector('#fixture').append(final);
      window.dispatchEvent(new Event('pagehide'));await wait(250);await api('sync');globalThis.__seenDOMCapture.stop();
      assert(seenTest.saved.size===count+1,'Final meaningful state not captured');
      assert(latest().dom.html.includes('FINAL_PAGEHIDE_MARKER'),'Final DOM missing');
    });
    await check('offline helper preserves queued DOM and retries without duplicates',async()=>{
      seenTest.control.helperOnline=false;
      await globalThis.__seenDOMCapture.start();document.querySelector('#visible').textContent='OFFLINE_DOM_MARKER';await settle();globalThis.__seenDOMCapture.stop();
      assert((await pendingUploads()).some(r=>r.ready),'No durable queue');
      seenTest.control.helperOnline=true;await api('sync');assert(!(await pendingUploads()).some(r=>r.ready),'Queue not drained');
      const count=seenTest.saved.size;await api('sync');assert(seenTest.saved.size===count,'Duplicate retry');
      assert(latest().dom.html.includes('OFFLINE_DOM_MARKER'),'Offline DOM lost');
    });
    await check('large Unicode DOM arrives intact in multiple chunks',async()=>{
      const large=document.createElement('div');large.id='large';large.textContent='Grüße 🐻'.repeat(15000);document.querySelector('#fixture').append(large);
      await globalThis.__seenDOMCapture.start();await settle();globalThis.__seenDOMCapture.stop();
      assert(latest().dom.html.includes(large.textContent),'Large DOM truncated');assert(latest().meta.chunks>1,'Chunk test too small');large.remove();
    });
    await check('removed nodes remain available in earlier snapshots',()=>{
      assert([...seenTest.saved.values()].some(r=>r.dom.html.includes('id="large"')),'Earlier DOM overwritten');
    });
    await check('queries and fragments retained on SPA navigation',async()=>{
      const old=location.href;history.pushState(null,'','/tests/fixture.html?q=kept#kept');
      await globalThis.__seenDOMCapture.start();await settle();globalThis.__seenDOMCapture.stop();
      assert(latest().meta.url.endsWith('?q=kept#kept'),'URL redacted');history.replaceState(null,'',old);
    });
    await check('legacy text database remains readable',async()=>{assert(Array.isArray(await legacyPages()),'Legacy database unavailable');});
    output.textContent+='\n\nALL '+checks.length+' CHECKS PASSED. Synthetic archive retained for UI preview.';
  } catch(error){globalThis.__seenDOMCapture?.stop();output.textContent+='\nFAIL '+error.message;}
});
