const C='ff-v2';
self.addEventListener('install',e=>{e.waitUntil(caches.open(C).then(c=>c.add('/')).then(()=>self.skipWaiting()))});
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(k=>Promise.all(k.filter(x=>x!==C).map(x=>caches.delete(x)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',e=>{const r=e.request;if(r.method!=='GET')return;const u=new URL(r.url);
if(r.mode==='navigate'&&u.pathname==='/'){e.respondWith(fetch(r).then(x=>{const c=x.clone();caches.open(C).then(y=>y.put('/',c));return x}).catch(()=>caches.match('/')));return}
if((u.origin===location.origin&&u.pathname.startsWith('/static/'))||u.hostname==='cdnjs.cloudflare.com'){e.respondWith(caches.match(r).then(h=>{const n=fetch(r).then(x=>{if(x.ok||x.type==='opaque'){const c=x.clone();caches.open(C).then(y=>y.put(r,c))}return x}).catch(()=>h);return h||n}))}});
