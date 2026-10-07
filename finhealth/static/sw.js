const C='ff-v1';
self.addEventListener('install',e=>{e.waitUntil(caches.open(C).then(c=>c.add('/')).then(()=>self.skipWaiting()))});
self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));
self.addEventListener('fetch',e=>{if(e.request.method!=='GET'||e.request.mode!=='navigate'||new URL(e.request.url).pathname!=='/')return;
e.respondWith(fetch(e.request).then(r=>{const c=r.clone();caches.open(C).then(x=>x.put('/',c));return r}).catch(()=>caches.match('/')))});
