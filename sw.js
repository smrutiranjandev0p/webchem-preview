const CACHE_NAME='webchem-shell-v1';
const BASE=new URL('./',self.location.href);
const SHELL=[new URL('./',BASE).href,new URL('./index.html',BASE).href,new URL('./manifest.webmanifest',BASE).href];
self.addEventListener('install',event=>{event.waitUntil(caches.open(CACHE_NAME).then(cache=>cache.addAll(SHELL)).then(()=>self.skipWaiting()))});
self.addEventListener('activate',event=>{event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith('webchem-')&&k!==CACHE_NAME).map(k=>caches.delete(k)))).then(()=>self.clients.claim()))});
self.addEventListener('fetch',event=>{const req=event.request;if(req.method!=='GET'||new URL(req.url).origin!==self.location.origin)return;
 event.respondWith(fetch(req).then(response=>{if(response&&response.ok&&req.url.startsWith(BASE.href)){const copy=response.clone();caches.open(CACHE_NAME).then(cache=>cache.put(req,copy));}return response}).catch(()=>caches.match(req).then(cached=>cached|| (req.mode==='navigate'?caches.match(new URL('./index.html',BASE).href):Response.error()))));
});
